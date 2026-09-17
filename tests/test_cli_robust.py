"""Ordinary input ends in a message, and what ao prints suits where it goes (CLI-ROBUST).

A product review ran ao in a sandbox. `ao cost --since yesterday`, `ao stats --since 17/09/2026` and
`ao mail compact soon` ended in tracebacks, among nine time syntaxes no two commands shared, and so
did a ledger lock another process held and Ctrl+C. `ao answer` recorded a key the question never
offered and overwrote the answer before it; there was no `ao --version`; colour went into pipes and
past NO_COLOR; `ao watch` looped screen codes into a pipe; and `-C` a directory that does not exist
showed an empty panel and exited 0.
"""
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import ao
from ao import cli, lib as A, storage

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 17, 14, 30).timestamp()
PAST = "2026-01-15"                  # a date behind any clock this suite runs on
FORMS = ["30m", "2h", "1d", "today", "yesterday", PAST]


class _Stdout(io.StringIO):
    """A stdout that says whether it is a terminal."""

    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


def _printed(project, monkeypatch, tty, *words):
    stdout = _Stdout(tty)
    monkeypatch.setattr(sys, "stdout", stdout)
    return cli.main(["-C", project["root"], *words]), stdout.getvalue()


# ---- one time syntax ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, seconds", [("30m", 1800), ("2h", 7200), ("1d", 86400), ("1w", 7 * 86400),
                                           ("1.5h", 5400), ("24h", 86400), ("7d", 7 * 86400), (" 2H ", 7200)])
def test_a_span_counts_back_from_now_and_forward_for_a_snooze(text, seconds):
    when = A.parse_time(text)

    assert when.seconds == seconds and when.at is None and when.label() == f"last {text.strip()}"
    assert when.moment(now=NOW) == NOW - seconds and when.moment(ahead=True, now=NOW) == NOW + seconds


def test_today_yesterday_and_a_date_name_local_moments():
    midnight = datetime(2026, 9, 17).timestamp()

    assert A.parse_time("today", now=NOW).moment() == midnight == A.parse_time("2026-09-17").moment()
    assert A.parse_time("yesterday", now=NOW).moment() == datetime(2026, 9, 16).timestamp()
    assert A.parse_time("2026-09-17T14:30").moment() == A.parse_time("2026-09-17 14:30:00").moment() == NOW
    utc = datetime(2026, 9, 17, 11, 30, tzinfo=timezone.utc).timestamp()
    assert A.parse_time("2026-09-17T11:30Z").moment() == A.parse_time("2026-09-17T14:30+03:00").moment() == utc
    assert A.parse_time("today", now=NOW).label() == "since today"


def test_a_bare_number_counts_in_the_unit_its_option_always_took():
    assert A.time_span("7", "d", now=NOW) == 7 and A.time_span("0.5", "d", now=NOW) == 0.5
    assert A.time_span("24", "h", now=NOW) == 24 == A.time_span("1d", "h", now=NOW)
    assert A.time_span("12h", "d", now=NOW) == 0.5 and A.time_span("today", "h", now=NOW) == 14.5

    with pytest.raises(ValueError, match="needs a unit"):
        A.parse_time("7")
    with pytest.raises(ValueError, match="is in the future"):
        A.time_span("2099-01-01", "d", now=NOW)


@pytest.mark.parametrize("text", ["17/09/2026", "soon", "", "2h ago", "-2h", "1y", "2026-02-30", "2026-09-17T25:00"])
def test_anything_else_is_refused_naming_the_forms_it_takes(text):
    with pytest.raises(ValueError, match="is not a time: give 30m, 2h, 1d"):
        A.parse_time(text)


@pytest.mark.parametrize("words, dest, before", [
    (["cost", "--since"], "since", ["24h", "7d", "30d"]),
    (["stats", "--since"], "since", ["2026-09-10"]),
    (["stats", "--until"], "until", ["2026-09-17"]),
    (["alarms", "snooze", "doctor:commit-hook", "--until"], "until", ["2099-01-01"]),
    (["digest", "--days"], "days", ["1", "0.5"]),
    (["prune", "--days"], "days", ["7"]),
    (["prune", "--review-days"], "review_days", ["30"]),
    (["status", "--window"], "window", ["24", "12.5"]),
])
def test_every_time_option_takes_every_form_and_the_ones_it_took_before(words, dest, before):
    parser = cli.build_parser()

    for text in FORMS + before:
        value = getattr(parser.parse_args(words + [text]), dest)
        assert isinstance(value, A.When) or value >= 0, text


def test_a_span_option_still_holds_the_number_its_command_reads():
    def parsed(*words):
        return cli.build_parser().parse_args(list(words))

    assert parsed("digest", "--days", "7").days == 7 and parsed("digest").days == 1
    assert parsed("digest", "--days", "12h").days == 0.5 and parsed("digest", "--days", ".5").days == 0.5
    assert parsed("status", "--window", "24").window == 24 == parsed("status", "--window", "1d").window
    assert parsed("prune", "--review-days", "30").review_days == 30 and parsed("prune").days == 7
    assert parsed("cost", "--since", "yesterday").since.label() == "since yesterday"


def test_since_and_mail_compact_read_the_same_forms(project, monkeypatch, capsys):
    compacted = []
    monkeypatch.setattr(A, "compact_messages", lambda root, days: compacted.append(days) or [])

    for text in FORMS + ["last"]:
        assert cli.cmd_since(project, SimpleNamespace(ref=text, no_mark=True)) == 0, text
    for text in ["30", "7d", "12h", "today", "yesterday", PAST]:
        args = SimpleNamespace(action="compact", type=text, topic=None, body=None, mail_class=None)
        assert cli.cmd_mail(project, args) in (None, 0), text

    assert compacted[:3] == [30, 7, 0.5] and len(compacted) == 6 and all(days >= 0 for days in compacted)


@pytest.mark.parametrize("since", ["yesterday", "today", "30m"])
def test_cost_reads_the_windows_it_crashed_on(project, monkeypatch, since):
    asked = []
    monkeypatch.setattr(A, "turn_costs", lambda cfg, since=None: asked.append(since) or {"turns": [], "by_class": {}})

    assert cli.main(["-C", project["root"], "cost", "--since", since]) == 0
    assert len(asked) == 1 and asked[0] <= time.time()


def test_a_snooze_until_a_span_counts_forward_from_now(project):
    before = time.time()

    assert cli.main(["-C", project["root"], "alarms", "snooze", "doctor:commit-hook", "--until", "3d",
                     "--why", "waits on the owner"]) == 0

    until = A.alarm_snoozed(A.project_key(project["root"]), "doctor:commit-hook")["until"]
    assert before + 3 * 86400 - 1 <= until <= time.time() + 3 * 86400


# ---- ordinary trouble ends in a line -----------------------------------------------------------

@pytest.mark.parametrize("words", [
    ["cost", "--since", "soon"], ["stats", "--since", "17/09/2026"], ["digest", "--days", "a-week"],
    ["status", "--window", "yesterday-ish"], ["prune", "--days", "2099-01-01"],
    ["alarms", "snooze", "doctor:commit-hook", "--until", "next week"],
])
def test_a_time_ao_cannot_read_exits_2_with_a_message_and_no_traceback(project, capsys, words):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-C", project["root"], *words])

    err = capsys.readouterr().err
    assert stopped.value.code == 2 and "Traceback" not in err
    assert re.search(r"is not a time: give|is in the future", err)


def test_mail_compact_refuses_what_is_no_time_before_compacting_anything(project, monkeypatch, capsys):
    monkeypatch.setattr(A, "compact_messages", lambda root, days: pytest.fail(f"compacted at {days} days"))

    assert cli.main(["-C", project["root"], "mail", "compact", "soon"]) == 2
    assert cli.main(["-C", project["root"], "mail", "compact", "2099-01-01"]) == 2

    shown = capsys.readouterr()
    assert "'soon' is not a time" in shown.out and "2099-01-01 is in the future" in shown.out
    assert "Traceback" not in shown.err


def test_a_ledger_lock_held_past_the_wait_is_one_line_naming_it(project, monkeypatch, capsys):
    root = project["root"]
    clock = [0.0]

    def an_hour_later():
        clock[0] += 3600                     # every look at the clock is an hour on: the wait ends at once
        return clock[0]

    with storage._exclusive_lock(A.decisions_path(root) + ".lock", timeout=0):
        monkeypatch.setattr(storage, "time", SimpleNamespace(monotonic=an_hour_later, sleep=lambda seconds: None,
                                                             time=time.time))
        code = cli.main(["-C", root, "decide", "use sqlite", "--why", "it is enough"])

    err = capsys.readouterr().err
    assert code == 1 and "Traceback" not in err and len(err.strip().splitlines()) == 1
    assert os.path.join(".ao", "ledger", "decisions.jsonl.lock") in err and "run this again" in err


def test_ctrl_c_exits_130_without_a_traceback_and_lets_the_machine_lock_go(project, monkeypatch, tmp_path, capsys):
    real_run, lock, held = subprocess.run, tmp_path / "gate.lock", []
    monkeypatch.setattr(A, "GATE_LOCK", str(lock))

    def run(*args, **kwargs):
        if (args[0] if args else kwargs.get("args")) == ["sleep", "20"]:
            held.append(lock.exists())
            raise KeyboardInterrupt                  # Ctrl+C while the wrapped command runs
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)

    assert cli.main(["-C", project["root"], "lock", "--", "sleep", "20"]) == 130
    assert held == [True] and not lock.exists() and "Traceback" not in capsys.readouterr().err


def test_ctrl_c_while_a_message_body_is_read_exits_130_and_writes_nothing(project, monkeypatch, capsys):
    class Interrupted:
        def read(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(sys, "stdin", Interrupted())

    assert cli.main(["-C", project["root"], "mail", "send", "INFO", "hello"]) == 130
    assert os.listdir(os.path.join(project["root"], project["mailbox"])) == []
    assert "Traceback" not in capsys.readouterr().err


def test_an_unexpected_error_is_one_line_and_ao_debug_keeps_its_traceback(project, monkeypatch, capsys):
    def broken(cfg, args):
        raise RuntimeError("the store\nis gone")

    monkeypatch.setattr(cli, "cmd_board", broken)
    monkeypatch.delenv("AO_DEBUG", raising=False)

    assert cli.main(["-C", project["root"], "board"]) == 1
    [line] = capsys.readouterr().err.splitlines()
    assert line.startswith("ao board: RuntimeError: the store is gone") and "AO_DEBUG=1" in line

    monkeypatch.setenv("AO_DEBUG", "1")
    with pytest.raises(RuntimeError):
        cli.main(["-C", project["root"], "board"])


def test_a_project_directory_that_does_not_exist_exits_2(tmp_path, capsys):
    missing, afile = tmp_path / "no" / "such" / "project", tmp_path / "a-file"
    afile.write_text("not a directory\n", encoding="utf-8")

    for root, what in ((missing, "no such directory"), (afile, "not a directory")):
        with pytest.raises(SystemExit) as stopped:
            cli.main(["-C", str(root), "status"])
        err = capsys.readouterr().err
        assert stopped.value.code == 2 and f"-C {root}: {what}" in err and "Traceback" not in err


# ---- answers -----------------------------------------------------------------------------------

def _answer(project, capsys, *words):
    code = cli.main(["-C", project["root"], "answer", *words])
    return code, capsys.readouterr().out


def test_an_answer_is_a_key_the_question_offers(project, capsys):
    root = project["root"]
    asked = A.ask(root, "Which database for the cache?", ["Postgres", "SQLite"])

    code, out = _answer(project, capsys, asked["id"], "z")
    assert code == 2 and "a) Postgres" in out and "b) SQLite" in out and "x) " in out
    assert _answer(project, capsys, asked["id"], "x")[0] == 2                  # x is answered in words
    assert _answer(project, capsys, asked["id"], "b", "because")[0] == 2       # b by its key alone
    assert A.decisions(root)[0]["state"] == "open"

    assert _answer(project, capsys, asked["id"], "B")[0] == 0
    [rec] = A.decisions(root)
    assert (rec["state"], rec["answer"], rec["answer_key"]) == ("answered", "SQLite", "b")
    assert rec["answered_by"] == "terminal" and len(rec["answers"]) == 1


def test_a_second_answer_needs_change_and_both_stay_on_the_record(project, capsys):
    root = project["root"]
    asked = A.ask(root, "Which database for the cache?", ["Postgres", "SQLite"])
    assert _answer(project, capsys, asked["id"], "b")[0] == 0

    code, out = _answer(project, capsys, asked["id"], "x", "Use", "DuckDB")
    assert code == 2 and "already answered" in out and A.decisions(root)[0]["answer"] == "SQLite"

    code, out = _answer(project, capsys, asked["id"], "x", "Use", "DuckDB", "--change")
    [rec] = A.decisions(root)
    assert code == 0 and "SQLite → Use DuckDB" in out
    assert (rec["answer"], rec["answer_key"]) == ("Use DuckDB", "x")
    assert [(row["answer"], row["answer_key"]) for row in rec["answers"]] == [("SQLite", "b"), ("Use DuckDB", "x")]


def test_a_change_to_an_answer_recorded_before_rows_keeps_that_answer_first(project):
    root = project["root"]
    asked = A.ask(root, "Which queue?", ["files", "sqlite"])
    path = os.path.join(root, A.DECISION_DIR, asked["id"] + ".json")
    with open(path, encoding="utf-8") as fh:
        rec = json.load(fh)
    rec.update(state="answered", answer="files", answer_key="a", answered_at=1, answered_by="a phone")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)

    with pytest.raises(A.AnswerRefused):
        A.answer(root, asked["id"], "b")
    changed = A.answer(root, asked["id"], "b", by="terminal", change=True)

    assert [(row["answer"], row["answered_by"]) for row in changed["answers"]] == [("files", "a phone"),
                                                                                  ("sqlite", "terminal")]


# ---- the version -------------------------------------------------------------------------------

def test_the_version_is_written_once_and_ao_the_package_and_pyproject_agree(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version"])
    shown = capsys.readouterr().out.strip()

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = pyproject.split("\n[project]\n", 1)[1].split("\n[", 1)[0]
    source = re.search(r'^\[tool\.hatch\.version\]\n(?:#.*\n)*path = "([^"]+)"', pyproject, re.M).group(1)
    written = re.findall(r'^__version__ = "([^"]+)"', (ROOT / source).read_text(encoding="utf-8"), re.M)

    assert stopped.value.code == 0 and shown == f"ao {ao.__version__}"
    assert source == "src/ao/__init__.py" and written == [ao.__version__]
    assert re.search(r'^dynamic = \["version"\]$', project, re.M) and not re.search(r"^version\s*=", project, re.M)


# ---- colour and the terminal -------------------------------------------------------------------

def test_colour_reaches_a_terminal_and_never_a_pipe_no_color_or_a_dumb_terminal(project, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    assert "\x1b[" in _printed(project, monkeypatch, True, "board")[1]
    assert "\x1b[" not in _printed(project, monkeypatch, False, "board")[1]
    for value in ("1", "0", ""):
        monkeypatch.setenv("NO_COLOR", value)                     # set, to anything
        assert "\x1b[" not in _printed(project, monkeypatch, True, "board")[1], value
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert "\x1b[" not in _printed(project, monkeypatch, True, "board")[1]


def test_watch_prints_the_panel_once_where_nothing_draws_it(project, monkeypatch):
    class FirstWaitEnds:
        """The time module as the command line sees it, where a watch that loops ends at its first wait."""

        def __getattr__(self, name):
            return getattr(time, name)

        def sleep(self, seconds):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "time", FirstWaitEnds())
    monkeypatch.setattr(cli, "render", lambda *args, **kwargs: f"{A.C['b']}PANEL{A.C['reset']}")
    monkeypatch.setattr(cli, "render_fleet", lambda *args, **kwargs: [f"{A.C['b']}FLEET{A.C['reset']}"])
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    for words, frame in ((["watch", "-i", "1"], "PANEL"), (["watch", "--all", "-i", "1"], "FLEET")):
        code, out = _printed(project, monkeypatch, False, *words)
        assert code == 0 and out.count(frame) == 1 and "\x1b" not in out, words

    code, out = _printed(project, monkeypatch, True, "watch", "-i", "1")
    assert code == 0 and out.startswith("\x1b[?1049h") and out.endswith("\x1b[?25h\x1b[?1049l")

    monkeypatch.setenv("TERM", "dumb")
    code, out = _printed(project, monkeypatch, True, "watch", "-i", "1")
    assert code == 0 and out.count("PANEL") == 1 and "\x1b" not in out
