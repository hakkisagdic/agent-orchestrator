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
import subprocess
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
  <array>{python_arg}<string>{script}</string>
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
    _urgent_banner(cfg)
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
                # Exit zero is not proof that anything ran. A test runner whose
                # worker pool fails can collect zero tests and exit 0, and the
                # gate stays green having measured nothing — the second pilot's
                # agent found vitest doing exactly that. A gate that declares
                # `min_tests` must see at least that many actually execute.
                need = int(g.get("min_tests", 0) or 0)
                if need and (p_ + f_) < need:
                    passed = False
                    detail += f" — only {p_ + f_} ran, {need} required"
            elif g.get("min_tests"):
                passed = False
                detail = f"exit {code}, but no test count found and min_tests={g['min_tests']}"
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
    else:
        # `reviewer != implementer` was stated in the safety model and enforced
        # nowhere: the implementer wrote its own review and authority was granted
        # on it. A model reviewing its own output shares its own blind spots, so
        # the verdict measured nothing it did not already believe.
        try:
            body = open(os.path.join(root, cfg["reviews"], revs[0][0]),
                        errors="replace").read(4000)
        except OSError:
            body = ""
        m = A.re.search(r"reviewer:\s*`([^`]+)`", body)
        who = m.group(1) if m else None
        impl_id = (cfg.get("implementer") or {}).get("session") or ""
        if not who:
            reasons.append(f"{revs[0][0]} names no reviewer — cannot tell who wrote it")
        elif impl_id and (who in impl_id or impl_id in who):
            reasons.append(f"the review was written by the implementer ({who})")
        # The review's own tree digest must match, or it described a different tree.
        tm = A.re.search(r"tree:\s*`([^`]+)`", body)
        if tm and tm.group(1) != now:
            reasons.append(f"{revs[0][0]} reviewed a different tree — re-run `ao review`")
    held = A.hold_state(root)
    if held:
        reasons.append(f"project is held by {held.get('by')}: {held.get('reason','')}")
    # The strongest lever available against a protocol that cannot interrupt. We
    # cannot stop the agent working, but we decide whether the work may land — so
    # an unacknowledged urgent message blocks the commit rather than being missed.
    for m in A.urgent_messages(root, cfg):
        reasons.append(f"urgent message unacknowledged: {m['id']} — {m['title']}")
    if not A.sh("git status --porcelain", cwd=root):
        reasons.append("nothing to commit")

    if reasons:
        print(f"{C['red']}{C['b']}REFUSED{C['reset']}")
        for r in reasons:
            print(f"  {C['red']}·{C['reset']} {r}")
        A.record_authority(root, False, reasons, now, (ver or {}).get("id"))
        return 1

    token = f"C-{int(time.time())}"
    try:
        rbody = open(os.path.join(root, cfg["reviews"], revs[0][0]),
                     errors="replace").read(4000)
        rwho = (A.re.search(r"reviewer:\s*`([^`]+)`", rbody) or [None, None])[1]
    except Exception:
        rwho = None
    print(f"{C['green']}{C['b']}GRANTED{C['reset']}  {token}")
    print(f"  {C['dim']}verified{C['reset']} {ver['id']} · {C['dim']}review{C['reset']} {revs[0][0]}")
    print(f"  {C['dim']}tree{C['reset']}     {now[:23]}…")
    print(f"\n  {C['dim']}push is not covered by this grant and never will be.{C['reset']}")
    A.record_authority(root, True, [], now, ver["id"], token,
                       review=revs[0][0], reviewer=rwho)
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


def cmd_a2a_mcp(cfg, args):
    """Expose configured A2A agents to an MCP client."""
    if args.action != "serve":
        reg = os.path.join(cfg["root"], ".ao", "a2a-agents.json")
        exe = shutil.which("ao") or sys.argv[0]
        print(f"{C['b']}Add to an MCP client's config:{C['reset']}")
        print(json.dumps({"mcpServers": {"a2a": {
            "command": exe, "args": ["-C", cfg["root"], "a2a-mcp", "serve"]}}}, indent=2))
        print(f"\n{C['b']}Then register agents in {reg}:{C['reset']}")
        print(json.dumps({"weather": {"url": "https://agent.example.com/a2a/v1",
                                      "headers": {"Authorization": "Bearer …"}}}, indent=2))
        print(f"{C['dim']}The dialect is read from each agent's card; set "
              f'"version": "0.3" to pin one.{C["reset"]}')
        return 0
    return _serve("a2a_mcp", cfg, [])


def cmd_a2a(cfg, args):
    """Serve this project's board as A2A tasks, on loopback."""
    if args.action != "serve":
        print(f"{C['b']}ao a2a serve --port {args.port}{C['reset']}")
        print(f"  card   http://127.0.0.1:{args.port}/.well-known/agent-card.json")
        print(f"  tasks  http://127.0.0.1:{args.port}/tasks")
        return 0
    return _serve("a2a", cfg, ["--port", str(args.port)])


def _urgent_banner(cfg):
    """Print anything urgent before the caller does something expensive.

    This is the injection point MCP cannot provide. The agent came here on its
    own, on the way to a heavy operation, and that is the last cheap moment to
    tell it the ground moved.
    """
    msgs = A.urgent_messages(cfg["root"], cfg)
    if not msgs:
        return msgs
    print(f"{C['red']}{C['b']}{'━' * 62}{C['reset']}")
    print(f"{C['red']}{C['b']}  {len(msgs)} URGENT message(s) from the architect, unacknowledged"
          f"{C['reset']}")
    for m in msgs:
        print(f"{C['red']}  ·{C['reset']} {C['b']}{m['title']}{C['reset']}")
        print(f"    {C['dim']}{m['id']}{C['reset']}")
    print(f"{C['dim']}  Read them (ao_inbox, or the file) and acknowledge before "
          f"continuing.{C['reset']}")
    print(f"{C['red']}{C['b']}{'━' * 62}{C['reset']}\n")
    # Flush before returning. The caller is about to hand the terminal to a
    # subprocess writing straight to the fd, and buffered output would surface
    # *after* the thing it was warning about — a warning nobody can act on.
    sys.stdout.flush()
    return msgs


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
    _urgent_banner(cfg)
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


def cmd_credits(cfg, args):
    """Credit usage: the provider's own figure, with a local estimate behind it.

    Prefer the account. `GetUsageLimits` returns exactly what the dashboard shows,
    authenticated with the token the CLI already holds, so there is no reason to
    estimate when the real number is one request away. The transcript reading
    stays as the offline path and is labelled as the floor it is — measured here
    at roughly two thirds of the true figure, because it cannot see what ran on
    another machine.
    """
    from datetime import date, datetime

    acct = None if args.offline else A.kiro_account_usage()
    if acct and not acct.get("error") and not acct.get("expired"):
        used, limit = acct["used"], acct["limit"]
        pct = used / limit * 100 if limit else 0
        col = C["red"] if pct > 90 else C["yellow"] if pct > 70 else C["green"]
        filled = int(pct / 5)
        reset = datetime.fromtimestamp(acct["reset_at"]).strftime("%d %b") if acct.get("reset_at") else "?"
        print(f"{C['b']}{acct.get('plan') or 'account'}{C['reset']}"
              f"{C['dim']}  resets {reset}{C['reset']}")
        print(f"\n   {col}{'█' * filled}{'░' * (20 - filled)}{C['reset']} {pct:5.1f}%"
              f"   {C['b']}{used:,.2f}{C['reset']} of {limit:,.0f}")
        print(f"   {C['b']}{limit - used:,.2f}{C['reset']} remaining")
        if acct.get("overage_status") == "DISABLED":
            print(f"\n   {C['dim']}overage disabled — work stops at the limit, it does not "
                  f"bill on{C['reset']}")
        elif acct.get("overage_cap"):
            print(f"\n   {C['dim']}overage {acct['overage_now']:,.2f} of "
                  f"{acct['overage_cap']:,.0f} at {acct.get('overage_rate')}/credit{C['reset']}")

        if args.local:
            u = A.credit_usage()
            here = sum(v for d, v in u["days"].items()
                       if d >= date.today().replace(day=1).isoformat())
            share = here / used * 100 if used else 0
            print(f"\n   {C['dim']}transcripts on this machine account for {here:,.0f} "
                  f"({share:.0f}%) of it{C['reset']}")
        return 0

    if acct and acct.get("expired"):
        print(f"{C['yellow']}The CLI's token has expired.{C['reset']} "
              f"Run {C['b']}kiro-cli login{C['reset']} and try again.")
    elif acct and acct.get("error"):
        print(f"{C['yellow']}Account lookup failed:{C['reset']} {acct['error']}")
    elif not args.offline:
        print(f"{C['dim']}No account token available; falling back to transcripts.{C['reset']}")

    u = A.credit_usage()
    if not u["days"]:
        print(f"{C['dim']}No local sessions with usage records either.{C['reset']}")
        return 1
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    reset = args.reset_day or (adapter.get("billing") or {}).get("reset_day") or 1
    reset = max(1, min(28, int(reset)))
    periods = {}
    for d, v in u["days"].items():
        y, m, dd = (int(x) for x in d.split("-"))
        if dd < reset:
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        periods[f"{y:04d}-{m:02d}"] = periods.get(f"{y:04d}-{m:02d}", 0) + v
    print(f"\n{C['b']}{C['mag']}── ESTIMATE FROM LOCAL TRANSCRIPTS {'─' * 22}{C['reset']}")
    for pm in sorted(periods):
        print(f"   {pm}   {periods[pm]:>9,.2f}")
    print(f"\n{C['dim']}A floor: only sessions stored here are visible. Measured against"
          f"\nthe account figure it came to about two thirds of the truth.{C['reset']}")
    return 0


def cmd_telegram(cfg, args):
    """Set up, test, or run the phone channel.

    The inbound direction is the reason this exists. When the architect's quota
    runs out everything stops — twice in one day here — and a person who can write
    a decision from a phone at that moment keeps it moving. Their message becomes
    an urgent file in the mailbox, which is what the architect's own decisions
    already are.
    """
    from . import telegram
    conf = telegram.CONF
    c = telegram.config()
    label = f"com.agentorchestrator.telegram.{os.path.basename(cfg['root']).lower()}"

    if args.action == "setup":
        print(f"{C['b']}1.{C['reset']} Telegram: {C['b']}@BotFather{C['reset']} → /newbot → token")
        print(f"{C['b']}2.{C['reset']} Bota bir mesaj yaz, sonra chat id'ni al:")
        print(f"   {C['dim']}curl -s \"https://api.telegram.org/bot<TOKEN>/getUpdates\" \\\\{C['reset']}")
        print(f"   {C['dim']}  | python3 -c \"import sys,json; print([u['message']['chat']['id'] for u in json.load(sys.stdin)['result']])\"{C['reset']}")
        print(f"{C['b']}3.{C['reset']} Dosyayı {C['b']}sen{C['reset']} yaz — bir bot token'ı "
              f"kimlik bilgisidir; ne repoya ne bir sohbete girer:")
        print(f"   {C['dim']}mkdir -p ~/.ao{C['reset']}")
        print(f"   {C['dim']}echo '{{\"token\":\"<BOT_TOKEN>\",\"chats\":[\"<CHAT_ID>\"]}}' > {conf}{C['reset']}")
        print(f"   {C['dim']}chmod 600 {conf}{C['reset']}")
        print(f"\n{C['b']}4.{C['reset']} {C['b']}ao telegram test{C['reset']} → {C['b']}ao telegram install{C['reset']}")
        print(f"\n{C['dim']}The allowlist is not optional: an inbound channel without one")
        print(f"is an authority surface open to whoever finds the bot.{C['reset']}")
        return 0

    if args.action == "test":
        if not c:
            print(f"{C['red']}No config at {conf}{C['reset']} — run {C['b']}ao telegram setup{C['reset']}")
            return 1
        name = cfg.get("project") or os.path.basename(cfg["root"])
        n = telegram.send(f"*{name}* — bağlantı testi.\n\nBu sohbete yazdığın her mesaj "
                          f"acil karar olarak kutuya düşer ve uygulayıcı onaylamadan "
                          f"commit edemez.\n\nKomutlar: /status /board /credits /notices /fleet",
                          cfg["root"])
        print(f"{C['green']}sent to {n} chat(s){C['reset']}" if n else
              f"{C['red']}send failed{C['reset']} — check the token and the chat ids")
        return 0 if n else 1

    if args.action == "poll":
        return _serve("telegram", cfg, ["--once"] if args.once else [])

    if args.action == "uninstall":
        A.sh(f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null")
        p = os.path.join(A.HOME, "Library", "LaunchAgents", label + ".plist")
        if os.path.exists(p):
            os.remove(p)
        print(f"removed {label}")
        return 0

    if args.action == "install":
        if not c:
            print(f"{C['red']}No config{C['reset']} — run ao telegram setup first")
            return 1
        exe = shutil.which("ao") or os.path.abspath(sys.argv[0])
        log = os.path.join(A.HOME, ".ao", f"telegram-{os.path.basename(cfg['root']).lower()}.log")
        # KeepAlive rather than StartInterval: long polling holds the connection
        # open, so the job wants restarting when it ends, not running on a clock.
        plist = os.path.join(A.HOME, "Library", "LaunchAgents", label + ".plist")
        open(plist, "w").write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            f'  <key>Label</key><string>{label}</string>\n'
            '  <key>ProgramArguments</key>\n'
            f'  <array><string>{exe}</string><string>-C</string><string>{cfg["root"]}</string>\n'
            '    <string>telegram</string><string>poll</string></array>\n'
            '  <key>KeepAlive</key><true/>\n  <key>RunAtLoad</key><true/>\n'
            f'  <key>StandardOutPath</key><string>{log}</string>\n'
            f'  <key>StandardErrorPath</key><string>{log}</string>\n'
            '</dict></plist>\n')
        A.sh(f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null")
        A.sh(f"launchctl bootstrap gui/$(id -u) {plist} 2>&1")
        print(f"{C['green']}installed{C['reset']} {label}")
        return 0

    print("config          " + (f"{C['green']}{conf}{C['reset']}" if c else
                                f"{C['red']}missing{C['reset']} — ao telegram setup"))
    if c:
        print(f"chats allowed   {len(c['chats'])}")
    print("poller          " + (f"{C['green']}running{C['reset']}"
                                if A.sh(f"launchctl list | grep {label}") else
                                f"{C['dim']}not installed — ao telegram install{C['reset']}"))
    return 0


def _decision_text(rec):
    lines = [f"❓ *{rec['question']}*"]
    if rec.get("context"):
        lines.append(f"\n_{rec['context']}_")
    if rec.get("slice"):
        lines.append(f"\ndilim: `{rec['slice']}`")
    lines.append("")
    for o in rec["options"]:
        lines.append(f"*{o['key']})* {o['label']}")
    lines.append(f"\nCevap: butona bas, ya da `{rec['id']} <harf>` yaz. "
                 f"Serbest metin için `{rec['id']} x <cevabın>`.")
    return "\n".join(lines)


def cmd_ask(cfg, args):
    """Pose a decision the implementer cannot make for itself.

    A blocker written as prose costs minutes to answer from a phone. The same
    blocker as a question with options costs one tap, and that difference decides
    whether a run survives the hours when nobody is at a desk.
    """
    root = cfg["root"]
    if not args.question:
        print(f"usage: {C['b']}ao ask \"question\" \"option a\" \"option b\" …{C['reset']}")
        return 0
    rec = A.ask(root, args.question, args.options or [], context=args.context,
                slice_id=args.slice)
    print(f"{C['b']}{rec['id']}{C['reset']}  {rec['question']}")
    for o in rec["options"]:
        print(f"   {C['b']}{o['key']}){C['reset']} {o['label']}")
    try:
        from . import telegram
        kb = [[{"text": f"{o['key']}) {o['label'][:40]}",
                "callback_data": f"{rec['id']}:{o['key']}"}]
              for o in rec["options"] if not o.get("free_text")]
        n = telegram.send(_decision_text(rec), root, keyboard=kb)
        print(f"\n{C['dim']}sent to {n} chat(s){C['reset']}" if n else
              f"\n{C['dim']}no phone channel configured — answer with "
              f"`ao answer {rec['id']} <key>`{C['reset']}")
    except Exception as e:
        print(f"\n{C['dim']}phone delivery skipped: {e}{C['reset']}")
    return 0


def cmd_answer(cfg, args):
    """Answer a pending decision from the terminal."""
    rec = A.answer(cfg["root"], args.id, " ".join(args.value), by="terminal")
    if not rec:
        print(f"{C['red']}no such decision{C['reset']} {args.id}")
        return 1
    print(f"{C['green']}answered{C['reset']} {rec['id']}: {rec['answer']}")
    return 0


def cmd_decisions(cfg, args):
    """Open and recently answered questions."""
    rows = A.decisions(cfg["root"])
    if not rows:
        print(f"{C['dim']}No decisions recorded.{C['reset']}")
        return 0
    for r in sorted(rows, key=lambda x: x["asked_at"], reverse=True)[:args.n]:
        age = int((time.time() - r["asked_at"]) / 60)
        if r["state"] == "open":
            print(f"{C['yellow']}OPEN{C['reset']}     {C['b']}{r['id']}{C['reset']}  "
                  f"{r['question']}  {C['dim']}{age}m{C['reset']}")
            for o in r["options"]:
                print(f"           {C['dim']}{o['key']}){C['reset']} {o['label']}")
        else:
            print(f"{C['green']}answered{C['reset']} {C['b']}{r['id']}{C['reset']}  "
                  f"{r['question']}  {C['dim']}→ {r['answer']} "
                  f"({r.get('answered_by')}){C['reset']}")
    return 0


def cmd_handoff(cfg, args):
    """Write down everything a successor needs, and send it.

    The centre running out of quota does not break delivery — reports are written
    before the quota gate and the transport is HTTP, so a question still reaches a
    phone when the architect is dead. What breaks is that nobody decides, and the
    person who could is handed "out of quota" and nothing else.

    So say what is actually stopped, what it is waiting on, and what would move it.
    Twice in one day here the state that would have unblocked a run existed only
    inside a conversation nobody else could reach; this is that state, on disk,
    where the next actor — a human, a fresh architect, tomorrow's session — can
    pick it up.
    """
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    bd = A.board(root)
    g = A.git_state(root)
    opens = A.decisions(root, "open")
    revs = A.reviews(root, cfg["reviews"], limit=1)
    acct = A.kiro_account_usage() if impl.get("adapter") == "kiro" else None
    state, age, doing = A.busy(cfg, adapter) if impl else ("unknown", None, "")

    lines = [f"# Devir — {cfg.get('project') or os.path.basename(root)}",
             f"_{datetime.now():%Y-%m-%d %H:%M}_", ""]
    if args.reason:
        lines += [f"**Sebep:** {args.reason}", ""]

    lines += ["## Şu an", f"- uygulayıcı: **{state}**"
              + (f", son yazım {age // 60}dk önce" if age is not None else ""),
              f"- HEAD `{(g['log'][0] if g['log'] else '?')[:60]}`",
              f"- {len(g['dirty'])} dosya commit'siz, {g['ahead']} commit push'suz"]
    if revs:
        lines.append(f"- son review: {revs[0][1]} ({revs[0][0]})")
    if doing:
        lines.append(f"- diyor ki: _{doing[:200]}_")
    if acct and not acct.get("error"):
        lines.append(f"- kredi: {acct['used']:,.0f} / {acct['limit']:,.0f}"
                     f" ({acct['limit'] - acct['used']:,.0f} kaldı)")

    if opens:
        lines += ["", "## Cevap bekleyen kararlar — **bunlar işi açar**"]
        for d in opens:
            lines.append(f"- `{d['id']}` {d['question']}")
            for o in d["options"]:
                lines.append(f"    - `{d['id']} {o['key']}` → {o['label']}")

    if bd["blocked"]:
        lines += ["", "## Blocked"]
        for it in bd["blocked"]:
            lines.append(f"- **{it['id']}** {it['title']} — "
                         f"{it['notes'].get('needs', 'sebep kayıtlı değil')}")
    if bd["running"]:
        lines += ["", "## Yürüyen"]
        for it in bd["running"]:
            lines.append(f"- **{it['id']}** {it['title']}")
    if bd["queued"]:
        lines += ["", f"## Sıradaki ({len(bd['queued'])} madde)",
                  f"- **{bd['queued'][0]['id']}** {bd['queued'][0]['title']}"]

    lines += ["", "## Devralan ne yapabilir",
              "- Bekleyen kararı cevapla: telefondan butona bas, ya da "
              "`ao answer <id> <harf>`",
              "- Serbest karar yaz: Telegram'a mesaj at — acil olarak kutuya düşer",
              "- Durumu gör: `ao status`, `ao board`, `ao decisions`",
              "", "_push, PR ve epic kapatma hiçbir devirde aktarılmaz._"]

    text = "\n".join(lines)
    path = os.path.join(root, cfg["mailbox"],
                        f"{datetime.now():%Y%m%d-%H%M}-fable-to-anyone-DEVIR.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text + "\n")
    print(text)

    if not args.no_send:
        try:
            from . import telegram
            head = text.split("## Devralan")[0]
            n = telegram.send(head[:3800], root)
            print(f"\n{C['dim']}sent to {n} chat(s) · saved to "
                  f"{os.path.relpath(path, root)}{C['reset']}")
        except Exception as e:
            print(f"\n{C['dim']}saved; phone delivery skipped: {e}{C['reset']}")
    return 0


REVIEW_PROMPT = """Sen bu deponun BAĞIMSIZ gözden geçireni sin. Kodu sen yazmadın ve
yazanı savunmuyorsun.

İncelenecek: aşağıdaki diff. Kabul sınırı: {boundary}

Şunu ara, sırayla:
1. Kabul sınırının karşılanmadığı yerler — iddia edilen ile yapılan arasındaki fark
2. Doğruluk hataları: yanlış sonuç, kaçırılan durum, sessiz başarısızlık
3. Güvenlik/yetki sınırı ihlalleri: fixture kanıtının production gibi sunulması,
   yetki yüzeyinin genişlemesi, fail-open davranış
4. Testin gerçekten ne kanıtladığı — geçen test, doğru şeyi test etmiyor olabilir

Bulmadığın şeyi yazma. Bulgu yoksa bunu açıkça söyle; boş bir review, uydurulmuş
bir bulgudan iyidir.

Çıktını TAM OLARAK şu biçimde ver, başka hiçbir şey yazma:

VERDICT: APPROVED  (ya da NEEDS_CHANGES)
BLOCKER: <n>
HIGH: <n>
MEDIUM: <n>
LOW: <n>

## Bulgular
- [SEVERITY] dosya:satır — tek cümlelik iddia
  Nasıl bozulur: <somut girdi/durum → yanlış çıktı>

BLOCKER veya HIGH varsa VERDICT mutlaka NEEDS_CHANGES olmalı."""


def cmd_review(cfg, args):
    """Review the working tree with an actor that did not write it.

    `reviewer != implementer` is stated in the safety model and was, until this
    command, enforced nowhere: the implementer wrote its own review and
    `commit-ok` granted authority on it. A model reviewing its own output shares
    its own blind spots, so the verdict measured nothing that the implementer had
    not already believed.

    The reviewer is configured separately and should be a different family where
    one is available. Its identity is recorded in the review, and commit-ok
    refuses a review whose author is the implementer.
    """
    import subprocess
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    rv = cfg.get("reviewer") or {}
    if not rv.get("argv"):
        print(f"{C['yellow']}No reviewer configured.{C['reset']} Add to .ao/config.json:")
        print(json.dumps({"reviewer": {
            "id": "claude-reviewer", "family": "anthropic",
            "argv": ["claude", "-p", "{prompt}", "--model", "claude-opus-5",
                     "--allowedTools", "Read,Grep,Glob"]}}, indent=2))
        print(f"\n{C['dim']}It must not be the implementer. A model reviewing its own")
        print(f"output shares its own blind spots.{C['reset']}")
        return 1
    if rv.get("id") and rv["id"] == impl.get("session"):
        print(f"{C['red']}The reviewer is the implementer.{C['reset']} That is the one "
              f"configuration this refuses.")
        return 2

    diff = A.sh("git diff HEAD", cwd=root, timeout=60) or ""
    untracked = [l[3:] for l in (A.sh("git status --porcelain", cwd=root) or "").split("\n")
                 if l.startswith("?? ") and not l[3:].startswith((".ao/", "agent-mail/"))]
    for f in untracked[:20]:
        p = os.path.join(root, f)
        if os.path.isfile(p) and os.path.getsize(p) < 200_000:
            try:
                diff += f"\n--- NEW FILE {f} ---\n" + open(p, errors="replace").read()
            except OSError:
                pass
    if not diff.strip():
        print(f"{C['dim']}Nothing to review — the tree matches HEAD.{C['reset']}")
        return 0

    boundary = args.boundary or ""
    if not boundary:
        for it in A.board(root)["running"]:
            boundary = it["notes"].get("acceptance") or it["notes"].get("scope") or it["title"]
            break
    boundary = boundary or "not declared — say so as a finding"

    prompt = REVIEW_PROMPT.format(boundary=boundary) + "\n\n--- DIFF ---\n" + diff[:400_000]
    argv = [x.replace("{prompt}", prompt) for x in rv["argv"]]
    exe = shutil.which(argv[0])
    if not exe:
        print(f"{C['red']}{argv[0]} not on PATH{C['reset']}")
        return 1
    argv[0] = exe
    print(f"{C['dim']}reviewing {len(diff):,} chars against: {boundary[:70]}{C['reset']}")
    r = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                       timeout=args.timeout)
    out = (r.stdout or r.stderr or "").strip()
    if not out:
        print(f"{C['red']}reviewer produced nothing{C['reset']} (exit {r.returncode})")
        return 1

    verdict = "NEEDS_CHANGES"
    m = A.re.search(r"VERDICT:\s*(APPROVED|NEEDS_CHANGES)", out)
    if m:
        verdict = m.group(1)
    sev = {k: int(A.re.search(rf"{k}:\s*(\d+)", out).group(1))
           if A.re.search(rf"{k}:\s*(\d+)", out) else 0
           for k in ("BLOCKER", "HIGH", "MEDIUM", "LOW")}
    # A verdict that contradicts its own findings is not a verdict.
    if verdict == "APPROVED" and (sev["BLOCKER"] or sev["HIGH"]):
        verdict = "NEEDS_CHANGES"

    d = os.path.join(root, cfg["reviews"])
    os.makedirs(d, exist_ok=True)
    head = A.sh("git rev-parse --short HEAD", cwd=root)
    name = f"{datetime.now():%Y-%m-%d-%H%M%S}-{head}.md"
    open(os.path.join(d, name), "w").write(
        f"# Review {name}\n\n"
        f"- reviewer: `{rv.get('id') or argv[0]}`  family: `{rv.get('family','?')}`\n"
        f"- implementer: `{impl.get('adapter')}/{(impl.get('session') or '')[:20]}`\n"
        f"- tree: `{A.tree_digest(root)}`\n- boundary: {boundary}\n\n{out}\n")
    A.record_notice(root, "review", f"{verdict} {sev}", sent=False, key="review")

    col = C["green"] if verdict == "APPROVED" else C["yellow"]
    print(f"\n{col}{C['b']}{verdict}{C['reset']}  "
          f"BLOCKER {sev['BLOCKER']} · HIGH {sev['HIGH']} · "
          f"MEDIUM {sev['MEDIUM']} · LOW {sev['LOW']}")
    print(f"{C['dim']}{cfg['reviews']}/{name}{C['reset']}")
    if verdict != "APPROVED":
        for line in out.split("\n"):
            if line.strip().startswith("- ["):
                print("  " + line.strip()[:150])
    return 0 if verdict == "APPROVED" else 1


def cmd_digest(cfg, args):
    """What happened in a window — read from the ledgers, never estimated.

    Event alerts answer "did something just occur". They cannot answer "is this
    week going well", and reconstructing that from thirty notifications is asking
    a human to do the tool's job.

    It also absorbs the question people actually ask most: *why is nothing
    moving*. That is not a separate command — it is the same facts, read at the
    top instead of the bottom.
    """
    root = cfg["root"]
    d = A.digest(root, cfg, args.days)
    win = ("24 saat" if args.days == 1 else
           f"{int(args.days)} gün" if args.days == int(args.days) else f"{args.days} gün")

    # Lead with the blocking answer. Someone opening this at 3am wants "what is
    # in the way", not a scoreboard.
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    state, age, doing = A.busy(cfg, adapter) if impl else ("unknown", None, "")
    spin = A.spinning(root)
    print(f"{C['b']}{cfg.get('project') or os.path.basename(root)}{C['reset']}"
          f"{C['dim']}  son {win}{C['reset']}\n")

    col = {"working": C["green"], "slowing": C["yellow"]}.get(state, C["red"])
    line = f"{col}{state}{C['reset']}"
    if age is not None:
        line += f"{C['dim']}, son yazım {age // 60}dk önce{C['reset']}"
    if spin:
        line += f"  {C['red']}⚠ {spin}dk meşgul, üretim yok{C['reset']}"
    print(f"  durum    {line}")
    if doing:
        print(f"  {C['dim']}↳ {doing[:110]}{C['reset']}")

    if d["decisions"]["open"]:
        print(f"\n  {C['yellow']}{d['decisions']['open']} cevap bekleyen karar{C['reset']}"
              f"{C['dim']} — bunlar işi açar: ao decisions{C['reset']}")
    for b in d["blocked"]:
        print(f"  {C['red']}⊘{C['reset']} {b['id']}  {C['dim']}{b['needs'][:88]}{C['reset']}")

    print(f"\n{C['b']}{C['mag']}── İNEN İŞ {'─' * 46}{C['reset']}")
    print(f"  {C['b']}{len(d['commits'])}{C['reset']} commit"
          f"{C['dim']}, {d['unpushed']} push'suz{C['reset']}")
    for c in d["commits"][:args.n]:
        print(f"    {C['dim']}{c['sha']}{C['reset']} {c['subject'][:76]}")

    v, a, r = d["verifications"], d["authority"], d["reviews"]
    print(f"\n{C['b']}{C['mag']}── KAPILAR {'─' * 46}{C['reset']}")
    vf = f", {C['red']}{v['failed']} düştü{C['reset']}" if v["failed"] else ""
    rc = f", {C['yellow']}{r['changes']} değişiklik{C['reset']}" if r["changes"] else ""
    ar = f", {C['yellow']}{a['refused']} reddedildi{C['reset']}" if a["refused"] else ""
    print(f"  doğrulama  {C['green']}{v['passed']} geçti{C['reset']}{vf}")
    print(f"  review     {C['green']}{r['approved']} APPROVED{C['reset']}{rc}")
    print(f"  commit-ok  {C['green']}{a['granted']} verildi{C['reset']}{ar}")
    # A refusal repeated all week is a process problem, not an incident.
    for reason, n in d["refusal_reasons"]:
        if n > 1:
            print(f"    {C['dim']}{n}× {reason}{C['reset']}")

    dec = d["decisions"]
    if dec["asked"]:
        med = f"{dec['median_minutes']}dk" if dec["median_minutes"] is not None else "—"
        print(f"\n  karar      {dec['answered']}/{dec['asked']} cevaplandı"
              f"{C['dim']}, ortanca {med}{C['reset']}")

    if d.get("credits"):
        c = d["credits"]
        pct = c["used"] / c["limit"] * 100 if c["limit"] else 0
        cc = C["red"] if pct > 90 else C["yellow"] if pct > 70 else C["green"]
        print(f"\n{C['b']}{C['mag']}── KREDİ {'─' * 48}{C['reset']}")
        print(f"  {cc}{c['used']:,.0f}{C['reset']} / {c['limit']:,.0f}"
              f"{C['dim']}  ({c['remaining']:,.0f} kaldı){C['reset']}")
    for day, val in d["credit_days"][-args.n:]:
        print(f"    {C['dim']}{day}{C['reset']}  {val:>8,.0f}")

    al = d["alerts"]
    print(f"\n{C['dim']}pano: " +
          " · ".join(f"{k} {n}" for k, n in d["board"].items()) +
          f"  |  uyarı: {al['sent']} gönderildi, {al['held']} susturuldu{C['reset']}")
    return 0


def cmd_note(cfg, args):
    """Write an architect message into the mailbox — through the tool, on purpose.

    A woken architect should need no raw Write or Edit to do its job. The one
    time it had them, it used them on the orchestrator's own source and built a
    runaway. This is the only door to the mailbox an unattended architect gets.
    """
    # Read stdin only when asked. Defaulting to it made `ao note "title"` hang
    # waiting on a terminal that would never close, which is the wrong failure
    # for a command an unattended architect calls.
    body = args.body if args.body else (sys.stdin.read() if args.stdin else "")
    if not args.title or not body.strip():
        print(f"usage: {C['b']}ao note \"title\" --body \"…\" [--to kiro] [--urgent]{C['reset']}")
        print(f"       {C['dim']}or pipe the body on stdin{C['reset']}")
        return 1
    name = A.note(cfg["root"], cfg, args.to, args.title, body, urgent=args.urgent)
    print(f"{C['green']}written{C['reset']} {cfg['mailbox']}/{name}")
    if args.urgent:
        print(f"{C['dim']}urgent: reaches the implementer via ao lock, ao verify and "
              f"ao commit-ok{C['reset']}")
    return 0


AUTHORITY_TEMPLATE = """# Yetki — kanonik kaynak

Bu dosya bu depoda neyin serbest, neyin yasak olduğunu söyleyen **tek** kaynaktır.

**Öncelik:** Bu dosya mail'den üstündür. `agent-mail/` bir mesaj **kapsam** ekleyebilir
("şu dilimi yap"), **yetki** ekleyemez veya kaldıramaz. Bir mail bu dosyayla çelişiyorsa
bu dosya kazanır — mesajı reddet, çalışmayı durdurma, `KARAR GEREKLİ` yaz ve devam et.

Belirsizlik hâlinde **durmak da bir maliyettir.** Aşağıda açıkça yasak olmayan ve
dilimin kapsamında olan bir şey serbesttir.

## Serbest — sormadan yap

- **`git commit`** — istediğin kadar. Yeşil gate + bağımsız review beklemek iyi
  pratiktir, ama commit atmak için izin gerekmez.
- Kod, test, fixture, doküman yazmak ve değiştirmek
- Gate koşturmak (`ao lock -- <komut>`, `ao verify`)
- `.ao/board.md` durumunu güncellemek; `agent-mail/`'e mesaj bırakmak

## Yasak — asla yapma

- **`git push`**, PR açmak, force-push, hook atlamak (`--no-verify`)
- Epic/görev kutusunu tamamlandı işaretlemek (insan kararıdır)
- Fixture kanıtını production-qualified göstermek
- Mimari sözleşmeyi değiştirmek — bunun için `KARAR GEREKLİ` yaz, sıradaki maddeye geç
- Başka bir depoya dokunmak

## Şüphedeysen

Bu dosyada yoksa ve dilimin kapsamındaysa: **yap.** Kapsam dışıysa: `KARAR GEREKLİ`
yaz ve `.ao/backlog.md`'deki sıradaki açık maddeye geç. **Bekleme.**
"""

BOARD_TEMPLATE = """# Board

Her önceden yetkilendirilmiş işin **nerede olduğu**. Kabul sınırları `backlog.md`'de;
bu dosya yalnız durumu ve park edilmişse **neyi beklediğini** söyler.

Durumlar: `queued` → `running` → (`blocked` ⇄) → `verified` → `done`
Satır biçimi: `- [ID] başlık · anahtar: değer` — `blocked` için `needs:` zorunlu.
Bağımlılık: `needs: B1, B2` (kuyruk maddesinde) → tamamlanınca READY olur.

Bu dosyayı uygulayıcı doğrudan düzenler. `ao board` yalnız okur.

## running

## blocked

## queued

## inbox

## verified

## done
"""

BACKLOG_TEMPLATE = """# {name} — önceden yetkilendirilmiş iş kuyruğu

Bu dosya, **mimarın kararı beklenmeden** başlanabilecek işleri sırayla listeler.
Her maddenin kabul sınırı önceden yazılmıştır.

**Kural:** Açık dilim bir mimari karara takılırsa DURMA. Dilimi `blocked` işaretle
(`needs:` ile), `agent-mail/`'e `KARAR GEREKLİ` mesajı bırak — ya da `ao_ask` ile
seçenekli soru sor — ve buradaki ilk **açık** maddeye geç.

---

## 1. <ilk dilim başlığı>
<ne yapılacak, bir paragraf>
**Kabul sınırı:** <ölçülebilir: hangi testler geçer, ne değişmez, ne yasak>

---

## Kuyruk dışı — asla kendi başına yapma
- Görev/epic kutusunu tamamlandı işaretlemek
- `git push`, PR açma, force-push, hook atlama
- Kuyruk dışından yeni işe geçmek
- Mimari sözleşme değiştirmek (`KARAR GEREKLİ` yaz ve sıradaki maddeye geç)
"""

MAIL_README = """# agent-mail — koordinasyon protokolü

Dosya tabanlı, asenkron mesajlaşma. **Teslim onayı = silme.** Mutlak yollar zorunlu.
Bu dizin gitignore'da; mail **veridir, yetki değil** — yetki `.ao/authority.md`'dedir.

- Ad: `YYYYMMDD-HHMM-<gönderen>-to-<alıcı>-<TÜR>-<konu>.md`
- Türler: `DECISION` (kapsam/karar), `INFO`, `ACIL` (acil — `## ACİL` başlığıyla;
  `ao lock`, `ao verify`, `ao commit-ok` üzerinden ulaşır ve onaylanmadan commit
  engellenir), `ANOMALY` (watchdog olgusu), `DEVIR` (devir notu).
- Uygulayıcı her tur başında `ao_inbox` çeker, uygulayıp/reddedip `ao_ack` ile siler.
- Uygulayıcı takılınca `ao_report {{kind:"blocked"}}` ya da `ao_ask` — düz metinle
  park etmez.
"""

STEERING_COORD = """---
inclusion: always
---

# Coordination: the `ao` tools

Every turn starts with `ao_inbox`; apply or explicitly reject each message, then
`ao_ack`. Report changes as they happen: `ao_report {{kind: "blocked"|"status"|"done"}}`.
When you need a decision, ask with options — `ao_ask` — and move to the next queued
item; do not park on prose. Check `ao_decisions` next turn.

Urgent messages (`## ACİL`) reach you through `ao lock`, `ao verify` and every `ao_*`
response, and `ao commit-ok` refuses until you acknowledge them.

Review with `ao review`, never yourself. `ao commit-ok` refuses a review you wrote.
Heavy commands go through the machine lock: `ao lock -- <cmd>`. `ao verify` takes it
itself. Authority lives in `.ao/authority.md`, not in mail. `push` is never yours.
"""


def _detect_gates(root):
    """Guess the project's gates from what is already there. A wrong guess costs a
    failed verify, which is visible; no guess costs an empty gate file nobody
    notices, which is not."""
    gates, quick = {}, []
    pj = os.path.join(root, "package.json")
    if os.path.exists(pj):
        try:
            scripts = (json.load(open(pj)).get("scripts") or {})
        except Exception:
            scripts = {}
        for name, key in (("typecheck", "typecheck"), ("lint", "lint"), ("test", "test")):
            if key in scripts:
                gates[name] = {"run": f"npm run {key}", "expect": "exit_zero",
                               "timeout": 600 if name != "test" else 2400}
                if name != "test":
                    quick.append(name)
        if "test" in gates:
            gates["test"]["serialise"] = True
    elif any(os.path.exists(os.path.join(root, f)) for f in ("pyproject.toml", "setup.py")):
        gates["test"] = {"run": "python -m pytest -q", "expect": "exit_zero",
                         "timeout": 2400, "serialise": True}
        if shutil.which("ruff"):
            gates["lint"] = {"run": "ruff check .", "expect": "exit_zero", "timeout": 300}
            quick.append("lint")
    gates["diff-check"] = {"run": "git diff --check", "expect": "exit_zero", "timeout": 60}
    quick.append("diff-check")
    return {"gates": gates,
            "profiles": {"quick": quick, "full": list(gates)},
            "default_profile": "quick"}


def cmd_init(cfg, args):
    """Put `ao` on a project. Idempotent: existing files are left alone.

    This is the answer to "how do I apply this to another project". Everything
    it writes is a file the agent already knows how to read, so a project without
    MCP or a watchdog gets the whole protocol from the files alone; the optional
    flags add the automation on top.
    """
    root = cfg["root"]
    name = args.name or os.path.basename(root)
    wrote, kept = [], []

    def put(rel, content, mode=None):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            kept.append(rel)
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(content)
        if mode:
            os.chmod(p, mode)
        wrote.append(rel)

    put(".ao/config.json", json.dumps({"project": name, "round_budget": 5}, indent=2) + "\n")
    put(".ao/board.md", BOARD_TEMPLATE)
    put(".ao/backlog.md", BACKLOG_TEMPLATE.format(name=name))
    put(".ao/authority.md", AUTHORITY_TEMPLATE)
    put(".ao/gates.json", json.dumps(_detect_gates(root), indent=2) + "\n")
    put(".ao/ledger/.gitkeep", "")
    put(".ao/decisions/.gitkeep", "")
    put("semantic-review/.gitkeep", "")
    put("agent-mail/README.md", MAIL_README.format())

    gi = os.path.join(root, ".gitignore")
    lines = open(gi).read().split("\n") if os.path.exists(gi) else []
    add = [l for l in ("agent-mail/*.md", "!agent-mail/README.md", ".ao/inbox/", ".ao/hold")
           if l not in lines]
    if add:
        with open(gi, "a") as fh:
            fh.write("\n# agent-orchestrator: mail is transient; the ledger and reviews are not\n"
                     + "\n".join(add) + "\n")
        wrote.append(".gitignore (+%d)" % len(add))

    # Steering for whichever agent this repo uses. Kiro reads .kiro/steering;
    # Claude Code and most others read CLAUDE.md / AGENTS.md.
    if os.path.isdir(os.path.join(root, ".kiro")) or args.agent == "kiro":
        put(".kiro/steering/ao-coordination.md", STEERING_COORD.format())
    else:
        for f in ("CLAUDE.md", "AGENTS.md"):
            p = os.path.join(root, f)
            if os.path.exists(p) and "ao_inbox" not in open(p, errors="replace").read():
                with open(p, "a") as fh:
                    fh.write("\n\n" + STEERING_COORD.split("---\n\n", 2)[-1])
                wrote.append(f + " (+ao section)")
                break
        else:
            put("AGENTS.md", STEERING_COORD.split("---\n\n", 2)[-1])

    for rel in wrote:
        print(f"  {C['green']}wrote{C['reset']}  {rel}")
    for rel in kept:
        print(f"  {C['dim']}kept   {rel}{C['reset']}")

    exe = shutil.which("ao") or os.path.abspath(sys.argv[0])
    if args.mcp and shutil.which("kiro-cli"):
        A.sh(f'kiro-cli mcp add --name ao --scope workspace --command "{exe}" '
             f'--args \'["-C","{root}","mcp","serve"]\'', cwd=root)
        print(f"  {C['green']}mcp{C['reset']}    registered `ao` with kiro-cli (workspace)")
    if args.watchdog:
        code = subprocess.run([exe, "-C", root, "watchdog", "install"],
                              capture_output=True, text=True).returncode
        print(f"  {C['green'] if code == 0 else C['red']}watchdog{C['reset']} "
              f"{'installed' if code == 0 else 'install failed — run ao watchdog install'}")

    print(f"\n{C['b']}Next:{C['reset']} write the first slice into .ao/backlog.md with an "
          f"acceptance boundary, then {C['b']}ao doctor{C['reset']}.")
    if not (args.mcp and args.watchdog):
        print(f"{C['dim']}Automation is opt-in: ao init --mcp --watchdog{C['reset']}")
    return 0


def cmd_decide(cfg, args):
    """Record an architect decision where it survives.

    `ao note` is a message: read, acted on, deleted. A decision is a fact about
    the project that the next architect — or the same one after a compaction —
    has to be able to find. It goes to the ledger, and to the implementer's
    mailbox so it is acted on; if it answers an open `ao ask`, that is closed too.
    """
    root = cfg["root"]
    if args.list:
        p = os.path.join(root, ".ao", "ledger", "decisions.jsonl")
        rows = []
        if os.path.exists(p):
            for line in open(p, errors="replace"):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        if not rows:
            print(f"{C['dim']}No decisions recorded.{C['reset']}")
            return 0
        for r in rows[-args.n:]:
            when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
            print(f"  {C['dim']}{when}{C['reset']}  {C['b']}{r['id']}{C['reset']}  {r['decision']}")
            if r.get("why"):
                print(f"           {C['dim']}{r['why'][:110]}{C['reset']}")
        return 0
    if not args.decision:
        print(f"usage: {C['b']}ao decide \"decision\" --why \"…\" [--answers D-123] "
              f"[--scope B2] [--urgent]{C['reset']}")
        return 1
    rec = {"id": f"AD-{int(time.time())}", "at": int(time.time()), "decision": args.decision,
           "why": args.why, "scope": args.scope, "answers": args.answers, "by": "architect"}
    d = os.path.join(root, ".ao", "ledger")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "decisions.jsonl"), "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if args.answers:
        ans = A.answer(root, args.answers, "x " + args.decision if False else args.decision,
                       by="architect")
        print(f"  {C['green']}answered{C['reset']} {args.answers}" if ans else
              f"  {C['yellow']}no open question {args.answers}{C['reset']}")
    body = f"{args.decision}\n\n**Neden:** {args.why or '—'}"
    if args.scope:
        body += f"\n\n**Kapsam:** {args.scope}"
    body += f"\n\n_karar kaydı: {rec['id']}_"
    name = A.note(root, cfg, args.to, args.decision[:60], body, urgent=args.urgent)
    print(f"  {C['green']}recorded{C['reset']} {rec['id']}  →  {cfg['mailbox']}/{name}")
    return 0


def _since_marker(root):
    return os.path.join(root, ".ao", "ledger", "since.json")


def cmd_since(cfg, args):
    """What changed since I last looked — for an architect coming back.

    `ao digest` is a window; this is a delta, anchored on the moment you last
    asked. A resumed session after a compaction, or a human back from lunch, has
    one question — what happened while I was gone — and reconstructing it from
    the whole day's digest is the tool making them do its job.
    """
    root = cfg["root"]
    ref = args.ref or "last"
    now = time.time()
    if ref == "last":
        try:
            cut = json.load(open(_since_marker(root)))["at"]
        except Exception:
            cut = now - 86400
    elif A.re.fullmatch(r"\d+(\.\d+)?[hdm]", ref):
        n, unit = float(ref[:-1]), ref[-1]
        cut = now - n * {"m": 60, "h": 3600, "d": 86400}[unit]
    else:
        ts = A.sh(f"git log -1 --format=%ct {ref}", cwd=root)
        if not ts.isdigit():
            print(f"{C['red']}not a duration (2h, 1d), a git ref, or 'last': {ref}{C['reset']}")
            return 1
        cut = float(ts)
    mins = int((now - cut) / 60)
    print(f"{C['b']}since{C['reset']} {C['dim']}{mins // 60}h {mins % 60}m ago{C['reset']}")

    log = A.sh(f"git log --since=@{int(cut)} --pretty='%h|%s'", cwd=root) or ""
    commits = [l.split("|", 1) for l in log.split("\n") if "|" in l]
    print(f"\n  {C['b']}{len(commits)}{C['reset']} commit")
    for sha, subj in commits[:8]:
        print(f"    {C['dim']}{sha}{C['reset']} {subj[:72]}")

    def newer(rel):
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d)
                      if not f.startswith(".") and os.path.getmtime(os.path.join(d, f)) >= cut)
    revs = newer(cfg["reviews"])
    if revs:
        verdicts = dict(A.reviews(root, cfg["reviews"], limit=40))
        print(f"\n  {C['b']}{len(revs)}{C['reset']} review")
        for f in revs[-6:]:
            v = verdicts.get(f, "")
            col = C["green"] if "APPROVED" in v.upper() else C["yellow"]
            print(f"    {col}{v or '?':<14}{C['reset']} {f[:60]}")
    decs = [d for d in A.decisions(root) if d.get("asked_at", 0) >= cut or (d.get("answered_at") or 0) >= cut]
    if decs:
        print(f"\n  {C['b']}{len(decs)}{C['reset']} decision")
        for d in decs[-6:]:
            st = f"{C['green']}→ {d['answer']}{C['reset']}" if d["state"] == "answered" else f"{C['yellow']}open{C['reset']}"
            print(f"    {d['id']}  {d['question'][:56]}  {st}")
    mail = [m for m in A.mailbox(root, cfg["mailbox"])
            if os.path.getmtime(os.path.join(root, cfg["mailbox"], m)) >= cut]
    if mail:
        print(f"\n  {C['b']}{len(mail)}{C['reset']} message in the mailbox now")
        for m in mail[:6]:
            print(f"    {C['dim']}{m[:70]}{C['reset']}")
    notices = [n for n in A.notices(root, 50, include_suppressed=False) if n.get("at", 0) >= cut]
    if notices:
        print(f"\n  {C['b']}{len(notices)}{C['reset']} alert sent")
        for n in notices[:4]:
            print(f"    {C['dim']}{n['title']}: {n['msg'][:60]}{C['reset']}")
    b = A.board(root)
    print(f"\n  {C['dim']}board now: " + " · ".join(f"{k} {len(v)}" for k, v in b.items() if v) + C["reset"])

    if not args.no_mark:
        os.makedirs(os.path.dirname(_since_marker(root)), exist_ok=True)
        json.dump({"at": now}, open(_since_marker(root), "w"))
    return 0


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
    # Eligible work first: queued items whose `needs:` are all done. This is the
    # dependency graph answering "what is next" without the implementer choosing
    # its own scope.
    rd = A.ready(root)
    if rd:
        print(f"\n{C['b']}{C['green']}READY{C['reset']} {C['dim']}({len(rd)}) — "
              f"dependencies satisfied{C['reset']}")
        for it in rd:
            role = f"  {C['cyan']}role:{it['role']}{C['reset']}" if it.get("role") else ""
            print(f"   {C['b']}{it['id']}{C['reset']}  {it['title']}{role}")
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

    # hold — stops unattended turns only. An interactive session has a person in
    # it who did not ask to be stopped; the lock still keeps the watchdog from
    # starting anything new.
    pids = A.agent_pids(root, adapter, headless_only=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"by": args.by, "reason": args.reason or "manual intervention",
               "at": int(time.time()), "stopped": pids},
              open(path, "w"), indent=2)
    if not pids:
        dead = A.orphans(root, adapter)
        if dead:
            print(f"clearing {len(dead)} orphaned process(es) left by ended turns: {dead}")
            A.sweep_orphans(dead)
        print(f"{C['yellow']}hold set{C['reset']} — no agent turn was running")
        return 0
    dead = A.orphans(root, adapter)
    if dead:
        print(f"clearing {len(dead)} orphaned process(es) left by ended turns: {dead}")
        A.sweep_orphans(dead)
    print(f"stopping {len(pids)} process(es): {pids}")
    for pid in pids:
        A.kill_turn(pid, signal.SIGTERM)     # whole group; let it finish the write it is in
    deadline = time.time() + args.grace
    while time.time() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            break
        time.sleep(0.5)
    alive = [p for p in pids if _alive(p)]
    for pid in alive:
        A.kill_turn(pid, signal.SIGKILL)
    print(f"{C['red']}HELD{C['reset']} — {len(pids) - len(alive)} exited on request, "
          f"{len(alive)} killed. The watchdog will not restart while .ao/hold exists.")
    return 0


def cmd_writers(cfg, args):
    """Who is writing in this tree — turns, not processes, with orphans set aside.

    This is the measurement a single-writer rule should run. The process table
    answers a different question: one turn is a wrapper, a runtime and an engine,
    and a turn that ended can leave the last two behind at 0% CPU with the repo
    as their cwd. Counting processes reported four writers where there were
    none, and an implementer that trusted the count refused to write for three
    and a half hours. Exit status is 0 for at most one live turn, 1 otherwise;
    `--clean` stops orphans only — never a live turn, never a person's session.
    """
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    roots, dead = A.writers(root, adapter)
    table = A._proc_table()
    rows = []
    for pid in roots:
        args_ = (A.sh(f"ps -o etime=,args= -p {pid}") or "").strip()
        et, _, cmd = args_.partition(" ")
        rows.append({"pid": pid, "elapsed": et, "headless": A._is_headless(pid),
                     "tty": table.get(pid, (0, 0, "?"))[2], "cmd": cmd.strip()[:90]})
    if args.clean and dead:
        A.sweep_orphans(dead)
        left = [p for p in dead if A._pid_alive(p)]
        cleaned = [p for p in dead if p not in left]
    else:
        cleaned, left = [], dead
    if args.json:
        print(json.dumps({"writers": len(roots), "turns": rows, "orphans": left,
                          "cleaned": cleaned}, ensure_ascii=False))
        return 0 if len(roots) <= 1 else 1
    if not roots:
        print(f"{C['green']}0 writers{C['reset']} — no live turn in this tree")
    else:
        colour = C['green'] if len(roots) == 1 else C['red']
        print(f"{colour}{len(roots)} writer(s){C['reset']}")
        for r in rows:
            kind = "headless" if r["headless"] else f"interactive tty={r['tty']}"
            print(f"   {r['pid']:>6}  {r['elapsed']:>10}  {kind:<22} {r['cmd']}")
    if cleaned:
        print(f"   cleaned {len(cleaned)} orphan(s): {cleaned}")
    if left:
        print(f"   {C['yellow']}{len(left)} orphan(s){C['reset']} left by ended turns, not counted: {left}"
              + ("" if args.clean else "  (`ao writers --clean` stops them)"))
    return 0 if len(roots) <= 1 else 1


def cmd_fanout(cfg, args):
    """May a fan-out of N sub-agents start now — and what did the last one cost.

    `ok` answers from three facts (hard cap, recent limit hit, provider window);
    `record` writes what a run cost so the next estimate is empirical; `history`
    lists the runs. Exit 1 on any refusal, so a coordinator can gate on it.
    """
    root = cfg["root"]
    if args.action == "record":
        if args.agents is None:
            print("--agents is required"); return 2
        rec = A.record_fanout(root, args.agents, args.done, args.errors, args.tokens, args.note)
        print(f"{C['green']}recorded{C['reset']} {json.dumps(rec, ensure_ascii=False)}")
        return 0
    if args.action == "history":
        rows = A.fanout_history(root, args.limit)
        if not rows:
            print("no fan-outs recorded"); return 0
        for r in rows:
            when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
            flag = f"  {C['red']}limit hit{C['reset']}" if r.get("limit_hit") else ""
            print(f"  {when}  {r['agents']:>3} agents  done {r.get('done','?'):>3}  "
                  f"errors {r.get('errors','?'):>3}  tokens {r.get('tokens','?')}{flag}"
                  + (f"  — {r['note']}" if r.get('note') else ""))
        return 0
    if args.agents is None:
        print("--agents is required"); return 2
    v = A.fanout_verdict(root, cfg, args.agents, args.per_agent_tokens, args.provider)
    if args.json:
        print(json.dumps(v, ensure_ascii=False))
        return 0 if v["ok"] else 1
    colour = C['green'] if v["ok"] else C['red']
    print(f"{colour}{v['verdict'].upper()}{C['reset']}  {v['agents']} agents × ~{v['per_agent_tokens']:,} "
          f"tokens ({v['per_agent_source']}) ≈ {v['estimated_tokens']:,} tokens")
    w = v.get("window")
    if w:
        print(f"   {args.provider} window: {w['pct']}% used, {w['window']} window, resets in {w['resets_in']}"
              + (f"; {v['spent_this_window']:,} tokens fanned out in it so far" if v['spent_this_window'] else ""))
    for r in v["reasons"]:
        print(f"   {C['yellow'] if not v['ok'] else C['dim']}· {r}{C['reset']}")
    if v["ok"]:
        print(f"   {C['dim']}after the run: ao fanout record --agents {v['agents']} --done D --errors E --tokens T{C['reset']}")
    return 0 if v["ok"] else 1


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
        label=label, python_arg=(f"<string>{python}</string>" if python else ""),
        script=script, root=root,
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
            key = os.path.basename(root.rstrip("/")) or "root"
            we = wake_error(os.path.join(STATE_DIR, f"escalate-{key}.log"))
            if we:
                text, used, when = we
                print(f"last wake       {C['red']}failed{C['reset']} {when} [{used or '?'}]: {text[:90]}")
                if used and used != f"{rb} {rv}":
                    print(f"                {C['dim']}a different binary resolves now; the next wake will use it{C['reset']}")
                else:
                    print(f"                {C['yellow']}same binary — update it (claude update) or remove the stale copy{C['reset']}")
        print(f"ao for agents   {C['green']}{reachable}{C['reset']}")
    else:
        print(f"ao for agents   {C['red']}not on a spawned agent's PATH{C['reset']}")
        print(f"                {C['dim']}a shell alias does not count — "
              f"uv tool install ao-orchestrator{C['reset']}")

    steer = os.path.join(root, ".kiro", "steering")
    if os.path.isdir(steer) and not reachable:
        refs = [f for f in os.listdir(steer)
                if f.endswith(".md") and "ao " in open(os.path.join(steer, f),
                                                       errors="replace").read()]
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
    ak = sub.add_parser("ask", help="pose a decision, answerable in one tap")
    ak.add_argument("question", nargs="?")
    ak.add_argument("options", nargs="*")
    ak.add_argument("--context")
    ak.add_argument("--slice")
    ak.set_defaults(fn=cmd_ask)
    an = sub.add_parser("answer", help="answer a pending decision")
    an.add_argument("id")
    an.add_argument("value", nargs="+")
    an.set_defaults(fn=cmd_answer)
    dc = sub.add_parser("decisions", help="open and answered questions")
    dc.add_argument("-n", type=int, default=10)
    dc.set_defaults(fn=cmd_decisions)
    dg = sub.add_parser("digest", help="what happened, read from the ledgers")
    dg.add_argument("--days", type=float, default=1.0)
    dg.add_argument("-n", type=int, default=6)
    dg.set_defaults(fn=cmd_digest)
    ini = sub.add_parser("init", help="put ao on this project (idempotent)")
    ini.add_argument("--name")
    ini.add_argument("--agent", choices=["kiro", "claude", "auto"], default="auto")
    ini.add_argument("--mcp", action="store_true", help="also register the MCP server")
    ini.add_argument("--watchdog", action="store_true", help="also install the watchdog")
    ini.set_defaults(fn=cmd_init)
    de = sub.add_parser("decide", help="record an architect decision durably")
    de.add_argument("decision", nargs="?")
    de.add_argument("--why")
    de.add_argument("--answers", help="open decision id this settles, e.g. D-123")
    de.add_argument("--scope")
    de.add_argument("--to", default="kiro")
    de.add_argument("--urgent", action="store_true")
    de.add_argument("--list", action="store_true")
    de.add_argument("-n", type=int, default=10)
    de.set_defaults(fn=cmd_decide)
    si = sub.add_parser("since", help="what changed since you last looked")
    si.add_argument("ref", nargs="?", help="last | 2h | 1d | <git ref>")
    si.add_argument("--no-mark", action="store_true")
    si.set_defaults(fn=cmd_since)
    nt = sub.add_parser("note", help="write an architect message into the mailbox")
    nt.add_argument("title")
    nt.add_argument("--body")
    nt.add_argument("--to", default="kiro")
    nt.add_argument("--urgent", action="store_true")
    nt.add_argument("--stdin", action="store_true", help="read the body from stdin")
    nt.set_defaults(fn=cmd_note)
    hf = sub.add_parser("handoff", help="write and send everything a successor needs")
    hf.add_argument("--reason")
    hf.add_argument("--no-send", action="store_true")
    hf.set_defaults(fn=cmd_handoff)
    rw = sub.add_parser("review", help="review the tree with an actor that did not write it")
    rw.add_argument("--boundary", help="acceptance boundary; defaults to the running slice")
    rw.add_argument("--timeout", type=int, default=900)
    rw.set_defaults(fn=cmd_review)
    tg = sub.add_parser("telegram", help="phone channel: alerts out, decisions in")
    tg.add_argument("action", nargs="?", default="status",
                    choices=["status", "setup", "test", "poll", "install", "uninstall"])
    tg.add_argument("--once", action="store_true", help="poll: one pass then exit")
    tg.set_defaults(fn=cmd_telegram)
    cr = sub.add_parser("credits", help="credit spend measured from local transcripts")
    cr.add_argument("--offline", action="store_true", help="skip the account lookup")
    cr.add_argument("--local", action="store_true", help="also show this machine's share")
    cr.add_argument("--reset-day", type=int, help="fallback: override the renewal day")
    cr.set_defaults(fn=cmd_credits)
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
    am = sub.add_parser("a2a-mcp", help="reach A2A agents from an MCP client (stdio)")
    am.add_argument("action", choices=["serve", "config"], nargs="?", default="config")
    am.set_defaults(fn=cmd_a2a_mcp)

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
    fo = sub.add_parser("fanout", help="may a fan-out of N sub-agents start now; record what one cost")
    fo.add_argument("action", choices=["ok", "record", "history"], nargs="?", default="ok")
    fo.add_argument("--agents", type=int)
    fo.add_argument("--per-agent-tokens", type=int, dest="per_agent_tokens")
    fo.add_argument("--provider", default="claude")
    fo.add_argument("--done", type=int)
    fo.add_argument("--errors", type=int)
    fo.add_argument("--tokens", type=int)
    fo.add_argument("--note")
    fo.add_argument("--limit", type=int, default=20)
    fo.add_argument("--json", action="store_true")
    fo.set_defaults(fn=cmd_fanout)
    w = sub.add_parser("writers", help="live turns in this tree (not processes); orphans set aside")
    w.add_argument("--clean", action="store_true", help="stop orphaned processes left by ended turns")
    w.add_argument("--json", action="store_true")
    w.set_defaults(fn=cmd_writers)
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
