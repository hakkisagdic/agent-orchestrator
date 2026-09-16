import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A

GATE = "import glob, runpy; [runpy.run_path(p) for p in sorted(glob.glob('use_*.py'))]"


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit_files(root, message, **files):
    for name, text in files.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    _git(root, "add", *files)
    _git(root, "commit", "-q", "-m", message)


def _check(project, branch):
    return cli.cmd_merge_check(project, SimpleNamespace(branch=branch, into="HEAD", profile="full", wait=0))


def test_two_green_branches_whose_merge_is_red_are_caught_and_every_merge_is_accounted_for(project, tmp_path,
                                                                                           monkeypatch):
    root = project["root"]
    command = (subprocess.list2cmdline if os.name == "nt" else shlex.join)([sys.executable, "-c", GATE])
    spec = {"gates": {"test": {"run": command, "timeout": 60}},
            "profiles": {"full": ["test"]}, "default_profile": "full"}
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    base = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _commit_files(root, "one argument", **{"lib.py": "def f(x):\n    return x\n", "use_a.py": "import lib\nlib.f(1)\n"})
    for name, files in (("caller", {"use_b.py": "import lib\nlib.f(3)\n"}), ("docs", {"notes.txt": "notes\n"}),
                        ("other", {"other.txt": "other\n"})):
        _git(root, "checkout", "-q", "-b", name, base)
        _commit_files(root, name, **files)
    _git(root, "checkout", "-q", "-b", "sig", base)
    _commit_files(root, "two arguments", **{"lib.py": "def f(x, y):\n    return x + y\n",
                                            "use_a.py": "import lib\nlib.f(1, 2)\n"})
    _git(root, "checkout", "-q", base)
    _git(root, "merge", "-q", "--ff-only", "sig")

    assert _check(project, "docs") == 0
    _git(root, "merge", "-q", "--no-ff", "--no-edit", "docs")
    verified = _git(root, "rev-parse", "HEAD")
    assert _check(project, "caller") == 1          # green alone, red merged: f now takes two arguments
    red = A.merge_checks(root)[-1]
    assert red["passed"] is False and red["into"] == verified and red["branch"] == _git(root, "rev-parse", "caller")
    _git(root, "merge", "-q", "--no-ff", "--no-edit", "caller")
    failed = _git(root, "rev-parse", "HEAD")
    assert _git(root, "rev-parse", "HEAD^{tree}") == red["tree"]
    _git(root, "merge", "-q", "--no-ff", "--no-edit", "other")
    unchecked = _git(root, "rev-parse", "HEAD")

    assert A.unverified_merges(root, project) == [
        (unchecked, "no run of its merge result was recorded"),
        (failed, f"the recorded run of its merge result, {red['id']}, failed"),
    ]
    assert len(_git(root, "worktree", "list").splitlines()) == 1


def test_a_merge_that_does_not_apply_is_recorded_as_a_conflict_without_running_gates(project, tmp_path, monkeypatch):
    root = project["root"]
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump({"gates": {"test": {"run": "exit 1"}}, "profiles": {"full": ["test"]}}, fh)
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    base = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _commit_files(root, "base", **{"a.txt": "one\n"})
    _git(root, "checkout", "-q", "-b", "left")
    _commit_files(root, "left", **{"a.txt": "left\n"})
    _git(root, "checkout", "-q", base)
    _commit_files(root, "right", **{"a.txt": "right\n"})

    assert _check(project, "left") == 1

    row = A.merge_checks(root)[-1]
    assert row["conflict"] == "conflicts in a.txt" and row["gates"] == [] and row["tree"] is None
