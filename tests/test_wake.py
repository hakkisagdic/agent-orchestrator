import time

from ao import watchdog as W


def _log(tmp_path, *segments):
    p = tmp_path / "escalate-x.log"
    p.write_text("".join(segments), encoding="utf-8")
    return str(p)


def test_binary_failure_is_read_with_its_binary(tmp_path):
    p = _log(tmp_path, "\n=== 2026-09-05 17:31:32 escalate /usr/local/bin/claude 2.1.185 ===\n"
             "API Error: 400 Claude Code 2.1.185 does not support this model; version 2.1.251 or newer is required.\n")
    e = W.wake_error(p)
    assert e["kind"] == "binary" and e["binary"] == "/usr/local/bin/claude 2.1.185"
    assert "does not support" in e["text"]


def test_quota_failure_carries_reset_time(tmp_path):
    p = _log(tmp_path, "\n=== 2026-09-05 01:20:00 escalate /x/claude 2.1.261 ===\n"
             "You've hit your session limit · resets 4:30am (Europe/Istanbul)\n")
    e = W.wake_error(p)
    assert e["kind"] == "quota"
    assert time.strftime("%H:%M", time.localtime(e["resets_at"])) == "04:30"
    assert e["resets_at"] > time.time() - 24 * 3600


def test_relative_reset_is_parsed():
    now = 1_000_000
    assert W.parse_reset("usage limit reached, resets in 4h 43m", now) == now + 4 * 3600 + 43 * 60


def test_session_failure_and_clean_log(tmp_path):
    p = _log(tmp_path, "\n=== 2026-09-05 17:31:32 escalate /x/claude 2.1.261 ===\n"
             "No conversation found with session ID: abc\n")
    assert W.wake_error(p)["kind"] == "session"
    p = _log(tmp_path, "\n=== 2026-09-05 17:31:32 escalate /x/claude 2.1.261 ===\nMimar: mailbox işlendi.\n")
    assert W.wake_error(p) is None


def test_only_the_last_segment_counts(tmp_path):
    p = _log(tmp_path, "\n=== 2026-09-05 16:00:00 escalate /old 2.1.185 ===\nAPI Error: 400 does not support this model\n",
             "\n=== 2026-09-05 18:00:00 escalate /new 2.1.261 ===\nok, processed\n")
    assert W.wake_error(p) is None
