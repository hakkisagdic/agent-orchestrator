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


# ---- a reviewer route that could write is refused where it is started, not only named (REVIEWER-REACH) ---------

import pytest  # noqa: E402

from ao import lib as A, storage  # noqa: E402
from tests.test_review_chain import _args, _fake, _repo_with_change  # noqa: E402

CODEX = ["codex", "exec", "{prompt}", "--ignore-user-config"]
CLAUDE = ["claude", "-p", "{prompt}", "--strict-mcp-config"]
WIDER = [
    CODEX + ["--sandbox", "workspace-write"],
    CODEX + ["--sandbox=danger-full-access"],
    CODEX + ["--dangerously-bypass-approvals-and-sandbox"],
    CODEX + ["--approve-for-me"],
    CODEX + ["--not-so-yolo"],
    CODEX + ["--yolo"],
    A.compose_reviewer("codex", model="m")["argv"] + ["--sandbox", "danger-full-access"],
    CLAUDE + ["--permission-mode", "bypassPermissions"],
    CLAUDE + ["--dangerously-skip-permissions"],
]


@pytest.mark.parametrize("argv", WIDER, ids=lambda argv: " ".join(argv[3:]))
def test_a_reviewer_route_that_could_write_is_refused_not_started(project, monkeypatch, argv):
    """Only `ao doctor --check` named such a route; a review started it and recorded what it answered."""
    started = []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (f"/agents/{name}", "0.160.0"))
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kw:
                        started.append(argv) or {"ok": True, "out": "VERDICT: APPROVED"})
    route = {"id": "rv", "argv": argv}

    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, "review this", 60, False, route)

    assert started == []
    assert attempt["kind"] == "configuration-error" and attempt["retryable"] is False
    assert attempt["reason"].startswith("a reviewer must not be able to write: ")


def test_a_review_whose_only_reviewer_could_write_approves_nothing_and_a_composed_one_still_runs(
        project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    fake = _fake("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")
    started, real = [], cli._run_reviewer
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (fake[0], "0.160.0"))
    # The harness is a fake that answers APPROVED; the flags ao handed it are kept for the assertion.
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kw:
                        started.append(argv[1:]) or real(root, fake[:3] + ["x"], timeout, fallback, **kw))
    writing = {"id": "rv", "adapter": "codex", "family": "openai", "argv": CODEX + ["--sandbox", "workspace-write"]}

    code = cli.cmd_review(dict(project, reviewer=writing), _args())
    row = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)[-1]
    assert started == [] and row["verdict"] == "UNAVAILABLE" and code == 3

    composed = dict(A.compose_reviewer("codex", model="m", family="openai"), id="rv")
    code = cli.cmd_review(dict(project, reviewer=composed), _args())
    row = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)[-1]
    assert len(started) == 1 and row["verdict"] == "APPROVED" and code == 0


def test_a_second_sandbox_flag_after_the_pinned_one_is_named():
    composed = A.compose_reviewer("codex", model="m")["argv"]
    assert AL.reviewer_problems(composed + ["--sandbox", "danger-full-access"]) == \
        ["it runs with --sandbox danger-full-access, where ao pins read-only"]
