"""Proofs belonged to other slices, content edits were lost, and claims lacked an owner (TRIAGE-4-REST).

Bind a move proof to the authorising slice, compare content paths without case and check
their recorded bytes, require a person's boundary statement, and distinguish an empty
withheld tree from a failed unpack. Claims beyond the bounded read remain counted.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import allowlist as AL, cli, language, lib as A
from tests.standin import program
from tests.test_catchup_evidence import (
    APPROVED, B_PART, PERSON, WITHOUT_B, _base, _catchup, _git, _land, _running, _write,
)
from tests.test_kiro_content import _add, _agent, _layer
from tests.test_review_tree import _tree_of
from tests.test_switches_and_bypass import _allow_candidate_verification
from tests.test_waiver_bound_2 import _bound_review


@pytest.mark.parametrize("lines", [
    ("[B7] ordinary", "[B8] split · move-only"),
    ("[B7] split · move-only", "[B8] split · move-only"),
])
def test_grant_records_no_other_or_ambiguous_slice_proof(project, monkeypatch, capsys, lines):
    _base(project, monkeypatch)
    root = project["root"]
    _running(root, *lines)
    _write(root, "mod.py", WITHOUT_B)
    _write(root, "parts/mod_b.py", B_PART)
    _allow_candidate_verification(monkeypatch, A.index_candidate(root))
    waiver = A.waive(root, "review", "B7", "reviewer unavailable", by="A. Person")

    assert cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None)) == 0

    grant = A.latest_authority_decision(root)
    assert grant["waiver"] == waiver["id"]
    assert "move_only" not in grant
    assert "no move proof recorded" in capsys.readouterr().out


def test_catchup_reviews_a_grant_proven_for_another_slice(project, monkeypatch, capsys):
    _base(project, monkeypatch)
    record = A.record_authority

    def legacy_grant(*args, **kwargs):
        kwargs["move_only"] = {"slice": "B8", "moved": 1}
        return record(*args, **kwargs)

    with monkeypatch.context() as old:
        old.setattr(A, "record_authority", legacy_grant)
        waiver, _, _, _ = _land(project, monkeypatch, capsys, "B7",
                                 {"mod.py": WITHOUT_B, "parts/mod_b.py": B_PART})
    assert A.review_waiver_ranges(project["root"])[0]["move_only"]["slice"] == "B8"
    capsys.readouterr()

    assert _catchup(project, plan=True, **PERSON) == 0

    out = capsys.readouterr().out
    assert "review by a family other than writer-family" in out
    assert "closes by proof" not in out
    assert [w["id"] for w in A.open_waivers(project["root"])] == [waiver["id"]]


@pytest.mark.parametrize("review_slice", ["B7", "B8", None])
def test_grant_uses_the_review_slice_when_a_review_takes_precedence(project, monkeypatch, capsys, review_slice):
    _base(project, monkeypatch)
    root = project["root"]
    _running(root, "[B7] split · move-only", "[B8] ordinary")
    _write(root, "mod.py", WITHOUT_B)
    _write(root, "parts/mod_b.py", B_PART)
    candidate = A.index_candidate(root)
    _allow_candidate_verification(monkeypatch, candidate)
    waiver = A.waive(root, "review", "B7", "reviewer unavailable", by="A. Person")
    _bound_review(monkeypatch)
    evidence = A.candidate_review_decision(root, project["reviews"], candidate["digest"])["match"][3]
    evidence["slice"] = review_slice

    assert cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None)) == 0

    grant = A.latest_authority_decision(root)
    assert grant["review"] == "r.md" and not grant.get("waiver")
    assert ("move_only" in grant) is (review_slice == "B7")
    assert [w["id"] for w in A.open_waivers(root)] == [waiver["id"]]


def test_case_aliases_in_one_import_are_refused_on_every_filesystem(project, tmp_path, capsys):
    source, tree = _tree_of(tmp_path, {".kiro/steering/Guide.md": b"# first\n",
                                       ".kiro/steering/guide.md": b"# second\n"})
    pin = _git(source, "commit-tree", tree, "-m", "two spellings")

    assert _add(project, f"{source}@{pin}", steering="Guide,guide") == 2

    out = capsys.readouterr().out
    assert "would be written by both steering Guide and steering guide" in out
    assert not (Path(project["root"]) / ".kiro").exists()


@pytest.mark.parametrize("target, kept", [
    (".kiro/steering/AO-PLAYBOOK.md", {".kiro/steering/ao-playbook.md"}),
    (".kiro/steering/Private/guide.md", {".kiro/steering/private/"}),
])
def test_kept_content_paths_are_case_folded(project, target, kept):
    refusal = A._content_refusal(project["root"], "kiro", target, kept)
    assert refusal and "keeps for itself" in refusal


def test_another_manifest_entry_owns_case_aliases_even_if_the_file_is_missing(project):
    root = Path(project["root"])
    (root / ".ao/content.json").write_text(json.dumps({"skills": [{"skill": "Guide", "files": {
        ".kiro/steering/Guide.md": "sha256:old"}}]}), encoding="utf-8")
    item = A._content_item("steering", "guide")
    item["files"] = {".kiro/steering/guide.md": ("kiro", b"new")}

    assert A._content_refusals(str(root), [item]) == [
        ".kiro/steering/guide.md was vendored as skill Guide: remove that first"]


@pytest.mark.parametrize("same_as_new", [False, True])
def test_reimport_preserves_a_hand_edited_agent(project, tmp_path, capsys, same_as_new):
    source, pin = _layer(tmp_path, {"agents/reader.json": _agent("reader")})
    assert _add(project, f"{source}@{pin}", agents="reader") == 0
    target = Path(project["root"]) / ".kiro/agents/reader.json"
    edited = _agent("reader", description="a person's edit")
    target.write_text(edited, encoding="utf-8")
    replacement = edited if same_as_new else _agent("reader", description="new upstream")
    _write(source, ".kiro/agents/reader.json", replacement)
    _git(source, "commit", "-q", "-m", "another pin")
    new_pin = _git(source, "rev-parse", "HEAD")
    manifest = Path(project["root"]) / ".ao/content.json"
    before = manifest.read_bytes()
    capsys.readouterr()

    assert _add(project, f"{source}@{new_pin}", agents="reader") == 2

    assert "changed since it was vendored: move it aside first" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == edited
    assert manifest.read_bytes() == before


def test_reimport_updates_an_unedited_agent(project, tmp_path):
    source, pin = _layer(tmp_path, {"agents/reader.json": _agent("reader")})
    assert _add(project, f"{source}@{pin}", agents="reader") == 0
    replacement = _agent("reader", description="new upstream")
    _write(source, ".kiro/agents/reader.json", replacement)
    _git(source, "commit", "-q", "-m", "another pin")

    assert _add(project, f"{source}@{_git(source, 'rev-parse', 'HEAD')}", agents="reader") == 0

    assert (Path(project["root"]) / ".kiro/agents/reader.json").read_text(encoding="utf-8") == replacement
    assert A.verify_content(project["root"]) == []


@pytest.mark.parametrize("by", [None, "architect"])
def test_boundary_replacement_requires_a_person(project, capsys, by):
    assert _catchup(project, boundary="replacement claims", by=by) == 2
    out = capsys.readouterr().out
    assert "--by" in out and ("required" in out if by is None else "person's statement" in out)


@pytest.mark.parametrize("rule", ["Bash(ao catchup --boundary:*)",
                                   "Bash(ao catchup --boundary text --by Alice)"])
def test_boundary_replacement_is_named_as_a_person_act(rule):
    reason = "replaces a waived range's commit claims"
    found = AL.problems(["claude", "--allowedTools", rule])
    assert any(why == reason and granted == rule for why, _, granted in found)
    assert any(why == reason for why, _ in AL.FORBIDDEN)


def test_boundary_person_is_recorded_in_review_evidence(project, monkeypatch, capsys, tmp_path):
    _base(project, monkeypatch)
    _land(project, monkeypatch, capsys, "B7", {"src/a.py": "value = 1\n"}, note=None)
    reviewer = program(tmp_path, "reviewer", "\n".join(f"print({line!r})" for line in APPROVED))
    cfg = dict(project, reviewer={"id": "r", "family": "review-family", "argv": [reviewer]})

    assert _catchup(cfg, boundary="replacement claims", **PERSON) == 0

    (path,) = (Path(project["root"]) / "semantic-review").glob("*.md")
    evidence = A.review_evidence(path.read_text(encoding="utf-8"))
    assert evidence["boundary"] == "replacement claims" and "claims" not in evidence
    person = evidence["boundary_by"]
    assert person["by"] == "A. Person"
    assert "user" in person and isinstance(person["interactive"], bool)


def test_boundary_alone_accepts_a_named_person(project):
    statement, refusal = cli._catchup_statement(project, SimpleNamespace(boundary="claims", by="A. Person"))
    assert refusal is None and statement["by"] == "A. Person"


def test_claims_beyond_the_documented_cap_are_counted(project, monkeypatch):
    root = project["root"]
    base = _git(root, "rev-parse", "HEAD")
    for number in range(3):
        _write(root, "src/a.py", f"value = {number}\n")
        _git(root, "commit", "-q", "-m", f"step {number}")
    monkeypatch.setattr(A, "REVIEW_CLAIMS_MAX_COMMITS", 1)

    claims = A.review_range_claims(root, f"{base}..HEAD")

    assert claims["commits"] == 3 and claims["inlined"] == 1
    assert len(claims["subjects"]) == 1 and "step 2" in claims["subjects"][0]
    assert "not inlined for size: 2 commit(s)" in claims["text"]
    assert "step 0" not in claims["text"] and "step 1" not in claims["text"]


@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("key", ["prompt.review-tree", "prompt.review-range-tree"])
def test_an_empty_withheld_tree_is_not_an_unpack_failure(project, tmp_path, lang, key):
    repo, tree = _tree_of(tmp_path, {"AGENTS.md": b"instructions\n", ".claude/settings.json": b"{}\n"})
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert cli._unpack_candidate(repo, tree, str(fresh)) == str(fresh)
    candidate = fresh / cli.REVIEW_TREE_DIR
    assert candidate.is_dir() and list(candidate.iterdir()) == []

    note = language.text(dict(project, language=lang), key, folder="candidate", withheld="AGENTS.md, .claude")

    if lang == "en":
        assert "empty or missing" not in note
        assert "is empty, everything in the tree was withheld" in note
        assert "is missing, the unpacking failed" in note
    else:
        assert "boşsa ya da yoksa" not in note
        assert "boşsa ağaçtaki her şey dışarıda bırakılmıştır" in note
        assert "yoksa açma başarısız olmuştur" in note
