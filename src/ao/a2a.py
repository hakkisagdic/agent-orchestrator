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
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import lib as A

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
        "version": "0.1.0",
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


class Handler(BaseHTTPRequestHandler):
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

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": str(e)}})
        rid, method = req.get("id"), req.get("method")
        params = req.get("params") or {}
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


def main():
    global CFG
    root = sys.argv[sys.argv.index("-C") + 1] if "-C" in sys.argv else None
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8731
    CFG = A.load_config(A.find_root(root))
    # Bind loopback only. This exposes project state; it is not for a network.
    srv = HTTPServer(("127.0.0.1", port), Handler)
    print(f"A2A on http://127.0.0.1:{port}  card: /.well-known/agent-card.json", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
