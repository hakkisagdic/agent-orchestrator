"""What rehearsing the catch-up planned for 2026-10-01 left to polish (CATCHUP-POLISH).

The tool repository's ledgers were caught up in a scratch copy with a stand-in reviewer, and
six things were still wrong. A retrospective review's header named the implementer `None/`
where none was configured. `--limit N` stopped the run, and said waivers waited for the next
run that needed no review. A run whose every review decided nothing exited 0, as a run that
closed ten waivers did. The first cycle after two weeks off
named exhausted credits as standing six hours after the plan reset. A request nobody had been
shown and no architect could act on was mailed under two keys. And the commit a waiver's grant
landed was looked for among the newest 500 commits only.
"""
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from ao import cli, features as F, lib as A
from ao import watchdog as W
from tests.test_catchup_ready import APPROVED, PERSON, _catchup, _configure, _git, _land, _legacy_waiver, _reviews
from tests.test_noise_repeats import BLOCKED, DAY, HOUR, REQUEST, _heartbeat, _world
from tests.test_oct1_fixes import _answering, _waived_ranges
from tests.test_review_chain import _fake
from tests.test_switches_and_bypass import _allow_candidate_verification

UNREAD = "unread decision request"


# ---- 1. a retrospective review says who implements, or that nobody does -------------------------

def test_a_review_with_no_implementer_configured_says_so_and_that_the_range_is_judged_against_its_author(
        project, monkeypatch):
    monkeypatch.setattr(W, "run", lambda ns: 0)
    root = project["root"]
    cfg = _configure(root, {key: value for key, value in project.items() if key != "implementer"})
    assert "implementer" not in cfg
    _land(root, "a.py", "value = 1\n", "base")
    _legacy_waiver(root, "B7")
    _land(root, "a.py", "value = 2\n", "fix: b7")

    assert _catchup(dict(cfg, reviewer={"id": "r1", "family": "review-family", "argv": _fake(*APPROVED)}),
                    **PERSON) == 0

    (name,) = _reviews(root)
    header = open(os.path.join(root, "semantic-review", name), encoding="utf-8").read().split("\nVERDICT:")[0]
    assert "\n- implementer: none configured; the range is judged against its author\n" in header
    assert "None/" not in header and "- author's family: writer-family, named by A. Person" in header


def test_the_implementer_line_names_a_configured_implementer_as_it_did():
    assert cli._implementer_line("kiro/s1", None) == "- implementer: `kiro/s1`"
    assert cli._implementer_line("kiro/s1", {"family": "writer-family"}) == \
        "- implementer: `kiro/s1`; the range is judged against its author"
    assert cli._implementer_line(None, None) == "- implementer: none configured"


# ---- 2. --limit bounds the reviews, and counts only them -----------------------------------------

def test_limit_counts_only_what_a_later_run_would_review_and_the_run_still_closes_what_needs_none(
        project, monkeypatch, capsys):
    root = project["root"]
    ranges = _waived_ranges(root, "B1", "B2")
    empty = A.waive(root, "review", "B9", "quota", by="A. Person", hours=0.0002)      # nothing is granted under it
    time.sleep(1)
    seen = []
    monkeypatch.setattr(cli, "cmd_review", lambda cfg, ns: seen.append(ns.commits) or 0)
    monkeypatch.setattr(W, "run", lambda ns: 0)

    assert _catchup(project, plan=True, limit=1, **PERSON) == 0
    out = capsys.readouterr().out
    # It said two waivers waited for the next run: B2, and B9, which that run closes unreviewed.
    assert "--limit 1: 1 waiver(s) wait for a later run to review them" in out and "2 waiver(s)" not in out
    assert f"{empty['id']} (B9): expired unused; a run closes it" in out and "(B2)" not in out

    assert _catchup(project, limit=1, **PERSON) == 0
    out = capsys.readouterr().out
    assert seen == [ranges[0]]
    assert f"{empty['id']} (B9): expired unused; closed" in out
    assert "--limit 1: 1 waiver(s) wait for a later run to review them" in out
    assert [w["slice"] for w in A.open_waivers(root)] == ["B2"]


# ---- 3. a run whose reviews decided nothing exits 3; progress and nothing to do exit 0 -------------

def test_a_run_whose_reviews_all_decided_nothing_exits_3_whatever_else_it_closed(project, monkeypatch, capsys):
    monkeypatch.delenv("AO_ROLE", raising=False)
    root = project["root"]
    _waived_ranges(root, "B1", "B2")
    empty = A.waive(root, "review", "B9", "quota", by="A. Person", hours=0.0002)
    time.sleep(1)
    monkeypatch.setattr(W, "run", lambda ns: 0)

    down = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _answering(unavailable=("B1",))})
    assert _catchup(down, **PERSON) == 3
    out = capsys.readouterr().out
    assert f"{empty['id']} (B9): expired unused; closed" in out
    assert "handled 1 item(s); none of the 1 review(s) it started decided anything" in out

    invalid = dict(project, reviewer={"id": "r1", "family": "review-family",
                                      "argv": _answering(invalid=("B1", "B2"))})
    assert _catchup(invalid, **PERSON) == 3
    assert "none of the 2 review(s) it started decided anything" in capsys.readouterr().out

    refused = dict(project, reviewer={"id": "r1", "family": "writer-family", "argv": _fake(*APPROVED)})
    assert _catchup(refused, **PERSON) == 3
    assert [w["slice"] for w in A.open_waivers(root)] == ["B1", "B2"]

    approving = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _answering()})
    assert _catchup(approving, **PERSON) == 0
    assert A.open_waivers(root) == []
    assert _catchup(approving, plan=True, **PERSON) == 0                 # a plan keeps its contract


def test_a_run_with_nothing_to_do_exits_0_and_a_run_whose_reviews_all_decide_nothing_exits_3(
        project, monkeypatch, capsys):
    root = project["root"]
    monkeypatch.setattr(W, "run", lambda ns: 0)
    reviewer = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _fake(*APPROVED)})

    # An idle catch-up is no failure: no waiver at all, none this run can act on without a person, none in the slice.
    assert _catchup(reviewer, **PERSON) == 0
    _waived_ranges(root, "B1")
    assert _catchup(reviewer) == 0
    assert "--author-family" in capsys.readouterr().out
    assert _catchup(reviewer, slice="B8", **PERSON) == 0

    down = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _fake("unavailable", exit_code=17)})
    assert _catchup(down, **PERSON) == 3
    assert "handled 0 item(s); none of the 1 review(s) it started decided anything" in capsys.readouterr().out
    assert [w["slice"] for w in A.open_waivers(root)] == ["B1"]


# ---- 4. a resume names nothing whose own known end has passed -------------------------------------

def _back_after_two_weeks(project, monkeypatch, tmp_path, reset_in=None, ended_in=None, reading=None):
    """The jobs come back after two weeks off, to a credits alarm the silence carried and a snooze that ended in it.

    The implementer's last readings before the stop were spent, with a reset `reset_in` from now, or none;
    the carried episode ends `ended_in` from now, or records no end; this cycle's usage check reads
    `reading`, or cannot read the account.
    """
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    root, now = world.root, clock[0]
    began = now - 14 * DAY
    _heartbeat(root, began)
    for before in (6 * HOUR, 600):
        A.record_credit_sample(root, 10200, 10000, reset_at=None if reset_in is None else now + reset_in,
                               account="acct-1", at=began - before, adapter="kiro")
    W.save_state(root, {"last_credit_sample": began, "last_credit_attempt": began})
    episode = {"first": began - DAY, "last": began - 600, "level": "red", "ring": "red", "count": 48,
               "title": "proj: credits exhausted", "red_due": False, "red_sent": began - 5 * HOUR}
    if ended_in is not None:
        episode["quiet_until"] = now + ended_in
    A.save_alarms({"proj:credits-exhaust": episode})
    A.alarm_snooze("proj", "credits-exhaust", now - DAY, by="owner", why="the plan resets on the 1st")
    monkeypatch.setattr(A, "account_usage", lambda timeout=20, adapter_id=None: dict(reading or {"expired": True}))
    world.transcript_age(14 * DAY + HOUR)
    return world, sent, clock


def _resume_notice(root):
    [notice] = [row for row in A.notices(root, limit=400, include_suppressed=True) if row.get("key") == "resume"]
    return notice


@pytest.mark.parametrize("reset_in, ended_in", [(-6 * HOUR, None), (None, -6 * HOUR)],
                         ids=["the-last-readings-reset-passed", "the-episodes-own-end-passed"])
def test_a_resume_names_no_credits_as_standing_once_their_own_known_end_has_passed(
        project, monkeypatch, tmp_path, reset_in, ended_in):
    world, sent, _ = _back_after_two_weeks(project, monkeypatch, tmp_path, reset_in=reset_in, ended_in=ended_in)

    world.cycle(dry_run=False)

    notice = _resume_notice(world.root)
    assert "credits-exhaust" not in notice["named"] and "credits-exhaust" not in notice["msg"]
    assert not [entry for entry in sent if "credits" in entry[1] + entry[2]]


def test_credits_still_stand_at_a_resume_before_their_reset(project, monkeypatch, tmp_path):
    world, _, _ = _back_after_two_weeks(project, monkeypatch, tmp_path, reset_in=10 * DAY)

    world.cycle(dry_run=False)

    notice = _resume_notice(world.root)
    assert "credits-exhaust" in notice["named"] and "its snooze ended" in notice["msg"]
    assert "stood when the silence began" in notice["msg"]


def test_credits_a_reading_taken_as_the_watchdog_resumes_finds_spent_still_stand(project, monkeypatch, tmp_path):
    spent = {"used": 10240.0, "limit": 10000.0, "reset_at": time.time() - 6 * HOUR, "account": "acct-1"}
    world, _, _ = _back_after_two_weeks(project, monkeypatch, tmp_path, reset_in=-6 * HOUR, reading=spent)

    world.cycle(dry_run=False)

    notice = _resume_notice(world.root)
    assert "credits-exhaust" in notice["named"] and "its snooze ended" not in notice["msg"]
    assert "stood when the silence began" not in notice["msg"]


# ---- 5. a request nobody has been shown is one condition with one ladder -------------------------

def _cycles(world, clock, seconds):
    end = clock[0] + seconds
    while clock[0] < end:
        world.cycle(dry_run=False)
        clock[0] += 300


def _unseen_request(world, clock, written):
    world.mail(REQUEST, BLOCKED)                          # nobody is shown it
    os.utime(os.path.join(world.root, "agent-mail", REQUEST), (written, written))


def test_an_unseen_request_no_architect_will_act_on_is_mailed_under_one_key(project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)          # architect wakes are off
    _unseen_request(world, clock, clock[0])

    _cycles(world, clock, 11 * HOUR)

    assert not [entry for entry in sent if UNREAD in entry[1]]
    mails = [body.split("\n")[0] for kind, _, body, _ in sent if kind == "email"]
    assert len(mails) == 2 and all(mail.startswith(f"decision-requested: {REQUEST} in agent-mail/") for mail in mails)
    assert [alarm["key"] for alarm in A.active_alarms("proj")] == ["anomaly:decision-requested"]


def test_once_an_architect_can_act_the_requests_own_ladder_counts_from_the_last_alarm_that_told_it(
        project, monkeypatch, tmp_path):
    world, sent, clock = _world(project, monkeypatch, tmp_path)
    _unseen_request(world, clock, clock[0])
    _cycles(world, clock, 5 * HOUR)                       # "needs you" tells it, and it has turned red and mailed
    assert not [entry for entry in sent if UNREAD in entry[1]]

    F.set_switch(world.root, "architect_wake", True)      # an architect can be woken for it now
    _cycles(world, clock, 50 * 60)

    # Its own ladder does not ring red at once on the five hours a person has already been told of it.
    told = [row for row in A.notices(world.root, limit=400, include_suppressed=True)
            if row["key"] == f"unseen:{REQUEST}"]
    assert told and not any(row["sent"] for row in told)
    assert "since an alarm to a person last named it" in told[-1]["msg"]
    assert not [entry for entry in sent if UNREAD in entry[1]]

    _cycles(world, clock, 15 * 60)

    assert [entry[0] for entry in sent if UNREAD in entry[1]] == ["desktop", "telegram"]


def test_a_resume_names_an_unseen_request_no_architect_will_act_on_once(project, monkeypatch, tmp_path):
    world, _, clock = _world(project, monkeypatch, tmp_path)
    began = clock[0] - 3 * DAY
    _heartbeat(world.root, began)
    _unseen_request(world, clock, clock[0] - 4 * DAY)
    # Its own alarm was red when the silence began, as it was before one alarm told it.
    A.save_alarms({f"proj:unseen:{REQUEST}": {"first": began - 5 * HOUR, "last": began, "level": "red", "ring": "red",
                                              "count": 60, "title": "proj: unread decision request",
                                              "red_due": False, "red_sent": began - HOUR}})
    world.transcript_age(3 * DAY)

    world.cycle(dry_run=False)

    notice = _resume_notice(world.root)
    assert "anomaly:decision-requested" in notice["named"] and f"unseen:{REQUEST}" not in notice["named"]
    assert "nobody has been shown" not in notice["msg"] and "stood when the silence began" not in notice["msg"]


# ---- 6. a grant's commit is found however much has landed since ----------------------------------

def _commits(root, count, start, path):
    """`count` commits on top of HEAD, each changing `path`, committed a minute apart from `start`; the last one."""
    branch, head = _git(root, "symbolic-ref", "HEAD"), _git(root, "rev-parse", "HEAD")
    stream = []
    for n in range(count):
        message, data = f"step {n}".encode(), f"{n}\n".encode()
        stream += [b"commit %s\n" % branch.encode(), b"committer t <t@t> %d +0000\n" % (start + 60 * n),
                   b"data %d\n%s\n" % (len(message), message)]
        if n == 0:
            stream.append(b"from %s\n" % head.encode())
        stream.append(b"M 100644 inline %s\ndata %d\n%s\n" % (path.encode(), len(data), data))
    subprocess.run(["git", "fast-import", "--quiet"], cwd=root, input=b"".join(stream), check=True,
                   capture_output=True)
    _git(root, "read-tree", "--reset", "-u", "HEAD")
    return _git(root, "rev-parse", "HEAD")


def _granted_and_landed(project, monkeypatch, capsys):
    """A change granted under a review waiver for a running slice, and committed: (waiver, grant, commit)."""
    root = project["root"]
    board = os.path.join(root, ".ao", "board.md")
    text = open(board, encoding="utf-8").read()
    with open(board, "w", encoding="utf-8") as fh:
        fh.write(text.replace("## running\n", "## running\n- [B7] slice · since: 2026-09-15 10:00\n"))
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write("value = 2\n")
    _git(root, "add", "src/a.py")
    _allow_candidate_verification(monkeypatch, A.index_candidate(root))
    waiver = A.waive(root, "review", "B7", "the implementer is out of credits", by="A. Person")
    assert cli.cmd_commit_ok(project, SimpleNamespace(verify=False, profile=None)) == 0
    capsys.readouterr()
    _git(root, "commit", "-q", "-m", "b7")
    with open(board, "w", encoding="utf-8") as fh:
        fh.write(text)
    return waiver, A.latest_authority_decision(root), _git(root, "rev-parse", "HEAD")


def _logs(monkeypatch):
    """Each `git log` the waiver ranges run: (its arguments, the commits it listed)."""
    calls, real = [], A._git_output

    def recorded(root, *args, **kwargs):
        out = real(root, *args, **kwargs)
        if args and args[0] == "log":
            calls.append((args, out.decode("ascii").splitlines()))
        return out
    monkeypatch.setattr(A, "_git_output", recorded)
    return calls


def test_a_waiver_whose_commit_landed_500_commits_ago_is_found_from_its_grants_head(project, monkeypatch, capsys):
    root = project["root"]
    before = _commits(root, 600, int(time.time()) - 30 * DAY, "earlier.txt")      # a long history before the waiver
    waiver, grant, landed = _granted_and_landed(project, monkeypatch, capsys)
    parent = _git(root, "rev-parse", f"{landed}^")
    _git(root, "revert", "--no-edit", "HEAD")             # undone and landed again: the same tree, another commit
    _git(root, "revert", "--no-edit", "HEAD")
    _commits(root, 500, int(time.time()), "later.txt")
    walked = _logs(monkeypatch)

    (item,) = A.review_waiver_ranges(root)

    assert (item["start"], item["end"], item["landed"], item["problem"]) == (parent, landed, 1, None)
    assert item["grant"] == grant["token"] and grant["candidate"]["head"] == before
    [(args, listed)] = walked
    assert f"{before}..HEAD" in args and len(listed) == 503 and not [line for line in listed if before in line]


def test_a_grant_whose_head_git_does_not_have_is_found_from_the_waivers_date_and_nothing_else_is_searched(
        project, monkeypatch, capsys):
    root = project["root"]
    before = _commits(root, 600, int(time.time()) - 30 * DAY, "earlier.txt")
    waiver, grant, landed = _granted_and_landed(project, monkeypatch, capsys)
    _commits(root, 500, int(time.time()), "later.txt")
    rows, waivers = A.authority_rows, A.waiver_rows
    monkeypatch.setattr(A, "authority_rows", lambda target: [
        dict(row, candidate=dict(row["candidate"], head="0" * 40)) if row.get("waiver") == waiver["id"] else row
        for row in rows(target)])
    walked = _logs(monkeypatch)

    (item,) = A.review_waiver_ranges(root)

    assert (item["end"], item["landed"], item["problem"]) == (landed, 1, None)
    [(args, listed)] = walked
    assert f"--since=@{waiver['at'] - 86400}" in args
    assert len(listed) == 501 and not [line for line in listed if before in line]

    monkeypatch.setattr(A, "waiver_rows", lambda target: [{key: value for key, value in row.items() if key != "at"}
                                                         for row in waivers(target)])
    (item,) = A.review_waiver_ranges(root)
    assert item["problem"].startswith("UNRESOLVED: no grant under it records a head on HEAD's line")
