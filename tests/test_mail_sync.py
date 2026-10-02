import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from ao import cli, lib as A


def _store(project, sync_repo):
    root = project["root"]
    stored = {key: value for key, value in project.items() if key != "root"}
    stored["mail"] = {"store": "append-only", "sync_repo": sync_repo}
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    cfg = A.load_config(root)
    token = "gh" + "p_" + "B" * 36
    # Written by hand, as an agent writes its mail: write_mail redacts, and the sync's own scan went untested
    # (MAIL-SYNC-2).
    os.makedirs(os.path.join(root, "agent-mail"), exist_ok=True)
    with open(os.path.join(root, "agent-mail", "20260916-0900-fable-to-kiro-NOTE-a.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(f"# a\n\nkeep {token} out\n")
    A.reconcile_mail_ledger(root, cfg)
    return root, cfg


def _bare(tmp_path):
    bare = tmp_path / "all-mail.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    return bare


def test_the_store_syncs_scanned_to_a_private_repository_under_its_project_ref(project, tmp_path):
    bare = tmp_path / "all-mail.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    root, cfg = _store(project, str(bare))

    commit, where = A.sync_mail(root, cfg)

    ref = f"refs/mail/{A.project_key(root)}"
    assert where == f"{bare} {ref}"
    assert subprocess.run(["git", "--git-dir", str(bare), "rev-parse", ref], capture_output=True, text=True,
                          check=True).stdout.strip() == commit
    body = subprocess.run(["git", "--git-dir", str(bare), "show", f"{ref}:.ao/mail/store/20260916-0900-fable-to-kiro-NOTE-a.md"],
                          capture_output=True, text=True, check=True).stdout
    assert "[redacted:" in body and "gh" + "p_" not in body
    assert A.mail_sync_state(root, cfg) == (commit, commit, None)
    assert "refs/ao/mail" not in subprocess.run(["git", "for-each-ref", "refs/heads"], cwd=root, capture_output=True,
                                                text=True).stdout


def test_a_host_named_without_a_user_or_with_an_uppercase_scheme_is_asked_not_taken_for_a_directory(
        project, tmp_path, monkeypatch):
    """MAIL-SYNC-2: `github.com:owner/repo.git`, an SSH alias and `HTTPS://...` were taken for directories on this
    machine, and mail was pushed with no host asked."""
    import shutil
    root = project["root"]
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)                  # no gh: no host can be asked

    for url in ("github.com:example/team-mail.git", "HTTPS://github.com/example/team-mail.git",
                "gh-work:example/team-mail.git", "git@github.com:example/team-mail.git"):
        assert A._url_is_private(root, url) is None, url
    assert A._url_is_private(root, str(tmp_path / "all-mail.git")) is True


def test_the_products_own_remote_is_refused_in_another_of_its_forms(project, monkeypatch):
    root, cfg = _store(project, "git@github.com:Example/Product.git")
    monkeypatch.setattr(A, "_url_is_private", lambda root, url: True)
    real = A._git_output
    # Nothing in this test may reach a host: a refusal missing would otherwise push to it.
    monkeypatch.setattr(A, "_git_output", lambda root, *args, **kw: pytest.fail("pushed") if "push" in args
                        else real(root, *args, **kw))
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/product.git"], cwd=root, check=True)

    with pytest.raises(RuntimeError, match="this product's own remote"):
        A.sync_mail(root, cfg)


def test_a_compacted_body_is_scanned_before_it_leaves(project, tmp_path):
    """MAIL-SYNC-2: the scan passed over .gz archives, and a compacted body took its credential to the copy."""
    import gzip
    import time
    root, cfg = _store(project, str(_bare(tmp_path)))
    name = "20260916-0900-fable-to-kiro-NOTE-a.md"
    assert name in A.compact_messages(root, 0, now=time.time() + 10)

    A.sync_mail(root, cfg)

    blob = subprocess.run(["git", "--git-dir", str(tmp_path / "all-mail.git"), "show",
                           f"refs/mail/{A.project_key(root)}:.ao/mail/archive/{name}.gz"],
                          capture_output=True, check=True).stdout
    assert b"gh" + b"p_" not in gzip.decompress(blob) and b"[redacted:" in gzip.decompress(blob)


def test_mail_stored_after_a_sync_or_never_synced_is_reported_ahead_of_its_copy(project, tmp_path, monkeypatch):
    """MAIL-SYNC-2: only `ao mail sync` moves the ref the doctor compared, so mail stored since read as synced."""
    root, cfg = _store(project, str(_bare(tmp_path)))
    local, remote, problem = A.mail_sync_state(root, cfg)
    assert local == "unsynced" and remote is None and problem is None                    # never synced
    commit, _ = A.sync_mail(root, cfg)
    assert A.mail_sync_state(root, cfg) == (commit, commit, None)

    A.write_mail(root, cfg, "20260916-0901-fable-to-kiro-NOTE-b.md", "# b\n", {"kind": "note", "from": "fable",
                                                                                "to": "kiro"})

    local, remote, problem = A.mail_sync_state(root, cfg)
    assert remote == commit and problem is None and local != remote


def test_a_target_that_cannot_be_read_is_named_not_reported_behind(project, tmp_path):
    bare = _bare(tmp_path)
    root, cfg = _store(project, str(bare))
    A.sync_mail(root, cfg)
    os.rename(bare, tmp_path / "moved.git")

    local, remote, problem = A.mail_sync_state(root, cfg)

    assert remote is None and problem and str(bare) in problem and "cannot be read" in problem


def test_a_public_or_unverified_target_and_the_products_own_remote_are_refused(project, monkeypatch):
    root, cfg = _store(project, "https://github.com/example/team-mail.git")
    monkeypatch.setattr(A, "_url_is_private", lambda root, url: None)

    with pytest.raises(RuntimeError, match="did not confirm it is private"):
        A.sync_mail(root, cfg)

    subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/product.git"], cwd=root, check=True)
    cfg["mail"]["sync_repo"] = "https://github.com/example/product.git"
    with pytest.raises(RuntimeError, match="this product's own remote"):
        A.sync_mail(root, cfg)
