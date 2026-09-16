import re
import sys

from ao import cli, lib as A


def _unregistered(monkeypatch):
    """Registering a helper reads the process table first: on Windows a PowerShell query that
    can outlast these reviewers whole, which the heartbeat is not about (#71)."""
    monkeypatch.setattr(A, "helper_register", lambda *args, **kwargs: None)
    monkeypatch.setattr(A, "helper_release", lambda *args, **kwargs: None)


def test_a_reviewer_that_sleeps_then_fails_shows_it_was_alive_and_how_it_ended(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "REVIEW_HEARTBEAT_SECONDS", 0.1)
    _unregistered(monkeypatch)
    script = "import sys, time; time.sleep(0.45); sys.exit(3)"

    attempt = cli._run_reviewer(project["root"], [sys.executable, "-c", script], timeout=10, label="r1")

    out = capsys.readouterr().out
    assert attempt["kind"] == "nonzero-exit" and attempt["reason"] == "exited 3"
    assert re.search(r"reviewer r1 still working: \d+\.\ds elapsed, pid \d+", out), out
    assert re.search(r"reviewer r1 exited 3 after \d+\.\ds", out), out


def test_the_heartbeat_waits_add_up_to_the_timeout_and_no_further(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "REVIEW_HEARTBEAT_SECONDS", 0.1)
    _unregistered(monkeypatch)
    script = "import time; time.sleep(5)"

    attempt = cli._run_reviewer(project["root"], [sys.executable, "-c", script], timeout=0.35, label="slow")

    out = capsys.readouterr().out
    assert attempt["kind"] == "timeout"
    assert len(re.findall(r"reviewer slow still working", out)) == 3
