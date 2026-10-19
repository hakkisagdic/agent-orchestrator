"""Process introspection without a dependency.

psutil answers "which processes exist, what is each one's exact argv, its cwd,
its parent and group, does it have a terminal" — and ao needs exactly those
answers to tell an agent turn from a shell that mentions one. We do not install
packages, so this borrows psutil's *method*: on macOS the public libproc calls
(`proc_listpids`, `proc_pidinfo` with PROC_PIDTBSDINFO and PROC_PIDVNODEPATHINFO)
and `sysctl(KERN_PROCARGS2)` for the argument vector, through ctypes; on Linux
the /proc files. Both give the argv as a vector, so a path with a space in it
("…/Application Support/kiro-cli/node") is one argument and not two — the bug
class that whitespace-splitting `ps` output produced. Both answer in
milliseconds where `pgrep -f` plus one `lsof` per pid took seconds.

Every function falls back to the platform's `ps` and `lsof` when the native path is
unavailable or fails its self-check, so nothing here can make ao blind. No tool here
is started through a shell. Where the table itself cannot be read - the reader fails,
does not answer in time, or answers nothing a table can be made of - the readers raise
TableUnreadable rather than answer an empty one: an empty table reads as no process at
all, and a guard that asks whether anything runs passed when it could not look.
"""
import contextlib
import ctypes
import ctypes.util
import os
import struct
import subprocess
import sys
import time
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

_NATIVE = None          # decided on first use: "darwin" | "linux" | None (ps and lsof fallbacks)


class TableUnreadable(Exception):
    """The process table could not be read on this platform; the message names the platform and why.

    On Windows the table is one PowerShell query, and a PowerShell that was missing, failed or did
    not answer in time gave an empty table: every caller read that as fact - no agent turn running,
    no reviewer alive, nothing holding a session - and a guard that asks "is anything running?"
    passed when it could not look. It is an error its callers see now, and each that decides
    something from the table refuses or holds; one that only shows it says it could not look
    (WINDOWS-TABLE-CLOSED).
    """

    def __init__(self, why, platform=None):
        self.why = why
        self.platform = platform or platform_name()
        super().__init__(f"the process table could not be read on {self.platform}: {why}")


def platform_name():
    """The platform as a person names it, for a message that says where something could not be done."""
    if sys.platform == "win32":
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux" if sys.platform.startswith("linux") else sys.platform


class _Unanswered(str):
    """A tool's empty answer that knows why it is empty: "" to every reader, `why` to the one that asks
    (WINDOWS-TABLE-CLOSED)."""

    def __new__(cls, why):
        empty = super().__new__(cls, "")
        empty.why = why
        return empty


def _run(argv):
    """A platform tool's standard output; an empty answer that says why when it gives none.

    Started without a shell and found where ao finds every program (lib.binary_candidates),
    so a launchd job whose PATH is minimal still finds it; it reads nothing from standard
    input. Every reader takes the answer as text, and "" when the tool could not be run, as
    the shell helper this replaced gave; its first line of standard error, its exit status or
    its silence says why, for the reader that must not take nothing for an answer: the process
    table read an empty PowerShell answer as no process at all (WINDOWS-TABLE-CLOSED).
    """
    from . import lib
    program = lib.runnable_binary(argv[0])
    if program is None:
        return _Unanswered(f"{argv[0]} was not found")
    try:
        done = subprocess.run([program, *argv[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding=UTF8, errors="replace", timeout=30)
    except subprocess.TimeoutExpired:
        return _Unanswered(f"{argv[0]} did not answer within 30 seconds")
    except Exception as exc:
        return _Unanswered(f"{argv[0]} could not be started ({type(exc).__name__}: {exc})")
    if (done.stdout or "").strip():
        return done.stdout
    said = next((line.strip() for line in (getattr(done, "stderr", None) or "").splitlines() if line.strip()), "")
    status = getattr(done, "returncode", 0)
    why = f"{argv[0]} exited with status {status}" if status else f"{argv[0]} printed nothing"
    return _Unanswered(f"{why}: {said[:200]}" if said else why)


def system_directory():
    """Windows' system directory as Windows names it (GetSystemDirectoryW), or None where it cannot be asked.

    For lib.windows_system_directory when SystemRoot names none: the lib's parts may not load ctypes
    (WINDOWS-NO-CWD-EXEC).
    """
    try:
        answer = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetSystemDirectoryW(answer, len(answer))
    except (AttributeError, OSError):
        return None
    return answer.value if size else None


def oem_encoding():
    """The codec of Windows' OEM code page, which its console programs write to a pipe in - "cp857" on a Turkish
    Windows, "cp437" on an American one - or None where there is none to ask (WINDOWS-SAY-WHY).

    For lib.console_text: the lib's parts may not load ctypes. GetOEMCP is asked, and a code page
    Python has no codec for is none.
    """
    import codecs
    try:
        name = f"cp{int(ctypes.windll.kernel32.GetOEMCP())}"
        codecs.lookup(name)
    except (AttributeError, OSError, LookupError, ValueError):
        return None
    return name


def _clock_seconds(text):
    """Seconds in a `ps -o etime` reading, [[dd-]hh:]mm:ss, or None."""
    days, _, clock = text.strip().rpartition("-")
    try:
        seconds = 0
        for part in clock.split(":"):
            seconds = seconds * 60 + int(part)
        return seconds + int(days or 0) * 86400
    except ValueError:
        return None


def _cim_epoch(value):
    """Epoch seconds of a Win32_Process CreationDate as it reaches ao, or None.

    Windows PowerShell's ConvertTo-Json writes a date as /Date(<milliseconds>)/; WMI's own
    form is DMTF, yyyymmddHHMMSS.ffffff followed by the offset from UTC in minutes.
    """
    import calendar
    import re as _re
    text = str(value or "")
    m = _re.fullmatch(r"/Date\((-?\d+)[^)]*\)/", text)
    if m:
        return int(m.group(1)) / 1000
    m = _re.fullmatch(r"(\d{14})\.\d+([+-])(\d{3})", text)
    if m:
        local = calendar.timegm(time.strptime(m.group(1), "%Y%m%d%H%M%S"))
        return local - int(m.group(2) + m.group(3)) * 60
    return None


def group_cpu_seconds(pgid):
    """CPU seconds spent so far by every process in one process group, or None when unreadable (#25).

    A reviewer thinking spends CPU as its answer streams in; one that has hung
    spends none. Windows exposes no process group here, so it answers None.
    """
    if os.name == "nt":
        return None
    total, found = 0.0, False
    if sys.platform.startswith("linux"):
        tick = os.sysconf("SC_CLK_TCK")
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/stat", "rb") as fh:
                    fields = fh.read().rsplit(b")", 1)[1].split()
            except (OSError, IndexError):
                continue
            if len(fields) > 12 and int(fields[2]) == pgid:
                total += (int(fields[11]) + int(fields[12])) / tick
                found = True
        return total if found else None
    for line in _run(["ps", "-A", "-o", "pgid=,time="]).splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) != pgid:
            continue
        seconds = 0.0
        try:
            for piece in parts[1].split(":"):
                seconds = seconds * 60 + float(piece)
        except ValueError:
            continue
        total += seconds
        found = True
    return total if found else None


# ---------------------------------------------------------------- macOS (libproc)
class _Darwin:
    CTL_KERN, KERN_ARGMAX, KERN_PROCARGS2 = 1, 8, 49
    PROC_ALL_PIDS, PROC_PIDTBSDINFO, PROC_PIDVNODEPATHINFO = 1, 3, 9
    BSDINFO_SIZE, VNODEPATHINFO_SIZE, VNODE_INFO_SIZE, MAXPATHLEN = 136, 2352, 152, 1024
    NODEV = 0xFFFFFFFF

    def __init__(self):
        self.libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self.libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.argmax = self._sysctl_int([self.CTL_KERN, self.KERN_ARGMAX])

    def _sysctl_int(self, mib):
        arr = (ctypes.c_int * len(mib))(*mib)
        out = ctypes.c_int()
        sz = ctypes.c_size_t(ctypes.sizeof(out))
        if self.libc.sysctl(arr, len(mib), ctypes.byref(out), ctypes.byref(sz), None, 0) != 0:
            raise OSError(ctypes.get_errno())
        return out.value

    def all_pids(self):
        # A failed proc_listpids answers -1, and the slice of the buffer it gave then held no pid: no process at
        # all, where it could not look (WINDOWS-TABLE-CLOSED).
        n = self.libproc.proc_listpids(self.PROC_ALL_PIDS, 0, None, 0)
        if n <= 0:
            raise TableUnreadable(f"proc_listpids answered {n} (errno {ctypes.get_errno()})", "macOS")
        buf = (ctypes.c_int * (n // 4 + 64))()
        n = self.libproc.proc_listpids(self.PROC_ALL_PIDS, 0, buf, ctypes.sizeof(buf))
        pids = [p for p in buf[:max(n, 0) // 4] if p > 0]
        if not pids:
            raise TableUnreadable(f"proc_listpids listed no process (it answered {n})", "macOS")
        return pids

    def argv(self, pid):
        mib = (ctypes.c_int * 3)(self.CTL_KERN, self.KERN_PROCARGS2, pid)
        buf = ctypes.create_string_buffer(self.argmax)
        sz = ctypes.c_size_t(self.argmax)
        if self.libc.sysctl(mib, 3, buf, ctypes.byref(sz), None, 0) != 0:
            return None
        raw = buf.raw[:sz.value]
        if len(raw) < 4:
            return None
        argc = struct.unpack("i", raw[:4])[0]
        rest = raw[4:]
        if b"\0" not in rest:
            return None
        rest = rest[rest.index(b"\0"):].lstrip(b"\0")          # skip the exec path and its padding
        parts = rest.split(b"\0")
        return [p.decode("utf-8", "replace") for p in parts[:max(argc, 0)]]

    def cwd(self, pid):
        buf = ctypes.create_string_buffer(self.VNODEPATHINFO_SIZE * 2)
        n = self.libproc.proc_pidinfo(pid, self.PROC_PIDVNODEPATHINFO, ctypes.c_uint64(0), buf, ctypes.sizeof(buf))
        if n <= 0:
            return None
        p = buf.raw[self.VNODE_INFO_SIZE:self.VNODE_INFO_SIZE + self.MAXPATHLEN]
        return p.split(b"\0", 1)[0].decode("utf-8", "replace") or None

    def info(self, pid):
        buf = ctypes.create_string_buffer(self.BSDINFO_SIZE * 2)
        n = self.libproc.proc_pidinfo(pid, self.PROC_PIDTBSDINFO, ctypes.c_uint64(0), buf, ctypes.sizeof(buf))
        if n < self.BSDINFO_SIZE:
            return None
        r = buf.raw
        ppid, = struct.unpack_from("I", r, 16)
        pgid, = struct.unpack_from("I", r, 100)
        tdev, = struct.unpack_from("I", r, 108)
        start, = struct.unpack_from("Q", r, 120)
        comm = r[48:64].split(b"\0", 1)[0].decode("utf-8", "replace")
        return {"ppid": ppid, "pgid": pgid, "tty": None if tdev == self.NODEV else tdev,
                "start": start, "comm": comm}

    def elapsed(self, pid):
        # `start` is the start time's whole seconds; ps subtracts them from its own clock's.
        info = self.info(pid)
        return None if info is None else max(0, int(time.time()) - info["start"])

    SZOMB = 5

    def zombie(self, pid):
        buf = ctypes.create_string_buffer(self.BSDINFO_SIZE * 2)
        n = self.libproc.proc_pidinfo(pid, self.PROC_PIDTBSDINFO, ctypes.c_uint64(0), buf, ctypes.sizeof(buf))
        if n < self.BSDINFO_SIZE:
            return None
        status, = struct.unpack_from("I", buf.raw, 4)
        return status == self.SZOMB


# ---------------------------------------------------------------- Linux (/proc)
class _Linux:
    def all_pids(self):
        try:
            pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
        except OSError as exc:
            raise TableUnreadable(f"/proc could not be listed ({exc})", "Linux") from None
        if not pids:
            raise TableUnreadable("/proc lists no process", "Linux")      # this one runs (WINDOWS-TABLE-CLOSED)
        return pids

    def argv(self, pid):
        try:
            raw = open(f"/proc/{pid}/cmdline", "rb").read()
        except OSError:
            return None
        return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p] or None

    def cwd(self, pid):
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None

    def zombie(self, pid):
        try:
            stat = open(f"/proc/{pid}/stat", encoding=UTF8).read()
        except OSError:
            return None
        return stat[stat.rindex(")") + 2:stat.rindex(")") + 3] == "Z"

    def info(self, pid):
        try:
            stat = open(f"/proc/{pid}/stat", encoding=UTF8).read()
        except OSError:
            return None
        rest = stat[stat.rindex(")") + 2:].split()
        # fields after comm: state ppid pgrp session tty_nr … starttime(22nd overall = index 19 here)
        ppid, pgid, tty = int(rest[1]), int(rest[2]), int(rest[4])
        return {"ppid": ppid, "pgid": pgid, "tty": None if tty == 0 else tty,
                "start": int(rest[19]) if len(rest) > 19 else 0,
                "comm": stat[stat.index("(") + 1:stat.rindex(")")]}

    def elapsed(self, pid):
        # `start` counts clock ticks since boot; ps takes whole seconds of both, as here.
        info = self.info(pid)
        try:
            with open("/proc/uptime", encoding=UTF8) as fh:
                since_boot = int(float(fh.read().split()[0]))
        except (OSError, ValueError, IndexError):
            return None
        return None if info is None else max(0, since_boot - info["start"] // os.sysconf("SC_CLK_TCK"))


# ---------------------------------------------------------------- Windows (CIM via PowerShell, JSON)
class _Windows:
    """Win32_Process through PowerShell, as JSON — structured, not parsed text.

    CommandLine is one string on Windows; it is split with the platform's own
    quoting rules here. The working directory is not exposed by CIM; it is read from
    the process environment block, as psutil reads it (#9), and where that cannot be
    read agent matching falls back to the repository path on the command line.
    """
    _cache = None
    _cache_at = 0.0
    # PowerShell writes what a script outputs to a pipe in the console's code page, so a command line
    # holding a letter outside it - a repository under C:\Users\<a Turkish name>, a prompt in Turkish -
    # arrived as "?" or as bytes that are not UTF-8, and a turn named by its repository's path was not
    # placed. The JSON goes to the standard output handle as UTF-8 bytes, past any code page (#71).
    SNAPSHOT = ("$json = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,"
                "ExecutablePath,Name,SessionId,CreationDate | ConvertTo-Json -Compress; "
                "$bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$json); "
                "$out = [Console]::OpenStandardOutput(); $out.Write($bytes, 0, $bytes.Length); $out.Flush()")

    def invalidate(self):
        self._cache = None
        self._cache_at = 0.0

    def _snapshot(self):
        """{pid: Win32_Process row} for every process, kept two seconds; TableUnreadable when there is none.

        A PowerShell that was missing, failed, did not answer in time, or printed something that is
        not the table gave an empty snapshot, and every reader took that for no process at all: no
        turn in the tree, no reviewer, no session in use (WINDOWS-TABLE-CLOSED). Only the length of
        an answer that is not JSON is told, since the command lines in it can hold a secret.
        """
        import json as _json
        import time as _time
        if self._cache is not None and _time.time() - self._cache_at < 2.0:
            return self._cache
        query = "PowerShell's Get-CimInstance Win32_Process"
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", self.SNAPSHOT])
        if not out.strip():
            raise TableUnreadable(getattr(out, "why", None) or f"{query} printed nothing", "Windows")
        try:
            data = _json.loads(out)
        except ValueError:
            raise TableUnreadable(f"{query} printed {len(out)} characters that are not JSON", "Windows") from None
        snap = {}
        for r in data if isinstance(data, list) else [data]:
            try:
                pid = int(r.get("ProcessId"))
            except (AttributeError, TypeError, ValueError):
                continue
            snap[pid] = r
        # A snapshot that lists no process is no reading - this one runs - so it is not kept for the next
        # two seconds' readers; the next one asks again (WINDOWS-LANE-4), and this one is told it could not look.
        if not snap:
            raise TableUnreadable(f"{query} listed no process", "Windows")
        self._cache, self._cache_at = snap, _time.time()
        return snap

    @staticmethod
    def _split(cmdline):
        # Windows argument splitting: quoted segments keep their spaces.
        import re as _re
        return [t.strip('"') for t in _re.findall(r'"[^"]*"|\S+', cmdline or "")]

    def all_pids(self):
        return list(self._snapshot())

    def argv(self, pid):
        r = self._snapshot().get(pid)
        if not r:
            return None
        av = self._split(r.get("CommandLine") or "")
        if not av and r.get("ExecutablePath"):
            av = [r["ExecutablePath"]]
        return av or None

    def cwd(self, pid):
        """The working directory from the process's environment block, as psutil reads it (#9).

        CIM does not expose it. OpenProcess with query and read rights, the PEB
        address from NtQueryInformationProcess, then ProcessParameters and its
        CurrentDirectory. Only a 64-bit process is read by a 64-bit interpreter:
        a 32-bit process, one this user may not open, or a read that comes back
        short is None, which every caller handles on the record (#71).
        """
        try:
            return self._environment_cwd(pid)
        except Exception:
            return None

    @staticmethod
    def _environment_cwd(pid):
        from ctypes import wintypes
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.ReadProcessMemory.restype = wintypes.BOOL
        kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                               ctypes.POINTER(ctypes.c_size_t)]
        kernel32.IsWow64Process.restype = wintypes.BOOL
        kernel32.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long
        ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                                    ctypes.POINTER(ctypes.c_ulong)]
        PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            return None
        try:
            wow64 = wintypes.BOOL()
            if not kernel32.IsWow64Process(handle, ctypes.byref(wow64)) or wow64.value:
                return None
            basic = (ctypes.c_void_p * 6)()          # PROCESS_BASIC_INFORMATION; PebBaseAddress is the second
            returned = ctypes.c_ulong(0)
            if ntdll.NtQueryInformationProcess(handle, 0, basic, ctypes.sizeof(basic), ctypes.byref(returned)) != 0:
                return None

            def read(address, length):
                buffer = ctypes.create_string_buffer(length)
                got = ctypes.c_size_t(0)
                if not kernel32.ReadProcessMemory(handle, address, buffer, length, ctypes.byref(got)) \
                        or got.value != length:
                    raise OSError("short read of another process's memory")
                return buffer.raw

            peb = basic[1]
            if not peb:
                return None
            parameters, = struct.unpack_from("<Q", read(peb + 0x20, 8))   # PEB.ProcessParameters
            length, _, text = struct.unpack_from("<HH4xQ", read(parameters + 0x38, 16))   # CurrentDirectory.DosPath
            if not length or not text:
                return None
            path = read(text, length).decode("utf-16-le")
            return path[:-1] if len(path) > 3 and path.endswith("\\") else path
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def alive(pid, kernel32=None, last_error=None):
        """Whether `pid` runs, asked of the process itself: opened and waited on for no time (WINDOWS-PID-ALIVE).

        A wait that times out is a running process, and a signalled one has exited, its pid held only by a
        handle someone keeps. Access is denied only to a process that exists, one this user may not open;
        a pid no process has is an invalid parameter. Anything else is None. `kernel32` and `last_error`
        stand in for the platform's where there is none.
        """
        if kernel32 is None:
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            last_error = ctypes.get_last_error
        SYNCHRONIZE, PROCESS_QUERY_LIMITED_INFORMATION = 0x00100000, 0x1000
        handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            error = last_error()
            return True if error == 5 else False if error == 87 else None      # ACCESS_DENIED, INVALID_PARAMETER
        try:
            state = kernel32.WaitForSingleObject(handle, 0)
            return True if state == 0x102 else False if state == 0 else None   # WAIT_TIMEOUT, WAIT_OBJECT_0
        finally:
            kernel32.CloseHandle(handle)

    def ppid(self, pid):
        """The parent pid from a Toolhelp snapshot of the process list (ANCESTOR-WINDOWS).

        The CIM snapshot the rest of this backend reads is a PowerShell run, a second or more, and it
        answers nothing where PowerShell does not run; a parent read from it for `ao lock -- ao verify`
        was then unknown, and the verify waited out the lock its own parent held. Toolhelp is the
        process list the kernel keeps, read in-process. None when it cannot be read or holds no `pid`.
        """
        from ctypes import wintypes

        class Entry(ctypes.Structure):                # PROCESSENTRY32W
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        for name in ("Process32FirstW", "Process32NextW"):
            getattr(kernel32, name).restype = wintypes.BOOL
            getattr(kernel32, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
        if snapshot is None or snapshot == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
            return None
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(Entry)
            found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32ProcessID == pid:
                    return int(entry.th32ParentProcessID)
                found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return None
        finally:
            kernel32.CloseHandle(snapshot)

    def info(self, pid):
        r = self._snapshot().get(pid)
        if not r:
            return None
        ppid = int(r.get("ParentProcessId") or 0)
        return {"ppid": ppid, "pgid": pid, "tty": None if int(r.get("SessionId") or 0) == 0 else int(r["SessionId"]),
                "start": r.get("CreationDate") or 0, "comm": r.get("Name") or ""}

    def elapsed(self, pid):
        started = _cim_epoch((self._snapshot().get(pid) or {}).get("CreationDate"))
        return None if started is None else max(0, int(time.time() - started))


# ---------------------------------------------------------------- Windows job objects (WIN-JOB)
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
CREATE_SUSPENDED = 0x00000004
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA, _PROCESS_TERMINATE = 0x0100, 0x0001
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_READ_CONTROL = 0x00020000
_SE_DACL_PRESENT = 0x0004
_ACL_REVISION = 2
_SPAWN_OS_ERRORS = (2, 3, 193, 267)
_ACTIVE_JOB = None


class _JobBasicLimitInformation(ctypes.Structure):
    """JOBOBJECT_BASIC_LIMIT_INFORMATION, as the Windows headers lay it out."""
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    """IO_COUNTERS, six unsigned 64-bit counts."""
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JobExtendedLimitInformation(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION: 144 bytes on 64-bit Windows, 112 on 32-bit."""
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry32(ctypes.Structure):
    """THREADENTRY32, as Windows' tlhelp32.h lays it out."""
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ThreadID", ctypes.c_uint32),
        ("th32OwnerProcessID", ctypes.c_uint32),
        ("tpBasePri", ctypes.c_int32),
        ("tpDeltaPri", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
    ]


class _OwnerRightsReadControlAcl(ctypes.Structure):
    """An ACL with one ACCESS_ALLOWED_ACE granting OWNER_RIGHTS (S-1-3-4) READ_CONTROL only.

    A thread started with NULL lpThreadAttributes receives the creator token's default DACL, which
    grants THREAD_SUSPEND_RESUME to any same-user process. With an explicit OWNER_RIGHTS ACE for
    READ_CONTROL alone, OpenThread for THREAD_SUSPEND_RESUME or WRITE_DAC is denied while the direct
    hThread handle from CreateProcessW keeps THREAD_ALL_ACCESS (WIN-JOB).
    """
    _fields_ = [
        ("AclRevision", ctypes.c_uint8),
        ("Sbz1", ctypes.c_uint8),
        ("AclSize", ctypes.c_uint16),
        ("AceCount", ctypes.c_uint16),
        ("Sbz2", ctypes.c_uint16),
        ("AceType", ctypes.c_uint8),
        ("AceFlags", ctypes.c_uint8),
        ("AceSize", ctypes.c_uint16),
        ("Mask", ctypes.c_uint32),
        ("SidRevision", ctypes.c_uint8),
        ("SubAuthorityCount", ctypes.c_uint8),
        ("IdentifierAuthority", ctypes.c_uint8 * 6),
        ("SubAuthority", ctypes.c_uint32),
    ]


class _SecurityDescriptor(ctypes.Structure):
    """Absolute SECURITY_DESCRIPTOR (winnt.h) with an explicit DACL."""
    _fields_ = [
        ("Revision", ctypes.c_uint8),
        ("Sbz1", ctypes.c_uint8),
        ("Control", ctypes.c_uint16),
        ("Owner", ctypes.c_void_p),
        ("Group", ctypes.c_void_p),
        ("Sacl", ctypes.c_void_p),
        ("Dacl", ctypes.c_void_p),
    ]


class _SecurityAttributes(ctypes.Structure):
    """SECURITY_ATTRIBUTES (wtypesbase.h)."""
    _fields_ = [
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int32),
    ]


class _StartupInfoW(ctypes.Structure):
    """STARTUPINFOW (processthreadsapi.h): 104 bytes on 64-bit Windows, 68 on 32-bit."""
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("lpReserved", ctypes.c_void_p),
        ("lpDesktop", ctypes.c_void_p),
        ("lpTitle", ctypes.c_void_p),
        ("dwX", ctypes.c_uint32),
        ("dwY", ctypes.c_uint32),
        ("dwXSize", ctypes.c_uint32),
        ("dwYSize", ctypes.c_uint32),
        ("dwXCountChars", ctypes.c_uint32),
        ("dwYCountChars", ctypes.c_uint32),
        ("dwFillAttribute", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("wShowWindow", ctypes.c_uint16),
        ("cbReserved2", ctypes.c_uint16),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _StartupInfoExW(ctypes.Structure):
    """STARTUPINFOEXW (winbase.h): STARTUPINFOW followed by LPPROC_THREAD_ATTRIBUTE_LIST."""
    _fields_ = [
        ("StartupInfo", _StartupInfoW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    """PROCESS_INFORMATION (processthreadsapi.h)."""
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_uint32),
        ("dwThreadId", ctypes.c_uint32),
    ]


def _locked_thread_acl():
    return _OwnerRightsReadControlAcl(
        AclRevision=_ACL_REVISION, AclSize=ctypes.sizeof(_OwnerRightsReadControlAcl), AceCount=1,
        AceType=0, AceSize=20, Mask=_READ_CONTROL,
        SidRevision=1, SubAuthorityCount=1, IdentifierAuthority=(0, 0, 0, 0, 0, 3), SubAuthority=4,
    )


def _windows_error(call, code):
    """What a Windows call refused with, in a message a person reads: the call, the code, a common name."""
    known = {5: "access denied", 6: "invalid handle", 8: "not enough memory", 87: "invalid parameter"}
    return f"{call} failed (error {code}{', ' + known[code] if code in known else ''})"


def _job_kernel32():
    """kernel32 with the job, creation and thread-resume calls' restype and argtypes set."""
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.POINTER(_ProcessInformation)]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    for name in ("Thread32First", "Thread32Next"):
        getattr(kernel32, name).restype = wintypes.BOOL
        getattr(kernel32, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


class JobObject:
    """A Windows job holding one reviewer's process tree, ended when its handle closes (WIN-JOB).

    Windows has no process group for `_stop_reviewer_leftovers` to stop, so what a reviewer started
    and left behind ran on. A job with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE ends every process assigned
    to it - and every process they start - when the last handle closes. When `_winapi.CreateProcess`
    is hooked through `creating`, the child is placed in the job at `CreateProcessW` via
    `PROC_THREAD_ATTRIBUTE_JOB_LIST` and its suspended primary thread is given an `OWNER_RIGHTS`
    `READ_CONTROL`-only DACL so no concurrent same-user process can resume it; `resume` checks that
    `ResumeThread` found a suspend count of 1; `close` may be called more than once.
    """

    def __init__(self, kernel32=None, last_error=None):
        self._kernel32 = kernel32 if kernel32 is not None else _job_kernel32()
        self._last_error = last_error if last_error is not None else getattr(ctypes, "get_last_error", lambda: 0)
        self._held = set()
        self._threads = {}
        self._deferred_threads = set()
        self.why = None
        self.handle = self._kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            self.why = _windows_error("CreateJobObjectW", self._last_error())
            self.handle = None
            return
        limits = _JobExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(self.handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                     ctypes.byref(limits), ctypes.sizeof(limits)):
            self.why = _windows_error("SetInformationJobObject", self._last_error())
            self.close()

    def _create_process(self, executable, args, proc_attrs, thread_attrs,
                        inherit_handles, creationflags, env, cwd, startupinfo):
        """Start `args` via `CreateProcessW` already in this job and with a locked thread DACL (WIN-JOB)."""
        if self.why:
            raise OSError(self.why)
        if self.handle is None:
            self.why = "the job is closed"
            raise OSError(self.why)
        attr_dict = getattr(startupinfo, "lpAttributeList", None) or {}
        handle_list = [int(h) for h in (attr_dict.get("handle_list") or [])]
        attr_count = 2 if handle_list else 1
        attr_size = ctypes.c_size_t(0)
        self._kernel32.InitializeProcThreadAttributeList(None, attr_count, 0, ctypes.byref(attr_size))
        if not attr_size.value:
            self.why = _windows_error("InitializeProcThreadAttributeList", self._last_error())
            raise OSError(self.why)
        attr_buf = ctypes.create_string_buffer(attr_size.value)
        if not self._kernel32.InitializeProcThreadAttributeList(attr_buf, attr_count, 0, ctypes.byref(attr_size)):
            self.why = _windows_error("InitializeProcThreadAttributeList", self._last_error())
            raise OSError(self.why)
        try:
            job_list = (ctypes.c_void_p * 1)(self.handle)
            if not self._kernel32.UpdateProcThreadAttribute(
                attr_buf, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.byref(job_list), ctypes.sizeof(job_list), None, None,
            ):
                self.why = _windows_error("UpdateProcThreadAttribute", self._last_error())
                raise OSError(self.why)
            handles = None
            if handle_list:
                handles = (ctypes.c_void_p * len(handle_list))(*handle_list)
                if not self._kernel32.UpdateProcThreadAttribute(
                    attr_buf, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                    ctypes.byref(handles), ctypes.sizeof(handles), None, None,
                ):
                    self.why = _windows_error("UpdateProcThreadAttribute", self._last_error())
                    raise OSError(self.why)
            si_ex = _StartupInfoExW()
            si_ex.StartupInfo.cb = ctypes.sizeof(_StartupInfoExW)
            if startupinfo is not None:
                si_ex.StartupInfo.dwFlags = int(getattr(startupinfo, "dwFlags", 0) or 0)
                si_ex.StartupInfo.wShowWindow = int(getattr(startupinfo, "wShowWindow", 0) or 0)
                si_ex.StartupInfo.hStdInput = int(getattr(startupinfo, "hStdInput", 0) or 0)
                si_ex.StartupInfo.hStdOutput = int(getattr(startupinfo, "hStdOutput", 0) or 0)
                si_ex.StartupInfo.hStdError = int(getattr(startupinfo, "hStdError", 0) or 0)
            si_ex.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p).value
            acl = _locked_thread_acl()
            sd = _SecurityDescriptor(Revision=1, Control=_SE_DACL_PRESENT, Dacl=ctypes.addressof(acl))
            thread_sa = _SecurityAttributes(
                nLength=ctypes.sizeof(_SecurityAttributes),
                lpSecurityDescriptor=ctypes.addressof(sd),
                bInheritHandle=0,
            )
            flags = int(creationflags) | CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT
            env_buf = None
            if env is not None:
                items = sorted(((os.fsdecode(k), os.fsdecode(v)) for k, v in env.items()),
                               key=lambda item: item[0].upper())
                env_text = "".join(f"{k}={v}\0" for k, v in items) + "\0"
                env_buf = ctypes.create_unicode_buffer(env_text)
                flags |= CREATE_UNICODE_ENVIRONMENT
            cmd_buf = ctypes.create_unicode_buffer(args) if args is not None else None
            pi = _ProcessInformation()
            if not self._kernel32.CreateProcessW(
                executable, cmd_buf, None, ctypes.byref(thread_sa),
                bool(inherit_handles), flags,
                ctypes.cast(env_buf, ctypes.c_void_p) if env_buf is not None else None,
                cwd, ctypes.byref(si_ex), ctypes.byref(pi),
            ):
                code = self._last_error()
                msg = _windows_error("CreateProcessW", code)
                if code in (2, 3):
                    raise FileNotFoundError(2, msg)
                if code in _SPAWN_OS_ERRORS:
                    raise OSError(code, msg)
                self.why = msg
                raise OSError(self.why)
            pid, ht = int(pi.dwProcessId), int(pi.hThread)
            self._held.add(pid)
            self._threads[pid] = ht
            self._deferred_threads.add(ht)
            return int(pi.hProcess), ht, pid, int(pi.dwThreadId)
        finally:
            self._kernel32.DeleteProcThreadAttributeList(attr_buf)

    @contextlib.contextmanager
    def creating(self, winapi=None):
        """Hook `_winapi.CreateProcess` so `Popen` puts the child in this job at creation (WIN-JOB)."""
        if winapi is None:
            winapi = getattr(subprocess, "_winapi", None)
        if winapi is None or self.why or self.handle is None:
            yield self
            return
        orig_create = winapi.CreateProcess
        orig_close = getattr(winapi, "CloseHandle", None)

        def close_handle(handle):
            if int(handle) in self._deferred_threads:
                self._deferred_threads.discard(int(handle))
                return None
            return orig_close(handle)

        winapi.CreateProcess = self._create_process
        if orig_close is not None:
            winapi.CloseHandle = close_handle
        try:
            yield self
        finally:
            winapi.CreateProcess = orig_create
            if orig_close is not None:
                winapi.CloseHandle = orig_close

    def hold(self, pid):
        """Assign `pid` to the job; None when it is held, or the platform's reason."""
        if self.why:
            return self.why
        if self.handle is None:
            return "the job is closed"
        if int(pid) in self._held:
            return None
        process = self._kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
        if not process:
            return _windows_error(f"OpenProcess for {pid}", self._last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self.handle, process):
                return _windows_error(f"AssignProcessToJobObject for {pid}", self._last_error())
        finally:
            self._kernel32.CloseHandle(process)
        self._held.add(int(pid))
        return None

    def _thread_id(self, pid):
        """The one primary thread id of `pid` from a Toolhelp thread snapshot, or (None, why)."""
        snapshot = self._kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return None, _windows_error(f"CreateToolhelp32Snapshot for {pid}", self._last_error())
        try:
            entry = _ThreadEntry32()
            entry.dwSize = ctypes.sizeof(_ThreadEntry32)
            tids = []
            found = self._kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == int(pid):
                    tids.append(int(entry.th32ThreadID))
                found = self._kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            if not tids:
                return None, f"no thread found for {pid}"
            if len(tids) > 1:
                return None, f"process {pid} already has {len(tids)} threads before resume"
            return tids[0], None
        finally:
            self._kernel32.CloseHandle(snapshot)

    def resume(self, pid):
        """Resume `pid`'s primary thread after `CREATE_SUSPENDED`, once `hold` put it in the job (WIN-JOB)."""
        if self.why:
            return self.why
        if self.handle is None:
            return "the job is closed"
        pid = int(pid)
        if pid not in self._held:
            return f"process {pid} is not in the job"
        thread = self._threads.pop(pid, None)
        if thread is not None:
            self._deferred_threads.discard(thread)
        else:
            tid, why = self._thread_id(pid)
            if why:
                return why
            thread = self._kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, tid)
            if not thread:
                return _windows_error(f"OpenThread for {pid}", self._last_error())
        try:
            previous = self._kernel32.ResumeThread(thread)
            if previous == 0xFFFFFFFF:
                return _windows_error(f"ResumeThread for {pid}", self._last_error())
            if previous != 1:
                return f"ResumeThread for {pid} found suspend count {previous}, expected 1"
        finally:
            self._kernel32.CloseHandle(thread)
        return None

    def close(self):
        """Close the job's handle: whatever of its tree still runs ends (KILL_ON_JOB_CLOSE)."""
        while self._threads:
            _, thread = self._threads.popitem()
            self._deferred_threads.discard(thread)
            self._kernel32.CloseHandle(thread)
        if self.handle is not None:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


# ---------------------------------------------------------------- tool fallbacks (ps, lsof)
class _Shell:
    def all_pids(self):
        out = _run(["ps", "-eo", "pid="])
        pids = [int(x) for x in out.split() if x.isdigit()]
        if not pids:
            # A ps that could not run listed no process, which read as none running (WINDOWS-TABLE-CLOSED).
            raise TableUnreadable(getattr(out, "why", None) or "ps listed no process")
        return pids

    def argv(self, pid):
        out = _run(["ps", "-o", "args=", "-p", str(pid)]).strip()
        return out.split() if out else None            # lossy: spaces in paths split

    def cwd(self, pid):
        for line in _run(["lsof", "-w", "-n", "-P", "-a", "-d", "cwd", "-Fn", "-p", str(pid)]).split("\n"):
            if line.startswith("n"):
                return line[1:]
        return None

    def info(self, pid):
        out = _run(["ps", "-o", "ppid=,pgid=,tty=,lstart=,comm=", "-p", str(pid)]).strip().split(None, 3)
        if len(out) < 3:
            return None
        return {"ppid": int(out[0]), "pgid": int(out[1]), "tty": None if out[2] in ("??", "?", "-") else out[2],
                "start": 0, "comm": out[3] if len(out) > 3 else ""}

    def elapsed(self, pid):
        return _clock_seconds(_run(["ps", "-o", "etime=", "-p", str(pid)]))


def _backend():
    global _NATIVE
    if _NATIVE is not None:
        return _NATIVE
    if sys.platform == "win32":
        # Windows has no ps or lsof to fall back on, and the ps of Git for Windows numbers its own
        # processes: its backend stays, whatever its first reading gave. That reading is a PowerShell
        # run, and one taken while a test stood in for subprocess failed, so every process read after it
        # in that suite went unread (WINDOWS-LANE-4).
        _NATIVE = _Windows()
        return _NATIVE
    cand = None
    try:
        if sys.platform == "darwin":
            cand = _Darwin()
        elif sys.platform.startswith("linux") and os.path.isdir("/proc"):
            cand = _Linux()
        # Self-check against the one process we know everything about: this one.
        if cand is not None:
            me = os.getpid()
            info = cand.info(me) or {}
            here = cand.cwd(me)
            ok = info.get("ppid") == os.getppid() and bool(cand.argv(me)) and \
                (here is None or os.path.normcase(os.path.normpath(here))
                 == os.path.normcase(os.path.normpath(os.getcwd())))
            if not ok:
                cand = None
    except Exception:
        cand = None
    _NATIVE = cand or _Shell()
    return _NATIVE


def refresh():
    """Invalidate a cached process snapshot before observing a just-spawned pid."""
    invalidate = getattr(_backend(), "invalidate", None)
    if invalidate:
        invalidate()


def native():
    """True when the platform API answers (not the ps and lsof fallback)."""
    return not isinstance(_backend(), _Shell)


def all_pids():
    return _backend().all_pids()


def argv(pid):
    return _backend().argv(pid)


def cwd(pid):
    return _backend().cwd(pid)


def zombie(pid):
    """Has this process exited and only waits to be reaped? It holds a pid and no work.

    A container whose first process never reaps, or a parent that has not yet waited,
    leaves one behind, and `kill(pid, 0)` still succeeds on it. Windows has none.
    """
    if os.name == "nt":
        return False
    probe = getattr(_backend(), "zombie", None)
    try:
        answer = probe(pid) if probe else None
    except Exception:
        answer = None
    if answer is None:
        answer = _run(["ps", "-o", "stat=", "-p", str(int(pid))]).strip().startswith("Z")
    return answer


def info(pid):
    return _backend().info(pid)


def alive(pid):
    """Whether process `pid` runs, as Windows answers for the process itself; None where it does not answer.

    Only Windows asks here (WINDOWS-PID-ALIVE): signal 0 is no probe there, and the snapshot its backend
    reads is kept two seconds, so a process started inside them was not in it and read as gone. None
    elsewhere, where `kill(pid, 0)` asks the process.
    """
    if sys.platform != "win32":
        return None
    try:
        pid = int(pid)
        return _Windows.alive(pid) if pid > 0 else False
    except Exception:
        return None


@contextlib.contextmanager
def creating_in_job(enabled=True, kernel32=None, last_error=None, winapi=None):
    """Create the Windows job before `Popen` and hook `_winapi.CreateProcess` so the child starts inside it (WIN-JOB)."""
    global _ACTIVE_JOB
    if not enabled or sys.platform != "win32":
        yield None
        return
    job = JobObject(kernel32, last_error)
    previous = _ACTIVE_JOB
    _ACTIVE_JOB = job
    try:
        with job.creating(winapi=winapi):
            yield job
    finally:
        _ACTIVE_JOB = previous
        if _ACTIVE_JOB is None and job.handle is not None and not job._held and not job._threads:
            job.close()


def hold_in_job(pid, kernel32=None, last_error=None):
    """A job holding `pid` and what it starts, resumed after assignment and killed when it closes (WIN-JOB), or (None, why).

    None and None where this is not Windows: there a process group is stopped instead, by
    `_stop_reviewer_leftovers`. When started under `creating_in_job`, the process is placed in the job at
    `CreateProcessW` with `PROC_THREAD_ATTRIBUTE_JOB_LIST` and a locked thread DACL, then resumed here from
    its direct thread handle. A job the platform refused - or whose process was already resumed or could not
    be resumed - is closed, ending the process and leaking no handle, and the reason is the caller's to say.
    """
    if sys.platform != "win32":
        return None, None
    job = (_ACTIVE_JOB if (_ACTIVE_JOB is not None and kernel32 is None and last_error is None)
           else JobObject(kernel32, last_error))
    why = job.hold(pid) or job.resume(pid)
    if why:
        job.close()
        return None, why
    return job, None


def ppid(pid):
    """The pid of the process that started `pid`, or None where the platform does not say (ANCESTOR-WINDOWS)."""
    reader = getattr(_backend(), "ppid", None)
    try:
        parent = reader(int(pid)) if reader else None
        if parent is None:
            parent = (info(int(pid)) or {}).get("ppid")
    except Exception:
        return None
    return None if parent is None else int(parent)


def elapsed(pid):
    """Whole seconds since the process started, as `ps -o etime` counts them; None when that cannot be read."""
    try:
        return _backend().elapsed(pid)
    except Exception:
        return None


def table():
    """{pid: (ppid, pgid, tty)} for every process; tty is "??" when there is none,
    matching the shape the older ps-based code produced.

    TableUnreadable where the platform lists no process, or lists processes of which none can be
    read: either is a table it could not read, never one in which nothing runs (WINDOWS-TABLE-CLOSED).
    """
    out = {}
    b = _backend()
    pids = b.all_pids()
    for pid in pids:
        i = b.info(pid)
        if i:
            out[pid] = (i["ppid"], i["pgid"], "??" if i["tty"] is None else str(i["tty"]))
    if pids and not out:
        raise TableUnreadable(f"it lists {len(pids)} processes and none of them could be read")
    return out
