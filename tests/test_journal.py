"""The watchdog's own records survive a kill -9 (JOURNAL).

Its state was written over in place, and a torn file read as a fresh state: the markers that a park
was resumed, a report handed to a wake, a nudge counted were gone, and the next cycle could resume or
wake a second time. These kill the writer outright, as a power cut or kill -9 does, mid-work.
"""
import json
import os
import subprocess
import sys
import time

from ao import watchdog as W

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
WRITER = """
import sys
sys.path.insert(0, {src!r})
from ao import watchdog as W
W.STATE_DIR = {state_dir!r}
pad = "x" * 400000
n = 0
open({ready!r}, "w").close()
while True:
    n += 1
    W.save_state({root!r}, {{"n": n, "pad": pad}})
"""


def _killed_mid_writing(script, ready):
    process = subprocess.Popen([sys.executable, str(script)], env=os.environ.copy())
    deadline = time.time() + 30
    while not ready.exists() and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    process.kill()
    process.wait()
    ready.unlink()


def test_a_state_written_while_its_writer_is_killed_reads_whole(project, tmp_path):
    root = project["root"]
    ready = tmp_path / "ready"
    script = tmp_path / "writer.py"
    script.write_text(WRITER.format(src=SRC, state_dir=W.STATE_DIR, ready=str(ready), root=root), encoding="utf-8")

    for _ in range(4):
        _killed_mid_writing(script, ready)

        with open(W.state_path(root), encoding="utf-8") as fh:
            state = json.load(fh)
        assert state["n"] >= 1 and len(state["pad"]) == 400000
        assert W.load_state(root)["n"] == state["n"]
