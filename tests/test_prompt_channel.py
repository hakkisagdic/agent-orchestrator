"""A prompt too long for one argument reaches its CLI on standard input or in a file (PROMPT-CHANNEL).

A review prompt carries a diff of up to 400 KB and its context, and it travelled as one argument.
Linux refuses one argument over 131,072 bytes, and landed ranges already reached 175 KB: the reviewer
could not start, and the spawn error read as a reviewer that was unavailable. An adapter declares how
else its CLI takes a prompt. Past the bound ao hands the prompt over that way, byte for byte, from a
private file it removes when the process ends; with nothing declared it starts nothing and says why,
as a configuration error that catch-up leaves open, never as an unavailable reviewer or a verdict.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from ao import cli, language, lib as A, procs, watchdog as W
from tests.scenarios import World

APPROVED = "print('VERDICT: APPROVED\\nBLOCKER: 0\\nHIGH: 0\\nMEDIUM: 0\\nLOW: 0')"
# Past Linux's bound on one argument in bytes, not in characters, holding what a shell or a template would read.
LARGE = "ş" * 70_000 + "\nquotes ' \" ` $(true) and {prompt} and {prompt_file}\n"
# Records what it was handed - standard input, the file its --prompt-file names, its arguments - then answers.
FAKE_CLI = """import json, os, stat, sys
record = sys.argv[1]
seen = {"argv": sys.argv[2:]}
if "--prompt-file" in sys.argv:
    path = sys.argv[sys.argv.index("--prompt-file") + 1]
    with open(path, "rb") as fh:
        handed = fh.read()
    seen.update(path=path, mode=stat.S_IMODE(os.stat(path).st_mode),
                directory=stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode))
elif sys.argv[-1] == "--from-stdin":
    handed = sys.stdin.buffer.read()
else:
    handed = sys.argv[-1].encode("utf-8")
with open(record, "wb") as fh:
    fh.write(handed)
with open(record + ".json", "w", encoding="utf-8") as fh:
    json.dump(seen, fh)
""" + APPROVED + "\n"


def _linux(monkeypatch):
    """Run as Linux would bound one argument. The process backend is chosen first, for the real platform."""
    procs.native()
    monkeypatch.setattr(cli.sys, "platform", "linux")


def _declare(monkeypatch, ident, **blocks):
    """Ship one more adapter, with these blocks, beside the package's own."""
    shipped = A.package_adapters()
    monkeypatch.setattr(A, "package_adapters", lambda: dict(shipped, **{ident: dict(blocks, id=ident)}))


def _route(tmp_path, ident, adapter, *extra):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    record = tmp_path / f"{ident}.received"
    return {"id": ident, "family": "review-family", "adapter": adapter,
            "argv": [sys.executable, str(script), str(record), *extra, "{prompt}"]}, record


def _seen(record):
    return json.loads(open(str(record) + ".json", encoding="utf-8").read())


def _private_temp(monkeypatch, tmp_path):
    """Every temporary directory ao makes in this test lands here, outside the project."""
    private = tmp_path / "temporary"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    return private


def _no_process(*args, **kwargs):
    raise AssertionError("a reviewer was started")


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _large_range(root):
    """A landed range whose review prompt outgrows one argument: (base, end)."""
    base = _git(root, "rev-parse", "HEAD")
    with open(os.path.join(root, "large.txt"), "w", encoding="utf-8") as fh:
        fh.write("değer = 'bir satır'\n" * 7_000)
    _git(root, "add", "large.txt")
    _git(root, "commit", "-q", "-m", "feat: a large change")
    return base, _git(root, "rev-parse", "HEAD")


def test_a_prompt_past_one_argument_reaches_a_declared_stdin_byte_for_byte(project, monkeypatch, tmp_path):
    _linux(monkeypatch)
    private = _private_temp(monkeypatch, tmp_path)
    _declare(monkeypatch, "piped", send={"argv": ["piped", "{prompt}"],
                                         "stdin": {"replaces": ["{prompt}"], "with": ["--from-stdin"]}})
    route, record = _route(tmp_path, "r1", "piped")

    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, LARGE, 60, False, route)

    assert attempt["ok"] is True, attempt
    assert record.read_bytes() == LARGE.encode("utf-8")
    assert _seen(record)["argv"] == ["--from-stdin"]
    assert os.listdir(private) == []


def test_a_prompt_past_one_argument_reaches_a_declared_file_that_is_private_and_gone_afterwards(
        project, monkeypatch, tmp_path):
    _linux(monkeypatch)
    private = _private_temp(monkeypatch, tmp_path)
    _declare(monkeypatch, "filed", send={"argv": ["filed", "{prompt}"], "file": {
        "replaces": ["{prompt}"], "with": ["--prompt-file", "{prompt_file}"]}})
    route, record = _route(tmp_path, "r1", "filed")

    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, LARGE, 60, False, route)

    assert attempt["ok"] is True, attempt
    seen = _seen(record)
    assert record.read_bytes() == LARGE.encode("utf-8")
    assert seen["argv"] == ["--prompt-file", seen["path"]]
    assert not os.path.realpath(seen["path"]).startswith(os.path.realpath(project["root"]) + os.sep)
    if os.name != "nt":
        assert (seen["mode"], seen["directory"]) == (0o600, 0o700)
    assert not os.path.exists(seen["path"]) and os.listdir(private) == []


def test_a_prompt_that_fits_one_argument_is_handed_over_as_before(project, monkeypatch, tmp_path):
    private = _private_temp(monkeypatch, tmp_path)
    _declare(monkeypatch, "piped", send={"argv": ["piped", "{prompt}"],
                                         "stdin": {"replaces": ["{prompt}"], "with": ["--from-stdin"]}})
    route, record = _route(tmp_path, "r1", "piped")
    small = "a small prompt: quotes ' \" and a {prompt_file}"

    plan, refused = A.prompt_plan(route["argv"], small, "piped")
    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, small, 60, False, route)

    assert refused is None and plan["channel"] == "argument" and plan["argv"] == route["argv"]
    assert A.prompt_input(plan, project["root"], ["cli", small]) == (
        {"argv": ["cli", small], "stdin": None, "directory": None}, None)
    assert attempt["ok"] is True, attempt
    assert record.read_bytes() == small.encode("utf-8") and _seen(record)["argv"] == [small]
    assert os.listdir(private) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows bounds the command line, not one argument")
def test_the_linux_bound_on_one_argument_applies_when_the_platform_is_linux(monkeypatch):
    _linux(monkeypatch)

    assert A.argument_overflow(["cli", "a" * (A.LINUX_ARGUMENT_BYTES - 1)], env={}) is None
    assert A.argument_overflow(["cli", "a" * A.LINUX_ARGUMENT_BYTES], env={}) == \
        "one argument on linux carries at most 131071 bytes"
    assert A.argument_overflow(["cli", "ş" * (A.LINUX_ARGUMENT_BYTES // 2)], env={}) is not None

    # Elsewhere the arguments and the environment share one bound, with room kept free.
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(A.os, "sysconf", lambda name: 1_048_576)
    assert A.argument_overflow(["cli", "a" * A.LINUX_ARGUMENT_BYTES], env={}) is None
    assert A.argument_overflow(["cli", "a" * 1_000_000], env={}) == (
        "arguments and environment on darwin carry at most 1048576 bytes together; this command takes 1000021, "
        "and 65536 are kept free")


def test_a_review_whose_prompt_no_route_can_take_is_refused_as_configuration_before_anything_starts(
        project, monkeypatch, capsys):
    _linux(monkeypatch)
    monkeypatch.setattr(cli, "_run_reviewer", _no_process)
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", _no_process)
    _declare(monkeypatch, "bare", send={"argv": ["bare", "{prompt}"]})
    root = project["root"]
    base, end = _large_range(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "review-family", "adapter": "bare",
                                  "argv": [sys.executable, "-c", APPROVED, "{prompt}"]})

    assert cli.cmd_review(cfg, SimpleNamespace(boundary="b", paths=None, commits=f"{base}..{end}")) == 2

    out = capsys.readouterr().out
    assert "CONFIGURATION ERROR" in out and "none was started" in out
    reason = next(line for line in out.splitlines() if "r1: " in line)
    assert "the prompt is " in reason and "adapter bare declares no other channel for `send`" in reason
    if os.name != "nt":
        assert " bytes, and one argument on linux carries at most 131071 bytes; " in reason
    assert os.listdir(os.path.join(root, "semantic-review")) == []
    assert not A.reviewer_state(root).get("pending_review")
    assert not [row for row in A.notices(root, limit=20, include_suppressed=True)
                if row.get("key") == "review-unavailable"]


def test_catch_up_keeps_a_waiver_open_when_no_reviewer_can_be_handed_its_range(project, monkeypatch, capsys):
    monkeypatch.setattr(W, "run", lambda ns: 0)
    _linux(monkeypatch)
    monkeypatch.setattr(cli, "_run_reviewer", _no_process)
    _declare(monkeypatch, "bare", send={"argv": ["bare", "{prompt}"]})
    root = project["root"]
    waiver = {"event": "waived", "id": "W-legacy-L1", "gate": "review", "slice": "L1",
              "why": "the implementer is out of credits", "by": "A. Person", "at": int(time.time()),
              "head": _git(root, "rev-parse", "HEAD"), "tree": "t"}
    with open(A.waivers_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(waiver) + "\n")
    _large_range(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "review-family", "adapter": "bare",
                                  "argv": [sys.executable, "-c", APPROVED, "{prompt}"]})

    assert cli.cmd_catchup(cfg, SimpleNamespace(boundary=None, plan=False, limit=None, slice=None,
                                                author_family="writer-family", by="A. Person")) == 3

    out = capsys.readouterr().out
    assert "adapter bare declares no other channel" in out
    assert "reviewer configuration invalid, or the range cannot be reviewed; W-legacy-L1 stays open" in out
    assert [w["id"] for w in A.open_waivers(root)] == ["W-legacy-L1"]
    assert os.listdir(os.path.join(root, "semantic-review")) == []
    assert not A.reviewer_state(root).get("pending_review")


def test_a_review_past_one_argument_is_recorded_when_its_reviewer_takes_the_prompt_on_stdin(
        project, monkeypatch, tmp_path):
    _linux(monkeypatch)
    _declare(monkeypatch, "piped", send={"argv": ["piped", "{prompt}"],
                                         "stdin": {"replaces": ["{prompt}"], "with": ["--from-stdin"]}})
    root = project["root"]
    base, end = _large_range(root)
    route, record = _route(tmp_path, "r1", "piped")

    assert cli.cmd_review(dict(project, reviewer=route),
                          SimpleNamespace(boundary="b", paths=None, commits=f"{base}..{end}")) == 0

    handed = record.read_bytes()
    assert len(handed) > A.LINUX_ARGUMENT_BYTES and _seen(record)["argv"] == ["--from-stdin"]
    assert (language.text(project, "prompt.review-candidate") + "\n").encode("utf-8") in handed
    assert "değer = 'bir satır'".encode("utf-8") in handed
    assert [verdict for _, verdict in A.reviews(root, "semantic-review")] == ["APPROVED"]


def test_a_route_that_cannot_take_the_prompt_is_refused_and_the_chain_walks_on(project, monkeypatch, tmp_path):
    _linux(monkeypatch)
    _declare(monkeypatch, "bare", send={"argv": ["bare", "{prompt}"]})
    _declare(monkeypatch, "piped", send={"argv": ["piped", "{prompt}"],
                                         "stdin": {"replaces": ["{prompt}"], "with": ["--from-stdin"]}})
    primary = {"id": "r1", "adapter": "bare", "argv": [sys.executable, "-c", "raise SystemExit(9)", "{prompt}"]}
    fallback, record = _route(tmp_path, "r2", "piped")

    result = cli._invoke_reviewer_chain(project["root"], [primary, fallback], LARGE, 60, False, primary=primary)

    assert result["used"] is fallback and record.read_bytes() == LARGE.encode("utf-8")
    assert result["failures"][0]["kind"] == "configuration-error"
    assert result["failures"][0]["retryable"] is False
    assert "adapter bare declares no other channel for `send`" in result["failures"][0]["reason"]


def test_a_capability_matrix_route_is_handed_its_prompt_file_as_any_route_is(project, monkeypatch, tmp_path):
    _linux(monkeypatch)
    _declare(monkeypatch, "filed", send={"argv": ["filed", "{prompt}"], "file": {
        "replaces": ["{prompt}"], "with": ["--prompt-file", "{prompt_file}"]}})
    route, record = _route(tmp_path, "bound", None)
    bound = {"index": 0, "eligible": True, "argv": route["argv"] + ["{model}"],
             "identity": {"binding": "binding.primary", "adapter": "filed", "model_argument": "review-runtime-v1"}}

    label, _, _, attempt = cli._reviewer_route_invocation(project["root"], bound, LARGE, 60, True, None)

    assert (label, attempt["ok"]) == ("binding.primary", True), attempt
    seen = _seen(record)
    assert record.read_bytes() == LARGE.encode("utf-8")
    assert seen["argv"] == ["--prompt-file", seen["path"], "review-runtime-v1"] and not os.path.exists(seen["path"])


def test_a_hunt_whose_prompt_no_channel_carries_is_refused_and_recorded(project, monkeypatch, capsys):
    _linux(monkeypatch)
    monkeypatch.setattr(cli, "_run_reviewer", _no_process)
    root = project["root"]
    _large_range(root)
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-c", "print()", "{prompt}"],
                                "bytes_per_run": 400_000})

    assert cli.cmd_hunt(cfg, SimpleNamespace(action="run", fingerprint=None)) == 2

    assert "no adapter ao ships runs this command" in capsys.readouterr().out
    (run,) = [row for row in A.hunter_rows(root) if row.get("event") == "run"]
    assert run["ok"] is False and "no adapter ao ships runs this command" in run["reason"]
    assert [name for name in A.mailbox(root, "agent-mail") if "-hunter-to-" in name] == []


def test_a_detached_turn_takes_its_prompt_on_standard_input_and_never_a_file(project, monkeypatch):
    _linux(monkeypatch)
    template = ["both", "--session", "{session}", "{prompt}"]
    _declare(monkeypatch, "both", resume={"argv": template,
                                          "stdin": {"replaces": ["{prompt}"], "with": []},
                                          "file": {"replaces": ["{prompt}"], "with": ["-f", "{prompt_file}"]}})
    _declare(monkeypatch, "filed", resume={"argv": template,
                                           "file": {"replaces": ["{prompt}"], "with": ["-f", "{prompt_file}"]}})

    plan, _ = A.prompt_plan(template, LARGE, "both", detached=True)
    given, refused = A.prompt_input(plan, project["root"], ["both", "--session", "s1"])
    try:
        assert refused is None and given["argv"] == ["both", "--session", "s1"]
        assert given["stdin"].read() == LARGE.encode("utf-8")
        if os.name != "nt":
            assert given["directory"] is None          # nothing named is left once it is handed over
    finally:
        A.release_prompt(given)
    assert A.prompt_plan(template, LARGE, "filed")[0]["channel"] == "file"
    _, why = A.prompt_plan(template, LARGE, "filed", detached=True)
    assert why.endswith("adapter filed declares only a prompt file for `resume`, which is removed when its "
                        "process ends, and ao does not wait on a detached turn")


def test_a_nudge_whose_prompt_no_channel_carries_starts_nothing(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    _linux(monkeypatch)
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)

    W.run(SimpleNamespace(root=world.root, idle_minutes=6.0, dry_run=True, prompt="devam " + LARGE))

    assert "the nudge's prompt cannot be handed over" in world.verdict
    assert "adapter kiro declares no other channel for `resume`" in world.verdict
    assert not [line for line in W._TRACE if line.startswith(("DRY RUN", "idle "))]


def test_every_channel_a_shipped_adapter_declares_replaces_arguments_its_own_command_carries():
    shipped = A.package_adapters()
    declaring = {ident for ident, adapter in shipped.items()
                 if any(isinstance((adapter.get(capability) or {}).get(channel), dict)
                        for capability in ("send", "resume") for channel in A.PROMPT_CHANNELS)}

    assert {ident: A.prompt_channel_problems(adapter) for ident, adapter in shipped.items()
            if A.prompt_channel_problems(adapter)} == {}
    # Each was verified from the CLI's documentation or its --help; every other adapter takes its prompt in argv.
    assert declaring == {"aider", "amp", "claude-code", "codex", "command-code", "copilot", "droid", "gemini",
                         "hermes", "omp", "qwen"}
    assert all(str((adapter.get(capability) or {}).get(channel, {}).get("note") or "").strip()
               for adapter in shipped.values() for capability in ("send", "resume") for channel in A.PROMPT_CHANNELS
               if isinstance((adapter.get(capability) or {}).get(channel), dict))
    broken = {"id": "broken", "send": {"argv": ["broken", "-p", "{prompt}"],
                                       "stdin": {"replaces": ["--prompt", "{prompt}"], "with": ["{prompt_file}"]},
                                       "file": {"replaces": ["-p"], "with": ["-f"]}}}
    assert A.prompt_channel_problems(broken) == [
        "`send.stdin.replaces` must occur exactly once in `send.argv`",
        "`send.stdin.with` must carry {prompt_file} nowhere: the prompt is on standard input",
        "`send.file.replaces` must list the arguments that carry {prompt}, one of them holding it",
        "`send.file.with` must carry {prompt_file} exactly once",
    ]
    assert "`send.file.with` must carry {prompt_file} exactly once" in A.validate_adapter(broken)
