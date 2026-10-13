"""Crash-safe local persistence primitives for AO's append-only ledgers.

The ledgers are authority evidence, not logging. A caller may act on a row as
soon as this module returns, so returning means all bytes reached ``fsync`` and,
for a newly-created file, the containing directory entry did too. The API stays
standard-library-only and uses an OS lock to keep independent AO processes from
interleaving JSON records or selecting competing hash-chain predecessors.
"""
from contextlib import contextmanager
import errno
import hashlib
import json
import os
import time

UTF8 = "utf-8"
CHAIN_PREVIOUS_FIELD = "previous"


class LedgerCorruption(RuntimeError):
    """A committed ledger record is malformed or violates its hash chain."""


class LedgerLockTimeout(TimeoutError):
    """Another process held a ledger lock past the caller's deadline.

    `path` is the lock and `timeout` the seconds waited for it, so a command can say in one
    line which lock stood in its way (CLI-ROBUST).
    """

    def __init__(self, message, path=None, timeout=None):
        super().__init__(message)
        self.path, self.timeout = path, timeout


class LockThroughLink(OSError):
    """A lock file that is a link, or not a regular file: ao takes no lock through it (LOCK-LINKS)."""


def _call(checkpoint, step):
    if checkpoint is not None:
        checkpoint(step)


def _open_lock_nt(path, refused):
    """A Windows lock file's handle, opened as the link itself where the path is one, never through it (LOCK-LINKS).

    FILE_FLAG_OPEN_REPARSE_POINT opens a link, not what it names, so OPEN_ALWAYS makes nothing through one; the
    caller refuses the handle when it is a reparse point. A link Windows will not open that way is refused here.
    """
    import ctypes
    import msvcrt
    import stat as _stat
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create, close = kernel32.CreateFileW, kernel32.CloseHandle
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close.argtypes = (wintypes.HANDLE,)
    generic_read_write, share_read_write, open_always = 0x80000000 | 0x40000000, 0x1 | 0x2, 4
    normal_open_reparse_point = 0x80 | 0x00200000
    raw = create(path, generic_read_write, share_read_write, None, open_always, normal_open_reparse_point, None)
    if raw is None or raw == wintypes.HANDLE(-1).value:
        error = ctypes.WinError(ctypes.get_last_error())
        try:
            linked = _stat.S_ISLNK(os.lstat(path).st_mode)
        except OSError:
            linked = False
        if linked:
            raise LockThroughLink(refused) from error
        raise error
    try:
        fd = msvcrt.open_osfhandle(raw, os.O_APPEND)
    except BaseException:
        close(raw)
        raise
    try:
        return open(fd, "a+b", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def _open_lock(path):
    """A lock file's handle: the file reached through no final link, a regular file of one name; LockThroughLink
    otherwise.

    A lock was opened "a+b", which follows a final link and creates the file it names: a link put where a lock is
    kept - `.ao/` in the working tree holds several - had ao create a file wherever the link pointed and write a
    byte into it, outside the tree an agent may write. On POSIX a lock is opened with O_NOFOLLOW, and without
    blocking, so a FIFO put in its place is refused at once rather than waited on; a POSIX platform without
    O_NOFOLLOW takes no lock. Windows has no O_NOFOLLOW, and looking at the path before and after a creating open
    left that open to follow a link put there in between: there the path is opened as the link itself
    (`_open_lock_nt`), so nothing is made through one. A link, anything but a regular file, or a file that has
    another name - a hard link put in the lock's place - is then refused before anything is written (LOCK-LINKS).
    """
    import stat as _stat
    refused = (f"{path} is a link, not a regular file, or a file with another name: ao takes no lock through it; "
               "remove it, and ao makes the lock again")
    if os.name == "nt":
        handle = _open_lock_nt(path, refused)
    else:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise LockThroughLink(f"{path}: this platform opens no file through no link (it has no O_NOFOLLOW), "
                                  "and ao takes no lock it cannot open that way")
        flags = nofollow | getattr(os, "O_NONBLOCK", 0)
        try:
            handle = open(path, "a+b", buffering=0, opener=lambda name, mode: os.open(name, mode | flags, 0o666))
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise LockThroughLink(refused) from exc     # what O_NOFOLLOW answers a final link with
            raise
    try:
        opened = os.fstat(handle.fileno())
        reparse = getattr(opened, "st_file_attributes", 0) & getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if not _stat.S_ISREG(opened.st_mode) or reparse or opened.st_nlink != 1:
            raise LockThroughLink(refused)
    except BaseException:
        handle.close()
        raise
    return handle


def _lock_still_named(path, handle):
    """Whether `path` still names, through no link, the one file `handle` holds (LOCK-LINKS)."""
    import stat as _stat
    try:
        at = os.lstat(path)
    except FileNotFoundError:
        return False
    held = os.fstat(handle.fileno())
    if _stat.S_ISLNK(at.st_mode) or held.st_nlink != 1:
        return False
    if not at.st_ino or not held.st_ino:
        return True         # a file system that numbers no file cannot tell, and has no links to swap in
    return (at.st_dev, at.st_ino) == (held.st_dev, held.st_ino)


def _let_go(handle):
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


@contextmanager
def _exclusive_lock(path, timeout=10.0):
    """Cross-platform advisory lock on one byte of a sidecar file.

    The byte is written once, by whichever process finds the file empty. On Windows a lock is mandatory for
    writes, and another process could take the file between this one's look and its write: the write then
    failed with PermissionError, as four processes appending to a new ledger at once showed on the Windows
    lane. The file is no longer empty then, which is all the write was for, and the handle buffers nothing,
    so nothing is written again when it closes (WINDOWS-LOCK-WRITE).

    A lock is held only while its path still names the file locked: a file whose name was taken away, or given
    to another file, after it was opened - an agent writes `.ao/` - is let go and the path's file taken instead,
    where two of ao's writers each held a file of the one name. A lock in the tree orders ao's own writers; an
    agent that replaces one while it is held could already write the ledger it guards, which the ledger's chain
    and checkpoints show (LOCK-LINKS).
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        handle = _open_lock(path)
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                try:
                    handle.write(b"\0")
                except PermissionError:
                    pass
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
                        raise LedgerLockTimeout(f"timed out locking {path}", path, timeout)
                    time.sleep(0.02)
            named = _lock_still_named(path, handle)
        except BaseException:
            _let_go(handle)
            raise
        if named:
            break
        _let_go(handle)
        if time.monotonic() >= deadline:
            raise LedgerLockTimeout(f"timed out locking {path}: its file was replaced as it was taken", path,
                                    timeout)
    try:
        yield
    finally:
        _let_go(handle)


def _read_jsonl_unlocked(path, allow_partial_tail=True):
    """Parse a ledger while the caller owns its lock.

    A row is committed by its newline. A final line without one - even valid
    JSON - is what an interrupted or failed append leaves, and is never a row:
    a grant whose append raised must not be read back as authority (#68).
    """
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
        if not complete:
            if allow_partial_tail:
                break
            raise LedgerCorruption(f"uncommitted final record {index + 1} in {path}")
        try:
            rows.append(json.loads(body.decode(UTF8)))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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


def chained_row_digest(record, chain):
    """Canonical, domain-separated SHA-256 digest of one chained JSON object."""
    if not isinstance(record, dict):
        raise TypeError("a chained ledger row must be a JSON object")
    if not isinstance(chain, str) or not chain:
        raise ValueError("a chained ledger requires a non-empty domain")
    canonical = json.dumps(
        record,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    domain = chain.encode(UTF8)
    digest = hashlib.sha256()
    digest.update(domain)
    digest.update(b"\0")
    digest.update(canonical)
    return "sha256:" + digest.hexdigest()


def checkpoint_path():
    """Where chained ledgers record their committed length: outside the repository (#62).

    A hash chain detects a changed or inserted row but not a removed tail, because
    every prefix of a valid chain is itself valid; deleting the newest grants, or
    the whole file, left a ledger that passed every check. The recorded length is
    kept apart from the ledger, so removing rows from the file does not remove the
    record of how many there were.
    """
    return (os.environ.get("AO_LEDGER_CHECKPOINTS")
            or os.path.join(os.path.expanduser("~"), ".ao", "ledger-checkpoints.json"))


def _load_committed_lengths():
    try:
        with open(checkpoint_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# What Windows answers, for a moment, when a rename would replace a file another handle
# holds open: ERROR_ACCESS_DENIED and ERROR_SHARING_VIOLATION.
_WINDOWS_IN_USE = (5, 32)
_REPLACE_ATTEMPTS = 8
_REPLACE_FIRST_WAIT = 0.01


def _replace(source, target):
    """``os.replace``, retried with backoff for about a second while Windows reports the target in use (#71).

    A chained read opens the checkpoint store under its own ledger's lock, not the
    store's, and Windows does not rename over a file another handle holds open: an
    append to one ledger failed while another ledger was being read, and the row it had
    written was taken back. A virus scanner holds a new file open the same way. Every
    replace in this module comes here. Only those two Windows errors are retried, a
    bounded number of times, and the last one still reaches the caller; POSIX renames
    over an open file, and a PermissionError there is raised at once, as before.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            return os.replace(source, target)
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in _WINDOWS_IN_USE or attempt + 1 == _REPLACE_ATTEMPTS:
                raise
            time.sleep(_REPLACE_FIRST_WAIT * 2 ** attempt)


def _write_committed_lengths(data):
    """Replace the checkpoint store whole; the caller holds its lock.

    Through replace_file_durably, under a name no file had and never through a link: `<store>.<pid>.tmp`, opened to
    truncate, wrote the target of a link left at that name, and AO_LEDGER_CHECKPOINTS can name a store in a folder an
    agent writes (STORAGE-TMP).
    """
    replace_file_durably(checkpoint_path(), json.dumps(data, indent=1, sort_keys=True).encode("utf-8"))


def _record_committed_length(path, count, digest, sealed=None):
    store = checkpoint_path()
    os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    with _exclusive_lock(store + ".lock"):
        data = _load_committed_lengths()
        key = os.path.realpath(path)
        mark = {"count": count, "digest": digest, "at": int(time.time())}
        previous = data.get(key) if isinstance(data.get(key), dict) else {}
        # A seal is recorded outside the repository too, so one written inside it
        # cannot retire rows this machine never retired (#50).
        if sealed is not None or previous.get("sealed"):
            mark["sealed"] = sealed if sealed is not None else previous["sealed"]
        data[key] = mark
        _write_committed_lengths(data)


def _check_committed_length(path, rows, chain, seal=None):
    """Refuse a ledger shorter than its recorded length, or diverging from it (#62), across a seal (#50)."""
    mark = _load_committed_lengths().get(os.path.realpath(path))
    if not isinstance(mark, dict) or not isinstance(mark.get("count"), int) or mark["count"] <= 0:
        return
    recorded = mark.get("sealed")
    if recorded or seal:
        same = bool(recorded and seal) and recorded.get("retired") == seal.get("retired") \
            and recorded.get("digest") == seal.get("digest")
        if not same:
            raise LedgerCorruption(
                f"{path} carries a seal this machine did not record, or lost the one it did "
                f"(the record is kept in {checkpoint_path()})"
            )
    retired = seal["retired"] if seal else 0
    count = mark["count"]
    if retired + len(rows) < count:
        raise LedgerCorruption(
            f"{path} holds {retired + len(rows)} committed rows but recorded {count}: rows were removed from its end"
            f" (the count is kept in {checkpoint_path()})"
        )
    if count <= retired:
        if count == retired and seal.get("digest") == mark.get("digest"):
            return
        raise LedgerCorruption(f"{path} row {count} is sealed but does not match the digest recorded for it")
    if chained_row_digest(rows[count - retired - 1], chain) != mark.get("digest"):
        raise LedgerCorruption(
            f"{path} row {count} does not match the digest recorded for it in {checkpoint_path()}"
        )


def seal_path(path):
    return path + ".seal.json"


def _load_seal(path):
    """The seal that retired a ledger's prefix, or None (#50)."""
    try:
        with open(seal_path(path), encoding="utf-8") as handle:
            seal = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise LedgerCorruption(f"the seal of {path} cannot be read: {exc}") from exc
    if not isinstance(seal, dict) or not isinstance(seal.get("retired"), int) or seal["retired"] < 1 \
            or not isinstance(seal.get("digest"), str):
        raise LedgerCorruption(f"the seal of {path} is malformed")
    return seal


def _settle_seal(path, chain, previous_field):
    """Finish or undo a seal a crash interrupted; the caller holds the ledger lock (#50).

    A seal writes its intent before it rewrites the ledger. If the ledger already
    starts after the retired rows, the seal is completed; otherwise the intent is
    dropped and the ledger is as it was.
    """
    pending = seal_path(path) + ".pending"
    if not os.path.exists(pending):
        return
    with open(pending, encoding="utf-8") as handle:
        intent = json.load(handle)
    rows = _read_jsonl_unlocked(path, allow_partial_tail=False) if os.path.exists(path) else []
    if rows and isinstance(rows[0], dict) and rows[0].get(previous_field) == intent["digest"]:
        last = rows[-1]
        _record_committed_length(path, intent["retired"] + len(rows), chained_row_digest(last, chain),
                                 sealed={"retired": intent["retired"], "digest": intent["digest"]})
        _replace(pending, seal_path(path))
        _sync_directory(os.path.dirname(path) or ".", os.fsync)
    else:
        os.remove(pending)


def seal_chained_jsonl(path, chain, keep, *, previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False):
    """Retire all but the newest `keep` rows of a chained ledger into an archive, never deleting one (#50).

    The retired rows are written whole to `sealed/<ledger>.<first>-<last>.jsonl`
    beside the ledger. The seal names how many rows it retired and the digest of
    the last of them, which becomes the genesis the first kept row links to, and
    the same seal is recorded outside the repository with the committed length.
    A sealed ledger and the unsealed one give every reader the same newest rows.
    Returns the seal, or None when there is nothing to retire.
    """
    if keep < 1:
        raise ValueError("a seal keeps at least one row")
    with _exclusive_lock(path + ".lock"):
        _settle_seal(path, chain, previous_field)
        if not os.path.exists(path):
            return None
        rows = _read_jsonl_unlocked(path, allow_partial_tail=False)
        seal = _load_seal(path)
        _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix, seal=seal)
        _check_committed_length(path, rows, chain, seal=seal)
        if len(rows) <= keep:
            return None
        retire, kept = rows[:-keep], rows[-keep:]
        if any(not isinstance(row, dict) or previous_field not in row for row in kept):
            return None                        # rows from before the chain cannot start a sealed ledger
        base = seal["retired"] if seal else 0
        first, last = base + 1, base + len(retire)
        archive = os.path.join(os.path.dirname(path) or ".", "sealed", f"{os.path.basename(path)}.{first}-{last}.jsonl")
        replace_file_durably(archive, b"".join(_encode_jsonl(row) for row in retire))
        intent = {"chain": chain, "retired": last, "digest": chained_row_digest(retire[-1], chain),
                  "archives": list((seal or {}).get("archives") or []) + [os.path.basename(archive)],
                  "at": int(time.time())}
        replace_file_durably(seal_path(path) + ".pending", (json.dumps(intent, sort_keys=True) + "\n").encode(UTF8))
        replace_file_durably(path, b"".join(_encode_jsonl(row) for row in kept))
        _settle_seal(path, chain, previous_field)
        return _load_seal(path)


def sealed_rows(path):
    """Every row a seal retired from a ledger, oldest first, read from its archives (#50)."""
    seal = _load_seal(path)
    rows = []
    for name in (seal or {}).get("archives") or []:
        rows.extend(_read_jsonl_unlocked(os.path.join(os.path.dirname(path) or ".", "sealed", name),
                                         allow_partial_tail=False))
    return rows


def _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix=False, seal=None):
    expected = None
    start = 0
    offset = 0
    if seal is not None:
        # A sealed ledger's first row links to the last row the seal retired (#50).
        expected, offset = seal["digest"], seal["retired"]
    elif legacy_prefix:
        # Rows written before a ledger was chained carry no link. They may only
        # come first, and the first linked row names the digest of the last one.
        while start < len(rows) and isinstance(rows[start], dict) \
                and previous_field not in rows[start]:
            start += 1
        if start:
            try:
                expected = chained_row_digest(rows[start - 1], chain)
            except (TypeError, ValueError) as exc:
                raise LedgerCorruption(
                    f"cannot digest chained JSONL record {start} in {path}: {exc}"
                ) from exc
    for index, row in enumerate(rows[start:], start + 1 + offset):
        if not isinstance(row, dict):
            raise LedgerCorruption(
                f"chained JSONL record {index} in {path} is not an object"
            )
        if previous_field not in row:
            raise LedgerCorruption(
                f"chained JSONL record {index} in {path} has no {previous_field!r} field"
            )
        actual = row.get(previous_field)
        if actual != expected:
            raise LedgerCorruption(
                f"broken hash chain at record {index} in {path}: "
                f"expected {expected!r}, got {actual!r}"
            )
        if "ordinal" in row and row.get("ordinal") != index:
            raise LedgerCorruption(
                f"chained JSONL record {index} in {path} carries ordinal {row.get('ordinal')!r}"
            )
        try:
            expected = chained_row_digest(row, chain)
        except (TypeError, ValueError) as exc:
            raise LedgerCorruption(
                f"cannot digest chained JSONL record {index} in {path}: {exc}"
            ) from exc
    return rows


def read_chained_jsonl(path, chain, allow_partial_tail=True, timeout=10.0, *,
                       previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False):
    """Read and validate every committed row in one predecessor hash chain.

    The chain proves no row was changed or inserted; the recorded length proves
    none was cut off the end, including all of them (#62).
    """
    if not os.path.exists(path) and not os.path.exists(seal_path(path) + ".pending"):
        _check_committed_length(path, [], chain, seal=_load_seal(path))
        return []
    with _exclusive_lock(path + ".lock", timeout=timeout):
        _settle_seal(path, chain, previous_field)
        seal = _load_seal(path)
        rows = _read_jsonl_unlocked(path, allow_partial_tail) if os.path.exists(path) else []
        _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix, seal=seal)
        _check_committed_length(path, rows, chain, seal=seal)
        return rows


def _sync_directory(directory, fsync):
    """Persist a newly-created directory entry where the platform supports it."""
    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(directory or ".", flags)
    try:
        fsync(fd)
    except OSError as exc:
        # Some filesystems expose directories but reject fsync. This is a
        # platform capability limit, not evidence that data was persisted.
        if exc.errno in (errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", -1)):
            return False
        raise
    finally:
        os.close(fd)
    return True


def replace_file_durably(path, data, *, mode=None, _checkpoint=None, _fsync=None):
    """Replace a whole file so it is only ever seen with its old bytes or its new ones.

    ``open(path, "w")`` truncates first. A crash, kill or full disk before the new
    bytes are down leaves a zero-byte file, and a zero-byte ``.ao/config.json`` no
    longer holds the ``capability_matrix`` that keeps strict authority on (#56).
    This writes a temporary file beside the target, fsyncs it, renames it over the
    target and fsyncs the directory. ``_checkpoint`` and ``_fsync`` are the private
    test seams the ledger appends take.

    ``mode`` is for a file that holds a credential. Its temporary file is created
    with that mode, under a name no file had (``O_EXCL``, and ``O_NOFOLLOW`` where
    the platform has it), so the bytes are never readable by anyone the mode leaves
    out - not while they are written, and not when a write is cut short - and a file
    or a link already at that name is never written through. Written in place and
    chmod-ed after, ``~/.ao/email.json`` was readable by every user of the machine
    meanwhile, and stayed so when the process stopped between the two
    (HUNT-LEADS-SEC). Without ``mode`` the file keeps the mode it had, or 0644.

    Every temporary file is made that way, with or without ``mode``. Without one it
    was ``.<name>.<pid>.tmp``, opened to truncate and through any link: an agent that
    left a link at that name for a likely pid in a folder ao writes - ``.ao/`` among
    them - had ao truncate and write the link's target, a file of the person's
    (STORAGE-TMP).
    """
    fsync = _fsync or os.fsync
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o7777
        except OSError:
            mode = 0o644
    temporary = os.path.join(parent, f".{os.path.basename(path)}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    opened = False
    try:
        fd = os.open(temporary, flags, mode)
        opened = True
        try:
            _call(_checkpoint, "temporary-opened")
            remaining = memoryview(data)
            while remaining:
                try:
                    count = os.write(fd, remaining)
                except InterruptedError:
                    continue
                if count <= 0:
                    raise OSError(errno.EIO, "write made no forward progress")
                remaining = remaining[count:]
            _call(_checkpoint, "temporary-written")
            fsync(fd)
            _call(_checkpoint, "temporary-fsynced")
        finally:
            os.close(fd)
        _replace(temporary, path)
    except BaseException:
        # Only a file this call made goes: what stood at an exclusive name before it is not this call's.
        if opened:
            try:
                os.remove(temporary)
            except OSError:
                pass
        raise
    _call(_checkpoint, "replaced")
    if _sync_directory(parent, fsync):
        _call(_checkpoint, "directory-fsynced")


def _repair_partial_tail(path, fsync, checkpoint):
    """Cut off the uncommitted final record an interrupted or failed append left.

    It is never completed: its writer did not return, so it was never committed (#68).
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return "clean"

    # Validate every complete row before touching the tail. Without this pass an
    # invalid earlier row followed by a partial tail could be retained while a
    # new record was appended, falsely reporting a successful durable write on
    # an already-corrupt ledger.
    data = open(path, "rb").read()
    _read_jsonl_unlocked(path, allow_partial_tail=True)
    if data.endswith((b"\n", b"\r")):
        return "clean"

    boundary = max(data.rfind(b"\n"), data.rfind(b"\r")) + 1
    with open(path, "r+b", buffering=0) as handle:
        handle.truncate(boundary)
        fsync(handle.fileno())
    _call(checkpoint, "truncated-tail")
    return "truncated-tail"


def _encode_jsonl(record):
    # The same inputs the chain digest accepts - ASCII escapes, no NaN - so no row
    # is written that could never be digested, and a path Git hands over
    # undecodable is escaped rather than refused (#68).
    return (json.dumps(
        record, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ) + "\n").encode("ascii")


def _append_payload_unlocked(path, parent, payload, write, fsync, checkpoint):
    """Append one encoded row while the caller owns the sidecar lock.

    All or nothing (#68): a failure at any step - a short write, the file fsync, the
    directory fsync - cuts the written bytes off again, and removes a file this
    append created, before the error goes to the caller. Returns the same undo for
    a caller whose own next step fails while it still holds the lock.
    """
    created = not os.path.exists(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o644)
    size = os.fstat(fd).st_size

    def undo():
        try:
            if created:
                os.remove(path)
                return
            os.truncate(path, size)
            handle = os.open(path, os.O_WRONLY | getattr(os, "O_BINARY", 0))
            try:
                os.fsync(handle)
            finally:
                os.close(handle)
        except OSError:
            pass

    try:
        _call(checkpoint, "opened")
        remaining = memoryview(payload)
        while remaining:
            try:
                count = write(fd, remaining)
            except InterruptedError:
                continue
            if not isinstance(count, int) or count <= 0 or count > len(remaining):
                raise OSError(errno.EIO, "append made no forward progress")
            remaining = remaining[count:]
        _call(checkpoint, "written")
        fsync(fd)
        _call(checkpoint, "fsynced")
    except BaseException:
        os.close(fd)
        undo()
        raise
    os.close(fd)

    if created:
        try:
            synced = _sync_directory(parent, fsync)
        except BaseException:
            undo()
            raise
        if synced:
            _call(checkpoint, "directory-fsynced")
    return undo


def append_jsonl(path, record, timeout=10.0, *, _checkpoint=None,
                 _write=None, _fsync=None):
    """Append one JSON object and durably persist it before returning.

    ``_checkpoint``, ``_write`` and ``_fsync`` are deliberately private test
    seams. They let the suite stop a real subprocess between storage barriers
    and inject short writes or I/O failures without a production crash switch.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    payload = _encode_jsonl(record)
    write = _write or os.write
    fsync = _fsync or os.fsync

    with _exclusive_lock(path + ".lock", timeout=timeout):
        _call(_checkpoint, "locked")
        _repair_partial_tail(path, fsync, _checkpoint)
        _append_payload_unlocked(
            path, parent, payload, write, fsync, _checkpoint
        )
    return record


def append_chained_jsonl(path, record, chain, timeout=10.0, *,
                         previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False, prepare=None,
                         _checkpoint=None, _write=None, _fsync=None):
    """Atomically select a predecessor, append, and persist one chained row.

    Existing committed rows are validated before a successor is constructed.
    Predecessor selection and append share one lock, so concurrent writers form
    one linear chain instead of siblings that name the same predecessor.
    `prepare`, given the committed rows, returns fields to set on the record under
    that lock: what must be unique across writers is decided where they take turns.
    """
    if not isinstance(record, dict):
        raise TypeError("a chained ledger row must be a JSON object")
    if not isinstance(previous_field, str) or not previous_field:
        raise ValueError("previous_field must be a non-empty string")
    if previous_field in record or "ordinal" in record:
        raise ValueError(f"caller must not set the {previous_field!r} or 'ordinal' chain fields")

    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    write = _write or os.write
    fsync = _fsync or os.fsync

    with _exclusive_lock(path + ".lock", timeout=timeout):
        _call(_checkpoint, "locked")
        _settle_seal(path, chain, previous_field)
        _repair_partial_tail(path, fsync, _checkpoint)
        rows = _read_jsonl_unlocked(path, allow_partial_tail=False)
        seal = _load_seal(path)
        _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix, seal=seal)
        _check_committed_length(path, rows, chain, seal=seal)
        retired = seal["retired"] if seal else 0
        previous = chained_row_digest(rows[-1], chain) if rows else (seal["digest"] if seal else None)
        chained = {previous_field: previous, "ordinal": retired + len(rows) + 1}
        chained.update(record)
        if prepare is not None:
            fields = prepare(rows) or {}
            if previous_field in fields or "ordinal" in fields:
                raise ValueError(f"prepare must not set the {previous_field!r} or 'ordinal' chain fields")
            chained.update(fields)
        payload = _encode_jsonl(chained)
        digest = chained_row_digest(chained, chain)
        undo = _append_payload_unlocked(
            path, parent, payload, write, fsync, _checkpoint
        )
        try:
            _record_committed_length(path, retired + len(rows) + 1, digest)
        except BaseException:
            # A row whose length could not be recorded is taken back, so the
            # caller's failure and the file agree (#68).
            undo()
            raise
    return chained
