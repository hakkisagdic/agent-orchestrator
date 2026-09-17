"""Four conditions that still repeated after NOTICE-NOISE and WAITING-ONE-ALARM (NOISE-REPEATS).

Rebuilt in a temporary project, a day after the jobs came back still told these again and
again: a request an architect at the keyboard had not read, under "needs you" and under
"reports wait for the architect"; an open decision, hourly on the desktop and the phone
beside the anomaly that named it; an unseen request, hourly between its thresholds and mailed
before its red; and a wake failing on a transport error, 95 "architect woken" lines to the
phone. Each is now one alarm that rings once for what it says and climbs the ladder, and what
is new - another report, another decision, a threshold crossed, a wake that works at last, a
failure of another kind - is told.
"""
import os
import time

from ao import cli, email, features as F, lib as A, telegram
from ao import watchdog as W
from tests.scenarios import World

NOTIFY = W.notify                  # the scenario world replaces it; these are about the real ladder
HOUR, DAY = 3600, 86400
REQUEST = "20260902-0900-kiro-to-fable-BLOCKED-credits.md"
BLOCKED = "# credits are exhausted until the reset\n\n## KARAR GEREKLİ\n\nnew account or wait?\n"
DONE = "20260902-0830-kiro-to-fable-DONE-b6.md"
LATER = "20260903-1000-kiro-to-fable-DONE-b7.md"
PLAIN = "# b6 done\n\nBlockers: none\n"
INTERACTIVE = "no architect will act on it: the architect session is interactive and acts only when someone prompts it"
WAKES_OFF = "no architect will act on it: architect wakes are switched off"
OVERLOADED = 'API Error: 529 {"type":"error","error":{"type":"overloaded_error"},"request_id":"req_011CT%08dabcdefgh"}'
WOKEN = "🤖 *Mimar uyandırıldı* — 2 rapor işleniyor (pid 99999)"
WOKEN_AT_LAST = "🤖 *Mimar uyandırıldı* — başarısız denemelerin ardından, 2 rapor için (pid 99999)"


def _since(name):
    return W._when(A._name_time(name))


def _heartbeat(root, at):
    path = A.heartbeat_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(int(at)))
    os.utime(path, (at, at))


def _world(project, monkeypatch, tmp_path, wakes=False, present=False):
    """A scenario world on a clock the test moves, with the real ladder and recorded channels."""
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    world = World(project, monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(W, "notify", NOTIFY)
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None:
                        sent.append(("desktop", title, msg, clock[0])) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None:
                        sent.append(("telegram", text, "", clock[0])) or 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None:
                        sent.append(("email", subject, body, clock[0])) or True)
    monkeypatch.setattr(A, "heartbeat", lambda target: _heartbeat(target, clock[0]))
    for switch in ("nudge", "refill") + (() if wakes else ("architect_wake",)):
        F.set_switch(world.root, switch, False)
    if present:
        world.architect()
    world.transcript_age(HOUR)
    return world, sent, clock


def _mail(world, name, body):
    world.mail(name, body)
    A.mail_seen(world.root, [name], by="architect")       # shown, and not answered


def _on(sent, channel, word=""):
    return [title for kind, title, _, _ in sent if kind == channel and word in title]


def _mails(sent, word=""):
    return [body.split("\n")[0] for kind, subject, body, _ in sent if kind == "email" and word in subject]


def _cycles(world, clock, seconds):
    end = clock[0] + seconds
    while clock[0] < end:
        world.cycle(dry_run=False)
        clock[0] += 300


def _architect_wakes(world):
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).endswith("/claude")]


def _wakes(world, monkeypatch, clock, failure):
    """Each wake the watchdog starts, and what its log segment says: `failure(n)` for wake n, None when it works."""
    started = []

    class _Proc:
        pid = 99999

    def popen(argv, **kw):
        world.spawned.append(argv)
        if isinstance(argv, list) and argv and str(argv[0]).endswith("/claude"):
            started.append({"at": int(clock[0]), "text": failure(len(started) + 1)})
        return _Proc()

    def read(log_path):
        last = started[-1] if started else None
        if not last or not last["text"]:
            return None
        return {"text": last["text"], "binary": "/agents/claude 2.1.261", "at": last["at"], "kind": "other",
                "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last["at"])), "resets_at": None}

    monkeypatch.setattr(W.subprocess, "Popen", popen)
    monkeypatch.setattr(W, "wake_error", read)
    return started


# ---- 1. an architect at the keyboard ---------------------------------------------------------

def test_a_request_the_architect_at_the_keyboard_has_not_read_is_one_alarm_that_names_it(
        project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path, wakes=True, present=True)
    _mail(world, REQUEST, BLOCKED)

    trace = world.cycle()
    assert sent == [] and any("named by the alarm of the anomaly they stand for" in line for line in trace)

    _cycles(world, clock, 2 * HOUR)

    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["anomaly:decision-requested"]
    assert _on(sent, "desktop") == ["proj: needs you"] and len(_on(sent, "telegram")) == 1
    assert _mails(sent) == [f"decision-requested: {REQUEST} in agent-mail/, waiting since {_since(REQUEST)} — "
                            f"{INTERACTIVE}"]
    assert _architect_wakes(world) == []


def test_a_report_no_anomaly_stands_for_waits_for_the_architect_by_name_and_another_is_told(
        project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path, wakes=True, present=True)
    F.set_switch(world.root, "refill", True)              # the architect at the keyboard fills the queue
    _mail(world, DONE, PLAIN)                             # asks nothing, and no work is open

    _cycles(world, clock, 2 * HOUR)

    assert _on(sent, "desktop") == ["proj: reports wait for the architect"] and len(_on(sent, "telegram")) == 1
    assert _mails(sent) == [f"{DONE} in agent-mail/, waiting since {_since(DONE)} — the architect session is "
                            "interactive and reads it when prompted"]
    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["present-pending"]

    _mail(world, LATER, PLAIN)
    _cycles(world, clock, 30 * 60)

    assert _on(sent, "desktop") == ["proj: reports wait for the architect"] * 2 and len(_on(sent, "telegram")) == 2
    assert _mails(sent)[1:] == [f"2 reports in agent-mail/, waiting since {_since(DONE)}, the newest {LATER} — the "
                                "architect session is interactive and reads them when prompted"]


# ---- 2. an open decision ---------------------------------------------------------------------

def test_an_open_decision_rings_once_for_its_question_and_mails_on_reds_schedule_until_answered(
        project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    root, start = world.root, clock[0]
    first = A.ask(root, "which store keeps the ledger?", ["files", "sqlite"])

    for minute in range(15, 7 * 60 + 16, 5):             # its own check, every cycle for seven hours
        clock[0] = start + minute * 60
        assert W.escalate_open_decisions(root, "proj") == [first["id"]]

    assert _on(sent, "desktop") == ["proj: decision waiting"] and len(_on(sent, "telegram")) == 1
    assert _mails(sent) == [f"{first['id']}: which store keeps the ledger? — open 75m; ao answer {first['id']} <key>",
                            f"{first['id']}: which store keeps the ledger? — open 435m; ao answer {first['id']} <key>"]

    second = A.ask(root, "who reviews the ledger?", ["a person", "the reviewer"])
    clock[0] += 16 * 60
    W.escalate_open_decisions(root, "proj")

    assert len(_on(sent, "desktop")) == 2 and len(_on(sent, "telegram")) == 2      # another decision is told

    A.answer(root, first["id"], "a")
    A.answer(root, second["id"], "a")
    sent.clear()
    assert W.escalate_open_decisions(root, "proj") == [] and sent == []


def test_a_decision_no_architect_will_act_on_is_told_at_once_by_its_own_alarm_and_not_as_needs_you(
        project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    decision = A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"])

    world.cycle(dry_run=False)

    assert [(title, msg) for kind, title, msg, _ in sent if kind == "desktop"] == [
        ("proj: decision waiting", f"{decision['id']}: which store keeps the ledger? — open 0m; ao answer "
                                   f"{decision['id']} <key> — {WAKES_OFF}")]

    _cycles(world, clock, 3 * HOUR)

    assert _on(sent, "desktop") == ["proj: decision waiting"] and len(_on(sent, "telegram")) == 1
    assert _on(sent, "email") == ["proj: decision waiting"]
    assert [alarm["key"] for alarm in A.active_alarms("proj")] == [f"decision-open:{decision['id']}"]


def test_a_decision_held_for_a_coming_wake_still_reaches_a_person_once_after_its_wait(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path, wakes=True)
    decision = A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"])

    world.cycle(dry_run=False)
    assert len(_architect_wakes(world)) == 1 and _on(sent, "desktop") == []      # the wake it is owed (#20)

    _cycles(world, clock, 3 * HOUR)

    told = [(title, msg) for kind, title, msg, _ in sent if kind == "desktop"]
    assert [title for title, _ in told] == ["proj: decision waiting"]
    assert told[0][1].startswith(f"{decision['id']}: which store keeps the ledger? — open 15m;")
    assert len(_on(sent, "telegram", "decision waiting")) == 1 and _on(sent, "email") == ["proj: decision waiting"]


def test_a_request_behind_a_decision_is_told_while_the_wake_fails(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path, wakes=True)
    failure = {"text": "env: node: No such file or directory", "binary": "/agents/claude 2.1.261",
               "when": "2026-09-17 10:00:00", "at": clock[0] - 60, "kind": "binary", "resets_at": None}
    monkeypatch.setattr(W, "wake_error", lambda log_path: dict(failure))
    W.save_state(world.root, {"wake_error": dict({key: failure[key] for key in ("text", "binary", "when", "kind")},
                                                 at=clock[0] - 60)})
    decision = A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"])
    _mail(world, REQUEST, BLOCKED)

    world.cycle(dry_run=False)

    desktop = {title: msg for kind, title, msg, _ in sent if kind == "desktop"}
    assert desktop["proj: decision waiting"].startswith(f"{decision['id']}: which store keeps the ledger?")
    assert desktop["proj: needs you"].startswith(f"decision-requested: {REQUEST} in agent-mail/")
    assert len(_on(sent, "desktop")) == 2


def test_a_snooze_on_a_decision_keeps_it_off_every_channel_and_hides_no_request(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    decision = A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"])
    A.alarm_snooze("proj", f"decision-open:{decision['id']}", clock[0] + 10 * DAY, by="owner", why="asked on Monday")
    _mail(world, REQUEST, BLOCKED)

    _cycles(world, clock, 2 * HOUR)

    assert _on(sent, "desktop") == ["proj: needs you"] and _on(sent, "email") == ["proj: needs you"]
    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["anomaly:decision-requested"]


# ---- 3. an unseen request --------------------------------------------------------------------

def test_an_unseen_request_rings_as_it_crosses_orange_and_red_and_mails_on_reds_schedule(
        project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path, wakes=True)
    world.mail(REQUEST, BLOCKED)                          # nobody is shown it
    written = clock[0] - 10 * 60
    os.utime(os.path.join(world.root, "agent-mail", REQUEST), (written, written))

    def minutes(channel):
        return [int((at - written) / 60) for kind, title, _, at in sent
                if kind == channel and "unread decision request" in title]

    _cycles(world, clock, 50 * 60)                        # yellow, up to the minute before orange: the architect's

    told = [row for row in A.notices(world.root, limit=400, include_suppressed=True)
            if row["key"] == f"unseen:{REQUEST}"]
    assert told and not any(row["sent"] for row in told) and minutes("desktop") == []

    _cycles(world, clock, 10 * HOUR + 10 * 60)

    assert minutes("desktop") == [60, 240] and minutes("telegram") == [60, 240]
    assert minutes("email") == [240, 600]
    [alarm] = [alarm for alarm in A.active_alarms("proj") if alarm["key"] == f"unseen:{REQUEST}"]
    assert alarm["ring"] == "red"


# ---- 4. a failing wake -----------------------------------------------------------------------

def test_a_wake_failing_on_a_transport_error_is_one_alarm_and_no_architect_woken_line(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path, wakes=True)
    started = _wakes(world, monkeypatch, clock, lambda n: OVERLOADED % n)     # a new request id every time
    _mail(world, REQUEST, BLOCKED)

    _cycles(world, clock, 8 * HOUR)

    assert len(started) == 32                                                 # retried every fifteen minutes
    assert _on(sent, "telegram", "Mimar uyandırıldı") == [WOKEN]              # the first, before any failed
    assert _on(sent, "desktop", "uyandırılamadı") == ["proj: mimar uyandırılamadı"]
    assert len(_on(sent, "telegram", "uyandırılamadı")) == 1
    assert len(_mails(sent, "uyandırılamadı")) == 2                           # red after its hour, six hours on
    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["architect-wake-failed"]


def test_a_wake_that_works_after_failures_is_told_once(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path, wakes=True)
    started = _wakes(world, monkeypatch, clock, lambda n: OVERLOADED % n if n < 5 else None)
    _mail(world, REQUEST, BLOCKED)

    _cycles(world, clock, 3 * HOUR)

    assert len(started) == 5                              # the fifth worked, and was handed the reports
    assert _on(sent, "telegram", "Mimar uyandırıldı") == [WOKEN, WOKEN_AT_LAST]
    assert "wake_retry" not in W.load_state(world.root)


def test_a_transport_that_fails_in_another_way_is_told_again(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path, wakes=True)
    _wakes(world, monkeypatch, clock, lambda n: OVERLOADED % n if n < 9
           else 'API Error: 500 {"type":"api_error","message":"Internal server error"}')
    _mail(world, REQUEST, BLOCKED)

    _cycles(world, clock, 3 * HOUR)

    mailed = _mails(sent, "uyandırılamadı")
    assert len(mailed) == 2 and "API Error: 529" in mailed[0] and "API Error: 500" in mailed[1]


def test_a_failure_is_named_by_its_kind_binary_and_words_not_its_time_or_request_id():
    one = {"kind": "other", "binary": "/agents/claude 2.1.261", "when": "2026-09-17 10:00:00", "at": 1,
           "text": OVERLOADED % 1}
    again = dict(one, when="2026-09-17 10:15:00", at=901, text=OVERLOADED % 2)

    assert W.wake_failure_told(one) == W.wake_failure_told(again)
    assert W.wake_failure_told(one) != W.wake_failure_told(dict(one, text='API Error: 500 {"type":"api_error"}'))
    assert W.wake_failure_told(one) != W.wake_failure_told(dict(one, binary="/agents/claude 2.1.262"))
    assert W.wake_failure_told(one) != W.wake_failure_told(dict(one, kind="session"))


def test_the_doctor_raises_a_failed_wake_saying_what_the_watchdog_says(project, monkeypatch):
    raised = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: raised.append(kw) or True)
    failure = {"text": "env: node: No such file or directory", "binary": "/agents/claude 2.1.261",
               "when": "2026-09-17 10:00:00", "at": time.time() - 60, "kind": "binary", "resets_at": None}
    monkeypatch.setattr(W, "wake_error", lambda log_path: dict(failure))
    monkeypatch.setattr(cli, "doctor_problems", lambda cfg: [
        ("wake-failed", "last architect wake failed (binary): env: node: No such file or directory")])

    assert cli._doctor_check(project, page=True) == 1

    assert [(kw["key"], kw["what"]) for kw in raised] == [("architect-wake-failed", W.wake_failure_told(failure))]
