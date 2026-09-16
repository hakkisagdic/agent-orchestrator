import os
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage
from tests.test_commit_authority import _allow_commit_prerequisites
from tests.test_review_chain import _args, _fake, _repo_with_change

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")
REJECTED = ("VERDICT: NEEDS_CHANGES", "BLOCKER: 1", "HIGH: 0", "MEDIUM: 0", "LOW: 0",
            "- [BLOCKER] src/a.py:1 — the defect")


def _reviewer(lines, **extra):
    return {"id": "r1", "family": "x", "argv": _fake(*lines), **extra}


def _review(project, reviewer, **args):
    cfg = dict(project, reviewer=reviewer)
    code = cli.cmd_review(cfg, _args(**args))
    row = storage.read_chained_jsonl(A.review_ledger_path(project["root"]), A.REVIEW_CHAIN)[-1]
    return cfg, code, row


def _artefact(project, row):
    return os.path.join(project["root"], project["reviews"], row["artefact"])


def _commit_ok(cfg, monkeypatch, capsys):
    root = cfg["root"]
    _allow_commit_prerequisites(monkeypatch, A.tree_digest(root, cfg), A.index_candidate(root))
    capsys.readouterr()
    code = cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None))
    return code, capsys.readouterr().out


def _approved_then_rejected(project):
    _repo_with_change(project["root"])
    cfg, code, approval = _review(project, _reviewer(APPROVED))
    assert code == 0
    _, code, rejection = _review(project, _reviewer(REJECTED))
    assert code == 1
    return cfg, approval, rejection


def test_every_review_is_recorded_in_order_with_the_bytes_written(project):
    root = project["root"]
    _repo_with_change(root)
    _review(project, _reviewer(("down",)) | {"argv": _fake("down", exit_code=17)})
    _review(project, _reviewer(("no schema here",)))
    _review(project, _reviewer(APPROVED))

    rows = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)
    assert [row["verdict"] for row in rows] == ["UNAVAILABLE", "INVALID", "APPROVED"]
    assert len({row["artefact"] for row in rows}) == 3
    candidate = A.index_candidate(root)["digest"]
    for row in rows:
        data = open(_artefact(project, row), "rb").read()
        assert row["sha256"] == "sha256:" + __import__("hashlib").sha256(data).hexdigest()
        assert row["candidate"] == candidate and row["fallback"] is False


@pytest.mark.parametrize("damage", ["emptied", "cut-short", "deleted"])
def test_a_damaged_newest_review_refuses_instead_of_reaching_the_older_approval(
    project, monkeypatch, capsys, damage
):
    cfg, approval, rejection = _approved_then_rejected(project)
    path = _artefact(project, rejection)
    if damage == "deleted":
        os.remove(path)
    else:
        data = open(path, "rb").read()
        open(path, "wb").write(b"" if damage == "emptied" else data[: len(data) // 2])

    assert A.latest_candidate_review(project["root"], project["reviews"],
                                     A.index_candidate(project["root"])["digest"]) is None
    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 1
    assert f"the newest review of this candidate, {rejection['artefact']}" in out


def test_touching_an_older_approval_does_not_make_it_the_newest(project, monkeypatch, capsys):
    cfg, approval, _ = _approved_then_rejected(project)
    later = time.time() + 3600
    os.utime(_artefact(project, approval), (later, later))

    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 1 and "no APPROVED prospective review is bound to this staged candidate" in out


def test_a_fallback_approval_cannot_supersede_a_rejection_but_the_primary_can(project, monkeypatch, capsys):
    cfg, _, rejection = _approved_then_rejected(project)
    down_then_fallback = _reviewer(("down",)) | {
        "argv": _fake("down", exit_code=17),
        "fallbacks": [{"id": "r2", "family": "y", "argv": _fake(*APPROVED)}],
    }
    cfg, code, row = _review(project, down_then_fallback)
    assert code == 0 and row["fallback"] is True and row["verdict"] == "APPROVED"

    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 1
    assert f"cannot supersede the rejection of this candidate in {rejection['artefact']}" in out

    cfg, code, row = _review(project, _reviewer(APPROVED))
    assert code == 0 and row["fallback"] is False
    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 0 and "GRANTED" in out


def test_a_timeout_on_the_command_line_cannot_starve_the_primary(project, monkeypatch):
    root = project["root"]
    # Registering a reviewer reads the process table, on Windows a PowerShell query that can
    # outlast the one-second timeout this primary is given (#71).
    monkeypatch.setattr(A, "helper_register", lambda *args, **kwargs: None)
    monkeypatch.setattr(A, "helper_release", lambda *args, **kwargs: None)
    _repo_with_change(root)
    slow = [sys.executable, "-c",
            "import time; time.sleep(2); " + "; ".join(f"print({line!r})" for line in APPROVED), "{prompt}"]
    chain = {"id": "r1", "family": "x", "argv": slow,
             "fallbacks": [{"id": "r2", "family": "y", "argv": _fake(*APPROVED)}]}

    _, code, row = _review(project, chain, timeout=1)
    assert code == 0 and row["reviewer"] == "r1" and row["fallback"] is False

    configured = dict(project, reviewer=chain, review_timeout=1)
    assert cli.cmd_review(configured, _args()) == 0
    row = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)[-1]
    assert row["reviewer"] == "r2" and row["fallback"] is True
