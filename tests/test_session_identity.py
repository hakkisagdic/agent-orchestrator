"""ao owns session identity: `auto` resolves to the role's own session, or says why it cannot (SESSION-IDENTITY).

Every `ao init --profile` wrote `session: auto`, and every reader took it for a session's id: right
after a clean init `ao status` read no transcript, the watchdog had nothing to watch, and `ao tail`,
`ao cost` and `ao fleet` were blind, while the same store read correctly once the implementer block
was deleted. A Claude Code session was never discovered at all, and where the implementer and the
architect ran Claude Code in one directory, a real wake resumed the implementer's own session as the
architect, because the architect's session was the newest transcript there. The tests that covered
sessions pinned their ids, so none of it showed.

These run on synthetic stores in a temporary home: the session store each adapter declares, written
as the harness writes it, and the watchdog's own cycle over it.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from ao import cli, language, lib as A, watchdog as W
from tests.scenarios import World

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
# The readers a scenario fakes, kept whole here: these tests read the stores themselves.
SESSION_PATHS, DISCOVER_ARCHITECT = A.session_paths, A.discover_architect
ARCHITECT = {"adapter": "claude-code", "name": "fable", "argv": ["claude", "--resume", "{session}", "-p", "{prompt}"]}
BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"
PROMPT = "add a subtract function to the calculator, with a unit test for it"


def _plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _written(path, records, age):
    """A transcript as its store leaves it: the records, last written `age` seconds ago."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(record) + "\n" for record in records))
    when = time.time() - age
    os.utime(path, (when, when))
    return path


def _stamp(age):
    return datetime.fromtimestamp(time.time() - age, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _kiro_session(workspace, session, age, folder="ws-1"):
    """A Kiro session of `workspace` in the store its adapter declares; its transcript's path."""
    store = A.load_adapter("kiro")["sessions"]
    directory = os.path.join(A._home_path(store["dir"]), folder, session)
    os.makedirs(directory)
    with open(os.path.join(directory, store["meta"]), "w", encoding="utf-8") as fh:
        json.dump({store["workspaces"]: [workspace], "title": session, "status": "idle"}, fh)
    return _written(os.path.join(directory, store["transcript"]),
                    [{"timestamp": _stamp(age), "payload": {"type": "turn_end"}}], age)


def _claude_session(cwd, session, age, words=PROMPT, named=None):
    """A Claude Code session started in `cwd`, its records naming `named` (cwd) as theirs; its transcript's path."""
    store = A.load_adapter("claude-code")["sessions"]
    records = [{"type": "user", "timestamp": _stamp(age), "cwd": named or cwd, "sessionId": session,
                "message": {"role": "user", "content": words}},
               {"type": "assistant", "timestamp": _stamp(age), "cwd": named or cwd, "sessionId": session,
                "message": {"id": f"msg-{session}", "role": "assistant", "stop_reason": "end_turn",
                            "content": [{"type": "text", "text": "the function and its unit test are written"}],
                            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 0, "output_tokens": 5}}}]
    directory = A.escaped_cwd_dir(store, cwd)
    return _written(os.path.join(directory, store["transcript"].replace("{session}", session)), records, age)


def _config(root, project, **blocks):
    """Write the project's config with these role blocks, as a person or `ao init` leaves it on disk."""
    stored = {key: value for key, value in dict(project, **blocks).items() if key != "root" and value is not None}
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)


def _world(project, monkeypatch, tmp_path, **blocks):
    """A scenario world whose cycle reads the session stores, not a transcript handed to it."""
    world = World(project, monkeypatch, tmp_path)
    monkeypatch.setattr(A, "session_paths", SESSION_PATHS)
    monkeypatch.setattr(A, "discover_architect", DISCOVER_ARCHITECT)
    _config(world.root, project, **blocks)
    return world


def _wakes(world):
    wake = language.text(world.cfg, "prompt.wake")
    return [argv for argv in world.spawned if isinstance(argv, list) and wake in argv]


def _init(tmp_path, monkeypatch, name, profile):
    """`ao init --profile` in a fresh repository, its reviewer probe answering."""
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.setattr(cli, "_reviewer_probe", lambda cfg, timeout=cli.REVIEW_PROBE_TIMEOUT: {
        "configured": True, "ok": True, "route": "fixture-reviewer", "binary": sys.executable,
        "version": "fixture", "reason": "exact nonce echoed", "kind": "success"})
    args = SimpleNamespace(name=None, profile=profile, implementer=None, model=None, effort=None,
                           reviewer_model=None, agent=None, no_mcp=True, rules=False, watchdog=False)
    assert cli.cmd_init({"root": str(root)}, args) == 0
    return str(root)


def _panel(cfg, monkeypatch, capsys):
    monkeypatch.setattr(A, "quota", lambda adapter, ttl=300: [])       # no keyflip is asked
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, headless_only=False: [])
    capsys.readouterr()
    cli.cmd_status(cfg, SimpleNamespace(messages=4, window=24.0))
    return _plain(capsys.readouterr().out)


# ── auto, end to end ──

def test_a_kiro_profile_resolves_auto_to_the_workspaces_newest_session_and_status_reads_it(
        project, monkeypatch, tmp_path, capsys):
    root = _init(tmp_path, monkeypatch, "kiro-project", "claude-kiro")
    stored = json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))
    assert stored["implementer"]["session"] == "auto"
    assert ".ao/sessions.json" in open(os.path.join(root, ".gitignore"), encoding="utf-8").read().split("\n")
    _kiro_session(root, "s-earlier", 3600)
    current = _kiro_session(root, "s-current", 900)
    _kiro_session(str(tmp_path / "another-project"), "s-elsewhere", 5, folder="ws-2")

    cfg = A.load_config(root)

    assert cfg["implementer"]["session"] == "s-current" and A.session_paths(cfg)[0] == current
    assert A.session_state(cfg, "implementer")["how"] == "discovered"
    assert A.session_to_resume(cfg, "implementer") == ("s-current", None)
    out = _panel(cfg, monkeypatch, capsys)
    assert "kiro · last write 15m" in out and "no transcript" not in out


def test_a_claude_profile_reads_the_claude_code_session_and_the_architect_does_not_take_it(
        project, monkeypatch, capsys):
    root = project["root"]
    _config(root, {"project": "proj", "round_budget": 5})
    # The blocks `ao init --profile claude-claude` writes; whether its reviewer may review is another question.
    args = SimpleNamespace(profile="claude-claude", implementer=None, model=None, effort=None, reviewer_model=None)
    assert cli._apply_profile(root, args) == ["implementer", "reviewer", "architect"]
    only = _claude_session(root, "s-only", 1800)

    cfg = A.load_config(root)

    assert cfg["implementer"]["session"] == "s-only" and A.session_paths(cfg)[0] == only
    architect = A.session_state(cfg, "architect")
    assert architect["session"] is None and "is the implementer's" in architect["why"]
    assert "claude-code · last write 30m" in _panel(cfg, monkeypatch, capsys)
    assert cli.cmd_tail(cfg, SimpleNamespace(n=4)) is None
    assert PROMPT in capsys.readouterr().out


# ── the watchdog resumes only a session that is the role's ──

def test_the_watchdog_dry_cycle_nudges_the_resolved_session_and_never_auto(project, monkeypatch, tmp_path):
    world = _world(project, monkeypatch, tmp_path, implementer={"adapter": "kiro", "session": "auto", "name": "kiro"})
    _kiro_session(world.root, "s-earlier", 7200)
    _kiro_session(world.root, "s-current", 900)
    world.board("running", "- [S1] the calculator slice · since: 2026-09-17 09:00")

    trace = world.cycle()

    assert world.verdict.startswith("DRY RUN:") and "--resume-id s-current" in world.verdict
    assert not any("auto" in line.split() for line in trace)


def test_a_session_read_beside_a_role_that_is_not_settled_is_not_nudged_until_one_is_pinned(
        project, monkeypatch, tmp_path):
    world = _world(project, monkeypatch, tmp_path, implementer={"adapter": "claude-code", "session": "auto",
                                                                "name": "claude"},
                   architect=dict(ARCHITECT, session="auto"))
    _claude_session(world.root, "s-only", 900)
    world.board("running", "- [S1] the calculator slice · since: 2026-09-17 09:00")

    trace = world.cycle()

    assert "not nudging" in world.verdict and "pinned" in world.verdict
    assert not any(line.startswith("DRY RUN:") for line in trace)

    _config(world.root, project, implementer={"adapter": "claude-code", "session": "s-only", "name": "claude"},
            architect=dict(ARCHITECT, session="auto"))
    world.cycle()

    assert world.verdict.startswith("DRY RUN:") and "--resume s-only" in world.verdict


def test_a_pinned_id_wins_and_a_helpers_session_is_never_a_roles(project, tmp_path):
    root = project["root"]
    _config(root, project, implementer={"adapter": "kiro", "session": "s-pinned", "name": "kiro"})
    pinned = _kiro_session(root, "s-pinned", 7200)
    _kiro_session(root, "s-newer", 60)

    cfg = A.load_config(root)

    assert cfg["implementer"]["session"] == "s-pinned" and A.session_state(cfg, "implementer")["how"] == "pinned"
    assert A.session_paths(cfg)[0] == pinned                  # found without a workspace directory in the config
    assert A.session_to_resume(cfg, "implementer") == ("s-pinned", None)

    _config(root, project, implementer={"adapter": "claude-code", "session": "s-mine", "name": "claude"},
            architect=dict(ARCHITECT, session="s-lead"))
    _claude_session(root, "s-mine", 3600)
    _claude_session(root, "s-lead", 1800)
    _claude_session(root, "s-newest", 5)
    # A reviewer runs in a directory of its own outside the repository, however new its session.
    _claude_session(str(tmp_path / "ao-reviewer-fixture"), "s-reviewer", 1)
    cfg = A.load_config(root)

    assert (cfg["implementer"]["session"], cfg["architect"]["session"]) == ("s-mine", "s-lead")
    assert not os.path.exists(A.sessions_path(root))           # a pin is the config's; nothing is recorded
    _config(root, project, implementer={"adapter": "claude-code", "session": "auto", "name": "claude"},
            architect=None)
    assert A.load_config(root)["implementer"]["session"] == "s-newest"


def test_the_architects_wake_never_resumes_the_implementers_session(project, monkeypatch, tmp_path):
    world = _world(project, monkeypatch, tmp_path,
                   implementer={"adapter": "claude-code", "session": "s-mine", "name": "claude"},
                   architect=dict(ARCHITECT, session="auto"))
    _claude_session(world.root, "s-mine", 30)                  # the implementer's, and the newest
    world.mail("20260917-1200-claude-to-fable-BLOCKED-queue.md", BLOCKED)

    trace = world.cycle(dry_run=False)

    assert _wakes(world) == []
    assert any("architect session not resolvable" in line and "is the implementer's" in line for line in trace)

    _claude_session(world.root, "s-lead", 3600)                # the architect's own, older
    world.cycle(dry_run=False)

    (argv,) = _wakes(world)
    assert argv[argv.index("--resume") + 1] == "s-lead" and "s-mine" not in argv
    assert A.session_records(world.root)["architect"]["session"] == "s-lead"


def test_two_sessions_the_implementer_does_not_hold_are_ambiguous_and_the_architect_is_not_woken(
        project, monkeypatch, tmp_path):
    world = _world(project, monkeypatch, tmp_path,
                   implementer={"adapter": "claude-code", "session": "s-mine", "name": "claude"},
                   architect=dict(ARCHITECT, session="auto"))
    _claude_session(world.root, "s-mine", 30)
    _claude_session(world.root, "s-one", 1800)
    _claude_session(world.root, "s-two", 3600)
    world.mail("20260917-1200-claude-to-fable-BLOCKED-queue.md", BLOCKED)

    trace = world.cycle(dry_run=False)

    assert _wakes(world) == []
    assert any("architect session not resolvable" in line and "cannot tell which is the architect's" in line
               for line in trace)
    assert not os.path.exists(A.sessions_path(world.root))


def test_the_doctor_prints_each_roles_session_and_how_and_names_an_ambiguity_as_a_problem(project):
    root = project["root"]
    _config(root, project, implementer={"adapter": "claude-code", "session": "auto", "name": "claude"},
            architect=dict(ARCHITECT, session="auto"))
    _claude_session(root, "s-one", 60)
    _claude_session(root, "s-two", 600)
    cfg = A.load_config(root)

    lines = [_plain(line) for line in cli._session_lines(cfg)]
    problems = dict(cli.doctor_problems(cfg))

    assert lines[0].startswith("implementer     claude-code / auto  ambiguous")
    assert lines[1].startswith("architect       claude-code / auto  ambiguous")
    assert "cannot tell which is the implementer's" in problems["implementer-session"]
    assert "architect-session" in problems and cli._doctor_severity("implementer-session", "") == "red"

    _config(root, project, implementer={"adapter": "claude-code", "session": "s-one", "name": "claude"},
            architect=dict(ARCHITECT, session="auto"))
    cfg = A.load_config(root)
    lines = [_plain(line) for line in cli._session_lines(cfg)]
    problems = dict(cli.doctor_problems(cfg))

    assert lines[0] == "implementer     claude-code / s-one  pinned"
    assert lines[1].startswith("architect       claude-code / s-two  discovered")
    assert "implementer-session" not in problems and "architect-session" not in problems


def test_the_recorded_ids_survive_a_new_process(project):
    root = project["root"]
    _config(root, project, implementer={"adapter": "claude-code", "session": "s-mine", "name": "claude"},
            architect=dict(ARCHITECT, session="auto"))
    _claude_session(root, "s-mine", 30)
    _claude_session(root, "s-lead", 3600)
    assert A.load_config(root)["architect"]["session"] == "s-lead"
    assert A.session_records(root)["architect"]["session"] == "s-lead"
    _claude_session(root, "s-newer", 5)               # newer, and the implementer does not hold it either
    script = ("import json, sys; sys.path.insert(0, sys.argv[1]); from ao import lib as A; "
              "cfg = A.load_config(sys.argv[2]); "
              "print(json.dumps([cfg['architect']['session'], cfg['architect']['_session']['how']]))")

    # The new process's home is the test's: Windows reads it from USERPROFILE and never from HOME (#71).
    run = subprocess.run([sys.executable, "-c", script, SRC, root],
                         env=dict(os.environ, HOME=A.HOME, USERPROFILE=A.HOME),
                         capture_output=True, text=True, timeout=120)

    assert json.loads(run.stdout.strip().splitlines()[-1]) == ["s-lead", "recorded"], run.stderr


# ── every store is enumerated ──

def test_with_no_implementer_configured_a_claude_code_session_is_found_and_resumed_only_alone(
        project, monkeypatch, capsys):
    root = project["root"]
    _config(root, project, implementer=None, architect=None)
    _claude_session(root, "s-running", 600)

    cfg = A.load_config(root)

    assert (cfg["implementer"]["adapter"], cfg["implementer"]["session"]) == ("claude-code", "s-running")
    assert A.session_to_resume(cfg, "implementer") == ("s-running", None)
    assert "claude-code · last write 10m" in _panel(cfg, monkeypatch, capsys)

    _config(root, project, implementer=None)           # the architect this project names runs the same harness
    cfg = A.load_config(root)

    assert cfg["implementer"]["session"] == "s-running" and A.session_to_resume(cfg, "implementer")[0] is None


def test_fleet_and_projects_list_the_workspaces_a_claude_code_store_keeps(project, monkeypatch, tmp_path, capsys):
    root = project["root"]
    _config(root, project, implementer={"adapter": "claude-code", "session": "auto", "name": "claude"},
            architect=None)
    _claude_session(root, "s-work", 600)
    # A reviewer's temporary directory is gone once its review ends: no project to watch.
    _claude_session(str(tmp_path / "ao-reviewer-gone"), "s-review", 5)
    # A directory whose records name another path is not that path's: its name does not escape from it.
    _claude_session(str(tmp_path / "somewhere-else"), "s-moved", 5, named=str(tmp_path))
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, headless_only=False: [])

    rows = A.all_workspaces()
    fleet = cli._fleet_rows()
    cli.cmd_projects({}, SimpleNamespace())

    assert [(row["path"], row["adapter"], row["session"]) for row in rows] == [(root, "claude-code", "s-work")]
    assert [(row["name"], row["state"]) for row in fleet] == [("proj", "idle")]
    out = _plain(capsys.readouterr().out)
    assert "claude-code" in out and root in out
