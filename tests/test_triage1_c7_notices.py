"""Credit and mail notices retain the evidence behind their claims (TRIAGE-1-MEDIUM)."""
import time
from pathlib import Path
from types import SimpleNamespace
from ao import cli, lib as A, storage, watchdog as W


def test_c7_doctor_credit_notice_has_sample(project, monkeypatch, capsys):
    root = project["root"]
    monkeypatch.setattr(cli, "doctor_problems", lambda cfg: [("credits-exhaust", "credits exhausted: spent")])
    monkeypatch.setattr(cli, "_watchdog_first_cycle_due", lambda root: None)
    monkeypatch.setattr(W, "desktop_notify", lambda *args, **kwargs: False)
    from ao import email, telegram
    monkeypatch.setattr(email, "send", lambda *args, **kwargs: False)
    monkeypatch.setattr(telegram, "send", lambda *args, **kwargs: 0)
    A.record_credit_sample(root, 100, 100, account="acct-a", adapter="kiro")
    assert cli._doctor_check(project, page=True) == 1
    row = storage.read_jsonl(str(Path(root, ".ao", "ledger", "notices.jsonl")))[-1]
    assert cli.cmd_notices(project, SimpleNamespace(n=12, all=True, ident=row["id"])) == 0
    assert "100/100" in capsys.readouterr().out
    assert row["evidence"]["check"] == "credit_reading"


def test_c7_mail_notice_keeps_evidence(project, monkeypatch):
    from ao import email, telegram
    monkeypatch.setattr(email, "send", lambda *args, **kwargs: True)
    monkeypatch.setattr(telegram, "send", lambda *args, **kwargs: 0)
    monkeypatch.setattr(W, "desktop_notify", lambda *args, **kwargs: False)
    ev = A.notice_evidence("credit_reading", [{"value": "100/100", "at": time.time(), "source": "account"}])
    W.notify("spent", "100/100", project["root"], key="credits-exhaust", level="red", audience="human", evidence=ev)
    rows = storage.read_jsonl(str(Path(project["root"], ".ao", "ledger", "notices.jsonl")))
    mail = next(row for row in rows if row["key"] == "mail:credits-exhaust")
    assert mail["evidence"] == ev
