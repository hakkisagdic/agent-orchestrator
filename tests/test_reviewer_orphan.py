"""Stopping a submitted review stops the reviewer it started (REVIEWER-ORPHAN).

A review's run was stopped by hand and its reviewer worked on for nobody: the reviewer leads a session of
its own, so that a timeout can stop it with everything it started, and for the same reason nothing sent to
the run reached it. The run now stops its reviewer on the signals that stop it, a run killed outright
leaves its reviewer named in its state, and `ao review cancel` stops the one or the other.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, procs
from tests.test_review_chain import _repo_with_change

# A reviewer that, when AO_TEST_PID names a file, writes its pid there and works for two minutes; else it
# approves at once.
REVIEWER = """
import os, time
if os.environ.get("AO_TEST_PID"):
    with open(os.environ["AO_TEST_PID"] + ".part", "w") as fh:
        fh.write(str(os.getpid()))
    os.replace(os.environ["AO_TEST_PID"] + ".part", os.environ["AO_TEST_PID"])
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(0.05)
for line in ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0"):
    print(line)
"""
POSIX = pytest.mark.skipif(os.name == "nt", reason="the run is stopped by POSIX signals here")


def _args(**kw):
    base = dict(action=None, rid=None, any=False, run=None, boundary="b", paths=None, commits=None, timeout=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _with_reviewer(project):
    reviewer = {"id": "r1", "family": "x", "argv": [sys.executable, "-c", REVIEWER, "{prompt}"]}
    path = os.path.join(project["root"], ".ao", "config.json")
    with open(path, encoding="utf-8") as fh:
        config = json.load(fh)
    config["reviewer"] = reviewer
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return dict(project, reviewer=reviewer)


def _until(condition, seconds=120.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


def _log(root, rid):
    try:
        with open(os.path.join(root, ".ao", "reviews", f"{rid}.log"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return "(no log)"


def _reviewer_at_work(project, tmp_path, monkeypatch, capsys):
    """Submit the staged candidate with its run detached, as ever, and wait for its reviewer to work.

    The run is an ao of its own, which finds its home in HOME: it is given the home this test's ao
    reads, so both keep the helper registry in the same place.
    """
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    pid_file = tmp_path / "reviewer.pid"
    monkeypatch.setenv("AO_TEST_PID", str(pid_file))
    monkeypatch.setenv("HOME", A.HOME)
    assert cli.cmd_review(cfg, _args(action="submit")) == 0
    rid = _plain(capsys).split()[0]
    assert _until(lambda: pid_file.exists()), _log(root, rid)
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert _until(lambda: (cli._review_state(root, rid).get("reviewer") or {}).get("pid") == pid), _log(root, rid)
    return cfg, rid, pid


def _reaped(pid):
    """Wait for a run this test started, as the init process waits for one whose submit has exited.

    Unreaped, a run killed outright stays a zombie of this process, which a liveness probe can read
    either way for a moment.
    """
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass                                        # reaped already, by subprocess's own clean-up
    return not A._pid_alive(pid)


def _stop_leftovers(*pids):
    for pid in pids:
        if pid and A._pid_alive(pid):
            A.kill_turn(pid, signal.SIGKILL)


@POSIX
def test_a_signal_that_stops_the_run_stops_its_reviewer_and_says_so(project, tmp_path, monkeypatch, capsys):
    cfg, rid, reviewer = _reviewer_at_work(project, tmp_path, monkeypatch, capsys)
    root = cfg["root"]
    runner = cli._review_state(root, rid)["pid"]
    try:
        procs.refresh()                             # this process's table was read before the reviewer began
        assert A.helper_pids(root, "reviewer") == {reviewer}
        os.kill(runner, signal.SIGTERM)

        assert _until(lambda: cli._review_state(root, rid)["state"] != "running"), _log(root, rid)
        assert _until(lambda: not A._pid_alive(reviewer))
        state = cli._review_state(root, rid)
        assert state["state"] == "failed"
        assert state["reason"] == f"the run was stopped by SIGTERM; its reviewer, pid {reviewer}, was stopped with it"
        assert "reviewer" not in state and "phase" not in state
        assert A.helper_pids(root, "reviewer") == set()
        assert re.search(rf"\d\d:\d\d:\d\d {rid} stopped by SIGTERM", _log(root, rid))
    finally:
        _stop_leftovers(reviewer, runner)


@POSIX
def test_a_run_killed_outright_leaves_its_reviewer_named_and_review_cancel_stops_it(
        project, tmp_path, monkeypatch, capsys):
    cfg, rid, reviewer = _reviewer_at_work(project, tmp_path, monkeypatch, capsys)
    root = cfg["root"]
    runner = cli._review_state(root, rid)["pid"]
    try:
        os.kill(runner, signal.SIGKILL)
        assert _reaped(runner)
        assert A._pid_alive(reviewer)               # its own session: the kill did not reach it

        assert cli.cmd_reviews(cfg, SimpleNamespace()) == 0
        shown = _plain(capsys)
        assert re.search(rf"{rid}\s+lost\s", shown)
        assert f"its reviewer, pid {reviewer}, runs on without it: `ao review cancel {rid}` stops it" in shown

        assert cli.cmd_review(cfg, _args(action="cancel", rid=rid)) == 0
        reason = f"cancelled by `ao review cancel`: its runner was already gone; its reviewer, pid {reviewer}, was stopped"
        assert _plain(capsys).strip() == f"{rid} cancelled: {reason}"
        assert _until(lambda: not A._pid_alive(reviewer))
        state = cli._review_state(root, rid)
        assert (state["state"], state["reason"]) == ("failed", reason)
        assert "reviewer" not in state and A.helper_pids(root, "reviewer") == set()
    finally:
        _stop_leftovers(reviewer, runner)


@POSIX
def test_review_cancel_asks_a_live_run_first_and_the_run_stops_its_reviewer(project, tmp_path, monkeypatch, capsys):
    cfg, rid, reviewer = _reviewer_at_work(project, tmp_path, monkeypatch, capsys)
    root = cfg["root"]
    runner = cli._review_state(root, rid)["pid"]
    try:
        assert cli.cmd_review(cfg, _args(action="cancel", rid=rid)) == 0

        reason = f"the run was stopped by SIGTERM; its reviewer, pid {reviewer}, was stopped with it"
        assert _plain(capsys).strip() == f"{rid} cancelled: {reason}"
        assert _until(lambda: not A._pid_alive(reviewer))
        assert cli._review_state(root, rid)["reason"] == reason
    finally:
        _stop_leftovers(reviewer, runner)


def test_review_cancel_says_why_it_cancels_nothing(project, capsys):
    root = project["root"]
    cli._write_review_state(root, {"id": "R-1", "state": "finished", "verdict": "APPROVED"})

    assert cli.cmd_review(project, _args(action="cancel", rid="R-1")) == 1
    assert _plain(capsys).strip() == "R-1 has ended: finished; nothing to cancel"
    assert cli.cmd_review(project, _args(action="cancel", rid="R-2")) == 2
    assert _plain(capsys).strip() == "no review R-2"
    assert cli.cmd_review(project, _args(action="cancel")) == 2
    assert "needs a review's id" in _plain(capsys)


def test_a_pid_that_is_no_longer_the_run_or_the_reviewer_is_left_alone(project, capsys):
    """A pid is given to another process once its own is gone: neither is signalled on the pid alone."""
    root = project["root"]
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert _until(lambda: A._process_start(other.pid, refresh=True) is not None)
        cli._write_review_state(root, {"id": "R-1", "state": "running", "submitted_at": int(time.time()) - 600,
                                       "pid": other.pid, "reviewer": {"pid": other.pid, "start": "not its start"}})

        assert cli.cmd_review(project, _args(action="cancel", rid="R-1")) == 0

        assert _plain(capsys).strip() == "R-1 cancelled: cancelled by `ao review cancel`: its runner was already gone"
        assert other.poll() is None
        assert cli._review_state(root, "R-1")["state"] == "failed"
    finally:
        other.kill()
        other.wait()


def test_a_signal_after_the_run_recorded_its_end_leaves_that_end(project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    spawned = []
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root, rid: spawned.append(rid))
    assert cli.cmd_review(cfg, _args(action="submit")) == 0
    before = signal.getsignal(signal.SIGTERM)

    def late(root, state):
        raise cli.ReviewRunStopped(signal.SIGTERM)

    monkeypatch.setattr(cli, "_review_finished_event", late)

    assert cli.cmd_review(cfg, _args(run=spawned[0], boundary=None)) == 128 + signal.SIGTERM

    state = cli._review_state(root, spawned[0])
    assert (state["state"], state["verdict"]) == ("finished", "APPROVED")
    assert signal.getsignal(signal.SIGTERM) is before           # put back as the run found it


@POSIX
def test_a_review_in_the_foreground_stopped_by_a_signal_stops_its_reviewer(project, tmp_path):
    """REVIEWER-ORPHAN-3: only a submitted review's detached run stopped its reviewer on SIGTERM. `ao review` and
    `ao catchup` in the foreground, stopped so, left their reviewer - which leads a session of its own - working
    for nobody, and a hosted reviewer spent its credits on it."""
    root = project["root"]
    _repo_with_change(root)
    _with_reviewer(project)
    pid_file = tmp_path / "reviewer.pid"
    env = dict(os.environ, AO_TEST_PID=str(pid_file), HOME=A.HOME)
    run = subprocess.Popen([sys.executable, "-m", "ao", "-C", root, "review", "--boundary", "b"], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reviewer = None
    try:
        assert _until(lambda: pid_file.exists() or run.poll() is not None), "the review ended before its reviewer began"
        reviewer = int(pid_file.read_text(encoding="utf-8"))
        run.send_signal(signal.SIGTERM)
        run.wait(timeout=60)

        assert _until(lambda: not A._pid_alive(reviewer), 30)
        assert run.returncode != 0
    finally:
        _stop_leftovers(reviewer, run.pid if run.poll() is None else None)


def _signalled_while_starting(started, real):
    """`real`, a reviewer's start, with the stop signal's handler run after the child is made and before the start
    returns, as Python runs it when the signal arrives there."""
    def start(*args, **kw):
        made = real(*args, **kw)
        started.append(made)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return made
    return start


def test_a_signal_that_comes_while_the_reviewer_starts_stops_it_once_ao_holds_it(project, monkeypatch):
    """REVIEWER-ORPHAN-4: a stop raised inside the reviewer's start, after its child was made, escaped before ao
    held the child, and the reviewer - leading a session of its own - worked on for nobody."""
    started, before = [], signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(cli.subprocess, "Popen", _signalled_while_starting(started, cli.subprocess.Popen))

    with pytest.raises(cli.ReviewRunStopped):
        cli._run_reviewer(project["root"], [sys.executable, "-c", "import time; time.sleep(120)"], 60)

    (proc,) = started
    assert _until(lambda: proc.poll() is not None, 30)
    assert signal.getsignal(signal.SIGTERM) is before


def test_a_signal_that_comes_while_an_acp_reviewer_starts_stops_it_once_ao_holds_it(project, monkeypatch):
    """REVIEWER-ORPHAN-4: the same, for a reviewer ao reaches through ACP."""
    from ao import acp
    started, before = [], signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(acp, "Session", _signalled_while_starting(started, acp.Session))

    with pytest.raises(cli.ReviewRunStopped):
        cli._run_acp_reviewer(project["root"], [sys.executable, "-c", "import time; time.sleep(120)"], "acp-test",
                              "review this", 60, "acp-test")

    (session,) = started
    assert _until(lambda: session.proc.poll() is not None, 30)
    assert signal.getsignal(signal.SIGTERM) is before


def test_an_acp_session_that_fails_to_start_after_its_agent_did_stops_it_and_holds_no_stop(project, monkeypatch):
    """REVIEWER-ORPHAN-5: a session whose reader could not start raised past ao with its agent running, and the
    signal handlers, still holding, would have let every later stop go."""
    from ao import acp
    started, real = [], acp.subprocess.Popen
    monkeypatch.setattr(acp.subprocess, "Popen", lambda *a, **kw: started.append(real(*a, **kw)) or started[-1])

    class NoThread:
        def __init__(self, *args, **kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(acp, "threading", SimpleNamespace(Thread=NoThread))
    before = signal.getsignal(signal.SIGTERM)

    attempt = cli._run_acp_reviewer(project["root"], [sys.executable, "-c", "import time; time.sleep(120)"],
                                    "acp-test", "review this", 60, "acp-test")

    (proc,) = started
    assert attempt["kind"] == "spawn-unknown" and "RuntimeError" in attempt["reason"]
    assert _until(lambda: proc.poll() is not None, 30)
    assert signal.getsignal(signal.SIGTERM) is before


def test_a_start_ended_by_what_is_no_error_puts_the_handlers_back(project, monkeypatch):
    """REVIEWER-ORPHAN-5: only an error was caught around the start; anything else left the handlers holding."""
    class Ended(BaseException):
        pass

    def start(*args, **kw):
        raise Ended()

    monkeypatch.setattr(cli.subprocess, "Popen", start)
    before = signal.getsignal(signal.SIGTERM)

    with pytest.raises(Ended):
        cli._run_reviewer(project["root"], [sys.executable, "-c", "pass"], 60)

    assert signal.getsignal(signal.SIGTERM) is before
