"""Catch-up is ready for the reviews the owner deferred (CATCHUP-READY).

A waived range is reviewed by a model family other than the one that wrote it,
whoever is configured to implement by then; a family ao did not record waits for a
person to name it, on the record; a run can be previewed and bounded; and the
decision request it leaves is English and addressed to a role.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, matrix as M, watchdog as W
from tests.test_capability_matrix import _strict_config
from tests.test_review_chain import _fake
from tests.test_switches_and_bypass import _allow_candidate_verification

APPROVED = ("VERDICT: APPROVED", "BLOCKER: 0", "HIGH: 0", "MEDIUM: 0", "LOW: 0")
NEEDS_CHANGES = ("VERDICT: NEEDS_CHANGES", "BLOCKER: 1", "HIGH: 0", "MEDIUM: 0", "LOW: 0", "## Findings",
                 "- [BLOCKER] src/a.py:1 - the value is the wrong one")
PERSON = dict(author_family="writer-family", by="A. Person")


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _land(root, name, text, message):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", name), "w", encoding="utf-8") as fh:
        fh.write(text)
    _git(root, "add", f"src/{name}")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _configure(root, cfg):
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)
    return A.load_config(root)


def _legacy_waiver(root, slice_id):
    """A review waiver as ledgers held it before waivers were bounded: its range runs to the next waiver's head."""
    row = {"event": "waived", "id": f"W-legacy-{slice_id}", "gate": "review", "slice": slice_id,
           "why": "the implementer is out of credits", "by": "A. Person", "at": int(time.time()),
           "head": _git(root, "rev-parse", "HEAD"), "tree": "t"}
    with open(A.waivers_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _catchup(cfg, **given):
    args = dict(boundary=None, plan=False, limit=None, slice=None, author_family=None, by=None)
    args.update(given)
    return cli.cmd_catchup(cfg, SimpleNamespace(**args))


def _reviews(root):
    return sorted(name for name in os.listdir(os.path.join(root, "semantic-review")) if name.endswith(".md"))


def _marking(marker, *lines):
    """A reviewer that leaves a file behind when it runs, then answers with these lines."""
    script = f"open({str(marker)!r}, 'w').write('ran'); " + "; ".join(f"print({line!r})" for line in lines)
    return [sys.executable, "-c", script, "{prompt}"]


def test_a_reviewer_of_the_architects_family_is_refused_for_the_range_it_landed_with_no_implementer(
        project, monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("AO_ROLE", raising=False)
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    architect = {"name": "lead", "family": "writer-family", "argv": ["architect-cli", "-p", "{prompt}"]}
    cfg = _configure(root, dict({key: value for key, value in project.items() if key != "implementer"},
                                architect=architect))
    assert "implementer" not in cfg
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    open(board, "w", encoding="utf-8").write(text.replace("## running\n", "## running\n- [B7] slice · since: 2026-09-15 10:00\n"))
    with open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write("value = 2\n")
    _git(root, "add", "src/a.py")
    _allow_candidate_verification(monkeypatch, A.index_candidate(root))
    waiver = A.waive(root, "review", "B7", "the implementer is out of credits", by="A. Person")

    monkeypatch.setenv("AO_ROLE", "architect")          # a turn ao started for the architect lands it
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False, profile=None)) == 0
    monkeypatch.delenv("AO_ROLE")
    grant = A.latest_authority_decision(root)
    assert grant["waiver"] == waiver["id"]
    assert grant["author"] == {"role": "architect", "actor": "lead", "adapter": None, "family": "writer-family"}
    _git(root, "commit", "-q", "-m", "b7")
    _land(root, "b.py", "later = 1\n", "later work, not under the waiver")
    monkeypatch.setattr(W, "run", lambda ns: 0)

    marker = tmp_path / "same-family-ran"
    same = {"id": "same", "family": "writer-family", "argv": _marking(marker, *APPROVED)}
    # Compared with the configured implementer alone, there is nobody to be independent of.
    assert cli._reviewer_ineligible(cfg, same) is None
    capsys.readouterr()
    assert _catchup(dict(cfg, reviewer=same)) == 0
    assert "it declares the author's model family (writer-family)" in capsys.readouterr().out
    assert not marker.exists() and _reviews(root) == []
    assert [w["id"] for w in A.open_waivers(root)] == [waiver["id"]]

    # A fallback of that family is not run either when the independent primary cannot answer.
    other_down = {"id": "other", "family": "review-family", "argv": _fake("unavailable", exit_code=17),
                  "fallbacks": [same]}
    assert _catchup(dict(cfg, reviewer=other_down)) == 0
    assert "fallback same not run: it declares the author's model family (writer-family)" in capsys.readouterr().out
    assert not marker.exists()
    assert [w["id"] for w in A.open_waivers(root)] == [waiver["id"]]

    other = {"id": "other", "family": "review-family", "argv": _fake(*APPROVED)}
    assert _catchup(dict(cfg, reviewer=other)) == 0
    assert A.open_waivers(root) == []
    approved = [name for name in _reviews(root)
                if "VERDICT: APPROVED" in open(os.path.join(root, "semantic-review", name), encoding="utf-8").read()]
    body = open(os.path.join(root, "semantic-review", approved[0]), encoding="utf-8").read()
    author = A.review_evidence(body)["author"]
    assert (author["role"], author["actor"], author["family"], author["grant"], author["stated"]) == \
        ("architect", "lead", "writer-family", grant["token"], None)


def test_an_unknown_author_family_is_refused_until_a_person_names_one_and_the_naming_is_recorded(
        project, monkeypatch, capsys):
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    waiver = _legacy_waiver(root, "B7")
    _land(root, "a.py", "value = 2\n", "b7")
    cfg = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _fake(*APPROVED)})
    monkeypatch.setattr(W, "run", lambda ns: 0)

    assert _catchup(cfg) == 0
    out = capsys.readouterr().out
    assert "the family of the model that wrote it is not established" in out and "--author-family" in out
    assert _reviews(root) == [] and [w["id"] for w in A.open_waivers(root)] == [waiver["id"]]

    assert _catchup(cfg, author_family="writer-family") == 2
    assert "--by is required" in capsys.readouterr().out
    assert _catchup(cfg, author_family="writer-family", by="architect") == 2
    assert "a person's statement" in capsys.readouterr().out
    assert _catchup(cfg, by="A. Person") == 2
    assert _catchup(cfg, author_family="review-family", by="A. Person") == 0
    assert "it declares the author's model family (review-family)" in capsys.readouterr().out
    assert _reviews(root) == [] and [w["id"] for w in A.open_waivers(root)] == [waiver["id"]]

    assert _catchup(cfg, author_family="Writer-Family", by="A. Person") == 0
    assert A.open_waivers(root) == []
    (name,) = _reviews(root)
    body = open(os.path.join(root, "semantic-review", name), encoding="utf-8").read()
    stated = A.review_evidence(body)["author"]["stated"]
    assert (stated["family"], stated["by"]) == ("writer-family", "A. Person")
    assert "user" in stated and isinstance(stated["interactive"], bool)
    assert "- author's family: writer-family, named by A. Person" in body


def _snapshot(*directories):
    found = {}
    for directory in directories:
        for base, _, files in os.walk(directory):
            for name in files:
                path = os.path.join(base, name)
                with open(path, "rb") as fh:
                    found[path] = hashlib.sha256(fh.read()).hexdigest()
    return found


def test_plan_lists_each_waiver_its_range_and_size_and_writes_nothing(project, monkeypatch, capsys):
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    first = _legacy_waiver(root, "B7")
    _land(root, "a.py", "value = 2\nmore = 3\n", "b7")                # one line changed, one added: 3
    second = _legacy_waiver(root, "B8")
    head = _land(root, "b.py", "other = 1\n", "b8")                    # 1
    A.deferred_append(root, "nudge", reason="implementer quota")
    cfg = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _fake(*APPROVED)})

    def written(*args, **kwargs):
        raise AssertionError("a plan changed something")

    for target, name in ((cli, "cmd_review"), (A, "close_waiver"), (A, "write_mail"), (A, "deferred_close"),
                         (W, "run")):
        monkeypatch.setattr(target, name, written)
    A.open_waivers(root)                          # every reader takes the ledger's lock file first
    before = _snapshot(root, A.HOME, os.path.dirname(os.environ["AO_LEDGER_CHECKPOINTS"]))

    assert _catchup(cfg, plan=True) == 0
    unnamed = capsys.readouterr().out
    assert _catchup(cfg, plan=True, **PERSON) == 0
    out = capsys.readouterr().out

    assert _snapshot(root, A.HOME, os.path.dirname(os.environ["AO_LEDGER_CHECKPOINTS"])) == before
    assert f"{first['id']} (B7): {first['head'][:12]}..{second['head'][:12]}, 1 commit(s), 3 changed line(s)" in out
    assert f"{second['id']} (B8): {second['head'][:12]}..{head[:12]}, 1 commit(s), 1 changed line(s)" in out
    assert "totals: 2 waiver(s); 2 review(s) of 2 commit(s) and 4 changed line(s); 0 refused" in out
    assert "1 deferred wake(s) and nudge(s)" in out
    assert "totals: 2 waiver(s); 0 review(s) of 0 commit(s) and 0 changed line(s); 2 refused" in unnamed
    assert [w["id"] for w in A.open_waivers(root)] == [first["id"], second["id"]]


def test_limit_starts_at_most_that_many_reviews_and_slice_takes_one_slices_waivers(project, monkeypatch, capsys):
    root = project["root"]
    _land(root, "a.py", "value = 0\n", "base")
    empty = _legacy_waiver(root, "B0")              # nothing lands before the next waiver: closed, no review
    waivers = []
    for n, slice_id in enumerate(("B1", "B2", "B3"), 1):
        waivers.append(_legacy_waiver(root, slice_id))
        _land(root, "a.py", f"value = {n}\n", slice_id.lower())
    heads = [w["head"] for w in waivers] + [_git(root, "rev-parse", "HEAD")]
    ranges = [f"{heads[n]}..{heads[n + 1]}" for n in range(3)]
    seen = []
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns: seen.append(ns.commits) or 0)
    monkeypatch.setattr(W, "run", lambda ns: 0)

    assert _catchup(project, limit=0, **PERSON) == 2
    assert _catchup(project, plan=True, limit=2, **PERSON) == 0
    out = capsys.readouterr().out
    assert all(w["id"] in out for w in (empty, *waivers[:2])) and waivers[2]["id"] not in out
    assert "--limit 2: 1 waiver(s) wait for the next run" in out and seen == []

    assert _catchup(project, limit=2, **PERSON) == 0
    assert seen == ranges[:2]
    assert [w["id"] for w in A.open_waivers(root)] == [waivers[2]["id"]]

    seen.clear()
    assert _catchup(project, slice="B9", **PERSON) == 0
    assert seen == []
    assert _catchup(project, slice="B3", **PERSON) == 0
    assert seen == [ranges[2]]
    assert A.open_waivers(root) == []


def test_the_needs_changes_request_is_english_and_addressed_to_the_architect_role(project, monkeypatch, capsys):
    monkeypatch.delenv("AO_ROLE", raising=False)
    root = project["root"]
    cfg = _configure(root, dict(project, architect=dict(project["architect"], name="lead")))
    _land(root, "a.py", "value = 1\n", "base")
    waiver = _legacy_waiver(root, "B7/fix")
    _land(root, "a.py", "value = 2\n", "b7")
    monkeypatch.setattr(W, "run", lambda ns: 0)

    reviewed = dict(cfg, reviewer={"id": "r1", "family": "review-family", "argv": _fake(*NEEDS_CHANGES)})
    assert _catchup(reviewed, **PERSON) == 0

    assert A.open_waivers(root) == []
    (review,) = _reviews(root)
    (name,) = os.listdir(os.path.join(root, "agent-mail"))
    assert "-human-to-lead-REVIEW-b7-fix-needs-changes" in name and A.to_architect(name, cfg)
    meta = A.mail_meta(os.path.join(root, "agent-mail", name))
    assert (meta["from_role"], meta["to_role"], meta["class"], meta["slice"]) == \
        ("human", "architect", "needs-decision", "B7/fix")
    body = open(os.path.join(root, "agent-mail", name), encoding="utf-8").read()
    assert body.isascii() and "## Decision required" in body
    assert f"semantic-review/{review}" in body and waiver["id"] in body
    assert A.mail_class(name, meta, body) == "needs-decision"


def test_independence_is_judged_against_the_author_not_the_implementer_configured_now(project):
    route = {"id": "r", "family": "review-family", "argv": ["kiro-cli", "chat", "--no-interactive", "{prompt}"]}

    assert "the implementer's own engine" in cli._reviewer_ineligible(project, route)
    assert cli._reviewer_ineligible(project, route, author={"family": "writer-family"}) is None
    assert "the author's model family (review-family)" in \
        cli._reviewer_ineligible(project, route, author={"family": "review-family"})
    assert "declares no model family" in \
        cli._reviewer_ineligible(project, dict(route, family=None), author={"family": "writer-family"})

    cfg = _strict_config(project, primary_family="writer-family")          # the implementer binding's family
    assert [route["eligible"] for route in M.resolve(cfg)["reviewers"]] == [False, True]
    resolved = M.resolve(cfg, author_families=["fallback-family"])
    assert [(route["eligible"], route["ineligible_reason"]) for route in resolved["reviewers"]] == \
        [(True, ""), (False, "same model family as the author")]
    with pytest.raises(M.MatrixError, match="independent of the author's model family"):
        M.resolve(cfg, author_families=["writer-family", "fallback-family"])
