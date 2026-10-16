"""The eleven adapter claims triaged on 2026-10-12, measured on 2026-10-16 against the harnesses
they name and held here: a fixed value cannot drift back, and a carried note cannot lose the
measurement that justified keeping it.

Method and disposition per claim are in docs/audit/2026-10-16-harness-measure.md. Three of the
eleven were wrong when measured (cloud-generic's fork fetch, gemini's resume argv, qoder's idle
window) and are fixed; the rest are kept with their measured bound recorded beside them.
"""
import inspect

from ao import journal, lib as A


def test_cloud_generic_fetches_a_fork_pr_from_the_pull_namespace():
    """A fork PR's branch does not exist in the base repository - only refs/pull/{pr}/head does."""
    result = A.load_adapter("cloud-generic")["result"]

    assert result["fetch"] == ["git", "fetch", "origin", "refs/pull/{pr}/head"]
    assert "refs/pull/4143/head" in result["fetch_note"] and "fork" in result["fetch_note"]


def test_geminis_resume_argv_carries_the_session_it_resumes():
    """Until 2026-10-16 the resume argv was the send's, so a resume started a new session."""
    adapter = A.load_adapter("gemini")

    assert adapter["send"]["argv"] == ["gemini", "--prompt", "{prompt}"]
    assert adapter["resume"]["argv"] == ["gemini", "--resume", "{session}", "--prompt", "{prompt}"]
    assert A.prompt_channel_problems(adapter) == []
    plan, why = A.prompt_plan(adapter["resume"]["argv"], "x" * 2_000_000, "gemini")
    assert why is None and plan["channel"] == "stdin" and "{prompt}" not in plan["argv"]


def test_a_qoder_turn_stays_out_of_idle_across_a_ten_minute_check():
    """Measured 2026-10-16: reviewer-store turns stayed quiet 612 s before a first partial; IDE turns 599/606 s."""
    busy = A.load_adapter("qoder")["busy"]

    assert busy["idle_seconds"] == 900
    assert "612" in busy["note"] and "599" in busy["note"] and "135" in busy["note"]


def test_codexs_resume_note_holds_the_rechecked_0_160_0_reading():
    """Rechecked 2026-10-16 on codex-cli 0.160.0: resume rejects --sandbox; -c sandbox_mode is accepted."""
    adapter = A.load_adapter("codex")

    assert adapter["options"]["sandbox"] == ["--sandbox", "{mode}"]
    assert "--sandbox" not in " ".join(adapter["resume"]["argv"])
    note = adapter["options"]["sandbox_note"]
    assert "0.160.0" in note and "unexpected argument" in note and 'sandbox_mode="read-only"' in note


def test_kiros_744_hour_stop_is_the_longest_month_between_resets():
    adapter = A.load_adapter("kiro")

    assert [stop["hours"] for stop in adapter["quota"]["stops"]] == [744, 744, 744]
    assert 744 == 31 * 24
    assert "744 h = 31 days" in adapter["quota"]["stops_note"]


def test_pr_agents_measured_0_45_facts_are_carried():
    """Measured on the PyPI 0.45.0 wheel, 2026-10-16: exit 0 without an answer file; the pin lifts the 32000 budget."""
    adapter = A.load_adapter("pr-agent")
    environment = adapter["review"]["environment"]

    assert environment["set"]["CONFIG__MAX_MODEL_TOKENS"] == "1000000"
    assert environment["set"]["CONFIG__PROPAGATE_TOOL_ERRORS"] == "true"
    assert "get_max_tokens" in environment["note"] and "32000" in environment["note"]
    assert "exited 0 with no answer file" in adapter["notes"]
    assert "raise SystemExit(run())" in adapter["notes"]
    assert "PyPI 0.45.0 wheel" in adapter["disclaimer"]


def test_cursor_agents_command_was_reread_as_agent_on_the_parameters_page():
    adapter = A.load_adapter("cursor-agent")

    assert "re-read 2026-10-16" in adapter["verified_source"]
    assert "`agent`" in adapter["verified_source"] and "as declared here" in adapter["verified_source"]
    assert A.adapter_binaries(adapter) == ["agent", "cursor-agent"]


def test_a_journal_lease_reads_the_wall_clock_and_says_why():
    note = inspect.getdoc(journal.claim)

    assert "wall clock" in note and "re-verifies" in note and "reboot" in note


def test_the_measurements_were_recorded_without_overclaiming_verification():
    """A measurement written beside a note is not an adoption run: no edited adapter's verified level moved."""
    levels = {ident: A.load_adapter(ident).get("verified")
              for ident in ("cloud-generic", "gemini", "qoder", "kiro", "codex", "pr-agent", "cursor-agent")}

    assert levels == {"cloud-generic": "partial", "gemini": "untested", "qoder": "partial",
                      "kiro": "full", "codex": "partial", "pr-agent": "untested",
                      "cursor-agent": "documented"}
