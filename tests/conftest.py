import json
import os
import shutil
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

# The git the fixtures here run, chosen once before any test moves PATH or AO_GIT: the
# one ao measures with. On macOS the git on PATH is an xcrun stub that costs more per
# call than git itself, and the guard below runs before and after every test.
GIT = A.git_binary()
REPO = os.path.dirname(SRC)


def _git_ahead_of_its_stub():
    """Put git's exec-path first on PATH when the git on PATH is the xcrun stub.

    git does the same for every hook it runs, so under the pre-push hook a test's plain
    `git` is already the compiled one; run by hand, each of those calls paid the stub.
    """
    on_path = shutil.which("git")
    real = A._xcrun_git(on_path) if on_path else None
    if not real:
        return
    exec_path = subprocess.run([real, "--exec-path"], capture_output=True, text=True).stdout.strip()
    if exec_path and os.path.isfile(os.path.join(exec_path, "git")):
        os.environ["PATH"] = exec_path + os.pathsep + os.environ.get("PATH", "")


_git_ahead_of_its_stub()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal ao project: git repo, .ao/config.json, board, empty mailbox."""
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run([GIT, "init", "-q"], cwd=root, check=True)
    (root / ".ao-project").write_bytes(b"ao-project-v1\n")
    subprocess.run([GIT, "add", ".ao-project"], cwd=root, check=True)
    subprocess.run([GIT, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
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
def _machine_settings(tmp_path_factory):
    """Machine settings live outside the repository; give each test its own file."""
    path = tmp_path_factory.mktemp("machine-settings") / "settings.json"
    previous = os.environ.get("AO_SETTINGS")
    os.environ["AO_SETTINGS"] = str(path)
    yield
    if previous is None:
        os.environ.pop("AO_SETTINGS", None)
    else:
        os.environ["AO_SETTINGS"] = previous


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


def _git_text(*args):
    """Run git in the repository the tests live in; a path that is not UTF-8 still compares exactly."""
    result = subprocess.run([GIT, *args], cwd=REPO, capture_output=True, encoding="utf-8", errors="surrogateescape")
    return result.returncode, result.stdout


def _config_files():
    """Every file core.bare can be read from for this repository, found once.

    The repository's own config and config.worktree and the user's global files,
    present or not, and every file git reports reading, includes among them. A
    system file that does not exist is not watched: creating one takes root.
    """
    home = os.path.expanduser("~")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    paths = [os.environ.get("GIT_CONFIG_GLOBAL") or os.path.join(home, ".gitconfig"),
             os.path.join(xdg, "git", "config")]
    code, out = _git_text("rev-parse", "--git-common-dir", "--git-path", "config.worktree")
    if code == 0 and len(out.splitlines()) == 2:
        common, worktree = out.splitlines()
        paths += [os.path.join(REPO, common, "config"), os.path.join(REPO, worktree)]
    code, out = _git_text("config", "--list", "--show-origin", "-z")
    origins = out.split("\0")[0::2] if code == 0 else []
    paths += [os.path.join(REPO, origin[5:]) for origin in origins if origin.startswith("file:")]
    return sorted(set(paths))


_CONFIG_FILES = _config_files()
_BARE = {"signature": None, "value": None}


def _core_bare():
    """`git config --bool core.bare`, asked again only when something it is read from changed.

    Each file's inode, size and nanosecond mtime, and the variables that name or
    inject git config: git replaces a config file by renaming a new one over it,
    and any other writer moves its mtime, so an unchanged signature is an
    unchanged answer, and the git call is saved on nearly every snapshot.
    """
    signature = [sorted((key, value) for key, value in os.environ.items()
                        if key.startswith("GIT_") or key in ("HOME", "XDG_CONFIG_HOME"))]
    for path in _CONFIG_FILES:
        try:
            st = os.stat(path)
        except OSError:
            signature.append(None)
        else:
            signature.append((st.st_ino, st.st_size, st.st_mtime_ns))
    if signature != _BARE["signature"]:
        _BARE["value"] = _git_text("config", "--bool", "core.bare")[1].strip()
        _BARE["signature"] = signature
    return _BARE["value"]


def _repo_state():
    """HEAD, the branch, the porcelain status and core.bare, in one git call and a few stats.

    `status --porcelain=v2 --branch` carries the commit and branch HEAD names with the
    status. Its upstream lines are left out: a fetch or a push elsewhere moves them, and
    the guard never read them. The status alone fails on a bare repository, but not in
    a linked worktree of one, so core.bare is still read.
    """
    code, out = _git_text("status", "--porcelain=v2", "--branch", "--no-ahead-behind")
    lines = [line for line in out.splitlines() if not line.startswith(("# branch.upstream ", "# branch.ab "))]
    return code, lines, _core_bare()


@pytest.fixture(autouse=True)
def _repo_untouched(request):
    """No test may change the repository it lives in.

    Six commits once landed on a maintainer's branch from a test that ran git
    in the wrong directory; the repository's config even ended up `bare`. This
    guard names the test that does it, the first time it does it.
    """
    before = _repo_state()
    yield
    after = _repo_state()
    assert after == before, f"{request.node.nodeid} changed the repository it runs in: {before} -> {after}"
