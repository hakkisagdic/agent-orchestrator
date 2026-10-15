"""Mail ack consumed another reader's messages, malformed requests ended stdio servers,
and terminal fields and copied commands acted as instructions. Validate before dispatch
or acknowledgement, escape display text, and quote only what the shell keeps literal.
Deep lane records become unreadable; hook removal rechecks a replacement; concurrent
harness record writers keep each other's entries under one lock (LEADS-OPEN).

All projects and records belong to the fixture; no harness or scheduler is started.
"""
import io
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import a2a_mcp, cli, lib as A, mcp, storage


TAINTED = "text\x1b]0;forged\x07\nforged row\u202e"


def _safe_output(capsys):
    out = capsys.readouterr().out
    assert TAINTED not in out and "\x1b" not in out and "\u202e" not in out
    assert "\\x1b" in out and "\\x0a" in out and "\\u202e" in out


@pytest.mark.parametrize("command", ["banner", "commit-ok", "commit-check"])
def test_urgent_printers_escape_message_text(project, monkeypatch, capsys, command):
    monkeypatch.setattr(cli, "C", dict.fromkeys(cli.C, ""))
    monkeypatch.setattr(A, "urgent_messages", lambda *args: [{"id": TAINTED, "title": TAINTED}])
    if command == "banner":
        cli._urgent_banner(project)
    elif command == "commit-ok":
        assert cli.cmd_commit_ok(project, SimpleNamespace()) == 1
    else:
        assert cli.cmd_commit_check(project, SimpleNamespace()) == 1
    _safe_output(capsys)


@pytest.mark.parametrize("state", ["open", "answered"])
def test_decisions_escape_every_displayed_field(project, monkeypatch, capsys, state):
    monkeypatch.setattr(cli, "C", dict.fromkeys(cli.C, ""))
    row = dict(id=TAINTED, question=TAINTED, state=state, asked_at=0, proposal=TAINTED,
               options=[{"key": TAINTED, "label": TAINTED}], answer=TAINTED, answered_by=TAINTED,
               answers=[{"answer": TAINTED}, {"answer": TAINTED}])
    monkeypatch.setattr(A, "decisions", lambda root: [row])
    assert cli.cmd_decisions(project, SimpleNamespace(n=10)) == 0
    _safe_output(capsys)


@pytest.mark.parametrize("detail", [False, True])
def test_notices_escape_text_but_keep_json_values(project, monkeypatch, capsys, detail):
    monkeypatch.setattr(cli, "C", dict.fromkeys(cli.C, ""))
    row = dict(id=TAINTED, title=TAINTED, msg=TAINTED, at=0, key=TAINTED, evidence={})
    monkeypatch.setattr(A, "notices", lambda *args, **kwargs: [row])
    monkeypatch.setattr(A, "evidence_lines", lambda evidence: [TAINTED])
    args = SimpleNamespace(ident=TAINTED if detail else None, n=10, all=True, json=False)
    assert cli.cmd_notices(project, args) == 0
    # A message's own line breaks stay lines in the detail view.
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\u202e" not in out and "\\x1b" in out
    args.json = True
    assert cli.cmd_notices(project, args) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["notice"] if detail else data["notices"][0])["msg"] == TAINTED


def test_reviews_escape_record_fields(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "C", dict.fromkeys(cli.C, ""))
    monkeypatch.setattr(cli, "_review_states", lambda root: [
        dict(id=TAINTED, state="finished", slice=TAINTED, verdict=TAINTED)])
    assert cli.cmd_reviews(project, SimpleNamespace()) == 0
    _safe_output(capsys)


@pytest.mark.parametrize("store", ["deletion", "append-only"])
@pytest.mark.parametrize("role", ["implementer", "architect", "reviewer", "misspelled"])
def test_mail_ack_refuses_a_glob_with_another_readers_mail(project, monkeypatch, capsys, store, role):
    root = project["root"]
    cfg = dict(project, mail={"store": store})
    Path(root, ".ao", "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    names = ["20261012-0900-fable-to-kiro-DECISION-go.md",
             "20261012-0901-kiro-to-fable-REPORT-done.md"]
    for name in names:
        A.write_mail(root, cfg, name, "# Read this\n", {"kind": "note"})
    monkeypatch.setenv("AO_ROLE", role)
    args = SimpleNamespace(action="ack", type="*.md", body=None)

    assert cli.cmd_mail(cfg, args) == 1
    assert "not acknowledged" in capsys.readouterr().out
    assert A.mailbox(root, cfg["mailbox"]) == names
    assert not any(r.get("event") == "consumed" for r in A.mail_log(root))
    if store == "append-only":
        assert A.unhandled_messages(root) == names

    if role in ("implementer", "architect"):
        args.type = names[0 if role == "implementer" else 1]
        assert cli.cmd_mail(cfg, args) in (None, 0)
    monkeypatch.delenv("AO_ROLE")
    args.type = "*.md"
    assert cli.cmd_mail(cfg, args) in (None, 0)
    assert A.mailbox(root, cfg["mailbox"]) == []


def _serve(module, project, monkeypatch, capsys, lines):
    monkeypatch.setattr(sys, "argv", ["ao", "-C", project["root"]])
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    if module is a2a_mcp:
        monkeypatch.setattr(module, "ROOT", None)
    module.main()
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


@pytest.mark.parametrize("error", [ValueError, RecursionError])
def test_a2a_bridge_reads_on_after_parser_limits(project, monkeypatch, capsys, error):
    loads = json.loads

    def limited(text, *args, **kwargs):
        if text == "parser-limit":
            raise error("parser limit")
        return loads(text, *args, **kwargs)

    monkeypatch.setattr(a2a_mcp.json, "loads", limited)
    assert _serve(a2a_mcp, project, monkeypatch, capsys,
                  ["parser-limit", '{"id":7,"method":"ping"}']) == [
                      {"jsonrpc": "2.0", "id": 7, "result": {}}]


@pytest.mark.parametrize("module", [mcp, a2a_mcp], ids=["mcp", "a2a"])
@pytest.mark.parametrize("ident", [[], {}, True, float("nan"), float("inf")])
def test_mcp_rejects_invalid_ids_before_dispatch(project, monkeypatch, capsys, module, ident):
    called = []
    monkeypatch.setattr(module, "call", lambda *args: called.append(args) or {})
    replies = _serve(module, project, monkeypatch, capsys, [
        json.dumps({"id": ident, "method": "tools/call", "params": {"name": "anything"}}),
        '{"id":7,"method":"ping"}'])
    assert called == []
    assert replies[0]["id"] is None and replies[0]["error"]["code"] == -32600
    assert replies[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


@pytest.mark.parametrize("field,value", [("by", "Ada Lovelace"), ("paths", "dir & files"),
                                        ("commits", "base..topic")])
def test_person_review_quotes_every_copyable_word_for_windows(monkeypatch, capsys, field, value):
    quote = A.copyable_word
    monkeypatch.setattr(A, "copyable_word", lambda word: quote(word, windows=True))
    person = {"by": value if field == "by" else "Ada"}
    args = SimpleNamespace(paths=[value] if field == "paths" else None,
                           commits=value if field == "commits" else None)
    cli._show_person_review(person, "diff\n", {"diff_digest": "sha256:" + "a" * 64}, "scope", args)
    commands = [line for line in capsys.readouterr().out.splitlines() if "--verdict" in line]
    assert len(commands) == 2 and all(quote(value, windows=True) in line for line in commands)


@pytest.mark.parametrize("field", ["by", "paths", "commits"])
def test_person_review_does_not_offer_an_expanding_windows_command(monkeypatch, capsys, field):
    quote = A.copyable_word
    monkeypatch.setattr(A, "copyable_word", lambda word: quote(word, windows=True))
    word = "$(run-something)"
    cli._show_person_review({"by": word if field == "by" else "Ada"}, "diff\n",
                            {"diff_digest": "sha256:" + "a" * 64}, "scope",
                            SimpleNamespace(paths=[word] if field == "paths" else None,
                                            commits=word if field == "commits" else None))
    out = capsys.readouterr().out
    assert "ao person-review --by" not in out
    assert "cannot safely quote" in out


def test_lane_records_names_a_parser_limit_as_unreadable(project, monkeypatch):
    directory = Path(project["root"], ".ao", "lanes")
    directory.mkdir()
    (directory / "deep.json").write_text("parser-limit", encoding="utf-8")
    (directory / "ok.json").write_text('{"state":"finished"}', encoding="utf-8")
    load = json.load

    def limited(fh, *args, **kwargs):
        if Path(fh.name).name == "deep.json":
            raise RecursionError("parser limit")
        return load(fh, *args, **kwargs)

    monkeypatch.setattr(A.json, "load", limited)
    assert A.lane_records(project["root"]) == [{"name": "deep", "state": "unreadable"},
                                               {"name": "ok", "state": "finished"}]


@pytest.mark.parametrize("during", ["before reread", "during reread"])
def test_hook_uninstall_preserves_a_replacement(project, monkeypatch, capsys, during):
    root = project["root"]
    path = Path(root, ".git", "hooks", "pre-commit")
    path.write_bytes(cli._render_local_hook("pre-commit", "."))
    path.chmod(0o755)
    inv = cli._ao_hook_inventory(root)
    foreign = b"#!/bin/sh\n# another tool's hook\nexit 0\n"
    if during == "before reread":
        path.write_bytes(foreign)
    else:
        classify = cli._classify_hook

        def swap(candidate, *args, **kwargs):
            result = classify(candidate, *args, **kwargs)
            if candidate == str(path):
                path.write_bytes(foreign)
            return result

        monkeypatch.setattr(cli, "_classify_hook", swap)
    assert cli._hooks_uninstall(inv, False) == 1
    assert path.read_bytes() == foreign
    assert "left" in capsys.readouterr().out


@pytest.mark.parametrize("first_kind", ["written", "seen"])
def test_harness_seen_writers_keep_each_others_records(project, monkeypatch, first_kind):
    root = project["root"]
    target = A.project_file(root, "harness-seen")
    files = [(Path(root, "CLAUDE.md"), b"first\n"), (Path(root, "AGENTS.md"), b"second\n")]
    for path, data in files:
        path.write_bytes(data)
    ready, contender = threading.Event(), threading.Event()
    read, replace, lock = A.harness_seen_record, storage.replace_file_durably, storage._exclusive_lock

    def observed_read(where):
        record = read(where)
        if threading.current_thread().name.endswith("_1"):
            contender.set()
        return record

    @contextmanager
    def observed_lock(*args, **kwargs):
        if threading.current_thread().name.endswith("_1"):
            contender.set()
        with lock(*args, **kwargs):
            yield

    def paused_write(path, data, **kwargs):
        if path == target and threading.current_thread().name.endswith("_0"):
            ready.set()
            assert contender.wait(5), "the other writer never reached the record"
        return replace(path, data, **kwargs)

    monkeypatch.setattr(A, "harness_seen_record", observed_read)
    monkeypatch.setattr(storage, "replace_file_durably", paused_write)
    monkeypatch.setattr(storage, "_exclusive_lock", observed_lock)
    changes = [("CLAUDE.md", "modified", "digest")]
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="harness") as pool:
        first = pool.submit(A.record_harness_written, root, [files[0]], "first") if first_kind == "written" else \
            pool.submit(A.record_harness_seen, root, changes, "Ada", "digest")
        if not ready.wait(5):
            first.result(timeout=1)
            pytest.fail("the first writer never reached its write")
        second = pool.submit(A.record_harness_written, root, [files[1]], "second")
        first.result(timeout=10)
        second.result(timeout=10)
    record = read(root)
    assert "AGENTS.md" in {item["path"] for item in record["written"]}
    if first_kind == "written":
        assert "CLAUDE.md" in {item["path"] for item in record["written"]}
    else:
        assert record["changes"] == [{"path": "CLAUDE.md", "state": "modified", "sha256": "digest"}]
