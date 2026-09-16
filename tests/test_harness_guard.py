"""The core names no harness (#76).

Harness knowledge lives in the adapters and nowhere else. This guard reads every
core module and fails when a harness's id, command, process, dot-directory or
rule file appears in code - a string that is not a docstring, or a name - the
same shape as the role-name guard of #31, and the reason both exist.
"""
import ast
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "src" / "ao" / "adapters"
# Commands that are ordinary words first - an A2A role is "agent" - say nothing about a harness.
ORDINARY = {"agent", "amp", "cloud", "cmd", "cursor", "pi", "q"}


def _harness_words():
    words, markers = set(), set()
    for path in ADAPTERS.glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "vendors.json":
            words |= {vendor["id"] for vendor in document["vendors"]}
            continue
        if path.name == "profiles.json":
            continue
        words.add(document["id"])
        send = (document.get("send") or {}).get("argv") or []
        detect = document.get("detect") or {}
        words |= set(send[:1]) | set(detect.get("binaries") or []) | set(detect.get("processes") or [])
        markers |= {d.strip("/") for d in detect.get("dirs") or []}
        markers |= set((document.get("directives") or {}).get("rule_files") or [])
    return words, markers


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def _hits(path, words, markers):
    word = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(sorted(map(re.escape, words - ORDINARY), key=len, reverse=True))
                      + r")(?![A-Za-z0-9])", re.I)
    marker = re.compile("|".join(re.escape(m) + r"(?![A-Za-z0-9_])" for m in sorted(markers, key=len, reverse=True)))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            text = node.value
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            text = node.name
        else:
            continue
        if word.search(text) or (markers and marker.search(text)):
            out.append(f"{path.name}:{node.lineno} {text[:80]!r}")
    return out


def test_no_core_module_names_a_harness():
    words, markers = _harness_words()
    assert {"kiro", "claude-code", "kiro-cli", "codex", "qwen"} <= words and {".kiro", "CLAUDE.md"} <= markers

    found = [hit for path in sorted((ROOT / "src" / "ao").rglob("*.py")) for hit in _hits(path, words, markers)]

    assert found == []


def test_the_guard_sees_a_harness_named_in_code(tmp_path):
    words, markers = _harness_words()
    sample = tmp_path / "sample.py"
    sample.write_text('"""A docstring may say kiro."""\n'
                      'def claude_dir():\n    return os.path.join(HOME, ".kiro", "steering")\n'
                      'NAMES = ("agent", "codex")\n', encoding="utf-8")

    found = _hits(sample, words, markers)

    assert sorted(hit.split(" ", 1)[1] for hit in found) == ["'.kiro'", "'claude_dir'", "'codex'"]
