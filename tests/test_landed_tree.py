import os
import stat
import subprocess
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_commit_authority import _allow_commit_prerequisites
from tests.conftest import review_off

RACE_HOOK = """#!/bin/sh
# the background job racing the commit: stages a path after the check passed
echo "unreviewed = True" > src/unreviewed.py
git add src/unreviewed.py
"""


def _granted_candidate(project, monkeypatch):
    root = project["root"]
    for name, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
                        ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(name, value)
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("value = 1\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)
    cfg = review_off(project)
    _allow_commit_prerequisites(monkeypatch, A.tree_digest(root, cfg), A.index_candidate(root))
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    return cfg


def _race(root):
    hook = os.path.join(root, ".git", "hooks", "pre-commit")
    open(hook, "w", encoding="utf-8", newline="\n").write(RACE_HOOK)
    os.chmod(hook, os.stat(hook).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _rev(root, *args):
    return subprocess.run(["git", "rev-parse", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def test_a_hook_that_would_stage_after_the_check_does_not_run_and_the_granted_tree_lands(project, monkeypatch, capsys):
    """Git wrote the tree after the hook returned, so a hook that staged a path landed it (#64): ao commit writes the
    granted tree itself and runs no hook (LEADS-GATE)."""
    root = project["root"]
    cfg = _granted_candidate(project, monkeypatch)
    granted = A.latest_authority_decision(root)["candidate"]["index_tree"]
    _race(root)

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 0

    assert _rev(root, "HEAD^{tree}") == granted
    assert not os.path.exists(os.path.join(root, "src", "unreviewed.py"))
    assert A.commits_without_grant(root) == []


def test_a_commit_of_the_granted_tree_is_quiet(project, monkeypatch, capsys):
    root = project["root"]
    cfg = _granted_candidate(project, monkeypatch)

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 0

    assert A.landed_commit_problem(root) is None
    assert A.commits_without_grant(root) == []
    assert "LANDED OUTSIDE ITS GRANT" not in capsys.readouterr().out
    assert not any(key == "landed-without-grant" for key, _ in cli.doctor_problems(cfg))


def test_history_from_before_the_first_grant_is_not_measured(project, monkeypatch):
    root = project["root"]
    assert A.commits_without_grant(root) == []
    _granted_candidate(project, monkeypatch)
    assert A.commits_without_grant(root) == []
