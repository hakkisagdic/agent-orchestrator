"""Archive bytes and repair intent remain verifiable across lock changes (TRIAGE-1-MEDIUM)."""
import os
from pathlib import Path
import pytest
from ao import lib as A, storage
from tests import test_consistency as consistency


@pytest.mark.parametrize("edited", [False, True])
def test_c4_archive_pointer_does_not_vouch_for_bytes(project, tmp_path, edited):
    root = project["root"]
    name = "2026-09-01-100000-review.md"
    A.write_review_artefact(root, project["reviews"], name, "VERDICT: APPROVED\n",
                            evidence={}, verdict="APPROVED")
    archive = tmp_path / "archive"
    archive.mkdir()
    path = Path(root, project["reviews"], name)
    if edited:
        path.replace(archive / name)
        (archive / name).write_text("changed\n", encoding="utf-8")
    else:
        path.unlink()
    A.record_archived(root, name, str(archive))
    A.record_authority(root, True, [], "sha256:t", None, "C-1", review=name)
    kinds = {row["kind"] for row in A.consistency_findings(root, project)}
    assert ("review-bytes" if edited else "review-missing") in kinds
    if not edited:
        assert "grant-review" in kinds


def test_c4_repair_keeps_a_retaken_lock(project, tmp_path):
    root, _ = consistency._seed(project, tmp_path)
    findings = A.consistency_findings(root, project)
    A.acquire_architect(root, os.getpid(), "new holder")
    before = Path(A.architect_lock_path(root)).read_bytes()
    A.repair_consistency(root, findings)
    assert Path(A.architect_lock_path(root)).read_bytes() == before


def test_c4_corrupt_repair_history_prevents_every_action(project, tmp_path):
    root, _ = consistency._seed(project, tmp_path)
    findings = A.consistency_findings(root, project)
    paths = [Path(A.architect_lock_path(root)), Path(storage.checkpoint_path())]
    before = [path.read_bytes() for path in paths]
    Path(root, ".ao", "ledger", "repairs.jsonl").write_text("broken\n", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        A.repair_consistency(root, findings)
    assert [path.read_bytes() for path in paths] == before
    assert not A.archived_artefacts(root)


def test_c4_repair_records_intent_before_action(project, monkeypatch):
    root = project["root"]

    def action(*args):
        rows = storage.read_chained_jsonl(str(Path(root, ".ao", "ledger", "repairs.jsonl")), A.REPAIR_CHAIN)
        assert rows and rows[-1]["event"] == "intent"
        raise OSError("interrupted repair")

    monkeypatch.setattr(A, "record_archived", action)
    with pytest.raises(OSError, match="interrupted repair"):
        A.repair_consistency(root, [{"repair": ("archive-pointer", "r.md", "archive"), "text": "missing pointer"}])


@pytest.mark.parametrize("operation", ["acquire", "release", "repair"])
def test_c4_remove_takes_the_architect_guard(tmp_path, monkeypatch, operation):
    import json
    from ao import cli
    from tests import test_safe_remove as remove

    home = remove._home(tmp_path, monkeypatch)
    root = remove._project(tmp_path / "work", "guarded")
    remove._launchd(monkeypatch)
    lock = Path(A.architect_lock_path(root))
    guard = Path(str(lock) + ".guard")
    neighbour = home / ".ao" / "architect-other.lock.guard"
    neighbour.write_text("another project's guard", encoding="utf-8")
    if operation == "acquire":
        assert A.acquire_architect(root, os.getpid(), "test")
    elif operation == "release":
        A.release_architect(root)
    else:
        holder = {"pid": 123, "who": "dead holder", "at": 1}
        lock.write_text(json.dumps(holder), encoding="utf-8")
        monkeypatch.setattr(A, "_pid_alive", lambda pid: False)
        A.repair_consistency(root, [{"repair": ("architect-lock", str(lock), holder), "text": "dead holder"}])
    assert guard.exists()
    assert cli.cmd_remove({"root": root, "reviews": "semantic-review"}, remove.REMOVE) == 0
    assert not guard.exists()
    assert neighbour.read_text(encoding="utf-8") == "another project's guard"
