"""The commit hook reads an index path that starts with a drive letter as absolute (HOOK-V4).

The hook ao installs is a POSIX sh script, and it told an absolute index path by its leading slash
alone. Under Git for Windows' shell a drive-letter path - the temporary index the execution proof
hands Git, or a linked worktree's index - was taken for a relative one and put after the directory
Git ran the hook in, so ao read another index than the one Git commits, and refused: no proof passed
on Windows. One ASCII letter, a colon and a slash or a backslash now start an absolute path as well.
That is hook version 4, so the exact v3 hook reads as legacy and `ao hooks install` puts v4 in its
place. Here the lines of the hook that read the path run under sh, and Git runs no hook; on Windows
the execution-proof tests in tests/test_hook_paths.py and tests/test_init_adoption.py run them under
Git's own shell.
"""
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli
from tests.test_hook_paths import SHARED_HOOKS_REFUSED, _active, _git

NO_BIN_SH = ("Windows has no /bin/sh; there the execution-proof tests run these lines under Git's own shell, "
             "with the drive-letter index the proof makes")

# How Git names an index on Windows: the proof's temporary index, and a linked worktree's.
DRIVE_LETTER = ("C:/repo/.git/index", "C:\\repo\\.git\\index", "c:/repo/.git/worktrees/linked/index",
                "Z:\\temp\\ao-hook-probe-0\\index")
# Relative, each of them: no slash after the colon, two letters, a digit.
RELATIVE = ("index", ".git/next-index-1.lock", "C:index", "CD:/index", "1:/index")

# The v3 pre-commit hook of a project at the top of its repository, byte for byte: what ao installed,
# and what an enrolled repository holds until `ao hooks install` replaces it.
V3_PRE_COMMIT = (
    b"#!/bin/sh\n"
    b"# agent-orchestrator: ao-hook-v3 role=pre-commit binding=project-local\n"
    b"initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
    b'root="$initial_cwd"\n'
    b"case ${GIT_INDEX_FILE-} in\n"
    b'  ""|/*) ;;\n'
    b'  *) GIT_INDEX_FILE="$initial_cwd/$GIT_INDEX_FILE"; export GIT_INDEX_FILE ;;\n'
    b"esac\n"
    b"unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES "
    b"GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_NOSYSTEM "
    b"GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_LITERAL_PATHSPECS GIT_GLOB_PATHSPECS "
    b"GIT_NOGLOB_PATHSPECS GIT_ICASE_PATHSPECS\n"
    b'if ! git --literal-pathspecs -C "$root" ls-files --error-unmatch -- .ao-project >/dev/null 2>&1 &&\n'
    b'   ! git --literal-pathspecs -C "$root" cat-file -e HEAD:.ao-project 2>/dev/null; then\n'
    b"  exit 0\n"
    b"fi\n"
    b"ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
    b'exec "$ao" -C "$root" commit-check\n'
)
FALLBACK = "/opt/ao tool/bin/ao"
INSTALL = SimpleNamespace(action="install", allow_shared_hooks=False)


def _body(binding):
    """The current pre-commit hook for a project at the top of its repository, bound as `binding` says."""
    if binding == "project-local":
        return cli._render_local_hook("pre-commit", ".")
    return cli._render_scoped_hook("pre-commit", ".", "/repo/.git", binding)


def _reading(body):
    """The lines of a hook body that read GIT_INDEX_FILE: where the hook started, and the case after it."""
    text = body.decode("utf-8")
    start = next(line for line in text.splitlines(keepends=True) if line.startswith("initial_cwd="))
    case = text[text.index("case ${GIT_INDEX_FILE-} in\n"):]
    return start + case[:case.index("esac\n") + len("esac\n")]


def _handed_on(body, cwd, value):
    """GIT_INDEX_FILE as those lines leave it for ao, where Git ran the hook in `cwd` with `value` (None: unset)."""
    env = {key: val for key, val in os.environ.items() if key != "GIT_INDEX_FILE"}
    if value is not None:
        env["GIT_INDEX_FILE"] = value
    script = _reading(body) + 'printf "%s" "${GIT_INDEX_FILE-(unset)}"\n'
    ran = subprocess.run(["/bin/sh", "-c", script], cwd=str(cwd), env=env, capture_output=True, text=True,
                         timeout=60)
    assert (ran.returncode, ran.stderr) == (0, ""), ran.stderr
    return ran.stdout


def _v3_as_installed(body, fallback):
    """A v3 body as install wrote it: naming the ao that installed it where the hook cannot travel (SAFE-REMOVE)."""
    if fallback is None:
        return body
    return body.replace(cli._AO_LOOKUP.encode("utf-8"), cli._ao_lookup(fallback).encode("utf-8"), 1)


@pytest.mark.skipif(os.name == "nt", reason=NO_BIN_SH)
@pytest.mark.parametrize("binding", ("project-local", "shared"))
def test_the_hook_leaves_a_drive_letter_index_as_git_gave_it_and_joins_a_relative_one(tmp_path, binding):
    body = _body(binding)
    here = os.path.realpath(tmp_path)

    for value in DRIVE_LETTER + ("/repo/.git/index", ""):
        assert _handed_on(body, tmp_path, value) == value
    for value in RELATIVE:
        assert _handed_on(body, tmp_path, value) == here + "/" + value
    assert _handed_on(body, tmp_path, None) == "(unset)"


@pytest.mark.skipif(os.name == "nt", reason=NO_BIN_SH)
def test_the_v3_hook_put_the_directory_before_a_drive_letter_index(tmp_path):
    here = os.path.realpath(tmp_path)

    for value in DRIVE_LETTER:
        assert _handed_on(V3_PRE_COMMIT, tmp_path, value) == here + "/" + value


def test_v4_is_the_v3_hook_with_its_version_and_the_drive_letter_arm_and_nothing_else():
    arm = (b'  ""|/*) ;;\n', b'  ""|/*|[A-Za-z]:[\\\\/]*) ;;\n')
    pairs = [(cli._render_v3_local_hook(role, rel), cli._render_local_hook(role, rel))
             for role in ("pre-commit", "pre-push") for rel in (".", "sub dir")]
    pairs += [(cli._render_v3_scoped_hook(role, ".", "/repo/.git", binding),
               cli._render_scoped_hook(role, ".", "/repo/.git", binding))
              for role in ("pre-commit", "pre-push") for binding in ("shared", "external")]

    assert cli._render_v3_local_hook("pre-commit", ".") == V3_PRE_COMMIT
    for v3, v4 in pairs:
        assert v3.count(arm[0]) == 1
        assert v3.replace(b"ao-hook-v3 ", b"ao-hook-v4 ").replace(*arm) == v4


@pytest.mark.parametrize(("hooks_path", "fallback"), ((None, FALLBACK), (".githooks", None)),
                         ids=("git-directory", "hooks-path-in-the-tree"))
def test_an_exact_v3_hook_reads_as_legacy_and_install_puts_v4_in_its_place(project, hooks_path, fallback):
    root = project["root"]
    if hooks_path:
        _git(root, "config", "core.hooksPath", hooks_path)
    hooks = Path(cli._ao_hook_inventory(root)["active_dir"])
    hooks.mkdir(parents=True, exist_ok=True)
    for role in ("pre-commit", "pre-push"):
        (hooks / role).write_bytes(_v3_as_installed(cli._render_v3_local_hook(role, "."), fallback))

    before = cli._ao_hook_inventory(root)
    finding = dict(cli.doctor_problems(project))["commit-hook"]

    assert [(_active(before, role)["static_state"], _active(before, role)["eligible"])
            for role in ("pre-commit", "pre-push")] == [("legacy (behavior unverified)", True)] * 2
    assert "legacy (behavior unverified)" in finding and "ao hooks install" in finding
    assert cli.cmd_hooks(project, INSTALL) == 0
    after = cli._ao_hook_inventory(root)
    for role in ("pre-commit", "pre-push"):
        target = _active(after, role)
        assert target["static_state"] == "current-local (behavior unverified)"
        assert (hooks / role).read_bytes() == target["body"]
        assert b"# agent-orchestrator: ao-hook-v4 " in target["body"]


@pytest.mark.skipif(os.name == "nt", reason=SHARED_HOOKS_REFUSED)
def test_an_exact_v3_shared_hook_reads_as_legacy_and_install_puts_v4_in_its_place(project, tmp_path):
    root = project["root"]
    _git(root, "worktree", "add", "-q", "-b", "linked", str(tmp_path / "linked"))
    shared = cli._ao_hook_inventory(root)
    hooks = Path(shared["active_dir"])
    for role in ("pre-commit", "pre-push"):
        body = cli._render_v3_scoped_hook(role, ".", shared["common_dir"], "shared")
        (hooks / role).write_bytes(_v3_as_installed(body, FALLBACK))

    before = cli._ao_hook_inventory(root)

    assert before["directory_class"] == "shared"
    assert [_active(before, role)["static_state"] for role in ("pre-commit", "pre-push")] == \
        ["legacy (behavior unverified)"] * 2
    assert cli.cmd_hooks(project, SimpleNamespace(action="install", allow_shared_hooks=True)) == 0
    after = cli._ao_hook_inventory(root)
    for role in ("pre-commit", "pre-push"):
        target = _active(after, role)
        assert target["static_state"] == "current-scoped (behavior unverified)"
        assert (hooks / role).read_bytes() == target["body"]
        assert b"# agent-orchestrator: ao-hook-v4 " in target["body"]
