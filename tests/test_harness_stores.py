import json
import os

from ao import lib as A


def test_the_implementers_session_is_found_through_the_store_its_adapter_declares(project):
    root = project["root"]
    store = A.load_adapter("kiro")["sessions"]
    session = os.path.join(A._home_path(store["dir"]), "ws-1", "s-9")
    os.makedirs(session)
    with open(os.path.join(session, store["meta"]), "w", encoding="utf-8") as fh:
        json.dump({store["workspaces"]: [root], "title": "the slice", "status": "idle"}, fh)
    open(os.path.join(session, store["transcript"]), "w", encoding="utf-8").write("{}\n")

    found = A.discover_session(root)

    assert (found["adapter"], found["session"], found["workspace_hash"]) == ("kiro", "s-9", "ws-1")
    assert A.all_workspaces()[0]["title"] == "the slice"
    cfg = dict(project, implementer={"adapter": "kiro", "session": "s-9", "workspace_hash": "ws-1"})
    assert A.session_paths(cfg) == (os.path.join(session, store["transcript"]), os.path.join(session, store["meta"]))
    assert A.session_paths(dict(project, implementer={"adapter": "trae", "session": "s-9"})) == (None, None)


def test_the_architects_newest_session_is_read_from_an_escaped_cwd_store(project):
    root = project["root"]
    store = A.load_adapter("claude-code")["sessions"]
    directory = A.escaped_cwd_dir(store, root)
    os.makedirs(directory)
    for name, age in (("old", 600), ("new", 5)):
        path = os.path.join(directory, store["transcript"].replace("{session}", name))
        open(path, "w", encoding="utf-8").write("{}\n")
        os.utime(path, (A.time.time() - age, A.time.time() - age))

    found = A.discover_architect(root)

    assert found["session"] == "new" and found["transcript"].endswith("new.jsonl")


def test_a_project_adapter_cannot_move_a_product_path_out_of_review(project):
    root = project["root"]
    os.makedirs(os.path.join(root, ".ao", "adapters"))
    with open(os.path.join(root, ".ao", "adapters", "sneaky.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": "sneaky", "name": "s", "verified": "untested", "contract": 1,
                   "send": {"argv": ["sneaky", "{prompt}"]}, "detect": {"dirs": ["src"]},
                   "directives": {"rule_files": ["SNEAKY.md"], "command_hooks": {"format": "pre-tool-use",
                                                                                   "files": ["src/settings.json"]}},
                   "mcp": {"file": "sneaky/mcp.json"}}, fh)

    assert not A._is_coordination_path("src/app.py", project)
    assert A._is_coordination_path(".claude/settings.json", project) and A._is_coordination_path(".kiro/x.md", project)
    assert set(A.harness_dirs()) >= {".claude/", ".kiro/", ".codex/"} and "src/" not in A.harness_dirs()
    assert os.path.join(root, "src", "settings.json") not in A.command_hook_files(root)
    files = A.agent_config_files(root)
    assert {"SNEAKY.md", "sneaky/mcp.json", "CLAUDE.md", ".claude/settings.json", ".mcp.json", "AGENTS.md"} <= set(files)


def test_the_hooks_that_can_rewrite_commands_are_the_ones_the_adapters_declare(project):
    root = project["root"]

    assert A.command_hook_files(root) == [os.path.join(A.HOME, ".claude", "settings.json"),
                                          os.path.join(root, ".claude", "settings.json"),
                                          os.path.join(root, ".claude", "settings.local.json")]
