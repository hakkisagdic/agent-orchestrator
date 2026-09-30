"""What the retrospective review of MERGE-EVIDENCE found that still held (MERGE-EVIDENCE-2).

The doctor caught any error reading the merge ledger and named no unverified merge, so one broken row
took every recent merge for a checked one. And nothing showed a merge older than `merge.check_days`
is left out: every merge the test made was seconds old.
"""
import os
import subprocess
import time

from ao import cli, lib as A
from tests.test_merge_check import _commit_files, _git


def _merge(root, branch, when=None):
    """Merge `branch` into the current branch with a merge commit, dated `when` seconds ago if given."""
    env = dict(os.environ)
    if when is not None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - when))
        env.update(GIT_COMMITTER_DATE=stamp, GIT_AUTHOR_DATE=stamp)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "--no-ff", "--no-edit", branch],
                   cwd=root, check=True, capture_output=True, env=env)
    return _git(root, "rev-parse", "HEAD")


def test_a_merge_older_than_the_window_is_not_asked_for_and_a_recent_one_is(project):
    root = project["root"]
    base = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    for name in ("old", "new"):
        _git(root, "checkout", "-q", "-b", name, base)
        _commit_files(root, name, **{f"{name}.txt": name})
        _git(root, "checkout", "-q", base)
    old = _merge(root, "old", when=30 * 86400)
    new = _merge(root, "new")

    named = [sha for sha, _ in A.unverified_merges(root, project)]

    assert named == [new] and old not in named


def test_the_doctor_says_when_the_merge_ledger_cannot_be_read(project, monkeypatch, tmp_path):
    from ao import email, telegram
    monkeypatch.setattr(email, "CONF", str(tmp_path / "no-email.json"))
    monkeypatch.setattr(telegram, "CONF", str(tmp_path / "no-telegram.json"))

    def broken(root, cfg):
        raise ValueError("the merge ledger's chain breaks at row 3")

    monkeypatch.setattr(A, "unverified_merges", broken)

    assert dict(cli.doctor_problems(project))["unverified-merge"].startswith(
        "merge ledger: cannot be read (the merge ledger's chain breaks at row 3)")
