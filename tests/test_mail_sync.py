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
                        and "--get-url" not in args else real(root, *args, **kw))  # no host is read; --get-url reads none

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


# ---- the URL judged is the URL git reaches: no rewrite, no remote's name, every origin URL (MAIL-SYNC-4) -----------

def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _config_value(path):
    """A path as a git config file holds it: a backslash there begins an escape, and a Windows path has them."""
    return str(path).replace("\\", "\\\\")


def _bare_named(tmp_path, name):
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], check=True)
    return tmp_path / name


def _mail_refs(bare):
    out = subprocess.run(["git", "--git-dir", str(bare), "for-each-ref", "--format=%(refname)", "refs/mail"],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


def _says_private(monkeypatch):
    import shutil
    asked = []
    real_which, real_run = shutil.which, subprocess.run
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "gh" if name == "gh" else real_which(name, *a, **k))
    monkeypatch.setattr(subprocess, "run", lambda argv, *a, **kw: (asked.append(argv) or SimpleNamespace(stdout="true\n"))
                        if argv[:1] == ["gh"] else real_run(argv, *a, **kw))
    return asked


@pytest.mark.parametrize("target, rule", [("mailbox", "pushInsteadOf"), ("mailbox", "insteadOf"),
                                          ("https://github.com/example/team-mail.git", "pushInsteadOf")])
def test_a_target_git_rewrites_is_refused_not_judged_as_written(project, tmp_path, monkeypatch, target, rule):
    """`mailbox` was judged a private directory, and a github.com URL by GitHub's answer, while a url.<base> rule
    sent the push to another repository."""
    stranger = _bare_named(tmp_path, "stranger.git")
    root, cfg = _store(project, target)
    _says_private(monkeypatch)
    _git(root, "config", f"url.{stranger}.{rule}", target)

    with pytest.raises(RuntimeError, match="name the repository git reaches"):
        A.sync_mail(root, cfg)
    assert _mail_refs(stranger) == []
    assert "name the repository git reaches" in A.mail_sync_state(root, cfg)[2]


def test_the_url_a_remote_names_is_not_rewritten_a_second_time(project, tmp_path):
    mail, stranger = _bare_named(tmp_path, "mail.git"), _bare_named(tmp_path, "stranger.git")
    root, cfg = _store(project, "backup")
    _git(root, "remote", "add", "backup", str(mail))
    _git(root, "remote", "set-url", "--push", "backup", str(mail))
    _git(root, "config", f"url.{tmp_path / 'stranger'}.pushInsteadOf", str(tmp_path / "mail"))

    with pytest.raises(RuntimeError, match="name the repository git reaches"):
        A.sync_mail(root, cfg)
    assert _mail_refs(stranger) == [] and _mail_refs(mail) == []


def test_a_remote_whose_url_git_rewrites_once_syncs_to_the_rewritten_url(project, tmp_path):
    mail = _bare_named(tmp_path, "mail.git")
    root, cfg = _store(project, "backup")
    _git(root, "remote", "add", "backup", "mailshort")
    _git(root, "config", f"url.{mail}.insteadOf", "mailshort")

    commit, where = A.sync_mail(root, cfg)

    assert where == f"{mail} refs/mail/{A.project_key(root)}" and _mail_refs(mail) == [f"refs/mail/{A.project_key(root)}"]


def test_a_push_url_that_names_another_remote_is_refused(project, tmp_path):
    """A remote whose pushurl is `origin` resolved to `origin`, judged a private directory, and `git push origin`
    took the mail to the product."""
    product, mail = _bare_named(tmp_path, "product.git"), _bare_named(tmp_path, "mail.git")
    root, cfg = _store(project, "backup")
    _git(root, "remote", "add", "origin", str(product))
    _git(root, "remote", "add", "backup", str(mail))
    _git(root, "remote", "set-url", "--push", "backup", "origin")

    with pytest.raises(RuntimeError, match="git reads origin as"):
        A.sync_mail(root, cfg)
    local, remote, problem = A.mail_sync_state(root, cfg)
    assert _mail_refs(product) == [] and remote is None and "git reads origin as" in problem


def test_a_url_that_is_also_a_remotes_name_is_refused(project, tmp_path, monkeypatch):
    mail, stranger = _bare_named(tmp_path, "mail.git"), _bare_named(tmp_path, "stranger.git")
    url = "https://github.com/example/team-mail.git"
    root, cfg = _store(project, "backup")
    _says_private(monkeypatch)
    _git(root, "remote", "add", "backup", str(mail))
    _git(root, "remote", "set-url", "--push", "backup", url)
    _git(root, "config", f"remote.{url}.url", str(stranger))       # `git remote add` refuses the name; config takes it

    with pytest.raises(RuntimeError, match="name the repository git reaches"):
        A.sync_mail(root, cfg)
    assert _mail_refs(stranger) == []


def test_a_remote_kept_outside_the_repositorys_config_is_not_taken_for_a_directory(project, tmp_path, monkeypatch):
    """`git remote get-url` knows only this repository's remotes; `git push` also takes one from ~/.gitconfig."""
    stranger = _bare_named(tmp_path, "stranger.git")
    root, cfg = _store(project, "mailbox")
    person = tmp_path / "gitconfig"
    person.write_text(f'[user]\n\tname = t\n\temail = t@t\n[remote "mailbox"]\n\turl = {_config_value(stranger)}\n',
                      encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(person))

    with pytest.raises(RuntimeError, match="git reads mailbox as"):
        A.sync_mail(root, cfg)
    assert _mail_refs(stranger) == []


@pytest.mark.parametrize("how", ["two pushurls", "two urls"])
def test_every_url_origin_pushes_to_is_the_products_own_remote(project, tmp_path, how):
    """The guard read origin's first push URL alone, and `git push origin` reaches every one."""
    product, mirror = _bare_named(tmp_path, "product.git"), _bare_named(tmp_path, "mirror.git")
    root, cfg = _store(project, str(mirror))
    _git(root, "remote", "add", "origin", str(product))
    if how == "two pushurls":
        _git(root, "remote", "set-url", "--add", "--push", "origin", str(product))
        _git(root, "remote", "set-url", "--add", "--push", "origin", str(mirror))
    else:
        _git(root, "config", "--add", "remote.origin.url", str(mirror))

    with pytest.raises(RuntimeError, match="this product's own remote"):
        A.sync_mail(root, cfg)
    assert _mail_refs(mirror) == []


def test_an_origin_kept_in_the_global_config_is_the_products_own_remote(project, tmp_path, monkeypatch):
    """MAIL-SYNC-5: `git remote get-url origin` knows only the repository's own remotes; an origin kept in ~/.gitconfig
    is still where `git push origin` goes, and the mail went to it."""
    product = _bare(tmp_path)
    root, cfg = _store(project, str(product))
    global_config = tmp_path / "gitconfig"
    global_config.write_text(f'[remote "origin"]\n\turl = {_config_value(product)}\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    _never_pushed(monkeypatch)

    with pytest.raises(RuntimeError, match="this product's own remote"):
        A.sync_mail(root, cfg)


def test_a_refusal_names_where_git_pushes_not_where_it_fetches(project, tmp_path, monkeypatch):
    """MAIL-SYNC-5: with an insteadOf and a pushInsteadOf on one URL, the refusal named the fetch rewrite, a place git
    would not push."""
    root, cfg = _store(project, "mailbox")
    for key, value in (("url./elsewhere/fetched.insteadOf", "mailbox"), ("url./elsewhere/pushed.pushInsteadOf", "mailbox")):
        subprocess.run(["git", "config", key, value], cwd=root, check=True)
    _never_pushed(monkeypatch)

    with pytest.raises(RuntimeError, match="pushed"):
        A.sync_mail(root, cfg)
