"""What the retrospective review of ROLE-TABLE found that still held (ROLE-TABLE-2).

`ao role swap` with its second role left out, or an actor's name in its place, wrote a role of no name
into the table and left the one named empty. The handoff note, the note a hold's release leaves and a
person's message from Telegram were written past write_mail: no envelope with the roles, no credential
scan, nothing in the mail ledger.
"""
import json
import os
import time
from types import SimpleNamespace

from ao import cli, lib as A, telegram


def _config(root):
    with open(os.path.join(root, ".ao", "config.json"), encoding="utf-8") as fh:
        return fh.read()


def test_a_swap_names_two_different_roles(project, capsys):
    before = _config(project["root"])
    for other in (None, "kiro", "implementer"):
        assert cli.cmd_role(project, SimpleNamespace(action="swap", role="implementer", actor=other)) == 2

    assert _config(project["root"]) == before
    assert "ao role swap names two different roles" in capsys.readouterr().out


def _written(root, name):
    return [row for row in A.mail_log(root, 200) if row.get("event") == "written" and row.get("id") == name]


def test_the_note_a_hold_release_leaves_is_mail_ao_wrote(project):
    root = project["root"]
    with open(os.path.join(root, A.HOLD_FILE), "w", encoding="utf-8") as fh:
        json.dump({"by": "a person", "reason": "editing", "at": int(time.time()), "stopped": []}, fh)

    assert cli.cmd_hold(project, SimpleNamespace(action="release", note="the parser moved", by=None, reason=None,
                                                 grace=5)) == 0

    (name,) = [n for n in A.mailbox(root, project["mailbox"]) if n.endswith("-INFO-hold-released.md")]
    meta = A.mail_meta(os.path.join(root, project["mailbox"], name))
    assert (meta["from_role"], meta["to_role"], meta["class"]) == ("architect", "implementer", "fyi")
    assert _written(root, name)


def test_a_handoff_note_is_mail_ao_wrote(project, monkeypatch):
    monkeypatch.setattr(A, "busy", lambda *args, **kwargs: ("idle", None, ""))
    monkeypatch.setattr(A, "account_usage", lambda *args, **kwargs: None)

    assert cli.cmd_handoff(project, SimpleNamespace(reason=None, no_send=True)) == 0

    (name,) = A.mailbox(project["root"], project["mailbox"])
    assert A.mail_meta(os.path.join(project["root"], project["mailbox"], name))["from_role"] == "architect"
    assert _written(project["root"], name)


def test_a_persons_message_from_telegram_is_mail_ao_wrote(project, monkeypatch, tmp_path):
    updates = [{"update_id": 7, "message": {"text": "stop the deploy", "chat": {"id": 42}, "from": {"username": "a"}}}]
    monkeypatch.setattr(telegram, "config", lambda: {"token": "t", "chats": ["42"]})
    monkeypatch.setattr(telegram, "api", lambda conf, method, **params:
                        {"ok": True, "result": updates} if method == "getUpdates" else {"ok": True})
    monkeypatch.setattr(telegram, "_offset_path", lambda: str(tmp_path / "telegram-offset"))

    [name] = telegram.poll(project["root"], project)["written"]

    meta = A.mail_meta(os.path.join(project["root"], project["mailbox"], name))
    assert (meta["class"], meta["to_role"]) == ("urgent", "implementer") and _written(project["root"], name)
    assert [message["id"] for message in A.urgent_messages(project["root"], project)] == [name]
