"""A process runs on Windows when it says so, not when a snapshot two seconds old listed it (WINDOWS-PID-ALIVE).

The Windows lane's weekly run found `ao verify` and `ao merge-check` starting their gates under a gate lock
another live process held. Liveness there was read from the CIM snapshot, which the backend keeps for two
seconds: a holder started inside them was missing from it, so its lock read as a dead run's, was cleared
and was taken. The process is now opened and waited on for no time; a fresh snapshot answers only where
that cannot.
"""
import os
import subprocess
import sys

import pytest

from ao import lib as A, procs


class _Kernel32:
    """OpenProcess, WaitForSingleObject and CloseHandle as Windows answers them for one process."""

    def __init__(self, handle=7, error=0, state=0x102):
        self.handle, self.error, self.state = handle, error, state
        self.opened, self.closed = [], []

    def OpenProcess(self, access, inherit, pid):
        self.opened.append((access, inherit, pid))
        return self.handle

    def WaitForSingleObject(self, handle, milliseconds):
        assert (handle, milliseconds) == (self.handle, 0)
        return self.state

    def CloseHandle(self, handle):
        self.closed.append(handle)


@pytest.mark.parametrize("kernel32, answer", [
    (_Kernel32(state=0x102), True),                   # the wait timed out: it runs
    (_Kernel32(state=0), False),                      # signalled: it exited, and a handle keeps its pid
    (_Kernel32(state=0xFFFFFFFF), None),              # the wait failed
    (_Kernel32(handle=0, error=87), False),           # no process has the pid
    (_Kernel32(handle=0, error=5), True),             # one this user may not open exists
    (_Kernel32(handle=0, error=6), None),
])
def test_windows_asks_the_process_itself_whether_it_runs(kernel32, answer):
    assert procs._Windows.alive(4242, kernel32, lambda: kernel32.error) is answer
    assert kernel32.opened == [(0x00100000 | 0x1000, False, 4242)]    # SYNCHRONIZE, PROCESS_QUERY_LIMITED_INFORMATION
    assert kernel32.closed == ([kernel32.handle] if kernel32.handle else [])


def test_a_lock_holder_missing_from_a_snapshot_runs_when_the_process_says_so(monkeypatch):
    """The regression: a snapshot taken before the holder started said it was gone, and its lock was cleared."""
    monkeypatch.setattr(procs, "all_pids", lambda: [])
    monkeypatch.setattr(procs, "alive", lambda pid: pid == 4242)
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        assert A._pid_alive(4242) is True and A._pid_alive(4243) is False


def test_where_windows_does_not_answer_a_fresh_snapshot_does(monkeypatch):
    listed = []
    monkeypatch.setattr(procs, "alive", lambda pid: None)
    monkeypatch.setattr(procs, "refresh", lambda: listed.append(4242))
    monkeypatch.setattr(procs, "all_pids", lambda: list(listed))
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        assert A._pid_alive(4242) is True


def test_only_windows_is_asked_this_way():
    assert procs.alive(os.getpid()) is (True if sys.platform == "win32" else None)


@pytest.mark.skipif(sys.platform != "win32", reason="asks a live Windows process; the Windows lane runs it")
def test_a_process_started_after_the_snapshot_runs_at_once_and_is_gone_once_it_ends():
    procs.all_pids()                                  # a snapshot, kept for two seconds
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert A._pid_alive(child.pid) is True
    finally:
        child.kill()
        child.wait()
    assert A._pid_alive(child.pid) is False           # this Popen still holds its handle: signalled, not running
