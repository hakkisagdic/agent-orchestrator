"""A boundary that lists criteria is judged criterion by criterion, and lands only when each is met (CRITERIA-VERDICTS).

A review returned one verdict for a whole boundary, so a boundary of several conditions could be approved with one of
them never examined, and nothing on the record said which. The criteria a boundary lists - numbered, or separated by
semicolons on one line - are now each asked for in a note after the prompt, each recorded in the review's evidence
with the reviewer's evidence, and `ao commit-ok` refuses a review that did not find every one met, naming each one
that is not. A single sentence lists no criteria, and is reviewed, recorded and granted exactly as it was.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, language, lib as A, storage
from tests import test_commit_authority as authority
from tests.test_review_chain import _args, _fake, _repo_with_change

APPROVED = ["VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0"]
THREE = "the store is durable; a restart replays it; a duplicate is refused"
NUMBERED = ("the claim journal:\n1. a claim is journalled before admission\n"
            "2. a duplicate delivery is refused with the first id\n3. a restart replays the journal")
BOUNDARY_FILE = ("# S1 - the claim journal\n## Invariant\nA claim is journalled before it is admitted.\n"
                 "## Scenarios\n1. a duplicate delivery is refused with the first id\n"
                 "2. a restart replays the journal in order\n"
                 "## Out of scope\n1. the public claim API stays as it is\n")
SECTION_MARKERS = "|".join(re.escape(marker) for marker in language.TEXTS["prompt.review-section"].values())
# A reviewer that keeps every prompt it is handed and approves, adding the criterion lines AO_TEST_ANSWERS holds for
# the question it was asked: a scenario's section by its name, a review asked as one question by "whole".
REVIEWER = f"MARKERS = {SECTION_MARKERS!r}\nAPPROVED = {APPROVED!r}\n" + """
import json, os, re, sys
prompt = sys.argv[1]
with open(os.environ["AO_TEST_PROMPTS"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(prompt) + "\\n")
section = re.search(r"Scenario \\d+", re.split(MARKERS, prompt)[-1]) if re.search(MARKERS, prompt) else None
lines = json.loads(os.environ["AO_TEST_ANSWERS"]).get(section.group(0) if section else "whole", [])
print("\\n".join(APPROVED + lines))
"""


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _running(root, row):
    with open(os.path.join(root, ".ao", "board.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Board\n\n## running\n{row}\n\n## blocked\n\n## queued\n\n## verified\n\n## done\n")


def _reviewed(project, tmp_path, monkeypatch, row, answers):
    """(root, cfg, the prompts handed so far) for a staged change, the running slice `row`, and a reviewer answering
    each question with the criterion lines `answers` names for it."""
    root = project["root"]
    _repo_with_change(root)
    _running(root, row)
    prompts = tmp_path / "prompts.jsonl"
    monkeypatch.setenv("AO_TEST_PROMPTS", str(prompts))
    monkeypatch.setenv("AO_TEST_ANSWERS", json.dumps(answers))
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [sys.executable, "-c", REVIEWER, "{prompt}"]})

    def handed():
        return [json.loads(line) for line in prompts.read_text(encoding="utf-8").splitlines()] \
            if prompts.exists() else []
    return root, cfg, handed


def _newest(root):
    """The newest review's artefact, by the ledger's order: two written in one second sort `-2.md` first by name."""
    (row, *_) = reversed(storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN))
    return Path(root, "semantic-review", row["artefact"]).read_text(encoding="utf-8")


def _note(cfg, criteria):
    listing = "\n".join(f"{number}. {text}" for number, text in enumerate(criteria, 1))
    return f"\n\n{language.text(cfg, 'prompt.review-criteria', criteria=listing)}\n"


def _commit_ok(cfg, monkeypatch, capsys):
    root = cfg["root"]
    authority._allow_commit_prerequisites(monkeypatch, A.tree_digest(root, cfg), A.index_candidate(root))
    capsys.readouterr()
    code = cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None, review=None))
    return code, _plain(capsys)


# ---- the criteria a boundary lists --------------------------------------------------------------

@pytest.mark.parametrize("boundary,criteria", [
    (NUMBERED, ["a claim is journalled before admission", "a duplicate delivery is refused with the first id",
                "a restart replays the journal"]),
    ("the journal: 1) a claim is journalled 2) a restart replays it (3) a duplicate is refused",
     ["a claim is journalled", "a restart replays it", "a duplicate is refused"]),
    (THREE + "; `ao review --boundary 'a; b'` stays one criterion",
     ["the store is durable", "a restart replays it", "a duplicate is refused",
      "`ao review --boundary 'a; b'` stays one criterion"]),
])
def test_a_boundary_lists_its_criteria_numbered_or_separated_by_semicolons(boundary, criteria):
    listed = A.boundary_criteria(boundary)

    assert [criterion["text"] for criterion in listed] == criteria
    assert [criterion["id"] for criterion in listed] == list(range(1, len(criteria) + 1))


@pytest.mark.parametrize("boundary", [
    "the store is durable", "the store is durable, and a restart replays it", "not declared — say so as a finding",
    "1. a boundary with one numbered line", "upgrade to 3.9, then 2. is not a list", "", None])
def test_a_single_sentence_lists_no_criteria(boundary):
    assert A.boundary_criteria(boundary) == []


def test_a_boundary_files_criteria_are_its_scenarios_at_its_commit_never_its_out_of_scope_or_a_later_change(project):
    root = project["root"]
    os.makedirs(os.path.join(root, "docs", "slices"))
    Path(root, "docs", "slices", "S1.md").write_text(BOUNDARY_FILE, encoding="utf-8")
    authority._git(root, "add", "docs")
    authority._git(root, "commit", "-q", "-m", "boundary")
    pinned = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True,
                            text=True).stdout.strip()[:12]
    Path(root, "docs", "slices", "S1.md").write_text(BOUNDARY_FILE + "3. a scenario added after the pin\n",
                                                     encoding="utf-8")
    authority._git(root, "commit", "-q", "-am", "the boundary moves")
    _running(root, f"- [S1] the claim journal · boundary: docs/slices/S1.md@{pinned}")

    source = A.read_boundary(root, A.running_slice(root))

    assert "+3. a scenario added after the pin" in source["text"]          # the change still reaches the reviewer
    assert A.boundary_criteria(source["label"], source) == [
        {"id": 1, "text": "a duplicate delivery is refused with the first id"},
        {"id": 2, "text": "a restart replays the journal in order"}]


# ---- a verdict for each criterion ---------------------------------------------------------------

def test_a_criterion_is_judged_at_an_answers_margin_with_evidence_and_one_not_met_stands_over_the_rest():
    criteria = A.boundary_criteria("a is done; b is done; c is done; d is done; e is done; f is done")
    answers = ["\n".join(APPROVED + ["CRITERION 1: **MET** — `a**b` is kept as the reviewer wrote it",
                                     "    CRITERION 2: MET — an indented line decides nothing",
                                     "- **CRITERION 3:** met - a bullet and bold are read",
                                     "CRITERION 4: MET",
                                     "CRITERION 9: MET — a number the boundary does not have",
                                     "CRITERION 6: MET"]),
               "CRITERION 3: NOT MET: another section found it broken\nCRITERION 5: NOT MET\n"
               "CRITERION 6: MET - another section gave the evidence"]

    verdicts = A.criterion_verdicts(criteria, answers)

    assert [(v["id"], v["verdict"], v["evidence"]) for v in verdicts] == [
        (1, "met", "`a**b` is kept as the reviewer wrote it"),
        (2, None, ""),                                                  # indented: nothing was said
        (3, "not met", "another section found it broken"),              # one section's NOT MET stands
        (4, None, "it was said to be met with no evidence"),            # and the record says why it is none
        (5, "not met", ""),
        (6, "met", "another section gave the evidence"),
    ]
    assert A.criteria_refusals({"criteria": verdicts}) == [
        "criterion 2 has no verdict: b is done",
        "criterion 3 is not met: c is done — another section found it broken",
        "criterion 4 has no verdict: d is done — it was said to be met with no evidence",
        "criterion 5 is not met: e is done"]


def test_each_criterion_is_asked_after_the_prompt_and_what_the_review_found_of_it_is_recorded(
        project, tmp_path, monkeypatch, capsys):
    root, cfg, handed = _reviewed(project, tmp_path, monkeypatch, f"- [S1] the store · acceptance: {THREE}", {
        "whole": ["CRITERION 1: MET — tests/test_store.py::test_durable",
                  "CRITERION 2: NOT MET — the replay skips the last entry"]})

    assert cli.cmd_review(cfg, _args(boundary=None)) == 0          # the verdict is still the counts'

    (prompt,) = handed()
    note = _note(cfg, ["the store is durable", "a restart replays it", "a duplicate is refused"])
    assert prompt.endswith(f"\n\n{language.text(cfg, 'prompt.review-tree')}\n" + note)
    body = _newest(root)
    evidence = A.review_evidence(body)
    assert evidence["verdict"] == "APPROVED" and evidence["criteria"] == [
        {"id": 1, "text": "the store is durable", "verdict": "met",
         "evidence": "tests/test_store.py::test_durable"},
        {"id": 2, "text": "a restart replays it", "verdict": "not met", "evidence": "the replay skips the last entry"},
        {"id": 3, "text": "a duplicate is refused", "verdict": None, "evidence": ""}]
    assert "- criterion 2 (a restart replays it): not met — the replay skips the last entry" in body.splitlines()
    assert "- criterion 3 (a duplicate is refused): no verdict" in body.splitlines()
    out = _plain(capsys)
    assert "2 of 3 not met or not judged" in out and "criterion 3 has no verdict: a duplicate is refused" in out


def test_commit_ok_refuses_a_criterion_not_met_or_never_judged_and_names_each(project, tmp_path, monkeypatch, capsys):
    root, cfg, _ = _reviewed(project, tmp_path, monkeypatch, f"- [S1] the store · acceptance: {THREE}", {
        "whole": ["CRITERION 1: MET — tests/test_store.py::test_durable",
                  "CRITERION 2: NOT MET — the replay skips the last entry"]})
    assert cli.cmd_review(cfg, _args(boundary=None)) == 0

    code, out = _commit_ok(cfg, monkeypatch, capsys)

    assert code == 1 and "REFUSED" in out and "GRANTED" not in out
    assert "criterion 2 is not met: a restart replays it — the replay skips the last entry" in out
    assert "criterion 3 has no verdict: a duplicate is refused" in out
    assert "criterion 1 " not in out
    (refusal,) = A.authority_rows(root)
    assert refusal["granted"] is False and any("criterion 3 has no verdict" in reason for reason in refusal["reasons"])


def test_commit_ok_grants_when_the_review_found_every_criterion_met(project, tmp_path, monkeypatch, capsys):
    _, cfg, _ = _reviewed(project, tmp_path, monkeypatch, f"- [S1] the store · acceptance: {THREE}", {
        "whole": ["CRITERION 1: MET — tests/test_store.py::test_durable",
                  "CRITERION 2: MET — tests/test_store.py::test_replay",
                  "CRITERION 3: MET — tests/test_store.py::test_duplicate"]})
    assert cli.cmd_review(cfg, _args(boundary=None)) == 0

    code, out = _commit_ok(cfg, monkeypatch, capsys)

    assert code == 0 and "GRANTED" in out


def test_in_a_sectioned_review_a_criterion_is_met_when_a_section_judged_it_and_none_found_it_not_met(
        project, tmp_path, monkeypatch):
    root, cfg, handed = _reviewed(project, tmp_path, monkeypatch, "- [S1] the claim journal", {
        "Scenario 1": ["CRITERION 1: MET — journal.py:10 writes before it admits"],
        "Scenario 2": ["CRITERION 2: MET — tests/test_journal.py::test_duplicate"],
        "Scenario 3": ["CRITERION 3: MET — tests/test_journal.py::test_replay",
                       "CRITERION 1: NOT MET — a crash between the write and the fsync loses the claim"]})

    assert cli.cmd_review(cfg, _args(boundary=NUMBERED)) == 0

    note = _note(cfg, ["a claim is journalled before admission", "a duplicate delivery is refused with the first id",
                       "a restart replays the journal"])
    assert [prompt.count(note) for prompt in handed()] == [1, 1, 1]      # every section is handed the criteria
    evidence = A.review_evidence(_newest(root))
    assert [section["section"] for section in evidence["sections"]] == ["scenario:1", "scenario:2", "scenario:3"]
    assert [(c["id"], c["verdict"], c["evidence"]) for c in evidence["criteria"]] == [
        (1, "not met", "a crash between the write and the fsync loses the claim"),
        (2, "met", "tests/test_journal.py::test_duplicate"),
        (3, "met", "tests/test_journal.py::test_replay")]


def test_a_single_sentence_boundary_is_reviewed_recorded_and_granted_as_before(project, tmp_path, monkeypatch, capsys):
    root, cfg, handed = _reviewed(project, tmp_path, monkeypatch, "- [S1] the store · acceptance: the store is durable",
                                  {"whole": []})

    assert cli.cmd_review(cfg, _args(boundary=None)) == 0

    (prompt,) = handed()
    assert prompt.endswith(f"\n\n{language.text(cfg, 'prompt.review-tree')}\n") and "CRITERION" not in prompt
    body = _newest(root)
    assert "criteria" not in A.review_evidence(body) and "- criterion" not in body
    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 0 and "GRANTED" in out and "criteri" not in out


def test_commit_check_holds_a_grant_to_the_criteria_its_review_recorded(project, capsys):
    root = project["root"]
    authority._repo_with_change(root)
    authority._persist_exact_candidate_grant(project)
    assert cli.cmd_commit_check(project, SimpleNamespace()) == 0
    # The granted review, recorded again with a criterion it did not find met: the grant no longer stands on it.
    path = os.path.join(root, project["reviews"], "approved.md")
    body = Path(path).read_text(encoding="utf-8")
    evidence = A.review_evidence(body)
    unmet = dict(evidence, criteria=[{"id": 1, "text": "the value is two", "verdict": "not met",
                                      "evidence": "it is still one"}])
    Path(path).write_text(body.replace(A.review_evidence_line(evidence), A.review_evidence_line(unmet), 1),
                          encoding="utf-8")
    A.record_review(root, "approved.md", Path(path).read_bytes(), unmet, "APPROVED")
    capsys.readouterr()

    code = cli.cmd_commit_check(project, SimpleNamespace())

    out = _plain(capsys)
    assert code == 1 and "COMMIT REFUSED" in out
    assert "approved.md: criterion 1 is not met: the value is two — it is still one" in out


# ---- a person and a stand-in answer the criteria too --------------------------------------------

def test_a_person_is_shown_the_criteria_and_answers_them_in_the_findings_file(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    _running(root, f"- [S1] the store · acceptance: {THREE}")
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "s1", "name": "claude", "model": "model-a"})
    args = dict(by="A. Person", verdict=None, digest=None, findings=None, boundary=None, paths=None, commits=None)
    capsys.readouterr()

    assert cli.cmd_person_review(cfg, SimpleNamespace(**args)) == 0

    shown = _plain(capsys)
    assert "it lists 3 criteria" in shown and "    2. a restart replays it" in shown
    assert re.search(r"--verdict APPROVED --digest [0-9a-f]{16} --findings <file>", shown)
    findings = tmp_path / "findings.txt"
    findings.write_text("CRITERION 1: MET - read src/a.py\n  CRITERION 2: MET - an indented line is still mine\n"
                        "CRITERION 3: MET - read the test\nsome words that are no finding\n", encoding="utf-8")
    digest = re.search(r"--digest ([0-9a-f]{16})", shown).group(1)
    assert cli.cmd_person_review(cfg, SimpleNamespace(**dict(args, verdict="APPROVED", digest=digest,
                                                             findings=str(findings)))) == 0
    body = _newest(root)
    assert [(c["verdict"], c["evidence"]) for c in A.review_evidence(body)["criteria"]] == [
        ("met", "read src/a.py"), ("met", "an indented line is still mine"), ("met", "read the test")]
    # Any other line of the file is still kept off the margin, where it could decide something.
    assert [line for line in body.splitlines() if line.strip() == "some words that are no finding"] \
        and not any(line.startswith("some words") for line in body.splitlines())
    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 0 and "GRANTED" in out


def test_a_stand_in_answer_is_read_against_the_criteria_its_request_asked_about(project, tmp_path):
    root = project["root"]
    os.makedirs(os.path.join(root, "docs", "slices"))
    Path(root, "docs", "slices", "S1.md").write_text(BOUNDARY_FILE, encoding="utf-8")
    _repo_with_change(root)                                     # commits the boundary file with the base
    _running(root, "- [S1] the claim journal · boundary: docs/slices/S1.md")
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake("down", exit_code=17)})

    assert cli.cmd_review(cfg, _args(boundary=None)) == 3

    (request,) = sorted(Path(A.review_requests_dir(root)).glob("*.md"))
    nonce = request.stem
    meta = A.review_request(root, nonce)
    criteria = ["a duplicate delivery is refused with the first id", "a restart replays the journal in order"]
    assert _note(cfg, criteria).strip() in request.read_text(encoding="utf-8")
    # The request keeps only the boundary file's name, which lists no criteria; its criteria are kept beside it.
    assert A.boundary_criteria(meta["boundary"]) == [] and [c["text"] for c in meta["criteria"]] == criteria
    answer = tmp_path / "carried.txt"
    answer.write_text("\n".join([f"NONCE: {nonce}"] + APPROVED + [
        "CRITERION 1: MET — refuse() returns the first id", "CRITERION 2: NOT MET — replay() sorts by time"]),
        encoding="utf-8")

    assert cli.cmd_collect_review(cfg, SimpleNamespace(nonce=nonce, response=str(answer), model="model-1",
                                                       by="a person")) == 0

    assert [(c["text"], c["verdict"]) for c in A.review_evidence(_newest(root))["criteria"]] == [
        (criteria[0], "met"), (criteria[1], "not met")]
    rows = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)
    assert rows[-1]["reviewer"] == "human-assisted:model-1"
