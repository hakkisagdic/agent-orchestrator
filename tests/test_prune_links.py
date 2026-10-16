"""Git cleanup could follow linked administration outside the repository; refuse it first (PRUNE-LINKS)."""
import contextlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A


def _git(root, *args):
    return subprocess.run([A.git_binary(), *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit_merge_gates(root):
    # Merge-check reads the resulting tree's definitions (C6), so cleanup tests need
    # gates in their fixture's commit to reach the worktree and lock lifecycle.
    (Path(root) / ".ao" / "gates.json").write_text(json.dumps({
        "gates": {"check": {"run": "unused"}}, "profiles": {"full": ["check"]}}), encoding="utf-8")
    _git(root, "add", ".ao/gates.json")
    _git(root, "commit", "-qm", "declare merge gates")


def _link(path, target, kind):
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("junctions require Windows")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                       check=True, capture_output=True)
    else:
        try:
            path.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this machine cannot make a symbolic link")


def _unlink(path, kind):
    if kind == "junction":
        path.rmdir()
    else:
        path.unlink()


def _poison(root, tmp_path, scope, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "valuable").write_bytes(b"keep these bytes")
    (outside / "gitdir").write_text(str(tmp_path / "missing" / ".git"), encoding="utf-8")
    worktrees = Path(root) / _git(root, "rev-parse", "--git-common-dir") / "worktrees"
    if scope == "folder":
        path = worktrees
    else:
        worktrees.mkdir(exist_ok=True)
        path = worktrees / "foreign"
    _link(path, outside, kind)
    return path, outside


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("scope", ["folder", "entry"])
@pytest.mark.parametrize("present", [False, True], ids=["prune", "remove"])
def test_slice_cleanup_refuses_links_without_touching_outside(project, tmp_path, monkeypatch, kind, scope, present):
    root = project["root"]
    path, outside = _poison(root, tmp_path, scope, kind)
    tree = tmp_path / "retired"
    if present:
        tree.mkdir()
        (tree / "keep").write_bytes(b"checkout stays")
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    calls, output = [], A._git_output

    def record(root, *args, **kwargs):
        calls.append(args)
        return output(root, *args, **kwargs)

    monkeypatch.setattr(A, "_git_output", record)
    try:
        with pytest.raises(A.WorktreeCleanupRefused) as refusal:
            A.prune_worktree(root, {"path": str(tree), "branch": ""}, apply=True)
        assert str(path) in str(refusal.value)
        assert "remove the link or junction" in str(refusal.value)
        assert "ao prunes no worktree while it is there" in str(refusal.value)
        assert {p.name: p.read_bytes() for p in outside.iterdir()} == before
        assert not any(args[0] == "worktree" for args in calls)
        if present:
            assert (tree / "keep").read_bytes() == b"checkout stays"
    finally:
        _unlink(path, kind)


@pytest.mark.parametrize("operation", ["prune", "remove"])
@pytest.mark.parametrize("unreadable", ["common", "worktrees", "entry"])
def test_unreadable_administration_runs_no_cleanup(project, tmp_path, monkeypatch, operation, unreadable):
    root = project["root"]
    common = Path(root) / _git(root, "rev-parse", "--git-common-dir")
    folder = common / "worktrees"
    folder.mkdir()
    entry = folder / "unreadable"
    entry.mkdir()
    blocked = {"common": common, "worktrees": folder, "entry": entry}[unreadable]
    scan, lstat, output = os.scandir, os.lstat, A._git_output
    calls = []

    def unreadable_stat(**kwargs):
        raise PermissionError("cannot inspect entry")

    def unreadable_lstat(path, *args, **kwargs):
        if unreadable == "common" and Path(path) == folder:
            raise PermissionError("cannot search common directory")
        return lstat(path, *args, **kwargs)

    @contextlib.contextmanager
    def unreadable_scan(path):
        if Path(path) == blocked:
            raise PermissionError("cannot list administration")
        with scan(path) as entries:
            if unreadable == "entry" and Path(path) == folder:
                yield iter([SimpleNamespace(path=str(entry), stat=unreadable_stat)])
            else:
                yield entries

    def record(root, *args, **kwargs):
        calls.append(args)
        return output(root, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", unreadable_scan)
    monkeypatch.setattr(os, "lstat", unreadable_lstat)
    monkeypatch.setattr(A, "_git_output", record)
    with pytest.raises(A.WorktreeCleanupRefused, match="ao prunes no worktree while it cannot be read"):
        A._git_worktree_cleanup(root, operation, *([] if operation == "prune" else ["--force", "unused"]))
    assert not any(args[0] == "worktree" for args in calls)


def test_a_dangling_link_is_refused_before_git_runs(project, tmp_path, monkeypatch):
    root = project["root"]
    folder = Path(root) / ".git" / "worktrees"
    _link(folder, tmp_path / "missing", "symlink")
    calls, output = [], A._git_output

    def record(root, *args, **kwargs):
        calls.append(args)
        return output(root, *args, **kwargs)

    monkeypatch.setattr(A, "_git_output", record)
    try:
        with pytest.raises(A.WorktreeCleanupRefused, match="remove the link or junction"):
            A._git_worktree_cleanup(root, "prune")
        assert calls == [("rev-parse", "--git-common-dir")]
    finally:
        folder.unlink()


def test_an_ordinary_stale_worktree_is_pruned_from_a_linked_checkout(project, tmp_path):
    root = project["root"]
    caller, stale = tmp_path / "caller", tmp_path / "stale"
    _git(root, "worktree", "add", "--detach", str(caller), "HEAD")
    _git(root, "worktree", "add", "--detach", str(stale), "HEAD")
    stale.rename(tmp_path / "moved")
    admin = Path(root) / ".git" / "worktrees" / "stale"
    assert admin.is_dir()
    A._git_worktree_cleanup(str(caller), "prune")
    assert not admin.exists()
    assert caller.is_dir()
    A._git_worktree_cleanup(root, "remove", "--force", str(caller))
    assert not caller.exists()


def test_a_link_in_the_common_directory_is_refused_from_a_linked_checkout(project, tmp_path):
    root = project["root"]
    caller = tmp_path / "caller"
    _git(root, "worktree", "add", "--detach", str(caller), "HEAD")
    path, outside = _poison(root, tmp_path, "entry", "symlink")
    try:
        with pytest.raises(A.WorktreeCleanupRefused, match="ao prunes no worktree while it is there"):
            A._git_worktree_cleanup(str(caller), "prune")
        assert (outside / "valuable").read_bytes() == b"keep these bytes"
    finally:
        path.unlink()


def test_windows_reparse_attributes_are_refused_on_older_python(project, monkeypatch):
    root = project["root"]
    folder = Path(root) / ".git" / "worktrees"
    folder.mkdir()
    real = os.lstat

    def reparse(path, *args, **kwargs):
        if Path(path) == folder:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", reparse)
    with pytest.raises(A.WorktreeCleanupRefused, match="remove the link or junction"):
        A._git_worktree_cleanup(root, "prune")


def test_failed_lane_start_reports_the_cleanup_refusal(project, tmp_path):
    root = project["root"]
    tree = tmp_path / "lane"
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "worktree", "add", "-b", "lane/test", str(tree), "HEAD")
    tree.rename(tmp_path / "moved")
    path, outside = _poison(root, tmp_path, "entry", "symlink")
    try:
        failure = A._drop_lane_branch(root, "lane/test", base)
        assert "the branch lane/test stays" in failure
        assert str(path) in failure and "ao prunes no worktree" in failure
        assert _git(root, "rev-parse", "lane/test") == base
        assert (outside / "valuable").read_bytes() == b"keep these bytes"
    finally:
        path.unlink()


def test_prove_reports_cleanup_refusal_as_a_failed_check(project, tmp_path, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    paths = []

    def steps(*args):
        paths.append(_poison(root, tmp_path, "entry", "symlink")[0])
        return True, None, None

    monkeypatch.setattr(cli, "_prove_steps", steps)
    try:
        ok, failed, fix, _ = cli._prove_throwaway(project)
        assert not ok and "worktree cleanup failed" in failed
        assert str(paths[0]) in failed and "ao prunes no worktree" in failed
        assert fix and list(tmp_path.glob("ao-prove-*/tree/.git"))
        assert (tmp_path / "outside" / "valuable").read_bytes() == b"keep these bytes"
    finally:
        for path in paths:
            path.unlink()


def test_merge_check_reports_cleanup_refusal_and_releases_its_lock(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    _commit_merge_gates(root)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    released, paths = [], []
    monkeypatch.setattr(cli, "_take_gate_lock", lambda cfg, wait: lambda: released.append(True))

    def gate(*args):
        paths.append(_poison(root, tmp_path, "entry", "symlink")[0])
        return "", 0

    monkeypatch.setattr(cli, "_run_gate", gate)
    try:
        assert cli.cmd_merge_check(project, SimpleNamespace(
            branch="HEAD", into="HEAD", profile="full", wait=0)) == 1
        out = capsys.readouterr().out
        assert "worktree cleanup failed" in out and str(paths[0]) in out
        assert "ao prunes no worktree" in out and released == [True]
        assert not A.merge_checks(root)[-1]["passed"]
        assert list(tmp_path.glob("ao-merge-check-*/result/.git"))
        assert (tmp_path / "outside" / "valuable").read_bytes() == b"keep these bytes"
    finally:
        for path in paths:
            path.unlink()


def _fail_git_cleanup(monkeypatch, tmp_path, operation):
    output, calls = A._git_output, []

    def fail(root, *args, **kwargs):
        if args[:1] == ("worktree",):
            calls.append(args[1])
        if args[:2] == ("worktree", operation):
            if operation == "remove":
                return output(root, "worktree", "remove", "--force", str(tmp_path / "not-a-worktree"))
            return output(root, "worktree", "prune", "--ao-test-invalid-option")
        return output(root, *args, **kwargs)

    monkeypatch.setattr(A, "_git_output", fail)
    return calls


@pytest.mark.parametrize("operation", ["remove", "prune"])
def test_merge_check_keeps_a_pass_when_git_cleanup_fails(project, tmp_path, monkeypatch, capsys, operation):
    root = project["root"]
    _commit_merge_gates(root)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    released = []
    monkeypatch.setattr(cli, "_take_gate_lock", lambda cfg, wait: lambda: released.append(True))
    monkeypatch.setattr(cli, "_run_gate", lambda *args: ("", 0))
    calls = _fail_git_cleanup(monkeypatch, tmp_path, operation)

    assert cli.cmd_merge_check(project, SimpleNamespace(
        branch="HEAD", into="HEAD", profile="full", wait=0)) == 0

    assert A.merge_checks(root)[-1]["passed"]
    assert released == [True] and calls == ["remove", "prune"]
    assert not list(tmp_path.glob("ao-merge-check-*"))
    assert "worktree cleanup incomplete" in capsys.readouterr().out


@pytest.mark.parametrize("operation", ["remove", "prune"])
def test_prove_keeps_a_pass_when_git_cleanup_fails(project, tmp_path, monkeypatch, capsys, operation):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(cli, "_prove_steps", lambda *args: (True, None, None))
    calls = _fail_git_cleanup(monkeypatch, tmp_path, operation)

    assert cli._prove_throwaway(project)[:3] == (True, None, None)

    assert calls == ["remove", "prune"]
    assert not list(tmp_path.glob("ao-prove-*"))
    assert "worktree cleanup incomplete" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("operation", ["prune", "remove"])
def test_a_differently_cased_worktrees_link_is_refused(project, tmp_path, monkeypatch, kind, operation):
    root = project["root"]
    common = Path(root) / ".git"
    probe = common / "CaseProbe"
    probe.write_bytes(b"case probe")
    try:
        if not (common / "caseprobe").exists():
            pytest.skip("this filesystem does not fold case")
    finally:
        probe.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "valuable").write_bytes(b"keep these bytes")
    path = common / "Worktrees"
    _link(path, outside, kind)
    calls, output = [], A._git_output

    def record(root, *args, **kwargs):
        calls.append(args)
        return output(root, *args, **kwargs)

    monkeypatch.setattr(A, "_git_output", record)
    try:
        with pytest.raises(A.WorktreeCleanupRefused, match="ao prunes no worktree while it is there"):
            A._git_worktree_cleanup(root, operation, *([] if operation == "prune" else ["--force", "unused"]))
        assert calls == [("rev-parse", "--git-common-dir")]
        assert (outside / "valuable").read_bytes() == b"keep these bytes"
    finally:
        _unlink(path, kind)


def test_a_non_directory_worktrees_entry_is_refused(project, monkeypatch):
    root = project["root"]
    path = Path(root) / ".git" / "worktrees"
    path.write_bytes(b"not a directory")
    calls, output = [], A._git_output

    def record(root, *args, **kwargs):
        calls.append(args)
        return output(root, *args, **kwargs)

    monkeypatch.setattr(A, "_git_output", record)
    with pytest.raises(A.WorktreeCleanupRefused, match="remove the non-directory entry"):
        A._git_worktree_cleanup(root, "prune")
    assert calls == [("rev-parse", "--git-common-dir")]
    assert path.read_bytes() == b"not a directory"


def _fail_worktree_add(monkeypatch):
    run, calls = subprocess.run, []

    def fail(args, *positional, **kwargs):
        if args[1:3] == ["worktree", "add"]:
            return subprocess.CompletedProcess(args, 128, b"", b"cannot make the checkout")
        if args[1:2] == ["worktree"]:
            calls.append(args[2])
        return run(args, *positional, **kwargs)

    output = A._git_output

    def record(root, *args, **kwargs):
        if args[:1] == ("worktree",):
            calls.append(args[1])
        return output(root, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(A, "_git_output", record)
    return calls


@pytest.mark.parametrize("caller", ["merge-check", "prove"])
def test_a_failed_add_that_made_nothing_does_not_run_cleanup(project, tmp_path, monkeypatch, capsys, caller):
    root = project["root"]
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    calls = _fail_worktree_add(monkeypatch)
    if caller == "merge-check":
        _commit_merge_gates(root)
        released = []
        monkeypatch.setattr(cli, "_take_gate_lock", lambda cfg, wait: lambda: released.append(True))
        assert cli.cmd_merge_check(project, SimpleNamespace(
            branch="HEAD", into="HEAD", profile="full", wait=0)) == 2
        assert released == [True]
        assert not A.merge_checks(root)
    else:
        result = cli._prove_throwaway(project)
        assert not result[0] and "could not be made" in result[1]
    assert not calls
    assert "worktree cleanup" not in capsys.readouterr().out
    assert not list(tmp_path.glob("ao-merge-check-*"))
    assert not list(tmp_path.glob("ao-prove-*"))


@pytest.mark.parametrize("state", ["removed", "refused", "add-failed", "skipped"])
def test_prove_says_whether_its_worktree_is_gone_or_remains(project, tmp_path, monkeypatch, capsys, state):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(cli, "_hook_execution_probe", lambda inv: {"installed": True, "detail": ""})
    monkeypatch.setattr(cli, "_reviewer_probe", lambda cfg: {"ok": True, "configured": True})
    paths = []

    def steps(*args):
        if state == "refused":
            paths.append(_poison(project["root"], tmp_path, "entry", "symlink")[0])
        return True, None, None

    monkeypatch.setattr(cli, "_prove_steps", steps)
    if state == "add-failed":
        _fail_worktree_add(monkeypatch)
    try:
        assert cli.cmd_prove(project, SimpleNamespace(no_review=state == "skipped")) == (0 if state == "removed" else 1)
        summary = capsys.readouterr().out.splitlines()[-1]
        assert "nothing was committed" in summary
        if state == "refused":
            trees = list(tmp_path.glob("ao-prove-*/tree"))
            assert len(trees) == 1
            assert f"the throwaway worktree remains at {trees[0]}" in summary
            assert "worktree is gone" not in summary
        elif state == "removed":
            assert "the throwaway worktree is gone" in summary
            assert not list(tmp_path.glob("ao-prove-*"))
        else:
            assert "no throwaway worktree was made" in summary
            assert "worktree is gone" not in summary
    finally:
        for path in paths:
            path.unlink()
