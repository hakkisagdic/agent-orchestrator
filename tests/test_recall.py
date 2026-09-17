import json
import os
import subprocess

from ao import cli, lib as A


def _other_project(tmp_path):
    other = tmp_path / "acme-api"
    (other / ".ao").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    with open(A.project_registry_path(), "w", encoding="utf-8") as fh:
        json.dump({"acme-api": {"root": str(other)}}, fh)
    return str(other)


def test_recall_reads_questions_findings_lessons_and_decisions_across_projects(project, tmp_path):
    root = project["root"]
    other = _other_project(tmp_path)
    asked = A.ask(other, "The delivery binding lives in model.ts, outside the declared path boundary: widen?",
                  ["widen the boundary", "split the slice"])
    A.answer(other, asked["id"], "b")
    review = os.path.join(root, "semantic-review", "2026-09-07-101010-abc.md")
    with open(review, "w", encoding="utf-8") as fh:
        fh.write("VERDICT: NEEDS_CHANGES\n\n    - [HIGH] src/claims.py:12 — the delivery binding is written after "
                 "admission, outside the path boundary\n")
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    with open(os.path.join(root, "docs", "lessons.md"), "w", encoding="utf-8") as fh:
        fh.write("# Lessons\n\n## 1. A boundary that could not hold its own delivery binding\n\nFound mid-slice.\n")

    results = A.recall("delivery binding boundary", root)

    by_kind = {found["kind"]: found for found in results}
    assert by_kind["question"]["project"] == "acme-api"
    assert by_kind["question"]["outcome"] == "answered: split the slice"
    assert by_kind["question"]["source"] == f".ao/decisions/{asked['id']}.json"
    assert by_kind["finding"]["source"] == "semantic-review/2026-09-07-101010-abc.md:3"
    assert by_kind["finding"]["outcome"] == "HIGH in a NEEDS_CHANGES review"
    assert by_kind["lesson"]["source"] == "docs/lessons.md:3"
    assert A.recall("nothing like this anywhere", root) == []


def test_opening_a_decision_carries_a_near_match_to_the_person_it_reaches(project, tmp_path, capsys):
    root = project["root"]
    other = _other_project(tmp_path)
    before = A.ask(other, "Boundary conflict: model.ts is outside the declared paths. Widen or split?",
                   ["widen", "split"])
    A.answer(other, before["id"], "a")

    rec = A.ask(root, "model.ts falls outside the declared paths of this slice — widen or split the boundary?",
                ["widen", "split"])

    assert [(found["project"], found["id"]) for found in rec["precedents"]] == [("acme-api", before["id"])]
    assert f"önceden: acme-api question {before['id']} — answered: widen" in cli._decision_text(rec)
    stored = json.load(open(os.path.join(root, ".ao", "decisions", rec["id"] + ".json"), encoding="utf-8"))
    assert stored["precedents"][0]["source"] == f".ao/decisions/{before['id']}.json"
