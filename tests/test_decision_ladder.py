import json
import os
import time

import pytest

from ao import lib as A, watchdog as W
from tests.scenarios import World

BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _asked(root, minutes_ago, question="which store keeps the ledger?"):
    decision = A.ask(root, question, ["files", "sqlite"])
    path = os.path.join(root, A.DECISION_DIR, decision["id"] + ".json")
    data = json.load(open(path, encoding="utf-8"))
    data["asked_at"] = int(time.time() - minutes_ago * 60)
    json.dump(data, open(path, "w", encoding="utf-8"))
    return decision["id"]


def _architect_wakes(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and argv[0].endswith("/claude")]


def _next_second():
    """Return once the clock is into a later second: a decision's id is the second it was asked in."""
    later = int(time.time()) + 1
    while time.time() < later:
        time.sleep(0.01)


def test_a_decision_open_fifteen_minutes_rings_a_person(project, monkeypatch):
    root = project["root"]
    rung = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: rung.append((title, msg, kw)))
    old = _asked(root, 20)
    _next_second()
    _asked(root, 5, "a fresh one")

    assert W.escalate_open_decisions(root, "proj") == [old]
    [(title, msg, kw)] = rung
    assert kw["audience"] == "human" and kw["key"] == f"decision-open:{old}" and old in msg

    A.answer(root, old, "a")
    rung.clear()
    assert W.escalate_open_decisions(root, "proj") == [] and rung == []


def test_a_fresh_decision_wakes_the_architect_past_a_report_already_handed(world):
    world.transcript_age(900)
    world.mail("20260916-0900-kiro-to-fable-BLOCKED-old.md", BLOCKED)
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1
    st = W.load_state(world.root)
    st["last_arch_wake"] = time.time() - 3600
    W.save_state(world.root, st)
    world.spawned.clear()

    _asked(world.root, 1)
    world.cycle(dry_run=False)

    assert len(_architect_wakes(world)) == 1


def test_the_implementer_is_not_nudged_while_a_decision_is_open(world):
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)
    decision = _asked(world.root, 1)

    world.cycle()

    assert f"decision {decision} is open" in world.verdict


def test_an_outage_leaves_one_deferral_one_quiet_alarm_and_one_wake_when_it_ends(world, monkeypatch):
    for _ in range(14):
        A.deferred_append(world.root, "wake", reason="architect quota", until=time.time() + 7 * 86400)
    assert len([row for row in A.deferred_open(world.root) if row["kind"] == "wake"]) == 1

    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    until = time.time() + 7 * 86400
    st = W.load_state(world.root)
    st["arch_quota_until"] = until
    W.save_state(world.root, st)
    quiet = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: quiet.append(kw.get("quiet_until")) or True)
    for _ in range(3):
        world.cycle(dry_run=False)
    assert _architect_wakes(world) == []
    assert until in quiet

    st = W.load_state(world.root)
    st["arch_quota_until"] = time.time() - 1
    W.save_state(world.root, st)
    for _ in range(3):
        world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1
