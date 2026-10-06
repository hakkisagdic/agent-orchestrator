"""A review is judged against the project's threat model, as its last commit holds it (REVIEW-THREAT-MODEL).

Catchup reviews kept finding new edge cases: each needed an attacker to have landed malicious code through an
earlier review, or the interpreter to fail at one chosen line. Without a model of who the attacker is, a reviewer
had no line between a blocker and a hardening note. `review.threat_model` names the project's model; ao puts it in
every review prompt, read from HEAD, so a candidate cannot loosen the model it is judged under.
"""
from ao import cli, language, settings as S
from tests.test_review_context import _args, _git, _package, _review_file, _reviewer, _stage_test, _write

MODEL = "# Threat model\n\nIn scope: the implementer agent, MODEL-AS-COMMITTED.\n"


def _model_between(prompt):
    start = prompt.index("--- THREAT MODEL:")
    return prompt[start:prompt.index("--- END OF THREAT MODEL ---", start)]


def _with_model(cfg, path="docs/threat-model.md"):
    return dict(cfg, review=dict(cfg.get("review") or {}, threat_model=path))


def test_a_review_is_judged_against_the_threat_model_the_last_commit_holds(project, tmp_path):
    root = _package(project)
    _write(root, "docs/threat-model.md", MODEL)
    _git(root, "add", "docs/threat-model.md")
    _git(root, "commit", "-q", "-m", "threat model")
    # the candidate loosens the model it would be reviewed under
    _write(root, "docs/threat-model.md", MODEL.replace("MODEL-AS-COMMITTED", "everything out of scope, LOOSENED"))
    _git(root, "add", "docs/threat-model.md")
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(_with_model(cfg), _args()) == 0

    prompt = capture.read_text(encoding="utf-8")
    model = _model_between(prompt)
    assert "MODEL-AS-COMMITTED" in model and "LOOSENED" not in model
    candidate = "\n\n" + language.text(project, "prompt.review-candidate") + "\n"
    assert prompt.index("--- THREAT MODEL:") < prompt.index(candidate)
    assert "- threat model: docs/threat-model.md `sha256:" in _review_file(project)


def test_without_a_threat_model_a_review_carries_none(project, tmp_path):
    root = _package(project)
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(cfg, _args()) == 0

    assert "THREAT MODEL" not in capture.read_text(encoding="utf-8")
    assert "- threat model:" not in _review_file(project)


def test_a_threat_model_the_last_commit_does_not_hold_is_said_and_left_out(project, tmp_path, capsys):
    root = _package(project)
    _stage_test(root)
    cfg, capture = _reviewer(project, tmp_path)

    assert cli.cmd_review(_with_model(cfg, "docs/no-such-model.md"), _args()) == 0

    assert "THREAT MODEL" not in capture.read_text(encoding="utf-8")
    assert "which the last commit does not hold" in capsys.readouterr().out


def test_a_section_answered_under_another_threat_model_is_no_answer_under_this_one(project):
    root, sections, chain = project["root"], [{"name": "s1"}], [{"id": "r"}]
    evidence = {"diff_digest": "d"}

    without = cli._section_journal(root, evidence, "b", sections, chain)
    under = cli._section_journal(root, dict(evidence, threat_model={"path": "m", "digest": "sha256:1"}), "b",
                                 sections, chain)
    other = cli._section_journal(root, dict(evidence, threat_model={"path": "m", "digest": "sha256:2"}), "b",
                                 sections, chain)

    assert len({without, under, other}) == 3


def test_the_threat_model_is_a_path_inside_the_repository():
    assert S.usable("review.threat_model", "docs/threat-model.md")
    for value in ("/etc/model.md", "../model.md", "docs/../model.md", "docs\\model.md", "-model.md", "C:model.md",
                  "docs//model.md"):
        assert not S.usable("review.threat_model", value), value
