import os
import sys
import time

import pytest

from ao import cli, lib as A
from tests.test_review_chain import _args, _fake, _repo_with_change
from tests.test_reviewer_identity import _commit_ok

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")


def _reviews(root):
    d = os.path.join(root, "semantic-review")
    return sorted(f for f in os.listdir(d) if not f.startswith("."))


def test_a_fallback_running_the_implementers_engine_is_not_run(project, capsys):
    cfg = dict(project, reviewer={"id": "primary", "family": "x", "argv": _fake("nope", exit_code=17),
                                  "fallbacks": [{"argv": ["kiro-cli", "chat", "--no-interactive", "{prompt}"]}]})

    assert cli._reviewer_ineligible(cfg, cfg["reviewer"]["fallbacks"][0]) == \
        "it runs the implementer's own engine (kiro-cli)"
    assert cli._reviewer_ineligible(cfg, cfg["reviewer"]) is None


def test_a_primary_the_rule_refuses_is_refused_before_anything_runs(project, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "own", "argv": ["kiro-cli", "chat", "{prompt}"]})

    assert cli.cmd_review(cfg, _args()) == 2
    assert "own engine (kiro-cli)" in capsys.readouterr().out
    assert _reviews(root) == []


def test_declared_families_decide_for_one_engine_with_many_models(project):
    implementer = dict(project["implementer"], adapter="opencode", family="anthropic")
    other = {"id": "gpt", "family": "openai", "argv": ["opencode", "run", "-m", "gpt-5", "{prompt}"]}
    same = {"id": "sonnet", "family": "Anthropic", "argv": ["claude", "-p", "{prompt}"]}
    cfg = dict(project, implementer=implementer)

    assert cli._reviewer_ineligible(cfg, other) is None
    assert cli._reviewer_ineligible(cfg, same) == "it declares the implementer's model family (anthropic)"


def test_the_probe_applies_the_same_rule(project):
    cfg = dict(project, reviewer={"id": "own", "argv": ["kiro-cli", "chat", "{prompt}"]})

    probe = cli._reviewer_probe(cfg)

    assert probe["ok"] is False and probe["kind"] == "configuration-error"
    assert "own engine" in probe["reason"]


def test_an_approval_by_a_route_the_rule_refuses_grants_nothing(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    assert cli.cmd_review(dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)}),
                          _args()) == 0

    # The id the review recorded now names a route that runs the implementer's engine.
    moved = dict(project, reviewer={"id": "r1", "argv": ["kiro-cli", "chat", "{prompt}"]})
    code, out = _commit_ok(moved, monkeypatch, capsys)

    assert code == 1
    assert "may not review this implementer: it runs the implementer's own engine (kiro-cli)" in out


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows uses taskkill /T")
def test_a_reviewer_that_times_out_takes_what_it_started_with_it(project, tmp_path):
    marker = tmp_path / "child.pid"
    script = ("import subprocess, sys, time; "
              f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
              f"open({str(marker)!r}, 'w').write(str(child.pid)); time.sleep(120)")

    attempt = cli._run_reviewer(project["root"], [sys.executable, "-c", script], timeout=3)

    assert attempt["kind"] == "timeout"
    child = int(marker.read_text())
    for _ in range(50):
        if not A._pid_alive(child):
            break
        time.sleep(0.1)
    assert not A._pid_alive(child)


def test_a_prompt_one_argument_cannot_carry_is_refused_before_anything_runs(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    monkeypatch.setattr(cli, "_reviewer_argv_limit", lambda: 100)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})

    assert cli.cmd_review(cfg, _args()) == 2
    assert "one argument on" in capsys.readouterr().out
    assert _reviews(root) == []


def test_a_review_is_recorded_before_its_file_so_a_lost_write_refuses(project):
    root = project["root"]
    evidence = {"schema": 2, "kind": "index-candidate", "authorizable": True, "slice": "S1",
                "candidate": {"digest": "sha256:c9"}, "verdict": "APPROVED"}
    name = "2026-09-16-120000-lost.md"
    blocker = os.path.join(root, "semantic-review", name)
    os.makedirs(os.path.join(blocker, "in-the-way"))

    with pytest.raises(OSError):
        A.write_review_artefact(root, "semantic-review", name, "VERDICT: APPROVED\n",
                                evidence=evidence, verdict="APPROVED")

    decision = A.candidate_review_decision(root, "semantic-review", "sha256:c9")
    assert decision["match"] is None and "cannot be read" in decision["problem"]
