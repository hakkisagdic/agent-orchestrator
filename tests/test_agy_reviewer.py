"""An agy reviewer runs as an agent of ao's whose tools only read (AGY-REVIEWER).

Measured on agy 1.3.0 on 2026-10-06. No flag leaves agy reading: `--dangerously-skip-permissions` only widens what a
run may do, and a print run denies a write or a command only because nobody is there to ask. Run as an agent whose
tools are view_file, grep_search, find_by_name and list_dir, it read the file it was asked to and had no tool to write
a file or run a command. The person's MCP servers stay attached to every agent, Linear's writing tools among them, and
a call to one was denied only because no rule in the person's settings allowed it: ao refuses the reviewer while those
settings allow more than reading. Its answer is the `response` of the one JSON record it prints.
"""
import json
import os
import sys

import pytest

from ao import cli, lib as A

AGY = ["agy", "--print={prompt}", "--output-format=json", "--model=gemini-3.1-pro-high"]
READS = ["view_file", "grep_search", "find_by_name", "list_dir"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home of the test's own, so the person's real agy settings decide nothing here. Windows' expanduser reads
    USERPROFILE and not HOME, so both are set."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)                    # the project fixture may have made it already
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _settings(home, document):
    path = home / ".gemini" / "antigravity-cli" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")


def _front_matter(path):
    text = path.read_text(encoding="utf-8")
    _, head, body = text.split("---\n", 2)
    return {key: json.loads(value) for key, value in (line.split(": ", 1) for line in head.splitlines())}, body


def test_an_agy_reviewer_runs_as_an_agent_whose_tools_only_read(tmp_path, home):
    argv, why = cli._reviewer_agent(str(tmp_path), AGY)

    assert why is None and argv[:-1] == AGY and argv[-1].startswith("--agent=ao-reviewer-")
    name = argv[-1].split("=", 1)[1]
    config, body = _front_matter(tmp_path / ".agents" / "agents" / f"{name}.md")
    assert config["name"] == name and config["tools"] == READS
    assert config["commandExecutionPolicy"] == "off" and config["mcpServers"] == [] and config["mainAgent"] is True
    assert "write nothing" in body
    # a name the tree cannot know: every run has its own
    assert cli._reviewer_agent(str(tmp_path), AGY)[0][-1] != argv[-1]


@pytest.mark.parametrize("allowed", [["mcp(linear-mcp-server)"], ["read_file(src/)", "command(ls)"],
                                     ["write_file(*)"], [{"tool": "mcp"}]])
def test_a_person_s_settings_that_allow_more_than_reading_refuse_the_reviewer(tmp_path, home, allowed):
    _settings(home, {"permissions": {"allow": allowed}})

    argv, why = cli._reviewer_agent(str(tmp_path), AGY)

    assert argv == AGY and why and "would not only read as a reviewer" in why and json.dumps(allowed[-1]) in why
    assert not (tmp_path / ".agents").exists()


def test_a_person_s_settings_that_allow_reading_alone_keep_the_reviewer(tmp_path, home):
    _settings(home, {"permissions": {"allow": ["read_file(src/)"], "deny": ["command(sudo)"]}})

    assert cli._reviewer_agent(str(tmp_path), AGY)[1] is None


@pytest.mark.parametrize("document", ["{not json", json.dumps({"permissions": {"allow": "mcp(*)"}})])
def test_settings_ao_cannot_read_as_rules_refuse_the_reviewer(tmp_path, home, document):
    _settings(home, document)

    assert "would not only read as a reviewer" in cli._reviewer_agent(str(tmp_path), AGY)[1]


def test_the_answer_is_the_record_s_response_and_a_denied_tool_is_named():
    record = json.dumps({"conversation_id": "c", "status": "SUCCESS", "response": "VERDICT: APPROVED\nBLOCKER: 0",
                         "denied_actions": []})
    denied = json.dumps({"status": "SUCCESS", "response": "",
                         "denied_actions": [{"action": "mcp", "display_name": "CallMcpTool"}]})

    assert cli._answer_record(AGY, record + "\n") == ("VERDICT: APPROVED\nBLOCKER: 0", [])
    assert cli._answer_record(AGY, denied) == ("", ["mcp"])
    assert cli._answer_record([part for part in AGY if part != "--output-format=json"], record) is None
    assert cli._answer_record(["kiro-cli", "chat", "{prompt}"], record) is None


def test_an_agy_reviewer_is_eligible_and_a_command_that_names_an_agent_of_its_own_is_refused():
    assert A.reviewer_eligibility(A.load_adapter("antigravity")) == (True, None)
    composed = A.compose_reviewer("antigravity", model="gemini-3.1-pro-high", family="google")["argv"]

    assert A.reading_problems(composed) == []
    for named in (["--agent=firestore-rules-author"], ["--agent", "firestore-rules-author"]):
        assert any("it names --agent" in problem for problem in A.reading_problems(composed + named)), named


def _harness(tmp_path, record, notice=""):
    """A stand-in agy: it checks the agent ao named holds read tools alone, and prints `record`."""
    harness = tmp_path / "agy"
    harness.write_text(f"""#!{sys.executable}
import os, sys
name = [arg for arg in sys.argv if arg.startswith("--agent=")][0].split("=", 1)[1]
with open(os.path.join(".agents", "agents", name + ".md"), encoding="utf-8") as fh:
    text = fh.read()
assert '"view_file"' in text and "write_to_file" not in text and "run_command" not in text, text
sys.stderr.write({notice!r})
print({json.dumps(record)!r})
""", encoding="utf-8")
    harness.chmod(0o755)
    return [str(harness), "--print=review this", "--output-format=json"]


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_an_agy_review_reads_the_answer_in_the_record_its_harness_prints(project, tmp_path, home):
    verdict = "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0"
    argv = _harness(tmp_path, {"conversation_id": "c", "status": "SUCCESS", "response": verdict})

    attempt = cli._run_reviewer(project["root"], argv, 30)

    assert attempt["ok"] and attempt["out"] == verdict, attempt


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_a_review_its_harness_denied_a_tool_says_so_and_is_asked_again(project, tmp_path, home):
    argv = _harness(tmp_path, {"status": "SUCCESS", "response": "", "denied_actions": [{"action": "mcp"}]},
                    notice="jetski: no output produced - a tool required the mcp permission\n")

    attempt = cli._run_reviewer(project["root"], argv, 30)

    assert not attempt["ok"] and attempt["kind"] == "silence" and attempt["retryable"], attempt
    assert "its harness denied it mcp" in attempt["reason"]


@pytest.mark.parametrize("version, refused", [("1.3.0", False), ("1.3.1", False), ("1.1.19", True), ("1.4.0", True),
                                              ("", True)])
def test_an_agy_reviewer_runs_only_on_the_release_its_agent_was_measured_on(version, refused):
    """AGY-REVIEWER-2: agy 1.1.19 wrote files in a print run, and the agent that holds agy to reading was measured on
    1.3.0 alone; a release nobody measured may hold it to nothing."""
    attempt = cli._measured_release_problem(A.load_adapter("antigravity"), "/opt/agy", version)

    assert (attempt is not None) == refused
    if refused:
        assert attempt["kind"] == "missing-binary" and "the 1.3 release its reviewer agent was measured on" in \
            attempt["reason"]


def test_a_route_through_an_agy_nobody_measured_is_not_started(project, monkeypatch):
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: ("/opt/agy", "1.1.19"))
    started = []
    monkeypatch.setattr(cli, "_run_reviewer", lambda *args, **kwargs: started.append(args) or {"ok": True})
    route = A.compose_reviewer("antigravity", model="gemini-3.1-pro-high", family="google")

    attempt = cli._reviewer_route_invocation(project["root"], route, "review this", 30, False, route)[3]

    assert not started and attempt["kind"] == "missing-binary" and "/opt/agy is 1.1.19" in attempt["reason"]


def test_a_release_that_is_no_version_is_named():
    agent = dict(A.load_adapter("antigravity")["options"]["reviewer_agent"], release="latest")

    assert any("release" in problem for problem in A.reviewer_agent_problems({"options": {"reviewer_agent": agent}}))


def _failing(tmp_path, record, code, notice=""):
    """A stand-in agy that fails as agy 1.3 does: a record with status ERROR, a notice on stderr, a non-zero exit."""
    harness = tmp_path / "agy"
    harness.write_text(f"""#!{sys.executable}
import sys
sys.stderr.write({notice!r})
print({json.dumps(record)!r})
sys.exit({code})
""", encoding="utf-8")
    harness.chmod(0o755)
    return [str(harness), "--print=review this", "--output-format=json"]


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_a_review_agy_could_not_send_for_the_network_is_asked_again(project, tmp_path, home):
    """AGY-TRANSIENT: agy 1.3 ended a review with exit 3 on a network issue, and the review was UNAVAILABLE and not
    asked again, where the same review asked again answered."""
    said = "There was a network issue connecting to the server, please try again."
    argv = _failing(tmp_path, {"status": "ERROR", "response": "", "error": said}, 3, notice=f"error: {said}\n")

    attempt = cli._run_reviewer(project["root"], argv, 30)

    assert attempt["kind"] == "temporary-exit" and attempt["retryable"] and said in attempt["reason"], attempt


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_a_failure_agy_names_that_is_not_passing_is_not_asked_again(project, tmp_path, home):
    said = "model gemini-9 is not available to this account"
    argv = _failing(tmp_path, {"status": "ERROR", "response": "", "error": said}, 1, notice=f"error: {said}\n")

    attempt = cli._run_reviewer(project["root"], argv, 30)

    assert attempt["kind"] == "nonzero-exit" and not attempt["retryable"], attempt
