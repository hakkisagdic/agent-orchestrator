"""A reviewer reads nothing of ao's own standard input (REVIEWER-STDIN).

On 2026-10-12 a range was reviewed again with Codex from a shell whose standard input was a pipe nobody closed.
`codex exec`, finding its input open and no terminal, said "Reading additional input from stdin...", waited for the
rest of its prompt there, spent no CPU, and was ended as stalled after ten minutes - twice, the retry as the first.
A reviewer's input is now empty unless its prompt is handed on it.
"""
import os
import subprocess
import sys

import pytest

from ao import cli

READS_ALL = ("import sys\n"
             "rest = sys.stdin.read()\n"
             "print('VERDICT: APPROVED')\n"
             "print('read', len(rest))\n")


def test_a_reviewer_is_started_with_an_empty_input_unless_its_prompt_comes_on_it(project, monkeypatch):
    started = []
    real = subprocess.Popen

    def popen(argv, *args, **kwargs):
        started.append(kwargs.get("stdin"))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    cli._run_reviewer(project["root"], [sys.executable, "-c", "print('VERDICT: APPROVED')", "{prompt}"], timeout=30)
    plan = {"argv": [sys.executable, "-c", READS_ALL], "channel": "stdin", "run": None, "prompt": "the prompt",
            "batch": None}
    cli._run_reviewer(project["root"], plan["argv"], timeout=30, channel=plan)

    assert started[0] is subprocess.DEVNULL
    assert started[1] is not None and started[1] is not subprocess.DEVNULL      # the prompt's own file


@pytest.mark.skipif(os.name == "nt", reason="the test hands this process a pipe as its input, and on Windows a child "
                                            "is given the process's standard handle, which that does not change")
def test_a_reviewer_that_reads_its_input_to_the_end_answers_while_ao_s_own_input_stays_open(project):
    read_end, write_end = os.pipe()                    # ao's own input: a pipe its writer never closes
    kept = os.dup(0)
    os.dup2(read_end, 0)
    try:
        attempt = cli._run_reviewer(project["root"], [sys.executable, "-c", READS_ALL, "{prompt}"], timeout=20)
    finally:
        os.dup2(kept, 0)
        for fd in (kept, read_end, write_end):
            os.close(fd)

    assert attempt["ok"] is True, attempt
    assert attempt["out"].splitlines() == ["VERDICT: APPROVED", "read 0"]
