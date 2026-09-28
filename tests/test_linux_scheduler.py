"""On Linux the watchdog's two jobs are systemd user units, as they are launchd jobs on macOS (LINUX-SCHEDULER).

`ao watchdog install` wrote a plist into ~/Library/LaunchAgents and asked launchctl, which a Linux
machine does not have: the install failed there, and the watchdog ran only when someone ran it. Each
job is now a oneshot service and a timer in ~/.config/systemd/user, started at once and then every
interval as the launchd job's RunAtLoad and StartInterval start it, enabled with `systemctl --user`
by argument vector. Where no user systemd answers - a container, WSL without systemd, a `su` shell -
nothing is written, and install says why and what would run the jobs instead.

The units are written into a scratch home and read back here, and `systemctl` and `loginctl` are
fake executables first on PATH that keep what they were asked in a file, so every call is the real
program-start path ao takes.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake systemctl is a POSIX executable; Windows "
                                                          "schedules the watchdog with Task Scheduler")

PROGRAMS = {"ao-watchdog": ["/opt/ao tool/bin/ao-watchdog"], "ao": ["/opt/ao tool/bin/ao"]}

# A user manager that loads the unit files present at its last daemon-reload, and a loginctl.
FAKE_SYSTEMCTL = r'''
import json, os, sys

STATE, UNITS = os.environ["AO_FAKE_SYSTEMD"], os.environ["AO_FAKE_UNITS"]
with open(STATE, encoding="utf-8") as fh:
    state = json.load(fh)
args = sys.argv[1:]
state["calls"].append(args)


def done(code, out="", err=""):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    sys.exit(code)


if args[:1] != ["--user"]:
    done(2, err="this fake is a user manager only")
if not state["reachable"]:
    done(1, err="Failed to connect to bus: No medium found")
verb, units = args[1], [word for word in args[2:] if not word.startswith("--")]
wants = os.path.join(UNITS, "timers.target.wants")
if verb == "show":
    done(0, out="Version=255")
if verb == "daemon-reload":
    state["loaded"] = sorted(name for name in os.listdir(UNITS) if os.path.isfile(os.path.join(UNITS, name))) \
        if os.path.isdir(UNITS) else []
    done(0)
if verb == "enable":
    for unit in units:
        if unit not in state["loaded"]:
            done(1, err=f"Failed to enable unit: Unit file {unit} does not exist.")
        os.makedirs(wants, exist_ok=True)
        if not os.path.lexists(os.path.join(wants, unit)):
            os.symlink(os.path.join(UNITS, unit), os.path.join(wants, unit))
        state["enabled"] = sorted(set(state["enabled"]) | {unit})
    done(0, err="Created symlink.")
if verb == "restart":
    state["active"] = sorted(set(state["active"]) | set(units))
    done(0)
if verb in ("disable", "stop"):
    for unit in units:
        if verb == "disable" and os.path.lexists(os.path.join(wants, unit)):
            os.remove(os.path.join(wants, unit))
        state["enabled"] = [name for name in state["enabled"] if verb == "stop" or name != unit]
        if (verb == "stop" or "--now" in args) and unit not in state["stuck"]:
            state["active"] = [name for name in state["active"] if name != unit]
    done(0)
if verb == "is-active":
    done(0 if set(units) & set(state["active"]) else 3,
         out="\n".join("active" if unit in state["active"] else "inactive" for unit in units))
done(2, err=f"the fake does not know: {args}")
'''

FAKE_LOGINCTL = r'''
import os, sys
linger = os.environ.get("AO_FAKE_LINGER", "no")
if linger == "unknown":
    print("Failed to get user: User ID is not logged in or lingering", file=sys.stderr)
    sys.exit(1)
print(f"Linger={linger}")
'''


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _args(action="install", **changes):
    """What `ao watchdog <action>` parses to, with an idle time given so no setting is read."""
    return SimpleNamespace(**{"action": action, "idle_minutes": 6, "interval": 120, **changes})


def _fake(bindir, name, source):
    """A program called `name` in `bindir` that runs `source` with this interpreter."""
    script = bindir / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    program = bindir / name
    program.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} \"$@\"\n",
                       encoding="utf-8")
    program.chmod(0o755)


def _systemd(tmp_path, monkeypatch, booted=True, reachable=True, linger="no"):
    """ao on a Linux whose user systemd and loginctl are the fakes above; the fake manager's state, as a reader."""
    home = Path(A.HOME)
    monkeypatch.setenv("HOME", str(home))                   # the programs are looked for from here, not a real home
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    _fake(bindir, "systemctl", FAKE_SYSTEMCTL)
    _fake(bindir, "loginctl", FAKE_LOGINCTL)
    state = tmp_path / "systemd.json"
    state.write_text(json.dumps({"calls": [], "loaded": [], "enabled": [], "active": [], "stuck": [],
                                 "reachable": reachable}), encoding="utf-8")
    monkeypatch.setenv("PATH", os.pathsep.join([str(bindir), os.environ.get("PATH", "")]))
    monkeypatch.setenv("AO_FAKE_SYSTEMD", str(state))
    monkeypatch.setenv("AO_FAKE_UNITS", str(home / ".config" / "systemd" / "user"))
    monkeypatch.setenv("AO_FAKE_LINGER", linger)
    runtime = tmp_path / "run" / "systemd" / "system"
    if booted:
        runtime.mkdir(parents=True)
    monkeypatch.setattr(cli, "SYSTEMD_RUNTIME", str(runtime))
    monkeypatch.setattr(cli, "_scheduler", lambda: "systemd")
    monkeypatch.setattr(cli, "_scheduled_argv", lambda console, clone, module: list(PROGRAMS[console]))
    paths = [tmp_path / "tools bin", tmp_path / "100%"]      # a space and a percent, as a PATH may hold them
    for path in paths:
        path.mkdir()
    monkeypatch.setattr(W, "child_path", lambda: os.pathsep.join(str(path) for path in paths))

    def read():
        return json.loads(state.read_text(encoding="utf-8"))

    def change(**values):
        state.write_text(json.dumps(dict(read(), **values)), encoding="utf-8")

    read.change = change
    read.path = os.pathsep.join(str(path) for path in paths)
    return read


def _directives(text):
    """{name: [value, ...]} of a unit file's settings, comments and section headers left out."""
    found = {}
    for line in text.split("\n"):
        if line and not line.startswith(("#", "[")):
            name, _, value = line.partition("=")
            found.setdefault(name, []).append(value)
    return found


def _systemd_words(value, expand=True):
    """The words systemd reads from an ExecStart= or Environment= value, as systemd.syntax(7) and
    systemd.service(5) describe it: `%%` is a `%`, each word is unquoted and C-unescaped, and on a
    line that expands variables `$$` is a `$`. Anything else those rules would change fails here."""
    text, i = [], 0
    while i < len(value):
        assert value[i] != "%" or value[i + 1:i + 2] == "%", f"systemd would expand the specifier in {value!r}"
        text.append(value[i])
        i += 2 if value[i] == "%" else 1
    text, words, i = "".join(text), [], 0
    while i < len(text):
        if text[i] == " ":
            i += 1
            continue
        assert text[i] == '"', f"an unquoted word in {text!r}"
        word, i = [], i + 1
        while text[i] != '"':
            if text[i] == "\\" and text[i + 1] == "x":
                word.append(chr(int(text[i + 2:i + 4], 16)))
                i += 4
            elif text[i] == "\\":
                assert text[i + 1] in '\\"', f"an escape ao does not write in {text!r}"
                word.append(text[i + 1])
                i += 2
            else:
                word.append(text[i])
                i += 1
        words.append("".join(word))
        i += 1
    if expand:
        for word in words:
            assert "$" not in word.replace("$$", ""), f"systemd would expand a variable in {word!r}"
        words = [word.replace("$$", "$") for word in words]
    return words


def _units(key):
    """The user unit directory, and the names without suffix of the two jobs' units for a project's key."""
    return Path(A.HOME) / ".config" / "systemd" / "user", f"ao-watchdog-{key}", f"ao-doctor-{key}"


# ---- install -----------------------------------------------------------------------------------

def test_install_writes_a_service_and_a_timer_for_each_job_and_enables_and_starts_the_timers(project, tmp_path,
                                                                                               monkeypatch, capsys):
    fake = _systemd(tmp_path, monkeypatch)
    root, key = project["root"], A.project_key(project["root"]).lower()
    units, watchdog, doctor = _units(key)
    log, dlog = (os.path.join(A.HOME, ".ao", f"{job}-{key}.log") for job in ("watchdog", "doctor"))

    assert cli.cmd_watchdog(project, _args()) == 0

    service = _directives((units / f"{watchdog}.service").read_text(encoding="utf-8"))
    timer = _directives((units / f"{watchdog}.timer").read_text(encoding="utf-8"))
    assert _systemd_words(service["ExecStart"][0]) == PROGRAMS["ao-watchdog"] + ["--root", root, "--idle-minutes", "6"]
    assert _systemd_words(service["Environment"][0], expand=False) == [f"PATH={fake.path}"]
    assert service["Type"] == ["oneshot"] and service["KillMode"] == ["process"]
    assert service["StandardOutput"] == service["StandardError"] == [f"append:{log}"]
    assert timer["OnActiveSec"] == ["0"] and timer["OnUnitActiveSec"] == ["120s"] and timer["AccuracySec"] == ["1s"]
    assert timer["WantedBy"] == ["timers.target"]
    dservice = _directives((units / f"{doctor}.service").read_text(encoding="utf-8"))
    dtimer = _directives((units / f"{doctor}.timer").read_text(encoding="utf-8"))
    assert _systemd_words(dservice["ExecStart"][0]) == PROGRAMS["ao"] + ["-C", root, "doctor", "--check", "--notify"]
    assert dservice["StandardOutput"] == [f"append:{dlog}"] and dtimer["OnUnitActiveSec"] == ["900s"]

    timers = [f"{watchdog}.timer", f"{doctor}.timer"]
    assert fake()["calls"] == [["--user", "show", "--property=Version"], ["--user", "daemon-reload"],
                               ["--user", "enable", *timers], ["--user", "restart", *timers],
                               ["--user", "is-active", *timers]]
    assert fake()["active"] == fake()["enabled"] == sorted(timers)
    out = _plain(capsys)
    assert f"installed {watchdog}.timer" in out and f"installed {doctor}.timer" in out
    assert cli.LINGER_ADVICE in out and f"remove with: ao -C {root} watchdog uninstall" in out
    assert not (Path(A.HOME) / "Library").exists()


def test_the_doctor_reads_the_interval_the_timer_was_installed_with(project, tmp_path, monkeypatch):
    _systemd(tmp_path, monkeypatch)
    root = project["root"]
    assert cli._watchdog_interval(root) == 120              # nothing installed: what install schedules by default

    assert cli.cmd_watchdog(project, _args(interval=300)) == 0

    assert cli._watchdog_interval(root) == 300


def test_install_says_so_when_the_manager_does_not_start_a_timer(project, tmp_path, monkeypatch, capsys):
    fake = _systemd(tmp_path, monkeypatch)
    real = cli._systemd_states
    monkeypatch.setattr(cli, "_systemd_states", lambda *units: dict(real(*units), **{units[-1]: "failed"}))

    assert cli.cmd_watchdog(project, _args()) == 1

    out = _plain(capsys)
    key = A.project_key(project["root"]).lower()
    assert f"NOT SCHEDULED: ao-doctor-{key}.timer is failed" in out and "installed" not in out
    assert f"run: systemctl --user enable --now ao-watchdog-{key}.timer ao-doctor-{key}.timer" in out
    assert fake()["calls"][-1][1] == "is-active"


def test_an_interval_that_would_start_each_cycle_as_the_last_ends_is_refused(project, tmp_path, monkeypatch, capsys):
    fake = _systemd(tmp_path, monkeypatch)

    assert cli.cmd_watchdog(project, _args(interval=0)) == 1

    assert "--interval is the seconds between cycles, at least 1" in _plain(capsys)
    assert not (Path(A.HOME) / ".config").exists() and fake()["calls"] == []


def test_linger_is_advised_only_when_loginctl_says_it_is_off(project, tmp_path, monkeypatch, capsys):
    _systemd(tmp_path, monkeypatch, linger="yes")

    assert cli.cmd_watchdog(project, _args()) == 0
    assert cli.cmd_watchdog(project, _args("status")) == 0

    out = _plain(capsys)
    assert cli.LINGER_ADVICE not in out and "\nlinger  yes\n" in out
    monkeypatch.setenv("AO_FAKE_LINGER", "unknown")         # loginctl knows no session of this user
    assert cli.cmd_watchdog(project, _args("status")) == 0
    assert "\nlinger  unknown\n" in _plain(capsys)


# ---- where there is no user systemd ------------------------------------------------------------

def test_without_systemd_nothing_is_written_and_the_crontab_that_would_run_the_jobs_is_printed(project, tmp_path,
                                                                                                 monkeypatch, capsys):
    fake = _systemd(tmp_path, monkeypatch, booted=False)
    root, key = project["root"], A.project_key(project["root"]).lower()

    assert cli.cmd_watchdog(project, _args()) == 1

    out = _plain(capsys)
    assert "not installed: no user systemd — this machine does not run systemd (a container" in out
    assert not (Path(A.HOME) / ".config").exists() and fake()["calls"] == []
    lines = {line.split(" >> ")[0].strip(): line for line in out.split("\n") if " * * * * " in line}
    watchdog = [*PROGRAMS["ao-watchdog"], "--root", root, "--idle-minutes", "6"]
    doctor = [*PROGRAMS["ao"], "-C", root, "doctor", "--check", "--notify"]
    assert set(lines) == {f"*/2 * * * * {shlex.join(watchdog)}", f"*/15 * * * * {shlex.join(doctor)}"}
    log = os.path.join(A.HOME, ".ao", f"watchdog-{key}.log")
    assert lines[f"*/2 * * * * {shlex.join(watchdog)}"].endswith(f" >> {shlex.quote(log)} 2>&1")
    assert f"one watchdog cycle by hand: {shlex.join(watchdog)}" in out


def test_a_manager_this_shell_cannot_reach_is_named_with_what_systemctl_said(project, tmp_path, monkeypatch, capsys):
    fake = _systemd(tmp_path, monkeypatch, reachable=False)

    assert cli.cmd_watchdog(project, _args()) == 1

    out = _plain(capsys)
    assert "systemctl --user reaches no user manager: Failed to connect to bus: No medium found" in out
    assert not (Path(A.HOME) / ".config").exists()
    assert fake()["calls"] == [["--user", "show", "--property=Version"]]


def test_a_crontab_line_escapes_the_percent_cron_would_read_as_a_line_break(capsys):
    cli._systemd_absent("no systemd", {"watchdog": ["/opt/ao", "--root", "/w/50%"], "doctor": ["/opt/ao"]},
                        {"watchdog": 90, "doctor": 900}, {"watchdog": "/h/w.log", "doctor": "/h/d.log"})

    out = _plain(capsys)
    assert "*/2 * * * * /opt/ao --root /w/50\\% >> /h/w.log 2>&1" in out       # 90s is every two minutes to cron


def test_a_log_path_a_unit_cannot_name_is_refused_before_anything_is_written(project, tmp_path, monkeypatch, capsys):
    fake = _systemd(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "project_key", lambda root: "pro\x01j")

    assert cli.cmd_watchdog(project, _args()) == 1

    assert "a unit cannot send output to a path holding a control character" in _plain(capsys)
    assert not (Path(A.HOME) / ".config").exists() and ["--user", "daemon-reload"] not in fake()["calls"]


# ---- status and uninstall ----------------------------------------------------------------------

def test_status_reads_the_timers_linger_and_the_log(project, tmp_path, monkeypatch, capsys):
    _systemd(tmp_path, monkeypatch)
    key = A.project_key(project["root"]).lower()
    status = _args("status")
    assert cli.cmd_watchdog(project, status) == 0
    before = _plain(capsys)
    assert "timer   inactive" in before and "units   absent  (~/.config/systemd/user)" in before

    cli.cmd_watchdog(project, _args())
    (Path(A.HOME) / ".ao" / f"watchdog-{key}.log").write_text("cycle 1\ncycle 2\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.cmd_watchdog(project, status) == 0

    out = _plain(capsys).split("\n")
    assert out[:5] == [f"unit    ao-watchdog-{key}.timer", "timer   active",
                       "doctor  active  (ao doctor --check every 15m)", "units   present  (~/.config/systemd/user)",
                       f"linger  no — {cli.LINGER_ADVICE}"]
    assert "\n".join(out).rstrip().endswith("cycle 1\ncycle 2")


def test_status_without_a_user_manager_says_why(project, tmp_path, monkeypatch, capsys):
    _systemd(tmp_path, monkeypatch, reachable=False)

    assert cli.cmd_watchdog(project, _args("status")) == 0

    out = _plain(capsys)
    assert "timer   unknown" in out and "systemd systemctl --user reaches no user manager" in out


def test_uninstall_disables_and_stops_both_jobs_removes_their_files_and_the_heartbeat(project, tmp_path,
                                                                                     monkeypatch, capsys):
    fake = _systemd(tmp_path, monkeypatch)
    root, key = project["root"], A.project_key(project["root"]).lower()
    units, watchdog, doctor = _units(key)
    cli.cmd_watchdog(project, _args())
    A.heartbeat(root)
    fake.change(calls=[])

    assert cli.cmd_watchdog(project, _args("uninstall")) == 0

    assert sorted(os.listdir(units)) == ["timers.target.wants"] and os.listdir(units / "timers.target.wants") == []
    assert fake()["active"] == fake()["enabled"] == []
    assert [" ".join(call[1:]) for call in fake()["calls"]] == [
        step for unit in (watchdog, doctor)
        for step in ("show --property=Version", f"disable --now {unit}.timer", f"stop {unit}.service",
                     "daemon-reload", f"is-active {unit}.timer")]
    assert not os.path.exists(A.heartbeat_path(root))
    out = _plain(capsys)
    assert f"removed {watchdog}.timer" in out and f"removed {doctor}.timer" in out

    assert cli.cmd_watchdog(project, _args("uninstall")) == 0
    assert "removed" not in _plain(capsys)                  # nothing was there the second time


# ---- ao remove ---------------------------------------------------------------------------------

def test_remove_finds_the_jobs_by_their_files_and_takes_each_off_checked_gone(project, tmp_path, monkeypatch):
    fake = _systemd(tmp_path, monkeypatch)
    key = A.project_key(project["root"])
    units, watchdog, doctor = _units(key.lower())
    assert cli._installed_jobs(key) == []
    cli.cmd_watchdog(project, _args())

    assert cli._installed_jobs(key) == [("systemd timer", f"{watchdog}.timer"), ("systemd timer", f"{doctor}.timer")]
    assert [cli._unschedule(name) for _, name in cli._installed_jobs(key)] == [None, None]

    assert cli._installed_jobs(key) == [] and fake()["active"] == []


def test_a_timer_that_stays_active_is_named_and_its_files_are_gone(project, tmp_path, monkeypatch):
    fake = _systemd(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "JOB_GONE_SECONDS", 0)
    units, watchdog, _ = _units(A.project_key(project["root"]).lower())
    cli.cmd_watchdog(project, _args())
    fake.change(stuck=[f"{watchdog}.timer"])

    left = cli._unschedule(f"{watchdog}.timer")

    assert left == f"still active — systemctl --user disable --now {watchdog}.timer"
    assert not (units / f"{watchdog}.timer").exists()


def test_a_manager_out_of_reach_leaves_the_jobs_and_their_files_to_a_session_that_reaches_it(project, tmp_path,
                                                                                             monkeypatch):
    fake = _systemd(tmp_path, monkeypatch)
    units, watchdog, _ = _units(A.project_key(project["root"]).lower())
    cli.cmd_watchdog(project, _args())
    fake.change(reachable=False, calls=[])

    left = cli._unschedule(f"{watchdog}.timer")

    assert left.startswith("systemctl --user reaches no user manager: Failed to connect to bus")
    assert left.endswith("take it off from a login session of this user: ao watchdog uninstall")
    assert (units / f"{watchdog}.timer").exists() and (units / f"{watchdog}.service").exists()
    assert f"{watchdog}.timer" in fake()["active"]


def test_without_systemd_a_jobs_files_are_all_there_is_to_remove(project, tmp_path, monkeypatch):
    fake = _systemd(tmp_path, monkeypatch)
    units, watchdog, _ = _units(A.project_key(project["root"]).lower())
    cli.cmd_watchdog(project, _args())
    os.rmdir(cli.SYSTEMD_RUNTIME)
    fake.change(calls=[])

    assert os.path.islink(units / "timers.target.wants" / f"{watchdog}.timer")

    assert cli._unschedule(f"{watchdog}.timer") is None

    assert not any(os.path.lexists(path) for path in (units / f"{watchdog}.service", units / f"{watchdog}.timer",
                                                      units / "timers.target.wants" / f"{watchdog}.timer"))
    assert fake()["calls"] == []


# ---- the doctor --------------------------------------------------------------------------------

def test_the_doctor_shows_the_watchdog_running_from_its_timer(project, tmp_path, monkeypatch, capsys):
    _systemd(tmp_path, monkeypatch)
    cli.cmd_doctor(project, SimpleNamespace(check=False))
    assert "watchdog        not installed — ao watchdog install" in _plain(capsys)

    cli.cmd_watchdog(project, _args())
    cli.cmd_doctor(project, SimpleNamespace(check=False))

    assert "watchdog        running" in _plain(capsys)


# ---- the pieces --------------------------------------------------------------------------------

def test_linux_schedules_with_systemd_windows_with_task_scheduler_and_the_rest_with_launchd(monkeypatch):
    for name, platform, scheduler in (("nt", "win32", "schtasks"), ("posix", "darwin", "launchd"),
                                      ("posix", "linux", "systemd")):
        monkeypatch.setattr(cli.sys, "platform", platform)
        monkeypatch.setattr(cli.os, "name", name)
        assert cli._scheduler() == scheduler


@pytest.mark.parametrize("word", ["/opt/ao tool/bin/ao", 'say "hi"', "back\\slash\\", "100%", "%h",
                                  "$HOME and ${PATH} and $$", "tab\tand\nline", ";", "-flag", "café"])
def test_a_word_reaches_the_program_as_it_was_whatever_it_holds(word):
    assert _systemd_words(cli._systemd_word(word)) == [word]
    assert _systemd_words(cli._systemd_word(word, expand=False), expand=False) == [word]
    assert "\n" not in cli._systemd_word(word)


def test_a_description_cannot_end_its_line_or_join_the_next_one():
    assert cli._systemd_text("/w/a\nExecStartPre=/bin/x\\") == "/w/a?ExecStartPre=/bin/x?"
    assert cli._systemd_text("/w/100%h") == "/w/100%%h"


def test_unit_names_escape_what_a_unit_name_cannot_hold_and_keep_two_keys_apart():
    assert cli._systemd_unit("watchdog", "Proj") == "ao-watchdog-proj"
    assert cli._systemd_unit("doctor", "my proj@x") == "ao-doctor-my\\x20proj\\x40x"
    assert cli._systemd_unit("watchdog", "café") == "ao-watchdog-caf\\xc3\\xa9"
    keys = ["a b", "a_b", "a\\x20b", "a\\b", "a-b", "a.b"]
    names = [cli._systemd_unit("watchdog", key) for key in keys]
    assert len(set(names)) == len(keys)
    assert all(re.fullmatch(r"[A-Za-z0-9:_.\\-]+", name) for name in names)


def test_a_long_key_is_cut_to_the_length_systemd_accepts_and_stays_its_own(tmp_path):
    first, second = "é" * 120 + "a", "é" * 120 + "b"
    names = [cli._systemd_unit("watchdog", key) for key in (first, second)]

    assert names[0] != names[1]
    assert all(len(name + ".service") <= cli.SYSTEMD_NAME_MAX for name in names)
    assert all(re.fullmatch(r"ao-watchdog-(\\xc3\\xa9)+-[0-9a-f]{8}", name) for name in names)
