"""A window is read whole, whatever the notices ledger still holds (NOTICE-WINDOW).

Whether a notice may ring again was read from the last 100 KB of `.ao/ledger/notices.jsonl`.
Rebuilt after two weeks off, a day of notices was more than that: a review parked through the
day rang the desktop and the phone again each time its last ring had left those 100 KB, and
the ledger's own bound (`retention.observation_kb`) can keep less than a window. When each
key was last recorded and last sent is now folded beside the ledger as each notice is written,
before anything trims the ledger, and a check reads that and the lines written after the
newest one folded.
"""
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

from ao import cli, email, lib as A, telegram
from ao import watchdog as W

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
HOUR = 3600
UNSEEN = "unseen:20260902-0900-implementer-to-architect-BLOCKED-credits.md"


def _clock(monkeypatch, start=None):
    clock = [time.time() if start is None else start]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    return clock


def _ledger(root):
    return os.path.join(root, ".ao", "ledger", "notices.jsonl")


def _folded(root):
    with open(A.notice_times_path(root), encoding="utf-8") as fh:
        return json.load(fh)


def _bound(project, kb):
    with open(os.path.join(project["root"], ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump(dict({k: v for k, v in project.items() if k != "root"}, retention={"observation_kb": kb}), fh)


def _busy(root, clock, hours, rows=800):
    """`rows` held notices about seven other conditions, spread over `hours`: a busy ledger."""
    for n in range(rows):
        clock[0] += hours * HOUR / rows
        A.record_notice(root, f"proj: condition {n % 7}", "x" * 200, False, key=f"other-{n % 7}")


def _channels(monkeypatch):
    """What rang the desktop and the phone; a mail is taken and dropped."""
    rung = []
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: rung.append(title) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: rung.append(text) or 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None: True)
    return rung


def test_a_window_whose_notices_exceed_100_kb_still_sees_its_first_ring(project, monkeypatch):
    root = project["root"]
    clock = _clock(monkeypatch)
    rung = _channels(monkeypatch)
    A.record_notice(root, "proj: a review is parked", "the reviewer was unavailable", True, key="review-parked")
    with open(_ledger(root), "rb") as fh:
        first = fh.readline()
    _busy(root, clock, hours=20)

    with open(_ledger(root), "rb") as fh:
        assert first not in fh.read()[-100_000:]
    assert A.notice_recently_sent(root, "review-parked", 24 * HOUR)
    assert A.notice_recently_recorded(root, "review-parked", 24 * HOUR)
    assert W.notify("proj: a review is parked", "the reviewer was unavailable", root, key="review-parked",
                    window=24 * HOUR, audience="human") is False
    assert rung == []

    # A ledger nothing has folded, or a fold that cannot be read, is read back to the window's
    # start, and reading it writes nothing.
    os.remove(A.notice_times_path(root))
    assert A.notice_recently_sent(root, "review-parked", 24 * HOUR)
    assert not A.notice_recently_sent(root, "review-parked", 19 * HOUR)
    with open(A.notice_times_path(root), "w", encoding="utf-8") as fh:
        fh.write("{")
    assert A.notice_recently_sent(root, "review-parked", 24 * HOUR)
    os.remove(A.notice_times_path(root))
    assert A.notice_recently_recorded(root, "review-parked", 24 * HOUR)
    assert not os.path.exists(A.notice_times_path(root))

    A.record_notice(root, "proj: condition 0", "x", False, key="other-0")      # the next notice folds the ledger again
    assert "review-parked" in _folded(root)["keys"]
    clock[0] += 5 * HOUR
    assert not A.notice_recently_sent(root, "review-parked", 24 * HOUR)
    assert W.notify("proj: a review is parked", "the reviewer was unavailable", root, key="review-parked",
                    window=24 * HOUR, audience="human") is True
    assert rung[0] == "proj: a review is parked"


def test_a_ledger_its_bound_has_trimmed_still_answers_for_the_whole_window(project, monkeypatch):
    root = project["root"]
    _bound(project, 64)
    clock = _clock(monkeypatch)
    A.record_notice(root, "proj: hold standing 4h", "set by owner", True, key="hold-standing")
    A.record_notice(root, "proj: anomaly", "report-waiting", False, key="anomaly:report-waiting")
    _busy(root, clock, hours=5, rows=900)

    assert os.path.getsize(_ledger(root)) <= 64 * 1024 * 1.25 + 400
    with open(_ledger(root), encoding="utf-8") as fh:
        keys = {json.loads(line)["key"] for line in fh}
    assert not keys & {"hold-standing", "anomaly:report-waiting"}
    assert A.notice_recently_sent(root, "hold-standing", 6 * HOUR)
    assert A.notice_recently_recorded(root, "anomaly:report-waiting", 6 * HOUR)
    assert not A.notice_recently_sent(root, "anomaly:report-waiting", 6 * HOUR)       # held, never delivered

    clock[0] += 1.5 * HOUR
    assert not A.notice_recently_sent(root, "hold-standing", 6 * HOUR)


def test_what_no_fold_counted_is_folded_before_the_watchdog_or_a_prune_trims_it(project, monkeypatch):
    root = project["root"]
    clock = _clock(monkeypatch)
    with monkeypatch.context() as patched:
        patched.setattr(A, "fold_notice_times", lambda target: False)            # folds that could not be written
        A.record_notice(root, "proj: architect wake failed", "binary", True, key="architect-wake-failed")
        _busy(root, clock, hours=3, rows=600)
    assert not os.path.exists(A.notice_times_path(root))
    _bound(project, 64)

    assert A.bound_observation_logs(root, W.STATE_DIR) == [_ledger(root)]

    with open(_ledger(root), encoding="utf-8") as fh:
        assert "architect-wake-failed" not in fh.read()
    assert A.notice_recently_sent(root, "architect-wake-failed", 6 * HOUR)

    with monkeypatch.context() as patched:
        patched.setattr(A, "fold_notice_times", lambda target: False)
        A.record_notice(root, "proj: decision waiting", "D-1 waits", True, key="decision-open:D-1")
    clock[0] += 1.5 * HOUR

    assert cli.cmd_prune(project, SimpleNamespace(days=1 / 24, keep_kb=64, evidence=False, yes=True,
                                                  review_days=30)) == 0

    with open(_ledger(root), encoding="utf-8") as fh:
        assert "decision-open:D-1" not in fh.read()
    assert A.notice_recently_sent(root, "decision-open:D-1", 2 * HOUR)


RING = ("import sys; from ao import lib as A; "
        "A.record_notice(sys.argv[1], 'proj: hold standing 5h', 'set by owner', True, key='hold-standing')")
BUSY = ("import sys; from ao import lib as A; "
        "[A.record_notice(sys.argv[1], 'proj: condition', 'x' * 200, False, key='other-%d' % (n % 7)) "
        "for n in range(500)]")


def _process(root, home, script):
    env = dict(os.environ, HOME=home, PYTHONPATH=SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))
    done = subprocess.run([sys.executable, "-c", script, root], env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_a_window_holds_across_processes_and_across_folds_that_overtake_each_other(project, tmp_path):
    root = project["root"]
    _process(root, str(tmp_path / "home"), RING)             # one cycle rings
    _process(root, str(tmp_path / "home"), BUSY)             # the cycles after it write more than 100 KB

    assert os.path.getsize(_ledger(root)) > 100_000
    assert A.notice_recently_sent(root, "hold-standing", 6 * HOUR)

    # Two processes fold at once, and the one that folded less is replaced last: what it did not fold is read.
    with open(A.notice_times_path(root), encoding="utf-8") as fh:
        older = fh.read()
    A.record_notice(root, "proj: decision waiting", "D-1 waits", True, key="decision-open:D-1")
    with open(A.notice_times_path(root), "w", encoding="utf-8") as fh:
        fh.write(older)
    assert "decision-open:D-1" not in _folded(root)["keys"]
    assert A.notice_recently_sent(root, "decision-open:D-1", HOUR)
    A.record_notice(root, "proj: condition", "x", False, key="other-0")
    assert "decision-open:D-1" in _folded(root)["keys"]


def test_a_resume_notice_rings_every_key_it_named_for_that_keys_whole_window(project, monkeypatch):
    root = project["root"]
    _bound(project, 64)
    clock = _clock(monkeypatch)
    A.record_notice(root, "proj: watchdog resumed after 14d", "2 stand", True, key="resume",
                    named=["review-parked", "hold-standing"])
    A.record_notice(root, "proj: watchdog resumed after 14d", "1 stands", False, key="resume", named=[UNSEEN])
    _busy(root, clock, hours=5, rows=900)

    with open(_ledger(root), encoding="utf-8") as fh:
        assert '"resume"' not in fh.read()
    assert A.notice_recently_sent(root, "review-parked", 24 * HOUR)
    assert A.notice_recently_sent(root, "hold-standing", 6 * HOUR)
    assert A.notice_recently_recorded(root, UNSEEN, 6 * HOUR)
    assert not A.notice_recently_sent(root, UNSEEN, 6 * HOUR)             # told to the architect, not delivered

    clock[0] += 1.5 * HOUR
    assert not A.notice_recently_sent(root, "hold-standing", 6 * HOUR)
    assert A.notice_recently_sent(root, "review-parked", 24 * HOUR)


def test_what_is_folded_is_bounded_by_keys_and_by_age(project, monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "NOTICE_TIMES_KEYS", 50)                     # the bound, small enough to cross quickly
    clock = _clock(monkeypatch, start=1_790_000_000.0)
    for n in range(150):
        clock[0] += 1
        A.record_notice(root, "proj: unread decision request", "x", n % 2 == 0, key=f"unseen:{n}")

    folded = _folded(root)
    assert len(folded["keys"]) == 50
    assert "unseen:99" not in folded["keys"] and "unseen:100" in folded["keys"]
    assert folded["forgotten_through"] == 1_790_000_100
    # A key the fold let go is read from the ledger while a window reaches back past it.
    assert A.notice_recently_recorded(root, "unseen:0", 24 * HOUR)
    assert A.notice_recently_sent(root, "unseen:0", HOUR) and not A.notice_recently_sent(root, "unseen:1", HOUR)

    clock[0] += (A.NOTICE_TIMES_DAYS + 1) * 86400
    A.record_notice(root, "proj: hold standing 4h", "set by owner", True, key="hold-standing")

    folded = _folded(root)
    assert list(folded["keys"]) == ["hold-standing"] and os.path.getsize(A.notice_times_path(root)) < 1000
    assert not A.notice_recently_recorded(root, "unseen:149", 24 * HOUR)


def test_a_storm_notice_rings_once_in_its_hour_and_what_the_storm_held_was_not_sent(project, monkeypatch):
    root = project["root"]
    clock = _clock(monkeypatch)
    rung = _channels(monkeypatch)
    for n in range(12):
        A.record_notice(root, f"proj: condition {n}", "m", True, key=f"k{n}")

    assert W.notify("proj: provider degraded", "529s", root, key="provider-degraded", audience="human") is False
    _busy(root, clock, hours=0.5, rows=600)
    assert W.notify("proj: out of quota", "window exhausted", root, key="out-of-quota", audience="human") is False

    assert rung == ["proj: alert storm"]
    assert A.notice_recently_recorded(root, "provider-degraded", HOUR)
    assert not A.notice_recently_sent(root, "provider-degraded", HOUR)
