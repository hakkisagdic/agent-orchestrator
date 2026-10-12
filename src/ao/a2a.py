#!/usr/bin/env python3
"""ao-a2a — serve this project's board as A2A tasks.

A2A (Agent2Agent) gives agents from different vendors a shared vocabulary for
work in flight. Its task states map onto this board almost exactly, and the
match is not a coincidence: `input-required` is precisely a slice parked on a
human decision, which is the state this tool was built to make visible.

Two states do not map cleanly, and the mapping table says so rather than
pretending otherwise. `inbox` and `queued` are both A2A `submitted` — A2A has no
notion of an item that arrived but has not been *admitted*, which is this tool's
central gate. And `verified` has no A2A equivalent at all: gates passed,
authority to land not yet granted. Renaming our states to match would delete the
distinction the tool exists to draw.

Read-only by design. Accepting work over A2A means letting a remote agent put
items on this board, and admission — deciding what may be worked unattended — is
not a thing to expose on a network port. Pull from a tracker instead; see
docs/sources.md.

Standard library only.
"""
import json
import os
import socket
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, lib as A

CFG = {}


def agent_card(base):
    root = CFG["root"]
    return {
        "protocolVersion": "1.0",
        "name": f"agent-orchestrator:{CFG.get('project') or os.path.basename(root)}",
        "description": "Orchestration state for one repository: what the implementer is "
                       "doing, what is blocked and on what, and what has been measured.",
        "url": base,
        "preferredTransport": "JSONRPC",
        "interfaces": [{"url": base, "protocolBinding": "JSONRPC",
                        "protocolVersion": "1.0"}],
        "version": __version__,
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {"id": "board", "name": "Work board",
             "description": "Every work item and its state, including what a blocked item "
                            "is waiting on.",
             "tags": ["status", "planning"]},
            {"id": "status", "name": "Implementer status",
             "description": "Live state of the agent working this repository.",
             "tags": ["status", "telemetry"]},
        ],
    }


def tasks(v1=True):
    """Board items as A2A tasks. The id is the board id, so it is stable.

    1.0 prefixes and upper-cases every state (`input-required` becomes
    `TASK_STATE_INPUT_REQUIRED`) and re-cases roles. Emit whichever the caller
    asked for, keyed off the method name it used, rather than picking one and
    making half the callers wrong.
    """
    root = CFG["root"]
    out = []
    for state, items in A.board(root).items():
        for it in items:
            note = it["notes"]
            a2a = A.A2A_STATE.get(state, "unknown")
            wire = ("TASK_STATE_" + a2a.upper().replace("-", "_")) if v1 else a2a
            task = {"id": it["id"], "contextId": os.path.basename(root),
                    "status": {"state": wire},
                    "metadata": {"aoState": state, "title": it["title"], **note}}
            if state == "blocked":
                # A2A calls it input-required; the useful part is *what* input.
                part = {"text": note.get("needs") or "reason not recorded"}
                if not v1:
                    part["kind"] = "text"
                task["status"]["message"] = {
                    "role": "ROLE_AGENT" if v1 else "agent", "parts": [part]}
            out.append(task)
    return out


# The largest request the board's methods take: one names a method and a task id. A body's declared length
# was read whole, however large (A2A-HARDEN).
MAX_REQUEST_BYTES = 1 << 20


class Handler(BaseHTTPRequestHandler):
    # A connection that sends nothing, or less than its Content-Length said, is closed rather than kept: the
    # server read a declared body with no limit in time, one connection at a time, so one stalled client held
    # every other (A2A-HARDEN).
    timeout = 30

    def parse_request(self):
        """Refuse any Host but this server's own before anything is read or computed (A2A-HARDEN).

        The server binds loopback alone, and a page from another site can still point its own name at
        127.0.0.1 and read the board through a person's browser (DNS rebinding): `ao watch --web` refused
        such a request, this server answered it.
        """
        if not super().parse_request():
            return False
        if (self.headers.get("Host") or "").strip().lower() not in self.server.hosts():
            self._send(403, {"error": f"this A2A server answers only to {' or '.join(sorted(self.server.hosts()))}"})
            return False
        return True

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/a2a+json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass                                     # the access log is noise here

    def do_GET(self):
        base = f"http://{self.headers.get('Host', 'localhost')}"
        if self.path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            return self._send(200, agent_card(base))
        if self.path == "/tasks":
            return self._send(200, {"tasks": tasks()})
        self._send(404, {"error": "not found"})

    def _error(self, rid, code, message, status=200):
        return self._send(status, {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        # A negative length read until the client closed; a large one was read whole (A2A-HARDEN).
        if n < 0:
            return self._error(None, -32700, "Content-Length is not a length", 400)
        if n > MAX_REQUEST_BYTES:
            return self._error(None, -32600, f"a request of {n} bytes is past the {MAX_REQUEST_BYTES} this server "
                                             "reads", 413)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._error(None, -32700, str(e), 400)
        # A JSON array or a scalar, or params that are no object, ended the request with no answer (A2A-HARDEN).
        if not isinstance(req, dict):
            return self._error(None, -32600, "a request is a JSON object")
        rid, method = req.get("id"), req.get("method")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            return self._error(rid, -32602, "params is a JSON object")
        if method in ("ListTasks", "tasks/list"):
            return self._send(200, {"jsonrpc": "2.0", "id": rid,
                                    "result": {"tasks": tasks(method[0].isupper())}})
        if method in ("GetTask", "tasks/get"):
            want = params.get("id")
            for t in tasks(method[0].isupper()):
                if t["id"] == want:
                    return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": t})
            return self._send(200, {"jsonrpc": "2.0", "id": rid,
                                    "error": {"code": -32001, "message": "task not found"}})
        if method in ("message/send", "message/stream", "tasks/cancel",
                      "SendMessage", "SubscribeToTask", "CancelTask"):
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32004,
                "message": "this endpoint is read-only: admitting work is a local decision, "
                           "not a network-exposed one. See docs/sources.md."}})
        self._send(200, {"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601, "message": f"method not found: {method}"}})


class _Server(ThreadingHTTPServer):
    """The A2A listener, named by its address: the resolver is not asked for the loopback address's name,
    which on a machine whose resolver is slow to answer held the server back from listening (LOOPBACK-BIND).

    Each connection has a thread of its own, so a client that stalls holds up no other, as in `ao watch --web`
    (A2A-HARDEN).
    """

    daemon_threads = True
    # As in the web view: elsewhere the option only lets a restarted server take its port back at once; on
    # Windows it lets the bind succeed on a port another program already listens on, where it must fail.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def hosts(self):
        """The Host values a request may carry: this server's address, by number or as localhost."""
        host, port = self.server_address[:2]
        names = {host, "localhost"}
        return {f"{name}:{port}" for name in names} | (names if port == 80 else set())

    def handle_error(self, request, client_address):
        """A client gone, or closed for sending nothing in time, is no error worth a traceback."""
        if isinstance(sys.exc_info()[1], (ConnectionError, socket.timeout)):
            return
        super().handle_error(request, client_address)


def main():
    global CFG
    A.utf8_streams()                         # what it prints is UTF-8, as every ao process's is (#71)
    root = sys.argv[sys.argv.index("-C") + 1] if "-C" in sys.argv else None
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8731
    CFG = A.load_config(A.find_root(root))
    # Bind loopback only. This exposes project state; it is not for a network.
    srv = _Server(("127.0.0.1", port), Handler)
    print(f"A2A on http://127.0.0.1:{port}  card: /.well-known/agent-card.json", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
