"""One ACP session: a prompt turn, what the agent streams, and the permissions it asks for (ACP-CLIENT).

A fake agent speaks the protocol on its standard streams, records every message the client sends it, and plays
the turn AO_TEST_TURN names: it streams its answer in chunks, reports a tool call and asks leave to run it, asks
the client for a file, or does not end until it is cancelled - or at all.
"""
import json
import sys
import time

import pytest

from ao import acp, lib as A

AGENT = """
import json, os, sys, time
turn = os.environ.get("AO_TEST_TURN", "answer")
asked = open(os.environ["AO_TEST_ASKED"], "a", encoding="utf-8")
open(os.environ["AO_TEST_PID"], "w").write(str(os.getpid()))

def say(message):
    print(json.dumps(message), flush=True)

def update(session, body):
    say({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": body}})

def receive():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    asked.write(json.dumps(message) + "\\n")
    asked.flush()
    return message

while True:
    message = receive()
    method, mid = message.get("method"), message.get("id")
    if method == "initialize":
        say({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
    elif method == "session/new":
        say({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "sess-1"}})
    elif method == "session/prompt":
        session = message["params"]["sessionId"]
        if turn == "files":
            say({"jsonrpc": "2.0", "id": "fs-1", "method": "fs/read_text_file", "params": {"path": "x"}})
            receive()
        if turn in ("answer", "files", "tool"):
            update(session, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "hmm"}})
            update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "VERDICT: "}})
        if turn == "tool":
            update(session, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Run git commit",
                             "kind": "execute", "status": "pending"})
            say({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
                 "params": {"sessionId": session, "toolCall": {"toolCallId": "t1", "title": "Run git commit",
                                                               "kind": "execute"},
                            "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                                        {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
            reply = receive()
            allowed = reply.get("result", {}).get("outcome", {}).get("optionId") == "yes"
            update(session, {"sessionUpdate": "tool_call_update", "toolCallId": "t1",
                             "status": "completed" if allowed else "failed"})
        if turn in ("answer", "files", "tool"):
            update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "APPROVED"}})
            say({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        elif turn in ("slow", "stuck"):
            say({"jsonrpc": "2.0", "id": "perm-2", "method": "session/request_permission",
                 "params": {"sessionId": session, "toolCall": {"toolCallId": "t2", "title": "Edit a.py", "kind": "edit"},
                            "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once"}]}})
            while True:
                seen = receive()
                if seen.get("method") == "session/cancel" and turn == "slow":
                    say({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "cancelled"}})
                    break
"""


@pytest.fixture
def agent(tmp_path, monkeypatch):
    asked = tmp_path / "asked.jsonl"
    monkeypatch.setenv("AO_TEST_ASKED", str(asked))
    monkeypatch.setenv("AO_TEST_PID", str(tmp_path / "agent.pid"))

    def sent():
        return [json.loads(line) for line in asked.read_text(encoding="utf-8").splitlines()] if asked.exists() else []

    return [sys.executable, "-c", AGENT], sent, tmp_path / "agent.pid"


def _turn(argv, tmp_path, monkeypatch, turn, timeout=30, permission=None):
    monkeypatch.setenv("AO_TEST_TURN", turn)
    with acp.Session(argv, str(tmp_path), permission=permission) as session:
        session.initialize()
        session.new_session()
        return session.prompt("review this", timeout=timeout), session.decisions


def test_a_turn_is_the_agents_message_chunks_in_order_and_its_stop_reason(agent, tmp_path, monkeypatch):
    argv, sent, _ = agent

    result, decisions = _turn(argv, tmp_path, monkeypatch, "answer")

    assert result == {"ended": "end_turn", "text": "VERDICT: APPROVED", "tool_calls": []}
    assert decisions == []
    assert [m.get("method") for m in sent()] == ["initialize", "session/new", "session/prompt"]
    prompt = sent()[2]["params"]
    assert prompt == {"sessionId": "sess-1", "prompt": [{"type": "text", "text": "review this"}]}
    assert sent()[1]["params"] == {"cwd": str(tmp_path), "mcpServers": []}


def test_a_permission_is_rejected_by_default_and_the_tool_call_is_kept(agent, tmp_path, monkeypatch):
    argv, sent, _ = agent

    result, decisions = _turn(argv, tmp_path, monkeypatch, "tool")

    assert result["tool_calls"] == [{"id": "t1", "title": "Run git commit", "kind": "execute", "status": "failed"}]
    assert decisions == [{"id": "t1", "tool": "Run git commit", "kind": "execute", "decision": "reject_once"}]
    replies = [m for m in sent() if m.get("id") == "perm-1"]
    assert replies == [{"jsonrpc": "2.0", "id": "perm-1", "result": {"outcome": {"outcome": "selected",
                                                                                  "optionId": "no"}}}]


def test_a_policy_picks_the_option_it_allows(agent, tmp_path, monkeypatch):
    argv, _, _ = agent
    seen = []

    def allow_reads_and_this(tool_call, options):
        seen.append((tool_call.get("kind"), [o["kind"] for o in options]))
        return "yes"

    result, decisions = _turn(argv, tmp_path, monkeypatch, "tool", permission=allow_reads_and_this)

    assert seen == [("execute", ["allow_once", "reject_once"])]
    assert decisions == [{"id": "t1", "tool": "Run git commit", "kind": "execute", "decision": "allow_once"}]
    assert result["tool_calls"][0]["status"] == "completed"


def test_a_policy_that_fails_or_names_no_offered_option_allows_nothing(agent, tmp_path, monkeypatch):
    argv, _, _ = agent

    def broken(tool_call, options):
        raise RuntimeError("the policy broke")

    assert _turn(argv, tmp_path, monkeypatch, "tool", permission=broken)[1][0]["decision"] == "reject_once"
    assert _turn(argv, tmp_path, monkeypatch, "tool", permission=lambda c, o: "maybe")[1][0]["decision"] == "reject_once"


def test_a_request_for_a_method_ao_does_not_provide_is_answered_so(agent, tmp_path, monkeypatch):
    argv, sent, _ = agent

    result, _ = _turn(argv, tmp_path, monkeypatch, "files")

    assert result["text"] == "VERDICT: APPROVED"
    assert {"jsonrpc": "2.0", "id": "fs-1",
            "error": {"code": acp.METHOD_NOT_FOUND, "message": "ao provides no fs/read_text_file"}} in sent()


def test_a_turn_past_its_time_is_cancelled_and_its_pending_permission_answered_cancelled(agent, tmp_path, monkeypatch):
    argv, sent, _ = agent

    result, decisions = _turn(argv, tmp_path, monkeypatch, "slow", timeout=2)

    assert result["ended"] == "cancelled"
    methods = [m.get("method") for m in sent()]
    assert "session/cancel" in methods
    # The permission asked before the cancel was decided by the policy then; nothing was allowed.
    assert decisions == [{"id": "t2", "tool": "Edit a.py", "kind": "edit", "decision": "cancelled"}]


def test_a_turn_that_does_not_end_even_when_cancelled_is_a_timeout_and_its_agent_is_stopped(
        agent, tmp_path, monkeypatch):
    argv, _, pid_file = agent
    monkeypatch.setattr(acp, "CANCEL_GRACE", 1.0)

    result, _ = _turn(argv, tmp_path, monkeypatch, "stuck", timeout=2)

    assert result["ended"] == "timeout"
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while A._pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not A._pid_alive(pid)


def test_a_prompt_before_a_session_is_refused(agent, tmp_path):
    argv, _, _ = agent
    with acp.Session(argv, str(tmp_path)) as session:
        with pytest.raises(acp.ProbeError, match="no session"):
            session.prompt("x", timeout=5)
