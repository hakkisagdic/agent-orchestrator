"""A reviewer whose harness waits for a person to sign in is stopped and says so (KIRO-SIGNED-OUT).

Measured on kiro-cli 2.27.1 on 2026-10-07: with its sign-in expired, a review printed `Opening browser... | Press (^)
+ C to cancel` and waited ten minutes for a browser nobody would open, then ended on `error: OAuth error: Auth portal
timed out` and exit 1, and the review was UNAVAILABLE with no word of why; two catch-up reviews in a row spent twenty
minutes so. Kiro's adapter names what it prints then, and ao reads it at each heartbeat, stops the reviewer, and says
a person signs in with `kiro-cli login`.
"""
import os
import sys
import time

import pytest

from ao import cli


def _kiro(tmp_path, script):
    """A stand-in kiro-cli: ao knows it by its name."""
    harness = tmp_path / "kiro-cli"
    harness.write_text(f"#!{sys.executable}\nimport sys, time\n{script}", encoding="utf-8")
    harness.chmod(0o755)
    return [str(harness), "chat", "--no-interactive", "review this", "--trust-tools="]


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs, and Windows reads a "
                                            "child's streams only once they close")
def test_a_reviewer_waiting_for_a_sign_in_is_stopped_at_the_next_beat(project, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "REVIEW_HEARTBEAT_SECONDS", 1)
    argv = _kiro(tmp_path, "print('\\u25b0\\u25b0\\u25b1 Opening browser... | Press (^) + C to cancel', flush=True)\n"
                           "time.sleep(120)\n")
    started = time.monotonic()

    attempt = cli._run_reviewer(project["root"], argv, 90)

    assert time.monotonic() - started < 60
    assert attempt["kind"] == "signed-out" and not attempt["retryable"], attempt
    assert "a person runs `kiro-cli login`" in attempt["reason"] and "Opening browser" in attempt["reason"]


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_a_reviewer_that_ended_on_its_sign_in_says_so(project, tmp_path):
    argv = _kiro(tmp_path, "sys.stderr.write('error: OAuth error: Auth portal timed out\\n')\nsys.exit(1)\n")

    attempt = cli._run_reviewer(project["root"], argv, 30)

    assert attempt["kind"] == "signed-out" and not attempt["retryable"], attempt
    assert "Auth portal timed out" in attempt["reason"]


def test_an_answer_that_quotes_the_words_is_no_sign_in():
    spec = cli._signed_out_spec(["kiro-cli", "chat"])
    quoted = '{"type":"sessionUpdate","data":{"update":{"text":"the diff prints Opening browser... for kiro"}}}'

    assert cli._signed_out_line(spec, quoted) is None
    assert cli._signed_out_line(spec, "▰ Opening browser... | Press (^) + C to cancel").startswith("▰")


def test_a_harness_whose_adapter_names_no_sign_in_is_read_as_before():
    assert cli._signed_out_spec(["claude", "-p", "x"]) is None
    assert cli._signed_out_line(None, "Opening browser...") is None
