"""What waits for an architect nobody will wake is one alarm (WAITING-ONE-ALARM).

With architect wakes switched off, a decision request was told twice: "needs you" under its
anomaly's key, and "2 report(s) waiting and architect wakes are off" under `reports-no-wake`,
counting the watchdog's own report of that anomaly as a second report. Neither said which
report or since when. A report an anomaly stands for is now named by that anomaly's alarm;
`reports-no-wake` rings only for the reports no anomaly stands for.
"""
import os
import time

from ao import email, features as F, lib as A, telegram
from ao import watchdog as W
from tests.scenarios import World

NOTIFY = W.notify                  # the scenario world replaces it; the ladder tests need the real one
HOUR, DAY = 3600, 86400
REQUEST = "20260902-0900-kiro-to-fable-BLOCKED-credits.md"
LATER = "20260903-1000-kiro-to-fable-BLOCKED-store.md"
BLOCKED = "# credits are exhausted until the reset\n\n## KARAR GEREKLİ\n\nnew account or wait?\n"
DONE = "20260902-0830-kiro-to-fable-DONE-b6.md"
PLAIN = "# b6 done\n\nBlockers: none\n"
WAKES_OFF = "no architect will act on it: architect wakes are switched off"


def _since(name):
    return W._when(A._name_time(name))


def _heartbeat(root, at):
    path = A.heartbeat_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(int(at)))
    os.utime(path, (at, at))


def _world(project, monkeypatch, tmp_path, wakes=False, real=True):
    """A scenario world on a clock the test moves; with `real`, the real ladder and recorded channels."""
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    world = World(project, monkeypatch, tmp_path)
    sent = []
    if real:
        monkeypatch.setattr(W, "notify", NOTIFY)
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: sent.append(("desktop", title, msg)) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None:
                        sent.append(("telegram", text, "")) or 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None:
                        sent.append(("email", subject, body)) or True)
    monkeypatch.setattr(A, "heartbeat", lambda target: _heartbeat(target, clock[0]))
    for switch in ("nudge", "refill") + (() if wakes else ("architect_wake",)):
        F.set_switch(world.root, switch, False)
    world.transcript_age(HOUR)
    return world, sent, clock


def _mail(world, name, body):
    world.mail(name, body)
    A.mail_seen(world.root, [name], by="architect")       # shown, and not answered


def _raised(monkeypatch):
    raised = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: raised.append((title, msg, kw)) or True)
    return raised


def _needs_you(raised):
    return {kw["key"]: (msg, kw.get("what")) for title, msg, kw in raised if title == "proj: needs you"}


def _on(sent, channel):
    return [title for kind, title, _ in sent if kind == channel]


def _mails(sent):
    return [body.split("\n")[0] for kind, _, body in sent if kind == "email"]


def _cycles(world, clock, seconds):
    end = clock[0] + seconds
    while clock[0] < end:
        world.cycle(dry_run=False)
        clock[0] += 300


# ---- one request, one alarm ---------------------------------------------------------------------

def test_a_request_nobody_is_woken_for_is_one_alarm_that_says_which_report_since_when_and_why(
        project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    _mail(world, REQUEST, BLOCKED)

    trace = world.cycle()
    assert sent == [] and any("named by the alarm of the anomaly they stand for" in line for line in trace)

    _cycles(world, clock, 2 * HOUR)

    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["anomaly:decision-requested"]
    assert _on(sent, "desktop") == ["proj: needs you"] and len(_on(sent, "telegram")) == 1
    assert _mails(sent) == [
        f"decision-requested: {REQUEST} in agent-mail/, waiting since {_since(REQUEST)} — {WAKES_OFF}"]


def test_a_second_request_hours_later_is_told_again_by_the_same_alarm(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    _mail(world, REQUEST, BLOCKED)
    _cycles(world, clock, 3 * HOUR)
    assert len(_on(sent, "desktop")) == 1 and len(_mails(sent)) == 1

    _mail(world, LATER, BLOCKED)
    _cycles(world, clock, 30 * 60)

    assert _on(sent, "desktop") == ["proj: needs you"] * 2 and len(_on(sent, "telegram")) == 2
    mails = _mails(sent)
    assert len(mails) == 2 and mails[1] == (f"decision-requested: 2 reports in agent-mail/, waiting since "
                                            f"{_since(REQUEST)}, the newest {LATER} — {WAKES_OFF}")
    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["anomaly:decision-requested"]


def test_a_plain_report_while_work_is_open_is_named_by_its_anomaly_alone(project, monkeypatch, tmp_path):
    world, _, _ = _world(project, monkeypatch, tmp_path, real=False)
    raised = _raised(monkeypatch)
    world.board("running", "- [S1] a slice · since: 2026-09-02 08:00")
    _mail(world, DONE, PLAIN)

    world.cycle(dry_run=False)

    assert _needs_you(raised) == {"anomaly:report-waiting": (
        f"report-waiting: {DONE} in agent-mail/, waiting since {_since(DONE)} — {WAKES_OFF}",
        f"report-waiting:{DONE}")}


def test_a_report_name_the_phone_would_read_as_markup_reaches_it_as_written(project, monkeypatch, tmp_path):
    world, sent, _ = _world(project, monkeypatch, tmp_path)
    request = "20260902-0900-kiro-to-fable-BLOCKED-account_usage-returns-none.md"
    _mail(world, request, BLOCKED)

    world.cycle(dry_run=False)

    [phone] = _on(sent, "telegram")
    assert phone == ("*proj: needs you*\ndecision-requested: 20260902-0900-kiro-to-fable-BLOCKED-account\\_usage-"
                     f"returns-none.md in agent-mail/, waiting since {_since(request)} — {WAKES_OFF}")


# ---- where they differ, they stay two ----------------------------------------------------------

def test_a_report_no_anomaly_stands_for_is_told_on_its_own_beside_the_request(project, monkeypatch, tmp_path):
    world, _, _ = _world(project, monkeypatch, tmp_path, real=False)
    raised = _raised(monkeypatch)
    _mail(world, DONE, PLAIN)                             # asks nothing, and no work is open
    _mail(world, REQUEST, BLOCKED)

    world.cycle(dry_run=False)

    told = _needs_you(raised)
    assert set(told) == {"anomaly:decision-requested", "reports-no-wake"}
    assert told["reports-no-wake"] == (f"{DONE} in agent-mail/, waiting since {_since(DONE)} — {WAKES_OFF}", DONE)
    assert told["anomaly:decision-requested"][0].startswith(f"decision-requested: {REQUEST} in agent-mail/")


def test_a_request_behind_an_open_decision_is_not_hidden_by_the_decisions_alarm(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    decision = A.ask(world.root, "which store keeps the ledger?", ["files", "sqlite"])
    _mail(world, REQUEST, BLOCKED)                        # its anomaly is of the decision's kind

    _cycles(world, clock, HOUR)

    # The decision is told by its own alarm, so the anomaly's "needs you" names the request (NOISE-REPEATS).
    told = [(title, msg) for kind, title, msg in sent if kind == "desktop"]
    assert [title for title, _ in told] == ["proj: decision waiting", "proj: needs you"]
    assert told[0][1].startswith(f"{decision['id']}: which store keeps the ledger?")
    assert told[1][1].startswith(f"decision-requested: {REQUEST} in agent-mail/, waiting since {_since(REQUEST)}")
    assert {alarm["key"] for alarm in A.active_alarms("proj")} == {f"decision-open:{decision['id']}",
                                                                    "anomaly:decision-requested"}


def test_a_plain_report_alone_is_still_told_that_no_architect_will_read_it(project, monkeypatch, tmp_path):
    world, _, _ = _world(project, monkeypatch, tmp_path, real=False)
    raised = _raised(monkeypatch)
    _mail(world, DONE, PLAIN)

    world.cycle(dry_run=False)

    assert _needs_you(raised)["reports-no-wake"] == (
        f"{DONE} in agent-mail/, waiting since {_since(DONE)} — {WAKES_OFF}", DONE)


def test_with_wakes_on_a_failing_wake_leaves_the_request_to_its_anomaly_and_raises_no_waiting_alarm(
        project, monkeypatch, tmp_path):
    world, _, clock = _world(project, monkeypatch, tmp_path, wakes=True, real=False)
    raised = _raised(monkeypatch)
    failure = {"text": "env: node: No such file or directory", "binary": "/agents/claude 2.1.261",
               "when": "2026-09-17 10:00:00", "at": clock[0] - 60, "kind": "binary", "resets_at": None}
    monkeypatch.setattr(W, "wake_error", lambda log_path: dict(failure))
    W.save_state(world.root, {"wake_error": dict({key: failure[key] for key in ("text", "binary", "when", "kind")},
                                                 at=clock[0] - 60)})
    _mail(world, REQUEST, BLOCKED)

    trace = world.cycle(dry_run=False)

    told = _needs_you(raised)
    assert set(told) == {"anomaly:decision-requested"}
    assert told["anomaly:decision-requested"][0] == (
        f"decision-requested: {REQUEST} in agent-mail/, waiting since {_since(REQUEST)} — no architect will act on "
        "it: the last wake failed with this binary: env: node: No such file or directory")
    assert any("not retrying" in line for line in trace)


# ---- snoozes set on either key ------------------------------------------------------------------

def test_a_snooze_on_the_requests_anomaly_keeps_every_channel_quiet_about_it(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    A.alarm_snooze("proj", "anomaly:decision-requested", clock[0] + 10 * DAY, by="owner", why="answered on Monday")
    _mail(world, REQUEST, BLOCKED)

    _cycles(world, clock, 8 * HOUR)

    assert sent == [] and A.active_alarms("proj") == []


def test_a_snooze_on_reports_no_wake_holds_what_it_names_and_hides_no_request(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    A.alarm_snooze("proj", "reports-no-wake", clock[0] + 10 * DAY, by="owner", why="wakes are off on purpose")
    _mail(world, DONE, PLAIN)
    _mail(world, REQUEST, BLOCKED)

    _cycles(world, clock, 2 * HOUR)

    assert _on(sent, "desktop") == ["proj: needs you"] and len(_mails(sent)) == 1
    assert REQUEST in _mails(sent)[0] and DONE not in _mails(sent)[0]
    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["anomaly:decision-requested"]
    held = [row for row in A.notices(world.root, limit=400, include_suppressed=True) if row["key"] == "reports-no-wake"]
    assert held and all(row["msg"].startswith(DONE) and "[snoozed until" in row["msg"] for row in held)


# ---- what an anomaly stands for ------------------------------------------------------------------

def test_an_anomalys_own_report_is_known_by_its_kind_and_one_whose_condition_ended_is_not():
    standing = [{"kind": "decision-requested", "key": "implementer", "reports": [REQUEST]},
                {"kind": "review-returned", "key": "R-1"}]
    own = "watchdog-to-fable-ANOMALY-decision-requested-implementer.md"
    older = "20260916-1200-watchdog-to-fable-ANOMALY-decision-requested-20260902-0900-kiro-to-fable-BLOCKED-credits.md"
    returned = "watchdog-to-fable-ANOMALY-review-returned-R-1.md"
    ended = ["watchdog-to-fable-ANOMALY-review-loop.md", "watchdog-to-fable-ANOMALY-busy-without-progress.md"]
    lead = "hunter-to-fable-LEAD-parser.md"

    named = W.anomaly_reports(standing, [REQUEST, DONE, own, older, returned, lead] + ended)

    assert named == {REQUEST, own, older, returned}


def test_an_anomaly_without_reports_says_its_first_fact(project):
    decision = {"kind": "decision-requested", "key": "D-1", "facts": ["D-1 is open since 02 Sep 09:00", "which store?"]}

    assert W.anomaly_waits(project["root"], project, decision) == "decision-requested: D-1 is open since 02 Sep 09:00"
    assert W.anomaly_waits(project["root"], project, {"kind": "decision-requested", "facts": {"decision": "D-1"}}) \
        == "decision-requested"
