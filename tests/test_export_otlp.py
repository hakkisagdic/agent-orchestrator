"""Tests for `ao export otlp [--attested] [--out FILE]` (EXPORT-OTLP)."""
import contextlib
import copy
from datetime import datetime
import hashlib
import json
import os
import re
import secrets
import subprocess

import pytest

from ao import __version__
from ao import cli
from ao import lib as A
from ao import matrix as M
from ao.storage import LedgerCorruption

GIT = A.git_binary()
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")
DIGITS = re.compile(r"^\d+$")

ALLOWED_SPAN_ATTRIBUTES = frozenset({
    "ao.step",
    "ao.slice",
    "ao.candidate",
    "ao.verification.id",
    "ao.verification.profile",
    "ao.review.id",
    "ao.review.kind",
    "ao.review.tier",
    "ao.waiver.id",
    "ao.commit.sha",
    "ao.commit.tree",
    "ao.outcome",
    "ao.authorizable",
    "ao.granted",
    "ao.implementer.actor",
    "ao.implementer.family",
    "ao.reviewer.actor",
    "ao.reviewer.family",
    "ao.gates.count",
    "ao.gates.passed_count",
    "ao.gates.failed_count",
    "ao.tests.pass_count",
    "ao.tests.fail_count",
    "ao.reviewers.count",
    "ao.candidate.changed_count",
    "ao.reasons.count",
})


@contextlib.contextmanager
def _at_epoch(epoch):
    """Run ao's writers at a fixed unix timestamp."""
    real_time = A.time.time
    A.time.time = lambda: epoch
    try:
        yield
    finally:
        A.time.time = real_time


def _strict_matrix_config(project, *, reviewer_binding="codex", reviewer_adapter="codex",
                          reviewer_family="openai"):
    """Build a strict capability-matrix config so `M.authority_fields` produces production schema-3 grants."""
    cfg = copy.deepcopy(project)
    cfg["capability_matrix"] = {
        "version": 1,
        "providers": {
            "provider://writer": {"capabilities": ["invoke"]},
            "provider://review": {"capabilities": ["invoke"]},
        },
        "models": {
            "model.writer": {
                "family": "anthropic",
                "argument": "claude-sonnet-4-6",
                "capabilities": ["implementation"],
            },
            "model.primary": {
                "family": reviewer_family,
                "argument": "gpt-5.4",
                "capabilities": ["semantic-review"],
            },
            "model.fallback": {
                "family": "google",
                "argument": "gemini-2.5-pro",
                "capabilities": ["semantic-review"],
            },
        },
        "tools": {
            "tool.writer": {
                "adapter": "claude",
                "capabilities": ["prompt", "workspace-write"],
            },
            "tool.primary": {
                "adapter": reviewer_adapter,
                "argv": ["codex", "{prompt}", "{model}"],
                "capabilities": ["prompt"],
            },
            "tool.fallback": {
                "adapter": "antigravity",
                "argv": ["agy", "{prompt}", "{model}"],
                "capabilities": ["prompt"],
            },
        },
        "bindings": {
            "claude": {
                "provider": "provider://writer", "model": "model.writer", "tool": "tool.writer",
            },
            reviewer_binding: {
                "provider": "provider://review", "model": "model.primary", "tool": "tool.primary",
            },
            "agy": {
                "provider": "provider://review", "model": "model.fallback", "tool": "tool.fallback",
            },
        },
    }
    cfg["implementer"] = {
        "adapter": "claude",
        "model": "claude-sonnet-4-6",
        "session": "s1",
        "name": "claude",
        "binding": "claude",
    }
    cfg["reviewer"] = {
        "binding": reviewer_binding,
        "fallbacks": [{"binding": "agy"}],
    }
    return cfg


def _tree_digest(root):
    """Deterministic SHA-256 over every path, entry kind and file payload under `root`."""
    h = hashlib.sha256()
    if not os.path.exists(root):
        h.update(b"missing")
        return h.hexdigest()
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        h.update(f"D:{rel_dir}\0".encode("utf-8"))
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if os.path.islink(full):
                h.update(f"L:{rel}:{os.readlink(full)}\0".encode("utf-8"))
            else:
                with open(full, "rb") as fh:
                    data = fh.read()
                FileDigest = hashlib.sha256(data).hexdigest()
                h.update(f"F:{rel}:{len(data)}:{FileDigest}\0".encode("utf-8"))
    return h.hexdigest()


def _attr_map(attributes):
    """Validate OTLP/JSON KeyValue list and return `{key: decoded_python_value}`."""
    assert isinstance(attributes, list)
    seen = {}
    for item in attributes:
        assert isinstance(item, dict)
        assert set(item.keys()) == {"key", "value"}
        key = item["key"]
        assert isinstance(key, str) and key
        assert key not in seen, f"duplicate attribute key {key}"
        val = item["value"]
        assert isinstance(val, dict)
        assert len(val) == 1, f"AnyValue must have one union field, got {val}"
        field, raw = next(iter(val.items()))
        assert field in ("stringValue", "intValue", "boolValue"), f"unexpected AnyValue type {field}"
        if field == "stringValue":
            assert isinstance(raw, str)
            seen[key] = raw
        elif field == "intValue":
            assert isinstance(raw, str) and DIGITS.fullmatch(raw), f"intValue must be decimal string, got {raw!r}"
            seen[key] = int(raw)
        elif field == "boolValue":
            assert isinstance(raw, bool)
            seen[key] = raw
    return seen


def _seed_two_slices(project):
    """Populate two slices using ao's own writers (`record_verification`, `write_review_artefact`, `record_authority`)."""
    root = project["root"]
    cfg = _strict_matrix_config(project)
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({k: v for k, v in cfg.items() if k != "root"}) + "\n")
    gates_spec = {
        "gates": {
            "pytest": {"cmd": "pytest"},
            "lint": {"cmd": "flake8"},
        },
        "profiles": {
            "full": ["pytest", "lint"],
            "fast": ["pytest"],
            "smoke": ["pytest"],
        },
    }
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(gates_spec) + "\n")
    resolution = M.resolve(cfg)
    codex_identity = resolution["reviewers"][0]["identity"]

    # SLICE-A: stage two files and measure the real index candidate.
    # Leave .ao/board.md without a running slice during verify so SLICE-A's grant and verification
    # must resolve their slice via `review_to_slice`.
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("print('DIFF_LINE_NEVER_EXPORT')\n")
    with open(os.path.join(root, "secret_home_file.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run([GIT, "add", "app.py", "secret_home_file.py"], cwd=root, check=True)
    cand_a = A.index_candidate(root)
    tree_a = cand_a["index_tree"]
    head_a = cand_a["head"]

    ver_a_rec = {
        "id": "V-1700000100000",
        "at": datetime.fromtimestamp(1700000100).isoformat(timespec="seconds"),
        "schema": 2,
        "profile": "full",
        "gates_digest": A.gate_definitions_digest(root, "full"),
        "by": "ao verify",
        "passed": True,
        "gates": [
            {"name": "pytest", "passed": True, "seconds": 1.5, "counts": {"pass": 12, "fail": 0}},
            {"name": "lint", "passed": True, "seconds": 0.5, "counts": {"pass": 0, "fail": 0}},
        ],
        "candidate": cand_a,
        "candidate_after": cand_a,
        "candidate_ready": True,
        "candidate_issues": [],
        "isolation_inputs": A.gate_inputs(root),
        "tree": A.tree_digest(root, cfg),
        "head": head_a[:12],
        "dirty": 0,
    }
    ver_a = A.record_verification(root, ver_a_rec)
    assert ver_a.get("slice") is None

    with _at_epoch(1700000200):
        A.write_review_artefact(
            root,
            cfg["reviews"],
            "20231114-221640-R-001.md",
            "## Findings\nNone.\n\nVERDICT: APPROVED\n",
            evidence={
                "kind": "index-candidate",
                "candidate": cand_a,
                "authorizable": True,
                "slice": "SLICE-A",
                "review_tier": "independent",
            },
            verdict="APPROVED",
            reviewer="codex",
            fallback=False,
        )

    grant_id = "-".join(("C", "20231114", "221820", hashlib.sha256(b"export-otlp").hexdigest()[:12]))
    with _at_epoch(1700000300):
        grant_a = A.record_authority(
            root,
            True,
            [],
            A.tree_digest(root, cfg),
            ver_a["id"],
            grant_id,
            review="20231114-221640-R-001.md",
            reviewer="codex",
            candidate=cand_a,
            scope=A.candidate_scope(cand_a),
            **M.authority_fields(resolution, codex_identity),
        )
    assert "slice" not in grant_a
    assert "implementer" not in grant_a
    assert "tier" not in grant_a

    commit_msg = A.with_trailers("feat: land slice a", A.grant_trailers(grant_a))
    env = dict(os.environ, GIT_COMMITTER_DATE="1700000400 +0000", GIT_AUTHOR_DATE="1700000400 +0000")
    subprocess.run(
        [GIT, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", commit_msg],
        cwd=root, check=True, env=env,
    )
    commit_sha = subprocess.run(
        [GIT, "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()

    # Second slice: put SLICE-B under `## running` on the board so `record_verification` sets `slice="SLICE-B"`,
    # leave `slice=None` on the review, and record a refused grant via `record_authority` without `review` or
    # `reviewer` (exact `cmd_commit_ok` refusal shape) so the grant resolves `SLICE-B` via `verification_to_slice`.
    with open(os.path.join(root, ".ao", "board.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# Board\n\n## running\n- [SLICE-B] Slice B\n\n"
            "## blocked\n\n## queued\n\n## inbox\n\n## verified\n\n## done\n"
        )
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "secret_path.py"), "w", encoding="utf-8") as fh:
        fh.write("y = 2\n")
    subprocess.run([GIT, "add", "src/secret_path.py"], cwd=root, check=True)
    cand_b = A.index_candidate(root)

    ver_b_rec = {
        "id": "V-1700000500000",
        "at": datetime.fromtimestamp(1700000500).isoformat(timespec="seconds"),
        "schema": 2,
        "profile": "smoke",
        "gates_digest": A.gate_definitions_digest(root, "smoke"),
        "by": "ao verify",
        "passed": False,
        "gates": [
            {"name": "pytest", "passed": False, "seconds": 0.25, "counts": {"pass": 4, "fail": 2}},
        ],
        "candidate": cand_b,
        "candidate_after": cand_b,
        "candidate_ready": True,
        "candidate_issues": [],
        "isolation_inputs": A.gate_inputs(root),
        "tree": A.tree_digest(root, cfg),
        "head": commit_sha[:12],
        "dirty": 0,
    }
    ver_b = A.record_verification(root, ver_b_rec)
    assert ver_b.get("slice") == "SLICE-B"

    with _at_epoch(1700000600):
        A.write_review_artefact(
            root,
            cfg["reviews"],
            "20231114-222320-R-002.md",
            "## Findings\n- [HIGH] src/secret_path.py:1 — broken\n\nVERDICT: NEEDS_CHANGES\n",
            evidence={
                "kind": "index-candidate",
                "candidate": cand_b,
                "authorizable": False,
                "slice": None,
                "review_tier": "independent",
            },
            verdict="NEEDS_CHANGES",
            reviewer="agy",
            fallback=False,
        )

    with _at_epoch(1700000700):
        grant_b = A.record_authority(
            root,
            False,
            [
                f"verification failed in {A.HOME}/private_dir",
                "SUPER_SECRET_PROMPT_TEXT",
            ],
            A.tree_digest(root, cfg),
            ver_b["id"],
            candidate=cand_b,
            scope=A.candidate_scope(cand_b),
            **M.authority_fields(resolution, None),
        )
    assert "slice" not in grant_b
    assert "implementer" not in grant_b
    assert "tier" not in grant_b

    return {
        "tree_a": tree_a,
        "commit_sha": commit_sha,
        "cand_a": cand_a["digest"],
        "cand_a_obj": cand_a,
        "cand_b": cand_b["digest"],
        "cand_b_obj": cand_b,
        "ver_a_id": ver_a["id"],
        "ver_b_id": ver_b["id"],
        "grant_id": grant_id,
        "cfg": cfg,
        "resolution": resolution,
    }


def test_otlp_shape_and_slice_traces(project, capsys):
    seeded = _seed_two_slices(project)
    rc = cli.main(["-C", project["root"], "export", "otlp"])
    assert rc == 0
    raw = capsys.readouterr().out
    doc = json.loads(raw)

    assert set(doc.keys()) == {"resourceSpans"}
    assert isinstance(doc["resourceSpans"], list) and len(doc["resourceSpans"]) == 1
    res_spans = doc["resourceSpans"][0]
    assert set(res_spans.keys()) == {"resource", "scopeSpans"}
    res_attrs = _attr_map(res_spans["resource"]["attributes"])
    assert res_attrs["service.name"] == "ao"
    assert res_attrs["service.version"] == __version__

    assert isinstance(res_spans["scopeSpans"], list) and len(res_spans["scopeSpans"]) == 1
    scope_spans = res_spans["scopeSpans"][0]
    assert scope_spans["scope"] == {"name": "ao", "version": __version__}
    spans = scope_spans["spans"]
    assert len(spans) == 7

    by_slice = {}
    span_ids = set()
    for span in spans:
        assert set(span.keys()) <= {
            "traceId", "spanId", "parentSpanId", "name", "kind",
            "startTimeUnixNano", "endTimeUnixNano", "attributes", "status",
        }
        assert HEX32.fullmatch(span["traceId"]) and span["traceId"] != "0" * 32
        assert HEX16.fullmatch(span["spanId"]) and span["spanId"] != "0" * 16
        assert span["spanId"] not in span_ids
        span_ids.add(span["spanId"])
        if "parentSpanId" in span:
            assert HEX16.fullmatch(span["parentSpanId"]) and span["parentSpanId"] != "0" * 16
        assert span["name"] in ("verification", "review", "grant", "commit")
        assert span["kind"] == 1 and not isinstance(span["kind"], bool)
        assert DIGITS.fullmatch(span["startTimeUnixNano"])
        assert DIGITS.fullmatch(span["endTimeUnixNano"])
        assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"])
        assert isinstance(span["status"], dict) and set(span["status"].keys()) == {"code"}
        assert span["status"]["code"] in (1, 2) and not isinstance(span["status"]["code"], bool)

        attrs = _attr_map(span["attributes"])
        assert set(attrs.keys()) <= ALLOWED_SPAN_ATTRIBUTES
        assert attrs["ao.step"] == span["name"]
        by_slice.setdefault(attrs["ao.slice"], []).append((span, attrs))

    assert set(by_slice.keys()) == {"SLICE-A", "SLICE-B"}
    slice_a = by_slice["SLICE-A"]
    slice_b = by_slice["SLICE-B"]
    assert [s["name"] for s, _ in slice_a] == ["verification", "review", "grant", "commit"]
    assert [s["name"] for s, _ in slice_b] == ["verification", "review", "grant"]

    trace_a = {s["traceId"] for s, _ in slice_a}
    trace_b = {s["traceId"] for s, _ in slice_b}
    assert len(trace_a) == 1 and len(trace_b) == 1
    assert trace_a != trace_b

    # Root span has no parentSpanId; subsequent spans link within the trace.
    assert "parentSpanId" not in slice_a[0][0]
    assert slice_a[1][0]["parentSpanId"] == slice_a[0][0]["spanId"]
    assert slice_a[2][0]["parentSpanId"] == slice_a[1][0]["spanId"]
    assert slice_a[3][0]["parentSpanId"] == slice_a[2][0]["spanId"]
    assert "parentSpanId" not in slice_b[0][0]
    assert slice_b[1][0]["parentSpanId"] == slice_b[0][0]["spanId"]
    assert slice_b[2][0]["parentSpanId"] == slice_b[1][0]["spanId"]

    # Ledger times in nanoseconds as decimal strings (including naive ISO verification `at`).
    assert slice_a[0][0]["startTimeUnixNano"] == str(1700000100 * 1_000_000_000)
    assert slice_a[0][0]["endTimeUnixNano"] == str(1700000102 * 1_000_000_000)
    assert slice_a[1][0]["startTimeUnixNano"] == str(1700000200 * 1_000_000_000)
    assert slice_a[2][0]["startTimeUnixNano"] == str(1700000300 * 1_000_000_000)
    assert slice_a[3][0]["startTimeUnixNano"] == str(1700000400 * 1_000_000_000)
    assert slice_b[0][0]["startTimeUnixNano"] == str(1700000500 * 1_000_000_000)

    # Status codes and attributes for SLICE-A and SLICE-B.
    assert [s["status"]["code"] for s, _ in slice_a] == [1, 1, 1, 1]
    assert [s["status"]["code"] for s, _ in slice_b] == [2, 2, 2]
    assert slice_a[0][1]["ao.outcome"] == "PASS"
    assert slice_a[0][1]["ao.verification.profile"] == "full"
    assert slice_a[0][1]["ao.gates.count"] == 2
    assert slice_a[0][1]["ao.gates.passed_count"] == 2
    assert slice_a[0][1]["ao.gates.failed_count"] == 0
    assert slice_a[0][1]["ao.tests.pass_count"] == 12
    assert slice_a[0][1]["ao.tests.fail_count"] == 0
    assert slice_a[0][1]["ao.candidate.changed_count"] == 2
    assert slice_a[1][1]["ao.review.tier"] == "independent"
    assert slice_a[1][1]["ao.implementer.family"] == "anthropic"
    assert slice_a[1][1]["ao.reviewer.family"] == "openai"
    assert slice_a[2][1]["ao.granted"] is True
    assert slice_a[2][1]["ao.review.tier"] == "independent"
    assert slice_a[2][1]["ao.candidate.changed_count"] == 2
    assert slice_a[2][1]["ao.implementer.family"] == "anthropic"
    assert slice_a[2][1]["ao.reviewer.family"] == "openai"
    assert slice_a[3][1]["ao.commit.sha"] == seeded["commit_sha"]
    assert slice_a[3][1]["ao.commit.tree"] == seeded["tree_a"]
    assert slice_a[3][1]["ao.implementer.family"] == "anthropic"
    assert slice_a[3][1]["ao.reviewer.family"] == "openai"
    assert slice_a[3][1]["ao.implementer.actor"] == slice_a[2][1]["ao.implementer.actor"]
    assert slice_a[3][1]["ao.reviewer.actor"] == slice_a[2][1]["ao.reviewer.actor"]

    assert slice_b[0][1]["ao.outcome"] == "FAIL"
    assert slice_b[0][1]["ao.verification.profile"] == "smoke"
    assert slice_b[0][1]["ao.gates.passed_count"] == 0
    assert slice_b[0][1]["ao.gates.failed_count"] == 1
    assert slice_b[0][1]["ao.tests.pass_count"] == 4
    assert slice_b[0][1]["ao.tests.fail_count"] == 2
    assert slice_b[0][1]["ao.candidate.changed_count"] == 1
    assert slice_b[2][1]["ao.granted"] is False
    assert slice_b[2][1]["ao.review.id"] == "20231114-222320-R-002.md"
    assert slice_b[2][1]["ao.review.tier"] == "independent"
    assert slice_b[2][1]["ao.candidate.changed_count"] == 1
    assert slice_b[2][1]["ao.implementer.family"] == "anthropic"
    assert slice_b[2][1]["ao.reviewer.family"] == "google"
    assert slice_b[2][1]["ao.reasons.count"] == 2


def test_otlp_resolves_slice_via_tree_and_waived_grant_author(project, monkeypatch):
    """When verification and a waived schema-2 grant have no slice, commit + slice ledger resolve via tree_to_slice."""
    root = project["root"]
    cfg = copy.deepcopy(project)
    cfg["implementer"] = {"adapter": "claude-code", "session": "s1", "name": "claude"}
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({k: v for k, v in cfg.items() if k != "root"}) + "\n")
    monkeypatch.setattr(A, "invoking_role", lambda: "implementer")

    with open(os.path.join(root, "feature.py"), "w", encoding="utf-8") as fh:
        fh.write("value = 42\n")
    subprocess.run([GIT, "add", "feature.py"], cwd=root, check=True)
    cand = A.index_candidate(root)

    ver = A.record_verification(root, {
        "id": "V-1700001100000",
        "at": datetime.fromtimestamp(1700001100).isoformat(timespec="seconds"),
        "schema": 2,
        "profile": "quick",
        "passed": True,
        "gates": [{"name": "pytest", "passed": True, "seconds": 0.5, "counts": {"pass": 3, "fail": 0}}],
        "candidate": cand,
        "candidate_after": cand,
        "candidate_ready": True,
        "candidate_issues": [],
        "isolation_inputs": None,
        "tree": A.tree_digest(root, cfg),
        "head": cand["head"][:12],
        "dirty": 0,
    })
    assert ver.get("slice") is None

    with _at_epoch(1700001200):
        waiver = A.waive(root, "review", None, "urgent hotfix", "Ada")
    grant_id = "C-1700001300000000000-1"
    with _at_epoch(1700001300):
        grant = A.record_authority(
            root,
            True,
            [],
            A.tree_digest(root, cfg),
            ver["id"],
            grant_id,
            candidate=cand,
            scope=A.candidate_scope(cand),
            waiver=waiver["id"],
            author=A.grant_author(cfg),
        )
    assert "slice" not in grant
    assert "implementer" not in grant

    # Also record an earlier schema-1 style grant row with only `tree` (no `candidate.digest`) to exercise
    # direct `tree_to_slice` resolution on the grant span itself.
    with _at_epoch(1700001250):
        A.record_authority(
            root,
            False,
            ["prior check"],
            cand["index_tree"],
            None,
            candidate={"index_tree": cand["index_tree"], "changed_count": 1},
            author=A.grant_author(cfg),
        )

    commit_msg = A.with_trailers("feat: land slice c", A.grant_trailers(grant))
    env = dict(os.environ, GIT_COMMITTER_DATE="1700001400 +0000", GIT_AUTHOR_DATE="1700001400 +0000")
    subprocess.run(
        [GIT, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", commit_msg],
        cwd=root, check=True, env=env,
    )
    commit_sha = subprocess.run(
        [GIT, "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()

    with _at_epoch(1700001401):
        A.record_slice_move(
            root, "SLICE-C", "running", "done", by="implementer", why="landed",
            notes={"commit": commit_sha[:12]},
        )

    doc = A.export_otlp(root, cfg)
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 4
    trace_ids = {s["traceId"] for s in spans}
    assert len(trace_ids) == 1
    for span in spans:
        attrs = _attr_map(span["attributes"])
        assert attrs["ao.slice"] == "SLICE-C"
        if attrs["ao.step"] in ("grant", "commit"):
            assert attrs["ao.implementer.family"] == "anthropic"
            assert attrs["ao.implementer.actor"].startswith("sha256:")


def test_otlp_deterministic_ids_and_out_file(project, tmp_path, capsys):
    _seed_two_slices(project)
    assert cli.main(["-C", project["root"], "export", "otlp"]) == 0
    first_raw = capsys.readouterr().out

    out_path = tmp_path / "export-traces.json"
    assert cli.main(["-C", project["root"], "export", "otlp", "--out", str(out_path)]) == 0
    assert capsys.readouterr().out == ""
    second_raw = out_path.read_text(encoding="utf-8")

    assert first_raw == second_raw
    first_doc = json.loads(first_raw)
    second_doc = A.export_otlp(project["root"])
    assert first_doc == second_doc


def test_otlp_strictly_read_only(project, tmp_path, capsys):
    _seed_two_slices(project)
    root = project["root"]
    home_ao = os.path.join(A.HOME, ".ao")
    os.makedirs(home_ao, exist_ok=True)
    with open(os.path.join(home_ao, "state.json"), "w", encoding="utf-8") as fh:
        fh.write('{"kept": true}\n')

    # Configure session: "auto" so load_config would record a session if not read-only.
    cfg_path = os.path.join(root, ".ao", "config.json")
    cfg = json.loads(open(cfg_path, encoding="utf-8").read())
    cfg["implementer"] = {"adapter": "claude", "session": "auto", "name": "claude"}
    with open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(cfg) + "\n")

    before_project = _tree_digest(root)
    before_home_ao = _tree_digest(home_ao)

    assert cli.main(["-C", root, "export", "otlp"]) == 0
    _ = capsys.readouterr().out
    assert _tree_digest(root) == before_project
    assert _tree_digest(home_ao) == before_home_ao

    out_file = tmp_path / "outside-out.json"
    assert cli.main(["-C", root, "export", "otlp", "--out", str(out_file)]) == 0
    assert out_file.is_file()
    assert _tree_digest(root) == before_project
    assert _tree_digest(home_ao) == before_home_ao


def test_otlp_excludes_private_fields(project, capsys):
    seeded = _seed_two_slices(project)
    root = project["root"]
    secret_key = "-".join(("sk", "ant", "api03", secrets.token_hex(20)))
    with _at_epoch(1700000800):
        A.record_review(
            root,
            f"{A.HOME}/semantic-review/20231114-222500-R-003.md",
            b"VERDICT: APPROVED\n",
            {
                "kind": "commit-range",
                "candidate": {"digest": seeded["cand_a"]},
                "commits": [seeded["commit_sha"]],
                "author": {"actor": f"{A.HOME}/bin/claude", "family": secret_key},
                "authorizable": True,
                "slice": "SLICE-A",
                "review_tier": "full",
            },
            "APPROVED",
            reviewer="PROMPT line 1\nPROMPT line 2",
            fallback=False,
        )

    assert cli.main(["-C", root, "export", "otlp"]) == 0
    raw = capsys.readouterr().out
    for forbidden in (
        "DIFF_LINE_NEVER_EXPORT",
        "SUPER_SECRET_PROMPT_TEXT",
        "secret_home_file.py",
        "secret_path.py",
        "private_dir",
        seeded["grant_id"],
        secret_key,
        "PROMPT line 1",
        str(A.HOME),
        os.path.expanduser("~"),
        str(root),
    ):
        assert forbidden not in raw, f"private value leaked into OTLP export: {forbidden!r}"


def test_otlp_rejects_tampered_slice_ledger(project):
    seeded = _seed_two_slices(project)
    root = project["root"]
    slices_path = A.slice_ledger_path(root)
    with _at_epoch(1700000400):
        A.record_slice_move(
            root, "SLICE-A", "running", "done", by="implementer", why="landed",
            notes={"commit": seeded["commit_sha"]},
        )
    with open(slices_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": 1700000401,
            "slice": "FORGED-SLICE",
            "from": "running",
            "to": "done",
            "notes": {"commit": seeded["commit_sha"]},
        }) + "\n")

    with pytest.raises(LedgerCorruption):
        A.export_otlp(root)


def test_otlp_propagates_landed_commits_failure(project, monkeypatch):
    _seed_two_slices(project)
    root = project["root"]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("git log failed")

    monkeypatch.setattr(A, "landed_commits", _boom)
    with pytest.raises(RuntimeError, match="git log failed"):
        A.export_otlp(root)


def test_otlp_hashes_actor_identities_and_allowlists_adapter_families(project, capsys):
    seeded = _seed_two_slices(project)
    root = project["root"]
    opaque_hex_cred = secrets.token_hex(16)
    secret_cfg = _strict_matrix_config(
        project,
        reviewer_binding=opaque_hex_cred,
        reviewer_adapter=opaque_hex_cred,
        reviewer_family="custom-secret-family",
    )
    secret_resolution = M.resolve(secret_cfg)
    secret_reviewer_identity = secret_resolution["reviewers"][0]["identity"]

    with _at_epoch(1700000900):
        A.write_review_artefact(
            root,
            project["reviews"],
            "20231114-222640-R-004.md",
            "## Findings\nNone.\n\nVERDICT: APPROVED\n",
            evidence={
                "kind": "index-candidate",
                "candidate": seeded["cand_a_obj"],
                "authorizable": True,
                "slice": "SLICE-A",
                "review_tier": "independent",
            },
            verdict="APPROVED",
            reviewer=opaque_hex_cred,
            fallback=False,
        )
    with _at_epoch(1700000950):
        A.record_authority(
            root,
            True,
            [],
            A.tree_digest(root, secret_cfg),
            seeded["ver_a_id"],
            None,
            review="20231114-222640-R-004.md",
            reviewer=opaque_hex_cred,
            candidate=seeded["cand_a_obj"],
            scope=A.candidate_scope(seeded["cand_a_obj"]),
            **M.authority_fields(secret_resolution, secret_reviewer_identity),
        )

    assert cli.main(["-C", root, "export", "otlp"]) == 0
    raw = capsys.readouterr().out
    assert opaque_hex_cred not in raw
    assert "custom-secret-family" not in raw

    doc = json.loads(raw)
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    actor_hash_re = re.compile(r"^sha256:[0-9a-f]{16}$")
    allowed_families = {"anthropic", "openai", "google", "unknown"}

    claude_hashes = set()
    cred_hashes = set()
    for span in spans:
        attrs = _attr_map(span["attributes"])
        for role in ("implementer", "reviewer"):
            actor_key = f"ao.{role}.actor"
            family_key = f"ao.{role}.family"
            if actor_key in attrs:
                assert actor_hash_re.fullmatch(attrs[actor_key]), (
                    f"{actor_key} must be sha256:<16-hex>, got {attrs[actor_key]!r}"
                )
            if family_key in attrs:
                assert attrs[family_key] in allowed_families, (
                    f"{family_key} must be declared adapter family or 'unknown', got {attrs[family_key]!r}"
                )
        if attrs.get("ao.review.id") == "20231114-222640-R-004.md" and attrs["ao.step"] == "review":
            claude_hashes.add(attrs["ao.implementer.actor"])
            cred_hashes.add(attrs["ao.reviewer.actor"])
            assert attrs["ao.implementer.family"] == "anthropic"
            assert attrs["ao.reviewer.family"] == "unknown"
        if attrs.get("ao.review.id") == "20231114-222640-R-004.md" and attrs["ao.step"] == "grant":
            claude_hashes.add(attrs["ao.implementer.actor"])
            cred_hashes.add(attrs["ao.reviewer.actor"])
            assert attrs["ao.implementer.family"] == "anthropic"
            assert attrs["ao.reviewer.family"] == "unknown"

    assert len(claude_hashes) == 1
    assert len(cred_hashes) == 1
    assert claude_hashes != cred_hashes


def test_otlp_refuses_unattested_ledgers_without_flag_and_exports_attested_with_flag(
    project, tmp_path, monkeypatch, capsys
):
    """A ledger with no checkpoint entry (moved, reset, recorded elsewhere, older than checkpoints) is refused
    unless `--attested` is given, in which case only the attested part is exported and the output says so."""
    _seed_two_slices(project)
    root = project["root"]

    # Point AO_LEDGER_CHECKPOINTS at an empty store (simulating a moved project or reset checkpoint store).
    empty_store = tmp_path / "reset-checkpoints.json"
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", str(empty_store))

    rc = cli.main(["-C", root, "export", "otlp"])
    assert rc != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "verifications.jsonl" in captured.err
    assert "reviews.jsonl" in captured.err
    assert "authority.jsonl" in captured.err
    assert "no checkpoint here: moved, reset, recorded elsewhere, older than checkpoints" in captured.err

    # With `--attested`, the export succeeds, omits the unattested ledgers, and states so on the resource.
    rc = cli.main(["-C", root, "export", "otlp", "--attested"])
    assert rc == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    res_attrs = _attr_map(doc["resourceSpans"][0]["resource"]["attributes"])
    assert res_attrs["ao.export.attested_only"] is True
    assert res_attrs["ao.export.partial"] is True
    assert set(res_attrs["ao.export.unattested_ledgers"].split(",")) == {
        "verifications.jsonl", "reviews.jsonl", "authority.jsonl",
    }
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [s["name"] for s in spans] == ["commit"]


def test_otlp_snapshot_excludes_uncommitted_row_that_rolls_back(project, monkeypatch):
    import ao.storage as storage

    seeded = _seed_two_slices(project)
    root = project["root"]
    baseline_export = A.export_otlp(root)
    review_path = A.review_ledger_path(root)
    baseline_reviews = storage.read_chained_jsonl(review_path, A.REVIEW_CHAIN, lock=False)

    mid_snapshots = []

    def _fail_before_checkpoint(path, *_args, **_kwargs):
        chain = A.REVIEW_CHAIN if path == review_path else A.SLICE_CHAIN
        with pytest.raises(LedgerCorruption, match=os.path.basename(path)):
            storage.read_chained_jsonl(path, chain, lock=False)
        with pytest.raises(LedgerCorruption, match=os.path.basename(path)):
            A.export_otlp(root)
        mid_snapshots.append((
            storage.read_chained_jsonl(path, chain, lock=False, attested=True),
            A.export_otlp(root, attested=True),
        ))
        raise OSError("simulated checkpoint failure")

    monkeypatch.setattr(storage, "_record_committed_length", _fail_before_checkpoint)
    with pytest.raises(OSError, match="simulated checkpoint failure"):
        with _at_epoch(1700000999):
            A.write_review_artefact(
                root,
                project["reviews"],
                "20231114-222959-R-999.md",
                "VERDICT: APPROVED\n",
                evidence={
                    "kind": "index-candidate",
                    "candidate": seeded["cand_a_obj"],
                    "authorizable": True,
                    "slice": "SLICE-A",
                    "review_tier": "independent",
                },
                verdict="APPROVED",
                reviewer="codex",
                fallback=False,
            )

    assert len(mid_snapshots) == 1
    mid_rows, mid_export = mid_snapshots[0]
    assert mid_rows == baseline_reviews
    assert mid_export["resourceSpans"][0]["scopeSpans"] == baseline_export["resourceSpans"][0]["scopeSpans"]
    mid_res_attrs = _attr_map(mid_export["resourceSpans"][0]["resource"]["attributes"])
    assert mid_res_attrs["ao.export.attested_only"] is True
    assert mid_res_attrs["ao.export.partial"] is True
    assert mid_res_attrs["ao.export.unattested_ledgers"] == "reviews.jsonl"
    assert A.export_otlp(root) == baseline_export

    # Also verify a brand-new ledger whose first row rolls back before checkpointing.
    mid_snapshots.clear()
    with pytest.raises(OSError, match="simulated checkpoint failure"):
        with _at_epoch(1700000400):
            A.record_slice_move(
                root, "UNCOMMITTED-SLICE", "running", "done", by="implementer", why="landed",
                notes={"commit": seeded["commit_sha"]},
            )
    assert len(mid_snapshots) == 1
    assert mid_snapshots[0][0] == []
    assert mid_snapshots[0][1]["resourceSpans"][0]["scopeSpans"] == baseline_export["resourceSpans"][0]["scopeSpans"]
    slice_res_attrs = _attr_map(mid_snapshots[0][1]["resourceSpans"][0]["resource"]["attributes"])
    assert slice_res_attrs["ao.export.attested_only"] is True
    assert slice_res_attrs["ao.export.partial"] is True
    assert slice_res_attrs["ao.export.unattested_ledgers"] == "slices.jsonl"
    assert A.export_otlp(root) == baseline_export
