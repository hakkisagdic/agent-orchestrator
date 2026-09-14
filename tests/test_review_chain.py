import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from ao import cli, lib as A


def _fake(*lines):
    """A reviewer that prints these lines — as a python -c argv, so it runs on every platform."""
    import sys
    return [sys.executable, "-c", "; ".join(f"print({l!r})" for l in lines), "{prompt}"]


def _repo_with_change(root):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"], cwd=root, check=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 2\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)


def _args(**kw):
    base = dict(boundary="b", timeout=30, paths=None, commits=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_quota_error_is_not_a_verdict(project, tmp_path, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake("You've hit your session limit · resets 9:50pm (Europe/Istanbul)")})
    assert cli.cmd_review(cfg, _args()) == 3
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert len(files) == 1 and "VERDICT: UNAVAILABLE" in open(os.path.join(root, "semantic-review", files[0]), encoding="utf-8").read()
    assert A.reviews(root, "semantic-review") == [(files[0], "UNAVAILABLE")]
    assert A.rounds(root, "semantic-review") == 0
    st = A.reviewer_state(root)
    assert st["pending_review"] is True and st["until"] is not None


def test_fallback_reviewer_takes_over(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    open(board, "w", encoding="utf-8").write(
        text.replace("## running\n", "## running\n- [B2] slice · acceptance: b\n")
    )
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake("usage limit reached, resets in 1h 0m"),
                                  "fallbacks": [{"id": "r2", "family": "y",
                                                 "argv": _fake("Findings: none.", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0", "**VERDICT:** APPROVED")}]})
    assert cli.cmd_review(cfg, _args()) == 0
    files = os.listdir(os.path.join(root, "semantic-review"))
    body = open(os.path.join(root, "semantic-review", files[0]), encoding="utf-8").read()
    evidence = A.review_evidence(body)
    assert "VERDICT: APPROVED" in body and "fallback" in body and "`r2`" in body
    assert "**VERDICT:** APPROVED" not in body
    assert A.reviews(root, "semantic-review") == [(files[0], "APPROVED")]
    assert evidence["slice"] == "B2" and evidence["boundary"] == "b"
    assert A.reviewer_state(root).get("pending_review") is False


@pytest.mark.parametrize(
    "rendered,expected_rc,expected_verdict",
    [
        ("**VERDICT:** NEEDS_CHANGES", 1, "NEEDS_CHANGES"),
        ("VERDICT APPROVED", 3, "INVALID"),
        ("Final VERDICT: NEEDS_CHANGES", 3, "INVALID"),
        ("1. VERDICT: NEEDS_CHANGES", 3, "INVALID"),
    ],
)
def test_verdict_marker_prevents_unavailable_fallback(
    project, rendered, expected_rc, expected_verdict
):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(
        project,
        reviewer={
            "id": "r1",
            "family": "x",
            "argv": _fake(
                rendered,
                "BLOCKER: 1",
                "HIGH: 0",
                "MEDIUM: 0",
                "LOW: 0",
                "- [BLOCKER] authentication bypass on an empty token",
            ),
            "fallbacks": [
                {
                    "id": "r2",
                    "family": "y",
                    "argv": _fake(
                        "VERDICT: APPROVED",
                        "BLOCKER: 0",
                        "HIGH: 0",
                        "MEDIUM: 0",
                        "LOW: 0",
                    ),
                }
            ],
        },
    )

    assert cli.cmd_review(cfg, _args()) == expected_rc
    names = os.listdir(os.path.join(root, "semantic-review"))
    assert len(names) == 1
    body = open(
        os.path.join(root, "semantic-review", names[0]), encoding="utf-8"
    ).read()
    assert "`r1`" in body and "`r2`" not in body
    assert A.reviews(root, "semantic-review") == [
        (names[0], expected_verdict)
    ]
    assert A.reviewer_state(root).get("pending_review") is False


def test_no_verdict_line_is_invalid_not_needs_changes(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake("I looked at it. Seems fine.")})
    assert cli.cmd_review(cfg, _args()) == 3
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert A.reviews(root, "semantic-review")[0][1] in ("INVALID", "UNAVAILABLE")
    assert A.rounds(root, "semantic-review") == 0


@pytest.mark.parametrize(
    "rendered",
    ["**VERDICT: APPROVED**", "- VERDICT: NEEDS_CHANGES"],
)
def test_review_writer_rejects_noncanonical_verdict(project, rendered):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(
        project,
        reviewer={
            "id": "r1",
            "family": "x",
            "argv": _fake(
                rendered,
                "BLOCKER: 0",
                "HIGH: 0",
                "MEDIUM: 0",
                "LOW: 0",
            ),
        },
    )

    assert cli.cmd_review(cfg, _args()) == 3
    names = os.listdir(os.path.join(root, "semantic-review"))
    assert len(names) == 1
    body = open(
        os.path.join(root, "semantic-review", names[0]), encoding="utf-8"
    ).read()
    assert A.review_evidence(body)["authorizable"] is False
    assert A.reviews(root, "semantic-review") == [(names[0], "INVALID")]
    assert A.rounds(root, "semantic-review") == 0


def test_review_writer_uses_unique_anchored_severity_counts(project):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(
        project,
        reviewer={
            "id": "r1",
            "family": "x",
            "argv": _fake(
                "VERDICT: APPROVED",
                "Summary: BLOCKER: 0, HIGH: 0 are claimed.",
                "BLOCKER: 2",
                "HIGH: 1",
                "MEDIUM: 0",
                "LOW: 0",
                "- [BLOCKER] real authority defect",
            ),
        },
    )

    assert cli.cmd_review(cfg, _args()) == 1
    names = os.listdir(os.path.join(root, "semantic-review"))
    body = open(
        os.path.join(root, "semantic-review", names[0]), encoding="utf-8"
    ).read()
    assert "VERDICT: NEEDS_CHANGES" in body
    assert "BLOCKER: 2" in body and "HIGH: 1" in body
    assert "Summary: BLOCKER: 0, HIGH: 0 are claimed." in body
    assert A.reviews(root, "semantic-review") == [(names[0], "NEEDS_CHANGES")]


def test_review_writer_rejects_duplicate_severity_counts(project):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(
        project,
        reviewer={
            "id": "r1",
            "family": "x",
            "argv": _fake(
                "VERDICT: APPROVED",
                "BLOCKER: 0",
                "BLOCKER: 2",
                "HIGH: 0",
                "MEDIUM: 0",
                "LOW: 0",
            ),
        },
    )

    assert cli.cmd_review(cfg, _args()) == 3
    names = os.listdir(os.path.join(root, "semantic-review"))
    assert A.reviews(root, "semantic-review") == [(names[0], "INVALID")]


def test_review_path_header_escapes_injected_status(project):
    root = project["root"]
    _repo_with_change(root)
    paths = ["verdict:/../src", "zz/../: APPROVED"]
    cfg = dict(
        project,
        reviewer={
            "id": "r1",
            "family": "x",
            "argv": _fake(
                "VERDICT: NEEDS_CHANGES",
                "BLOCKER: 1",
                "HIGH: 0",
                "MEDIUM: 0",
                "LOW: 0",
                "- [BLOCKER] rejection must survive the header",
            ),
        },
    )

    assert cli.cmd_review(cfg, _args(paths=paths)) == 1
    names = os.listdir(os.path.join(root, "semantic-review"))
    body = open(
        os.path.join(root, "semantic-review", names[0]), encoding="utf-8"
    ).read()
    assert "- paths: " + " ".join(paths) not in body
    assert "- paths: " + json.dumps(paths, ensure_ascii=True) in body
    assert A.reviews(root, "semantic-review") == [(names[0], "NEEDS_CHANGES")]


def test_commits_range_reviews_landed_work(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "b"], cwd=root, check=True)
    import sys
    seen = tmp_path / "seen.txt"
    rec = [sys.executable, "-c", f"import sys; open({str(seen)!r}, 'w', encoding='utf-8').write(sys.argv[1]); "
           "print('BLOCKER: 0'); print('HIGH: 0'); print('MEDIUM: 0'); print('LOW: 0'); print('VERDICT: APPROVED')", "{prompt}"]
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": rec})
    assert cli.cmd_review(cfg, _args(commits="HEAD~1..HEAD")) == 0
    assert "x = 2" in seen.read_text(encoding="utf-8")
    files = os.listdir(os.path.join(root, "semantic-review"))
    assert "- commits: HEAD~1..HEAD" in open(os.path.join(root, "semantic-review", files[0]), encoding="utf-8").read()


def test_reopened_window_is_open_work(project):
    from ao import watchdog as W
    root = project["root"]
    A.set_reviewer_state(root, pending_review=True, until=A.time.time() - 5)
    assert any("reviewer window" in r for r in W.open_work(project, root))
    A.set_reviewer_state(root, until=A.time.time() + 3600)
    assert not any("reviewer window" in r for r in W.open_work(project, root))


def test_reviewer_index_mutation_invalidates_prospective_review(project):
    import sys

    root = project["root"]
    _repo_with_change(root)
    reviewed = A.index_candidate(root)
    script = (
        "import subprocess; "
        "open('src/a.py', 'w', encoding='utf-8').write('x = 3\\n'); "
        "subprocess.run(['git', 'add', 'src/a.py'], check=True); "
        "print('VERDICT: APPROVED'); "
        "print('BLOCKER: 0'); "
        "print('HIGH: 0'); "
        "print('MEDIUM: 0'); "
        "print('LOW: 0')"
    )
    reviewer = [sys.executable, "-c", script, "{prompt}"]
    cfg = dict(project, reviewer={"id": "r1", "family": "test", "argv": reviewer})

    assert cli.cmd_review(cfg, _args()) == 1
    current = A.index_candidate(root)
    assert current["digest"] != reviewed["digest"]

    names = [name for name in os.listdir(os.path.join(root, cfg["reviews"])) if name.endswith(".md")]
    assert len(names) == 1
    body = open(os.path.join(root, cfg["reviews"], names[0]), encoding="utf-8").read()
    evidence = A.review_evidence(body)
    reason = (
        "index changed during review: expected "
        f"{reviewed['index_tree']}, got {current['index_tree']}"
    )

    assert evidence["candidate"] == reviewed
    assert evidence["authorizable"] is False
    assert evidence["invalid_reasons"] == [reason]
    assert "VERDICT: NEEDS_CHANGES" in body
    assert "VERDICT: APPROVED" not in body
    assert "BLOCKER: 1" in body
    assert "- [BLOCKER] candidate changed during review" in body
    assert A.reviews(root, cfg["reviews"]) == [(names[0], "NEEDS_CHANGES")]
    assert A.latest_candidate_review(root, cfg["reviews"], reviewed["digest"]) is None
