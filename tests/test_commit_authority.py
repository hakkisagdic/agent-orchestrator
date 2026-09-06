import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

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
    _git(root, "add", "src/a.py")
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


def _write_approved_review(project, tree=None, candidate=None):
    root = project["root"]
    lines = ["# Review approved.md", ""]
    if candidate is not None:
        scope = A.candidate_scope(candidate)
        lines.append(A.review_evidence_line({
            "schema": 2,
            "kind": "index-candidate",
            "authorizable": True,
            "candidate": candidate,
            "scope": scope,
            "diff_digest": "sha256:" + hashlib.sha256(
                A.candidate_diff(root, candidate, scope)
            ).hexdigest(),
        }))
    lines.extend([
        "- reviewer: `independent-reviewer`",
        "- implementer: `kiro/s1`",
    ])
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


def _tamper_review(path, field):
    body = open(path, encoding="utf-8").read()
    evidence = A.review_evidence(body)
    original = A.review_evidence_line(evidence)
    if field == "candidate":
        evidence["candidate"]["changed_count"] += 1
    elif field == "scope":
        evidence["scope"]["digest"] = "sha256:tampered"
    elif field == "diff_digest":
        evidence["diff_digest"] = "sha256:tampered"
    else:
        raise AssertionError(f"unknown review tamper field: {field}")
    open(path, "w", encoding="utf-8").write(
        body.replace(original, A.review_evidence_line(evidence), 1)
    )


def _allow_commit_prerequisites(monkeypatch, tree, candidate):
    monkeypatch.setattr(
        A,
        "latest_verification",
        lambda root: {
            "id": "V-1",
            "passed": True,
            "tree": tree,
            "gates": [],
            "candidate": candidate,
            "candidate_ready": True,
        },
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

    argv = [sys.executable, "-c", 'print("gate ok")']
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
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


def test_gate_lock_liveness_probe_does_not_signal_windows_process(tmp_path, monkeypatch):
    from ao import procs

    lock = tmp_path / "gate.lock"
    lock.write_text(json.dumps({"root": "project", "pid": 4242, "at": 1}), encoding="utf-8")
    monkeypatch.setattr(A, "GATE_LOCK", str(lock))
    monkeypatch.setattr(A.os, "name", "nt")
    monkeypatch.setattr(procs, "all_pids", lambda: [4242])

    def unexpected_signal(*args):
        raise AssertionError(f"Windows liveness probe sent signal: {args}")

    monkeypatch.setattr(A.os, "kill", unexpected_signal)
    assert A.gate_lock_holder()["pid"] == 4242

    monkeypatch.setattr(procs, "all_pids", lambda: [])
    assert A.gate_lock_holder() is None
    assert not lock.exists()


def test_review_artifact_does_not_invalidate_reviewed_tree(project):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(
        project,
        reviewer={"id": "independent-reviewer", "family": "test", "argv": _approved_reviewer()},
    )
    before = A.tree_digest(root, cfg)
    candidate = A.index_candidate(root)

    assert cli.cmd_review(cfg, _review_args()) == 0

    names = [name for name in os.listdir(os.path.join(root, cfg["reviews"])) if name.endswith(".md")]
    assert len(names) == 1
    body = open(os.path.join(root, cfg["reviews"], names[0]), encoding="utf-8").read()
    recorded = re.search(r"tree:\s*`([^`]+)`", body).group(1)
    evidence = A.review_evidence(body)
    assert recorded == before == A.tree_digest(root, cfg)
    assert evidence["authorizable"] is True
    assert evidence["candidate"]["digest"] == candidate["digest"]


def test_commit_ok_refuses_legacy_review_without_candidate_evidence(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    tree = A.tree_digest(root, project)
    candidate = A.index_candidate(root)
    _write_approved_review(project, tree)
    _allow_commit_prerequisites(monkeypatch, tree, candidate)

    code = cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None))
    output = capsys.readouterr().out

    assert code == 1
    assert "no APPROVED prospective review is bound" in output
    assert "GRANTED" not in output


def test_commit_ok_refuses_when_grant_cannot_be_persisted(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    tree = A.tree_digest(root, project)
    candidate = A.index_candidate(root)
    _write_approved_review(project, tree, candidate)
    _allow_commit_prerequisites(monkeypatch, tree, candidate)

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


def test_commit_ok_records_the_exact_index_candidate(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    tree = A.tree_digest(root, project)
    candidate = A.index_candidate(root)
    _write_approved_review(project, tree, candidate)
    _allow_commit_prerequisites(monkeypatch, tree, candidate)

    code = cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None))
    output = capsys.readouterr().out

    assert code == 0 and "GRANTED" in output
    ledger = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    row = json.loads(open(ledger, encoding="utf-8").read().splitlines()[-1])
    assert row["schema"] == 2
    assert row["candidate"]["digest"] == candidate["digest"]
    assert row["candidate"]["index_tree"] == candidate["index_tree"]
    assert row["scope"]["kind"] == "full-index"


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        (
            "candidate",
            "review evidence candidate does not exactly match the current index candidate",
        ),
        (
            "scope",
            "review evidence scope does not exactly match the current candidate",
        ),
        (
            "diff_digest",
            "review evidence diff digest does not match the exact candidate diff",
        ),
    ],
)
def test_commit_ok_refuses_tampered_review_integrity(
    project, monkeypatch, capsys, field, reason
):
    root = project["root"]
    _repo_with_change(root)
    tree = A.tree_digest(root, project)
    candidate = A.index_candidate(root)
    review_path = _write_approved_review(project, tree, candidate)
    _tamper_review(review_path, field)
    _allow_commit_prerequisites(monkeypatch, tree, candidate)

    code = cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None))
    output = capsys.readouterr().out

    assert code == 1
    assert reason in output
    assert "GRANTED" not in output


def test_commit_ok_refuses_index_mutation_after_review_and_verification(
    project, monkeypatch, capsys
):
    root = project["root"]
    path = _repo_with_change(root)
    tree = A.tree_digest(root, project)
    reviewed = A.index_candidate(root)
    _write_approved_review(project, tree, reviewed)
    _allow_commit_prerequisites(monkeypatch, tree, reviewed)

    open(path, "w", encoding="utf-8").write("value = 3\n")
    _git(root, "add", "src/a.py")
    current = A.index_candidate(root)
    assert current["digest"] != reviewed["digest"]

    code = cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None))
    output = capsys.readouterr().out

    assert code == 1
    assert "index changed since V-1" in output
    assert "no APPROVED prospective review is bound" in output
    assert "GRANTED" not in output


def _install_passing_gate(project, tmp_path, monkeypatch):
    root = project["root"]
    argv = [sys.executable, "-c", 'print("gate ok")']
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    gates = {
        "gates": {"smoke": {"run": command, "expect": "exit_zero", "timeout": 30}},
        "profiles": {"quick": ["smoke"]},
        "default_profile": "quick",
    }
    open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8").write(
        json.dumps(gates)
    )
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))


def test_verify_records_exact_staged_candidate_before_and_after(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    _repo_with_change(root)
    _install_passing_gate(project, tmp_path, monkeypatch)
    expected = A.index_candidate(root)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 0
    record = A.latest_verification(root)

    assert record["passed"] is True
    assert record["candidate"] == expected
    assert record["candidate_after"] == expected
    assert record["candidate_ready"] is True
    assert record["candidate_issues"] == []
    assert record["candidate"]["changed_paths"] == ["src/a.py"]


def test_verify_refuses_partially_staged_product_candidate(
    project, tmp_path, monkeypatch
):
    root = project["root"]
    path = _repo_with_change(root)
    expected = A.index_candidate(root)
    open(path, "w", encoding="utf-8").write("value = 3\n")
    _install_passing_gate(project, tmp_path, monkeypatch)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 1
    record = A.latest_verification(root)
    issue = "unstaged product changes differ from the index: src/a.py"

    assert record["passed"] is False
    assert record["candidate_ready"] is False
    assert record["candidate"] == expected
    assert record["candidate_after"] == expected
    assert record["candidate_issues"] == [issue]
    isolation = next(gate for gate in record["gates"] if gate["name"] == "candidate-isolation")
    assert isolation == {
        "name": "candidate-isolation",
        "passed": False,
        "detail": issue,
        "exit": 1,
        "seconds": 0,
    }


def test_commit_ok_refuses_approved_retrospective_review_for_new_candidate(
    project, monkeypatch, capsys
):
    root = project["root"]
    path = _repo_with_change(root)
    _git(root, "commit", "-q", "-m", "reviewed change")
    cfg = dict(
        project,
        reviewer={"id": "independent-reviewer", "family": "test", "argv": _approved_reviewer()},
    )
    args = _review_args()
    args.commits = "HEAD~1..HEAD"

    assert cli.cmd_review(cfg, args) == 0
    names = [name for name in os.listdir(os.path.join(root, cfg["reviews"])) if name.endswith(".md")]
    assert len(names) == 1
    body = open(os.path.join(root, cfg["reviews"], names[0]), encoding="utf-8").read()
    evidence = A.review_evidence(body)
    assert "VERDICT: APPROVED" in body
    assert evidence["kind"] == "commit-range"
    assert evidence["authorizable"] is False
    assert evidence["commits"] == "HEAD~1..HEAD"

    open(path, "w", encoding="utf-8").write("value = 3\n")
    _git(root, "add", "src/a.py")
    tree = A.tree_digest(root, cfg)
    candidate = A.index_candidate(root)
    _allow_commit_prerequisites(monkeypatch, tree, candidate)
    assert A.latest_candidate_review(root, cfg["reviews"], candidate["digest"]) is None
    capsys.readouterr()

    code = cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None))
    output = capsys.readouterr().out

    assert code == 1
    assert "no APPROVED prospective review is bound to this staged candidate" in output
    assert "GRANTED" not in output


def _persist_exact_candidate_grant(project):
    root = project["root"]
    candidate = A.index_candidate(root)
    review_path = _write_approved_review(project, A.tree_digest(root, project), candidate)
    review_name = os.path.basename(review_path)
    verification = {
        "id": "V-persisted",
        "schema": 2,
        "passed": True,
        "candidate_ready": True,
        "candidate": candidate,
        "candidate_after": candidate,
        "gates": [],
    }
    A.record_verification(root, verification)
    A.record_authority(
        root,
        True,
        [],
        A.tree_digest(root, project),
        verification["id"],
        "C-persisted",
        review=review_name,
        reviewer="independent-reviewer",
        candidate=candidate,
        scope=A.candidate_scope(candidate),
    )
    return candidate


def test_commit_check_accepts_exact_persisted_grant_without_mutating_ledger(
    project, capsys
):
    root = project["root"]
    _repo_with_change(root)
    candidate = _persist_exact_candidate_grant(project)
    ledger = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    before = open(ledger, "rb").read()

    code = cli.cmd_commit_check(project, SimpleNamespace())
    output = capsys.readouterr().out

    assert code == 0
    assert "AUTHORIZED" in output
    assert candidate["index_tree"] in output
    assert A.latest_authority_decision(root)["token"] == "C-persisted"
    assert open(ledger, "rb").read() == before


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        (
            "scope",
            "granted review scope does not exactly match the persisted authority grant",
        ),
        (
            "diff_digest",
            "review evidence diff digest does not match the exact candidate diff",
        ),
    ],
)
def test_commit_check_refuses_tampered_review_integrity_without_mutating_ledger(
    project, capsys, field, reason
):
    root = project["root"]
    _repo_with_change(root)
    _persist_exact_candidate_grant(project)
    review_path = os.path.join(root, project["reviews"], "approved.md")
    _tamper_review(review_path, field)
    ledger = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    before = open(ledger, "rb").read()

    code = cli.cmd_commit_check(project, SimpleNamespace())
    output = capsys.readouterr().out

    assert code == 1
    assert "COMMIT REFUSED" in output
    assert reason in output
    assert open(ledger, "rb").read() == before


def test_commit_check_refuses_changed_index_without_mutating_ledger(project, capsys):
    root = project["root"]
    path = _repo_with_change(root)
    _persist_exact_candidate_grant(project)
    ledger = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    before = open(ledger, "rb").read()

    open(path, "w", encoding="utf-8").write("value = 3\n")
    _git(root, "add", "src/a.py")
    code = cli.cmd_commit_check(project, SimpleNamespace())
    output = capsys.readouterr().out

    assert code == 1
    assert "COMMIT REFUSED" in output
    assert "latest grant does not match the current index candidate" in output
    assert open(ledger, "rb").read() == before
