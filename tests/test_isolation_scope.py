import json
import os
import subprocess
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_commit_authority import _allow_commit_prerequisites, _write_approved_review


def _staged_candidate(root):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True)


def _gates(root, **inputs):
    gates = {name: dict({"run": f"echo {name}"}, **({"inputs": globs} if globs is not None else {}))
             for name, globs in inputs.items()}
    json.dump({"gates": gates, "profiles": {"quick": list(gates)}},
              open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8"))


def test_a_path_no_gate_reads_is_noise_and_the_grant_is_issued(project, monkeypatch, capsys):
    root = project["root"]
    _staged_candidate(root)
    open(os.path.join(root, "notes.txt"), "w", encoding="utf-8").write("scratch\n")
    _gates(root, tests=["src/**", "tests/**"], lint=["src/**"])

    issues = A.candidate_worktree_issues(root, project)
    assert issues["untracked"] == [] and issues["worktree_noise"] == ["notes.txt"]

    tree, candidate = A.tree_digest(root, project), A.index_candidate(root)
    _write_approved_review(project, tree, candidate)
    _allow_commit_prerequisites(monkeypatch, tree, candidate)
    capsys.readouterr()
    assert cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None)) == 0
    assert "worktree noise" in capsys.readouterr().out and A.latest_authority_decision(root)["granted"]


def test_a_path_a_gate_reads_still_refuses(project):
    root = project["root"]
    _staged_candidate(root)
    open(os.path.join(root, "src", "b.py"), "w", encoding="utf-8").write("y = 2\n")
    _gates(root, tests=["src/**"])

    issues = A.candidate_worktree_issues(root, project)

    assert issues["untracked"] == ["src/b.py"] and issues["worktree_noise"] == []


def test_one_gate_without_inputs_means_every_dirty_path_counts(project):
    root = project["root"]
    _staged_candidate(root)
    open(os.path.join(root, "notes.txt"), "w", encoding="utf-8").write("scratch\n")
    _gates(root, tests=["src/**"], full=None)

    issues = A.candidate_worktree_issues(root, project)

    assert issues["untracked"] == ["notes.txt"] and issues["worktree_noise"] == []
    assert A.gate_inputs(root) is None
