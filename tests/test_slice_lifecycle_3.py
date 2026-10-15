"""Review found substring section matches, stale waits and raw slice output (SLICE-LIFECYCLE-3).

An entry must reach the section its ledger names, a start must end its old wait, and text
from a board, move or review must be shown escaped wherever the slice commands print it.
"""
import os

import pytest

from ao import cli, lib as A, storage
from tests.test_slice_lifecycle import BOARD, _board, _moves, _review, _slice, _where


@pytest.mark.parametrize("state", ["inbox", "queued"])
@pytest.mark.parametrize("decoy", ["- [OLD] mentions ## {state}",
                                   "- [OLD] title · note: ## {state}",
                                   "### {state}", "## {state}-later"])
def test_append_uses_the_heading_line_not_text_before_it(project, state, decoy):
    root = project["root"]
    before = f"# Board\n## running\n{decoy.format(state=state)}\n\n## {state}\n- [FIRST] first\n"
    _board(root, before)
    with A.BoardEdit(root, hold=True) as edit:
        edit.append(state, "- [NEW] new · acceptance: it works")
        edit.write()

    assert _where(root, "NEW")[0] == state
    assert open(os.path.join(root, ".ao", "board.md"), encoding="utf-8").read() == before.replace(
        f"\n## {state}\n", f"\n## {state}\n- [NEW] new · acceptance: it works\n")
    (move,) = _moves(root)
    assert (move["slice"], move["from"], move["to"]) == ("NEW", None, state)


@pytest.mark.parametrize("decoy", ["", "- [OLD] mentions ## queued", "### queued", "## queued-later"])
def test_append_refuses_a_missing_heading_without_changing_or_recording_anything(project, decoy):
    root = project["root"]
    before = f"# Board\n## running\n{decoy}\n"
    _board(root, before)
    with A.BoardEdit(root, hold=True) as edit:
        with pytest.raises(ValueError, match="no ## queued heading.*add.*heading.*run it again"):
            edit.append("queued", "- [NEW] new")
        assert not edit.changed and not edit.moves
        assert edit.write() is False
    assert open(os.path.join(root, ".ao", "board.md"), encoding="utf-8").read() == before
    assert not os.path.exists(A.slice_ledger_path(root))


@pytest.mark.parametrize("heading", ["## queued", "  ##\tqueued  "])
@pytest.mark.parametrize("ending", ["", "\n"])
def test_append_and_move_share_the_readers_heading_rules_at_end_of_file(project, heading, ending):
    root = project["root"]
    _board(root, f"# Board\n## running\n- [OLD] old\n{heading}{ending}")
    with A.BoardEdit(root, hold=True) as edit:
        edit.append("queued", "- [NEW] new")
        assert edit.move("OLD", "queued")
        edit.write()
    assert [item["id"] for item in A.board(root)["queued"]] == ["OLD", "NEW"]
    assert all(row["to"] == "queued" for row in _moves(root))


def test_board_add_says_how_to_repair_a_missing_section(project, monkeypatch, capsys):
    root = project["root"]
    _board(root, "# Board\n## running\n### queued\n")
    monkeypatch.delenv("AO_ROLE", raising=False)
    assert cli.main(["-C", root, "board", "add", "NEW", "new", "--acceptance", "it works"]) == 1
    said = capsys.readouterr().err
    assert "no ## queued heading" in said and "run it again" in said
    assert not os.path.exists(A.slice_ledger_path(root))


@pytest.mark.parametrize("state", ["queued", "blocked"])
@pytest.mark.parametrize("role", [None, "architect"])
def test_start_ends_the_wait_before_a_later_block(project, monkeypatch, state, role):
    root = project["root"]
    _board(root, f"# Board\n## {state}\n- [S1] work · acceptance: it works · waiting: human\n")
    assert _slice(project, monkeypatch, role, "start", "S1") == 0
    current, item = _where(root, "S1")
    assert current == "running" and "waiting" not in item["notes"]
    (move,) = _moves(root)
    assert move["notes"]["waiting"] is None and move["replaced"]["waiting"] == "human"
    assert _slice(project, monkeypatch, role, "block", "the service is down") == 0
    assert "waiting" not in _where(root, "S1")[1]["notes"]
    assert A.human_waits(root) == []


@pytest.mark.parametrize("action", ["start", "block", "blocked", "abandon", "status", "slices", "gone"])
def test_slice_commands_show_board_and_move_text_escaped(project, monkeypatch, capsys, action):
    root = project["root"]
    title, reason = "title\x1b[2J\u202e", "reason\x1b[2J\u202e"
    key, value = "note\u202e", "value\x1b[2J\u202e"
    state = "queued" if action == "start" else "blocked" if action in ("blocked", "slices") else "running"
    _board(root, f"# Board\n## {state}\n- [S1] {title} · {key}: {value} · needs: {reason}\n")
    if action == "gone":
        A.record_slice_move(root, "S1", "queued", "running", title=title, why=reason)
        _board(root, "# Board\n## running\n")
        argv, expected = ("slices",), (title,)
    elif action in ("status", "slices"):
        A.record_slice_move(root, "S1", "queued", state, title=title, why=reason)
        argv = ("status", "S1") if action == "status" else ("slices",)
        expected = (title, reason, key, value) if action == "status" else (title, reason)
    elif action == "blocked":
        argv, expected = ("block", "again", "--slice", "S1"), (reason,)
    elif action == "start":
        argv, expected = ("start", "S1", "--why", reason), (title, reason)
    elif action == "abandon":
        argv, expected = ("abandon", "--why", reason), (title,)
    else:
        argv, expected = ("block", reason), (title, reason)
    assert _slice(project, monkeypatch, None, *argv) == 0
    said = capsys.readouterr().out
    for text in expected:
        assert text not in said and A.shown_text(text) in said


def test_slice_status_shows_findings_since_and_void_reasons_escaped(project, monkeypatch, capsys):
    root = project["root"]
    finding, since, reason = "finding\u202e", "date\u202e", "void\u202e"
    _board(root, BOARD.replace("the store ·", f"the store · since: {since} ·"))
    _review(root, "NEEDS_CHANGES", "S1", [("HIGH", finding)])
    move = A.record_slice_move(root, "S1", "queued", "running")
    storage.append_chained_jsonl(A.slice_ledger_path(root),
                                {"slice": "S1", "voids": move["ordinal"], "why": reason}, A.SLICE_CHAIN)
    assert _slice(project, monkeypatch, None, "status", "S1") == 0
    said = capsys.readouterr().out
    for text in (finding, since, reason):
        assert text not in said and A.shown_text(text) in said
