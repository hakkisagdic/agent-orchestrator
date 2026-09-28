"""A count is never below the findings the reviewer listed (REVIEW-FINDINGS-COUNT).

A reviewer declares four counts and lists its findings, and ao decided by the counts alone: an answer
that counted HIGH 0 above a listed HIGH finding was recorded as an approval. The findings are now
counted too, wherever they stand and under no heading in particular, and each count ao decides by is
the larger of the two, so the list can only make a verdict stricter. A verdict line or count ao cannot
read still makes the answer INVALID: nothing here reads a looser answer than before.
"""
import os

from ao import cli, lib as A
from tests.test_review_chain import _args, _fake, _repo_with_change

CLEAN = ["VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0"]


def _artefact(root):
    names = os.listdir(os.path.join(root, "semantic-review"))
    assert len(names) == 1
    return names[0], open(os.path.join(root, "semantic-review", names[0]), encoding="utf-8").read()


def test_each_finding_line_of_the_reviewer_is_counted_and_no_quoted_one():
    answer = "\n".join(CLEAN + [
        "", "## Findings",
        "- [HIGH] src/a.py:1 — the value is lost",
        "  How it breaks: x = 2 reads back as 1",
        "- [MEDIUM] src/a.py:2 — a name that says less than it does",
        "    - [BLOCKER] quoted in an indented block, not the reviewer's own",
        "- [SEVERITY] file:line — the prompt's own example, echoed",
        "```",
        "- [BLOCKER] quoted in a fenced block",
        "```",
        "- [LOW] src/a.py:3 — a finding after a quoted block still counts",
        "", "## Notes",
        "- [HIGH] src/b.py:9 — a note is outside the candidate: carrying a severity, it still does not count",
        "", "## Findings, again",
        "- [MEDIUM] src/a.py:4 — a heading after the notes ends them",
    ])

    assert cli._listed_findings(answer) == {"BLOCKER": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 1}


def test_either_language_is_the_same_review():
    english = "## Findings\n- [BLOCKER] a.py:1 — data is lost\n\n## Notes\n- [HIGH] b.py:2 — outside\n"
    turkish = "## Bulgular\n- [BLOCKER] a.py:1 — veri kaybı\n\n## Notlar\n- [HIGH] b.py:2 — kapsam dışı\n"

    assert cli._listed_findings(english) == cli._listed_findings(turkish) == cli._listed_findings(
        "- [BLOCKER] a.py:1 — data is lost\n") == {"BLOCKER": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    assert cli._listed_findings("\n".join(CLEAN + ["", "No findings."])) == dict.fromkeys(
        ("BLOCKER", "HIGH", "MEDIUM", "LOW"), 0)


def test_a_listed_finding_raises_the_count_it_was_declared_below():
    declared = {"BLOCKER": 0, "HIGH": 0, "MEDIUM": 2, "LOW": 0}
    listed = {"BLOCKER": 0, "HIGH": 1, "MEDIUM": 1, "LOW": 0}

    counts, lines = cli._decided_counts(declared, listed)

    assert counts == {"BLOCKER": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 0}
    assert lines == ["- adjudicated: the reviewer counted HIGH 0 and listed 1 HIGH finding(s); "
                     "a count is never below the findings listed"]
    assert cli._decided_counts(declared, dict.fromkeys(declared, 0)) == (declared, [])


def test_an_approval_above_a_listed_high_finding_is_needs_changes(project):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(
        *CLEAN, "", "## Findings", "- [HIGH] src/a.py:1 — the value is lost")})

    cli.cmd_review(cfg, _args())

    name, body = _artefact(root)
    assert A.reviews(root, "semantic-review") == [(name, "NEEDS_CHANGES")]
    assert "\nHIGH: 1\n" in body
    assert ("- adjudicated: the reviewer counted HIGH 0 and listed 1 HIGH finding(s); "
            "a count is never below the findings listed") in body
    assert "- adjudicated: the reviewer wrote APPROVED; BLOCKER 0 and HIGH 1 make it NEEDS_CHANGES" in body


def test_a_declared_count_stands_where_its_findings_are_written_in_another_shape(project):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(
        "VERDICT: NEEDS_CHANGES", "BLOCKER: 0", "HIGH: 1", "MEDIUM: 0", "LOW: 0", "",
        "## Findings", "1. HIGH — src/a.py:1 the value is lost")})

    cli.cmd_review(cfg, _args())

    name, body = _artefact(root)
    assert A.reviews(root, "semantic-review") == [(name, "NEEDS_CHANGES")]
    assert "a count is never below" not in body


def test_a_verdict_line_ao_cannot_read_is_still_invalid_whatever_the_findings(project):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(
        "**VERDICT: APPROVED**", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0", "", "## Findings", "None.")})

    assert cli.cmd_review(cfg, _args()) == 3

    name, _ = _artefact(root)
    assert A.reviews(root, "semantic-review") == [(name, "INVALID")]


def test_each_section_is_read_the_same_way_and_says_so_in_its_block(project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(
        *CLEAN, "", "## Findings", "- [HIGH] src/a.py:1 — the value is lost")})
    boundary = "the claim journal:\n1. a claim is journalled before admission\n2. a duplicate is refused by its id"

    cli.cmd_review(cfg, _args(boundary=boundary))

    name, body = _artefact(root)
    assert A.reviews(root, "semantic-review") == [(name, "NEEDS_CHANGES")]
    assert "\nHIGH: 2\n" in body
    assert body.count("- adjudicated: the reviewer counted HIGH 0 and listed 1 HIGH finding(s)") == 2
