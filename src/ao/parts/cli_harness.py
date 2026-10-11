"""Harnesses: `ao harness probe`, which asks each harness that speaks ACP what it supports (ACP-PROBE), and
`ao harness seen`, where a person reads the harness configuration an agent changed (HARNESS-CONFIG).

A part of src/ao/cli.py, run in its namespace by `_part` as the parts moved out of it are (#44); it is
not importable on its own.
"""


def harness_profile_path(adapter_id):
    """Where what a harness said it supports is kept: ~/.ao/harness/<id>.json, the machine's, as its binaries are."""
    return os.path.join(A.HOME, ".ao", "harness", f"{adapter_id}.json")


def cmd_harness(cfg, args):
    """Ask each harness whose adapter says how to start it in ACP what it supports, and keep the answer (ACP-PROBE).

    Only an adapter's `acp.argv` is started, found as ao finds any program, and only the harnesses named,
    or every one whose adapter declares that command. Each is asked `initialize` and, with --sessions,
    `session/list` for this directory; it is sent no prompt and opens no session, so asking spends no quota.
    What it answers is kept in ~/.ao/harness/<id>.json with when it was asked and which binary answered: how
    many sessions it listed is kept, and nothing of what they are. A harness that is not installed is said
    to be so; one that does not answer in --timeout seconds is said not to, and the command exits 1.

    `ao harness seen` is a person's command of its own (_harness_seen).
    """
    from . import acp
    from .storage import replace_file_durably
    if getattr(args, "action", None) == "seen":
        return _harness_seen(cfg, args)
    if getattr(args, "by", None) or getattr(args, "digest", None):
        print("--by and --digest belong to ao harness seen, where a person records what they read; probe takes neither")
        return 2
    root = cfg.get("root") or os.getcwd()
    # Each adapter as ao loads it, from the package, the user's layer and the project's, a later one
    # overriding by id; one whose contract this ao does not implement is not loaded, as elsewhere.
    catalog = {adapter_id: entry["adapter"] for adapter_id, entry in A.adapter_catalog(cfg.get("root")).items()
               if not entry["problem"]}
    named = list(getattr(args, "ids", None) or [])
    unknown = [adapter_id for adapter_id in named if adapter_id not in catalog]
    if unknown:
        print(f"{C['red']}no adapter{C['reset']} {', '.join(unknown)}; `ao adapters` lists them")
        return 2
    silent = [adapter_id for adapter_id in named if not isinstance(catalog[adapter_id].get("acp"), dict)]
    if silent:
        print(f"{C['red']}no ACP command{C['reset']}: {', '.join(silent)} declares no `acp.argv`")
        return 2
    chosen = named or sorted(adapter_id for adapter_id, adapter in catalog.items()
                             if isinstance(adapter.get("acp"), dict))
    if not chosen:
        print(f"{C['dim']}No adapter says how to start its harness in ACP (`acp.argv`).{C['reset']}")
        return 1
    failed = 0
    for adapter_id in chosen:
        argv = [str(part) for part in catalog[adapter_id]["acp"].get("argv") or []]
        if not argv:
            failed += 1
            print(f"  {adapter_id:<12} {C['red']}no command{C['reset']}: its `acp.argv` is empty")
            continue
        path, version = A.resolve_binary(argv[0])
        if path is None:
            print(f"  {adapter_id:<12} {C['dim']}not installed: {argv[0]} is on no path ao searches{C['reset']}")
            continue
        try:
            found = acp.probe([path] + argv[1:], root, timeout=args.timeout, sessions=args.sessions)
        except acp.ProbeError as exc:
            failed += 1
            print(f"  {adapter_id:<12} {C['red']}no answer{C['reset']}: {exc}")
            continue
        profile = dict({"id": adapter_id, "measured_at": int(time.time()), "argv": argv, "binary": path,
                        "binary_version": version}, **found)
        target = harness_profile_path(adapter_id)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        replace_file_durably(target, (json.dumps(profile, indent=1, sort_keys=True) + "\n").encode(UTF8))
        agent = " ".join(part for part in (found["agent"].get("name"), found["agent"].get("version")) if part)
        print(f"  {adapter_id:<12} ACP {found['protocol_version']}  {agent or '(unnamed)'}  "
              f"load {'yes' if found['load_session'] else 'no'}  "
              f"session: {', '.join(found['session']) or '-'}  mcp: {', '.join(found['mcp']) or '-'}"
              + (f"  sessions here: {found['sessions']}" if "sessions" in found else ""))
    return 1 if failed else 0


# ---- what a harness reads from the tree, read by a person before anything ao starts here runs (HARNESS-CONFIG) ----

def _harness_change_lines(root, path, state, view):
    """The lines that show one change as it stands: its diff from HEAD where it is text (HARNESS-CONFIG)."""
    import difflib
    kind = view[0] if view else "deleted"
    if kind == "deleted":
        return ["deleted: HEAD holds it, and a harness started here reads none"]
    if kind == "special":
        return [f"{view[1]}, not a regular file: a harness that opens it reads whatever its writer sends"]
    if kind == "missing":
        return ["nothing: the link leads nowhere"]
    if kind == "link":
        target, inner = view[1], view[2]
        if inner is None:
            return [f"a link to the folder {A.shown_text(target)}: each file in it is a change of its own below"]
        return [f"a link to {A.shown_text(target)}, which holds:"] \
            + _harness_change_lines(root, path, "untracked", inner)
    data, size = view[1], view[2]
    if data is None:
        return [f"{size} bytes, more than ao shows here: read the whole file before you record it"]
    if b"\0" in data:
        return [f"{size} bytes, not text: read it with a tool that shows such a file before you record it"]
    before = b""
    if state in ("modified", "hidden"):
        try:
            before = A._git_output(root, "cat-file", "blob", f"HEAD:{path}")
        except RuntimeError:
            before = b""
    lines = list(difflib.unified_diff(before.decode(UTF8, "replace").splitlines(),
                                      data.decode(UTF8, "replace").splitlines(),
                                      fromfile=f"HEAD:{path}" if before else "nothing", tofile=path, lineterm=""))
    return [A.shown_text(line) for line in lines] or ["empty"]


def _harness_seen(cfg, args):
    """Show a person the harness configuration an agent changed in this tree, and record it as seen (HARNESS-CONFIG).

    What a harness reads from a project - hooks, plugins, MCP servers, instructions - acts in the next session started
    there, before any review has seen it, so no wake, refill, nudge, hunt or lane starts here while a change to it
    stands unseen. With --by alone each unseen change is shown as it stands, its diff from HEAD where it is text, with
    the digest of exactly those bytes, and nothing is recorded. With --by and that digest it is recorded as seen, kept
    in ~/.ao outside the tree; a change made since it was shown refuses the record, so what is recorded is what was
    read, as `ao person-review` records a verdict. It is a person's command, as `ao waive` is: --by names a person and
    never an agent or a role, the login and whether a terminal was attached are recorded beside the name, and no grant
    ao checks admits it.
    """
    root = cfg["root"]
    if getattr(args, "ids", None):
        print("ao harness seen names no adapter: it shows what every harness ao knows reads from this tree")
        return 2
    by = (getattr(args, "by", None) or "").strip()
    if not by or not by.isprintable() or len(by) > 80:
        print("--by is required: the person who reads the changes, in one line of at most 80 characters")
        return 2
    if by.lower() in _agent_names(cfg):
        print(f"--by names an agent or a role ({by}); seeing what a harness runs is a person's act")
        return 2
    shown = {}
    try:
        changes = A.project_config_changes(root, shown=shown)
    except Exception as exc:
        print(f"{C['red']}not read{C['reset']}: what a harness reads from this tree cannot be read "
              f"({' '.join(str(exc).split())}); nothing ao starts here runs until it can, and nothing was recorded")
        return 1
    if not changes:
        print(f"{C['green']}nothing a harness reads from this tree differs from HEAD{C['reset']}; nothing to record")
        return 0
    record = A.harness_seen_record(root) or {}
    unseen = A.harness_unseen(changes, record)
    if not unseen:
        when = time.strftime("%d %b %H:%M", time.localtime(float(record.get("at") or 0)))
        print(f"{C['green']}seen{C['reset']}: {record.get('by') or 'a person'} recorded each change to what a harness "
              f"reads here as it stands, on {when}: {A.harness_config_line(changes)}")
        return 0
    digest = A.harness_config_digest(unseen)
    wanted = (getattr(args, "digest", None) or "").strip()
    if wanted:
        if wanted != digest:
            print(f"{C['red']}refused{C['reset']}: what a harness reads here is not what that digest was taken of - it "
                  f"changed since it was shown, or the digest is another's. Nothing was recorded; "
                  f"`ao harness seen --by {by}` shows it as it stands now")
            return 2
        A.record_harness_seen(root, changes, by, digest)
        print(f"{C['green']}recorded{C['reset']}: {by} has seen {A.harness_config_line(unseen)}; a wake, refill, "
              "nudge, hunt or lane starts in this tree again")
        return 0
    print(f"{C['b']}harness configuration nobody has seen{C['reset']}: {len(unseen)} change(s) to what a harness "
          "reads from this tree, each as it stands now")
    for path, state, _ in unseen:
        print(f"\n{C['b']}{A.shown_text(path)}{C['reset']} ({A.HARNESS_CONFIG_STATES.get(state, state)})")
        for line in _harness_change_lines(root, path, state, shown.get(path)):
            print(f"  {line}")
    print(f"\ndigest {digest}")
    print(f"Nothing is recorded yet. Read each change above, then\n  ao harness seen --by {by} --digest {digest}\n"
          "records exactly these bytes as seen; a change made since refuses it.")
    return 0


# ---- a harness's lifecycle hooks: `ao agent-hook EVENT`, and `ao agents` (HOOK-SPOOL) ---------------------------

AGENT_HOOK_BYTES = 1 << 20          # a hook's input past this is not read
AGENT_HOOK_WAIT = 2.0               # seconds a hook's input may take to arrive


def _hook_input():
    """The JSON a hook hands its command on standard input, or {}: read for AGENT_HOOK_WAIT seconds at most, so
    a harness that leaves the stream open never holds its own turn on ao."""
    import threading
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    got = []

    def read():
        try:
            got.append(sys.stdin.read(AGENT_HOOK_BYTES + 1))
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(AGENT_HOOK_WAIT)
    raw = got[0] if got else ""
    if not raw or len(raw) > AGENT_HOOK_BYTES:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def cmd_agent_hook(cfg, args):
    """Record one lifecycle event a harness's hook hands ao in the machine's event log, and nothing else (HOOK-SPOOL).

    The hook names the event in ao's words (A.AGENT_EVENTS) and, with --harness, the adapter it is.
    From what the hook hands over on standard input ao keeps the session's id and directory, and a
    tool's name or a notification's type where the event is one: never a prompt, a tool's input or
    output, or a transcript. It writes nothing on standard output, which a harness may read as its
    own input, and always exits 0, so a hook can neither fail a turn nor hold it: an event ao does not
    know, or one from a directory that is no ao project, is not recorded, and nothing is said.
    """
    root = cfg.get("root")
    if args.event not in A.AGENT_EVENTS or not root or not os.path.isdir(os.path.join(root, ".ao")):
        return 0
    payload = _hook_input()

    def text(key, limit=256):
        value = payload.get(key)
        return value[:limit] if isinstance(value, str) and value.strip() else None

    A.emit_event(root, f"agent-{args.event}", {
        "harness": args.harness, "session": text("session_id", 128), "cwd": text("cwd", 1024),
        "tool": text("tool_name", 128) if args.event in ("tool", "tool-done") else None,
        "notice": text("notification_type", 64) if args.event == "notification" else None})
    return 0


def cmd_agents(cfg, args):
    """The sessions this project's harnesses' hooks have spoken of, and what each is doing now (HOOK-SPOOL)."""
    sessions = sorted(A.agent_sessions(cfg["root"]).values(), key=lambda s: s.get("at") or 0, reverse=True)
    if not sessions:
        print(f"{C['dim']}No harness has told ao of a session here through its hooks.{C['reset']}")
        return 0
    now = time.time()
    tone = {"working": C["green"], "waiting": C["yellow"], "idle": C["dim"], "ended": C["dim"]}
    for session in sessions:
        since = _elapsed(now - float(session.get("at") or now))
        print(f"  {tone.get(session['state'], '')}{session['state']:<8}{C['reset']} "
              f"{str(session.get('harness') or '?'):<12} {session['session'][:24]:<24} "
              f"{since:>8}  last: {session['event']}")
    return 0
