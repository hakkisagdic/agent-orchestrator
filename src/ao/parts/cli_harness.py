"""Harnesses: `ao harness probe`, which asks each harness that speaks ACP what it supports (ACP-PROBE).

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
    """
    from . import acp
    from .storage import replace_file_durably
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
