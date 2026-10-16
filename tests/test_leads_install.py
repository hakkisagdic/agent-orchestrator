"""The bug hunter's leads on install, removal, scheduling and the proof, each held (LEADS-INSTALL).

ao's read-only bug hunter read src/ao/parts on 2026-10-12. `ao remove` took a person's own .gitignore lines
whenever they read like ao's, ignored every error of deleting the project's state and said it was gone, and found a
project's systemd jobs by their files alone, missing a timer the manager still ran. A stop that left the watchdog's
service running counted as done, and so did every Task Scheduler query that failed, whatever its reason. An MCP
entry that served another project was taken as this one's, and the MCP file was rewritten where it resolved
seconds earlier, through whatever link had been swapped in since, over whatever another program had written
meanwhile; an emptied one was deleted with its directory unsynced. `ao adapters conform` started its fixture
through a first line Windows never runs, `ao prove` merged two runs of one second into one archive and left its
worktree registered when the archive could not be written, `ao doctor` hid the heartbeat, alarms and channels
whenever ao was not on an agent's PATH, and `ao lane start` lost its exit 130 to whatever reading the lane's
record raised. Each test here fails without its fix.

An independent review then found the fixes failing open in five places. The removal checked a path and deleted it
by path, so a link swapped in for one of its directories between the two sent the delete outside the project; a
user manager or a launchd that did not answer read as holding nothing, and so did a Task Scheduler that refused
every query alike; the MCP file was compared before its new bytes were written on Windows, which left the whole
write for a change to be lost in; and a comment in ao's words made the lines under it ao's, a person's own among
them. The delete now walks directories held open from the project down, a scheduler that cannot be asked keeps
the project's state, absence needs Task Scheduler to list its tasks, the comparison is the last step before the
replace everywhere, and init names the lines it adds by their count and digest.

Every scheduler is a fake, as in tests/test_safe_remove.py and tests/test_linux_scheduler.py: nothing here asks
the machine's launchd, systemd or Task Scheduler anything.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import storage, cli, lib as A
from tests.standin import program
from tests.test_linux_scheduler import _args as _watchdog_args
from tests.test_linux_scheduler import _systemd, _units
from tests.test_profiles import _git_init, _init_args
from tests.test_safe_remove import LAUNCHD_ONLY, REMOVE, _home, _launchd, _plain, _project

POSIX_SYSTEMD = "the fake systemctl is a /bin/sh program; Windows schedules the watchdog with Task Scheduler"
LINKS = "making a symbolic link takes a privilege on Windows"
NOT_FOUND = "ERROR: The system cannot find the file specified."


def _removable(tmp_path, monkeypatch, name="svc"):
    """A project past the first phase of `ao remove`, on a machine where none of its jobs is scheduled."""
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_installed_jobs", lambda key: [])
    root = _project(tmp_path, name)
    return root, {"root": root, "reviews": "semantic-review"}


def _write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _serving(root, *more):
    """The MCP entry skillkit.register_mcp writes for the project at `root`."""
    return {"command": "/opt/ao/bin/ao", "args": ["-C", str(root), "mcp", "serve", *more]}


# ---- e8e33d6d06b54193: the .gitignore lines ao added, and only those ------------------------------------------

def test_the_lines_init_wrote_go_and_the_same_lines_a_person_wrote_before_stay(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    _git_init(root)
    (root / ".gitignore").write_text("build/\n.ao/hold\nagent-mail/*.md\n", encoding="utf-8")
    args = _init_args(profile=None, agent="kiro", no_mcp=True)
    args.allow_uncovered_gates = True
    assert cli.cmd_init({"root": str(root)}, args) == 0
    written = (root / ".gitignore").read_text(encoding="utf-8")
    added = [line for line in cli.AO_GITIGNORE if line not in (".ao/hold", "agent-mail/*.md")]
    # Init adds no line already there, under a comment that names the lines it added.
    assert written.endswith("\n" + cli._gitignore_block(added) + "\n" + "\n".join(added) + "\n")

    found = cli._gitignore_without_ao(str(root))

    assert [line for line in found[1] if line] == ["build/", ".ao/hold", "agent-mail/*.md"]
    assert found[2] == 1 + len(added)                                  # the comment and the four lines init added


def _gitignore(root, text):
    # Preserve the fixture's CRLF: text mode on Windows adds a second CR (WINDOWS-TESTS-8).
    (Path(root) / ".gitignore").write_bytes(text.encode("utf-8"))
    return Path(root) / ".gitignore"


def test_a_persons_block_under_aos_comment_stays_named_and_inits_own_goes(tmp_path, monkeypatch, capsys):
    """The review's case: a .gitignore that held ao's comment over `.ao/hold` before init - copied from another
    project - lost both to the removal, with the block init added."""
    root, cfg = _removable(tmp_path, monkeypatch)
    added = [line for line in cli.AO_GITIGNORE if line != ".ao/hold"]
    theirs = f"build/\n{cli.AO_GITIGNORE_COMMENT}\n.ao/hold\n"
    gitignore = _gitignore(root, theirs + "\n" + cli._gitignore_block(added) + "\n" + "\n".join(added) + "\n")

    assert cli.cmd_remove(cfg, SimpleNamespace(yes=False, allow_shared_hooks=False)) == 0
    listed = _plain(capsys)
    assert cli.cmd_remove(cfg, REMOVE) == 0

    assert gitignore.read_text(encoding="utf-8") == theirs + "\n"
    assert "   .gitignore: the lines ao added" in listed and "stays .gitignore line 2 and the lines under it" in listed
    assert "kept .gitignore line 2 and the lines under it: ao cannot tell it wrote them" in _plain(capsys)


def test_a_block_ao_wrote_and_changed_since_or_an_older_aos_stays_named(tmp_path, monkeypatch, capsys):
    root, cfg = _removable(tmp_path, monkeypatch)
    added = [".ao/inbox/", ".ao/hold"]
    # A person took a line out of init's block, and an older ao's block names no lines.
    text = f"{cli._gitignore_block(added)}\n.ao/inbox/\n\n{cli.AO_GITIGNORE_COMMENT}\n.ao/lanes/\n"
    gitignore = _gitignore(root, text)

    assert cli.cmd_remove(cfg, REMOVE) == 0

    out = _plain(capsys)
    assert gitignore.read_text(encoding="utf-8") == text
    assert "kept .gitignore line 1 and the lines under it" in out and "kept .gitignore line 4" in out


def test_only_the_lines_init_named_go_and_a_line_written_right_under_them_stays(tmp_path, monkeypatch):
    root, cfg = _removable(tmp_path, monkeypatch)
    added = list(cli.AO_GITIGNORE) + [".mcp.json"]                     # init adds the MCP file it writes too
    text = "a/\r\n" + cli._gitignore_block(added) + "\r\n" + "\r\n".join(added) + "\r\nb/\r\n"
    gitignore = _gitignore(root, text)
    assert gitignore.read_bytes() == text.encode("utf-8")

    assert cli.cmd_remove(cfg, REMOVE) == 0

    assert gitignore.read_text(encoding="utf-8").splitlines() == ["a/", "b/"]     # one a Windows editor wrote, too


# ---- a198dc25e0d1ba0a: a service a stop did not end ----------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason=POSIX_SYSTEMD)
def test_a_service_still_running_after_its_stop_is_named_and_the_job_left(project, tmp_path, monkeypatch):
    fake = _systemd(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "JOB_GONE_SECONDS", 0)
    _, watchdog, _ = _units(A.project_key(project["root"]).lower())
    assert cli.cmd_watchdog(project, _watchdog_args()) == 0
    service = f"{watchdog}.service"
    fake.change(active=fake()["active"] + [service], stuck=[service])           # a cycle the stop does not end

    left = cli._unschedule(f"{watchdog}.timer")

    assert left == f"its service still active — systemctl --user stop {service}"
    assert f"{watchdog}.timer" not in fake()["active"] and ["--user", "stop", f"{watchdog}.timer", service] \
        in fake()["calls"]


@pytest.mark.skipif(os.name == "nt", reason=POSIX_SYSTEMD)
def test_remove_stops_with_state_intact_while_the_watchdogs_service_runs(project, tmp_path, monkeypatch, capsys):
    fake = _systemd(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "JOB_GONE_SECONDS", 0)
    root = project["root"]
    _, watchdog, _ = _units(A.project_key(root).lower())
    assert cli.cmd_watchdog(project, _watchdog_args()) == 0
    subprocess.run(["git", "rm", "-q", "--", cli.PROJECT_MARKER], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "ao goes"], cwd=root,
                   check=True)
    fake.change(active=fake()["active"] + [f"{watchdog}.service"], stuck=[f"{watchdog}.service"])

    assert cli.cmd_remove(project, REMOVE) == 1

    out = _plain(capsys)
    assert f"left systemd timer {watchdog}.timer: its service still active" in out and "AO state kept intact" in out
    assert os.path.isdir(os.path.join(root, ".ao"))


# ---- 8490828cee28085c: a Task Scheduler query that failed for another reason than absence ---------------------

def _task_scheduler(monkeypatch, answers, deletable=(), others=(NOT_FOUND, 1), listing=("Folder: \\", 0)):
    """Task Scheduler as schtasks answers it: `answers` maps a task to (what it prints, its exit) for /Query and for
    /Delete alike, any other name is answered `others` - one it does not have - and a /Query naming no task, which
    lists them all, is answered `listing`. A task in `deletable` is held until /Delete takes it."""
    asked, held = [], set(deletable)

    def schtasks(*args):
        asked.append(args)
        if "/TN" not in args:
            return listing
        name = args[args.index("/TN") + 1]
        if name in held:
            if args[0] == "/Delete":
                held.discard(name)
                return "SUCCESS: The scheduled task was successfully deleted.", 0
            return "Ready", 0
        return answers.get(name, others)

    monkeypatch.setattr(cli, "_schtasks", schtasks)
    monkeypatch.setattr(cli, "_scheduler", lambda: "schtasks")
    return asked


# What a Task Scheduler that refuses this user everything answers every query with, a listing included.
REFUSED = [("ERROR: Access is denied.", 1), ("ERROR: The Task Scheduler service is not available.", 1)]


@pytest.mark.parametrize("refused", REFUSED)
def test_a_failure_every_query_shares_proves_no_task_absent(monkeypatch, refused):
    """The review's case: the name no task has fails as the task's own name does when nothing can be asked at all."""
    _task_scheduler(monkeypatch, {}, others=refused, listing=refused)

    held = cli._task_held("ao-watchdog-svc")

    assert held == f"it lists no task: {refused[0]}"
    assert cli._installed_jobs("svc") == [("scheduled task", "ao-watchdog-svc"), ("scheduled task", "ao-doctor-svc")]


def test_remove_keeps_the_state_of_a_project_when_task_scheduler_refuses_every_query(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    root = _project(tmp_path, "svc")
    _task_scheduler(monkeypatch, {}, others=REFUSED[0], listing=REFUSED[0])

    assert cli.cmd_remove({"root": root, "reviews": "semantic-review"}, REMOVE) == 1

    out = _plain(capsys)
    assert ("left scheduled task ao-watchdog-svc: Task Scheduler cannot say whether it still holds it (it lists no "
            "task: ERROR: Access is denied.)") in out and "AO state kept intact" in out
    assert os.path.isdir(os.path.join(root, ".ao")) and A.registered_key(root) == A.project_key(root)


def test_a_task_scheduler_cannot_say_it_lacks_is_tried_named_and_left(monkeypatch):
    denied = ("ERROR: Access is denied.", 1)
    asked = _task_scheduler(monkeypatch, {"ao-watchdog-svc": denied})

    assert cli._installed_jobs("Svc") == [("scheduled task", "ao-watchdog-svc")]     # the doctor's is not there
    left = cli._unschedule("ao-watchdog-svc")

    assert left == ("Task Scheduler cannot say whether it still holds it (ERROR: Access is denied.) — delete it from "
                    "a prompt that can: schtasks /Delete /TN ao-watchdog-svc /F")
    assert ("/Delete", "/TN", "ao-watchdog-svc", "/F") in asked


def test_a_schtasks_that_cannot_be_run_proves_no_task_absent(monkeypatch):
    monkeypatch.setattr(cli, "_schtasks", lambda *args: ("", None))
    monkeypatch.setattr(cli, "_scheduler", lambda: "schtasks")

    assert cli._installed_jobs("svc") == [("scheduled task", "ao-watchdog-svc"), ("scheduled task", "ao-doctor-svc")]
    assert "schtasks could not be run" in cli._unschedule("ao-doctor-svc")


def test_an_absence_said_in_any_language_is_an_absence(monkeypatch):
    """schtasks says it in the machine's language: a failure that reads as its answer for a name no task has is one,
    where Task Scheduler lists its tasks."""
    turkish = ("HATA: Sistem belirtilen dosyayı bulamıyor.", 1)
    _task_scheduler(monkeypatch, {}, others=turkish, listing=("Klasör: \\", 0))

    assert cli._task_held("ao-watchdog-svc") is False and cli._installed_jobs("svc") == []


def test_remove_keeps_the_state_of_a_project_whose_task_cannot_be_read(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    root = _project(tmp_path, "svc")
    _task_scheduler(monkeypatch, {"ao-watchdog-svc": ("ERROR: Access is denied.", 1)})
    cfg = {"root": root, "reviews": "semantic-review"}

    assert cli.cmd_remove(cfg, SimpleNamespace(yes=False, allow_shared_hooks=False)) == 0
    assert "   scheduled task ao-watchdog-svc" in _plain(capsys)
    assert cli.cmd_remove(cfg, REMOVE) == 1

    out = _plain(capsys)
    assert "left scheduled task ao-watchdog-svc: Task Scheduler cannot say" in out and "AO state kept intact" in out
    assert os.path.isdir(os.path.join(root, ".ao")) and A.registered_key(root) == A.project_key(root)


def test_watchdog_uninstall_on_windows_says_which_task_it_left(project, monkeypatch, capsys):
    key = A.project_key(project["root"]).lower()
    denied = {f"ao-doctor-{key}": ("ERROR: Access is denied.", 1)}
    _task_scheduler(monkeypatch, denied, deletable={f"ao-watchdog-{key}"})

    assert cli.cmd_watchdog(project, SimpleNamespace(action="uninstall", idle_minutes=6, interval=120)) == 1

    out = _plain(capsys)
    assert f"removed ao-watchdog-{key}" in out and f"left ao-doctor-{key}: Task Scheduler cannot say" in out
    assert cli.cmd_watchdog(project, SimpleNamespace(action="status", idle_minutes=6, interval=120)) == 0
    assert f"ao-doctor-{key}: unknown — Task Scheduler cannot say (ERROR: Access is denied.)" in _plain(capsys)


# ---- 33947736bda0bc52: a timer the manager runs after its files are gone ------------------------------------

@pytest.mark.skipif(os.name == "nt", reason=POSIX_SYSTEMD)
def test_a_timer_running_without_its_files_is_found_and_taken_off(project, tmp_path, monkeypatch):
    fake = _systemd(tmp_path, monkeypatch)
    key = A.project_key(project["root"])
    units, watchdog, _ = _units(key.lower())
    fake.change(active=[f"{watchdog}.timer"])                    # loaded before its files were deleted by hand
    assert not units.exists()

    assert cli._installed_jobs(key) == [("systemd timer", f"{watchdog}.timer")]
    assert cli._unschedule(f"{watchdog}.timer") is None

    assert fake()["active"] == [] and cli._installed_jobs(key) == []


@pytest.mark.skipif(os.name == "nt", reason=POSIX_SYSTEMD)
def test_without_a_manager_to_ask_the_files_are_all_there_is_to_find(project, tmp_path, monkeypatch):
    fake = _systemd(tmp_path, monkeypatch, booted=False)
    _, watchdog, _ = _units(A.project_key(project["root"]).lower())
    fake.change(active=[f"{watchdog}.timer"])

    assert cli._installed_jobs(A.project_key(project["root"])) == [] and fake()["calls"] == []


@pytest.mark.skipif(os.name == "nt", reason=POSIX_SYSTEMD)
def test_uninstall_finds_a_registered_projects_timer_the_manager_runs_without_its_files(project, tmp_path,
                                                                                        monkeypatch):
    fake = _systemd(tmp_path, monkeypatch)
    _, watchdog, _ = _units(A.project_key(project["root"]).lower())
    fake.change(active=[f"{watchdog}.timer"])

    assert cli._machine_jobs() == [("systemd timer", f"{watchdog}.timer")]
    assert [cli._unschedule(name) for _, name in cli._machine_jobs()] == [None] and cli._machine_jobs() == []


# ---- the review of LEADS-INSTALL: a scheduler that cannot be asked holds what it may hold ----------------------

def _past_phase_one(root):
    subprocess.run(["git", "rm", "-q", "--", cli.PROJECT_MARKER], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "ao goes"], cwd=root,
                   check=True)


@pytest.mark.skipif(os.name == "nt", reason=POSIX_SYSTEMD)
def test_a_user_manager_that_cannot_be_asked_keeps_the_state_of_a_project_whose_timer_may_run(project, tmp_path,
                                                                                            monkeypatch, capsys):
    """The review's case: the files deleted by hand, a timer still loaded, and the manager out of this shell's
    reach - its silence read as no timer, and the removal took the state while the timer ran on."""
    fake = _systemd(tmp_path, monkeypatch, reachable=False)
    root, key = project["root"], A.project_key(project["root"])
    _, watchdog, doctor = _units(key.lower())
    fake.change(active=[f"{watchdog}.timer"])
    _past_phase_one(root)

    assert cli._installed_jobs(key) == [("systemd timer", f"{watchdog}.timer"), ("systemd timer", f"{doctor}.timer")]
    assert cli.cmd_remove(project, REMOVE) == 1

    out = _plain(capsys)
    assert (f"left systemd timer {watchdog}.timer: systemctl --user reaches no user manager: Failed to connect to "
            "bus: No medium found — take it off from a login session of this user") in out
    assert "AO state kept intact" in out and os.path.isdir(os.path.join(root, ".ao"))


@pytest.mark.skipif(os.name == "nt", reason=POSIX_SYSTEMD)
def test_uninstall_tries_and_names_a_projects_timers_when_the_manager_cannot_be_asked(project, tmp_path, monkeypatch):
    fake = _systemd(tmp_path, monkeypatch, reachable=False)
    _, watchdog, doctor = _units(A.project_key(project["root"]).lower())
    fake.change(active=[f"{watchdog}.timer"])

    jobs = cli._machine_jobs()

    assert jobs == [("systemd timer", f"{doctor}.timer"), ("systemd timer", f"{watchdog}.timer")]
    assert all("reaches no user manager" in cli._unschedule(name) for _, name in jobs)


@pytest.mark.parametrize("said", [("active", 0), ("", 0), ("maintenance\ninactive", 3)])
def test_an_answer_that_is_not_a_state_for_each_unit_is_no_answer(tmp_path, monkeypatch, said):
    """One line for two units, none at all, or a word that is neither running nor stopped: none says the job is
    gone, and each read so."""
    runtime = tmp_path / "run" / "systemd" / "system"
    runtime.mkdir(parents=True)
    monkeypatch.setattr(cli, "SYSTEMD_RUNTIME", str(runtime))
    monkeypatch.setattr(cli, "_scheduler", lambda: "systemd")
    monkeypatch.setattr(cli, "_systemctl", lambda *args, merge=False: said)

    loaded = cli._systemd_loaded("ao-watchdog-svc")

    assert isinstance(loaded, str) and loaded, loaded
    assert ("systemd timer", "ao-watchdog-svc.timer") in cli._installed_jobs("svc")
    assert cli._systemd_running({"a.timer": "inactive", "a.service": "failed"}) is False


@pytest.mark.skipif(os.name == "nt", reason=POSIX_SYSTEMD)
def test_a_timer_taken_off_is_gone_only_when_the_manager_says_each_unit_stopped(project, tmp_path, monkeypatch):
    _systemd(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "JOB_GONE_SECONDS", 0)
    _, watchdog, _ = _units(A.project_key(project["root"]).lower())
    assert cli.cmd_watchdog(project, _watchdog_args()) == 0
    asked = cli._systemd_states
    # A manager that answers its stop, then a word for the timer that says neither running nor stopped.
    monkeypatch.setattr(cli, "_systemd_states", lambda *units: dict(asked(*units), **{units[0]: "maintenance"}))

    left = cli._unschedule(f"{watchdog}.timer")

    assert left == (f"systemctl --user is-active answers 'maintenance' for {watchdog}.timer, neither running nor "
                    "stopped, so whether it still runs is not known — take it off from a login session of this "
                    "user: ao watchdog uninstall")


def _launchd_unlisted(monkeypatch):
    """A launchd whose listing fails: `launchctl list` exits 1 and lists nothing, so no label can be read in it."""
    loaded, asked = _launchd(monkeypatch)
    answers = A._run_program
    monkeypatch.setattr(A, "_run_program", lambda argv, **kwargs: ("", 1) if argv[1:] == ["list"]
                        else answers(argv, **kwargs))
    return loaded, asked


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_a_launchd_that_lists_nothing_keeps_the_state_of_a_project_it_may_run_a_job_for(tmp_path, monkeypatch,
                                                                                      capsys):
    _home(tmp_path, monkeypatch)
    root = _project(tmp_path, "svc")
    key = A.project_key(root)
    _launchd_unlisted(monkeypatch)
    labels = [cli._launchd_label(job, key) for job in cli.LAUNCHD_JOBS]

    assert cli._installed_jobs(key) == [("launchd job", label) for label in labels]
    assert cli.cmd_remove({"root": root, "reviews": "semantic-review"}, REMOVE) == 1

    out = _plain(capsys)
    assert (f"left launchd job {labels[0]}: launchd cannot say whether it still holds it (launchctl list exited 1)"
            in out) and "AO state kept intact" in out
    assert os.path.isdir(os.path.join(root, ".ao"))


@pytest.mark.skipif(os.name == "nt", reason=LAUNCHD_ONLY)
def test_uninstall_tries_and_names_each_registered_projects_jobs_when_launchd_lists_nothing(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    key = A.project_key(_project(tmp_path, "svc"))
    _launchd_unlisted(monkeypatch)

    jobs = cli._machine_jobs()

    assert jobs == sorted(("launchd job", cli._launchd_label(job, key)) for job in cli.LAUNCHD_JOBS)
    assert all("launchd cannot say" in cli._unschedule(name) for _, name in jobs)


# ---- ae13156eded84720: what the recursive delete could not take ----------------------------------------------

def test_a_part_of_ao_state_that_cannot_be_deleted_is_named_and_the_removal_is_not_complete(tmp_path, monkeypatch,
                                                                                             capsys):
    root, cfg = _removable(tmp_path, monkeypatch)
    held = Path(root) / ".ao" / "ledger" / "held.json"
    held.write_text("{}\n", encoding="utf-8")
    real_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        if os.path.basename(os.fsdecode(path)) == "held.json":
            raise PermissionError(13, "Permission denied", os.fsdecode(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {unlink})

    assert cli.cmd_remove(cfg, REMOVE) == 1

    out = _plain(capsys)
    assert re.search(r"left \.ao/: \[Errno 13\] Permission denied: .*held\.json", out), out
    assert "not complete" in out and "phase 2/2 complete" not in out
    assert held.exists() and not (Path(root) / "agent-mail").exists()        # the rest went all the same


@pytest.mark.skipif(os.name == "nt", reason=LINKS)
def test_a_path_whose_directory_a_link_takes_outside_the_project_is_not_deleted_there(tmp_path, monkeypatch, capsys):
    """Found verifying the lead above: `.kiro` made a link to a directory elsewhere took the delete of
    `.kiro/steering/ao-playbook.md` there."""
    root, cfg = _removable(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "steering").mkdir(parents=True)
    playbook = elsewhere / "steering" / "ao-playbook.md"
    playbook.write_text("someone else's\n", encoding="utf-8")
    os.symlink(elsewhere, os.path.join(root, ".kiro"))
    link = Path(root) / ".ao" / "shared"
    os.symlink(elsewhere, link)                                              # a link inside: it goes, as a link

    assert cli.cmd_remove(cfg, REMOVE) == 1

    out = _plain(capsys)
    assert playbook.read_text(encoding="utf-8") == "someone else's\n"
    assert "left .kiro/steering/ao-playbook.md: its directory resolves outside the project" in out
    assert not os.path.lexists(link) and elsewhere.is_dir() and not (Path(root) / ".ao").exists()


# The plan's harness paths under a directory of their own, and what stands at each elsewhere.
UNDER_A_DIRECTORY = [(".kiro/steering", ["ao-coordination.md", "ao-playbook.md", "ao-single-writer.md",
                                         "ao-machine.md"]),
                     (".claude/skills", ["ao/SKILL.md"])]


@pytest.mark.skipif(os.name == "nt", reason=LINKS)
@pytest.mark.parametrize("directory, names", UNDER_A_DIRECTORY)
def test_a_link_swapped_in_for_a_directory_after_the_check_deletes_nothing_outside(tmp_path, monkeypatch, capsys,
                                                                                   directory, names):
    """The review's case: the containment was checked by path and the delete went by path, and a link put in place
    of a directory between the two - by an agent racing the removal - took the delete outside the project."""
    root, cfg = _removable(tmp_path, monkeypatch)
    inside, elsewhere = Path(root) / directory, tmp_path / "elsewhere"
    for name in names:
        for place in (inside, elsewhere):
            (place / name).parent.mkdir(parents=True, exist_ok=True)
            (place / name).write_text(f"{place.name}'s\n", encoding="utf-8")
    real_contains, swapped = cli._hook_contains, []

    def contains(parent, child):
        held = real_contains(parent, child)
        if not swapped and os.path.basename(os.fspath(child)) == inside.name:      # checked: now the link goes in
            swapped.append(inside.rename(inside.with_name(inside.name + "-moved")))
            os.symlink(elsewhere, inside)
        return held

    monkeypatch.setattr(cli, "_hook_contains", contains)

    assert cli.cmd_remove(cfg, REMOVE) == 1

    out = _plain(capsys)
    assert swapped and all((elsewhere / name).read_text(encoding="utf-8") == "elsewhere's\n" for name in names)
    assert re.search(rf"left {re.escape(directory)}/\S+: ", out) and "not complete" in out


@pytest.mark.skipif(os.name == "nt", reason=LINKS)
def test_a_link_swapped_in_inside_a_directory_as_it_is_emptied_deletes_nothing_outside(tmp_path, monkeypatch, capsys):
    """Below the plan's path the delete walks directories it holds open too: a directory swapped for a link once
    the one above it was read is never entered."""
    root, cfg = _removable(tmp_path, monkeypatch)
    ledger, elsewhere = Path(root) / ".ao" / "ledger", tmp_path / "elsewhere"
    (ledger / "verifications.jsonl").write_text("{}\n", encoding="utf-8")
    elsewhere.mkdir()
    (elsewhere / "verifications.jsonl").write_text("theirs\n", encoding="utf-8")
    real_scandir, swapped = os.scandir, []

    class Listing(list):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def close(self):
            pass

    def scandir(*args):
        with real_scandir(*args) as entries:
            found = Listing(entries)
        if not swapped and any(entry.name == "ledger" for entry in found):          # read: now the link goes in
            swapped.append(ledger.rename(ledger.with_name("ledger-moved")))
            os.symlink(elsewhere, ledger)
        return found

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {scandir})

    assert cli.cmd_remove(cfg, REMOVE) == 1

    assert swapped and (elsewhere / "verifications.jsonl").read_text(encoding="utf-8") == "theirs\n"
    assert "left .ao/: " in _plain(capsys)


@pytest.mark.parametrize("reviews", ["..", ".", "x/../.."])
def test_a_plan_path_naming_the_directory_above_or_the_project_itself_deletes_nothing(tmp_path, monkeypatch, capsys,
                                                                                    reviews):
    """The reviews directory comes from .ao/config.json, which an agent writes: `..` passed the check of the
    directory the path is in - the project - and the directory above the project was deleted whole."""
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_installed_jobs", lambda key: [])
    root = _project(tmp_path / "work", "svc")
    beside = tmp_path / "work" / "beside.txt"
    beside.write_text("kept\n", encoding="utf-8")
    (Path(root) / "src.py").write_text("kept\n", encoding="utf-8")
    cfg = {"root": root, "reviews": reviews}

    assert cli.cmd_remove(cfg, SimpleNamespace(yes=False, allow_shared_hooks=False)) == 0
    assert f"not {reviews}/: it names no path inside the project" in _plain(capsys)
    assert cli.cmd_remove(cfg, REMOVE) == 1

    assert f"left {reviews}/: it names no path inside the project, and nothing is deleted for it" in _plain(capsys)
    assert beside.read_text(encoding="utf-8") == "kept\n" and (Path(root) / "src.py").exists()
    assert not (Path(root) / ".ao").exists()                                 # the rest of the plan went


# ---- e7dfee67dadf7d2b: an MCP entry of another project's ----------------------------------------------------

@pytest.mark.parametrize("entry, ours", [
    (_serving("/p"), True),
    (_serving("/p", "--role", "reviewer"), True),
    ({"command": "/usr/bin/python3", "args": ["-m", "ao", "-C", "/p", "mcp", "serve"]}, True),
    (_serving("/other"), False),                                            # another project's ao
    ({"command": "/usr/bin/docker", "args": ["run", "tool", "mcp", "serve"]}, False),     # a program that ends so
    ({"command": "ao", "args": ["mcp", "serve"]}, False),                   # registered some other way: left
])
def test_an_mcp_entry_is_this_projects_when_it_serves_this_project(entry, ours):
    assert cli._serves_ao(entry, "/p") is ours


def test_remove_leaves_an_ao_entry_that_serves_another_project(tmp_path, monkeypatch, capsys):
    root, cfg = _removable(tmp_path, monkeypatch)
    other = _project(tmp_path, "web")
    mcp = _write_json(Path(root) / ".mcp.json", {"mcpServers": {"ao": _serving(other), "x": {"command": "x"}}})
    before = mcp.read_bytes()

    assert cli.cmd_remove(cfg, SimpleNamespace(yes=False, allow_shared_hooks=False)) == 0
    assert "the `ao` server entry" not in _plain(capsys)
    assert cli.cmd_remove(cfg, REMOVE) == 0

    assert mcp.read_bytes() == before and "phase 2/2 complete" in _plain(capsys)


# ---- d9ed9f517729daf0, 0f85f4da96e261fd, 531407213a21d084: the MCP file, written where it lies ------------------

def _kiro_file(root):
    """ao's entry in Kiro's MCP file, beside another server's: (the file, its declared key, remove when empty)."""
    return _write_json(Path(root) / ".kiro" / "settings" / "mcp.json",
                       {"mcpServers": {"ao": _serving(root), "other": {"command": "other"}}}), "mcpServers", False


@pytest.mark.skipif(os.name == "nt", reason=LINKS)
def test_a_link_swapped_in_after_the_file_was_found_is_not_written_through(tmp_path, monkeypatch):
    root, _ = _removable(tmp_path, monkeypatch)
    path, key, empty = _kiro_file(root)
    assert cli._mcp_entries(root)[0] == [(str(path), key, empty)]
    # The person's own file elsewhere, holding an entry for this project too.
    outside = _write_json(tmp_path / "home" / ".kiro" / "settings" / "mcp.json",
                          {"mcpServers": {"ao": _serving(root), "secret": {"env": {"TOKEN": "t"}}}})
    before = outside.read_bytes()
    os.remove(path)
    os.symlink(outside, path)

    with pytest.raises(ValueError, match="outside the project"):
        cli._drop_mcp_entry(str(path), key, empty, root)

    assert outside.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason=LINKS)
def test_a_directory_swapped_for_a_link_as_the_file_is_written_takes_the_write_nowhere(tmp_path, monkeypatch):
    root, _ = _removable(tmp_path, monkeypatch)
    path, key, empty = _kiro_file(root)
    outside = _write_json(tmp_path / "home" / ".kiro" / "settings" / "mcp.json", {"mcpServers": {"mine": {}}})
    before = outside.read_bytes()
    settings = path.parent
    real_dumps, swapped = json.dumps, []

    def dumps(*args, **kwargs):
        if not swapped:                                   # between the read and the write, as an agent racing it
            swapped.append(settings.rename(settings.with_name("settings-moved")))
            os.symlink(outside.parent, settings)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(json, "dumps", dumps)

    try:
        cli._drop_mcp_entry(str(path), key, empty, root)
    except (OSError, ValueError):
        pass                                              # refused is as good as written in place

    assert swapped and outside.read_bytes() == before
    assert not list(outside.parent.glob(".mcp.json.*")) and not list(outside.parent.glob(".*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason=LINKS)
def test_a_directory_swapped_just_before_the_new_file_is_made_takes_the_write_nowhere(tmp_path, monkeypatch):
    """After the last check and before the write, only a directory held open keeps the write in it: a path checked
    again is a path resolved again, through whatever link is there by then."""
    root, _ = _removable(tmp_path, monkeypatch)
    path, key, empty = _kiro_file(root)
    outside = _write_json(tmp_path / "home" / ".kiro" / "settings" / "mcp.json", {"mcpServers": {"mine": {}}})
    before = outside.read_bytes()
    settings = path.parent
    real_open, swapped = os.open, []

    def opened(name, flags, *args, **kwargs):
        if flags & os.O_CREAT and not swapped:
            swapped.append(settings.rename(settings.with_name("settings-moved")))
            os.symlink(outside.parent, settings)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {opened})

    try:
        cli._drop_mcp_entry(str(path), key, empty, root)
    except (OSError, ValueError):
        pass

    assert swapped and outside.read_bytes() == before and sorted(os.listdir(outside.parent)) == ["mcp.json"]


def test_a_change_made_to_the_file_while_the_entry_is_taken_out_is_not_overwritten(tmp_path, monkeypatch):
    root, _ = _removable(tmp_path, monkeypatch)
    path, key, empty = _kiro_file(root)
    theirs = {"mcpServers": {"ao": _serving(root), "other": {"command": "other"}, "added": {"command": "new"}}}
    real_dumps, written = json.dumps, []

    def dumps(*args, **kwargs):
        if not written:                                   # the harness adds a server as ao writes
            written.append(path.write_text(real_dumps(theirs), encoding="utf-8"))
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(json, "dumps", dumps)

    with pytest.raises(ValueError, match="changed while ao was taking its entry out"):
        cli._drop_mcp_entry(str(path), key, empty, root)

    assert json.loads(path.read_text(encoding="utf-8")) == theirs
    assert sorted(os.listdir(path.parent)) == ["mcp.json"]                   # no temporary file left beside it


def test_a_change_made_while_the_new_file_is_written_is_not_overwritten(tmp_path, monkeypatch):
    """The review's case, as far as files can hold it: the bytes are compared as the last thing before the rename.
    Both held-folder backends compare after syncing the replacement."""
    root, _ = _removable(tmp_path, monkeypatch)
    path, key, empty = _kiro_file(root)
    theirs = {"mcpServers": {"ao": _serving(root), "other": {"command": "other"}, "added": {"command": "new"}}}
    real_fsync, written = os.fsync, []

    def fsync(fd):
        if not written and stat.S_ISREG(os.fstat(fd).st_mode):       # the new file's sync: the harness writes now
            written.append(path.write_text(json.dumps(theirs), encoding="utf-8"))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)

    with pytest.raises(ValueError, match="changed while ao was taking its entry out"):
        cli._drop_mcp_entry(str(path), key, empty, root)

    assert written and json.loads(path.read_text(encoding="utf-8")) == theirs
    assert sorted(os.listdir(path.parent)) == ["mcp.json"]


def test_a_file_emptied_of_aos_entry_goes_with_its_directory_synced(tmp_path, monkeypatch):
    root, _ = _removable(tmp_path, monkeypatch)
    path = _write_json(Path(root) / ".mcp.json", {"mcpServers": {"ao": _serving(root)}})
    real_fsync, synced = os.fsync, []

    def fsync(fd):
        synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)

    assert cli._drop_mcp_entry(str(path), "mcpServers", True, root) == "file"

    assert not path.exists()
    # Windows syncs no directory, as storage.replace_file_durably syncs none there.
    assert synced == ([] if os.name == "nt" else [True])


def test_the_entry_goes_and_every_other_entry_and_key_stays(tmp_path, monkeypatch):
    root, _ = _removable(tmp_path, monkeypatch)
    path = _write_json(Path(root) / ".mcp.json", {"mcpServers": {"ao": _serving(root, "--role", "implementer"),
                                                                 "other": {"command": "o"}}, "inputs": [1]})
    path.chmod(0o640)

    assert cli._drop_mcp_entry(str(path), "mcpServers", True, root) == "entry"

    assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {"other": {"command": "o"}}, "inputs": [1]}
    assert not [name for name in os.listdir(root) if name.endswith(".tmp")]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o640


# ---- 38a3d1d2903bac44: the conformance harness, started as a program on every platform ------------------------

def test_conform_starts_its_harness_from_an_interpreter_whose_path_holds_a_space(project, tmp_path, monkeypatch,
                                                                                 capsys):
    if os.name != "nt":
        spaced = tmp_path / "python with space" / "bin"
        spaced.mkdir(parents=True)
        os.symlink(os.path.realpath(sys.executable), spaced / "python3")
        monkeypatch.setattr(sys, "executable", str(spaced / "python3"))

    assert cli.cmd_adapters(project, SimpleNamespace(action="conform", target="kiro")) == 0

    out = _plain(capsys)
    assert re.search(r"send\s+pass", out) and re.search(r"resume\s+pass", out), out


def test_conform_takes_the_words_that_start_a_harness(tmp_path):
    harness = os.path.join(os.path.dirname(A.__file__), "conformance_harness.py")
    results = dict((c, s) for c, s, _ in A.conform_adapter(A.load_adapter("kiro"), [sys.executable, "-I", harness],
                                                           str(tmp_path)))
    assert results["send"] == results["resume"] == "pass"
    standin = program(tmp_path / "bin", "harness", open(harness, encoding="utf-8").read())
    assert dict((c, s) for c, s, _ in A.conform_adapter(A.load_adapter("kiro"), standin, str(tmp_path)))["send"] \
        == "pass"


# ---- 340d7068e8b17af8, 38b4a67977d905c8: the throwaway's archive and its cleanup ----------------------------

class _OneSecond(datetime):
    """Every run in the same second."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 12, 9, 30, 0)


def _steps_writing(tag):
    def steps(cfg, root, tree, stamp, links, unlinked, heard):
        os.makedirs(os.path.join(tree, ".ao"), exist_ok=True)
        with open(os.path.join(tree, ".ao", f"{tag}.txt"), "w", encoding="utf-8") as fh:
            fh.write(tag)
        return True, None, None
    return steps


def test_two_throwaways_in_one_second_keep_an_archive_each(project, monkeypatch):
    monkeypatch.setattr(cli, "datetime", _OneSecond)
    archive = Path(A.HOME) / ".ao" / "archive" / A.project_key(project["root"])
    for tag in ("first", "second"):
        monkeypatch.setattr(cli, "_prove_steps", _steps_writing(tag))
        result = cli._prove_throwaway(project)
        assert result[:3] == (True, None, None)
        assert result[3] and not os.path.exists(result[3])

    assert sorted(os.listdir(archive)) == ["prove-20261012-093000", "prove-20261012-093000-2"]
    assert sorted(os.listdir(archive / "prove-20261012-093000" / ".ao")) == ["first.txt"]
    assert sorted(os.listdir(archive / "prove-20261012-093000-2" / ".ao")) == ["second.txt"]


def test_an_archive_that_cannot_be_written_is_said_and_the_worktree_goes_all_the_same(project, monkeypatch, capsys):
    root = project["root"]
    monkeypatch.setattr(cli, "_prove_steps", _steps_writing("run"))
    made, real_mkdtemp = [], tempfile.mkdtemp

    def mkdtemp(*args, **kwargs):
        made.append(real_mkdtemp(*args, **kwargs))
        return made[-1]

    def copytree(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(shutil, "copytree", copytree)

    result = cli._prove_throwaway(project)
    assert result[:3] == (True, None, None)
    assert result[3] == os.path.join(made[0], "tree")

    assert "were not archived: OSError: [Errno 28] No space left on device" in _plain(capsys)
    worktrees = subprocess.run(["git", "worktree", "list"], cwd=root, capture_output=True, text=True).stdout
    assert len(worktrees.splitlines()) == 1 and made and not os.path.exists(made[0])


# ---- a54662ba1633a992: the doctor where ao is not on an agent's PATH -----------------------------------------

def test_the_doctor_says_the_heartbeat_alarms_and_channels_where_agents_cannot_run_ao(project, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)    # no ao, nor any agent CLI, is found
    monkeypatch.setattr(A, "_run_program", lambda argv, **kwargs: ("", 0))    # a launchd or systemd holding nothing
    monkeypatch.setattr(cli, "_schtasks", lambda *args: ("", None))           # and no Task Scheduler to ask
    A.heartbeat(project["root"])

    cli.cmd_doctor(project, SimpleNamespace(check=False))

    out = _plain(capsys)
    assert "ao for agents   not on a spawned agent's PATH" in out
    for line in ("last tick       0m", "channels        desktop on", "ping            off", "features        "):
        assert line in out, line
    assert out.index("features        ") < out.index("ao for agents")


# ---- 6822ab92d182a397: Ctrl-C during a lane start, whatever reading its record raises ------------------------

def test_a_lane_start_stopped_by_a_signal_exits_130_whatever_its_record_holds(project, monkeypatch):
    root = project["root"]
    lanes = Path(root) / ".ao" / "lanes"
    lanes.mkdir(parents=True)
    # A record nested deeper than the parser goes: reading it raises RecursionError, which no read of it caught.
    (lanes / "X.json").write_text("[" * 1_000_000 + "]" * 1_000_000, encoding="utf-8")

    def stopped(root, cfg, item):
        raise KeyboardInterrupt("signal 15")

    monkeypatch.setattr(A, "start_lane", stopped)

    assert cli.main(["-C", root, "lane", "start", "X"]) == 130


@pytest.mark.parametrize("operation", ["mcp-write", "mcp-empty", "remove-file", "remove-tree"])
def test_removal_refuses_without_either_held_backend(tmp_path, monkeypatch, operation):
    root = tmp_path / "repo"
    root.mkdir()
    path = _write_json(root / "folder/mcp.json", {"mcpServers": {"ao": _serving(root)}})
    before = path.read_bytes()
    monkeypatch.setattr(os, "supports_dir_fd", set())
    monkeypatch.setattr(storage, "_windows_tree_available", lambda: False)
    if operation.startswith("mcp"):
        with pytest.raises(storage.InTheWay, match="cannot hold"):
            cli._drop_mcp_entry(str(path), "mcpServers", operation == "mcp-empty", str(root))
    else:
        rel = "folder/mcp.json" if operation == "remove-file" else "folder"
        assert "cannot hold" in cli._remove_in_project(str(root), rel)
    assert path.read_bytes() == before
    assert sorted(os.listdir(path.parent)) == ["mcp.json"]


@pytest.fixture
def native_removal_handles(tmp_path, monkeypatch):
    """Exercise the Windows backend everywhere; existing native tests hold its kernel contract."""
    live, calls = {}, []
    original_open, original_unlink = os.open, os.unlink
    original_rmdir, original_replace, original_scandir = os.rmdir, os.replace, os.scandir

    def pinned(path):
        parent = Path(path).parent
        while True:
            assert str(parent) in live.values(), f"folder not held: {parent}"
            if parent == parent.parent:
                break
            parent = parent.parent

    def close(handle):
        del live[handle]

    def opened(path, flags=None, mode=0o666):
        path = str(path)
        if flags is None:
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            if os.path.islink(path) or not os.path.isdir(path):
                raise storage.InTheWay(path, "not a folder of its own")
            handle = object()
            live[handle] = path
            return handle, close
        pinned(path)
        assert not os.path.islink(path)
        calls.append("open")
        return original_open(path, flags, mode)

    def unlink(path, *args, **kwargs):
        pinned(path)
        calls.append("unlink")
        return original_unlink(path, *args, **kwargs)

    def rmdir(path, *args, **kwargs):
        pinned(path)
        assert str(path) not in live.values(), "release the child before deleting its name"
        calls.append("rmdir")
        return original_rmdir(path, *args, **kwargs)

    def replace(source, target, *args, **kwargs):
        pinned(source)
        pinned(target)
        calls.append("replace")
        return original_replace(source, target, *args, **kwargs)

    def scandir(path):
        assert str(path) in live.values(), "hold the directory while listing it"
        calls.append("scandir")
        return original_scandir(path)

    # Patch only during the operation, so pytest's cleanup uses the real filesystem APIs.
    from contextlib import contextmanager
    @contextmanager
    def active():
        with monkeypatch.context() as patch:
            patch.setattr(os, "supports_dir_fd", set())
            patch.setattr(storage, "_windows_tree_available", lambda: True)
            patch.setattr(storage, "_open_tree_nt", opened)
            patch.setattr(os, "unlink", unlink)
            patch.setattr(os, "rmdir", rmdir)
            patch.setattr(os, "replace", replace)
            patch.setattr(os, "scandir", scandir)
            yield calls
        assert live == {}, "every handle must be released on success and on error"
    return active


@pytest.mark.parametrize("operation", ["mcp-write", "mcp-empty", "remove-tree", "changed", "write-error"])
def test_removal_uses_native_held_folders(tmp_path, monkeypatch, native_removal_handles, operation):
    root = tmp_path / "repo"
    root.mkdir()
    path = _write_json(root / "folder/inner/mcp.json", {"mcpServers": {"ao": _serving(root)}})
    before = path.read_bytes()
    with native_removal_handles() as calls:
        if operation == "remove-tree":
            assert cli._remove_in_project(str(root), "folder") is None
            assert calls.count("scandir") == 2 and calls.count("rmdir") == 2
            assert not (root / "folder").exists()
        elif operation in ("changed", "write-error"):
            original = os.fsync
            def sync(fd):
                if operation == "write-error":
                    raise OSError("write interrupted")
                path.write_bytes(b'{"mcpServers": {"person": {}}}')
                return original(fd)
            with monkeypatch.context() as patch:
                patch.setattr(os, "fsync", sync)
                with pytest.raises((OSError, ValueError), match="write interrupted|changed while"):
                    cli._drop_mcp_entry(str(path), "mcpServers", False, str(root))
            assert "open" in calls and "unlink" in calls and "replace" not in calls
            assert path.read_bytes() == (before if operation == "write-error" else
                                         b'{"mcpServers": {"person": {}}}')
            assert sorted(os.listdir(path.parent)) == ["mcp.json"]
        else:
            empty = operation == "mcp-empty"
            assert cli._drop_mcp_entry(str(path), "mcpServers", empty, str(root)) == (
                "file" if empty else "entry")
            assert "open" in calls and ("unlink" if empty else "replace") in calls
            if empty:
                assert not path.exists()
            else:
                assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {}}


def test_removal_unlinks_a_windows_junction_without_entering_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "_tree_stat",
                        lambda *args: SimpleNamespace(st_file_attributes=0x410))
    monkeypatch.setattr(storage, "_tree_rmdir", lambda *args: calls.append(("rmdir", args)))
    monkeypatch.setattr(storage, "_tree_unlink", lambda *args: calls.append(("unlink", args)))
    storage._tree_remove_leaf(str(tmp_path), "junction")
    assert calls == [("rmdir", (str(tmp_path), "junction"))]


def test_remove_gitignore_rewrite_does_not_follow_a_swapped_leaf(tmp_path, monkeypatch):
    root, cfg = _removable(tmp_path, monkeypatch)
    lines = [".ao/hold"]
    path = _gitignore(root, cli._gitignore_block(lines) + "\n".join(["", *lines, ""]))
    outside = tmp_path / "person"
    outside.write_bytes(b"person's file")
    original = cli._gitignore_without_ao
    swapped, reads = [], []
    def read(where):
        result = original(where)
        reads.append(True)
        if result[2] and len(reads) == 2:
            path.unlink()
            try:
                path.symlink_to(outside)
            except OSError:
                pytest.skip("symbolic links require permission on this Windows host")
            swapped.append(True)
        return result
    monkeypatch.setattr(cli, "_gitignore_without_ao", read)
    cli.cmd_remove(cfg, REMOVE)
    assert swapped and outside.read_bytes() == b"person's file"

@pytest.mark.skipif(os.name == "nt", reason="Windows keeps no POSIX mode")
def test_mcp_removal_keeps_the_mode_despite_umask(tmp_path):
    path = _write_json(tmp_path / "mcp.json", {"mcpServers": {"ao": _serving(tmp_path)}})
    path.chmod(0o664)
    previous = os.umask(0o077)
    try:
        assert cli._drop_mcp_entry(str(path), "mcpServers", False, str(tmp_path)) == "entry"
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == 0o664


def test_held_opt_in_reader_refuses_an_in_tree_seal_intent(project):
    root = project["root"]
    rows = [A.record_opt_in(root, "features.probe", value, "Person") for value in (True, False, True)]
    path = Path(A.opt_ins_path(root))
    path.write_text(json.dumps(rows[-1]) + "\n", encoding="utf-8")
    pending = path.with_name(path.name + ".seal.json.pending")
    pending.write_text(json.dumps({"retired": 2, "digest": rows[-1]["previous"]}), encoding="utf-8")
    before = Path(storage.checkpoint_path()).read_bytes()
    with pytest.raises(storage.LedgerCorruption, match="this machine did not begin"):
        A.recorded_opt_in(root, "features.probe")
    assert pending.exists() and Path(storage.checkpoint_path()).read_bytes() == before


# Independent review: paths inside the project must still be opened without following links.
@pytest.mark.skipif(os.name == "nt", reason=LINKS)
@pytest.mark.parametrize("directory, leaf", [("link", "src"), ("link", ".git"),
                                           (".kiro/steering", "ao-playbook.md"),
                                           (".claude/skills/ao", "SKILL.md")])
def test_review_paths_removal_refuses_internal_directory_links(tmp_path, directory, leaf):
    root = tmp_path / "repo"
    root.mkdir()
    target = root if directory == "link" else root / "product"
    target.mkdir(exist_ok=True)
    kept = target / leaf
    if leaf in ("src", ".git"):
        kept.mkdir()
        kept = kept / "person.txt"
    kept.write_bytes(b"person's product")
    link = root / directory
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)

    problem = cli._remove_in_project(str(root), directory + "/" + leaf)

    assert problem, "a directory link in the removal plan must be refused"
    assert link.is_symlink() and kept.read_bytes() == b"person's product"


@pytest.mark.skipif(os.name == "nt", reason=LINKS)
@pytest.mark.parametrize("empty, directory_link", [(True, False), (False, False), (False, True)])
def test_review_paths_mcp_refuses_internal_links_swapped_after_discovery(tmp_path, monkeypatch, empty, directory_link):
    root, _ = _removable(tmp_path, monkeypatch)
    path = Path(root) / (".mcp.json" if empty else ".kiro/settings/mcp.json")
    document = {"mcpServers": {"ao": _serving(root)}}
    if not empty:
        document["mcpServers"]["person"] = {"command": "person"}
    _write_json(path, document)
    assert (str(path), "mcpServers", empty) in cli._mcp_entries(root)[0]
    target = _write_json(Path(root) / "product/mcp.json", document)
    before = target.read_bytes()
    if directory_link:
        path.parent.rename(path.parent.with_name("settings-saved"))
        path.parent.symlink_to(target.parent, target_is_directory=True)
    else:
        path.unlink()
        path.symlink_to(target)

    with pytest.raises(storage.InTheWay):
        cli._drop_mcp_entry(str(path), "mcpServers", empty, root)

    assert target.read_bytes() == before
    assert (path.parent if directory_link else path).is_symlink()
    assert list(target.parent.iterdir()) == [target]


def test_review_paths_gitignore_accepts_non_utf8_bytes(tmp_path, monkeypatch):
    root, cfg = _removable(tmp_path, monkeypatch)
    lines = [".ao/hold"]
    path = Path(root) / ".gitignore"
    path.write_bytes(b"build-\xff/\r\n" + (cli._gitignore_block(lines) + "\r\n.ao/hold\r\n").encode("utf-8"))

    assert cli.cmd_remove(cfg, REMOVE) == 0

    assert path.read_text(encoding="utf-8") == "build-\ufffd/\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO")
def test_review_paths_gitignore_fifo_is_refused_without_a_blocking_open(tmp_path, monkeypatch, capsys):
    import builtins
    root, cfg = _removable(tmp_path, monkeypatch)
    path = Path(root) / ".gitignore"
    os.mkfifo(path)
    original = builtins.open

    def opened(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and os.fsdecode(file) == str(path):
            pytest.fail("a FIFO must never reach a blocking open")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", opened)

    assert cli.cmd_remove(cfg, REMOVE) == 1

    assert stat.S_ISFIFO(path.lstat().st_mode)
    assert "left" in _plain(capsys)


@pytest.mark.skipif(os.name == "nt", reason=LINKS)
def test_review_paths_gitignore_link_before_second_read_is_never_copied(tmp_path, monkeypatch, capsys):
    root, cfg = _removable(tmp_path, monkeypatch)
    lines = [".ao/hold"]
    path = _gitignore(root, cli._gitignore_block(lines) + "\n.ao/hold\n")
    outside = tmp_path / "private-ignore"
    secret = "private contents\n" + cli._gitignore_block(lines) + "\n.ao/hold\n"
    outside.write_text(secret, encoding="utf-8")
    original, reads = cli._gitignore_without_ao, []

    def read(where):
        reads.append(True)
        if len(reads) == 2:
            path.unlink()
            path.symlink_to(outside)
        return original(where)

    monkeypatch.setattr(cli, "_gitignore_without_ao", read)

    assert cli.cmd_remove(cfg, REMOVE) == 1

    assert len(reads) == 2 and path.is_symlink()
    assert outside.read_text(encoding="utf-8") == secret
    out = _plain(capsys)
    assert "left" in out and ".gitignore" in out and "private contents" not in out
