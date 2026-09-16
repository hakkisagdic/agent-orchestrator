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

Every function falls back to the old shell commands when the native path is
unavailable or fails its self-check, so nothing here can make ao blind.
"""
import ctypes
import ctypes.util
import os
import struct
import subprocess
import sys
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

_NATIVE = None          # decided on first use: "darwin" | "linux" | None (shell fallbacks)


def _sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding=UTF8, errors="replace", timeout=30).stdout
    except Exception:
        return ""


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
    for line in _sh("ps -A -o pgid=,time=").splitlines():
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
        n = self.libproc.proc_listpids(self.PROC_ALL_PIDS, 0, None, 0)
        buf = (ctypes.c_int * (n // 4 + 64))()
        n = self.libproc.proc_listpids(self.PROC_ALL_PIDS, 0, buf, ctypes.sizeof(buf))
        return [p for p in buf[:n // 4] if p > 0]

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
        return [int(d) for d in os.listdir("/proc") if d.isdigit()]

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

    def invalidate(self):
        self._cache = None
        self._cache_at = 0.0

    def _snapshot(self):
        import json as _json
        import time as _time
        if self._cache is not None and _time.time() - self._cache_at < 2.0:
            return self._cache
        cmd = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,"
               "ExecutablePath,Name,SessionId,CreationDate | ConvertTo-Json -Compress")
        out = _sh(f'powershell -NoProfile -NonInteractive -Command "{cmd}"')
        rows = []
        try:
            data = _json.loads(out) if out.strip() else []
            rows = data if isinstance(data, list) else [data]
        except ValueError:
            rows = []
        snap = {}
        for r in rows:
            try:
                pid = int(r.get("ProcessId"))
            except (TypeError, ValueError):
                continue
            snap[pid] = r
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

    def info(self, pid):
        r = self._snapshot().get(pid)
        if not r:
            return None
        ppid = int(r.get("ParentProcessId") or 0)
        return {"ppid": ppid, "pgid": pid, "tty": None if int(r.get("SessionId") or 0) == 0 else int(r["SessionId"]),
                "start": r.get("CreationDate") or 0, "comm": r.get("Name") or ""}


# ---------------------------------------------------------------- shell fallbacks
class _Shell:
    def all_pids(self):
        return [int(x) for x in _sh("ps -eo pid=").split() if x.isdigit()]

    def argv(self, pid):
        out = _sh(f"ps -o args= -p {pid}").strip()
        return out.split() if out else None            # lossy: spaces in paths split

    def cwd(self, pid):
        for line in _sh(f"lsof -w -n -P -a -d cwd -Fn -p {pid}").split("\n"):
            if line.startswith("n"):
                return line[1:]
        return None

    def info(self, pid):
        out = _sh(f"ps -o ppid=,pgid=,tty=,lstart=,comm= -p {pid}").strip().split(None, 3)
        if len(out) < 3:
            return None
        return {"ppid": int(out[0]), "pgid": int(out[1]), "tty": None if out[2] in ("??", "?", "-") else out[2],
                "start": 0, "comm": out[3] if len(out) > 3 else ""}


def _backend():
    global _NATIVE
    if _NATIVE is not None:
        return _NATIVE
    cand = None
    try:
        if sys.platform == "darwin":
            cand = _Darwin()
        elif sys.platform.startswith("linux") and os.path.isdir("/proc"):
            cand = _Linux()
        elif sys.platform == "win32":
            cand = _Windows()
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
    """True when the platform API answers (not the shell fallback)."""
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
        answer = _sh(f"ps -o stat= -p {int(pid)}").strip().startswith("Z")
    return answer


def info(pid):
    return _backend().info(pid)


def table():
    """{pid: (ppid, pgid, tty)} for every process; tty is "??" when there is none,
    matching the shape the older ps-based code produced."""
    out = {}
    b = _backend()
    for pid in b.all_pids():
        i = b.info(pid)
        if i:
            out[pid] = (i["ppid"], i["pgid"], "??" if i["tty"] is None else str(i["tty"]))
    return out
