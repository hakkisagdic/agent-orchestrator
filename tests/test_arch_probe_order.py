"""The architect's program is asked its version only where a wake would ask it (ARCH-PROBE-ORDER).

Resolving a program runs it. The notice holder and doctor skipped the wake's command checks, then checked without
its session and prompt values. The first tests hid the refusal behind a config present only in memory. These tests
accept the configuration actually on disk, exercise each boundary separately, and observe both probes and alarms.
Dry escalation also accounted for alarms it never delivered, preparing a wake prompt and probing the architect
before building the implementer's nudge. Only live alarm bookkeeping needs that check (ARCH-PROBE-ORDER-2).
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, language, lib as A, watchdog as W
from tests import standin
from tests.test_turn_argv import _seen, _store


def _configured(project, **fields):
    project.update(_store(project["root"], architect=dict(project["architect"], **fields)))
    unseen = A.unseen_harness_config(project["root"])
    if unseen:
        assert _seen(project, by="Test Person", digest=A.harness_config_digest(unseen)) == 0
    assert W.turn_hold(project["root"], project, "architect") is None


def _program(directory, tmp_path):
    marker = tmp_path / "ran"
    program = standin.program(directory, "claude",
                              f"open({str(marker)!r}, 'w', encoding='utf-8').close()\nprint('9.9.0')\n")
    return program, marker


@pytest.fixture
def available(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(A, "architect_present", lambda root, arch: False)
    monkeypatch.setattr(W, "quota_ok", lambda adapter: True)
    monkeypatch.setattr(W, "arch_alive", lambda root, arch: False)
    monkeypatch.setattr(A, "window_headroom", lambda provider: (None, 10))
    found = A.program_path
    monkeypatch.setattr(A, "program_path", lambda name, *args, **kwargs: (
        str(tmp_path / "ao") if name == "ao" else found(name, *args, **kwargs)))


def _inspect(consumer, project, capsys):
    if consumer == "hold":
        return W.architect_hold_reason(project["root"], project, {}, {})
    capsys.readouterr()
    cli.cmd_doctor(project, cli.build_parser().parse_args(["doctor"]))
    return capsys.readouterr().out


def _stopped(consumer, result, code, reason):
    if consumer == "hold":
        assert result["holdable"] is False and result["code"] == code, result
        assert reason in result["reason"]
    else:
        line = next(line for line in result.splitlines() if line.startswith("architect bin"))
        assert "architect bin   not asked" in line and reason in line, line


@pytest.mark.parametrize("consumer", ["hold", "doctor"])
def test_a_program_inside_the_tree_is_refused_before_its_version_is_asked(
        project, tmp_path, available, capsys, consumer):
    program, marker = _program(project["root"], tmp_path)
    _configured(project, argv=[program, "-p", "{prompt}"])

    result = _inspect(consumer, project, capsys)

    assert not marker.exists()
    _stopped(consumer, result, "refused", "inside this repository")


@pytest.mark.parametrize("consumer", ["hold", "doctor"])
def test_an_unseen_command_is_held_before_its_version_is_asked(
        project, tmp_path, monkeypatch, available, capsys, consumer):
    assert A.unseen_harness_config(project["root"]) == []
    program, marker = _program(tmp_path / "bin", tmp_path)
    monkeypatch.setattr(W, "child_path", lambda: str(tmp_path / "bin"))
    project.update(_store(project["root"], architect=dict(project["architect"], argv=[program, "-p", "{prompt}"])))

    result = _inspect(consumer, project, capsys)

    assert not marker.exists()
    _stopped(consumer, result, "held", "nobody has seen")


@pytest.mark.parametrize("consumer", ["hold", "doctor"])
@pytest.mark.parametrize("field", ["session", "prompt"])
def test_a_value_that_reads_as_a_flag_is_refused_before_any_version_probe(
        project, tmp_path, monkeypatch, available, capsys, consumer, field):
    program, marker = _program(tmp_path / "bin", tmp_path)
    monkeypatch.setattr(W, "child_path", lambda: str(tmp_path / "bin"))
    _configured(project, argv=[program, "--resume", "{session}", "-p", "{prompt}"],
                session="test-session", **({field: "--x"} if field != "session" else {}))
    if field == "session":
        project.update(_store(project["root"], architect=dict(project["architect"], session="--x")))
    assert W.turn_hold(project["root"], project, "architect") is None

    result = _inspect(consumer, project, capsys)

    assert not marker.exists()
    _stopped(consumer, result, "refused", f"its {field} `--x` would be read as a flag")


@pytest.mark.parametrize("consumer", ["hold", "doctor"])
def test_an_unresolved_session_stops_before_any_version_probe(
        project, tmp_path, monkeypatch, available, capsys, consumer):
    program, marker = _program(tmp_path / "bin", tmp_path)
    monkeypatch.setattr(W, "child_path", lambda: str(tmp_path / "bin"))
    _configured(project, argv=[program, "--resume", "{session}", "-p", "{prompt}"], session="auto")
    assert A.session_to_resume(project, "architect")[0] is None

    result = _inspect(consumer, project, capsys)

    assert not marker.exists()
    _stopped(consumer, result, "session-unresolved", "architect session")


@pytest.mark.parametrize("consumer", ["hold", "doctor"])
def test_a_candidate_inside_the_tree_is_not_asked_its_version(
        project, tmp_path, monkeypatch, available, capsys, consumer):
    _, marker = _program(Path(project["root"]) / "bin", tmp_path)
    monkeypatch.setattr(W, "child_path", lambda: os.path.join(project["root"], "bin"))
    _configured(project, argv=["claude", "-p", "{prompt}"])

    result = _inspect(consumer, project, capsys)

    assert not marker.exists()
    if consumer == "hold":
        assert result["code"] == "binary-missing" and not result["holdable"], result
    else:
        assert "architect bin   not found" in result


@pytest.mark.parametrize("consumer", ["hold", "doctor"])
def test_an_accepted_command_outside_the_tree_is_still_asked_its_version(
        project, tmp_path, monkeypatch, available, capsys, consumer):
    program, marker = _program(tmp_path / "bin", tmp_path)
    monkeypatch.setattr(W, "child_path", lambda: str(tmp_path / "bin"))
    _configured(project, argv=[program, "-p", "{prompt}"])

    result = _inspect(consumer, project, capsys)

    assert marker.exists()
    if consumer == "hold":
        assert result["holdable"] and result["code"] == "wakeable", result
    else:
        assert f"architect bin   {program}" in result


@pytest.mark.parametrize("field", ["session", "prompt"])
def test_a_refused_value_rings_the_human_instead_of_holding_the_anomaly(
        project, tmp_path, monkeypatch, available, field):
    program, marker = _program(tmp_path / "bin", tmp_path)
    monkeypatch.setattr(W, "child_path", lambda: str(tmp_path / "bin"))
    _configured(project, argv=[program, "--resume", "{session}", "-p", "{prompt}"],
                **dict({"session": "test-session"}, **{field: "--x"}))
    sent = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: sent.append((msg, kw)))
    monkeypatch.setattr(A, "anomalies", lambda *args, **kwargs: [
        {"kind": "decision-requested", "facts": {"decision": "D-1"}, "key": "D-1"}])
    monkeypatch.setattr(A, "write_report", lambda *args, **kwargs: "report.md")
    monkeypatch.setattr(A, "mailbox", lambda *args, **kwargs: [])

    W.escalate(project["root"], project, {}, 0, SimpleNamespace(idle_minutes=6.0, dry_run=False), {})

    assert not marker.exists()
    anomaly = [(msg, kw) for msg, kw in sent if kw.get("key") == "anomaly:decision-requested:D-1"]
    assert len(anomaly) == 1 and anomaly[0][1]["audience"] == "human", sent
    assert f"its {field} `--x` would be read as a flag" in anomaly[0][0]


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("recent", [False, True])
@pytest.mark.parametrize("session", ["test-session", "--x"])
def test_only_live_anomaly_alarm_bookkeeping_prepares_and_probes_the_architect(
        project, tmp_path, monkeypatch, available, dry_run, recent, session):
    program, marker = _program(tmp_path / "bin", tmp_path)
    monkeypatch.setattr(W, "child_path", lambda: str(tmp_path / "bin"))
    _configured(project, argv=[program, "--resume", "{session}", "-p", "{prompt}"], session=session)
    handed, plan = [], A.prompt_plan
    monkeypatch.setattr(A, "prompt_plan", lambda template, text, *args, **kwargs:
                        handed.append(text) or plan(template, text, *args, **kwargs))
    sent = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: sent.append(kw["audience"]))
    monkeypatch.setattr(A, "anomalies", lambda *args, **kwargs: [
        {"kind": "decision-requested", "facts": {"decision": "D-1"}, "key": "D-1", "reports": ["request.md"]}])
    monkeypatch.setattr(A, "notice_recently_recorded", lambda *args, **kwargs: recent)
    monkeypatch.setattr(A, "write_report", lambda *args, **kwargs: "report.md")
    monkeypatch.setattr(A, "mailbox", lambda *args, **kwargs: [])
    told = set()

    W.escalate(project["root"], project, {}, 0, SimpleNamespace(idle_minutes=6.0, dry_run=dry_run), {}, told=told)

    assert handed == ([] if dry_run else [language.text(project, "prompt.wake")])
    assert marker.exists() == (not dry_run and session == "test-session")
    assert told == ({"request.md"} if not dry_run and session == "--x" else set())
    assert sent == ([] if dry_run or recent else ["human" if session == "--x" else "architect"])
