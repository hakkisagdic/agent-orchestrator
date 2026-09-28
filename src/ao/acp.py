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
