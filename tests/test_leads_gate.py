"""The commit hook stands aside only on Git's answer that no marker is tracked, and install writes over no hook it
did not find (LEADS-GATE-HOOK).

The hook's guard stood aside when its own queries failed, and a subdirectory project's staged marker deletion read
as no marker; on a branch not born yet it asks ao, which reads this machine's record that the repository is
enrolled. ao's exact hook without its execute bit read as current, so install left a hook Git never ran, and install
wrote over a hook another tool put on a path it had found free.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A
from tests import standin
from tests.test_commit_trailer import _git

MARKER = b"ao-project-v1\n"
INSTALL = SimpleNamespace(action="install", allow_shared_hooks=False)


def _run(cwd, *argv):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *argv], cwd=str(cwd),
                          capture_output=True, text=True)


# ---- a6fd673ee0685921: the hook stands aside only on Git's answer that no marker is tracked -------

def _hooked(tmp_path, body=None):
    """A repository whose ao project is its proj/ directory, the marker committed, and ao's pre-commit hook."""
    outer = tmp_path / "outer"
    (outer / "proj").mkdir(parents=True)
    assert _run(outer, "init", "-q").returncode == 0
    (outer / "proj" / ".ao-project").write_bytes(MARKER)
    assert _run(outer, "add", "proj/.ao-project").returncode == 0
    assert _run(outer, "commit", "-q", "-m", "enrol proj").returncode == 0
    hook = outer / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(body or cli._render_local_hook("pre-commit", "proj"))
    hook.chmod(0o755)
    return outer


@pytest.mark.parametrize("how", ("marker deletion staged", "project moved away"))
def test_the_hook_refuses_where_its_queries_find_no_answer(tmp_path, how):
    outer = _hooked(tmp_path)
    head = _run(outer, "rev-parse", "HEAD").stdout
    if how == "marker deletion staged":
        assert _run(outer, "rm", "-q", "--cached", "proj/.ao-project").returncode == 0
    else:
        os.rename(str(outer / "proj"), str(outer / "elsewhere"))

    done = _run(outer, "commit", "-q", "--allow-empty", "-m", "unchecked")

    # It went on to ao, which this PATH does not hold: refused, where the v4 guard let the commit through.
    assert done.returncode != 0 and "agent-orchestrator: ao not found" in done.stderr
    assert _run(outer, "rev-parse", "HEAD").stdout == head


def test_the_hook_asks_ao_while_any_ref_tracks_the_marker_and_on_a_branch_not_born_yet(tmp_path):
    """It stands aside only where no ref's history tracks a marker; a project a person removed by hand, its hook left
    in place, asks ao - here no ao is found, and the commit is refused - until the hook is uninstalled
    (LEADS-GATE-HOOK)."""
    outer = _hooked(tmp_path)
    assert _run(outer, "rm", "-q", "proj/.ao-project").returncode == 0
    assert not (outer / "proj").exists()
    assert _run(outer, "commit", "-q", "--no-verify", "-m", "leave ao").returncode == 0

    assert "ao not found" in _run(outer, "commit", "-q", "--allow-empty", "-m", "the hook left in place").stderr
    never = tmp_path / "never"
    never.mkdir()
    assert _run(never, "init", "-q").returncode == 0
    assert _run(never, "commit", "-q", "--allow-empty", "-m", "no marker anywhere").returncode == 0
    hook = never / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(cli._render_local_hook("pre-commit", "."))
    hook.chmod(0o755)
    assert _run(never, "commit", "-q", "--allow-empty", "-m", "a repository ao never governed").returncode == 0
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    assert _run(unborn, "init", "-q").returncode == 0
    hook = unborn / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(cli._render_local_hook("pre-commit", "."))
    hook.chmod(0o755)
    # A branch not born yet goes on to ao, since only ao can say whether the repository was enrolled (LEADS-GATE):
    # with no ao it is refused, and an ao that finds nothing tracked and nothing recorded lets the first commit in.
    assert "ao not found" in _run(unborn, "commit", "-q", "--allow-empty", "-m", "first").stderr
    standin.program(tmp_path / "bin", "ao", "import sys\nfrom ao import cli\nsys.exit(cli.main(sys.argv[1:]))\n")
    home = tmp_path / "home"
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
               PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    done = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
                           "-m", "first"], cwd=str(unborn), env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_v4_hook_reads_as_legacy_and_install_puts_the_current_one_in_its_place(project):
    root = project["root"]
    hooks = Path(cli._ao_hook_inventory(root)["active_dir"])
    for role in ("pre-commit", "pre-push"):
        (hooks / role).write_bytes(cli._render_v4_local_hook(role, "."))
        (hooks / role).chmod(0o755)

    before = cli._ao_hook_inventory(root)
    assert {target["static_state"] for target in before["targets"] if target["active"]} == \
        {"legacy (behavior unverified)"}
    assert cli.cmd_hooks(project, INSTALL) == 0

    for target in cli._ao_hook_inventory(root)["targets"]:
        if target["active"]:
            assert target["static_state"] == "current-local (behavior unverified)"
            assert b"# agent-orchestrator: ao-hook-v6 " in (hooks / target["role"]).read_bytes()


# ---- 39aaf2997fe78820: ao's exact hook without its execute bit is replaced ------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode is not a Windows hook property")
def test_install_makes_a_copy_of_the_current_hook_that_git_does_not_run_one_it_runs(project):
    root = project["root"]
    path = Path(cli._ao_hook_inventory(root)["active_dir"]) / "pre-commit"
    path.write_bytes(cli._render_local_hook("pre-commit", "."))
    path.chmod(0o644)

    before = [t for t in cli._ao_hook_inventory(root)["targets"] if t["active"] and t["role"] == "pre-commit"][0]
    assert (before["static_state"], before["eligible"]) == ("legacy (not executable)", True)
    assert cli.cmd_hooks(project, INSTALL) == 0

    assert os.stat(path).st_mode & 0o100
    after = [t for t in cli._ao_hook_inventory(root)["targets"] if t["active"] and t["role"] == "pre-commit"][0]
    assert after["static_state"] == "current-local (behavior unverified)"
    assert cli._hook_portable(path.read_bytes()) == cli._render_local_hook("pre-commit", ".")


# ---- c973cfc5d2fd5b01: install writes over no hook it did not find ---------------------------------

@pytest.mark.parametrize("found", ("a free path", "ao's older hook"))
def test_install_leaves_a_hook_another_tool_wrote_after_install_read_the_path(project, monkeypatch, capsys, found):
    root = project["root"]
    path = Path(cli._ao_hook_inventory(root)["active_dir"]) / "pre-commit"
    if found == "ao's older hook":
        path.write_bytes(cli._render_v4_local_hook("pre-commit", "."))
        path.chmod(0o755)
    theirs, real_fsync, written = b"#!/bin/sh\n# the project's own checks\nexit 0\n", os.fsync, []
    A.record_enrollment(root)          # kept already, as after any commit check: the sync below is the hook's

    def another_tool_writes(fd):
        # Install has read the path, and syncs the pre-commit hook it is about to put there.
        if not written:
            path.write_bytes(theirs)
            written.append(path)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", another_tool_writes)
    assert cli.cmd_hooks(project, INSTALL) == 1

    assert path.read_bytes() == theirs
    assert "it is left as it stands" in capsys.readouterr().out
    assert not [name for name in os.listdir(str(path.parent)) if name.startswith(".ao-hook-")]


@pytest.mark.parametrize("how", ("renamed into place", "rewritten in place, its size and time kept"))
def test_install_leaves_a_hook_another_tool_put_there_after_install_classified_ao_s_older_one(project, monkeypatch,
                                                                                                capsys, how):
    """The path read as ao's older hook, and another tool put its own there before install replaced it: the replace was
    not held to the file it had classified, and the other tool's hook was lost. A rewrite in place that keeps the
    size and sets the time back is caught by the bytes and by the time the inode changed (LEADS-GATE-HOOK)."""
    root = project["root"]
    path = Path(cli._ao_hook_inventory(root)["active_dir"]) / "pre-commit"
    path.write_bytes(cli._render_v4_local_hook("pre-commit", "."))
    path.chmod(0o755)
    theirs, real_classify, real_identity = b"#!/bin/sh\n# the project's own checks\nexit 0\n", cli._classify_hook, \
        cli._file_identity
    writing, swapped = [], []
    A.record_enrollment(root)

    def identity(at):
        writing.append(at)                          # install is about to write: what it reads next is the last look
        return real_identity(at)

    def classified_then_swapped(at, role, *args, **kwargs):
        answer = real_classify(at, role, *args, **kwargs)
        if writing and not swapped and str(at) == str(path):
            swapped.append(at)
            if how == "renamed into place":
                other = path.with_name("theirs.tmp")
                other.write_bytes(theirs)
                os.replace(other, path)             # renamed into place once install classified ao's older hook
            else:
                kept = os.stat(path)
                with open(path, "r+b") as fh:       # the same inode and length, the modified time set back
                    fh.write(theirs.ljust(kept.st_size, b"#")[:kept.st_size])
                os.utime(path, ns=(kept.st_atime_ns, kept.st_mtime_ns))
        return answer
    monkeypatch.setattr(cli, "_file_identity", identity)
    monkeypatch.setattr(cli, "_classify_hook", classified_then_swapped)

    assert cli.cmd_hooks(project, INSTALL) == 1

    assert swapped and path.read_bytes().startswith(theirs)
    assert "another hook was put there while install read it" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="Windows takes a free path by rename, which publishes the whole file")
def test_a_hook_written_where_no_hard_link_can_be_made_is_executable_only_once_whole(tmp_path, monkeypatch):
    """Created executable and then written, the hook stood empty for a moment, and Git ran the empty file, which
    lets every commit through (LEADS-GATE)."""
    path, temporary = tmp_path / "pre-commit", tmp_path / ".ao-hook-x"
    temporary.write_bytes(b"#!/bin/sh\nexit 1\n")
    seen, real_fsync = [], os.fsync

    def no_link(*args, **kwargs):
        raise OSError(1, "this file system makes no hard link")

    def watching_fsync(fd):
        seen.append(os.stat(path).st_mode & 0o111 if path.exists() else None)
        return real_fsync(fd)

    monkeypatch.setattr(os, "link", no_link)
    monkeypatch.setattr(os, "fsync", watching_fsync)

    cli._hook_put_on_free_path(str(temporary), str(path), b"#!/bin/sh\nexit 1\n")

    assert seen and all(mode == 0 for mode in seen), seen                    # never executable while written
    assert path.read_bytes() == b"#!/bin/sh\nexit 1\n" and os.stat(path).st_mode & 0o777 == 0o755


def test_an_orphan_branch_of_an_enrolled_repository_is_no_new_repository(project, capsys):
    """`git switch --orphan`, the marker unstaged: HEAD not born and nothing staged read as a repository before its
    marker's first commit, and the hook stood aside (LEADS-GATE). The history tracks the marker on main."""
    root = project["root"]
    _git(root, "switch", "-q", "--orphan", "elsewhere")
    _git(root, "rm", "-q", "--cached", "-r", "--ignore-unmatch", ".")

    enrollment = cli._project_enrollment(root)

    assert enrollment["state"] == "broken" and "tracks .ao-project on another branch" in enrollment["detail"]
    guard = cli._hook_enrollment_guard("$top", ".")
    run = subprocess.run(["sh", "-c", "top=$1\n" + guard + "echo goes-on-to-ao\n", "sh", root],
                         capture_output=True, text=True)
    assert run.stdout.strip() == "goes-on-to-ao", run.stderr


def test_a_repository_whose_every_marker_ref_was_deleted_is_refused_where_this_machine_enrolled_it(project, capsys):
    """Every ref whose history tracked the marker deleted, an orphan branch, `.ao/config.json` taken from the tree:
    Git answered that the repository was never enrolled, and the hook stood aside for an unreviewed root commit.
    The hook asks ao on a branch not born yet, and ao reads what this machine recorded (LEADS-GATE)."""
    root = project["root"]
    assert A.registered_key(root) is None
    cli.main(["-C", root, "commit-check"])                    # a commit-check of the enrolled project records it
    capsys.readouterr()
    assert A.enrollment_record(root)["root"] == os.path.realpath(root)
    _git(root, "switch", "-q", "--orphan", "elsewhere")
    for ref in _git(root, "for-each-ref", "--format=%(refname)").split():
        _git(root, "update-ref", "-d", ref)
    os.remove(os.path.join(root, ".ao", "config.json"))      # the implementer writes any file in the tree
    assert _git(root, "log", "--all", "--format=%H", "--", ".ao-project").strip() == ""

    enrollment = cli._project_enrollment(root)

    assert enrollment["state"] == "broken" and "recorded the project as enrolled" in enrollment["detail"]
    guard = cli._hook_enrollment_guard("$top", ".")
    run = subprocess.run(["sh", "-c", "top=$1\n" + guard + "echo goes-on-to-ao\n", "sh", root],
                         capture_output=True, text=True)
    assert run.stdout.strip() == "goes-on-to-ao", run.stderr
    # What ao removes with the project, it forgets: the branch is a new repository's again.
    os.remove(A.enrollment_record_path(root)[0])
    assert cli._project_enrollment(root)["state"] == "uninitialized"


def test_hooks_install_records_the_enrolled_project_and_writes_nothing_into_its_tree(project):
    """The hook asks ao on a branch not born yet, and ao reads the record: kept from the install on, not from the first
    commit check after it, and through the machine registry alone, `.ao/` as it was (LEADS-GATE)."""
    root = project["root"]
    tree = sorted(os.listdir(os.path.join(root, ".ao")))
    assert A.enrollment_record(root) is None

    assert cli.cmd_hooks(dict(project), INSTALL) == 0

    assert A.enrollment_record(root)["root"] == os.path.realpath(root)
    assert sorted(os.listdir(os.path.join(root, ".ao"))) == tree


def test_an_enrollment_this_machine_cannot_record_refuses_the_install_and_the_commit(project, monkeypatch, capsys):
    """A record that could not be written was passed over and the install succeeded: a branch not born yet then read
    as a new repository's. Each refuses now, and the record leaves `.ao/project-key` alone (LEADS-GATE)."""
    root = project["root"]
    from ao import storage

    def unwritable(path, data, *args, **kwargs):
        raise PermissionError(13, "Permission denied", path)
    monkeypatch.setattr(storage, "replace_file_durably", unwritable)

    assert cli.cmd_hooks(dict(project), INSTALL) == 1
    assert "cannot record that the project is enrolled" in capsys.readouterr().out
    assert cli.main(["-C", root, "commit-check"]) == 1
    assert "cannot record that the project is enrolled" in capsys.readouterr().out
    assert not os.path.exists(os.path.join(root, ".ao", "project-key"))


def test_a_repository_claiming_the_project_key_leaves_the_enrolled_repositorys_record(project, tmp_path):
    """The record was kept under the project's key, and `.ao/project-key` in a tree claims a key whose `.ao/` is gone: a
    nested repository claiming it, its commit checked, wrote its own record over the enrolled one's, and an orphan
    commit there found nothing. The record is named by its repository, and none other's is written over
    (LEADS-GATE-HOOK)."""
    root = project["root"]
    key = A.project_key(root)
    assert A.record_enrollment(root) is None
    kept = A.enrollment_record(root)
    nested = Path(root, "nested")
    nested.mkdir()
    _git(str(nested), "init", "-q")
    (nested / ".ao").mkdir()
    (nested / ".ao" / "project-key").write_text(json.dumps({"key": key, "root": str(nested)}), encoding="utf-8")
    os.rename(os.path.join(root, ".ao"), os.path.join(root, ".ao-gone"))         # the enrolled project's .ao/ gone

    assert A.record_enrollment(str(nested)) is None

    assert A.enrollment_record(root) == kept
    assert A.enrollment_record(str(nested))["repository"] != kept["repository"]
    assert Path(root, ".ao-gone", "project-key").exists() and not Path(root, ".ao", "project-key").exists()


def test_a_linked_worktree_of_an_enrolled_repository_finds_its_record(project, tmp_path):
    """The record named the project by its worktree's path: a linked worktree made after it, an orphan branch
    there and every marker ref deleted, read as a new repository's. It names the repository (LEADS-GATE)."""
    root = project["root"]
    assert A.record_enrollment(root) is None
    linked = str(tmp_path / "linked")
    _git(root, "worktree", "add", "-q", "--detach", linked)
    _git(linked, "switch", "-q", "--orphan", "elsewhere")
    for ref in _git(root, "for-each-ref", "--format=%(refname)").split():
        _git(root, "update-ref", "-d", ref)

    assert A.enrollment_record(linked)["repository"] == A.repository_identity(root)
    assert "recorded the project as enrolled" in cli._project_enrollment(linked)["detail"]


def test_an_install_on_an_orphan_branch_of_an_enrolled_repository_records_it(project):
    """Install recorded the project only where it read as enrolled: on an orphan branch it read as broken, the record was
    passed over, and with every marker ref deleted next the repository read as a new one. Anything but uninitialized
    is recorded (LEADS-GATE-HOOK)."""
    root = project["root"]
    _git(root, "switch", "-q", "--orphan", "elsewhere")
    assert cli._project_enrollment(root)["state"] == "broken" and A.enrollment_record(root) is None

    cli.cmd_hooks(dict(project), INSTALL)

    assert A.enrollment_record(root)["repository"] == A.repository_identity(root)
    for ref in _git(root, "for-each-ref", "--format=%(refname)").split():
        _git(root, "update-ref", "-d", ref)
    os.remove(os.path.join(root, ".ao", "config.json"))
    assert "recorded the project as enrolled" in cli._project_enrollment(root)["detail"]


def test_a_branch_made_from_before_the_marker_s_commit_goes_on_to_ao_which_refuses_it(project):
    """A branch made from a commit before the marker's tracks no marker in its index or its HEAD: the hook stood aside,
    and an unreviewed commit landed. While any ref's history tracks the marker the hook asks ao, which reads this
    machine's record (LEADS-GATE-HOOK)."""
    root = project["root"]
    assert A.record_enrollment(root) is None
    empty = subprocess.run(["git", "mktree"], cwd=root, input="", capture_output=True, text=True,
                           check=True).stdout.strip()
    before = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit-tree", empty,
                             "-m", "before ao"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    _git(root, "switch", "-q", "-c", "old", before)

    guard = cli._hook_enrollment_guard("$top", ".")
    run = subprocess.run(["sh", "-c", "top=$1\nroot=$1\n" + guard + "echo goes-on-to-ao\n", "sh", root],
                         capture_output=True, text=True)
    assert run.stdout.strip() == "goes-on-to-ao", run.stderr
    assert "made from before the marker's commit" in cli._project_enrollment(root)["detail"]


def test_the_repository_recorded_is_the_one_asked_about_whatever_git_dir_says(project, tmp_path, monkeypatch):
    """`GIT_DIR=/other ao -C /victim hooks install` put the hook into /victim and recorded /other as enrolled: the
    identity was asked with ao's own repository binding. It is asked with none (LEADS-GATE-HOOK)."""
    root = project["root"]
    other = tmp_path / "other"
    other.mkdir()
    _git(str(other), "init", "-q")
    victim = A.repository_identity(root)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    assert A.repository_identity(root) == victim
    assert A.record_enrollment(root) is None
    monkeypatch.delenv("GIT_DIR")
    assert A.enrollment_record(root)["repository"] == victim


def test_a_commit_given_another_home_still_finds_the_repositorys_record(project, tmp_path, monkeypatch):
    """The record was kept in ~/.ao: `HOME=<empty> git commit` had the hook's ao read a home with no record in it, and an
    orphan branch with every marker ref deleted read as a new repository's. It is kept in the repository's git
    directory (LEADS-GATE-HOOK)."""
    root = project["root"]
    assert A.record_enrollment(root) is None
    _git(root, "switch", "-q", "--orphan", "elsewhere")
    for ref in _git(root, "for-each-ref", "--format=%(refname)").split():
        _git(root, "update-ref", "-d", ref)
    os.remove(os.path.join(root, ".ao", "config.json"))
    empty = tmp_path / "another-home"
    empty.mkdir()
    monkeypatch.setenv("HOME", str(empty))
    monkeypatch.setattr(A, "HOME", str(empty))

    assert A.enrollment_record(root) is not None
    assert "recorded the project as enrolled" in cli._project_enrollment(root)["detail"]
