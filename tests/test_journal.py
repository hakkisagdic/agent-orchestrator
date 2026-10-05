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


@pytest.mark.skipif(os.name == "nt", reason="a child's handle does not append on Windows (CPython gh-86772); the "
                                            "watchdog leaves the log of a process it started whole until it ends")
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


def test_what_a_process_appends_while_the_tail_is_read_is_kept_once(tmp_path, monkeypatch):
    """JOURNAL-3-2: the catch-up read again from the old end what the first read had already taken."""
    log = tmp_path / "nudge.log"
    log.write_bytes(b"".join(b"line %06d %s\n" % (n, b"x" * 200) for n in range(7000)))
    fd = os.open(log, os.O_WRONLY | os.O_APPEND)
    real = open

    class Reading:                          # the trimmer's file: a process appends as the first read starts
        def __init__(self, fh):
            self.fh, self.reads = fh, 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self.fh.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self.fh, name)

        def read(self, *a):
            self.reads += 1
            if self.reads == 1:
                os.write(fd, b"written while the tail was read\n")
            return self.fh.read(*a)

    monkeypatch.setattr(A, "open", lambda *a, **k: Reading(real(*a, **k)), raising=False)
    try:
        assert A.bound_store(str(log), 1024, in_place=True)
    finally:
        os.close(fd)

    assert log.read_bytes().count(b"written while the tail was read\n") == 1


WAITING_CHILD = """
import sys
print("a turn the watchdog started", flush=True)
sys.stdin.readline()
print("API Error: 529 overloaded", flush=True)
"""


def test_a_log_a_started_process_still_writes_is_left_whole_until_it_ends(project, tmp_path):
    """JOURNAL-3-2: on Windows a child's handle does not append (CPython gh-86772), so a log trimmed under it
    grew a gap of NULs and kept no bound; on any system a line written as the file was cut could be lost."""
    root = project["root"]
    os.makedirs(W.STATE_DIR, exist_ok=True)
    log = os.path.join(W.STATE_DIR, A.project_file_name("nudge-log", A.project_key(root)))
    with open(log, "wb") as fh:
        fh.write(b"".join(b"line %06d %s\n" % (n, b"x" * 200) for n in range(14000)))     # past 1.25 x 2 MB
    script = tmp_path / "turn.py"
    script.write_text(WAITING_CHILD, encoding="utf-8")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("=== a nudge ===\n")
        fh.flush()
        process = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=fh,
                                   stderr=subprocess.STDOUT, text=True)
    st = W.load_state(root)
    st.update(child_pid=process.pid, child_start=A._process_start(process.pid, refresh=True))
    W.save_state(root, st)
    size = os.path.getsize(log)

    A.bound_observation_logs(root, W.STATE_DIR, busy=W._logs_written(root))

    assert os.path.getsize(log) >= size                     # left whole while its turn runs
    process.communicate("\n", timeout=30)
    A.bound_observation_logs(root, W.STATE_DIR, busy=W._logs_written(root))

    data = open(log, "rb").read()
    assert len(data) < 2 * 1024 * 1024 and b"\0" not in data
    assert b"API Error: 529 overloaded" in data[-20000:]


def test_a_later_end_moves_the_deferral_that_stands(project):
    root = project["root"]
    first = A.deferred_append(root, "wake", reason="architect quota", until=1000)

    again = A.deferred_append(root, "wake", reason="architect quota", until=2000)

    assert again["id"] == first["id"] and [(r["id"], r["until"]) for r in A.deferred_open(root)] == [(first["id"], 2000)]


def test_catchup_closes_deferred_work_once_its_work_was_done(project, monkeypatch, capsys):
    """Catchup closed every deferral as replayed and then ran its cycle, or skipped it: a cycle that did not run
    left the deferred wake forgotten. Then it closed every row once its cycle ran, though that cycle could not do
    the work either and had just deferred it again (JOURNAL-4-2): a row closes on the work it waited for."""
    from ao import cli
    from tests.test_waiver_bounds import NAMED
    root = project["root"]
    deferred = A.deferred_append(root, "wake", reason="architect quota")
    _old_heartbeat(root)
    monkeypatch.setattr(W, "run", lambda ns: 0)                         # another cycle held the project
    cli.cmd_catchup(project, NAMED)
    assert [r["id"] for r in A.deferred_open(root)] == [deferred["id"]]
    assert "stays open: no cycle ran" in capsys.readouterr().out

    def ran_still_blocked(ns):
        ns.ran = True
        return 0
    monkeypatch.setattr(W, "run", ran_still_blocked)
    cli.cmd_catchup(project, NAMED)
    assert [r["id"] for r in A.deferred_open(root)] == [deferred["id"]]
    assert "stays open: its cycle could not do it yet" in capsys.readouterr().out

    def woke(ns):
        st = W.load_state(root)
        st["last_arch_wake"] = time.time()
        W.save_state(root, st)
        ns.ran = True
        return 0
    monkeypatch.setattr(W, "run", woke)
    cli.cmd_catchup(project, NAMED)
    assert A.deferred_open(root) == [] and _closed(root) == [(deferred["id"], "woken")]


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
    assert any(title.endswith("B8 parked on quota") for title, *_ in world.notices)     # the park's own alarm


def test_a_hold_whose_wait_on_a_running_cycle_times_out_stops_what_it_counted_and_says_so(project, monkeypatch,
                                                                                         capsys):
    """JOURNAL-6-2: after a minute's wait on a cycle that began before the hold, `ao hold` counted the turns as they
    stood, said HELD and exited 0; a turn that cycle started after the count ran on and nobody was told."""
    from types import SimpleNamespace
    from ao import cli, storage
    root = project["root"]
    running, stopped = [7001], []
    monkeypatch.setattr(A, "agent_pids", lambda root_, adapter, headless_only=False: list(running))
    monkeypatch.setattr(A, "orphans", lambda root_, adapter, table=None: [])
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root_, adapter: [])
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: stopped.append(pid))
    monkeypatch.setattr(cli, "_alive", lambda pid: False)
    real = storage._exclusive_lock
    monkeypatch.setattr(storage, "_exclusive_lock", lambda path, timeout=10.0:     # the minute, made half a second
                        real(path, min(timeout, 0.5) if str(path).endswith(".cycle.lock") else timeout))
    lock = os.path.join(W.STATE_DIR, W.CYCLE_LOCK.format(key=A.project_key(root)))
    holding, release = threading.Event(), threading.Event()

    def cycle():
        with real(lock, timeout=5):
            holding.set()
            release.wait(10)
    thread = threading.Thread(target=cycle)
    thread.start()
    holding.wait(5)

    code = cli.cmd_hold(project, SimpleNamespace(action="hold", by="a person", reason="test", grace=1, note=None))
    release.set()
    thread.join()

    out = capsys.readouterr().out
    assert code == 1
    assert "still running after a minute" in out and "`ao hold` again stops it" in out
    assert stopped == [7001] and A.hold_state(root)["stopped"] == [7001]


def test_a_report_wake_reads_a_hold_placed_while_its_window_rotated(world, monkeypatch):
    """JOURNAL-6-2: the report wake read the hold before it rotated the architect's window, which can wait minutes
    on keyflip, and woke the architect under a hold placed meanwhile."""
    from ao.storage import replace_file_durably
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)

    def rotation(cfg, argv, who, on_wait=None):
        if who == "architect":
            hold = {"by": "a person", "reason": "test", "at": int(time.time()), "stopped": []}
            replace_file_durably(os.path.join(world.root, A.HOLD_FILE), json.dumps(hold).encode("utf-8"))
        return {"ok": True, "provider": None, "rotated": False, "text": "headroom"}
    monkeypatch.setattr(A, "rotate_if_exhausted", rotation)

    trace = world.cycle(dry_run=False)

    assert _architect_wakes(world) == []
    assert "held by a person since this cycle began; not waking the architect" in trace


# ---- shared records changed one writer at a time (JOURNAL-7) ------------------------------------------------------

def test_two_writers_of_the_alarm_episodes_lose_neither_ones_raises(project):
    """Every project's watchdog shares ~/.ao/alarms.json, and each read it, changed it and wrote it back."""
    def raise_often(key):
        for _ in range(40):
            A.alarm_touch("proj", key, "orange")
    threads = [threading.Thread(target=raise_often, args=(key,)) for key in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    episodes = A.load_alarms()
    assert episodes["proj:one"]["count"] == 40 and episodes["proj:two"]["count"] == 40


def test_a_sections_journal_one_review_holds_is_not_asked_by_a_second(project, tmp_path):
    from ao import cli
    journal = str(tmp_path / "sections" / "abc.jsonl")
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    holder = subprocess.Popen([sys.executable, "-c", (
        f"import sys, time; sys.path.insert(0, {src!r}); from ao import cli\n"
        f"lease = cli._section_lease({journal!r}); print('held', flush=True); time.sleep(60)")],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert cli._section_lease(journal) is None
    finally:
        holder.kill()
        holder.wait()

    lease = cli._section_lease(journal)                     # its holder ended: the lease holds nothing
    assert lease is not None
    lease.close()


def test_a_review_finds_its_sections_journal_held_and_asks_nothing(project, tmp_path, monkeypatch):
    from tests.test_review_sections import SCENARIOS, _args, _setup
    from ao import cli
    root, cfg, calls = _setup(project, tmp_path, monkeypatch, "- [S1] the claim journal")
    monkeypatch.setattr(cli, "_section_lease", lambda journal: None)

    assert cli.cmd_review(cfg, _args(SCENARIOS)) == 3

    assert not calls.exists() or calls.read_text() == ""


def test_an_alarm_change_is_not_written_blind_when_the_store_is_busy(project, monkeypatch):
    """JOURNAL-7-2: past ten seconds without the store's lock a change was written without it, and the stalled
    holder's copy then overwrote it; a person's unsnooze lost that way silenced an alarm until its date."""
    from ao import storage
    real = storage._exclusive_lock
    monkeypatch.setattr(storage, "_exclusive_lock",                       # the ten seconds, made short
                        lambda path, timeout=10.0: real(path, timeout=min(timeout, 0.2)))
    A.alarm_touch("proj", "old", "orange")
    A.alarm_snooze("proj", "old", time.time() + 3600, why="waiting on the owner")
    before = open(A.alarms_path(), "rb").read()

    with real(A.alarms_path() + ".lock", timeout=1):      # a writer that has read the episodes and not yet written
        ring, episode = A.alarm_touch("proj", "new", "orange")
        assert ring == "orange" and episode["count"] == 1  # the notice is still decided, from the store as it stands
        assert open(A.alarms_path(), "rb").read() == before
        with pytest.raises(storage.LedgerLockTimeout):     # a person's unsnooze is refused, not written blind
            A.alarm_unsnooze("proj", "old")

    assert "proj:new" not in A.load_alarms()               # left for the next raise, which records it
    A.alarm_touch("proj", "new", "orange")
    assert A.load_alarms()["proj:new"]["count"] == 1
    assert A.alarm_snoozed("proj", "old")


def test_a_notice_still_rings_while_the_alarm_store_is_busy(project, monkeypatch):
    from ao import email, storage, telegram
    real = storage._exclusive_lock
    monkeypatch.setattr(storage, "_exclusive_lock", lambda path, timeout=10.0: real(path, timeout=min(timeout, 0.2)))
    rung = []
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: rung.append(title) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: rung.append(text) or 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None: True)

    with real(A.alarms_path() + ".lock", timeout=1):
        assert W.notify("proj: disk", "the disk is full", project["root"], key="disk", audience="human") is True

    assert rung and rung[0] == "proj: disk"
    assert "proj:disk" not in A.load_alarms()               # its record is left for the next raise



def test_a_mail_whose_mark_the_busy_store_could_not_take_goes_once_more(project, monkeypatch, capsys):
    """JOURNAL-7-3: a mark that a notice went is never written blind and is not kept, so the next cycle mails the red
    once more and records it - told twice, never not at all, and not a third time."""
    from ao import email, storage, telegram
    real = storage._exclusive_lock
    monkeypatch.setattr(storage, "_exclusive_lock", lambda path, timeout=10.0: real(path, timeout=min(timeout, 0.2)))
    mails = []
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None: mails.append(subject) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: 1)
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: True)
    mailed = A.alarm_mailed

    def stalled(*a, **k):                                 # a writer stalls on the store as the mail's mark is written
        with real(A.alarms_path() + ".lock", timeout=1):
            return mailed(*a, **k)

    def raise_disk():
        W.notify("proj: disk", "the disk is full", project["root"], key="disk", audience="human",
                 level="red", what="disk full")
    monkeypatch.setattr(A, "alarm_mailed", stalled)
    raise_disk()
    assert mails == ["proj: disk"] and A.load_alarms()["proj:disk"].get("red_sent") is None   # not written blind
    assert "alarm_mailed was not written" in capsys.readouterr().err
    monkeypatch.setattr(A, "alarm_mailed", mailed)
    raise_disk()                                          # the next cycle tells it once more...
    raise_disk()                                          # ...and records it: no third mail
    assert mails == ["proj: disk", "proj: disk"] and A.load_alarms()["proj:disk"]["red_sent"] is not None

LEASE_CONTENDER = """
import os, sys, time
sys.path.insert(0, {src!r})
from ao import cli, lib as A
journal, me, other, gate = sys.argv[1:5]
real = A._pid_alive
def judged_after_both_read(pid):          # a lease read as a holder's pid was judged after both reviews read it
    open(gate + "." + me, "w").close()
    deadline = time.time() + 10
    while not os.path.exists(gate + "." + other) and time.time() < deadline:
        time.sleep(0.01)
    return real(pid)
A._pid_alive = judged_after_both_read
while not os.path.exists(gate):
    time.sleep(0.005)
lease = cli._section_lease(journal)       # held while the process keeps it
print("HOLDS" if lease is not None else "refused", flush=True)
time.sleep(1.5)                           # asking its sections, the lease held
"""


def test_two_reviews_that_find_a_dead_holder_do_not_both_hold_the_lease(tmp_path):
    """JOURNAL-7-2: both read a lease whose holder had ended, both replaced it, and both asked and paid."""
    journal = str(tmp_path / "sections" / "abc.jsonl")
    os.makedirs(os.path.dirname(journal))
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    with open(journal + ".lease", "w", encoding="utf-8") as fh:            # left by a review that was killed
        json.dump({"pid": ended.pid, "start": 1}, fh)
    script = tmp_path / "contender.py"
    script.write_text(LEASE_CONTENDER.format(src=SRC), encoding="utf-8")
    gate = str(tmp_path / "go")
    reviews = [subprocess.Popen([sys.executable, str(script), journal, me, other, gate],
                                stdout=subprocess.PIPE, text=True)
               for me, other in (("one", "two"), ("two", "one"))]
    time.sleep(1.0)
    open(gate, "w").close()
    answers = [review.communicate(timeout=60)[0].strip() for review in reviews]

    assert sorted(answers) == ["HOLDS", "refused"]


def test_a_review_that_finds_the_lease_held_asks_nothing(project, tmp_path, monkeypatch):
    import glob
    from tests.test_review_sections import SCENARIOS, _args, _setup
    from ao import cli, storage
    root, cfg, calls = _setup(project, tmp_path, monkeypatch, "- [S1] the claim journal", fail="Scenario 3")
    assert cli.cmd_review(cfg, _args(SCENARIOS)) == 3                     # cut off at its third section
    [journal] = glob.glob(os.path.join(root, ".ao", "reviews", "sections", "*.jsonl"))
    calls.write_text("", encoding="utf-8")
    monkeypatch.setenv("AO_TEST_FAIL", "")

    with storage._exclusive_lock(journal + ".lease", timeout=1):           # held as a running review holds it
        assert cli.cmd_review(cfg, _args(SCENARIOS)) == 3

    assert calls.read_text() == ""


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


# ---- a deferral closes on the work it waited for, not on a cycle that ran (JOURNAL-4-2) ---------------------------

class _Clock:
    def __init__(self, at):
        self.at = at

    def time(self):
        return self.at

    def __getattr__(self, name):
        return getattr(time, name)


def _deferred_rows(root):
    path = os.path.join(root, ".ao", "ledger", "deferred.jsonl")
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()] if os.path.exists(path) else []


def _closed(root):
    return [(r["id"], r["outcome"]) for r in _deferred_rows(root) if r["event"] == "closed"]


def _old_heartbeat(root, age=60):
    path = A.heartbeat_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def _deferred_earlier(monkeypatch, root, kind, seconds=1200, **fields):
    monkeypatch.setattr(A, "time", _Clock(time.time() - seconds))
    row = A.deferred_append(root, kind, **fields)
    monkeypatch.setattr(A, "time", time)
    return row


def _nudges(world):
    return [a for a in world.spawned if isinstance(a, list) and a and str(a[0]).endswith("/kiro-cli")]


def _running_nudge(world, monkeypatch):
    class Running:
        pid, returncode = 99999, None

        def poll(self):
            return None
    monkeypatch.setattr(W.subprocess, "Popen", lambda argv, **kw: world.spawned.append(argv) or Running())
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def test_a_row_catchups_cycle_writes_because_it_still_cannot_nudge_stays_open(world, capsys):
    from ao import cli
    from tests.test_waiver_bounds import NAMED
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(3600)
    world.quota = False
    _old_heartbeat(world.root)

    cli.cmd_catchup(world.cfg, NAMED)

    assert [r["kind"] for r in A.deferred_open(world.root)] == ["nudge"] and _closed(world.root) == []
    assert "stays open: its cycle could not do it yet" in capsys.readouterr().out


def test_a_standing_row_its_cycle_still_cannot_do_stays_open(world, monkeypatch):
    from ao import cli
    from tests.test_waiver_bounds import NAMED
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(3600)
    world.quota = False
    standing = _deferred_earlier(monkeypatch, world.root, "nudge", reason="implementer quota")
    _old_heartbeat(world.root)

    cli.cmd_catchup(world.cfg, NAMED)

    assert [r["id"] for r in A.deferred_open(world.root)] == [standing["id"]] and _closed(world.root) == []


def test_a_row_whose_nudge_catchups_cycle_starts_closes_as_done(world, monkeypatch, capsys):
    from ao import cli
    from tests.test_waiver_bounds import NAMED
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(3600)
    standing = _deferred_earlier(monkeypatch, world.root, "nudge", reason="implementer quota")
    _running_nudge(world, monkeypatch)
    _old_heartbeat(world.root)

    cli.cmd_catchup(world.cfg, NAMED)

    assert len(_nudges(world)) == 1
    assert A.deferred_open(world.root) == [] and _closed(world.root) == [(standing["id"], "nudged")]
    assert "catchup handled 1 item(s)" in capsys.readouterr().out.replace("\x1b[32m", "").replace("\x1b[0m", "")


def test_a_wake_row_inside_its_quota_window_stays_open(world, monkeypatch):
    from ao import cli
    from tests.test_waiver_bounds import NAMED
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    until = time.time() + 3600
    st = W.load_state(world.root)
    st["arch_quota_until"] = until
    W.save_state(world.root, st)
    standing = _deferred_earlier(monkeypatch, world.root, "wake", reason="architect quota", until=until)
    _old_heartbeat(world.root)

    cli.cmd_catchup(world.cfg, NAMED)

    assert [r["id"] for r in A.deferred_open(world.root)] == [standing["id"]] and _closed(world.root) == []


def test_a_cycle_that_stops_at_a_hold_leaves_the_row_open(world, monkeypatch):
    from ao import cli
    from tests.test_waiver_bounds import NAMED
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(3600)
    standing = _deferred_earlier(monkeypatch, world.root, "nudge", reason="implementer quota")
    with open(os.path.join(world.root, ".ao", "hold"), "w", encoding="utf-8") as fh:
        json.dump({"by": "A. Person", "reason": "looking at it", "at": time.time()}, fh)
    _old_heartbeat(world.root)

    cli.cmd_catchup(world.cfg, NAMED)

    assert [r["id"] for r in A.deferred_open(world.root)] == [standing["id"]] and _closed(world.root) == []


def test_a_nudge_a_fraction_of_a_second_before_a_deferral_does_not_close_it(project, monkeypatch):
    root = project["root"]
    second = 1_791_200_100
    monkeypatch.setattr(A, "time", _Clock(second + 0.9))
    A.deferred_append(root, "nudge", reason="implementer quota")
    monkeypatch.setattr(A, "time", time)

    W._close_deferred_done(root, {"last_nudge": second + 0.7})
    assert len(A.deferred_open(root)) == 1

    W._close_deferred_done(root, {"last_nudge": second + 1.2})
    assert A.deferred_open(root) == []


def test_a_whole_second_row_on_disk_counts_from_the_end_of_its_second(project):
    root = project["root"]
    second = 1_791_200_100
    path = os.path.join(root, ".ao", "ledger", "deferred.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "deferred", "id": f"DF-{second}-nudge", "kind": "nudge", "at": second}) + "\n")

    W._close_deferred_done(root, {"last_nudge": second + 0.7})
    assert len(A.deferred_open(root)) == 1

    W._close_deferred_done(root, {"last_nudge": second + 1.2})
    assert A.deferred_open(root) == []


def test_a_cycle_that_ran_inside_one_timestamp_quantum_is_read_as_run(world, monkeypatch, capsys):
    from ao import cli
    from tests.test_waiver_bounds import NAMED
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(3600)
    standing = _deferred_earlier(monkeypatch, world.root, "nudge", reason="implementer quota")
    _running_nudge(world, monkeypatch)
    path = _old_heartbeat(world.root)
    quantum = float(int(time.time()))
    os.utime(path, (quantum, quantum))
    beat = A.heartbeat

    def coarse(root):
        beat(root)
        os.utime(A.heartbeat_path(root), (quantum, quantum))
    monkeypatch.setattr(A, "heartbeat", coarse)

    cli.cmd_catchup(world.cfg, NAMED)

    out = capsys.readouterr().out
    assert "another cycle holds this project" not in out
    assert A.deferred_open(world.root) == [] and _closed(world.root) == [(standing["id"], "nudged")]


def test_another_cycles_beat_is_not_read_as_catchups_cycle(world, monkeypatch, capsys):
    from ao import cli
    from ao.storage import _exclusive_lock
    from tests.test_waiver_bounds import NAMED
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(3600)
    world.quota = False
    standing = _deferred_earlier(monkeypatch, world.root, "nudge", reason="implementer quota")
    _old_heartbeat(world.root)
    run = W.run

    def raced(args):
        A.heartbeat(args.root)
        return run(args)
    monkeypatch.setattr(W, "run", raced)
    os.makedirs(W.STATE_DIR, exist_ok=True)
    lock = os.path.join(W.STATE_DIR, W.CYCLE_LOCK.format(key=A.project_key(world.root)))

    with _exclusive_lock(lock):
        cli.cmd_catchup(world.cfg, NAMED)

    out = capsys.readouterr().out
    assert "another cycle holds this project" in out and "stays open: no cycle ran" in out
    assert [r["id"] for r in A.deferred_open(world.root)] == [standing["id"]] and _closed(world.root) == []


# ---- a started step holds while its process provably runs; only the architect resumes its session (JOURNAL-2-2) --

SID = "0b7c7d3e-5a8e-4a43-9d3f-2f0e5d1c9a11"
ARCH = {"argv": ["claude", "--resume", "{session}", "-p", "{prompt}"]}


def _architect(world, **fields):
    world.cfg["architect"] = dict(world.cfg["architect"], **fields)
    stored = {k: v for k, v in world.cfg.items() if k != "root"}
    with open(os.path.join(world.root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)


def test_a_started_step_is_read_whatever_its_lease(tmp_path):
    from ao import journal as J
    path = str(tmp_path / "journal.db")
    assert J.claim(path, "wake:new:abc", "architect", 900, now=1000.0)
    J.started(path, "wake:new:abc", 4242, 999.5)
    assert J.under_way(path, "architect", now=1901.0) == []
    assert J.running(path, "architect") == [("wake:new:abc", 4242, 999.5)]
    J.finished(path, "wake:new:abc")
    assert J.running(path, "architect") == []


def test_a_wake_cut_off_before_it_registered_holds_past_its_lease_while_its_process_runs(world, monkeypatch):
    """A sessionless architect cut off before it was registered: neither the helper scan nor `session_in_use` sees
    it, the journal is all there is, and past its lease a second wake started beside it."""
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: 1234.5 if pid == 99999 else None)
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    register = A.helper_register

    def killed(root, pid, what):
        raise KeyboardInterrupt
    monkeypatch.setattr(A, "helper_register", killed)
    with pytest.raises(KeyboardInterrupt):
        world.cycle(dry_run=False)
    (first,) = _architect_wakes(world)
    world.process(99999, list(first))
    monkeypatch.setattr(A, "helper_register", register)
    world.spawned.clear()

    later = time.time() + W.WAKE_LEASE + 60
    monkeypatch.setattr(time, "time", lambda: later)
    trace = world.cycle(dry_run=False)
    assert _architect_wakes(world) == []
    assert any("an architect wake is already running" in line for line in trace)

    world.procs.pop(99999, None)                          # it ended: the key is released and the wake retried
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1


def test_a_reused_pid_does_not_hold_a_step_past_its_lease(monkeypatch):
    monkeypatch.setattr(A, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: 2000.0)     # another process now
    assert not W._step_process_proven(4242, 999.5)
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: None)       # unreadable
    assert not W._step_process_proven(4242, 999.5)
    assert not W._step_process_proven(4242, None)                                    # never recorded


def test_an_architect_a_cut_off_cycle_started_is_adopted_and_neither_reaped_nor_doubled(world, monkeypatch):
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: 1234.5 if pid == 99999 else None)
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)

    def killed(root, pid, what):
        raise KeyboardInterrupt
    monkeypatch.setattr(A, "helper_register", killed)
    with pytest.raises(KeyboardInterrupt):
        world.cycle(dry_run=False)
    (first,) = _architect_wakes(world)
    world.process(99999, list(first))
    # the registry as the scenario keeps it: what helper_register writes, helper_pids reads
    monkeypatch.setattr(A, "helper_register", lambda root, pid, what: world.helper(pid, what))
    world.spawned.clear()

    later = time.time() + W.WAKE_LEASE + 60
    monkeypatch.setattr(time, "time", lambda: later)
    trace = world.cycle(dry_run=False)

    assert _architect_wakes(world) == []
    assert 99999 in world.procs                                  # not reaped as a hung implementer turn
    assert any("is registered now" in line for line in trace)
    assert not any("reaping" in line for line in trace)


def test_of_many_processes_opening_a_new_journal_at_once_none_is_refused(tmp_path):
    """Switching a new journal to WAL answered `database is locked` at once to a second opener, past the busy
    timeout, and a claim failed as if the journal could not be written."""
    child = ("import sys, time, json\nsys.path.insert(0, %r)\nfrom ao import journal as J\n"
             "at = float(sys.argv[1])\nwhile time.time() < at: pass\n"
             "print(json.dumps(J.claim(sys.argv[2], 'refill:s1:1', 'architect', 900)))\n") % SRC
    for round_ in range(5):
        path = str(tmp_path / f"j{round_}.db")
        at = time.time() + 1.0
        runs = [subprocess.Popen([sys.executable, "-c", child, str(at), path], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for _ in range(12)]
        answers = [run.communicate() for run in runs]
        assert all(run.returncode == 0 for run in runs), [err for _, err in answers if err][:1]
        assert sorted(json.loads(out) for out, _ in answers) == [False] * 11 + [True]


def test_a_journal_that_cannot_be_written_starts_the_step_and_tells_a_person(world, monkeypatch):
    """Failing open is meant - a broken journal must not stop every wake - but nobody was told the single-owner
    guarantee was off."""
    from ao import journal as J

    def unwritable(*args, **kwargs):
        raise J.sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(J, "claim", unwritable)

    assert W._claim_step(world.root, "wake:new:abc") is True

    assert any(title.endswith("the watchdog's journal cannot be written") and audience == "human"
               for title, _, audience, _ in world.notices)


@pytest.mark.parametrize("argv, hit", [
    (["claude", "--resume", SID, "-p", "x"], True),
    (["/opt/x/bin/claude", "--resume", SID], True),
    (["node", "/opt/claude-code/cli.js", "--resume", SID], True),    # the adapter's process name in the path
    (["node", "/opt/node_modules/claude/cli.js", "--resume", SID], True),
    (["node", "/opt/tools/indexer.js", SID], False),                  # a runtime running something else
    (["grep", "-r", SID, "/srv/x/.claude"], False),
    (["python3", "worker.py", SID], False),
    (["jq", "--arg", "s", SID, "."], False),
    (["tmux", "new-session", "-d", "-s", SID], False),
    (["docker", "run", "--label=" + SID, "img"], False),
])
def test_only_the_architects_harness_resumes_its_session(monkeypatch, argv, hit):
    from ao import procs
    monkeypatch.setattr(procs, "all_pids", lambda: [4242])
    monkeypatch.setattr(procs, "argv", lambda pid: argv if pid == 4242 else None)
    assert (A.session_in_use(SID, ARCH) == [4242]) is hit


def test_an_equals_form_harness_still_counts(monkeypatch):
    from ao import procs
    monkeypatch.setattr(procs, "all_pids", lambda: [4242])
    monkeypatch.setattr(procs, "argv", lambda pid: ["agy", f"--conversation={SID}", "--print=x"])
    assert A.session_in_use(SID, {"argv": ["agy", "--conversation={session}", "--print={prompt}"]}) == [4242]


def test_an_unrelated_process_naming_the_session_does_not_stop_a_wake(world):
    _architect(world, session="auto", argv=["claude", "--resume", "{session}", "-p", "{prompt}"])
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.process(4242, ["python3", "worker.py", "sess-1"], cwd="/elsewhere")

    world.cycle(dry_run=False)

    assert len(_architect_wakes(world)) == 1


def test_the_architects_own_resume_of_the_session_still_stops_a_wake(world):
    _architect(world, session="auto", argv=["claude", "--resume", "{session}", "-p", "{prompt}"])
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.process(4242, ["/agents/claude", "--resume", "sess-1", "-p", "x"], cwd="/elsewhere")

    trace = world.cycle(dry_run=False)

    assert _architect_wakes(world) == []
    assert any("is resumed by a running process already" in line for line in trace)
