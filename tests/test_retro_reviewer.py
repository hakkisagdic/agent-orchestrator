"""A landed range can be reviewed again by another actor, as the slice that landed it (RETRO-REVIEWER).

139 catch-up reviews of 2026-09-30 and 10-01 record no author family, and 83 of them were made by the alibaba
family while a Qwen model also implemented: the ledgers do not show those reviewers' independence. A range can be
reviewed again by a family that wrote none of it, but `ao review --commits` reviewed with the reviewer role alone,
judged a range against the running slice's boundary and recorded the review as the running slice's. `--reviewer`
names an actor of the project's table, held to every rule `ao catchup --reviewer` holds one to, and `--slice` records
the review as the slice that landed the range, judged against its commit messages as a catch-up review is.
"""
import json
import os
import subprocess

from ao import cli, lib as A
from tests.test_catchup_ready import _land
from tests.test_catchup_reviewer import _roles, _table


def _review(cfg, *argv):
    return cli.cmd_review(cfg, cli.build_parser().parse_args(["review", *argv]))


def _rows(root):
    with open(os.path.join(root, ".ao", "ledger", "reviews.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_a_landed_range_is_reviewed_by_the_actor_named_and_recorded_as_its_slice(project, capsys):
    root = project["root"]
    cfg = _table(project)
    start = _land(root, "a.py", "value = 0\n", "base")
    end = _land(root, "a.py", "value = 1\n", "B1 sets the value to one")

    assert _review(cfg, "--commits", f"{start}..{end}", "--reviewer", "second-reviewer", "--slice", "B1") == 0

    row = _rows(root)[-1]
    assert (row["kind"], row["slice"], row["commits"], row["verdict"]) == ("commit-range", "B1", f"{start}..{end}",
                                                                          "APPROVED")
    with open(os.path.join(root, project["reviews"], row["artefact"]), encoding="utf-8") as fh:
        evidence = A.review_evidence(fh.read())
    assert evidence["reviewer"]["family"] == "other-family"                   # second-reviewer's, not the role's
    assert evidence["boundary"] == "the range B1 landed: what its commit messages say it does"
    assert (evidence["claims"]["commits"], evidence["claims"]["inlined"]) == (1, 1)   # its message was in the prompt
    assert "this run reviews with second-reviewer in place of first-reviewer" in capsys.readouterr().out
    assert _roles(root) == {"implementer": "builder", "reviewer": "first-reviewer"}


def test_an_actor_that_may_not_review_or_a_staged_candidate_is_refused_before_a_review_starts(project, capsys):
    root = project["root"]
    cfg = _table(project)
    start = _land(root, "a.py", "value = 0\n", "base")
    end = _land(root, "a.py", "value = 1\n", "B1")
    before = len(_rows(root)) if os.path.exists(os.path.join(root, ".ao", "ledger", "reviews.jsonl")) else 0
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout

    said = {}
    for actor in ("no-such-actor", "hand-written", "builder"):
        capsys.readouterr()
        assert _review(cfg, "--commits", f"{start}..{end}", "--reviewer", actor) == 2
        said[actor] = capsys.readouterr().out
    for argv in (["--reviewer", "second-reviewer"], ["--slice", "B1"]):
        assert _review(cfg, *argv) == 2
        assert "--reviewer and --slice go with --commits" in capsys.readouterr().out

    assert "the project's table has no such actor" in said["no-such-actor"]
    assert "only an actor `ao role set reviewer` composed may review" in said["hand-written"]
    assert "builder is the implementer, and no actor reviews its own work" in said["builder"]
    after = len(_rows(root)) if os.path.exists(os.path.join(root, ".ao", "ledger", "reviews.jsonl")) else 0
    assert after == before
    assert subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout == status
