"""`ao remove` takes one project's things and nothing else; ao's jobs and hooks name programs that exist (SAFE-REMOVE).

A product review found four defects. `ao remove --yes` deleted every file in ~/.ao whose name held the
project's key: removing `proj` took the machine registry and its lock with the logs and push windows of
`bigproject` and `myproj`, and a project called `email` would take the e-mail channel's settings. It
removed its jobs through `python -m ao watchdog uninstall`, which from a clone has no ao module, read no
result, never removed the telegram poller and said everything was gone. A commit hook looked ao up with
`command -v ao` under /bin/sh, which cannot see the alias the README set up, so every commit failed with
"ao not found". And `ao watchdog install` scheduled a clone's script from a package that has none.
"""
import importlib.util
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage, watchdog as W
from tests.test_docs_names import _code_paths

SRC = str(Path(__file__).resolve().parent.parent / "src" / "ao")
REMOVE = SimpleNamespace(yes=True, allow_shared_hooks=False)
LAUNCHD_ONLY = "the jobs here are launchd's; Windows tasks have their own test"


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _home(tmp_path, monkeypatch):
    """A scratch home whose ~/.ao also holds the machine registry, as it does outside the tests."""
    home = tmp_path / "home"
    (home / ".ao").mkdir(parents=True)
    monkeypatch.setattr(A, "HOME", str(home))
    monkeypatch.setattr(W, "STATE_DIR", str(home / ".ao"))
    monkeypatch.setenv("AO_PROJECT_REGISTRY", str(home / ".ao" / "projects.json"))
    return home


def _project(parent, name):
    """An ao project that never adopted the marker: its removal goes straight to the second phase."""
    root = parent / name
    (root / ".ao" / "ledger").mkdir(parents=True)
    (root / "agent-mail").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=root, check=True)
    (root / ".ao" / "config.json").write_text(json.dumps({"project": name}), encoding="utf-8")
    return str(root)


def _launchd(monkeypatch, stuck=()):
    """A launchd asked through _run_program: it lists what is loaded, and a stuck label will not boot out."""
    loaded, asked = set(), []

    def run(argv, cwd=None, timeout=20, stderr=subprocess.DEVNULL):
        asked.append(list(argv))
        assert argv[0] == "launchctl", argv
        if argv[1] == "list":
            return "\n".join(["PID\tStatus\tLabel"] + [f"-\t0\t{label}" for label in sorted(loaded)]), 0
        if argv[1] == "bootout":
            label = argv[2].split("/", 2)[2]
            if label in stuck:
                return "Boot-out failed: 1: Operation not permitted", 1
            if label not in loaded:
                return "Boot-out failed: 3: No such process", 3
            loaded.discard(label)
        elif argv[1] == "bootstrap":
            loaded.add(os.path.basename(argv[3])[:-len(".plist")])
        return "", 0

    monkeypatch.setattr(A, "_run_program", run)
    monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
    return loaded, asked


def _jobs(home, loaded, key, jobs=cli.LAUNCHD_JOBS):
    """What `ao watchdog install` and `ao telegram install` leave: loaded labels, and their plists."""
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    labels = [cli._launchd_label(job, key) for job in jobs]
    for label in labels:
        (agents / f"{label}.plist").write_text("<plist/>\n", encoding="utf-8")
        loaded.add(label)
    return labels


def _executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


# ---- the files a project keeps in ~/.ao --------------------------------------------------------

def test_the_remove_table_holds_every_name_a_writer_gives_a_projects_file_in_home():
    # Read from the code as tests/test_docs_names.py reads it: every name built directly under ~/.ao from a
    # piece the code computes. A writer that named a project's file itself would show here, outside the table.
    built = {pattern[len("~/.ao/"):] for pattern in _code_paths(SRC)
             if pattern.startswith("~/.ao/") and pattern.count("/") == 2 and "*" in pattern and pattern != "~/.ao/*"}
    table = {template.replace("{key}", "*") for template in A.PROJECT_FILES.values()}

    assert {"heartbeat-*", "push-*.ok", "watchdog-*.log", "nudge-*.log", "escalate-*.log", "doctor-*.json",
            "helpers-*.json"} <= built
    assert sorted(built - table) == []
    # A launchd label is lower-cased, and a job's log is named from it.
    assert set(A.project_file_names("Api")) == {template.format(key=key) for template in A.PROJECT_FILES.values()
                                                 for key in ("Api", "api")}


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_remove_takes_exactly_this_projects_files_and_jobs_and_leaves_every_other(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    state = home / ".ao"
    loaded, asked = _launchd(monkeypatch)
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        assert not (isinstance(argv, (list, tuple)) and "uninstall" in argv), f"removed out of process: {argv}"
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(A, "_process_start", lambda pid, refresh=False: "started")
    # Every name here holds `email`: the removed project's, two neighbours' and a channel's settings.
    root = _project(tmp_path / "work", "email")
    others = [_project(tmp_path / "work", "bigemail"), _project(tmp_path / "work", "email-api")]
    keys = {path: A.project_key(path) for path in [root] + others}
    for path, key in keys.items():
        A.heartbeat(path)
        cli.cmd_push({"root": path}, SimpleNamespace(action="allow", minutes=5))
        W.save_state(path, {"attempts": 1})
        W.record_cycle(path, SimpleNamespace(dry_run=False), time.time())
        A.helper_register(path, os.getpid(), "reviewer")
        A.acquire_architect(path, os.getpid(), "test")
        A.set_reviewer_state(path, verdict="APPROVED")
        cli._watchdog_first_cycle_due(path)
        for log in ("watchdog-log", "doctor-log", "telegram-log"):        # what the jobs' own output goes to
            (state / A.project_file_name(log, key.lower())).write_text("ran\n", encoding="utf-8")
    machine = {"email.json", "telegram.json", "settings.json", "alarms.json", "pings.json", "quota.json"}
    for name in machine:
        (state / name).write_text("{}\n", encoding="utf-8")
    labels = _jobs(home, loaded, keys[root])
    neighbour = _jobs(home, loaded, keys[others[1]], ("watchdog",))       # its label begins with this one's
    before = set(os.listdir(state))
    cfg = {"root": root, "reviews": "semantic-review"}

    assert cli.cmd_remove(cfg, SimpleNamespace(yes=False, allow_shared_hooks=False)) == 0
    listed = _plain(capsys).splitlines()
    assert cli.cmd_remove(cfg, REMOVE) == 0
    out = _plain(capsys)

    after = set(os.listdir(state))
    gone = before - after
    assert gone == set(A.project_file_names(keys[root])) & before and len(gone) >= 10
    assert after == before - gone
    assert machine | {"projects.json", "projects.json.lock"} <= after
    assert {f"   ~/.ao/{name}" for name in gone} == {line for line in listed if line.startswith("   ~/.ao/")
                                                        and ": the `" not in line}
    assert {f"   launchd job {label}" for label in labels} == {line for line in listed if "launchd job" in line}
    assert loaded == set(neighbour)
    assert all(["launchctl", "bootout", f"gui/501/{label}"] in asked for label in labels)
    assert not any((home / "Library" / "LaunchAgents" / f"{label}.plist").exists() for label in labels)
    assert set(json.load(open(state / "projects.json", encoding="utf-8"))) == {keys[path] for path in others}
    assert f"removed the `{keys[root]}` entry from ~/.ao/projects.json" in out and "phase 2/2 complete" in out


def test_the_registry_loses_only_this_projects_row_under_its_lock_and_stays(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    first, second = _project(tmp_path / "work", "api"), _project(tmp_path / "personal", "api")
    one, two = A.project_key(first), A.project_key(second)
    registry = A.project_registry_path()
    rows = json.load(open(registry, encoding="utf-8"))
    rows["unread"] = "a row ao does not read is written back as it was"
    with open(registry, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    taken, real_lock = [], storage._exclusive_lock

    def lock(path, timeout=10.0):
        taken.append(path)
        return real_lock(path, timeout)

    monkeypatch.setattr(storage, "_exclusive_lock", lock)

    assert A.forget_project(first) == [one]

    assert taken == [registry + ".lock"]
    kept = json.load(open(registry, encoding="utf-8"))
    assert set(kept) == {two, "unread"} and kept["unread"] == rows["unread"] and kept[two] == rows[two]
    assert A.forget_project(first) == [] and os.path.exists(registry)


# ---- the scheduled jobs ------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_a_job_left_installed_is_named_and_the_removal_stops_with_state_intact(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    root = _project(tmp_path, "svc")
    key = A.project_key(root)
    stuck = cli._launchd_label("telegram", key)
    loaded, _ = _launchd(monkeypatch, stuck={stuck})
    watchdog, doctor, _ = _jobs(home, loaded, key)
    A.heartbeat(root)
    monkeypatch.setattr(cli, "JOB_GONE_SECONDS", 0)

    assert cli.cmd_remove({"root": root, "reviews": "semantic-review"}, REMOVE) == 1

    out = _plain(capsys)
    assert f"removed launchd job {watchdog}" in out and f"removed launchd job {doctor}" in out
    assert f"left launchd job {stuck}: still loaded" in out and "AO state kept intact" in out
    assert "phase 2/2 complete" not in out
    assert loaded == {stuck}
    assert os.path.isdir(os.path.join(root, ".ao")) and os.path.exists(A.heartbeat_path(root))
    assert A.registered_key(root) == key


def test_windows_tasks_are_deleted_in_this_process_and_a_task_left_is_named(monkeypatch):
    tasks, asked = {"ao-watchdog-svc", "ao-doctor-svc"}, []

    def schtasks(*args):
        asked.append(args)
        name = args[args.index("/TN") + 1]
        if args[0] == "/Query":
            return ("Ready", 0) if name in tasks else ("ERROR: The system cannot find the file specified.", 1)
        if name == "ao-doctor-svc":
            return "ERROR: Access is denied.", 1
        tasks.discard(name)
        return "SUCCESS: The scheduled task was successfully deleted.", 0

    monkeypatch.setattr(cli, "_schtasks", schtasks)
    monkeypatch.setattr(cli.os, "name", "nt")

    assert cli._installed_jobs("Svc") == [("scheduled task", "ao-watchdog-svc"), ("scheduled task", "ao-doctor-svc")]
    assert cli._unschedule("ao-watchdog-svc") is None and tasks == {"ao-doctor-svc"}
    assert "Access is denied" in cli._unschedule("ao-doctor-svc")
    assert ("/Delete", "/TN", "ao-watchdog-svc", "/F") in asked


# ---- the hook's ao -----------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="the hook runs under a POSIX shell here, and Windows names no fallback")
def test_the_hook_runs_the_ao_that_installed_it_only_when_path_has_none_and_that_file_is_there(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / cli.PROJECT_MARKER).write_bytes(cli.PROJECT_MARKER_BYTES)
    subprocess.run(["git", "add", cli.PROJECT_MARKER], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "adopt ao"], cwd=root,
                   check=True)
    installed = _executable(tmp_path / "clone" / "bin" / "ao", '#!/bin/sh\necho "installed ao $*"\nexit 7\n')
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_bytes(cli._render_local_hook("pre-commit", ".", str(installed)))
    bindir = tmp_path / "path"
    bindir.mkdir()
    env = {"HOME": str(tmp_path), "PATH": os.pathsep.join([str(bindir), os.path.dirname(A.git_binary()), "/usr/bin",
                                                           "/bin"])}
    if shutil.which("ao", path=env["PATH"]):
        pytest.skip("an ao is installed beside git or in /usr/bin")
    here = os.path.realpath(root)

    def commit():
        return subprocess.run(["/bin/sh", str(hook)], cwd=root, env=env, capture_output=True, text=True, timeout=60)

    ran = commit()
    assert (ran.returncode, ran.stdout.strip()) == (7, f"installed ao -C {here} commit-check"), ran.stderr

    _executable(bindir / "ao", '#!/bin/sh\necho "path ao $*"\n')
    ran = commit()
    assert (ran.returncode, ran.stdout.strip()) == (0, f"path ao -C {here} commit-check"), ran.stderr

    os.remove(bindir / "ao")
    os.remove(installed)
    ran = commit()
    assert ran.returncode == 1 and ran.stdout == "" and "agent-orchestrator: ao not found" in ran.stderr
    assert cli._hook_portable(hook.read_bytes()) == cli._render_local_hook("pre-commit", ".")


def test_install_names_the_installing_ao_in_the_git_directory_and_never_in_the_tree(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(cli, "_hook_fallback", lambda: "/opt/ao tool/bin/ao")
    install = SimpleNamespace(action="install", allow_shared_hooks=False)

    assert cli.cmd_hooks(project, install) == 0

    hooks = os.path.join(root, ".git", "hooks")
    assert open(os.path.join(hooks, "pre-commit"), "rb").read() == \
        cli._render_local_hook("pre-commit", ".", "/opt/ao tool/bin/ao")
    active = {t["role"]: t for t in cli._ao_hook_inventory(root)["targets"] if t["active"]}
    assert active["pre-commit"]["static_state"] == "current-local (behavior unverified)"
    assert active["pre-push"]["static_state"] == "current-local (behavior unverified)"

    for role in ("pre-commit", "pre-push"):
        os.remove(os.path.join(hooks, role))
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=root, check=True)
    assert cli.cmd_hooks(project, install) == 0
    assert open(os.path.join(root, ".githooks", "pre-commit"), "rb").read() == cli._render_local_hook("pre-commit", ".")


def test_status_doctor_and_prove_say_plainly_that_bin_sh_finds_no_ao_and_how_to_fix_it(project, monkeypatch, capsys):
    root = project["root"]
    with open(os.path.join(root, ".git", "hooks", "pre-commit"), "wb") as fh:
        fh.write(cli._render_local_hook("pre-commit", "."))
    detail = f"the hook exited 1 without AO's nonce-bound refusal proof: {cli.HOOK_AO_NOT_FOUND}"
    monkeypatch.setattr(cli, "_hook_execution_probe",
                        lambda inv: {"installed": False, "state": "not installed", "detail": detail, "exit": 1})
    monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)
    fix = cli._ao_link_fix()

    assert cli.cmd_hooks(project, SimpleNamespace(action="status", allow_shared_hooks=False)) == 0
    status = _plain(capsys)
    assert f'ao for hooks: {cli.HOOK_AO_NOT_FOUND} (a shell alias is invisible to it); a commit fails with ' \
           f'"ao not found"' in status
    assert f"  fix: {fix}" in status
    assert dict(cli.doctor_problems(project))["commit-hook"].endswith(f" — {fix}")
    assert cli.cmd_prove(project, SimpleNamespace(no_review=True)) == 1
    assert f"{detail} — {fix}" in _plain(capsys)
    if os.name != "nt":
        assert "ln -s " in fix and fix.endswith("~/.local/bin/ao and put ~/.local/bin on PATH")


# ---- the program a scheduled job starts --------------------------------------------------------

def test_a_scheduled_job_names_a_program_that_exists_or_nothing(tmp_path, monkeypatch):
    console = {name: str(_executable(tmp_path / "console" / name, "#!/bin/sh\n")) for name in ("ao", "ao-watchdog")}
    on_path = dict(console)
    monkeypatch.setattr(cli.shutil, "which", lambda name, *args, **kwargs: on_path.get(name))
    watchdog = ("ao-watchdog", ("scripts", "ao-watchdog"), "ao.watchdog")

    # A console script on PATH carries its own interpreter.
    assert cli._scheduled_argv(*watchdog) == [console["ao-watchdog"]]
    # A clone's script is started by this interpreter.
    on_path.clear()
    assert cli._scheduled_argv(*watchdog) == [sys.executable, os.path.join(A.REPO, "scripts", "ao-watchdog")]
    # An installed package has no clone beside it: this interpreter runs the module it imports.
    monkeypatch.setattr(A, "REPO", str(tmp_path / "lib"))
    monkeypatch.setattr(cli, "_package_installed", lambda: True)
    assert cli._scheduled_argv(*watchdog) == [sys.executable, "-m", "ao.watchdog"]
    assert cli._scheduled_argv("ao", ("bin", "ao"), "ao") == [sys.executable, "-m", "ao"]
    assert importlib.util.find_spec("ao.watchdog") is not None
    # Nothing a scheduler could start.
    monkeypatch.setattr(cli, "_package_installed", lambda: False)
    assert cli._scheduled_argv(*watchdog) is None


def test_a_source_tree_is_not_an_installed_package():
    assert cli._package_installed() is False


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_install_refuses_a_missing_program_and_schedules_an_installed_packages_modules(project, tmp_path, monkeypatch,
                                                                                         capsys):
    loaded, asked = _launchd(monkeypatch)
    monkeypatch.setattr(cli.shutil, "which", lambda name, *args, **kwargs: None)
    monkeypatch.setattr(A, "REPO", str(tmp_path / "lib"))
    monkeypatch.setattr(cli, "_package_installed", lambda: False)
    install = SimpleNamespace(action="install", idle_minutes=6, interval=120)
    agents = Path(A.HOME) / "Library" / "LaunchAgents"
    root, key = project["root"], A.project_key(project["root"])

    assert cli.cmd_watchdog(project, install) == 1
    assert "not installed" in _plain(capsys)
    assert not agents.exists() and not any(argv[1] == "bootstrap" for argv in asked)

    monkeypatch.setattr(cli, "_package_installed", lambda: True)
    assert cli.cmd_watchdog(project, install) in (None, 0)

    with open(agents / f"{cli._launchd_label('watchdog', key)}.plist", "rb") as fh:
        assert plistlib.load(fh)["ProgramArguments"] == [sys.executable, "-m", "ao.watchdog", "--root", root,
                                                          "--idle-minutes", "6"]
    with open(agents / f"{cli._launchd_label('doctor', key)}.plist", "rb") as fh:
        assert plistlib.load(fh)["ProgramArguments"] == [sys.executable, "-m", "ao", "-C", root, "doctor", "--check",
                                                          "--notify"]
    assert loaded == {cli._launchd_label("watchdog", key), cli._launchd_label("doctor", key)}


def test_windows_install_refuses_a_missing_program_and_fails_when_schtasks_does(project, tmp_path, monkeypatch, capsys):
    root, key = project["root"], A.project_key(project["root"])        # registered before Windows is emulated
    created, status = [], {"code": 1}

    def schtasks(*args):
        created.append(args)
        return ("SUCCESS: created", 0) if status["code"] == 0 else ("ERROR: Access is denied.", 1)

    monkeypatch.setattr(cli, "_schtasks", schtasks)
    monkeypatch.setattr(cli.shutil, "which", lambda name, *args, **kwargs: None)
    monkeypatch.setattr(A, "REPO", str(tmp_path / "lib"))
    monkeypatch.setattr(cli, "_package_installed", lambda: False)
    monkeypatch.setattr(cli.os, "name", "nt")
    install = SimpleNamespace(action="install", idle_minutes=6, interval=120)

    assert cli.cmd_watchdog(project, install) == 1 and created == []

    monkeypatch.setattr(cli, "_package_installed", lambda: True)
    assert cli.cmd_watchdog(project, install) == 1
    assert f"FAILED ao-watchdog-{key.lower()} (every 2m): ERROR: Access is denied." in _plain(capsys)

    status["code"], created[:] = 0, []
    assert cli.cmd_watchdog(project, install) == 0
    command = next(args[args.index("/TR") + 1] for args in created if f"ao-watchdog-{key.lower()}" in args)
    assert command == subprocess.list2cmdline([sys.executable, "-m", "ao.watchdog", "--root", root, "--idle-minutes",
                                               "6"])
