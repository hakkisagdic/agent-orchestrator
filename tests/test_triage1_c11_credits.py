"""Credit shares reuse one reading and compare only a known billing cycle (TRIAGE-1-MEDIUM)."""
import time
from types import SimpleNamespace
import pytest
from ao import cli, lib as A
from datetime import date


def test_c11_credits_reads_account_once(project, monkeypatch, capsys):
    calls = []

    def account(**kwargs):
        calls.append(kwargs)
        return {"used": 50, "limit": 100, "reset_at": None} if len(calls) == 1 else None

    monkeypatch.setattr(A, "account_usage", account)
    assert cli.cmd_credits(project, SimpleNamespace(offline=False, local=False, reset_day=None)) == 0
    assert len(calls) == 1 and "counts every session" in capsys.readouterr().out


def test_c11_share_uses_account_cycle_not_requested_cost_window(project, monkeypatch):
    start = time.time() - 3 * 86400
    monkeypatch.setattr(A, "account_usage", lambda **kwargs: {
        "used": 100, "limit": 200, "cycle_start": start})
    monkeypatch.setattr(A, "turn_costs", lambda cfg, since=None: {
        "turns": 1, "total": 20 if since == start else 150, "unit": "credits"})
    lines = "\n".join(cli._account_beside_share(project))
    assert "20%" in lines and "cycle" in lines and "150%" not in lines


def test_c11_unknown_cycle_has_no_share_percentage(project, monkeypatch):
    monkeypatch.setattr(A, "account_usage", lambda **kwargs: {"used": 100, "limit": 200})
    monkeypatch.setattr(A, "turn_costs", lambda *args, **kwargs: {"turns": 1, "total": 20, "unit": "credits"})
    lines = "\n".join(cli._account_beside_share(project))
    assert "%" not in lines and "cycle start" in lines


@pytest.mark.parametrize("known_cycle", [False, True])
def test_c11_local_calendar_subtotal_is_not_an_account_share(project, monkeypatch, capsys, known_cycle):
    acct = {"used": 100, "limit": 200, "reset_at": None}
    if known_cycle:
        acct["cycle_start"] = 1000
    asked = []
    monkeypatch.setattr(A, "account_usage", lambda **kwargs: asked.append(kwargs) or acct)
    monkeypatch.setattr(A, "turn_costs", lambda *args, **kwargs: {
        "turns": 1, "total": 10, "unit": "credits"})
    month = date.today().replace(day=1).isoformat()
    monkeypatch.setattr(A, "credit_usage", lambda **kwargs: {"days": {month: 25, "2000-01-01": 70}})
    assert cli.cmd_credits(project, SimpleNamespace(offline=False, local=True, reset_day=None)) == 0
    out = capsys.readouterr().out
    assert "(25%) of it" not in out
    assert f"local transcript estimate, calendar month {month[:7]}: 25" in out
    assert len(asked) == 1
    if known_cycle:
        assert "10% of the account's used" in out
    else:
        assert "cycle start is unknown" in out
