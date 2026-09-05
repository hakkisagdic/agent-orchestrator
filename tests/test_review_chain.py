import json
import os
import stat
import subprocess
from types import SimpleNamespace

from ao import cli, lib as A


def _script(path, body):
    open(path, "w").write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, stat.S_IRWXU)
    return path


def _repo_with_change(root):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w").write("x = 1\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"], cwd=root, check=True)
    open(os.path.join(root, "src", "a.py"), "w").write("x = 2\n")


def _args(**kw):
    base = dict(boundary="b", timeout=30, paths=None, commits=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_quota_error_is_not_a_verdict(project, tmp_path, capsys):
    root = project["root"]
    _repo_with_change(root)
    limited = _script(tmp_path / "limited", "echo \"You've hit your session limit · resets 9:50pm (Europe/Istanbul)\"")
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [str(limited), "{prompt}"]})
    assert cli.cmd_review(cfg, _args()) == 3
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert len(files) == 1 and "VERDICT: UNAVAILABLE" in open(os.path.join(root, "semantic-review", files[0])).read()
    assert A.reviews(root, "semantic-review") == [(files[0], "UNAVAILABLE")]
    assert A.rounds(root, "semantic-review") == 0
    st = A.reviewer_state(root)
    assert st["pending_review"] is True and st["until"] is not None


def test_fallback_reviewer_takes_over(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    limited = _script(tmp_path / "limited", "echo 'usage limit reached, resets in 1h 0m'")
    ok = _script(tmp_path / "ok", "echo 'Findings: none.'; echo 'BLOCKER: 0'; echo 'HIGH: 0'; echo 'MEDIUM: 0'; echo 'LOW: 0'; echo 'VERDICT: APPROVED'")
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [str(limited), "{prompt}"],
                                  "fallbacks": [{"id": "r2", "family": "y", "argv": [str(ok), "{prompt}"]}]})
    assert cli.cmd_review(cfg, _args()) == 0
    files = os.listdir(os.path.join(root, "semantic-review"))
    body = open(os.path.join(root, "semantic-review", files[0])).read()
    assert "VERDICT: APPROVED" in body and "fallback" in body and "`r2`" in body
    assert A.reviewer_state(root).get("pending_review") is False


def test_no_verdict_line_is_invalid_not_needs_changes(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    mute = _script(tmp_path / "mute", "echo 'I looked at it. Seems fine.'")
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [str(mute), "{prompt}"]})
    assert cli.cmd_review(cfg, _args()) == 3
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert A.reviews(root, "semantic-review")[0][1] in ("INVALID", "UNAVAILABLE")
    assert A.rounds(root, "semantic-review") == 0


def test_commits_range_reviews_landed_work(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "b"], cwd=root, check=True)
    seen = tmp_path / "seen.txt"
    rec = _script(tmp_path / "rec", f"printf '%s' \"$1\" > {seen}; echo 'BLOCKER: 0'; echo 'HIGH: 0'; echo 'VERDICT: APPROVED'")
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [str(rec), "{prompt}"]})
    assert cli.cmd_review(cfg, _args(commits="HEAD~1..HEAD")) == 0
    assert "x = 2" in seen.read_text()
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert "- commits: HEAD~1..HEAD" in open(os.path.join(root, "semantic-review", files[0])).read()


def test_reopened_window_is_open_work(project):
    from ao import watchdog as W
    root = project["root"]
    A.set_reviewer_state(root, pending_review=True, until=A.time.time() - 5)
    assert any("reviewer window" in r for r in W.open_work(project, root))
    A.set_reviewer_state(root, until=A.time.time() + 3600)
    assert not any("reviewer window" in r for r in W.open_work(project, root))
