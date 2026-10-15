"""A slice is started, blocked and abandoned by command, and each move ao makes is a ledger line (SLICE-LIFECYCLE).

A slice's state was the section of `.ao/board.md` its row sat in and nothing more. The playbook had the
implementer move rows by editing the file, so nothing said when a slice started, who parked it or why,
and a slice deleted from the board left no trace at all - docs/slices.md called abandoning one an
outcome to record and ao recorded nothing. `ao slice start|block|abandon` move a row as the board has
always been moved, and each move ao makes - those commands, `ao board add`, `ao source import` and the
watchdog's quota park - is first a line in the chained `.ao/ledger/slices.jsonl`: which slice, from
which section to which, by whom, when and why. Who may make which move follows docs/roles.md: the
implementer starts a READY item and resumes a block it made, anyone blocks, and dropping a slice or
pointing it at a boundary file is the architect's or a person's. `ao slice status` and `ao slices`
read the board and the ledger together and name a row moved or removed by hand.

The review of that round found three holes (SLICE-LIFECYCLE-2). `ao slice start` judged an item READY
on one reading of the board and moved it on a later one, so an implementer that wrote `waiting:
architect` in between had ao record an authorized-looking start of a slice that was not READY; the
watchdog's park moved a row from wherever a hand had put it while it decided. A move is now decided
and made on one reading, which ao's other writers wait for and which is compared with the board as it
stands before it is replaced: a board that changed meanwhile moves nothing, and says to run it again.
A board write that failed after its line left the ledger holding a move that never happened, and a
retry recorded a second one: a line now voids it, and readers leave both out. And `ao slice status`
said a slice reviewed before the review ledger had no review at all, though its rounds counted them.
"""
import errno
import json
import os
import subprocess
import time

import pytest

from ao import allowlist as AL, cli, lib as A, storage
from ao import watchdog as W
from tests.test_quota_park import LIMIT, PROMPT, SLICE, _harness, _nudges_run, _say

REVIEWS = "semantic-review"
BOARD = ("# Board\n\n## running\n\n## blocked\n\n## queued\n"
         "- [S1] the store · acceptance: the store keeps every row it is given\n"
         "- [S2] the export · acceptance: an export holds every row · needs: S1\n\n"
         "## inbox\n\n## verified\n\n## done\n")


def _board(root, text=BOARD):
    with open(os.path.join(root, ".ao", "board.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


def _slice(cfg, monkeypatch, role, *argv):
    """`ao slice …` as a turn ao started for `role` runs it, or as a person when `role` is None."""
    if role:
        monkeypatch.setenv("AO_ROLE", role)
    else:
        monkeypatch.delenv("AO_ROLE", raising=False)
    words = list(argv) if argv[0] == "slices" else ["slice", *argv]
    args = cli.build_parser().parse_args(words)
    return args.fn(cfg, args)


def _where(root, iid):
    return next(((state, item) for state, items in A.board(root).items() for item in items if item["id"] == iid),
                (None, None))


def _moves(root, iid=None):
    return [row for row in A.slice_moves(root) if iid is None or row["slice"] == iid]


def _commit(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    subprocess.run(["git", "add", rel], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", rel], cwd=root,
                   check=True)


def _review(root, verdict, slice_id, findings=(), n=[0]):
    n[0] += 1
    name = f"2026-10-12-{n[0]:06d}-r.md"
    counts = {key: sum(1 for severity, _ in findings if severity == key)
              for key in ("BLOCKER", "HIGH", "MEDIUM", "LOW")}
    evidence = {"schema": 2, "kind": "index-candidate", "authorizable": True, "slice": slice_id,
                "candidate": {"digest": f"sha256:c{n[0]}"}, "verdict": verdict, "counts": counts}
    listed = "".join(f"    - [{severity}] {text}\n" for severity, text in findings)
    body = f"# review\n\nVERDICT: {verdict}\n\n{A.review_evidence_line(evidence)}\n\n## Reviewer output\n\n{listed}"
    A.write_review_artefact(root, REVIEWS, name, body, evidence=evidence, verdict=verdict, reviewer="r1")
    return name


# ---- start ----------------------------------------------------------------------------------------------

def test_the_implementer_starts_a_ready_item_and_the_move_is_recorded_before_anyone_asks(project, monkeypatch, capsys):
    root = project["root"]
    _board(root)

    assert _slice(project, monkeypatch, "implementer", "start", "S1") == 0

    state, item = _where(root, "S1")
    assert state == "running" and item["notes"]["since"] and item["notes"]["acceptance"]
    (row,) = _moves(root)
    assert (row["slice"], row["from"], row["to"], row["by"], row["why"]) == ("S1", "queued", "running", "implementer",
                                                                            None)
    assert row["ordinal"] == 1 and row["previous"] is None and isinstance(row["at"], int)
    assert "queued → running" in capsys.readouterr().out

    # Started again, it is where it was: nothing moves and nothing is recorded.
    assert _slice(project, monkeypatch, "implementer", "start", "S1") == 0
    assert "running already; nothing moved" in capsys.readouterr().out and len(_moves(root)) == 1


def test_an_implementer_is_refused_an_item_that_is_not_ready_and_the_architect_starts_it_on_the_record(
        project, monkeypatch, capsys):
    root = project["root"]
    _board(root)
    before = open(os.path.join(root, ".ao", "board.md"), encoding="utf-8").read()

    assert _slice(project, monkeypatch, "implementer", "start", "S2") == 2
    assert "S2 needs S1, which has not landed" in capsys.readouterr().out
    assert open(os.path.join(root, ".ao", "board.md"), encoding="utf-8").read() == before
    assert not os.path.exists(A.slice_ledger_path(root))

    assert _slice(project, monkeypatch, "architect", "start", "S2") == 0
    (row,) = _moves(root, "S2")
    assert row["by"] == "architect" and row["why"] == "started while not READY: S2 needs S1, which has not landed"


def test_an_item_that_ended_or_was_never_admitted_does_not_start(project, monkeypatch, capsys):
    root = project["root"]
    _board(root, BOARD.replace("## inbox\n", "## inbox\n- [S3] a pull · needs: no acceptance boundary written\n")
           .replace("## done\n", "## done\n- [S0] the schema\n"))

    for iid, said in (("S3", "held without an acceptance boundary"), ("S0", "a slice that ended does not start"),
                      ("S9", "S9 is not on the board")):
        assert _slice(project, monkeypatch, None, "start", iid) == 2
        assert said in capsys.readouterr().out
    assert not os.path.exists(A.slice_ledger_path(root))


# ---- block, and the block lifted -------------------------------------------------------------------------

def test_a_block_parks_the_running_slice_and_its_resume_gives_back_the_needs_it_carried(project, monkeypatch, capsys):
    root = project["root"]
    _board(root, BOARD.replace("## running\n",
                               "## running\n- [S5] the import · since: 2026-10-12 09:00 · needs: S4\n"))

    assert _slice(project, monkeypatch, "implementer", "block", "the vendor's signing key", "--waiting", "human") == 0
    state, item = _where(root, "S5")
    assert state == "blocked"
    assert item["notes"] == {"since": "2026-10-12 09:00", "needs": "the vendor's signing key", A.HELD_NEEDS: "S4",
                             "waiting": "human"}
    assert [entry["id"] for entry in A.human_waits(root)] == ["S5"]

    assert _slice(project, monkeypatch, "implementer", "start", "S5") == 0
    state, item = _where(root, "S5")
    assert state == "running" and item["notes"] == {"since": "2026-10-12 09:00", "needs": "S4"}
    block, resume = _moves(root, "S5")
    assert (block["from"], block["to"], block["by"], block["why"]) == ("running", "blocked", "implementer",
                                                                      "the vendor's signing key")
    assert block["replaced"] == {"needs": "S4"}
    assert (resume["from"], resume["to"], resume["by"]) == ("blocked", "running", "implementer")
    assert resume["replaced"] == {"needs": "the vendor's signing key", A.HELD_NEEDS: "S4", "waiting": "human"}


def test_a_block_someone_else_made_is_theirs_to_lift(project, monkeypatch, capsys):
    root = project["root"]
    _board(root, BOARD.replace("## running\n", "## running\n- [S5] the import\n- [S6] the audit\n"))

    assert _slice(project, monkeypatch, "architect", "block", "a decision on the format") == 2
    assert "2 slices are running (S5, S6): name one with --slice" in capsys.readouterr().out
    assert _slice(project, monkeypatch, "architect", "block", "a decision on the format", "--slice", "S5") == 0
    assert _slice(project, monkeypatch, "implementer", "start", "S5") == 2
    assert "S5 was blocked by architect" in capsys.readouterr().out

    A.board_move(root, "S6", "blocked", {"needs": "quota (resets 14:00)"}, by="watchdog", why="usage limit")
    assert _slice(project, monkeypatch, "implementer", "start", "S6") == 2
    assert "parked on quota by the watchdog" in capsys.readouterr().out

    assert _slice(project, monkeypatch, "architect", "start", "S5") == 0
    assert _where(root, "S5")[0] == "running" and _where(root, "S6")[0] == "blocked"
    assert [(row["slice"], row["to"], row["by"]) for row in _moves(root)] == [
        ("S5", "blocked", "architect"), ("S6", "blocked", "watchdog"), ("S5", "running", "architect")]


def test_a_reason_is_required_and_holds_no_separator(project, monkeypatch, capsys):
    root = project["root"]
    _board(root, BOARD.replace("## running\n", "## running\n- [S5] the import\n"))

    assert _slice(project, monkeypatch, None, "block") == 2
    assert _slice(project, monkeypatch, None, "block", "one · two") == 2
    assert _slice(project, monkeypatch, None, "block", "x", "--waiting", "a person") == 2
    assert _where(root, "S5")[0] == "running" and not os.path.exists(A.slice_ledger_path(root))


# ---- abandon -----------------------------------------------------------------------------------------------

def test_abandoning_is_recorded_under_rejected_with_its_reason_and_is_never_the_implementers(
        project, monkeypatch, capsys):
    root = project["root"]
    _board(root)

    assert _slice(project, monkeypatch, "implementer", "abandon", "--slice", "S2", "--why", "superseded") == 2
    assert "the architect's decision or a person's" in capsys.readouterr().out
    assert _slice(project, monkeypatch, None, "abandon", "--slice", "S2") == 2
    assert "--why is required" in capsys.readouterr().out
    assert _where(root, "S2")[0] == "queued" and not os.path.exists(A.slice_ledger_path(root))

    assert _slice(project, monkeypatch, None, "abandon", "S2", "--why", "superseded by the metadata authority") == 0
    state, item = _where(root, "S2")
    assert state == "rejected" and item["notes"]["abandoned"] == "superseded by the metadata authority"
    (row,) = _moves(root)
    assert (row["from"], row["to"], row["by"], row["why"]) == ("queued", "rejected", "person",
                                                              "superseded by the metadata authority")


def test_a_landed_slice_is_not_abandoned(project, monkeypatch, capsys):
    root = project["root"]
    _board(root, BOARD.replace("## done\n", "## done\n- [S0] the schema\n"))

    assert _slice(project, monkeypatch, "architect", "abandon", "--slice", "S0", "--why", "wrong") == 2
    assert "a slice that landed is not abandoned" in capsys.readouterr().out


# ---- a boundary file --------------------------------------------------------------------------------------

def test_a_boundary_file_must_be_committed_and_takes_the_sentences_place_which_the_ledger_keeps(
        project, monkeypatch, capsys):
    root = project["root"]
    _board(root)
    os.makedirs(os.path.join(root, "docs", "slices"))
    with open(os.path.join(root, "docs", "slices", "S1.md"), "w", encoding="utf-8") as fh:
        fh.write("# S1\n## Invariant\nNo row is lost.\n")

    assert _slice(project, monkeypatch, "architect", "start", "S1", "--boundary-file", "docs/slices/S1.md") == 2
    assert "is not committed: commit it first" in capsys.readouterr().out
    assert _slice(project, monkeypatch, "architect", "start", "S1", "--boundary-file", "../outside.md") == 2
    _commit(root, "docs/slices/S1.md", "# S1\n## Invariant\nNo row is lost.\n")
    assert _slice(project, monkeypatch, "implementer", "start", "S1", "--boundary-file", "docs/slices/S1.md") == 2
    assert "a boundary is the architect's to write" in capsys.readouterr().out

    assert _slice(project, monkeypatch, "architect", "start", "S1", "--boundary-file", "docs/slices/S1.md") == 0
    state, item = _where(root, "S1")
    head = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=root, capture_output=True, text=True).stdout
    assert state == "running" and item["notes"]["boundary"] == f"docs/slices/S1.md@{head.strip()}"
    assert "acceptance" not in item["notes"]
    assert A.read_boundary(root, item)["problem"] is None
    (row,) = _moves(root)
    assert row["replaced"] == {"acceptance": "the store keeps every row it is given"}
    assert row["notes"]["acceptance"] is None and row["notes"]["boundary"] == item["notes"]["boundary"]


# ---- what ao already did to the board is recorded too ------------------------------------------------------

def test_an_item_admitted_with_board_add_enters_the_ledger_with_its_boundary(project, monkeypatch):
    root = project["root"]
    _board(root)
    monkeypatch.delenv("AO_ROLE", raising=False)
    args = cli.build_parser().parse_args(["board", "add", "S7", "the report", "--acceptance", "a report per day"])

    assert cli.cmd_board(project, args) == 0
    (row,) = _moves(root)
    assert (row["slice"], row["from"], row["to"], row["by"]) == ("S7", None, "queued", "person")
    assert row["title"] == "the report" and row["notes"] == {"acceptance": "a report per day"}


def test_items_a_source_import_admits_or_holds_enter_the_ledger(project, monkeypatch):
    root = project["root"]
    _board(root)
    with open(os.path.join(root, ".ao", "sources.json"), "w", encoding="utf-8") as fh:
        json.dump({"bound_root": root, "sources": [{"id": "tracker", "kind": "mcp", "server": "linear"}]}, fh)
    os.makedirs(os.path.join(root, ".ao", "inbox"))
    with open(os.path.join(root, ".ao", "inbox", "tracker.json"), "w", encoding="utf-8") as fh:
        json.dump({"bound_root": root, "source": "tracker", "items": [
            {"id": "T1", "title": "a ticket", "acceptance": "it is fixed"}, {"id": "T2", "title": "another"}]}, fh)
    monkeypatch.delenv("AO_ROLE", raising=False)

    assert cli.cmd_source(project, cli.build_parser().parse_args(["source", "import"])) == 0
    admitted, held = _moves(root)
    assert (admitted["slice"], admitted["from"], admitted["to"], admitted["by"]) == ("T1", None, "queued", "person")
    assert admitted["notes"] == {"source": "tracker", "acceptance": "it is fixed"}
    assert (held["slice"], held["to"], held["notes"]["needs"]) == ("T2", "inbox", "no acceptance boundary written")


def test_the_watchdogs_quota_park_and_its_end_are_each_a_line_by_the_watchdog(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)
    world.cycle(dry_run=False)
    state = W.load_state(world.root)
    state["quota_park"]["until"] = time.time() - 1                       # the reset has passed
    W.save_state(world.root, state)
    world.cycle(dry_run=False)

    park, resume = _moves(world.root, "B8")
    assert (park["from"], park["to"], park["by"], park["why"]) == ("running", "blocked", "watchdog",
                                                                  "the implementer stopped on its usage limit")
    assert park["notes"]["needs"].startswith("quota (resets ")
    assert (resume["from"], resume["to"], resume["by"]) == ("blocked", "running", "watchdog")
    assert resume["why"].startswith("the usage limit reset ")


def test_a_ledger_that_cannot_take_the_line_leaves_the_board_as_it_was(project, monkeypatch):
    root = project["root"]
    _board(root)
    with open(A.slice_ledger_path(root), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"slice": "S1", "from": "queued", "to": "running", "previous": "sha256:forged",
                             "ordinal": 1}) + "\n")
    before = open(os.path.join(root, ".ao", "board.md"), encoding="utf-8").read()

    with pytest.raises(storage.LedgerCorruption):
        _slice(project, monkeypatch, "implementer", "start", "S1")
    with pytest.raises(storage.LedgerCorruption):
        A.board_move(root, "S1", "blocked", {"needs": "x"}, by="watchdog")
    assert open(os.path.join(root, ".ao", "board.md"), encoding="utf-8").read() == before
    # A move within a section is no transition, and writes no line.
    assert A.board_move(root, "S1", "queued", {"role": "implementer"}) is True


# ---- a move is decided and made on one reading of the board (SLICE-LIFECYCLE-2) ----------------------------

def _edited_by_hand(root, old, new):
    path = os.path.join(root, ".ao", "board.md")
    text = open(path, encoding="utf-8").read()
    assert old in text
    _board(root, text.replace(old, new))


def test_a_hand_edit_between_aos_reading_and_its_write_moves_nothing_and_says_to_run_it_again(
        project, monkeypatch, capsys):
    root = project["root"]
    _board(root)
    judged = A.lane_not_ready
    row = "- [S1] the store · acceptance: the store keeps every row it is given\n"

    def judged_then_parked_by_hand(*args):
        ready = judged(*args)                       # READY on what ao read, and then the implementer parks it
        _edited_by_hand(root, row, row.replace("\n", " · waiting: architect\n"))
        return ready

    monkeypatch.setattr(A, "lane_not_ready", judged_then_parked_by_hand)
    monkeypatch.setenv("AO_ROLE", "implementer")

    assert cli.main(["-C", root, "slice", "start", "S1"]) == 1
    said = capsys.readouterr().err
    assert "the board changed while ao read it" in said and "run it again" in said
    state, item = _where(root, "S1")
    assert state == "queued" and item["notes"]["waiting"] == "architect"         # the hand edit stands
    assert not os.path.exists(A.slice_ledger_path(root))                          # and nothing was recorded

    # Run again, it is judged on the board as it stands: S1 is not READY, and the implementer is refused.
    monkeypatch.setattr(A, "lane_not_ready", judged)
    assert cli.main(["-C", root, "slice", "start", "S1"]) == 2
    assert "S1 is waiting on architect" in capsys.readouterr().out


def test_a_hand_edit_landing_while_ao_writes_the_board_voids_the_move_it_recorded(project, monkeypatch, capsys):
    root = project["root"]
    _board(root)
    recorded = A.record_slice_move

    def recorded_then_landed_by_hand(*args, **kwargs):
        line = recorded(*args, **kwargs)            # the move is on the record, the board not yet replaced
        _edited_by_hand(root, "## done\n", "## done\n- [S0] the schema\n")
        return line

    monkeypatch.setattr(A, "record_slice_move", recorded_then_landed_by_hand)
    monkeypatch.setenv("AO_ROLE", "implementer")

    assert cli.main(["-C", root, "slice", "start", "S1"]) == 1
    assert "the slice ledger voids the move it had recorded; run it again" in capsys.readouterr().err
    assert _where(root, "S1")[0] == "queued" and _where(root, "S0")[0] == "done"
    move, void = storage.read_chained_jsonl(A.slice_ledger_path(root), A.SLICE_CHAIN)
    assert (move["slice"], move["from"], move["to"]) == ("S1", "queued", "running")
    assert (void["slice"], void["voids"], void["by"]) == ("S1", move["ordinal"], "implementer")
    assert void["why"] == "the board was edited after ao read it and before ao wrote it; the edit stands"
    assert A.slice_moves(root) == []


def test_the_watchdog_moves_nothing_on_a_board_a_hand_changed_while_it_decided_the_park(
        project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)
    decided = W.quota_park_step

    def decided_then_landed_by_hand(*args, **kwargs):
        step = decided(*args, **kwargs)             # the park is decided on what the watchdog read ...
        _edited_by_hand(world.root, SLICE + "\n", "")
        _edited_by_hand(world.root, "## done\n", f"## done\n{SLICE}\n")     # ... and a person lands B8 meanwhile
        return step

    monkeypatch.setattr(W, "quota_park_step", decided_then_landed_by_hand)
    trace = world.cycle(dry_run=False)

    assert any(line.startswith("the board changed while the watchdog read it: nothing moved") for line in trace)
    board = A.board(world.root)
    assert [item["id"] for item in board["done"]] == ["B8"] and not board["blocked"]
    assert not os.path.exists(A.slice_ledger_path(world.root)) and "quota_park" not in W.load_state(world.root)

    monkeypatch.setattr(W, "quota_park_step", decided)
    world.cycle(dry_run=False)                       # decided again on the board as it stands: nothing runs
    assert not A.board(world.root)["blocked"] and not os.path.exists(A.slice_ledger_path(world.root))


def test_a_board_that_cannot_be_written_leaves_a_line_voiding_the_move_and_a_retry_records_it_once(
        project, monkeypatch, capsys):
    root = project["root"]
    _board(root)
    path = os.path.join(root, ".ao", "board.md")
    before = open(path, encoding="utf-8").read()
    rename = storage._replace

    def refused(source, target):
        if os.path.basename(target) == "board.md":
            raise PermissionError(errno.EACCES, "Permission denied", target)
        return rename(source, target)

    monkeypatch.setattr(storage, "_replace", refused)
    with pytest.raises(PermissionError):
        _slice(project, monkeypatch, "implementer", "start", "S1")

    assert open(path, encoding="utf-8").read() == before
    move, void = storage.read_chained_jsonl(A.slice_ledger_path(root), A.SLICE_CHAIN)
    assert (move["slice"], move["from"], move["to"]) == ("S1", "queued", "running")
    assert (void["slice"], void["from"], void["to"], void["voids"]) == ("S1", "queued", "running", move["ordinal"])
    assert void["why"] == "the board could not be written: PermissionError: Permission denied"
    assert A.slice_moves(root) == []                           # no move the board did not make
    capsys.readouterr()
    assert _slice(project, monkeypatch, None, "status", "S1") == 0
    out = capsys.readouterr().out
    assert "state     queued" in out and "ao last recorded it" not in out and "never took" not in out
    assert "queued → running" in out and "did not happen: the board could not be written: PermissionError" in out
    assert _slice(project, monkeypatch, None, "slices") == 0
    assert "ao last recorded it" not in capsys.readouterr().out

    monkeypatch.setattr(storage, "_replace", rename)
    assert _slice(project, monkeypatch, "implementer", "start", "S1") == 0
    (moved,) = A.slice_moves(root)                              # the retry is recorded once
    assert (moved["from"], moved["to"], moved["ordinal"]) == ("queued", "running", 3)
    assert _where(root, "S1")[0] == "running"


def test_a_move_ao_recorded_that_the_board_never_took_is_named_as_such(project, monkeypatch, capsys):
    """A process stopped between the line and the board leaves no line voiding the move; status says what it may be."""
    root = project["root"]
    _board(root)
    A.record_slice_move(root, "S1", "queued", "running", by="implementer", title="the store")

    assert _slice(project, monkeypatch, None, "status", "S1") == 0
    out = capsys.readouterr().out
    assert "the board never took ao's last recorded move of it, queued → running" in out
    assert "ao stopped between recording it and writing the board, or a hand moved it back" in out


# ---- reading where slices stand ----------------------------------------------------------------------------

def test_status_says_since_when_how_it_got_there_its_rounds_and_its_newest_reviews_findings(
        project, monkeypatch, capsys):
    root = project["root"]
    _board(root)
    _slice(project, monkeypatch, "implementer", "start", "S1")
    _review(root, "NEEDS_CHANGES", "S1", [("HIGH", "src/store.py:12 - a row past the first page is dropped")])
    _review(root, "NEEDS_CHANGES", "S1", [("HIGH", "src/store.py:30 - the cursor skips a page"),
                                          ("LOW", "src/store.py:2 - an unused import")])
    _review(root, "APPROVED", "S2")
    capsys.readouterr()

    assert _slice(project, monkeypatch, None, "status") == 0
    out = capsys.readouterr().out
    assert "S1  the store" in out and "running since " in out and "moved by implementer" in out
    assert "rounds    2 of 5" in out
    assert "NEEDS_CHANGES" in out and "BLOCKER 0 · HIGH 1 · MEDIUM 0 · LOW 1" in out
    assert "- [HIGH] src/store.py:30 - the cursor skips a page" in out
    assert "a row past the first page" not in out                  # an older review's finding is not open
    assert "queued → running" in out and "by implementer" in out

    # A review file that is not the bytes the ledger recorded says so instead of its findings.
    newest = sorted(os.listdir(os.path.join(root, REVIEWS)))[-2]
    with open(os.path.join(root, REVIEWS, newest), "a", encoding="utf-8") as fh:
        fh.write("    - [BLOCKER] added later\n")
    assert _slice(project, monkeypatch, None, "status", "S1") == 0
    out = capsys.readouterr().out
    assert "not the bytes the review ledger recorded" in out and "added later" not in out


def test_a_review_from_before_the_review_ledger_is_the_newest_review_its_slice_has(project, monkeypatch, capsys):
    """`rounds` read review files with no ledger row as it always had, and status said such a slice had no review."""
    root = project["root"]
    _board(root)
    evidence = {"schema": 2, "kind": "index-candidate", "authorizable": True, "slice": "S1",
                "candidate": {"digest": "sha256:before"}, "verdict": "NEEDS_CHANGES",
                "counts": {"BLOCKER": 0, "HIGH": 1, "MEDIUM": 0, "LOW": 0}}
    with open(os.path.join(root, REVIEWS, "2026-09-01-120000-before.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# review\n\nVERDICT: NEEDS_CHANGES\n\n{A.review_evidence_line(evidence)}\n\n## Reviewer output\n\n"
                 "    - [HIGH] src/store.py:12 - a row past the first page is dropped\n")

    assert _slice(project, monkeypatch, None, "status", "S1") == 0
    out = capsys.readouterr().out
    assert "rounds    1 of 5" in out and "none that reached a verdict" not in out
    assert "NEEDS_CHANGES" in out and "BLOCKER 0 · HIGH 1 · MEDIUM 0 · LOW 0" in out
    assert "- [HIGH] src/store.py:12 - a row past the first page is dropped" in out
    assert "no row in the review ledger: a review from before it, read from its file as it stands" in out

    _review(root, "APPROVED", "S1")                 # one the ledger records is newer, and the ledger's word holds
    assert _slice(project, monkeypatch, None, "status", "S1") == 0
    out = capsys.readouterr().out
    assert "APPROVED" in out and "no row in the review ledger" not in out and "src/store.py:12" not in out


def test_a_row_moved_or_removed_by_hand_is_named_by_status_and_slices(project, monkeypatch, capsys):
    root = project["root"]
    _board(root)
    _slice(project, monkeypatch, "implementer", "start", "S1")
    path = os.path.join(root, ".ao", "board.md")
    text = open(path, encoding="utf-8").read()
    line = next(row for row in text.splitlines() if row.startswith("- [S1]"))
    _board(root, text.replace(line + "\n", "").replace("## done\n", f"## done\n{line}\n"))
    capsys.readouterr()

    assert _slice(project, monkeypatch, None, "status", "S1") == 0
    assert "moved by hand: ao last recorded it under running" in capsys.readouterr().out
    assert _slice(project, monkeypatch, None, "slices") == 0
    out = capsys.readouterr().out
    assert "S1" in out and "moved by hand: ao last recorded it under running" in out

    _board(root, open(path, encoding="utf-8").read().replace(line + "\n", ""))
    assert _slice(project, monkeypatch, None, "slices", "--open") == 0
    gone = next(row for row in capsys.readouterr().out.splitlines() if "S1" in row)
    assert "the store" in gone and "not on the board: ao last recorded it under running" in gone
    assert _slice(project, monkeypatch, None, "status", "S1") == 0
    assert "not on the board" in capsys.readouterr().out
    assert _slice(project, monkeypatch, None, "status", "S9") == 1


def test_slices_lists_every_slice_by_state_and_open_leaves_out_the_ended(project, monkeypatch, capsys):
    root = project["root"]
    _board(root, BOARD.replace("## done\n", "## done\n- [S0] the schema\n"))
    _slice(project, monkeypatch, None, "abandon", "--slice", "S2", "--why", "superseded")
    capsys.readouterr()

    assert _slice(project, monkeypatch, None, "slices") == 0
    every = capsys.readouterr().out
    assert all(iid in every for iid in ("S0", "S1", "S2")) and "superseded" in every
    assert _slice(project, monkeypatch, None, "slices", "--open") == 0
    opened = capsys.readouterr().out
    assert "S1" in opened and "S0" not in opened and "S2" not in opened


# ---- grants ----------------------------------------------------------------------------------------------------

def test_no_implementer_grant_admits_abandoning_and_the_shipped_grants_admit_what_each_role_runs():
    named = [reason for reason, _, _ in AL.problems(["claude", "--allowedTools", "Bash(ao slice:*)"],
                                                    role="implementer")]
    assert "abandons a slice, which the architect decides" in named
    assert AL.problems(["claude", "--allowedTools", "Bash(ao slice:*)"], role="architect") == []

    implementer = AL.rules(A.role_commands(A.load_adapter("claude-code"))["implementer"])
    architect = AL.rules(cli._architect_argv("claude-code"))
    for command in ("ao slice start S1", 'ao slice block "x" --waiting human', "ao slice status", "ao slices --open"):
        assert any(AL.admits(rule, command) for rule in implementer), command
    assert not any(AL.admits(rule, "ao slice abandon --why w") for rule in implementer)
    for command in ("ao slice abandon --slice S1 --why w", "ao slice start S1 --boundary-file f", "ao slices"):
        assert any(AL.admits(rule, command) for rule in architect), command
