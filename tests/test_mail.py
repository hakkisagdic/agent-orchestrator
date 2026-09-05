import json
import os
import time

from ao import lib as A
from ao import mcp


def test_write_mail_adds_envelope_and_ledger(project):
    root = project["root"]
    name = A.write_mail(root, project, "20260905-1800-kiro-to-fable-BLOCKED-x.md", "# x\n\nbody\n",
                        {"kind": "blocked", "from": "kiro", "to": "fable", "slice": "B7"})
    meta = A.mail_meta(os.path.join(root, "agent-mail", name))
    assert meta["kind"] == "blocked" and meta["slice"] == "B7" and meta["id"] == name
    rows = A.mail_log(root)
    assert rows[-1]["event"] == "written" and rows[-1]["summary"] == "x"
    body = open(os.path.join(root, "agent-mail", name), encoding="utf-8").read()
    assert body.startswith("---\nao: 1\n") and "# x" in body


def test_consumed_mail_is_reconciled(project):
    root = project["root"]
    name = A.write_mail(root, project, "20260905-1800-fable-to-kiro-DECISION-x.md", "# d\n", {"kind": "decision"})
    os.remove(os.path.join(root, "agent-mail", name))
    A.reconcile_mail_ledger(root, project)
    assert A.mail_log(root)[-1]["event"] == "consumed"
    assert A.mail_search(root, "d")[0]["id"] == name


def test_repeated_report_folds_into_one_file(project):
    root = project["root"]
    r1 = mcp.call("ao_report", {"kind": "blocked", "summary": "queue empty", "needs": "next slice"}, project, False)
    first = os.path.join(root, "agent-mail", r1["written"])
    os.utime(first, (1_700_000_000, 1_700_000_000))
    r2 = mcp.call("ao_report", {"kind": "blocked", "summary": "queue empty", "needs": "next slice"}, project, False)
    assert r2["written"] == r1["written"] and r2["repeated"] == 2
    assert len(os.listdir(os.path.join(root, "agent-mail"))) == 1
    assert os.path.getmtime(first) == 1_700_000_000           # age preserved
    assert "Tekrar: 2" in open(first, encoding="utf-8").read()


def test_anomalies_group_reports_by_kind(project, monkeypatch):
    root = project["root"]
    for i in range(5):
        open(os.path.join(root, "agent-mail", f"20260905-06{i}1-kiro-to-fable-BLOCKED-q.md"), "w", encoding="utf-8").write(
            "# queue empty\n\n## KARAR GEREKLİ\n")
    open(os.path.join(root, "agent-mail", "20260905-0621-kiro-to-fable-DONE-b6.md"), "w", encoding="utf-8").write(
        "# b6 done\n\nBlockers: none\n")
    monkeypatch.setattr(A, "agent_pids", lambda *a, **k: [])
    out = A.anomalies(root, project, {}, 0, 60)
    kinds = {a["kind"]: a for a in out if a["kind"] in ("decision-requested", "report-waiting")}
    assert set(kinds) == {"decision-requested", "report-waiting"}
    assert "5 report(s)" in kinds["decision-requested"]["facts"][0]
    assert kinds["decision-requested"]["key"] == "implementer"


def test_write_report_keys_on_condition_not_file(project):
    root = project["root"]
    n1 = A.write_report(root, project, "decision-requested", ["the implementer wrote a.md"], key="implementer")
    n2 = A.write_report(root, project, "decision-requested", ["the implementer wrote b.md"], key="implementer")
    assert n1 == "watchdog-to-fable-ANOMALY-decision-requested-implementer.md" and n2 is None


def test_configured_names_are_honoured(project):
    cfg = dict(project)
    cfg["implementer"] = dict(cfg["implementer"], name="dev")
    cfg["architect"] = dict(cfg["architect"], name="lead")
    root = cfg["root"]
    r = mcp.call("ao_report", {"kind": "status", "summary": "hi"}, cfg, False)
    assert "-dev-to-lead-STATUS-" in r["written"]
    assert A.implementer_inbox(root, cfg) == []
    open(os.path.join(root, "agent-mail", "20260905-1800-lead-to-dev-DECISION-x.md"), "w", encoding="utf-8").write("# x\n")
    assert len(A.implementer_inbox(root, cfg)) == 1
