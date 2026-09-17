"""A subagent's spend, calls and writes are its implementer's, and a harness's own notes are no one's words.

A session of the second shipped harness that delegates writes each subagent's records to a transcript of
its own beside the session's - `<session>/subagents/agent-<id>.jsonl`, or `workflows/<run>/agent-<id>.jsonl`
for the agents a workflow run starts - and the adapter declares where they are and what joins each to the
session (`transcript.subagents`): the result of the call that started one names it, and its sidecar names
the call. Every reading charges a subagent's spend to the turn that started it, once, and as delegated:
`ao cost`, the panel, `ao_status` and the credit estimate. A subagent writes a response's records as it
streams them, its output tokens growing record by record, so a response costs its highest record; the
usage the call's result carries is one response's, and is never read. A transcript nothing joins is not
counted: its spend is in no turn the reading holds. A subagent's calls and writes are the implementer's,
joined or not, so a person's edit beside them is still told apart. A record the harness marks as a note of
its own, a compaction's summary or a background task's notification is not the person speaking. No core
module names a field these declarations hold.
"""
import ast
import json
import os
import re
import time
from types import SimpleNamespace

from ao import cli, lib as A, mcp
from tests.test_harness_guard import _docstrings
from tests.test_second_harness_cost import (HARNESS, R1, R2, R3, R4, _call, _prompt, _response, _result, _spent,
                                            _stamp, _text, _usage)
from tests.test_transcript_shape import ORDINARY, ROOT, _steps

S1, S2 = _usage(4, 900, 12000, 300), _usage(2, 50, 12900, 120)
S3, S4 = _usage(3, 700, 8000, 200), _usage(1, 40, 3000, 60)
ORPHAN = _usage(5, 5000, 50000, 900)


def _streamed(at, ident, stop, usage, *blocks):
    """A response as a subagent's transcript writes it: a record per block as it streams, its output tokens growing."""
    return [{"type": "assistant", "timestamp": _stamp(at), "isSidechain": True,
             "message": {"id": ident, "type": "message", "role": "assistant", "content": [block], "stop_reason": stop,
                         "usage": dict(usage, output_tokens=usage["output_tokens"] * n // len(blocks))}}
            for n, block in enumerate(blocks, 1)]


def _named(at, call, content, **named):
    """The result of a call that started a subagent, naming it as the store does, with one response's usage beside."""
    return dict(_result(at, call, content), toolUseResult=dict(named, usage=_usage(9, 9, 9, 9), totalTokens=36))


def _delegating(root, now):
    """Two turns: the first starts a subagent, which starts one of its own, and a workflow run; the second, none."""
    return [
        _prompt(now - 600, "write the parser the board names, and have its checks run beside it"),
        *_response(now - 590, "msg-1", "tool_use", R1, _text("a subagent writes the parser"),
                   _call("call-1", "Agent", description="the parser", prompt="write src/mine.py")),
        _named(now - 500, "call-1", "the parser is written", agentId="a1", status="completed"),
        *_response(now - 490, "msg-2", "tool_use", R2, _call("call-2", "Workflow", script="checks.js")),
        _named(now - 489, "call-2", "the checks run in the background", runId="wf-1", status="async_launched"),
        *_response(now - 480, "msg-3", "end_turn", R3, _text("the parser is written and its checks are running")),
        _prompt(now - 300, "what is left on the board"),
        *_response(now - 290, "msg-4", "end_turn", R4, _text("nothing is left on the board")),
    ]


def _subagents(root, now):
    """{name under the session's subagent directory: records}. The workflow's agent writes during the second turn."""
    return {
        "agent-a1.jsonl": [
            _prompt(now - 585, "write src/mine.py"),
            *_streamed(now - 580, "sub-1", "tool_use", S1, _text("writing the parser"),
                       _call("sub-call-1", "Write", file_path=os.path.join(root, "src", "mine.py"), content="x = 1\n")),
            _result(now - 570, "sub-call-1", "File created successfully"),
            *_streamed(now - 560, "sub-2", "tool_use", S2, _call("sub-call-2", "Agent", prompt="review the parser")),
            _result(now - 520, "sub-call-2", "the parser reads well"),
            *_streamed(now - 510, "sub-3", "end_turn", S3, _text("the parser is written"), _text("and reviewed")),
        ],
        "agent-n1.jsonl": [
            _prompt(now - 550, "review the parser"),
            *_streamed(now - 540, "sub-4", "end_turn", S4, _text("the parser reads well")),
        ],
        "workflows/wf-1/agent-w1.jsonl": [
            _prompt(now - 100, "run the parser's checks"),
            *_streamed(now - 90, "sub-5", "end_turn", S1, {"type": "thinking", "thinking": "run them"},
                       _text("the checks pass")),
        ],
    }


# The subagent a call started, and the one a subagent's call started, are named by their sidecars.
SIDECARS = {"agent-a1.meta.json": "call-1", "agent-n1.meta.json": "sub-call-2"}


def _lines(records):
    return "".join(json.dumps(record) + "\n" for record in records)


def _world(project, monkeypatch, tmp_path, session=None, subagents=None, sidecars=SIDECARS, adapter=HARNESS):
    """The implementer's session transcript, and its subagents' transcripts and sidecars where the harness puts them."""
    root, now = project["root"], time.time()
    tmp_path.mkdir(parents=True, exist_ok=True)
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text(_lines(_delegating(root, now) if session is None else session), encoding="utf-8")
    directory = tmp_path / "s1" / "subagents"
    for name, records in (_subagents(root, now) if subagents is None else subagents).items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(_lines(records), encoding="utf-8")
    for name, call in sidecars.items():
        (directory / name).write_text(json.dumps({"agentType": "general-purpose", "toolUseId": call}),
                                      encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    return dict(project, implementer={"adapter": adapter, "session": "s1"}), transcript


def _undelegated(project):
    """The shipped declaration, but for its subagents, in the project's adapter layer."""
    shipped = A.load_adapter(HARNESS)
    declared = {"id": "undelegated-fixture", "name": "The harness, its subagents undeclared", "verified": "untested",
                "contract": 1, "send": {"argv": ["undelegated-fixture", "{prompt}"]}, "billing": shipped["billing"],
                "telemetry": shipped["telemetry"],
                "transcript": {key: value for key, value in shipped["transcript"].items() if key != "subagents"}}
    os.makedirs(os.path.join(project["root"], ".ao", "adapters"), exist_ok=True)
    with open(os.path.join(project["root"], ".ao", "adapters", "undelegated-fixture.json"), "w",
              encoding="utf-8") as fh:
        json.dump(declared, fh)
    return declared["id"]


OWN, DELEGATED = _spent(R1, R2, R3, R4), _spent(S1, S2, S3, S4, S1)


# ── a subagent's spend is charged once, to the turn that started it ──

def test_a_subagents_spend_is_charged_once_to_the_turn_that_started_it_in_every_reading(project, monkeypatch, tmp_path,
                                                                                      capsys):
    cfg, transcript = _world(project, monkeypatch, tmp_path)
    recs = A.read_tail(str(transcript))
    adapter = A.implementer_adapter(cfg)

    costs = A.turn_costs(cfg)

    # The workflow's agent wrote during the second turn and is the first turn's, which started the run.
    assert [(turn["cls"], turn["usage"], turn["delegated"], turn["tool_calls"]) for turn in costs["turns"]] == [
        ("product", _spent(R1, R2, R3) + DELEGATED, DELEGATED, 4), ("analysis", _spent(R4), 0.0, 0)]
    assert (costs["total"], costs["delegated"]) == (OWN + DELEGATED, DELEGATED)
    assert costs["by_class"]["product"]["delegated"] == DELEGATED
    panel = A.telemetry(recs, adapter, str(transcript))
    assert (panel["total"], panel["turns"], panel["delegated"], panel["last"]) == (OWN + DELEGATED, 2, DELEGATED,
                                                                                  (_spent(R4), 0))
    # Records alone, without the transcript they came from, hold the session's own spend.
    assert (A.telemetry(recs, adapter)["total"], A.telemetry(recs, adapter)["delegated"]) == (OWN, 0.0)
    declared = dict(A.package_adapters()[HARNESS], billing={"fallback": {"transcripts": str(transcript),
                                                                         "reading": "per-response"}})
    monkeypatch.setattr(A, "package_adapters", lambda: {HARNESS: declared})
    estimate = A.credit_usage(adapter_id=HARNESS)
    assert sum(estimate["days"].values()) == OWN + DELEGATED and estimate["sessions"][0]["turns"] == 2

    assert cli.cmd_cost(cfg, SimpleNamespace(since=None)) == 0
    shown = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert re.search(r"class\s+turns\s+spend\s+share\s+delegated", shown)
    assert re.search(rf"product\s+1\s+{_spent(R1, R2, R3) + DELEGATED}\s+\d+%\s+{DELEGATED}\b", shown)
    assert f", {DELEGATED:.0f} delegated)" in re.sub(r"\x1b\[[0-9;]*m", "", cli.render(cfg, 0))
    status = mcp.status_payload(cfg)
    assert (status["cost_total"], status["cost_delegated"]) == (OWN + DELEGATED, DELEGATED)


def test_a_streamed_response_costs_its_highest_record_and_the_usage_a_result_carries_is_never_read(project,
                                                                                                    monkeypatch,
                                                                                                    tmp_path):
    cfg, transcript = _world(project, monkeypatch, tmp_path)
    streamed = A.read_tail(str(tmp_path / "s1" / "subagents" / "agent-a1.jsonl"))
    first = {}
    for record in streamed:
        if record["type"] == "assistant":
            first.setdefault(record["message"]["id"], _spent(record["message"]["usage"]))

    # Its first record holds half of the response's output tokens, and a reading from it would miss the rest.
    assert first["sub-1"] < _spent(S1) and first["sub-3"] < _spent(S3)
    assert A.turn_costs(cfg)["delegated"] == DELEGATED
    # One response is written in the session's transcript and again in a subagent's: it is charged once.
    copy = [record for record in streamed if record["type"] == "assistant" and record["message"]["id"] == "sub-2"]
    transcript.write_text(transcript.read_text(encoding="utf-8") + _lines(copy), encoding="utf-8")
    assert (A.turn_costs(cfg)["total"], A.turn_costs(cfg)["turns"][1]["usage"]) == (OWN + DELEGATED, _spent(R4))


def test_a_subagent_joined_by_its_result_alone_or_by_its_sidecar_alone_is_charged_all_the_same(project, monkeypatch,
                                                                                               tmp_path):
    # Without sidecars the subagent a subagent started is joined by nothing: its call's result names no subagent.
    cfg, _ = _world(project, monkeypatch, tmp_path, sidecars={})
    assert A.turn_costs(cfg)["delegated"] == _spent(S1, S2, S3, S1)

    # With sidecars and no result naming anything, every call a sidecar names joins its subagent.
    now = time.time()
    session = [dict(record, toolUseResult={}) if "toolUseResult" in record else record
               for record in _delegating(project["root"], now)]
    cfg, _ = _world(project, monkeypatch, tmp_path / "sidecars", session=session)
    assert A.turn_costs(cfg)["delegated"] == _spent(S1, S2, S3, S4)


# ── what nothing joins is not counted ──

def test_a_subagent_transcript_nothing_joins_is_not_counted_and_its_writes_are_still_the_implementers(project,
                                                                                                     monkeypatch,
                                                                                                     tmp_path):
    root, now = project["root"], time.time()
    orphan = [_prompt(now - 60, "a subagent no record of the session names"),
              *_streamed(now - 50, "sub-9", "tool_use", ORPHAN,
                         _call("sub-call-9", "Edit", file_path=os.path.join(root, "src", "orphan.py"), old_string="a",
                               new_string="b")),
              _result(now - 45, "sub-call-9", "The file has been updated")]
    cfg, transcript = _world(project, monkeypatch, tmp_path,
                             subagents=dict(_subagents(root, now), **{"agent-a9.jsonl": orphan}))
    recs = A.read_tail(str(transcript))

    assert A.turn_costs(cfg)["total"] == OWN + DELEGATED
    assert A.telemetry(recs, A.implementer_adapter(cfg), str(transcript))["total"] == OWN + DELEGATED
    # The panel reads a tail: a subagent a record before it started is outside it, as that record's turn is.
    second = recs.index(next(record for record in recs if record.get("message", {}).get("content")
                             == "what is left on the board"))
    tail = A.telemetry(recs[second:], A.implementer_adapter(cfg), str(transcript))
    assert (tail["total"], tail["turns"], tail["delegated"]) == (_spent(R4), 1, 0.0)
    assert os.path.realpath(os.path.join(root, "src", "orphan.py")) in A.implementer_recent_writes(cfg)


# ── a subagent's writes are the implementer's ──

def test_a_subagents_writes_are_the_implementers_and_a_persons_edit_beside_them_is_foreign(project, monkeypatch,
                                                                                          tmp_path):
    cfg, _ = _world(project, monkeypatch, tmp_path)
    root = cfg["root"]
    os.makedirs(os.path.join(root, "src"))
    for name in ("mine.py", "theirs.py"):
        with open(os.path.join(root, "src", name), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")

    assert A.implementer_recent_writes(cfg) == {os.path.realpath(os.path.join(root, "src", "mine.py"))}
    assert A.foreign_edits(root, cfg) == ["src/theirs.py"]
    assert A.turn_costs(cfg)["turns"][0]["product_writes"] == 1

    # Undeclared, the subagents are read by nothing: their writes look like a person's, and their spend is gone.
    undeclared = dict(cfg, implementer={"adapter": _undelegated(project), "session": "s1"})
    assert A.implementer_recent_writes(undeclared) == set()
    assert A.foreign_edits(root, undeclared) == ["src/mine.py", "src/theirs.py"]
    costs = A.turn_costs(undeclared)
    assert (costs["total"], costs["delegated"], [turn["cls"] for turn in costs["turns"]]) == (
        OWN, 0.0, ["analysis", "analysis"])


# ── a harness's own notes are no one's words ──

def test_a_note_of_the_harness_a_compaction_summary_and_a_task_notification_are_not_the_person_speaking(
        project, monkeypatch, tmp_path):
    now = time.time()
    records = [
        _prompt(now - 90, "take the next slice on the board and write the tests that prove it"),
        dict(_prompt(now - 89, "Caveat: the messages below were written while the person ran local commands"),
             isMeta=True),
        dict(_prompt(now - 88, "This session is being continued from a previous conversation that ran out of "
                               "context. The summary below covers the earlier part of the conversation."),
             isCompactSummary=True, isVisibleInTranscriptOnly=True),
        *_response(now - 80, "msg-1", "end_turn", R1, _text("the slice is written and its tests pass here too")),
        dict(_prompt(now - 70, "<task-notification><status>completed</status><summary>the checks ran"
                               "</summary></task-notification>"), origin={"kind": "task-notification"}),
        dict(_prompt(now - 60, "and now the empty-input guard, with a test for it"), origin={"kind": "human"}),
    ]
    cfg, transcript = _world(project, monkeypatch, tmp_path, session=records, subagents={}, sidecars={})
    recs = A.read_tail(str(transcript))
    shipped = A.implementer_adapter(cfg)

    assert [(role, text.split()[0]) for _, role, text in A.messages(recs, 8, shipped)] == [
        ("user", "take"), ("assistant", "the"), ("user", "and")]
    # The records are what the adapter declares: without `not_words` the same records speak as the person.
    messages = {key: value for key, value in shipped["transcript"]["messages"].items() if key != "not_words"}
    undeclared = dict(shipped, transcript=dict(shipped["transcript"], messages=messages))
    assert [(role, text.split()[0]) for _, role, text in A.messages(recs, 8, undeclared)] == [
        ("user", "take"), ("user", "Caveat:"), ("user", "This"), ("assistant", "the"),
        ("user", "<task-notification><status>completed</status><summary>the"), ("user", "and")]


# ── the core names nothing these declarations hold ──

def _declared_words():
    """Every path step and value a shipped adapter declares to name a subagent, or a record that is no one's words."""
    words = set()
    for path in sorted((ROOT / "src" / "ao" / "adapters").glob("*.json")):
        transcript = json.loads(path.read_text(encoding="utf-8")).get("transcript")
        transcript = transcript if isinstance(transcript, dict) else {}
        declared = transcript.get("subagents") if isinstance(transcript.get("subagents"), dict) else {}
        for entry in declared.get("transcripts") or []:
            words |= set(_steps(entry.get("named_by"))) if isinstance(entry, dict) else set()
        sidecar = declared.get("sidecar") if isinstance(declared.get("sidecar"), dict) else {}
        words |= set(_steps(sidecar.get("call")))
        messages = transcript.get("messages") if isinstance(transcript.get("messages"), dict) else {}
        for match in messages.get("not_words") or []:
            for key, value in match.items() if isinstance(match, dict) else ():
                words |= set(_steps(key)) | set(_steps(value))
    # A plain word of a path names nothing of a harness's: a record's kind, a mail's origin.
    return words - ORDINARY - {"kind", "origin"}


def test_no_core_module_names_a_field_that_names_a_subagent_or_a_record_that_is_no_ones_words():
    words = _declared_words()
    assert {"toolUseResult", "agentId", "runId", "toolUseId", "isMeta", "isCompactSummary",
            "task-notification"} <= words
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
