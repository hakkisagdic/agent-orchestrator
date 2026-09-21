"""A transcript a database holds is read through what its adapter declares, and only as much of it as declared.

A JSONL store gives a transcript as a file, so a path is enough to read one. A store that keeps
every session of every project in one database has no such path: which rows are this session's,
which column holds a record's body and where a message's parts hang are all facts inside the
schema. `DatabaseTail` carries the session a read is for and asks the adapter for the rest, so
no core module names a store's table or column (#76) - and a store that declares nothing is read
as holding nothing rather than as some other harness's store.
"""
import json
import os
import time
from datetime import datetime

from ao import lib as A

# A store of one message table and one part table, in the shape a real install was found in:
# times in milliseconds, the record's fields in a JSON column, the children naming their parent.
SESSIONS = [("ses_one", "/work/alpha", "first turn", 1000), ("ses_two", "/work/beta", "other agent", 900)]
MESSAGES = [("msg_a1", "ses_one", "user", None, 1000, 1000), ("msg_a2", "ses_one", "assistant", "tool-calls", 2000, 2000),
            ("msg_a3", "ses_one", "assistant", "stop", 3000, 9000), ("msg_b1", "ses_two", "user", None, 800, 800)]
PARTS = [("prt_1", "msg_a1", "ses_one", "text", {"text": "do the thing, then write the file it asks for"}, 1000),
         ("prt_2", "msg_a2", "ses_one", "tool", {"tool": "write", "callID": "c1",
                                                 "state": {"status": "completed", "input": {"filePath": "/work/alpha/x"}}}, 2000),
         ("prt_3", "msg_a2", "ses_one", "text", {"text": "writing it now, which is the second thing I was asked to do"}, 2100),
         ("prt_4", "msg_b1", "ses_two", "text", {"text": "not this session's, and no reader of the first should say it"}, 800)]

RECORD = {
    "table": "message", "kind": "role", "body": "data", "session_key": "session_id",
    "order": "time_created", "time": "time_created", "time_unit": "milliseconds", "text_keys": ["text"],
    "children": {"table": "part", "key": "message_id", "parent_key": "id", "session_key": "session_id",
                 "body": "data", "as": "parts", "order": "time_created", "time": "time_created",
                 "time_unit": "milliseconds"},
}
FRESHNESS = {"query": "SELECT MAX(time_updated) FROM part WHERE session_id = ?", "time_unit": "milliseconds"}
STORE = {"kind": "sqlite-sessions",
         "query": "SELECT id, title, time_updated FROM session WHERE directory = ? AND parent_id IS NULL "
                  "ORDER BY time_updated DESC",
         "session": "id", "title": "title", "mtime": "time_updated", "mtime_unit": "milliseconds"}


def make_store(tmp_path, record=None, sessions=None):
    """A database holding two sessions' rows, and the package adapter that declares how to read it."""
    import sqlite3
    db = tmp_path / "state.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE session (id TEXT, directory TEXT, title TEXT, parent_id TEXT, time_updated INTEGER)")
    connection.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, "
                       "time_updated INTEGER, data TEXT)")
    connection.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
                       "time_created INTEGER, time_updated INTEGER, data TEXT)")
    connection.executemany("INSERT INTO session VALUES (?, ?, ?, NULL, ?)", SESSIONS)
    connection.executemany("INSERT INTO message VALUES (?, ?, ?, ?, ?)",
                           [(mid, sid, created, updated, json.dumps({"role": role, "finish": finish}))
                            for mid, sid, role, finish, created, updated in MESSAGES])
    connection.executemany("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
                           [(pid, mid, sid, at, at, json.dumps(dict(body, type=kind)))
                            for pid, mid, sid, kind, body, at in PARTS])
    connection.commit()
    connection.close()
    adapter = {"id": "somestore", "transcript": {"kind": "sqlite", "path": str(db), "record": record if record is not None
                                                  else RECORD, "freshness": FRESHNESS, "messages": {
                                                       "prompt": ["user"], "reply": ["assistant"], "blocks": "parts[]",
                                                       "match": {"type": "text"}},
                                                   "turn": {"end_when": {"type": "assistant", "field": "finish",
                                                                         "values": ["stop"]},
                                                            "conversation": ["user", "assistant"]},
                                                   "tool_call": {"type": "assistant", "blocks": "parts[]",
                                                                 "match": {"type": "tool"}, "name": "tool",
                                                                 "id": "callID", "args": "state.input",
                                                                 "result": "state.output",
                                                                 "path_keys": ["state.input.filePath"]}},
               "sessions": sessions if sessions is not None else STORE}
    return str(db), adapter


def read_adapter(tmp_path, monkeypatch):
    """The declared store, as a package adapter ao can find."""
    db, adapter = make_store(tmp_path)
    monkeypatch.setattr(A, "package_adapters", lambda: {adapter["id"]: adapter})
    return db, adapter


# ── the reading ───────────────────────────────────────────────────────────────

def test_a_session_is_read_as_records_with_its_children_nested(tmp_path, monkeypatch):
    db, adapter = read_adapter(tmp_path, monkeypatch)
    tail = A.DatabaseTail(db, "ses_one", A._block(adapter["transcript"], "record"),
                          A._block(adapter["transcript"], "freshness"))
    shape = A.transcript_shape(adapter)
    recs = tail.read()

    assert [A.record_kind(rec, shape) for rec in recs] == ["user", "assistant", "assistant"]
    assert [A.record_time(rec, shape) for rec in recs] == [
        A._epoch_iso(1000, "milliseconds"), A._epoch_iso(2000, "milliseconds"), A._epoch_iso(3000, "milliseconds")]
    assert [rec.get("finish") for rec in recs] == [None, "tool-calls", "stop"]
    assert [len(rec.get("parts") or []) for rec in recs] == [1, 2, 0]
    assert "not this session's" not in json.dumps([rec.get("parts") for rec in recs])


def test_a_record_holds_its_own_columns_and_its_json_columns_fields_together(tmp_path, monkeypatch):
    db, adapter = read_adapter(tmp_path, monkeypatch)
    tail = A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], adapter["transcript"]["freshness"])
    shape = A.transcript_shape(adapter)
    tool = shape["tool"]

    calls = [(name, args) for rec in tail.read() for name, args in A.tool_calls(A.record_body(rec, shape), tool)]
    assert calls == [("write", {"filePath": "/work/alpha/x"})]
    assert A.closes_turn(tail.read()[-1], shape) and not A.closes_turn(tail.read()[1], shape)
    assert [text for _, _, text in A.messages(tail.read(), 8, adapter)] == [
        "do the thing, then write the file it asks for", "writing it now, which is the second thing I was asked to do"]


def test_a_tail_is_the_newest_rows_and_no_more_than_its_bytes(tmp_path, monkeypatch):
    db, adapter = read_adapter(tmp_path, monkeypatch)
    tail = A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], adapter["transcript"]["freshness"])
    shape = A.transcript_shape(adapter)

    assert [A.record_kind(rec, shape) for rec in tail.read(1)] == ["assistant"]
    assert [A.record_kind(rec, shape) for rec in tail.read(100_000)] == ["user", "assistant", "assistant"]


def test_a_missing_or_unreadable_store_reads_as_no_records(tmp_path, monkeypatch):
    db, adapter = read_adapter(tmp_path, monkeypatch)
    tail = A.DatabaseTail(db, "ses_absent", adapter["transcript"]["record"], adapter["transcript"]["freshness"])

    assert tail.read() == []
    assert A.DatabaseTail(str(tmp_path / "nothing.db"), "ses_one", adapter["transcript"]["record"]).read() == []
    assert tail.last_activity() is None


def test_the_store_is_never_opened_for_writing(tmp_path, monkeypatch):
    """A second writer corrupts the agent's own state, so a read must not be able to start one."""
    import sqlite3
    import pytest
    db, adapter = read_adapter(tmp_path, monkeypatch)
    tail = A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], adapter["transcript"]["freshness"])
    connection = tail._connect()

    with pytest.raises(sqlite3.OperationalError):
        connection.execute("UPDATE part SET time_updated = 1 WHERE session_id = 'ses_one'")
    connection.close()
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True).execute("DELETE FROM message")
    assert len(tail.read()) == 3


# ── what a declaration may not do ─────────────────────────────────────────────

def test_a_name_that_is_not_one_word_is_not_written_into_a_query(tmp_path, monkeypatch):
    db, adapter = read_adapter(tmp_path, monkeypatch)
    for name in ("message; DROP TABLE part", "message'", "1message", "message name", ""):
        tail = A.DatabaseTail(db, "ses_one", dict(adapter["transcript"]["record"], table=name))
        assert tail.read() == []
        assert A._store_name(name) == ""
    assert len(A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"]).read()) == 3


def test_a_query_that_is_not_a_select_is_not_run(tmp_path, monkeypatch):
    db, adapter = read_adapter(tmp_path, monkeypatch)
    for declared in ("DELETE FROM part", "SELECT 1; DELETE FROM part", "PRAGMA table_info(part)", ""):
        tail = A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], {"query": declared})
        assert tail.last_activity() is None
        assert A._read_query(declared) == ""
    assert len(A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], FRESHNESS).read()) == 3
    assert len(A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], FRESHNESS).read()) == 3


def test_a_record_declaration_names_nothing_the_core_then_invents(tmp_path, monkeypatch):
    """No `transcript.record` means no records, not another store's tables guessed at."""
    db, _ = make_store(tmp_path)
    assert A.DatabaseTail(db, "ses_one", {}).read() == []
    assert A.DatabaseTail(db, "ses_one", {"table": "message"}).read() == []
    assert A.DatabaseTail(db, "", RECORD).read() == []


# ── where a session is found, and how old it is ───────────────────────────────

def test_a_session_is_found_by_the_working_directory_the_store_names(tmp_path, monkeypatch):
    db, adapter = read_adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "load_adapter", lambda ident: adapter)
    monkeypatch.setattr(A, "implementer_adapter", lambda: adapter)
    found = A.store_sessions(adapter["id"], "/work/alpha")

    assert [row["session"] for row in found] == ["ses_one"]
    assert found[0]["title"] == "first turn"
    assert abs(found[0]["mtime"] - 1.0) < 0.001
    assert isinstance(found[0]["transcript"], A.DatabaseTail)

    resolved, meta = A.role_session_paths({"root": "/work/alpha", "implementer": {"session": "ses_one",
                                                                                 "adapter": adapter["id"]}},
                                          "implementer")
    assert meta is None and resolved == A.DatabaseTail(db, "ses_one", RECORD, FRESHNESS)
    assert os.path.exists(resolved) and os.path.getsize(resolved) == os.path.getsize(db)


def test_a_working_directory_is_a_bound_value_never_query_text(tmp_path, monkeypatch):
    """A path is something a person can name, and a quote in it must not end the query."""
    db, adapter = read_adapter(tmp_path, monkeypatch)
    import sqlite3
    connection = sqlite3.connect(db)
    connection.execute("INSERT INTO session VALUES ('ses_odd', '/work/?'';DROP--', 'quoted', NULL, 500)")
    connection.commit()
    connection.close()

    assert A._sqlite_sessions(adapter["id"], STORE, "/work/?'';DROP--") == []
    assert A._sqlite_sessions(adapter["id"], STORE, "/work/alpha") and A._sqlite_sessions(adapter["id"], STORE, "") == []


def test_a_databases_own_clock_says_when_this_session_wrote(tmp_path, monkeypatch):
    """The file is written by every session in it, so its age is a machine's, not this agent's."""
    db, adapter = read_adapter(tmp_path, monkeypatch)
    tail = A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], adapter["transcript"]["freshness"])
    shape = A.transcript_shape(adapter)
    old = time.time() - 100_000
    os.utime(db, (old, old))

    assert tail.last_activity() == 2.1                                  # MAX of this session's part times
    assert A.last_write(tail, shape) == 2.1
    assert int(time.time() - os.path.getmtime(db)) > 90_000

    silent = A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], {})
    assert silent.last_activity() is None and A.last_write(silent, shape) == old


def test_a_busy_read_of_a_store_asks_the_store_and_not_the_file(tmp_path, monkeypatch):
    import sqlite3
    db, adapter = read_adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "load_adapter", lambda ident: adapter)
    monkeypatch.setattr(A, "agent_pids", lambda root, a: [123])
    cfg = {"root": "/work/alpha", "implementer": {"session": "ses_one", "adapter": adapter["id"]}}
    os.utime(db, (time.time() - 100_000, time.time() - 100_000))
    connection = sqlite3.connect(db)

    connection.execute("UPDATE part SET time_updated = ? WHERE session_id = 'ses_one'", (int(time.time() * 1000),))
    connection.commit()
    assert A.busy(cfg, adapter)[0] == "working"

    connection.execute("UPDATE part SET time_updated = 1")
    connection.commit()
    connection.close()
    assert A.busy(cfg, adapter)[0] == "idle"


def test_a_time_in_milliseconds_is_rendered_in_the_readers_own_timezone(tmp_path, monkeypatch):
    """A panel that prints UTC next to a local clock is an observation tool that lies by three hours."""
    db, adapter = read_adapter(tmp_path, monkeypatch)
    tail = A.DatabaseTail(db, "ses_one", adapter["transcript"]["record"], adapter["transcript"]["freshness"])
    shape = A.transcript_shape(adapter)
    record = tail.read()[-1]

    stamp = A.record_time(record, shape)
    assert stamp.endswith("Z") and len(stamp) == 24
    assert A.local_hhmm(stamp) == datetime.fromtimestamp(3.0).strftime("%H:%M")
