import os
import time
from types import SimpleNamespace

from ao import cli, lib as A
from ao.storage import append_chained_jsonl


def _review(root, name, verdict, slice_id, digest="sha256:c1", kind="index-candidate", at=None):
    evidence = {"kind": kind, "candidate": {"digest": digest}, "slice": slice_id, "authorizable": kind == "index-candidate"}
    A.record_review(root, name, f"VERDICT: {verdict}\n".encode(), evidence, verdict)


def test_a_respecified_slice_keeps_its_history_and_the_outcome_is_read_from_the_ledgers(project, capsys):
    root = project["root"]
    with open(os.path.join(root, ".ao", "board.md"), "a", encoding="utf-8") as fh:
        fh.write("- [S1] the store slice · since: 2026-09-15 09:00\n- [F1] fix what S1 landed · fixes: S1\n")
    _review(root, "r1.md", "NEEDS_CHANGES", "S1")
    append_chained_jsonl(A.decisions_path(root), {"id": "AD-1", "at": int(time.time()), "decision": "narrow it",
                                                  "scope": "S1"}, A.DECISION_CHAIN, legacy_prefix=True)
    _review(root, "r2.md", "NEEDS_CHANGES", "S1")
    _review(root, "r3.md", "APPROVED", "S1")
    size = {"paths": 2, "kinds": {kind: {"paths": 0, "added": 0, "deleted": 0}
                                  for kind in ("product", "tests", "fixtures", "generated", "deletion")}}
    size["kinds"]["product"] = {"paths": 1, "added": 120, "deleted": 30}
    A.record_verification(root, {"id": "V-1", "at": 1, "passed": True, "candidate": {"digest": "sha256:c1"},
                                 "candidate_size": size})
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-1", review="r3.md",
                       candidate={"digest": "sha256:c1"}, scope={})

    [outcome] = A.slice_outcomes(root)

    assert outcome["verdicts"] == ["NEEDS_CHANGES", "NEEDS_CHANGES", "APPROVED"] and outcome["rounds"] == 3
    assert outcome["first_pass"] is False and outcome["defect_found"] is True
    assert outcome["size"]["kinds"]["product"]["added"] == 120 and outcome["hours"] > 0
    stats = A.outcome_stats([outcome])
    assert stats["rounds"]["median"] == 3 and stats["product_lines"]["median"] == 150 and stats["defects_pct"] == 100

    assert cli.cmd_stats(project, SimpleNamespace(all=False, since=None, until=None, slices=True)) == 0
    out = capsys.readouterr().out
    assert "1 slices landed" in out and "NEEDS_CHANGES → NEEDS_CHANGES → APPROVED" in out


def test_a_waived_slice_counts_as_landed_without_rounds(project):
    root = project["root"]
    waiver = A.waive(root, "review", "S2", "the reviewer is out of credits", "a person")
    A.record_authority(root, True, [], "sha256:t", "V-2", "C-2", candidate={"digest": "sha256:c2"}, scope={},
                       waiver=waiver["id"])

    [outcome] = A.slice_outcomes(root)

    assert outcome["slice"] == "S2" and outcome["waived"] is True and outcome["rounds"] == 0
    assert A.outcome_stats([outcome])["rounds"] is None
