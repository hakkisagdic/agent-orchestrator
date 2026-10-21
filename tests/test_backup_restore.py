import hashlib
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, lib as A


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


def test_a_backup_restored_into_an_empty_checkout_validates_and_names_what_it_could_not_verify(
        project, tmp_path, capsys, monkeypatch):
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
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source), by="Ada", ask=True)) == 1

    out = A.re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)   # colour off, words kept
    assert "not verified  .ao/board.md: its bytes do not match the manifest" in out
    assert "the authority chain and the board validate" in out
    assert [row["granted"] for row in A.authority_rows(str(empty))] == [True, False]
    assert open(empty / ".ao" / "authority.md", encoding="utf-8").read().startswith("# Authority")


def test_a_backup_restored_without_by_or_ask_leaves_ledgers_unattested_and_names_how_to_attest_them(
        project, tmp_path, capsys, monkeypatch):
    root = project["root"]
    _governance(root)
    assert cli.cmd_backup(project, SimpleNamespace(to=str(tmp_path / "backups"))) == 0
    [stamp] = os.listdir(tmp_path / "backups" / A.project_key(root))
    source = tmp_path / "backups" / A.project_key(root) / stamp

    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
    cfg = dict(project, root=str(empty))
    capsys.readouterr()

    assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source))) == 0
    out = A.re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert ".ao/ledger/authority.jsonl" in out and "unattested" in out
    assert f"ao restore {source} --by <name> --ask" in out
    assert A.authority_rows(str(empty)) == []
    problems = dict(cli.doctor_problems(cfg))
    assert "authority-unattested" in problems
    assert "ao restore" in problems["authority-unattested"] and "--by <name> --ask" in problems["authority-unattested"]

    # Without --ask, or without a terminal, or on a no, it restores the files and attests nothing.
    assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source), by="Ada")) == 0
    assert A.authority_rows(str(empty)) == []
    assert A.latest_restore_attestation(str(empty)) is None

    monkeypatch.setattr(cli, "_confirm", lambda prompt: None)
    assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source), by="Ada", ask=True)) == 0
    assert A.authority_rows(str(empty)) == []
    assert A.latest_restore_attestation(str(empty)) is None

    monkeypatch.setattr(cli, "_confirm", lambda prompt: False)
    assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source), by="Ada", ask=True)) == 0
    assert A.authority_rows(str(empty)) == []
    assert A.latest_restore_attestation(str(empty)) is None
    capsys.readouterr()

    prompts = []
    monkeypatch.setattr(cli, "_confirm", lambda prompt: (prompts.append(prompt), True)[1])
    assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source), by="Ada", ask=True)) == 0
    out = A.re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    manifest_digest = A.backup_manifest_digest(str(source))
    assert manifest_digest in out
    assert ".ao/ledger/authority.jsonl: 2 row(s), last sha256:" in out
    assert prompts and "Ada" in prompts[0]
    assert [row["granted"] for row in A.authority_rows(str(empty))] == [True, False]
    assert "authority-unattested" not in dict(cli.doctor_problems(cfg))
    recorded = A.latest_restore_attestation(str(empty))
    assert recorded is not None
    assert recorded["by"] == "Ada" and isinstance(recorded["user"], str) and isinstance(recorded["interactive"], bool)
    assert recorded["manifest"] == manifest_digest
    assert recorded["ledgers"] == {".ao/ledger/authority.jsonl": 2}
    backup_report = "\n".join(A.re.sub(r"\x1b\[[0-9;]*m", "", line) for line in cli._backup_lines(cfg))
    assert "restore" in backup_report and "Ada" in backup_report and manifest_digest[:19] in backup_report


def test_a_backup_restore_with_by_or_ask_is_refused_to_an_agent(project, tmp_path, capsys):
    root = project["root"]
    _governance(root)
    assert cli.cmd_backup(project, SimpleNamespace(to=str(tmp_path / "backups"))) == 0
    [stamp] = os.listdir(tmp_path / "backups" / A.project_key(root))
    source = tmp_path / "backups" / A.project_key(root) / stamp

    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
    cfg = dict(project, root=str(empty))
    capsys.readouterr()

    assert ("attests a restored backup", "ao restore s --by p") in AL.FORBIDDEN
    assert ("attests a restored backup", "ao restore s --by p --ask") in AL.FORBIDDEN
    for rule in (
        "Bash(ao restore s --by Alice)",
        "Bash(ao restore s --by Alice --ask)",
        "Bash(ao restore s --ask --by Alice)",
        "Bash(ao restore s --by=Alice --ask)",
        "Bash(ao restore s --b Alice --a)",
        "Bash(ao restore s --b=Alice --a)",
        "Bash(ao restore s --ask)",
    ):
        problems = AL.problems(["claude", "-p", "{prompt}", "--allowedTools", rule], {})
        assert any(kind == "attests a restored backup" for kind, _, _ in problems), rule
    assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source), ask=True)) == 2
    assert "--by is required" in capsys.readouterr().out
    for agent in ("implementer", "architect", "fable"):
        assert cli.cmd_restore(cfg, SimpleNamespace(source=str(source), by=agent, ask=True)) == 2
        assert "--by names an agent or a role" in capsys.readouterr().out
    assert not (empty / ".ao").exists()


def test_forged_backup_whose_manifest_matches_forged_rows_attests_nothing_unless_a_person_answers_yes(
        project, tmp_path, capsys, monkeypatch):
    from ao import storage
    root = project["root"]
    _governance(root)
    w = A.waive(root, "review", "B7", "reason", by="Ada")
    A.close_waiver(root, w["id"], "done")
    assert A.open_waivers(root) == []

    # The implementer crafts a backup directory whose manifest digests match a forged opt-in row and a truncated
    # waiver ledger that drops the `closed` row.
    forged_dir = tmp_path / "forged-backup"
    (forged_dir / ".ao" / "ledger").mkdir(parents=True)
    for rel in (".ao/config.json", ".ao/board.md", ".ao/authority.md"):
        target = forged_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(( tmp_path / ".." / root / rel ).resolve().read_bytes())
    opt_row = {"previous": None, "ordinal": 1, "at": 1, "setting": "review.same_family", "value": "labeled",
               "by": "Ada", "user": "ada", "interactive": True}
    (forged_dir / ".ao" / "ledger" / "opt-ins.jsonl").write_bytes(storage._encode_jsonl(opt_row))
    waiver_first = open(os.path.join(root, ".ao", "ledger", "waivers.jsonl"), "rb").read().splitlines(keepends=True)[0]
    (forged_dir / ".ao" / "ledger" / "waivers.jsonl").write_bytes(waiver_first)
    files = {}
    for rel in (".ao/config.json", ".ao/board.md", ".ao/authority.md",
                ".ao/ledger/opt-ins.jsonl", ".ao/ledger/waivers.jsonl"):
        data = (forged_dir / rel).read_bytes()
        files[rel] = "sha256:" + hashlib.sha256(data).hexdigest()
    (forged_dir / "manifest.json").write_text(
        json.dumps({"schema": 1, "project": A.project_key(root), "at": 1, "head": None, "files": files}),
        encoding="utf-8",
    )

    # Running `ao restore <dir> --by Ada` without --ask, or with --ask when no terminal is attached, attests nothing:
    # the forged opt-in does not count, and the rolled-back waiver ledger is refused as shrunk rather than reopening.
    monkeypatch.setattr(cli, "_confirm", lambda prompt: None)
    assert cli.cmd_restore(project, SimpleNamespace(source=str(forged_dir), by="Ada")) == 0
    assert cli.cmd_restore(project, SimpleNamespace(source=str(forged_dir), by="Ada", ask=True)) == 0
    assert A.recorded_opt_in(root, "review.same_family") is None
    with pytest.raises(storage.LedgerCorruption, match="rows were removed from its end"):
        A.open_waivers(root)


def test_restoring_an_unsealed_backup_over_a_sealed_ledger_clears_the_stale_seal_checkpoint(
        project, tmp_path, monkeypatch):
    from ao import storage
    root = project["root"]
    _governance(root)
    assert cli.cmd_backup(project, SimpleNamespace(to=str(tmp_path / "backups"))) == 0
    [stamp] = os.listdir(tmp_path / "backups" / A.project_key(root))
    source = tmp_path / "backups" / A.project_key(root) / stamp

    A.record_authority(root, True, [], "sha256:v", "V-3", "C-3")
    auth_path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    storage.seal_chained_jsonl(auth_path, A.AUTHORITY_CHAIN, keep=1)
    assert "sealed" in storage._checkpoint_entry(auth_path)
    os.remove(storage.seal_path(auth_path))

    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)
    assert cli.cmd_restore(project, SimpleNamespace(source=str(source), by="Ada", ask=True)) == 0
    assert "sealed" not in storage._checkpoint_entry(auth_path)
    assert [row["granted"] for row in A.authority_rows(root)] == [True, False]
    assert [row["granted"] for row in A.authority_history(root)] == [True, False]



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
