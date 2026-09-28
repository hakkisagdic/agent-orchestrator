#!/usr/bin/env python3
"""ao-mcp — expose this project's orchestration state over MCP, read-mostly.

Any MCP-capable client (Claude Code, Cursor, Zed, an IDE extension) can then ask
what the implementer is doing and what has been measured, without shelling out
and without learning this tool's output format.

One rule shapes the surface: **authority never goes on it.** `commit-ok` decides
whether work may land, and exposing it here would let the implementer grant
itself the authority the separation exists to withhold. Reading is free; the one
expensive action, `verify`, is opt-in because it runs the project's real gates.

A second rule narrows it per session (MCP-ROLES): a server started for a role,
`--role architect|implementer|reviewer`, lists and runs that role's tools alone. A
reviewer reads and sends nothing; an implementer works its mailbox; the architect
keeps every tool. `ao init` writes the role into a registration only one role reads.

JSON-RPC 2.0 over stdio, standard library only.
"""
import json
import os
import sys
import time

from . import __version__, language, lib as A, settings as S
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

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
    {"name": "ao_candidate",
     "description": "The candidate staged now and what a review judges it against: the running "
                    "slice and its boundary, the candidate's digest and paths, and the committed "
                    "source a test-only candidate exercises. Not the diff. Read-only.",
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
                    "that unparks a slice. A rule proposal's question is 'proposed' "
                    "until a person answers it.",
     "inputSchema": {"type": "object",
                     "properties": {"state": {"type": "string",
                                              "enum": ["open", "proposed", "answered"]}}}},
    {"name": "ao_propose",
     "description": "Propose a change to a rule you work under - a step of the playbook, a "
                    "steering or rule file, .ao/authority.md, a gate - instead of editing it. "
                    "It is recorded in the decision ledger with the outcomes of the project's "
                    "recent slices, and a person accepts or rejects it; until one does, keep "
                    "working under the rule as it stands. ao_decisions shows the answer. The "
                    "same change proposed again is not recorded twice.",
     "inputSchema": {"type": "object", "required": ["text", "why"],
                     "properties": {
                         "text": {"type": "string",
                                  "description": "the change: what the rule would say, or what would change"},
                         "why": {"type": "string",
                                 "description": "what went wrong under the rule as it stands"},
                         "rule_file": {"type": "string",
                                       "description": "the file that holds the rule, as a path in the project"}}}},
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
    {"name": "ao_watchdog",
     "description": "Why the watchdog did or did not act. explain runs one dry cycle "
                    "and returns its measurements and verdicts; trace returns the recorded "
                    "cycles. Read-only.",
     "inputSchema": {"type": "object",
                     "properties": {"action": {"type": "string", "enum": ["explain", "trace"]},
                                    "last": {"type": "integer"}}}},
    {"name": "ao_verify",
     "description": "Run the project's declared gates and record the measured result. "
                    "Expensive; disabled unless the server was started with --allow-verify.",
     "inputSchema": {"type": "object",
                     "properties": {"profile": {"type": "string"}}}},
]

EVERY_TOOL = tuple(tool["name"] for tool in TOOLS)

# The tools each role's playbook uses, and the only ones a server started for that role lists or
# runs (MCP-ROLES). The implementer's are the tools the playbook's "Talking" section names, and
# ao_verify, the gate step of its loop, still behind --allow-verify. A reviewer judges a candidate:
# it reads the project's state, the board and the candidate, and sends no mail and no report. The
# architect keeps every tool it had before roles existed.
ROLE_TOOLS = {
    "architect": EVERY_TOOL,
    "implementer": ("ao_status", "ao_board", "ao_notices", "ao_inbox", "ao_ack", "ao_report", "ao_ask",
                    "ao_decisions", "ao_propose", "ao_fanout", "ao_watchdog", "ao_verify"),
    "reviewer": ("ao_status", "ao_board", "ao_candidate"),
}


def role_tools(role):
    """The names of the tools a server started for `role` lists and runs.

    Every tool when `role` is None or one this server does not know: a registration written
    before roles existed, one a newer ao wrote, or one a person mistyped. Refusing then would
    take the tools from a session that may well be the architect's, so the server keeps what it
    served before roles existed, and `role_notice` says so.
    """
    return ROLE_TOOLS.get(role, EVERY_TOOL)


def role_notice(role):
    """The line a server prints on stderr when it cannot hold its session to a role's tools, or None.

    stderr, because stdout is the protocol: an MCP client keeps what its server writes there in
    its own log, where the person who wrote the registration finds it.
    """
    if role in ROLE_TOOLS:
        return None
    known = ", ".join(sorted(ROLE_TOOLS))
    if role is None:
        return (f"ao mcp: started with no --role, so every tool is served; the roles are {known}, "
                "and `ao init` writes one into each registration a single role reads")
    return f"ao mcp: --role {role!r} is none of {known}, so every tool is served"


def status_payload(cfg):
    from . import watchdog as W
    root = cfg["root"]
    # The server loads the config once, and an agent starts it before its own session has written a
    # transcript: a session `auto` found no session for is looked for again (SESSION-IDENTITY).
    if not (A.session_state(cfg, "implementer") or {}).get("session"):
        cfg = dict(A.load_config(root), root=root)
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    state, age, desc = A.busy(cfg, adapter) if impl else ("unknown", None, "")
    msgs, _ = A.session_paths(cfg)
    recs = A.read_tail(msgs, 4_000_000) if msgs else []
    tel = A.telemetry(recs, adapter, msgs) if recs else {}
    g = A.git_state(root)
    revs = A.reviews(root, cfg["reviews"], limit=3)
    return {"project": cfg.get("project") or os.path.basename(root), "root": root,
            "adapter": impl.get("adapter"), "state": state, "seconds_since_write": age,
            "doing": desc, "spinning_minutes": A.spinning(root),
            "nothing_to_do_since": (W.load_state(root).get("idle_answer") or {}).get("since"),
            "context_percent": tel.get("ctx"), "turns": tel.get("turns"),
            "cost_total": tel.get("total"), "cost_delegated": tel.get("delegated"), "cost_unit": tel.get("unit"),
            "head": g["log"][0] if g["log"] else None,
            "dirty_files": len(g["dirty"]), "unpushed": g["ahead"],
            "behind": g.get("behind"), "base": g.get("base"), "merged": g.get("merged"),
            "mailbox": A.mailbox(root, cfg["mailbox"]),
            "reviews": [{"file": f, "verdict": v} for f, v in revs],
            "throughput": A.throughput(root, cfg),
            "held": A.hold_state(root),
            "agent_processes": len(A.agent_pids(root, adapter)) if impl else 0}


def board_payload(root):
    b = A.board(root)
    graph = A.board_graph(root)
    return {"states": {k: v for k, v in b.items() if v},
            "ready": [item["id"] for item in graph["ready"]], "problems": graph["problems"],
            "a2a": {k: A.A2A_STATE.get(k) for k in b if b[k]},
            "counts": {k: len(v) for k, v in b.items()}}


def candidate_payload(cfg):
    """The candidate staged now, and what a review judges it against (MCP-ROLES).

    What `ao review` builds its request from, read the same way: the running slice, its boundary
    (the file its row points at, or its sentence), the candidate's identity and paths, and the
    committed source a test-only candidate exercises (#97), within review.context_bytes. The diff
    is left out: a reviewer is handed the one it judges, and a submitted review judges the tree it
    pinned, which the index may have moved on from - the digest tells the two apart.
    """
    root = cfg["root"]
    running = A.running_slice(root)
    source = A.read_boundary(root, running)
    out = {"slice": (running or {}).get("id"), "title": (running or {}).get("title"),
           "boundary": (source or {}).get("text") or A.slice_boundary(running) or None}
    try:
        candidate = A.index_candidate(root)
    except (RuntimeError, ValueError) as exc:
        return dict(out, candidate=None, context=None, problem=f"the staged candidate cannot be measured: {exc}")
    out["candidate"] = {key: candidate[key] for key in ("digest", "head", "index_tree", "changed_paths")}
    context = A.review_context(root, candidate["changed_paths"], candidate["index_tree"], candidate["head"],
                               S.get(cfg, "review.context_bytes"))
    out["context"] = None if context is None else \
        {key: context[key] for key in ("rev", "paths", "names", "omitted", "text")}
    return out


def call(name, args, cfg, allow_verify, role=None):
    """Run one tool for a server started for `role`, and return what it answers.

    A tool outside the role's set is refused here, not only left off the listing: a client can
    call a name it was never shown, and the refusal is the same whichever way the call arrives.
    """
    root = cfg["root"]
    if name in EVERY_TOOL and name not in role_tools(role):
        return {"error": f"{name} is not one of the {role}'s tools; this server was started with --role {role}"}
    if name == "ao_status":
        return status_payload(cfg)
    if name == "ao_board":
        return board_payload(root)
    if name == "ao_candidate":
        return candidate_payload(cfg)
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
            if A.to_architect(m, cfg) or A.from_watchdog(m):
                continue                     # our own outbound, or the watchdog's; not addressed to us
            try:
                body = open(os.path.join(root, box, m), errors="replace", encoding=UTF8).read(20000)
            except OSError:
                continue
            out.append({"id": m, "body": body})
        A.mail_seen(root, [message["id"] for message in out], "implementer")   # shown, not handled (#30)
        return {"messages": out, "count": len(out),
                "note": "acknowledge each with ao_ack after applying or rejecting it"}

    if name == "ao_ack":
        box = cfg.get("mailbox", "agent-mail")
        mid = os.path.basename(args.get("id", ""))
        path = os.path.join(root, box, mid)
        if not mid or mid == "README.md" or not os.path.exists(path):
            return {"error": f"no such message: {args.get('id')}"}
        if A.mail_store_mode(root) == "append-only":
            A.ingest_mail(root, cfg)
            A.handle_message(root, cfg, mid, "implementer", args.get("outcome", "applied"))   # a record (#80)
        else:
            os.remove(path)
        A.record_notice(root, "ack", f"{mid}: {args.get('outcome', 'applied')}",
                        sent=False, key="ack")
        A.mail_ledger_append(root, {"event": "consumed", "id": mid,
                                    "outcome": args.get("outcome", "applied")})
        return {"acknowledged": mid, "outcome": args.get("outcome", "applied")}

    if name == "ao_report":
        box = cfg.get("mailbox", "agent-mail")
        os.makedirs(os.path.join(root, box), exist_ok=True)
        kind = args.get("kind", "status")
        # `blocked` writes the marker the watchdog escalates on within one cycle.
        # The agent saying so directly beats a detector inferring it twenty
        # minutes later, which is what used to happen. It is the project's
        # language's marker; the watchdog reads every language's (LANGUAGE-FILES).
        header = language.marker(cfg, "decision") if kind == "blocked" else f"## {kind.upper()}"
        slug = A.safe_slug(args["summary"].lower(), "report")
        # The same request twice is one request. An implementer nudged into a
        # turn with nothing to do reports the same blocker again; eighty copies
        # of "queue empty" stood in one mailbox after eleven hours, each a fresh
        # anomaly and a fresh wake. Fold a repeat into the standing report and
        # keep that file's age — the age is the fact the architect needs.
        impl, arch = A.mail_names(cfg)
        marker = f"-{impl}-to-{arch}-{kind.upper()}-"
        dup = None
        for m in A.mailbox(root, box):
            if marker in m and A._report_summary(os.path.join(root, box, m)) == args["summary"].strip():
                dup = m
        if dup:
            n = A.bump_repeat(os.path.join(root, box, dup), cfg)
            return {"written": dup, "repeated": n, "escalates": kind == "blocked",
                    "delivered_to_phone": 0,
                    "note": (f"the same {kind} report is already standing ({n}× now); the architect "
                             f"sees one file with its original time. Do not report it again; end the turn.")}
        name_ = f"{time.strftime('%Y%m%d-%H%M')}-{impl}-to-{arch}-{kind.upper()}-{slug}.md"
        text = f"# {args['summary']}\n\n{header}\n\n"
        # Counts are quoted from the ledger, not typed; a green claim over a red
        # verification is said to be what it is (#6).
        inconsistent = A.report_inconsistency(
            root, " ".join(str(args.get(field) or "") for field in ("summary", "detail", "needs")))
        if inconsistent:
            text += f"## INCONSISTENT\n\n{inconsistent}\n\n"
        if args.get("detail"):
            text += args["detail"] + "\n\n"
        if args.get("needs"):
            text += f"**Needs:** {args['needs']}\n"
        evidence = A.verification_evidence(root)
        if evidence:
            text += f"**Verification:** {evidence}\n"
        A.write_mail(root, cfg, name_, text, {"kind": kind, "from": impl, "to": arch,
                                              "slice": args.get("slice")})
        delivered = 0
        if kind == "blocked":
            try:
                from . import telegram
                delivered = telegram.send(
                    language.text(cfg, "report.blocked-phone", summary=args["summary"],
                                  needs=args.get("needs") or args.get("detail") or ""),
                    root)
            except Exception:
                pass
        return {"written": name_, "escalates": kind == "blocked",
                "delivered_to_phone": delivered, "inconsistent": inconsistent,
                "note": ("the architect is woken on the next watchdog cycle"
                         if kind == "blocked" else "queued for the architect")}

    if name == "ao_watchdog":
        from types import SimpleNamespace
        from . import watchdog as W
        if args.get("action", "explain") == "trace":
            return {"cycles": W.cycles(root, int(args.get("last") or 20))}
        W.run(SimpleNamespace(root=root, idle_minutes=None, dry_run=True))
        return {"facts": dict(W._FACTS), "trace": list(W._TRACE),
                "verdict": W._TRACE[-1] if W._TRACE else ""}
    if name == "ao_fanout":
        if args.get("action") == "record":
            return A.record_fanout(root, args["agents"], args.get("done"), args.get("errors"),
                                   args.get("tokens"), args.get("note"))
        return A.fanout_verdict(root, cfg, int(args["agents"]), args.get("per_agent_tokens"))
    if name == "ao_ask":
        rec = A.ask(root, args["question"], args.get("options") or [],
                    context=args.get("context"), slice_id=args.get("slice"), cfg=cfg)
        # Delivery is decided here, not by the caller. An implementer with its own
        # channel to a phone is a spam surface; an implementer that reports and
        # lets the centre route is not.
        delivered = 0
        try:
            from .cli import _phone_decision
            delivered = _phone_decision(rec, cfg)
        except Exception:
            pass
        return {"id": rec["id"], "options": rec["options"],
                "delivered_to_phone": delivered,
                "note": "park the slice and take the next queued item; check "
                        "ao_decisions next turn"}

    if name == "ao_decisions":
        return {"decisions": A.decisions(root, args.get("state"))}

    if name == "ao_propose":
        # A proposal, never an edit: it records the change and asks a person, and writes no rule file. It is
        # the agent's to make, as a question is; the answer is a person's, and no tool here gives one.
        try:
            proposal, question, created = A.propose(root, cfg, args.get("text"), args.get("why"),
                                                     rule_file=args.get("rule_file"), via="mcp")
        except A.ProposalRefused as exc:
            return {"error": str(exc)}
        if not created:
            return {"id": proposal["id"], "question": proposal["question"], "standing": True,
                    "note": "this change is proposed already and waits for a person; do not propose it again"}
        delivered = 0
        try:
            from .cli import _phone_decision
            delivered = _phone_decision(question, cfg)
        except Exception:
            pass
        return {"id": proposal["id"], "question": proposal["question"], "delivered_to_phone": delivered,
                "note": "a person accepts or rejects it; keep working under the rule as it stands, and read "
                        "the answer with ao_decisions"}

    if name == "ao_verify":
        if not allow_verify:
            return {"error": "verify is disabled; start the server with --allow-verify"}
        import subprocess
        r = subprocess.run([sys.executable, "-m", "ao", "-C", root, "verify"]
                           + (["-p", args["profile"]] if args.get("profile") else []),
                           capture_output=True, text=True, encoding=UTF8, errors="replace", timeout=3600,
                           env=A.self_child_env())
        return {"exit": r.returncode, "output": (r.stdout or r.stderr)[-4000:],
                "record": A.latest_verification(root)}
    return {"error": f"unknown tool {name}"}


def main():
    # MCP's stdio transport is UTF-8. A client writes a request in it whatever the platform's code
    # page, and each reply below is seven-bit JSON, which reads the same in any (#71).
    A.utf8_streams()
    root = None
    allow_verify = "--allow-verify" in sys.argv
    if "-C" in sys.argv:
        root = sys.argv[sys.argv.index("-C") + 1]
    # The role the registration names (MCP-ROLES): fixed for the session, since a harness starts
    # its servers once, when its session starts. Read as AO_ROLE is, without case or surrounding
    # space, so a hand-written `Reviewer` is not an unknown role served every tool.
    role = sys.argv[sys.argv.index("--role") + 1].strip().lower() if "--role" in sys.argv[:-1] else None
    served = role_tools(role)
    notice = role_notice(role)
    if notice:
        print(notice, file=sys.stderr, flush=True)
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
                          "serverInfo": {"name": "agent-orchestrator", "version": __version__}}
            elif method in ("notifications/initialized", "initialized"):
                continue                                  # notification: no reply
            elif method == "tools/list":
                result = {"tools": [tool for tool in TOOLS if tool["name"] in served]}
            elif method == "tools/call":
                p = req.get("params") or {}
                payload = call(p.get("name"), p.get("arguments") or {}, cfg, allow_verify, role)
                # Ride along on whatever the agent called. MCP has no way to push,
                # so the next best thing is to attach the message to the next
                # response it asks for — which costs nothing and arrives sooner
                # than the agent's own next inbox check. Only where ao_inbox is served:
                # the note sends the agent there, and a role without it - a reviewer -
                # can neither read the message nor acknowledge it.
                if isinstance(payload, dict) and p.get("name") != "ao_inbox" and "ao_inbox" in served:
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
            # Escaped to ASCII, as the error reply above always was: this write is outside the handler, and
            # a character the stream cannot carry, or a lone surrogate a request held, took the server down.
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
