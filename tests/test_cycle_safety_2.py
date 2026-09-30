"""A turn whose start could not be read is believed by its pid for a bounded time (CYCLE-SAFETY-2).

The retrospective review of CYCLE-SAFETY found child_alive falling back to the pid alone when the
watchdog could not read a spawned turn's start: a pid another process was given since read as the
turn still running, and the refill wake waited for good.
"""
import time

from ao import lib as A, watchdog as W


def test_a_turn_whose_start_was_not_read_is_believed_by_its_pid_for_a_bounded_time(monkeypatch):
    monkeypatch.setattr(A, "_pid_alive", lambda pid: True)
    state = {"child_pid": 4242, "child_start": None, "last_nudge": time.time() - 60}

    assert W.child_alive(state) is True
    state["last_nudge"] = time.time() - W.UNVERIFIED_CHILD_SECONDS - 1
    assert W.child_alive(state) is False


def test_a_turn_whose_start_was_read_is_known_by_it(monkeypatch):
    monkeypatch.setattr(A, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: "started at nine")
    state = {"child_pid": 4242, "child_start": "started at nine", "last_nudge": 0}

    assert W.child_alive(state) is True
    assert W.child_alive(dict(state, child_start="started at ten")) is False
