import time

from ao import cli, lib as A
from ao import watchdog as W


def _capture(monkeypatch, problems):
    raised = []
    monkeypatch.setattr(cli, "doctor_problems", lambda cfg: problems)
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, key=None, window=1800,
                        audience=None, level=None, **kw: raised.append(key) or True)
    return raised


def test_the_doctor_does_not_ring_a_second_alarm_for_what_the_watchdog_rings(project, monkeypatch, capsys):
    raised = _capture(monkeypatch, [
        ("credits-exhaust", "credits exhausted at the last reading"),
        ("wake-failed", "last architect wake failed (binary): not found"),
        ("no-channel", "no human channel beyond desktop notifications"),
    ])
    A.alarm_touch("proj", "credits-exhaust", "red", title="proj: credits exhausted")

    assert cli._doctor_check(project, page=True) == 1

    out = capsys.readouterr().out
    assert "PROBLEM credits-exhaust" in out and "PROBLEM no-channel" in out
    assert raised == ["doctor:wake-failed", "doctor:no-channel"]


def test_the_doctor_rings_once_the_watchdogs_alarm_has_gone_quiet(project, monkeypatch):
    raised = _capture(monkeypatch, [("wake-failed", "last architect wake failed (session): gone")])
    A.alarm_touch("proj", "architect-wake-failed", "orange",
                  now=time.time() - A.ALARM_RESET_AFTER - 60)

    assert cli._doctor_check(project, page=True) == 1

    assert raised == ["doctor:wake-failed"]


def test_a_reading_over_the_limit_is_exhausted_not_a_date_ahead():
    projection = {"before_reset": True, "exhausts_at": time.time() + 3600, "per_day": 8547}

    assert cli._credits_problem(projection, {"used": 12503.14, "limit": 10000}) == (
        "credits-exhaust", "credits exhausted at the last reading: 12503/10000")
    code, text = cli._credits_problem(projection, {"used": 9000, "limit": 10000})
    assert code == "credits-exhaust" and text.startswith("credits run out ")
    assert cli._credits_problem({"before_reset": False}, {"used": 10, "limit": 10000}) is None
    assert cli._credits_problem(None, None) is None
