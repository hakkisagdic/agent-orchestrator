import os
import time

from ao import lib as A


def _write(root, name, body):
    p = os.path.join(root, "agent-mail", name)
    open(p, "w", encoding="utf-8").write(body)
    return p


def test_waiting_on_architect_when_request_stands_and_inbox_empty(project):
    root = project["root"]
    _write(root, "20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    assert A.waiting_on_architect(root, project)[0].startswith("20260905-0631")


def test_unread_inbox_mail_overrides_waiting(project):
    root = project["root"]
    _write(root, "20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## KARAR GEREKLİ\n")
    # the answer is OLDER than the repeat and still the answer
    _write(root, "20260905-0600-fable-to-kiro-DECISION-refill.md", "# refill\n\nB7\n")
    assert A.waiting_on_architect(root, project) is None


def test_queued_work_means_not_waiting(project):
    root = project["root"]
    _write(root, "20260905-0631-kiro-to-fable-BLOCKED-queue.md", "# x\n\n## KARAR GEREKLİ\n")
    b = os.path.join(root, ".ao", "board.md")
    _t = open(b, encoding="utf-8").read()
    open(b, "w", encoding="utf-8").write(_t.replace("## queued\n", "## queued\n- [B7] next · acceptance: #7\n"))
    assert A.waiting_on_architect(root, project) is None


def test_status_report_is_not_waiting(project):
    root = project["root"]
    _write(root, "20260905-0631-kiro-to-fable-DONE-b6.md", "# done\n\n## DONE\n\nBlockers: none\n")
    assert A.waiting_on_architect(root, project) is None


def test_product_dirty_ignores_coordination_dirs(project):
    root = project["root"]
    _write(root, "20260905-0631-kiro-to-fable-BLOCKED-q.md", "# q\n")
    open(os.path.join(root, ".ao", "x.json"), "w", encoding="utf-8").write("{}")
    assert A.product_dirty(root, project) == []
    open(os.path.join(root, "src.py"), "w", encoding="utf-8").write("x")
    assert len(A.product_dirty(root, project)) == 1


def test_name_time_parses_mailbox_stamps():
    t = A._name_time("20260905-0631-kiro-to-fable-BLOCKED-q.md")
    assert time.strftime("%Y%m%d-%H%M", time.localtime(t)) == "20260905-0631"
    assert A._name_time("watchdog-to-fable-ANOMALY-x.md") is None


def test_respecified_slice_restarts_round_budget(project, monkeypatch):
    root = project["root"]
    b = os.path.join(root, ".ao", "board.md")
    _t = open(b, encoding="utf-8").read()
    open(b, "w", encoding="utf-8").write(_t.replace("## running\n", "## running\n- [B6] slice · since: 2026-09-04 10:00\n"))
    rev = os.path.join(root, "semantic-review")
    for i, verdict in enumerate(["NEEDS_CHANGES"] * 3):
        p = os.path.join(rev, f"2026-09-04-1{i}0000-x.md")
        open(p, "w", encoding="utf-8").write(f"# review\n\nVerdict: {verdict}\n")
    monkeypatch.setattr(A, "reviews", lambda root, d, limit=50: [(f, "NEEDS_CHANGES") for f in sorted(os.listdir(rev), reverse=True)])
    assert A.rounds(root, "semantic-review") == 3
    with open(os.path.join(root, ".ao", "ledger", "decisions.jsonl"), "a", encoding="utf-8") as fh:
        fh.write('{"id":"AD-1","at":%d,"scope":"B6","by":"architect"}\n' % int(time.time() + 5))
    assert A.rounds(root, "semantic-review") == 0


def test_alarm_ladder_orange_becomes_red_after_an_hour(project):
    now = 1_000_000
    ring, e = A.alarm_touch("proj", "wake-failed", "orange", now=now)
    assert ring == "orange"
    ring, e = A.alarm_touch("proj", "wake-failed", "orange", now=now + 30 * 60)
    assert ring == "orange"
    ring, e = A.alarm_touch("proj", "wake-failed", "orange", now=now + 61 * 60)
    assert ring == "red" and e["red_due"] is True
    A.alarm_mailed("proj", "wake-failed", now=now + 61 * 60)
    ring, e = A.alarm_touch("proj", "wake-failed", "orange", now=now + 90 * 60)
    assert ring == "red" and e["red_due"] is False        # mailed once, not again yet
    t = now + 90 * 60
    while t < now + 61 * 60 + 7 * 3600:                    # condition keeps being raised
        t += 30 * 60
        ring, e = A.alarm_touch("proj", "wake-failed", "orange", now=t)
    assert ring == "red" and e["red_due"] is True         # six hours after the mail: again


def test_alarm_episode_resets_after_silence(project):
    now = 1_000_000
    A.alarm_touch("proj", "hold", "orange", now=now)
    ring, e = A.alarm_touch("proj", "hold", "orange", now=now + 5 * 3600)   # silent 5h
    assert ring == "orange" and e["first"] == now + 5 * 3600


def test_explicit_red_rings_immediately(project):
    ring, e = A.alarm_touch("proj", "credits", "red", now=5)
    assert ring == "red" and e["red_due"] is True


def test_running_slice_is_open_work(project):
    from ao import watchdog as W
    root = project["root"]
    assert W.open_work(project, root) == []
    b = os.path.join(root, ".ao", "board.md")
    _t = open(b, encoding="utf-8").read()
    open(b, "w", encoding="utf-8").write(_t.replace("## running\n", "## running\n- [B7] slice · since: 2026-09-05 18:17\n"))
    assert "slice running" in W.open_work(project, root)
