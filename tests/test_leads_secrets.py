"""Where ao had a person type, print or keep a secret, it no longer does (LEADS-SECRETS).

ao's bug hunter and another slice's agent named six leads on 2026-10-12, all real. `ao telegram setup` had a person
put the bot token into a curl command line - on Windows a PowerShell one - to find their chat id, then echo the file
into ~/.ao and chmod it: the shell's history and the process list kept the token, and anyone on the machine could read
the file until the chmod. `ao telegram install`, `uninstall` and `status` refused on Windows alone, so on Linux they
wrote into a ~/Library/LaunchAgents that is not there and asked for launchctl. `ao pings` printed the whole ping URL,
whose UUID is all it takes to ping the check. `.ao/a2a-agents.json` held an agent's Authorization header in a file a
commit takes. And `ao handoff` named its note by the minute and nothing else, so a second handoff in the same minute
was written over the first.

ao asks for the bot token at the terminal and does not show it, finds the chats that wrote to the bot with it, and
writes ~/.ao/telegram.json itself, 0600 from its creation; a script names the variable that holds the token. The
poller's commands refuse wherever there is no launchd, and say what keeps it running there. `ao pings` shows where
the pings go and the last four characters. A header in `.ao/a2a-agents.json` names the variable that holds its value,
and a credential written out there is refused. A handoff takes a name no mail had. Nothing here reaches Telegram, a
health check or an agent: every request is faked.
"""
import builtins
import getpass
import json
import os
import re
import stat
import sys
import warnings
from datetime import datetime
from types import SimpleNamespace

import pytest

from ao import a2a_mcp, cli, language, lib as A, telegram

TOKEN = "123456789:AAH-dqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
ADA = {"update_id": 7, "message": {"text": "hello", "chat": {"id": 42, "type": "private", "first_name": "Ada",
                                                             "username": "ada"}}}
STRANGER = {"update_id": 8, "message": {"text": "hi", "chat": {"id": -1001234, "type": "group",
                                                               "title": "Ops\x1b[31m room"}}}
# What the setup printed for a person to type or paste, with the token in it, or before the file was private.
OLD_STEPS = ("curl", "Invoke-RestMethod", "getUpdates", "echo '", "chmod 600", "mkdir -p")


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


@pytest.fixture
def conf(project, monkeypatch, tmp_path):
    """~/.ao/telegram.json of the project's own home, where no ~/.ao is yet."""
    path = tmp_path / "home" / ".ao" / "telegram.json"
    monkeypatch.setattr(telegram, "CONF", str(path))
    return path


@pytest.fixture
def telegram_api(monkeypatch):
    """Telegram, faked: the requests ao makes of it, and the updates it answers getUpdates with."""
    asked, updates = [], [ADA, STRANGER]

    def api(conf, method, **params):
        asked.append((conf.get("token"), method, params))
        assert method == "getUpdates", f"setup asked Telegram for {method}"
        return {"ok": True, "result": updates}

    monkeypatch.setattr(telegram, "api", api)
    return asked


def _at_a_terminal(monkeypatch, attached, token=None, typed=None):
    """A terminal attached or not; what a person types at the token's prompt and at the chats'."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: attached, raising=False)
    prompts = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt: prompts.append(prompt) or token if token is not None
                        else pytest.fail("the token was asked for"))
    monkeypatch.setattr(builtins, "input", lambda prompt: prompts.append(prompt) or typed if typed is not None
                        else pytest.fail("the chats were asked for"))
    return prompts


def _setup(project, **options):
    return cli.cmd_telegram(project, SimpleNamespace(action="setup", once=False, chat=options.get("chat"),
                                                     token_env=options.get("token_env")))


def _created(monkeypatch, directory):
    """Every file opened for creation under `directory`, with the mode it was created with."""
    opened, real = [], os.open

    def recording(path, flags, mode=0o777, *args, **kwargs):
        if str(path).startswith(str(directory)) and flags & os.O_CREAT:
            opened.append((os.path.basename(str(path)), mode))
        return real(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    return opened


# ---- ao telegram setup: the token asked unseen, the chats found with it, the file written by ao --------------

def test_the_setup_asks_for_the_token_unseen_and_writes_the_file_private_from_its_creation(project, conf,
                                                                                           telegram_api,
                                                                                           monkeypatch, capsys):
    prompts = _at_a_terminal(monkeypatch, True, token=f"  {TOKEN}\n", typed="42")
    created = _created(monkeypatch, conf.parent)

    assert _setup(project) == 0

    out = _plain(capsys)
    assert prompts == ["bot token (not shown): ", "chats it allows, their ids separated by spaces: "]
    assert [(token, method) for token, method, _ in telegram_api] == [(TOKEN, "getUpdates")]
    assert "            42  Ada (@ada)" in out and "      -1001234  Ops[31m room" in out   # no escape reaches it
    assert f"saved {conf} ({cli._private_mode()}): 1 chat(s) allowed" in out and "now: ao telegram test" in out
    assert TOKEN not in out and not [old for old in OLD_STEPS if old in out]
    assert json.loads(conf.read_text(encoding="utf-8")) == {"token": TOKEN, "chats": ["42"]}
    assert telegram.config() == {"token": TOKEN, "chats": ["42"]}
    if os.name == "nt":
        pytest.skip("Windows keeps no file mode: the file is as private as its folder (WINDOWS-SAY-WHY)")
    assert created and all(mode & 0o077 == 0 for _, mode in created), created
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600


def test_a_file_the_old_steps_left_readable_is_private_once_the_setup_writes_it(project, conf, telegram_api,
                                                                                 monkeypatch, capsys):
    conf.parent.mkdir(parents=True)
    conf.write_text(json.dumps({"token": "1:old", "chats": ["9"]}), encoding="utf-8")
    os.chmod(conf, 0o644)
    _at_a_terminal(monkeypatch, True, token=TOKEN, typed="42, -1001234")

    assert _setup(project) == 0

    assert json.loads(conf.read_text(encoding="utf-8")) == {"token": TOKEN, "chats": ["42", "-1001234"]}
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600


def test_without_a_terminal_the_setup_asks_nothing_and_says_how_a_script_names_the_token(project, conf, monkeypatch,
                                                                                         capsys):
    monkeypatch.setattr(telegram, "api", lambda *args, **kwargs: pytest.fail("Telegram was asked"))
    _at_a_terminal(monkeypatch, False)
    monkeypatch.setattr(cli, "_scheduler", lambda: "launchd")

    assert _setup(project) == 0
    out = _plain(capsys)
    kept = language.text(None, "private-file.kept-windows" if os.name == "nt" else "private-file.kept")
    assert "3. You give ao the token when it asks — a bot token is a credential: it is not shown" in out
    assert f"it writes {conf} itself, {kept}." in out
    assert "4. ao telegram test → ao telegram install" in out
    assert "No terminal is attached, so ao asks for nothing" in out and "--token-env NAME --chat ID" in out
    assert not [old for old in OLD_STEPS if old in out] and not conf.exists()

    assert _setup(project, chat=["42"]) == 2
    assert ("not saved: the bot token is asked at a terminal, and none is attached; --token-env names a variable "
            "that holds it") in _plain(capsys)
    assert _setup(project, chat=["42"], token_env="AO_TEST_TELEGRAM_TOKEN") == 2
    assert "not saved: $AO_TEST_TELEGRAM_TOKEN is not set" in _plain(capsys)
    assert not conf.exists()

    monkeypatch.setenv("AO_TEST_TELEGRAM_TOKEN", TOKEN)
    assert _setup(project, chat=["42", "42"], token_env="AO_TEST_TELEGRAM_TOKEN") == 0     # named: nothing is asked
    assert telegram.config() == {"token": TOKEN, "chats": ["42"]}
    assert TOKEN not in _plain(capsys)


def test_a_script_that_names_no_chat_is_told_the_chats_that_wrote_and_saves_nothing(project, conf, telegram_api,
                                                                                    monkeypatch, capsys):
    _at_a_terminal(monkeypatch, False)
    monkeypatch.setenv("AO_TEST_TELEGRAM_TOKEN", TOKEN)

    assert _setup(project, token_env="AO_TEST_TELEGRAM_TOKEN") == 2

    out = _plain(capsys)
    assert "chats that wrote to the bot:" in out and "42  Ada (@ada)" in out
    assert "not saved: name the chats it allows with --chat" in out and not conf.exists()


@pytest.mark.parametrize("token, typed, said", [
    ("", None, "not saved: no token was given"),
    ("not a token", None, "not saved: that is no bot token"),
    (f"{TOKEN} more", None, "not saved: that is no bot token"),
    (TOKEN, "", "not saved: no chat was allowed, and the allowlist is not optional"),
    (TOKEN, "42 @ada", "not saved: a chat id is a number, as Telegram gives it: @ada"),
])
def test_what_is_no_token_or_no_chat_is_refused_and_nothing_is_saved(project, conf, telegram_api, monkeypatch, capsys,
                                                                    token, typed, said):
    _at_a_terminal(monkeypatch, True, token=token, typed=typed)

    assert _setup(project) == 2

    assert said in _plain(capsys) and not conf.exists()
    if typed is None:
        assert telegram_api == []                      # what is no token is never put into a request's URL


def test_what_telegram_refuses_is_said_without_the_token(project, conf, monkeypatch, capsys):
    monkeypatch.setattr(telegram, "api", lambda conf, method, **params:
                        {"ok": False, "error": f"HTTP 401 for /bot{conf['token']}/getUpdates"})
    _at_a_terminal(monkeypatch, True, token=TOKEN)

    assert _setup(project) == 2

    out = _plain(capsys)
    assert "not saved: Telegram did not say which chats wrote to the bot (HTTP 401 for /bot<token>/getUpdates)" in out
    assert TOKEN not in out and not conf.exists()


# ---- the poller: a launchd job, refused wherever there is no launchd --------------------------------------------

@pytest.mark.parametrize("scheduler, said", [
    ("systemd", "Linux does not have; keep `ao telegram poll` running as a systemd user service instead"),
    ("schtasks", "Windows does not have; schedule `ao telegram poll` with Task Scheduler instead"),
])
def test_the_poller_is_refused_wherever_there_is_no_launchd(project, monkeypatch, capsys, scheduler, said):
    monkeypatch.setattr(cli, "_scheduler", lambda: scheduler)
    monkeypatch.setattr(A, "_run_program", lambda argv, *args, **kwargs: pytest.fail(f"{argv[0]} was asked"))
    monkeypatch.setattr(telegram, "config", lambda: {"token": TOKEN, "chats": ["42"]})

    for action in ("install", "uninstall", "status"):
        assert cli.cmd_telegram(project, SimpleNamespace(action=action, once=False)) == 1
        assert _plain(capsys) == f"refused: the telegram poller is scheduled with launchd, which {said}\n"
    assert not os.path.exists(os.path.join(A.HOME, "Library"))

    monkeypatch.setattr(telegram, "api", lambda *args, **kwargs: pytest.fail("Telegram was asked"))
    _at_a_terminal(monkeypatch, False)
    assert _setup(project) == 0
    assert "4. ao telegram test → ao telegram poll" in _plain(capsys)


# ---- ao pings: where the pings go, never the secret ---------------------------------------------------------------

@pytest.mark.parametrize("url, shown", [
    ("https://hc-ping.com/1f0e7c55-9a3b-4c7e-8d2a-6b5f4e3d2c1a", "https://…"),
    ("https://someone:pw@hc.example.com:8443/ping/1f0e7c55-9a3b-4c7e-8d2a-6b5f4e3d2c1a?rid=9", "https://…"),
    ("https://hc-ping.com/abc", "https://…"),
    ("hc-ping.com/1f0e7c55-9a3b-4c7e-8d2a-6b5f4e3d2c1a", "…"),
    ("https://hc-ping.com:port/1f0e7c55-9a3b-4c7e-8d2a-6b5f4e3d2c1a", "https://…"),
])
def test_ao_pings_shows_where_the_pings_go_and_never_the_secret(project, monkeypatch, tmp_path, capsys, url, shown):
    monkeypatch.setattr(A, "pings_path", lambda: str(tmp_path / "pings.json"))
    assert cli.cmd_pings(project, SimpleNamespace(action="status", url=None, all=False)) == 0
    assert _plain(capsys) == "ping url: not configured\n"

    A.set_ping_url(project["root"], url)
    assert cli.cmd_pings(project, SimpleNamespace(action="status", url=None, all=False)) == 0

    out = _plain(capsys)
    assert out == f"ping url: {shown}  (the rest is the secret, in {tmp_path / 'pings.json'})\n"
    assert "9a3b-4c7e" not in out and "someone" not in out and "pw@" not in out


# ---- .ao/a2a-agents.json: a header names the variable that holds it -----------------------------------------------

@pytest.fixture
def bridge(project, monkeypatch, tmp_path):
    """The A2A bridge in the project, its requests and card fetches faked: (write a registry, what was sent)."""
    sent = []

    def answered(url, headers, payload, timeout=60):
        sent.append(dict(headers))
        return {"jsonrpc": "2.0", "id": 1, "result": {"id": "t1", "status": {"state": "submitted"}}}

    def card(url, headers=None, timeout=20):
        sent.append(dict(headers or {}))
        return {"name": "weather"}, "1.0", url + "/.well-known/agent-card.json"

    monkeypatch.setattr(a2a_mcp, "_post", answered)
    monkeypatch.setattr(a2a_mcp, "fetch_card", card)
    monkeypatch.setattr(a2a_mcp, "ROOT", project["root"])

    def register(headers, where="project"):
        path = (os.path.join(project["root"], ".ao", "a2a-agents.json") if where == "project"
                else os.path.join(A.HOME, ".ao", "a2a-agents.json"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"weather": {"url": "http://127.0.0.1:9/a2a", "version": "1.0", "headers": headers}}, fh)
        return path

    return register, sent


def test_a_header_takes_its_value_from_the_variable_it_names(bridge, monkeypatch):
    register, sent = bridge
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"}, where="machine")      # the machine's file binds it
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}", "X-Tenant": "acme"})
    monkeypatch.setenv("AO_TEST_A2A_TOKEN", "from-the-env")

    view = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})
    listed = a2a_mcp.call("a2a_agents", {})

    assert view["id"] == "t1" and listed["agents"][0]["reachable"] is True
    assert [headers["Authorization"] for headers in sent] == ["Bearer from-the-env"] * 2
    assert sent[0]["X-Tenant"] == "acme"


def test_a_variable_that_is_not_set_is_named_and_nothing_is_sent(bridge, monkeypatch):
    register, sent = bridge
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"})
    monkeypatch.delenv("AO_TEST_A2A_TOKEN", raising=False)

    refused = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert refused == {"error": "agent weather: its Authorization header names ${AO_TEST_A2A_TOKEN}, which is not set "
                                "where the bridge runs: set it in the environment the MCP client starts the bridge "
                                "with"}
    assert sent == []


def test_a_credential_written_into_the_projects_file_is_refused_and_the_machines_file_may_hold_one(bridge,
                                                                                                    monkeypatch):
    register, sent = bridge
    project_file = register({"Authorization": "Bearer written-here"})

    refused = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})
    listed = a2a_mcp.call("a2a_agents", {})

    assert "written into .ao/a2a-agents.json, which a commit takes with the project" in refused["error"]
    assert "\"Bearer ${NAME}\"" in refused["error"] and "written-here" not in json.dumps([refused, listed])
    assert listed["agents"] == [{"id": "weather", "url": "http://127.0.0.1:9", "error": refused["error"],
                                 "reachable": False}]
    for header in ("X-Api-Key", "x-auth-token", "Cookie"):
        register({header: "written-here"})
        assert "error" in a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"}), header
    assert sent == []

    os.remove(project_file)
    register({"Authorization": "Bearer kept-in-home"}, where="machine")
    assert a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})["id"] == "t1"
    assert sent == [{"Authorization": "Bearer kept-in-home", "Content-Type": "application/a2a+json",
                     "A2A-Version": "1.0"}]


def test_a_value_with_a_line_break_is_refused_without_being_quoted(bridge, monkeypatch):
    register, sent = bridge
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"}, where="machine")
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"})
    monkeypatch.setenv("AO_TEST_A2A_TOKEN", "first\r\nX-Injected: second")

    refused = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert refused == {"error": "agent weather: its Authorization header holds a line break, which no header may"}
    assert sent == []


def test_a_variable_goes_only_to_an_origin_the_machines_file_sends_it_to(bridge, monkeypatch, project):
    """An agent can write .ao/a2a-agents.json: one that kept the header and named its own host had the bridge send
    the person's token there (LEADS-SECRETS)."""
    register, sent = bridge
    monkeypatch.setenv("AO_TEST_A2A_TOKEN", "the-persons")
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"})                       # no machine file binds it

    unbound = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert "sends to no agent at http://127.0.0.1:9" in unbound["error"] and sent == []
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"}, where="machine")
    path = os.path.join(project["root"], ".ao", "a2a-agents.json")
    with open(path, "w", encoding="utf-8") as fh:                                    # the agent moves the URL
        json.dump({"weather": {"url": "https://collector.example:8443/a2a", "version": "1.0",
                               "headers": {"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"}}}, fh)

    moved = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})
    listed = a2a_mcp.call("a2a_agents", {})

    assert "sends to no agent at https://collector.example:8443" in moved["error"]
    assert listed["agents"][0]["reachable"] is False and sent == []
    assert "the-persons" not in json.dumps([unbound, moved, listed])


def test_the_config_command_shows_a_header_naming_its_variable(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "ao_program", lambda: "ao")

    assert cli.cmd_a2a_mcp(project, SimpleNamespace(action="config")) == 0

    out = _plain(capsys)
    assert '"Authorization": "Bearer ${WEATHER_A2A_TOKEN}"' in out and "Bearer …" not in out
    assert "${NAME} is read from the environment the client starts the bridge with" in out


# ---- ao handoff: a second note in the same minute is a second note ------------------------------------------------

class _Now(datetime):
    """The clock `ao handoff` stamps its note with, stopped at one minute."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 12, 16, 0, 30)


def test_two_handoffs_in_one_minute_are_two_notes(project, monkeypatch):
    monkeypatch.setattr(cli, "datetime", _Now)
    monkeypatch.setattr(A, "busy", lambda *args, **kwargs: ("idle", None, ""))
    monkeypatch.setattr(A, "account_usage", lambda *args, **kwargs: None)
    box = os.path.join(project["root"], project["mailbox"])

    for reason in ("the quota ran out", "the quota ran out again"):
        assert cli.cmd_handoff(project, SimpleNamespace(reason=reason, no_send=True)) == 0

    names = A.mailbox(project["root"], project["mailbox"])
    assert sorted(names) == ["20261012-1600-fable-to-anyone-HANDOFF-2.md", "20261012-1600-fable-to-anyone-HANDOFF.md"]
    notes = {name: open(os.path.join(box, name), encoding="utf-8").read() for name in names}
    assert "**Reason:** the quota ran out\n" in notes["20261012-1600-fable-to-anyone-HANDOFF.md"]
    assert "**Reason:** the quota ran out again\n" in notes["20261012-1600-fable-to-anyone-HANDOFF-2.md"]
    assert [row["id"] for row in A.mail_log(project["root"], 200) if row.get("event") == "written"] == \
        ["20261012-1600-fable-to-anyone-HANDOFF.md", "20261012-1600-fable-to-anyone-HANDOFF-2.md"]


# ---- its second review: a prompt that cannot hide, a secret in a host, an agent that echoes its header -----------

def test_a_terminal_that_cannot_hide_what_is_typed_is_asked_no_token(project, conf, telegram_api, monkeypatch, capsys):
    """getpass reads the line echoed where it cannot turn echo off, and says so only in a warning (LEADS-SECRETS)."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    def echoed(prompt):
        warnings.warn("Can not control echo on the terminal.", getpass.GetPassWarning)
        return TOKEN

    monkeypatch.setattr(getpass, "getpass", echoed)

    assert cli._hidden_input("bot token (not shown): ") is None
    assert _setup(project) == 2

    out = _plain(capsys)
    assert "this terminal cannot hide what is typed" in out and "--token-env" in out
    assert not conf.exists() and telegram_api == []


@pytest.mark.parametrize("url, shown", [
    ("https://0f9e8d7c-6b5a-4c3d-2e1f-0a9b8c7d6e5f.checks.example/ping", "https://…"),
    ("https://0f9e8d7c-6b5a-4c3d-2e1f-0a9b8c7d6e5f.com/", "https://…"),
    ("https://deadmansecret.com", "https://…"),
    ("https://public.example/prefix/that/is/long/ABCD", "https://…"),
    ("http://10.0.0.7:8000/ping/0f9e8d7c-6b5a-4c3d-2e1f-0a9b8c7d6e5f", "http://…"),
])
def test_a_ping_url_shows_no_part_that_can_hold_the_checks_secret(url, shown):
    """The address is the secret, whatever part of it the check's UUID or name stands in (LEADS-SECRETS)."""
    assert cli._ping_url_shown(url) == shown
    assert "0f9e8d7c" not in shown and "deadman" not in shown and "ABCD" not in shown


def test_an_agent_that_echoes_the_credential_it_was_sent_has_it_answered_as_credential(bridge, monkeypatch, project):
    """What an agent answers is its own text: one that echoed its header had the bridge hand the person's token to
    the model that called it, and to the notice it records (LEADS-SECRETS)."""
    register, sent = bridge
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"}, where="machine")
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"})
    monkeypatch.setenv("AO_TEST_A2A_TOKEN", "the-token")
    answers = iter([
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "refused Bearer the-token"}},
        {"jsonrpc": "2.0", "id": 1, "result": {"id": "t-the-token", "status": {
            "state": "working", "message": {"parts": [{"text": "I was sent the-token"}]}}}},
    ])
    monkeypatch.setattr(a2a_mcp, "_post", lambda url, headers, payload, timeout=60: sent.append(headers) or next(answers))

    refused = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})
    taken = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert sent and all(headers["Authorization"] == "Bearer the-token" for headers in sent)
    answered = json.dumps([refused, taken])
    assert "the-token" not in answered and "[credential]" in answered
    notices = open(os.path.join(project["root"], ".ao", "ledger", "notices.jsonl"), encoding="utf-8").read()
    assert "the-token" not in notices


def test_a_credential_too_short_to_hide_is_sent_nowhere(bridge, monkeypatch):
    """An answer that echoed `key42` could not be told from ordinary text to be hidden (LEADS-SECRETS)."""
    register, sent = bridge
    register({"Authorization": "key42"}, where="machine")

    refused = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert "fewer than 8 characters" in refused["error"] and "key42" not in json.dumps(refused) and sent == []


@pytest.mark.parametrize("name, carries", [
    ("Authorization", True), ("Proxy-Authorization", True), ("X-Api-Key", True), ("x-auth-token", True),
    ("Cookie", True), ("PRIVATE-TOKEN", True), ("X-Session-Id", True),
    ("X-Monkey", False), ("X-Tenant", False), ("Accept", False), ("X-Keyboard-Layout", False),
])
def test_a_header_carries_a_credential_by_the_words_of_its_name(name, carries):
    assert a2a_mcp.credential_header(name) is carries


def test_a_header_whose_name_only_holds_a_credential_word_is_sent(bridge):
    register, sent = bridge
    register({"X-Monkey": "penguins"})

    assert a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})["id"] == "t1"
    assert sent[0]["X-Monkey"] == "penguins"


def test_a_short_value_a_variable_holds_is_sent_in_no_header(bridge, monkeypatch):
    """A variable holds a credential whatever its header is called; one too short to hide was sent in a header
    whose name said nothing of a credential, and an echo of it came back unhidden (LEADS-SECRETS)."""
    register, sent = bridge
    register({"X-Partner": "${AO_TEST_A2A_TOKEN}"}, where="machine")
    register({"X-Partner": "${AO_TEST_A2A_TOKEN}"})
    monkeypatch.setenv("AO_TEST_A2A_TOKEN", "key42")

    refused = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert "fewer than 8 characters" in refused["error"] and "key42" not in json.dumps(refused) and sent == []


def test_the_answer_is_hidden_by_the_registry_the_request_was_made_from(bridge, monkeypatch, project):
    """Read twice, the registry could change between the request and the reading of what to hide (LEADS-SECRETS)."""
    register, sent = bridge
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"}, where="machine")
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"})
    monkeypatch.setenv("AO_TEST_A2A_TOKEN", "the-token")
    reads = []
    real = a2a_mcp.registry
    monkeypatch.setattr(a2a_mcp, "registry", lambda: reads.append(1) or real())
    monkeypatch.setattr(a2a_mcp, "_post", lambda url, headers, payload, timeout=60: sent.append(headers) or {
        "jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "refused Bearer the-token"}})

    answered = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert reads == [1] and "the-token" not in json.dumps(answered)


def test_each_part_of_a_structured_credential_is_hidden_where_an_agent_echoes_it_alone(bridge, monkeypatch):
    """`Cookie: sid=...` was hidden whole and by its last word: an agent that echoed the session id alone handed it
    to the model (LEADS-SECRETS)."""
    register, sent = bridge
    register({"Cookie": "sid=session-one; csrf=crosssite-two"}, where="machine")
    monkeypatch.setattr(a2a_mcp, "_post", lambda url, headers, payload, timeout=60: sent.append(headers) or {
        "jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "bad session-one, bad crosssite-two"}})

    answered = json.dumps(a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"}))

    assert sent and "session-one" not in answered and "crosssite-two" not in answered


def test_a_project_registry_gone_after_its_reading_is_still_judged_the_projects(bridge, monkeypatch):
    """Told apart by a second look, a project file renamed away after it was read was taken for the machine's, and
    its URL sent the person's variable with no origin bound to it (LEADS-SECRETS)."""
    register, sent = bridge
    register({"Authorization": "Bearer ${AO_TEST_A2A_TOKEN}"})          # no machine file binds the variable
    monkeypatch.setenv("AO_TEST_A2A_TOKEN", "the-persons")
    monkeypatch.setattr(a2a_mcp.os.path, "exists", lambda path: False)   # gone by the time anything looks again

    refused = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert "sends to no agent at" in refused["error"] and sent == []


def test_an_agents_url_is_answered_with_no_credential_it_carries(bridge, monkeypatch, project):
    register, sent = bridge
    path = os.path.join(A.HOME, ".ao", "a2a-agents.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"weather": {"url": "https://user:pass-word-9@agent.example:8443/a2a/path-value-2?code=query-value-1#x",
                               "version": "1.0"}}, fh)

    listed = a2a_mcp.call("a2a_agents", {})

    answered = json.dumps(listed)
    assert listed["agents"][0]["url"] == "https://agent.example:8443"
    assert "query-value-1" not in answered and "pass-word-9" not in answered and "code=" not in answered
    assert "path-value-2" not in answered


@pytest.mark.parametrize("value, parts", [
    ("Bearer a-long-token", ["a-long-token"]), ("sid=session-one; csrf=crosssite-two", ["session-one", "crosssite-two"]),
    ("Basic dXNlcjpwYXNzMQ==", ["dXNlcjpwYXNzMQ=="]), ("sid=x; csrf=long-secret-1", ["x", "long-secret-1"]),
])
def test_the_secret_parts_of_a_credential_are_its_values_never_its_names(value, parts):
    assert a2a_mcp.secret_parts(value) == parts


def test_a_cookie_with_a_short_value_is_sent_nowhere(bridge):
    """`sid=x; csrf=long-secret` passed on its last word, and an echo of `x` could not be hidden (LEADS-SECRETS)."""
    register, sent = bridge
    register({"Cookie": "sid=x; csrf=long-secret-1"}, where="machine")

    refused = a2a_mcp.call("a2a_send", {"agent": "weather", "message": "hello"})

    assert "fewer than 8 characters" in refused["error"] and sent == []
