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


def test_an_id_a_line_could_not_hold_or_a_needs_note_name_is_refused(tmp_path, capsys):
    cfg = _project(tmp_path)
    before = (tmp_path / ".ao" / "board.md").read_text(encoding="utf-8")

    for bad in ("two words", "a]b", "·dot", "", "a,b", "(later)", "/x", "a\\b"):
        assert _add(cfg, bad, "title", "--acceptance", "it runs") == 2
        assert "cannot be an id" in capsys.readouterr().out
    assert (tmp_path / ".ao" / "board.md").read_text(encoding="utf-8") == before


# ---- BOARD-ADD-2 --------------------------------------------------------------------------------

def test_a_phase_id_is_admitted_and_baselined_on_its_plan(tmp_path):
    """An id the board reads between its brackets is one `ao board add` takes: ACME-187/1 is a phase of
    the plan ACME-187, as docs/sources.md has each phase enter the board."""
    cfg = _project(tmp_path)
    (tmp_path / ".ao" / "plans").mkdir()
    (tmp_path / ".ao" / "plans" / "ACME-187.md").write_text("# ACME-187\n1. route table extraction\n",
                                                          encoding="utf-8")

    assert _add(cfg, "ACME-187/1", "route table extraction", "--acceptance", "the table is extracted") == 0

    assert sorted(item["id"] for item in A.board(cfg["root"])["queued"]) == ["ACME-187/1", "FIRST"]
    assert A.plan_baseline(cfg["root"]) == {"ACME-187/1": A.plan_digest(cfg["root"], "ACME-187/1")}
    assert A.plan_digest(cfg["root"], "ACME-187/1").startswith("sha256:")


def test_the_plan_as_admitted_is_recorded_and_an_edit_after_it_is_drift(tmp_path):
    cfg = _project(tmp_path)
    plan = tmp_path / ".ao" / "plans" / "SECOND.md"
    plan.parent.mkdir()
    plan.write_text("# SECOND\nthe plan\n", encoding="utf-8")

    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs") == 0
    assert A.plan_baseline(cfg["root"])["SECOND"] == A.plan_digest(cfg["root"], "SECOND")
    assert A.plan_drift(cfg["root"]) == []

    plan.write_text("# SECOND\nthe plan, changed after admission\n", encoding="utf-8")
    assert A.plan_drift(cfg["root"]) == ["SECOND"]


def test_an_item_without_a_plan_records_no_baseline(tmp_path):
    cfg = _project(tmp_path)

    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs") == 0
    assert A.plan_baseline(cfg["root"]) == {}


def test_a_dot_that_would_forge_a_note_is_refused_and_nothing_is_written(tmp_path, capsys):
    """A '·' separates a board line's notes, so `--acceptance "x · needs: Y"` would write a dependency
    nobody checked."""
    cfg = _project(tmp_path)
    before = (tmp_path / ".ao" / "board.md").read_text(encoding="utf-8")

    for argv in (("SECOND", "a title · needs: FIRST", "--acceptance", "it runs"),
                 ("SECOND", "a title", "--acceptance", "it runs · needs: NOWHERE"),
                 ("SECOND", "a title", "--acceptance", "it runs", "--role", "architect · needs: FIRST")):
        assert _add(cfg, *argv) == 2
        assert "no '·'" in capsys.readouterr().out
    assert (tmp_path / ".ao" / "board.md").read_text(encoding="utf-8") == before


def test_needs_are_read_as_the_board_reads_them(tmp_path, capsys):
    """`--needs "ACME 1"` passed as one held id while the line it wrote read as two ids the board lacks."""
    cfg = _project(tmp_path)
    board = tmp_path / ".ao" / "board.md"
    board.write_text(board.read_text(encoding="utf-8").replace(
        "- [FIRST]", "- [ACME 1] written by hand · acceptance: y\n- [FIRST]"), encoding="utf-8")
    before = board.read_text(encoding="utf-8")

    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs", "--needs", "ACME 1") == 2
    assert "it needs ACME, 1, which the board does not hold" in capsys.readouterr().out
    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs", "--needs", "(FIRST)") == 2
    assert "`(FIRST)`, which a needs note would not read as an id" in capsys.readouterr().out
    assert board.read_text(encoding="utf-8") == before

    assert _add(cfg, "THIRD", "a third item", "--acceptance", "it runs") == 0
    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs", "--needs", "FIRST THIRD") == 0
    second = next(item for item in A.board(cfg["root"])["queued"] if item["id"] == "SECOND")
    assert second["notes"]["needs"] == "FIRST, THIRD"
    assert A.board_graph(cfg["root"])["problems"] == []


def test_a_role_is_written_on_the_line(tmp_path):
    cfg = _project(tmp_path)

    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs", "--role", "architect") == 0
    second = next(item for item in A.board(cfg["root"])["queued"] if item["id"] == "SECOND")
    assert second["notes"] == {"acceptance": "it runs", "role": "architect"}


def test_without_a_board_file_nothing_is_written(tmp_path, capsys):
    (tmp_path / ".ao").mkdir()
    cfg = {"root": str(tmp_path), "language": "en"}

    assert _add(cfg, "SECOND", "the second item", "--acceptance", "it runs") == 2
    assert "no .ao/board.md" in capsys.readouterr().out
    assert not (tmp_path / ".ao" / "board.md").exists()


def test_a_new_board_says_that_ao_board_add_writes_to_it(tmp_path):
    from ao import language
    for lang in ("en", "tr"):
        text = language.text({"language": lang}, "init.board")
        assert "`ao board add`" in text and "only reads" not in text and "yalnız okur" not in text
