"""Ten named implementer attacks on commit authority, each of which must fail closed (#7).

The authority model changed several times in a few days, and each hole was found
by someone attacking it. These are the attacks enumerated instead of rediscovered.
Every test takes the implementer's side: it holds a real grant, or reaches for
one, and then does the one thing the attack names. Where ao can only detect rather
than prevent - the index race - the test says so and proves the detection.
"""
import os
import shutil
import stat
import subprocess
import time
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_commit_authority import _approved_reviewer

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def _git(root, *args, env=None):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _stage(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)
    _git(root, "add", rel)


def _verified(root):
    """A passing verification of exactly the staged candidate, recorded in the chained ledger."""
    candidate = A.index_candidate(root)
    A.record_verification(root, {"id": f"V-attack-{time.time_ns()}", "passed": True,
                                 "candidate": candidate, "candidate_ready": True, "gates": []})


def _commit_ok(cfg, capsys):
    capsys.readouterr()
    code = cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None))
    return code, capsys.readouterr().out


def _commit_check(cfg, capsys):
    capsys.readouterr()
    code = cli.cmd_commit_check(cfg, SimpleNamespace())
    return code, capsys.readouterr().out


def _granted(project, capsys, content="value = 1\n"):
    """The implementer's starting point: one staged change, verified and granted."""
    root = project["root"]
    _stage(root, "src/a.py", content)
    cfg = dict(project, features={"review": False})
    _verified(root)
    code, out = _commit_ok(cfg, capsys)
    assert code == 0, out
    assert _commit_check(cfg, capsys)[0] == 0
    return cfg


def test_attack_01_stage_swap(project, capsys):
    cfg = _granted(project, capsys)
    _stage(cfg["root"], "src/a.py", "value = 'swapped in after the grant'\n")

    code, out = _commit_check(cfg, capsys)
    assert code == 1 and "COMMIT REFUSED" in out


def test_attack_02_index_race(project, capsys, monkeypatch):
    """Detected, not prevented: Git writes the tree after the hook returns (#64)."""
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(name, "t")
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "t@t")
    cfg = _granted(project, capsys)
    root = cfg["root"]
    hook = os.path.join(root, ".git", "hooks", "pre-commit")
    open(hook, "w", encoding="utf-8", newline="\n").write(
        "#!/bin/sh\necho 'smuggled = True' > src/smuggled.py\ngit add src/smuggled.py\n")
    os.chmod(hook, os.stat(hook).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    capsys.readouterr()

    assert cli.cmd_commit(cfg, SimpleNamespace(message="land", file=None)) == 1
    assert "LANDED OUTSIDE ITS GRANT" in capsys.readouterr().out
    assert A.latest_authority_decision(root)["granted"] is False
    assert A.commits_without_grant(root) == [_git(root, "rev-parse", "HEAD")]


def test_attack_03_forged_review_evidence(project, capsys):
    root = project["root"]
    _stage(root, "src/a.py", "value = 1\n")
    candidate = A.index_candidate(root)
    scope = A.candidate_scope(candidate)
    evidence = {"schema": 2, "kind": "index-candidate", "authorizable": True, "candidate": candidate,
                "scope": scope, "diff_digest": "sha256:" + __import__("hashlib").sha256(
                    A.candidate_diff(root, candidate, scope)).hexdigest(),
                "reviewer": {"id": "independent", "family": "other", "fallback": False},
                "verdict": "APPROVED", "counts": {"BLOCKER": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}}
    open(os.path.join(root, "semantic-review", "forged.md"), "w", encoding="utf-8").write(
        "# Review forged.md\n\n" + A.review_evidence_line(evidence)
        + "\n- reviewer: `independent`\n\nVERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n")
    _verified(root)

    code, out = _commit_ok(project, capsys)
    assert code == 1 and "no APPROVED prospective review is bound to this staged candidate" in out


def test_attack_04_retro_review_as_authority(project, capsys):
    root = project["root"]
    _stage(root, "src/a.py", "value = 1\n")
    _git(root, "commit", "-q", "-m", "base")
    _stage(root, "src/a.py", "value = 2\n")
    _git(root, "commit", "-q", "-m", "landed without review")
    cfg = dict(project, reviewer={"id": "independent", "family": "other", "argv": _approved_reviewer()})
    retro = SimpleNamespace(boundary="b", timeout=30, paths=None, commits="HEAD~1..HEAD")
    assert cli.cmd_review(cfg, retro) == 0
    _git(root, "reset", "-q", "--soft", "HEAD~1")
    _verified(root)

    code, out = _commit_ok(cfg, capsys)
    assert code == 1 and "no APPROVED prospective review is bound to this staged candidate" in out


def test_attack_05_waiver_replay(project, capsys):
    root = project["root"]
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    open(board, "w", encoding="utf-8").write(text.replace("## running\n", "## running\n- [B7] slice\n"))
    waiver = A.waive(root, "review", "B7", "reviewer at quota", by="alice (owner)")
    _stage(root, "src/a.py", "value = 1\n")
    _verified(root)
    assert _commit_ok(project, capsys)[0] == 0

    _stage(root, "src/b.py", "replayed = True\n")
    _verified(root)
    code, out = _commit_ok(project, capsys)
    assert code == 1 and f"waiver {waiver['id']} already authorised other bytes" in out


def test_attack_06_coordination_path_smuggling(project, capsys):
    root = project["root"]
    _stage(root, "src/a.py", "value = 1\n")
    _stage(root, "semantic-review/approved.md", "VERDICT: APPROVED\n")
    _verified(root)

    code, out = _commit_ok(dict(project, features={"review": False}), capsys)
    assert code == 1 and "coordination paths must not share a product commit" in out


def test_attack_07_hook_removal_fails_closed(project, capsys):
    """The hook is ao commit-check; removing the state it reads must refuse, not skip (#90)."""
    cfg = _granted(project, capsys)
    root = cfg["root"]
    shutil.rmtree(os.path.join(root, ".ao"))

    code, out = _commit_check(A.load_config(root), capsys)
    assert code == 1 and "COMMIT REFUSED" in out
    head = _git(root, "rev-parse", "HEAD")
    assert cli.cmd_commit(A.load_config(root), SimpleNamespace(message="land", file=None)) == 1
    assert _git(root, "rev-parse", "HEAD") == head


def test_attack_08_ledger_truncation(project, capsys):
    cfg = _granted(project, capsys)
    ledger = os.path.join(cfg["root"], ".ao", "ledger", "authority.jsonl")
    open(ledger, "w", encoding="utf-8").write("")

    code, out = _commit_check(cfg, capsys)
    assert code == 1 and "rows were removed from its end" in out


def test_attack_09_digest_collision_by_rename(project, capsys):
    cfg = _granted(project, capsys)
    _git(cfg["root"], "mv", "src/a.py", "src/renamed.py")

    code, out = _commit_check(cfg, capsys)
    assert code == 1 and "COMMIT REFUSED" in out


def test_attack_10_git_index_file_spoof(project, capsys, monkeypatch, tmp_path):
    root = project["root"]
    spoof = str(tmp_path / "spoof.index")
    env = dict(os.environ, GIT_INDEX_FILE=spoof)
    _git(root, "read-tree", "HEAD", env=env)
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("value = 'reviewed'\n")
    _git(root, "add", "src/a.py", env=env)
    monkeypatch.setenv("GIT_INDEX_FILE", spoof)
    cfg = dict(project, features={"review": False})
    _verified(root)
    assert _commit_ok(cfg, capsys)[0] == 0
    monkeypatch.delenv("GIT_INDEX_FILE")

    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("value = 'what lands'\n")
    _git(root, "add", "src/a.py")
    code, out = _commit_check(cfg, capsys)
    assert code == 1 and "COMMIT REFUSED" in out
