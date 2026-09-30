"""What the retrospective review of UNREAD-AGE found that still held (UNREAD-AGE-2).

A message whose body could not be read, or whose written time in the mail ledger was no number, was
dropped from the unseen messages, so a decision request so read never climbed the ladder. And which
time ages a message was shown only for mail written by hand: one ao writes is aged from the ledger.
"""
import os
import time

import pytest

from ao import lib as A
from tests.test_unread_age import REQUEST

BODY = "# which store keeps the ledger?\n\n## KARAR GEREKLİ\n"


def _by_hand(root, minutes_ago):
    path = os.path.join(root, "agent-mail", REQUEST)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(BODY)
    when = time.time() - minutes_ago * 60
    os.utime(path, (when, when))
    return path


def test_a_message_whose_ledger_time_is_no_number_is_aged_by_its_file(project):
    _by_hand(project["root"], 90)
    A.mail_ledger_append(project["root"], {"event": "written", "id": REQUEST, "at": "yesterday"})

    (message,) = A.unseen_messages(project["root"], project)

    assert message["class"] == "needs-decision" and 85 * 60 < message["age"] < 95 * 60


def test_a_message_ao_wrote_is_aged_from_the_ledger_whatever_its_file_says(project):
    A.write_mail(project["root"], project, REQUEST, BODY)
    old = time.time() - 300 * 60
    os.utime(os.path.join(project["root"], "agent-mail", REQUEST), (old, old))

    (message,) = A.unseen_messages(project["root"], project)

    assert message["age"] < 60


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="a file's mode does not keep this user out here")
def test_a_message_that_cannot_be_read_is_still_one_nobody_has_been_shown(project):
    path = _by_hand(project["root"], 30)
    os.chmod(path, 0)
    try:
        assert [message["id"] for message in A.unseen_messages(project["root"], project)] == [REQUEST]
    finally:
        os.chmod(path, 0o644)
