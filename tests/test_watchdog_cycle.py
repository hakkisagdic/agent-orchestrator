import os
from types import SimpleNamespace

from ao import lib as A
from ao import watchdog as W


def test_dry_cycle_is_traced_and_not_recorded(project):
    root = project["root"]
    ns = SimpleNamespace(root=root, idle_minutes=6.0, dry_run=True, prompt=W.NUDGE_PROMPT)
    assert W.run(ns) == 0
    assert W._TRACE and "nothing to watch" in W._TRACE[-1]
    assert not os.path.exists(W.cycles_path(root))


def test_real_cycle_is_recorded(project):
    root = project["root"]
    ns = SimpleNamespace(root=root, idle_minutes=6.0, dry_run=False, prompt=W.NUDGE_PROMPT)
    W.run(ns)
    rows = W.cycles(root)
    assert rows and rows[-1]["verdict"] == W._TRACE[-1] and "trace" in rows[-1]


def test_expired_alarm_is_returned_once(project):
    now = 1_000_000
    A.alarm_touch("proj", "x", "orange", now=now)
    assert A.expire_alarms("proj", now=now + 60) == []
    done = A.expire_alarms("proj", now=now + 3 * 3600)
    assert [e["key"] for e in done] == ["x"]
    assert A.expire_alarms("proj", now=now + 3 * 3600) == []


def test_stale_sibling_heartbeats(project, tmp_path):
    d = os.path.join(A.HOME, ".ao")
    os.makedirs(d, exist_ok=True)
    for name, age in (("heartbeat-proj", 10), ("heartbeat-other", 2000), ("heartbeat-fresh", 30)):
        p = os.path.join(d, name)
        open(p, "w").write("x")
        os.utime(p, (A.time.time() - age, A.time.time() - age))
    assert A.stale_siblings(project["root"]) == {"other": 2000} or list(A.stale_siblings(project["root"])) == ["other"]


def test_storm_cap(project):
    root = project["root"]
    for i in range(12):
        A.record_notice(root, f"t{i}", "m", sent=True, key=f"k{i}")
    assert W.storm(root) is True
    assert W.storm(root, limit=13) is False


def test_turn_ended_reads_the_transcripts_own_word(project, tmp_path, monkeypatch):
    import json
    tr = tmp_path / "t.jsonl"
    rows = [{"payload": {"type": "turn_start"}}, {"payload": {"type": "tool_call"}},
            {"payload": {"type": "turn_end"}}, {"payload": {"type": "session_metadata"}}]
    tr.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(tr), None))
    assert A.turn_ended(project) is True
    tr.write_text("\n".join(json.dumps(r) for r in rows[:2]) + "\n")
    assert A.turn_ended(project) is False
