import json
import os
import time
from types import SimpleNamespace

from ao import cli, lib as A, watchdog as W

REVIEWS = "semantic-review"


def _running(root, slice_id="R1", since="2026-09-16 09:00"):
    path = os.path.join(root, ".ao", "board.md")
    board = open(path, encoding="utf-8").read()
    board = "\n".join(line for line in board.splitlines() if not line.startswith("- ["))
    open(path, "w", encoding="utf-8").write(
        board.replace("## running\n", f"## running\n- [{slice_id}] a slice · since: {since}\n", 1) + "\n")


def _review(root, verdict, slice_id="R1", candidate="sha256:c1", fallback=False, n=[0]):
    n[0] += 1
    name = f"2026-09-16-{n[0]:06d}-r.md"
    evidence = {"schema": 2, "kind": "index-candidate", "authorizable": True, "slice": slice_id,
                "candidate": {"digest": candidate}, "verdict": verdict}
    body = f"# review\n\nVERDICT: {verdict}\n\n{A.review_evidence_line(evidence)}\n"
    A.write_review_artefact(root, REVIEWS, name, body, evidence=evidence, verdict=verdict,
                            reviewer="r1", fallback=fallback)
    return name


def test_a_round_is_a_recorded_verdict_whatever_happens_to_its_file(project):
    root = project["root"]
    _running(root)
    first = _review(root, "NEEDS_CHANGES")
    second = _review(root, "NEEDS_CHANGES")
    assert A.rounds(root, REVIEWS) == 2

    os.remove(os.path.join(root, REVIEWS, first))
    old = time.time() - 30 * 86400
    os.utime(os.path.join(root, REVIEWS, second), (old, old))
    assert A.rounds(root, REVIEWS) == 2


def test_moving_since_on_the_board_does_not_restart_the_budget(project):
    root = project["root"]
    _running(root)
    _review(root, "NEEDS_CHANGES")
    _review(root, "NEEDS_CHANGES")

    _running(root, since="2030-01-01 00:00")

    assert A.rounds(root, REVIEWS) == 2


def test_unavailable_and_invalid_are_not_rounds(project):
    root = project["root"]
    _running(root)
    _review(root, "NEEDS_CHANGES")
    _review(root, "UNAVAILABLE")
    _review(root, "INVALID")

    assert A.rounds(root, REVIEWS) == 1


def test_a_fallback_approval_after_a_rejection_ends_nothing(project):
    root = project["root"]
    _running(root)
    _review(root, "NEEDS_CHANGES")
    _review(root, "APPROVED", fallback=True)
    assert A.rounds(root, REVIEWS) == 1

    _review(root, "APPROVED")
    assert A.rounds(root, REVIEWS) == 0


def test_only_the_running_slice_counts(project):
    root = project["root"]
    _running(root, "R2")
    _review(root, "NEEDS_CHANGES", slice_id="R1")
    _review(root, "NEEDS_CHANGES", slice_id="R2")

    assert A.rounds(root, REVIEWS) == 1


def _decide(project, scope):
    args = SimpleNamespace(list=False, n=10, decision="re-specified", why="scope moved", scope=scope,
                           answers=None, urgent=False, to=None)
    return cli.cmd_decide(project, args)


def test_a_recorded_respecification_restarts_the_budget_and_a_hand_added_one_does_not(project):
    root = project["root"]
    _running(root)
    _review(root, "NEEDS_CHANGES")
    time.sleep(1.1)
    assert _decide(project, "R1") == 0
    assert A.rounds(root, REVIEWS) == 0

    time.sleep(1.1)
    _review(root, "NEEDS_CHANGES")
    _review(root, "NEEDS_CHANGES")
    assert A.rounds(root, REVIEWS) == 2
    with open(A.decisions_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "AD-forged", "at": int(time.time()) + 60, "scope": "R1",
                             "by": "architect"}) + "\n")

    assert A.rounds(root, REVIEWS) >= 2


def test_a_review_that_did_not_take_place_is_neither_findings_nor_progress(project):
    root = project["root"]
    before = A.work_fingerprint(root)
    _review(root, "UNAVAILABLE")

    assert A.work_fingerprint(root) == before
    assert "open review findings" not in W.open_work(project, root)

    _review(root, "NEEDS_CHANGES")
    assert A.work_fingerprint(root) != before


def test_the_digest_counts_only_reviews_that_took_place(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "kiro_account_usage", lambda timeout=20: None)
    _review(root, "NEEDS_CHANGES")
    _review(root, "UNAVAILABLE")
    _review(root, "APPROVED")

    counts = A.digest(root, project)["reviews"]

    assert counts == {"total": 2, "approved": 1, "changes": 1, "not_reviewed": 1}


def test_only_an_implementer_granted_ao_decide_is_named():
    from ao import allowlist as AL
    grant = ["agent", "--allowedTools", "Read,Bash(ao decide:*)"]

    assert AL.problems(grant, role="implementer") == [
        ("re-specifies a slice, restarting its round budget", "ao decide x --scope s", "Bash(ao decide:*)")]
    assert AL.problems(grant, role="architect") == []
