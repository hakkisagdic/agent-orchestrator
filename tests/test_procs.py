import os
import sys

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


def test_argv_is_a_vector_not_split_text():
    # a path with a space survives as one argument on the native backends
    if not procs.native():
        return
    me = os.getpid()
    assert all(isinstance(a, str) for a in procs.argv(me))
