import json
import os
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage

NAMES = ["20260916-0900-fable-to-kiro-NOTE-a.md", "20260916-0901-fable-to-kiro-NOTE-b.md",
         "20260916-0902-fable-to-kiro-NOTE-c.md"]


def _mode(project, mode):
    root = project["root"]
    stored = {key: value for key, value in project.items() if key != "root"}
    stored["mail"] = {"store": mode}
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    return A.load_config(root)


def _write(root, cfg, name, body=None):
    A.write_mail(root, cfg, name, body or f"# {name}\n\nabout the claim journal\n",
                 {"kind": "note", "from": "fable", "to": "kiro"})


def _ack(cfg, name):
    return cli.cmd_mail(cfg, SimpleNamespace(action="ack", type=name, topic=None, body="done"))


@pytest.mark.parametrize("mode", ["deletion", "append-only"])
def test_the_derived_queue_answers_exactly_as_deletion_did(project, mode):
    root = project["root"]
    cfg = _mode(project, mode)
    for name in NAMES:
        _write(root, cfg, name)
    A.reconcile_mail_ledger(root, cfg)

    _ack(cfg, NAMES[1])
    A.reconcile_mail_ledger(root, cfg)

    assert A.mailbox(root, "agent-mail") == [NAMES[0], NAMES[2]]
    assert sorted(os.listdir(os.path.join(root, "agent-mail"))) == [NAMES[0], NAMES[2]]


def test_removing_a_message_without_handling_it_removes_nothing(project):
    root = project["root"]
    cfg = _mode(project, "append-only")
    _write(root, cfg, NAMES[0])
    A.reconcile_mail_ledger(root, cfg)
    view = os.path.join(root, "agent-mail", NAMES[0])

    os.remove(view)
    assert A.mailbox(root, "agent-mail") == [NAMES[0]]
    A.reconcile_mail_ledger(root, cfg)
    assert os.path.exists(view)

    os.remove(A._store_path(root, NAMES[0]))
    os.remove(view)
    assert A.unhandled_messages(root) == [NAMES[0]]


def test_a_compacted_stub_still_answers_unhandled_and_brings_its_body_back(project):
    root = project["root"]
    cfg = _mode(project, "append-only")
    _write(root, cfg, NAMES[0], "# a\n\nthe original words\n")
    _write(root, cfg, NAMES[1])
    A.reconcile_mail_ledger(root, cfg)
    _ack(cfg, NAMES[1])

    assert sorted(A.compact_messages(root, 0, now=time.time() + 10)) == sorted(NAMES[:2])

    assert open(A._store_path(root, NAMES[0]), "rb").read().startswith(b"ao-mail-stub v1\n")
    assert A.mailbox(root, "agent-mail") == [NAMES[0]]
    os.remove(os.path.join(root, "agent-mail", NAMES[0]))
    A.reconcile_mail_ledger(root, cfg)
    assert "the original words" in open(os.path.join(root, "agent-mail", NAMES[0]), encoding="utf-8").read()
    assert [row["id"] for row in A.room_search("original words", root)] == [NAMES[0]]


def test_a_handling_record_cut_from_the_store_fails_closed(project):
    root = project["root"]
    cfg = _mode(project, "append-only")
    for name in NAMES[:2]:
        _write(root, cfg, name)
    A.reconcile_mail_ledger(root, cfg)
    _ack(cfg, NAMES[0])
    ledger = os.path.join(root, ".ao", "ledger", "mail-store.jsonl")
    lines = open(ledger, encoding="utf-8").readlines()

    with open(ledger, "w", encoding="utf-8") as fh:
        fh.writelines(lines[:-1])

    with pytest.raises(storage.LedgerCorruption):
        A.unhandled_messages(root)
