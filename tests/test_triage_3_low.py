"""Doctor, config, wake and routing gaps survived earlier fixes (TRIAGE-3-LOW).

Exercise each incident before its repair, including the answered-report world
that the fuzz oracle incorrectly called a watchdog failure.
"""
import errno
import json
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage, watchdog as W
from tests.scenarios import World
from tests.test_capability_matrix import _strict_config
from tests.test_escalate_durable import BLOCKED, _architect, _architect_wakes, _age_last_wake
from tests.test_ready_graph import _board
from tests.test_ready_graph_2 import EMPTY
from tests.test_scenario_fuzz import IDLE, INVARIANTS, _Forced, _build, _nudged


def test_doctor_checks_kiro_reading_flags(project):
    cfg = dict(project, reviewer={"id": "writing", "argv": [
        "kiro-cli", "chat", "--no-interactive", "--trust-tools=write", "{prompt}"]})
    assert "lacks --trust-tools=" in dict(cli._actor_grant_problems(cfg))["reviewer-tools:writing"]


def test_doctor_checks_matrix_routes_with_their_pins(project):
    cfg = _strict_config(project)
    tools = cfg["capability_matrix"]["tools"]
    tools["tool.primary"]["argv"] = ["claude", "-p", "{prompt}", "--model", "{model}"]
    tools["tool.fallback"]["argv"] = A.compose_reviewer("claude-code", model="{model}")["argv"]
    problems = dict(cli._actor_grant_problems(cfg))
    assert "no --strict-mcp-config" in problems["reviewer-tools:binding.primary"]
    assert "reviewer-tools:binding.fallback" not in problems


@pytest.mark.parametrize("limit", ["bytes", "depth"])
def test_config_writer_refuses_what_its_reader_cannot_read(project, limit):
    path = Path(project["root"]) / ".ao/config.json"
    before = path.read_bytes()
    if limit == "bytes":
        document = {"padding": "é" * ((A.PROJECT_CONFIG_MAX_BYTES - 20) // 2)}
        compact = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        assert len(compact.encode("utf-8")) <= A.PROJECT_CONFIG_MAX_BYTES
        text = json.dumps(dict(document, features={"review": True}), indent=2, ensure_ascii=False)
        assert len(text) < A.PROJECT_CONFIG_MAX_BYTES < len(text.encode("utf-8"))
    else:
        text = '{"nested":' + '[' * A.PROJECT_CONFIG_MAX_CONTAINER_DEPTH + '0' + ']' * A.PROJECT_CONFIG_MAX_CONTAINER_DEPTH + '}'
    with pytest.raises(ValueError, match="config.json exceeds"):
        A.write_project_config(project["root"], text)
    assert path.read_bytes() == before


@pytest.mark.parametrize("command", ["config", "features"])
def test_config_reports_a_change_after_directory_sync_fails(project, monkeypatch, capsys, command):
    original = storage.replace_file_durably

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "directory sync failed")
        os.fsync(fd)

    def write(path, data, **kwargs):
        return original(path, data, _fsync=fsync, **kwargs)

    # Windows cannot open directories for fsync; inject its error at that boundary (TRIAGE-3-LOW).
    if os.name == "nt":
        def directory_sync(directory, sync):
            raise OSError(errno.EIO, "directory sync failed")
        monkeypatch.setattr(storage, "_sync_directory", directory_sync)
    monkeypatch.setattr(storage, "replace_file_durably", write)
    if command == "config":
        code = cli.cmd_config(project, SimpleNamespace(action="set", key="round_budget", value="3", machine=False))
        assert A.project_config_document(project["root"])["config"]["round_budget"] == 3
    else:
        code = cli.cmd_features(project, SimpleNamespace(action="off", key="nudge", by=None))
        assert A.project_config_document(project["root"])["config"]["features"]["nudge"] is False
    out = capsys.readouterr().out
    assert code == 1 and "not changed" not in out
    assert "changed" in out and "durab" in out


@pytest.mark.parametrize("turn", ["wake", "refill", "nudge"])
def test_unrecorded_flags_start_no_turn(project, monkeypatch, tmp_path, turn):
    world = World(project, monkeypatch, tmp_path)
    world.transcript_age(3600)
    if turn in ("wake", "refill"):
        _architect(world, argv=["claude", "-p", "{prompt}"])
    if turn == "wake":
        world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    if turn == "nudge":
        world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    recorded = []

    def failed(root, role, flags, reason):
        recorded.append(role)
        raise OSError("audit unavailable")

    monkeypatch.setattr(A, "record_actor_flags", failed)
    monkeypatch.setattr(W.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(W.subprocess, "Popen", lambda argv, **kw: world.spawned.append(argv) or
                        SimpleNamespace(pid=99999, poll=lambda: 0, returncode=0))
    world.cycle(dry_run=False)
    assert recorded == ["implementer" if turn == "nudge" else "architect"]
    assert not [argv for argv in world.spawned if str(argv[0]).startswith("/agents/")]
    assert any("flags" in message and audience == "human" for _, message, audience, _ in world.notices)


def test_same_second_report_rewrite_is_handed_again(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    world.transcript_age(900)
    name = "20260916-1200-kiro-to-fable-BLOCKED-queue.md"
    world.mail(name, BLOCKED)
    path = Path(world.root) / world.cfg["mailbox"] / name
    second = int(time.time()) * 1_000_000_000
    os.utime(path, ns=(second, second + 100_000_000))
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1
    _age_last_wake(world)
    world.spawned.clear()
    path.write_text(BLOCKED.replace("queue", "store"), encoding="utf-8")
    os.utime(path, ns=(second, second + 200_000_000))
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1


@pytest.mark.parametrize("recipient", ["reviewer", "judge"])
def test_reviewer_notes_stay_out_of_the_implementer_inbox(project, recipient):
    cfg = dict(project, reviewer={"actor": "judge", "name": "judge", "id": "judge"})
    args = SimpleNamespace(title="read this", body="review context", stdin=False, to=recipient, urgent=False)
    assert cli.cmd_note(cfg, args) == 0
    [name] = A.mailbox(project["root"], cfg["mailbox"])
    meta = A.mail_meta(os.path.join(project["root"], cfg["mailbox"], name))
    assert meta["to_role"] == "reviewer"
    assert name not in A.implementer_inbox(project["root"], cfg)
    assert not A.addressed_to(name, cfg, "implementer")


def test_a_duplicate_done_id_cannot_satisfy_a_dependency(project):
    _board(project["root"], EMPTY.format(queued="- [A] unfinished\n- [C] dependent · needs: A\n", done="- [A] done\n"))
    assert A.ready(project["root"]) == []


def test_answered_blocked_report_nudges_and_satisfies_fuzz_oracle(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    facts = _build(world, _Forced(10 * IDLE, running=True, blocked_report=True, inbox=True,
                                  quota=True, nudge_on=True), monkeypatch)
    trace = world.cycle(dry_run=True)
    assert _nudged(trace)
    assert [name for name, holds in INVARIANTS if not holds(facts, trace)] == []
