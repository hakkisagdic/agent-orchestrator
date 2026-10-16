"""Staging requires passed verification and stalls name only their own slice (TRIAGE-1-MEDIUM)."""
import time
from ao import lib as A
from tests import test_round_ledger as rounds
from tests import test_throughput as throughput


def test_c10_failed_verification_is_not_staged(project):
    candidate = throughput._stage(project["root"], "value.py", "value = 1\n")
    A.record_verification(project["root"], {"id": "V-failed", "at": time.time() - 7200,
        "candidate": candidate, "candidate_ready": True, "passed": False})
    result = A.throughput(project["root"], project)
    assert result["staged"] == 0 and result["stall"] is None


def test_c10_unrelated_decision_does_not_explain_stall(project, monkeypatch):
    candidate = throughput._stage(project["root"], "value.py", "value = 1\n")
    rounds._running(project["root"], "S1")
    throughput._verified(project["root"], candidate, minutes_ago=95)
    monkeypatch.setattr(A, "decisions", lambda root: [{"id": "D-other", "state": "open", "slice": "S2",
                                                     "asked_at": time.time() - 3600, "question": "other work"}])
    reason = A.throughput(project["root"], project)["stall"]["reason"]
    assert "D-other" not in reason and "verified" in reason
