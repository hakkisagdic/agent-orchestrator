import os
import sys

import pytest

from ao import procs


def test_self_consistency_on_this_platform():
    me = os.getpid()
    assert procs.cwd(me) in (os.getcwd(), None)          # Windows' CIM exposes no cwd
    assert procs.info(me)["ppid"] == os.getppid()
    av = procs.argv(me)
    assert av and os.path.basename(av[0]).lower().startswith("python")
    assert me in procs.all_pids()
    t = procs.table()
    assert t[me][0] == os.getppid()


def test_supported_host_uses_native_backend():
    supported = sys.platform in ("darwin", "win32") or (
        sys.platform.startswith("linux") and os.path.isdir("/proc")
    )
    if not supported:
        pytest.skip("native process backend is unsupported on this platform")
    assert procs.native()


def test_argv_is_a_vector_not_split_text():
    # a path with a space survives as one argument on the native backends
    if not procs.native():
        return
    me = os.getpid()
    assert all(isinstance(a, str) for a in procs.argv(me))



def test_windows_backend_exposes_creation_date_as_process_identity():
    backend = object.__new__(procs._Windows)
    backend._cache = {
        42: {
            "ProcessId": 42,
            "ParentProcessId": 1,
            "CommandLine": r"C:\\Tools\\claude.exe -p x",
            "Name": "claude.exe",
            "SessionId": 1,
            "CreationDate": "20260906183012.123456+180",
        }
    }
    backend._cache_at = float("inf")

    assert backend.info(42)["start"] == "20260906183012.123456+180"
    backend.invalidate()
    assert backend._cache is None and backend._cache_at == 0.0
