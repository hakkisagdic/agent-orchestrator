"""A lock ao keeps is a file an agent can replace, and a link put in its place was followed.

`_exclusive_lock` opened its file "a+b", which follows a final link and creates the file it names, and wrote a byte
into a file it found empty: a link put where a lock is kept - `.ao/` in the working tree holds several, the reviews'
submission lock among them - had ao create a file wherever the link pointed, outside the tree an agent may write,
and write into it. A lock is now opened through no final link, and only as a regular file; anything else is refused
and named (LOCK-LINKS).
"""
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from ao import storage


def _link(target, at):
    try:
        os.symlink(target, at)
    except (OSError, NotImplementedError) as exc:          # Windows without the symlink privilege
        pytest.skip(f"no symbolic link can be made here: {exc}")


def _take(path):
    with storage._exclusive_lock(str(path), timeout=0):
        pass


def test_a_link_where_a_lock_is_kept_is_refused_and_nothing_is_made_where_it_points(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    lock = tmp_path / "ledger.jsonl.lock"
    _link(str(outside / "made"), str(lock))

    with pytest.raises(storage.LockThroughLink) as refused:
        _take(lock)

    assert not (outside / "made").exists()
    assert str(lock) in str(refused.value) and "remove it" in str(refused.value)


def test_a_link_to_an_empty_file_is_refused_and_the_file_stays_empty(tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"")
    lock = tmp_path / "submitted.lock"
    _link(str(victim), str(lock))

    with pytest.raises(storage.LockThroughLink):
        _take(lock)

    assert victim.read_bytes() == b""


@pytest.mark.skipif(os.name == "nt", reason="Windows opens a lock as the link itself, with no O_NOFOLLOW to miss")
def test_a_posix_platform_without_o_nofollow_takes_no_lock(tmp_path, monkeypatch):
    lock = tmp_path / "decisions.lock"
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    with pytest.raises(storage.LockThroughLink) as refused:
        _take(lock)

    assert not lock.exists() and "O_NOFOLLOW" in str(refused.value)


def test_a_file_with_another_name_where_a_lock_is_kept_is_refused_and_stays_empty(tmp_path):
    """A hard link put in the lock's place names a file outside: no O_NOFOLLOW stops it, its count of names does."""
    victim = tmp_path / "victim"
    victim.write_bytes(b"")
    lock = tmp_path / "hunter.lock"
    try:
        os.link(str(victim), str(lock))
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"no hard link can be made here: {exc}")

    with pytest.raises(storage.LockThroughLink):
        _take(lock)

    assert victim.read_bytes() == b""


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot unlink a file held open")
def test_a_lock_file_that_lost_its_name_as_it_was_opened_is_refused(tmp_path, monkeypatch):
    """A file with no name left is no lock another writer of ao can find: every count of names but one is refused."""
    lock = tmp_path / "ledger.jsonl.lock"
    real_open = os.open

    def unnamed(name, flags, mode=0o777, **kw):
        fd = real_open(name, flags, mode, **kw)
        if os.fspath(name) == str(lock):
            os.unlink(name)
        return fd
    monkeypatch.setattr(os, "open", unnamed)

    with pytest.raises(storage.LockThroughLink):
        _take(lock)


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot unlink a file held open")
def test_a_lock_whose_file_lost_its_name_once_opened_is_taken_again_on_the_file_the_name_holds(tmp_path, monkeypatch):
    """Two writers each held a file of the one name: the first's file had lost the name before its lock was taken."""
    lock = tmp_path / "ledger.jsonl.lock"
    real, calls = storage._open_lock, []

    def opened_then_unnamed(path):
        handle = real(path)
        calls.append(path)
        if len(calls) == 1:
            os.unlink(path)
        return handle
    monkeypatch.setattr(storage, "_open_lock", opened_then_unnamed)

    with storage._exclusive_lock(str(lock), timeout=5):
        monkeypatch.setattr(storage, "_open_lock", real)
        with pytest.raises(storage.LedgerLockTimeout):
            _take(lock)                          # the file the name holds is the one locked

    assert len(calls) == 2


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a FIFO is POSIX's")
def test_a_fifo_where_a_lock_is_kept_is_refused_at_once(tmp_path):
    lock = tmp_path / "events.jsonl.lock"
    os.mkfifo(str(lock))
    raised = []

    def take():
        try:
            _take(lock)
        except storage.LockThroughLink as exc:
            raised.append(exc)
    worker = threading.Thread(target=take, daemon=True)
    worker.start()
    worker.join(10)

    assert not worker.is_alive(), "opening the FIFO waited for a writer"
    assert raised


def test_a_ledger_append_through_a_link_left_at_its_lock_writes_nothing(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    ledger = tmp_path / "ledger.jsonl"
    _link(str(outside / "made"), str(ledger) + ".lock")

    with pytest.raises(storage.LockThroughLink):
        storage.append_jsonl(str(ledger), {"kind": "row"})

    assert not ledger.exists() and not (outside / "made").exists()


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="the platform's own way is the one taken")
def test_a_test_that_names_the_platform_windows_takes_its_lock_the_way_this_platform_offers(tmp_path, monkeypatch):
    """A test of Windows' behaviour sets `os.name` and a stand-in msvcrt on another platform: its lock went the
    Windows way, to a kernel32 that platform has not, and the hold the test ran failed (LOCK-LINKS-2)."""
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=lambda *args: None))
    lock = tmp_path / "ledger.jsonl.lock"

    _take(lock)

    assert lock.read_bytes() == b"\0"


def test_a_lock_that_is_a_regular_file_still_locks_and_holds_its_byte(tmp_path):
    lock = tmp_path / "ledger.jsonl.lock"

    _take(lock)
    _take(lock)

    assert lock.read_bytes() == b"\0"
