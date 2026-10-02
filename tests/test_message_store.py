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


def test_mail_ao_writes_is_in_the_store_before_any_reconcile(project):
    """MESSAGE-STORE-2: mail ao wrote reached the store at the next reconcile, and a view file removed before then
    took its message with it."""
    root = project["root"]
    cfg = _mode(project, "append-only")
    _write(root, cfg, NAMES[0])
    os.remove(os.path.join(root, "agent-mail", NAMES[0]))

    assert A.mailbox(root, "agent-mail") == [NAMES[0]]


def test_a_body_stored_without_its_row_is_taken_in_at_the_next_reconcile(project, monkeypatch):
    """MESSAGE-STORE-3: the body was stored and its row's append failed; with the view removed before the next
    reconcile, the message was gone from the queue and its body orphaned in the store."""
    from ao import storage
    root = project["root"]
    cfg = _mode(project, "append-only")
    append = A._mail_store_append

    def lock_held(root_, row):
        if row.get("event") == "message":
            raise storage.LedgerLockTimeout("timed out")
        return append(root_, row)
    monkeypatch.setattr(A, "_mail_store_append", lock_held)
    _write(root, cfg, NAMES[0])
    monkeypatch.setattr(A, "_mail_store_append", append)
    os.remove(os.path.join(root, "agent-mail", NAMES[0]))

    A.reconcile_mail_ledger(root, cfg)

    assert A.mailbox(root, "agent-mail") == [NAMES[0]]
    assert os.path.exists(os.path.join(root, "agent-mail", NAMES[0]))


def test_a_compaction_cut_short_keeps_the_body_and_writes_its_archive_durably(project, monkeypatch):
    """MESSAGE-STORE-2: the archive was written without reaching the disk before the body became its stub, and a
    compaction whose record did not land left a stub the next one wrote over the archive."""
    from ao import storage
    root = project["root"]
    cfg = _mode(project, "append-only")
    _write(root, cfg, NAMES[0], body="# the words\n\nthe original words\n")
    A.reconcile_mail_ledger(root, cfg)
    before = A.message_body(root, NAMES[0])
    written, append = [], A._mail_store_append
    durable = storage.replace_file_durably
    monkeypatch.setattr(storage, "replace_file_durably", lambda path, data: written.append(path) or durable(path, data))

    def cut_short(root_, row):
        if row.get("event") == "compacted":
            raise OSError("the process was stopped")
        return append(root_, row)
    monkeypatch.setattr(A, "_mail_store_append", cut_short)
    with pytest.raises(OSError):
        A.compact_messages(root, 0, now=time.time() + 10)
    monkeypatch.setattr(A, "_mail_store_append", append)

    assert os.path.normpath(written[0]).endswith(os.path.join("archive", NAMES[0] + ".gz"))
    assert A.compact_messages(root, 0, now=time.time() + 10) == [NAMES[0]]
    assert b"the original words" in before and A.message_body(root, NAMES[0]) == before
    assert A.compact_messages(root, 0, now=time.time() + 10) == []


def test_the_doctor_names_a_mail_store_it_cannot_read(project):
    """MESSAGE-STORE-2: a corrupt store left the queue to the view, and nothing said so."""
    root = project["root"]
    cfg = _mode(project, "append-only")
    for name in NAMES[:2]:
        _write(root, cfg, name)
    _ack(cfg, NAMES[0])
    ledger = os.path.join(root, ".ao", "ledger", "mail-store.jsonl")
    lines = open(ledger, encoding="utf-8").readlines()
    with open(ledger, "w", encoding="utf-8") as fh:
        fh.writelines(lines[:-1])

    problems = dict(cli.doctor_problems(A.load_config(root)))

    assert "the mail store cannot be read" in problems["mail-store"]


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
