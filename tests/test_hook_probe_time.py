"""The commit hook's proof waits for the hook as long as the machine says (HOOK-PROBE-TIME).

`ao init`, `ao hooks install` and the doctor prove ao's pre-commit hook by having Git run it on a synthetic
candidate. The hook is ao itself - a Python start and a commit check - and the proof gave it 15 seconds. On
2026-10-12, with the machine's load average at 85 from other projects' containers, the run took longer: an
installed hook was reported unproven, `ao init --adopt` exited 1, and the push whose suite ran it was refused.
The wait is `hooks.probe_timeout_seconds` now, a machine setting, 60 seconds unless a person sets another.
"""
from types import SimpleNamespace

from ao import cli
from tests.test_hook_paths import _make_current_ao_available
from tests.test_init_adoption import _init, _legacy


def test_the_proof_waits_for_the_hook_as_long_as_the_machine_setting_says(tmp_path, monkeypatch):
    root, cfg, _ = _legacy(tmp_path, monkeypatch)
    _make_current_ao_available(monkeypatch, tmp_path)
    waited, run = [], cli._hook_git

    def recorded(cwd, *args, **kwargs):
        if args[:2] == ("hook", "run"):
            waited.append(kwargs.get("timeout"))
        return run(cwd, *args, **kwargs)

    monkeypatch.setattr(cli, "_hook_git", recorded)
    assert cli.cmd_init(cfg, _init("--adopt")) == 0
    set_by = SimpleNamespace(action="set", key="hooks.probe_timeout_seconds", value="240", machine=True, by=None)
    assert cli.cmd_config(cfg, set_by) == 0

    assert cli._hook_execution_probe(cli._ao_hook_inventory(str(root)))["installed"] is True
    assert waited == [60, 240]
