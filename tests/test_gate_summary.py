"""Gate summaries retain stream order, require both counts and include pytest errors (TRIAGE-2-LOW)."""
import os
import shlex
import subprocess

import pytest

from ao import cli, lib as A
from tests.standin import program


def test_gate_summary_uses_the_order_the_process_wrote(project, tmp_path):
    runner = program(tmp_path, "gate-output", "import os\n"
                     "os.write(2, b'# pass 900\\n# fail 0\\n')\n"
                     "os.write(1, b'3 passed in 1.00s\\n')\n")
    command = subprocess.list2cmdline([runner]) if os.name == "nt" else shlex.quote(runner)

    output, code = cli._run_gate(project, {"run": command, "timeout": 10}, project["root"])

    assert code == 0
    assert output == "# pass 900\n# fail 0\n3 passed in 1.00s\n"
    assert A.gate_counts(output) == (3, 0)


@pytest.mark.parametrize("summary", [r"(?P<pass>\d+)", r"(?P<fail>\d+)", r"(\d+)", "[", ""])
def test_gate_summary_requires_a_valid_regex_with_both_named_groups(summary):
    why = cli._gate_shape_problem({"run": "tests", "summary": summary})
    assert why and "summary" in why
    assert A.gate_counts("900", summary) is None


def test_gate_summary_does_not_invent_a_count_for_an_unmatched_group():
    summary = r"(?P<pass>\d+) passed(?:, (?P<fail>\d+) failed)?"
    assert cli._gate_shape_problem({"run": "tests", "summary": summary}) is None
    assert A.gate_counts("900 passed", summary) is None
    assert A.gate_counts("900 passed, 0 failed", summary) == (900, 0)


@pytest.mark.parametrize("closing, expected", [("1 error in 0.10s", (0, 1)),
                                                ("=== 2 errors in 0.10s ===", (0, 2))])
def test_pytest_errors_alone_are_failures(closing, expected):
    assert A.gate_counts(closing) == expected
