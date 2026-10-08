"""The architect is named for its role in mail, never for a model (ARCHITECT-NAME, #72).

A project that named no architect was given a model's name, "fable", in every message's file name, and `ao init` wrote
that name into each project it set up. The default is now the role's own, `architect`. Mail a project wrote to the old
default stays the architect's there: the ao repository held 104 such messages, and acknowledging them to move them would
have deleted them where the mail store deletes what is acknowledged.
"""
import json

from ao import cli, lib as A, mcp, settings
from tests.test_profiles import _git_init, _init_args

LEGACY_BLOCKED = "20260916-1200-kiro-to-fable-BLOCKED-queue.md"


def _unnamed(project):
    """The project as one that never named its architect."""
    return dict(project, architect={key: value for key, value in project["architect"].items() if key != "name"})


def test_an_architect_no_one_named_is_named_for_its_role(project):
    cfg = _unnamed(project)

    assert settings.get(cfg, "architect.name") == "architect"
    assert A.mail_names(cfg) == ("kiro", "architect")


def test_mail_written_to_the_old_default_is_still_the_architect_s(project):
    cfg = _unnamed(project)

    assert A.to_architect(LEGACY_BLOCKED, cfg)
    assert A.to_architect("20261008-1200-kiro-to-architect-BLOCKED-queue.md", cfg)
    assert A.from_architect("20260916-1300-fable-to-kiro-DECISION-respecify.md", cfg)
    assert A.role_of("fable", cfg) == "architect" and A.role_of("architect", cfg) == "architect"


def test_a_named_architect_answers_to_its_name_alone(project):
    """A name the project gave is no alias of the old default, nor the old default of it."""
    lead = dict(project, architect=dict(project["architect"], name="lead"))

    assert A.to_architect("20260916-1200-kiro-to-lead-BLOCKED-queue.md", lead)
    assert not A.to_architect(LEGACY_BLOCKED, lead) and A.role_of("fable", lead) is None
    assert A.to_architect(LEGACY_BLOCKED, project)          # the fixture's project names its architect "fable"


def test_an_implementer_named_fable_keeps_its_mail(project):
    cfg = _unnamed(dict(project, implementer=dict(project["implementer"], name="fable")))

    assert A.role_of("fable", cfg) == "implementer"
    assert not A.to_architect("20260916-1300-architect-to-fable-DECISION-x.md", cfg)


def test_a_standing_report_to_the_old_default_takes_the_repeat(project):
    """A repeat folds into the report already standing, whichever name it was addressed by, and keeps its age."""
    first = mcp.call("ao_report", {"kind": "blocked", "summary": "queue empty"}, project, False)
    again = mcp.call("ao_report", {"kind": "blocked", "summary": "queue empty"}, _unnamed(project), False)

    assert "-kiro-to-fable-BLOCKED-" in first["written"]
    assert again["written"] == first["written"] and again["repeated"] == 2


def test_a_standing_anomaly_to_the_old_default_is_not_written_again(project):
    root = project["root"]

    assert A.write_report(root, project, "decision-requested", ["a.md"], key="implementer") == \
        "watchdog-to-fable-ANOMALY-decision-requested-implementer.md"
    assert A.write_report(root, _unnamed(project), "decision-requested", ["b.md"], key="implementer") is None


def test_init_names_the_architect_for_its_role(tmp_path, capsys, monkeypatch):
    root = tmp_path / "fresh"
    root.mkdir()
    _git_init(root)
    monkeypatch.setattr(cli, "_reviewer_probe", lambda cfg, timeout=None: {
        "configured": True, "ok": True, "route": "fixture-reviewer", "binary": "/fixture/reviewer",
        "version": "1.0.0", "reason": "exact nonce echoed", "kind": "success"})

    assert cli.cmd_init({"root": str(root)}, _init_args()) == 0, capsys.readouterr().out

    assert json.loads((root / ".ao" / "config.json").read_text(encoding="utf-8"))["architect"]["name"] == "architect"
