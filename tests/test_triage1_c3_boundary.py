"""Candidate reviews keep pinned boundaries and name unknown comparisons (TRIAGE-1-MEDIUM)."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from ao import cli, lib as A
from tests import test_criteria_verdicts as criteria
from tests import test_slice_boundary as boundary
from tests.test_review_chain import _args


def _boundary(project):
    root = project["root"]
    boundary._write(root, "docs/spec.md", "# Spec\n## Invariant\nKeep the value.\n")
    boundary._git(root, "add", "docs/spec.md")
    boundary._git(root, "commit", "-qm", "spec")
    sha = boundary._git(root, "rev-parse", "HEAD")
    boundary._board(root, f"- [S1] value · boundary: docs/spec.md@{sha}", state="running")
    return sha


def test_c3_override_is_refused(project, tmp_path, monkeypatch, capsys):
    root, cfg, handed = criteria._reviewed(project, tmp_path, monkeypatch, "- [S1] value", {})
    _boundary(project)
    boundary._write(root, "src/a.py", "x = 3\n")
    boundary._git(root, "add", "src/a.py")
    assert cli.cmd_review(cfg, _args(boundary="another boundary")) == 2
    assert not handed()
    assert "--boundary" in capsys.readouterr().out


def test_c3_boundary_requires_commit(project):
    _boundary(project)
    item = {"notes": {"boundary": "docs/spec.md"}}
    result = A.read_boundary(project["root"], item)
    assert result["problem"] and "commit" in result["problem"]


def test_c3_diff_error_is_unknown_and_named(project, monkeypatch):
    _boundary(project)
    original = A._git_output

    def fail_diff(root, *args, **kwargs):
        if args[0] == "diff":
            raise RuntimeError("diff could not be read")
        return original(root, *args, **kwargs)

    monkeypatch.setattr(A, "_git_output", fail_diff)
    result = A.read_boundary(project["root"], A.running_slice(project["root"]))
    assert result["changed"] is None
    assert "diff could not be read" in result["problem"]
    assert "diff could not be read" in result["text"] and "finding" in result["text"]


def test_c3_commit_refuses_review_missing_current_pointer(project, tmp_path, monkeypatch, capsys):
    root, cfg, _ = criteria._reviewed(project, tmp_path, monkeypatch, "- [S1] value", {})
    _boundary(project)
    boundary._write(root, "src/a.py", "x = 3\n")
    boundary._git(root, "add", "src/a.py")
    boundary._board(root, "- [S1] value · acceptance: keep the value", state="running")
    assert cli.cmd_review(cfg, _args(boundary=None)) == 0
    sha = boundary._git(root, "rev-parse", "HEAD")
    boundary._board(root, f"- [S1] value · boundary: docs/spec.md@{sha}", state="running")
    code, out = criteria._commit_ok(cfg, monkeypatch, capsys)
    assert code != 0 and "boundary" in out


def test_c3_stand_in_keeps_boundary_binding(project, tmp_path, monkeypatch):
    from tests.test_review_chain import _fake
    root, cfg, _ = criteria._reviewed(project, tmp_path, monkeypatch, "- [S1] value", {})
    sha = _boundary(project)
    boundary._write(root, "src/a.py", "x = 3\n")
    boundary._git(root, "add", "src/a.py")
    cfg["reviewer"]["argv"] = _fake("down", exit_code=17)
    assert cli.cmd_review(cfg, _args(boundary=None)) == 3
    request = next(Path(A.review_requests_dir(root)).glob("*.md"))
    meta = A.review_request(root, request.stem)
    assert meta.get("boundary_file", {}).get("commit") == sha
    answer = tmp_path / "answer.txt"
    answer.write_text("\n".join([f"NONCE: {request.stem}"] + criteria.APPROVED), encoding="utf-8")
    assert cli.cmd_collect_review(cfg, SimpleNamespace(nonce=request.stem, response=str(answer),
                                                      model="model-1", by="a person")) == 0
    body = criteria._newest(root)
    assert A.review_evidence(body)["boundary_file"] == meta["boundary_file"]


@pytest.mark.parametrize("pointer", ["docs/missing.md", "docs/missing.md@not-a-commit",
                                     "docs/missing.md@HEAD", "../outside.md@HEAD"])
def test_c3_unreadable_boundary_has_no_comparison_result(project, pointer):
    result = A.read_boundary(project["root"], {"notes": {"boundary": pointer}})
    assert result["problem"] and "finding" in result["text"]
    assert result["changed"] is None and result["sha256"] is None


@pytest.mark.parametrize("command", ["commit-check", "commit"])
def test_c3_restored_boundary_invalidates_an_existing_grant(project, tmp_path, monkeypatch, capsys, command):
    root, cfg, _ = criteria._reviewed(project, tmp_path, monkeypatch, "- [S1] value", {})
    sha = _boundary(project)
    boundary._write(root, "src/a.py", "x = 3\n")
    boundary._git(root, "add", "src/a.py")
    boundary._board(root, "- [S1] value", state="running")
    assert cli.cmd_review(cfg, _args(boundary="trivial")) == 0
    code, out = criteria._commit_ok(cfg, monkeypatch, capsys)
    assert code == 0, out
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0

    boundary._board(root, f"- [S1] value · boundary: docs/spec.md@{sha}", state="running")
    head = boundary._git(root, "rev-parse", "HEAD")
    capsys.readouterr()
    code = cli.cmd_commit_check(cfg, SimpleNamespace()) if command == "commit-check" else \
        cli.cmd_commit(cfg, SimpleNamespace(message="land without the boundary", file=None))
    assert code == 1 and "boundary" in capsys.readouterr().out
    assert boundary._git(root, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("problem", ["unpinned", "missing-file", "missing-commit", "comparison"])
@pytest.mark.parametrize("command", ["commit-ok", "commit-check"])
def test_c3_unreadable_boundary_cannot_grant(project, tmp_path, monkeypatch, capsys, problem, command):
    root, cfg, _ = criteria._reviewed(project, tmp_path, monkeypatch, "- [S1] value", {})
    sha = _boundary(project)
    pointer = {"unpinned": "docs/spec.md", "missing-file": f"docs/missing.md@{sha}",
               "missing-commit": "docs/spec.md@" + "0" * 40,
               "comparison": f"docs/spec.md@{sha}"}[problem]
    boundary._board(root, f"- [S1] value · boundary: {pointer}", state="running")
    boundary._write(root, "src/a.py", "x = 3\n")
    boundary._git(root, "add", "src/a.py")
    original = A._git_output

    def unreadable_diff(root, *args, **kwargs):
        if args == ("diff", sha, "--", "docs/spec.md"):
            raise RuntimeError("boundary comparison unavailable")
        return original(root, *args, **kwargs)

    if problem == "comparison":
        monkeypatch.setattr(A, "_git_output", unreadable_diff)
    assert cli.cmd_review(cfg, _args(boundary=None)) == 0  # the stand-in approves even the unreadable boundary
    monkeypatch.setattr(A, "_git_output", original)
    evidence = A.review_evidence(criteria._newest(root))
    assert evidence["boundary_file"]["changed"] is None
    if command == "commit-check":
        # Reproduce a persisted grant issued while the implementer hid the pointer, even though the
        # approval itself recorded an unreadable boundary. Restoring its matching pointer must not help.
        boundary._board(root, "- [S1] value", state="running")
        code, out = criteria._commit_ok(cfg, monkeypatch, capsys)
        assert code == 0, out
        assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0
        boundary._board(root, f"- [S1] value · boundary: {pointer}", state="running")
        capsys.readouterr()
        assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 1
        assert "boundary" in capsys.readouterr().out
        return
    code, out = criteria._commit_ok(cfg, monkeypatch, capsys)
    assert code == 1 and "boundary" in out
    assert A.latest_authority_decision(root)["granted"] is False


@pytest.mark.parametrize("changed", [False, True])
def test_c3_readable_pinned_boundary_still_authorizes(project, tmp_path, monkeypatch, capsys, changed):
    root, cfg, _ = criteria._reviewed(project, tmp_path, monkeypatch, "- [S1] value", {})
    _boundary(project)
    if changed:
        boundary._write(root, "docs/spec.md", "# Spec\n## Invariant\nKeep the updated value.\n")
        boundary._git(root, "commit", "-qam", "update boundary after the pin")
    boundary._write(root, "src/a.py", "x = 3\n")
    boundary._git(root, "add", "src/a.py")
    assert cli.cmd_review(cfg, _args(boundary=None)) == 0
    assert A.review_evidence(criteria._newest(root))["boundary_file"]["changed"] is changed
    code, out = criteria._commit_ok(cfg, monkeypatch, capsys)
    assert code == 0, out
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0
