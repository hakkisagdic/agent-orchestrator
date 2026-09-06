import os
from pathlib import Path
import subprocess

import pytest

from ao import lib as A


def _git(root, *args, env=None):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        env=merged,
        check=True,
        capture_output=True,
    )


def _stage_file(root, content="value = 1\n"):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    path = os.path.join(root, "src", "a.py")
    open(path, "w", encoding="utf-8").write(content)
    _git(root, "add", "src/a.py")
    return path


def test_index_candidate_tracks_staged_bytes_not_worktree_bytes(project):
    root = project["root"]
    path = _stage_file(root, "value = 'staged'\n")
    staged = A.index_candidate(root)

    open(path, "w", encoding="utf-8").write("value = 'unstaged'\n")
    assert A.index_candidate(root)["digest"] == staged["digest"]
    assert A.candidate_worktree_issues(root, project, staged)["unstaged"] == ["src/a.py"]

    _git(root, "add", "src/a.py")
    assert A.index_candidate(root)["digest"] != staged["digest"]


def test_candidate_diff_is_immutable_after_live_index_changes(project):
    root = project["root"]
    path = _stage_file(root, "value = 'reviewed'\n")
    reviewed = A.index_candidate(root)
    reviewed_diff = A.candidate_diff(root, reviewed).decode("utf-8")

    open(path, "w", encoding="utf-8").write("value = 'later'\n")
    _git(root, "add", "src/a.py")

    assert "reviewed" in reviewed_diff and "later" not in reviewed_diff
    assert A.candidate_diff(root, reviewed) == reviewed_diff.encode("utf-8")


def test_scoped_candidate_refuses_staged_paths_outside_scope(project):
    root = project["root"]
    _stage_file(root)
    open(os.path.join(root, "other.txt"), "w", encoding="utf-8").write("other\n")
    _git(root, "add", "other.txt")
    candidate = A.index_candidate(root)

    scope = A.candidate_scope(candidate, ["src"])
    assert scope["outside_paths"] == ["other.txt"]
    assert A.candidate_scope(candidate)["outside_paths"] == []


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:\\absolute"])
def test_candidate_scope_rejects_paths_outside_repository(project, path):
    candidate = A.index_candidate(project["root"])
    with pytest.raises(ValueError):
        A.candidate_scope(candidate, [path])


def test_candidate_uses_active_git_index_file(project, tmp_path, monkeypatch):
    root = project["root"]
    _stage_file(root, "value = 'main-index'\n")
    main_candidate = A.index_candidate(root)

    alternate = tmp_path / "alternate.index"
    _git(root, "read-tree", "HEAD", env={"GIT_INDEX_FILE": str(alternate)})
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate))
    alternate_candidate = A.index_candidate(root)

    assert alternate_candidate["changed_count"] == 0
    assert alternate_candidate["index_tree"] != main_candidate["index_tree"]


def test_review_evidence_round_trips_candidate_metadata(project):
    candidate = A.index_candidate(project["root"])
    evidence = {
        "schema": 2,
        "kind": "index-candidate",
        "authorizable": True,
        "candidate": candidate,
        "scope": A.candidate_scope(candidate),
    }
    body = "# Review\n\n" + A.review_evidence_line(evidence) + "\n"
    assert A.review_evidence(body) == evidence


def _write_candidate_review(path, candidate, verdict, *, authorizable=True):
    evidence = {
        "schema": 2,
        "kind": "index-candidate",
        "authorizable": authorizable,
        "candidate": candidate,
        "scope": A.candidate_scope(candidate),
    }
    path.write_text(
        "# Review\n\n"
        + A.review_evidence_line(evidence)
        + f"\n\nVERDICT: {verdict}\n",
        encoding="utf-8",
    )


def test_newest_matching_structured_review_is_authoritative(project):
    root = project["root"]
    _stage_file(root)
    candidate = A.index_candidate(root)
    review_dir = os.path.join(root, project["reviews"])

    approved = Path(review_dir, "approved.md")
    _write_candidate_review(approved, candidate, "APPROVED")
    os.utime(approved, (1, 1))

    unavailable = Path(review_dir, "unavailable.md")
    unavailable.write_text("VERDICT: UNAVAILABLE\n", encoding="utf-8")
    os.utime(unavailable, (2, 2))
    assert A.latest_candidate_review(root, project["reviews"], candidate["digest"])[0] == "approved.md"

    rejected = Path(review_dir, "rejected.md")
    _write_candidate_review(rejected, candidate, "NEEDS_CHANGES")
    os.utime(rejected, (3, 3))
    assert A.latest_candidate_review(root, project["reviews"], candidate["digest"]) is None


def test_latest_authority_decision_uses_only_actual_boolean_rows(project):
    from ao.storage import append_jsonl

    path = os.path.join(project["root"], ".ao", "ledger", "authority.jsonl")
    append_jsonl(path, {"granted": True, "token": "older"})
    append_jsonl(path, {"granted": "yes", "token": "not-a-decision"})
    assert A.latest_authority_decision(project["root"])["token"] == "older"

    append_jsonl(path, {"granted": False, "token": "newest"})
    assert A.latest_authority_decision(project["root"])["token"] == "newest"


def test_latest_authority_decision_fails_closed_on_corruption(project):
    from ao.storage import LedgerCorruption

    path = os.path.join(project["root"], ".ao", "ledger", "authority.jsonl")
    open(path, "w", encoding="utf-8").write('{"granted":true}\nnot-json\n')
    with pytest.raises(LedgerCorruption):
        A.latest_authority_decision(project["root"])
