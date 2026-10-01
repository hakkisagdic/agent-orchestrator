import json
import os
import sys

from ao import skillkit

NEWCOMER = {
    "id": "newcomer", "name": "Newcomer Agent", "verified": "untested", "contract": 1,
    "disclaimer": "a fixture: a harness no code names",
    "send": {"argv": ["newcomer", "{prompt}"]},
    "options": {"trust_none": None, "trust_none_why": "a fixture"},
    "detect": {"dirs": [".newcomer"], "binaries": ["newcomer"]},
    "directives": {"steering_dir": ".newcomer/rules",
                   "playbook": {"path": ".newcomer/rules/ao-playbook.md", "header": "<!-- always -->\n\n"},
                   "coordination": ".newcomer/rules/ao-coordination.md",
                   "rule_files": ["NEWCOMER.md"],
                   "ao_files": [".newcomer/rules/ao-playbook.md", ".newcomer/rules/ao-coordination.md"]},
    "mcp": {"file": ".newcomer/mcp.json", "key": "servers", "extra": {"transport": "stdio"}},
}


def _declare(root, adapter=NEWCOMER):
    directory = os.path.join(root, ".ao", "adapters")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, adapter["id"] + ".json"), "w", encoding="utf-8") as fh:
        json.dump(adapter, fh)


def test_a_harness_no_code_names_is_detected_and_set_up_from_its_adapter_alone(project, monkeypatch):
    root = project["root"]
    _declare(root)
    os.makedirs(os.path.join(root, ".newcomer"))
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)

    _, agents = skillkit.detect_agents(root)
    assert agents == {"newcomer"}

    written = skillkit.install_playbook(root, agents)
    assert written[".newcomer/rules/ao-playbook.md"] == "wrote"
    text = open(os.path.join(root, ".newcomer", "rules", "ao-playbook.md"), encoding="utf-8").read()
    assert text.startswith("<!-- always -->\n\n" + skillkit.MARK_START)
    assert skillkit.register_mcp(root, agents, exe="/x/ao") == {"newcomer": "registered"}
    servers = json.load(open(os.path.join(root, ".newcomer", "mcp.json"), encoding="utf-8"))["servers"]
    assert servers["ao"] == {"command": "/x/ao", "args": ["-C", root, "mcp", "serve"], "transport": "stdio"}

    assert skillkit.rule_file_names(root)[-1] == "AGENTS.md" and "NEWCOMER.md" in skillkit.rule_file_names(root)
    assert ".newcomer/rules" in skillkit.steering_dirs(root)
    paths, mcp_files = skillkit.ao_files(root)
    assert ".newcomer/rules/ao-coordination.md" in paths
    assert (os.path.join(".newcomer", "mcp.json"), "ao", False) in mcp_files

    assert skillkit.rules_wired(root) is True          # its steering directory holds the playbook


def test_a_setup_path_that_leaves_the_project_is_written_nowhere_and_never_removed(project, tmp_path):
    """HARNESS-SETUP-2: the project's adapter layer is one an agent can write, and a playbook, a rule file, an MCP
    file or a file ao removes declared outside the project was written there, and deleted there."""
    root = project["root"]
    outside = dict(NEWCOMER, directives=dict(NEWCOMER["directives"], playbook={"path": "../victim.md"},
                                             rule_files=["../RULES.md"], ao_files=["../victim.md", "~/x.md"]),
                   mcp={"file": "~/.config/newcomer/mcp.json"})
    _declare(root, outside)
    os.makedirs(os.path.join(root, ".newcomer"))

    written = skillkit.install_playbook(root, {"newcomer"}, rules=True)
    registered = skillkit.register_mcp(root, {"newcomer"}, exe="/x/ao")

    parent = os.path.dirname(os.path.realpath(root))
    assert written["../victim.md"] == "not written: outside the project"
    assert not os.path.exists(os.path.join(parent, "victim.md")) and not os.path.exists(os.path.join(parent, "RULES.md"))
    assert "../RULES.md" not in skillkit.rule_file_names(root)
    assert registered["newcomer"].startswith("not written: ~/.config/newcomer/mcp.json lies outside the project")
    assert not os.path.exists(os.path.join(root, "~"))
    paths, mcp_files = skillkit.ao_files(root)
    assert "../victim.md" not in paths and "~/x.md" not in paths
    assert all(not path.startswith("~") for path, _, _ in mcp_files)


def test_a_project_layer_names_no_command_ao_runs_to_register_its_server(project):
    """HARNESS-SETUP-2: a project adapter's `mcp.register` argv ran during init, a command an agent chose."""
    root = project["root"]
    marker = os.path.join(root, "ran")
    _declare(root, dict(NEWCOMER, mcp=dict(NEWCOMER["mcp"], register=[
        sys.executable, "-c", f"open({marker!r}, 'w').close()"])))
    os.makedirs(os.path.join(root, ".newcomer"))

    assert skillkit.register_mcp(root, {"newcomer"}, exe="/x/ao") == {"newcomer": "registered"}

    assert not os.path.exists(marker)
    with open(os.path.join(root, ".newcomer", "mcp.json"), encoding="utf-8") as fh:
        assert "ao" in json.load(fh)["servers"]


def test_a_name_resolves_through_the_vendor_list_and_rule_files_follow_what_is_declared(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)

    assert skillkit.detect_agents(root, "claude") == ("claude-code", {"claude-code"})
    assert {"claude", "claude-code", "kiro", "auto", "all"} <= set(skillkit.agent_choices())

    open(os.path.join(root, "AGENTS.md"), "w", encoding="utf-8").write("# Agents\n")
    _declare(root)
    out = skillkit.install_playbook(root, {"newcomer"}, rules=True)
    assert "AGENTS.md" in out and not os.path.exists(os.path.join(root, "NEWCOMER.md"))
    out = skillkit.install_playbook(root, {"generic"}, rules=True)
    assert out["AGENTS.md"] == "kept"


def test_the_shipped_harnesses_declare_what_init_used_to_hardcode(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name: None)

    assert skillkit.detect_agents(root, "all")[1] >= {"claude-code", "kiro", "codex"}
    assert skillkit.rule_file_names(root) == ["CLAUDE.md", "AGENTS.md"]
    paths, mcp_files = skillkit.ao_files(root)
    assert paths[0] == ".claude/skills/ao/" and ".kiro/steering/ao-machine.md" in paths
    assert (".mcp.json", "ao", True) in mcp_files
    manual = skillkit.register_mcp(root, {"codex"}, exe="/x/ao")["codex"]
    assert manual.splitlines()[0] == "manual: add to ~/.codex/config.toml"
    assert 'args = ["-C", "%s", "mcp", "serve"]' % root.replace("\\", "\\\\") in manual    # a TOML string (#71)
