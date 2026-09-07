"""Each test is a fault from docs/watchdog.md, as the world the cycle saw."""
import pytest

from tests.scenarios import World

KIRO = ["/agents/kiro-cli", "chat", "--resume-id", "s1", "--no-interactive", "devam"]
ENGINE = ["/agents/Application Support/kiro-cli/node", "--experimental-wasm-modules", "x"]


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def test_idle_implementer_with_a_running_slice_is_nudged(world):
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)
    world.cycle()
    assert "nudging" in world.verdict or "DRY RUN" in world.verdict


def test_f12_a_shell_that_mentions_the_agent_is_not_a_turn(world):
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)
    world.process(500, ["/bin/zsh", "-c", "cd repo && ao mail ack kiro-to-fable-x.md >| /tmp/claude-add3-cwd"], headless=False)
    world.cycle()
    assert "already in this tree" not in world.verdict
    assert "nudging" in world.verdict or "DRY RUN" in world.verdict


def test_f10_orphans_are_swept_and_not_counted(world):
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)
    world.process(300, ENGINE, ppid=1, pgid=299)               # leader 299 is dead: an orphan
    trace = world.cycle()
    assert any("orphaned" in line for line in trace)
    assert "already in this tree" not in world.verdict


def test_live_turn_blocks_a_second_one(world):
    world.transcript_age(120).process(600, KIRO)
    world.cycle()
    assert "already in this tree" in world.verdict


def test_f3_hung_turn_is_reaped_after_three_idle_windows(world):
    world.transcript_age(6 * 60 * 3 + 5).process(600, KIRO)
    trace = world.cycle()
    assert any("reaping" in line for line in trace)


def test_turn_closed_by_transcript_is_reaped_at_the_idle_window(world):
    world.transcript_age(400).process(600, KIRO)
    world.turn_ended = True
    trace = world.cycle()
    assert any("linger; reaping" in line for line in trace)


def test_f11_waiting_on_the_architect_is_not_nudged(world):
    world.transcript_age(900)
    world.mail("20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    world.cycle()
    assert "waiting on the architect" in world.verdict


def test_an_unread_answer_overrides_waiting(world):
    world.transcript_age(900)
    world.mail("20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    world.mail("20260905-0600-fable-to-kiro-DECISION-refill.md", "# refill\n\nB7\n")
    world.cycle()
    assert "nudging" in world.verdict or "DRY RUN" in world.verdict


def test_f15_empty_queue_without_a_source_wakes_the_architect(world):
    world.transcript_age(900)
    world.cycle()
    assert "would wake the architect to refill" in world.verdict


def test_live_interactive_architect_suppresses_pending_report_wake(world):
    world.transcript_age(900)
    world.mail("20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    world.architect()

    trace = world.cycle(dry_run=False)

    assert any("architect is already at the keyboard" in line for line in trace)
    assert not any(
        isinstance(argv, list) and argv and argv[0].endswith("/claude")
        for argv in world.spawned
    )


def test_live_interactive_architect_suppresses_refill_before_dry_run(world):
    architect_tree = world.tmp / "architect-worktree"
    world.architect_cwd(architect_tree).architect()
    world.transcript_age(900)

    trace = world.cycle()

    assert any("queue low, but the architect is already at the keyboard" in line for line in trace)
    assert not any("would wake the architect to refill" in line for line in trace)


def test_headless_registered_architect_helper_has_same_dry_and_live_verdict(world):
    architect_tree = world.tmp / "architect-worktree"
    world.architect_cwd(architect_tree).architect(
        pid=730,
        argv=["/agents/claude", "-p", "triage"],
    ).helper(730)
    world.transcript_age(900)

    dry = world.cycle()
    live = world.cycle(dry_run=False)

    assert "queue low, but an architect wake is already running" in dry[-1]
    assert "queue low, but an architect wake is already running" in live[-1]
    assert not any(
        isinstance(argv, list) and argv and argv[0].endswith("/claude")
        for argv in world.spawned
    )


def test_headless_tree_through_nonmatching_process_does_not_suppress_refill(world):
    architect_tree = world.tmp / "architect-worktree"
    world.architect_cwd(architect_tree)
    world.process(750, ["/agents/claude", "--print", "triage"], cwd=architect_tree)
    world.process(751, ["/bin/sh", "-c", "runtime"], cwd=architect_tree, ppid=750)
    world.process(
        752,
        ["/agents/node", "/opt/claude-code/engine.js"],
        cwd=architect_tree,
        ppid=751,
    )
    world.transcript_age(900)

    trace = world.cycle()

    assert "would wake the architect to refill" in world.verdict
    assert not any("architect is already at the keyboard" in line for line in trace)


def test_headless_tree_through_nonmatching_process_is_not_report_presence(world):
    architect_tree = world.tmp / "architect-worktree"
    world.architect_cwd(architect_tree)
    world.process(760, ["/agents/claude", "--no-interactive", "triage"], cwd=architect_tree)
    world.process(761, ["/bin/sh", "-c", "runtime"], cwd=architect_tree, ppid=760)
    world.process(
        762,
        ["/agents/node", "/opt/claude-code/engine.js"],
        cwd=architect_tree,
        ppid=761,
    )
    world.mail("20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    world.transcript_age(900)

    trace = world.cycle(dry_run=False)

    assert not any("architect is already at the keyboard" in line for line in trace)
    assert any(
        isinstance(argv, list) and argv and argv[0].endswith("/claude")
        for argv in world.spawned
    )


def test_reused_remembered_architect_pid_does_not_delay_report_wake(world):
    from ao import lib as A
    from ao import watchdog as W

    architect_tree = world.tmp / "architect-worktree"
    world.architect_cwd(architect_tree).architect(pid=740)
    world.mail("20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    world.transcript_age(900)
    first = world.cycle(dry_run=False)
    assert any("architect is already at the keyboard" in line for line in first)

    # The architect exits and an unrelated process reuses its pid. Neither the
    # remembered state nor the live-but-stale lock may become presence.
    world.process(740, ["/bin/sleep", "100"], cwd=architect_tree, headless=False)
    state = W.load_state(world.root)
    state["arch_pid"] = 740
    W.save_state(world.root, state)
    A.acquire_architect(world.root, 740, "stale watchdog wake")
    world.spawned.clear()

    world.cycle(dry_run=False)

    assert any(
        isinstance(argv, list) and argv and argv[0].endswith("/claude")
        for argv in world.spawned
    )


def test_dead_architect_releases_refill_on_the_next_cycle(world):
    architect_tree = world.tmp / "architect-worktree"
    world.architect_cwd(architect_tree).architect(pid=740)
    world.transcript_age(900)
    first = world.cycle()
    assert any("architect is already at the keyboard" in line for line in first)

    world.procs.pop(740)
    second = world.cycle()

    assert "would wake the architect to refill" in second[-1]


def test_f16_architect_at_quota_is_not_woken(world):
    from ao import watchdog as W
    world.transcript_age(900)
    world.mail("20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    st = W.load_state(world.root)
    st["arch_quota_until"] = __import__("time").time() + 3600
    W.save_state(world.root, st)
    trace = world.cycle(dry_run=False)
    assert any("architect at quota" in line for line in trace)


def test_implementer_quota_defers_the_nudge(world):
    from ao import lib as A
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)
    world.quota = False
    world.cycle()
    assert not any(r["kind"] == "nudge" for r in A.deferred_open(world.root))
    world.cycle(dry_run=False)
    assert any(r["kind"] == "nudge" for r in A.deferred_open(world.root))


def test_reviewer_window_reopened_is_open_work(world):
    from ao import lib as A
    world.transcript_age(700)
    A.set_reviewer_state(world.root, pending_review=True, until=__import__("time").time() - 5)
    world.board("queued", "- [B9] later · acceptance: #9")
    trace = world.cycle()
    assert any("reviewer window reopened" in line for line in trace)
