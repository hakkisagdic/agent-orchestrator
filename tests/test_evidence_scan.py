import hashlib
import json
import os
from types import SimpleNamespace

from ao import cli, lib as A
from ao.storage import read_chained_jsonl

# Built at run time, so no credential-shaped literal sits in this file.
ANTHROPIC = "s" + "k-ant-" + "api03-" + "Q7" * 14
GITHUB = "gh" + "p_" + "Zx9" * 9
AWS = "AK" + "IA" + "QZ7XW3PLM2KD9RTA"


def test_a_review_artefact_is_written_and_recorded_scanned(project):
    root = project["root"]
    body = f"VERDICT: NEEDS_CHANGES\n\nthe reviewer echoed a key: {ANTHROPIC}\n"

    A.write_review_artefact(root, "semantic-review", "r.md", body, evidence={}, verdict="NEEDS_CHANGES")

    data = open(os.path.join(root, "semantic-review", "r.md"), "rb").read()
    assert ANTHROPIC.encode() not in data and b"[redacted:anthropic-key]" in data
    (row,) = read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)
    assert row["sha256"] == "sha256:" + hashlib.sha256(data).hexdigest()


def test_mail_is_written_scanned(project):
    root = project["root"]
    name = A.write_mail(root, project, "20260916-1200-kiro-to-fable-INFO-x.md",
                        f"# token in the report\n\nexport GITHUB_TOKEN={GITHUB}\n", {"kind": "info"})

    text = open(os.path.join(root, project["mailbox"], name), encoding="utf-8").read()
    assert GITHUB not in text and "[redacted:github-token]" in text


def test_decision_records_are_written_scanned(project, capsys):
    root = project["root"]
    decision = A.ask(root, f"rotate the key {AWS}?", ["yes", "no"], context=f"password = {GITHUB}")
    A.answer(root, decision["id"], f"x rotated; new one is {ANTHROPIC}")
    cli.cmd_decide(project, SimpleNamespace(list=False, n=10, decision=f"use {ANTHROPIC}", why=None, scope=None,
                                            answers=None, urgent=False, to="kiro"))

    stored = open(os.path.join(root, A.DECISION_DIR, decision["id"] + ".json"), encoding="utf-8").read()
    ledger = open(A.decisions_path(root), encoding="utf-8").read()
    for text in (stored, ledger):
        assert AWS not in text and GITHUB not in text and ANTHROPIC not in text
    assert "[redacted:aws-access-key]" in stored and "[redacted:anthropic-key]" in ledger


def test_verification_records_are_written_scanned(project):
    root = project["root"]
    A.record_verification(root, {"id": "V-1", "passed": False, "gates": [
        {"name": "test", "detail": f"Authorization: Bearer {GITHUB}{GITHUB}", "exit": 1}]})

    text = open(os.path.join(root, ".ao", "ledger", "verifications.jsonl"), encoding="utf-8").read()
    assert GITHUB not in text and "[redacted:" in text


def test_digests_and_prose_are_left_alone():
    digest = "sha256:" + "ab12" * 16
    commit = "0f" * 20
    text = f"candidate {digest} at {commit}; the password field is validated"

    assert A.scan_evidence(text) == (text, [])
