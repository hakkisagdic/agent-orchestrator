"""Maintenance: remove, prune, notices, the watchdog command, projects, adapters, worktrees, prove,
backup, doctor.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


def _remove_inert_local(target, inv):
    """The one protected effective hook that may safely outlive project state."""
    common_hooks = os.path.realpath(os.path.join(inv["common_dir"], "hooks"))
    return bool(
        target["active"]
        and _state_base(target["static_state"]) == "current-local"
        and target["directory_class"] == "project-local"
        and not target["globally_configured"]
        and target["effective_count"] == 1
        and not target["legacy_location"]
        and os.path.realpath(target["directory"]) != common_hooks
        and (_hook_contains(inv["top"], target["directory"])
             or _hook_contains(inv["git_dir"], target["directory"]))
    )


def _remove_hook_preflight(inv, allow):
    plan, blockers, inert = [], [], []
    if inv["error"]:
        print(f"{C['red']}hook resolver failed; state kept intact{C['reset']}: {inv['error']}")
        return False, plan, inert
    for target in inv["targets"]:
        base = _state_base(target["static_state"])
        if target["eligible"] and base in ("current-local", "current-scoped", "legacy"):
            plan.append(target)
            continue
        if not target["protected"] or base not in (
            "current-local", "current-scoped", "legacy", "ambiguous-ao"
        ):
            continue
        if target["reachability"] == "dead-misplaced":
            print(f"preserved protected dead-misplaced {target['role']} (does not block remove): "
                  f"{target['path']}")
            continue
        if base == "current-local" and _remove_inert_local(target, inv):
            inert.append(target)
            print(f"preserved protected current-local {target['role']} — becomes inert after .ao removal: "
                  f"{target['path']}")
            continue
        blockers.append(target)
        print(f"{C['red']}remove blocked{C['reset']} by protected potentially-effective "
              f"{target['static_state']} {target['role']}: {target['path']}")
    if blockers or _authorization_refusal(plan, allow):
        print("AO state was not changed")
        return False, plan, inert
    return True, plan, inert


def cmd_remove(cfg, args):
    """Take ao off only after every reachable hook target passes one preflight."""
    root = cfg["root"]
    key = A.project_key(root)
    from . import skillkit
    harness_files, mcp_files = skillkit.ao_files(root)
    plan = [PROJECT_MARKER, ".ao/", "agent-mail/", cfg.get("reviews", "semantic-review") + "/"] + harness_files
    print(f"{C['b']}ao remove{C['reset']} would delete from {root}:")
    for rel in plan:
        if os.path.exists(os.path.join(root, rel)):
            print(f"   {rel}")
    for f, name, _ in mcp_files:
        p = os.path.join(root, f)
        if os.path.exists(p):
            print(f"   {f}: the `{name}` server entry (other entries stay)")
    print(f"   launchd jobs, ~/.ao state and logs for {key}, .gitignore lines")
    print(f"   {C['dim']}hook files only when statically AO-owned, untracked, and fully authorized; "
          f"protected dead-misplaced files are preserved{C['reset']}")
    print(f"   {C['dim']}not touched: product files, reviews you moved elsewhere, "
          f"{' / '.join(skillkit.rule_file_names(root))} text (paste-in was yours){C['reset']}")
    if not args.yes:
        print(f"\nre-run with {C['b']}--yes{C['reset']} to do it")
        return 0

    enrollment = _project_enrollment(root)
    if enrollment["state"] == "broken":
        print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
              f"{enrollment['detail']}")
        return 1
    if enrollment["state"] == "enrolled":
        marker_path = os.path.join(root, PROJECT_MARKER)
        if os.path.lexists(marker_path):
            if os.path.isdir(marker_path) and not os.path.islink(marker_path):
                print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
                      f"{PROJECT_MARKER} is a directory")
                return 1
            try:
                os.remove(marker_path)
            except OSError as exc:
                print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
                      f"cannot remove {PROJECT_MARKER}: {exc}")
                return 1
            print(f"removed working-tree {PROJECT_MARKER}")
        print(f"{C['yellow']}phase 1/2{C['reset']} — AO project state and enforcement remain active")
        print(f"  stage the marker deletion: git add -u -- {PROJECT_MARKER}")
        print("  authorize and commit that deletion, then run: ao remove --yes")
        return 0

    allow = bool(getattr(args, "allow_shared_hooks", False))
    inventory = _ao_hook_inventory(root)
    safe, hook_plan, _ = _remove_hook_preflight(inventory, allow)
    if not safe:
        return 1
    for target in hook_plan:
        try:
            os.remove(target["path"])
            print(f"removed {target['role']} — {target['path']}")
        except OSError as exc:
            print(f"{C['red']}failed to remove hook; state kept intact{C['reset']} "
                  f"{target['path']}: {exc}")
            return 1

    # Re-resolve before the first state deletion. A topology race cannot turn a
    # shared live hook into an unrecorded survivor behind deleted AO state.
    after_hooks = _ao_hook_inventory(root)
    safe, new_plan, _ = _remove_hook_preflight(after_hooks, allow)
    if not safe or new_plan:
        print(f"{C['red']}hook topology changed during remove; state kept intact{C['reset']}")
        return 1

    import shutil as _sh
    subprocess.run([sys.executable, "-m", "ao", "-C", root, "watchdog", "uninstall"], capture_output=True)
    for rel in plan:
        p = os.path.join(root, rel)
        if os.path.isdir(p):
            _sh.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    for f, name, remove_when_empty in mcp_files:
        p = os.path.join(root, f)
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding=UTF8))
                if name in (d.get("mcpServers") or {}):
                    del d["mcpServers"][name]
                    json.dump(d, open(p, "w", encoding=UTF8), indent=2)
                if not d.get("mcpServers") and remove_when_empty:
                    os.remove(p)
            except (OSError, ValueError):
                pass
    gi = os.path.join(root, ".gitignore")
    if os.path.exists(gi):
        lines = open(gi, encoding=UTF8).read().split("\n")
        keep = [l for l in lines if l.strip() not in ("agent-mail/*.md", "!agent-mail/README.md", ".ao/inbox/", ".ao/hold")
                and "agent-orchestrator: mail is transient" not in l]
        open(gi, "w", encoding=UTF8).write("\n".join(keep))
    # Backlog #66 owns replacement of this basename/substring state namespace.
    for f in os.listdir(os.path.join(A.HOME, ".ao")) if os.path.isdir(os.path.join(A.HOME, ".ao")) else []:
        if key in f:
            try:
                os.remove(os.path.join(A.HOME, ".ao", f))
            except OSError:
                pass
    print(f"{C['green']}removed{C['reset']} — phase 2/2 complete; AO state is gone and preserved local source hooks are inert")
    return 0


def _alive(pid):
    # os.kill(pid, 0) is CTRL_C_EVENT on Windows, not a question (#71).
    return A._pid_alive(pid)


# What each store is for. The split matters: pruning operational noise is
# housekeeping, pruning evidence destroys the record that commit authority was
# granted against — so they cannot share a default.
STORES = [
    ("progress",      "operational", ".ao/ledger/progress.jsonl",      "watchdog samples behind the spin check"),
    ("notices",       "operational", ".ao/ledger/notices.jsonl",       "alerts raised and suppressed"),
    ("inbox",         "operational", ".ao/inbox",                      "imported source pulls (*.imported)"),
    ("verifications", "evidence",    ".ao/ledger/verifications.jsonl", "measured gate results"),
    ("plans",         "evidence",    ".ao/ledger/plans.jsonl",         "plan hashes as admitted"),
]
HOME_LOGS = ["nudge-{key}.log", "watchdog-{key}.log", "refill-{key}.log"]


def _prune_jsonl(path, cutoff, dry):
    """Drop records older than cutoff. Returns (dropped, kept, bytes_freed)."""
    if not os.path.exists(path):
        return 0, 0, 0
    before = os.path.getsize(path)
    keep = []
    dropped = 0
    for line in open(path, errors="replace", encoding=UTF8):
        if not line.strip():
            continue
        try:
            if json.loads(line).get("at", 0) < cutoff:
                dropped += 1
                continue
        except Exception:
            pass                                  # unparseable: keep it, do not silently lose data
        keep.append(line if line.endswith("\n") else line + "\n")
    if dropped and not dry:
        from .storage import replace_file_durably
        replace_file_durably(path, "".join(keep).encode(UTF8))
    freed = before - sum(len(k.encode()) for k in keep) if dropped else 0
    return dropped, len(keep), max(0, freed)


def cmd_prune(cfg, args):
    """Trim the records this tool accumulates, without touching the audit trail.

    Every store here grows monotonically — one nudge log reached 295 KB in a
    night, and the progress ledger gains a row every two minutes. Left alone they
    become the reason someone stops running the tool.

    Evidence is excluded by default and needs `--evidence`. Verification records
    and plan hashes are what commit authority was granted against; deleting them
    as housekeeping would quietly remove the ability to answer "on what basis did
    this land".
    """
    root = cfg["root"]
    cutoff = time.time() - args.days * 86400
    dry = not args.yes
    key = A.project_key(root)
    print(f"{C['b']}pruning records older than {args.days} day(s){C['reset']}"
          f"{C['dim']}  {root}{C['reset']}")
    if dry:
        print(f"{C['yellow']}dry run — add --yes to apply{C['reset']}")

    total = 0
    for name, kind, rel, desc in STORES:
        if kind == "evidence" and args.evidence:
            # Evidence is never pruned. A chained ledger past its bound is sealed:
            # its oldest rows move whole to .ao/ledger/sealed/ and stay readable (#50).
            keep = S.get(cfg, "retention.evidence_keep")
            sealed = None
            if name == "verifications" and not dry:
                from .storage import seal_chained_jsonl
                sealed = seal_chained_jsonl(os.path.join(root, rel), A.VERIFICATION_CHAIN, keep, legacy_prefix=True)
            print(f"  {C['dim']}keep  {name:<14} evidence is never pruned; "
                  + (f"sealed through row {sealed['retired']} into .ao/ledger/sealed/" if sealed
                     else f"rows past the newest {keep} are sealed, not dropped") + f"{C['reset']}")
            continue
        if kind == "evidence" and not args.evidence:
            path = os.path.join(root, rel)
            if os.path.exists(path):
                n = sum(1 for _ in open(path, errors="replace", encoding=UTF8))
                print(f"  {C['dim']}skip  {name:<14} {n} records — evidence, needs --evidence{C['reset']}")
            continue
        path = os.path.join(root, rel)
        if name == "inbox":
            if not os.path.isdir(path):
                continue
            gone = 0
            for f in os.listdir(path):
                fp = os.path.join(path, f)
                if f.endswith(".imported") and os.path.getmtime(fp) < cutoff:
                    total += os.path.getsize(fp)
                    gone += 1
                    if not dry:
                        os.remove(fp)
            if gone:
                print(f"  {C['green']}{'would drop' if dry else 'dropped'}{C['reset']}  "
                      f"{name:<14} {gone} file(s)  {C['dim']}{desc}{C['reset']}")
            continue
        if name == "notices" and not dry:
            A.fold_notice_times(root)             # what the prune drops still counts in a window (NOTICE-WINDOW)
        dropped, kept, freed = _prune_jsonl(path, cutoff, dry)
        total += freed
        if dropped:
            print(f"  {C['green']}{'would drop' if dry else 'dropped'}{C['reset']}  "
                  f"{name:<14} {dropped} of {dropped + kept}  {C['dim']}{desc}{C['reset']}")

    # Review artefacts stay while anything rests on them; the rest leave by age (#38).
    review_days = getattr(args, "review_days", None)
    if review_days is None:
        review_days = S.get(cfg, "review.prune_after_days")
    try:
        outcome = A.prune_review_artefacts(root, cfg, review_days, apply=not dry)
    except Exception as exc:
        print(f"  {C['yellow']}keep  {'reviews':<14} every artefact: what rests on them cannot be read "
              f"({exc}){C['reset']}")
    else:
        why = {}
        for reasons in outcome["kept"].values():
            for reason in reasons:
                why[reason] = why.get(reason, 0) + 1
        if outcome["moved"]:
            total += outcome["bytes"]
            print(f"  {C['green']}{'would move' if dry else 'moved'}{C['reset']}  {'reviews':<14} "
                  f"{len(outcome['moved'])} older than {review_days:g} day(s) that nothing rests on  "
                  f"{C['dim']}→ {outcome['archive']}{C['reset']}")
        if outcome["kept"] or outcome["recent"]:
            detail = ", ".join(f"{count} {reason}" for reason, count in sorted(why.items()))
            print(f"  {C['dim']}keep  {'reviews':<14} {len(outcome['kept'])} referenced"
                  + (f" ({detail})" if detail else "") + f", {outcome['recent']} recent{C['reset']}")

    # The authority chain is never truncated by deletion: past its bound it is sealed (#50).
    if args.evidence and not dry:
        from .storage import seal_chained_jsonl
        sealed = seal_chained_jsonl(os.path.join(root, ".ao", "ledger", "authority.jsonl"), A.AUTHORITY_CHAIN,
                                    S.get(cfg, "retention.evidence_keep"))
        if sealed:
            print(f"  {C['green']}sealed{C['reset']}  {'authority':<14} through row {sealed['retired']}; "
                  f"{C['dim']}ao commit-check validates across the seal{C['reset']}")
    # Dedupe by inode, not by path string: this filesystem is case-insensitive, so
    # "nudge-Voltrai.log" and "nudge-voltrai.log" are one file that would
    # otherwise be counted — and truncated — twice.
    seen_inodes = set()
    for pattern in HOME_LOGS:
        path = os.path.join(A.HOME, ".ao", pattern.format(key=key))
        alt = os.path.join(A.HOME, ".ao", pattern.format(key=key.lower()))
        for pth in {path, alt}:
            if not os.path.exists(pth):
                continue
            ino = os.stat(pth).st_ino
            if ino in seen_inodes:
                continue
            seen_inodes.add(ino)
            size = os.path.getsize(pth)
            if size < args.keep_kb * 1024:
                continue
            if not dry:
                # keep the tail: the last turn's output is the only part anyone
                # reads, and it is what a failed nudge is diagnosed from
                with open(pth, errors="replace", encoding=UTF8) as fh:
                    fh.seek(max(0, size - args.keep_kb * 1024))
                    tail = fh.read()
                with open(pth, "w", encoding=UTF8) as fh:
                    fh.write(f"[truncated by ao prune {datetime.now():%Y-%m-%d %H:%M}]\n" + tail)
            total += size - args.keep_kb * 1024
            print(f"  {C['green']}{'would trim' if dry else 'trimmed'}{C['reset']}  "
                  f"{os.path.basename(pth):<24} {size // 1024}KB → {args.keep_kb}KB")

    print(f"\n{C['b']}{total // 1024}KB{C['reset']} {'reclaimable' if dry else 'reclaimed'}")
    return 0


def cmd_notices(cfg, args):
    """Alerts this project raised — the desktop notification, kept.

    A notification reaches the human and vanishes, so the architect reading the
    panel is the one participant who never sees what the human was told.
    """
    root = cfg["root"]
    wanted = getattr(args, "ident", None)
    if wanted:
        # "Why did I get this?" is one command (#37).
        row = next((r for r in A.notices(root, 10**9, include_suppressed=True) if r.get("id") == wanted), None)
        if not row:
            print(f"no notice {wanted}; `ao notices --all` lists them with their ids")
            return 1
        when = datetime.fromtimestamp(row["at"]).strftime("%d %b %H:%M")
        print(f"{C['b']}{row['title']}{C['reset']}  {C['dim']}{wanted} · {when} · "
              f"{'sent' if row.get('sent') else 'held'} · key {row.get('key')}{C['reset']}")
        print(f"  {row.get('msg')}")
        for line in A.evidence_lines(row.get("evidence")):
            print(f"  {line}")
        return 0
    rows = A.notices(root, args.n, include_suppressed=args.all)
    if not rows:
        print(f"{C['dim']}No notices recorded.{C['reset']}")
        return 0
    for r in rows:
        when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
        tag = (f"{C['green']}sent{C['reset']}" if r.get("sent")
               else f"{C['dim']}held{C['reset']}")
        print(f"  {C['dim']}{when}{C['reset']}  {tag}  {C['b']}{r['title']}{C['reset']}  {r['msg']}"
              + (f"  {C['dim']}{r['id']}{' · evidence' if r.get('evidence') else ''}{C['reset']}"
                 if r.get("id") else ""))
    if not args.all:
        print(f"{C['dim']}  (--all also shows alerts the rate limit suppressed){C['reset']}")
    return 0


def _watchdog_windows(cfg, args):
    """Task Scheduler is Windows' launchd: one task every two minutes, one every fifteen."""
    root = cfg["root"]
    key = A.project_key(root).lower()
    tasks = {f"ao-watchdog-{key}": (2, f'"{shutil.which("ao-watchdog") or "ao-watchdog"}" --root "{root}" --idle-minutes {getattr(args, "idle_minutes", None) or S.get(cfg, "watchdog.idle_minutes")}'),
             f"ao-doctor-{key}": (15, f'"{shutil.which("ao") or "ao"}" -C "{root}" doctor --check --notify')}
    if args.action == "status":
        for name in tasks:
            r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True, text=True, encoding=UTF8, errors="replace")
            print(f"{name}: {'scheduled' if r.returncode == 0 else 'absent'}")
        return 0
    if args.action == "uninstall":
        for name in tasks:
            subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True)
            print(f"removed {name}")
        return 0
    for name, (minutes, cmd) in tasks.items():
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes), "/TN", name, "/TR", cmd],
                           capture_output=True, text=True, encoding=UTF8, errors="replace")
        print(f"{'installed' if r.returncode == 0 else 'FAILED'} {name} (every {minutes}m)" + ("" if r.returncode == 0 else f": {r.stderr.strip()[:120]}"))
    return 0


def _log_tail(path, count=5):
    """A log's last `count` lines as `tail -<count>` printed them, stripped; "" when it cannot be read.

    Read back from the end in blocks: a job's log grows for as long as the job runs.
    """
    try:
        with open(path, "rb") as fh:
            position, data = fh.seek(0, os.SEEK_END), b""
            while position and data.count(b"\n") <= count:
                step = min(position, 65536)
                position -= step
                fh.seek(position)
                data = fh.read(step) + data
    except OSError:
        return ""
    lines = data.split(b"\n")
    if data.endswith(b"\n"):
        lines.pop()
    # Decoded as sh() decoded tail's output: text mode also reads a carriage return as a newline.
    text = b"\n".join(lines[-count:]).decode(UTF8, "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def cmd_watchdog(cfg, args):
    if getattr(args, "idle_minutes", None) is None:
        args.idle_minutes = S.get(cfg, "watchdog.idle_minutes")
    return _cmd_watchdog(cfg, args)


def _cmd_watchdog(cfg, args):
    """Install, remove or inspect the launchd job that restarts a stalled agent."""
    if args.action in ("explain", "trace"):
        return _watchdog_debug(cfg, args)
    if os.name == "nt":
        return _watchdog_windows(cfg, args)
    import getpass
    root = cfg["root"]
    key = A.project_key(root).lower()
    label = f"com.agentorchestrator.watchdog.{key}"
    plist_path = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
    # After a pip/uv install there is no scripts/ directory; there is a console
    # script on PATH. launchd needs an absolute path either way, so resolve
    # whichever one this installation actually has.
    script = shutil.which("ao-watchdog") or os.path.join(A.REPO, "scripts", "ao-watchdog")
    # A console script carries its own interpreter in its shebang — the venv's.
    # Prefixing it with *this* process's python (the system one, if `ao watchdog
    # install` was run from a checkout) imports `ao` from an interpreter that
    # does not have it, and launchd logs ModuleNotFoundError every two minutes.
    # Only our repo shim needs an explicit interpreter, because its shebang is
    # `/usr/bin/env python3` and launchd's PATH is minimal.
    in_repo = os.path.realpath(script).startswith(os.path.realpath(A.REPO))
    python = sys.executable if in_repo else ""
    log = os.path.expanduser(f"~/.ao/watchdog-{key}.log")

    if args.action == "status":
        loaded = _launchd_listed(label)
        dloaded = _launchd_listed(f"com.agentorchestrator.doctor.{key}")
        print(f"label   {label}")
        print(f"doctor  {'loaded' if dloaded else 'not installed'}  (ao doctor --check every 15m)")
        print(f"plist   {'present' if os.path.exists(plist_path) else 'absent'}")
        print(f"loaded  {loaded if loaded else 'no'}")
        from .watchdog import cycle_health
        health = cycle_health(root)
        if health:
            took = (f"longest took {health['longest_seconds']:.0f}s at {health['longest_at']}"
                    if health["longest_seconds"] is not None else "durations not recorded yet")
            gap = (f"longest gap {health['gap_minutes']:.0f}m before {health['gap_at']}"
                   if health["gap_at"] else "no gaps")
            print(f"cycles  {health['count']} recorded · {took} · {gap}")
        if os.path.exists(log):
            print(f"\nlast lines of {log}:")
            print(_log_tail(log))
        return

    if args.action == "uninstall":
        if _launchctl("bootout", _launchd_domain(label))[1] != 0:
            _launchctl("unload", plist_path)
        if os.path.exists(plist_path):
            os.remove(plist_path)
        print(f"removed {label}")
        dlabel = f"com.agentorchestrator.doctor.{key}"
        dplist = os.path.expanduser(f"~/Library/LaunchAgents/{dlabel}.plist")
        _launchctl("bootout", _launchd_domain(dlabel))
        if os.path.exists(dplist):
            os.remove(dplist)
            print(f"removed {dlabel}")
        # A heartbeat left behind reads as a dead watchdog to every other project (audit).
        try:
            os.remove(A.heartbeat_path(root))
        except OSError:
            pass
        return

    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
    os.makedirs(os.path.expanduser("~/.ao"), exist_ok=True)
    open(plist_path, "w", encoding=UTF8).write(PLIST.format(
        path=_launchd_path(), label=label, python_arg=(f"<string>{python}</string>" if python else ""),
        script=script, root=root,
        idle=args.idle_minutes, interval=args.interval, log=log))
    _launchctl("bootout", _launchd_domain(label))
    # bootout is asynchronous: a bootstrap issued before the old job is fully
    # gone fails with "5: Input/output error" and leaves nothing loaded — a
    # reinstall that silently uninstalled. Wait for the label to clear, retry.
    out = ""
    for attempt in range(5):
        if _launchd_listed(label):
            time.sleep(1)
        out, status = _launchctl("bootstrap", _launchd_domain(), plist_path, merge=True)
        out = out or ("launchctl could not be run" if status is None else "loaded")
        if "error" not in out.lower() or _launchd_listed(label):
            break
        time.sleep(1 + attempt)
    if not _launchd_listed(label):
        print(f"{C['red']}NOT LOADED{C['reset']} {label}: {out.strip()[:120]} — run: launchctl bootstrap gui/$(id -u) {plist_path}")
        return 1
    print(f"installed {label}")
    # The second, independent check. A watchdog cannot report its own death;
    # this job runs `ao doctor --check --notify` every fifteen minutes from its own
    # launchd entry and raises the alarm the watchdog would have.
    dlabel = f"com.agentorchestrator.doctor.{key}"
    dplist = os.path.expanduser(f"~/Library/LaunchAgents/{dlabel}.plist")
    ao_exe = shutil.which("ao") or os.path.join(A.REPO, "bin", "ao")
    ao_in_repo = os.path.realpath(ao_exe).startswith(os.path.realpath(A.REPO))
    dargs = ([sys.executable] if ao_in_repo else []) + [ao_exe, "-C", root, "doctor", "--check", "--notify"]
    open(dplist, "w", encoding=UTF8).write(PLIST_CMD.format(
        path=_launchd_path(), label=dlabel, args="".join(f"<string>{a}</string>" for a in dargs),
        interval=900, log=os.path.expanduser(f"~/.ao/doctor-{key}.log")))
    _launchctl("bootout", _launchd_domain(dlabel))
    _launchctl("bootstrap", _launchd_domain(), dplist, merge=True)
    print(f"installed {dlabel}  (ao doctor --check --notify every 15m — the second, independent check)")
    print(f"  checks every {args.interval}s · nudges after {args.idle_minutes}m idle")
    print(f"  log: {log}")
    print(f"  remove with: ao -C {root} watchdog uninstall")
    if "error" in out.lower():
        print(f"  launchctl: {out}")


def cmd_projects(cfg, args):
    ws = A.all_workspaces()
    if not ws:
        print("No local agent sessions found.")
    else:
        print(f"{'last active':<12}{'status':<14}workspace")
    for r in ws:
        mins = int((time.time() - r["mtime"]) / 60)
        age = f"{mins}m" if mins < 90 else (f"{mins//60}h" if mins < 2880 else f"{mins//1440}d")
        col = C["green"] if mins < 5 else C["dim"]
        print(f"{col}{age:<12}{C['reset']}{r['status'][:13]:<14}{r['path']}")
    # Directories with one name used to share every file ao keeps outside them (#66).
    for base, rows in A.project_key_collisions().items():
        print(f"\n{C['yellow']}{len(rows)} projects are named {base}{C['reset']}; "
              "each keeps its own files under its key:")
        for key, where in rows:
            print(f"  {key:<28}{where}")


def cmd_adapters(cfg, args):
    """Every adapter ao can load, where it came from, and whether a candidate is sound (#77)."""
    root = cfg["root"]
    action = getattr(args, "action", None) or "list"
    if action in ("validate", "conform"):
        target = getattr(args, "target", None)
        catalog = A.adapter_catalog(root)
        if target in catalog:
            adapter = catalog[target]["adapter"]
        else:
            try:
                with open(target or "", encoding=UTF8) as fh:
                    adapter = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"{C['red']}no adapter {target}{C['reset']}: {exc}")
                return 2
        if action == "validate":
            problems = A.validate_adapter(adapter)
            for problem in problems:
                print(f"  {C['red']}·{C['reset']} {problem}")
            print(f"{C['green']}sound{C['reset']}" if not problems else f"{len(problems)} problem(s)")
            return 1 if problems else 0
        import tempfile
        harness = os.path.join(os.path.dirname(A.__file__), "conformance_harness.py")
        with tempfile.TemporaryDirectory(prefix="ao-conform-") as workdir:
            shim = os.path.join(workdir, "harness")
            with open(shim, "w", encoding=UTF8) as fh:
                fh.write(f"#!{sys.executable}\n" + open(harness, encoding=UTF8).read())
            os.chmod(shim, 0o755)
            results = A.conform_adapter(adapter, shim, workdir)
        for capability, state, detail in results:
            tone = C["green"] if state == "pass" else C["red"] if state == "fail" else C["dim"]
            print(f"  {capability:<11} {tone}{state}{C['reset']}  {C['dim']}{detail}{C['reset']}")
        return 1 if any(state == "fail" for _, state, _ in results) else 0
    avail = A.tool_availability()
    print(f"{'adapter':<16}{'source':<9}{'contract':<10}{'verified':<12}{'on this machine':<22}observation")
    for ident, entry in sorted(A.adapter_catalog(root).items()):
        a = entry["adapter"]
        if a.get("kind") == "cloud":
            continue
        if entry["problem"]:
            print(f"{ident:<16}{entry['source']:<9}{C['red']}refused{C['reset']}  {entry['problem']}")
            continue
        verified = a.get("verified", "?")
        col = C["green"] if verified == "full" else C["yellow"] if verified == "partial" else C["dim"]
        have = avail.get(ident, {})
        here = ("installed + account" if have.get("installed") and have.get("account") else "installed"
                if have.get("installed") else "account, no CLI" if have.get("account") else "—")
        observation = "call-return" if a.get("observation_mode") == "call-return" \
            else (a.get("transcript", {}) or {}).get("kind", "—")
        eligible, _ = A.reviewer_eligibility(a)
        print(f"{ident:<16}{entry['source']:<9}{str(a.get('contract', A.ADAPTER_CONTRACT)):<10}{col}{verified:<12}"
              f"{C['reset']}{here:<22}{observation:<14}"
              f"{'reviewer: eligible' if eligible else C['dim'] + 'reviewer: ineligible' + C['reset']}")
    for vendor in A.vendor_list():
        if not vendor.get("adapter"):
            print(f"{vendor['id']:<16}{C['dim']}{'vendor':<9}{'—':<10}{'no adapter':<12}{vendor.get('why', '')}"
                  f"{C['reset']}")
    for problem in A.vendor_problems():
        print(f"{C['red']}vendor list{C['reset']}: {problem}")
    print(f"\n{C['dim']}Account detection via keyflip surfaces; it never reads the secret. Adapters load from the "
          f"package, then ~/.ao/adapters, then .ao/adapters; a later one overrides by id.{C['reset']}")


def _optional_features(cfg):
    """(name, state, what would enable it) for each optional capability, core excluded (#82)."""
    from . import email, telegram
    root = cfg["root"]
    mail = email.config()
    features = [
        ("keyflip", "installed" if shutil.which("keyflip") else "absent",
         "install keyflip for account budgets and quota rotation"),
        ("telegram", "configured" if telegram.config() else "absent", "ao telegram setup"),
        ("email", f"configured ({mail['provider']})" if mail else "absent", "ao email setup"),
        ("ping", "configured" if A.ping_url(root) else "absent", "ao ping set <url>"),
    ]
    # A tool reviewer is an optional extra with its own provider account (#86).
    primary = cfg.get("reviewer") or {}
    routes = [primary] + list(primary.get("fallbacks") or []) if isinstance(primary, dict) else []
    configured = {route.get("adapter") for route in routes if _tool_route(route)}
    for ident, adapter in sorted(A.package_adapters().items()):
        contract = A.tool_review_contract(adapter)
        if contract is None:
            continue
        found = any(shutil.which(binary) or _tool_beside_interpreter(binary) for binary in A.adapter_binaries(adapter))
        features.append((ident, "absent" if not found else "configured" if ident in configured else "installed",
                         str(contract.get("install") or f"install {ident}")))
    return features


def _measurement_lines(cfg):
    """How ao measures, what could filter the numbers an agent reads (#51), and what each filter does to them (#52)."""
    filters = A.measurement_filters(cfg["root"])
    state = (f"{C['yellow']}{len(filters)} possible filter(s){C['reset']}" if filters
             else f"{C['green']}unfiltered{C['reset']}")
    lines = [f"{'measurement':<16}{state}  {C['dim']}ao measures with {A.git_binary()}, "
             f"the candidate without a shell{C['reset']}"]
    lines += [f"{'':<16}{C['dim']}{text}{C['reset']}" for text in filters]
    try:
        probed = A.probe_filters(cfg["root"])
    except Exception as exc:
        return lines + [f"{'filter probe':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    return lines + [f"{'filter probe':<16}{C['green'] if result['verdict'] == 'in-force' else C['yellow']}"
                    f"{A.filter_probe_text(result)}{C['reset']}" for result in probed]


def _review_evidence_lines(cfg):
    """The reviews a grant rests on that git does not hold: one disk from gone (#38)."""
    try:
        risky = A.grant_artefacts_at_risk(cfg["root"], cfg)
    except Exception as exc:
        return [f"{'review evidence':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    if not risky:
        return [f"{'review evidence':<16}{C['green']}every review a grant rests on is in git{C['reset']}"]
    untracked = sum(1 for _, state in risky if state == "untracked")
    lines = [f"{'review evidence':<16}{C['yellow']}{untracked} untracked, {len(risky) - untracked} missing of "
             f"the reviews grants rest on{C['reset']}  {C['dim']}commit them; ao prune never moves them{C['reset']}"]
    lines += [f"{'':<16}{C['dim']}{state:<9} {name}{C['reset']}" for name, state in risky[:5]]
    if len(risky) > 5:
        lines.append(f"{'':<16}{C['dim']}and {len(risky) - 5} more{C['reset']}")
    return lines


def _architect_absence_lines(cfg):
    """How long the architect has been away and how many questions wait for it (#84)."""
    try:
        away = A.architect_absence(cfg["root"], cfg)
    except Exception as exc:
        return [f"{'architect away':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    seen = (f"last seen {_elapsed(time.time() - away['seen_at'])} ago" if away["seen_at"]
            else "not seen in this project's records")
    if not away["waiting"]:
        return [f"{'architect away':<16}{C['dim']}{seen} · no question waiting{C['reset']}"]
    oldest = _elapsed(time.time() - float(away["oldest_at"] or time.time()))
    return [f"{'architect away':<16}{C['yellow']}{seen} · {len(away['waiting'])} question(s) waiting, the oldest "
            f"{away['waiting'][0]} for {oldest}{C['reset']}  {C['dim']}answered in one pass: ao decisions{C['reset']}"]


def _human_bytes(count):
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024


def cmd_worktrees(cfg, args):
    """Every worktree, what keeps it and whether it may go; `prune` retires those that may (#42).

    Seven worktrees stood on this machine, one per slice, none removed when its
    branch landed, each a full checkout with its own state and stale reviews.
    Prune is a dry run until --yes.
    """
    root = cfg["root"]
    try:
        facts = A.worktree_facts(root, cfg, sizes=True)
    except RuntimeError as exc:
        print(f"{C['red']}{exc}{C['reset']}")
        return 2
    pruning = getattr(args, "action", None) == "prune"
    apply = pruning and getattr(args, "yes", False)
    if pruning and not apply:
        print(f"{C['yellow']}dry run — add --yes to apply{C['reset']}")
    for fact in facts:
        size = _human_bytes(fact["bytes"]) if fact["bytes"] is not None \
            else "gone" if not os.path.isdir(fact["path"]) else "—"
        state = (f"{C['green']}may go{C['reset']} ({fact['why']})" if fact["may_go"]
                 else f"{C['dim']}keep{C['reset']} ({'; '.join(fact['keep']) or 'not merged, slice not rejected'})")
        print(f"  {fact['path']}  {C['dim']}{fact['branch'] or 'detached'} · {size}{C['reset']}  {state}")
        if pruning and fact["may_go"]:
            try:
                for step in A.prune_worktree(root, fact, apply=apply):
                    print(f"      {'done' if apply else 'would'}: {step}")
            except (OSError, RuntimeError) as exc:
                print(f"      {C['red']}stopped{C['reset']}: {exc}")
                return 1
    return 0


def _worktree_lines(cfg):
    """Worktrees whose branch is merged or gone, with their size on disk (#42)."""
    try:
        going = [fact for fact in A.worktree_facts(cfg["root"], cfg, sizes=True) if fact["may_go"]]
    except Exception as exc:
        return [f"{'worktrees':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    if not going:
        return []
    total = sum(fact["bytes"] or 0 for fact in going)
    lines = [f"{'worktrees':<16}{C['yellow']}{len(going)} may go ({_human_bytes(total)}){C['reset']}  "
             f"{C['dim']}ao worktrees prune{C['reset']}"]
    lines += [f"{'':<16}{C['dim']}{fact['path']}  {fact['why']}  "
              f"{_human_bytes(fact['bytes']) if fact['bytes'] is not None else 'gone'}{C['reset']}" for fact in going[:5]]
    return lines


PROVE_BOUNDARY = ("ao prove: a throwaway candidate that adds one line to ao-prove.txt and nothing else. "
                  "Approve it if the diff is exactly that.")


def _prove_throwaway(cfg):
    """A one-line slice through verify, review and commit-ok in a temporary worktree; nothing lands (#85).

    Returns (ok, what failed or None, what would fix it or None). The worktree's
    coordination state and review go to ~/.ao/archive/<project>/prove-<stamp>/.
    """
    import contextlib
    import io
    import shutil
    import tempfile
    from types import SimpleNamespace
    root = cfg["root"]
    scratch = tempfile.mkdtemp(prefix="ao-prove-")
    tree = os.path.join(scratch, "tree")
    links, heard = [], io.StringIO()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        added = subprocess.run([A.git_binary(), "worktree", "add", "--detach", "--quiet", tree, "HEAD"], cwd=root,
                               capture_output=True)
        if added.returncode:
            return False, "a temporary worktree could not be made: " + added.stderr.decode(UTF8, "replace").strip(), \
                "a repository with at least one commit"
        os.makedirs(os.path.join(tree, ".ao"), exist_ok=True)
        for name in ("config.json", "gates.json"):
            if os.path.exists(os.path.join(root, ".ao", name)):
                shutil.copy2(os.path.join(root, ".ao", name), os.path.join(tree, ".ao", name))
        with open(os.path.join(tree, ".ao", "board.md"), "w", encoding=UTF8) as fh:
            fh.write(f"# Board\n\n## running\n- [AO-PROVE] a throwaway one-line change · acceptance: {PROVE_BOUNDARY}\n"
                     "\n## blocked\n\n## queued\n\n## verified\n\n## done\n")
        for name in S.get(cfg, "merge.link_paths"):
            source, target = os.path.join(root, name), os.path.join(tree, name)
            if os.path.exists(source) and not os.path.lexists(target):
                try:
                    os.symlink(source, target)
                    links.append(target)
                except OSError:
                    pass
        with open(os.path.join(tree, "ao-prove.txt"), "a", encoding=UTF8) as fh:
            fh.write(f"ao prove {stamp}\n")
        subprocess.run([A.git_binary(), "add", "ao-prove.txt"], cwd=tree, check=True, capture_output=True)
        tree_cfg = A.load_config(tree)
        with contextlib.redirect_stdout(heard):
            verified = cmd_verify(tree_cfg, SimpleNamespace(profile="quick", wait=900))
        if verified != 0:
            return False, "the quick gates did not pass a one-line change", \
                "run `ao verify -p quick` and read the failing gate:\n" + _last_lines(heard.getvalue())
        with contextlib.redirect_stdout(heard):
            reviewed = cmd_review(tree_cfg, SimpleNamespace(action=None, rid=None, any=False, run=None, boundary=None,
                                                            paths=None, commits=None, timeout=None))
        if reviewed != 0:
            return False, "the reviewer did not approve a trivial candidate", \
                "read the review it wrote (archived below); exit 3 means no reviewer could review:\n" \
                + _last_lines(heard.getvalue())
        with contextlib.redirect_stdout(heard):
            granted = cmd_commit_ok(tree_cfg, SimpleNamespace(verify=False, profile=None, review=None))
        if granted != 0:
            return False, "ao commit-ok refused the approved candidate", _last_lines(heard.getvalue())
        return True, None, None
    finally:
        for link in links:
            try:
                os.unlink(link)
            except OSError:
                pass
        archive = os.path.join(A.HOME, ".ao", "archive", A.project_key(root), f"prove-{stamp}")
        for part in (".ao", cfg.get("reviews", "semantic-review")):
            if os.path.isdir(os.path.join(tree, part)):
                shutil.copytree(os.path.join(tree, part), os.path.join(archive, part), symlinks=True,
                                dirs_exist_ok=True)
        subprocess.run([A.git_binary(), "worktree", "remove", "--force", tree], cwd=root, capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)
        subprocess.run([A.git_binary(), "worktree", "prune"], cwd=root, capture_output=True)


def _last_lines(text, count=6):
    lines = [A.re.sub(r"\x1b\[[0-9;]*m", "", line) for line in text.strip().splitlines() if line.strip()]
    return "\n".join("      " + line for line in lines[-count:])


def cmd_prove(cfg, args):
    """Run the guarantees instead of describing them, and say what would fix each one that fails (#85).

    The hook must refuse a synthetic candidate, the reviewer must answer and be
    another actor than the implementer, and a throwaway slice must go through
    verify, review and commit-ok in a temporary worktree. Nothing is committed.
    """
    root = cfg["root"]
    results = []
    probe = _hook_execution_probe(_ao_hook_inventory(root))
    results.append(("hook refuses an unauthorised commit", probe["installed"],
                    None if probe["installed"] else f"{_hook_probe_text(probe)} — ao hooks install"))
    reviewer = _reviewer_probe(cfg)
    reviewer_ok = reviewer["ok"] and reviewer["configured"]
    results.append(("reviewer answers and is another actor", reviewer_ok, None if reviewer_ok else
                    f"{_reviewer_probe_text(reviewer)} — name a reviewer of another model family in .ao/config.json "
                    "(docs/roles.md)"))
    if getattr(args, "no_review", False):
        results.append(("a throwaway slice lands end to end", False, "skipped with --no-review: not proven"))
    elif not reviewer_ok:
        results.append(("a throwaway slice lands end to end", False, "not tried: no reviewer can review"))
    else:
        ok, failed, fix = _prove_throwaway(cfg)
        results.append(("a throwaway slice lands end to end", ok, None if ok else f"{failed} — {fix}"))
    for claim, ok, fix in results:
        print(f"  {C['green'] + 'proven' if ok else C['red'] + 'NOT PROVEN'}{C['reset']}  {claim}")
        if fix:
            print(f"      {fix}")
    proven = all(ok for _, ok, _ in results)
    print(f"\n{C['b']}{'PROVEN' if proven else 'NOT PROVEN'}{C['reset']}  "
          f"{C['dim']}nothing was committed; the throwaway worktree is gone{C['reset']}")
    return 0 if proven else 1


def _init_then_prove(cfg, args):
    code = cmd_init(cfg, args)
    if code or not getattr(args, "prove", False):
        return code
    print(f"\n{C['b']}proving the guarantees{C['reset']}")
    return cmd_prove(A.load_config(cfg["root"]), args)


def _doctor_consistency(cfg, repair=False):
    """`ao doctor --consistency [--repair]`: the four stores checked against each other (#47)."""
    root = cfg["root"]
    findings = A.consistency_findings(root, cfg)
    if repair:
        for finding in A.repair_consistency(root, findings):
            print(f"{C['green']}repaired{C['reset']}  {finding['text']}  {C['dim']}recorded in "
                  f".ao/ledger/repairs.jsonl{C['reset']}")
        findings = A.consistency_findings(root, cfg)
    if not findings:
        print(f"{C['green']}consistent{C['reset']}  board, ledgers, review artefacts and git agree")
        return 0
    for finding in findings:
        mark = f"{C['yellow']}repairable{C['reset']}" if finding["repair"] else f"{C['red']}disagrees{C['reset']}"
        print(f"{mark}  {finding['kind']}: {finding['text']}")
    if any(finding["repair"] for finding in findings):
        print(f"{C['dim']}ao doctor --consistency --repair fixes the repairable ones and records what it did{C['reset']}")
    return 1


def _store_bound_lines(cfg):
    """Observation stores past their bound (#50): the watchdog holds them each cycle, so one here means it is not."""
    from .watchdog import STATE_DIR
    over = A.stores_over_bound(cfg["root"], cfg, STATE_DIR)
    if not over:
        return []
    return [f"{'stores':<16}{C['yellow']}{len(over)} over their bound{C['reset']}  "
            f"{C['dim']}{', '.join(f'{os.path.basename(p)} {size // 1024}KB/{limit // 1024}KB' for p, size, limit in over)}"
            f" — the watchdog trims them each cycle; is it running?{C['reset']}"]


def cmd_backup(cfg, args):
    """Write the project's governance to the destination it names: a directory, `ref`, or `remote:<name>` (#46)."""
    destination = getattr(args, "to", None) or (cfg.get("backup") or {}).get("to")
    if not destination:
        print(f"{C['yellow']}no destination{C['reset']}: `ao backup --to <directory|ref|remote:name>`, or name one as "
              "backup.to in .ao/config.json")
        return 2
    try:
        manifest = A.write_backup(cfg["root"], cfg, destination)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"{C['red']}not backed up{C['reset']}: {exc}")
        return 1
    print(f"{C['green']}backed up{C['reset']} {len(manifest['files'])} governance file(s) → {manifest['where']}")
    return 0


def cmd_restore(cfg, args):
    """Reconstruct the control plane from a backup directory; say what could not be verified (#46)."""
    root = cfg["root"]
    try:
        restored, unverified = A.restore_backup(root, args.source)
    except (OSError, ValueError, KeyError) as exc:
        print(f"{C['red']}not restored{C['reset']}: {exc}")
        return 2
    print(f"restored {len(restored)} file(s)")
    for line in unverified:
        print(f"  {C['red']}not verified{C['reset']}  {line}")
    try:
        A.authority_rows(root)
        A.board(root)
        print(f"{C['green']}the authority chain and the board validate{C['reset']}")
    except Exception as exc:
        print(f"{C['red']}restored state does not validate{C['reset']}: {exc}")
        return 1
    return 1 if unverified else 0


def _backup_lines(cfg):
    """How old the newest backup is, and whether governance exists with none (#46)."""
    age = A.backup_age(cfg["root"])
    if age is None:
        return [f"{'backup':<16}{C['yellow']}none{C['reset']}  {C['dim']}the control plane is on this disk only — "
                f"ao backup --to <directory|ref|remote:name>{C['reset']}"]
    seconds, where = age
    tone = C["green"] if seconds < 7 * 86400 else C["yellow"]
    return [f"{'backup':<16}{tone}{_elapsed(seconds)} ago{C['reset']}  {C['dim']}{where}{C['reset']}"]


def cmd_doctor(cfg, args):
    if getattr(args, "consistency", False):
        return _doctor_consistency(cfg, repair=getattr(args, "repair", False))
    if getattr(args, "check", False):
        # Scheduled checks return through the existing static helper here;
        # only the manual path below invokes the reviewer nonce probe.
        if getattr(args, "notify", False):
            return _doctor_check(cfg, page=True)
        return _doctor_check(cfg)
    root, impl, adapter = _ctx(cfg)
    ok = lambda b: f"{C['green']}ok{C['reset']}" if b else f"{C['red']}missing{C['reset']}"

    # Run outside a project, every check is "missing" and none of it is a fault.
    # Say that instead of printing a wall of red.
    if not impl and not os.path.isdir(os.path.join(root, cfg["mailbox"])):
        print(f"{C['yellow']}No project here.{C['reset']} {C['dim']}{root}{C['reset']}\n")
        ws = A.all_workspaces()[:5]
        if ws:
            print("Point at one of these:")
            for r in ws:
                mins = int((time.time() - r["mtime"]) / 60)
                age = f"{mins}m ago" if mins < 90 else f"{mins // 60}h ago"
                print(f"  {C['b']}ao -C {r['path']} doctor{C['reset']}  {C['dim']}{age}{C['reset']}")
        else:
            print(f"{C['dim']}No local agent sessions found. See docs/adapters.md.{C['reset']}")
        return
    print(f"root            {root}")
    # Configuration is optional: discovery covers the common case, so its absence
    # is a fact, not a fault. Red is reserved for things that need fixing.
    has_cfg = os.path.exists(os.path.join(root, ".ao", "config.json"))
    print(f"config          " + (f"{C['green']}.ao/config.json{C['reset']}" if has_cfg
                                 else f"{C['dim']}none — using auto-discovery{C['reset']}"))
    print(f"implementer     " + (f"{impl.get('adapter')} / {impl.get('session','')[:24]}"
                                 if impl else f"{C['red']}none found{C['reset']}"))
    msgs, _ = A.session_paths(cfg)
    print(f"transcript      {ok(bool(msgs and os.path.exists(msgs)))}")
    print(f"mailbox         {ok(os.path.isdir(os.path.join(root, cfg['mailbox'])))}")
    print(f"reviews         {ok(os.path.isdir(os.path.join(root, cfg['reviews'])))}")
    reviewer_probe = _reviewer_probe(cfg)
    probe_tone = C["green"] if reviewer_probe["ok"] else C["red"]
    print(
        f"reviewer probe  {probe_tone}{_reviewer_probe_text(reviewer_probe)}"
        f"{C['reset']}"
    )
    print(f"review budget   {C['dim']}{_review_budget_text(_review_timeout(cfg))}{C['reset']}")
    guard = _implementer_commit_guard(cfg)
    if guard:
        print(f"commit guard    {C['dim']}{guard}{C['reset']}")
    try:
        waiver_lines = A.open_waiver_report(root)
    except Exception as exc:
        waiver_lines = [f"{C['red']}waiver ledger is unreadable: {exc}{C['reset']}"]
    print(f"waivers         {len(waiver_lines) if waiver_lines else 'none open'}"
          + (" open" if waiver_lines else ""))
    for line in waiver_lines:
        print(f"                {C['dim']}{line}{C['reset']}")
    for line in _review_evidence_lines(cfg):
        print(line)
    for line in _architect_absence_lines(cfg):
        print(line)
    for line in _worktree_lines(cfg):
        print(line)
    for line in _store_bound_lines(cfg):
        print(line)
    for line in _backup_lines(cfg):
        print(line)
    print(f"quota source    {'keyflip' if A.runnable_binary('keyflip') else '—'}")
    # Optional capabilities announce themselves; the core never needs them (#82).
    for name, state, hint in _optional_features(cfg):
        print(f"optional        {name:<9} {state}" + (f"  {C['dim']}{hint}{C['reset']}" if state == "absent" else ""))
    key = A.project_key(root).lower()
    if os.name == "nt":
        # Windows schedules the watchdog with Task Scheduler, not launchd (#71).
        wd = subprocess.run(["schtasks", "/Query", "/TN", f"ao-watchdog-{key}"], capture_output=True,
                            text=True, encoding=UTF8, errors="replace").returncode == 0
    else:
        wd = _launchd_listed(f"com.agentorchestrator.watchdog.{key}")
    print(f"watchdog        {C['green']}running{C['reset']}" if wd else
          f"watchdog        {C['dim']}not installed — ao watchdog install{C['reset']}")
    err = A.last_nudge_error(root)
    if err:
        mins = int((time.time() - err.get("at", 0)) / 60)
        print(f"last restart    {C['red']}failed {mins}m ago (exit {err.get('code')}){C['reset']}")
        print(f"                {C['dim']}{err.get('tail','')[:110]}{C['reset']}")
    else:
        print(f"last restart    {C['dim']}no failures recorded{C['reset']}")
    hook_inventory = _ao_hook_inventory(root)
    if hook_inventory["error"]:
        detail = f"resolver failure: {hook_inventory['error']}"
        print(f"{'commit hook':<16}{C['yellow']}{detail}{C['reset']}  ao hooks status")
        print(f"{'push hook':<16}{C['yellow']}{detail}{C['reset']}  AO push-window hook unavailable")
    else:
        active = _active_hook_targets(hook_inventory)
        legacy_project = _project_enrollment(root)["state"] == "legacy"
        for role, label in (("pre-commit", "commit hook"), ("pre-push", "push hook")):
            target = active[role]
            base = _state_base(target["static_state"])
            current = base in ("current-local", "current-scoped")
            tone = C["green"] if current else C["yellow"]
            notes = []
            if role == "pre-commit" and not current:
                notes.append(
                    "adopt .ao-project first, then ao hooks install"
                    if legacy_project else "ao hooks install"
                )
            if role == "pre-push" and not current:
                notes.append("AO push-window hook unavailable")
            suffix = "  " + "; ".join(notes) if notes else ""
            print(
                f"{label:<16}{tone}{target['static_state']} / "
                f"{target['track_state']}{C['reset']}{suffix}"
            )
        for target in hook_inventory["targets"]:
            if target["active"] or target["role"] != "pre-commit" \
                    or target["reachability"] != "potentially-effective" \
                    or _state_base(target["static_state"]) not in (
                        "current-local", "current-scoped", "legacy", "ambiguous-ao"
                    ):
                continue
            print(
                f"{'commit misplaced':<16}{C['yellow']}{target['static_state']} / "
                f"{target['track_state']} / {target['directory_class']} — "
                f"{target['path']}{C['reset']}  ao hooks uninstall, then ao hooks install"
            )
    hook_proof = _hook_execution_probe(hook_inventory)
    proof_tone = C["green"] if hook_proof["installed"] else C["yellow"]
    print(f"{'commit proof':<16}{proof_tone}{_hook_probe_text(hook_proof)}{C['reset']}")
    print(f"{'checkout':<16}{_checkout_position(A.git_state(root))}")
    for line in _measurement_lines(cfg):
        print(line)
    # Can the *agent* run `ao`? A shell alias is invisible to a non-interactive
    # process, so steering that says "run your gates through ao lock" is an
    # instruction the agent cannot follow — and a disciplined agent then parks the
    # slice rather than working around it. That cost this project three parked
    # items and half a day. Check the child's PATH, not this shell's.
    from .watchdog import child_path
    reachable = shutil.which("ao", path=child_path())
    if reachable:
        # The architect is woken through a binary the watchdog resolves by version,
        # not by PATH order; say which one, and whether the last wake died on it.
        arch = cfg.get("architect") or {}
        if arch.get("argv"):
            from .watchdog import child_path, wake_error, STATE_DIR
            rb, rv = A.resolve_binary(arch["argv"][0], path=child_path())
            others = [c for c in A.binary_candidates(arch["argv"][0], child_path()) if c != rb]
            print(f"architect bin   {C['green'] if rb else C['red']}{rb or 'not found'}{C['reset']} {C['dim']}{rv}{C['reset']}"
                  + (f"  {C['dim']}({len(others)} older copy: {', '.join(others)}){C['reset']}" if others else ""))
            key = A.project_key(root)
            we = wake_error(os.path.join(STATE_DIR, f"escalate-{key}.log"))
            if we:
                text, used, when = we["text"], we["binary"], we["when"]
                print(f"last wake       {C['red']}failed{C['reset']} {when} [{used or '?'}]: {text[:90]}")
                if not used:
                    print(f"                {C['dim']}binary not recorded (older log); the next wake uses the one above{C['reset']}")
                elif used != f"{rb} {rv}":
                    print(f"                {C['dim']}a different binary resolves now; the next wake will use it{C['reset']}")
                else:
                    update = next((adapter["detect"]["update"] for adapter in A.package_adapters().values()
                                   if (adapter.get("detect") or {}).get("update")
                                   and os.path.basename(arch["argv"][0]) in A.adapter_binaries(adapter)), None)
                    how = f" ({' '.join(update)})" if update else ""
                    print(f"                {C['yellow']}same binary — update it{how} or remove the stale copy{C['reset']}")
        # Liveness and channels. A watchdog nobody can prove is alive, and an orange
        # alarm with no channel beyond the desktop, are both silent failures.
        hb = A.heartbeat_age(root)
        if hb is None:
            print(f"last tick       {C['yellow']}never{C['reset']} {C['dim']}(no heartbeat file yet){C['reset']}")
        else:
            tone = C['green'] if hb < 360 else C['red']
            print(f"last tick       {tone}{hb // 60}m {hb % 60}s ago{C['reset']}"
                  + (f"  {C['red']}watchdog is not running its cycles{C['reset']}" if hb >= 360 else ""))
        from . import telegram as _tg, email as _em
        tg_ok, em_ok = bool(_tg.config()), bool(_em.config())
        print(f"channels        desktop {C['green']}on{C['reset']} · telegram "
              f"{C['green'] if tg_ok else C['yellow']}{'on' if tg_ok else 'off'}{C['reset']} · e-mail "
              f"{C['green'] if em_ok else C['yellow']}{'on' if em_ok else 'off'}{C['reset']}"
              + ("" if (tg_ok or em_ok) else f"  {C['yellow']}orange alarms reach nothing but the desktop — ao email setup{C['reset']}"))
        try:
            from .watchdog import load_state as _ls
            _st = _ls(root)
            if _st.get("arch_quota_until", 0) > time.time():
                print(f"architect       {C['yellow']}at quota{C['reset']} until "
                      f"{time.strftime('%H:%M', time.localtime(_st['arch_quota_until']))}")
        except Exception:
            pass
        alive = A.active_alarms(A.project_key(root))
        if alive:
            worst = max(alive, key=lambda e: {"yellow": 0, "orange": 1, "red": 2}.get(e.get("ring"), 0))
            print(f"alarms          {C['red'] if worst['ring'] == 'red' else C['yellow']}{len(alive)} live, worst {worst['ring']}{C['reset']}  {C['dim']}ao alarms{C['reset']}")
        # Things that fail silently unless someone asks: the transcript format (a CLI
        # update changes it and every status goes blind), the credits running out
        # before the reset, the external ping, the push hook, the switches.
        try:
            msgs_p, _ = A.session_paths(cfg)
            if msgs_p and os.path.exists(msgs_p):
                n_recs = len(A.read_tail(msgs_p, 2_000_000))
                age_m = int((time.time() - os.path.getmtime(msgs_p)) / 60)
                bad = n_recs == 0 and age_m < 60
                print(f"transcript      {C['red'] if bad else C['green']}{n_recs} records parsed{C['reset']}, last write {age_m}m ago"
                      + (f"  {C['red']}fresh file, nothing parsed — the CLI's format changed; update the adapter{C['reset']}" if bad else ""))
        except Exception:
            pass
        # The implementer's own account only, or that its adapter declares none (ACCOUNT-READERS).
        for line in _doctor_credit_lines(cfg):
            print(line)
        print(f"ping            {C['green'] + 'configured' + C['reset'] if A.ping_url(root) else C['yellow'] + 'off' + C['reset'] + '  ao pings setup'}")
        from . import features as _F
        print(f"features        {sum(_F.switches(cfg).values())}/{len(_F.ORDER)} on  {C['dim']}ao cost --features for what "
              f"each spent{C['reset']}")
        print(f"ao for agents   {C['green']}{reachable}{C['reset']}")
    else:
        print(f"ao for agents   {C['red']}not on a spawned agent's PATH{C['reset']}")
        print(f"                {C['dim']}a shell alias does not count — "
              f"uv tool install ao-orchestrator{C['reset']}")

    from . import skillkit as _skillkit
    for steering in _skillkit.steering_dirs(root):
        steer = os.path.join(root, *steering.split("/"))
        if not os.path.isdir(steer) or reachable:
            continue
        refs = [f for f in os.listdir(steer)
                if f.endswith(".md") and "ao " in open(os.path.join(steer, f),
                                                       errors="replace", encoding=UTF8).read()]
        if refs:
            print(f"                {C['yellow']}steering references it: "
                  f"{', '.join(refs)}{C['reset']}")

    # Two copies of `ao` on one machine is an ambiguity that bites silently: a
    # shell alias to a git checkout and a package install answer to the same
    # name, drift apart after the next commit, and which one runs depends on
    # how it was invoked. The second pilot's agent caught it on its first day.
    cands = []
    for cand in (shutil.which("ao"), os.path.join(A.HOME, ".local", "bin", "ao"),
                 os.path.join(A.REPO, "bin", "ao")):
        if cand and os.path.exists(cand):
            real = os.path.realpath(cand)
            if real not in [os.path.realpath(c) for c in cands]:
                cands.append(cand)
    if len(cands) > 1:
        print(f"ao binaries     {C['yellow']}{len(cands)} distinct{C['reset']} — "
              f"{C['dim']}which one runs depends on how it is invoked{C['reset']}")
        for c in cands:
            print(f"                {C['dim']}{c} → {os.path.realpath(c)}{C['reset']}")
        print(f"                {C['dim']}keep one: drop the shell alias, or "
              f"uv tool uninstall ao-orchestrator{C['reset']}")
    return 0 if reviewer_probe["ok"] else 1
