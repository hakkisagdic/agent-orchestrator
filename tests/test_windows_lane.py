"""What the Windows lane's first full run found in ao, done here the way Windows does it (#71).

A harness on Windows names the files it writes with backslashes, and `ao cost` counted a turn
that wrote product or coordination files as analysis. CPython 3.12 and later on Windows read two
clocks into `st_ctime`: a path's stat reports when a file was created, a handle's when its
metadata last changed, so `ao init` took the marker it had just written for one that changed
before it could be read. Each test holds on every platform.
"""
import ntpath
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A
from tests.test_second_harness_cost import R1, R2, R3, R4, R5, _call, _prompt, _response, _result, _text, _world
from tests.test_windows_followups import _Instead

WINDOWS_ROOT = "C:\\Users\\me\\work\\proj"
# How much sooner a file was created than its metadata last changed, as a report of the two clocks measured it.
CREATED_SOONER_NS = 104_669_900


def test_a_write_a_windows_harness_names_with_backslashes_is_product_or_coordination(project, monkeypatch,
                                                                                     tmp_path):
    now = time.time()
    parser, note = ntpath.join(WINDOWS_ROOT, "src", "parser.py"), ntpath.join(WINDOWS_ROOT, "agent-mail", "note.md")
    records = [
        _prompt(now - 560, "write the parser the board names, and leave the architect a note"),
        *_response(now - 550, "msg-1", "tool_use", R1, _text("writing the parser"),
                   _call("call-1", "Write", file_path=parser, content="x = 1\n")),
        _result(now - 545, "call-1", "File created successfully"),
        *_response(now - 540, "msg-2", "tool_use", R2, _call("call-2", "Edit", file_path=note, old_string="a",
                                                              new_string="b")),
        _result(now - 535, "call-2", "The file has been updated"),
        *_response(now - 530, "msg-3", "end_turn", R3, _text("the parser is written")),
        # Only a coordination file, named relative to the project.
        _prompt(now - 420, "move the parser slice on the board"),
        *_response(now - 410, "msg-4", "tool_use", R4,
                   _call("call-4", "Edit", file_path=ntpath.join(".ao", "board.md"), old_string="a", new_string="b")),
        _result(now - 405, "call-4", "The file has been updated"),
        *_response(now - 400, "msg-5", "end_turn", R5, _text("the parser slice is verified on the board")),
    ]
    cfg, _ = _world(project, monkeypatch, tmp_path, records=records)

    costs = A.turn_costs(cfg)

    assert [(turn["cls"], turn["product_writes"], turn["coord_writes"]) for turn in costs["turns"]] == [
        ("product", 1, 1), ("coordination", 0, 1)]


def _stat_on(platform, **moved):
    """`os` as the marker read sees it on `platform`, where a path's stat differs from a handle's by `moved`."""
    def lstat(path, *args, **kwargs):
        value = os.lstat(path, *args, **kwargs)
        fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
        return SimpleNamespace(**{name: field + moved.get(name, 0) for name, field in fields.items()})

    return _Instead(os, name=platform, lstat=lstat)


def _read(read, root, monkeypatch, platform, **moved):
    with monkeypatch.context() as patch:
        patch.setattr(cli, "os", _stat_on(platform, **moved))
        return read(root)


def test_a_marker_whose_path_and_handle_read_two_clocks_is_the_marker_on_windows_and_changed_elsewhere(
        tmp_path, monkeypatch):
    (tmp_path / cli.PROJECT_MARKER).write_bytes(cli.PROJECT_MARKER_BYTES)
    read = cli._worktree_project_marker_document

    on_windows = _read(read, str(tmp_path), monkeypatch, "nt", st_ctime_ns=-CREATED_SOONER_NS)
    elsewhere = _read(read, str(tmp_path), monkeypatch, "posix", st_ctime_ns=-CREATED_SOONER_NS)

    assert on_windows["problem"] is None and on_windows["fingerprint"][2] == cli.PROJECT_MARKER_BYTES
    # Everywhere else a path and a handle read one clock, and two readings that differ are a change.
    assert elsewhere["problem"] == f"{cli.PROJECT_MARKER} changed before it could be read"


@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns"])
def test_on_windows_a_marker_whose_path_and_handle_disagree_on_anything_else_is_refused(tmp_path, monkeypatch,
                                                                                        field):
    (tmp_path / cli.PROJECT_MARKER).write_bytes(cli.PROJECT_MARKER_BYTES)

    document = _read(cli._worktree_project_marker_document, str(tmp_path), monkeypatch, "nt",
                     **{"st_ctime_ns": -CREATED_SOONER_NS, field: 1})

    assert document["problem"] == f"{cli.PROJECT_MARKER} changed before it could be read"


def test_init_keeps_the_marker_it_writes_where_a_path_and_a_handle_read_two_clocks(tmp_path, monkeypatch, capsys):
    root = tmp_path / "two-clocks"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    read = cli._worktree_project_marker_document
    monkeypatch.setattr(cli, "_worktree_project_marker_document",
                        lambda path: _read(read, path, monkeypatch, "nt", st_ctime_ns=-CREATED_SOONER_NS))
    monkeypatch.setattr(cli, "_reviewer_probe", lambda cfg, timeout=cli.REVIEW_PROBE_TIMEOUT: {
        "configured": True, "ok": True, "route": "fixture-reviewer", "binary": "/fixture/reviewer",
        "version": "fixture", "reason": "exact nonce echoed", "kind": "success"})
    args = SimpleNamespace(name=None, profile="claude-kiro", implementer=None, model=None, effort=None,
                           reviewer_model=None, agent=None, no_mcp=True, rules=False, watchdog=False)

    assert cli.cmd_init({"root": str(root)}, args) == 0, capsys.readouterr().out

    assert (root / cli.PROJECT_MARKER).read_bytes() == cli.PROJECT_MARKER_BYTES
    assert (root / ".ao" / "config.json").exists()
