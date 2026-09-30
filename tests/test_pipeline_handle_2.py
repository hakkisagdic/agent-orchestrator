"""What the retrospective review of PIPELINE-HANDLE found that still held (PIPELINE-HANDLE-2).

anomalies() wrote a returned review's wait over its own `age` - the transcript's - so every check after
that loop read the wrong number whenever a review had come back. A review record with no id stopped
`ao status` and the anomalies. And a review that ended with no verdict was told to be handled as one
that came back.
"""
import json
import os
import time

from ao import cli, lib as A


def _review(root, name, **state):
    directory = os.path.join(root, ".ao", "reviews")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def test_a_returned_review_leaves_the_transcripts_age_to_the_checks_after_it(project, monkeypatch):
    root = project["root"]
    _review(root, "R-1", id="R-1", state="finished", verdict="APPROVED", slice="S1", finished_at=time.time() - 2700)
    monkeypatch.setattr(A, "agent_pids", lambda root, adapter, **kw: [10, 20])
    monkeypatch.setattr(A, "orphans", lambda root, adapter: [])
    monkeypatch.setattr(A, "process_trees", lambda pids, parent=None: [10, 20])

    kinds = [a["kind"] for a in A.anomalies(root, project, {}, 30, 600)]

    assert "several-turns-active" in kinds and "review-returned" in kinds


def test_a_review_record_that_names_no_id_is_named_by_its_file(project, capsys):
    _review(project["root"], "R-2", state="failed", slice="S1", finished_at=time.time())

    assert [review["id"] for review in A.returned_reviews(project["root"])] == ["R-2"]
    cli.cmd_status(project, cli.argparse.Namespace(messages=4, window=24.0))
    assert "REVIEW ENDED for S1: R-2 failed, no verdict — collect it, then submit again" in capsys.readouterr().out
