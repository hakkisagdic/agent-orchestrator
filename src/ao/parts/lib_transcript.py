"""Sessions and transcripts: discovery, stores, progress and anomalies read from a transcript.

A part of src/ao/lib.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


# ── session discovery ─────────────────────────────────────────────────────────

_PACKAGE_ADAPTERS = {}


def package_adapters():
    """{id: adapter} for the adapters in the package alone (#76).

    Whatever decides authority - which directories are coordination rather than
    product, which processes are agents - reads only these. A user's or a project's
    adapter layer is writable by the agents it describes, and must not be able to
    move a product path out of review by declaring it a harness's directory. Read
    once per state of the directory: a path check runs this for every changed path.
    """
    directory = adapters_dir()
    try:
        key = (directory, os.stat(directory).st_mtime_ns)
        names = sorted(os.listdir(directory))
    except OSError:
        return {}
    if key in _PACKAGE_ADAPTERS:
        return _PACKAGE_ADAPTERS[key]
    found = {}
    for name in names:
        if not name.endswith(".json") or name in DATA_FILES:
            continue
        try:
            with open(os.path.join(directory, name), encoding=UTF8) as fh:
                adapter = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(adapter, dict):
            found[str(adapter.get("id") or name[:-5])] = adapter
    _PACKAGE_ADAPTERS.clear()
    _PACKAGE_ADAPTERS[key] = found
    return found


def _home_path(template):
    """A declared path with a leading ~ resolved against ao's HOME, so every reader of it agrees."""
    text = str(template or "")
    if text == "~":
        return HOME
    if text.startswith("~/"):
        return os.path.join(HOME, *text[2:].split("/"))
    return text


def session_stores(kind):
    """[(adapter id, store)] for each adapter whose `sessions` store is of this kind (#76)."""
    return [(ident, adapter["sessions"]) for ident, adapter in sorted(package_adapters().items())
            if isinstance(adapter.get("sessions"), dict) and adapter["sessions"].get("kind") == kind]


def _workspace_sessions(store):
    """Every session a workspace-meta store holds: its id, workspace directory, workspaces and transcript age."""
    base = _home_path(store.get("dir"))
    if not os.path.isdir(base):
        return
    for ws in os.listdir(base):
        wsd = os.path.join(base, ws)
        if not os.path.isdir(wsd):
            continue
        for sess in os.listdir(wsd):
            meta = os.path.join(wsd, sess, store["meta"])
            msgs = os.path.join(wsd, sess, store["transcript"])
            if not (os.path.exists(meta) and os.path.exists(msgs)):
                continue
            try:
                m = json.load(open(meta, encoding=UTF8))
                mtime = os.path.getmtime(msgs)
            except Exception:
                continue
            yield {"session": sess, "workspace_hash": ws, "paths": m.get(store["workspaces"]) or [], "mtime": mtime,
                   "title": m.get(store.get("title") or "title", ""), "status": m.get(store.get("status") or "status", "")}


def discover_session(cwd):
    """Find the most recently active local agent session whose workspace is cwd.

    A store that keeps each session's metadata with its workspace paths (an adapter's
    `sessions` of kind workspace-meta) is enough to resolve the opaque per-workspace
    directory without asking the vendor CLI.
    """
    best = None
    for ident, store in session_stores("workspace-meta"):
        for row in _workspace_sessions(store):
            if cwd in row["paths"] and (best is None or row["mtime"] > best["_mtime"]):
                best = {"adapter": ident, "session": row["session"], "workspace_hash": row["workspace_hash"],
                        "cwd": cwd, "_mtime": row["mtime"]}
    return best


def all_workspaces():
    """Every local agent session grouped by the workspace it belongs to.

    Lets `ao` answer "which projects can I watch?" without any configuration —
    the vendor stores already record their own workspace paths.
    """
    found = {}
    for ident, store in session_stores("workspace-meta"):
        for row in _workspace_sessions(store):
            for path in row["paths"]:
                cur = found.get(path)
                if cur is None or row["mtime"] > cur["mtime"]:
                    found[path] = {"path": path, "mtime": row["mtime"], "session": row["session"],
                                   "workspace_hash": row["workspace_hash"], "adapter": ident,
                                   "title": row["title"], "status": row["status"]}
    return sorted(found.values(), key=lambda r: r["mtime"], reverse=True)


def session_paths(cfg):
    """Transcript and metadata paths for the configured implementer.

    This was hard-wired to one harness's store, which made the watchdog work for
    that harness only: an implementer on another resolved to no transcript, the
    watchdog said "nothing to watch" and quietly never ran. Every adapter declares
    its store (`sessions`); an implementer whose adapter declares none has no paths.
    """
    impl = cfg.get("implementer") or {}
    sess = impl.get("session")
    if not sess:
        return None, None
    store = load_adapter(impl.get("adapter") or "").get("sessions") or {}
    if store.get("kind") == "escaped-cwd":
        cwd = impl.get("cwd") or cfg.get("root", "")
        return os.path.join(escaped_cwd_dir(store, cwd), store["transcript"].replace("{session}", sess)), None
    ws = impl.get("workspace_hash")
    if store.get("kind") != "workspace-meta" or not ws:
        return None, None
    d = os.path.join(_home_path(store["dir"]), ws, sess)
    return os.path.join(d, store["transcript"]), os.path.join(d, store["meta"])


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
    the CLI check still works on its own. Which tools and which binaries come from
    the vendor list and the adapters, never from a table here (#89).
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
    vendors = [vendor for vendor in vendor_list() if vendor.get("adapter")]
    alias = {str(surface).lower(): vendor["adapter"] for vendor in vendors for surface in vendor.get("surfaces") or []}
    named = {}
    for k, v in rows.items():
        named[alias.get(k, k)] = v
    catalog = adapter_catalog()
    for vendor in vendors:
        binaries = adapter_binaries((catalog.get(vendor["adapter"]) or {}).get("adapter"))
        if not binaries:
            continue
        found = next((binary for binary in binaries if _sh.which(binary)), None)
        entry = named.setdefault(vendor["adapter"], {})
        entry["installed"] = bool(found)
        entry["binary"] = found or binaries[0]
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
    names.update(agent_process_names())
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


def agent_process_names():
    """Program names that are agent turns, as the shipped adapters declare them (`detect.processes`) (#76).

    The package's adapters only: whether a process is a writer decides holds and
    nudges, and a layer an agent can write must not be able to hide one.
    """
    return {str(name) for adapter in package_adapters().values()
            for name in (adapter.get("detect") or {}).get("processes") or [] if name}


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
        # Observation is held to its bound as it is written (#50).
        bound_store(os.path.join(d, "notices.jsonl"), settings.get(load_config(root), "retention.observation_kb"))
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

    acct = account_usage()
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
    defaults = globals().get("COORDINATION_DIRS", (".ao/", "agent-mail/"))
    values = list(defaults) + list(harness_dirs()) + [
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
