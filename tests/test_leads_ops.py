"""The bug hunter's leads on catch-up, cost, the doctor, completion and the hunt itself (LEADS-OPS).

ao's read-only bug hunter read src/ao/parts on 2026-10-12 and sent nine leads. Seven were faults:
a NEEDS_CHANGES waiver closed before the architect's decision request was written, so a request
that could not be written was never written; the scheduled check paged what the watchdog's first
cycle was about to name whenever `ao watchdog uninstall` had taken the heartbeat; `ao doctor` said
exhausted, in red, of a reading whose own reset had passed; `ao cost` printed a window that spent
nothing as a total of 1, and ended on a review a prune removed while it counted; the completion
scripts took every option for one that takes the next word alone, so the second path of
`ao review --paths` was offered the command's actions; and two hunts at once each read the leads
already sent before either recorded its own, both sent one lead, and the second wrote its mail
over the first's. One was half right: a review that exits 3 with nothing recorded asked no
reviewer, so it holds no other review, but it was told as an unavailable reviewer. One was wrong:
bash's Readline quotes what a completion function offers only under `-o filenames`, which the
bash script does not set, so its `%q` is the only quoting.
"""
import argparse
import contextlib
import os
import re
import shlex
import shutil
import sys
import threading
import time
from collections import Counter
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, storage, watchdog as W
from tests.test_bug_hunter import _OneMinute, _repo, _stand_in
from tests.test_catchup_ready import NEEDS_CHANGES, PERSON, _catchup, _land, _legacy_waiver
from tests.test_completion import POSIX_SHELL, _run
from tests.test_notice_noise import DAY, HOUR, IMPLEMENTER, _clock, _heartbeat, _human, _paged, _watchdog_job
from tests.test_oct1_fixes import _waived_ranges
from tests.test_review_chain import _fake


def _plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _no_space(*args, **kwargs):
    raise OSError(28, "No space left on device")


# ---- catch-up --------------------------------------------------------------------------------------------------

def test_a_review_that_exits_3_with_nothing_recorded_is_told_as_one_and_holds_no_other(project, monkeypatch, capsys):
    root = project["root"]
    ranges = _waived_ranges(root, "B1", "B2")
    seen = []

    def held(cfg, ns):
        # What `ao review` does when another review of the range is asking its sections: no reviewer is asked.
        seen.append(ns.commits)
        print("not reviewed: another review of this candidate is asking its sections")
        return 3

    monkeypatch.setattr(cli, "cmd_review", held)
    monkeypatch.setattr(W, "run", lambda ns: 0)

    assert _catchup(project, **PERSON) == 3

    out = capsys.readouterr().out
    assert "no review was recorded (exit 3); W-legacy-B1 stays open" in out
    assert "reviewer still unavailable" not in out and "started no other review" not in out
    assert seen == ranges


def test_a_decision_request_that_cannot_be_written_leaves_its_waiver_open_for_the_next_run(
        project, monkeypatch, capsys):
    monkeypatch.delenv("AO_ROLE", raising=False)
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    waiver = _legacy_waiver(root, "B7")
    _land(root, "a.py", "value = 2\n", "b7")
    monkeypatch.setattr(W, "run", lambda ns: 0)
    reviewed = dict(project, reviewer={"id": "r1", "family": "review-family", "argv": _fake(*NEEDS_CHANGES)})
    write_mail = A.write_mail
    monkeypatch.setattr(A, "write_mail", _no_space)

    assert _catchup(reviewed, **PERSON) == 1

    out = _plain(capsys.readouterr().out)
    assert "the architect's decision request was not written: [Errno 28] No space left on device; " \
           f"{waiver['id']} stays open" in out
    assert [w["id"] for w in A.open_waivers(root)] == [waiver["id"]]

    monkeypatch.setattr(A, "write_mail", write_mail)
    assert _catchup(reviewed, **PERSON) == 0
    assert A.open_waivers(root) == []
    (name,) = os.listdir(os.path.join(root, "agent-mail"))
    assert name.endswith("-REVIEW-b7-needs-changes.md")


def test_a_persons_needs_changes_closes_its_waiver_once_the_request_is_written(project, monkeypatch, capsys):
    monkeypatch.delenv("AO_ROLE", raising=False)
    root = project["root"]
    _land(root, "a.py", "value = 1\n", "base")
    waiver = _legacy_waiver(root, "B7")
    _land(root, "a.py", "value = 2\n", "b7")
    monkeypatch.setattr(W, "run", lambda ns: 0)
    monkeypatch.setattr(cli, "_person_range_review", lambda root, cfg, commits: {
        "verdict": "NEEDS_CHANGES", "artefact": "person-b7.md",
        "person": {"by": "A. Person", "user": "a", "interactive": True}})
    write_mail = A.write_mail
    monkeypatch.setattr(A, "write_mail", _no_space)

    assert _catchup(project) == 1

    assert [w["id"] for w in A.open_waivers(root)] == [waiver["id"]]
    assert "closed on a person's review" not in capsys.readouterr().out

    monkeypatch.setattr(A, "write_mail", write_mail)
    assert _catchup(project) == 0
    assert A.open_waivers(root) == [] and len(os.listdir(os.path.join(root, "agent-mail"))) == 1


# ---- the doctor ------------------------------------------------------------------------------------------------

def _doctor_record(root, at, beat):
    """The scheduled check's record of its last run, and the heartbeat it found then."""
    path = A.project_file(root, "doctor-runs")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"at": %d, "back": null, "beat": %s}' % (at, "null" if beat is None else int(beat)))


def test_the_doctor_leaves_a_watchdog_installed_again_with_no_heartbeat_to_its_first_cycle(
        project, monkeypatch, capsys):
    clock = _clock(monkeypatch)
    raised = _paged(monkeypatch)
    root = project["root"]
    stopped = clock[0] - 14 * DAY
    _doctor_record(root, stopped, beat=stopped - 60)      # its last run, with the watchdog running
    _watchdog_job(root, 300)                               # installed again: the uninstall took the heartbeat
    A.record_credit_sample(root, 10240, 10000, reset_at=clock[0] + 10 * DAY, account="acct-1", at=stopped - 600,
                           adapter=IMPLEMENTER)
    assert A.heartbeat_age(root) is None

    assert cli._doctor_check(project, page=True) == 1

    out = capsys.readouterr().out
    assert _human(raised) == [] and "not paged: the watchdog's job came back with this check" in out

    clock[0] += 60                                         # its first cycle ran, and it keeps running
    _heartbeat(root, clock[0])
    clock[0] += 14 * 60
    _heartbeat(root, clock[0] - 30)
    cli._doctor_check(project, page=True)

    assert _human(raised) == ["credits-exhaust"]


@pytest.mark.parametrize("ran", ["by-hand-before", "never"])
def test_a_check_that_saw_no_heartbeat_when_it_last_ran_gives_no_cycle(project, monkeypatch, ran):
    clock = _clock(monkeypatch)
    raised = _paged(monkeypatch)
    root = project["root"]
    if ran == "by-hand-before":
        _doctor_record(root, clock[0] - 2 * HOUR, beat=None)  # two hours ago, where no watchdog runs
    A.record_credit_sample(root, 10240, 10000, reset_at=clock[0] + 10 * DAY, account="acct-1",
                           at=clock[0] - 3 * HOUR, adapter=IMPLEMENTER)

    cli._doctor_check(project, page=True)

    assert _human(raised) == ["credits-exhaust"]


def test_the_doctor_shows_a_spent_reading_past_its_reset_as_the_last_reading_not_as_exhausted(project):
    root = project["root"]
    now = time.time()
    # Read two weeks before the jobs came back; the plan reset six hours before they did.
    A.record_credit_sample(root, 10240, 10000, reset_at=now - 6 * HOUR, account="acct-1", at=now - 14 * DAY,
                           adapter=IMPLEMENTER)

    (line,) = [" ".join(_plain(line).split()) for line in cli._doctor_credit_lines(project)]

    assert line.startswith("credits 10240/10000 at the last reading, taken before its reset on ")
    assert line.endswith("— unknown since; `ao credits` reads them now") and "exhausted" not in line
    own = cli._implementer_credits(project)
    assert cli._credits_problem(own["rate"], own["samples"][-1]) is None        # as the check reads it

    A.record_credit_sample(root, 10240, 10000, reset_at=now + DAY, account="acct-1", at=now - HOUR,
                           adapter=IMPLEMENTER)
    (line,) = [" ".join(_plain(line).split()) for line in cli._doctor_credit_lines(project)]
    assert line == "credits 10240/10000 at the last reading exhausted"


# ---- ao cost ---------------------------------------------------------------------------------------------------

def _spent(monkeypatch, total):
    monkeypatch.setattr(A, "account_usage", lambda timeout=20, adapter_id=None: {
        "used": 5000.0, "limit": 10000.0, "reset_at": None, "plan": "KIRO PRO+", "overage_status": "DISABLED"})
    monkeypatch.setattr(A, "turn_costs", lambda cfg, since=None: {
        "turns": 3, "total": total, "unit": "credits", "ao_commands": Counter(),
        "by_class": {"analysis": {"turns": 3, "usage": total, "wasted": 0, "wasted_usage": 0}}})


def test_ao_cost_prints_a_window_that_spent_nothing_as_nothing(project, monkeypatch, capsys):
    _spent(monkeypatch, 0.0)

    assert cli.cmd_cost(project, SimpleNamespace(since=None)) == 0

    out = _plain(capsys.readouterr().out)
    assert re.search(r"^  total +3 +0$", out, re.M)
    assert re.search(r"^  analysis +3 +0 +—", out, re.M) and "0%" not in out.split("the account:")[0]
    assert "coordination + ceremony: no share: nothing was spent" in out


def test_ao_cost_counts_no_review_a_prune_removed_after_it_was_listed(project, monkeypatch, capsys):
    root = project["root"]
    _spent(monkeypatch, 1250.0)
    reviews = os.path.join(root, "semantic-review")
    with open(os.path.join(reviews, "kept.md"), "w", encoding="utf-8") as fh:
        fh.write("VERDICT: APPROVED\n")
    gone = os.path.join(reviews, "gone.md")
    listdir, isfile = os.listdir, os.path.isfile
    # It was there when the directory was listed and looked at, and a prune running beside it removed it since.
    monkeypatch.setattr(os, "listdir", lambda path=".": listdir(path) + (
        ["gone.md"] if os.path.abspath(path) == os.path.abspath(reviews) else []))
    monkeypatch.setattr(os.path, "isfile", lambda path: path == gone or isfile(path))

    assert cli.cmd_cost(project, SimpleNamespace(since=None)) == 0

    assert "reviews: 1 files (0 unavailable/invalid, cost nothing)" in _plain(capsys.readouterr().out)
    assert [name for name, _ in A.reviews(root, "semantic-review", limit=10)] == ["kept.md"]


# ---- completion ------------------------------------------------------------------------------------------------

def _arity_parser():
    """A command whose options take no word, the next alone, an optional one, two, any number, or all after."""
    parser = argparse.ArgumentParser(prog="ao")
    sub = parser.add_subparsers(dest="cmd")
    pick = sub.add_parser("pick")
    pick.add_argument("what", nargs="?", choices=["first", "second"])
    pick.add_argument("--flag", action="store_true")
    pick.add_argument("--one", choices=["o1", "o2"])
    pick.add_argument("--maybe", nargs="?", choices=["m1", "m2"])
    pick.add_argument("--two", nargs=2, choices=["t1", "t2"])
    pick.add_argument("--many", nargs="*", choices=["a1", "a2"])
    pick.add_argument("--rest", nargs=argparse.REMAINDER, choices=["r1", "r2"])
    return parser


OPTIONS = ["--flag", "--help", "--many", "--maybe", "--one", "--rest", "--two"]
# The words after `ao pick` before the cursor, the word at it, and what completes it.
ARITY = [(["--many", "a1"], "", ["a1", "a2"]),                 # a second value of --many, not the command's word
         (["--two", "t1"], "", ["t1", "t2"]),                  # the second of --two's two
         (["--two", "t1", "t2"], "", ["first", "second"]),     # both taken: the command's word
         (["--maybe", "--two"], "", ["t1", "t2"]),             # --maybe takes no word that begins with -
         (["--maybe"], "-", OPTIONS),                          # a word that begins with - is an option
         (["--rest", "r1", "-x"], "", ["r1", "r2"]),           # --rest takes every word after it
         (["--one", "o1"], "", ["first", "second"])]


def test_the_model_says_how_many_words_each_option_takes():
    words = cli._completion_model(_arity_parser())["pick"]

    assert words["valued"] == ["--one"]
    assert words["arity"] == {"--maybe": "?", "--two": "2", "--many": "*", "--rest": "..."}
    review = cli._completion_model(cli.build_parser())["review"]
    assert review["arity"] == {"--paths": "*"} and "--paths" not in review["valued"]


def test_fish_and_powershell_hold_each_options_arity():
    fish = cli.completion_script(_arity_parser(), "fish")
    powershell = cli.completion_script(_arity_parser(), "powershell")

    assert "function __ao_arity --argument-names cmd option" in fish
    assert "    if contains -- \"$cmd\" 'pick'; and contains -- \"$option\" '--many'\n        printf '%s\\n' '*'" in fish
    assert "set -l arity (__ao_arity \"$cmd\" \"$word\")" in fish
    assert "    function Get-AoArity([string]$Command, [string]$Option) {" in powershell
    assert "                    '--two' {\n                        return @('2')" in powershell
    assert "$arity = [string](@(Get-AoArity $command $word) | Select-Object -First 1)" in powershell


def _bash_offers(script, asked, cwd):
    """What the bash script offers for each list of words, the last word the one at the cursor."""
    body = ['source "$1"'] + [f"COMP_WORDS=({' '.join(shlex.quote(word) for word in words)}); "
                              f"COMP_CWORD={len(words) - 1}; _ao; printf '%s\\n' \"${{COMPREPLY[@]}}\"; echo --"
                              for words in asked]
    done = _run(["bash", "-c", "\n".join(body), "_", str(script)], cwd)
    assert done.returncode == 0, done.stderr
    return [block.split() for block in done.stdout.split("--\n")[:-1]]


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason=POSIX_SHELL)
def test_bash_completes_each_word_an_option_takes_as_that_options(tmp_path):
    script = tmp_path / "ao.bash"
    script.write_text(cli.completion_script(_arity_parser(), "bash"), encoding="utf-8")
    ours = tmp_path / "ours.bash"
    ours.write_text(cli.completion_script(cli.build_parser(), "bash"), encoding="utf-8")

    assert _bash_offers(script, [["ao", "pick", *before, cur] for before, cur, _ in ARITY], tmp_path) == \
        [expected for _, _, expected in ARITY]
    # A second path is a path: files, where `ao review`'s actions were offered.
    assert _bash_offers(ours, [["ao", "review", "--paths", "a.py", ""], ["ao", "review", ""]], tmp_path) == \
        [[], ["submit", "collect", "cancel"]]


@pytest.mark.skipif(os.name == "nt" or shutil.which("zsh") is None, reason=POSIX_SHELL)
def test_zsh_completes_each_word_an_option_takes_as_that_options(tmp_path):
    script = tmp_path / "_ao"
    script.write_text(cli.completion_script(_arity_parser(), "zsh"), encoding="utf-8")
    body = ['autoload -U compinit; compinit -u -d "$2"', 'source "$1"',
            "compadd() { [[ $1 == -- ]] && shift; print -rl -- \"$@\"; }", "_files() { print -r -- '(files)'; }"]
    for before, cur, _ in ARITY:
        words = ["ao", "pick", *before, cur]
        body.append(f"words=({' '.join(shlex.quote(w) for w in words)}); CURRENT={len(words)}; "
                    f"PREFIX={shlex.quote(cur)}; _ao; print -- --")

    done = _run(["zsh", "-f", "-c", "\n".join(body), "_", str(script), str(tmp_path / "zcompdump")], tmp_path)

    assert done.returncode == 0, done.stderr
    offered = [block.split() for block in done.stdout.split("--\n")[:-1]]
    assert offered == [expected for _, _, expected in ARITY]


# ---- the hunt --------------------------------------------------------------------------------------------------

LEAD = "- [clock] src/a.py:3 parse_reset — a reset named for tomorrow is read as today"


def test_two_hunts_at_once_send_one_lead_once(project, monkeypatch, capsys):
    root = project["root"]
    _repo(root)
    _stand_in(monkeypatch)
    monkeypatch.setattr(cli, "datetime", _OneMinute)                    # both in one minute: one mail's name
    monkeypatch.setattr(cli, "_run_reviewer", lambda root, argv, timeout, **kwargs: {"ok": True, "out": LEAD + "\n"})
    cfg = dict(project, hunter={"id": "h1", "argv": [sys.executable, "-B", "-c", "print()", "{prompt}"]})
    reading = threading.Event()         # the first hunt has read the leads already sent
    met = threading.Event()             # and the second has too, or waits for the first to finish
    known, lock = A.hunter_known, storage._exclusive_lock

    def known_then_wait(path):
        found = known(path)
        if threading.current_thread() is first:
            reading.set()
            assert met.wait(timeout=60)
        else:
            met.set()
        return found

    @contextlib.contextmanager
    def waiting(path, timeout=10.0):
        if path.endswith("hunter.lock") and threading.current_thread() is second:
            met.set()
        with lock(path, timeout=timeout):
            yield

    monkeypatch.setattr(A, "hunter_known", known_then_wait)
    monkeypatch.setattr(storage, "_exclusive_lock", waiting)
    codes, errors = [], []

    def hunt():
        try:
            codes.append(cli.cmd_hunt(cfg, SimpleNamespace(action="run", fingerprint=None)))
        except BaseException as exc:                                    # an assertion in a thread is told here
            errors.append(exc)

    first, second = threading.Thread(target=hunt), threading.Thread(target=hunt)
    first.start()
    assert reading.wait(timeout=60), errors          # past its slice of the tree, so the two never write it at once
    second.start()
    first.join(timeout=120)
    second.join(timeout=120)

    assert errors == [] and codes == [0, 0]
    assert [row["fingerprint"] for row in A.hunter_rows(root) if row.get("event") == "sent"] == \
        [A.parse_leads(LEAD)[0]["fingerprint"]]
    assert len([name for name in A.mailbox(root, "agent-mail") if "-hunter-to-" in name]) == 1
    assert sorted(re.findall(r"\d+ lead\(s\), (\d+) new", capsys.readouterr().out)) == ["0", "1"]
