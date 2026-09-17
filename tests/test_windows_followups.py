"""What the Windows lane would have met first, done here the way Windows does it (#9, #71).

Git for Windows checks `.ao-project` out with CRLF; the clock ticks every 15.6 ms before
Python 3.13; a file another handle holds open cannot be renamed over or deleted; a
program is C:\\...\\name.exe; TOML reads a backslash as an escape; and cmd.exe keeps
single quotes and doubled backslashes. Each test holds on every platform.
"""
import errno
import json
import os
import pathlib
import re
import subprocess
import tempfile
import time
from types import SimpleNamespace

import pytest

from ao import cli, drivers, lib as A, skillkit, storage

CRLF_MARKER = cli.PROJECT_MARKER_BYTES.replace(b"\n", b"\r\n")
CHAIN = "test-windows-followups-v1"


class _Instead:
    """A module as one module sees it, with some of its names replaced."""

    def __init__(self, module, **replaced):
        self._module, self._replaced = module, replaced

    def __getattr__(self, name):
        return self._replaced[name] if name in self._replaced else getattr(self._module, name)


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def _enrolled_clone(tmp_path):
    """A repository that committed the exact marker, cloned with Git for Windows' default."""
    source = tmp_path / "enrolled"
    source.mkdir()
    _git(source, "init", "-q")
    (source / cli.PROJECT_MARKER).write_bytes(cli.PROJECT_MARKER_BYTES)
    _git(source, "add", cli.PROJECT_MARKER)
    _git(source, "commit", "-q", "-m", "adopt ao")
    clone = tmp_path / "windows-clone"
    _git(tmp_path, "clone", "-q", "-c", "core.autocrlf=true", str(source), str(clone))
    assert (clone / cli.PROJECT_MARKER).read_bytes() == CRLF_MARKER
    return clone


def _marker_read_on(platform, monkeypatch):
    """The working-tree marker read with the bytes `platform` accepts, its file stat'ed as this machine stats it.

    The stat comparison stays this machine's: a Windows stat read as if elsewhere compared the clocks
    Windows keeps apart, and refused a marker that had not changed (#71).
    """
    real = cli._worktree_project_marker_document

    def read(root):
        with monkeypatch.context() as patch:
            patch.setattr(os, "name", platform)
            forms = cli._worktree_marker_forms()
        with monkeypatch.context() as patch:
            patch.setattr(cli, "_worktree_marker_forms", lambda: forms)
            return real(root)

    return read


def test_the_marker_git_for_windows_checks_out_is_the_marker_there_and_nowhere_else(tmp_path, monkeypatch):
    clone = _enrolled_clone(tmp_path)
    marker = clone / cli.PROJECT_MARKER

    on_windows = _marker_read_on("nt", monkeypatch)(str(clone))
    elsewhere = _marker_read_on("posix", monkeypatch)(str(clone))

    assert on_windows["problem"] is None and on_windows["fingerprint"][2] == CRLF_MARKER
    assert elsewhere["problem"] == f"{cli.PROJECT_MARKER} bytes are not ao-project-v1"
    for other in (CRLF_MARKER + b"\r\n", CRLF_MARKER + b"x", b"ao-project-v1\r\r\n", b"ao-project-v1\r"):
        marker.write_bytes(other)
        assert _marker_read_on("nt", monkeypatch)(str(clone))["problem"], other


def test_enrollment_reads_the_exact_blob_whatever_the_working_tree_holds(tmp_path, monkeypatch):
    clone = _enrolled_clone(tmp_path)
    # Even were every caller to accept the CRLF form, enrollment would not read it.
    monkeypatch.setattr(cli, "_worktree_marker_forms", lambda: (cli.PROJECT_MARKER_BYTES, CRLF_MARKER))

    assert cli._head_project_marker(str(clone))["status"] == "canonical"
    _git(clone, "add", cli.PROJECT_MARKER)                   # Git stores the checkout as the exact bytes
    assert cli._index_project_marker(str(clone))["status"] == "canonical"
    assert _git(clone, "status", "--porcelain") == ""

    typed = tmp_path / "typed-with-crlf"
    typed.mkdir()
    _git(typed, "init", "-q")
    (typed / cli.PROJECT_MARKER).write_bytes(CRLF_MARKER)
    _git(typed, "-c", "core.autocrlf=false", "add", cli.PROJECT_MARKER)
    indexed = cli._index_project_marker(str(typed))

    assert indexed == {"status": "invalid", "detail": "active index marker bytes are not ao-project-v1"}
    assert cli._project_enrollment(str(typed))["state"] == "broken"


def test_init_keeps_the_marker_a_windows_clone_holds_and_refuses_it_elsewhere(tmp_path, monkeypatch, capsys):
    clone = _enrolled_clone(tmp_path)
    monkeypatch.setattr(cli, "_reviewer_probe", lambda cfg, timeout=cli.REVIEW_PROBE_TIMEOUT: {
        "configured": True, "ok": True, "route": "fixture-reviewer", "binary": "/fixture/reviewer",
        "version": "fixture", "reason": "exact nonce echoed", "kind": "success"})
    args = SimpleNamespace(name=None, profile="claude-kiro", implementer=None, model=None, effort=None,
                           reviewer_model=None, agent=None, no_mcp=True, rules=False, watchdog=False)
    elsewhere, on_windows = _marker_read_on("posix", monkeypatch), _marker_read_on("nt", monkeypatch)

    monkeypatch.setattr(cli, "_worktree_project_marker_document", elsewhere)
    assert cli.cmd_init({"root": str(clone)}, args) == 1
    assert "bytes are not ao-project-v1" in capsys.readouterr().out
    assert not (clone / ".ao" / "config.json").exists()

    monkeypatch.setattr(cli, "_worktree_project_marker_document", on_windows)
    assert cli.cmd_init({"root": str(clone)}, args) == 0, capsys.readouterr().out

    assert (clone / ".ao" / "config.json").exists()
    assert (clone / cli.PROJECT_MARKER).read_bytes() == CRLF_MARKER     # kept as Git checked it out


def _one_tick(monkeypatch):
    """Windows' clock before Python 3.13: every reading within a tick is the same reading."""
    tick = time.time_ns()
    monkeypatch.setattr(A, "_ID_CLOCK", {})
    monkeypatch.setattr(A, "time", _Instead(time, time_ns=lambda: tick))
    return tick


def _commit_text(root, text):
    with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", text.strip())


def test_ids_minted_in_one_clock_tick_are_distinct_and_keep_their_shape(project, tmp_path, monkeypatch):
    root = project["root"]
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump({"gates": {"test": {"run": "exit 1"}}, "profiles": {"full": ["test"]}}, fh)
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    base = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _commit_text(root, "base\n")
    _git(root, "checkout", "-q", "-b", "left")
    _commit_text(root, "left\n")
    _git(root, "checkout", "-q", base)
    _commit_text(root, "right\n")                              # a merge check of left records a conflict at once
    tick = _one_tick(monkeypatch)
    millisecond = tick // 10 ** 6

    for _ in range(2):
        assert cli.cmd_merge_check(project, SimpleNamespace(branch="left", into="HEAD", profile="full", wait=0)) == 1
        A.record_notice(root, "a notice", "minted in the same tick", True)
    checks = [row["id"] for row in A.merge_checks(root)]
    notices = [row["id"] for row in reversed(A.notices(root, 10, include_suppressed=True))]
    tokens = [f"C-{A.unique_ns()}" for _ in range(2)]

    assert checks == [f"MC-{millisecond}", f"MC-{millisecond + 2}"]
    assert notices == [f"N-{millisecond + 1}", f"N-{millisecond + 3}"]
    assert all(re.fullmatch(r"(MC|N)-\d{13}", ident) for ident in checks + notices)
    assert tokens == [f"C-{tick}", f"C-{tick + 1}"]
    monkeypatch.setattr(A, "time", _Instead(time, time_ns=lambda: tick + 10 ** 9))
    assert A.unique_ms() == millisecond + 1000                  # once the clock moves on, the id follows it


def test_two_submits_in_one_tick_pin_two_reviews_and_pass_over_an_id_another_process_holds(project, monkeypatch):
    from tests.test_review_chain import _repo_with_change
    root = project["root"]
    _repo_with_change(root)
    millisecond = _one_tick(monkeypatch) // 10 ** 6
    reviews = os.path.join(root, ".ao", "reviews")
    os.makedirs(reviews, exist_ok=True)
    held = os.path.join(reviews, f"R-{millisecond}.json")
    open(held, "wb").close()                                       # claimed by a submit in another process
    spawned = []
    monkeypatch.setattr(cli, "_spawn_review_run", lambda root, rid: spawned.append(rid))
    args = SimpleNamespace(action="submit", rid=None, any=False, run=None, boundary="b", paths=None, commits=None,
                           timeout=None)

    assert cli.cmd_review(project, args) == 0 and cli.cmd_review(project, args) == 0

    assert spawned == [f"R-{millisecond + 1}", f"R-{millisecond + 2}"]
    first, second = (cli._review_state(root, rid) for rid in spawned)
    assert first["index"] != second["index"] and os.path.exists(first["index"]) and os.path.exists(second["index"])
    assert os.path.getsize(held) == 0


def _windows_rename(monkeypatch, open_handles, attempts, waits, on_wait=None):
    """storage's os.replace refused, as Windows refuses it, while another handle holds the target open."""
    real = os.replace

    def replace(source, target):
        attempts.append(os.fspath(target))
        if os.path.realpath(target) in open_handles:
            refused = PermissionError(errno.EACCES, "Access is denied", os.fspath(target))
            refused.winerror = 5
            raise refused
        return real(source, target)

    def sleep(seconds):
        waits.append(seconds)
        if on_wait:
            on_wait()

    monkeypatch.setattr(storage, "os", _Instead(os, replace=replace))
    monkeypatch.setattr(storage, "time", _Instead(time, sleep=sleep))


def test_an_append_outlasts_a_read_of_another_ledger_holding_the_checkpoint_store(tmp_path, monkeypatch):
    authority, reviews = str(tmp_path / "authority.jsonl"), str(tmp_path / "reviews.jsonl")
    storage.append_chained_jsonl(authority, {"grant": 0}, CHAIN)
    storage.append_chained_jsonl(reviews, {"review": 0}, CHAIN)
    store = storage.checkpoint_path()
    open_handles = {os.path.realpath(store): open(store, "rb")}     # the read of reviews.jsonl, not yet closed
    attempts, waits = [], []

    def the_read_finishes():
        handle = open_handles.pop(os.path.realpath(store), None)
        if handle:
            handle.close()

    _windows_rename(monkeypatch, open_handles, attempts, waits, on_wait=the_read_finishes)
    try:
        storage.append_chained_jsonl(authority, {"grant": 1}, CHAIN)
    finally:
        the_read_finishes()

    assert [row["grant"] for row in storage.read_chained_jsonl(authority, CHAIN)] == [0, 1]
    assert waits == [storage._REPLACE_FIRST_WAIT] and len(attempts) == 2
    with open(store, encoding="utf-8") as fh:
        assert json.load(fh)[os.path.realpath(authority)]["count"] == 2


def test_a_rename_windows_keeps_refusing_fails_within_a_bound_and_takes_the_row_back(tmp_path, monkeypatch):
    authority = tmp_path / "authority.jsonl"
    storage.append_chained_jsonl(str(authority), {"grant": 0}, CHAIN)
    store = pathlib.Path(storage.checkpoint_path())
    before = (authority.read_bytes(), store.read_bytes())
    attempts, waits = [], []
    _windows_rename(monkeypatch, {os.path.realpath(store)}, attempts, waits)

    with pytest.raises(PermissionError):
        storage.append_chained_jsonl(str(authority), {"grant": 1}, CHAIN)

    assert (authority.read_bytes(), store.read_bytes()) == before
    assert len(attempts) == storage._REPLACE_ATTEMPTS and sum(waits) < 2
    assert not [name for name in os.listdir(os.path.dirname(store)) if name.endswith(".tmp")]


def test_a_permission_error_that_is_not_windows_holding_the_file_is_raised_at_once(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    attempts, waits = [], []

    def refused(source, target):
        attempts.append(target)
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(storage, "os", _Instead(os, replace=refused))
    monkeypatch.setattr(storage, "time", _Instead(time, sleep=waits.append))

    with pytest.raises(PermissionError):
        storage.replace_file_durably(str(target), b"{}\n")

    assert len(attempts) == 1 and waits == [] and os.listdir(tmp_path) == []


def test_a_sibling_agent_binary_is_known_by_its_windows_path(monkeypatch):
    chat = "C:\\Users\\me\\AppData\\Local\\kiro-cli\\kiro-cli-chat.exe"
    files = {chat, "C:/tools/kiro-cli-chat.exe", "C:\\Users\\me\\kiro-cli-notes.txt"}

    def machine(platform):
        return _Instead(os, name=platform, path=_Instead(os.path, isfile=lambda path: path in files))

    monkeypatch.setattr(A, "os", machine("nt"))
    assert A._executable(chat) and A._executable("C:/tools/kiro-cli-chat.exe")
    assert A._is_agent_process(7, {"kiro-cli"}, [chat, "acp", "--agent-engine=kas"])
    assert not A._executable("C:\\Users\\me\\kiro-cli-notes.txt")          # not a program Windows runs
    assert not A._executable("kiro-cli-chat.exe")                           # a name, not a path
    assert not A._executable("C:\\Users\\me\\gone\\kiro-cli-chat.exe")      # no file there

    monkeypatch.setattr(A, "os", machine("posix"))                          # elsewhere, as before
    assert not A._is_agent_process(7, {"kiro-cli"}, [chat, "acp", "--agent-engine=kas"])


def test_the_manual_mcp_snippet_writes_a_windows_root_escaped_for_toml(project):
    root, exe = "C:\\Users\\me\\work\\ao repo", "C:\\Users\\me\\venv\\Scripts\\ao.exe"

    snippet = skillkit.register_mcp(root, {"codex"}, exe=exe)["codex"].split("\n", 1)[1]

    assert 'command = "C:\\\\Users\\\\me\\\\venv\\\\Scripts\\\\ao.exe"' in snippet
    assert 'args = ["-C", "C:\\\\Users\\\\me\\\\work\\\\ao repo", "mcp", "serve"]' in snippet
    assert skillkit._toml_string_body('a "quoted"\tname') == 'a \\"quoted\\"\\u0009name'


def test_the_escaped_snippet_is_toml_that_reads_back_the_windows_paths(project):
    tomllib = pytest.importorskip("tomllib", reason="the TOML reader ships with Python 3.11")
    root, exe = "C:\\Users\\me\\work\\ao repo", "C:\\Users\\me\\venv\\Scripts\\ao.exe"

    snippet = skillkit.register_mcp(root, {"codex"}, exe=exe)["codex"].split("\n", 1)[1]

    assert tomllib.loads(snippet)["mcp_servers"]["ao"] == {"command": exe, "args": ["-C", root, "mcp", "serve"]}


def test_a_scoped_review_diff_hands_git_its_pathspecs_without_a_shell(project, monkeypatch):
    root = project["root"]
    scoped = os.path.join(root, "it's scoped")
    os.makedirs(scoped)
    with open(os.path.join(scoped, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    with open(os.path.join(scoped, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 2\n")
    monkeypatch.setattr(A, "sh", lambda cmd, **kwargs: pytest.fail(f"a shell was started: {cmd}"))

    diff, included = A.review_diff(root, project, paths=["it's scoped"])

    assert "x = 2" in diff and included == []


def test_the_probe_answers_when_a_timed_out_hook_still_holds_its_index(project, tmp_path, monkeypatch):
    root = project["root"]
    inventory = {"error": None, "root": root, "top": root, "targets": [
        {"role": "pre-commit", "active": True, "static_state": "current-local (behavior unverified)",
         "track_state": "untracked"}]}
    real_git, real_unlink = cli._hook_git, os.unlink

    def git(cwd, *args, **kwargs):
        if args[:2] == ("hook", "run"):
            raise cli._HookResolutionError("git query failed: timed out after 15 seconds")
        return real_git(cwd, *args, **kwargs)

    def unlink(path, *args, **kwargs):
        # A child of the timed-out hook still has the index open, and Windows will not delete it.
        if os.path.basename(os.fsdecode(path)) == "index":
            raise PermissionError(errno.EACCES, "The process cannot access the file", os.fsdecode(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cli, "_hook_git", git)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(os, "unlink", unlink)

    proof = cli._hook_execution_probe(inventory)

    assert proof == {"installed": False, "state": "not installed", "exit": None,
                     "detail": "Git could not execute the pre-commit probe: git query failed: timed out after 15 seconds"}
    [left] = [name for name in os.listdir(tmp_path) if name.startswith("ao-hook-probe-")]
    assert os.listdir(tmp_path / left) == ["index"]


def test_the_token_store_is_queried_without_a_shell_whatever_its_path_holds(monkeypatch):
    db = "C:\\Users\\me\\AppData\\Roaming\\agent cli\\data.sqlite3"
    api = {"token": {"sqlite": db, "table": "auth_kv", "key": "agent:token", "field": "access_token"},
           "profile": {"argv": ["agent", "whoami"], "prefix": "arn:agent"}}
    ran = []

    def run(argv, **kwargs):
        ran.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stderr="",
                               stdout=json.dumps({"access_token": "t", "expires_at": time.time() + 60}) + "\n")

    monkeypatch.setattr(drivers, "os", _Instead(os, path=_Instead(os.path, exists=lambda path: path == db)))
    monkeypatch.setattr(drivers, "subprocess", _Instead(subprocess, run=run))
    monkeypatch.setattr(A, "sh", lambda *args, **kwargs: pytest.fail("the token store was read through a shell"))
    monkeypatch.setattr(A, "binary_candidates", lambda name, path=None: [])

    assert drivers.usage_limits(api) == {"error": "agent is not on PATH or in the usual install directories"}
    [(argv, kwargs)] = ran
    assert argv == ["sqlite3", db, "SELECT value FROM auth_kv WHERE key='agent:token';"]
    assert not kwargs.get("shell")
