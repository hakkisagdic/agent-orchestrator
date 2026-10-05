import json
import os
import sys
from types import SimpleNamespace

from ao import allowlist as AL, cli


def _bare(root):
    json.dump({"project": "proj", "round_budget": 5}, open(os.path.join(root, ".ao", "config.json"), "w",
                                                             encoding="utf-8"))


def test_the_reviewer_ao_writes_starts_no_mcp_server_on_either_path(project):
    root = project["root"]
    for reviewer_model in (None, "claude-sonnet-5"):
        _bare(root)
        args = SimpleNamespace(profile="claude-kiro", implementer=None, model=None, effort=None,
                               reviewer_model=reviewer_model)
        cli._apply_profile(root, args)
        argv = json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))["reviewer"]["argv"]

        assert "--strict-mcp-config" in argv and not any(arg.startswith("--mcp-config") for arg in argv)
        assert AL.reviewer_problems(argv) == []


def test_a_reviewer_that_can_start_servers_or_write_is_named():
    assert AL.reviewer_problems(["claude", "-p", "{prompt}", "--allowedTools", ""]) == [
        "it starts every configured MCP server (no --strict-mcp-config)"]
    assert AL.reviewer_problems(["claude", "-p", "{prompt}", "--strict-mcp-config", "--mcp-config", "x.json",
                                 "--allowedTools", "Read,Edit,Bash(git:*)"]) == [
        "its allowed tools go beyond reading: Bash, Edit", "it loads MCP servers from --mcp-config"]
    assert AL.reviewer_problems(["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", "{prompt}"]) == [
        "it is granted every tool"]
    assert AL.reviewer_problems(["kiro-cli", "chat", "--no-interactive", "--trust-tools=", "{prompt}"]) == []


def test_the_doctor_names_a_reviewer_that_reaches_beyond_reading(project):
    cfg = dict(project, reviewer={"id": "open-reviewer", "argv": ["claude", "-p", "{prompt}"],
                                  "fallbacks": [{"id": "fallback-writer",
                                                 "argv": ["claude", "-p", "{prompt}", "--strict-mcp-config",
                                                          "--allowedTools", "Write"]}]})

    problems = dict(cli._actor_grant_problems(cfg))

    assert "no --strict-mcp-config" in problems["reviewer-tools:open-reviewer"]
    assert "beyond reading: Write" in problems["reviewer-tools:fallback-writer"]


# ---- a reviewer route that could write is refused where it is started, not only named (REVIEWER-REACH) ---------

import pytest  # noqa: E402

from ao import lib as A, storage  # noqa: E402
from tests.test_review_chain import _args, _fake, _repo_with_change  # noqa: E402

CODEX = ["codex", "exec", "{prompt}", "--ignore-user-config"]
CLAUDE = ["claude", "-p", "{prompt}", "--strict-mcp-config"]
WIDER = [
    CODEX + ["--sandbox", "workspace-write"],
    CODEX + ["--sandbox=danger-full-access"],
    CODEX + ["--dangerously-bypass-approvals-and-sandbox"],
    CODEX + ["--approve-for-me"],
    CODEX + ["--not-so-yolo"],
    CODEX + ["--yolo"],
    A.compose_reviewer("codex", model="m")["argv"] + ["--sandbox", "danger-full-access"],
    CLAUDE + ["--permission-mode", "bypassPermissions"],
    CLAUDE + ["--dangerously-skip-permissions"],
]


@pytest.mark.parametrize("argv", WIDER, ids=lambda argv: " ".join(argv[3:]))
def test_a_reviewer_route_that_could_write_is_refused_not_started(project, monkeypatch, argv):
    """Only `ao doctor --check` named such a route; a review started it and recorded what it answered."""
    started = []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (f"/agents/{name}", "0.160.0"))
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kw:
                        started.append(argv) or {"ok": True, "out": "VERDICT: APPROVED"})
    route = {"id": "rv", "argv": argv}

    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, "review this", 60, False, route)

    assert started == []
    assert attempt["kind"] == "configuration-error" and attempt["retryable"] is False
    assert attempt["reason"].startswith("a reviewer must not be able to write: ")


def test_a_review_whose_only_reviewer_could_write_approves_nothing_and_a_composed_one_still_runs(
        project, monkeypatch):
    root = project["root"]
    _repo_with_change(root)
    fake = _fake("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")
    started, real = [], cli._run_reviewer
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (fake[0], "0.160.0"))
    # The harness is a fake that answers APPROVED; the flags ao handed it are kept for the assertion.
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kw:
                        started.append(argv[1:]) or real(root, fake[:3] + ["x"], timeout, fallback, **kw))
    writing = {"id": "rv", "adapter": "codex", "family": "openai", "argv": CODEX + ["--sandbox", "workspace-write"]}

    code = cli.cmd_review(dict(project, reviewer=writing), _args())
    row = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)[-1]
    assert started == [] and row["verdict"] == "UNAVAILABLE" and code == 3

    composed = dict(A.compose_reviewer("codex", model="m", family="openai"), id="rv")
    code = cli.cmd_review(dict(project, reviewer=composed), _args())
    row = storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)[-1]
    assert len(started) == 1 and row["verdict"] == "APPROVED" and code == 0


def test_a_second_sandbox_flag_after_the_pinned_one_is_named():
    composed = A.compose_reviewer("codex", model="m")["argv"]
    assert AL.reviewer_problems(composed + ["--sandbox", "danger-full-access"]) == \
        ["it runs with --sandbox danger-full-access, where ao pins read-only"]


# ---- a route without the flags its adapter declares leave it only reading is refused too (REVIEWER-REACH-2) ----

# Each runs a reviewer-eligible adapter's command without its `options.trust_none` and names nothing
# `reviewer_problems` reads: no grant of every tool, no allowlist, no pinned flag, no bypass spelling.
WITHOUT_TRUST_NONE = {
    "kiro": ["kiro-cli", "chat", "--no-interactive", "{prompt}"],
    "kiro trusting the shell": ["kiro-cli", "chat", "--no-interactive", "--trust-tools=fs_write,execute_bash",
                                "{prompt}"],
    "qoder": ["qodercli", "-p", "{prompt}"],
    "qoder accept_edits": ["qodercli", "-p", "{prompt}", "--permission-mode", "accept_edits"],
    "qwen": ["qwen", "-p", "{prompt}"],
    "qwen auto-edit": ["qwen", "-p", "{prompt}", "--approval-mode", "auto-edit"],
    "hermes": ["hermes", "-z", "{prompt}"],
    "kilocode": ["kilo", "run", "{prompt}"],
    "omp": ["omp", "-p", "{prompt}"],
    "pi": ["pi", "-p", "{prompt}"],
    "reasonix": ["reasonix", "-p", "{prompt}"],
    "reasonix workspace-write": ["reasonix", "-p", "{prompt}", "--permission-mode=workspace-write"],
    "codex without --ignore-rules": CODEX,
}
ELIGIBLE = ("claude-code", "codex", "hermes", "kilocode", "kiro", "omp", "pi", "qoder", "qwen", "reasonix")


def _spawned(monkeypatch):
    started = []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: (f"/agents/{name}", "1.0.0"))
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, fallback=False, **kw:
                        started.append(argv) or {"ok": True, "out": "VERDICT: APPROVED"})
    return started


@pytest.mark.parametrize("argv", list(WITHOUT_TRUST_NONE.values()), ids=list(WITHOUT_TRUST_NONE))
def test_a_route_without_its_adapters_trust_none_is_refused_not_started(project, monkeypatch, argv):
    """`pi -p` and `omp -p` edit and run commands with none of it; only a pin was asked of a route."""
    started = _spawned(monkeypatch)
    route = {"id": "rv", "argv": argv}

    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, "review this", 60, False, route)

    assert started == []
    assert attempt["kind"] == "configuration-error" and attempt["retryable"] is False
    assert attempt["reason"].startswith("a reviewer must not be able to write: ")


@pytest.mark.parametrize("ident", ELIGIBLE)
def test_a_composed_route_of_every_eligible_adapter_still_runs(project, monkeypatch, ident):
    started = _spawned(monkeypatch)
    route = A.compose_reviewer(ident, model="m")

    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, "review this", 60, False, route)

    assert attempt["ok"] and len(started) == 1


def test_a_route_of_a_program_no_adapter_runs_is_still_its_own(project, monkeypatch):
    started = _spawned(monkeypatch)
    route = {"id": "rv", "argv": [sys.executable, "-c", "print('VERDICT: APPROVED')", "{prompt}"]}

    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, "review this", 60, False, route)

    assert attempt["ok"] and len(started) == 1


# A flag of trust_none given again with another value, which a harness that takes the last of two runs with.
OVERRIDDEN = {
    "kiro": ["kiro-cli", "chat", "--no-interactive", "--trust-tools=", "--trust-tools=fs_write,execute_bash",
             "{prompt}"],
    "qwen": ["qwen", "-p", "{prompt}", "--approval-mode", "plan", "--approval-mode", "auto-edit"],
    "kilocode": ["kilo", "run", "{prompt}", "--agent", "ask", "--agent", "code"],
    "hermes": ["hermes", "-z", "{prompt}", "--toolsets", "safe", "--toolsets", "terminal"],
    "pi": ["pi", "-p", "{prompt}", "--tools", "read,grep,find,ls", "--tools", "bash"],
    "reasonix": ["reasonix", "-p", "{prompt}", "--permission-mode=read-only", "--permission-mode=workspace-write"],
    "qoder": ["qodercli", "-p", "{prompt}", "--tools", "Read,Grep,Glob", "--permission-mode", "dont_ask",
              "--strict-mcp-config", "--permission-mode", "accept_edits"],
}


@pytest.mark.parametrize("argv", list(OVERRIDDEN.values()), ids=list(OVERRIDDEN))
def test_a_trust_none_flag_given_again_with_another_value_is_refused(project, monkeypatch, argv):
    started = _spawned(monkeypatch)
    route = {"id": "rv", "argv": argv}

    _, _, _, attempt = cli._reviewer_route_invocation(project["root"], route, "review this", 60, False, route)

    assert started == [] and attempt["kind"] == "configuration-error"
    assert "leaves it only reading" in attempt["reason"]


def test_a_narrower_list_than_trust_none_names_is_no_override():
    assert A.reading_problems(["pi", "-p", "{prompt}", "--tools", "read,grep,find,ls", "--tools", "read"]) == []


def test_a_reading_flag_given_after_the_end_of_options_or_with_more_values_does_not_count():
    """REVIEWER-REACH-3: a token after `--` is no option, though the check found it there; and a variadic flag given a
    second word - `--tools Read,Grep,Glob Edit` - takes it as a tool, where the check read only the first."""
    composed = A.compose_reviewer("qoder", model="m")["argv"]
    at = composed.index("--tools")
    after_end = composed[:at] + ["--"] + composed[at:]
    widened = composed[:at + 2] + ["Edit,Write"] + composed[at + 2:]

    assert any("lacks" in p for p in A.reading_problems(after_end)), after_end
    assert any("more than one value" in p for p in A.reading_problems(widened)), widened
    assert A.reading_problems(composed) == []



def test_a_reading_flag_given_again_with_an_equals_sign_and_a_second_word_does_not_count():
    """REVIEWER-REACH-4: yargs reads `--tools=Read,Grep,Glob Edit` as two values, as it reads the form with a space;
    only the space form was asked, so appending it to a composed route passed."""
    composed = A.compose_reviewer("qoder", model="m")["argv"]

    assert A.reading_problems(composed + ["--tools=Read,Grep,Glob"]) == []
    assert any("more than one value (Read,Grep,Glob Edit)" in p
               for p in A.reading_problems(composed + ["--tools=Read,Grep,Glob", "Edit"]))
    assert any("more than one value ((empty) fs_write)" in p for p in A.reading_problems(
        ["kiro-cli", "chat", "--no-interactive", "{prompt}", "--trust-tools=", "fs_write"]))


def test_ao_s_own_prompt_after_a_reading_flag_is_no_second_value():
    """REVIEWER-REACH-4: kiro takes its prompt as the word after its options, and a route may put it there."""
    assert A.reading_problems(["kiro-cli", "chat", "--no-interactive", "--trust-tools=", "{prompt}"]) == []
    assert A.reading_problems(["kiro-cli", "chat", "--no-interactive", "--trust-tools=", "review this"],
                              prompt="review this") == []
