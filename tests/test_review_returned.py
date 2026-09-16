import re
import time
from types import SimpleNamespace

from ao import cli, lib as A


def _returned(root, rid, minutes_ago, **fields):
    state = dict({"id": rid, "state": "finished", "verdict": "APPROVED", "slice": "S1", "tree": "t" * 40,
                  "submitted_at": int(time.time()) - 3600, "finished_at": int(time.time() - minutes_ago * 60)},
                 **fields)
    cli._write_review_state(root, state)


def test_ao_status_names_a_returned_review_until_it_is_collected(project, capsys):
    root = project["root"]
    _returned(root, "R-1", 5)

    cli.cmd_status(project, SimpleNamespace(messages=4, window=24.0))
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert "REVIEW RETURNED for S1: R-1 APPROVED — handle it before new work" in out

    cli.cmd_review(project, SimpleNamespace(action="collect", rid="R-1", any=False, run=None))
    capsys.readouterr()
    cli.cmd_status(project, SimpleNamespace(messages=4, window=24.0))
    assert "REVIEW RETURNED" not in capsys.readouterr().out


def test_the_watchdog_raises_a_returned_review_left_unattended(project):
    root = project["root"]
    _returned(root, "R-2", 45, verdict="NEEDS_CHANGES")
    _returned(root, "R-3", 5)
    _returned(root, "R-4", 90, state="running", verdict=None)

    found = [a for a in A.anomalies(root, project, {}, 900, 360) if a["kind"] == "review-returned"]

    assert [a["key"] for a in found] == ["R-2"] and "NEEDS_CHANGES 45m ago" in found[0]["facts"][0]
