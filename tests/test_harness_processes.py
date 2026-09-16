from ao import allowlist as AL, lib as A

NEWCOMER = {"id": "newcomer", "name": "n", "verified": "untested", "contract": 1,
            "send": {"argv": ["nc", "{prompt}"]},
            "detect": {"binaries": ["nc"], "processes": ["newcomer-agent"]},
            "options": {"trust_none": ["--read-only"],
                        "mcp_isolation": {"required": ["--no-servers"], "forbidden": ["--servers"]}}}


def test_the_agent_process_names_are_the_ones_the_shipped_adapters_declare():
    assert A.agent_process_names() == {"kiro-cli", "claude", "claude-code", "codex", "cursor-agent"}


def test_a_harness_declared_by_its_adapter_is_a_writer_and_one_agent_under_all_its_names(monkeypatch):
    from ao import procs
    monkeypatch.setattr(A, "package_adapters", lambda: {"newcomer": NEWCOMER})
    vectors = {41: ["/opt/bin/newcomer-agent", "--resume", "s"], 42: ["/opt/bin/other-tool", "run"]}
    monkeypatch.setattr(procs, "all_pids", lambda: list(vectors))
    monkeypatch.setattr(procs, "argv", lambda pid: vectors.get(pid))
    monkeypatch.setattr(procs, "cwd", lambda pid: "/repo")
    monkeypatch.setattr(A, "helper_pids", lambda root: set())
    monkeypatch.setattr(A.os.path, "realpath", lambda p: p)

    assert A.agent_pids("/repo", {}) == [41]
    assert A.agent_process_names() == {"newcomer-agent"}


def test_a_reviewers_mcp_isolation_is_read_from_its_adapter_by_binary(monkeypatch):
    assert "it starts every configured MCP server (no --strict-mcp-config)" in AL.reviewer_problems(
        ["claude", "-p", "{prompt}", "--allowedTools", "Read"])

    monkeypatch.setattr(A, "package_adapters", lambda: {"newcomer": NEWCOMER})

    assert AL.reviewer_problems(["/opt/bin/nc", "{prompt}", "--servers=x.json"]) == [
        "it starts every configured MCP server (no --no-servers)", "it loads MCP servers from --servers"]
    assert AL.reviewer_problems(["nc", "{prompt}", "--no-servers"]) == []
    assert AL.reviewer_problems(["claude", "-p", "{prompt}"]) == []
