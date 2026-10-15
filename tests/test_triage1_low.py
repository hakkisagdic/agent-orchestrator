"""Exact grants and evidence bounds went unnamed; plans were called sealed (TRIAGE-1-LOW)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, lib as A, settings as S, storage


@pytest.mark.parametrize("rule", [
    "Bash(ao config set round_budget 999)",
    "Bash(ao config set round_budget 999 --machine)",
    "Bash(ao config unset round_budget)",
    "Bash(env ao config unset round_budget --machine)",
])
def test_implementer_settings_grants_are_named_whatever_the_value(rule):
    argv = ["agent", "--allowedTools", rule]
    found = AL.problems(argv, role="implementer")
    assert any("setting" in reason and granted == rule for reason, _, granted in found)
    assert AL.problems(argv, role="architect") == []


@pytest.mark.parametrize("role", ["implementer", "architect"])
def test_machine_role_names_are_known_groups(project, tmp_path, monkeypatch, role):
    machine = tmp_path / "settings.json"
    monkeypatch.setenv("AO_SETTINGS", str(machine))
    machine.write_text(json.dumps({role: {"name": "dev"}}), encoding="utf-8")
    assert S.problems(project) == []
    machine.write_text(json.dumps({role: {"name": "dev", "typo": "unknown"}}), encoding="utf-8")
    assert [key for key, _ in S.problems(project)] == [role + ".typo"]


@pytest.mark.parametrize("name,chain,legacy", [
    ("verifications", A.VERIFICATION_CHAIN, True),
    ("authority", A.AUTHORITY_CHAIN, False),
])
def test_doctor_names_live_evidence_rows_past_the_bound(project, name, chain, legacy):
    cfg = dict(project, retention={"evidence_keep": 500})
    path = Path(project["root"]) / ".ao" / "ledger" / (name + ".jsonl")
    for n in range(500):
        storage.append_chained_jsonl(str(path), {"id": str(n)}, chain, legacy_prefix=legacy)
    assert name + ".jsonl" not in "\n".join(cli._store_bound_lines(cfg))
    storage.append_chained_jsonl(str(path), {"id": "500"}, chain, legacy_prefix=legacy)
    line = "\n".join(cli._store_bound_lines(cfg))
    assert name + ".jsonl 501/500 rows" in line
    assert "ao prune --evidence --yes" in line
    storage.seal_chained_jsonl(str(path), chain, keep=500, legacy_prefix=legacy)
    assert name + ".jsonl" not in "\n".join(cli._store_bound_lines(cfg))


@pytest.mark.parametrize("yes", [False, True])
def test_prune_says_plans_are_kept_whole_and_never_sealed(project, capsys, yes):
    path = Path(project["root"]) / ".ao" / "ledger" / "plans.jsonl"
    before = b'{"at": 1, "plan": "old"}\n'
    path.write_bytes(before)
    args = SimpleNamespace(days=30, yes=yes, evidence=True, keep_kb=256, review_days=30)
    assert cli.cmd_prune(project, args) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if "keep  plans" in line)
    assert "kept whole; never sealed" in line
    assert path.read_bytes() == before


def test_doctor_names_an_unreadable_evidence_bound(project):
    path = Path(project["root"]) / ".ao" / "ledger" / "verifications.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    line = "\n".join(cli._store_bound_lines(project))
    assert "cannot measure evidence retention" in line
    assert "verifications.jsonl" in line and "backup" in line


@pytest.mark.parametrize("rule", [
    "Bash(ao doctor:*)",
    "Bash(ao doctor --check --notify)",
    "Bash(ao doctor --notify --check)",
])
def test_agent_grants_must_not_page_a_person(rule):
    assert any(reason == "pages a person" and granted == rule
               for reason, _, granted in AL.problems(["agent", "--allowedTools", rule]))
    assert AL.problems(["agent", "--allowedTools", "Bash(ao doctor --check)"]) == []
