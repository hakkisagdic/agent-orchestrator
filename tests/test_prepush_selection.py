"""The pre-push hook runs what a push touches before the whole suite (PREPUSH-PARALLEL).

`.githooks/changed_tests.py` reads the refs git hands the hook and prints the test files to run first.
It only orders the run - the whole suite runs after it - so these hold it to choosing well and to
choosing nothing where every test may move.
"""
import os
import subprocess
import sys

HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".githooks", "changed_tests.py")
ZERO = "0" * 40


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def _write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root, "src/ao/clock.py", "def parse_reset(text):\n    return text\n\n\ndef other(text):\n    return text\n")
    _write(root, "tests/test_clock.py", "from ao.clock import parse_reset\n")
    _write(root, "tests/test_other.py", "from ao.clock import other\n")
    _write(root, "tests/test_docs_names.py", "\n")
    _write(root, "tests/conftest.py", "\n")
    _write(root, "docs/guide.md", "# guide\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _pushed(root, base, change):
    """Commit `change` (path -> text) on top of base and ask the helper what to run first."""
    for path, text in change.items():
        _write(root, path, text)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change")
    tip = _git(root, "rev-parse", "HEAD")
    refs = f"refs/heads/main {tip} refs/heads/main {base}\n"
    result = subprocess.run([sys.executable, HELPER], input=refs, cwd=str(root), capture_output=True, text=True,
                            check=True)
    return result.stdout.split()


def test_a_source_change_runs_first_the_tests_that_name_what_it_touched(tmp_path):
    root, base = _repo(tmp_path)

    chosen = _pushed(root, base, {"src/ao/clock.py": "def parse_reset(text):\n    return text.strip()\n\n\n"
                                                     "def other(text):\n    return text\n"})

    assert chosen == ["tests/test_clock.py"]


def test_a_changed_test_runs_itself_and_a_changed_document_runs_the_documentation_tests(tmp_path):
    root, base = _repo(tmp_path)

    chosen = _pushed(root, base, {"tests/test_other.py": "from ao.clock import other\n# more\n",
                                  "docs/guide.md": "# guide\n\nmore\n"})

    assert chosen == ["tests/test_docs_names.py", "tests/test_other.py"]


def test_a_change_to_the_suites_own_conftest_runs_nothing_first(tmp_path):
    root, base = _repo(tmp_path)

    assert _pushed(root, base, {"tests/conftest.py": "# every test may move with this\n",
                                "tests/test_other.py": "from ao.clock import other\n# more\n"}) == []


def test_a_deleted_ref_pushes_nothing_to_test(tmp_path):
    root, base = _repo(tmp_path)
    refs = f"(delete) {ZERO} refs/heads/gone {base}\n"

    result = subprocess.run([sys.executable, HELPER], input=refs, cwd=str(root), capture_output=True, text=True,
                            check=True)

    assert result.stdout.split() == []
