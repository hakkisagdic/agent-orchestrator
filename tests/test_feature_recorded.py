"""Review is switched off only by a person, on the record (FEATURE-RECORDED).

`ao features off review` wrote `.ao/config.json` and nothing else, and `ao commit-ok` read the switch from there - a
file the implementer can write. An implementer that wrote `"features": {"review": false}`, or ran the command itself,
was granted on verification alone, and its unreviewed commit landed with a grant that bound its tree, so neither
`ao doctor` nor the watchdog flagged it. A switch that weakens a guarantee is now off only where a person's record in
the opt-in ledger says so too, as a recorded setting is; the config alone turns it off nowhere, and says so.
"""
import json
import os
from types import SimpleNamespace

from ao import cli, features as F, lib as A
from tests.test_attacks import _commit_ok, _stage, _verified


def _write_config(root, **features):
    document = A.project_config_document(root)["config"]
    document["features"] = dict(document.get("features") or {}, **features)
    A.write_project_config(root, json.dumps(document, indent=2))
    return dict(A.load_config(root), root=root)


def _features(cfg, action, key="review", by=None):
    return cli.cmd_features(cfg, SimpleNamespace(action=action, key=key, by=by))


def test_review_turned_off_in_the_config_alone_stays_required(project, capsys):
    root = project["root"]
    _stage(root, "src/a.py", "value = 'unreviewed'\n")
    cfg = _write_config(root, review=False)            # what an implementer that edits files can write
    _verified(root)

    code, out = _commit_ok(cfg, capsys)

    assert F.enabled(cfg, "review") is True and F.unrecorded(cfg) == ["review"]
    assert code != 0 and "review is off in .ao/config.json with no person's record" in out


def test_a_person_turns_review_off_on_the_record(project, capsys):
    root = project["root"]

    assert _features(project, "off", by="Alice") == 0

    cfg = dict(A.load_config(root), root=root)
    row = A.recorded_opt_in(root, "features.review")
    assert row["value"] is False and row["by"] == "Alice" and F.enabled(cfg, "review") is False
    _stage(root, "src/a.py", "value = 'verified alone'\n")
    _verified(root)
    assert _commit_ok(cfg, capsys)[0] == 0


def test_turning_review_off_takes_a_person(project, capsys):
    root = project["root"]

    assert _features(project, "off") == 2
    assert "--by is required" in capsys.readouterr().out
    assert _features(project, "off", by="architect") == 2
    assert "names an agent or a role" in capsys.readouterr().out
    assert _features(project, "off", key="inventory_review") == 2
    assert _features(project, "off", key="nudge", by="Alice") == 2      # a switch that weakens no guarantee
    assert (A.project_config_document(root)["config"].get("features") or {}).get("review", True) is True
    assert A.recorded_opt_in(root, "features.review") is None


def test_turning_review_on_is_recorded_so_no_earlier_off_stands(project):
    """A hand edit back to off after review was turned on again finds the newest record says on."""
    root = project["root"]
    assert _features(project, "off", by="Alice") == 0
    assert _features(project, "on") == 0

    cfg = _write_config(root, review=False)

    assert A.recorded_opt_in(root, "features.review")["value"] is True and F.enabled(cfg, "review") is True


def test_doctor_names_a_switch_off_with_no_record(project):
    cfg = _write_config(project["root"], review=False, inventory_review=False)

    problems = dict(cli.doctor_problems(cfg))

    assert "ao features off review --by <name>" in problems["unrecorded-switch:review"]
    assert "unrecorded-switch:inventory_review" in problems


def test_a_config_that_names_no_root_is_held_to_review():
    assert F.enabled({"features": {"review": False}}, "review") is True
    assert F.enabled({"features": {"nudge": False}}, "nudge") is False
