"""Warp's Oz CLI: the harness ao can start and cannot yet read back (WARP-ADAPTER).

Measured on Oz v0.2026.10.07.08.29.stable_00 on 2026-10-15. One real turn was started:
`oz agent run -p ... -C <a throwaway directory> --model <a custom endpoint's model id> --no-snapshot
--output-format json` authenticated as the person's Warp account, wrote the two events below on
standard output, sent its request to the endpoint that person configured, and ended at that
provider's daily token cap with exit 1 and the cap's own words on stderr. So the assertions here
are that command surface and those two events. Nothing is asserted about a reply, because no run has
given one yet - which is exactly why the adapter declares no answer path and this file pins that.
"""
import json
import os
from pathlib import Path

import pytest

from ao import cli, lib as A, skillkit, watchdog as W
from tests.standin import program

# What the measured run wrote on stdout, verbatim but for its ids.
RUN_EVENTS = "\n".join([
    json.dumps({"type": "system", "event_type": "run_started", "run_id": "run-0001",
                "run_url": "https://oz.warp.dev/runs/run-0001"}),
    json.dumps({"type": "system", "event_type": "conversation_started", "conversation_id": "conversation-0001"}),
])
FAILED_TURN = ('Error: Response stream finished unexpectedly with internal error: POST '
               '"https://endpoint.example/v1/chat/completions": 429 Too Many Requests {"message":'
               '"...","code":"daily_token_limit_exceeded"}')


def test_the_oz_command_line_is_warp_and_a_run_started_without_a_person_reads_as_one():
    """A harness is identified by the program it runs: the CLI is `oz`, and `warp` on PATH is a VPN."""
    warp = A.load_adapter("warp")
    assert A._program_name("/Applications/Warp.app/Contents/Resources/bin/oz") == "oz"
    assert A.command_adapter(["oz", "agent", "run", "-p", "the next slice"])["id"] == "warp"
    assert A._headless_argv(["oz", "agent", "run", "-p", "x"]), warp["detect"]["headless"]
    assert A._headless_argv(["oz", "agent", "run", "--prompt=x"]), "a flag with its value attached is the same run"
    # `oz run list` and `oz run get` read the server's run list: not turns, and `ao hold` and the reaper
    # stop what this answers True for, so a subcommand named `run` must never make one.
    assert not A._headless_argv(["oz", "run", "list"])
    assert not A._headless_argv(["oz", "run", "get", "run-0001"])


def test_ao_neither_widens_a_warp_turn_nor_claims_to():
    """The adapter names no approval flag, because the CLI has none, so a composed command stays as it was."""
    warp = A.load_adapter("warp")
    assert A.reviewer_eligibility(warp)[0] is False, warp["options"]["trust_none_why"]
    argv = list(warp["send"]["argv"])
    assert A.pinned_argv(argv, "implementer", adapter=warp) == (argv, []), "ao adds no grant it cannot name"


def test_a_warp_turn_answers_nothing_ao_has_measured_enough_to_read():
    """No result record has ever been seen, so no path to one is declared and each reader finds nothing."""
    warp = A.load_adapter("warp")
    assert warp["observation_mode"] == "call-return" and warp["transcript"]["kind"] == "call-return"
    assert "record" not in warp["transcript"] and "sessions" not in warp
    assert A.session_store_reachable(warp) is False
    assert A.store_sessions("warp", "/tmp/any-checkout") == []
    assert "answer_record" not in warp["options"] and "result_record" not in warp

    shape = A.transcript_shape(warp)
    for line in RUN_EVENTS.splitlines():
        event = json.loads(line)
        assert A.record_kind(event, shape) is None, "a system event names no kind ao was told about"
        assert A.message_words(event, shape) == "", "and holds no words ao would read out of it"
    assert A.messages([], adapter=warp) == []
    # The review path that turns a printed record into an answer asks this adapter nothing: it declares
    # no flags and no field, and inventing one would read a system event as the agent's verdict.
    assert cli._answer_record(["oz", "agent", "run", "-p", "x", "--output-format", "json"], RUN_EVENTS) is None


def test_the_store_ao_mapped_belongs_to_the_app_and_not_to_a_run_ao_started():
    """The app's own conversations are a different surface from a CLI run's, and no store is declared over them."""
    warp = A.load_adapter("warp")
    assert "warp.sqlite" in warp["notes"], "the mapped store is written down, not quietly dropped"
    assert not A._block(warp, "transcript").get("path"), "and nothing reads it as this harness's transcript"


@pytest.mark.parametrize("directory", ["/Applications/Warp.app/Contents/Resources/bin",
                                      "~/Applications/Warp.app/Contents/Resources/bin"])
def test_the_shipped_bundle_is_found_and_admitted_without_an_adapter_layer(project, tmp_path, monkeypatch, directory):
    """The bundle's oz was off PATH; state and log directories found no program (WARP-ADAPTER)."""
    bundle = tmp_path / "Applications" / "Warp.app" / "Contents" / "Resources" / "bin"
    executable = program(bundle, "oz", "raise AssertionError('this stand-in must never run')\n")
    expanduser = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser", lambda path: str(bundle) if path == directory else expanduser(path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    assert A.binary_candidates("oz") == [executable]
    shipped = A.turn_adapter("warp")
    assert directory in shipped["detect"]["install_dirs"]
    assert A.runnable_binary(shipped["send"]["argv"][0]) == executable
    assert str(bundle) in W.child_path().split(os.pathsep)
    assert A.turn_refusal("architect", "warp", [executable, "agent", "run", "-p", "x"],
                          root=project["root"], resolved=executable) is None


@pytest.mark.parametrize("requested", ["warp", "auto"])
def test_init_with_only_oz_uses_the_files_only_setup(project, tmp_path, monkeypatch, capsys, requested):
    """An MCP note alone promised tools with an empty registration list (WARP-ADAPTER)."""
    executable = program(tmp_path / "bin", "oz", "raise AssertionError('oz must never run')\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert cli.main(["-C", project["root"], "init", "--agent", requested]) == 0
    output = capsys.readouterr().out
    assert "No MCP-capable agent detected" in output
    assert "registered:" not in output
    assert (Path(project["root"]) / ".ao" / "PLAYBOOK.md").is_file()
    assert not (Path(project["root"]) / ".warp").exists()
    assert not (Path(project["root"]) / ".mcp.json").exists()
    assert "warp" not in dict(skillkit.setup_adapters(project["root"]))
    assert skillkit.detect_agents(project["root"])[1] == {"generic"}
    assert A.program_path("oz") == executable


def test_the_measurement_describes_evidence_without_internal_landing_process():
    """The public note explains measured refusals, not how its slice lands (WARP-ADAPTER)."""
    note = Path(__file__).resolve().parents[1] / "docs" / "audit" / "2026-10-15-warp-store.md"
    text = note.read_text(encoding="utf-8")
    assert "Acceptance boundary" not in text
    assert "landing kit" not in text and "CHANGELOG" not in text and "five-path guideline" not in text
    introduction = text.split("## Two harnesses", 1)[0]
    assert "measured refusals" in introduction


def test_warp_accepts_a_pinned_skill_into_its_own_directory(project, tmp_path, capsys):
    """skills_dir planned a Warp target which detect.dirs did not admit (WARP-ADAPTER)."""
    from tests.test_content_seam import _source
    source, pin = _source(tmp_path)
    assert cli.main(["-C", project["root"], "content", "add", f"{source}@{pin}",
                     "--skills", "alpha", "--harness", "warp"]) == 0, capsys.readouterr().out
    skill = Path(project["root"]) / ".warp" / "skills" / "alpha" / "SKILL.md"
    assert skill.read_text(encoding="utf-8").endswith("Do it well.\n")
    assert A.verify_content(project["root"]) == []
    assert A.load_adapter("warp")["detect"]["dirs"] == [".warp"]
    assert A._content_refusal(project["root"], "warp", ".agents/skills/alpha/SKILL.md", set())


@pytest.mark.parametrize("staged", [False, True])
def test_warp_skills_are_harness_files_and_product_changes_remain_reviewable(project, staged):
    """Warp's missing ownership directory put its skills into product review (WARP-ADAPTER)."""
    from tests.test_content_seam import _git
    root = Path(project["root"])
    skill = root / ".warp" / "skills" / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# harness-only text\n", encoding="utf-8")
    (root / "product.txt").write_text("product change\n", encoding="utf-8")
    if staged:
        _git(root, "add", ".warp/skills/alpha/SKILL.md", "product.txt")
    assert ".warp/" in A.harness_dirs()
    dirty = A.product_dirty(str(root), project)
    assert any("product.txt" in line for line in dirty)
    assert not any(".warp" in line for line in dirty)
    diff, _ = A.review_diff(str(root), project)
    assert "product change" in diff and "harness-only text" not in diff


@pytest.mark.parametrize("directory", ["/Applications/Warp.app/Contents/Resources/bin",
                                      "~/Applications/Warp.app/Contents/Resources/bin"])
def test_adapters_reports_the_off_path_warp_bundle_as_installed(project, tmp_path, monkeypatch, capsys, directory):
    """Availability must search where ao starts programs, including bundles (WARP-ADAPTER)."""
    bundle = tmp_path / "Warp.app" / "Contents" / "Resources" / "bin"
    executable = program(bundle, "oz", "raise AssertionError('oz must never run')\n")
    expanduser = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser", lambda path: str(bundle) if path == directory else expanduser(path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.setattr(A, "_run_program", lambda *args, **kwargs: ("", 0))  # no keyflip
    monkeypatch.setattr(A, "_SURFACES", {"at": 0.0, "rows": {}})
    assert A.program_path("oz") is None
    assert A.runnable_binary("oz") == executable
    assert A.tool_availability()["warp"] == {"installed": True, "binary": "oz"}
    assert cli.main(["-C", project["root"], "adapters"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("warp "))
    assert "installed" in line
