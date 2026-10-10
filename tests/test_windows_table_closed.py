"""A process table that could not be read is an error its callers see, never an empty table (WINDOWS-TABLE-CLOSED).

On Windows the process table is one PowerShell query. A PowerShell that was missing, failed, did not
answer within its thirty seconds, or printed something that was not the table gave an empty table, and
every caller read "no process" as fact: no agent turn in the tree, no reviewer alive, nothing holding
the architect's session. `ao hold` said no turn was running and stopped nothing, `ao writers` counted
none, and a watchdog cycle could start a turn or wake an architect beside one it never saw. The macOS
and Linux readers and the `ps` they fall back to turned their own failures into the same empty table.
Each reader now raises procs.TableUnreadable, naming the platform and why; each caller that decides from
the table refuses or holds, and each that only shows it says it could not look. Every failure here is
made as the platform makes it, on any platform: no PowerShell runs.
"""
import json
import os
import re
import subprocess
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, mcp, procs, watchdog as W
from tests.scenarios import World

WHY = "the process table could not be read on Windows: powershell was not found"
BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"
STAND_DOWN = f"{WHY}; this cycle starts no turn, wakes nobody and reaps nothing, and the next cycle looks again"


@pytest.fixture
def powershell_missing(monkeypatch):
    """The Windows backend where PowerShell cannot be found; returns the turns anything tried to stop.

    Windows does not answer for the process itself either, so every liveness question reaches the table.
    """
    monkeypatch.setattr(procs, "_NATIVE", procs._Windows())
    found = A.runnable_binary
    monkeypatch.setattr(A, "runnable_binary", lambda name: None if name == "powershell" else found(name))
    monkeypatch.setattr(procs, "alive", lambda pid: None)
    stopped = []
    monkeypatch.setattr(A, "kill_turn", lambda pid, sig: stopped.append(pid))
    return stopped


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _blind(monkeypatch):
    """The scenario's process table, read as Windows reads it where PowerShell cannot be found."""
    def unread(*args, **kwargs):
        raise procs.TableUnreadable("powershell was not found", "Windows")
    monkeypatch.setattr(procs, "all_pids", unread)
    monkeypatch.setattr(A, "_proc_table", unread)


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _agent_turns(world):
    """What the cycle started of the agents' own programs, which the scenario resolves under /agents/."""
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).startswith("/agents/")]


def _powershell_answers(monkeypatch, run):
    """PowerShell is found, and answers as `run` does in place of subprocess.run: nothing is started."""
    monkeypatch.setattr(A, "runnable_binary", lambda name: os.path.join(os.sep, "no-such-dir", name))
    monkeypatch.setattr(procs.subprocess, "run", run)


def _answered(stdout="", stderr="", returncode=0):
    return lambda argv, **kwargs: SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _timed_out(argv, **kwargs):
    raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))


QUERY = "PowerShell's Get-CimInstance Win32_Process"


# ---- the readers -----------------------------------------------------------------------------

def test_a_powershell_that_cannot_be_found_is_named_where_it_was_an_empty_table(powershell_missing):
    for read in (procs.all_pids, procs.table, lambda: procs.argv(4), lambda: procs.info(4)):
        with pytest.raises(procs.TableUnreadable) as caught:
            read()
        assert str(caught.value) == WHY and caught.value.platform == "Windows"


@pytest.mark.parametrize("run, why", [
    (_timed_out, "powershell did not answer within 30 seconds"),
    (_answered(stderr="Get-CimInstance : Access denied\r\nAt line:1 char:9\r\n", returncode=1),
     "powershell exited with status 1: Get-CimInstance : Access denied"),
    (_answered(), "powershell printed nothing"),
    (_answered(stdout="[]"), f"{QUERY} listed no process"),
    (_answered(stdout="null"), f"{QUERY} listed no process"),
    (_answered(stdout='[{"Name": "System Idle Process"}]'), f"{QUERY} listed no process"),
], ids=["timed out", "failed", "silent", "empty list", "null", "rows without a pid"])
def test_each_way_powershell_fails_is_a_table_that_could_not_be_read(monkeypatch, run, why):
    _powershell_answers(monkeypatch, run)
    backend = procs._Windows()

    for read in (backend.all_pids, lambda: backend.argv(4), lambda: backend.info(4)):
        with pytest.raises(procs.TableUnreadable) as caught:
            read()
        assert str(caught.value) == f"the process table could not be read on Windows: {why}"


def test_an_answer_that_is_not_the_table_is_told_by_its_length_never_its_text(monkeypatch):
    cut = '[{"ProcessId": 7, "CommandLine": "C:\\\\tools\\\\agent.exe --secret=not-for-a-log'
    _powershell_answers(monkeypatch, _answered(stdout=cut))

    with pytest.raises(procs.TableUnreadable) as caught:
        procs._Windows().all_pids()

    assert str(caught.value) == (f"the process table could not be read on Windows: {QUERY} printed {len(cut)} "
                                 "characters that are not JSON")


class _Libproc:
    """proc_listpids as libproc answers it: the bytes the list needs, then the list itself."""

    def __init__(self, answers, pids=()):
        self.answers, self.pids = list(answers), list(pids)

    def proc_listpids(self, kind, typeinfo, buffer, size):
        if buffer is not None:
            for at, pid in enumerate(self.pids):
                buffer[at] = pid
        return self.answers.pop(0)


@pytest.mark.parametrize("answers, why", [
    ([-1], "proc_listpids answered -1 (errno "),
    ([8, 0], "proc_listpids listed no process (it answered 0)"),
], ids=["failed", "listed none"])
def test_a_libproc_that_lists_no_process_is_no_table_on_macos(answers, why):
    darwin = object.__new__(procs._Darwin)
    darwin.libproc = _Libproc(answers)

    with pytest.raises(procs.TableUnreadable) as caught:
        darwin.all_pids()

    assert str(caught.value).startswith("the process table could not be read on macOS: " + why)
    darwin.libproc = _Libproc([8, 8], [42, 43])
    assert darwin.all_pids() == [42, 43]


def test_a_proc_that_cannot_be_listed_is_no_table_on_linux(monkeypatch):
    def refused(path):
        raise PermissionError(13, "Permission denied", path)

    with monkeypatch.context() as patch:
        patch.setattr(procs.os, "listdir", refused)
        with pytest.raises(procs.TableUnreadable) as unlisted:
            procs._Linux().all_pids()
        patch.setattr(procs.os, "listdir", lambda path: ["self", "sys"])
        with pytest.raises(procs.TableUnreadable) as empty:
            procs._Linux().all_pids()

    assert str(unlisted.value).startswith("the process table could not be read on Linux: /proc could not be listed")
    assert str(empty.value) == "the process table could not be read on Linux: /proc lists no process"


def test_a_ps_that_cannot_run_is_no_table(monkeypatch):
    monkeypatch.setattr(procs, "_run", lambda argv: procs._Unanswered("ps was not found"))

    with pytest.raises(procs.TableUnreadable) as caught:
        procs._Shell().all_pids()

    assert str(caught.value) == f"the process table could not be read on {procs.platform_name()}: ps was not found"


class _Blind:
    """A backend that lists processes and can read none of them."""

    def all_pids(self):
        return [1, 2]

    def info(self, pid):
        return None


def test_a_table_none_of_whose_processes_can_be_read_is_no_table(monkeypatch):
    monkeypatch.setattr(procs, "_NATIVE", _Blind())

    with pytest.raises(procs.TableUnreadable) as caught:
        procs.table()

    assert str(caught.value).endswith(": it lists 2 processes and none of them could be read")


def test_an_empty_answer_reads_as_nothing_to_every_other_reader():
    unanswered = procs._Unanswered("ps exited with status 1")

    assert unanswered == "" and not unanswered.strip() and unanswered.split() == [] and unanswered.why


# ---- the callers that decide ------------------------------------------------------------------

def _as_windows(monkeypatch, ask):
    """What `ask` returns, or the error it raises, where os.name is Windows' - only while it asks, so a failure
    is still reported in this platform's paths."""
    with monkeypatch.context() as patch:
        patch.setattr(A.os, "name", "nt")
        try:
            return ask()
        except procs.TableUnreadable as exc:
            return str(exc)


def test_every_guard_that_asks_what_runs_hears_that_it_could_not_look(project, powershell_missing, monkeypatch):
    root, adapter, architect = project["root"], A.load_adapter("kiro"), project["architect"]
    A.project_key(root)                                 # registered first: the lock is not taken under "nt"
    asks = {
        "agent_pids": lambda: A.agent_pids(root, adapter),
        "agent_pids, headless only": lambda: A.agent_pids(root, adapter, headless_only=True),
        "unplaced_agent_pids": lambda: A.unplaced_agent_pids(root, adapter),
        "orphans": lambda: A.orphans(root, adapter),
        "writers": lambda: A.writers(root, adapter),
        "process_trees": lambda: A.process_trees([4242]),
        "session_in_use": lambda: A.session_in_use("s1", architect),
        "architect_present": lambda: A.architect_present(root, architect),
        "architect_turn_present": lambda: A.architect_turn_present(root, architect),
        "arch_alive": lambda: W.arch_alive(root, architect),
    }

    told = {name: _as_windows(monkeypatch, ask) for name, ask in asks.items()}

    assert told == {name: WHY for name in asks}


def test_a_process_neither_windows_nor_the_table_answers_for_is_taken_as_running(powershell_missing, monkeypatch):
    """A gate lock, the architect's lock, a review in flight: what holds a process holds it while nobody can say."""
    answers = [_as_windows(monkeypatch, ask) for ask in (
        lambda: A._pid_alive(4242), lambda: A._process_start(4242), lambda: A._process_start(4242, refresh=True),
        lambda: A._process_start(4242, refresh=True, strict=True))]

    assert answers == [True, None, None, WHY]


def test_a_turn_whose_start_cannot_be_read_now_is_believed_for_a_bounded_time(powershell_missing, monkeypatch):
    state = {"child_pid": 4242, "child_start": "20260906183012.123456+180", "last_nudge": time.time() - 60}
    long_ago = dict(state, last_nudge=time.time() - W.UNVERIFIED_CHILD_SECONDS - 1)

    assert [_as_windows(monkeypatch, lambda: W.child_alive(st)) for st in (state, long_ago)] == [True, False]


def test_a_hold_that_cannot_see_the_turns_in_its_tree_stands_and_says_so(project, powershell_missing, capsys):
    args = SimpleNamespace(action="hold", by="a person", reason="editing", grace=0, note=None)

    assert cli.cmd_hold(project, args) == 1

    out = _plain(capsys)
    assert f"hold set, but {WHY}: a turn in this tree may still be running" in out
    assert "no agent turn was running" not in out
    assert A.hold_state(project["root"]) and powershell_missing == []


@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_writers_are_unknown_never_none_and_fail_a_single_writer_rule(project, powershell_missing, capsys, as_json):
    assert cli.cmd_writers(project, SimpleNamespace(clean=False, json=as_json)) == 1

    out = _plain(capsys)
    if as_json:
        assert json.loads(out) == {"writers": None, "unreadable": WHY}
    else:
        assert f"writers unknown — {WHY}" in out and "0 writers" not in out


def _running_review(root, rid, **fields):
    cli._write_review_state(root, dict({"id": rid, "state": "running", "submitted_at": int(time.time()) - 600},
                                       **fields))


def test_review_cancel_stops_and_records_nothing_it_cannot_see(project, powershell_missing, capsys):
    root = project["root"]
    _running_review(root, "R-1", pid=os.getpid())                               # a run that answers, unread
    _running_review(root, "R-2", reviewer={"pid": 4242, "start": "20260906183012.123456+180"})
    args = SimpleNamespace(action="cancel", rid="R-1", any=False, run=None, boundary="b", paths=None, commits=None,
                           timeout=None)

    assert cli.cmd_review(project, args) == 1
    assert _plain(capsys).strip() == (f"R-1 is not cancelled: {WHY}, so whether its runner still runs cannot be told. "
                                      "The review stays running: run `ao review cancel R-1` again once the table "
                                      "can be read")
    args.rid = "R-2"
    assert cli.cmd_review(project, args) == 1
    assert f"R-2 is not cancelled: {WHY}, so whether its reviewer still runs cannot be told." in _plain(capsys)

    assert [cli._review_state(root, rid)["state"] for rid in ("R-1", "R-2")] == ["running", "running"]
    assert cli._review_state(root, "R-2")["reviewer"]["pid"] == 4242 and powershell_missing == []


def test_a_command_stopped_by_an_unreadable_table_says_so_in_one_line(project, monkeypatch, capsys):
    def unread(cfg, args):
        raise procs.TableUnreadable("powershell was not found", "Windows")
    monkeypatch.setattr(cli, "cmd_writers", unread)

    assert cli.main(["-C", project["root"], "writers"]) == 1

    assert capsys.readouterr().err.strip() == (f"ao writers stopped: {WHY}. It decides nothing from processes it "
                                               "cannot see; run it again once the table can be read")


# ---- the watchdog -----------------------------------------------------------------------------

def test_the_watchdog_starts_no_turn_beside_one_it_cannot_see_and_tells_a_person(world, monkeypatch):
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)
    world.cycle()
    assert "nudging" in world.verdict or "DRY RUN" in world.verdict           # what it can see, it nudges
    _blind(monkeypatch)

    world.cycle(dry_run=False)

    assert world.verdict == STAND_DOWN and _agent_turns(world) == []
    assert (f"{A.project_key(world.root)}: the process table cannot be read", "human") in \
        [(title, audience) for title, _, audience, _ in world.notices]
    assert W._FACTS["process_table"] == WHY


def test_no_architect_is_woken_beside_one_unseen_and_what_waits_reaches_a_person(world, monkeypatch):
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    _blind(monkeypatch)

    world.cycle(dry_run=False)

    assert world.verdict == STAND_DOWN
    assert _agent_turns(world) == []
    needs_you = [msg for title, msg, audience, _ in world.notices if title.endswith(": needs you")]
    assert needs_you and f"no architect will act on it: {WHY}, so no architect is woken" in needs_you[0]


def test_an_empty_queue_wakes_no_architect_to_refill_beside_one_unseen(world, monkeypatch):
    world.transcript_age(900)
    world.cycle()
    assert "would wake the architect to refill" in world.verdict              # what it can see, it refills
    _blind(monkeypatch)

    trace = world.cycle(dry_run=False)

    assert world.verdict == STAND_DOWN and _agent_turns(world) == []
    assert not any("refill" in line for line in trace)


def test_a_cached_quota_block_stands_where_presence_cannot_be_measured(world, monkeypatch):
    until = time.time() + 3600
    W.save_state(world.root, dict(W.load_state(world.root), arch_quota_until=until))
    world.transcript_age(900)
    _blind(monkeypatch)

    trace = world.cycle(dry_run=False)

    assert f"{WHY}; the architect's cached quota block stands" in trace
    assert W.load_state(world.root)["arch_quota_until"] == until


def test_what_waits_for_the_architect_goes_to_a_person_when_no_wake_can_be_decided(project, powershell_missing):
    hold = W.architect_hold_reason(project["root"], project, {}, {})

    assert hold == {"holdable": False, "code": "process-table", "reason": f"{WHY}, so no architect is woken"}


def test_a_retried_wake_is_told_only_once_whether_it_runs_can_be_seen(project, powershell_missing, monkeypatch):
    told = []
    monkeypatch.setattr(W, "_tell_phone", lambda root, text: told.append(text))
    st = {"wake_retry": {"at": time.time(), "pid": 4242, "reports": 1}}

    assert W.tell_retried_wake(project["root"], project, st) is False
    assert told == [] and "wake_retry" in st                                   # still owed


def test_a_request_still_reaches_the_architect_when_turns_cannot_be_counted(project, powershell_missing):
    with open(os.path.join(project["root"], "agent-mail", "20260916-1200-kiro-to-fable-BLOCKED-queue.md"), "w",
              encoding="utf-8") as fh:
        fh.write(BLOCKED)

    kinds = [anomaly["kind"] for anomaly in A.anomalies(project["root"], project, A.load_adapter("kiro"), 30, 360)]

    assert "decision-requested" in kinds and "several-turns-active" not in kinds


# ---- the callers that only show it -------------------------------------------------------------

def test_hold_status_says_it_could_not_count(project, powershell_missing, capsys):
    assert cli.cmd_hold(project, SimpleNamespace(action="status")) == 0

    assert f"agent processes in this tree: unknown — {WHY}" in _plain(capsys)


def test_the_implementers_state_is_unknown_and_says_why(project, powershell_missing, monkeypatch, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"payload": {"type": "turn_end"}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))

    state, age, doing = A.busy(project, A.load_adapter("kiro"))

    assert (state, doing) == ("unknown", WHY) and age is not None and age < 60


def test_the_status_tool_counts_no_process_it_could_not_see(project, powershell_missing):
    status = mcp.status_payload(project)

    assert status["agent_processes"] is None and status["agent_processes_unread"] == WHY


def test_reviews_say_a_reviewer_they_could_not_see_may_run_on(project, powershell_missing, capsys):
    _running_review(project["root"], "R-2", reviewer={"pid": 4242, "start": "20260906183012.123456+180"})

    assert cli.cmd_reviews(project, SimpleNamespace()) == 0

    out = _plain(capsys)
    assert re.search(r"R-2\s+lost\s", out) and f"whether its reviewer runs on cannot be told: {WHY}" in out
