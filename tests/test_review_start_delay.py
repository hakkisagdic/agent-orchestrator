"""A submitted review says what it is doing, and on what it waits (REVIEW-START-DELAY).

A review was seen to start its reviewer 69 minutes after its submit while `ao reviews` said "running"
the whole time, and its log held nothing. The run takes no gate lock - only `ao verify`, `ao
merge-check` and `ao lock` do - so a suite holding that lock is shown here not to hold a review back.
What was missing was any way to see what the run did: its state carries its phase now, `ao reviews`
shows it, and its log is written a line at a time from a first line that names the runner.
"""
import contextlib
import io
import json
import os
import re
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A
from ao.storage import LedgerLockTimeout, _exclusive_lock
from tests.test_review_chain import _repo_with_change

# A reviewer run by what it inherits: it notes that it started; it records what its run's state names
# once that names it; it exits 75, the temporary failure, the first time it is asked to; it waits for a
# go file when one is named; and it approves.
REVIEWER = """
import json, os, sys, time
env = os.environ
if env.get("AO_TEST_STARTED"):
    open(env["AO_TEST_STARTED"], "a").close()
if env.get("AO_TEST_STATE"):
    mine, deadline = "pid %d" % os.getpid(), time.time() + 60
    while True:
        try:
            with open(env["AO_TEST_STATE"], encoding="utf-8") as fh:
                phase = json.load(fh).get("phase") or {}
        except (OSError, ValueError):
            phase = {}
        if str(phase.get("on")).endswith(mine) or time.time() > deadline:
            break
        time.sleep(0.05)
    with open(env["AO_TEST_SEEN"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": os.getpid(), "phase": phase}) + "\\n")
if env.get("AO_TEST_FAIL_ONCE") and not os.path.exists(env["AO_TEST_FAIL_ONCE"]):
    open(env["AO_TEST_FAIL_ONCE"], "w").close()
    sys.exit(75)
if env.get("AO_TEST_GO"):
    deadline = time.time() + 120
    while not os.path.exists(env["AO_TEST_GO"]) and time.time() < deadline:
        time.sleep(0.05)
for line in ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0"):
    print(line)
"""
SCENARIOS = "the claim journal:\n1. a claim is journalled before admission\n2. a duplicate is refused by its id"
FIRST_LINE = r"\d\d:\d\d:\d\d {rid} started: runner pid \d+, \S+ after the submit, reviewing tree {tree}"


def _args(**kw):
    base = dict(action=None, rid=None, any=False, run=None, boundary="b", paths=None, commits=None, timeout=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _with_reviewer(project):
    """The project with REVIEWER as its reviewer, in memory and in .ao/config.json for a run that reads it."""
    reviewer = {"id": "r1", "family": "x", "argv": [sys.executable, "-c", REVIEWER, "{prompt}"]}
    path = os.path.join(project["root"], ".ao", "config.json")
    with open(path, encoding="utf-8") as fh:
        config = json.load(fh)
    config["reviewer"] = reviewer
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return dict(project, reviewer=reviewer)


def _submitted(cfg, monkeypatch, **kw):
    """Submit the staged candidate with its run left to the test, and return the review's id."""
    spawned = []
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root, rid: spawned.append(rid))
    assert cli.cmd_review(cfg, _args(action="submit", **kw)) == 0
    return spawned[0]


def _state(root, rid, **fields):
    state = dict({"id": rid, "state": "running", "tree": "t" * 40, "candidate": "sha256:x", "slice": None,
                  "submitted_at": int(time.time())}, **fields)
    cli._write_review_state(root, state)


def _until(condition, seconds=120.0):
    """Poll until `condition()` holds; the bound is generous, for the machine running the suite may be busy."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


def test_a_submitted_review_reads_starting_until_its_runner_reports(project, monkeypatch, capsys):
    _repo_with_change(project["root"])
    rid = _submitted(project, monkeypatch)
    capsys.readouterr()

    assert cli.cmd_reviews(project, SimpleNamespace()) == 0
    assert re.search(rf"{rid}\s+starting\s.*its runner has not reported yet \(", _plain(capsys))
    assert cli.cmd_review(project, _args(action="collect", rid=rid)) == 1
    assert re.search(rf"{rid} is still starting: .*; its runner has not reported yet \(", _plain(capsys))


def test_a_suite_holding_the_gate_lock_does_not_hold_a_review_back(project, tmp_path, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    rid = _submitted(cfg, monkeypatch)
    suite = str(tmp_path / "suite")
    lock = tmp_path / "gate.lock"
    lock.write_text(json.dumps({"root": suite, "pid": os.getpid(), "at": int(time.time()) - 3600}), encoding="utf-8")
    monkeypatch.setattr(A, "GATE_LOCK", str(lock))
    asked = []
    monkeypatch.setattr(A, "acquire_gate_lock", lambda root, timeout=0: asked.append(root) or False)

    assert cli.cmd_review(cfg, _args(run=rid, boundary=None)) == 0

    state = cli._review_state(root, rid)
    assert state["state"] == "finished" and state["verdict"] == "APPROVED" and "phase" not in state
    assert asked == [] and A.gate_lock_holder()["root"] == suite


def test_while_a_reviewer_works_its_state_names_the_section_the_reviewer_and_its_pid(project, tmp_path, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    rid = _submitted(cfg, monkeypatch, boundary=SCENARIOS)
    seen = tmp_path / "seen.jsonl"
    monkeypatch.setenv("AO_TEST_STATE", cli._review_state_path(root, rid))
    monkeypatch.setenv("AO_TEST_SEEN", str(seen))

    assert cli.cmd_review(cfg, _args(run=rid, boundary=None)) == 0

    rows = [json.loads(line) for line in seen.read_text(encoding="utf-8").splitlines()]
    assert [(row["phase"].get("what"), row["phase"].get("on")) for row in rows] == [
        ("running", f"section 1/2 scenario:1; r1, pid {rows[0]['pid']}"),
        ("running", f"section 2/2 scenario:2; r1, pid {rows[1]['pid']}")]
    assert "phase" not in cli._review_state(root, rid)


def test_a_retry_wait_reads_waiting_and_is_in_the_log_before_the_wait_begins(project, tmp_path, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    rid = _submitted(cfg, monkeypatch)
    monkeypatch.setenv("AO_TEST_FAIL_ONCE", str(tmp_path / "failed-once"))
    path = tmp_path / f"{rid}.log"
    # A detached run's standard output is its log: a file, which Python writes in blocks unless told.
    log = open(path, "a", encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", log)
    during = {}

    def wait(seconds):
        during["phase"] = cli._review_state(root, rid)["phase"]
        during["log"] = path.read_text(encoding="utf-8").splitlines()
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            cli.cmd_reviews(cfg, SimpleNamespace())
        during["reviews"] = shown.getvalue()

    monkeypatch.setattr(cli, "_review_retry_wait", wait)

    try:
        assert cli.cmd_review(cfg, _args(run=rid, boundary=None)) == 0
    finally:
        log.close()

    tree = cli._review_state(root, rid)["tree"]
    assert (during["phase"]["what"], during["phase"]["on"]) == ("waiting", "30s, then r1 once more")
    assert re.fullmatch(FIRST_LINE.format(rid=rid, tree=tree), during["log"][0])
    assert any(re.fullmatch(rf"\d\d:\d\d:\d\d {rid} waiting: 30s, then r1 once more", line) for line in during["log"])
    assert re.search(rf"{rid}\s+waiting\s.*30s, then r1 once more \(", during["reviews"])
    assert log.line_buffering is False                  # put back as the run found it
    assert cli._review_state(root, rid)["verdict"] == "APPROVED"


def test_a_review_behind_the_held_keyflip_rotation_lock_reads_waiting_until_the_lock_is_let_go(
        project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    rid = _submitted(cfg, monkeypatch)
    with open(os.environ["AO_SETTINGS"], "w", encoding="utf-8") as fh:
        json.dump({"keyflip": {"rotation": "on"}}, fh)
    # The reviewer spends a provider whose window reads spent, and has headroom when read again under
    # the lock, as after another actor's rotation: keyflip itself is never run.
    windows = []
    monkeypatch.setattr(A, "provider_of", lambda argv: "claude")
    monkeypatch.setattr(A, "provider_window", lambda name: windows.append(name) or {
        "pct": 99 if len(windows) == 1 else 10, "window": "5h", "resets_in": "1h", "resets_s": 3600, "raw": ""})
    started = tmp_path / "reviewer-started"
    monkeypatch.setenv("AO_TEST_STARTED", str(started))
    outcome, held = {}, {}

    def run():
        try:
            outcome["code"] = cli.cmd_review(cfg, _args(run=rid, boundary=None))
        except BaseException as exc:                    # asserted on below, in the test's own thread
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    try:
        with _exclusive_lock(os.path.join(A.HOME, ".ao", "keyflip-rotation.lock"), timeout=5):
            worker.start()
            _until(lambda: windows or not worker.is_alive(), 60)
            assert windows, outcome                      # it read a spent window and went for the lock
            time.sleep(0.5)                              # a lock that did not hold would have let it on by now
            held.update(alive=worker.is_alive(), windows=len(windows), started=started.exists(),
                        phase=cli._review_state(root, rid)["phase"])
            capsys.readouterr()
            cli.cmd_reviews(cfg, SimpleNamespace())
            held["reviews"] = _plain(capsys)
    finally:
        worker.join(120)

    assert (held["alive"], held["windows"], held["started"]) == (True, 1, False)
    assert held["phase"]["what"] == "waiting"
    assert held["phase"]["on"] == "quota headroom for r1; a spent window waits on the machine's keyflip rotation lock"
    assert re.search(rf"{rid}\s+waiting\s.*quota headroom for r1; .*keyflip rotation lock \(", held["reviews"])
    assert outcome == {"code": 0} and started.exists() and len(windows) == 2
    assert cli._review_state(root, rid)["verdict"] == "APPROVED"


def test_a_run_that_stops_on_an_error_is_recorded_failed_rather_than_left_to_read_as_lost(
        project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    rid = _submitted(cfg, monkeypatch)
    lock = os.path.join(A.HOME, ".ao", "keyflip-rotation.lock")

    def timed_out(cfg, argv, who, on_wait=None):
        raise LedgerLockTimeout(f"timed out locking {lock}", lock, 180)

    monkeypatch.setattr(A, "rotate_if_exhausted", timed_out)

    assert cli.main(["-C", root, "review", "--run", rid]) == 1

    state = cli._review_state(root, rid)
    assert state["state"] == "failed" and "phase" not in state
    assert state["reason"] == f"the run stopped: LedgerLockTimeout: timed out locking {lock}"
    assert rid in [returned["id"] for returned in A.returned_reviews(root)]    # the watchdog raises it
    capsys.readouterr()
    cli.cmd_reviews(cfg, SimpleNamespace())
    assert re.search(rf"{rid}\s+failed", _plain(capsys))


def test_a_runner_gone_without_a_result_reads_lost_and_says_what_it_was_doing(project, capsys):
    root = project["root"]
    gone, now = 2 ** 22 + 12345, int(time.time())
    _state(root, "R-21", pid=gone, phase={"what": "waiting", "on": "30s, then r1 once more", "since": now})
    _state(root, "R-22", submitted_at=now - 300,
           phase={"what": "starting", "on": "its runner has not reported yet", "since": now - 300})
    _state(root, "R-23", pid=os.getpid())               # written before phases were kept

    assert cli.cmd_reviews(project, SimpleNamespace()) == 0

    out = _plain(capsys)
    assert re.search(r"R-21\s+lost\s.*  it was waiting: 30s, then r1 once more$", out, re.M)
    assert re.search(r"R-22\s+lost\s.*  its runner never reported; its log is \.ao/reviews/R-22\.log$", out, re.M)
    assert re.search(r"R-23\s+running\s+\S+\s+\S+\s*$", out, re.M)


def test_a_detached_run_logs_its_first_line_and_its_reviewer_while_a_suite_holds_the_gate_lock(
        project, tmp_path, monkeypatch, capsys):
    """The whole path, started apart as `ao review submit` starts it, with the gate lock held throughout."""
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    # The run finds ~/.ao through the home it is handed, never the machine's own.
    monkeypatch.setenv("HOME", A.HOME)
    monkeypatch.setenv("USERPROFILE", A.HOME)
    go = tmp_path / "go"
    monkeypatch.setenv("AO_TEST_GO", str(go))
    suite = str(tmp_path / "suite")
    lock = os.path.join(A.HOME, ".ao", "gate.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    with open(lock, "w", encoding="utf-8") as fh:
        json.dump({"root": suite, "pid": os.getpid(), "at": int(time.time()) - 3600}, fh)

    assert cli.cmd_review(cfg, _args(action="submit")) == 0
    (state,) = cli._review_states(root)
    rid = state["id"]

    def now():
        return cli._review_state(root, rid) or {}          # None only while the run replaces it

    try:
        _until(lambda: now().get("state") != "running" or (now().get("phase") or {}).get("what") == "running")
        assert (now().get("phase") or {}).get("what") == "running", now()
        log = open(os.path.join(root, ".ao", "reviews", f"{rid}.log"), encoding="utf-8").read().splitlines()
        capsys.readouterr()
        cli.cmd_reviews(cfg, SimpleNamespace())
        shown = _plain(capsys)
    finally:
        go.write_text("go", encoding="utf-8")

    assert re.fullmatch(FIRST_LINE.format(rid=rid, tree=state["tree"]), log[0]), log
    assert any(re.fullmatch(rf"\d\d:\d\d:\d\d {rid} running: r1, pid \d+", line) for line in log), log
    assert re.search(rf"{rid}\s+running\s.*r1, pid \d+ \(", shown)
    assert _until(lambda: now().get("state") not in (None, "running"))
    assert now()["verdict"] == "APPROVED"
    with open(lock, encoding="utf-8") as fh:
        assert json.load(fh)["root"] == suite


# ---- what its review found (REVIEW-START-DELAY, second round) ----------------------------------------

def _ended_events(rid):
    with open(A.events_path(), encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return [row for row in rows if row.get("kind") == "review-finished" and (row.get("data") or {}).get("review") == rid]


def test_a_run_that_stops_on_an_error_is_told_in_the_event_log_as_every_end_is(project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    rid = _submitted(cfg, monkeypatch)

    def unreadable(root):
        raise RuntimeError("the index could not be read")

    monkeypatch.setattr(A, "index_candidate", unreadable)

    with pytest.raises(RuntimeError, match="the index could not be read"):
        cli.cmd_review(cfg, _args(run=rid, boundary=None))

    state = cli._review_state(root, rid)
    assert state["state"] == "failed" and "the index could not be read" in state["reason"]
    assert [(row["data"]["state"], row["data"].get("verdict")) for row in _ended_events(rid)] == [("failed", None)]


def test_an_error_in_the_runs_first_step_is_recorded_failed_too(project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    rid = _submitted(cfg, monkeypatch)

    def no_log():
        raise OSError("the log cannot be written")

    monkeypatch.setattr(cli, "_log_by_line", no_log)
    before = os.environ.get("GIT_INDEX_FILE")

    with pytest.raises(OSError, match="the log cannot be written"):
        cli.cmd_review(cfg, _args(run=rid, boundary=None))

    state = cli._review_state(root, rid)
    assert state["state"] == "failed" and "the log cannot be written" in state["reason"]
    assert os.environ.get("GIT_INDEX_FILE") == before           # put back as the run found it


def test_a_review_whose_window_has_headroom_never_reads_waiting(project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = _with_reviewer(project)
    rid = _submitted(cfg, monkeypatch)
    with open(os.environ["AO_SETTINGS"], "w", encoding="utf-8") as fh:
        json.dump({"keyflip": {"rotation": "on"}}, fh)
    monkeypatch.setattr(A, "provider_of", lambda argv: "claude")
    monkeypatch.setattr(A, "provider_window", lambda name: {"pct": 10, "window": "5h", "resets_in": "1h",
                                                            "resets_s": 3600, "raw": ""})
    phases, real = [], cli._review_phase
    monkeypatch.setattr(cli, "_review_phase", lambda what, on="", section=None: (phases.append(what),
                                                                                  real(what, on, section)))

    assert cli.cmd_review(cfg, _args(run=rid, boundary=None)) == 0

    assert "waiting" not in phases and "running" in phases
    assert cli._review_state(root, rid)["verdict"] == "APPROVED"
