"""The bug hunter's leads on how ao handles a secret, each read against the code and held (HUNT-LEADS-SEC).

The bug hunter read src/ao on 2026-10-12 and sent six leads on secrets. Five were real. The account lookup sent the
OIDC access token a harness's CLI holds through urllib's urlopen, which follows a 301, 302 or 303 with every header -
`Authorization` among them - to whatever host it names; and a token store value that was JSON but no object raised
out of it where its callers expect None, as did an answer of another shape. ~/.ao/email.json was opened with the
umask, written, and only then chmod-ed to 0600, and truncated in place, so its credential was readable meanwhile and
a write cut short lost the settings that worked; ~/.ao/pings.json was written the same way. The API reviewer took a
base URL holding a query or a fragment, where `/chat/completions` then landed. And `ao mcp --role` with a role it
did not know served every tool. The sixth, that Telegram's bot token and formsubmit's alias travel with a redirect,
is not so: they are in the URL's path, and urllib asks the place the answer names, never the one it left; a test
below holds that too. Every exchange here is with a listener on 127.0.0.1 a test starts; no store, CLI or network
of a person's is read.
"""
import io
import json
import os
import re
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from ao import a2a_mcp, api_review, cli, drivers, email, lib as A, mcp, outbound, skillkit, storage, telegram

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQLITE3, AGENT = "/stand-in/sqlite3", "/stand-in/agent"          # never run: subprocess.run is a stand-in here
TOKEN = "the-cli-s-access-token"
ROLES = sorted(mcp.ROLE_TOOLS)


class _Instead:
    """A module as one module sees it, with some of its names replaced."""

    def __init__(self, module, **replaced):
        self._module, self._replaced = module, replaced

    def __getattr__(self, name):
        return self._replaced[name] if name in self._replaced else getattr(self._module, name)


class _Site:
    """A listener on 127.0.0.1 that keeps each request and answers as it is told: a status, a body, a Location."""

    def __init__(self, status=200, body=b"{}", location=None):
        self.requests, site = [], self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                      "body": data})
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site(monkeypatch):
    """Make listeners; no proxy the environment names stands between a test and them."""
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(outbound, "OPENER", outbound.opener())   # it read the proxies when it was built
    made = []

    def make(**kwargs):
        made.append(_Site(**kwargs))
        return made[-1]
    yield make
    for one in made:
        one.close()


def _seen(sites):
    """Every byte a listener was sent: path, headers and body, as one text."""
    return " ".join(f"{r['path']} {json.dumps(r['headers'])} {r['body']!r}" for one in sites for r in one.requests)


# ---- the account lookup (348764d14eb6ce83, c056e89251ea5853) ---------------------------------------------------------

def _lookup(monkeypatch, tmp_path, endpoint, stored=None):
    """The adapter's usage lookup, its token store holding `stored` and its CLI naming a profile; neither is run."""
    store = tmp_path / "data.sqlite3"
    store.write_bytes(b"")
    stored = json.dumps({"access_token": TOKEN}) if stored is None else stored

    def run(argv, **kwargs):
        if argv[0] == SQLITE3:
            return SimpleNamespace(returncode=0, stdout=stored + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="arn:agent:profile/one\n", stderr="")

    monkeypatch.setattr(drivers, "subprocess", _Instead(subprocess, run=run))
    monkeypatch.setattr(A, "program_path", lambda name, path=None: SQLITE3 if name == "sqlite3" else None)
    monkeypatch.setattr(A, "binary_candidates", lambda name, path=None: [AGENT])
    return {"token": {"sqlite": str(store), "table": "auth_kv", "key": "agent:token", "field": "access_token"},
            "profile": {"argv": ["agent", "whoami"], "prefix": "arn:agent"}, "endpoint": endpoint,
            "content_type": "application/x-amz-json-1.0", "target": "Agent.GetUsageLimits",
            "body": {"profileArn": "{profile}"}}


def test_the_account_lookup_follows_no_redirect_so_its_token_reaches_no_other_host(monkeypatch, tmp_path, site):
    elsewhere = site()
    moved = site(status=302, location=elsewhere.url + "/usage")

    answer = drivers.usage_limits(_lookup(monkeypatch, tmp_path, moved.url + "/"), timeout=10)

    assert elsewhere.requests == []
    [asked] = moved.requests                                   # the endpoint the adapter names, and it alone
    assert asked["headers"]["Authorization"] == "Bearer " + TOKEN
    assert answer["error"].startswith("HTTP 302, a redirect") and "billing.api.endpoint" in answer["error"]
    assert TOKEN not in answer["error"]


@pytest.mark.parametrize("stored", ["[]", '"a string"', "7", "null", '{"access_token": 7}',
                                    '{"access_token": ["t"]}'])
def test_a_token_store_value_that_is_no_object_or_no_string_token_is_no_token(monkeypatch, tmp_path, site, stored):
    endpoint = site()

    assert drivers.usage_limits(_lookup(monkeypatch, tmp_path, endpoint.url + "/", stored), timeout=10) is None
    assert endpoint.requests == []


@pytest.mark.parametrize("body, expected", [
    ([], {"error": "the response is no JSON object"}),
    ({"usageBreakdownList": 5}, {"error": "no CREDIT row in response"}),
    ({"usageBreakdownList": ["x", {"resourceType": "CREDIT", "currentUsage": 3, "usageLimit": 50}],
      "subscriptionInfo": [], "overageConfiguration": "off"}, {"used": 3, "limit": 50, "plan": None}),
], ids=["no object", "no list of rows", "parts of other shapes"])
def test_an_answer_of_another_shape_is_said_not_raised(monkeypatch, tmp_path, site, body, expected):
    endpoint = site(body=json.dumps(body).encode())

    answer = drivers.usage_limits(_lookup(monkeypatch, tmp_path, endpoint.url + "/"), timeout=10)

    assert {key: answer.get(key) for key in expected} == expected


# ---- the files that hold a credential (0e7db6c91316724e, ea81c8c6874fb38a) -------------------------------------------

POSIX = os.name != "nt"          # Windows keeps no file mode: there a private file is as private as its folder (#71)


@pytest.fixture
def umask_open():
    """The most permissive umask, so a file created without its own mode would be readable by everyone."""
    if not POSIX:
        yield
        return
    before = os.umask(0)
    yield
    os.umask(before)


def _writes(monkeypatch):
    """[(the mode of the file written to, the bytes)] for each write storage makes from here on."""
    written = []

    def write(fd, data):
        written.append((stat.S_IMODE(os.fstat(fd).st_mode) if POSIX else 0o600, bytes(data)))
        return os.write(fd, data)

    monkeypatch.setattr(storage, "os", _Instead(os, write=write))
    return written


def _modes_holding(written, secret):
    """The modes of the files each write that held `secret` went to: [] when none went through storage."""
    return [mode for mode, data in written if secret.encode() in data]


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode) if POSIX else 0o600


def test_a_private_file_is_never_readable_beyond_its_mode_and_replaces_the_one_before(tmp_path, umask_open):
    target = tmp_path / "credential.json"
    target.write_bytes(b"the old one")
    if POSIX:
        target.chmod(0o644)
    seen = []

    def checkpoint(step):
        for name in os.listdir(tmp_path):
            if name != target.name:
                seen.append((step, _mode(tmp_path / name)))

    storage.replace_file_durably(str(target), b"the new one", mode=0o600, _checkpoint=checkpoint)

    assert {step for step, _ in seen} >= {"temporary-opened", "temporary-written", "temporary-fsynced"}
    assert {mode for _, mode in seen} == {0o600}
    assert target.read_bytes() == b"the new one" and _mode(target) == 0o600
    assert os.listdir(tmp_path) == [target.name]


def test_a_file_already_at_the_private_temporary_name_is_neither_written_through_nor_removed(tmp_path, monkeypatch):
    target = tmp_path / "credential.json"
    target.write_bytes(b"the old one")
    monkeypatch.setattr(storage, "os", _Instead(os, urandom=lambda count: b"\0" * count))
    planted = tmp_path / f".credential.json.{os.getpid()}.{'00' * 4}.tmp"
    planted.write_bytes(b"someone else's")

    with pytest.raises(FileExistsError):
        storage.replace_file_durably(str(target), b"the new one", mode=0o600)

    assert planted.read_bytes() == b"someone else's" and target.read_bytes() == b"the old one"


@pytest.fixture
def conf(tmp_path, monkeypatch):
    path = tmp_path / "ao" / "email.json"
    monkeypatch.setattr(email, "CONF", str(path))
    return path


def test_the_mail_settings_exist_at_0600_from_their_creation(conf, monkeypatch, umask_open):
    written = _writes(monkeypatch)

    email.save("a-formsubmit-alias", to="me@example.test")

    assert set(_modes_holding(written, "a-formsubmit-alias")) == {0o600}     # every byte of it, from the first
    assert email.config()["token"] == "a-formsubmit-alias" and _mode(conf) == 0o600
    assert os.listdir(conf.parent) == [conf.name]


def test_a_mail_settings_write_cut_short_keeps_the_settings_that_worked(conf, umask_open):
    email.save_provider("smtp", host="smtp.example.test", to="me@example.test", password="the-first")

    with pytest.raises(TypeError):        # json stops at a value it cannot write, after the ones before it
        email._write({"provider": "smtp", "host": "smtp.example.test", "to": "me@example.test",
                      "password": "the-second", "name": "ao", "cut": object()})

    assert email.config()["password"] == "the-first" and _mode(conf) == 0o600
    assert os.listdir(conf.parent) == [conf.name]


def test_the_ping_url_is_written_at_0600_from_its_creation_and_replaced_whole(project, monkeypatch, umask_open):
    root = project["root"]
    A.project_key(root)                  # a project's name is written the first time any command sees it
    written = _writes(monkeypatch)

    A.set_ping_url(root, "https://ping.example.test/the-first")

    assert set(_modes_holding(written, "the-first")) == {0o600} and _mode(A.pings_path()) == 0o600

    def disk_full(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage, "os", _Instead(os, write=disk_full))
    with pytest.raises(OSError):
        A.set_ping_url(root, "https://ping.example.test/the-second")

    assert A.ping_url(root) == "https://ping.example.test/the-first"
    assert [name for name in os.listdir(os.path.dirname(A.pings_path())) if name.endswith(".tmp")] == []


# ---- the API reviewer (86c2dfdb83fe4f96) -----------------------------------------------------------------------------

def _providers(monkeypatch, tmp_path, *entries):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"review": {"api_providers": list(entries)}}), encoding="utf-8")
    monkeypatch.setenv("AO_SETTINGS", str(path))
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")


def _review(tmp_path, model="local/m"):
    diff, out = tmp_path / "candidate.diff", tmp_path / "answer.md"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    return api_review.main(["--model", model, "--diff-file", str(diff), "--output", str(out), "review this"])


@pytest.mark.parametrize("suffix", ["?key=a-key-in-the-query", "#v1", "?"])
def test_a_base_url_holding_a_query_or_a_fragment_is_refused_before_anything_is_sent(
        monkeypatch, tmp_path, site, capsys, suffix):
    served = site(body=json.dumps({"choices": [{"message": {"content": "VERDICT: APPROVED"}}]}).encode())
    _providers(monkeypatch, tmp_path, f"local {served.url}/v1{suffix} AO_TEST_API_KEY")

    assert _review(tmp_path) == 2
    err = capsys.readouterr().err
    assert served.requests == [] and "holds a query or a fragment" in err and "a-key-in-the-query" not in err


def test_a_base_url_naming_a_user_or_a_password_is_refused_and_not_repeated(monkeypatch, tmp_path, capsys):
    _providers(monkeypatch, tmp_path, "remote https://me:the-password@api.example.invalid/v1 AO_TEST_API_KEY")

    assert _review(tmp_path, "remote/m") == 2
    err = capsys.readouterr().err
    assert "names a user or a password" in err and "the-password" not in err


def test_the_api_reviewer_says_a_redirect_and_where_to_write_the_url_it_moved_to(monkeypatch, tmp_path, site,
                                                                                capsys):
    elsewhere = site()
    moved = site(status=301, location=elsewhere.url + "/v1/chat/completions")
    _providers(monkeypatch, tmp_path, f"local {moved.url}/v1 AO_TEST_API_KEY")

    assert _review(tmp_path) == 1

    err = capsys.readouterr().err
    assert elsewhere.requests == [] and len(moved.requests) == 1
    assert "HTTP 301, a redirect" in err and "review.api_providers" in err and "a-key-for-this-test" not in err


# ---- ao mcp --role (c19b4fef69b8ea1d) --------------------------------------------------------------------------------

def _server(monkeypatch, capsys, argv, requests=()):
    """Run the server itself with `argv`: (what it returned, its answers by id, what it wrote on stderr)."""
    monkeypatch.setattr(sys, "argv", ["ao-mcp"] + list(argv))
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(dict(request, jsonrpc="2.0")) + "\n"
                                                          for request in requests)))
    capsys.readouterr()
    code = mcp.main()
    out, err = capsys.readouterr()
    return code, {answer["id"]: answer for answer in map(json.loads, out.splitlines())}, err


LISTING = [{"id": 1, "method": "initialize", "params": {}}, {"id": 2, "method": "tools/list"}]


@pytest.mark.parametrize("action", ["serve", "config"])
def test_ao_mcp_refuses_a_role_it_does_not_know_and_names_the_roles(project, monkeypatch, capsys, action):
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(dict(request, jsonrpc="2.0")) + "\n"
                                                          for request in LISTING)))
    monkeypatch.setattr(A, "ao_program", lambda: "/opt/ao/bin/ao")
    capsys.readouterr()

    with pytest.raises(SystemExit) as stopped:
        cli.main(["-C", project["root"], "mcp", action, "--role", "reviwer"])

    out, err = capsys.readouterr()
    assert stopped.value.code == 2 and out == ""                 # no tool listed, no registration printed
    assert "reviwer" in err and all(role in err for role in ROLES)


@pytest.mark.parametrize("argv", [["--role", "reviwer"], ["--role"], ["--role="], ["--role=reviwer"],
                                  ["--role", "reviewer", "--role", "Admin"]],
                         ids=["unknown", "named nothing", "empty", "unknown, joined", "the last one unknown"])
def test_the_server_itself_refuses_a_role_it_does_not_know_before_it_answers(project, monkeypatch, capsys, argv):
    code, answers, err = _server(monkeypatch, capsys, ["-C", project["root"]] + argv, LISTING)

    assert code == 2 and answers == {}
    assert err.startswith("ao mcp: --role ") and all(role in err for role in ROLES)


def test_a_role_joined_to_its_option_is_that_role_and_no_role_is_still_every_tool(project, monkeypatch, capsys):
    code, answers, err = _server(monkeypatch, capsys, ["-C", project["root"], "--role=Reviewer"], LISTING)
    assert code is None
    assert {tool["name"] for tool in answers[2]["result"]["tools"]} == set(mcp.ROLE_TOOLS["reviewer"])

    code, answers, err = _server(monkeypatch, capsys, ["-C", project["root"]], LISTING)
    assert [tool["name"] for tool in answers[2]["result"]["tools"]] == list(mcp.EVERY_TOOL)
    assert "every tool is served" in err


def test_a_role_no_server_would_start_for_is_served_no_tool_should_one_reach_a_call(project):
    assert mcp.role_tools("reviwer") == ()
    assert mcp.call("ao_board", {}, project, False, "reviwer")["error"].startswith("ao_board is not one of")


def test_the_registrations_ao_init_writes_still_start_their_role_s_server(project, monkeypatch, capsys):
    """What skillkit.register_mcp writes, run as a client runs it (MCP-ROLES): kiro implements, claude architects."""
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name, *args, **kwargs: None)  # a harness's CLI is never run
    skillkit.register_mcp(root, {"claude-code", "kiro"}, exe="/x/ao")
    monkeypatch.setattr(sys, "argv", list(sys.argv))

    for relative, role in ((".mcp.json", "architect"), (".kiro/settings/mcp.json", "implementer")):
        with open(os.path.join(root, *relative.split("/")), encoding="utf-8") as fh:
            args = json.load(fh)["mcpServers"]["ao"]["args"]
        monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(dict(request, jsonrpc="2.0")) + "\n"
                                                              for request in LISTING)))
        capsys.readouterr()

        assert args[-2:] == ["--role", role] and cli.main(args) == 0

        answers = {answer["id"]: answer for answer in map(json.loads, capsys.readouterr().out.splitlines())}
        assert {tool["name"] for tool in answers[2]["result"]["tools"]} == set(mcp.ROLE_TOOLS[role]), relative


# ---- the other requests that leave ao --------------------------------------------------------------------------------

def test_one_redirect_handler_serves_every_request_that_carries_a_credential_header():
    """The A2A bridge and the API reviewer each kept a copy; the account lookup had none (HUNT-LEADS-SEC)."""
    sources = {os.path.relpath(os.path.join(folder, name), ROOT).replace(os.sep, "/"):
               open(os.path.join(folder, name), encoding="utf-8").read()
               for folder, _, names in os.walk(os.path.join(ROOT, "src", "ao")) for name in names
               if name.endswith(".py")}

    assert [path for path, text in sources.items() if "HTTPRedirectHandler" in text] == ["src/ao/outbound.py"]
    assert a2a_mcp._OPENER is outbound.OPENER
    for path in ("src/ao/drivers.py", "src/ao/api_review.py", "src/ao/a2a_mcp.py"):
        assert not re.search(r"\burlopen\(|\bbuild_opener\(", sources[path]), path


def test_a_token_in_the_url_s_path_does_not_travel_with_a_redirect(monkeypatch, site):
    """Why Telegram's and formsubmit's requests keep urllib's handling: neither carries its token in a header."""
    elsewhere = site()
    moved = site(status=302, location=elsewhere.url + "/moved")
    monkeypatch.setattr(telegram, "API", moved.url + "/bot{token}/{method}")
    monkeypatch.setattr(email, "ENDPOINT", moved.url + "/ajax/{token}")

    telegram.api({"token": "123:the-bot-token"}, "sendMessage", chat_id="1", text="the alarm's words")
    email.FormSubmit().send({"token": "the-alias", "name": "ao"}, "subject", "the alarm's words", "proj")

    assert len(moved.requests) == 2 and "the-bot-token" in _seen([moved]) and "the-alias" in _seen([moved])
    assert len(elsewhere.requests) == 2 and all(request["body"] == b"" for request in elsewhere.requests)
    assert "the-bot-token" not in _seen([elsewhere]) and "the-alias" not in _seen([elsewhere])
