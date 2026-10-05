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


# ---- a sync target is judged as the one URL git pushes to (MAIL-SYNC-3) ------------------------------------------

def _no_gh(monkeypatch):
    import shutil
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "gh" else real(name, *a, **k))


def _never_pushed(monkeypatch):
    real = A._git_output
    # Nothing in these tests may reach a host: a refusal missing would otherwise push to it.
    monkeypatch.setattr(A, "_git_output", lambda root, *args, **kw: pytest.fail("pushed") if "push" in args
                        else real(root, *args, **kw))


def test_a_remote_named_in_place_of_a_url_is_judged_as_the_url_git_pushes_to(project, monkeypatch):
    """`origin` or `backup` as mail.sync_repo was taken for a directory on this machine - private, no host asked,
    never compared with the product's remote - and `git push origin` took the mail to the product."""
    root, cfg = _store(project, "origin")
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/product.git"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "backup", "https://github.com/example/team-mail.git"], cwd=root, check=True)
    _no_gh(monkeypatch)                                                          # no host can be asked
    _never_pushed(monkeypatch)

    with pytest.raises(RuntimeError, match="this product's own remote"):
        A.sync_mail(root, cfg)
    cfg["mail"]["sync_repo"] = "backup"
    with pytest.raises(RuntimeError, match="did not confirm it is private"):
        A.sync_mail(root, cfg)


def test_the_doctor_judges_a_remote_named_in_place_of_a_url_by_its_url(project, monkeypatch):
    root, cfg = _store(project, "backup")
    subprocess.run(["git", "remote", "add", "backup", "https://github.com/example/team-mail.git"], cwd=root, check=True)
    _no_gh(monkeypatch)
    real = A._git_output
    monkeypatch.setattr(A, "_git_output", lambda root, *args, **kw: b"" if "ls-remote" in args
                        else real(root, *args, **kw))                            # no host is read

    local, remote, problem = A.mail_sync_state(root, cfg)

    assert problem and "could not be verified private" in problem


def test_github_answers_only_for_a_repository_on_github_itself(project, monkeypatch):
    """`github.com/<owner>/<repo>` was matched anywhere in the URL, so GitHub's answer for an unrelated repository let
    mail go to another host."""
    import shutil
    root = project["root"]
    asked = []
    real_which, real_run = shutil.which, subprocess.run
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "gh" if name == "gh" else real_which(name, *a, **k))
    monkeypatch.setattr(subprocess, "run", lambda argv, *a, **kw: (asked.append(argv) or SimpleNamespace(stdout="true\n"))
                        if argv[:1] == ["gh"] else real_run(argv, *a, **kw))     # GitHub says private, to anyone

    for url in ("https://evilGITHUB.COM/example/private-mail.git",
                "https://evil.example/github.com/example/private-mail.git",
                "git@evil.example:github.com/example/private-mail.git"):
        assert A._url_is_private(root, url) is None, url
    assert asked == []
    assert A._url_is_private(root, "git@github.com:example/private-mail.git") is True
    assert asked == [["gh", "api", "repos/example/private-mail", "--jq", ".private"]]


def test_an_option_in_place_of_a_repository_never_reaches_git(project, tmp_path, monkeypatch):
    """`--upload-pack=<command>` as mail.sync_repo was handed to `git ls-remote`, and every `ao doctor` ran it."""
    marker = tmp_path / "ran"
    root, cfg = _store(project, f"--upload-pack=touch {marker}")
    _never_pushed(monkeypatch)

    local, remote, problem = A.mail_sync_state(root, cfg)

    assert remote is None and "an option to git" in problem
    with pytest.raises(RuntimeError, match="an option to git"):
        A.sync_mail(root, cfg)
    assert not marker.exists()


def test_the_products_own_remote_is_refused_whatever_the_case_of_its_suffix(project, monkeypatch):
    root, cfg = _store(project, "https://github.com/Example/Product.GIT")
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/product.git"], cwd=root, check=True)
    _never_pushed(monkeypatch)

    with pytest.raises(RuntimeError, match="this product's own remote"):
        A.sync_mail(root, cfg)


def test_a_governance_backup_remote_is_judged_by_the_url_it_pushes_to_on_github_itself(project, monkeypatch):
    """`ao backup --to remote:<name>` asked GitHub about the fetch URL, matched anywhere in it, and pushed to the
    pushurl."""
    import shutil
    root = project["root"]
    asked = []
    real_which, real_run = shutil.which, subprocess.run
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "gh" if name == "gh" else real_which(name, *a, **k))
    monkeypatch.setattr(subprocess, "run", lambda argv, *a, **kw: (asked.append(argv) or SimpleNamespace(stdout="true\n"))
                        if argv[:1] == ["gh"] else real_run(argv, *a, **kw))
    subprocess.run(["git", "remote", "add", "backup", "https://github.com/example/governance.git"], cwd=root, check=True)
    subprocess.run(["git", "remote", "set-url", "--push", "backup", "https://evil.example/github.com/example/x.git"],
                   cwd=root, check=True)

    assert A.remote_is_private(root, "backup") is None and asked == []
    assert A.remote_is_private(root, "--upload-pack=true") is None and asked == []
