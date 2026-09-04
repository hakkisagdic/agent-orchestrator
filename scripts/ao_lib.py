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

HOME = os.path.expanduser("~")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def load_config(root):
    """.ao/config.json — the docs show YAML for readability; the reference
    scripts read JSON so they stay dependency-free."""
    p = os.path.join(root, ".ao", "config.json")
    cfg = {}
    if os.path.exists(p):
        try:
            cfg = json.load(open(p))
        except Exception:
            cfg = {}
    cfg.setdefault("root", root)
    cfg.setdefault("mailbox", "agent-mail")
    cfg.setdefault("reviews", "semantic-review")
    if "implementer" not in cfg:
        found = discover_session(root)
        if found:
            cfg["implementer"] = found
    return cfg


def load_adapter(adapter_id):
    p = os.path.join(REPO, "adapters", f"{adapter_id}.json")
    return json.load(open(p)) if os.path.exists(p) else {}


# ── shell ─────────────────────────────────────────────────────────────────────

def sh(cmd, cwd=None, timeout=20):
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
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
                    m = json.load(open(meta))
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
                m = json.load(open(meta))
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
    impl = cfg.get("implementer") or {}
    ws, sess = impl.get("workspace_hash"), impl.get("session")
    if not (ws and sess):
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
            m = json.load(open(meta))
            status = m.get(
                (adapter.get("busy") or {}).get("status_field", "status"), "")
            desc = m.get(
                (adapter.get("busy") or {}).get("description_field", "description"), "") or ""
        except Exception:
            pass
    idle_s = (adapter.get("busy") or {}).get("idle_seconds", 240)
    running = status in ((adapter.get("busy") or {}).get("running_values") or ["in_progress"])
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
    for line in open(p, errors="replace"):
        line = line.rstrip()
        m = re.match(r"^##\s+([a-z]+)\s*$", line.strip())
        if m:
            state = m.group(1) if m.group(1) in out else None
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
        cfg = json.load(open(p))
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
    p = os.path.join(root, ".ao", "ledger", "plans.jsonl")
    out = {}
    if not os.path.exists(p):
        return out
    for line in open(p, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:
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
    d = os.path.join(root, ".ao", "ledger")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "plans.jsonl"), "a") as fh:
        fh.write(json.dumps({"item": item_id, "digest": digest,
                             "at": int(time.time())}) + "\n")


def board_append(root, state, line):
    """Append one item line under `## <state>`, creating the section if needed.

    Append rather than rewrite: the implementing agent edits this same file, and
    a full rewrite would silently drop whatever it wrote between our read and our
    write.
    """
    p = os.path.join(root, ".ao", "board.md")
    text = open(p).read() if os.path.exists(p) else "# Board\n"
    head = f"## {state}"
    if head not in text:
        text = text.rstrip("\n") + f"\n\n{head}\n"
    idx = text.index(head) + len(head)
    nl = text.index("\n", idx) + 1
    text = text[:nl] + line.rstrip() + "\n" + text[nl:]
    open(p, "w").write(text)


def last_nudge_error(root):
    """The most recent failed nudge, if the watchdog recorded one."""
    key = os.path.basename(root.rstrip("/")) or "root"
    try:
        st = json.load(open(os.path.join(HOME, ".ao", f"watchdog-{key}.json")))
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

def reviews(root, reviews_dir, limit=4):
    d = os.path.join(root, reviews_dir)
    if not os.path.isdir(d):
        return []
    files = sorted(os.listdir(d), key=lambda f: os.path.getmtime(os.path.join(d, f)),
                   reverse=True)[:limit]
    out = []
    for f in files:
        verdict = ""
        try:
            for line in open(os.path.join(d, f), errors="ignore"):
                if "Verdict" in line:
                    verdict = re.sub(r".*:\s*", "", line).replace("*", "").strip()
                    break
        except Exception:
            pass
        out.append((f, verdict))
    return out


def rounds(root, reviews_dir):
    """Consecutive NEEDS_CHANGES from the newest backwards — the round budget."""
    n = 0
    for _, v in reviews(root, reviews_dir, limit=50):
        if "APPROVED" in v.upper():
            break
        n += 1
    return n


def mailbox(root, mail_dir):
    d = os.path.join(root, mail_dir)
    if not os.path.isdir(d):
        return []
    return [f for f in sorted(os.listdir(d)) if f != "README.md" and f.endswith(".md")]


def git_state(root):
    return {
        "log": sh("git log --oneline -4", cwd=root).split("\n"),
        "dirty": [l for l in sh("git status --short", cwd=root).split("\n") if l.strip()],
        "ahead": sh("git rev-list --count origin/main..HEAD", cwd=root) or "0",
    }
