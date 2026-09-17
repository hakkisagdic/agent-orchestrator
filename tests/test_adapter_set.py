import json
import os
import re
import shutil
from types import SimpleNamespace

from ao import cli, lib as A

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAYCER = {"claude", "codex", "opencode", "traycer", "cursor", "grok", "qwen", "kiro", "droid", "kimi", "copilot",
           "kilocode", "openrouter", "amp", "devin", "pi", "hermes", "omp", "huggingface", "reasonix", "antigravity"}
NEW = ("qwen", "grok", "droid", "kimi", "kilocode", "pi", "hermes", "omp", "reasonix")


def test_every_vendor_of_the_reference_enum_has_an_adapter_or_a_reason_and_the_list_matches_the_package(project):
    vendors = {vendor["id"]: vendor for vendor in A.vendor_list()}

    assert {ident for ident, vendor in vendors.items() if vendor.get("traycer")} == TRAYCER
    assert A.vendor_problems() == []
    assert all(vendors[ident].get("adapter") or vendors[ident].get("why") for ident in TRAYCER)
    assert "vendors" not in A.adapter_catalog()


def test_an_adapter_no_vendor_names_is_an_error_not_a_gap(project, tmp_path, monkeypatch):
    package = tmp_path / "adapters"
    shutil.copytree(A.adapters_dir(), package)
    (package / "newcomer.json").write_text(json.dumps({"id": "newcomer", "name": "n", "verified": "untested",
                                                       "contract": 1, "send": {"argv": ["newcomer", "{prompt}"]}}),
                                           encoding="utf-8")
    listed = json.loads((package / "vendors.json").read_text(encoding="utf-8"))
    listed["vendors"] += [{"id": "ghost", "adapter": "ghost"}, {"id": "mute", "adapter": None}]
    (package / "vendors.json").write_text(json.dumps(listed), encoding="utf-8")
    monkeypatch.setattr(A, "adapters_dir", lambda: str(package))

    problems = A.vendor_problems()

    assert "adapter newcomer is shipped, but no vendor in adapters/vendors.json names it" in problems
    assert "vendor ghost names adapter ghost, which is not shipped" in problems
    assert "vendor mute has no adapter and no reason for having none" in problems


def test_every_adapter_says_what_was_verified_and_the_new_ones_validate(project):
    for ident in A.shipped_adapter_ids():
        adapter = A.load_adapter(ident)
        assert adapter["verified"] in A.ADAPTER_VERIFIED, ident
        if adapter["verified"] != "full":
            assert any(adapter.get(key) for key in ("disclaimer", "notes", "verified_source")), ident
        assert "trust_none" in (adapter.get("options") or {}), ident
    for ident in NEW:
        adapter = A.load_adapter(ident)
        assert A.validate_adapter(adapter) == [], ident
        assert adapter["verified"] == "untested" and adapter["sources"], ident


def test_the_reviewer_role_follows_what_each_new_adapter_can_deny(project):
    eligible = {ident for ident in NEW if A.reviewer_eligibility(A.load_adapter(ident))[0]}

    assert eligible == {"qwen", "kilocode", "pi", "hermes", "omp", "reasonix"}
    assert A.compose_reviewer("reasonix", model="m", effort="high")["argv"] == [
        "reasonix", "-p", "{prompt}", "--model", "m", "--effort", "high", "--permission-mode=read-only"]
    assert "--prompt" in A.reviewer_eligibility(A.load_adapter("kimi"))[1]


def test_the_doctor_names_a_configured_adapter_whose_command_this_machine_lacks(project, monkeypatch):
    cfg = dict(project, implementer={"adapter": "kilocode", "session": "s1", "name": "dev"})
    monkeypatch.setattr(A, "binary_candidates", lambda name, path=None: [])

    assert "kilo or kilocode is not on this machine" in dict(cli.doctor_problems(cfg))["adapter-binary:dev"]

    monkeypatch.setattr(A, "binary_candidates", lambda name, path=None: ["/opt/bin/kilocode"] if name == "kilocode" else [])
    assert "adapter-binary:dev" not in dict(cli.doctor_problems(cfg))


def test_availability_and_the_listing_derive_from_the_vendor_list(project, monkeypatch, capsys):
    asked = []
    monkeypatch.setattr(A, "_run_program", lambda argv, cwd=None, **kwargs: asked.append((list(argv), cwd)) or
                        ("●  Gemini CLI  signed in\n○  cursor  none", 0))
    monkeypatch.setattr(A, "_SURFACES", {"at": 0.0, "rows": {}})
    monkeypatch.setattr(shutil, "which", lambda name, *args, **kwargs: "/opt/bin/kilocode" if name == "kilocode" else None)

    rows = A.tool_availability()

    assert asked == [(["keyflip", "surfaces"], A.HOME)]
    assert rows["gemini"]["account"] is True and rows["cursor-agent"]["account"] is False
    assert rows["kilocode"] == {"installed": True, "binary": "kilocode"}
    assert rows["qwen"] == {"installed": False, "binary": "qwen"}
    assert A.adapter_binaries(A.load_adapter("cursor-agent")) == ["agent", "cursor-agent"]
    cli.cmd_adapters(project, SimpleNamespace(action="list", target=None))
    out = capsys.readouterr().out
    assert re.search(r"openrouter .*no adapter", out) and re.search(r"^hermes ", out, re.M)


def test_the_support_matrix_in_the_docs_is_the_vendor_list(project):
    text = open(os.path.join(ROOT, "docs", "adapters.md"), encoding="utf-8").read()
    rows = {m.group(1): (m.group(2), m.group(3), m.group(4))
            for m in re.finditer(r"^\| `([a-z-]+)` \| (`[a-z-]+`|—) \| (\w+|—) \| (\w+|—) \|", text, re.M)}

    for vendor in A.vendor_list():
        adapter = vendor.get("adapter")
        if adapter:
            loaded = A.load_adapter(adapter)
            expected = (f"`{adapter}`", loaded["verified"],
                        "eligible" if A.reviewer_eligibility(loaded)[0] else "ineligible")
        else:
            expected = ("—", "—", "—")
        assert rows.pop(vendor["id"], None) == expected, vendor["id"]
    assert rows == {}
