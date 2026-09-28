"""What a default Windows install does to text, done here the way it does it (WINDOWS-CLOSE, #71).

A Python a person installs on Windows runs without UTF-8 mode, and it reads and writes a pipe in the
ANSI code page. Every lane of the hosted suite runs with PYTHONUTF8=1, so none of them met it: an arrow,
a box line or a Turkish letter ao printed to a pipe ended the command, and the MCP server with it, and a
body piped in as UTF-8 was read as the code page's characters. PYTHONIOENCODING=cp1252 gives an
interpreter on any platform the streams of a Western Windows install, so each test here holds everywhere.
"""
import ast
import io
import json
import os
import pathlib
import subprocess
import sys

from ao import lib as A

ROOT = pathlib.Path(__file__).resolve().parent.parent
TITLE = "İşçi → ılık çay"          # İ, ş and ı are outside cp1252, and → is outside the Turkish cp1254 as well


def _code_page_env(home):
    """The environment of an interpreter a default Western Windows install starts, with this ao importable."""
    return A.self_child_env(dict(os.environ, HOME=home, USERPROFILE=home, PYTHONIOENCODING="cp1252",
                                 PYTHONUTF8="0"))


def _ao(root, *args, stdin=b""):
    return subprocess.run([sys.executable, "-m", "ao", "-C", root, *args], input=stdin, capture_output=True,
                          env=_code_page_env(A.HOME), timeout=120)


def _board(root, line):
    with open(os.path.join(root, ".ao", "board.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Board\n\n## running\n\n## blocked\n\n## queued\n\n{line}\n\n## verified\n\n## done\n")


def test_a_command_prints_what_the_code_page_lacks_to_a_pipe_as_utf8(project):
    root = project["root"]
    _board(root, f"- [S1] {TITLE}")

    done = _ao(root, "board")

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert TITLE in done.stdout.decode("utf-8")


def test_a_body_piped_in_as_utf8_is_read_as_utf8_whatever_the_code_page(project):
    root = project["root"]
    body = "gövde: ılık → sıcak\n"

    done = _ao(root, "note", "a note from a pipe", "--stdin", stdin=body.encode("utf-8"))

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    [written] = [name for name in os.listdir(os.path.join(root, "agent-mail")) if name.endswith(".md")]
    with open(os.path.join(root, "agent-mail", written), encoding="utf-8") as fh:
        assert body.strip() in fh.read()


def _request(ident, name, arguments=None, ascii_only=False):
    """A tools/call as a client writes it: one line of UTF-8 JSON, escaped to ASCII where it must be."""
    call = {"jsonrpc": "2.0", "id": ident, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}
    return json.dumps(call, ensure_ascii=ascii_only).encode("utf-8") + b"\n"


def _replies(done):
    """{id: the tool's answer} from a server that ended cleanly and wrote nothing but seven-bit JSON."""
    assert done.returncode == 0 and not done.stderr, done.stderr.decode("utf-8", "replace")
    # Seven-bit on the wire: no code page between the server and its client can change a byte of it.
    assert done.stdout.isascii()
    replies = {}
    for reply in map(json.loads, done.stdout.decode("ascii").splitlines()):
        result = reply["result"]
        replies[reply["id"]] = json.loads(result["content"][0]["text"]) if "content" in result else result
    return replies


def test_the_mcp_server_reads_utf8_and_answers_in_ascii_json_whatever_the_code_page(project):
    root = project["root"]
    _board(root, f"- [S1] {TITLE}")
    # A lone surrogate reaches a server only as a JSON escape; UTF-8 mode's surrogateescape carries
    # U+DC80 to U+DCFF and no other, so a high one is what a UTF-8 stream alone cannot write.
    unknown, lone = "ao_ılık→", "\ud800"

    # Started for a role, as `ao init` registers one, so its standard error holds no notice (MCP-ROLES); the
    # architect's is every tool.
    replies = _replies(_ao(root, "mcp", "serve", "--role", "architect",
                           stdin=b'{"jsonrpc": "2.0", "id": 1, "method": "initialize"}\n'
                           + _request(2, "ao_board") + _request(3, unknown)
                           + _request(4, lone, ascii_only=True)))

    assert replies[1]["serverInfo"]["name"] == "agent-orchestrator"
    assert [item["title"] for item in replies[2]["states"]["queued"]] == [TITLE]
    assert replies[3] == {"error": f"unknown tool {unknown}"} and replies[4] == {"error": f"unknown tool {lone}"}


def test_the_a2a_bridge_reads_utf8_and_answers_in_ascii_json_as_well(project):
    agent = "ılık→"

    replies = _replies(_ao(project["root"], "a2a-mcp", "serve", stdin=_request(1, "a2a_task", {"agent": agent})))

    assert replies[1] == {"error": f"unknown agent {agent!r}", "configured": []}


def _stream(encoding, data=b""):
    return io.TextIOWrapper(io.BytesIO(data), encoding=encoding)


def test_a_stream_outside_utf8_is_set_as_utf8_mode_sets_it(monkeypatch):
    stdin, stdout, stderr = _stream("cp1252"), _stream("cp1252"), _stream("cp1252")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    A.utf8_streams()

    assert [(stream.encoding, stream.errors) for stream in (stdin, stdout, stderr)] == [
        ("utf-8", "surrogateescape"), ("utf-8", "surrogateescape"), ("utf-8", "backslashreplace")]


def test_a_stream_in_utf8_one_already_read_and_one_without_an_encoding_keep_what_they_had(monkeypatch):
    read, utf8, replaced = _stream("cp1252", b"first\nsecond\n"), _stream("UTF-8"), io.StringIO()
    read.readline()                                   # a stream read from can no longer change its encoding
    monkeypatch.setattr(sys, "stdin", read)
    monkeypatch.setattr(sys, "stdout", utf8)
    monkeypatch.setattr(sys, "stderr", replaced)

    A.utf8_streams()

    assert (read.encoding, read.readline()) == ("cp1252", "second\n")
    assert (utf8.encoding, utf8.errors) == ("UTF-8", "strict")
    assert replaced.encoding is None


class _Unasked:
    """What stands in for a stream in some hosts: asking it for its encoding raises."""

    @property
    def encoding(self):
        raise AssertionError("there is no stream behind this one")


def test_no_stream_or_one_that_raises_for_its_encoding_stops_nothing(monkeypatch):
    unasked = _Unasked()
    monkeypatch.setattr(sys, "stdin", None)                   # pythonw starts with none
    monkeypatch.setattr(sys, "stdout", unasked)
    monkeypatch.setattr(sys, "stderr", unasked)

    A.utf8_streams()

    assert (sys.stdin, sys.stdout, sys.stderr) == (None, unasked, unasked)


def _first_statements_of_main():
    """{module: its main()'s first statement} for each module under src/ao that has a main()."""
    found = {}
    for path in sorted((ROOT / "src" / "ao").rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                body = [statement for statement in node.body if not isinstance(statement, ast.Global)
                        and not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))]
                found[path.relative_to(ROOT).as_posix()] = ast.unparse(body[0])
    return found


def test_every_entry_point_sets_its_streams_before_it_reads_or_prints_anything():
    firsts = _first_statements_of_main()

    assert sorted(firsts) == ["src/ao/a2a.py", "src/ao/a2a_mcp.py", "src/ao/cli.py", "src/ao/mcp.py",
                              "src/ao/telegram.py", "src/ao/watchdog.py"]
    assert set(firsts.values()) == {"A.utf8_streams()"}


# ---- text is read and written with an explicit encoding everywhere (#71) ----------------------------

OPENERS = {(None, "open"), ("io", "open"), ("os", "fdopen")}
RUNNERS = {"run", "Popen", "check_output", "check_call", "call"}


def _called(func):
    """(owner, name) of what a call calls: (None, "open") for open(), ("os", "fdopen") for os.fdopen()."""
    if isinstance(func, ast.Name):
        return None, func.id
    if isinstance(func, ast.Attribute):
        return (func.value.id if isinstance(func.value, ast.Name) else None), func.attr
    return None, None


def _text_without_an_encoding(call):
    """The text stream a call opens, or reads from a program, without naming its encoding; None for any other."""
    owner, name = _called(call.func)
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    if "encoding" in keywords or None in keywords:          # named, or perhaps named in a **mapping
        return None
    if (owner, name) in OPENERS:
        mode = call.args[1] if len(call.args) > 1 else keywords.get("mode")
        binary = isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value
        return None if binary or len(call.args) > 3 else f"{owner + '.' if owner else ''}{name}()"
    if name in ("read_text", "write_text"):
        return None if len(call.args) > (0 if name == "read_text" else 1) else f"{name}()"
    if owner == "subprocess" and name in RUNNERS:
        text = [keywords[key] for key in ("text", "universal_newlines") if key in keywords]
        return f"subprocess.{name}(text=True)" if any(not (isinstance(value, ast.Constant) and not value.value)
                                                      for value in text) else None
    return None


def _unnamed_encodings(path, rel):
    calls = [node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path)))
             if isinstance(node, ast.Call)]
    return sorted((rel, call.lineno, what) for call in calls for what in [_text_without_an_encoding(call)] if what)


def test_every_text_stream_ao_opens_or_reads_from_a_program_names_its_encoding():
    found = [hit for path in sorted((ROOT / "src" / "ao").rglob("*.py"))
             for hit in _unnamed_encodings(path, path.relative_to(ROOT).as_posix())]

    assert found == []


def test_the_encoding_guard_sees_each_way_text_is_opened_without_one(tmp_path):
    module = tmp_path / "module.py"
    module.write_text(
        "import io, os, subprocess\n"
        "open('a')\n"
        "open('a', 'w')\n"
        "open('a', 'rb')\n"
        "open('a', encoding='utf-8')\n"
        "io.open('a', mode='a')\n"
        "os.fdopen(3, 'w')\n"
        "os.open('a', os.O_RDONLY)\n"
        "path.read_text()\n"
        "path.write_text('x', 'utf-8')\n"
        "subprocess.run(['x'], text=True)\n"
        "subprocess.run(['x'], text=False)\n"
        "subprocess.check_output(['x'], universal_newlines=True, encoding='utf-8')\n",
        encoding="utf-8")

    assert _unnamed_encodings(module, "module.py") == [
        ("module.py", 2, "open()"), ("module.py", 3, "open()"), ("module.py", 6, "io.open()"),
        ("module.py", 7, "os.fdopen()"), ("module.py", 9, "read_text()"), ("module.py", 11, "subprocess.run(text=True)")]
