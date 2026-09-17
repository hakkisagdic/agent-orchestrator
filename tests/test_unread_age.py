import os
import re
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A
from tests.scenarios import World

REQUEST = "20260916-0900-kiro-to-fable-BLOCKED-store.md"


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _written(root, name, minutes_ago):
    path = os.path.join(root, "agent-mail", name)
    when = time.time() - minutes_ago * 60
    os.utime(path, (when, when))


def _told(world):
    return [notice for notice in world.notices if "unread decision request" in notice[0]]


def test_an_unseen_decision_request_climbs_to_red_and_stops_once_its_reader_is_shown_it(world, monkeypatch, capsys):
    world.mail(REQUEST, "# which store keeps the ledger?\n\n## KARAR GEREKLİ\n")
    world.transcript_age(900)
    for minutes, audience, level in ((20, "architect", None), (70, "human", "orange"), (250, "human", "red")):
        _written(world.root, REQUEST, minutes)
        world.notices.clear()
        world.cycle()
        told = _told(world)
        assert told and told[-1][2] == audience and told[-1][3] == level, (minutes, told)
        assert f"{REQUEST} has waited {minutes}m" in told[-1][1]

    monkeypatch.setenv("AO_ROLE", "architect")
    cli.cmd_mail(world.cfg, SimpleNamespace(action="list", type="INFO", topic=None, body=None))
    world.notices.clear()
    world.cycle()

    assert _told(world) == []
    assert [row["by"] for row in A.mail_log(world.root) if row.get("event") == "seen"] == ["architect"]


def test_classes_come_from_the_envelope_or_the_kind_and_status_names_the_oldest_unseen(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "quota", lambda adapter, ttl=300: [])   # no real keyflip: status asks it for the quota
    assert A.mail_class("20260916-0900-kiro-to-fable-DONE-b6.md") == "fyi"
    assert A.mail_class("20260916-0900-kiro-to-fable-BLOCKED-b6.md") == "needs-decision"
    assert A.mail_class("20260916-0900-fable-to-kiro-DIRECTIVE-b6.md") == "needs-read"
    assert A.mail_class("x.md", {"class": "needs-decision", "kind": "note"}) == "needs-decision"
    A.write_mail(root, project, "20260916-0901-fable-to-kiro-NOTE-a.md", "# a\n", {"kind": "note", "class": "fyi"})
    with open(os.path.join(root, "agent-mail", REQUEST), "w", encoding="utf-8") as fh:
        fh.write("# which store?\n\n## KARAR GEREKLİ\n")
    _written(root, REQUEST, 42)

    text = re.sub(r"\x1b\[[0-9;]*m", "", cli.render(project, 0))

    assert f"oldest unseen {REQUEST}  needs-decision, 42m · 1 more unseen" in text
