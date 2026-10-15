"""Lost notices, false outcomes and mutable baselines from re-review (TRIAGE-2-MEDIUM)."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage, watchdog as W
from tests.conftest import review_off
from tests.scenarios import World
from tests.test_outcomes import _review


@pytest.mark.parametrize("spec", [None, "x", [1], {"gates": ["x"]},
    {"gates": {"bad": "x", "ok": {"run": "pytest"}}},
    {"gates": {"ok": {"run": "pytest"}}, "profiles": {"quick": ["missing"]}}])
def test_c3_doctor_names_unusable_gates(project, spec):
    path = Path(project["root"], ".ao", "gates.json")
    if spec is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(json.dumps(spec), encoding="utf-8")
    assert "gates-unusable" in dict(cli.doctor_problems(project))


def test_c3_coverage_skips_non_object_gates(project):
    Path(project["root"], "a.py").write_text("pass\n", encoding="utf-8")
    assert A.gate_coverage_gaps(project["root"], {"gates": {"bad": "x", "ok": {"run": "pytest"}}}, 1) == []


@pytest.mark.parametrize("manifest,content,chain,runner", [
    ("api.csproj", "", "dotnet", "dotnet"), ("api.fsproj", "", "dotnet", "dotnet"),
    ("app.sln", "", "dotnet", "dotnet"), ("pom.xml", "", "jvm", "mvn"),
    ("build.gradle", "", "jvm", "gradle"), ("Package.swift", "", "swift", "swift"),
    ("pyproject.toml", "", "python", "pytest"), ("setup.py", "", "python", "pytest"),
    ("go.mod", "", "go", "go"), ("Cargo.toml", "", "rust", "cargo"),
    ("package.json", '{"scripts":{"lint":"eslint ."}}', "node", "npm")])
def test_c3_detects_child_toolchains(project, manifest, content, chain, runner):
    root = Path(project["root"])
    (root / "package.json").write_text('{"scripts":{"lint":"eslint ."}}', encoding="utf-8")
    child = root / "back end"
    child.mkdir()
    (child / manifest).write_text(content, encoding="utf-8")
    spec = cli._detect_gates(str(root))
    scoped = [gate for gate in spec["gates"].values() if "back end/**" in gate.get("inputs", [])]
    assert scoped and any(runner in gate["run"] for gate in scoped)
    assert any(A._gate_covers(gate, "back end", chain) for gate in scoped)
    assert not any(A._gate_covers(gate, "other", chain) for gate in scoped)
    assert any(name in spec["profiles"]["quick"] for name, gate in spec["gates"].items() if gate in scoped)


@pytest.mark.parametrize("run", ["echo pytest", "echo 'x; pytest'", "printf '%s' pytest", "echo /bin/pytest", "echo ';' pytest", 'echo "&&" pytest'])
def test_c3_printing_a_runner_does_not_cover(run):
    assert not A._gate_covers({"run": run}, ".", "python")


@pytest.mark.parametrize("run", ["pytest -q", "python -m pytest", "uv run pytest", "cd src && pytest", "true; pytest", "cat x | pytest"])
def test_c3_commands_still_cover(run):
    assert A._gate_covers({"run": run}, ".", "python")


@pytest.mark.parametrize("lengths", [(8, 12, 16), (8, 12, 16, 64)])
def test_c4_hash_collisions_preserve_every_live_project(project, tmp_path, monkeypatch, lengths):
    registry = tmp_path / "registry.json"
    monkeypatch.setenv("AO_PROJECT_REGISTRY", str(registry))
    target = tmp_path / "new" / "api"
    (target / ".ao").mkdir(parents=True)
    digest = hashlib.sha256(os.path.realpath(target).encode("utf-8")).hexdigest()
    held = {}
    for index, key in enumerate(["api"] + ["api-" + digest[:n] for n in lengths]):
        root = tmp_path / str(index) / "api"
        (root / ".ao").mkdir(parents=True)
        held[key] = {"root": str(root)}
    registry.write_text(json.dumps(held), encoding="utf-8")
    key = A.project_key(str(target))
    assert key not in held
    assert all(A.project_registry().get(name) == row for name, row in held.items())


def test_c4_retired_project_is_not_a_collision(project, tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    monkeypatch.setenv("AO_PROJECT_REGISTRY", str(registry))
    registry.write_text(json.dumps({"live": {"root": project["root"]},
        "gone": {"root": str(tmp_path / "gone" / Path(project["root"]).name)}}), encoding="utf-8")
    assert A.project_key_collisions() == {}


def _grant(project, token="C-land"):
    root = project["root"]
    _review(root, token + ".md", "APPROVED", token)
    A.record_authority(root, True, [], "sha256:t", "V-1", token, review=token + ".md")


def _land(root, token):
    subprocess.run([A.git_binary(), "-c", "core.hooksPath=", "commit", "--allow-empty", "-m",
                    "land\n\nAo-Grant: " + token], cwd=root, check=True, capture_output=True)
    return int(subprocess.check_output([A.git_binary(), "show", "-s", "--format=%ct", "HEAD"], cwd=root))


def test_c5_only_landed_grants_count_with_commit_time(project):
    _grant(project)
    assert A.slice_outcomes(project["root"]) == []
    landed = _land(project["root"], "C-land")
    [outcome] = A.slice_outcomes(project["root"])
    assert outcome["landed_at"] == landed


def test_c5_stats_names_an_unreadable_project_and_proposals_name_the_ledger(project, tmp_path, monkeypatch, capsys):
    _grant(project)
    _land(project["root"], "C-land")
    broken = tmp_path / "broken"
    (broken / ".ao" / "ledger").mkdir(parents=True)
    (broken / ".ao" / "ledger" / "authority.jsonl").write_text("bad\n", encoding="utf-8")
    monkeypatch.setattr(A, "recall_roots", lambda root: [("good", root), ("broken", str(broken))])
    cli.cmd_stats(project, SimpleNamespace(all=True, since=None, until=None, slices=False))
    output = capsys.readouterr().out
    assert "broken" in output and "cannot read" in output and "1 slices landed" in output
    assert "unread" in A.proposal_evidence(str(broken))


def test_c5_distribution_uses_median_and_nearest_rank():
    rows = [dict(waived=False, rounds=n, hours=n, first_pass=True, defect_found=False) for n in range(1, 11)]
    assert A.outcome_stats(rows[:2])["rounds"]["median"] == 1.5
    assert A.outcome_stats(rows)["rounds"]["p90"] == 9


@pytest.mark.parametrize("missing", ["implementer", "transcript"])
def test_c6_human_waits_are_delivered_without_a_session(project, monkeypatch, tmp_path, missing):
    world = World(project, monkeypatch, tmp_path)
    world.board("blocked", "- [B1] token expired · needs: a new token · waiting: human")
    if missing == "implementer":
        monkeypatch.setattr(A, "load_config", lambda root: dict(project, implementer={}))
    else:
        world.transcript.unlink()
    world.cycle()
    assert any("B1 waits on a person" in notice[1] and notice[2] == "human" for notice in world.notices)


def test_c7_work_during_startup_is_not_an_idle_answer(project, monkeypatch, tmp_path):
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "s1", "name": "claude"})
    Path(project["root"], ".ao", "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    world = World(cfg, monkeypatch, tmp_path)
    world.board("running", "- [S1] slice · since: 2026-09-16 09:00")
    world.transcript_age(900)
    product = Path(world.root, "product.py")
    product.write_text("before", encoding="utf-8")
    monkeypatch.setattr(A, "work_fingerprint", lambda root, cfg=None: product.read_text(encoding="utf-8"))
    inputs = A.nudge_inputs(world.root, cfg)

    class Child:
        pid = 4242
        returncode = 0

        def poll(self):
            product.write_text("after", encoding="utf-8")
            world.board("queued", "- [S2] next slice")
            with world.transcript.open("a", encoding="utf-8") as fh:
                fh.write('{}\n')
            return 0

    monkeypatch.setattr(W.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(W.time, "sleep", lambda seconds: None)
    world.cycle(dry_run=False)
    state = W.load_state(world.root)
    assert state.get("nudge_fingerprint") == "before"
    assert state["nudge_inputs"] == inputs
    world.turn_ended = True
    world.transcript_age(900)
    world.cycle()
    assert not W.load_state(world.root).get("idle_answer")


def test_c7_same_second_dirty_file_edits_change_fingerprint(project):
    path = Path(project["root"], "product.py")
    path.write_text("before", encoding="utf-8")
    stamp = 1_800_000_000_100_000_000
    os.utime(path, ns=(stamp, stamp))
    before = A.work_fingerprint(project["root"])
    path.write_text("edited", encoding="utf-8")
    os.utime(path, ns=(stamp + 100_000_000, stamp + 100_000_000))
    assert A.work_fingerprint(project["root"]) != before


def test_c8_colliding_topics_keep_both_bodies_and_ids(project, capsys, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(cli, "datetime", SimpleNamespace(now=lambda: datetime(2026, 10, 1, 12, 0)))
    for topic, body in (("alpha/beta", "first body"), ("alpha:beta", "second body")):
        cli.cmd_mail(project, SimpleNamespace(action="send", topic=topic, type="INFO", body=body))
    names = capsys.readouterr().out.splitlines()
    assert len(set(names)) == 2
    for name, body in zip(names, ("first body", "second body")):
        path = Path(project["root"], project["mailbox"], name)
        assert body in path.read_text(encoding="utf-8")
        assert A.mail_meta(str(path))["id"] == name
    assert {row["id"] for row in A.mail_log(project["root"]) if row["event"] == "written"} == set(names)


def test_c8_role_names_are_safe(project):
    cfg = dict(project, implementer=dict(project["implementer"], name="dev:ops"), architect={"name": "arch*ops"})
    assert all(re.fullmatch(r"[A-Za-z0-9._-]+", name) for name in A.mail_names(cfg))


def test_c9_long_decision_request_is_classified(project):
    A.write_mail(project["root"], project, "dev-to-architect-INFO-long.md", "x" * 5000 + "\n## Decision required\n")
    assert A.unseen_messages(project["root"], project)[0]["class"] == "needs-decision"


@pytest.mark.parametrize("row", [{}, [], {"item": "B1"}, {"item": [], "digest": "d"}])
def test_c10_malformed_baseline_refuses_commit(project, monkeypatch, capsys, row):
    Path(project["root"], ".ao", "ledger", "plans.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.setattr(A, "hold_state", lambda root: None)
    monkeypatch.setattr(A, "urgent_messages", lambda *args, **kwargs: [])
    assert cli.cmd_commit_ok(review_off(project), SimpleNamespace(verify=False, profile=None)) == 1
    assert "plan baselines cannot be read" in capsys.readouterr().out


@pytest.mark.parametrize("damage", ["edit", "delete"])
def test_c10_plan_chain_detects_tampering(project, damage):
    root = project["root"]
    A.record_plan(root, "B1", "first")
    A.record_plan(root, "B2", "second")
    path = Path(root, ".ao", "ledger", "plans.jsonl")
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace("first", "other") if damage == "edit" else "", encoding="utf-8")
    with pytest.raises(storage.LedgerCorruption):
        A.plan_baseline(root)


def test_c10_re_admission_is_explicit_and_legacy_prefix_is_kept(project):
    root = project["root"]
    path = Path(root, ".ao", "ledger", "plans.jsonl")
    path.write_text('{"item":"B1","digest":"first"}\n', encoding="utf-8")
    A.record_plan(root, "B1", "second")
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert row.get("event") == "readmitted" and row.get("replaces") == "first"
    assert A.plan_baseline(root) == {"B1": "second"}


@pytest.mark.parametrize("ledger", ["reviews", "authority", "verifications", "waivers"])
def test_c5_no_corrupt_ledger_is_silently_omitted(project, ledger):
    _grant(project)
    _land(project["root"], "C-land")
    Path(project["root"], ".ao", "ledger", ledger + ".jsonl").write_text("bad\n", encoding="utf-8")
    with pytest.raises(storage.LedgerCorruption):
        A.slice_outcomes(project["root"])


def test_c5_landing_time_is_not_grant_time(project, monkeypatch):
    with monkeypatch.context() as clock:
        clock.setattr(A.time, "time", lambda: 1_600_000_000)
        _grant(project)
    at = _land(project["root"], "C-land")
    [outcome] = A.slice_outcomes(project["root"])
    assert outcome["landed_at"] == at and at != 1_600_000_000


def test_c10_duplicate_legacy_baseline_is_not_a_readmission(project):
    path = Path(project["root"], ".ao", "ledger", "plans.jsonl")
    path.write_text('{"item":"B1","digest":"first"}\n{"item":"B1","digest":"second"}\n', encoding="utf-8")
    with pytest.raises(storage.LedgerCorruption):
        A.plan_baseline(project["root"])


@pytest.mark.parametrize("run,chain", [
    ("npx eslint .", "node"), ("npx --no-install tsc", "node"),
    ("uv run --frozen pytest", "python")])
def test_c3_runner_wrappers_cover_with_packages_and_flags(run, chain):
    assert A._gate_covers({"run": run}, ".", chain)
    assert not A._gate_covers({"run": "echo " + run}, ".", chain)


@pytest.mark.parametrize("mode", ["deletion", "append-only"])
def test_c8_handling_mail_does_not_release_its_id(project, mode):
    from tests.test_message_store import _ack, _mode
    cfg = _mode(project, mode)
    root = cfg["root"]
    name = "20261001-1200-dev-to-architect-INFO-topic.md"
    assert A.write_mail(root, cfg, name, "# first\n") == name
    A.mail_seen(root, [name], "architect")
    _ack(cfg, name)
    assert not Path(root, "agent-mail", name).exists()
    second = A.write_mail(root, cfg, name, "# second\n")
    assert second == name[:-3] + "-2.md"
    assert second in {row["id"] for row in A.unseen_messages(root, cfg)}
    if mode == "append-only":
        A.sync_mail_view(root, cfg)
        assert second in A.unhandled_messages(root)
        assert b"# first" in A.message_body(root, name)
        assert b"# second" in A.message_body(root, second)
    assert "# second" in Path(root, "agent-mail", second).read_text(encoding="utf-8")


@pytest.mark.parametrize("source", ["old-ledger", "stored-body", "store-row"])
def test_c8_historical_ids_survive_without_a_view(project, source):
    root = project["root"]
    name = "dev-to-architect-INFO-history.md"
    if source == "old-ledger":
        A.mail_ledger_append(root, {"event": "written", "id": name})
        for number in range(210):
            A.mail_ledger_append(root, {"event": "seen", "id": str(number)})
    elif source == "stored-body":
        store = Path(root, ".ao", "mail", "store")
        store.mkdir(parents=True)
        (store / name).write_text("# first\n", encoding="utf-8")
    else:
        A._mail_store_append(root, {"event": "message", "id": name})
    assert A.write_mail(root, project, name, "# second\n") == name[:-3] + "-2.md"


@pytest.mark.parametrize("manifest", [False, True])
def test_c3_unquotable_children_without_gates_do_not_block_detection(tmp_path, monkeypatch, manifest):
    child = tmp_path / "cash$"
    child.mkdir()
    if manifest:
        (child / "package.json").write_text("{}", encoding="utf-8")
    quote = A.copyable_word
    monkeypatch.setattr(A, "copyable_word", lambda word: quote(word, windows=True))
    assert cli._detect_gates(str(tmp_path))["profiles"]["quick"] == ["diff-check"]


@pytest.mark.parametrize("existing", [False, True])
def test_c3_init_refuses_unquotable_gates_cleanly_and_accepts_handwritten_gates(tmp_path, monkeypatch, capsys, existing):
    from tests.test_profiles import _git_init, _init_args, _tree_state
    root = tmp_path / "project"
    root.mkdir()
    _git_init(root)
    child = root / "cash$"
    child.mkdir()
    (child / "pyproject.toml").write_text("", encoding="utf-8")
    gates = root / ".ao" / "gates.json"
    content = '{"gates":{"test":{"run":"pytest"}},"profiles":{"quick":["test"]}}\n'
    if existing:
        gates.parent.mkdir()
        gates.write_text(content, encoding="utf-8")
    quote = A.copyable_word
    monkeypatch.setattr(A, "copyable_word", lambda word: quote(word, windows=True))
    before = _tree_state(root)
    result = cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="kiro", no_mcp=True))
    if existing:
        assert result == 0
        assert gates.read_text(encoding="utf-8") == content
    else:
        assert result == 1
        output = capsys.readouterr().out
        assert "init refused" in output and "declare its gates by hand" in output
        assert _tree_state(root) == before


@pytest.mark.parametrize("profiles,default", [
    (None, None), ({"full": ["test"]}, None), ({"quick": []}, None),
    ({"quick": ["test"]}, "absent")])
def test_c3_doctor_refuses_a_default_profile_with_no_gates(project, profiles, default):
    spec = {"gates": {"test": {"run": "pytest"}}}
    if profiles is not None:
        spec["profiles"] = profiles
    if default is not None:
        spec["default_profile"] = default
    Path(project["root"], ".ao", "gates.json").write_text(json.dumps(spec), encoding="utf-8")
    assert "gates-unusable" in dict(cli.doctor_problems(project))


@pytest.mark.parametrize("mode", ["deletion", "append-only"])
@pytest.mark.parametrize("key", ["implementer", None])
def test_c8_recurring_anomaly_stays_deduplicated_after_handling(project, mode, key):
    from tests.test_message_store import _ack, _mode
    cfg = _mode(project, mode)
    root = cfg["root"]
    facts = ["the implementer wrote a.md"]
    first = A.write_report(root, cfg, "decision-requested", facts, key=key)
    assert first
    for suffix in (2, 3):
        _ack(cfg, first)
        assert not Path(root, "agent-mail", first).exists()
        second = A.write_report(root, cfg, "decision-requested", facts, key=key)
        assert second and second.endswith(f"-{suffix}.md")
        path = Path(root, "agent-mail", second)
        before, stamp = path.read_bytes(), path.stat().st_mtime_ns
        assert A.write_report(root, cfg, "decision-requested", facts, key=key) is None
        unnamed = dict(cfg, architect={k: v for k, v in cfg["architect"].items() if k != "name"})
        assert A.write_report(root, unnamed, "decision-requested", facts, key=key) is None
        assert path.read_bytes() == before and path.stat().st_mtime_ns == stamp
        assert A.mailbox(root, cfg["mailbox"]) == [second]
        first = second


def test_c8_anomaly_key_number_is_not_a_delivery_suffix(project):
    from tests.test_message_store import _ack
    root = project["root"]
    facts = ["the implementer wrote a.md"]
    first = A.write_report(root, project, "decision-requested", facts, key="implementer")
    _ack(project, first)
    recurring = A.write_report(root, project, "decision-requested", facts, key="implementer")
    other = A.write_report(root, project, "decision-requested", facts, key="implementer-2")
    assert other and other != recurring
    assert A.write_report(root, project, "decision-requested", facts, key="implementer-2") is None
    assert A.write_report(root, project, "decision-requested", facts, key="implementer") is None
