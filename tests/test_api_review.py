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

    def __init__(self, answer=APPROVED, status=200, location=None, reply=None):
        self.requests, endpoint = [], self

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
                body = reply if reply is not None else {"choices": [{"message": {"role": "assistant", "content": answer}}]}
                encoded = json.dumps(body).encode()
                self.send_response(status)
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


def _run(tmp_path, model, prompt="review this"):
    diff, out = tmp_path / "candidate.diff", tmp_path / "answer.md"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    code = api_review.main(["--model", model, "--diff-file", str(diff), "--output", str(out), prompt])
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
    (home / ".ao").mkdir(parents=True)
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
    (home / ".ao").mkdir(parents=True)
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
