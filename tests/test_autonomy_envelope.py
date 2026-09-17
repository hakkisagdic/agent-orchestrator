import os
import re
import time

import pytest

from ao import cli, lib as A, watchdog as W
from ao.storage import append_chained_jsonl
from tests.scenarios import World


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def test_a_question_parks_its_slice_and_the_nudge_names_the_next_ready_item(world):
    world.board("blocked", "- [S1] the ledger slice · needs: the store decision")
    world.board("queued", "- [S2] the next slice")
    world.transcript_age(900)
    decision = A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"], slice_id="S1")["id"]

    trace = world.cycle()

    assert any(f"{decision} waits, and S2 is READY: an open question does not stop the queue" in line
               for line in trace)
    assert any(f"READY S2 while {decision} waits" in line for line in trace)
    assert "DRY RUN" in world.verdict or "nudging" in world.verdict
    assert W.queue_past_a_question(world.root) == (decision, "S2")
    note = W.parked_note(decision, "S2")
    assert f"needs: {decision}" in note and "READY S2" in note and "soruyu yeniden sorma" in note


def test_with_nothing_ready_an_open_question_still_stands_the_implementer_down(world):
    world.board("blocked", "- [S1] the ledger slice · needs: the store decision")
    world.board("queued", "- [S2] the next slice · needs: S1")
    world.transcript_age(900)
    decision = A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"], slice_id="S1")["id"]

    world.cycle()

    assert W.queue_past_a_question(world.root) is None
    assert f"decision {decision} is open" in world.verdict and "not nudging" in world.verdict


def _next_second():
    """Return once the clock is into a later second: a question's id is the second it was asked in."""
    later = int(time.time()) + 1
    while time.time() < later:
        time.sleep(0.01)


def test_the_doctor_says_how_long_the_architect_is_away_and_how_many_questions_wait(project):
    root = project["root"]
    first = A.ask(root, "first question?", ["a", "b"])["id"]
    _next_second()
    A.ask(root, "second question?", ["a", "b"])
    append_chained_jsonl(A.decisions_path(root), {"id": "AD-1", "at": int(time.time()) - 7200, "decision": "d"},
                         A.DECISION_CHAIN, legacy_prefix=True)

    away = A.architect_absence(root, project)
    text = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(cli._architect_absence_lines(project)))

    assert away["waiting"][0] == first and len(away["waiting"]) == 2
    assert "last seen 120m" in text and f"2 question(s) waiting, the oldest {first}" in text


def test_the_envelope_is_written_where_the_implementer_reads():
    playbook = open(os.path.join(os.path.dirname(A.__file__), "skill", "SKILL.md"), encoding="utf-8").read()
    assert "Working while the architect is away" in playbook
    assert "An unanswered\nquestion never stops the queue" in playbook
