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

_NATIVE = None          # decided on first use: "darwin" | "linux" | None (shell fallbacks)


def _sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


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

    def info(self, pid):
        try:
            stat = open(f"/proc/{pid}/stat").read()
        except OSError:
            return None
        rest = stat[stat.rindex(")") + 2:].split()
        # fields after comm: state ppid pgrp session tty_nr … starttime(22nd overall = index 19 here)
        ppid, pgid, tty = int(rest[1]), int(rest[2]), int(rest[4])
        return {"ppid": ppid, "pgid": pgid, "tty": None if tty == 0 else tty,
                "start": int(rest[19]) if len(rest) > 19 else 0,
                "comm": stat[stat.index("(") + 1:stat.rindex(")")]}


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
        # Self-check against the one process we know everything about: this one.
        if cand is not None:
            me = os.getpid()
            ok = (cand.cwd(me) == os.getcwd() and (cand.info(me) or {}).get("ppid") == os.getppid()
                  and bool(cand.argv(me)))
            if not ok:
                cand = None
    except Exception:
        cand = None
    _NATIVE = cand or _Shell()
    return _NATIVE


def native():
    """True when the platform API answers (not the shell fallback)."""
    return not isinstance(_backend(), _Shell)


def all_pids():
    return _backend().all_pids()


def argv(pid):
    return _backend().argv(pid)


def cwd(pid):
    return _backend().cwd(pid)


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
