"""No command reaches a shell unless it has to (SHELL-FREE).

A dry watchdog cycle still started a shell for keyflip's budget and read its prose; the
provider window, the tool list, the turn roots, `ao writers`, the process fallbacks and
the launchd jobs each started one too. A shell costs a process on every call and reads
its command as shell text: a project directory named with a space or a `;` reached
launchctl as other words, or ran. Each asks its program by argument vector now and must
read what the shell read. The guard below keeps it so: a call that hands a command line
to a shell is refused anywhere under src/ao but the places listed with their reasons.
"""
import ast
import calendar
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, procs, settings as S, watchdog as W

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---- the guard ------------------------------------------------------------------------------

SHELL_HELPERS = {"sh", "_sh_run"}
SHELL_FUNCTIONS = {("os", "system"), ("os", "popen"), ("subprocess", "getoutput"), ("subprocess", "getstatusoutput")}
# (file, the function the call is in, what starts the shell): why it keeps its shell.
ALLOWED = {
    ("src/ao/lib.py", "sh", "_sh_run()"):
        "sh() is the shell helper; it answers through _sh_run",
    ("src/ao/lib.py", "_sh_run", "shell=True"):
        "the shell helper itself, the one place ao's own code hands a command line to a shell",
    ("src/ao/parts/lib_transcript.py", "_quota_output", "_sh_run()"):
        "an adapter declares its quota command as words a shell ran: one a shell would change, a program "
        "found nowhere, and every command on Windows still run through the shell",
    ("src/ao/parts/cli_authority.py", "cmd_merge_check", "shell=True"):
        "a gate's `run` in .ao/gates.json is a shell command line the project declares, run in the merge result",
    ("src/ao/parts/cli_authority.py", "cmd_verify", "shell=True"):
        "a gate's `run` in .ao/gates.json is a shell command line the project declares",
}


def _starts_a_shell(call):
    func = call.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
    if name in SHELL_HELPERS:
        return f"{name}()"
    owner = func.value if isinstance(func, ast.Attribute) else None
    owner = owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute) else None
    if (owner, name) in SHELL_FUNCTIONS:
        return f"{owner}.{name}()"
    for keyword in call.keywords:
        if keyword.arg == "shell" and not (isinstance(keyword.value, ast.Constant) and not keyword.value.value):
            return "shell=True"
    return None


def _shell_calls(path, rel):
    found = []

    def visit(node, function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                what = _starts_a_shell(child)
                if what:
                    found.append((rel, function, what))
            visit(child, child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function)

    visit(ast.parse(path.read_text(encoding="utf-8"), str(path)), "<module>")
    return found


def test_no_command_reaches_a_shell_outside_the_places_that_must_keep_one():
    found = [call for path in sorted((ROOT / "src" / "ao").rglob("*.py"))
             for call in _shell_calls(path, path.relative_to(ROOT).as_posix())]

    assert sorted(found) == sorted(ALLOWED)
    assert all(reason.strip() for reason in ALLOWED.values())


def test_the_guard_sees_every_way_to_start_a_shell(tmp_path):
    module = tmp_path / "module.py"
    module.write_text(
        "import os, subprocess\n"
        "def a(A):\n    return A.sh('keyflip surfaces')\n"
        "def b():\n    return _sh_run('ps -eo pid')\n"
        "def c(flag):\n    subprocess.Popen('x', shell=flag)\n    subprocess.run(['x'], shell=False)\n"
        "def d():\n    os.system('x')\n    os.popen('x')\n    subprocess.getoutput('x')\n"
        "class E:\n    def f(self):\n        return A.subprocess.getstatusoutput('x')\n",
        encoding="utf-8")

    assert _shell_calls(module, "module.py") == [
        ("module.py", "a", "sh()"), ("module.py", "b", "_sh_run()"), ("module.py", "c", "shell=True"),
        ("module.py", "d", "os.system()"), ("module.py", "d", "os.popen()"), ("module.py", "d", "subprocess.getoutput()"),
        ("module.py", "f", "subprocess.getstatusoutput()")]


# ---- one program, started as the shell started it ------------------------------------------

def test_a_program_is_found_and_run_without_a_shell_as_sh_ran_it(monkeypatch):
    code = "import sys; print(' out ', flush=True); print('err', file=sys.stderr); print(repr(sys.stdin.read())); sys.exit(3)"

    assert A._run_program([sys.executable, "-c", code]) == ("out \n''", 3)
    assert A._run_program([sys.executable, "-c", code], stderr=subprocess.STDOUT)[0] == "out \nerr\n''"
    assert A._run_program(["ao-shell-free-absent-probe", "--version"]) == ("", None)
    assert A._run_program([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5) == ("", None)

    # An npm install on Windows leaves an extensionless POSIX script beside the .cmd.
    monkeypatch.setattr(A, "binary_candidates", lambda name, path=None: ["/npm/keyflip", "/npm/keyflip.cmd"])
    monkeypatch.setattr(A.os, "name", "posix")
    assert A.runnable_binary("keyflip") == "/npm/keyflip"
    monkeypatch.setattr(A.os, "name", "nt")
    assert A.runnable_binary("keyflip") == "/npm/keyflip.cmd"


# ---- keyflip's budget -----------------------------------------------------------------------

# `keyflip budget status --json` as keyflip writes it in each state, with made-up accounts, and what a
# keyflip behind its licence gate, without the flag or absent leaves: (document, exit status).
BUDGETS = {
    "no budget set": (
        '{"schemaVersion":1,"budget":{"defaults":null,"accounts":[],"alerts":[],"breached":false}}', 0),
    "defaults and no account": (
        '{"schemaVersion":1,"budget":{"defaults":{"fiveHourPct":80,"sevenDayPct":null},"accounts":[],'
        '"alerts":[],"breached":false}}', 0),
    "an account inside its budget": (
        '{"schemaVersion":1,"budget":{"defaults":null,"accounts":[{"name":"work","limits":{"fiveHourPct":80,'
        '"sevenDayPct":90},"usage":{"fiveHour":20,"sevenDay":null},"alerts":[]}],"alerts":[],'
        '"breached":false}}', 0),
    "an account near its budget": (
        '{"schemaVersion":1,"budget":{"defaults":null,"accounts":[{"name":"work","limits":{"fiveHourPct":80,'
        '"sevenDayPct":null},"usage":{"fiveHour":75,"sevenDay":10},"alerts":[{"name":"work",'
        '"metric":"fiveHour","pct":75,"limit":80,"breached":false,"level":"warn"}]}],'
        '"alerts":[{"name":"work","metric":"fiveHour","pct":75,"limit":80,"breached":false,"level":"warn"}],'
        '"breached":false}}', 0),
    "an account over its budget": (
        '{"schemaVersion":1,"budget":{"defaults":null,"accounts":[{"name":"work","limits":{"fiveHourPct":80,'
        '"sevenDayPct":null},"usage":{"fiveHour":95,"sevenDay":10},"alerts":[{"name":"work",'
        '"metric":"fiveHour","pct":95,"limit":80,"breached":true,"level":"breach"}]}],'
        '"alerts":[{"name":"work","metric":"fiveHour","pct":95,"limit":80,"breached":true,"level":"breach"}],'
        '"breached":true}}', 0),
    "one of two accounts over its budget": (
        '{"schemaVersion":1,"budget":{"defaults":null,"accounts":[{"name":"home","limits":{"fiveHourPct":80,'
        '"sevenDayPct":null},"usage":{"fiveHour":95,"sevenDay":10},"alerts":[{"name":"home",'
        '"metric":"fiveHour","pct":95,"limit":80,"breached":true,"level":"breach"}]},{"name":"work",'
        '"limits":{"fiveHourPct":80,"sevenDayPct":null},"usage":{"fiveHour":20,"sevenDay":10},"alerts":[]}],'
        '"alerts":[{"name":"home","metric":"fiveHour","pct":95,"limit":80,"breached":true,"level":"breach"}],'
        '"breached":true}}', 0),
    "the licence gate": (
        '{"schemaVersion":1,"error":{"message":"this feature needs the pro plan (keyflip license activate ...)"}}', 1),
    "a keyflip that writes prose": ("No account budgets set — add one:  keyflip budget set <account> --5h 80 --7d 90", 0),
    "keyflip absent": ("", None),
}
BREACHED = {"an account over its budget", "one of two accounts over its budget"}
NOT_BREACHED = {"an account inside its budget", "an account near its budget"}
KEPT = BREACHED | NOT_BREACHED | {"no budget set", "defaults and no account"}


@pytest.mark.parametrize("state", sorted(BUDGETS))
def test_a_breached_budget_blocks_one_not_breached_passes_and_without_one_the_window_decides(project, monkeypatch,
                                                                                         state):
    document, status = BUDGETS[state]
    asked, window = [], {"pct": 10}
    monkeypatch.setattr(A, "_QUOTA", {})
    monkeypatch.setattr(A, "_run_program", lambda argv, cwd=None, **kwargs: asked.append((tuple(argv), cwd)) or
                        (document, status))
    monkeypatch.setattr(A, "quota", lambda adapter, ttl=300: [f"Claude (Anthropic) {window['pct']}%  5h  resets in 1h"])

    open_window = W.quota_ok({})
    window["pct"] = 99
    spent_window = W.quota_ok({})

    # The document says which accounts are breached, not which one is active: a breach on either of two
    # accounts blocks. Without a readable budget the window decides, open or spent.
    expected = (False, False) if state in BREACHED else (True, True) if state in NOT_BREACHED else (True, False)
    assert (open_window, spent_window) == expected
    assert asked[0] == (("keyflip", "budget", "status", "--json"), A.HOME)
    assert len(asked) == (1 if state in KEPT else 2)


def test_the_budget_reading_keeps_the_accounts_and_each_breach(project, monkeypatch):
    breach = {"name": "home", "metric": "fiveHour", "pct": 95, "limit": 80}
    monkeypatch.setattr(A, "_QUOTA", {})
    monkeypatch.setattr(A, "_run_program", lambda argv, cwd=None, **kwargs: BUDGETS["one of two accounts over its budget"])

    assert A.quota_budget({}) == {"accounts": ["home", "work"], "breached": [breach]}
    kept = json.loads((pathlib.Path(A.HOME) / ".ao" / "quota.json").read_text(encoding="utf-8"))
    assert kept["keyflip budget status --json"]["lines"] == ["home", "work"]
    assert kept["keyflip budget status --json"]["breached"] == [breach]


PROBE = r"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.join(sys.argv[1], "src"))
from ao import lib as A
started = []
real_run = subprocess.run
def spy(*args, **kwargs):
    started.append({"argv": list(args[0]), "shell": bool(kwargs.get("shell")), "cwd": kwargs.get("cwd"),
                    "stdin": kwargs.get("stdin") == subprocess.DEVNULL, "stderr": kwargs.get("stderr") == subprocess.DEVNULL})
    return real_run(*args, **kwargs)
subprocess.run = spy
print(json.dumps({"budget": A.quota_budget(json.loads(sys.argv[2])), "started": started}))
"""


def _calls(calls):
    return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def _another_process(home, bindir, adapter):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.update(HOME=str(home), PATH=str(bindir) + os.pathsep + env.get("PATH", ""))
    run = subprocess.run([sys.executable, "-I", "-c", PROBE, str(ROOT), json.dumps(adapter)], capture_output=True,
                         text=True, env=env, timeout=120)
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stdout + run.stderr
    return json.loads(lines[-1])


@pytest.mark.skipif(os.name == "nt", reason="the fake keyflip is a POSIX shell script")
def test_a_budget_reading_is_kept_across_processes_for_the_quota_window(tmp_path):
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    calls = tmp_path / "keyflip-calls.txt"
    tool = bindir / "keyflip"
    tool.write_text(f'#!/bin/sh\necho "$(pwd -P) $*" >> {shlex.quote(str(calls))}\n'
                    f'echo {shlex.quote(BUDGETS["an account over its budget"][0])}\n', encoding="utf-8")
    tool.chmod(0o755)
    adapter = {"telemetry": {"quota": {"argv": ["ao-quota-probe"], "cache_seconds": 300}}}

    first = _another_process(home, bindir, adapter)
    second = _another_process(home, bindir, adapter)

    assert first["budget"] == second["budget"] == {
        "accounts": ["work"], "breached": [{"name": "work", "metric": "fiveHour", "pct": 95, "limit": 80}]}
    assert first["started"] == [{"argv": [str(tool), "budget", "status", "--json"], "shell": False, "cwd": str(home),
                                 "stdin": True, "stderr": True}]
    assert second["started"] == [] and _calls(calls) == [f"{os.path.realpath(home)} budget status --json"]

    kept_file = home / ".ao" / "quota.json"
    kept = json.loads(kept_file.read_text(encoding="utf-8"))
    assert kept["keyflip budget status --json"]["lines"] == ["work"]
    assert kept["keyflip budget status --json"]["breached"] == [{"name": "work", "metric": "fiveHour", "pct": 95, "limit": 80}]
    kept["keyflip budget status --json"]["at"] -= 301
    kept_file.write_text(json.dumps(kept), encoding="utf-8")

    assert _another_process(home, bindir, adapter)["budget"]["accounts"] == ["work"] and len(_calls(calls)) == 2


# ---- the provider window ----------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="the fake keyflip is a POSIX shell script")
def test_a_window_is_read_from_the_kept_quota_reading_and_read_again_after_a_rotation(project, tmp_path, monkeypatch):
    with open(S.machine_path(), "w", encoding="utf-8") as fh:
        json.dump({"keyflip": {"rotation": "on"}}, fh)
    bindir, calls, rotated = tmp_path / "bin", tmp_path / "keyflip-calls.txt", tmp_path / "rotated"
    bindir.mkdir()
    (bindir / "keyflip").write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {shlex.quote(str(calls))}\n'
        'case "$1" in\n'
        f"  next) echo 20 > {shlex.quote(str(rotated))} ;;\n"
        '  usage) echo "Provider usage (other AI tools on this machine):"\n'
        f'         echo "  Claude (Anthropic) $(cat {shlex.quote(str(rotated))} 2>/dev/null || echo 99)%  5h  resets in 2h 13m" ;;\n'
        "esac\n", encoding="utf-8")
    (bindir / "keyflip").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(A, "_QUOTA", {})
    command = " ".join(A._window_adapter()["telemetry"]["quota"]["argv"])

    assert A.provider_window("claude")["pct"] == 99
    assert A.provider_window("claude")["raw"] == "Claude (Anthropic) 99%  5h  resets in 2h 13m"
    assert _calls(calls) == ["usage --providers"]            # the panel's reading, kept, read twice

    headroom = A.rotate_if_exhausted(project, ["claude", "-p", "x"], "reviewer")

    assert headroom["text"] == "rotated through keyflip for the reviewer: claude now 20% used"
    assert _calls(calls) == ["usage --providers", "next --strategy best", "usage --providers"]
    kept = json.loads((pathlib.Path(A.HOME) / ".ao" / "quota.json").read_text(encoding="utf-8"))
    assert kept[command]["lines"] == ["Claude (Anthropic) 20%  5h  resets in 2h 13m"]


# ---- turns, writers and the process table --------------------------------------------------------

def _no_process(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail(f"a process was started: {args[0]}"))


def test_turn_roots_come_from_the_process_table(monkeypatch):
    table = {20: (1, 20, "??"), 21: (20, 20, "??"), 22: (21, 20, "??"), 30: (1, 30, "ttys001"), 31: (30, 30, "ttys001")}
    monkeypatch.setattr(A, "_proc_table", lambda: table)
    _no_process(monkeypatch)

    assert A.process_trees([20, 21, 22, 31]) == [20, 31]
    assert A.process_trees([21, 22, 4242]) == [21, 4242]         # a pid the table lacks has no known parent
    assert A.process_trees([]) == []


def test_a_writer_row_holds_the_elapsed_time_and_the_command_as_ps_printed_them(project, monkeypatch, capsys):
    argv = ["/agents/kiro-cli", "chat", "--no-interactive", "Continue.\n\nRules:\tone writer\x1b"]
    monkeypatch.setattr(A, "writers", lambda root, adapter: ([4242], []))
    monkeypatch.setattr(A, "unplaced_agent_pids", lambda root, adapter: [])
    monkeypatch.setattr(A, "_proc_table", lambda: {4242: (1, 4242, "??")})
    monkeypatch.setattr(A, "_is_headless", lambda pid: True)
    monkeypatch.setattr(procs, "elapsed", lambda pid: 90061 if pid == 4242 else None)
    monkeypatch.setattr(procs, "argv", lambda pid: argv if pid == 4242 else None)
    _no_process(monkeypatch)

    assert cli.cmd_writers(project, SimpleNamespace(clean=False, json=True)) == 0
    [turn] = json.loads(capsys.readouterr().out)["turns"]

    darwin = sys.platform == "darwin"
    assert turn["elapsed"] == ("01-01:01:01" if darwin else "1-01:01:01")
    assert turn["cmd"] == "/agents/kiro-cli chat --no-interactive " + (
        "Continue.\\012\\012Rules:\\011one writer^[" if darwin else "Continue.??Rules:?one writer?")


@pytest.mark.parametrize("seconds,darwin,elsewhere", [
    (0, "00:00", "00:00"), (59, "00:59", "00:59"), (3599, "59:59", "59:59"), (3600, "01:00:00", "01:00:00"),
    (86399, "23:59:59", "23:59:59"), (86400, "01-00:00:00", "1-00:00:00"), (100 * 86400 + 61, "100-00:01:01", "100-00:01:01")])
def test_elapsed_time_is_written_as_the_platforms_ps_writes_it(monkeypatch, seconds, darwin, elsewhere):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli._etime(seconds) == darwin
    monkeypatch.setattr(cli.sys, "platform", "linux")
    assert cli._etime(seconds) == elsewhere


def test_a_process_is_known_by_how_long_it_has_run():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        deadline = time.time() + 30
        while time.time() < deadline and (procs.elapsed(child.pid) or 0) < 2:
            time.sleep(0.25)
            procs.refresh()
        assert 2 <= procs.elapsed(child.pid) <= 60
    finally:
        child.kill()
        child.wait()
    procs.refresh()
    assert procs.elapsed(child.pid) is None


def test_the_readings_of_a_start_that_are_text_are_read_whole():
    assert procs._clock_seconds("  05:07\n") == 307
    assert procs._clock_seconds("01:02:03") == 3723
    assert procs._clock_seconds("07-01:02:03") == 7 * 86400 + 3723
    assert procs._clock_seconds("") is None and procs._clock_seconds("?") is None
    assert procs._cim_epoch("/Date(1757178612000)/") == 1757178612
    assert procs._cim_epoch("20260906183012.123456+180") == calendar.timegm((2026, 9, 6, 15, 30, 12, 0, 0, 0))
    assert procs._cim_epoch("20260906183012.123456-060") == calendar.timegm((2026, 9, 6, 19, 30, 12, 0, 0, 0))
    assert procs._cim_epoch(None) is None and procs._cim_epoch("yesterday") is None


@pytest.mark.skipif(os.name == "nt", reason="ps and lsof are the POSIX fallbacks; Windows reads CIM")
def test_the_ps_and_lsof_fallbacks_start_no_shell(monkeypatch):
    started, real_run = [], subprocess.run

    def run(argv, **kwargs):
        started.append((argv, kwargs))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    fallback, me = procs._Shell(), os.getpid()

    assert me in fallback.all_pids()
    assert fallback.info(me)["ppid"] == os.getppid() and fallback.argv(me)
    assert abs(fallback.elapsed(me) - procs.elapsed(me)) <= 2
    fallback.cwd(me)
    procs.group_cpu_seconds(os.getpgid(0))

    assert started and all(isinstance(argv, list) and os.path.isabs(argv[0]) and not kwargs.get("shell")
                           and kwargs.get("stdin") == subprocess.DEVNULL for argv, kwargs in started)
    assert {os.path.basename(argv[0]) for argv, _ in started} <= {"ps", "lsof"}


def test_the_windows_process_table_asks_powershell_by_argument_vector(monkeypatch):
    asked = []
    snapshot = json.dumps([{"ProcessId": 42, "ParentProcessId": 1, "CommandLine": "C:\\x\\a.exe -p", "Name": "a.exe",
                            "SessionId": 0, "CreationDate": "/Date(1757178612000)/"}])
    monkeypatch.setattr(procs, "_run", lambda argv: asked.append(argv) or snapshot)
    backend = procs._Windows()
    backend.invalidate()

    assert backend.argv(42) == ["C:\\x\\a.exe", "-p"]
    assert asked == [["powershell", "-NoProfile", "-NonInteractive", "-Command",
                      "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,"
                      "ExecutablePath,Name,SessionId,CreationDate | ConvertTo-Json -Compress"]]
    before = int(time.time() - 1757178612)
    elapsed = backend.elapsed(42)
    assert before <= elapsed <= int(time.time() - 1757178612)
    assert backend.elapsed(43) is None and len(asked) == 1          # both read the snapshot already taken


# ---- launchd ----------------------------------------------------------------------------------

def _launchd(monkeypatch):
    """A launchd that loads what is bootstrapped and answers `launchctl list`, asked through _run_program."""
    loaded, asked = set(), []

    def run(argv, cwd=None, timeout=20, stderr=subprocess.DEVNULL):
        asked.append((list(argv), stderr))
        assert argv[0] == "launchctl", argv
        if argv[1] == "list":
            return "\n".join(["PID\tStatus\tLabel"] + [f"-\t0\t{label}" for label in sorted(loaded)]), 0
        if argv[1] == "bootout":
            label = argv[2].split("/", 2)[2]
            if label in loaded:
                loaded.discard(label)
                return "", 0
            return ("Boot-out failed: 3: No such process" if stderr == subprocess.STDOUT else ""), 3
        if argv[1] == "bootstrap":
            loaded.add(os.path.basename(argv[3])[:-len(".plist")])
            return "", 0
        assert argv[1] == "unload", argv
        return "", 0

    monkeypatch.setattr(A, "_run_program", run)
    monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
    return loaded, asked


def test_launchctl_is_asked_by_argument_vector_and_its_listing_read_as_grep_read_it(monkeypatch):
    loaded, asked = _launchd(monkeypatch)
    loaded.update({"com.agentorchestrator.watchdog.proj", "com.agentorchestrator.watchdog.proj-api",
                   "com.agentorchestrator.doctor.proj"})

    assert cli._launchd_domain() == "gui/501" and cli._launchd_domain("a.b") == "gui/501/a.b"
    assert cli._launchd_listed("com.agentorchestrator.watchdog.proj").split("\n") == [
        "-\t0\tcom.agentorchestrator.watchdog.proj", "-\t0\tcom.agentorchestrator.watchdog.proj-api"]
    assert cli._launchd_listed("com.agentorchestrator.watchdog.gone") == ""
    assert cli._launchctl("bootout", "gui/501/a b;c")[1] == 3
    assert cli._launchctl("bootstrap", "gui/501", "/Library Agents/x.plist", merge=True) == ("", 0)
    assert asked[-2:] == [(["launchctl", "bootout", "gui/501/a b;c"], subprocess.DEVNULL),
                          (["launchctl", "bootstrap", "gui/501", "/Library Agents/x.plist"], subprocess.STDOUT)]


@pytest.mark.skipif(os.name == "nt", reason="launchd is macOS's; Windows schedules the watchdog with Task Scheduler")
def test_the_watchdog_job_is_installed_read_and_removed_by_argument_vector(project, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))                   # the plists land here, not in a real LaunchAgents
    loaded, asked = _launchd(monkeypatch)
    root, key = project["root"], A.project_key(project["root"]).lower()
    label, dlabel = f"com.agentorchestrator.watchdog.{key}", f"com.agentorchestrator.doctor.{key}"
    agents = home / "Library" / "LaunchAgents"

    def watchdog(action):
        return cli.cmd_watchdog(project, SimpleNamespace(action=action, idle_minutes=6, interval=120))

    watchdog("install")
    assert loaded == {label, dlabel} and (agents / f"{label}.plist").exists() and (agents / f"{dlabel}.plist").exists()
    words = [argv for argv, _ in asked]
    assert words.index(["launchctl", "bootout", f"gui/501/{label}"]) < \
        words.index(["launchctl", "bootstrap", "gui/501", str(agents / f"{label}.plist")])
    assert ["launchctl", "bootstrap", "gui/501", str(agents / f"{dlabel}.plist")] in words
    assert all((stderr == subprocess.STDOUT) == (argv[1] == "bootstrap") for argv, stderr in asked)
    assert f"installed {label}" in capsys.readouterr().out

    (home / ".ao" / f"watchdog-{key}.log").write_bytes(b"cycle 1\r\ncycle 2\ncycle 3\r\ncycle 4\ncycle 5\ncycle 6\ncycle 7")
    watchdog("status")
    out = capsys.readouterr().out
    assert f"loaded  -\t0\t{label}" in out and "doctor  loaded" in out
    assert out.rstrip().endswith("cycle 3\ncycle 4\ncycle 5\ncycle 6\ncycle 7")

    asked.clear()
    watchdog("uninstall")
    assert loaded == set() and not (agents / f"{label}.plist").exists() and not (agents / f"{dlabel}.plist").exists()
    assert [argv for argv, _ in asked] == [["launchctl", "bootout", f"gui/501/{label}"],
                                          ["launchctl", "bootout", f"gui/501/{dlabel}"]]
    asked.clear()
    watchdog("uninstall")                                   # nothing loaded: bootout fails, unload is asked
    assert [argv for argv, _ in asked][:2] == [["launchctl", "bootout", f"gui/501/{label}"],
                                              ["launchctl", "unload", str(agents / f"{label}.plist")]]


@pytest.mark.skipif(os.name == "nt", reason="the telegram poller is a launchd job; Windows refuses it")
def test_the_telegram_poller_is_read_and_removed_by_argument_vector(project, monkeypatch, capsys):
    from ao import telegram
    loaded, asked = _launchd(monkeypatch)
    monkeypatch.setattr(telegram, "config", lambda: None)
    label = f"com.agentorchestrator.telegram.{A.project_key(project['root']).lower()}"

    assert cli.cmd_telegram(project, SimpleNamespace(action="status", once=False)) == 0
    assert "not installed" in capsys.readouterr().out
    loaded.add(label)
    assert cli.cmd_telegram(project, SimpleNamespace(action="status", once=False)) == 0
    assert "running" in capsys.readouterr().out
    assert cli.cmd_telegram(project, SimpleNamespace(action="uninstall", once=False)) == 0
    assert loaded == set() and asked[-1] == (["launchctl", "bootout", f"gui/501/{label}"], subprocess.DEVNULL)


def test_a_logs_last_lines_are_read_as_tail_printed_them(tmp_path):
    log = tmp_path / "watchdog.log"
    log.write_bytes(b"a\n" + b"y" * 70000 + b"\nb\r\nc\n")
    assert cli._log_tail(str(log)) == "a\n" + "y" * 70000 + "\nb\nc"
    log.write_bytes(b"1\n2\n3\n4\n5\n6\n7\n\n")
    assert cli._log_tail(str(log)) == "4\n5\n6\n7"
    log.write_bytes(b"\xff bad\n")
    assert cli._log_tail(str(log)) == "\ufffd bad"
    assert cli._log_tail(str(tmp_path / "missing.log")) == ""
