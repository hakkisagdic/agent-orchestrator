"""A rule is asked as the command it runs, behind wrappers and git's global options (ALLOWLIST-NORMALIZE).

The allowlist check asked each rule the forbidden commands in their plain forms and in a rewriter's. A rule a
person writes for an agent's everyday work - `Bash(git -C:*)` to read a status elsewhere, `Bash(timeout 60
pytest:*)` to bound a test run - admits a forbidden command behind the words it fixes: `git -C . commit -n`
skips the commit hook, `timeout 60 pytest -p x` loads any plugin. Each rule is now also read as the command it
runs, with each program that runs what follows it, a rewriter's wrappers and git's global options taken off,
and a rule that admits a forbidden command only that way is named once, saying so.
"""
import pytest

from ao import allowlist as AL


@pytest.mark.parametrize("words, runs", [
    ("timeout 60 pytest", "pytest"),
    ("timeout -k 5 60 pytest -q", "pytest -q"),
    ("nice -n 10", ""),
    ("env FOO=1 -u BAR git status", "git status"),
    ("rtk proxy git commit", "git commit"),
    ("rtk git -C . commit", "git commit"),
    ("git -C /repo -c a=b --no-pager commit -n", "git commit -n"),
    ("git --git-dir=.git status", "git status"),
    ("sudo -u root timeout 5 sh", "sh"),
    ("xargs -n 1 git add", "git add"),
    ("ls -la", "ls -la"),
    ("git status", "git status"),
])
def test_each_wrapper_and_git_global_option_is_taken_off(words, runs):
    assert AL.unwrap(words.split()) == runs.split()


@pytest.mark.parametrize("rule, plain", [
    ("Bash(timeout 60 pytest:*)", "Bash(pytest:*)"),
    ("Bash(nice:*)", "Bash(*)"),
    ("Bash(git -C:*)", "Bash(git:*)"),
    ("Bash(rtk git -C /repo commit *)", "Bash(git commit:*)"),
    ("Bash(git status:*)", None),                  # already the command it runs
    ("Bash(timeout 60 pytest -q)", None),          # an exact command admits nothing after it
    ("Bash(timeout * pytest:*)", None),            # a wildcard inside fixes no words to read
    ("Read", None),
])
def test_a_rule_is_read_as_the_command_it_runs(rule, plain):
    assert AL.normalized_rule(rule) == plain


def _problems(*rules, role="implementer"):
    return AL.problems(["x", "--allowedTools", ",".join(rules)], {}, role=role)


@pytest.mark.parametrize("rule, reason, command", [
    ("Bash(git -C:*)", "skips the commit hook", "git commit --no-verify -m x"),
    ("Bash(git -C /repo commit:*)", "skips the commit hook", "git commit --no-verify -m x"),
    ("Bash(timeout 60 pytest:*)", "runs arbitrary code", "pytest -p x"),
    ("Bash(nice -n 10:*)", "skips the commit hook", "git commit --no-verify -m x"),
    ("Bash(env FOO=1 git:*)", "skips the commit hook", "git commit --no-verify -m x"),
    ("Bash(rtk git -C:*)", "skips the commit hook", "git commit --no-verify -m x"),
])
def test_a_rule_that_admits_a_forbidden_command_behind_its_words_is_named_once(rule, reason, command):
    assert _problems(rule) == [(f"{reason}, as the command it runs", command, rule)]


def test_everyday_rules_behind_the_same_words_are_not_named():
    assert _problems("Bash(git status:*)", "Bash(git diff:*)", "Bash(timeout 60 git status:*)",
                     "Bash(git -C . status:*)", "Bash(xargs -n 1 git add:*)", "Bash(ao status:*)") == []


def test_a_rule_named_for_a_form_it_admits_as_it_stands_is_not_named_again():
    found = _problems("Bash(rtk git:*)")
    assert found and all(rule == "Bash(rtk git:*)" for _, _, rule in found)
    assert not [reason for reason, _, _ in found if reason.endswith("as the command it runs")]


def test_the_doctor_line_says_the_rule_admits_it_as_the_command_it_runs():
    line = AL.describe("implementer", "qoder", _problems("Bash(timeout:*)"))
    assert line == ("implementer (qoder): Bash(timeout:*) admits `git commit --no-verify -m x` "
                    "(skips the commit hook, as the command it runs)")
