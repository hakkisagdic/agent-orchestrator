"""The web view and the A2A server listen without asking the resolver for the loopback address's name
(LOOPBACK-BIND).

HTTPServer names itself with socket.getfqdn(), a reverse lookup that waits on a resolver slow to answer;
on a hosted macOS runner `ao watch --web` did not listen within the ten seconds its tests give it. Both
servers are named by their address, and the lookup here fails the test if it is made at all.
"""
import socket

from ao import a2a, web


def _no_lookup(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the resolver was asked for the loopback address's name")
    monkeypatch.setattr(socket, "getfqdn", refuse)


def test_the_web_view_listens_without_a_reverse_lookup(monkeypatch):
    _no_lookup(monkeypatch)
    server = web.listen({}, "proj", 5, 0)
    try:
        port = server.server_port
        assert server.server_name == "127.0.0.1" and port > 0
        assert {f"127.0.0.1:{port}", f"localhost:{port}"} <= server.hosts()
    finally:
        server.server_close()


def test_the_a2a_server_listens_without_a_reverse_lookup(monkeypatch):
    _no_lookup(monkeypatch)
    server = a2a._Server(("127.0.0.1", 0), a2a.Handler)
    try:
        assert server.server_name == "127.0.0.1" and server.server_port > 0
    finally:
        server.server_close()
