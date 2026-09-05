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
    cfg = json.load(open(os.path.join(root, ".mcp.json")))
    assert cfg["mcpServers"]["ao"]["args"] == ["-C", root, "mcp", "serve"]
    # idempotent, and other servers survive
    cfg["mcpServers"]["other"] = {"command": "x"}
    json.dump(cfg, open(os.path.join(root, ".mcp.json"), "w"))
    assert skillkit.register_mcp(root, agents, exe="/usr/local/bin/ao")["claude-code"] == "kept"
    assert "other" in json.load(open(os.path.join(root, ".mcp.json")))["mcpServers"]
    assert skillkit.install_playbook(root, agents)[".claude/skills/ao/SKILL.md"] == "kept"
    skill = open(os.path.join(root, ".claude", "skills", "ao", "SKILL.md")).read()
    assert skill.startswith("---\nname: ao\n") and skillkit.MARK_START in skill


def test_kiro_gets_steering_and_settings(project, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, ".kiro"))
    monkeypatch.setattr(skillkit.shutil, "which", lambda n: None)
    _, agents = skillkit.detect_agents(root)
    out = skillkit.install_playbook(root, agents)
    assert out[".kiro/steering/ao-playbook.md"] == "wrote"
    assert open(os.path.join(root, ".kiro", "steering", "ao-playbook.md")).read().startswith("---\ninclusion: always")
    skillkit.register_mcp(root, agents, exe="/x/ao")
    assert "ao" in json.load(open(os.path.join(root, ".kiro", "settings", "mcp.json")))["mcpServers"]


def test_agents_md_section_is_replaced_not_duplicated(project):
    root = project["root"]
    p = os.path.join(root, "AGENTS.md")
    open(p, "w").write("# Agents\n\nkeep me\n")
    skillkit.install_playbook(root, {"generic"})
    skillkit.install_playbook(root, {"generic"})
    text = open(p).read()
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
