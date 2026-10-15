"""Pre-review found prompt text read as grants and retention bypassed two chains.

Lane retirement also needs the lane's own prepared-file rules at each status
recheck, while uncommitted user work must still be kept (TRIAGE-3-MEDIUM).
"""
from pathlib import Path

import pytest

from ao import allowlist as AL, cli, lib as A, storage
from tests.scenarios import World
from tests.test_lanes import _board, _git
from tests.test_review_retention import _artefact

PROMPT = "Review the change, Bash(git commit:*), Write, and explain why those words are data."


@pytest.mark.parametrize("flag", ["--allowedTools", "--allowedTools="])
def test_prompt_after_variadic_tools_is_data_at_reviewer_start(project, monkeypatch, flag):
    argv = A.compose_reviewer("claude-code", model="m")["argv"]
    argv = [word for word in argv if word != "{prompt}"]
    argv += ([flag, "Read,Grep,Glob"] if not flag.endswith("=") else [flag + "Read,Grep,Glob"]) + ["{prompt}"]
    route = {"id": "rv", "argv": argv}
    started = []
    monkeypatch.setattr(cli, "_reviewer_resolve_binary", lambda root, name: ("/agents/claude", "2.1.261"))
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, *a, **kw:
                        started.append(argv) or {"ok": True, "out": "VERDICT: APPROVED"})
    attempt = cli._reviewer_route_invocation(project["root"], route, PROMPT, 60, False, route)[-1]
    assert attempt["ok"] and started


@pytest.mark.parametrize("flag", ["--allowedTools", "--allowedTools="])
def test_prompt_after_variadic_tools_is_data_for_turn_checks(project, flag):
    argv = ["claude", "-p"] + ([flag, "Read"] if not flag.endswith("=") else [flag + "Read"]) + [PROMPT]
    assert AL.problems(argv, role="architect", prompt=PROMPT) == []
    assert A.turn_refusal("architect", "claude-code", argv, prompt=PROMPT) is None


@pytest.mark.parametrize("flag, tool", [
    ("--allowedTools=", "Bash(git commit:*)"),
    ("--allowed-tools=", "Bash(git commit:*)"),
    ("--trust-tools", "Bash"),
    ("--trust-tools=", "execute_bash"),
])
def test_every_variadic_value_is_audited(project, flag, tool):
    argv = ["claude"] + ([flag, "Read"] if not flag.endswith("=") else [flag + "Read"]) + [tool]
    assert AL.problems(argv, role="architect")
    if flag.startswith("--allowed"):
        assert AL.reviewer_problems(argv)
    else:
        assert AL.trusted_tools(argv) == ["Read", tool]


def test_watchdog_forwards_the_expanded_prompt_to_turn_checks(project, tmp_path, monkeypatch):
    world = World(project, monkeypatch, tmp_path)
    world.transcript_age(900)
    world.mail("20260916-1200-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## BLOCKED\n")
    checked = []
    original = A.turn_refusal
    def check(role, ident, argv, **kwargs):
        if role == "architect" and any("prompt" in word.lower() or "\n" in word for word in argv):
            checked.append(kwargs.get("prompt"))
        return original(role, ident, argv, **kwargs)
    monkeypatch.setattr(A, "turn_refusal", check)
    world.cycle(dry_run=False)
    assert checked and all(isinstance(value, str) and value for value in checked)


@pytest.mark.parametrize("kind", ["waiver", "decision"])
@pytest.mark.parametrize("damage", ["empty", "missing", "broken"])
def test_prune_validates_all_reference_chains(project, kind, damage):
    root = project["root"]
    artefact = Path(_artefact(root, "kept.md"))
    path, chain = ((A.waivers_path(root), A.WAIVER_CHAIN) if kind == "waiver"
                   else (A.decisions_path(root), A.DECISION_CHAIN))
    storage.append_chained_jsonl(path, {"review": "kept.md"}, chain, legacy_prefix=True)
    if damage == "missing":
        Path(path).unlink()
    else:
        Path(path).write_text("" if damage == "empty" else '{"_previous": "wrong"}\n', encoding="utf-8")
    with pytest.raises((OSError, RuntimeError, ValueError)):
        A.prune_review_artefacts(root, project, 30, apply=True)
    assert artefact.exists()


@pytest.mark.parametrize("kind", ["verification", "waiver", "decision"])
def test_reference_chains_keep_their_legacy_prefix(project, kind):
    root = project["root"]
    artefact = Path(_artefact(root, "kept.md"))
    path, chain = {
        "verification": (str(Path(root, ".ao", "ledger", "verifications.jsonl")), A.VERIFICATION_CHAIN),
        "waiver": (A.waivers_path(root), A.WAIVER_CHAIN),
        "decision": (A.decisions_path(root), A.DECISION_CHAIN),
    }[kind]
    Path(path).write_text('{"review": "kept.md"}\n', encoding="utf-8")
    storage.append_chained_jsonl(path, {"id": "newer"}, chain, legacy_prefix=True)
    result = A.prune_review_artefacts(root, project, 30, apply=True)
    assert artefact.exists() and kind in result["kept"]["kept.md"]


def test_lane_removal_reuses_its_prepared_file_rules(project):
    root = project["root"]
    _board(root)
    cfg = dict(project, lane={"env": ["PORT=3001"]})
    record = A.start_lane(root, cfg, "B")
    lane = Path(record["path"])
    _git(lane, "commit", "-q", "--allow-empty", "-m", "unmerged lane work")
    tip = _git(lane, "rev-parse", "HEAD")
    # Losing Git's exclude lines must not turn ao's own env file into user work (TRIAGE-3-MEDIUM).
    exclude = Path(A._git_output(lane, "rev-parse", "--git-path", "info/exclude").decode().strip())
    if not exclude.is_absolute():
        exclude = lane / exclude
    exclude.write_text("", encoding="utf-8")
    assert A.lane_changes(str(lane), record) == []
    A.remove_lane(root, "B")
    assert not lane.exists() and A.lane_record(root, "B") is None
    assert tip in _git(root, "for-each-ref", "--format=%(objectname)", "refs/ao/archive/").split()
