"""A gate run, `ao lock` and a lane start read the machine first, and wait or refuse rather than thrash
(MACHINE-PRESSURE).

Five test suites at once swap a laptop into uselessness while each looks reasonable, and the machine then appears
hung. The gate lock kept ao's own suites apart and nothing else: whatever else loaded the machine, `ao verify` started
its suite into it, and `ao lane start` made a lane whatever the load. Now memory free, swap-ins a second and disk free
on the home volume are read once the lock is held - for a lane, before anything is made - each against a machine
setting; a run waits up to --wait while one is past its limit, then refuses, naming what it read, the limit and what
to do. The numbers are those measured on a working machine: the rate is the lower median of four one-second samples,
so a burst of 96, 185, 237 and 1482 passes, and swap used, which macOS never hands back, is never read. `ao lane start`
also refuses a lane past limits.max_write_lanes. Nothing here reads the real machine: captured outputs and stand-in
readings only.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, pressure as P, settings as S
from tests.conftest import CALM_MACHINE

# What the programs print and the files hold, as captured on a Mac and on a Linux machine, the numbers changed.
MEMORY_PRESSURE = """The system has 17179869184 (1048576 pages with a page size of 16384).

Stats:
Pages free: 4026
Pages purgeable: 3125
Pages purged: 1203482

Swap I/O:
Swapins: 2286632
Swapouts: 2900283

Page Q counts:
Pages active: 287519
Pages inactive: 283436
Pages speculative: 1530
Pages throttled: 0
Pages wired down: 147421

Compressor Stats:
Pages used by compressor: 309437
Pages decompressed: 59283612
Pages compressed: 74316893

File I/O:
Pageins: 25640218
Pageouts: 371264

System-wide memory free percentage: 47%
"""

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                3975.
Pages active:                            287448.
Pages inactive:                          283436.
Pages speculative:                         1530.
Pages throttled:                              0.
Pages wired down:                        147421.
Pages purgeable:                           3125.
"Translation faults":                1876154722.
Pages copy-on-write:                   61234567.
Pages zero filled:                    987654321.
Pages reactivated:                     12345678.
Pages purged:                           1203482.
File-backed pages:                       123456.
Anonymous pages:                         449028.
Pages stored in compressor:             1234567.
Pages occupied by compressor:            309437.
Decompressions:                        59283612.
Compressions:                          74316893.
Pageins:                               25640218.
Pageouts:                                371264.
Swapins:                                {swapins}.
Swapouts:                              99999999.
"""

MEMINFO = """MemTotal:       16314424 kB
MemFree:          813780 kB
MemAvailable:    9788654 kB
Buffers:          321456 kB
Cached:          8123456 kB
SwapCached:        12345 kB
Active:          7654321 kB
Inactive:        5432109 kB
SwapTotal:       2097148 kB
SwapFree:              0 kB
Dirty:              1234 kB
"""

VMSTAT = """nr_free_pages 203445
nr_zone_inactive_anon 123456
nr_zone_active_anon 654321
pgpgin 98765432
pgpgout 12345678
pswpin 1234567
pswpout 76543210
pgfault 1928374655
"""

BOARD = """# Board

## running
- [R] already being worked

## blocked

## queued
- [B] ready to start
- [D] ready too
- [E] ready as well

## verified

## done
"""


def _plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _reading(memory=60.0, paging=0, disk=500.0, unmeasured=()):
    return P.Reading(memory, paging, () if paging is None else (paging,) * P.SAMPLES, disk, tuple(unmeasured))


class _Clock:
    """pressure's time module, except that sleeping moves this clock on instead of waiting."""

    def __init__(self):
        self.start = self.now = 1000.0
        self.slept = 0

    def __getattr__(self, name):
        import time
        return getattr(time, name)

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept += seconds
        self.now += seconds

    @property
    def elapsed(self):
        return self.now - self.start


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(P, "time", clock)
    return clock


@pytest.fixture
def lock(tmp_path, monkeypatch):
    """The machine gate lock, in this test's directory instead of the machine's."""
    path = tmp_path / "gate.lock"
    monkeypatch.setattr(A, "GATE_LOCK", str(path))
    return path


def _machine(monkeypatch, clock, *readings):
    """The machine read as `readings`, in turn, the last for every reading after it; each reading takes four seconds
    of `clock`, as sampling paging does. Returns the readings taken, as they are taken."""
    taken = []

    def read():
        taken.append(readings[min(len(taken), len(readings) - 1)])
        if clock is not None:
            clock.now += P.SAMPLES * P.SAMPLE_SECONDS
        return taken[-1]

    monkeypatch.setattr(P, "read", read)
    return taken


def _counter(start, rises):
    """A swap-in counter that reads `start`, then rises by each of `rises` in turn."""
    values = [start]
    for rise in rises:
        values.append(values[-1] + rise)
    it = iter(values)
    return lambda: (next(it), None)


def _machine_limits(**limits):
    """Machine settings, in the file this test's ~/.ao/settings.json stands for."""
    with open(os.environ["AO_SETTINGS"], "w", encoding="utf-8") as fh:
        json.dump({"limits": limits}, fh)


def _gates(root):
    """One gate that passes, in a quick and a full profile."""
    argv = [sys.executable, "-c", "print('gate ok')"]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    spec = {"gates": {"smoke": {"run": command, "expect": "exit_zero", "timeout": 60}},
            "profiles": {"quick": ["smoke"], "full": ["smoke"]}, "default_profile": "quick"}
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)


def _git(root, *args):
    return subprocess.run([A.git_binary(), "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _programs(monkeypatch, outputs):
    """Stand-ins for the programs ao asks on macOS: {name: [what it prints each time it is asked]}; a program not
    named is not on PATH. Returns the names asked, in order."""
    asked = []
    printed = {name: iter(texts) for name, texts in outputs.items()}
    monkeypatch.setattr(A, "program_path", lambda name, path=None: f"/stand-in/{name}" if name in outputs else None)

    def run(argv, **kwargs):
        name = os.path.basename(argv[0])
        asked.append(name)
        return subprocess.CompletedProcess(argv, 0, stdout=next(printed[name]), stderr="")

    monkeypatch.setattr(P, "subprocess", SimpleNamespace(run=run, DEVNULL=subprocess.DEVNULL,
                                                         TimeoutExpired=subprocess.TimeoutExpired))
    return asked


def _volume(monkeypatch, gib):
    """os.statvfs answering `gib` GiB free for any path; returns the paths it was asked about."""
    asked = []

    def statvfs(path):
        asked.append(path)
        return SimpleNamespace(f_bavail=int(gib * (1 << 30)) // 4096, f_frsize=4096)

    monkeypatch.setattr(os, "statvfs", statvfs, raising=False)
    return asked


# ---- the reading -----------------------------------------------------------------------------------------------

def test_the_programs_and_files_are_read_as_captured_on_macos_and_linux():
    assert P.parse_memory_pressure(MEMORY_PRESSURE) == 47.0
    assert P.parse_vm_stat(VM_STAT.format(swapins=2286632)) == 2286632
    assert P.parse_meminfo(MEMINFO) == pytest.approx(100.0 * 9788654 / 16314424)
    assert P.parse_vmstat(VMSTAT) == 1234567
    for parse in (P.parse_memory_pressure, P.parse_vm_stat, P.parse_meminfo, P.parse_vmstat):
        assert parse("") is None and parse("something else entirely\n") is None
    # A kernel before MemAvailable names no figure ao would trust, rather than one worked out from MemFree.
    assert P.parse_meminfo(MEMINFO.replace("MemAvailable:    9788654 kB\n", "")) is None


def test_the_median_of_four_one_second_samples_decides_so_one_burst_passes(clock, project):
    rate, samples, why = P.swapin_rate(_counter(5000, [96, 185, 237, 1482]))

    assert (rate, samples, why) == (185, (96, 185, 237, 1482), None)
    assert clock.slept == 4                                       # four one-second samples
    # A burst whose window a mean would put over the ceiling passes all the same...
    rate, samples, _ = P.swapin_rate(_counter(0, [96, 185, 237, 6000]))
    assert sum(samples) / len(samples) > 1500 and rate == 185
    assert P.shortfalls(_reading(paging=rate), project) == []
    # ...and paging over it for three seconds of four is thrash, and does not.
    rate, _, _ = P.swapin_rate(_counter(0, [1400, 2100, 2600, 3100]))
    assert rate == 2100
    assert [key for key, _ in P.shortfalls(_reading(paging=rate), project)] == [P.PAGING_CEILING]


def test_a_sample_is_the_rise_over_the_time_it_took(clock):
    """A counter that is slow to answer is not read as paging faster than it did."""
    values = iter([0, 1000, 2000, 3000, 4000])

    def slow():
        clock.now += 1                                            # each read of it takes a second as well
        return next(values), None

    assert P.swapin_rate(slow)[:2] == (500, (500, 500, 500, 500))


def test_swap_used_is_never_read_as_pressure(monkeypatch, clock, tmp_path):
    # vm_stat's Swapouts dwarf its Swapins here, and a full swap file leaves memory available on Linux all the same.
    assert P.parse_vm_stat(VM_STAT.format(swapins=10)) == 10
    assert P.parse_meminfo(MEMINFO) == P.parse_meminfo(MEMINFO.replace("SwapFree:              0 kB",
                                                                       "SwapFree:        2097148 kB"))
    # Reading a Mac asks memory_pressure once and vm_stat for each sample: never sysctl's swap usage.
    asked = _programs(monkeypatch, {"memory_pressure": [MEMORY_PRESSURE],
                                    "vm_stat": [VM_STAT.format(swapins=n) for n in (100, 196, 381, 618, 2100)]})
    volumes = _volume(monkeypatch, 120)

    reading = P.measure("darwin")

    assert asked == ["memory_pressure"] + ["vm_stat"] * 5
    assert reading == P.Reading(47.0, 185, (96, 185, 237, 1482), 120.0, ())
    assert volumes == [A.HOME]                                    # the volume that holds the home, never /
    # Linux reads its two files, and the home's volume.
    meminfo, vmstat = tmp_path / "meminfo", tmp_path / "vmstat"
    meminfo.write_text(MEMINFO, encoding="utf-8")
    vmstat.write_text(VMSTAT, encoding="utf-8")
    monkeypatch.setattr(P, "MEMINFO", str(meminfo))
    monkeypatch.setattr(P, "VMSTAT", str(vmstat))

    reading = P.measure("linux")

    assert reading.free_memory_pct == pytest.approx(60.0, abs=0.1) and reading.swapins_per_sec == 0
    assert reading.free_disk_gib == 120.0 and reading.unmeasured == ()


# ---- waiting, and refusing -----------------------------------------------------------------------------------

@pytest.mark.parametrize("short, setting, words, remedy", [
    (dict(memory=9.0), P.MEMORY_FLOOR, "memory 9% free, under the 15% floor", "Let the machine settle"),
    (dict(paging=2400), P.PAGING_CEILING, "paging at 2400 swap-ins a second, over the 1500 ceiling",
     "Let the machine settle"),
    (dict(disk=5.25), P.DISK_FLOOR, "disk 5.2 GiB free on the home volume, under the 8 GiB floor",
     "Free disk on the home volume"),
], ids=["memory", "paging", "disk"])
def test_each_limit_refuses_a_verify_with_its_message_after_waiting_no_longer_than_wait(project, lock, clock,
                                                                                       monkeypatch, capsys, short,
                                                                                       setting, words, remedy):
    root = project["root"]
    _gates(root)
    taken = _machine(monkeypatch, clock, _reading(**short))

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=60)) == 2

    out = _plain(capsys.readouterr().out)
    assert f"the machine is short: {words} ({setting}) — waiting up to 60s for room" in out
    assert (f"the machine is still short after 60s; not starting the gates: {words} ({setting}). {remedy}, then run "
            f"this again; or a person changes the limit for this machine: ao config set {setting} <n> --machine") in out
    assert 0 < clock.elapsed <= 60 and len(taken) > 1               # it read again while it waited, and no longer
    assert A.latest_verification(root) is None and "▶ smoke" not in out          # no gate ran
    assert not lock.exists()                                         # the lock it held while it waited is let go


def test_ao_lock_and_merge_check_wait_for_the_machine_and_refuse_as_verify_does(project, lock, clock, monkeypatch,
                                                                                capsys):
    root = project["root"]
    _gates(root)
    _machine(monkeypatch, clock, _reading(paging=2400))
    mark = os.path.join(root, "command-ran")

    assert cli.cmd_lock(project, SimpleNamespace(command=["--", sys.executable, "-c", f"open({mark!r}, 'w')"],
                                                 wait=30)) == 2

    out = _plain(capsys.readouterr().out)
    assert "the machine is still short after 30s; not starting the command: paging at 2400" in out
    assert not os.path.exists(mark) and not lock.exists() and clock.elapsed <= 30
    base = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _git(root, "checkout", "-q", "-b", "feature")
    Path(root, "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(root, "add", "feature.txt", ".ao/gates.json")
    _git(root, "commit", "-q", "-m", "feature")
    _git(root, "checkout", "-q", base)
    clock.start, clock.slept = clock.now, 0

    assert cli.cmd_merge_check(project, SimpleNamespace(branch="feature", into="HEAD", profile="full", wait=0)) == 2

    out = _plain(capsys.readouterr().out)
    assert "the machine is short; not starting the gates: paging at 2400" in out and "waiting up to" not in out
    assert clock.elapsed == P.SAMPLES and clock.slept == 0           # with no wait, the one reading decides
    assert A.merge_checks(root) == [] and not lock.exists()
    assert len(_git(root, "worktree", "list").splitlines()) == 1     # no merge result was checked out


def test_a_machine_that_settles_while_a_verify_waits_lets_it_run(project, lock, clock, monkeypatch, capsys):
    root = project["root"]
    _gates(root)
    taken = _machine(monkeypatch, clock, _reading(paging=3000), _reading(disk=6.0), CALM_MACHINE)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=900)) == 0

    out = _plain(capsys.readouterr().out)
    assert "waiting up to 900s for room" in out
    assert "the machine has room again after 32s: memory 60% free · paging 0 swap-ins/s · disk 500 GiB free" in out
    assert len(taken) == 3 and A.latest_verification(root)["passed"] is True


def test_a_calm_machine_is_read_once_and_a_run_under_its_holders_lock_reads_none(project, lock, monkeypatch, capsys):
    root = project["root"]
    _gates(root)
    taken = _machine(monkeypatch, None, CALM_MACHINE)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=60)) == 0
    assert len(taken) == 1
    assert cli.cmd_lock(project, SimpleNamespace(command=["--", sys.executable, "-c", "pass"], wait=0)) == 0
    assert len(taken) == 2
    # Inside a lock its own holder took - `ao lock -- ao verify` - a run reads nothing: the holder read the machine.
    assert A.acquire_gate_lock(root, 0)
    try:
        assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 0
    finally:
        A.release_gate_lock()
    assert len(taken) == 2
    assert "machine" not in _plain(capsys.readouterr().out)          # a machine that has room is not talked about


def test_windows_says_once_that_it_reads_nothing_and_refuses_nothing(project, lock, clock, monkeypatch, capsys):
    root = project["root"]
    _gates(root)
    asked = _programs(monkeypatch, {})
    reading = P.measure("win32")
    assert reading == P.Reading(None, None, (), None,
                                (("machine", "ao reads memory, paging and disk on macOS and Linux"),))
    assert asked == [] and clock.slept == 0                          # nothing was run or sampled
    _machine_limits(refuse_below_free_memory_pct=100, refuse_above_swapins_per_sec=1,
                    refuse_below_free_disk_gib=100000)                 # limits no measured machine meets
    taken = _machine(monkeypatch, clock, reading)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=60)) == 0

    out = _plain(capsys.readouterr().out)
    assert out.count("not measured") == 1
    assert ("machine not measured (ao reads memory, paging and disk on macOS and Linux): nothing waits or refuses on "
            "it") in out
    assert len(taken) == 1 and clock.slept == 0
    assert A.latest_verification(root)["passed"] is True


def test_a_program_that_cannot_be_read_is_not_measured_said_once_and_refuses_nothing(project, lock, clock,
                                                                                      monkeypatch, capsys):
    _programs(monkeypatch, {"vm_stat": ["Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"]})
    _volume(monkeypatch, 5.0)

    reading = P.measure("darwin")

    assert reading.unmeasured == (("memory", "memory_pressure is not on PATH"),
                                  ("paging", "vm_stat printed no Swapins line"))
    assert (reading.free_memory_pct, reading.swapins_per_sec, reading.free_disk_gib) == (None, None, 5.0)
    _machine_limits(refuse_below_free_memory_pct=100, refuse_above_swapins_per_sec=1)
    taken = _machine(monkeypatch, clock, reading)

    refused = P.wait_for_room(project, 30, "the gates")

    out = _plain(capsys.readouterr().out)
    assert out.count("not measured") == 2 and len(taken) > 1         # one line, said once while the disk was waited on
    assert out.startswith("memory not measured (memory_pressure is not on PATH); paging not measured (vm_stat printed "
                          "no Swapins line): nothing waits or refuses on them\n")
    assert refused.startswith("the machine is still short after 30s; not starting the gates: disk 5 GiB free")
    assert "memory" not in refused and "paging" not in refused       # only what was measured refuses


def test_the_limits_are_the_machines_and_a_project_cannot_loosen_them(project):
    cfg = dict(project, limits={"refuse_above_swapins_per_sec": 999999, "refuse_below_free_disk_gib": 0,
                                "max_write_lanes": 5})

    assert P.limits(cfg) == {P.MEMORY_FLOOR: 15, P.PAGING_CEILING: 1500, P.DISK_FLOOR: 8}
    assert S.get(cfg, "limits.max_write_lanes") == 5                 # the lane cap is the project's own
    named = {key for key, text in S.problems(cfg) if "--machine" in text}
    assert named == {P.PAGING_CEILING, P.DISK_FLOOR}
    _machine_limits(refuse_above_swapins_per_sec=2500, refuse_below_free_memory_pct=10)
    assert P.limits(cfg) == {P.MEMORY_FLOOR: 10, P.PAGING_CEILING: 2500, P.DISK_FLOOR: 8}


# ---- lanes -----------------------------------------------------------------------------------------------------

def _lane(action, item=None):
    return SimpleNamespace(action=action, item=item)


def test_a_lane_start_reads_the_machine_last_and_refuses_at_once_when_it_is_short(project, tmp_path, clock,
                                                                                   monkeypatch, capsys):
    root = project["root"]
    Path(root, ".ao", "board.md").write_text(BOARD, encoding="utf-8")
    taken = _machine(monkeypatch, clock, _reading(memory=9.0))

    assert cli.cmd_lane(project, _lane("start", "R")) == 2             # not READY: refused before the machine is read
    assert taken == []
    assert cli.cmd_lane(project, _lane("start", "B")) == 2

    out = _plain(capsys.readouterr().out)
    assert ("refused: the machine is short; not starting the lane: memory 9% free, under the 15% floor "
            f"({P.MEMORY_FLOOR}). Let the machine settle, then run this again; or a person changes the limit for this "
            f"machine: ao config set {P.MEMORY_FLOOR} <n> --machine") in out
    assert len(taken) == 1 and clock.slept == 0                       # it waits for no lock, and for nothing else
    assert A.lane_records(root) == [] and not (tmp_path / "proj-lanes").exists()
    assert not _git(root, "branch", "--list", "lane/B")
    _machine(monkeypatch, clock, CALM_MACHINE)
    assert cli.cmd_lane(project, _lane("start", "B")) == 0


def test_ao_lane_start_refuses_a_lane_past_the_cap_and_makes_nothing(project, tmp_path, capsys):
    root = project["root"]
    Path(root, ".ao", "board.md").write_text(BOARD, encoding="utf-8")
    assert cli.cmd_lane(project, _lane("start", "B")) == 0
    assert cli.cmd_lane(project, _lane("start", "D")) == 0
    capsys.readouterr()

    assert cli.cmd_lane(project, _lane("start", "E")) == 2

    out = _plain(capsys.readouterr().out)
    assert ("refused: this checkout has 2 lanes already, as many as limits.max_write_lanes allows: B (ready), "
            "D (ready); `ao lane remove <item>` retires one, or `ao config set limits.max_write_lanes <n>` changes "
            "the cap") in out
    assert A.lane_record(root, "E") is None and not (tmp_path / "proj-lanes" / "E").exists()
    assert not _git(root, "branch", "--list", "lane/E")
    assert cli.cmd_lane(dict(project, limits={"max_write_lanes": 3}), _lane("start", "E")) == 0
    assert cli.cmd_lane(project, _lane("remove", "E")) == 0
    assert cli.cmd_lane(project, _lane("remove", "B")) == 0
    assert cli.cmd_lane(project, _lane("start", "E")) == 0             # a lane retired makes room for another


def test_two_lane_starts_at_once_never_both_pass_the_cap(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    Path(root, ".ao", "board.md").write_text(BOARD, encoding="utf-8")
    assert cli.cmd_lane(project, _lane("start", "B")) == 0

    def another_start_meanwhile():
        # Both found room before either had a record; the other one makes its record while this one reads the machine.
        Path(root, ".ao", "lanes", "D.json").write_text(json.dumps({"item": "D", "state": "preparing"}),
                                                       encoding="utf-8")
        return CALM_MACHINE

    monkeypatch.setattr(P, "read", another_start_meanwhile)
    capsys.readouterr()

    assert cli.cmd_lane(project, _lane("start", "E")) == 2

    assert "this checkout has 2 lanes already, as many as limits.max_write_lanes allows: B (ready), D (preparing)" \
        in _plain(capsys.readouterr().out)
    # It gave way, and left nothing: no record, no worktree, no branch.
    assert A.lane_record(root, "E") is None and not (tmp_path / "proj-lanes" / "E").exists()
    assert not _git(root, "branch", "--list", "lane/E")


# ---- the doctor ------------------------------------------------------------------------------------------------

def test_the_doctor_shows_the_machine_on_one_line(project, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)    # no agent CLI is found, or started

    cli.cmd_doctor(project, SimpleNamespace(check=False))

    lines = [line for line in _plain(capsys.readouterr().out).splitlines() if line.startswith("machine ")]
    assert lines == [f"{'machine':<16}memory 60% free · paging 0 swap-ins/s · disk 500 GiB free"]
    _machine(monkeypatch, None, _reading(memory=9.0, paging=2400, disk=7.96))
    assert _plain(cli._machine_line(project)) == (
        f"{'machine':<16}memory 9% free, under the 15% floor · paging 2400 swap-ins/s, over the 1500 ceiling · "
        "disk 7.9 GiB free, under the 8 GiB floor")
    _machine(monkeypatch, None, _reading(memory=None, unmeasured=[("memory", "memory_pressure is not on PATH")]))
    assert _plain(cli._machine_line(project)) == (
        f"{'machine':<16}memory not measured (memory_pressure is not on PATH) · paging 0 swap-ins/s · "
        "disk 500 GiB free")
    _machine(monkeypatch, None, P.measure("win32"))
    assert _plain(cli._machine_line(project)) == (
        f"{'machine':<16}not measured (ao reads memory, paging and disk on macOS and Linux)")
