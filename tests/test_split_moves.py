import os
import subprocess
from types import SimpleNamespace

from ao import cli, lib as A

LIB = '"""a module"""\nimport os\n\nX = 1\n\n\ndef a():\n    return X\n\n\ndef b(n):\n    return n + 1\n\n\ndef c():\n    return b(1)\n'


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(root):
    with open(os.path.join(root, "mod.py"), "w", encoding="utf-8") as fh:
        fh.write(LIB)
    _git(root, "add", "mod.py")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "mod")


def _stage(root, module, part):
    with open(os.path.join(root, "mod.py"), "w", encoding="utf-8") as fh:
        fh.write(module)
    os.makedirs(os.path.join(root, "parts"), exist_ok=True)
    with open(os.path.join(root, "parts", "mod_b.py"), "w", encoding="utf-8") as fh:
        fh.write(part)
    _git(root, "add", "mod.py", "parts/mod_b.py")


def test_a_definition_moved_byte_for_byte_is_a_pure_move(project):
    root = project["root"]
    _repo(root)
    _stage(root, LIB.replace('def b(n):\n    return n + 1\n\n\n', '_part("mod_b", globals())\n\n\n'),
           '"""b, moved"""\n\n\ndef b(n):\n    return n + 1\n')

    result = A.split_moves(root)

    assert result == {"moved": [("b", "mod.py", "parts/mod_b.py")], "problems": []}


def test_a_move_that_edits_loses_adds_or_forgets_to_load_is_refused(project):
    root = project["root"]
    _repo(root)
    module = LIB.replace('def b(n):\n    return n + 1\n\n\n', '').replace('def c():\n    return b(1)\n', 'Y = 2\n')
    _stage(root, module.replace("X = 1", "X = 3"), '\n\ndef b(n):\n    return n + 2\n')

    problems = A.split_moves(root)["problems"]

    assert "b changed on its way from mod.py to parts/mod_b.py" in problems
    assert "X changed in mod.py" in problems
    assert "c left mod.py and arrived nowhere" in problems and "Y is new in mod.py" in problems
    assert "parts/mod_b.py is not loaded by any _part call" in problems


def test_a_part_runs_in_the_namespace_of_the_module_it_came_from(tmp_path, monkeypatch):
    (tmp_path / "shared.py").write_text("def twice():\n    return helper() * 2\n", encoding="utf-8")
    monkeypatch.setattr(A, "_PARTS_DIR", str(tmp_path))
    namespace = {"helper": lambda: 21}

    A._part("shared", namespace)

    assert namespace["twice"]() == 42 and namespace["twice"].__globals__ is namespace
    namespace["helper"] = lambda: 5
    assert namespace["twice"]() == 10


def test_a_move_only_slice_cannot_land_a_candidate_that_is_not_a_pure_move(project, capsys):
    root = project["root"]
    _repo(root)
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read().replace("## running\n", "## running\n- [SPLIT-B] move b · move-only\n")
    open(board, "w", encoding="utf-8").write(text)
    _stage(root, LIB.replace('def b(n):\n    return n + 1\n\n\n', '_part("mod_b", globals())\n\n\n'),
           'def b(n):\n    return n * 2\n')

    assert cli.cmd_commit_ok(project, SimpleNamespace(review=None, verify=False, profile=None)) == 1
    out = capsys.readouterr().out
    assert "SPLIT-B is move-only: b changed on its way from mod.py to parts/mod_b.py" in out
    assert cli.cmd_split_check(project, SimpleNamespace()) == 1
