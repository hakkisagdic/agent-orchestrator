"""Shared library for the agent-orchestrator reference scripts.

Standard library only, by design — see docs/surfaces.md. Nothing here writes to a
vendor's session store; observation is strictly read-only.
"""
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

HOME = os.path.expanduser("~")
from . import settings  # noqa: E402  (reads HOME through this module, lazily)
# Adapters ship with the package, but the documented install is still a git
# clone plus an alias — both have to resolve. Look beside this module first, then
# at the repository root, so neither path depends on the other existing.
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(_HERE))


def adapters_dir():
    for cand in (os.path.join(_HERE, "adapters"), os.path.join(REPO, "adapters")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(_HERE, "adapters")

C = {
    "reset": "\033[0m", "dim": "\033[2m", "b": "\033[1m", "green": "\033[32m",
    "red": "\033[31m", "yellow": "\033[33m", "cyan": "\033[36m",
    "mag": "\033[35m", "blue": "\033[34m",
}


# ── config ────────────────────────────────────────────────────────────────────

def find_root(start=None):
    """Nearest ancestor containing .ao/, else the git root, else cwd.

    An explicit path is taken at face value — asking for a directory and being
    given its parent is never what the caller meant. And $HOME/.ao is the global
    state directory, not a project marker, so the walk never treats home as a
    project root.
    """
    if start:
        return os.path.abspath(os.path.expanduser(start))
    d = os.path.abspath(os.getcwd())
    while True:
        if d != HOME and os.path.isdir(os.path.join(d, ".ao")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    top = sh("git rev-parse --show-toplevel", cwd=start or os.getcwd())
    return top or os.path.abspath(start or os.getcwd())


def _has_top_level_json_key(raw, wanted):
    """Recognize a top-level JSON object key even when the document is truncated.

    Config parse failure cannot be allowed to erase an opt-in authority boundary.
    Scan only complete depth-one string tokens followed by ``:``; decoding each
    token with the JSON decoder also recognizes escaped spellings such as
    ``capability\\u005fmatrix`` without mistaking nested keys or string values for
    a top-level declaration. Invalid UTF-8 elsewhere is retained as surrogate
    text and does not hide an ASCII key.
    """
    if not isinstance(raw, (bytes, bytearray)):
        return False
    text = bytes(raw).decode(UTF8, "surrogateescape")
    length = len(text)
    index = 1 if text.startswith("\ufeff") else 0
    while index < length and text[index].isspace():
        index += 1
    if index >= length or text[index] != "{":
        return False
    depth = 1
    index += 1
    while index < length and depth:
        char = text[index]
        if char == '"':
            start = index
            index += 1
            escaped = False
            while index < length:
                current = text[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    index += 1
                    break
                index += 1
            else:
                return False
            if depth == 1:
                after = index
                while after < length and text[after].isspace():
                    after += 1
                if after < length and text[after] == ":":
                    try:
                        key = json.loads(text[start:index])
                    except (TypeError, ValueError, UnicodeError):
                        key = None
                    if key == wanted:
                        return True
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        index += 1
    return False


PROJECT_CONFIG_MAX_BYTES = 1_048_576
PROJECT_CONFIG_MAX_CONTAINER_DEPTH = 64


def _json_container_depth_problem(raw, max_depth=PROJECT_CONFIG_MAX_CONTAINER_DEPTH):
    """Return a bounded lexical depth error before the recursive JSON decoder.

    JSON structural characters are ASCII, so scanning bytes is sufficient and
    avoids decoding or recursively parsing a document whose container nesting is
    already outside the project-state contract. Braces inside strings and escaped
    quotes do not affect depth; all other syntax validation remains json.loads's
    job.
    """
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):  # [ {
            depth += 1
            if depth > max_depth:
                return (
                    f".ao/config.json exceeds the {max_depth}-level "
                    "container depth"
                )
        elif byte in (0x5D, 0x7D):  # ] }
            depth -= 1
    return None


def project_config_document(root):
    """Read one bounded, regular, non-empty-object project config document.

    The returned raw prefix is always at most PROJECT_CONFIG_MAX_BYTES. Keeping
    the parse result and its problem together gives pre-dispatch loading and
    commit enforcement one state table instead of two subtly different readers.
    """
    import stat

    path = os.path.join(root, ".ao", "config.json")
    raw = b""
    try:
        listed = os.lstat(path)
    except OSError as exc:
        return {
            "config": None,
            "raw": raw,
            "problem": f".ao/config.json is missing or unreadable ({exc})",
        }
    if not stat.S_ISREG(listed.st_mode):
        return {
            "config": None,
            "raw": raw,
            "problem": ".ao/config.json is not a regular file",
        }

    try:
        with open(path, "rb") as fh:
            opened = os.fstat(fh.fileno())
            if not stat.S_ISREG(opened.st_mode) or (
                listed.st_dev,
                listed.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                return {
                    "config": None,
                    "raw": raw,
                    "problem": ".ao/config.json changed before it could be read",
                }
            raw = fh.read(PROJECT_CONFIG_MAX_BYTES)
            finished = os.fstat(fh.fileno())
    except OSError as exc:
        return {
            "config": None,
            "raw": raw,
            "problem": f".ao/config.json is missing or unreadable ({exc})",
        }

    if max(opened.st_size, finished.st_size) > PROJECT_CONFIG_MAX_BYTES:
        return {
            "config": None,
            "raw": raw,
            "problem": (
                ".ao/config.json exceeds the "
                f"{PROJECT_CONFIG_MAX_BYTES:,}-byte limit"
            ),
        }
    if opened.st_size != finished.st_size or len(raw) != finished.st_size:
        return {
            "config": None,
            "raw": raw,
            "problem": ".ao/config.json changed while it was being read",
        }

    problem = _json_container_depth_problem(raw)
    if problem:
        return {"config": None, "raw": raw, "problem": problem}
    try:
        parsed = json.loads(raw.decode(UTF8))
    except (UnicodeError, ValueError, RecursionError) as exc:
        return {
            "config": None,
            "raw": raw,
            "problem": f".ao/config.json is not readable valid JSON ({exc})",
        }
    if not isinstance(parsed, dict) or not parsed:
        return {
            "config": None,
            "raw": raw,
            "problem": ".ao/config.json must be a non-empty top-level JSON object",
        }
    return {"config": parsed, "raw": raw, "problem": None}


def load_config(root):
    """Load bounded project config; malformed legacy state degrades explicitly.

    Missing config remains optional for discovery and ``ao init``. When a config
    path exists but cannot satisfy the project-state contract, callers receive
    defaults plus ``_config_problem`` instead of an exception or unbounded parse.
    Enrollment enforcement consumes the same ``project_config_document`` result.
    """
    p = os.path.join(root, ".ao", "config.json")
    document = project_config_document(root)
    cfg = document["config"] or {}
    if document["problem"] is not None and os.path.lexists(p):
        cfg["_config_problem"] = document["problem"]
        # Legacy malformed config has historically degraded to discovery. Once
        # the strict key is present, however, a parse failure must not erase the
        # opt-in and silently reactivate legacy authority. ``raw`` is bounded.
        if _has_top_level_json_key(document["raw"], "capability_matrix"):
            cfg["_capability_matrix_error"] = True
    cfg.setdefault("root", root)
    cfg.setdefault("mailbox", "agent-mail")
    cfg.setdefault("reviews", "semantic-review")
    if "implementer" not in cfg:
        found = discover_session(root)
        if found:
            cfg["implementer"] = found
    return cfg


def write_project_config(root, text):
    """Write `.ao/config.json` whole or not at all (#56)."""
    from .storage import replace_file_durably
    replace_file_durably(os.path.join(root, ".ao", "config.json"), text.encode(UTF8))


def load_adapter(adapter_id):
    p = os.path.join(adapters_dir(), f"{adapter_id}.json")
    return json.load(open(p, encoding=UTF8)) if os.path.exists(p) else {}


# ── shell ─────────────────────────────────────────────────────────────────────

def _shell_word(word):
    if os.name == "nt":
        return subprocess.list2cmdline([word])
    import shlex
    return shlex.quote(word)


def sh(cmd, cwd=None, timeout=20):
    # cmd.exe has no /dev/null; it calls it NUL, and the command failed instead (#71).
    if os.name == "nt":
        cmd = cmd.replace("2>/dev/null", "2>NUL")
    # Through a shell too, git is the compiled one, not a script standing in front of it (#51).
    if cmd.startswith("git "):
        cmd = _shell_word(git_binary()) + cmd[3:]
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True, encoding=UTF8, errors="replace", timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


# ── session discovery ─────────────────────────────────────────────────────────

def discover_session(cwd):
    """Find the most recently active local agent session whose workspace is cwd.

    Kiro-style stores keep session.json with workspacePaths; that is enough to
    resolve the opaque per-workspace directory without asking the vendor CLI.
    """
    base = os.path.join(HOME, ".kiro", "sessions")
    best = None
    if os.path.isdir(base):
        for ws in os.listdir(base):
            wsd = os.path.join(base, ws)
            if not os.path.isdir(wsd):
                continue
            for sess in os.listdir(wsd):
                meta = os.path.join(wsd, sess, "session.json")
                msgs = os.path.join(wsd, sess, "messages.jsonl")
                if not (os.path.exists(meta) and os.path.exists(msgs)):
                    continue
                try:
                    m = json.load(open(meta, encoding=UTF8))
                except Exception:
                    continue
                if cwd not in (m.get("workspacePaths") or []):
                    continue
                mt = os.path.getmtime(msgs)
                if best is None or mt > best["_mtime"]:
                    best = {"adapter": "kiro", "session": sess, "workspace_hash": ws,
                            "cwd": cwd, "_mtime": mt}
    return best


def all_workspaces():
    """Every local agent session grouped by the workspace it belongs to.

    Lets `ao` answer "which projects can I watch?" without any configuration —
    the vendor stores already record their own workspace paths.
    """
    base = os.path.join(HOME, ".kiro", "sessions")
    found = {}
    if not os.path.isdir(base):
        return []
    for ws in os.listdir(base):
        wsd = os.path.join(base, ws)
        if not os.path.isdir(wsd):
            continue
        for sess in os.listdir(wsd):
            meta = os.path.join(wsd, sess, "session.json")
            msgs = os.path.join(wsd, sess, "messages.jsonl")
            if not (os.path.exists(meta) and os.path.exists(msgs)):
                continue
            try:
                m = json.load(open(meta, encoding=UTF8))
            except Exception:
                continue
            for path in (m.get("workspacePaths") or []):
                mt = os.path.getmtime(msgs)
                cur = found.get(path)
                if cur is None or mt > cur["mtime"]:
                    found[path] = {"path": path, "mtime": mt, "session": sess,
                                   "workspace_hash": ws, "adapter": "kiro",
                                   "title": m.get("title", ""), "status": m.get("status", "")}
    return sorted(found.values(), key=lambda r: r["mtime"], reverse=True)


def session_paths(cfg):
    """Transcript and metadata paths for the configured implementer.

    This was hard-wired to Kiro's store, which made the watchdog Kiro-only in
    practice: a Claude Code implementer resolved to no transcript, the watchdog
    said "nothing to watch" and quietly never ran. The claude-code adapter
    already declares its transcript layout; honour it.
    """
    impl = cfg.get("implementer") or {}
    sess = impl.get("session")
    if not sess:
        return None, None
    if impl.get("adapter") == "claude-code":
        cwd = impl.get("cwd") or cfg.get("root", "")
        return os.path.join(claude_project_dir(cwd), sess + ".jsonl"), None
    ws = impl.get("workspace_hash")
    if not ws:
        return None, None
    d = os.path.join(HOME, ".kiro", "sessions", ws, sess)
    return os.path.join(d, "messages.jsonl"), os.path.join(d, "session.json")


# ── transcript ────────────────────────────────────────────────────────────────

def read_tail(path, nbytes=900_000):
    """Last nbytes of a JSONL transcript, first (partial) line dropped."""
    recs = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - nbytes))
            blob = f.read().decode("utf-8", "ignore")
    except Exception:
        return recs
    lines = blob.split("\n")
    if size > nbytes:
        lines = lines[1:]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            pass
    return recs


def _strings(o, out, depth=0):
    if depth > 7:
        return
    if isinstance(o, str):
        if len(o) > 30:
            out.append(o)
    elif isinstance(o, dict):
        for k, v in o.items():
            if k in ("text", "content", "message"):
                _strings(v, out, depth + 1)
            elif isinstance(v, (dict, list)):
                _strings(v, out, depth + 1)
    elif isinstance(o, list):
        for x in o:
            _strings(x, out, depth + 1)


def local_hhmm(ts):
    """Render an ISO timestamp in the reader's own timezone.

    Transcripts store UTC. Slicing "…T00:44:12.525Z" for its characters prints
    00:44 next to a panel header showing local 03:44, and a message written
    thirty seconds ago then reads as three hours stale. In an observation tool
    that is not cosmetic: it is the difference between "the agent just said this"
    and "the agent stopped saying things", which are opposite conclusions.
    """
    if not ts or len(ts) < 16:
        return "--:--"
    try:
        t = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:                 # naive stamps are already local
            return dt.strftime("%H:%M")
        return dt.astimezone().strftime("%H:%M")
    except ValueError:
        return ts[11:16]


def messages(recs, limit=8, kinds=("assistant", "user")):
    """[(HH:MM, kind, text)] oldest→newest."""
    out = []
    for r in reversed(recs):
        pl = r.get("payload", r)
        if not isinstance(pl, dict):
            continue
        kind = pl.get("type") or pl.get("role")
        if kind not in kinds:
            continue
        buf = []
        _strings(pl, buf)
        text = " ".join(" ".join(buf).split())
        if len(text) < 40:
            continue
        ts = r.get("timestamp", "")
        out.append((local_hhmm(ts), kind, text))
        if len(out) >= limit:
            break
    return list(reversed(out))


def telemetry(recs, adapter):
    """Context %, per-turn and session cost, driven by the adapter's block."""
    tel = adapter.get("telemetry", {})
    ctx_spec = tel.get("context") or {}
    cost_spec = tel.get("cost") or {}
    out = {"ctx": None, "total": 0.0, "turns": 0, "last": None,
           "unit": cost_spec.get("unit", "unit")}
    for r in recs:
        pl = r.get("payload", r)
        if not isinstance(pl, dict):
            continue
        t = pl.get("type")
        if ctx_spec.get("from") == "transcript" and t == ctx_spec.get("type"):
            match = ctx_spec.get("match") or {}
            if all(pl.get(k) == v for k, v in match.items()):
                val = pl.get("value")
                if isinstance(val, dict):
                    key = ctx_spec.get("field", "").split(".")[-1]
                    if isinstance(val.get(key), (int, float)):
                        out["ctx"] = val[key]
        if cost_spec.get("from") == "transcript" and t == cost_spec.get("type"):
            for s in pl.get("promptTurnSummaries", []) or []:
                u = s.get("usage") or 0
                if isinstance(u, (int, float)):
                    out["total"] += u
                    out["turns"] += 1
                    out["last"] = (u, len(s.get("usedTools") or []))
    return out


_QUOTA = {"at": 0.0, "lines": []}


def quota(adapter, ttl=300):
    """Provider quota via the adapter's command, cached. Silent if absent."""
    spec = (adapter.get("telemetry") or {}).get("quota") or {}
    argv = spec.get("argv")
    if not argv:
        return []
    if time.time() - _QUOTA["at"] < spec.get("cache_seconds", ttl):
        return _QUOTA["lines"]
    out = sh(" ".join(argv) + " 2>/dev/null", cwd=HOME)
    lines = [l.strip() for l in out.split("\n")[1:]
             if l.strip() and "unknown" not in l]
    _QUOTA.update(at=time.time(), lines=lines)
    return lines


def busy(cfg, adapter):
    """(state, seconds_since_write, description). Conservative: status AND age."""
    msgs, meta = session_paths(cfg)
    if not msgs or not os.path.exists(msgs):
        return "unknown", None, ""
    age = int(time.time() - os.path.getmtime(msgs))
    status, desc = "", ""
    if meta and os.path.exists(meta):
        try:
            m = json.load(open(meta, encoding=UTF8))
            status = m.get(
                (adapter.get("busy") or {}).get("status_field", "status"), "")
            desc = m.get(
                (adapter.get("busy") or {}).get("description_field", "description"), "") or ""
        except Exception:
            pass
    idle_s = (adapter.get("busy") or {}).get("idle_seconds", 240)
    running = status in ((adapter.get("busy") or {}).get("running_values") or ["in_progress"])

    # A freshly-written transcript does not mean a live turn. The file keeps its
    # mtime after the process exits, so a killed agent reads as WORKING for the
    # whole idle window — the observation layer asserting the opposite of the
    # truth, at exactly the moment someone is looking to find out what happened.
    # Only ask the OS when the mtime would otherwise claim "working"; that is the
    # only case where the answer changes anything, and it keeps the panel cheap.
    if age < idle_s and not agent_pids(cfg["root"], adapter):
        return "stopped", age, desc

    if age < 120:
        state = "working"
    elif age < idle_s:
        state = "slowing"
    else:
        state = "idle" if not running else "idle"
    return state, age, desc


_SURFACES = {"at": 0.0, "rows": {}}


def tool_availability(ttl=600):
    """Which agent tools are actually usable on this machine.

    Two independent facts, and both matter: is the CLI installed (which), and is
    there an authenticated account for it. keyflip already answers the second for
    a range of tools and never reads the secret itself, so ask it rather than
    reinventing credential detection — align, do not depend: if keyflip is absent
    the CLI check still works on its own.
    """
    import shutil as _sh
    if time.time() - _SURFACES["at"] < ttl and _SURFACES["rows"]:
        return _SURFACES["rows"]
    rows = {}
    out = sh("keyflip surfaces 2>/dev/null", cwd=HOME)
    for line in out.split("\n"):
        line = line.strip()
        if not (line.startswith("●") or line.startswith("○")):
            continue
        body = line[1:].strip()
        name = re.split(r"\s{2,}", body)[0].strip().lower()
        rows[name] = {"account": line.startswith("●")}
    alias = {"gemini cli": "gemini", "codex cli": "codex", "github copilot": "copilot",
             "cursor": "cursor-agent", "aider": "aider", "opencode": "opencode"}
    named = {}
    for k, v in rows.items():
        named[alias.get(k, k)] = v
    for adapter_id, binary in (("kiro", "kiro-cli"), ("claude-code", "claude"),
                               ("antigravity", "agy"), ("opencode", "opencode"),
                               ("codex", "codex"), ("gemini", "gemini"),
                               ("cursor-agent", "cursor-agent"), ("aider", "aider"),
                               ("amp", "amp"), ("copilot", "copilot"),
                               ("amazon-q", "q"), ("command-code", "cmd")):
        entry = named.setdefault(adapter_id, {})
        entry["installed"] = bool(_sh.which(binary))
        entry["binary"] = binary
    _SURFACES.update(at=time.time(), rows=named)
    return named


BOARD_STATES = ("running", "blocked", "queued", "inbox", "verified", "done", "rejected")

# A2A (Agent2Agent) task states, for the day an A2A endpoint serialises this
# board. Kept as a table rather than adopted as the vocabulary: `inbox` and
# `queued` are both A2A `submitted`, and `verified` has no A2A equivalent at all
# — it is evidence gathered *before* completion, which is the distinction this
# whole tool exists to make. Renaming our states to match would delete it.
A2A_STATE = {
    "inbox": "submitted",       # pulled from a source, not yet admitted
    "queued": "submitted",      # admitted: has a written acceptance boundary
    "running": "working",
    "blocked": "input-required",
    "verified": "working",      # gates passed; authority to land not yet granted
    "done": "completed",
    "rejected": "rejected",
}


def board(root):
    """.ao/board.md — where each pre-authorised item currently is.

    A flat backlog answers "what is next" but not "what stopped, and on what".
    Once an agent is allowed to park a blocked slice and pick up the next item,
    that second question is the one a human actually needs on returning: the
    parked item is invisible precisely because work continued without it.

    The file is the single source of truth and the agent edits it directly, the
    way it edits mail and reviews. Parsing here stays deliberately forgiving —
    a board a human cannot hand-edit during an incident is a board that goes
    stale during exactly the incident it was built for.

    Returns {state: [{"id", "title", "notes": {k: v}}]}.
    """
    p = os.path.join(root, ".ao", "board.md")
    out = {st: [] for st in BOARD_STATES}
    if not os.path.exists(p):
        return out
    state = None
    for line in open(p, errors="replace", encoding=UTF8):
        line = line.rstrip()
        if line.lstrip().startswith("##"):
            # Every heading ends a section; only a state's own heading starts one (#33).
            m = re.match(r"^##\s+([a-z]+)\s*$", line.strip())
            state = m.group(1) if m and m.group(1) in out else None
            continue
        if not state or not line.lstrip().startswith("- "):
            continue
        item = line.lstrip()[2:].strip()
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", item)
        if not m:
            continue
        rest = [x.strip() for x in m.group(2).split("·")]
        notes = {}
        for chunk in rest[1:]:
            k, _, v = chunk.partition(":")
            if v:
                notes[k.strip()] = v.strip()
            elif chunk:
                notes[chunk] = ""
        out[state].append({"id": m.group(1), "title": rest[0] if rest else "",
                           "notes": notes})
    return out


def sources(root):
    """.ao/sources.json — external work queues feeding this project's board.

    `ao` speaks no tracker API and holds no tracker credential. The MCP client is
    the *agent*: it pulls from Linear/Jira/GitHub with a server that already
    exists, normalises the result to a file, and this side admits it. That keeps
    the zero-install, file-only core intact — a tracker is an addition for people
    who install one, never a prerequisite.
    """
    p = os.path.join(root, ".ao", "sources.json")
    if not os.path.exists(p):
        return {}
    try:
        cfg = json.load(open(p, encoding=UTF8))
    except Exception:
        return {}
    cfg.setdefault("sources", [])
    cfg.setdefault("wip_limit", 1)
    cfg.setdefault("refill_below", 3)
    return cfg


def inbox_files(root):
    """Normalised pulls waiting to be admitted: .ao/inbox/<source-id>.json."""
    d = os.path.join(root, ".ao", "inbox")
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".json")]


def binding_error(root, declared):
    """Refuse work that belongs to another project.

    A source is bound to exactly one repository. Without this check a tracker
    feeding project A can put an item on project B's board, and an agent that
    grinds boards without reading URLs will happily implement it there. The
    damage is silent and lands as a commit in the wrong repository, so the check
    belongs at the boundary rather than in anyone's memory.
    """
    if not declared:
        return "item file declares no bound_root"
    if os.path.realpath(declared) != os.path.realpath(root):
        return f"bound to {declared}, not {root}"
    return None


def plan_digest(root, item_id):
    """Content hash of the plan an item is worked against, or None."""
    base = item_id.split("/")[0]
    p = os.path.join(root, ".ao", "plans", f"{base}.md")
    if not os.path.exists(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def plan_baseline(root):
    """Plan hashes as they stood when each item was admitted."""
    # A torn or malformed row fails closed instead of silently dropping the
    # baselines around it, which switched the plan-drift refusal off (#68).
    from .storage import read_jsonl
    out = {}
    for rec in read_jsonl(os.path.join(root, ".ao", "ledger", "plans.jsonl")):
        if not isinstance(rec, dict):
            continue
        if rec.get("item") and rec.get("digest"):
            out[rec["item"]] = rec["digest"]
    return out


def plan_drift(root):
    """Items whose plan changed after admission.

    The implementer reads its plan; it does not write to it. When the document a
    slice is measured against can be edited by the thing being measured, every
    later check is circular. Recording the hash at admission turns that from an
    invisible failure into a line of output.
    """
    base = plan_baseline(root)
    drifted = []
    for item, was in base.items():
        now = plan_digest(root, item)
        if now and now != was:
            drifted.append(item)
    return drifted


def record_plan(root, item_id, digest):
    from .storage import append_jsonl
    append_jsonl(os.path.join(root, ".ao", "ledger", "plans.jsonl"),
                 {"item": item_id, "digest": digest, "at": int(time.time())})


def board_append(root, state, line):
    """Append one item line under `## <state>`, creating the section if needed.

    Append rather than rewrite: the implementing agent edits this same file, and
    a full rewrite would silently drop whatever it wrote between our read and our
    write.
    """
    p = os.path.join(root, ".ao", "board.md")
    text = open(p, encoding=UTF8).read() if os.path.exists(p) else "# Board\n"
    head = f"## {state}"
    if head not in text:
        text = text.rstrip("\n") + f"\n\n{head}\n"
    idx = text.index(head) + len(head)
    nl = text.index("\n", idx) + 1
    text = text[:nl] + line.rstrip() + "\n" + text[nl:]
    open(p, "w", encoding=UTF8).write(text)


def record_progress(root, cfg):
    """One line per watchdog check: what actually moved.

    Cheap by construction — the watchdog already runs, and this is a git call
    plus an append. The point is to have *history* of the artifacts, because a
    single snapshot cannot tell activity from progress.
    """
    msgs, _ = session_paths(cfg)
    # Coordination state is left out: the watchdog appends to this very ledger and
    # to its notices every cycle, and while those writes counted as editing the
    # spin check could never see a frozen run (#96).
    porcelain, churn = _product_changes(root, cfg)
    # Content churn, not file count. A slice deep in editing its established file
    # set holds the dirty *count* stable for many minutes — same eight files, new
    # content each cycle — and a count-only check reads that as frozen and cries
    # spin. The newest mtime across the changed set advances on every edit, so it
    # tells editing apart from a genuine stall. A long gate run writes nothing, so
    # it correctly stays frozen, and the minute threshold covers that case.
    rec = {"at": int(time.time()),
           "head": sh("git rev-parse --short HEAD", cwd=root),
           "dirty": len(porcelain), "churn": churn,
           "size": os.path.getsize(msgs) if msgs and os.path.exists(msgs) else 0}
    d = os.path.join(root, ".ao", "ledger")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "progress.jsonl")
    try:
        with open(p, encoding=UTF8) as fh:
            last = fh.readlines()[-1:]
        if last:
            prev = json.loads(last[0])
            if (prev.get("head"), prev.get("dirty"), prev.get("churn"), prev.get("size")) == \
               (rec["head"], rec["dirty"], rec["churn"], rec["size"]):
                return                                # nothing changed; do not log noise
    except Exception:
        pass
    with open(p, "a", encoding=UTF8) as fh:
        fh.write(json.dumps(rec) + "\n")


def spinning(root, min_minutes=6, min_samples=3):
    """Is the agent busy without producing anything?

    An agent stuck in an observe-and-wait loop is the hardest failure to see,
    because every health signal is green: the transcript grows, tool calls fire,
    cost accrues. The watchdog never questions "working". What separates five
    productive turns from five turns of re-checking whether the tree is stable is
    not activity — it is whether any artifact moved.

    So compare the two directly: transcript growing (busy) while HEAD and the
    dirty-file count hold still (nothing produced), sustained long enough that a
    slow gate cannot explain it. Returns minutes spent spinning, or None.

    This exact failure cost roughly forty minutes in this project's own run,
    while the panel showed WORKING in green the entire time.

    The defaults are deliberately low because of what this is used for. Reporting
    a false positive costs a line of output; acting on one costs a turn. So the
    *report* threshold is a few watchdog cycles, while anything that spends money
    or kills a process keeps its own, far more conservative bound. Set one
    threshold for both and you get the worst of each: too slow to be useful, and
    still not safe enough to act on.
    """
    p = os.path.join(root, ".ao", "ledger", "progress.jsonl")
    if not os.path.exists(p):
        return None
    recs = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    recs = recs[-40:]
    if len(recs) < min_samples:
        return None
    # Frozen means nothing was produced AND nothing was edited: same HEAD, same
    # dirty count, and no newer mtime in the changed set. Editing the same files
    # advances churn, so it breaks the run and is not spin.
    head = recs[-1].get("head")
    dirty = recs[-1].get("dirty")
    churn = recs[-1].get("churn")
    run = [r for r in reversed(recs)
           if r.get("head") == head and r.get("dirty") == dirty and r.get("churn") == churn]
    if len(run) < min_samples:
        return None
    grew = run[0].get("size", 0) > run[-1].get("size", 0)      # transcript still moving
    span = (run[0]["at"] - run[-1]["at"]) / 60
    return int(span) if grew and span >= min_minutes else None


HOLD_FILE = ".ao/hold"


def hold_state(root):
    """Who has stopped this project's agent, and why. None when running free."""
    p = os.path.join(root, HOLD_FILE)
    if not os.path.exists(p):
        return None
    try:
        st = json.load(open(p, encoding=UTF8))
    except Exception:
        st = {"by": "unknown", "reason": "unreadable hold file"}
    st["minutes"] = int((time.time() - st.get("at", time.time())) / 60)
    return st


def agent_pids(root, adapter, headless_only=False):
    """Agent processes whose working directory is this repository.

    Match on the process's cwd rather than its command line. The command line is
    unreliable — a long resume prompt gets truncated by `ps`, and the binary may
    be a bare `node` under a version manager — whereas the cwd is exactly the
    question being asked: is something editing *this* tree?

    One pass over the process table through the platform API (procs.py): exact
    argument vectors, cwd and parentage in milliseconds. The shell path this
    replaced — `pgrep -f`, one `lsof` per candidate, `ps` output split on
    whitespace — took seconds and mistook a shell that mentioned the agent, and
    a runtime under "Application Support", for turns.
    """
    from . import procs
    names = set()
    for key in ("send", "resume"):
        argv = (adapter.get(key) or {}).get("argv") or []
        if argv:
            names.add(os.path.basename(argv[0]))
    names.update({"kiro-cli", "claude", "claude-code", "codex", "cursor-agent"})
    want = os.path.realpath(root)
    me = os.getpid()
    out = []
    for pid in procs.all_pids():
        if pid == me:
            continue
        av = procs.argv(pid)
        if not av or not _is_agent_process(pid, names, av):
            continue
        cw = procs.cwd(pid)
        if cw is None:
            # No cwd on this platform (Windows): the repository path on the command
            # line is the next best evidence that this turn works in this tree.
            if any(os.path.realpath(a.rstrip("/\\")) == want for a in av if os.path.isabs(a)):
                out.append(pid)
        elif os.path.realpath(cw) == want:
            out.append(pid)
    # Processes ao itself started inside the repo — the reviewer above all — are
    # agents by every other test and writers by none. Exclude them and their
    # descendants: a reviewer's runtime child would otherwise surface as a root.
    helpers = helper_pids(root)
    if helpers and out:
        table = _proc_table()

        def under_helper(pid):
            seen = set()
            current = pid
            while current > 1 and current not in seen:
                if current in helpers:
                    return True
                seen.add(current)
                current = table.get(current, (0, 0, ""))[0]
            return False
        out = [p for p in out if not under_helper(p)]
    out = sorted(set(out))
    if headless_only:
        # Never a human's interactive session. `ao hold` once stopped seven
        # processes in a repository; two were the orchestrator's own turn and
        # five were the owner's live Claude sessions, cut mid-work. A hold
        # exists to stop unattended turns — the ones started with -p/--print —
        # and an interactive session, by definition, has a person in it who did
        # not ask to be stopped.
        out = [p for p in out if _is_headless(p)]
    return out


def _executable(t):
    """A real executable file at this path (a hook, so scenarios can fabricate a world)."""
    return "/" in t and os.path.isfile(t) and os.access(t, os.X_OK)


def _program_name(token):
    """Case-folded command basename without a Windows PATHEXT suffix."""
    base = os.path.basename(str(token).replace("\\", "/")).lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if base.endswith(suffix):
            return base[:-len(suffix)]
    return base


def _is_agent_process(pid, names, argv=None):
    """Is an agent binary actually on this command line?

    Works on the argument *vector*: a path with a space is one argument. The
    program itself, a sibling executable (kiro-cli-chat), or a runtime (node…)
    running something under the agent's install directory count; a shell whose
    command text mentions the agent, or a file named after it, does not. Windows
    PATHEXT suffixes and separators are normalized before matching.
    """
    from . import procs
    toks = argv if argv is not None else (procs.argv(pid) or [])
    if not toks:
        return False
    runtimes = ("node", "bun", "deno", "python", "python3")
    runtime = _program_name(toks[0]) in runtimes
    executable = _executable
    normalized_names = {_program_name(name) for name in names if name}
    for name in normalized_names:
        for i, token in enumerate(toks):
            base = _program_name(token)
            path = str(token).replace("\\", "/").lower()
            if base == name and (i == 0 or executable(token)):
                return True                    # the program itself
            if base.startswith(name + "-") and executable(token):
                return True                    # a sibling binary: kiro-cli-chat — a real executable
            if runtime and i <= 2 and f"/{name}/" in path:
                return True                    # a runtime under the agent's install dir
    return False


def _is_configured_agent_process(names, argv):
    """Exact configured launcher or runtime-package identity for one agent role.

    Writer discovery intentionally accepts sibling tools such as
    ``kiro-cli-chat``. Architect presence must not: a reviewer or monitor that
    shares a prefix is a different role and cannot suppress a wake.
    """
    if not argv:
        return False
    wanted = {_program_name(name) for name in names if name}
    if _program_name(argv[0]) in wanted:
        return True
    runtimes = {"node", "bun", "deno", "python", "python3"}
    if _program_name(argv[0]) not in runtimes:
        return False
    for token in argv[:3]:
        components = {
            _program_name(component)
            for component in str(token).replace("\\", "/").split("/")
            if component
        }
        if components & wanted:
            return True
    return False


def _is_headless(pid):
    """A turn started non-interactively (-p / --print / --no-interactive)."""
    from . import procs
    args = procs.argv(pid) or []
    return any(f in args for f in ("-p", "--print", "--no-interactive"))


def _proc_table():
    """pid -> (ppid, pgid, tty) for every process, from the platform API (see procs.py)."""
    from . import procs
    return procs.table()


def orphans(root, adapter, table=None):
    """Agent processes left behind by a turn that already ended.

    A turn is spawned in its own session (`start_new_session=True`), so the wrapper
    leads the process group and every child it starts — runtime, engine — inherits
    that group. When the wrapper dies and a child does not, the child is
    re-parented to init but keeps the dead leader's group id. That is the whole
    signature: no controlling terminal, and a process-group leader that no longer
    exists. Nothing a person is sitting in looks like that — a terminal session has
    a tty, a desktop-app session has the app as its live parent and leader.

    These matter because they are invisible to the reaper (the headless flag is on
    the wrapper, not on them) and visible to every writer count. Three of them sat
    in one repository for hours at 0% CPU, and the implementer — correctly applying
    its single-writer rule to what the process table showed — refused to write for
    the entire time, while each of its empty turns tripped the reaper again and
    made one more.
    """
    table = table or _proc_table()
    out = []
    for pid in agent_pids(root, adapter):
        ppid, pgid, tty = table.get(pid, (None, None, None))
        if pgid and tty == "??" and pgid != pid and pgid not in table:
            out.append(pid)
    return out


def writers(root, adapter):
    """Live turn roots in this tree, with orphans set aside.

    Returns (roots, orphans). The number of roots is what a single-writer rule
    should count: one root per turn, however many processes the turn is made of,
    and none for what a finished turn left behind.
    """
    table = _proc_table()
    dead = set(orphans(root, adapter, table))
    live = [p for p in agent_pids(root, adapter) if p not in dead]
    return process_trees(live, {pid: t[0] for pid, t in table.items()}), sorted(dead)


def kill_turn(pid, sig):
    """Signal a turn — the whole process group when this pid leads one.

    Signalling only the wrapper is how orphans are made: it exits, its runtime and
    engine children do not, and they keep the repository as their cwd. A turn we
    started is its own session, so its pid is its group id and one killpg reaches
    everything it spawned. A pid that leads no group is signalled alone. On
    Windows there are no groups to signal; `taskkill /T` kills the tree.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return
    try:
        if pgid == pid:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
    except OSError:
        pass


def sweep_orphans(pids, grace=3.0):
    """Stop orphaned agent processes by their (leaderless) groups.

    Each orphan still carries the group id of the turn that made it, and its own
    children carry the same one, so signalling the group clears the whole remnant
    at once — a child re-parented to an orphan would otherwise be orphaned a second
    time by the very cleanup. Returns the pids that were alive when we started.
    """
    import signal as _sig
    if os.name == "nt":
        for pid in pids:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return list(pids)
    groups = set()
    for pid in pids:
        try:
            groups.add(os.getpgid(pid))
        except OSError:
            pass
    for g in groups:
        try:
            os.killpg(g, _sig.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + grace
    while time.time() < deadline and any(_pid_alive(p) for p in pids):
        time.sleep(0.25)
    for g in groups:
        try:
            os.killpg(g, _sig.SIGKILL)
        except OSError:
            pass
    return list(pids)


def _pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        # On Windows signal 0 is CTRL_C_EVENT, not a harmless existence probe.
        # Sending it to the lock owner interrupts the very gate run whose
        # liveness we are checking. Use the platform process snapshot instead.
        from . import procs
        try:
            return pid in set(procs.all_pids())
        except (OSError, subprocess.SubprocessError, ValueError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_start(pid, refresh=False):
    """Stable process-start identity, optionally forcing a fresh backend scan."""
    from . import procs
    attempts = 3 if refresh else 1
    for attempt in range(attempts):
        try:
            if refresh:
                procs.refresh()
            start = (procs.info(int(pid)) or {}).get("start")
        except (OSError, TypeError, ValueError):
            start = None
        if start not in (None, 0, ""):
            return start
        if refresh and attempt + 1 < attempts:
            time.sleep(0.02)
    return None


PROJECTION_CHECKS = ("burn_rate",)


def notice_evidence(check, samples, source=None):
    """What a notice was raised on: the check, and each sample's value, source and time (#37).

    The owner received a quota-looking alert for a limit that did not exist and had no
    way to ask why. A projection is only as good as the readings it projected from, so
    one cannot be recorded without them.
    """
    rows = [{"value": sample.get("value"), "source": sample.get("source") or source,
             "at": sample.get("at")} for sample in samples or []]
    if check in PROJECTION_CHECKS and not rows:
        raise ValueError(f"a {check} notice needs the samples it projected from")
    return {"check": check, "samples": rows}


def record_notice(root, title, msg, sent, key=None, evidence=None):
    """Every notification we raise, kept where the architect can read it.

    A desktop notification is fire-and-forget: it reaches the human and vanishes,
    so the one participant who could act on a pattern of alerts — the architect
    reading the panel — is the only one who never sees them. Recording them puts
    both sides on the same evidence.

    `sent=False` rows matter as much as sent ones: they are the alerts a human
    would have received without the rate limit, and a long run of them is itself
    the signal that something has been wrong for a while.
    """
    d = os.path.join(root, ".ao", "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "notices.jsonl"), "a", encoding=UTF8) as fh:
            row = {"id": f"N-{int(time.time() * 1000)}", "at": int(time.time()), "title": title,
                   "msg": msg, "sent": bool(sent), "key": key or title}
            if evidence:
                row["evidence"] = evidence
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def evidence_lines(evidence, now=None):
    """A notice's evidence as lines a person reads: the check, then each sample and its age."""
    if not evidence:
        return ["no evidence recorded (raised before notices kept it, or by a check that has none)"]
    now = now or time.time()
    lines = [f"check: {evidence.get('check')}"]
    for sample in evidence.get("samples") or []:
        at = sample.get("at")
        age = f"{int((now - at) / 60)}m ago" if isinstance(at, (int, float)) else "time unknown"
        lines.append(f"  {sample.get('value')}  from {sample.get('source') or '?'}, {age}")
    return lines


def notices(root, limit=10, include_suppressed=False):
    """Recent notifications, newest first."""
    p = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("sent") or include_suppressed:
            out.append(rec)
    return list(reversed(out))[:limit]


def notice_recently_sent(root, key, window):
    """Was this same alert already delivered inside the window?"""
    p = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    if not os.path.exists(p):
        return False
    cutoff = time.time() - window
    try:
        with open(p, errors="replace", encoding=UTF8) as fh:
            fh.seek(max(0, os.path.getsize(p) - 100_000))
            lines = fh.read().split("\n")
    except OSError:
        return False
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("at", 0) < cutoff:
            return False
        if rec.get("key") == key and rec.get("sent"):
            return True
    return False


def _ledger_time(value):
    """Epoch seconds from a ledger `at`: verifications write ISO text, the rest epochs."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _stall_reason(root, cfg, candidate, waiting):
    """Why the newest staged candidate has not landed, from what was recorded."""
    try:
        for row in reversed(authority_rows(root)):
            if isinstance(row, dict) and row.get("granted") is False \
                    and (row.get("candidate") or {}).get("digest") == candidate.get("digest"):
                return "commit refused: " + "; ".join(str(r) for r in (row.get("reasons") or [])[:2])
    except Exception:
        pass
    reviews_dir = cfg.get("reviews") or "semantic-review"
    for name, verdict in reviews(root, reviews_dir, limit=20):
        try:
            with open(os.path.join(root, reviews_dir, name), encoding=UTF8, errors="replace") as fh:
                head = fh.read(4000)
        except OSError:
            continue
        if candidate.get("digest", "-") in head:
            if "APPROVED" in (verdict or "").upper():
                return f"review approved ({name}) but nothing committed"
            return f"review {verdict} ({name})"
    if waiting:
        oldest = min(waiting, key=lambda d: d.get("asked_at") or 0)
        return f"waiting on decision {oldest.get('id')}: {str(oldest.get('question') or '')[:80]}"
    return "verified, then neither committed, refused nor reviewed"


def throughput(root, cfg, hours=24.0, now=None):
    """Candidates staged and landed, decisions asked and waiting, and what that makes the implementer (#91).

    Busy-detection asks whether a process exists, so an implementer that is alive
    and lands nothing reads as busy forever. On 2026-09-14 one ran full work
    cycles for an hour - a candidate reached 255 passed and was staged - and
    produced four blocked reports, three decision requests and one landing while
    every surface read healthy. The ratio that would have shown it, candidates
    staged against candidates landed, was computed nowhere.

    A candidate is staged when `ao verify` recorded it ready, and landed when a
    commit's tree is its index tree. The state is read in order: stalled (the
    newest staged candidate has not landed for the `stall_minutes` setting),
    landing (a candidate landed in the window), staging (staged, none landed yet),
    busy (the transcript moved, nothing staged) and idle (nothing moved). None of
    it reads the mailbox.
    """
    from .storage import read_jsonl
    now = now or time.time()
    cut = now - hours * 3600
    try:
        rows = read_jsonl(os.path.join(root, ".ao", "ledger", "verifications.jsonl"))
    except Exception:
        rows = []
    ready = []
    for row in rows:
        candidate = row.get("candidate") if isinstance(row, dict) else None
        if isinstance(candidate, dict) and row.get("candidate_ready") and candidate.get("index_tree"):
            ready.append((_ledger_time(row.get("at")), candidate))
    trees = {}
    try:
        log = _git_output(root, "log", "-n", "400", "--format=%T %ct").decode(UTF8, "replace")
    except RuntimeError:
        log = ""
    for line in log.split("\n"):
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            trees.setdefault(parts[0], int(parts[1]))
    staged = {candidate["digest"] for at, candidate in ready if at >= cut}
    landed = {candidate["index_tree"] for _, candidate in ready
              if trees.get(candidate["index_tree"], 0) >= cut}
    every = decisions(root)
    asked = [d for d in every if (d.get("asked_at") or 0) >= cut]
    waiting = [d for d in every if d.get("state") == "open"]
    oldest = min((d.get("asked_at") or now for d in waiting), default=None)
    stall = None
    if ready:
        at, newest = max(ready, key=lambda item: item[0])
        minutes = (now - at) / 60
        if newest["index_tree"] not in trees and minutes >= settings.get(cfg, "stall_minutes"):
            stall = {"minutes": int(minutes), "candidate": newest.get("digest"),
                     "paths": list(newest.get("changed_paths") or []),
                     "reason": _stall_reason(root, cfg, newest, waiting)}
    msgs, _ = session_paths(cfg)
    try:
        moved = bool(msgs) and os.path.getmtime(msgs) >= cut
    except OSError:
        moved = False
    state = ("stalled" if stall else "landing" if landed else "staging" if staged
             else "busy" if moved else "idle")
    return {"hours": hours, "staged": len(staged), "landed": len(landed),
            "decisions_asked": len(asked), "decisions_open": len(waiting),
            "oldest_open_minutes": int((now - oldest) / 60) if oldest is not None else None,
            "state": state, "stall": stall}


def digest(root, cfg, since_days=1.0):
    """What actually happened in a window, from the ledgers rather than memory.

    Event alerts answer "did something just occur". They cannot answer "is this
    week going well", and asking a human to reconstruct that from thirty
    notifications is asking them to do the tool's job. Everything here is already
    on disk — commits, verifications, authority grants, decisions, notices — so
    the summary is read, never estimated.
    """
    cut = time.time() - since_days * 86400
    out = {"since_days": since_days, "at": int(time.time())}

    # git's approxidate wants "N hours ago"; a bare "24.hours" parses to nothing
    # and silently reports zero commits on a day that landed two.
    # No shell: through one, a bare "|" in --pretty=%h|%ct|%s is a pipe, and on
    # Windows a quoted one is too, so the commit list quietly came back empty (#71).
    log = git_text(root, "log", f"--since={int(since_days * 24)} hours ago", "--pretty=%h|%ct|%s")
    out["commits"] = [dict(zip(("sha", "at", "subject"), l.split("|", 2)))
                      for l in log.split("\n") if l.count("|") >= 2]
    out["unpushed"] = int(sh("git rev-list --count @{u}..HEAD 2>/dev/null", cwd=root) or 0) \
        if sh("git rev-parse --abbrev-ref @{u} 2>/dev/null", cwd=root) else \
        len([l for l in (sh("git log --branches --not --remotes --pretty=%h", cwd=root) or "").split("\n") if l])

    def _window(rows):
        fresh = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            # The ledgers disagree on the type of `at`: verifications write an
            # ISO string, everything else an epoch. Read both.
            at = r.get("at", 0)
            if isinstance(at, str):
                try:
                    at = datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    at = 0
            if at >= cut:
                fresh.append(r)
        return fresh

    def _jsonl(rel):
        p = os.path.join(root, rel)
        rows = []
        if os.path.exists(p):
            for line in open(p, errors="replace", encoding=UTF8):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return _window(rows)

    ver = _jsonl(".ao/ledger/verifications.jsonl")
    out["verifications"] = {"total": len(ver),
                            "passed": sum(1 for v in ver if v.get("passed")),
                            "failed": sum(1 for v in ver if not v.get("passed"))}
    try:
        auth = _window(authority_rows(root))
    except Exception as exc:
        # This view cannot authorize, but it must not summarize manipulated
        # authority as trustworthy counts. Preserve the rest of the digest and
        # make the integrity failure explicit instead of skipping bad rows.
        auth = []
        out["authority"] = {
            "granted": 0, "refused": 0,
            "integrity": "broken", "error": str(exc),
        }
    else:
        out["authority"] = {
            "granted": sum(1 for a in auth if a.get("granted")),
            "refused": sum(1 for a in auth if not a.get("granted")),
            "integrity": "valid",
        }
    # Why authority was withheld is the actionable half — a refusal repeated all
    # week is a process problem, not an incident.
    reasons = {}
    for a in auth:
        for r in a.get("reasons") or []:
            key = re.sub(r"[0-9a-f]{7,}|V-\d+|D-\d+|\d{4}-\d\d-\d\d\S*", "…", r)[:70]
            reasons[key] = reasons.get(key, 0) + 1
    out["refusal_reasons"] = sorted(reasons.items(), key=lambda x: -x[1])[:5]

    notices = _jsonl(".ao/ledger/notices.jsonl")
    out["alerts"] = {"sent": sum(1 for n in notices if n.get("sent")),
                     "held": sum(1 for n in notices if not n.get("sent"))}

    decs = [d for d in decisions(root) if d.get("asked_at", 0) >= cut]
    answered = [d for d in decs if d.get("state") == "answered"]
    out["decisions"] = {
        "asked": len(decs), "answered": len(answered),
        "open": len([d for d in decisions(root, "open")]),
        "median_minutes": (sorted((d["answered_at"] - d["asked_at"]) // 60
                                  for d in answered)[len(answered) // 2]
                           if answered else None)}

    b = board(root)
    out["board"] = {k: len(v) for k, v in b.items() if v}
    out["blocked"] = [{"id": i["id"], "title": i["title"],
                       "needs": i["notes"].get("needs", "")} for i in b["blocked"]]

    revs = reviews(root, cfg.get("reviews", "semantic-review"), limit=40)
    fresh = []
    for f, v in revs:
        try:
            if os.path.getmtime(os.path.join(root, cfg.get("reviews", "semantic-review"), f)) >= cut:
                fresh.append(v)
        except OSError:
            pass
    # Only a review that produced a verdict is a review (audit); the rest are counted apart.
    out["reviews"] = {"total": sum(1 for v in fresh if v in ("APPROVED", "NEEDS_CHANGES")),
                      "approved": sum(1 for v in fresh if v == "APPROVED"),
                      "changes": sum(1 for v in fresh if v == "NEEDS_CHANGES"),
                      "not_reviewed": sum(1 for v in fresh if v not in ("APPROVED", "NEEDS_CHANGES"))}

    acct = kiro_account_usage()
    if acct and not acct.get("error"):
        out["credits"] = {"used": acct["used"], "limit": acct["limit"],
                          "remaining": acct["limit"] - acct["used"]}
    local = credit_usage()
    days = sorted(local.get("days", {}).items())
    out["credit_days"] = [(d, v) for d, v in days
                          if d >= time.strftime("%Y-%m-%d", time.localtime(cut))]
    return out


AGENT_LEDGERS = ("authority.jsonl", "decisions.jsonl", "fanouts.jsonl", "verifications.jsonl",
                 "waivers.jsonl")


def _product_changes(root, cfg):
    """(porcelain lines, newest mtime) for uncommitted paths outside coordination state."""
    lines = product_dirty(root, cfg)
    churn = 0
    for line in lines:
        name = line[3:].split(" -> ")[-1].strip().strip('"')
        try:
            churn = max(churn, int(os.path.getmtime(os.path.join(root, name))))
        except OSError:
            pass
    return lines, churn


def work_fingerprint(root, cfg=None):
    """Everything that moves when work is happening, in one short string.

    A commit is one shape of progress, not the only one. A slice whose review
    found real defects withholds its commit *because it is behaving correctly*,
    and a HEAD-only progress check cannot tell that apart from an agent that
    died — so the backoff exhausted itself on a live slice and stood down for two
    hours. Count the product tree and its content, the reviews and the decisions
    too: if any of them moved, something is being done.

    Only what an agent produces counts. The watchdog appends to its own ledgers
    every cycle - progress, notices, credits, the mail ledger - and while those
    counted, the fingerprint changed on every cycle and a nudge's backoff reset
    before it could grow: between 07:00 and 12:00 on 2026-09-15 the watchdog
    nudged 29 times and never once backed off (#96).
    """
    cfg = cfg if cfg is not None else load_config(root)
    lines, churn = _product_changes(root, cfg)
    parts = [sh("git rev-parse --short HEAD", cwd=root) or "", "\n".join(lines), str(churn)]
    # A review that did not take place is a file, not progress: an unavailable
    # reviewer written every turn reset the nudge backoff every turn (audit).
    not_reviews = set()
    try:
        from .storage import read_chained_jsonl
        not_reviews = {row.get("artefact") for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
                       if isinstance(row, dict) and row.get("verdict") not in ("APPROVED", "NEEDS_CHANGES")}
    except Exception:
        pass
    for sub in (cfg.get("reviews") or "semantic-review", DECISION_DIR):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            try:
                parts.append("|".join(sorted(
                    f"{f}:{int(os.path.getmtime(os.path.join(d, f)))}"
                    for f in os.listdir(d) if f not in not_reviews)))
            except OSError:
                pass
    ledger = os.path.join(root, ".ao", "ledger")
    for name in AGENT_LEDGERS:
        try:
            parts.append(f"{name}:{int(os.path.getmtime(os.path.join(ledger, name)))}")
        except OSError:
            pass
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def nudge_inputs(root, cfg):
    """What an idle implementer could act on, in one short string (#96).

    work_fingerprint measures what an agent produces; this measures what it is
    given: the board, the backlog, the decisions and mail addressed to it. An
    implementer that answered a nudge by changing nothing is not asked again
    until one of these, or its work, moves.
    """
    parts = []
    for rel in (".ao/board.md", ".ao/backlog.md"):
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                parts.append(hashlib.sha256(fh.read()).hexdigest())
        except OSError:
            parts.append("-")
    d = os.path.join(root, DECISION_DIR)
    try:
        parts.append("|".join(sorted(f"{f}:{int(os.path.getmtime(os.path.join(d, f)))}"
                                     for f in os.listdir(d))))
    except OSError:
        parts.append("-")
    parts.append("|".join(sorted(implementer_inbox(root, cfg))))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _coordination_dirs(cfg):
    """Repository-relative directories generated or consumed by orchestration.

    These files are evidence and coordination state, not the product tree being
    measured. Keep one definition for review scope, dirty-product detection and
    commit authority so one AO command cannot invalidate another command's
    evidence merely by recording its result.
    """
    defaults = globals().get(
        "COORDINATION_DIRS",
        (".ao/", "agent-mail/", ".kiro/", ".claude/", ".codex/"),
    )
    values = list(defaults) + [
        (cfg or {}).get("reviews", "semantic-review"),
        (cfg or {}).get("mailbox", "agent-mail"),
    ]
    out = []
    for value in values:
        raw = str(value or "").replace("\\", "/")
        while raw.startswith("./"):
            raw = raw[2:]
        normal = os.path.normpath(raw).replace("\\", "/")
        # A coordination setting must never be able to exclude the repository
        # root, escape it, or hide an absolute product path from authority.
        if not normal or normal in (".", "..") or normal.startswith("../") \
                or os.path.isabs(raw):
            continue
        prefix = normal.rstrip("/") + "/"
        if prefix not in out:
            out.append(prefix)
    return tuple(out)


def _is_coordination_path(path, cfg):
    normal = str(path or "").replace("\\", "/")
    while normal.startswith("./"):
        normal = normal[2:]
    return any(normal == prefix.rstrip("/") or normal.startswith(prefix)
               for prefix in _coordination_dirs(cfg))


# ---- ao's own measurements are not read through a filter (#51) ------------------------

# ELF, Mach-O (fat and thin, either byte order) and PE: a compiled program, not a script.
_NATIVE_MAGIC = (b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xcf\xfa\xed\xfe",
                 b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"MZ")
# What ao treats as evidence; the doctor names them when something could filter them.
MEASUREMENTS = ("candidate size and paths", "git status", "rev-parse", "write-tree", "hash-object",
                "gate output")
_REWRITING_HOOK = re.compile(r"\brtk\b|\bproxy\b|rewrit|compress|condens", re.I)
_GIT_BINARIES = {}


def _native_executable(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    return any(head.startswith(magic) for magic in _NATIVE_MAGIC)


def _find_git_binary():
    explicit = os.environ.get("AO_GIT")
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    names = ("git.exe", "git") if os.name == "nt" else ("git",)
    directories = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and os.path.isabs(d)]
    if os.name == "nt":
        directories.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "cmd"))
    else:
        directories += ["/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin"]
    for directory in directories:
        for name in names:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK) \
                    and _native_executable(os.path.realpath(candidate)):
                return candidate
    return "git"


def git_binary():
    """The git ao measures with (#51).

    A token-saving proxy in an agent's shell reported a 5,844-line staged diff as
    533 lines, and the number nearly sized a decision. ao reads its measurements
    itself, never from an agent's terminal, and it does not take a wrapper for git
    either: AO_GIT when that names an executable, otherwise the first git on PATH,
    then in the system directories, that is a compiled program rather than a
    script in front of one; plain "git" only when there is none.
    """
    key = (os.environ.get("AO_GIT"), os.environ.get("PATH", ""), os.name)
    found = _GIT_BINARIES.get(key)
    if found is None or (found != "git" and not os.path.isfile(found)):
        found = _GIT_BINARIES[key] = _find_git_binary()
    return found


def measured_by():
    """How the measurements in a record were taken: which git, no shell, read by ao (#51)."""
    return {"git": git_binary(), "candidate_via_shell": False, "captured_by": "ao"}


def measurement_filters(root):
    """What could stand between a number and its source, one sentence each (#51).

    A script named git ahead of any compiled git on PATH, AO_GIT naming nothing
    executable, and a Claude Code PreToolUse hook that rewrites the shell commands
    an agent runs. A hook is recognised by its command, since what it does cannot
    be read without running it.
    """
    import shutil
    found = []
    explicit = os.environ.get("AO_GIT")
    if explicit and not (os.path.isfile(explicit) and os.access(explicit, os.X_OK)):
        found.append(f"AO_GIT names {explicit}, which is not an executable file; ao measures with {git_binary()}")
    first = shutil.which("git")
    if first and not _native_executable(os.path.realpath(first)):
        chosen = git_binary()
        found.append(f"git on PATH is {first}, a script in front of git; " + (
            f"ao measures with {chosen}" if chosen != "git" else
            "no compiled git was found, so ao's own measurements go through it too: set AO_GIT"))
    for path in (os.path.join(HOME, ".claude", "settings.json"),
                 os.path.join(root, ".claude", "settings.json"),
                 os.path.join(root, ".claude", "settings.local.json")):
        try:
            with open(path, encoding=UTF8) as fh:
                hooks = json.load(fh).get("hooks")
        except (OSError, ValueError, AttributeError):
            continue
        entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            matcher = str(entry.get("matcher") or "*")
            if matcher != "*" and "bash" not in matcher.lower():
                continue
            for hook in entry.get("hooks") or []:
                command = str(hook.get("command") or "") if isinstance(hook, dict) else ""
                if _REWRITING_HOOK.search(command):
                    found.append(f"{path}: the PreToolUse hook `{command}` can rewrite the shell commands an "
                                 f"agent runs, so what an agent reads of {', '.join(MEASUREMENTS)} may be "
                                 "compressed; take such numbers from ao's records, which do not pass through it")
    return found


def _git_output(root, *args, timeout=60):
    """Run git without a shell and return bytes, failing closed on an unreadable tree."""
    try:
        result = subprocess.run(
            [git_binary(), *args], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot measure git tree: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:240]
        raise RuntimeError(f"cannot measure git tree: {detail or 'git failed'}")
    return result.stdout


def _digest_field(digest, label, value):
    """Length-frame one digest field so path/content boundaries cannot collide."""
    digest.update(len(label).to_bytes(2, "big"))
    digest.update(label)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def tree_digest(root, cfg=None):
    """A staging-independent fingerprint of tracked and untracked product state.

    HEAD already identifies every unchanged tracked file. We therefore hash the
    current bytes, type and executable bit of each path that differs from HEAD,
    plus every non-ignored untracked path. The representation is identical before
    and after `git add`, including for newly added files.

    AO's own ledgers, reviews, mail and agent coordination directories are
    deliberately excluded. Verification and review must be able to persist their
    evidence without immediately making that evidence stale.
    """
    cfg = load_config(root) if cfg is None else cfg
    changed = _git_output(
        root, "diff", "--name-only", "-z", "--no-renames", "HEAD", "--"
    ).split(b"\0")
    untracked = _git_output(
        root, "ls-files", "--others", "--exclude-standard", "-z", "--"
    ).split(b"\0")
    paths = {
        os.fsdecode(raw)
        for raw in changed + untracked
        if raw and not _is_coordination_path(os.fsdecode(raw), cfg)
    }

    digest = hashlib.sha256()
    _digest_field(digest, b"format", b"ao-product-tree-v2")
    _digest_field(digest, b"head", _git_output(root, "rev-parse", "HEAD").strip())

    for rel in sorted(paths, key=os.fsencode):
        full = os.path.join(root, rel)
        _digest_field(digest, b"path", os.fsencode(rel))
        try:
            st = os.lstat(full)
        except OSError:
            _digest_field(digest, b"entry", b"missing")
            continue

        if os.path.islink(full):
            _digest_field(digest, b"entry", b"symlink")
            _digest_field(digest, b"target", os.fsencode(os.readlink(full)))
            continue
        if os.path.isfile(full):
            content = hashlib.sha256()
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    content.update(chunk)
            _digest_field(digest, b"entry", b"file")
            _digest_field(digest, b"executable", b"1" if st.st_mode & 0o111 else b"0")
            _digest_field(digest, b"content", content.digest())
            continue
        if os.path.isdir(full):
            # A directory here is normally a changed gitlink. Recurse so a dirty
            # submodule is measured by content rather than by index state.
            try:
                sub = tree_digest(full, {})
            except RuntimeError:
                sub = "unreadable-directory"
            _digest_field(digest, b"entry", b"directory")
            _digest_field(digest, b"content", sub.encode(UTF8, "surrogateescape"))
            continue
        _digest_field(digest, b"entry", f"special:{st.st_mode}".encode())

    return "sha256:" + digest.hexdigest()


CANDIDATE_FORMAT = "ao-index-candidate-v1"
REVIEW_EVIDENCE_PREFIX = "<!-- ao-evidence: "


def index_candidate(root):
    """Measure the exact tree Git would commit from the active index.

    ``git write-tree`` honors ``GIT_INDEX_FILE``, so this also measures the
    temporary index exposed by ``git commit -a`` and path-limited commits. The
    resulting tree object is immutable even if the live index changes later.
    """
    head = _git_output(root, "rev-parse", "HEAD").strip()
    index_tree = _git_output(root, "write-tree").strip()
    if not head or not index_tree:
        raise RuntimeError("cannot measure index candidate: missing object id")
    head_s = head.decode("ascii", "strict")
    tree_s = index_tree.decode("ascii", "strict")
    status = _git_output(
        root, "diff-tree", "-r", "--no-commit-id", "--name-status", "-z",
        "--no-renames", head_s, tree_s, "--",
    )
    names = _git_output(
        root, "diff-tree", "-r", "--no-commit-id", "--name-only", "-z",
        "--no-renames", head_s, tree_s, "--",
    )
    changed_paths = sorted(
        {os.fsdecode(raw) for raw in names.split(b"\0") if raw}, key=os.fsencode
    )
    digest = hashlib.sha256()
    _digest_field(digest, b"format", CANDIDATE_FORMAT.encode("ascii"))
    _digest_field(digest, b"head", head)
    _digest_field(digest, b"index-tree", index_tree)
    _digest_field(digest, b"status", status)
    return {
        "format": CANDIDATE_FORMAT,
        "digest": "sha256:" + digest.hexdigest(),
        "head": head_s,
        "index_tree": tree_s,
        "changed_paths": changed_paths,
        "changed_count": len(changed_paths),
        "status_digest": "sha256:" + hashlib.sha256(status).hexdigest(),
    }


def _normal_scope_paths(paths):
    out = []
    for value in paths or []:
        raw = str(value or "").replace("\\", "/")
        if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
            raise ValueError(f"scope path must be repository-relative: {value}")
        normal = os.path.normpath(raw).replace("\\", "/")
        if normal in ("", ".", "..") or normal.startswith("../"):
            raise ValueError(f"scope path escapes the repository: {value}")
        if normal not in out:
            out.append(normal)
    return sorted(out, key=os.fsencode)


def _path_in_scope(path, scope_paths):
    return any(path == base or path.startswith(base.rstrip("/") + "/")
               for base in scope_paths)


def candidate_scope(candidate, paths=None):
    """Bind a literal review scope to a candidate, rejecting staged extras."""
    scope_paths = _normal_scope_paths(paths)
    kind = "paths" if scope_paths else "full-index"
    outside = [p for p in candidate["changed_paths"]
               if scope_paths and not _path_in_scope(p, scope_paths)]
    digest = hashlib.sha256()
    _digest_field(digest, b"format", b"ao-candidate-scope-v1")
    _digest_field(digest, b"candidate", candidate["digest"].encode("ascii"))
    _digest_field(digest, b"kind", kind.encode("ascii"))
    for path in scope_paths:
        _digest_field(digest, b"path", os.fsencode(path))
    return {
        "kind": kind,
        "paths": scope_paths,
        "digest": "sha256:" + digest.hexdigest(),
        "outside_paths": outside,
    }


def candidate_diff(root, candidate, scope=None):
    """Render the immutable staged candidate, never the mutable worktree."""
    scope = scope or candidate_scope(candidate)
    args = [
        "--literal-pathspecs", "diff-tree", "-p", "--binary", "--full-index",
        "--no-ext-diff", "--no-renames", "--no-commit-id",
        candidate["head"], candidate["index_tree"], "--",
    ]
    args.extend(scope.get("paths") or [])
    return _git_output(root, *args, timeout=60)


# ---- size is a tripwire that asks a question, not a gate that reshapes work (#34) ------

SIZE_KINDS = ("product", "tests", "fixtures", "generated", "deletion")
_GENERATED_PATH = re.compile(r"(^|/)(dist|build|generated|__generated__|vendor|node_modules)/|\.min\.(js|css)$|"
                             r"\.pb\.go$|_pb2\.py$|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|"
                             r"poetry\.lock|Cargo\.lock|go\.sum|uv\.lock|Gemfile\.lock)$")
_FIXTURE_PATH = re.compile(r"(^|/)(fixtures?|testdata|__snapshots__|snapshots)/|\.snap$")


def size_kind(path):
    """What a changed path is, for counting: generated, fixtures, tests or product."""
    path = str(path).replace("\\", "/")
    if _GENERATED_PATH.search(path):
        return "generated"
    if _FIXTURE_PATH.search(path):
        return "fixtures"
    name = path.rsplit("/", 1)[-1]
    if _is_test_path(path) or "__tests__" in path.split("/") or "spec" in path.split("/")[:-1] \
            or re.search(r"_test\.go$|\.(test|spec)\.[cm]?[jt]sx?$", name):
        return "tests"
    return "product"


def candidate_size(root, candidate):
    """The staged candidate's size by kind and by path, never as one number (#34).

    A single total conflated 400 lines of fixtures with 400 lines of concurrency.
    Lines are what git counts between HEAD and the pinned index tree; a file
    whose change only removes lines is a deletion, whatever its path.
    """
    listed = _git_output(root, "diff-tree", "-r", "--numstat", "-z", "--no-renames",
                         candidate["head"], candidate["index_tree"])
    kinds = {kind: {"paths": 0, "added": 0, "deleted": 0} for kind in SIZE_KINDS}
    for entry in listed.split(b"\0"):
        added, _, rest = entry.partition(b"\t")
        deleted, _, path = rest.partition(b"\t")
        if not path:
            continue
        plus = int(added) if added.isdigit() else 0
        minus = int(deleted) if deleted.isdigit() else 0
        kind = "deletion" if plus == 0 and minus > 0 else size_kind(os.fsdecode(path))
        kinds[kind]["paths"] += 1
        kinds[kind]["added"] += plus
        kinds[kind]["deleted"] += minus
    return {"paths": sum(k["paths"] for k in kinds.values()), "kinds": kinds}


def size_text(size):
    """One line naming each kind that changed: paths and lines, product first."""
    parts = [f"{kind} {k['paths']} path(s) +{k['added']}/-{k['deleted']}"
             for kind, k in size["kinds"].items() if k["paths"]]
    return ", ".join(parts) or "nothing changed"


def size_tripwire(cfg, size):
    """Where a candidate's size stands against the guideline, and what that asks for (#34).

    Within it, nothing. Over it, a question: the boundary must say why the slice
    is one invariant that cannot be split without leaving a seam unreviewed, and
    that statement goes to the reviewer. Only far above it, where no review is
    credible at any length, a refusal. Generated files and pure deletions count
    toward no limit.
    """
    lines = size["kinds"]["product"]["added"] + size["kinds"]["product"]["deleted"]
    paths = sum(size["kinds"][kind]["paths"] for kind in ("product", "tests", "fixtures"))
    guide_lines = settings.get(cfg, "size.guideline_product_lines")
    guide_paths = settings.get(cfg, "size.guideline_paths")
    refuse_lines = settings.get(cfg, "size.refuse_product_lines")
    measured = f"{lines} product line(s) across {paths} path(s)"
    guideline = f"the guideline is {guide_lines} product lines and {guide_paths} paths"
    if lines > refuse_lines:
        return {"state": "refuse", "lines": lines, "paths": paths, "overshoot_pct": None,
                "text": f"{measured} is far above what a review can credibly judge ({refuse_lines} product "
                        f"lines, size.refuse_product_lines); {guideline}. Split it."}
    if lines <= guide_lines and paths <= guide_paths:
        return {"state": "within", "lines": lines, "paths": paths, "overshoot_pct": 0, "text": measured}
    overshoot = max(100 * (lines - guide_lines) / guide_lines, 100 * (paths - guide_paths) / guide_paths)
    return {"state": "over", "lines": lines, "paths": paths, "overshoot_pct": round(overshoot),
            "text": f"{measured} is over the guideline ({guideline})"}


def one_slice_statement(item, boundary=None):
    """Why an oversized slice is one invariant, as its boundary states it (#34): a file section or `one slice:`."""
    if boundary and (boundary.get("sections") or {}).get("why one slice", "").strip():
        return boundary["sections"]["why one slice"].strip()
    return ((((item or {}).get("notes") or {}).get("one slice")) or "").strip()


REVIEW_CONTEXT_BUDGET = 100_000


def _is_test_path(path):
    parts = str(path).replace("\\", "/").split("/")
    name = parts[-1]
    return (any(part in ("tests", "test") for part in parts[:-1])
            or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py")


def _review_definitions(root, rev, path, cache):
    """Top-level definitions of one committed Python file: (lines, name -> (start, end))."""
    import ast
    if path not in cache:
        cache[path] = None
        try:
            source = _git_output(root, "show", f"{rev}:{path}", timeout=30).decode(UTF8, "replace")
            tree = ast.parse(source)
        except (RuntimeError, SyntaxError, ValueError):
            return None
        spans = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                spans[node.name] = (min([node.lineno] + [d.lineno for d in node.decorator_list]),
                                    node.end_lineno)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(target, ast.Name):
                        spans[target.id] = (node.lineno, node.end_lineno)
        cache[path] = (source.splitlines(), spans)
    return cache[path]


def review_context(root, paths, read_rev, context_rev, budget=REVIEW_CONTEXT_BUDGET):
    """Committed source a test-only candidate exercises, for the reviewer to read (#97).

    The candidate is what may land; this is what it is judged against. None of it
    enters the diff or the candidate digest, so supplying it widens the authority
    binding by nothing. Context is attached only when every Python file in the
    candidate is a test: a test cannot be judged without the code it runs, while
    a source change carries its own subject.

    The tests are read at ``read_rev`` (the index tree for a staged candidate) and
    the source at ``context_rev``. A definition the tests name through an import -
    an attribute of an imported module, an imported name, or a name handed to
    ``setattr`` as a string - is copied whole, in the order the tests first name
    it, while the budget lasts. What does not fit is named, never cut, and the
    whole text stays within the budget unless the names alone exceed it. Python only.
    """
    import ast
    py = [path for path in paths or [] if path.endswith(".py")]
    if not py or not all(_is_test_path(path) for path in py):
        return None
    try:
        listing = _git_output(root, "ls-tree", "-r", "--name-only", "-z", context_rev, "--")
    except RuntimeError:
        return None
    committed = {os.fsdecode(raw) for raw in listing.split(b"\0") if raw}
    refs = []
    for path in py:
        try:
            tree = ast.parse(_git_output(root, "show", f"{read_rev}:{path}", timeout=30))
        except (RuntimeError, SyntaxError, ValueError):
            continue
        modules, names = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        modules[alias.asname] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    bound = alias.asname or alias.name
                    modules[bound] = f"{node.module}.{alias.name}"
                    names[bound] = (node.module, alias.name)
        for node in ast.walk(tree):
            at = (path, getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in modules):
                refs.append((at, modules[node.value.id], node.attr))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names:
                refs.append((at, *names[node.id]))
            elif (isinstance(node, ast.Call) and len(node.args) >= 2
                  and isinstance(node.args[0], ast.Name) and node.args[0].id in modules
                  and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                refs.append((at, modules[node.args[0].id], node.args[1].value))
    cache, seen, order, blocks = {}, set(), [], {}
    for _, module, name in sorted(refs):
        qualified = f"{module}.{name}"
        if qualified in seen:
            continue
        seen.add(qualified)
        base = module.replace(".", "/")
        path = next((candidate for candidate in (f"src/{base}.py", f"{base}.py",
                                                 f"src/{base}/__init__.py", f"{base}/__init__.py")
                     if candidate in committed), None)
        found = _review_definitions(root, context_rev, path, cache) if path else None
        if not found or name not in found[1]:
            continue
        start, end = found[1][name]
        order.append(qualified)
        blocks[qualified] = (path, f"# {path}:{start}-{end}  {qualified}\n"
                                   + "\n".join(found[0][start - 1:end]) + "\n")
    files = list(dict.fromkeys(blocks[qualified][0] for qualified in order))

    def render(kept):
        omitted = [qualified for qualified in order if qualified not in kept]
        lines = [f"committed at {context_rev[:12]}; read-only; not under review",
                 "files: " + (", ".join(files) or "none resolved from the tests' imports")]
        if omitted:
            shown = []
            for qualified in omitted:
                if sum(len(name) + 2 for name in shown) + len(qualified) > 2_000:
                    break
                shown.append(qualified)
            more = len(omitted) - len(shown)
            lines.append("not inlined for size, read them from the committed tree: "
                         + ", ".join(shown) + (f" and {more} more" if more else ""))
        return {"rev": context_rev, "paths": files, "names": list(kept), "omitted": omitted,
                "text": "\n".join(lines) + "".join("\n\n" + blocks[q][1] for q in kept)}

    kept, spent = [], 0
    for qualified in order:
        size = len(blocks[qualified][1].encode(UTF8)) + 2
        if spent + size <= budget:
            kept.append(qualified)
            spent += size
    result = render(kept)
    while kept and len(result["text"].encode(UTF8)) > budget:
        kept.pop()
        result = render(kept)
    return result


def review_range_context(root, commits, budget=REVIEW_CONTEXT_BUDGET):
    """The same context for a retrospective range, read at the range's end commit."""
    if ".." not in commits:
        return None
    end = commits.split("..", 1)[1].lstrip(".") or "HEAD"
    if end.startswith("-"):
        return None
    try:
        rev = _git_output(root, "rev-parse", "--verify", "--quiet",
                          end + "^{commit}").decode("ascii").strip()
        names = _git_output(root, "diff", "--name-only", "-z", "--no-renames", commits, "--")
    except (RuntimeError, UnicodeError):
        return None
    paths = sorted({os.fsdecode(raw) for raw in names.split(b"\0") if raw}, key=os.fsencode)
    return review_context(root, paths, rev, rev, budget)


def review_context_line(context):
    files = review_header_value(", ".join(context["paths"])) \
        or "none resolved from the tests' imports"
    line = (f"- context: read-only at `{context['rev'][:12]}`: {files}; "
            f"{len(context['names'])} definitions")
    if context["omitted"]:
        line += f", {len(context['omitted'])} not inlined for size"
    return line


# ---- gate coverage: a source tree no gate exercises (#5) ------------------------------

TOOLCHAINS = {
    "python": {"ext": (".py",), "runners": ("pytest", "python", "python3", "ruff", "mypy", "tox", "uv")},
    "node": {"ext": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
             "runners": ("npm", "npx", "pnpm", "yarn", "node", "vitest", "jest", "tsc", "bun", "deno")},
    "dotnet": {"ext": (".cs", ".fs", ".vb"), "runners": ("dotnet", "msbuild")},
    "go": {"ext": (".go",), "runners": ("go",)},
    "rust": {"ext": (".rs",), "runners": ("cargo",)},
    "jvm": {"ext": (".java", ".kt", ".scala"), "runners": ("mvn", "gradle", "gradlew", "sbt")},
    "swift": {"ext": (".swift",), "runners": ("swift", "xcodebuild")},
}


def source_trees(root, minimum):
    """{top-level tree: {toolchain: files}} for every tree holding at least `minimum` of one toolchain's files.

    Tracked and untracked-but-not-ignored files, as git lists them; files at the top
    level count as the tree ".".
    """
    try:
        listed = _git_output(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    except RuntimeError:
        return {}
    counts = {}
    for raw in listed.split(b"\0"):
        path = os.fsdecode(raw)
        if not path:
            continue
        tree = path.split("/", 1)[0] if "/" in path else "."
        chain = next((name for name, spec in TOOLCHAINS.items() if path.endswith(spec["ext"])), None)
        if chain:
            counts.setdefault(tree, {}).setdefault(chain, 0)
            counts[tree][chain] += 1
    return {tree: {chain: n for chain, n in chains.items() if n >= minimum}
            for tree, chains in counts.items() if any(n >= minimum for n in chains.values())}


def _gate_covers(gate, tree, chain):
    """Whether one gate exercises a toolchain's files in one tree: it runs that toolchain's
    runner and, when it declares inputs, one of them reaches into the tree."""
    import fnmatch
    run = str((gate or {}).get("run") or "")
    runners = TOOLCHAINS[chain]["runners"]
    if not any(re.search(rf"(?:^|[\s/;&|(]){re.escape(runner)}(?:$|[\s.;&|)])", run) for runner in runners):
        return False
    inputs = (gate or {}).get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return True
    probe = "x" + TOOLCHAINS[chain]["ext"][0] if tree == "." else f"{tree}/x{TOOLCHAINS[chain]['ext'][0]}"
    return any(fnmatch.fnmatchcase(probe, str(glob)) or str(glob).startswith(f"{tree}/") for glob in inputs)


def gate_coverage_gaps(root, spec, minimum, names=None):
    """(tree, toolchain, files) for every tree's toolchain that no gate - or none of `names` - exercises."""
    gates = (spec or {}).get("gates") or {}
    chosen = [gate for name, gate in gates.items() if names is None or name in names]
    gaps = []
    for tree, chains in sorted(source_trees(root, minimum).items()):
        for chain, files in sorted(chains.items()):
            if not any(_gate_covers(gate, tree, chain) for gate in chosen):
                gaps.append((tree, chain, files))
    return gaps


def gate_inputs(root):
    """The paths the declared gates read, as globs, or None for the whole tree (#16).

    A gate may name its `inputs`. Only when every gate in `.ao/gates.json` does can
    a dirty path outside all of them be known not to reach a gate; one gate without
    inputs reads the whole tree, and so does a file that cannot be read.
    """
    try:
        with open(os.path.join(root, ".ao", "gates.json"), encoding=UTF8) as fh:
            spec = json.load(fh)
    except (OSError, ValueError):
        return None
    gates = spec.get("gates") if isinstance(spec, dict) else None
    if not isinstance(gates, dict) or not gates:
        return None
    globs = set()
    for gate in gates.values():
        declared = gate.get("inputs") if isinstance(gate, dict) else None
        if not isinstance(declared, list) or not declared \
                or not all(isinstance(item, str) and item.strip() for item in declared):
            return None
        globs.update(item.strip() for item in declared)
    return sorted(globs)


def candidate_worktree_issues(root, cfg, candidate=None, inputs="declared"):
    """State that would make gates execute bytes other than the staged candidate.

    Only a dirty path that is part of the candidate or that a gate reads refuses (#16).
    On 2026-09-06 seven untracked files outside the slice withheld commit authority
    until a person moved them. A path no gate reads is listed as `worktree_noise`,
    never fatal; while any gate declares no inputs, every dirty path counts.
    """
    candidate = candidate or index_candidate(root)
    if inputs == "declared":
        inputs = gate_inputs(root)
    unstaged = {
        os.fsdecode(raw)
        for raw in _git_output(
            root, "diff", "--name-only", "-z", "--no-renames", "--"
        ).split(b"\0")
        if raw and not _is_coordination_path(os.fsdecode(raw), cfg)
    }
    untracked = {
        os.fsdecode(raw)
        for raw in _git_output(
            root, "ls-files", "--others", "--exclude-standard", "-z", "--"
        ).split(b"\0")
        if raw and not _is_coordination_path(os.fsdecode(raw), cfg)
    }
    coordination = {
        path for path in candidate["changed_paths"] if _is_coordination_path(path, cfg)
    }
    if inputs is None:
        noise = set()
    else:
        import fnmatch
        mine = set(candidate["changed_paths"])
        noise = {path for path in unstaged | untracked
                 if path not in mine and not any(fnmatch.fnmatchcase(path, glob) for glob in inputs)}
    return {
        "unstaged": sorted(unstaged - noise, key=os.fsencode),
        "untracked": sorted(untracked - noise, key=os.fsencode),
        "staged_coordination": sorted(coordination, key=os.fsencode),
        "worktree_noise": sorted(noise, key=os.fsencode),
    }


def candidate_issue_messages(issues):
    labels = (
        ("unstaged", "unstaged product changes differ from the index"),
        ("untracked", "untracked product files are outside the index"),
        ("staged_coordination", "coordination paths must not share a product commit"),
    )
    return [f"{label}: {', '.join(issues[key])}" for key, label in labels
            if issues.get(key)]


def review_evidence(body):
    """Structured evidence embedded in a human-readable review artifact."""
    for line in str(body or "").splitlines():
        if line.startswith(REVIEW_EVIDENCE_PREFIX) and line.endswith(" -->"):
            try:
                value = json.loads(line[len(REVIEW_EVIDENCE_PREFIX):-4])
            except (TypeError, ValueError):
                return None
            return value if isinstance(value, dict) else None
    return None


def review_evidence_line(value):
    return REVIEW_EVIDENCE_PREFIX + json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ) + " -->"


def review_header_value(value):
    """One header value on one line, whatever the implementer or the config supplied (#55).

    A review artefact's header echoes the boundary, the commit range, labels and
    file names. A value holding a line break, or any other character
    ``str.splitlines`` breaks on (``\\x1c``, ``\\x85``, ``\\u2028`` ...), would start a
    line of its own, and a line reading ``VERDICT: APPROVED`` there is a verdict to
    every reader. Such a value is written as a JSON string; a printable one as it is.
    """
    text = str(value)
    return text if text.isprintable() else json.dumps(text, ensure_ascii=True)


REVIEWER_OUTPUT_HEADING = "## Reviewer output, verbatim"
REVIEWER_OUTPUT_INDENT = "    "


def review_verbatim_lines(text):
    """The reviewer's own output for the artefact, below ao's adjudication (#54).

    Indented, so none of its lines starts at the margin, where the verdict and the
    evidence line are read. It is split with the ``splitlines`` those readers use,
    so a separator inside a line cannot lift the rest of it back to the margin.
    """
    return ["", REVIEWER_OUTPUT_HEADING, ""] + [
        REVIEWER_OUTPUT_INDENT + line if line else ""
        for line in str(text or "").splitlines()
    ]


def candidate_review_integrity(root, candidate, evidence):
    """Return the canonical reviewed scope and any exact-candidate refusals."""
    reasons = []
    if not isinstance(evidence, dict):
        return None, ["review evidence is not a structured object"]
    if evidence.get("candidate") != candidate:
        reasons.append(
            "review evidence candidate does not exactly match the current index candidate"
        )

    recorded_scope = evidence.get("scope")
    if not isinstance(recorded_scope, dict):
        reasons.append("review evidence has no structured candidate scope")
        return None, reasons
    paths = recorded_scope.get("paths")
    if not isinstance(paths, list):
        reasons.append("review evidence candidate scope paths must be a list")
        return None, reasons
    try:
        scope = candidate_scope(candidate, paths)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        reasons.append(f"review evidence has an invalid candidate scope: {exc}")
        return None, reasons

    if scope["outside_paths"]:
        reasons.append(
            "review evidence scope excludes staged paths: "
            + ", ".join(scope["outside_paths"])
        )
    if recorded_scope != scope:
        reasons.append(
            "review evidence scope does not exactly match the current candidate"
        )

    try:
        diff_digest = "sha256:" + hashlib.sha256(
            candidate_diff(root, candidate, scope)
        ).hexdigest()
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, UnicodeError) as exc:
        reasons.append(f"review evidence candidate diff cannot be reproduced: {exc}")
    else:
        if evidence.get("diff_digest") != diff_digest:
            reasons.append(
                "review evidence diff digest does not match the exact candidate diff: "
                f"expected {diff_digest}, got {evidence.get('diff_digest') or '<missing>'}"
            )
    return scope, reasons


REVIEW_CHAIN = "ao-review-row-v1"


def review_requests_dir(root):
    return os.path.join(root, ".ao", "review-requests")


STANDIN_LIMITS = (
    "ao cannot verify which model wrote this answer; it records the model the person declared",
    "a same-user actor could have written the answer; the nonce binds it to one candidate, nothing more",
)


def write_review_request(root, candidate, scope, diff_digest, boundary, slice_id, paths, prompt):
    """A review request a person can carry to a session ao cannot reach (#75).

    Written when no reviewer could be reached for a staged candidate: the exact
    prompt the reviewer would have received, and a nonce the answer must lead with.
    The metadata beside it binds the request to that candidate.
    """
    import secrets
    from .storage import replace_file_durably
    nonce = secrets.token_hex(16)
    directory = review_requests_dir(root)
    meta = {"nonce": nonce, "at": int(time.time()), "candidate": candidate["digest"],
            "index_tree": candidate["index_tree"], "scope": scope, "diff_digest": diff_digest,
            "boundary": boundary, "slice": slice_id, "paths": paths, "collected": None}
    path = os.path.join(directory, f"{nonce}.md")
    replace_file_durably(os.path.join(directory, f"{nonce}.json"),
                         json.dumps(meta, indent=1, sort_keys=True).encode(UTF8))
    text = (f"# ao review request {nonce}\n\n"
            "No reviewer could be reached for the staged candidate below. Paste everything\n"
            "under the line into a session running a different model family from the\n"
            "implementer's, save its whole answer to a file, and then a person runs:\n\n"
            f"    ao collect-review {nonce} --response <file> --model <the model that answered> --by <your name>\n\n"
            f"The request binds to candidate `{candidate['digest']}`; if the staged bytes change,\n"
            "it no longer applies.\n\n---\n\n"
            f"Cevabının İLK satırı tam olarak şu olsun: NONCE: {nonce}\n\n{prompt}\n")
    replace_file_durably(path, text.encode(UTF8))
    return dict(meta, path=path)


def review_request(root, nonce):
    """A request's metadata by its nonce, or None."""
    if not re.fullmatch(r"[0-9a-f]{32}", str(nonce or "")):
        return None
    try:
        with open(os.path.join(review_requests_dir(root), f"{nonce}.json"), encoding=UTF8) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("nonce") == nonce else None


def mark_review_request_collected(root, nonce, artefact, by):
    """A request answers once: record which artefact its answer became and who carried it."""
    from .storage import replace_file_durably
    meta = review_request(root, nonce)
    if meta is None:
        return None
    meta["collected"] = {"at": int(time.time()), "artefact": artefact, "by": by}
    replace_file_durably(os.path.join(review_requests_dir(root), f"{nonce}.json"),
                         json.dumps(meta, indent=1, sort_keys=True).encode(UTF8))
    return meta


def review_ledger_path(root):
    return os.path.join(root, ".ao", "ledger", "reviews.jsonl")


def review_artefact_name(root, reviews_dir, head):
    """A review artefact name no earlier review has, so no record points at a rewritten file."""
    stem = f"{datetime.now():%Y-%m-%d-%H%M%S}-{head}"
    name, n = f"{stem}.md", 2
    while os.path.lexists(os.path.join(root, reviews_dir, name)):
        name, n = f"{stem}-{n}.md", n + 1
    return name


def record_review(root, name, data, evidence, verdict, reviewer=None, fallback=False):
    """Append one review to the chained review ledger: which file, which bytes, what it decided (#63)."""
    from .storage import append_chained_jsonl
    evidence = evidence if isinstance(evidence, dict) else {}
    candidate = evidence.get("candidate") if isinstance(evidence.get("candidate"), dict) else {}
    row = {
        "at": int(time.time()),
        "artefact": name,
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "kind": evidence.get("kind"),
        "candidate": candidate.get("digest"),
        "verdict": verdict,
        "authorizable": evidence.get("authorizable") is True,
        "fallback": bool(fallback),
        "reviewer": reviewer,
        "slice": evidence.get("slice"),
    }
    if row["kind"] == "commit-range":
        row["commits"] = evidence.get("commits")
    return append_chained_jsonl(review_ledger_path(root), row, REVIEW_CHAIN)


def write_review_artefact(root, reviews_dir, name, text, *, evidence, verdict,
                          reviewer=None, fallback=False):
    """Record a review, then write exactly the recorded bytes, whole (#63, #65).

    The row comes first. A write that dies after it leaves a newest review that
    cannot be read, and that refuses; written first, a file whose row was never
    appended left the older recorded approval of the candidate deciding (audit).
    """
    from .storage import replace_file_durably
    directory = os.path.join(root, reviews_dir)
    os.makedirs(directory, exist_ok=True)
    data = scan_evidence(text)[0].encode(UTF8)          # scanned before it is recorded or written (#48)
    record_review(root, name, data, evidence, verdict, reviewer=reviewer, fallback=fallback)
    replace_file_durably(os.path.join(directory, name), data)


# ---- review artefacts: kept while anything rests on them, pruned by age (#38) ----------

# Files whose text names the review artefacts they rest on, and what each one is.
REVIEW_REFERENCE_FILES = (
    ("grant", (".ao", "ledger", "authority.jsonl")),
    ("verification", (".ao", "ledger", "verifications.jsonl")),
    ("board", (".ao", "board.md")),
    ("waiver", (".ao", "ledger", "waivers.jsonl")),
    ("decision", (".ao", "ledger", "decisions.jsonl")),
)


# ---- a merge is verified on its result, before it is made (#39) ------------------------

MERGE_CHAIN = "ao-merge-check-row-v1"


def merge_checks_path(root):
    return os.path.join(root, ".ao", "ledger", "merges.jsonl")


def merge_checks(root):
    """Recorded runs of merge results, oldest first; raises on a broken chain."""
    from .storage import read_chained_jsonl
    return [row for row in read_chained_jsonl(merge_checks_path(root), MERGE_CHAIN) if isinstance(row, dict)]


def record_merge_check(root, row):
    from .storage import append_chained_jsonl
    return append_chained_jsonl(merge_checks_path(root), scan_record(row), MERGE_CHAIN)


def merge_result_tree(root, into, branch):
    """(tree, conflict): the tree `git merge` would make of two commits, computed without touching a worktree.

    RuntimeError when git cannot compute it at all (merge-tree --write-tree needs git 2.38).
    """
    try:
        result = subprocess.run([git_binary(), "merge-tree", "--write-tree", "--name-only", "--no-messages",
                                 into, branch], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot compute the merge: {exc}") from exc
    lines = result.stdout.decode(UTF8, "replace").splitlines()
    if result.returncode == 0 and lines:
        return lines[0].strip(), None
    if result.returncode == 1 and lines:
        return None, "conflicts in " + ", ".join(line.strip() for line in lines[1:] if line.strip())
    detail = result.stderr.decode(UTF8, "replace").strip()
    raise RuntimeError(f"cannot compute the merge: {detail or f'git merge-tree exit {result.returncode}'}")


def unverified_merges(root, cfg):
    """(merge commit, why) for each recent merge on HEAD's first-parent line no passing run vouches for (#39).

    A run vouches for a merge only when it names both parents and the tree the
    merge made, so a run of an older pair, or of a result that was changed while
    merging, vouches for nothing.
    """
    days = settings.get(cfg, "merge.check_days")
    log = git_text(root, "log", "--merges", "--first-parent", f"--since={days}.days", "--format=%H %T %P", "HEAD")
    runs = {(row.get("into"), row.get("branch"), row.get("tree")): row for row in merge_checks(root)}
    out = []
    for line in log.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        sha, tree, first, second = parts[:4]
        run = runs.get((first, second, tree))
        if run is None:
            out.append((sha, "no run of its merge result was recorded"))
        elif run.get("passed") is not True:
            out.append((sha, f"the recorded run of its merge result, {run.get('id')}, failed"))
    return out


def review_artefact_names(root, reviews_dir):
    """The review artefacts on disk: the files in the reviews directory, dotfiles aside."""
    directory = os.path.join(root, reviews_dir)
    try:
        return sorted(name for name in os.listdir(directory)
                      if not name.startswith(".") and os.path.isfile(os.path.join(directory, name)))
    except FileNotFoundError:
        return []


def tracked_review_names(root, reviews_dir):
    """The review artefacts git tracks, by name; RuntimeError when git cannot say."""
    listed = _git_output(root, "ls-files", "-z", "--", reviews_dir)
    return {os.path.basename(os.fsdecode(path)) for path in listed.split(b"\0") if path}


def review_artefact_references(root, cfg):
    """{artefact: [what rests on it]} for every review artefact ao must keep (#38).

    A grant, a verification, the board, a waiver or a decision that names the
    file; a submitted review's state; a review of a slice that is neither done nor
    rejected, or of the candidate staged now; and a file git tracks, which moving
    would turn into a deletion in the tree. The review ledger is the catalogue of
    every review, not a reason to keep one. Whatever cannot be read raises, so
    nothing is pruned on a guess.
    """
    from .storage import read_chained_jsonl
    names = review_artefact_names(root, cfg["reviews"])
    kept = {}

    def keep(name, why):
        reasons = kept.setdefault(name, [])
        if why not in reasons:
            reasons.append(why)

    texts = []
    for why, parts in REVIEW_REFERENCE_FILES:
        try:
            with open(os.path.join(root, *parts), encoding=UTF8, errors="replace") as fh:
                texts.append((why, fh.read()))
        except FileNotFoundError:
            continue
    states = os.path.join(root, ".ao", "reviews")
    for entry in sorted(os.listdir(states)) if os.path.isdir(states) else []:
        if entry.startswith("R-") and entry.endswith(".json"):
            with open(os.path.join(states, entry), encoding=UTF8, errors="replace") as fh:
                texts.append(("submitted review", fh.read()))
    for why, text in texts:
        for name in names:
            if name in text:
                keep(name, why)
    open_slices = {item["id"] for state, items in board(root).items() if state not in ("done", "rejected")
                   for item in items}
    try:
        staged = index_candidate(root)["digest"]
    except RuntimeError:
        staged = None
    for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN):
        name = row.get("artefact") if isinstance(row, dict) else None
        if name not in names:
            continue
        if row.get("slice") in open_slices:
            keep(name, "open slice")
        if staged and row.get("candidate") == staged:
            keep(name, "staged candidate")
    tracked = tracked_review_names(root, cfg["reviews"])
    for name in names:
        if name in tracked:
            keep(name, "tracked in git")
    return kept


def prune_review_artefacts(root, cfg, days, apply=False, now=None):
    """Move the review artefacts nothing rests on, older than `days`, out of the repository (#38).

    Age is the later of the file's time and its ledger row's, so neither alone
    makes a review old. They move to ~/.ao/archive/<project>/, not away: a review
    is evidence someone may still ask for. Returns what was kept and why, how many
    were recent, what was moved (or would be), its bytes and where it went.
    """
    import shutil
    from .storage import read_chained_jsonl
    now = time.time() if now is None else now
    kept = review_artefact_references(root, cfg)
    written = {}
    for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN):
        if isinstance(row, dict) and row.get("artefact"):
            written[row["artefact"]] = max(written.get(row["artefact"], 0), int(row.get("at") or 0))
    directory = os.path.join(root, cfg["reviews"])
    cutoff = now - days * 86400
    recent, moving, size = 0, [], 0
    for name in review_artefact_names(root, cfg["reviews"]):
        if name in kept:
            continue
        path = os.path.join(directory, name)
        if max(os.path.getmtime(path), written.get(name, 0)) >= cutoff:
            recent += 1
        else:
            moving.append(name)
            size += os.path.getsize(path)
    folder = f"{os.path.basename(os.path.normpath(cfg['reviews']))}-{datetime.fromtimestamp(now):%Y%m%d-%H%M%S}"
    archive = os.path.join(HOME, ".ao", "archive", project_key(root), folder)
    if apply and moving:
        os.makedirs(archive, exist_ok=True)
        for name in moving:
            shutil.move(os.path.join(directory, name), os.path.join(archive, name))
    return {"kept": kept, "recent": recent, "moved": moving, "bytes": size, "archive": archive}


def grant_artefacts_at_risk(root, cfg):
    """(name, "untracked" or "missing") for each review a grant rests on that git does not hold (#38)."""
    granted = sorted({row["review"] for row in authority_rows(root)
                      if isinstance(row, dict) and row.get("granted") is True and row.get("review")})
    if not granted:
        return []
    tracked = tracked_review_names(root, cfg["reviews"])
    directory = os.path.join(root, cfg["reviews"])
    return [(name, "untracked" if os.path.exists(os.path.join(directory, name)) else "missing")
            for name in granted if name not in tracked]


def candidate_review_decision(root, review_dir, candidate_digest):
    """What the newest recorded review of one candidate decides (#63).

    Returns ``{"match": (name, verdict, body, evidence) or None, "problem": text or None}``.
    Reviews are taken in the order ao recorded them in the chained review ledger,
    never by file time, which anyone can touch. A review that did not take place
    (UNAVAILABLE) masks nothing. Otherwise the newest review of the candidate
    decides: a missing, unreadable, emptied or rewritten artefact is a refusal and
    never a step back to an older approval, and a fallback's approval cannot
    supersede a completed rejection of the same candidate.
    """
    from .storage import read_chained_jsonl
    rows = [row for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
            if isinstance(row, dict) and row.get("kind") == "index-candidate"
            and row.get("candidate") == candidate_digest]
    for position in range(len(rows) - 1, -1, -1):
        row = rows[position]
        if row.get("verdict") == "UNAVAILABLE":
            continue
        name = row.get("artefact")
        try:
            with open(os.path.join(root, review_dir, str(name)), "rb") as fh:
                data = fh.read()
        except OSError:
            return {"match": None,
                    "problem": f"the newest review of this candidate, {name}, cannot be read"}
        if "sha256:" + hashlib.sha256(data).hexdigest() != row.get("sha256"):
            return {"match": None,
                    "problem": f"the newest review of this candidate, {name}, is not the file "
                               "ao recorded: emptied, cut short or rewritten"}
        body = data.decode(UTF8, "replace")
        evidence = review_evidence(body)
        verdict = _review_verdict(body)
        if isinstance(evidence, dict) and "verdict" in evidence and evidence["verdict"] != verdict:
            verdict = "INVALID"
        if verdict != "APPROVED" or not isinstance(evidence, dict) \
                or evidence.get("authorizable") is not True:
            return {"match": None, "problem": None}
        rejection = next((earlier for earlier in reversed(rows[:position])
                          if earlier.get("verdict") == "NEEDS_CHANGES"), None)
        if row.get("fallback") and rejection:
            return {"match": None,
                    "problem": f"{name} is a fallback reviewer's approval; it cannot supersede "
                               f"the rejection of this candidate in {rejection.get('artefact')}"}
        return {"match": (name, verdict, body, evidence), "problem": None}
    return {"match": None, "problem": None}


def range_review_verdict(root, commits, since=0):
    """The verdict of a retrospective review of exactly this range, recorded after `since` rows, or None."""
    from .storage import read_chained_jsonl
    rows = read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
    for row in reversed(rows[since:]):
        if isinstance(row, dict) and row.get("kind") == "commit-range" \
                and row.get("commits") == commits:
            return row.get("verdict")
    return None


def review_row_count(root):
    from .storage import read_chained_jsonl
    return len(read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN))


def latest_candidate_review(root, review_dir, candidate_digest, limit=40):
    """Return approval only when the newest recorded review of the candidate approves."""
    return candidate_review_decision(root, review_dir, candidate_digest)["match"]


AUTHORITY_CHAIN = "ao-authority-row-v1"


def authority_rows(root):
    """All committed authority rows after validating the complete hash chain.

    A non-empty legacy ledger without predecessor fields is deliberately
    unreadable. Rewriting old authority in place would turn an append-only audit
    trail into evidence manufactured by the reader.
    """
    from .storage import read_chained_jsonl
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    return read_chained_jsonl(path, AUTHORITY_CHAIN)


def latest_authority_decision(root):
    """Newest real boolean decision, only after the full chain validates."""
    rows = authority_rows(root)
    return next(
        (row for row in reversed(rows)
         if isinstance(row, dict) and type(row.get("granted")) is bool),
        None,
    )


VERIFICATION_CHAIN = "ao-verification-row-v1"


GATE_SUMMARY_TAIL_LINES = 30


def gate_counts(output, summary=None):
    """Pass and fail counts from the summary a test runner ends with, or None (#70).

    Only the last lines are read, and the last summary in them counts: a test name,
    a log line or a fixture the implementer wrote can print "# pass 900" anywhere
    earlier. A gate can name its runner's summary with `summary`, a regex with `pass`
    and `fail` groups. Nothing found is None - unparsed, never a number.
    """
    tail = "\n".join(str(output or "").splitlines()[-GATE_SUMMARY_TAIL_LINES:])
    if summary:
        try:
            found = list(re.finditer(summary, tail, re.M))
        except re.error:
            return None
        if not found:
            return None
        groups = found[-1].groupdict()
        try:
            return int(groups.get("pass") or 0), int(groups.get("fail") or 0)
        except ValueError:
            return None
    # node --test closes with "# pass N" / "# fail N" (TAP) or "ℹ pass N" / "ℹ fail N".
    for mark in ("#", "ℹ"):
        passes = re.findall(rf"^{mark} pass (\d+)[ \t]*$", tail, re.M)
        fails = re.findall(rf"^{mark} fail (\d+)[ \t]*$", tail, re.M)
        if passes and fails:
            return int(passes[-1]), int(fails[-1])
    # pytest closes with "3 failed, 461 passed, 2 skipped in 12.30s".
    closing = re.findall(r"^=*[ \t]*((?:\d+ [a-z]+(?:, )?)+) in [\d.]+s\b", tail, re.M)
    if closing:
        counts = {word: int(n) for n, word in re.findall(r"(\d+) ([a-z]+)", closing[-1])}
        if "passed" in counts or "failed" in counts:
            return (counts.get("passed", 0),
                    counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0))
    return None


def gate_summary_line(output, summary=None):
    """The closing line gate_counts read its numbers from, or None (#6).

    Kept in the verification beside the exit code, so a report can quote what the
    runner said rather than a number the reporter typed.
    """
    tail = str(output or "").splitlines()[-GATE_SUMMARY_TAIL_LINES:]
    if summary:
        try:
            found = [line for line in tail if re.search(summary, line)]
        except re.error:
            return None
        return found[-1].strip()[:200] if found else None
    for line in reversed(tail):
        text = line.strip()
        if re.match(r"^[#ℹ] (pass|fail) \d+$", text) or \
                re.match(r"^=*[ \t]*(?:\d+ [a-z]+(?:, )?)+ in [\d.]+s\b", text):
            return text.strip("= ")[:200]
    return None


def verification_evidence(root):
    """One line naming the newest verification: its id, each gate's exit and closing line (#6)."""
    try:
        record = latest_verification(root)
    except Exception:
        return None
    if not record:
        return None
    gates = [f"{g.get('name')} exit {g.get('exit')}" + (f" ({g['summary']})" if g.get("summary") else "")
             for g in record.get("gates") or [] if isinstance(g, dict)]
    return (f"{record.get('id')} {'passed' if record.get('passed') else 'FAILED'}: "
            + ("; ".join(gates) if gates else "no gates"))


_GREEN_CLAIM = re.compile(r"\b\d+\s+passed\b|\ball\s+(?:tests|gates|checks)\s+(?:pass|passed|green)\b|"
                          r"\bgreen\b|\byeşil\b|\bgeçti\b|\bgeçiyor\b", re.I)
_RED_ADMISSION = re.compile(r"\b[1-9]\d*\s+(?:failed|errors?)\b|\bFAIL(?:ED)?\b|\bkırmızı\b|\bkaldı\b")


def report_inconsistency(root, text):
    """Why a report that claims green contradicts the newest verification, or None (#6).

    A report said "159 passed" while the suite had exited 1. The claim is read from
    the report's words; the fact from the verification ledger. A report that admits
    a failure, or a project with no verification, is not inconsistent.
    """
    body = str(text or "")
    if not _GREEN_CLAIM.search(body) or _RED_ADMISSION.search(body):
        return None
    try:
        record = latest_verification(root)
    except Exception:
        return None
    if not record or record.get("passed") is not False:
        return None
    failed = [f"{g.get('name')} exit {g.get('exit')}" for g in record.get("gates") or []
              if isinstance(g, dict) and not g.get("passed")]
    return (f"the report claims green, but the newest verification {record.get('id')} failed"
            + (f": {', '.join(failed)}" if failed else ""))


def gate_definitions_digest_of(spec, profile):
    """Canonical digest of the gate definitions one profile runs, or None when it has none (#61).

    `.ao/gates.json` is a file the implementer can write. A verification that does
    not name the definitions it ran lets a gate weakened after the run inherit the
    pass measured under the stronger one.
    """
    try:
        ran = [[name, spec["gates"][name]] for name in spec["profiles"][profile]]
        canonical = json.dumps({"profile": profile, "gates": ran}, ensure_ascii=True,
                               sort_keys=True, separators=(",", ":"))
    except (KeyError, TypeError, ValueError):
        return None
    return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def gate_definitions_digest(root, profile):
    """The digest of the definitions in force now, read from `.ao/gates.json`."""
    try:
        with open(os.path.join(root, ".ao", "gates.json"), encoding=UTF8) as fh:
            spec = json.load(fh)
    except (OSError, ValueError):
        return None
    return gate_definitions_digest_of(spec, profile)


def latest_verification(root):
    """The newest complete, chained `ao verify` record, or None.

    The ledger is chained like the authority ledger (#61), so a row appended
    without a valid link makes it unreadable. Rows written before it was chained
    carry no link; they stay readable as history and never count.
    """
    from .storage import CHAIN_PREVIOUS_FIELD, read_chained_jsonl
    rows = read_chained_jsonl(os.path.join(root, ".ao", "ledger", "verifications.jsonl"),
                              VERIFICATION_CHAIN, legacy_prefix=True)
    chained = [row for row in rows if CHAIN_PREVIOUS_FIELD in row]
    return chained[-1] if chained else None


def record_verification(root, record):
    """Durably append one chained verification before reporting its result."""
    from .storage import append_chained_jsonl
    path = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    return append_chained_jsonl(path, scan_record(record), VERIFICATION_CHAIN, legacy_prefix=True)


def _granted_trees(root):
    """The index trees authority was granted for, and the commit the first grant built on."""
    rows = [row for row in authority_rows(root)
            if isinstance(row, dict) and row.get("granted") is True]
    trees = {(row.get("candidate") or {}).get("index_tree") for row in rows}
    trees.discard(None)
    base = next(((row.get("candidate") or {}).get("head") for row in rows
                 if (row.get("candidate") or {}).get("head")), None)
    return trees, base, rows


def landed_commit_problem(root, commit="HEAD"):
    """Why a landed commit is not the tree its grant bound, or None when it is (#64).

    commit-check measures the index when the pre-commit hook runs, and Git writes
    the commit's tree from the index after hooks return, so a process that stages
    a path in between lands it inside an authorised commit. A hook cannot prevent
    that; comparing the tree that landed with the tree that was granted detects it.
    """
    try:
        sha = _git_output(root, "rev-parse", "--verify", commit).decode("ascii").strip()
        tree = _git_output(root, "rev-parse", "--verify", f"{commit}^{{tree}}").decode("ascii").strip()
    except (RuntimeError, UnicodeError) as exc:
        return f"cannot read {commit}: {exc}"
    _, _, rows = _granted_trees(root)
    grant = rows[-1] if rows else None
    granted = ((grant or {}).get("candidate") or {}).get("index_tree")
    if not granted:
        return f"commit {sha[:12]} landed with no grant on record"
    if granted != tree:
        return (f"commit {sha[:12]} landed tree {tree[:12]}, but grant "
                f"{grant.get('token') or '<unnamed>'} bound tree {granted[:12]}")
    return None


def commits_without_grant(root, limit=50):
    """Landed commits, newest first, whose tree no grant bound (#64).

    Only commits on top of the one the first grant was built on are measured;
    history from before the authority ledger has nothing to be compared with.
    Merges are left out: their trees are Git's, not a candidate's.
    """
    trees, base, _ = _granted_trees(root)
    if base is None:
        return []
    try:
        log = _git_output(root, "log", f"--max-count={int(limit)}", "--no-merges",
                          "--format=%H %T", f"{base}..HEAD", "--").decode("ascii")
    except (RuntimeError, UnicodeError):
        return []
    return [sha for sha, tree in (line.split() for line in log.splitlines() if line.strip())
            if tree not in trees]


def record_authority(root, granted, reasons, tree, verification, token=None,
                     review=None, reviewer=None, candidate=None, scope=None,
                     matrix=None, role_bindings=None, implementer_identity=None,
                     reviewer_identity=None, waiver=None):
    """Persist one hash-chained authority decision, raising on any broken prefix."""
    from .storage import append_chained_jsonl
    record = {"at": int(time.time()), "granted": bool(granted),
              "token": token, "reasons": reasons, "tree": tree,
              "verification": verification, "review": review,
              "reviewer": reviewer}
    if matrix is not None:
        record.update({
            "schema": 3,
            "candidate": candidate,
            "scope": scope,
            "matrix": matrix,
            "role_bindings": role_bindings,
            "implementer_identity": implementer_identity,
            "reviewer_identity": reviewer_identity,
        })
    elif candidate is not None:
        record.update({"schema": 2, "candidate": candidate, "scope": scope})
    if waiver is not None:
        # The waiver a grant stood on; it covers no other candidate after this (#67).
        record["waiver"] = waiver
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    # Keep the reviewer's identity here, not only in the review file. A grant is
    # not real until the chain prefix validates and the locked append, file
    # fsync and (on first creation) directory fsync have all completed.
    return append_chained_jsonl(path, record, AUTHORITY_CHAIN)


GATE_LOCK = os.path.join(HOME, ".ao", "gate.lock")


def gate_lock_holder():
    """Which project is running its gates right now, if any."""
    if not os.path.exists(GATE_LOCK):
        return None
    try:
        st = json.load(open(GATE_LOCK, encoding=UTF8))
    except Exception:
        return None
    if not _pid_alive(st.get("pid", -1)):            # stale lock from a killed run
        try:
            os.remove(GATE_LOCK)
        except OSError:
            pass
        return None
    st["minutes"] = int((time.time() - st.get("at", time.time())) / 60)
    return st


def acquire_gate_lock(root, timeout=0):
    """Serialise the expensive work across every project on this machine.

    Roles split who decides; this splits who spends the machine. Each project's
    watchdog is independent, so without a machine-wide lock N projects run N test
    suites at once — and on a shared laptop that is not N times the throughput, it
    is one suite that no longer finishes. This project watched an agent burn five
    review rounds walking a concurrency setting down from 8 to 1 while fighting
    exactly that.

    Advisory and best-effort: a lock nobody can steal becomes a lock that wedges
    the machine, so a holder whose process is gone is cleared on sight.
    """
    os.makedirs(os.path.dirname(GATE_LOCK), exist_ok=True)
    deadline = time.time() + timeout
    while True:
        holder = gate_lock_holder()
        if not holder:
            try:
                fd = os.open(GATE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as fh:
                    json.dump({"root": root, "pid": os.getpid(),
                               "at": int(time.time())}, fh)
                return True
            except FileExistsError:
                pass
        if time.time() >= deadline:
            return False
        time.sleep(2)


def release_gate_lock():
    holder = gate_lock_holder()
    if holder and holder.get("pid") == os.getpid():
        try:
            os.remove(GATE_LOCK)
        except OSError:
            pass


def process_trees(pids, parent=None):
    """Group pids into independent trees — how many *turns*, not how many processes.

    One turn is several processes: a wrapper spawns a runtime which spawns
    children. Counting processes therefore reports four concurrent writers where
    there is one, and an alarm that fires on normal operation is an alarm people
    learn to ignore. Count only the roots: a pid whose parent is not itself in the
    set.
    """
    if not pids:
        return []
    if parent is None:
        parent = {}
        for line in (sh("ps -eo pid,ppid") or "").split("\n")[1:]:
            f = line.split()
            if len(f) >= 2 and f[0].isdigit() and f[1].isdigit():
                parent[int(f[0])] = int(f[1])
    known = set(pids)
    return sorted(p for p in pids if parent.get(p) not in known)


def anomalies(root, cfg, adapter, age, idle_seconds, exclude_pids=()):
    """Conditions a watchdog cannot resolve, reported as facts rather than verdicts.

    A guard chain is good at mechanical questions — is a turn running, is there
    quota, has this nudge already failed. It is bad at everything else, and the
    failures of this project came from letting it try: it decided a provider
    outage was a stuck agent, decided a hung process was a live writer, decided a
    finished slice was work in progress. Each decision was defensible from the one
    signal it had and wrong given the others.

    So it stops deciding. It collects what it can see and hands that to the
    architect, who has the context to weigh it. Crucially the facts carry no
    conclusion — "four processes, transcript moved 12s ago, HEAD unchanged for
    three hours" is useful; "there is a concurrent writer" is the guess that cost
    seven hours the last time something made it.
    """
    out = []
    groups = {}

    # An explicit request outranks every heuristic here. When the implementer
    # writes to the architect it has already decided it is blocked, and waiting
    # for a detector to independently notice is absurd — this project spent half a
    # day doing exactly that while a message saying "decision required" sat
    # unread. Fire on the next cycle, not after a threshold.
    for m in mailbox(root, cfg.get("mailbox", "agent-mail")):
        if not to_architect(m, cfg):
            continue
        # The watchdog must not read its own outbox as an inbox. Its anomaly
        # reports are addressed to the architect ("watchdog-to-fable-…"), so they
        # matched this filter and were re-escalated as fresh "report-waiting"
        # anomalies — each new report's name concatenating the last, a runaway
        # that filled the mailbox with names hundreds of characters long. What
        # needs a decision is what the implementer or a human sent, never what
        # this detector emitted.
        if m.startswith("watchdog-to-") or "-watchdog-to-" in m:
            continue
        # The architect's notes to itself are not the implementer asking (#18).
        if from_architect(m, cfg):
            continue
        try:
            body = open(os.path.join(root, cfg.get("mailbox", "agent-mail"), m),
                        errors="replace", encoding=UTF8).read(4000)
        except OSError:
            continue
        first = next((l for l in body.split("\n") if l.strip().startswith("#")), m)
        # "I am blocked" and "I finished" both want the architect eventually, but
        # only one of them stops work. Treating a completion report as urgent is
        # how an urgent channel becomes background noise.
        # Match structure, not substrings. The implementer's report template ends
        # with a "Blockers:" line on every message, including "Blockers: none", so
        # a bare `"blocker" in body` test classified every routine status report
        # as urgent and re-raised it every ten minutes: twenty-eight false alarms
        # in one hour, produced by a line that said the opposite of what matched.
        low = body.lower()
        asking = any(h in low for h in ("## karar gerekli", "## acil",
                                        "## decision required", "## urgent",
                                        "## blocked"))
        if not asking:
            for line in low.split("\n"):
                t = line.strip().lstrip("-*# ").strip()
                if not t.startswith(("blockers:", "blocker:", "engel:", "engeller:")):
                    continue
                value = t.split(":", 1)[1].strip(" .`")
                # An empty value, or one that opens by saying there are none, is
                # the template reporting health — not a request for anything.
                asking = bool(value) and not value.startswith(
                    ("none", "no ", "yok", "-", "n/a", "hiç"))
                break
        kind = "decision-requested" if asking else "report-waiting"
        contradiction = report_inconsistency(root, body)
        if contradiction:
            out.append({"kind": "inconsistent-report", "key": m,
                        "facts": [f"{m}: {contradiction}"]})
        g = groups.setdefault(kind, {"n": 0, "first": m})
        g["n"] += 1
        g["latest"] = m
        g["title"] = first.lstrip("# ").strip()[:200]
    # A review that came back and nobody took is a slice left unattended (#28, W1).
    waited = settings.get(cfg, "review.unhandled_minutes") * 60
    for state in returned_reviews(root):
        age = time.time() - float(state.get("finished_at") or time.time())
        if age >= waited:
            out.append({"kind": "review-returned", "key": state["id"],
                        "facts": [f"{state['id']} for slice {state.get('slice')} returned "
                                  f"{state.get('verdict') or state.get('state')} {int(age / 60)}m ago and "
                                  "has not been collected"]})
    # A question asked with `ao ask` wants the architect as much as a report does,
    # and may have no mail at all (#20). Each open one is its own anomaly.
    for decision in decisions(root, "open"):
        asked = decision.get("asked_at") or 0
        out.append({"kind": "decision-requested", "key": decision.get("id"),
                    "facts": [f"{decision.get('id')} is open since "
                              f"{time.strftime('%d %b %H:%M', time.localtime(asked)) if asked else '?'}",
                              str(decision.get("question") or "")[:200]]})
    # One anomaly per kind, however many reports carry it. Eighty "queue empty"
    # reports in eleven hours became eighty anomaly files and forty wake attempts;
    # the architect needed one line saying "eighty, since 06:31".
    for kind, g in groups.items():
        since = g["first"][:13] if re.match(r"\d{8}-\d{4}", g["first"]) else g["first"][:20]
        head = f"the implementer wrote {g['latest']}"
        if g["n"] > 1:
            head += f" — {g['n']} report(s) of this kind standing, the first since {since}"
        out.append({"kind": kind, "key": "implementer",
                    "facts": [head, g["title"],
                              "an explicit request — not a symptom needing corroboration"
                              if kind == "decision-requested" else "a report, not a blocker"]})

    # Exclude pids the caller knows are not implementer writers — above all the
    # architect the watchdog itself spawned, which resumes with this repo as its
    # cwd and would otherwise read as a second turn. The watchdog knows its pid;
    # the detector should not have to guess.
    pids = [p for p in agent_pids(root, adapter) if p not in set(exclude_pids)]
    dirty = len([l for l in sh("git status --porcelain", cwd=root).split("\n") if l.strip()])
    head = sh("git rev-parse --short HEAD", cwd=root)

    # What a finished turn left behind is not a turn. Orphans are cleared by the
    # watchdog before it counts; here they are simply not counted.
    trees = process_trees([p for p in pids if p not in set(orphans(root, adapter))])
    if len(trees) > 1 and age < idle_seconds:
        out.append({"kind": "several-turns-active", "roots": trees,
                    "facts": [f"{len(trees)} independent process trees with this repo as "
                              f"cwd, roots {trees} (of {len(pids)} processes)",
                              f"transcript last written {int(age)}s ago",
                              "separate roots mean separate turns, not one turn's children"]})
    spin = spinning(root)
    if spin:
        out.append({"kind": "busy-without-progress",
                    "facts": [f"transcript growing for {spin}m",
                              f"HEAD unchanged at {head}", f"{dirty} files dirty, unchanged"]})
    rn = rounds(root, cfg["reviews"])
    budget = settings.get(cfg, "round_budget")
    if rn > budget:
        revs = reviews(root, cfg["reviews"], limit=3)
        out.append({"kind": "over-round-budget",
                    "facts": [f"round {rn} of {budget} on the current slice"] +
                             [f"{f}: {v}" for f, v in revs]})
    # The same finding returning review after review. A round count cannot see
    # it; this is the actual shape of a slice that is not converging.
    loops = review_loop(root, cfg.get("reviews", "semantic-review"))
    if loops:
        out.append({"kind": "review-loop",
                    "facts": [f"[{l['sev']}] {l['file']} — \"{l['clause']}\" has come back "
                              f"{l['count']} reviews running" for l in loops[:4]] +
                             ["more rounds will not converge this; it needs re-specifying "
                              "or a different actor"]})
    err = last_nudge_error(root)
    if err and time.time() - err.get("at", 0) < 3600:
        out.append({"kind": "restart-failed",
                    "facts": [f"exit {err.get('code')} "
                              f"{int((time.time() - err.get('at', 0)) / 60)}m ago",
                              (err.get("tail") or "")[:300]]})
    return out


def write_report(root, cfg, kind, facts, key=None):
    """A watchdog-to-architect message: observations, no interpretation.

    Deduplicated by what the anomaly is *about*, not by wall-clock time. The name
    once carried a minute stamp, so the exists-guard only caught collisions inside
    the same minute and a standing condition produced a fresh file every cycle —
    twenty-five copies of one unread status report in an hour. Key the filename on
    (kind, source) instead: while an anomaly for that pair sits unprocessed, no
    second one is written. The architect deleting it is what re-arms the report,
    which is correct — a condition that recurs after it was judged is genuinely
    new.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    try:
        os.makedirs(box, exist_ok=True)
        # A stable key from the source the facts name (e.g. "the implementer wrote
        # X.md"), falling back to the kind alone for anomalies with no single
        # source. This is what makes the exists-guard actually guard.
        src = "-" + re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-") if key else ""
        for f in [] if key else facts:
            mrk = re.search(r"wrote\s+(\S+\.md)", f)
            if mrk:
                src = "-" + re.sub(r"[^A-Za-z0-9]+", "-", mrk.group(1)[:-3]).strip("-")
                break
        _, arch = mail_names(cfg)
        name = f"watchdog-to-{arch}-ANOMALY-{kind}{src}.md"
        path = os.path.join(box, name)
        if os.path.exists(path):
            return None
        text = (f"# ANOMALY — {kind}\n\n"
                f"Watchdog observation at {time.strftime('%Y-%m-%d %H:%M:%S')}. "
                f"Facts only; the watchdog draws no conclusion and took no action "
                f"beyond standing down.\n\n"
                + "".join(f"- {f}\n" for f in facts)
                + "\n## Asked of the architect\n\n"
                "Decide whether this needs intervention, and what. If it is normal, "
                "delete this message; if not, act and record what you did.\n")
        return write_mail(root, cfg, name, text, {"kind": "anomaly", "from": "watchdog", "to": arch})
    except OSError:
        return None


def kiro_account_usage(timeout=20):
    """Real credit usage from the provider, not an estimate.

    `GetUsageLimits` on the CodeWhisperer runtime returns exactly what the app's
    dashboard shows: credits used, the plan limit, the reset date, overage
    settings. It authenticates with the OIDC access token the CLI already holds
    after login, read from its local store — the same credential, on the same
    machine, for the same account.

    Two things this is not. It is not the `ksk_` API key: that key is rejected as
    a bearer token here, so it authenticates something else. And it is not
    guesswork from transcripts — that reading exists as an offline fallback and
    undercounts by whatever ran on another machine, which measured about a third.

    Returns None when there is no usable token; the caller falls back rather than
    presenting an error as a balance. The token is used and never stored, logged
    or returned.
    When the CLI cannot be found or run it returns {"error": ...} naming the step,
    so a caller can report a broken check instead of a silent one.
    """
    db = os.path.join(HOME, "Library", "Application Support", "kiro-cli", "data.sqlite3")
    if not os.path.exists(db):
        return None
    raw = sh(f"sqlite3 {json.dumps(db)} "
             "\"SELECT value FROM auth_kv WHERE key='kirocli:odic:token';\"")
    if not raw:
        return None
    try:
        tok = json.loads(raw)
    except Exception:
        return None
    access = tok.get("access_token")
    if not access:
        return None
    if tok.get("expires_at"):
        try:
            exp = tok["expires_at"]
            exp = float(exp) if not isinstance(exp, str) else \
                __import__("datetime").datetime.fromisoformat(
                    exp.replace("Z", "+00:00")).timestamp()
            if exp < time.time():
                return {"expired": True}
        except Exception:
            pass

    # Resolve the CLI through the harness binary search, not the ambient PATH: under
    # launchd that PATH is /usr/bin:/bin:/usr/sbin:/sbin, kiro-cli was never found,
    # and a `2>/dev/null` turned the miss into None, so the watchdog never recorded
    # a sample and the exhaustion alarm could not fire. Say which step failed.
    found = binary_candidates("kiro-cli")
    if not found:
        return {"error": "kiro-cli is not on PATH or in the usual install directories"}
    try:
        prof = subprocess.run([found[0], "whoami"], capture_output=True, text=True, encoding=UTF8,
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{found[0]} whoami could not run ({type(exc).__name__})"}
    arn = next((line.strip() for line in (prof.stdout or "").splitlines()
                if line.strip().startswith("arn:aws:codewhisperer")), "")
    if not arn:
        return {"error": f"{found[0]} whoami returned no profile ARN (exit {prof.returncode})"}

    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        "https://codewhisperer.us-east-1.amazonaws.com/",
        data=json.dumps({"profileArn": arn}).encode(),
        headers={"Content-Type": "application/x-amz-json-1.0",
                 "x-amz-target": "AmazonCodeWhispererService.GetUsageLimits",
                 "Authorization": "Bearer " + access})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception:
        return None

    row = next((b for b in d.get("usageBreakdownList") or []
                if b.get("resourceType") == "CREDIT"), None)
    if not row:
        return {"error": "no CREDIT row in response"}
    sub = d.get("subscriptionInfo") or {}
    over = d.get("overageConfiguration") or {}
    return {
        "used": row.get("currentUsageWithPrecision", row.get("currentUsage")),
        "limit": row.get("usageLimitWithPrecision", row.get("usageLimit")),
        "reset_at": row.get("nextDateReset") or d.get("nextDateReset"),
        "days_until_reset": d.get("daysUntilReset"),
        "plan": sub.get("subscriptionTitle"),
        "overage_status": over.get("overageStatus"),
        "overage_cap": row.get("overageCapWithPrecision", row.get("overageCap")),
        "overage_rate": row.get("overageRate"),
        "overage_now": row.get("currentOveragesWithPrecision", row.get("currentOverages")),
        # Which account the figures belong to, without keeping the profile ARN itself (#36).
        "account": credit_account(arn),
    }


def credit_usage(monthly_budget=None):
    """Credit spend read from local transcripts, by billing month.

    There is no endpoint for this. A Kiro API key authenticates the CLI
    (`KIRO_API_KEY`); it is not a REST credential, and the published docs
    describe no usage or quota route. The dashboard in the app is the authority.

    Locally, each session writes `usage_summary` records carrying
    `{unit: "credit", usage: <float>}`. The values climb and then drop, because a
    record reports the running total *of the turn in progress* and a drop means a
    new turn began. So a turn costs the peak it reached, and a session costs the
    sum of those peaks.

    Two simpler readings are wrong by large factors and both were tried first:
    summing every record counts each turn once per progress update (30x high),
    and taking only the final record counts one turn per session (40x low). The
    peaks reading was confirmed against a known 10,000/month allowance — the
    month of heaviest use came to 10,148, where the others gave 13,168 and 258.
    """
    import glob
    from collections import defaultdict
    months, days, sessions = defaultdict(float), defaultdict(float), []
    for f in glob.glob(os.path.join(HOME, ".kiro", "sessions", "*", "*", "messages.jsonl")):
        peaks, cur, month, turns = 0.0, 0.0, "", 0
        cur_day = ""
        try:
            with open(f, errors="replace", encoding=UTF8) as fh:
                for line in fh:
                    if '"promptTurnSummaries"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    pl = rec.get("payload", rec)
                    if pl.get("type") != "usage_summary":
                        continue
                    v = sum(x.get("usage", 0) for x in (pl.get("promptTurnSummaries") or [])
                            if isinstance(x.get("usage"), (int, float)))
                    ts = (rec.get("timestamp") or "")
                    if v < cur:                  # dropped: the previous turn ended at cur
                        peaks += cur
                        turns += 1
                        # Attribute the turn to the day it ran, not to whenever the
                        # session was last touched. A long session crosses billing
                        # periods, and charging all of it to the final period is how
                        # a month reads as full while the previous one reads as empty.
                        days[cur_day or ts[:10]] += cur
                    cur, cur_day = v, ts[:10]
                    month = ts[:7]
        except OSError:
            continue
        if cur or peaks:
            peaks += cur
            turns += 1
            days[cur_day or month + "-01"] += cur
            months[month] += peaks
            sessions.append({"session": os.path.basename(os.path.dirname(f)),
                             "month": month, "turns": turns, "credits": round(peaks, 2),
                             "mtime": os.path.getmtime(f)})
    sessions.sort(key=lambda r: r["mtime"], reverse=True)
    this_month = time.strftime("%Y-%m")
    # Billing periods are not calendar months — a subscription renews on its own
    # day — so return the daily series and let the caller cut it wherever the
    # user's period actually starts.
    return {"sessions": sessions, "days": {d: round(v, 2) for d, v in sorted(days.items())},
            "months": {m: round(v, 2) for m, v in sorted(months.items())},
            "this_month": round(months.get(this_month, 0.0), 2),
            "budget": monthly_budget,
            "remaining": (round(monthly_budget - months.get(this_month, 0.0), 2)
                          if monthly_budget else None)}


URGENT_MARKERS = ("## ACİL", "## URGENT", "## DUR", "## STOP")


ROLES = ("implementer", "architect")


def invoking_role():
    """The role this ao process runs for, from AO_ROLE, or None for a person or an unknown caller (#29)."""
    role = (os.environ.get("AO_ROLE") or "").strip().lower()
    return role if role in ROLES else None


def returned_reviews(root):
    """Submitted reviews that have ended and that nobody has collected yet (#28)."""
    directory = os.path.join(root, ".ao", "reviews")
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    out = []
    for name in names:
        if not (name.startswith("R-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(directory, name), encoding=UTF8) as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and state.get("state") not in (None, "running") \
                and not state.get("collected_at"):
            out.append(state)
    return out


def urgent_messages(root, cfg, role="implementer"):
    """Unacknowledged messages the implementer must see before it does anything big.

    MCP cannot interrupt. Its tools fire only when the agent chooses to call them,
    so an urgent message sits unread until the agent's next `ao_inbox` — which may
    be a turn away, or never if it is stuck. A2A agents get an inbound push; an
    MCP-only agent does not, and no amount of protocol design changes that.

    What we do control is the boundary the agent crosses on its own: it runs `ao`
    to take the machine lock, to verify, and to ask whether it may commit. Those
    are precisely the moments before something expensive or irreversible, which is
    exactly when an urgent message needs to land. So the CLI carries it.

    Marked messages only. Everything routine waits for `ao_inbox`, or the channel
    becomes noise and gets skimmed — which is how it fails.

    Urgency is resolved for a role, not hardcoded to the implementer (#29): mail
    addressed to the architect's role is the architect's, the rest the
    implementer's, and role None takes both. An implementer report marked urgent
    sat unread for four hours because this skipped everything bound for the architect.
    """
    box = cfg.get("mailbox", "agent-mail")
    out = []
    for m in mailbox(root, box):
        addressed = "architect" if to_architect(m, cfg) else "implementer"
        if role and addressed != role:
            continue
        try:
            body = open(os.path.join(root, box, m), errors="replace", encoding=UTF8).read(8000)
        except OSError:
            continue
        upper = body.upper()
        if any(k.upper() in upper for k in URGENT_MARKERS):
            title = next((l for l in body.split("\n") if l.strip().startswith("# ")), m)
            out.append({"id": m, "title": title.lstrip("# ").strip()[:120], "body": body,
                        "to": addressed})
    return out


def claude_project_dir(cwd):
    """The directory Claude Code keeps a working directory's transcripts in (#71).

    On macOS and Linux the name is the absolute path with "/" and "." made
    dashes. ao built it that way everywhere, and on Windows `C:\\repo` kept its
    drive: os.path.join took it as an absolute path, dropped ~/.claude/projects,
    and the watchdog found no transcript and ended every cycle there. On Windows
    every character outside letters, digits and dashes is made a dash. A
    directory that already exists under either name is used as found.
    """
    base = os.path.join(HOME, ".claude", "projects")
    cwd = str(cwd or "")
    posix = cwd.replace("/", "-").replace(".", "-")
    portable = re.sub(r"[^A-Za-z0-9-]", "-", cwd)
    for name in (posix, portable):
        if name and "/" not in name and "\\" not in name and ":" not in name \
                and os.path.isdir(os.path.join(base, name)):
            return os.path.join(base, name)
    return os.path.join(base, portable if os.name == "nt" else posix)


def unplaced_agent_pids(root, adapter):
    """Agent processes that may be working in this tree but cannot be placed (#71).

    Windows exposes no process working directory, and no shipped adapter's command
    line names the repository, so there a turn in this tree was no writer at all:
    `ao hold` stopped nothing and the watchdog started a second turn. The guards
    that must not miss a writer count these and say why. Where a process's
    directory can be read this is empty.
    """
    if os.name != "nt":
        return []
    from . import procs
    names = set()
    for key in ("send", "resume"):
        argv = (adapter.get(key) or {}).get("argv") or []
        if argv:
            names.add(os.path.basename(argv[0]))
    names.update({"kiro-cli", "claude", "claude-code", "codex", "cursor-agent"})
    want = os.path.realpath(root)
    me = os.getpid()
    helpers = helper_pids(root)
    table = _proc_table() if helpers else {}

    def under_helper(pid):
        seen, current = set(), pid
        while current > 1 and current not in seen:
            if current in helpers:
                return True
            seen.add(current)
            current = table.get(current, (0, 0, ""))[0]
        return False

    out = []
    for pid in procs.all_pids():
        if pid == me:
            continue
        av = procs.argv(pid)
        if not av or not _is_agent_process(pid, names, av) or procs.cwd(pid) is not None:
            continue
        if any(os.path.isabs(a) and os.path.realpath(a.rstrip("/\\")) == want for a in av):
            continue                      # it names this tree: agent_pids placed it
        if helpers and under_helper(pid):
            continue
        out.append(pid)
    return sorted(out)


def git_text(root, *args, timeout=60):
    """git's output as text without a shell, or "" when git fails (#71).

    cmd.exe treats neither single quotes nor `2>/dev/null` as a POSIX shell does:
    the quoted `--pretty` format was split at its "|" and the log came back empty.
    """
    try:
        return _git_output(root, *args, timeout=timeout).decode(UTF8, "replace").strip()
    except RuntimeError:
        return ""


def discover_architect(cwd):
    """The newest Claude Code session for a directory, by transcript mtime.

    Pinning a session id in config goes stale the moment the human opens a new
    conversation, and a watchdog that wakes a dead session fails silently — the
    worst shape of failure, because everything still looks configured. Resolve it
    from disk instead, the same way the implementer's session is resolved.
    """
    # Claude Code flattens the path into a directory name - a worktree under
    # ".claude" becomes "…Voltrai--claude-worktrees…", with the doubled dash where
    # "/." was - and on Windows the drive and backslashes go the same way (#71).
    d = claude_project_dir(cwd)
    if not os.path.isdir(d):
        return None
    best, best_mt = None, 0
    for f in os.listdir(d):
        if not f.endswith(".jsonl"):
            continue
        mt = os.path.getmtime(os.path.join(d, f))
        if mt > best_mt:
            best, best_mt = f[:-6], mt
    return {"session": best, "transcript": os.path.join(d, best + ".jsonl"),
            "age": int(time.time() - best_mt)} if best else None


def _architect_process_roots(root, architect=None, helper_only=False):
    """Configured architect roots, optionally restricted to proven AO helpers."""
    from . import procs

    if architect is None:
        try:
            architect = (load_config(root).get("architect") or {})
        except (OSError, TypeError, ValueError):
            architect = {}
    architect = architect or {}
    configured = architect.get("argv") or []
    if not configured:
        return []

    command = _program_name(configured[0])
    names = {command} if command else set()
    # CLI launchers commonly exec a runtime under the package's other public
    # name. These are aliases of the configured command, not a generic list of
    # agents: a Kiro implementer must not become a Claude architect merely
    # because both are interactive in the same tree.
    aliases = {
        "claude": {"claude", "claude-code"},
        "claude-code": {"claude", "claude-code"},
        "kiro": {"kiro", "kiro-cli"},
        "kiro-cli": {"kiro", "kiro-cli"},
    }
    names.update(aliases.get(command, set()))
    if not names:
        return []

    def normal_path(path):
        raw = str(path).replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", raw):
            return raw.rstrip("/").lower()
        return os.path.normcase(os.path.realpath(raw))

    def absolute_path(path):
        raw = str(path).replace("\\", "/")
        return os.path.isabs(raw) or bool(re.match(r"^[A-Za-z]:/", raw))

    targets = {normal_path(architect.get("cwd") or root)}
    if helper_only:
        # Watchdog helpers are spawned in the project root even when the human
        # architect works in a configured worktree.
        targets.add(normal_path(root))
    table = _proc_table()
    parents = {pid: row[0] for pid, row in table.items()}
    helpers = helper_pids(root, "architect" if helper_only else None)

    def under_helper(pid):
        current = pid
        seen = set()
        while current > 1 and current not in seen:
            if current in helpers:
                return True
            seen.add(current)
            current = parents.get(current, 0)
        return False

    candidates = set()
    vectors = {}
    for pid in procs.all_pids():
        helper = under_helper(pid)
        if pid == os.getpid() or (helper_only and not helper) \
                or (not helper_only and helper):
            continue
        argv = procs.argv(pid)
        if not argv or not _is_configured_agent_process(names, argv):
            continue
        cwd = procs.cwd(pid)
        if cwd is None:
            # Windows' existing process backend cannot read cwd. Keep the same
            # fail-closed fallback as agent_pids(): an exact absolute repository
            # argument is required until backlog #9 adds PEB cwd support.
            in_project = any(
                normal_path(arg.rstrip("/\\")) in targets
                for arg in argv if absolute_path(arg)
            )
        else:
            in_project = normal_path(cwd) in targets
        if not in_project:
            continue
        candidates.add(pid)
        vectors[pid] = argv

    # Preserve ancestry through nonmatching intermediaries. Filtering first and
    # looking only at an immediate parent turns `claude -p -> shell -> runtime`
    # into two roots; the flagless runtime then looks interactive. Walk the full
    # parent graph and assign every matching process to its highest matching
    # ancestor, with cycle detection for a corrupt or racing process snapshot.
    roots = set()
    for pid in candidates:
        root_pid = pid
        current = pid
        seen = set()
        while current not in seen:
            seen.add(current)
            parent = parents.get(current, 0)
            if parent in candidates:
                root_pid = parent
            if parent <= 1 or parent not in parents:
                break
            current = parent
        roots.add(root_pid)
    return [(pid, vectors[pid]) for pid in sorted(roots)]


def architect_present(root, architect=None):
    """Is a human-driven architect process alive for this project?

    Presence is a process fact, not transcript recency. Match only the configured
    architect command in its configured cwd, exclude AO helpers and descendants,
    and classify headlessness at the full process-tree root. No positive result
    is cached, so process exit releases presence on the next scan.
    """
    headless_flags = ("-p", "--print", "--no-interactive")
    return any(
        not any(flag in argv for flag in headless_flags)
        for _, argv in _architect_process_roots(root, architect)
    )


def architect_turn_present(root, architect=None):
    """Is a configured architect process alive in a proven AO helper tree?

    This is the duplicate-wake guard. It revalidates the registered helper's
    process-start identity and current process tree; an unrelated same-binary
    implementer, remembered pid, or lock file cannot postpone a wake.
    """
    return bool(_architect_process_roots(root, architect, helper_only=True))


DECISION_DIR = ".ao/decisions"


def decisions(root, state=None):
    """Open questions the implementer cannot answer for itself.

    A blocker written as prose costs minutes to answer from a phone: read it,
    work out what is being asked, type a paragraph. The same blocker written as
    a question with options costs one tap. That difference decides whether a run
    survives the hours when nobody is at a desk.
    """
    d = os.path.join(root, DECISION_DIR)
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(d, f), encoding=UTF8))
        except Exception:
            continue
        rec["id"] = f[:-5]
        if state and rec.get("state") != state:
            continue
        out.append(rec)
    return out


def ask(root, question, options, context=None, slice_id=None):
    """Record a question. Free text is always the last option.

    Options are a convenience, never a cage: the answer that matters is often the
    one nobody listed, and a form that cannot express it produces a wrong answer
    chosen because it was available.
    """
    d = os.path.join(root, DECISION_DIR)
    os.makedirs(d, exist_ok=True)
    did = f"D-{int(time.time())}"
    opts = [{"key": chr(ord('a') + i), "label": o} for i, o in enumerate(options[:8])]
    opts.append({"key": "x", "label": "Başka (serbest metin)", "free_text": True})
    # The same question answered before, here or in another project, travels with it (#43).
    try:
        precedents = [{key: found.get(key) for key in ("project", "kind", "id", "at", "outcome", "source")}
                      for found in recall(f"{question} {context or ''}", root, limit=3, share=0.4)]
    except Exception:
        precedents = []
    rec = scan_record({"asked_at": int(time.time()), "question": question, "context": context,
                       "slice": slice_id, "options": opts, "state": "open",
                       "answer": None, "answered_at": None, "answered_by": None,
                       "precedents": precedents})
    json.dump(rec, open(os.path.join(d, did + ".json"), "w", encoding=UTF8),
              ensure_ascii=False, indent=2)
    rec["id"] = did
    return rec


def answer(root, did, key_or_text, by="human"):
    """Answer one question. Returns the updated record, or None if unknown."""
    p = os.path.join(root, DECISION_DIR, did + ".json")
    if not os.path.exists(p):
        return None
    rec = json.load(open(p, encoding=UTF8))
    chosen = next((o for o in rec["options"] if o["key"] == key_or_text.strip().lower()), None)
    rec["answer"] = chosen["label"] if chosen and not chosen.get("free_text") \
        else key_or_text
    rec["answer_key"] = chosen["key"] if chosen else None
    rec["state"] = "answered"
    rec["answered_at"] = int(time.time())
    rec["answered_by"] = by
    rec = scan_record(rec)
    json.dump(rec, open(p, "w", encoding=UTF8), ensure_ascii=False, indent=2)
    rec["id"] = did
    return rec


def last_nudge_error(root):
    """The most recent failed nudge, if the watchdog recorded one."""
    key = project_key(root)
    try:
        st = json.load(open(os.path.join(HOME, ".ao", f"watchdog-{key}.json"), encoding=UTF8))
    except Exception:
        return None
    return st.get("last_error")


def recent_errors(recs, limit=3, adapter=None):
    """Tool calls the agent itself marked as failed.

    Use the structural verdict the store already carries — Kiro records
    `success: true|false` on every tool_result — never a text search. Matching on
    words like "failed" surfaces the agent's own search patterns and passing test
    names, which is worse than showing nothing: a panel that cries wolf gets
    ignored exactly when it is right.
    """
    field = ((adapter or {}).get("telemetry", {}).get("failure") or {}).get("field", "success")
    out = []
    for r in reversed(recs):
        pl = r.get("payload", r)
        if not isinstance(pl, dict) or pl.get("type") != "tool_result":
            continue
        if pl.get(field) is not False:
            continue
        # Failed tool output is usually a wall of passing lines with the real
        # cause buried in it. Lead with the line that actually failed.
        raw = str(pl.get("content", ""))
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
        def is_signal(ln):
            low = ln.lower()
            if ln.startswith("✔") or low.startswith("output:"):
                return False
            return (ln.startswith("✖") or "error ts" in low or "error:" in low
                    or low.startswith("fail") or " failing tests" in low
                    or "exit code: 1" in low or low.startswith("✗"))
        signal = next((ln for ln in lines if is_signal(ln)), None)
        if not signal:
            signal = next((ln for ln in lines if not ln.lower().startswith("output:")
                           and not ln.startswith("✔")), lines[0] if lines else raw)
        text = " ".join(str(signal)[:260].split())
        out.append((local_hhmm(r.get("timestamp", "")) or "--:--", text))
        if len(out) >= limit:
            break
    return list(reversed(out))


# ── repository signals ────────────────────────────────────────────────────────

def _review_verdict(body):
    """Return one explicit top-level verdict value, otherwise INVALID."""
    from .verdicts import VERDICTS
    allowed = set(VERDICTS)
    values = []
    malformed = False
    for line in str(body or "").splitlines():
        if line.startswith((" ", "\t")):
            continue
        if not re.match(r"^(?:\*\*)?verdict\b", line, re.I):
            continue
        prefix = re.match(
            r"^(?:\*\*verdict[ \t]*:\*\*|verdict[ \t]*:)", line, re.I
        )
        if not prefix:
            malformed = True
            continue
        value = line[prefix.end():].strip(" \t")
        if value.startswith("**") and value.endswith("**") and len(value) >= 4:
            value = value[2:-2].strip(" \t")
        if not re.fullmatch(r"[A-Za-z_]+", value or ""):
            malformed = True
            continue
        value = value.upper()
        if value not in allowed:
            malformed = True
            continue
        if value not in values:
            values.append(value)
    return values[0] if not malformed and len(values) == 1 else "INVALID"


def _has_verdict_marker(body):
    """Whether output contains any verdict-like marker; authority stays stricter."""
    return bool(re.search(r"\bverdict\b", str(body or ""), re.I))


def reviews(root, reviews_dir, limit=4):
    d = os.path.join(root, reviews_dir)
    if not os.path.isdir(d):
        return []
    # Review artefacts only. `ao init` leaves semantic-review/.gitkeep, and as the
    # newest file it was reported as an INVALID review and written into every
    # verification record as the review the tree had been measured against.
    files = sorted((f for f in os.listdir(d)
                    if not f.startswith(".") and os.path.isfile(os.path.join(d, f))),
                   key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)[:limit]
    out = []
    for f in files:
        verdict = "INVALID"
        try:
            body = open(os.path.join(d, f), errors="ignore", encoding=UTF8).read()
            verdict = _review_verdict(body)
            evidence = review_evidence(body)
            # ao records the verdict it adjudicated in the evidence line too; a
            # margin verdict that disagrees was changed after ao wrote it (#60).
            if isinstance(evidence, dict) and "verdict" in evidence \
                    and evidence["verdict"] != verdict:
                verdict = "INVALID"
            # Preserve legacy one-line quota/auth artifacts written before reviews
            # carried evidence. An artefact with an evidence line records how the
            # reviewer's process ended as its verdict line, so words in it — a
            # finding about authentication or a rate limit — never decide it (#57).
            has_verdict_line = _has_verdict_marker(body)
            if verdict == "INVALID" and not has_verdict_line \
                    and evidence is None \
                    and REVIEW_UNAVAILABLE_RE.search(body):
                verdict = "UNAVAILABLE"
        except Exception:
            pass
        out.append((f, verdict))
    return out


REVIEW_UNAVAILABLE_RE = re.compile(
    r"hit your (?:session|usage|weekly|monthly) limit|usage limit|rate limit|"
    r"out of (?:credits|quota)|API Error: (?:401|403|429|5\d\d)|"
    r"Not logged in|login required|authentication", re.I)


def reviewer_state_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"reviewer-{key}.json")


def reviewer_state(root):
    try:
        return json.load(open(reviewer_state_path(root), encoding=UTF8))
    except (OSError, ValueError):
        return {}


def set_reviewer_state(root, **fields):
    st = reviewer_state(root)
    st.update(fields)
    try:
        os.makedirs(os.path.dirname(reviewer_state_path(root)), exist_ok=True)
        json.dump(st, open(reviewer_state_path(root), "w", encoding=UTF8))
    except OSError:
        pass
    return st


def _edge_ids(item, field):
    """Board ids a note names; a parenthesised word is a remark, not an id."""
    return [token for token in re.split(r"[,\s]+", item["notes"].get(field, ""))
            if token and not token.startswith("(")]


def board_graph(root):
    """READY, derived from the board's dependency edges, and what is wrong with them (#33).

    A dependency graph, not a handoff mechanism. The valuable part of "backend
    done, now the frontend" is that the second item becomes eligible the moment
    the first lands, and putting the edge on the board keeps the implementer from
    choosing its own scope, the one authority it must not hold.

    `needs: B3, B4` on a queued item names the board items it waits for;
    `unlocks:` on any item is the same edge written from the other end. `needs:`
    on a blocked item stays its reason in words. On 2026-09-07 the architect
    decided what was actionable by reading the board as prose, and a dependency
    named there was never checked against anything. So an id that is not on the
    board, an id listed twice and a cycle are problems naming both ends, never a
    silent skip, and no item they touch is READY; neither is one `waiting:` on
    someone. READY is derived here and nowhere else: a hand-written `## ready`
    section is a problem too.

    Returns {"ready": [item with its role], "problems": [text]}.
    """
    b = board(root)
    problems, where = [], {}
    for state in BOARD_STATES:
        for item in b[state]:
            if item["id"] in where:
                problems.append(f"{item['id']} is on the board twice, under {where[item['id']]} and {state}")
            where.setdefault(item["id"], state)
    try:
        with open(os.path.join(root, ".ao", "board.md"), encoding=UTF8, errors="replace") as fh:
            if any(re.match(r"^\s*##\s+ready\s*$", line, re.I) for line in fh):
                problems.append("the board has a hand-written READY section; READY is derived from `needs:`, "
                                "so its items belong under `## queued`")
    except OSError:
        pass
    edges = {item["id"]: set(_edge_ids(item, "needs")) for item in b["queued"]}
    for state in BOARD_STATES:
        for item in b[state]:
            for target in _edge_ids(item, "unlocks"):
                if target not in where:
                    problems.append(f"{item['id']} unlocks {target}, which is not on the board")
                else:
                    edges.setdefault(target, set()).add(item["id"])
    broken = set()
    for item_id in sorted(edges):
        for dep in sorted(edges[item_id]):
            if dep not in where:
                problems.append(f"{item_id} needs {dep}, which is not on the board")
                broken.add(item_id)
    colour, trail, cycles = {}, [], set()

    def visit(node):
        colour[node] = "open"
        trail.append(node)
        for dep in sorted(edges.get(node, ())):
            if colour.get(dep) == "open":
                cycle = trail[trail.index(dep):]
                if frozenset(cycle) not in cycles:
                    cycles.add(frozenset(cycle))
                    problems.append("a cycle: " + ", ".join(
                        f"{first} needs {then}" for first, then in zip(cycle, cycle[1:] + [dep])))
                broken.update(cycle)
            elif dep not in colour and dep in edges:
                visit(dep)
        trail.pop()
        colour[node] = "closed"

    for node in sorted(edges):
        if node not in colour:
            visit(node)
    done = {item["id"] for state in ("done", "verified") for item in b[state]}
    ready_items = [{**item, "role": item["notes"].get("role", "")} for item in b["queued"]
                   if item["id"] not in broken and "waiting" not in item["notes"]
                   and all(dep in done for dep in edges.get(item["id"], ()))]
    return {"ready": ready_items, "problems": problems}


def ready(root):
    """Queued items whose dependencies are all done and which wait on no one (#33)."""
    return board_graph(root)["ready"]


def review_loop(root, reviews_dir, min_repeats=3):
    """The same finding coming back review after review.

    A round budget counts rounds. It cannot see that round four's blocker is
    round two's blocker with the line numbers moved — which is the actual
    failure: the implementer is not converging, and more rounds will not help.
    Fingerprint each finding on its file and its first clause, and report any
    that recurs across consecutive NEEDS_CHANGES reviews.
    """
    seen = {}
    d = os.path.join(root, reviews_dir)
    for f, v in reviews(root, reviews_dir, limit=12):
        if "APPROVED" in (v or "").upper():
            break
        try:
            body = open(os.path.join(d, f), errors="replace", encoding=UTF8).read()
        except OSError:
            continue
        # Findings sit in the reviewer's verbatim block, indented (#54).
        for m in re.finditer(r"^(?:    )?- \[(BLOCKER|HIGH|MEDIUM|LOW)\]\s*([^\s:]+)[:\d]*\s*[—-]\s*(.{0,60})",
                             body, re.M):
            key = (m.group(2), re.sub(r"\W+", " ", m.group(3).lower()).strip()[:40])
            seen.setdefault(key, {"sev": m.group(1), "count": 0, "reviews": []})
            seen[key]["count"] += 1
            seen[key]["reviews"].append(f)
    return [{"file": k[0], "clause": k[1], **v}
            for k, v in seen.items() if v["count"] >= min_repeats]


# ---- recall: what was decided, found or learned before, in every project (#43) ----------

_RECALL_STOP = frozenset(
    "the and for with that this from into have has had was were are not but then than when what which who why how "
    "its our your their there here been being will would could should about after before over under only also just "
    "very more most some such each other same one two can may must does did done yet still any all bir ve ile için "
    "bu şu da de mi ne gibi daha çok".split())
_FINDING_LINE = re.compile(r"^\s*- \[(BLOCKER|HIGH|MEDIUM|LOW)\]\s*(.+)$")


def _recall_words(text):
    words = (word.strip("._-") for word in re.findall(r"[a-zçğıöşü0-9][a-zçğıöşü0-9_.-]*", str(text or "").lower()))
    return {word for word in words if len(word) >= 3 and word not in _RECALL_STOP}


def _epoch(value):
    """A recorded time as seconds, whether ao wrote it as a number or as ISO text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def recall_roots(root=None):
    """(project, root) for every project this machine has registered, the one asking first."""
    out, seen = [], set()
    if root:
        out.append((project_key(root), root))
        seen.add(os.path.realpath(root))
    for name, row in sorted(project_registry().items()):
        path = row.get("root")
        if path and os.path.isdir(path) and os.path.realpath(path) not in seen:
            seen.add(os.path.realpath(path))
            out.append((name, path))
    return out


def recall_entries(project, root):
    """What one project remembers, each with the file and row it came from (#43).

    Questions asked and their answers, architect decisions, waivers, review
    findings with their review's verdict, and lessons. A source that cannot be
    read is passed over; the rest still answer.
    """
    entries = []

    def add(kind, ident, at, text, outcome, source):
        entries.append({"project": project, "kind": kind, "id": ident, "at": _epoch(at), "text": text,
                        "outcome": outcome, "source": source})

    for rec in decisions(root):
        add("question", rec.get("id"), rec.get("answered_at") or rec.get("asked_at"),
            " ".join(str(rec.get(key) or "") for key in ("question", "context", "answer")),
            f"answered: {rec['answer']}" if rec.get("answer") else rec.get("state") or "open",
            f"{DECISION_DIR}/{rec.get('id')}.json")
    readers = ((decision_rows, "decision", ("decision", "why", "scope"), ".ao/ledger/decisions.jsonl"),
               (waiver_rows, "waiver", ("gate", "slice", "why"), ".ao/ledger/waivers.jsonl"))
    for reader, kind, fields, source in readers:
        try:
            rows = reader(root)
        except Exception:
            continue
        for number, row in enumerate(rows, 1):
            if isinstance(row, dict):
                add(kind, row.get("id"), row.get("at"), " ".join(str(row.get(key) or "") for key in fields),
                    row.get("event") or ("recorded by " + str(row.get("by") or "architect")), f"{source}:{number}")
    reviews_dir = "semantic-review"
    try:
        with open(os.path.join(root, ".ao", "config.json"), encoding=UTF8) as fh:
            reviews_dir = json.load(fh).get("reviews") or reviews_dir
    except (OSError, ValueError, AttributeError):
        pass
    directory = os.path.join(root, reviews_dir)
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        path = os.path.join(directory, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding=UTF8, errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        verdict = _review_verdict(body)
        for number, line in enumerate(body.splitlines(), 1):
            found = _FINDING_LINE.match(line)
            if found:
                add("finding", name, os.path.getmtime(path), found.group(2), f"{found.group(1)} in a {verdict} review",
                    f"{reviews_dir}/{name}:{number}")
    try:
        with open(os.path.join(root, "docs", "lessons.md"), encoding=UTF8, errors="replace") as fh:
            lessons = fh.read().splitlines()
    except OSError:
        lessons = []
    starts = [number for number, line in enumerate(lessons) if line.startswith("## ")]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lessons)
        add("lesson", lessons[start][3:].strip(), None, "\n".join(lessons[start:end]), "a lesson",
            f"docs/lessons.md:{start + 1}")
    return entries


def recall(text, root=None, limit=10, exclude=(), share=0.5):
    """Records in every registered project that share the words of `text`, best first (#43).

    Plain words over the files ao already keeps: no index, no service, no network.
    One or two words must all appear; longer text needs `share` of its words.
    """
    words = _recall_words(text)
    if not words:
        return []
    need = len(words) if len(words) <= 2 else max(2, int(len(words) * share + 0.999))
    found = []
    for project, path in recall_roots(root):
        try:
            entries = recall_entries(project, path)
        except Exception:
            continue
        for entry in entries:
            if entry["id"] in exclude:
                continue
            score = len(words & _recall_words(entry["text"]))
            if score >= need:
                found.append(dict(entry, score=score))
    found.sort(key=lambda entry: (-entry["score"], -(entry["at"] or 0)))
    return found[:limit]


def architect_absence(root, cfg):
    """When the architect was last seen, and the questions waiting for its return (#84).

    Seen means its session wrote, it recorded a decision, or it left mail still in
    the mailbox, whichever is newest.
    """
    seen = []
    try:
        found = discover_architect((cfg.get("architect") or {}).get("cwd") or root)
        if found and found.get("age") is not None:
            seen.append(time.time() - float(found["age"]))
    except Exception:
        pass
    try:
        rows = decision_rows(root)
        if rows and _epoch(rows[-1].get("at")):
            seen.append(_epoch(rows[-1].get("at")))
    except Exception:
        pass
    mailbox_dir = cfg.get("mailbox", "agent-mail")
    for name in mailbox(root, mailbox_dir):
        if from_architect(name, cfg):
            try:
                seen.append(os.path.getmtime(os.path.join(root, mailbox_dir, name)))
            except OSError:
                pass
    waiting = sorted(decisions(root, "open"), key=lambda d: d.get("asked_at") or 0)
    return {"seen_at": max(seen) if seen else None, "waiting": [d["id"] for d in waiting],
            "oldest_at": waiting[0].get("asked_at") if waiting else None}


# ---- a worktree lives exactly as long as its slice (#42) --------------------------------

def default_branch(root):
    """The branch work lands on: origin's HEAD, else main, else master, else the main checkout's."""
    remote = git_text(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if remote.startswith("refs/remotes/origin/"):
        return remote[len("refs/remotes/origin/"):]
    for name in ("main", "master"):
        if git_text(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
            return name
    trees = worktree_list(root)
    return trees[0]["branch"] if trees else None


def worktree_list(root):
    """Every worktree of the repository: {"path", "head", "branch", "prunable"}, the main one first."""
    listed = _git_output(root, "worktree", "list", "--porcelain", "-z")
    out = []
    for record in listed.split(b"\0\0"):
        fields = {}
        for attribute in record.split(b"\0"):
            key, _, value = os.fsdecode(attribute).partition(" ")
            if key:
                fields[key] = value
        if "worktree" in fields:
            branch = fields.get("branch", "")
            out.append({"path": fields["worktree"], "head": fields.get("HEAD"),
                        "branch": branch[len("refs/heads/"):] if branch.startswith("refs/heads/") else None,
                        "prunable": "prunable" in fields})
    return out


def reviews_in_flight(root):
    """Submitted reviews in one checkout whose runner is alive or has only just started."""
    directory = os.path.join(root, ".ao", "reviews")
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    flying = []
    for name in names:
        if not (name.startswith("R-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(directory, name), encoding=UTF8) as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and state.get("state") == "running" and (
                _pid_alive(state.get("pid")) if state.get("pid")
                else time.time() - float(state.get("submitted_at") or 0) < 60):
            flying.append(state.get("id") or name[:-5])
    return flying


def _tree_bytes(path):
    total = 0
    for directory, subdirs, files in os.walk(path):
        subdirs[:] = [d for d in subdirs if not os.path.islink(os.path.join(directory, d))]
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total


def worktree_facts(root, cfg, sizes=False):
    """What decides whether each worktree may go (#42).

    A worktree may go when its branch is merged into the default branch, when the
    board rejected the slice that owns it (`worktree:` or `branch:` on the item),
    or when its directory is already gone - and never while it holds product
    changes nobody committed, a review in flight, or the command that is asking.
    """
    target = default_branch(root)
    board_items = board(root)
    owners = []
    for state, items in board_items.items():
        for item in items:
            owners.append((state, item))
    here = os.path.realpath(root)
    out = []
    for index, tree in enumerate(worktree_list(root)):
        path, branch = tree["path"], tree["branch"]
        real = os.path.realpath(path)
        slice_state = next((state for state, item in owners
                            if (item["notes"].get("worktree") and os.path.realpath(item["notes"]["worktree"]) == real)
                            or (branch and item["notes"].get("branch") == branch)), None)
        merged = bool(branch and target and branch != target and subprocess.run(
            [git_binary(), "merge-base", "--is-ancestor", tree["head"], target], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
        exists = os.path.isdir(path)
        dirty = product_dirty(path, cfg) if exists else []
        flying = reviews_in_flight(path) if exists else []
        keep = []
        if index == 0:
            keep.append("the main checkout")
        if real == here:
            keep.append("the checkout asking")
        if dirty:
            keep.append(f"{len(dirty)} uncommitted product change(s)")
        if flying:
            keep.append(f"review in flight: {', '.join(flying)}")
        why = "merged into " + target if merged else "its slice was rejected" if slice_state == "rejected" \
            else "its directory is gone" if not exists or tree["prunable"] else None
        may_go = bool(why) and not keep
        # Sizes are for what may go: walking every dependency tree on disk is not free.
        out.append(dict(tree, merged=merged, slice_state=slice_state, dirty=len(dirty), in_flight=flying,
                        keep=keep, why=why, may_go=may_go,
                        bytes=_tree_bytes(path) if sizes and may_go and exists else None))
    return out


def prune_worktree(root, fact, apply=False, now=None):
    """Retire one worktree that may go: archive its coordination state and branch tip, then remove both (#42).

    The `.ao/` state and review artefacts go to ~/.ao/archive/<project>/, and the
    branch tip stays reachable as refs/ao/archive/<branch>-<stamp>, so what the
    worktree held can still be read after it is gone.
    """
    import shutil
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y%m%d-%H%M%S")
    name = os.path.basename(os.path.normpath(fact["path"]))
    archive = os.path.join(HOME, ".ao", "archive", project_key(root), f"worktree-{name}-{stamp}")
    steps = []
    for part in (".ao", "semantic-review"):
        source = os.path.join(fact["path"], part)
        if os.path.isdir(source):
            steps.append(f"archive {part}/ to {archive}")
            if apply:
                shutil.copytree(source, os.path.join(archive, part), symlinks=True)
    if fact["branch"]:
        steps.append(f"keep {fact['branch']} at refs/ao/archive/{fact['branch']}-{stamp}")
        if apply:
            _git_output(root, "update-ref", f"refs/ao/archive/{fact['branch']}-{stamp}", fact["head"])
    steps.append(f"remove the worktree {fact['path']}")
    if apply and os.path.isdir(fact["path"]):
        _git_output(root, "worktree", "remove", "--force", fact["path"])
    if fact["branch"]:
        steps.append(f"delete the branch {fact['branch']}")
        if apply:
            _git_output(root, "branch", "-D", fact["branch"])
    steps.append("git worktree prune")
    if apply:
        _git_output(root, "worktree", "prune")
    return steps


# ---- the secondary project: the same agent, another queue (#8, #22, #92) ----------------

HUMAN_WAITING = ("human", "insan", "person", "owner")


def secondary_projects(cfg):
    """The projects this one names as secondary (`"secondary": [{"root", "name"}]`), each with its config (#8)."""
    out = []
    for entry in (cfg or {}).get("secondary") or []:
        other_root = entry.get("root") if isinstance(entry, dict) else entry
        if not isinstance(other_root, str) or not os.path.isdir(os.path.join(other_root, ".ao")):
            continue
        try:
            other = load_config(other_root)
        except Exception:
            continue
        name = (entry.get("name") if isinstance(entry, dict) else None) or project_key(other_root)
        out.append({"name": name, "root": other_root, "cfg": other})
    return out


def working_elsewhere(cfg, idle_seconds):
    """The secondary project the implementer is writing in now, if it is (#22).

    2026-09-07: while the implementer worked in an ao worktree, the Voltrai
    watchdog read the same agent as idle with a slice running and nudged it
    back. Presence belongs to the agent: a transcript moving in a secondary
    project is the agent working.
    """
    for other in secondary_projects(cfg):
        try:
            transcript, _ = session_paths(other["cfg"])
            age = time.time() - os.path.getmtime(transcript)
        except (OSError, TypeError, ValueError):
            continue
        if age < idle_seconds:
            return {"name": other["name"], "root": other["root"], "age": age}
    return None


def secondary_ready(cfg):
    """The first READY item of a secondary project, for an implementer with nothing READY here (#8)."""
    for other in secondary_projects(cfg):
        try:
            items = ready(other["root"])
        except Exception:
            continue
        if items:
            return {"name": other["name"], "root": other["root"], "item": items[0]["id"]}
    return None


def human_waits(root):
    """Blocked board items the implementer declared as waiting on a person: `waiting: human` (#92)."""
    return [item for item in board(root)["blocked"]
            if (item["notes"].get("waiting") or "").strip().lower() in HUMAN_WAITING]


def safe_slug(text, fallback="note", limit=40):
    """A file-name part holding only [A-Za-z0-9._-] (#19).

    `ao mail send note "ao: kiro/* dal …"` kept the "/" and wrote into a directory
    that did not exist. Separators, wildcards, quotes, whitespace and letters
    outside ASCII all become a dash; what is left is cut to `limit`.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "")).strip("-.")[:limit].strip("-.")
    return slug or fallback


def note(root, cfg, to, title, body, urgent=False):
    """Write an architect message into the mailbox through the tool.

    So that a woken architect needs no raw Write or Edit to do its job. That
    matters because the one time it had them, it used them on the orchestrator's
    own source and built a runaway. The mailbox is the only thing an unattended
    architect should be able to write, and this is the only door to it.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    os.makedirs(box, exist_ok=True)
    slug = safe_slug(title.lower(), "not")
    kind = "ACIL" if urgent else "DECISION"
    impl, arch = mail_names(cfg)
    to = safe_slug(to, impl)
    name = f"{time.strftime('%Y%m%d-%H%M')}-{arch}-to-{to}-{kind}-{slug}.md"
    text = f"# {title}\n\n" + ("## ACİL\n\n" if urgent else "") + body.rstrip() + "\n"
    return write_mail(root, cfg, name, text, {"kind": kind.lower(), "from": arch, "to": to})


def running_slice(root):
    """The one board item review accounting treats as current.

    A malformed board may contain several running entries. Preserve the board's
    established first-entry behavior, but resolve it once so review attribution,
    start time and re-specification cannot silently select different slices.
    """
    items = board(root)["running"]
    return items[0] if items else None


def slice_boundary(item):
    """The declared review boundary for a parsed board item."""
    if not item:
        return ""
    notes = item.get("notes") or {}
    return notes.get("acceptance") or notes.get("scope") or item.get("title") or ""


# ---- a slice's boundary: a sentence, or a file the row points at (#73, #35) ------------

BOUNDARY_SECTIONS = ("invariant", "scenarios", "paths", "out of scope", "why one slice")
_BOUNDARY_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")
_NAMED_FILE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.\w+|[\w-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|"
                         r"rb|cs|swift|c|h|cpp|hpp|sql|sh|ps1|json|ya?ml|toml|md))(?![\w/-])")
_NAMED_SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})(?:\(\))?`")


def boundary_pointer(item):
    """(path, commit or None) when a board item's boundary is a file: `boundary: docs/slices/B8a.md@1a2b3c4`."""
    value = (((item or {}).get("notes") or {}).get("boundary") or "").strip()
    if not value:
        return None
    path, _, commit = value.partition("@")
    return path.strip(), (commit.strip() or None)


def boundary_sections(text):
    """{section: body} for the headed parts of a boundary file (#73)."""
    sections, current = {}, None
    for line in text.splitlines():
        heading = re.match(r"^#{1,4}\s+(.+?)\s*$", line)
        if heading:
            name = heading.group(1).strip().lower()
            current = next((section for section in BOUNDARY_SECTIONS if section in name), None)
            if current:
                sections.setdefault(current, "")
            continue
        if current:
            sections[current] += line + "\n"
    return sections


def read_boundary(root, item):
    """The boundary file a slice points at, read at the commit the pointer names (#73).

    The boundary lived as prose in a table cell, and twice on 2026-09-07/08 the
    boundary itself was what was wrong, found mid-slice by an implementer that had
    started, with no diff to show what changed. A file read at a named commit is
    one source of truth, and when it has changed since, the change travels to the
    reviewer as a diff. None when the item's boundary is a sentence.

    Returns {"file", "commit", "changed", "sha256", "label", "text", "sections", "problem"}.
    """
    pointer = boundary_pointer(item)
    if not pointer:
        return None
    path, commit = pointer
    out = {"file": path, "commit": commit, "changed": False, "sha256": None, "sections": {}, "problem": None}
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, path))
    if os.path.isabs(path) or not target.startswith(real_root + os.sep):
        out["problem"] = "it is not a path inside the repository"
    elif commit and not _BOUNDARY_COMMIT.fullmatch(commit):
        out["problem"] = f"{commit!r} is not a commit id"
    else:
        try:
            if commit:
                data = _git_output(root, "show", f"{commit}:{path}")
            else:
                with open(target, "rb") as fh:
                    data = fh.read()
        except (OSError, RuntimeError) as exc:
            out["problem"] = str(exc)
    if out["problem"]:
        out.update(label=f"{path} (unreadable)",
                   text=f"the boundary file {path} cannot be read: {out['problem']} - say so as a finding")
        return out
    content = data.decode(UTF8, "replace")
    diff = ""
    if commit:
        try:
            diff = _git_output(root, "diff", commit, "--", path).decode(UTF8, "replace")
        except RuntimeError:
            diff = ""
    label = f"{path} at {commit[:12]}" if commit else f"{path} as it stands in the worktree"
    text = f"the boundary file {label}:\n\n{content}"
    if diff.strip():
        label += ", changed since"
        text += (f"\n\nThe file has changed since {commit[:12]}. The change is part of what you judge: "
                 f"a boundary that moved mid-slice is a finding unless a decision records it.\n{diff}")
    out.update(changed=bool(diff.strip()), sha256="sha256:" + hashlib.sha256(data).hexdigest(), label=label,
               text=text, sections=boundary_sections(content))
    return out


def declared_paths(item, boundary=None):
    """[(path, new)] a slice declares: its boundary file's Paths section, or its row's `paths:` note."""
    if boundary and boundary.get("sections", {}).get("paths"):
        raw = [line.strip().lstrip("-*").strip() for line in boundary["sections"]["paths"].splitlines()]
    else:
        raw = (((item or {}).get("notes") or {}).get("paths") or "").split(",")
    out = []
    for entry in raw:
        entry = entry.strip().strip("`").strip()
        new = bool(re.search(r"\((?:new|yeni)\)$", entry, re.I))
        path = re.sub(r"\s*\((?:new|yeni)\)$", "", entry, flags=re.I).strip().strip("`").strip()
        if path:
            out.append((path, new))
    return out


def _symbol_owners(root, symbol):
    """Tracked files that define a symbol, by a definition keyword in front of its name."""
    pattern = rf"(def|class|function|interface|type|struct|enum|trait|fn|func|const|let|var)[[:space:]]+{symbol}"
    try:
        listed = _git_output(root, "grep", "-l", "-z", "-w", "-E", pattern, timeout=20)
    except RuntimeError:
        return []
    return [os.fsdecode(path) for path in listed.split(b"\0") if path]


def boundary_conflicts(root, item):
    """What a slice's declared paths cannot hold, found when it is registered (#35).

    2026-09-07: B8a named eight paths while its own acceptance could only be met
    by touching a ninth; the implementer found out mid-slice and lost three hours
    waiting on a decision. Advisory prose matching, so it names and never blocks:
    a declared path that neither exists nor is marked `(new)`, a file the
    acceptance names outside the declared paths, and the file defining a symbol
    the acceptance names in backticks when that file is outside them. An item
    that declares no paths has nothing to check.
    """
    source = read_boundary(root, item)
    if source and source["problem"]:
        return [f"its boundary file {source['file']} cannot be read: {source['problem']}"]
    declared = declared_paths(item, source)
    if not declared:
        return []

    def inside(path):
        return any(path == d or path.startswith(d.rstrip("/") + "/") for d, _ in declared)

    out = [f"{path} is declared but does not exist; write `(new)` after it if the slice creates it"
           for path, new in declared if not new and not os.path.exists(os.path.join(root, path))]
    if source:
        text = "\n".join(body for name, body in source["sections"].items()
                         if name not in ("paths", "out of scope", "why one slice")) \
            or source["text"]
    else:
        text = slice_boundary(item)
    tracked = None
    for named in sorted(set(_NAMED_FILE.findall(text))):
        if inside(named) or any(d.endswith("/" + named) for d, _ in declared):
            continue
        if "/" in named:
            if os.path.exists(os.path.join(root, named)):
                out.append(f"the acceptance names {named}, outside the declared paths")
            continue
        if tracked is None:
            try:
                tracked = [os.fsdecode(p) for p in _git_output(root, "ls-files", "-z").split(b"\0") if p]
            except RuntimeError:
                tracked = []
        owners = [p for p in tracked if os.path.basename(p) == named]
        if owners and not any(inside(p) for p in owners):
            out.append(f"the acceptance names {named} ({', '.join(owners[:3])}), outside the declared paths")
    for symbol in sorted(set(_NAMED_SYMBOL.findall(text)))[:10]:
        owners = _symbol_owners(root, symbol)
        if owners and not any(inside(p) for p in owners):
            out.append(f"the acceptance names `{symbol}`, defined in {', '.join(owners[:3])}, "
                       "outside the declared paths")
    return out


def boundary_advice(root, cfg, item):
    """Everything worth saying about one item's boundary before it is worked (#35, #73)."""
    notes = (item or {}).get("notes") or {}
    out = boundary_conflicts(root, item)
    if notes.get("boundary") and notes.get("acceptance"):
        out.append("it has both a boundary file and an acceptance sentence; one source of truth, so keep the file")
    limit = settings.get(cfg, "boundary.inline_max_chars")
    sentence = notes.get("acceptance") or ""
    if not notes.get("boundary") and len(sentence) > limit:
        out.append(f"its acceptance is {len(sentence)} characters; above boundary.inline_max_chars ({limit}) it "
                   "belongs in a file the row points at with `boundary: path@commit`")
    return out


DECISION_CHAIN = "ao-decision-row-v1"


def decisions_path(root):
    return os.path.join(root, ".ao", "ledger", "decisions.jsonl")


def decision_rows(root):
    """Architect decisions in the order `ao decide` recorded them (#65).

    Rows written before the ledger was chained are read as its legacy prefix. A
    row added by hand after a chained one breaks the chain and the read raises:
    a re-specification nobody recorded must not reset a round budget.
    """
    from .storage import read_chained_jsonl
    return [row for row in read_chained_jsonl(decisions_path(root), DECISION_CHAIN, legacy_prefix=True)
            if isinstance(row, dict)]


def respecified_at(root, item=None):
    """When the architect last re-specified the current slice (`ao decide --scope <id>`).

    None when the decision ledger cannot be trusted: the budget then stands.
    """
    item = item or running_slice(root)
    item_id = ((item or {}).get("id") or (item or {}).get("key") or "").strip()
    if not item_id:
        return None
    try:
        rows = decision_rows(root)
    except Exception:
        return None
    last = 0
    for r in rows:
        if r.get("by") == "architect" and (r.get("scope") or "").strip() == item_id:
            try:
                last = max(last, int(r.get("at") or 0))
            except (TypeError, ValueError):
                continue
    return last or None


def _recorded_review_slice(root, reviews_dir, row):
    """The slice of a review recorded before ledger rows carried it, from its unchanged file."""
    try:
        with open(os.path.join(root, reviews_dir, str(row.get("artefact"))), "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if "sha256:" + hashlib.sha256(data).hexdigest() != row.get("sha256"):
        return None
    evidence = review_evidence(data.decode(UTF8, "replace"))
    return (evidence or {}).get("slice") if isinstance(evidence, dict) else None


def rounds(root, reviews_dir):
    """Completed prospective review rounds spent on the current slice (#65).

    A round is a review that produced a verdict, read from the chained review
    ledger in the order ao recorded it. Deleting, emptying or back-dating a review
    file changes nothing, and neither does editing the board's `since:`, which an
    implementer can reach. Only index-candidate reviews of the running board
    item's exact ID count - HEAD and candidate bytes are not slice identity. The
    count runs back to the newest approval that could authorise, or to the
    architect's newest recorded re-specification of the item. A fallback's
    approval after a rejection of the same candidate authorises nothing, so it
    ends nothing: the rejection still counts. UNAVAILABLE and INVALID are not
    rounds; nobody completed a review.

    Review files from before the ledger have no row and are read as they always
    were, so a slice that began before it keeps its count.
    """
    current = running_slice(root)
    slice_id = ((current or {}).get("id") or (current or {}).get("key") or "").strip()
    if not slice_id:
        return 0
    respec = respecified_at(root, current) or 0

    from .storage import read_chained_jsonl
    try:
        rows = read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
    except Exception:
        rows = []                       # an unreadable ledger is reported elsewhere; files still count
    events, recorded, rejected = [], set(), set()
    for order, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("kind") != "index-candidate":
            continue
        recorded.add(row.get("artefact"))
        verdict, candidate = row.get("verdict"), row.get("candidate")
        if verdict not in ("APPROVED", "NEEDS_CHANGES"):
            continue
        row_slice = row["slice"] if "slice" in row else _recorded_review_slice(root, reviews_dir, row)
        mine = (row_slice or "").strip() == slice_id
        try:
            at = int(row.get("at") or 0)
        except (TypeError, ValueError):
            at = 0
        if verdict == "NEEDS_CHANGES":
            if candidate:
                rejected.add(candidate)
            if mine:
                events.append((at, 1, order, "round"))
        elif mine and row.get("authorizable") is True \
                and not (row.get("fallback") and candidate in rejected):
            events.append((at, 1, order, "approved"))

    for order, (f, v) in enumerate(reviews(root, reviews_dir, limit=None)):
        if f in recorded or v not in ("APPROVED", "NEEDS_CHANGES"):
            continue
        path = os.path.join(root, reviews_dir, f)
        try:
            at = int(os.path.getmtime(path))
            body = open(path, errors="replace", encoding=UTF8).read(100_000)
        except OSError:
            continue
        evidence = review_evidence(body)
        if not evidence or evidence.get("kind") != "index-candidate" \
                or (evidence.get("slice") or "").strip() != slice_id \
                or evidence.get("review_status") in ("unavailable", "invalid"):
            continue
        events.append((at, 0, -order, "approved" if v == "APPROVED" else "round"))

    n = 0
    for at, _, _, what in sorted(events, reverse=True):
        if at < respec or what == "approved":
            break
        n += 1
    return n


def mailbox(root, mail_dir):
    d = os.path.join(root, mail_dir)
    if not os.path.isdir(d):
        return []
    return [f for f in sorted(os.listdir(d)) if f != "README.md" and f.endswith(".md")]


REMOTE_PREFIX = "refs/remotes/origin/"


def _default_remote_branch(root):
    """The remote default branch to measure a checkout against, as a full ref, or None.

    origin/HEAD can still name a branch the remote has since deleted, so a candidate
    is used only when it resolves to a commit.
    """
    candidates = []
    try:
        ref = _git_output(root, "symbolic-ref", "--quiet",
                          "refs/remotes/origin/HEAD").decode(UTF8, "replace").strip()
        if ref.startswith(REMOTE_PREFIX):
            candidates.append(ref)
    except RuntimeError:
        pass
    for candidate in (*candidates, REMOTE_PREFIX + "main", REMOTE_PREFIX + "master"):
        try:
            _git_output(root, "rev-parse", "--verify", "--quiet", candidate + "^{commit}")
            return candidate
        except RuntimeError:
            continue
    return None


def git_state(root):
    """Where this checkout stands, not only what it holds.

    An ahead count alone reads as current on a checkout that is weeks behind. On
    2026-09-14 `ao status` printed "0 commits unpushed" for a branch 77 commits
    behind main and already merged into it, and two backlog rows were written from
    measurements taken in that checkout. So measure against the remote default
    branch, report behind as well as ahead, say when the branch is already merged,
    and say "?" when there is nothing to compare against instead of "0".

    "ahead" stays a string for existing readers; "behind" is an int or None.
    """
    ref = _default_remote_branch(root)
    ahead = behind = None
    if ref:
        try:
            counts = _git_output(root, "rev-list", "--left-right", "--count",
                                 f"{ref}...HEAD").decode(UTF8, "replace").split()
            if len(counts) == 2 and all(c.isdigit() for c in counts):
                behind, ahead = int(counts[0]), int(counts[1])
        except RuntimeError:
            pass
    merged = False
    if ahead == 0 and behind:
        # The default branch itself, checked out behind its remote, needs a pull; it
        # is not a merged branch. A detached head inside the base counts as merged.
        try:
            head = _git_output(root, "symbolic-ref", "--quiet", "HEAD").decode(UTF8, "replace").strip()
        except RuntimeError:
            head = ""
        merged = head != "refs/heads/" + ref[len(REMOTE_PREFIX):]
    known = ahead is not None
    return {
        "log": sh("git log --oneline -4", cwd=root).split("\n"),
        "dirty": [l for l in sh("git status --short", cwd=root).split("\n") if l.strip()],
        "ahead": str(ahead) if known else "?",
        "behind": behind,
        "base": ref[len("refs/remotes/"):] if known else None,
        "merged": merged,
    }


# ---- fan-out budget --------------------------------------------------------
#
# A coordinator that fans out to sub-agents has no gate of its own. One ran 47
# verification agents at once: 11 finished, 36 died with "session limit", two
# million tokens were spent and the answer was mostly missing. The watchdog's
# quota guard could not have helped — it watches the implementer's pool, and the
# fan-out was the coordinator's. This is the gate that was missing: a hard cap,
# the provider window as keyflip reports it, and the project's own record of
# what fan-outs cost, so the estimate is empirical after the first one.

def fanout_config(cfg):
    """The fan-out limits in force for this project (#74)."""
    return {name: settings.get(cfg, f"fanout.{name}")
            for name in ("max_agents", "per_agent_tokens", "window_reserve_pct")}


def provider_window(name="claude"):
    """The machine-wide usage window keyflip reports for a provider, parsed.

    Read through the same adapter command the status panel uses, so the panel and
    the verdict never disagree. Returns {pct, window, resets_in, resets_s, raw} or
    None when no readable line exists — and None is reported as "unreadable", not
    as headroom.
    """
    argv = None
    for aid in ("kiro", "claude-code"):
        try:
            spec = (load_adapter(aid).get("telemetry") or {}).get("quota") or {}
        except Exception:
            spec = {}
        if spec.get("argv"):
            argv = spec["argv"]
            break
    if not argv:
        return None
    out = sh(" ".join(argv) + " 2>/dev/null", cwd=HOME) or ""
    for line in out.split("\n"):
        if name.lower() not in line.lower():
            continue
        m = re.search(r"(\d+)\s*%", line)
        if not m:
            continue
        w = re.search(r"\b(\d+[hdw])\b", line)
        r = re.search(r"resets?\s+(?:in\s+)?([0-9hms ]+)", line)
        secs = 0
        if r:
            for n, u in re.findall(r"(\d+)\s*([hms])", r.group(1)):
                secs += int(n) * {"h": 3600, "m": 60, "s": 1}[u]
        return {"pct": int(m.group(1)), "window": w.group(1) if w else "?",
                "resets_in": r.group(1).strip() if r else "?", "resets_s": secs,
                "raw": line.strip()}
    return None


def fanout_history(root, limit=20):
    p = os.path.join(root, ".ao", "ledger", "fanouts.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-limit:]


def record_fanout(root, agents, done=None, errors=None, tokens=None, note=None):
    """What a fan-out actually cost. The next verdict is estimated from this."""
    d = os.path.join(root, ".ao", "ledger")
    os.makedirs(d, exist_ok=True)
    rec = {"at": int(time.time()), "agents": int(agents)}
    if done is not None:
        rec["done"] = int(done)
    if errors is not None:
        rec["errors"] = int(errors)
    if tokens is not None:
        rec["tokens"] = int(tokens)
    if note:
        rec["note"] = note
    rec["limit_hit"] = bool(rec.get("errors")) and bool(
        re.search(r"limit|quota|429|rate", note or "", re.I))
    with open(os.path.join(d, "fanouts.jsonl"), "a", encoding=UTF8) as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def observed_per_agent_tokens(root):
    """Average tokens per agent over recorded fan-outs (errored agents spent too)."""
    tok = n = 0
    for r in fanout_history(root, 50):
        if r.get("tokens") and (r.get("done") or r.get("errors")):
            tok += r["tokens"]
            n += (r.get("done") or 0) + (r.get("errors") or 0)
    return int(tok / n) if n else None


def fanout_verdict(root, cfg, agents, per_agent_tokens=None, provider="claude"):
    """May a fan-out of this size start now?

    Three checks, each a fact the caller can see: the project's hard cap, whether
    a fan-out already hit this provider's limit inside the current window, and
    how much of the window keyflip says is left. The token estimate is shown, not
    gated on — a percentage window cannot be converted to tokens honestly — but
    it becomes empirical after the first recorded run.
    """
    fc = fanout_config(cfg)
    observed = observed_per_agent_tokens(root)
    per = per_agent_tokens or observed or fc["per_agent_tokens"]
    win = provider_window(provider)
    reasons, verdict = [], "ok"
    if agents > fc["max_agents"]:
        verdict = "too-many"
        reasons.append(f"{agents} agents > max_agents {fc['max_agents']} — run in batches of "
                       f"{fc['max_agents']} and record each")
    now = time.time()
    span = 5 * 3600
    if win and win.get("window", "").endswith("h"):
        try:
            span = int(win["window"][:-1]) * 3600
        except ValueError:
            pass
    window_start = now - (span - win["resets_s"]) if win and win.get("resets_s") else now - span
    for r in reversed(fanout_history(root, 10)):
        if r.get("limit_hit") and r["at"] >= window_start:
            if verdict == "ok":
                verdict = "limit-hit-recently"
            reasons.append(f"a fan-out of {r['agents']} hit the provider limit "
                           f"{int((now - r['at']) / 60)}m ago ({r.get('errors')} errors); "
                           f"wait for the window to reset"
                           + (f" (in {win['resets_in']})" if win else ""))
            break
    if win:
        left = 100 - win["pct"]
        if left < fc["window_reserve_pct"]:
            if verdict == "ok":
                verdict = "window-low"
            reasons.append(f"{provider} window {win['pct']}% used, {left}% left < reserve "
                           f"{fc['window_reserve_pct']}%; resets in {win['resets_in']}")
    else:
        reasons.append(f"{provider} window unreadable (keyflip absent or no line) — "
                       f"hard cap and history only")
    spent = sum(r.get("tokens") or 0 for r in fanout_history(root, 50) if r["at"] >= window_start)
    return {"verdict": verdict, "ok": verdict == "ok", "agents": agents,
            "per_agent_tokens": per,
            "per_agent_source": "arg" if per_agent_tokens else ("observed" if observed else "default"),
            "estimated_tokens": agents * per, "spent_this_window": spent,
            "window": win, "max_agents": fc["max_agents"], "reasons": reasons}



# ---- binaries ----------------------------------------------------------------
#
# The architect was woken forty times in eleven hours and every wake died with
# "Claude Code 2.1.185 does not support this model". The binary was real, on
# PATH, and two hundred versions stale — an npm-global leftover in /usr/local
# whose node had long since moved under a version manager, where a current
# copy sat unused. `which` answers "the first one", and the first one is the
# wrong question. Ask "the newest one" and remember what it said.

_BIN_DIRS = ("~/.local/bin", "~/bin", "/usr/local/bin", "/opt/homebrew/bin",
             "~/.claude/local", "~/.npm-global/bin", "~/.volta/bin", "~/.asdf/shims")
_BIN_GLOBS = ("~/.local/share/fnm/node-versions/*/installation/bin",
              "~/.fnm/node-versions/*/installation/bin",
              "~/.nvm/versions/node/*/bin", "~/.local/share/mise/installs/node/*/bin")


def binary_candidates(name, path=None):
    """Every executable called `name` this machine has, PATH first, deduplicated."""
    import glob as _glob
    dirs = [d for d in (path or os.environ.get("PATH", "")).split(os.pathsep) if d]
    dirs += [os.path.expanduser(d) for d in settings.get(None, "binaries.extra_dirs")]
    dirs += [os.path.expanduser(d) for d in _BIN_DIRS]
    for g in _BIN_GLOBS:
        dirs += sorted(_glob.glob(os.path.expanduser(g)), reverse=True)
    exts = [""] + (os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";") if os.name == "nt" else [])
    seen, out = set(), []
    for d in dirs:
        for ext in exts:
            cand = os.path.join(d, name + ext.lower()) if ext else os.path.join(d, name)
            if not (os.path.isfile(cand) and os.access(cand, os.X_OK)):
                continue
            real = os.path.realpath(cand)
            if real in seen:
                continue
            seen.add(real)
            out.append(cand)
    return out


def binary_version(path):
    """`path --version`, cached by (path, mtime) so a wake does not pay for it twice."""
    cache_p = os.path.join(HOME, ".ao", "binaries.json")
    try:
        cache = json.load(open(cache_p, encoding=UTF8))
    except (OSError, ValueError):
        cache = {}
    try:
        mtime = os.path.getmtime(os.path.realpath(path))
    except OSError:
        return ""
    ent = cache.get(path)
    if ent and ent.get("mtime") == mtime:
        return ent.get("version", "")
    out = ""
    try:
        # cwd=HOME: a version probe started from inside a repository would inherit
        # that cwd and read, for one cycle, as a turn running in it.
        r = subprocess.run([path, "--version"], capture_output=True, text=True, encoding=UTF8, errors="replace", timeout=25, cwd=HOME,
                           env=dict(os.environ, PATH=os.environ.get("PATH", "") + ":" +
                                    os.path.dirname(os.path.realpath(path)) + ":" + os.path.dirname(path)))
        m = re.search(r"(\d+\.\d+\.\d+)", (r.stdout or "") + (r.stderr or ""))
        out = m.group(1) if m else ""
    except Exception:
        out = ""
    cache[path] = {"mtime": mtime, "version": out, "at": int(time.time())}
    try:
        os.makedirs(os.path.dirname(cache_p), exist_ok=True)
        json.dump(cache, open(cache_p, "w", encoding=UTF8))
    except OSError:
        pass
    return out


def _vtuple(v):
    return tuple(int(x) for x in v.split(".")) if v else (0,)


def resolve_binary(name, path=None):
    """(path, version) of the newest `name` on this machine; (None, "") if none.

    An absolute path is returned as-is with its version. Ties keep PATH order.
    """
    if os.path.isabs(name):
        return (name if os.access(name, os.X_OK) else None), binary_version(name) if os.path.exists(name) else ""
    best = (None, "")
    for cand in binary_candidates(name, path):
        v = binary_version(cand)
        if best[0] is None or _vtuple(v) > _vtuple(best[1]):
            best = (cand, v)
    return best


# ---- implementer reports ------------------------------------------------------

def _report_summary(path):
    try:
        for line in open(path, errors="replace", encoding=UTF8):
            if line.startswith("#"):
                return line.lstrip("# ").strip()
    except OSError:
        pass
    return ""


def bump_repeat(path):
    """Fold a repeated report into the standing one; keep its mtime (its age is the fact)."""
    try:
        st = os.stat(path)
        body = open(path, errors="replace", encoding=UTF8).read()
    except OSError:
        return 0
    m = re.search(r"^Tekrar: (\d+)", body, re.M)
    n = int(m.group(1)) + 1 if m else 2
    line = f"Tekrar: {n} · son: {time.strftime('%Y-%m-%d %H:%M')}"
    body = re.sub(r"^Tekrar: .*$", line, body, flags=re.M) if m else body.rstrip("\n") + "\n\n" + line + "\n"
    try:
        open(path, "w", encoding=UTF8).write(body)
        os.utime(path, (st.st_atime, st.st_mtime))
    except OSError:
        pass
    return n


def _name_time(name):
    m = re.match(r"(\d{8})-(\d{4})", name)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M"))
    except ValueError:
        return None


def implementer_inbox(root, cfg):
    """Mail addressed to the implementer: not its own reports, not the watchdog's."""
    box = cfg.get("mailbox", "agent-mail")
    return [m for m in mailbox(root, box) if not to_architect(m, cfg) and not from_watchdog(m)]


def product_dirty(root, cfg):
    """Uncommitted paths outside the coordination directories."""
    out = []
    # Not sh(): it strips its output, and the first line's leading status column
    # went with it - " M .ao/ledger/notices.jsonl" became "M .ao/...", the path
    # lost its first character, and a coordination file read as a product change.
    # A failure still reads as no uncommitted paths, as it did through sh(); no
    # caller decides authority from this.
    try:
        status = _git_output(root, "status", "--porcelain").decode(UTF8, "replace")
    except Exception:
        status = ""
    for line in status.split("\n"):
        if not line.strip():
            continue
        raw = line[3:].strip().strip('"')
        paths = [part.strip().strip('"') for part in raw.split(" -> ")]
        # A rename crossing the boundary is a product change; ignore it only
        # when both its old and new names are coordination state.
        if paths and all(_is_coordination_path(path, cfg) for path in paths):
            continue
        out.append(line)
    return out


def waiting_on_architect(root, cfg):
    """The implementer's standing request the architect has not answered.

    Returns (report, asked_at) when the newest implementer report asks for a
    decision, nothing addressed to the implementer arrived after it, and the
    board has nothing queued. Nudging in that state produces reports, not
    progress: eighty of them, once, at eight-minute intervals.
    """
    box = cfg.get("mailbox", "agent-mail")
    files = mailbox(root, box)
    reports = [m for m in files if to_architect(m, cfg) and not from_watchdog(m)
               and not from_architect(m, cfg)]
    if not reports:
        return None
    latest = reports[-1]
    p = os.path.join(root, box, latest)
    try:
        low = open(p, errors="replace", encoding=UTF8).read(4000).lower()
    except OSError:
        return None
    if not any(h in low for h in ("## karar gerekli", "## acil", "## decision required",
                                   "## urgent", "## blocked")):
        return None
    # Delivery is by deletion, so any file addressed to the implementer is
    # unread — whatever its timestamp. An answer written while the implementer
    # was repeating its request is older than the repeat and still the answer.
    if implementer_inbox(root, cfg):
        return None
    if board(root)["queued"]:
        return None
    return latest, _name_time(latest) or os.path.getmtime(p)


# ---- mail envelope, names, ledger --------------------------------------------

def mail_names(cfg):
    """(implementer, architect) mail names; defaults keep the historical files valid."""
    impl = settings.get(cfg, "implementer.name").strip()
    arch = settings.get(cfg, "architect.name").strip()
    return impl, arch


def to_architect(name, cfg):
    _, arch = mail_names(cfg)
    return f"-to-{arch}-" in name or "-to-fable-" in name or "-to-architect-" in name


def from_architect(name, cfg):
    """Whether a mail was written by the architect, read from its sender field (#18)."""
    _, arch = mail_names(cfg)
    found = re.match(r"^\d{8}-\d{4}-(.+?)-to-", name)
    return bool(found) and found.group(1).lower() in {arch.lower(), "fable", "architect"}


def notice_recently_recorded(root, key, window):
    """Was this key recorded inside the window at all, delivered or held (#69)?

    An architect-audience notice is recorded unsent by design, so a check on sent
    rows never held for one, and the same anomaly wrote a row every cycle.
    """
    p = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    if not os.path.exists(p):
        return False
    cutoff = time.time() - window
    try:
        with open(p, errors="replace", encoding=UTF8) as fh:
            fh.seek(max(0, os.path.getsize(p) - 100_000))
            lines = fh.read().split("\n")
    except OSError:
        return False
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("at", 0) < cutoff:
            return False
        if rec.get("key") == key:
            return True
    return False


SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9_-]{16,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}",
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    r"[A-Za-z0-9+_=-]{40,}",
)


# What ao writes into a repository is scanned for credentials first (#48). Named rules,
# so a redaction says what it removed; hex digests (commit ids, sha256 values) match
# none of them, which is why the generic long-token rule `redact` uses is not here.
EVIDENCE_RULES = (
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("anthropic-key", r"sk-ant-[A-Za-z0-9_-]{16,}"),
    ("openai-key", r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    ("github-token", r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("slack-token", r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    ("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ("bearer-token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    ("assigned-secret", r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token)\b[\"']?\s*[:=]\s*[\"']?"
                        r"[A-Za-z0-9+/_.=-]{12,}"),
)


def scan_evidence(text):
    """(text with every credential-shaped string replaced by `[redacted:<rule>]`, the rules that hit)."""
    out = str(text if text is not None else "")
    hits = []
    for rule, pattern in EVIDENCE_RULES:
        out, count = re.subn(pattern, f"[redacted:{rule}]", out)
        if count:
            hits.append(rule)
    return out, hits


def scan_record(value):
    """A JSON-shaped value with every string in it scanned (#48)."""
    if isinstance(value, str):
        return scan_evidence(value)[0]
    if isinstance(value, dict):
        return {key: scan_record(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scan_record(item) for item in value]
    return value


def redact(text):
    """Text from an agent's own output with anything token-shaped masked (#69).

    A wake or nudge log merges a model's prose with its process's errors, and a
    line of it went to the committed notices ledger, Telegram and e-mail. What
    leaves the log is masked first.
    """
    out = str(text or "")
    for pattern in SECRET_PATTERNS:
        out = re.sub(pattern, "[redacted]", out)
    return out


def record_actor_flags(root, actor, flags, reason):
    """Record a flag ao adds to an actor's argv, with why, each time that changes (#69)."""
    from .storage import append_jsonl, read_jsonl
    path = os.path.join(root, ".ao", "ledger", "actor-flags.jsonl")
    try:
        rows = read_jsonl(path)
    except Exception:
        rows = []
    last = next((row for row in reversed(rows)
                 if isinstance(row, dict) and row.get("actor") == actor), None)
    if last and last.get("flags") == list(flags) and last.get("reason") == reason:
        return last
    return append_jsonl(path, {"at": int(time.time()), "actor": actor,
                               "flags": list(flags), "reason": reason})


def from_watchdog(name):
    return name.startswith("watchdog-to-") or "-watchdog-to-" in name


def write_mail(root, cfg, name, body, meta=None):
    """Every mail ao itself writes goes through here: envelope first, prose after.

    The body stays Markdown — agents and people read it. The envelope is a small
    front-matter block a program can read without parsing prose: kind, from, to,
    slice, time, id. And every write is mirrored to `.ao/ledger/mail.jsonl`, so
    that delivery-by-deletion stops deleting the record: a mailbox that was
    emptied is still searchable, and "how long did that request stand" has an
    answer after the file is gone.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    os.makedirs(box, exist_ok=True)
    meta = dict(meta or {})
    meta.setdefault("ao", 1)
    meta.setdefault("id", name)
    meta.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    order = ["ao", "id", "kind", "from", "to", "slice", "at"]
    keys = [k for k in order if k in meta] + [k for k in meta if k not in order]
    meta = scan_record(meta)
    body = scan_evidence(body)[0]                     # scanned before it is written (#48)
    head = "---\n" + "".join(f"{k}: {meta[k]}\n" for k in keys if meta[k] not in (None, "")) + "---\n"
    with open(os.path.join(box, name), "w", encoding=UTF8) as fh:
        fh.write(head + body.lstrip("\n"))
    summary = next((l.lstrip("# ").strip() for l in body.split("\n") if l.startswith("#")), "")
    mail_ledger_append(root, {"event": "written", "id": name, "kind": meta.get("kind"),
                              "from": meta.get("from"), "to": meta.get("to"),
                              "slice": meta.get("slice"), "summary": summary[:200]})
    return name


def mail_meta(path):
    """The envelope of a mail file, or {} for a file written without one."""
    try:
        head = open(path, errors="replace", encoding=UTF8).read(2000)
    except OSError:
        return {}
    if not head.startswith("---\n"):
        return {}
    end = head.find("\n---", 4)
    if end < 0:
        return {}
    out = {}
    for line in head[4:end].split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def mail_ledger_append(root, row):
    d = os.path.join(root, ".ao", "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        row = dict(row)
        row.setdefault("at", int(time.time()))
        with open(os.path.join(d, "mail.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def mail_log(root, limit=200):
    p = os.path.join(root, ".ao", "ledger", "mail.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-limit:]


def reconcile_mail_ledger(root, cfg):
    """Mail the ledger saw written and the mailbox no longer has was consumed.

    The architect and the implementer delete by hand; nothing hooks `rm`. So
    the watchdog closes the loop each cycle: an open id that is not on disk gets
    a `consumed` row, timed now — a lower bound on when it was read.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    rows = mail_log(root, 5000)
    open_ids = {}
    for r in rows:
        if r.get("event") == "written":
            open_ids[r["id"]] = r
        elif r.get("event") == "consumed":
            open_ids.pop(r["id"], None)
    closed = []
    for mid, r in open_ids.items():
        if not os.path.exists(os.path.join(box, mid)):
            mail_ledger_append(root, {"event": "consumed", "id": mid, "kind": r.get("kind"),
                                      "from": r.get("from"), "to": r.get("to"),
                                      "stood_s": int(time.time()) - int(r.get("at") or time.time())})
            closed.append(mid)
    return closed


def mail_search(root, text, limit=50):
    """Ledger rows and live files whose id, summary or body mention `text`."""
    t = text.lower()
    out, seen = [], set()
    for r in reversed(mail_log(root, 5000)):
        if r.get("event") != "written":
            continue
        hay = " ".join(str(r.get(k) or "") for k in ("id", "summary", "kind", "slice")).lower()
        if t in hay and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
        if len(out) >= limit:
            return out
    return out


# ---- alarms -------------------------------------------------------------------
#
# Three levels, named by who acts: yellow is the architect's (an anomaly and a
# wake), orange is the human's (desktop + Telegram), red is the human's *now*
# (e-mail). The ladder is what turns a standing orange into a red: on 2026-09-05
# every alert went to a notification centre nobody looked at for eleven hours.

# The defaults, from the settings registry (#74); the functions below read the values in force.
ALARM_RED_AFTER = settings.default("alarms.red_after_minutes") * 60
ALARM_RED_REPEAT = settings.default("alarms.red_repeat_hours") * 3600
ALARM_RESET_AFTER = settings.default("alarms.reset_after_hours") * 3600


def _alarm_reset_after():
    return settings.get(None, "alarms.reset_after_hours") * 3600


def alarms_path():
    return os.path.join(HOME, ".ao", "alarms.json")


def load_alarms():
    try:
        return json.load(open(alarms_path(), encoding=UTF8))
    except (OSError, ValueError):
        return {}


def save_alarms(d):
    try:
        os.makedirs(os.path.dirname(alarms_path()), exist_ok=True)
        json.dump(d, open(alarms_path(), "w", encoding=UTF8), indent=1)
    except OSError:
        pass


def alarm_snoozes_path():
    return os.path.join(HOME, ".ao", "alarm-snoozes.json")


def load_alarm_snoozes():
    try:
        return json.load(open(alarm_snoozes_path(), encoding=UTF8))
    except (OSError, ValueError):
        return {}


def _save_alarm_snoozes(d):
    os.makedirs(os.path.dirname(alarm_snoozes_path()), exist_ok=True)
    with open(alarm_snoozes_path(), "w", encoding=UTF8) as fh:
        json.dump(d, fh, indent=1)


def alarm_snooze(project, key, until, by="human", why=""):
    """Keep one alarm off the human channels until a date; it stays on the record.

    A snooze is for a condition that is real, known and waiting on someone. On
    2026-09-15 the doctor check reported Voltrai's legacy commit hook, whose fix
    only the owner can make and not before 1 October; left alone it would ring red
    and mail every six hours about something nobody could act on yet. The snooze
    names who set it and why, and it ends by itself on the date.
    """
    d = load_alarm_snoozes()
    d[f"{project}:{key}"] = {"until": int(until), "by": by, "why": why, "at": int(time.time())}
    _save_alarm_snoozes(d)
    return d[f"{project}:{key}"]


def alarm_unsnooze(project, key):
    d = load_alarm_snoozes()
    gone = d.pop(f"{project}:{key}", None)
    if gone is not None:
        _save_alarm_snoozes(d)
    return gone


def alarm_snoozed(project, key, now=None):
    """The snooze standing for this alarm, or None; an expired snooze is no snooze."""
    entry = load_alarm_snoozes().get(f"{project}:{key}")
    if isinstance(entry, dict) and float(entry.get("until") or 0) > (now or time.time()):
        return entry
    return None


def alarm_touch(project, key, level, now=None, red_after=ALARM_RED_AFTER, title=None,
                persist=True, quiet_until=None, evidence=None):
    """Calculate a raise of `key` at `level`; return (level to ring at, episode).

    An orange raised repeatedly for `red_after` seconds rings red. `red_due` on
    the episode says whether a mail should go now (once per ALARM_RED_REPEAT).
    ``persist=False`` runs the identical calculation against the current ledger
    without writing it, so watchdog explain can preview the live verdict safely.
    """
    now = now or time.time()
    d = load_alarms()
    k = f"{project}:{key}"
    e = d.get(k) or {}
    if e and now - e.get("last", 0) > _alarm_reset_after():
        e = {}
    e.setdefault("first", now)
    e["last"] = now
    e["level"] = level
    e["count"] = e.get("count", 0) + 1
    if title:
        e["title"] = title
    if quiet_until:
        e["quiet_until"] = float(quiet_until)
    if evidence:
        e["evidence"] = evidence      # the ladder shows what the notice was raised on (#37)
    ring = level
    e["red_due"] = False
    if level == "red" or (level == "orange" and now - e["first"] >= red_after):
        ring = "red"
        e["red_due"] = e.get("red_sent") is None or now - e["red_sent"] >= settings.get(None, "alarms.red_repeat_hours") * 3600
        # A standing red with a known end is mailed once, then held until that end (#40).
        if e.get("red_sent") is not None and now < float(e.get("quiet_until") or 0):
            e["red_due"] = False
    e["ring"] = ring
    d[k] = e
    if persist:
        save_alarms(d)
    return ring, e


def alarm_mailed(project, key, now=None):
    d = load_alarms()
    k = f"{project}:{key}"
    if k in d:
        d[k]["red_sent"] = now or time.time()
        d[k]["red_due"] = False
        save_alarms(d)


def active_alarms(project=None, now=None):
    now = now or time.time()
    out = []
    for k, e in load_alarms().items():
        proj, _, key = k.partition(":")
        if project and proj != project:
            continue
        if now - e.get("last", 0) > _alarm_reset_after():
            continue
        out.append(dict(e, project=proj, key=key, age_s=int(now - e.get("first", now))))
    return sorted(out, key=lambda e: -e["age_s"])


# ---- heartbeat ------------------------------------------------------------------

# ---- one name for what a project keeps outside its tree (#66) -----------------------

PROJECT_KEY_FILE = "project-key"


def project_registry_path():
    """Which path holds which project name on this machine."""
    return os.environ.get("AO_PROJECT_REGISTRY") or os.path.join(HOME, ".ao", "projects.json")


def project_registry():
    try:
        with open(project_registry_path(), encoding=UTF8) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: row for name, row in data.items() if isinstance(row, dict) and row.get("root")}


def _usable_key(name):
    return (isinstance(name, str) and 0 < len(name) <= 200 and name not in (".", "..")
            and not any(ch in name for ch in "/\\\0\n\r"))


def project_key(root):
    """The name a project's files outside its tree are kept under (#66).

    Push windows, watchdog state and logs, heartbeats, locks, the reviewer and
    helper records and alarms were named by the directory's basename, so two
    checkouts called `api` shared them: a push allowed in one opened the other,
    and one project's watchdog state overwrote the other's.

    A project's name belongs to its resolved path in the machine registry. A new
    project takes its basename - the name its files already had, so an existing
    install keeps them - unless a path that is still an ao project holds that
    name, compared without case because launchd labels are lower-cased; then it
    takes the basename and eight hex digits of its resolved path. The name is
    also written to `.ao/project-key` with the path, so a lost registry gives
    each project its own name back and a copied `.ao/` does not claim another's.
    A directory with no `.ao/` is not an ao project: it gets its basename and
    nothing is written.
    """
    base = os.path.basename(os.path.abspath(root).rstrip("/\\")) or "root"
    real = os.path.realpath(root)
    if not os.path.isdir(os.path.join(real, ".ao")):
        return base
    mine = next((name for name, row in project_registry().items() if row.get("root") == real), None)
    if mine:
        return mine
    from .storage import _exclusive_lock, replace_file_durably
    registry = project_registry_path()
    recorded = os.path.join(real, ".ao", PROJECT_KEY_FILE)
    try:
        with _exclusive_lock(registry + ".lock"):
            known = project_registry()
            mine = next((name for name, row in known.items() if row.get("root") == real), None)
            if mine is None:
                held = {name.lower() for name, row in known.items()
                        if row.get("root") != real and os.path.isdir(os.path.join(row["root"], ".ao"))}
                try:
                    with open(recorded, encoding=UTF8) as fh:
                        mark = json.load(fh)
                except (OSError, ValueError):
                    mark = {}
                wanted = mark.get("key") if isinstance(mark, dict) and mark.get("root") == real else None
                if _usable_key(wanted) and wanted.lower() not in held:
                    mine = wanted
                elif base.lower() not in held:
                    mine = base
                else:
                    mine = f"{base}-{hashlib.sha256(real.encode('utf-8')).hexdigest()[:8]}"
                known = {name: row for name, row in known.items() if name.lower() != mine.lower()}
                known[mine] = {"root": real, "at": int(time.time())}
                replace_file_durably(registry, json.dumps(known, indent=1, sort_keys=True).encode("utf-8"))
            replace_file_durably(recorded, json.dumps({"key": mine, "root": real}).encode("utf-8"))
    except OSError:
        return mine or base
    return mine


def project_key_collisions():
    """Registered projects that share a directory name, and the name each is kept under."""
    groups = {}
    for name, row in sorted(project_registry().items()):
        base = os.path.basename(str(row["root"]).rstrip("/\\")).lower()
        groups.setdefault(base, []).append((name, row["root"]))
    return {base: rows for base, rows in groups.items() if len(rows) > 1}


def heartbeat_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"heartbeat-{key}")


def heartbeat(root):
    """The watchdog touching this each cycle is the only proof it is alive."""
    try:
        p = heartbeat_path(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding=UTF8) as fh:
            fh.write(str(int(time.time())))
    except OSError:
        pass


def heartbeat_age(root):
    try:
        return int(time.time() - os.path.getmtime(heartbeat_path(root)))
    except OSError:
        return None


def expire_alarms(project, now=None):
    """Episodes that went quiet are over; return them once and forget them."""
    now = now or time.time()
    d = load_alarms()
    done = []
    for k in list(d):
        proj, _, key = k.partition(":")
        e = d[k]
        if proj == project and now - e.get("last", 0) > _alarm_reset_after():
            done.append(dict(e, project=proj, key=key, age_s=int(e.get("last", now) - e.get("first", now))))
            del d[k]
    if done:
        save_alarms(d)
    return done


RETIRED_HEARTBEAT_AGE = settings.default("heartbeat.retired_days") * 86400


def stale_siblings(root, max_age=900):
    """Other projects whose watchdog once had a heartbeat and now has none.

    A dead watchdog cannot report itself; the ones next to it can. Only projects
    with a heartbeat file count — a project that never ran one is not late."""
    me = project_key(root)
    out = {}
    try:
        for f in os.listdir(os.path.join(HOME, ".ao")):
            if not f.startswith("heartbeat-") or f == f"heartbeat-{me}":
                continue
            age = int(time.time() - os.path.getmtime(os.path.join(HOME, ".ao", f)))
            # A week of silence is a project that was retired, not a watchdog that
            # just died; ringing a person about it every cycle never ended (audit).
            if max_age < age <= settings.get(None, "heartbeat.retired_days") * 86400:
                out[f[len("heartbeat-"):]] = age
    except OSError:
        pass
    return out


# ---- review scope ----------------------------------------------------------------

COORDINATION_DIRS = (".ao/", "agent-mail/", ".kiro/", ".claude/", ".codex/")


def review_diff(root, cfg, paths=None, budget=1_500_000):
    """What the reviewer sees: the slice's changes, and nothing else.

    `git diff HEAD` carried every tracked change in the tree, so an unrelated
    steering edit polluted a review of a credential lifecycle; untracked files
    were capped at the first twenty, and thirty review artefacts consumed the
    cap before the one untracked file that mattered — the inventory under
    review — was reached. Two rounds of a five-round budget went to that.

    Coordination directories are never product. Untracked product files are
    included newest first within a byte budget. `paths` narrows both sides.
    """
    skip = _coordination_dirs(cfg)
    spec = " -- " + " ".join(f"'{p}'" for p in paths) if paths else ""
    diff = sh(f"git diff HEAD{spec}", cwd=root, timeout=60) or ""
    if not paths:
        parts = re.split(r"(?m)^(?=diff --git )", diff)
        diff = "".join(pt for pt in parts if not any(
            f" b/{d}" in pt.split("\n", 1)[0] for d in skip))
    untracked = [l[3:].strip().strip('"') for l in (sh("git status --porcelain", cwd=root) or "").split("\n")
                 if l.startswith("?? ")]
    untracked = [f for f in untracked if not _is_coordination_path(f, cfg)]
    if paths:
        untracked = [f for f in untracked if any(f == p or f.startswith(p.rstrip("/") + "/")
                                                  or p.startswith(f.rstrip("/") + "/") for p in paths)]
    files = []
    for f in untracked:
        p = os.path.join(root, f)
        if os.path.isdir(p):
            for dp, _, fn in os.walk(p):
                for x in fn:
                    rel = os.path.relpath(os.path.join(dp, x), root).replace(os.sep, "/")
                    if _is_coordination_path(rel, cfg):
                        continue
                    if not paths or any(rel == q or rel.startswith(q.rstrip("/") + "/") for q in paths):
                        files.append(rel)
        elif os.path.isfile(p):
            files.append(f)
    files.sort(key=lambda f: -os.path.getmtime(os.path.join(root, f)))
    included, used = [], 0
    for f in files:
        p = os.path.join(root, f)
        try:
            size = os.path.getsize(p)
            if size > 400_000 or used + size > budget:
                continue
            raw = open(p, "rb").read()
            if b"\0" in raw[:4000]:
                continue
            diff += f"\n--- NEW FILE {f} ---\n" + raw.decode("utf-8", "replace")
            used += size
            included.append(f)
        except OSError:
            pass
    return diff, included


# ---- helpers: processes ao starts that are not writers -------------------------

def helpers_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"helpers-{key}.json")


def _helpers_update(root, change):
    """Change the helper registry under its lock and replace it whole (#68).

    It was read, changed and written back unlocked, and the read path wrote too, so
    a status call overwrote a helper registered a moment before and the watchdog
    counted a running reviewer as a second writer.
    """
    from .storage import _exclusive_lock, replace_file_durably
    path = helpers_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _exclusive_lock(path + ".lock"):
        try:
            with open(path, encoding=UTF8) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        change(d)
        replace_file_durably(path, json.dumps(d).encode(UTF8))


def helper_register(root, pid, what):
    """A reviewer, a probe: started by ao inside the repo, never a writer.

    Bind the declaration to process start identity. PID existence alone is not
    identity: after reuse it would exclude an unrelated process indefinitely.
    """
    start = _process_start(pid, refresh=True)

    def change(d):
        # Dead, reused or unprovable entries go here, under the lock, never on a read.
        for key in list(d):
            recorded = d.get(key) if isinstance(d.get(key), dict) else {}
            try:
                current = _process_start(int(key))
            except (TypeError, ValueError):
                current = None
            if current is None or current != recorded.get("start"):
                d.pop(key, None)
        d[str(pid)] = {"what": what, "at": int(time.time()), "start": start}

    try:
        _helpers_update(root, change)
    except OSError:
        pass


def helper_release(root, pid):
    try:
        _helpers_update(root, lambda d: d.pop(str(pid), None))
    except OSError:
        pass


def helper_pids(root, what=None):
    """Registered helpers still running as the process that registered; reading writes nothing."""
    try:
        with open(helpers_path(root), encoding=UTF8) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return set()
    if not isinstance(d, dict):
        return set()
    live = set()
    for key, recorded in d.items():
        try:
            pid = int(key)
        except (TypeError, ValueError):
            continue
        recorded = recorded if isinstance(recorded, dict) else {}
        current_start = _process_start(pid)
        # Dead, reused, or legacy/unprovable pid: never let declaration alone
        # exclude a process. Conservative writer checks may count it once.
        if current_start is not None and recorded.get("start") == current_start:
            if what is None or recorded.get("what") == what:
                live.add(pid)
    return live


# ---- cost: what the coordination itself spends ----------------------------------
#
# "How much quota does ao cost, and is it bureaucracy?" is answerable only from
# the transcript. Every turn is classified by what it did — wrote product, ran
# the review/gate ceremony, only coordinated, or read and thought — and its
# spend is summed per class, so the overhead is a number and not an opinion.

_PRODUCT_PATH = re.compile(r"(^|/)(src|lib|app|apps|test|tests|spec|fixtures|evidence|docs|plugins|site|"
                           r"package\.json|pyproject\.toml|tsconfig)")
_COORD_PATH = re.compile(r"(^|/)(agent-mail|\.ao|semantic-review|\.kiro|\.claude)(/|$)")


def turn_costs(cfg, since=None):
    """Per-turn cost and class from the implementer's transcript.

    Returns {"unit", "turns": [ {start, usage, cls, tool_calls, product_writes,
    reviews, commits, blocked_report} ], "by_class": {cls: {turns, usage}},
    "ao_commands": Counter, "total"}. Classes: product (wrote product files or
    committed), ceremony (review / verify / lock / commit-ok, nothing written),
    coordination (only inbox/report/writers/board, few calls), analysis (read and
    reasoned, wrote nothing).
    """
    import collections
    msgs, _ = session_paths(cfg)
    out = {"unit": "credit", "turns": [], "by_class": {}, "ao_commands": collections.Counter(), "total": 0.0}
    if not msgs or not os.path.exists(msgs):
        return out
    recs = read_tail(msgs, 400_000_000)
    cur = None

    def ts(d):
        raw = d.get("timestamp", "")
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    for d in recs:
        pl = d.get("payload") or d.get("message") or {}
        t = pl.get("type") or d.get("type")
        if t == "turn_start" or (t == "user" and cur is None) or (t == "user" and cur and cur.get("closed")):
            cur = {"start": ts(d), "usage": 0.0, "product_writes": 0, "coord_writes": 0, "tool_calls": 0,
                   "reviews": 0, "commits": 0, "blocked_report": False, "ao": collections.Counter()}
            out["turns"].append(cur)
            continue
        if cur is None:
            continue
        if t == "usage_summary":
            for ps in pl.get("promptTurnSummaries") or []:
                try:
                    cur["usage"] += float(ps.get("usage") or 0)
                    out["unit"] = ps.get("unit") or out["unit"]
                except (TypeError, ValueError):
                    pass
        elif t == "assistant" and isinstance(pl.get("usage"), dict):      # claude-code transcripts
            u = pl["usage"]
            cur["usage"] += (u.get("input_tokens", 0) + u.get("output_tokens", 0)) / 1000.0
            out["unit"] = "ktok"
        elif t == "turn_end":
            cur["closed"] = True
        elif t == "tool_call":
            cur["tool_calls"] += 1
            name = str(pl.get("toolName") or pl.get("name") or "")
            args = pl.get("args") or pl.get("input") or {}
            text = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
            if name.endswith("ao_report") and "blocked" in text:      # MCP clients prefix tool names
                cur["blocked_report"] = True
            if "write" in name.lower() or name in ("fs_write", "Edit", "Write", "MultiEdit"):
                path = str(args.get("path") or args.get("file_path") or "") if isinstance(args, dict) else ""
                if _COORD_PATH.search(path):
                    cur["coord_writes"] += 1
                elif _PRODUCT_PATH.search(path) or (path and "/" in path):
                    cur["product_writes"] += 1
            m = re.search(r"\bao\s+(?:-C\s+\S+\s+)?([a-z][a-z-]+)", text)
            if m:
                cur["ao"][m.group(1)] += 1
                out["ao_commands"][m.group(1)] += 1
            if re.search(r"\bao\s+(?:-C\s+\S+\s+)?review\b", text):
                cur["reviews"] += 1
            if re.search(r"git\s+commit\b", text):
                cur["commits"] += 1
    for tn in out["turns"]:
        if since and (not tn["start"] or tn["start"] < since):
            tn["cls"] = None
            continue
        if tn["product_writes"] or tn["commits"]:
            cls = "product"
        elif tn["reviews"] or any(c in tn["ao"] for c in ("verify", "lock", "commit-ok")):
            cls = "ceremony"
        elif tn["blocked_report"] or (tn["tool_calls"] <= 8 and (tn["ao"] or tn["coord_writes"])):
            cls = "coordination"
        else:
            cls = "analysis"
        tn["cls"] = cls
        b = out["by_class"].setdefault(cls, {"turns": 0, "usage": 0.0, "wasted": 0, "wasted_usage": 0.0})
        b["turns"] += 1
        b["usage"] += tn["usage"]
        if tn["blocked_report"] and not tn["product_writes"]:
            b["wasted"] += 1
            b["wasted_usage"] += tn["usage"]
        out["total"] += tn["usage"]
    return out


# ---- deferred work: what could not run, so it can run later ----------------------
#
# A quota window closing must not lose anything. Every action ao could not take
# — a nudge, a wake, a review — is written down with why, and `ao catchup`
# replays the queue when the way is clear. This is the "bypass now, reconcile
# later" the human asked for: the run degrades, it never forgets.

def deferred_append(root, kind, **fields):
    """Defer one kind of work; an open deferral of that kind is the one that stands (#87).

    A wake is state, not a queue of attempts: fourteen deferred wakes piled up over
    one outage and replayed in a burst when it ended.
    """
    standing = next((row for row in deferred_open(root) if row.get("kind") == kind), None)
    if standing:
        return standing
    d = os.path.join(root, ".ao", "ledger")
    rec = {"event": "deferred", "id": f"DF-{int(time.time())}-{kind}", "kind": kind, "at": int(time.time())}
    rec.update(fields)
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "deferred.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def deferred_close(root, did, outcome="done"):
    d = os.path.join(root, ".ao", "ledger")
    try:
        with open(os.path.join(d, "deferred.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps({"event": "closed", "id": did, "at": int(time.time()), "outcome": outcome}) + "\n")
    except OSError:
        pass


def deferred_open(root):
    p = os.path.join(root, ".ao", "ledger", "deferred.jsonl")
    if not os.path.exists(p):
        return []
    rows, closed = {}, set()
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "deferred":
            rows[r["id"]] = r
        elif r.get("event") == "closed":
            closed.add(r["id"])
    return [r for i, r in rows.items() if i not in closed]


def recently_deferred(root, kind, within=3600):
    cut = time.time() - within
    return any(r["kind"] == kind and r["at"] >= cut for r in deferred_open(root))


# ---- waivers: the human's bypass, on the record ---------------------------------

WAIVER_CHAIN = "ao-waiver-row-v1"
WAIVER_HOURS_DEFAULT = settings.default("waivers.default_hours")
WAIVER_HOURS_MAX = settings.default("waivers.max_hours")


def waivers_path(root):
    return os.path.join(root, ".ao", "ledger", "waivers.jsonl")


def waiver_rows(root):
    """Every waiver row once the chain validates (#67).

    Rows written before the ledger was chained may stand first. An unlinked row
    after them, or a broken link, makes the ledger unreadable, and every reader
    fails closed.
    """
    from .storage import read_chained_jsonl
    return read_chained_jsonl(waivers_path(root), WAIVER_CHAIN, legacy_prefix=True)


def _login_and_terminal():
    """The login a command ran under and whether a terminal was attached."""
    try:
        import getpass
        user = getpass.getuser()
    except Exception:
        user = None
    try:
        interactive = os.isatty(0)
    except OSError:
        interactive = False
    return user, bool(interactive)


def waive(root, gate, slice_id, why, by, hours=WAIVER_HOURS_DEFAULT):
    """Record that a person waived a gate for one slice, for a bounded time (#67).

    A chained append like an authority row. It carries an expiry, the name the
    person gave, and beside it what ao can check: the login and whether a terminal
    was attached.
    """
    from .storage import append_chained_jsonl
    now = int(time.time())
    taken = {row.get("id") for row in waiver_rows(root) if isinstance(row, dict)}
    wid, n = f"W-{now}", 2
    while wid in taken:
        wid, n = f"W-{now}-{n}", n + 1
    user, interactive = _login_and_terminal()
    record = {"event": "waived", "id": wid, "gate": gate, "slice": slice_id, "why": why,
              "by": by, "at": now, "expires": now + int(float(hours) * 3600),
              "user": user, "interactive": interactive,
              "head": sh("git rev-parse HEAD", cwd=root), "tree": tree_digest(root)}
    return append_chained_jsonl(waivers_path(root), record, WAIVER_CHAIN, legacy_prefix=True)


def close_waiver(root, wid, outcome):
    """Retire a waiver with a durable chained append; a failure raises rather than leaving it open unseen (#67)."""
    from .storage import append_chained_jsonl
    return append_chained_jsonl(
        waivers_path(root),
        {"event": "closed", "id": wid, "at": int(time.time()), "outcome": outcome},
        WAIVER_CHAIN, legacy_prefix=True,
    )


def open_waivers(root, gate=None, slice_id=None):
    rows, closed = {}, set()
    for r in waiver_rows(root):
        if not isinstance(r, dict):
            continue
        if r.get("event") == "waived" and r.get("id"):
            rows[r["id"]] = r
        elif r.get("event") == "closed":
            closed.add(r.get("id"))
    out = [r for i, r in rows.items() if i not in closed]
    if gate:
        out = [r for r in out if r.get("gate") == gate]
    if slice_id:
        out = [r for r in out if r.get("slice") in (slice_id, "*")]
    return out


def _waiver_candidates(root):
    """Candidate digests each waiver has already authorised, from the grants that used it."""
    bound = {}
    for row in authority_rows(root):
        if isinstance(row, dict) and row.get("granted") is True and row.get("waiver"):
            bound.setdefault(row["waiver"], set()).add((row.get("candidate") or {}).get("digest"))
    return bound


def review_waiver_for(root, running, candidate_digest, now=None):
    """The review waiver that may stand in for a review of this candidate, and why others may not (#67).

    It names a running slice exactly, has not expired, and has authorised no other
    candidate. A waiver for every slice, one from before waivers expired, and one
    already spent on other bytes stand in for nothing.
    """
    now = time.time() if now is None else now
    bound = _waiver_candidates(root)
    notes = []
    for waiver in reversed(open_waivers(root, gate="review")):
        wid, slice_id = waiver.get("id"), waiver.get("slice")
        if slice_id not in running:
            if slice_id == "*":
                notes.append(f"waiver {wid} names every slice; a waiver covers one")
            continue
        expires = waiver.get("expires")
        if not isinstance(expires, (int, float)) or isinstance(expires, bool):
            notes.append(f"waiver {wid} for {slice_id} predates waiver expiry; a person opens a new one")
            continue
        if expires <= now:
            notes.append(f"waiver {wid} for {slice_id} expired "
                         f"{time.strftime('%d %b %H:%M', time.localtime(expires))}")
            continue
        used = bound.get(wid, set())
        if used and candidate_digest not in used:
            notes.append(f"waiver {wid} already authorised other bytes; it covers one candidate")
            continue
        return waiver, notes
    return None, notes


def open_waiver_report(root, now=None):
    """One line per open waiver with its age and expiry, for `ao doctor` (#67)."""
    now = time.time() if now is None else now
    lines = []
    for waiver in open_waivers(root):
        age = max(0, int(now - (waiver.get("at") or now)))
        expires = waiver.get("expires")
        state = ("no expiry (legacy)" if not isinstance(expires, (int, float))
                 else "expired" if expires <= now
                 else f"expires in {int((expires - now) // 3600)}h")
        lines.append(f"{waiver.get('id')} {waiver.get('gate')} for {waiver.get('slice')}, "
                     f"open {age // 86400}d {age % 86400 // 3600}h, {state}, by {waiver.get('by')}")
    return lines


_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _bound_waiver_target(root, waiver, trees):
    """The one landed commit a bounded waiver covers, found by the tree granted under it (#17).

    A waiver that nothing was granted under covers no commit; one whose granted tree
    never landed is UNRESOLVED rather than reviewed by guess.
    """
    item = {"waiver": waiver, "start": "", "end": "", "landed": 0,
            "newest": False, "problem": None, "unused": not trees,
            "expired": (waiver.get("expires") or 0) <= time.time()}
    if not trees:
        return item
    try:
        log = _git_output(root, "log", "--max-count=500", "--format=%H %T", "HEAD", "--").decode("ascii")
    except (RuntimeError, UnicodeError):
        item["problem"] = "UNRESOLVED: git cannot list the landed commits"
        return item
    sha = next((parts[0] for parts in (line.split() for line in log.splitlines())
                if len(parts) == 2 and parts[1] in trees), None)
    if not sha:
        item["problem"] = "UNRESOLVED: no landed commit carries the tree granted under it"
        return item
    try:
        parent = _git_output(root, "rev-parse", "--verify", f"{sha}^").decode("ascii").strip()
    except (RuntimeError, UnicodeError):
        item["problem"] = "UNRESOLVED: its landed commit has no parent to review against"
        return item
    item.update(start=parent, end=sha, landed=1)
    return item


def review_waiver_ranges(root):
    """Each open review waiver with the commits that landed while it was the newest.

    A waiver records the HEAD it was opened on and nothing else, so reviewing
    head..HEAD for every open waiver reviews each later slice again under the
    first slice's boundary: with one waiver per slice, N waivers become N
    overlapping reviews and the earliest carries every slice after it. A slice's
    commits run from its waiver's head to the head of the next review waiver
    opened after it, open or closed, and the newest waiver runs to HEAD.

    Heads come from a ledger file anyone on the machine can edit, so each is
    required to be a full object id and git runs without a shell.

    Returns dicts: waiver, start, end, landed (commit count), newest, problem.
    """
    waived, closed = [], set()
    for r in waiver_rows(root):
        if not isinstance(r, dict):
            continue
        if r.get("event") == "waived" and r.get("gate") == "review":
            waived.append(r)
        elif r.get("event") == "closed":
            closed.add(r.get("id"))
    if not waived:
        return []
    granted = {}
    for row in authority_rows(root):
        if isinstance(row, dict) and row.get("granted") is True and row.get("waiver"):
            granted.setdefault(row["waiver"], set()).add((row.get("candidate") or {}).get("index_tree"))
    try:
        current = _git_output(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    except (RuntimeError, UnicodeError):
        current = None
    out = []
    for i, w in enumerate(waived):
        if w.get("id") in closed:
            continue
        if "expires" in w:
            out.append(_bound_waiver_target(root, w, granted.get(w["id"], set())))
            continue
        later = waived[i + 1] if i + 1 < len(waived) else None
        start = str(w.get("head") or "")
        end = str(later.get("head") or "") if later else (current or "")
        item = {"waiver": w, "start": start, "end": end, "landed": 0,
                "newest": later is None, "problem": None}
        if not _OBJECT_ID.fullmatch(start):
            item["problem"] = "its recorded head is not a full object id"
        elif not _OBJECT_ID.fullmatch(end):
            item["problem"] = ("the next waiver's recorded head is not a full object id"
                               if later else "HEAD cannot be resolved")
        else:
            try:
                ancestor = subprocess.run(
                    [git_binary(), "merge-base", "--is-ancestor", start, end], cwd=root,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
                ).returncode
            except (OSError, subprocess.TimeoutExpired):
                ancestor = 2
            if ancestor == 1:
                item["problem"] = "its head is not an ancestor of the range end; history was rewritten"
            elif ancestor:
                item["problem"] = "git cannot compare its head with the range end"
            else:
                try:
                    item["landed"] = int(_git_output(root, "rev-list", "--count", f"{start}..{end}").strip() or 0)
                except (RuntimeError, ValueError):
                    item["problem"] = "git cannot count the commits in its range"
        out.append(item)
    return out


# ---- credits: burn rate and the day the work stops -------------------------------

def credit_account(profile):
    """A stable name for the account a reading came from; the profile itself is not kept (#36)."""
    if not profile:
        return None
    return "acct-" + hashlib.sha256(str(profile).encode("utf-8")).hexdigest()[:12]


def record_credit_sample(root, used, limit, reset_at=None, account=None, at=None):
    d = os.path.join(root, ".ao", "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "credits.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps({"at": int(at if at is not None else time.time()), "used": float(used),
                                 "limit": float(limit), "reset_at": reset_at,
                                 "account": account}) + "\n")
    except OSError:
        pass


def credit_samples(root, limit=500):
    p = os.path.join(root, ".ao", "ledger", "credits.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-limit:]


def burn_rate(root, window=72 * 3600, now=None):
    """From the samples: credits per day, days left, and whether the plan runs out
    before it resets. None when there are not two samples a few hours apart."""
    now = now or time.time()
    rows = [r for r in credit_samples(root) if r["at"] >= now - window]
    # One account's series only (#36). An owner who switched accounts left the old
    # account's cumulative `used` in the ledger, and the projection ran across both:
    # exhaustion declared against an account that was 4% used. A reading that does
    # not say whose it is projects nothing.
    account = rows[-1].get("account") if rows else None
    if not account:
        return None
    rows = [r for r in rows if r.get("account") == account]
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    span = last["at"] - first["at"]
    if span < 3 * 3600:
        return None
    per_day = max(0.0, (last["used"] - first["used"]) / span * 86400)
    remaining = max(0.0, last["limit"] - last["used"])
    days_left = remaining / per_day if per_day > 0 else None
    exhausts_at = now + days_left * 86400 if days_left is not None else None
    reset_at = last.get("reset_at")
    before_reset = bool(exhausts_at and reset_at and exhausts_at < reset_at)
    return {"per_day": per_day, "remaining": remaining, "days_left": days_left,
            "exhausts_at": exhausts_at, "reset_at": reset_at, "before_reset": before_reset,
            "used": last["used"], "limit": last["limit"], "account": account,
            "samples": [first, last]}


# ---- external ping: the dead man's switch --------------------------------------------

def pings_path():
    return os.path.join(HOME, ".ao", "pings.json")


def ping_url(root):
    try:
        d = json.load(open(pings_path(), encoding=UTF8))
    except (OSError, ValueError):
        return None
    key = project_key(root)
    return d.get(key) or d.get("*")


def set_ping_url(root, url, all_projects=False):
    try:
        d = json.load(open(pings_path(), encoding=UTF8))
    except (OSError, ValueError):
        d = {}
    d["*" if all_projects else project_key(root)] = url
    os.makedirs(os.path.dirname(pings_path()), exist_ok=True)
    json.dump(d, open(pings_path(), "w", encoding=UTF8), indent=2)
    os.chmod(pings_path(), 0o600)
    return d


def ping(root, opener=None):
    """One GET to the project's ping URL. A service that expects it every N
    minutes alarms when it stops — which is the only way anyone learns that both
    the watchdog and its doctor job have died."""
    url = ping_url(root)
    if not url:
        return None
    import urllib.request
    try:
        (opener or urllib.request.urlopen)(url, timeout=5)
        return True
    except Exception:
        return False


# ---- architect lock: one judge at a time --------------------------------------------

def architect_lock_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"architect-{key}.lock")


def architect_lock_holder(root):
    try:
        d = json.load(open(architect_lock_path(root), encoding=UTF8))
    except (OSError, ValueError):
        return None
    if d.get("pid") and not _pid_alive(int(d["pid"])):
        return None                                        # stale: holder died
    if time.time() - d.get("at", 0) > 3 * 3600:
        return None                                        # stale: forgotten
    return d


def acquire_architect(root, pid, who):
    """Take the lock unless a live holder has it. Two architect copies deciding at
    once — a desktop resume beside a watchdog wake — contradict each other."""
    holder = architect_lock_holder(root)
    if holder and holder.get("pid") != pid:
        return None
    d = {"pid": pid, "who": who, "at": int(time.time())}
    try:
        os.makedirs(os.path.dirname(architect_lock_path(root)), exist_ok=True)
        json.dump(d, open(architect_lock_path(root), "w", encoding=UTF8))
    except OSError:
        pass
    return d


def release_architect(root, pid=None):
    try:
        d = json.load(open(architect_lock_path(root), encoding=UTF8))
        if pid is None or d.get("pid") == pid:
            os.remove(architect_lock_path(root))
    except (OSError, ValueError):
        pass


# ---- foreign edits: a person in the same files -------------------------------------

def implementer_recent_writes(cfg, minutes=15):
    """Paths the implementer's tools wrote in the last N minutes, from its transcript."""
    msgs, _ = session_paths(cfg)
    if not msgs or not os.path.exists(msgs):
        return set()
    cut = time.time() - minutes * 60
    out = set()
    for d in read_tail(msgs, 3_000_000):
        pl = d.get("payload") or {}
        if pl.get("type") != "tool_call":
            continue
        try:
            import datetime as _dt
            at = _dt.datetime.fromisoformat(d.get("timestamp", "").replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if at < cut:
            continue
        args = pl.get("args") or {}
        path = args.get("path") or args.get("file_path") if isinstance(args, dict) else None
        if path:
            out.add(os.path.realpath(str(path)))
    return out


def foreign_edits(root, cfg, minutes=15):
    """Product files changed in the last N minutes that the implementer did not write.

    `ao writers` sees agents; it cannot see a person in an editor. This is the
    nearest thing: a dirty product file whose mtime is recent and which the
    implementer's own tool calls never touched. Named in the nudge so the
    implementer keeps away from it."""
    mine = implementer_recent_writes(cfg, minutes)
    cut = time.time() - minutes * 60
    out = []
    for line in product_dirty(root, cfg):
        rel = line[3:].strip().strip('"')
        p = os.path.join(root, rel)
        files = []
        if os.path.isdir(p):                                    # git lists an untracked dir as one entry
            for dp, _, fn in os.walk(p):
                files += [os.path.join(dp, x) for x in fn]
        elif os.path.isfile(p):
            files = [p]
        for f in files:
            try:
                if os.path.getmtime(f) >= cut and os.path.realpath(f) not in mine:
                    out.append(os.path.relpath(f, root).replace(os.sep, "/"))     # git's slashes, everywhere
            except OSError:
                pass
    return sorted(out)


# ---- fleet reserve: the machine's shared windows ----------------------------------

def fleet_reserve():
    return settings.get(None, "fleet.window_reserve_pct")


def window_headroom(provider="claude"):
    """(left_pct, reserve_pct) or (None, reserve) when the window is unreadable."""
    w = provider_window(provider)
    return (100 - w["pct"]) if w else None, fleet_reserve()


def turn_ended(cfg):
    """Has the implementer's transcript closed its last turn?

    A process that is alive after its transcript wrote `turn_end` is not
    working; it is a runtime that forgot to exit. Waiting the full silence
    threshold for it (three times the idle window) cost twenty minutes per
    occurrence. The transcript's own word is enough to reap at the idle window."""
    msgs, _ = session_paths(cfg)
    if not msgs or not os.path.exists(msgs):
        return False
    tail = read_tail(msgs, 200_000)
    for d in reversed(tail):
        t = (d.get("payload") or {}).get("type") or d.get("type")
        if t in ("session_metadata", "usage_summary", "session_event"):
            continue                                        # bookkeeping after the turn
        return t in ("turn_end", "result")
    return False
