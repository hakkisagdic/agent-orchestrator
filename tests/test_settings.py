import ast
import json
import os
import pathlib
import re
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, settings as S, watchdog as W

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    path = tmp_path / "machine-settings.json"
    monkeypatch.setenv("AO_SETTINGS", str(path))
    return path


def _args(action, key=None, value=None, machine=False):
    return SimpleNamespace(action=action, key=key, value=value, machine=machine)


def _project_file(root):
    return json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))


def test_a_project_setting_comes_from_the_project_then_the_machine_then_its_default(project, machine):
    cfg = dict(project)
    assert S.resolve(cfg, "round_budget") == (5, "default", None)

    machine.write_text(json.dumps({"round_budget": 4}))
    assert S.resolve(cfg, "round_budget") == (4, "machine", None)

    cfg["round_budget"] = 2
    assert S.resolve(cfg, "round_budget") == (2, "project", None)

    cfg["round_budget"] = "two"
    value, source, problem = S.resolve(cfg, "round_budget")
    assert (value, source) == (4, "machine") and "'two'" in problem


def test_a_machine_setting_is_never_read_from_a_project(project, machine):
    cfg = dict(project, quota={"block_percent": 50})

    assert S.get(cfg, "quota.block_percent") == 97
    assert any(key == "quota.block_percent" and "--machine" in text for key, text in S.problems(cfg))


def test_ao_config_sets_gets_lists_and_unsets(project, machine, capsys):
    root = project["root"]
    assert cli.cmd_config(project, _args("set", "round_budget", "3")) == 0
    assert _project_file(root)["round_budget"] == 3
    cfg = dict(A.load_config(root), root=root)
    capsys.readouterr()
    assert cli.cmd_config(cfg, _args("get", "round_budget")) == 0
    assert capsys.readouterr().out.strip() == "3"

    assert cli.cmd_config(cfg, _args("set", "alarms.red_after_minutes", "30")) == 0
    assert _project_file(root)["alarms"] == {"red_after_minutes": 30}
    assert _project_file(root)["implementer"] == project["implementer"]      # nothing else moved

    assert cli.cmd_config(cfg, _args("set", "quota.block_percent", "90")) == 2
    assert cli.cmd_config(cfg, _args("set", "quota.block_percent", "90", machine=True)) == 0
    assert json.loads(machine.read_text()) == {"quota": {"block_percent": 90}}
    assert cli.cmd_config(cfg, _args("set", "round_budget", "lots")) == 2
    assert cli.cmd_config(cfg, _args("set", "quota.block_percent", "101", machine=True)) == 2
    assert cli.cmd_config(cfg, _args("set", "no_such_setting", "1")) == 2

    assert cli.cmd_config(cfg, _args("unset", "round_budget")) == 0
    assert "round_budget" not in _project_file(root)
    capsys.readouterr()
    assert cli.cmd_config(dict(A.load_config(root), root=root), _args("list")) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in S.SETTINGS)


def test_what_is_set_is_what_ao_uses(project, machine, monkeypatch):
    cfg = dict(project, review_timeout=42, fanout={"max_agents": 2},
               implementer=dict(project["implementer"], name="dev"))
    assert cli._review_timeout(cfg) == 42
    assert A.fanout_config(cfg) == {"max_agents": 2, "per_agent_tokens": 50_000, "window_reserve_pct": 30}
    assert A.mail_names(cfg) == ("dev", "fable")

    machine.write_text(json.dumps({"quota": {"block_percent": 50}, "fleet": {"window_reserve_pct": 10}}))
    monkeypatch.setattr(A, "sh", lambda *args, **kwargs: "")
    monkeypatch.setattr(A, "quota", lambda adapter, ttl=300: ["claude 60% of the window"])
    assert W.quota_ok({}) is False
    assert A.fleet_reserve() == 10


def test_an_alarm_episode_ends_when_the_machine_setting_says(project, machine):
    now = time.time()
    A.alarm_touch("proj", "standing", "orange", now=now - 3 * 3600)
    assert A.active_alarms("proj", now=now) == []

    machine.write_text(json.dumps({"alarms": {"reset_after_hours": 4}}))
    assert [episode["key"] for episode in A.active_alarms("proj", now=now)] == ["standing"]


def test_the_doctor_names_a_setting_that_is_not_used(project, machine):
    cfg = dict(project, round_budget=0, alarms={"red_after_minute": 30})

    keys = [key for key, _ in cli.doctor_problems(cfg)]

    assert "setting:round_budget" in keys and "setting:alarms.red_after_minute" in keys


def test_the_documentation_lists_every_setting_with_its_default():
    text = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| `([a-z_.]+)` \| `([^`]*)` \|", text, re.M))

    assert set(rows) == set(S.SETTINGS)
    for key, shown in rows.items():
        default = S.SETTINGS[key].default
        assert shown == ("none" if default == [] else str(default)), key


def test_every_setting_the_docs_name_is_one_ao_reads():
    groups = sorted({key.split(".")[0] for key in S.SETTINGS if "." in key} - {"implementer", "architect"})
    pattern = re.compile(r"`((?:%s)\.[a-z_]+|round_budget|review_timeout)`" % "|".join(groups))
    named = set()
    for doc in (ROOT / "docs").glob("*.md"):
        if doc.name not in ("backlog.md", "lessons.md"):
            named.update(pattern.findall(doc.read_text(encoding="utf-8")))

    assert named and named <= set(S.SETTINGS), sorted(named - set(S.SETTINGS))


def test_no_module_reads_a_threshold_with_a_default_of_its_own():
    numeric = {key.rsplit(".", 1)[-1] for key, spec in S.SETTINGS.items() if spec.kind in (int, float)}
    found = []
    for path in sorted((ROOT / "src" / "ao").glob("*.py")):
        if path.name == "settings.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                    and len(node.args) == 2 and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value in numeric and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, (int, float)):
                found.append(f"{path.name}:{node.lineno} .get({node.args[0].value!r}, {node.args[1].value!r})")

    assert found == []
