import json
import os
import time

import pytest

from ao import lib as A, watchdog as W
from tests.scenarios import World


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _secondary(world, tmp_path, *queued, written_ago=0):
    other = tmp_path / "ao-secondary"
    (other / ".ao").mkdir(parents=True)
    config = {"project": "ao-secondary", "mailbox": "agent-mail", "reviews": "semantic-review",
              "implementer": {"adapter": "kiro", "session": "s2", "name": "kiro"}}
    (other / ".ao" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (other / ".ao" / "board.md").write_text("# Board\n\n## running\n\n## blocked\n\n## queued\n"
                                            + "".join(f"{line}\n" for line in queued) + "\n## verified\n\n## done\n",
                                            encoding="utf-8")
    stored = {key: value for key, value in world.cfg.items() if key != "root"}
    stored["secondary"] = [{"root": str(other), "name": "ao"}]
    with open(os.path.join(world.root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    transcript = tmp_path / "secondary-transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    when = time.time() - written_ago
    os.utime(transcript, (when, when))
    here = str(world.transcript)
    world.mp.setattr(A, "session_paths", lambda cfg: (str(transcript), None)
                     if os.path.realpath(str(cfg.get("root"))) == os.path.realpath(str(other)) else (here, None))
    return other


def _implementer_turns(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).endswith("/kiro-cli")]


def test_an_implementer_writing_in_the_secondary_project_is_working_not_idle(world, tmp_path):
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)
    _secondary(world, tmp_path)

    world.cycle(dry_run=False)

    assert world.verdict.startswith("working in ao") and "not nudging here" in world.verdict
    assert _implementer_turns(world) == []


def test_with_nothing_ready_here_the_nudge_names_the_secondary_projects_ready_item(world, tmp_path):
    world.board("blocked", "- [B1] needs the owner · waiting: architect")
    world.transcript_age(900)
    _secondary(world, tmp_path, "- [S9] secondary work", written_ago=3600)

    trace = world.cycle()

    assert any("READY S9 in ao" in line for line in trace)
    assert "DRY RUN" in world.verdict or "nudging" in world.verdict
    cfg = A.load_config(world.root)
    found = A.secondary_ready(cfg)
    assert found["item"] == "S9" and "secondary project ao" in W.secondary_note(cfg, found)


def test_a_blocker_declared_for_a_person_reaches_a_person_and_is_never_held(world):
    world.board("blocked", "- [B1] the reviewer token expired · needs: a new token · waiting: human")
    world.transcript_age(900)

    world.cycle()

    told = [notice for notice in world.notices if "B1 waits on a person: a new token" in notice[1]]
    assert len(told) == 1 and told[0][2] == "human"
