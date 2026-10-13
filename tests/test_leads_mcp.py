"""The bug hunter's leads on the MCP server and on what ao prints, each held here (LEADS-MCP).

ao's read-only bug hunter read src/ao on 2026-10-12 and named five leads. `ao_ack` took any name in the mailbox, so
an implementer consumed the architect's reports, the hunter's leads and the watchdog's anomalies: deleted, or, with
`mail.store` append-only, recorded handled - which the store keeps from the actor deciding what was handled precisely
so that it cannot erase the question. A line on the server's standard input that was JSON but no object reached
`.get` outside its handler and ended the server, as it had ended the A2A bridge; so did a number past Python's 4300
digits and nesting deeper than the parser recurses. `ao agents` printed what a hook's command line and input said,
the status panel, the board and the mail what agents wrote, and `ao harness probe` what a vendor's harness answered,
each as it was: an escape sequence acted on the terminal of whoever read it, and a line feed wrote a line that read as
ao's. `ao harness seen` put the name it was given into the commands it prints to copy without quoting it, so a name
with a space broke the command and a `;` ran one of its own. The fifth lead, a server started with no role or one it
does not know, is answered in the slice's report: an unknown role is refused by HUNT-LEADS-SEC, and a server with no
role keeps every tool, the architect's - with `ao_ack` held to the implementer's mail whatever the role.
"""
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import acp, cli, lib as A, mcp

INVALID = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "a request is a JSON object"}}
PING = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})
PONG = {"jsonrpc": "2.0", "id": 7, "result": {}}
# What a terminal acts on. A file's name holds a direction override on every platform; Windows refuses a control
# character in one, so those go only where no name is made of them.
ACTED_ON = ("\x1b", "\x07", "\r", "‮")


def _serve(monkeypatch, capsys, root, lines, *flags):
    """Run `ao -C <root> mcp serve <flags>` over `lines` on its standard input: every reply it wrote, in order."""
    monkeypatch.setattr(sys, "argv", list(sys.argv))            # `ao mcp serve` rewrites it for the server
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(line + "\n" for line in lines)))
    capsys.readouterr()
    assert cli.main(["-C", root, "mcp", "serve", *flags]) == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def _mail_store(project, store):
    """The fixture's project with `mail.store` set to `store`, as ao loads it."""
    root = project["root"]
    stored = {key: value for key, value in project.items() if key != "root"}
    stored["mail"] = {"store": store}
    Path(root, ".ao", "config.json").write_text(json.dumps(stored), encoding="utf-8")
    return A.load_config(root)


def _acted_on(text):
    return [char for char in ACTED_ON if char in text]


# ---- the MCP server ------------------------------------------------------------------------------

@pytest.mark.parametrize("store", ["deletion", "append-only"])
@pytest.mark.parametrize("role", ["implementer", None], ids=["the implementer's server", "a server with no role"])
def test_ao_ack_takes_only_mail_addressed_to_the_implementer(project, store, role):
    root = project["root"]
    cfg = _mail_store(project, store)
    implementer, architect = A.mail_names(cfg)
    others = sorted([f"20261012-0900-{implementer}-to-{architect}-REPORT-done.md",
                     f"20261012-0901-hunter-to-{architect}-LEADS-src.md",
                     f"watchdog-to-{architect}-ANOMALY-idle.md"])
    mine = f"20261012-0902-{architect}-to-{implementer}-DECISION-go.md"
    for name in others + [mine]:
        A.write_mail(root, cfg, name, f"# {name}\n\nApply it, then acknowledge it.\n", {"kind": "note"})

    assert [message["id"] for message in mcp.call("ao_inbox", {}, cfg, False, role)["messages"]] == [mine]
    for name in others:
        answer = mcp.call("ao_ack", {"id": name, "outcome": "applied"}, cfg, False, role)
        assert list(answer) == ["error"] and answer["error"].startswith(f"{name} is not addressed to the implementer")
    assert mcp.call("ao_ack", {"id": mine}, cfg, False, role) == {"acknowledged": mine, "outcome": "applied"}

    assert A.mailbox(root, "agent-mail") == others                      # each still waits for its own reader
    assert all(Path(root, "agent-mail", name).exists() for name in others)
    assert {row.get("id") for row in A.mail_log(root) if row.get("event") == "consumed"} == {mine}
    if store == "append-only":
        assert A.unhandled_messages(root) == others                     # no handled record erased the question


def test_a_line_that_is_no_object_is_answered_as_invalid_and_the_server_goes_on(project, monkeypatch, capsys):
    lines = ["[]", "[1, 2]", "1", '"ao_status"', "null", "true"]

    replies = _serve(monkeypatch, capsys, project["root"], lines + [PING], "--role", "implementer")

    assert replies == [INVALID] * len(lines) + [PONG]


def test_a_line_the_parser_cannot_hold_is_passed_over_and_the_server_goes_on(project, monkeypatch, capsys):
    depth = 1_000_000                       # past what the parser recurses on every Python ao runs on
    nested = ('{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": ' + "[" * depth + "]" * depth + "}}")
    # More than 4300 digits, which Python will not read as a number, and no id: answered by nothing either way.
    huge = '{"jsonrpc": "2.0", "method": "ping", "params": {"agents": ' + "7" * 5000 + "}}"

    replies = _serve(monkeypatch, capsys, project["root"], [nested, huge, PING], "--role", "implementer")

    assert replies[-1] == PONG
    assert all(reply == {"jsonrpc": "2.0", "id": 1, "result": {}} for reply in replies[:-1])


# ---- what ao prints ---------------------------------------------------------------------------------

def test_ao_agents_shows_what_a_hook_said_escaped(project, capsys, monkeypatch):
    """The hook is a command any program on the machine can run, with whatever command line and input it likes."""
    monkeypatch.setenv("NO_COLOR", "1")
    A.emit_event(project["root"], "agent-prompt", {"harness": "claude\x1b]0;pwned\x07",
                                                   "session": "s-1\r\n  idle  forged  s-2"})

    assert cli.cmd_agents(project, None) == 0

    out = capsys.readouterr().out
    assert _acted_on(out) == [] and len(out.splitlines()) == 1         # no line of its own: one session, one row
    assert "claude\\x1b]0;pwned\\x07" in out and "s-1\\x0d\\x0a  idle  forged  s-2" in out


def test_harness_probe_shows_what_a_harness_answered_escaped(project, capsys, monkeypatch):
    """What a harness answers is a vendor's output. Only an adapter the package ships is asked (PROBE-SHIPPED), so
    the two stand in as shipped ones; an id a layer declares is shown escaped where it is named (test_acp_probe)."""
    monkeypatch.setenv("NO_COLOR", "1")
    answers, refuses = "fake", "late"
    shipped = dict(A.package_adapters(), **{ident: {
        "id": ident, "name": name, "verified": "untested", "contract": A.ADAPTER_CONTRACT,
        "send": {"argv": ["x", "{prompt}"]}, "acp": {"argv": ["fake-agent", name], "measured": "never"}}
        for name, ident in (("answers", answers), ("refuses", refuses))})
    monkeypatch.setattr(A, "package_adapters", lambda: shipped)
    monkeypatch.setattr(A, "resolve_binary", lambda name, path=None: ("/no/such/fake-agent", "1.0"))

    def probe(argv, cwd, timeout=None, sessions=False, env=None):          # no harness is started
        if argv[-1] == "refuses":
            raise acp.ProbeError("initialize was refused: \x1b[2Jgo away")
        return {"protocol_version": "1\x1b[8m", "agent": {"name": "agent\x1b]0;pwned\x07", "version": 7},
                "load_session": False, "session": ["list\x1b[31m"], "prompt": [], "mcp": [], "auth": []}

    monkeypatch.setattr(acp, "probe", probe)

    code = cli.cmd_harness(project, cli.build_parser().parse_args(["harness", "probe", answers, refuses]))

    out = capsys.readouterr().out
    assert code == 1 and _acted_on(out) == []                          # one did not answer, and nothing acts
    assert "fake         ACP 1\\x1b[8m  agent\\x1b]0;pwned\\x07 7" in out
    assert "session: list\\x1b[31m" in out
    assert "late         no answer: initialize was refused: \\x1b[2Jgo away" in out


def test_the_status_panel_the_board_and_the_mail_show_what_agents_wrote_escaped(project, capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(A, "quota", lambda adapter, ttl=300: ["claude: 5h window\x1b[2J 40%"])   # no real keyflip
    root = project["root"]
    Path(root, ".ao", "board.md").write_text(
        "# Board\n\n## running\n\n## blocked\n- [S1] fix it\x1b]0;pwned\x07 · needs: a reply\x1b[8m\n\n## queued\n"
        "- [S2] next\x1b[2J · acceptance: it works\n\n## inbox\n\n## verified\n\n## done\n", encoding="utf-8")
    urgent = "20261012-0900-fable-to-kiro-DECISION-stop‮gpj.md"
    Path(root, "agent-mail", urgent).write_text("# stop now\x1b[2J\n\n## URGENT\n\nline one\x1b[8m\nline two\n",
                                                encoding="utf-8")
    Path(root, ".ao", "reviews").mkdir()
    Path(root, ".ao", "reviews", "R-9.json").write_text(json.dumps({
        "id": "R-1; touch pwned", "state": "finished", "verdict": "APPROVED\x1b[2J", "slice": "S1"}), encoding="utf-8")

    panel = cli.render(project, shown=False)
    assert cli.cmd_board(project, SimpleNamespace(view=None)) == 0
    board = capsys.readouterr().out
    cli.cmd_mail(project, SimpleNamespace(action="read"))
    mail = capsys.readouterr().out

    for out in (panel, board, mail):
        assert _acted_on(out) == [], out
    assert "fix it\\x1b]0;pwned\\x07" in panel and "a reply\\x1b[8m" in panel and "5h window\\x1b[2J" in panel
    assert "stop now\\x1b[2J" in panel and "stop\\u202egpj.md" in panel and "APPROVED\\x1b[2J" in panel
    assert "fix it\\x1b]0;pwned\\x07" in board and "next\\x1b[2J" in board and "stop now\\x1b[2J" in board
    assert "line one\\x1b[8m\nline two\n" in mail                      # a body's lines stay lines
    # The returned review's id is the record's, a file in the tree, and none ao makes: no command is made of it.
    returned = next(line for line in panel.splitlines() if "REVIEW RETURNED" in line)
    assert "ao review collect" not in returned and "its id is none ao makes" in returned


# ---- the commands ao prints to copy -------------------------------------------------------------------

@pytest.mark.parametrize("by", ["Ada Lovelace", "Ada; touch pwned", "Ada $(touch pwned)"])
def test_harness_seen_quotes_the_name_in_each_command_it_prints_to_copy(project, capsys, by):
    root = project["root"]
    Path(root, ".claude").mkdir()
    Path(root, ".claude", "settings.json").write_text('{"hooks": {}}\n', encoding="utf-8")

    def seen(**given):
        cli.cmd_harness(project, SimpleNamespace(**dict({"action": "seen", "ids": [], "by": by, "digest": None,
                                                         "ask": False, "sessions": False, "timeout": 20.0}, **given)))
        return capsys.readouterr().out

    shown = seen()
    asked = seen(ask=True)                       # no terminal here: nothing is asked, and the command to run is named
    refused = seen(digest="sha256:" + "0" * 64)  # a digest of nothing shown: refused, naming the command that shows it
    commands = [part.split("`")[0].split("\n")[0].strip() for out in (shown, asked, refused)
                for part in out.split("ao harness seen ")[1:]]

    assert len(commands) == 4, (shown, asked, refused)
    for command in commands:
        if os.name == "nt":
            # Double quotes cmd.exe and PowerShell both read as one word, and no command for a name PowerShell would
            # run part of inside them.
            assert command.startswith(f"--by {A.copyable_word(by) or '<your name>'}"), command
        else:
            words = shlex.split(command)                                 # the words sh reads, as a person copies it
            assert words[:2] == ["--by", by] and words[2:3] in ([], ["--digest"], ["--ask"]), command


@pytest.mark.parametrize("word, windows, copied", [
    ("R-1791849149123", True, '"R-1791849149123"'), ("R-1791849149123", False, "R-1791849149123"),
    ("Ada Lovelace", True, '"Ada Lovelace"'), ("Ada Lovelace", False, "'Ada Lovelace'"),
    ("C:\\repo&calc", True, '"C:\\repo&calc"'), ("a|b>c^d(e);f", True, '"a|b>c^d(e);f"'),
    ("C:\\dir\\", True, '"C:\\dir\\\\"'), ("C:\\repo&calc", False, "'C:\\repo&calc'"),
    ("R-1 $(Start-Process calc)", True, None), ("R-1 `whoami`", True, None), ("%USERPROFILE%", True, None),
    ("a!b", True, None), ('say "x"', True, None), ("\u201cquoted\u201d", True, None), ("\u2018q\u2019", True, None),
    ("line\nbreak", False, None), ("tab\there", True, None), ("esc\x1b[2J", True, None),
])
def test_a_word_to_copy_is_one_no_shell_a_person_pastes_into_reads_as_more(word, windows, copied):
    """On Windows a word is always put in double quotes, where cmd.exe reads `&` and `|` as text: left bare, a path
    an agent named, `C:\\repo&calc`, ran calc. PowerShell runs `$(...)` inside the quotes, so a word holding it, or
    another sign either shell expands, is copied in no command (LEADS-MCP)."""
    assert A.copyable_word(word, windows=windows) == copied


def test_a_tab_is_shown_escaped_where_it_would_move_what_follows():
    assert A.shown_text("real\tFORGED") == "real\\x09FORGED"


def test_a_returned_review_whose_id_ao_made_is_given_its_command_to_copy(project, capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(A, "returned_reviews", lambda root: [
        {"id": "R-1791849149123", "slice": "S1", "verdict": "APPROVED", "state": "done"},
        {"id": "R-1 $(Start-Process calc)", "slice": "S2", "verdict": "APPROVED", "state": "done"}])

    panel = cli.render(project, shown=False)

    assert f"(ao review collect {A.copyable_word('R-1791849149123')})" in panel
    assert panel.count("ao review collect") == 1 and panel.count("its id is none ao makes") == 1
