import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import cli, lib as A
from tests.test_review_chain import _fake, _repo_with_change

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the filter here is a POSIX shell script named git")

MANGLED = "1 file changed (compressed)"
APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")


def _filter_in_front_of_git(tmp_path, monkeypatch):
    """A token-saving proxy installed as `git` on PATH: whatever is asked, a short summary."""
    shim = tmp_path / "filter"
    shim.mkdir()
    script = shim / "git"
    script.write_text(f"#!/bin/sh\necho '{MANGLED}'\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.delenv("AO_GIT", raising=False)
    monkeypatch.setenv("PATH", str(shim) + os.pathsep + os.environ.get("PATH", ""))
    return str(script)


def test_a_verification_under_a_mangling_filter_records_the_true_numbers(project, tmp_path, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    for i in range(3):
        with open(os.path.join(root, "src", f"m{i}.py"), "w", encoding="utf-8") as fh:
            fh.write("value = 1\n")
    subprocess.run(["git", "add", "src"], cwd=root, check=True)
    spec = {"gates": {"test": {"run": shlex.join([sys.executable, "-c", "print('ok')"]), "timeout": 30}},
            "profiles": {"quick": ["test"]}, "default_profile": "quick"}
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()
    script = _filter_in_front_of_git(tmp_path, monkeypatch)
    # The filter does stand in front of git for anything that asks the shell for it.
    asked = subprocess.run("git rev-parse --short HEAD", shell=True, cwd=root, capture_output=True, text=True)
    assert asked.stdout.strip() == MANGLED

    cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0))

    record = A.latest_verification(root)
    assert record["candidate"]["changed_paths"] == ["src/m0.py", "src/m1.py", "src/m2.py"]
    assert record["head"] == head
    assert record["measured_by"]["git"] != script and record["measured_by"]["candidate_via_shell"] is False


def test_a_review_under_the_filter_carries_the_true_candidate_and_how_it_was_measured(project, tmp_path,
                                                                                      monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    truth = A.index_candidate(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})
    script = _filter_in_front_of_git(tmp_path, monkeypatch)

    args = SimpleNamespace(action=None, rid=None, any=False, run=None, boundary="b", paths=None, commits=None,
                           timeout=None)
    assert cli.cmd_review(cfg, args) == 0

    _, verdict, _, evidence = A.latest_candidate_review(root, "semantic-review", truth["digest"])
    assert verdict == "APPROVED" and evidence["candidate"]["changed_paths"] == ["src/a.py"]
    assert evidence["measured_by"]["git"] not in (script, "git")


def test_the_doctor_names_a_script_in_front_of_git_and_a_hook_that_rewrites_commands(project, tmp_path,
                                                                                     monkeypatch):
    root = project["root"]
    script = _filter_in_front_of_git(tmp_path, monkeypatch)
    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
    with open(os.path.join(root, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "rtk hook claude"},
            {"type": "command", "command": "hooks/identity-guard.sh"}]}]}}, fh)

    text = "\n".join(cli._measurement_lines(project))

    assert "2 possible filter(s)" in text
    assert f"git on PATH is {script}, a script in front of git" in text
    assert "`rtk hook claude`" in text and "write-tree" in text
    assert "identity-guard" not in text
