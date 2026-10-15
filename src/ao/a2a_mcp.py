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
import math
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ao import __version__, lib as A, outbound  # noqa: E402
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

# A header's value names a variable that holds it as `${NAME}`, read where the bridge runs (LEADS-SECRETS).
VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# A header whose name says it carries a credential: in the project's file its value must name a variable. Read by the
# words of the name, so `X-Api-Key` and `Proxy-Authorization` are and `X-Monkey` is not (LEADS-SECRETS).
CREDENTIAL_WORDS = frozenset({"auth", "authorization", "authentication", "key", "apikey", "token", "secret",
                              "password", "passwd", "cookie", "session", "credential", "credentials", "bearer"})
# The fewest characters a credential is sent with: one shorter is guessed sooner than hidden, and an answer that
# echoes it could not be told from ordinary text to be redacted (LEADS-SECRETS).
CREDENTIAL_MIN = 8


def secret_parts(value):
    """The parts of a credential header's value that are secret: of each `;` or `,` item, a `name=value` pair's value,
    and of an item with no name, its last word - the token after `Bearer`, a cookie's value, never its name."""
    parts = []
    for item in re.split(r"[;,]", str(value)):
        item = item.strip()
        pair = re.match(r"^[A-Za-z0-9_.!#$%&'*+^`|~-]+=(.*)$", item)
        if item:
            parts.append(pair.group(1).strip() if pair else (item.split() or [""])[-1])
    return parts


def credential_header(name):
    """Whether a header's name says it carries a credential, read by its words."""
    return any(word in CREDENTIAL_WORDS for word in re.split(r"[^a-z0-9]+", str(name).lower()) if word)


def registries():
    """The files agents are registered in, in the order `agents` reads them: the project's, then the machine's."""
    return (os.path.join(ROOT or os.getcwd(), ".ao", "a2a-agents.json"),
            os.path.join(A.HOME, ".ao", "a2a-agents.json"))


def _origin(url):
    """`scheme://host:port` of an http or https URL, its host lower-cased and its default port written; else None."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return f"{parts.scheme}://{parts.hostname.lower()}:{port or (443 if parts.scheme == 'https' else 80)}"


def bound_variables():
    """{variable: {origin}}: where the machine's file sends each variable one of its headers names (LEADS-SECRETS).

    `.ao/a2a-agents.json` is a file the implementer writes: one that kept `Bearer ${WEATHER_A2A_TOKEN}` and named
    another URL had the bridge send the token to that host. A variable the project's file names is sent only to an
    origin the machine's `~/.ao/a2a-agents.json`, outside every tree, sends it to.
    """
    try:
        with open(registries()[1], encoding=UTF8) as fh:
            machine = json.load(fh)
    except (OSError, ValueError):
        return {}
    bound = {}
    for agent in machine.values() if isinstance(machine, dict) else []:
        headers = agent.get("headers") if isinstance(agent, dict) else None
        origin = _origin(agent.get("url")) if isinstance(headers, dict) else None
        for value in (headers or {}).values() if origin else []:
            for variable in VARIABLE.findall(str(value)):
                bound.setdefault(variable, set()).add(origin)
    return bound


def resolved(aid, agent, in_project, bound=None):
    """(the agent with each `${NAME}` in its headers replaced by that variable's value, None), or (None, why not).

    `.ao/a2a-agents.json` is a file of the project, which a commit takes with the rest of `.ao/`, and an
    Authorization header written into it went into the repository's history with it. In the project's file a
    credential header names the variable that holds it, which is set where the bridge runs, and one written out
    there is refused rather than sent; `~/.ao/a2a-agents.json`, outside every repository, may hold one. A variable
    the project's file names is sent only to an origin the machine's file sends it to (`bound`, bound_variables). A value
    that holds a line break is refused here too: http.client refuses it with the value in its error, which the
    bridge answered with (LEADS-SECRETS).
    """
    headers = agent.get("headers") or {}
    if not isinstance(headers, dict):
        return None, f"agent {aid}: its headers are no JSON object"
    out = {}
    for name, value in headers.items():
        text = str(value)
        named = VARIABLE.findall(text)
        unset = [variable for variable in named if not os.environ.get(variable)]
        if unset:
            return None, (f"agent {aid}: its {name} header names ${{{unset[0]}}}, which is not set where the bridge "
                          "runs: set it in the environment the MCP client starts the bridge with")
        origin = _origin(agent.get("url"))
        loose = [variable for variable in named if in_project and origin not in (bound or {}).get(variable, ())]
        if loose:
            return None, (f"agent {aid}: its {name} header names ${{{loose[0]}}}, which the machine's "
                          f"~/.ao/a2a-agents.json sends to no agent at {origin or 'its URL'}: .ao/a2a-agents.json is "
                          "a file an agent can write, and a URL changed there carried the credential to another host; "
                          "register the agent with its URL and that header in ~/.ao/a2a-agents.json")
        if in_project and not named and credential_header(name):
            return None, (f"agent {aid}: its {name} header is written into .ao/a2a-agents.json, which a commit "
                          "takes with the project; name the variable that holds it instead, as \"Bearer ${NAME}\", "
                          "and set NAME where the bridge runs")
        out[name] = VARIABLE.sub(lambda found: os.environ[found.group(1)].strip(), text)
        if "\n" in out[name] or "\r" in out[name]:
            return None, f"agent {aid}: its {name} header holds a line break, which no header may"
        # A value a variable holds is a credential whatever the header's name, since that is what a variable is for
        # here: each is hidden in what the bridge answers, so none shorter than can be told apart is sent.
        short = [variable for variable in named if len(os.environ[variable].strip()) < CREDENTIAL_MIN]
        if short or credential_header(name) and any(len(part) < CREDENTIAL_MIN for part in secret_parts(out[name])):
            return None, (f"agent {aid}: its {name} header carries a credential of fewer than {CREDENTIAL_MIN} "
                          "characters, which ao sends nowhere: one that short is guessed, and an answer that echoes "
                          "it cannot be told apart to be hidden")
    return dict(agent, headers=out), None


def registry():
    """(configured agents, whether they are the project's): {id: {url, headers?, version?}} and where they came from,
    read in one opening of one file - the project's `.ao/a2a-agents.json` when it has one, else the machine's.

    A file, not an environment variable. The rest of this tool keeps its state in
    files that a human can read during an incident, and a registry that only
    exists inside a process's environment is the one thing you cannot inspect when
    something is wrong. A credential is the exception: a header names the variable
    that holds it (`resolved`). Whose file it was is taken from the same opening: told apart by a second look, a
    project file renamed away between the reading and the look was taken for the machine's, and its URLs were sent
    the person's variables with no origin bound to them (LEADS-SECRETS).
    """
    for at, path in enumerate(registries()):
        try:
            with open(path, encoding=UTF8) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return {}, at == 0
        return (data if isinstance(data, dict) else {}), at == 0
    return {}, False


def agents():
    """The configured agents, as `registry` reads them."""
    return registry()[0]


def _shown_url(url):
    """An agent's URL as an answer shows it: its origin - scheme, host and port - alone. A credential can stand in
    every other part of it, its user, its path (`/a2a/<token>`), its query (`?access_token=...`), and the model the
    bridge answers needs none of them (LEADS-SECRETS)."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(str(url or ""))
        netloc = (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
    except ValueError:
        return "…"
    return f"{parts.scheme}://{netloc}" if parts.scheme and netloc else "…"


# Follow no redirect: urllib carried every header of a request - an agent's Authorization among them - to wherever a
# redirect pointed, another host included (A2A-HARDEN). An agent that moved is written anew in .ao/a2a-agents.json.
# The opener is the one every request of ao's that carries a credential leaves by (HUNT-LEADS-SEC).
_OPENER = outbound.OPENER


def _post(url, headers, payload, timeout=60):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def fetch_card(url, headers=None, timeout=20):
    """The agent card, and the dialect it implies."""
    base = url.rstrip("/")
    for path in CARD_PATHS:
        for candidate in (base + path, base.rsplit("/", 1)[0] + path):
            try:
                req = urllib.request.Request(candidate, headers=headers or {})
                with _OPENER.open(req, timeout=timeout) as r:
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

    A message is sent in the other dialect only when the first said it does not
    take the method or the request: after a timeout, a dropped connection or any
    other answer it is not known whether the agent took the message, and sending
    it again could start the work twice (A2A-HARDEN).
    """
    url = agent["url"]
    order = [version] if version else ["1.0", "0.3"]
    once = method_key == "send"
    unknown = "the agent may have taken the message: ask a2a_task, or a2a_agents, before sending it again"
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
            if once and e.code not in (400, 404, 405, 415):
                return dict(last, note=unknown)
            continue
        except Exception as e:
            last = {"error": str(e)[:200], "dialect": v}
            if once:
                return dict(last, note=unknown)
            continue
        if not isinstance(out, dict):
            last = {"error": "the agent answered with no JSON-RPC object", "dialect": v}
            if once:
                return dict(last, note=unknown)
            continue
        err = out.get("error") or {}
        if not isinstance(err, dict):
            err = {"message": str(err)[:200]}
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


def _credentials(reg):
    """Each value a request of the bridge's may carry as a credential, longest first: a variable's value a header names,
    and a credential header's value as it is sent - the machine's file may hold one written out - with each of its
    parts, the token after `Bearer` and each value of `sid=...; csrf=...`, which an answer can echo alone."""
    found = set()
    for agent in reg.values() if isinstance(reg, dict) else []:
        headers = agent.get("headers") if isinstance(agent, dict) else None
        for name, value in headers.items() if isinstance(headers, dict) else []:
            text = str(value)
            for variable in VARIABLE.findall(text):
                held = os.environ.get(variable, "").strip()
                found.update([held] + re.split(r"[\s;,=:&]+", held))
            if credential_header(name):
                sent = VARIABLE.sub(lambda named: os.environ.get(named.group(1), "").strip(), text).strip()
                found.update([sent] + re.split(r"[\s;,=:&]+", sent) + secret_parts(sent))
        # A URL can carry one too: its user, its password, a value of its query (`?access_token=...`).
        from urllib.parse import urlsplit, parse_qsl
        try:
            parts = urlsplit(str((agent or {}).get("url") or "") if isinstance(agent, dict) else "")
            found.update(value for _, value in parse_qsl(parts.query))
            found.update(part for part in (parts.username, parts.password) if part)
        except ValueError:
            pass
    # None shorter than CREDENTIAL_MIN is sent (`resolved`), so none such needs hiding, and hiding every few letters
    # would garble the answer.
    return sorted((value for value in found if len(value) >= CREDENTIAL_MIN), key=len, reverse=True)


def _redacted(value, credentials):
    """`value` with each credential in its text replaced by `[credential]`."""
    if isinstance(value, str):
        for credential in credentials:
            value = value.replace(credential, "[credential]")
        return value
    if isinstance(value, dict):
        return {key: _redacted(item, credentials) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item, credentials) for item in value]
    return value


def call(name, args):
    """A tool's answer, with no credential the bridge sends in it.

    What an agent answers is its own text, and one that echoed the header it was sent - in an error, a task's
    message, its card - had the bridge hand the person's token to the model that called it, and to the notice the
    bridge records (LEADS-SECRETS). A credential is hidden as it was sent and by each of its parts; an agent the person
    gave one to can still pass it on encoded, which no hiding stops - the guard there is the origin the person bound it
    to (bound_variables), and docs/threat-model.md puts such an agent out of scope.
    """
    # One reading of the registry for the request and for what is hidden in its answer: read twice, a file changed
    # between the two sent a credential the second reading named and the first did not know to hide.
    reg, in_project = registry()
    credentials = _credentials(reg)
    return _redacted(_answer(name, args, credentials, reg, in_project), credentials)


def _answer(name, args, credentials, reg, in_project):
    # The agents are the project's when it has a registry, as the one reading of it said (`registry`).
    bound = bound_variables() if in_project else None
    if name == "a2a_agents":
        out = []
        for aid, a in reg.items():
            a, why = resolved(aid, a, in_project, bound)
            if why:
                out.append({"id": aid, "url": _shown_url(reg[aid].get("url")), "error": why, "reachable": False})
                continue
            card, ver, at = fetch_card(a["url"], a["headers"])
            out.append({"id": aid, "url": _shown_url(a["url"]),
                        "name": (card or {}).get("name"),
                        "description": (card or {}).get("description"),
                        "dialect": a.get("version") or ver or "undeclared",
                        "card": _shown_url(at) if at else None, "reachable": card is not None})
        return {"agents": out, "count": len(out),
                "note": "none configured; write .ao/a2a-agents.json" if not out else None}

    a = reg.get(args.get("agent") or "")
    if not a:
        return {"error": f"unknown agent {args.get('agent')!r}",
                "configured": list(reg)}
    a, why = resolved(args["agent"], a, in_project, bound)
    if why:
        return {"error": why}
    version = a.get("version")

    if name == "a2a_agent":
        card, ver, at = fetch_card(a["url"], a.get("headers"))
        if not card:
            return {"error": "no agent card at " + _shown_url(a["url"])}
        return {"card_url": _shown_url(at), "dialect": version or ver or "undeclared",
                "name": card.get("name"), "description": card.get("description"),
                "skills": [{"id": s.get("id"), "name": s.get("name"),
                            "description": s.get("description")}
                           for s in card.get("skills") or []]}

    if name == "a2a_send":
        # One message, whichever dialect carries it: an agent can tell a repeat by its id (A2A-HARDEN).
        message_id = f"ao-{os.urandom(8).hex()}"

        def params(d):
            part = {"text": args["message"]}
            if d["part_kind"]:
                part["kind"] = "text"
            msg = {"role": d["role_user"], "parts": [part], "messageId": message_id}
            for k in ("taskId", "contextId"):
                if args.get(k):
                    msg[k] = args[k]
            return {"message": msg}
        r = rpc(a, "send", params, version)
        if "result" not in r:
            return r
        view = _redacted(_task_view(r["result"]), credentials)
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
    # MCP's stdio transport is UTF-8, and each reply below is seven-bit JSON, as ao's own server's are (#71).
    A.utf8_streams()
    if "-C" in sys.argv:
        ROOT = os.path.abspath(os.path.expanduser(sys.argv[sys.argv.index("-C") + 1]))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (ValueError, RecursionError):
            # Parser limits must not end the bridge and take every tool away (LEADS-OPEN).
            continue
        if not isinstance(req, dict):
            # A JSON array or a scalar is no request, and reading it as one ended the server (A2A-HARDEN).
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": "a request is a JSON object"}}) + "\n")
            sys.stdout.flush()
            continue
        rid, method = req.get("id"), req.get("method")
        # Never dispatch or echo a container id: encoding a deeply nested one ended the server (LEADS-OPEN).
        if (rid is not None and type(rid) not in (str, int, float)) or (
                isinstance(rid, float) and not math.isfinite(rid)):
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": "a request id must be a string, finite number or null"}}) + "\n")
            sys.stdout.flush()
            continue
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
            # Escaped to ASCII, as the error reply above always was: a write outside the handler must not fail.
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
