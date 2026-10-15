"""Round 3: a held tree, an exclusive first config, kept modes and shared rules (LEADS-ADAPTERS)."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from ao import cli, lib as A, skillkit, storage
from tests.test_leads_adapters import _can_link, _fresh_repository
from tests.test_profiles import _init_args


@pytest.mark.parametrize("operation", ["create", "replace", "read"])
def test_no_safe_tree_backend_refuses(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    monkeypatch.setattr(storage, "_windows_tree_available", lambda: False, raising=False)
    with pytest.raises(storage.InTheWay, match="cannot hold"):
        if operation == "create":
            storage.create_in_tree(str(tmp_path), "new/file", b"x")
        elif operation == "replace":
            storage.replace_in_tree(str(tmp_path), "new/file", b"x")
        else:
            storage.read_in_tree(str(tmp_path), "new/file")
    assert list(tmp_path.iterdir()) == []


def test_tree_choice_uses_capabilities_not_os_name(tmp_path, monkeypatch):
    if not storage.folders_held_open():
        pytest.skip("this test exercises the dir_fd backend")
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        patch.setattr(storage, "_windows_tree_available", lambda: False, raising=False)
        assert storage.create_in_tree(str(tmp_path), "file", b"x") == "wrote"
        assert storage.read_in_tree(str(tmp_path), "file") == b"x"


def test_tree_choice_requires_nofollow(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    monkeypatch.setattr(storage, "_windows_tree_available", lambda: False, raising=False)
    with pytest.raises(storage.InTheWay, match="cannot hold"):
        storage.create_in_tree(str(tmp_path), "file", b"x")


def test_first_config_appearing_at_create_is_kept(tmp_path, monkeypatch, capsys):
    root = _fresh_repository(tmp_path, monkeypatch)
    real_create, real_replace = storage.create_in_tree, storage.replace_in_tree
    theirs = b'{"project": "concurrent"}\n'
    appeared = []

    def race(operation, where, rel, data, **kwargs):
        if rel == ".ao/config.json" and not appeared:
            appeared.append(True)
            (root / ".ao").mkdir(exist_ok=True)
            (root / rel).write_bytes(theirs)
        return operation(where, rel, data, **kwargs)

    monkeypatch.setattr(storage, "create_in_tree", lambda *a, **kw: race(real_create, *a, **kw))
    monkeypatch.setattr(storage, "replace_in_tree", lambda *a, **kw: race(real_replace, *a, **kw))
    code = cli.cmd_init({"root": str(root)}, _init_args(profile=None))
    assert appeared and (root / ".ao/config.json").read_bytes() == theirs
    assert code == 1 and "appeared while init ran" in capsys.readouterr().out
    assert not (root / ".ao/board.md").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps no POSIX mode")
def test_replacement_keeps_mode_despite_umask(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_bytes(b"old")
    path.chmod(0o664)
    previous = os.umask(0o022)
    try:
        storage.replace_in_tree(str(tmp_path), "AGENTS.md", b"new")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == 0o664


def test_opt_in_folder_swapped_after_preflight_writes_nothing_outside(tmp_path, monkeypatch):
    _can_link(tmp_path)
    root = _fresh_repository(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    real = A.record_opt_in
    def swapped(where, *args):
        ledger = root / ".ao/ledger"
        if ledger.exists():
            ledger.rename(root / ".ao/ledger-moved")
        ledger.symlink_to(outside, target_is_directory=True)
        return real(where, *args)
    monkeypatch.setattr(A, "record_opt_in", swapped)
    code = cli.cmd_init({"root": str(root)}, _init_args(profile=None, review_tier="same-family", by="Person"))
    assert list(outside.iterdir()) == []
    assert code == 1


@pytest.mark.parametrize("leaf", ["opt-ins.jsonl", "opt-ins.jsonl.lock"])
def test_opt_in_refuses_leaf_link(project, tmp_path, leaf):
    _can_link(tmp_path)
    root = Path(project["root"])
    directory = root / ".ao/ledger"
    directory.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    (directory / leaf).symlink_to(outside)
    with pytest.raises(OSError):
        A.record_opt_in(str(root), "review.same_family", "labeled", "Person")
    assert not outside.exists()


def test_opt_in_chain_and_partial_tail(project):
    root = project["root"]
    first = A.record_opt_in(root, "review.same_family", "labeled", "Person")
    with open(A.opt_ins_path(root), "ab") as handle:
        handle.write(b'{"unfinished":')
    second = A.record_opt_in(root, "review.same_family", None, "Person")
    assert second["previous"] == storage.chained_row_digest(first, A.OPT_IN_CHAIN)
    assert second["ordinal"] == 2
    assert A.recorded_opt_in(root, "review.same_family") == second


def test_known_mcp_format_never_runs_vendor(project, monkeypatch):
    calls = []
    monkeypatch.setattr(A, "program_path", lambda *a, **kw: "/standin/vendor")
    monkeypatch.setattr(skillkit.subprocess, "run",
                        lambda *a, **kw: calls.append(a) or type("Result", (), {"returncode": 0})())
    assert skillkit.register_mcp(project["root"], {"kiro"}, exe="/x/ao") == {"kiro": "registered"}
    assert calls == []
    path = Path(project["root"]) / ".kiro/settings/mcp.json"
    assert json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["ao"]["command"] == "/x/ao"


def test_unknown_mcp_format_refuses_and_names_command(tmp_path, monkeypatch, capsys):
    root = _fresh_repository(tmp_path, monkeypatch)
    adapter = dict(A.load_adapter("kiro"))
    adapter["mcp"] = dict(adapter["mcp"], file=".kiro/settings/mcp.unknown", format="unknown")
    monkeypatch.setattr(skillkit, "setup_adapters", lambda *a, **kw: [("kiro", adapter)])
    monkeypatch.setattr(A, "program_path", lambda *a, **kw: "/standin/kiro-cli")
    monkeypatch.setattr(A, "ao_program", lambda: "/x/ao")
    calls = []
    original = subprocess.run
    def run(argv, *args, **kwargs):
        if argv[0] == "/standin/kiro-cli":
            calls.append(argv)
            return type("Result", (), {"returncode": 0})()
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    code = cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="kiro", no_mcp=False))
    assert calls == [] and code == 1
    out = capsys.readouterr().out
    assert "kiro-cli mcp add" in out and "cannot" in out
    assert not (root / ".kiro/settings/mcp.unknown").exists()


@pytest.mark.parametrize("target", ["AGENTS.md", "unwritten.md", "../outside.md"])
def test_rules_link_only_allowed_to_another_written_rule(tmp_path, monkeypatch, capsys, target):
    _can_link(tmp_path)
    root = _fresh_repository(tmp_path, monkeypatch)
    destination = root / target
    destination.write_text("Person's rules\n", encoding="utf-8")
    (root / "CLAUDE.md").symlink_to(target)
    code = cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="claude-code", rules=True))
    out = capsys.readouterr().out
    if target == "AGENTS.md":
        assert code == 0 and "CLAUDE.md" in out and "AGENTS.md" in out and "link" in out
        assert destination.read_text(encoding="utf-8").count(skillkit.MARK_START) == 1
    else:
        assert code == 1 and "not written" in out
        assert destination.read_text(encoding="utf-8") == "Person's rules\n"
    assert (root / "CLAUDE.md").is_symlink()


@pytest.mark.skipif(os.name != "nt", reason="native Windows handles and junctions")
@pytest.mark.parametrize("kind", ["junction", "link", "leaf"])
@pytest.mark.parametrize("operation", ["create", "replace", "read"])
def test_windows_tree_refuses_links(tmp_path, kind, operation):
    root, outside = tmp_path / "repo", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "file").write_bytes(b"person")
    if kind == "junction":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "folder"), str(outside)], check=True,
                       capture_output=True)
    else:
        _can_link(tmp_path)
        if kind == "link":
            (root / "folder").symlink_to(outside, target_is_directory=True)
        else:
            (root / "folder").mkdir()
            (root / "folder/file").symlink_to(outside / "file")
    try:
        if kind == "leaf" and operation == "create":
            assert storage.create_in_tree(str(root), "folder/file", b"new") == "kept"
        else:
            with pytest.raises(storage.InTheWay):
                if operation == "create":
                    storage.create_in_tree(str(root), "folder/new", b"new")
                elif operation == "replace":
                    storage.replace_in_tree(str(root), "folder/file", b"new")
                else:
                    storage.read_in_tree(str(root), "folder/file")
        assert sorted(p.name for p in outside.iterdir()) == ["file"]
        assert (outside / "file").read_bytes() == b"person"
    finally:
        if kind == "junction":
            os.rmdir(str(root / "folder"))


@pytest.mark.skipif(os.name != "nt", reason="native Windows share modes")
def test_windows_folders_cannot_be_renamed_during_write(tmp_path):
    (tmp_path / "folder/inner").mkdir(parents=True)
    attempts = []
    def checkpoint(step):
        if step == "temporary-opened":
            for path in (tmp_path / "folder", tmp_path / "folder/inner"):
                with pytest.raises(OSError):
                    path.rename(path.with_name(path.name + "-moved"))
                attempts.append(path)
    storage.replace_in_tree(str(tmp_path), "folder/inner/file", b"new", _checkpoint=checkpoint)
    assert len(attempts) == 2
    (tmp_path / "folder").rename(tmp_path / "released")


@pytest.mark.parametrize("operation", ["create", "replace", "read"])
def test_windows_api_contract_and_lifetime_on_every_platform(tmp_path, monkeypatch, operation):
    """A stand-in kernel checks flags and handle lifetime; native tests check the kernel's enforcement."""
    import ctypes
    import sys
    from types import SimpleNamespace

    opened, closed, live, calls = [], [], {}, []
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder/file").write_bytes(b"old")

    class Function:
        def __init__(self, call):
            self.call = call
        def __call__(self, *args):
            return self.call(*args)

    def create(path, access, share, security, disposition, flags, template):
        assert share == 3 and flags & 0x00200000 and flags & 0x02000000
        directory = not access & 0xC0000000
        # Windows records the share mode only of a handle that asks for data access: a folder held with none could
        # be renamed under it.
        assert not directory or access & 0x0001, "a folder is held to list it, or its share mode holds nothing"
        calls.append((directory, disposition))
        fd = 100000 + len(opened) if directory else os.open(
            path, os.O_RDONLY if disposition == 3 else os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        live[fd] = directory
        opened.append(fd)
        return fd

    def info(fd, output):
        output._obj.attributes = 0x10 if live[fd] else 0
        return True

    def close(fd):
        closed.append(fd)
        del live[fd]
        return True

    def convert(fd, flags):
        # Every ancestor, root and child folder stays pinned until the leaf is handed to Python.
        assert sum(live.values()) >= 2
        del live[fd]
        return fd

    kernel = SimpleNamespace(CreateFileW=Function(create), CloseHandle=Function(close),
                             GetFileInformationByHandle=Function(info))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=convert))
    monkeypatch.setattr(os, "supports_dir_fd", set())
    if operation == "create":
        storage.create_in_tree(str(tmp_path), "folder/new", b"new")
    elif operation == "replace":
        storage.replace_in_tree(str(tmp_path), "folder/file", b"new")
    else:
        assert storage.read_in_tree(str(tmp_path), "folder/file") == b"old"
    assert live == {} and closed
    assert all(disposition == 3 for directory, disposition in calls if directory)
    assert (False, 1 if operation != "read" else 3) in calls


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="POSIX held descriptor race")
@pytest.mark.parametrize("when", ["lock", "ledger"])
def test_opt_in_swap_after_folder_held_stays_in_held_folder(project, tmp_path, monkeypatch, when):
    _can_link(tmp_path)
    root = Path(project["root"])
    ledger = root / ".ao/ledger"
    ledger.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    original, swapped = os.open, []
    trigger = "opt-ins.jsonl.lock" if when == "lock" else "opt-ins.jsonl"

    def opened(path, flags, *args, **kwargs):
        fd = original(path, flags, *args, **kwargs)
        if not swapped and os.path.basename(path) == trigger:
            ledger.rename(root / ".ao/ledger-moved")
            ledger.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
        return fd

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {opened})
    A.record_opt_in(str(root), "review.same_family", "labeled", "Person")
    assert swapped and list(outside.iterdir()) == []
    row = json.loads((root / ".ao/ledger-moved/opt-ins.jsonl").read_text(encoding="utf-8"))
    assert row["ordinal"] == 1


def test_opt_in_sealed_chain_is_preserved(project):
    root = project["root"]
    for value in (True, False, True):
        A.record_opt_in(root, "features.probe", value, "Person")
    storage.seal_chained_jsonl(A.opt_ins_path(root), A.OPT_IN_CHAIN, keep=1)
    last = A.recorded_opt_in(root, "features.probe")
    row = A.record_opt_in(root, "features.probe", False, "Person")
    assert row["ordinal"] == 4
    assert row["previous"] == storage.chained_row_digest(last, A.OPT_IN_CHAIN)


def test_opt_in_checkpoint_failure_rolls_back(project, monkeypatch):
    root = project["root"]
    first = A.record_opt_in(root, "features.probe", True, "Person")
    def failed(*args, **kwargs):
        raise OSError("checkpoint unavailable")
    monkeypatch.setattr(storage, "_record_committed_length", failed)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        A.record_opt_in(root, "features.probe", False, "Person")
    assert A.recorded_opt_in(root, "features.probe") == first


def test_rule_target_swapped_for_outside_link_is_refused(tmp_path, monkeypatch):
    _can_link(tmp_path)
    root = _fresh_repository(tmp_path, monkeypatch)
    (root / "AGENTS.md").write_bytes(b"own")
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    outside = tmp_path / "outside"
    outside.write_bytes(b"person")
    original = skillkit._write_marked
    def swapped(where, rel, *args, **kwargs):
        if rel == "AGENTS.md":
            (root / rel).unlink()
            (root / rel).symlink_to(outside)
        return original(where, rel, *args, **kwargs)
    monkeypatch.setattr(skillkit, "_write_marked", swapped)
    assert cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="claude-code", rules=True)) == 1
    assert outside.read_bytes() == b"person"


def test_failed_first_opt_in_removes_new_ledger(project, monkeypatch):
    def failed(*args, **kwargs):
        raise OSError("checkpoint unavailable")
    monkeypatch.setattr(storage, "_record_committed_length", failed)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        A.record_opt_in(project["root"], "features.probe", True, "Person")
    assert not os.path.exists(A.opt_ins_path(project["root"]))


@pytest.mark.parametrize("rewrite_done", [False, True])
def test_opt_in_recovers_pending_seal(project, monkeypatch, rewrite_done):
    root = project["root"]
    for value in (True, False, True):
        A.record_opt_in(root, "features.probe", value, "Person")
    path = A.opt_ins_path(root)
    replace = storage.replace_file_durably
    def interrupted(where, *args, **kwargs):
        if where == path:
            if rewrite_done:
                replace(where, *args, **kwargs)
            raise OSError("interrupted seal")
        return replace(where, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "replace_file_durably", interrupted)
        with pytest.raises(OSError, match="interrupted seal"):
            storage.seal_chained_jsonl(path, A.OPT_IN_CHAIN, keep=1)
    assert A.recorded_opt_in(root, "features.probe")["ordinal"] == 3
    assert not os.path.exists(storage.seal_path(path) + ".pending")
    assert A.record_opt_in(root, "features.probe", False, "Person")["ordinal"] == 4
