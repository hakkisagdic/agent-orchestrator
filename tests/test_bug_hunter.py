import os
import re
import subprocess
import sys
import time
from datetime import datetime
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


def _stand_in(monkeypatch):
    """Ship an adapter for the stand-in hunter, a Python script, with -B as the flag its command must carry.

    -B writes no bytecode and changes nothing else; -I would also drop PYTHONUTF8, and on Windows the
    leads' dash came back in the ANSI code page, where no lead could be read.
    """
    shipped = A.package_adapters()
    stand_in = {"id": "stand-in", "detect": {"binaries": [os.path.basename(sys.executable)]},
                "options": {"trust_none": ["-B"]}}
    monkeypatch.setattr(A, "package_adapters", lambda: dict(shipped, **{"stand-in": stand_in}))


class _OneMinute(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 1, 12, 0, 30, tzinfo=tz)


def _hunt(cfg, action="run", fingerprint=None):
    return cli.cmd_hunt(cfg, SimpleNamespace(action=action, fingerprint=fingerprint))


def test_a_hunt_mails_new_leads_suppresses_repeats_and_remembers_discards(project, monkeypatch, capsys):
    root = project["root"]
    _repo(root)
    _stand_in(monkeypatch)
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-B", "-c", LEADS, "{prompt}"]})
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


@pytest.mark.parametrize("argv", [["sh", "-c", "printf owned > src/a.py", "{prompt}"],
                                  ["codex", "exec", "-s", "workspace-write", "{prompt}"],
                                  [sys.executable, "-c", LEADS, "{prompt}"]])
def test_a_hunter_no_shipped_adapter_holds_to_reading_is_refused(project, monkeypatch, capsys, argv):
    """BUG-HUNTER-2: a program no adapter declares, or a harness that cannot run without tools, passed the check,
    and a `sh -c` hunter rewrote a file."""
    root = project["root"]
    _repo(root)
    monkeypatch.setattr(cli, "_run_reviewer", lambda *a, **k: pytest.fail("the hunter ran"))

    assert _hunt(dict(project, hunter={"id": "h1", "argv": argv})) == 2

    assert "the hunter must not be able to write" in capsys.readouterr().out
    with open(os.path.join(root, "src", "a.py"), encoding="utf-8") as fh:
        assert fh.read() == "def f():\n    return 1\n"


def test_a_hunter_missing_a_flag_its_harness_reads_by_is_refused(project, monkeypatch, capsys):
    _stand_in(monkeypatch)
    monkeypatch.setattr(cli, "_run_reviewer", lambda *a, **k: pytest.fail("the hunter ran"))

    assert _hunt(dict(project, hunter={"id": "h1", "argv": [sys.executable, "-c", LEADS, "{prompt}"]})) == 2

    assert "its command lacks -B" in capsys.readouterr().out


def test_a_hunt_reads_its_budget_in_bytes(project):
    """BUG-HUNTER-2: the budget counted characters, and a file of `é` was read at twice the bytes it allowed."""
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "u.py"), "w", encoding="utf-8") as fh:
        fh.write("é" * 60000)
    subprocess.run(["git", "add", "src"], cwd=root, check=True)
    cfg = dict(project, hunter={"bytes_per_run": 60000, "files_per_run": 1})

    read = [text for _ in range(10) for path, text in A.hunt_slice(root, cfg)[0] if path == "src/u.py"]

    assert read and all(len(text.encode("utf-8")) <= 60000 for text in read)


def test_two_hunts_in_one_minute_keep_both_lead_mails(project, monkeypatch):
    """BUG-HUNTER-2: a lead mail was named by its minute and first path, and a second hunt wrote over the first."""
    root = project["root"]
    _repo(root)
    _stand_in(monkeypatch)
    monkeypatch.setattr(cli, "datetime", _OneMinute)
    for lead in ("- [clock] src/a.py:3 parse_reset — x", "- [durability] src/a.py:3 parse_reset — y"):
        argv = [sys.executable, "-B", "-c", f"print({lead!r})", "{prompt}"]
        assert _hunt(dict(project, hunter={"id": "h1", "argv": argv})) == 0

    assert len([name for name in A.mailbox(root, "agent-mail") if "-hunter-to-" in name]) == 2


def test_the_watchdog_starts_a_hunt_only_when_switched_on_and_due(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-c", LEADS, "{prompt}"]})
    st = {}

    assert W._schedule_hunt(world.root, cfg, st) is False
    cfg["features"] = {"hunter": True}
    assert W._schedule_hunt(world.root, cfg, st) is True
    assert any(isinstance(argv, list) and argv[-2:] == ["hunt", "run"] for argv in world.spawned)
    assert W._schedule_hunt(world.root, cfg, st) is False
