import glob
import json
import os
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A

PROBE_OR_APPROVE = ("import sys; p = sys.argv[1]; "
                    "print(p.splitlines()[-1] if p.startswith('Reviewer invocation probe') else "
                    "'VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0')")
PROBE_OR_REFUSE = PROBE_OR_APPROVE.replace("APPROVED\\nBLOCKER: 0", "NEEDS_CHANGES\\nBLOCKER: 1")


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _configured(project, tmp_path, monkeypatch, script, installed=True):
    root = project["root"]
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump({"gates": {"check": {"run": shlex.join([sys.executable, "-c", "print('ok')"]), "timeout": 60}},
                   "profiles": {"quick": ["check"]}, "default_profile": "quick"}, fh)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [sys.executable, "-c", script, "{prompt}"]})
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    monkeypatch.setattr(cli, "_hook_execution_probe", lambda inv: {
        "installed": installed, "state": "installed" if installed else "not installed",
        "detail": "" if installed else "Git resolved no active pre-commit target", "exit": None})
    return A.load_config(root)


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def test_a_first_run_is_proven_end_to_end_and_leaves_nothing_behind(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    cfg = _configured(project, tmp_path, monkeypatch, PROBE_OR_APPROVE)
    before = _git(root, "status", "--porcelain")

    assert cli.cmd_prove(cfg, SimpleNamespace(no_review=False)) == 0

    out = _plain(capsys)
    for claim in ("hook refuses an unauthorised commit", "reviewer answers and is another actor",
                  "a throwaway slice lands end to end"):
        assert f"proven  {claim}" in out
    assert _git(root, "status", "--porcelain") == before
    assert len(_git(root, "worktree", "list").splitlines()) == 1
    assert _git(root, "log", "--oneline").count("\n") == 1
    reviews = glob.glob(os.path.join(A.HOME, ".ao", "archive", A.project_key(root), "prove-*", "semantic-review", "*.md"))
    assert len(reviews) == 1


def test_each_guarantee_that_does_not_hold_says_what_would_fix_it(project, tmp_path, monkeypatch, capsys):
    cfg = _configured(project, tmp_path, monkeypatch, PROBE_OR_REFUSE, installed=False)

    assert cli.cmd_prove(cfg, SimpleNamespace(no_review=False)) == 1

    out = _plain(capsys)
    assert "NOT PROVEN  hook refuses an unauthorised commit" in out and "ao hooks install" in out
    assert "proven  reviewer answers and is another actor" in out
    assert "NOT PROVEN  a throwaway slice lands end to end" in out
    assert "the reviewer did not approve a trivial candidate" in out


def test_the_getting_started_page_types_only_commands_ao_has():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(here, "docs", "getting-started.md"), encoding="utf-8").read()
    commands = set(re.findall(r"add_parser\(\s*[\"']([a-z0-9-]+)[\"']", open(cli.__file__, encoding="utf-8").read()))
    typed = [line.split()[1] for line in re.findall(r"^ao [a-z-]+", page, re.M)]

    assert "prove" in typed and "init" in typed and "hooks" in typed
    assert [name for name in typed if name not in commands] == []
