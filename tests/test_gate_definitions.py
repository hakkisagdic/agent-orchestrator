import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import cli, features as F, lib as A, storage


def _gates(root, command):
    spec = {"gates": {"smoke": {"run": command, "expect": "exit_zero", "timeout": 30}},
            "profiles": {"quick": ["smoke"]}, "default_profile": "quick"}
    open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8").write(json.dumps(spec))


def _command(*code):
    argv = [sys.executable, "-c", "; ".join(code)]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _verified_candidate(project, tmp_path, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("value = 1\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)
    _gates(root, _command("import sys", "sys.exit(0)"))
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    monkeypatch.setattr(A, "plan_drift", lambda root: [])
    monkeypatch.setattr(A, "hold_state", lambda root: None)
    monkeypatch.setattr(A, "urgent_messages", lambda *args, **kwargs: [])
    F.set_switch(root, "review", False)
    cfg = A.load_config(root)
    assert cli.cmd_verify(cfg, SimpleNamespace(profile="quick", wait=0)) == 0
    return cfg


def test_a_verification_names_the_definitions_it_ran(project, tmp_path, monkeypatch):
    root = project["root"]
    _verified_candidate(project, tmp_path, monkeypatch)

    record = A.latest_verification(root)
    assert record["gates_digest"] == A.gate_definitions_digest(root, "quick")
    assert record["gates_digest"].startswith("sha256:")
    assert [gate["run"] for gate in record["gates"]] == [_command("import sys", "sys.exit(0)")]


def test_gates_rewritten_between_verify_and_commit_ok_refuse_the_grant(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    cfg = _verified_candidate(project, tmp_path, monkeypatch)
    verified = A.latest_verification(root)["id"]
    _gates(root, _command("pass"))
    capsys.readouterr()

    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 1
    assert f"gate definitions changed since {verified}" in capsys.readouterr().out


def test_gates_rewritten_after_the_grant_fail_commit_check(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    cfg = _verified_candidate(project, tmp_path, monkeypatch)
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0
    _gates(root, _command("pass"))
    capsys.readouterr()

    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 1
    assert "gate definitions changed since the grant's verification" in capsys.readouterr().out


@pytest.mark.parametrize("link", [None, "sha256:" + "0" * 64], ids=["no-link", "wrong-link"])
def test_a_forged_verification_row_makes_the_ledger_unreadable(project, tmp_path, monkeypatch, capsys, link):
    root = project["root"]
    cfg = _verified_candidate(project, tmp_path, monkeypatch)
    forged = dict(A.latest_verification(root), id="V-forged")
    forged.pop("previous")
    forged.pop("ordinal")
    if link:
        forged["previous"] = link
    with open(os.path.join(root, ".ao", "ledger", "verifications.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(forged) + "\n")
    capsys.readouterr()

    with pytest.raises(storage.LedgerCorruption):
        A.latest_verification(root)
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 1
    assert "verification ledger is unreadable" in capsys.readouterr().out


def test_rows_from_before_the_chain_stay_history_and_never_count(project):
    root = project["root"]
    path = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    legacy = [{"id": "V-1", "passed": True}, {"id": "V-2", "passed": True}]
    open(path, "w", encoding="utf-8").write("".join(json.dumps(row) + "\n" for row in legacy))

    assert A.latest_verification(root) is None
    A.record_verification(root, {"id": "V-3", "passed": True})
    assert A.latest_verification(root)["id"] == "V-3"
    assert [row["id"] for row in storage.read_jsonl(path)] == ["V-1", "V-2", "V-3"]

    rows = open(path, encoding="utf-8").read().splitlines()
    rows[1] = json.dumps({"id": "V-2", "passed": False})
    open(path, "w", encoding="utf-8").write("\n".join(rows) + "\n")
    with pytest.raises(storage.LedgerCorruption, match="broken hash chain"):
        A.latest_verification(root)
