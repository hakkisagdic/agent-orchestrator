import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from ao import cli, email, features as F, lib as A, telegram
from ao import watchdog as W
from tests.scenarios import World

NOTIFY = W.notify                  # the scenario world replaces it; these are about the real ladder
IMPLEMENTER = "kiro"               # the project's implementer: the shipped adapter whose account the sampler reads
DAY, HOUR = 86400, 3600
REQUEST = "20260902-0900-kiro-to-fable-BLOCKED-credits.md"
BLOCKED = "# credits are exhausted until the reset\n\n## KARAR GEREKLİ\n\nnew account or wait?\n"
NEEDS_YOU = "decision-requested — no architect will act on it: architect wakes are switched off"


def _clock(monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    return clock


def _channels(monkeypatch):
    sent = []
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: sent.append(("desktop", title, msg)) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None:
                        sent.append(("telegram", text, "")) or 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None:
                        sent.append(("email", subject, body)) or True)
    return sent


def _on(sent, channel):
    return [title for kind, title, _ in sent if kind == channel]


def _exhausted(monkeypatch, reset, account="acct-1"):
    monkeypatch.setattr(A, "account_usage", lambda timeout=20, adapter_id=None: {
        "used": 10240.0, "limit": 10000.0, "reset_at": reset, "account": account})


def _heartbeat(root, at):
    """The heartbeat a watchdog cycle leaves, written at `at`."""
    path = A.heartbeat_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(int(at)))
    os.utime(path, (at, at))


def _doctor_ran(root, at):
    """The scheduled check's record of its last run."""
    path = os.path.join(A.HOME, ".ao", f"doctor-{A.project_key(root)}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"at": int(at), "back": None}, fh)


def _watchdog_job(root, interval):
    """The watchdog's launchd job as `ao watchdog install --interval` writes it."""
    label = f"com.agentorchestrator.watchdog.{A.project_key(root).lower()}"
    path = os.path.join(A.HOME, "Library", "LaunchAgents", f"{label}.plist")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cli.PLIST.format(label=label, python_arg="", script="ao-watchdog", root=root, idle=6,
                                  interval=interval, log="watchdog.log", path="/usr/bin"))


def _doctor_findings(cfg):
    """What the scheduled check finds about the watchdog and the credits, worded as doctor_problems words it."""
    root, found = cfg["root"], []
    age = A.heartbeat_age(root)
    if age is not None and age > cli.WATCHDOG_SILENT_AFTER:
        found.append(("watchdog-dead", f"watchdog silent for {age // 60}m — launchctl / ao watchdog status"))
    own = cli._implementer_credits(cfg)
    credits = cli._credits_problem(own["rate"], own["samples"][-1] if own["samples"] else None)
    return found + ([credits] if credits else []) + [
        ("no-channel", "no human channel beyond desktop notifications — ao email setup")]


def _paged(monkeypatch):
    raised = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, key=None, audience=None, **kw:
                        raised.append((key, audience)) or True)
    monkeypatch.setattr(cli, "doctor_problems", _doctor_findings)
    return raised


def _human(raised):
    return [key for key, audience in raised if audience == "human"]


# ---- credits: a known end holds the mail, and news is told ------------------------------------

def test_exhausted_credits_are_mailed_once_and_held_until_the_reset_the_reading_names(project, monkeypatch):
    sent = _channels(monkeypatch)
    clock = _clock(monkeypatch)
    root = project["root"]
    reset = clock[0] + 25 * HOUR + 30
    _exhausted(monkeypatch, reset)
    st = {}

    for _ in range(50):                                   # a reading every half hour, all of the day before the reset
        W._sample_credits(root, st, IMPLEMENTER, "proj", now=clock[0])
        clock[0] += 1801

    assert [kind for kind, _, _ in sent] == ["email", "desktop", "telegram"]
    (alarm,) = A.active_alarms("proj")
    assert alarm["key"] == "credits-exhaust" and alarm["quiet_until"] == reset and alarm["count"] == 50

    W._sample_credits(root, st, IMPLEMENTER, "proj", now=clock[0])     # past the reset, and still spent

    assert _on(sent, "email") == ["proj: credits exhausted"] * 2


def test_the_day_a_projected_run_out_comes_true_is_told_though_the_projection_is_held(project, monkeypatch):
    sent = _channels(monkeypatch)
    clock = _clock(monkeypatch)
    root = project["root"]
    reset = clock[0] + 30 * DAY
    A.record_credit_sample(root, 1000, 10_000, reset_at=reset, account="acct-1", at=clock[0] - 5 * HOUR,
                           adapter=IMPLEMENTER)
    reading = {"used": 5000.0, "limit": 10000.0, "reset_at": reset, "account": "acct-1"}
    monkeypatch.setattr(A, "account_usage", lambda timeout=20, adapter_id=None: dict(reading))
    st = {}

    for used in (5000.0, 5000.0, 10240.0, 10240.0):
        reading["used"] = used
        W._sample_credits(root, st, IMPLEMENTER, "proj", now=clock[0])
        clock[0] += 1801

    mailed = _on(sent, "email")
    assert len(mailed) == 2 and mailed[0].startswith("proj: credits run out ")
    assert mailed[1] == "proj: credits exhausted"
    assert len(_on(sent, "desktop")) == 1 and len(_on(sent, "telegram")) == 1


def test_a_reset_is_a_known_end_only_in_the_seconds_the_provider_gives():
    now = 1_790_000_000

    assert W.credit_reset(now + DAY, now) == now + DAY and W.credit_reset(str(now + DAY), now) == now + DAY
    assert W.credit_reset(now - DAY, now) == now - DAY                  # past: it holds nothing
    assert W.credit_reset((now + DAY) * 1000, now) is None
    assert W.credit_reset("2026-10-01", now) is None and W.credit_reset(None, now) is None


# ---- the doctor: a job that came back, and one condition as one alarm ---------------------------

def test_the_doctor_leaves_a_watchdog_whose_job_just_came_back_to_its_first_cycle(project, monkeypatch, capsys):
    clock = _clock(monkeypatch)
    raised = _paged(monkeypatch)
    root = project["root"]
    stopped = clock[0] - 14 * DAY
    _heartbeat(root, stopped)                             # both jobs were switched off together
    _doctor_ran(root, stopped)
    _watchdog_job(root, 300)
    A.record_credit_sample(root, 10240, 10000, reset_at=clock[0] + 10 * DAY, account="acct-1", at=stopped - 600,
                           adapter=IMPLEMENTER)

    assert cli._doctor_check(project, page=True) == 1

    out = capsys.readouterr().out
    assert _human(raised) == [] and "PROBLEM watchdog-dead" in out and "not paged" in out

    clock[0] += 60                                        # its first cycle ran, and it keeps running
    _heartbeat(root, clock[0])
    clock[0] += 14 * 60
    _heartbeat(root, clock[0] - 30)
    cli._doctor_check(project, page=True)

    assert _human(raised) == ["credits-exhaust"]


def test_a_watchdog_that_does_not_come_back_is_paged_one_run_later(project, monkeypatch):
    clock = _clock(monkeypatch)
    raised = _paged(monkeypatch)
    root = project["root"]
    _heartbeat(root, clock[0] - 14 * DAY)
    _watchdog_job(root, 300)                              # and the check has no record of its own yet

    cli._doctor_check(project, page=True)
    clock[0] += 15 * 60
    assert _human(raised) == []
    cli._doctor_check(project, page=True)

    assert _human(raised) == ["doctor:watchdog-dead"]


def test_a_doctor_that_cannot_keep_its_record_pages_a_silent_watchdog_as_it_did(project, monkeypatch):
    clock = _clock(monkeypatch)
    raised = _paged(monkeypatch)
    root = project["root"]
    _heartbeat(root, clock[0] - 14 * DAY)
    os.makedirs(os.path.join(A.HOME, ".ao", f"doctor-{A.project_key(root)}.json"))     # nothing can be written there

    cli._doctor_check(project, page=True)

    assert _human(raised) == ["doctor:watchdog-dead"]


@pytest.mark.parametrize("check_ran, watchdog_ran", [(15 * 60, 40 * 60), (2 * DAY, 3 * DAY)],
                         ids=["check-running", "watchdog-stopped-first"])
def test_a_watchdog_that_stopped_while_the_doctor_ran_is_paged_at_once(project, monkeypatch, check_ran, watchdog_ran):
    clock = _clock(monkeypatch)
    raised = _paged(monkeypatch)
    root = project["root"]
    _heartbeat(root, clock[0] - watchdog_ran)
    _doctor_ran(root, clock[0] - check_ran)
    _watchdog_job(root, 300)

    cli._doctor_check(project, page=True)

    assert _human(raised) == ["doctor:watchdog-dead"]


def test_the_doctor_raises_the_watchdogs_own_credits_alarm(project, monkeypatch):
    sent = _channels(monkeypatch)
    monkeypatch.setattr(cli, "doctor_problems", _doctor_findings)
    root = project["root"]
    _exhausted(monkeypatch, time.time() + 10 * DAY)
    W._sample_credits(root, {}, IMPLEMENTER, "proj")
    _heartbeat(root, time.time())

    for _ in range(3):
        assert cli._doctor_check(project, page=True) == 1

    assert [kind for kind, _, _ in sent] == ["email", "desktop", "telegram"]
    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["credits-exhaust"]


def test_a_snooze_on_the_credits_alarm_keeps_the_doctor_off_the_channels_too(project, monkeypatch):
    sent = _channels(monkeypatch)
    monkeypatch.setattr(cli, "doctor_problems", _doctor_findings)
    root = project["root"]
    A.record_credit_sample(root, 10240, 10000, reset_at=time.time() + 10 * DAY, account="acct-1", adapter=IMPLEMENTER)
    A.alarm_snooze("proj", "credits-exhaust", time.time() + 10 * DAY, by="owner", why="the plan resets on the 1st")
    _heartbeat(root, time.time())

    assert cli._doctor_check(project, page=True) == 1

    assert sent == [] and A.active_alarms("proj") == []


# ---- needs you: once, then the ladder -----------------------------------------------------------

def test_needs_you_rings_once_turns_red_and_mails_on_its_schedule_until_another_request_waits(project, monkeypatch):
    sent = _channels(monkeypatch)
    clock = _clock(monkeypatch)
    root, start = project["root"], clock[0]

    def raised(minute, request):
        clock[0] = start + minute * 60
        return W.notify("proj: needs you", NEEDS_YOU, root, key="anomaly:decision-requested", window=600,
                        audience="human", what=f"decision-requested:{request}")

    for minute in range(0, 7 * 60 + 1, 10):              # the same request, every ten minutes for seven hours
        raised(minute, REQUEST)

    assert _on(sent, "desktop") == ["proj: needs you"] and len(_on(sent, "telegram")) == 1
    assert _on(sent, "email") == ["proj: needs you"] * 2                      # red after its hour, again six hours on

    assert raised(7 * 60 + 10, "20260903-1000-kiro-to-fable-BLOCKED-store.md") is True

    assert len(_on(sent, "desktop")) == 2 and len(_on(sent, "email")) == 3    # another request is news


def test_needs_you_says_what_waits(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    told = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: told.append((title, kw)) or True)
    F.set_switch(world.root, "architect_wake", False)
    world.mail(REQUEST, BLOCKED)
    world.transcript_age(HOUR)

    world.cycle(dry_run=False)

    # The request and the watchdog's report of it are named by the anomaly's alarm alone (WAITING-ONE-ALARM).
    assert {kw["key"]: kw.get("what") for title, kw in told if title == "proj: needs you"} == {
        "anomaly:decision-requested": f"decision-requested:{REQUEST}"}


def test_a_request_that_arrives_just_after_a_resume_is_told(project, monkeypatch, tmp_path):
    clock = _clock(monkeypatch)
    world = World(project, monkeypatch, tmp_path)
    monkeypatch.setattr(W, "notify", NOTIFY)
    sent = _channels(monkeypatch)
    monkeypatch.setattr(A, "heartbeat", lambda target: _heartbeat(target, clock[0]))
    F.set_switch(world.root, "architect_wake", False)
    _heartbeat(world.root, clock[0] - 3 * DAY)
    world.mail(REQUEST, BLOCKED)
    world.transcript_age(3 * DAY)
    A.mail_seen(world.root, [REQUEST], by="architect")

    world.cycle(dry_run=False)                            # the resume names "needs you" once
    assert _on(sent, "desktop") == ["proj: watchdog resumed after 3d"]

    later = "20260903-1000-kiro-to-fable-BLOCKED-store.md"
    world.mail(later, BLOCKED)
    os.utime(os.path.join(world.root, "agent-mail", later), (clock[0] + 60, clock[0] + 60))
    A.mail_seen(world.root, [later], by="architect")
    for _ in range(3):
        clock[0] += 300
        world.cycle(dry_run=False)

    assert "proj: needs you" in _on(sent, "desktop")


def test_an_architect_without_quota_is_handed_off_once_for_what_waits(project, monkeypatch, tmp_path):
    clock = _clock(monkeypatch)
    world = World(project, monkeypatch, tmp_path)
    handoffs = []
    monkeypatch.setattr(W, "subprocess", SimpleNamespace(
        run=lambda argv, **kw: handoffs.append(argv) if "handoff" in argv else None, Popen=subprocess.Popen,
        DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT, PIPE=subprocess.PIPE,
        SubprocessError=subprocess.SubprocessError, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(A, "heartbeat", lambda target: _heartbeat(target, clock[0]))
    world.quota = False
    world.mail(REQUEST, BLOCKED)
    world.transcript_age(HOUR)

    for _ in range(3):
        world.cycle(dry_run=False)
        clock[0] += HOUR + 1
    assert len(handoffs) == 1

    world.mail("20260903-1000-kiro-to-fable-BLOCKED-store.md", BLOCKED)
    later = clock[0] + 1
    os.utime(os.path.join(world.root, "agent-mail", "20260903-1000-kiro-to-fable-BLOCKED-store.md"), (later, later))
    clock[0] += 2
    world.cycle(dry_run=False)

    assert len(handoffs) == 2


# ---- a day after two weeks off ------------------------------------------------------------------

def test_a_day_after_two_weeks_off_tells_each_condition_once_on_each_channel_its_ladder_allows(
        project, monkeypatch, tmp_path):
    clock = _clock(monkeypatch)
    world = World(project, monkeypatch, tmp_path)
    monkeypatch.setattr(W, "notify", NOTIFY)
    sent = _channels(monkeypatch)
    monkeypatch.setattr(cli, "doctor_problems", _doctor_findings)
    monkeypatch.setattr(A, "heartbeat", lambda target: _heartbeat(target, clock[0]))
    root, start = world.root, clock[0]
    for switch in ("nudge", "refill", "architect_wake"):
        F.set_switch(root, switch, False)
    # Two weeks ago both jobs were switched off. The implementer's credits were spent, reset ten days
    # from now; it had asked for a decision nobody can wake the architect to take; the credits alarm
    # had mailed and "needs you" had turned red.
    stopped, reset = start - 14 * DAY, start + 10 * DAY
    _heartbeat(root, stopped)
    _doctor_ran(root, stopped)
    _watchdog_job(root, 300)
    world.mail(REQUEST, BLOCKED)
    os.utime(os.path.join(root, "agent-mail", REQUEST), (stopped - DAY, stopped - DAY))
    A.mail_seen(root, [REQUEST], by="architect")
    world.transcript_age(14 * DAY + 2 * HOUR)
    for before in (6 * HOUR, 600):
        A.record_credit_sample(root, 10200, 10000, reset_at=reset, account="acct-1", at=stopped - before,
                               adapter=IMPLEMENTER)
    W.save_state(root, {"last_credit_sample": stopped, "last_credit_attempt": stopped})
    A.save_alarms({
        "proj:credits-exhaust": {"first": stopped - DAY, "last": stopped, "level": "red", "ring": "red", "count": 48,
                                 "title": "proj: credits exhausted", "red_due": False, "red_sent": stopped - 5 * HOUR},
        "proj:anomaly:decision-requested": {"first": stopped - DAY, "last": stopped, "level": "orange", "ring": "red",
                                            "count": 140, "title": "proj: needs you", "red_due": False,
                                            "red_sent": stopped - 2 * HOUR}})
    _exhausted(monkeypatch, reset)
    # Both jobs load again, the doctor first; then the watchdog runs every five minutes and the doctor
    # every quarter hour, for a day.
    jobs = sorted([(at, "doctor") for at in range(0, DAY, 900)] + [(at, "watchdog") for at in range(5, DAY, 300)])

    for at, job in jobs:
        clock[0] = start + at
        if job == "watchdog":
            world.cycle(dry_run=False)
        else:
            cli._doctor_check(A.load_config(root), page=True)

    assert _on(sent, "desktop") == ["proj: watchdog resumed after 14d"]
    assert len(_on(sent, "telegram")) == 1
    mailed = [body.split("\n")[0] for kind, _, body in sent if kind == "email"]
    # One alarm for the request, which says which report waits and since when (WAITING-ONE-ALARM).
    assert mailed == [f"decision-requested: {REQUEST} in agent-mail/, waiting since {W._when(A._name_time(REQUEST))} "
                      "— no architect will act on it: architect wakes are switched off"] * 4
    alarms = {alarm["key"]: alarm for alarm in A.active_alarms("proj")}
    assert alarms["credits-exhaust"].get("red_sent") is None and alarms["credits-exhaust"]["quiet_until"] == reset
    assert "doctor:watchdog-dead" not in alarms and "doctor:credits-exhaust" not in alarms
