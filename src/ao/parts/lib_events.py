"""The machine's event log: what ao did, one line an event, in ~/.ao/events.jsonl (EVENTS-LOG).

A part of src/ao/lib.py, run in its namespace by `_part`; it is not importable on its own.

A surface that wanted to know what ao was doing had to poll: `ao status` read a project's files
again, and a person waiting on a submitted review ran `ao reviews` until it came back. Now ao
appends one line to a file the whole machine shares when it records a verification, submits a
review or sees a submitted one end, grants commit authority, sends mail, and nudges or wakes an
agent, and `ao events --follow`, or any program that reads the file, learns of each as it happens.

The log observes the work and is never part of it. Nothing reads it to decide anything, so it is
no evidence: it is kept as the other observation stores are, held to its bound as it is written,
its oldest lines going first. And writing an event never fails the command that did the work.
"""


# How long an event waits for another process's append or trim of the log. Each holds the lock
# for one short write, or one rewrite of a bounded file, so a wait this long means something is
# stuck, and the event is given up and said rather than holding up the work it reports.
EVENTS_LOCK_SECONDS = 5


def events_path():
    """Where the machine's event log is: ~/.ao/events.jsonl, or the file AO_EVENTS names.

    AO_EVENTS is how the tests keep their events apart from a person's, as AO_SETTINGS keeps
    their settings apart.
    """
    return os.environ.get("AO_EVENTS") or os.path.join(HOME, ".ao", "events.jsonl")


def event_line(row):
    """One event as the log holds it: compact JSON on one line, as UTF-8 bytes.

    A value JSON cannot hold is written as its text, so an odd value costs its form, never the event.
    """
    return (json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode(UTF8)


def emit_event(root, kind, data=None):
    """Append one event of this project to the machine's log; the row written, or None when it could not be.

    `data` is the event's short payload - ids, names and verdicts, never a body, a diff or a
    transcript - with each value that is None left out. Every string in it is scanned the way
    evidence is (#48): the log is a file other programs read, and docs/safety.md keeps secrets
    out of the event log. The project is named by its key (project_key), the name its files in
    ~/.ao carry, so one line of the log and the rest of what ao keeps of a project agree.

    The append and the bound share one lock, so an event written while another process trims
    the file is not lost with the old file. A line a killed writer left unfinished is ended
    first, so it spoils only itself.

    Nothing here raises into the command that did the work: the verification, the grant or the
    mail it reports stands whether or not its event could be written. An event that could not
    be, or a bound that could not be held, is said on standard error - never on standard output,
    where the MCP server speaks its protocol - and the command goes on.
    """
    path = events_path()
    try:
        from .storage import _exclusive_lock
        payload = {key: value for key, value in (data or {}).items() if value is not None}
        row = {"at": round(time.time(), 3), "project": project_key(root), "kind": kind,
               "data": scan_record(payload)}
        line = event_line(row)
        with _exclusive_lock(path + ".lock", timeout=EVENTS_LOCK_SECONDS):
            with open(path, "a+b") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell():
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        line = b"\n" + line
                    fh.seek(0, os.SEEK_END)
                fh.write(line)
            try:
                bound_store(path, settings.get(None, "retention.events_kb"))
            except Exception as exc:                    # the event is written; only the trim failed
                print(f"ao: {path} was not held to its bound: {type(exc).__name__}: {exc}", file=sys.stderr)
        return row
    except Exception as exc:
        print(f"ao: the {kind} event was not written to {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def event_row(line):
    """The event one line of the log holds, as a dict, or None for a line that holds none."""
    try:
        row = json.loads(line)
    except ValueError:                                  # not JSON, or not UTF-8
        return None
    return row if isinstance(row, dict) else None


def event_matches(row, project=None, since=None):
    """Whether an event is of `project`, its key compared without case, and written at or after `since`, an epoch.

    A key is compared without case because no two projects hold keys that differ only in case
    (project_key). An event whose time cannot be read is not shown as being after anything.
    """
    if project is not None and str(row.get("project", "")).lower() != str(project).lower():
        return False
    if since is not None:
        at = row.get("at")
        if isinstance(at, bool) or not isinstance(at, (int, float)) or at < since:
            return False
    return True


class EventTail:
    """A reader of the machine's event log that goes on from where it stopped, whatever the bound did meanwhile.

    The bound replaces the file with its newest lines (bound_store), so an offset alone would read
    on from the wrong place after a trim, or wait past the end of the shorter file. The reader keeps
    the last line it read and where that line ended as well: while the line still ends there, the
    file is the one it was reading; once it does not, the reader finds the line in the new file and
    goes on after it. A trim that took lines this reader had not reached leaves `missed` set, so a
    follower can say that events may be missing instead of passing over them. `skipped` counts the
    lines that held no event. A line still being written is left for the next poll.

    The file is opened for each poll and closed again: on Windows a handle held open would stop the
    bound from replacing the file.
    """

    def __init__(self, path=None):
        self.path = path or events_path()
        self.offset = 0                  # bytes of the file read
        self.mark, self.mark_end = b"", 0    # the last line read with content, and where it ended
        self.missed, self.skipped = False, 0

    def poll(self):
        """The events written since the last poll, oldest first.

        Raises OSError when the log exists and cannot be read; a log not written yet has no events.
        """
        try:
            fh = open(self.path, "rb")
        except FileNotFoundError:
            return []
        with fh:
            if not self._in_place(fh, os.fstat(fh.fileno()).st_size):
                self._find_place(fh)
            fh.seek(self.offset)
            chunk = fh.read()
        whole = chunk[:chunk.rfind(b"\n") + 1]
        rows, end = [], self.offset
        for line in whole.split(b"\n")[:-1]:
            end += len(line) + 1
            if not line.strip():
                continue
            self.mark, self.mark_end = line, end
            row = event_row(line)
            if row is None:
                self.skipped += 1
            else:
                rows.append(row)
        self.offset += len(whole)
        return rows

    def _in_place(self, fh, size):
        """Whether the file is still the one the last poll read: as long, and its last line still where it was."""
        if size < self.offset:
            return False
        if not self.mark:
            return True
        fh.seek(self.mark_end - len(self.mark) - 1)
        return fh.read(len(self.mark) + 1) == self.mark + b"\n"

    def _find_place(self, fh):
        """After a trim, go on after the last line read; if the trim took it, from the top, and say so."""
        if not self.mark:
            self.offset = 0
            return
        fh.seek(0)
        found = (b"\n" + fh.read()).rfind(b"\n" + self.mark + b"\n")
        if found < 0:
            self.offset, self.mark, self.mark_end, self.missed = 0, b"", 0, True
            return
        after = found + len(self.mark) + 1               # where the line ends in the file as it is now
        self.offset, self.mark_end = after + (self.offset - self.mark_end), after


def read_events(path=None):
    """Every event the log holds now, oldest first."""
    return EventTail(path).poll()


# ---- what a harness's lifecycle hooks said of its sessions (HOOK-SPOOL) ----------------------------------------

# The lifecycle events ao takes from a harness's hooks, by the name the hook command gives it, and what each says
# of the session it came from. A harness names its events its own way - Claude Code's Stop and Gemini's
# AfterAgent are both a turn-end here - so the command a hook runs names the event in ao's words, and an event
# ao does not know is not recorded.
AGENT_EVENTS = {
    "session-start": "idle",
    "prompt": "working",
    "tool": "working",
    "tool-done": "working",
    "subagent-end": "working",
    "compact": "working",
    "notification": "waiting",
    "turn-end": "idle",
    "session-end": "ended",
}


def agent_sessions(root, rows=None):
    """{session: {"session", "harness", "state", "event", "at", "first", "cwd"}} for this project, from what its
    harnesses' hooks told the machine's event log, the latest event of each session deciding its state (HOOK-SPOOL).

    A session is known here only from its hooks: one no hook has spoken of is not in it, and nothing here reads
    a harness's own store.
    """
    key = project_key(root)
    sessions = {}
    for row in read_events() if rows is None else rows:
        kind = str(row.get("kind") or "")
        if row.get("project") != key or not kind.startswith("agent-") or kind[len("agent-"):] not in AGENT_EVENTS:
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        event = kind[len("agent-"):]
        sid = str(data.get("session") or "(no session id)")
        session = sessions.setdefault(sid, {"session": sid, "first": row.get("at"), "harness": None, "cwd": None})
        session.update(event=event, at=row.get("at"), state=AGENT_EVENTS[event],
                       harness=data.get("harness") or session["harness"], cwd=data.get("cwd") or session["cwd"])
    return sessions
