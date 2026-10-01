import hashlib
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from ao import cli, lib as A


def _governance(root):
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-1")
    A.record_authority(root, False, ["refused"], "sha256:u", "V-2")
    with open(os.path.join(root, ".ao", "authority.md"), "w", encoding="utf-8") as fh:
        fh.write("# Authority\n\nThe implementer may commit locally.\n")
    with open(os.path.join(root, ".ao", "board.md"), "a", encoding="utf-8") as fh:
        fh.write("- [S1] a slice · landed: abc1234\n")
    A.ask(root, "which store keeps the ledger?", ["files", "sqlite"])
    with open(os.path.join(root, ".ao", "ledger", "authority.jsonl.lock"), "w") as fh:
        fh.write("")


def test_a_backup_restored_into_an_empty_checkout_validates_and_names_what_it_could_not_verify(project, tmp_path,
                                                                                               capsys):
    root = project["root"]
    _governance(root)

    assert cli.cmd_backup(project, SimpleNamespace(to=str(tmp_path / "backups"))) == 0

    [stamp] = os.listdir(tmp_path / "backups" / A.project_key(root))
    source = tmp_path / "backups" / A.project_key(root) / stamp
    manifest = json.loads((source / "manifest.json").read_text())
    assert ".ao/authority.md" in manifest["files"] and ".ao/ledger/authority.jsonl" in manifest["files"]
    assert not any(name.endswith(".lock") for name in manifest["files"])
    assert A.backup_age(root)[0] < 60

    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
    (source / ".ao" / "board.md").write_text("tampered\n", encoding="utf-8")
    cfg = dict(project, root=str(empty))

    assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source))) == 1

    out = A.re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)   # colour off, words kept
    assert "not verified  .ao/board.md: its bytes do not match the manifest" in out
    assert "the authority chain and the board validate" in out
    assert [row["granted"] for row in A.authority_rows(str(empty))] == [True, False]
    assert open(empty / ".ao" / "authority.md", encoding="utf-8").read().startswith("# Authority")


def test_a_manifest_path_that_leaves_the_checkout_is_restored_nowhere(tmp_path):
    """GOVERNANCE-BACKUP-2: the manifest is not authenticated, and `../outside.txt` in it was written beside the
    checkout."""
    checkout = tmp_path / "parent" / "checkout"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    source = tmp_path / "attacker" / "backup" / "snapshot"
    source.mkdir(parents=True)
    (source.parent / "outside.txt").write_bytes(b"owned")
    digest = "sha256:" + hashlib.sha256(b"owned").hexdigest()
    (source / "manifest.json").write_text(json.dumps({"schema": 1, "files": {"../outside.txt": digest}}),
                                          encoding="utf-8")

    restored, unverified = A.restore_backup(str(checkout), str(source))

    assert restored == [] and unverified == ["../outside.txt: not a path inside the checkout"]
    assert not (tmp_path / "parent" / "outside.txt").exists()


def test_a_backup_that_restores_no_control_plane_fails(project, tmp_path, capsys):
    """GOVERNANCE-BACKUP-2: an empty manifest restored nothing, exited 0 and said the state validated."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_text(json.dumps({"schema": 1, "files": {}}), encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=empty, check=True)

    assert cli.cmd_restore(dict(project, root=str(empty)), SimpleNamespace(source=str(source))) == 1

    out = capsys.readouterr().out
    assert "restored no control plane" in out and "validate" not in out


def test_an_unreadable_authority_chain_keeps_every_review_artefact(project, capsys, tmp_path):
    """GOVERNANCE-BACKUP-2: a chain that could not be read left out the artefacts its grants rest on, and the
    backup said nothing."""
    root = project["root"]
    reviews = project.get("reviews", "semantic-review")
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-1", review="grant.md")
    os.makedirs(os.path.join(root, reviews), exist_ok=True)
    with open(os.path.join(root, reviews, "grant.md"), "w", encoding="utf-8") as fh:
        fh.write("VERDICT: APPROVED\n")
    assert f"{reviews}/grant.md" in A.governance_files(root, project)
    ledger = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    with open(ledger, encoding="utf-8") as fh:
        rows = fh.read().splitlines()
    first = json.loads(rows[0])
    first["reasons"] = ["edited after the fact"]
    with open(ledger, "w", encoding="utf-8") as fh:
        fh.write("\n".join([json.dumps(first)] + rows[1:]) + "\n")
    with pytest.raises(Exception):
        A.authority_rows(root)

    assert f"{reviews}/grant.md" in A.governance_files(root, project)
    assert cli.cmd_backup(project, SimpleNamespace(to=str(tmp_path / "backups"))) == 0
    assert "the authority chain does not validate" in capsys.readouterr().out


def test_a_ref_backup_touches_neither_the_index_nor_the_worktree(project):
    root = project["root"]
    _governance(root)
    before = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout

    manifest = A.write_backup(root, project, "ref")

    assert manifest["where"].startswith("refs/ao/backup/latest ")
    listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", "refs/ao/backup/latest"], cwd=root,
                            capture_output=True, text=True, check=True).stdout.split()
    assert ".ao/ledger/authority.jsonl" in listed and "ao-backup-manifest.json" in listed
    assert subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout == before


def test_a_remote_the_host_does_not_confirm_private_is_refused(project, monkeypatch):
    root = project["root"]
    subprocess.run(["git", "remote", "add", "governance", "https://github.com/example/public-product.git"], cwd=root,
                   check=True)
    monkeypatch.setattr(A, "remote_is_private", lambda root, remote: False)

    with pytest.raises(RuntimeError, match="did not confirm it is private"):
        A.write_backup(root, project, "remote:governance")
