"""A reviewer answers through ACP where the project sets review.transport to acp (ACP-REVIEWER).

A fake agent speaks the protocol and plays the turn AO_TEST_TURN names: it answers a review, asks leave to
read or to edit first, runs an edit without asking, stops at a token limit, says nothing, or does not end
until it is cancelled. The session lets a reviewer read and nothing else, an answer is read as a spawned
reviewer's output is, and a route that cannot answer through ACP is spawned as before, saying why.
"""
import json
import sys

from ao import cli, settings as S

ANSWER = "VERDICT: APPROVED\nBLOCKER: 0 HIGH: 0 MEDIUM: 0 LOW: 0\n"

AGENT = """
import json, os, sys
turn = os.environ.get("AO_TEST_TURN", "answer")
answer = os.environ.get("AO_TEST_ANSWER", "")

def say(message):
    print(json.dumps(message), flush=True)

def update(session, body):
    say({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": body}})

def receive():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)

def ask(session, kind):
    say({"jsonrpc": "2.0", "id": "perm-" + kind, "method": "session/request_permission",
         "params": {"sessionId": session, "toolCall": {"toolCallId": kind, "title": kind, "kind": kind},
                    "options": [{"optionId": "always", "name": "Always", "kind": "allow_always"},
                                {"optionId": "once", "name": "Allow", "kind": "allow_once"},
                                {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
    reply = receive().get("result", {}).get("outcome", {})
    update(session, {"sessionUpdate": "tool_call", "toolCallId": kind, "title": kind, "kind": kind,
                     "status": "completed" if reply.get("optionId") == "once" else "failed"})

while True:
    message = receive()
    method, mid = message.get("method"), message.get("id")
    if method == "initialize":
        say({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {},
                                                     "agentInfo": {"name": "fake-reviewer", "version": "1.0"}}})
    elif method == "session/new":
        say({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "sess-1"}})
    elif method == "session/prompt":
        session = message["params"]["sessionId"]
        if turn == "ask":
            ask(session, "read")
            ask(session, "edit")
        if turn == "ignored":
            say({"jsonrpc": "2.0", "id": "perm-o", "method": "session/request_permission",
                 "params": {"sessionId": session, "toolCall": {"toolCallId": "o", "title": "o", "kind": "other"},
                            "options": [{"optionId": "once", "name": "Allow", "kind": "allow_once"},
                                        {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
            receive()
            update(session, {"sessionUpdate": "tool_call", "toolCallId": "o", "title": "o", "kind": "other",
                             "status": "completed"})
        if turn == "wrote":
            update(session, {"sessionUpdate": "tool_call", "toolCallId": "w", "title": "Edit a.py", "kind": "edit",
                             "status": "completed"})
        if turn == "slow":
            while receive().get("method") != "session/cancel":
                pass
            say({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "cancelled"}})
            continue
        if turn != "silent":
            update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": answer}})
        stop = "max_tokens" if turn == "limit" else "end_turn"
        say({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": stop}})
"""


def _review(project, monkeypatch, turn, timeout=30, answer=ANSWER):
    monkeypatch.setenv("AO_TEST_TURN", turn)
    monkeypatch.setenv("AO_TEST_ANSWER", answer)
    return cli._run_acp_reviewer(project["root"], [sys.executable, "-c", AGENT], "qoder", "review this", timeout,
                                 "qoder-reviewer")


def _transport(project, value):
    path = f"{project['root']}/.ao/config.json"
    with open(path, encoding="utf-8") as fh:
        config = json.load(fh)
    config["review"] = dict(config.get("review") or {}, transport=value)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)


def test_an_answer_through_acp_is_read_as_a_spawned_reviewers_output_and_says_where_it_came_from(
        project, monkeypatch):
    attempt = _review(project, monkeypatch, "answer")

    assert attempt["ok"] and attempt["kind"] == "success"
    assert attempt["out"] == ANSWER.strip()
    assert attempt["transport"] == "acp"
    assert attempt["acp"] == {"adapter": "qoder", "agent": "fake-reviewer 1.0"}


def test_a_reviewer_is_let_read_once_and_refused_an_edit(project, monkeypatch, capsys):
    attempt = _review(project, monkeypatch, "ask")

    assert attempt["ok"], attempt
    assert "it asked leave 2 time(s), 1 refused" in capsys.readouterr().out
    assert cli._acp_reviewer_permission({"kind": "read"}, [{"optionId": "a", "kind": "allow_always"},
                                                           {"optionId": "o", "kind": "allow_once"}]) == "o"
    assert cli._acp_reviewer_permission({"kind": "search"}, [{"optionId": "o", "kind": "allow_once"}]) == "o"
    for kind in ("edit", "delete", "move", "execute", "fetch", "switch_mode", None):
        assert cli._acp_reviewer_permission({"kind": kind}, [{"optionId": "o", "kind": "allow_once"}]) is None
    # Only a standing rule is offered: nothing is picked, so the session rejects.
    assert cli._acp_reviewer_permission({"kind": "read"}, [{"optionId": "a", "kind": "allow_always"}]) is None


def test_a_turn_that_ran_an_edit_without_leave_is_no_review_whatever_it_answered(project, monkeypatch):
    attempt = _review(project, monkeypatch, "wrote")

    assert not attempt["ok"] and attempt["kind"] == "wrote"
    assert "without ao's leave" in attempt["reason"] and attempt["out"] == ""


def test_a_turn_that_ran_a_call_ao_refused_is_no_review_whatever_its_kind(project, monkeypatch):
    """ACP-REVIEWER-3: a call of kind `other` ao refused, which the agent ran all the same, left the answer a review."""
    attempt = _review(project, monkeypatch, "ignored")

    assert not attempt["ok"] and attempt["kind"] == "wrote" and "(other)" in attempt["reason"]


def test_a_turn_past_its_time_is_cancelled_and_is_a_timeout(project, monkeypatch):
    attempt = _review(project, monkeypatch, "slow", timeout=3)

    assert not attempt["ok"] and attempt["kind"] == "timeout" and attempt["retryable"]


def test_a_turn_that_did_not_end_its_answer_or_said_nothing_is_no_review(project, monkeypatch):
    limited = _review(project, monkeypatch, "limit")
    silent = _review(project, monkeypatch, "silent")

    assert limited["kind"] == "acp-error" and "max_tokens" in limited["reason"]
    assert silent["kind"] == "silence" and not silent["ok"]


def test_the_transport_decides_whether_a_route_answers_through_acp(project, monkeypatch, capsys):
    root = project["root"]
    route = {"id": "qoder-reviewer", "adapter": "qoder", "argv": ["qodercli", "-p", "{prompt}"]}
    through, spawned = [], []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (f"/agents/{name}", "1.0"))
    monkeypatch.setattr(cli, "_run_acp_reviewer", lambda root, argv, adapter, prompt, timeout, label, **kw:
                        through.append((argv, adapter)) or {"ok": True, "out": ANSWER, "transport": "acp"})
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kw:
                        spawned.append(argv) or {"ok": True, "out": ANSWER})

    assert cli._acp_reviewer_command(root, route, False) == (None, None)       # spawn, the default: nothing said
    cli._reviewer_route_invocation(root, route, "review this", 60, False, route)
    assert len(spawned) == 1 and through == []

    _transport(project, "acp")
    capsys.readouterr()
    _, _, _, attempt = cli._reviewer_route_invocation(root, route, "review this", 60, False, route)
    assert attempt["ok"] and through == [(["/agents/qodercli", "--acp"], "qoder")] and len(spawned) == 1

    claude = {"id": "claude-reviewer", "adapter": "claude-code", "argv": ["claude", "-p", "{prompt}"]}
    cli._reviewer_route_invocation(root, claude, "review this", 60, False, claude)
    assert len(spawned) == 2
    assert "claude-reviewer is spawned, not run through ACP: adapter claude-code declares no ACP command" \
        in capsys.readouterr().out


def test_a_route_that_cannot_answer_through_acp_says_why(project):
    root = project["root"]
    _transport(project, "acp")

    named = {"adapter": "qoder", "argv": ["qodercli", "-p", "{prompt}"], "model": "some-model"}
    assert "it names a model (some-model)" in cli._acp_reviewer_command(root, named, False)[1]
    writer = {"adapter": "opencode", "argv": ["opencode", "run", "{prompt}"]}
    assert "adapter opencode may not review" in cli._acp_reviewer_command(root, writer, False)[1]
    tool = {"kind": "tool", "adapter": "pr-agent", "argv": ["pr-agent"]}
    assert "tool reviewer" in cli._acp_reviewer_command(root, tool, False)[1]
    stranger = {"adapter": "no-such-harness", "argv": ["x"]}
    assert "no-such-harness is no adapter ao ships" in cli._acp_reviewer_command(root, stranger, False)[1]


def test_a_review_through_acp_records_its_transport(project):
    evidence = {}
    cli._acp_review_evidence(evidence, {"transport": "acp", "acp": {"adapter": "qoder", "agent": "fake 1.0"}})
    spawned = {}
    cli._acp_review_evidence(spawned, {"ok": True, "out": ANSWER})

    assert evidence == {"transport": "acp", "acp": {"adapter": "qoder", "agent": "fake 1.0"}}
    assert spawned == {} and cli._acp_review_lines(spawned) == []
    assert cli._acp_review_lines(evidence) == ["- transport: `acp`  adapter: `qoder`  agent: `fake 1.0`"]
    assert S.default("review.transport") == "spawn"
