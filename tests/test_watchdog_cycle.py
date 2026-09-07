import os
from types import SimpleNamespace

from ao import email, lib as A, telegram
from ao import watchdog as W


def test_dry_cycle_is_traced_and_not_recorded(project):
    root = project["root"]
    ns = SimpleNamespace(root=root, idle_minutes=6.0, dry_run=True, prompt=W.NUDGE_PROMPT)
    assert W.run(ns) == 0
    assert W._TRACE and "nothing to watch" in W._TRACE[-1]
    assert not os.path.exists(W.cycles_path(root))


def test_real_cycle_is_recorded(project):
    root = project["root"]
    ns = SimpleNamespace(root=root, idle_minutes=6.0, dry_run=False, prompt=W.NUDGE_PROMPT)
    W.run(ns)
    rows = W.cycles(root)
    assert rows and rows[-1]["verdict"] == W._TRACE[-1] and "trace" in rows[-1]


def test_expired_alarm_is_returned_once(project):
    now = 1_000_000
    A.alarm_touch("proj", "x", "orange", now=now)
    assert A.expire_alarms("proj", now=now + 60) == []
    done = A.expire_alarms("proj", now=now + 3 * 3600)
    assert [e["key"] for e in done] == ["x"]
    assert A.expire_alarms("proj", now=now + 3 * 3600) == []


def test_stale_sibling_heartbeats(project, tmp_path):
    d = os.path.join(A.HOME, ".ao")
    os.makedirs(d, exist_ok=True)
    for name, age in (("heartbeat-proj", 10), ("heartbeat-other", 2000), ("heartbeat-fresh", 30)):
        p = os.path.join(d, name)
        open(p, "w", encoding="utf-8").write("x")
        os.utime(p, (A.time.time() - age, A.time.time() - age))
    assert A.stale_siblings(project["root"]) == {"other": 2000} or list(A.stale_siblings(project["root"])) == ["other"]


def test_storm_cap(project):
    root = project["root"]
    for i in range(12):
        A.record_notice(root, f"t{i}", "m", sent=True, key=f"k{i}")
    assert W.storm(root) is True
    assert W.storm(root, limit=13) is False


def test_turn_ended_reads_the_transcripts_own_word(project, tmp_path, monkeypatch):
    import json
    tr = tmp_path / "t.jsonl"
    rows = [{"payload": {"type": "turn_start"}}, {"payload": {"type": "tool_call"}},
            {"payload": {"type": "turn_end"}}, {"payload": {"type": "session_metadata"}}]
    tr.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(tr), None))
    assert A.turn_ended(project) is True
    tr.write_text("\n".join(json.dumps(r) for r in rows[:2]) + "\n")
    assert A.turn_ended(project) is False


def test_explicit_audience_is_not_overridden_by_title(project, monkeypatch):
    root = project["root"]
    desktop, phone = [], []
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: desktop.append(argv))
    monkeypatch.setattr(telegram, "send", lambda body, target: phone.append((body, target)) or True)

    assert W.notify(
        "proj: mimar kotada", "quota", root,
        key="localized-human", audience="human",
    ) is True
    assert len(desktop) == 1 and len(phone) == 1
    assert A.active_alarms("proj")[0]["key"] == "localized-human"

    assert W.notify(
        "proj: internal anomaly", "architect fact", root,
        key="architect-only", audience="architect",
    ) is False
    assert len(desktop) == 1 and len(phone) == 1
    architect_notice = next(
        item for item in A.notices(root, include_suppressed=True)
        if item["key"] == "architect-only"
    )
    assert architect_notice["sent"] is False


def test_explicit_red_credit_alarm_reaches_email_immediately(project, monkeypatch):
    root = project["root"]
    mailed = []
    monkeypatch.setattr(W.subprocess, "run", lambda *args, **kw: None)
    monkeypatch.setattr(telegram, "send", lambda *args, **kw: False)
    monkeypatch.setattr(
        email, "send", lambda title, body, target: mailed.append((title, target)) or True,
    )

    assert W.notify(
        "proj: credits run out 07 Sep", "credits exhausted", root,
        key="credits-exhaust", audience="human", level="red",
    ) is True
    assert mailed == [("proj: credits run out 07 Sep", root)]
    alarm = A.active_alarms("proj")[0]
    assert alarm["key"] == "credits-exhaust"
    assert alarm["ring"] == "red" and alarm["red_sent"] is not None


def test_standing_architect_quota_advances_from_orange_to_red(project, monkeypatch):
    root = project["root"]
    clock = [1_000_000.0]
    mailed = []
    monkeypatch.setattr(W.time, "time", lambda: clock[0])
    monkeypatch.setattr(W.subprocess, "run", lambda *args, **kw: None)
    monkeypatch.setattr(telegram, "send", lambda *args, **kw: False)
    monkeypatch.setattr(
        email, "send", lambda title, body, target: mailed.append((title, target)) or True,
    )
    state = {
        "arch_quota_until": clock[0] + 3 * 3600,
        "wake_error": {"text": "weekly limit reached"},
    }

    assert W.touch_architect_quota(root, state) is True
    first = A.active_alarms("proj", now=clock[0])[0]
    assert first["ring"] == "orange" and mailed == []

    clock[0] += 61 * 60
    W.touch_architect_quota(root, state)
    standing = A.active_alarms("proj", now=clock[0])[0]
    assert standing["ring"] == "red"
    assert standing["count"] == 2 and standing["red_sent"] == clock[0]
    assert mailed == [("proj: mimar kotada", root)]


def test_cycle_previews_and_persists_quota_ladder_until_reset(
    project, tmp_path, monkeypatch, capsys,
):
    root = project["root"]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    clock = [1_000_000.0]
    state = {
        "arch_quota_until": clock[0] + 90 * 60,
        "wake_error": {"text": "weekly limit reached"},
    }
    desktop, phone, mailed = [], [], []

    monkeypatch.setattr(W.time, "time", lambda: clock[0])
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: desktop.append(argv))
    monkeypatch.setattr(
        telegram, "send", lambda body, target: phone.append((body, target)) or True,
    )
    monkeypatch.setattr(
        email, "send", lambda title, body, target: mailed.append((title, target)) or True,
    )
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    monkeypatch.setattr(A, "load_adapter", lambda name: {})
    monkeypatch.setattr(A, "architect_present", lambda target: False)
    monkeypatch.setattr(A, "agent_pids", lambda target, adapter: [])
    monkeypatch.setattr(A, "foreign_edits", lambda target, cfg: [])
    monkeypatch.setattr(A, "stale_siblings", lambda target: {})
    monkeypatch.setattr(A, "heartbeat", lambda target: None)
    monkeypatch.setattr(A, "reconcile_mail_ledger", lambda target, cfg: None)
    monkeypatch.setattr(A, "record_progress", lambda target, cfg: None)
    monkeypatch.setattr(A, "ping", lambda target: None)
    monkeypatch.setattr(
        A, "hold_state",
        lambda target: {
            "by": "human", "minutes": 1, "reason": "maintenance", "at": clock[0],
        },
    )
    monkeypatch.setattr(W, "load_state", lambda target: state)

    dry_args = SimpleNamespace(
        root=root, idle_minutes=6.0, dry_run=True, prompt=W.NUDGE_PROMPT,
    )
    live_args = SimpleNamespace(
        root=root, idle_minutes=6.0, dry_run=False, prompt=W.NUDGE_PROMPT,
    )

    assert W._cycle(dry_args, root) == 0
    first_preview = capsys.readouterr().out
    assert "would ring orange desktop/Telegram channels" in first_preview
    assert A.active_alarms("proj", now=clock[0]) == []
    assert desktop == [] and phone == [] and mailed == []

    assert W._cycle(live_args, root) == 0
    capsys.readouterr()
    first = A.active_alarms("proj", now=clock[0])[0]
    assert first["ring"] == "orange" and first["count"] == 1
    assert len(desktop) == 1 and len(phone) == 1 and mailed == []

    clock[0] += 61 * 60
    assert W._cycle(dry_args, root) == 0
    red_preview = capsys.readouterr().out
    assert "would send red e-mail" in red_preview
    assert "would suppress red desktop/Telegram channels (recent notice)" in red_preview
    unchanged = A.active_alarms("proj", now=clock[0])[0]
    assert unchanged["ring"] == "orange" and unchanged["count"] == 1
    assert len(desktop) == 1 and len(phone) == 1 and mailed == []

    assert W._cycle(live_args, root) == 0
    capsys.readouterr()
    standing = A.active_alarms("proj", now=clock[0])[0]
    assert standing["ring"] == "red" and standing["count"] == 2
    assert standing["red_sent"] == clock[0]
    assert mailed == [("proj: mimar kotada", root)]
    assert len(desktop) == 1 and len(phone) == 1

    clock[0] += 30 * 60
    assert clock[0] > state["arch_quota_until"]
    assert W._cycle(live_args, root) == 0
    capsys.readouterr()
    after_reset = A.active_alarms("proj", now=clock[0])[0]
    assert after_reset["count"] == 2 and after_reset["red_sent"] == standing["red_sent"]
    assert mailed == [("proj: mimar kotada", root)]


def test_omitted_audience_retains_legacy_title_inference(project, monkeypatch):
    root = project["root"]
    desktop, phone = [], []
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: desktop.append(argv))
    monkeypatch.setattr(
        telegram, "send", lambda body, target: phone.append((body, target)) or True,
    )

    assert W.notify(
        "proj: needs you", "legacy human route", root, key="legacy-human",
    ) is True
    assert len(desktop) == 1 and len(phone) == 1
    assert A.active_alarms("proj")[0]["key"] == "legacy-human"

    assert W.notify(
        "proj: internal anomaly", "legacy architect route", root,
        key="legacy-architect",
    ) is False
    assert len(desktop) == 1 and len(phone) == 1
    architect_notice = next(
        item for item in A.notices(root, include_suppressed=True)
        if item["key"] == "legacy-architect"
    )
    assert architect_notice["sent"] is False


def test_dry_cycle_escalation_has_no_alarm_or_channel_side_effects(
    project, tmp_path, monkeypatch,
):
    from ao import features as F

    root = project["root"]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    pending = (
        tmp_path / "proj" / "agent-mail"
        / "20260907-0100-kiro-to-fable-BLOCKED-x.md"
    )
    pending.write_text("blocked\n", encoding="utf-8")
    desktop, phone, mailed, feature_checks = [], [], [], []

    def unexpected(*args, **kwargs):
        raise AssertionError("dry-run attempted a persistent side effect")

    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: desktop.append(argv))
    monkeypatch.setattr(
        telegram, "send", lambda body, target: phone.append((body, target)) or True,
    )
    monkeypatch.setattr(
        email, "send", lambda title, body, target: mailed.append((title, target)) or True,
    )
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))
    monkeypatch.setattr(A, "load_adapter", lambda name: {})
    monkeypatch.setattr(A, "heartbeat", unexpected)
    monkeypatch.setattr(A, "reconcile_mail_ledger", unexpected)
    monkeypatch.setattr(A, "record_progress", unexpected)
    monkeypatch.setattr(A, "expire_alarms", unexpected)
    monkeypatch.setattr(A, "ping", unexpected)
    monkeypatch.setattr(A, "record_notice", unexpected)
    monkeypatch.setattr(
        A, "notice_recently_sent",
        lambda target, key, window: key == "anomaly:decision-requested",
    )
    monkeypatch.setattr(A, "architect_present", lambda target: False)
    monkeypatch.setattr(A, "agent_pids", lambda target, adapter, **kw: [])
    monkeypatch.setattr(A, "foreign_edits", lambda target, cfg: [])
    monkeypatch.setattr(A, "stale_siblings", lambda target: {})
    monkeypatch.setattr(A, "hold_state", lambda target: None)
    monkeypatch.setattr(A, "spinning", lambda target: None)
    monkeypatch.setattr(A, "anomalies", lambda *args, **kwargs: [
        {"kind": "decision-requested", "facts": {"decision": "D-1"}, "key": "D-1"},
    ])
    monkeypatch.setattr(A, "write_report", unexpected)
    monkeypatch.setattr(A, "mailbox", lambda target, mailbox: [pending.name])
    monkeypatch.setattr(A, "orphans", lambda target, adapter: [4242])
    monkeypatch.setattr(A, "sweep_orphans", unexpected)
    monkeypatch.setattr(A, "work_fingerprint", lambda target: "fp")
    monkeypatch.setattr(W, "load_state", lambda target: {})
    monkeypatch.setattr(W, "quota_ok", lambda adapter: True)
    monkeypatch.setattr(
        F, "enabled",
        lambda cfg, key: feature_checks.append(key) or False,
    )

    args = SimpleNamespace(
        root=root, idle_minutes=6.0, dry_run=True, prompt=W.NUDGE_PROMPT,
    )
    assert W.run(args) == 0
    assert any(
        "would suppress anomaly decision-requested" in line for line in W._TRACE
    )
    assert any("orphaned agent process" in line for line in W._TRACE)
    assert "architect_wake" in feature_checks
    assert desktop == [] and phone == [] and mailed == []
    assert A.active_alarms("proj") == []
