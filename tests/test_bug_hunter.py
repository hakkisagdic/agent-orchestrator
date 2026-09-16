import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W
from tests.scenarios import World

LEADS = ("import sys\n"
         "print('- [clock] src/a.py:3 parse_reset — a reset named for tomorrow is read as today')\n"
         "print('- [durability] src/b.py:9 save — written in place; a crash leaves half a file')\n"
         "print('some prose that is not a lead')\n")


def _repo(root):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    for name in ("a.py", "b.py"):
        with open(os.path.join(root, "src", name), "w", encoding="utf-8") as fh:
            fh.write("def f():\n    return 1\n")
    subprocess.run(["git", "add", "src"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "src"], cwd=root, check=True)


def _hunt(cfg, action="run", fingerprint=None):
    return cli.cmd_hunt(cfg, SimpleNamespace(action=action, fingerprint=fingerprint))


def test_a_hunt_mails_new_leads_suppresses_repeats_and_remembers_discards(project, capsys):
    root = project["root"]
    _repo(root)
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-c", LEADS, "{prompt}"]})
    before = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True,
                            text=True).stdout

    assert _hunt(cfg) == 0

    mails = [name for name in A.mailbox(root, "agent-mail") if "-hunter-to-" in name]
    assert len(mails) == 1
    body = open(os.path.join(root, "agent-mail", mails[0]), encoding="utf-8").read()
    fingerprints = re.findall(r"\(`([0-9a-f]{16})`\)", body)
    assert "src/a.py:3 parse_reset" in body and len(fingerprints) == 2
    assert subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True,
                          text=True).stdout == before
    assert not [a for a in A.anomalies(root, cfg, {}, 0, 360) if a.get("kind") in ("report-waiting", "decision-requested")]

    os.remove(os.path.join(root, "agent-mail", mails[0]))
    assert _hunt(cfg, "discard", fingerprints[0]) == 0
    assert _hunt(cfg) == 0
    assert [name for name in A.mailbox(root, "agent-mail") if "-hunter-to-" in name] == []
    assert any(row["event"] == "discarded" and row["fingerprint"] == fingerprints[0] for row in A.hunter_rows(root))
    assert re.search(r"hunted \d+ file\(s\): 2 lead\(s\), 0 new", capsys.readouterr().out)


def test_a_hunter_that_could_write_is_refused(project, capsys):
    cfg = dict(project, hunter={"id": "h1", "argv": ["claude", "-p", "{prompt}", "--dangerously-skip-permissions"]})

    assert _hunt(cfg) == 2

    assert "the hunter must not be able to write" in capsys.readouterr().out


def test_the_watchdog_starts_a_hunt_only_when_switched_on_and_due(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-c", LEADS, "{prompt}"]})
    st = {}

    assert W._schedule_hunt(world.root, cfg, st) is False
    cfg["features"] = {"hunter": True}
    assert W._schedule_hunt(world.root, cfg, st) is True
    assert any(isinstance(argv, list) and argv[-2:] == ["hunt", "run"] for argv in world.spawned)
    assert W._schedule_hunt(world.root, cfg, st) is False
