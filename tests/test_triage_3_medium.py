"""Deferred reviews, grants, retirement and watchdog checks lost context (TRIAGE-3-MEDIUM).

Each regression uses a temporary project; no real harness or account is contacted.
"""
import json
import os
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, drivers, lib as A, storage, watchdog as W
from tests.scenarios import World
from tests.test_review_chain import _repo_with_change
from tests.standin import program
from tests.test_review_retention import _artefact
from tests.test_review_submit import _args
from tests.test_standin_collect import _answer, _collect, _carried_rows, APPROVED
from tests.test_worktree_lifetime import _worktree, _git


def _reviewer(tmp_path, exit_code=0, capture=None):
    source = "import sys\n"
    if capture:
        source += f"open({str(capture)!r}, 'w', encoding='utf-8').write(sys.argv[1])\n"
    source += "\n".join(f"print({line!r})" for line in APPROVED)
    source += f"\nraise SystemExit({exit_code})\n"
    return program(tmp_path / "bin", "reviewer", source)


def _board(root, running="S1", keep=True):
    old = "- [S1] first · acceptance: changed boundary\n" if keep else ""
    Path(root, ".ao", "board.md").write_text(
        "## running\n" + ("- [S1] first · acceptance: original boundary\n" if running == "S1"
                         else "- [S2] second · acceptance: second boundary\n")
        + "## queued\n" + (old if running != "S1" else "") + "## done\n", encoding="utf-8")


@pytest.mark.parametrize("keep", [True, False], ids=["board-moved", "slice-gone"])
def test_m1_submitted_review_keeps_its_slice_and_boundary(project, tmp_path, monkeypatch, keep):
    root = project["root"]
    _repo_with_change(root)
    _board(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [_reviewer(tmp_path), "{prompt}"]})
    spawned = []
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root, rid: spawned.append(rid))
    assert cli.cmd_review(cfg, _args(action="submit", boundary=None)) == 0
    _board(root, "S2", keep)
    code = cli.cmd_review(cfg, _args(run=spawned[0], boundary=None))
    rows = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)
    if not keep:
        assert code == 2 and not rows
    else:
        assert code == 0 and rows[-1]["slice"] == "S1"
        evidence = A.review_evidence(Path(root, "semantic-review", rows[-1]["artefact"]).read_text(encoding="utf-8"))
        assert evidence["boundary"] == "original boundary"


@pytest.mark.parametrize("keep", [True, False], ids=["board-moved", "slice-gone"])
def test_m1_carried_review_keeps_request_slice(project, tmp_path, capsys, keep):
    root = project["root"]
    _repo_with_change(root)
    _board(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x",
                                 "argv": [_reviewer(tmp_path, exit_code=17), "{prompt}"]})
    assert cli.cmd_review(cfg, _args(boundary="b")) == 3
    (meta,) = Path(A.review_requests_dir(root)).glob("*.json")
    request = A.review_request(root, meta.stem)
    _board(cfg["root"], "S2", keep)
    code, out = _collect(cfg, request["nonce"], _answer(tmp_path, request["nonce"], APPROVED), capsys)
    rows = _carried_rows(cfg["root"])
    # The request kept outside the tree owns a carried answer even after its board row is gone.
    assert code == 0, out
    (row,) = rows
    assert row["slice"] == "S1"
    evidence = A.review_evidence(Path(root, "semantic-review", row["artefact"]).read_text(encoding="utf-8"))
    assert evidence["slice"] == "S1" and evidence["boundary"] == "b"
    assert A.review_request(root, request["nonce"])["collected"]["artefact"] == row["artefact"]


def test_m2_all_allowed_tool_words_are_checked(project):
    argv = ["claude", "--allowedTools", "Read", "Bash(git commit:*)", "-p", "{prompt}"]
    assert any("skips the commit hook" in why for why, _, _ in AL.problems(argv, role="architect"))
    assert A.turn_refusal("architect", "claude-code", argv)


@pytest.mark.parametrize("argv", [
    ["copilot", "--allow-all-tools"],
    ["claude", "--permission-mode", "bypassPermissions"],
])
def test_m2_bypass_grants_are_audited_and_refused(project, argv):
    assert AL.grants_everything(argv)
    cfg = dict(project, architect={"name": "architect", "argv": argv})
    assert "actor-grant:architect" in dict(cli._actor_grant_problems(cfg))
    ident = "copilot" if argv[0] == "copilot" else "claude-code"
    assert A.turn_refusal("architect", ident, argv)


@pytest.mark.parametrize("prefix", [["env"], ["/usr/bin/env", "KIRO_LOG=0"]])
def test_m3_wrapped_routes_cannot_hide_the_harness(project, prefix):
    argv = prefix + ["kiro-cli", "chat", "--no-interactive", "{prompt}"]
    route = {"argv": argv}
    assert cli._reviewer_ineligible(project, route) is not None
    assert A.route_refusal("reviewer", None, argv)
    assert A.route_refusal("hunter", None, argv)


def test_m4_linked_review_directory_is_refused(project, tmp_path):
    root = project["root"]
    outside = tmp_path / "outside"
    outside.mkdir()
    kept = outside / "old.md"
    kept.write_text("outside evidence", encoding="utf-8")
    os.utime(kept, (1, 1))
    directory = Path(root, "semantic-review")
    directory.rmdir()
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, RuntimeError)):
        A.prune_review_artefacts(root, project, 30, apply=True)
    assert kept.read_text(encoding="utf-8") == "outside evidence"


def test_m4_linked_review_entry_is_skipped(project, tmp_path):
    outside = tmp_path / "old.md"
    outside.write_text("outside evidence", encoding="utf-8")
    os.utime(outside, (1, 1))
    link = Path(project["root"], "semantic-review", "linked.md")
    link.symlink_to(outside)
    result = A.prune_review_artefacts(project["root"], project, 30, apply=True)
    assert result["moved"] == [] and link.is_symlink()


@pytest.mark.parametrize("kind", ["grant", "verification"])
def test_m4_missing_reference_ledger_keeps_everything(project, kind):
    root = project["root"]
    loose = _artefact(root, "old.md")
    if kind == "grant":
        A.record_authority(root, True, [], "sha256:t", "V-1", "C-1", review="old.md")
        name = "authority.jsonl"
    else:
        A.record_verification(root, {"id": "V-1", "at": 1, "passed": True, "review": "old.md"})
        name = "verifications.jsonl"
    Path(root, ".ao", "ledger", name).unlink()
    with pytest.raises((OSError, RuntimeError, ValueError)):
        A.prune_review_artefacts(root, project, 30, apply=True)
    assert Path(loose).exists()


@pytest.mark.parametrize("kind", ["grant", "verification"])
def test_m4_sealed_references_keep_the_review(project, kind):
    root = project["root"]
    kept = _artefact(root, "old.md")
    if kind == "grant":
        for n in range(2):
            A.record_authority(root, True, [], "sha256:t", "V-1", f"C-{n}", review="old.md" if n == 0 else None)
        path, chain = Path(root, ".ao", "ledger", "authority.jsonl"), A.AUTHORITY_CHAIN
    else:
        for n in range(2):
            A.record_verification(root, {"id": f"V-{n}", "at": n, "passed": True,
                                         "review": "old.md" if n == 0 else None})
        path, chain = Path(root, ".ao", "ledger", "verifications.jsonl"), A.VERIFICATION_CHAIN
    storage.seal_chained_jsonl(str(path), chain, keep=1, legacy_prefix=kind == "verification")
    result = A.prune_review_artefacts(root, project, 30, apply=True)
    assert Path(kept).exists() and kind in result["kept"]["old.md"]


def test_m4_unreadable_index_keeps_everything(project, monkeypatch):
    loose = _artefact(project["root"], "old.md")
    def unreadable(root):
        raise RuntimeError("index unreadable")
    monkeypatch.setattr(A, "index_candidate", unreadable)
    with pytest.raises(RuntimeError, match="index unreadable"):
        A.prune_review_artefacts(project["root"], project, 30, apply=True)
    assert Path(loose).exists()


def test_m5_unreadable_status_keeps_worktree(project, tmp_path, monkeypatch):
    root = project["root"]
    path = _worktree(root, tmp_path, "landed")
    _git(root, "merge", "-q", "--no-ff", "--no-edit", "landed")
    original = A._git_output
    def status(root, *args, **kw):
        if os.path.realpath(root) == os.path.realpath(path) and args[0] == "status":
            raise RuntimeError("status failed")
        return original(root, *args, **kw)
    monkeypatch.setattr(A, "_git_output", status)
    fact = next(f for f in A.worktree_facts(root, project) if f["branch"] == "landed")
    assert not fact["may_go"] and any("status unreadable" in why for why in fact["keep"])


def test_m5_removal_rechecks_status(project, tmp_path, monkeypatch):
    root = project["root"]
    path = _worktree(root, tmp_path, "landed")
    _git(root, "merge", "-q", "--no-ff", "--no-edit", "landed")
    fact = next(f for f in A.worktree_facts(root, project) if f["branch"] == "landed")
    (path / "landed.txt").write_text("not committed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="uncommitted"):
        A.prune_worktree(root, fact, apply=True)
    assert path.exists()


def test_m5_remote_default_merge_allows_retirement(project, tmp_path):
    root = project["root"]
    path = _worktree(root, tmp_path, "landed")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(path, "rev-parse", "HEAD"))
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    fact = next(f for f in A.worktree_facts(root, project) if f["branch"] == "landed")
    assert fact["may_go"] and fact["merged"]


def test_m6_empty_reading_is_a_broken_check(project, monkeypatch):
    monkeypatch.setattr(A, "account_usage", lambda **kw: None)
    st = {}
    W._sample_credits(project["root"], st, "kiro", "proj", now=time.time())
    assert st.get("last_credit_attempt") and st.get("credit_check_problem")
    assert "credits-check" in dict(cli.doctor_problems(project))


def test_m6_missing_token_store_is_named(project):
    result = drivers.usage_limits({"token": {"sqlite": "~/absent-token-store"}})
    assert result and "token store" in result["error"]


def test_m6_request_failure_is_a_broken_check(project, tmp_path, monkeypatch):
    from ao import outbound
    db = tmp_path / "tokens"
    db.touch()
    api = {"token": {"sqlite": str(db), "table": "tokens", "key": "token"},
           "profile": {"argv": ["fake", "profile"]}, "endpoint": "https://example.invalid/",
           "content_type": "application/json", "target": "usage"}
    monkeypatch.setattr(A, "program_path", lambda name: "sqlite3")
    monkeypatch.setattr(A, "binary_candidates", lambda name: ["fake"])
    monkeypatch.setattr(drivers.subprocess, "run", lambda argv, **kw: SimpleNamespace(
        stdout=json.dumps({"access_token": "secret"}) if argv[0] == "sqlite3" else "arn:example", returncode=0))
    def offline(*args, **kw):
        raise urllib.error.URLError("secret must never enter the error")
    monkeypatch.setattr(outbound.OPENER, "open", offline)
    result = drivers.usage_limits(api)
    assert result and "URLError" in result["error"] and "secret" not in result["error"]
    monkeypatch.setattr(A, "account_usage", lambda **kw: drivers.usage_limits(api))
    st = {}
    W._sample_credits(project["root"], st, "kiro", "proj", now=time.time())
    assert st["credit_check_problem"] and "credits-check" in dict(cli.doctor_problems(project))


def test_m7_second_decision_gets_its_own_report_and_wake(project, tmp_path, monkeypatch):
    world = World(project, monkeypatch, tmp_path)
    world.transcript_age(900)
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, key=None, **kw:
                        A.record_notice(root, title, msg, False, key=key))
    first = A.ask(world.root, "first question", ["yes", "no"])
    world.cycle(dry_run=False)
    st = W.load_state(world.root)
    st["last_arch_wake"] = time.time() - 3600
    W.save_state(world.root, st)
    world.spawned.clear()
    # The stored decision ID is independent of the current wall clock (TRIAGE-3-MEDIUM).
    second = dict(first, id=first["id"] + "-second", question="second question")
    Path(world.root, A.DECISION_DIR, second["id"] + ".json").write_text(json.dumps(second), encoding="utf-8")
    world.cycle(dry_run=False)
    reports = Path(world.root, project["mailbox"]).glob("*.md")
    assert any(second["id"] in Path(path).read_text(encoding="utf-8") for path in reports)
    assert any(isinstance(argv, list) and argv[0].endswith("/claude") for argv in world.spawned)


def test_m1_submitted_boundary_file_keeps_contents(project, tmp_path, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    boundary = Path(root, ".ao", "boundary.md")
    original = "# Boundary\n## Invariant\nkeep the original invariant\n"
    boundary.write_text(original, encoding="utf-8")
    Path(root, ".ao", "board.md").write_text(
        "## running\n- [S1] first · boundary: .ao/boundary.md\n## queued\n", encoding="utf-8")
    source = A.read_boundary(root, A.running_slice(root))
    capture = tmp_path / "prompt.txt"
    cfg = dict(project, reviewer={"id": "r1", "family": "x",
                                 "argv": [_reviewer(tmp_path, capture=capture), "{prompt}"]})
    spawned = []
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root, rid: spawned.append(rid))
    assert cli.cmd_review(cfg, _args(action="submit", boundary=None)) == 0
    boundary.write_text("# Boundary\n## Invariant\na replacement invariant\n", encoding="utf-8")
    assert cli.cmd_review(cfg, _args(run=spawned[0], boundary=None)) == 0
    prompt = capture.read_text(encoding="utf-8")
    assert "keep the original invariant" in prompt and "a replacement invariant" not in prompt
    row = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)[-1]
    evidence = A.review_evidence(Path(root, "semantic-review", row["artefact"]).read_text(encoding="utf-8"))
    assert evidence["boundary_file"]["sha256"] == source["sha256"]


@pytest.mark.parametrize("kind", ["grant", "verification"])
def test_m4_missing_sealed_segment_keeps_everything(project, kind):
    root = project["root"]
    kept = _artefact(root, "old.md")
    if kind == "grant":
        for n in range(2):
            A.record_authority(root, True, [], "sha256:t", "V-1", f"C-{n}", review="old.md" if n == 0 else None)
        path, chain = Path(root, ".ao", "ledger", "authority.jsonl"), A.AUTHORITY_CHAIN
    else:
        for n in range(2):
            A.record_verification(root, {"id": f"V-{n}", "at": n, "passed": True,
                                         "review": "old.md" if n == 0 else None})
        path, chain = Path(root, ".ao", "ledger", "verifications.jsonl"), A.VERIFICATION_CHAIN
    seal = storage.seal_chained_jsonl(str(path), chain, keep=1, legacy_prefix=kind == "verification")
    (path.parent / "sealed" / seal["archives"][0]).unlink()
    with pytest.raises((OSError, RuntimeError, ValueError)):
        A.prune_review_artefacts(root, project, 30, apply=True)
    assert Path(kept).exists()
