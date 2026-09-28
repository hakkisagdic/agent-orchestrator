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


def _session_facts(state):
    """A role's session as the JSON documents name it: its id, how ao found it, and why it could not (JSON-OUTPUT)."""
    return {"id": state.get("session"), "how": state.get("how"), "why": state.get("why")} if state else None


def _unresolved_session(seconds, session):
    """The session ao could not resolve, when that is why no transcript was read; None otherwise.

    An `auto` ao could not resolve says why, where it would read as a transcript gone missing
    (SESSION-IDENTITY).
    """
    return session if seconds is None and session and not session["id"] and session["why"] else None


def status_facts(cfg, msg_count=8, window_hours=24.0, shown=False):
    """What the status panel shows, read once: `render` draws it and `ao status --json` prints it (JSON-OUTPUT).

    docs/surfaces.md lists the keys. What the panel reads only for an implementer - its session,
    telemetry, problems, throughput and messages - is null or empty without one, and the workspaces
    a person could point ao at instead are read only then. The reads run in the panel's order.

    `shown` is for the panel, whose banner puts the urgent messages in front of its reader: naming
    them records them as seen, as drawing them always did (#30), before the unseen ones are read.
    The document records nothing, as `ao_status` records nothing. A program reads it - a status bar,
    a scheduled job - under whatever AO_ROLE it inherited, and a message recorded as seen by a
    reader who never saw it would never climb the watchdog's ladder.
    """
    root, impl, adapter = _ctx(cfg)
    facts = {"project": cfg.get("project") or os.path.basename(root), "root": root, "at": int(time.time()),
             "implementer": None, "workspaces": None, "telemetry": None, "problems": None}
    recs = []
    if impl:
        state, age, desc = A.busy(cfg, adapter)
        session = _session_facts(A.session_state(cfg, "implementer"))
        # The agent may be writing in a secondary project; the panel says where (#22).
        elsewhere = A.working_elsewhere(cfg, S.get(cfg, "watchdog.idle_minutes") * 60) \
            if state in ("stopped", "idle") else None
        facts["implementer"] = {
            "adapter": impl.get("adapter"), "state": state, "seconds_since_write": age, "doing": desc,
            "session": session,
            "working_elsewhere": {"name": elsewhere["name"], "root": elsewhere["root"],
                                  "seconds_since_write": int(elsewhere["age"])} if elsewhere else None}
        msgs_path, _ = A.session_paths(cfg)
        recs = A.read_tail(msgs_path, 12_000_000) if msgs_path else []
        tel = A.telemetry(recs, adapter, msgs_path)
        last = tel.get("last")
        facts["telemetry"] = {
            "context_percent": tel.get("ctx"), "cost_unit": tel.get("unit"), "turns": tel.get("turns"),
            "cost_total": tel.get("total"), "cost_average": tel["total"] / max(1, tel["turns"]) if last else None,
            "cost_delegated": tel.get("delegated"),
            "last_turn_cost": last[0] if last else None, "last_turn_tool_calls": last[1] if last else None,
            # keyflip's provider windows are the machine's, never the implementer's own pool.
            "machine_quota": A.quota(adapter)}
        nudge_err = A.last_nudge_error(root)
        errs = A.recent_errors(recs, 2, adapter)
        spin = A.spinning(root)
        from .watchdog import load_state as _load_state
        idle_answer = (_load_state(root) or {}).get("idle_answer")
        facts["problems"] = {
            "nothing_to_do_since": (idle_answer.get("since") or 0) if idle_answer else None,
            "spinning_minutes": spin,
            "nudge_error": {"at": nudge_err.get("at", 0), "code": nudge_err.get("code"),
                            "tail": nudge_err.get("tail", ""), "log": A.project_file(root, "nudge-log")}
            if nudge_err else None,
            "agent_errors": [{"time": hh, "text": text} for hh, text in errs]}
    else:
        facts["workspaces"] = [{"path": ws["path"], "seconds_since_write": int(time.time() - ws["mtime"])}
                               for ws in A.all_workspaces()[:5]]
    facts["returned_reviews"] = [{"id": review["id"], "slice": review.get("slice"), "verdict": review.get("verdict"),
                                  "state": review.get("state")} for review in A.returned_reviews(root)]
    revs = A.reviews(root, cfg["reviews"])
    facts["reviews"] = [{"file": name, "verdict": verdict} for name, verdict in revs]
    facts["rounds"] = {"spent": A.rounds(root, cfg["reviews"]), "budget": S.get(cfg, "round_budget")} \
        if revs else None
    facts["throughput"] = A.throughput(root, cfg, hours=window_hours) if impl else None
    g = A.git_state(root)
    facts["repository"] = {"log": [line for line in g["log"][:3] if line], "dirty_files": len(g["dirty"]),
                           "ahead": int(g["ahead"]) if str(g["ahead"]).isdigit() else None,
                           "behind": g.get("behind"), "base": g.get("base"), "merged": bool(g.get("merged"))}
    waiting = A.mailbox(root, cfg["mailbox"])
    marked = _marked_mail(cfg, shown)
    facts["mail"] = {
        "dir": cfg.get("mailbox", "agent-mail"), "waiting": waiting,
        "urgent": None if marked is None else [{"id": m["id"], "to": m["to"], "title": m["title"]}
                                               for m in marked[0]],
        # Read after the panel's banner recorded its messages as seen, so the panel does not call them
        # unseen (#30). The document recorded nothing: a marked message nobody was shown is in both lists.
        "unseen": [{"id": m["id"], "class": m["class"], "age_seconds": int(m["age"])}
                   for m in A.unseen_messages(root, cfg)]}
    bd = A.board(root)
    facts["board"] = {"counts": {state: len(items) for state, items in bd.items()},
                      "blocked": [{"id": it["id"], "title": it["title"],
                                   "why": it["notes"].get("needs") or it["notes"].get("waiting") or None}
                                  for it in bd["blocked"]]}
    facts["messages"] = [{"time": hh, "role": kind, "text": text}
                         for hh, kind, text in (A.messages(recs, msg_count, adapter) if impl else [])]
    return facts


def render(cfg, msg_count=8, width=None, max_lines=None, window_hours=24.0):
    """Render the panel from status_facts. When max_lines is given the output never exceeds it:
    the fixed sections are laid out first and the message log — the only elastic
    part — takes whatever is left. A panel taller than the window scrolls, and a
    scrolled panel stacks its own headers on every refresh."""
    facts = status_facts(cfg, msg_count, window_hours, shown=True)
    impl = cfg.get("implementer") or {}
    w = min(width or shutil.get_terminal_size((120, 40)).columns, 130)
    L = []
    a = L.append

    a(f"{C['b']}{C['cyan']}{'═' * w}{C['reset']}")
    a(f"{C['b']}  {facts['project'].upper()}{C['reset']}{C['dim']}   agent-orchestrator   "
      f"{datetime.fromtimestamp(facts['at']):%d %b %H:%M:%S}{C['reset']}")
    a(f"{C['b']}{C['cyan']}{'═' * w}{C['reset']}")

    # implementer state
    implementer = facts["implementer"]
    if implementer:
        txt, col = {"working": ("● WORKING", C["green"]),
                    "slowing": ("◐ slowing", C["yellow"]),
                    "stopped": ("■ STOPPED", C["red"]),
                    "idle": ("○ IDLE", C["red"])}.get(implementer["state"], ("? unknown", C["dim"]))
        age, desc = implementer["seconds_since_write"], implementer["doing"]
        agestr = f"last write {age // 60}m {age % 60}s ago" if age is not None else "no transcript"
        unresolved = _unresolved_session(age, implementer["session"])
        if unresolved:
            agestr, desc = f"session {unresolved['how']}", unresolved["why"]
        a(f"\n{col}{C['b']}{txt}{C['reset']}  {C['dim']}{impl.get('adapter','?')} · {agestr}{C['reset']}")
        for i, ln in enumerate(textwrap.wrap(desc, w - 6)[:2]):
            a(f"  {C['dim']}↳{C['reset']} {ln}" if i == 0 else f"    {ln}")
        elsewhere = implementer["working_elsewhere"]
        if elsewhere:
            a(f"  {C['green']}↳ working in {elsewhere['name']}{C['reset']}  {C['dim']}"
              f"{elsewhere['root']} · last write {elsewhere['seconds_since_write']}s ago there{C['reset']}")
    else:
        a(f"\n{C['yellow']}No implementer session found for this workspace.{C['reset']}")
        a(f"   {C['dim']}{facts['root']}{C['reset']}")
        if facts["workspaces"]:
            a("\n   Run it from a project, or point at one:")
            for ws in facts["workspaces"]:
                mins = int(ws["seconds_since_write"] / 60)
                age = f"{mins}m ago" if mins < 90 else f"{mins // 60}h ago"
                a(f"     {C['b']}ao -C {ws['path']}{C['reset']}  {C['dim']}{age}{C['reset']}")
        else:
            a(f"   {C['dim']}No local agent sessions found at all. See docs/adapters.md.{C['reset']}")

    # telemetry
    tel = facts["telemetry"]
    if tel and (tel["context_percent"] is not None or tel["last_turn_cost"] is not None or tel["machine_quota"]):
        a(f"\n{C['b']}{C['mag']}── QUOTA / CONTEXT {'─' * max(0, w - 20)}{C['reset']}")
        if tel["context_percent"] is not None:
            note = "  ← start a fresh session" if tel["context_percent"] > 85 else ""
            a(f"   context  {_bar(tel['context_percent'])}{C['dim']}{note}{C['reset']}")
        if tel["last_turn_cost"] is not None:
            lu, avg = tel["last_turn_cost"], tel["cost_average"]
            warn = f"  {C['yellow']}⚠ {lu/max(avg,1):.1f}× average{C['reset']}" if lu > 2 * avg else ""
            # The subagents a turn started spent inside its cost, and inside the total.
            delegated = f", {tel['cost_delegated']:.0f} delegated" if tel["cost_delegated"] else ""
            a(f"   cost     last turn {C['b']}{lu:.0f}{C['reset']} {tel['cost_unit']} "
              f"({tel['last_turn_tool_calls']} tool calls) · {tel['turns']} turns, total "
              f"{C['b']}{tel['cost_total']:.0f}{C['reset']}{C['dim']} (avg {avg:.0f}{delegated}){C['reset']}{warn}")
        if tel["machine_quota"]:
            # keyflip reports machine-wide provider windows, which are NOT the
            # implementer's own pool — Kiro bills credits, Claude Code bills a
            # 5h window. Labelling it plainly avoids reading someone else's
            # quota as this agent's.
            a(f"   {C['dim']}other tools on this machine (not {impl.get('adapter','this agent')}'s pool):{C['reset']}")
            for line in tel["machine_quota"]:
                a(f"     {C['dim']}{line}{C['reset']}")

    # problems — only rendered when there is one, so an empty panel means healthy
    trouble = facts["problems"]
    if trouble and (trouble["nudge_error"] or trouble["agent_errors"] or trouble["spinning_minutes"]
                    or trouble["nothing_to_do_since"] is not None):
        a(f"\n{C['b']}{C['red']}── PROBLEMS {'─' * max(0, w - 13)}{C['reset']}")
        if trouble["nothing_to_do_since"] is not None:
            a(f"   {C['yellow']}nothing to do{C['reset']} "
              f"{C['dim']}{_idle_answer_text({'since': trouble['nothing_to_do_since']})}{C['reset']}")
        if trouble["spinning_minutes"]:
            a(f"   {C['red']}spinning{C['reset']} {C['dim']}{trouble['spinning_minutes']}m busy, nothing committed "
              f"or changed{C['reset']}")
            a(f"     {C['dim']}activity is not progress — re-specify, split, or check for "
              f"a wait loop{C['reset']}")
        nudge_err = trouble["nudge_error"]
        if nudge_err:
            mins = int((time.time() - nudge_err["at"]) / 60)
            a(f"   {C['red']}restart failed{C['reset']} {C['dim']}{mins}m ago · "
              f"exit {nudge_err['code']}{C['reset']}")
            for ln in textwrap.wrap(nudge_err["tail"], w - 8)[:2]:
                a(f"     {C['dim']}{ln}{C['reset']}")
            a(f"     {C['dim']}full log: {_home_relative(nudge_err['log'])}{C['reset']}")
        for error in trouble["agent_errors"]:
            for i, ln in enumerate(textwrap.wrap(error["text"], w - 12)[:2]):
                a(f"   {C['dim']}{error['time']}{C['reset']} {C['yellow']}agent error{C['reset']}  {ln}"
                  if i == 0 else f"        {C['dim']}│{C['reset']}  {ln}")

    # A returned review is handled before new work starts (#28, W1).
    for review in facts["returned_reviews"]:
        a(f"\n   {C['yellow']}{C['b']}REVIEW RETURNED{C['reset']} for {review['slice'] or 'a slice'}: "
          f"{review['id']} {review['verdict'] or review['state']} — handle it before new work "
          f"({C['b']}ao review collect {review['id']}{C['reset']})")
    # reviews + round budget
    if facts["reviews"]:
        rn, budget = facts["rounds"]["spent"], facts["rounds"]["budget"]
        a(f"\n{C['b']}{C['mag']}── REVIEWS {'─' * max(0, w - 12)}{C['reset']}")
        if rn > budget:
            a(f"   {C['red']}⚠ round {rn}/{budget} — over budget: re-specify, split, "
              f"or change actor{C['reset']}")
        elif rn:
            a(f"   {C['dim']}round {rn}/{budget}{C['reset']}")
        for review in facts["reviews"]:
            col = C["green"] if "APPROVED" in review["verdict"].upper() else C["yellow"]
            a(f"   {review['file'].split('-pr')[0]}   {col}{review['verdict']}{C['reset']}")

    # throughput: what the window produced, so busy-and-landing-nothing shows (#91)
    tp = facts["throughput"]
    if tp is not None:
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
    repo = facts["repository"]
    a(f"\n{C['b']}{C['mag']}── REPOSITORY {'─' * max(0, w - 15)}{C['reset']}")
    for ln in repo["log"]:
        a(f"   {ln[:w-5]}")
    a(f"   {C['dim']}{repo['dirty_files']} files uncommitted · {C['reset']}{_checkout_position(repo)}")

    mail = facts["mail"]
    a(f"\n   {C['b']}Mailbox:{C['reset']} " +
      (", ".join(mail["waiting"]) if mail["waiting"] else f"{C['dim']}empty{C['reset']}"))
    if mail["urgent"] is not None:
        for line in _banner_lines(mail["dir"], mail["urgent"], len(mail["waiting"])):
            a(f"   {line}")
    unseen = mail["unseen"]
    if unseen:
        oldest = unseen[0]
        a(f"   {C['yellow']}oldest unseen{C['reset']} {oldest['id']}  {C['dim']}{oldest['class']}, "
          f"{oldest['age_seconds'] // 60}m" + (f" · {len(unseen) - 1} more unseen" if len(unseen) > 1 else "")
          + f"{C['reset']}")

    # Board — one line, because a parked item is invisible by construction: work
    # moved on past it, so no other signal in this panel looks wrong.
    board = facts["board"]
    if any(board["counts"].values()):
        counts = " · ".join(f"{board['counts'][k]} {k}" for k in
                            ("running", "blocked", "queued", "verified", "done") if board["counts"][k])
        a(f"   {C['b']}Board:{C['reset']}   {counts}")
        for it in board["blocked"]:
            why = it["why"] or "reason not recorded"
            for i, ln in enumerate(textwrap.wrap(f"{it['id']}  {it['title']} — {why}", w - 14)[:2]):
                a(f"     {C['red']}⊘{C['reset']} {ln}" if i == 0 else f"       {C['dim']}{ln}{C['reset']}")

    # The message log is elastic and goes last, so a short window drops history
    # rather than the state you actually steer by.
    msgs_block = []
    if facts["messages"]:
        msgs_block.append(f"\n{C['b']}{C['mag']}── RECENT MESSAGES {'─' * max(0, w - 20)}{C['reset']}")
        for message in facts["messages"]:
            tag = f"{C['blue']}YOU  {C['reset']}" if message["role"] == "user" else f"{C['cyan']}AGENT{C['reset']}"
            for i, ln in enumerate(textwrap.wrap(message["text"], w - 14)[:3]):
                msgs_block.append(f"   {C['dim']}{message['time']}{C['reset']} {tag}  {ln}" if i == 0
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


def _marked_mail(cfg, shown):
    """(the marked messages for the role running this command, how many messages wait), or None when the
    mailbox cannot be read. Where they are `shown`, drawn for whoever runs the command, they are recorded
    as seen: named on a screen is shown to its reader (#30). A document a program reads shows nobody anything.

    A person, or a caller with no AO_ROLE, is shown what is marked for either role (#29).
    """
    root = cfg["root"]
    try:
        urgent = A.urgent_messages(root, cfg, A.invoking_role())
        waiting = len(A.mailbox(root, cfg.get("mailbox", "agent-mail")))
        if shown:
            A.mail_seen(root, [m["id"] for m in urgent], A.invoking_role() or "person")
    except OSError:
        return None
    return urgent, waiting


def _banner_lines(mailbox_dir, urgent, waiting):
    """The banner: each marked message and whom it is for, then how many others wait (#29)."""
    lines = [f"{C['red']}{C['b']}URGENT{C['reset']} for the {m['to']}: {C['b']}{m['title']}{C['reset']}  "
             f"{C['dim']}{m['id']}{C['reset']}" for m in urgent]
    others = waiting - len(urgent)
    if others > 0:
        lines.append(f"{C['dim']}{others} other message(s) waiting in {mailbox_dir}/{C['reset']}")
    return lines


def _mailbox_banner(cfg):
    """Lines naming the marked messages for the role running this command, and how many others wait (#29).

    A person, or a caller with no AO_ROLE, sees what is marked for either role.
    """
    marked = _marked_mail(cfg, shown=True)
    return [] if marked is None else _banner_lines(cfg.get("mailbox", "agent-mail"), *marked)


def cmd_status(cfg, args):
    window = getattr(args, "window", None) or 24.0
    if getattr(args, "json", False):
        # The facts alone, as one document: no colour, no banner, nothing else on stdout, and nothing
        # recorded as seen, since no reader was shown the messages it names (JSON-OUTPUT).
        print(json.dumps(status_facts(cfg, args.messages, window, shown=False), ensure_ascii=False))
        return
    print(render(cfg, args.messages, window_hours=window))


def cmd_watch(cfg, args):
    if not A.terminal():
        # A pipe, a log or a dumb terminal gets the panel once. Looping there wrote the alternate
        # screen and a clear before every frame into whatever read it, and never ended (CLI-ROBUST).
        width = shutil.get_terminal_size((120, 40)).columns
        print("\n".join(render_fleet(width)) if getattr(args, "all", False) else render(cfg, args.messages, width))
        return 0
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
    """One row per workspace with a local agent session, what needs a human first.

    `ao fleet` draws the rows and `ao fleet --json` prints them, under the keys docs/parallel.md
    lists (JSON-OUTPUT).
    """
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
        session = _session_facts(A.session_state(cfg, "implementer"))
        bd = A.board(root)
        g = A.git_state(root)
        rows.append({"name": cfg.get("project") or os.path.basename(root), "root": root,
                     "state": state, "seconds_since_write": age, "doing": desc, "session": session,
                     "queued": len(bd["queued"]), "blocked": len(bd["blocked"]),
                     "running": len(bd["running"]), "dirty_files": len(g["dirty"]),
                     "mail_waiting": len(A.mailbox(root, cfg["mailbox"])),
                     "spinning_minutes": A.spinning(root),
                     "has_source": bool(A.sources(root))})
    # what needs a human first: spinning, then blocked, then idle
    rows.sort(key=lambda r: (r["spinning_minutes"] is None, r["blocked"] == 0, r["state"] != "idle"))
    return rows


def fleet_facts():
    """The fleet, read once: when it was read, and the rows in the order `ao fleet` draws them (JSON-OUTPUT)."""
    return {"at": int(time.time()), "projects": _fleet_rows()}


def render_fleet(width=None):
    w = min(width or shutil.get_terminal_size((120, 40)).columns, 130)
    facts = fleet_facts()
    L = [f"{C['b']}{C['cyan']}{'═' * w}{C['reset']}",
         f"{C['b']}  ALL PROJECTS{C['reset']}{C['dim']}   agent-orchestrator   "
         f"{datetime.fromtimestamp(facts['at']):%d %b %H:%M:%S}{C['reset']}",
         f"{C['b']}{C['cyan']}{'═' * w}{C['reset']}"]
    rows = facts["projects"]
    if not rows:
        L.append(f"\n{C['dim']}No workspaces with a local agent session.{C['reset']}")
        return L
    for r in rows:
        dot, col = {"working": ("●", C["green"]), "slowing": ("◐", C["yellow"]),
                    "stopped": ("■", C["red"]),
                    "idle": ("○", C["red"])}.get(r["state"], ("?", C["dim"]))
        agestr = f"{r['seconds_since_write'] // 60}m" if r["seconds_since_write"] is not None else "—"
        flags = []
        if r["spinning_minutes"]:
            flags.append(f"{C['red']}spinning {r['spinning_minutes']}m{C['reset']}")
        if r["blocked"]:
            flags.append(f"{C['red']}{r['blocked']} blocked{C['reset']}")
        if r["mail_waiting"]:
            flags.append(f"{C['cyan']}{r['mail_waiting']} mail{C['reset']}")
        if not r["has_source"]:
            flags.append(f"{C['dim']}no source{C['reset']}")
        L.append(f"\n {col}{dot}{C['reset']} {C['b']}{r['name'][:22]:<22}{C['reset']}"
                 f"{C['dim']}{r['state']:<8} {agestr:>4}{C['reset']}  "
                 f"q{r['queued']} r{r['running']} "
                 f"{C['dim']}·{C['reset']} {r['dirty_files']} dirty"
                 + ("   " + "  ".join(flags) if flags else ""))
        desc = r["doing"]
        unresolved = _unresolved_session(r["seconds_since_write"], r["session"])
        if unresolved:
            desc = f"session {unresolved['how']}: {unresolved['why']}"          # why it is unknown (SESSION-IDENTITY)
        if desc:
            for ln in textwrap.wrap(desc, w - 8)[:1]:
                L.append(f"     {C['dim']}↳ {ln}{C['reset']}")
    return L


def cmd_fleet(cfg, args):
    if getattr(args, "json", False):
        # The rows alone, as one document: no colour, no header, nothing else on stdout (JSON-OUTPUT).
        print(json.dumps(fleet_facts(), ensure_ascii=False))
        return 0
    for ln in render_fleet():
        print(ln)
    return 0


def cmd_tail(cfg, args):
    for line in _mailbox_banner(cfg):
        print(line)
    msgs_path, _ = A.session_paths(cfg)
    if not msgs_path:
        session = A.session_state(cfg, "implementer") or {}
        why = session.get("why") if not session.get("session") else None
        print(f"No implementer session found: {why}" if why else "No implementer session found.", file=sys.stderr)
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
        # The age shares its positional with send's type, so no argparse type can read it; the
        # same parser can (CLI-ROBUST). A bare number is days, as it always was.
        try:
            days = A.time_span(args.type, "d") if args.type and args.type != "INFO" else 30.0
        except ValueError as exc:
            print(f"ao mail compact: {exc}")
            return 2
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
