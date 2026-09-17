import re
from types import SimpleNamespace

from ao import cli, lib as A

MARKED = "# queue is empty and S3 needs a decision\n\n## ACİL\n\nwhich store?\n"


def _mail(project, name, body):
    import os
    open(os.path.join(project["root"], project["mailbox"], name), "w", encoding="utf-8").write(body)


def test_a_marked_message_to_the_architect_reaches_the_architect_in_ao_status(project, monkeypatch, capsys):
    _mail(project, "20260916-1200-kiro-to-fable-BLOCKED-queue.md", MARKED)
    _mail(project, "20260916-1100-fable-to-kiro-INFO-next.md", "# next\n")
    monkeypatch.setenv("AO_ROLE", "architect")
    monkeypatch.setattr(A, "quota", lambda adapter, ttl=300: [])   # no real keyflip: status asks it for the quota

    assert cli.cmd_status(project, SimpleNamespace(messages=4, window=24.0)) in (None, 0)

    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    assert "URGENT" in out and "for the architect" in out and "queue is empty and S3 needs a decision" in out
    assert "1 other message(s) waiting" in out


def test_urgency_is_the_role_the_message_is_addressed_to(project, monkeypatch):
    _mail(project, "20260916-1200-kiro-to-fable-BLOCKED-queue.md", MARKED)
    _mail(project, "20260916-1201-fable-to-kiro-DECISION-stop.md", "# stop S3\n\n## ACİL\n")

    assert [m["id"] for m in A.urgent_messages(project["root"], project)] == ["20260916-1201-fable-to-kiro-DECISION-stop.md"]
    assert [m["to"] for m in A.urgent_messages(project["root"], project, "architect")] == ["architect"]
    assert len(A.urgent_messages(project["root"], project, None)) == 2


def test_board_tail_and_mail_carry_the_banner_for_the_implementer(project, monkeypatch, capsys):
    _mail(project, "20260916-1201-fable-to-kiro-DECISION-stop.md", "# stop S3\n\n## ACİL\n")
    _mail(project, "20260916-1200-kiro-to-fable-BLOCKED-queue.md", MARKED)
    monkeypatch.setenv("AO_ROLE", "implementer")

    cli.cmd_board(project, SimpleNamespace())
    cli.cmd_mail(project, SimpleNamespace(action="list", topic=None, type=None, body=None))
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert out.count("URGENT for the implementer: stop S3") == 2
    assert "for the architect" not in out and "1 other message(s) waiting" in out
