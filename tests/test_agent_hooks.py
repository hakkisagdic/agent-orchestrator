"""A harness's lifecycle hooks tell ao what its sessions are doing, through the machine's event log (HOOK-SPOOL).

What ao knew of a session it did not start came from the harness's private store, read by a shape measured by hand.
A hook the harness runs itself on each lifecycle event - `ao agent-hook turn-end --harness claude-code` for Claude
Code's Stop - records the event instead: the session's id and directory, and a tool's name or a notification's
type where the event is one, never a prompt or a tool's input. It prints nothing and always exits 0, so it can
neither fail nor hold the turn that ran it. `ao agents` shows each session's latest state.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from ao import cli, lib as A


def _hook(root, event, payload=None, harness="claude-code", raw=None):
    """Run the hook command as a harness runs it: a process of its own, the event's JSON on standard input."""
    env = A.self_child_env(dict(os.environ, HOME=A.HOME, USERPROFILE=A.HOME))
    data = raw if raw is not None else json.dumps(payload or {})
    return subprocess.run([sys.executable, "-m", "ao", "-C", root, "agent-hook", event, "--harness", harness],
                          input=data, capture_output=True, text=True, env=env, timeout=60)


def _agent_rows():
    return [row for row in A.read_events() if str(row.get("kind", "")).startswith("agent-")]


def test_an_event_is_recorded_with_the_session_and_nothing_of_its_content(project):
    root = project["root"]
    done = _hook(root, "tool", {"session_id": "s-1", "cwd": root, "hook_event_name": "PreToolUse",
                                "tool_name": "Bash", "tool_input": {"command": "cat ~/.ssh/id_rsa"},
                                "prompt": "a secret prompt", "transcript_path": "/x/y.jsonl"})

    assert (done.returncode, done.stdout) == (0, "")
    (row,) = _agent_rows()
    assert row["kind"] == "agent-tool" and row["project"] == A.project_key(root)
    assert row["data"] == {"harness": "claude-code", "session": "s-1", "cwd": root, "tool": "Bash"}
    assert "id_rsa" not in json.dumps(row) and "secret" not in json.dumps(row) and "y.jsonl" not in json.dumps(row)


def test_the_latest_event_of_each_session_is_its_state(project, capsys):
    root = project["root"]
    for event, sid in (("session-start", "s-1"), ("prompt", "s-1"), ("session-start", "s-2"), ("prompt", "s-2"),
                       ("turn-end", "s-2"), ("notification", "s-1")):
        assert _hook(root, event, {"session_id": sid, "notification_type": "permission_prompt"}).returncode == 0

    sessions = A.agent_sessions(root)

    assert {sid: (s["state"], s["event"]) for sid, s in sessions.items()} == {
        "s-1": ("waiting", "notification"), "s-2": ("idle", "turn-end")}
    assert _agent_rows()[-1]["data"]["notice"] == "permission_prompt"
    assert cli.cmd_agents(project, None) == 0
    out = capsys.readouterr().out
    assert "waiting  claude-code  s-1" in out and "idle     claude-code  s-2" in out and "last: turn-end" in out


@pytest.mark.parametrize("event, raw", [
    ("stopped-thinking", "{}"),              # an event ao does not know
    ("turn-end", "not json"),                # input that is not the hook's JSON: the event, with nothing of it
])
def test_what_ao_cannot_use_neither_fails_the_hook_nor_prints(project, event, raw):
    done = _hook(project["root"], event, raw=raw)

    assert (done.returncode, done.stdout) == (0, "")
    rows = _agent_rows()
    assert rows == [] if event == "stopped-thinking" else [r["kind"] for r in rows] == ["agent-turn-end"]


def test_a_directory_that_is_no_ao_project_records_nothing(project, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    done = _hook(str(elsewhere), "turn-end", {"session_id": "s-9"})

    assert (done.returncode, done.stdout) == (0, "") and _agent_rows() == []


def test_a_hook_whose_input_never_ends_is_let_go(project, monkeypatch):
    """A harness that leaves the stream open does not hold its own turn on ao."""
    root = project["root"]
    env = A.self_child_env(dict(os.environ, HOME=A.HOME, USERPROFILE=A.HOME))
    started = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-m", "ao", "-C", root, "agent-hook", "prompt"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        proc.stdin.write('{"session_id": "s-3"')           # half a message, and the stream left open
        proc.stdin.flush()
        assert proc.wait(timeout=30) == 0
    finally:
        proc.stdin.close()
    assert time.monotonic() - started < 30
    assert [r["data"].get("session") for r in _agent_rows()] == [None]


def test_no_session_is_shown_before_a_hook_speaks(project, capsys):
    assert cli.cmd_agents(project, None) == 0
    assert "No harness has told ao of a session here through its hooks." in capsys.readouterr().out
