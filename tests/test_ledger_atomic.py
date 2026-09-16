import errno
import json
import os
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage

CHAIN = "test-atomic-chain-v1"


def _ledger(tmp_path, rows=1):
    path = str(tmp_path / "authority.jsonl")
    for index in range(rows):
        storage.append_chained_jsonl(path, {"grant": index}, CHAIN)
    return path


def _short_then_full(fd, data):
    if not getattr(_short_then_full, "wrote", False):
        _short_then_full.wrote = True
        return os.write(fd, bytes(data[:-1]))
    raise OSError(errno.ENOSPC, "disk full")


def _eio(_fd):
    raise OSError(errno.EIO, "fsync failed")


@pytest.mark.parametrize("step", ["short-write", "fsync", "length-record"])
def test_a_failed_append_leaves_the_ledger_exactly_as_it_was(tmp_path, monkeypatch, step):
    path = _ledger(tmp_path, 2)
    before = open(path, "rb").read()
    kwargs = {}
    if step == "short-write":
        _short_then_full.wrote = False
        kwargs["_write"] = _short_then_full
    elif step == "fsync":
        kwargs["_fsync"] = _eio
    else:
        monkeypatch.setattr(storage, "_record_committed_length",
                            lambda *args: (_ for _ in ()).throw(OSError(errno.EIO, "record lost")))

    with pytest.raises(OSError):
        storage.append_chained_jsonl(path, {"granted": True, "token": "C-reported-refused"}, CHAIN, **kwargs)

    assert open(path, "rb").read() == before
    assert [row["grant"] for row in storage.read_chained_jsonl(path, CHAIN)] == [0, 1]


def test_a_new_ledger_whose_directory_cannot_be_synced_is_not_left_behind(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows has no directory fsync API")
    path = str(tmp_path / "authority.jsonl")
    monkeypatch.setattr(storage, "_sync_directory",
                        lambda *args: (_ for _ in ()).throw(OSError(errno.ENOSYS, "not implemented")))

    with pytest.raises(OSError, match="not implemented"):
        storage.append_chained_jsonl(path, {"granted": True}, CHAIN)

    assert not os.path.exists(path)
    assert storage.read_chained_jsonl(path, CHAIN) == []


def test_an_unterminated_row_is_never_read_and_is_cut_off_not_completed(tmp_path):
    path = _ledger(tmp_path, 1)
    first = storage.read_chained_jsonl(path, CHAIN)[0]
    grant = {"previous": storage.chained_row_digest(first, CHAIN), "ordinal": 2, "granted": True}
    with open(path, "ab") as handle:
        handle.write(json.dumps(grant, separators=(",", ":")).encode("ascii"))

    assert storage.read_chained_jsonl(path, CHAIN) == [first]
    with pytest.raises(storage.LedgerCorruption, match="uncommitted final record"):
        storage.read_chained_jsonl(path, CHAIN, allow_partial_tail=False)

    second = storage.append_chained_jsonl(path, {"grant": "after"}, CHAIN)
    assert storage.read_chained_jsonl(path, CHAIN) == [first, second]
    assert b'"granted":true' not in open(path, "rb").read()


def test_a_row_the_chain_digest_cannot_read_is_never_written(tmp_path):
    path = _ledger(tmp_path, 1)
    before = open(path, "rb").read()

    with pytest.raises(ValueError):
        storage.append_chained_jsonl(path, {"weight": float("nan")}, CHAIN)
    assert open(path, "rb").read() == before

    undecodable = os.fsdecode(b"caf\xe9.txt")
    row = storage.append_chained_jsonl(path, {"changed_paths": [undecodable]}, CHAIN)
    assert storage.read_chained_jsonl(path, CHAIN)[-1] == row
    assert row["changed_paths"] == [undecodable]


def test_plan_baselines_survive_a_torn_append_and_fail_closed_on_corruption(project):
    root = project["root"]
    ledger = os.path.join(root, ".ao", "ledger", "plans.jsonl")
    A.record_plan(root, "B4", "d4")
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write('{"item":"B5","dig')
    A.record_plan(root, "B6", "d6")

    assert A.plan_baseline(root) == {"B4": "d4", "B6": "d6"}

    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write("not json\n")
    with pytest.raises(storage.LedgerCorruption):
        A.plan_baseline(root)


def test_commit_ok_refuses_when_plan_baselines_cannot_be_read(project, monkeypatch, capsys):
    root = project["root"]
    os.makedirs(os.path.join(root, ".ao", "ledger"), exist_ok=True)
    with open(os.path.join(root, ".ao", "ledger", "plans.jsonl"), "w", encoding="utf-8") as handle:
        handle.write("not json\n")
    monkeypatch.setattr(A, "hold_state", lambda root: None)
    monkeypatch.setattr(A, "urgent_messages", lambda *args, **kwargs: [])
    capsys.readouterr()

    assert cli.cmd_commit_ok(dict(project, features={"review": False}),
                             SimpleNamespace(verify=False, profile=None)) == 1
    assert "plan baselines cannot be read" in capsys.readouterr().out


def test_prune_keeps_evidence_whole_and_rewrites_logs_whole(project, capsys):
    root = project["root"]
    verifications = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    A.record_verification(root, {"id": "V-old", "at": 1, "passed": True})
    kept = open(verifications, "rb").read()
    notices = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    with open(notices, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": 1, "title": "old"}) + "\n")
        handle.write(json.dumps({"at": 4_000_000_000, "title": "new"}) + "\n")

    cli.cmd_prune(project, SimpleNamespace(days=1, keep_kb=64, evidence=True, yes=True))

    assert open(verifications, "rb").read() == kept
    assert A.latest_verification(root)["id"] == "V-old"
    assert "evidence is never pruned" in capsys.readouterr().out
    assert [json.loads(line)["title"] for line in open(notices, encoding="utf-8")] == ["new"]


def test_reading_the_helper_registry_writes_nothing(project, monkeypatch, tmp_path):
    starts = {290: "process-a", 291: "process-b"}
    monkeypatch.setattr(A, "HOME", str(tmp_path))
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: starts.get(pid))
    root = project["root"]

    A.helper_register(root, 290, "reviewer")
    path = A.helpers_path(root)
    before = open(path, "rb").read()
    starts[290] = None
    assert A.helper_pids(root) == set()
    assert open(path, "rb").read() == before

    starts[290] = "process-a"
    A.helper_register(root, 291, "architect")
    assert A.helper_pids(root) == {290, 291}
    A.helper_release(root, 290)
    assert A.helper_pids(root) == {291}
