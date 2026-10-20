"""A slice that spends past its budget is parked, and only a person or the architect gives it more (SLICE-BUDGET).

The watchdog measured a slice's time and spend only after the fact, in `ao gain`: a runaway slice
could spend a day's window before anything said so. These drive the guard on a temporary board and
a canned spend reading. The budget is the current running run's - a slice blocked on quota and
resumed starts a new run - and spend is the implementer's turns since it started. A park writes
the reading on the board and tells the architect; ao never resumes an over-budget slice, and the
implementer may not lift the block.
"""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from ao import cli
from ao import lib as A
from ao import settings as S
from ao import watchdog as W
from tests.scenarios import World

ROOT = Path(__file__).resolve().parent.parent
REAL_POPEN = subprocess.Popen
SLICE = "- [B8] the slice · since: 2026-09-26 08:00"


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _running(world):
    """The slice was queued and the implementer started it, as its ledger records."""
    world.board("queued", SLICE)
    assert A.board_move(world.root, "B8", "running", by="implementer")
    return world


def _cfg(world, **values):
    return dict(world.cfg, **values)


def _record(at, kind, **payload):
    stamp = datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")
    return {"timestamp": stamp, "payload": dict(type=kind, **payload)}


def _turn(at, *usages):
    """A turn opening at `at`, with each `(when, credits)` usage record, closed after the last."""
    end = max([at] + [when for when, _ in usages]) + 1
    rows = [_record(at, "turn_start", executionId=f"x{int(at)}")]
    for when, credits in usages:
        rows.append(_record(when, "usage_summary",
                            promptTurnSummaries=[{"unit": "credit", "usage": credits}]))
    rows.append(_record(end, "turn_end", executionId=f"x{int(at)}", stopReason="end_turn"))
    return rows


def _transcript(world, *turns):
    world.transcript.write_text("".join(json.dumps(row) + "\n" for rows in turns for row in rows), encoding="utf-8")


def test_a_slice_past_its_minutes_is_parked_with_the_reading_and_the_architect_is_told(world):
    _running(world)
    cfg = _cfg(world, slice_budget_minutes=30)
    assert W.slice_budget(world.root, cfg, now=time.time() + 31 * 60) is True

    board = A.board(world.root)
    assert board["running"] == []
    assert [item["id"] for item in board["blocked"]] == ["B8"]
    needs = board["blocked"][0]["notes"]["needs"]
    assert needs.startswith("budget (") and needs.endswith("m of 30m)") and "31m" in needs
    (notice,) = world.notices
    assert notice[2] == "architect" and "B8" in notice[0] + notice[1] and "30m" in notice[1]
    assert "the architect or a person gives it more by starting it again" in notice[1]
    assert ("mimar ya da bir kişi dilimi yeniden başlatarak verir"
            in A.language.text({"language": "tr"}, "alarm.slice-budget", reading="B8: 31m of 30m"))
    # The move is the watchdog's, so the implementer may not lift it.
    assert A.slice_moves(world.root)[-1]["by"] == "watchdog"
    assert A.slice_moves(world.root)[-1]["to"] == "blocked"
    # Nothing is moved again while it waits.
    assert W.slice_budget(world.root, cfg, now=time.time() + 3600) is False


def test_a_slice_past_its_spend_is_parked_in_the_unit_its_adapter_records(world):
    _running(world)
    now = time.time()
    _transcript(world, _turn(now + 1, (now + 2, 7.5)))
    cfg = _cfg(world, slice_budget_spend=5.0)
    assert W.slice_budget(world.root, cfg, now=now + 10) is True

    board = A.board(world.root)
    assert board["blocked"][0]["notes"]["needs"] == "budget (spent 7.5 credit of 5)"
    assert world.notices and "spent 7.5 credit of 5" in world.notices[0][1]


def test_a_slice_started_inside_a_turn_counts_that_turns_spend_from_the_slices_start(world):
    _running(world)
    start = A.slice_moves(world.root)[-1]["at"]
    cfg = _cfg(world, slice_budget_spend=5.0)
    # The turn opened before the slice started and spent 6 credits on earlier work, then 3 on this slice.
    _transcript(world, _turn(start - 30, (start - 10, 6.0), (start + 10, 3.0)))
    assert W.slice_budget(world.root, cfg, now=start + 20) is False
    assert A.board(world.root)["running"]

    # Spending another 4.5 credits later in the same turn takes the slice's spend to 7.5, past 5.
    _transcript(world, _turn(start - 30, (start - 10, 6.0), (start + 10, 3.0), (start + 15, 4.5)))
    assert W.slice_budget(world.root, cfg, now=start + 20) is True
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (spent 7.5 credit of 5)"


def test_spend_is_attributed_to_its_slice_and_overlapping_spend_parks_both_slices(world, monkeypatch):
    now = int(time.time())
    world.board("queued", SLICE)
    world.board("queued", SLICE.replace("B8", "B9"))
    monkeypatch.setattr(A.time, "time", lambda: now - 100)
    assert A.board_move(world.root, "B8", "running", by="implementer")
    monkeypatch.setattr(A.time, "time", lambda: now - 50)
    assert A.board_move(world.root, "B9", "running", by="implementer")
    cfg = _cfg(world, slice_budget_spend=5.0)

    # Spend from when B8 ran alone belongs to B8 alone: B8 is parked for it and B9 is not.
    _transcript(world, _turn(now - 90, (now - 80, 7.5)))
    assert W.slice_budget(world.root, cfg, now=now) is True
    assert [item["id"] for item in A.board(world.root)["blocked"]] == ["B8"]
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (spent 7.5 credit of 5)"
    assert [item["id"] for item in A.board(world.root)["running"]] == ["B9"]

    # A turn that ran while both B8 and B9 were running cannot be attributed to either: both are parked.
    monkeypatch.setattr(A.time, "time", lambda: now - 45)
    assert A.board_move(world.root, "B8", "running", by="person")
    _transcript(world, _turn(now - 40, (now - 30, 7.5)))
    world.notices.clear()
    assert W.slice_budget(world.root, cfg, now=now) is True
    board = A.board(world.root)
    assert board["running"] == []
    assert sorted(item["id"] for item in board["blocked"]) == ["B8", "B9"]
    for item in board["blocked"]:
        assert item["notes"]["needs"] == "budget (spend cannot be attributed across B8, B9)"
    (notice,) = world.notices
    assert notice[2] == "architect" and "spend cannot be attributed across B8, B9" in notice[1]


def test_slice_budget_spend_refuses_non_finite_values(world):
    for text in ("nan", "inf", "-inf"):
        assert S.usable("slice_budget_spend", float(text)) is False
        with pytest.raises(ValueError, match="slice_budget_spend must be a finite number of at least 0"):
            S.parse("slice_budget_spend", text)
        cfg = _cfg(world, slice_budget_spend=float(text))
        value, source, problem = S.resolve(cfg, "slice_budget_spend")
        assert (value, source) == (0.0, "default")
        assert "a finite number of at least 0" in problem


def test_with_both_budgets_the_note_names_both_and_under_budget_nothing_moves(world):
    _running(world)
    now = time.time()
    _transcript(world, _turn(now + 1, (now + 2, 90)))
    cfg = _cfg(world, slice_budget_spend=100.0, slice_budget_minutes=60)
    assert W.slice_budget(world.root, cfg, now=now + 5 * 60) is False
    assert A.board(world.root)["running"]

    cfg = _cfg(world, slice_budget_spend=80.0, slice_budget_minutes=4)
    assert W.slice_budget(world.root, cfg, now=now + 5 * 60) is True
    needs = A.board(world.root)["blocked"][0]["notes"]["needs"]
    assert "spent 90 credit of 80" in needs and "5m of 4m" in needs


def test_with_no_budget_nothing_is_read_and_nothing_moves(world, monkeypatch):
    _running(world)
    monkeypatch.setattr(A, "read_tail", lambda *a, **k: pytest.fail("no budget must read no transcript"))
    assert W.slice_budget(world.root, _cfg(world), now=time.time() + 10 ** 6) is False
    assert A.board(world.root)["running"]


def test_a_resumed_slice_starts_its_run_again_and_a_person_can_give_more(world):
    _running(world)
    cfg = _cfg(world, slice_budget_minutes=30)
    assert W.slice_budget(world.root, cfg, now=time.time() + 31 * 60) is True

    # A person lifts the block: the new running run starts now, and the same budget applies to it.
    assert A.board_move(world.root, "B8", "running", by="person")
    assert W.slice_budget(world.root, cfg, now=time.time() + 5 * 60) is False
    assert A.board(world.root)["running"]
    assert W.slice_budget(world.root, cfg, now=time.time() + 32 * 60) is True


def test_a_slice_the_implementer_does_not_hold_is_left_to_its_own_actor(world):
    world.board("queued", SLICE.replace("the slice", "the slice · role: qa"))
    assert A.board_move(world.root, "B8", "running", by="person")
    cfg = _cfg(world, slice_budget_minutes=1)
    assert W.slice_budget(world.root, cfg, now=time.time() + 120) is False
    assert A.board(world.root)["running"]


def test_a_slice_with_no_recorded_start_is_parked_and_names_the_missing_reading(world):
    world.board("running", SLICE)
    cfg = _cfg(world, slice_budget_minutes=30)
    assert W.slice_budget(world.root, cfg, now=time.time() + 60) is True
    board = A.board(world.root)
    assert board["running"] == []
    assert [item["id"] for item in board["blocked"]] == ["B8"]
    assert board["blocked"][0]["notes"]["needs"] == "budget (no recorded start)"
    (notice,) = world.notices
    assert notice[2] == "architect" and "B8: no recorded start" in notice[1]


def _commit_config(world, **values):
    stored = dict(world.cfg, **values)
    stored.pop("root", None)
    path = os.path.join(world.root, ".ao", "config.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    subprocess.run([A.git_binary(), "add", ".ao/config.json"], cwd=world.root, check=True)
    subprocess.run([A.git_binary(), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "cap"], cwd=world.root, check=True)


def test_a_committed_cap_applies_from_head_and_drops_when_head_moves_or_git_fails_while_a_machine_cap_holds(
    world, monkeypatch
):
    monkeypatch.setattr(subprocess, "Popen", REAL_POPEN)
    _running(world)
    assert W._committed_project_config(world.root) == {}
    _commit_config(world, slice_budget_minutes=30)
    assert W._committed_project_config(world.root)["slice_budget_minutes"] == 30

    tampered = _cfg(world, slice_budget_minutes=120)
    assert W.slice_budget(world.root, tampered, now=time.time() + 31 * 60) is True
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (31m of 30m)"

    # Moving HEAD to a commit without the cap drops the committed cap (the documented limit), while a
    # machine cap outside the tree still holds.
    subprocess.run([A.git_binary(), "checkout", "-q", "--detach", "HEAD~1"], cwd=world.root, check=True)
    assert W._committed_project_config(world.root) == {}
    assert A.board_move(world.root, "B8", "running", by="person")
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=0), now=time.time() + 31 * 60) is False
    assert A.board(world.root)["running"]

    S.write_machine({"slice_budget_minutes": 25})
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=0), now=time.time() + 26 * 60) is True
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (26m of 25m)"

    # A real git read failure (unborn HEAD) also reads as no committed cap, while the machine cap still holds.
    subprocess.run([A.git_binary(), "checkout", "-q", "-"], cwd=world.root, check=True)
    assert W._committed_project_config(world.root)["slice_budget_minutes"] == 30
    (Path(world.root) / ".git" / "HEAD").write_text("ref: refs/heads/unborn\n", encoding="utf-8")
    assert W._committed_project_config(world.root) == {}
    S.write_machine({})
    assert A.board_move(world.root, "B8", "running", by="person")
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=0), now=time.time() + 31 * 60) is False
    S.write_machine({"slice_budget_minutes": 25})
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=0), now=time.time() + 26 * 60) is True
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (26m of 25m)"
    assert "prevents self-modification" not in " ".join((W._effective_slice_budgets.__doc__ or "").split())


def test_docs_state_a_committed_cap_counts_while_head_shows_it_and_the_machine_cap_holds_outside_the_tree():
    watchdog_doc = " ".join((ROOT / "docs" / "watchdog.md").read_text(encoding="utf-8").split())
    config_doc = " ".join((ROOT / "docs" / "configuration.md").read_text(encoding="utf-8").split())
    docstring = " ".join((W._effective_slice_budgets.__doc__ or "").split())

    assert "cannot widen or zero a cap set in the machine settings or in a committed" not in watchdog_doc
    for text in (watchdog_doc, config_doc, docstring):
        assert "counts while `HEAD` shows it" in text
        assert "moves `HEAD` or breaks the git read" in text
        assert "no committed cap" in text


def test_a_person_can_widen_or_switch_off_a_budget_without_loading_or_writing_watchdog_state(world, monkeypatch):
    _running(world)
    start = W.budget_start("B8", A.slice_moves(world.root))
    monkeypatch.setattr(W, "load_state", lambda *_a, **_k: pytest.fail("slice_budget must not load watchdog state"))
    monkeypatch.setattr(W, "save_state", lambda *_a, **_k: pytest.fail("slice_budget must not save watchdog state"))
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=30), now=start + 5 * 60) is False

    # Raising 30 to 120 where it is set takes effect on the next cycle instead of staying pinned at 30.
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=120), now=start + 45 * 60) is False
    assert A.board(world.root)["running"]
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=120), now=start + 121 * 60) is True
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (121m of 120m)"

    # Setting 0 everywhere switches the budget off.
    assert A.board_move(world.root, "B8", "running", by="person")
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=0), now=start + 500 * 60) is False
    assert A.board(world.root)["running"]


def test_a_run_one_second_past_its_minute_budget_is_parked(world):
    _running(world)
    start = W.budget_start("B8", A.slice_moves(world.root))
    cfg = _cfg(world, slice_budget_minutes=30)
    assert W.slice_budget(world.root, cfg, now=start + 30 * 60) is False
    assert W.slice_budget(world.root, cfg, now=start + 30 * 60 + 1) is True
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (31m of 30m)"


def test_a_spend_budget_parks_the_slice_when_the_adapter_declares_no_usage_telemetry(world, capsys):
    _running(world)
    cfg = _cfg(world, implementer={"adapter": "command-code", "session": "s1", "name": "cc"},
               slice_budget_spend=5.0, slice_budget_minutes=30)
    assert W.slice_budget(world.root, cfg, now=time.time() + 60) is True
    assert "command-code" in capsys.readouterr().out
    board = A.board(world.root)
    assert board["running"] == []
    assert [item["id"] for item in board["blocked"]] == ["B8"]
    assert board["blocked"][0]["notes"]["needs"] == (
        "budget (adapter 'command-code' declares no transcript usage telemetry)"
    )
    (notice,) = world.notices
    assert notice[2] == "architect" and "adapter 'command-code' declares no transcript usage telemetry" in notice[1]


def test_a_whole_turn_usage_summary_spanning_the_slices_start_is_charged_in_full(world):
    _running(world)
    start = W.budget_start("B8", A.slice_moves(world.root))
    _transcript(world, _turn(start - 30, (start + 10, 50.0)))
    assert W.slice_budget(world.root, _cfg(world, slice_budget_spend=5.0), now=start + 20) is True
    assert A.board(world.root)["running"] == []
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (spent 50 credit of 5)"


def test_a_slice_hand_moved_back_to_running_after_blocking_does_not_reuse_its_old_start(world):
    _running(world)
    assert A.board_move(world.root, "B8", "blocked", {"needs": "question"}, by="implementer")
    world.board("running", SLICE)
    assert W.budget_start("B8", A.slice_moves(world.root)) is None
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=30), now=time.time() + 60) is True
    board = A.board(world.root)
    assert board["running"] == []
    assert board["blocked"][0]["notes"]["needs"] == "budget (no recorded start)"


def test_an_overflowing_json_integer_in_a_float_setting_is_unusable_and_does_not_raise(world):
    _running(world)
    cfg = _cfg(world, slice_budget_spend=10 ** 310, slice_budget_minutes=30)
    assert S.usable("slice_budget_spend", 10 ** 310) is False
    assert S.get(cfg, "slice_budget_spend") == 0.0
    assert W.slice_budget(world.root, cfg, now=time.time() + 31 * 60) is True


def test_a_budget_blocked_slice_hand_moved_back_to_running_stays_past_its_budget(world):
    _running(world)
    cfg = _cfg(world, slice_budget_minutes=30)
    assert W.slice_budget(world.root, cfg, now=time.time() + 31 * 60) is True
    assert [item["id"] for item in A.board(world.root)["blocked"]] == ["B8"]

    open(f"{world.root}/.ao/board.md", "w", encoding="utf-8").write(
        f"# Board\n\n## running\n{SLICE}\n\n## queued\n\n## blocked\n\n## done\n")
    assert W.slice_budget(world.root, cfg, now=time.time() + 32 * 60) is True
    assert A.board(world.root)["running"] == []
    assert [item["id"] for item in A.board(world.root)["blocked"]] == ["B8"]


def test_a_per_response_event_after_the_slices_start_in_an_earlier_turn_is_counted(world):
    _running(world)
    start = W.budget_start("B8", A.slice_moves(world.root))
    stamp = lambda at: datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")
    _transcript(world, [
        {"timestamp": stamp(start - 30), "type": "user", "message": {"content": "nudge"}},
        {"timestamp": stamp(start + 10), "type": "assistant",
         "message": {"id": "msg_1", "stop_reason": "end_turn",
                     "usage": {"input_tokens": 4, "output_tokens": 3}}},
    ])
    cfg = _cfg(world, implementer={"adapter": "claude-code", "session": "s1", "name": "cc"},
               slice_budget_spend=5.0)
    assert W.slice_budget(world.root, cfg, now=start + 20) is True
    assert A.board(world.root)["blocked"][0]["notes"]["needs"] == "budget (spent 7 token of 5)"


def test_an_unreadable_slice_ledger_parks_the_running_slice_and_stands_down(world):
    _running(world)
    open(A.slice_ledger_path(world.root), "w", encoding="utf-8").write("{not json\n")
    world.cfg["slice_budget_minutes"] = 30
    stored = dict(world.cfg)
    stored.pop("root", None)
    open(f"{world.root}/.ao/config.json", "w", encoding="utf-8").write(json.dumps(stored))
    world.process(500, ["/agents/kiro-cli", "chat", "--resume-id", "s1", "--no-interactive", "go"])
    world.transcript_age(0)
    trace = world.cycle(dry_run=False)
    assert trace[-1] == "B8 parked on the budget it was given (B8: the slice ledger could not be read); not nudging"

    board = A.board(world.root)
    assert board["running"] == []
    assert [item["id"] for item in board["blocked"]] == ["B8"]
    assert board["blocked"][0]["notes"]["needs"] == "budget (the slice ledger could not be read)"
    (notice,) = world.notices
    assert notice[2] == "architect" and "B8: the slice ledger could not be read" in notice[1]
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=30), now=time.time() + 120) is True


def test_an_unreadable_transcript_store_parks_the_running_slice_and_names_the_missing_reading(world):
    _running(world)
    world.transcript.unlink()
    world.transcript.mkdir()
    cfg = _cfg(world, slice_budget_spend=5.0)
    assert W.slice_budget(world.root, cfg, now=time.time() + 60) is True

    board = A.board(world.root)
    assert board["running"] == []
    assert [item["id"] for item in board["blocked"]] == ["B8"]
    assert board["blocked"][0]["notes"]["needs"] == "budget (the transcript could not be read)"
    (notice,) = world.notices
    assert notice[2] == "architect" and "B8: the transcript could not be read" in notice[1]


def test_a_busy_implementer_past_its_cap_is_parked_in_the_same_cycle(world, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(A.time, "time", lambda: now - 31 * 60)
    _running(world)
    monkeypatch.setattr(A.time, "time", lambda: now)
    world.cfg["slice_budget_minutes"] = 30
    stored = dict(world.cfg)
    stored.pop("root", None)
    open(f"{world.root}/.ao/config.json", "w", encoding="utf-8").write(json.dumps(stored))
    world.process(500, ["/agents/kiro-cli", "chat", "--resume-id", "s1", "--no-interactive", "go"])
    world.transcript_age(0)

    trace = world.cycle(dry_run=False, idle_minutes=6.0)
    assert trace == ["B8 parked on the budget it was given (B8: 31m of 30m); not nudging"]
    board = A.board(world.root)
    assert board["running"] == []
    assert [item["id"] for item in board["blocked"]] == ["B8"]
    assert board["blocked"][0]["notes"]["needs"] == "budget (31m of 30m)"
    assert not any(
        isinstance(argv, list) and argv and argv[0].endswith(("/kiro-cli", "/claude"))
        for argv in world.spawned
    )


def test_hold_overrides_slice_budget_without_writing_board_ledger_or_alarm(world, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(A.time, "time", lambda: now - 31 * 60)
    _running(world)
    monkeypatch.setattr(A.time, "time", lambda: now)
    world.cfg["slice_budget_minutes"] = 30
    stored = dict(world.cfg)
    stored.pop("root", None)
    open(f"{world.root}/.ao/config.json", "w", encoding="utf-8").write(json.dumps(stored))
    open(f"{world.root}/.ao/hold", "w", encoding="utf-8").write(
        json.dumps({"by": "alice", "reason": "editing board", "at": now - 60, "stopped": []})
    )
    moves_before = A.slice_moves(world.root)

    trace = world.cycle(dry_run=False, idle_minutes=6.0)
    assert trace[-1] == "held by alice for 1m: editing board"
    assert [item["id"] for item in A.board(world.root)["running"]] == ["B8"]
    assert A.board(world.root)["blocked"] == []
    assert A.slice_moves(world.root) == moves_before
    assert not any("budget" in title + msg for title, msg, *_ in world.notices)


def test_implementer_starting_a_budget_parked_slice_is_refused_with_budget_reason(world, monkeypatch, capsys):
    _running(world)
    assert W.slice_budget(world.root, _cfg(world, slice_budget_minutes=30), now=time.time() + 31 * 60) is True
    assert [item["id"] for item in A.board(world.root)["blocked"]] == ["B8"]

    monkeypatch.setenv("AO_ROLE", "implementer")
    rc = cli.cmd_slice(world.cfg, argparse.Namespace(action="start", text="B8", why=None, boundary_file=None))
    out = capsys.readouterr().out
    assert rc == 2
    assert "parked on its budget by the watchdog" in out
    assert "nothing gives it back on its own" in out
    assert "ao slice start B8" in out
    assert "parked on quota" not in out
    assert [item["id"] for item in A.board(world.root)["blocked"]] == ["B8"]

    monkeypatch.delenv("AO_ROLE", raising=False)
    assert cli.cmd_slice(world.cfg, argparse.Namespace(action="start", text="B8", why=None, boundary_file=None)) == 0
    assert [item["id"] for item in A.board(world.root)["running"]] == ["B8"]


def test_implementer_blocking_and_restarting_its_own_slice_does_not_reset_its_budget(world, monkeypatch):
    now = int(time.time())
    t0 = now - 31 * 60
    monkeypatch.setattr(A.time, "time", lambda: t0)
    _running(world)
    S.write_machine({"slice_budget_minutes": 30, "slice_budget_spend": 5.0})

    monkeypatch.setenv("AO_ROLE", "implementer")
    monkeypatch.setattr(A.time, "time", lambda: t0 + 29 * 60)
    assert cli.cmd_slice(world.cfg, argparse.Namespace(action="block", text="x", why=None, slice="B8",
                                                       waiting=None)) == 0
    assert cli.cmd_slice(world.cfg, argparse.Namespace(action="start", text="B8", why=None, boundary_file=None)) == 0

    _transcript(world, _turn(t0 + 10 * 60, (t0 + 10 * 60 + 1, 3.0)), _turn(t0 + 30 * 60, (t0 + 30 * 60 + 1, 3.0)))
    assert W.budget_start("B8", A.slice_moves(world.root)) == t0
    assert W.slice_budget(world.root, _cfg(world), now=now) is True
    board = A.board(world.root)
    assert board["running"] == []
    assert [item["id"] for item in board["blocked"]] == ["B8"]
    assert board["blocked"][0]["notes"]["needs"] == "budget (spent 6 credit of 5; 31m of 30m)"

    monkeypatch.delenv("AO_ROLE", raising=False)
    monkeypatch.setattr(A.time, "time", lambda: now)
    assert cli.cmd_slice(world.cfg, argparse.Namespace(action="start", text="B8", why=None, boundary_file=None)) == 0
    assert W.budget_start("B8", A.slice_moves(world.root)) == now
    assert W.slice_budget(world.root, _cfg(world), now=now + 60) is False
