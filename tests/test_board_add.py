"""`ao board add` admits one item in one step, and only with a written acceptance boundary (BOARD-ADD).

Work entered ao by editing `.ao/board.md` by hand, or through a tracker import. Both keep the rule that
matters - no item runs unattended without an acceptance boundary written before the work - and the
first is a file a person has to learn the shape of. The command writes the same line the import
writes, records the same plan baseline, and refuses what the import would hold back.
"""
import os

import pytest

from ao import cli, lib as A


def _project(tmp_path):
    (tmp_path / ".ao").mkdir()
    (tmp_path / ".ao" / "board.md").write_text(
        "# Board\n\n## running\n\n## blocked\n\n## queued\n- [FIRST] the first item · acceptance: it runs\n\n"
        "## inbox\n", encoding="utf-8")
    return {"root": str(tmp_path), "language": "en"}


def _add(cfg, *argv):
    args = cli.build_parser().parse_args(["board", "add", *argv])
    return cli.cmd_board(cfg, args)


def test_an_item_with_a_boundary_is_queued_and_ready(tmp_path, capsys):
    cfg = _project(tmp_path)

    assert _add(cfg, "SECOND", "the second item", "--acceptance", "the suite passes and nothing else changes") == 0
    queued = {item["id"]: item for item in A.board(cfg["root"])["queued"]}

    assert queued["SECOND"]["title"] == "the second item"
    assert queued["SECOND"]["notes"]["acceptance"] == "the suite passes and nothing else changes"
    assert "SECOND" in [item["id"] for item in A.board_graph(cfg["root"])["ready"]]


def test_an_item_without_a_boundary_is_refused_and_nothing_is_written(tmp_path, capsys):
    cfg = _project(tmp_path)
    before = (tmp_path / ".ao" / "board.md").read_text(encoding="utf-8")

    assert _add(cfg, "SECOND", "the second item") == 2
    assert "acceptance" in capsys.readouterr().out
    assert (tmp_path / ".ao" / "board.md").read_text(encoding="utf-8") == before


def test_an_id_already_on_the_board_is_refused(tmp_path, capsys):
    cfg = _project(tmp_path)

    assert _add(cfg, "FIRST", "again", "--acceptance", "anything") == 2
    assert [item["id"] for item in A.board(cfg["root"])["queued"]] == ["FIRST"]


def test_a_dependency_on_an_item_the_board_does_not_hold_is_refused(tmp_path, capsys):
    cfg = _project(tmp_path)

    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs", "--needs", "NOWHERE") == 2
    assert "NOWHERE" in capsys.readouterr().out
    assert [item["id"] for item in A.board(cfg["root"])["queued"]] == ["FIRST"]


def test_an_item_that_needs_another_waits_for_it(tmp_path):
    cfg = _project(tmp_path)

    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs", "--needs", "FIRST") == 0
    ready = [item["id"] for item in A.board_graph(cfg["root"])["ready"]]

    assert "FIRST" in ready and "SECOND" not in ready


def test_an_id_a_line_could_not_hold_is_refused(tmp_path):
    cfg = _project(tmp_path)

    for bad in ("two words", "a]b", "·dot", ""):
        assert _add(cfg, bad, "title", "--acceptance", "it runs") == 2
