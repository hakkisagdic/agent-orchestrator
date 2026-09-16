import json
import os

from ao import lib as A, mcp
from tests.scenarios import World


def _verification(root, passed, gates):
    A.record_verification(root, {"id": "V-100", "at": "2026-09-16T12:00:00", "schema": 2, "passed": passed,
                                 "gates": gates})


FAILED = [{"name": "tests", "passed": False, "exit": 1, "summary": "3 failed, 156 passed in 12.30s"}]
GREEN = [{"name": "tests", "passed": True, "exit": 0, "summary": "159 passed in 12.30s"}]


def test_a_closing_line_is_kept_as_the_runner_wrote_it():
    output = "collected 159 items\n...\n=========== 3 failed, 156 passed in 12.30s ===========\n"
    assert A.gate_summary_line(output) == "3 failed, 156 passed in 12.30s"
    assert A.gate_summary_line("ok\n# pass 12\n# fail 0\n") == "# fail 0"
    assert A.gate_summary_line("Tests: 4 ok, 0 broken\n", r"Tests: (?P<pass>\d+) ok") == "Tests: 4 ok, 0 broken"
    assert A.gate_summary_line("nothing to see\n") is None


def test_a_green_claim_over_a_failed_verification_is_inconsistent(project):
    root = project["root"]
    assert A.report_inconsistency(root, "159 passed, ready to land") is None          # no verification yet

    _verification(root, False, FAILED)
    reason = A.report_inconsistency(root, "S1 done: 159 passed, ready to land")
    assert "V-100 failed" in reason and "tests exit 1" in reason
    assert A.report_inconsistency(root, "S1: 3 failed, fixing") is None               # the report admits it
    assert A.report_inconsistency(root, "queue empty, waiting for a decision") is None


def test_a_green_claim_over_a_passed_verification_is_consistent(project):
    root = project["root"]
    _verification(root, True, GREEN)

    assert A.report_inconsistency(root, "all tests pass") is None


def test_ao_report_quotes_the_ledger_and_marks_the_contradiction(project):
    root = project["root"]
    _verification(root, False, FAILED)

    result = mcp.call("ao_report", {"kind": "done", "summary": "S1 done, 159 passed"}, project, False)

    text = open(os.path.join(root, project["mailbox"], result["written"]), encoding="utf-8").read()
    assert "## INCONSISTENT" in text and result["inconsistent"]
    assert "**Verification:** V-100 FAILED: tests exit 1 (3 failed, 156 passed in 12.30s)" in text


def test_the_watchdog_raises_an_inconsistent_report(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    _verification(project["root"], False, FAILED)
    world.mail("20260916-1200-kiro-to-fable-DONE-s1.md", "# S1 done\n\n## DONE\n\n159 passed\n")

    found = A.anomalies(project["root"], project, {}, 900, 360)

    assert any(a["kind"] == "inconsistent-report" and "V-100 failed" in a["facts"][0] for a in found)
