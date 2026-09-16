import time
from types import SimpleNamespace

from ao import cli, email, lib as A, telegram
from ao import watchdog as W

PROBLEMS = [
    ("watchdog-dead", "watchdog silent for 40m — launchctl / ao watchdog status"),
    ("no-channel", "no human channel beyond desktop notifications — ao email setup"),
    ("credits-exhaust", "credits exhausted at the last reading: 12503/10000"),
    ("shared-pool", "the reviewer draws on the implementer's credit pool"),
]


def _record_notify(monkeypatch):
    calls = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, key=None, window=1800, audience=None,
                        level=None, quiet_until=None: calls.append((key, audience, quiet_until)) or True)
    return calls


def _channels(monkeypatch):
    desktop, phone, mailed = [], [], []
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: desktop.append(argv))
    monkeypatch.setattr(telegram, "send", lambda body, target: phone.append(body) or True)
    monkeypatch.setattr(email, "send", lambda title, body, target: mailed.append(title) or True)
    return desktop, phone, mailed


def test_a_check_run_by_a_person_or_an_agent_pages_nobody(project, monkeypatch, capsys):
    calls = _record_notify(monkeypatch)
    monkeypatch.setattr(cli, "doctor_problems", lambda cfg: PROBLEMS)

    assert cli.cmd_doctor(project, SimpleNamespace(check=True)) == 1

    assert calls == []
    out = capsys.readouterr().out
    assert all(f"PROBLEM {key}:" in out for key, _ in PROBLEMS)


def test_the_scheduled_check_pages_only_red_and_records_advisories_for_the_architect(project, monkeypatch):
    calls = _record_notify(monkeypatch)
    monkeypatch.setattr(cli, "doctor_problems", lambda cfg: PROBLEMS)
    A.record_credit_sample(project["root"], 12503, 10000, reset_at=1790812800)

    assert cli.cmd_doctor(project, SimpleNamespace(check=True, notify=True)) == 1

    assert calls == [("doctor:watchdog-dead", "human", None),
                     ("doctor:no-channel", "architect", None),
                     ("doctor:credits-exhaust", "human", 1790812800),
                     ("doctor:shared-pool", "architect", None)]


def test_exhausted_credits_are_red_and_a_projection_is_an_advisory():
    assert cli._doctor_severity("credits-exhaust", "credits exhausted at the last reading: 1/1") == "red"
    assert cli._doctor_severity("credits-exhaust", "credits run out 20 Sep, before the reset (900/day)") == "yellow"
    assert cli._doctor_severity("commit-hook", "execution proof failed") == "yellow"


def test_a_standing_red_with_a_known_end_is_mailed_once_then_held(project, monkeypatch):
    desktop, phone, mailed = _channels(monkeypatch)
    end = time.time() + 14 * 86400

    assert W.notify("proj: credits exhausted", "12503/10000", project["root"], key="credits-exhaust",
                    audience="human", level="red", quiet_until=end) is True
    assert (len(mailed), len(desktop), len(phone)) == (1, 1, 1)

    alarms = A.load_alarms()
    alarms["proj:credits-exhaust"]["red_sent"] -= 7 * 3600     # past the six hours after which red mails again
    A.save_alarms(alarms)

    assert W.notify("proj: credits exhausted", "12503/10000", project["root"], key="credits-exhaust",
                    window=0, audience="human", level="red", quiet_until=end) is False
    assert (len(mailed), len(desktop), len(phone)) == (1, 1, 1)


def test_the_hold_ends_with_the_condition_it_waited_for(project):
    start = 1_000_000.0
    end = start + 24 * 3600
    A.alarm_touch("proj", "k", "red", now=start, quiet_until=end)
    A.alarm_mailed("proj", "k", now=start)
    seen = {}
    for hour in range(1, 26):
        _, episode = A.alarm_touch("proj", "k", "red", now=start + hour * 3600, quiet_until=end)
        seen[hour] = episode["red_due"]

    assert seen[7] is False and seen[23] is False
    assert seen[25] is True


def test_an_alarm_that_stops_being_raised_is_recorded_not_announced(project, monkeypatch):
    desktop, phone, mailed = _channels(monkeypatch)
    root = project["root"]

    W._announce_resolved(root, {"project": "proj", "key": "doctor:no-channel", "count": 3, "age_s": 600})
    assert (desktop, phone, mailed) == ([], [], [])

    W._announce_resolved(root, {"project": "proj", "key": "credits-exhaust", "count": 6, "age_s": 9000,
                                "red_sent": time.time() - 3600, "ring": "red"})
    assert len(desktop) == 1 and phone == [] and mailed == []
    titles = [notice.get("title") for notice in A.notices(root, limit=10, include_suppressed=True)]
    assert "proj: no longer raised — doctor:no-channel" in titles
