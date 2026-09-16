import json
import os
import time

import pytest

from ao import lib as A, settings as S
from tests.scenarios import World

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake keyflip is a POSIX shell script")


def _machine(**values):
    with open(S.machine_path(), "w", encoding="utf-8") as fh:
        json.dump(values, fh)


def _fake_keyflip(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "keyflip-calls.txt"
    script = bindir / "keyflip"
    script.write_text(f"#!/bin/sh\necho \"$@\" >> {calls}\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    return calls


def _windows(monkeypatch, *percents):
    readings = list(percents)
    monkeypatch.setattr(A, "provider_window", lambda name="claude": {
        "pct": readings.pop(0) if len(readings) > 1 else readings[0], "window": "5h", "resets_in": "2h",
        "resets_s": 7200, "raw": "claude"})


def test_with_rotation_off_ao_neither_reads_nor_rotates(project, monkeypatch):
    monkeypatch.setattr(A, "provider_window", lambda name="claude": pytest.fail("read a window with rotation off"))

    assert A.rotate_if_exhausted(project, ["claude", "-p", "x"], "reviewer")["ok"] is True


def test_a_spent_window_is_rotated_through_keyflip_before_the_actor_starts(project, tmp_path, monkeypatch):
    _machine(keyflip={"rotation": "on"})
    calls = _fake_keyflip(tmp_path, monkeypatch)
    _windows(monkeypatch, 99, 99, 20)

    headroom = A.rotate_if_exhausted(project, ["claude", "-p", "x"], "reviewer")

    assert headroom == {"ok": True, "provider": "claude", "rotated": True,
                        "text": "rotated through keyflip for the reviewer: claude now 20% used"}
    assert calls.read_text().split("\n")[0] == "next --strategy best"
    assert A.rotate_if_exhausted(project, ["kiro-cli", "chat", "x"], "implementer")["text"] == "rotation off"


def test_no_headroom_anywhere_keeps_the_architect_asleep_and_tells_a_person(project, tmp_path, monkeypatch):
    _machine(keyflip={"rotation": "on"})
    _fake_keyflip(tmp_path, monkeypatch)
    world = World(project, monkeypatch, tmp_path)
    _windows(monkeypatch, 99)
    world.transcript_age(900)
    world.mail("20260916-0900-kiro-to-fable-BLOCKED-store.md", "# which store?\n\n## KARAR GEREKLİ\n")

    world.cycle(dry_run=False)

    assert not [argv for argv in world.spawned if isinstance(argv, list) and argv and argv[0].endswith("/claude")]
    told = [notice for notice in world.notices if notice[0].endswith("no headroom")]
    assert told and told[0][2] == "human" and "claude" in told[0][1]
