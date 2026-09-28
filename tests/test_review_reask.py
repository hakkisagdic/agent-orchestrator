"""An answer ao cannot read is asked for once more, saying what was wrong, before it is INVALID (REVIEW-REASK).

A reviewer that left out its verdict line, or wrote a count twice, made the review INVALID at once, and a
person submitted it again by hand. The same reviewer is now told which lines it missed and asked the same
question once more. The second answer is read no more loosely than the first: if it cannot be read either,
or does not come, the review is INVALID as before, and the artefact says the reviewer was asked twice.
"""
import json
import os
import sys

from ao import cli, lib as A
from tests.test_review_chain import _args, _repo_with_change

# A reviewer that counts its calls in AO_TEST_COUNT, keeps each prompt it is handed beside AO_TEST_PROMPTS, and
# gives the answers AO_TEST_ANSWERS lists, one a call and the last one after that; an answer that is a number is
# the exit status of a reviewer that answers nothing.
REVIEWER = """
import json, os, sys
count = os.environ["AO_TEST_COUNT"]
n = (int(open(count).read()) if os.path.exists(count) else 0) + 1
open(count, "w").write(str(n))
open(os.environ["AO_TEST_PROMPTS"] + "." + str(n), "w", encoding="utf-8").write(sys.argv[1])
answers = json.loads(os.environ["AO_TEST_ANSWERS"])
answer = answers[min(n, len(answers)) - 1]
if isinstance(answer, int):
    sys.exit(answer)
print(answer)
"""
GOOD = "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n\n## Findings\nNone."
NO_VERDICT = "BLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n\n## Findings\nNone."
TWO_HIGHS = "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nHIGH: 1\nMEDIUM: 0\nLOW: 0"


def _reviewing(project, tmp_path, monkeypatch, *answers):
    monkeypatch.setenv("AO_TEST_COUNT", str(tmp_path / "calls"))
    monkeypatch.setenv("AO_TEST_PROMPTS", str(tmp_path / "prompt"))
    monkeypatch.setenv("AO_TEST_ANSWERS", json.dumps(list(answers)))
    monkeypatch.setattr(cli, "_review_retry_wait", lambda seconds: None)
    _repo_with_change(project["root"])
    return dict(project, reviewer={"id": "r1", "family": "x", "argv": [sys.executable, "-c", REVIEWER, "{prompt}"]})


def _calls(tmp_path):
    return int((tmp_path / "calls").read_text())


def _prompt(tmp_path, n):
    return (tmp_path / f"prompt.{n}").read_text(encoding="utf-8")


def _artefact(root):
    names = os.listdir(os.path.join(root, "semantic-review"))
    assert len(names) == 1
    return names[0], open(os.path.join(root, "semantic-review", names[0]), encoding="utf-8").read()


def test_an_answer_without_a_verdict_line_is_asked_for_again_and_the_second_is_read(project, tmp_path, monkeypatch):
    cfg = _reviewing(project, tmp_path, monkeypatch, NO_VERDICT, GOOD)

    assert cli.cmd_review(cfg, _args()) == 0

    assert _calls(tmp_path) == 2
    first, second = _prompt(tmp_path, 1), _prompt(tmp_path, 2)
    assert second.startswith(first)
    assert ("--- ao COULD NOT READ YOUR ANSWER: `VERDICT: …` did not stand once each at the start of a line."
            in second[len(first):])
    name, body = _artefact(project["root"])
    assert A.reviews(project["root"], "semantic-review") == [(name, "APPROVED")]
    assert "- re-asked: the first answer did not hold `VERDICT: …` once each; this is the second" in body


def test_a_count_written_twice_is_named_in_the_note(project, tmp_path, monkeypatch):
    cfg = _reviewing(project, tmp_path, monkeypatch, TWO_HIGHS, GOOD)

    assert cli.cmd_review(cfg, _args()) == 0

    assert "`HIGH: <n>` did not stand once each" in _prompt(tmp_path, 2)


def test_a_readable_answer_is_not_asked_for_again(project, tmp_path, monkeypatch):
    cfg = _reviewing(project, tmp_path, monkeypatch, GOOD)

    assert cli.cmd_review(cfg, _args()) == 0

    assert _calls(tmp_path) == 1
    assert "re-asked" not in _artefact(project["root"])[1]


def test_a_second_answer_that_cannot_be_read_either_is_invalid_and_says_so(project, tmp_path, monkeypatch):
    cfg = _reviewing(project, tmp_path, monkeypatch, NO_VERDICT, "I looked at it. Seems fine.")

    assert cli.cmd_review(cfg, _args()) == 3

    assert _calls(tmp_path) == 2
    name, body = _artefact(project["root"])
    assert A.reviews(project["root"], "semantic-review") == [(name, "INVALID")]
    assert ("- re-asked: the first answer did not hold `VERDICT: …` once each; "
            "the second could not be read either") in body
    assert A.review_evidence(body)["invalid_reasons"] == ["reviewer returned no valid verdict/count schema"]


def test_a_second_answer_that_does_not_come_leaves_the_first_invalid(project, tmp_path, monkeypatch):
    cfg = _reviewing(project, tmp_path, monkeypatch, NO_VERDICT, 1)

    assert cli.cmd_review(cfg, _args()) == 3

    assert _calls(tmp_path) == 2
    name, body = _artefact(project["root"])
    assert A.reviews(project["root"], "semantic-review") == [(name, "INVALID")]
    assert "- re-asked:" in body


def test_a_section_is_asked_again_on_its_own_and_its_block_says_so(project, tmp_path, monkeypatch):
    cfg = _reviewing(project, tmp_path, monkeypatch, NO_VERDICT, GOOD, GOOD)
    boundary = "the claim journal:\n1. a claim is journalled before admission\n2. a duplicate is refused by its id"

    assert cli.cmd_review(cfg, _args(boundary=boundary)) == 0

    assert _calls(tmp_path) == 3
    name, body = _artefact(project["root"])
    assert A.reviews(project["root"], "semantic-review") == [(name, "APPROVED")]
    assert body.count("- re-asked: the first answer did not hold `VERDICT: …` once each; this is the second") == 1


def test_a_turkish_project_is_told_in_turkish(project, tmp_path, monkeypatch):
    cfg = _reviewing(dict(project, language="tr"), tmp_path, monkeypatch, NO_VERDICT, GOOD)

    assert cli.cmd_review(cfg, _args()) == 0

    assert "--- ao CEVABINI OKUYAMADI: `VERDICT: …` satır başında birer kez yer almadı." in _prompt(tmp_path, 2)


def test_a_summary_verdict_line_after_the_findings_is_asked_away(project, tmp_path, monkeypatch):
    """The answer a reviewer gave on 2026-09-28: the schema, then a second verdict line summing it up."""
    summed = GOOD + "\n\nVERDICT: APPROVED — 0 BLOCKER, 0 HIGH. The two MEDIUM/LOW findings do not block."
    cfg = _reviewing(project, tmp_path, monkeypatch, summed, GOOD)

    assert cli.cmd_review(cfg, _args()) == 0

    assert "`VERDICT: …` did not stand once each" in _prompt(tmp_path, 2)
    name, _ = _artefact(project["root"])
    assert A.reviews(project["root"], "semantic-review") == [(name, "APPROVED")]
