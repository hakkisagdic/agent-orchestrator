import json
import os
import subprocess
import sys

import pytest

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
    monkeypatch.setattr(skillkit.shutil, "which", lambda name, *args, **kwargs: None)

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
    # Its own layer names the files ao wrote for it, and the removal names them and leaves them (REMOVE-SHIPPED).
    assert ".newcomer/rules/ao-coordination.md" not in paths
    assert ".newcomer/rules/ao-coordination.md" in skillkit.layer_ao_files(root)
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
    assert "../victim.md" not in skillkit.layer_ao_files(root) and "~/x.md" not in skillkit.layer_ao_files(root)
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


def test_a_project_adapter_is_one_agent_init_and_skill_accept_by_name(project):
    """HARNESS-SETUP-3: the parser's --agent names were read without the project, so its own adapters were refused."""
    from ao import cli
    root = project["root"]
    _declare(root)

    assert "newcomer" in skillkit.agent_choices(root) and "newcomer" not in skillkit.agent_choices()
    assert cli.main(["-C", root, "skill", "install", "--agent", "newcomer"]) == 0
    assert os.path.isfile(os.path.join(root, ".newcomer", "rules", "ao-playbook.md"))


def test_a_root_option_missing_its_directory_is_told_by_the_parser_proper(capsys):
    """HARNESS-SETUP-3: the project is read before the parser is built, and a -C with no directory is still told
    as `ao` tells it, not by that first reading."""
    from ao import cli
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-C"])

    assert stopped.value.code == 2 and capsys.readouterr().err.startswith("usage: ao ")


def test_a_harness_present_by_its_file_alone_gets_its_coordination(tmp_path, monkeypatch, capsys):
    """HARNESS-SETUP-3: init wrote the coordination only for a harness found by a directory, though the playbook
    went to every harness found."""
    from ao import cli
    from tests import conftest
    from tests.test_profiles import _init_args
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run([conftest.GIT, "init", "-q"], cwd=root, check=True)
    (root / "NEWCOMER.md").write_text("# rules\n", encoding="utf-8")
    _declare(str(root), dict(NEWCOMER, detect={"files": ["NEWCOMER.md"]}))

    assert cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="auto")) == 0, capsys.readouterr().out

    assert (root / ".newcomer" / "rules" / "ao-playbook.md").is_file()
    assert (root / ".newcomer" / "rules" / "ao-coordination.md").is_file()


def test_a_name_resolves_through_the_vendor_list_and_rule_files_follow_what_is_declared(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(skillkit.shutil, "which", lambda name, *args, **kwargs: None)

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
    monkeypatch.setattr(skillkit.shutil, "which", lambda name, *args, **kwargs: None)

    assert skillkit.detect_agents(root, "all")[1] >= {"claude-code", "kiro", "codex"}
    assert skillkit.rule_file_names(root) == ["CLAUDE.md", "AGENTS.md"]
    paths, mcp_files = skillkit.ao_files(root)
    assert paths[0] == ".claude/skills/ao/" and ".kiro/steering/ao-machine.md" in paths
    assert (".mcp.json", "ao", True) in mcp_files
    manual = skillkit.register_mcp(root, {"codex"}, exe="/x/ao")["codex"]
    assert manual.splitlines()[0] == "manual: add to ~/.codex/config.toml"
    assert 'args = ["-C", "%s", "mcp", "serve"]' % root.replace("\\", "\\\\") in manual    # a TOML string (#71)


def test_a_path_only_a_layer_names_as_ao_s_is_named_by_the_removal_and_never_deleted(project, capsys):
    """REMOVE-SHIPPED: a project adapter an agent wrote, its `ao_files` naming the source, had `ao remove --yes`
    delete it; a layer that overrides a shipped adapter adds nothing to what is deleted either."""
    from ao import cli
    root = project["root"]
    os.makedirs(os.path.join(root, "src"))
    open(os.path.join(root, "src", "app.py"), "w", encoding="utf-8").write("print('mine')\n")
    open(os.path.join(root, "README.md"), "w", encoding="utf-8").write("# mine\n")
    _declare(root, dict(NEWCOMER, directives=dict(NEWCOMER["directives"], ao_files=["src/", "README.md"])))
    kiro = json.load(open(os.path.join(cli.A.adapters_dir(), "kiro.json"), encoding="utf-8"))
    kiro["directives"]["ao_files"] = kiro["directives"]["ao_files"] + ["src/app.py"]
    _declare(root, kiro)

    paths, _ = skillkit.ao_files(root)
    assert "src/" not in paths and "README.md" not in paths and "src/app.py" not in paths
    assert ".kiro/steering/ao-playbook.md" in paths                       # the shipped adapter's own, as before
    assert {"src/", "README.md", "src/app.py"} <= set(skillkit.layer_ao_files(root))

    args = cli.build_parser().parse_args(["remove"])
    assert cli.cmd_remove(project, args) == 0

    out = capsys.readouterr().out
    plan = out.split("hook files only when", 1)[0]
    assert "   src/\n" not in plan and "README.md" not in plan
    assert "left: src/, which only a user's or a project's adapter layer names as ao's" in out
    assert "left: README.md, which only" in out and "left: src/app.py, which only" in out
