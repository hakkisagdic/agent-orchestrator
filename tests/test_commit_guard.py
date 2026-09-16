from ao import cli, lib as A, watchdog as W
from tests.test_attacks import _git, _granted


def test_a_commit_made_around_the_hook_reaches_the_architect_within_a_cycle(project, capsys):
    cfg = _granted(project, capsys)
    root = cfg["root"]
    _git(root, "commit", "-q", "--no-verify", "-m", "granted bytes")
    open(f"{root}/src/a.py", "w", encoding="utf-8").write("value = 'never granted'\n")
    _git(root, "commit", "-q", "--no-verify", "-am", "around the hook")
    stray = _git(root, "rev-parse", "HEAD")

    st = {}
    assert W.report_ungranted_commits(root, "proj", st) == 1
    assert W.report_ungranted_commits(root, "proj", st) == 1
    told = [n for n in A.notices(root, limit=100, include_suppressed=True)
            if n.get("key") == f"ungranted-commits:{stray[:12]}"]
    assert len(told) == 1 and stray[:12] in told[0]["msg"]
    assert st["ungranted_told"] == [stray]


def test_doctor_says_which_guard_holds_for_the_implementer(project):
    every_tool = cli._implementer_commit_guard(project)
    assert "surfaces within one watchdog cycle" in every_tool

    scoped = dict(project, implementer={"adapter": "claude-code", "session": "s1"})
    assert "admits no hook bypass" in cli._implementer_commit_guard(scoped)
    assert cli._implementer_commit_guard(dict(project, implementer={})) is None
