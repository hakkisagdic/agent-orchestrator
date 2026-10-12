"""A hunt goes through every line of every tracked file up to hunter.max_file_kb, a piece a hunt where a file is
larger than one hunt, and names a file past that size it passes over (BUG-HUNTER-3).

The hunter's first run on ao's own tree, on 2026-10-12, read eight files, and how it chose them had three faults.
A file that did not fit what was left of a hunt's budget was passed over: the cursor had already gone on beyond it,
so the same file could be missed on every round. A file larger than a whole hunt was read to its first
`hunter.bytes_per_run` and never further - ao's largest files are three and four times that, so most of
cli_review.py and watchdog.py was never read. And a tree in which nothing could be read as text kept the hunt
going round for ever. The cursor also named a place in `git ls-files`, so a file added above it moved another
file into its place.
"""
import json
import os
import re
import subprocess
import sys
import threading
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_bug_hunter import _stand_in

# A hunter that keeps its prompt and finds nothing: an answer, so the hunt counts as run.
SAVE_PROMPT = ("import os, sys\nopen(os.environ['AO_TEST_PROMPT'], 'w', encoding='utf-8').write(sys.argv[1])\n"
               "print('nothing here')\n")


def _track(root, files):
    for path, text in files.items():
        full = os.path.join(root, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(text if isinstance(text, bytes) else text.encode("utf-8"))
    subprocess.run(["git", "add", "--", *files], cwd=root, check=True)


def _untrack(root, *paths):
    subprocess.run(["git", "rm", "-q", "--cached", "--", *paths], cwd=root, check=True)
    for path in paths:
        os.remove(os.path.join(root, *path.split("/")))


def _definitions(count):
    """A module of `count` functions: a definition, a line of body and a blank line each."""
    return "".join(f"def f{n}():\n    return {'x' * 60!r}\n\n" for n in range(count))


def _hunts(root, cfg, times):
    """What `times` hunts read, in order: [(path, first line, last line, lines in the file, text)]."""
    read = []
    for _ in range(times):
        pieces, _ = A.hunt_slice(root, cfg)
        read += [(path, first, last, total, text) for path, text, first, last, total in pieces]
    return read


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def test_a_file_larger_than_a_hunt_is_read_a_piece_a_hunt_each_line_once_each_piece_a_whole_definition(project):
    root = project["root"]
    source = _definitions(200)
    lines = source.splitlines(keepends=True)
    _track(root, {"src/big.py": source})
    cfg = dict(project, hunter={"bytes_per_run": 2000, "files_per_run": 8})

    pieces = [piece for piece in _hunts(root, cfg, 30) if piece[0] == "src/big.py"]
    round_one = pieces[:next(at for at, piece in enumerate(pieces) if piece[2] == len(lines)) + 1]

    assert len(round_one) > 5 and all(piece[3] == len(lines) for piece in round_one)
    assert [piece[1] for piece in round_one] == [1] + [piece[2] + 1 for piece in round_one[:-1]]
    assert "".join(piece[4] for piece in round_one) == source
    assert all(len(piece[4].encode("utf-8")) <= 2000 for piece in round_one)
    assert all(lines[piece[1] - 1].startswith("def ") for piece in round_one)
    assert pieces[len(round_one)][1] == 1                                   # and the next round starts it again


def test_a_file_that_does_not_fit_what_is_left_of_a_hunt_starts_the_next_one(project):
    root = project["root"]
    _track(root, {"src/a.py": "a = 1\n" * 100, "src/b.py": "b = 2\n" * 300, "src/c.py": "c = 3\n" * 10})
    cfg = dict(project, hunter={"bytes_per_run": 2000, "files_per_run": 8})

    first, second = A.hunt_slice(root, cfg)[0], A.hunt_slice(root, cfg)[0]

    assert [piece[0] for piece in first] == [".ao-project", "src/a.py"]    # b.py's 1800 bytes did not fit after
    assert [piece[0] for piece in second][:2] == ["src/b.py", "src/c.py"]   # it was passed over until now


def test_the_cursor_keeps_the_file_it_stopped_before_by_its_name(project):
    root = project["root"]
    _track(root, {"src/m.py": "m = 1\n" * 200, "src/y.py": "y = 1\n" * 300, "src/z.py": "z = 1\n"})
    cfg = dict(project, hunter={"bytes_per_run": 2000, "files_per_run": 8})
    assert [piece[0] for piece in A.hunt_slice(root, cfg)[0]] == [".ao-project", "src/m.py"]

    _track(root, {"src/a.py": "a = 1\n", "src/b.py": "b = 1\n"})            # two files above where it stopped
    assert A.hunt_slice(root, cfg)[0][0][0] == "src/y.py"

    with open(os.path.join(root, ".ao", "hunter.json"), "w", encoding="utf-8") as fh:
        json.dump({"cursor": 0, "path": "src/x.py", "line": 7}, fh)          # a file gone: the one after it
    assert A.hunt_slice(root, cfg)[0][0][:3] == ("src/y.py", "y = 1\n" * 300, 1)


def test_a_cursor_written_before_it_is_read_as_the_place_in_the_list_it_was(project):
    root = project["root"]
    _track(root, {"src/a.py": "a = 1\n", "src/b.py": "b = 1\n"})
    with open(os.path.join(root, ".ao", "hunter.json"), "w", encoding="utf-8") as fh:
        json.dump({"cursor": 2}, fh)

    assert A.hunt_slice(root, dict(project, hunter={"files_per_run": 1}))[0][0][0] == "src/b.py"


def test_a_tree_with_nothing_a_hunt_can_read_ends_the_hunt(project):
    root = project["root"]
    _track(root, {"assets/a.bin": b"\xff\xfe\x00\x81" * 64, "assets/b.bin": b"\x80\x81" * 64})
    _untrack(root, ".ao-project")
    found = []
    hunt = threading.Thread(target=lambda: found.append(A.hunt_slice(root, project)), daemon=True)

    hunt.start()
    hunt.join(30)

    assert not hunt.is_alive(), "the hunt went round the tree for ever"
    assert found[0][0] == []


def test_a_line_longer_than_a_whole_hunt_is_cut_to_it_and_the_next_hunt_goes_on(project):
    root = project["root"]
    _track(root, {"src/long.js": "x" * 5000 + "\n", "src/next.py": "n = 1\n"})
    cfg = dict(project, hunter={"bytes_per_run": 1000, "files_per_run": 8})

    read = _hunts(root, cfg, 3)

    (long,) = [piece for piece in read if piece[0] == "src/long.js"]
    assert long[1:4] == (1, 1, 1) and long[4] == "x" * 1000
    assert "src/next.py" in [piece[0] for piece in read]


def test_a_file_past_max_file_kb_is_passed_over_and_said(project, monkeypatch, capsys, tmp_path):
    root = project["root"]
    _track(root, {"src/a.py": "def f():\n    return 1\n", "vendor/bundle.js": "x = 1;\n" * 400})
    _stand_in(monkeypatch)
    monkeypatch.setenv("AO_TEST_PROMPT", str(tmp_path / "prompt.txt"))
    cfg = dict(project, hunter={"max_file_kb": 2, "id": "h1",
                                "argv": [sys.executable, "-B", "-c", SAVE_PROMPT, "{prompt}"]})

    assert cli.cmd_hunt(cfg, SimpleNamespace(action="run", fingerprint=None)) == 0

    assert "passed over 1 file(s) larger than hunter.max_file_kb (2 KB): vendor/bundle.js" in _plain(capsys)
    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert "--- src/a.py ---" in prompt and "bundle.js" not in prompt


def test_a_piece_is_headed_with_the_lines_it_holds_in_the_project_s_language(project, monkeypatch, tmp_path):
    root = project["root"]
    source = _definitions(100)
    total = len(source.splitlines())
    _track(root, {"src/big.py": source})
    _stand_in(monkeypatch)
    kept = tmp_path / "prompt.txt"
    monkeypatch.setenv("AO_TEST_PROMPT", str(kept))
    argv = [sys.executable, "-B", "-c", SAVE_PROMPT, "{prompt}"]
    heads = {}
    for lang in ("en", "tr"):
        cfg = dict(project, language=lang, hunter={"id": "h1", "argv": argv, "bytes_per_run": 2000})
        with open(os.path.join(root, ".ao", "hunter.json"), "w", encoding="utf-8") as fh:
            json.dump({"cursor": 1, "path": "src/big.py", "line": 1}, fh)
        assert cli.cmd_hunt(cfg, SimpleNamespace(action="run", fingerprint=None)) == 0
        heads[lang] = re.findall(r"^--- (.+) ---$", kept.read_text(encoding="utf-8"), re.M)

    (run,) = [row for row in A.hunter_rows(root) if row.get("event") == "run"][-1:]
    (path, first, last, of) = run["parts"][0]
    assert (path, first, of) == ("src/big.py", 1, total) and 1 < last < total
    assert heads["en"] == [f"src/big.py (lines 1 to {last} of {total}; the first line below is line 1)"]
    assert heads["tr"] == [f"src/big.py (satır 1-{last} / {total}; aşağıdaki ilk satır 1. satırdır)"]
