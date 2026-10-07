"""An opencode reviewer runs as an agent of ao's whose permission denies every tool but reading (OPENCODE-REVIEWER).

Measured on opencode 1.18.27 on 2026-10-07 with EVREN's DeepSeek V4 Flash. No flag denies opencode a tool - `--auto`
only approves - so its adapter was ineligible to review. Run as an agent whose permission denies `*` and allows read,
grep, glob and list, with `--pure`, opencode offered the model read, grep and glob alone; asked to write a file, run a
command or fetch a page, it had no tool to, and nothing was written. It prints the turn's progress on stderr and the
model's text alone on stdout.
"""
import os
import sys

import pytest

from ao import allowlist, cli, lib as A

OPENCODE = ["opencode", "run", "{prompt}", "--model", "evren/deepseek-v4-flash", "--pure"]


def _front_matter(path):
    import json
    _, head, body = path.read_text(encoding="utf-8").split("---\n", 2)
    return {key: json.loads(value) for key, value in (line.split(": ", 1) for line in head.splitlines())}, body


def test_an_opencode_reviewer_runs_as_an_agent_whose_permission_only_reads(tmp_path):
    argv, why = cli._reviewer_agent(str(tmp_path), OPENCODE)

    assert why is None and argv[:-2] == OPENCODE and argv[-2] == "--agent"
    config, body = _front_matter(tmp_path / ".opencode" / "agents" / f"{argv[-1]}.md")
    assert config["permission"] == {"*": "deny", "read": "allow", "grep": "allow", "glob": "allow", "list": "allow"}
    assert config["mode"] == "primary" and "write nothing" in body


def test_an_opencode_reviewer_is_eligible_and_holds_to_its_flags():
    assert A.reviewer_eligibility(A.load_adapter("opencode")) == (True, None)
    composed = A.compose_reviewer("opencode", model="evren/deepseek-v4-flash", family="deepseek")["argv"]

    assert composed == OPENCODE and A.reading_problems(composed) == []
    assert any("--pure" in problem for problem in A.reading_problems([part for part in composed if part != "--pure"]))
    assert any("it names --agent" in problem for problem in A.reading_problems(composed + ["--agent", "build"]))
    assert any("--auto" in problem for problem in allowlist.reviewer_problems(composed + ["--auto"]))


@pytest.mark.parametrize("version, refused", [("1.18.27", False), ("1.18.30", False), ("1.17.3", True),
                                              ("1.19.0", True)])
def test_an_opencode_reviewer_runs_only_on_the_release_its_agent_was_measured_on(version, refused):
    assert (cli._measured_release_problem(A.load_adapter("opencode"), "/opt/opencode", version) is not None) == refused


def _harness(tmp_path, stdout, stderr):
    """A stand-in opencode: it checks the agent ao named allows reading alone, and prints as opencode run does."""
    harness = tmp_path / "opencode"
    harness.write_text(f"""#!{sys.executable}
import os, sys
name = sys.argv[sys.argv.index("--agent") + 1]
with open(os.path.join(".opencode", "agents", name + ".md"), encoding="utf-8") as fh:
    text = fh.read()
assert '"*": "deny"' in text and '"write"' not in text and '"bash"' not in text, text
assert "--pure" in sys.argv
sys.stderr.write({stderr!r})
sys.stdout.write({stdout!r})
""", encoding="utf-8")
    harness.chmod(0o755)
    return [str(harness), "run", "review this", "--model", "evren/deepseek-v4-flash", "--pure"]


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_an_opencode_review_is_the_text_it_prints_on_stdout(project, tmp_path):
    verdict = "VERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n"
    argv = _harness(tmp_path, verdict, "> ao-reviewer · deepseek-v4-flash\n* Read candidate/x.py\n")

    attempt = cli._run_reviewer(project["root"], argv, 30)

    assert attempt["ok"] and attempt["out"] == verdict.strip(), attempt


@pytest.mark.skipif(os.name == "nt", reason="the stand-in harness is a script its shebang runs")
def test_an_opencode_run_that_printed_only_its_progress_answered_nothing(project, tmp_path):
    """Its progress is no answer: read as one, a tool call's line was a reviewer's text."""
    argv = _harness(tmp_path, "", "> ao-reviewer · deepseek-v4-flash\n* Glob candidate/**\nVERDICT: APPROVED\n")

    attempt = cli._run_reviewer(project["root"], argv, 30)

    assert not attempt["ok"] and attempt["kind"] == "silence" and attempt["retryable"], attempt
