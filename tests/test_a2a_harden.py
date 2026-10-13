"""The A2A server and the A2A bridge hold to what a loopback service and a careful client hold to (A2A-HARDEN).

The bug hunter's first run over src/ao, on 2026-10-12, named five leads in a2a.py and a2a_mcp.py, all real. The
server answered one connection at a time and read a body's declared length with no limit in time or size, so one
client that stalled held every other; a request that was valid JSON but no object, or whose params were none,
ended with no answer. The bridge followed a redirect with every header of the request, an agent's Authorization
among them, to wherever it pointed; it sent a message again in the other dialect, under a new id, after a timeout
that left unknown whether the agent had taken it; and a line on its standard input that was a JSON array ended the
bridge. The server also answered a request named for any host, which `ao watch --web` refuses: a page from another
site can point its own name at 127.0.0.1 and read the board through a person's browser.
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ao import a2a, a2a_mcp, lib as A


@pytest.fixture
def server(project, monkeypatch):
    """The A2A server on a free loopback port, serving this project's board: (port, its Handler class)."""
    monkeypatch.setattr(a2a, "CFG", project)
    srv = a2a._Server(("127.0.0.1", 0), a2a.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()
    srv.server_close()


def _ask(port, method, path, body=None, host=None, length=None, timeout=10):
    """(status, the JSON answer) for one request, its Host and Content-Length as given."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
    data = body.encode("utf-8") if isinstance(body, str) else body
    if data is not None or length is not None:
        conn.putheader("Content-Length", str(length if length is not None else len(data)))
    conn.endheaders()
    if data is not None and length is None:
        conn.send(data)
    answer = conn.getresponse()
    status, text = answer.status, answer.read().decode("utf-8")
    conn.close()
    return status, json.loads(text) if text else None


def test_a_request_named_for_another_host_is_refused_before_the_board_is_read(server, monkeypatch):
    def read():
        raise AssertionError("the board was read for a request named for another host")

    monkeypatch.setattr(a2a, "tasks", read)
    status, answer = _ask(server, "GET", "/tasks", host="rebound.example:8731")

    assert status == 403 and f"127.0.0.1:{server}" in answer["error"] and f"localhost:{server}" in answer["error"]
    monkeypatch.setattr(a2a, "tasks", lambda v1=True: [])
    assert _ask(server, "GET", "/tasks", host=f"localhost:{server}") == (200, {"tasks": []})


def test_a_client_that_stalls_holds_up_no_other_and_is_closed(server, monkeypatch):
    monkeypatch.setattr(a2a.Handler, "timeout", 1)
    stalled = socket.create_connection(("127.0.0.1", server), timeout=10)
    stalled.sendall(f"POST / HTTP/1.1\r\nHost: 127.0.0.1:{server}\r\nContent-Length: 100\r\n\r\n{{".encode())
    started = time.monotonic()

    status, card = _ask(server, "GET", "/.well-known/agent-card.json", timeout=5)

    assert status == 200 and card["name"] and time.monotonic() - started < 5
    answer = stalled.recv(4096)                                # closed after its second, with a reason or none
    assert answer == b"" or answer.startswith(b"HTTP/1.0 400")
    stalled.close()


def test_a_body_past_the_limit_or_a_length_that_is_none_is_refused_unread(server):
    past = a2a.MAX_REQUEST_BYTES + 1

    assert _ask(server, "POST", "/", length=past)[0] == 413
    for length in ("-1", "many"):
        status, answer = _ask(server, "POST", "/", body=b"", length=length)
        assert (status, answer["error"]["code"]) == (400, -32700), length


def test_a_request_that_is_no_object_or_whose_params_are_none_is_answered_as_invalid(server):
    assert _ask(server, "POST", "/", body="[1, 2]")[1]["error"]["code"] == -32600
    asked = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "GetTask", "params": [1]})
    assert _ask(server, "POST", "/", body=asked)[1] == {"jsonrpc": "2.0", "id": 7, "error": {
        "code": -32602, "message": "params is a JSON object"}}


# ---- the bridge -------------------------------------------------------------------------------------------------

def _listen(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_the_bridge_follows_no_redirect_so_no_header_reaches_another_host(monkeypatch):
    reached = []

    class Elsewhere(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            reached.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        do_POST = do_GET

    elsewhere = _listen(Elsewhere)

    class Moved(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{elsewhere.server_port}/agent")
            self.end_headers()

        do_POST = do_GET

    moved = _listen(Moved)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    url = f"http://127.0.0.1:{moved.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as raised:
            a2a_mcp._post(url + "/", {"Authorization": "Bearer kept-here"}, {"jsonrpc": "2.0"}, timeout=10)
        assert raised.value.code == 302
        assert a2a_mcp.fetch_card(url, {"Authorization": "Bearer kept-here"}, timeout=10) == (None, None, None)
        assert reached == []
    finally:
        for srv in (moved, elsewhere):
            srv.shutdown()
            srv.server_close()


def test_a_send_whose_answer_is_unknown_is_not_sent_again_and_a_read_tries_the_other_dialect(monkeypatch):
    calls = []

    def timed_out(url, headers, payload, timeout=60):
        calls.append(payload["method"])
        raise TimeoutError("timed out")

    monkeypatch.setattr(a2a_mcp, "_post", timed_out)
    agent = {"url": "http://127.0.0.1:9/"}

    sent = a2a_mcp.rpc(agent, "send", lambda d: {"message": {}})
    assert calls == ["SendMessage"] and sent["error"] == "timed out" and "a2a_task" in sent["note"]

    calls.clear()
    assert "error" in a2a_mcp.rpc(agent, "get", lambda d: {"id": "t1"})
    assert calls == ["GetTask", "tasks/get"]


def test_a_message_keeps_its_id_in_the_dialect_it_falls_back_to(project, monkeypatch):
    payloads = []

    def answered(url, headers, payload, timeout=60):
        payloads.append(payload)
        if len(payloads) == 1:
            return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "method not found"}}
        return {"jsonrpc": "2.0", "id": 1, "result": {"id": "t1", "status": {"state": "submitted"}}}

    monkeypatch.setattr(a2a_mcp, "_post", answered)
    monkeypatch.setattr(a2a_mcp, "registry", lambda: ({"x": {"url": "http://127.0.0.1:9/"}}, False))
    monkeypatch.setattr(a2a_mcp, "ROOT", project["root"])

    view = a2a_mcp.call("a2a_send", {"agent": "x", "message": "hello"})

    assert (view["id"], view["dialect"]) == ("t1", "0.3")
    first, second = (payload["params"]["message"]["messageId"] for payload in payloads)
    assert [payload["method"] for payload in payloads] == ["SendMessage", "message/send"] and first == second


def test_a_line_that_is_no_object_is_answered_and_the_bridge_goes_on(project):
    lines = "[1, 2]\n" + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
    env = A.self_child_env(dict(os.environ))

    done = subprocess.run([sys.executable, "-m", "ao", "-C", project["root"], "a2a-mcp", "serve"],
                          input=lines.encode("utf-8"), capture_output=True, env=env, timeout=60)

    replies = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines() if line.strip()]
    assert replies == [{"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "a request is a JSON object"}},
                       {"jsonrpc": "2.0", "id": 1, "result": {}}], done.stderr
