"""The watchdog's own records survive a kill -9 (JOURNAL).

Its state was written over in place, and a torn file read as a fresh state: the markers that a park
was resumed, a report handed to a wake, a nudge counted were gone, and the next cycle could resume or
wake a second time. These kill the writer outright, as a power cut or kill -9 does, mid-work.
"""
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from ao import lib as A, watchdog as W
from tests.scenarios import World

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


# ---- a log a started process writes is trimmed in the file it holds (JOURNAL-3) ----------------------------------

CHILD = """
import sys, time
for n in range(int(sys.argv[1])):
    print("line %d " % n + "x" * 200, flush=True)
time.sleep(1.0)
print("the last words of a failed wake", flush=True)
"""


def test_a_log_trimmed_while_its_process_writes_keeps_what_the_process_writes_after(project, tmp_path):
    """A log replaced by a new file left its process writing into the old one, and the failure it wrote last
    never reached the log the watchdog reads a wake's failure from."""
    from ao import lib as A
    log = tmp_path / "escalate.log"
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    with open(log, "a", encoding="utf-8") as fh:
        process = subprocess.Popen([sys.executable, str(script), "7000"], stdout=fh, stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while (not log.exists() or log.stat().st_size < 1_400_000) and time.time() < deadline:
        time.sleep(0.05)

    assert A.bound_store(str(log), 1024, in_place=True)
    process.wait(timeout=30)

    text = log.read_text(encoding="utf-8")
    assert "the last words of a failed wake" in text and log.stat().st_size < 1_300_000


# ---- deferred work is closed only once it was done or a cycle ran with it (JOURNAL-4) -----------------------------

def test_a_later_end_moves_the_deferral_that_stands(project):
    root = project["root"]
    first = A.deferred_append(root, "wake", reason="architect quota", until=1000)

    again = A.deferred_append(root, "wake", reason="architect quota", until=2000)

    assert again["id"] == first["id"] and [(r["id"], r["until"]) for r in A.deferred_open(root)] == [(first["id"], 2000)]


def test_catchup_closes_deferred_work_only_after_a_cycle_ran_with_it(project, monkeypatch, capsys):
    """Catchup closed every deferral as replayed and then ran its cycle, or skipped it: a cycle that did not run
    left the deferred wake forgotten."""
    from ao import cli
    from tests.test_waiver_bounds import NAMED
    root = project["root"]
    deferred = A.deferred_append(root, "wake", reason="architect quota")
    os.makedirs(os.path.dirname(A.heartbeat_path(root)), exist_ok=True)
    with open(A.heartbeat_path(root), "w", encoding="utf-8"):
        pass
    os.utime(A.heartbeat_path(root), (time.time() - 60, time.time() - 60))
    monkeypatch.setattr(W, "run", lambda ns: 0)                         # another cycle held the project

    cli.cmd_catchup(project, NAMED)

    assert [r["id"] for r in A.deferred_open(root)] == [deferred["id"]]
    assert "stays open: no cycle ran" in capsys.readouterr().out

    def ran(ns):
        with open(A.heartbeat_path(root), "w", encoding="utf-8"):
            pass
        return 0
    monkeypatch.setattr(W, "run", ran)
    cli.cmd_catchup(project, NAMED)

    assert A.deferred_open(root) == []


def test_a_wake_started_after_a_deferral_closes_it_in_the_next_cycle(world):
    """A deferred wake stayed open after the wake it waited for, and piled up for `ao doctor` and catchup; it is
    closed a cycle later, so the notice a silence owes still names it in the cycle that woke."""
    deferred = A.deferred_append(world.root, "wake", reason="architect quota")
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1 and [r["id"] for r in A.deferred_open(world.root)] == [deferred["id"]]

    world.cycle(dry_run=False)

    assert A.deferred_open(world.root) == []


# ---- a hold stops what a cycle started as it was placed, and a park whose transcript went is told (JOURNAL-6) -----

def test_a_turn_a_cycle_starts_while_a_hold_is_placed_is_stopped_with_the_rest(project, monkeypatch):
    """`ao hold` counted the turns and then wrote the hold: a turn the watchdog started between the two read no
    hold and ran on under it."""
    from types import SimpleNamespace
    from ao import cli
    from ao.storage import _exclusive_lock
    root = project["root"]
    running, stopped = [], []
    monkeypatch.setattr(A, "agent_pids", lambda root_, adapter, headless_only=False: list(running))
    monkeypatch.setattr(A, "orphans", lambda root_, adapter, table=None: [])
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root_, adapter: [])
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: stopped.append(pid))
    monkeypatch.setattr(cli, "_alive", lambda pid: False)
    lock = os.path.join(W.STATE_DIR, W.CYCLE_LOCK.format(key=A.project_key(root)))
    holding = threading.Event()

    def cycle():                              # a cycle past its reading of the hold, about to start a turn
        with _exclusive_lock(lock, timeout=5):
            holding.set()
            time.sleep(0.5)
            running.append(4242)
    thread = threading.Thread(target=cycle)
    thread.start()
    holding.wait(5)

    assert cli.cmd_hold(project, SimpleNamespace(action="hold", by="a person", reason="test", grace=1, note=None)) == 0
    thread.join()

    assert stopped == [4242] and A.hold_state(root)["stopped"] == [4242]


def test_a_park_whose_transcript_is_gone_is_told_and_its_alarm_stands(world):
    """The cycle ended on a missing transcript before the park's alarm, and the parked slice waited on unseen."""
    st = W.load_state(world.root)
    st["quota_park"] = {"items": ["B8"], "at": time.time() - 600, "text": "hit your session limit",
                        "until": time.time() + 3600, "named": True, "source": "transcript", "since": time.time() - 600}
    W.save_state(world.root, st)
    os.remove(world.transcript)

    world.cycle(dry_run=False)

    assert any("a parked slice cannot resume" in title for title, *_ in world.notices)


# ---- what the watchdog starts is claimed before it starts (JOURNAL-2) ----------------------------------------------

BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"


def test_a_step_is_claimed_once_while_its_lease_holds_and_again_once_it_has_passed(tmp_path):
    from ao import journal as J
    path = str(tmp_path / "journal.db")

    assert J.claim(path, "wake:s1:abc", "architect", 900, now=1000.0)
    assert not J.claim(path, "wake:s1:abc", "architect", 900, now=1500.0)
    assert J.under_way(path, "architect", now=1500.0) == [("wake:s1:abc", None, None)]
    J.started(path, "wake:s1:abc", 4242, 999.5)
    assert J.under_way(path, "architect", now=1500.0) == [("wake:s1:abc", 4242, 999.5)]
    assert J.claim(path, "wake:s1:abc", "architect", 900, now=2000.0)              # the lease has passed
    J.finished(path, "wake:s1:abc")
    assert J.under_way(path, "architect", now=2001.0) == []


def test_of_many_claims_at_once_one_holds(tmp_path):
    from ao import journal as J
    path = str(tmp_path / "journal.db")
    held, start = [], threading.Barrier(8)

    def claim():
        start.wait()
        held.append(J.claim(path, "refill:s1:1", "architect", 900))
    threads = [threading.Thread(target=claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(held) == [False] * 7 + [True]


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _agent_turns(world):
    """What the cycle started of the agents' own programs, which the scenario resolves under /agents/."""
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).startswith("/agents/")]


def _architect_wakes(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and argv[0].endswith("/claude")]


def test_a_wake_cut_off_after_its_process_started_is_not_started_again(world, monkeypatch):
    """The wake's process started, and the cycle was killed before it registered it: the next cycle, seeing no
    architect it knew of, woke a second one into the same session."""
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    register = A.helper_register

    def killed(root, pid, what):
        raise KeyboardInterrupt
    monkeypatch.setattr(A, "helper_register", killed)
    with pytest.raises(KeyboardInterrupt):
        world.cycle(dry_run=False)
    (first,) = _architect_wakes(world)
    world.process(99999, list(first))                    # the architect it started runs on, recorded nowhere
    monkeypatch.setattr(A, "helper_register", register)
    world.spawned.clear()

    world.cycle(dry_run=False)

    assert _architect_wakes(world) == []


def test_a_wake_cut_off_before_its_start_was_written_holds_its_claim_for_its_lease(world, monkeypatch):
    from ao import journal as J
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    started = J.started

    def killed(path, key, pid, start):
        raise KeyboardInterrupt
    monkeypatch.setattr(J, "started", killed)
    with pytest.raises(KeyboardInterrupt):
        world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1
    monkeypatch.setattr(J, "started", started)
    world.spawned.clear()

    world.cycle(dry_run=False)

    assert _architect_wakes(world) == []
    later = time.time() + W.WAKE_LEASE + 60
    monkeypatch.setattr(time, "time", lambda: later)
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1             # nothing ran it, and the lease has passed


def test_a_resume_cut_off_after_its_process_started_is_not_resumed_twice(world, monkeypatch):
    """Exactly one resume after a kill -9 during the resume: the implementer's turn is found in its tree."""
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)

    class Running:
        pid, returncode = 99999, None

        def poll(self):
            return None
    monkeypatch.setattr(W.subprocess, "Popen", lambda argv, **kw: world.spawned.append(argv) or Running())
    monkeypatch.setattr(time, "sleep", lambda seconds: None)              # the nudge's twelve seconds to fail
    measure = A._process_start

    def killed(pid, refresh=False):
        raise KeyboardInterrupt
    monkeypatch.setattr(A, "_process_start", killed)
    with pytest.raises(KeyboardInterrupt):
        world.cycle(dry_run=False)
    (first,) = _agent_turns(world)
    world.process(99999, list(first), cwd=world.root)
    monkeypatch.setattr(A, "_process_start", measure)
    world.spawned.clear()

    world.cycle(dry_run=False)

    assert _agent_turns(world) == []
