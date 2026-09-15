import subprocess
from types import SimpleNamespace

import re

from ao import cli, lib as A


def _plain(text):
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _clone_with_main(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    _git(work, "commit", "-q", "--allow-empty", "-m", "base")
    _git(work, "push", "-q", "origin", "HEAD:main")
    _git(work, "remote", "set-head", "origin", "main")
    return work


def test_a_checkout_behind_main_says_so_and_that_it_is_merged(tmp_path):
    work = _clone_with_main(tmp_path)
    old = _git(work, "rev-parse", "HEAD")
    for i in range(3):
        _git(work, "commit", "-q", "--allow-empty", "-m", f"later {i}")
    _git(work, "push", "-q", "origin", "HEAD:main")
    _git(work, "checkout", "-q", "-b", "stale", old)

    state = A.git_state(str(work))

    assert state["base"] == "origin/main"
    assert (state["ahead"], state["behind"], state["merged"]) == ("0", 3, True)


def test_unpushed_commits_count_against_the_remote_default_branch(tmp_path):
    work = _clone_with_main(tmp_path)
    _git(work, "commit", "-q", "--allow-empty", "-m", "local")

    state = A.git_state(str(work))

    assert (state["ahead"], state["behind"], state["merged"]) == ("1", 0, False)


def test_the_default_branch_behind_its_remote_needs_a_pull_not_a_merge(tmp_path):
    work = _clone_with_main(tmp_path)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    _git(other, "commit", "-q", "--allow-empty", "-m", "elsewhere")
    _git(other, "push", "-q", "origin", "HEAD:main")
    _git(work, "fetch", "-q", "origin")

    state = A.git_state(str(work))

    assert (state["ahead"], state["behind"], state["merged"]) == ("0", 1, False)


def test_an_origin_head_naming_a_deleted_branch_falls_back_to_origin_main(tmp_path):
    work = _clone_with_main(tmp_path)
    _git(work, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    _git(work, "commit", "-q", "--allow-empty", "-m", "local")

    state = A.git_state(str(work))

    assert (state["base"], state["ahead"], state["behind"]) == ("origin/main", "1", 0)


def test_a_checkout_with_no_remote_is_unknown_not_zero(tmp_path):
    work = tmp_path / "solo"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "commit", "-q", "--allow-empty", "-m", "base")

    state = A.git_state(str(work))

    assert state["base"] is None and state["ahead"] == "?" and state["behind"] is None


def test_position_line_names_the_behind_count_and_a_merged_branch():
    line = _plain(cli._checkout_position(
        {"base": "origin/main", "ahead": "0", "behind": 77, "merged": True}))
    assert "0 ahead / 77 behind origin/main" in line
    assert "already merged; this checkout is 77 commits old" in line
    assert "no remote default branch" in _plain(cli._checkout_position({"base": None}))
