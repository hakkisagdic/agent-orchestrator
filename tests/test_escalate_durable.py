import json
import os
import time

import pytest

from ao import lib as A, storage, watchdog as W
from tests.scenarios import World

BLOCKED = "# queue empty\n\n## KARAR GEREKLİ\n"
# Built at run time, so no credential-shaped literal sits in the source.
TOKEN = "s" + "k-" + "ant-api03-" + "AB" * 12


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _architect_wakes(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and argv[0].endswith("/claude")]


def _architect(world, **fields):
    """Change the architect in the config the watchdog reads, which is the one on disk."""
    world.cfg["architect"] = dict(world.cfg["architect"], **fields)
    stored = {k: v for k, v in world.cfg.items() if k != "root"}
    with open(os.path.join(world.root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(stored, fh)


def _age_last_wake(world, seconds=3600):
    st = W.load_state(world.root)
    st["last_arch_wake"] = time.time() - seconds
    W.save_state(world.root, st)


def test_a_renamed_architect_is_woken_for_what_is_addressed_to_it(world):
    _architect(world, name="lead")
    world.transcript_age(900)
    world.mail("20260916-1200-dev-to-lead-BLOCKED-queue.md", BLOCKED)

    world.cycle(dry_run=False)

    assert len(_architect_wakes(world)) == 1
    assert "20260916-1200-dev-to-lead-BLOCKED-queue.md" in W.load_state(world.root)["handed"]


def test_a_wake_that_failed_hands_its_reports_back(world, monkeypatch):
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1

    wake = W.load_state(world.root)["last_arch_wake"]
    monkeypatch.setattr(W, "wake_error", lambda log_path: {
        "text": "API Error: 500 upstream unavailable", "binary": "/agents/claude 2.1.261",
        "when": "x", "at": int(wake), "kind": "other", "resets_at": None})
    _age_last_wake(world)
    world.spawned.clear()
    world.cycle(dry_run=False)

    assert len(_architect_wakes(world)) == 1
    assert "20260916-1200-kiro-to-fable-BLOCKED-queue.md" in W.load_state(world.root)["handed"]


def test_a_report_handed_to_a_wake_that_worked_does_not_wake_again(world):
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1

    _age_last_wake(world)
    world.spawned.clear()
    for _ in range(3):
        world.cycle(dry_run=False)

    assert _architect_wakes(world) == []


def test_a_dead_session_is_not_resumed_again(world, monkeypatch):
    _architect(world, session="auto", argv=["claude", "--resume", "{session}", "-p", "{prompt}"])
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1

    wake = W.load_state(world.root)["last_arch_wake"]
    monkeypatch.setattr(W, "wake_error", lambda log_path: {
        "text": "No conversation found with session ID: sess-1", "binary": "/agents/claude 2.1.261",
        "when": "x", "at": int(wake), "kind": "session", "resets_at": None})
    _age_last_wake(world)
    world.spawned.clear()
    trace = world.cycle(dry_run=False)

    assert _architect_wakes(world) == []
    assert any("no other session was found" in line for line in trace)

    monkeypatch.setattr(A, "discover_architect", lambda cwd: {"session": "sess-2", "age": 1})
    world.cycle(dry_run=False)
    (argv,) = _architect_wakes(world)
    assert "sess-2" in argv and "sess-1" not in argv


def test_a_failed_wake_rings_a_person(world, monkeypatch):
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.cycle(dry_run=False)
    wake = W.load_state(world.root)["last_arch_wake"]
    monkeypatch.setattr(W, "wake_error", lambda log_path: {
        "text": "API Error: 500 internal, key " + TOKEN,
        "binary": "/agents/claude 2.1.261", "when": "x", "at": int(wake), "kind": "other",
        "resets_at": None})
    _age_last_wake(world)
    world.notices.clear()
    world.cycle(dry_run=False)

    rung = [notice for notice in world.notices if notice[2] == "human" and "uyandırılamadı" in notice[0]]
    assert rung and TOKEN not in rung[0][1] and "[redacted]" in rung[0][1]


def test_a_present_architect_is_told_about_waiting_reports(world):
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", BLOCKED)
    world.architect()

    world.cycle(dry_run=False)

    told = [notice for notice in world.notices if "reports wait for the architect" in notice[0]]
    assert told and told[0][2] == "human" and "20260916-1200-kiro-to-fable-BLOCKED-queue.md" in told[0][1]
    assert _architect_wakes(world) == []


def test_the_watchdogs_own_anomaly_wakes_the_architect_once(world, monkeypatch):
    world.transcript_age(900)
    monkeypatch.setattr(A, "anomalies", lambda *args, **kwargs: [
        {"kind": "busy-without-progress", "facts": ["transcript growing for 40m", "HEAD unchanged"]}])

    world.cycle(dry_run=False)
    world.cycle(dry_run=False)

    assert len(_architect_wakes(world)) == 1
    ledger = os.path.join(world.root, ".ao", "ledger", "notices.jsonl")
    lines = open(ledger, encoding="utf-8").read().splitlines() if os.path.exists(ledger) else []
    rows = [json.loads(line) for line in lines if '"anomaly:busy-without-progress"' in line]
    assert len(rows) <= 1


def test_the_architects_note_to_itself_is_not_a_request(project):
    root = project["root"]
    cfg = dict(project, architect=dict(project["architect"], name="architect"))
    open(os.path.join(root, "agent-mail", "20260916-1200-architect-to-architect-DECISION-note.md"),
         "w", encoding="utf-8").write(BLOCKED)

    assert not [a for a in A.anomalies(root, cfg, {}, 900, 360) if a["kind"] in ("decision-requested", "report-waiting")]
    assert A.waiting_on_architect(root, cfg) is None


def test_a_flag_ao_adds_to_an_actor_is_recorded_once_per_change(project):
    root = project["root"]
    for _ in range(3):
        A.record_actor_flags(root, "implementer", ["--trust-all-tools"], "no tool scope in the resume argv")
    A.record_actor_flags(root, "implementer", ["--trust-tools=read"], "scoped")

    rows = storage.read_jsonl(os.path.join(root, ".ao", "ledger", "actor-flags.jsonl"))
    assert [row["flags"] for row in rows] == [["--trust-all-tools"], ["--trust-tools=read"]]


def test_redaction_masks_tokens_and_keeps_paths():
    github = "gh" + "p_" + "16C7e42F292c6912E7710c838347Ae178B4a"
    path = "/srv/someone/Projects/agent-orchestrator/src/ao/cli.py"
    masked = A.redact(f"Error: auth failed for {TOKEN} and {github} reading {path}")
    assert TOKEN not in masked and github not in masked
    assert path in masked
