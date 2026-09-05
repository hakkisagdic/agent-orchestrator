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


def test_waiver_is_honoured_by_commit_ok_and_recorded(project, monkeypatch, capsys):
    root = project["root"]
    b = os.path.join(root, ".ao", "board.md")
    t = open(b).read()
    open(b, "w").write(t.replace("## running\n", "## running\n- [B7] slice · since: 2026-09-05 10:00\n"))
    now = A.tree_digest(root)
    monkeypatch.setattr(A, "latest_verification", lambda r: {"id": "V-1", "passed": True, "tree": now, "gates": []})
    monkeypatch.setattr(A, "plan_drift", lambda r: [])
    monkeypatch.setattr(A, "hold_state", lambda r: None)
    monkeypatch.setattr(A, "urgent_messages", lambda *a, **k: [], raising=False)
    args = SimpleNamespace(verify=False, profile=None)
    cli.cmd_commit_ok(project, args)
    out = capsys.readouterr().out
    assert "no review found" in out
    w = A.waive(root, "review", "B7", "reviewer at quota; human accepts the risk", by="hakki")
    assert A.open_waivers(root, gate="review", slice_id="B7")[0]["id"] == w["id"]
    cli.cmd_commit_ok(project, args)
    out = capsys.readouterr().out
    assert "no review found" not in out and "review waived" in out
    A.close_waiver(root, w["id"], "reviewed")
    assert A.open_waivers(root) == []


def test_review_feature_off_needs_no_review(project, monkeypatch, capsys):
    root = project["root"]
    F.set_switch(root, "review", False)
    cfg = A.load_config(root)
    now = A.tree_digest(root)
    monkeypatch.setattr(A, "latest_verification", lambda r: {"id": "V-1", "passed": True, "tree": now, "gates": []})
    monkeypatch.setattr(A, "plan_drift", lambda r: [])
    monkeypatch.setattr(A, "hold_state", lambda r: None)
    cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None))
    assert "no review found" not in capsys.readouterr().out


def test_burn_rate_projects_exhaustion(project):
    root = project["root"]
    now = 1_800_000_000
    reset = now + 20 * 86400
    A.record_credit_sample(root, 6000, 10000, reset)
    p = os.path.join(root, ".ao", "ledger", "credits.jsonl")
    rows = [json.loads(l) for l in open(p)]
    rows[0]["at"] = now - 2 * 86400
    rows.append({"at": now, "used": 7000, "limit": 10000, "reset_at": reset})
    open(p, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
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
    open(mine, "w").write("x")
    open(theirs, "w").write("y")
    tr = tmp_path / "t.jsonl"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
    tr.write_text(json.dumps({"timestamp": stamp, "payload": {"type": "tool_call", "toolName": "fs_write",
                                                             "args": {"path": mine}}}) + "\n")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(tr), None))
    assert A.foreign_edits(root, project) == ["src/theirs.py"]


def test_push_window_and_hook(project, capsys):
    root = project["root"]
    assert cli.cmd_push(project, SimpleNamespace(action="check", minutes=30)) == 1
    cli.cmd_push(project, SimpleNamespace(action="allow", minutes=30))
    assert cli.cmd_push(project, SimpleNamespace(action="check", minutes=30)) == 0
    assert cli.cmd_hooks(project, SimpleNamespace(action="install")) == 0
    hook = os.path.join(root, ".git", "hooks", "pre-push")
    assert os.path.exists(hook) and "push check" in open(hook).read() and os.access(hook, os.X_OK)
    cli.cmd_hooks(project, SimpleNamespace(action="uninstall"))
    assert not os.path.exists(hook)


def test_verdict_without_counts_is_invalid(project, tmp_path):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w").write("x = 1\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"], cwd=root, check=True)
    open(os.path.join(root, "src", "a.py"), "w").write("x = 2\n")
    import sys
    cfg = dict(project, reviewer={"id": "r", "family": "x", "argv": [sys.executable, "-c", "print('VERDICT: APPROVED')", "{prompt}"]})
    assert cli.cmd_review(cfg, SimpleNamespace(boundary="b", timeout=30, paths=None, commits=None)) == 3
    assert A.reviews(root, "semantic-review")[0][1] == "INVALID"


def test_catchup_reviews_the_landed_range_and_closes_the_waiver(project, tmp_path, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w").write("x = 1\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"], cwd=root, check=True)
    w = A.waive(root, "review", "B7", "quota", by="h")
    open(os.path.join(root, "src", "a.py"), "w").write("x = 2\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "b7"], cwd=root, check=True)
    import sys
    cfg = dict(project, reviewer={"id": "r", "family": "x", "argv": [sys.executable, "-c",
                                  "print('BLOCKER: 0'); print('HIGH: 0'); print('MEDIUM: 0'); print('LOW: 0'); print('VERDICT: APPROVED')", "{prompt}"]})
    from ao import watchdog as W
    monkeypatch.setattr(W, "run", lambda ns: 0)
    assert cli.cmd_catchup(cfg, SimpleNamespace(boundary=None)) == 0
    assert A.open_waivers(root) == []
    body = open(os.path.join(root, "semantic-review", os.listdir(os.path.join(root, "semantic-review"))[0])).read()
    assert "- commits:" in body and "VERDICT: APPROVED" in body
