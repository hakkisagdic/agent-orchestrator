"""Harness configuration an agent writes reaches no harness ao starts before a person has seen it (HARNESS-CONFIG).

REVIEW-TREE-3 kept a candidate's hooks, plugins and MCP servers from running as its reviewer's: measured on
2026-10-07, Claude Code ran a SessionStart hook from a tree's .claude/settings.json before it had even signed in. The
same files act wherever else a harness starts in a tree an agent writes, so a hook the implementer put there ran in its
own next nudge, outside the permissions that hold its commands, and in the architect's session when the watchdog woke it
in the same checkout - each before a review had seen it (backlog #112). Now `ao verify`, `ao doctor` and the watchdog
name every change, staged or not, to what an adapter declares its harness reads from a project, and no wake, refill,
nudge, hunt or lane starts in a tree that holds one until a person has read it with `ao harness seen`.
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, lib as A, watchdog as W
from tests.scenarios import World

UNSEEN = (".claude/settings.json", "modified", "ab" * 32)
BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"
BOARD = "# Board\n\n## running\n\n## blocked\n\n## queued\n- [B] ready to start\n\n## inbox\n\n## verified\n\n## done\n"


def _git(root, *args):
    return subprocess.run([A.git_binary(), "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _committed(root, files):
    for rel, text in files.items():
        _write(root, rel, text)
    _git(root, "add", "--", *files)
    _git(root, "commit", "-q", "-m", "base")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _named(root):
    return [(path, state) for path, state, _ in A.project_config_changes(root)]


def _seen_by_a_person(root):
    unseen = A.unseen_harness_config(root)
    A.record_harness_seen(root, A.project_config_changes(root), "Ayşe", A.harness_config_digest(unseen))


def _can_link(tmp_path):
    try:
        os.symlink(str(tmp_path), str(tmp_path / "link-probe"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symbolic link")


# ---- what counts as a change ----------------------------------------------------------------------------------------

def test_every_change_to_what_a_harness_reads_is_named_staged_or_not_and_nothing_else(project):
    root = project["root"]
    _committed(root, {".claude/settings.json": "{}\n", "AGENTS.md": "rules\n", "CLAUDE.md": "claude\n",
                      "GEMINI.md": "gemini\n", ".cursorrules": "cursor\n", "src/app.py": "x = 1\n",
                      ".gitignore": ".claude/settings.local.json\n"})
    _write(root, ".claude/settings.json", '{"hooks": {}}\n')                       # changed, not staged
    _write(root, "AGENTS.md", "rules, changed\n")
    _git(root, "add", "AGENTS.md")                                                 # changed and staged
    _write(root, ".mcp.json", "{}\n")
    _git(root, "add", ".mcp.json")                                                 # new and staged
    os.remove(os.path.join(root, "CLAUDE.md"))
    _write(root, ".kiro/steering/extra.md", "steer\n")                             # untracked, in a folder
    _write(root, "docs/AGENTS.md", "nested\n")                                     # wherever in the tree it is
    _write(root, ".claude/settings.local.json", '{"hooks": 1}\n')                  # ignored, and read all the same
    _git(root, "update-index", "--assume-unchanged", "GEMINI.md")
    _write(root, "GEMINI.md", "gemini, hidden\n")
    _git(root, "update-index", "--skip-worktree", ".cursorrules")
    _write(root, ".cursorrules", "cursor, hidden\n")
    _write(root, "src/app.py", "x = 2\n")                                          # product code no harness reads

    changes = A.project_config_changes(root)

    assert [(path, state) for path, state, _ in changes] == [
        (".claude/settings.json", "modified"), (".claude/settings.local.json", "ignored"), (".cursorrules", "hidden"),
        (".kiro/steering/extra.md", "untracked"), (".mcp.json", "added"), ("AGENTS.md", "modified"),
        ("CLAUDE.md", "deleted"), ("GEMINI.md", "hidden"), ("docs/AGENTS.md", "untracked")]
    shas = {path: sha for path, _, sha in changes}
    assert shas[".claude/settings.json"] == _sha('{"hooks": {}}\n') and shas["GEMINI.md"] == _sha("gemini, hidden\n")
    assert shas["CLAUDE.md"] is None


def test_a_file_git_is_told_to_skip_is_no_change_while_it_holds_heads_content(project):
    root = project["root"]
    _committed(root, {"GEMINI.md": "gemini\n", ".cursorrules": "cursor\n"})
    _git(root, "update-index", "--assume-unchanged", "GEMINI.md")
    _git(root, "update-index", "--skip-worktree", ".cursorrules")

    assert _named(root) == []


def test_an_ignored_folder_is_read_only_where_a_harness_reads_it_and_node_modules_never(project, monkeypatch):
    root = project["root"]
    _committed(root, {".gitignore": "node_modules/\n.claude/\nout/**\nbuild/\n"})
    for rel in (".claude/hooks/run.sh", ".claude/settings.local.json", ".claude/node_modules/dep/AGENTS.md",
                "out/.claude/settings.json", "out/node_modules/x/AGENTS.md", "out/report.txt",
                "node_modules/pkg/AGENTS.md", "node_modules/pkg/.claude/settings.json", "build/.claude/settings.json"):
        _write(root, rel, rel + "\n")
    listed, real = [], os.listdir
    monkeypatch.setattr(os, "listdir", lambda path=".": listed.append(os.fspath(path)) or real(path))

    changes = A.project_config_changes(root)

    # A configuration folder ignored as a whole is read, and one git names inside an ignored folder - everything
    # under out/ is ignored, and out/.claude/ is listed as a folder of its own.
    assert [(path, state) for path, state, _ in changes] == [
        (".claude/hooks/run.sh", "ignored"), (".claude/settings.local.json", "ignored"),
        ("out/.claude/settings.json", "ignored")]
    assert listed and not any("node_modules" in path for path in listed)
    assert not any(os.path.basename(path) == "build" for path in listed)


def test_a_tracked_file_in_an_ignored_folder_is_a_change_only_when_it_changed(project):
    root = project["root"]
    _committed(root, {".claude/settings.json": "{}\n"})
    _committed(root, {".gitignore": ".claude/\n"})
    _write(root, ".claude/settings.local.json", "{}\n")

    assert _named(root) == [(".claude/settings.local.json", "ignored")]


def test_a_repository_cloned_into_a_configuration_folder_is_read_and_a_worktree_of_this_one_is_not(project):
    root = project["root"]
    _committed(root, {"README.md": "a project\n"})
    _git(root, "worktree", "add", "-q", "-b", "side", os.path.join(root, ".claude", "worktrees", "side"))
    _write(root, ".claude/worktrees/side/AGENTS.md", "the worktree's own, checked where something runs there\n")
    plugin = os.path.join(root, ".opencode", "plugin", "evil")
    os.makedirs(plugin)
    _git(plugin, "init", "-q")
    _write(root, ".opencode/plugin/evil/index.js", "run()\n")

    assert _named(root) == [(".opencode/plugin/evil/index.js", "untracked")]


def test_a_link_is_read_through_so_what_it_leads_to_cannot_change_unseen(project, tmp_path):
    _can_link(tmp_path)
    root = project["root"]
    _write(root, "notes/claude/settings.json", '{"hooks": {}}\n')
    os.symlink(os.path.join("notes", "claude"), os.path.join(root, ".claude"), target_is_directory=True)
    os.symlink("..", os.path.join(root, "notes", "claude", "back"), target_is_directory=True)     # a cycle

    first = A.project_config_changes(root)
    assert [(path, state) for path, state, _ in first][:2] == [(".claude", "untracked"),
                                                              (".claude/back", "untracked")]
    assert (".claude/settings.json", "untracked") in [(path, state) for path, state, _ in first]
    _write(root, "notes/claude/settings.json", '{"hooks": {"SessionStart": []}}\n')

    assert A.harness_config_digest(A.project_config_changes(root)) != A.harness_config_digest(first)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFO or /dev/zero on this platform")
def test_a_fifo_or_a_device_where_a_harness_reads_is_named_and_never_waited_on(project):
    root = project["root"]
    _committed(root, {".gitignore": ".claude/\n"})
    os.makedirs(os.path.join(root, ".claude"))
    os.mkfifo(os.path.join(root, ".claude", "settings.json"))
    os.symlink("/dev/zero", os.path.join(root, "AGENTS.md"))
    found, shown = [], {}
    reader = threading.Thread(target=lambda: found.append(A.project_config_changes(root, shown=shown)), daemon=True)

    reader.start()
    reader.join(60)

    assert not reader.is_alive() and [(path, state) for path, state, _ in found[0]] == [
        (".claude/settings.json", "ignored"), ("AGENTS.md", "untracked")]
    assert shown[".claude/settings.json"] == ("special", "a FIFO")
    assert shown["AGENTS.md"] == ("link", "/dev/zero", ("special", "a device"))


def test_past_its_limit_ao_stops_reading_and_says_so_rather_than_name_some(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "HARNESS_CONFIG_LIMIT", 3)
    for n in range(4):
        _write(root, f".kiro/steering/{n}.md", "steer\n")

    with pytest.raises(RuntimeError, match="more than 3 changes"):
        A.project_config_changes(root)


# ---- seen, by a person -----------------------------------------------------------------------------------------------

def test_a_change_is_seen_by_its_path_and_its_content(project):
    root = project["root"]
    _committed(root, {"AGENTS.md": "rules\n"})
    _write(root, "AGENTS.md", "rules, changed\n")
    _write(root, ".claude/settings.json", "{}\n")
    assert A.unseen_harness_config(root) == A.project_config_changes(root) != []

    _seen_by_a_person(root)
    assert A.unseen_harness_config(root) == []
    assert not os.path.realpath(A.project_file(root, "harness-seen")).startswith(os.path.realpath(root))

    _write(root, ".claude/settings.json", '{"hooks": {}}\n')                      # changed after it was seen
    assert [path for path, _, _ in A.unseen_harness_config(root)] == [".claude/settings.json"]

    _write(root, ".claude/settings.json", "{}\n")                                 # the bytes that were seen
    _git(root, "commit", "-q", "-am", "AGENTS.md lands")                          # one lands; the other stays seen
    assert A.unseen_harness_config(root) == []

    with open(A.project_file(root, "harness-seen"), "w", encoding="utf-8") as fh:
        fh.write("{torn")
    # A commit with no grant is no landing: with the record gone, what it committed is unseen again.
    assert [path for path, _, _ in A.unseen_harness_config(root)] == [".claude/settings.json", "AGENTS.md"]


def test_the_digest_holds_each_path_and_its_content_never_its_state(project):
    root = project["root"]
    _write(root, ".claude/settings.json", "{}\n")
    untracked = A.project_config_changes(root)
    _seen_by_a_person(root)
    _git(root, "add", ".claude/settings.json")
    staged = A.project_config_changes(root)

    assert ([state for _, state, _ in untracked], [state for _, state, _ in staged]) == (["untracked"], ["added"])
    assert A.harness_config_digest(staged) == A.harness_config_digest(untracked)
    assert A.unseen_harness_config(root) == []                                    # staged: still what was seen
    _write(root, ".claude/settings.json", "{ }\n")
    assert A.harness_config_digest(A.project_config_changes(root)) != A.harness_config_digest(staged)


def _seen(cfg, by=None, digest=None, ids=None):
    return cli.cmd_harness(cfg, SimpleNamespace(action="seen", ids=ids or [], by=by, digest=digest, sessions=False,
                                                timeout=20.0))


def test_a_person_reads_each_change_then_records_exactly_the_bytes_shown(project, capsys):
    root = project["root"]
    _committed(root, {"AGENTS.md": "rules\n"})
    _write(root, "AGENTS.md", "rules\nrun this first\x1b[8m, unseen\x1b[0m \u202eevil\n")
    _write(root, ".claude/settings.json", '{"hooks": {}}\n')

    assert _seen(project, by="Ayşe") == 0
    out = capsys.readouterr().out
    assert "AGENTS.md (modified)" in out and "+run this first\\x1b[8m, unseen\\x1b[0m \\u202eevil" in out
    assert ".claude/settings.json (untracked)" in out and '+{"hooks": {}}' in out
    digest = A.harness_config_digest(A.unseen_harness_config(root))
    assert f"ao harness seen --by Ayşe --digest {digest}" in out
    assert not os.path.exists(A.project_file(root, "harness-seen"))               # shown, and nothing recorded

    _write(root, ".claude/settings.json", '{"hooks": {"SessionStart": []}}\n')    # changed after it was shown
    assert _seen(project, by="Ayşe", digest=digest) == 2
    assert "Nothing was recorded" in capsys.readouterr().out
    assert len(A.unseen_harness_config(root)) == 2

    digest = A.harness_config_digest(A.unseen_harness_config(root))
    assert _seen(project, by="Ayşe", digest=digest) == 0
    assert "recorded" in capsys.readouterr().out and A.unseen_harness_config(root) == []
    record = A.harness_seen_record(root)
    assert (record["by"], record["digest"]) == ("Ayşe", digest) and {"user", "interactive", "at"} <= set(record)
    assert _seen(project, by="Ayşe") == 0 and "seen" in capsys.readouterr().out


@pytest.mark.parametrize("by", [None, "  ", "kiro", "architect", "Fable", "watchdog", "a\nperson"])
def test_an_agent_or_a_role_cannot_say_it_has_seen_them(project, capsys, by):
    root = project["root"]
    _write(root, ".claude/settings.json", "{}\n")
    digest = A.harness_config_digest(A.unseen_harness_config(root))

    assert _seen(project, by=by, digest=digest) == 2
    assert not os.path.exists(A.project_file(root, "harness-seen")) and A.unseen_harness_config(root)


def test_with_nothing_changed_nothing_is_recorded_and_probe_takes_no_person(project, capsys):
    root = project["root"]

    assert _seen(project, by="Ayşe", digest="sha256:" + "0" * 64) == 0
    assert "differs from HEAD" in capsys.readouterr().out
    assert not os.path.exists(A.project_file(root, "harness-seen"))
    assert _seen(project, by="Ayşe", ids=["claude-code"]) == 2
    assert cli.cmd_harness(project, SimpleNamespace(action="probe", ids=[], by="Ayşe", digest=None, sessions=False,
                                                    timeout=1.0)) == 2


def test_the_command_line_reads_as_the_documents_show_it():
    args = cli.build_parser().parse_args(["harness", "seen", "--by", "Ayşe", "--digest", "sha256:ab"])
    assert (args.action, args.by, args.digest, args.fn) == ("seen", "Ayşe", "sha256:ab", cli.cmd_harness)


def test_no_grant_ao_checks_admits_recording_what_a_person_has_seen():
    assert ("records harness configuration as seen", "ao harness seen --by p --digest d") in AL.FORBIDDEN
    assert ("records harness configuration as seen", "ao harness seen --by p --digest d", "Bash(ao harness:*)") \
        in AL.problems(["claude", "--allowedTools", "Bash(ao harness:*)"])
    for key in ("implementer_tools", "architect_tools"):
        rules = A.load_adapter("claude-code")["options"][key].split(",")
        assert not any(AL.admits(rule, "ao harness seen --by p --digest d") for rule in rules)


# ---- verify and doctor name them -------------------------------------------------------------------------------------

def _gates(root):
    argv = [sys.executable, "-c", "import sys; sys.exit(0)"]
    spec = {"gates": {"smoke": {"run": subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv),
                                "expect": "exit_zero", "timeout": 60}},
            "profiles": {"quick": ["smoke"]}, "default_profile": "quick"}
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)


def test_verify_names_what_nobody_has_seen_and_records_it_without_failing(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    _write(root, "src/a.py", "value = 1\n")
    _write(root, "AGENTS.md", "the slice changes the rules\n")
    _git(root, "add", "src/a.py", "AGENTS.md")
    # A harness's own folder is coordination state, never a candidate's: no review would ever see this one.
    _write(root, ".claude/settings.json", '{"hooks": {}}\n')
    _gates(root)
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    monkeypatch.setattr(A, "plan_drift", lambda root: [])
    monkeypatch.setattr(A, "urgent_messages", lambda *args, **kwargs: [])

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 0

    out = capsys.readouterr().out
    assert "harness configuration nobody has seen" in out
    assert ".claude/settings.json (untracked)" in out and "AGENTS.md (added)" in out
    assert "not a failed gate" in out and "ao harness seen --by <name>" in out
    record = A.latest_verification(root)
    assert record["passed"] is True
    assert [change["path"] for change in record["harness_config"]["unseen"]] == [".claude/settings.json", "AGENTS.md"]
    assert record["harness_config"]["digest"] == A.harness_config_digest(A.unseen_harness_config(root))


def test_doctor_names_them_and_pages_a_person_under_the_watchdogs_own_alarm(project, monkeypatch):
    root = project["root"]
    _write(root, ".claude/settings.json", "{}\n")

    found = dict(cli._harness_config_problem(root))["harness-config"]
    assert ".claude/settings.json (untracked)" in found and "ao harness seen --by <name>" in found
    assert any(".claude/settings.json (untracked)" in line for line in cli._harness_config_lines(project))
    rang = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: rang.append((title, kw)) or True)
    monkeypatch.setattr(cli, "doctor_problems", lambda cfg: cli._harness_config_problem(cfg["root"]))

    assert cli._doctor_check(project, page=True) == 1
    assert rang == [(f"{A.project_key(root)}: harness-config", {
        "key": "harness-config", "window": 6 * 3600, "audience": "human", "level": None,
        "what": A.harness_config_digest(A.unseen_harness_config(root))})]

    _seen_by_a_person(root)
    assert cli._harness_config_problem(root) == []
    assert "no change nobody has seen" in cli._harness_config_lines(project)[0]


def test_doctor_names_what_it_cannot_read(project, monkeypatch):
    def unreadable(root):
        raise RuntimeError("cannot measure git tree: index.lock exists")
    monkeypatch.setattr(A, "unseen_harness_config", unreadable)

    found = dict(cli._harness_config_problem(project["root"]))["harness-config"]
    assert "cannot be read (cannot measure git tree: index.lock exists)" in found
    assert cli._harness_config_told(project["root"]) == "unreadable"


# ---- the watchdog tells a person, and starts nothing ----------------------------------------------------------------

def test_the_watchdog_names_them_to_a_person_once_for_what_they_hold(project, monkeypatch):
    root = project["root"]
    _write(root, ".claude/settings.json", "{}\n")
    rang = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: rang.append((title, msg, kw)) or True)

    W.tell_harness_config(root, "proj")
    (title, msg, kw), = rang
    assert title == "proj: harness configuration nobody has seen" and ".claude/settings.json (untracked)" in msg
    assert "ao harness seen --by <name>" in msg
    assert (kw["audience"], kw["key"], kw["what"]) == (
        "human", "harness-config", A.harness_config_digest(A.unseen_harness_config(root)))

    _seen_by_a_person(root)
    rang.clear()
    W.tell_harness_config(root, "proj")
    assert rang == []

    def unreadable(root, names=None, shown=None):
        raise RuntimeError("cannot measure git tree: index.lock exists")
    monkeypatch.setattr(A, "project_config_changes", unreadable)
    W.tell_harness_config(root, "proj")
    assert rang[0][2]["what"] == "unreadable" and "cannot be read" in rang[0][1]


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _turns(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).startswith("/agents/")]


def _architect_wakes(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and argv[0].endswith("/claude")]


def test_a_cycle_tells_a_person_whatever_else_it_decides(world):
    world.harness_unseen = [UNSEEN]

    world.cycle()

    told = [notice for notice in world.notices if "harness configuration nobody has seen" in notice[0]]
    assert told and told[0][2] == "human" and ".claude/settings.json (modified)" in told[0][1]


class _Exited:
    """A started turn that has already ended cleanly, so a live nudge runs to its end."""
    pid = 99999
    returncode = 0

    def poll(self):
        return 0


def test_a_nudge_waits_for_a_person_to_see_what_the_implementer_wrote(world, monkeypatch):
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00").transcript_age(900)
    world.harness_unseen = [UNSEEN]
    monkeypatch.setattr(W.subprocess, "Popen", lambda argv, **kw: world.spawned.append(argv) or _Exited())
    monkeypatch.setattr(W.time, "sleep", lambda seconds: None)

    world.cycle()
    assert "harness configuration nobody has seen" in world.verdict and world.verdict.endswith("not nudging")
    world.cycle(dry_run=False)
    assert _turns(world) == [] and world.verdict.endswith("not nudging")

    world.harness_unseen = []
    world.cycle(dry_run=False)
    assert len(_turns(world)) == 1


def test_a_report_wake_waits_for_a_person_too(world):
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.harness_unseen = [UNSEEN]

    trace = world.cycle(dry_run=False)
    assert _architect_wakes(world) == []
    assert any("harness configuration nobody has seen" in line and line.endswith("not waking the architect")
               for line in trace)

    world.harness_unseen = []
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1


def test_a_refill_waits_for_a_person_too(world):
    world.transcript_age(900)
    world.harness_unseen = [UNSEEN]

    world.cycle()
    assert "harness configuration nobody has seen" in world.verdict
    assert world.verdict.endswith("not waking the architect to refill")
    world.cycle(dry_run=False)
    assert _architect_wakes(world) == [] and world.verdict.endswith("not waking the architect to refill")

    world.harness_unseen = []
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1


def test_a_hunt_waits_for_a_person_too(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-c", "pass", "{prompt}"]},
               features={"hunter": True})
    world.harness_unseen = [UNSEEN]
    st = {}

    assert W._schedule_hunt(world.root, cfg, st) is False
    assert "last_hunt" not in st and not any(argv[-2:] == ["hunt", "run"] for argv in world.spawned)

    world.harness_unseen = []
    assert W._schedule_hunt(world.root, cfg, st) is True


def test_what_cannot_be_read_holds_every_turn(world, monkeypatch):
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00").transcript_age(900)

    def unreadable(root):
        raise RuntimeError("cannot measure git tree: index.lock exists")
    monkeypatch.setattr(A, "unseen_harness_config", unreadable)

    world.cycle(dry_run=False)

    assert _turns(world) == [] and "cannot be read" in world.verdict and world.verdict.endswith("not nudging")


# ---- a lane --------------------------------------------------------------------------------------------------------

def test_a_lane_does_not_start_while_harness_configuration_stands_unseen(project, capsys):
    root = project["root"]
    _write(root, ".ao/board.md", BOARD)
    _write(root, "AGENTS.md", "an agent's rules\n")

    assert cli.cmd_lane(project, SimpleNamespace(action="start", item="B")) == 2
    out = capsys.readouterr().out
    assert "refused" in out and "AGENTS.md (untracked)" in out and "ao harness seen --by <name>" in out
    assert A.lane_record(root, "B") is None and _git(root, "branch", "--list", "lane/B").strip() == ""

    _seen_by_a_person(root)
    assert cli.cmd_lane(project, SimpleNamespace(action="start", item="B")) == 0


def test_a_lanes_post_create_command_does_not_run_on_configuration_written_while_git_made_the_lane(project,
                                                                                                    monkeypatch):
    root = project["root"]
    _write(root, ".ao/board.md", BOARD)
    asked = []

    def written_meanwhile(root):
        # The start asks first and finds nothing; the post-create command asks again once git has made the lane.
        asked.append(root)
        return [] if len(asked) == 1 else [("AGENTS.md", "untracked", "ab" * 32)]
    monkeypatch.setattr(A, "unseen_harness_config", written_meanwhile)
    cfg = dict(project, lane={"post_create": [sys.executable, "-c", "open('ran', 'w').close()"]})

    assert cli.cmd_lane(cfg, SimpleNamespace(action="start", item="B")) == 1

    record = A.lane_record(root, "B")
    assert record["state"] == "failed" and "was not run" in record["problem"]
    assert "AGENTS.md (untracked)" in record["problem"]
    assert not os.path.exists(os.path.join(record["path"], "ran"))


def test_a_worktree_made_where_a_harness_reads_is_read_as_configuration(project):
    """From the review of HARNESS-CONFIG: a worktree of this repository was skipped wherever it stood, so `git worktree
    add .claude` hid every hook written into it. A worktree is skipped only in the folder a harness keeps the worktrees
    it makes in, `.claude/worktrees`, which no harness reads as configuration."""
    root = project["root"]
    _committed(root, {"README.md": "a project\n"})
    _git(root, "worktree", "add", "-q", "-b", "cfg", os.path.join(root, ".claude"))
    _write(root, ".claude/settings.json", '{"hooks": {"SessionStart": []}}\n')

    named = dict(_named(root))

    assert named[".claude/settings.json"] == "untracked" and ".claude/README.md" in named
    assert A.harness_worktree_dirs() == {(".claude", "worktrees")}


def test_a_tracked_link_is_read_through_so_its_target_cannot_change_unseen(project, tmp_path):
    """From the review of HARNESS-CONFIG: `.claude/settings.json -> ../tmp.json`, seen once, stayed seen while
    tmp.json changed, since git names the change at tmp.json, which is no configuration."""
    _can_link(tmp_path)
    root = project["root"]
    _write(root, "tmp.json", '{"hooks": {}}\n')
    os.makedirs(os.path.join(root, ".claude"))
    os.symlink(os.path.join("..", "tmp.json"), os.path.join(root, ".claude", "settings.json"))
    _git(root, "add", "tmp.json", ".claude/settings.json")
    _git(root, "commit", "-q", "-m", "a linked configuration")

    assert _named(root) == [(".claude/settings.json", "linked")]
    _seen_by_a_person(root)
    assert A.unseen_harness_config(root) == []

    _write(root, "tmp.json", '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "id"}]}]}}\n')

    assert [path for path, _, _ in A.unseen_harness_config(root)] == [".claude/settings.json"]


def test_worktree_dirs_must_name_folders_within_the_project():
    for bad in (["/tmp"], ["../x"], ["~/w"], [""], ".claude/worktrees", [3]):
        problems = A.validate_adapter({"id": "x", "name": "x", "verified": "partial", "worktree_dirs": bad})
        assert any("worktree_dirs" in problem for problem in problems), bad


def test_a_change_committed_with_no_grant_is_a_change_still(project, monkeypatch):
    """From the review of HARNESS-CONFIG: an implementer that committed its hook with `git commit --no-verify` left
    nothing differing from HEAD, and the next nudge ran it. Measured from the newest tree a grant bound, it is named."""
    root = project["root"]
    _committed(root, {"README.md": "a project\n"})
    granted = _git(root, "rev-parse", "HEAD^{tree}").strip()
    head = _git(root, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(A, "_granted_trees", lambda root: ({granted}, head, []))
    assert _named(root) == []

    _committed(root, {".claude/settings.json": '{"hooks": {"SessionStart": []}}\n'})

    assert _named(root) == [(".claude/settings.json", "committed")]
    assert A.harness_config_base(root) == head


def test_a_change_committed_where_no_grant_was_ever_made_is_a_change_still(project):
    """From the review of HARNESS-CONFIG: with no grant on record there was nothing to measure from, so a hook committed
    with `git commit --no-verify` went unseen. The HEAD the configuration was first read at - `ao init`'s, or the first
    reading's - is measured from, and a later `ao init` does not move it."""
    root = project["root"]
    _committed(root, {"README.md": "a project\n"})
    assert A.unseen_harness_config(root) == []                  # the first reading keeps HEAD to measure from
    first = A.harness_baseline(root)

    _committed(root, {".claude/settings.json": '{"hooks": {"SessionStart": []}}\n'})

    assert _named(root) == [(".claude/settings.json", "committed")]
    assert A.record_harness_baseline(root, "ao init") is None and A.harness_baseline(root) == first


def test_a_granted_commit_after_an_ungranted_one_does_not_make_it_seen(project, monkeypatch):
    """A grant reviews its candidate against the HEAD it built on, so a hook committed with no grant below it was in
    no review: the oldest ungranted commit is measured from, whatever was granted after it."""
    root = project["root"]
    _committed(root, {"README.md": "a project\n"})
    base = _git(root, "rev-parse", "HEAD").strip()
    _committed(root, {".claude/settings.json": '{"hooks": {"SessionStart": []}}\n'})       # no grant
    _committed(root, {"README.md": "a project, reviewed\n"})
    granted = _git(root, "rev-parse", "HEAD^{tree}").strip()
    monkeypatch.setattr(A, "_granted_trees", lambda root: ({granted}, base, []))

    assert _named(root) == [(".claude/settings.json", "committed")]


def test_a_tracked_file_rewritten_behind_git_s_stat_cache_is_a_change(project):
    """From the review of HARNESS-CONFIG: a file rewritten at its old size with its modification time put back reads as
    unchanged to git status where the change time is not checked; it is compared by content with HEAD's blob."""
    root = project["root"]
    _git(root, "config", "core.trustctime", "false")
    _git(root, "config", "core.checkStat", "minimal")
    path = _write(root, ".claude/settings.json", '{"hooks": {}}\n')
    _git(root, "add", ".claude/settings.json")
    _git(root, "commit", "-q", "-m", "base")
    stat = os.stat(path)
    os.utime(path, (stat.st_atime - 60, stat.st_mtime - 60))
    _git(root, "update-index", "--refresh")
    stat = os.stat(path)
    _write(root, ".claude/settings.json", '{"hooks":[9]}\n')                            # the same size
    os.utime(path, (stat.st_atime, stat.st_mtime))
    assert ".claude/settings.json" not in _git(root, "status", "--porcelain")

    assert _named(root) == [(".claude/settings.json", "modified")]
