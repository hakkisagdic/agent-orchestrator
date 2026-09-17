"""Every turn ao starts holds the grant ao composes, and nothing a person's settings add to it (GRANTS-PINNED).

A Claude Code run given no --permission-mode starts in the mode the person's settings name, and a
`claude -p --resume` in the mode a new run would, so an acceptEdits, auto or bypassPermissions
default widened every nudge, every architect wake and the reviewer ao treats as read-only. The
implementer's and the architect's grants lacked commands their own playbook told them to run, so
an unattended turn was silently denied those steps. A codex nudge turned its sandbox off, and flags
that turn approvals off sat in adapters with no reason written down.
"""
import json
import os
import re
import subprocess
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, language, lib as A, storage, watchdog as W
from tests.scenarios import World

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYBOOK = os.path.join(ROOT, "src", "ao", "skill", "SKILL.md")
BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"
TOOL = re.compile(r"(?<![\w.])ao_[a-z][a-z_]*[a-z]")

# The playbook sections each role works from, by number; section 5 is every role's.
SECTIONS = {"implementer": ("3.", "4.", "5.", "8."), "architect": ("5.", "7.")}

# An `ao` command a role's text names that is not that role's to run, and why. Each must still be
# named there and still be outside the grant, or the excuse has outlived what it excused.
NAMED_NOT_RUN = {
    "implementer": {
        "ao lock -- <command>": "runs any command (allowlist.FORBIDDEN); the declared gates take the same lock "
                                "through ao verify",
        "ao lock": "named where an urgent note reaches the implementer; the same command",
        "ao commit-check": "what the commit hook runs against Git's index; ao commit runs it itself",
        "ao hooks status": "how doctor and init prove the hook, not a step of the loop",
        "ao hooks": "installing and removing hooks is a person's (allowlist.FORBIDDEN)",
    },
    "architect": {
        "ao lock": "named where an urgent note reaches the implementer; the architect runs none of the three",
        "ao verify": "as ao lock",
        "ao commit-ok": "as ao lock",
    },
}

# The flags that approve everything, sandbox nothing or check no permission in a command ao starts,
# each declared with its reason; the replaced ones are gone from this list.
KEPT = {
    "aider": {"implementer": ["--yes-always"], "architect": ["--yes-always"]},
    "amazon-q": {"implementer": ["--trust-all-tools"]},
    "amp": {"implementer": ["--dangerously-allow-all"]},
    "antigravity": {"implementer": ["--dangerously-skip-permissions"]},
    "codex": {"implementer": ["--dangerously-bypass-approvals-and-sandbox"]},
    "command-code": {"implementer": ["--yolo"]},
    "copilot": {"implementer": ["--allow-all-tools"]},
    "cursor-agent": {"implementer": ["-f"], "architect": ["-f"]},
    "droid": {"implementer": ["--auto"]},
    "gemini": {"implementer": ["--yolo"]},
    "kilocode": {"implementer": ["--auto"]},
    "kiro": {"implementer": ["--trust-all-tools"]},
    "omp": {"implementer": ["--approval-mode yolo"]},
}


def _after(argv, flag):
    return argv[argv.index(flag) + 1]


def _swapped(argv, flag, value):
    argv = list(argv)
    argv[argv.index(flag) + 1] = value
    return argv


def _store(root, cfg):
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)


def _spawned(world, program):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).endswith(f"/{program}")]


class _Exited:
    """A started turn that has already ended cleanly, so a live nudge runs to its end."""
    pid = 99999
    returncode = 0

    def poll(self):
        return 0


def _nudges_run(world, monkeypatch):
    monkeypatch.setattr(W.subprocess, "Popen", lambda argv, **kw: world.spawned.append(argv) or _Exited())
    monkeypatch.setattr(W.time, "sleep", lambda seconds: None)


def _grant(ident, role):
    if role == "architect":
        return AL.rules(cli._architect_argv(ident))
    return AL.rules(A.role_commands(A.load_adapter(ident))["implementer"])


def _texts(role):
    playbook = open(PLAYBOOK, encoding="utf-8").read()
    sections = [part for part in re.split(r"(?m)^## ", playbook)[1:] if part.split(" ", 1)[0] in SECTIONS[role]]
    # Every language's prompts: a Turkish project's turns are told the same commands (LANGUAGE-PROMPTS).
    prompts = []
    for chosen in ({"language": lang} for lang in language.LANGUAGES):
        if role == "implementer":
            prompts += [language.text(chosen, "prompt.nudge"), W.parked_note(chosen, "D-1", "S2"),
                        W.secondary_note(chosen, {"name": "other", "root": "/elsewhere", "item": "S9"})]
        else:
            prompts += [language.text(chosen, "prompt.wake"), language.text(chosen, "prompt.refill")]
    return "\n".join(sections + prompts)


def _commands(text):
    """{span: [command]} for each `ao …` span a text names: `"…"`, `<…>` and `…` filled, `a|b` expanded."""
    found = {}
    for span in re.findall(r"`(ao\s[^`]*)`", text):
        span = " ".join(span.split())
        filled = span.replace("…", "x")
        filled = re.sub(r'"[^"]*"', "x", re.sub(r"<[^<>]*>", "x", filled))
        commands = [""]
        for word in filled.split(" "):
            commands = [f"{done} {choice}".strip() for done in commands for choice in word.split("|")]
        found[span] = commands
    return found


def test_every_command_ao_composes_for_a_claude_turn_names_the_mode_and_tools_its_adapter_pins():
    adapter = A.load_adapter("claude-code")
    resume = adapter["resume"]["argv"]
    composed = {"implementer": resume + A.unattended_flags(adapter, resume)[0],
                "architect": cli._architect_argv("claude-code"),
                "reviewer": A.compose_reviewer("claude-code", model="m")["argv"]}

    for role, argv in composed.items():
        assert A.pinned_argv(argv, role) == (argv, []), role
        assert argv.count("--permission-mode") == 1 and _after(argv, "--permission-mode") == "dontAsk", role
        assert "--dangerously-skip-permissions" not in argv, role
    assert _after(composed["reviewer"], "--tools") == "Read,Grep,Glob"
    assert AL.reviewer_problems(composed["reviewer"]) == []
    assert A.load_adapter("claude-code")["resume"]["continue_last"][-2:] == ["--permission-mode", "dontAsk"]


def test_a_reviewer_in_another_mode_or_with_a_tool_beyond_reading_is_named():
    reviewer = A.compose_reviewer("claude-code", model="m")["argv"]

    for mode in ("default", "plan", "acceptEdits", "auto", "bypassPermissions"):
        assert f"it runs with --permission-mode {mode}, where ao pins dontAsk" in AL.reviewer_problems(
            _swapped(reviewer, "--permission-mode", mode))
    assert "it runs with --tools Read,Grep,Glob,Edit, where ao pins Read,Grep,Glob" in AL.reviewer_problems(
        _swapped(reviewer, "--tools", "Read,Grep,Glob,Edit"))
    assert AL.reviewer_problems(_swapped(reviewer, "--tools", "Read")) == []


def test_a_reviewer_route_composed_before_the_pin_runs_with_it(project, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: ("/agents/claude", "2.1.261"))
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kw:
                        seen.append(argv) or {"ok": True, "out": "VERDICT: APPROVED"})
    route = {"id": "claude-reviewer", "argv": ["claude", "-p", "{prompt}", "--model", "m",
                                               "--allowedTools", "Read,Grep,Glob", "--strict-mcp-config"]}

    label, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, "review this", 60, False, route)

    assert attempt["ok"] and label == "claude-reviewer"
    (argv,) = seen
    assert argv[-4:] == ["--permission-mode", "dontAsk", "--tools", "Read,Grep,Glob"]
    assert "ao appends --permission-mode dontAsk --tools Read,Grep,Glob" in capsys.readouterr().out


def test_the_hunter_is_held_to_the_reviewers_pin(project, monkeypatch, capsys):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"))
    with open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def f():\n    return 1\n")
    subprocess.run(["git", "add", "src"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "src"], cwd=root, check=True)
    seen = []
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, **kw: seen.append(argv) or {"ok": True, "out": ""})
    hunter = ["claude", "-p", "{prompt}", "--allowedTools", "Read,Grep,Glob", "--strict-mcp-config"]
    run = SimpleNamespace(action="run", fingerprint=None)

    assert cli.cmd_hunt(dict(project, hunter={"id": "h1", "argv": hunter}), run) == 0
    (argv,) = seen
    assert argv[-4:] == ["--permission-mode", "dontAsk", "--tools", "Read,Grep,Glob"]

    widened = dict(project, hunter={"id": "h1", "argv": hunter + ["--permission-mode", "acceptEdits"]})
    assert cli.cmd_hunt(widened, run) == 2
    assert "it runs with --permission-mode acceptEdits, where ao pins dontAsk" in capsys.readouterr().out
    assert len(seen) == 1


def test_an_architect_block_composed_before_the_pin_is_woken_with_it_and_the_flag_is_recorded(
        project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)

    world.cycle(dry_run=False)

    (argv,) = _spawned(world, "claude")
    assert argv[-2:] == ["--permission-mode", "dontAsk"]
    (row,) = storage.read_jsonl(os.path.join(world.root, ".ao", "ledger", "actor-flags.jsonl"))
    assert row["actor"] == "architect" and row["flags"] == ["--permission-mode", "dontAsk"]


def test_a_claude_implementer_is_nudged_in_the_pinned_mode_with_no_grant_appended(project, monkeypatch, tmp_path):
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "s1", "name": "claude"})
    _store(project["root"], cfg)
    world = World(cfg, monkeypatch, tmp_path)
    _nudges_run(world, monkeypatch)
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)

    world.cycle(dry_run=False)

    (argv,) = _spawned(world, "claude")
    assert argv[1:3] == ["--resume", "s1"]
    assert argv.count("--permission-mode") == 1 and _after(argv, "--permission-mode") == "dontAsk"
    assert "--dangerously-skip-permissions" not in argv
    assert AL.rules(argv) == A.load_adapter("claude-code")["options"]["implementer_tools"].split(",")


@pytest.mark.parametrize("ident,role", [("claude-code", "implementer"), ("claude-code", "architect"),
                                        ("qwen", "implementer"), ("qoder", "implementer")])
def test_each_role_is_granted_every_ao_command_and_tool_its_playbook_and_prompts_name(ident, role):
    text = _texts(role)
    rules = _grant(ident, role)
    named = _commands(text)
    excused = NAMED_NOT_RUN[role]

    missing = [f"`{span}`: {command}" for span, commands in sorted(named.items()) if span not in excused
               for command in commands if not any(AL.admits(rule, command) for rule in rules)]
    # A harness whose grant names ao's MCP tools must name each one its text does; one that names
    # none reaches the mailbox and the board through the CLI commands checked above.
    if any(rule.startswith("mcp__") for rule in rules):
        missing += [tool for tool in sorted(set(TOOL.findall(text))) if f"mcp__ao__{tool}" not in rules]

    assert missing == []
    for span, why in excused.items():
        assert why and span in named, (role, span)
        assert not all(any(AL.admits(rule, command) for rule in rules) for command in named[span]), (role, span)


def test_the_architect_edits_the_coordination_files_its_routine_names_and_no_other():
    argv = cli._architect_argv("claude-code")
    rules = AL.rules(argv)
    text = _texts("architect")

    assert not {"Bash", "Bash(*)", "Edit", "Write", "NotebookEdit"} & set(rules)
    edited = sorted(rule[len("Edit(/"):-1] for rule in rules if rule.startswith("Edit("))
    assert edited == [".ao/backlog.md", ".ao/board.md", ".ao/inbox/**"]
    assert all(path.rstrip("*") in text for path in edited)
    assert not [rule for rule in rules if rule.startswith(("Write(", "NotebookEdit("))]
    assert AL.problems(argv, role="architect") == []
    assert AL.admits("Bash(rm agent-mail/*)", "rm agent-mail/x src/app.py")


def test_the_doctor_names_a_mode_other_than_the_pin_a_product_write_and_an_architect_grant_older_than_its_adapter(
        project):
    old = cli._architect_argv("claude-code")
    at = old.index("--allowedTools") + 1
    old[at] = ",".join(rule for rule in old[at].split(",") if not rule.startswith("Edit(")) + ",Bash(rm agent-mail/*)"
    old = _swapped(old, "--permission-mode", "acceptEdits")
    cfg = dict(project, architect={"adapter": "claude-code", "name": "fable", "argv": old})

    problems = dict(cli._actor_grant_problems(cfg))

    assert "it runs with --permission-mode acceptEdits, where ao pins dontAsk" in problems["actor-mode:architect"]
    assert "Bash(rm agent-mail/*) admits `rm agent-mail/x src/app.py` (removes a product file)" \
        in problems["actor-grant:architect"]
    assert "Edit(/.ao/board.md)" in problems["architect-grant"] and "options.architect_tools" in problems["architect-grant"]
    assert "architect-grant" not in dict(cli._actor_grant_problems(
        dict(project, architect={"adapter": "claude-code", "name": "fable", "argv": cli._architect_argv("claude-code")})))


def test_a_codex_nudge_waits_for_a_person_and_starts_with_its_bypass_only_once_allowed(project, monkeypatch, tmp_path):
    machine = tmp_path / "machine-settings.json"
    monkeypatch.setenv("AO_SETTINGS", str(machine))
    cfg = dict(project, implementer={"adapter": "codex", "session": "s1", "name": "dev"})
    _store(project["root"], cfg)
    world = World(cfg, monkeypatch, tmp_path)
    _nudges_run(world, monkeypatch)
    monkeypatch.setattr(W.shutil, "which", lambda name, path=None, mode=None: f"/agents/{os.path.basename(name)}")
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)

    world.cycle(dry_run=False)

    assert _spawned(world, "codex") == []
    assert "no person has allowed that on this machine" in world.verdict and world.verdict.endswith("not nudging")
    assert any(audience == "human" and "nudge refused" in title for title, _, audience, _ in world.notices)
    assert "with the network" in dict(cli._actor_grant_problems(cfg))["nudge-refused:codex"]

    machine.write_text(json.dumps({"watchdog": {"bypass_adapters": ["codex"]}}), encoding="utf-8")
    world.cycle(dry_run=False)

    (argv,) = _spawned(world, "codex")
    assert argv[-1] == "--dangerously-bypass-approvals-and-sandbox"
    problems = dict(cli._actor_grant_problems(cfg))
    assert "nudge-refused:codex" not in problems and "outside its sandbox" in problems["actor-grant:implementer"]


@pytest.mark.parametrize("ident,flags", [("hermes", []), ("reasonix", ["--permission-mode=workspace-write"]),
                                         ("droid", ["--auto", "medium"])])
def test_an_unattended_turn_starts_with_the_narrower_grant_its_adapter_documents(ident, flags):
    adapter = A.load_adapter(ident)
    nudge = A.role_commands(adapter)["implementer"]

    assert A.unattended_flags(adapter, adapter["resume"]["argv"])[0] == flags
    assert not A._holds(nudge, adapter["options"]["trust_all"])
    assert A.bypass_refusal(adapter, flags) is None


def test_a_harness_with_no_approval_flag_is_nudged_with_nothing_appended_and_still_named_as_granting_everything():
    for ident in ("grok", "kimi", "pi"):
        adapter = A.load_adapter(ident)
        assert "trust_all" in adapter["options"] and adapter["options"]["trust_all"] is None
        # A null trust_all was appended as list(None), and the nudge failed on it.
        assert A.unattended_flags(adapter, adapter["resume"]["argv"]) == ([], None)
        assert AL.grants_everything(adapter["resume"]["argv"], adapter["options"])


def test_every_flag_that_turns_approvals_off_in_a_command_ao_starts_is_declared_with_its_reason():
    shipped = {ident: A.load_adapter(ident) for ident in A.shipped_adapter_ids()}
    carried = {ident: {role: A.bypass_arguments(argv) for role, argv in A.role_commands(adapter).items()
                       if A.bypass_arguments(argv)} for ident, adapter in shipped.items()}

    assert {ident: A.bypass_problems(adapter) for ident, adapter in shipped.items() if A.bypass_problems(adapter)} == {}
    assert {ident: roles for ident, roles in carried.items() if roles} == KEPT
    assert all(len(entry["why"]) > 80 for adapter in shipped.values() for entry in A.declared_bypass(adapter).values())
    assert [ident for ident, adapter in shipped.items()
            if any(entry.get("sandbox") for entry in A.declared_bypass(adapter).values())] == ["codex"]


def test_a_new_flag_that_turns_approvals_off_is_refused_until_its_reason_is_written():
    newcomer = {"id": "newcomer", "name": "n", "verified": "untested", "contract": 1,
                "send": {"argv": ["nc", "-p", "{prompt}"]},
                "resume": {"argv": ["nc", "--resume", "{session}", "-p", "{prompt}", "--yolo"]},
                "options": {"trust_none": None, "trust_none_why": "a fixture"}}
    unexplained = "the architect and the implementer commands carry `--yolo`, and `options.bypass` gives no reason for it there"

    assert unexplained in A.validate_adapter(newcomer)

    newcomer["options"]["bypass"] = {"--yolo": {"roles": ["implementer", "architect"], "why": "a reason"},
                                     "-f": {"roles": ["implementer"], "why": "stale"}}
    assert A.bypass_problems(newcomer) == [
        "`options.bypass` gives a reason for `-f` in the implementer command, which does not carry it"]
    assert A.bypass_arguments(["x", "--sandbox", "danger-full-access", "--permission-mode=bypassPermissions",
                               "--approval-mode", "auto-edit", "-p", "{prompt}"]) == [
        "--sandbox danger-full-access", "--permission-mode=bypassPermissions"]


def test_a_rule_is_read_the_way_the_harness_reads_a_wildcard():
    assert AL.admits("Bash(ls *)", "ls") and not AL.admits("Bash(ls *)", "lsof") and AL.admits("Bash(ls*)", "lsof")
    assert AL.admits("Bash(ls:*)", "ls -la") and AL.admits("Bash(git * main)", "git push origin main")
    assert not AL.admits("Bash(* --help *)", "npm --help") and AL.admits("Bash(* --help *)", "npm --help x")
    assert AL.admits("Bash(git diff:*)", "git diff --output=src/app.py")
    assert not AL.admits("Bash(ao writers)", "ao writers --clean")
