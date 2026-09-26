"""A grant admits what a command rewriter makes of the commands it names, and nothing more (GRANTS-RTK).

A machine can run every shell command an agent issues through rtk: a hook the harness runs before
each call rewrites `git diff` to `rtk git diff`, and the harness then checks its rules against the
rewritten command. A grant naming `Bash(git diff:*)` did not admit `rtk git diff`, so an unattended
turn was denied the commands its grant named (measured on qodercli). Every grant a turn ao starts
holds now names, right after each rule for a command rtk rewrites, the same rule for rtk's form of
it. These tests hold each shipped grant to exactly those rules, hold rtk's measured rewrites to the
rules meant to admit them, and hold the forms to the package's own data.
"""
import json
import os

import pytest

from ao import allowlist as AL, cli, language, lib as A, storage, watchdog as W
from tests.scenarios import World

BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"

# What `rtk rewrite` answered on rtk 0.50.0 for a command each shipped grant names: measured, not derived.
MEASURED = (
    ("git status --short", "rtk git status --short"),
    ("git log --oneline -5", "rtk git log --oneline -5"),
    ("git diff --stat", "rtk git diff --stat"),
    ("git add -A", "rtk git add -A"),
    ("ls -la src", "rtk ls -la src"),
    ("cat -n x", "rtk read -n x"),
    ("head -n 5 x", "rtk read x --head-lines 5"),
    ("tail -n 5 x", "rtk read x --tail-lines 5"),
    ("grep -rn foo .", "rtk grep -rn foo ."),
    ("ps aux", "rtk ps aux"),
)
IMPLEMENTER_NAMES = ["git status --short", "git log --oneline -5", "git diff --stat", "git add -A", "ls -la src",
                     "cat -n x", "grep -rn foo ."]

# The rules each shipped grant gains, in order: rtk's form of every command it names that rtk rewrites.
IMPLEMENTER_GAINS = ["Bash(rtk git status:*)", "Bash(rtk git log:*)", "Bash(rtk git diff:*)", "Bash(rtk git add:*)",
                     "Bash(rtk ls:*)", "Bash(rtk read:*)", "Bash(rtk grep:*)"]
GAINED = {
    "claude-code implementer": IMPLEMENTER_GAINS,
    "qoder implementer": IMPLEMENTER_GAINS,
    # Qwen spells a rule's trailing wildcard with a space, and its forms keep that spelling.
    "qwen implementer": [rule.replace(":*)", " *)") for rule in IMPLEMENTER_GAINS],
    # cat, head and tail are all rewritten to rtk read, so the three gain one rule between them.
    "claude-code architect": ["Bash(rtk git status:*)", "Bash(rtk ls:*)", "Bash(rtk read:*)", "Bash(rtk grep:*)",
                              "Bash(rtk ps:*)"],
}
NAMED = {"claude-code implementer": IMPLEMENTER_NAMES, "qoder implementer": IMPLEMENTER_NAMES,
         "qwen implementer": IMPLEMENTER_NAMES,
         "claude-code architect": ["git status --short", "ls -la src", "cat -n x", "head -n 5 x", "tail -n 5 x",
                                   "grep -rn foo .", "ps aux"]}


def _grant(name):
    """(the argv as its adapter declares the grant, the argv a turn ao starts holds, the role) of one shipped grant."""
    if name == "claude-code architect":
        declared = cli._architect_argv("claude-code")
        return declared, A.admit_rewrites(declared)[0], "architect"      # what the watchdog wakes it with
    adapter = A.load_adapter(name.split(" ")[0])
    resume = adapter["resume"]["argv"]
    return resume + list(adapter["options"].get("unattended") or []), A.role_commands(adapter)["implementer"], \
        "implementer"


def _rtk():
    (rtk,) = [rewriter for rewriter in A.command_rewriters() if rewriter["id"] == "rtk"]
    return rtk


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


def _nudge(project, monkeypatch, tmp_path, adapter, program):
    """The argv of the one nudge the watchdog starts for an idle implementer of `adapter`, and the root."""
    cfg = dict(project, implementer={"adapter": adapter, "session": "s1", "name": "dev"})
    _store(project["root"], cfg)
    world = World(cfg, monkeypatch, tmp_path)
    monkeypatch.setattr(W.subprocess, "Popen", lambda argv, **kw: world.spawned.append(argv) or _Exited())
    monkeypatch.setattr(W.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(W.shutil, "which", lambda name, path=None, mode=None: f"/agents/{os.path.basename(name)}")
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)
    world.cycle(dry_run=False)
    (argv,) = _spawned(world, program)
    return argv, world.root


@pytest.mark.parametrize("name", sorted(GAINED))
def test_each_grant_a_turn_starts_with_gains_rtks_form_of_each_rule_rtk_rewrites_and_nothing_else(name):
    declared, composed, role = _grant(name)
    before, after = AL.rules(declared), AL.rules(composed)

    gained = [rule for rule in after if rule not in before]

    assert gained == GAINED[name]
    assert [rule for rule in after if rule not in gained] == before
    assert all(after[after.index(rule) - 1] in before for rule in gained)      # each right after a declared rule
    assert AL.problems(composed, role=role) == []


@pytest.mark.parametrize("name", sorted(GAINED))
def test_what_rtk_made_of_a_command_the_grant_names_is_admitted_where_the_command_was_and_was_denied_before(name):
    declared, composed, _ = _grant(name)
    before, after = AL.rules(declared), AL.rules(composed)

    named = [command for command, _ in MEASURED if any(AL.admits(rule, command) for rule in before)]
    rewritten = [dict(MEASURED)[command] for command in named]

    assert named == NAMED[name]
    assert [command for command in rewritten if any(AL.admits(rule, command) for rule in before)] == []
    assert [command for command in rewritten if not any(AL.admits(rule, command) for rule in after)] == []


def test_every_command_a_shipped_grant_names_is_one_rtk_rewrites_or_one_it_was_measured_to_leave_alone():
    rtk = _rtk()
    unmeasured = []
    for ident in sorted(A.shipped_adapter_ids()):
        argvs = list(A.role_commands(A.load_adapter(ident)).values()) + [cli._architect_argv(ident)]
        for rule in sorted({rule for argv in argvs for rule in AL.rules(argv) if rule.startswith("Bash(")}):
            body = rule[len("Bash("):-1]
            wildcard = next((end for end in (":*", " *") if body.endswith(end)), "")
            words = body[:len(body) - len(wildcard)]
            if words.split(" ")[0] in rtk["unchanged"]:
                continue
            if wildcard and (words in rtk["forms"] or words in rtk["forms"].values()):
                continue
            unmeasured.append(f"{ident}: {rule}")

    # A rule for a command rtk may rewrite, which no measured form covers, is blocked under rtk.
    assert unmeasured == []


def test_a_grant_given_with_an_equals_sign_gains_each_form_once_and_a_second_admission_adds_nothing():
    once, added = A.admit_rewrites(["x", "--allowed-tools=Read,Bash(ls:*),Bash(head:*),Bash(tail:*)"])

    assert once == ["x", "--allowed-tools=Read,Bash(ls:*),Bash(rtk ls:*),Bash(head:*),Bash(rtk read:*),Bash(tail:*)"]
    assert added == ["Bash(rtk ls:*)", "Bash(rtk read:*)"]
    assert A.admit_rewrites(once) == (once, [])


def test_an_exact_rule_a_rule_naming_arguments_of_its_own_and_a_command_rtk_leaves_alone_gain_nothing():
    argv = ["x", "-p", "{prompt}", "--allowedTools",
            "Read,Bash(git status),Bash(git diff --stat:*),Bash(ao status:*),Bash(lsof:*),Edit(/a),mcp__ao__ao_inbox"]

    assert A.admit_rewrites(argv) == (argv, [])
    assert A.admit_rewrites(["x", "-p", "Bash(git diff:*)"]) == (["x", "-p", "Bash(git diff:*)"], [])


def test_a_form_is_only_ever_its_rewriters_own_command_and_plain_words_after_it():
    odd = [{"id": "odd", "command": "rtk", "forms": {
        "ls": "rtk", "cat": "sh -c", "grep": "rtk *", "ps": "rtk ps),Bash(sh -c:*", "head": "rtk ./written-by-an-agent",
        "git status": "rtk git status"}}]
    grant = "Bash(ls:*),Bash(cat:*),Bash(grep:*),Bash(ps:*),Bash(head:*),Bash(git status:*)"

    assert A.admit_rewrites(["x", "--allowedTools", grant], odd)[1] == ["Bash(rtk git status:*)"]


def test_the_shipped_rewriter_declares_every_form_and_wrapper_in_the_shape_ao_reads():
    rtk = _rtk()

    assert A.rewriter_forms(rtk) == rtk["forms"]                 # no form of the package's own is dropped
    assert rtk["unchanged"] and all(isinstance(program, str) and " " not in program for program in rtk["unchanged"])
    assert rtk["runs_any_command"] and all(wrapper.startswith("rtk ") for wrapper in rtk["runs_any_command"])
    assert {words.split(" ")[0] for words in rtk["forms"]}.isdisjoint(rtk["unchanged"])


def test_the_forms_are_read_from_the_package_and_from_no_layer_an_agent_can_write(tmp_path, monkeypatch):
    layer = tmp_path / "user-adapters" / "rewriters"
    layer.mkdir(parents=True)
    (layer / "wide.json").write_text(json.dumps({"id": "wide", "command": "rtk", "forms": {"ls": "rtk proxy ls"}}),
                                     encoding="utf-8")
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user-adapters"))

    assert [rewriter["id"] for rewriter in A.command_rewriters()] == ["rtk"]
    assert A.admit_rewrites(["x", "--allowedTools", "Bash(ls:*)"])[1] == ["Bash(rtk ls:*)"]


def test_the_allowlist_check_reads_a_grant_as_a_turn_holds_it_and_asks_rtks_form_of_each_forbidden_command():
    found = AL.problems(["x", "--allowedTools", "Read,Bash(git log:*)"], role="architect")

    assert [(command, rule) for _, command, rule in found] == [
        ("git log --output=src/app.py", "Bash(git log:*)"),
        ("rtk git log --output=src/app.py", "Bash(rtk git log:*)")]
    assert AL.problems(["x", "--allowedTools", "Bash(rtk proxy:*)"]) == [
        ("runs any command", "rtk proxy sh -c x", "Bash(rtk proxy:*)")]


def test_a_claude_implementer_is_nudged_with_rtks_form_of_the_grant_its_resume_carries(project, monkeypatch, tmp_path):
    argv, root = _nudge(project, monkeypatch, tmp_path, "claude-code", "claude")
    rules = AL.rules(argv)

    assert [rule for rule in rules if rule.startswith("Bash(rtk ")] == IMPLEMENTER_GAINS
    assert rules[rules.index("Bash(git diff:*)") + 1] == "Bash(rtk git diff:*)"
    # Nothing was appended: the grant is the resume's own, and so are the forms in it.
    assert not os.path.exists(os.path.join(root, ".ao", "ledger", "actor-flags.jsonl"))


def test_a_qoder_implementer_is_nudged_with_rtks_form_of_the_grant_it_is_appended_and_the_record_holds_it(
        project, monkeypatch, tmp_path):
    argv, root = _nudge(project, monkeypatch, tmp_path, "qoder", "qodercli")
    grant = argv[argv.index("--allowed-tools") + 1]

    assert [rule for rule in grant.split(",") if rule.startswith("Bash(rtk ")] == IMPLEMENTER_GAINS
    (row,) = storage.read_jsonl(os.path.join(root, ".ao", "ledger", "actor-flags.jsonl"))
    assert row["actor"] == "implementer" and row["flags"][-2:] == ["--allowed-tools", grant]
    assert "options.unattended" in row["reason"] and "command rewriter" in row["reason"]


@pytest.mark.parametrize("prompt,mail", [("prompt.wake", BLOCKED), ("prompt.refill", None)], ids=["report", "refill"])
def test_an_architect_woken_from_the_block_ao_init_wrote_holds_rtks_form_of_its_grant(
        project, monkeypatch, tmp_path, prompt, mail):
    declared = cli._architect_argv("claude-code")
    cfg = dict(project, architect={"adapter": "claude-code", "session": "a1", "name": "fable", "argv": declared})
    _store(project["root"], cfg)
    world = World(cfg, monkeypatch, tmp_path)
    world.transcript_age(900)
    if mail:                                   # a report wakes it; an empty queue with no report wakes it to refill
        world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", mail)

    world.cycle(dry_run=False)

    (argv,) = _spawned(world, "claude")
    assert language.text(cfg, prompt) in argv
    assert [rule for rule in AL.rules(argv) if rule not in AL.rules(declared)] == GAINED["claude-code architect"]
    # The block in .ao/config.json keeps the grant as ao init wrote it; the turn gains the forms when it starts.
    with open(os.path.join(world.root, ".ao", "config.json"), encoding="utf-8") as fh:
        assert json.load(fh)["architect"]["argv"] == declared


def test_a_package_rewriter_that_does_not_parse_raises_rather_than_declaring_nothing(tmp_path, monkeypatch):
    """The allowlist check reads the same declarations: losing them quietly would pass a grant it must refuse."""
    import pytest
    from ao import lib as A

    rewriters = tmp_path / A.REWRITERS_DIR
    rewriters.mkdir(parents=True)
    (rewriters / "broken.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(A, "adapters_dir", lambda: str(tmp_path))

    with pytest.raises(ValueError, match="broken.json"):
        A.command_rewriters()


def test_a_package_without_a_rewriters_directory_declares_none(tmp_path, monkeypatch):
    from ao import lib as A

    monkeypatch.setattr(A, "adapters_dir", lambda: str(tmp_path))
    assert A.command_rewriters() == []
