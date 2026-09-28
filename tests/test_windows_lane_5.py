"""A tree stopped on Windows is gone from the next reading of the process list (WINDOWS-LANE-5).

The Windows lane's run after WINDOWS-LANE-4 failed one test: `ao hold` stopped the turn it found, then
named that same turn as one it could not place, and exited 1. Since WINDOWS-PID-ALIVE a stopped process
is known gone at once, where the wait used to last until the two-second snapshot was taken again; the
snapshot taken before the stop still listed the turn, and its directory could no longer be read.
"""
import os
import signal

import pytest

from ao import lib as A, procs


@pytest.mark.parametrize("stop", [lambda: A.kill_turn(4242, signal.SIGTERM), lambda: A.sweep_orphans([4242])],
                         ids=["kill_turn", "sweep_orphans"])
def test_a_tree_stopped_on_windows_is_read_again_before_anything_reads_it(monkeypatch, stop):
    happened = []
    with monkeypatch.context() as patch:
        patch.setattr(A.subprocess, "run", lambda argv, **kw: happened.append(argv[0]))
        patch.setattr(procs, "refresh", lambda: happened.append("refresh"))
        patch.setattr(os, "name", "nt")
        stop()

    assert happened == ["taskkill", "refresh"]
