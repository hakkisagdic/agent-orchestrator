import json
import pathlib
import subprocess
from types import SimpleNamespace

from ao import allowlist as AL, cli, lib as A

ADAPTERS = sorted(pathlib.Path(A.adapters_dir()).glob("*.json"))


def test_each_rule_is_asked_what_it_would_admit():
    assert AL.admits("Bash(git commit:*)", "git commit --no-verify -m x")
    assert AL.admits("Bash(ao:*)", "ao push allow 30")
    assert AL.admits("Bash(find:*)", "find . -exec sh -c x ;")
    assert not AL.admits("Bash(ao commit -m:*)", "ao push allow 30")
    assert not AL.admits("Bash(ao commit-ok:*)", "ao commit --no-verify")
    assert not AL.admits("Bash(git add:*)", "git -c core.hooksPath=/dev/null commit -m x")
    assert not AL.admits("Bash(ao doctor)", "ao doctor --check --notify")
    assert not AL.admits("Read", "python3 -c x")


def test_the_shipped_claude_code_implementer_grant_admits_no_bypass():
    adapter = json.loads((pathlib.Path(A.adapters_dir()) / "claude-code.json").read_text())
    argv = adapter["resume"]["argv"]

    assert AL.problems(argv, adapter["options"]) == []
    assert AL.rules(argv) == adapter["options"]["implementer_tools"].split(",")
    for rule in AL.rules(argv):
        admitted = [command for _, command in AL.FORBIDDEN if AL.admits(rule, command)]
        assert admitted == [], (rule, admitted)


def test_every_shipped_adapter_is_audited_and_none_admits_a_bypass_through_a_pattern():
    report = {}
    for path in ADAPTERS:
        adapter = json.loads(path.read_text())
        report[path.stem] = AL.problems((adapter.get("resume") or {}).get("argv") or [],
                                        adapter.get("options") or {})

    assert report["claude-code"] == []
    assert report["kiro"] == [("grants every tool", "*", "*")]
    assert all(command == "*" for found in report.values() for _, command, _ in found)


def test_the_architect_grant_admits_no_bypass():
    tools = A.load_adapter("claude-code")["options"]["architect_tools"]
    assert AL.problems(["claude", "-p", "{prompt}", "--allowedTools", tools], role="architect") == []


def test_doctor_names_an_actor_whose_grant_admits_a_bypass(project):
    cfg = dict(project, architect={"name": "architect", "argv": [
        "claude", "-p", "{prompt}", "--allowedTools", "Read,Bash(git commit:*),Bash(ao:*)"]})

    problems = dict(cli._actor_grant_problems(cfg))

    text = problems["actor-grant:architect"]
    assert "Bash(git commit:*) admits `git commit --no-verify -m x` (skips the commit hook)" in text
    assert "Bash(ao:*) admits `ao push allow 30` (opens a push window)" in text
    assert "every tool" in problems["actor-grant:implementer"]


def test_doctor_names_a_rule_written_for_a_command_rewriter_that_admits_what_the_command_would_not(project):
    # A person whose turns a hook runs through rtk might widen a grant to get past it (GRANTS-RTK).
    cfg = dict(project, architect={"name": "architect", "argv": [
        "claude", "-p", "{prompt}", "--allowedTools", "Read,Bash(rtk git:*),Bash(rtk:*)"]})

    text = dict(cli._actor_grant_problems(cfg))["actor-grant:architect"]

    assert "Bash(rtk git:*) admits `rtk git commit --no-verify -m x` (skips the commit hook)" in text
    assert ("runs arbitrary code", "rtk python3 -c x", "Bash(rtk:*)") in AL.problems(cfg["architect"]["argv"])
    assert AL.problems(["claude", "--allowedTools", "Bash(rtk run:*)"]) == [
        ("runs any command", "rtk run sh -c x", "Bash(rtk run:*)")]


def test_ao_commit_checks_authority_first_and_passes_nothing_but_the_message(project, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(cli, "cmd_commit_check", lambda cfg, args: 1)
    # What landed is compared with the grant after Git commits (#64); tested in test_landed_tree.
    monkeypatch.setattr(A, "landed_commit_problem", lambda root: None)

    assert cli.cmd_commit(project, SimpleNamespace(message="land it", file=None)) == 1
    assert calls == []

    monkeypatch.setattr(cli, "cmd_commit_check", lambda cfg, args: 0)
    assert cli.cmd_commit(project, SimpleNamespace(message="land it", file=None)) == 0
    assert calls == [[A.git_binary(), "commit", "-m", "land it"]]
    assert cli.cmd_commit(project, SimpleNamespace(message="land it", file="msg.txt")) == 2



import pytest


@pytest.mark.parametrize("rule, reason", [
    ("Bash(ao features off review --by Alice)", "switches review off"),
    ("Bash(ao waive review --slice S1 --by Alice --why late)", "waives a gate"),
    ("Bash(ao waive review --slice S1 --by Alice --why *)", "waives a gate"),
    ("Bash(ao person-review --by Alice --verdict APPROVED --digest abc)", "records a person's review"),
    ("Bash(ao catchup --move-only S1 --by Alice)", "states which waived slices only moved code"),
    ("Bash(ao init --profile x --review-tier same-family --by Alice)", "opts into same-family review"),
    ("Bash(timeout 60 ao push allow 5)", "opens a push window"),
    ("Bash(ao harness seen --by Alice --digest 0123abcd)", "records harness configuration as seen"),
])
def test_a_person_s_act_written_out_in_full_is_named_by_its_first_words(rule, reason):
    """GRANTS-PREFIX (#114): each forbidden command was asked as a sample, `--by p`, so a rule that fixed another name
    after --by admitted the act and passed the audit."""
    found = AL.problems(["claude", "-p", "{prompt}", "--allowedTools", f"Read,{rule}"], {})

    assert [(r, granted) for r, _, granted in found] == [(reason, rule)]


@pytest.mark.parametrize("rule", ["Bash(ao catchup --limit 5)", "Bash(ao init --profile claude-kiro)",
                                  "Bash(ao features on review)", "Bash(ao status:*)",
                                  "Bash(ao harness seen --by Alice)"])
def test_a_rule_that_starts_no_person_s_act_is_not_named(rule):
    assert AL.problems(["claude", "-p", "{prompt}", "--allowedTools", f"Read,{rule}"], {}) == []


def test_an_unknown_ao_subcommand_is_not_assumed_harmless():
    assert AL.problems(["claude", "--allowedTools", "Bash(ao waiver-list)"])
