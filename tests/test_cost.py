import json
import os

from ao import lib as A


def _rec(t, **payload):
    return json.dumps({"timestamp": "2026-09-05T10:00:00Z", "payload": dict(type=t, **payload)})


def test_turn_costs_classifies_and_sums(project, tmp_path, monkeypatch):
    tr = tmp_path / "t.jsonl"
    lines = [
        _rec("turn_start"), _rec("tool_call", toolName="fs_write", args={"path": "/r/src/a.ts"}),
        _rec("usage_summary", promptTurnSummaries=[{"unit": "credit", "usage": 5.0}]), _rec("turn_end"),
        _rec("turn_start"), _rec("tool_call", toolName="execute_bash", args={"command": "ao review --boundary x"}),
        _rec("usage_summary", promptTurnSummaries=[{"unit": "credit", "usage": 3.0}]), _rec("turn_end"),
        _rec("turn_start"), _rec("tool_call", toolName="ao_report", args={"kind": "blocked", "summary": "q"}),
        _rec("usage_summary", promptTurnSummaries=[{"unit": "credit", "usage": 2.0}]), _rec("turn_end"),
        _rec("turn_start"), _rec("tool_call", toolName="fs_read", args={"path": "/r/src/a.ts"}),
        _rec("usage_summary", promptTurnSummaries=[{"unit": "credit", "usage": 1.0}]), _rec("turn_end"),
    ]
    tr.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(tr), None))
    c = A.turn_costs(project)
    by = c["by_class"]
    assert by["product"]["usage"] == 5 and by["ceremony"]["usage"] == 3
    assert by["coordination"]["usage"] == 2 and by["coordination"]["wasted"] == 1
    assert by["analysis"]["usage"] == 1 and c["total"] == 11
    assert c["ao_commands"]["review"] == 1
