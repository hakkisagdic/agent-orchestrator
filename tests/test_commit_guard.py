from ao import cli, lib as A, telegram, watchdog as W
from tests.test_attacks import _git, _granted


def test_a_commit_made_around_the_hook_reaches_a_person_within_a_cycle(project, capsys, monkeypatch):
    """LEDGER-HISTORY: it reached the architect as a notice row nobody sends. It is a person's alarm now, rung
    once for the commits it names however often the cycle raises it."""
    cfg = _granted(project, capsys)
    root = cfg["root"]
    rang = []
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: rang.append(msg) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: 1)
    _git(root, "commit", "-q", "--no-verify", "-m", "granted bytes")
    open(f"{root}/src/a.py", "w", encoding="utf-8").write("value = 'never granted'\n")
    _git(root, "commit", "-q", "--no-verify", "-am", "around the hook")
    stray = _git(root, "rev-parse", "HEAD")

    told = W.report_ungranted_commits(root, "proj")
    assert W.report_ungranted_commits(root, "proj") == told and told.startswith("1 commit(s)") and stray[:12] in told
    assert len(rang) == 1 and stray[:12] in rang[0]
    (episode,) = [e for e in A.active_alarms(A.project_key(root)) if e["key"] == "landed-without-grant"]
    assert episode["count"] == 2


def test_doctor_says_which_guard_holds_for_the_implementer(project):
    every_tool = cli._implementer_commit_guard(project)
    assert "surfaces within one watchdog cycle" in every_tool

    scoped = dict(project, implementer={"adapter": "claude-code", "session": "s1"})
    assert "admits no hook bypass" in cli._implementer_commit_guard(scoped)
    assert cli._implementer_commit_guard(dict(project, implementer={})) is None
