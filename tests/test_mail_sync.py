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
    A.write_mail(root, cfg, "20260916-0900-fable-to-kiro-NOTE-a.md", f"# a\n\nkeep {token} out\n",
                 {"kind": "note", "from": "fable", "to": "kiro"})
    A.reconcile_mail_ledger(root, cfg)
    return root, cfg


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


def test_a_public_or_unverified_target_and_the_products_own_remote_are_refused(project, monkeypatch):
    root, cfg = _store(project, "https://github.com/example/team-mail.git")
    monkeypatch.setattr(A, "_url_is_private", lambda root, url: None)

    with pytest.raises(RuntimeError, match="did not confirm it is private"):
        A.sync_mail(root, cfg)

    subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/product.git"], cwd=root, check=True)
    cfg["mail"]["sync_repo"] = "https://github.com/example/product.git"
    with pytest.raises(RuntimeError, match="this product's own remote"):
        A.sync_mail(root, cfg)
