"""Qoder's command surface is the CLI's, measured on qodercli, not the desktop store's guesses.

The adapter was first written from a running Qoder store whose send, resume, options and mcp
blocks had never been run because no CLI was present. qodercli 1.1.59 was then run on
2026-09-21, and those blocks now describe what the CLI does. This reads them back: the argv ao
builds its turns from, the grant it appends to a turn nobody attends, the model and session
listings, the MCP registration, and the read-only route a composed reviewer takes - so a later
edit that drops a measured flag or reopens a bypass is caught here rather than in a live turn.
"""
from ao import allowlist as AL, lib as A


def _after(argv, flag):
    return argv[argv.index(flag) + 1]


def test_qoder_drives_the_cli_it_names_and_resumes_a_session_by_id():
    adapter = A.load_adapter("qoder")

    assert adapter["send"]["argv"] == ["qodercli", "-p", "{prompt}"]
    assert adapter["resume"]["argv"] == ["qodercli", "-r", "{session}", "-p", "{prompt}"]
    assert adapter["detect"]["binaries"] == ["qodercli"]


def test_qoder_maps_claude_codes_pinned_mode_and_tools_onto_its_own_flags():
    options = A.load_adapter("qoder")["options"]

    assert options["model"] == ["--model", "{model}"]
    assert options["output_json"] == ["-o", "stream-json"]
    assert options["effort"] == ["--reasoning-effort", "{effort}"]
    assert options["trust_none"] == ["--tools", "Read,Grep,Glob", "--permission-mode", "dont_ask",
                                     "--strict-mcp-config"]
    assert options["trust_all"] == ["--dangerously-skip-permissions"]
    unattended = options["unattended"]
    assert unattended[:3] == ["--permission-mode", "accept_edits", "--allowed-tools"]
    assert _after(unattended, "--permission-mode") == "accept_edits"
    granted = _after(unattended, "--allowed-tools").split(",")
    assert granted[0] == "Bash(ao status:*)" and all(rule.startswith("Bash(") for rule in granted)
    assert not {"Read", "Grep", "Glob", "Write", "Edit"} & set(granted)
    assert "Bash(ao:*)" not in granted


def test_qoders_implementer_grant_is_the_resume_with_the_unattended_scope_appended():
    adapter = A.load_adapter("qoder")
    resume = adapter["resume"]["argv"]

    flags, _ = A.unattended_flags(adapter, resume)
    assert flags == adapter["options"]["unattended"]
    assert A.role_commands(adapter)["implementer"] == resume + flags
    assert AL.problems(resume, adapter["options"]) == []


def test_a_composed_qoder_reviewer_reads_and_starts_no_server():
    adapter = A.load_adapter("qoder")

    assert A.reviewer_eligibility(adapter) == (True, None)
    assert A.compose_reviewer("qoder", model="m")["argv"] == [
        "qodercli", "-p", "{prompt}", "--model", "m", "--tools", "Read,Grep,Glob",
        "--permission-mode", "dont_ask", "--strict-mcp-config"]
    assert A.role_commands(adapter)["reviewer"] == [
        "qodercli", "-p", "{prompt}", "--tools", "Read,Grep,Glob", "--permission-mode", "dont_ask",
        "--strict-mcp-config"]
    assert AL.reviewer_problems(A.compose_reviewer("qoder", model="m")["argv"]) == []


def test_qoder_lists_models_by_argv_and_marks_the_session_listing_unrun():
    adapter = A.load_adapter("qoder")

    assert adapter["list_models"]["argv"] == ["qodercli", "--list-models"]
    assert adapter["list_sessions"]["argv"] == ["qodercli", "--list-sessions"]
    assert adapter["list_sessions"].get("unverified")


def test_qoder_pins_its_default_and_review_model_to_a_family_named_model_not_a_tier():
    models = A.load_adapter("qoder")["models"]

    assert models["default"] == models["review"] == "Qwen3.8-Flash"
    assert "family" in models["_note"] and "tier" in models["_note"]


def test_qoder_registers_ao_as_a_local_mcp_server_and_warns_off_the_runtime_key_file():
    mcp = A.load_adapter("qoder")["mcp"]

    assert mcp["file"] == ".mcp.json"
    assert mcp["register"] == ["qodercli", "mcp", "add", "-s", "local", "ao", "{exe}", "{args}"]
    assert "approval" in mcp["note"]
    assert "mcp-router.json" in mcp["warning"] and "key" in mcp["warning"]


def test_qoders_escaped_cwd_makes_a_directory_name_of_every_non_letter_and_digit():
    note = A.load_adapter("qoder")["transcript"]["escaped_cwd"]

    assert "not a letter or a digit" in note and "-private-tmp-a-b-c" in note


def test_a_prompt_too_big_for_an_argument_reaches_qoder_on_standard_input():
    """A prompt past what an argument carries has a route, because the CLI's own channel is declared.

    Measured on qodercli 1.1.59: `-p` with no prompt argument reads standard input, and an argument
    carried 4, 8, 16, 24 and 32 KB without complaint - so the channel is what the platform's limit
    needs, not a workaround for a size this CLI refuses.
    """
    adapter = A.load_adapter("qoder")

    assert A.prompt_channel_problems(adapter) == []
    for capability in ("send", "resume"):
        channel = adapter[capability]["stdin"]
        assert channel["replaces"] == ["{prompt}"] and channel["with"] == []
    small, huge = A.prompt_plan(adapter["send"]["argv"], "x" * 24_000, "qoder")[0], \
        A.prompt_plan(adapter["send"]["argv"], "x" * 2_000_000, "qoder")[0]
    assert small["channel"] == "argument"      # today's policy: an argument until the platform says no
    assert huge["channel"] == "stdin" and "{prompt}" not in huge["argv"]
