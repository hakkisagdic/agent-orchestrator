"""A reviewer reached through an OpenAI-compatible chat API, as a tool ao runs over the candidate (API-REVIEWER).

`ao-api-review` is ao's own stdlib client. It asks a provider the machine names in `review.api_providers` once,
with the review prompt as the only message, and writes the answer where ao reads a tool's answer. It has no
tools: the model reads what the prompt carries. These run it against a stand-in of the endpoint on this machine.
"""
import http.server
import json
import os
import threading

import pytest

from ao import api_review, cli, lib as A, storage
from tests.test_review_chain import _args, _repo_with_change

APPROVED = "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n\n## Findings\nNone."


class _Endpoint:
    """A chat-completions stand-in on 127.0.0.1: it keeps each request and answers as it is told."""

    def __init__(self, answer=APPROVED, status=200, location=None, reply=None, turns=None):
        self.requests, endpoint = [], self
        scripted = list(turns) if turns is not None else []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                endpoint.requests.append({"path": self.path, "headers": dict(self.headers),
                                          "body": json.loads(body or b"{}")})
                if location:
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.end_headers()
                    return
                code = status
                if scripted:
                    step = scripted.pop(0)
                    if isinstance(step, tuple):
                        code, payload = step
                    else:
                        payload = step
                elif reply is not None:
                    payload = reply
                else:
                    payload = {"choices": [{"message": {"role": "assistant", "content": answer}}]}
                encoded = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _tool_turn(*calls):
    return {"choices": [{"message": {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"call_{idx}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for idx, (name, args) in enumerate(calls, 1)
        ],
    }, "finish_reason": "tool_calls"}]}


@pytest.fixture
def endpoint():
    made = []

    def make(**kw):
        made.append(_Endpoint(**kw))
        return made[-1]
    yield make
    for one in made:
        one.close()


def _providers(monkeypatch, tmp_path, *entries):
    """The machine's settings, holding these providers; the suite's own home stays as it is."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"review": {"api_providers": list(entries)}}), encoding="utf-8")
    monkeypatch.setenv("AO_SETTINGS", str(path))
    return path


def _run(tmp_path, model, prompt="review this", tree=None):
    diff, out = tmp_path / "candidate.diff", tmp_path / "answer.md"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    argv = ["--model", model, "--diff-file", str(diff), "--output", str(out)]
    if tree is not None:
        argv += ["--tree", str(tree)]
    argv.append(prompt)
    code = api_review.main(argv)
    return code, (out.read_text(encoding="utf-8") if out.exists() else None)


def test_the_client_asks_its_provider_once_and_writes_the_answer(monkeypatch, tmp_path, endpoint, capsys):
    served = endpoint()
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")

    code, answer = _run(tmp_path, "local/glm-5.3", "the review prompt, the diff in it")

    assert code == 0 and answer == APPROVED
    (asked,) = served.requests
    assert asked["path"] == "/v1/chat/completions"
    assert asked["headers"]["Authorization"] == "Bearer" + " " + "a-key-for-this-test"
    assert asked["body"] == {"model": "glm-5.3",
                             "messages": [{"role": "user", "content": "the review prompt, the diff in it"}]}
    assert "a-key-for-this-test" not in capsys.readouterr().out


@pytest.mark.parametrize("entry, why", [
    ("remote http://example.com/v1 AO_TEST_API_KEY", "https"),
    ("remote https://example.com/v1 not-a-name", "environment variable"),
    ("remote https://example.com/v1", "a word, a base URL and"),
])
def test_a_provider_whose_key_could_leave_unprotected_is_refused_before_anything_is_sent(
        monkeypatch, tmp_path, capsys, entry, why):
    _providers(monkeypatch, tmp_path, entry)
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")

    code, answer = _run(tmp_path, "remote/m")

    assert code == 2 and answer is None and why in capsys.readouterr().err


def test_a_provider_not_named_or_a_key_not_set_is_said_and_nothing_is_sent(monkeypatch, tmp_path, endpoint, capsys):
    served = endpoint()
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.delenv("AO_TEST_API_KEY", raising=False)

    assert _run(tmp_path, "elsewhere/m")[0] == 2 and "review.api_providers names no elsewhere" in capsys.readouterr().err
    assert _run(tmp_path, "local/m")[0] == 2 and "AO_TEST_API_KEY is not set" in capsys.readouterr().err
    assert _run(tmp_path, "no-provider")[0] == 2 and "<provider>/<model>" in capsys.readouterr().err
    assert served.requests == []


def test_a_redirect_is_not_followed_with_the_key(monkeypatch, tmp_path, endpoint, capsys):
    elsewhere = endpoint()
    served = endpoint(location=elsewhere.url + "/chat/completions")
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")

    code, answer = _run(tmp_path, "local/m")

    assert code == 1 and answer is None and elsewhere.requests == []
    assert "HTTP 302" in capsys.readouterr().err


def test_an_error_or_an_empty_answer_writes_nothing(monkeypatch, tmp_path, endpoint, capsys):
    failing, silent = endpoint(status=503), endpoint(answer="")
    _providers(monkeypatch, tmp_path, f"failing {failing.url} AO_TEST_API_KEY", f"silent {silent.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")

    assert _run(tmp_path, "failing/m") == (1, None) and "HTTP 503" in capsys.readouterr().err
    assert _run(tmp_path, "silent/m") == (1, None) and "no answer" in capsys.readouterr().err


def test_the_adapter_composes_a_tool_reviewer_ao_may_run():
    adapter = A.load_adapter("openai-api")

    assert A.reviewer_eligibility(adapter) == (True, None) and A.tool_review_problems(adapter) == []
    route = A.compose_reviewer("openai-api", model="evren/glm-5.3", family="zhipu")
    assert route["kind"] == "tool" and route["argv"] == adapter["send"]["argv"]
    assert (route["model"], route["family"]) == ("evren/glm-5.3", "zhipu")


def test_a_review_through_the_api_reviewer_is_read_and_recorded(project, monkeypatch, tmp_path, endpoint):
    """End to end: ao writes the candidate, runs the client as a tool in a directory of its own, and reads the
    answer the stand-in endpoint gave."""
    root = project["root"]
    _repo_with_change(root)
    served = endpoint()
    home = tmp_path / "home"
    (home / ".ao").mkdir(parents=True, exist_ok=True)
    (home / ".ao" / "settings.json").write_text(json.dumps(
        {"review": {"api_providers": [f"local {served.url} AO_TEST_API_KEY"]}}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))                    # the tool reads the machine's settings as ao does
    monkeypatch.setenv("USERPROFILE", str(home))             # where Windows finds the home
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")
    client = cli._tool_beside_interpreter("ao-api-review")
    if client is None:
        pytest.skip("the client's script is installed with ao, and this interpreter has none")
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (
        (client, "ao-api-review") if name == "ao-api-review" else (None, None)))
    route = dict(A.compose_reviewer("openai-api", model="local/glm-5.3", family="zhipu"), id="rv")

    code = cli.cmd_review(dict(project, reviewer=route), _args())

    row = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)[-1]
    assert code == 0 and row["verdict"] == "APPROVED"
    (asked,) = served.requests
    assert asked["body"]["model"] == "glm-5.3" and "diff --git" in asked["body"]["messages"][0]["content"]


def test_a_prompt_past_one_argument_reaches_the_client_on_its_standard_input(project, monkeypatch, tmp_path, endpoint):
    """Linux carries no argument over 131,072 bytes, and a review prompt carries a diff of up to 400 KB: the client
    takes - for its prompt and reads it from standard input, as its adapter declares (PROMPT-CHANNEL)."""
    root = project["root"]
    _repo_with_change(root)
    served = endpoint()
    home = tmp_path / "home"
    (home / ".ao").mkdir(parents=True, exist_ok=True)
    (home / ".ao" / "settings.json").write_text(json.dumps(
        {"review": {"api_providers": [f"local {served.url} AO_TEST_API_KEY"]}}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")
    client = cli._tool_beside_interpreter("ao-api-review")
    if client is None:
        pytest.skip("the client's script is installed with ao, and this interpreter has none")
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (
        (client, "ao-api-review") if name == "ao-api-review" else (None, None)))
    monkeypatch.setattr(A, "argument_overflow", lambda argv, env=None: "this platform carries less in one argument")
    started, real = [], cli.subprocess.Popen
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, *a, **kw: started.append(list(argv)) or real(argv, *a, **kw))
    route = dict(A.compose_reviewer("openai-api", model="local/glm-5.3", family="zhipu"), id="rv")

    code = cli.cmd_review(dict(project, reviewer=route), _args())

    (asked,) = served.requests
    assert code == 0 and "diff --git" in asked["body"]["messages"][0]["content"]
    (handed,) = [argv for argv in started if argv and argv[0] == client]
    assert handed[-1] == "-" and not any("diff --git" in part for part in handed)


def test_a_provider_on_this_machine_is_asked_directly_whatever_proxy_is_named(monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-2: urllib sent a request for 127.0.0.1 through the proxy the environment named, its key in plain
    text, and took that proxy's answer for the provider's."""
    served, proxy = endpoint(), endpoint()
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(name, proxy.url.rsplit("/", 1)[0])
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    code, answer = _run(tmp_path, "local/m")

    assert code == 0 and answer == APPROVED
    assert proxy.requests == [] and len(served.requests) == 1


def test_a_key_no_header_carries_is_refused_and_never_shown(monkeypatch, tmp_path, endpoint, capsys):
    """API-REVIEWER-2: the error that refused a key holding a line break named the header's value, and the key was
    printed with it."""
    served = endpoint()
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key\nfor-this-test")

    code, answer = _run(tmp_path, "local/m")

    said = capsys.readouterr().err
    assert code == 2 and answer is None and served.requests == []
    assert "AO_TEST_API_KEY" in said and "a-key" not in said and "for-this-test" not in said


def test_the_doctor_finds_the_client_where_a_review_finds_it(project, monkeypatch, tmp_path):
    """API-REVIEWER-2: an interpreter installed for everyone on Windows keeps its scripts in Scripts, not beside
    python.exe; a review found the client there, and `ao doctor` named it absent."""
    import sys
    import sysconfig
    scripts, real = tmp_path / "Scripts", sysconfig.get_path
    scripts.mkdir()
    client = scripts / ("ao-api-review.exe" if os.name == "nt" else "ao-api-review")
    client.write_text("", encoding="utf-8")
    client.chmod(0o755)
    monkeypatch.setattr(A, "binary_candidates", lambda name, path=None: [])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "python"))
    monkeypatch.setattr(sysconfig, "get_path", lambda name, *a, **kw: str(scripts) if name == "scripts"
                        else real(name, *a, **kw))
    cfg = dict(project, implementer={"adapter": "openai-api", "session": "s1", "name": "dev"})

    assert A.absent_adapter_binaries(cfg) == [] and cli._tool_beside_interpreter("ao-api-review") == str(client)

    client.unlink()
    assert A.absent_adapter_binaries(cfg) == [("dev", "openai-api", ["ao-api-review"])]


def test_a_protocol_error_that_echoes_the_key_never_shows_it(monkeypatch, tmp_path, capsys):
    """API-REVIEWER-3: a provider that answered with the key in a malformed status line raised BadStatusLine, which
    the client did not catch, and its traceback carried the key."""
    class Echo(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            key = self.headers.get("Authorization", "").split()[-1]
            self.wfile.write(f"HTTP/1.1 {key} is no status\r\n\r\n".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _providers(monkeypatch, tmp_path, f"local http://127.0.0.1:{server.server_address[1]}/v1 AO_TEST_API_KEY")
        monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")
        code, answer = _run(tmp_path, "local/m")
    finally:
        server.shutdown()
        server.server_close()

    said = capsys.readouterr()
    assert code == 1 and answer is None
    assert "a-key-for-this-test" not in said.err + said.out and "<key>" in said.err


def test_the_key_is_taken_out_as_written_and_as_python_escapes_it():
    assert api_review._scrub("got 'k\\\\ey' and k\\ey", "k\\ey") == "got '<key>' and <key>"


def test_a_key_spelled_like_the_mark_is_taken_out_all_the_same():
    """API-REVIEWER-4: a key spelled `<key>` was replaced by the mark `<key>`, itself."""
    assert api_review._scrub("got <key> back", "<key>") == "got <redacted> back"
    assert api_review._scrub("the key e", "e") == "th*** k***y ***"          # a mark holding no part of the key


def test_a_model_that_spent_its_answer_reasoning_is_said_so(monkeypatch, tmp_path, endpoint, capsys):
    """API-REVIEWER-5: EVREN's reasoning models spent all 16,384 tokens it allows on a 28 KB review prompt, and the
    client said only that the provider gave no answer."""
    served = endpoint(reply={"choices": [{"message": {"role": "assistant", "content": None,
                                                      "reasoning_content": "a-key-for-this-test " * 3},
                                          "finish_reason": "length"}],
                             "usage": {"completion_tokens": 16384,
                                       "completion_tokens_details": {"reasoning_tokens": 16384}}})
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")

    code, answer = _run(tmp_path, "local/glm-5.3")

    said = capsys.readouterr().err
    assert code == 1 and answer is None
    assert "glm-5.3 on local spent the whole answer it may give (16384 tokens of thinking)" in said
    assert "a-key-for-this-test" not in said


def test_an_empty_answer_that_did_not_run_out_is_still_no_answer(monkeypatch, tmp_path, endpoint, capsys):
    served = endpoint(reply={"choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}]})
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", "a-key-for-this-test")

    assert _run(tmp_path, "local/m")[0] == 1 and "local gave no answer for m" in capsys.readouterr().err


def test_the_client_with_a_tree_runs_read_grep_glob_and_list_and_answers(monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-TOOLS: a loopback stub calls read, grep, glob and list over --tree DIR and then writes its verdict."""
    import secrets
    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    (tree / "README.md").write_text("# title\n", encoding="utf-8")
    (tree / "src" / "mod.py").write_text("line1 = 1\ncaller_target = 42\nline3 = 3\n", encoding="utf-8")
    (tree / "src" / "notes.txt").write_text("caller_target in notes\n", encoding="utf-8")
    turns = [
        _tool_turn(
            ("list", {"path": "."}),
            ("glob", {"pattern": "src/**/*.py"}),
        ),
        _tool_turn(
            ("grep", {"pattern": "caller_target", "path": "src", "glob": "*.py"}),
            ("read", {"path": "src/mod.py", "first_line": 2, "line_count": 1}),
        ),
    ]
    served = endpoint(turns=turns)
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))

    code, answer = _run(tmp_path, "local/m", "review prompt", tree=tree)

    assert code == 0 and answer == APPROVED
    assert len(served.requests) == 3
    assert [t["function"]["name"] for t in served.requests[0]["body"]["tools"]] == ["read", "grep", "glob", "list"]
    round1_tools = [m for m in served.requests[1]["body"]["messages"] if m["role"] == "tool"]
    assert [m["content"] for m in round1_tools] == ["README.md\nsrc/", "src/mod.py"]
    round2_tools = [m for m in served.requests[2]["body"]["messages"] if m["role"] == "tool"][2:]
    assert round2_tools[0]["content"] == "src/mod.py:2: caller_target = 42"
    assert round2_tools[1]["content"] == "2: caller_target = 42"
    stats = json.loads((tmp_path / "answer.md.tool.json").read_text(encoding="utf-8"))
    assert stats["rounds"] == 2 and stats["read_bytes"] > 0


def test_confinement_refuses_absolute_paths_parent_escapes_and_links_out_and_keeps_links_inside(
        monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-TOOLS: every path is relative to DIR and resolved inside it; a link that stays inside works."""
    import secrets
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_MARKER\n", encoding="utf-8")
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "inside.py").write_text("INSIDE_OK = 1\n", encoding="utf-8")
    (tree / "out_rel").symlink_to("../outside.txt")
    (tree / "out_abs").symlink_to(str(outside))
    (tree / "in_link.py").symlink_to("sub/inside.py")
    (tree / "sub" / "rel_in.py").symlink_to("../sub/inside.py")

    turns = [
        _tool_turn(
            ("read", {"path": str(outside)}),
            ("read", {"path": "../outside.txt"}),
            ("read", {"path": "sub/../../outside.txt"}),
            ("read", {"path": "out_rel"}),
            ("read", {"path": "out_abs"}),
            ("grep", {"pattern": "OUTSIDE", "path": "../outside.txt"}),
            ("grep", {"pattern": "OUTSIDE", "path": "out_rel"}),
            ("glob", {"pattern": "../outside.txt"}),
            ("glob", {"pattern": str(outside)}),
            ("glob", {"pattern": "out_rel"}),
            ("list", {"path": ".."}),
            ("list", {"path": str(tmp_path)}),
            ("list", {"path": "out_rel"}),
            ("read", {"path": "in_link.py"}),
            ("read", {"path": "sub/rel_in.py"}),
        ),
    ]
    served = endpoint(turns=turns)
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))

    code, answer = _run(tmp_path, "local/m", tree=tree)

    assert code == 0 and answer == APPROVED
    results = [m["content"] for m in served.requests[1]["body"]["messages"] if m["role"] == "tool"]
    assert all("OUTSIDE_MARKER" not in r for r in results)
    for refused in results[:13]:
        assert refused.startswith("refused:"), refused
    assert results[13] == "1: INSIDE_OK = 1"
    assert results[14] == "1: INSIDE_OK = 1"


def test_harness_configuration_is_hidden_from_the_tools_at_every_depth(monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-TOOLS: harness configuration in the tree is hidden from read, grep, glob and list."""
    import secrets
    tree = tmp_path / "tree"
    (tree / ".claude").mkdir(parents=True)
    (tree / ".claude" / "settings.json").write_text('{"hooks": "HIDDEN_HOOK"}\n', encoding="utf-8")
    (tree / "CLAUDE.md").write_text("HIDDEN_RULE\n", encoding="utf-8")
    (tree / "pkg" / ".kiro" / "steering").mkdir(parents=True)
    (tree / "pkg" / ".kiro" / "steering" / "guide.md").write_text("HIDDEN_STEERING\n", encoding="utf-8")
    (tree / "pkg" / "visible.py").write_text("VISIBLE_CODE = 1\n", encoding="utf-8")
    (tree / "cfg_link").symlink_to("CLAUDE.md")

    turns = [
        _tool_turn(
            ("list", {"path": "."}),
            ("list", {"path": "pkg"}),
            ("glob", {"pattern": "**/*"}),
            ("grep", {"pattern": "HIDDEN|VISIBLE"}),
            ("read", {"path": "CLAUDE.md"}),
            ("read", {"path": ".claude/settings.json"}),
            ("read", {"path": "pkg/.kiro/steering/guide.md"}),
            ("read", {"path": "cfg_link"}),
            ("list", {"path": ".claude"}),
            ("glob", {"pattern": ".claude/*"}),
        ),
    ]
    served = endpoint(turns=turns)
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))

    code, answer = _run(tmp_path, "local/m", tree=tree)

    assert code == 0 and answer == APPROVED
    results = [m["content"] for m in served.requests[1]["body"]["messages"] if m["role"] == "tool"]
    assert results[0] == "pkg/"
    assert results[1] == "visible.py"
    assert results[2] == "pkg/visible.py"
    assert results[3] == "pkg/visible.py:1: VISIBLE_CODE = 1"
    for refused in results[4:]:
        assert "refused:" in refused and "withheld" in refused
    assert all("HIDDEN_" not in r for r in results)


def test_each_answer_bound_and_each_turn_bound_tells_the_model_and_asks_for_a_verdict_without_tools(
        monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-TOOLS: per-answer bounds (bytes, lines, matches) and per-turn bounds (rounds, read_bytes, wall time)."""
    import secrets
    tree = tmp_path / "tree"
    tree.mkdir()
    for i in range(6):
        (tree / f"f{i}.py").write_text("\n".join(f"match_{i}_{line}" for line in range(10)) + "\n", encoding="utf-8")

    # 1. Per-answer bounds: lines, bytes, matches
    monkeypatch.setattr(api_review, "TOOL_MAX_LINES", 3)
    monkeypatch.setattr(api_review, "TOOL_MAX_MATCHES", 2)
    monkeypatch.setattr(api_review, "TOOL_MAX_BYTES", 80)
    served = endpoint(turns=[
        _tool_turn(
            ("read", {"path": "f0.py"}),
            ("grep", {"pattern": "match_"}),
            ("glob", {"pattern": "*.py"}),
            ("list", {"path": "."}),
        ),
    ])
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))
    code, answer = _run(tmp_path, "local/m", tree=tree)
    assert code == 0 and answer == APPROVED
    ans_tools = [m["content"] for m in served.requests[1]["body"]["messages"] if m["role"] == "tool"]
    assert "truncated at 3 lines" in ans_tools[0] or "truncated at 80 bytes" in ans_tools[0]
    assert all(len(r.encode("utf-8")) <= 80 for r in ans_tools)
    assert "truncated at 2 matches" in ans_tools[1]
    assert "truncated at 2 matches" in ans_tools[2]
    assert "truncated at 2 entries" in ans_tools[3]

    # 2. Turn bound: tool rounds
    monkeypatch.setattr(api_review, "TOOL_MAX_LINES", 200)
    monkeypatch.setattr(api_review, "TOOL_MAX_MATCHES", 100)
    monkeypatch.setattr(api_review, "TOOL_MAX_BYTES", 32_768)
    monkeypatch.setattr(api_review, "TURN_MAX_ROUNDS", 2)
    served_rounds = endpoint(turns=[
        _tool_turn(("list", {"path": "."})),
        _tool_turn(("list", {"path": "."})),
    ])
    _providers(monkeypatch, tmp_path, f"local {served_rounds.url} AO_TEST_API_KEY")
    code, answer = _run(tmp_path, "local/m", tree=tree)
    assert code == 0 and answer == APPROVED and len(served_rounds.requests) == 3
    assert "tools" in served_rounds.requests[0]["body"] and "tools" in served_rounds.requests[1]["body"]
    assert "tools" not in served_rounds.requests[2]["body"]
    assert "tool round limit (2 rounds) reached" in served_rounds.requests[2]["body"]["messages"][-1]["content"]

    # 3. Turn bound: total bytes read
    monkeypatch.setattr(api_review, "TURN_MAX_ROUNDS", 16)
    monkeypatch.setattr(api_review, "TURN_MAX_READ_BYTES", 25)
    served_bytes = endpoint(turns=[_tool_turn(("read", {"path": "f0.py"}))])
    _providers(monkeypatch, tmp_path, f"local {served_bytes.url} AO_TEST_API_KEY")
    code, answer = _run(tmp_path, "local/m", tree=tree)
    assert code == 0 and answer == APPROVED and len(served_bytes.requests) == 2
    assert "tools" not in served_bytes.requests[1]["body"]
    assert "total bytes read limit (25 bytes) reached" in served_bytes.requests[1]["body"]["messages"][-1]["content"]
    stats = json.loads((tmp_path / "answer.md.tool.json").read_text(encoding="utf-8"))
    assert stats["read_bytes"] == 25

    # 4. Turn bound: wall time
    monkeypatch.setattr(api_review, "TURN_MAX_READ_BYTES", 262_144)
    monkeypatch.setattr(api_review, "TURN_MAX_SECONDS", 0.0)
    served_time = endpoint(turns=[_tool_turn(("read", {"path": "f0.py"}))])
    _providers(monkeypatch, tmp_path, f"local {served_time.url} AO_TEST_API_KEY")
    code, answer = _run(tmp_path, "local/m", tree=tree)
    assert code == 0 and answer == APPROVED and len(served_time.requests) == 2
    assert "tools" not in served_time.requests[1]["body"]
    assert "wall-time limit" in served_time.requests[1]["body"]["messages"][-1]["content"]


def test_a_provider_that_refuses_tools_makes_the_review_unavailable_and_says_so(monkeypatch, tmp_path, endpoint, capsys):
    """API-REVIEWER-TOOLS: a provider that refuses tools makes the review UNAVAILABLE and never runs silently without them."""
    import secrets
    tree = tmp_path / "tree"
    tree.mkdir()
    refusing = endpoint(turns=[(400, {"error": {"message": "tools are not supported by this model"}})])
    _providers(monkeypatch, tmp_path, f"local {refusing.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))

    code, answer = _run(tmp_path, "local/m", tree=tree)

    said = capsys.readouterr().err
    assert code == 1 and answer is None and len(refusing.requests) == 1
    assert "local refused the review tools for m" in said


def test_a_route_opting_into_tree_gets_the_tree_and_evidence_while_a_route_without_tree_is_unchanged(
        project, monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-TOOLS: a route with `--tree {tree}` gets the unpacked candidate tree, the tree note and evidence
    fields (`rounds`, `read_bytes`), while a route without `--tree {tree}` sends one request with no tools."""
    import hashlib
    import secrets
    import subprocess
    root = project["root"]
    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
    with open(os.path.join(root, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
        fh.write('{"hooks": "withheld"}\n')
    _repo_with_change(root)

    served = endpoint(turns=[
        _tool_turn(
            ("list", {"path": "."}),
            ("read", {"path": "src/a.py"}),
            ("read", {"path": ".claude/settings.json"}),
        ),
    ])
    home = tmp_path / "home"
    (home / ".ao").mkdir(parents=True, exist_ok=True)
    (home / ".ao" / "settings.json").write_text(json.dumps(
        {"review": {"api_providers": [f"local {served.url} AO_TEST_API_KEY"]}}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PYTHONPATH", os.path.join(A.REPO, "src"))
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))
    client = cli._tool_beside_interpreter("ao-api-review")
    if client is None:
        pytest.skip("the client's script is installed with ao, and this interpreter has none")
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root_, name: (
        (client, "ao-api-review") if name in ("ao-api-review", client) else (None, None)))

    # 1. Opted-in route with --tree {tree}
    composed = A.compose_reviewer("openai-api", model="local/m", family="zhipu")
    tree_route = dict(composed, id="rv-tree", argv=composed["argv"][:-1] + ["--tree", "{tree}", composed["argv"][-1]])
    staged_diff = A.candidate_diff(root, A.index_candidate(root))
    expected_digest = "sha256:" + hashlib.sha256(staged_diff).hexdigest()

    assert cli.cmd_review(dict(project, reviewer=tree_route), _args()) == 0

    assert len(served.requests) == 2
    first_prompt = served.requests[0]["body"]["messages"][0]["content"]
    assert cli._tree_note(project, True) in first_prompt
    assert "tools" in served.requests[0]["body"]
    tool_outputs = [m["content"] for m in served.requests[1]["body"]["messages"] if m["role"] == "tool"]
    assert "src/" in tool_outputs[0] and ".claude/" not in tool_outputs[0]
    assert "x = 2" in tool_outputs[1]
    assert "refused:" in tool_outputs[2] and "withheld" in tool_outputs[2]
    row = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)[-1]
    assert row["verdict"] == "APPROVED"
    names = sorted(os.listdir(os.path.join(root, "semantic-review")))
    evidence = A.review_evidence(open(os.path.join(root, "semantic-review", names[-1]), encoding="utf-8").read())
    assert evidence["tool"]["handed"] == expected_digest
    assert evidence["tool"]["bytes"] == len(staged_diff)
    assert evidence["tool"]["rounds"] == 1 and evidence["tool"]["read_bytes"] > 0
    assert cli._tool_review_evidence({"diff_digest": expected_digest}, tree_route,
                                     {"tool": {"handed": "sha256:" + "0" * 64, "bytes": 1, "rounds": 1, "read_bytes": 5}}) \
        == "the bytes handed to the tool reviewer are not the candidate diff this review names"

    # 2. Route without --tree {tree}: one request, no tools, no tree note
    plain_served = endpoint()
    (home / ".ao" / "settings.json").write_text(json.dumps(
        {"review": {"api_providers": [f"local {plain_served.url} AO_TEST_API_KEY"]}}), encoding="utf-8")
    plain_route = dict(composed, id="rv-plain")

    assert cli.cmd_review(dict(project, reviewer=plain_route), _args()) == 0

    (only_req,) = plain_served.requests
    assert "tools" not in only_req["body"]
    assert cli._tree_note(project, True) not in only_req["body"]["messages"][0]["content"]
    names = sorted(os.listdir(os.path.join(root, "semantic-review")))
    plain_evidence = A.review_evidence(open(os.path.join(root, "semantic-review", names[-1]), encoding="utf-8").read())
    assert plain_evidence["tool"]["rounds"] == 0 and plain_evidence["tool"]["read_bytes"] == 0


def test_a_route_opting_into_tree_drops_the_tree_flag_on_probe_and_keeps_it_on_review(
        project, monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-TOOLS-2: where a command is built with no candidate tree to hand it (the reviewer probe, or an
    unpack that failed), `review.tree` (`--tree {tree}`) leaves together with `{tree}`; a review with a candidate
    fills it as before."""
    import secrets
    root = project["root"]
    _repo_with_change(root)
    client = cli._tool_beside_interpreter("ao-api-review")
    if client is None:
        pytest.skip("the client's script is installed with ao, and this interpreter has none")
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root_, name: (
        (client, "ao-api-review") if name in ("ao-api-review", client) else (None, None)))
    home = tmp_path / "home"
    (home / ".ao").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PYTHONPATH", os.path.join(A.REPO, "src"))
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))

    composed = A.compose_reviewer("openai-api", model="local/m", family="zhipu")
    tree_route = dict(composed, id="rv-tree", argv=composed["argv"][:-1] + ["--tree", "{tree}", composed["argv"][-1]])

    # 1. Reviewer probe of the route with `--tree {tree}`: no candidate tree, so `--tree {tree}` leaves and the
    # prompt is not misread as `--tree`'s argument.
    monkeypatch.setattr(cli, "_reviewer_probe_nonce", lambda: "AO-REVIEWER-PROBE-deadbeef")
    probe_served = endpoint(answer="AO-REVIEWER-PROBE-deadbeef")
    (home / ".ao" / "settings.json").write_text(json.dumps(
        {"review": {"api_providers": [f"local {probe_served.url} AO_TEST_API_KEY"]}}), encoding="utf-8")

    probe = cli._reviewer_probe(dict(project, reviewer=tree_route), timeout=30)

    assert probe["ok"] is True and probe["route"] == "rv-tree"
    (probe_req,) = probe_served.requests
    assert "tools" not in probe_req["body"]
    probe_prompt = probe_req["body"]["messages"][0]["content"]
    assert "AO-REVIEWER-PROBE-deadbeef" in probe_prompt
    assert cli._tree_note(project, True) not in probe_prompt

    # 2. A review of the same route with a candidate fills `--tree {tree}` and offers tools.
    review_served = endpoint(turns=[_tool_turn(("read", {"path": "src/a.py"}))])
    (home / ".ao" / "settings.json").write_text(json.dumps(
        {"review": {"api_providers": [f"local {review_served.url} AO_TEST_API_KEY"]}}), encoding="utf-8")

    assert cli.cmd_review(dict(project, reviewer=tree_route), _args()) == 0

    assert len(review_served.requests) == 2
    assert "tools" in review_served.requests[0]["body"]
    assert cli._tree_note(project, True) in review_served.requests[0]["body"]["messages"][0]["content"]
    tool_msgs = [m for m in review_served.requests[1]["body"]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and "x = 2" in tool_msgs[0]["content"]


def test_ao_runs_its_own_installations_client_before_path_and_doctor_names_a_mismatched_path_copy(
        project, monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-TOOLS-2: a tool that ships with ao runs from the installation of the ao that runs it first, and
    `ao doctor` names a PATH copy whose version differs from ao's own."""
    import secrets
    import sys
    root = project["root"]
    _repo_with_change(root)
    client = cli._tool_beside_interpreter("ao-api-review")
    if client is None:
        pytest.skip("the client's script is installed with ao, and this interpreter has none")

    # Put an older `ao-api-review` (0.5.2, which rejects `--tree`) first on PATH.
    stale_bin = tmp_path / "stale-bin"
    stale_bin.mkdir()
    stale_marker = tmp_path / "stale-ran"
    stale_script = stale_bin / ("ao-api-review.cmd" if os.name == "nt" else "ao-api-review")
    if os.name == "nt":
        stale_script.write_text(
            f'@echo off\nif "%1"=="--version" (\n  echo ao-api-review 0.5.2\n  exit /b 0\n)\n'
            f'echo stale > "{stale_marker}"\nexit /b 2\n',
            encoding="utf-8",
        )
    else:
        stale_script.write_text(
            f"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then\n  echo \"ao-api-review 0.5.2\"\n  exit 0\nfi\n"
            f"echo stale > {str(stale_marker)!r}\nexit 2\n",
            encoding="utf-8",
        )
        stale_script.chmod(0o755)
    monkeypatch.setenv("PATH", str(stale_bin) + os.pathsep + os.environ.get("PATH", ""))

    # 1. `ao doctor` names the mismatched PATH copy, and says nothing when its version matches ao's own.
    lines = cli._shipped_tool_lines(root)
    assert len(lines) == 1
    assert str(stale_script) in lines[0] and "0.5.2" in lines[0] and cli.__version__ in lines[0]

    matching_bin = tmp_path / "matching-bin"
    matching_bin.mkdir()
    matching_script = matching_bin / ("ao-api-review.cmd" if os.name == "nt" else "ao-api-review")
    if os.name == "nt":
        matching_script.write_text(f"@echo off\necho ao-api-review {cli.__version__}\n", encoding="utf-8")
    else:
        matching_script.write_text(f"#!/bin/sh\necho \"ao-api-review {cli.__version__}\"\n", encoding="utf-8")
        matching_script.chmod(0o755)
    monkeypatch.setenv("PATH", str(matching_bin))
    assert cli._shipped_tool_lines(root) == []

    # 2. With the stale copy first on PATH, a review still runs this ao's own installation's copy beside sys.executable.
    monkeypatch.setenv("PATH", str(stale_bin) + os.pathsep + os.environ.get("PATH", ""))
    served = endpoint(turns=[_tool_turn(("read", {"path": "src/a.py"}))])
    home = tmp_path / "home"
    (home / ".ao").mkdir(parents=True, exist_ok=True)
    (home / ".ao" / "settings.json").write_text(json.dumps(
        {"review": {"api_providers": [f"local {served.url} AO_TEST_API_KEY"]}}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PYTHONPATH", os.path.join(A.REPO, "src"))
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))
    composed = A.compose_reviewer("openai-api", model="local/m", family="zhipu")
    tree_route = dict(composed, id="rv-tree", argv=composed["argv"][:-1] + ["--tree", "{tree}", composed["argv"][-1]])

    assert cli.cmd_review(dict(project, reviewer=tree_route), _args()) == 0
    assert not stale_marker.exists()
    assert len(served.requests) == 2


def test_tool_calls_without_ids_or_with_duplicate_ids_receive_unique_matching_ids(
        monkeypatch, tmp_path, endpoint):
    """API-REVIEWER-TOOLS-2: every tool call of a round gets a non-empty id unique within the turn, written into both
    the assistant message's `tool_calls` and the tool result's `tool_call_id`."""
    import secrets
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("alpha\n", encoding="utf-8")
    (tree / "b.txt").write_text("beta\n", encoding="utf-8")
    turns = [
        # Round 1: missing id, None id, empty/whitespace id
        {"choices": [{"message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"type": "function", "function": {"name": "read", "arguments": json.dumps({"path": "a.txt"})}},
                {"id": None, "type": "function", "function": {"name": "read", "arguments": json.dumps({"path": "b.txt"})}},
                {"id": "   ", "type": "function", "function": {"name": "list", "arguments": json.dumps({"path": "."})}},
            ],
        }, "finish_reason": "tool_calls"}]},
        # Round 2: duplicate id within the round ("dup") and duplicate of a round-1 synthesized id ("call_1_1")
        {"choices": [{"message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "dup", "type": "function", "function": {"name": "read", "arguments": json.dumps({"path": "a.txt"})}},
                {"id": "dup", "type": "function", "function": {"name": "read", "arguments": json.dumps({"path": "b.txt"})}},
                {"id": "call_1_1", "type": "function", "function": {"name": "list", "arguments": json.dumps({"path": "."})}},
            ],
        }, "finish_reason": "tool_calls"}]},
    ]
    served = endpoint(turns=turns)
    _providers(monkeypatch, tmp_path, f"local {served.url} AO_TEST_API_KEY")
    monkeypatch.setenv("AO_TEST_API_KEY", secrets.token_hex(8))

    code, answer = _run(tmp_path, "local/m", tree=tree)

    assert code == 0 and answer == APPROVED
    assert len(served.requests) == 3

    # Check request 2 (sent after round 1)
    msgs_r1 = served.requests[1]["body"]["messages"]
    assistant_r1 = [m for m in msgs_r1 if m["role"] == "assistant"][0]
    tools_r1 = [m for m in msgs_r1 if m["role"] == "tool"]
    r1_call_ids = [c["id"] for c in assistant_r1["tool_calls"]]
    r1_result_ids = [m["tool_call_id"] for m in tools_r1]
    assert r1_call_ids == ["call_1_1", "call_1_2", "call_1_3"]
    assert r1_result_ids == r1_call_ids

    # Check request 3 (sent after round 2)
    msgs_r2 = served.requests[2]["body"]["messages"]
    assistant_r2 = [m for m in msgs_r2 if m["role"] == "assistant"][1]
    tools_r2 = [m for m in msgs_r2 if m["role"] == "tool"][3:]
    r2_call_ids = [c["id"] for c in assistant_r2["tool_calls"]]
    r2_result_ids = [m["tool_call_id"] for m in tools_r2]
    assert r2_call_ids == ["dup", "call_2_2", "call_2_3"]
    assert r2_result_ids == r2_call_ids
    assert len(set(r1_call_ids + r2_call_ids)) == 6
