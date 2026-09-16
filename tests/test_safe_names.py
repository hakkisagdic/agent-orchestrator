import os
import re
from types import SimpleNamespace

from ao import cli, lib as A

HOSTILE = 'a/b\\c:d*e?f"g<h>i|j k\tl'


def test_a_topic_with_every_forbidden_character_lands_in_the_mailbox(project, capsys):
    args = SimpleNamespace(action="send", topic=HOSTILE, type="info/../x", body="hello")

    cli.cmd_mail(project, args)

    names = os.listdir(os.path.join(project["root"], project["mailbox"]))
    assert len(names) == 1 and names[0] == capsys.readouterr().out.strip()
    assert re.fullmatch(r"[A-Za-z0-9._-]+", names[0]), names[0]


def test_the_slug_keeps_only_safe_characters_and_never_comes_back_empty():
    assert A.safe_slug(HOSTILE) == "a-b-c-d-e-f-g-h-i-j-k-l"
    assert A.safe_slug("kuyruk boş, karar gerekli") == "kuyruk-bo-karar-gerekli"
    assert A.safe_slug("../../etc") == "etc"
    assert A.safe_slug("///", "mesaj") == "mesaj"
    assert len(A.safe_slug("x" * 200)) == 40


def test_a_note_to_an_unsafe_name_stays_in_the_mailbox(project):
    name = A.note(project["root"], project, "kiro/../../x", "title: with/slash", "body")

    assert os.path.exists(os.path.join(project["root"], project["mailbox"], name))
    assert "/" not in name
