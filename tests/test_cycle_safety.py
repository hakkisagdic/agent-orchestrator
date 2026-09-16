import os
import time
from types import SimpleNamespace

import pytest

from ao import lib as A, storage, watchdog as W
from tests.scenarios import World

KIRO = ["/agents/kiro-cli", "chat", "--resume-id", "s1", "--no-interactive", "devam"]


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _live(world):
    return world.cycle(dry_run=False)


def _turns(world):
    """Agent processes the cycle started; a live cycle's git calls go through Popen too."""
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).startswith("/agents/")]


def test_a_second_live_cycle_stands_down_while_one_runs(world):
    lock = os.path.join(W.STATE_DIR, W.CYCLE_LOCK.format(key=os.path.basename(world.root)))
    os.makedirs(W.STATE_DIR, exist_ok=True)
    with storage._exclusive_lock(lock, timeout=1):
        trace = _live(world)
    assert any("another watchdog cycle is running" in line for line in trace)
    assert _turns(world) == []


def test_the_heartbeat_is_written_before_a_cycle_ends_early(project):
    root = project["root"]
    assert A.heartbeat_age(root) is None
    W.run(SimpleNamespace(root=root, idle_minutes=6.0, dry_run=False, prompt=W.NUDGE_PROMPT))
    assert A.heartbeat_age(root) is not None


def test_a_hold_placed_during_the_cycle_stops_the_nudge(world, monkeypatch):
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)
    late = {"now": False}
    # The hold appears after the guards ran: the flag record is the last step before the spawn.
    monkeypatch.setattr(A, "record_actor_flags", lambda *args, **kwargs: late.update(now=True))
    monkeypatch.setattr(A, "hold_state", lambda root: {"by": "a person", "minutes": 5, "reason": "editing"}
                        if late["now"] else None)
    trace = _live(world)

    assert any("since this cycle began; not nudging" in line for line in trace)
    assert _turns(world) == []


def test_a_person_left_in_the_tree_after_reaping_is_not_nudged_over(world):
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.process(510, KIRO)
    world.process(520, ["/agents/kiro-cli", "chat"], headless=False, tty="ttys001")
    world.transcript_age(6 * 60 * 3 + 60)

    trace = _live(world)

    assert any("interactive agent process(es) remain" in line for line in trace)
    assert 520 in world.procs and _turns(world) == []


def test_the_child_is_known_by_its_start_not_its_pid(monkeypatch):
    monkeypatch.setattr(A, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: "reused")
    assert W.child_alive({"child_pid": 123, "child_start": "ours"}) is False
    assert W.child_alive({"child_pid": 123, "child_start": "reused"}) is True
    monkeypatch.setattr(A, "_pid_alive", lambda pid: False)
    assert W.child_alive({"child_pid": 123, "child_start": "reused"}) is False


def test_only_the_architects_mail_counts_as_an_intervention(world, monkeypatch):
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)
    world.review("NEEDS_CHANGES", age=600)
    monkeypatch.setattr(A, "rounds", lambda root, reviews: 9)
    world.mail("20260916-1200-watchdog-to-fable-ANOMALY-over-round-budget.md", "# over budget\n")

    world.cycle()
    assert "over budget (9/5); notified instead of nudging" in world.verdict

    world.mail("20260916-1300-fable-to-kiro-DECISION-respecify.md", "# re-specified\n")
    trace = world.cycle()
    assert any("re-specified since the last review; proceeding" in line for line in trace)


def test_a_parked_review_is_a_persons_to_unblock_not_open_work(world):
    A.set_reviewer_state(world.root, pending_review=True, until=None,
                         reason="Not logged in: token " + "s" + "k-" + "ant-" + "AB" * 12)
    assert not any("reviewer" in reason for reason in W.open_work(world.cfg, world.root))

    world.transcript_age(900)
    _live(world)

    parked = [notice for notice in world.notices if "a review is parked" in notice[0]]
    assert parked and parked[0][2] == "human" and "[redacted]" in parked[0][1]


def test_a_retired_projects_heartbeat_is_not_a_dead_watchdog(project):
    ao = os.path.join(A.HOME, ".ao")
    os.makedirs(ao, exist_ok=True)
    for name, age in (("heartbeat-recently-dead", 20 * 60), ("heartbeat-retired", 10 * 86400)):
        path = os.path.join(ao, name)
        open(path, "w", encoding="utf-8").write("1")
        then = time.time() - age
        os.utime(path, (then, then))

    assert set(A.stale_siblings(project["root"])) == {"recently-dead"}


def test_desktop_notifications_never_raise_where_the_platform_has_none(monkeypatch):
    monkeypatch.setattr(W.sys, "platform", "linux")
    assert W.desktop_notify("title", "message") is False
    assert all(part for part in W.child_path().split(os.pathsep))


def test_a_refill_is_not_started_without_quota(world):
    world.transcript_age(900)
    world.quota = False

    trace = _live(world)

    assert any("no quota headroom to wake the architect" in line for line in trace)
    assert _turns(world) == []


def _last_refill_failed(monkeypatch, kind, text):
    at = time.time() - 60
    monkeypatch.setattr(W, "wake_error", lambda log_path: {
        "text": text, "binary": "/agents/claude 2.1.261", "when": "x", "at": at,
        "kind": kind, "resets_at": None} if "refill-" in os.path.basename(log_path) else None)


def test_a_refill_that_failed_on_its_binary_is_not_retried_and_a_person_is_told(world, monkeypatch):
    world.transcript_age(900)
    _last_refill_failed(monkeypatch, "binary", "env: node: No such file or directory")

    trace = _live(world)

    assert any("the last refill with this binary failed (binary); not retrying" in line for line in trace)
    assert _turns(world) == []
    assert [notice for notice in world.notices if "refill failed" in notice[0] and notice[2] == "human"]


def test_a_refill_after_a_transport_error_is_retried_and_a_person_is_told(world, monkeypatch):
    world.transcript_age(900)
    _last_refill_failed(monkeypatch, "other", "API Error: 500 upstream unavailable")

    _live(world)

    assert len(_turns(world)) == 1
    assert [notice for notice in world.notices if "refill failed" in notice[0]]


def test_a_refill_that_hit_the_limit_waits_for_its_window(world, monkeypatch):
    world.transcript_age(900)
    _last_refill_failed(monkeypatch, "quota", "You've hit your session limit")

    trace = _live(world)

    assert any("the last refill hit the architect's limit; waiting until" in line for line in trace)
    assert _turns(world) == []
