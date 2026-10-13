"""`ao harness probe` asks each harness that speaks ACP what it supports, and sends no prompt (ACP-PROBE).

Nothing here starts a real harness: a fake agent speaks the protocol - one JSON-RPC message a line on its
standard streams - records every method it is asked, and answers as the file AO_TEST_AGENT names tells it to.
"""
import json
import os
import sys
import time

import pytest

from ao import acp, cli, lib as A

AGENT = """
import json, os, sys, time
script = json.load(open(os.environ["AO_TEST_AGENT"], encoding="utf-8"))
asked = os.environ["AO_TEST_ASKED"]
open(os.environ["AO_TEST_PID"], "w").write(str(os.getpid()))
for line in script.get("noise", []):
    print(line, flush=True)
for raw in sys.stdin:
    message = json.loads(raw)
    with open(asked, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(message) + "\\n")
    method = message.get("method")
    if method is None:
        continue                                   # the client's answer to a request of this agent's
    if script.get("silent"):
        time.sleep(60)
    if script.get("exit"):
        sys.exit(3)
    if script.get("ask_first") and method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": "agent-1", "method": "fs/read_text_file", "params": {}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {}}), flush=True)
        reply = sys.stdin.readline()               # the client's answer, read before initialize is answered
        with open(asked, "a", encoding="utf-8") as fh:
            fh.write(reply if reply.endswith("\\n") else reply + "\\n")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": script["initialize"]}), flush=True)
    elif method == "session/list":
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                          "result": {"sessions": [{"sessionId": "s-1", "cwd": "/x", "title": "private"},
                                                  {"sessionId": "s-2", "cwd": "/x", "title": "private"}]}}),
              flush=True)
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "no"}}),
              flush=True)
"""
INITIALIZE = {
    "protocolVersion": 1,
    "agentInfo": {"name": "fake-agent", "version": "9.9.9"},
    "agentCapabilities": {"loadSession": True,
                          "sessionCapabilities": {"list": {}, "resume": {}, "close": None},
                          "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
                          "mcpCapabilities": {"http": True, "sse": False}},
    "authMethods": [{"id": "login", "name": "Log in"}],
}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    """(argv, asked, write): the fake agent's command, the methods it was asked, and how to script it."""
    script = tmp_path / "agent.json"
    asked = tmp_path / "asked.jsonl"
    monkeypatch.setenv("AO_TEST_AGENT", str(script))
    monkeypatch.setenv("AO_TEST_ASKED", str(asked))
    monkeypatch.setenv("AO_TEST_PID", str(tmp_path / "agent.pid"))

    def write(**how):
        script.write_text(json.dumps(dict({"initialize": INITIALIZE}, **how)), encoding="utf-8")

    def methods():
        return [json.loads(line).get("method") for line in asked.read_text(encoding="utf-8").splitlines()] \
            if asked.exists() else []

    write()
    return [sys.executable, "-c", AGENT], methods, write, tmp_path / "agent.pid"


def test_initialize_alone_is_asked_and_its_answer_is_kept_as_flags_and_names(agent, tmp_path):
    argv, methods, _, _ = agent

    found = acp.probe(argv, str(tmp_path), timeout=30)

    assert found == {"protocol_version": 1, "agent": {"name": "fake-agent", "version": "9.9.9"},
                     "load_session": True, "session": ["list", "resume"], "prompt": ["embeddedContext", "image"],
                     "mcp": ["http"], "auth": ["login"]}
    assert methods() == ["initialize"]


def test_sessions_are_counted_when_asked_and_nothing_of_them_is_kept(agent, tmp_path):
    argv, methods, _, _ = agent

    found = acp.probe(argv, str(tmp_path), timeout=30, sessions=True)

    assert found["sessions"] == 2 and "private" not in json.dumps(found) and "s-1" not in json.dumps(found)
    assert methods() == ["initialize", "session/list"]


def test_an_agent_that_lists_no_sessions_is_not_asked_to(agent, tmp_path):
    argv, methods, write, _ = agent
    write(initialize=dict(INITIALIZE, agentCapabilities={"loadSession": False}))

    found = acp.probe(argv, str(tmp_path), timeout=30, sessions=True)

    assert "sessions" not in found and found["session"] == [] and methods() == ["initialize"]


def test_what_the_agent_asks_back_is_answered_and_noise_is_passed_over(agent, tmp_path):
    argv, methods, write, _ = agent
    write(ask_first=True, noise=["starting up...", "[1, 2]"])

    assert acp.probe(argv, str(tmp_path), timeout=30)["agent"]["name"] == "fake-agent"

    rows = [json.loads(line) for line in (tmp_path / "asked.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {"jsonrpc": "2.0", "id": "agent-1",
            "error": {"code": acp.METHOD_NOT_FOUND, "message": "ao's probe answers no requests"}} in rows


def test_an_agent_that_does_not_answer_is_said_not_to_and_is_stopped(agent, tmp_path):
    argv, _, write, pid_file = agent
    write(silent=True)

    with pytest.raises(acp.ProbeError, match="no answer to initialize within the time allowed"):
        acp.probe(argv, str(tmp_path), timeout=2)

    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while A._pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not A._pid_alive(pid)


def test_an_agent_that_ends_before_it_answers_is_said_to(agent, tmp_path):
    argv, _, write, _ = agent
    write(exit=True)

    with pytest.raises(acp.ProbeError, match=r"the agent ended before it answered initialize"):
        acp.probe(argv, str(tmp_path), timeout=30)


def test_a_command_that_cannot_start_is_said_not_to(tmp_path):
    with pytest.raises(acp.ProbeError, match="could not start"):
        acp.probe([str(tmp_path / "no-such-agent")], str(tmp_path), timeout=5)


# ---- ao harness probe ---------------------------------------------------------------------------

def _declared(adapter_id, argv, measured="fake 1.0 answered initialize, 2026-09-28"):
    return {"id": adapter_id, "name": adapter_id, "verified": "untested", "contract": A.ADAPTER_CONTRACT,
            "send": {"argv": ["x", "{prompt}"]}, "acp": {"argv": argv, "measured": measured}}


def _adapter(monkeypatch, adapter_id, argv, measured="fake 1.0 answered initialize, 2026-09-28"):
    """An adapter the package ships, as a test stands one in: only a shipped adapter's ACP command is started."""
    shipped = dict(A.package_adapters(), **{adapter_id: _declared(adapter_id, argv, measured)})
    monkeypatch.setattr(A, "package_adapters", lambda: shipped)


def _layer(directory, adapter_id, argv):
    """An adapter a user's or a project's layer declares, a file the agents it describes can write."""
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{adapter_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(_declared(adapter_id, argv), fh)


def _probe(project, *ids, sessions=False):
    args = cli.build_parser().parse_args(["harness", "probe", *ids, "--timeout", "30"]
                                         + (["--sessions"] if sessions else []))
    return cli.cmd_harness(project, args)


def test_the_command_keeps_each_answer_in_the_machines_harness_profiles(project, agent, capsys, monkeypatch):
    argv, methods, _, _ = agent
    _adapter(monkeypatch, "fake", argv)

    assert _probe(project, "fake", sessions=True) == 0

    out = capsys.readouterr().out
    assert "fake         ACP 1  fake-agent 9.9.9  load yes  session: list, resume  mcp: http  sessions here: 2" in out
    profile = json.loads(open(cli.harness_profile_path("fake"), encoding="utf-8").read())
    assert profile["argv"] == argv and profile["binary"] == sys.executable and profile["sessions"] == 2
    assert profile["session"] == ["list", "resume"] and isinstance(profile["measured_at"], int)
    assert cli.harness_profile_path("fake").startswith(os.path.join(A.HOME, ".ao", "harness"))
    assert methods() == ["initialize", "session/list"]


def test_a_harness_that_is_not_installed_is_said_to_be_and_nothing_is_kept(project, capsys, monkeypatch):
    _adapter(monkeypatch, "absent", ["ao-test-no-such-harness", "acp"])

    assert _probe(project, "absent") == 0

    assert "absent       not installed: ao-test-no-such-harness is on no path ao searches" in capsys.readouterr().out
    assert not os.path.exists(cli.harness_profile_path("absent"))


def test_one_that_does_not_answer_makes_the_command_exit_1(project, agent, capsys, monkeypatch):
    argv, _, write, _ = agent
    write(exit=True)
    _adapter(monkeypatch, "fake", argv)

    assert _probe(project, "fake") == 1
    assert "fake         no answer: the agent ended before it answered initialize" in capsys.readouterr().out


def test_an_adapter_without_an_acp_command_or_unknown_is_refused(project, capsys):
    assert _probe(project, "no-such-adapter") == 2
    assert "no adapter" in capsys.readouterr().out
    assert _probe(project, "claude-code") == 2
    assert "declares no `acp.argv`" in capsys.readouterr().out


def test_an_acp_command_a_user_s_or_a_project_s_layer_declares_is_never_started(project, tmp_path, capsys,
                                                                              monkeypatch):
    """A layer is a file an agent writes: an adapter of its own, or another command for one ao ships, would start a
    program the agent chose when a person probes (PROBE-SHIPPED)."""
    marker = tmp_path / "started"
    chosen = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    monkeypatch.setenv("AO_USER_ADAPTERS", str(tmp_path / "user"))
    _layer(os.path.join(project["root"], ".ao", "adapters"), "planted", chosen)
    _layer(str(tmp_path / "user"), "kiro", chosen)
    with open(os.path.join(project["root"], ".ao", "adapters", "dressed.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(_declared("dressed\x1b[2J", chosen)), fh)

    assert _probe(project, "planted") == 2
    assert "not started: ao ships no adapter planted" in capsys.readouterr().out

    assert _probe(project) == 0
    out = capsys.readouterr().out
    assert "Not started: the ACP command a user's or a project's layer declares for dressed\\x1b[2J, kiro, planted" in out
    assert "\x1b" not in out
    assert "kiro         not installed: kiro-cli is on no path ao searches" in out

    assert _probe(project, "kiro") == 0
    assert "layer declares for kiro;" in capsys.readouterr().out
    assert not marker.exists()


def test_a_shipped_adapter_whose_id_names_no_plain_file_is_not_asked(project, tmp_path, capsys, monkeypatch):
    marker = tmp_path / "started"
    _adapter(monkeypatch, "odd\u202e/..", [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])

    assert _probe(project, "odd\u202e/..") == 1

    out = capsys.readouterr().out
    assert "odd\\u202e/.. not asked: 'odd\\u202e/..' is no adapter id a harness profile is named by" in out
    assert not marker.exists()


@pytest.mark.parametrize("ident", ["../escape", "..", ".hidden", "a/b", "a\\b", ""])
def test_a_harness_profile_is_named_by_a_plain_id_alone(ident):
    with pytest.raises(ValueError, match="no adapter id a harness profile is named by"):
        cli.harness_profile_path(ident)


def test_the_shipped_acp_commands_are_the_three_measured_and_validate():
    shipped = {adapter_id: adapter["acp"] for adapter_id, adapter in A.package_adapters().items() if "acp" in adapter}
    assert {adapter_id: block["argv"] for adapter_id, block in shipped.items()} == {
        "kiro": ["kiro-cli", "acp"], "opencode": ["opencode", "acp"], "qoder": ["qodercli", "--acp"]}
    assert all(A.acp_problems(A.package_adapters()[adapter_id]) == [] for adapter_id in shipped)


@pytest.mark.parametrize("block, problem", [
    ("acp", "`acp` must be an object"),
    ({"argv": [], "measured": "m"}, "`acp.argv` must be a list of strings"),
    ({"argv": ["x", "{prompt}"], "measured": "m"}, "`acp.argv` carries no placeholder"),
    ({"argv": ["x"]}, "`acp.measured` must say which release answered, and when"),
])
def test_an_acp_command_that_is_not_one_is_named(block, problem):
    assert any(found.startswith(problem) for found in A.acp_problems({"acp": block}))
