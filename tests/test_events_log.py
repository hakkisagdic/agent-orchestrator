"""ao tells the machine's event log what it did, and `ao events` reads and follows it (EVENTS-LOG).

A surface that wanted to know what ao was doing had to poll a project's files, and a person waiting
on a submitted review ran `ao reviews` until it came back. Each place that does the work now appends
one line to ~/.ao/events.jsonl: a verification recorded, a review submitted and ended, commit
authority granted, mail sent, a nudge and a wake. These tests hold each of them to one line, the
reader to its filters and to following across the bound's trims, the log to its bound, and the work
to standing when its event cannot be written.
"""
import json
import os
import subprocess
from types import SimpleNamespace

from ao import cli, lib as A, watchdog as W
from tests import conftest
from tests.scenarios import World


def _write(*rows):
    """Lines appended as another ao process would have written them, at the times the test chooses."""
    with open(A.events_path(), "ab") as fh:
        for row in rows:
            fh.write(A.event_line(row))


def _of(kind):
    return [row for row in A.read_events() if row["kind"] == kind]


def _args(**given):
    """`ao events` with its defaults, and what the test gives."""
    return SimpleNamespace(**dict({"follow": False, "project": None, "since": None, "n": 20, "json": False}, **given))


def _store(root, cfg):
    with open(os.path.join(root, ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)


def _stage(root):
    with open(os.path.join(root, "feature.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    subprocess.run([conftest.GIT, "add", "feature.py"], cwd=root, check=True)


def _submit(project, monkeypatch, capsys, spawn=lambda root, rid: None):
    """`ao review submit` on the staged candidate, its run started by `spawn`; the review's id."""
    monkeypatch.setattr(cli, "_spawn_review_run", spawn)
    cli.cmd_review_submit(project, SimpleNamespace(boundary=None, paths=None))
    return capsys.readouterr().out.split()[0]


class _Exited:
    """A nudged turn that has already ended cleanly, so a live nudge runs to its end."""
    pid = 4242
    returncode = 0

    def poll(self):
        return 0


# ---- where the log is ---------------------------------------------------------------------------

def test_the_log_is_under_ao_home_unless_ao_events_names_another_file(project, monkeypatch):
    monkeypatch.delenv("AO_EVENTS")

    A.emit_event(project["root"], "verification", {"id": "V-1", "profile": None})

    assert A.events_path() == os.path.join(A.HOME, ".ao", "events.jsonl")
    (row,) = A.read_events()
    assert (row["project"], row["kind"]) == (A.project_key(project["root"]), "verification")
    assert row["data"] == {"id": "V-1"}
    assert isinstance(row["at"], float)


# ---- each emitter writes one line ---------------------------------------------------------------

def test_a_recorded_verification_is_one_line_naming_its_id_its_result_and_its_profile(project):
    row = A.record_verification(project["root"], {"id": "V-7", "at": 1, "passed": False, "profile": "quick"})

    (event,) = A.read_events()
    assert row["id"] == "V-7"
    assert (event["project"], event["kind"]) == (A.project_key(project["root"]), "verification")
    assert event["data"] == {"id": "V-7", "passed": False, "profile": "quick"}


def test_a_grant_is_one_line_and_a_refusal_writes_none(project):
    root = project["root"]
    A.record_authority(root, False, ["no review"], "sha256:t", "V-1")
    assert A.read_events() == []

    A.record_authority(root, True, [], "sha256:t", "V-1", "C-9", review="r.md",
                       candidate={"index_tree": "abc123", "digest": "sha256:d"})

    (event,) = A.read_events()
    assert event["kind"] == "authority-granted"
    assert event["data"] == {"token": "C-9", "verification": "V-1", "review": "r.md", "index_tree": "abc123"}


def test_mail_ao_sends_is_one_line_naming_the_message_and_never_quoting_it(project, capsys):
    args = SimpleNamespace(action="send", type="DECISION", topic="events", body="the body stays in the mailbox",
                           mail_class=None)

    assert cli.cmd_mail(project, args) is None
    name = capsys.readouterr().out.strip()

    (event,) = A.read_events()
    assert event["kind"] == "mail-sent"
    assert event["data"] == {"message": name, "kind": "decision", "from": "fable", "to": "kiro"}
    assert "stays in the mailbox" not in open(A.events_path(), encoding="utf-8").read()


def test_a_submitted_review_is_told_when_it_is_submitted_and_when_it_ends_by_its_id(project, monkeypatch, capsys):
    root = project["root"]
    _stage(root)
    rid = _submit(project, monkeypatch, capsys)
    state = cli._review_state(root, rid)

    def review(cfg, args):
        A.record_review(root, "r.md", b"VERDICT: APPROVED\n", {"candidate": {"digest": state["candidate"]}},
                        "APPROVED", reviewer="reviewer")
        return 0

    monkeypatch.setattr(cli, "cmd_review", review)
    assert cli.cmd_review_run(project, rid) == 0

    submitted, finished = A.read_events()
    assert (submitted["kind"], submitted["data"]) == ("review-submitted", {"review": rid, "tree": state["tree"]})
    assert (finished["kind"], finished["data"]) == (
        "review-finished", {"review": rid, "state": "finished", "verdict": "APPROVED", "artefact": "r.md"})


def test_a_submitted_review_whose_candidate_moved_is_told_stale(project, monkeypatch, capsys):
    root = project["root"]
    _stage(root)
    rid = _submit(project, monkeypatch, capsys)
    git = [conftest.GIT, "-c", "user.email=t@t", "-c", "user.name=t"]
    tree = subprocess.run(git + ["rev-parse", "HEAD^{tree}"], cwd=root, capture_output=True, text=True).stdout.strip()
    moved = subprocess.run(git + ["commit-tree", tree, "-p", "HEAD", "-m", "moved"], cwd=root, capture_output=True,
                           text=True, check=True).stdout.strip()
    subprocess.run(git + ["update-ref", "HEAD", moved], cwd=root, check=True)       # HEAD moves; the index does not

    assert cli.cmd_review_run(project, rid) == 2

    assert [(row["kind"], row["data"].get("state")) for row in A.read_events()] == [
        ("review-submitted", None), ("review-finished", "stale")]


def test_a_review_that_cannot_start_is_told_submitted_then_failed(project, monkeypatch, capsys):
    _stage(project["root"])

    def refuse(root, rid):
        raise OSError("no process could be started")

    rid = _submit(project, monkeypatch, capsys, spawn=refuse)

    events = A.read_events()
    assert [(row["kind"], row["data"].get("state")) for row in events] == [
        ("review-submitted", None), ("review-finished", "failed")]
    assert {row["data"]["review"] for row in events} == {rid}


def test_a_nudge_is_one_line_with_its_pid_attempt_and_early_exit_and_a_dry_cycle_tells_nothing(
        project, monkeypatch, tmp_path):
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "s1", "name": "claude"})
    _store(project["root"], cfg)
    world = World(cfg, monkeypatch, tmp_path)
    monkeypatch.setattr(W.subprocess, "Popen", lambda argv, **kw: world.spawned.append(argv) or _Exited())
    monkeypatch.setattr(W.time, "sleep", lambda seconds: None)
    world.board("running", "- [S1] a slice · since: 2026-09-16 09:00")
    world.transcript_age(900)

    world.cycle()
    assert _of("nudge") == []

    world.cycle(dry_run=False)

    (event,) = _of("nudge")
    assert event["data"] == {"pid": 4242, "attempt": 1, "exit": 0}


def test_a_wake_for_reports_is_one_line_naming_why_and_how_many(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    world.transcript_age(900)
    world.mail("20260926-1200-kiro-to-fable-BLOCKED-queue.md", "# queue empty\n\n## DECISION REQUIRED\n")

    world.cycle(dry_run=False)

    (event,) = _of("wake")
    assert event["data"]["why"] == "reports" and event["data"]["pid"] == 99999
    assert event["data"]["reports"] >= 1


def test_a_wake_to_refill_the_queue_is_one_line_naming_its_depth(project, monkeypatch, tmp_path):
    world = World(project, monkeypatch, tmp_path)
    world.transcript_age(900)

    world.cycle(dry_run=False)

    (event,) = _of("wake")
    assert event["data"] == {"why": "refill", "queue": 0, "pid": 99999}


# ---- the reader -------------------------------------------------------------------------------

def test_ao_events_shows_one_projects_events_since_a_time_and_the_newest_n(project, capsys):
    _write({"at": 1000.0, "project": "alpha", "kind": "verification", "data": {"id": "V-1", "passed": True}},
           {"at": 3000.0, "project": "beta", "kind": "mail-sent", "data": {"message": "m.md"}},
           {"at": 3500.0, "project": "Alpha", "kind": "wake", "data": {"why": "refill", "pid": 7}},
           {"at": 4000.0, "project": "alpha", "kind": "nudge", "data": {"pid": 8, "attempt": 1}})
    since = A.parse_time("1970-01-01T00:40:00+00:00")                   # 2400 seconds past the epoch

    assert cli.cmd_events(project, _args(project="alpha", since=since, json=True)) == 0
    assert [json.loads(line)["kind"] for line in capsys.readouterr().out.splitlines()] == ["wake", "nudge"]

    assert cli.cmd_events(project, _args(n=1)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "3 earlier event(s) not shown; -n 0 shows every one" and "nudge" in out[1] and len(out) == 2

    assert cli.cmd_events(project, _args(n=0)) == 0
    assert len(capsys.readouterr().out.splitlines()) == 4

    assert cli.cmd_events(project, _args(project="gamma")) == 0
    assert capsys.readouterr().out.startswith("no events of gamma in ")


def test_a_person_reads_the_time_project_kind_and_name_value_pairs_and_nothing_a_terminal_acts_on(project, capsys):
    _write({"at": 1000.0, "project": "alpha", "kind": "mail-sent", "data": {"message": "a b.md", "to": "kiro"}},
           {"at": "later", "project": "x\x1b[2J", "kind": "k", "note": "made by another program"})

    assert cli.cmd_events(project, _args()) == 0

    first, second = capsys.readouterr().out.splitlines()
    assert first.split("  ", 1)[1].split() == ["alpha", "mail-sent", 'message="a', 'b.md"', "to=kiro"]
    assert "\x1b" not in second and "\\u001b" in second and second.startswith("????-??-?? ??:??:??")
    assert second.endswith('note="made by another program"')


def test_json_output_is_each_line_as_the_log_holds_it(project, capsys):
    A.emit_event(project["root"], "verification", {"id": "V-1", "passed": True})
    A.emit_event(project["root"], "nudge", {"pid": 1, "attempt": 1})
    with open(A.events_path(), encoding="utf-8") as fh:
        held = fh.read()

    assert cli.cmd_events(project, _args(json=True)) == 0
    assert capsys.readouterr().out == held


def test_follow_prints_each_new_event_of_its_project_as_it_is_written_until_ctrl_c(project, monkeypatch, capsys):
    root = project["root"]
    A.emit_event(root, "verification", {"id": "V-1"})
    waits = []

    def wait(seconds):
        waits.append(seconds)
        if len(waits) > 1:
            raise KeyboardInterrupt
        A.emit_event(root, "mail-sent", {"message": "m.md"})
        _write({"at": 1.0, "project": "other", "kind": "wake", "data": {}})

    monkeypatch.setattr(cli, "_events_wait", wait)

    assert cli.cmd_events(project, _args(follow=True, project=A.project_key(root), json=True)) == 0

    out = capsys.readouterr()
    assert [json.loads(line)["kind"] for line in out.out.splitlines()] == ["verification", "mail-sent"]
    assert waits == [cli.EVENTS_FOLLOW_SECONDS] * 2 and "following " in out.err


def test_a_follower_reads_on_across_the_bounds_trim_without_repeating_or_missing_a_line(project):
    path = A.events_path()
    tail = A.EventTail(path)
    _write(*({"at": float(n), "project": "p", "kind": "k", "data": {"n": n, "pad": "x" * 200}} for n in range(40)))
    assert [row["data"]["n"] for row in tail.poll()] == list(range(40))

    assert A.bound_store(path, 4)                       # the file is replaced by its newest lines
    _write(*({"at": float(n), "project": "p", "kind": "k", "data": {"n": n}} for n in (40, 41)))

    assert [row["data"]["n"] for row in tail.poll()] == [40, 41]
    assert not tail.missed and tail.poll() == []


def test_a_follower_a_trim_overtook_goes_on_from_the_top_and_knows_events_may_be_missing(project):
    path = A.events_path()
    tail = A.EventTail(path)
    _write({"at": 0.0, "project": "p", "kind": "k", "data": {"n": 0}})
    assert len(tail.poll()) == 1
    _write(*({"at": float(n), "project": "p", "kind": "k", "data": {"n": n, "pad": "x" * 200}} for n in range(1, 61)))

    assert A.bound_store(path, 4)
    rows = tail.poll()

    assert tail.missed
    assert rows[0]["data"]["n"] > 1 and rows[-1]["data"]["n"] == 60
    assert [row["data"]["n"] for row in rows] == list(range(rows[0]["data"]["n"], 61))


def test_a_line_left_unfinished_spoils_only_itself_and_is_counted_not_shown(project, capsys):
    path = A.events_path()
    tail = A.EventTail(path)
    with open(path, "ab") as fh:
        fh.write(b'{"at":1,"project":"p","ki')                    # a writer killed mid-line
    assert tail.poll() == []                                        # a line still being written waits

    A.emit_event(project["root"], "verification", {"id": "V-2"})

    assert [row["data"]["id"] for row in tail.poll()] == ["V-2"] and tail.skipped == 1
    assert cli.cmd_events(project, _args()) == 0
    out = capsys.readouterr().out.splitlines()
    assert "id=V-2" in out[0] and out[1] == f"1 line(s) of {path} hold no event and were passed over"


# ---- the bound, and the work standing without its event ------------------------------------------

def test_the_log_is_held_to_its_bound_as_it_is_written(project):
    with open(os.environ["AO_SETTINGS"], "w", encoding="utf-8") as fh:
        json.dump({"retention": {"events_kb": 64}}, fh)

    largest = 0
    for n in range(700):
        A.emit_event(project["root"], "mail-sent", {"message": f"{n:04d}-" + "x" * 120})
        largest = max(largest, os.path.getsize(A.events_path()))

    tail = A.EventTail()
    rows = tail.poll()
    assert largest <= 64 * 1024 * 1.25                    # never past it once an event is written, not only at the end
    assert rows[-1]["data"]["message"].startswith("0699-") and not rows[0]["data"]["message"].startswith("0000-")
    assert tail.skipped == 0


def test_an_event_that_cannot_be_written_is_said_on_stderr_and_the_work_stands(project, monkeypatch, capsys,
                                                                                tmp_path):
    blocked = tmp_path / "a-file-not-a-directory"
    blocked.write_text("", encoding="utf-8")
    monkeypatch.setenv("AO_EVENTS", str(blocked / "events.jsonl"))

    row = A.record_verification(project["root"], {"id": "V-3", "at": 1, "passed": True})

    assert row["id"] == "V-3" and A.latest_verification(project["root"])["id"] == "V-3"
    out = capsys.readouterr()
    assert out.out == "" and f"the verification event was not written to {blocked / 'events.jsonl'}" in out.err


def test_a_bound_that_cannot_be_held_is_said_and_the_event_still_stands(project, monkeypatch, capsys):
    def full(path, kb):
        raise OSError("the disk is full")

    monkeypatch.setattr(A, "bound_store", full)

    row = A.emit_event(project["root"], "nudge", {"pid": 1})

    assert row is not None and A.read_events() == [row]
    assert f"ao: {A.events_path()} was not held to its bound: OSError: the disk is full" in capsys.readouterr().err


def test_a_credential_in_a_payload_is_masked_before_it_is_written(project):
    token = "sk-ant-" + "a1b2c3d4" * 3

    A.emit_event(project["root"], "mail-sent", {"message": f"note-{token}.md"})

    with open(A.events_path(), encoding="utf-8") as fh:
        held = fh.read()
    assert token not in held and "note-[redacted:anthropic-key].md" in held


def test_ao_events_takes_follow_project_since_n_and_json():
    words = ["events", "-f", "--project", "acme-api", "--since", "2h", "-n", "5", "--json"]

    args = cli.build_parser().parse_args(words)

    assert (args.follow, args.project, args.n, args.json, args.fn) == (True, "acme-api", 5, True, cli.cmd_events)
    assert args.since.seconds == 7200
    assert cli.build_parser().parse_args(["events"]).n == 20
