import os
import re
import sys
from types import SimpleNamespace

from ao import cli, language, lib as A
from tests.test_review_chain import _repo_with_change

# A section's question follows the section marker, read here in either language (LANGUAGE-PROMPTS).
MARKERS = "|".join(re.escape(marker) for marker in language.TEXTS["prompt.review-section"].values())
REVIEWER = f"MARKERS = {MARKERS!r}\n" + """
import os, re, sys
prompt = sys.argv[1]
section = re.search(r"(Scenario \\d+|Lens `[a-z]+`)", re.split(MARKERS, prompt)[-1])
name = section.group(1) if section else "whole"
open(os.environ["AO_TEST_CALLS"], "a").write(name + "\\n")
if name in os.environ.get("AO_TEST_FAIL", "").split(","):
    sys.exit(1)
high = 1 if name in os.environ.get("AO_TEST_HIGH", "").split(",") else 0
print("VERDICT: " + ("NEEDS_CHANGES" if high else "APPROVED"))
print("BLOCKER: 0"); print("HIGH: %d" % high); print("MEDIUM: 0"); print("LOW: 0")
if high:
    print("- [HIGH] src/a.py:1 — " + name + " is not satisfied")
"""
SCENARIOS = ("the claim journal:\n1. a claim is journalled before admission\n"
             "2. a duplicate delivery is refused with the first id\n3. a restart replays the journal")


def _args(boundary=None):
    return SimpleNamespace(action=None, rid=None, any=False, run=None, boundary=boundary, paths=None, commits=None,
                           timeout=None)


def _setup(project, tmp_path, monkeypatch, board_line, fail="", high=""):
    root = project["root"]
    _repo_with_change(root)
    with open(os.path.join(root, ".ao", "board.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Board\n\n## running\n{board_line}\n\n## blocked\n\n## queued\n\n## verified\n\n## done\n")
    calls = tmp_path / "calls.txt"
    monkeypatch.setenv("AO_TEST_CALLS", str(calls))
    monkeypatch.setenv("AO_TEST_FAIL", fail)
    monkeypatch.setenv("AO_TEST_HIGH", high)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [sys.executable, "-c", REVIEWER, "{prompt}"]})
    return root, cfg, calls


def _newest(root):
    directory = os.path.join(root, "semantic-review")
    name = sorted(os.listdir(directory))[-1]
    return open(os.path.join(directory, name), encoding="utf-8").read()


def test_each_scenario_is_its_own_question_and_the_verdict_is_computed_from_them(project, tmp_path, monkeypatch):
    root, cfg, calls = _setup(project, tmp_path, monkeypatch, "- [S1] the claim journal", high="Scenario 2")

    assert cli.cmd_review(cfg, _args(SCENARIOS)) == 1

    assert calls.read_text().splitlines() == ["Scenario 1", "Scenario 2", "Scenario 3"]
    artefact = _newest(root)
    assert "\nVERDICT: NEEDS_CHANGES\n" in artefact and "Section 2/3: scenario:2 — NEEDS_CHANGES" in artefact
    sections = A.review_evidence(artefact)["sections"]
    assert [section["verdict"] for section in sections] == ["APPROVED", "NEEDS_CHANGES", "APPROVED"]


def test_a_review_cut_off_resumes_asking_only_what_was_not_answered(project, tmp_path, monkeypatch):
    root, cfg, calls = _setup(project, tmp_path, monkeypatch, "- [S1] the claim journal", fail="Scenario 3")

    assert cli.cmd_review(cfg, _args(SCENARIOS)) == 3                 # no verdict, not a round
    assert calls.read_text().splitlines()[:3] == ["Scenario 1", "Scenario 2", "Scenario 3"]

    calls.write_text("", encoding="utf-8")
    monkeypatch.setenv("AO_TEST_FAIL", "")
    assert cli.cmd_review(cfg, _args(SCENARIOS)) == 0

    assert calls.read_text().splitlines() == ["Scenario 3"]


def test_declared_lenses_are_asked_as_sections_and_recorded(project, tmp_path, monkeypatch):
    root, cfg, calls = _setup(project, tmp_path, monkeypatch,
                              "- [S1] the store · acceptance: keep it right · lenses: correctness, clock, -tests")

    assert cli.cmd_review(cfg, _args()) == 0

    assert calls.read_text().splitlines() == ["Lens `correctness`", "Lens `clock`"]
    evidence = A.review_evidence(_newest(root))
    assert evidence["lenses"] == {"asked": ["correctness", "clock"], "waived": ["tests"], "added": []}
