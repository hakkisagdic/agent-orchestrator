import json
import os
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W


def test_a_projection_notice_cannot_be_written_without_its_samples():
    with pytest.raises(ValueError):
        A.notice_evidence("burn_rate", [])
    assert A.notice_evidence("credit_reading", [])["samples"] == []


def test_the_credit_projection_rings_with_the_readings_it_projected_from(project, monkeypatch):
    root = project["root"]
    now = time.time()
    A.record_credit_sample(root, 1000, 10_000, reset_at=now + 30 * 86400, account="acct-a", at=now - 5 * 3600)
    monkeypatch.setattr(A, "account_usage", lambda timeout=20: {
        "used": 5000, "limit": 10_000, "reset_at": now + 30 * 86400, "account": "acct-a"})
    rung = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: rung.append((title, kw)))

    W._sample_credits(root, {}, {"billing": {"api": {"target": "GetUsageLimits"}}}, "proj", now=now)

    (title, kw), = rung
    evidence = kw["evidence"]
    assert "credits run out" in title and evidence["check"] == "burn_rate"
    assert [sample["value"] for sample in evidence["samples"]] == ["1000/10000", "5000/10000"]
    assert all("acct-a" in sample["source"] for sample in evidence["samples"])


def test_ao_notices_with_an_id_prints_what_the_notice_was_raised_on(project, monkeypatch, capsys):
    root = project["root"]
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: False)
    from ao import telegram
    monkeypatch.setattr(telegram, "send", lambda *args, **kwargs: 0)
    evidence = A.notice_evidence("burn_rate", [{"value": "1000/10000", "source": "GetUsageLimits", "at": time.time() - 600},
                                               {"value": "5000/10000", "source": "GetUsageLimits", "at": time.time()}])
    W.notify("proj: credits run out 20 Sep", "5000/10000 at 19200/day", root, key="credits-exhaust",
             window=0, audience="human", evidence=evidence)

    row = json.loads(open(os.path.join(root, ".ao", "ledger", "notices.jsonl"), encoding="utf-8")
                     .read().splitlines()[-1])
    assert row["id"].startswith("N-") and row["evidence"] == evidence

    capsys.readouterr()
    assert cli.cmd_notices(project, SimpleNamespace(n=12, all=True, ident=row["id"])) == 0
    out = capsys.readouterr().out
    assert "check: burn_rate" in out and "1000/10000  from GetUsageLimits, 10m ago" in out
    assert cli.cmd_notices(project, SimpleNamespace(n=12, all=True, ident="N-0")) == 1


def test_the_alarm_ladder_keeps_the_evidence_it_escalates_on(project):
    evidence = A.notice_evidence("credit_reading", [{"value": "12503/10000", "source": "GetUsageLimits",
                                                      "at": time.time()}])

    A.alarm_touch("proj", "credits-exhaust", "red", evidence=evidence)

    (episode,) = A.active_alarms("proj")
    assert episode["evidence"] == evidence
