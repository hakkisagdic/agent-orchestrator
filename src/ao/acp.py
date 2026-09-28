"""A client of the Agent Client Protocol just wide enough to ask an agent what it supports (ACP-PROBE).

ao has driven each harness through flags measured by hand and read what each did from its private stores
(docs/adapters.md). Most of the harnesses ao drives now also speak ACP: JSON-RPC 2.0, one message a line, over
the standard streams of a process the client starts, and an agent says what it supports when it is asked
`initialize`. This asks that and, when told to, `session/list`, and nothing else: it opens no session and
sends no prompt, so asking spends no quota. It is the first step of driving harnesses through ACP; nothing
in ao reads what it measures yet but `ao harness`.

The standard library only, as the rest of ao: a reader thread, a queue and a deadline. A request the agent
sends back is answered as a method this client does not have, since it declares no capability, and a line
that is not JSON is passed over.
"""
import json
import os
import queue
import signal
import subprocess
import threading
import time

from . import __version__

PROTOCOL_VERSION = 1
PROBE_TIMEOUT = 20.0
CANCEL_GRACE = 5.0                  # seconds a cancelled turn has to end before its agent is stopped
METHOD_NOT_FOUND = -32601


class ProbeError(RuntimeError):
    """An agent that did not answer what it was asked, and why."""


def _group():
    """Start the agent as the leader of its own process group, so stopping it stops what it started."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _stop(proc):
    """Stop the agent and everything it started, and wait for it."""
    if proc.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    for stream in (proc.stdin, proc.stdout):
        try:
            if stream:
                stream.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _read(stream, lines):
    for line in iter(stream.readline, ""):
        lines.put(line)
    lines.put(None)


def _send(proc, message):
    proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def _answer(proc, lines, request_id, method, deadline):
    """The agent's response to request `request_id`, answering what it asks meanwhile; raises ProbeError."""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise ProbeError(f"no answer to {method} within the time allowed")
        try:
            line = lines.get(timeout=left)
        except queue.Empty:
            continue
        if line is None:
            code = proc.poll()
            raise ProbeError(f"the agent ended before it answered {method}"
                             + (f" (exit {code})" if code is not None else ""))
        try:
            message = json.loads(line)
        except ValueError:
            continue                            # a line of the agent's own that is not a message
        if not isinstance(message, dict):
            continue
        if "method" in message:
            if "id" in message:                 # a request of the agent's: this client has no methods
                _send(proc, {"jsonrpc": "2.0", "id": message["id"],
                             "error": {"code": METHOD_NOT_FOUND, "message": "ao's probe answers no requests"}})
            continue                            # a notification: nothing to do
        if message.get("id") != request_id:
            continue
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise ProbeError(f"{method} was refused: {error.get('message') or error.get('code') or 'no reason'}")
        return message.get("result")


def summary(result):
    """What an `initialize` result says the agent supports, as flags and names ao keeps."""
    result = result if isinstance(result, dict) else {}
    caps = result.get("agentCapabilities") if isinstance(result.get("agentCapabilities"), dict) else {}

    def present(block):
        block = caps.get(block) if isinstance(caps.get(block), dict) else {}
        return sorted(key for key, value in block.items() if value not in (None, False))

    info = result.get("agentInfo") if isinstance(result.get("agentInfo"), dict) else {}
    methods = result.get("authMethods") if isinstance(result.get("authMethods"), list) else []
    return {
        "protocol_version": result.get("protocolVersion"),
        "agent": {"name": info.get("name"), "version": info.get("version")},
        "load_session": bool(caps.get("loadSession")),
        "session": present("sessionCapabilities"),
        "prompt": present("promptCapabilities"),
        "mcp": present("mcpCapabilities"),
        "auth": sorted(str(m["id"]) for m in methods if isinstance(m, dict) and m.get("id")),
    }


def probe(argv, cwd, timeout=PROBE_TIMEOUT, sessions=False, env=None):
    """What the agent `argv` starts says it supports; raises ProbeError when it does not say.

    Asks `initialize` and, with `sessions`, `session/list` for `cwd` where the agent lists sessions,
    keeping how many it listed and nothing of what they are. The agent is stopped, with everything it
    started, whatever it answers.
    """
    try:
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                **_group())
    except OSError as exc:
        raise ProbeError(f"could not start {argv[0]}: {exc}") from exc
    lines = queue.Queue()
    threading.Thread(target=_read, args=(proc.stdout, lines), daemon=True).start()
    deadline = time.monotonic() + timeout
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                     "params": {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {},
                                "clientInfo": {"name": "ao", "version": __version__}}})
        found = summary(_answer(proc, lines, 0, "initialize", deadline))
        if sessions and "list" in found["session"]:
            _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "session/list", "params": {"cwd": cwd}})
            listed = _answer(proc, lines, 1, "session/list", deadline)
            found["sessions"] = len(listed.get("sessions") or []) if isinstance(listed, dict) else 0
        return found
    except (OSError, ValueError) as exc:
        raise ProbeError(f"the agent's streams failed: {exc}") from exc
    finally:
        _stop(proc)


# ---- one session: a prompt turn, its updates and the permissions it asks for (ACP-CLIENT) ----------------------

def reject_everything(tool_call, options):
    """The default permission policy: no tool the agent asks leave to run is allowed."""
    return None


class Session:
    """One agent process speaking ACP, and one session in it (ACP-CLIENT).

    `permission(tool_call, options)` decides each `session/request_permission`: it returns the optionId it
    picks, or None, which picks the agent's first reject option or, where it offers none, answers cancelled.
    Every decision is kept in `decisions`, with the tool call it was about. A request for a client method
    ao does not provide - files, terminals - is answered as a method it does not have, since it declares
    no such capability. Use as a context manager, so the agent is stopped with everything it started.
    """

    def __init__(self, argv, cwd, env=None, permission=None):
        self.cwd = cwd
        self.permission = permission or reject_everything
        self.decisions, self.session_id, self._next = [], None, 0
        try:
            self.proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                         **_group())
        except OSError as exc:
            raise ProbeError(f"could not start {argv[0]}: {exc}") from exc
        self.lines = queue.Queue()
        threading.Thread(target=_read, args=(self.proc.stdout, self.lines), daemon=True).start()
        self._turn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        _stop(self.proc)

    def _request(self, method, params, timeout):
        request_id = self._next
        self._next += 1
        _send(self.proc, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return self._await(request_id, method, time.monotonic() + timeout)

    def _await(self, request_id, method, deadline):
        """The response to `request_id`, handling what the agent sends meanwhile; raises ProbeError."""
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ProbeError(f"no answer to {method} within the time allowed")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                code = self.proc.poll()
                raise ProbeError(f"the agent ended before it answered {method}"
                                 + (f" (exit {code})" if code is not None else ""))
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "method" in message:
                self._handle(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"] if isinstance(message["error"], dict) else {}
                raise ProbeError(f"{method} was refused: {error.get('message') or error.get('code') or 'no reason'}")
            return message.get("result")

    def _handle(self, message):
        """A request or a notification of the agent's."""
        method, params = message.get("method"), message.get("params") or {}
        if method == "session/update" and self._turn is not None:
            update = params.get("update") if isinstance(params.get("update"), dict) else {}
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                content = update.get("content") if isinstance(update.get("content"), dict) else {}
                if content.get("type") == "text":
                    self._turn["text"].append(str(content.get("text") or ""))
            elif kind in ("tool_call", "tool_call_update"):
                call_id = update.get("toolCallId")
                call = self._turn["tool_calls"].setdefault(call_id, {"id": call_id})
                call.update({key: update[key] for key in ("title", "kind", "status") if key in update})
            return
        if "id" not in message:
            return                                          # another notification: nothing to do
        if method == "session/request_permission":
            self._reply(message["id"], {"outcome": self._decide(params)})
            return
        _send(self.proc, {"jsonrpc": "2.0", "id": message["id"],
                          "error": {"code": METHOD_NOT_FOUND, "message": f"ao provides no {method}"}})

    def _decide(self, params):
        """The outcome of one permission request, by the policy, kept in `decisions`."""
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        options = [o for o in params.get("options") or [] if isinstance(o, dict) and o.get("optionId")]
        if self._turn is not None and self._turn.get("cancelled"):
            # A request pending when the turn was cancelled is answered cancelled, as the protocol asks.
            self.decisions.append({"tool": tool_call.get("title"), "kind": tool_call.get("kind"),
                                   "decision": "cancelled"})
            return {"outcome": "cancelled"}
        try:
            chosen = None if self._turn is None else self.permission(tool_call, options)
        except Exception:
            chosen = None                                   # a policy that fails allows nothing
        ids = {o["optionId"]: o for o in options}
        if chosen not in ids:
            chosen = next((o["optionId"] for o in options if str(o.get("kind", "")).startswith("reject")), None)
        outcome = {"outcome": "selected", "optionId": chosen} if chosen else {"outcome": "cancelled"}
        kind = ids.get(chosen, {}).get("kind") if chosen else "cancelled"
        self.decisions.append({"tool": tool_call.get("title"), "kind": tool_call.get("kind"), "decision": kind})
        return outcome

    def _reply(self, request_id, result):
        _send(self.proc, {"jsonrpc": "2.0", "id": request_id, "result": result})

    def initialize(self, timeout=PROBE_TIMEOUT):
        """What the agent supports, as `probe` keeps it."""
        return summary(self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {},
                                                    "clientInfo": {"name": "ao", "version": __version__}}, timeout))

    def new_session(self, timeout=PROBE_TIMEOUT, mcp_servers=()):
        """Open a session in `cwd`; returns its id."""
        result = self._request("session/new", {"cwd": self.cwd, "mcpServers": list(mcp_servers)}, timeout)
        self.session_id = (result or {}).get("sessionId") if isinstance(result, dict) else None
        if not self.session_id:
            raise ProbeError("session/new answered no sessionId")
        return self.session_id

    def prompt(self, text, timeout):
        """One prompt turn: {"ended", "text", "tool_calls"}, where "ended" is the stop reason the agent gave.

        The text is the agent's message chunks joined in the order they came. A turn that has not
        ended by `timeout` is cancelled - pending permission requests answered cancelled, as the
        protocol asks - and given a few seconds to end; its stop reason is then "cancelled", or
        "timeout" when it did not end even so.
        """
        if not self.session_id:
            raise ProbeError("no session: new_session first")
        self._turn = {"text": [], "tool_calls": {}}
        request_id = self._next
        self._next += 1
        _send(self.proc, {"jsonrpc": "2.0", "id": request_id, "method": "session/prompt",
                          "params": {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]}})
        try:
            result = self._await(request_id, "session/prompt", time.monotonic() + timeout)
            stop = (result or {}).get("stopReason") if isinstance(result, dict) else None
        except ProbeError as exc:
            if "within the time allowed" not in str(exc):
                raise
            self._turn["cancelled"] = True
            _send(self.proc, {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session_id}})
            try:
                result = self._await(request_id, "session/prompt", time.monotonic() + CANCEL_GRACE)
                stop = (result or {}).get("stopReason") if isinstance(result, dict) else "cancelled"
            except ProbeError:
                stop = "timeout"
        turn, self._turn = self._turn, None
        return {"ended": stop, "text": "".join(turn["text"]), "tool_calls": list(turn["tool_calls"].values())}
