"""Turns, writes and credits are counted the way the transcripts are written.

Each reading below was checked against a local store of the reference harness before it
changed, and each was wrong against it:

- the harness writes a prompt and then its turn's start marker, and both opened a turn, so
  `ao cost` and `ao cost --features` counted every turn twice while spend stayed put;
- the credit estimate took a usage record for the running total of the turn in progress and
  a drop between two records for a new turn, while `ao cost` and the panel added records up:
  every record is a whole turn's cost, and the peaks merged each run of rising turns into its
  last. The reading is the adapter's to declare, and every reader applies the declared one;
- tools that replace text in, append to, delete or move a file were not writes;
- a transcript whose last turn end is followed by a session or tombstone record read as a
  turn still running, so the watchdog waited three idle windows instead of one.
"""
import json
import os
import time
from datetime import datetime, timezone

import pytest

from ao import lib as A
from tests.test_transcript_shape import FIXTURE, _event, _world

MONTH_END = datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc).timestamp()


def _record(at, kind, **payload):
    stamp = datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")
    return {"timestamp": stamp, "payload": dict(type=kind, **payload)}


def _turn(records, at, usage, *calls, prompt=True, before_start=(), request_ids=True):
    """One turn as the reference harness writes it: prompt, start, tool calls, the usage record, its end.

    `usage` None writes no usage record (an interrupted turn), [] an empty summary (a failed
    one). `request_ids` False writes the older record shape, with neither request ids nor the
    tools the turn used.
    """
    if prompt:
        records.append(_record(at, "user", content="take the next slice on the board, with its tests"))
    records.extend(before_start)
    records.append(_record(at, "turn_start", executionId=f"x{int(at)}"))
    for name, args in calls:
        records.append(_record(at + 30, "tool_call", toolName=name, args=args))
    if usage is not None:
        summaries = [] if usage == [] else [{"unit": "credit", "unitPlural": "credits", "usage": usage}]
        payload = {"executionId": f"x{int(at)}", "status": "success" if summaries else "failed",
                   "elapsedTime": 60_000, "promptTurnSummaries": summaries}
        if request_ids and summaries:
            summaries[0]["usedTools"] = [name for name, _ in calls]
            payload["requestIds"] = [f"q{int(at)}-{n}" for n in range(3)]
        records.append(_record(at + 60, "usage_summary", **payload))
    records.append(_record(at + 60, "session_event"))
    records.append(_record(at + 60, "turn_end", executionId=f"x{int(at)}", stopReason="end_turn"))


def _transcript(path, monkeypatch, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(record) + "\n" for record in records))
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(path), None))
    return str(path)


# ── a prompt and its start marker are one turn ──

def test_a_prompt_and_the_start_marker_after_it_are_one_turn_and_no_spend_moves(project, tmp_path, monkeypatch):
    root = project["root"]
    shapes = {}
    for prompted in (False, True):
        records = []
        _turn(records, MONTH_END, 5.0, ("fs_write", {"path": f"{root}/src/a.py", "text": "x"}), prompt=prompted)
        # A call the harness makes of its own between the prompt and the start does not split the turn.
        _turn(records, MONTH_END + 600, 3.0, ("execute_bash", {"command": "ao review --boundary b"}), prompt=prompted,
              before_start=[_record(MONTH_END + 600, "tool_call", toolName="settings_fetch", args={})] if prompted
              else ())
        _turn(records, MONTH_END + 1200, 1.0, ("read_file", {"path": f"{root}/src/a.py"}), prompt=prompted)
        _transcript(tmp_path / f"prompted-{prompted}.jsonl", monkeypatch, records)
        shapes[prompted] = (A.turn_costs(project), A.feature_costs(project))

    (marked, marked_features), (prompted, prompted_features) = shapes[False], shapes[True]
    assert len(prompted["turns"]) == len(marked["turns"]) == 3
    assert prompted["by_class"] == marked["by_class"] and prompted["total"] == marked["total"] == 9.0
    assert [turn["cls"] for turn in prompted["turns"]] == ["product", "ceremony", "analysis"]
    assert prompted_features["turns"] == marked_features["turns"] == 3


def test_a_start_marker_still_opens_a_turn_once_the_open_one_has_started(project, tmp_path, monkeypatch):
    records = []
    _turn(records, MONTH_END, 2.0, ("read_file", {"path": "/r/src/a.py"}))
    # A prompt typed while the turn still runs belongs to the next turn, which its start opens.
    records.insert(3, _record(MONTH_END + 40, "user", content="and after that, the one below it on the board"))
    _turn(records, MONTH_END + 600, 4.0, ("read_file", {"path": "/r/src/b.py"}), prompt=False)
    _transcript(tmp_path / "t.jsonl", monkeypatch, records)

    assert [turn["usage"] for turn in A.turn_costs(project)["turns"]] == [2.0, 4.0]


# ── usage adds up under the declared reading, for every reader alike ──

def test_every_usage_record_is_a_whole_turn_and_the_credit_estimate_ao_cost_and_the_panel_agree(project, tmp_path,
                                                                                                  monkeypatch):
    records = []
    _turn(records, MONTH_END, [], request_ids=False)                               # failed: an empty summary
    _turn(records, MONTH_END + 3600, 2.0, ("read_file", {"path": "/r/src/a.py"}), request_ids=False)
    _turn(records, MONTH_END + 7200, 5.0, ("read_file", {"path": "/r/src/a.py"}), request_ids=False)
    _turn(records, MONTH_END + 12600, 9.0, ("read_file", {"path": "/r/src/b.py"}), ("grep_search", {"query": "x"}))
    _turn(records, MONTH_END + 14400, 4.0, ("read_file", {"path": "/r/src/c.py"}))
    _turn(records, MONTH_END + 16200, None, ("read_file", {"path": "/r/src/d.py"}))  # interrupted: no usage record
    store = A.load_adapter("kiro")["billing"]["fallback"]["transcripts"]
    path = _transcript(A._home_path(store.replace("*", "s1")), monkeypatch, records)

    estimate, costs = A.credit_usage(), A.turn_costs(project)
    panel = A.telemetry(A.read_tail(path), A.load_adapter("kiro"))

    # Read as running totals, a drop taken for a new turn, the same records came to 13, all of it in September.
    assert sum(estimate["days"].values()) == costs["total"] == panel["total"] == 20.0
    assert estimate["days"] == {"2026-08-31": 7.0, "2026-09-01": 13.0}
    assert estimate["months"] == {"2026-08": 7.0, "2026-09": 13.0}
    assert [turn["usage"] for turn in costs["turns"]] == [0.0, 2.0, 5.0, 9.0, 4.0, 0.0]
    assert estimate["sessions"][0]["turns"] == 5 and (panel["turns"], panel["last"]) == (4, (4.0, 1))


def _rounds(meters):
    """A never-shipped harness's rounds, each prompted before it opens, with the usage records it writes."""
    now, records = time.time() - 3600, []
    for n, values in enumerate(meters):
        at = now + 60 * n
        records.append(_event(at, "ask", body="the next slice on the board and the tests that prove it"))
        records.append(_event(at + 1, "round.open"))
        records += [_event(at + 2 + i, "meter", tokens={"in": value, "out": 0}) for i, value in enumerate(values)]
        records.append(_event(at + 20, "round.close"))
    return records


@pytest.mark.parametrize("reading, meters, spent", [
    ("peak-per-turn", [[100, 250, 400], [50, 300], [80]], 780),   # each record the running total of its round
    ("sum", [[400], [300], [80]], 780),                           # each record the whole cost of its round
])
def test_the_declared_reading_adds_usage_up_for_ao_cost_the_panel_and_the_credit_estimate(project, monkeypatch,
                                                                                          tmp_path, reading, meters,
                                                                                          spent):
    adapter = dict(FIXTURE, billing={"fallback": {"transcripts": str(tmp_path / "transcript.jsonl"),
                                                  "reading": reading}})
    cfg, transcript = _world(project, monkeypatch, tmp_path, adapter=adapter, records=_rounds(meters))
    monkeypatch.setattr(A, "package_adapters", lambda: {adapter["id"]: adapter})

    costs = A.turn_costs(cfg)
    panel = A.telemetry(A.read_tail(str(transcript)), adapter)
    estimate = A.credit_usage()

    assert costs["total"] == panel["total"] == sum(estimate["days"].values()) == spent
    assert len(costs["turns"]) == panel["turns"] == estimate["sessions"][0]["turns"] == 3
    assert panel["last"] == (80, 0)


def test_the_estimate_needs_a_reading_ao_implements_and_nothing_reads_usage_under_an_unknown_one(project, monkeypatch,
                                                                                                 tmp_path):
    # Undeclared, usage adds up for `ao cost` and the panel as it always has, and the estimate
    # still reads nothing; declared but unknown, no reader reads usage rather than misread it.
    for fallback, spent in (({}, 480), ({"reading": "every-record"}, 0)):
        adapter = dict(FIXTURE, billing={"fallback": dict(fallback, transcripts=str(tmp_path / "transcript.jsonl"))})
        cfg, transcript = _world(project, monkeypatch, tmp_path, adapter=adapter, records=_rounds([[400], [80]]))
        monkeypatch.setattr(A, "package_adapters", lambda: {adapter["id"]: adapter})

        assert A.credit_usage()["days"] == {}
        assert A.turn_costs(cfg)["total"] == A.telemetry(A.read_tail(str(transcript)), adapter)["total"] == spent


# ── writes ──

def test_a_tool_that_replaces_appends_deletes_or_moves_a_file_is_a_write(project, tmp_path, monkeypatch):
    root = project["root"]
    now, records = time.time() - 600, []
    _turn(records, now, 4.0, ("str_replace", {"path": f"{root}/src/a.py", "oldStr": "a", "newStr": "b",
                                              "replace_all": False}))
    _turn(records, now + 60, 1.0, ("fs_append", {"path": f"{root}/docs/notes.md", "text": "one more line"}))
    _turn(records, now + 120, 1.0, ("delete_file", {"targetFile": f"{root}/src/old.py", "explanation": "unused"}))
    _turn(records, now + 180, 1.0, ("smart_relocate", {"sourcePath": f"{root}/src/b.py",
                                                       "destinationPath": f"{root}/lib/b.py"}))
    _turn(records, now + 240, 1.0, ("semantic_rename", {"path": f"{root}/src/c.py", "line": 3, "character": 4,
                                                        "oldName": "parse", "newName": "read"}))
    _turn(records, now + 300, 1.0, ("str_replace", {"path": f"{root}/agent-mail/reply.md", "oldStr": "a",
                                                    "newStr": "b", "replace_all": False}))
    _transcript(tmp_path / "t.jsonl", monkeypatch, records)

    costs = A.turn_costs(project)

    assert [(turn["cls"], turn["product_writes"], turn["coord_writes"]) for turn in costs["turns"]] == \
        [("product", 1, 0)] * 5 + [("coordination", 0, 1)]
    assert {os.path.realpath(os.path.join(root, *name.split("/"))) for name in ("src/old.py", "lib/b.py")} \
        <= A.implementer_recent_writes(project)


# ── the end of a turn ──

def test_a_turn_end_followed_by_a_session_or_tombstone_record_has_ended(project, tmp_path, monkeypatch):
    path = tmp_path / "t.jsonl"
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(path), None))

    def ended(*kinds):
        path.write_text("".join(json.dumps({"payload": {"type": kind}}) + "\n" for kind in kinds), encoding="utf-8")
        return A.turn_ended(project)

    turn = ("user", "turn_start", "tool_call", "tool_result", "usage_summary", "session_event", "turn_end")
    assert ended(*turn, "session_start") is True
    assert ended(*turn, "tombstone") is True
    assert ended(*turn, "session_start", "tombstone") is True
    assert ended("user", "turn_start", "tool_call", "tombstone") is False
    assert ended(*turn, "tool_call", "tool_result") is False
