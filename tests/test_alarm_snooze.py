import time
from types import SimpleNamespace

from ao import cli, lib as A, telegram
from ao import watchdog as W


def _channels(monkeypatch):
    desktop, phone = [], []
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: desktop.append(argv))
    monkeypatch.setattr(telegram, "send", lambda body, target: phone.append((body, target)) or True)
    return desktop, phone


def test_a_snoozed_alarm_stays_off_the_channels_and_off_the_ladder(project, monkeypatch):
    desktop, phone = _channels(monkeypatch)
    A.alarm_snooze("proj", "doctor:commit-hook", time.time() + 3600, by="owner", why="adopt the marker on 1 Oct")

    sent = W.notify("proj: commit-hook", "legacy hook", project["root"], key="doctor:commit-hook",
                    window=3600, audience="human")

    assert sent is False and desktop == [] and phone == []
    assert A.active_alarms("proj") == []


def test_an_expired_snooze_rings_again(project, monkeypatch):
    desktop, _ = _channels(monkeypatch)
    A.alarm_snooze("proj", "doctor:commit-hook", time.time() - 1, by="owner", why="over")

    assert W.notify("proj: commit-hook", "legacy hook", project["root"], key="doctor:commit-hook",
                    window=3600, audience="human") is True
    assert A.active_alarms("proj")[0]["key"] == "doctor:commit-hook" and len(desktop) == 1


def test_a_snooze_needs_a_future_date_and_a_reason_and_is_listed(project, capsys):
    def alarms(**overrides):
        args = dict(action="snooze", key="doctor:commit-hook", until=None, why=None, by="owner", level=None)
        return cli.cmd_alarms(project, SimpleNamespace(**dict(args, **overrides)))

    assert alarms(until="2099-01-01") == 2
    assert alarms(until="2001-01-01", why="in the past") == 2
    assert alarms(until="2099-01-01", why="waits on the owner") == 0
    assert A.alarm_snoozed("proj", "doctor:commit-hook")["why"] == "waits on the owner"
    capsys.readouterr()

    assert alarms(action="list", key=None) == 0
    assert "snoozed" in capsys.readouterr().out
    assert alarms(action="unsnooze") == 0 and A.alarm_snoozed("proj", "doctor:commit-hook") is None
