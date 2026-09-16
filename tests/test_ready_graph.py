import os
import re
from types import SimpleNamespace

from ao import cli, lib as A

BOARD = """# Board

## running

## blocked
- [H1] a human decision · needs: the owner's word

## queued
- [B] second of the chain · needs: A
- [C] third of the chain · needs: B
- [D] one end of a cycle · needs: E
- [E] the other end · needs: D
- [F] a leaf waiting on a person · waiting: human
- [G] needs a ghost · needs: Z
- [K] unlocked from the other end

## verified

## done
- [A] first of the chain · unlocks: K
"""


def _board(root, text=BOARD):
    with open(os.path.join(root, ".ao", "board.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def test_ready_is_derived_from_the_graph_and_every_broken_edge_is_named(project):
    root = project["root"]
    _board(root)

    graph = A.board_graph(root)

    assert [item["id"] for item in graph["ready"]] == ["B", "K"]
    assert graph["problems"] == ["G needs Z, which is not on the board", "a cycle: D needs E, E needs D"]


def test_a_chain_advances_one_link_as_each_item_lands(project):
    root = project["root"]
    _board(root, BOARD.replace("- [B] second of the chain · needs: A\n", "")
           .replace("## done\n", "## done\n- [B] second of the chain\n"))

    assert [item["id"] for item in A.ready(root)] == ["C", "K"]


def test_a_hand_written_ready_section_is_refused_and_ao_board_ready_prints_the_derived_set(project, capsys):
    root = project["root"]
    _board(root, BOARD + "\n## ready\n- [X] chosen by hand\n- [A] listed again\n")

    code = cli.cmd_board(project, SimpleNamespace(view="ready"))

    out = _plain(capsys)
    assert code == 1
    assert "board: the board has a hand-written READY section" in out
    assert re.findall(r"^([A-Z]\w*)  ", out, re.M) == ["B", "K"]
    assert A.board(root)["done"][0]["id"] == "A" and len(A.board(root)["done"]) == 1


def test_the_doctor_names_a_broken_board(project, monkeypatch, tmp_path):
    from ao import email, telegram
    monkeypatch.setattr(email, "CONF", str(tmp_path / "no-email.json"))
    monkeypatch.setattr(telegram, "CONF", str(tmp_path / "no-telegram.json"))
    _board(project["root"])

    problems = dict(cli.doctor_problems(project))

    assert problems["board-graph"].startswith("G needs Z, which is not on the board; a cycle: D needs E")
