"""Harnesses: `ao harness probe`, which asks each harness that speaks ACP what it supports (ACP-PROBE), and
`ao harness seen`, where a person reads the harness configuration an agent changed (HARNESS-CONFIG).

A part of src/ao/cli.py, run in its namespace by `_part` as the parts moved out of it are (#44); it is
not importable on its own.
"""


def harness_profile_path(adapter_id):
    """Where what a harness said it supports is kept: ~/.ao/harness/<id>.json, the machine's, as its binaries are.

    The id names a file, so one that is not a plain name - a separator, a leading dot - is refused rather than
    followed out of that folder (PROBE-SHIPPED).
    """
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(adapter_id)):
        raise ValueError(f"{adapter_id!r} is no adapter id a harness profile is named by")
    return os.path.join(A.HOME, ".ao", "harness", f"{adapter_id}.json")


def cmd_harness(cfg, args):
    """Ask each harness whose adapter says how to start it in ACP what it supports, and keep the answer (ACP-PROBE).

    Only an adapter's `acp.argv` is started, found as ao finds any program, and only the harnesses named,
    or every one whose adapter declares that command. The adapter is one the package ships: an ACP command
    is a program ao starts, and a user's or a project's layer is a file the agents it describes can write, so
    the command such a layer declares is named and never started, as a turn's and a route's are not
    (TURN-ARGV, ROUTE-ARGV, PROBE-SHIPPED). Each is asked `initialize` and, with --sessions,
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
    if getattr(args, "by", None) or getattr(args, "digest", None) or getattr(args, "ask", False):
        print("--by, --digest and --ask belong to ao harness seen, where a person records what they read; probe takes "
              "none of them")
        return 2
    root = cfg.get("root") or os.getcwd()
    # Only the adapters the package ships; what a user's or a project's layer declares of an ACP command - an
    # adapter of its own, or another command for one ao ships - is named, and not started (PROBE-SHIPPED).
    catalog = {adapter_id: adapter for adapter_id, adapter in A.package_adapters().items()
               if adapter.get("contract", A.ADAPTER_CONTRACT) == A.ADAPTER_CONTRACT}
    layered = sorted(adapter_id for adapter_id, entry in A.adapter_catalog(cfg.get("root")).items()
                     if entry["source"] != "package" and isinstance(entry["adapter"].get("acp"), dict)
                     and entry["adapter"].get("acp") != (catalog.get(adapter_id) or {}).get("acp"))
    named = list(getattr(args, "ids", None) or [])
    unknown = [adapter_id for adapter_id in named if adapter_id not in catalog]
    if unknown:
        held = [adapter_id for adapter_id in unknown if adapter_id in layered]
        if held:
            print(f"{C['red']}not started{C['reset']}: ao ships no adapter {', '.join(map(A.shown_text, held))}, and "
                  "it starts the ACP command of one the package ships alone, never one a user's or a project's layer "
                  "declares")
        missing = [adapter_id for adapter_id in unknown if adapter_id not in held]
        if missing:
            print(f"{C['red']}no adapter{C['reset']} {', '.join(map(A.shown_text, missing))}; `ao adapters` lists them")
        return 2
    noted = [adapter_id for adapter_id in layered if not named or adapter_id in named]
    if noted:
        print(f"{C['dim']}Not started: the ACP command a user's or a project's layer declares for "
              f"{', '.join(map(A.shown_text, noted))}; ao starts the one an adapter it ships declares, and only "
              f"that.{C['reset']}")
    silent = [adapter_id for adapter_id in named if not isinstance(catalog[adapter_id].get("acp"), dict)]
    if silent:
        print(f"{C['red']}no ACP command{C['reset']}: the adapter ao ships for {', '.join(map(A.shown_text, silent))} "
              "declares no `acp.argv`")
        return 2
    chosen = named or sorted(adapter_id for adapter_id, adapter in catalog.items()
                             if isinstance(adapter.get("acp"), dict))
    if not chosen:
        print(f"{C['dim']}No adapter ao ships says how to start its harness in ACP (`acp.argv`).{C['reset']}")
        return 1
    failed = 0
    for adapter_id in chosen:
        argv = [str(part) for part in catalog[adapter_id]["acp"].get("argv") or []]
        # What the harness answers is a vendor's, and an id named on the command line is a person's: each is shown
        # escaped, as `ao agents` shows a hook's (LEADS-MCP), as an id a layer declares is where it is named above.
        if not argv:
            failed += 1
            print(f"  {A.shown_text(adapter_id):<12} {C['red']}no command{C['reset']}: its `acp.argv` is empty")
            continue
        try:
            target = harness_profile_path(adapter_id)          # a harness is asked only where its answer can be kept
        except ValueError as exc:
            failed += 1
            print(f"  {A.shown_text(adapter_id):<12} {C['red']}not asked{C['reset']}: {A.shown_text(exc)}")
            continue
        path, version = A.resolve_binary(argv[0])
        if path is None:
            print(f"  {A.shown_text(adapter_id):<12} {C['dim']}not installed: {A.shown_text(argv[0])} is on no path "
                  f"ao searches{C['reset']}")
            continue
        try:
            found = acp.probe([path] + argv[1:], root, timeout=args.timeout, sessions=args.sessions)
        except acp.ProbeError as exc:
            failed += 1
            print(f"  {A.shown_text(adapter_id):<12} {C['red']}no answer{C['reset']}: {A.shown_text(exc)}")
            _probe_left(adapter_id, exc.still_running)
            continue
        left = found.pop("still_running", [])
        profile = dict({"id": adapter_id, "measured_at": int(time.time()), "argv": argv, "binary": path,
                        "binary_version": version}, **found)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        replace_file_durably(target, (json.dumps(profile, indent=1, sort_keys=True) + "\n").encode(UTF8))
        agent = " ".join(A.shown_text(part) for part in (found["agent"].get("name"), found["agent"].get("version"))
                         if part)
        print(f"  {A.shown_text(adapter_id):<12} ACP {A.shown_text(found['protocol_version'])}  "
              f"{agent or '(unnamed)'}  load {'yes' if found['load_session'] else 'no'}  "
              f"session: {A.shown_text(', '.join(found['session'])) or '-'}  "
              f"mcp: {A.shown_text(', '.join(found['mcp'])) or '-'}"
              + (f"  sessions here: {found['sessions']}" if "sessions" in found else ""))
        if left:
            failed += 1
            _probe_left(adapter_id, left)
    return 1 if failed else 0


def _probe_left(adapter_id, left):
    """Say what of a probed agent still runs after the probe stopped it: asked, not assumed stopped
    (WINDOWS-STOP-PROVEN), whether the agent answered or not (ACP-PROBE-LEFT)."""
    if left:
        print(f"  {A.shown_text(adapter_id):<12} {C['red']}{len(left)} process(es) of its agent still run after the "
              f"probe stopped it{C['reset']}: {list(left)}; {_stop_by_hand()}")


# ---- what a harness reads from the tree, read by a person before anything ao starts here runs (HARNESS-CONFIG) ----

def _turn_lines(identity):
    """What a role's block says of its command, a line for each thing it says, as a person reads it (TURN-ARGV); a
    reviewer's routes a line each, so one route's change reads as that route's (ROUTE-ARGV)."""
    lines = []
    for key, value in sorted((identity or {}).items()):
        if value is None:
            continue
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            lines += [f"{key}[{at}]: {json.dumps(item, ensure_ascii=False, sort_keys=True)}"
                      for at, item in enumerate(value)]
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return [A.shown_text(line) for line in lines]


def _harness_change_lines(root, path, state, view):
    """The lines that show one change as it stands: its diff from HEAD where it is text (HARNESS-CONFIG); for a role's
    command, the command a turn would start and what its block says, beside what was last accepted (TURN-ARGV)."""
    import difflib
    import shlex
    kind = view[0] if view else "deleted"
    if kind == "turn":
        _, role, identity, accepted, (ident, argv) = view
        starts = (A.shown_text(" ".join(shlex.quote(str(part)) for part in argv)) if argv
                  else f"nothing: no adapter ao ships composes it ({A.shown_text(ident or 'none named')})")
        lines = list(difflib.unified_diff(_turn_lines(accepted), _turn_lines(identity), lineterm="",
                                          fromfile="accepted" if accepted else "nothing accepted",
                                          tofile=".ao/config.json"))
        return [f"{A.HELD_COMMANDS[role]} would start: {starts}"] + lines
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


def _harness_seen_text(record, changes):
    """Who saw the changes that stand, as a person reads it: what ao wrote itself, and the person who recorded the rest,
    and when (HARNESS-SEEN-OWN)."""
    standing = {(path, sha) for path, _, sha in changes}
    wrote = sorted(item["path"] for item in record.get("written") or []
                   if isinstance(item, dict) and (item.get("path"), item.get("sha256")) in standing)
    others = [change for change in changes if change[0] not in wrote]
    parts = []
    if wrote:
        parts.append(f"ao wrote {', '.join(A.shown_text(path) for path in wrote)} itself")
    if others:
        when = time.strftime("%d %b %H:%M", time.localtime(float(record.get("at") or 0)))
        parts.append(f"{record.get('by') or 'a person'} recorded {A.harness_config_line(others)} on {when}")
    return "; ".join(parts)


def _record_seen(root, by, digest):
    """Record what a person was shown, while it is what the digest was taken of; the command's exit (HARNESS-CONFIG)."""
    try:
        changes = A.harness_config_changes(root)
    except Exception as exc:
        print(f"{C['red']}not recorded{C['reset']}: what a harness reads from this tree cannot be read "
              f"({A.shown_text(' '.join(str(exc).split()))})")
        return 1
    unseen = A.harness_unseen(changes, A.harness_seen_record(root) or {})
    if not unseen or A.harness_config_digest(unseen) != digest:
        print(f"{C['red']}refused{C['reset']}: what a harness reads here is not what that digest was taken of - it "
              f"changed since it was shown, or the digest is another's. Nothing was recorded; "
              f"`ao harness seen --by {A.copyable_word(by) or '<your name>'}` shows it as it stands now")
        return 2
    A.record_harness_seen(root, changes, by, digest)
    print(f"{C['green']}recorded{C['reset']}: {A.shown_text(by)} has seen {A.harness_config_line(unseen)}; a wake, refill, "
          "nudge, hunt or lane starts in this tree again")
    return 0


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

    With --ask, at a terminal, the changes are shown and the person is asked, and a yes records exactly what was shown,
    as the digest does: one step for a person's own tool approvals, which Claude Code writes into
    .claude/settings.local.json each time a tool is allowed for good, and which ao cannot tell from an agent's write
    (HARNESS-SEEN-OWN). Nobody at a terminal - a pipe, a scheduled job, an agent's tool call - is never a yes.
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
    ask, wanted = bool(getattr(args, "ask", False)), (getattr(args, "digest", None) or "").strip()
    if ask and wanted:
        print("--ask and --digest are two ways to record what was shown; give one of them")
        return 2
    shown = {}
    try:
        # Each role's command among them, as its block in .ao/config.json says it (TURN-ARGV).
        A.record_turn_baseline(root, "first reading")
        changes = A.harness_config_changes(root, shown=shown)
    except Exception as exc:
        print(f"{C['red']}not read{C['reset']}: what a harness reads from this tree cannot be read "
              f"({A.shown_text(' '.join(str(exc).split()))}); nothing ao starts here runs until it can, and nothing "
              "was recorded")
        return 1
    if not changes:
        print(f"{C['green']}nothing a harness reads from this tree differs from HEAD{C['reset']}, and each role's "
              "command is the one accepted; nothing to record")
        return 0
    record = A.harness_seen_record(root) or {}
    unseen = A.harness_unseen(changes, record)
    if not unseen:
        print(f"{C['green']}seen{C['reset']}: each change to what a harness reads here is seen as it stands - "
              f"{_harness_seen_text(record, changes)}")
        return 0
    digest = A.harness_config_digest(unseen)
    if wanted:
        return _record_seen(root, by, wanted)
    print(f"{C['b']}harness configuration nobody has seen{C['reset']}: {len(unseen)} change(s) to what a harness "
          "reads from this tree, each as it stands now")
    for path, state, _ in unseen:
        print(f"\n{C['b']}{A.shown_text(path)}{C['reset']} ({A.HARNESS_CONFIG_STATES.get(state, state)})")
        for line in _harness_change_lines(root, path, state, shown.get(path)):
            print(f"  {line}")
    print(f"\ndigest {digest}")
    # The name goes into each command a person copies quoted as one word for any shell they paste it into: copied,
    # `--by Ada Lovelace` named `Lovelace` an adapter and was refused, and a `;` or a `$(...)` in a name ran as a
    # command. A name no quoting holds as one word in every shell is put in no command (LEADS-MCP).
    named = A.copyable_word(by) or "<your name>"
    unquotable = "" if A.copyable_word(by) else (f" ({A.shown_text(by)} holds a sign a shell reads as more than a "
                                                 "name: type the command with your name as you write it)")
    if ask:
        answer = _confirm(f"Record exactly these as seen by {A.shown_text(by)}? [y/N] ")
        if not answer:
            print("Nothing was recorded" + (": nobody can answer here, standard input is not a terminal; read the "
                                            f"changes above, then `ao harness seen --by {named} --digest {digest}` "
                                            f"records them{unquotable}" if answer is None else "."))
            return 1
        return _record_seen(root, by, digest)
    print(f"Nothing is recorded yet. Read each change above, then\n  ao harness seen --by {named} --digest {digest}\n"
          f"records exactly these bytes as seen; a change made since refuses it. At a terminal, "
          f"`ao harness seen --by {named} --ask` shows them and records them on your yes{unquotable}.")
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
        # The harness's name and the session's id are what a hook's command line and input said, and any program on
        # the machine can run the hook: shown escaped, so an escape sequence is not acted on and a line feed starts
        # no row of its own (LEADS-MCP).
        print(f"  {tone.get(session['state'], '')}{session['state']:<8}{C['reset']} "
              f"{A.shown_text(session.get('harness') or '?'):<12} {A.shown_text(session['session'][:24]):<24} "
              f"{since:>8}  last: {session['event']}")
    return 0
