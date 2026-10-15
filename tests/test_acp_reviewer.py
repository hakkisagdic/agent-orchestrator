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
    call = {"toolCallId": kind, "title": kind, "kind": kind}
    if kind == "read":
        call["locations"] = [{"path": "a.py"}]          # what it reads, in the folder it runs in (REVIEW-PATH-HIGHS)
    say({"jsonrpc": "2.0", "id": "perm-" + kind, "method": "session/request_permission",
         "params": {"sessionId": session, "toolCall": call,
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
        if turn in ("ignored", "ignored-empty", "ignored-empty-overwritten", "ignored-unnamed"):
            call = "o" if turn == "ignored" else ""
            say({"jsonrpc": "2.0", "id": "perm-o", "method": "session/request_permission",
                 "params": {"sessionId": session, "toolCall": {"toolCallId": call, "title": "o", "kind": "other"},
                            "options": [{"optionId": "once", "name": "Allow", "kind": "allow_once"},
                                        {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
            receive()
            ran = {"sessionUpdate": "tool_call", "toolCallId": call, "title": "o", "kind": "other", "status": "completed"}
            if turn == "ignored-unnamed":
                del ran["toolCallId"]                   # the run is reported with no id, where it was refused under ""
            update(session, ran)
            if turn == "ignored-empty-overwritten":
                # A read ao lets run, under the same empty id, that fails: its report replaces the one of the run.
                say({"jsonrpc": "2.0", "id": "perm-r", "method": "session/request_permission",
                     "params": {"sessionId": session, "toolCall": {"toolCallId": call, "title": "r", "kind": "read",
                                                                   "locations": [{"path": "a.py"}]},
                                "options": [{"optionId": "once", "name": "Allow", "kind": "allow_once"},
                                            {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
                receive()
                update(session, {"sessionUpdate": "tool_call", "toolCallId": call, "title": "r", "kind": "read",
                                 "status": "failed"})
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


def test_an_answer_through_acp_is_read_as_a_spawned_reviewers_output_and_says_where_it_came_from(
        project, monkeypatch):
    attempt = _review(project, monkeypatch, "answer")

    assert attempt["ok"] and attempt["kind"] == "success"
    assert attempt["out"] == ANSWER.strip()
    assert attempt["transport"] == "acp"
    assert attempt["acp"] == {"adapter": "qoder", "agent": "fake-reviewer 1.0"}


def test_a_reviewer_is_let_read_once_and_refused_an_edit(project, monkeypatch, capsys, tmp_path):
    attempt = _review(project, monkeypatch, "ask")

    assert attempt["ok"], attempt
    assert "it asked leave 2 time(s), 1 refused" in capsys.readouterr().out
    # What it reads lies in the folder the review runs in (REVIEW-PATH-HIGHS).
    folder, here = str(tmp_path), [{"path": str(tmp_path / "a.py")}]
    assert cli._acp_reviewer_permission({"kind": "read", "locations": here},
                                        [{"optionId": "a", "kind": "allow_always"},
                                         {"optionId": "o", "kind": "allow_once"}], folder) == "o"
    assert cli._acp_reviewer_permission({"kind": "search", "locations": here}, [{"optionId": "o", "kind": "allow_once"}],
                                        folder) == "o"
    for kind in ("edit", "delete", "move", "execute", "fetch", "switch_mode", None):
        assert cli._acp_reviewer_permission({"kind": kind, "locations": here}, [{"optionId": "o", "kind": "allow_once"}],
                                            folder) is None
    # Only a standing rule is offered: nothing is picked, so the session rejects.
    assert cli._acp_reviewer_permission({"kind": "read", "locations": here}, [{"optionId": "a", "kind": "allow_always"}],
                                        folder) is None


def test_a_turn_that_ran_an_edit_without_leave_is_no_review_whatever_it_answered(project, monkeypatch):
    attempt = _review(project, monkeypatch, "wrote")

    assert not attempt["ok"] and attempt["kind"] == "wrote"
    assert "without ao's leave" in attempt["reason"] and attempt["out"] == ""


def test_a_turn_that_ran_a_call_ao_refused_is_no_review_whatever_its_kind(project, monkeypatch):
    """ACP-REVIEWER-3: a call of kind `other` ao refused, which the agent ran all the same, left the answer a review."""
    attempt = _review(project, monkeypatch, "ignored")

    assert not attempt["ok"] and attempt["kind"] == "wrote" and "(other)" in attempt["reason"]


def test_a_refused_call_with_an_empty_id_that_ran_is_no_review_either(project, monkeypatch):
    """ACP-REVIEWER-4: the refused calls were kept only where their id was not empty, so one with none ran unseen."""
    attempt = _review(project, monkeypatch, "ignored-empty")

    assert not attempt["ok"] and attempt["kind"] == "wrote" and "(other)" in attempt["reason"]


def test_a_refused_call_without_an_id_is_taken_to_have_run_whatever_is_reported_after_it(project, monkeypatch):
    """ACP-REVIEWER-5: a refused call without an id was matched by that id, so a later call's report under the same
    empty id replaced the one that said it ran, and a run reported with no id at all did not match the empty one."""
    overwritten = _review(project, monkeypatch, "ignored-empty-overwritten")
    unnamed = _review(project, monkeypatch, "ignored-unnamed")

    for attempt in (overwritten, unnamed):
        assert not attempt["ok"] and attempt["kind"] == "wrote" and "(other)" in attempt["reason"], attempt


def test_a_turn_past_its_time_is_cancelled_and_is_a_timeout(project, monkeypatch):
    attempt = _review(project, monkeypatch, "slow", timeout=3)

    assert not attempt["ok"] and attempt["kind"] == "timeout" and attempt["retryable"]


def test_a_turn_that_did_not_end_its_answer_or_said_nothing_is_no_review(project, monkeypatch):
    limited = _review(project, monkeypatch, "limit")
    silent = _review(project, monkeypatch, "silent")

    assert limited["kind"] == "acp-error" and "max_tokens" in limited["reason"]
    assert silent["kind"] == "silence" and not silent["ok"] and silent["retryable"]  # REVIEW-EMPTY-TURN


def test_the_transport_decides_whether_a_route_answers_through_acp(project, monkeypatch, capsys):
    # The transport a review passes is the one it read with its configuration (REVIEW-PATH-HIGHS), and kiro's is the
    # ACP command measured to hold a reviewer to reads: qoder's is spawned now (tests/test_review_path_highs.py).
    from ao import lib as A
    root = project["root"]
    route = A.compose_reviewer("kiro")
    through, spawned = [], []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (f"/agents/{name}", "1.0"))
    monkeypatch.setattr(cli, "_run_acp_reviewer", lambda root, argv, adapter, prompt, timeout, label, **kw:
                        through.append((argv, adapter)) or {"ok": True, "out": ANSWER, "transport": "acp"})
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kw:
                        spawned.append(argv) or {"ok": True, "out": ANSWER})

    assert cli._acp_reviewer_command(root, route, False) == (None, None)       # spawn, the default: nothing said
    cli._reviewer_route_invocation(root, route, "review this", 60, False, route)
    assert len(spawned) == 1 and through == []

    capsys.readouterr()
    _, _, _, attempt = cli._reviewer_route_invocation(root, route, "review this", 60, False, route, transport="acp")
    assert attempt["ok"] and through == [(["/agents/kiro-cli", "acp"], "kiro")] and len(spawned) == 1

    claude = {"id": "claude-reviewer", "adapter": "claude-code",
              "argv": ["claude", "-p", "{prompt}", "--allowedTools", "Read,Grep,Glob", "--strict-mcp-config"]}
    cli._reviewer_route_invocation(root, claude, "review this", 60, False, claude, transport="acp")
    assert len(spawned) == 2
    assert "claude-reviewer is spawned, not run through ACP: adapter claude-code declares no ACP command" \
        in capsys.readouterr().out


def test_a_route_that_cannot_answer_through_acp_says_why(project):
    root = project["root"]

    named = {"adapter": "qoder", "argv": ["qodercli", "-p", "{prompt}"], "model": "some-model"}
    assert "it names a model (some-model)" in cli._acp_reviewer_command(root, named, False, "acp")[1]
    # OPENCODE-REVIEWER-2: opencode reviews as an agent of ao's, and `opencode acp` takes no --agent.
    agent = {"adapter": "opencode", "argv": ["opencode", "run", "{prompt}", "--pure"]}
    assert "nobody measured that its ACP command runs as one" in cli._acp_reviewer_command(root, agent, False,
                                                                                         "acp")[1]
    tool = {"kind": "tool", "adapter": "pr-agent", "argv": ["pr-agent"]}
    assert "tool reviewer" in cli._acp_reviewer_command(root, tool, False, "acp")[1]
    stranger = {"adapter": "no-such-harness", "argv": ["x"]}
    assert "no-such-harness is no adapter ao ships" in cli._acp_reviewer_command(root, stranger, False, "acp")[1]


def test_a_review_through_acp_records_its_transport(project):
    evidence = {}
    cli._acp_review_evidence(evidence, {"transport": "acp", "acp": {"adapter": "qoder", "agent": "fake 1.0"}})
    spawned = {}
    cli._acp_review_evidence(spawned, {"ok": True, "out": ANSWER})

    assert evidence == {"transport": "acp", "acp": {"adapter": "qoder", "agent": "fake 1.0"}}
    assert spawned == {} and cli._acp_review_lines(spawned) == []
    assert cli._acp_review_lines(evidence) == ["- transport: `acp`  adapter: `qoder`  agent: `fake 1.0`"]
    assert S.default("review.transport") == "spawn"


# ---- a later report under the same id takes back neither a kind nor a completion (ACP-REVIEWER-5) ---------------

import pytest  # noqa: E402

# A scripted agent: AO_TEST_STEPS lists its steps, each a permission it asks for and waits on ({"ask": toolCall}) or
# a session update it sends ({"report": update}); then it answers APPROVED and ends its turn.
STEPS_AGENT = r"""
import json, os, sys
steps = json.loads(os.environ["AO_TEST_STEPS"])

def say(message):
    print(json.dumps(message), flush=True)

def receive():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)

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
        for n, step in enumerate(steps):
            if "ask" in step:
                say({"jsonrpc": "2.0", "id": "perm-%d" % n, "method": "session/request_permission",
                     "params": {"sessionId": session, "toolCall": step["ask"],
                                "options": [{"optionId": "once", "name": "Allow", "kind": "allow_once"},
                                            {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
                receive()
            else:
                say({"jsonrpc": "2.0", "method": "session/update",
                     "params": {"sessionId": session, "update": step["report"]}})
        say({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n"}}}})
        say({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
"""
ABSENT = object()


def _call(kind, call_id, status=None, variant="tool_call"):
    body = {"sessionUpdate": variant, "title": kind or "t"}
    if kind is not None:
        body["kind"] = kind
    if call_id is not ABSENT:
        body["toolCallId"] = call_id
    if status:
        body["status"] = status
    return body


def _ask(kind, call_id):
    body = {"title": kind, "kind": kind}
    if kind == "read":
        body["locations"] = [{"path": "a.py"}]      # a read ao lets run, in the folder it runs in (REVIEW-PATH-HIGHS)
    if call_id is not ABSENT:
        body["toolCallId"] = call_id
    return {"ask": body}


def _rep(*args, **kw):
    return {"report": _call(*args, **kw)}


RAN_ALL_THE_SAME = {
    "refused under an empty id, run under it": [_ask("other", ""), _rep("other", "", "completed")],
    "then a read let run under that id fails": [
        _ask("other", ""), _rep("other", "", "completed"), _ask("read", ""), _rep("read", "", "failed")],
    "refused under an empty id, run with none": [_ask("other", ""), _rep("other", ABSENT, "completed")],
    "refused with no id, run under an empty one": [_ask("other", ABSENT), _rep("other", "", "completed")],
    "refused and run with no id": [_ask("other", ABSENT), _rep("other", ABSENT, "completed")],
    "refused and run with no id, then a read fails": [
        _ask("other", ABSENT), _rep("other", ABSENT, "completed"), _ask("read", ABSENT), _rep("read", ABSENT, "failed")],
    "refused and run under an id, then reported failed": [
        _ask("other", "x"), _rep("other", "x", "completed"), _rep(None, "x", "failed", variant="tool_call_update")],
    "an edit run unasked, then reported a failed read": [
        _rep("edit", "w", "completed"), _rep("read", "w", "failed", variant="tool_call_update")],
    "an edit run unasked under an empty id, then a read fails": [
        _rep("edit", "", "completed"), _ask("read", ""), _rep("read", "", "failed")],
    "an edit pending, a read completed under its id, then the edit's completion": [
        _rep("edit", "z", "pending"), _ask("read", "z"), _rep("read", "z", "completed"),
        _rep(None, "z", "completed", variant="tool_call_update")],
}


@pytest.mark.parametrize("name", list(RAN_ALL_THE_SAME))
def test_a_review_whose_agent_ran_what_it_should_not_is_not_accepted(project, monkeypatch, name):
    """A call's record kept only its last report: a later report under the same id - its own, or another call's that
    shares the id - took back the kind it was run as, or that it completed, and the review was accepted."""
    monkeypatch.setenv("AO_TEST_STEPS", json.dumps(RAN_ALL_THE_SAME[name]))

    attempt = cli._run_acp_reviewer(project["root"], [sys.executable, "-c", STEPS_AGENT], "qoder", "review this", 30,
                                    "qoder-reviewer")

    assert not attempt["ok"] and attempt["kind"] == "wrote", (name, attempt)


def test_an_adapter_that_may_not_review_is_not_reached_over_acp_either(project, monkeypatch):
    from ao import lib as A
    root = project["root"]
    writer = {"id": "writer", "acp": {"argv": ["writer", "acp"], "measured": "writer 1.0 answered initialize, 2026-10-07"},
              "send": {"argv": ["writer", "{prompt}"]}, "options": {"trust_none": None, "trust_none_why": "it writes"}}
    shipped = dict(A.package_adapters(), writer=writer)
    monkeypatch.setattr(A, "package_adapters", lambda: shipped)

    assert "adapter writer may not review" in cli._acp_reviewer_command(root, {"adapter": "writer"}, False, "acp")[1]


def test_a_kiro_reviewer_is_reached_over_acp_as_its_agent_was_measured_to_hold_there(project):
    """KIRO-READONLY-2 measured kiro-cli acp running as ao's agent; its adapter says so, and opencode's does not."""
    from ao import lib as A
    root = project["root"]

    assert cli._acp_reviewer_command(root, {"adapter": "kiro", "argv": ["kiro-cli", "chat", "{prompt}"]}, False,
                                    "acp")[0] \
        == {"argv": ["kiro-cli", "acp"], "adapter": "kiro"}
    assert A.load_adapter("kiro")["options"]["reviewer_agent"]["acp"] is True
    assert "acp" not in A.load_adapter("opencode")["options"]["reviewer_agent"]
