"""tools/evidence.py measures what a checkout's ledgers show about the guarantee, and only reads (EVIDENCE).

docs/evidence.md gives numbers about ao's own repository - commits whose tree a grant bound, trailers that match the
grant they name, reviews by kind, verdict, family and tier, waivers opened and how each closed - and a reader can make
each again only if the tool that printed them counts right. These build a checkout whose answer is known - a commit
no grant bound, a commit after the first trailers that carries none, a review by the author's own family, an
artefact whose bytes changed, a waiver still open - and measure it as a reader would: in the checkout, from a copy of
its ledgers, and in a clone that keeps none. A ledger whose chain does not validate is said, and nothing is measured.
Platform lane runs recorded by `ao evidence` in `.ao/ledger/platform-runs.jsonl` are held below too (LANE-EVIDENCE).
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from ao import cli, lib as A
from tests import standin

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
    rows = [{"artefact": str(outside), "sha256": digest}, {"artefact": "../elsewhere.md", "sha256": digest},
            {"artefact": "a\x00b.md", "sha256": digest}]

    assert _tool()._reviews(str(state), rows)["artefacts_held"] == 0


def _args(**overrides):
    values = dict(action="show", all=False, json=False, platform=None, run=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def _head(root):
    return subprocess.run([A.git_binary(), "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(root, message):
    subprocess.run([A.git_binary(), "-c", "core.hooksPath=", "commit", "--allow-empty", "-m", message],
                   cwd=root, check=True, capture_output=True)
    return _head(root)


def test_a_recorded_run_is_read_back_with_the_commits_head_added(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    first = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("39026620122", commit=first, platform="windows")

    assert cli.cmd_evidence(project, _args(action="record", platform="Windows", run="39026620122")) == 0

    out = capsys.readouterr().out
    assert "recorded windows run 39026620122" in out and first[:10] in out
    second = _commit(root, "after the lane")
    assert second != first
    assert cli.cmd_evidence(project, _args(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["platform"] for row in payload] == ["windows"]
    row = payload[0]
    assert row["green"] is True and row["known"] is True and row["behind"] == 1
    assert row["vouches"] is True and row["stale"] is False
    assert row["commit"] == first and row["jobs_total"] == 1 and row["jobs_passed"] == 1 and row["jobs_failed"] == 0
    assert row["suite"] == "full"
    rows = A.platform_runs(root)
    assert len(rows) == 1 and rows[0]["ordinal"] == 1 and rows[0]["previous"] is None


def test_show_reads_the_newest_of_each_platform_unless_all_is_named(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    first = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("1", commit=first, platform="windows")
    A.record_platform_run(root, platform="windows", run="1", at=100)
    second = _commit(root, "move")
    gh.add_run("2", commit=second, platform="windows")
    gh.add_run("3", commit=second, platform="linux")
    A.record_platform_run(root, platform="windows", run="2", at=200)
    A.record_platform_run(root, platform="linux", run="3", at=150)

    assert cli.cmd_evidence(project, _args(json=True)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert [(row["platform"], row["run"], row["behind"]) for row in summary] == [
        ("linux", "3", 0), ("windows", "2", 0)]

    assert cli.cmd_evidence(project, _args(all=True, json=True)) == 0
    every = json.loads(capsys.readouterr().out)
    assert [row["run"] for row in every] == ["1", "2", "3"]

    assert cli.cmd_evidence(project, _args()) == 0
    out = capsys.readouterr().out
    assert "windows" in out and "linux" in out and "green" in out and "behind HEAD" in out


def test_doctor_names_a_run_that_no_longer_covers_the_tree(project, tmp_path, monkeypatch):
    root = project["root"]
    first = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("1", commit=first, platform="windows", conclusion="failure")
    A.record_platform_run(root, platform="windows", run="1", at=100)
    problems = dict(cli.doctor_problems(project))
    assert "evidence-red:windows" in problems
    assert "failed 1 job(s) (failure)" in problems["evidence-red:windows"]

    gh.add_run("2", commit=first, platform="windows", conclusion="success")
    A.record_platform_run(root, platform="windows", run="2", at=200)
    problems = dict(cli.doctor_problems(project))
    assert not [key for key in problems if key.startswith("evidence-red")]

    _commit(root, "move")
    problems = dict(cli.doctor_problems(dict(project, evidence={"max_commits_behind": 0})))
    assert "1 commit(s) behind HEAD" in problems["evidence-stale:windows"]
    assert "evidence.max_commits_behind 0" in problems["evidence-stale:windows"]


def test_a_run_that_names_no_commit_it_holds_vouches_for_nothing(project, tmp_path, monkeypatch):
    root = project["root"]
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("1", commit="", platform="windows")
    A.record_platform_run(root, platform="windows", run="1", at=100)
    problems = dict(cli.doctor_problems(project))
    assert "names no commit" in problems["evidence-unresolved:windows"]

    gh.add_run("2", commit="0" * 40, platform="linux")
    A.record_platform_run(root, platform="linux", run="2", at=110)
    problems = dict(cli.doctor_problems(project))
    assert "cannot resolve" in problems["evidence-unresolved:linux"]


def test_doctor_is_quiet_without_a_recorded_run(project):
    assert [key for key, _ in cli.doctor_problems(project) if key.startswith("evidence-")] == []


def test_plain_or_uncheckpointed_rows_are_refused_as_current_evidence(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: `platform_runs` read plain legacy rows with no chain or checkpoint, so an
    implementer could edit `.ao/ledger/platform-runs.jsonl` before any chained append to claim green at HEAD
    and have `ao evidence show`, `ao gain`, and `ao doctor --check` treat it as green."""
    root = project["root"]
    first = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("new", commit=first, platform="windows")
    ledger = os.path.join(root, A.PLATFORM_RUNS_FILE)
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    with open(ledger, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": 1, "platform": "windows", "commit": first, "run": "old",
                             "conclusion": "failure", "suite": "full"}) + "\n")
    with open(ledger, encoding="utf-8") as fh:
        text = fh.read()
    with open(ledger, "w", encoding="utf-8") as fh:
        fh.write(text.replace('"failure"', '"success"'))

    assert A.platform_runs(root) == []
    assert cli.cmd_evidence(project, _args()) == 0
    assert "does not record ao appending" in capsys.readouterr().out
    problems = dict(cli.doctor_problems(project))
    assert "evidence-unattested" in problems and "does not record ao appending" in problems["evidence-unattested"]
    assert "evidence-red:windows" not in problems
    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="new")) == 1
    assert "could not be written" in capsys.readouterr().out

    with open(ledger, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"previous": None, "ordinal": 1, "at": 1, "platform": "windows", "commit": first,
                             "run": "forged", "conclusion": "success", "suite": "full"}) + "\n")
    assert A.platform_runs(root) == []
    problems = dict(cli.doctor_problems(project))
    assert "evidence-unattested" in problems and "does not record ao appending" in problems["evidence-unattested"]
    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="new")) == 1
    capsys.readouterr()

    os.remove(ledger)
    A.record_platform_run(root, platform="windows", run="new", at=2)
    rows = A.platform_runs(root)
    assert [row["run"] for row in rows] == ["new"]
    assert rows[0]["previous"] is None and rows[0]["ordinal"] == 1
    assert [key for key, _ in cli.doctor_problems(project) if key.startswith("evidence-")] == []


def test_record_refuses_missing_or_option_shaped_run_and_writes_nothing(project, tmp_path, monkeypatch, capsys):
    FakeGh(tmp_path, monkeypatch, root=project["root"])
    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="--repo=other/repo")) == 2
    assert "must be a run id" in capsys.readouterr().out
    assert not os.path.exists(os.path.join(project["root"], A.PLATFORM_RUNS_FILE))

    assert cli.cmd_evidence(project, _args(action="record", platform="windows")) == 2
    out = capsys.readouterr().out
    assert "--run" in out


def test_a_ledger_that_cannot_be_written_is_refused_with_the_reason(project, tmp_path, capsys, monkeypatch):
    FakeGh(tmp_path, monkeypatch, root=project["root"])

    def refuse(*args, **kwargs):
        raise OSError("another run holds the ledger")
    monkeypatch.setattr(A, "record_platform_run", refuse)

    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="1")) == 1
    out = capsys.readouterr().out
    assert "nothing recorded" in out and "could not be written" in out and "another run holds the ledger" in out


def test_show_says_no_run_was_recorded_instead_of_zero(project, capsys):
    assert cli.cmd_evidence(project, _args()) == 0

    out = capsys.readouterr().out
    assert "no platform run recorded" in out and ".ao/ledger/platform-runs.jsonl" in out


def test_a_rerun_in_the_same_second_replaces_the_earlier_run_by_ledger_order(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: newest-run selection compared second-resolution `at` timestamps with `>`,
    so a failed rerun recorded in the same second as a green run tied and left the green row selected."""
    root = project["root"]
    first = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("1", commit=first, platform="windows", conclusion="success")
    gh.add_run("2", commit=first, platform="windows", conclusion="failure")
    A.record_platform_run(root, platform="windows", run="1", at=100)
    A.record_platform_run(root, platform="windows", run="2", at=100)

    assert cli.cmd_evidence(project, _args(json=True)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert [(row["platform"], row["run"], row["green"], row["vouches"]) for row in summary] == [
        ("windows", "2", False, False)]

    problems = dict(cli.doctor_problems(project))
    assert "evidence-red:windows" in problems
    assert "run 2 failed 1 job(s) (failure)" in problems["evidence-red:windows"]


def test_record_refuses_a_duplicate_run_of_the_same_platform(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: recording did not enforce one row per `(platform, run)`, so re-recording an
    older green run 17 after a failed run 18 made the duplicate the newest row and hid run 18's failure."""
    root = project["root"]
    first = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("17", commit=first, platform="windows", conclusion="success",
               jobs=[{"name": "test (windows-latest, 3.12)", "status": "completed", "conclusion": "success"},
                     {"name": "test (ubuntu-latest, 3.12)", "status": "completed", "conclusion": "success"}])
    gh.add_run("18", commit=first, platform="windows", conclusion="failure")
    A.record_platform_run(root, platform="windows", run="17", at=100)
    A.record_platform_run(root, platform="windows", run="18", at=110)

    assert cli.cmd_evidence(project, _args(action="record", platform="Windows", run="17")) == 2
    out = capsys.readouterr().out
    assert "nothing recorded" in out and "windows run 17 is already recorded" in out

    A.record_platform_run(root, platform="linux", run="17", at=120)
    summary = {row["platform"]: row for row in A.platform_run_summaries(root, project)}
    assert summary["windows"]["run"] == "18" and summary["windows"]["vouches"] is False
    assert summary["linux"]["run"] == "17" and summary["linux"]["vouches"] is True
    problems = dict(cli.doctor_problems(project))
    assert "run 18 failed 1 job(s) (failure)" in problems["evidence-red:windows"]


def test_a_run_whose_commit_is_not_an_ancestor_of_head_does_not_vouch_for_head(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: `rev-list C..HEAD` is 0 when C is a descendant of HEAD on another branch, so a
    green run at C was read as 0 commits behind HEAD and vouched for a tree it did not test."""
    root = project["root"]
    base = _head(root)
    other = _commit(root, "on another branch")
    subprocess.run([A.git_binary(), "checkout", "-q", base], cwd=root, check=True)
    assert _head(root) == base
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("19", commit=other, platform="windows")
    A.record_platform_run(root, platform="windows", run="19", at=100)

    assert cli.cmd_evidence(project, _args(json=True)) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["green"] is False and row["known"] is True and row["behind"] is None and row["vouches"] is False

    problems = dict(cli.doctor_problems(project))
    assert "not an ancestor of HEAD" in problems["evidence-unresolved:windows"]


def test_record_reads_the_commit_the_lane_tested_from_gh_instead_of_substituting_head(
        project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: recording a lane run of commit A after the checkout moved to B must record
    the commit the CI run tested (`headSha` from `gh`), never current HEAD."""
    root = project["root"]
    tested = _head(root)
    after = _commit(root, "after the lane")
    assert after != tested
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("20", commit=tested, platform="windows")

    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="20")) == 0
    capsys.readouterr()
    assert cli.cmd_evidence(project, _args(json=True)) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["commit"] == tested and row["behind"] == 1
    problems = dict(cli.doctor_problems(dict(project, evidence={"max_commits_behind": 0})))
    assert "1 commit(s) behind HEAD" in problems["evidence-stale:windows"]


FAKE_GH = '''
import json
import os
import sys

RUN_VIEW_FIELDS = {
    "attempt",
    "conclusion",
    "createdAt",
    "databaseId",
    "displayTitle",
    "event",
    "headBranch",
    "headSha",
    "jobs",
    "name",
    "number",
    "startedAt",
    "status",
    "updatedAt",
    "url",
    "workflowDatabaseId",
    "workflowName",
}

here = os.path.dirname(os.path.abspath(sys.argv[0]))
with open(os.path.join(here, "gh-calls.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "gh_repo": os.environ.get("GH_REPO")}) + "\\n")
with open(os.path.join(here, "gh-world.json"), encoding="utf-8") as fh:
    world = json.load(fh)
if not world.get("authenticated", True):
    sys.stderr.write("To get started with GitHub CLI, please run:  gh auth login\\n")
    sys.exit(4)
if world.get("network_error"):
    sys.stderr.write(world["network_error"] + "\\n")
    sys.exit(1)
args = sys.argv[1:]
runs = world.get("runs") or {}
if args[:2] == ["run", "view"] and len(args) >= 3:
    if "--json" in args:
        idx = args.index("--json")
        requested = [field for field in (args[idx + 1] if idx + 1 < len(args) else "").split(",") if field]
        for field in requested:
            if field not in RUN_VIEW_FIELDS:
                sys.stderr.write(f'Unknown JSON field: "{field}"\\n')
                sys.exit(1)
        fields = set(requested)
    else:
        fields = None
    if args[2] in runs:
        run = runs[args[2]]
        if fields is not None:
            run = {key: value for key, value in run.items() if key in fields}
        print(json.dumps(run))
        sys.exit(0)
sys.stderr.write(f"run {args[2] if len(args) >= 3 else '?'} not found\\n")
sys.exit(1)
'''


class FakeGh:
    """A stand-in `gh` on PATH that answers `gh run view` from canned JSON and records every call."""

    def __init__(self, tmp_path, monkeypatch, root=None, repository="acme/proj"):
        self.bin = tmp_path / "gh-bin"
        self.bin.mkdir(exist_ok=True)
        self.program = standin.program(self.bin, "gh", FAKE_GH)
        monkeypatch.setenv("PATH", str(self.bin) + os.pathsep + os.environ.get("PATH", ""))
        monkeypatch.setattr(A, "_BIN_DIRS", ())
        monkeypatch.setattr(A, "_BIN_GLOBS", ())
        self.repository = repository
        if root is not None:
            subprocess.run([A.git_binary(), "remote", "add", "origin", f"https://github.com/{repository}.git"],
                           cwd=root, capture_output=True)
        self._runs = {}
        self._seq = 0
        self._authenticated = True
        self._network_error = ""
        self._write()

    def _write(self):
        world = {
            "authenticated": self._authenticated,
            "network_error": self._network_error,
            "runs": self._runs,
        }
        (self.bin / "gh-world.json").write_text(json.dumps(world), encoding="utf-8")

    def add_run(self, run_id, *, commit, platform="windows", status="completed", conclusion="success",
                suite="full", repository=None, number=None, created_at=None, jobs=None):
        run_id = str(run_id)
        self._seq += 1
        run_number = (int(run_id) if run_id.isdigit() else self._seq) if number is None else int(number)
        stamp = created_at or datetime.fromtimestamp(
            1_700_000_000 + (run_number if run_number < 100_000_000 else self._seq), timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        repo = self.repository if repository is None else repository
        if jobs is None:
            jobs = [{"name": f"test ({platform}-latest, 3.12)", "status": status, "conclusion": conclusion}]
        payload = {
            "databaseId": int(run_id) if run_id.isdigit() else run_id,
            "number": run_number,
            "createdAt": stamp,
            "status": status,
            "conclusion": conclusion,
            "headSha": commit,
            "workflowName": suite,
            "name": suite,
            "url": f"https://github.com/{repo}/actions/runs/{run_id}" if repo else "",
            "jobs": jobs,
        }
        self._runs[run_id] = payload
        self._write()
        return payload

    def fail(self, *, authenticated=True, network_error=""):
        self._authenticated = authenticated
        self._network_error = network_error
        self._write()

    def calls(self):
        path = self.bin / "gh-calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_record_reads_the_run_from_gh_and_takes_no_caller_asserted_outcome(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: `ao evidence record` promoted caller-asserted `--passed 0 --commit HEAD`
    to a green attestation without checking that the lane ran. Evidence comes from CI through `gh`, never from
    what the caller asserts."""
    root = project["root"]
    head = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("39026620122", commit=head, platform="windows", status="completed", conclusion="failure")

    parser = cli.build_parser(root)
    ev = next(action.choices["evidence"] for action in parser._actions if isinstance(action.choices, dict))
    flags = {flag for action in ev._actions for flag in action.option_strings}
    assert not (flags & {"--passed", "--failed", "--skipped", "--suite", "--commit"})

    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="39026620122",
                                           passed=0, failed=0, commit=head)) == 0
    capsys.readouterr()
    calls = gh.calls()
    assert len(calls) == 1 and calls[0]["argv"][:3] == ["run", "view", "39026620122"]

    assert cli.cmd_evidence(project, _args(json=True)) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["conclusion"] == "failure" and row["jobs_total"] == 1 and row["jobs_failed"] == 1
    assert row["green"] is False and row["vouches"] is False
    problems = dict(cli.doctor_problems(project))
    assert "evidence-red:windows" in problems


def test_runs_not_on_this_repo_not_finished_not_successful_or_not_ancestor_never_read_as_green(
        project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: a run that is not on this repository, not finished, not successful, or
    not on a commit HEAD holds as an ancestor is recorded as such and never reads as green."""
    monkeypatch.setattr(A, "session_paths", lambda cfg: (None, None))
    root = project["root"]
    base = _head(root)
    other = _commit(root, "on another branch")
    subprocess.run([A.git_binary(), "checkout", "-q", base], cwd=root, check=True)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("101", commit=base, platform="windows", repository="other/fork")
    gh.add_run("102", commit=base, platform="windows", status="in_progress", conclusion="")
    gh.add_run("103", commit=base, platform="windows",
               jobs=[{"name": "test (ubuntu-latest, 3.12)", "status": "completed", "conclusion": "success"}])
    gh.add_run("104", commit=other, platform="windows", status="completed", conclusion="success")

    for run_id, expected_note in (
        ("101", "not this repository"),
        ("102", "not finished"),
        ("103", "no windows job"),
        ("104", "not an ancestor of HEAD"),
    ):
        assert cli.cmd_evidence(project, _args(action="record", platform="windows", run=run_id)) == 0
        out = capsys.readouterr().out
        assert "(red)" in out
        stored = A.platform_runs(root)[-1]
        assert stored["run"] == run_id and stored["green"] is False
        summary = A.platform_run_summaries(root, project)[0]
        assert summary["run"] == run_id and summary["green"] is False and summary["vouches"] is False
        problems = dict(cli.doctor_problems(project))
        problem_text = " ".join(v for k, v in problems.items() if k.startswith("evidence-"))
        assert expected_note in problem_text
        assert cli.cmd_gain(project, SimpleNamespace(all=False, since=None, days=14, slices=False,
                                                     json=False, sources=False, quota=False)) == 0
        gain_out = capsys.readouterr().out
        assert "windows red" in gain_out and "windows green" not in gain_out


def test_recording_an_older_run_later_does_not_hide_a_newer_ci_run(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: selecting the last appended row hid a newer failed CI run 42 when a
    distinct older green run 41 was recorded later. The newest run is the one CI says is newest (its own
    run number and creation time), never the row appended last."""
    monkeypatch.setattr(A, "session_paths", lambda cfg: (None, None))
    root = project["root"]
    first = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("42", commit=first, platform="windows", conclusion="failure",
               number=42, created_at="2026-10-19T12:02:00Z")
    gh.add_run("41", commit=first, platform="windows", conclusion="success",
               number=41, created_at="2026-10-19T12:01:00Z")
    A.record_platform_run(root, platform="windows", run="42", at=100)
    A.record_platform_run(root, platform="windows", run="41", at=200)

    assert cli.cmd_evidence(project, _args(json=True)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert [(row["platform"], row["run"], row["green"], row["vouches"]) for row in summary] == [
        ("windows", "42", False, False)]

    assert cli.cmd_evidence(project, _args(all=True, json=True)) == 0
    every = json.loads(capsys.readouterr().out)
    assert [row["run"] for row in every] == ["42", "41"]

    assert cli.cmd_gain(project, SimpleNamespace(all=False, since=None, days=14, slices=False,
                                                 json=False, sources=False, quota=False)) == 0
    gain_out = capsys.readouterr().out
    assert "windows red" in gain_out and "windows green" not in gain_out

    problems = dict(cli.doctor_problems(project))
    assert "evidence-red:windows" in problems
    assert "run 42 failed 1 job(s) (failure)" in problems["evidence-red:windows"]


def test_gh_run_view_rejects_unknown_json_fields_and_record_requests_only_run_fields(
        project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: `PLATFORM_RUN_FIELDS` asked `gh run view --json` for `repository`,
    `headRepository`, and `isCrossRepository` (pull-request fields, not run fields), which real `gh` refuses with
    `Unknown JSON field`; and `_platform_on_repository` fell back to True or folder-name matching without a remote."""
    root = project["root"]
    base = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root, repository="acme/proj")
    gh.add_run("105", commit=base, platform="windows", repository="acme/proj")
    gh.add_run("106", commit=base, platform="windows", repository="")

    try:
        A.gh_json(root, gh.program, "run", "view", "105", "--json", "databaseId,repository")
    except A.GhUnreadable as exc:
        assert 'Unknown JSON field: "repository"' in str(exc)
    else:
        raise AssertionError("FakeGh must reject non-run JSON fields the way real gh does")

    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="105")) == 0
    out = capsys.readouterr().out
    assert "(green)" in out
    argv = gh.calls()[-1]["argv"]
    requested = set(argv[argv.index("--json") + 1].split(","))
    assert requested.isdisjoint({"repository", "headRepository", "isCrossRepository"})

    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="106")) == 0
    out = capsys.readouterr().out
    assert "(red)" in out
    summary = A.platform_run_summaries(root, project)[0]
    assert summary["run"] == "106" and summary["on_repository"] is False and summary["green"] is False

    subprocess.run([A.git_binary(), "remote", "remove", "origin"], cwd=root, check=True)
    reloaded = A.platform_run_reading(root, project, A.platform_runs(root)[0])
    assert reloaded["on_repository"] is False and reloaded["green"] is False


def test_unattested_rows_leave_attested_prefix_readable_and_clone_records_its_own(
        project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: `_require_checkpointed_runs` refused the whole ledger whenever its row count
    did not equal this machine's checkpoint count, breaking a clone/CI runner/moved checkout and discarding attested
    rows when an unattested row was appended after them."""
    from ao.storage import CHAIN_PREVIOUS_FIELD, chained_row_digest
    monkeypatch.setattr(A, "session_paths", lambda cfg: (None, None))
    root = project["root"]
    first = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("1", commit=first, platform="windows", conclusion="failure")
    gh.add_run("2", commit=first, platform="windows", conclusion="success")
    A.record_platform_run(root, platform="windows", run="1", at=100)

    ledger = os.path.join(root, A.PLATFORM_RUNS_FILE)
    with open(ledger, encoding="utf-8") as fh:
        existing = [json.loads(line) for line in fh]
    forged = dict(existing[0], run="forged", conclusion="success", status="completed", green=True,
                  jobs_passed=1, jobs_failed=0, ordinal=2,
                  **{CHAIN_PREVIOUS_FIELD: chained_row_digest(existing[-1], A.PLATFORM_RUNS_CHAIN)})
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(forged) + "\n")

    assert [row["run"] for row in A.platform_runs(root)] == ["1"]
    assert cli.cmd_evidence(project, _args()) == 0
    show_out = capsys.readouterr().out
    assert "the first 1 row(s) are ao's own and stay" in show_out and "run 1" in show_out
    problems = dict(cli.doctor_problems(project))
    assert "evidence-unattested" in problems
    assert "the first 1 row(s) are ao's own and stay" in problems["evidence-unattested"]
    assert "evidence-red:windows" in problems
    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="2")) == 1
    assert "the first 1 row(s) are ao's own and stay" in capsys.readouterr().out

    with open(ledger, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(existing[0]) + "\n")
    other_checkpoints = tmp_path / "other-machine-checkpoints.json"
    monkeypatch.setenv("AO_LEDGER_CHECKPOINTS", str(other_checkpoints))
    assert A.platform_runs(root) == []
    assert cli.cmd_evidence(project, _args()) == 0
    clone_show = capsys.readouterr().out
    assert "no row of it is ao's own; a person moves the file aside" in clone_show
    assert cli.cmd_gain(project, SimpleNamespace(all=False, since=None, days=14, slices=False,
                                                 json=False, sources=False, quota=False)) == 0
    gain_out = capsys.readouterr().out
    assert "does not record ao appending" in gain_out and "cannot read" not in gain_out
    clone_problems = dict(cli.doctor_problems(project))
    assert "evidence-unattested" in clone_problems and "evidence-ledger" not in clone_problems

    os.remove(ledger)
    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="2")) == 0
    capsys.readouterr()
    assert [row["run"] for row in A.platform_runs(root)] == ["2"]
    assert [key for key, _ in cli.doctor_problems(project) if key.startswith("evidence-")] == []


def test_a_run_whose_jobs_name_no_platform_never_reads_as_green(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: when no job in a run named any known platform, `_platform_relevant_jobs`
    returned every job as relevant, letting a green `lint` or `docs` run record as green for `windows`."""
    monkeypatch.setattr(A, "session_paths", lambda cfg: (None, None))
    root = project["root"]
    head = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.add_run("107", commit=head, platform="windows", status="completed", conclusion="success",
               suite="docs", jobs=[
                   {"name": "lint", "status": "completed", "conclusion": "success"},
                   {"name": "build-docs", "status": "completed", "conclusion": "success"},
               ])

    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="107")) == 0
    out = capsys.readouterr().out
    assert "(red)" in out and "0/0 job(s) passed" in out
    summary = A.platform_run_summaries(root, project)[0]
    assert summary["jobs_total"] == 0 and summary["jobs_passed"] == 0
    assert summary["green"] is False and summary["vouches"] is False
    problems = dict(cli.doctor_problems(project))
    assert "ran no windows job" in problems["evidence-red:windows"]
    assert cli.cmd_gain(project, SimpleNamespace(all=False, since=None, days=14, slices=False,
                                                 json=False, sources=False, quota=False)) == 0
    gain_out = capsys.readouterr().out
    assert "windows red" in gain_out and "windows green" not in gain_out


def test_jobs_carry_only_gh_job_fields_and_doctor_reports_failed_jobs(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: `gh run view --json jobs` returns job `name`, `status`, and `conclusion`,
    never per-job `passed`/`failed`/`skipped` test counts."""
    root = project["root"]
    head = _head(root)
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    payload = gh.add_run("108", commit=head, platform="windows", status="completed", conclusion="failure")
    assert set(payload["jobs"][0]) == {"name", "status", "conclusion"}

    row = A.record_platform_run(root, platform="windows", run="108", at=100)
    reading = A.platform_run_reading(root, project, row)
    for item in (row, reading):
        assert all(key not in item for key in ("passed", "failed", "skipped"))
    assert reading["jobs_total"] == 1 and reading["jobs_passed"] == 0 and reading["jobs_failed"] == 1
    assert cli.cmd_evidence(project, _args()) == 0
    assert "0/1 job(s) passed (failure)" in capsys.readouterr().out
    problems = dict(cli.doctor_problems(project))
    assert "failed 1 job(s) (failure)" in problems["evidence-red:windows"]


def test_record_without_gh_or_network_records_nothing_and_says_why(project, tmp_path, monkeypatch, capsys):
    """LANE-EVIDENCE, from the review: with no `gh`, or no network, ao records nothing and says why."""
    root = project["root"]
    monkeypatch.setattr(A, "runnable_binary", lambda name: None)

    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="201")) == 1
    out = capsys.readouterr().out
    assert "nothing recorded" in out and "gh is not on this machine" in out
    assert not os.path.exists(os.path.join(root, A.PLATFORM_RUNS_FILE))

    monkeypatch.undo()
    gh = FakeGh(tmp_path, monkeypatch, root=root)
    gh.fail(network_error="error connecting to api.github.com")
    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="201")) == 1
    out = capsys.readouterr().out
    assert "nothing recorded" in out and "api.github.com" in out
    assert not os.path.exists(os.path.join(root, A.PLATFORM_RUNS_FILE))

    gh.fail(authenticated=False)
    assert cli.cmd_evidence(project, _args(action="record", platform="windows", run="201")) == 1
    out = capsys.readouterr().out
    assert "nothing recorded" in out and "gh auth login" in out
    assert not os.path.exists(os.path.join(root, A.PLATFORM_RUNS_FILE))
