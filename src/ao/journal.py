"""What the watchdog starts, kept in one journal per project, so a crash never starts it twice (JOURNAL).

The watchdog started an architect and recorded it afterwards: a kill -9, a power cut or a Ctrl+C
between the two left a headless architect no record named, and the next cycle, seeing none, woke
another into the same session. A step is now claimed under a key before its process starts - the
session and the reports it is handed name a wake - with a lease, and the process's pid and start
are written the moment it has one. A claim that holds, or a process a step names that still runs,
is a step under way, and the same key is not started again until the lease has passed.

SQLite is in Python's standard library, and one transaction is all-or-nothing: a claim is taken by
one writer, two cycles or a cycle and a command alike. The journal is written ahead (WAL) and synced
on every commit (synchronous=FULL). It lives with the watchdog's other per-project state in ~/.ao.
Where an interpreter was built without sqlite3 there is no journal, and the watchdog falls back on
its own records and process scans, as before.
"""
import os
import time

try:
    import sqlite3
except ImportError:                      # an interpreter built without it
    sqlite3 = None

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS steps (key TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, "
    "lease_until REAL NOT NULL, pid INTEGER, start REAL, at REAL NOT NULL, detail TEXT)",
    "CREATE TABLE IF NOT EXISTS sleeps (id TEXT PRIMARY KEY, kind TEXT NOT NULL, at REAL NOT NULL, "
    "wake_at REAL, reason TEXT, closed_at REAL, outcome TEXT)",
)


def available():
    return sqlite3 is not None


def _connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    for statement in SCHEMA:
        connection.execute(statement)
    return connection


def _transaction(path, work):
    """Run `work(connection)` in one immediate transaction; its result, or None where there is no journal."""
    if sqlite3 is None:
        return None
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = work(connection)
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        return result
    finally:
        connection.close()


def claim(path, key, kind, lease, now=None):
    """Whether this caller now holds `key`: it is new, or the step that held it is done or its lease has passed."""
    now = time.time() if now is None else now

    def work(connection):
        row = connection.execute("SELECT state, lease_until FROM steps WHERE key = ?", (key,)).fetchone()
        if row and row[0] in ("claimed", "started") and row[1] > now:
            return False
        connection.execute("INSERT OR REPLACE INTO steps (key, kind, state, lease_until, pid, start, at) "
                           "VALUES (?, ?, 'claimed', ?, NULL, NULL, ?)", (key, kind, now + lease, now))
        return True
    held = _transaction(path, work)
    return True if held is None else held


def started(path, key, pid, start):
    """The process a claimed step started: its pid and when it began, so a later reader tells it from a reuse."""
    _transaction(path, lambda c: c.execute("UPDATE steps SET state = 'started', pid = ?, start = ? WHERE key = ?",
                                           (pid, start, key)))


def finished(path, key, outcome="done"):
    _transaction(path, lambda c: c.execute("UPDATE steps SET state = ?, lease_until = 0 WHERE key = ?",
                                           (outcome, key)))


def step(path, key):
    """{state, pid, start, lease_until} of one step, or None."""
    if sqlite3 is None or not os.path.exists(path):
        return None
    connection = _connect(path)
    try:
        row = connection.execute("SELECT state, pid, start, lease_until FROM steps WHERE key = ?", (key,)).fetchone()
    finally:
        connection.close()
    return dict(zip(("state", "pid", "start", "lease_until"), row)) if row else None


def under_way(path, kind, now=None):
    """[(key, pid, start)] of the steps of `kind` whose claim holds; a pid is None while a start was not written."""
    if sqlite3 is None or not os.path.exists(path):
        return []
    now = time.time() if now is None else now
    connection = _connect(path)
    try:
        return [tuple(row) for row in connection.execute(
            "SELECT key, pid, start FROM steps WHERE kind = ? AND state IN ('claimed', 'started') AND lease_until > ?",
            (kind, now))]
    finally:
        connection.close()
