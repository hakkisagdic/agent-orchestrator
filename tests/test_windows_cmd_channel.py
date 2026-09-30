"""A batch program on Windows is handed its prompt on standard input, or it is not started (WINDOWS-CMD-CHANNEL).

An agent CLI installed with npm on Windows is a `.cmd` file, which runs through cmd.exe, and cmd.exe reads
its arguments as its own syntax: it ends the command at a line break, replaces `%NAME%`, and a double quote
in the text changes what `&` and `|` mean. The watchdog handed the implementer and the architect their
prompts as an argument, and the architect's wake runs to several paragraphs: through a `.cmd` it would have
read the first. A batch program is now handed its prompt on standard input whatever its size, where its
adapter declares standard input; where the adapter declares none nothing starts, and the reason names
Windows and cmd.exe. Windows is what `os.name` says, faked here while a prompt is handed over and only
then, so every platform runs these.
"""
import os

import pytest

from ao import language, lib as A, watchdog as W
from tests.scenarios import World
from tests.test_prompt_channel import LARGE, _linux, _private_temp

NPM = "D:\\ao-home\\npm\\"      # where npm puts its CLIs; no person's home, which tracked-file hygiene refuses
# Short, and holding what cmd.exe reads as its own: a line break, a variable, quotes, `&`, `|` and `<`.
SHORT = 'the first line\nthe second: %PATH% "quoted" & echo | more < nul'


def _on_windows(monkeypatch, call, *args, **kwargs):
    """`call` as it runs on Windows: os.name says so while it runs, and only then."""
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        return call(*args, **kwargs)


def _rendered(ident, prompt, program, detached=True):
    """(plan, argv): the plan for adapter `ident`'s resume, and its command rendered as the watchdog renders one,
    `program` in place of the command's name."""
    plan, refused = A.prompt_plan(A.package_adapters()[ident]["resume"]["argv"], prompt, ident, detached=detached)
    assert refused is None, refused
    argv = [part.replace("{session}", "s1").replace("{prompt}", prompt) for part in plan["argv"]]
    return plan, [program] + argv[1:]


def test_a_batch_program_is_handed_even_a_short_prompt_on_standard_input(monkeypatch, tmp_path):
    private = _private_temp(monkeypatch, tmp_path)
    plan, argv = _rendered("claude-code", SHORT, NPM + "claude.CMD")
    argv += ["--model", "opus"]                         # what the watchdog appends after the template stays
    at = plan["argv"].index("{prompt}")

    given, why = _on_windows(monkeypatch, A.prompt_input, plan, str(tmp_path / "repository"), argv)

    assert plan["channel"] == "argument" and why is None    # it fits one argument, and is not handed over in one
    try:
        assert argv[at] == SHORT and given["argv"] == argv[:at] + argv[at + 1:]
        assert given["argv"][:4] == [NPM + "claude.CMD", "--resume", "s1", "-p"]
        assert given["stdin"].read() == SHORT.encode("utf-8")
    finally:
        A.release_prompt(given)
    assert os.listdir(private) == []


@pytest.mark.parametrize("ident, prompt, detached, channel", [
    ("kiro", SHORT, True, "argument"),        # its CLI takes a prompt in an argument alone
    ("droid", LARGE, False, "file"),          # a prompt file is named by its path, which is an argument too
], ids=["no-other-channel", "a-prompt-file"])
def test_a_batch_program_whose_adapter_declares_no_standard_input_is_refused_naming_windows_and_cmd(
        monkeypatch, tmp_path, ident, prompt, detached, channel):
    _linux(monkeypatch)
    private = _private_temp(monkeypatch, tmp_path)
    plan, argv = _rendered(ident, prompt, f"{NPM}{ident}.BAT", detached)

    given, why = _on_windows(monkeypatch, A.prompt_input, plan, str(tmp_path / "repository"), argv)

    assert plan["channel"] == channel and given is None
    assert "Windows" in why and "cmd.exe" in why
    assert why.endswith(f"adapter {ident} declares no standard input for `resume`")
    assert os.listdir(private) == []                    # refused before anything was written


@pytest.mark.parametrize("ident, program, name", [
    ("claude-code", NPM + "claude.exe", "nt"),                # a program Windows starts itself
    ("kiro", NPM + "kiro-cli.exe", "nt"),                     # whatever its adapter declares
    ("claude-code", "/usr/local/bin/claude.cmd", "posix"),    # the suffix means nothing where cmd.exe runs nothing
], ids=["exe", "exe-without-standard-input", "elsewhere"])
def test_a_program_that_is_no_batch_file_on_windows_is_handed_its_prompt_as_before(
        monkeypatch, ident, program, name):
    plan, argv = _rendered(ident, SHORT, program)

    with monkeypatch.context() as patch:
        patch.setattr(os, "name", name)
        handed = A.prompt_input(plan, None, argv)

    assert plan["channel"] == "argument" and SHORT in argv
    assert handed == ({"argv": argv, "stdin": None, "directory": None}, None)


def test_a_command_that_does_not_carry_its_prompt_where_its_template_does_is_refused_not_cut(monkeypatch, tmp_path):
    plan, argv = _rendered("claude-code", SHORT, NPM + "claude.cmd")
    argv.insert(1, "--verbose")                         # the prompt no longer stands where the template holds it

    given, why = _on_windows(monkeypatch, A.prompt_input, plan, str(tmp_path / "repository"), argv)

    assert given is None and "cmd.exe" in why
    assert why.endswith("this command does not carry its prompt where its template does")


# ---- the watchdog starts both roles through that one decision ----------------------------------------------

class _Started:
    """A process the cycle started, which has already ended cleanly."""
    pid = 99999
    returncode = 0

    def poll(self):
        return 0


def _starts(monkeypatch):
    """[(argv, what its standard input held)] for each process the cycle starts; None where no file is on it."""
    started = []

    def popen(argv, **kwargs):
        stdin = kwargs.get("stdin")
        started.append((argv, stdin.read() if hasattr(stdin, "read") else None))
        return _Started()
    monkeypatch.setattr(W.subprocess, "Popen", popen)
    return started


def _hands_over_as_windows(monkeypatch):
    """The cycle hands each prompt over as Windows would; everything else in it runs on this platform."""
    handed = A.prompt_input
    monkeypatch.setattr(A, "prompt_input", lambda *args, **kwargs: _on_windows(monkeypatch, handed, *args, **kwargs))


def _batch(started, program):
    return [(argv, stdin) for argv, stdin in started if isinstance(argv, list) and argv and argv[0] == NPM + program]


@pytest.mark.parametrize("reports, key", [(True, "prompt.wake"), (False, "prompt.refill")], ids=["wake", "refill"])
def test_the_architect_started_through_a_batch_file_reads_its_whole_prompt_on_standard_input(
        project, monkeypatch, tmp_path, reports, key):
    world = World(project, monkeypatch, tmp_path)
    started = _starts(monkeypatch)
    monkeypatch.setattr(A, "resolve_binary", lambda name, path=None: (f"{NPM}{os.path.basename(name)}.cmd", "2.1.261"))
    _hands_over_as_windows(monkeypatch)
    world.transcript_age(900)
    if reports:
        world.mail("20260917-1200-kiro-to-fable-BLOCKED-queue.md",
                   f"# queue empty\n\n{language.marker(project, 'decision')}\n")

    world.cycle(dry_run=False)

    prompt = language.text(project, key)
    ((argv, handed),) = _batch(started, "claude.cmd")
    assert handed == prompt.encode("utf-8") and prompt not in argv
    assert argv == [NPM + "claude.cmd", "-p", "--permission-mode", "dontAsk"]


def test_a_nudge_through_a_batch_file_whose_adapter_declares_no_standard_input_starts_nothing(
        project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    started = _starts(monkeypatch)
    found = W.shutil.which
    monkeypatch.setattr(W.shutil, "which", lambda name, path=None, mode=None:
                        f"{NPM}{name}.cmd" if name == "kiro-cli" else found(name, path, mode))
    _hands_over_as_windows(monkeypatch)
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)

    trace = world.cycle(dry_run=False)

    (refused,) = [line for line in trace if line.startswith("the nudge's prompt cannot be handed over: ")]
    assert "Windows" in refused and "cmd.exe" in refused
    assert refused.endswith("adapter kiro declares no standard input for `resume`; not nudging")
    assert _batch(started, "kiro-cli.cmd") == []
