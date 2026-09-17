"""Status: the status screen, watch, fleet, tail and mail commands.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


def _ctx(cfg):
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", ""), root) if impl else {}
    return root, impl, adapter


def _bar(pct, width=20):
    filled = max(0, min(width, int(pct / (100 / width))))
    col = C["red"] if pct > 85 else C["yellow"] if pct > 70 else C["green"]
    return f"{col}{'█' * filled}{'░' * (width - filled)}{C['reset']} {pct:.0f}%"


def _checkout_position(g):
    """One line for where a checkout stands: never an ahead count on its own (#89)."""
    if g.get("base") is None:
        return f"{C['yellow']}no remote default branch to compare against{C['reset']}"
    text = f"{C['dim']}{g['ahead']} ahead / {g['behind']} behind {g['base']}{C['reset']}"
    if g.get("merged"):
        return text + f"  {C['yellow']}already merged; this checkout is {g['behind']} commits old{C['reset']}"
    if g.get("behind"):
        return text + f"  {C['yellow']}behind{C['reset']}"
    return text


def _idle_answer_text(idle, now=None):
    """How long the implementer has had nothing to do, and what would change that (#96)."""
    since = idle.get("since") or 0
    minutes = max(0, int(((now or time.time()) - since) / 60))
    return (f"since {time.strftime('%H:%M', time.localtime(since))} ({minutes // 60}h {minutes % 60}m): "
            "answered a nudge without changing anything; waiting for the board, the backlog, "
            "a decision or mail")


def _window_label(hours):
    return f"{hours:g}h" if hours < 48 else f"{hours / 24:g}d"


def _throughput_lines(tp):
    """The throughput section as two plain lines: the counts, then the state and why (#91)."""
    counts = (f"staged {tp['staged']} · landed {tp['landed']} · decisions {tp['decisions_asked']} asked, "
              f"{tp['decisions_open']} waiting")
    if tp.get("oldest_open_minutes") is not None:
        counts += f" (oldest {tp['oldest_open_minutes'] // 60}h {tp['oldest_open_minutes'] % 60}m)"
    stall = tp.get("stall")
    if tp["state"] == "stalled" and stall:
        paths = ", ".join(stall["paths"][:3]) + (" …" if len(stall["paths"]) > 3 else "")
        state = (f"stalled {stall['minutes'] // 60}h {stall['minutes'] % 60}m on a staged candidate "
                 f"({paths or 'no paths recorded'}) — {stall['reason']}")
    else:
        state = {"landing": "landing: candidates are reaching commits",
                 "staging": "staging: candidates verified, none landed yet",
                 "busy": "busy: the implementer took turns and staged nothing",
                 "idle": "idle: no turns in this window"}[tp["state"]]
    return counts, state


def render(cfg, msg_count=8, width=None, max_lines=None, window_hours=24.0):
    """Render the panel. When max_lines is given the output never exceeds it:
    the fixed sections are laid out first and the message log — the only elastic
    part — takes whatever is left. A panel taller than the window scrolls, and a
    scrolled panel stacks its own headers on every refresh."""
    root, impl, adapter = _ctx(cfg)
    w = min(width or shutil.get_terminal_size((120, 40)).columns, 130)
    L = []
    a = L.append

    name = cfg.get("project") or os.path.basename(root)
    a(f"{C['b']}{C['cyan']}{'═' * w}{C['reset']}")
    a(f"{C['b']}  {name.upper()}{C['reset']}{C['dim']}   agent-orchestrator   "
      f"{datetime.now():%d %b %H:%M:%S}{C['reset']}")
    a(f"{C['b']}{C['cyan']}{'═' * w}{C['reset']}")

    # implementer state
    if impl:
        state, age, desc = A.busy(cfg, adapter)
        label = {"working": ("● WORKING", C["green"]),
                 "slowing": ("◐ slowing", C["yellow"]),
                 "stopped": ("■ STOPPED", C["red"]),
                 "idle": ("○ IDLE", C["red"])}.get(state, ("? unknown", C["dim"]))
        txt, col = label
        agestr = f"last write {age // 60}m {age % 60}s ago" if age is not None else "no transcript"
        a(f"\n{col}{C['b']}{txt}{C['reset']}  {C['dim']}{impl.get('adapter','?')} · {agestr}{C['reset']}")
        for i, ln in enumerate(textwrap.wrap(desc, w - 6)[:2]):
            a(f"  {C['dim']}↳{C['reset']} {ln}" if i == 0 else f"    {ln}")
        if state in ("stopped", "idle"):
            # The agent may be writing in a secondary project; say where (#22).
            elsewhere = A.working_elsewhere(cfg, S.get(cfg, "watchdog.idle_minutes") * 60)
            if elsewhere:
                a(f"  {C['green']}↳ working in {elsewhere['name']}{C['reset']}  {C['dim']}"
                  f"{elsewhere['root']} · last write {int(elsewhere['age'])}s ago there{C['reset']}")
    else:
        a(f"\n{C['yellow']}No implementer session found for this workspace.{C['reset']}")
        a(f"   {C['dim']}{root}{C['reset']}")
        ws = A.all_workspaces()[:5]
        if ws:
            a(f"\n   Run it from a project, or point at one:")
            for r in ws:
                mins = int((time.time() - r["mtime"]) / 60)
                age = f"{mins}m ago" if mins < 90 else f"{mins // 60}h ago"
                a(f"     {C['b']}ao -C {r['path']}{C['reset']}  {C['dim']}{age}{C['reset']}")
        else:
            a(f"   {C['dim']}No local agent sessions found at all. See docs/adapters.md.{C['reset']}")

    # telemetry
    if impl:
        msgs_path, _ = A.session_paths(cfg)
        recs = A.read_tail(msgs_path, 12_000_000) if msgs_path else []
        tel = A.telemetry(recs, adapter, msgs_path)
        q = A.quota(adapter)
        if tel.get("ctx") is not None or tel.get("last") or q:
            a(f"\n{C['b']}{C['mag']}── QUOTA / CONTEXT {'─' * max(0, w - 20)}{C['reset']}")
            if tel.get("ctx") is not None:
                note = "  ← start a fresh session" if tel["ctx"] > 85 else ""
                a(f"   context  {_bar(tel['ctx'])}{C['dim']}{note}{C['reset']}")
            if tel.get("last"):
                lu, lt = tel["last"]
                avg = tel["total"] / max(1, tel["turns"])
                warn = f"  {C['yellow']}⚠ {lu/max(avg,1):.1f}× average{C['reset']}" if lu > 2 * avg else ""
                # The subagents a turn started spent inside its cost, and inside the total.
                delegated = f", {tel['delegated']:.0f} delegated" if tel.get("delegated") else ""
                a(f"   cost     last turn {C['b']}{lu:.0f}{C['reset']} {tel['unit']} "
                  f"({lt} tool calls) · {tel['turns']} turns, total {C['b']}{tel['total']:.0f}{C['reset']}"
                  f"{C['dim']} (avg {avg:.0f}{delegated}){C['reset']}{warn}")
            if q:
                # keyflip reports machine-wide provider windows, which are NOT the
                # implementer's own pool — Kiro bills credits, Claude Code bills a
                # 5h window. Labelling it plainly avoids reading someone else's
                # quota as this agent's.
                a(f"   {C['dim']}other tools on this machine (not {impl.get('adapter','this agent')}'s pool):{C['reset']}")
                for line in q:
                    a(f"     {C['dim']}{line}{C['reset']}")

        msg_records = recs

    # problems — only rendered when there is one, so an empty panel means healthy
    if impl:
        nudge_err = A.last_nudge_error(root)
        errs = A.recent_errors(recs, 2, adapter)
        spin = A.spinning(root)
        from .watchdog import load_state as _load_state
        idle_answer = (_load_state(root) or {}).get("idle_answer")
        if nudge_err or errs or spin or idle_answer:
            a(f"\n{C['b']}{C['red']}── PROBLEMS {'─' * max(0, w - 13)}{C['reset']}")
            if idle_answer:
                a(f"   {C['yellow']}nothing to do{C['reset']} "
                  f"{C['dim']}{_idle_answer_text(idle_answer)}{C['reset']}")
            if spin:
                a(f"   {C['red']}spinning{C['reset']} {C['dim']}{spin}m busy, nothing committed "
                  f"or changed{C['reset']}")
                a(f"     {C['dim']}activity is not progress — re-specify, split, or check for "
                  f"a wait loop{C['reset']}")
            if nudge_err:
                mins = int((time.time() - nudge_err.get("at", 0)) / 60)
                a(f"   {C['red']}restart failed{C['reset']} {C['dim']}{mins}m ago · "
                  f"exit {nudge_err.get('code')}{C['reset']}")
                for ln in textwrap.wrap(nudge_err.get("tail", ""), w - 8)[:2]:
                    a(f"     {C['dim']}{ln}{C['reset']}")
                a(f"     {C['dim']}full log: {_home_relative(A.project_file(root, 'nudge-log'))}{C['reset']}")
            for hh, text in errs:
                for i, ln in enumerate(textwrap.wrap(text, w - 12)[:2]):
                    a(f"   {C['dim']}{hh}{C['reset']} {C['yellow']}agent error{C['reset']}  {ln}"
                      if i == 0 else f"        {C['dim']}│{C['reset']}  {ln}")

    # A returned review is handled before new work starts (#28, W1).
    for state in A.returned_reviews(root):
        a(f"\n   {C['yellow']}{C['b']}REVIEW RETURNED{C['reset']} for {state.get('slice') or 'a slice'}: "
          f"{state['id']} {state.get('verdict') or state.get('state')} — handle it before new work "
          f"({C['b']}ao review collect {state['id']}{C['reset']})")
    # reviews + round budget
    revs = A.reviews(root, cfg["reviews"])
    if revs:
        rn = A.rounds(root, cfg["reviews"])
        budget = S.get(cfg, "round_budget")
        a(f"\n{C['b']}{C['mag']}── REVIEWS {'─' * max(0, w - 12)}{C['reset']}")
        if rn > budget:
            a(f"   {C['red']}⚠ round {rn}/{budget} — over budget: re-specify, split, "
              f"or change actor{C['reset']}")
        elif rn:
            a(f"   {C['dim']}round {rn}/{budget}{C['reset']}")
        for f, v in revs:
            col = C["green"] if "APPROVED" in v.upper() else C["yellow"]
            a(f"   {f.split('-pr')[0]}   {col}{v}{C['reset']}")

    # throughput: what the window produced, so busy-and-landing-nothing shows (#91)
    if impl:
        tp = A.throughput(root, cfg, hours=window_hours)
        label = _window_label(window_hours)
        a(f"\n{C['b']}{C['mag']}── THROUGHPUT {label} {'─' * max(0, w - 16 - len(label))}{C['reset']}")
        counts, state = _throughput_lines(tp)
        colour = {"stalled": C["red"], "busy": C["yellow"], "idle": C["dim"],
                  "landing": C["green"]}.get(tp["state"], "")
        a(f"   {C['dim']}{counts}{C['reset']}")
        # A stall names paths and a reason; keep it to two lines so a bounded panel stays bounded.
        for i, ln in enumerate(textwrap.wrap(state, w - 6, break_on_hyphens=False)[:2]):
            a(f"   {colour}{ln}{C['reset']}" if i == 0 else f"     {C['dim']}{ln}{C['reset']}")

    # git + mail
    g = A.git_state(root)
    a(f"\n{C['b']}{C['mag']}── REPOSITORY {'─' * max(0, w - 15)}{C['reset']}")
    for ln in g["log"][:3]:
        if ln:
            a(f"   {ln[:w-5]}")
    a(f"   {C['dim']}{len(g['dirty'])} files uncommitted · {C['reset']}{_checkout_position(g)}")

    mail = A.mailbox(root, cfg["mailbox"])
    a(f"\n   {C['b']}Mailbox:{C['reset']} " +
      (", ".join(mail) if mail else f"{C['dim']}empty{C['reset']}"))
    for line in _mailbox_banner(cfg):
        a(f"   {line}")
    unseen = A.unseen_messages(root, cfg)
    if unseen:
        oldest = unseen[0]
        a(f"   {C['yellow']}oldest unseen{C['reset']} {oldest['id']}  {C['dim']}{oldest['class']}, "
          f"{int(oldest['age'] // 60)}m" + (f" · {len(unseen) - 1} more unseen" if len(unseen) > 1 else "")
          + f"{C['reset']}")

    # Board — one line, because a parked item is invisible by construction: work
    # moved on past it, so no other signal in this panel looks wrong.
    bd = A.board(root)
    if any(bd.values()):
        counts = " · ".join(f"{len(bd[k])} {k}" for k in
                            ("running", "blocked", "queued", "verified", "done") if bd[k])
        a(f"   {C['b']}Board:{C['reset']}   {counts}")
        for it in bd["blocked"]:
            why = it["notes"].get("needs") or it["notes"].get("waiting") or "reason not recorded"
            for i, ln in enumerate(textwrap.wrap(f"{it['id']}  {it['title']} — {why}", w - 14)[:2]):
                a(f"     {C['red']}⊘{C['reset']} {ln}" if i == 0 else f"       {C['dim']}{ln}{C['reset']}")

    # The message log is elastic and goes last, so a short window drops history
    # rather than the state you actually steer by.
    msgs_block = []
    try:
        ms = A.messages(msg_records, msg_count, adapter)
    except NameError:
        ms = []
    if ms:
        msgs_block.append(f"\n{C['b']}{C['mag']}── RECENT MESSAGES {'─' * max(0, w - 20)}{C['reset']}")
        for hh, kind, text in ms:
            tag = f"{C['blue']}YOU  {C['reset']}" if kind == "user" else f"{C['cyan']}AGENT{C['reset']}"
            for i, ln in enumerate(textwrap.wrap(text, w - 14)[:3]):
                msgs_block.append(f"   {C['dim']}{hh}{C['reset']} {tag}  {ln}" if i == 0
                                  else f"        {C['dim']}│{C['reset']}      {ln}")

    if max_lines is None:
        return "\n".join(L + msgs_block)

    # Truncate by real lines, not by list elements: a section header carries its
    # own leading blank line, so element count and line count are not the same.
    fixed = "\n".join(L).split("\n")
    if len(fixed) >= max_lines:
        return "\n".join(fixed[:max_lines])
    room = max_lines - len(fixed)
    msg_lines = "\n".join(msgs_block).split("\n") if msgs_block else []
    if len(msg_lines) > room:
        msg_lines = msg_lines[:max(0, room - 1)]
        if msg_lines:
            msg_lines.append(f"   {C['dim']}… older messages hidden (window too short){C['reset']}")
    return "\n".join(fixed + msg_lines)


def _mailbox_banner(cfg):
    """Lines naming the marked messages for the role running this command, and how many others wait (#29).

    A person, or a caller with no AO_ROLE, sees what is marked for either role.
    """
    root = cfg["root"]
    try:
        urgent = A.urgent_messages(root, cfg, A.invoking_role())
        waiting = len(A.mailbox(root, cfg.get("mailbox", "agent-mail")))
        # Named here is shown to its reader (#30).
        A.mail_seen(root, [m["id"] for m in urgent], A.invoking_role() or "person")
    except OSError:
        return []
    lines = [f"{C['red']}{C['b']}URGENT{C['reset']} for the {m['to']}: {C['b']}{m['title']}{C['reset']}  "
             f"{C['dim']}{m['id']}{C['reset']}" for m in urgent]
    others = waiting - len(urgent)
    if others > 0:
        lines.append(f"{C['dim']}{others} other message(s) waiting in {cfg.get('mailbox', 'agent-mail')}/{C['reset']}")
    return lines


def cmd_status(cfg, args):
    print(render(cfg, args.messages, window_hours=getattr(args, "window", None) or 24.0))


def cmd_watch(cfg, args):
    sys.stdout.write("\033[?1049h\033[?25l")   # alternate screen, hidden cursor
    try:
        while True:
            size = shutil.get_terminal_size((120, 40))
            if getattr(args, "all", False):
                lines = render_fleet(size.columns)
                out = "\n".join(lines[:max(4, size.lines - 2)])
            else:
                out = render(cfg, args.messages, size.columns, size.lines - 2)
            sys.stdout.write("\033[H\033[J" + out +
                             f"\n{C['dim']}  every {args.interval}s · Ctrl+C to exit{C['reset']}")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")  # cursor back, restore scrollback
        sys.stdout.flush()


def _fleet_rows():
    """One row per workspace with a local agent session."""
    rows = []
    for ws in A.all_workspaces():
        root = ws["path"]
        try:
            cfg = A.load_config(root)
        except Exception:
            continue
        impl = cfg.get("implementer") or {}
        adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
        state, age, desc = A.busy(cfg, adapter) if impl else ("unknown", None, "")
        bd = A.board(root)
        g = A.git_state(root)
        rows.append({"name": cfg.get("project") or os.path.basename(root), "root": root,
                     "state": state, "age": age, "desc": desc,
                     "queued": len(bd["queued"]), "blocked": len(bd["blocked"]),
                     "running": len(bd["running"]), "dirty": len(g["dirty"]),
                     "mail": len(A.mailbox(root, cfg["mailbox"])),
                     "spin": A.spinning(root),
                     "sources": bool(A.sources(root))})
    # what needs a human first: spinning, then blocked, then idle
    rows.sort(key=lambda r: (r["spin"] is None, r["blocked"] == 0, r["state"] != "idle"))
    return rows


def render_fleet(width=None):
    w = min(width or shutil.get_terminal_size((120, 40)).columns, 130)
    L = [f"{C['b']}{C['cyan']}{'═' * w}{C['reset']}",
         f"{C['b']}  ALL PROJECTS{C['reset']}{C['dim']}   agent-orchestrator   "
         f"{datetime.now():%d %b %H:%M:%S}{C['reset']}",
         f"{C['b']}{C['cyan']}{'═' * w}{C['reset']}"]
    rows = _fleet_rows()
    if not rows:
        L.append(f"\n{C['dim']}No workspaces with a local agent session.{C['reset']}")
        return L
    for r in rows:
        dot, col = {"working": ("●", C["green"]), "slowing": ("◐", C["yellow"]),
                    "stopped": ("■", C["red"]),
                    "idle": ("○", C["red"])}.get(r["state"], ("?", C["dim"]))
        agestr = f"{r['age'] // 60}m" if r["age"] is not None else "—"
        flags = []
        if r["spin"]:
            flags.append(f"{C['red']}spinning {r['spin']}m{C['reset']}")
        if r["blocked"]:
            flags.append(f"{C['red']}{r['blocked']} blocked{C['reset']}")
        if r["mail"]:
            flags.append(f"{C['cyan']}{r['mail']} mail{C['reset']}")
        if not r["sources"]:
            flags.append(f"{C['dim']}no source{C['reset']}")
        L.append(f"\n {col}{dot}{C['reset']} {C['b']}{r['name'][:22]:<22}{C['reset']}"
                 f"{C['dim']}{r['state']:<8} {agestr:>4}{C['reset']}  "
                 f"q{r['queued']} r{r['running']} "
                 f"{C['dim']}·{C['reset']} {r['dirty']} dirty"
                 + ("   " + "  ".join(flags) if flags else ""))
        if r["desc"]:
            for ln in textwrap.wrap(r["desc"], w - 8)[:1]:
                L.append(f"     {C['dim']}↳ {ln}{C['reset']}")
    return L


def cmd_fleet(cfg, args):
    for ln in render_fleet():
        print(ln)
    return 0


def cmd_tail(cfg, args):
    for line in _mailbox_banner(cfg):
        print(line)
    msgs_path, _ = A.session_paths(cfg)
    if not msgs_path:
        print("No implementer session found.", file=sys.stderr)
        return 1
    _, _, adapter = _ctx(cfg)
    for hh, kind, text in A.messages(A.read_tail(msgs_path), args.n, adapter):
        who = "YOU  " if kind == "user" else "AGENT"
        print(f"--- {hh} [{who}] ---\n{text}\n")


def cmd_mail(cfg, args):
    root = cfg["root"]
    d = os.path.join(root, cfg["mailbox"])
    if args.action in ("list", "read"):
        for line in _mailbox_banner(cfg):
            print(line)
    if args.action in ("list", "read"):
        # What a reader's own command lists is seen by that reader, not yet handled (#30).
        role = A.invoking_role()
        shown = [f for f in A.mailbox(root, cfg["mailbox"]) if A.addressed_to(f, cfg, role)]
        A.mail_seen(root, shown, role or "person")
    if args.action == "list":
        for f in A.mailbox(root, cfg["mailbox"]):
            print(f)
    elif args.action == "read":
        for f in A.mailbox(root, cfg["mailbox"]):
            print(f"===== {f} =====\n{open(os.path.join(d, f), encoding=UTF8).read()}")
    elif args.action == "send":
        os.makedirs(d, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        topic = A.safe_slug((args.topic or "note").lower())
        impl, arch = A.mail_names(cfg)
        name = f"{stamp}-{arch}-to-{impl}-{A.safe_slug(args.type.upper(), 'INFO')}-{topic}.md"
        body = args.body if args.body else sys.stdin.read()
        A.write_mail(root, cfg, name, body.rstrip() + "\n",
                     {"kind": args.type.lower(), "from": arch, "to": impl,
                      "class": getattr(args, "mail_class", None)})
        print(name)
    elif args.action == "ack":
        import fnmatch
        pattern = args.type if args.type and args.type != "INFO" else None
        if not pattern:
            print("usage: ao mail ack <file-or-glob>   e.g. ao mail ack 'watchdog-to-*-ANOMALY-*'"); return 2
        hits = [f for f in A.mailbox(root, cfg["mailbox"]) if f == pattern or fnmatch.fnmatch(f, pattern)]
        if not hits:
            print(f"no message matches {pattern}"); return 1
        store = A.mail_store_mode(root) == "append-only"
        if store:
            A.ingest_mail(root, cfg)
        for f in hits:
            if store:
                # Handling is a record; the message stays in the store (#80).
                A.handle_message(root, cfg, f, A.invoking_role() or "person", args.body or "processed")
            else:
                os.remove(os.path.join(d, f))
            A.mail_ledger_append(root, {"event": "consumed", "id": f, "outcome": args.body or "processed"})
            print(f"  {C['green']}acked{C['reset']} {f}")
    elif args.action == "sync":
        A.ingest_mail(root, cfg)
        try:
            commit, where = A.sync_mail(root, cfg)
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            print(f"{C['red']}not synced{C['reset']}: {exc}")
            return 1
        print(f"{C['green']}synced{C['reset']} the message store {commit[:12]} → {where}")
    elif args.action == "compact":
        days = float(args.type) if args.type and args.type != "INFO" else 30.0
        compacted = A.compact_messages(root, days)
        print(f"compacted {len(compacted)} stored message(s) older than {days:g} day(s) to stubs; "
              "their bodies stay in .ao/mail/archive/")
    elif args.action == "log":
        A.reconcile_mail_ledger(root, cfg)
        for r in A.mail_log(root, 40):
            when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
            extra = f"  stood {r['stood_s'] // 60}m" if r.get("stood_s") is not None else ""
            print(f"  {when}  {r.get('event', '?'):<9} {r.get('id', '')[:70]}{extra}")
    elif args.action == "search":
        if not args.type or args.type == "INFO":
            print("usage: ao mail search <text>"); return 2
        for r in A.mail_search(root, args.type):
            when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
            print(f"  {when}  {r.get('kind') or '':<9} {r['id'][:60]}  {C['dim']}{r.get('summary', '')[:70]}{C['reset']}")
