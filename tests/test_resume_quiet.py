import json
import os
import time

import pytest

from ao import email, lib as A, telegram
from ao import watchdog as W
from tests.scenarios import World

NOTIFY = W.notify                  # the scenario world replaces it; a resume is about the real ladder
REQUEST = "20260916-0900-kiro-to-fable-BLOCKED-store.md"
DAY = 86400
EXHAUSTED = {"used": 10240.0, "limit": 10000.0, "account": "acct-1"}


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    w = World(project, monkeypatch, tmp_path)
    monkeypatch.setattr(W, "notify", NOTIFY)
    w.sent = []
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: w.sent.append(("desktop", title)) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: w.sent.append(("telegram", text)) or 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None:
                        w.sent.append(("email", subject)) or True)
    return w


def _last_cycle(world, seconds_ago):
    """The heartbeat the last cycle left, `seconds_ago`; nothing has run since."""
    path = A.heartbeat_path(world.root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("1")
    then = time.time() - seconds_ago
    os.utime(path, (then, then))
    return then


def _request(world, name, seconds_ago):
    world.mail(name, "# which store keeps the ledger?\n\n## KARAR GEREKLİ\n")
    then = time.time() - seconds_ago
    os.utime(os.path.join(world.root, "agent-mail", name), (then, then))


def _channel(world, name):
    return [text for channel, text in world.sent if channel == name]


def _resume_notices(world):
    return [row for row in A.notices(world.root, limit=100, include_suppressed=True) if row.get("key") == "resume"]


def test_after_two_weeks_one_notice_names_everything_and_nothing_rings_on_the_silence(world, monkeypatch):
    began = _last_cycle(world, 14 * DAY)
    # What stood when the jobs were switched off: an unseen request already red and mailed,
    # the credits alarm snoozed until a day before the return, a wake deferred on the
    # architect's quota, and an implementer idle with no credits left.
    _request(world, REQUEST, 15 * DAY)
    A.save_alarms({f"proj:unseen:{REQUEST}": {"first": began - 5 * 3600, "last": began, "level": "red", "count": 150,
                                              "title": "proj: unread decision request", "ring": "red",
                                              "red_due": False, "red_sent": began - 3600}})
    A.alarm_snooze("proj", "credits-exhaust", time.time() - DAY, by="owner", why="the plan resets on the 1st")
    with open(os.path.join(world.root, ".ao", "ledger", "deferred.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "deferred", "id": f"DF-{int(began - DAY)}-wake", "kind": "wake",
                             "at": int(began - DAY), "reason": "architect quota"}) + "\n")
    W.save_state(world.root, {"last_credit_sample": began, "last_credit_attempt": began})
    monkeypatch.setattr(A, "account_usage", lambda timeout=20: dict(EXHAUSTED))
    world.transcript_age(14 * DAY + 3600)

    trace = world.cycle()
    assert world.sent == [] and "resume" not in W.load_state(world.root)
    assert any("would send one resume notice to the human" in line for line in trace)

    world.cycle(dry_run=False)

    assert _channel(world, "email") == []
    assert _channel(world, "desktop") == ["proj: watchdog resumed after 14d"]
    [phone] = _channel(world, "telegram")
    [notice] = _resume_notices(world)
    for key in (f"unseen:{REQUEST}", "credits-exhaust", "deferred:wake", "anomaly:decision-requested"):
        assert key in notice["named"] and key in phone, key
    assert notice["sent"] is True and len(notice["named"]) == len(set(notice["named"]))
    # Nothing is red on the age the silence gave it; the carried episode closed unannounced.
    assert {alarm["key"] for alarm in A.active_alarms("proj")} == {"credits-exhaust"}
    assert not any("no longer raised" in row["title"]
                   for row in A.notices(world.root, limit=100, include_suppressed=True))

    world.sent.clear()
    world.cycle(dry_run=False)

    assert world.sent == [] and len(_resume_notices(world)) == 1


def test_a_request_after_the_resume_climbs_as_before_and_a_carried_one_ages_from_the_resume(world):
    resumed = time.time() - 90 * 60
    _last_cycle(world, 60)
    W.save_state(world.root, {"resume": {"at": resumed, "since": resumed - 14 * DAY, "quiet": 7200,
                                         "carried": [], "snoozes": [], "announced": int(resumed)}})
    carried, fresh = REQUEST, "20260930-1200-kiro-to-fable-BLOCKED-queue.md"
    _request(world, carried, 15 * DAY)
    _request(world, fresh, 70 * 60)
    world.transcript_age(900)

    world.cycle(dry_run=False)

    told = [text for text in _channel(world, "telegram") if "unread decision request" in text]
    assert _channel(world, "email") == [] and len(told) == 2
    assert any(f"{fresh} has waited 70m and nobody has been shown it" in text and "resumed" not in text
               for text in told)
    assert any(carried in text and "(90m since the watchdog resumed)" in text for text in told)
    rings = {alarm["key"]: alarm["ring"] for alarm in A.active_alarms("proj")}
    assert rings[f"unseen:{carried}"] == "orange" and rings[f"unseen:{fresh}"] == "orange"
    assert _resume_notices(world) == []


def test_a_silence_under_the_threshold_changes_nothing(world):
    _last_cycle(world, 40 * 60)
    _request(world, REQUEST, 5 * 3600)
    world.transcript_age(900)

    world.cycle(dry_run=False)

    assert "resume" not in W.load_state(world.root) and _resume_notices(world) == []
    assert _channel(world, "email") == ["proj: unread decision request"]


def test_a_snooze_that_ended_in_the_silence_is_named_once_and_a_standing_one_stays_off(world, monkeypatch):
    _last_cycle(world, 3 * DAY)
    A.alarm_snooze("proj", "credits-exhaust", time.time() - DAY, by="owner", why="the plan resets on the 1st")
    A.alarm_snooze("proj", "waiting-human:S1", time.time() + 7 * DAY, by="owner", why="back next week")
    world.board("blocked", "- [S1] pick a vendor · waiting: human · needs: the owner's choice")
    A.save_alarms({"proj:waiting-human:S1": {"first": time.time() - 4 * DAY, "last": time.time() - 3 * DAY,
                                             "level": "orange", "ring": "orange", "count": 9}})
    world.transcript_age(3 * DAY)
    monkeypatch.setattr(A, "account_usage", lambda timeout=20: dict(EXHAUSTED))

    world.cycle(dry_run=False)

    [notice] = _resume_notices(world)
    assert "credits-exhaust" in notice["named"] and "its snooze ended" in notice["msg"]
    assert "waiting-human:S1" not in notice["named"]
    assert _channel(world, "email") == [] and len(_channel(world, "telegram")) == 1
    assert "credits-exhaust" in {alarm["key"] for alarm in A.active_alarms("proj")}     # one alarm (#106)

    world.sent.clear()
    st = W.load_state(world.root)
    W._sample_credits(world.root, st, A.load_adapter("kiro", world.root), "proj", now=time.time() + 3600)

    assert world.sent == []


def test_a_silence_that_carried_only_the_architects_business_goes_to_its_mailbox(world):
    _last_cycle(world, 3 * DAY)
    _request(world, REQUEST, 4 * DAY)
    world.transcript_age(3 * DAY)
    # The whole machine was off: a sibling has not ticked yet either, and is not a dead watchdog.
    sibling = os.path.join(A.HOME, ".ao", "heartbeat-other")
    with open(sibling, "w", encoding="utf-8") as fh:
        fh.write("1")
    os.utime(sibling, (time.time() - 3 * DAY, time.time() - 3 * DAY))

    world.cycle(dry_run=False)

    [notice] = _resume_notices(world)
    assert notice["sent"] is False and f"unseen:{REQUEST}" in notice["named"]
    assert world.sent == []
    assert any(name.startswith("watchdog-to-fable-ANOMALY-resumed-")
               for name in os.listdir(os.path.join(world.root, "agent-mail")))
