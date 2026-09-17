"""What rehearsing the catch-up planned for 2026-10-01 found (OCT1-FIXES).

A copy of the tool repository's ledgers was replayed with a stand-in reviewer. Repeated
`ao catchup --limit N` runs met the ranges whose reviews had failed first every time, and a
reviewer that did not answer was asked again for every range of the run; an INVALID review
was reported as an unavailable reviewer; a reviewer named while a slice ran was reported as
none configured, and a second assignment dropped it unchecked; and, with the jobs back
after two weeks, the scheduled check paged credits exhausted from a reading taken before
the plan reset.
"""
import json
import os
import sys
import time
from types import SimpleNamespace

from ao import cli, lib as A, watchdog as W
from tests.test_catchup_ready import APPROVED, PERSON, _catchup, _land, _legacy_waiver
from tests.test_review_chain import _fake

RUNNING = "- [S1] a slice in flight · since: 2026-09-30 09:00\n"


def _waived_ranges(root, *slices):
    """One legacy waiver per slice, each with one landed commit; the range each one's review is asked for."""
    _land(root, "a.py", "value = 0\n", "base")
    heads = []
    for n, slice_id in enumerate(slices, 1):
        heads.append(_legacy_waiver(root, slice_id)["head"])
        _land(root, "a.py", f"value = {n}\n", slice_id.lower())
    heads.append(A._git_text(root, "rev-parse", "HEAD"))
    return [f"{heads[n]}..{heads[n + 1]}" for n in range(len(slices))]


def _undecided_review(root, commits, verdict):
    """A retrospective review of exactly this range that decided nothing, as the review ledger records one."""
    A.record_review(root, f"{verdict.lower()}-{commits[:7]}.md", b"no review took place",
                    {"kind": "commit-range", "commits": commits}, verdict)


def _answering(unavailable=(), invalid=()):
    """A reviewer that exits for some waived slices, answers no verdict for others, and approves the rest."""
    script = ("import sys\nprompt = sys.argv[1]\n"
              f"if any(f'waived review for {{s}}:' in prompt for s in {list(unavailable)!r}): raise SystemExit(17)\n"
              f"if any(f'waived review for {{s}}:' in prompt for s in {list(invalid)!r}): print('it looks fine')\n"
              "else:\n" + "".join(f"    print({line!r})\n" for line in APPROVED))
    return [sys.executable, "-c", script, "{prompt}"]


def test_a_range_whose_last_review_decided_nothing_waits_behind_the_ranges_no_review_failed_on(
        project, monkeypatch, capsys):
    root = project["root"]
    ranges = _waived_ranges(root, "B1", "B2", "B3")
    _undecided_review(root, ranges[0], "UNAVAILABLE")
    _undecided_review(root, ranges[1], "INVALID")
    seen = []
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns: seen.append(ns.commits) or 3)
    monkeypatch.setattr(W, "run", lambda ns: 0)

    assert _catchup(project, plan=True, **PERSON) == 0
    out = capsys.readouterr().out
    assert out.index("(B3)") < out.index("(B1)") < out.index("(B2)")
    assert "its last review ended UNAVAILABLE, so it comes after the ranges no review has failed on" in out
    assert "its last review ended INVALID" in out

    # Every run of --limit 1 asked for B1 again and never reached B3.
    assert _catchup(project, limit=1, **PERSON) == 0
    assert seen == [ranges[2]]
    assert [w["slice"] for w in A.open_waivers(root)] == ["B1", "B2", "B3"]


def test_a_run_starts_no_review_after_one_finds_the_reviewer_unavailable(project, monkeypatch, capsys):
    monkeypatch.delenv("AO_ROLE", raising=False)
    root = project["root"]
    _waived_ranges(root, "B1", "B2", "B3")
    empty = A.waive(root, "review", "B9", "quota", by="A. Person", hours=0.0002)
    time.sleep(1)
    monkeypatch.setattr(W, "run", lambda ns: 0)
    spawned = []
    run_reviewer = cli._run_reviewer
    monkeypatch.setattr(cli, "_run_reviewer",
                        lambda *args, **kwargs: spawned.append(args[1]) or run_reviewer(*args, **kwargs))
    down = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _answering(unavailable=("B1",))})

    assert _catchup(down, **PERSON) == 0

    out = capsys.readouterr().out
    assert len(spawned) == 1 and "reviewer still unavailable; W-legacy-B1 stays open" in out
    assert "the reviewer was unavailable for W-legacy-B1: this run started no other review, and 2 waiver(s) wait" in out
    assert f"{empty['id']} (B9): expired unused; closed" in out          # what needs no reviewer is still done
    assert [w["slice"] for w in A.open_waivers(root)] == ["B1", "B2", "B3"]

    # The next run takes the ranges no review has failed on first, and an INVALID answer is told as one.
    answering = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _answering(invalid=("B2",))})
    assert _catchup(answering, **PERSON) == 0
    out = capsys.readouterr().out
    assert "the reviewer returned no valid verdict; W-legacy-B2 stays open" in out
    assert "reviewer still unavailable" not in out and "started no other review" not in out
    assert [w["slice"] for w in A.open_waivers(root)] == ["B2"]


def test_no_move_only_slice_is_reported_missing_when_the_waiver_ledger_cannot_be_read(project, monkeypatch, capsys):
    root = project["root"]
    A.waive(root, "review", "B7", "quota", by="A. Person")
    with open(A.waivers_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "closed", "id": "W-other", "at": 1, "outcome": "forged"}) + "\n")
    monkeypatch.setattr(W, "run", lambda ns: 0)

    assert _catchup(project, move_only="B7", by="A. Person") == 1

    out = capsys.readouterr().out
    assert "waivers cannot be read" in out and "no open review waiver names it" not in out


def _table(root):
    """A role table holding a reviewer nobody has assigned yet and two architects, and a slice running."""
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"project": "proj", "roles": {"architect": "lead"}, "actors": {
            "r2": {"id": "r2", "family": "review-family", "argv": _fake(*APPROVED)},
            "lead": {"name": "lead", "family": "writer-family", "argv": ["architect-cli", "-p", "{prompt}"]},
            "lead2": {"name": "lead2", "family": "writer-family", "argv": ["architect-cli", "-p", "{prompt}"]}}}, fh)
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    with open(board, "w", encoding="utf-8") as fh:
        fh.write(text.replace("## running\n", "## running\n" + RUNNING, 1))


def _leaves_running(root):
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    with open(board, "w", encoding="utf-8") as fh:
        fh.write(text.replace(RUNNING, ""))


def _stored(root):
    return json.load(open(os.path.join(root, ".ao", "config.json"), encoding="utf-8"))


def _role(root, capsys, action="show", role=None, actor=None):
    code = cli.cmd_role(A.load_config(root), SimpleNamespace(action=action, role=role, actor=actor))
    return code, capsys.readouterr().out


def test_a_reviewer_assigned_while_a_slice_runs_is_named_wherever_no_reviewer_holds_the_role(
        project, monkeypatch, capsys):
    root = project["root"]
    ranges = _waived_ranges(root, "B7")
    _table(root)
    monkeypatch.setattr(W, "run", lambda ns: 0)

    code, out = _role(root, capsys, "set", "reviewer", "r2")
    assert code == 0 and "takes effect once S1 leaves running" in out
    waiting = "r2 is assigned the reviewer role and holds it once S1 leaves running"

    assert _catchup(A.load_config(root), plan=True, **PERSON) == 0
    assert f"no reviewer is configured; {waiting}" in capsys.readouterr().out
    assert cli.cmd_review(A.load_config(root), SimpleNamespace(boundary="b", paths=None, commits=ranges[0])) == 1
    out = capsys.readouterr().out
    assert waiting in out and "Add to .ao/config.json" not in out
    code, out = _role(root, capsys)
    assert "{'reviewer': 'r2'} once S1 leaves running" in out

    _leaves_running(root)

    # The table showed nobody in the role and a wait that had already ended.
    code, out = _role(root, capsys)
    reviewer = next(line for line in out.splitlines() if line.strip().startswith("reviewer"))
    assert "r2" in reviewer and "leaves running" not in out
    assert A.load_config(root)["reviewer"]["actor"] == "r2"


def test_a_second_assignment_joins_the_one_that_waits_and_is_checked_against_it(project, capsys):
    root = project["root"]
    _table(root)
    assert _role(root, capsys, "set", "reviewer", "r2")[0] == 0

    # It replaced the waiting reviewer, unchecked against the implementer that actor would also have become.
    code, out = _role(root, capsys, "set", "implementer", "r2")
    assert code == 2 and "no actor reviews its own work" in out
    code, out = _role(root, capsys, "set", "architect", "lead2")
    assert code == 0 and "takes effect once S1 leaves running" in out
    assert _stored(root)["roles_next"] == {"after": "S1", "roles": {"architect": "lead2", "reviewer": "r2"}}

    _leaves_running(root)
    cfg = A.load_config(root)
    assert (cfg["reviewer"]["actor"], cfg["architect"]["actor"]) == ("r2", "lead2")

    # With nothing running, an assignment keeps what the waiting one brought into force.
    assert _role(root, capsys, "set", "architect", "lead")[0] == 0
    stored = _stored(root)
    assert stored["roles"] == {"architect": "lead", "reviewer": "r2"} and "roles_next" not in stored


def test_a_spent_reading_whose_reset_has_passed_is_no_credits_finding(project):
    root = project["root"]
    now = time.time()
    spent = {"used": 10240.0, "limit": 10000.0, "account": "acct-1"}

    assert cli._credits_problem(None, dict(spent, reset_at=now - 3600), now=now) is None
    assert cli._credits_problem(None, dict(spent, reset_at=now + 3600), now=now)[1].startswith(
        "credits exhausted at the last reading: 10240/10000")
    assert cli._credits_problem(None, dict(spent, reset_at="2026-10-01"), now=now) is not None    # no known end

    # Read two weeks before the jobs came back; the plan reset six hours before they did.
    A.record_credit_sample(root, 10240, 10000, reset_at=now - 6 * 3600, account="acct-1", at=now - 14 * 86400,
                           adapter="kiro")
    own = cli._implementer_credits(project)
    assert own["readable"] and own["samples"][-1]["used"] == 10240.0
    assert cli._credits_problem(own["rate"], own["samples"][-1]) is None
