import time

import pytest

from ao import watchdog as W
from tests.scenarios import World


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _at(text):
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))


def test_a_clock_reset_is_not_pushed_past_its_window_when_the_line_is_read_again():
    line = "You've hit your session limit · resets 9:20pm"
    reset = _at("2026-09-07 21:20")

    assert W.parse_reset(line, now=_at("2026-09-07 17:48"), window=W.ARCHITECT_QUOTA_WINDOW) == reset
    assert W.parse_reset(line, now=_at("2026-09-07 23:00"), window=W.ARCHITECT_QUOTA_WINDOW) == reset
    assert W.parse_reset("resets 12:10am", now=_at("2026-09-07 23:30"),
                         window=W.ARCHITECT_QUOTA_WINDOW) == _at("2026-09-08 00:10")


def test_an_old_quota_line_does_not_raise_the_block_again_after_the_state_is_cleared(tmp_path):
    log = tmp_path / "escalate-proj.log"
    stale = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 6 * 3600))
    log.write_text(f"\n=== {stale} escalate /agents/claude 2.1.261 ===\nYou've hit your session limit\n",
                   encoding="utf-8")

    old = W.wake_error(str(log))

    assert old["kind"] == "quota" and W.quota_block_until(old) is None

    fresh = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 600))
    log.write_text(f"\n=== {fresh} escalate /agents/claude 2.1.261 ===\n"
                   "You've hit your session limit · resets in 1h\n", encoding="utf-8")
    new = W.wake_error(str(log))
    assert W.quota_block_until(new) == pytest.approx(new["at"] + 3600)


def test_a_present_architect_clears_a_cached_quota_block(world):
    W.save_state(world.root, {"attempts": 0, "arch_quota_until": time.time() + 3 * 3600})
    world.architect()

    trace = world.cycle(dry_run=False)

    assert any("cached quota block is cleared" in line for line in trace)
    assert "arch_quota_until" not in W.load_state(world.root)
