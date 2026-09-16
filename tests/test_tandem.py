import json
import os
from types import SimpleNamespace

from ao import cli, lib as A

ACTORS = {
    "kiro": {"adapter": "kiro", "session": "s1", "name": "kiro"},
    "fable": {"adapter": "claude-code", "session": "auto", "name": "fable", "argv": ["claude", "-p", "{prompt}"]},
    "r1": {"id": "r1", "argv": ["claude", "-p", "{prompt}"]},
}


def _table(project, kind=None):
    root = project["root"]
    stored = {key: value for key, value in project.items() if key != "root"}
    stored.update(actors=ACTORS, roles={"implementer": "kiro", "architect": "fable", "reviewer": "r1"})
    if kind:
        stored["repository"] = {"kind": kind}
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    return A.load_config(root)


def _set(cfg, role, actor, hotfix=False):
    return cli.cmd_role(cfg, SimpleNamespace(action="set", role=role, actor=actor, hotfix=hotfix))


def test_a_product_repository_refuses_the_architect_as_implementer_unless_named_a_hotfix(project, capsys):
    cfg = _table(project)

    assert _set(cfg, "implementer", "fable") == 2
    assert "on a product repository the architect does not implement" in capsys.readouterr().out
    assert _set(cfg, "implementer", "fable", hotfix=True) == 0


def test_on_a_tool_repository_roles_rotate_and_the_reviewer_is_never_the_author(project):
    root = project["root"]
    cfg = _table(project, kind="tool")
    assert cli._reviewer_ineligible(cfg, ACTORS["r1"]) is None      # kiro implements; a claude reviewer may review

    assert _set(cfg, "implementer", "fable") == 0

    rotated = A.load_config(root)
    assert rotated["implementer"]["actor"] == "fable"
    assert "the implementer's own engine (claude)" in cli._reviewer_ineligible(rotated, ACTORS["r1"])
    assert _set(rotated, "reviewer", "fable") == 2
