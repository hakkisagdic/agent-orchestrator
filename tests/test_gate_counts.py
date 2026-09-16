import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import cli, lib as A

NOISE = "\n".join(f"ok {n} - a passing case" for n in range(40))

TABLE = [
    ("node-tap", "TAP version 13\nok 1 - a\n# tests 3\n# pass 2\n# fail 1\n# cancelled 0\n", (2, 1)),
    ("node-spec", "✔ a\nℹ tests 3\nℹ pass 3\nℹ fail 0\nℹ duration_ms 12\n", (3, 0)),
    ("pytest-quiet", "....F\n1 failed, 463 passed in 328.25s (0:05:28)\n", (463, 1)),
    ("pytest-errors", "4 failed, 114 passed, 1 error in 79.87s (0:01:19)\n", (114, 5)),
    ("pytest-verbose", "===== 464 passed, 2 warnings in 171.72s (0:02:51) =====\n", (464, 0)),
    ("misleading-earlier-summary",
     "# pass 900\n# fail 0\nnot ok 1 - the real failure\n# tests 2\n# pass 1\n# fail 1\n", (1, 1)),
    ("misleading-name-outside-the-tail", "# pass 900\n# fail 0\n" + NOISE + "\n", None),
    ("no-summary-at-all", "compiled 12 files\n", None),
    ("prose-mentioning-passes", "the pass 3 of the fail 2 rule\n", None),
]


@pytest.mark.parametrize("case,output,expected", TABLE, ids=[row[0] for row in TABLE])
def test_counts_come_only_from_the_closing_summary(case, output, expected):
    assert A.gate_counts(output) == expected


def test_a_gate_can_name_its_runner_summary():
    output = "RESULT pass=12 fail=0\n# pass 900\n# fail 0\nRESULT pass=7 fail=1\n"
    assert A.gate_counts(output, r"^RESULT pass=(?P<pass>\d+) fail=(?P<fail>\d+)$") == (7, 1)
    assert A.gate_counts(output, r"^NOTHING (?P<pass>\d+)$") is None
    assert A.gate_counts(output, r"(unbalanced") is None


def _verify(project, tmp_path, monkeypatch, script, **gate):
    root = project["root"]
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8").write("value = 1\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)
    argv = [sys.executable, "-c", script]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    spec = {"gates": {"test": {"run": command, "timeout": 30, **gate}},
            "profiles": {"quick": ["test"]}, "default_profile": "quick"}
    open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8").write(json.dumps(spec))
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    code = cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0))
    return code, A.latest_verification(root)["gates"][0]


def test_a_misleading_line_cannot_make_a_failing_gate_read_as_passing(project, tmp_path, monkeypatch):
    script = ("print('# pass 900'); print('# fail 0'); print('not ok 1 - real'); "
              "print('# pass 1'); print('# fail 1'); raise SystemExit(1)")
    code, gate = _verify(project, tmp_path, monkeypatch, script)
    assert code == 1
    assert gate["passed"] is False and gate["counts"] == {"pass": 1, "fail": 1} and gate["detail"] == "1/2"


def test_min_tests_is_held_to_the_runner_summary_not_a_printed_number(project, tmp_path, monkeypatch):
    script = "print('# pass 900'); print('# fail 0'); " + "; ".join(f"print('ok {n}')" for n in range(40))
    code, gate = _verify(project, tmp_path, monkeypatch, script, min_tests=5)
    assert code == 1
    assert gate["passed"] is False and gate["counts"] is None
    assert "closing summary was not found" in gate["detail"]
