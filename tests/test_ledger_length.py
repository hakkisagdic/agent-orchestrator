import os
from types import SimpleNamespace

import pytest

from ao import storage as S

CHAIN = "test-ledger"


def _ledger(tmp_path, rows):
    path = str(tmp_path / "authority.jsonl")
    for index in range(rows):
        S.append_chained_jsonl(path, {"grant": index}, CHAIN)
    return path


def _keep_first(path, keep):
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(lines[:keep])


@pytest.mark.parametrize("keep", [4, 2, 1, 0])
def test_a_ledger_cut_short_is_a_broken_commitment_not_a_shorter_chain(tmp_path, keep):
    path = _ledger(tmp_path, 5)
    _keep_first(path, keep)

    with pytest.raises(S.LedgerCorruption, match="recorded 5"):
        S.read_chained_jsonl(path, CHAIN)


def test_a_deleted_ledger_is_detected_too(tmp_path):
    path = _ledger(tmp_path, 3)
    os.remove(path)

    with pytest.raises(S.LedgerCorruption, match="recorded 3"):
        S.read_chained_jsonl(path, CHAIN)


def test_every_row_carries_its_ordinal(tmp_path):
    path = _ledger(tmp_path, 3)

    assert [row["ordinal"] for row in S.read_chained_jsonl(path, CHAIN)] == [1, 2, 3]


def test_nothing_is_appended_onto_a_ledger_that_lost_its_tail(tmp_path):
    path = _ledger(tmp_path, 3)
    _keep_first(path, 1)

    with pytest.raises(S.LedgerCorruption):
        S.append_chained_jsonl(path, {"grant": "after the cut"}, CHAIN)
    with open(path, encoding="utf-8") as handle:
        assert len(handle.readlines()) == 1


def test_commit_check_refuses_when_the_authority_ledger_lost_its_tail(project, capsys):
    from ao import cli, lib as A

    root = project["root"]
    for token in ("C-1", "C-2"):
        A.record_authority(root, False, ["no staged candidate"], "tree", "V-1", token=token)
    _keep_first(os.path.join(root, ".ao", "ledger", "authority.jsonl"), 1)

    assert cli.cmd_commit_check(project, SimpleNamespace()) == 1
    assert "rows were removed from its end" in capsys.readouterr().out
