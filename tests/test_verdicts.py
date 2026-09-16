import ast
import pathlib
import re

import pytest

from ao import lib as A, matrix as M
from ao.verdicts import REVIEW_STATUSES, VERDICTS, ReviewStatus, Verdict

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _listed(path, label):
    line = next(line for line in path.read_text(encoding="utf-8").splitlines() if label in line)
    part = line.split(label, 1)[1].split(".")[0]
    return set(re.findall(r"`([A-Za-z_]+)`", part))


def test_the_enum_the_playbook_and_the_protocol_list_the_same_values():
    for doc in (ROOT / "src" / "ao" / "skill" / "SKILL.md", ROOT / "docs" / "protocol.md"):
        assert _listed(doc, "Verdicts:") == set(VERDICTS), doc.name
        assert _listed(doc, "Review status:") == set(REVIEW_STATUSES), doc.name


@pytest.mark.parametrize("line", ["VERDICT: APPROVE", "VERDICT: LGTM", "VERDICT: approved-ish",
                                  "VERDICT: APPROVED\nVERDICT: NEEDS_CHANGES"])
def test_a_verdict_outside_the_enum_is_invalid(line):
    assert A._review_verdict(line) == Verdict.INVALID


def test_every_enum_value_parses_as_itself():
    for verdict in Verdict:
        assert A._review_verdict(f"VERDICT: {verdict.value}") == verdict


def test_a_review_status_outside_the_enum_is_refused_when_written():
    with pytest.raises(ValueError):
        M.add_evidence_context({}, {"matrix": {}, "reviewers": [], "implementer_identity": {},
                                    "implementer": None}, [], review_status="completed")


def test_no_module_spells_a_verdict_or_status_the_enum_does_not_hold():
    lookalike = re.compile(r"^(APPROV|NEEDS[ _-]?CHANGE|UNAVAIL|REJECT|LGTM)", re.I)
    found = []
    for path in sorted((ROOT / "src" / "ao").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isupper() \
                    and lookalike.match(node.value) and node.value not in VERDICTS:
                found.append(f"{path.name}:{node.lineno} {node.value!r}")
            if isinstance(node, ast.keyword) and node.arg == "review_status" \
                    and isinstance(node.value, ast.Constant) and node.value.value not in REVIEW_STATUSES:
                found.append(f"{path.name}:{node.value.lineno} review_status={node.value.value!r}")
    assert found == []
