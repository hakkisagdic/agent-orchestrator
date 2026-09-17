import json
import os
import re
import subprocess
import sys
from types import SimpleNamespace

from ao import cli, language, lib as A

SOURCE = '''def add(a, b):
    \"\"\"Add two numbers.

    This docstring is long on purpose: the definition has to be large enough
    that a budget can hold the small helper below and not this one, so a test
    can see a definition left out whole instead of cut in the middle. It keeps
    going for a few more words to make that margin comfortable for the test.
    \"\"\"
    return a + b


def helper():
    return "patched in tests"


def unused():
    return "never named by the tests"
'''

TEST = '''from pkg import core


def test_add(monkeypatch):
    monkeypatch.setattr(core, "helper", lambda: "x")
    assert core.add(1, 2) == 3
'''


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                          check=True, capture_output=True, text=True).stdout.strip()


def _write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


def _package(project):
    root = project["root"]
    _write(root, "src/pkg/__init__.py", "")
    _write(root, "src/pkg/core.py", SOURCE)
    _git(root, "add", "src")
    _git(root, "commit", "-q", "-m", "package")
    return root


def _stage_test(root):
    _write(root, "tests/test_core.py", TEST)
    _git(root, "add", "tests/test_core.py")


def _reviewer(project, tmp_path, output=()):
    capture = tmp_path / "prompt.txt"
    lines = ["VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0", *output]
    script = (f"import sys, pathlib; pathlib.Path({str(capture)!r}).write_text(sys.argv[1], encoding='utf-8'); "
              f"print({chr(10).join(lines)!r})")
    cfg = dict(project, reviewer={"id": "r", "family": "x", "argv": [sys.executable, "-c", script, "{prompt}"]})
    return cfg, capture


def _args(**overrides):
    return SimpleNamespace(**dict(dict(boundary="b", timeout=30, paths=None, commits=None), **overrides))


def _review_file(project):
    d = os.path.join(project["root"], project["reviews"])
    name = sorted(f for f in os.listdir(d) if f.endswith(".md"))[-1]
    with open(os.path.join(d, name), encoding="utf-8") as handle:
        return handle.read()


def test_a_one_path_test_only_candidate_is_judged_with_the_source_it_runs(project, tmp_path):
    root = _package(project)
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args()) == 0

    candidate, context = capture.read_text(encoding="utf-8").split(
        "\n" + language.text(project, "prompt.review-context") + "\n", 1)
    assert language.text(project, "prompt.review-candidate") in candidate and "tests/test_core.py" in candidate
    assert "return a + b" not in candidate
    assert "def helper():" in context and "def add(a, b):" in context
    assert context.index("def helper():") < context.index("def add(a, b):")
    assert "def unused():" not in context
    review = _review_file(project)
    evidence = json.loads(re.search(r"<!-- ao-evidence: (.*) -->", review).group(1))
    assert evidence["candidate"]["changed_paths"] == ["tests/test_core.py"]
    assert "- context: read-only at" in review and "src/pkg/core.py; 2 definitions" in review


def test_a_note_outside_the_candidate_is_kept_and_cannot_change_the_verdict(project, tmp_path):
    root = _package(project)
    _stage_test(root)
    notes = ["- src/pkg/core.py:5 — helper has no test of its own",
             "- [HIGH] src/pkg/core.py:9 — unused is dead code outside this candidate"]
    cfg, capture = _reviewer(project, tmp_path, ["", "## Bulgular", "", "## Notlar", *notes])

    assert cli.cmd_review(cfg, _args()) == 0

    prompt = capture.read_text(encoding="utf-8")
    assert prompt.index("VERDICT RULE") < prompt.index("Acceptance boundary: b")
    assert "## Notes" in prompt
    review = _review_file(project)
    assert "VERDICT: APPROVED" in review and all(note in review for note in notes)


def test_a_source_change_carries_its_own_subject_and_no_context(project, tmp_path):
    root = _package(project)
    _write(root, "src/pkg/core.py", SOURCE.replace("a + b", "b + a"))
    _write(root, "tests/test_core.py", TEST)
    _git(root, "add", "src/pkg/core.py", "tests/test_core.py")
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args()) == 0

    assert language.text(project, "prompt.review-context") not in capture.read_text(encoding="utf-8")
    assert "- context:" not in _review_file(project)


def test_only_python_files_decide_whether_a_candidate_is_test_only(project):
    root = _package(project)
    head = _git(root, "rev-parse", "HEAD")

    with_docs = A.review_context(root, ["tests/test_core.py", "docs/notes.md"], head, head)
    with_source = A.review_context(root, ["src/pkg/core.py", "tests/test_core.py"], head, head)

    assert with_docs is not None and with_docs["names"] == []
    assert "none resolved" in with_docs["text"]
    assert with_source is None


def test_context_that_does_not_fit_is_named_never_cut(project):
    root = _package(project)
    _stage_test(root)
    candidate = A.index_candidate(root)

    def context(budget):
        return A.review_context(root, ["tests/test_core.py"], candidate["index_tree"],
                                candidate["head"], budget)

    full = context(10_000)
    assert (full["names"], full["omitted"]) == (["pkg.core.helper", "pkg.core.add"], [])
    size = len(full["text"].encode("utf-8"))
    blocks = {chunk.split("\n", 1)[0].split()[-1]: "# " + chunk
              for chunk in full["text"].split("\n\n# ")[1:]}
    tight, none = context(size - 200), context(0)
    assert (tight["names"], tight["omitted"]) == (["pkg.core.helper"], ["pkg.core.add"])
    assert (none["names"], none["omitted"]) == ([], ["pkg.core.helper", "pkg.core.add"])
    for budget, ctx in ((size, full), (size - 200, tight)):
        assert len(ctx["text"].encode("utf-8")) <= budget
    for ctx in (full, tight, none):
        assert all(blocks[name] in ctx["text"] for name in ctx["names"])
        assert all(name in ctx["text"] and "def " + name.rsplit(".", 1)[1] + "(" not in ctx["text"]
                   for name in ctx["omitted"])
        assert ctx["paths"] == ["src/pkg/core.py"]


def test_context_never_pushes_a_prompt_past_one_argument():
    assert cli._review_context_budget("x" * cli.REVIEW_PROMPT_ARG_BYTES) == 0
    room = cli._review_context_budget("x")
    assert 0 < room <= A.REVIEW_CONTEXT_BUDGET and 1 + room + 200 <= cli.REVIEW_PROMPT_ARG_BYTES


def test_a_retrospective_range_takes_context_from_its_end_commit(project, tmp_path):
    root = _package(project)
    base = _git(root, "rev-parse", "HEAD")
    _stage_test(root)
    _git(root, "commit", "-q", "-m", "test only")
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args(commits=f"{base}..HEAD")) == 0

    marker = language.text(project, "prompt.review-context")
    context = capture.read_text(encoding="utf-8").split("\n" + marker + "\n", 1)[1]
    assert context.startswith("committed at " + _git(root, "rev-parse", "HEAD")[:12])
    assert "def add(a, b):" in context
