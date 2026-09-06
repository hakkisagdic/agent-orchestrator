import json
import os
import stat
import subprocess
import time
from types import SimpleNamespace

from ao import cli, features as F, lib as A


def test_features_default_estimate_and_switch(project):
    root = project["root"]
    assert F.estimate(project) == sum(v[2] for v in F.FEATURES.values())
    F.set_switch(root, "review", False)
    cfg = A.load_config(root)
    assert F.enabled(cfg, "review") is False and F.estimate(cfg) == F.estimate(project) - 8
    for k in F.ORDER:
        F.set_switch(root, k, False)
    assert F.estimate(A.load_config(root)) == 0


def test_deferred_queue_roundtrip(project):
    root = project["root"]
    r = A.deferred_append(root, "nudge", reason="implementer quota")
    assert A.recently_deferred(root, "nudge") and [x["id"] for x in A.deferred_open(root)] == [r["id"]]
    A.deferred_close(root, r["id"], "replayed")
    assert A.deferred_open(root) == []


def _stage_candidate(root):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    path = os.path.join(root, "src", "candidate.py")
    open(path, "w", encoding="utf-8").write("value = 1\n")
    subprocess.run(["git", "add", "src/candidate.py"], cwd=root, check=True,
                   capture_output=True)
    return A.index_candidate(root)


def _allow_candidate_verification(monkeypatch, candidate):
    monkeypatch.setattr(
        A,
        "latest_verification",
        lambda root: {
            "id": "V-1",
            "passed": True,
            "gates": [],
            "candidate": candidate,
            "candidate_ready": True,
        },
    )
    monkeypatch.setattr(A, "plan_drift", lambda root: [])
    monkeypatch.setattr(A, "hold_state", lambda root: None)
    monkeypatch.setattr(A, "urgent_messages", lambda *args, **kwargs: [])


def test_waiver_is_honoured_by_commit_ok_and_recorded(project, monkeypatch, capsys):
    root = project["root"]
    b = os.path.join(root, ".ao", "board.md")
    t = open(b, encoding="utf-8").read()
    open(b, "w", encoding="utf-8").write(t.replace("## running\n", "## running\n- [B7] slice · since: 2026-09-05 10:00\n"))
    candidate = _stage_candidate(root)
    _allow_candidate_verification(monkeypatch, candidate)
    args = SimpleNamespace(verify=False, profile=None)

    assert cli.cmd_commit_ok(project, args) == 1
    out = capsys.readouterr().out
    assert "no APPROVED prospective review is bound" in out

    w = A.waive(root, "review", "B7", "reviewer at quota; human accepts the risk", by="hakki")
    assert A.open_waivers(root, gate="review", slice_id="B7")[0]["id"] == w["id"]
    assert cli.cmd_commit_ok(project, args) == 0
    out = capsys.readouterr().out
    assert "review waived" in out and "GRANTED" in out
    assert cli.cmd_commit_check(project, SimpleNamespace()) == 0
    assert "AUTHORIZED" in capsys.readouterr().out

    A.close_waiver(root, w["id"], "reviewed")
    assert A.open_waivers(root) == []
    assert cli.cmd_commit_check(project, SimpleNamespace()) == 1
    assert "no matching running-slice waiver is open" in capsys.readouterr().out


def test_review_feature_off_needs_no_review(project, monkeypatch, capsys):
    root = project["root"]
    F.set_switch(root, "review", False)
    cfg = A.load_config(root)
    candidate = _stage_candidate(root)
    _allow_candidate_verification(monkeypatch, candidate)

    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    output = capsys.readouterr().out
    assert "GRANTED" in output
    assert "no APPROVED prospective review is bound" not in output
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0
    assert "AUTHORIZED" in capsys.readouterr().out


def test_burn_rate_projects_exhaustion(project):
    root = project["root"]
    now = 1_800_000_000
    reset = now + 20 * 86400
    A.record_credit_sample(root, 6000, 10000, reset)
    p = os.path.join(root, ".ao", "ledger", "credits.jsonl")
    rows = [json.loads(l) for l in open(p, encoding="utf-8")]
    rows[0]["at"] = now - 2 * 86400
    rows.append({"at": now, "used": 7000, "limit": 10000, "reset_at": reset})
    open(p, "w", encoding="utf-8").write("\n".join(json.dumps(r) for r in rows) + "\n")
    br = A.burn_rate(root, now=now)
    assert round(br["per_day"]) == 500 and round(br["days_left"]) == 6 and br["before_reset"] is True


def test_ping_is_best_effort(project, monkeypatch):
    root = project["root"]
    assert A.ping(root) is None
    A.set_ping_url(root, "https://hc-ping.com/x")
    assert A.ping(root, opener=lambda url, timeout=0: object()) is True
    assert A.ping(root, opener=lambda url, timeout=0: (_ for _ in ()).throw(OSError())) is False


def test_architect_lock_is_exclusive_and_stale_safe(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "_pid_alive", lambda pid: pid == 111)
    assert A.acquire_architect(root, 111, "wake") is not None
    assert A.acquire_architect(root, 222, "refill") is None          # held by a live 111
    monkeypatch.setattr(A, "_pid_alive", lambda pid: False)
    assert A.architect_lock_holder(root) is None                       # holder died: stale
    assert A.acquire_architect(root, 222, "refill") is not None


def test_foreign_edits_ignore_the_implementers_own_writes(project, tmp_path, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"))
    mine = os.path.join(root, "src", "mine.py")
    theirs = os.path.join(root, "src", "theirs.py")
    open(mine, "w", encoding="utf-8").write("x")
    open(theirs, "w", encoding="utf-8").write("y")
    tr = tmp_path / "t.jsonl"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
    tr.write_text(json.dumps({"timestamp": stamp, "payload": {"type": "tool_call", "toolName": "fs_write",
                                                             "args": {"path": mine}}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(tr), None))
    assert A.foreign_edits(root, project) == ["src/theirs.py"]


def test_push_window_and_hooks(project, capsys):
    root = project["root"]
    assert cli.cmd_push(project, SimpleNamespace(action="check", minutes=30)) == 1
    cli.cmd_push(project, SimpleNamespace(action="allow", minutes=30))
    assert cli.cmd_push(project, SimpleNamespace(action="check", minutes=30)) == 0

    assert cli.cmd_hooks(project, SimpleNamespace(action="install")) == 0
    hook_dir = os.path.join(root, ".git", "hooks")
    pre_commit = os.path.join(hook_dir, "pre-commit")
    pre_push = os.path.join(hook_dir, "pre-push")
    assert "commit-check" in open(pre_commit, encoding="utf-8").read()
    assert "push check" in open(pre_push, encoding="utf-8").read()
    assert os.access(pre_commit, os.X_OK) and os.access(pre_push, os.X_OK)

    assert cli.cmd_hooks(project, SimpleNamespace(action="status")) == 0
    status_output = capsys.readouterr().out
    assert "pre-commit: installed" in status_output
    assert "pre-push: installed" in status_output

    assert cli.cmd_hooks(project, SimpleNamespace(action="uninstall")) == 0
    assert not os.path.exists(pre_commit)
    assert not os.path.exists(pre_push)


def test_hook_install_preflight_is_atomic_and_uninstall_preserves_foreign_hook(
    project, capsys
):
    root = project["root"]
    hook_dir = os.path.join(root, ".git", "hooks")
    os.makedirs(hook_dir, exist_ok=True)
    pre_commit = os.path.join(hook_dir, "pre-commit")
    pre_push = os.path.join(hook_dir, "pre-push")
    foreign = "#!/bin/sh\necho foreign\n"
    open(pre_push, "w", encoding="utf-8").write(foreign)

    assert cli.cmd_hooks(project, SimpleNamespace(action="install")) == 1
    output = capsys.readouterr().out
    assert "not installing either AO hook" in output
    assert not os.path.exists(pre_commit)
    assert open(pre_push, encoding="utf-8").read() == foreign

    assert cli.cmd_hooks(project, SimpleNamespace(action="uninstall")) == 0
    assert not os.path.exists(pre_commit)
    assert open(pre_push, encoding="utf-8").read() == foreign


def test_verdict_without_counts_is_invalid_and_masks_older_approval(project, tmp_path):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"], cwd=root, check=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 2\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)
    candidate = A.index_candidate(root)
    scope = A.candidate_scope(candidate)
    older = os.path.join(root, "semantic-review", "older-approved.md")
    open(older, "w", encoding="utf-8").write(
        "# Review older-approved.md\n\n"
        + A.review_evidence_line({
            "schema": 2,
            "kind": "index-candidate",
            "authorizable": True,
            "candidate": candidate,
            "scope": scope,
            "diff_digest": "sha256:older",
        })
        + "\n\nVERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n"
    )
    os.utime(older, (1, 1))
    assert A.latest_candidate_review(root, "semantic-review", candidate["digest"])[0] == "older-approved.md"

    import sys
    cfg = dict(project, reviewer={"id": "r", "family": "x", "argv": [sys.executable, "-c", "print('VERDICT: APPROVED')", "{prompt}"]})
    assert cli.cmd_review(cfg, SimpleNamespace(boundary="b", timeout=30, paths=None, commits=None)) == 3

    reviews = A.reviews(root, "semantic-review")
    assert reviews[0][1] == "INVALID"
    body = open(os.path.join(root, "semantic-review", reviews[0][0]), encoding="utf-8").read()
    evidence = A.review_evidence(body)
    assert evidence["kind"] == "index-candidate"
    assert evidence["candidate"] == candidate
    assert evidence["scope"] == scope
    assert evidence["authorizable"] is False
    assert evidence["invalid_reasons"] == ["reviewer returned no valid verdict/count schema"]
    assert f"- candidate: `{candidate['digest']}`" in body
    assert f"- index-tree: `{candidate['index_tree']}`" in body
    assert f"- scope: `{scope['kind']}` `{scope['digest']}`" in body
    assert A.latest_candidate_review(root, "semantic-review", candidate["digest"]) is None


def test_catchup_reviews_the_landed_range_and_closes_the_waiver(project, tmp_path, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"], cwd=root, check=True)
    w = A.waive(root, "review", "B7", "quota", by="h")
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("x = 2\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "b7"], cwd=root, check=True)
    import sys
    cfg = dict(project, reviewer={"id": "r", "family": "x", "argv": [sys.executable, "-c",
                                  "print('BLOCKER: 0'); print('HIGH: 0'); print('MEDIUM: 0'); print('LOW: 0'); print('VERDICT: APPROVED')", "{prompt}"]})
    from ao import watchdog as W
    monkeypatch.setattr(W, "run", lambda ns: 0)
    assert cli.cmd_catchup(cfg, SimpleNamespace(boundary=None)) == 0
    assert A.open_waivers(root) == []
    body = open(os.path.join(root, "semantic-review", os.listdir(os.path.join(root, "semantic-review"))[0]), encoding="utf-8").read()
    assert "- commits:" in body and "VERDICT: APPROVED" in body
