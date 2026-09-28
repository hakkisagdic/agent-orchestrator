import atexit
import functools
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

import pytest

# A hook (or a shell) may hand us GIT_DIR; with it set, every git command in a
# fixture acts on the real repository instead of the temp one. Drop it first.
for _var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY"):
    os.environ.pop(_var, None)

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

# ---- a machine of the suite's own (TRUST-HYGIENE) ----------------------------------------------
#
# ao binds what it keeps on a machine to the home when a module is imported: telegram.CONF,
# email.CONF and the watchdog's STATE_DIR were the person's own files, and a test that did not point
# them away itself - several did, "nothing reaches a phone" - could message their phone, read their
# settings or write beside their watchdog. A program a test started by name was looked for on their
# PATH, where their agent CLIs are, and ao read the files their AO_ variables name. So the suite gets
# a home, a PATH and an environment of its own before ao is imported, and every process a test
# starts inherits them.

REAL_HOME = os.path.expanduser("~")
SESSION = tempfile.mkdtemp(prefix="ao-tests-")
atexit.register(shutil.rmtree, SESSION, True)
SESSION_HOME = os.path.join(SESSION, "home")
SESSION_BIN = os.path.join(SESSION, "bin")
# Who commits in a test that names nobody. git read the person's identity from their home, and a
# guess from the host name fails on a host with no domain, so the suite's home holds one.
GIT_IDENTITY = ("ao tests", "ao-tests@example.invalid")
# Where ao looks for a program whatever PATH says, outside every home (see "ao's own search past
# PATH" below): package managers install agent CLIs there for every user of a machine.
SHARED_INSTALL_DIRS = ("/usr/local/bin", "/opt/homebrew/bin")
# Variables that point git or ao back into the person's home.
HOME_POINTERS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "GIT_CONFIG_GLOBAL")
# The suite's own switches (tests/test_scenario_fuzz.py): a person sets them to run it differently.
SUITE_SWITCHES = ("AO_FUZZ_SEED", "AO_FUZZ_SEEDS")


def _own_environment(environ):
    """Make `environ`, the environment the suite started with, the suite's own; it is changed in place.

    Every variable of ao's begins AO_, and each points ao at something of the person's: a settings
    file (AO_SETTINGS), the project registry, the ledgers' recorded lengths, the adapters they added
    (AO_USER_ADAPTERS), the git ao measures with (AO_GIT), the role of a turn ao started (AO_ROLE).
    Those and the variables that point back into the home are dropped, whatever ao names next, so ao
    and git read the suite's home in their place; the suite's own switches stay. HOME, and
    USERPROFILE for Windows, name the suite's home.
    """
    for name in [name for name in environ
                 if name in HOME_POINTERS or (name.startswith("AO_") and name not in SUITE_SWITCHES)]:
        del environ[name]
    environ["HOME"] = SESSION_HOME
    environ["USERPROFILE"] = SESSION_HOME


def _own_home():
    """The suite's home holds only a git identity, and the environment names it.

    Every path ao derives from the home is then empty: ~/.ao, each harness's session store, the
    credentials a harness keeps for its account, and each directory under the home ao searches for
    a program.
    """
    os.makedirs(SESSION_HOME)
    os.makedirs(SESSION_BIN)
    with open(os.path.join(SESSION_HOME, ".gitconfig"), "w", encoding="utf-8") as fh:
        fh.write("[user]\n\tname = %s\n\temail = %s\n" % GIT_IDENTITY)
    _own_environment(os.environ)


_own_home()

from ao import lib as A  # noqa: E402
from ao import watchdog as W  # noqa: E402


def _within(path, root):
    """Whether a path, followed through its links, is `root` or lies inside it."""
    path, root = (os.path.normcase(os.path.realpath(p)) for p in (path, root))
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _outside_shared_install_dirs(directory):
    real = os.path.normcase(os.path.realpath(directory))
    return all(real != os.path.normcase(os.path.realpath(shared)) for shared in SHARED_INSTALL_DIRS)


def _agent_programs():
    """The command name of every harness ao ships an adapter for."""
    return sorted({name for adapter in A.package_adapters().values() for name in A.adapter_binaries(adapter)})


def _holds_an_agent_cli(directory):
    """Whether a program named for one of those harnesses is in `directory`."""
    extensions = [""] + (os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").lower().split(";") if os.name == "nt" else [])
    return any(os.path.isfile(os.path.join(directory, name + extension))
               for name in _agent_programs() for extension in extensions)


def _system_directories():
    """The operating system's own program directories, which the suite's PATH keeps.

    On POSIX that is the PATH launchd hands a job - /usr/bin, /bin, /usr/sbin, /sbin - which ao's own
    scheduled jobs start with, so ao already finds what it needs there. On Windows it is each
    directory under SystemRoot the PATH the suite started with names: cmd, PowerShell, tasklist.
    """
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        return [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and _within(d, system_root)]
    return [d for d in ("/usr/bin", "/bin", "/usr/sbin", "/sbin") if os.path.isdir(d)]


def _git_directory(found):
    """The directory the suite's PATH names for the git ao found: one holding git and git's own programs alone.

    On POSIX that is git's exec-path, where git keeps the programs it runs itself and which git puts
    first on the PATH of every hook and helper it starts. Not the directory git was found in: that can
    be /usr/local/bin or /opt/homebrew/bin, with agent CLIs beside git. Nor a link to git in a directory
    of the suite's own: a git built with a runtime prefix, as Apple's is, finds its helpers and templates
    from the place it was started from, and started through a link it finds neither. On Windows, where
    git.exe loads the libraries installed beside it, it is the directory git.exe was found in. Either
    is left out when an agent CLI is in it, and then ao finds git in the system's directories, as it
    does under launchd: None.
    """
    if not os.path.isabs(found):
        return None
    if os.name == "nt":
        directory = os.path.dirname(found)
    else:
        try:
            asked = subprocess.run([found, "--exec-path"], stdin=subprocess.DEVNULL, capture_output=True,
                                   text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        directory = asked.stdout.strip() if asked.returncode == 0 else ""
        git = os.path.join(directory, "git")
        if not (os.path.isabs(directory) and os.path.isfile(git) and os.access(git, os.X_OK)):
            return None
    if not _outside_shared_install_dirs(directory) or _holds_an_agent_cli(directory):
        return None
    return directory


def _own_path():
    """PATH is a bin of the suite's own, a directory that holds git alone, and the system's directories.

    Agent CLIs install under the home, in a version manager's directory, in /usr/local/bin or in
    /opt/homebrew/bin, and none of those is on it, so a test that starts a program by name reaches the
    fake it wrote or nothing. `python3` and `python` in the suite's bin run this interpreter with its own
    environment, for the fixtures that are scripts starting `#!/usr/bin/env python3`; the other tests
    name sys.executable themselves. git is found once, on the PATH the suite started with, and the
    directory that holds it alone comes before the system's: on macOS the git in /usr/bin is an xcrun
    stub that costs more per call than git itself, and the guard below runs git before and after every
    test.
    """
    directories = [SESSION_BIN] + ([GIT_DIRECTORY] if GIT_DIRECTORY else [])
    directories += [d for d in SYSTEM_DIRS if d not in directories]
    if os.name != "nt":                      # Windows runs no shebang line
        for name in ("python3", "python"):
            shim = os.path.join(SESSION_BIN, name)
            with open(shim, "w", encoding="utf-8") as fh:
                fh.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
            os.chmod(shim, 0o755)
    os.environ["PATH"] = os.pathsep.join(directories)


SYSTEM_DIRS = _system_directories()
GIT_DIRECTORY = _git_directory(A.git_binary())
_own_path()

# The git the fixtures here run, chosen once before any test moves PATH or AO_GIT: the one
# ao measures with, found on the suite's own PATH.
GIT = A.git_binary()
REPO = os.path.dirname(SRC)

# Imported on the suite's own PATH, as every module a test imports is.
from ao import cli  # noqa: E402

# ---- ao's own search past PATH -------------------------------------------------------------------
#
# ao also looks for a program in SHARED_INSTALL_DIRS, /usr/local/bin and /opt/homebrew/bin, whatever
# PATH says - lib._BIN_DIRS when it resolves a binary or discovers a reviewer, watchdog.child_path
# for the PATH it hands an agent it starts - because a launchd job's PATH names neither. Package
# managers install agent CLIs there for every user of a machine, and `ao doctor`, a review and an
# architect wake ask each copy they find for its --version. So in this process a program in those
# directories is out of reach like one on the person's PATH: each function of ao's that looks there
# is run with what it finds there left out. tests/test_suite_isolation.py finds every such function
# in ao's source and fails on one KEPT_OFF_SHARED does not name. A process a test starts inherits
# HOME, PATH and the environment but not this: an ao started as a child still looks in both.


def _found_outside(found):
    return [path for path in found if _outside_shared_install_dirs(os.path.dirname(path))]


_AO_BINARY_CANDIDATES = A.binary_candidates
_AO_REVIEWER_CANDIDATE_PATHS = cli._reviewer_candidate_paths
_AO_CHILD_PATH = W.child_path


@functools.wraps(_AO_BINARY_CANDIDATES)
def _binary_candidates(name, path=None):
    return _found_outside(_AO_BINARY_CANDIDATES(name, path))


@functools.wraps(_AO_REVIEWER_CANDIDATE_PATHS)
def _reviewer_candidate_paths(name, deadline=None):
    return _found_outside(_AO_REVIEWER_CANDIDATE_PATHS(name, deadline))


@functools.wraps(_AO_CHILD_PATH)
def _child_path():
    return os.pathsep.join(d for d in _AO_CHILD_PATH().split(os.pathsep) if d and _outside_shared_install_dirs(d))


# `file:function` in ao's source -> (the module it runs in, its name there, what runs in its place).
KEPT_OFF_SHARED = {
    "src/ao/parts/lib_mail.py:binary_candidates": (A, "binary_candidates", _binary_candidates),
    "src/ao/parts/cli_review.py:_reviewer_candidate_paths": (cli, "_reviewer_candidate_paths",
                                                            _reviewer_candidate_paths),
    "src/ao/watchdog.py:child_path": (W, "child_path", _child_path),
}
for _module, _name, _in_its_place in KEPT_OFF_SHARED.values():
    setattr(_module, _name, _in_its_place)


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
    # every test's ~/.ao is its own, apart from the suite's home and from every other test's
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(A, "HOME", str(home))
    monkeypatch.setattr(W, "STATE_DIR", str(home / ".ao"))   # bound at import, from the suite's home
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
