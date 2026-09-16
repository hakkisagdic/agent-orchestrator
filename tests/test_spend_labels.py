import re
from collections import Counter
from types import SimpleNamespace

from ao import cli, lib as A


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _usage(monkeypatch):
    monkeypatch.setattr(A, "account_usage", lambda timeout=20: {
        "used": 5000.0, "limit": 10000.0, "reset_at": None, "plan": "KIRO PRO+", "overage_status": "DISABLED"})
    monkeypatch.setattr(A, "turn_costs", lambda cfg, since=None: {
        "turns": 10, "total": 1250.0, "unit": "credits", "ao_commands": Counter(),
        "by_class": {"product": {"turns": 10, "usage": 1250.0, "wasted": 0, "wasted_usage": 0}}})


def test_ao_cost_sets_the_account_beside_aos_own_share(project, monkeypatch, capsys):
    _usage(monkeypatch)

    assert cli.cmd_cost(project, SimpleNamespace(since=None)) == 0

    out = _plain(capsys)
    assert "the account: 5,000 of 10,000 used" in out
    assert "ao's own share, from its transcript: 1,250 credits (25% of the account's used)" in out
    assert "ao's transcript holds only ao's own turns" in out


def test_ao_credits_says_whose_spend_the_account_figure_is(project, monkeypatch, capsys):
    _usage(monkeypatch)

    assert cli.cmd_credits(project, SimpleNamespace(offline=False, local=False, reset_day=None)) == 0

    out = _plain(capsys)
    assert "ao's own share, from its transcript" in out and "counts every session on it" in out
