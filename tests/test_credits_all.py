"""`ao credits --all` reads every shipped adapter's account and local estimate (CREDITS-ALL).

Only an adapter that declares a lookup ao ships a driver for is asked; one that declares none
is named beside the others rather than shown another harness's figure, or a zero. The local
estimate is read only where the adapter declares its transcripts, and `--offline` asks nothing.
"""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from ao import cli, lib as A

KIRO = {"billing": {"api": {"driver": "usage-limits"},
                    "fallback": {"transcripts": "~/.kiro/sessions/*/*/messages.jsonl"}}}
READING = {"billing": {"fallback": {"reading": "per-response"}}}


def _args(**values):
    base = dict(all=True, offline=False, local=False, reset_day=None)
    base.update(values)
    return SimpleNamespace(**base)


def _month_days():
    month = date.today().replace(day=1)
    return {month.isoformat(): 2.0, (month - timedelta(days=1)).isoformat(): 99.0,
            "2099-01-01": 3.0}


def test_every_readable_account_is_asked_and_the_rest_are_named(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "package_adapters", lambda: {"kiro": KIRO, "qoder": READING})
    monkeypatch.setattr(A, "usage_api", lambda ident: {"driver": "usage-limits"} if ident == "kiro" else {})
    asked = []

    def account(timeout=20, adapter_id=None):
        asked.append((timeout, adapter_id))
        return {"plan": "Kiro Pro", "used": 12.3, "limit": 1000, "reset_at": 1790000000, "cycle_start": None}

    monkeypatch.setattr(A, "account_usage", account)
    monkeypatch.setattr(A, "credit_usage", lambda adapter_id: {"days": _month_days()})

    assert cli.cmd_credits(project, _args()) == 0
    out = capsys.readouterr().out
    assert "kiro" in out and "Kiro Pro" in out and "12.30 of 1,000" in out and "resets" in out
    assert asked == [(20, "kiro")]
    assert "this machine" in out and "2.00 on 1 day(s)" in out
    assert "5.00" not in out and "99.00" not in out and "left out" in out and "2099-01-01" in out
    assert "no account ao can read, and no local estimate declared: qoder" in out


def test_offline_asks_no_account_and_says_so(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "package_adapters", lambda: {"kiro": KIRO})
    monkeypatch.setattr(A, "usage_api", lambda ident: {"driver": "usage-limits"})
    monkeypatch.setattr(A, "account_usage", lambda **k: pytest.fail("--offline must ask nothing"))
    monkeypatch.setattr(A, "credit_usage", lambda adapter_id: {"days": {}})

    assert cli.cmd_credits(project, _args(offline=True)) == 0
    out = capsys.readouterr().out
    assert "not asked (--offline)" in out and "no usage records this month" in out


def test_a_failed_lookup_is_named_and_the_others_still_read(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "package_adapters", lambda: {"kiro": KIRO})
    monkeypatch.setattr(A, "usage_api", lambda ident: {"driver": "usage-limits"})
    monkeypatch.setattr(A, "account_usage", lambda **k: {"error": "the token store is missing"})
    monkeypatch.setattr(A, "credit_usage", lambda adapter_id: {"days": {}})

    assert cli.cmd_credits(project, _args()) == 0
    out = capsys.readouterr().out
    assert "lookup failed" in out and "the token store is missing" in out


@pytest.mark.parametrize(
    "acct, why",
    [
        ({"plan": "Kiro Pro", "used": None, "limit": None, "reset_at": None}, "no usage or limit in response"),
        ({"plan": "Kiro Pro", "used": None, "limit": 1000, "reset_at": 1790000000}, "no usage in response"),
        ({"plan": "Kiro Pro", "used": 12.3, "limit": None, "reset_at": 1790000000}, "no limit in response"),
    ],
)
def test_missing_account_fields_say_reading_is_unavailable_and_print_no_number(project, monkeypatch, capsys, acct, why):
    monkeypatch.setattr(A, "package_adapters", lambda: {"kiro": KIRO})
    monkeypatch.setattr(A, "usage_api", lambda ident: {"driver": "usage-limits"})
    monkeypatch.setattr(A, "account_usage", lambda **k: acct)
    monkeypatch.setattr(A, "credit_usage", lambda adapter_id: {"days": {}})

    assert cli.cmd_credits(project, _args()) == 0
    out = capsys.readouterr().out
    assert "reading unavailable" in out and why in out
    assert "0.00" not in out and "of 0" not in out and "0.0%" not in out and "1,000" not in out and "12.30" not in out


def test_current_month_estimate_bounds_both_ends_and_names_what_was_left_out(project, monkeypatch, capsys):
    today = date.today()
    first = today.replace(day=1)
    last = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    before = (first - timedelta(days=1)).isoformat()
    days = {first.isoformat(): 2.0, last.isoformat(): 1.5, before: 99.0, "2099-01-01": 3.0}
    monkeypatch.setattr(A, "package_adapters", lambda: {"kiro": KIRO})
    monkeypatch.setattr(A, "usage_api", lambda ident: {})
    monkeypatch.setattr(A, "credit_usage", lambda adapter_id: {"days": days})

    assert cli.cmd_credits(project, _args(offline=True)) == 0
    out = capsys.readouterr().out
    assert "3.50 on 2 day(s)" in out
    assert "2 day(s) outside" in out and "left out" in out and before in out and "2099-01-01" in out
    assert "6.50" not in out and "99.00" not in out and "3.00" not in out
