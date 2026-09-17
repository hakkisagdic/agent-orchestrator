"""Claims and context take what a review's routes can carry, not what one argument leaves (REVIEW-BUDGET).

A review prompt's commit-message claims and read-only context were held to what 120 KB (30 KB on
Windows) left beside the diff, whatever the reviewer could carry. A waived range whose diff came near
that size was reviewed with its claims only named, and the claims are what a retrospective review
judges. Now a route that declares a channel for its prompt, or whose command fits here, lets them take
review.context_bytes within what the diff budget leaves; a route that takes its prompt only in its
argument holds them to one argument's worth, as before, and so does every route on Windows that takes it
there. A chain hands every route one prompt, built to the least a route that may run can carry.
"""
import json
import os
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W
from tests.test_prompt_channel import _declare, _git, _linux, _route

PIPED = {"argv": ["piped", "{prompt}"], "stdin": {"replaces": ["{prompt}"], "with": ["--from-stdin"]}}
BARE = {"argv": ["bare", "{prompt}"]}
BOUNDARY = "waived review for L1: the implementer is out of credits"
# What each commit of the range claims: eight of them are too long to fit beside a diff near 120 KB.
BODY = "What was wrong: the tally skipped a case.\nWhat changed: it counts every case.\n" * 30

linux_sized = pytest.mark.skipif(os.name == "nt", reason="the range is sized against Linux's bound on one argument")


def _waived_range(root, commits=8):
    """A legacy review waiver and what landed under it, a diff of about 108 KB claimed by each commit: the range."""
    waiver = {"event": "waived", "id": "W-legacy-L1", "gate": "review", "slice": "L1",
              "why": "the implementer is out of credits", "by": "A. Person", "at": int(time.time()),
              "head": _git(root, "rev-parse", "HEAD"), "tree": "t"}
    with open(A.waivers_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(waiver) + "\n")
    with open(os.path.join(root, "large.txt"), "w", encoding="utf-8") as fh:
        fh.write("değer = 'bir satır'\n" * 4_700)
    _git(root, "add", "large.txt")
    _git(root, "commit", "-q", "-m", "feat: a large change\n\n" + BODY)
    for step in range(1, commits):
        with open(os.path.join(root, "tally.txt"), "w", encoding="utf-8") as fh:
            fh.write(f"{step}\n")
        _git(root, "add", "tally.txt")
        _git(root, "commit", "-q", "-m", f"fix: step {step}\n\n" + BODY)
    return f"{waiver['head']}..{_git(root, 'rev-parse', 'HEAD')}"


def _catchup(cfg, monkeypatch):
    monkeypatch.setattr(W, "run", lambda ns: 0)
    return cli.cmd_catchup(cfg, SimpleNamespace(boundary=None, plan=False, limit=None, slice=None,
                                                author_family="writer-family", by="A. Person"))


def _evidence(root):
    (name,) = [name for name in os.listdir(os.path.join(root, "semantic-review")) if name.endswith(".md")]
    return A.review_evidence(open(os.path.join(root, "semantic-review", name), encoding="utf-8").read())


def _claims_as_one_argument_held_them(root, commits):
    """The claims a range was given when one argument's worth held them, as review had always measured."""
    diff = A._git_output(root, "diff", "--binary", "--full-index", "--no-ext-diff", commits, "--").decode(
        "utf-8", "replace")
    smallest = cli.REVIEW_PROMPT.format(boundary=cli._claims_statement(BOUNDARY, "")) \
        + f"\n\n{cli.REVIEW_CANDIDATE_MARKER}\n" + diff
    room = max(0, min(A.REVIEW_CONTEXT_BUDGET, cli.REVIEW_PROMPT_ARG_BYTES - len(smallest.encode("utf-8")) - 200))
    return A.review_range_claims(root, commits, room)


@linux_sized
def test_a_large_range_reviewed_through_a_route_that_declares_a_channel_carries_its_claims_whole(
        project, monkeypatch, tmp_path):
    _linux(monkeypatch)
    _declare(monkeypatch, "piped", send=PIPED)
    root = project["root"]
    commits = _waived_range(root)
    route, record = _route(tmp_path, "r1", "piped")

    assert _catchup(dict(project, reviewer=route), monkeypatch) == 0

    handed = record.read_bytes().decode("utf-8")
    claims = _evidence(root)["claims"]
    assert (claims["commits"], claims["inlined"]) == (8, 8)
    assert handed.count("    What changed: it counts every case.") == 8 * 30
    assert "not inlined for size" not in handed and len(handed.encode("utf-8")) > cli.REVIEW_PROMPT_ARG_BYTES
    assert _claims_as_one_argument_held_them(root, commits)["inlined"] < 8
    assert A.open_waivers(root) == []


@linux_sized
def test_the_same_range_through_a_route_with_no_channel_on_linux_is_bounded_as_before(
        project, monkeypatch, tmp_path, capsys):
    _linux(monkeypatch)
    _declare(monkeypatch, "bare", send=BARE)
    root = project["root"]
    commits = _waived_range(root)
    route, record = _route(tmp_path, "r1", "bare")

    assert _catchup(dict(project, reviewer=route), monkeypatch) == 0

    before = _claims_as_one_argument_held_them(root, commits)
    claims = _evidence(root)["claims"]
    assert (claims["digest"], claims["inlined"]) == (before["digest"], before["inlined"])
    assert 0 < claims["inlined"] < 8
    assert len(record.read_bytes()) <= cli.REVIEW_PROMPT_ARG_BYTES
    assert f"held to one argument's worth, {cli.REVIEW_PROMPT_ARG_BYTES} bytes: r1 takes its prompt in its " \
           "argument alone" in capsys.readouterr().out
    assert A.open_waivers(root) == []


def test_windows_keeps_one_arguments_bound_for_a_route_that_takes_its_prompt_there(monkeypatch):
    _declare(monkeypatch, "bare", send=BARE)
    _declare(monkeypatch, "piped", send=PIPED)
    bare = {"id": "bare-route", "adapter": "bare", "argv": ["bare", "{prompt}"]}
    piped = {"id": "piped-route", "adapter": "piped", "argv": ["piped", "{prompt}"]}
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli, "REVIEW_PROMPT_ARG_BYTES", 30_000)

    # Even a command line that would fit with everything the claims could add keeps the bound there.
    assert cli._review_prompt_bound([bare], 5_000, 0, 1_000, 4_000) == (30_000, "bare-route")
    # A route that takes its prompt on standard input is not held on Windows either.
    assert cli._review_prompt_bound([piped], 20_000, 0, 100_000, 18_000) == (20_000 + 400_000 - 18_000, None)
    assert cli._review_prompt_bound([piped, bare], 20_000, 0, 100_000, 18_000) == (30_000, "bare-route")


@linux_sized
def test_a_mixed_chain_hands_every_route_the_prompt_the_least_that_may_run_can_carry(
        project, monkeypatch, tmp_path, capsys):
    _linux(monkeypatch)
    _declare(monkeypatch, "piped", send=PIPED)
    _declare(monkeypatch, "bare", send=BARE)
    root = project["root"]
    commits = _waived_range(root)
    primary, record = _route(tmp_path, "r1", "piped")
    fallback, _ = _route(tmp_path, "r2", "bare")

    assert _catchup(dict(project, reviewer=dict(primary, fallbacks=[fallback])), monkeypatch) == 0

    # The primary, which could carry the claims whole, is handed what its fallback could carry.
    evidence = _evidence(root)
    assert evidence["reviewer"]["id"] == "r1" and evidence["reviewer"]["fallback"] is False
    assert evidence["claims"]["digest"] == _claims_as_one_argument_held_them(root, commits)["digest"]
    assert len(record.read_bytes()) <= cli.REVIEW_PROMPT_ARG_BYTES
    assert "r2 takes its prompt in its argument alone" in capsys.readouterr().out

    # A fallback that could not be handed even the smallest prompt will not run, and holds nobody down.
    primary_route = {"id": "r1", "adapter": "piped", "argv": ["piped", "{prompt}"]}
    fallback_route = {"id": "r2", "adapter": "bare", "argv": ["bare", "{prompt}"]}
    assert cli._review_prompt_bound([primary_route, fallback_route], 140_000, 0, 100_000, 137_000) \
        == (140_000 + 400_000 - 137_000, None)


@linux_sized
def test_each_section_is_measured_with_its_question(monkeypatch):
    _linux(monkeypatch)
    _declare(monkeypatch, "bare", send=BARE)
    bare = {"id": "bare-route", "adapter": "bare", "argv": ["bare", "{prompt}"]}

    # Held to one argument, the prompt leaves room for the longest question.
    assert cli._review_prompt_bound([bare], 100_000, 600, 100_000, 97_000) == (120_000 - 600, "bare-route")
    # A route that carries the smallest prompt but not with a question cannot run, and holds nobody down.
    assert cli._review_prompt_bound([bare], A.LINUX_ARGUMENT_BYTES - 300, 600, 100_000, 97_000) \
        == (120_000 - 600, None)


def test_claims_and_context_share_the_setting_and_the_room_the_diff_budget_leaves(project):
    budget = cli._review_context_budget
    assert budget("x" * 10_000, 10_000 + 400_000 - 9_000, 100_000) == 100_000
    assert budget("x" * 395_000, 395_000 + 400_000 - 390_000, 100_000) == 10_000 - 200
    assert budget("x" * 10_000, 10_000 + 400_000 - 9_000, 60_000 - 45_000) == 15_000
    assert A.REVIEW_CONTEXT_BUDGET == cli.S.default("review.context_bytes") == 100_000
    assert cli.S.get(dict(project, review={"context_bytes": 0}), "review.context_bytes") == 0
