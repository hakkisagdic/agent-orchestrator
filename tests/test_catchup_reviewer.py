"""A catch-up run may name its own reviewer, so runs over different slices go side by side (CATCHUP-REVIEWER).

`ao catchup --reviewer <actor>` reviews a run with another reviewer actor of the project's table in place of
the reviewer role. Only an actor a reviewer composition made may stand in, never the implementer's, and the
table is read, not changed.
"""
import json
import os

from ao import cli, lib as A, watchdog as W
from tests.test_catchup_ready import APPROVED, PERSON, _catchup, _configure, _land, _legacy_waiver
from tests.test_review_chain import _fake


def _table(project):
    """The project's table: an implementer, the reviewer role's actor, a second composed reviewer, a hand-written one."""
    actors = {
        "builder": {"adapter": "claude-code", "argv": ["claude", "-p", "{prompt}"], "family": "writer-family"},
        "first-reviewer": {"argv": _fake(*APPROVED), "composed": True, "family": "review-family"},
        "second-reviewer": {"argv": _fake(*APPROVED), "composed": True, "family": "other-family"},
        "hand-written": {"argv": _fake(*APPROVED), "family": "review-family"},
    }
    roles = {"implementer": "builder", "reviewer": "first-reviewer"}
    return dict(_configure(project["root"], dict(project, actors=actors, roles=roles)), root=project["root"])


def _roles(root):
    with open(os.path.join(root, ".ao", "config.json"), encoding="utf-8") as fh:
        return json.load(fh)["roles"]


def test_a_run_reviews_with_the_actor_it_names_and_leaves_the_table_as_it_was(project, monkeypatch, capsys):
    root = project["root"]
    cfg = _table(project)
    _land(root, "a.py", "value = 0\n", "base")
    _legacy_waiver(root, "B1")
    _land(root, "a.py", "value = 1\n", "b1")
    seen = []
    review = cli.cmd_review
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns: seen.append(cfg["reviewer"].get("actor")) or review(cfg, ns))
    monkeypatch.setattr(W, "run", lambda ns: 0)
    assert cfg["reviewer"]["actor"] == "first-reviewer"

    assert _catchup(cfg, slice="B1", reviewer="second-reviewer", **PERSON) == 0

    assert seen == ["second-reviewer"]
    assert A.open_waivers(root) == []
    assert "this run reviews with second-reviewer in place of first-reviewer; the table is unchanged" \
        in capsys.readouterr().out
    assert _roles(root) == {"implementer": "builder", "reviewer": "first-reviewer"}


def test_an_actor_that_may_not_review_is_refused_before_the_run_starts(project, monkeypatch, capsys):
    root = project["root"]
    cfg = _table(project)
    _land(root, "a.py", "value = 0\n", "base")
    _legacy_waiver(root, "B1")
    _land(root, "a.py", "value = 1\n", "b1")
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns: (_ for _ in ()).throw(AssertionError("no review starts")))

    refused = {}
    for actor in ("no-such-actor", "hand-written", "builder"):
        capsys.readouterr()
        assert _catchup(cfg, slice="B1", reviewer=actor, **PERSON) == 2
        refused[actor] = capsys.readouterr().out

    assert "the project's table has no such actor; its reviewer actors: first-reviewer, second-reviewer" \
        in refused["no-such-actor"]
    assert "only an actor `ao role set reviewer` composed may review" in refused["hand-written"]
    assert "builder is the implementer, and no actor reviews its own work" in refused["builder"]
    assert [w["slice"] for w in A.open_waivers(root)] == ["B1"]


def test_a_reviewer_of_the_authors_family_is_refused_whichever_way_it_was_named(project, monkeypatch, capsys):
    root = project["root"]
    cfg = _table(project)
    _land(root, "a.py", "value = 0\n", "base")
    _legacy_waiver(root, "B1")
    _land(root, "a.py", "value = 1\n", "b1")
    monkeypatch.setattr(W, "run", lambda ns: 0)

    # The person says the range was written by the second reviewer's family: that actor may not review it.
    assert _catchup(cfg, slice="B1", reviewer="second-reviewer", author_family="other-family", by="A. Person") == 3
    assert "it declares the author's model family (other-family)" in capsys.readouterr().out
    assert [w["slice"] for w in A.open_waivers(root)] == ["B1"]
