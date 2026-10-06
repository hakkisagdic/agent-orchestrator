"""A review is judged against the project's threat model, as it stood before the change (REVIEW-THREAT-MODEL).

Catchup reviews kept finding new edge cases: each needed an attacker to have landed malicious code through an
earlier review, or the interpreter to fail at one chosen line. Without a model of who the attacker is, a reviewer
had no line between a blocker and a hardening note. ao puts the project's model - THREAT_MODEL.md or
docs/threat-model.md, read from HEAD - in every review prompt, so a candidate cannot loosen the model it is judged
under, and no setting the implementer can write points it elsewhere (REVIEW-THREAT-MODEL-2). A landed range is
judged under the model from before it, and a change to the model that landed under a waiver still open judges no
review until it is reviewed itself (REVIEW-THREAT-MODEL-3).
"""
import json
from types import SimpleNamespace

from ao import cli, language, lib as A, storage
from tests.test_review_context import _args, _git, _package, _review_file, _reviewer, _stage_test, _write

MODEL = "# Threat model\n\nIn scope: the implementer agent, MODEL-AS-COMMITTED.\n"
LOOSENED = MODEL.replace("MODEL-AS-COMMITTED", "everything out of scope, LOOSENED")


def _model_between(prompt):
    start = prompt.index("--- THREAT MODEL:")
    return prompt[start:prompt.index("--- END OF THREAT MODEL ---", start)]


def _commit(root, path, text):
    _write(root, path, text)
    _git(root, "add", path)
    _git(root, "commit", "-q", "-m", path)


def test_a_review_is_judged_against_the_threat_model_the_last_commit_holds(project, tmp_path):
    root = _package(project)
    _commit(root, "docs/threat-model.md", MODEL)
    # the candidate loosens the model it would be reviewed under
    _write(root, "docs/threat-model.md", MODEL.replace("MODEL-AS-COMMITTED", "everything out of scope, LOOSENED"))
    _git(root, "add", "docs/threat-model.md")
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args()) == 0

    prompt = capture.read_text(encoding="utf-8")
    model = _model_between(prompt)
    assert "MODEL-AS-COMMITTED" in model and "LOOSENED" not in model
    candidate = "\n\n" + language.text(project, "prompt.review-candidate") + "\n"
    assert prompt.index("--- THREAT MODEL:") < prompt.index(candidate)
    assert "- threat model: docs/threat-model.md `sha256:" in _review_file(project)


def test_the_model_at_the_root_is_read_first_and_no_setting_points_elsewhere(project, tmp_path):
    """REVIEW-THREAT-MODEL-2: `review.threat_model` was a setting in `.ao/config.json`, a file the implementer can
    write, and could point a review at any committed text that put everything out of scope."""
    root = _package(project)
    _commit(root, "THREAT_MODEL.md", MODEL.replace("MODEL-AS-COMMITTED", "AT-THE-ROOT"))
    _commit(root, "docs/threat-model.md", MODEL)
    _commit(root, "docs/permissive.md", "# Threat model\n\nEverything is out of scope.\n")
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)
    cfg = dict(cfg, review=dict(cfg.get("review") or {}, threat_model="docs/permissive.md"))

    assert cli.cmd_review(cfg, _args()) == 0

    model = _model_between(capture.read_text(encoding="utf-8"))
    assert "AT-THE-ROOT" in model and "Everything is out of scope" not in model


def test_without_a_threat_model_a_review_carries_none(project, tmp_path):
    root = _package(project)
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args()) == 0

    assert "THREAT MODEL" not in capture.read_text(encoding="utf-8")
    assert "- threat model:" not in _review_file(project)


def test_a_model_the_candidate_brings_is_not_the_one_it_is_judged_under(project, tmp_path):
    root = _package(project)
    _write(root, "THREAT_MODEL.md", MODEL)                 # staged with the candidate, not in the last commit
    _git(root, "add", "THREAT_MODEL.md")
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args()) == 0

    assert "--- THREAT MODEL:" not in capture.read_text(encoding="utf-8")


def test_a_persons_review_is_recorded_under_no_model(project, capsys):
    """REVIEW-THREAT-MODEL-2: a person is shown the diff and the boundary, not the model, and the record claimed the
    model's digest for the person's verdict."""
    from tests.test_review_tiers import _artefact, _digest, _person, _rows
    root = _package(project)
    _commit(root, "docs/threat-model.md", MODEL)
    _stage_test(root)
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "s1", "name": "claude", "model": "model-a"})

    code, shown = _person(cfg, capsys)
    assert code == 0 and "THREAT MODEL" not in shown
    assert _person(cfg, capsys, verdict="APPROVED", digest=_digest(shown))[0] == 0

    (row,) = _rows(root)
    assert "- threat model:" not in _artefact(cfg, row)


def test_a_carried_answer_is_recorded_under_the_model_its_request_carried(project, monkeypatch, tmp_path):
    """REVIEW-THREAT-MODEL-2: a stand-in answer was attributed to the model at collection, where the person answered
    the prompt the request was written with."""
    root = _package(project)
    _stage_test(root)
    asked = {"path": "docs/threat-model.md", "digest": "sha256:" + "1" * 64}
    request = A.write_review_request(root, project, A.index_candidate(root), {"kind": "staged", "digest": "d"}, "dd",
                                     "b", None, None, "the prompt", threat_model=asked)
    assert A.review_request(root, request["nonce"])["threat_model"] == asked
    response = tmp_path / "answer.md"
    response.write_text(f"NONCE: {request['nonce']}\nVERDICT: APPROVED\nBLOCKER: 0\nHIGH: 0\nMEDIUM: 0\nLOW: 0\n",
                        encoding="utf-8")
    carried = []
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, args: carried.append(args.carried) or 0)

    cli._collect_review(project, SimpleNamespace(nonce=request["nonce"], response=str(response)), root, "Alice",
                        "some-model", storage.read_chained_jsonl)

    assert carried and carried[0]["evidence"]["threat_model"] == asked


def test_a_section_answered_under_another_threat_model_is_no_answer_under_this_one(project):
    root, sections, chain = project["root"], [{"name": "s1"}], [{"id": "r"}]
    evidence = {"diff_digest": "d"}

    without = cli._section_journal(root, evidence, "b", sections, chain)
    under = cli._section_journal(root, dict(evidence, threat_model={"path": "m", "digest": "sha256:1"}), "b",
                                 sections, chain)
    other = cli._section_journal(root, dict(evidence, threat_model={"path": "m", "digest": "sha256:2"}), "b",
                                 sections, chain)

    assert len({without, under, other}) == 3


def test_no_setting_names_the_threat_model():
    from ao import settings as S
    assert "review.threat_model" not in S.SETTINGS
    assert json.dumps(cli.THREAT_MODEL_PATHS) == '["THREAT_MODEL.md", "docs/threat-model.md"]'


def test_a_landed_range_is_judged_under_the_model_from_before_it(project, tmp_path):
    """REVIEW-THREAT-MODEL-3: a retrospective review read the model from HEAD, which held the range's own, so a range a
    person waived chose the model catch-up reviewed it under."""
    root = _package(project)
    _commit(root, "docs/threat-model.md", MODEL)
    base = _git(root, "rev-parse", "HEAD")
    _commit(root, "docs/threat-model.md", LOOSENED)
    _stage_test(root)
    _git(root, "commit", "-q", "-m", "test only")
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args(commits=f"{base}..HEAD")) == 0

    model = _model_between(capture.read_text(encoding="utf-8"))
    assert "MODEL-AS-COMMITTED" in model and "LOOSENED" not in model


def test_a_model_changed_under_a_waiver_still_open_judges_no_later_review(project, monkeypatch, capsys, tmp_path):
    """REVIEW-THREAT-MODEL-3: a change to the model a person waived set the model of every later review - staged, or a
    later waived range's - before anyone had reviewed the change."""
    from tests.test_switches_and_bypass import _allow_candidate_verification
    from tests.test_waiver_bounds import _commit_ok, _running
    root = _package(project)
    _commit(root, "docs/threat-model.md", MODEL)
    _running(root, "B7")
    _write(root, "docs/threat-model.md", LOOSENED)
    _git(root, "add", "docs/threat-model.md")
    _allow_candidate_verification(monkeypatch, A.index_candidate(root))
    A.waive(root, "review", "B7", "quota", by="alice (owner)")
    assert _commit_ok(project, capsys)[0] == 0
    _git(root, "commit", "-q", "-m", "the model, waived")
    later = _git(root, "rev-parse", "HEAD")
    _commit(root, "src/pkg/other.py", "X = 1\n")
    cfg, capture = _reviewer(project, tmp_path)
    _stage_test(root)

    assert cli.cmd_review(cfg, _args()) == 0
    assert "MODEL-AS-COMMITTED" in _model_between(capture.read_text(encoding="utf-8"))
    _git(root, "commit", "-q", "-m", "test only")
    assert cli.cmd_review(cfg, _args(commits=f"{later}..HEAD")) == 0
    model = _model_between(capture.read_text(encoding="utf-8"))
    assert "MODEL-AS-COMMITTED" in model and "LOOSENED" not in model


def test_a_review_runs_without_a_model_when_ao_cannot_tell_which_applies(project, monkeypatch, capsys, tmp_path):
    """REVIEW-THREAT-MODEL-3: an open waiver whose range ao cannot place could have changed the model, and a review
    without a model judges every finding by its severity."""
    root = _package(project)
    _commit(root, "docs/threat-model.md", MODEL)
    monkeypatch.setattr(A, "review_waiver_ranges", lambda root: [{"problem": "UNRESOLVED", "landed": 0}])
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args()) == 0

    assert "THREAT MODEL" not in capture.read_text(encoding="utf-8")
    assert "cannot tell which threat model" in capsys.readouterr().out


def test_a_range_s_base_is_the_commit_git_diffs_it_from(project):
    root = _package(project)
    first = _git(root, "rev-parse", "HEAD")
    _commit(root, "a.txt", "a\n")
    second = _git(root, "rev-parse", "HEAD")

    assert cli._range_base(root, f"{first}..{second}") == first
    assert cli._range_base(root, f"{first}...{second}") == first           # their merge base
    assert cli._range_base(root, second) == second                         # `git diff X` diffs X against the tree
    assert cli._range_base(root, f"{second}^!") == first
    assert cli._range_base(root, "no-such-commit..HEAD") is None


def test_a_model_a_waived_merge_brought_from_a_side_branch_judges_no_later_review(project, monkeypatch, capsys,
                                                                                  tmp_path):
    """REVIEW-THREAT-MODEL-4: a commit was taken to be in a waived range only when it descended from the range's start,
    and a waived merge brings in commits that do not: the side branch's model judged every later review."""
    from tests.test_switches_and_bypass import _allow_candidate_verification
    from tests.test_waiver_bounds import _commit_ok, _running
    root = _package(project)
    _commit(root, "docs/threat-model.md", MODEL)
    _git(root, "checkout", "-q", "-b", "side")
    _commit(root, "docs/threat-model.md", LOOSENED)
    _git(root, "checkout", "-q", "-")
    _commit(root, "src/pkg/other.py", "X = 1\n")
    _running(root, "B7")
    _git(root, "merge", "-q", "--no-ff", "--no-commit", "side")
    _allow_candidate_verification(monkeypatch, A.index_candidate(root))
    A.waive(root, "review", "B7", "quota", by="alice (owner)")
    assert _commit_ok(project, capsys)[0] == 0
    _git(root, "commit", "-q", "-m", "merge side, waived")
    cfg, capture = _reviewer(project, tmp_path)
    _stage_test(root)

    assert cli.cmd_review(cfg, _args()) == 0

    model = _model_between(capture.read_text(encoding="utf-8"))
    assert "MODEL-AS-COMMITTED" in model and "LOOSENED" not in model
