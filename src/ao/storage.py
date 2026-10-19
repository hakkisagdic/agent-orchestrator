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
import re
import stat
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

    The way is chosen by what the platform offers - O_NOFOLLOW where it has one - and not by `os.name`, which a
    test of Windows' behaviour sets on another platform: there the lock went the Windows way, to a kernel32 that
    platform has not, and the hold the test ran failed where its lock was taken (LOCK-LINKS-2).
    """
    import stat as _stat
    refused = (f"{path} is a link, not a regular file, or a file with another name: ao takes no lock through it; "
               "remove it, and ao makes the lock again")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None and os.name == "nt":
        handle = _open_lock_nt(path, refused)
    else:
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
def _exclusive_lock(path, timeout=10.0, *, _folder=None):
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
    if _folder is None:
        os.makedirs(parent, exist_ok=True)
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if _folder is None:
            handle = _open_lock(path)
        else:
            fd = _tree_open(_folder, os.path.basename(path), os.O_RDWR | os.O_CREAT | os.O_APPEND)
            if os.fstat(fd).st_nlink != 1:
                os.close(fd)
                raise LockThroughLink(f"{path} has another name; remove the link before retrying")
            handle = open(fd, "a+b", buffering=0)
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
            if _folder is None:
                named = _lock_still_named(path, handle)
            else:
                try:
                    at = _tree_stat(_folder, os.path.basename(path))
                    held = os.fstat(handle.fileno())
                    named = not stat.S_ISLNK(at.st_mode) and held.st_nlink == 1 and \
                        (at.st_dev, at.st_ino) == (held.st_dev, held.st_ino)
                except FileNotFoundError:
                    named = False
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
    return _jsonl_rows(data, path, allow_partial_tail)


def _jsonl_records(data):
    """([(line number, record bytes)] of each complete, non-empty line, the line number of an unfinished last one or
    None) - the one way ao splits JSONL bytes into records, which a read, a count and an append all use (OPTIN-ATTESTED).

    A line ends at `\n`, `\r` or `\r\n`; an empty one is no record, and one of blanks is a record a parse refuses.
    Counted any other way, a row put after `\r` on ao's own line was one line to a count and two records to a read.
    """
    records = []
    for index, raw in enumerate(data.splitlines(keepends=True)):
        body = raw.rstrip(b"\r\n")
        if not body:
            continue
        if not raw.endswith((b"\n", b"\r")):
            return records, index
        records.append((index, body))
    return records, None


def _jsonl_row(body, index, path):
    try:
        return json.loads(body.decode(UTF8))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerCorruption(f"malformed JSONL record {index + 1} in {path}: {exc}") from exc


def _jsonl_rows(data, path, allow_partial_tail=True):
    """Parse bytes already read through a held file (LEADS-ADAPTERS)."""
    records, unfinished = _jsonl_records(data)
    rows = [_jsonl_row(body, index, path) for index, body in records]
    if unfinished is not None and not allow_partial_tail:
        raise LedgerCorruption(f"uncommitted final record {unfinished + 1} in {path}")
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
    agent writes (STORAGE-TMP). It syncs the folder after the rename too: renamed into place with no sync, an append
    returned on a record a power cut could still take back (the review of LEDGER-LENGTH, LEDGER-HISTORY).
    """
    replace_file_durably(checkpoint_path(), json.dumps(data, indent=1, sort_keys=True).encode("utf-8"))


def _checkpoint_entry(path, *, identity=None):
    """What the checkpoint store records for one ledger: {} when nothing."""
    entry = _load_committed_lengths().get(identity or os.path.realpath(path))
    return entry if isinstance(entry, dict) else {}


def _update_checkpoint(path, change, *, identity=None):
    """Replace one ledger's record in the checkpoint store with `change(record)`, under the store's lock."""
    store = checkpoint_path()
    os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    with _exclusive_lock(store + ".lock"):
        data = _load_committed_lengths()
        key = identity or os.path.realpath(path)
        data[key] = change(dict(data[key]) if isinstance(data.get(key), dict) else {})
        _write_committed_lengths(data)


def _record_committed_length(path, count, digest, sealed=None, *, identity=None):
    def change(previous):
        mark = {"count": count, "digest": digest, "at": int(time.time())}
        # A seal is recorded outside the repository too, so one written inside it
        # cannot retire rows this machine never retired (#50).
        if sealed is not None or previous.get("sealed"):
            mark["sealed"] = sealed if sealed is not None else previous["sealed"]
        # A seal under way is settled before any append; kept, one an append met anyway is refused, not lost.
        if previous.get("intent"):
            mark["intent"] = previous["intent"]
        return mark
    _update_checkpoint(path, change, identity=identity)


def _check_committed_length(path, rows, chain, seal=None, *, identity=None):
    """Refuse a ledger shorter than its recorded length, or diverging from it (#62), across a seal (#50)."""
    mark = _load_committed_lengths().get(identity or os.path.realpath(path))
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


def _check_missing_chained_ledger(path, chain, seal=None, *, identity=None):
    """A file missing where a checkpoint or seal records rows is unreadable, never an empty history."""
    _check_committed_length(path, [], chain, seal=seal, identity=identity)
    entry = _checkpoint_entry(path, identity=identity)
    if seal or entry.get("sealed") or "intent" in entry or \
            (isinstance(entry.get("count"), int) and entry["count"] > 0):
        raise LedgerCorruption(f"{path} is absent but its checkpoint or seal records rows: rows were removed; "
                               "restore the ledger before reading its history")


def _load_seal(path, *, _folder=None):
    """The seal that retired a ledger's prefix, or None (#50)."""
    try:
        if _folder is None:
            with open(seal_path(path), encoding="utf-8") as handle:
                seal = json.load(handle)
        else:
            data = _tree_bytes(_folder, os.path.basename(seal_path(path)))
            if data is None:
                return None
            seal = json.loads(data.decode(UTF8))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise LedgerCorruption(f"the seal of {path} cannot be read: {exc}") from exc
    if not isinstance(seal, dict) or not isinstance(seal.get("retired"), int) or seal["retired"] < 1 \
            or not isinstance(seal.get("digest"), str):
        raise LedgerCorruption(f"the seal of {path} is malformed")
    return seal


def _seal_intent(path, entry):
    """The seal of `path` under way that this machine recorded in `entry`, its checkpoint record, or None."""
    intent = entry.get("intent")
    if "intent" not in entry:
        return None
    before = intent.get("before") if isinstance(intent, dict) else None
    if not isinstance(intent, dict) or not isinstance(intent.get("retired"), int) or intent["retired"] < 1 \
            or not isinstance(intent.get("digest"), str) or not isinstance(intent.get("archives"), list) \
            or not isinstance(before, dict) or not isinstance(before.get("count"), int) \
            or before["count"] <= intent["retired"] \
            or not isinstance(before.get("digest"), str):
        raise LedgerCorruption(f"the seal of {path} under way, as {checkpoint_path()} records it, is malformed: "
                               "nothing is read from the ledger until a person compares the two")
    return intent


def _settle_seal(path, chain, previous_field, legacy_prefix=False, *, _folder=None, identity=None):
    """Finish or undo a seal a crash interrupted, from the intent this machine recorded; the caller holds the ledger lock (#50).

    The intent was read from `<ledger>.seal.json.pending`, in the tree the implementer writes: a
    ledger cut to its newest rows, beside an intent naming the cut rows as sealed, was finished
    as a seal and the record outside the repository rewritten to match, so the cut, or a whole
    chain of the writer's own with any genesis, read as authority (the review of BOUNDED-STORES).
    A seal now records its intent in the checkpoint store, under that store's lock, before it
    rewrites the ledger, and only that intent is settled: finished when the ledger starts after
    the rows it retires, holds exactly the rows it kept and the archives hold exactly those it
    retired; undone when the ledger is still the one it was taken from; refused otherwise. ao
    writes no intent in the tree, so a `.pending` found there is none of its own: it is refused,
    named and left where it is (LEDGER-HISTORY). The opt-in reader uses the same settlement,
    with every tree operation relative to its held folder and its checkpoint identity fixed
    before the folder can move (LEADS-ADAPTERS).
    """
    stray = seal_path(path) + ".pending"
    if _folder is None:
        stray_exists = os.path.lexists(stray)
    else:
        try:
            _tree_stat(_folder, os.path.basename(stray))
            stray_exists = True
        except FileNotFoundError:
            stray_exists = False
    if stray_exists:
        raise LedgerCorruption(
            f"{stray} names a seal of {os.path.basename(path)} that this machine did not begin: ao records a seal's "
            f"intent in {checkpoint_path()}, never in the tree. It is left where it is and the ledger is not read "
            "while it stands; read it, then move it out of .ao/ledger/"
        )
    entry = _checkpoint_entry(path, identity=identity)
    intent = _seal_intent(path, entry)
    if intent is None:
        return
    before = intent["before"]
    if (entry.get("count"), entry.get("digest")) != (before["count"], before["digest"]):
        raise LedgerCorruption(
            f"the record of {path} in {checkpoint_path()} changed while a seal of it was under way: nothing is read "
            "from the ledger until a person compares the two"
        )
    if intent.get("chain") != chain:
        raise LedgerCorruption(f"the seal of {path} under way was begun for another chain than {chain!r}")
    if _folder is None:
        rows = _read_jsonl_unlocked(path, allow_partial_tail=False) if os.path.exists(path) else []
    else:
        rows = _jsonl_rows(_tree_bytes(_folder, os.path.basename(path)) or b"", path, allow_partial_tail=False)
    if rows and isinstance(rows[0], dict) and rows[0].get(previous_field) == intent["digest"]:
        # Rewritten: what stands must be the newest rows the intent kept, and the archives the rows it retired.
        seal = {key: intent.get(key) for key in ("chain", "retired", "digest", "archives", "at")}
        _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix, seal=seal)
        if intent["retired"] + len(rows) != before["count"] or chained_row_digest(rows[-1], chain) != before["digest"]:
            raise LedgerCorruption(
                f"{path} was rewritten by a seal and no longer holds the rows it kept, through row {before['count']}: "
                "rows were changed or removed after it"
            )
        _, archived = _sealed_rows_held(path, chain, seal, previous_field, legacy_prefix, _folder=_folder)
        data = (json.dumps(seal, sort_keys=True) + "\n").encode(UTF8)
        if _folder is None:
            replace_file_durably(seal_path(path), data)
        else:
            _tree_replace_bytes(_folder, os.path.basename(seal_path(path)), data)

        def finished(record):
            record.pop("intent", None)
            record.update(count=before["count"], digest=before["digest"], at=int(time.time()),
                          sealed={"retired": intent["retired"], "digest": intent["digest"], "archives": archived})
            return record
        _update_checkpoint(path, finished, identity=identity)
        return
    # Not rewritten: the ledger must still be the one the seal was taken from.
    seal = _load_seal(path, _folder=_folder)
    _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix, seal=seal)
    held = (seal["retired"] if seal else 0) + len(rows)
    if held != before["count"] or not rows or chained_row_digest(rows[-1], chain) != before["digest"]:
        raise LedgerCorruption(
            f"{path} changed while a seal of it was under way: it holds {held} rows where the seal found "
            f"{before['count']}"
        )
    _check_committed_length(path, rows, chain, seal=seal, identity=identity)
    _sealed_rows_held(path, chain, seal, previous_field, legacy_prefix, _folder=_folder)

    def undone(record):
        record.pop("intent", None)
        return record
    _update_checkpoint(path, undone, identity=identity)


def seal_chained_jsonl(path, chain, keep, *, previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False,
                       _checkpoint=None):
    """Retire all but the newest `keep` rows of a chained ledger into an archive, never deleting one (#50).

    The retired rows are written whole to `sealed/<ledger>.<first>-<last>.jsonl`
    beside the ledger. The seal names how many rows it retired and the digest of
    the last of them, which becomes the genesis the first kept row links to, and
    the same seal is recorded outside the repository with the committed length.
    A sealed ledger and the unsealed one give every reader the same newest rows.
    Returns the seal, or None when there is nothing to retire.

    Before the ledger is rewritten, the seal's intent - what it retires, the digest of
    the last of those, its archives, and the length and newest digest the ledger had -
    is recorded in the checkpoint store under that store's lock, and a seal cut short
    is settled from that record alone (LEDGER-HISTORY). `_checkpoint` is the private
    test seam the appends take.
    """
    if keep < 1:
        raise ValueError("a seal keeps at least one row")
    with _exclusive_lock(path + ".lock"):
        _settle_seal(path, chain, previous_field, legacy_prefix)
        if not os.path.exists(path):
            _check_missing_chained_ledger(path, chain, seal=_load_seal(path))
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
        # A seal adds its archive to ones that hold: stacked on a broken one, it would carry the break on.
        _sealed_rows_held(path, chain, seal, previous_field, legacy_prefix)
        base = seal["retired"] if seal else 0
        first, last = base + 1, base + len(retire)
        archive = os.path.join(os.path.dirname(path) or ".", "sealed", f"{os.path.basename(path)}.{first}-{last}.jsonl")
        replace_file_durably(archive, b"".join(_encode_jsonl(row) for row in retire))
        intent = {"chain": chain, "retired": last, "digest": chained_row_digest(retire[-1], chain),
                  "archives": list((seal or {}).get("archives") or []) + [os.path.basename(archive)],
                  "at": int(time.time()),
                  "before": {"count": base + len(rows), "digest": chained_row_digest(rows[-1], chain)}}

        def begun(record):
            record.update(count=intent["before"]["count"], digest=intent["before"]["digest"], at=intent["at"],
                          intent=intent)
            return record
        _update_checkpoint(path, begun)
        _call(_checkpoint, "intent-recorded")
        replace_file_durably(path, b"".join(_encode_jsonl(row) for row in kept))
        _call(_checkpoint, "ledger-rewritten")
        _settle_seal(path, chain, previous_field, legacy_prefix)
        return _load_seal(path)


def _sealed_rows_held(path, chain, seal, previous_field, legacy_prefix, recorded=None, *, _folder=None):
    """(the rows `seal` retired, oldest first, the digest of its archives' bytes), held to the seal (LEDGER-HISTORY).

    The archives were read as they stood: a row edited in one was returned as it was, and a reader
    took its id for one ao had recorded (the review of BOUNDED-STORES). They must now be one chain,
    from the ledger's first row to the seal's digest, and hold exactly the rows it retired, or
    LedgerCorruption names what does not hold. Archives whose bytes are those this machine held to
    the seal when it sealed them - `recorded`, the digest the checkpoint store keeps - are not
    walked link by link again: a reader of a long history pays for parsing it, not for its chain.
    """
    if not seal:
        return [], None
    folder = os.path.join(os.path.dirname(path) or ".", "sealed")
    names = seal.get("archives")
    if not isinstance(names, list) or not names \
            or not all(isinstance(name, str) and name and os.path.basename(name) == name for name in names):
        raise LedgerCorruption(f"the seal of {path} names no archives ao can read")
    parts, whole = [], hashlib.sha256()
    for name in names:
        archive = os.path.join(folder, name)
        try:
            if _folder is None:
                with open(archive, "rb") as handle:
                    data = handle.read()
            else:
                with _tree_folder(_folder, ["sealed"], make=False) as held:
                    data = _tree_bytes(held, name) if held is not None else None
                if data is None:
                    raise FileNotFoundError(archive)
        except OSError as exc:
            raise LedgerCorruption(f"{archive}, which the seal of {path} names, cannot be read: {exc}") from exc
        whole.update(data)
        parts.append((archive, data))
    digest = "sha256:" + whole.hexdigest()
    rows, expected = [], None
    for archive, data in parts:
        part = _jsonl_rows(data, archive, allow_partial_tail=False)
        if digest != recorded:
            _validate_chained_rows(archive, part, chain, previous_field, legacy_prefix and not rows,
                                   seal={"digest": expected, "retired": len(rows)} if rows else None)
            if part:
                expected = chained_row_digest(part[-1], chain)
        rows.extend(part)
    if len(rows) != seal["retired"] or not rows or chained_row_digest(rows[-1], chain) != seal["digest"]:
        raise LedgerCorruption(
            f"the archives of {path} hold {len(rows)} rows ending at "
            f"{chained_row_digest(rows[-1], chain) if rows else 'none'}, where its seal retired {seal['retired']} "
            f"ending at {seal['digest']}"
        )
    return rows, digest


def sealed_rows(path, chain, *, previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False, seal=None):
    """Every row a seal retired from a ledger, oldest first, read from its archives and held to its seal (#50).

    `seal` is the ledger's own when not given. The caller holds the ledger's lock, or reads a copy
    no one appends to; `read_chained_history` reads the whole history under the lock (LEDGER-HISTORY).
    """
    # Snapshot readers do not settle or write. They must refuse an unfinished seal rather than measure half of it.
    stray = seal_path(path) + ".pending"
    if os.path.lexists(stray):
        raise LedgerCorruption(f"{stray} names a seal this machine did not begin: ao records its intent in "
                               f"{checkpoint_path()}, never in the tree; the ledger is not read while it stands")
    if "intent" in _checkpoint_entry(path):
        raise LedgerCorruption(f"the seal of {path} awaits settlement; read the ledger under its lock before "
                               "reading its archives")
    seal = seal if seal is not None else _load_seal(path)
    if not os.path.exists(path):
        _check_missing_chained_ledger(path, chain, seal=seal)
    return _sealed_rows_held(path, chain, seal, previous_field, legacy_prefix)[0]


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
                       previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False, lock=True, attested=False):
    """Read and validate every committed row in one predecessor hash chain.

    The chain proves no row was changed or inserted; the recorded length proves
    none was cut off the end, including all of them (#62).
    """
    if not os.path.exists(path) and not os.path.lexists(seal_path(path) + ".pending") \
            and "intent" not in _checkpoint_entry(path):
        _check_missing_chained_ledger(path, chain, seal=_load_seal(path))
        return []
    if not lock:
        return _read_chained_snapshot(
            path, chain, allow_partial_tail, previous_field, legacy_prefix, attested=attested
        )[0]
    with _exclusive_lock(path + ".lock", timeout=timeout):
        return _read_chained_unlocked(path, chain, allow_partial_tail, previous_field, legacy_prefix)[0]


def _read_chained_unlocked(path, chain, allow_partial_tail, previous_field, legacy_prefix):
    """(the live rows, the seal they follow), validated; the caller holds the ledger lock."""
    _settle_seal(path, chain, previous_field, legacy_prefix)
    seal = _load_seal(path)
    if not os.path.exists(path):
        _check_missing_chained_ledger(path, chain, seal=seal)
    rows = _read_jsonl_unlocked(path, allow_partial_tail) if os.path.exists(path) else []
    _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix, seal=seal)
    _check_committed_length(path, rows, chain, seal=seal)
    return rows, seal


def _read_chained_snapshot(path, chain, allow_partial_tail, previous_field, legacy_prefix, *, attested=False):
    """(the live rows, the seal they follow, unattested info), validated without locking or settling a seal (EXPORT-OTLP)."""
    stray = seal_path(path) + ".pending"
    if os.path.lexists(stray):
        raise LedgerCorruption(
            f"{stray} names a seal of {os.path.basename(path)} that this machine did not begin: ao records a seal's "
            f"intent in {checkpoint_path()}, never in the tree. It is left where it is and the ledger is not read "
            "while it stands; read it, then move it out of .ao/ledger/"
        )
    entry = _checkpoint_entry(path)
    if "intent" in entry:
        raise LedgerCorruption(f"the seal of {path} awaits settlement; read the ledger under its lock before "
                               "reading a snapshot")
    seal = _load_seal(path)
    if not os.path.exists(path):
        _check_missing_chained_ledger(path, chain, seal=seal)
        return [], seal, None
    rows = _read_jsonl_unlocked(path, allow_partial_tail)
    _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix, seal=seal)
    _check_committed_length(path, rows, chain, seal=seal)
    retired = seal["retired"] if seal else 0
    total = retired + len(rows)
    count = entry.get("count")
    has_count = isinstance(count, int) and not isinstance(count, bool) and count > 0
    unattested = None
    if has_count:
        if total > count:
            extra = total - count
            msg = (
                f"{os.path.basename(path)} ({path}) holds {total} row(s), {extra} past its recorded count of {count} "
                f"in {checkpoint_path()} (no checkpoint here for the trailing row(s): recorded elsewhere, reset, or "
                "an append still under way); pass --attested to export only the attested part"
            )
            if not attested:
                raise LedgerCorruption(msg)
            rows = rows[:max(0, count - retired)]
            unattested = {"ledger": os.path.basename(path), "reason": "rows_past_checkpoint", "message": msg}
    elif total > 0 or seal is not None:
        msg = (
            f"{os.path.basename(path)} ({path}) holds {total} row(s) with no checkpoint here: moved, reset, "
            f"recorded elsewhere, older than checkpoints (checkpoints are kept in {checkpoint_path()}); "
            "pass --attested to export only the attested part"
        )
        if not attested:
            raise LedgerCorruption(msg)
        rows = []
        seal = None
        unattested = {"ledger": os.path.basename(path), "reason": "no_checkpoint", "message": msg}
    else:
        rows = []
    return rows, seal, unattested


def _read_chained_history_snapshot(path, chain, *, previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False,
                                   attested=False):
    """(rows, unattested) for a lockless snapshot of a chained ledger's full history (EXPORT-OTLP)."""
    if not os.path.exists(path) and not os.path.lexists(seal_path(path) + ".pending") \
            and "intent" not in _checkpoint_entry(path):
        _check_missing_chained_ledger(path, chain, seal=_load_seal(path))
        return [], None
    rows, seal, unattested = _read_chained_snapshot(
        path, chain, True, previous_field, legacy_prefix, attested=attested
    )
    recorded = _checkpoint_entry(path).get("sealed")
    retired, _ = _sealed_rows_held(path, chain, seal, previous_field, legacy_prefix,
                                   recorded=recorded.get("archives") if isinstance(recorded, dict) else None)
    return retired + rows, unattested


def read_chained_history(path, chain, timeout=10.0, *, previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False,
                         lock=True, attested=False):
    """Every row a chained ledger ever committed, oldest first: those its seal retired, then the live ones (LEDGER-HISTORY).

    One chain, read under the ledger's lock: the archives end at the seal's digest with exactly
    the rows it retired, and the live rows go on from it as `read_chained_jsonl` holds them. A
    reader that asks what was ever granted, verified or waived reads this; one that asks for the
    newest decision reads the live rows. Read from the live rows alone, a seal made a spent waiver
    look unused and moved the first grant the landed-tree check starts from (the review of
    BOUNDED-STORES). `lock=False` reads a snapshot without creating a `.lock` file or settling a
    seal (EXPORT-OTLP).
    """
    if not os.path.exists(path) and not os.path.lexists(seal_path(path) + ".pending") \
            and "intent" not in _checkpoint_entry(path):
        _check_missing_chained_ledger(path, chain, seal=_load_seal(path))
        return []
    if not lock:
        return _read_chained_history_snapshot(
            path, chain, previous_field=previous_field, legacy_prefix=legacy_prefix, attested=attested
        )[0]
    with _exclusive_lock(path + ".lock", timeout=timeout):
        rows, seal = _read_chained_unlocked(path, chain, True, previous_field, legacy_prefix)
        recorded = _checkpoint_entry(path).get("sealed")
        retired, _ = _sealed_rows_held(path, chain, seal, previous_field, legacy_prefix,
                                       recorded=recorded.get("archives") if isinstance(recorded, dict) else None)
        return retired + rows


def _sync_directory(directory, fsync):
    """Persist a newly-created directory entry where the platform supports it."""
    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(directory or ".", flags)
    try:
        return _sync_folder_held(fd, fsync)
    finally:
        os.close(fd)


def _sync_folder_held(fd, fsync):
    """Persist the entries of a directory held open as `fd`; False where its file system cannot."""
    try:
        fsync(fd)
    except OSError as exc:
        # Some filesystems expose directories but reject fsync. This is a
        # platform capability limit, not evidence that data was persisted.
        if exc.errno in (errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", -1)):
            return False
        raise
    return True


class ReplacedNotDurable(OSError):
    """The replacement happened, but syncing its directory failed (TRIAGE-3-LOW)."""


def _write_all(fd, data):
    """Write every byte of `data` to `fd`, or raise."""
    remaining = memoryview(data)
    while remaining:
        try:
            count = os.write(fd, remaining)
        except InterruptedError:
            continue
        if count <= 0:
            raise OSError(errno.EIO, "write made no forward progress")
        remaining = remaining[count:]


def replace_file_durably(path, data, *, mode=None, before_replace=None, _checkpoint=None, _fsync=None):
    """Replace a whole file so it is only ever seen with its old bytes or its new ones.

    ``open(path, "w")`` truncates first. A crash, kill or full disk before the new
    bytes are down leaves a zero-byte file, and a zero-byte ``.ao/config.json`` no
    longer holds the ``capability_matrix`` that keeps strict authority on (#56).
    This writes a temporary file beside the target, fsyncs it, renames it over the
    target and fsyncs the directory. ``_checkpoint`` and ``_fsync`` are the private
    test seams the ledger appends take.

    ``before_replace`` is called once the new bytes are durable beside the target and
    right before the rename: the last moment a caller that must not replace what
    someone changed since its reading can look, and what it raises leaves the target
    as it was and takes the temporary file away (SLICE-LIFECYCLE-2).

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
            _write_all(fd, data)
            _call(_checkpoint, "temporary-written")
            fsync(fd)
            _call(_checkpoint, "temporary-fsynced")
        finally:
            os.close(fd)
        if before_replace is not None:
            before_replace()
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
    try:
        synced = _sync_directory(parent, fsync)
    except OSError as exc:
        raise ReplacedNotDurable(f"{path} changed, but durability could not be confirmed ({exc}); "
                                 "read the file and retry after fixing the storage error") from exc
    if synced:
        _call(_checkpoint, "directory-fsynced")


# ---- a file in a project's tree, written and read through no link (LEADS-ADAPTERS) -------------------------------
#
# `ao init` writes into a tree an agent writes too, and it wrote by path: each folder on the way was whatever stood
# there as the file was opened. A `.ao` or an `agent-mail` that was a link - or became one between init's look and its
# write - took the configuration, the board, the playbook and the manifest out of the project, and a `.ao/PLAYBOOK.md`
# or an MCP file that was a link had ao's text added to the file of the person's it led to. Where the platform opens a
# file relative to a folder it holds open, as POSIX does, each folder is opened so from the project's root down, never
# through a link, and the file is made, replaced or read in the last of them: a link put on the way meanwhile moves
# nothing. Windows holds every folder handle without delete sharing, so no folder can be renamed or replaced while
# its path is used; every handle is opened as the reparse point itself and reparse points are refused.

class InTheWay(OSError):
    """What stands where a file is written or read in a project's tree and is not gone through: a link or a file where
    a folder goes, a link at the file's own name, or no regular file. `where` is its path in the tree (LEADS-ADAPTERS).
    """

    def __init__(self, where, what):
        super().__init__(f"{where} {what}")
        self.where = where


_A_FOLDER = "is a link or a file where a folder of the project's own goes"
_A_LINK = "is a link, and ao reads and writes through none"
_NO_FILE = "is no regular file"
_WINDOWS_LINK_TAG = 0x20000000          # the name-surrogate bit of a reparse tag: a symbolic link's, a junction's


def folders_held_open():
    """Whether this platform opens a file relative to a folder it holds open, as POSIX does and Python on Windows does
    not (LEADS-ADAPTERS)."""
    return bool(getattr(os, "O_NOFOLLOW", 0) and getattr(os, "O_DIRECTORY", 0)) and \
        {os.open, os.mkdir, os.rename, os.stat, os.unlink} <= os.supports_dir_fd


def not_a_folder_of_its_own(path):
    """Whether what stands at `path` is no folder of the project's own: a link, a junction - Windows' link to a folder,
    which `os.path.islink` does not name before Python 3.12, known by the name-surrogate bit of its reparse tag, which a
    cloud file's placeholder does not carry - a file, or what cannot be looked at (LEADS-ADAPTERS)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) \
        or bool(getattr(info, "st_reparse_tag", 0) & _WINDOWS_LINK_TAG)


def _tree_parts(rel):
    """The names of `rel`, a path within a project, folder by folder."""
    text = str(rel).replace(os.sep, "/")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or ".." in parts or (os.name == "nt" and any(":" in part for part in parts)):
        raise InTheWay(text, "names no file within the project")
    return parts


def _folder_held(root, parts, make):
    """The descriptor of the folder `parts` names under `root`, each opened relative to the one before it and never
    through a link, and those missing made where `make`; None where one is missing. InTheWay where one is a link or a
    file."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.dup(root) if isinstance(root, int) else os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for at, part in enumerate(parts):
            if make:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                inner = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if make:
                    raise
                os.close(fd)
                return None
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
                    raise InTheWay("/".join(parts[:at + 1]), _A_FOLDER) from None
                raise
            held, fd = fd, inner
            os.close(held)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _windows_tree_available():
    """Select the native handle path by its APIs, even when a test changes os.name (LEADS-ADAPTERS)."""
    import ctypes
    return callable(getattr(ctypes, "WinDLL", None))


def _require_windows_tree():
    if not _windows_tree_available():
        raise InTheWay("this platform", "cannot hold folders through no link; ao refuses this file operation")


def open_new_text(path):
    """Create a UTF-8 file through no final link, or report that its name is taken (WINDOWS-TESTS-7).

    Windows' CRT exclusive open follows a dangling link before creating its target. CREATE_NEW with
    OPEN_REPARSE_POINT instead refuses the occupied name, including a link planted during the open.
    Choose by the available APIs: tests can name a POSIX host Windows without giving it WinDLL.
    """
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is not None:
        opener = lambda name, flags: os.open(name, flags | nofollow, 0o666)
    elif _windows_tree_available():
        opener = _open_tree_nt
    else:
        raise OSError(f"{path}: this platform cannot create a file through no link; "
                      "ao refuses the write; use a supported platform")
    return open(path, "x", encoding=UTF8, opener=opener)


def _open_tree_nt(path, flags=None, mode=0o666):
    """Open the directory or leaf itself, with no delete sharing; reject every reparse point (LEADS-ADAPTERS)."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Information(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
                    ("links", wintypes.DWORD), ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create, close, information = kernel.CreateFileW, kernel.CloseHandle, kernel.GetFileInformationByHandle
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    information.argtypes = (wintypes.HANDLE, ctypes.POINTER(Information))
    information.restype = wintypes.BOOL
    directory = flags is None
    # A folder is opened to list it, not for its attributes alone: Windows records the share mode of a handle that
    # asks for data access only, so one that asked for none let another process rename the folder it was holding.
    access = (0x0001 | 0x0080) if directory else (0xC0000000 if flags & os.O_RDWR else
                                                   0x40000000 if flags & os.O_WRONLY else 0x80000000)
    disposition = 3 if directory or not flags & os.O_CREAT else (1 if flags & os.O_EXCL else 4)
    raw = create(os.fspath(path), access, 0x1 | 0x2, None, disposition, 0x02000000 | 0x00200000, None)
    if raw is None or raw == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = Information()
        if not information(raw, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400:
            raise InTheWay(path, _A_FOLDER if directory else _A_LINK)
        if bool(info.attributes & 0x10) != directory:
            raise InTheWay(path, _A_FOLDER if directory else _NO_FILE)
        if directory:
            return raw, close
        fd = msvcrt.open_osfhandle(raw, flags & (os.O_APPEND | os.O_RDONLY | os.O_WRONLY | os.O_RDWR)
                                  | getattr(os, "O_BINARY", 0))
    except BaseException:
        close(raw)
        raise
    return fd


@contextmanager
def _folders_nt(root, parts, make):
    """Pin the whole absolute path until the leaf operation ends, including the project's ancestors."""
    _require_windows_tree()
    absolute = os.path.abspath(root)
    drive, tail = os.path.splitdrive(absolute)
    folder = drive + os.sep
    ancestors = [part for part in tail.split(os.sep) if part]
    names = ancestors + list(parts)
    handles = []
    try:
        handles.append(_open_tree_nt(folder))
        for at, part in enumerate(names):
            folder = os.path.join(folder, part)
            # Ancestors and root must already exist; traversal permission need not allow mkdir there
            # (the round-4 review of LEADS-ADAPTERS).
            if make and at >= len(ancestors):
                try:
                    os.mkdir(folder)
                except FileExistsError:
                    pass
            try:
                handles.append(_open_tree_nt(folder))
            except FileNotFoundError:
                if make:
                    raise
                yield None
                return
            except OSError as exc:
                shown = "/".join(parts[:at - len(ancestors) + 1]) if at >= len(ancestors) else folder
                raise InTheWay(shown, f"{_A_FOLDER}, or cannot be held ({exc})") from exc
        yield folder
    finally:
        for handle, close in reversed(handles):
            close(handle)


@contextmanager
def _tree_folder(root, parts, make):
    """A dir_fd on POSIX or a path pinned by native handles on Windows (LEADS-ADAPTERS)."""
    if folders_held_open():
        fd = _folder_held(root, parts, make)
        try:
            yield fd
        finally:
            if fd is not None:
                os.close(fd)
    else:
        with _folders_nt(root, parts, make) as folder:
            yield folder


@contextmanager
def _tree_child(folder, name):
    """Hold a child for reading or removal while its parent stays held, through no link (LEADS-INSTALL)."""
    if isinstance(folder, int):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
        try:
            yield fd
        finally:
            os.close(fd)
    else:
        _require_windows_tree()
        child = os.path.join(folder, name)
        handle, close = _open_tree_nt(child)
        try:
            yield child
        finally:
            close(handle)


def _tree_rmdir(folder, name):
    if isinstance(folder, int):
        os.rmdir(name, dir_fd=folder)
    else:
        os.rmdir(os.path.join(folder, name))


def _tree_remove_leaf(folder, name):
    """Unlink a leaf, including a Windows junction itself, never its target (LEADS-INSTALL)."""
    info = _tree_stat(folder, name)
    if not isinstance(folder, int) and getattr(info, "st_file_attributes", 0) & 0x10:
        _tree_rmdir(folder, name)
    else:
        _tree_unlink(folder, name)


def _tree_open(folder, name, flags, mode=0o666):
    if isinstance(folder, int):
        try:
            fd = os.open(name, flags | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), mode, dir_fd=folder)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise InTheWay(name, _A_LINK) from exc
            if exc.errno in (errno.ENXIO, errno.ENODEV, getattr(errno, "EOPNOTSUPP", -1)):
                raise InTheWay(name, _NO_FILE) from exc
            raise
    else:
        try:
            fd = _open_tree_nt(os.path.join(folder, name), flags, mode)
        except InTheWay as exc:
            raise InTheWay(name, str(exc)[len(str(exc.where)) + 1:]) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise InTheWay(name, _NO_FILE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _tree_stat(folder, name):
    return os.stat(name, dir_fd=folder, follow_symlinks=False) if isinstance(folder, int) else \
        os.lstat(os.path.join(folder, name))


def _tree_unlink(folder, name):
    if isinstance(folder, int):
        os.unlink(name, dir_fd=folder)
    else:
        os.unlink(os.path.join(folder, name))


def _tree_sync(folder, fsync):
    return _sync_folder_held(folder, fsync) if isinstance(folder, int) else False


def create_in_tree(root, rel, data):
    """Make the file `rel` in the project at `root`, holding `data`, where nothing stands at that name: "wrote", or
    "kept" where a file, a folder or a link stands - kept as it is and never followed. InTheWay where a folder on the
    way is a link or a file (LEADS-ADAPTERS)."""
    parts = _tree_parts(rel)
    with _tree_folder(root, parts[:-1], make=True) as folder:
        try:
            out = _tree_open(folder, parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            return "kept"
        with os.fdopen(out, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _tree_sync(folder, os.fsync)
    return "wrote"


def replace_in_tree(root, rel, data, *, mode=None, _checkpoint=None, _fsync=None):
    """Replace the file `rel` in the project at `root` whole, as replace_file_durably does - a temporary under a name no
    file had, fsynced, renamed over it, the folder fsynced - in the folder held open from the root down: a link at its
    name is replaced and nothing is written through one. Without `mode` the file keeps the mode it had, or 0644.
    `_checkpoint` and `_fsync` are replace_file_durably's test seams, at the same steps. InTheWay where a folder on the
    way is a link or a file, or a folder stands at its name (LEADS-ADAPTERS)."""
    fsync = _fsync or os.fsync
    parts = _tree_parts(rel)
    shown = "/".join(parts)

    def kept_mode(listed):
        if stat.S_ISDIR(listed.st_mode):
            raise InTheWay(shown, "is a folder, where a file of the project's goes")
        return listed.st_mode & 0o7777 if stat.S_ISREG(listed.st_mode) else 0o644

    if not folders_held_open():
        with _folders_nt(root, parts[:-1], make=True) as folder:
            path = os.path.join(folder, parts[-1])
            try:
                leaf = _open_tree_nt(path, os.O_RDONLY)
            except FileNotFoundError:
                kept = 0o644
            else:
                try:
                    kept = kept_mode(os.fstat(leaf))
                finally:
                    os.close(leaf)
            temporary = os.path.join(folder, f".{parts[-1]}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
            out = _open_tree_nt(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                try:
                    _call(_checkpoint, "temporary-opened")
                    _write_all(out, data)
                    _call(_checkpoint, "temporary-written")
                    fsync(out)
                    _call(_checkpoint, "temporary-fsynced")
                finally:
                    os.close(out)
                _replace(temporary, path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
            _call(_checkpoint, "replaced")
        return
    fd = _folder_held(root, parts[:-1], make=True)
    try:
        name = parts[-1]
        try:
            kept = kept_mode(os.stat(name, dir_fd=fd, follow_symlinks=False))
        except FileNotFoundError:
            kept = 0o644
        temporary = f".{name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                      kept if mode is None else mode, dir_fd=fd)
        try:
            try:
                _call(_checkpoint, "temporary-opened")
                _write_all(out, data)
                os.fchmod(out, kept if mode is None else mode)
                _call(_checkpoint, "temporary-written")
                fsync(out)
                _call(_checkpoint, "temporary-fsynced")
            finally:
                os.close(out)
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=fd)
            except OSError:
                pass
            raise
        _call(_checkpoint, "replaced")
        if _sync_folder_held(fd, fsync):
            _call(_checkpoint, "directory-fsynced")
    finally:
        os.close(fd)


def read_in_tree(root, rel):
    """The bytes of the regular file `rel` in the project at `root`, or None where nothing is there. InTheWay where it,
    or a folder on its way, is a link, or it is no regular file - a FIFO is never waited on (LEADS-ADAPTERS)."""
    parts = _tree_parts(rel)
    with _tree_folder(root, parts[:-1], make=False) as folder:
        if folder is None:
            return None
        try:
            leaf = _tree_open(folder, parts[-1], os.O_RDONLY)
        except FileNotFoundError:
            return None
        except InTheWay as exc:
            raise InTheWay("/".join(parts), str(exc)[len(str(exc.where)) + 1:]) from exc
        with os.fdopen(leaf, "rb") as fh:
            return fh.read()


def append_in_tree(root, rel, data):
    """Append only to a single-name regular file opened through no link (LEADS-ADAPTERS)."""
    parts = _tree_parts(rel)
    with _tree_folder(root, parts[:-1], make=True) as folder:
        fd = _tree_open(folder, parts[-1], os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            if os.fstat(fd).st_nlink != 1:
                raise InTheWay(rel, "is a file with another name")
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        _tree_sync(folder, os.fsync)


def _tree_bytes(folder, name):
    try:
        fd = _tree_open(folder, name, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _tree_rename(folder, source, target):
    if isinstance(folder, int):
        os.replace(source, target, src_dir_fd=folder, dst_dir_fd=folder)
    else:
        _replace(os.path.join(folder, source), os.path.join(folder, target))


def _tree_seal(folder, name, identity):
    """Read a seal through the held tree, including when its ledger is absent (LEADS-ADAPTERS)."""
    raw_seal = _tree_bytes(folder, name + ".seal.json")
    seal = None
    if raw_seal is not None:
        try:
            seal = json.loads(raw_seal.decode(UTF8))
        except (ValueError, UnicodeError) as exc:
            raise LedgerCorruption(f"the seal of {identity} cannot be read: {exc}") from exc
        if not isinstance(seal, dict) or not isinstance(seal.get("retired"), int) or seal["retired"] < 1 \
                or not isinstance(seal.get("digest"), str):
            raise LedgerCorruption(f"the seal of {identity} is malformed")
    return seal


def _tree_replace_bytes(folder, name, data):
    """Publish a recovered seal in the folder held for its ledger, even if that folder moved (LEDGER-HISTORY)."""
    temporary = f".{name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    fd = _tree_open(folder, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        _tree_rename(folder, temporary, name)
    except BaseException:
        try:
            _tree_unlink(folder, temporary)
        except OSError:
            pass
        raise
    _tree_sync(folder, os.fsync)


def _chained_in_tree(root, rel, chain, record=None, every=False):
    """The opt-in ledger's read or append, including recovery, under its usual sidecar lock (LEADS-ADAPTERS).

    Keep the JSONL, chain, seals and machine checkpoint unchanged. Only this ledger's filesystem operations
    change: the lock, ledger and seal are opened in the held folder; truncation and rollback use the same
    descriptor, never a name an agent can redirect. Other ledger callers keep their existing API.
    """
    nothing = 0 if every else []                    # what an empty ledger is to the caller
    parts = _tree_parts(rel)
    name = parts[-1]
    identity = os.path.join(os.path.realpath(root), *parts)
    # Before the store is read at all: a store inside the tree attests nothing and is never acted on (OPTIN-ATTESTED).
    if _store_in_tree(root):
        if record is None:
            return nothing
        raise LedgerCorruption(
            f"the checkpoint store {checkpoint_path()} lies inside the project, where an agent writes: ao records "
            "no opt-in beside it - point AO_LEDGER_CHECKPOINTS outside the tree, or unset it")
    recovering = "intent" in _checkpoint_entry(identity, identity=identity)
    with _tree_folder(root, parts[:-1], make=record is not None or recovering) as folder:
        if folder is None:
            _check_missing_chained_ledger(identity, chain, identity=identity)
            return nothing
        if record is None and not recovering:
            # Reading a setting before any opt-in must not create a lock in an otherwise clean project, even
            # when init refuses during planning. A checkpoint intent needs locked recovery; an in-tree intent
            # needs locked refusal (LEADS-ADAPTERS, LEDGER-HISTORY).
            for leaf in (name, name + ".seal.json.pending"):
                try:
                    _tree_stat(folder, leaf)
                except FileNotFoundError:
                    continue
                break
            else:
                _check_missing_chained_ledger(identity, chain, seal=_tree_seal(folder, name, identity), identity=identity)
                return nothing
        with _exclusive_lock(identity + ".lock", _folder=folder):
            _settle_seal(identity, chain, CHAIN_PREVIOUS_FIELD, _folder=folder, identity=identity)
            data = _tree_bytes(folder, name)
            seal = _load_seal(identity, _folder=folder)
            if data is None:
                _check_missing_chained_ledger(identity, chain, seal=seal, identity=identity)
                data = b""
            attested = _attested_length(identity, seal)
            if record is None:
                records, _ = _jsonl_records(data)
                if every:
                    return len(records)
                # Only the rows ao recorded are read and checked: a tail past them - well formed or not - is nobody's
                # record, and never makes ao's own unreadable (OPTIN-ATTESTED).
                rows = [_jsonl_row(body, index, identity) for index, body in records[:attested]]
                _validate_chained_rows(identity, rows, chain, CHAIN_PREVIOUS_FIELD, seal=seal)
                _check_committed_length(identity, rows, chain, seal=seal, identity=identity)
                return rows
            past = len(_jsonl_records(data)[0]) - attested
            if past > 0:
                # Appended after them, ao's record would vouch for rows it never wrote (OPTIN-ATTESTED). Counted as a
                # read counts them, before anything parses them.
                raise LedgerCorruption(f"{rel}: {_unattested_repair(past, attested)}")
            rows = _jsonl_rows(data, identity)
            _validate_chained_rows(identity, rows, chain, CHAIN_PREVIOUS_FIELD, seal=seal)
            _check_committed_length(identity, rows, chain, seal=seal, identity=identity)
            retired = seal["retired"] if seal else 0
            previous = chained_row_digest(rows[-1], chain) if rows else (seal["digest"] if seal else None)
            chained = {CHAIN_PREVIOUS_FIELD: previous, "ordinal": retired + len(rows) + 1}
            chained.update(record)
            payload = _encode_jsonl(chained)
            created = False
            try:
                fd = _tree_open(folder, name, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
                created = True
            except FileExistsError:
                fd = _tree_open(folder, name, os.O_RDWR)
            remove_created = created
            try:
                if os.fstat(fd).st_nlink != 1:
                    raise InTheWay(rel, "has another name; ao appends only to the project's own file")
                # Refuse a different leaf swapped in since the read; a hard link is refused before any truncation.
                with os.fdopen(os.dup(fd), "rb") as handle:
                    current = handle.read()
                if current != data:
                    raise InTheWay(rel, "changed while ao read it; retry the opt-in")
                boundary = len(data) if not data or data.endswith((b"\n", b"\r")) else \
                    max(data.rfind(b"\n"), data.rfind(b"\r")) + 1
                os.ftruncate(fd, boundary)
                if boundary != len(data):
                    os.fsync(fd)
                os.lseek(fd, boundary, os.SEEK_SET)
                try:
                    _write_all(fd, payload)
                    os.fsync(fd)
                    if created:
                        _tree_sync(folder, os.fsync)
                    _record_committed_length(identity, retired + len(rows) + 1,
                                             chained_row_digest(chained, chain), identity=identity)
                except BaseException:
                    os.ftruncate(fd, boundary)
                    os.fsync(fd)
                    raise
                remove_created = False
            finally:
                held = os.fstat(fd)
                os.close(fd)
                if remove_created:
                    try:
                        named = _tree_stat(folder, name)
                        if (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino):
                            _tree_unlink(folder, name)
                    except OSError:
                        pass
            return chained


def append_chained_in_tree(root, rel, record, chain):
    """Append an opt-in without following a folder or leaf link (LEADS-ADAPTERS)."""
    return _chained_in_tree(root, rel, chain, record)


def read_chained_in_tree(root, rel, chain):
    """Read opt-ins under the same held-folder lock as their append (LEADS-ADAPTERS): the rows this machine's checkpoint
    store records ao appending, and no others (OPTIN-ATTESTED).

    The chain proves no committed row was changed and the recorded length that none was cut off; neither says who
    wrote a row past that length. A person's opt-in weakens a guarantee, and the implementer can write any file in
    the tree: the first row of a chain needs no digest at all, and one past ao's last append needs only the digest of
    the row before it. A row there - in a ledger this machine never recorded, or added after ao's last append - is
    nobody's record.
    """
    return _chained_in_tree(root, rel, chain)


def unattested_in_tree(root, rel, chain):
    """(rows this machine's checkpoint store does not record ao appending, rows it does) of an in-tree ledger
    (OPTIN-ATTESTED)."""
    attested = len(_chained_in_tree(root, rel, chain))
    return max(0, _chained_in_tree(root, rel, chain, every=True) - attested), attested


def _store_in_tree(root):
    """Whether this machine's checkpoint store lies inside the project's tree (OPTIN-ATTESTED).

    AO_LEDGER_CHECKPOINTS moves the store, and the implementer can name it on any command it runs: a store inside the
    tree is a file it writes, so a row it wrote and a count it wrote beside it would vouch for each other. Such a store
    attests nothing, and ao appends no opt-in beside it, as `ao doctor` already names the variable (PYTHON-ENV).

    Compared by what the folders are, not how their names are spelled: a case-insensitive file system answers the
    same folder to `/A/Proj` and `/a/proj`, and the real path keeps whichever spelling it was given. Each folder on
    the store's path, as named and as resolved, is held to the root's device and inode, so neither case nor a link
    moves it out.
    """
    try:
        top = os.stat(root)
    except OSError:
        return False
    named = checkpoint_path()
    if os.pardir in re.split(r"[\\/]+", named):
        # `a/link/..` is the link target's parent to the file system and `a` to a path read as text: either reading
        # can hide the other, so a store named with `..` is taken as the tree's.
        return True
    given = named if os.path.isabs(named) else os.path.join(os.getcwd(), named)
    # The path as named and as resolved, both: one through a link the implementer keeps in the tree is the tree's,
    # wherever the link points as ao looks, since the link can point elsewhere by the time the store is read.
    for path in (given, os.path.realpath(named)):
        if not top.st_ino:
            # A file system that numbers no file: the paths, case folded where the platform folds case.
            store, folder = os.path.normcase(path), os.path.normcase(os.path.realpath(root))
            if store == folder or store.startswith(folder.rstrip(os.sep) + os.sep):
                return True
            continue
        here = os.path.dirname(path)
        while True:
            try:
                held = os.stat(here)
            except OSError:
                held = None
            if held is not None and (held.st_dev, held.st_ino) == (top.st_dev, top.st_ino):
                return True
            parent = os.path.dirname(here)
            if parent == here:
                break
            here = parent
    return False


def _unattested_repair(foreign, attested):
    """What a person does about rows past ao's record, keeping the ones ao did record (OPTIN-ATTESTED)."""
    keep = (f"the first {attested} row(s) are ao's own and stay; a person removes exactly the last {foreign} line(s) "
            "of the file") if attested else "no row of it is ao's own; a person moves the file aside"
    return (f"{foreign} row(s) the checkpoint store of this machine ({checkpoint_path()}) does not record ao "
            f"appending - something other than ao wrote them, or an append did not finish. They turn nothing off and "
            f"ao appends nothing after them: {keep}, and records again what is missing")


def _attested_length(identity, seal):
    """How many of a ledger's live rows this machine's checkpoint store records ao committing (OPTIN-ATTESTED)."""
    count = _checkpoint_entry(identity, identity=identity).get("count")
    return max(0, (count if isinstance(count, int) else 0) - (seal["retired"] if seal else 0))


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
        _settle_seal(path, chain, previous_field, legacy_prefix)
        if not os.path.exists(path):
            _check_missing_chained_ledger(path, chain, seal=_load_seal(path))
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
