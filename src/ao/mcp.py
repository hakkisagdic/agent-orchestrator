#!/usr/bin/env python3
"""ao-mcp — expose this project's orchestration state over MCP, read-mostly.

Any MCP-capable client (Claude Code, Cursor, Zed, an IDE extension) can then ask
what the implementer is doing and what has been measured, without shelling out
and without learning this tool's output format.

One rule shapes the surface: **authority never goes on it.** `commit-ok` decides
whether work may land, and exposing it here would let the implementer grant
itself the authority the separation exists to withhold. Reading is free; the one
expensive action, `verify`, is opt-in because it runs the project's real gates.

JSON-RPC 2.0 over stdio, standard library only.
"""
import json
import os
import sys
import time

from . import lib as A

PROTOCOL = "2025-06-18"

TOOLS = [
    {"name": "ao_status",
     "description": "Implementer state, context/cost telemetry, git state and open reviews.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_board",
     "description": "Where each pre-authorised work item is: running, blocked (with what it "
                    "waits on), queued, inbox, verified, done. Blocked items are the ones a "
                    "human must act on.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_notices",
     "description": "Alerts this project raised, including ones the rate limit suppressed.",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer", "default": 10},
                                    "include_suppressed": {"type": "boolean", "default": False}}}},
    {"name": "ao_fleet",
     "description": "One row per project with a local agent session, ordered by what needs a "
                    "human first.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_inbox",
     "description": "Coordination messages addressed to you that you have not yet "
                    "acknowledged. Check this at the start of every turn. Each message "
                    "carries an id; acknowledge it with ao_ack once you have applied or "
                    "explicitly rejected it.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_ack",
     "description": "Acknowledge one coordination message: it is removed, which is how "
                    "delivery is confirmed. Acknowledge only after applying or explicitly "
                    "rejecting the message — never on a partial read.",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "string"},
                                    "outcome": {"type": "string",
                                                "description": "applied | rejected: why"}}}},
    {"name": "ao_report",
     "description": "Tell the architect something, at any point in a turn. Use kind "
                    "'blocked' when you cannot proceed without a decision — that escalates "
                    "immediately rather than waiting for a detector to infer it. Use "
                    "'status' or 'done' for everything else.",
     "inputSchema": {"type": "object", "required": ["kind", "summary"],
                     "properties": {
                         "kind": {"type": "string", "enum": ["blocked", "status", "done"]},
                         "summary": {"type": "string"},
                         "detail": {"type": "string"},
                         "needs": {"type": "string",
                                   "description": "for kind=blocked: what input would unblock it"}}}},
    {"name": "ao_ask",
     "description": "Ask the architect or the human a question you cannot answer "
                    "yourself, with options. Prefer this over parking on a prose "
                    "blocker: an options question is answerable from a phone in one "
                    "tap, a paragraph is not. Free text is always available as a last "
                    "option, so list what you think is likely, not everything.",
     "inputSchema": {"type": "object", "required": ["question", "options"],
                     "properties": {
                         "question": {"type": "string"},
                         "options": {"type": "array", "items": {"type": "string"},
                                     "description": "2-8 concrete choices"},
                         "context": {"type": "string",
                                     "description": "what makes this a real question"},
                         "slice": {"type": "string"}}}},
    {"name": "ao_decisions",
     "description": "Questions you asked and whether they have been answered. Check "
                    "this at the start of a turn: an answered question is the thing "
                    "that unparks a slice.",
     "inputSchema": {"type": "object",
                     "properties": {"state": {"type": "string",
                                              "enum": ["open", "answered"]}}}},
    {"name": "ao_fanout",
     "description": "Before fanning out to sub-agents: may a fan-out of this size "
                    "start now (hard cap, recent limit hit, provider window)? "
                    "After: record what it cost so the next estimate is real. "
                    "47 agents once died at 36 with a session limit; this is the "
                    "gate that would have refused it.",
     "inputSchema": {"type": "object", "required": ["agents"],
                     "properties": {
                         "action": {"type": "string", "enum": ["ok", "record"]},
                         "agents": {"type": "integer"},
                         "per_agent_tokens": {"type": "integer"},
                         "done": {"type": "integer"}, "errors": {"type": "integer"},
                         "tokens": {"type": "integer"}, "note": {"type": "string"}}}},
    {"name": "ao_verify",
     "description": "Run the project's declared gates and record the measured result. "
                    "Expensive; disabled unless the server was started with --allow-verify.",
     "inputSchema": {"type": "object",
                     "properties": {"profile": {"type": "string"}}}},
]


def status_payload(cfg):
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    state, age, desc = A.busy(cfg, adapter) if impl else ("unknown", None, "")
    msgs, _ = A.session_paths(cfg)
    recs = A.read_tail(msgs, 4_000_000) if msgs else []
    tel = A.telemetry(recs, adapter) if recs else {}
    g = A.git_state(root)
    revs = A.reviews(root, cfg["reviews"], limit=3)
    return {"project": cfg.get("project") or os.path.basename(root), "root": root,
            "adapter": impl.get("adapter"), "state": state, "seconds_since_write": age,
            "doing": desc, "spinning_minutes": A.spinning(root),
            "context_percent": tel.get("ctx"), "turns": tel.get("turns"),
            "cost_total": tel.get("total"), "cost_unit": tel.get("unit"),
            "head": g["log"][0] if g["log"] else None,
            "dirty_files": len(g["dirty"]), "unpushed": g["ahead"],
            "mailbox": A.mailbox(root, cfg["mailbox"]),
            "reviews": [{"file": f, "verdict": v} for f, v in revs],
            "held": A.hold_state(root),
            "agent_processes": len(A.agent_pids(root, adapter)) if impl else 0}


def board_payload(root):
    b = A.board(root)
    return {"states": {k: v for k, v in b.items() if v},
            "a2a": {k: A.A2A_STATE.get(k) for k in b if b[k]},
            "counts": {k: len(v) for k, v in b.items()}}


def call(name, args, cfg, allow_verify):
    root = cfg["root"]
    if name == "ao_status":
        return status_payload(cfg)
    if name == "ao_board":
        return board_payload(root)
    if name == "ao_notices":
        return {"notices": A.notices(root, args.get("limit", 10),
                                     args.get("include_suppressed", False))}
    if name == "ao_fleet":
        return {"projects": [{"name": os.path.basename(w["path"]), "root": w["path"]}
                             for w in A.all_workspaces()]}
    if name == "ao_inbox":
        # The same files the file-based protocol uses. One source of truth: an MCP
        # store beside the mailbox would be a second place for the same fact, and
        # every failure in this project's history has come from two records of one
        # thing drifting apart.
        box = cfg.get("mailbox", "agent-mail")
        out = []
        for m in A.mailbox(root, box):
            if "-to-fable-" in m or "-to-architect-" in m:
                continue                     # our own outbound; not addressed to us
            try:
                body = open(os.path.join(root, box, m), errors="replace").read(20000)
            except OSError:
                continue
            out.append({"id": m, "body": body})
        return {"messages": out, "count": len(out),
                "note": "acknowledge each with ao_ack after applying or rejecting it"}

    if name == "ao_ack":
        box = cfg.get("mailbox", "agent-mail")
        mid = os.path.basename(args.get("id", ""))
        path = os.path.join(root, box, mid)
        if not mid or mid == "README.md" or not os.path.exists(path):
            return {"error": f"no such message: {args.get('id')}"}
        os.remove(path)
        A.record_notice(root, "ack", f"{mid}: {args.get('outcome', 'applied')}",
                        sent=False, key="ack")
        return {"acknowledged": mid, "outcome": args.get("outcome", "applied")}

    if name == "ao_report":
        box = cfg.get("mailbox", "agent-mail")
        os.makedirs(os.path.join(root, box), exist_ok=True)
        kind = args.get("kind", "status")
        # `blocked` writes the marker the watchdog escalates on within one cycle.
        # The agent saying so directly beats a detector inferring it twenty
        # minutes later, which is what used to happen.
        header = "## KARAR GEREKLİ" if kind == "blocked" else f"## {kind.upper()}"
        slug = "".join(c if c.isalnum() else "-" for c in args["summary"].lower())[:40]
        name_ = f"{time.strftime('%Y%m%d-%H%M')}-kiro-to-fable-{kind.upper()}-{slug}.md"
        with open(os.path.join(root, box, name_), "w") as fh:
            fh.write(f"# {args['summary']}\n\n{header}\n\n")
            if args.get("detail"):
                fh.write(args["detail"] + "\n\n")
            if args.get("needs"):
                fh.write(f"**Needs:** {args['needs']}\n")
        delivered = 0
        if kind == "blocked":
            try:
                from . import telegram
                delivered = telegram.send(
                    f"⛔ *{args['summary']}*\n\n{args.get('needs') or args.get('detail') or ''}"
                    f"\n\n_uygulayıcı takıldı; cevap yazarsan acil karar olarak düşer_",
                    root)
            except Exception:
                pass
        return {"written": name_, "escalates": kind == "blocked",
                "delivered_to_phone": delivered,
                "note": ("the architect is woken on the next watchdog cycle"
                         if kind == "blocked" else "queued for the architect")}

    if name == "ao_fanout":
        if args.get("action") == "record":
            return A.record_fanout(root, args["agents"], args.get("done"), args.get("errors"),
                                   args.get("tokens"), args.get("note"))
        return A.fanout_verdict(root, cfg, int(args["agents"]), args.get("per_agent_tokens"))
    if name == "ao_ask":
        rec = A.ask(root, args["question"], args.get("options") or [],
                    context=args.get("context"), slice_id=args.get("slice"))
        # Delivery is decided here, not by the caller. An implementer with its own
        # channel to a phone is a spam surface; an implementer that reports and
        # lets the centre route is not.
        delivered = 0
        try:
            from . import telegram
            from .cli import _decision_text
            kb = [[{"text": f"{o['key']}) {o['label'][:40]}",
                    "callback_data": f"{rec['id']}:{o['key']}"}]
                  for o in rec["options"] if not o.get("free_text")]
            delivered = telegram.send(_decision_text(rec), root, keyboard=kb)
        except Exception:
            pass
        return {"id": rec["id"], "options": rec["options"],
                "delivered_to_phone": delivered,
                "note": "park the slice and take the next queued item; check "
                        "ao_decisions next turn"}

    if name == "ao_decisions":
        return {"decisions": A.decisions(root, args.get("state"))}

    if name == "ao_verify":
        if not allow_verify:
            return {"error": "verify is disabled; start the server with --allow-verify"}
        import subprocess
        r = subprocess.run([sys.executable, "-m", "ao", "-C", root, "verify"]
                           + (["-p", args["profile"]] if args.get("profile") else []),
                           capture_output=True, text=True, timeout=3600)
        return {"exit": r.returncode, "output": (r.stdout or r.stderr)[-4000:],
                "record": A.latest_verification(root)}
    return {"error": f"unknown tool {name}"}


def main():
    root = None
    allow_verify = "--allow-verify" in sys.argv
    if "-C" in sys.argv:
        root = sys.argv[sys.argv.index("-C") + 1]
    cfg = A.load_config(A.find_root(root))

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid, method = req.get("id"), req.get("method")
        try:
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL,
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "agent-orchestrator", "version": "0.1.0"}}
            elif method in ("notifications/initialized", "initialized"):
                continue                                  # notification: no reply
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = req.get("params") or {}
                payload = call(p.get("name"), p.get("arguments") or {}, cfg, allow_verify)
                # Ride along on whatever the agent called. MCP has no way to push,
                # so the next best thing is to attach the message to the next
                # response it asks for — which costs nothing and arrives sooner
                # than the agent's own next inbox check.
                if isinstance(payload, dict) and p.get("name") != "ao_inbox":
                    urgent = A.urgent_messages(cfg["root"], cfg)
                    if urgent:
                        payload["URGENT_UNACKNOWLEDGED"] = [
                            {"id": u["id"], "title": u["title"]} for u in urgent]
                        payload["URGENT_NOTE"] = (
                            "Read these with ao_inbox and acknowledge them before "
                            "continuing. ao commit-ok will refuse while any remain.")
                result = {"content": [{"type": "text",
                                       "text": json.dumps(payload, ensure_ascii=False,
                                                          indent=2, default=str)}]}
            elif method == "ping":
                result = {}
            else:
                raise ValueError(f"method not found: {method}")
        except Exception as e:                            # never take the transport down
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "error": {"code": -32603, "message": str(e)}}) + "\n")
            sys.stdout.flush()
            continue
        if rid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result},
                                        ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
