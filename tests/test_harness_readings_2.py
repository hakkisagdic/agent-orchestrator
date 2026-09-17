"""The second harness's readings, each measured on its session stores before it changed.

- A Stop hook runs after the response that ends a turn and writes nothing until its
  summary, which does not follow every such response and never went on with a turn: the
  turn has ended at the response, and the summary is bookkeeping.
- A user record holding only tool results is not a person speaking, and a tool call is not
  the agent speaking: a message's words are the blocks `transcript.messages` declares.
- The account `ao cost` sets beside ao's share is the implementer's own, or it says there
  is none, never the first shipped adapter's.
- A file the implementer only read is not its write: foreign edits ask the declared tools.
- A refusal ends its turn; max_tokens does not, since the harness asks again in that turn.
- A response the store writes again, later in a transcript or in another transcript one
  reading adds up, is counted once in that reading.
- A turn started without a person is known by an argument its own harness declares
  (`detect.headless`), read only for the harness a process runs as and only from the package.
"""
import ast
import json
import os
import re
import time
from collections import Counter
from types import SimpleNamespace

from ao import cli, lib as A
from tests.test_harness_guard import _docstrings
from tests.test_processes import _architect_process_table
from tests.test_second_harness_cost import (HARNESS, R1, R2, R3, R4, _append, _bookkeeping, _call, _prompt,
                                            _response, _result, _spent, _stamp, _text, _world)
from tests.test_transcript_shape import FIXTURE, ROOT, _world as _fixture_world


def _lines(records):
    return "".join(json.dumps(record) + "\n" for record in records)


# ── a Stop hook runs after the turn has ended ──

def test_a_stop_hook_leaves_the_turn_ended_while_it_runs_and_after_its_summary(project, monkeypatch, tmp_path):
    now = time.time()
    cfg, transcript = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser the board names, with its tests"),
        *_response(now - 50, "msg-1", "end_turn", R1, _text("the parser is written and its tests pass"))])

    # While the hook runs the store holds the response and, at most, bookkeeping of its own.
    assert A.turn_ended(cfg) is True
    _append(transcript, _bookkeeping(now - 49, "queue-operation", operation="enqueue"),
            _bookkeeping(now - 49, "ai-title", aiTitle="the parser"))
    assert A.turn_ended(cfg) is True
    _append(transcript, _bookkeeping(now - 47, "system", subtype="stop_hook_summary", hookCount=2, hookInfos=[{}, {}],
                                     hookErrors=[], preventedContinuation=False, stopReason="", hasOutput=False))
    assert A.turn_ended(cfg) is True
    assert [turn["usage"] for turn in A.turn_costs(cfg)["turns"]] == [_spent(R1)]
    _append(transcript, _prompt(now, "now add the empty-input guard"))
    assert A.turn_ended(cfg) is False


# ── a message's words are the blocks its adapter declares ──

def _conversation(root, now):
    """A prompt, a reply that writes a file, the tool's result, the reply that ends the turn, a prompt in blocks."""
    return [
        _prompt(now - 90, "take the next slice on the board and write the tests that prove it"),
        *_response(now - 80, "msg-1", "tool_use", R1, _text("reading the board before writing anything at all"),
                   _call("call-1", "Write", file_path=os.path.join(root, "src", "parser.py"),
                         content="def parse(text):\n    return text.split()  # long enough to read as words")),
        _result(now - 75, "call-1", "The file src/parser.py has been created successfully with its content"),
        *_response(now - 70, "msg-2", "end_turn", R2, _text("the parser is written and its tests pass here too")),
        {"type": "user", "timestamp": _stamp(now - 10), "message": {"role": "user", "content": [
            {"type": "text", "text": "and now the empty-input guard, with a test for it"}]}},
    ]


def test_a_tool_result_is_not_the_person_speaking_and_a_tool_call_is_not_the_agent(project, monkeypatch, tmp_path):
    cfg, transcript = _world(project, monkeypatch, tmp_path, records=_conversation(project["root"], time.time()))
    recs = A.read_tail(str(transcript))
    shipped = A.implementer_adapter(cfg)

    shown = [(role, text.split()[0]) for _, role, text in A.messages(recs, 8, shipped)]

    assert shown == [("user", "take"), ("assistant", "reading"), ("assistant", "the"), ("user", "and")]
    # The words are what the adapter declares: without its blocks the same records show the call and the result.
    whole = dict(shipped, transcript=dict(shipped["transcript"], messages={"prompt": ["user"], "reply": ["assistant"]}))
    assert [(role, text.split()[0]) for _, role, text in A.messages(recs, 8, whole)] == [
        ("user", "take"), ("assistant", "reading"), ("assistant", "def"), ("user", "The"), ("assistant", "the"),
        ("user", "and")]


# ── the account is the implementer's own ──

def test_the_account_beside_the_share_is_the_implementers_own_or_it_says_there_is_none(project, monkeypatch, tmp_path,
                                                                                       capsys):
    asked = []
    monkeypatch.setattr(A, "account_usage", lambda timeout=20, adapter_id=None: asked.append(adapter_id) or {
        "used": 5000.0, "limit": 10000.0, "reset_at": None})
    cfg, _ = _world(project, monkeypatch, tmp_path)

    assert cli.cmd_cost(cfg, SimpleNamespace(since=None)) == 0

    out = capsys.readouterr().out
    assert f"the account: {HARNESS} declares none ao can read" in out and "5,000" not in out and asked == []

    monkeypatch.setattr(A, "turn_costs", lambda cfg, since=None: {
        "turns": 10, "total": 1250.0, "unit": "credits", "ao_commands": Counter(),
        "by_class": {"product": {"turns": 10, "usage": 1250.0, "wasted": 0, "wasted_usage": 0}}})
    assert cli.cmd_cost(project, SimpleNamespace(since=None)) == 0
    out = capsys.readouterr().out
    assert "the account: 5,000 of 10,000 used" in out and "(25% of the account's used)" in out
    assert asked == [project["implementer"]["adapter"]]


def test_an_adapter_is_asked_for_its_own_account_lookup_not_the_first_one_shipped(monkeypatch):
    lookup = {"driver": "usage-limits", "login": ["first", "login"]}
    monkeypatch.setattr(A, "package_adapters", lambda: {
        "first": {"billing": {"api": lookup}}, "second": {"billing": {"api": dict(lookup, login=["second", "login"])}},
        "third": {"billing": {"fallback": {"reading": "sum"}}}})

    assert A.usage_api("first")["login"] == ["first", "login"] and A.usage_api("second")["login"] == ["second", "login"]
    assert A.usage_api("third") == {} and A.usage_api("absent") == {} and A.account_usage(adapter_id="third") is None
    # There is no first one to fall back on: a lookup that names no adapter reads none (ACCOUNT-READERS).
    assert A.usage_api(None) == {} and A.account_usage() is None


# ── a file the implementer only read is not its write ──

def test_a_file_the_implementer_only_read_is_not_its_write(project, monkeypatch, tmp_path):
    root, now = project["root"], time.time()
    mine, theirs = (os.path.join(root, "src", name) for name in ("mine.py", "theirs.py"))
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 90, "fix the parser and leave the reader as it is"),
        *_response(now - 80, "msg-1", "tool_use", R1, _call("call-1", "Read", file_path=theirs)),
        _result(now - 78, "call-1", "x = 1"),
        *_response(now - 70, "msg-2", "tool_use", R2, _call("call-2", "Edit", file_path=mine, old_string="x",
                                                            new_string="y")),
        _result(now - 68, "call-2", "The file has been updated"),
        *_response(now - 60, "msg-3", "end_turn", R3, _text("the parser is fixed"))])
    os.makedirs(os.path.join(root, "src"))
    for path in (mine, theirs):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")

    assert A.implementer_recent_writes(cfg) == {os.path.realpath(mine)}
    assert A.foreign_edits(root, cfg) == ["src/theirs.py"]


def test_which_tools_write_is_the_adapters_to_declare(project, monkeypatch, tmp_path):
    root = project["root"]
    mine, readme = (os.path.realpath(os.path.join(root, *name)) for name in (("src", "mine.py"), ("README.md",)))

    cfg, _ = _fixture_world(project, monkeypatch, tmp_path)
    assert A.implementer_recent_writes(cfg) == {mine}

    reader_writes = dict(FIXTURE, id="write-fixture", transcript=dict(FIXTURE["transcript"], tool_call=dict(
        FIXTURE["transcript"]["tool_call"], write_tools=["file.put", "file.get"])))
    cfg, _ = _fixture_world(project, monkeypatch, tmp_path, adapter=reader_writes)
    assert A.implementer_recent_writes(cfg) == {mine, readme}


# ── a refusal ends its turn, max_tokens does not ──

def test_a_refusal_ends_its_turn_and_max_tokens_does_not(project, monkeypatch, tmp_path):
    now = time.time()
    cfg, transcript = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 300, "read the attachment and do what it says"),
        *_response(now - 290, "msg-1", "refusal", R1, _text("that request is one this model declines"))])

    assert A.turn_ended(cfg) is True
    _append(transcript, _prompt(now - 200, "then summarise the parser module instead"))
    assert A.turn_ended(cfg) is False
    _append(transcript, *_response(now - 190, "msg-2", "max_tokens", R2, {"type": "thinking", "thinking": "a plan"}))
    assert A.turn_ended(cfg) is False
    # The harness asks again in the same turn, after a note of its own.
    _append(transcript, {"type": "user", "isMeta": True, "timestamp": _stamp(now - 185),
                         "message": {"role": "user", "content": "a note the harness writes before it asks again"}},
            *_response(now - 180, "msg-3", "tool_use", R3, _call("call-3", "Read", file_path="src/parser.py")),
            _result(now - 175, "call-3", "def parse(text): ..."))
    assert A.turn_ended(cfg) is False
    _append(transcript, *_response(now - 170, "msg-4", "end_turn", R4, _text("the parser splits on whitespace")))
    assert A.turn_ended(cfg) is True
    assert [turn["usage"] for turn in A.turn_costs(cfg)["turns"]] == [_spent(R1), _spent(R2, R3, R4)]


# ── a response written again is counted once in a reading ──

def _sessions(now):
    """Three turns; the store writes the first response again, as it was, after the second turn."""
    first = _response(now - 500, "msg-1", "end_turn", R1, _text("the parser is written"))
    return [_prompt(now - 510, "write the parser"), *first,
            _prompt(now - 400, "now its tests"), *_response(now - 390, "msg-2", "end_turn", R2, _text("tests written")),
            *[dict(record) for record in first],
            _prompt(now - 300, "and the guard"), *_response(now - 290, "msg-3", "end_turn", R3, _text("the guard"))]


def test_a_response_the_store_writes_again_is_counted_once_in_a_transcript(project, monkeypatch, tmp_path):
    cfg, transcript = _world(project, monkeypatch, tmp_path, records=_sessions(time.time()))

    costs = A.turn_costs(cfg)
    panel = A.telemetry(A.read_tail(str(transcript)), A.implementer_adapter(cfg))

    assert [turn["usage"] for turn in costs["turns"]] == [_spent(R1), _spent(R2), _spent(R3)]
    assert costs["total"] == panel["total"] == _spent(R1, R2, R3) and panel["turns"] == 3


def test_a_resumed_sessions_copy_of_another_is_counted_once_across_the_transcripts_one_reading_adds_up(monkeypatch,
                                                                                                       tmp_path):
    records = _sessions(time.time())
    store = tmp_path / "sessions"
    store.mkdir()
    (store / "first.jsonl").write_text(_lines(records[:4]), encoding="utf-8")
    (store / "resumed.jsonl").write_text(_lines(records[:4] + records[5:]), encoding="utf-8")
    shipped = A.package_adapters()[HARNESS]
    declared = dict(shipped, billing={"fallback": {"transcripts": str(store / "*.jsonl"), "reading": "per-response"}})
    monkeypatch.setattr(A, "package_adapters", lambda: {HARNESS: declared})

    estimate = A.credit_usage(adapter_id=HARNESS)

    per_transcript = sum(A.telemetry(A.read_tail(str(path)), declared)["total"] for path in store.iterdir())
    assert per_transcript == _spent(R1, R2, R1, R2, R3)
    assert sum(estimate["days"].values()) == _spent(R1, R2, R3)


# ── an unattended turn is known by its own harness's arguments ──

def test_an_unattended_turn_is_known_by_an_argument_its_own_harness_declares():
    assert A._headless_argv(["/usr/local/bin/claude", "-p", "the next slice"])
    assert A._headless_argv(["claude", "--print", "x"])
    assert A._headless_argv(["node", "/opt/lib/claude-code/cli.js", "-p"])                  # a runtime running it
    assert A._headless_argv(["/opt/bin/kiro-cli", "chat", "--no-interactive", "x"])
    assert A._headless_argv(["agy", "--print=the next slice", "--output-format=json"])      # its value attached
    # One harness's argument is an ordinary one of another's: hermes selects a profile with -p.
    assert A._headless_argv(["hermes", "-z", "x"]) and not A._headless_argv(["hermes", "-p", "work"])
    assert not A._headless_argv(["claude", "--no-interactive"]) and not A._headless_argv(["kiro-cli", "chat", "-p"])
    assert not A._headless_argv(["/bin/zsh", "-c", "claude -p hello"]) and not A._headless_argv([])
    assert not A._headless_argv(["/usr/bin/python3", "-c", "x", "-p"])       # no shipped harness runs as this command


def test_every_shipped_adapter_declares_its_unattended_arguments_and_every_command_it_starts_holds_one():
    for ident, adapter in sorted(A.package_adapters().items()):
        declared = (adapter.get("detect") or {}).get("headless")
        assert isinstance(declared, list) and all(isinstance(word, str) and word for word in declared), ident
        commands = [(adapter.get("send") or {}).get("argv"), (adapter.get("resume") or {}).get("argv"),
                    (adapter.get("resume") or {}).get("continue_last")]
        for argv in (argv for argv in commands if argv):
            assert A._headless_argv([re.sub(r"\{[a-z_]+\}", "x", part) for part in argv]), (ident, argv)


def test_is_headless_reads_a_process_by_its_pid_alone(monkeypatch):
    from ao import procs
    vectors = {11: ["/usr/local/bin/claude", "-p", "x"], 12: ["/usr/local/bin/claude"],
               13: ["/opt/bin/kiro-cli", "chat", "--no-interactive", "x"], 14: None}
    monkeypatch.setattr(procs, "argv", lambda pid: vectors.get(pid))

    assert [pid for pid in vectors if A._is_headless(pid)] == [11, 13]


def test_a_hold_stops_only_the_turns_their_own_harness_calls_unattended(monkeypatch):
    from ao import procs
    vectors = {21: ["/usr/local/bin/claude", "-p", "x"], 22: ["/usr/local/bin/claude", "--no-interactive"],
               23: ["/opt/bin/kiro-cli", "chat", "--no-interactive", "x"], 24: ["/opt/bin/kiro-cli", "chat"]}
    monkeypatch.setattr(procs, "all_pids", lambda: list(vectors))
    monkeypatch.setattr(procs, "argv", lambda pid: vectors.get(pid))
    monkeypatch.setattr(procs, "cwd", lambda pid: "/repo")
    monkeypatch.setattr(A, "helper_pids", lambda root: set())
    monkeypatch.setattr(A.os.path, "realpath", lambda p: p)

    assert A.agent_pids("/repo", {}) == [21, 22, 23, 24]
    assert A.agent_pids("/repo", {}, headless_only=True) == [21, 23]


def test_an_architect_session_holding_another_harness_argument_is_a_person_at_the_keyboard(monkeypatch):
    _architect_process_table(monkeypatch, {201: ["/agents/claude", "--no-interactive"]}, {201: "/repo"})
    assert A.architect_present("/repo", {"argv": ["claude"]})

    _architect_process_table(monkeypatch, {202: ["/agents/claude", "--print", "triage"]}, {202: "/repo"})
    assert not A.architect_present("/repo", {"argv": ["claude"]})


def test_the_declaration_decides_and_a_layer_an_agent_can_write_does_not(project, monkeypatch):
    root = project["root"]
    os.makedirs(os.path.join(root, ".ao", "adapters"), exist_ok=True)
    with open(os.path.join(root, ".ao", "adapters", f"{HARNESS}.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(A.load_adapter(HARNESS), detect={"headless": ["--resume"]}), fh)

    assert A.load_adapter(HARNESS, root)["detect"]["headless"] == ["--resume"]
    assert not A._headless_argv(["claude", "--resume", "s1"]) and A._headless_argv(["claude", "--resume", "s1", "-p"])

    newcomer = {"id": "newcomer", "send": {"argv": ["nc", "--unattended", "{prompt}"]},
                "detect": {"binaries": ["nc"], "headless": ["--unattended"]}}
    monkeypatch.setattr(A, "package_adapters", lambda: {"newcomer": newcomer})
    assert A._headless_argv(["/opt/bin/nc", "--unattended", "x"]) and not A._headless_argv(["claude", "-p", "x"])
    monkeypatch.setattr(A, "package_adapters", lambda: {"newcomer": dict(newcomer, detect={"binaries": ["nc"]})})
    assert not A._headless_argv(["/opt/bin/nc", "--unattended", "x"])


# ── the core names nothing these readings declare ──

def test_the_core_holds_no_declared_stop_reason_and_no_unattended_argument_where_it_decides_one():
    reasons, arguments = set(), set()
    for adapter in A.package_adapters().values():
        ends = ((adapter.get("transcript") or {}).get("turn") or {}).get("end_when")
        for when in ends if isinstance(ends, list) else [ends]:
            reasons |= set(when.get("values") or []) if isinstance(when, dict) else set()
        arguments |= set((adapter.get("detect") or {}).get("headless") or [])
    assert {"end_turn", "stop_sequence", "refusal"} <= reasons and {"-p", "--print", "--no-interactive"} <= arguments

    found = []
    for path in sorted((ROOT / "src" / "ao").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        found += [f"{path.name}:{node.lineno} {node.value!r}" for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
                  and node.value in reasons]
        for function in ast.walk(tree):
            if isinstance(function, ast.FunctionDef) and function.name in ("_is_headless", "_headless_argv",
                                                                            "agent_pids", "architect_present"):
                found += [f"{path.name}:{node.lineno} {node.value!r}" for node in ast.walk(function)
                          if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
                          and node.value in arguments]

    assert found == []
