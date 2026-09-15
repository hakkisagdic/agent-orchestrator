import json
import os
import time
from types import SimpleNamespace

from ao import watchdog as W


def test_a_real_cycle_records_when_it_started_and_how_long_it_took(project):
    ns = SimpleNamespace(root=project["root"], idle_minutes=6.0, dry_run=False, prompt=W.NUDGE_PROMPT)
    before = int(time.time())

    W.run(ns)

    row = W.cycles(project["root"])[-1]
    assert before <= row["started"] <= row["at"] and 0 <= row["seconds"] < 120


def test_cycle_health_tells_a_long_cycle_from_a_long_silence(project):
    path = W.cycles_path(project["root"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    base = 1_789_470_000
    rows = [{"at": base, "verdict": "written before started existed"},
            {"at": base + 120, "started": base + 116, "seconds": 4, "verdict": "b"},
            {"at": base + 1500, "started": base + 300, "seconds": 1200, "verdict": "ran twenty minutes"},
            {"at": base + 1620, "started": base + 1615, "seconds": 5, "verdict": "d"}]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(row) + "\n" for row in rows))

    health = W.cycle_health(project["root"])

    def when(t):
        return time.strftime("%d %b %H:%M", time.localtime(t))

    assert (health["count"], health["longest_seconds"], health["longest_at"]) == (4, 1200, when(base + 1500))
    assert round(health["gap_minutes"]) == 3 and health["gap_at"] == when(base + 1500)
