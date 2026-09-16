import os
import stat
import subprocess
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_commit_authority import _allow_commit_prerequisites

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
    cfg = dict(project, features={"review": False})
    _allow_commit_prerequisites(monkeypatch, A.tree_digest(root, cfg), A.index_candidate(root))
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    return cfg


def _race(root):
    hook = os.path.join(root, ".git", "hooks", "pre-commit")
    open(hook, "w", encoding="utf-8").write(RACE_HOOK)
    os.chmod(hook, os.stat(hook).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_a_path_staged_after_the_check_is_reported_and_recorded(project, monkeypatch, capsys):
    root = project["root"]
    cfg = _granted_candidate(project, monkeypatch)
    granted = A.latest_authority_decision(root)["candidate"]["index_tree"]
    _race(root)
    capsys.readouterr()

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land a", file=None)) == 1

    landed = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=root, check=True,
                            capture_output=True, text=True).stdout.strip()
    assert landed != granted
    out = capsys.readouterr().out
    assert "LANDED OUTSIDE ITS GRANT" in out and f"bound tree {granted[:12]}" in out
    refusal = A.latest_authority_decision(root)
    assert refusal["granted"] is False and "landed tree" in refusal["reasons"][0]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()
    assert A.commits_without_grant(root) == [head]
    assert any(key == "landed-without-grant" for key, _ in cli.doctor_problems(cfg))


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
