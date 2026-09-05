import json
import os
import subprocess
from types import SimpleNamespace

from ao import cli, lib as A


def _fake(*lines):
    """A reviewer that prints these lines — as a python -c argv, so it runs on every platform."""
    import sys
    return [sys.executable, "-c", "; ".join(f"print({l!r})" for l in lines), "{prompt}"]


def _repo_with_change(root):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"], cwd=root, check=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 2\n")


def _args(**kw):
    base = dict(boundary="b", timeout=30, paths=None, commits=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_quota_error_is_not_a_verdict(project, tmp_path, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake("You've hit your session limit · resets 9:50pm (Europe/Istanbul)")})
    assert cli.cmd_review(cfg, _args()) == 3
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert len(files) == 1 and "VERDICT: UNAVAILABLE" in open(os.path.join(root, "semantic-review", files[0]), encoding="utf-8").read()
    assert A.reviews(root, "semantic-review") == [(files[0], "UNAVAILABLE")]
    assert A.rounds(root, "semantic-review") == 0
    st = A.reviewer_state(root)
    assert st["pending_review"] is True and st["until"] is not None


def test_fallback_reviewer_takes_over(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake("usage limit reached, resets in 1h 0m"),
                                  "fallbacks": [{"id": "r2", "family": "y",
                                                 "argv": _fake("Findings: none.", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0", "VERDICT: APPROVED")}]})
    assert cli.cmd_review(cfg, _args()) == 0
    files = os.listdir(os.path.join(root, "semantic-review"))
    body = open(os.path.join(root, "semantic-review", files[0]), encoding="utf-8").read()
    assert "VERDICT: APPROVED" in body and "fallback" in body and "`r2`" in body
    assert A.reviewer_state(root).get("pending_review") is False


def test_no_verdict_line_is_invalid_not_needs_changes(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake("I looked at it. Seems fine.")})
    assert cli.cmd_review(cfg, _args()) == 3
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert A.reviews(root, "semantic-review")[0][1] in ("INVALID", "UNAVAILABLE")
    assert A.rounds(root, "semantic-review") == 0


def test_commits_range_reviews_landed_work(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "b"], cwd=root, check=True)
    import sys
    seen = tmp_path / "seen.txt"
    rec = [sys.executable, "-c", f"import sys; open({str(seen)!r}, 'w', encoding='utf-8').write(sys.argv[1]); "
           "print('BLOCKER: 0'); print('HIGH: 0'); print('MEDIUM: 0'); print('LOW: 0'); print('VERDICT: APPROVED')", "{prompt}"]
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": rec})
    assert cli.cmd_review(cfg, _args(commits="HEAD~1..HEAD")) == 0
    assert "x = 2" in seen.read_text(encoding="utf-8")
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert "- commits: HEAD~1..HEAD" in open(os.path.join(root, "semantic-review", files[0]), encoding="utf-8").read()


def test_reopened_window_is_open_work(project):
    from ao import watchdog as W
    root = project["root"]
    A.set_reviewer_state(root, pending_review=True, until=A.time.time() - 5)
    assert any("reviewer window" in r for r in W.open_work(project, root))
    A.set_reviewer_state(root, until=A.time.time() + 3600)
    assert not any("reviewer window" in r for r in W.open_work(project, root))
