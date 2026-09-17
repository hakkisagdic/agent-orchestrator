"""A subagent at work is its implementer at work, to every reader that decides whether the implementer is.

A session of the second shipped harness writes each subagent's records to a transcript of its own beside the
session's (`transcript.subagents`). The session waits for a subagent with its own transcript quiet, or ends its
turn while one works on in the background, and the readers the watchdog and the panel decide from read the
session transcript alone: such an implementer read as idle and its turn as ended, so the watchdog nudged it and
reaped the runtime that waited for its subagent. The implementer's last write is now a subagent's too, a turn
does not read as ended while a subagent has written since the session last did, and what a subagent writes is
the transcript growing for the spin check. Those readers stat the subagent transcripts and open none of them. A
finished subagent keeps nothing alive: its session writes after it. A subagent's failed calls are not the
implementer's errors, and a task notification, a note of the harness or a compaction summary are no one's
words and still open, or stay inside, the turn the model answers.
"""
import builtins
import json
import os
import re
import time
from datetime import datetime

from ao import cli, lib as A
from tests.scenarios import World
from tests.test_second_harness_cost import (HARNESS, R1, R2, R3, R4, R5, R6, _append, _call, _prompt, _response,
                                            _result, _spent, _text)
from tests.test_subagent_spend import S1, S2, _lines, _named, _streamed, _undelegated


def _when(record):
    return datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()


def _written(path, records, at=None):
    """A transcript as the store leaves it: its records, last written when its last record was, or at `at`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_lines(records), encoding="utf-8")
    when = max(_when(record) for record in records) if at is None else at
    os.utime(path, (when, when))
    return path


def _world(project, monkeypatch, tmp_path, session, subagents, adapter=HARNESS):
    """The implementer's session transcript, and its subagents' beside it: {name: (records, last written or None)}."""
    transcript = _written(tmp_path / "s1.jsonl", session)
    for name, (records, at) in subagents.items():
        _written(tmp_path / "s1" / "subagents" / name, records, at)
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    return dict(project, implementer={"adapter": adapter, "session": "s1"}), transcript


def _running(monkeypatch):
    """A runtime in the tree, so that a fresh write reads as working rather than stopped."""
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, headless_only=False: [4242])


def _background(now):
    """A turn that starts a subagent in the background and ends, a quarter of an hour ago."""
    return [
        _prompt(now - 900, "take the parser slice and have its checks run beside it"),
        *_response(now - 895, "msg-1", "tool_use", R1,
                   _call("call-1", "Agent", prompt="write src/parser.py and run its checks", run_in_background=True)),
        _named(now - 894, "call-1", "the agent works in the background", agentId="a1", status="async_launched",
               isAsync=True),
        *_response(now - 890, "msg-2", "end_turn", R2, _text("the parser is being written in the background")),
    ]


def _subagent(now, last):
    """The subagent's records: started by the call, its last write `last` seconds ago."""
    return [
        _prompt(now - 894, "write src/parser.py and run its checks"),
        *_streamed(now - 880, "sub-1", "tool_use", S1, _call("sub-call-1", "Bash", command="python3 -m pytest -q")),
        _result(now - last, "sub-call-1", "3 passed"),
    ]


# ── a quiet session whose subagent works ──

def test_a_quiet_session_whose_subagent_works_is_neither_idle_nor_ended(project, monkeypatch, tmp_path):
    now = time.time()
    _running(monkeypatch)
    cfg, transcript = _world(project, monkeypatch, tmp_path, _background(now),
                             {"agent-a1.jsonl": (_subagent(now, 20), None)})
    adapter = A.implementer_adapter(cfg)

    state, age, _ = A.busy(cfg, adapter)

    assert state == "working" and age < 120 and A.turn_ended(cfg) is False
    assert A.last_write(str(transcript), A.transcript_shape(adapter)) == os.path.getmtime(
        tmp_path / "s1" / "subagents" / "agent-a1.jsonl")
    # The session transcript alone is what the readers saw: silent for a quarter of an hour, its turn ended.
    undeclared = dict(cfg, implementer={"adapter": _undelegated(project), "session": "s1"})
    state, age, _ = A.busy(undeclared, A.implementer_adapter(undeclared))
    assert state == "idle" and age >= 890 and A.turn_ended(undeclared) is True

    # A session that waits on a subagent it did not send to the background is as quiet, and as busy.
    waiting = [_prompt(now - 900, "take the parser slice and write it through an agent"),
               *_response(now - 895, "msg-1", "tool_use", R1, _call("call-1", "Agent", prompt="write src/parser.py"))]
    cfg, _ = _world(project, monkeypatch, tmp_path / "waiting", waiting,
                    {"agent-a1.jsonl": (_subagent(now, 20), None)})
    assert A.busy(cfg, adapter)[0] == "working" and A.turn_ended(cfg) is False


def test_a_secondary_projects_subagent_at_work_is_the_implementer_working_there(project, monkeypatch, tmp_path):
    now = time.time()
    other = tmp_path / "secondary"
    (other / ".ao").mkdir(parents=True)
    (other / ".ao" / "config.json").write_text(json.dumps({
        "project": "secondary", "mailbox": "agent-mail", "reviews": "semantic-review",
        "implementer": {"adapter": HARNESS, "session": "s1", "name": "claude"}}), encoding="utf-8")
    cfg, _ = _world(project, monkeypatch, tmp_path / "store", _background(now),
                    {"agent-a1.jsonl": (_subagent(now, 20), None)})

    found = A.working_elsewhere(dict(cfg, secondary=[{"root": str(other), "name": "secondary"}]), 360)

    assert found["name"] == "secondary" and found["age"] < 120


def _watched(project, monkeypatch, tmp_path, subagent_last, runtime=True):
    """The watchdog's world: the implementer's turn closed 400 s ago, its subagent last wrote `subagent_last` s ago."""
    turn_ended = A.turn_ended
    world = World(project, monkeypatch, tmp_path)
    monkeypatch.setattr(A, "turn_ended", turn_ended)              # the reading itself, not the world's stand-in
    stored = {key: value for key, value in project.items() if key != "root"}
    stored["implementer"] = {"adapter": HARNESS, "session": "transcript", "name": "claude"}
    with open(os.path.join(world.root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    now = time.time()
    _written(world.transcript, [
        _prompt(now - 420, "take the parser slice and have its checks run beside it"),
        *_response(now - 415, "msg-1", "tool_use", R1,
                   _call("call-1", "Agent", prompt="run the parser's checks", run_in_background=True)),
        _named(now - 414, "call-1", "the agent works in the background", agentId="a1", status="async_launched",
               isAsync=True),
        *_response(now - 400, "msg-2", "end_turn", R2, _text("the parser's checks run in the background"))])
    _written(tmp_path / "transcript" / "subagents" / "agent-a1.jsonl",
             [_prompt(now - 414, "run the parser's checks"),
              *_streamed(now - 410, "sub-1", "tool_use", S1, _call("sub-call-1", "Bash", command="python3 -m pytest"))],
             at=now - subagent_last)
    if runtime:
        world.process(4242, ["/agents/claude", "-p", "the parser slice"])
    return world


def test_the_watchdog_leaves_a_runtime_that_waits_for_its_subagent_and_reaps_one_that_waits_for_nothing(
        project, monkeypatch, tmp_path):
    world = _watched(project, monkeypatch, tmp_path, subagent_last=30)

    trace = world.cycle()

    assert "already in this tree" in world.verdict and not any("reaping" in line for line in trace)
    # The subagent finished before the turn closed: the runtime lingers for nothing, and goes at the idle window.
    finished = time.time() - 410
    os.utime(tmp_path / "transcript" / "subagents" / "agent-a1.jsonl", (finished, finished))
    trace = world.cycle()
    assert any("linger; reaping" in line for line in trace)


def test_the_watchdog_does_not_nudge_a_session_that_is_quiet_while_its_subagent_works(project, monkeypatch, tmp_path):
    world = _watched(project, monkeypatch, tmp_path, subagent_last=30, runtime=False)
    world.board("running", "- [B8] the parser slice · since: 2026-09-17 09:00")

    world.cycle()

    assert world.verdict.startswith("working (")


# ── a finished subagent ──

def test_a_finished_subagent_keeps_no_session_alive(project, monkeypatch, tmp_path):
    now = time.time()
    _running(monkeypatch)
    done = [_prompt(now - 894, "write src/parser.py and run its checks"),
            *_streamed(now - 880, "sub-1", "end_turn", S1, _text("the parser is written and its checks pass"))]
    # The subagent ends, the harness tells the session, and the session answers.
    told = [*_background(now),
            dict(_prompt(now - 500, "<task-notification><status>completed</status></task-notification>"),
                 origin={"kind": "task-notification"}),
            *_response(now - 490, "msg-3", "end_turn", R3, _text("the parser is written and its checks pass"))]
    cfg, _ = _world(project, monkeypatch, tmp_path, told, {"agent-a1.jsonl": (done, now - 510)})

    state, age, _ = A.busy(cfg, A.implementer_adapter(cfg))

    assert state == "idle" and age >= 490 and A.turn_ended(cfg) is True
    # A subagent the session waited for ended before the result the session wrote of it.
    waited = [_prompt(now - 900, "take the parser slice and write it through an agent"),
              *_response(now - 895, "msg-1", "tool_use", R1, _call("call-1", "Agent", prompt="write src/parser.py")),
              _named(now - 700, "call-1", "the parser is written", agentId="a1", status="completed"),
              *_response(now - 690, "msg-2", "end_turn", R2, _text("the parser is written and its checks pass"))]
    cfg, _ = _world(project, monkeypatch, tmp_path / "waited", waited, {"agent-a1.jsonl": (done, now - 701)})
    assert A.busy(cfg, A.implementer_adapter(cfg))[0] == "idle" and A.turn_ended(cfg) is True


# ── the bound ──

def test_the_liveness_readers_stat_the_subagent_transcripts_and_open_none_of_them(project, monkeypatch, tmp_path):
    now = time.time()
    _running(monkeypatch)
    subagents = {f"agent-a{n}.jsonl": (_subagent(now, 30 + n), None) for n in range(12)}
    subagents.update({f"workflows/wf-1/agent-w{n}.jsonl": (_subagent(now, 50 + n), None) for n in range(12)})
    subagents["agent-a0.jsonl"] = (_subagent(now, 30) + [_result(now - 30, "sub-call-1", "x" * 1_000_000)] * 3, None)
    cfg, transcript = _world(project, monkeypatch, tmp_path, _background(now), subagents)
    beside = os.path.realpath(tmp_path / "s1" / "subagents")
    for name in subagents:
        with open(os.path.join(beside, name[:-len(".jsonl")] + ".meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"agentType": "general-purpose", "toolUseId": "call-1"}, fh)
    adapter = A.implementer_adapter(cfg)
    opened, real_open = [], builtins.open

    def spy(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            opened.append(os.path.realpath(os.fsdecode(file)))
        return real_open(file, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", spy)

    state, _, _ = A.busy(cfg, adapter)
    ended = A.turn_ended(cfg)
    last = A.last_write(str(transcript), A.transcript_shape(adapter))
    A.record_progress(project["root"], cfg)

    assert (state, ended, last) == ("working", False, os.path.getmtime(os.path.join(beside, "agent-a0.jsonl")))
    assert [path for path in opened if path.startswith(beside + os.sep)] == []
    with real_open(os.path.join(project["root"], ".ao", "ledger", "progress.jsonl"), encoding="utf-8") as fh:
        size = json.loads(fh.readlines()[-1])["size"]
    assert size == os.path.getsize(transcript) + sum(os.path.getsize(os.path.join(beside, name)) for name in subagents)
    assert os.path.getsize(os.path.join(beside, "agent-a0.jsonl")) > 3_000_000     # longer than any tail a reader takes
    # A reading that joins subagents to their turns opens them, sidecars and all, and the spy sees it.
    A.turn_costs(cfg)
    assert os.path.join(beside, "agent-a0.jsonl") in opened and os.path.join(beside, "agent-a0.meta.json") in opened


def test_a_subagent_that_writes_while_nothing_is_produced_is_busy_without_progress(project, monkeypatch, tmp_path):
    now = time.time()
    cfg, _ = _world(project, monkeypatch, tmp_path, _background(now), {"agent-a1.jsonl": (_subagent(now, 20), None)})
    subagent = tmp_path / "s1" / "subagents" / "agent-a1.jsonl"
    clock = [now]
    monkeypatch.setattr(A.time, "time", lambda: clock[0])

    for minute in (0, 4, 8):
        clock[0] = now + minute * 60
        with open(subagent, "a", encoding="utf-8") as fh:         # the subagent retries; nothing else moves
            fh.write(_lines([_result(clock[0], "sub-call-1", "1 failed")]))
        A.record_progress(project["root"], cfg)

    assert A.spinning(project["root"]) == 8


# ── errors ──

def test_a_subagents_failed_calls_are_not_the_implementers_errors_and_a_failed_subagent_is(project, monkeypatch,
                                                                                          tmp_path):
    now = time.time()
    session = [
        _prompt(now - 600, "write the parser through an agent, then run the cli checks"),
        *_response(now - 595, "msg-1", "tool_use", R1, _call("call-1", "Agent", prompt="write src/parser.py")),
        _named(now - 500, "call-1", "the parser is written and its checks pass", agentId="a1", status="completed"),
        *_response(now - 490, "msg-2", "tool_use", R2,
                   _call("call-2", "Bash", command="python3 -m pytest -q tests/test_cli.py")),
        _result(now - 480, "call-2", "collected 2 items\nFAIL tests/test_cli.py::test_help\nexit code: 1",
                is_error=True),
        *_response(now - 470, "msg-3", "end_turn", R3, _text("the parser passes, and the help test fails on its own")),
    ]
    subagent = [
        _prompt(now - 594, "write src/parser.py"),
        *_streamed(now - 590, "sub-1", "tool_use", S1,
                   _call("sub-call-1", "Bash", command="python3 -m pytest -q tests/test_parser.py")),
        _result(now - 580, "sub-call-1", "collected 3 items\nFAIL tests/test_parser.py::test_empty\nexit code: 1",
                is_error=True),
        *_streamed(now - 570, "sub-2", "end_turn", S2, _text("the empty-input guard is added and the checks pass")),
    ]
    cfg, transcript = _world(project, monkeypatch, tmp_path, session, {"agent-a1.jsonl": (subagent, None)})
    adapter = A.implementer_adapter(cfg)

    errors = A.recent_errors(A.read_tail(str(transcript)), 3, adapter)

    assert [text for _, text in errors] == ["FAIL tests/test_cli.py::test_help"]
    panel = re.sub(r"\x1b\[[0-9;]*m", "", cli.render(cfg, 0))
    assert "FAIL tests/test_cli.py::test_help" in panel and "test_parser.py::test_empty" not in panel
    # A subagent that failed reaches the implementer as the failed result of the call that waited for it.
    failed = [*session[:2], dict(_result(now - 500, "call-1", "the agent failed: the parser's checks never passed",
                                         is_error=True), toolUseResult={"agentId": "a1", "status": "failed"})]
    cfg, transcript = _world(project, monkeypatch, tmp_path / "failed", failed, {"agent-a1.jsonl": (subagent, None)})
    assert [text for _, text in A.recent_errors(A.read_tail(str(transcript)), 3, adapter)] == [
        "the agent failed: the parser's checks never passed"]


# ── records that are no one's words, and turns ──

def test_a_notification_a_note_and_a_summary_are_no_ones_words_and_the_turns_the_model_answers_stand(
        project, monkeypatch, tmp_path):
    now = time.time()
    records = [
        _prompt(now - 900, "take the parser slice and have its checks run beside it"),
        *_response(now - 890, "msg-1", "end_turn", R1, _text("parser written, and its checks run in the background")),
        # A background task ends: the harness tells the model, which answers without a person.
        dict(_prompt(now - 700, "<task-notification><status>completed</status><summary>the checks ran</summary>"
                                "</task-notification>"), origin={"kind": "task-notification"}),
        *_response(now - 695, "msg-2", "tool_use", R2, _call("call-2", "Bash", command="git status --short")),
        _result(now - 690, "call-2", " M src/parser.py"),
        *_response(now - 685, "msg-3", "end_turn", R3, _text("checks passed, and the parser is ready to commit")),
        # Another session's message is a note the harness writes, and the model answers it all the same.
        dict(_prompt(now - 500, "the reviewer session asks whether the parser slice is ready for its review"),
             isMeta=True, origin={"kind": "peer"}),
        *_response(now - 495, "msg-4", "end_turn", R4, _text("ready: the parser slice can go to its review right now")),
        # The summary of a compacted conversation, written while a turn runs, does not split it.
        _prompt(now - 300, "now add the empty-input guard, with a test for it"),
        *_response(now - 295, "msg-5", "tool_use", R5, _call("call-5", "Read", file_path="src/parser.py")),
        _result(now - 290, "call-5", "def parse(text): ..."),
        dict(_prompt(now - 285, "This session is being continued from a previous conversation that ran out of "
                                "context."), isCompactSummary=True),
        *_response(now - 280, "msg-6", "end_turn", R6, _text("guard and its test are written for the empty input")),
    ]
    cfg, transcript = _world(project, monkeypatch, tmp_path, records, {})
    shipped = A.implementer_adapter(cfg)
    recs = A.read_tail(str(transcript))

    costs = A.turn_costs(cfg)

    assert [(turn["usage"], turn["tool_calls"]) for turn in costs["turns"]] == [
        (_spent(R1), 0), (_spent(R2, R3), 1), (_spent(R4), 0), (_spent(R5, R6), 1)]
    assert A.telemetry(recs, shipped, str(transcript))["turns"] == 4
    assert [(role, text.split()[0]) for _, role, text in A.messages(recs, 8, shipped)] == [
        ("user", "take"), ("assistant", "parser"), ("assistant", "checks"), ("assistant", "ready:"), ("user", "now"),
        ("assistant", "guard")]
    # Words and turns are two readings: without `not_words` the same records speak, and the turns are the same.
    messages = {key: value for key, value in shipped["transcript"]["messages"].items() if key != "not_words"}
    undeclared = dict(shipped, transcript=dict(shipped["transcript"], messages=messages))
    assert ("user", "This") in [(role, text.split()[0]) for _, role, text in A.messages(recs, 8, undeclared)]
    assert A.telemetry(recs, undeclared, str(transcript))["turns"] == 4
    # A notification after the turn's end opens the turn the model will answer, for the watchdog too.
    assert A.turn_ended(cfg) is True
    _append(transcript, dict(_prompt(now - 10, "<task-notification><status>completed</status></task-notification>"),
                             origin={"kind": "task-notification"}))
    assert A.turn_ended(cfg) is False
