"""A subagent path stays in its session's directory, a background task's failed end is a failure, and a reply no
model wrote answers no turn of the implementer's.

`transcript.subagents` names files ao reads and stats, and an adapter layer the agent it describes can write may
declare it. A `dir`, a transcript's `path` or a sidecar's `path` holding `..`, an absolute path or a drive is
refused where it is declared, a `dir` that does not name its session too, and every file is read by its real path
only when that path is inside the session's own subagent directory: a symbolic link whose target leaves it is not
followed, and wildcards keep matching inside it. The declaration stays readable from every layer, confined.

A task the implementer runs in the background ends after the call that started it returned, and the harness tells
the session in a record of its own (`telemetry.failure.ends`): an end whose status is a failure reaches the panel's
problems as a failed result does, once however often it is told. A reply the harness writes in the model's place
with no usage (`transcript.messages.harness_replies`) answers nothing: a turn holding no other reply is counted
apart as unanswered, and in no count of the implementer's turns. No core module names a value these declare.
"""
import ast
import builtins
import copy
import json
import os
import re
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, mcp
from tests.test_harness_guard import _docstrings
from tests.test_second_harness_cost import (HARNESS, R1, R2, R3, R4, R5, R6, _call, _prompt, _response, _result,
                                            _spent, _stamp, _text, _usage)
from tests.test_subagent_spend import S1, S2, _lines, _named, _streamed
from tests.test_transcript_shape import ORDINARY, ROOT, _steps

X1 = _usage(7, 7000, 70000, 700)


def _write(path, records, at):
    """A transcript as the store leaves it, last written at `at`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_lines(records), encoding="utf-8")
    os.utime(path, (at, at))
    return path


def _link(target, link):
    """A symbolic link at `link`, or the test is skipped where this platform makes none."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=os.path.isdir(target))
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"no symbolic link can be made here: {exc}")


def _subagent(root, now, ident, usage, name=None):
    """A subagent's records: one response, writing a file of the project when `name` is given."""
    block = _call(f"{ident}-call", "Write", file_path=os.path.join(root, "src", name), content="x = 1\n") if name \
        else _text("the work is done")
    return [_prompt(now - 575, "the work the session delegated"), *_streamed(now - 570, ident, "tool_use", usage, block)]


def _session(now):
    """One turn that starts two subagents: a1, beside the session where the harness writes it, and x1, which no
    directory of this session holds."""
    return [
        _prompt(now - 600, "write the parser and its checks through agents"),
        *_response(now - 595, "msg-1", "tool_use", R1, _call("call-1", "Agent", prompt="write src/mine.py")),
        _named(now - 590, "call-1", "the parser is written", agentId="a1", status="completed"),
        *_response(now - 585, "msg-2", "tool_use", R2, _call("call-2", "Agent", prompt="write src/theirs.py")),
        _named(now - 540, "call-2", "the checks are written", agentId="x1", status="completed"),
        *_response(now - 530, "msg-3", "end_turn", R3, _text("the parser and its checks are written")),
    ]


def _store(project, monkeypatch, tmp_path, now, session=None, adapter=HARNESS):
    """The implementer's session s1 in a store directory with its subagent a1; another session s2 whose subagent is
    named x1, and a directory outside the store holding a transcript named x1, both written after s1 last was."""
    root, store = project["root"], tmp_path / "store"
    transcript = _write(store / "s1.jsonl", _session(now) if session is None else session, now - 530)
    _write(store / "s1" / "subagents" / "agent-a1.jsonl", _subagent(root, now, "sub-a1", S1, "mine.py"), now - 560)
    (store / "s1" / "subagents" / "agent-a1.meta.json").write_text(json.dumps({"toolUseId": "call-1"}),
                                                                   encoding="utf-8")
    _write(store / "s2.jsonl", [_prompt(now - 20, "another session in the same directory")], now - 20)
    for place in (store / "s2" / "subagents", tmp_path / "outside"):
        _write(place / "agent-x1.jsonl", _subagent(root, now, "sub-x1", X1, "theirs.py"), now - 10)
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    return dict(project, implementer={"adapter": adapter, "session": "s1"}), transcript


def _layered(project, ident=HARNESS, subagents=None, **changes):
    """The shipped declarations of the harness in the project's adapter layer, as `ident`, with `changes` made."""
    adapter = copy.deepcopy(A.load_adapter(HARNESS))
    adapter["id"] = ident
    if subagents is not None:
        adapter["transcript"]["subagents"] = subagents
    for path, value in changes.items():
        *steps, last = path.split("__")
        block = adapter
        for step in steps:
            block = block[step]
        if value is None:
            block.pop(last, None)
        else:
            block[last] = value
    os.makedirs(os.path.join(project["root"], ".ao", "adapters"), exist_ok=True)
    with open(os.path.join(project["root"], ".ao", "adapters", f"{ident}.json"), "w", encoding="utf-8") as fh:
        json.dump(adapter, fh)
    return adapter


def _opened(monkeypatch):
    """The real path of every file opened from here on."""
    opened, real_open = [], builtins.open

    def spy(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            opened.append(os.path.realpath(os.fsdecode(file)))
        return real_open(file, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", spy)
    return opened


# ── a subagent path is confined to its session's subagent directory ──

ESCAPES = {
    "..": ({"dir": "{session}/../s2/subagents"}, False),
    "an absolute path": ({"transcripts": [{"path": "{outside}/agent-{id}.jsonl", "named_by": "toolUseResult.agentId"}]},
                         True),
    "a wildcard": ({"transcripts": [{"path": "../../*/subagents/agent-{id}.jsonl", "named_by": "toolUseResult.agentId"}]},
                   True),
    "another session's directory": ({"dir": "s2/subagents"}, False),
}


@pytest.mark.parametrize("escape", sorted(ESCAPES))
def test_a_subagent_path_that_leaves_the_sessions_directory_is_refused_whatever_layer_declares_it(
        escape, project, monkeypatch, tmp_path):
    now = time.time()
    change, keeps_the_shipped_paths = ESCAPES[escape]
    shipped = A.load_adapter(HARNESS)["transcript"]["subagents"]
    outside = (tmp_path / "outside").as_posix()
    declared = copy.deepcopy(shipped)
    for key, value in change.items():
        value = json.loads(json.dumps(value).replace("{outside}", outside))
        declared[key] = declared[key] + value if key == "transcripts" and keeps_the_shipped_paths else value
    adapter = _layered(project, subagents=declared)
    cfg, transcript = _store(project, monkeypatch, tmp_path, now)
    root, shape = project["root"], A.transcript_shape(A.implementer_adapter(cfg))
    opened = _opened(monkeypatch)
    expected = _spent(S1) if keeps_the_shipped_paths else 0.0

    costs = A.turn_costs(cfg)

    # x1's transcripts sit in another session's directory and outside the store: neither is this session's subagent.
    assert (costs["total"], costs["delegated"]) == (_spent(R1, R2, R3) + expected, expected)
    assert A.telemetry(A.read_tail(str(transcript)), A.implementer_adapter(cfg), str(transcript))["delegated"] == expected
    assert os.path.realpath(os.path.join(root, "src", "theirs.py")) not in A.implementer_recent_writes(cfg)
    away = [os.path.realpath(tmp_path / "outside"), os.path.realpath(tmp_path / "store" / "s2")]
    assert [path for path in opened if any(path.startswith(place + os.sep) for place in away)] == []
    # Written after the session's own transcript, they would keep its ended turn running and its silence short.
    assert A.turn_ended(cfg) is True and A.last_write(str(transcript), shape) == os.path.getmtime(transcript)
    assert all(not any(path.startswith(place + os.sep) for place in away)
               for path in (A.subagents(str(transcript), shape) or {"files": []})["files"])
    # A person writing the declaration is told why it reads nothing there.
    problems = A.validate_adapter(adapter)
    assert any(problem.startswith("`transcript.subagents.") for problem in problems) \
        and A.subagent_problems(A.load_adapter(HARNESS)) == []


def test_a_symbolic_link_whose_target_leaves_the_subagent_directory_is_not_followed(project, monkeypatch, tmp_path):
    now = time.time()
    root = project["root"]
    session = [
        _prompt(now - 600, "write the parser, its checks and its docs through agents"),
        *_response(now - 595, "msg-1", "tool_use", R1, _call("call-1", "Agent", prompt="write src/mine.py"),
                   _call("call-2", "Agent", prompt="write src/theirs.py"), _call("call-9", "Agent", prompt="the docs")),
        _named(now - 590, "call-1", "the parser is written", agentId="a1", status="completed"),
        _named(now - 589, "call-2", "the checks are written", agentId="x1", status="completed"),
        _result(now - 588, "call-9", "the docs are written"),               # names no agent: only a sidecar joins it
        *_response(now - 585, "msg-2", "tool_use", R2, _call("call-3", "Workflow", script="checks.js"),
                   _call("call-4", "Workflow", script="docs.js")),
        _named(now - 580, "call-3", "the checks run", runId="wf-2", status="async_launched"),
        _named(now - 579, "call-4", "the docs run", runId="wf-9", status="async_launched"),
        *_response(now - 530, "msg-3", "end_turn", R3, _text("the parser, its checks and its docs are under way")),
    ]
    cfg, transcript = _store(project, monkeypatch, tmp_path, now, session=session)
    beside, outside = tmp_path / "store" / "s1" / "subagents", tmp_path / "outside"
    # A transcript, a directory a wildcard reaches and a sidecar, each a link to a file outside.
    _link(outside / "agent-x1.jsonl", beside / "agent-x1.jsonl")
    _write(outside / "runs" / "wf-9" / "agent-w9.jsonl", _subagent(root, now, "sub-w9", X1), now - 10)
    _link(outside / "runs" / "wf-9", beside / "workflows" / "wf-9")
    _write(beside / "agent-o1.jsonl", _subagent(root, now, "sub-o1", X1), now - 565)
    (outside / "o1.meta.json").write_text(json.dumps({"toolUseId": "call-9"}), encoding="utf-8")
    _link(outside / "o1.meta.json", beside / "agent-o1.meta.json")
    # A link whose target stays inside the directory is followed.
    _write(beside / "runs" / "wf-2" / "agent-w2.jsonl", _subagent(root, now, "sub-w2", S2), now - 566)
    _link(beside / "runs" / "wf-2", beside / "workflows" / "wf-2")
    shape = A.transcript_shape(A.implementer_adapter(cfg))
    opened = _opened(monkeypatch)
    real = os.path.realpath

    found = A.subagents(str(transcript), shape)

    inside = [real(beside / "agent-a1.jsonl"), real(beside / "agent-o1.jsonl"),
              real(beside / "runs" / "wf-2" / "agent-w2.jsonl")]
    assert found == {"files": inside, "named": {(0, "a1"): inside[:1], (0, "o1"): inside[1:2], (1, "wf-2"): inside[2:]},
                     "calls": {"call-1": inside[:1]}}
    costs = A.turn_costs(cfg)
    # a1 and the workflow run reached through the inner link are charged; o1, whose sidecar leaves, is joined by nothing.
    assert costs["delegated"] == _spent(S1, S2)
    assert A.implementer_recent_writes(cfg) == {real(os.path.join(root, "src", "mine.py"))}
    assert len(A.subagent_writes(str(transcript), shape)) == 3 and A.turn_ended(cfg) is True
    assert [path for path in opened if path.startswith(real(outside) + os.sep)] == []


def test_the_subagents_declared_inside_the_sessions_directory_are_read_from_any_layer_as_before(project, monkeypatch,
                                                                                                tmp_path):
    now = time.time()
    cfg, transcript = _store(project, monkeypatch, tmp_path, now)
    beside = os.path.realpath(tmp_path / "store" / "s1" / "subagents")
    shipped = A.load_adapter(HARNESS)

    found = A.subagents(str(transcript), A.transcript_shape(shipped))

    mine = [os.path.join(beside, "agent-a1.jsonl")]
    assert found == {"files": mine, "named": {(0, "a1"): mine}, "calls": {"call-1": mine}}
    assert A.turn_costs(cfg)["delegated"] == _spent(S1) and A.subagent_problems(shipped) == []
    assert A.implementer_recent_writes(cfg) == {os.path.realpath(os.path.join(project["root"], "src", "mine.py"))}
    # The store reached through a link to its directory is the same store.
    _link(tmp_path / "store", tmp_path / "linked")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(tmp_path / "linked" / "s1.jsonl"), None))
    assert A.turn_costs(cfg)["delegated"] == _spent(S1)
    # A harness declared in a project's adapter layer delegates the same way, and is confined the same way.
    _layered(project, ident="bounds-fixture")
    layered = dict(cfg, implementer={"adapter": "bounds-fixture", "session": "s1"})
    assert A.implementer_adapter(layered)["id"] == "bounds-fixture" and A.turn_costs(layered)["delegated"] == _spent(S1)


# ── a background task's failed end is a failure ──

def _notification(status, summary, *tasks, call=None, event=None):
    """The text a background task's end is told in."""
    return ("<task-notification>" + "".join(f"<task-id>{task}</task-id>" for task in tasks)
            + (f"<tool-use-id>{call}</tool-use-id>" if call else "") + (f"<event>{event}</event>" if event else "")
            + (f"<status>{status}</status>" if status else "") + f"<summary>{summary}</summary></task-notification>")


def _told(at, *args, **kwargs):
    """A background task's end told when no turn runs: a record of the prompt's kind, which the model answers."""
    return dict(_prompt(at, _notification(*args, **kwargs)), origin={"kind": "task-notification"})


def _queued(at, *args, **kwargs):
    """The same end told while a turn runs: queued into it as an attachment."""
    return {"type": "attachment", "timestamp": _stamp(at),
            "attachment": {"type": "queued_command", "prompt": _notification(*args, **kwargs),
                           "commandMode": "task-notification", "timestamp": _stamp(at)}}


CHECKS = 'Background command "python3 -m pytest -q" failed with exit code 1'
GUARD = 'Agent "the guard" failed: Agent terminated early due to an API error'
DOCS = 'No completion record was found for background agent "the docs" from the previous session.'


def test_a_failed_or_stopped_background_end_reaches_the_failure_signal_once(project, monkeypatch, tmp_path):
    now = time.time()
    records = [
        _prompt(now - 900, "run the parser's checks in the background, and write the guard through an agent"),
        *_response(now - 895, "msg-1", "tool_use", R1,
                   _call("call-1", "Bash", command="python3 -m pytest -q", run_in_background=True),
                   _call("call-2", "Agent", prompt="write src/guard.py", run_in_background=True)),
        _result(now - 894, "call-1", "Command running in background with ID: b1"),
        _named(now - 893, "call-2", "the agent works in the background", agentId="a1", status="async_launched",
               isAsync=True),
        # The checks fail, and then the agent, while the turn runs: both ends are queued into it.
        _queued(now - 880, "failed", CHECKS, "b1", call="call-1"),
        _queued(now - 875, "failed", GUARD, "a1", call="call-2"),
        *_response(now - 870, "msg-2", "end_turn", R2, _text("the checks and the guard agent both failed")),
        # The checks' end is told again once the turn is over; a monitor's event carries no status.
        _told(now - 860, "failed", CHECKS, "b1", call="call-1"),
        _told(now - 855, None, "Monitor event: the log grew", "m1", event="a line"),
        *_response(now - 850, "msg-3", "end_turn", R3, _text("the checks still fail on the empty input")),
        # A command a person stopped, and one that completed, are not failures.
        _told(now - 700, "killed", 'Background command "npm run dev" was stopped', "b2"),
        _told(now - 690, "completed", 'Background command "git fetch" completed (exit code 0)', "b3"),
        *_response(now - 680, "msg-4", "end_turn", R4, _text("the dev server is stopped and the fetch is done")),
        # Resumed, the session finds an agent of its previous process unfinished, and is told so twice.
        _told(now - 600, "stopped", DOCS, "a2"),
        *_response(now - 595, "msg-5", "end_turn", R5, _text("the docs agent must be run again, from the start")),
        _told(now - 590, "stopped", DOCS, "a2"),
        # A person who pastes such a text is a person speaking.
        _prompt(now - 500, _notification("failed", "pasted into the prompt, not told", "b9")),
        *_response(now - 495, "msg-6", "end_turn", R6, _text("that notification was pasted, not a failure here")),
    ]
    transcript = _write(tmp_path / "s1.jsonl", records, now - 495)
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    cfg = dict(project, implementer={"adapter": HARNESS, "session": "s1"})
    recs, shipped = A.read_tail(str(transcript)), A.implementer_adapter(cfg)

    errors = A.recent_errors(recs, 10, shipped)

    assert [text for _, text in errors] == [GUARD, CHECKS, DOCS]
    panel = re.sub(r"\x1b\[[0-9;]*m", "", cli.render(cfg, 0, width=130))
    assert "agent error" in panel and CHECKS in panel and DOCS in panel and panel.count(DOCS) == 1
    assert [text for _, text in A.recent_errors(recs, 2, shipped)] == [CHECKS, DOCS]
    # The records are what the adapter declares: undeclared, no end reaches the signal, as before.
    failure = {key: value for key, value in shipped["telemetry"]["failure"].items() if key != "ends"}
    undeclared = dict(shipped, telemetry=dict(shipped["telemetry"], failure=failure))
    assert A.recent_errors(recs, 10, undeclared) == []
    # A failed result of the implementer's own call still reads beside them, newest last.
    _write(transcript, records + [*_response(now - 400, "msg-7", "tool_use", R1, _call("call-7", "Bash", command="make")),
                                  _result(now - 395, "call-7", "make: *** [all] Error 2", is_error=True)], now - 395)
    assert [text for _, text in A.recent_errors(A.read_tail(str(transcript)), 2, shipped)] == [
        DOCS, "make: *** [all] Error 2"]


# ── a reply the harness wrote in the model's place ──

def _in_place(at, ident, text, **marks):
    """A reply the harness writes itself: its own model name, no usage, closed by a stop sequence."""
    none = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0,
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}, "service_tier": None}
    return dict({"type": "assistant", "timestamp": _stamp(at),
                 "message": {"id": ident, "model": "<synthetic>", "type": "message", "role": "assistant",
                             "content": [_text(text)], "stop_reason": "stop_sequence", "stop_sequence": "",
                             "usage": none}}, **marks)


def test_a_turn_the_harness_answered_in_the_models_place_is_counted_apart_and_in_no_count_of_work(
        project, monkeypatch, tmp_path, capsys):
    now = time.time()
    root = project["root"]
    records = [
        _prompt(now - 900, "take the parser slice and write its tests"),
        *_response(now - 890, "msg-1", "end_turn", R1, _text("the parser slice and its tests are written")),
        # The service refuses the next prompt: the harness replies in the model's place and nothing is spent.
        _prompt(now - 800, "now the empty-input guard, with a test for it"),
        _in_place(now - 799, "reply-1", "API Error: 529 the service is overloaded", isApiErrorMessage=True),
        # A note that asks the model nothing is answered by the harness too.
        dict(_prompt(now - 700, "the reviewer session says the parser slice is ready for review"), isMeta=True),
        _in_place(now - 699, "reply-2", "No response requested."),
        # Asked again, the model answers and writes; the service fails inside that turn, which stands.
        _prompt(now - 600, "now the empty-input guard, with a test for it"),
        *_response(now - 590, "msg-2", "tool_use", R2,
                   _call("call-2", "Write", file_path=os.path.join(root, "src", "guard.py"), content="x = 1\n")),
        _result(now - 585, "call-2", "File created successfully"),
        _in_place(now - 580, "reply-3", "API Error: Connection closed mid-response.", isApiErrorMessage=True),
    ]
    transcript = _write(tmp_path / "s1.jsonl", records, now - 580)
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    cfg = dict(project, implementer={"adapter": HARNESS, "session": "s1"})
    recs, shipped = A.read_tail(str(transcript)), A.implementer_adapter(cfg)

    costs = A.turn_costs(cfg)

    assert [(turn["cls"], turn["usage"]) for turn in costs["turns"]] == [
        ("analysis", _spent(R1)), (A.UNANSWERED, 0.0), (A.UNANSWERED, 0.0), ("product", _spent(R2))]
    assert costs["by_class"][A.UNANSWERED]["turns"] == 2 and costs["total"] == _spent(R1, R2)
    assert A.feature_costs(cfg)["turns"] == 2
    assert cli.cmd_cost(cfg, SimpleNamespace(since=None)) == 0
    shown = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert re.search(rf"total\s+2\s+{_spent(R1, R2)}\b", shown) and "unanswered: 2 more turn(s)" in shown
    tel = A.telemetry(recs, shipped, str(transcript))
    assert (tel["turns"], tel["total"], tel["last"]) == (2, _spent(R1, R2), (_spent(R2), 1))
    assert mcp.status_payload(cfg)["turns"] == 2
    # The reply still ends the turn it falls in: the watchdog reads the session as done, not as working.
    assert A.turn_ended(cfg) is True
    # Undeclared, the same records are four turns of work, as they were.
    _layered(project, ident="unmarked-fixture", transcript__messages__harness_replies=None)
    unmarked = dict(cfg, implementer={"adapter": "unmarked-fixture", "session": "s1"})
    assert [turn["cls"] for turn in A.turn_costs(unmarked)["turns"]] == ["analysis", "analysis", "analysis", "product"]
    assert A.telemetry(recs, A.implementer_adapter(unmarked), str(transcript))["turns"] == 4
    # The credit estimate, had the adapter declared the transcript, counts the same two turns and the same spend.
    declared = dict(A.package_adapters()[HARNESS], billing={"fallback": {"transcripts": str(transcript),
                                                                         "reading": "per-response"}})
    monkeypatch.setattr(A, "package_adapters", lambda: {HARNESS: declared})
    estimate = A.credit_usage(adapter_id=HARNESS)
    assert sum(estimate["days"].values()) == _spent(R1, R2) and estimate["sessions"][0]["turns"] == 2


# ── the core names nothing these declarations hold ──

def _declared_words():
    """Every path step, value and marker a shipped adapter declares for a background task's end or a harness's reply."""
    words = set()
    for path in sorted((ROOT / "src" / "ao" / "adapters").glob("*.json")):
        adapter = json.loads(path.read_text(encoding="utf-8"))
        transcript = adapter.get("transcript") if isinstance(adapter.get("transcript"), dict) else {}
        messages = transcript.get("messages") if isinstance(transcript.get("messages"), dict) else {}
        telemetry = adapter.get("telemetry") if isinstance(adapter.get("telemetry"), dict) else {}
        failure = telemetry.get("failure") if isinstance(telemetry.get("failure"), dict) else {}
        ends = failure.get("ends") if isinstance(failure.get("ends"), dict) else {}
        matches = list(messages.get("harness_replies") or [])
        for record in ends.get("records") or []:
            if isinstance(record, dict):
                words |= set(_steps(record.get("type"))) | set(_steps(record.get("field")))
                matches.append(record.get("match"))
        for match in matches:
            for key, value in match.items() if isinstance(match, dict) else ():
                words |= set(_steps(key)) | set(_steps(value))
        for key in ("status", "id", "text", "failed_when"):
            words |= set(_steps(ends.get(key)))
    # Plain words of a path or a status name nothing of a harness's: a record's kind, a model, a failure.
    return words - ORDINARY - {"kind", "origin", "model", "attachment", "prompt", "failed", "stopped"}


def test_no_core_module_names_a_value_that_marks_a_background_end_or_a_reply_no_model_wrote():
    words = _declared_words()
    assert {"<synthetic>", "commandMode", "task-notification", "<status>", "<task-id>", "</summary>"} <= words
    word = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True))
                      + r")(?![A-Za-z0-9_])")

    found = []
    for path in sorted((ROOT / "src" / "ao").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        found += [f"{path.name}:{node.lineno} {node.value[:80]!r}" for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
                  and word.search(node.value)]

    assert found == []
