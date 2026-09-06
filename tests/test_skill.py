import json
import os
import re

from ao import cli, skillkit


def _subcommands():
    src = open(cli.__file__, encoding="utf-8").read()
    return sorted(set(re.findall(r'add_parser\("([a-z0-9-]+)"', src)))


def test_every_command_is_in_the_playbook():
    body = skillkit.playbook()[1]
    missing = [c for c in _subcommands() if f"`ao {c}" not in body]
    assert not missing, f"playbook lacks: {missing}"


def test_init_registers_mcp_and_writes_playbook(project, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, ".claude"))
    monkeypatch.setattr(skillkit.shutil, "which", lambda n: None)
    _, agents = skillkit.detect_agents(root)
    assert "claude-code" in agents
    assert skillkit.install_playbook(root, agents)[".claude/skills/ao/SKILL.md"] == "wrote"
    assert skillkit.register_mcp(root, agents, exe="/usr/local/bin/ao")["claude-code"] == "registered"
    cfg = json.load(open(os.path.join(root, ".mcp.json"), encoding="utf-8"))
    assert cfg["mcpServers"]["ao"]["args"] == ["-C", root, "mcp", "serve"]
    # idempotent, and other servers survive
    cfg["mcpServers"]["other"] = {"command": "x"}
    json.dump(cfg, open(os.path.join(root, ".mcp.json"), "w", encoding="utf-8"))
    assert skillkit.register_mcp(root, agents, exe="/usr/local/bin/ao")["claude-code"] == "kept"
    assert "other" in json.load(open(os.path.join(root, ".mcp.json"), encoding="utf-8"))["mcpServers"]
    assert skillkit.install_playbook(root, agents)[".claude/skills/ao/SKILL.md"] == "kept"
    skill = open(os.path.join(root, ".claude", "skills", "ao", "SKILL.md"), encoding="utf-8").read()
    assert skill.startswith("---\nname: ao\n") and skillkit.MARK_START in skill


def test_kiro_gets_steering_and_settings(project, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, ".kiro"))
    monkeypatch.setattr(skillkit.shutil, "which", lambda n: None)
    _, agents = skillkit.detect_agents(root)
    out = skillkit.install_playbook(root, agents)
    assert out[".kiro/steering/ao-playbook.md"] == "wrote"
    assert open(os.path.join(root, ".kiro", "steering", "ao-playbook.md"), encoding="utf-8").read().startswith("---\ninclusion: always")
    skillkit.register_mcp(root, agents, exe="/x/ao")
    assert "ao" in json.load(open(os.path.join(root, ".kiro", "settings", "mcp.json"), encoding="utf-8"))["mcpServers"]


def test_rule_files_are_not_written_unless_asked(project):
    root = project["root"]
    p = os.path.join(root, "AGENTS.md")
    open(p, "w", encoding="utf-8").write("# Agents\n\nkeep me\n")
    out = skillkit.install_playbook(root, {"generic"})
    assert "AGENTS.md" not in out and open(p, encoding="utf-8").read() == "# Agents\n\nkeep me\n"
    assert out[".ao/PLAYBOOK.md"] == "wrote"
    out = skillkit.install_playbook(root, {"generic"}, rules=True)
    out = skillkit.install_playbook(root, {"generic"}, rules=True)
    text = open(p, encoding="utf-8").read()
    assert text.count(skillkit.MARK_START) == 1 and "keep me" in text


def test_next_steps_tell_the_human_to_restart():
    lines = skillkit.next_steps({"claude-code"}, {"claude-code": "registered"})
    assert "restart" in lines[0].lower() and "Claude Code" in lines[0]


def test_doctor_problems_see_a_dead_watchdog(project, monkeypatch, tmp_path):
    from ao import lib as A, email, telegram
    monkeypatch.setattr(email, "CONF", str(tmp_path / "no-email.json"))
    monkeypatch.setattr(telegram, "CONF", str(tmp_path / "no-telegram.json"))
    root = project["root"]
    A.heartbeat(root)
    p = A.heartbeat_path(root)
    os.utime(p, (A.time.time() - 900, A.time.time() - 900))
    keys = [k for k, _ in cli.doctor_problems(project)]
    assert "watchdog-dead" in keys and "no-channel" in keys


def test_remove_undoes_init_and_only_removes_ao_owned_hooks(project, monkeypatch):
    from types import SimpleNamespace
    root = project["root"]
    os.makedirs(os.path.join(root, ".claude"))
    monkeypatch.setattr(skillkit.shutil, "which", lambda n: None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: None)
    _, agents = skillkit.detect_agents(root)
    skillkit.install_playbook(root, agents)
    skillkit.register_mcp(root, agents, exe="/x/ao")
    json.dump({"mcpServers": {"ao": {"command": "/x/ao"}, "other": {"command": "y"}}}, open(os.path.join(root, ".mcp.json"), "w", encoding="utf-8"))

    hooks = os.path.join(root, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    pre_commit = os.path.join(hooks, "pre-commit")
    pre_push = os.path.join(hooks, "pre-push")
    open(pre_commit, "w", encoding="utf-8").write(
        "#!/bin/sh\n# agent-orchestrator: fixture-owned hook\n"
    )
    foreign = "#!/bin/sh\necho foreign\n"
    open(pre_push, "w", encoding="utf-8").write(foreign)

    cli.cmd_remove(project, SimpleNamespace(yes=False))
    assert os.path.exists(os.path.join(root, ".ao"))
    assert os.path.exists(pre_commit)
    assert open(pre_push, encoding="utf-8").read() == foreign

    cli.cmd_remove(project, SimpleNamespace(yes=True))
    assert not os.path.exists(os.path.join(root, ".ao")) and not os.path.exists(os.path.join(root, ".claude", "skills", "ao"))
    assert not os.path.exists(pre_commit)
    assert open(pre_push, encoding="utf-8").read() == foreign
    assert json.load(open(os.path.join(root, ".mcp.json"), encoding="utf-8"))["mcpServers"] == {"other": {"command": "y"}}


def test_doctor_reports_both_hook_states_with_safe_ownership_check(
    project, monkeypatch, capsys
):
    from types import SimpleNamespace

    root = project["root"]
    paths = cli._ao_hook_paths(root)
    open(paths["pre-commit"], "w", encoding="utf-8").write(
        "#!/bin/sh\n# agent-orchestrator: fixture-owned hook\n"
    )
    open(paths["pre-push"], "w", encoding="utf-8").write(
        "#!/bin/sh\necho foreign\n"
    )
    monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)

    cli.cmd_doctor(project, SimpleNamespace(check=False))
    output = re.sub(r"\033\[[0-9;]*m", "", capsys.readouterr().out)

    assert re.search(r"^commit hook\s+installed$", output, re.M)
    assert re.search(r"^push hook\s+foreign$", output, re.M)
