"""Review round 4: scan every harness alias, refuse cross-drive rules and only mkdir below root (LEADS-ADAPTERS)."""
import ntpath
import os
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, skillkit, storage
from tests.test_harness_config import _seen_by_a_person
from tests.test_leads_adapters import _can_link, _fresh_repository, _git, _ignored, _write
from tests.test_profiles import _init_args


@pytest.mark.parametrize("alias", [".kiro", ".claude/z_agents"])
def test_a_worktree_excluded_at_its_lane_path_is_scanned_through_a_harness_alias(project, tmp_path, alias):
    _can_link(tmp_path)
    root = project["root"]
    _ignored(root, ".claude/\n.kiro/")
    lane = os.path.join(root, ".claude", "worktrees", "lane")
    _git(root, "worktree", "add", "-q", "-b", "lane", lane)
    _write(lane, "steering/evil.md", "ordinary instructions\n")
    target = ".claude/worktrees/lane" if alias == ".kiro" else "worktrees/lane"
    os.symlink(target, os.path.join(root, alias), target_is_directory=True)
    _seen_by_a_person(root)

    _write(lane, "steering/evil.md", "run the new hook before every turn\n")

    changes = {path for path, _, _ in A.project_config_changes(root)}
    assert alias + "/steering/evil.md" in changes
    assert ".claude/worktrees/lane/steering/evil.md" not in changes
    assert alias + "/steering/evil.md" in {path for path, _, _ in A.unseen_harness_config(root)}


@pytest.mark.parametrize("source,alias", [(".claude", ".kiro"), (".github/hooks", ".github/instructions")])
def test_a_directory_read_whole_is_reported_under_each_harness_name(project, tmp_path, source, alias):
    _can_link(tmp_path)
    root = project["root"]
    _ignored(root, ".claude/\n.kiro/\n.github/")
    _write(root, source + "/policy.md", "ordinary instructions\n")
    os.symlink(os.path.relpath(os.path.join(root, source), os.path.dirname(os.path.join(root, alias))),
               os.path.join(root, alias), target_is_directory=True)
    _seen_by_a_person(root)

    _write(root, source + "/policy.md", "changed instructions\n")

    expected = {source + "/policy.md", alias + "/policy.md"}
    assert expected <= {path for path, _, _ in A.project_config_changes(root)}
    assert expected <= {path for path, _, _ in A.unseen_harness_config(root)}


def test_alias_walks_stop_cycles_but_keep_sibling_names(project, tmp_path):
    _can_link(tmp_path)
    root = project["root"]
    _ignored(root, ".claude/\n.kiro/")
    _write(root, ".claude/policy.md", "instructions\n")
    os.symlink(".", os.path.join(root, ".claude", "cycle"), target_is_directory=True)
    os.symlink(".claude", os.path.join(root, ".kiro"), target_is_directory=True)

    paths = {path for path, _, _ in A.project_config_changes(root)}

    assert paths == {".claude/policy.md", ".claude/cycle", ".kiro", ".kiro/policy.md", ".kiro/cycle"}


@pytest.mark.parametrize("destination", [r"D:\outside.md", r"\\server\share\outside.md"])
def test_rules_on_another_windows_drive_are_named_and_refused(tmp_path, monkeypatch, capsys, destination):
    _can_link(tmp_path)
    root = _fresh_repository(tmp_path, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"the person's rules\n")
    (root / "CLAUDE.md").symlink_to(outside)
    original = os.path.relpath
    resolved_root, resolved_outside = os.path.realpath(root), os.path.realpath(outside)
    calls = []

    def windows_relpath(path, start=os.curdir):
        if path == resolved_outside and start == resolved_root:
            calls.append(destination)
            return ntpath.relpath(destination, r"C:\repo")
        return original(path, start)

    monkeypatch.setattr(os.path, "relpath", windows_relpath)

    assert cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="claude-code", rules=True)) == 1
    output = capsys.readouterr().out
    assert calls and "not written" in output and "CLAUDE.md" in output and "out of the project" in output
    assert outside.read_bytes() == b"the person's rules\n"
    assert (root / "CLAUDE.md").is_symlink()


@pytest.mark.parametrize("operation", ["create", "replace", "append"])
def test_windows_writes_do_not_try_to_create_the_root_or_its_ancestors(tmp_path, monkeypatch, operation):
    """Model Windows allowing traversal but denying CreateDirectoryW on existing ancestors."""
    root = r"C:\shared\repo"
    ancestors = ["C:\\", r"C:\shared", root]
    held, closed, made = [], [], []
    real_open, real_mkdir, real_replace = os.open, os.mkdir, os.replace

    def local(path):
        rel = ntpath.relpath(path, root)
        assert rel != ".." and not rel.startswith("..\\")
        return tmp_path.joinpath(*rel.split("\\"))

    def mkdir(path):
        if path in ancestors:
            raise PermissionError(13, "ancestor permits traversal, not creation", path)
        made.append(path)
        return real_mkdir(local(path))

    def opened(path, flags=None, mode=0o666):
        if flags is None:
            assert path in ancestors or local(path).is_dir()
            held.append(path)
            return path, closed.append
        assert held == ancestors + [ntpath.join(root, "nested")] and not closed
        return real_open(local(path), flags, mode)

    windows = SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
    windows.name, windows.sep, windows.path, windows.supports_dir_fd = "nt", "\\", ntpath, set()
    windows.mkdir = mkdir
    windows.replace = lambda source, target: real_replace(local(source), local(target))
    monkeypatch.setattr(storage, "os", windows)
    monkeypatch.setattr(storage, "_windows_tree_available", lambda: True)
    monkeypatch.setattr(storage, "_open_tree_nt", opened)

    if operation == "create":
        assert storage.create_in_tree(root, "nested/file", b"new") == "wrote"
    elif operation == "replace":
        storage.replace_in_tree(root, "nested/file", b"new")
    else:
        storage.append_in_tree(root, "nested/file", b"new")

    assert (tmp_path / "nested/file").read_bytes() == b"new"
    assert made == [ntpath.join(root, "nested")]
    assert closed == list(reversed(held))
