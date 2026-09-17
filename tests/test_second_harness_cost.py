"""An implementer on the second shipped harness has its turns, spend, writes, failures and turn end read.

Its session store writes no tool call, tool result or turn end as a record of its own:
a response is one record per content block, each repeating the response's id, stop
reason and usage; a tool call is a block of the response, its result a block of the
user record after it, and nothing closes a turn but the stop reason of the response
that ends it. The adapter declares that nesting (`blocks` and `match`, `turn.end_when`,
`turn.conversation`, and the `per-response` reading with the path to a response's id) and
the core reads it through the turn rule and the usage reading every harness shares: turns
and their classes, tokens counted once per response, writes to product and coordination
files, a person's edits beside them, failed results, and a turn end that bookkeeping
kinds, even ones no release has written yet, do not reopen. No context is read, since
no record carries one, its tokens are not set as a share of an account it does not bill,
and no core module names a field or value the nesting declares.
"""
import ast
import json
import os
import re
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_harness_guard import _docstrings
from tests.test_transcript_shape import ORDINARY, ROOT, _steps

HARNESS = "claude-code"


def _stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def _usage(fresh, created, read, written):
    return {"input_tokens": fresh, "cache_creation_input_tokens": created, "cache_read_input_tokens": read,
            "output_tokens": written, "service_tier": "standard",
            "server_tool_use": {"web_search_requests": 3, "web_fetch_requests": 1},
            "cache_creation": {"ephemeral_5m_input_tokens": created, "ephemeral_1h_input_tokens": 0}}


def _prompt(at, text):
    return {"type": "user", "timestamp": _stamp(at), "message": {"role": "user", "content": text}}


def _response(at, ident, stop, usage, *blocks):
    """One response as the store writes it: a record per content block, each repeating the response's usage."""
    return [{"type": "assistant", "timestamp": _stamp(at),
             "message": {"id": ident, "type": "message", "role": "assistant", "content": [block],
                         "stop_reason": stop, "usage": dict(usage)}} for block in blocks]


def _text(text):
    return {"type": "text", "text": text}


def _call(ident, name, **arguments):
    return {"type": "tool_use", "id": ident, "name": name, "input": arguments}


def _result(at, call, content, **verdict):
    return {"type": "user", "timestamp": _stamp(at),
            "message": {"role": "user", "content": [dict(type="tool_result", tool_use_id=call, content=content,
                                                          **verdict)]}}


def _bookkeeping(at, kind, **fields):
    return dict(type=kind, timestamp=_stamp(at), **fields)


R1, R2, R3 = _usage(10, 2000, 30000, 400), _usage(5, 100, 32000, 200), _usage(5, 50, 32300, 120)
R4, R5 = _usage(3, 500, 33000, 150), _usage(3, 20, 33500, 80)
R6, R7 = _usage(2, 300, 34000, 90), _usage(2, 40, 34400, 60)


def _spent(*usages):
    return sum(usage[key] for usage in usages for key in ("input_tokens", "cache_creation_input_tokens",
                                                          "cache_read_input_tokens", "output_tokens"))


def _store(root, now):
    """Three turns: one writes product and coordination files, one reviews, one reads and meets a failing test.

    The last turn's response makes two calls, and the first call's result is written between its two records.
    """
    return [
        _prompt(now - 560, "write the parser the board names, with its tests"),
        *_response(now - 550, "msg-1", "tool_use", R1, _text("writing the parser first"),
                   _call("call-1", "Write", file_path=os.path.join(root, "src", "mine.py"), content="x = 1\n")),
        _result(now - 545, "call-1", "File created successfully"),
        _bookkeeping(now - 544, "attachment", attachment={"type": "hook_success"}),
        *_response(now - 540, "msg-2", "tool_use", R2,
                   _call("call-2", "Edit", file_path=os.path.join(root, "agent-mail", "note.md"),
                         old_string="a", new_string="b")),
        _result(now - 535, "call-2", "The file has been updated"),
        *_response(now - 530, "msg-3", "end_turn", R3, {"type": "thinking", "thinking": "done"},
                   _text("the parser is written and its tests pass")),
        _bookkeeping(now - 529, "system", subtype="stop_hook_summary"),
        {"type": "last-prompt", "sessionId": "s1"},
        _prompt(now - 420, "review the candidate"),
        *_response(now - 410, "msg-4", "tool_use", R4, _text("asking for the review"),
                   _call("call-4", "Bash", command="ao review --boundary b", description="review")),
        _result(now - 405, "call-4", "review requested", is_error=False),
        *_response(now - 400, "msg-5", "end_turn", R5, _text("the review is requested")),
        _prompt(now - 300, "why does the parser test fail"),
        *_response(now - 290, "msg-6", "tool_use", R6,
                   _call("call-6", "Read", file_path=os.path.join(root, "README.md"))),
        _bookkeeping(now - 289, "queue-operation", operation="enqueue"),
        _result(now - 288, "call-6", "# readme"),
        *_response(now - 290, "msg-6", "tool_use", R6,
                   _call("call-7", "Bash", command="python3 -m pytest -q tests/test_parser.py")),
        _result(now - 284, "call-7", "collected 3 items\nFAIL tests/test_parser.py::test_empty\nexit code: 1",
                is_error=True),
        *_response(now - 280, "msg-7", "stop_sequence", R7, _text("the empty-input test fails on a missing guard")),
        {"type": "ai-title", "sessionId": "s1"},
    ]


def _world(project, monkeypatch, tmp_path, records=None, adapter=HARNESS):
    root = project["root"]
    cfg = dict(project, implementer={"adapter": adapter, "session": "s1"})
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("".join(json.dumps(record) + "\n"
                                  for record in (records if records is not None else _store(root, time.time()))),
                          encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    return cfg, transcript


def _append(transcript, *records):
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(record) + "\n" for record in records))


def test_turns_and_their_classes_come_from_prompts_responses_and_the_tool_calls_nested_in_them(project, monkeypatch,
                                                                                              tmp_path):
    cfg, _ = _world(project, monkeypatch, tmp_path)

    costs = A.turn_costs(cfg)

    assert A.implementer_adapter(cfg)["id"] == HARNESS and costs["unit"] == "token"
    assert [(turn["cls"], turn["usage"], turn["tool_calls"], turn["product_writes"], turn["coord_writes"])
            for turn in costs["turns"]] == [("product", _spent(R1, R2, R3), 2, 1, 1),
                                            ("ceremony", _spent(R4, R5), 1, 0, 0),
                                            ("analysis", _spent(R6, R7), 2, 0, 0)]
    assert costs["ao_commands"]["review"] == 1 and costs["total"] == _spent(R1, R2, R3, R4, R5, R6, R7)


def test_a_response_written_as_several_records_is_counted_once(project, monkeypatch, tmp_path):
    cfg, transcript = _world(project, monkeypatch, tmp_path)
    recs = A.read_tail(str(transcript))
    per_record = sum(_spent(record["message"]["usage"]) for record in recs if record["type"] == "assistant")

    tel = A.telemetry(recs, A.implementer_adapter(cfg))

    assert per_record > _spent(R1, R2, R3, R4, R5, R6, R7)
    assert (tel["total"], tel["turns"], tel["last"], tel["unit"], tel["ctx"]) == (
        _spent(R1, R2, R3, R4, R5, R6, R7), 3, (_spent(R6, R7), 2), "token", None)
    assert A.telemetry(recs, A.implementer_adapter(cfg))["total"] == A.turn_costs(cfg)["total"]


def test_usage_read_per_response_without_the_path_to_its_id_is_not_read(project, monkeypatch, tmp_path):
    shipped = A.load_adapter(HARNESS)
    assert shipped["billing"]["fallback"]["reading"] == "per-response"
    cost = {key: value for key, value in shipped["telemetry"]["cost"].items() if key != "response"}
    declared = {"id": "nested-fixture", "name": "A harness that nests its records", "verified": "untested",
                "contract": 1, "send": {"argv": ["nested-fixture", "{prompt}"]}, "billing": shipped["billing"],
                "transcript": shipped["transcript"], "telemetry": dict(shipped["telemetry"], cost=cost)}
    os.makedirs(os.path.join(project["root"], ".ao", "adapters"), exist_ok=True)
    with open(os.path.join(project["root"], ".ao", "adapters", "nested-fixture.json"), "w", encoding="utf-8") as fh:
        json.dump(declared, fh)
    cfg, transcript = _world(project, monkeypatch, tmp_path, adapter="nested-fixture")

    costs = A.turn_costs(cfg)

    assert [turn["cls"] for turn in costs["turns"]] == ["product", "ceremony", "analysis"] and costs["total"] == 0
    tel = A.telemetry(A.read_tail(str(transcript)), A.implementer_adapter(cfg))
    assert (tel["total"], tel["turns"], tel["last"]) == (0.0, 0, None)


def test_its_nested_writes_tell_its_own_edits_from_a_persons(project, monkeypatch, tmp_path):
    cfg, _ = _world(project, monkeypatch, tmp_path)
    root = cfg["root"]
    os.makedirs(os.path.join(root, "src"))
    for name in ("mine.py", "theirs.py"):
        with open(os.path.join(root, "src", name), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")

    assert os.path.realpath(os.path.join(root, "src", "mine.py")) in A.implementer_recent_writes(cfg)
    assert A.foreign_edits(root, cfg) == ["src/theirs.py"]


def test_a_failed_result_nested_in_a_user_record_is_read_and_a_passing_one_is_not(project, monkeypatch, tmp_path):
    cfg, transcript = _world(project, monkeypatch, tmp_path)
    recs = A.read_tail(str(transcript))

    assert [text for _, text in A.recent_errors(recs, 3, A.implementer_adapter(cfg))] == [
        "FAIL tests/test_parser.py::test_empty"]


def test_the_turn_ends_at_the_response_that_ends_it_whatever_bookkeeping_follows(project, monkeypatch, tmp_path):
    cfg, transcript = _world(project, monkeypatch, tmp_path)
    now = time.time()

    assert A.turn_ended(cfg) is True
    _append(transcript, _bookkeeping(now, "a-kind-no-release-has-written-yet"))
    assert A.turn_ended(cfg) is True
    _append(transcript, _prompt(now, "now add the empty-input guard"))
    assert A.turn_ended(cfg) is False
    _append(transcript, *_response(now, "msg-8", "tool_use", R1, _text("adding the guard"),
                                   _call("call-8", "Edit", file_path="src/mine.py", old_string="x", new_string="y")),
            _bookkeeping(now, "attachment", attachment={"type": "hook_success"}))
    assert A.turn_ended(cfg) is False
    _append(transcript, _result(now, "call-8", "The file has been updated"))
    assert A.turn_ended(cfg) is False
    _append(transcript, *_response(now, "msg-9", "end_turn", R2, _text("the guard is in")),
            _bookkeeping(now, "system", subtype="stop_hook_summary"), {"type": "last-prompt", "sessionId": "s1"})
    assert A.turn_ended(cfg) is True
    assert len(A.turn_costs(cfg)["turns"]) == 4


def test_its_tokens_are_not_set_as_a_share_of_an_account_it_does_not_bill(project, monkeypatch, tmp_path, capsys):
    cfg, _ = _world(project, monkeypatch, tmp_path)
    monkeypatch.setattr(A, "account_usage", lambda timeout=20: {"used": 5000.0, "limit": 10000.0, "reset_at": None})

    assert cli.cmd_cost(cfg, SimpleNamespace(since=None)) == 0

    out = capsys.readouterr().out
    assert "implementer spend by turn class" in out and "token; whole transcript" in out
    assert "the account: 5,000 of 10,000 used" in out and "ao's own share" not in out


# ── the core names nothing the nesting declares ──

def _nesting_words():
    """Every path step and value a shipped adapter declares in `blocks`, `match`, `turn` and the cost's `response`."""
    words = set()
    for path in sorted((ROOT / "src" / "ao" / "adapters").glob("*.json")):
        adapter = json.loads(path.read_text(encoding="utf-8"))
        transcript = adapter.get("transcript") if isinstance(adapter.get("transcript"), dict) else {}
        telemetry = adapter.get("telemetry") if isinstance(adapter.get("telemetry"), dict) else {}
        turn = transcript.get("turn") if isinstance(transcript.get("turn"), dict) else {}
        ends = turn.get("end_when") if isinstance(turn.get("end_when"), list) else [turn.get("end_when")]
        for when in ends:
            if isinstance(when, dict):
                words |= set(_steps(when.get("type"))) | set(_steps(when.get("field")))
                words |= set(_steps(when.get("values")))
        words |= set(_steps(turn.get("conversation")))
        for declared in (transcript.get("tool_call"), telemetry.get("failure"), telemetry.get("cost")):
            if isinstance(declared, dict):
                words |= set(_steps(declared.get("blocks"))) | set(_steps(declared.get("response")))
                for key, value in (declared.get("match") if isinstance(declared.get("match"), dict) else {}).items():
                    words |= set(_steps(key)) | set(_steps(value))
    return words - ORDINARY - {"id"}


def test_no_core_module_names_a_field_or_value_the_nesting_declares():
    words = _nesting_words()
    assert {"tool_use", "tool_result", "stop_reason", "end_turn", "stop_sequence"} <= words
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
