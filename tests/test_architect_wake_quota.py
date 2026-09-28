"""The architect is not woken before its usage limit resets, and one notice says when it will be (ARCHITECT-WAKE-QUOTA).

A wake that stopped on the architect's usage limit was read with the five-hour window whatever
the limit. A weekly limit whose stop named only a clock time already past when it was written
was read as that time gone, so its block ended at once and the architect could be woken again
within minutes, days before its week was over. The wake now reads the limit the stop names, and
the window the machine reads for the architect's provider - keyflip's, kept in ~/.ao/quota.json,
as the implementer's quota gate reads it - and holds every wake until the later reset. With no
reset known it waits one window after the stop, as it always did.

Each scenario runs whole watchdog cycles against a fabricated world (tests/scenarios.py) with a
canned wake log and, where it names one, a canned reading.
"""
import json
import os
import time

import pytest

from ao import email, lib as A, telegram, watchdog as W
from tests.scenarios import World

HOUR, DAY = 3600, 86400
REPORT = "20260916-1200-kiro-to-fable-BLOCKED-queue.md"
ASKING = "# queue empty\n\n## DECISION REQUIRED\n"
# The world reads no log and no reading; these scenarios read the canned ones they write.
READ_WAKE_LOG, READ_WINDOW, READ_QUOTA = W.wake_error, A.provider_window, A.quota


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    monkeypatch.setattr(W, "wake_error", READ_WAKE_LOG)
    world.rung = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw:
                        world.rung.append(dict(kw, title=title, msg=msg)) or True)
    return world


def _written(ago):
    """When a stop was written, `ago` seconds back: a whole second, never in a day's first two minutes.

    A clock time a minute before it is then that same day's, so a stop can name a clock time
    already past when it was written.
    """
    at = int(time.time()) - ago
    first_minutes = time.localtime(at).tm_hour == 0 and time.localtime(at).tm_min < 2
    return at - 180 if first_minutes else at


def _clock(at):
    """A clock time as a usage-limit message names it: "2:05pm"."""
    return time.strftime("%I:%M%p", time.localtime(at)).lstrip("0").lower()


def _next_day_at(at):
    """The clock time of `at` the next day, as a reset that names only it is read: 24 hours after that day's."""
    lt = time.localtime(at)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, 0, 0, 0, -1)) + DAY


def _stopped(world, at, message, log="escalate-log", wake="escalate"):
    """The last wake of the architect, started at `at`, as its log reads: it stopped on `message`."""
    os.makedirs(W.STATE_DIR, exist_ok=True)
    path = os.path.join(W.STATE_DIR, A.project_file_name(log, A.project_key(world.root)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(at))} {wake} /agents/claude 2.1.261 ===\n"
                 f"{message}\n")


def _reading(monkeypatch, *lines):
    """keyflip's lines, kept in ~/.ao/quota.json under the command the adapters' quota block declares.

    The reading is read back as every ao process reads it; a command started to take a new
    one fails the test, so what is read is what was canned.
    """
    command = " ".join(A._window_adapter()["telemetry"]["quota"]["argv"])
    os.makedirs(os.path.dirname(A.quota_readings_path()), exist_ok=True)
    with open(A.quota_readings_path(), "w", encoding="utf-8") as fh:
        json.dump({command: {"at": time.time(), "lines": list(lines)}}, fh)
    monkeypatch.setattr(A, "_QUOTA", {})
    monkeypatch.setattr(A, "quota", READ_QUOTA)
    monkeypatch.setattr(A, "provider_window", READ_WINDOW)
    monkeypatch.setattr(A, "_quota_output", lambda argv: pytest.fail(f"a reading was taken: {argv}"))


def _architect_wakes(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and argv[0].endswith("/claude")]


def _quota_notices(world):
    return [notice for notice in world.rung if notice.get("key") == "architect-quota"]


def test_a_weekly_limit_that_names_only_a_clock_time_is_not_woken_before_that_time_comes_round(world):
    world.transcript_age(900).mail(REPORT, ASKING)
    at = _written(2 * HOUR)
    _stopped(world, at, f"You've hit your weekly limit · resets {_clock(at - 60)} (Europe/Istanbul)")
    reset = _next_day_at(at - 60)

    first = world.cycle(dry_run=False)
    second = world.cycle(dry_run=False)

    assert _architect_wakes(world) == []
    assert W.load_state(world.root)["arch_quota_until"] == reset
    for trace in (first, second):
        assert any(f"architect at quota until {W.reset_when(reset)}; not waking" in line for line in trace)
    [told, *_] = _quota_notices(world)
    assert told["quiet_until"] == reset and f"wakes are held until {W.reset_when(reset)}" in told["msg"]


def test_a_short_window_is_woken_once_the_reset_it_names_has_passed(world):
    world.transcript_age(900).mail(REPORT, ASKING)
    at = _written(2 * HOUR)
    _stopped(world, at, f"You've hit your session limit · resets {_clock(at + HOUR)} (Europe/Istanbul)")

    world.cycle(dry_run=False)

    assert len(_architect_wakes(world)) == 1
    assert "arch_quota_until" not in W.load_state(world.root)
    assert _quota_notices(world) == []


@pytest.mark.parametrize("lines", [(), ("Claude (Anthropic) 12%  5h  resets in 1h",)],
                         ids=["no reading", "a window with headroom"])
@pytest.mark.parametrize("ago,woken", [(2 * HOUR, False), (6 * HOUR, True)])
def test_a_stop_that_names_no_reset_is_held_one_window_after_it_as_it_always_was(world, monkeypatch, lines,
                                                                                    ago, woken):
    _reading(monkeypatch, *lines)
    world.transcript_age(900).mail(REPORT, ASKING)
    at = _written(ago)
    _stopped(world, at, "You've hit your weekly limit")

    world.cycle(dry_run=False)

    assert bool(_architect_wakes(world)) is woken
    assert W.load_state(world.root).get("arch_quota_until") == (None if woken else at + 5 * HOUR)


def test_the_reset_a_spent_window_states_holds_the_wake_and_the_notice_names_its_day(world, monkeypatch):
    _reading(monkeypatch, "Claude (Anthropic) 100%  weekly  resets in 3d 4h", "Codex (OpenAI) 3%  5h  resets in 2h")
    world.transcript_age(900).mail(REPORT, ASKING)
    _stopped(world, _written(2 * HOUR), "You've hit your weekly limit")

    before = time.time()
    world.cycle(dry_run=False)
    after = time.time()

    until = W.load_state(world.root)["arch_quota_until"]
    assert before + 3 * DAY + 4 * HOUR <= until <= after + 3 * DAY + 4 * HOUR
    assert _architect_wakes(world) == []
    [told] = _quota_notices(world)
    day = time.strftime("%d %b %H:%M", time.localtime(until))
    assert told["quiet_until"] == until and f"wakes are held until {day};" in told["msg"]


def test_a_refill_waits_for_the_reset_the_spent_window_states_and_says_its_day(world, monkeypatch):
    _reading(monkeypatch, "Claude (Anthropic) 100%  weekly  resets in 3d 4h")
    world.transcript_age(900)
    _stopped(world, _written(2 * HOUR), "You've hit your weekly limit", log="refill-log", wake="refill")

    before = time.time()
    trace = world.cycle(dry_run=False)
    after = time.time()

    assert _architect_wakes(world) == []
    days = {time.strftime("%d %b %H:%M", time.localtime(at + 3 * DAY + 4 * HOUR)) for at in (before, after)}
    assert any(trace[-1] == f"the last refill hit the architect's limit; waiting until {day}" for day in days)


def test_the_end_of_a_hold_is_told_by_its_clock_within_a_day_and_by_its_date_beyond():
    now = time.mktime(time.strptime("2026-09-10 12:00", "%Y-%m-%d %H:%M"))

    assert W.reset_when(now + 5 * HOUR, now) == "17:00"
    assert W.reset_when(now + 22 * HOUR, now) == "10:00"
    assert W.reset_when(now + 3 * DAY + 4 * HOUR, now) == "13 Sep 16:00"


def test_one_notice_says_when_and_says_it_again_only_when_the_end_moves(project, monkeypatch):
    root = project["root"]
    clock = [time.mktime(time.strptime("2026-09-10 12:00", "%Y-%m-%d %H:%M"))]
    rung, mailed = [], []
    monkeypatch.setattr(W.time, "time", lambda: clock[0])
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: rung.append(msg) or True)
    monkeypatch.setattr(telegram, "send", lambda *args, **kw: False)
    monkeypatch.setattr(email, "send", lambda title, body, target: mailed.append(body) or True)
    # The five-hour window's stop: rung once, and mailed once the hour has turned it red.
    state = {"arch_quota_until": clock[0] + 3 * HOUR, "wake_error": {"text": "You've hit your session limit"}}
    assert W.touch_architect_quota(root, state) is True
    clock[0] += 61 * 60
    W.touch_architect_quota(root, state)
    assert len(rung) == 1 and len(mailed) == 1 and "wakes are held until 15:00" in mailed[0]

    clock[0] += 5 * 60
    W.touch_architect_quota(root, state)
    assert len(rung) == 1 and len(mailed) == 1                   # nothing new: held until the end it named

    # The week's limit read after it moves the end: that is told, once.
    state = {"arch_quota_until": clock[0] + 3 * DAY, "wake_error": {"text": "You've hit your weekly limit"}}
    W.touch_architect_quota(root, state)
    W.touch_architect_quota(root, state)
    assert len(mailed) == 2 and "wakes are held until 13 Sep 13:06" in mailed[1]


# ---- a weekly or monthly limit that names no reset (ARCHITECT-WAKE-QUOTA-2) ------------------------------------

def _stopped_in_a_row(world, stops, log="escalate-log", wake="escalate"):
    """The architect's last wakes, one segment each, as its log reads them: [(started at, message)]."""
    os.makedirs(W.STATE_DIR, exist_ok=True)
    path = os.path.join(W.STATE_DIR, A.project_file_name(log, A.project_key(world.root)))
    with open(path, "w", encoding="utf-8") as fh:
        for at, message in stops:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(at))} {wake} /agents/claude 2.1.261 ===\n"
                     f"{message}\n")


@pytest.mark.parametrize("in_a_row, held", [(1, 5 * HOUR), (2, 10 * HOUR), (3, 20 * HOUR), (8, 7 * DAY)])
def test_each_wake_in_a_row_that_stops_on_a_weekly_limit_naming_no_reset_waits_twice_as_long(
        world, monkeypatch, in_a_row, held):
    _reading(monkeypatch)
    world.transcript_age(900).mail(REPORT, ASKING)
    last = _written(60)
    _stopped_in_a_row(world, [(last - (in_a_row - 1 - n) * HOUR, "You've hit your weekly limit")
                              for n in range(in_a_row)])

    world.cycle(dry_run=False)

    assert not _architect_wakes(world)
    assert W.load_state(world.root).get("arch_quota_until") == last + held


def test_a_stop_that_named_its_reset_breaks_the_run(world, monkeypatch):
    _reading(monkeypatch)
    world.transcript_age(900).mail(REPORT, ASKING)
    last = _written(60)
    _stopped_in_a_row(world, [(last - 3 * HOUR, "You've hit your weekly limit"),
                              (last - 2 * HOUR, "You've hit your session limit · resets in 1h"),
                              (last, "You've hit your weekly limit")])

    world.cycle(dry_run=False)

    assert W.load_state(world.root).get("arch_quota_until") == last + 5 * HOUR
