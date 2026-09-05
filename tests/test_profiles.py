import json
import os
from types import SimpleNamespace

from ao import cli


def _bare(root):
    json.dump({"project": "proj", "round_budget": 5}, open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8"))


def test_profile_writes_role_blocks_and_keeps_existing(project):
    root = project["root"]
    _bare(root)
    args = SimpleNamespace(profile="claude-claude", implementer=None, model=None, effort=None, reviewer_model=None)
    assert cli._apply_profile(root, args) == ["implementer", "reviewer", "architect"]
    cfg = json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))
    assert cfg["implementer"]["adapter"] == "claude-code" and cfg["implementer"]["model"] == "claude-sonnet-5"
    assert "--model" in cfg["reviewer"]["argv"] and "claude-opus-5" in cfg["reviewer"]["argv"]
    assert cfg["architect"]["session"] == "auto" and "Bash(ao:*)" in cfg["architect"]["argv"][-1]
    # second run: nothing overwritten
    cfg["implementer"]["model"] = "custom"
    json.dump(cfg, open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8"))
    assert cli._apply_profile(root, args) == []
    assert json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))["implementer"]["model"] == "custom"


def test_kiro_profile_with_effort(project):
    root = project["root"]
    _bare(root)
    args = SimpleNamespace(profile="claude-kiro", implementer=None, model=None, effort="high", reviewer_model=None)
    cli._apply_profile(root, args)
    cfg = json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))
    assert cfg["implementer"] == {"adapter": "kiro", "session": "auto", "name": "kiro", "effort": "high"}
