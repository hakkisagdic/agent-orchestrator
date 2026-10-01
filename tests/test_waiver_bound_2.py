"""What the retrospective review of WAIVER-BOUND found that still held (WAIVER-BOUND-2).

commit-ok asked for an open waiver before it asked for a review, so a candidate an APPROVED review was
bound to was granted as waived: the review went unrecorded and the waiver was spent on it. commit-check
held a grant to the newest open waiver, so a second waiver opened for the slice refused the grant the
first had made. And catchup took a review that exited 0 with no verdict it could find, for a range that
changed lines, for a review of an empty range, and closed its waiver.
"""
import os
import subprocess
from types import SimpleNamespace

from ao import cli, lib as A, watchdog as W
from tests.test_switches_and_bypass import _allow_candidate_verification
from tests.test_waiver_bounds import NAMED, _commit, _commit_ok, _landed_under_a_waiver, _running, _stage


def _bound_review(monkeypatch, holds=True):
    """An APPROVED review bound to the staged candidate, one that holds or one that does not."""
    evidence = {"reviewer": {"id": "independent"}}
    monkeypatch.setattr(A, "candidate_review_decision", lambda root, reviews, digest: {
        "match": ("r.md", "semantic-review/r.md", "body", evidence), "problem": None})
    monkeypatch.setattr(A, "candidate_review_integrity", lambda root, candidate, ev: (
        None, [] if holds else ["the reviewed tree is not the staged one"]))
    monkeypatch.setattr(A, "criteria_refusals", lambda ev: [])
    monkeypatch.setattr(cli, "_recorded_review_tier", lambda cfg, ev, who: ("another model family", None))


def test_a_candidate_a_review_is_bound_to_stands_on_it_and_the_waiver_stays_open(project, monkeypatch, capsys):
    root = project["root"]
    _running(root, "B7")
    _allow_candidate_verification(monkeypatch, _stage(root, "a.py", "value = 1\n"))
    waiver = A.waive(root, "review", "B7", "quota", by="alice (owner)")
    _bound_review(monkeypatch)

    code, out = _commit_ok(project, capsys)

    assert code == 0 and "review waived" not in out
    grant = A.latest_authority_decision(root)
    assert grant["review"] == "r.md" and grant.get("waiver") is None
    assert A.review_waiver_for(root, ["B7"], "other bytes")[0]["id"] == waiver["id"]     # not spent


def test_a_bound_review_that_does_not_hold_leaves_the_waiver_to_stand_in(project, monkeypatch, capsys):
    root = project["root"]
    _running(root, "B7")
    _allow_candidate_verification(monkeypatch, _stage(root, "a.py", "value = 1\n"))
    waiver = A.waive(root, "review", "B7", "quota", by="alice (owner)")
    _bound_review(monkeypatch, holds=False)

    code, out = _commit_ok(project, capsys)

    assert code == 0 and "the review bound to this candidate does not hold" in out and "review waived" in out
    assert A.latest_authority_decision(root)["waiver"] == waiver["id"]


def test_a_grant_is_checked_against_the_waiver_it_stood_on_not_the_newest_one_open(project, monkeypatch, capsys):
    root = project["root"]
    _running(root, "B7")
    _allow_candidate_verification(monkeypatch, _stage(root, "a.py", "value = 1\n"))
    first = A.waive(root, "review", "B7", "quota", by="alice (owner)")
    assert _commit_ok(project, capsys)[0] == 0
    assert A.latest_authority_decision(root)["waiver"] == first["id"]
    A.waive(root, "review", "B7", "a second reason", by="alice (owner)")        # opened since

    assert cli.cmd_commit_check(project, SimpleNamespace()) == 0


def test_a_review_that_records_no_verdict_closes_only_a_range_with_no_net_change(project, monkeypatch, capsys):
    waiver, _, _ = _landed_under_a_waiver(project, monkeypatch, capsys)
    monkeypatch.setattr(W, "run", lambda ns: 0)
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns: 0)                    # exits 0, records nothing
    capsys.readouterr()

    assert cli.cmd_catchup(project, NAMED) == 1
    assert "recorded no verdict ao can find" in capsys.readouterr().out
    assert [w["id"] for w in A.open_waivers(project["root"])] == [waiver["id"]]

    monkeypatch.setattr(A, "range_unchanged", lambda root, start, end: True)
    assert cli.cmd_catchup(project, NAMED) == 0
    assert A.open_waivers(project["root"]) == []


def test_a_review_that_records_no_verdict_keeps_open_a_range_no_line_of_which_changed(project, monkeypatch,
                                                                                         capsys):
    """WAIVER-BOUND-3: no changed line was taken for no change, and a binary file, or a textconv driver that reads
    two versions alike, changes a range git counts no line of."""
    root = project["root"]
    blob = os.path.join(root, "src", "blob.bin")
    os.makedirs(os.path.dirname(blob), exist_ok=True)
    with open(blob, "wb") as fh:
        fh.write(b"\x00\x01\x02")
    subprocess.run(["git", "add", "src/blob.bin"], cwd=root, check=True, capture_output=True)
    _commit(root, "base")
    _running(root, "B7")
    with open(blob, "wb") as fh:
        fh.write(b"\x00\x09\x02\x03")
    subprocess.run(["git", "add", "src/blob.bin"], cwd=root, check=True, capture_output=True)
    _allow_candidate_verification(monkeypatch, A.index_candidate(root))
    waiver = A.waive(root, "review", "B7", "quota", by="alice (owner)")
    assert _commit_ok(project, capsys)[0] == 0
    landed = _commit(root, "b7")
    parent = subprocess.run(["git", "rev-parse", f"{landed}^"], cwd=root, check=True, capture_output=True,
                            text=True).stdout.strip()
    assert A.range_changed_lines(root, parent, landed) == 0 and A.range_unchanged(root, parent, landed) is False
    monkeypatch.setattr(W, "run", lambda ns: 0)
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns: 0)                    # a diff that showed nothing
    capsys.readouterr()

    assert cli.cmd_catchup(project, NAMED) == 1
    assert "recorded no verdict ao can find" in capsys.readouterr().out
    assert [w["id"] for w in A.open_waivers(root)] == [waiver["id"]]
