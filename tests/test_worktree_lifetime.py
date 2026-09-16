import glob
import json
import os
import subprocess
import time
from types import SimpleNamespace

from ao import cli, lib as A


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _worktree(root, tmp_path, name):
    path = tmp_path / name
    _git(root, "worktree", "add", "-q", "-b", name, str(path))
    (path / f"{name}.txt").write_text(name, encoding="utf-8")
    _git(path, "add", f"{name}.txt")
    _git(path, "commit", "-q", "-m", name)
    return path


def test_merged_and_rejected_worktrees_go_while_dirty_and_reviewed_ones_stay(project, tmp_path, capsys):
    root = project["root"]
    merged, dirty, reviewed, rejected = (_worktree(root, tmp_path, name)
                                         for name in ("merged", "dirty", "reviewed", "rejected"))
    for name in ("merged", "dirty", "reviewed"):
        _git(root, "merge", "-q", "--no-ff", "--no-edit", name)
    (dirty / "dirty.txt").write_text("changed, never committed", encoding="utf-8")
    (reviewed / ".ao" / "reviews").mkdir(parents=True)
    with open(reviewed / ".ao" / "reviews" / "R-1.json", "w", encoding="utf-8") as fh:
        json.dump({"id": "R-1", "state": "running", "pid": os.getpid(), "submitted_at": int(time.time())}, fh)
    (merged / ".ao").mkdir()
    (merged / ".ao" / "notes.jsonl").write_text('{"kept": true}\n', encoding="utf-8")
    rejected_head = _git(rejected, "rev-parse", "HEAD")
    with open(os.path.join(root, ".ao", "board.md"), "a", encoding="utf-8") as fh:
        fh.write(f"\n## rejected\n- [ABN] an abandoned slice · worktree: {rejected}\n")

    assert cli.cmd_worktrees(project, SimpleNamespace(action="prune", yes=False)) == 0
    assert merged.exists() and rejected.exists()

    assert cli.cmd_worktrees(project, SimpleNamespace(action="prune", yes=True)) == 0

    assert not merged.exists() and not rejected.exists()
    assert dirty.exists() and reviewed.exists()
    left = {os.path.realpath(fact["path"]) for fact in A.worktree_list(root)}
    assert left == {os.path.realpath(p) for p in (root, dirty, reviewed)}
    branches = _git(root, "branch", "--format=%(refname:short)").split()
    assert "merged" not in branches and "rejected" not in branches and "dirty" in branches
    archived = _git(root, "for-each-ref", "--format=%(objectname)", "refs/ao/archive/")
    assert rejected_head in archived.split()
    kept = glob.glob(os.path.join(A.HOME, ".ao", "archive", A.project_key(root), "worktree-merged-*", ".ao",
                                  "notes.jsonl"))
    assert len(kept) == 1
    out = capsys.readouterr().out
    assert "1 uncommitted product change(s)" in out and "review in flight: R-1" in out


def test_the_doctor_names_worktrees_that_may_go_with_their_size(project, tmp_path):
    root = project["root"]
    _worktree(root, tmp_path, "landed")
    _git(root, "merge", "-q", "--no-ff", "--no-edit", "landed")

    text = "\n".join(cli._worktree_lines(project))

    assert "1 may go" in text and "merged into" in text and str(tmp_path / "landed") in text
