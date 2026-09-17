import json
import os
import stat
import subprocess
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage
from tests.test_switches_and_bypass import _allow_candidate_verification

WAIVE = dict(gate="review", slice="B7", why="reviewer at quota", by="alice (owner)", hours=24.0)
# A catch-up that reaches a review names the family that wrote the range (#65).
NAMED = SimpleNamespace(boundary=None, author_family="author-family", by="A. Person")


def _running(root, *slices):
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    items = "".join(f"- [{s}] slice {s} · since: 2026-09-05 10:00\n" for s in slices)
    open(board, "w", encoding="utf-8").write(text.replace("## running\n", "## running\n" + items))


def _stage(root, name, text):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", name), "w", encoding="utf-8").write(text)
    subprocess.run(["git", "add", f"src/{name}"], cwd=root, check=True, capture_output=True)
    return A.index_candidate(root)


def _commit(root, message):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message],
                   cwd=root, check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit_ok(project, capsys):
    capsys.readouterr()
    code = cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None))
    return code, capsys.readouterr().out


@pytest.mark.parametrize("change,message", [
    ({"slice": None}, "--slice is required"),
    ({"slice": "*"}, "--slice is required"),
    ({"by": None}, "--by is required"),
    ({"by": "kiro"}, "names an agent or a role"),
    ({"by": "human"}, "names an agent or a role"),
    ({"hours": 0.0}, "--hours must be above 0"),
    ({"hours": 169.0}, "--hours must be above 0"),
])
def test_ao_waive_refuses_a_waiver_without_bounds(project, capsys, change, message):
    assert cli.cmd_waive(project, SimpleNamespace(**{**WAIVE, **change})) == 2
    assert message in capsys.readouterr().out
    assert not os.path.exists(A.waivers_path(project["root"]))


def test_a_waiver_is_a_chained_row_with_an_expiry_and_who_ran_it(project):
    root = project["root"]
    assert cli.cmd_waive(project, SimpleNamespace(**WAIVE)) == 0

    (row,) = storage.read_chained_jsonl(A.waivers_path(root), A.WAIVER_CHAIN)
    assert row["slice"] == "B7" and row["by"] == "alice (owner)"
    assert 0 < row["expires"] - row["at"] <= 24 * 3600
    assert "user" in row and isinstance(row["interactive"], bool)
    assert A.open_waiver_report(root)[0].startswith(f"{row['id']} review for B7")


def test_an_expired_waiver_authorises_nothing(project, monkeypatch, capsys):
    root = project["root"]
    _running(root, "B7")
    _allow_candidate_verification(monkeypatch, _stage(root, "a.py", "value = 1\n"))
    A.waive(root, "review", "B7", "quota", by="alice (owner)", hours=0.0002)
    time.sleep(1)

    code, out = _commit_ok(project, capsys)
    assert code == 1 and "expired" in out


def test_a_waiver_cannot_be_replayed_for_other_bytes(project, monkeypatch, capsys):
    root = project["root"]
    _running(root, "B7")
    first = _stage(root, "a.py", "value = 1\n")
    _allow_candidate_verification(monkeypatch, first)
    waiver = A.waive(root, "review", "B7", "quota", by="alice (owner)")

    code, out = _commit_ok(project, capsys)
    assert code == 0 and "review waived" in out
    assert A.latest_authority_decision(root)["waiver"] == waiver["id"]
    assert cli.cmd_commit_check(project, SimpleNamespace()) == 0

    second = _stage(root, "b.py", "other = 2\n")
    _allow_candidate_verification(monkeypatch, second)
    code, out = _commit_ok(project, capsys)
    assert code == 1 and f"waiver {waiver['id']} already authorised other bytes" in out


def test_a_waiver_for_every_slice_or_without_an_expiry_covers_nothing(project, monkeypatch, capsys):
    root = project["root"]
    _running(root, "B7")
    _allow_candidate_verification(monkeypatch, _stage(root, "a.py", "value = 1\n"))
    with open(A.waivers_path(root), "w", encoding="utf-8") as fh:
        for wid, slice_id in (("W-every", "*"), ("W-legacy", "B7")):
            fh.write(json.dumps({"event": "waived", "id": wid, "gate": "review", "slice": slice_id,
                                 "why": "old", "by": "human", "at": 1, "head": "x", "tree": "t"}) + "\n")

    code, out = _commit_ok(project, capsys)
    assert code == 1
    assert "W-every names every slice" in out and "W-legacy for B7 predates waiver expiry" in out


def test_a_row_appended_without_a_link_makes_waivers_unreadable(project, monkeypatch, capsys):
    root = project["root"]
    _running(root, "B7")
    _allow_candidate_verification(monkeypatch, _stage(root, "a.py", "value = 1\n"))
    A.waive(root, "review", "B7", "quota", by="alice (owner)")
    with open(A.waivers_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "closed", "id": "W-other", "at": 1, "outcome": "forged"}) + "\n")

    with pytest.raises(storage.LedgerCorruption):
        A.open_waivers(root)
    code, out = _commit_ok(project, capsys)
    assert code == 1 and "waiver ledger is unreadable" in out


def _landed_under_a_waiver(project, monkeypatch, capsys):
    root = project["root"]
    _stage(root, "a.py", "value = 1\n")
    _commit(root, "base")
    _running(root, "B7")
    _allow_candidate_verification(monkeypatch, _stage(root, "a.py", "value = 2\n"))
    waiver = A.waive(root, "review", "B7", "quota", by="alice (owner)")
    assert _commit_ok(project, capsys)[0] == 0
    landed = _commit(root, "b7")
    parent = subprocess.run(["git", "rev-parse", f"{landed}^"], cwd=root, check=True,
                            capture_output=True, text=True).stdout.strip()
    _stage(root, "c.py", "unrelated = 3\n")
    _commit(root, "later work, not under the waiver")
    return waiver, parent, landed


def test_catchup_reviews_exactly_the_commit_granted_under_a_bounded_waiver(project, monkeypatch, capsys):
    waiver, parent, landed = _landed_under_a_waiver(project, monkeypatch, capsys)
    seen = []
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns: seen.append(ns.commits) or 3)
    from ao import watchdog as W
    monkeypatch.setattr(W, "run", lambda ns: 0)

    assert cli.cmd_catchup(project, NAMED) == 3
    assert seen == [f"{parent}..{landed}"]
    assert [w["id"] for w in A.open_waivers(project["root"])] == [waiver["id"]]


def test_catchup_does_not_close_a_waiver_when_no_review_was_recorded(project, monkeypatch, capsys):
    waiver, _, _ = _landed_under_a_waiver(project, monkeypatch, capsys)
    from ao import watchdog as W
    monkeypatch.setattr(W, "run", lambda ns: 0)
    mailed = []
    monkeypatch.setattr(A, "write_mail", lambda *args, **kwargs: mailed.append(args))

    for code in (1, 2):
        monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns, code=code: code)
        assert cli.cmd_catchup(project, NAMED) == 3
        assert [w["id"] for w in A.open_waivers(project["root"])] == [waiver["id"]]
    assert mailed == []


def test_a_close_that_cannot_be_written_fails_catchup(project, monkeypatch, capsys):
    root = project["root"]
    _stage(root, "a.py", "value = 1\n")
    _commit(root, "base")
    empty = A.waive(root, "review", "B7", "quota", by="alice (owner)", hours=0.0002)
    time.sleep(1)
    from ao import watchdog as W
    monkeypatch.setattr(W, "run", lambda ns: 0)
    path = os.path.abspath(A.waivers_path(root))
    append = storage.append_chained_jsonl

    def refused(target, *args, **kwargs):
        # A file mode does not stop root, so the refusal is the write's own (the Linux lane runs as root).
        if os.path.abspath(target) == path:
            raise PermissionError(13, "Permission denied", target)
        return append(target, *args, **kwargs)

    monkeypatch.setattr(storage, "append_chained_jsonl", refused)
    assert cli.cmd_catchup(project, SimpleNamespace(boundary=None)) == 1
    assert "could not close" in capsys.readouterr().out
    assert [w["id"] for w in A.open_waivers(root)] == [empty["id"]]
