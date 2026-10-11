"""ao never starts a program the current directory holds (WINDOWS-NO-CWD-EXEC).

Windows looks for a program named without a directory in the current directory before PATH:
`subprocess` with a bare `taskkill`, `schtasks`, `git` or `cmd.exe`, and `shutil.which`, which before
Python 3.12 put the current directory first whatever path it was given. ao runs in a tree an agent
writes, so a `taskkill.exe`, `schtasks.exe`, `git.exe` or `powershell.exe` - or a `.bat`, `.cmd` or `.com`
PATHEXT admits - left in the repository root would have run as the person: when ao stopped a turn, read
the process table, scheduled its jobs or committed. An empty or relative PATH entry names the same
directory, or one below it, on Windows and POSIX alike, and ao's own searches took them too.

One resolver now starts every program: Windows' own tools from its system directory and nowhere else,
every other program from PATH's absolute entries in order, never the current directory, honouring
PATHEXT. `ao doctor` names an empty or relative PATH entry on Windows. Each failure is made here as
Windows makes it, on every platform: a system directory of the test's own, a repository with a program
planted under each name, and Windows' own lookup in place of shutil.which's.
"""
import json
import os
import shutil
import signal
import sys
from types import SimpleNamespace

import pytest

from ao import acp, cli, lib as A, procs

SYSTEM_PROGRAMS = ("taskkill", "schtasks", "tasklist", "cmd", "where", "powershell")
PLANTED = ("taskkill.exe", "schtasks.exe", "tasklist.exe", "cmd.exe", "where.exe", "powershell.exe",
           "git.exe", "git.bat", "ao.cmd", "claude.com")


def _program(path, mode=None):
    """A program file: a PE header, which is what makes a git a compiled one to ao."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    if mode is not None:
        path.chmod(mode)
    return path


def _windows_which(name, mode=None, path=None):
    """shutil.which as Windows had it before Python 3.12: the current directory first whatever path it is given,
    then each directory, each name with a PATHEXT extension."""
    path = os.environ.get("PATH", "") if path is None else path
    if not path:
        return None
    extensions = [extension.lower() for extension in os.environ["PATHEXT"].split(";") if extension]
    for directory in [os.curdir] + path.split(os.pathsep):
        for extension in extensions:
            candidate = os.path.join(directory, name + extension)
            if os.path.isfile(candidate):
                return candidate
    return None


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """A Windows of the test's own: its system directory, a repository an agent wrote into as the current
    directory with a program planted under each name ao starts, and a PATH whose one good entry is `tools`."""
    windows_dir, repo, tools = tmp_path / "Windows", tmp_path / "repo", tmp_path / "tools"
    system = windows_dir / "System32"
    for name in SYSTEM_PROGRAMS:
        _program(system.joinpath(*A.WINDOWS_SYSTEM_PROGRAMS[name]))
    for name in PLANTED:
        _program(repo / name)
    _program(repo / "bin" / "git.exe")
    _program(tools / "git.exe", mode=0o755)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("SystemRoot", str(windows_dir))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "bin", str(repo), str(tools)]))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("AO_GIT", raising=False)
    monkeypatch.setattr(shutil, "which", _windows_which)
    return SimpleNamespace(system=system, repo=repo, tools=tools)


def _as_windows(monkeypatch, ask):
    """What `ask` returns where os.name is Windows' - only while it asks, so a failure is reported in this
    platform's paths."""
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        return ask()


# ---- the resolver ------------------------------------------------------------------------------

def test_windows_own_programs_come_from_its_system_directory_and_from_nowhere_else(windows, monkeypatch):
    found = {name: _as_windows(monkeypatch, lambda: A.program_path(name)) for name in SYSTEM_PROGRAMS}

    assert found == {name: str(windows.system.joinpath(*A.WINDOWS_SYSTEM_PROGRAMS[name])) for name in SYSTEM_PROGRAMS}
    assert _as_windows(monkeypatch, lambda: A.program_path("taskkill.exe")) == str(windows.system / "taskkill.exe")
    # The process table's PowerShell, as procs asks for it.
    assert _as_windows(monkeypatch, lambda: A.runnable_binary("powershell")) == found["powershell"]


def test_a_system_program_windows_does_not_hold_is_none_never_the_one_planted(windows, monkeypatch):
    (windows.system / "taskkill.exe").unlink()

    assert _as_windows(monkeypatch, lambda: A.program_path("taskkill")) is None
    with pytest.raises(FileNotFoundError, match="taskkill is not in Windows' system directory"):
        _as_windows(monkeypatch, lambda: A.stop_windows_tree(4242))


def test_every_other_program_comes_from_paths_own_directories_never_the_current_one(windows, monkeypatch):
    """Windows' lookup answers the git.exe planted in the current directory first; ao takes the one in `tools`,
    the only entry that is absolute and not the current directory."""
    assert _windows_which("git", path=str(windows.tools)) == os.path.join(os.curdir, "git.exe")

    assert _as_windows(monkeypatch, lambda: A.program_path("git")) == str(windows.tools / "git.exe")
    assert _as_windows(monkeypatch, lambda: A.program_path("ao")) is None           # ao.cmd is the planted one
    assert _as_windows(monkeypatch, lambda: A.program_path("claude")) is None       # so is claude.com


@pytest.mark.parametrize("name", ["./git", "bin/git", os.path.join("bin", "git.exe")])
def test_a_name_relative_to_the_current_directory_names_no_program(windows, monkeypatch, name):
    assert _as_windows(monkeypatch, lambda: A.program_path(name)) is None


def test_the_directories_searched_are_paths_absolute_entries_in_order_and_never_the_current_one(windows):
    assert A.search_directories() == [str(windows.tools)]
    assert A.unsafe_path_entries() == ["", ".", "bin"]
    assert A.search_directories(os.pathsep.join([str(windows.tools), "bin", str(windows.repo)])) == \
        [str(windows.tools)]


@pytest.mark.skipif(os.name == "nt", reason="POSIX's own lookup: the execute bit, no PATHEXT")
def test_posix_looks_as_it_did_but_never_in_an_empty_or_relative_entry(tmp_path, monkeypatch):
    """shutil.which on POSIX reads an empty entry as the current directory and a relative one against it, and
    found the git planted in the repository through either."""
    repo, tools = tmp_path / "repo", tmp_path / "tools"
    for program in (repo / "git", repo / "bin" / "git", tools / "git"):
        _program(program, mode=0o755)
    monkeypatch.chdir(repo)

    monkeypatch.setenv("PATH", os.pathsep.join(["", "bin", ".", str(repo), str(tools)]))
    assert shutil.which("git") == "git"                              # what ao asked before
    assert A.program_path("git") == str(tools / "git")
    monkeypatch.setenv("PATH", os.pathsep.join(["", "bin", "."]))
    assert A.program_path("git") is None


def test_the_binary_search_and_reviewer_discovery_skip_a_relative_entry(tmp_path, monkeypatch):
    repo, tools = tmp_path / "repo", tmp_path / "tools"
    for program in (repo / "bin" / "claude", tools / "claude"):
        _program(program, mode=0o755)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", os.pathsep.join(["bin", str(tools)]))

    assert A.binary_candidates("claude") == [str(tools / "claude")]
    assert cli._reviewer_candidate_paths("claude") == [str(tools / "claude")]


# ---- what ao starts ----------------------------------------------------------------------------

def _started(monkeypatch):
    """What subprocess.run is asked to start, each argument vector with its keyword arguments, and nothing run."""
    started = []
    monkeypatch.setattr(A.subprocess, "run", lambda argv, **kwargs: started.append((argv, kwargs)) or
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(procs, "refresh", lambda: None)
    return started


def test_a_turn_and_an_acp_agent_are_stopped_by_windows_own_taskkill(windows, monkeypatch):
    started = _started(monkeypatch)
    agent = SimpleNamespace(poll=lambda: None, pid=4343, stdin=None, stdout=None, wait=lambda timeout=None: 0,
                            kill=lambda: None)

    _as_windows(monkeypatch, lambda: A.kill_turn(4242, signal.SIGTERM))
    _as_windows(monkeypatch, lambda: acp._stop(agent))

    taskkill = str(windows.system / "taskkill.exe")
    assert [argv for argv, _ in started] == [[taskkill, "/T", "/F", "/PID", "4242"],
                                             [taskkill, "/T", "/F", "/PID", "4343"]]


def test_the_jobs_are_scheduled_with_windows_own_schtasks(windows, monkeypatch):
    started = _started(monkeypatch)

    _as_windows(monkeypatch, lambda: cli._schtasks("/Query", "/TN", "ao-watchdog-proj"))

    schtasks = str(windows.system / "schtasks.exe")
    assert [argv for argv, _ in started] == [[schtasks, "/Query", "/TN", "ao-watchdog-proj"]]


def test_a_command_line_runs_in_windows_own_cmd_which_looks_in_path_alone(windows, monkeypatch):
    started = _started(monkeypatch)

    _as_windows(monkeypatch, lambda: A.sh("keyflip surfaces 2>/dev/null"))

    [(command, kwargs)] = started
    assert command == "keyflip surfaces 2>NUL" and kwargs["shell"] is True
    assert kwargs["executable"] == str(windows.system / "cmd.exe")
    assert kwargs["env"]["NoDefaultCurrentDirectoryInExePath"] == "1"
    assert _as_windows(monkeypatch, A.shell_program) == str(windows.system / "cmd.exe")
    if os.name != "nt":
        assert A.shell_program() is None                              # POSIX starts /bin/sh by its path


def test_the_git_ao_measures_with_is_never_the_one_planted_nor_the_bare_name(windows, monkeypatch):
    assert _as_windows(monkeypatch, A._find_git_binary) == str(windows.tools / "git.exe")

    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "bin", str(windows.repo)]))
    installed = os.path.join(os.environ["ProgramFiles"], "Git", "cmd", "git.exe")
    assert _as_windows(monkeypatch, A._find_git_binary) == installed     # fails to start, as no git would


def test_a_command_named_by_its_name_is_the_one_path_holds(windows, project, monkeypatch, capsys):
    """`ao lock -- <command>` and a codebase provider: by its name a program is the one on PATH, by a path
    that file."""
    started = _started(monkeypatch)
    monkeypatch.setattr(A, "gate_lock_holder", lambda: None)
    monkeypatch.setattr(A, "acquire_gate_lock", lambda root, wait: True)
    monkeypatch.setattr(A, "release_gate_lock", lambda: None)

    def lock(*command):
        return _as_windows(monkeypatch, lambda: cli.cmd_lock(project, SimpleNamespace(command=["--", *command],
                                                                                      wait=0)))

    assert lock("git", "status") == 0
    assert lock("ao", "status") == 127 and "ao is not on PATH" in capsys.readouterr().out
    cfg = dict(project, codebase={"provider": {"argv": ["claude", "{question}"]}})
    with pytest.raises(ValueError, match="the codebase provider claude is not on PATH"):
        _as_windows(monkeypatch, lambda: A.ask_codebase(project["root"], cfg, "where is the ledger?"))
    assert [argv for argv, _ in started] == [[str(windows.tools / "git.exe"), "status"]]


def test_windows_is_asked_for_its_system_directory_when_systemroot_names_none(monkeypatch):
    for variable in ("SystemRoot", "SYSTEMROOT", "windir", "WINDIR"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SystemRoot", "Windows")                       # not absolute: no directory at all
    monkeypatch.setattr(procs, "system_directory", lambda: os.path.join(os.sep, "D", "Windows", "System32"))
    assert A.windows_system_directory() == os.path.join(os.sep, "D", "Windows", "System32")

    monkeypatch.setattr(procs, "system_directory", lambda: None)
    assert A.windows_system_directory() == "C:\\Windows\\System32"


def test_windows_alone_answers_for_its_system_directory():
    found = procs.system_directory()

    assert (found is None) == (sys.platform != "win32")
    assert found is None or os.path.isfile(os.path.join(found, "cmd.exe"))


# ---- what a person is told ---------------------------------------------------------------------

def test_the_doctor_names_an_empty_or_relative_path_entry_on_windows(windows, monkeypatch, capsys):
    problems = _as_windows(monkeypatch, cli._path_problems)

    assert problems == [("path-relative", "PATH holds an empty entry, `.`, `bin`: each names the directory a program "
                                          "is started in, or one below it, and that can be a tree an agent writes; "
                                          "ao starts no program found only through them - take them out of PATH")]
    if os.name != "nt":
        assert cli._path_problems() == []                             # elsewhere ao says nothing of it
    monkeypatch.setenv("PATH", str(windows.tools))
    assert _as_windows(monkeypatch, cli._path_problems) == []



def test_the_mcp_config_ao_prints_names_no_bare_program(project, monkeypatch, capsys):
    """From the review of WINDOWS-NO-CWD-EXEC: with no ao on PATH the printed config named `sys.argv[0]`, which can be
    a bare `ao` that a client started in the project would look for there; it names this interpreter running ao."""
    from types import SimpleNamespace
    monkeypatch.setattr(A, "program_path", lambda name, path=None: None)
    monkeypatch.setattr(sys, "argv", ["ao", "mcp"])

    assert cli.cmd_mcp(project, SimpleNamespace(action="config", role=None, allow_verify=False)) == 0

    printed = json.loads(capsys.readouterr().out.split("\n", 1)[1])["mcpServers"]["agent-orchestrator"]
    assert printed["command"] == sys.executable and os.path.isabs(printed["command"])
    assert printed["args"][:2] == ["-m", "ao"]



def test_every_place_ao_names_itself_names_an_absolute_program(project, monkeypatch, capsys, tmp_path):
    """From the second review of WINDOWS-NO-CWD-EXEC: `ao a2a-mcp config` and the MCP registration `ao init` writes fell
    back on `sys.argv[0]` too, made absolute against the current directory, where an agent could leave an `ao`."""
    from ao import skillkit
    monkeypatch.setattr(A, "program_path", lambda name, path=None: None)
    monkeypatch.setattr(sys, "argv", ["ao"])

    assert A.ao_program() == (sys.executable, ["-m", "ao"])
    assert cli.cmd_a2a_mcp(project, SimpleNamespace(action="config")) == 0
    out = capsys.readouterr().out
    server = json.loads(out[out.index("{"):out.index("\n}") + 2])["mcpServers"]["a2a"]
    assert server["command"] == sys.executable and server["args"][:2] == ["-m", "ao"]

    root = str(tmp_path)
    assert skillkit.register_mcp(root, {"claude-code"})["claude-code"] == "registered"
    with open(os.path.join(root, ".mcp.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["mcpServers"]["ao"]
    assert entry["command"] == sys.executable and entry["args"][:4] == ["-m", "ao", "-C", root]



@pytest.mark.skipif(os.name == "nt", reason="POSIX's last resort")
def test_with_no_git_anywhere_posix_names_where_git_installs_never_the_bare_name(monkeypatch, tmp_path):
    """From the review of WINDOWS-NO-CWD-EXEC: on POSIX the last resort was the bare `git`, which execvp looks up
    through every PATH entry, an empty one - the current directory - among them."""
    monkeypatch.delenv("AO_GIT", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(A, "search_directories", lambda path=None: [])
    monkeypatch.setattr(A, "program_path", lambda name, path=None: None)
    monkeypatch.setattr(A.os.path, "isfile", lambda path: False)

    assert A._find_git_binary() == "/usr/bin/git"
