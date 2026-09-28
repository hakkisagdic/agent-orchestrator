"""`ao update` and `ao uninstall`: a person never has to learn how ao was installed (UPDATE-UNINSTALL).

Update tells the installation running it apart - a git clone, Homebrew, pipx, uv or pip - says what it will run,
and runs it only when told to; a clone with uncommitted changes is refused before anything runs. Uninstall takes
off the machine what ao put on it - every scheduled job in its namespace, ao's own hooks in the projects the
registry knows, and the `ao` entry in their MCP files - and names what it leaves. Everything here runs against a
temporary home, temporary projects, a stubbed launchd and stub programs that record how they were started;
nothing reaches the machine the tests run on.
"""
import io
import json
import os
import re
import shutil
import site
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A
from tests.conftest import GIT
from tests.test_safe_remove import LAUNCHD_ONLY, _executable, _home, _jobs, _launchd, _plain, _project

POSIX_STUBS = "the stub programs are /bin/sh scripts"
UPDATE, DRY_RUN = SimpleNamespace(yes=True, dry_run=False), SimpleNamespace(yes=False, dry_run=True)
ASK = SimpleNamespace(yes=False, dry_run=False)


def _git(root, *args):
    return subprocess.run([GIT, "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(root, message):
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)


def _recorder(path, log, code=0):
    """A program that writes the words it was started with to `log`, one a line, and exits with `code`."""
    return _executable(path, f"#!/bin/sh\nprintf '%s\\n' \"$0\" \"$@\" > '{log}'\nexit {code}\n")


def _started(log):
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else None


# ---- ao update: which installation ---------------------------------------------------------------------------

@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A clone of ao as the quickstart makes one - bin/ao and src/ao - one commit behind what it tracks.

    git runs with a scratch home, so the pull reads none of the configuration of the machine the tests run on.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "git-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "git-home" / ".config"))
    upstream = tmp_path / "upstream"
    _executable(upstream / "bin" / "ao", "#!/bin/sh\n")
    (upstream / "src" / "ao").mkdir(parents=True)
    (upstream / "src" / "ao" / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
    _git(tmp_path, "init", "-q", str(upstream))
    _commit(upstream, "first")
    _git(tmp_path, "clone", "-q", str(upstream), str(tmp_path / "ao"))
    (upstream / "src" / "ao" / "__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    _commit(upstream, "second")
    return SimpleNamespace(root=tmp_path / "ao", upstream=upstream)


INSTALLATION = cli._installation            # the real one, however often a test stands another in for it


def _running_from(monkeypatch, **where):
    """`ao update` asks about the ao these paths name, not about the one running the tests."""
    found = INSTALLATION(**where)
    monkeypatch.setattr(cli, "_installation", lambda: found)
    return found


def test_a_clone_is_told_by_its_bin_ao_and_git_and_updated_by_a_fast_forward(clone, monkeypatch, capsys):
    found = _running_from(monkeypatch, package=str(clone.root / "src" / "ao"))
    here = os.path.realpath(clone.root)

    assert (found["kind"], found["where"], found["remove"]) == ("clone", here, None)
    assert found["update"] == [A.git_binary(), "-C", here, "pull", "--ff-only"]

    assert cli.cmd_update({}, DRY_RUN) == 0
    shown = _plain(capsys)
    assert f"installed from a git clone at {here}" in shown and "tracking origin/" in shown
    assert f"will run: {cli._argv_text(found['update'])}" in shown and "nothing was run" in shown
    assert _git(clone.root, "rev-parse", "HEAD") != _git(clone.upstream, "rev-parse", "HEAD")

    assert cli.cmd_update({}, UPDATE) == 0
    assert "updated; `ao --version` names the version now installed" in _plain(capsys)
    assert _git(clone.root, "rev-parse", "HEAD") == _git(clone.upstream, "rev-parse", "HEAD")


def test_a_clone_with_uncommitted_changes_is_refused_before_anything_runs(clone, monkeypatch, capsys):
    _running_from(monkeypatch, package=str(clone.root / "src" / "ao"))
    (clone.root / "bin" / "ao").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    before = _git(clone.root, "rev-parse", "HEAD")

    assert cli.cmd_update({}, UPDATE) == 1
    out = _plain(capsys)
    assert "not updated" in out and "has uncommitted changes (bin/ao)" in out and "will run" not in out
    assert _git(clone.root, "rev-parse", "HEAD") == before

    # A file git does not track is not a change a fast-forward could lose.
    _git(clone.root, "checkout", "-q", "--", "bin/ao")
    (clone.root / "notes.txt").write_text("mine\n", encoding="utf-8")
    assert cli.cmd_update({}, DRY_RUN) == 0


def test_a_clone_on_no_branch_or_tracking_nothing_is_refused(clone, monkeypatch, capsys):
    _running_from(monkeypatch, package=str(clone.root / "src" / "ao"))
    branch = _git(clone.root, "symbolic-ref", "--short", "HEAD")

    _git(clone.root, "checkout", "-q", "--detach")
    assert cli.cmd_update({}, DRY_RUN) == 1
    assert "is on no branch (a detached HEAD)" in _plain(capsys)

    _git(clone.root, "checkout", "-q", branch)
    _git(clone.root, "branch", "--unset-upstream")
    assert cli.cmd_update({}, DRY_RUN) == 1
    assert f"branch {branch} of the clone at {os.path.realpath(clone.root)} tracks no upstream" in _plain(capsys)


def _environment(parent, *path):
    """A virtualenv-like directory holding site-packages/ao: (the environment, the package)."""
    env = parent.joinpath(*path)
    package = env / "lib" / "python3.12" / "site-packages" / "ao"
    package.mkdir(parents=True)
    return env, package


@pytest.mark.skipif(os.name == "nt", reason=POSIX_STUBS)
@pytest.mark.parametrize("kind", ["homebrew", "pipx", "uv", "pip", "pip --user"])
def test_each_package_install_is_told_apart_and_updated_by_its_own_tool(kind, tmp_path, monkeypatch, capsys):
    bindir, log = tmp_path / "bin", tmp_path / "started.txt"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    python = str(_recorder(tmp_path / "python", log))
    monkeypatch.setattr(site, "getsitepackages", lambda: [])
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(tmp_path / "user-site"))
    if kind == "homebrew":
        keg = tmp_path / "homebrew" / "Cellar" / "agent-orchestrator" / "0.4.0"
        env, package = _environment(keg, "libexec")
        (keg / "INSTALL_RECEIPT.json").write_text("{}", encoding="utf-8")
        tool = str(_recorder(tmp_path / "homebrew" / "bin" / "brew", log))      # beside the Cellar, not on PATH
        update, remove = [tool, "upgrade", "agent-orchestrator"], [tool, "uninstall", "agent-orchestrator"]
        where = keg.parent
    elif kind in ("pipx", "uv"):
        env, package = _environment(tmp_path, kind, "ao-orchestrator")
        (env / ("pipx_metadata.json" if kind == "pipx" else "uv-receipt.toml")).write_text("{}", encoding="utf-8")
        tool = str(_recorder(bindir / kind, log))
        verb = [] if kind == "pipx" else ["tool"]
        update, remove = [tool, *verb, "upgrade", "ao-orchestrator"], [tool, *verb, "uninstall", "ao-orchestrator"]
        where = env
    else:
        env, package = _environment(tmp_path, "venv")
        where = package.parent
        user = ["--user"] if kind == "pip --user" else []
        monkeypatch.setattr(site, "getusersitepackages" if user else "getsitepackages",
                            (lambda: str(where)) if user else (lambda: [str(where)]))
        update = [python, "-m", "pip", "install", "--upgrade", *user, "ao-orchestrator"]
        remove = [python, "-m", "pip", "uninstall", "ao-orchestrator"]

    found = _running_from(monkeypatch, package=str(package), prefix=str(env), python=python)

    assert (found["kind"], found["update"], found["remove"], found["missing"]) == \
        (kind.split()[0], update, remove, None)
    assert found["where"] == os.path.realpath(where)
    assert cli.cmd_update({}, DRY_RUN) == 0 and _started(log) is None
    assert cli.cmd_update({}, UPDATE) == 0
    assert _started(log) == update
    assert f"will run: {cli._argv_text(update)}" in _plain(capsys)


@pytest.mark.skipif(os.name == "nt", reason=POSIX_STUBS)
def test_update_asks_first_and_runs_nothing_without_a_yes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    log = tmp_path / "started.txt"
    _recorder(tmp_path / "bin" / "pipx", log)
    env, package = _environment(tmp_path, "pipx", "ao-orchestrator")
    (env / "pipx_metadata.json").write_text("{}", encoding="utf-8")
    _running_from(monkeypatch, package=str(package), prefix=str(env))
    answers = []
    monkeypatch.setattr(cli, "_confirm", lambda question: answers.pop(0))

    answers.append(False)
    assert cli.cmd_update({}, ASK) == 1 and _started(log) is None
    assert _plain(capsys).rstrip().endswith("not run")

    answers.append(None)
    assert cli.cmd_update({}, ASK) == 1 and _started(log) is None
    assert "standard input is not a terminal; run it again with --yes" in _plain(capsys)

    answers.append(True)
    assert cli.cmd_update({}, ASK) == 0 and _started(log)[1:] == ["upgrade", "ao-orchestrator"]


def test_nobody_at_a_terminal_is_never_read_as_a_yes(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))
    assert cli._confirm("run it? ") is None

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Terminal())
    for typed, meant in (("y", True), (" YES ", True), ("", False), ("n", False), ("sure", False)):
        monkeypatch.setattr("builtins.input", lambda question, typed=typed: typed)
        assert cli._confirm("run it? ") is meant, typed

    def closed(question):
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    assert cli._confirm("run it? ") is False


@pytest.mark.skipif(os.name == "nt", reason=POSIX_STUBS)
def test_a_failing_update_and_a_missing_tool_exit_1_and_say_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    env, package = _environment(tmp_path, "uv", "ao-orchestrator")
    (env / "uv-receipt.toml").write_text("", encoding="utf-8")

    found = _running_from(monkeypatch, package=str(package), prefix=str(env))
    assert found["missing"] == "uv" and found["update"] == ["uv", "tool", "upgrade", "ao-orchestrator"]
    assert cli.cmd_update({}, UPDATE) == 1
    assert "not updated: uv updates this installation, and it is not on PATH" in _plain(capsys)

    _recorder(tmp_path / "bin" / "uv", tmp_path / "started.txt", code=3)
    _running_from(monkeypatch, package=str(package), prefix=str(env))
    assert cli.cmd_update({}, UPDATE) == 1
    assert "update failed: uv exited 3; what it said is above" in _plain(capsys)


def test_an_installation_ao_cannot_tell_is_refused_and_named(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(site, "getsitepackages", lambda: [])
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(tmp_path / "user-site"))
    package = tmp_path / "unpacked" / "ao"
    package.mkdir(parents=True)
    # A keg without Homebrew's receipt, a src/ tree without git: neither is what it looks like.
    lookalikes = [tmp_path / "Cellar" / "agent-orchestrator" / "0.4.0" / "libexec" / "ao",
                  tmp_path / "download" / "src" / "ao"]
    _executable(tmp_path / "download" / "bin" / "ao", "#!/bin/sh\n")
    for path in lookalikes:
        path.mkdir(parents=True)
        assert cli._installation(package=str(path), prefix=str(tmp_path / "elsewhere"))["kind"] is None

    _running_from(monkeypatch, package=str(package), prefix=str(tmp_path / "elsewhere"))
    assert cli.cmd_update({}, UPDATE) == 1
    out = _plain(capsys)
    assert f"running from {os.path.realpath(package)}" in out and "update it the way you installed it" in out


def test_update_is_asked_or_told_never_both():
    parser = cli.build_parser()
    assert parser.parse_args(["update"]).fn is cli.cmd_update
    with pytest.raises(SystemExit):
        parser.parse_args(["update", "--yes", "--dry-run"])


def test_the_distribution_updated_is_the_one_pyproject_names():
    text = (Path(cli.__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    assert re.search(r'^name = "([^"]+)"$', project, re.M).group(1) == cli.DISTRIBUTION


# ---- ao uninstall ----------------------------------------------------------------------------------------------

UNINSTALL = SimpleNamespace(yes=True, purge=False, allow_shared_hooks=False)
LOOK = SimpleNamespace(yes=False, purge=False, allow_shared_hooks=False)
PIPX = {"kind": "pipx", "where": "/opt/pipx/venvs/ao-orchestrator", "update": ["pipx", "upgrade", "ao-orchestrator"],
        "remove": ["pipx", "uninstall", "ao-orchestrator"], "env": None, "missing": None}


def _serving(root):
    """The MCP entry skillkit.register_mcp writes for a project."""
    return {"command": "/opt/ao/bin/ao", "args": ["-C", str(root), "mcp", "serve"]}


def _write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ao_hooks(root, roles=("pre-commit", "pre-push")):
    """ao's own hook bytes, as `ao hooks install` writes them into the repository's git directory."""
    hooks = Path(root) / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for role in roles:
        (hooks / role).write_bytes(cli._render_local_hook(role, "."))
    return hooks


def _tree(*roots):
    """Every file under `roots`, with its bytes: what a dry run must leave exactly as it was."""
    return {str(path): path.read_bytes() for root in roots for path in Path(root).rglob("*")
            if path.is_file() and ".git/objects" not in path.as_posix()}


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A scratch machine: a home, a stubbed launchd, and two projects the registry knows, ao on both."""
    home = _home(tmp_path, monkeypatch)
    loaded, asked = _launchd(monkeypatch)
    monkeypatch.setattr(cli, "_installation", lambda: dict(PIPX))
    api, web = _project(tmp_path / "work", "api"), _project(tmp_path / "work", "web")
    keys = {root: A.project_key(root) for root in (api, web)}
    labels = _jobs(home, loaded, keys[api])
    agents = home / "Library" / "LaunchAgents"
    # A job of a project the registry lost, one launchd holds with its plist gone, and one that is not ao's.
    (agents / "com.agentorchestrator.watchdog.old.plist").write_text("<plist/>\n", encoding="utf-8")
    loaded.update({"com.agentorchestrator.doctor.gone", "com.example.backup"})
    (agents / "com.example.backup.plist").write_text("<plist/>\n", encoding="utf-8")
    _ao_hooks(api)
    web_hooks = _ao_hooks(web, ("pre-commit",))
    (web_hooks / "pre-push").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    _write_json(Path(api) / ".mcp.json", {"mcpServers": {"ao": _serving(api), "other": {"command": "other"}}})
    _write_json(Path(web) / ".mcp.json", {"mcpServers": {"ao": _serving(web)}})
    _write_json(Path(web) / ".kiro" / "settings" / "mcp.json", {"mcpServers": {"ao": dict(_serving(web), env={})}})
    A.heartbeat(api)
    (home / ".ao" / "settings.json").write_text("{}\n", encoding="utf-8")
    return SimpleNamespace(home=home, loaded=loaded, asked=asked, api=api, web=web, keys=keys, agents=agents,
                           labels=labels + ["com.agentorchestrator.doctor.gone", "com.agentorchestrator.watchdog.old"])


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_the_dry_run_names_every_job_hook_and_entry_and_changes_nothing(machine, capsys):
    before = _tree(machine.home, machine.api, machine.web)
    loaded = set(machine.loaded)

    assert cli.cmd_uninstall({}, LOOK) == 0

    out = _plain(capsys)
    assert {f"   launchd job {label}" for label in machine.labels} == \
        {line for line in out.splitlines() if "launchd job" in line}
    for root, roles in ((machine.api, ("pre-commit", "pre-push")), (machine.web, ("pre-commit",))):
        for role in roles:
            assert f"      hook {role} — {Path(root) / '.git' / 'hooks' / role}" in out
    assert out.count("the `ao` server entry in .mcp.json (other entries stay)") == 2
    assert f"the `ao` server entry in {os.path.join('.kiro', 'settings', 'mcp.json')}" in out
    assert f"~/.ao/heartbeat-{machine.keys[machine.api]} (a heartbeat left behind reads as a dead watchdog)" in out
    assert "it leaves:" in out and "`--purge` deletes it" in out
    assert "the program itself: run `pipx uninstall ao-orchestrator`" in out
    assert "re-run with --yes to do it" in out
    assert _tree(machine.home, machine.api, machine.web) == before
    assert machine.loaded == loaded and not any(argv[1] == "bootout" for argv in machine.asked)


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_uninstall_takes_exactly_aos_jobs_hooks_and_entries_and_names_what_it_leaves(machine, capsys):
    web_pre_push = (Path(machine.web) / ".git" / "hooks" / "pre-push").read_bytes()

    assert cli.cmd_uninstall({}, UNINSTALL) == 0

    out = _plain(capsys)
    assert machine.loaded == {"com.example.backup"}
    assert sorted(os.listdir(machine.agents)) == ["com.example.backup.plist"]
    assert all(["launchctl", "bootout", f"gui/501/{label}"] in machine.asked
               for label in machine.labels if label != "com.agentorchestrator.watchdog.old")
    hooks = {root: Path(root) / ".git" / "hooks" for root in (machine.api, machine.web)}
    assert not any((hooks[machine.api] / role).exists() for role in ("pre-commit", "pre-push"))
    assert not (hooks[machine.web] / "pre-commit").exists()
    assert (hooks[machine.web] / "pre-push").read_bytes() == web_pre_push          # not ao's: it stays as it was
    assert _read_json(Path(machine.api) / ".mcp.json") == {"mcpServers": {"other": {"command": "other"}}}
    assert not (Path(machine.web) / ".mcp.json").exists()                    # it held only ao's entry
    assert _read_json(Path(machine.web) / ".kiro" / "settings" / "mcp.json") == {"mcpServers": {}}
    assert not os.path.exists(A.heartbeat_path(machine.api))
    # What it leaves: each project's own state, and ~/.ao with its registry and settings.
    assert all(os.path.isdir(os.path.join(root, ".ao")) for root in (machine.api, machine.web))
    assert {"projects.json", "settings.json"} <= set(os.listdir(machine.home / ".ao"))
    assert "uninstalled: ao's jobs, hooks and MCP entries are off this machine" in out
    assert "the program itself stays — run `pipx uninstall ao-orchestrator`" in out


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_purge_deletes_home_ao_only_when_nothing_was_left(machine, monkeypatch, capsys):
    stuck = machine.labels[0]
    real = A._run_program

    def launchd(argv, **kwargs):
        if argv[1] == "bootout" and argv[2].endswith("/" + stuck):
            return "Boot-out failed: 1: Operation not permitted", 1
        return real(argv, **kwargs)

    monkeypatch.setattr(A, "_run_program", launchd)
    monkeypatch.setattr(cli, "JOB_GONE_SECONDS", 0)
    purge = SimpleNamespace(yes=True, purge=True, allow_shared_hooks=False)

    assert cli.cmd_uninstall({}, purge) == 1
    out = _plain(capsys)
    assert f"left launchd job {stuck}: still loaded" in out and "kept ~/.ao" in out and "not complete" in out
    assert "kept 1 heartbeat(s): a job left behind still writes one" in out
    assert os.path.exists(A.heartbeat_path(machine.api)) and (machine.home / ".ao" / "projects.json").exists()

    monkeypatch.setattr(A, "_run_program", real)
    assert cli.cmd_uninstall({}, purge) == 0
    assert "removed ~/.ao" in _plain(capsys) and not (machine.home / ".ao").exists()


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_an_mcp_entry_that_is_not_aos_stays_and_an_unreadable_file_is_left_named(machine, capsys):
    kiro = Path(machine.api) / ".kiro" / "settings" / "mcp.json"
    theirs = {"mcpServers": {"ao": {"command": "/usr/local/bin/ops", "args": ["run"]}}}
    _write_json(kiro, theirs)
    (Path(machine.web) / ".mcp.json").write_text('{"mcpServers": {"ao": ', encoding="utf-8")

    assert cli.cmd_uninstall({}, UNINSTALL) == 1

    out = _plain(capsys)
    assert _read_json(kiro) == theirs
    assert f"left {Path(machine.web) / '.mcp.json'}: it cannot be read" in out and "not complete: 1 thing(s)" in out


def test_an_mcp_entry_that_names_a_role_is_aos_all_the_same():
    """`ao init` ends a registration only one role reads with `--role <role>` (MCP-ROLES); it is still ao's."""
    served = {"command": "/usr/bin/python3", "args": ["-m", "ao", "-C", "/p", "mcp", "serve", "--role", "implementer"]}

    assert cli._serves_ao(served) and cli._serves_ao(dict(served, args=served["args"][:-2]))
    assert not cli._serves_ao({"command": "/usr/local/bin/ops", "args": ["run", "--role", "implementer"]})


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_a_tracked_ao_hook_stays_and_the_uninstall_says_it_is_not_complete(machine, capsys):
    web = Path(machine.web)
    for role in ("pre-commit", "pre-push"):
        (web / ".git" / "hooks" / role).unlink()
    (web / ".githooks").mkdir()
    (web / ".githooks" / "pre-commit").write_bytes(cli._render_local_hook("pre-commit", "."))
    _commit(web, "the team's hooks")
    _git(web, "config", "core.hooksPath", ".githooks")

    assert cli.cmd_uninstall({}, LOOK) == 0
    assert f"stays: current-local (behavior unverified) pre-commit — {web / '.githooks' / 'pre-commit'} (tracked" \
        in _plain(capsys)

    assert cli.cmd_uninstall({}, UNINSTALL) == 1
    out = _plain(capsys)
    assert (web / ".githooks" / "pre-commit").exists()
    assert f"left hooks of {machine.web}, each named above" in out and "not complete: 1 thing(s)" in out


@pytest.mark.skipif(os.name == "nt", reason="a shared or external hook is refused on Windows, for install too")
def test_a_shared_hook_goes_only_with_allow_shared_hooks(project, tmp_path, monkeypatch, capsys):
    _launchd(monkeypatch)
    monkeypatch.setattr(cli, "_installation", lambda: dict(PIPX))
    root = project["root"]
    # A linked worktree shares the repository's hooks directory: a hook there is every worktree's.
    _git(root, "worktree", "add", "-q", "--detach", str(tmp_path / "proj-linked"))
    shared = Path(root) / ".git" / "hooks"
    assert cli.cmd_hooks(project, SimpleNamespace(action="install", allow_shared_hooks=True)) == 0
    A.project_key(root)

    assert cli.cmd_uninstall({}, LOOK) == 0
    assert f"hook pre-commit — {shared / 'pre-commit'}  needs --allow-shared-hooks" in _plain(capsys)
    assert cli.cmd_uninstall({}, UNINSTALL) == 1
    assert (shared / "pre-commit").exists() and "needs --allow-shared-hooks" in _plain(capsys)

    assert cli.cmd_uninstall({}, SimpleNamespace(yes=True, purge=False, allow_shared_hooks=True)) == 0
    assert not (shared / "pre-commit").exists() and not (shared / "pre-push").exists()


@pytest.mark.skipif(os.name == "nt", reason="a shared or external hook is refused on Windows, for install too")
def test_a_hook_outside_every_repository_stays_named_since_ao_cannot_prove_it_untracked(project, tmp_path,
                                                                                         monkeypatch, capsys):
    _launchd(monkeypatch)
    monkeypatch.setattr(cli, "_installation", lambda: dict(PIPX))
    root = project["root"]
    team = tmp_path / "team-hooks"
    _git(root, "config", "core.hooksPath", str(team))
    assert cli.cmd_hooks(project, SimpleNamespace(action="install", allow_shared_hooks=True)) == 0
    A.project_key(root)

    assert cli.cmd_uninstall({}, LOOK) == 0
    assert f"stays: current-scoped (behavior unverified) pre-commit — {team / 'pre-commit'} (indeterminate" \
        in _plain(capsys)
    assert cli.cmd_uninstall({}, SimpleNamespace(yes=True, purge=False, allow_shared_hooks=True)) == 1
    assert (team / "pre-commit").exists() and f"left hooks of {root}, each named above" in _plain(capsys)


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_an_unreadable_registry_is_never_read_as_no_projects(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    _launchd(monkeypatch)
    monkeypatch.setattr(cli, "_installation", lambda: dict(PIPX))
    (home / ".ao" / "projects.json").write_text('{"api": ', encoding="utf-8")

    assert cli.cmd_uninstall({}, LOOK) == 0
    assert "~/.ao/projects.json cannot be read (" in _plain(capsys)
    assert cli.cmd_uninstall({}, SimpleNamespace(yes=True, purge=True, allow_shared_hooks=False)) == 1
    out = _plain(capsys)
    assert "left the hooks and MCP entries of every project: ~/.ao/projects.json cannot be read" in out
    assert "kept ~/.ao" in out and (home / ".ao" / "projects.json").exists()


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_a_project_gone_from_disk_is_named_and_leaves_nothing_behind(machine, capsys):
    shutil.rmtree(machine.web)

    assert cli.cmd_uninstall({}, UNINSTALL) == 0
    assert f"{machine.web}  gone from disk: nothing of it to take" in _plain(capsys)


def test_windows_jobs_are_the_tasks_of_the_projects_the_registry_knows(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    keys = [A.project_key(_project(tmp_path / "work", name)) for name in ("svc", "api")]
    tasks = {"ao-watchdog-svc", "ao-doctor-svc", "ao-doctor-api", "ao-watchdog-unknown"}
    monkeypatch.setattr(cli, "_schtasks", lambda *args: ("Ready", 0) if args[args.index("/TN") + 1] in tasks
                        else ("ERROR: The system cannot find the file specified.", 1))
    monkeypatch.setattr(cli.os, "name", "nt")

    assert sorted(keys) == ["api", "svc"]
    assert cli._machine_jobs() == [("scheduled task", "ao-doctor-api"), ("scheduled task", "ao-watchdog-svc"),
                                   ("scheduled task", "ao-doctor-svc")]


def test_the_launchd_namespace_is_the_one_every_ao_job_is_named_in():
    assert all(cli._launchd_label(job, "Api").startswith(cli.LAUNCHD_NAMESPACE) for job in cli.LAUNCHD_JOBS)
    assert not "com.agentorchestratorx.watchdog.api".startswith(cli.LAUNCHD_NAMESPACE)


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_what_it_leaves_names_the_dead_mans_switch_and_systemd_where_they_are(machine, monkeypatch, capsys):
    real_which = shutil.which
    monkeypatch.setattr(cli.shutil, "which", lambda name, *args, **kwargs: None if name == "systemctl"
                        else real_which(name, *args, **kwargs))
    assert cli.cmd_uninstall({}, LOOK) == 0
    out = _plain(capsys)
    assert "dead man's switch" not in out and "systemd" not in out

    _write_json(A.pings_path(), {"*": "https://ping.example/check"})
    monkeypatch.setattr(cli.shutil, "which", lambda name, *args, **kwargs: "/usr/bin/systemctl"
                        if name == "systemctl" else real_which(name, *args, **kwargs))
    assert cli.cmd_uninstall({}, SimpleNamespace(yes=False, purge=True, allow_shared_hooks=False)) == 0
    out = _plain(capsys)
    assert "the dead man's switch: 1 ping URL(s) stop being called with the jobs" in out
    assert "systemd: ao schedules no unit of its own" in out
    assert "~/.ao: every file ao keeps on this machine" in out and "`--purge` deletes it" not in out


@pytest.mark.skipif(os.name == "nt", reason="making a symbolic link takes a privilege on Windows")
def test_a_clone_is_removed_by_deleting_it_and_the_link_that_points_at_it(tmp_path, monkeypatch):
    clone = tmp_path / "ao"
    _executable(clone / "bin" / "ao", "#!/bin/sh\n")
    link = tmp_path / "home" / ".local" / "bin" / "ao"
    link.parent.mkdir(parents=True)
    link.symlink_to(clone / "bin" / "ao")
    monkeypatch.setattr(A, "HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli.shutil, "which", lambda name, *args, **kwargs: None)

    removal = cli._program_removal({"kind": "clone", "where": str(clone), "remove": None})

    assert removal == f"delete the clone {clone} and the link {link}"
    assert cli._program_removal({"kind": None, "where": "/x", "remove": None}) == "remove it the way it was installed"
    assert cli._program_removal(PIPX) == "run `pipx uninstall ao-orchestrator`"
