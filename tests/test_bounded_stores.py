import json
import os
import time

import pytest

from ao import lib as A, storage


def _grants(root, count):
    for n in range(count):
        A.record_authority(root, n % 2 == 0, [] if n % 2 == 0 else ["refused"], f"sha256:{n}", f"V-{n}", f"C-{n}")


def test_the_authority_chain_verifies_across_a_seal_and_gives_the_same_answer(project):
    root = project["root"]
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    _grants(root, 9)
    before = A.latest_authority_decision(root)
    newest = A.authority_rows(root)[-3:]

    seal = storage.seal_chained_jsonl(path, A.AUTHORITY_CHAIN, keep=3)

    assert seal["retired"] == 6
    assert A.latest_authority_decision(root) == before and A.authority_rows(root) == newest
    assert len(storage.sealed_rows(path)) == 6
    A.record_authority(root, True, [], "sha256:new", "V-new", "C-new")
    assert [row["token"] for row in A.authority_rows(root)][-2:] == ["C-8", "C-new"]
    assert A.authority_rows(root)[-1]["ordinal"] == 10


def test_a_seal_removed_or_forged_is_refused_like_a_broken_chain(project):
    root = project["root"]
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    _grants(root, 5)
    storage.seal_chained_jsonl(path, A.AUTHORITY_CHAIN, keep=2)
    seal_file = storage.seal_path(path)
    saved = open(seal_file, encoding="utf-8").read()

    os.remove(seal_file)
    with pytest.raises(storage.LedgerCorruption):
        A.authority_rows(root)

    forged = dict(json.loads(saved), retired=2)
    with open(seal_file, "w", encoding="utf-8") as fh:
        json.dump(forged, fh)
    with pytest.raises(storage.LedgerCorruption):
        A.authority_rows(root)

    with open(seal_file, "w", encoding="utf-8") as fh:
        fh.write(saved)
    assert len(A.authority_rows(root)) == 2


def test_a_seal_interrupted_after_its_intent_is_finished_or_undone(project):
    root = project["root"]
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    _grants(root, 4)
    whole = open(path, "rb").read()
    intent = {"chain": A.AUTHORITY_CHAIN, "retired": 2, "digest": "sha256:" + "0" * 64, "archives": [], "at": 1}
    with open(storage.seal_path(path) + ".pending", "w", encoding="utf-8") as fh:
        json.dump(intent, fh)

    assert len(A.authority_rows(root)) == 4                 # the ledger was never rewritten: the intent is dropped
    assert open(path, "rb").read() == whole and not os.path.exists(storage.seal_path(path) + ".pending")


def test_a_referenced_verification_survives_a_prune_that_removes_its_neighbours(project):
    root = project["root"]
    path = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    for n in range(6):
        A.record_verification(root, {"id": f"V-{n}", "at": n, "passed": True})
    A.record_authority(root, True, [], "sha256:t", "V-1", "C-1")

    storage.seal_chained_jsonl(path, A.VERIFICATION_CHAIN, keep=2, legacy_prefix=True)

    live = [row["id"] for row in storage.read_chained_jsonl(path, A.VERIFICATION_CHAIN, legacy_prefix=True)]
    assert live == ["V-4", "V-5"]
    assert A.verification_by_id(root, "V-1")["id"] == "V-1"
    assert A.latest_verification(root)["id"] == "V-5"
    assert not [f for f in A.consistency_findings(root, project) if f["kind"] == "grant-verification"]


def test_an_observation_store_is_held_to_its_bound_as_it_is_written(project):
    root = project["root"]
    notices = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(dict({k: v for k, v in project.items() if k != "root"}, retention={"observation_kb": 64}), fh)
    for n in range(900):
        A.record_notice(root, f"notice {n}", "x" * 80, False, key=f"k{n}")

    assert os.path.getsize(notices) <= 64 * 1024 * 1.25 + 400
    rows = [json.loads(line) for line in open(notices, encoding="utf-8")]
    assert rows[-1]["title"] == "notice 899" and rows[0]["title"] != "notice 0"
