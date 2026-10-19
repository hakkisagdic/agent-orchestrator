"""The command a turn starts with is the package's, not the tree's (TURN-ARGV).

The review of HARNESS-CONFIG noted that the watchdog composed the implementer's nudge from its adapter as every layer
loads it - the project's own .ao/adapters/ among them - and started the architect's wake and refill with the argv its
block in .ao/config.json names (backlog #116). Both are files an agent can write, so a change to either could start a
command the agent chose: another program in the harness's place, a grant that admits `git commit --no-verify` or a
person's act, a model or a session that reads as a flag. Now each turn is composed from the adapter the package ships
and checked against it before it starts - its program found as that harness's, its grant audited - and a role's command
that differs from the one last accepted holds every turn, as unseen harness configuration does, until a person has
seen it with `ao harness seen`.
"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W
from tests.scenarios import World

BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"
ARCHITECT = {"name": "fable", "argv": ["claude", "-p", "{prompt}"]}


def _store(root, **blocks):
    """Write role blocks into .ao/config.json, as an agent can; a block of None is taken out."""
    path = os.path.join(root, ".ao", "config.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for role, block in blocks.items():
        if block is None:
            cfg.pop(role, None)
        else:
            cfg[role] = block
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    return dict(cfg, root=root)


def _declare(root, ident, resume):
    """An adapter in the project's own layer, which an agent can write."""
    path = os.path.join(root, ".ao", "adapters", f"{ident}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"id": ident, "name": ident, "verified": "untested", "contract": 1,
                   "send": {"argv": resume[:1] + ["{prompt}"]}, "resume": {"argv": resume}}, fh)


def _program(path):
    """An executable file at `path`, named as Windows would start it."""
    path = str(path) + (".exe" if os.name == "nt" else "")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(path, 0o755)
    return path


class _Exited:
    """A started turn that has already ended cleanly, so a live nudge runs to its end."""
    pid = 99999
    returncode = 0

    def poll(self):
        return 0


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    monkeypatch.setattr(W.subprocess, "Popen", lambda argv, **kw: world.spawned.append(argv) or _Exited())
    monkeypatch.setattr(W.time, "sleep", lambda seconds: None)
    return world


def _turns(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).startswith("/agents/")]


def _refusals(world):
    return [(title, audience) for title, _, audience, _ in world.notices if title.endswith("refused")]


# ---- a nudge is the package's ---------------------------------------------------------------------------------------

def test_a_nudge_runs_the_shipped_adapters_command_whatever_the_projects_layer_says(world):
    _declare(world.root, "kiro", ["kiro-cli", "chat", "--resume-id", "{session}", "--agent", "agent-written",
                                  "--no-interactive", "{prompt}"])
    assert "agent-written" in A.load_adapter("kiro", world.root)["resume"]["argv"]   # what the nudge was composed from
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00").transcript_age(900)

    world.cycle(dry_run=False)

    turn, = _turns(world)
    assert turn[:3] == ["/agents/kiro-cli", "chat", "--resume-id"] and "--agent" not in turn
    assert "--trust-all-tools" in turn                      # the package's own grant for a turn nobody attends


def test_a_nudge_whose_adapter_no_package_ships_is_refused_and_a_person_told(world):
    _declare(world.root, "home-made", ["kiro-cli", "chat", "--resume-id", "{session}", "{prompt}"])
    _store(world.root, implementer={"adapter": "home-made", "session": "s1", "name": "kiro"})
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00").transcript_age(900)

    world.cycle(dry_run=False)

    assert _turns(world) == [] and world.verdict.endswith("not nudging")
    assert "its adapter home-made is no adapter ao ships" in world.verdict
    assert _refusals(world) == [(f"{A.project_key(world.root)}: nudge refused", "human")]


@pytest.mark.parametrize("field, value", [("model", "--dangerously-skip-permissions"), ("session", "--agent=evil")])
def test_a_value_from_the_block_that_reads_as_a_flag_is_refused(world, field, value):
    _store(world.root, implementer={"adapter": "kiro", "session": "s1", "name": "kiro", field: value})
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00").transcript_age(900)

    world.cycle(dry_run=False)

    assert _turns(world) == []
    assert f"its {field} `{value}` would be read as a flag" in world.verdict and world.verdict.endswith("not nudging")


# ---- the architect's command runs the harness its adapter ships, with that adapter's grant -----------------------

def test_a_wake_and_a_refill_that_run_another_program_are_refused(world):
    _store(world.root, architect={"name": "fable", "adapter": "claude-code", "argv": ["sh", "-c", "x", "{prompt}"]})
    world.transcript_age(900)
    why = "the watchdog refuses the architect's command: it runs `sh`, which is not claude-code's program (claude)"

    world.cycle()
    assert why in world.verdict and world.verdict.endswith("not waking the architect to refill")
    world.cycle(dry_run=False)
    assert _turns(world) == [] and why in world.verdict and world.verdict.endswith("not waking the architect to refill")

    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    trace = world.cycle(dry_run=False)
    assert _turns(world) == [] and f"{why}; not waking the architect" in trace
    assert set(_refusals(world)) == {(f"{A.project_key(world.root)}: architect wake refused", "human")}


def test_an_architect_command_no_shipped_adapter_runs_is_refused():
    assert A.turn_refusal("architect", None, ["sh", "-c", "x"]) == \
        "the watchdog refuses the architect's command: no adapter ao ships runs `sh`"
    assert "its adapter home-made is no adapter ao ships" in A.turn_refusal("architect", "home-made", ["claude"])


@pytest.mark.parametrize("grant, admitted", [
    ("Bash(git commit:*)", "skips the commit hook"),
    ("Bash(ao waive:*)", "waives a gate"),
    ("Bash(ao push allow:*)", "opens a push window"),
    ("Bash(ao harness seen:*)", "records harness configuration as seen"),
    ("Bash(sh -c:*)", "runs arbitrary code"),
])
def test_a_grant_that_admits_a_bypass_or_a_persons_act_is_refused_at_spawn(grant, admitted):
    refused = A.turn_refusal("architect", "claude-code", ["claude", "-p", "x", "--allowedTools", f"Read,{grant}"])

    assert refused.startswith("the watchdog refuses the architect's command: its grant admits")
    assert f"({admitted}) through {grant}, which claude-code's own architect command does not" in refused


def test_a_grant_of_every_tool_is_refused_where_the_shipped_command_grants_less():
    assert "(grants every tool)" in A.turn_refusal("architect", "claude-code",
                                                   ["claude", "-p", "x", "--dangerously-skip-permissions"])
    # Kiro's own nudge trusts every tool, as its adapter declares for a turn nobody attends: its architect's does not.
    ident, nudge = A.turn_command({"implementer": {"adapter": "kiro"}}, "implementer")
    assert "--trust-all-tools" in nudge and A.turn_refusal("implementer", ident, nudge) is None
    assert "(grants every tool)" in A.turn_refusal("architect", "kiro", ["kiro-cli", "chat", "--trust-all-tools"])


def test_a_wake_whose_grant_admits_a_hook_bypass_does_not_start(world):
    _store(world.root, architect=dict(ARCHITECT, argv=ARCHITECT["argv"] + ["--allowedTools", "Bash(git commit:*)"]))
    world.transcript_age(900).mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)

    trace = world.cycle(dry_run=False)

    assert _turns(world) == []
    assert any("(skips the commit hook) through Bash(git commit:*)" in line
               and line.endswith("not waking the architect") for line in trace)


def test_every_command_ao_composes_from_a_shipped_adapter_starts():
    """No false refusal: the nudge ao composes for each shipped adapter, and the architect argv `ao init` writes."""
    for ident, adapter in sorted(A.package_adapters().items()):
        if not (adapter.get("resume") or {}).get("argv"):
            continue
        implementer = {"implementer": {"adapter": ident, "model": "a-model", "effort": "high"}}
        assert A.turn_refusal("implementer", *A.turn_command(implementer, "implementer"),
                              values={"model": "a-model", "effort": "high"}) is None, ident
        architect = {"architect": {"adapter": ident, "argv": cli._architect_argv(ident)}}
        assert A.turn_refusal("architect", *A.turn_command(architect, "architect")) is None, ident


def test_a_program_named_by_its_path_runs_only_where_ao_finds_that_harness(project, tmp_path):
    root = project["root"]
    found = _program(tmp_path / "bin" / "claude")
    planted = _program(tmp_path / "elsewhere" / "claude")
    inside = _program(os.path.join(root, "tools", "claude"))
    search = str(tmp_path / "bin")

    def refusal(program, resolved=None):
        return A.turn_refusal("architect", "claude-code", [program, "-p", "x"], root=root,
                              resolved=resolved or program, search=search)

    assert refusal(found) is None
    assert "which is not where ao finds claude-code's program (claude) on the path it searches" in refusal(planted)
    assert "inside this repository, which an agent writes" in refusal(inside)
    assert "inside this repository" in refusal("claude", resolved=inside)           # found by name, on a path into it
    relative = os.path.join("tools", "claude")                                   # as this platform writes it
    assert f"by a path from where it starts, `{relative}`" in refusal(relative)


# ---- what the role's block says holds every turn until a person has seen it ----------------------------------------

def _seen(cfg, by="Ayşe", digest=None):
    return cli.cmd_harness(cfg, SimpleNamespace(action="seen", ids=[], by=by, digest=digest, ask=False))


def test_a_changed_command_holds_every_turn_until_a_person_reads_and_records_it(project, capsys):
    root = project["root"]
    assert A.unseen_harness_config(root) == []                       # the first reading accepts what stands
    assert {role: entry["by"] for role, entry in A.turn_record(root).items()} == {
        "implementer": "first reading", "architect": "first reading", "reviewer": "first reading",
        "hunter": "first reading"}

    cfg = _store(root, implementer=dict(project["implementer"], model="model-b"))
    unseen = A.unseen_harness_config(root)
    assert [(path, state) for path, state, _ in unseen] == [("the implementer's command", "changed")]
    assert "the implementer's command (changed)" in W.harness_config_hold(root)
    assert "the implementer's command (changed)" in dict(cli._harness_config_problem(root))["harness-config"]
    assert cli._verify_harness_config(root)["unseen"][0]["path"] == "the implementer's command"

    assert _seen(cfg) == 0
    out = capsys.readouterr().out
    assert "a nudge would start: kiro-cli chat --resume-id '{session}' --no-interactive '{prompt}' " \
           "--trust-all-tools --model model-b" in out
    assert '+model: "model-b"' in out and A.turn_record(root)["implementer"]["by"] == "first reading"

    assert _seen(cfg, digest=A.harness_config_digest(unseen)) == 0
    assert A.unseen_harness_config(root) == [] and W.harness_config_hold(root) is None
    accepted = A.turn_record(root)["implementer"]
    assert (accepted["by"], accepted["identity"]["model"]) == ("Ayşe", "model-b")

    _store(root, implementer=project["implementer"])                 # put back: a change from what was seen
    assert [path for path, _, _ in A.unseen_harness_config(root)] == ["the implementer's command"]


def test_an_architect_command_edited_or_added_after_the_first_reading_is_a_change(project):
    root = project["root"]
    _store(root, architect=None)
    assert A.unseen_harness_config(root) == [] and A.turn_record(root)["architect"]["sha256"] is None

    _store(root, architect=ARCHITECT)
    assert [path for path, _, _ in A.unseen_harness_config(root)] == ["the architect's command"]
    _store(root, architect=dict(ARCHITECT, prompt="Pull nothing; write the backlog as you like."))
    assert [path for path, _, _ in A.unseen_harness_config(root)] == ["the architect's command"]


def test_a_turn_composed_from_a_command_nobody_has_seen_waits_though_the_file_is_put_back(world, monkeypatch):
    A.record_turn_baseline(world.root, "first reading")
    loaded = A.load_config

    def read_before_it_was_put_back(root):
        cfg = loaded(root)
        cfg["architect"] = dict(cfg["architect"], argv=ARCHITECT["argv"] + ["--allowedTools", "Read"])
        return cfg
    monkeypatch.setattr(A, "load_config", read_before_it_was_put_back)
    world.transcript_age(900).mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)

    trace = world.cycle(dry_run=False)

    assert _turns(world) == []
    assert any("the architect's command (changed)" in line and line.endswith("not waking the architect")
               for line in trace)


def test_ao_init_accepts_the_commands_it_composes_and_an_edit_after_it_is_unseen(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(A, "HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run([A.git_binary(), "init", "-q"], cwd=root, check=True)
    monkeypatch.setattr(cli, "_reviewer_probe", lambda cfg, timeout=cli.REVIEW_PROBE_TIMEOUT: {
        "configured": True, "ok": True, "route": "fixture-reviewer", "binary": sys.executable,
        "version": "fixture", "reason": "exact nonce echoed", "kind": "success"})
    args = SimpleNamespace(name=None, profile="agy-codex", implementer=None, model=None, effort=None,
                           reviewer_model=None, agent=None, no_mcp=True, rules=False, watchdog=False)

    assert cli.cmd_init({"root": str(root)}, args) == 0

    assert "the implementer's command, the architect's command" in capsys.readouterr().out
    assert {role: entry["by"] for role, entry in A.turn_record(str(root)).items()} == {
        "implementer": "ao init", "architect": "ao init", "reviewer": "ao init", "hunter": "ao init"}
    assert [path for path, _, _ in A.unseen_harness_config(str(root)) if path.endswith("command")] == []
    with open(os.path.join(str(root), ".ao", "config.json"), encoding="utf-8") as fh:
        architect = json.load(fh)["architect"]
    _store(str(root), architect=dict(architect, argv=architect["argv"] + ["--add-dir", "/"]))
    assert "the architect's command" in [path for path, _, _ in A.unseen_harness_config(str(root))]


# ---- a person is told, under one alarm whoever sees it --------------------------------------------------------------

def test_doctor_names_a_refused_turn_in_the_watchdogs_words_and_under_its_alarm(project, monkeypatch):
    root = project["root"]
    _store(root, architect={"name": "fable", "adapter": "claude-code", "argv": ["sh", "-c", "x", "{prompt}"]})
    cfg = A.load_config(root)
    text = "the watchdog refuses the architect's command: it runs `sh`, which is not claude-code's program (claude)"

    assert cli._turn_problems(cfg) == [("turn-refused:architect", text)]
    assert cli._doctor_severity("turn-refused:architect", text) == "red"
    assert any(text in line and line.startswith("turn command") for line in cli._turn_command_lines(cfg))
    rang = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: rang.append((title, msg, kw)) or True)
    monkeypatch.setattr(cli, "doctor_problems", lambda c: cli._turn_problems(c))

    assert cli._doctor_check(cfg, page=True) == 1
    assert rang == [(f"{A.project_key(root)}: turn-refused:architect", text, {
        "key": "turn-refused:architect", "window": 6 * 3600, "audience": "human", "level": None, "what": text})]
    rang.clear()
    W.tell_turn_refused(root, "architect", text)
    assert [(kw["key"], kw["what"]) for _, _, kw in rang] == [("turn-refused:architect", text)]


def test_doctor_names_no_command_whose_turns_are_switched_off(project):
    root = project["root"]
    _store(root, architect={"name": "fable", "adapter": "claude-code", "argv": ["sh", "-c", "x", "{prompt}"]},
           features={"architect_wake": False, "refill": False})

    assert cli._turn_problems(A.load_config(root)) == []
