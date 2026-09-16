import json
import os
from types import SimpleNamespace

from ao import allowlist as AL, cli


def _bare(root):
    json.dump({"project": "proj", "round_budget": 5}, open(os.path.join(root, ".ao", "config.json"), "w",
                                                             encoding="utf-8"))


def test_the_reviewer_ao_writes_starts_no_mcp_server_on_either_path(project):
    root = project["root"]
    for reviewer_model in (None, "claude-sonnet-5"):
        _bare(root)
        args = SimpleNamespace(profile="claude-kiro", implementer=None, model=None, effort=None,
                               reviewer_model=reviewer_model)
        cli._apply_profile(root, args)
        argv = json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))["reviewer"]["argv"]

        assert "--strict-mcp-config" in argv and not any(arg.startswith("--mcp-config") for arg in argv)
        assert AL.reviewer_problems(argv) == []


def test_a_reviewer_that_can_start_servers_or_write_is_named():
    assert AL.reviewer_problems(["claude", "-p", "{prompt}", "--allowedTools", ""]) == [
        "it starts every configured MCP server (no --strict-mcp-config)"]
    assert AL.reviewer_problems(["claude", "-p", "{prompt}", "--strict-mcp-config", "--mcp-config", "x.json",
                                 "--allowedTools", "Read,Edit,Bash(git:*)"]) == [
        "its allowed tools go beyond reading: Bash, Edit", "it loads MCP servers from --mcp-config"]
    assert AL.reviewer_problems(["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", "{prompt}"]) == [
        "it is granted every tool"]
    assert AL.reviewer_problems(["kiro-cli", "chat", "--no-interactive", "--trust-tools=", "{prompt}"]) == []


def test_the_doctor_names_a_reviewer_that_reaches_beyond_reading(project):
    cfg = dict(project, reviewer={"id": "open-reviewer", "argv": ["claude", "-p", "{prompt}"],
                                  "fallbacks": [{"id": "fallback-writer",
                                                 "argv": ["claude", "-p", "{prompt}", "--strict-mcp-config",
                                                          "--allowedTools", "Write"]}]})

    problems = dict(cli._actor_grant_problems(cfg))

    assert "no --strict-mcp-config" in problems["reviewer-tools:open-reviewer"]
    assert "beyond reading: Write" in problems["reviewer-tools:fallback-writer"]
