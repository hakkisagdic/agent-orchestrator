import os
import re
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, lib as A

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


def _board(root, *lines, state="queued"):
    body = "# Board\n\n## running\n\n## blocked\n\n## queued\n\n## verified\n\n## done\n"
    _write(root, ".ao/board.md", body.replace(f"## {state}\n", f"## {state}\n" + "".join(f"{l}\n" for l in lines)))


def test_an_acceptance_needing_a_file_outside_the_declared_paths_is_named_at_registration(project, capsys):
    root = project["root"]
    _write(root, "src/api.ts", "export const api = 1\n")
    _write(root, "src/identity/model.ts", "export function bindDelivery() {}\n")
    _git(root, "add", "src")
    _git(root, "commit", "-q", "-m", "source")
    _board(root, "- [B8a] claim admission · acceptance: a durable delivery binding in model.ts through "
                 "`bindDelivery` · paths: src/api.ts, src/journal.ts (new), src/ghost.ts")

    conflicts = A.boundary_conflicts(root, A.board(root)["queued"][0])

    assert conflicts == [
        "src/ghost.ts is declared but does not exist; write `(new)` after it if the slice creates it",
        "the acceptance names model.ts (src/identity/model.ts), outside the declared paths",
        "the acceptance names `bindDelivery`, defined in src/identity/model.ts, outside the declared paths",
    ]
    assert cli.cmd_board(project, SimpleNamespace(view=None)) == 0
    assert "boundary: the acceptance names `bindDelivery`" in re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    found = dict(cli.doctor_problems(project))["boundary-conflict"]
    assert found.startswith("B8a: src/ghost.ts is declared but does not exist")


def test_a_boundary_file_is_judged_at_its_commit_and_its_later_change_reaches_the_reviewer(project, tmp_path):
    root = project["root"]
    _write(root, "docs/slices/B8a.md", "# B8a\n## Invariant\nclaims are journalled before admission\n"
                                       "## Paths\n- src/a.py\n## Out of scope\n- src/other.py\n")
    _write(root, "src/a.py", "x = 1\n")
    _git(root, "add", "docs", "src")
    _git(root, "commit", "-q", "-m", "boundary")
    sha = _git(root, "rev-parse", "HEAD")[:12]
    _write(root, "docs/slices/B8a.md", "# B8a\n## Invariant\nclaims are journalled, or not\n## Paths\n- src/a.py\n")
    _git(root, "commit", "-q", "-am", "the boundary moves")
    _board(root, f"- [B8a] claim admission · boundary: docs/slices/B8a.md@{sha}", state="running")
    item = A.running_slice(root)

    source = A.read_boundary(root, item)

    assert source["changed"] is True and source["label"] == f"docs/slices/B8a.md at {sha}, changed since"
    assert "claims are journalled before admission" in source["text"]
    assert "+claims are journalled, or not" in source["text"]
    assert A.declared_paths(item, source) == [("src/a.py", False)]
    assert A.boundary_conflicts(root, item) == []

    _write(root, "src/a.py", "x = 2\n")
    _git(root, "add", "src/a.py")
    seen = tmp_path / "prompt.txt"
    script = f"import sys; open({str(seen)!r}, 'w', encoding='utf-8').write(sys.argv[1]); " \
             + "; ".join(f"print({line!r})" for line in APPROVED)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": [sys.executable, "-c", script, "{prompt}"]})
    args = SimpleNamespace(action=None, rid=None, any=False, run=None, boundary=None, paths=None, commits=None,
                           timeout=None)

    assert cli.cmd_review(cfg, args) == 0

    prompt = seen.read_text(encoding="utf-8")
    assert "claims are journalled before admission" in prompt and f"has changed since {sha}" in prompt
    _, _, _, evidence = A.latest_candidate_review(root, "semantic-review", A.index_candidate(root)["digest"])
    assert evidence["boundary"] == f"docs/slices/B8a.md at {sha}, changed since"
    assert evidence["boundary_file"]["commit"] == sha and evidence["boundary_file"]["changed"] is True


def test_a_long_sentence_and_two_sources_of_truth_are_named(project):
    root = project["root"]
    _board(root, f"- [S1] long · acceptance: {'x' * 401}", "- [S2] both · acceptance: short · boundary: docs/b.md",
           "- [S3] outside · boundary: ../elsewhere.md")
    long_item, both, outside = A.board(root)["queued"]

    assert A.boundary_advice(root, project, long_item) == [
        "its acceptance is 401 characters; above boundary.inline_max_chars (400) it belongs in a file the row "
        "points at with `boundary: path@commit`"]
    advice = A.boundary_advice(root, project, both)
    assert advice[0].startswith("its boundary file docs/b.md cannot be read")
    assert advice[1] == "it has both a boundary file and an acceptance sentence; one source of truth, so keep the file"
    assert A.boundary_conflicts(root, outside) == [
        "its boundary file ../elsewhere.md cannot be read: it is not a path inside the repository"]
