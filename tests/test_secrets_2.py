"""A ping URL typed nowhere, a new bot's first messages read, and no mail written over another (SECRETS-2).

LEADS-SECRETS left four things open on 2026-10-13. `ao pings setup --url https://hc-ping.com/<uuid>` had a person type
the ping URL on a command line, which the shell's history and the process list keep, and whoever reads the check's UUID
can ping it, so a dead watchdog looks alive. A new Telegram bot token kept the old bot's ~/.ao/telegram-offset: the
poller asked the new bot for its updates from the old bot's number on, Telegram takes every update below the number
asked from as read, and a person's first messages to the new bot were dropped unread. `write_mail` opened its file with
"w": a second `ao note`, `ao mail send`, `ao_report` or hold release of the same minute and subject was written over the
first, two writers that each found a name free - a handoff's look at the mailbox among them - both wrote to it, and a
link left at a mail's name was written through. And `ao doctor` hinted `ao ping set <url>`, a command that never was.

`ao pings setup` asks for the URL at the terminal and does not show it, takes it from the variable a script names, and
refuses one given on the command line. A token of another bot drops the offset file, and nothing beside it. Every mail
is created under a name no file has, `-2` after its own when that is taken, and its writer says the name it was written
under. The doctor hints `ao pings setup`. Nothing here reaches healthchecks or Telegram: every request is faked.
"""
import builtins
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, mcp, telegram
from tests.test_bug_hunter import _repo, _stand_in
from tests.test_docs_commands import _commands, _refusals

URL = "https://hc-ping.com/1f0e7c55-9a3b-4c7e-8d2a-6b5f4e3d2c1a"
SECRET = "9a3b-4c7e"                                   # the middle of the UUID, which nothing may print
OLD_TOKEN = "123456789:AAH-dqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
NEW_TOKEN = "987654321:BBG-xvPdmBH2wFKIyeTdpgRBt1L6QBMEtbx"
NAME = "20261013-1000-fable-to-kiro-NOTE-the-parser.md"
SECOND = "20261013-1000-fable-to-kiro-NOTE-the-parser-2.md"


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


# ---- ao pings setup: the URL asked unseen, or named by its variable, never typed on a command line ---------------

def _at_a_terminal(monkeypatch, attached, typed=None):
    """A terminal attached or not, and what a person pastes at the URL's prompt: [the prompts asked]."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: attached, raising=False)
    prompts = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt: prompts.append(prompt) or typed if typed is not None
                        else pytest.fail("the ping URL was asked for"))
    return prompts


def _setup(project, **options):
    return cli.cmd_pings(project, SimpleNamespace(action="setup", url=options.get("url"),
                                                 url_env=options.get("url_env"), all=options.get("all", False)))


def _saved():
    try:
        with open(A.pings_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def test_at_a_terminal_the_setup_asks_for_the_ping_url_unseen_and_saves_it(project, monkeypatch, capsys):
    prompts = _at_a_terminal(monkeypatch, True, typed=f"  {URL}\n")

    assert _setup(project) == 0

    out = _plain(capsys)
    assert prompts == ["ping URL (not shown): "]
    assert "2. Copy its ping URL and paste it when ao asks. It is not shown" in out
    assert f"saved {A.pings_path()} ({cli._private_mode()}): this project pings https://…" in out
    assert "now: ao pings test" in out
    assert "--url https" not in out and SECRET not in out
    assert A.ping_url(project["root"]) == URL and _saved() == {A.project_key(project["root"]): URL}


def test_a_script_names_the_variable_that_holds_the_url(project, monkeypatch, capsys):
    _at_a_terminal(monkeypatch, False)
    monkeypatch.delenv("AO_TEST_PING_URL", raising=False)

    assert _setup(project, url_env="AO_TEST_PING_URL") == 2
    assert _plain(capsys) == "not saved: $AO_TEST_PING_URL is not set\n"
    monkeypatch.setenv("AO_TEST_PING_URL", "")
    assert _setup(project, url_env="AO_TEST_PING_URL") == 2
    assert _plain(capsys) == "not saved: $AO_TEST_PING_URL is empty\n"
    assert _saved() is None

    monkeypatch.setenv("AO_TEST_PING_URL", URL)
    assert _setup(project, url_env="AO_TEST_PING_URL", all=True) == 0          # named: nothing is asked

    out = _plain(capsys)
    assert out.startswith(f"saved {A.pings_path()} ") and "every project with no URL of its own pings" in out
    assert SECRET not in out and _saved() == {"*": URL}


def test_without_a_terminal_or_a_variable_the_setup_refuses_and_says_both_ways(project, monkeypatch, capsys):
    _at_a_terminal(monkeypatch, False)

    assert _setup(project) == 2

    out = _plain(capsys)
    assert "1. Create a check at https://healthchecks.io" in out
    assert out.endswith("not saved: the ping URL is asked at a terminal, and none is attached; run ao pings setup in "
                        "a terminal of yours, or name the variable that holds it with --url-env NAME\n")
    assert _saved() is None


def test_a_url_given_on_the_command_line_is_refused_unsaved_and_unquoted(project, monkeypatch, capsys):
    _at_a_terminal(monkeypatch, True)                    # at a terminal too: nothing is asked in its place
    monkeypatch.setenv("AO_TEST_PING_URL", URL)
    parsed = cli.build_parser().parse_args(["pings", "setup", "--url", URL, "--all"])

    assert parsed.fn(project, parsed) == 2
    assert _setup(project, url=URL, url_env="AO_TEST_PING_URL") == 2

    out = _plain(capsys)
    assert out == 2 * ("not saved: a ping URL on a command line stays in the shell's history and the process list, "
                       "where whoever reads it can ping the check; run ao pings setup to paste it unseen, or name "
                       "the variable that holds it with --url-env NAME\n")
    assert _saved() is None
    named = cli.build_parser().parse_args(["pings", "setup", "--url-env", "AO_TEST_PING_URL", "--all"])
    assert (named.url, named.url_env, named.all) == (None, "AO_TEST_PING_URL", True)


@pytest.mark.parametrize("typed, said", [
    ("", "not saved: no ping URL was given"),
    ("   \n", "not saved: no ping URL was given"),
    ("hc-ping.com/1f0e7c55-9a3b-4c7e-8d2a-6b5f4e3d2c1a", "not saved: that is no ping URL"),
    ("file:///etc/9a3b-4c7e", "not saved: that is no ping URL"),
    ("ftp://hc-ping.com/1f0e7c55-9a3b-4c7e-8d2a-6b5f4e3d2c1a", "not saved: that is no ping URL"),
    ("https://", "not saved: that is no ping URL"),
    ("https://hc-ping.com:port/1f0e7c55-9a3b-4c7e-8d2a-6b5f4e3d2c1a", "not saved: that is no ping URL"),
    ("https://hc-ping.com/1f0e7c55 9a3b-4c7e", "not saved: that is no ping URL"),
    ("https://hc-ping.com/\x1b[31m9a3b-4c7e", "not saved: that is no ping URL"),
])
def test_what_is_no_ping_url_is_refused_unsaved_and_unquoted(project, monkeypatch, capsys, typed, said):
    _at_a_terminal(monkeypatch, True, typed=typed)

    assert _setup(project) == 2

    out = _plain(capsys)
    assert said in out and SECRET not in out and _saved() is None


# ---- a new bot token: the old bot's place in the messages goes, and nothing beside it ------------------------------

@pytest.fixture
def conf(project, monkeypatch, tmp_path):
    """~/.ao/telegram.json of the project's own home; the poller's offset file is beside it."""
    path = tmp_path / "home" / ".ao" / "telegram.json"
    monkeypatch.setattr(telegram, "CONF", str(path))
    return path


def test_a_new_token_drops_the_old_bots_offset_and_nothing_beside_it(conf):
    telegram.save(OLD_TOKEN, ["42"])
    offset = conf.parent / "telegram-offset"
    assert str(offset) == telegram._offset_path()
    offset.write_text("1001", encoding="utf-8")
    beside = {name: f"{name}'s own\n" for name in ("telegram-offset.bak", "telegram-log-proj", "pings.json")}
    for name, text in beside.items():
        (conf.parent / name).write_text(text, encoding="utf-8")

    telegram.save(OLD_TOKEN, ["42", "-1001234"])                # the same bot, another chat: its place is kept
    assert offset.read_text(encoding="utf-8") == "1001"

    telegram.save(NEW_TOKEN, ["42"])
    assert not offset.exists()
    assert {name: (conf.parent / name).read_text(encoding="utf-8") for name in beside} == beside
    assert telegram.config() == {"token": NEW_TOKEN, "chats": ["42"]}
    telegram.save("1:another", ["42"])                         # with no offset to drop, nothing to say
    assert telegram.config()["token"] == "1:another"


def test_after_a_new_token_the_poller_reads_the_new_bots_first_messages(project, conf, monkeypatch, capsys):
    """Telegram, faked as it answers: from the update the poller asks from on, and what is below it confirmed
    read and forgotten."""
    held = {OLD_TOKEN: [{"update_id": 1000, "message": {"text": "old bot", "chat": {"id": 42}}}],
            NEW_TOKEN: [{"update_id": 7, "message": {"text": "stop the deploy", "chat": {"id": 42}}}]}

    def api(conf, method, **params):
        if method != "getUpdates":
            return {"ok": True}
        held[conf["token"]] = [u for u in held[conf["token"]] if u["update_id"] >= (params.get("offset") or 0)]
        return {"ok": True, "result": list(held[conf["token"]])}

    monkeypatch.setattr(telegram, "api", api)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    telegram.save(OLD_TOKEN, ["42"])
    assert len(telegram.poll(project["root"], project)["written"]) == 1
    assert open(telegram._offset_path(), encoding="utf-8").read() == "1001"

    monkeypatch.setenv("AO_TEST_TELEGRAM_TOKEN", NEW_TOKEN)
    assert cli.cmd_telegram(project, SimpleNamespace(action="setup", once=False, chat=["42"],
                                                     token_env="AO_TEST_TELEGRAM_TOKEN")) == 0

    [name] = telegram.poll(project["root"], project)["written"]
    assert "stop the deploy" in open(os.path.join(project["root"], project["mailbox"], name), encoding="utf-8").read()


# ---- write_mail: every mail created under a name no file has -------------------------------------------------------

def _store(project, mode):
    """The project's config with its mail kept by deletion or in the append-only store."""
    root = project["root"]
    stored = {key: value for key, value in project.items() if key != "root"}
    stored["mail"] = {"store": mode}
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    return A.load_config(root)


def _read(project, name):
    with open(os.path.join(project["root"], project["mailbox"], name), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("mode", ["deletion", "append-only"])
def test_two_mails_of_one_name_in_one_minute_are_two_mails(project, mode):
    root, cfg = project["root"], _store(project, mode)
    envelope = {"kind": "note", "from": "fable", "to": "kiro"}

    assert A.write_mail(root, cfg, NAME, "# the first\n", envelope) == NAME
    assert A.write_mail(root, cfg, NAME, "# the second\n", envelope) == SECOND

    assert A.mailbox(root, "agent-mail") == sorted([NAME, SECOND])
    assert "# the first" in _read(project, NAME) and "# the second" in _read(project, SECOND)
    assert [A.mail_meta(os.path.join(root, "agent-mail", name))["id"] for name in (NAME, SECOND)] == [NAME, SECOND]
    assert [row["id"] for row in A.mail_log(root) if row["event"] == "written"] == [NAME, SECOND]
    if mode == "append-only":
        assert b"# the second" in A.message_body(root, SECOND) and b"# the first" in A.message_body(root, NAME)


def test_a_link_left_at_a_mails_name_is_not_written_through(project, tmp_path):
    root = project["root"]
    target = tmp_path / "outside" / "authorized_keys"
    target.parent.mkdir()
    try:
        os.symlink(str(target), os.path.join(root, "agent-mail", NAME))
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symbolic link")

    assert A.write_mail(root, project, NAME, "# the note\n", {"kind": "note"}) == SECOND

    assert not target.exists() and os.path.islink(os.path.join(root, "agent-mail", NAME))
    assert "# the note" in _read(project, SECOND)


class _Now(datetime):
    """The clock `ao handoff` stamps its note with, stopped at one minute."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 13, 10, 0, 30)


def _handoff_quietly(monkeypatch):
    monkeypatch.setattr(cli, "datetime", _Now)
    monkeypatch.setattr(A, "busy", lambda *args, **kwargs: ("idle", None, ""))
    monkeypatch.setattr(A, "account_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram, "send", lambda *args, **kwargs: 0)


@pytest.mark.parametrize("writer", ["handoff", "pr"])
def test_a_taken_suffix_advances_the_original_stem(project, monkeypatch, capsys, writer):
    """A base name exists, then another writer takes -2 at exclusive creation: this mail must be -3 (SECRETS-2)."""
    _handoff_quietly(monkeypatch)
    if writer == "handoff":
        name = "20261013-1000-fable-to-anyone-HANDOFF.md"
    else:
        stamp = time.strftime("%Y%m%d-%H%M")
        name = f"{stamp}-pr-watch-to-kiro-PR-7-conflict-{'a' * 12}.md"
    second, third = name[:-3] + "-2.md", name[:-3] + "-3.md"
    box = os.path.join(project["root"], project["mailbox"])
    with open(os.path.join(box, name), "x", encoding="utf-8") as fh:
        fh.write("# first mail\n")
    original_open, raced = builtins.open, []

    def racing(path, mode="r", *args, **kwargs):
        if os.fspath(path) == os.path.join(box, second) and mode == "x" and not raced:
            raced.append(second)
            with original_open(path, "x", encoding="utf-8") as fh:
                fh.write("# racing mail\n")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", racing)
    if writer == "handoff":
        assert cli.cmd_handoff(project, SimpleNamespace(reason="the quota ran out", no_send=False)) == 0
        assert f"saved to {os.path.join(project['mailbox'], third)}" in _plain(capsys)
        assert "**Reason:** the quota ran out" in _read(project, third)
    else:
        pr = {"number": 7, "headRefOid": "a" * 40}
        assert A.write_pr_mail(project["root"], project, pr, "conflict", []) == third
        assert "# Pull request #7" in _read(project, third)
    assert raced == [second]
    assert A.mailbox(project["root"], project["mailbox"]) == sorted([name, second, third])
    assert _read(project, name) == "# first mail\n"
    assert _read(project, second) == "# racing mail\n"
    assert A.mail_meta(os.path.join(box, third))["id"] == third
    assert [row["id"] for row in A.mail_log(project["root"]) if row["event"] == "written"] == [third]


# ---- each writer says the name its mail was written under ----------------------------------------------------------

ANOTHERS = "# another writer's mail\n"


@pytest.fixture
def taken(monkeypatch):
    """Another writer's mail at the name each writer gives write_mail, made just before it writes: [those names]."""
    names, write = [], A.write_mail

    def racing(root, cfg, name, body, meta=None):
        with open(os.path.join(root, cfg.get("mailbox", "agent-mail"), name), "x", encoding="utf-8") as fh:
            fh.write(ANOTHERS)
        names.append(name)
        return write(root, cfg, name, body, meta)

    monkeypatch.setattr(A, "write_mail", racing)
    return names


def _last_word(capsys):
    """The name a command printed last, past the mailbox's folder."""
    return re.split(r"[\\/]", _plain(capsys).strip().split("\n")[-1].split()[-1])[-1]


def _mail_send(project, monkeypatch, capsys):
    cli.cmd_mail(project, SimpleNamespace(action="send", type="INFO", topic="the parser", body="it moved",
                                          mail_class=None))
    return [_last_word(capsys)]


def _note(project, monkeypatch, capsys):
    assert cli.cmd_note(project, SimpleNamespace(title="the parser moved", body="see the diff", stdin=False, to=None,
                                                 urgent=False)) == 0
    return [_last_word(capsys)]


def _hold_release(project, monkeypatch, capsys):
    with open(os.path.join(project["root"], A.HOLD_FILE), "w", encoding="utf-8") as fh:
        json.dump({"by": "a person", "reason": "editing", "at": int(time.time()), "stopped": []}, fh)
    assert cli.cmd_hold(project, SimpleNamespace(action="release", note="the parser moved", by=None, reason=None,
                                                 grace=5)) == 0
    return [_last_word(capsys)]


def _handoff(project, monkeypatch, capsys):
    _handoff_quietly(monkeypatch)
    assert cli.cmd_handoff(project, SimpleNamespace(reason="the quota ran out", no_send=False)) == 0
    return [_last_word(capsys)]


def _report(project, monkeypatch, capsys):
    return [mcp.call("ao_report", {"kind": "status", "summary": "the parser moved"}, project, False)["written"]]


def _telegram(project, monkeypatch, capsys):
    replies = []
    monkeypatch.setattr(telegram, "config", lambda: {"token": "t", "chats": ["42"]})
    monkeypatch.setattr(telegram, "_offset_path", lambda: os.path.join(A.HOME, "telegram-offset"))
    monkeypatch.setattr(telegram, "api", lambda conf, method, **params: replies.append(params.get("text")) or {
        "ok": True, "result": [{"update_id": 7, "message": {"text": "stop the deploy", "chat": {"id": 42}}}]})
    [written] = telegram.poll(project["root"], project)["written"]
    [said] = [re.search(r"`(.+?)`", text).group(1) for text in replies if text]
    return [written, said]


def _hunt(project, monkeypatch, capsys):
    _repo(project["root"])
    _stand_in(monkeypatch)
    out = "- [clock] src/a.py:3 parse_reset — a reset named for tomorrow is read as today\n"
    monkeypatch.setattr(cli, "_run_reviewer", lambda *args, **kwargs: {"ok": True, "out": out})
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-B", "-c", "pass", "{prompt}"]})
    assert cli.cmd_hunt(cfg, SimpleNamespace(action="run", fingerprint=None)) == 0
    return [row["mail"] for row in A.hunter_rows(project["root"]) if row.get("event") == "sent"]


@pytest.mark.parametrize("writer", [_mail_send, _note, _hold_release, _handoff, _report, _telegram, _hunt],
                         ids=["mail send", "note", "hold release", "handoff", "ao_report", "telegram", "hunt"])
def test_each_writer_says_the_name_its_mail_was_written_under(project, monkeypatch, capsys, taken, writer):
    said = writer(project, monkeypatch, capsys)

    [given] = taken
    written = given[:-len(".md")] + "-2.md"
    assert said and set(said) == {written}
    assert _read(project, given) == ANOTHERS and _read(project, written) != ANOTHERS
    assert A.mail_meta(os.path.join(project["root"], project["mailbox"], written))["id"] == written


@pytest.mark.parametrize("mode", ["deletion", "append-only"])
@pytest.mark.parametrize("writer", [_mail_send, _note, _report], ids=["mail send", "note", "ao_report"])
def test_a_same_minute_mail_after_ack_keeps_a_new_id(project, monkeypatch, capsys, mode, writer):
    """Acknowledging the first mail removes its view, but never makes its ID reusable (SECRETS-2)."""
    from tests.test_message_store import _ack
    cfg = _store(project, mode)
    root = cfg["root"]
    monkeypatch.setattr(cli, "datetime", _Now)
    strftime = time.strftime
    monkeypatch.setattr(time, "strftime", lambda fmt, *args: "20261013-1000" if fmt == "%Y%m%d-%H%M"
                        else strftime(fmt, *args))
    [first] = writer(cfg, monkeypatch, capsys)
    A.mail_seen(root, [first], "kiro")
    _ack(cfg, first)
    _plain(capsys)
    assert not os.path.exists(os.path.join(root, cfg["mailbox"], first))

    [second] = writer(cfg, monkeypatch, capsys)

    assert second == first[:-3] + "-2.md"
    assert second in {row["id"] for row in A.unseen_messages(root, cfg)}
    assert A.mail_meta(os.path.join(root, cfg["mailbox"], second))["id"] == second
    assert [row["id"] for row in A.mail_log(root) if row["event"] == "written"] == [first, second]
    if mode == "append-only":
        A.sync_mail_view(root, cfg)
        assert second in A.unhandled_messages(root)
        assert A.message_body(root, first) and A.message_body(root, second)
    assert os.path.isfile(os.path.join(root, cfg["mailbox"], second))


# ---- ao doctor: a hint names a command there is --------------------------------------------------------------------

def test_every_command_the_doctor_hints_for_an_optional_feature_is_one_the_parser_accepts(project, monkeypatch):
    from ao import email
    monkeypatch.setattr(email, "config", lambda: None)
    monkeypatch.setattr(telegram, "config", lambda: None)
    parser = cli.build_parser()

    hints = {name: hint for name, _, hint in cli._optional_features(project)}

    assert hints["ping"] == "ao pings setup"
    refused = {hint: _refusals(parser, words[1:]) for hint in hints.values() if hint.startswith("ao ")
               for _, words in _commands(hint, synopsis=True)}
    assert {hint: problems for hint, problems in refused.items() if problems} == {}
