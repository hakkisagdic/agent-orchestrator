"""A reviewer's messages are read with a break where a tool call fell between two (REVIEW-SEGMENTS).

kiro-cli's text output runs a turn's messages together. Measured on kiro-cli 2.27.1 on 2026-10-05, a turn that
remarked, read a file and remarked again printed `I will read the file now.Done reading.`. A reviewer whose verdict
followed a remark that way had its verdict line read as no verdict line, and it was asked again and paid for twice.
"""
import json

from ao import acp, allowlist, cli, lib as A
from tests.test_acp_client import _turn, agent  # noqa: F401 - the fake agent is a fixture


def _chunk(text):
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}


def _record(update):
    return json.dumps({"type": "sessionUpdate", "data": {"sessionId": "s1", "update": update}})


TOOL = {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Reading seed.txt:1", "kind": "read"}
DONE = {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "kind": "read", "status": "completed"}
# One turn as kiro-cli 2.27.1 wrote it with --output-format stream-json, its records cut to what is read.
STREAM = "\n".join([
    json.dumps({"type": "runStarted", "data": {"payloadSchema": "acp", "acpProtocolVersion": 1, "engine": "v2"}}),
    json.dumps({"type": "metadata", "data": {"sessionId": "s1", "contextUsagePercentage": 0.75}}),
    _record(_chunk("I")), _record(_chunk(" will read the file now.")),
    _record(TOOL), _record(DONE),
    _record(_chunk("Done")), _record(_chunk(" reading.")),
    _record(dict(TOOL, toolCallId="t2")), _record(dict(DONE, toolCallId="t2")),
    _record(_chunk("VERDICT: APPRO")), _record(_chunk("VED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0")),
    json.dumps({"type": "runFinished", "data": {"sessionId": "s1", "status": "success", "stopReason": "end_turn",
                                                "finalText": "I will read the file now.Done reading.VERDICT: APPROVED"
                                                             "\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0",
                                                "finalTextTruncated": False}}),
])
ANSWER = "I will read the file now.\nDone reading.\nVERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0"
KIRO = ["kiro-cli", "chat", "--no-interactive", "{prompt}", "--model", "some-model", "--trust-tools="]


def test_a_turns_messages_break_where_a_tool_call_fell_between_them():
    assert acp.answer_text([_chunk("Checking."), TOOL, DONE, _chunk("VERDICT: APPROVED")]) == \
        "Checking.\nVERDICT: APPROVED"
    # a message that ends its own line takes no second break, and a tool call before any message makes none
    assert acp.answer_text([TOOL, _chunk("a\n"), dict(TOOL, toolCallId="t2"), _chunk("b")]) == "a\nb"
    assert acp.answer_text([_chunk("a"), TOOL, dict(TOOL, toolCallId="t2"), _chunk("b")]) == "a\nb"


def test_a_review_over_acp_reads_a_message_split_by_a_tool_call_as_two(agent, tmp_path, monkeypatch):
    argv, _, _ = agent

    result, _ = _turn(argv, tmp_path, monkeypatch, "tool")

    assert result["text"] == "VERDICT: \nAPPROVED"


def test_a_kiro_reviewer_answers_in_session_updates_and_its_verdict_is_read():
    argv, pinned = A.pinned_argv(KIRO, "reviewer")

    assert pinned == ["--output-format", "stream-json"]
    assert cli._answer_stream(argv, STREAM) == ANSWER
    assert A._review_verdict(ANSWER) == "APPROVED" and cli._unread_lines(ANSWER) == []


def test_a_reviewer_whose_harness_declares_no_stream_or_that_writes_text_is_read_as_written():
    assert cli._answer_stream(KIRO, STREAM) == STREAM
    assert cli._answer_stream(["claude", "-p", "{prompt}", "--output-format", "stream-json"], STREAM) == STREAM
    # a release that ignored the flag and wrote text is read as it wrote it
    assert cli._answer_stream(A.pinned_argv(KIRO, "reviewer")[0], "VERDICT: APPROVED") == "VERDICT: APPROVED"


def test_a_kiro_reviewer_that_names_text_output_is_named():
    assert allowlist.reviewer_problems(KIRO + ["--output-format", "text"]) == \
        ["it runs with --output-format text, where ao pins stream-json"]


# ---- a reviewer whose provider failed to answer this time is a passing failure, retried (KIRO-TRANSIENT) --------

import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

FAILED_TO_GENERATE = "\n".join([
    json.dumps({"type": "runStarted", "data": {"payloadSchema": "acp", "acpProtocolVersion": 1, "engine": "v2"}}),
    json.dumps({"type": "runError", "data": {"sessionId": "s1", "stage": "prompt",
                                             "message": "Internal error (code -32603): Kiro failed to generate a response"}}),
])


def test_a_kiro_run_its_provider_failed_to_answer_is_read_as_passing():
    """Measured on kiro-cli 2.27.1 on 2026-10-05: a Sol review exited 1 after six minutes with this runError, and the
    review was UNAVAILABLE and not retried, as a reviewer that cannot run is."""
    argv = A.pinned_argv(KIRO, "reviewer")[0]

    assert cli._stream_error(argv, FAILED_TO_GENERATE) == "Internal error (code -32603): Kiro failed to generate a response"
    assert cli._stream_error(argv, FAILED_TO_GENERATE.replace("failed to generate a response", "the model refused")) is None
    assert cli._stream_error(KIRO, FAILED_TO_GENERATE) is None                  # text output names no runError
    assert cli._stream_error(["claude", "-p", "x", "--output-format", "stream-json"], FAILED_TO_GENERATE) is None


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_a_reviewer_whose_provider_failed_to_answer_is_a_temporary_exit_retried(project, tmp_path):
    harness = tmp_path / "kiro-cli"
    harness.write_text(f"#!{sys.executable}\nimport sys\nprint({FAILED_TO_GENERATE!r})\nsys.exit(1)\n",
                       encoding="utf-8")
    harness.chmod(0o755)
    argv = [str(harness)] + A.pinned_argv(KIRO, "reviewer")[0][1:]

    attempt = cli._run_reviewer(project["root"], [part.replace("{prompt}", "review this") for part in argv], 30)

    assert attempt["kind"] == "temporary-exit" and attempt["retryable"] is True
    assert "failed to generate a response" in attempt["reason"]
