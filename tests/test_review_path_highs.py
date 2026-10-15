"""What a person approves is bound where the implementer cannot rewrite it, and an ACP reviewer only reads
(REVIEW-PATH-HIGHS).

An independent re-review of ao's history found these on the review path; each was reproduced here first.

- A stand-in request bound a person's carried answer through `.ao/review-requests/<nonce>.json`, a file the
  implementer writes. Rewritten to name bytes it staged after the request - or written whole for a request ao never
  made - it had a person's genuine approval recorded for bytes no session was shown; emptied criteria let a criterion
  the session found not met hold nothing up; and the text a person carried was the implementer's to change too. A
  request is kept outside the tree now, in ~/.ao/review-requests/<project>/, its binding and its text, and
  `ao remove` takes them with the project. A collect reads the binding there alone and the text it binds must be the
  one ao wrote; the review compares the candidate, scope and diff it measures with the binding before it records
  anything, so bytes staged between the collect's check and the review are not taken either; and the answer is
  recorded as the slice its request was made for, where it went to whatever ran when a person collected it (#12).
- `ao person-review` bound a person's verdict to the diff alone and read the board again when it recorded it: a slice
  moved to running in between, one listing no criteria, had the approval recorded with none - a criterion the person
  had found not met then held nothing up - and as a slice they never judged. The digest a person quotes is of the
  diff and of the slice, boundary and criteria shown with it.
- An ACP reviewer was not held to reads. `review.transport` came from `.ao/config.json`, outside the reviewer a person
  accepts, and was read from the file again where a route started; `qodercli --acp`, with none of the flags that hold
  qoder's spawned reviewer, reviewed over ACP; every read it asked for was allowed, a key in the person's home among
  them; and a write it ran without asking that ended `failed` was no write, its answer taken as a review. The
  transport is part of the reviewer a person accepts and is judged as ao read it; only an adapter whose ACP command
  was measured to hold a reviewer to reads - kiro's agent - answers over ACP; a read or a search is let run only
  inside the folder the review runs in; and any write the agent reports, whatever its status, voids its answer.
  Round 2 found that an innocent location hid a traversal in a search pattern or an unknown input key, and an
  encoded file URI was checked before decoding. Every string value now has to name a contained path or a simple
  pattern whose fixed prefix stays inside; ambiguous syntax is refused (REVIEW-PATH-HIGHS).
- A borrowed Kiro agent kept `"prompt": "file:///..."` as written, though KIRO-CONTENT-2 took out a resource outside
  the project's tree for the same reason: the prompt loaded a file of this machine the source chose. A field the
  adapter declares its agent loads is now held as a resource is.
"""
import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage
from tests.test_criteria_verdicts import THREE, _plain, _running
from tests.test_review_chain import _args, _repo_with_change
from tests.test_route_argv import _reviewer, _seen, _store
from tests.test_standin_collect import APPROVED, _answer, _carried_rows, _collect, _unreachable

ANSWER = "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n"


def _within(path, root):
    path, root = (os.path.normcase(os.path.realpath(p)) for p in (path, root))
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _stage(root, text):
    with open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write(text)
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)


def _bound_to(root, candidate):
    """What a request for `candidate` binds, every digest as ao computes it - as the implementer can compute it too."""
    scope = A.candidate_scope(candidate)
    diff = A.candidate_diff(root, candidate, scope)
    return {"candidate": candidate["digest"], "index_tree": candidate["index_tree"], "scope": scope,
            "diff_digest": "sha256:" + hashlib.sha256(diff).hexdigest(), "paths": None, "criteria": []}


def _write_in_tree(root, nonce, request, text=None):
    """Write a request where ao kept them in the tree - and, while ao keeps them there, where it reads them - as the
    implementer can."""
    folders = {os.path.join(root, ".ao", "review-requests")}
    if _within(A.review_requests_dir(root), root):
        folders.add(A.review_requests_dir(root))
    for folder in folders:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{nonce}.json"), "w", encoding="utf-8") as fh:
            json.dump(request, fh)
        if text is not None:
            with open(os.path.join(folder, f"{nonce}.md"), "wb") as fh:
                fh.write(text)


# ---- a stand-in request binds outside the tree ------------------------------------------------------------------

def test_a_request_rewritten_in_the_tree_records_no_answer_for_bytes_staged_after_it(project, tmp_path, capsys):
    """TRIAGE-3 H2: the request named the bytes a session was shown; the implementer staged others and turned the
    request's candidate, scope, diff and criteria to them, and the person's genuine approval was recorded for them."""
    cfg, request = _unreachable(project)
    root, nonce = cfg["root"], request["nonce"]
    _stage(root, "x = 'bytes no session was shown'\n")
    forged = {key: value for key, value in request.items() if key != "path"}
    forged.update(_bound_to(root, A.index_candidate(root)))
    _write_in_tree(root, nonce, forged)

    code, out = _collect(cfg, nonce, _answer(tmp_path, nonce, APPROVED), capsys)

    assert code == 1 and "the staged candidate changed since the request" in out, out
    assert _carried_rows(root) == [] and not _within(A.review_requests_dir(root), root)


def test_a_request_ao_never_wrote_is_named_and_never_read(project, tmp_path, capsys):
    """A request is the implementer's to write whole while ao reads it from the tree: a text of its choosing for a
    session to judge, bound to the bytes it staged. One in .ao/review-requests/ binds nothing now."""
    root = project["root"]
    _repo_with_change(root)
    nonce = "c" * 32
    text = (f"NONCE: {nonce}\n--- CANDIDATE DIFF ---\n+x = 'a harmless line'\n").encode("utf-8")
    forged = dict(_bound_to(root, A.index_candidate(root)), nonce=nonce, at=0, boundary="b", slice=None,
                  collected=None, text="sha256:" + hashlib.sha256(text).hexdigest())
    _write_in_tree(root, nonce, forged, text)

    code, out = _collect(project, nonce, _answer(tmp_path, nonce, APPROVED), capsys)

    assert code == 2 and "where an older ao kept requests and the implementer can rewrite them" in out, out
    assert _carried_rows(root) == []


def test_the_text_a_person_carries_is_the_one_ao_wrote(project, tmp_path, capsys):
    cfg, request = _unreachable(project)
    root, nonce = cfg["root"], request["nonce"]
    text = os.path.join(A.review_requests_dir(root), f"{nonce}.md")
    assert not _within(text, root)
    with open(text, "ab") as fh:
        fh.write(b"\n+x = 'a line ao never wrote'\n")

    code, out = _collect(cfg, nonce, _answer(tmp_path, nonce, APPROVED), capsys)

    assert code == 1 and "text is not the one ao wrote for these bytes" in out and _carried_rows(root) == []


def test_bytes_staged_after_the_collect_compared_them_are_not_recorded_for_the_answer(project, tmp_path,
                                                                                      monkeypatch, capsys):
    """The collect compared the staged candidate with the request once, and the review measured it again: bytes staged
    between the two were recorded as the ones the session approved."""
    cfg, request = _unreachable(project)
    root, nonce = cfg["root"], request["nonce"]
    measured, real = A.index_candidate(root), A.index_candidate
    _stage(root, "x = 'staged while the person collected'\n")
    calls = []

    def first_the_bytes_of_the_request(at):
        calls.append(at)
        return measured if len(calls) == 1 else real(at)
    monkeypatch.setattr(A, "index_candidate", first_the_bytes_of_the_request)

    code, out = _collect(cfg, nonce, _answer(tmp_path, nonce, APPROVED), capsys)

    assert code == 1 and "is not the one the request was made for" in out and "nothing was recorded" in out, out
    assert _carried_rows(root) == [] and len(calls) >= 2


def test_a_carried_answer_is_recorded_as_the_slice_its_request_was_made_for(project, tmp_path, capsys):
    """TRIAGE-3 #12: the board moved to S2 before the person collected, and the review was S2's."""
    cfg, request = _unreachable(project)
    root, nonce = cfg["root"], request["nonce"]
    _running(root, "- [S2] another slice · acceptance: b")

    code, out = _collect(cfg, nonce, _answer(tmp_path, nonce, APPROVED), capsys)

    assert code == 0, out
    (row,) = _carried_rows(root)
    assert row["slice"] == "S1"


def test_remove_takes_the_projects_requests_and_leaves_another_projects(tmp_path, monkeypatch, capsys):
    """The requests left .ao/ with the project; kept in ~/.ao, they would outlive it, diffs and all."""
    from tests.test_safe_remove import REMOVE, _home, _project
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_installed_jobs", lambda key: [])
    root, neighbour = _project(tmp_path / "work", "proj"), _project(tmp_path / "work", "proj-x")
    for path in (root, neighbour):
        folder = A.review_requests_dir(path)
        os.makedirs(folder)
        for name in ("a" * 32 + ".json", "a" * 32 + ".md", "a" * 32 + ".collect.lock"):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                fh.write("{}\n")
    cfg = {"root": root, "reviews": "semantic-review"}

    assert cli.cmd_remove(cfg, SimpleNamespace(yes=False, allow_shared_hooks=False)) == 0
    assert "   ~/.ao/review-requests/proj/: the review requests ao wrote for it" in _plain(capsys)
    assert cli.cmd_remove(cfg, REMOVE) == 0

    assert not os.path.exists(A.review_requests_dir(root, "proj"))
    assert len(os.listdir(A.review_requests_dir(neighbour, "proj-x"))) == 3


# ---- a person's verdict is bound to what they were shown --------------------------------------------------------

def test_a_persons_verdict_is_refused_when_the_board_moved_after_they_read(project, tmp_path, capsys):
    root = project["root"]
    _repo_with_change(root)
    _running(root, f"- [S1] the store · acceptance: {THREE}")
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "s1", "name": "claude", "model": "model-a"})
    args = dict(by="A. Person", verdict=None, digest=None, findings=None, boundary=None, paths=None, commits=None)
    capsys.readouterr()
    assert cli.cmd_person_review(cfg, SimpleNamespace(**args)) == 0
    shown = _plain(capsys)
    digest = shown.split("--verdict APPROVED --digest ", 1)[1].split()[0]
    findings = tmp_path / "findings.txt"
    findings.write_text("CRITERION 1: MET - read it\nCRITERION 2: NOT MET - nothing replays the store\n"
                        "CRITERION 3: MET - read the test\n", encoding="utf-8")
    recorded = dict(args, verdict="APPROVED", digest=digest, findings=str(findings))
    # The implementer moves a slice that lists no criteria to running while the person writes their findings.
    _running(root, "- [S9] a slice of its own · acceptance: one sentence")

    code = cli.cmd_person_review(cfg, SimpleNamespace(**recorded))

    out = _plain(capsys)
    assert code == 1 and "is not the one you read" in out, out
    assert storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN) == []
    # With the board as the person read it, the verdict they gave is recorded on it.
    _running(root, f"- [S1] the store · acceptance: {THREE}")
    assert cli.cmd_person_review(cfg, SimpleNamespace(**recorded)) == 0
    (row,) = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)
    with open(os.path.join(root, "semantic-review", row["artefact"]), encoding="utf-8") as fh:
        evidence = A.review_evidence(fh.read())
    assert row["slice"] == "S1" and [c["verdict"] for c in evidence["criteria"]] == ["met", "not met", "met"]


# ---- an ACP reviewer only reads ---------------------------------------------------------------------------------

def test_turning_the_transport_to_acp_is_a_change_to_the_reviewer_a_person_accepted(project, monkeypatch, capsys):
    """TRIAGE-4 finding 5: the transport was read from the file the implementer writes, and no person saw it change."""
    root = project["root"]
    _store(root, reviewer=_reviewer())
    cfg = A.hold_unseen_routes(root, A.load_config(root))
    assert cfg.get("_unseen") is None and "transport" not in A.turn_identity(cfg, "reviewer")
    _store(root, review={"transport": "acp"})

    assert [(path, state) for path, state, _ in A.unseen_harness_config(root)] == [
        ("the reviewer's command", "changed")]
    started = []
    monkeypatch.setattr(cli, "_run_reviewer", lambda *args, **kwargs: started.append(args) or {"ok": True})
    capsys.readouterr()
    assert cli.cmd_review(A.load_config(root), _args()) == 2
    out = _plain(capsys)
    assert "not reviewed" in out and "the reviewer's command (changed)" in out and started == []
    assert _seen(cfg) == 0
    shown = _plain(capsys)
    assert '+transport: "acp"' in shown and "through its adapter's ACP command, where that was measured" in shown
    assert _seen(cfg, digest=A.harness_config_digest(A.unseen_harness_config(root))) == 0
    assert A.hold_unseen_routes(root, A.load_config(root)).get("_unseen") is None


def test_a_person_who_sets_the_transport_is_told_it_waits_for_them(project, capsys):
    root = project["root"]
    _store(root, reviewer=_reviewer())
    A.hold_unseen_routes(root, A.load_config(root))
    capsys.readouterr()

    assert cli.cmd_config(A.load_config(root), SimpleNamespace(action="set", key="review.transport", value="acp",
                                                               machine=False, by=None)) == 0

    assert "ao harness seen --by <name>" in _plain(capsys)
    assert [path for path, _, _ in A.unseen_harness_config(root)] == ["the reviewer's command"]


def test_a_review_reaches_its_routes_as_it_read_the_transport_whatever_the_file_says_after(project, monkeypatch,
                                                                                           capsys):
    """Read again where a route started, the file could say acp after ao had judged the reviewer it accepted."""
    root = project["root"]
    _repo_with_change(root)
    implementer = {"adapter": "claude-code", "session": "s1", "name": "claude", "model": "model-a"}
    _store(root, implementer=implementer, reviewer=dict(A.compose_reviewer("kiro"), family="kiro-family"))
    cfg = A.hold_unseen_routes(root, A.load_config(root))
    _store(root, review={"transport": "acp"})                      # after ao read it, as the implementer can
    through, spawned = [], []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (f"/agents/{name}", "2.27.1"))
    monkeypatch.setattr(cli, "_run_acp_reviewer", lambda root, argv, *args, **kwargs: through.append(argv) or {
        "ok": True, "out": ANSWER, "transport": "acp", "acp": {"adapter": "kiro", "agent": "fake"}})
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kwargs:
                        spawned.append(argv) or {"ok": True, "out": ANSWER})

    code = cli.cmd_review(cfg, _args())

    assert code == 0, _plain(capsys)
    assert through == [] and [argv[0] for argv in spawned] == ["/agents/kiro-cli"]


def test_only_an_acp_command_measured_to_hold_a_reviewer_to_reads_answers_over_acp(project, monkeypatch, capsys):
    """TRIAGE-4 finding 5: `qodercli --acp` reviewed with none of --tools Read,Grep,Glob, --permission-mode dont_ask
    or --strict-mcp-config, held by nothing but whether it asks first, which nobody has measured."""
    root = project["root"]
    qoder, kiro = A.compose_reviewer("qoder"), A.compose_reviewer("kiro")

    through, why = cli._acp_reviewer_command(root, qoder, False, "acp")

    assert through is None and "nobody measured that the ACP command adapter qoder declares, `qodercli --acp`, " \
                               "holds a reviewer to reads" in why
    assert "--tools Read,Grep,Glob --permission-mode dont_ask --strict-mcp-config" in why
    assert cli._acp_reviewer_command(root, kiro, False, "acp") == ({"argv": ["kiro-cli", "acp"], "adapter": "kiro"},
                                                                  None)
    reached, spawned = [], []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (f"/agents/{name}", "1.1.59"))
    monkeypatch.setattr(cli, "_run_acp_reviewer", lambda *args, **kwargs: reached.append(args) or {"ok": True})
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kwargs:
                        spawned.append(argv) or {"ok": True, "out": ANSWER})
    capsys.readouterr()

    cli._reviewer_route_invocation(root, qoder, "review this", 60, False, qoder, transport="acp")

    assert reached == [] and spawned[0][:2] == ["/agents/qodercli", "-p"] and "--strict-mcp-config" in spawned[0]
    assert "qoder-reviewer is spawned, not run through ACP: nobody measured" in _plain(capsys)


def test_a_read_is_let_run_only_inside_the_folder_the_review_runs_in(tmp_path):
    folder = tmp_path / "review"
    (folder / "candidate").mkdir(parents=True)
    once = [{"optionId": "always", "kind": "allow_always"}, {"optionId": "once", "kind": "allow_once"}]

    def decided(**call):
        return cli._acp_reviewer_permission(dict({"kind": "read"}, **call), once, str(folder))

    assert decided(locations=[{"path": str(folder / "candidate" / "a.py")}]) == "once"
    assert decided(kind="search", locations=[{"path": str(folder)}], rawInput={"pattern": "TODO"}) == "once"
    assert decided(locations=[{"path": "candidate/a.py", "line": 3}]) == "once"         # read from where it runs
    assert decided(rawInput={"path": "candidate/a.py"}) == "once"                       # its input names where
    # A pattern can name where a glob reads; a grep spelling the same value gets no wider leave (REVIEW-PATH-HIGHS).
    assert decided(kind="search", rawInput={"pattern": "/api/v1", "path": "candidate"}) is None
    for refused in ({"locations": [{"path": str(tmp_path / "id_ed25519")}]},
                    {"locations": [{"path": os.path.join(os.path.expanduser("~"), ".ssh", "id_ed25519")}]},
                    {"locations": [{"path": str(folder / "candidate" / ".." / ".." / "x")}]},
                    {"locations": [{"path": str(folder / "a")}], "rawInput": {"file_path": "~/.aws/credentials"}},
                    {"locations": [{"path": str(folder / "a")}], "rawInput": {"paths": ["a", "/etc/hosts"]}},
                    {"rawInput": {"operations": [{"mode": "Line", "path": str(tmp_path / "id_ed25519")}]}},
                    {"rawInput": {"path": "file:///etc/hosts"}}, {"rawInput": {"query": "~/.netrc"}},
                    {"rawInput": {"path": "a\x00b"}},
                    {"locations": "everywhere"}, {"locations": [{"line": 1}]}, {}):
        assert decided(**refused) is None, refused
    # No folder given holds a read to nowhere, as ACP-REVIEWER's callers once passed none.
    assert cli._acp_reviewer_permission({"kind": "read", "locations": [{"path": str(folder / "a")}]}, once) is None


@pytest.mark.skipif(os.name == "nt", reason="a link needs a privilege on Windows")
def test_a_read_through_a_link_inside_the_folder_to_a_file_outside_it_is_refused(tmp_path):
    folder, secret = tmp_path / "review", tmp_path / "secret"
    folder.mkdir()
    secret.write_text("key", encoding="utf-8")
    os.symlink(secret, folder / "innocent.txt")

    assert cli._acp_reviewer_permission({"kind": "read", "locations": [{"path": str(folder / "innocent.txt")}]},
                                        [{"optionId": "once", "kind": "allow_once"}], str(folder)) is None


# A scripted agent: AO_TEST_STEPS lists its steps - a read it asks leave for ({"read": path}, `{cwd}` its own folder;
# allowed, it puts what it read into its answer) or a session update it sends ({"report": update}) - then it answers.
AGENT = r"""
import json, os, sys
steps, said = json.loads(os.environ["AO_TEST_STEPS"]), []

def say(message):
    print(json.dumps(message), flush=True)

def receive():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)

def update(session, body):
    say({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": body}})

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
            if "read" not in step:
                update(session, step["report"])
                continue
            path = step["read"].replace("{cwd}", os.getcwd())
            call = {"toolCallId": "r%d" % n, "title": "Read", "kind": "read", "locations": [{"path": path}]}
            say({"jsonrpc": "2.0", "id": "perm-%d" % n, "method": "session/request_permission",
                 "params": {"sessionId": session, "toolCall": call,
                            "options": [{"optionId": "once", "name": "Allow", "kind": "allow_once"},
                                        {"optionId": "no", "name": "Reject", "kind": "reject_once"}]}})
            allowed = receive().get("result", {}).get("outcome", {}).get("optionId") == "once"
            if allowed:
                try:
                    with open(path, encoding="utf-8") as fh:
                        said.append("READ: " + fh.read())
                except OSError:
                    said.append("READ: nothing at " + os.path.basename(path))
            update(session, dict(call, sessionUpdate="tool_call", status="completed" if allowed else "failed"))
        text = "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n" + "\n".join(said)
        update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})
        say({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
"""


def _acp_review(project, monkeypatch, steps):
    monkeypatch.setenv("AO_TEST_STEPS", json.dumps(steps))
    return cli._run_acp_reviewer(project["root"], [sys.executable, "-c", AGENT], "kiro", "review this", 30,
                                 "kiro-reviewer")


def test_a_key_outside_the_reviewers_folder_never_reaches_its_answer(project, tmp_path, monkeypatch, capsys):
    """TRIAGE-4 finding 5: a read of a key in the person's home was allowed once, as any read was."""
    secret = tmp_path / "id_ed25519"
    secret.write_text("PRIVATE KEY MATERIAL", encoding="utf-8")

    attempt = _acp_review(project, monkeypatch, [{"read": str(secret)}, {"read": "{cwd}/candidate/notes.txt"}])

    assert attempt["ok"], attempt
    assert "PRIVATE KEY MATERIAL" not in attempt["out"] and "READ: nothing at notes.txt" in attempt["out"]
    out = _plain(capsys)
    assert "kiro-reviewer was refused a read: it names " in out and "outside the folder the review runs in" in out
    assert "it asked leave 2 time(s), 1 refused" in out


@pytest.mark.parametrize("status", ["failed", "in_progress", "pending"])
def test_a_write_the_agent_ran_without_asking_voids_its_answer_whatever_it_ended_as(project, monkeypatch, status):
    """TRIAGE-4 finding 6: an edit the agent ran without asking that ended `failed` was no write, and the answer
    was taken as a review."""
    edit = {"sessionUpdate": "tool_call", "toolCallId": "w", "title": "Edit a.py", "kind": "edit", "status": status}

    attempt = _acp_review(project, monkeypatch, [{"report": edit}])

    assert not attempt["ok"] and attempt["kind"] == "wrote" and "(edit)" in attempt["reason"], attempt


# ---- a borrowed agent loads no file from outside the project ----------------------------------------------------

def test_a_borrowed_kiro_agent_loads_no_prompt_from_outside_the_project(project, tmp_path, capsys):
    """TRIAGE-4, outside the batch: KIRO-CONTENT-2 took out an outside resource, and a prompt naming a file outside the
    project's tree was kept as written."""
    from tests.test_kiro_content import _add, _agent, _layer, _out, _read
    root = project["root"]
    source, pin = _layer(tmp_path, {"agents/outside.json": _agent("outside", prompt="file:///etc/hosts"),
                                    "agents/home.json": _agent("home", prompt="file://~/.aws/credentials"),
                                    "agents/inside.json": _agent("inside", prompt="file://./prompts/inside.md")})

    assert _add(project, f"{source}@{pin}", agents="outside,home,inside") == 0

    out = _out(capsys)
    assert "prompt" not in json.loads(_read(root, ".kiro/agents/outside.json"))
    assert "prompt" not in json.loads(_read(root, ".kiro/agents/home.json"))
    assert json.loads(_read(root, ".kiro/agents/inside.json"))["prompt"] == "file://./prompts/inside.md"
    assert "skipped agents/outside.json prompt file:///etc/hosts (a file:// prompt is a file the agent loads as its " \
           "instructions, outside the project's tree, which the source chose: never imported)" in out


@pytest.mark.parametrize("kind, field, value", [
    ("search", "rawInput", {"path": ".", "pattern": "../../.ssh/id_ed25519"}),
    ("read", "rawInput", {"uri": "{uri}/%2e%2e/secret"}),
    ("read", "rawInput", {"options": [{"anything": ["{outside}/secret"]}]}),
    ("read", "locations", [{"path": ".", "extra": [{"anything": "{outside}/secret"}]}]),
    ("search", "rawInput", {"path": ".", "pattern": "candidate/*/../secret"}),
    ("search", "rawInput", {"path": ".", "pattern": "{outside}/*.py"}),
    ("read", "rawInput", {"uri": "{uri}/%252e%252e/secret"}),
    ("read", "rawInput", {"uri": "file://localhost{uri_path}/notes.txt"}),
    ("read", "rawInput", {"uri": "{uri}/notes%00.txt"}),
    ("read", "rawInput", {"uri": "{uri}/bad%escape"}),
    ("read", "rawInput", {"uri": "{uri}/notes.txt?other=secret"}),
    ("read", "rawInput", {"uri": "{uri}/notes.txt#secret"}),
    ("search", "rawInput", {"pattern": "candidate/{safe,../../secret}"}),
    ("search", "rawInput", {"pattern": "safe|../../secret"}),
    ("read", "rawInput", {"anything": "https://example.invalid/secret"}),
    ("read", "rawInput", {"anything": ""}),
    ("read", "rawInput", {"anything": "candidate/notes\n.txt"}),
    ("read", "rawInput", {"anything": "candidate/notes\x00.txt"}),
], ids=["search-traversal", "encoded-uri", "nested-input", "nested-location", "grep-parent",
        "absolute-pattern", "double-encoded-uri", "uri-host", "encoded-nul", "malformed-escape",
        "uri-query", "uri-fragment", "brace-pattern", "regex-alternative", "unknown-uri", "empty",
        "newline", "nul"])
def test_acp_checks_every_string_before_allowing_a_read(tmp_path, kind, field, value):
    folder = tmp_path / "review"
    (folder / "candidate").mkdir(parents=True)
    from urllib.parse import urlsplit
    value = json.loads(json.dumps(value).replace("{uri}", folder.as_uri())
                       .replace("{uri_path}", urlsplit(folder.as_uri()).path)
                       .replace("{outside}", tmp_path.as_posix()))
    call = {"kind": kind, "locations": [{"path": "."}], field: value}

    assert cli._acp_reviewer_permission(call, [{"optionId": "once", "kind": "allow_once"}], str(folder)) is None


@pytest.mark.parametrize("kind, value", [
    ("read", {"path": "candidate/notes.txt"}),
    ("read", {"anything": [{"nested": "candidate/notes.txt"}]}),
    ("read", {"uri": "{uri}/candidate/notes%20here.txt"}),
    ("search", {"path": ".", "pattern": "candidate/**/*.py"}),
    ("search", {"path": "candidate", "pattern": "TODO"}),
    ("search", {"pattern": "TODO"}),
], ids=["read", "nested-read", "encoded-read", "glob", "grep", "pattern-alone"])
def test_acp_allows_contained_reads_and_searches_once(tmp_path, kind, value):
    folder = tmp_path / "review"
    (folder / "candidate").mkdir(parents=True)
    value = json.loads(json.dumps(value).replace("{uri}", folder.as_uri()))
    call = {"kind": kind, "rawInput": value}
    options = [{"optionId": "always", "kind": "allow_always"}, {"optionId": "once", "kind": "allow_once"}]

    assert cli._acp_reviewer_permission(call, options, str(folder)) == "once"


@pytest.mark.parametrize("inside", [False, True], ids=["outside", "inside"])
def test_acp_expands_home_before_deciding_where_a_value_reads(tmp_path, monkeypatch, inside):
    folder = tmp_path / "review"
    folder.mkdir()
    home = folder if inside else tmp_path
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    call = {"kind": "read", "locations": [{"path": "."}], "rawInput": {"anything": "~/notes.txt"}}

    assert cli._acp_reviewer_permission(call, [{"optionId": "once", "kind": "allow_once"}], str(folder)) \
        == ("once" if inside else None)


@pytest.mark.skipif(os.name == "nt", reason="a link needs a privilege on Windows")
def test_acp_checks_the_fixed_prefix_of_a_pattern_through_its_links(tmp_path):
    folder = tmp_path / "review"
    folder.mkdir()
    os.symlink(tmp_path, folder / "outside")
    call = {"kind": "search", "locations": [{"path": "."}], "rawInput": {"pattern": "outside/*.py"}}

    assert cli._acp_reviewer_permission(call, [{"optionId": "once", "kind": "allow_once"}], str(folder)) is None
