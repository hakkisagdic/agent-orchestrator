"""The bug hunter's leads on the review pipeline, each read against the code and fixed (LEADS-REVIEW).

ao's read-only bug hunter read src/ao/parts/cli_review.py and left nine leads; every one held:

- A candidate's diff was decoded with replacement characters, so every byte of it that is not UTF-8 read
  as one U+FFFD and a reviewer approved bytes it was never shown. Such a diff is shown escaped now, whole.
- `ao ci review` found the review it had recorded by counting the ledger's rows before it ran, which could
  take another review of the same range, and a ledger it could not read afterwards left the exit at the
  review's, APPROVED among them, with no verdict shown. cmd_review hands back the row it recorded, and
  what cannot be shown is no verdict. The detached run of a submitted review, and a collect of a stand-in's
  answer, found their own the same way.
- A landed range's review built its diff whole while its artefact recorded `--paths`: they are refused
  together, since a range is reviewed whole.
- Two runs of one submitted review both took it, and the first to end deleted the index the other read;
  two submits at once both counted the reviews in flight before either wrote its own.
- A reviewer's time was its timeout less the waits asked for, not the time that passed, and a route's time
  was read before it waited for quota headroom: both ran past their deadline.
- What a reviewer left running when it exited was never stopped, and a version probe ran in ao's own
  process group, so its timeout stopped the probe and not what it had started.
"""
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, procs, storage
from ao.storage import read_chained_jsonl
from tests.standin import program
from tests.test_ci_review import _ci, _stand_in, repository  # noqa: F401  (repository is a fixture these tests use)
from tests.test_review_chain import _fake, _repo_with_change
from tests.test_standin_collect import _answer, _collect, _unreachable

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")
APPROVED_TEXT = "\n".join(APPROVED) + "\n"
# A reviewer that keeps the prompt it was handed, in the file its first argument names, and approves.
PROMPT_KEEPER = ("import pathlib, sys\n"
                 "pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\n"
                 "print('VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0')\n")


def _args(**kw):
    base = dict(action=None, rid=None, any=False, run=None, boundary="b", paths=None, commits=None, timeout=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _plain(capsys):
    return A.re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _rows(root):
    return read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)


def _git(root, *args):
    subprocess.run([A.git_binary(), *args], cwd=root, check=True, capture_output=True)


def _until(condition, seconds=20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


# ---- 297ec568615c59ef: a diff that is not UTF-8 is shown, not replaced ------------------------------------------------

def test_a_diff_that_is_not_utf8_is_shown_escaped_and_no_two_such_diffs_read_alike(project, tmp_path, capsys):
    root = project["root"]
    _repo_with_change(root)
    kept = tmp_path / "prompt.txt"
    cfg = dict(project, reviewer={"id": "r1", "family": "x",
                                  "argv": [sys.executable, "-c", PROMPT_KEEPER, str(kept), "{prompt}"]})

    assert cli.cmd_review(cfg, _args()) == 0
    shown = kept.read_text(encoding="utf-8")
    assert "+x = 2" in shown and "--- ESCAPED" not in shown                 # UTF-8 is handed over as it was

    with open(os.path.join(root, "src", "a.py"), "wb") as fh:
        fh.write(b'x = "\xff" + "\\xfe"\n')                                # a byte, and the text that reads like one
    _git(root, "add", "src/a.py")
    assert cli.cmd_review(cfg, _args()) == 0

    shown = kept.read_text(encoding="utf-8")
    assert "\ufffd" not in shown
    assert '+x = "\\xff" + "\\\\xfe"' in shown
    assert shown.index("\n--- ESCAPED") < shown.index("\n--- CANDIDATE DIFF ---\n")    # above it, where ao writes
    artefact = os.path.join(root, "semantic-review", _rows(root)[-1]["artefact"])
    with open(artefact, encoding="utf-8") as fh:
        assert A.review_evidence(fh.read())["diff_escaped"] is True
    assert "the diff is not UTF-8" in _plain(capsys)
    # One reading per diff: bytes that differ read apart, and the text \xff, which a UTF-8 diff can hold, is handed
    # over without the note that says how an escaped diff reads.
    assert len({cli._shown_diff(diff) for diff in (b"\xff", b"\xfe", b"\\xff", b"\\\xff")}) == 4


def test_a_person_is_shown_a_diff_that_is_not_utf8_as_a_reviewer_is(project, capsys):
    root = project["root"]
    _repo_with_change(root)
    with open(os.path.join(root, "src", "a.py"), "wb") as fh:
        fh.write(b"x = '\xe9t\xe9'\n")                                    # Latin-1, as a file can be kept
    _git(root, "add", "src/a.py")

    assert cli.cmd_person_review(project, SimpleNamespace(by="Ada Lovelace", verdict=None, digest=None, findings=None,
                                                          commits=None, paths=None, boundary="b")) == 0

    out = _plain(capsys)
    assert "+x = '\\xe9t\\xe9'" in out and "\ufffd" not in out
    assert "the diff is not UTF-8: it is shown escaped, whole" in out


# ---- 6d2874cf711ec870, b7aa9b6736749904: `ao ci review` shows the review it recorded, or gives no verdict -------------

def _record_beside(cfg, args, verdict):
    """Another review of the range `args` names, as the same author's, recorded as a review run beside it would be."""
    evidence = {"schema": 2, "kind": "commit-range", "authorizable": False, "commits": args.commits,
                "author": args.author, "verdict": verdict,
                "counts": {"BLOCKER": 0, "HIGH": int(verdict == "NEEDS_CHANGES"), "MEDIUM": 0, "LOW": 0}}
    text = f"# Review beside\n\n{A.review_evidence_line(evidence)}\n\nVERDICT: {verdict}\n"
    A.write_review_artefact(cfg["root"], cfg.get("reviews") or "semantic-review", "beside.md", text,
                            evidence=evidence, verdict=verdict)


def test_ci_shows_its_own_review_and_not_one_of_the_same_range_recorded_beside_it(repository, monkeypatch, tmp_path):
    _stand_in(monkeypatch, tmp_path, APPROVED_TEXT)
    real = cli.cmd_review

    def reviewed_and_another_beside(cfg, args):
        code = real(cfg, args)
        _record_beside(cfg, args, "NEEDS_CHANGES")
        return code

    monkeypatch.setattr(cli, "cmd_review", reviewed_and_another_beside)

    code, summary, outputs = _ci(repository, tmp_path, "--base", "main", "--head", "feature",
                                 "--author-family", "writers")

    assert code == 0 and outputs["verdict"] == "APPROVED" and "**APPROVED**" in summary
    assert not outputs["artefact"].endswith("beside.md")
    assert [row["artefact"] for row in _rows(repository)][-1] == "beside.md"


def test_ci_gives_no_verdict_for_a_review_it_recorded_and_cannot_show(repository, monkeypatch, tmp_path):
    _stand_in(monkeypatch, tmp_path, APPROVED_TEXT)
    real = cli.cmd_review

    def reviewed_then_rewritten(cfg, args):
        code = real(cfg, args)
        newest = _rows(cfg["root"])[-1]
        with open(os.path.join(cfg["root"], "semantic-review", newest["artefact"]), "w", encoding="utf-8") as fh:
            fh.write("VERDICT: APPROVED\n")
        return code

    monkeypatch.setattr(cli, "cmd_review", reviewed_then_rewritten)

    code, summary, outputs = _ci(repository, tmp_path, "--base", "main", "--head", "feature",
                                 "--author-family", "writers")

    assert code == 3 and outputs["verdict"] == "none"
    assert "**no verdict**" in summary and "not the file ao recorded" in summary


def test_an_approved_exit_in_ci_comes_with_the_verdict_it_recorded(repository, monkeypatch, tmp_path):
    """The ledger was read again after the review to find it; that read raised, was swallowed, and the step exited 0
    with `verdict=none` and "not reviewed" in its summary."""
    _stand_in(monkeypatch, tmp_path, APPROVED_TEXT)

    def unreadable(*args, **kwargs):
        raise RuntimeError("broken hash chain")

    monkeypatch.setattr(A, "range_review", unreadable)

    code, summary, outputs = _ci(repository, tmp_path, "--base", "main", "--head", "feature",
                                 "--author-family", "writers")

    assert code == 0 and outputs["verdict"] == "APPROVED" and "**APPROVED**" in summary


def test_an_error_that_stops_the_review_in_ci_is_no_verdict(repository, monkeypatch, tmp_path):
    """It exited 1, as NEEDS_CHANGES does, and the action's `fail-on: no-verdict` passes a 1."""
    _stand_in(monkeypatch, tmp_path, APPROVED_TEXT)

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(A, "write_review_artefact", disk_full)

    code, summary, outputs = _ci(repository, tmp_path, "--base", "main", "--head", "feature",
                                 "--author-family", "writers")

    assert code == 3 and outputs["verdict"] == "none"
    assert "**no verdict**: the review stopped: OSError" in summary


def test_a_submitted_reviews_run_records_its_own_review_and_not_one_beside_it(project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})
    spawned = []
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root_, rid: spawned.append(rid))
    assert cli.cmd_review(cfg, _args(action="submit")) == 0
    real = cli.cmd_review

    def reviewed_and_another_beside(cfg_, args):
        code = real(cfg_, args)
        if getattr(args, "pinned", False):
            # A review of the same candidate in the foreground, ending after the run's.
            row = dict(_rows(root)[-1])
            evidence = {"schema": 2, "kind": "index-candidate", "authorizable": False,
                        "candidate": {"digest": row["candidate"]}, "verdict": "NEEDS_CHANGES"}
            A.write_review_artefact(root, "semantic-review", "beside.md", "VERDICT: NEEDS_CHANGES\n",
                                    evidence=evidence, verdict="NEEDS_CHANGES")
        return code

    monkeypatch.setattr(cli, "cmd_review", reviewed_and_another_beside)

    assert cli.cmd_review_run(cfg, spawned[0]) == 0

    state = cli._review_state(root, spawned[0])
    assert state["verdict"] == "APPROVED" and state["artefact"] != "beside.md"


def test_a_collect_marks_its_request_with_the_review_it_recorded(project, tmp_path, monkeypatch, capsys):
    cfg, request = _unreachable(project)
    real = cli.cmd_review

    def collected_and_another_beside(cfg_, args):
        code = real(cfg_, args)
        # Another request's answer, carried from a session of the same model, recorded meanwhile.
        evidence = {"schema": 2, "kind": "index-candidate", "authorizable": False, "transport": "human-carried",
                    "candidate": {"digest": "sha256:another"}, "verdict": "NEEDS_CHANGES"}
        A.write_review_artefact(cfg_["root"], "semantic-review", "beside.md", "VERDICT: NEEDS_CHANGES\n",
                                evidence=evidence, verdict="NEEDS_CHANGES", reviewer="human-assisted:gpt-5.5",
                                fallback=True)
        return code

    monkeypatch.setattr(cli, "cmd_review", collected_and_another_beside)

    code, _ = _collect(cfg, request["nonce"], _answer(tmp_path, request["nonce"], APPROVED), capsys)

    assert code == 0 and _rows(cfg["root"])[-1]["artefact"] == "beside.md"
    assert A.review_request(cfg["root"], request["nonce"])["collected"]["artefact"] != "beside.md"


# ---- dee6122d2abd0e6c: a landed range is reviewed whole ---------------------------------------------------------------

def test_paths_are_refused_with_a_landed_range_which_is_reviewed_whole(project, capsys):
    root = project["root"]
    _repo_with_change(root)
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x is 2")
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})

    assert cli.cmd_review(cfg, SimpleNamespace(boundary="b", paths=["src"], commits="HEAD~1..HEAD")) == 2
    assert "--paths goes with a staged candidate" in _plain(capsys)
    # `--paths` given with nothing after it is a --paths too (LEADS-REVIEW).
    assert cli.cmd_review(cfg, SimpleNamespace(boundary="b", paths=[], commits="HEAD~1..HEAD")) == 2
    assert "--paths goes with a staged candidate" in _plain(capsys)
    assert cli.cmd_person_review(project, SimpleNamespace(by="Ada Lovelace", verdict=None, digest=None, findings=None,
                                                          commits="HEAD~1..HEAD", paths=["src"], boundary=None)) == 2
    assert _rows(root) == []                                                # nothing was reviewed, nothing recorded


# ---- 8abf30b6c634fb17, 582098bc48c9628a: a submitted review is run once, and submits keep to max_inflight -----------

def test_a_submitted_review_is_run_once_and_a_second_run_leaves_its_index_alone(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})
    spawned = []
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root_, rid: spawned.append(rid))
    assert cli.cmd_review(cfg, _args(action="submit")) == 0
    rid = spawned[0]
    index = cli._review_state(root, rid)["index"]
    real, started, second = cli.cmd_review, [], []

    def a_second_run_starts_meanwhile(cfg_, args):
        if getattr(args, "pinned", False) and not started:
            started.append(True)
            second.append(cli.cmd_review_run(cfg_, rid))
            second.append(os.path.exists(index))
        return real(cfg_, args)

    monkeypatch.setattr(cli, "cmd_review", a_second_run_starts_meanwhile)

    assert cli.cmd_review_run(cfg, rid) == 0

    assert second == [2, True]
    assert f"{rid} is run already, by pid {os.getpid()}" in _plain(capsys)
    state = cli._review_state(root, rid)
    assert state["state"] == "finished" and state["verdict"] == "APPROVED" and not os.path.exists(index)


def test_two_submits_at_once_keep_to_review_max_inflight(project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cli._write_review_state(root, {"id": "R-1", "state": "running", "tree": "t" * 40, "candidate": "sha256:x",
                                   "slice": None, "pid": os.getpid(), "submitted_at": int(time.time())})
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root_, rid: None)
    inside, go, real = threading.Event(), threading.Event(), A.running_slice

    def the_first_waits_inside(root_):
        if not inside.is_set():
            inside.set()
            go.wait(10)
        return real(root_)

    monkeypatch.setattr(A, "running_slice", the_first_waits_inside)
    codes = {}

    def submit(name):
        codes[name] = cli.cmd_review(project, _args(action="submit"))

    first = threading.Thread(target=submit, args=("first",))
    first.start()
    assert inside.wait(10)
    second = threading.Thread(target=submit, args=("second",))
    second.start()
    second.join(1.5)                        # without the lock it submits here, between the first's count and claim
    go.set()
    first.join(30)
    second.join(30)

    flying = [state["id"] for state in cli._review_states(root) if cli._review_in_flight(state)]
    assert len(flying) == 2 and sorted(codes.values()) == [0, 2]


# ---- 9984d8c4907a8061, d24fc95834d62e26: a reviewer's deadline is the clock's -----------------------------------------

def test_a_reviewer_is_waited_on_no_longer_than_its_timeout_by_the_clock(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    cpu = [0.0]

    def slow_table(pgid):                   # the stall check reads the process table, which takes time
        clock[0] += 10
        cpu[0] += 1
        return cpu[0]

    monkeypatch.setattr(procs, "group_cpu_seconds", slow_table)

    def communicate(timeout=None):
        clock[0] += timeout
        raise subprocess.TimeoutExpired("reviewer", timeout)

    reviewer = SimpleNamespace(pid=4242, args=["reviewer"], communicate=communicate)

    with pytest.raises(subprocess.TimeoutExpired):
        cli._reviewer_communicate(reviewer, 100, "r1", clock[0], stall=600)

    assert clock[0] - 1000.0 <= 100


def _routes_that_take_their_time(monkeypatch, clock, handed):
    def takes_it(root, cand, prompt, timeout, strict, primary, **_):
        handed.append(timeout)
        clock[0] += timeout + cli.REVIEW_KILL_DRAIN_SECONDS
        return cand["id"], "/agents/reviewer", "1.0", {"ok": False, "out": "", "returncode": None,
                                                       "kind": "timeout", "retryable": True, "reason": "timed out"}

    monkeypatch.setattr(cli, "_reviewer_route_invocation", takes_it)


def test_a_route_gets_only_the_time_left_once_it_has_waited_for_quota_headroom(project, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli, "_review_retry_wait", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    handed = []
    _routes_that_take_their_time(monkeypatch, clock, handed)

    def waits_on_the_rotation_lock(cfg, argv, who, on_wait=None):
        clock[0] += 250
        return {"ok": True, "provider": "claude", "rotated": True, "text": "rotated"}

    monkeypatch.setattr(A, "rotate_if_exhausted", waits_on_the_rotation_lock)

    result = cli._invoke_reviewer_chain(project["root"], [{"id": "r0", "argv": ["reviewer"]}], "prompt", 100, False)

    budget = cli._review_chain_budget(100)
    assert result["used"] is None and clock[0] - 1000.0 <= budget
    assert handed == [budget - 250 - cli.REVIEW_KILL_DRAIN_SECONDS - cli.REVIEW_DISCOVERY_TOTAL_SECONDS]


def test_a_route_whose_wait_for_headroom_spent_the_budget_is_not_started(project, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    handed = []
    _routes_that_take_their_time(monkeypatch, clock, handed)

    def waits_out_the_budget(cfg, argv, who, on_wait=None):
        clock[0] += cli._review_chain_budget(100)
        return {"ok": True, "provider": "claude", "rotated": True, "text": "rotated"}

    monkeypatch.setattr(A, "rotate_if_exhausted", waits_out_the_budget)

    result = cli._invoke_reviewer_chain(project["root"], [{"id": "r0", "argv": ["reviewer"]}], "prompt", 100, False)

    assert handed == [] and result["used"] is None
    assert result["failures"][0]["reason"] == "not tried: the review chain budget of 300s was spent"


# ---- 7a6f0e19f4b1b06c, 32018e35166fd5ad: nothing a reviewer or a version probe started outlives it --------------------

@pytest.mark.skipif(os.name == "nt", reason="Windows has no process groups")
def test_a_group_whose_id_was_given_again_after_the_reviewer_was_reaped_is_left_alone():
    """Once the reviewer is reaped its pid is free, and a process given it may lead a group of that id: that group
    is another's, and was stopped as the reviewer's (LEADS-REVIEW)."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        reaped = SimpleNamespace(pid=other.pid, returncode=0)    # a reviewer reaped, its pid given to `other`

        assert cli._stop_reviewer_leftovers(reaped, "r") == []
        time.sleep(0.3)
        assert other.poll() is None, "another's group was stopped as the reviewer's"
    finally:
        other.kill()
        other.wait()


def test_a_link_where_the_submission_lock_is_kept_is_refused(project, tmp_path):
    """The submission lock is in the working tree, where an agent can put a link in its place: the lock is taken
    through no link, and nothing is made where it points (LEADS-REVIEW, LOCK-LINKS)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    reviews = os.path.join(project["root"], ".ao", "reviews")
    os.makedirs(reviews, exist_ok=True)
    try:
        os.symlink(str(outside / "made"), os.path.join(reviews, "submitted.lock"))
    except (OSError, NotImplementedError) as exc:          # Windows without the symlink privilege
        pytest.skip(f"no symbolic link can be made here: {exc}")

    with pytest.raises(storage.LockThroughLink):
        with cli._reviews_held(project["root"]):
            pass

    assert not (outside / "made").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows has no process groups; what a reviewer leaves there is a limit "
                                            "docs/windows.md names")
def test_what_a_reviewer_leaves_running_when_it_exits_is_stopped(project, tmp_path, capsys):
    marker = tmp_path / "child.pid"
    script = ("import subprocess, sys\n"
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
              "                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
              f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
              "print('VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0')\n")
    try:
        attempt = cli._run_reviewer(project["root"], [sys.executable, "-c", script], timeout=60)

        child = int(marker.read_text())
        assert attempt["kind"] == "success"
        assert _until(lambda: not A._pid_alive(child)), "the reviewer's child outlived the review"
        assert f"left 1 process(es) of its group running after it ended: [{child}]; ao stopped them" in _plain(capsys)
    finally:
        _kill_if_left(marker)


@pytest.mark.skipif(os.name == "nt", reason="a stop signal sent to this process is POSIX's")
def test_a_stop_signal_during_the_cleanup_waits_for_it_and_comes_after(project, tmp_path, monkeypatch):
    """The signal handlers were put back before the reviewer's leftovers were stopped: a SIGTERM in between ended ao
    with the reviewer's child running. A stop now waits while the leftovers are stopped, and comes after
    (LEADS-REVIEW)."""
    marker = tmp_path / "child.pid"
    script = ("import subprocess, sys\n"
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
              "                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
              f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
              "print('VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0')\n")
    real = cli._stop_reviewer_leftovers

    class Escaped(Exception):
        """The SIGTERM reached the handler that was there before the review: the cleanup did not hold it."""

    def before(signum, frame):
        raise Escaped()

    def signalled(proc, label):
        os.kill(os.getpid(), signal.SIGTERM)    # a stop that comes as the leftovers are about to be stopped
        return real(proc, label)
    monkeypatch.setattr(cli, "_stop_reviewer_leftovers", signalled)
    previous = signal.signal(signal.SIGTERM, before)
    try:
        with pytest.raises(cli.ReviewRunStopped):
            cli._run_reviewer(project["root"], [sys.executable, "-c", script], timeout=60)

        assert _until(lambda: not A._pid_alive(int(marker.read_text()))), "the reviewer's child outlived the stop"
        assert signal.getsignal(signal.SIGTERM) is before, "the handler that was there before is not back"
    finally:
        signal.signal(signal.SIGTERM, previous)
        _kill_if_left(marker)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no process groups; there taskkill /T stops a probe's tree")
def test_a_version_probe_that_does_not_answer_is_stopped_with_what_it_started(project, tmp_path):
    marker = tmp_path / "child.pid"
    probe = program(tmp_path / "bin", "ao-leads-probe",
                    "import subprocess, sys, time\n"
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                    f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
                    "time.sleep(60)\n")
    try:
        # Long enough for the probe to start its child on a busy machine; it never answers.
        assert cli._reviewer_binary_version(project["root"], probe, timeout=6) == ""

        assert marker.exists(), "the probe had not started its child when its timeout came"
        child = int(marker.read_text())
        assert _until(lambda: not A._pid_alive(child)), "the probe's child outlived its timeout"
    finally:
        _kill_if_left(marker)


def _kill_if_left(marker):
    """A child a test's process left, stopped by its pid so a failing test leaves nothing running."""
    try:
        os.kill(int(marker.read_text()), getattr(signal, "SIGKILL", signal.SIGTERM))
    except (OSError, ValueError):
        pass
