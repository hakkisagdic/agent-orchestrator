#!/usr/bin/env python3
"""ao-a2a-mcp — reach A2A agents from an MCP-only client.

Kiro speaks MCP and does not speak A2A: no agent card, no task lifecycle, no
`.well-known/agent-card.json` anywhere in its CLI or IDE. This bridges that gap in
the one direction that helps — remote A2A agents appear as MCP tools, so an
MCP-only implementer can discover them, send them work and follow the result.

It cannot make MCP interruptible. Nothing can; see docs/mcp.md.

## Why not just use the existing bridge

`a2anet/a2a-mcp` does the same job and predates this. It speaks A2A 0.3, and 0.3
is not what the specification says any more: 1.0 renamed every JSON-RPC method
(`message/send` → `SendMessage`), re-cased the roles (`user` → `ROLE_USER`),
prefixed every task state (`input-required` → `TASK_STATE_INPUT_REQUIRED`) and
changed the content type. An 0.3-only client silently fails against a 1.0 agent,
and a 1.0-only client fails against everything deployed today.

So this negotiates. It reads the agent card, takes the version the agent declares,
and speaks that dialect — falling back to trying both when a card says nothing,
because a card that omits its version is more common than one that lies about it.

Standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ao import __version__, lib as A  # noqa: E402
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

PROTOCOL = "2025-06-18"
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")

# The two dialects, side by side, because the wire format is the whole problem.
DIALECTS = {
    "1.0": {"send": "SendMessage", "get": "GetTask", "list": "ListTasks",
            "cancel": "CancelTask", "role_user": "ROLE_USER",
            "content_type": "application/a2a+json",
            "state_prefix": "TASK_STATE_", "part_kind": False},
    "0.3": {"send": "message/send", "get": "tasks/get", "list": "tasks/list",
            "cancel": "tasks/cancel", "role_user": "user",
            "content_type": "application/json",
            "state_prefix": "", "part_kind": True},
}


def normalise_state(state):
    """One vocabulary for the caller, whichever dialect produced it."""
    if not state:
        return "unknown"
    s = str(state)
    if s.startswith("TASK_STATE_"):
        s = s[len("TASK_STATE_"):]
    return s.lower().replace("_", "-")


ROOT = None


def agents():
    """Configured agents: {id: {url, headers?, version?}} from .ao/a2a-agents.json.

    A file, not an environment variable. The rest of this tool keeps its state in
    files that a human can read during an incident, and a registry that only
    exists inside a process's environment is the one thing you cannot inspect when
    something is wrong.
    """
    for p in (os.path.join(ROOT or os.getcwd(), ".ao", "a2a-agents.json"),
              os.path.join(A.HOME, ".ao", "a2a-agents.json")):
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding=UTF8))
            except Exception:
                return {}
    return {}


def _post(url, headers, payload, timeout=60):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def fetch_card(url, headers=None, timeout=20):
    """The agent card, and the dialect it implies."""
    base = url.rstrip("/")
    for path in CARD_PATHS:
        for candidate in (base + path, base.rsplit("/", 1)[0] + path):
            try:
                req = urllib.request.Request(candidate, headers=headers or {})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    card = json.loads(r.read().decode())
                declared = str(card.get("protocolVersion", "")).strip()
                # Interfaces can declare per-binding versions; prefer the JSON-RPC one.
                for iface in card.get("interfaces") or card.get("additionalInterfaces") or []:
                    if str(iface.get("protocolBinding", "")).upper() == "JSONRPC":
                        declared = str(iface.get("protocolVersion", declared))
                        break
                return card, ("1.0" if declared.startswith("1") else
                              "0.3" if declared else None), candidate
            except Exception:
                continue
    return None, None, None


def rpc(agent, method_key, params, version=None, timeout=90):
    """One JSON-RPC call in whichever dialect the agent speaks.

    When the card declares nothing, try 1.0 and fall back to 0.3 on a
    method-not-found. Guessing once and caching beats failing on a card that
    simply omitted a field.
    """
    url = agent["url"]
    order = [version] if version else ["1.0", "0.3"]
    last = None
    for v in order:
        d = DIALECTS[v]
        headers = dict(agent.get("headers") or {})
        headers["Content-Type"] = d["content_type"]
        headers.setdefault("A2A-Version", v)
        try:
            out = _post(url, headers, {"jsonrpc": "2.0", "id": 1,
                                       "method": d[method_key],
                                       "params": params(d)}, timeout)
        except urllib.error.HTTPError as e:
            last = {"error": f"HTTP {e.code}", "dialect": v}
            continue
        except Exception as e:
            last = {"error": str(e)[:200], "dialect": v}
            continue
        err = out.get("error") or {}
        if err.get("code") in (-32601, -32600):        # method not found / invalid
            last = {"error": err.get("message", "method not found"), "dialect": v}
            continue
        if err:
            return {"error": err.get("message", "error"), "code": err.get("code"),
                    "dialect": v}
        return {"result": out.get("result", out), "dialect": v}
    return last or {"error": "no dialect succeeded"}


def _task_view(res):
    """Flatten a Task from either dialect into one shape."""
    t = res.get("task") or res
    status = t.get("status") or {}
    msg = status.get("message") or {}
    parts = msg.get("parts") or []
    text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
    return {"id": t.get("id"), "contextId": t.get("contextId"),
            "state": normalise_state(status.get("state")),
            "message": text or None,
            "artifacts": [{"artifactId": a.get("artifactId"), "name": a.get("name"),
                           "text": " ".join(p.get("text", "")
                                            for p in (a.get("parts") or []))[:4000]}
                          for a in (t.get("artifacts") or [])]}


TOOLS = [
    {"name": "a2a_agents",
     "description": "List the configured A2A agents, with the protocol dialect each "
                    "one speaks.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "a2a_agent",
     "description": "One agent's card: what it is, and the skills it advertises.",
     "inputSchema": {"type": "object", "required": ["agent"],
                     "properties": {"agent": {"type": "string"}}}},
    {"name": "a2a_send",
     "description": "Send a message to an A2A agent. Returns the task, including "
                    "state 'input-required' when the agent needs more from you.",
     "inputSchema": {"type": "object", "required": ["agent", "message"],
                     "properties": {"agent": {"type": "string"},
                                    "message": {"type": "string"},
                                    "taskId": {"type": "string"},
                                    "contextId": {"type": "string"}}}},
    {"name": "a2a_task",
     "description": "Current state and artifacts of a task you already sent.",
     "inputSchema": {"type": "object", "required": ["agent", "taskId"],
                     "properties": {"agent": {"type": "string"},
                                    "taskId": {"type": "string"}}}},
    {"name": "a2a_cancel",
     "description": "Cancel a task.",
     "inputSchema": {"type": "object", "required": ["agent", "taskId"],
                     "properties": {"agent": {"type": "string"},
                                    "taskId": {"type": "string"}}}},
]


def call(name, args):
    reg = agents()
    if name == "a2a_agents":
        out = []
        for aid, a in reg.items():
            card, ver, at = fetch_card(a["url"], a.get("headers"))
            out.append({"id": aid, "url": a["url"],
                        "name": (card or {}).get("name"),
                        "description": (card or {}).get("description"),
                        "dialect": a.get("version") or ver or "undeclared",
                        "card": at, "reachable": card is not None})
        return {"agents": out, "count": len(out),
                "note": "none configured; write .ao/a2a-agents.json" if not out else None}

    a = reg.get(args.get("agent") or "")
    if not a:
        return {"error": f"unknown agent {args.get('agent')!r}",
                "configured": list(reg)}
    version = a.get("version")

    if name == "a2a_agent":
        card, ver, at = fetch_card(a["url"], a.get("headers"))
        if not card:
            return {"error": "no agent card at " + a["url"]}
        return {"card_url": at, "dialect": version or ver or "undeclared",
                "name": card.get("name"), "description": card.get("description"),
                "skills": [{"id": s.get("id"), "name": s.get("name"),
                            "description": s.get("description")}
                           for s in card.get("skills") or []]}

    if name == "a2a_send":
        def params(d):
            part = {"text": args["message"]}
            if d["part_kind"]:
                part["kind"] = "text"
            msg = {"role": d["role_user"], "parts": [part],
                   "messageId": f"ao-{os.urandom(8).hex()}"}
            for k in ("taskId", "contextId"):
                if args.get(k):
                    msg[k] = args[k]
            return {"message": msg}
        r = rpc(a, "send", params, version)
        if "result" not in r:
            return r
        view = _task_view(r["result"])
        # Remember what we sent, so a task can be followed after the turn that
        # started it has ended — the common case for anything slow.
        A.record_notice(ROOT or os.getcwd(), "a2a", f"{args['agent']} task {view.get('id')} "
                        f"{view.get('state')}", sent=False, key="a2a")
        return {**view, "dialect": r["dialect"]}

    if name in ("a2a_task", "a2a_cancel"):
        key = "get" if name == "a2a_task" else "cancel"
        r = rpc(a, key, lambda d: {"id": args["taskId"]}, version)
        if "result" not in r:
            return r
        return {**_task_view(r["result"]), "dialect": r["dialect"]}

    return {"error": f"unknown tool {name}"}


def main():
    global ROOT
    if "-C" in sys.argv:
        ROOT = os.path.abspath(os.path.expanduser(sys.argv[sys.argv.index("-C") + 1]))
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
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "ao-a2a-mcp", "version": __version__}}
            elif method in ("notifications/initialized", "initialized"):
                continue
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = req.get("params") or {}
                payload = call(p.get("name"), p.get("arguments") or {})
                result = {"content": [{"type": "text",
                                       "text": json.dumps(payload, ensure_ascii=False,
                                                          indent=2, default=str)}]}
            elif method == "ping":
                result = {}
            else:
                raise ValueError(f"method not found: {method}")
        except Exception as e:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "error": {"code": -32603,
                                                   "message": str(e)}}) + "\n")
            sys.stdout.flush()
            continue
        if rid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result},
                                        ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
