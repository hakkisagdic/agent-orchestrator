import json
import os
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")


def _write(root, path, lines):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write("".join(f"line_{n} = {n}\n" for n in range(lines)))


def _stage(root, *paths):
    subprocess.run(["git", "add", "-A", *paths], cwd=root, check=True)


def _commit(root):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base"], cwd=root,
                   check=True)


def _reviewer(project, tmp_path):
    marker = tmp_path / "spawned.txt"
    script = f"import sys; open({str(marker)!r}, 'w', encoding='utf-8').write(sys.argv[1]); " \
             + "; ".join(f"print({line!r})" for line in APPROVED)
    return dict(project, reviewer={"id": "r1", "family": "x", "argv": [sys.executable, "-c", script, "{prompt}"]}), marker


def _review(cfg):
    return cli.cmd_review(cfg, SimpleNamespace(action=None, rid=None, any=False, run=None, boundary="b", paths=None,
                                               commits=None, timeout=None))


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def test_size_is_reported_by_kind_and_path_never_as_one_number(project):
    root = project["root"]
    _write(root, "src/gone.py", 5)
    _stage(root, "src")
    _commit(root)
    _write(root, "src/app.py", 10)
    _write(root, "tests/test_app.py", 20)
    _write(root, "tests/fixtures/case.json", 30)
    _write(root, "package-lock.json", 50)
    os.remove(os.path.join(root, "src", "gone.py"))
    _stage(root, "src", "tests", "package-lock.json")

    size = A.candidate_size(root, A.index_candidate(root))

    assert size["paths"] == 5
    assert size["kinds"] == {
        "product": {"paths": 1, "added": 10, "deleted": 0},
        "tests": {"paths": 1, "added": 20, "deleted": 0},
        "fixtures": {"paths": 1, "added": 30, "deleted": 0},
        "generated": {"paths": 1, "added": 50, "deleted": 0},
        "deletion": {"paths": 1, "added": 0, "deleted": 5},
    }
    assert A.size_tripwire(project, size)["state"] == "within"


def test_a_green_small_overshoot_is_advised_not_refused_and_the_reviewer_is_asked(project, tmp_path, monkeypatch,
                                                                                  capsys):
    root = project["root"]
    _write(root, "src/app.py", 450)
    _stage(root, "src")
    spec = {"gates": {"test": {"run": shlex.join([sys.executable, "-c", "print('ok')"]), "timeout": 30}},
            "profiles": {"quick": ["test"]}, "default_profile": "quick"}
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 0
    assert A.latest_verification(root)["candidate_size"]["kinds"]["product"]["added"] == 450
    cfg, marker = _reviewer(project, tmp_path)
    capsys.readouterr()

    assert _review(cfg) == 0

    out = _plain(capsys)
    assert "450 product line(s) across 1 path(s) is over the guideline" in out
    assert "over by 12% and V-" in out and "do not reshape verified code to meet a size number" in out
    assert "does not say why it is one slice" in marker.read_text(encoding="utf-8")


def test_a_candidate_far_above_the_guideline_is_refused_before_a_reviewer_starts(project, tmp_path, capsys):
    root = project["root"]
    _write(root, "src/app.py", 4001)
    _stage(root, "src")
    cfg, marker = _reviewer(project, tmp_path)

    assert _review(cfg) == 2

    assert "4001 product line(s) across 1 path(s) is far above what a review can credibly judge" in _plain(capsys)
    assert not marker.exists()
