"""Crash-safe local persistence primitives for AO's append-only ledgers.

The ledgers are authority evidence, not logging.  A caller may act on a row as
soon as this module returns, so returning means all bytes reached ``fsync`` and,
for a newly-created file, the containing directory entry did too.  The API stays
standard-library-only and uses an OS lock to keep independent AO processes from
interleaving JSON records.
"""
from contextlib import contextmanager
import errno
import json
import os
import time

UTF8 = "utf-8"


class LedgerCorruption(RuntimeError):
    """A complete JSONL record is malformed; silently skipping it is unsafe."""


class LedgerLockTimeout(TimeoutError):
    """Another process held a ledger lock past the caller's deadline."""


def _call(checkpoint, step):
    if checkpoint is not None:
        checkpoint(step)


@contextmanager
def _exclusive_lock(path, timeout=10.0):
    """Cross-platform advisory lock on one byte of a sidecar file."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    handle = open(path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise LedgerLockTimeout(f"timed out locking {path}")
                time.sleep(0.02)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _read_jsonl_unlocked(path, allow_partial_tail=True):
    """Parse a ledger while the caller owns its lock."""
    try:
        data = open(path, "rb").read()
    except FileNotFoundError:
        return []
    rows = []
    lines = data.splitlines(keepends=True)
    for index, raw in enumerate(lines):
        complete = raw.endswith((b"\n", b"\r"))
        body = raw.rstrip(b"\r\n")
        if not body:
            continue
        try:
            rows.append(json.loads(body.decode(UTF8)))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            is_tail = index == len(lines) - 1
            if allow_partial_tail and is_tail and not complete:
                break
            raise LedgerCorruption(
                f"malformed JSONL record {index + 1} in {path}: {exc}"
            ) from exc
    return rows


def read_jsonl(path, allow_partial_tail=True, timeout=10.0):
    """Read committed JSONL records and fail on corruption before the tail.

    Readers share the writer's sidecar lock so they cannot observe a complete
    row in the interval after ``write`` but before ``fsync``. A killed process
    may still leave the final line without its newline; readers may ignore that
    one incomplete tail, while malformed complete rows always fail closed.
    """
    if not os.path.exists(path):
        return []
    with _exclusive_lock(path + ".lock", timeout=timeout):
        return _read_jsonl_unlocked(path, allow_partial_tail)


def _sync_directory(directory, fsync):
    """Persist a newly-created directory entry where the platform supports it."""
    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(directory or ".", flags)
    try:
        fsync(fd)
    except OSError as exc:
        # Some filesystems expose directories but reject fsync.  This is a
        # platform capability limit, not evidence that data was persisted.
        if exc.errno in (errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", -1)):
            return False
        raise
    finally:
        os.close(fd)
    return True


def _repair_partial_tail(path, fsync, checkpoint):
    """Salvage a complete unterminated row or truncate a partial final row."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return "clean"

    # Validate every complete row before touching the tail. Without this pass an
    # invalid earlier row followed by a partial tail could be retained while a
    # new record was appended, falsely reporting a successful durable write on
    # an already-corrupt ledger.
    data = open(path, "rb").read()
    _read_jsonl_unlocked(path, allow_partial_tail=True)
    if data.endswith(b"\n"):
        return "clean"

    boundary = data.rfind(b"\n") + 1
    tail = data[boundary:]
    try:
        json.loads(tail.decode(UTF8))
        salvage = True
    except (UnicodeDecodeError, json.JSONDecodeError):
        salvage = False

    with open(path, "r+b", buffering=0) as handle:
        if salvage:
            handle.seek(0, os.SEEK_END)
            handle.write(b"\n")
            action = "completed-tail"
        else:
            handle.truncate(boundary)
            action = "truncated-tail"
        fsync(handle.fileno())
    _call(checkpoint, action)
    return action


def append_jsonl(path, record, timeout=10.0, *, _checkpoint=None,
                 _write=None, _fsync=None):
    """Append one JSON object and durably persist it before returning.

    ``_checkpoint``, ``_write`` and ``_fsync`` are deliberately private test
    seams.  They let the suite stop a real subprocess between storage barriers
    and inject short writes or I/O failures without a production crash switch.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(UTF8)
    write = _write or os.write
    fsync = _fsync or os.fsync

    with _exclusive_lock(path + ".lock", timeout=timeout):
        _call(_checkpoint, "locked")
        _repair_partial_tail(path, fsync, _checkpoint)
        created = not os.path.exists(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o644)
        try:
            _call(_checkpoint, "opened")
            remaining = memoryview(payload)
            while remaining:
                try:
                    count = write(fd, remaining)
                except InterruptedError:
                    continue
                if not isinstance(count, int) or count <= 0 or count > len(remaining):
                    raise OSError(errno.EIO, "append made no forward progress")
                remaining = remaining[count:]
            _call(_checkpoint, "written")
            fsync(fd)
            _call(_checkpoint, "fsynced")
        finally:
            os.close(fd)

        if created and _sync_directory(parent, fsync):
            _call(_checkpoint, "directory-fsynced")
    return record
