import json
import os
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A, storage


def _dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _seed(project, tmp_path):
    root = project["root"]
    A.write_review_artefact(root, "semantic-review", "2026-09-01-100000-aaa.md", "VERDICT: APPROVED\n",
                            evidence={}, verdict="APPROVED")
    with open(os.path.join(root, "semantic-review", "2026-09-01-100000-aaa.md"), "a", encoding="utf-8") as fh:
        fh.write("edited afterwards\n")
    A.write_review_artefact(root, "semantic-review", "2026-09-01-100001-bbb.md", "VERDICT: APPROVED\n",
                            evidence={}, verdict="APPROVED")
    archive = os.path.join(A.HOME, ".ao", "archive", A.project_key(root), "semantic-review-20260902-000000")
    os.makedirs(archive)
    os.replace(os.path.join(root, "semantic-review", "2026-09-01-100001-bbb.md"),
               os.path.join(archive, "2026-09-01-100001-bbb.md"))
    A.record_authority(root, True, [], "sha256:t", "V-404", "C-1", review="2026-09-01-100002-gone.md")
    with open(os.path.join(root, ".ao", "board.md"), "a", encoding="utf-8") as fh:
        fh.write("- [B7] a slice · landed: deadbee\n")
    gone = str(tmp_path / "gone" / ".ao" / "ledger" / "authority.jsonl")
    with open(storage.checkpoint_path(), "w", encoding="utf-8") as fh:
        json.dump({gone: {"count": 1, "digest": "sha256:x", "at": 1}}, fh)
    os.makedirs(os.path.dirname(A.architect_lock_path(root)), exist_ok=True)
    with open(A.architect_lock_path(root), "w", encoding="utf-8") as fh:
        json.dump({"pid": _dead_pid(), "who": "a wake that died", "at": 1}, fh)
    return root, gone


def test_every_seeded_disagreement_is_found_with_both_sides(project, tmp_path):
    root, gone = _seed(project, tmp_path)

    findings = {finding["kind"]: finding for finding in A.consistency_findings(root, project)}

    assert set(findings) == {"grant-review", "grant-verification", "review-bytes", "archive-pointer",
                             "board-commit", "checkpoint", "architect-lock"}
    assert "grant C-1 rests on review 2026-09-01-100002-gone.md" in findings["grant-review"]["text"]
    assert "names verification V-404" in findings["grant-verification"]["text"]
    assert "B7 landed deadbee" in findings["board-commit"]["text"]
    assert gone in findings["checkpoint"]["text"]
    assert [kind for kind, finding in findings.items() if finding["repair"]] == [
        "archive-pointer", "checkpoint", "architect-lock"]


def test_a_repair_fixes_only_mechanical_state_records_itself_and_never_touches_authority(project, tmp_path, capsys):
    root, gone = _seed(project, tmp_path)
    authority = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    before = open(authority, "rb").read()

    assert cli.cmd_doctor(project, SimpleNamespace(check=False, consistency=True, repair=True)) == 1

    assert open(authority, "rb").read() == before
    left = {finding["kind"] for finding in A.consistency_findings(root, project)}
    assert left == {"grant-review", "grant-verification", "review-bytes", "board-commit"}
    assert gone not in json.load(open(storage.checkpoint_path(), encoding="utf-8"))
    assert not os.path.exists(A.architect_lock_path(root))
    assert "2026-09-01-100001-bbb.md" in A.archived_artefacts(root)
    repairs = storage.read_chained_jsonl(os.path.join(root, ".ao", "ledger", "repairs.jsonl"), A.REPAIR_CHAIN)
    assert sorted(row["kind"] for row in repairs) == ["architect-lock", "archive-pointer", "checkpoint"]
    assert "repaired" in capsys.readouterr().out
