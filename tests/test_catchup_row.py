"""A concurrent review of the same range and author supplied catch-up's verdict.

Catch-up now uses the row its own call recorded, and a call that records nothing
leaves the changed range's waiver open (CATCHUP-ROW).
Removing the ledger pre-check spent a review on an unreadable ledger, or closed
an unchanged range; recording failures must leave the waiver open and let the
run continue (CATCHUP-ROW).
"""
import os

import pytest

from ao import cli, lib as A, storage, watchdog as W
from tests.standin import program
from tests.test_catchup_ready import APPROVED, NEEDS_CHANGES, PERSON, _catchup, _land, _legacy_waiver


@pytest.mark.parametrize("own, other", [("APPROVED", "NEEDS_CHANGES"), ("NEEDS_CHANGES", "APPROVED")])
def test_catchup_closes_on_its_own_verdict_when_another_review_records_the_same_range_and_author(
        project, monkeypatch, tmp_path, own, other):
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    waiver = _legacy_waiver(root, "B7")
    _land(root, "a.py", "value = 2\n", "b7")
    monkeypatch.setattr(W, "run", lambda ns: 0)
    write = A.write_review_artefact
    recorded = []

    def concurrent(*args, **kwargs):
        row = write(*args, **kwargs)
        recorded.append(row)
        A.record_review(root, "other.md", f"VERDICT: {other}\n".encode("utf-8"), kwargs["evidence"], other)
        return row

    monkeypatch.setattr(A, "write_review_artefact", concurrent)
    lines = APPROVED if own == "APPROVED" else NEEDS_CHANGES
    reviewer = program(tmp_path, "reviewer", "\n".join(f"print({line!r})" for line in lines))
    cfg = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": [reviewer, "{prompt}"]})

    assert _catchup(cfg, **PERSON) == 0

    (row,) = recorded
    assert A.range_review(root, row["commits"], author=row["author"])["verdict"] == other
    assert A.open_waivers(root) == []
    (closed,) = [entry for entry in A.waiver_rows(root) if entry.get("event") == "closed"]
    assert closed["id"] == waiver["id"]
    assert closed["outcome"] == ("reviewed: " + own
                                  + (" — fix slice needed" if own == "NEEDS_CHANGES" else ""))
    mail = os.path.join(root, "agent-mail")
    if own == "NEEDS_CHANGES":
        (name,) = os.listdir(mail)
        with open(os.path.join(mail, name), encoding="utf-8") as fh:
            body = fh.read()
        assert row["artefact"] in body and "other.md" not in body
    else:
        assert os.listdir(mail) == []


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_catchup_keeps_the_waiver_open_when_only_another_call_recorded_a_review(
        project, monkeypatch, capsys, code):
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    waiver = _legacy_waiver(root, "B7")
    _land(root, "a.py", "value = 2\n", "b7")
    monkeypatch.setattr(W, "run", lambda ns: 0)

    def concurrent(cfg, ns):
        A.record_review(root, "other.md", b"VERDICT: APPROVED\n",
                        {"kind": "commit-range", "commits": ns.commits, "author": ns.author}, "APPROVED")
        return code

    monkeypatch.setattr(cli, "cmd_review", concurrent)

    assert _catchup(project, **PERSON) == (1 if code == 0 else 3)
    assert [row["id"] for row in A.open_waivers(root)] == [waiver["id"]]
    assert f"{waiver['id']} stays open" in capsys.readouterr().out
    assert os.listdir(os.path.join(root, "agent-mail")) == []


@pytest.mark.parametrize("failure", ["corrupt", "unreadable"])
@pytest.mark.parametrize("unchanged", [False, True], ids=["changed", "no-net-change"])
def test_catchup_reads_the_ledger_before_starting_a_review_or_closing_an_unchanged_range(
        project, monkeypatch, capsys, tmp_path, failure, unchanged):
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    waivers = []
    for value, slice_id in enumerate(("B7", "B8"), 2):
        waivers.append(_legacy_waiver(root, slice_id))
        _land(root, "a.py", f"value = {value}\n", slice_id)
        if unchanged:
            _land(root, "a.py", "value = 1\n", "restore the original value")
    ledger = A.review_ledger_path(root)
    if failure == "corrupt":
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write('{"prev": "broken"}\n')
        with pytest.raises(storage.LedgerCorruption):
            A.review_row_count(root)
    else:
        read = storage.read_chained_jsonl

        def unreadable(path, *args, **kwargs):
            if os.path.abspath(path) == os.path.abspath(ledger):
                raise OSError("review ledger cannot be opened")
            return read(path, *args, **kwargs)

        monkeypatch.setattr(storage, "read_chained_jsonl", unreadable)
    monkeypatch.setattr(W, "run", lambda ns: 0)
    reviewer = program(tmp_path, "reviewer", "\n".join(f"print({line!r})" for line in APPROVED))
    cfg = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": [reviewer, "{prompt}"]})
    review, started = cli.cmd_review, []

    def tracked(cfg, ns):
        started.append(ns.commits)
        return review(cfg, ns)

    monkeypatch.setattr(cli, "cmd_review", tracked)

    assert _catchup(cfg, **PERSON) == 1
    assert started == []
    assert [row["id"] for row in A.open_waivers(root)] == [row["id"] for row in waivers]
    out = capsys.readouterr().out
    assert "review ledger cannot be read" in out
    if failure == "corrupt":
        assert "does not record ao appending" in out
    assert all(f"{row['id']} stays open" in out for row in waivers)


@pytest.mark.parametrize("failure", ["corrupt", "unwritable", "artefact"])
def test_catchup_keeps_a_waiver_open_when_recording_fails_and_reviews_the_next_range(
        project, monkeypatch, capsys, tmp_path, failure):
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    first = _legacy_waiver(root, "B7")
    _land(root, "a.py", "value = 2\n", "b7")
    second = _legacy_waiver(root, "B8")
    _land(root, "a.py", "value = 3\n", "b8")
    monkeypatch.setattr(W, "run", lambda ns: 0)
    reviewer = program(tmp_path, "reviewer", "\n".join(f"print({line!r})" for line in APPROVED))
    cfg = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": [reviewer, "{prompt}"]})
    calls = []
    if failure == "artefact":
        replace = storage.replace_file_durably

        def unwritable(path, *args, **kwargs):
            if os.path.dirname(path) == os.path.join(root, "semantic-review"):
                calls.append(path)
                if len(calls) == 1:
                    raise OSError("review artefact cannot be written")
            return replace(path, *args, **kwargs)

        monkeypatch.setattr(storage, "replace_file_durably", unwritable)
    else:
        append = storage.append_chained_jsonl

        def failed_append(path, *args, **kwargs):
            if path == A.review_ledger_path(root):
                calls.append(path)
                if len(calls) == 1:
                    error = storage.LedgerCorruption if failure == "corrupt" else OSError
                    raise error("review ledger changed after the pre-check")
            return append(path, *args, **kwargs)

        monkeypatch.setattr(storage, "append_chained_jsonl", failed_append)

    assert _catchup(cfg, **PERSON) == 1
    assert len(calls) == 2
    assert [row["id"] for row in A.open_waivers(root)] == [first["id"]]
    (closed,) = [row for row in A.waiver_rows(root) if row.get("event") == "closed"]
    assert (closed["id"], closed["outcome"]) == (second["id"], "reviewed: APPROVED")
    out = capsys.readouterr().out
    assert "review could not be recorded" in out and f"{first['id']} stays open" in out
    assert "catchup handled 1 item(s)" in out
