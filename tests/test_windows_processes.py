"""What #9 asks to be proven on Windows itself; skipped with this reason everywhere else (#71)."""
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, procs

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="reads a live Windows process; the Windows lane runs it (#9)")

PROBE = ["-c", "import time; time.sleep(60)", "-p"]      # -p: an unattended turn, as its adapter declares


def _spawn(cwd):
    child = subprocess.Popen([sys.executable] + PROBE, cwd=cwd)
    deadline = time.time() + 20
    while time.time() < deadline:
        procs.refresh()
        if procs.argv(child.pid):
            return child
        time.sleep(0.5)
    child.kill()
    pytest.fail("the child never appeared in the process table")


def test_a_windows_process_s_working_directory_is_read_from_its_environment_block(tmp_path):
    child = _spawn(str(tmp_path))
    try:
        assert os.path.normcase(procs.cwd(child.pid) or "") == os.path.normcase(str(tmp_path))
        assert os.path.normcase(procs.cwd(os.getpid()) or "") == os.path.normcase(os.getcwd())
    finally:
        child.kill()


def test_an_agent_in_the_tree_is_placed_by_its_directory_and_a_hold_stops_it(project, monkeypatch, capsys):
    root = project["root"]
    adapter = {"send": {"argv": [os.path.basename(sys.executable), "-p", "{prompt}"]}, "detect": {"headless": ["-p"]}}
    monkeypatch.setattr(A, "load_adapter", lambda ident, root=None: adapter)
    # A hold stops what the shipped adapters call unattended, read for the harness a process runs as.
    monkeypatch.setattr(A, "package_adapters", lambda: {"probe": adapter})
    child = _spawn(root)
    try:
        assert child.pid in A.agent_pids(root, adapter)
        assert child.pid not in A.unplaced_agent_pids(root, adapter)

        assert cli.cmd_hold(project, SimpleNamespace(action="hold", by="the Windows lane", reason="proof", grace=5,
                                                     note=None)) == 0

        child.wait(timeout=20)
        assert A.hold_state(root) and "HELD" in capsys.readouterr().out
    finally:
        if child.poll() is None:
            child.kill()
