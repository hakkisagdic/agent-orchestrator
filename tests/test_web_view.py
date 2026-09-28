"""`ao watch --web`: the panel, the board and the fleet as read-only pages on 127.0.0.1 (WEB-VIEW).

Each test starts the server as the command line does, `ao watch --web --port 0`, in a thread
against a temporary project, and reads it the way a browser does.
"""
import html
import http.client
import re
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, web

BOARD = """# Board

## running
- [W1] the page

## blocked
- [W2] a question for the owner · needs: the owner's answer

## queued
- [W3] <script>alert(1)</script> after the page · needs: W1

## verified

## done
"""
URGENT = "20260926-0900-fable-to-kiro-DECISION-stop.md"


@pytest.fixture
def start(project, monkeypatch):
    """start(*words) runs `ao -C <project> watch --web --port 0 <words>` in a thread and returns once it serves.

    What comes back holds the port it listens on and the exit status the command returns once
    the server is shut down, which happens after the test. Serving is known from an answer to a
    path that is no page, so no page is computed before the test asks for one, and the line the
    command prints before it serves has been printed.
    """
    monkeypatch.setattr(A, "quota", lambda adapter, ttl=300: [])   # no real keyflip: the panel asks it for the quota
    monkeypatch.setattr(A, "all_workspaces", lambda: [{"path": project["root"], "mtime": time.time()}])
    monkeypatch.delenv("AO_ROLE", raising=False)
    Path(project["root"], ".ao", "board.md").write_text(BOARD, encoding="utf-8")
    listening, running = [], []
    listen = web.listen

    def recorded(*args, **kwargs):
        listening.append(listen(*args, **kwargs))
        return listening[-1]

    monkeypatch.setattr(web, "listen", recorded)

    def run(*words):
        view = SimpleNamespace(exits=[])
        argv = ["-C", project["root"], "watch", "--web", "--port", "0", *words]
        thread = threading.Thread(target=lambda: view.exits.append(cli.main(argv)), daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while len(listening) <= len(running) and thread.is_alive() and time.monotonic() < deadline:
            thread.join(0.01)
        assert len(listening) > len(running), f"ao watch --web exited {view.exits} before it listened"
        view.server, view.thread = listening[-1], thread
        view.port = view.server.server_port
        running.append(view)
        assert _request(view.port, "/not-a-page")[0] == 404
        return view

    yield run
    for view in running:
        view.server.shutdown()
        view.thread.join(10)


def _request(port, path="/", method="GET", host=None, body=None):
    """(status, headers, text) of one request, as a browser would make it."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request(method, path, body=body, headers={"Host": host} if host else {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode("utf-8")
    finally:
        connection.close()


def _text(page):
    """What a page shows inside its <pre>, as plain text."""
    return html.unescape(re.sub(r"<[^>]+>", "", re.search(r"<pre>(.*)</pre>", page, re.S).group(1)))


def _files(*roots):
    """Every file under these directories but a git directory, with its bytes."""
    return {str(path): path.read_bytes() for root in roots for path in sorted(Path(root).rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts}


def test_the_panel_the_board_and_the_fleet_are_pages_that_refresh_themselves(start, capsys):
    view = start("--interval", "5")

    pages = {path: _request(view.port, path) for path in ("/", "/board", "/fleet")}

    for path, (status, headers, page) in pages.items():
        assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8", path
        assert '<meta http-equiv="refresh" content="5">' in page, path
        assert all(f'<a href="{other}"' in page for other in ("/", "/board", "/fleet")), path
        assert page.count(' aria-current="page">') == 1 and f'<a href="{path}" aria-current="page">' in page, path
        assert "<title>proj · " in page, path
    panel, board, fleet = (_text(pages[path][2]) for path in ("/", "/board", "/fleet"))
    assert "PROJ" in panel and "REPOSITORY" in panel and "Board:   1 running · 1 blocked · 1 queued" in panel
    assert "RUNNING (1)" in board and "W2  a question for the owner   needs: the owner's answer" in board
    assert "ALL PROJECTS" in fleet and re.search(r"proj\s+unknown", fleet)
    assert f"ao watch --web: http://127.0.0.1:{view.port}/  read-only" in capsys.readouterr().out


def test_with_all_the_address_it_prints_is_the_fleet(start, capsys):
    view = start("--all")

    assert f"ao watch --web: http://127.0.0.1:{view.port}/fleet  read-only" in capsys.readouterr().out
    assert _request(view.port, "/fleet")[0] == 200


def test_the_board_page_is_the_text_ao_board_prints_banner_and_all(start, project, capsys):
    Path(project["root"], project["mailbox"], URGENT).write_text("# stop W1\n\n## URGENT\n", encoding="utf-8")
    view = start()
    capsys.readouterr()

    page = _request(view.port, "/board")[2]
    cli.cmd_board(project, SimpleNamespace(view=None))

    printed = capsys.readouterr().out
    assert printed.startswith("URGENT for the implementer: stop W1") and "QUEUED (1)" in printed
    assert _text(page) == printed.rstrip("\n")


def test_a_board_title_is_shown_as_text_and_the_page_may_run_no_script(start):
    view = start()

    status, headers, page = _request(view.port, "/board")

    assert status == 200 and "&lt;script&gt;alert(1)&lt;/script&gt; after the page" in page
    assert "<script" not in page
    assert headers["Content-Security-Policy"].startswith("default-src 'none'; style-src 'unsafe-inline'")
    assert headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"


def test_every_method_but_get_is_refused_and_changes_nothing(start, project):
    view = start()
    before = _files(project["root"])

    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "PROPFIND"):
        body = b"- [X] added over http" if method in ("POST", "PUT", "PATCH") else None

        status, headers, text = _request(view.port, "/board", method, body=body)

        assert status == 405 and headers["Allow"] == "GET", method
        assert text == ("" if method == "HEAD" else "ao watch --web is read-only: it answers GET and nothing else.\n")

    assert _files(project["root"]) == before
    assert _request(view.port, "/board")[0] == 200


def test_a_request_addressed_to_another_host_is_refused(start):
    view = start()

    status, _, text = _request(view.port, "/", host=f"attacker.example:{view.port}")

    assert status == 403 and f"localhost:{view.port}" in text and "<pre>" not in text
    assert _request(view.port, "/", host=f"localhost:{view.port}")[0] == 200
    assert _request(view.port, "/nowhere")[0] == 404


def test_nothing_but_a_loopback_address_is_bound():
    for host in ("0.0.0.0", "192.0.2.10", "localhost", "::1", ""):
        with pytest.raises(ValueError, match="loopback"):
            web.listen({}, "proj", 15, 0, host=host)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["watch", "--web", "--host", "0.0.0.0"])     # no option names another address

    server = web.listen({}, "proj", 15, 0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_a_page_marks_no_message_seen_and_writes_nothing_where_ao_board_marks_it_seen(start, project):
    Path(project["root"], project["mailbox"], URGENT).write_text("# stop W1\n\n## URGENT\n", encoding="utf-8")
    A.project_key(project["root"])          # a project is named the first time any command looks at it (#66)
    view = start()
    before = _files(project["root"], A.HOME)

    shown = [_text(_request(view.port, path)[2]) for path in ("/", "/board", "/fleet")]

    assert "URGENT for the implementer: stop W1" in shown[0] and "URGENT for the implementer: stop W1" in shown[1]
    assert _files(project["root"], A.HOME) == before
    assert [row for row in A.mail_log(project["root"]) if row.get("event") == "seen"] == []

    cli.cmd_board(project, SimpleNamespace(view=None))       # the terminal command shows it to its reader

    assert [(row["id"], row["by"]) for row in A.mail_log(project["root"]) if row.get("event") == "seen"] == \
        [(URGENT, "person")]


def test_a_page_that_cannot_be_computed_says_so_and_the_others_still_serve(start, monkeypatch, capsys):
    def broken(width=None):
        raise RuntimeError("the store\nis gone")

    monkeypatch.setattr(cli, "render_fleet", broken)
    monkeypatch.delenv("AO_DEBUG", raising=False)
    view = start()

    status, _, page = _request(view.port, "/fleet")

    assert status == 500 and "this page could not be computed: RuntimeError: the store is gone" in page
    assert '<meta http-equiv="refresh"' in page
    assert "ao watch --web: /fleet: RuntimeError: the store is gone" in capsys.readouterr().err
    assert _request(view.port, "/")[0] == 200


def test_the_panels_colours_become_classes_and_no_escape_reaches_the_page(start, monkeypatch):
    assert web.ansi_html("\x1b[1m\x1b[36m══\x1b[0m plain \x1b[2mdim <b>&\x1b[0m\x1b[?25l\x07end") == \
        '<span class="b cyan">══</span> plain <span class="dim">dim &lt;b&gt;&amp;</span>end'
    monkeypatch.setattr(A, "colour_enabled", lambda stream=None: True)      # as when started from a terminal
    view = start()

    page = _request(view.port, "/")[2]

    assert '<span class="b cyan">═══' in page and "\x1b" not in page


def test_the_stopped_server_ends_the_command_with_0(start):
    view = start()

    view.server.shutdown()
    view.thread.join(10)

    assert view.exits == [0]


def test_a_port_without_web_a_port_out_of_range_a_busy_port_and_a_zero_interval_are_refused(project, capsys):
    root = project["root"]
    assert cli.main(["-C", root, "watch", "--port", "9000"]) == 2
    assert "--port is the port of --web" in capsys.readouterr().err
    assert cli.main(["-C", root, "watch", "--web", "--port", "70000"]) == 2
    assert "a port is 0 to 65535" in capsys.readouterr().err
    assert cli.main(["-C", root, "watch", "--web", "--port", "0", "--interval", "0"]) == 2
    assert "a page refreshes at most once a second" in capsys.readouterr().err

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        assert cli.main(["-C", root, "watch", "--web", "--port", str(port)]) == 1
    assert f"cannot listen on 127.0.0.1:{port}" in capsys.readouterr().err
