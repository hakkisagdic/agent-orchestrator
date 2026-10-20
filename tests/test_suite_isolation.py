"""A test runs on a machine of the suite's own: not the person's home, PATH, variables or agent CLIs (TRUST-HYGIENE).

ao binds what it keeps on a machine when a module is imported - telegram.CONF, email.CONF, the
watchdog's STATE_DIR - and those were the person's own files: a test that did not point them away
could reach their phone and their mail. A program a test started by name was looked for on their
PATH, where their agent CLIs are, ao read the files their AO_ variables name, and ao's own search
looks in shared package-manager and adapter-declared directories whatever PATH says. tests/conftest.py gives the suite a
home, a PATH and an environment of its own before ao is imported, and keeps every search of ao's in
this process out of those directories. These hold it to that.
"""
import ast
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ao import cli, email, lib as A, telegram, watchdog as W
from tests import conftest

POSIX_SYSTEM = {"/usr/bin", "/bin", "/usr/sbin", "/sbin"}
# The variables the suite's own fixtures set for each test (tests/conftest.py).
SET_FOR_EACH_TEST = {"AO_SETTINGS", "AO_PROJECT_REGISTRY", "AO_LEDGER_CHECKPOINTS", "AO_EVENTS"}
# Places in ao's source that name _BIN_DIRS or a shared install directory without looking there for an agent CLI.
NOT_A_SEARCH_FOR_AN_AGENT = {
    "src/ao/parts/lib_mail.py:<module>": "binds _BIN_DIRS, which the searches read",
    "src/ao/parts/lib_mail.py:_SearchDirs._dirs": "names the directories _BIN_DIRS holds",
    "src/ao/parts/lib_gates.py:_find_git_binary": "looks there for git alone",
}


def _executable(path, text="#!/bin/sh\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _scopes(node, scope="<module>", prefix=""):
    """(scope, node) for each node under `node`: the function or method it is in, the outermost, or <module>."""
    for child in ast.iter_child_nodes(node):
        if scope == "<module>" and isinstance(child, ast.ClassDef):
            yield from _scopes(child, scope, prefix + child.name + ".")
        elif scope == "<module>" and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _scopes(child, prefix + child.name, prefix)
        else:
            yield scope, child
            yield from _scopes(child, scope, prefix)


def _names_a_shared_directory(node):
    return (isinstance(node, ast.Name) and node.id == "_BIN_DIRS") \
        or (isinstance(node, ast.Attribute) and node.attr == "_BIN_DIRS") \
        or (isinstance(node, ast.Constant) and node.value in ("_BIN_DIRS",) + conftest.SHARED_INSTALL_DIRS)


def _places_that_look_past_path():
    """`file:scope` for each place in ao's source that names _BIN_DIRS or a shared install directory."""
    found = set()
    for path in sorted(Path(conftest.SRC, "ao").rglob("*.py")):
        relative = path.relative_to(conftest.REPO).as_posix()
        for scope, node in _scopes(ast.parse(path.read_text(encoding="utf-8"), relative)):
            if _names_a_shared_directory(node):
                found.add(f"{relative}:{scope}")
    return found


def test_the_home_is_the_suites_and_every_machine_file_ao_bound_at_import_is_inside_it():
    assert os.path.expanduser("~") == conftest.SESSION_HOME != conftest.REAL_HOME
    for bound in (A.HOME, W.STATE_DIR, email.CONF, telegram.CONF):
        assert conftest._within(bound, conftest.SESSION_HOME), bound


def test_no_variable_of_aos_or_one_pointing_home_is_taken_from_the_environment_the_suite_started_in():
    started_in = {"AO_USER_ADAPTERS": "/x/adapters", "AO_SETTINGS": "/x/settings.json", "AO_GIT": "/x/git",
                  "AO_ROLE": "implementer", "AO_A_VARIABLE_AO_HAS_NOT_NAMED_YET": "/x", "AO_FUZZ_SEED": "7",
                  "AO_FUZZ_SEEDS": "80", "XDG_CONFIG_HOME": "/x/.config", "GIT_CONFIG_GLOBAL": "/x/.gitconfig",
                  "HOME": "/x", "USERPROFILE": "/x", "LANG": "C.UTF-8"}

    conftest._own_environment(started_in)

    assert started_in == {"AO_FUZZ_SEED": "7", "AO_FUZZ_SEEDS": "80", "HOME": conftest.SESSION_HOME,
                          "USERPROFILE": conftest.SESSION_HOME, "LANG": "C.UTF-8"}
    assert {name for name in os.environ if name.startswith("AO_")} <= SET_FOR_EACH_TEST | set(conftest.SUITE_SWITCHES)


def test_git_reads_the_suites_home_so_a_commit_that_names_nobody_is_the_suites():
    asked = subprocess.run([conftest.GIT, "config", "--global", "--get-regexp", "^user\\."], capture_output=True,
                           text=True, check=True).stdout.splitlines()

    assert asked == ["user.name " + conftest.GIT_IDENTITY[0], "user.email " + conftest.GIT_IDENTITY[1]]


def test_git_runs_no_maintenance_of_its_own_in_a_tests_repository(tmp_path):
    """A commit started Git's maintenance detached, and it pruned what a test had just made in the repository
    as the test read it (SUITE-MAINTENANCE)."""
    asked = subprocess.run([conftest.GIT, "config", "--global", "--get-regexp", "^(maintenance|gc)\\.auto$"],
                           capture_output=True, text=True, check=True).stdout.splitlines()

    assert sorted(asked) == ["gc.auto 0", "maintenance.auto false"]
    subprocess.run([conftest.GIT, "init", "-q", str(tmp_path)], check=True)
    assert subprocess.run([conftest.GIT, "-C", str(tmp_path), "config", "--type=bool", "maintenance.auto"],
                          capture_output=True, text=True, check=True).stdout.strip() == "false"



def _blocked_in_a_child():
    """The signals a process this test starts finds blocked."""
    said = subprocess.run([sys.executable, "-c", "import signal; "
                           "print(*sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, [])))"],
                          capture_output=True, text=True, check=True).stdout.split()
    return {int(number) for number in said}


@pytest.mark.skipif(not hasattr(signal, "pthread_sigmask"), reason="Windows keeps no signal mask")
def test_a_stop_signal_blocked_where_the_suite_started_is_unblocked_for_every_process_a_test_starts():
    """A landing daemon's shell had come to block SIGINT, and each `ao` a lane test started inherited the block:
    the test's SIGINT never arrived (SUITE-SIGMASK)."""
    stop = {int(signal.SIGINT), int(signal.SIGTERM), int(signal.SIGHUP)}
    assert not _blocked_in_a_child() & stop
    before = signal.pthread_sigmask(signal.SIG_BLOCK, stop)
    try:
        assert _blocked_in_a_child() >= stop                    # what a process blocks, the one it starts inherits
        conftest._own_signals()
        assert not _blocked_in_a_child() & stop
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, before)



@pytest.mark.skipif(os.name == "nt", reason="Windows has no SIGHUP, and no signal a parent ignores passes an exec")
def test_an_ao_a_test_starts_hears_the_stop_signals_a_shell_or_nohup_started_the_suite_ignoring():
    """A shell runs a command it puts in the background with SIGINT ignored and nohup ignores SIGHUP; an ao keeps
    what it was set to ignore, so CALM_AO starts it as a terminal does (SUITE-SIGMASK)."""
    report = ("import signal; print(signal.getsignal(signal.SIGINT) is signal.default_int_handler, "
              "signal.getsignal(signal.SIGTERM) is signal.SIG_DFL, signal.getsignal(signal.SIGHUP) is signal.SIG_DFL)")
    ignoring = ("import os, signal, sys\n"
                "signal.signal(signal.SIGINT, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
                "os.execv(sys.executable, [sys.executable, '-c', sys.argv[1]])\n")

    def started_ignoring(code):
        return subprocess.run([sys.executable, "-c", ignoring, code], capture_output=True, text=True,
                              check=True).stdout.split()

    assert started_ignoring(report)[::2] == ["False", "False"]              # what was ignored stays ignored
    assert started_ignoring(conftest.AS_A_TERMINAL_STARTS_IT + report) == ["True", "True", "True"]
    assert conftest.CALM_AO[2].startswith(conftest.AS_A_TERMINAL_STARTS_IT)


def test_path_is_the_suites_bin_then_the_directory_that_holds_git_alone_then_the_systems_own():
    entries = os.environ["PATH"].split(os.pathsep)
    git = [conftest.GIT_DIRECTORY] if conftest.GIT_DIRECTORY else []

    assert entries == [conftest.SESSION_BIN] + git + [d for d in conftest.SYSTEM_DIRS if d not in git]
    if git:                                   # the git ao measures with, and the one a test starts by name
        assert os.path.dirname(conftest.GIT) == conftest.GIT_DIRECTORY
        assert conftest._within(shutil.which("git"), conftest.GIT)


@pytest.mark.skipif(os.name == "nt", reason="on Windows the directory is the one git.exe was found in")
def test_the_directory_that_holds_git_is_gits_exec_path_and_git_started_from_it_finds_its_own_files(tmp_path):
    if conftest.GIT_DIRECTORY is None:
        pytest.skip("this machine's git keeps no git of its own in its exec-path")
    # Started from its exec-path, a git built with a runtime prefix finds its helpers there and its
    # templates under the prefix; started through a link anywhere else, Apple's git found neither and
    # `git init` warned that it had no templates.
    asked = subprocess.run([conftest.GIT, "--exec-path"], capture_output=True, text=True, check=True).stdout.strip()
    made = subprocess.run([conftest.GIT, "init", "-q", str(tmp_path / "repo")], capture_output=True, text=True,
                          check=True)

    assert os.path.realpath(asked) == os.path.realpath(conftest.GIT_DIRECTORY)
    assert made.stderr == "" and (tmp_path / "repo" / ".git" / "info").is_dir()
    assert conftest._outside_shared_install_dirs(conftest.GIT_DIRECTORY)
    assert not conftest._holds_an_agent_cli(conftest.GIT_DIRECTORY)


@pytest.mark.skipif(os.name == "nt", reason="Windows runs no shebang line, and the suite puts no python on PATH there")
def test_a_script_a_test_starts_by_its_shebang_runs_this_interpreter_in_the_suites_home(tmp_path):
    script = _executable(tmp_path / "fake-agent", "#!/usr/bin/env python3\nimport os, sys\n"
                                                  "print(sys.executable)\nprint(os.environ['HOME'])\n")

    ran = subprocess.run([str(script)], capture_output=True, text=True, check=True, timeout=60)

    assert ran.stdout.splitlines() == [sys.executable, conftest.SESSION_HOME]


def test_no_agent_cli_is_within_a_tests_reach_outside_the_temporary_and_the_systems_directories():
    allowed = [tempfile.gettempdir()] + conftest.SYSTEM_DIRS
    reached = {}
    for name in conftest._agent_programs():
        for found in ([shutil.which(name), shutil.which(name, path=W.child_path())] + A.binary_candidates(name)
                      + cli._reviewer_candidate_paths(name)):
            if found and not any(conftest._within(found, root) for root in allowed):
                reached.setdefault(name, []).append(found)

    assert reached == {}


def test_ao_finds_no_program_in_a_shared_install_directory_on_path_or_off_it(tmp_path, monkeypatch):
    # Only looked for, never run: a resolve would ask each copy it found for its --version.
    shared, own = tmp_path / "shared", tmp_path / "own"
    _executable(shared / "claude")
    _executable(own / "claude")
    monkeypatch.setattr(conftest, "SHARED_INSTALL_DIRS", conftest.SHARED_INSTALL_DIRS + (str(shared),))
    monkeypatch.setenv("PATH", os.pathsep.join([str(shared), str(own)]))

    assert A.binary_candidates("claude") == [str(own / "claude")]
    assert cli._reviewer_candidate_paths("claude") == [str(own / "claude")]
    handed = W.child_path().split(os.pathsep)
    assert str(own) in handed and str(shared) not in handed

    monkeypatch.setattr(A, "_BIN_DIRS", (str(shared),))    # off PATH: where ao looks after it
    monkeypatch.setenv("PATH", "")
    assert A.binary_candidates("claude") == []
    assert cli._reviewer_candidate_paths("claude") == []


def test_every_place_ao_looks_past_path_for_a_program_is_run_with_the_shared_directories_left_out():
    kept_off = conftest.KEPT_OFF_SHARED

    assert _places_that_look_past_path() == set(kept_off) | set(NOT_A_SEARCH_FOR_AN_AGENT)
    for place, (module, name, in_its_place) in kept_off.items():
        original = in_its_place.__wrapped__
        source = os.path.relpath(os.path.realpath(original.__code__.co_filename), os.path.realpath(conftest.REPO))
        assert f"{Path(source).as_posix()}:{original.__qualname__}" == place
        assert getattr(module, name) is in_its_place, place


def test_the_directories_kept_out_of_reach_are_the_ones_ao_names_past_path_outside_every_home(monkeypatch):
    searched = {d for d in A._SearchDirs() if not d.startswith("~")}
    monkeypatch.setenv("PATH", "")
    handed = {d for d in conftest._AO_CHILD_PATH().split(os.pathsep)
              if d and not conftest._within(d, conftest.SESSION_HOME)} - POSIX_SYSTEM

    assert searched == handed == set(conftest.SHARED_INSTALL_DIRS)


def test_a_new_package_install_directory_reaches_children_and_is_kept_off_tests(tmp_path, monkeypatch):
    """A bundle path must work for any adapter, without a harness-specific exception (WARP-ADAPTER)."""
    directory = str(tmp_path / "new-install" / "bin")
    adapters = dict(A.package_adapters(), newcomer={"detect": {"install_dirs": [directory]}})
    monkeypatch.setattr(A, "package_adapters", lambda: adapters)
    monkeypatch.setenv("PATH", "")
    _executable(Path(directory) / "newcomer")  # discovered, never started

    assert directory in A._SearchDirs()
    assert A.binary_candidates("newcomer") == [os.path.join(directory, "newcomer")]
    assert directory in conftest._AO_CHILD_PATH().split(os.pathsep)
    monkeypatch.setattr(conftest, "SHARED_INSTALL_DIRS", conftest._shared_install_dirs())
    assert directory in conftest.SHARED_INSTALL_DIRS
    assert A.binary_candidates("newcomer") == []
    assert cli._reviewer_candidate_paths("newcomer") == []
    assert directory not in W.child_path().split(os.pathsep)


def test_a_human_notice_in_a_test_reaches_the_stand_in_and_starts_no_desktop_notification_program(
    project, monkeypatch, desktop_notifications,
):
    """tests/test_notify_ntfy.py called watchdog.notify(..., audience="human") with ntfy and Telegram
    stubbed while watchdog.desktop_notify still ran osascript on the owner's Mac (SUITE-NO-BANNER)."""
    real_run = subprocess.run

    def guarded_run(argv, *args, **kwargs):
        first = argv[0] if isinstance(argv, (list, tuple)) and argv else argv
        program = os.path.splitext(os.path.basename(str(first).replace("\\", "/")))[0].lower()
        if program in ("osascript", "powershell", "pwsh"):
            pytest.fail(f"a test started desktop notification program {first!r}")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)

    assert W.desktop_notify is not conftest._DESKTOP_NOTIFY
    assert W.notify("cap hit", "daily cap reached", root=project["root"], key="cap", audience="human") is True
    assert desktop_notifications == [("cap hit", "daily cap reached")]
    assert W.desktop_notify.sent == [("cap hit", "daily cap reached")]
