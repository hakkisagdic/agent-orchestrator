import os
import sys
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_commit_authority import _allow_commit_prerequisites
from tests.test_review_chain import _args, _fake, _repo_with_change

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")


def _only_review(root):
    names = os.listdir(os.path.join(root, "semantic-review"))
    assert len(names) == 1, names
    path = os.path.join(root, "semantic-review", names[0])
    return names[0], path, open(path, encoding="utf-8").read()


def _commit_ok(cfg, monkeypatch, capsys):
    root = cfg["root"]
    _allow_commit_prerequisites(monkeypatch, A.tree_digest(root, cfg), A.index_candidate(root))
    capsys.readouterr()
    code = cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None))
    return code, capsys.readouterr().out


def _as_implementer(cfg, session):
    return dict(cfg, implementer={"adapter": "kiro", "session": session, "name": "kiro"})


def test_a_boundary_naming_another_reviewer_does_not_change_who_reviewed(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})
    boundary = "acceptance: reviewer: `independent-auditor`  family: `other` must approve"

    assert cli.cmd_review(cfg, _args(boundary=boundary)) == 0
    _, _, body = _only_review(root)
    evidence = A.review_evidence(body)
    assert evidence["reviewer"] == {"id": "r1", "family": "x", "fallback": False}
    assert evidence["verdict"] == "APPROVED"
    assert evidence["counts"] == {"BLOCKER": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}

    code, out = _commit_ok(_as_implementer(cfg, "r1"), monkeypatch, capsys)
    assert code == 1
    assert "the review was written by the implementer (r1)" in out
    assert A.latest_authority_decision(root)["granted"] is False


def test_a_reviewer_that_resumes_the_implementer_is_never_run_whatever_its_label(project, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    ran = tmp_path / "ran"
    script = f"open({str(ran)!r}, 'w').write('ran'); " + "; ".join(f"print({line!r})" for line in APPROVED)

    primary = dict(project, reviewer={"id": "independent-auditor", "family": "other",
                                      "argv": [sys.executable, "-c", script, "{prompt}", "--resume-id", "s1"]})
    assert cli.cmd_review(primary, _args()) == 2
    assert not ran.exists() and os.listdir(os.path.join(root, "semantic-review")) == []

    fallback = dict(project, reviewer={
        "id": "r1", "family": "x", "argv": _fake("down", exit_code=17),
        "fallbacks": [{"id": "independent-auditor", "family": "other",
                       "argv": [sys.executable, "-c", script, "{prompt}", "--session=s1"]}],
    })
    assert cli.cmd_review(fallback, _args()) == 3
    assert not ran.exists()
    _, _, body = _only_review(root)
    assert A.reviews(root, "semantic-review")[0][1] == "UNAVAILABLE"
    assert "independent-auditor" not in body


def test_commit_ok_refuses_a_recorded_reviewer_whose_command_resumes_the_implementer(
    project, monkeypatch, capsys
):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "independent-auditor", "family": "other", "argv": _fake(*APPROVED)})
    assert cli.cmd_review(cfg, _args()) == 0

    resumed = dict(cfg, reviewer=dict(cfg["reviewer"], argv=cfg["reviewer"]["argv"] + ["--resume-id", "s1"]))
    code, out = _commit_ok(resumed, monkeypatch, capsys)
    assert code == 1 and "the review was written by the implementer (independent-auditor)" in out


def test_a_session_id_inside_another_id_is_not_the_same_actor(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "s10", "family": "x", "argv": _fake(*APPROVED)})

    assert cli.cmd_review(cfg, _args()) == 0
    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 0 and "GRANTED" in out
    assert A.latest_authority_decision(root)["reviewer"] == "s10"


def test_an_approval_that_records_no_reviewer_grants_nothing(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})
    assert cli.cmd_review(cfg, _args()) == 0
    name, path, body = _only_review(root)
    evidence = A.review_evidence(body)
    unnamed = dict(evidence)
    del unnamed["reviewer"]
    open(path, "w", encoding="utf-8").write(
        body.replace(A.review_evidence_line(evidence), A.review_evidence_line(unnamed), 1))
    A.record_review(root, name, open(path, "rb").read(), unnamed, "APPROVED")

    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 1 and f"{name} records no reviewer in its evidence" in out


def test_a_margin_verdict_edited_away_from_the_evidence_is_invalid(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(
        "VERDICT: NEEDS_CHANGES", "BLOCKER: 1", "HIGH: 0", "MEDIUM: 0", "LOW: 0",
        "- [BLOCKER] src/a.py:1 — a real defect")})
    assert cli.cmd_review(cfg, _args()) == 1
    name, path, body = _only_review(root)
    open(path, "w", encoding="utf-8").write(
        body.replace("\nVERDICT: NEEDS_CHANGES\nBLOCKER: 1\n", "\nVERDICT: APPROVED\nBLOCKER: 0\n", 1))
    A.record_review(root, name, open(path, "rb").read(), A.review_evidence(body), "APPROVED")

    assert A.reviews(root, "semantic-review") == [(name, "INVALID")]
    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 1 and "no APPROVED prospective review is bound to this staged candidate" in out
