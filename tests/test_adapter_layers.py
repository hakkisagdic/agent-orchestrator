import json
import os
import sys
from types import SimpleNamespace

import pytest

from ao import cli, lib as A

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fixture harness runs through its shebang")


def _harness(tmp_path):
    harness = tmp_path / "harness"
    source = open(os.path.join(os.path.dirname(A.__file__), "conformance_harness.py"), encoding="utf-8").read()
    harness.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
    harness.chmod(0o755)
    return str(harness)


def test_every_shipped_adapter_passes_conformance_through_the_fixture_harness(tmp_path):
    harness = _harness(tmp_path)
    failures = {}
    for ident, entry in A.adapter_catalog().items():
        results = A.conform_adapter(entry["adapter"], harness, str(tmp_path))
        failed = [(capability, detail) for capability, state, detail in results if state == "fail"]
        if failed:
            failures[ident] = failed
    assert failures == {}
    kiro = dict((c, s) for c, s, _ in A.conform_adapter(A.load_adapter("kiro"), harness, str(tmp_path)))
    assert kiro["send"] == "pass" and kiro["resume"] == "pass"


def test_a_project_adapter_overrides_by_id_and_a_foreign_contract_is_refused(project, capsys):
    root = project["root"]
    directory = os.path.join(root, ".ao", "adapters")
    os.makedirs(directory)
    kiro = json.load(open(os.path.join(A.adapters_dir(), "kiro.json"), encoding="utf-8"))
    with open(os.path.join(directory, "kiro.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(kiro, name="Kiro, as this project runs it", contract=1), fh)
    with open(os.path.join(directory, "future.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": "future", "name": "a harness for a later ao", "verified": "untested", "contract": 99,
                   "send": {"argv": ["future", "{prompt}"]}}, fh)

    catalog = A.adapter_catalog(root)

    assert catalog["kiro"]["source"] == "project"
    assert A.load_adapter("kiro", root)["name"] == "Kiro, as this project runs it"
    assert A.load_adapter("kiro")["name"] != "Kiro, as this project runs it"
    assert A.load_adapter("future", root) == {}
    assert "contract 99" in catalog["future"]["problem"] and "contract 1" in catalog["future"]["problem"]
    cli.cmd_adapters(project, SimpleNamespace(action="list", target=None))
    out = capsys.readouterr().out
    assert "refused" in out and "project" in out


def test_validate_names_what_a_candidate_is_missing_before_anyone_relies_on_it(project, tmp_path, capsys):
    candidate = tmp_path / "half.json"
    candidate.write_text(json.dumps({"id": "half", "send": {"argv": ["half", "run"]},
                                     "resume": {"argv": ["half", "{prompt}", "{wat}"]}}), encoding="utf-8")

    assert cli.cmd_adapters(project, SimpleNamespace(action="validate", target=str(candidate))) == 1

    out = capsys.readouterr().out
    for text in ("`name` is missing", "`verified` is missing", "`contract` is missing",
                 "`send.argv` must carry {prompt} in exactly one argument", "`resume.argv` must carry {session}",
                 "placeholders ao does not fill: wat"):
        assert text in out
