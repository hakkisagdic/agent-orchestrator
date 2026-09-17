"""The guard in conftest.py that fails a test changing the repository it runs in.

It reads one git status a side and core.bare only when a file or variable it comes
from changed. These prove that saving does not blind it: a bare flip made from a
linked worktree, where the status still succeeds, a commit, a staged change and a
branch switch all register, and a fetch elsewhere does not.
"""
import subprocess

from tests import conftest


def _git(cwd, *args):
    return subprocess.run([conftest.GIT, "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(cwd),
                          check=True, capture_output=True, text=True).stdout.strip()


def _repository(path):
    path.mkdir()
    _git(path, "init", "-q")
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "one")
    return path


def _guarding(monkeypatch, repo):
    """The guard's snapshot, pointed at `repo`, with a fresh core.bare cache, and the git calls it makes."""
    calls = []
    real = conftest._git_text
    monkeypatch.setattr(conftest, "REPO", str(repo))
    monkeypatch.setattr(conftest, "_CONFIG_FILES", conftest._config_files())
    monkeypatch.setattr(conftest, "_BARE", {"signature": None, "value": None})
    monkeypatch.setattr(conftest, "_git_text", lambda *args: calls.append(args[0]) or real(*args))
    return conftest._repo_state, calls


def test_a_bare_flip_made_from_a_linked_worktree_is_seen_though_the_status_still_succeeds(tmp_path, monkeypatch):
    main = _repository(tmp_path / "main")
    _git(main, "worktree", "add", "-q", str(tmp_path / "linked"))
    state, calls = _guarding(monkeypatch, tmp_path / "linked")
    before = state()
    calls.clear()
    assert state() == before and calls == ["status"]

    _git(main, "config", "core.bare", "true")
    after = state()

    assert after[:2] == before[:2] and after[0] == 0
    assert (before[2], after[2]) == ("false", "true")


def test_commits_staging_and_a_branch_switch_are_seen_and_a_fetch_is_not(tmp_path, monkeypatch):
    main = _repository(tmp_path / "main")
    _git(tmp_path, "clone", "-q", "--bare", str(main), str(tmp_path / "remote.git"))
    _git(main, "remote", "add", "origin", str(tmp_path / "remote.git"))
    _git(main, "fetch", "-q", "origin")
    _git(main, "branch", "-q", "--set-upstream-to", "origin/" + _git(main, "rev-parse", "--abbrev-ref", "HEAD"))
    _git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(tmp_path / "elsewhere"))
    state, _ = _guarding(monkeypatch, main)
    snapshot = state()

    (tmp_path / "elsewhere" / "b.txt").write_text("b\n", encoding="utf-8")
    _git(tmp_path / "elsewhere", "add", "b.txt")
    _git(tmp_path / "elsewhere", "commit", "-q", "-m", "two")
    _git(tmp_path / "elsewhere", "push", "-q")
    _git(main, "fetch", "-q", "origin")
    assert state() == snapshot

    (main / "a.txt").write_text("changed\n", encoding="utf-8")
    _git(main, "add", "a.txt")
    staged = state()
    assert staged != snapshot

    _git(main, "commit", "-q", "-m", "three")
    committed = state()
    assert committed != staged and committed != snapshot

    _git(main, "checkout", "-q", "-b", "elsewhere")
    assert state() != committed
