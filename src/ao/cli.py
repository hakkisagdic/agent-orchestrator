#!/usr/bin/env python3
"""ao — agent-orchestrator, observation layer.

Implemented today: status, watch, tail, mail, verify, board, adapters, doctor.
Also: watchdog install/uninstall/status — a launchd job that restarts a stalled
implementer, with guards so it spends nothing when spending would not help.
Not yet: decide, since, init — see the
roadmap in README.md. Commands that do not exist say so rather than pretending.

Standard library only. Observation is strictly read-only.
"""
import argparse
import json
import os
import shutil
import signal
import sys
import textwrap
import time
from datetime import datetime

from . import lib as A

C = A.C


def _ctx(cfg):
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    return root, impl, adapter


def _bar(pct, width=20):
    filled = max(0, min(width, int(pct / (100 / width))))
    col = C["red"] if pct > 85 else C["yellow"] if pct > 70 else C["green"]
    return f"{col}{'█' * filled}{'░' * (width - filled)}{C['reset']} {pct:.0f}%"


def render(cfg, msg_count=8, width=None, max_lines=None):
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
        tel = A.telemetry(recs, adapter)
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
                a(f"   cost     last turn {C['b']}{lu:.0f}{C['reset']} {tel['unit']} "
                  f"({lt} tool calls) · {tel['turns']} turns, total {C['b']}{tel['total']:.0f}{C['reset']}"
                  f"{C['dim']} (avg {avg:.0f}){C['reset']}{warn}")
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
        if nudge_err or errs or spin:
            a(f"\n{C['b']}{C['red']}── PROBLEMS {'─' * max(0, w - 13)}{C['reset']}")
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
                a(f"     {C['dim']}full log: ~/.ao/nudge-{os.path.basename(root)}.log{C['reset']}")
            for hh, text in errs:
                for i, ln in enumerate(textwrap.wrap(text, w - 12)[:2]):
                    a(f"   {C['dim']}{hh}{C['reset']} {C['yellow']}agent error{C['reset']}  {ln}"
                      if i == 0 else f"        {C['dim']}│{C['reset']}  {ln}")

    # reviews + round budget
    revs = A.reviews(root, cfg["reviews"])
    if revs:
        rn = A.rounds(root, cfg["reviews"])
        budget = cfg.get("round_budget", 5)
        a(f"\n{C['b']}{C['mag']}── REVIEWS {'─' * max(0, w - 12)}{C['reset']}")
        if rn > budget:
            a(f"   {C['red']}⚠ round {rn}/{budget} — over budget: re-specify, split, "
              f"or change actor{C['reset']}")
        elif rn:
            a(f"   {C['dim']}round {rn}/{budget}{C['reset']}")
        for f, v in revs:
            col = C["green"] if "APPROVED" in v.upper() else C["yellow"]
            a(f"   {f.split('-pr')[0]}   {col}{v}{C['reset']}")

    # git + mail
    g = A.git_state(root)
    a(f"\n{C['b']}{C['mag']}── REPOSITORY {'─' * max(0, w - 15)}{C['reset']}")
    for ln in g["log"][:3]:
        if ln:
            a(f"   {ln[:w-5]}")
    a(f"   {C['dim']}{len(g['dirty'])} files uncommitted · {g['ahead']} commits unpushed{C['reset']}")

    mail = A.mailbox(root, cfg["mailbox"])
    a(f"\n   {C['b']}Mailbox:{C['reset']} " +
      (", ".join(mail) if mail else f"{C['dim']}empty{C['reset']}"))

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
        ms = A.messages(msg_records, msg_count)
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


def cmd_status(cfg, args):
    print(render(cfg, args.messages))


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
    msgs_path, _ = A.session_paths(cfg)
    if not msgs_path:
        print("No implementer session found.", file=sys.stderr)
        return 1
    _, _, adapter = _ctx(cfg)
    for hh, kind, text in A.messages(A.read_tail(msgs_path), args.n):
        who = "YOU  " if kind == "user" else "AGENT"
        print(f"--- {hh} [{who}] ---\n{text}\n")


def cmd_mail(cfg, args):
    root = cfg["root"]
    d = os.path.join(root, cfg["mailbox"])
    if args.action == "list":
        for f in A.mailbox(root, cfg["mailbox"]):
            print(f)
    elif args.action == "read":
        for f in A.mailbox(root, cfg["mailbox"]):
            print(f"===== {f} =====\n{open(os.path.join(d, f)).read()}")
    elif args.action == "send":
        os.makedirs(d, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        topic = (args.topic or "note").replace(" ", "-").lower()
        name = f"{stamp}-architect-to-implementer-{args.type.upper()}-{topic}.md"
        body = args.body if args.body else sys.stdin.read()
        open(os.path.join(d, name), "w").write(body.rstrip() + "\n")
        print(name)


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>{script}</string>
    <string>--root</string><string>{root}</string>
    <string>--idle-minutes</string><string>{idle}</string></array>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def cmd_verify(cfg, args):
    """Run the project's declared gates ourselves and record what we measured.

    The whole point of a second agent is not needing to trust the first one's
    report, so this executes the commands and writes the numbers to the ledger.
    Commit authority is later granted against this record, not against a claim.
    """
    import json as _json
    import subprocess
    root = cfg["root"]
    gates_file = os.path.join(root, ".ao", "gates.json")
    holder = A.gate_lock_holder()
    if holder and holder.get("root") != root:
        print(f"{C['yellow']}{os.path.basename(holder['root'])} is running its gates"
              f"{C['reset']} {C['dim']}({holder['minutes']}m){C['reset']} — waiting up to "
              f"{args.wait}s so two suites do not fight for one machine")
        if not A.acquire_gate_lock(root, args.wait):
            print(f"{C['red']}still busy; not starting a second suite{C['reset']}")
            return 2
    else:
        A.acquire_gate_lock(root, 0)
    if not os.path.exists(gates_file):
        print(f"{C['yellow']}No .ao/gates.json — nothing declared to verify.{C['reset']}")
        print(f"{C['dim']}See docs/gates.md for the shape.{C['reset']}")
        return 1
    spec = _json.load(open(gates_file))
    profile = args.profile or spec.get("default_profile", "quick")
    names = spec.get("profiles", {}).get(profile)
    if not names:
        print(f"unknown profile {profile}; have: {', '.join(spec.get('profiles', {}))}")
        return 1

    results, ok = [], True
    for name in names:
        g = spec["gates"][name]
        print(f"{C['dim']}▶ {name}{C['reset']}  {g['run']}")
        started = time.time()
        try:
            r = subprocess.run(g["run"], shell=True, cwd=root, capture_output=True,
                               text=True, timeout=g.get("timeout", 600))
            out, code = (r.stdout + r.stderr), r.returncode
        except subprocess.TimeoutExpired:
            out, code = "timed out", 124
        took = int(time.time() - started)

        if g.get("expect") == "empty":
            passed = not out.strip()
            detail = "clean" if passed else " ".join(out.split())[:120]
        else:
            passed = code == 0
            m = A.re.search(r"#\s*pass\s+(\d+)[\s\S]*?#\s*fail\s+(\d+)", out) or \
                A.re.search(r"(?:ℹ\s*)?pass\s+(\d+)[\s\S]*?(?:ℹ\s*)?fail\s+(\d+)", out)
            if m:
                p_, f_ = int(m.group(1)), int(m.group(2))
                detail = f"{p_}/{p_ + f_}"
                passed = passed and f_ == 0
            else:
                detail = f"exit {code}"
        ok = ok and passed
        mark = f"{C['green']}pass{C['reset']}" if passed else f"{C['red']}FAIL{C['reset']}"
        print(f"  {mark}  {detail}  {C['dim']}{took}s{C['reset']}")
        if not passed:
            tail = " ".join(out.strip().split("\n")[-4:])[:400]
            print(f"  {C['dim']}{tail}{C['reset']}")
        results.append({"name": name, "passed": passed, "detail": detail,
                        "exit": code, "seconds": took})

    # A plan the implementer edited is a plan that no longer measures anything:
    # the work and the standard it is judged by came from the same hand. Treat it
    # as a failed gate, because that is what it is.
    drift = A.plan_drift(root)
    if drift:
        ok = False
        print(f"\n  {C['red']}FAIL{C['reset']}  plan changed after admission: "
              f"{', '.join(drift)}")
        print(f"  {C['dim']}the implementer reads its plan; it does not write to it{C['reset']}")
        results.append({"name": "plan-integrity", "passed": False,
                        "detail": f"drifted: {','.join(drift)}", "exit": 1, "seconds": 0})

    revs = A.reviews(root, cfg["reviews"], limit=1)
    rec = {"id": f"V-{int(time.time())}", "at": datetime.now().isoformat(timespec="seconds"),
           "profile": profile, "by": "ao verify", "passed": ok, "gates": results,
           "plan_drift": drift,
           # Which tree these numbers describe. Without it the record cannot
           # authorise anything: a pass measured before the last three edits says
           # nothing about what is about to be committed.
           "tree": A.tree_digest(root),
           "review": revs[0][0] if revs else None,
           "review_verdict": revs[0][1] if revs else None,
           "head": A.sh("git rev-parse --short HEAD", cwd=root),
           "dirty": len([l for l in A.sh("git status --short", cwd=root).split("\n") if l.strip()])}
    ledger = os.path.join(root, ".ao", "ledger")
    os.makedirs(ledger, exist_ok=True)
    with open(os.path.join(ledger, "verifications.jsonl"), "a") as fh:
        fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")

    A.release_gate_lock()
    print(f"\n{C['b']}{'PASS' if ok else 'FAIL'}{C['reset']}  recorded as {rec['id']}")
    if revs:
        col = C["green"] if "APPROVED" in (revs[0][1] or "").upper() else C["yellow"]
        print(f"{C['dim']}newest review:{C['reset']} {col}{revs[0][1]}{C['reset']} ({revs[0][0]})")
    return 0 if ok else 1


def cmd_commit_ok(cfg, args):
    """May the current working tree be committed? Answered from evidence.

    This is the piece that lets work land without the architect awake. It grants
    nothing on request and nothing on a claim — it re-reads what was independently
    measured and checks that the measurement still describes the tree in front of
    it. Every refusal names the missing condition, so an agent can act on it
    instead of asking again.

    It does not commit. Deciding and acting stay in different hands; that
    separation is the whole reason the answer is worth anything.
    """
    root = cfg["root"]
    now = A.tree_digest(root)
    ver = A.latest_verification(root)
    revs = A.reviews(root, cfg["reviews"], limit=1)
    drift = A.plan_drift(root)

    reasons = []
    if not ver:
        reasons.append("no verification record — run `ao verify`")
    else:
        if not ver.get("passed"):
            failed = [g["name"] for g in ver.get("gates", []) if not g.get("passed")]
            reasons.append(f"last verification failed: {', '.join(failed) or ver['id']}")
        if ver.get("tree") and ver["tree"] != now:
            reasons.append(f"tree changed since {ver['id']} — re-run `ao verify`")
        elif not ver.get("tree"):
            reasons.append(f"{ver['id']} predates tree digests — re-run `ao verify`")
    if drift:
        reasons.append(f"plan edited after admission: {', '.join(drift)}")
    if not revs:
        reasons.append("no review found")
    elif "APPROVED" not in (revs[0][1] or "").upper():
        reasons.append(f"newest review is {revs[0][1]} ({revs[0][0]})")
    held = A.hold_state(root)
    if held:
        reasons.append(f"project is held by {held.get('by')}: {held.get('reason','')}")
    if not A.sh("git status --porcelain", cwd=root):
        reasons.append("nothing to commit")

    if reasons:
        print(f"{C['red']}{C['b']}REFUSED{C['reset']}")
        for r in reasons:
            print(f"  {C['red']}·{C['reset']} {r}")
        A.record_authority(root, False, reasons, now, (ver or {}).get("id"))
        return 1

    token = f"C-{int(time.time())}"
    print(f"{C['green']}{C['b']}GRANTED{C['reset']}  {token}")
    print(f"  {C['dim']}verified{C['reset']} {ver['id']} · {C['dim']}review{C['reset']} {revs[0][0]}")
    print(f"  {C['dim']}tree{C['reset']}     {now[:23]}…")
    print(f"\n  {C['dim']}push is not covered by this grant and never will be.{C['reset']}")
    A.record_authority(root, True, [], now, ver["id"], token)
    return 0


def _serve(module, cfg, extra):
    """Run a server module in this process.

    Calling it beats exec'ing a sibling script: the script path only exists in a
    git clone, and after `pip install ao` there is no scripts/ directory to point
    at. Importing works from both.
    """
    sys.argv = ["ao-" + module, "-C", cfg["root"]] + extra
    mod = __import__(f"ao.{module}", fromlist=["main"])
    return mod.main()


def cmd_mcp(cfg, args):
    """Serve this project's state to any MCP client, over stdio."""
    if args.action != "serve":
        print(f"{C['b']}Add to an MCP client's config:{C['reset']}")
        exe = shutil.which("ao") or sys.argv[0]
        print(json.dumps({"mcpServers": {"agent-orchestrator": {
            "command": exe, "args": ["-C", cfg["root"], "mcp", "serve"]}}}, indent=2))
        return 0
    return _serve("mcp", cfg, ["--allow-verify"] if args.allow_verify else [])


def cmd_a2a(cfg, args):
    """Serve this project's board as A2A tasks, on loopback."""
    if args.action != "serve":
        print(f"{C['b']}ao a2a serve --port {args.port}{C['reset']}")
        print(f"  card   http://127.0.0.1:{args.port}/.well-known/agent-card.json")
        print(f"  tasks  http://127.0.0.1:{args.port}/tasks")
        return 0
    return _serve("a2a", cfg, ["--port", str(args.port)])


def cmd_lock(cfg, args):
    """Run a heavy command under the machine-wide lock.

    `ao verify` already serialises its own runs, but the implementer does not go
    through `ao verify` — it runs `npm test` directly, as it should. So the lock
    covered the architect's measurements and not the thing they were competing
    with, which is the collision that actually happens.

    Wrapping is the fix, and it generalises past tests: a container build, a
    database restore, an integration environment. Anything that saturates a
    shared machine belongs behind one lock, or the machine is not shared, it is
    contended.

        ao lock -- npm test
        ao lock --wait 1800 -- docker build .
    """
    if not args.command:
        print(f"usage: {C['b']}ao lock -- <command>{C['reset']}")
        holder = A.gate_lock_holder()
        print(f"  {C['dim']}holder:{C['reset']} " +
              (f"{os.path.basename(holder['root'])} ({holder['minutes']}m)"
               if holder else f"{C['dim']}free{C['reset']}"))
        return 0
    import subprocess
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        print("nothing to run")
        return 0
    root = cfg["root"]
    holder = A.gate_lock_holder()
    if holder and holder.get("root") != root:
        print(f"{C['yellow']}{os.path.basename(holder['root'])} holds the machine lock"
              f"{C['reset']} {C['dim']}({holder['minutes']}m){C['reset']} — waiting up to "
              f"{args.wait}s", flush=True)
    if not A.acquire_gate_lock(root, args.wait):
        print(f"{C['red']}machine still busy after {args.wait}s; not starting{C['reset']}")
        return 2
    try:
        return subprocess.run(args.command, cwd=root).returncode
    finally:
        A.release_gate_lock()


def cmd_board(cfg, args):
    """Where every pre-authorised item is, blocked ones first.

    Order is deliberate: `running` and `blocked` are the two states a human can
    act on, and `blocked` is the one that goes unnoticed — work carried on past
    it, so nothing else in the panel looks wrong.
    """
    root = cfg["root"]
    b = A.board(root)
    path = os.path.join(root, ".ao", "board.md")
    if not os.path.exists(path):
        print(f"{C['yellow']}No board here.{C['reset']} Create {C['b']}.ao/board.md{C['reset']} with "
              f"`## running` / `## blocked` / `## queued` / `## verified` / `## done` sections\n"
              f"and one `- [ID] title · note: value` line per item.")
        return 0
    colours = {"running": C["green"], "blocked": C["red"], "queued": C["dim"],
               "verified": C["cyan"], "done": C["dim"]}
    for st in ("running", "blocked", "queued", "verified", "done"):
        items = b[st]
        if not items:
            continue
        print(f"\n{C['b']}{colours[st]}{st.upper()}{C['reset']} {C['dim']}({len(items)}){C['reset']}")
        for it in items:
            notes = "  ".join(f"{C['dim']}{k}:{C['reset']} {v}" if v else f"{C['dim']}{k}{C['reset']}"
                              for k, v in it["notes"].items())
            print(f"   {C['b']}{it['id']}{C['reset']}  {it['title']}" + (f"   {notes}" if notes else ""))
    if not any(b.values()):
        print(f"{C['dim']}Board is empty.{C['reset']}")
    return 0


def cmd_source(cfg, args):
    """External work queues: what is configured, what is waiting, what may enter.

    Admission is the whole point of this command. Pulling an issue is free and
    carries no authority; a tracker item is something a person wrote, not a
    specification anyone verified. An item enters the board only with a written
    acceptance boundary, because the alternative — an agent inferring scope from
    a title — is how a one-line bug fix becomes a refactor nobody asked for.

    `ao` does not judge which items qualify: it has no model and no tracker
    credential. It enforces the rule; an architect turn makes the call and writes
    it into the inbox file.
    """
    root = cfg["root"]
    sc = A.sources(root)
    if not sc:
        print(f"{C['yellow']}No sources configured.{C['reset']}  Create {C['b']}.ao/sources.json{C['reset']}:\n"
              f'  {{"bound_root": "{root}", "wip_limit": 1, "refill_below": 3,\n'
              f'   "sources": [{{"id": "linear-x", "kind": "mcp", "server": "linear",\n'
              f'                "select": {{"team": "…", "state": "Todo"}}}}]}}')
        return 0

    files = A.inbox_files(root)
    if args.action in ("list", "status"):
        bd = A.board(root)
        print(f"{C['b']}bound to{C['reset']}  {sc.get('bound_root', root)}")
        for src in sc["sources"]:
            sel = " ".join(f"{k}={v}" for k, v in (src.get("select") or {}).items())
            wb = (src.get("writeback") or {})
            wbs = (f"{C['yellow']}writes back{C['reset']}" if wb.get("enabled")
                   else f"{C['dim']}read-only{C['reset']}")
            print(f"  {C['b']}{src['id']}{C['reset']}  {src.get('kind','mcp')}:{src.get('server','?')}"
                  f"  {C['dim']}{sel}{C['reset']}  {wbs}")
        depth = len(bd["queued"])
        low = depth < sc["refill_below"]
        col = C["yellow"] if low else C["green"]
        print(f"\n{C['b']}queue{C['reset']}  {col}{depth} admitted{C['reset']}"
              f"{C['dim']} · refill below {sc['refill_below']} · wip limit {sc['wip_limit']}{C['reset']}"
              + (f"  {C['yellow']}← needs a refill pass{C['reset']}" if low else ""))
        print(f"{C['b']}inbox{C['reset']}  {len(bd['inbox'])} on the board, "
              f"{len(files)} pull file(s) not yet imported")
        return 0

    # import — the only place an item crosses from "someone wrote this" to
    # "an agent may work on it unattended"
    if not files:
        print(f"{C['dim']}Nothing in .ao/inbox/ to import.{C['reset']}")
        return 0
    admitted = held = 0
    for f in files:
        try:
            doc = json.load(open(f))
        except Exception as e:
            print(f"{C['red']}skip{C['reset']} {os.path.basename(f)}: unreadable ({e})")
            continue
        err = A.binding_error(root, doc.get("bound_root"))
        if err:
            print(f"{C['red']}REFUSED{C['reset']} {os.path.basename(f)}: {err}")
            continue
        sid = doc.get("source", "?")
        for it in doc.get("items", []):
            iid, title = it.get("id"), it.get("title", "")
            if not iid:
                continue
            if any(x["id"] == iid for st in A.BOARD_STATES for x in A.board(root)[st]):
                continue                                  # already on the board
            acc = (it.get("acceptance") or "").strip()
            if acc:
                A.board_append(root, "queued",
                               f"- [{iid}] {title} · source: {sid} · acceptance: {acc}")
                dg = A.plan_digest(root, iid)
                if dg:
                    A.record_plan(root, iid, dg)   # baseline: the plan as admitted
                admitted += 1
            else:
                why = it.get("shape") or "no acceptance boundary written"
                A.board_append(root, "inbox",
                               f"- [{iid}] {title} · source: {sid} · needs: {why}")
                held += 1
        os.rename(f, f + ".imported")
    print(f"{C['green']}{admitted} admitted{C['reset']} to queued · "
          f"{C['yellow']}{held} held{C['reset']} in inbox (no acceptance boundary)")
    return 0


def cmd_hold(cfg, args):
    """Stop this project's agents and keep them stopped.

    A kill switch that only kills is not a switch: the watchdog sees an idle
    session with open work and restarts it within a couple of minutes. So the
    stop and the lock are one operation, and every restart path checks the lock
    first.

    It stops *every* agent process whose cwd is this repository, not just the one
    we most recently started. Detached turns accumulate — this project found
    fifteen live `kiro-cli` processes in one tree, four of them still burning
    CPU, because each nudge spawned one and nothing ever reaped them. Tracking
    only our own last child made the rest invisible.
    """
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    path = os.path.join(root, A.HOLD_FILE)

    if args.action == "status":
        st = A.hold_state(root)
        if not st:
            print(f"{C['green']}running free{C['reset']} — no hold")
        else:
            print(f"{C['red']}HELD{C['reset']} by {C['b']}{st.get('by')}{C['reset']} "
                  f"for {st['minutes']}m: {st.get('reason','')}")
        pids = A.agent_pids(root, adapter)
        print(f"{len(pids)} agent process(es) in this tree" + (f": {pids}" if pids else ""))
        return 0

    if args.action == "release":
        if not os.path.exists(path):
            print(f"{C['dim']}No hold to release.{C['reset']}")
            return 0
        st = A.hold_state(root) or {}
        os.remove(path)
        print(f"{C['green']}released{C['reset']} after {st.get('minutes', 0)}m")
        if args.note:
            # The agent wakes into a tree it did not change. Say what moved, or it
            # spends its first turns rediscovering it — or worse, mistrusting it.
            box = os.path.join(root, cfg["mailbox"])
            os.makedirs(box, exist_ok=True)
            name = f"{datetime.now():%Y%m%d-%H%M}-fable-to-kiro-INFO-hold-released.md"
            with open(os.path.join(box, name), "w") as fh:
                fh.write(f"# INFO — hold released\n\nDuruldu: {st.get('minutes',0)} dakika\n"
                         f"Sebep: {st.get('reason','')}\n\n## Bu sürede ne değişti\n\n"
                         f"{args.note}\n")
            print(f"handover note → {cfg['mailbox']}/{name}")
        return 0

    # hold
    pids = A.agent_pids(root, adapter)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"by": args.by, "reason": args.reason or "manual intervention",
               "at": int(time.time()), "stopped": pids},
              open(path, "w"), indent=2)
    if not pids:
        print(f"{C['yellow']}hold set{C['reset']} — no agent process was running")
        return 0
    print(f"stopping {len(pids)} process(es): {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)         # let it finish the write it is in
        except OSError:
            pass
    deadline = time.time() + args.grace
    while time.time() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            break
        time.sleep(0.5)
    alive = [p for p in pids if _alive(p)]
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    print(f"{C['red']}HELD{C['reset']} — {len(pids) - len(alive)} exited on request, "
          f"{len(alive)} killed. The watchdog will not restart while .ao/hold exists.")
    return 0


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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
    for line in open(path, errors="replace"):
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
        with open(path, "w") as fh:
            fh.writelines(keep)
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
    key = os.path.basename(root.rstrip("/")) or "root"
    print(f"{C['b']}pruning records older than {args.days} day(s){C['reset']}"
          f"{C['dim']}  {root}{C['reset']}")
    if dry:
        print(f"{C['yellow']}dry run — add --yes to apply{C['reset']}")

    total = 0
    for name, kind, rel, desc in STORES:
        if kind == "evidence" and not args.evidence:
            path = os.path.join(root, rel)
            if os.path.exists(path):
                n = sum(1 for _ in open(path, errors="replace"))
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
        dropped, kept, freed = _prune_jsonl(path, cutoff, dry)
        total += freed
        if dropped:
            print(f"  {C['green']}{'would drop' if dry else 'dropped'}{C['reset']}  "
                  f"{name:<14} {dropped} of {dropped + kept}  {C['dim']}{desc}{C['reset']}")

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
                with open(pth, errors="replace") as fh:
                    fh.seek(max(0, size - args.keep_kb * 1024))
                    tail = fh.read()
                with open(pth, "w") as fh:
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
    rows = A.notices(root, args.n, include_suppressed=args.all)
    if not rows:
        print(f"{C['dim']}No notices recorded.{C['reset']}")
        return 0
    for r in rows:
        when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
        tag = (f"{C['green']}sent{C['reset']}" if r.get("sent")
               else f"{C['dim']}held{C['reset']}")
        print(f"  {C['dim']}{when}{C['reset']}  {tag}  {C['b']}{r['title']}{C['reset']}  {r['msg']}")
    if not args.all:
        print(f"{C['dim']}  (--all also shows alerts the rate limit suppressed){C['reset']}")
    return 0


def cmd_watchdog(cfg, args):
    """Install, remove or inspect the launchd job that restarts a stalled agent."""
    import getpass
    root = cfg["root"]
    key = os.path.basename(root.rstrip("/")).lower()
    label = f"com.agentorchestrator.watchdog.{key}"
    plist_path = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
    # After a pip/uv install there is no scripts/ directory; there is a console
    # script on PATH. launchd needs an absolute path either way, so resolve
    # whichever one this installation actually has.
    script = shutil.which("ao-watchdog") or os.path.join(A.REPO, "scripts", "ao-watchdog")
    log = os.path.expanduser(f"~/.ao/watchdog-{key}.log")

    if args.action == "status":
        loaded = A.sh(f"launchctl list | grep {label}")
        print(f"label   {label}")
        print(f"plist   {'present' if os.path.exists(plist_path) else 'absent'}")
        print(f"loaded  {loaded if loaded else 'no'}")
        if os.path.exists(log):
            print(f"\nlast lines of {log}:")
            print(A.sh(f"tail -5 {log}"))
        return

    if args.action == "uninstall":
        A.sh(f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null || launchctl unload {plist_path} 2>/dev/null")
        if os.path.exists(plist_path):
            os.remove(plist_path)
        print(f"removed {label}")
        return

    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
    os.makedirs(os.path.expanduser("~/.ao"), exist_ok=True)
    open(plist_path, "w").write(PLIST.format(
        label=label, python=sys.executable, script=script, root=root,
        idle=args.idle_minutes, interval=args.interval, log=log))
    A.sh(f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null")
    out = A.sh(f"launchctl bootstrap gui/$(id -u) {plist_path} 2>&1") or "loaded"
    print(f"installed {label}")
    print(f"  checks every {args.interval}s · nudges after {args.idle_minutes}m idle")
    print(f"  log: {log}")
    print(f"  remove with: ao -C {root} watchdog uninstall")
    if "error" in out.lower():
        print(f"  launchctl: {out}")


def cmd_projects(cfg, args):
    ws = A.all_workspaces()
    if not ws:
        print("No local agent sessions found.")
        return
    print(f"{'last active':<12}{'status':<14}workspace")
    for r in ws:
        mins = int((time.time() - r["mtime"]) / 60)
        age = f"{mins}m" if mins < 90 else (f"{mins//60}h" if mins < 2880 else f"{mins//1440}d")
        col = C["green"] if mins < 5 else C["dim"]
        print(f"{col}{age:<12}{C['reset']}{r['status'][:13]:<14}{r['path']}")


def cmd_adapters(cfg, args):
    d = A.adapters_dir()
    rows = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json") or f == "cloud-generic.json":
            continue
        try:
            a = __import__("json").load(open(os.path.join(d, f)))
        except Exception:
            continue
        rows.append((a.get("id", f), a.get("verified", "?"),
                     "call-return" if a.get("observation_mode") == "call-return"
                     else (a.get("transcript", {}) or {}).get("kind", "—")))
    avail = A.tool_availability()
    print(f"{'adapter':<16}{'verified':<12}{'on this machine':<22}observation")
    for r in rows:
        col = C["green"] if r[1] == "full" else C["yellow"] if r[1] == "partial" else C["dim"]
        a = avail.get(r[0], {})
        if a.get("installed") and a.get("account"):
            here = f"{C['green']}installed + account{C['reset']}"
        elif a.get("installed"):
            here = f"{C['green']}installed{C['reset']}"
        elif a.get("account"):
            here = f"{C['yellow']}account, no CLI{C['reset']}"
        else:
            here = f"{C['dim']}—{C['reset']}"
        pad = 22 - len(A.re.sub(r"\033\[[0-9;]*m", "", here))
        print(f"{r[0]:<16}{col}{r[1]:<12}{C['reset']}{here}{' ' * max(1, pad)}{r[2]}")
    print(f"\n{C['dim']}Account detection via keyflip surfaces; it never reads the secret.{C['reset']}")


def cmd_doctor(cfg, args):
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
    print(f"quota source    {'keyflip' if A.sh('command -v keyflip') else '—'}")
    key = os.path.basename(root.rstrip("/")).lower()
    wd = A.sh(f"launchctl list | grep com.agentorchestrator.watchdog.{key}")
    print(f"watchdog        {C['green']}running{C['reset']}" if wd else
          f"watchdog        {C['dim']}not installed — ao watchdog install{C['reset']}")
    err = A.last_nudge_error(root)
    if err:
        mins = int((time.time() - err.get("at", 0)) / 60)
        print(f"last restart    {C['red']}failed {mins}m ago (exit {err.get('code')}){C['reset']}")
        print(f"                {C['dim']}{err.get('tail','')[:110]}{C['reset']}")
    else:
        print(f"last restart    {C['dim']}no failures recorded{C['reset']}")
    print(f"\n{C['dim']}Not implemented yet: commit-ok, slice, decide, "
          f"since, mcp serve.{C['reset']}")


def main():
    p = argparse.ArgumentParser(prog="ao", description="agent-orchestrator (observation layer)")
    p.add_argument("-C", "--root", help="project directory (default: nearest .ao/ or git root)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="one-shot summary")
    s.add_argument("-m", "--messages", type=int, default=6)
    s.set_defaults(fn=cmd_status)

    w = sub.add_parser("watch", help="live panel; leave it in a background terminal")
    w.add_argument("-i", "--interval", type=int, default=15)
    w.add_argument("-m", "--messages", type=int, default=8)
    w.add_argument("--all", action="store_true", help="one row per project instead of one panel")
    w.set_defaults(fn=cmd_watch)

    sub.add_parser("fleet", help="one-shot view of every project").set_defaults(fn=cmd_fleet)

    t = sub.add_parser("tail", help="recent messages from the implementer's transcript")
    t.add_argument("-n", type=int, default=5)
    t.set_defaults(fn=cmd_tail)

    m = sub.add_parser("mail", help="list, read or send coordination messages")
    m.add_argument("action", choices=["list", "read", "send"])
    m.add_argument("type", nargs="?", default="INFO")
    m.add_argument("topic", nargs="?")
    m.add_argument("--body")
    m.set_defaults(fn=cmd_mail)

    v = sub.add_parser("verify", help="run the declared gates and record the result")
    v.add_argument("-p", "--profile")
    v.add_argument("--wait", type=int, default=900,
                   help="seconds to wait if another project holds the machine gate lock")
    v.set_defaults(fn=cmd_verify)

    wd = sub.add_parser("watchdog", help="launchd job that restarts a stalled agent")
    wd.add_argument("action", choices=["install", "uninstall", "status"])
    wd.add_argument("--interval", type=int, default=120)
    wd.add_argument("--idle-minutes", type=float, default=6)
    wd.set_defaults(fn=cmd_watchdog)

    sub.add_parser("board", help="where each pre-authorised item is").set_defaults(fn=cmd_board)
    sub.add_parser("commit-ok", help="may this tree be committed? decided from evidence"
                   ).set_defaults(fn=cmd_commit_ok)
    lk = sub.add_parser("lock", help="run a heavy command under the machine-wide lock")
    lk.add_argument("--wait", type=int, default=1800)
    lk.add_argument("command", nargs=argparse.REMAINDER)
    lk.set_defaults(fn=cmd_lock)
    mc = sub.add_parser("mcp", help="serve project state to MCP clients (stdio)")
    mc.add_argument("action", choices=["serve", "config"], nargs="?", default="config")
    mc.add_argument("--allow-verify", action="store_true", help="expose the gate runner too")
    mc.set_defaults(fn=cmd_mcp)
    a2 = sub.add_parser("a2a", help="serve the board as A2A tasks (loopback HTTP)")
    a2.add_argument("action", choices=["serve", "info"], nargs="?", default="info")
    a2.add_argument("--port", type=int, default=8731)
    a2.set_defaults(fn=cmd_a2a)
    n = sub.add_parser("notices", help="alerts this project raised")
    n.add_argument("-n", type=int, default=12)
    n.add_argument("--all", action="store_true", help="include rate-limited ones")
    n.set_defaults(fn=cmd_notices)
    pr = sub.add_parser("prune", help="trim accumulated records and logs")
    pr.add_argument("--days", type=float, default=7)
    pr.add_argument("--keep-kb", type=int, default=64, help="log tail to keep")
    pr.add_argument("--evidence", action="store_true", help="also prune verifications and plans")
    pr.add_argument("--yes", action="store_true", help="apply (default is a dry run)")
    pr.set_defaults(fn=cmd_prune)
    h = sub.add_parser("hold", help="stop this project's agents and keep them stopped")
    h.add_argument("action", choices=["hold", "release", "status"], nargs="?", default="hold")
    h.add_argument("reason", nargs="?")
    h.add_argument("--by", default="architect")
    h.add_argument("--note", help="on release: what changed while the agent was stopped")
    h.add_argument("--grace", type=float, default=10, help="seconds before SIGKILL")
    h.set_defaults(fn=cmd_hold)
    sr = sub.add_parser("source", help="external work queues feeding the board")
    sr.add_argument("action", choices=["list", "status", "import"], nargs="?", default="status")
    sr.set_defaults(fn=cmd_source)
    sub.add_parser("projects", help="workspaces with a local agent session").set_defaults(fn=cmd_projects)
    sub.add_parser("adapters", help="adapter registry and verification status").set_defaults(fn=cmd_adapters)
    sub.add_parser("doctor", help="check this workspace's wiring").set_defaults(fn=cmd_doctor)

    args = p.parse_args()
    if not getattr(args, "fn", None):
        p.print_help()
        return 0
    cfg = A.load_config(A.find_root(args.root))
    return args.fn(cfg, args) or 0


if __name__ == "__main__":
    sys.exit(main())
