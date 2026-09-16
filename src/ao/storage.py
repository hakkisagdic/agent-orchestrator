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


def _record_committed_length(path, count, digest):
    store = checkpoint_path()
    os.makedirs(os.path.dirname(store) or ".", exist_ok=True)
    with _exclusive_lock(store + ".lock"):
        data = _load_committed_lengths()
        data[os.path.realpath(path)] = {"count": count, "digest": digest, "at": int(time.time())}
        temporary = f"{store}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, store)


def _check_committed_length(path, rows, chain):
    """Refuse a ledger shorter than its recorded length, or diverging from it (#62)."""
    mark = _load_committed_lengths().get(os.path.realpath(path))
    if not isinstance(mark, dict) or not isinstance(mark.get("count"), int) or mark["count"] <= 0:
        return
    count = mark["count"]
    if len(rows) < count:
        raise LedgerCorruption(
            f"{path} holds {len(rows)} committed rows but recorded {count}: rows were removed from its end"
            f" (the count is kept in {checkpoint_path()})"
        )
    if chained_row_digest(rows[count - 1], chain) != mark.get("digest"):
        raise LedgerCorruption(
            f"{path} row {count} does not match the digest recorded for it in {checkpoint_path()}"
        )


def _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix=False):
    expected = None
    start = 0
    if legacy_prefix:
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
    for index, row in enumerate(rows[start:], start + 1):
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
    if not os.path.exists(path):
        _check_committed_length(path, [], chain)
        return []
    with _exclusive_lock(path + ".lock", timeout=timeout):
        rows = _read_jsonl_unlocked(path, allow_partial_tail)
        _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix)
        _check_committed_length(path, rows, chain)
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


def replace_file_durably(path, data, *, _checkpoint=None, _fsync=None):
    """Replace a whole file so it is only ever seen with its old bytes or its new ones.

    ``open(path, "w")`` truncates first. A crash, kill or full disk before the new
    bytes are down leaves a zero-byte file, and a zero-byte ``.ao/config.json`` no
    longer holds the ``capability_matrix`` that keeps strict authority on (#56).
    This writes a temporary file beside the target, fsyncs it, renames it over the
    target and fsyncs the directory. ``_checkpoint`` and ``_fsync`` are the private
    test seams the ledger appends take.
    """
    fsync = _fsync or os.fsync
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        mode = 0o644
    temporary = os.path.join(parent, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(temporary, flags, mode)
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
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    _call(_checkpoint, "replaced")
    if _sync_directory(parent, fsync):
        _call(_checkpoint, "directory-fsynced")


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


def _encode_jsonl(record):
    return (json.dumps(
        record, ensure_ascii=False, separators=(",", ":")
    ) + "\n").encode(UTF8)


def _append_payload_unlocked(path, parent, payload, write, fsync, checkpoint):
    """Append one encoded row while the caller owns the sidecar lock."""
    created = not os.path.exists(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o644)
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
    finally:
        os.close(fd)

    if created and _sync_directory(parent, fsync):
        _call(checkpoint, "directory-fsynced")


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
                         previous_field=CHAIN_PREVIOUS_FIELD, legacy_prefix=False,
                         _checkpoint=None, _write=None, _fsync=None):
    """Atomically select a predecessor, append, and persist one chained row.

    Existing committed rows are validated before a successor is constructed.
    Predecessor selection and append share one lock, so concurrent writers form
    one linear chain instead of siblings that name the same predecessor.
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
        _repair_partial_tail(path, fsync, _checkpoint)
        rows = _read_jsonl_unlocked(path, allow_partial_tail=False)
        _validate_chained_rows(path, rows, chain, previous_field, legacy_prefix)
        _check_committed_length(path, rows, chain)
        previous = chained_row_digest(rows[-1], chain) if rows else None
        chained = {previous_field: previous, "ordinal": len(rows) + 1}
        chained.update(record)
        payload = _encode_jsonl(chained)
        _append_payload_unlocked(
            path, parent, payload, write, fsync, _checkpoint
        )
        _record_committed_length(path, len(rows) + 1, chained_row_digest(chained, chain))
    return chained
