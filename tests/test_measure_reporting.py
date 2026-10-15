"""An explicit git wrapper and an unrecognised shell hook must be reported (TRIAGE-2-LOW)."""
import json
import sys
from pathlib import Path

from ao import lib as A


def test_measurement_filters_names_an_explicit_script(project, tmp_path, monkeypatch):
    wrapper = tmp_path / "git-wrapper"
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setenv("AO_GIT", str(wrapper))

    lines = A.measurement_filters(project["root"])

    assert any("AO_GIT" in line and str(wrapper) in line and "not a compiled program" in line for line in lines)
    monkeypatch.setenv("AO_GIT", sys.executable)
    assert not any("AO_GIT" in line for line in A.measurement_filters(project["root"]))


def test_measurement_filters_names_unrecognised_shell_hooks(project, monkeypatch):
    monkeypatch.delenv("AO_GIT", raising=False)
    path = Path(project["root"], ".claude", "settings.json")
    path.parent.mkdir()
    path.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "hooks/check.sh"}]},
        {"matcher": "Read", "hooks": [{"type": "command", "command": "hooks/read.sh"}]}]}}), encoding="utf-8")

    lines = A.measurement_filters(project["root"])

    assert any(str(path) in line and "hooks/check.sh" in line and "unverified" in line for line in lines)
    assert not any("hooks/read.sh" in line for line in lines)
