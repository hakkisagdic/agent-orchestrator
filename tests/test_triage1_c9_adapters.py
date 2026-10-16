"""Project process layers and operation placeholders agree with their callers (TRIAGE-1-MEDIUM)."""
import json
from pathlib import Path
from types import SimpleNamespace
from ao import cli, lib as A


def _project_adapter(project):
    adapter = dict(A.load_adapter("kiro"), send={"argv": ["fixture-harness", "{prompt}"]})
    folder = Path(project["root"], ".ao", "adapters")
    folder.mkdir()
    (folder / "kiro.json").write_text(json.dumps(adapter), encoding="utf-8")
    return adapter


def test_c9_hold_reads_project_adapter(project, monkeypatch, capsys):
    _project_adapter(project)
    from ao import procs
    monkeypatch.setattr(procs, "all_pids", lambda: [321])
    monkeypatch.setattr(procs, "argv", lambda pid: ["fixture-harness", "prompt"])
    monkeypatch.setattr(procs, "cwd", lambda pid: project["root"])
    assert cli.cmd_hold(project, SimpleNamespace(action="status")) == 0
    assert "1 agent process(es)" in capsys.readouterr().out


def test_c9_fleet_reads_project_adapter(project, monkeypatch):
    adapter = _project_adapter(project)
    monkeypatch.setattr(A, "all_workspaces", lambda: [{"path": project["root"]}])
    monkeypatch.setattr(A, "busy", lambda cfg, actual: ("busy" if actual == adapter else "idle", 0, ""))
    assert cli._fleet_rows()[0]["state"] == "busy"


def test_c9_turn_placeholders_are_limited():
    adapter = dict(A.load_adapter("kiro"), resume={"argv": ["fixture", "{session}", "{prompt}", "--model={model}"]})
    assert any("resume.argv" in p and "model" in p for p in A.validate_adapter(adapter))
