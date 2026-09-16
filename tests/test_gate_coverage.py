import json
import os
import subprocess

from ao import cli, lib as A
from tests.test_profiles import _init_args, _tree_state


def _files(root, tree, ext, n=6):
    os.makedirs(os.path.join(root, tree), exist_ok=True)
    for i in range(n):
        open(os.path.join(root, tree, f"f{i}{ext}"), "w", encoding="utf-8").write("x\n")


def _gates(root, gates):
    json.dump({"gates": gates, "profiles": {"quick": list(gates)}},
              open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8"))


def test_the_doctor_names_a_tree_no_gate_exercises(project):
    root = project["root"]
    _files(root, "frontend", ".ts")
    _files(root, "backend", ".cs")
    _files(root, "notes", ".cs", n=2)                        # under the minimum
    _gates(root, {"lint": {"run": "npm run lint"}})

    keys = [key for key, _ in cli.doctor_problems(project)]

    assert "gate-uncovered:backend" in keys
    assert "gate-uncovered:frontend" not in keys and "gate-uncovered:notes" not in keys


def test_a_gate_with_inputs_covers_only_the_trees_they_reach(project):
    root = project["root"]
    _files(root, "src", ".py")
    _files(root, "tools", ".py")
    spec = {"gates": {"pytest": {"run": "python -m pytest -q", "inputs": ["src/**", "tests/**"]}}}

    assert A.gate_coverage_gaps(root, spec, 5) == [("tools", "python", 6)]
    assert A.gate_coverage_gaps(root, {"gates": {"pytest": {"run": "python -m pytest -q"}}}, 5) == []


def test_every_detected_toolchain_gets_gates(tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}), encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (root / "go.mod").write_text("module x\n", encoding="utf-8")

    quick = cli._detect_gates(str(root))["profiles"]["quick"]

    assert {"lint", "pytest-collect", "go-vet", "diff-check"} <= set(quick)


def test_init_refuses_quick_gates_that_exercise_nothing_unless_told(tmp_path, capsys):
    root = tmp_path / "dotnet-without-a-solution"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _files(str(root), "backend", ".cs")
    before = _tree_state(root)

    assert cli.cmd_init({"root": str(root)}, _init_args(profile=None, agent="kiro", no_mcp=True)) == 1
    assert "exercise none of the detected toolchains (dotnet)" in capsys.readouterr().out
    assert _tree_state(root) == before

    args = _init_args(profile=None, agent="kiro", no_mcp=True)
    args.allow_uncovered_gates = True
    assert cli.cmd_init({"root": str(root)}, args) == 0
    assert os.path.exists(root / ".ao" / "gates.json")
