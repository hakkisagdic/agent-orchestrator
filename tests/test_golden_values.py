"""Golden values for the functions that turn a clock, a quota or a count into a decision (#41).

The costliest failures of 2026-09-07 were pure functions with a wrong answer: a reset at
21:20 read at 21:37 became 21:20 the next day, a credit projection ran across two
accounts, and a round counter counted every review at a HEAD. Each function below has
its table here - input and expected value, the awkward cases included - and the number
of branches it had when the table was written. A function that gains a branch fails
`test_no_golden_function_gains_a_branch_without_a_row` until the table gains rows for it
and the count is brought up to date.
"""
import ast
import inspect
import os
import textwrap
import time

import pytest

from ao import cli, lib as A, watchdog as W
from tests.test_round_ledger import _review, _running

HOUR = 3600
DAY = 86400


def _at(text):
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))


# ── watchdog.parse_reset: a provider message to the time the window reopens ──
PARSE_RESET = [
    # (message, written at, window, expected)
    ("usage limit reached, resets in 4h 43m", "2026-09-07 17:00", None, "+4:43"),
    ("rate limited · resets in 90s", "2026-09-07 17:00", None, "+0:01:30"),
    ("resets in 2h 5m 10s", "2026-09-07 17:00", None, "+2:05:10"),
    ("You've hit your session limit · resets 9:20pm", "2026-09-07 17:48", 5 * HOUR, "2026-09-07 21:20"),
    # the defect: read after the reset, it is the reset that passed, not tomorrow's
    ("You've hit your session limit · resets 9:20pm", "2026-09-07 21:37", 5 * HOUR, "2026-09-07 21:20"),
    ("resets 12:10am", "2026-09-07 23:30", 5 * HOUR, "2026-09-08 00:10"),            # spans midnight
    ("resets 12am", "2026-09-07 10:00", None, "2026-09-08 00:00"),
    ("resets 12pm", "2026-09-07 09:00", None, "2026-09-07 12:00"),
    ("resets 4am (Europe/Istanbul)", "2026-09-07 03:00", None, "2026-09-07 04:00"),
    ("resets at 16", "2026-09-07 15:00", None, "2026-09-07 16:00"),
    ("You've hit your weekly limit · resets Sep 14 at 4am (Europe/Istanbul)", "2026-09-10 12:00", 5 * HOUR,
     "2026-09-14 04:00"),
    ("You've hit your weekly limit · resets Jan 2 at 9:30pm", "2026-12-30 08:00", None, "2027-01-02 21:30"),
    ("weekly limit, resets September 3", "2026-09-01 08:00", None, "2026-09-03 00:00"),
    ("usage limit reached", "2026-09-07 17:00", None, None),
]


@pytest.mark.parametrize("message,written,window,expected", PARSE_RESET)
def test_parse_reset_golden_values(message, written, window, expected):
    now = _at(written)
    got = W.parse_reset(message, now=now, window=window)
    if expected is None:
        assert got is None
    elif expected.startswith("+"):
        parts = [int(part) for part in expected[1:].split(":")] + [0]
        assert got == now + parts[0] * HOUR + parts[1] * 60 + parts[2]
    else:
        assert got == _at(expected)


# ── watchdog.quota_block_until: a quota error read from the wake log to the end of its block ──
NOW = 1_800_000_000
QUOTA_BLOCK = [
    ("no error", None, None),
    ("an error that is not quota", {"kind": "binary", "at": NOW}, None),
    ("a named reset still ahead", {"kind": "quota", "at": NOW - HOUR, "resets_at": NOW + HOUR}, NOW + HOUR),
    ("no named reset: one window after it was written", {"kind": "quota", "at": NOW - HOUR}, NOW + 4 * HOUR),
    ("a named reset already passed", {"kind": "quota", "at": NOW - 2 * HOUR, "resets_at": NOW - HOUR}, None),
    ("no named reset, written a window ago", {"kind": "quota", "at": NOW - 6 * HOUR}, None),
    ("no time written: a window from now", {"kind": "quota"}, NOW + 5 * HOUR),
]


@pytest.mark.parametrize("case,err,expected", QUOTA_BLOCK)
def test_quota_block_until_golden_values(case, err, expected):
    assert W.quota_block_until(err, now=NOW) == expected, case


# ── lib.burn_rate: credit readings to a projection ──
ADAPTER = "kiro"         # the adapter whose readings are asked for; a fourth element is the one a reading came through


def _samples(root, rows):
    for at, used, account, *through in rows:
        A.record_credit_sample(root, used, 10_000, reset_at=NOW + 30 * DAY, account=account, at=at,
                               adapter=through[0] if through else ADAPTER)


BURN_RATE = [
    ("no readings", [], None),
    ("one reading", [(NOW - HOUR, 100, "acct-a")], None),
    ("two readings an hour apart", [(NOW - 2 * HOUR, 100, "acct-a"), (NOW - HOUR, 200, "acct-a")], None),
    ("four hours, 400 used", [(NOW - 5 * HOUR, 100, "acct-a"), (NOW - HOUR, 500, "acct-a")],
     {"per_day": 2400.0, "remaining": 9500.0, "before_reset": True, "account": "acct-a"}),
    ("nothing spent", [(NOW - 5 * HOUR, 100, "acct-a"), (NOW - HOUR, 100, "acct-a")],
     {"per_day": 0.0, "days_left": None, "before_reset": False}),
    ("the limit already exceeded", [(NOW - 5 * HOUR, 12_000, "acct-a"), (NOW - HOUR, 12_503, "acct-a")],
     {"remaining": 0.0, "days_left": 0.0, "before_reset": True}),
    # the defect: the old account's cumulative figure is not the new account's spend
    ("an account switch starts a new series",
     [(NOW - 30 * HOUR, 6_797, "acct-old"), (NOW - 20 * HOUR, 12_503, "acct-old"), (NOW - HOUR, 417, "acct-new")],
     None),
    ("the new account's own series",
     [(NOW - 30 * HOUR, 6_797, "acct-old"), (NOW - 20 * HOUR, 12_503, "acct-old"),
      (NOW - 9 * HOUR, 400, "acct-new"), (NOW - HOUR, 417, "acct-new")],
     {"per_day": 51.0, "before_reset": False, "account": "acct-new"}),
    ("a reading that does not name its account", [(NOW - 5 * HOUR, 100, "acct-a"), (NOW - HOUR, 500, None)], None),
    ("readings older than the window", [(NOW - 80 * HOUR, 100, "acct-a"), (NOW - 75 * HOUR, 9_000, "acct-a")],
     None),
    # the defect: another harness's readings in the same ledger are not this implementer's account
    ("another adapter's readings", [(NOW - 5 * HOUR, 100, "acct-a", "other"), (NOW - HOUR, 500, "acct-a", "other")],
     None),
    ("another adapter's newer reading ends no series of this one's",
     [(NOW - 5 * HOUR, 100, "acct-a"), (NOW - HOUR, 500, "acct-a"), (NOW - HOUR / 2, 9_000, "acct-b", "other")],
     {"per_day": 2400.0, "remaining": 9500.0, "account": "acct-a"}),
    ("a reading that does not name its adapter",
     [(NOW - 5 * HOUR, 100, "acct-a", None), (NOW - HOUR, 500, "acct-a", None)], None),
]


@pytest.mark.parametrize("case,rows,expected", BURN_RATE)
def test_burn_rate_golden_values(case, rows, expected, project):
    _samples(project["root"], rows)
    got = A.burn_rate(project["root"], ADAPTER, now=NOW)
    if expected is None:
        assert got is None, case
    else:
        assert got is not None, case
        assert {key: (round(got[key], 3) if isinstance(got[key], float) else got[key])
                for key in expected} == expected, case


# ── cli._credits_problem: a reading and a projection to one doctor finding ──
CREDITS_PROBLEM = [
    ("nothing read", None, None, None),
    ("under the limit, no projection", None, {"used": 400.0, "limit": 10_000.0, "account": "acct-a"}, None),
    ("exhausted, account named", None, {"used": 12_503.0, "limit": 10_000.0, "account": "acct-a"},
     "credits exhausted at the last reading: 12503/10000 (account acct-a)"),
    ("exhausted, account not named", None, {"used": 12_503.0, "limit": 10_000.0},
     "credits exhausted at the last reading: 12503/10000 (account not named by the reading)"),
    ("runs out before the reset",
     {"before_reset": True, "exhausts_at": _at("2026-09-20 12:00"), "per_day": 2400.0, "account": "acct-a"},
     {"used": 500.0, "limit": 10_000.0, "account": "acct-a"},
     "credits run out 20 Sep, before the reset (2400/day, account acct-a)"),
    ("lasts past the reset", {"before_reset": False, "account": "acct-a"},
     {"used": 500.0, "limit": 10_000.0, "account": "acct-a"}, None),
]


@pytest.mark.parametrize("case,projection,last,expected", CREDITS_PROBLEM)
def test_credits_problem_golden_values(case, projection, last, expected):
    got = cli._credits_problem(projection, last)
    if expected is None:
        assert got is None, case
    else:
        assert got[0] == "credits-exhaust" and got[1].startswith(expected), (case, got)


# ── lib.rounds: reviews to rounds spent on the running slice ──
ROUNDS = [
    # the defect: other slices' reviews at the same HEAD are not this slice's rounds
    ("other slices at the same HEAD", "R2", [("NEEDS_CHANGES", "R1")] * 3 + [("NEEDS_CHANGES", "R2")], 1),
    ("an approval ends the count", "R2", [("NEEDS_CHANGES", "R2"), ("APPROVED", "R2")], 0),
    ("a review that did not take place", "R2", [("NEEDS_CHANGES", "R2"), ("UNAVAILABLE", "R2")], 1),
    ("nothing running", None, [("NEEDS_CHANGES", "R2")], 0),
]


@pytest.mark.parametrize("case,running,reviews,expected", ROUNDS)
def test_rounds_golden_values(case, running, reviews, expected, project):
    if running:
        _running(project["root"], running)
    for verdict, slice_id in reviews:
        _review(project["root"], verdict, slice_id=slice_id)
    assert A.rounds(project["root"], "semantic-review") == expected, case


# ── the tables keep up with the functions ──
GOLDEN = {
    W.parse_reset: (PARSE_RESET, 10),
    W._reset_clock: (PARSE_RESET, 6),
    W.quota_block_until: (QUOTA_BLOCK, 6),
    A.burn_rate: (BURN_RATE, 11),
    cli._credits_problem: (CREDITS_PROBLEM, 8),
    A.rounds: (ROUNDS, 35),
}


def _branches(function):
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.While, ast.ExceptHandler)):
            count += 1
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            count += len(node.ifs)
    return count


@pytest.mark.parametrize("function", list(GOLDEN), ids=lambda f: f.__name__)
def test_no_golden_function_gains_a_branch_without_a_row(function):
    table, recorded = GOLDEN[function]
    branches = _branches(function)
    assert branches == recorded, (
        f"{function.__module__}.{function.__name__} has {branches} branches, its golden table was written "
        f"for {recorded}: add rows for what changed, then set the count to {branches}")
    assert table
