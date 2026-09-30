"""`ao watch --web`: what `ao watch`, `ao board` and `ao fleet` print, as read-only pages on 127.0.0.1 (WEB-VIEW).

A view of ao, not a new app. A person who would rather keep a browser tab open than a terminal
gets the text those commands print, computed when a page is asked for by the functions the
commands print with, and nothing more: no state of its own, no form, no method but GET, no
script, and nothing loaded from anywhere else. The command line hands this module its pages,
each a function returning a command's text; this module knows HTTP and HTML and nothing about a
project.

Standard library only.
"""
import html
import ipaddress
import os
import re
import socketserver
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The class each code the command line's palette (lib.C) writes puts on the page's text.
SGR_CLASSES = {"1": "b", "2": "dim", "31": "red", "32": "green", "33": "yellow", "34": "blue",
               "35": "mag", "36": "cyan"}
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
# Whatever else a terminal would act on: another escape sequence, a lone escape, a control character.
_TERMINAL_ONLY = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]")

# The page loads nothing and runs nothing: its style is inline, it holds no script, it posts no
# form, and no other page may frame it.
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

STYLE = """
:root { color-scheme: light dark; --bg: #fbfbf9; --fg: #1d1d1b; --muted: #6d6c67; --rule: #dcdbd5;
  --link: #1d5bb8; --red: #b42318; --green: #1a7f37; --yellow: #8a5a00; --blue: #1d5bb8;
  --mag: #8f3aa0; --cyan: #0a6e7c; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --fg: #e8e6e1; --muted: #9b9990; --rule: #34332f; --link: #82b1ff;
    --red: #ff8078; --green: #5fd46e; --yellow: #e6b84a; --blue: #82b1ff; --mag: #d4a6ff; --cyan: #5ad3dc; }
}
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
nav { display: flex; flex-wrap: wrap; gap: 4px 18px; align-items: baseline; padding: 10px 16px;
  border-bottom: 1px solid var(--rule); }
nav a { color: var(--link); text-decoration: none; }
nav a:hover { text-decoration: underline; }
nav a[aria-current="page"] { color: var(--fg); font-weight: 700; }
nav .note { color: var(--muted); margin-left: auto; }
pre { margin: 0; padding: 12px 16px 32px; overflow-x: auto; font: inherit; }
.b { font-weight: 700; }
.dim { color: var(--muted); }
.red { color: var(--red); } .green { color: var(--green); } .yellow { color: var(--yellow); }
.blue { color: var(--blue); } .mag { color: var(--mag); } .cyan { color: var(--cyan); }
"""


def loopback(host):
    """Whether `host` is an IPv4 loopback address, the only kind of address this view binds.

    A name is refused rather than resolved: `localhost` is loopback where the hosts file says so,
    and the check has to hold on every machine.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and address.is_loopback


def ansi_html(text):
    """`text` as a terminal draws it, as HTML for a <pre>: each palette code becomes a class.

    The panel carries codes only where ao's own output would (lib.colour_enabled), so a page is
    as colourful as the terminal it was started from. Anything else a terminal would act on is
    dropped, and every character of the text itself is escaped: a board title or a transcript
    line is shown, never interpreted.
    """
    out, bold, dim, colour = [], False, False, None
    for index, piece in enumerate(_SGR.split(text)):
        if index % 2:                                  # the parameters of one code, between two runs of text
            for code in piece.split(";"):
                if code in ("", "0"):
                    bold, dim, colour = False, False, None
                elif code == "1":
                    bold = True
                elif code == "2":
                    dim = True
                elif code == "22":
                    bold = dim = False
                elif code == "39":
                    colour = None
                elif code in SGR_CLASSES:
                    colour = SGR_CLASSES[code]
            continue
        shown = html.escape(_TERMINAL_ONLY.sub("", piece), quote=False)
        if not shown:
            continue
        classes = [name for name, on in (("b", bold), ("dim", dim)) if on] + ([colour] if colour else [])
        out.append(f'<span class="{" ".join(classes)}">{shown}</span>' if classes else shown)
    return "".join(out)


def document(name, pages, current, body, interval, at):
    """One whole page: `body`, already HTML, under a bar naming the project and linking every page.

    The refresh is a meta tag, so the page reloads itself with no script; `at` is when its text was computed.
    """
    here = ' aria-current="page"'
    links = "\n".join('<a href="%s"%s>%s</a>' % (html.escape(path), here if path == current else "",
                                                  html.escape(label)) for path, (label, _) in pages.items())
    title = f"{html.escape(name)} · {html.escape(pages[current][0])} · ao"
    return (f'<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta http-equiv="refresh" content="{int(interval)}">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{title}</title>\n<style>{STYLE}</style>\n</head>\n'
            f'<body>\n<nav><span class="b">ao · {html.escape(name)}</span>\n{links}\n'
            f'<span class="note">read-only · refreshes every {int(interval)}s · '
            f'{time.strftime("%H:%M:%S", time.localtime(at))}</span></nav>\n'
            f'<main><pre>{body}</pre></main>\n</body>\n</html>\n')


class _Handler(BaseHTTPRequestHandler):
    """GET a page; nothing else."""

    timeout = 30                     # a connection that sends nothing is closed rather than kept

    def log_message(self, format, *args):
        pass                         # the access log is noise here, as in a2a.py

    def parse_request(self):
        """Refuse any method but GET, and any Host but this server's own, before a page is computed.

        Refused here rather than in a do_* method, so that no method - POST, PUT, DELETE, one nobody
        has named - reaches anything: the view has nothing to write, and says so with 405. The Host
        check stops DNS rebinding, where a page from another site points its own name at 127.0.0.1
        and reads this view through the person's browser.
        """
        if not super().parse_request():
            return False
        if self.command != "GET":
            self._reply(HTTPStatus.METHOD_NOT_ALLOWED, "text/plain",
                        "ao watch --web is read-only: it answers GET and nothing else.\n", allow="GET")
            return False
        if (self.headers.get("Host") or "").strip().lower() not in self.server.hosts():
            self._reply(HTTPStatus.FORBIDDEN, "text/plain",
                        f"ao watch --web answers only to {' or '.join(sorted(self.server.hosts()))}.\n")
            return False
        return True

    def do_GET(self):
        pages, path = self.server.pages, self.path.split("?", 1)[0]
        if path not in pages:
            return self._reply(HTTPStatus.NOT_FOUND, "text/plain",
                               f"no page {path}; this view serves {', '.join(pages)}\n")
        status = HTTPStatus.OK
        try:
            with self.server.computing:
                body = ansi_html(pages[path][1]())
        except Exception as exc:
            # Said on the page and on the terminal that started the view; the next refresh asks again.
            what = f"{type(exc).__name__}: {' '.join(str(exc).split())}"
            print(f"ao watch --web: {path}: {what}", file=sys.stderr, flush=True)
            if os.environ.get("AO_DEBUG", "") not in ("", "0"):
                traceback.print_exc()
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            body = (f'<span class="red b">this page could not be computed: {html.escape(what, quote=False)}</span>\n'
                    f'<span class="dim">it is computed again at the next refresh</span>')
        self._reply(status, "text/html", document(self.server.name, pages, path, body, self.server.interval,
                                                  time.time()))

    def _reply(self, status, kind, text, allow=None):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


class _Server(ThreadingHTTPServer):
    """One loopback listener and the pages it serves.

    Each connection has a thread, so a browser's idle speculative connection holds up no other;
    the pages themselves are computed one at a time, as the terminal panel computes its frames.
    """

    daemon_threads = True
    # Elsewhere the option only lets a restarted view take its port back at once; on Windows it lets
    # the bind succeed on a port another program already listens on, where it must fail instead.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, pages, name, interval):
        self.pages, self.name, self.interval = pages, name, interval
        self.computing = threading.Lock()
        super().__init__(address, _Handler)

    def server_bind(self):
        """Bind, and name the server by its address rather than by asking the resolver for one (LOOPBACK-BIND).

        HTTPServer names itself with socket.getfqdn(), a reverse lookup of the loopback address, and where
        the resolver does not answer it at once - a hosted macOS runner is such a machine - the view waited
        out its timeouts before it listened. Nothing here reads the name: every check is by address.
        """
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def hosts(self):
        """The Host values a request may carry: this server's address, by number or as localhost."""
        host, port = self.server_address[:2]
        names = {host, "localhost"}
        return {f"{name}:{port}" for name in names} | (names if port == 80 else set())

    def handle_error(self, request, client_address):
        """A browser that closed its connection before the page was written is no error worth a traceback."""
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def listen(pages, name, interval, port, host="127.0.0.1"):
    """A server bound to `host`:`port` and listening, not yet serving; its serve_forever() serves.

    `pages` maps a path to (label, compute), in the order the bar links them: compute() returns
    the text a command prints, and it is called for every request of its page. `interval` is
    the seconds between a page's refreshes. Port 0 takes a free one, which `server_port` names.
    A host that is not an IPv4 loopback address is refused with ValueError before a socket is
    made; a port that cannot be bound raises OSError.
    """
    if not loopback(host):
        raise ValueError(f"ao watch --web binds a loopback address only, such as 127.0.0.1; refused {host!r}")
    return _Server((host, port), pages, name, interval)
