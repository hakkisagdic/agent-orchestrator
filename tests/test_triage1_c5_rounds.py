"""Recorded verdicts order rounds and corrupt history cannot crash work checks (TRIAGE-1-MEDIUM)."""
import json
import os
import time
from pathlib import Path
import pytest
from ao import lib as A, storage, watchdog as W
from tests import test_criteria_verdicts as criteria
from tests import test_round_ledger as rounds
from tests.test_round_ledger import _review, _running


def test_c5_unrecorded_approval_cannot_erase_rounds(project):
    root = project["root"]
    rounds._running(root)
    for _ in range(5):
        rounds._review(root, "NEEDS_CHANGES")
    ev = {"kind": "index-candidate", "slice": "R1"}
    Path(root, project["reviews"], "2099-01-01-000000-forged.md").write_text(
        "\n".join(criteria.APPROVED) + "\n" + A.review_evidence_line(ev), encoding="utf-8")
    future = time.time() + 60
    os.utime(Path(root, project["reviews"], "2099-01-01-000000-forged.md"), (future, future))
    assert A.rounds(root, project["reviews"]) == 5


def test_c5_ledger_order_survives_clock_step_back(project, monkeypatch):
    root = project["root"]
    rounds._running(root)
    now = time.time()
    monkeypatch.setattr(A.time, "time", lambda: now)
    rounds._review(root, "NEEDS_CHANGES")
    monkeypatch.setattr(A.time, "time", lambda: now - 60)
    rounds._review(root, "APPROVED")
    assert A.rounds(root, project["reviews"]) == 0


def test_c5_new_unavailable_files_do_not_hide_findings(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "_git_text", lambda *args: "0")
    rounds._review(root, "NEEDS_CHANGES")
    for _ in range(8):
        rounds._review(root, "UNAVAILABLE")
    assert "open review findings" in W.open_work(project, root)


@pytest.mark.parametrize("damage", ["malformed", "chain", "tail"])
def test_c5_open_work_survives_corrupt_review_history(project, damage):
    root = project["root"]
    _running(root)
    _review(root, "NEEDS_CHANGES")
    path = Path(A.review_ledger_path(root))
    data = path.read_bytes()
    if damage == "malformed":
        data = b"not-json\n"
    elif damage == "chain":
        row = json.loads(data)
        row["verdict"] = "APPROVED"
        data = (json.dumps(row) + "\n").encode("utf-8")
    else:
        data = data[:-10]
    path.write_bytes(data)
    with pytest.raises(storage.LedgerCorruption):
        storage.read_chained_jsonl(str(path), A.REVIEW_CHAIN)
    reasons = W.open_work(project, root)
    assert "slice running" in reasons and "open review findings" not in reasons
