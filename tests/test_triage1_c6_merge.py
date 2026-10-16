"""Merge verification binds the definitions in the resulting tree (TRIAGE-1-MEDIUM)."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from ao import cli, lib as A
from tests import test_slice_boundary as boundary


@pytest.mark.parametrize("dirty_gates", [False, True])
def test_c6_runs_gates_added_by_the_branch(project, monkeypatch, dirty_gates):
    root = project["root"]
    original = boundary._git(root, "rev-parse", "HEAD")
    spec = {"gates": {"new": {"run": "exit 1"}}, "profiles": {"full": ["new"]}}
    Path(root, ".ao", "gates.json").write_text(json.dumps(spec), encoding="utf-8")
    boundary._git(root, "add", ".ao/gates.json")
    boundary._git(root, "commit", "-qm", "add failing gate")
    branch = boundary._git(root, "rev-parse", "HEAD")
    boundary._git(root, "checkout", "--detach", original)
    if dirty_gates:
        Path(root, ".ao", "gates.json").write_text(json.dumps({
            "gates": {"local": {"run": "exit 0"}}, "profiles": {"full": ["local"]}}), encoding="utf-8")
    assert cli.cmd_merge_check(project, SimpleNamespace(branch=branch, into="HEAD", profile="full", wait=0)) == 1
    row = A.merge_checks(root)[-1]
    assert row["passed"] is False and row["gates"][0]["name"] == "new"
    assert row["gates_digest"] == A.gate_definitions_digest_of(spec, "full")


def test_c6_uncommitted_gates_do_not_supply_missing_merge_definitions(project, monkeypatch, capsys):
    root = project["root"]
    Path(root, ".ao", "gates.json").write_text(json.dumps({
        "gates": {"local": {"run": "exit 0"}}, "profiles": {"full": ["local"]}}), encoding="utf-8")

    def unexpected_lock(*args):
        pytest.fail("missing merge definitions must be rejected before taking the lock")

    monkeypatch.setattr(cli, "_take_gate_lock", unexpected_lock)
    assert cli.cmd_merge_check(project, SimpleNamespace(branch="HEAD", into="HEAD", profile="full", wait=0)) == 2
    assert "no usable .ao/gates.json" in capsys.readouterr().out
    assert not A.merge_checks(root)
    assert len(boundary._git(root, "worktree", "list").splitlines()) == 1


@pytest.mark.parametrize("missing", [False, True])
def test_c6_old_gate_digest_does_not_verify_merge(project, monkeypatch, missing):
    spec = {"gates": {"new": {"run": "exit 1"}}, "profiles": {"full": ["new"]}}
    if missing:
        spec = {}
    monkeypatch.setattr(A, "git_text", lambda *args: "merge tree first second")
    monkeypatch.setattr(A, "_git_output", lambda *args, **kwargs: json.dumps(spec).encode())
    A.record_merge_check(project["root"], {"id": "MC-old", "into": "first", "branch": "second",
                                         "tree": "tree", "profile": "full", "passed": True,
                                         "gates_digest": None if missing else "sha256:old"})
    found = A.unverified_merges(project["root"], project)
    assert found and found[0][0] == "merge" and "definitions" in found[0][1]
