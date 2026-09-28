"""Each role is served the tools its playbook uses, and a reviewer writes nothing (MCP-ROLES).

The MCP server served every agent every tool, so a reviewer that loaded it could file a report to
the architect or acknowledge mail meant for the implementer. A server started with --role lists
and runs that role's tools alone, and `ao init` writes the role into each harness's registration
when the harnesses reading it hold one role between them. The server is driven here the way a
client drives it - `ao mcp serve`, JSON-RPC lines on stdin, answers on stdout - so what is tested
is what an agent is listed and what it can call.
"""
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, mcp, skillkit

ROOT = Path(__file__).resolve().parent.parent

# The spec's reviewer: it reads the project's status, the board and the candidate it judges.
REVIEWER = {"ao_status", "ao_board", "ao_candidate"}


def _serve(monkeypatch, capsys, root, requests, *flags):
    """Run `ao -C <root> mcp serve <flags>` over `requests`: ({id: answer}, what it wrote on stderr)."""
    monkeypatch.setattr(sys, "argv", list(sys.argv))            # `ao mcp serve` rewrites it for the server
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(dict(request, jsonrpc="2.0")) + "\n"
                                                          for request in requests)))
    capsys.readouterr()
    assert cli.main(["-C", root, "mcp", "serve", *flags]) == 0
    captured = capsys.readouterr()
    answers = [json.loads(line) for line in captured.out.splitlines() if line.startswith("{")]
    return {answer["id"]: answer for answer in answers}, captured.err


def _listed(monkeypatch, capsys, root, *flags):
    answers, err = _serve(monkeypatch, capsys, root, [{"id": 1, "method": "initialize", "params": {}},
                                                      {"id": 2, "method": "tools/list"}], *flags)
    return [tool["name"] for tool in answers[2]["result"]["tools"]], err


def _called(monkeypatch, capsys, root, calls, *flags):
    """{tool: what it answered} for each (tool, arguments) called in one session of the server."""
    answers, _ = _serve(monkeypatch, capsys, root,
                        [{"id": number, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
                         for number, (name, arguments) in enumerate(calls)], *flags)
    return {name: json.loads(answers[number]["result"]["content"][0]["text"])
            for number, (name, _) in enumerate(calls)}


def _contents(root):
    """Every file of the project but git's own, byte for byte."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(Path(root).rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts}


def _required(tool, message):
    """Arguments a tool's schema requires, each filled: a string with the waiting message's name."""
    schema = tool.get("inputSchema") or {}
    fill = {"string": message, "integer": 2, "array": ["yes", "no"]}
    return {name: fill[((schema.get("properties") or {}).get(name) or {}).get("type", "string")]
            for name in schema.get("required") or []}


def _playbook_mailbox_tools():
    """The tools the playbook's mailbox section, the implementer's, names on its Tools line."""
    playbook = (ROOT / "src" / "ao" / "skill" / "SKILL.md").read_text(encoding="utf-8")
    section = re.search(r"^## 4\. .*?(?=^## )", playbook, re.M | re.S).group(0)
    line = re.search(r"^- Tools:.*?(?=^- )", section, re.M | re.S).group(0)
    return set(re.findall(r"`(ao_[a-z_]+)", line))


def _configure(root, **blocks):
    """Replace the fixture's role blocks with `blocks`, and keep the rest of .ao/config.json."""
    path = Path(root, ".ao", "config.json")
    cfg = {key: value for key, value in json.loads(path.read_text(encoding="utf-8")).items()
           if key not in A.ROLE_BLOCKS}
    path.write_text(json.dumps(dict(cfg, **blocks)), encoding="utf-8")


def _registered(root, relative):
    document = json.loads(Path(root, *relative.split("/")).read_text(encoding="utf-8"))
    return document["mcpServers"]["ao"]["args"]


def _git(root, *args):
    subprocess.run([A.git_binary(), "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                   check=True, capture_output=True)


# ---- the server ------------------------------------------------------------------------------

def test_every_role_ao_configures_has_a_tool_set_and_names_only_tools_the_server_has():
    assert set(mcp.ROLE_TOOLS) == set(A.ROLE_BLOCKS)
    for role, names in mcp.ROLE_TOOLS.items():
        assert set(names) <= set(mcp.EVERY_TOOL), f"{role} names a tool the server does not register"


@pytest.mark.parametrize("role", sorted(mcp.ROLE_TOOLS))
def test_each_role_is_listed_the_tools_its_playbook_uses_and_no_other(project, monkeypatch, capsys, role):
    expected = {"architect": set(mcp.EVERY_TOOL),                       # everything it had before roles
                "implementer": _playbook_mailbox_tools() | {"ao_verify"},  # and the gate step of its loop
                "reviewer": REVIEWER}[role]

    listed, err = _listed(monkeypatch, capsys, project["root"], "--role", role)

    assert set(listed) == expected and len(listed) == len(expected)
    assert "every tool is served" not in err


def test_the_playbook_still_names_the_implementer_s_mailbox_tools():
    """The implementer's expectation is read from the playbook; an empty read would prove nothing."""
    assert {"ao_inbox", "ao_ack", "ao_report", "ao_ask"} <= _playbook_mailbox_tools()


def test_a_reviewer_is_refused_every_tool_outside_its_set_and_its_own_change_nothing(project, monkeypatch, capsys):
    root = project["root"]
    implementer, architect = A.mail_names(project)
    message = f"20260926-0900-{architect}-to-{implementer}-DECISION-scratch.md"
    Path(root, "agent-mail", message).write_text("# scratch\n\n## URGENT\n\nApply it, then acknowledge it.\n",
                                                 encoding="utf-8")
    A.project_key(root)                  # a project's name is written the first time any command sees it
    before = _contents(root)

    answered = _called(monkeypatch, capsys, root, [(tool["name"], _required(tool, message)) for tool in mcp.TOOLS],
                       "--role", "reviewer")

    for name, payload in answered.items():
        if name in REVIEWER:
            assert "error" not in payload and "URGENT_UNACKNOWLEDGED" not in payload, name
        else:
            assert list(payload) == ["error"], name        # refused, and nothing rides along on it
            assert payload["error"].startswith(f"{name} is not one of the reviewer's tools")
    assert _contents(root) == before                       # no report, no acknowledgement, no mail marked seen
    assert (Path(root, "agent-mail") / message).exists()


def test_the_reviewer_s_tools_are_the_ones_docs_mcp_classes_read():
    """docs/mcp.md's access classes are measured by tests/test_docs_names.py; the reviewer holds only reads."""
    table = dict(re.findall(r"^\| `(ao_[a-z_]+)` \| (read|write|run) \|", (ROOT / "docs" / "mcp.md").read_text(
        encoding="utf-8"), re.M))
    assert set(table) == set(mcp.EVERY_TOOL)
    assert {table[name] for name in REVIEWER} == {"read"}


def test_urgent_mail_rides_along_where_ao_inbox_is_served(project, monkeypatch, capsys):
    root = project["root"]
    implementer, architect = A.mail_names(project)
    Path(root, "agent-mail", f"20260926-0900-{architect}-to-{implementer}-DECISION-stop.md").write_text(
        "# stop\n\n## URGENT\n\nStop and read this.\n", encoding="utf-8")

    as_implementer = _called(monkeypatch, capsys, root, [("ao_board", {})], "--role", "implementer")["ao_board"]
    as_reviewer = _called(monkeypatch, capsys, root, [("ao_board", {})], "--role", "reviewer")["ao_board"]

    assert [message["title"] for message in as_implementer["URGENT_UNACKNOWLEDGED"]] == ["stop"]
    assert "URGENT_UNACKNOWLEDGED" not in as_reviewer and "URGENT_NOTE" not in as_reviewer


@pytest.mark.parametrize("flags", [(), ("--role", "reviwer")], ids=["no role", "a role it does not know"])
def test_no_role_or_one_the_server_does_not_know_is_served_every_tool_and_says_so(project, monkeypatch, capsys,
                                                                                   flags):
    listed, err = _listed(monkeypatch, capsys, project["root"], *flags)

    assert listed == list(mcp.EVERY_TOOL)
    notice = [line for line in err.splitlines() if "every tool is served" in line]
    assert len(notice) == 1 and notice[0].startswith("ao mcp: ")
    assert ("'reviwer'" in notice[0]) == bool(flags)


def test_a_role_written_in_other_case_is_still_that_role(project, monkeypatch, capsys):
    listed, err = _listed(monkeypatch, capsys, project["root"], "--role", " Reviewer ")

    assert set(listed) == REVIEWER and "every tool is served" not in err


def test_the_candidate_is_the_staged_one_and_carries_what_a_review_judges_it_against(project, monkeypatch, capsys):
    root = project["root"]
    Path(root, "src").mkdir()
    Path(root, "src", "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(root, "add", "src/calc.py")
    _git(root, "commit", "-q", "-m", "calc")
    Path(root, "tests").mkdir()
    Path(root, "tests", "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n"
                                                   "    assert add(1, 2) == 3\n", encoding="utf-8")
    _git(root, "add", "tests/test_calc.py")
    board = Path(root, ".ao", "board.md")
    board.write_text(board.read_text(encoding="utf-8").replace(
        "## running\n", "## running\n- [S1] the adder · acceptance: add returns the sum\n"), encoding="utf-8")

    payload = _called(monkeypatch, capsys, root, [("ao_candidate", {})], "--role", "reviewer")["ao_candidate"]

    staged = A.index_candidate(root)
    assert (payload["slice"], payload["boundary"]) == ("S1", "add returns the sum")
    assert payload["candidate"] == {key: staged[key] for key in ("digest", "head", "index_tree", "changed_paths")}
    assert payload["candidate"]["changed_paths"] == ["tests/test_calc.py"]
    # A test-only candidate is judged against the committed source it runs (#97), as `ao review` hands it.
    assert payload["context"]["paths"] == ["src/calc.py"] and "def add(a, b):" in payload["context"]["text"]
    assert set(payload) == {"slice", "title", "boundary", "candidate", "context"}      # and no diff


def test_a_candidate_that_cannot_be_measured_is_said_to_be_so(project, monkeypatch):
    def unmeasurable(root):
        raise RuntimeError("no index")

    monkeypatch.setattr(A, "index_candidate", unmeasurable)

    payload = mcp.call("ao_candidate", {}, project, False, "reviewer")

    assert payload["candidate"] is None and payload["context"] is None
    assert payload["problem"] == "the staged candidate cannot be measured: no index"


# ---- the registrations -------------------------------------------------------------------------

def test_init_registers_each_harness_with_the_one_role_its_readers_hold(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)     # a harness's own CLI is never run

    # The fixture: kiro implements, and the architect runs `claude`; nothing reviews.
    assert skillkit.register_mcp(root, {"claude-code", "kiro"}, exe="/x/ao") == {"claude-code": "registered",
                                                                                 "kiro": "registered"}

    assert _registered(root, ".mcp.json") == ["-C", root, "mcp", "serve", "--role", "architect"]
    assert _registered(root, ".kiro/settings/mcp.json") == ["-C", root, "mcp", "serve", "--role", "implementer"]


ON_MCP_JSON = {"argv": ["claude", "-p", "{prompt}"]}      # a block whose harness reads .mcp.json


@pytest.mark.parametrize("blocks", [
    {"architect": ON_MCP_JSON, "reviewer": dict(ON_MCP_JSON, id="r")},
    {"architect": ON_MCP_JSON, "reviewer": {"id": "r", "adapter": "qoder"}},
    {"architect": ON_MCP_JSON, "reviewer": {"id": "r", "adapter": "pi", "fallbacks": [dict(ON_MCP_JSON, id="f")]}},
    {"architect": ON_MCP_JSON, "capability_matrix": {}},
    {},
], ids=["one harness architects and reviews", "a harness sharing its file reviews", "a reviewer's fallback reviews",
        "a capability matrix binds the roles", "no role is configured"])
def test_a_registration_read_by_two_roles_or_none_names_no_role(project, monkeypatch, blocks):
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)
    _configure(root, **blocks)

    assert skillkit.register_mcp(root, {"claude-code"}, exe="/x/ao") == {"claude-code": "registered"}

    assert _registered(root, ".mcp.json") == ["-C", root, "mcp", "serve"]


def test_the_default_profile_registers_its_implementer_and_leaves_the_two_role_harness_unnamed(project, monkeypatch):
    """What docs/mcp.md says of the default profile, from the role blocks `ao init --profile` composes."""
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)
    assert A.default_profile() == "claude-kiro"
    planned, _ = cli._profile_config(root, SimpleNamespace(profile=A.default_profile()), {"project": "proj"})
    Path(root, ".ao", "config.json").write_text(json.dumps(planned), encoding="utf-8")

    skillkit.register_mcp(root, {"claude-code", "kiro"}, exe="/x/ao")

    assert _registered(root, ".mcp.json") == ["-C", root, "mcp", "serve"]           # architect and reviewer
    assert _registered(root, ".kiro/settings/mcp.json") == ["-C", root, "mcp", "serve", "--role", "implementer"]


def test_a_role_reassigned_reaches_the_registration_when_init_registers_again(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)
    skillkit.register_mcp(root, {"kiro"}, exe="/x/ao")
    _configure(root, architect={"adapter": "kiro"})

    assert skillkit.register_mcp(root, {"kiro"}, exe="/x/ao") == {"kiro": "registered"}
    assert _registered(root, ".kiro/settings/mcp.json") == ["-C", root, "mcp", "serve", "--role", "architect"]
    assert skillkit.register_mcp(root, {"kiro"}, exe="/x/ao") == {"kiro": "kept"}


def test_a_manual_snippet_carries_the_role_escaped_as_its_root_is(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)
    _configure(root, implementer={"adapter": "codex"})
    exe = "/x/{args}/ao"                                   # filled once: a value is never filled again

    snippet = skillkit.register_mcp(root, {"codex"}, exe=exe)["codex"].split("\n", 1)[1]

    body = skillkit._toml_string_body
    assert f'command = "{body(exe)}"' in snippet
    assert f'args = ["-C", "{body(root)}", "mcp", "serve", "--role", "implementer"]' in snippet
    tomllib = pytest.importorskip("tomllib", reason="the TOML reader ships with Python 3.11")
    assert tomllib.loads(snippet)["mcp_servers"]["ao"] == {
        "command": exe, "args": ["-C", root, "mcp", "serve", "--role", "implementer"]}


def test_ao_mcp_config_prints_the_role_among_the_server_s_arguments(project, capsys):
    root = project["root"]

    assert cli.main(["-C", root, "mcp", "config", "--role", "reviewer"]) == 0

    out = capsys.readouterr().out
    server = json.loads(out[out.index("{"):])["mcpServers"]["agent-orchestrator"]
    assert server["args"] == ["-C", root, "mcp", "serve", "--role", "reviewer"]


def test_docs_mcp_lists_each_role_with_the_tools_it_is_served():
    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| (architect|implementer|reviewer) \| (.+?) \|$", text, re.M))

    assert set(rows) == set(mcp.ROLE_TOOLS)
    for role, cell in rows.items():
        named = set(re.findall(r"`(ao_[a-z_]+)`", cell)) or set(mcp.EVERY_TOOL)   # "every tool"
        assert named == set(mcp.ROLE_TOOLS[role]), role
