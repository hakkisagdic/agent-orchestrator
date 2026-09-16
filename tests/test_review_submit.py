import json
import os
import re
import subprocess
import time
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_review_chain import _fake, _repo_with_change

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")


def _args(**kw):
    base = dict(action=None, rid=None, any=False, run=None, boundary="b", paths=None, commits=None, timeout=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _submit(cfg, monkeypatch, spawned):
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root, rid: spawned.append(rid))
    return cli.cmd_review(cfg, _args(action="submit"))


def _state(root, rid, **fields):
    state = dict({"id": rid, "state": "running", "tree": "t" * 40, "candidate": "sha256:x", "slice": None,
                  "submitted_at": int(time.time())}, **fields)
    cli._write_review_state(root, state)
    return state


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def test_submit_pins_the_staged_tree_and_returns_at_once(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    spawned = []

    assert _submit(project, monkeypatch, spawned) == 0

    (rid,) = spawned
    state = cli._review_state(root, rid)
    assert rid in _plain(capsys)
    assert state["state"] == "running" and state["tree"] == A.index_candidate(root)["index_tree"]
    assert os.path.exists(state["index"])


def test_the_run_reviews_the_pinned_tree_while_the_implementer_moves_on(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})
    spawned = []
    assert _submit(cfg, monkeypatch, spawned) == 0
    pinned = cli._review_state(root, spawned[0])

    # The implementer stages its next change before the review has run.
    open(os.path.join(root, "src", "b.py"), "w", encoding="utf-8").write("y = 1\n")
    subprocess.run(["git", "add", "src/b.py"], cwd=root, check=True)
    assert cli.cmd_review(cfg, _args(run=spawned[0], boundary=None)) == 0

    state = cli._review_state(root, spawned[0])
    assert state["state"] == "finished" and state["verdict"] == "APPROVED"
    assert A.latest_candidate_review(root, "semantic-review", pinned["candidate"]) is not None
    assert "GIT_INDEX_FILE" not in os.environ and not os.path.exists(pinned["index"])


def test_submits_beyond_the_limit_are_refused_naming_what_to_collect(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    _state(root, "R-1", pid=os.getpid())
    _state(root, "R-2", pid=os.getpid())

    assert _submit(project, monkeypatch, []) == 2

    assert "collect first: R-1, R-2" in _plain(capsys)


def test_one_worktree_holds_one_slice_in_flight(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    _state(root, "R-7", pid=os.getpid(), slice="S9")

    assert _submit(project, monkeypatch, []) == 2

    assert "R-7 is in flight in this worktree for slice S9" in _plain(capsys)


def test_collect_takes_a_finished_review_once_and_never_waits(project, capsys):
    root = project["root"]
    _state(root, "R-5", state="finished", verdict="NEEDS_CHANGES", artefact="r.md", finished_at=int(time.time()))

    assert cli.cmd_review(project, _args(action="collect", any=True)) == 0
    assert "R-5" in _plain(capsys) and cli._review_state(root, "R-5")["collected_at"]
    assert cli.cmd_review(project, _args(action="collect", any=True)) == 1
    assert "nothing finished to collect" in _plain(capsys)


def test_commit_ok_on_a_submitted_review_refuses_another_tree(project, capsys):
    root = project["root"]
    _repo_with_change(root)
    _state(root, "R-9", state="finished", verdict="APPROVED", tree="0" * 40)

    assert cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None, review="R-9")) == 1

    out = _plain(capsys)
    assert A.index_candidate(root)["index_tree"] in out and "reviewed " + "0" * 40 in out


def test_ao_reviews_lists_state_and_names_a_lost_runner(project, capsys):
    root = project["root"]
    _state(root, "R-11", pid=2 ** 22 + 12345, submitted_at=int(time.time()) - 300)
    _state(root, "R-12", state="finished", verdict="APPROVED", finished_at=int(time.time()))

    assert cli.cmd_reviews(project, SimpleNamespace()) == 0

    out = _plain(capsys)
    assert re.search(r"R-11\s+lost", out) and re.search(r"R-12\s+finished.*APPROVED", out)
