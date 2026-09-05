import json
import os
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _repo_with_change(root):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    path = os.path.join(root, "src", "a.py")
    open(path, "w", encoding="utf-8").write("value = 1\n")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-q", "-m", "product base")
    open(path, "w", encoding="utf-8").write("value = 2\n")
    return path


def _approved_reviewer():
    script = "; ".join(
        f"print({line!r})"
        for line in (
            "VERDICT: APPROVED",
            "BLOCKER: 0",
            "HIGH: 0",
            "MEDIUM: 0",
            "LOW: 0",
            "",
            "## Bulgular",
            "Bulgu yok.",
        )
    )
    return [sys.executable, "-c", script, "{prompt}"]


def _review_args():
    return SimpleNamespace(boundary="product change", timeout=30, paths=None, commits=None)


def _write_approved_review(project, tree=None):
    root = project["root"]
    lines = [
        "# Review approved.md",
        "",
        "- reviewer: `independent-reviewer`",
        "- implementer: `kiro/s1`",
    ]
    if tree is not None:
        lines.append(f"- tree: `{tree}`")
    lines.extend(
        [
            "- boundary: product change",
            "",
            "VERDICT: APPROVED",
            "BLOCKER: 0",
            "HIGH: 0",
            "MEDIUM: 0",
            "LOW: 0",
            "",
        ]
    )
    path = os.path.join(root, project["reviews"], "approved.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    return path


def _allow_commit_prerequisites(monkeypatch, tree):
    monkeypatch.setattr(
        A,
        "latest_verification",
        lambda root: {"id": "V-1", "passed": True, "tree": tree, "gates": []},
    )
    monkeypatch.setattr(A, "plan_drift", lambda root: [])
    monkeypatch.setattr(A, "hold_state", lambda root: None)
    monkeypatch.setattr(A, "urgent_messages", lambda *args, **kwargs: [])


def test_tree_digest_hashes_untracked_content_and_ignores_staging(project):
    root = project["root"]
    path = os.path.join(root, "new-product.txt")
    open(path, "w", encoding="utf-8").write("alpha")
    alpha = A.tree_digest(root, project)

    open(path, "w", encoding="utf-8").write("bravo")
    bravo = A.tree_digest(root, project)
    assert bravo != alpha

    _git(root, "add", "new-product.txt")
    assert A.tree_digest(root, project) == bravo


def test_tree_digest_changes_for_tracked_product_content(project):
    root = project["root"]
    path = _repo_with_change(root)
    changed = A.tree_digest(root, project)
    open(path, "w", encoding="utf-8").write("value = 3\n")
    assert A.tree_digest(root, project) != changed


def test_verify_record_does_not_invalidate_its_own_tree(project, tmp_path, monkeypatch):
    root = project["root"]
    verification_ledger = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    open(verification_ledger, "w", encoding="utf-8").write("")
    _git(root, "add", ".ao/ledger/verifications.jsonl")
    _git(root, "commit", "-q", "-m", "track verification ledger")

    command = f"{shlex.quote(sys.executable)} -c {shlex.quote('print(\"gate ok\")')}"
    gates = {
        "gates": {"smoke": {"run": command, "expect": "exit_zero", "timeout": 30}},
        "profiles": {"quick": ["smoke"]},
        "default_profile": "quick",
    }
    open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8").write(
        json.dumps(gates)
    )
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))

    before = A.tree_digest(root, project)
    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 0
    after = A.tree_digest(root, project)
    record = A.latest_verification(root)

    assert after == before
    assert record is not None and record["passed"] is True and record["tree"] == after


def test_review_artifact_does_not_invalidate_reviewed_tree(project):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(
        project,
        reviewer={"id": "independent-reviewer", "family": "test", "argv": _approved_reviewer()},
    )
    before = A.tree_digest(root, cfg)

    assert cli.cmd_review(cfg, _review_args()) == 0

    names = [name for name in os.listdir(os.path.join(root, cfg["reviews"])) if name.endswith(".md")]
    assert len(names) == 1
    body = open(os.path.join(root, cfg["reviews"], names[0]), encoding="utf-8").read()
    recorded = re.search(r"tree:\s*`([^`]+)`", body).group(1)
    assert recorded == before == A.tree_digest(root, cfg)


def test_commit_ok_refuses_approved_review_without_tree(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    tree = A.tree_digest(root, project)
    _write_approved_review(project)
    _allow_commit_prerequisites(monkeypatch, tree)

    code = cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None))
    output = capsys.readouterr().out

    assert code == 1
    assert "names no tree digest" in output
    assert "GRANTED" not in output


def test_commit_ok_refuses_when_grant_cannot_be_persisted(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    tree = A.tree_digest(root, project)
    _write_approved_review(project, tree)
    _allow_commit_prerequisites(monkeypatch, tree)

    def fail_record(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(A, "record_authority", fail_record)
    code = cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None))
    output = capsys.readouterr().out

    assert code == 1
    assert "could not persist authority grant" in output
    assert "GRANTED" not in output


def test_record_authority_persists_the_complete_grant(project):
    root = project["root"]
    A.record_authority(
        root,
        True,
        [],
        "sha256:tree",
        "V-1",
        "C-1",
        review="review.md",
        reviewer="independent-reviewer",
    )
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    row = json.loads(open(path, encoding="utf-8").read().splitlines()[-1])
    assert row == {
        "at": row["at"],
        "granted": True,
        "token": "C-1",
        "reasons": [],
        "tree": "sha256:tree",
        "verification": "V-1",
        "review": "review.md",
        "reviewer": "independent-reviewer",
    }
