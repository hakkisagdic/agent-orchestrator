"""Test-only review context resolves committed definitions within its budget (TRIAGE-1-MEDIUM)."""
import pytest
from ao import lib as A
from tests import test_slice_boundary as boundary


@pytest.mark.parametrize("statement,path", [
    ("import pkg.core\nassert pkg.core.add(1, 2) == 3\n", "tests/test_core.py"),
    ("from . import core\nassert core.add(1, 2) == 3\n", "src/pkg/test_core.py"),
])
def test_c8_dotted_and_relative_imports_reach_context(project, statement, path):
    from tests import test_review_context as context
    root = context._package(project)
    context._write(root, path, statement)
    context._git(root, "add", path)
    candidate = A.index_candidate(root)
    result = A.review_context(root, [path], candidate["index_tree"], candidate["head"])
    assert "def add(a, b):" in result["text"]


@pytest.mark.parametrize("budget", [0, 1, 60, 100])
def test_c8_context_headers_obey_budget(project, budget):
    from tests import test_review_context as context
    root = context._package(project)
    context._stage_test(root)
    candidate = A.index_candidate(root)
    result = A.review_context(root, ["tests/test_core.py"], candidate["index_tree"], candidate["head"], budget)
    if budget == 0:
        assert result is None
    else:
        assert result is None or len(result["text"].encode("utf-8")) <= budget


def test_c8_dotted_import_finds_a_definition_in_a_loaded_part(project):
    root = project["root"]
    boundary._write(root, "src/ao/lib.py", '_part("lib_context", globals())\n')
    boundary._write(root, "src/ao/parts/lib_context.py", 'def review_context():\n    return "context"\n')
    boundary._git(root, "add", "src")
    boundary._git(root, "commit", "-qm", "module split into a part")
    boundary._write(root, "tests/test_context.py", 'import ao.lib\nassert ao.lib.review_context() == "context"\n')
    boundary._git(root, "add", "tests/test_context.py")
    candidate = A.index_candidate(root)
    result = A.review_context(root, ["tests/test_context.py"], candidate["index_tree"], candidate["head"])
    assert result["paths"] == ["src/ao/parts/lib_context.py"]
    assert "def review_context():" in result["text"]
