"""tools/evidence.py measures what a checkout's ledgers show about the guarantee, and only reads (EVIDENCE).

docs/evidence.md gives numbers about ao's own repository - commits whose tree a grant bound, trailers that match the
grant they name, reviews by kind, verdict, family and tier, waivers opened and how each closed - and a reader can make
each again only if the tool that printed them counts right. These build a checkout whose answer is known - a commit
no grant bound, a commit after the first trailers that carries none, a review by the author's own family, an
artefact whose bytes changed, a waiver still open - and measure it as a reader would: in the checkout, from a copy of
its ledgers, and in a clone that keeps none. A ledger whose chain does not validate is said, and nothing is measured.
"""
import json
import os
import shutil
import subprocess
import sys

from ao import lib as A

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "evidence.py")


def _git(root, *args):
    return subprocess.run([A.git_binary(), *args], cwd=root, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def _stage(root, name, text):
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        fh.write(text)
    _git(root, "add", name)
    return _git(root, "write-tree")


def _review(root, name, verdict, kind, family=None, counts=None, tier="independent", author=None):
    evidence = {"kind": kind, "verdict": verdict}
    if family:
        evidence.update(reviewer={"id": f"{family}-reviewer", "family": family})
    if counts:
        evidence["counts"] = counts
    if tier:
        evidence["review_tier"] = tier
    if author:
        evidence.update(author={"family": author}, commits="HEAD~1..HEAD")
    text = f"# Review {name}\n\n{A.review_evidence_line(evidence)}\n\nVERDICT: {verdict}\n"
    A.write_review_artefact(root, "semantic-review", name, text, evidence=evidence, verdict=verdict,
                            reviewer=f"{family}-reviewer" if family else None)


def _checkout(project):
    """Three commits on the first grant's head - granted, granted with trailers, and one no grant bound - with
    their reviews and waivers. Returns the checkout's root."""
    root = project["root"]
    base = _git(root, "rev-parse", "HEAD")
    tree = _stage(root, "a.py", "a = 1\n")
    A.record_authority(root, True, [], "sha256:a", "V-1", "C-1", review="r1.md",
                       candidate={"head": base, "index_tree": tree}, scope={"paths": []})
    _git(root, "commit", "-q", "-m", "feat: a")
    tree = _stage(root, "b.py", "b = 1\n")
    grant = A.record_authority(root, True, [], "sha256:b", "V-2", "C-2", review="r2.md",
                               candidate={"head": _git(root, "rev-parse", "HEAD"), "index_tree": tree},
                               scope={"paths": []})
    _git(root, "commit", "-q", "-m", A.with_trailers("feat: b", A.grant_trailers(grant)))
    _stage(root, "c.py", "c = 1\n")
    _git(root, "commit", "-q", "-m", "feat: c, which no grant bound")
    _review(root, "r1.md", "NEEDS_CHANGES", "index-candidate", "openai", {"BLOCKER": 1, "HIGH": 2, "LOW": 1})
    _review(root, "r2.md", "APPROVED", "index-candidate", "google", {"BLOCKER": 0, "HIGH": 0})
    _review(root, "r3.md", "APPROVED", "commit-range", "deepseek", {"MEDIUM": 1}, author="anthropic")
    _review(root, "r4.md", "NEEDS_CHANGES", "commit-range", "anthropic", {"HIGH": 1}, author="anthropic")
    _review(root, "r5.md", "UNAVAILABLE", "commit-range", tier=None)
    with open(os.path.join(root, "semantic-review", "r2.md"), "a", encoding="utf-8") as fh:
        fh.write("edited after it was recorded\n")
    waived = [A.waive(root, "review", f"S{n}", "deferred, not skipped", "A. Person")["id"] for n in (1, 2, 3)]
    A.close_waiver(root, waived[0], "reviewed: APPROVED")
    A.close_waiver(root, waived[1], "closed by proof: 3 definition(s) moved")
    return root


def _measure(*words):
    done = subprocess.run([sys.executable, TOOL, *words], capture_output=True, text=True, encoding="utf-8")
    return done.returncode, done.stdout, done.stderr


def _files(folder):
    found = {}
    for here, _, names in os.walk(folder):
        for name in names:
            with open(os.path.join(here, name), "rb") as fh:
                found[os.path.relpath(os.path.join(here, name), folder)] = fh.read()
    return found


def test_a_checkout_s_answer_is_measured_as_it_is_and_nothing_in_it_is_written(project):
    root = _checkout(project)
    before = _files(os.path.join(root, ".ao"))

    code, out, _ = _measure("--root", root, "--rev", "HEAD", "--json")

    assert code == 0
    found = json.loads(out)
    assert found["ledgers"] is True
    c = found["commits"]
    assert (c["commits"], c["merges"], c["tree_bound"], c["first_grant"]) == (3, 0, 2, "C-1")
    # From the first commit carrying trailers on, one matches its grant and the next carries none.
    assert (c["carrying"], c["since_first_trailer"], c["trailers_match"], c["grant_on_record"]) == (1, 2, 1, 1)
    assert found["authority"] == {"rows": 2, "grants": 2, "refusals": 0, "on_review": 2, "on_waiver": 0,
                                  "on_neither": 0, "on_neither_days": []}
    r = found["reviews"]
    assert r["reviews"] == 5 and r["artefacts_held"] == 4
    assert r["by_kind"] == {"index-candidate": {"NEEDS_CHANGES": 1, "APPROVED": 1},
                            "commit-range": {"APPROVED": 1, "NEEDS_CHANGES": 1, "UNAVAILABLE": 1}}
    assert r["by_family"] == {"openai": {"NEEDS_CHANGES": 1}, "google": {"APPROVED": 1},
                              "deepseek": {"APPROVED": 1}, "anthropic": {"NEEDS_CHANGES": 1}}
    assert r["tiers"] == {"another model family": 4, "none": 1}
    assert r["counts"] == {"BLOCKER": 1, "HIGH": 3, "LOW": 1, "MEDIUM": 1}
    assert (r["ranges"], r["ranges_with_author_family"], r["reviewed_by_author_family"]) == (3, 2, ["r4.md"])
    w = found["waivers"]
    assert (w["opened"], w["closed"], len(w["open"]), w["gates"]) == (3, 2, 1, {"review": 3})
    assert w["outcomes"] == {"review APPROVED": 1, "move proof": 1}
    # It only read: no lock file was made beside a ledger, and no byte under .ao/ changed.
    assert _files(os.path.join(root, ".ao")) == before


def test_the_report_says_each_number_as_a_line(project):
    root = _checkout(project)

    code, out, _ = _measure("--root", root, "--rev", "HEAD")

    assert code == 0
    assert "2 of 3 have a tree a grant bound" in out
    assert "1 of 2 match the grant they name, 1 of them on record" in out
    assert "landed ranges recording their author's family: 2 of 3; reviewed by that family: 1" in out
    assert "waivers: 3 opened (review 3)" in out and "2 closed, 1 open" in out


def test_a_copy_of_the_ledgers_measures_as_the_checkout_does_and_a_clone_checks_the_trailers_alone(project, tmp_path):
    root = _checkout(project)
    state = tmp_path / "state"
    shutil.copytree(os.path.join(root, ".ao"), state / ".ao")
    shutil.copytree(os.path.join(root, "semantic-review"), state / "semantic-review")
    clone = tmp_path / "clone"
    _git(str(tmp_path), "clone", "-q", root, str(clone))

    copied = json.loads(_measure("--root", root, "--state", str(state), "--rev", "HEAD", "--json")[1])
    alone = json.loads(_measure("--root", str(clone), "--rev", "HEAD", "--json")[1])

    assert copied == json.loads(_measure("--root", root, "--rev", "HEAD", "--json")[1])
    # A clone keeps no ledgers: its whole history is read, and the trailers are judged by tree alone.
    assert alone["ledgers"] is False and set(alone) == {"rev", "ledgers", "commits"}
    assert (alone["commits"]["commits"], alone["commits"]["carrying"], alone["commits"]["trailers_match"]) == (4, 1, 1)


def test_a_ledger_whose_chain_does_not_validate_is_named_and_nothing_is_measured(project, tmp_path):
    root = _checkout(project)
    state = tmp_path / "state"
    shutil.copytree(os.path.join(root, ".ao"), state / ".ao")
    ledger = state / ".ao" / "ledger" / "authority.jsonl"
    ledger.write_text(ledger.read_text(encoding="utf-8").replace('"C-1"', '"C-9"'), encoding="utf-8")

    code, out, err = _measure("--root", root, "--state", str(state), "--rev", "HEAD")

    assert code == 1 and out == ""
    assert "a ledger does not validate, so nothing is measured" in err and "authority.jsonl" in err


def _tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location("evidence_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_empty_authority_ledger_is_passed_as_itself_and_counts_no_grant_on_record(tmp_path):
    """EVIDENCE-2, from the review of EVIDENCE: `tokens if authority else None` read an empty ledger as no ledger. The
    trailers are held to the ledger as `ao commit-check` holds them - a grant the checkout's ledger does not hold fails
    nothing, since a grant stays in the ledger of the checkout that made it - and the grant is counted as not on
    record."""
    root = str(tmp_path / "repo")
    os.makedirs(root)
    _git(root, "init", "-q")
    with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("a\n")
    _git(root, "add", "a.txt")
    tree = _git(root, "write-tree").strip()
    _git(root, "commit", "-q", "-m", "a", "-m", f"Ao-Grant: C-1\nAo-Tree: {tree}\nAo-Verification: V-1\nAo-Review: r.md")
    tool = _tool()

    measured = tool._commits(root, "HEAD", [])

    assert (measured["since_first_trailer"], measured["trailers_match"], measured["grant_on_record"]) == (1, 1, 0)


def test_an_artefact_the_ledger_names_outside_the_reviews_folder_is_not_read(tmp_path):
    """EVIDENCE-2: the ledger, which anyone on the machine can edit, named an artefact by an absolute path, and the tool
    read whatever file that was."""
    outside = tmp_path / "elsewhere.md"
    outside.write_bytes(b"VERDICT: APPROVED\n")
    digest = "sha256:" + __import__("hashlib").sha256(outside.read_bytes()).hexdigest()
    state = tmp_path / "state"
    (state / "semantic-review").mkdir(parents=True)
    rows = [{"artefact": str(outside), "sha256": digest}, {"artefact": "../elsewhere.md", "sha256": digest}]

    assert _tool()._reviews(str(state), rows)["artefacts_held"] == 0
