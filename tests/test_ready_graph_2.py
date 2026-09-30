"""What the retrospective review of READY-GRAPH found that still held (READY-GRAPH-2).

An id listed twice and an item that unlocks what is not on the board were named as problems and still
returned READY, where the docstring said no item a problem touches is. A multi-word remark in `needs:`
left its words as the ids an item waited for. And the doctor said nothing when the board could not be
read as a graph at all.
"""
from ao import cli, lib as A
from tests.test_ready_graph import _board

EMPTY = "# Board\n\n## running\n\n## blocked\n\n## queued\n{queued}\n## verified\n\n## done\n{done}"


def _ready(root):
    return [item["id"] for item in A.ready(root)]


def test_an_id_on_the_board_twice_is_not_ready(project):
    _board(project["root"], EMPTY.format(queued="- [X] one\n- [X] two\n- [Y] clear\n", done=""))

    assert _ready(project["root"]) == ["Y"]
    assert "X is on the board twice, under queued and queued" in A.board_graph(project["root"])["problems"]


def test_an_item_that_unlocks_what_is_not_on_the_board_is_not_ready(project):
    _board(project["root"], EMPTY.format(queued="- [Q] q · needs: A · unlocks: GHOST\n", done="- [A] a\n"))

    assert _ready(project["root"]) == []


def test_a_remark_in_needs_names_no_id_however_many_words_it_has(project):
    _board(project["root"], EMPTY.format(queued="- [J] j · needs: A (see ticket 12)\n- [L] l · needs: A (in review\n",
                                         done="- [A] a\n"))

    assert _ready(project["root"]) == ["J", "L"] and A.board_graph(project["root"])["problems"] == []


def test_the_doctor_says_when_the_board_cannot_be_read_as_a_graph(project, monkeypatch, tmp_path):
    from ao import email, telegram
    monkeypatch.setattr(email, "CONF", str(tmp_path / "no-email.json"))
    monkeypatch.setattr(telegram, "CONF", str(tmp_path / "no-telegram.json"))

    def unreadable(root):
        raise OSError("board.md is a directory")

    monkeypatch.setattr(A, "board_graph", unreadable)

    assert dict(cli.doctor_problems(project))["board-graph"].startswith(
        "the board cannot be read as a graph (board.md is a directory)")
