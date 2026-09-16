import os
import subprocess
import sys
import time

import pytest

from ao import cli, procs

pytestmark = pytest.mark.skipif(os.name == "nt", reason="a process group's CPU is not readable on Windows")


def _spawn(code):
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)


def test_a_silent_reviewer_is_killed_as_stalled_and_a_thinking_one_is_left_to_finish(monkeypatch):
    monkeypatch.setattr(cli, "REVIEW_HEARTBEAT_SECONDS", 0.2)
    quiet = _spawn("import time; print('partial answer', flush=True); time.sleep(60)")
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired) as raised:
            cli._reviewer_communicate(quiet, 120, "quiet", started, stall=1.0)
    finally:
        quiet.kill()
        quiet.communicate()
    assert raised.value.stalled >= 1.0 and time.monotonic() - started < 20

    busy = _spawn("import time\nend = time.time() + 2.5\nwhile time.time() < end:\n    pass\nprint('verdict')")
    out, _ = cli._reviewer_communicate(busy, 120, "busy", time.monotonic(), stall=1.0)
    assert out.strip() == "verdict"


def test_the_cpu_of_a_process_group_is_read_as_it_is_spent():
    busy = _spawn("import time\nend = time.time() + 1.5\nwhile time.time() < end:\n    pass")
    try:
        first = procs.group_cpu_seconds(busy.pid)
        time.sleep(1.0)
        second = procs.group_cpu_seconds(busy.pid)
    finally:
        busy.wait()
    assert first is not None and second is not None and second > first
    assert procs.group_cpu_seconds(2 ** 22 - 3) is None
