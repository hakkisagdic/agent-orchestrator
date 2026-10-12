"""How hard the machine is pressed, read before heavy work starts (MACHINE-PRESSURE).

Five test suites at once swap a laptop into uselessness while each one looks reasonable on its own, and a machine in
that state appears hung, which is the most expensive kind of confusion to debug. The machine gate lock keeps ao's own
suites apart; what else loads the machine - a container build, an indexer, a suite run outside ao - it cannot see. So
a gate run and `ao lock -- <command>`, once they hold the lock, and `ao lane start`, before it makes anything, read
three figures, each against a machine setting, and wait while one is past its limit rather than thrash:

- memory free, in percent: on macOS the system-wide free percentage `memory_pressure` prints, the figure the kernel
  acts on - one worked out from vm_stat's page counts read 1.1 GB free on a machine the system called 47% free, and
  nearly stopped a healthy batch; on Linux MemAvailable over MemTotal, from /proc/meminfo.
- swap-ins a second: the lower median of four back-to-back one-second samples of the swap-in counter, vm_stat's
  Swapins on macOS and pswpin in /proc/vmstat on Linux. Paging spikes by an order of magnitude for a second whenever
  anything starts: four samples of an idle machine read 96, 185, 237 and 1482, and the mean of a window holding the
  last closed the gate on a machine with 49% of its memory free. The median lets one burst pass and still catches
  sustained thrash, which runs in the thousands; idle to busy stays under 200.
- disk free on the volume that holds the home, in GiB, never `/`: on macOS `/` is the sealed system volume and reads
  100% full when all is well. Three agents once failed within minutes of each other on a full disk while memory read
  40% free and paging 12 a second, and what they saw read like ordinary test failures.

Swap used is never read. macOS keeps a swap file after the pressure that grew it has passed, so an idle machine reads
93% used, and a gate on it is red for ever - a gate everyone learns to ignore, which is worse than none.

On Windows, and wherever a figure cannot be read, it is "not measured": said once, and never a refusal on its
account. `read` is the one place a command reads the machine through; the real reading samples paging for four
seconds, and the test suite puts a calm machine there (tests/conftest.py). Standard library only.
"""
import os
import re
import subprocess
import sys
import time
from collections import namedtuple

UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

# The swap-in counter is read SAMPLES + 1 times, SAMPLE_SECONDS apart; the rate is the lower median of the rises.
SAMPLES = 4
SAMPLE_SECONDS = 1
# Between two readings while a command waits for the machine to have room again.
RECHECK_SECONDS = 10
# What memory_pressure or vm_stat may take to answer before its figure is not measured.
PROGRAM_SECONDS = 10
GIB = float(1 << 30)
# Where Linux keeps the figures; a test points these at files of its own.
MEMINFO = "/proc/meminfo"
VMSTAT = "/proc/vmstat"

# The machine setting each figure is held to (src/ao/settings.py).
MEMORY_FLOOR = "limits.refuse_below_free_memory_pct"
PAGING_CEILING = "limits.refuse_above_swapins_per_sec"
DISK_FLOOR = "limits.refuse_below_free_disk_gib"
ELSEWHERE = "ao reads it on macOS and Linux"

# One reading. A figure is None where it was not measured, and `unmeasured` holds (what, why) for each such figure,
# or ("machine", why) when nothing was read at all; `swapin_samples` are the one-second rates the median came from.
Reading = namedtuple("Reading", "free_memory_pct swapins_per_sec swapin_samples free_disk_gib unmeasured")


def parse_memory_pressure(text):
    """The system-wide free percentage `memory_pressure` prints on macOS, or None."""
    found = re.search(r"free percentage:\s*(\d+(?:\.\d+)?)\s*%", text or "")
    return float(found.group(1)) if found else None


def parse_vm_stat(text):
    """vm_stat's Swapins counter on macOS - pages swapped in since boot - or None. Its Swapouts is never read."""
    found = re.search(r"^Swapins:\s*(\d+)\.?\s*$", text or "", re.M)
    return int(found.group(1)) if found else None


def parse_meminfo(text):
    """MemAvailable as a percentage of MemTotal, from Linux's /proc/meminfo, or None.

    Its SwapTotal and SwapFree are never read: swap used is a high-water mark, not present pressure.
    """
    values = dict(re.findall(r"^(MemTotal|MemAvailable):\s*(\d+)", text or "", re.M))
    total = int(values.get("MemTotal") or 0)
    if not total or "MemAvailable" not in values:
        return None
    return 100.0 * int(values["MemAvailable"]) / total


def parse_vmstat(text):
    """pswpin from Linux's /proc/vmstat - pages swapped in since boot - or None."""
    found = re.search(r"^pswpin\s+(\d+)\s*$", text or "", re.M)
    return int(found.group(1)) if found else None


def swapin_rate(count):
    """(swap-ins a second, the samples, None), or (None, (), why) when the counter cannot be read.

    `count()` gives (pages swapped in since boot, None), or (None, why). The counter is read once and then SAMPLES
    times, a second apart; each sample is its rise over the time that passed, which is the second and what reading
    the counter took. The rate is the lower median of the samples, the second smallest of four, as the machine
    script that measured these numbers takes it: it is over a ceiling only when three of the four seconds were,
    so a burst of a second or two passes, and a mean, which one burst decides, is never taken.
    """
    before, why = count()
    if before is None:
        return None, (), why
    at = time.monotonic()
    samples = []
    for _ in range(SAMPLES):
        time.sleep(SAMPLE_SECONDS)
        now_count, why = count()
        if now_count is None:
            return None, (), why
        now = time.monotonic()
        samples.append(max(0, int(round((now_count - before) / max(now - at, 0.001)))))
        before, at = now_count, now
    return sorted(samples)[(len(samples) - 1) // 2], tuple(samples), None


def _program_output(name):
    """(what program `name` printed, None), or (None, why it could not be read). The program is the one PATH holds,
    found as ao finds every program it starts, never in the current directory (WINDOWS-NO-CWD-EXEC)."""
    from . import lib as A
    program = A.program_path(name)
    if program is None:
        return None, f"{name} is not on PATH"
    try:
        done = subprocess.run([program], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding=UTF8,
                              errors="replace", timeout=PROGRAM_SECONDS)
    except subprocess.TimeoutExpired:
        return None, f"{name} did not answer in {PROGRAM_SECONDS}s"
    except OSError as exc:
        return None, f"{name} could not be started ({exc.strerror or exc})"
    if done.returncode:
        return None, f"{name} exited {done.returncode}"
    return done.stdout, None


def _file_text(path):
    try:
        with open(path, encoding=UTF8, errors="replace") as fh:
            return fh.read(), None
    except OSError as exc:
        return None, f"{path} cannot be read ({exc.strerror or exc})"


def _figure(source, parse, missing):
    """(the figure `parse` finds in what `source()` gives, None), or (None, why it was not found)."""
    text, why = source()
    if text is None:
        return None, why
    value = parse(text)
    return (value, None) if value is not None else (None, missing)


def _memory_macos():
    return _figure(lambda: _program_output("memory_pressure"), parse_memory_pressure,
                   "memory_pressure printed no free percentage")


def _swapins_macos():
    return _figure(lambda: _program_output("vm_stat"), parse_vm_stat, "vm_stat printed no Swapins line")


def _memory_linux():
    return _figure(lambda: _file_text(MEMINFO), parse_meminfo, f"{MEMINFO} names no MemAvailable")


def _swapins_linux():
    return _figure(lambda: _file_text(VMSTAT), parse_vmstat, f"{VMSTAT} has no pswpin line")


def _free_disk_gib(path):
    """(GiB a user may still write on the volume that holds `path`, None), or (None, why)."""
    try:
        st = os.statvfs(path)
    except (OSError, AttributeError) as exc:
        return None, f"the home volume cannot be read ({exc})"
    return st.f_bavail * st.f_frsize / GIB, None


def measure(system=None):
    """The machine as it is now, on `system` (this one's, sys.platform, unless a test names another).

    About four seconds on macOS and Linux, where paging is sampled; nothing is read on Windows, which says so.
    Elsewhere memory and paging are not measured and the disk is.
    """
    system = sys.platform if system is None else system
    if system.startswith("win"):
        return Reading(None, None, (), None, (("machine", "ao reads memory, paging and disk on macOS and Linux"),))
    if system == "darwin":
        memory, swapins = _memory_macos, _swapins_macos
    elif system.startswith("linux"):
        memory, swapins = _memory_linux, _swapins_linux
    else:
        memory = swapins = None
    unmeasured = []
    free_memory, why = memory() if memory else (None, ELSEWHERE)
    if free_memory is None:
        unmeasured.append(("memory", why))
    rate, samples, why = swapin_rate(swapins) if swapins else (None, (), ELSEWHERE)
    if rate is None:
        unmeasured.append(("paging", why))
    from . import lib as A
    free_disk, why = _free_disk_gib(A.HOME)
    if free_disk is None:
        unmeasured.append(("disk", why))
    return Reading(free_memory, rate, samples, free_disk, tuple(unmeasured))


def read():
    """The machine now: the one place a command reads it through. The test suite replaces this one function with a
    calm machine read at once (tests/conftest.py), as the real reading takes four seconds."""
    return measure()


def limits(cfg):
    """{setting: its value} for the three machine limits."""
    from . import settings as S
    return {key: S.get(cfg, key) for key in (MEMORY_FLOOR, PAGING_CEILING, DISK_FLOOR)}


def _down(value):
    """A figure as a person reads it: whole, or to one decimal, never rounded up across a limit."""
    tenths = int(value * 10 + 1e-6) / 10.0          # 14.7 is 14.699... as a float, and still 14.7
    return f"{tenths:.0f}" if tenths == int(tenths) else f"{tenths:.1f}"


def shortfalls(reading, cfg):
    """[(setting, what the reading shows against it)] for each measured figure past its limit; a figure that was not
    measured is never one."""
    limit, out = limits(cfg), []
    if reading.free_memory_pct is not None and reading.free_memory_pct < limit[MEMORY_FLOOR]:
        out.append((MEMORY_FLOOR, f"memory {_down(reading.free_memory_pct)}% free, under the {limit[MEMORY_FLOOR]}% "
                                  f"floor ({MEMORY_FLOOR})"))
    if reading.swapins_per_sec is not None and reading.swapins_per_sec > limit[PAGING_CEILING]:
        out.append((PAGING_CEILING, f"paging at {reading.swapins_per_sec} swap-ins a second, over the "
                                    f"{limit[PAGING_CEILING]} ceiling ({PAGING_CEILING})"))
    if reading.free_disk_gib is not None and reading.free_disk_gib < limit[DISK_FLOOR]:
        out.append((DISK_FLOOR, f"disk {_down(reading.free_disk_gib)} GiB free on the home volume, under the "
                                f"{limit[DISK_FLOOR]} GiB floor ({DISK_FLOOR})"))
    return out


def unmeasured_note(reading):
    """The one line a reading says of the figures it could not read, or None when it read them all."""
    if not reading.unmeasured:
        return None
    named = "; ".join(f"{what} not measured ({why})" for what, why in reading.unmeasured)
    return f"{named}: nothing waits or refuses on {'it' if len(reading.unmeasured) == 1 else 'them'}"


def summary(reading, cfg):
    """The reading on one line, each figure past its limit naming the limit: what `ao doctor` shows."""
    unmeasured = dict(reading.unmeasured)
    if "machine" in unmeasured:
        return f"not measured ({unmeasured['machine']})"
    limit, parts = limits(cfg), []
    if reading.free_memory_pct is None:
        parts.append(f"memory not measured ({unmeasured.get('memory')})")
    else:
        short = reading.free_memory_pct < limit[MEMORY_FLOOR]
        parts.append(f"memory {_down(reading.free_memory_pct)}% free"
                     + (f", under the {limit[MEMORY_FLOOR]}% floor" if short else ""))
    if reading.swapins_per_sec is None:
        parts.append(f"paging not measured ({unmeasured.get('paging')})")
    else:
        short = reading.swapins_per_sec > limit[PAGING_CEILING]
        parts.append(f"paging {reading.swapins_per_sec} swap-ins/s"
                     + (f", over the {limit[PAGING_CEILING]} ceiling" if short else ""))
    if reading.free_disk_gib is None:
        parts.append(f"disk not measured ({unmeasured.get('disk')})")
    else:
        short = reading.free_disk_gib < limit[DISK_FLOOR]
        parts.append(f"disk {_down(reading.free_disk_gib)} GiB free"
                     + (f", under the {limit[DISK_FLOOR]} GiB floor" if short else ""))
    return " · ".join(parts)


def refusal(short, waited, what):
    """The sentence a start refused for the machine prints: each figure past its limit, and what a person does."""
    keys = [key for key, _ in short]
    remedies = (["free disk on the home volume"] if DISK_FLOOR in keys else []) \
        + (["let the machine settle"] if MEMORY_FLOOR in keys or PAGING_CEILING in keys else [])
    change = (f"the limit for this machine: ao config set {keys[0]} <n> --machine" if len(keys) == 1
              else "the limits for this machine: ao config set <setting> <n> --machine")
    head = f"the machine is still short after {waited}s" if waited > 0 else "the machine is short"
    action = " and ".join(remedies)
    return (f"{head}; not starting {what}: {'; '.join(text for _, text in short)}. "
            f"{action[:1].upper()}{action[1:]}, then run this again; or a person changes {change}")


def _say(tone, text):
    from . import lib as A
    print(f"{A.C[tone]}{text}{A.C['reset']}", flush=True)


def wait_for_room(cfg, wait, what):
    """None once the machine has room for `what`, or the refusal to print when it stayed short for `wait` seconds.

    A machine that has room costs one reading. One that is short is read again every RECHECK_SECONDS while it stays
    so, and only while another reading can end before `wait` seconds are up, the first one's length being what each
    takes; with no wait the first reading decides. What could not be read is said once, from the first reading, and
    never refuses: a figure not measured is no figure past its limit.
    """
    started = time.monotonic()
    reading = read()
    note = unmeasured_note(reading)
    if note:
        _say("dim", note)
    short = shortfalls(reading, cfg)
    if not short:
        return None
    took = time.monotonic() - started
    deadline = started + max(0, wait)
    if wait > 0:
        _say("yellow", f"the machine is short: {'; '.join(text for _, text in short)} — waiting up to {wait}s "
                       f"for room")
    while short:
        left = deadline - time.monotonic()
        if left <= took:
            break
        time.sleep(min(RECHECK_SECONDS, left - took))
        reading = read()
        short = shortfalls(reading, cfg)
    if not short:
        _say("green", f"the machine has room again after {int(time.monotonic() - started)}s: "
                      f"{summary(reading, cfg)}")
        return None
    return refusal(short, wait, what)
