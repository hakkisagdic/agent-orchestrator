"""Someone who runs one harness reviews in tiers, each recorded and labeled (REVIEW-TIERS).

ao lands code only after a review by a model family other than the one that wrote it, and a
single-harness user could not meet that rule. `ao init --profile claude-claude`, which the
documents recommended, was refused as a reviewer "via unresolved binary" with the binary on
PATH; `ao role set reviewer claude-code --model m1` answered "assigned" and `ao review` then
refused; no person could review a candidate; and the one configuration that worked, a
reviewer with no implementer, compared the reviewer with nobody while `ao doctor` passed.

Three tiers now, decided by one rule: another model family, the default; another model of
the implementer's family, only where a person opted in on the record, labeled "same family:
weaker independence" wherever it shows; and a person's own review of the bytes shown to them.
"""
import json
import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, lib as A, matrix as M, storage, tiers as T
from ao import watchdog as W
from tests.test_capability_matrix import _strict_config
from tests.test_catchup_ready import APPROVED, _catchup, _land, _legacy_waiver
from tests.test_commit_authority import _allow_commit_prerequisites
from tests.test_profiles import _init_args
from tests.test_prove import PROBE_OR_APPROVE
from tests.test_review_chain import _args, _fake, _repo_with_change

PERSON = "A. Person"


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _write(root, cfg):
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)
    return A.load_config(root)


def _running(root, item):
    board = os.path.join(root, ".ao", "board.md")
    with open(board, encoding="utf-8") as fh:
        text = fh.read()
    with open(board, "w", encoding="utf-8") as fh:
        fh.write(text.replace("## running\n", f"## running\n- {item}\n"))


def _same_family(project, opted=True):
    """An implementer and a reviewer declaring one family and naming two models; a person opted in, or not."""
    implementer = dict(project["implementer"], family="writer-family", model="model-a")
    reviewer = {"id": "r-same", "family": "writer-family", "model": "model-b",
                "argv": [sys.executable, "-c", PROBE_OR_APPROVE, "{prompt}"]}
    cfg = dict(project, implementer=implementer, reviewer=reviewer)
    if opted:
        A.record_opt_in(project["root"], "review.same_family", "labeled", PERSON)
        cfg["review"] = {"same_family": "labeled"}
    return cfg


def _commit_ok(cfg, monkeypatch, capsys, review=None):
    root = cfg["root"]
    _allow_commit_prerequisites(monkeypatch, A.tree_digest(root, cfg), A.index_candidate(root))
    capsys.readouterr()
    code = cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None, review=review))
    return code, _plain(capsys)


def _person(cfg, capsys, **given):
    args = dict(by=PERSON, verdict=None, digest=None, findings=None, boundary=None, paths=None, commits=None)
    args.update(given)
    capsys.readouterr()
    code = cli.cmd_person_review(cfg, SimpleNamespace(**args))
    return code, _plain(capsys)


def _digest(shown):
    return re.search(r"--digest ([0-9a-f]{16})", shown).group(1)


def _rows(root):
    return storage.read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)


def _artefact(cfg, row):
    with open(os.path.join(cfg["root"], cfg["reviews"], row["artefact"]), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("reviewer,author,same_family,expected", [
    ({"person": True, "identity": True}, {"known": True}, False, (T.PERSON, None)),
    ({"identity": True, "family": "b"}, {"known": True, "family": "a"}, True, (None, "author")),
    ({"family": "b"}, {"known": True, "family": "a"}, False, (T.INDEPENDENT, None)),
    ({"family": "a", "declared": "a", "model": "m2"}, {"known": True, "family": "a", "declared": "a", "model": "m1"},
     False, (None, "family")),
    ({"family": "a", "declared": "a", "model": "m2"}, {"known": True, "family": "a", "declared": "a", "model": "m1"},
     True, (T.SAME_FAMILY, None)),
    ({"family": "a", "declared": "a", "model": "M1"}, {"known": True, "family": "a", "declared": "a", "model": "m1"},
     True, (None, "family/model")),
    ({"engine": "e", "declared": "a"}, {"known": True, "engines": ["e"], "declared": "a", "model": "m1"},
     True, (None, "engine/model-unnamed")),
    ({"engine": "e"}, {"known": True, "engines": ["e"]}, True, (None, "engine/families")),
    ({"engine": "other"}, {"known": True, "engines": ["e"]}, False, (T.INDEPENDENT, None)),
    ({"family": "person", "declared": "person"}, {"known": True, "family": "a"}, True, (None, "person-family")),
    ({"declared": "a"}, {"range": True, "families": ["a"]}, True, (None, "author-family")),
    ({"declared": "b"}, {"range": True, "families": ["a"]}, True, (T.INDEPENDENT, None)),
    ({"family": "b"}, {}, False, (None, None)),
])
def test_one_function_decides_the_tier_and_names_why_none_holds(reviewer, author, same_family, expected):
    assert T.tier(reviewer, author, same_family=same_family) == expected


def test_a_persons_review_is_bound_to_the_bytes_shown_and_grants_as_a_models_does(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    # A project that runs one harness and reviews as a person: no model reviewer at all.
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "s1", "name": "claude", "model": "model-a"})

    code, shown = _person(cfg, capsys)
    assert code == 0 and "+x = 2" in shown and "--verdict APPROVED --digest" in shown
    assert _rows(root) == []                                    # showing records nothing
    digest = _digest(shown)

    assert _person(cfg, capsys, verdict="APPROVED")[0] == 2     # a verdict names the digest it was given
    code, out = _person(cfg, capsys, verdict="APPROVED", digest="0" * 16)
    assert code == 1 and "not the one you read" in out
    code, out = _person(cfg, capsys, verdict="APPROVED", digest=digest)
    assert code == 0 and T.LABELS[T.PERSON] in out

    (row,) = _rows(root)
    assert (row["reviewer"], row["tier"], row["verdict"], row["authorizable"]) == \
        (f"person:{PERSON}", T.PERSON, "APPROVED", True)
    body = _artefact(cfg, row)
    evidence = A.review_evidence(body)
    assert evidence["diff_digest"].startswith("sha256:" + digest) and evidence["transport"] == "person"
    assert evidence["person"]["by"] == PERSON and set(evidence["person"]) == {"by", "user", "interactive"}
    assert "- tier: person review" in body.splitlines()

    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 0 and "GRANTED" in out and f"tier person review ({PERSON}, login" in out
    assert A.latest_authority_decision(root)["reviewer"] == f"person:{PERSON}"

    # Bytes staged after the reading are not the bytes read.
    with open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 3\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)
    code, out = _person(cfg, capsys, verdict="APPROVED", digest=digest)
    assert code == 1 and "not the one you read" in out and len(_rows(root)) == 1


def test_counts_come_from_the_findings_and_a_persons_approval_supersedes_no_rejection_of_the_same_bytes(
        project, monkeypatch, capsys, tmp_path):
    root = project["root"]
    _repo_with_change(root)
    rejected = ("VERDICT: NEEDS_CHANGES", "BLOCKER: 1", "HIGH: 0", "MEDIUM: 0", "LOW: 0", "- [BLOCKER] src/a.py:1 - no")
    assert cli.cmd_review(dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*rejected)}), _args()) == 1
    digest = _digest(_person(project, capsys)[1])
    findings = tmp_path / "findings.txt"

    findings.write_text("- [HIGH] src/a.py:1 - the value is wrong\nVERDICT: APPROVED\n", encoding="utf-8")
    code, out = _person(project, capsys, verdict="APPROVED", digest=digest, findings=str(findings))
    assert code == 2 and "make it NEEDS_CHANGES" in out
    findings.write_text("- [LOW] src/a.py:1 - name it better\n", encoding="utf-8")
    code, out = _person(project, capsys, verdict="NEEDS_CHANGES", digest=digest, findings=str(findings))
    assert code == 2 and "at least one - [BLOCKER] or - [HIGH] line" in out

    assert _person(project, capsys, verdict="APPROVED", digest=digest, findings=str(findings))[0] == 0
    assert _rows(root)[-1]["fallback"] is True
    code, out = _commit_ok(project, monkeypatch, capsys)
    assert code == 1 and "is a person's approval; it cannot supersede the rejection" in out


def test_a_name_that_belongs_to_an_agent_or_a_role_is_refused_wherever_a_person_acts(project, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = _write(root, dict(project, actors={"kiro": dict(project["implementer"]),
                                             "lead2": {"name": "lead2", "argv": ["lead-cli", "{prompt}"]}},
                            roles={"implementer": "kiro"}))

    for name in ("kiro", "Implementer", "human", "fable", "lead2"):
        code, out = _person(cfg, capsys, by=name)
        assert code == 2 and "names an agent or a role" in out, name
    assert _rows(root) == []

    opt_in = SimpleNamespace(action="set", key="review.same_family", value="labeled", machine=False, by="kiro")
    assert cli.cmd_config(cfg, opt_in) == 2 and "names an agent or a role" in _plain(capsys)
    assert cli.cmd_config(cfg, SimpleNamespace(**dict(vars(opt_in), by=None))) == 2
    assert "--by is required" in _plain(capsys)
    assert cli.cmd_config(cfg, SimpleNamespace(**dict(vars(opt_in), by=PERSON, machine=True))) == 2
    assert not os.path.exists(A.opt_ins_path(root))

    # Both acts are a person's: no grant ao checks may admit them (#58).
    forbidden = [command for _, command in AL.FORBIDDEN]
    assert any(command.startswith("ao person-review") for command in forbidden)
    assert any(command.startswith("ao config set review.same_family") for command in forbidden)
    for rule in AL.rules(["claude", "-p", "{prompt}", "--allowedTools", A.load_adapter("claude-code")["options"]["architect_tools"]]):
        assert [command for command in forbidden if AL.admits(rule, command)] == [], rule


def test_a_reviewer_of_the_implementers_family_is_refused_in_one_sentence_by_role_set_the_probe_review_and_init(
        project, tmp_path, capsys):
    root = project["root"]
    _repo_with_change(root)
    implementer = {"adapter": "claude-code", "session": "s1", "name": "claude", "model": "model-a"}
    cfg = _write(root, dict(project, implementer=implementer))
    sentence = cli._tier_refusal("it runs the implementer's own engine (claude)")
    assert all(way in sentence for way in ("another model family", "review.same_family labeled", "ao person-review"))

    role = SimpleNamespace(action="set", role="reviewer", actor="claude-code", model="model-b", effort=None,
                           family=None, hotfix=False)
    assert cli.cmd_role(cfg, role) == 2
    assert sentence in _plain(capsys) and "reviewer" not in (A.load_config(root).get("roles") or {})

    reviewer = A.compose_reviewer("claude-code", model="model-b")
    probe = cli._reviewer_probe(dict(cfg, reviewer=reviewer))
    assert (probe["ok"], probe["kind"], probe["reason"]) == (False, "configuration-error", sentence)
    assert "unresolved binary" not in cli._reviewer_probe_text(probe)

    assert cli.cmd_review(dict(cfg, reviewer=reviewer), _args()) == 2
    assert sentence in _plain(capsys) and os.listdir(os.path.join(root, "semantic-review")) == []

    fresh = tmp_path / "single-harness"
    fresh.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fresh, check=True)
    assert cli.cmd_init({"root": str(fresh)}, _init_args(profile="claude-claude", agent="kiro")) == 1
    out = _plain(capsys)
    assert sentence in out and "unresolved binary" not in out
    assert "--review-tier same-family --by <name>" in out and "--review-tier person" in out
    assert not (fresh / ".ao").exists()


def test_another_model_of_the_implementers_family_reviews_once_a_person_opted_in_and_is_labeled_everywhere(
        project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    _running(root, "[S1] a single-harness slice · since: 2026-09-17 09:00")
    unopted = _same_family(project, opted=False)
    assert cli._reviewer_ineligible(unopted, unopted["reviewer"]) == \
        "it declares the implementer's model family (writer-family)"
    # Written by hand, which an agent that edits files can do, the setting is no opt-in.
    assert cli._reviewer_ineligible(dict(unopted, review={"same_family": "labeled"}), unopted["reviewer"])

    cfg = _same_family(project)
    assert cli._review_tier(cfg, cfg["reviewer"]) == (T.SAME_FAMILY, None)
    assert "own model (model-a)" in cli._reviewer_ineligible(cfg, dict(cfg["reviewer"], model="model-a"))
    assert cli._reviewer_probe_text(cli._reviewer_probe(cfg)).endswith(f"exact nonce echoed; {T.SAME_FAMILY_LABEL}")

    spawned = []
    monkeypatch.setattr(cli, "_spawn_review_run", lambda run_root, rid: spawned.append(rid))
    submit = SimpleNamespace(action="submit", rid=None, any=False, run=None, boundary="b", paths=None, commits=None)
    assert cli.cmd_review(cfg, submit) == 0
    (rid,) = spawned
    assert cli.cmd_review(cfg, SimpleNamespace(**dict(vars(submit), action=None, run=rid, boundary=None))) == 0
    capsys.readouterr()

    row = _rows(root)[-1]
    assert (row["tier"], row["reviewer"], row["slice"]) == (T.SAME_FAMILY, "r-same", "S1")        # the ledger row
    assert f"- tier: {T.SAME_FAMILY_LABEL}" in _artefact(cfg, row).splitlines()                    # the header
    assert cli.cmd_reviews(cfg, SimpleNamespace()) == 0
    assert re.search(rf"{rid}\s+finished.*APPROVED\s+{T.SAME_FAMILY_LABEL}", _plain(capsys))        # ao reviews
    code, out = _commit_ok(cfg, monkeypatch, capsys, review=rid)
    assert code == 0 and f"tier {T.SAME_FAMILY_LABEL}" in out                                       # ao commit-ok
    assert cli.cmd_stats(cfg, SimpleNamespace(all=False, since=None, until=None, slices=True)) == 0
    stats = _plain(capsys)                                                                          # ao stats
    assert f"1 slice(s) on {T.SAME_FAMILY_LABEL}" in stats
    assert re.search(rf"S1\s+APPROVED\s+{T.SAME_FAMILY_LABEL}", stats)

    # Withdrawn on the record, the opt-in leaves the review granting nothing.
    withdraw = SimpleNamespace(action="set", key="review.same_family", value="refused", machine=False, by=PERSON)
    assert cli.cmd_config(_write(root, cfg), withdraw) == 0
    assert A.recorded_opt_in(root, "review.same_family")["value"] == "refused"
    code, out = _commit_ok(A.load_config(root), monkeypatch, capsys)
    assert code == 1 and "r-same may not review this implementer" in out


def test_a_reviewer_of_another_family_reviews_and_grants_as_before_with_no_label(project, monkeypatch, capsys):
    root = project["root"]
    _repo_with_change(root)
    cfg = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})

    assert cli._review_tier(cfg, cfg["reviewer"]) == (T.INDEPENDENT, None)
    assert cli.cmd_review(cfg, _args()) == 0
    out = _plain(capsys)
    (row,) = _rows(root)
    body = _artefact(cfg, row)
    assert (row["tier"], row["fallback"]) == (T.INDEPENDENT, False) and T.SAME_FAMILY_LABEL not in out
    assert A.review_evidence(body)["reviewer"] == {"id": "r1", "family": "x", "fallback": False}
    assert "- tier: another model family" in body.splitlines()

    code, out = _commit_ok(cfg, monkeypatch, capsys)
    assert code == 0 and "GRANTED" in out and "weaker" not in out and T.LABELS[T.PERSON] not in out


def test_the_capability_matrix_admits_a_binding_of_the_implementers_family_only_under_the_opt_in(project):
    cfg = _strict_config(project, primary_family="writer-family")
    assert [(route["eligible"], route["tier"]) for route in cli._resolve_matrix(cfg)["reviewers"]] == \
        [(False, None), (True, T.INDEPENDENT)]

    A.record_opt_in(project["root"], "review.same_family", "labeled", PERSON)
    opted = dict(cfg, review={"same_family": "labeled"})
    assert [(route["eligible"], route["tier"]) for route in cli._resolve_matrix(opted)["reviewers"]] == \
        [(True, T.SAME_FAMILY), (True, T.INDEPENDENT)]
    # The matrix itself reads no ledger: without the caller's word the binding stays ineligible.
    assert M.resolve(opted)["reviewers"][0]["ineligible_reason"] == "same model family as implementer"


def test_catch_up_closes_a_waiver_on_a_persons_review_of_exactly_its_range_whatever_family_wrote_it(
        project, monkeypatch, capsys):
    monkeypatch.setattr(W, "run", lambda ns: 0)
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    waiver = _legacy_waiver(root, "B7")
    end = _land(root, "a.py", "value = 2\n", "b7")
    cfg = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _fake(*APPROVED)})

    # Nobody established the family that wrote the range, so no model may review it.
    assert _catchup(cfg, plan=True) == 0
    assert "ao person-review --commits <range> --by <name>" in _plain(capsys)

    short = f"{waiver['head'][:12]}..{end[:12]}"                   # as ao catchup --plan names it
    code, shown = _person(cfg, capsys, commits=short)
    assert code == 0 and "waived review for B7" in shown
    assert _person(cfg, capsys, commits=short, verdict="APPROVED", digest=_digest(shown))[0] == 0
    row = _rows(root)[-1]
    assert (row["kind"], row["commits"], row["tier"], row["slice"]) == \
        ("commit-range", f"{waiver['head']}..{end}", T.PERSON, "B7")

    recorded = len(_rows(root))
    assert _catchup(cfg) == 0
    assert f"closed on a person's review ({PERSON}, login" in _plain(capsys)
    assert A.open_waivers(root) == [] and len(_rows(root)) == recorded            # no reviewer was started
    closed = [item for item in A.waiver_rows(root) if item.get("event") == "closed"][-1]
    assert closed["outcome"] == "reviewed by a person: APPROVED" and closed["evidence"]["review"] == row["artefact"]


def test_the_doctor_names_the_tier_in_force_and_a_project_with_no_implementer_as_a_problem(
        project, monkeypatch, capsys):
    root = project["root"]
    other = dict(project, reviewer={"id": "r1", "family": "x", "argv": _fake(*APPROVED)})
    assert cli._active_review_tier(other) == ("another model family (r1)", [])
    assert cli._active_review_tier(project)[0].startswith("person review: no model reviewer is configured")

    unrecorded = dict(_same_family(project, opted=False), review={"same_family": "labeled"})
    problems = dict(cli.doctor_problems(unrecorded))
    assert "review-tier" in problems
    assert "ao config set review.same_family labeled --by <name>" in problems["review-opt-in"]
    tier, problems = cli._active_review_tier(_same_family(project))
    assert tier.startswith(f"{T.SAME_FAMILY_LABEL} (r-same); opted in by {PERSON}") and problems == []

    # Nobody configured to implement: a reviewer compared with nobody is a problem, not a pass.
    bare = _write(root, {key: value for key, value in other.items() if key != "implementer"})
    assert "implementer" not in bare
    assert "no-implementer" in dict(cli.doctor_problems(bare))
    assert cli._active_review_tier(bare)[0].startswith("not established")

    monkeypatch.setattr(cli, "_reviewer_probe", lambda probe_cfg, timeout=cli.REVIEW_PROBE_TIMEOUT: {
        "configured": True, "ok": True, "route": "r1", "binary": "/fixture/reviewer", "version": "1.0",
        "reason": "exact nonce echoed", "kind": "success", "tier": None})
    # Nothing installed, as on CI: with an `ao` on PATH the doctor runs the architect's harness with --version.
    monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)
    capsys.readouterr()
    cli.cmd_doctor(bare, SimpleNamespace(check=False))
    out = _plain(capsys)
    assert re.search(r"^review tier\s+not established", out, re.M) and "no implementer is configured" in out


@pytest.mark.skipif(os.name == "nt", reason="the fixture reviewer runs through its shebang, which Windows "
                                            "does not honour")
def test_the_single_harness_profile_initialises_once_a_tier_is_chosen(project, tmp_path, monkeypatch, capsys):
    # A script in the harness's name that answers the probe; the harness itself is never started.
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text("#!/usr/bin/env python3\nimport sys\n"
                    "if '--version' in sys.argv:\n    print('1.0.0')\n    raise SystemExit(0)\n"
                    "print(sys.argv[sys.argv.index('-p') + 1].splitlines()[-1])\n", encoding="utf-8")
    fake.chmod(0o755)
    composed = cli._reviewer_block
    monkeypatch.setattr(cli, "_reviewer_block", lambda adapter, model=None: dict(
        composed(adapter, model), argv=[str(fake)] + composed(adapter, model)["argv"][1:]))

    same = tmp_path / "same-family"
    same.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=same, check=True)
    args = vars(_init_args(profile="claude-claude", agent="kiro", review_tier="same-family"))
    assert cli.cmd_init({"root": str(same)}, _init_args(**args)) == 1 and "--by is required" in _plain(capsys)
    assert cli.cmd_init({"root": str(same)}, _init_args(**dict(args, by="claude"))) == 1
    assert "names an agent or a role" in _plain(capsys) and not (same / ".ao").exists()

    assert cli.cmd_init({"root": str(same)}, _init_args(**dict(args, by=PERSON))) == 0
    assert f"exact nonce echoed; {T.SAME_FAMILY_LABEL}" in _plain(capsys)
    cfg = A.load_config(str(same))
    assert cfg["review"] == {"same_family": "labeled"} and cli._same_family_opt_in(cfg)["by"] == PERSON
    assert cli._review_tier(cfg, cfg["reviewer"]) == (T.SAME_FAMILY, None)

    person = tmp_path / "person"
    person.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=person, check=True)
    assert cli.cmd_init({"root": str(person)}, _init_args(profile="claude-claude", agent="kiro",
                                                          review_tier="person")) == 0
    cfg = A.load_config(str(person))
    assert "reviewer" not in cfg and cfg["implementer"]["adapter"] == "claude-code"
    assert cli._active_review_tier(cfg)[0].startswith("person review")
