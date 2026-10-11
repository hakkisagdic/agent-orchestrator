"""What a stop leaves running is named, never taken as stopped (WINDOWS-STOP-PROVEN).

`ao hold` on Windows stopped each turn with `taskkill /T /F` and said every tree was stopped, whatever
taskkill did: it is refused a process another user or an elevated prompt started, and it finds a
process's children by the parent each names, so a child whose parent had already exited - a turn's
wrapper ending as it was stopped - was left running. The watchdog's reaper then started a turn beside
the one it could not stop, `ao review cancel` recorded a reviewer stopped that worked on, and a harness
probe, a codebase provider, a lane's post-create command and a filter hook each said they stopped what
they started.

Each stop now names every process of the tree, as the table read it just before, to taskkill, asks
each of them afterwards whether it runs, and returns what does. `ao hold` reads the table again, names
what still runs and exits 1; the reaper stands down and tells a person (`reap-survived`); `ao review
cancel` records nothing over a runner that runs on and says which reviewer it could not stop; the others
say what still runs. A process older than the parent it names belongs to an earlier process that had
that pid, and is no part of the tree. Windows is made here on every platform: its system directory, its
taskkill and its answers are the test's own.
"""
import json
import os
import re
import signal
import sys
import time
from types import SimpleNamespace

import pytest

from ao import acp, cli, lib as A, procs
from ao import watchdog as W
from tests.scenarios import World
from tests.test_acp_probe import _adapter, _probe, agent  # noqa: F401  (agent is a fixture used here)

KIRO = ["/agents/kiro-cli", "chat", "--resume-id", "s1", "--no-interactive", "devam"]
HOLD = SimpleNamespace(action="hold", by="a person", reason="editing", grace=0, note=None)
BY_HAND_ON_WINDOWS = ("stop them by hand with `taskkill /T /F /PID <pid>`, from a prompt of the user that started "
                      "them, or an elevated one where access is denied")


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _as_windows(monkeypatch, ask):
    """What `ask` returns where os.name is Windows' - only while it asks, so a failure is reported in this
    platform's paths."""
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        return ask()


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """Windows' own taskkill and what it was asked, the process table a stop is read against, each process's
    age, and what still runs after taskkill: all the test's own."""
    system = tmp_path / "Windows" / "System32"
    system.mkdir(parents=True)
    (system / "taskkill.exe").write_bytes(b"MZ")
    monkeypatch.setenv("SystemRoot", str(system.parent))
    world = SimpleNamespace(taskkill=str(system / "taskkill.exe"), asked=[], table={}, ages={}, running=set())
    monkeypatch.setattr(A.subprocess, "run", lambda argv, **kwargs: world.asked.append(argv)
                        or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(procs, "refresh", lambda: None)
    monkeypatch.setattr(A, "_proc_table", lambda: dict(world.table))
    monkeypatch.setattr(procs, "elapsed", lambda pid: world.ages.get(pid))
    monkeypatch.setattr(procs, "alive", lambda pid: pid in world.running)
    monkeypatch.setattr(A, "STOP_PROOF_SECONDS", 0.3)
    return world


def _taskkill(windows, *pids):
    return [windows.taskkill, "/T", "/F"] + [word for pid in pids for word in ("/PID", str(pid))]


# ---- the stop: every process of the tree named, each asked afterwards ----------------------------------------

def test_every_process_of_the_tree_is_named_to_taskkill_and_what_still_runs_is_returned(windows, monkeypatch):
    # 4242 is the turn's wrapper, 4243 its runtime, 4244 an engine an elevated prompt started; 5000 is not of it.
    windows.table.update({4242: (1, 4242, "1"), 4243: (4242, 4243, "1"), 4244: (4243, 4244, "1"),
                          5000: (1, 5000, "1")})
    windows.running.update({4244, 5000})                    # taskkill was refused the engine

    left = _as_windows(monkeypatch, lambda: A.kill_turn(4242, signal.SIGTERM))

    assert windows.asked == [_taskkill(windows, 4242, 4243, 4244)]
    assert left == [4244]


def test_a_child_whose_parent_had_exited_is_named_to_taskkill_by_its_own_pid(windows, monkeypatch):
    # The wrapper ended as it was stopped: /T on its pid finds nothing, and its runtime still names it.
    windows.table.update({4243: (4242, 4243, "1"), 4244: (4243, 4244, "1")})

    left = _as_windows(monkeypatch, lambda: A.stop_windows_tree(4242))

    assert windows.asked == [_taskkill(windows, 4242, 4243, 4244)]
    assert left == []


def test_a_process_older_than_the_parent_it_names_is_no_part_of_the_tree(windows, monkeypatch):
    # 900 was started a day ago by an earlier process that had pid 4242; this turn started a minute ago.
    windows.table.update({4242: (1, 4242, "1"), 900: (4242, 900, "1"), 4243: (4242, 4243, "1")})
    windows.ages.update({4242: 60, 4243: 59, 900: 86400})
    windows.running.add(900)

    left = _as_windows(monkeypatch, lambda: A.kill_turn(4242, signal.SIGTERM))

    assert windows.asked == [_taskkill(windows, 4242, 4243)]
    assert left == []
    assert _as_windows(monkeypatch, lambda: A.process_tree(4242)) == [4242, 4243]


def test_a_process_nothing_answers_for_after_the_stop_is_taken_as_still_running(windows, monkeypatch):
    def unread():
        raise procs.TableUnreadable("powershell was not found", "Windows")
    monkeypatch.setattr(procs, "alive", lambda pid: None)
    monkeypatch.setattr(procs, "all_pids", unread)

    assert _as_windows(monkeypatch, lambda: A.kill_turn(4242, signal.SIGTERM)) == [4242]


def test_the_orphan_sweep_returns_what_it_could_not_stop(windows, monkeypatch):
    windows.table.update({300: (1, 300, "1"), 301: (300, 301, "1"), 400: (1, 400, "1")})
    windows.running.add(301)

    left = _as_windows(monkeypatch, lambda: A.sweep_orphans([300, 400]))

    assert windows.asked == [_taskkill(windows, 300, 301), _taskkill(windows, 400)]
    assert left == [301]


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
def test_a_forced_stop_elsewhere_returns_what_of_its_tree_still_runs_and_a_request_returns_nothing(windows,
                                                                                                    monkeypatch):
    windows.table.update({4242: (1, 4242, "??"), 4243: (4242, 4243, "??")})
    sent = []
    monkeypatch.setattr(A.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(A.os, "killpg", lambda group, sig: sent.append((group, sig)))
    monkeypatch.setattr(A, "_pid_alive", lambda pid: pid == 4243)       # a child that left the group

    assert A.kill_turn(4242, signal.SIGTERM) == []                     # asked: the caller waits, and looks
    assert A.kill_turn(4242, signal.SIGKILL) == [4243]
    assert sent == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]


# ---- ao hold -----------------------------------------------------------------------------------------------

def _hold_as_windows(project, monkeypatch, running, refused=()):
    """`ao hold` as Windows runs it, on the turns in `running`: taskkill stops each of them but those `refused`,
    which still run after it. Returns the exit status."""
    A.project_key(project["root"])                     # registered first, as every test that locks under "nt"

    def kill_turn(pid, sig):
        if pid in refused:
            return [pid]
        if pid in running:
            running.remove(pid)
        return []
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, headless_only=False: list(running))
    monkeypatch.setattr(A, "orphans", lambda root, adapter, table=None: [])
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root, adapter: [])
    monkeypatch.setattr(A, "kill_turn", kill_turn)
    monkeypatch.setattr(cli, "_alive", lambda pid: pid in running)
    monkeypatch.setattr(A, "_pid_alive", lambda pid: pid in running)
    with monkeypatch.context() as patch:
        # Windows locks a byte with msvcrt, which no other platform has; a lock no other process takes here.
        patch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=lambda *args: None))
        patch.setattr(os, "name", "nt")
        return cli.cmd_hold(project, HOLD)


def test_a_hold_names_the_turn_taskkill_left_running_and_exits_1(project, monkeypatch, capsys):
    assert _hold_as_windows(project, monkeypatch, [4242, 4343], refused=(4343,)) == 1

    out = _plain(capsys)
    assert "HELD — 1 process tree(s) stopped by force (Windows)." in out
    assert ("1 process(es) still run in this tree after the hold: [4343]. The hold stands and the watchdog starts "
            f"nothing beside them; {BY_HAND_ON_WINDOWS} - `ao hold status` shows what still runs") in out
    assert A.hold_state(project["root"])["stopped"] == [4242, 4343]


def test_a_hold_that_stopped_every_tree_says_so_and_exits_0(project, monkeypatch, capsys):
    assert _hold_as_windows(project, monkeypatch, [4242, 4343]) == 0

    out = _plain(capsys)
    assert "HELD — 2 process tree(s) stopped by force (Windows)." in out and "still run" not in out


def test_a_hold_reads_the_table_again_and_names_a_turn_its_stop_said_was_gone(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, headless_only=False: [4242])    # it runs on
    monkeypatch.setattr(A, "orphans", lambda root, adapter, table=None: [])
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root, adapter: [])
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: [])
    monkeypatch.setattr(cli, "_alive", lambda pid: False)

    assert cli.cmd_hold(project, HOLD) == 1

    out = _plain(capsys)
    assert "0 exited on request" in out or "0 process tree(s) stopped" in out
    assert "1 process(es) still run in this tree after the hold: [4242]. The hold stands" in out


def test_a_hold_names_what_its_orphan_sweep_left_running(project, monkeypatch, capsys):
    dead = [300]
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, headless_only=False: [])
    monkeypatch.setattr(A, "orphans", lambda root, adapter, table=None: list(dead))
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root, adapter: [])
    monkeypatch.setattr(A, "sweep_orphans", lambda pids, grace=3.0: dead.clear() or [301])   # its child, refused
    monkeypatch.setattr(A, "_pid_alive", lambda pid: pid == 301)

    assert cli.cmd_hold(project, HOLD) == 1

    out = _plain(capsys)
    assert "clearing 1 orphaned process(es) left by ended turns: [300]" in out
    assert "hold set — no agent turn was running" in out
    assert (f"1 process(es) still run in this tree after the hold: [301]. The hold stands and the watchdog starts "
            f"nothing beside them; {cli._stop_by_hand()} - `ao hold status` shows what still runs") in out


def test_writers_clean_says_what_its_sweep_left_running(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "writers", lambda root, adapter: ([], [300]))
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root, adapter: [])
    monkeypatch.setattr(A, "sweep_orphans", lambda pids, grace=3.0: [301])
    monkeypatch.setattr(A, "_proc_table", lambda: {})

    assert cli.cmd_writers(project, SimpleNamespace(clean=True, json=False)) == 0

    out = _plain(capsys)
    assert "cleaned 1 orphan(s): [300]" in out
    assert ("1 process(es) left by ended turns still run after they were stopped, not counted: [301]; "
            f"{cli._stop_by_hand()}") in out


# ---- the watchdog's reaper ---------------------------------------------------------------------------------

class _Unhurried:
    """The time module, but a sleep returns at once: the reaper waits ten seconds for a turn that will not go."""

    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def sleep(seconds):
        return None


def test_the_reaper_starts_no_turn_beside_one_it_could_not_stop_and_tells_a_person(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(6 * 60 * 3 + 5).process(600, KIRO)
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: [pid])         # taskkill was refused it
    monkeypatch.setattr(W, "time", _Unhurried())

    trace = world.cycle(dry_run=False)

    assert "1 unattended turn process(es) still run after reaping ([600]); not starting another turn" in trace
    assert [argv for argv in world.spawned if isinstance(argv, list) and str(argv[0]).startswith("/agents/")] == []
    assert [(title, audience) for title, _, audience, _ in world.notices
            if title.endswith("a hung turn could not be stopped")] == [("proj: a hung turn could not be stopped",
                                                                        "human")]


# ---- ao review cancel and the reviewers ---------------------------------------------------------------------

def _running_review(root, **fields):
    cli._write_review_state(root, dict({"id": "R-1", "state": "running", "submitted_at": int(time.time()) - 600,
                                        "pid": 4242}, **fields))


def test_review_cancel_records_nothing_over_a_runner_that_still_runs(project, monkeypatch, capsys):
    root = project["root"]
    _running_review(root)
    monkeypatch.setattr(cli, "_review_runner", lambda state: 4242)
    monkeypatch.setattr(cli, "_live_reviewer", lambda state: None)
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: [pid])          # refused, each time
    monkeypatch.setattr(A, "_pid_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(cli, "REVIEW_CANCEL_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(A, "STOP_PROOF_SECONDS", 0.2)

    assert cli.cmd_review_cancel(project, SimpleNamespace(rid="R-1")) == 1

    assert _plain(capsys).strip() == ("R-1 is not cancelled: its runner, pid 4242, still runs after it was stopped, "
                                      f"with [4242]; {cli._stop_by_hand()}, then `ao review cancel R-1` again")
    assert cli._review_state(root, "R-1")["state"] == "running"


def test_review_cancel_says_which_reviewer_it_could_not_stop_and_keeps_its_place(project, monkeypatch, capsys):
    root = project["root"]
    _running_review(root, reviewer={"pid": 4343, "start": "its start"})
    released = []
    monkeypatch.setattr(cli, "_review_runner", lambda state: None)      # the run was killed outright
    monkeypatch.setattr(cli, "_live_reviewer", lambda state: 4343)
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: [pid])          # an elevated prompt started it
    monkeypatch.setattr(A, "_pid_alive", lambda pid: pid == 4343)
    monkeypatch.setattr(A, "helper_release", lambda root_, pid: released.append(pid))
    monkeypatch.setattr(A, "STOP_PROOF_SECONDS", 0.2)

    assert cli.cmd_review_cancel(project, SimpleNamespace(rid="R-1")) == 1

    reason = ("cancelled by `ao review cancel`: its runner was already gone; its reviewer, pid 4343, still runs: it "
              "could not be stopped")
    assert _plain(capsys).splitlines() == [
        f"R-1 cancelled: {reason}",
        f"1 process(es) of the review still run after the cancel: [4343]; {cli._stop_by_hand()}"]
    state = cli._review_state(root, "R-1")
    assert (state["state"], state["reason"]) == ("failed", reason) and released == []


def test_a_reviewer_stopped_on_its_timeout_says_what_of_it_still_runs(monkeypatch, capsys):
    reviewer = SimpleNamespace(pid=4242, kill=lambda: None, communicate=lambda timeout=None: ("", ""),
                               stdout=None, stderr=None)
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: [4243, 4244])

    assert cli._reviewer_kill_and_drain(reviewer) == ("", "")

    assert _plain(capsys).strip() == ("reviewer pid 4242: 2 process(es) of it still run after it was stopped: "
                                      f"[4243, 4244]; {cli._stop_by_hand()}")


def test_an_acp_agents_stop_returns_what_of_it_still_runs_and_a_session_keeps_its_first(monkeypatch):
    agent = SimpleNamespace(poll=lambda: None, pid=4242, stdin=None, stdout=None, wait=lambda timeout=None: 0,
                            kill=lambda: None)
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: [4343])

    assert acp._stop(agent) == [4343]

    stops = [[4343], []]
    monkeypatch.setattr(acp, "_stop", lambda proc: stops.pop(0))
    session = object.__new__(acp.Session)
    session.proc, session.still_running = agent, None
    assert session.close() == [4343] and session.close() == [4343]


def test_a_harness_probe_that_leaves_its_agent_running_says_so_and_exits_1(project, agent, monkeypatch, capsys):
    argv, _, _, _ = agent
    _adapter(project, "fake", argv)
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: [4343])         # its engine, which the stop missed

    assert _probe(project, "fake") == 1

    assert (f"fake         1 process(es) of its agent still run after the probe stopped it: [4343]; "
            f"{cli._stop_by_hand()}") in _plain(capsys)
    profile = json.loads(open(cli.harness_profile_path("fake"), encoding="utf-8").read())
    assert "still_running" not in profile


# ---- what ao starts and stops on its own: a provider, a lane's command, a filter hook -------------------------

def test_a_codebase_provider_stopped_on_its_timeout_says_what_of_it_still_runs(tmp_path, monkeypatch):
    stop = A.kill_turn
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: (stop(pid, sig) or []) + [4343])

    with pytest.raises(ValueError) as caught:
        A._run_codebase_provider([sys.executable, "-c", "import time; time.sleep(30)"], str(tmp_path), 1)

    assert str(caught.value) == ("the codebase provider did not answer within 1s, and nothing was answered: it was "
                                 "stopped, and 1 process(es) it started still run: [4343] - stop them by hand")


def test_a_lane_command_that_ran_past_its_time_says_what_of_it_still_runs(project, monkeypatch, capsys):
    from tests.test_lanes import _args, _board
    _board(project["root"])
    stop = A._kill_lane_group
    monkeypatch.setattr(A, "_kill_lane_group", lambda pid: stop(pid) + [4343])     # what taskkill was refused
    hung = dict(project, lane={"post_create": [sys.executable, "-c", "import time; time.sleep(30)"],
                               "post_create_timeout": 1})

    assert cli.cmd_lane(hung, _args("start", "B")) == 1

    assert ("ran past 1 seconds, and 1 process(es) of it still run after it was stopped: [4343] - stop them by hand "
            "before the lane is removed") in _plain(capsys)


def test_a_filter_hook_cut_off_says_what_of_it_still_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "stop_windows_tree",
                        lambda pid: os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM)) or [4343])
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]

    answer = _as_windows(monkeypatch, lambda: A._run_hook(argv, b"{}", str(tmp_path), dict(os.environ), 1))

    assert answer == (None, b"", b"",
                      "it did not answer within 1s - and 1 process(es) of it still run after it was stopped, [4343] -")
