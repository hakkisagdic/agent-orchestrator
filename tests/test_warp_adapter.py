"""Warp's Oz CLI: what a completed turn answers, and what stays unread (WARP-ADAPTER, WARP-READABLE).

Measured on Oz v0.2026.10.07.08.29.stable_00. The command surface was read 2026-10-15, when one real
turn died at the provider's daily token cap with exit 1 after writing the two system events below. On
2026-10-16, with the cap reset, a run completed: its stream is three JSON lines ending in the agent's
own text, `oz run get <task id> --output-format json` answers the run's state, duration and usage, and
`oz run conversation get <conversation id> --output-format json` answers its messages. The assertions
below pin those read paths, and pin the refusals too: no tool-call event, no completed json-format
stream, no store, and no answer_record - warp cannot review, and nothing here pretends it can.
"""
import json
import os
from pathlib import Path

import pytest

from ao import cli, lib as A, skillkit, watchdog as W
from tests.standin import program

# The two system events a real run wrote first, ids stood in: the failed turn of 2026-10-15 ended on
# these, and a completed turn adds the agent line below - three lines, nothing after.
RUN_EVENTS = "\n".join([
    json.dumps({"type": "system", "event_type": "run_started", "run_id": "run-0001",
                "run_url": "https://oz.warp.dev/runs/run-0001"}),
    json.dumps({"type": "system", "event_type": "conversation_started", "conversation_id": "conversation-0001"}),
])
AGENT_EVENT = json.dumps({"type": "agent", "text": "Bye\n"})
FAILED_TURN = ('Error: Response stream finished unexpectedly with internal error: POST '
               '"https://endpoint.example/v1/chat/completions": 429 Too Many Requests {"message":'
               '"...","code":"daily_token_limit_exceeded"}')

# The run record `oz run get <the id the stream named> --output-format json` printed, its ids stood in:
# state, duration and usage are what ao reads from it; its words are not in it - `status_message` is
# the harness's own summary of the turn.
RUN_RECORD = {
    "run_id": "run-0001", "task_id": "run-0001",
    "state": "SUCCEEDED", "run_time": "PT33S",
    "conversation_id": "conversation-0001",
    "request_usage": {
        "total_tokens": 87739,
        "model_token_usage": [{"model_id": "model-0001", "total_tokens": 87739,
                               "usage_type": "custom_endpoint"}],
    },
    "status_message": {"message": "Answered."},
    "updated_at": "2000-01-01T00:01:33Z", "finished_at": None,
}

# The conversation record `oz run conversation get <id> --output-format json` printed: two runs of one
# conversation, four messages under one step. The first turn's messages carry the step's started_at and
# the second turn's its completed_at - the step's write time, not when the words were said.
CONVERSATION = {
    "conversation_id": "conversation-0001",
    "steps": [{
        "id": "step-0001", "description": "Two Short Words",
        "started_at": "2000-01-01T00:00:55Z", "completed_at": "2000-01-01T00:01:33Z", "steps": [],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Say hello in one word.",
                                          "message_id": "message-0001"}],
             "message_ids": ["message-0001"], "request_id": "request-0001",
             "timestamp": "2000-01-01T00:00:55Z"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hello", "message_id": "message-0002"}],
             "message_ids": ["message-0002"], "request_id": "request-0001",
             "timestamp": "2000-01-01T00:00:55Z"},
            {"role": "user", "content": [{"type": "text", "text": "Say goodbye in one word.",
                                          "message_id": "message-0003"}],
             "message_ids": ["message-0003"], "request_id": "request-0002",
             "timestamp": "2000-01-01T00:01:33Z"},
            {"role": "assistant", "content": [{"type": "text", "text": "Bye", "message_id": "message-0004"}],
             "message_ids": ["message-0004"], "request_id": "request-0002",
             "timestamp": "2000-01-01T00:01:33Z"},
        ],
    }],
}


def test_the_oz_command_line_is_warp_and_a_run_started_without_a_person_reads_as_one():
    """A harness is identified by the program it runs: the CLI is `oz`, and `warp` on PATH is a VPN."""
    warp = A.load_adapter("warp")
    assert A._program_name("/Applications/Warp.app/Contents/Resources/bin/oz") == "oz"
    assert A.command_adapter(["oz", "agent", "run", "-p", "the next slice"])["id"] == "warp"
    assert A._headless_argv(["oz", "agent", "run", "-p", "x"]), warp["detect"]["headless"]
    assert A._headless_argv(["oz", "agent", "run", "--prompt=x"]), "a flag with its value attached is the same run"
    # `oz run list` and `oz run get` read the server's run list: not turns, and `ao hold` and the reaper
    # stop what this answers True for, so a subcommand named `run` must never make one.
    assert not A._headless_argv(["oz", "run", "list"])
    assert not A._headless_argv(["oz", "run", "get", "run-0001"])


def test_ao_neither_widens_a_warp_turn_nor_claims_to():
    """The adapter names no approval flag, because the CLI has none, so a composed command stays as it was."""
    warp = A.load_adapter("warp")
    assert A.reviewer_eligibility(warp)[0] is False, warp["options"]["trust_none_why"]
    argv = list(warp["send"]["argv"])
    assert A.pinned_argv(argv, "implementer", adapter=warp) == (argv, []), "ao adds no grant it cannot name"


def test_a_completed_warp_turn_is_read_from_the_run_record_its_stream_names():
    """The answer path the adapter withheld until it was seen: state, run time and usage (WARP-READABLE)."""
    warp = A.load_adapter("warp")
    assert warp["observation_mode"] == "call-return" and warp["transcript"]["kind"] == "call-return"
    rr = warp["result_record"]
    assert rr["argv"] == ["oz", "run", "get", "{run}", "--output-format", "json"], "the id the stream named"
    assert A._path_value(RUN_RECORD, rr["session_id"]) == "conversation-0001"
    assert A._path_value(RUN_RECORD, rr["status"]) in rr["status_values"] == ["SUCCEEDED"]
    assert A._path_value(RUN_RECORD, rr["duration"]) == "PT33S"
    assert "status_message.message" in rr["note"], "the harness's summary is not mistaken for the words"
    cost = warp["telemetry"]["cost"]
    assert cost["from"] == "call-return" and cost["verified"] is True
    assert [A._path_value(RUN_RECORD, path) for path in cost["fields"]] == [87739]
    duration = warp["telemetry"]["duration"]
    assert duration["from"] == "call-return" and duration["verified"] is True
    assert A._path_value(RUN_RECORD, duration["field"]) == "PT33S"


def test_the_conversation_record_is_where_the_words_are_and_its_shape_says_so():
    """A resumed turn joins the last step; a message's stamp is the step's write time (WARP-READABLE)."""
    warp = A.load_adapter("warp")
    conv = warp["conversation"]
    assert conv["argv"] == ["oz", "run", "conversation", "get", "{session}", "--output-format", "json"]
    steps = CONVERSATION["steps"]
    assert len(steps) == 1, "two runs of one conversation left one step"
    messages = steps[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    answers = [block["text"] for m in messages if m["role"] == "assistant"
               for block in m["content"] if block["type"] == "text"]
    assert answers == ["Hello", "Bye"], "the assistant's words, read by the shape the block returns"
    assert messages[0]["timestamp"] == steps[0]["started_at"]
    assert messages[-1]["timestamp"] == steps[0]["completed_at"], "the stamp is the step's write time"
    assert "joins the last step" in conv["note"], "the join rule a reader must not lose"


def test_what_was_never_measured_stays_declared_unread():
    """No stream line is read as a record, no store is declared, and no answer_record pretends a review."""
    warp = A.load_adapter("warp")
    assert "record" not in warp["transcript"] and "sessions" not in warp
    assert A.session_store_reachable(warp) is False
    assert A.store_sessions("warp", "/tmp/any-checkout") == []
    assert "answer_record" not in warp["options"], "the review path's reader would read what ao never saw"
    shape = A.transcript_shape(warp)
    for line in (RUN_EVENTS + "\n" + AGENT_EVENT).splitlines():
        event = json.loads(line)
        assert A.record_kind(event, shape) is None, "no stream line is read as a record of a kind"
        assert A.message_words(event, shape) == "", "the last line's words are read from the record, not here"
    assert A.messages([], adapter=warp) == []
    # The review path that turns a printed record into an answer asks this adapter nothing: it declares
    # no flags and no field, and inventing one would read a system event as the agent's verdict.
    assert cli._answer_record(["oz", "agent", "run", "-p", "x", "--output-format", "json"],
                              RUN_EVENTS + "\n" + AGENT_EVENT) is None


def test_the_store_ao_mapped_belongs_to_the_app_and_not_to_a_run_ao_started():
    """The app's own conversations are a different surface from a CLI run's, and no store is declared over them."""
    warp = A.load_adapter("warp")
    assert "warp.sqlite" in warp["notes"], "the mapped store is written down, not quietly dropped"
    assert not A._block(warp, "transcript").get("path"), "and nothing reads it as this harness's transcript"


@pytest.mark.parametrize("directory", ["/Applications/Warp.app/Contents/Resources/bin",
                                      "~/Applications/Warp.app/Contents/Resources/bin"])
def test_the_shipped_bundle_is_found_and_admitted_without_an_adapter_layer(project, tmp_path, monkeypatch, directory):
    """The bundle's oz was off PATH; state and log directories found no program (WARP-ADAPTER)."""
    bundle = tmp_path / "Applications" / "Warp.app" / "Contents" / "Resources" / "bin"
    executable = program(bundle, "oz", "raise AssertionError('this stand-in must never run')\n")
    expanduser = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser", lambda path: str(bundle) if path == directory else expanduser(path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    assert A.binary_candidates("oz") == [executable]
    shipped = A.turn_adapter("warp")
    assert directory in shipped["detect"]["install_dirs"]
    assert A.runnable_binary(shipped["send"]["argv"][0]) == executable
    assert str(bundle) in W.child_path().split(os.pathsep)
    assert A.turn_refusal("architect", "warp", [executable, "agent", "run", "-p", "x"],
                          root=project["root"], resolved=executable) is None


@pytest.mark.parametrize("requested", ["warp", "auto"])
def test_init_with_only_oz_uses_the_files_only_setup(project, tmp_path, monkeypatch, capsys, requested):
    """An MCP note alone promised tools with an empty registration list (WARP-ADAPTER)."""
    executable = program(tmp_path / "bin", "oz", "raise AssertionError('oz must never run')\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert cli.main(["-C", project["root"], "init", "--agent", requested]) == 0
    output = capsys.readouterr().out
    assert "No MCP-capable agent detected" in output
    assert "registered:" not in output
    assert (Path(project["root"]) / ".ao" / "PLAYBOOK.md").is_file()
    assert not (Path(project["root"]) / ".warp").exists()
    assert not (Path(project["root"]) / ".mcp.json").exists()
    assert "warp" not in dict(skillkit.setup_adapters(project["root"]))
    assert skillkit.detect_agents(project["root"])[1] == {"generic"}
    assert A.program_path("oz") == executable


def test_the_measurement_describes_evidence_without_internal_landing_process():
    """The public note explains measured refusals, not how its slice lands (WARP-ADAPTER, WARP-READABLE)."""
    note = Path(__file__).resolve().parents[1] / "docs" / "audit" / "2026-10-15-warp-store.md"
    text = note.read_text(encoding="utf-8")
    assert "Acceptance boundary" not in text
    assert "landing kit" not in text and "CHANGELOG" not in text and "five-path guideline" not in text
    introduction = text.split("## Two harnesses", 1)[0]
    assert "measured refusals" in introduction
    assert "once the cap reset" in text, "the runs that answered are written into the note too"


def test_warp_accepts_a_pinned_skill_into_its_own_directory(project, tmp_path, capsys):
    """skills_dir planned a Warp target which detect.dirs did not admit (WARP-ADAPTER)."""
    from tests.test_content_seam import _source
    source, pin = _source(tmp_path)
    assert cli.main(["-C", project["root"], "content", "add", f"{source}@{pin}",
                     "--skills", "alpha", "--harness", "warp"]) == 0, capsys.readouterr().out
    skill = Path(project["root"]) / ".warp" / "skills" / "alpha" / "SKILL.md"
    assert skill.read_text(encoding="utf-8").endswith("Do it well.\n")
    assert A.verify_content(project["root"]) == []
    assert A.load_adapter("warp")["detect"]["dirs"] == [".warp"]
    assert A._content_refusal(project["root"], "warp", ".agents/skills/alpha/SKILL.md", set())


@pytest.mark.parametrize("staged", [False, True])
def test_warp_skills_are_harness_files_and_product_changes_remain_reviewable(project, staged):
    """Warp's missing ownership directory put its skills into product review (WARP-ADAPTER)."""
    from tests.test_content_seam import _git
    root = Path(project["root"])
    skill = root / ".warp" / "skills" / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# harness-only text\n", encoding="utf-8")
    (root / "product.txt").write_text("product change\n", encoding="utf-8")
    if staged:
        _git(root, "add", ".warp/skills/alpha/SKILL.md", "product.txt")
    assert ".warp/" in A.harness_dirs()
    dirty = A.product_dirty(str(root), project)
    assert any("product.txt" in line for line in dirty)
    assert not any(".warp" in line for line in dirty)
    diff, _ = A.review_diff(str(root), project)
    assert "product change" in diff and "harness-only text" not in diff


@pytest.mark.parametrize("directory", ["/Applications/Warp.app/Contents/Resources/bin",
                                      "~/Applications/Warp.app/Contents/Resources/bin"])
def test_adapters_reports_the_off_path_warp_bundle_as_installed(project, tmp_path, monkeypatch, capsys, directory):
    """Availability must search where ao starts programs, including bundles (WARP-ADAPTER)."""
    bundle = tmp_path / "Warp.app" / "Contents" / "Resources" / "bin"
    executable = program(bundle, "oz", "raise AssertionError('oz must never run')\n")
    expanduser = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser", lambda path: str(bundle) if path == directory else expanduser(path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.setattr(A, "_run_program", lambda *args, **kwargs: ("", 0))  # no keyflip
    monkeypatch.setattr(A, "_SURFACES", {"at": 0.0, "rows": {}})
    assert A.program_path("oz") is None
    assert A.runnable_binary("oz") == executable
    assert A.tool_availability()["warp"] == {"installed": True, "binary": "oz"}
    assert cli.main(["-C", project["root"], "adapters"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("warp "))
    assert "installed" in line
