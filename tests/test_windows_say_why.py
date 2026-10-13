"""What ao cannot hold on Windows it holds another way there, or says so (WINDOWS-SAY-WHY).

Several guarantees were made with what Windows does not have, and on Windows they quietly did not hold.
The flag that opens a subagent's file without following a link was 0 there, and a link was followed. A
file ao keeps private was given mode 0600, which Windows ignores, and ao said "(0600)". An ended turn's
leftovers are found by their process group, which Windows has none of, so none was ever found, and
nothing said none could be. The reviewer stall check reads a process group's CPU, and on Windows it never
fired. schtasks writes in the OEM code page and was read as UTF-8. A grant of `py`, `powershell` or `cmd`
was asked about none of the ways each runs a line of code. A silent watchdog was to be looked into with
launchctl, on every platform.

Where the platform has no O_NOFOLLOW the path is now looked at before it is opened - a link is refused -
and the file opened must be the one looked at. schtasks is read in the OEM code page. The grant audit asks
`py -c`, `powershell -Command`, `cmd /c` and their kin as it asks `python -c`. The hints name each
platform's own scheduler. What cannot be held is said where it bears - `ao writers`, a reviewer's wait,
the setup of a channel and the file it saved - and all of it in `ao doctor`. Windows is made here on every
platform.
"""
import json
import os
import re
import sys
import time
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, email, language, lib as A, procs, telegram
from tests.scenarios import World

# What a Turkish Windows's schtasks says of a task it cannot find, in its own words.
REFUSAL = "HATA: Sistem belirtilen dosyayı bulamıyor."
SAVED_ON_WINDOWS = "no file mode on Windows: as private as its folder"


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _as_windows(monkeypatch, ask):
    """What `ask` returns where os.name is Windows' - only while it asks, so a failure is reported in this
    platform's paths."""
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        return ask()


# ---- a file read without following a link, where the platform has no O_NOFOLLOW --------------------------

@pytest.fixture
def no_nofollow(monkeypatch):
    """A platform without O_NOFOLLOW, as Windows is."""
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)


def _transcript(path):
    path.write_text(json.dumps({"type": "user"}) + "\n", encoding="utf-8")
    return path


def test_a_regular_file_is_read_where_the_platform_has_no_o_nofollow(no_nofollow, tmp_path):
    assert A.read_tail(str(_transcript(tmp_path / "agent.jsonl")), regular=True) == [{"type": "user"}]


def test_a_link_is_refused_where_the_platform_has_no_o_nofollow(no_nofollow, tmp_path):
    link = tmp_path / "agent.jsonl"
    try:
        os.symlink(str(_transcript(tmp_path / "elsewhere.jsonl")), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symbolic link")

    with pytest.raises(OSError, match="is a link"):
        A._open_regular(str(link))
    assert A.read_tail(str(link), regular=True) == []


def test_a_file_swapped_as_it_was_opened_is_refused(no_nofollow, tmp_path, monkeypatch):
    # The path was looked at as one file and opened as another: what a link swapped in between makes.
    looked, opened = _transcript(tmp_path / "looked.jsonl"), _transcript(tmp_path / "opened.jsonl")
    seen, lstat = os.lstat(str(looked)), os.lstat
    monkeypatch.setattr(os, "lstat", lambda path, **kw: seen if str(path) == str(opened) else lstat(path, **kw))

    with pytest.raises(OSError, match="changed as it was opened"):
        A._open_regular(str(opened))


# ---- schtasks, read in the OEM code page ---------------------------------------------------------------

def test_what_schtasks_says_is_read_in_the_oem_code_page(tmp_path, monkeypatch):
    system = tmp_path / "Windows" / "System32"
    system.mkdir(parents=True)
    (system / "schtasks.exe").write_bytes(b"MZ")
    monkeypatch.setenv("SystemRoot", str(system.parent))
    said = [REFUSAL.encode("cp857"), REFUSAL.encode("utf-8")]          # its OEM code page, and after `chcp 65001`
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=1, stdout=b"",
                                                                                  stderr=said.pop(0)))
    monkeypatch.setattr(procs, "oem_encoding", lambda: "cp857")       # a Turkish Windows

    answers = [_as_windows(monkeypatch, lambda: cli._schtasks("/Query", "/TN", "ao-watchdog-proj")) for _ in "12"]

    assert answers == [(REFUSAL, 1), (REFUSAL, 1)]


def test_the_oem_code_page_is_windows_own_to_say():
    if sys.platform == "win32":
        assert re.fullmatch(r"cp\d+", procs.oem_encoding() or "")
    else:
        assert procs.oem_encoding() is None


# ---- the grant audit asks what Windows' own interpreters run ------------------------------------------------

def test_a_grant_of_windows_own_interpreters_is_asked_what_python_dash_c_is():
    """Each rule alone: a rule another rule's finding already covers is named once, for the first command it admits."""
    for rule, command in (("Bash(py:*)", "py -c x"), ("Bash(powershell:*)", "powershell -Command x"),
                          ("Bash(pwsh -c:*)", "pwsh -c x"), ("Bash(cmd:*)", "cmd /c x"),
                          ("Bash(cmd.exe /c:*)", "cmd /c x")):
        found = AL.problems(["claude", "-p", "{prompt}", "--allowedTools", f"Read,{rule}"])
        assert found and found[0][0] == "runs arbitrary code" and found[0][2] == rule, (rule, found)
        assert AL.admits(rule, command)
    assert AL.problems(["claude", "-p", "{prompt}", "--allowedTools", "Read,Bash(py --version)"]) == []


# ---- a silent watchdog, looked into with this platform's scheduler -------------------------------------------

@pytest.mark.parametrize("name, platform, tool", [("nt", "win32", "Task Scheduler"),
                                                  ("posix", "linux", "systemctl --user"),
                                                  ("posix", "darwin", "launchctl")])
def test_a_silent_watchdog_is_looked_into_with_its_platforms_own_scheduler(monkeypatch, name, platform, tool):
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", name)
        patch.setattr(sys, "platform", platform)
        assert A.scheduler_hint() == tool


def test_the_doctor_and_a_sibling_watchdog_name_it(project, monkeypatch, tmp_path):
    monkeypatch.setattr(A, "scheduler_hint", lambda: "Task Scheduler")
    monkeypatch.setattr(A, "heartbeat_age", lambda root: 3600)

    assert dict(cli.doctor_problems(project))["watchdog-dead"] == ("watchdog silent for 60m — Task Scheduler / "
                                                                  "ao watchdog status")

    world = World(project, monkeypatch, tmp_path)
    sibling = os.path.join(A.HOME, ".ao", "heartbeat-other")
    os.makedirs(os.path.dirname(sibling), exist_ok=True)
    with open(sibling, "w", encoding="utf-8") as fh:
        fh.write("1")
    os.utime(sibling, (time.time() - 1200, time.time() - 1200))
    world.cycle()
    assert [message for title, message, _, _ in world.notices if title == "other: watchdog silent"] == [
        "no heartbeat for 20m — its watchdog is not running; Task Scheduler / ao watchdog status"]


# ---- what cannot be held, said where it bears ----------------------------------------------------------------

def test_writers_says_on_windows_that_an_ended_turns_leftovers_are_not_told_apart(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "writers", lambda root, adapter: ([4242, 4343], []))
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root, adapter: [])
    monkeypatch.setattr(A, "_proc_table", lambda: {})
    monkeypatch.setattr(A, "_is_headless", lambda pid: True)
    monkeypatch.setattr(procs, "elapsed", lambda pid: None)
    monkeypatch.setattr(procs, "argv", lambda pid: ["kiro-cli", "chat"])

    assert _as_windows(monkeypatch, lambda: cli.cmd_writers(project, SimpleNamespace(clean=False, json=False))) == 1
    assert f"orphans not told apart: {cli.WINDOWS_LIMITS['orphans']}" in _plain(capsys)
    assert _as_windows(monkeypatch, lambda: cli.cmd_writers(project, SimpleNamespace(clean=False, json=True))) == 1
    told = json.loads(capsys.readouterr().out)
    assert (told["writers"], told["orphans"], told["orphans_unknown"]) == (2, None, cli.WINDOWS_LIMITS["orphans"])
    if os.name != "nt":
        assert cli.cmd_writers(project, SimpleNamespace(clean=False, json=True)) == 1
        assert json.loads(capsys.readouterr().out)["orphans"] == []           # elsewhere they are found


class _OsOf:
    """The os module as one platform names itself, for the watchdog alone: every other module reads its own."""

    def __init__(self, name):
        self.name = name

    def __getattr__(self, attribute):
        return getattr(os, attribute)


def test_a_cycle_on_windows_records_the_orphans_it_could_not_look_for_as_unknown(project, monkeypatch, tmp_path):
    from ao import watchdog as W
    world = World(project, monkeypatch, tmp_path)
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)

    world.cycle()
    # Counted where the platform can look for them; on a Windows runner the first cycle cannot either.
    assert W._FACTS["orphans"] == (None if os.name == "nt" else 0)
    monkeypatch.setattr(W, "os", _OsOf("nt"))
    world.cycle()
    assert W._FACTS["orphans"] is None


def test_a_reviewer_waited_on_on_windows_is_told_the_stall_check_does_not_apply(monkeypatch, capsys):
    reviewer = SimpleNamespace(pid=4242, args=["reviewer"], communicate=lambda timeout=None: ("VERDICT: APPROVED", ""))

    answer = _as_windows(monkeypatch, lambda: cli._reviewer_communicate(reviewer, 900, "r1", time.monotonic(),
                                                                        stall=600))

    assert answer == ("VERDICT: APPROVED", "")
    assert _plain(capsys).strip() == f"reviewer r1: {cli.WINDOWS_LIMITS['stall check']}"
    if os.name != "nt":
        cli._reviewer_communicate(reviewer, 900, "r1", time.monotonic(), stall=600)
        assert _plain(capsys) == ""                                           # elsewhere it applies


def test_a_file_ao_keeps_private_is_said_to_be_as_private_as_its_folder_on_windows(project, tmp_path, monkeypatch,
                                                                                   capsys):
    A.project_key(project["root"])                     # registered first, as every test that locks under "nt"
    monkeypatch.setattr(email, "CONF", str(tmp_path / "email.json"))

    asked = SimpleNamespace(action="setup", provider=None, token=None, to=None)
    assert _as_windows(monkeypatch, lambda: cli.cmd_email(project, asked)) == 0
    steps = _plain(capsys)
    assert (f"The token stays in {email.CONF}, as private as its folder's permissions (Windows keeps no file mode), "
            "and is never written to a repository.") in steps and "0600" not in steps
    saved = SimpleNamespace(action="setup", provider=None, token="t", to="me@example.com")
    assert _as_windows(monkeypatch, lambda: cli.cmd_email(project, saved)) == 0
    assert f"saved {email.CONF} ({SAVED_ON_WINDOWS}) → me@example.com" in _plain(capsys)
    pinged = SimpleNamespace(action="setup", url="https://hc-ping.com/x", all=False)
    assert _as_windows(monkeypatch, lambda: cli.cmd_pings(project, pinged)) == 0
    assert f"saved {A.pings_path()} ({SAVED_ON_WINDOWS})" in _plain(capsys)


def test_the_telegram_setup_on_windows_says_what_windows_runs_and_how_the_file_is_kept(project, monkeypatch,
                                                                                       capsys):
    A.project_key(project["root"])
    monkeypatch.setattr(telegram, "config", lambda: None)

    assert _as_windows(monkeypatch, lambda: cli.cmd_telegram(project, SimpleNamespace(action="setup",
                                                                                      once=False))) == 0

    out = _plain(capsys)
    assert language.text(None, "private-file.kept-windows") in out and language.text(None, "private-file.windows") in out
    assert "4. ao telegram test → ao telegram poll" in out
    # ao asks for the token and writes the file itself: no command line holds the token, PowerShell's neither, since
    # its history file kept it as a shell's does (LEADS-SECRETS).
    for posix in ("Invoke-RestMethod", "curl", "python3 -c", "mkdir -p", "chmod 600", "echo '", "ao telegram install"):
        assert posix not in out


def test_the_doctor_names_what_ao_cannot_hold_on_windows_and_why(project, monkeypatch, capsys):
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in _as_windows(monkeypatch, cli._windows_limit_lines)]

    assert lines == [f"{'on Windows':<16}what ao cannot hold here, and why"] + [
        f"{'':<16}{name}: {text}" for name, text in cli.WINDOWS_LIMITS.items()]
    if os.name != "nt":
        assert cli._windows_limit_lines() == []                              # elsewhere there is nothing to say
    monkeypatch.setattr(cli, "_windows_limit_lines", lambda: ["what ao cannot hold"])
    monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)  # no agent CLI is found, or started
    cli.cmd_doctor(project, SimpleNamespace(check=False))
    assert "what ao cannot hold" in _plain(capsys).split("\n")



@pytest.mark.parametrize("rule", ["Bash(powershell.exe -c:*)", "Bash(cmd.exe //c:*)", "Bash(py.exe -c:*)",
                                  "Bash(PYTHON.EXE -c:*)", "Bash(python -m:*)", "Bash(py -m:*)"])
def test_a_program_spelt_as_windows_starts_it_is_asked_as_any_other(rule):
    """From the review of WINDOWS-SAY-WHY: each forbidden command was one spelling, and a rule naming the program with its
    .exe, or in another case, admitted it unnamed; and `python -m` runs any module there is."""
    from ao import allowlist as AL
    found = AL.problems(["claude", "-p", "{prompt}", "--allowedTools", f"Read,{rule}"], {})

    assert any(reason.startswith("runs arbitrary code") for reason, _, _ in found), found


def test_a_file_system_that_numbers_no_file_is_read_as_changed(monkeypatch, tmp_path):
    """From the review of WINDOWS-SAY-WHY: with no O_NOFOLLOW, a file id of 0 - FAT - passed the check it cannot make."""
    path = tmp_path / "t.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    real = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p: os.stat_result((real(p).st_mode, 0) + tuple(real(p))[2:]))

    with pytest.raises(OSError, match="cannot say it did not"):
        A._open_regular(str(path))



@pytest.mark.parametrize("rule", ["Bash(python3 -O -c:*)", "Bash(\"powershell.exe\" -c:*)", "Bash(py.exe:*)",
                                  "Bash(node --eval:*)", "Bash(/usr/bin/python3:*)",
                                  "Bash(\"C:\\Program Files\\Python312\\python.exe\":*)"])
def test_a_grant_of_an_interpreter_is_named_whatever_its_rule_fixes(rule):
    """From the second review of WINDOWS-SAY-WHY: each spelling was a sample, and a rule fixing other arguments, or
    naming its program quoted or without a space before `:*`, passed. An interpreter is read as a closed world now."""
    from ao import allowlist as AL
    found = AL.problems(["claude", "-p", "{prompt}", "--allowedTools", f"Read,{rule}"], {})

    assert any(reason == "runs arbitrary code" and granted == rule for reason, _, granted in found), found


def test_an_interpreter_asked_only_its_version_is_no_grant_of_code():
    from ao import allowlist as AL
    assert AL.problems(["claude", "-p", "{prompt}", "--allowedTools", "Read,Bash(python3 --version)"], {}) == []
