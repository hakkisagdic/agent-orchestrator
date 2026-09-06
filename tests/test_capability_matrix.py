import copy
import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, matrix as M


APPROVED_SCRIPT = (
    "print('BLOCKER: 0'); print('HIGH: 0'); print('MEDIUM: 0'); "
    "print('LOW: 0'); print('VERDICT: APPROVED')"
)
INVALID_SCRIPT = "print('looks fine')"
QUOTA_SCRIPT = "print('usage limit reached, resets in 1h 0m')"


def _tool(script, adapter):
    return {
        "adapter": adapter,
        "argv": [sys.executable, "-c", script, "{prompt}", "{model}"],
        "capabilities": ["prompt"],
    }


def _strict_config(project, primary_script=APPROVED_SCRIPT,
                   primary_family="review-family", fallback_script=None,
                   fallback_family="fallback-family"):
    cfg = copy.deepcopy(project)
    providers = {
        "provider://writer": {"capabilities": ["invoke", "extra-provider-cap"]},
        "provider://review": {"capabilities": ["invoke"]},
    }
    models = {
        "model.writer": {
            "family": "writer-family",
            "argument": "writer-runtime-v1",
            "capabilities": ["implementation", "extra-model-cap"],
        },
        "model.primary": {
            "family": primary_family,
            "argument": "review-runtime-v1",
            "capabilities": ["semantic-review"],
        },
        "model.fallback": {
            "family": fallback_family,
            "argument": "review-runtime-v2",
            "capabilities": ["semantic-review"],
        },
    }
    tools = {
        "tool.writer": {
            "adapter": "writer-adapter",
            "capabilities": ["prompt", "workspace-write", "extra-tool-cap"],
        },
        "tool.primary": _tool(primary_script, "review-adapter-one"),
        "tool.fallback": _tool(fallback_script or APPROVED_SCRIPT, "review-adapter-two"),
    }
    bindings = {
        "binding.writer": {
            "provider": "provider://writer", "model": "model.writer", "tool": "tool.writer",
        },
        "binding.primary": {
            "provider": "provider://review", "model": "model.primary", "tool": "tool.primary",
        },
        "binding.fallback": {
            "provider": "provider://review", "model": "model.fallback", "tool": "tool.fallback",
        },
    }
    cfg["capability_matrix"] = {
        "version": 1,
        "providers": providers,
        "models": models,
        "tools": tools,
        "bindings": bindings,
    }
    cfg["implementer"] = {
        "adapter": "writer-adapter",
        "model": "writer-runtime-v1",
        "session": "writer-session",
        "binding": "binding.writer",
    }
    cfg["reviewer"] = {
        "binding": "binding.primary",
        # These legacy inline values are deliberately false. Strict identity
        # must come only from the bound declarations.
        "family": "spoofed-family",
        "argv": ["must-not-run"],
        "fallbacks": [{"binding": "binding.fallback", "family": "writer-family"}],
    }
    return cfg


def _args(**overrides):
    values = {"boundary": "matrix boundary", "timeout": 30, "paths": None, "commits": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def _stage_change(root, value="two"):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    path = os.path.join(root, "src", "a.py")
    open(path, "w", encoding="utf-8").write("value = %r\n" % value)
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True, capture_output=True)
    return path


def _review_body(cfg):
    directory = os.path.join(cfg["root"], cfg["reviews"])
    names = [name for name in os.listdir(directory) if name.endswith(".md")]
    assert len(names) == 1
    path = os.path.join(directory, names[0])
    return path, open(path, encoding="utf-8").read()


def _persist_verification(cfg):
    candidate = A.index_candidate(cfg["root"])
    record = {
        "id": "V-matrix",
        "schema": 2,
        "passed": True,
        "candidate_ready": True,
        "candidate": candidate,
        "candidate_after": candidate,
        "candidate_issues": [],
        "gates": [],
    }
    A.record_verification(cfg["root"], record)
    return candidate


def _quiet_authority_environment(monkeypatch):
    monkeypatch.setattr(A, "plan_drift", lambda root: [])
    monkeypatch.setattr(A, "hold_state", lambda root: None)
    monkeypatch.setattr(A, "urgent_messages", lambda *args, **kwargs: [])


def _tamper_evidence(path, mutate):
    body = open(path, encoding="utf-8").read()
    evidence = A.review_evidence(body)
    old = A.review_evidence_line(evidence)
    mutate(evidence)
    open(path, "w", encoding="utf-8").write(
        body.replace(old, A.review_evidence_line(evidence), 1)
    )


def test_absent_matrix_keeps_legacy_review_schema(project):
    _stage_change(project["root"])
    cfg = dict(project, reviewer={
        "id": "legacy-reviewer", "family": "legacy-family",
        "argv": [sys.executable, "-c", APPROVED_SCRIPT, "{prompt}"],
    })

    assert M.is_strict(cfg) is False
    assert cli.cmd_review(cfg, _args()) == 0
    _, body = _review_body(cfg)
    evidence = A.review_evidence(body)
    assert evidence["schema"] == 2
    assert "matrix" not in evidence
    assert "implementer_identity" not in evidence


def test_arbitrary_ids_resolve_to_safe_identity_and_canonical_digest(project):
    cfg = _strict_config(project)
    resolution = M.resolve(cfg)
    identity = resolution["implementer_identity"]

    assert identity == {
        "binding": "binding.writer",
        "provider": "provider://writer",
        "model": "model.writer",
        "model_argument": "writer-runtime-v1",
        "tool": "tool.writer",
        "adapter": "writer-adapter",
        "family": "writer-family",
        "provider_capabilities": ["extra-provider-cap", "invoke"],
        "model_capabilities": ["extra-model-cap", "implementation"],
        "tool_capabilities": ["extra-tool-cap", "prompt", "workspace-write"],
    }
    reordered = copy.deepcopy(cfg)
    reordered["capability_matrix"] = dict(
        reversed(list(reordered["capability_matrix"].items()))
    )
    assert M.resolve(reordered)["digest"] == resolution["digest"]
    assert resolution["digest"].startswith("sha256:")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cfg: cfg["capability_matrix"]["bindings"]["binding.primary"].update(
            provider="missing-provider"
        ),
        lambda cfg: cfg["capability_matrix"]["providers"]["provider://review"].update(
            capabilities="invoke"
        ),
        lambda cfg: cfg["capability_matrix"]["models"]["model.primary"].update(
            family="Not-Normalized"
        ),
        lambda cfg: cfg["reviewer"]["fallbacks"].append(
            {"binding": "binding.primary"}
        ),
        lambda cfg: cfg["capability_matrix"]["tools"]["tool.primary"].update(
            argv=[sys.executable, "{prompt}", "{unknown}", "{model}"]
        ),
        lambda cfg: cfg["capability_matrix"]["tools"]["tool.primary"].update(
            argv=[sys.executable, "{prompt}"]
        ),
        lambda cfg: cfg["capability_matrix"]["models"]["model.primary"].update(
            capabilities=[]
        ),
    ],
)
def test_invalid_strict_contract_fails_closed(project, mutate):
    cfg = _strict_config(project)
    mutate(cfg)
    with pytest.raises(M.MatrixError):
        M.resolve(cfg)


def test_implementer_runtime_adapter_and_model_must_match_binding(project):
    cfg = _strict_config(project)
    cfg["implementer"]["adapter"] = "different-adapter"
    cfg["implementer"]["model"] = "different-model"

    with pytest.raises(M.MatrixError) as caught:
        M.resolve(cfg)
    text = str(caught.value)
    assert "implementer.adapter" in text
    assert "implementer.model" in text


def test_same_family_primary_is_never_spawned_and_fallback_is_selected(
    project, tmp_path
):
    marker = tmp_path / "primary-spawned"
    primary = "open(%r, 'w').write('bad')" % str(marker)
    cfg = _strict_config(
        project, primary_script=primary, primary_family="writer-family",
        fallback_script=APPROVED_SCRIPT, fallback_family="independent-family",
    )
    _stage_change(cfg["root"])

    assert cli.cmd_review(cfg, _args()) == 0
    assert not marker.exists()
    _, body = _review_body(cfg)
    evidence = A.review_evidence(body)
    assert evidence["schema"] == 3
    assert evidence["reviewer_identity"]["binding"] == "binding.fallback"
    assert evidence["reviewer_identity"]["family"] == "independent-family"
    assert evidence["reviewer_attempts"] == [
        {
            "binding": "binding.primary",
            "outcome": "ineligible",
            "reason": "same model family as implementer",
        },
        {"binding": "binding.fallback", "outcome": "reviewed", "reason": ""},
    ]


def test_all_same_family_is_exit_two_without_spawning(project, monkeypatch):
    cfg = _strict_config(
        project, primary_family="writer-family", fallback_family="writer-family"
    )
    _stage_change(cfg["root"])

    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("an ineligible reviewer was spawned")

    monkeypatch.setattr(cli, "_run_reviewer", unexpected_spawn)
    assert cli.cmd_review(cfg, _args()) == 2
    assert os.listdir(os.path.join(cfg["root"], cfg["reviews"])) == []


def test_unavailable_eligible_reviewer_advances_to_fallback(project):
    cfg = _strict_config(
        project, primary_script=QUOTA_SCRIPT, primary_family="review-family",
        fallback_script=APPROVED_SCRIPT, fallback_family="fallback-family",
    )
    _stage_change(cfg["root"])

    assert cli.cmd_review(cfg, _args()) == 0
    _, body = _review_body(cfg)
    evidence = A.review_evidence(body)
    assert [attempt["outcome"] for attempt in evidence["reviewer_attempts"]] == [
        "unavailable", "reviewed",
    ]
    assert evidence["reviewer_attempts"][0]["reason"] == "runtime unavailable"
    assert evidence["reviewer_identity"]["binding"] == "binding.fallback"


def test_invalid_output_does_not_approval_shop_fallback(project, tmp_path):
    marker = tmp_path / "fallback-spawned"
    fallback = "open(%r, 'w').write('bad'); %s" % (str(marker), APPROVED_SCRIPT)
    cfg = _strict_config(
        project, primary_script=INVALID_SCRIPT, primary_family="review-family",
        fallback_script=fallback, fallback_family="fallback-family",
    )
    _stage_change(cfg["root"])

    assert cli.cmd_review(cfg, _args()) == 3
    assert not marker.exists()
    _, body = _review_body(cfg)
    evidence = A.review_evidence(body)
    assert evidence["review_status"] == "invalid"
    assert evidence["authorizable"] is False
    assert [attempt["outcome"] for attempt in evidence["reviewer_attempts"]] == [
        "invalid-output", "not-attempted",
    ]


def test_schema_three_evidence_contains_only_safe_declared_identity(project):
    cfg = _strict_config(project)
    cfg["capability_matrix"]["tools"]["tool.primary"]["argv"].append(
        "credential-sentinel-must-not-persist"
    )
    _stage_change(cfg["root"])

    assert cli.cmd_review(cfg, _args()) == 0
    _, body = _review_body(cfg)
    evidence = A.review_evidence(body)
    serialized = json.dumps(evidence, sort_keys=True)

    assert evidence["schema"] == 3
    assert set(evidence["matrix"]) == {"version", "digest"}
    assert set(evidence["reviewer_identity"]) == {
        "binding", "provider", "model", "model_argument", "tool", "adapter",
        "family", "provider_capabilities", "model_capabilities", "tool_capabilities",
    }
    assert "argv" not in serialized
    assert "credential-sentinel-must-not-persist" not in body


def test_strict_commit_ok_records_schema_three_matrix_identity(project, monkeypatch):
    cfg = _strict_config(project)
    _stage_change(cfg["root"])
    assert cli.cmd_review(cfg, _args()) == 0
    candidate = _persist_verification(cfg)
    _quiet_authority_environment(monkeypatch)

    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    row = A.latest_authority_decision(cfg["root"])
    resolution = M.resolve(cfg)
    assert row["schema"] == 3
    assert row["candidate"] == candidate
    assert row["matrix"] == resolution["matrix"]
    assert row["implementer_identity"] == resolution["implementer_identity"]
    assert row["reviewer_identity"]["binding"] == "binding.primary"
    assert row["reviewer"] == "binding.primary"
    assert "argv" not in json.dumps(row, sort_keys=True)


@pytest.mark.parametrize("tamper", ["legacy", "matrix", "identity"])
def test_strict_commit_ok_refuses_legacy_or_tampered_review(
    project, monkeypatch, capsys, tamper
):
    cfg = _strict_config(project)
    _stage_change(cfg["root"])
    assert cli.cmd_review(cfg, _args()) == 0
    path, _ = _review_body(cfg)
    if tamper == "legacy":
        _tamper_evidence(path, lambda evidence: evidence.update(schema=2))
    elif tamper == "matrix":
        _tamper_evidence(
            path, lambda evidence: evidence["matrix"].update(digest="sha256:tampered")
        )
    else:
        _tamper_evidence(
            path,
            lambda evidence: evidence["reviewer_identity"].update(
                family="writer-family"
            ),
        )
    _persist_verification(cfg)
    _quiet_authority_environment(monkeypatch)
    capsys.readouterr()

    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 1
    assert "GRANTED" not in capsys.readouterr().out


def test_strict_commit_check_accepts_exact_grant_and_refuses_drift_without_writing(
    project, monkeypatch, capsys
):
    cfg = _strict_config(project)
    _stage_change(cfg["root"])
    assert cli.cmd_review(cfg, _args()) == 0
    _persist_verification(cfg)
    _quiet_authority_environment(monkeypatch)
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    ledger = os.path.join(cfg["root"], ".ao", "ledger", "authority.jsonl")
    before = open(ledger, "rb").read()
    capsys.readouterr()

    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0
    assert "AUTHORIZED" in capsys.readouterr().out
    assert open(ledger, "rb").read() == before

    drifted = copy.deepcopy(cfg)
    drifted["capability_matrix"]["providers"]["provider://review"]["capabilities"].append(
        "drifted-capability"
    )
    assert cli.cmd_commit_check(drifted, SimpleNamespace()) == 1
    assert "COMMIT REFUSED" in capsys.readouterr().out
    assert open(ledger, "rb").read() == before

    role_drifted = copy.deepcopy(cfg)
    role_drifted["reviewer"]["binding"] = "binding.fallback"
    role_drifted["reviewer"]["fallbacks"] = [{"binding": "binding.primary"}]
    assert cli.cmd_commit_check(role_drifted, SimpleNamespace()) == 1
    assert "role bindings have drifted" in capsys.readouterr().out
    assert open(ledger, "rb").read() == before


def test_strict_and_legacy_grants_never_cross_authorize(project, monkeypatch, capsys):
    cfg = _strict_config(project)
    _stage_change(cfg["root"])
    assert cli.cmd_review(cfg, _args()) == 0
    candidate = _persist_verification(cfg)
    _quiet_authority_environment(monkeypatch)
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    ledger = os.path.join(cfg["root"], ".ao", "ledger", "authority.jsonl")
    strict_bytes = open(ledger, "rb").read()

    legacy_cfg = copy.deepcopy(cfg)
    del legacy_cfg["capability_matrix"]
    assert cli.cmd_commit_check(legacy_cfg, SimpleNamespace()) == 1
    assert "capability_matrix was removed" in capsys.readouterr().out
    assert open(ledger, "rb").read() == strict_bytes

    # Replace the decision ledger with an otherwise exact legacy grant, then
    # prove strict opt-in refuses it rather than silently falling back.
    open(ledger, "w", encoding="utf-8").write("")
    review_name = os.path.basename(_review_body(cfg)[0])
    A.record_authority(
        cfg["root"], True, [], A.tree_digest(cfg["root"], cfg), "V-matrix",
        "C-legacy", review=review_name, reviewer="binding.primary",
        candidate=candidate, scope=A.candidate_scope(candidate),
    )
    legacy_bytes = open(ledger, "rb").read()
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 1
    assert "schema-3 capability-matrix grant" in capsys.readouterr().out
    assert open(ledger, "rb").read() == legacy_bytes


def test_review_off_still_requires_matrix_but_can_grant_without_reviewer(
    project, monkeypatch
):
    cfg = _strict_config(
        project, primary_family="writer-family", fallback_family="writer-family"
    )
    cfg["features"] = {"review": False}
    _stage_change(cfg["root"])
    _persist_verification(cfg)
    _quiet_authority_environment(monkeypatch)

    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    row = A.latest_authority_decision(cfg["root"])
    assert row["schema"] == 3
    assert row["review"] is None
    assert row["reviewer_identity"] is None
    before = open(
        os.path.join(cfg["root"], ".ao", "ledger", "authority.jsonl"), "rb"
    ).read()
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0
    assert open(
        os.path.join(cfg["root"], ".ao", "ledger", "authority.jsonl"), "rb"
    ).read() == before


def test_doctor_has_stable_matrix_errors_and_legacy_has_no_matrix_warning(project):
    legacy_keys = [key for key, _ in cli.doctor_problems(project)]
    assert "capability-matrix" not in legacy_keys
    assert "no-independent-reviewer" not in legacy_keys

    malformed = _strict_config(project)
    malformed["capability_matrix"]["version"] = "1"
    malformed_keys = [key for key, _ in cli.doctor_problems(malformed)]
    assert "capability-matrix" in malformed_keys

    same_family = _strict_config(
        project, primary_family="writer-family", fallback_family="writer-family"
    )
    same_family_keys = [key for key, _ in cli.doctor_problems(same_family)]
    assert "no-independent-reviewer" in same_family_keys


@pytest.mark.parametrize(
    "payload",
    [
        b'{"capability_matrix": {"version": 1},',
        b'{"capability\\u005fmatrix": {"version": 1},',
        b'{"capability_matrix": {"version": 1},\xff',
    ],
)
def test_malformed_json_with_matrix_key_cannot_reactivate_legacy(project, payload):
    path = os.path.join(project["root"], ".ao", "config.json")
    open(path, "wb").write(payload)
    cfg = A.load_config(project["root"])
    assert M.is_strict(cfg) is True
    with pytest.raises(M.MatrixError):
        M.resolve(cfg)


def test_malformed_legacy_json_with_only_nested_matrix_key_stays_legacy(project):
    path = os.path.join(project["root"], ".ao", "config.json")
    open(path, "wb").write(b'{"outer":{"capability_matrix": 1},')
    assert M.is_strict(A.load_config(project["root"])) is False


def test_strict_catchup_keeps_waiver_open_on_configuration_exit_two(
    project, monkeypatch, capsys
):
    cfg = _strict_config(
        project, primary_family="writer-family", fallback_family="writer-family"
    )
    root = cfg["root"]
    waiver = A.waive(root, "review", "B7", "reviewer unavailable", by="human")
    _stage_change(root)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "landed"],
        cwd=root, check=True, capture_output=True,
    )

    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("an ineligible catch-up reviewer was spawned")

    def unexpected_mail(*args, **kwargs):
        raise AssertionError("catch-up falsely reported a completed review")

    from ao import watchdog as W
    monkeypatch.setattr(cli, "_run_reviewer", unexpected_spawn)
    monkeypatch.setattr(A, "write_mail", unexpected_mail)
    monkeypatch.setattr(W, "run", lambda args: 0)

    assert cli.cmd_catchup(cfg, SimpleNamespace(boundary=None)) == 0
    assert [item["id"] for item in A.open_waivers(root)] == [waiver["id"]]
    assert os.listdir(os.path.join(root, cfg["reviews"])) == []
    assert "reviewer configuration invalid" in capsys.readouterr().out


def test_strict_retrospective_evidence_is_never_authorizable(project):
    cfg = _strict_config(project)
    _stage_change(cfg["root"])
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "landed"],
        cwd=cfg["root"], check=True, capture_output=True,
    )

    assert cli.cmd_review(cfg, _args(commits="HEAD~1..HEAD")) == 0
    _, body = _review_body(cfg)
    evidence = A.review_evidence(body)
    assert evidence["schema"] == 3
    assert evidence["kind"] == "commit-range"
    assert evidence["authorizable"] is False
    assert evidence["review_status"] == "complete"
