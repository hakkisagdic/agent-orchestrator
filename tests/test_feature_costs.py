import json
import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from ao import cli, lib as A, watchdog as W

NUDGED_AT = "2026-09-16 12:00:00"


def _stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _turn(records, start, usage, *calls):
    records.append({"timestamp": _stamp(start), "payload": {"type": "turn_start"}})
    for name, args in calls:
        records.append({"timestamp": _stamp(start + 1), "payload": {"type": "tool_call", "toolName": name, "args": args}})
    records.append({"payload": {"type": "usage_summary", "promptTurnSummaries": [{"usage": usage, "unit": "credit"}]}})
    records.append({"payload": {"type": "turn_end"}})


def test_hunts_are_counted_when_the_transcript_has_no_turns(project, monkeypatch, capsys):
    """BUG-HUNTER-2: `ao cost --features` returned before what it counts apart when the transcript had no turns."""
    monkeypatch.setattr(A, "session_paths", lambda cfg: (None, None))
    A.hunter_record(project["root"], "run", files=[], cursor=0, ok=True, leads=0, sent=0, hunter="h1")

    assert cli.cmd_cost(project, SimpleNamespace(since=None, features=True)) == 0

    out = capsys.readouterr().out
    assert "no transcript turns in this window" in out and A.re.search(r"hunter\s+1 started", out)
    assert "its own command" in out


def test_each_feature_is_priced_from_what_the_transcript_and_the_watchdog_recorded(project, tmp_path, monkeypatch,
                                                                                  capsys):
    nudged = time.mktime(time.strptime(NUDGED_AT, "%Y-%m-%d %H:%M:%S"))
    records = []
    _turn(records, nudged - 3600, 3.0, ("shell", {"command": "ao review --boundary b"}))
    _turn(records, nudged - 1800, 1.0, ("shell", {"command": "ao board"}))
    _turn(records, nudged + 30, 2.0, ("fs_read", {"path": "src/a.py"}))
    _turn(records, nudged + 600, 10.0, ("fs_write", {"path": "src/a.py", "text": "x = 1"}))
    _turn(records, nudged + 7200, 4.0, ("fs_read", {"path": "src/b.py"}))
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    os.makedirs(W.STATE_DIR, exist_ok=True)
    key = A.project_key(project["root"])
    with open(os.path.join(W.STATE_DIR, f"nudge-{key}.log"), "w", encoding="utf-8") as fh:
        fh.write(f"\n=== {NUDGED_AT} nudge ===\nstarted\n")
    with open(os.path.join(W.STATE_DIR, f"escalate-{key}.log"), "w", encoding="utf-8") as fh:
        fh.write("\n=== 2026-09-16 12:05:00 escalate /agents/claude 2.1 ===\n")

    measured = A.feature_costs(project)

    assert measured["total"] == 20.0 and measured["turns"] == 5
    assert measured["features"] == {"review": {"turns": 1, "usage": 3.0}, "reports": {"turns": 1, "usage": 1.0},
                                    "nudge": {"turns": 1, "usage": 2.0}}
    assert measured["counted"] == {"architect_wake": 1, "refill": 0, "hunter": 0}
    assert cli.cmd_cost(project, SimpleNamespace(since=None, features=True)) == 0
    out = capsys.readouterr().out
    assert "review" in out and "15.0%" in out and "the architect's pool" in out
