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
    p = os.path.join(adapters_dir(), f"{adapter_id}.json")
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
        escaped = cwd.replace("/", "-").replace(".", "-")
        d = os.path.join(HOME, ".claude", "projects", escaped)
        return os.path.join(d, sess + ".jsonl"), None
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
            m = json.load(open(meta))
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


def record_progress(root, cfg):
    """One line per watchdog check: what actually moved.

    Cheap by construction — the watchdog already runs, and this is a git call
    plus an append. The point is to have *history* of the artifacts, because a
    single snapshot cannot tell activity from progress.
    """
    msgs, _ = session_paths(cfg)
    porcelain = [l for l in sh("git status --porcelain", cwd=root).split("\n") if l.strip()]
    # Content churn, not file count. A slice deep in editing its established file
    # set holds the dirty *count* stable for many minutes — same eight files, new
    # content each cycle — and a count-only check reads that as frozen and cries
    # spin. The newest mtime across the changed set advances on every edit, so it
    # tells editing apart from a genuine stall. A long gate run writes nothing, so
    # it correctly stays frozen, and the minute threshold covers that case.
    churn = 0
    for l in porcelain:
        f = l[3:].split(" -> ")[-1].strip().strip('"')
        try:
            churn = max(churn, int(os.path.getmtime(os.path.join(root, f))))
        except OSError:
            pass
    rec = {"at": int(time.time()),
           "head": sh("git rev-parse --short HEAD", cwd=root),
           "dirty": len(porcelain), "churn": churn,
           "size": os.path.getsize(msgs) if msgs and os.path.exists(msgs) else 0}
    d = os.path.join(root, ".ao", "ledger")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "progress.jsonl")
    try:
        with open(p) as fh:
            last = fh.readlines()[-1:]
        if last:
            prev = json.loads(last[0])
            if (prev.get("head"), prev.get("dirty"), prev.get("churn"), prev.get("size")) == \
               (rec["head"], rec["dirty"], rec["churn"], rec["size"]):
                return                                # nothing changed; do not log noise
    except Exception:
        pass
    with open(p, "a") as fh:
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
    for line in open(p, errors="replace"):
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
        st = json.load(open(p))
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
    """
    names = set()
    for key in ("send", "resume"):
        argv = (adapter.get(key) or {}).get("argv") or []
        if argv:
            names.add(os.path.basename(argv[0]))
    names.update({"kiro-cli", "claude", "claude-code", "codex", "cursor-agent"})
    cands = set()
    for name in names:
        for pid in (sh(f"pgrep -f {name}") or "").split():
            if pid.isdigit() and int(pid) != os.getpid():
                cands.add(int(pid))
    out = []
    if cands:
        # One lsof for every candidate. A call per pid was fine at ten processes
        # and took over two minutes at a hundred and fifty — every helper of a
        # desktop app matches `pgrep -f claude` — and the implementer's writer
        # check timed out on it. -w drops warnings, -n/-P skip name lookups.
        want = os.path.realpath(root)
        cur = None
        pids = ",".join(str(p) for p in sorted(cands))
        for line in (sh(f"lsof -w -n -P -a -d cwd -Fpn -p {pids}") or "").split("\n"):
            if line.startswith("p") and line[1:].isdigit():
                cur = int(line[1:])
            elif line.startswith("n") and cur is not None:
                if os.path.realpath(line[1:]) == want:
                    out.append(cur)
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


def _is_headless(pid):
    """A turn started non-interactively (-p / --print / --no-interactive)."""
    args = sh(f"ps -o args= -p {pid}") or ""
    return any(f in args.split() for f in ("-p", "--print", "--no-interactive"))


def _proc_table():
    """pid -> (ppid, pgid, tty) for every process, from one ps call."""
    out = {}
    for line in (sh("ps -eo pid,ppid,pgid,tty") or "").split("\n")[1:]:
        f = line.split()
        if len(f) >= 4 and f[0].isdigit() and f[1].isdigit() and f[2].isdigit():
            out[int(f[0])] = (int(f[1]), int(f[2]), f[3])
    return out


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
    return process_trees(live), sorted(dead)


def kill_turn(pid, sig):
    """Signal a turn — the whole process group when this pid leads one.

    Signalling only the wrapper is how orphans are made: it exits, its runtime and
    engine children do not, and they keep the repository as their cwd. A turn we
    started is its own session, so its pid is its group id and one killpg reaches
    everything it spawned. A pid that leads no group is signalled alone.
    """
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
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def record_notice(root, title, msg, sent, key=None):
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
        with open(os.path.join(d, "notices.jsonl"), "a") as fh:
            fh.write(json.dumps({"at": int(time.time()), "title": title,
                                 "msg": msg, "sent": bool(sent),
                                 "key": key or title}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def notices(root, limit=10, include_suppressed=False):
    """Recent notifications, newest first."""
    p = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, errors="replace"):
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
        with open(p, errors="replace") as fh:
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
    # Quote the format: sh() runs through a shell, where a bare "|" in
    # --pretty=%h|%ct|%s is a pipe, and the commit list quietly came back empty.
    log = sh(f"git log --since='{int(since_days * 24)} hours ago' --pretty='%h|%ct|%s'",
             cwd=root) or ""
    out["commits"] = [dict(zip(("sha", "at", "subject"), l.split("|", 2)))
                      for l in log.split("\n") if l.count("|") >= 2]
    out["unpushed"] = int(sh("git rev-list --count @{u}..HEAD 2>/dev/null", cwd=root) or 0) \
        if sh("git rev-parse --abbrev-ref @{u} 2>/dev/null", cwd=root) else \
        len([l for l in (sh("git log --branches --not --remotes --pretty=%h", cwd=root) or "").split("\n") if l])

    def _jsonl(rel):
        p = os.path.join(root, rel)
        rows = []
        if os.path.exists(p):
            for line in open(p, errors="replace"):
                try:
                    r = json.loads(line)
                except Exception:
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
                    rows.append(r)
        return rows

    ver = _jsonl(".ao/ledger/verifications.jsonl")
    out["verifications"] = {"total": len(ver),
                            "passed": sum(1 for v in ver if v.get("passed")),
                            "failed": sum(1 for v in ver if not v.get("passed"))}
    auth = _jsonl(".ao/ledger/authority.jsonl")
    out["authority"] = {"granted": sum(1 for a in auth if a.get("granted")),
                        "refused": sum(1 for a in auth if not a.get("granted"))}
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
    out["reviews"] = {"total": len(fresh),
                      "approved": sum(1 for v in fresh if "APPROVED" in (v or "").upper()),
                      "changes": sum(1 for v in fresh if "APPROVED" not in (v or "").upper())}

    acct = kiro_account_usage()
    if acct and not acct.get("error"):
        out["credits"] = {"used": acct["used"], "limit": acct["limit"],
                          "remaining": acct["limit"] - acct["used"]}
    local = credit_usage()
    days = sorted(local.get("days", {}).items())
    out["credit_days"] = [(d, v) for d, v in days
                          if d >= time.strftime("%Y-%m-%d", time.localtime(cut))]
    return out


def work_fingerprint(root):
    """Everything that moves when work is happening, in one short string.

    A commit is one shape of progress, not the only one. A slice whose review
    found real defects withholds its commit *because it is behaving correctly*,
    and a HEAD-only progress check cannot tell that apart from an agent that
    died — so the backoff exhausted itself on a live slice and stood down for two
    hours. Count the working tree, the reviews and the decisions too: if any of
    them moved, something is being done.
    """
    parts = [sh("git rev-parse --short HEAD", cwd=root) or "",
             sh("git status --porcelain", cwd=root) or ""]
    for sub in ("semantic-review", ".ao/decisions", ".ao/ledger"):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            try:
                parts.append("|".join(sorted(
                    f"{f}:{int(os.path.getmtime(os.path.join(d, f)))}"
                    for f in os.listdir(d))))
            except OSError:
                pass
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def tree_digest(root):
    """A stable fingerprint of the working tree, tracked and untracked alike.

    `git status --porcelain` names what differs; hashing the diff plus the status
    line captures *what* it differs by. Together they answer the only question
    that matters when reusing a measurement: is this the same tree the numbers
    were taken from?
    """
    h = hashlib.sha256()
    h.update((sh("git rev-parse HEAD", cwd=root) or "").encode())
    h.update((sh("git status --porcelain", cwd=root) or "").encode())
    h.update((sh("git diff HEAD", cwd=root) or "").encode())
    return "sha256:" + h.hexdigest()


def latest_verification(root):
    """The newest `ao verify` record, or None."""
    p = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    if not os.path.exists(p):
        return None
    last = None
    for line in open(p, errors="replace"):
        try:
            last = json.loads(line)
        except Exception:
            continue
    return last


def record_authority(root, granted, reasons, tree, verification, token=None,
                     review=None, reviewer=None):
    """Every authority decision, granted or refused, with what it rested on."""
    d = os.path.join(root, ".ao", "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "authority.jsonl"), "a") as fh:
            # Keep the reviewer's identity here, not only in the review file.
            # That file is untracked and was wiped once already; the record of
            # what authority rested on has to outlive the evidence it cites.
            fh.write(json.dumps({"at": int(time.time()), "granted": bool(granted),
                                 "token": token, "reasons": reasons, "tree": tree,
                                 "verification": verification,
                                 "review": review, "reviewer": reviewer},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


GATE_LOCK = os.path.join(HOME, ".ao", "gate.lock")


def gate_lock_holder():
    """Which project is running its gates right now, if any."""
    if not os.path.exists(GATE_LOCK):
        return None
    try:
        st = json.load(open(GATE_LOCK))
    except Exception:
        return None
    try:
        os.kill(st.get("pid", -1), 0)             # stale lock from a killed run
    except OSError:
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


def process_trees(pids):
    """Group pids into independent trees — how many *turns*, not how many processes.

    One turn is several processes: a wrapper spawns a runtime which spawns
    children. Counting processes therefore reports four concurrent writers where
    there is one, and an alarm that fires on normal operation is an alarm people
    learn to ignore. Count only the roots: a pid whose parent is not itself in the
    set.
    """
    if not pids:
        return []
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
        if "-to-fable-" not in m and "-to-architect-" not in m:
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
        try:
            body = open(os.path.join(root, cfg.get("mailbox", "agent-mail"), m),
                        errors="replace").read(4000)
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
        g = groups.setdefault(kind, {"n": 0, "first": m})
        g["n"] += 1
        g["latest"] = m
        g["title"] = first.lstrip("# ").strip()[:200]
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
    budget = cfg.get("round_budget", 5)
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
        name = f"watchdog-to-fable-ANOMALY-{kind}{src}.md"
        path = os.path.join(box, name)
        if os.path.exists(path):
            return None
        with open(path, "w") as fh:
            fh.write(f"# ANOMALY — {kind}\n\n"
                     f"Watchdog observation at {time.strftime('%Y-%m-%d %H:%M:%S')}. "
                     f"Facts only; the watchdog draws no conclusion and took no action "
                     f"beyond standing down.\n\n")
            for f in facts:
                fh.write(f"- {f}\n")
            fh.write("\n## Asked of the architect\n\n"
                     "Decide whether this needs intervention, and what. If it is normal, "
                     "delete this message; if not, act and record what you did.\n")
        return name
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

    arn = ""
    prof = sh("kiro-cli whoami 2>/dev/null")
    for line in (prof or "").split("\n"):
        if line.strip().startswith("arn:aws:codewhisperer"):
            arn = line.strip()
    if not arn:
        return None

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
            with open(f, errors="replace") as fh:
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


def urgent_messages(root, cfg):
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
    """
    box = cfg.get("mailbox", "agent-mail")
    out = []
    for m in mailbox(root, box):
        if "-to-fable-" in m or "-to-architect-" in m:
            continue                       # outbound; not for the implementer
        try:
            body = open(os.path.join(root, box, m), errors="replace").read(8000)
        except OSError:
            continue
        upper = body.upper()
        if any(k.upper() in upper for k in URGENT_MARKERS):
            title = next((l for l in body.split("\n") if l.strip().startswith("# ")), m)
            out.append({"id": m, "title": title.lstrip("# ").strip()[:120], "body": body})
    return out


def discover_architect(cwd):
    """The newest Claude Code session for a directory, by transcript mtime.

    Pinning a session id in config goes stale the moment the human opens a new
    conversation, and a watchdog that wakes a dead session fails silently — the
    worst shape of failure, because everything still looks configured. Resolve it
    from disk instead, the same way the implementer's session is resolved.
    """
    # Claude Code flattens the path into a directory name by replacing both "/"
    # and "." — a worktree under ".claude" becomes "…Voltrai--claude-worktrees…",
    # with the doubled dash where "/." was.
    escaped = cwd.replace("/", "-").replace(".", "-")
    d = os.path.join(HOME, ".claude", "projects", escaped)
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


def architect_present(cwd, idle_seconds=600):
    """Is a human-driven architect session currently active?

    The two-writer rule applied to the architect's own session. Resuming it while
    it is live does not corrupt anything — Claude Code forks a copy — but it
    produces a second architect that inherits the first one's task and continues
    *that* instead of the triage it was woken for. Observed directly: a woken copy
    picked up the conversation in progress and reported on it rather than reading
    the anomaly queue.

    So wake only into absence. A transcript written recently means someone is
    already there and does not need a duplicate of themselves.
    """
    found = discover_architect(cwd)
    return bool(found and found["age"] < idle_seconds)


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
            rec = json.load(open(os.path.join(d, f)))
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
    rec = {"asked_at": int(time.time()), "question": question, "context": context,
           "slice": slice_id, "options": opts, "state": "open",
           "answer": None, "answered_at": None, "answered_by": None}
    json.dump(rec, open(os.path.join(d, did + ".json"), "w"),
              ensure_ascii=False, indent=2)
    rec["id"] = did
    return rec


def answer(root, did, key_or_text, by="human"):
    """Answer one question. Returns the updated record, or None if unknown."""
    p = os.path.join(root, DECISION_DIR, did + ".json")
    if not os.path.exists(p):
        return None
    rec = json.load(open(p))
    chosen = next((o for o in rec["options"] if o["key"] == key_or_text.strip().lower()), None)
    rec["answer"] = chosen["label"] if chosen and not chosen.get("free_text") \
        else key_or_text
    rec["answer_key"] = chosen["key"] if chosen else None
    rec["state"] = "answered"
    rec["answered_at"] = int(time.time())
    rec["answered_by"] = by
    json.dump(rec, open(p, "w"), ensure_ascii=False, indent=2)
    rec["id"] = did
    return rec


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
                # Case-insensitive on purpose. The implementer's reviews wrote
                # "**Verdict:**" and `ao review` writes "VERDICT:", and a match on
                # the capitalised form alone left every ao-review verdict empty —
                # so commit-ok could never accept the independent reviewer's
                # APPROVED, the one verdict it was built to require.
                if "verdict" in line.lower():
                    verdict = re.sub(r".*:\s*", "", line).replace("*", "").strip()
                    break
        except Exception:
            pass
        out.append((f, verdict))
    return out


def ready(root):
    """Queued items whose dependencies are done.

    A dependency graph, not a handoff mechanism. The valuable part of "backend
    done, now the frontend" is that the second item becomes eligible the moment
    the first lands — and *who* picks it up is a routing detail. Putting the edge
    on the board keeps the implementer from choosing its own scope, which is the
    one authority it must not hold; the board decides what is next.

    `needs: B3, B4` on a queued line is the edge. `unlocks:` is the same edge
    written from the other end, kept because people think in both directions.
    """
    b = board(root)
    done = {i["id"] for st in ("done", "verified") for i in b[st]}
    unlocked = set()
    for st in ("done", "verified"):
        for i in b[st]:
            for u in re.split(r"[,\s]+", i["notes"].get("unlocks", "")):
                if u:
                    unlocked.add(u)
    out = []
    for i in b["queued"]:
        needs = [n for n in re.split(r"[,\s]+", i["notes"].get("needs", "")) if n]
        # a `needs:` on a *queued* item is a dependency, not a human blocker
        missing = [n for n in needs if n not in done and not n.startswith("(")]
        if not missing or i["id"] in unlocked:
            out.append({**i, "role": i["notes"].get("role", "")})
    return out


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
            body = open(os.path.join(d, f), errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"^- \[(BLOCKER|HIGH|MEDIUM|LOW)\]\s*([^\s:]+)[:\d]*\s*[—-]\s*(.{0,60})",
                             body, re.M):
            key = (m.group(2), re.sub(r"\W+", " ", m.group(3).lower()).strip()[:40])
            seen.setdefault(key, {"sev": m.group(1), "count": 0, "reviews": []})
            seen[key]["count"] += 1
            seen[key]["reviews"].append(f)
    return [{"file": k[0], "clause": k[1], **v}
            for k, v in seen.items() if v["count"] >= min_repeats]


def note(root, cfg, to, title, body, urgent=False):
    """Write an architect message into the mailbox through the tool.

    So that a woken architect needs no raw Write or Edit to do its job. That
    matters because the one time it had them, it used them on the orchestrator's
    own source and built a runaway. The mailbox is the only thing an unattended
    architect should be able to write, and this is the only door to it.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    os.makedirs(box, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:40].strip("-") or "not"
    kind = "ACIL" if urgent else "DECISION"
    name = f"{time.strftime('%Y%m%d-%H%M')}-fable-to-{to}-{kind}-{slug}.md"
    with open(os.path.join(box, name), "w") as fh:
        fh.write(f"# {title}\n\n")
        if urgent:
            fh.write("## ACİL\n\n")
        fh.write(body.rstrip() + "\n")
    return name


def slice_started(root):
    """When the running slice began, from the board's `since:` note."""
    for it in board(root)["running"]:
        raw = it["notes"].get("since")
        if not raw:
            continue
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return time.mktime(time.strptime(raw.strip()[:len(time.strftime(fmt))], fmt))
            except ValueError:
                continue
    return None


def respecified_at(root):
    """When the architect last re-specified the running slice (`ao decide --scope <id>`)."""
    ids = {(it.get("id") or it.get("key") or "").strip() for it in board(root)["running"]}
    ids.discard("")
    p = os.path.join(root, ".ao", "ledger", "decisions.jsonl")
    if not ids or not os.path.exists(p):
        return None
    last = 0
    for line in open(p, errors="replace"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("by") == "architect" and (r.get("scope") or "").strip() in ids:
            last = max(last, int(r.get("at") or 0))
    return last or None


def rounds(root, reviews_dir):
    """Review rounds spent on the *current slice*.

    Counting back to the last APPROVED was wrong: a round budget is a statement
    about one slice, and reviews from a previous slice keep counting against the
    next one forever. Worse, rounds burned on environmental failure — a
    double-writer incident, a provider outage — are indistinguishable from rounds
    burned on the work, so the budget starts measuring the harness rather than the
    agent, and the guard that reads it stops nudging for the wrong reason.

    A slice declares its start on the board. Count reviews after that, and the
    counter resets exactly when a new slice begins — which is also when
    re-specification happens, so no separate reset mechanism is needed.
    """
    started = slice_started(root)
    # An architect decision scoped to the running slice re-specifies it, and a
    # re-specified slice has a fresh budget — that is what the decision says.
    # Reading it from the ledger rather than from the board's `since:` means the
    # rule holds even when the implementer never touched the board: it did not,
    # and an over-budget anomaly kept firing on rounds the decision had already
    # written off.
    respec = respecified_at(root)
    if respec and (not started or respec > started):
        started = respec
    n = 0
    for f, v in reviews(root, reviews_dir, limit=50):
        if "APPROVED" in v.upper():
            break
        if started:
            try:
                if os.path.getmtime(os.path.join(root, reviews_dir, f)) < started:
                    break
            except OSError:
                pass
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


# ---- fan-out budget --------------------------------------------------------
#
# A coordinator that fans out to sub-agents has no gate of its own. One ran 47
# verification agents at once: 11 finished, 36 died with "session limit", two
# million tokens were spent and the answer was mostly missing. The watchdog's
# quota guard could not have helped — it watches the implementer's pool, and the
# fan-out was the coordinator's. This is the gate that was missing: a hard cap,
# the provider window as keyflip reports it, and the project's own record of
# what fan-outs cost, so the estimate is empirical after the first one.

FANOUT_DEFAULTS = {"max_agents": 12, "per_agent_tokens": 50_000, "window_reserve_pct": 30}


def fanout_config(cfg):
    out = dict(FANOUT_DEFAULTS)
    out.update(cfg.get("fanout") or {})
    return out


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
    for line in open(p, errors="replace"):
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
    with open(os.path.join(d, "fanouts.jsonl"), "a") as fh:
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
    dirs = [d for d in (path or os.environ.get("PATH", "")).split(":") if d]
    dirs += [os.path.expanduser(d) for d in _BIN_DIRS]
    for g in _BIN_GLOBS:
        dirs += sorted(_glob.glob(os.path.expanduser(g)), reverse=True)
    seen, out = set(), []
    for d in dirs:
        cand = os.path.join(d, name)
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
        cache = json.load(open(cache_p))
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
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=25,
                           env=dict(os.environ, PATH=os.environ.get("PATH", "") + ":" +
                                    os.path.dirname(os.path.realpath(path)) + ":" + os.path.dirname(path)))
        m = re.search(r"(\d+\.\d+\.\d+)", (r.stdout or "") + (r.stderr or ""))
        out = m.group(1) if m else ""
    except Exception:
        out = ""
    cache[path] = {"mtime": mtime, "version": out, "at": int(time.time())}
    try:
        os.makedirs(os.path.dirname(cache_p), exist_ok=True)
        json.dump(cache, open(cache_p, "w"))
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
        for line in open(path, errors="replace"):
            if line.startswith("#"):
                return line.lstrip("# ").strip()
    except OSError:
        pass
    return ""


def bump_repeat(path):
    """Fold a repeated report into the standing one; keep its mtime (its age is the fact)."""
    try:
        st = os.stat(path)
        body = open(path, errors="replace").read()
    except OSError:
        return 0
    m = re.search(r"^Tekrar: (\d+)", body, re.M)
    n = int(m.group(1)) + 1 if m else 2
    line = f"Tekrar: {n} · son: {time.strftime('%Y-%m-%d %H:%M')}"
    body = re.sub(r"^Tekrar: .*$", line, body, flags=re.M) if m else body.rstrip("\n") + "\n\n" + line + "\n"
    try:
        open(path, "w").write(body)
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
    return [m for m in mailbox(root, box)
            if "-to-fable-" not in m and "-to-architect-" not in m
            and not m.startswith("watchdog-to-") and "-watchdog-to-" not in m]


def product_dirty(root, cfg):
    """Uncommitted paths outside the coordination directories."""
    skip = tuple(x.rstrip("/") + "/" for x in (cfg.get("mailbox", "agent-mail"),
                                                cfg.get("reviews", "semantic-review"), ".ao") if x)
    out = []
    for line in (sh("git status --porcelain", cwd=root) or "").split("\n"):
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        if path.startswith(skip):
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
    reports = [m for m in files if ("-to-fable-" in m or "-to-architect-" in m)
               and not m.startswith("watchdog-to-") and "-watchdog-to-" not in m]
    if not reports:
        return None
    latest = reports[-1]
    p = os.path.join(root, box, latest)
    try:
        low = open(p, errors="replace").read(4000).lower()
    except OSError:
        return None
    if not any(h in low for h in ("## karar gerekli", "## acil", "## decision required",
                                   "## urgent", "## blocked")):
        return None
    at = _name_time(latest) or os.path.getmtime(p)
    for m in implementer_inbox(root, cfg):
        if (_name_time(m) or os.path.getmtime(os.path.join(root, box, m))) > at:
            return None
    if board(root)["queued"]:
        return None
    return latest, at
