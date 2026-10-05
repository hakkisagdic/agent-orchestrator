import json
import os
from types import SimpleNamespace

import pytest

from ao import allowlist, cli, lib as A


def test_every_adapter_declares_whether_it_can_deny_tools_and_no_composed_reviewer_grants_one():
    eligible = []
    for ident, entry in A.adapter_catalog().items():
        options = entry["adapter"].get("options") or {}
        assert "trust_none" in options, ident
        ok, why = A.reviewer_eligibility(entry["adapter"])
        if not ok:
            assert why, ident
            with pytest.raises(ValueError, match="ineligible for the reviewer role"):
                A.compose_reviewer(ident)
            continue
        route = A.compose_reviewer(ident, model="some-model")
        assert allowlist.reviewer_problems(route["argv"]) == [], (ident, route["argv"])
        assert not set(route["argv"]) & set(options.get("trust_all") or []), ident
        eligible.append(ident)
    assert {"kiro", "claude-code"} <= set(eligible)


@pytest.mark.parametrize("implementer,reviewer", [("kiro", "claude-code"), ("claude-code", "kiro"),
                                                  ("codex", "claude-code")])
def test_three_harness_pairs_hold_the_two_roles_with_no_hand_written_argv(project, implementer, reviewer):
    cfg = dict(project, implementer={"adapter": implementer, "session": "s1", "name": "dev"})
    route = A.compose_reviewer(reviewer, model="some-model")

    assert cli._reviewer_ineligible(cfg, route) is None
    assert route["composed"] is True and route["adapter"] == reviewer


def test_ao_role_names_an_adapter_and_a_model_and_the_doctor_names_an_ineligible_one(project, capsys):
    root = project["root"]
    stored = {key: value for key, value in project.items() if key != "root"}
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    args = SimpleNamespace(action="set", role="reviewer", actor="claude-code", model="some-model", effort=None,
                           hotfix=False)

    assert cli.cmd_role(A.load_config(root), args) == 0

    cfg = A.load_config(root)
    assert cfg["reviewer"]["actor"] == "claude-code-reviewer-some-model"
    assert cfg["reviewer"]["argv"][-3:] == ["--allowedTools", "Read,Grep,Glob", "--strict-mcp-config"]
    bad = SimpleNamespace(**dict(vars(args), actor="aider"))
    assert cli.cmd_role(cfg, bad) == 2
    assert "aider is ineligible for the reviewer role" in capsys.readouterr().out
    problems = dict(cli.doctor_problems(dict(cfg, reviewer={"adapter": "aider", "argv": ["aider", "{prompt}"]})))
    assert "reviewer-ineligible:aider" in problems


def test_codex_reviews_in_its_read_only_sandbox_without_the_persons_config():
    """CODEX-REVIEWER: measured on codex-cli 0.160.0, its read-only sandbox read a file and refused a write and the
    network; the person's config.toml offered the model computer use, a browser and web search."""
    route = A.compose_reviewer("codex", model="gpt-6-luna", effort="high", family="openai")
    argv = route["argv"]

    assert argv[:3] == ["codex", "exec", "{prompt}"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert {"--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--ephemeral"} <= set(argv)
    assert argv[argv.index("-c") + 1] == "model_reasoning_effort=high"
    assert allowlist.reviewer_problems(argv) == [] and A.pin_conflicts(argv, "reviewer") == []


def test_a_codex_reviewer_that_loads_the_persons_config_or_may_write_is_named(project):
    plain = ["codex", "exec", "{prompt}", "--sandbox", "read-only"]
    writing = ["codex", "exec", "{prompt}", "--ignore-user-config", "--sandbox", "workspace-write"]

    assert "it starts every configured MCP server (no --ignore-user-config)" in allowlist.reviewer_problems(plain)
    assert allowlist.reviewer_problems(writing) == ["it runs with --sandbox workspace-write, where ao pins read-only"]
    assert A.pinned_argv(["codex", "exec", "{prompt}", "--ignore-user-config"], "reviewer")[1] == \
        ["--sandbox", "read-only"]
    problems = dict(cli.doctor_problems(dict(project, reviewer={"adapter": "codex", "family": "openai",
                                                                "argv": plain})))
    assert any("no --ignore-user-config" in str(text) for text in problems.values()), problems
