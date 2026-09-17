"""A harness ao never shipped is read through the transcript shape its adapter declares (#76).

The readers assumed one harness's records - the kind under a payload, usage in turn
summaries, a tool's name in one field, a write tool by its name - with branches for
another harness's. The shape is now declared: `transcript.record`, `messages`, `turn`
and `tool_call`, and the `context`, `cost` and `failure` telemetry. A harness declared
only in a project's adapter layer, whose records look like neither shipped harness's,
has its turns, usage, writes, context, failures and messages read; an adapter that
declares no shape has nothing read from another harness's records; and no core module
names a kind, field or tool of a shipped adapter's shape.
"""
import ast
import json
import os
import pathlib
import re
import time
from datetime import datetime, timezone

from ao import lib as A
from tests.test_harness_guard import _docstrings

ROOT = pathlib.Path(__file__).resolve().parent.parent

FIXTURE = {
    "id": "shape-fixture", "name": "A harness ao never shipped", "verified": "untested", "contract": 1,
    "send": {"argv": ["shape-fixture", "run", "{prompt}"]},
    "transcript": {
        "kind": "jsonl",
        "record": {"time": "at", "kind": "event.name", "text_keys": ["body"]},
        "messages": {"prompt": ["ask"], "reply": ["say"]},
        "turn": {"start": ["round.open"], "end": ["round.close"], "bookkeeping": ["meter", "gauge"]},
        "tool_call": {"type": "invoke", "name": "tool.id", "args": "tool.params", "path_keys": ["target"],
                      "write_tools": ["file.put"]},
    },
    "telemetry": {
        "context": {"from": "transcript", "type": "gauge", "match": {"metric": "window"}, "field": "reading.percent"},
        "cost": {"from": "transcript", "type": "meter", "fields": ["tokens.in", "tokens.out"], "unit": "token"},
        "failure": {"from": "transcript", "type": "outcome", "field": "status", "failed_when": "error",
                    "text": "detail"},
    },
}


def _stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def _event(at, name, **fields):
    return {"at": _stamp(at), "event": dict(name=name, **fields)}


def _world(project, monkeypatch, tmp_path, adapter=FIXTURE, records=None):
    """A project whose implementer runs a harness declared in its own adapter layer, and that harness's transcript."""
    root = project["root"]
    os.makedirs(os.path.join(root, ".ao", "adapters"), exist_ok=True)
    with open(os.path.join(root, ".ao", "adapters", f"{adapter['id']}.json"), "w", encoding="utf-8") as fh:
        json.dump(adapter, fh)
    cfg = dict(project, implementer={"adapter": adapter["id"], "session": "s1"})
    now = time.time()
    if records is None:
        records = [
            _event(now - 600, "round.open"),
            _event(now - 599, "ask", body="add the parser the first slice on the board names, with its tests"),
            _event(now - 590, "invoke", tool={"id": "file.put",
                                              "params": {"target": os.path.join(root, "src", "mine.py"), "data": "x"}}),
            _event(now - 580, "meter", tokens={"in": 1200, "out": 300}),
            _event(now - 579, "gauge", metric="window", reading={"percent": 42.5}),
            _event(now - 578, "round.close"),
            # A shipped harness's turn and usage records mean nothing in this transcript.
            {"timestamp": _stamp(now - 500), "payload": {"type": "turn_start"}},
            {"timestamp": _stamp(now - 499), "payload": {"type": "usage_summary",
                                                         "promptTurnSummaries": [{"unit": "credit", "usage": 99.0}]}},
            _event(now - 400, "round.open"),
            _event(now - 390, "invoke", tool={"id": "shell.run", "params": {"cmd": "ao review --boundary b"}}),
            _event(now - 380, "meter", tokens={"in": 800, "out": 200}),
            _event(now - 379, "round.close"),
            _event(now - 200, "round.open"),
            _event(now - 190, "invoke", tool={"id": "file.get", "params": {"target": os.path.join(root, "README.md")}}),
            _event(now - 180, "outcome", status="error",
                   detail="collected 3 items\nFAIL tests/test_parser.py::test_empty\nexit code: 1"),
            _event(now - 170, "meter", tokens={"in": 400, "out": 100}),
            _event(now - 160, "say", body="the parser is written and one of its tests still fails on empty input"),
            _event(now - 150, "round.close"),
            _event(now - 149, "gauge", metric="window", reading={"percent": 57.0}),
        ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    return cfg, transcript


def test_a_harness_ao_never_shipped_has_its_turns_and_usage_read(project, monkeypatch, tmp_path):
    cfg, _ = _world(project, monkeypatch, tmp_path)

    costs = A.turn_costs(cfg)

    assert A.implementer_adapter(cfg)["id"] == "shape-fixture"
    assert [(turn["cls"], turn["usage"], turn["product_writes"]) for turn in costs["turns"]] == [
        ("product", 1500, 1), ("ceremony", 1000, 0), ("analysis", 500, 0)]
    assert costs["total"] == 3000 and costs["unit"] == "token" and costs["ao_commands"]["review"] == 1


def test_its_context_usage_failures_and_messages_come_from_the_same_declaration(project, monkeypatch, tmp_path):
    cfg, transcript = _world(project, monkeypatch, tmp_path)
    recs = A.read_tail(str(transcript))
    adapter = A.implementer_adapter(cfg)

    tel = A.telemetry(recs, adapter)

    assert (tel["ctx"], tel["total"], tel["turns"], tel["last"], tel["unit"]) == (57.0, 3000, 3, (500, 0), "token")
    assert [text for _, text in A.recent_errors(recs, 3, adapter)] == ["FAIL tests/test_parser.py::test_empty"]
    assert [(role, text.split()[0]) for _, role, text in A.messages(recs, 8, adapter)] == [
        ("user", "add"), ("assistant", "the")]


def test_its_turn_end_and_the_bookkeeping_after_it_are_the_declared_kinds(project, monkeypatch, tmp_path):
    cfg, transcript = _world(project, monkeypatch, tmp_path)

    assert A.turn_ended(cfg) is True
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_event(time.time(), "invoke", tool={"id": "file.get", "params": {}})) + "\n")
    assert A.turn_ended(cfg) is False


def test_its_tool_calls_tell_its_own_writes_from_a_persons(project, monkeypatch, tmp_path):
    cfg, _ = _world(project, monkeypatch, tmp_path)
    root = cfg["root"]
    os.makedirs(os.path.join(root, "src"))
    for name in ("mine.py", "theirs.py"):
        with open(os.path.join(root, "src", name), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")

    assert A.foreign_edits(root, cfg) == ["src/theirs.py"]


def test_the_credit_fallback_reads_a_declared_harness_through_its_shape_and_reading(project, monkeypatch, tmp_path):
    _, transcript = _world(project, monkeypatch, tmp_path)
    adapter = dict(FIXTURE, billing={"fallback": {"transcripts": str(transcript), "reading": "peak-per-turn"}})
    monkeypatch.setattr(A, "package_adapters", lambda: {"shape-fixture": adapter})

    usage = A.credit_usage(adapter_id="shape-fixture")

    assert sum(usage["days"].values()) == 3000 and usage["sessions"][0]["turns"] == 3
    other = dict(adapter, billing={"fallback": {"transcripts": str(transcript), "reading": "every-record"}})
    monkeypatch.setattr(A, "package_adapters", lambda: {"shape-fixture": other})
    assert A.credit_usage(adapter_id="shape-fixture")["days"] == {}


def test_an_adapter_that_declares_no_shape_has_nothing_read_from_another_harness_records(project, monkeypatch,
                                                                                         tmp_path):
    bare = {"id": "bare-fixture", "name": "A harness that declares no transcript shape", "verified": "untested",
            "contract": 1, "send": {"argv": ["bare-fixture", "{prompt}"]}}
    stamp = _stamp(time.time() - 60)
    records = [{"timestamp": stamp, "payload": payload} for payload in (
        {"type": "user", "content": "a prompt long enough to be shown as a message in the panel"},
        {"type": "turn_start"},
        {"type": "tool_call", "toolName": "fs_write", "args": {"path": "/r/src/a.py"}},
        {"type": "tool_result", "success": False, "content": "FAIL tests/test_a.py"},
        {"type": "session_metadata", "key": "contextUsage", "value": {"usagePercentage": 67.5}},
        {"type": "usage_summary", "promptTurnSummaries": [{"unit": "credit", "usage": 5.0}]},
        {"type": "turn_end"})]
    cfg, transcript = _world(project, monkeypatch, tmp_path, adapter=bare, records=records)
    recs = A.read_tail(str(transcript))

    assert A.turn_costs(cfg)["turns"] == [] and A.turn_ended(cfg) is False
    assert A.implementer_recent_writes(cfg) == set()
    assert A.telemetry(recs, bare) == {"ctx": None, "total": 0.0, "turns": 0, "last": None, "unit": "unit"}
    assert A.recent_errors(recs, 3, bare) == [] and A.messages(recs, 8, bare) == []


# ── the core names no shipped harness's shape ──

# Words a shipped shape declares that are plain words, or ao's own field names.
ORDINARY = {"type", "timestamp", "text", "content", "message", "user", "assistant", "args", "path", "key", "value",
            "success", "result", "name", "usage", "tool_call", "input", "refusal"}


def _steps(value):
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str):
            yield from (step.replace("[]", "") for step in item.split(".") if step)


def _shape_words():
    """Every kind, field and tool name a shipped adapter's transcript shape declares, but the ordinary words."""
    words = set()
    for path in sorted((ROOT / "src" / "ao" / "adapters").glob("*.json")):
        adapter = json.loads(path.read_text(encoding="utf-8"))
        transcript = adapter.get("transcript") if isinstance(adapter.get("transcript"), dict) else {}
        record = transcript.get("record") if isinstance(transcript.get("record"), dict) else {}
        words |= set(_steps(record.get("kind"))) | set(_steps(record.get("time"))) | set(_steps(transcript.get("kinds")))
        for block in ("messages", "turn"):
            for kinds in (transcript.get(block) or {}).values():
                words |= set(_steps(kinds))
        tool = transcript.get("tool_call") or {}
        for key in ("type", "name", "args", "path_keys", "write_tools"):
            words |= set(_steps(tool.get(key)))
        for spec in (adapter.get("telemetry") or {}).values():
            if isinstance(spec, dict) and spec.get("from") == "transcript":
                for key in ("type", "field", "fields", "tools", "text"):
                    words |= set(_steps(spec.get(key)))
                for key, value in (spec.get("match") or {}).items():
                    words |= set(_steps(key)) | set(_steps(value))
    return words - ORDINARY


def test_no_core_module_names_a_kind_field_or_tool_of_a_shipped_transcript_shape():
    words = _shape_words()
    assert {"turn_start", "usage_summary", "promptTurnSummaries", "toolName", "fs_write", "payload"} <= words
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
