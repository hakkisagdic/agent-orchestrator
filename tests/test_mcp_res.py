"""MCP-RES — MCP resources, prompts, `--project all` registry scoping, and the CLI authority boundary."""
import io
import json
from pathlib import Path

import pytest

from ao import cli, lib as A, mcp
from ao.storage import append_jsonl


def _meta(version=mcp.PROTOCOL_2026, **extra):
    return dict({"io.modelcontextprotocol/protocolVersion": version,
                 "io.modelcontextprotocol/clientCapabilities": {},
                 "io.modelcontextprotocol/clientInfo": {"name": "probe", "version": "1.0.0"}}, **extra)


def _with_meta(params, version):
    base = dict(params or {})
    if version is not None:
        base["_meta"] = _meta(version)
    return base


def _serve(monkeypatch, capsys, root, requests, *extra_argv):
    argv = ["ao-mcp"]
    if root is not None:
        argv += ["-C", str(root)]
    argv += list(extra_argv)
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(req) + "\n" for req in requests)))
    assert mcp.main() is None
    captured = capsys.readouterr()
    answers = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    return {ans.get("id"): ans for ans in answers}, captured.err


def _tree(root):
    root = Path(root)
    return {str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and ".git" not in path.parts}


def _make_project(tmp_path, name, slice_id="S1"):
    root = tmp_path / name
    root.mkdir()
    (root / ".ao" / "ledger").mkdir(parents=True)
    (root / "agent-mail").mkdir()
    (root / "semantic-review").mkdir()
    cfg = {
        "project": name,
        "mailbox": "agent-mail",
        "reviews": "semantic-review",
        "actors": {
            "claude-arch": {"adapter": "claude-code", "model": "claude-opus-4-6", "family": "anthropic"},
            "kiro-impl": {"adapter": "kiro", "model": "claude-sonnet-4-5", "family": "anthropic"},
            "codex-rev": {"adapter": "codex", "model": "gpt-5.4", "family": "openai"},
        },
        "roles": {"architect": "claude-arch", "implementer": "kiro-impl", "reviewer": "codex-rev"},
    }
    (root / ".ao" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (root / ".ao" / "board.md").write_text(
        f"# Board\n\n## running\n- [{slice_id}] Slice for {name} · acceptance: tests pass\n\n"
        "## blocked\n\n## queued\n\n## inbox\n\n## verified\n\n## done\n",
        encoding="utf-8",
    )
    A.record_slice_move(str(root), slice_id, "queued", "running", by="implementer", why="start")
    append_jsonl(
        str(root / ".ao" / "ledger" / "decisions.jsonl"),
        {"id": "DEC-1", "decision": f"Use stdlib in {name}", "why": "zero deps", "scope": "mcp", "at": 1700000010},
    )
    A.ask(str(root), f"Which port for {name}?", ["8731", "8732"], context="loopback", slice_id=slice_id, cfg=dict(cfg, root=str(root)))
    return root


@pytest.mark.parametrize("version", [mcp.PROTOCOL_2026, mcp.PROTOCOL, None])
def test_resources_and_templates_list_and_read_under_every_protocol_version(project, monkeypatch, capsys, version):
    root = project["root"]
    Path(root, ".ao", "board.md").write_text(
        "# Board\n\n## running\n- [MCP-RES] Resources and prompts · acceptance: spec compliant\n\n"
        "## blocked\n\n## queued\n\n## inbox\n\n## verified\n\n## done\n",
        encoding="utf-8",
    )
    A.record_slice_move(root, "MCP-RES", "queued", "running", by="implementer", why="start")
    append_jsonl(
        str(Path(root, ".ao", "ledger", "decisions.jsonl")),
        {"id": "DEC-9", "decision": "Keep authority in the CLI", "why": "separation of duties", "at": 1700000100},
    )
    A.ask(root, "Keep URI scheme ao://?", ["yes", "no"], slice_id="MCP-RES", cfg=project)

    requests = []
    if version is None:
        requests.append({"id": 0, "method": "initialize",
                         "params": {"protocolVersion": mcp.PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "legacy", "version": "1.0"}}})
    requests += [
        {"id": 1, "method": "resources/list", "params": _with_meta({}, version)},
        {"id": 2, "method": "resources/templates/list", "params": _with_meta({}, version)},
        {"id": 3, "method": "resources/read", "params": _with_meta({"uri": "ao://proj/board"}, version)},
        {"id": 4, "method": "resources/read", "params": _with_meta({"uri": "ao://proj/roles"}, version)},
        {"id": 5, "method": "resources/read", "params": _with_meta({"uri": "ao://proj/ledger"}, version)},
        {"id": 6, "method": "resources/read", "params": _with_meta({"uri": "ao://proj/decisions"}, version)},
        {"id": 7, "method": "resources/read", "params": _with_meta({"uri": "ao://proj/unknown"}, version)},
    ]

    before = _tree(root)
    answers, _ = _serve(monkeypatch, capsys, root, requests, "--role", "reviewer")
    assert _tree(root) == before

    listed = answers[1]["result"]["resources"]
    assert [r["uri"] for r in listed] == [
        "ao://proj/board",
        "ao://proj/roles",
        "ao://proj/ledger",
        "ao://proj/decisions",
    ]
    assert all(r["mimeType"] == "application/json" and r["name"].startswith("proj/") for r in listed)

    templates = answers[2]["result"]["resourceTemplates"]
    assert [t["uriTemplate"] for t in templates] == [
        "ao://{project}/board",
        "ao://{project}/roles",
        "ao://{project}/ledger",
        "ao://{project}/decisions",
    ]
    assert all(t["mimeType"] == "application/json" for t in templates)

    if version == mcp.PROTOCOL_2026:
        for req_id in (1, 2, 3, 4, 5, 6):
            assert answers[req_id]["result"]["resultType"] == "complete"
            assert "io.modelcontextprotocol/serverInfo" in answers[req_id]["result"]["_meta"]
        assert answers[7]["error"]["code"] == -32602
    else:
        for req_id in (1, 2, 3, 4, 5, 6):
            assert "resultType" not in answers[req_id]["result"]
        assert answers[7]["error"]["code"] == -32002

    board = json.loads(answers[3]["result"]["contents"][0]["text"])
    assert board["project"] == "proj" and board["uri"] == "ao://proj/board" and board["authority"] == "none"
    assert board["counts"]["running"] == 1

    roles = json.loads(answers[4]["result"]["contents"][0]["text"])
    assert roles["project"] == "proj" and roles["uri"] == "ao://proj/roles" and roles["authority"] == "none"
    assert set(roles["roles"]) == {"architect", "implementer", "reviewer"}

    ledger = json.loads(answers[5]["result"]["contents"][0]["text"])
    assert ledger["project"] == "proj" and ledger["uri"] == "ao://proj/ledger" and ledger["authority"] == "none"
    assert ledger["counts"]["slices"] == 1
    assert ledger["counts"]["decisions"] == 1
    assert ledger["latest_slice"]["slice"] == "MCP-RES"
    assert "token" not in json.dumps(ledger)

    decisions = json.loads(answers[6]["result"]["contents"][0]["text"])
    assert decisions["project"] == "proj" and decisions["uri"] == "ao://proj/decisions" and decisions["authority"] == "none"
    assert decisions["decisions"][-1]["decision"] == "Keep authority in the CLI"
    assert decisions["open_count"] == 1


@pytest.mark.parametrize("version", [mcp.PROTOCOL_2026, mcp.PROTOCOL, None])
def test_prompts_list_and_get_under_every_protocol_version(project, monkeypatch, capsys, version):
    root = project["root"]
    requests = [
        {"id": 1, "method": "prompts/list", "params": _with_meta({}, version)},
        {"id": 2, "method": "prompts/get", "params": _with_meta({"name": "status"}, version)},
        {"id": 3, "method": "prompts/get",
         "params": _with_meta({"name": "land-slice", "arguments": {"slice": "MCP-RES"}}, version)},
        {"id": 4, "method": "prompts/get", "params": _with_meta({"name": "stuck"}, version)},
        {"id": 5, "method": "prompts/get", "params": _with_meta({"name": "land-slice", "arguments": {}}, version)},
        {"id": 6, "method": "prompts/get", "params": _with_meta({"name": "no-such-prompt"}, version)},
    ]

    before = _tree(root)
    answers, _ = _serve(monkeypatch, capsys, root, requests)
    assert _tree(root) == before

    prompts = answers[1]["result"]["prompts"]
    assert [p["name"] for p in prompts] == ["status", "land-slice", "stuck"]
    by_name = {p["name"]: p for p in prompts}
    assert by_name["status"]["arguments"] == [
        {"name": "project", "description": "Project name in ao's project registry", "required": False}
    ]
    assert by_name["land-slice"]["arguments"] == [
        {"name": "project", "description": "Project name in ao's project registry", "required": False},
        {"name": "slice", "description": "Slice id on the project's board to land", "required": True},
    ]

    for req_id, expected_tools in (
        (2, ("ao_status", "ao_board", "ao_decisions", "ao_notices")),
        (3, ("ao_board", "ao_status", "ao_inbox", "ao_decisions", "ao_verify", "ao_candidate")),
        (4, ("ao_status", "ao_board", "ao_decisions", "ao_notices", "ao_watchdog", "ao_inbox")),
    ):
        res = answers[req_id]["result"]
        if version == mcp.PROTOCOL_2026:
            assert res["resultType"] == "complete"
            assert "io.modelcontextprotocol/serverInfo" in res["_meta"]
        assert "proj" in res["description"]
        msg = res["messages"][0]
        assert msg["role"] == "user"
        assert msg["content"]["type"] == "text"
        text = msg["content"]["text"]
        positions = [text.index(tool_name) for tool_name in expected_tools]
        assert positions == sorted(positions), f"tools out of order in prompt {req_id}: {expected_tools}"

    assert "MCP-RES" in answers[3]["result"]["messages"][0]["content"]["text"]
    assert answers[5]["error"]["code"] == -32602
    assert answers[6]["error"]["code"] == -32602


def test_project_all_serves_every_registered_project_and_refuses_any_outside_the_registry(
        tmp_path, monkeypatch, capsys):
    alpha = _make_project(tmp_path, "alpha", slice_id="A-1")
    beta = _make_project(tmp_path, "beta", slice_id="B-1")
    outsider = _make_project(tmp_path, "outsider", slice_id="OUT-1")

    registry_file = Path(A.project_registry_path())
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(json.dumps({
        "alpha": {"root": str(alpha), "at": 1700000000},
        "beta": {"root": str(beta), "at": 1700000001},
    }), encoding="utf-8")

    requests = [
        {"id": 1, "method": "tools/list", "params": {"_meta": _meta("2026-07-28")}},
        {"id": 2, "method": "resources/list", "params": {"_meta": _meta("2026-07-28")}},
        {"id": 3, "method": "prompts/list", "params": {"_meta": _meta("2026-07-28")}},
        {"id": 4, "method": "tools/call",
         "params": {"name": "ao_board", "arguments": {"project": "alpha"}, "_meta": _meta("2026-07-28")}},
        {"id": 5, "method": "tools/call",
         "params": {"name": "ao_board", "arguments": {"project": "beta"}, "_meta": _meta("2026-07-28")}},
        {"id": 6, "method": "tools/call",
         "params": {"name": "ao_board", "arguments": {"project": "outsider"}, "_meta": _meta("2026-07-28")}},
        {"id": 7, "method": "tools/call",
         "params": {"name": "ao_board", "arguments": {}, "_meta": _meta("2026-07-28")}},
        {"id": 8, "method": "resources/read",
         "params": {"uri": "ao://alpha/board", "_meta": _meta("2026-07-28")}},
        {"id": 9, "method": "resources/read",
         "params": {"uri": "ao://beta/ledger", "_meta": _meta("2026-07-28")}},
        {"id": 10, "method": "resources/read",
         "params": {"uri": "ao://outsider/board", "_meta": _meta("2026-07-28")}},
        {"id": 11, "method": "resources/read",
         "params": {"uri": "ao://outsider/board", "_meta": _meta("2025-06-18")}},
        {"id": 12, "method": "prompts/get",
         "params": {"name": "land-slice", "arguments": {"project": "alpha", "slice": "A-1"},
                    "_meta": _meta("2026-07-28")}},
        {"id": 13, "method": "prompts/get",
         "params": {"name": "status", "arguments": {"project": "outsider"}, "_meta": _meta("2026-07-28")}},
        {"id": 14, "method": "prompts/get",
         "params": {"name": "status", "arguments": {}, "_meta": _meta("2026-07-28")}},
    ]

    outsider_before = _tree(outsider)
    answers, _ = _serve(monkeypatch, capsys, alpha, requests, "--project", "all")
    assert _tree(outsider) == outsider_before

    tools = answers[1]["result"]["tools"]
    assert all("project" in t["inputSchema"]["properties"] and "project" in t["inputSchema"]["required"]
               for t in tools)

    uris = [r["uri"] for r in answers[2]["result"]["resources"]]
    assert uris == [
        "ao://alpha/board", "ao://alpha/roles", "ao://alpha/ledger", "ao://alpha/decisions",
        "ao://beta/board", "ao://beta/roles", "ao://beta/ledger", "ao://beta/decisions",
    ]
    assert all("outsider" not in uri for uri in uris)

    prompts = answers[3]["result"]["prompts"]
    for prompt in prompts:
        proj_arg = next(arg for arg in prompt["arguments"] if arg["name"] == "project")
        assert proj_arg["required"] is True

    board_alpha = json.loads(answers[4]["result"]["content"][0]["text"])
    assert board_alpha["project"] == "alpha" and board_alpha["states"]["running"][0]["id"] == "A-1"
    board_beta = json.loads(answers[5]["result"]["content"][0]["text"])
    assert board_beta["project"] == "beta" and board_beta["states"]["running"][0]["id"] == "B-1"

    refused_tool = json.loads(answers[6]["result"]["content"][0]["text"])
    assert "not in ao's project registry" in refused_tool["error"]
    missing_proj_tool = json.loads(answers[7]["result"]["content"][0]["text"])
    assert "project is required with --project all" in missing_proj_tool["error"]

    read_alpha = json.loads(answers[8]["result"]["contents"][0]["text"])
    assert read_alpha["project"] == "alpha" and read_alpha["states"]["running"][0]["id"] == "A-1"
    read_beta = json.loads(answers[9]["result"]["contents"][0]["text"])
    assert read_beta["project"] == "beta" and read_beta["latest_slice"]["slice"] == "B-1"

    assert answers[10]["error"]["code"] == -32602
    assert "not in ao's project registry" in answers[10]["error"]["message"]
    assert answers[11]["error"]["code"] == -32002
    assert "not in ao's project registry" in answers[11]["error"]["message"]

    prompt_alpha = answers[12]["result"]["messages"][0]["content"]["text"]
    assert "Project: alpha" in prompt_alpha and "Slice: A-1" in prompt_alpha
    assert answers[13]["error"]["code"] == -32602
    assert "not in ao's project registry" in answers[13]["error"]["message"]
    assert answers[14]["error"]["code"] == -32602


def test_unknown_project_scope_stops_the_server_and_cli_config_carries_project_all(project, monkeypatch, capsys):
    root = project["root"]
    monkeypatch.setattr("sys.argv", ["ao-mcp", "-C", root, "--project", "other"])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert mcp.main() == 2
    assert "--project 'other' is not 'all'" in capsys.readouterr().err

    monkeypatch.setattr(A, "ao_program", lambda: "/opt/ao/bin/ao")
    assert cli.main(["-C", root, "mcp", "config", "--project", "all", "--role", "reviewer"]) == 0
    out = capsys.readouterr().out
    server = json.loads(out[out.index("{"):])["mcpServers"]["agent-orchestrator"]
    assert server["args"] == ["-C", root, "mcp", "serve", "--project", "all", "--role", "reviewer"]


def test_authority_stays_in_the_cli_and_no_authority_command_is_reachable(project, monkeypatch, capsys):
    root = project["root"]
    question = A.ask(root, "Approve the change?", ["yes", "no"], slice_id="S1", cfg=project)
    authority_tools = (
        "ao_commit_ok", "ao_commit", "ao_waive", "ao_answer",
        "ao_person_review", "ao_collect_review", "ao_harness_seen", "ao_review",
    )
    assert set(mcp.EVERY_TOOL).isdisjoint(authority_tools)

    requests = [
        {"id": 1, "method": "resources/read", "params": {"uri": "ao://proj/board"}},
        {"id": 2, "method": "resources/read", "params": {"uri": "ao://proj/roles"}},
        {"id": 3, "method": "resources/read", "params": {"uri": "ao://proj/ledger"}},
        {"id": 4, "method": "resources/read", "params": {"uri": "ao://proj/decisions"}},
        {"id": 5, "method": "prompts/get", "params": {"name": "status"}},
        {"id": 6, "method": "prompts/get", "params": {"name": "land-slice", "arguments": {"slice": "S1"}}},
        {"id": 7, "method": "prompts/get", "params": {"name": "stuck"}},
    ]
    for offset, tool_name in enumerate(authority_tools, start=10):
        requests.append({"id": offset, "method": "tools/call",
                         "params": {"name": tool_name,
                                    "arguments": {"id": question["id"], "value": "a", "by": "person"}}})

    before = _tree(root)
    answers, _ = _serve(monkeypatch, capsys, root, requests)
    assert _tree(root) == before
    assert A.decisions(root, "open")[0]["id"] == question["id"]
    assert A.authority_history(root) == []
    assert A.waiver_rows(root) == []

    for offset, tool_name in enumerate(authority_tools, start=10):
        payload = json.loads(answers[offset]["result"]["content"][0]["text"])
        assert payload == {"error": f"unknown tool {tool_name}"}

    land_text = answers[6]["result"]["messages"][0]["content"]["text"]
    for cli_only in ("commit-ok", "person-review", "waive", "answer", "harness seen"):
        assert cli_only in land_text
