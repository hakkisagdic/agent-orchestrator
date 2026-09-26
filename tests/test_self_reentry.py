"""A child that re-enters ao as `-m ao` is handed the path this ao was imported from (SELF-REENTRY).

Three places start such a child: the review runner, the watchdog's hunt and the MCP server's verify.
Each ran `sys.executable -m ao` and said nothing about where ao lives, so from a clone - ao's own
documented quickstart, a symlink to `bin/ao` on PATH with nothing installed - the child died on "No
module named ao". The review died silently: its log held that one line from the interpreter, and the
review stood at "running" until it was declared lost 34 minutes later.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

import ao
from ao import lib as A

PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(ao.__file__)))
SPAWNS = ("src/ao/parts/cli_review.py", "src/ao/watchdog.py", "src/ao/mcp.py")

def _call_around(source, at):
    """The whole `subprocess.…(…)` statement holding the position `at`, parentheses balanced.

    Reading to the first `)` was not enough: one call closes a parenthesis of its own before the
    keyword arguments, and the guard passed a spawn that had none.
    """
    start, depth = source.rindex("subprocess.", 0, at), 0
    for end in range(start, len(source)):
        depth += (source[end] == "(") - (source[end] == ")")
        if depth == 0 and source[end] == ")":
            return source[start:end + 1]
    return source[start:]



def test_the_child_environment_names_the_directory_this_ao_was_imported_from():
    env = A.self_child_env({"PATH": "/usr/bin"})

    assert env["PYTHONPATH"].split(os.pathsep)[0] == PACKAGE_PARENT
    assert env["PATH"] == "/usr/bin"          # it adds one name and carries everything else


def test_a_path_already_there_is_kept_after_it_and_never_twice():
    env = A.self_child_env({"PYTHONPATH": os.pathsep.join(["/other", PACKAGE_PARENT])})

    assert env["PYTHONPATH"].split(os.pathsep) == [PACKAGE_PARENT, "/other"]


def _an_interpreter_that_cannot_import_ao():
    """A python on PATH that does not have ao, or None: the shape of a clone with nothing installed.

    `-I` was tried first and proves nothing: isolated mode ignores PYTHONPATH, and the interpreter
    running the suite finds ao through its own environment whatever the child is handed.
    """
    for name in ("python3", "python3.9", "python3.10", "python3.11", "python3.12", "python3.13", "python3.14"):
        exe = shutil.which(name)
        if not exe or os.path.realpath(exe) == os.path.realpath(sys.executable):
            continue
        if subprocess.run([exe, "-c", "import ao"], capture_output=True).returncode != 0:
            return exe
    return None


def test_an_interpreter_without_ao_imports_it_when_handed_this_environment():
    """The clone case, run: an interpreter that has no ao is given the environment and finds it."""
    exe = _an_interpreter_that_cannot_import_ao()
    if exe is None:
        pytest.skip("every python on PATH already imports ao")
    code = "import ao, sys; sys.stdout.write(ao.__file__)"
    scrubbed = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    without = subprocess.run([exe, "-c", code], capture_output=True, text=True, env=scrubbed)
    with_env = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                              env=A.self_child_env(scrubbed))

    assert without.returncode != 0 and "No module named" in without.stderr
    assert with_env.returncode == 0
    assert os.path.realpath(with_env.stdout.strip()) == os.path.realpath(ao.__file__)


def test_every_place_that_re_enters_ao_hands_the_child_that_environment():
    """A spawn of `-m ao` without it is the bug this closed, so each place is named and read."""
    root = pathlib.Path(A.REPO)
    seen = 0
    for rel in SPAWNS:
        source = (root / rel).read_text(encoding="utf-8")
        for spawn in re.finditer(r'\[sys\.executable, "-m", "ao"', source):
            assert "self_child_env(" in _call_around(source, spawn.start()), \
                f"{rel} starts ao without the child environment"
            seen += 1
    assert seen == len(SPAWNS)
