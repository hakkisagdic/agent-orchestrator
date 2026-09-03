"""Shared library for the agent-orchestrator reference scripts.

Standard library only, by design — see docs/surfaces.md. Nothing here writes to a
vendor's session store; observation is strictly read-only.
"""
import json
import os
import re
import subprocess
import time

HOME = os.path.expanduser("~")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

C = {
    "reset": "\033[0m", "dim": "\033[2m", "b": "\033[1m", "green": "\033[32m",
    "red": "\033[31m", "yellow": "\033[33m", "cyan": "\033[36m",
    "mag": "\033[35m", "blue": "\033[34m",
}


# ── config ────────────────────────────────────────────────────────────────────

def find_root(start=None):
    """Nearest ancestor containing .ao/, else the git root, else cwd."""
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(d, ".ao")):
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
        out.append((ts[11:16] or "--:--", kind, text))
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
