import json
import os
import subprocess
import time

import pytest

from ao import cli, lib as A
from ao import watchdog as W
from tests.scenarios import World


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _git(root, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                   capture_output=True)


def _nudged(world, seconds_ago=900, answered=True):
    """The state a nudge leaves behind; an answered one then writes to the transcript."""
    size = os.path.getsize(world.transcript)
    fp = A.work_fingerprint(world.root)
    W.save_state(world.root, {
        "attempts": 1, "last_nudge": time.time() - seconds_ago, "last_size": size,
        "last_fingerprint": fp, "nudge_size": size, "nudge_fingerprint": fp,
        "nudge_inputs": A.nudge_inputs(world.root, world.cfg)})
    if answered:
        with open(world.transcript, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"payload": {"type": "turn_end"}}) + "\n")
        world.turn_ended = True
    return world.transcript_age(700)


def test_an_implementer_that_answered_a_nudge_with_nothing_is_not_asked_again(world):
    world.board("running", "- [B8] slice · since: 2026-09-15 07:00")
    _nudged(world)

    first = world.cycle(dry_run=False)
    second = world.cycle(dry_run=False)

    assert "answered the last nudge without changing anything" in first[-1]
    assert "not nudging" in second[-1]
    assert not any(isinstance(argv, list) and argv and str(argv[0]).startswith("/agents/")
                   for argv in world.spawned)
    told = [notice for notice in world.notices if "nothing to do" in notice[0]]
    assert len(told) == 1 and told[0][2] == "human"
    assert W.load_state(world.root)["idle_answer"]["since"] > 0


def test_a_new_slice_on_the_board_reopens_nudging(world):
    world.board("running", "- [B8] slice · since: 2026-09-15 07:00")
    _nudged(world)
    world.cycle(dry_run=False)
    assert "idle_answer" in W.load_state(world.root)

    world.board("queued", "- [B9] next slice")
    trace = world.cycle()

    assert any("nudges resume" in line for line in trace)
    assert "DRY RUN" in world.verdict or "nudging" in world.verdict


def test_a_nudge_that_was_never_answered_still_backs_off(world):
    world.board("running", "- [B8] slice · since: 2026-09-15 07:00")
    _nudged(world, seconds_ago=60, answered=False)

    world.cycle()

    assert world.verdict.startswith("backing off")


def test_the_watchdogs_own_ledgers_are_not_work_and_product_edits_are(project):
    root = project["root"]
    ledger = os.path.join(root, ".ao", "ledger")
    for name in ("progress.jsonl", "notices.jsonl", "credits.jsonl", "mail.jsonl"):
        with open(os.path.join(ledger, name), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
    with open(os.path.join(root, "product.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    _git(root, "add", "-f", ".ao/ledger", "product.py")
    _git(root, "commit", "-q", "-m", "tracked ledgers, as in a project that commits them")
    before = A.work_fingerprint(root)

    later = time.time() + 30
    for name in ("progress.jsonl", "notices.jsonl", "credits.jsonl", "mail.jsonl"):
        path = os.path.join(ledger, name)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{}\n")
        os.utime(path, (later, later))
    assert A.work_fingerprint(root) == before

    with open(os.path.join(ledger, "verifications.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    verified = A.work_fingerprint(root)
    assert verified != before

    with open(os.path.join(root, "product.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 2\n")
    edited = A.work_fingerprint(root)
    assert edited != verified
    os.utime(os.path.join(root, "product.py"), (later + 60, later + 60))
    assert A.work_fingerprint(root) != edited


def test_progress_does_not_record_its_own_ledger_as_editing(project, tmp_path, monkeypatch):
    root = project["root"]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    notices = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    with open(notices, "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    _git(root, "add", "-f", ".ao/ledger/notices.jsonl")
    _git(root, "commit", "-q", "-m", "tracked notices")

    A.record_progress(root, project)
    with open(notices, "a", encoding="utf-8") as fh:
        fh.write("{}\n")
    later = time.time() + 30
    os.utime(notices, (later, later))
    A.record_progress(root, project)

    with open(os.path.join(root, ".ao", "ledger", "progress.jsonl"), encoding="utf-8") as fh:
        assert len(fh.readlines()) == 1


def test_a_modified_coordination_file_listed_first_is_not_product_dirt(project):
    root = project["root"]
    notices = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    with open(notices, "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    _git(root, "add", "-f", ".ao/ledger/notices.jsonl")
    _git(root, "commit", "-q", "-m", "tracked notices")
    with open(notices, "a", encoding="utf-8") as fh:
        fh.write("{}\n")

    assert A.product_dirty(root, project) == []


def test_status_says_how_long_the_implementer_has_had_nothing_to_do():
    since = time.mktime(time.strptime("2026-09-15 07:00", "%Y-%m-%d %H:%M"))

    text = cli._idle_answer_text({"since": since}, now=since + 5 * 3600 + 7 * 60)

    assert text.startswith("since 07:00 (5h 7m): answered a nudge without changing anything")
    assert text.endswith("waiting for the board, the backlog, a decision or mail")
