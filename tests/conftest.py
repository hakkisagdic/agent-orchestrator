import json
import os
import subprocess
import sys

import pytest

# A hook (or a shell) may hand us GIT_DIR; with it set, every git command in a
# fixture acts on the real repository instead of the temp one. Drop it first.
for _var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY"):
    os.environ.pop(_var, None)

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

from ao import lib as A  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal ao project: git repo, .ao/config.json, board, empty mailbox."""
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".ao-project").write_bytes(b"ao-project-v1\n")
    subprocess.run(["git", "add", ".ao-project"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "-m", "init"], cwd=root, check=True)
    (root / ".ao").mkdir()
    (root / ".ao" / "ledger").mkdir()
    cfg = {"project": "proj", "mailbox": "agent-mail", "reviews": "semantic-review",
           "implementer": {"adapter": "kiro", "session": "s1", "name": "kiro"},
           "architect": {"name": "fable", "argv": ["claude", "-p", "{prompt}"]}}
    (root / ".ao" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (root / ".ao" / "board.md").write_text(
        "# Board\n\n## running\n\n## blocked\n\n## queued\n\n## inbox\n\n## verified\n\n## done\n", encoding="utf-8")
    (root / "agent-mail").mkdir()
    (root / "semantic-review").mkdir()
    # keep every test's ~/.ao away from the real one
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(A, "HOME", str(home))
    from ao import watchdog as W
    monkeypatch.setattr(W, "STATE_DIR", str(home / ".ao"))   # bound at import from the real HOME
    full = dict(cfg, root=str(root))
    return full


@pytest.fixture(autouse=True)
def _ledger_checkpoints(tmp_path_factory):
    """Ledger lengths are recorded outside the repository; give each test its own record.

    Not through monkeypatch: requesting it here would set it up before
    _repo_untouched and undo a test's GIT_INDEX_FILE only after that guard runs
    git in the real repository.
    """
    store = tmp_path_factory.mktemp("ledger-checkpoints") / "ledger-checkpoints.json"
    previous = os.environ.get("AO_LEDGER_CHECKPOINTS")
    os.environ["AO_LEDGER_CHECKPOINTS"] = str(store)
    yield
    if previous is None:
        os.environ.pop("AO_LEDGER_CHECKPOINTS", None)
    else:
        os.environ["AO_LEDGER_CHECKPOINTS"] = previous


@pytest.fixture(autouse=True)
def _project_registry(tmp_path_factory):
    """Project keys are registered outside the repository; give each test its own registry."""
    registry = tmp_path_factory.mktemp("project-registry") / "projects.json"
    previous = os.environ.get("AO_PROJECT_REGISTRY")
    os.environ["AO_PROJECT_REGISTRY"] = str(registry)
    yield
    if previous is None:
        os.environ.pop("AO_PROJECT_REGISTRY", None)
    else:
        os.environ["AO_PROJECT_REGISTRY"] = previous


@pytest.fixture(autouse=True)
def _repo_untouched(request):
    """No test may change the repository it lives in.

    Six commits once landed on a maintainer's branch from a test that ran git
    in the wrong directory; the repository's config even ended up `bare`. This
    guard names the test that does it, the first time it does it.
    """
    repo = os.path.dirname(SRC)
    def snap():
        h = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        s = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
        b = subprocess.run(["git", "config", "--bool", "core.bare"], cwd=repo, capture_output=True, text=True).stdout.strip()
        return h, s, b
    before = snap()
    yield
    after = snap()
    assert after == before, f"{request.node.nodeid} changed the repository it runs in: {before} -> {after}"
