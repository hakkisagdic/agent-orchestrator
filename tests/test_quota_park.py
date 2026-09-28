"""A usage limit that stops the implementer mid-slice parks its slice until the limit resets (QUOTA-PARK).

The watchdog read a usage limit only where an architect's wake had failed on one. An implementer
that stopped on its own limit read as an agent ignoring its nudges: each nudge died on the same
limit until the backoff ran out and a person was told it was stuck. These drive the decision with
canned transcripts, nudges that die on the limit and a temporary board.

A stop is read in the words the implementer's own adapter declares, and its reset from what the
harness records beside them or within the limit's own hours. It parks the implementer's running
items and raises an alarm that climbs the ladder for as long as the park stands. Nothing nudges
while it waits. It ends at the reset, at one retry where no reset was named, as soon as the
transcript goes on past the stop, or when keyflip rotates to an account with headroom; the same
session is then resumed once. The board is read back when the watchdog's state has lost the park.
"""
import ast
import json
import os
import pathlib
import re
import time

import pytest

from ao import email, lib as A, telegram
from ao import watchdog as W
from tests.scenarios import World

NOTIFY = W.notify                  # the scenario world replaces it; the ladder tests below ring the real one
ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMPT = "continue the open slice from where the last turn stopped"
LIMIT = "You've hit your session limit · resets in 2h"
MODEL_LIMIT = "You've reached your Fable 5 limit. Run /usage-credits to continue or switch models with /model."
SLICE = "- [B8] the slice · since: 2026-09-26 08:00"
HOUR = 3600


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    return World(project, monkeypatch, tmp_path)


def _harness(project, monkeypatch, tmp_path):
    """A world whose implementer's harness writes the error its service returned into the transcript."""
    cfg = dict(project, implementer={"adapter": "claude-code", "session": "s1", "name": "claude"})
    with open(os.path.join(project["root"], ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)
    return World(cfg, monkeypatch, tmp_path)


def _say(world, *said):
    """The transcript, as (seconds ago, who, words[, fields the record holds]): a prompt, the model's reply,
    or the harness's reply written in the model's place, shaped as its store writes a usage limit. The
    session is then idle past the watchdog's window."""
    now = time.time()
    records = []
    for ago, who, words, *fields in said:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - ago))
        if who == "prompt":
            records.append({"type": "user", "timestamp": stamp, "message": {"role": "user", "content": words}})
            continue
        message = {"role": "assistant", "content": [{"type": "text", "text": words}]}
        record = {"type": "assistant", "timestamp": stamp, "message": message}
        if who == "harness":
            message.update(model="<synthetic>", stop_reason="stop_sequence",
                           usage={"input_tokens": 0, "output_tokens": 0})
            record.update(isApiErrorMessage=True, error="rate_limit", apiErrorStatus=429)
        else:
            message.update(model="a-model", stop_reason="end_turn", usage={"input_tokens": 10, "output_tokens": 20})
        records.append(dict(record, **(fields[0] if fields else {})))
    world.transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return world.transcript_age(900)


class _Exited:
    """A started turn that ends as soon as it is asked: cleanly, or on what its harness printed."""
    pid = 99999

    def __init__(self, code=0):
        self.returncode = code

    def poll(self):
        return self.returncode


def _nudges_run(world, monkeypatch, prints=None, code=1):
    """Every turn the watchdog starts runs to its end; one that `prints` its harness's words dies on them."""
    def started(argv, **kw):
        world.spawned.append(argv)
        if prints is None or not str(argv[0]).startswith("/agents/"):
            return _Exited()
        kw["stdout"].write(prints + "\n")
        kw["stdout"].flush()
        return _Exited(code)
    monkeypatch.setattr(W.subprocess, "Popen", started)
    monkeypatch.setattr(W.time, "sleep", lambda seconds: None)
    return world


def _turns(world):
    """The agent turns the watchdog started: what it ran from an agent's binary, not the git it asked."""
    return [argv for argv in world.spawned if isinstance(argv, list) and argv and str(argv[0]).startswith("/agents/")]


def _alarms(monkeypatch):
    """Every raise of the park's alarm, with how the ladder is told to treat it."""
    raised = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, key=None, window=1800, audience="human",
                        level=None, quiet_until=None, what=None, red_after=None:
                        raised.append({"title": title, "msg": msg, "key": key, "audience": audience,
                                       "quiet_until": quiet_until, "red_after": red_after, "what": what}) or True)
    return raised


def _ladder(world, monkeypatch, minutes, every=15):
    """Run a cycle every `every` minutes for `minutes` on a clock of its own, through the real alarm ladder.

    Returns what reached each channel: [(channel, title)].
    """
    sent = []
    monkeypatch.setattr(W, "notify", NOTIFY)
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: sent.append(("desktop", title)) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: sent.append(("telegram", text)) or 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None:
                        sent.append(("email", subject)) or True)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    for _ in range(minutes // every + 1):
        world.cycle(dry_run=False)
        clock[0] += every * 60
    return [(channel, title) for channel, title in sent if "parked on quota" in title]


def _park(world):
    return W.load_state(world.root).get("quota_park") or {}


def _needs(world, item="B8"):
    return next((row["notes"].get("needs") for row in A.board(world.root)["blocked"] if row["id"] == item), None)


# ---- the reading: which words say the implementer stopped on its limit, and when it comes back -------

def test_a_limit_the_harness_wrote_in_the_models_place_is_a_stop_and_the_models_own_words_are_not(
        project, monkeypatch, tmp_path):
    world = _say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT), (300, "harness", LIMIT))

    stop = W.usage_limit_stop(world.cfg, {})

    assert stop["source"] == "transcript" and stop["text"] == "hit your session limit · resets in 2h"
    assert stop["resets_at"] == pytest.approx(stop["at"] + 2 * HOUR)

    _say(world, (600, "prompt", PROMPT),
         (300, "model", "Done: the watchdog parks a slice when you hit your session limit and resets in 2h."))
    assert W.usage_limit_stop(world.cfg, {}) is None


def test_the_reset_the_harness_records_beside_its_words_is_the_one_read(project, monkeypatch, tmp_path):
    world = _harness(project, monkeypatch, tmp_path)
    recorded = int(time.time() - 300 + 3 * HOUR)
    _say(world, (600, "prompt", PROMPT), (300, "harness", "You've hit your session limit · resets 9:20pm (Somewhere)",
                                          {"quotaLimits": {"rateLimitType": "five_hour", "resetsAt": recorded}}))

    assert W.usage_limit_stop(world.cfg, {})["resets_at"] == recorded

    # A recorded reset no limit of these hours can mean - milliseconds, say - leaves the words to say it.
    _say(world, (600, "prompt", PROMPT), (300, "harness", LIMIT, {"quotaLimits": {"resetsAt": recorded * 1000}}))
    stop = W.usage_limit_stop(world.cfg, {})
    assert stop["resets_at"] == pytest.approx(stop["at"] + 2 * HOUR)


def test_a_weekly_limit_that_names_only_the_clock_is_read_within_the_week(project, monkeypatch, tmp_path):
    world = _harness(project, monkeypatch, tmp_path)
    at = time.mktime(time.strptime("2026-09-06 18:17", "%Y-%m-%d %H:%M"))
    died = {"at": at, "code": 1, "tail": "You've hit your weekly limit · resets 4am (Europe/Istanbul)"}

    stop = W.usage_limit_stop(world.cfg, {"last_error": died})

    # Read within five hours, 4am named at 18:17 was that morning's, already gone, and the park had no reset.
    assert stop["resets_at"] == time.mktime(time.strptime("2026-09-07 04:00", "%Y-%m-%d %H:%M"))
    assert stop["resets_at"] - at == pytest.approx(9.72 * HOUR, abs=60)


def test_a_session_limit_whose_named_time_is_not_after_it_names_no_reset(project, monkeypatch, tmp_path):
    world = _harness(project, monkeypatch, tmp_path)
    at = time.mktime(time.strptime("2026-09-26 22:00", "%Y-%m-%d %H:%M"))
    died = {"at": at, "code": 1, "tail": "You've hit your session limit · resets 9:20pm"}

    stop = W.usage_limit_stop(world.cfg, {"last_error": died})

    assert stop is not None and stop["resets_at"] is None


def test_the_older_form_names_its_reset_after_the_bar(project, monkeypatch, tmp_path):
    world = _harness(project, monkeypatch, tmp_path)
    at = time.time() - 60
    epoch = int(at + 4 * HOUR)

    stop = W.usage_limit_stop(world.cfg, {"last_error": {"at": at, "code": 1,
                                                        "tail": f"Claude AI usage limit reached|{epoch}"}})

    assert stop["resets_at"] == epoch


def test_a_models_own_limit_is_a_stop_that_names_no_reset(project, monkeypatch, tmp_path):
    world = _say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT), (300, "harness", MODEL_LIMIT))

    stop = W.usage_limit_stop(world.cfg, {})

    assert stop["text"].startswith("reached your Fable 5 limit.") and stop["resets_at"] is None


def test_a_nudge_that_died_on_the_limit_is_read_in_the_words_of_its_own_harness_only(world):
    at = time.time() - 60
    request = "-".join(("1f2e3d4c", "5b6a", "7980"))
    died = (f"=== 2026-09-26 08:00:00 nudge === ServiceQuotaExceededException "
            f"Error: You've reached your overage limit. (Request ID: {request})")

    stop = W.usage_limit_stop(world.cfg, {"last_error": {"at": at, "code": 1, "tail": died}})

    assert stop["source"] == "nudge" and stop["text"].startswith("reached your overage limit.")
    assert stop["resets_at"] is None
    # What an earlier turn said stands before its own nudge's header, and one harness's limit is no other's.
    earlier = "the overage reader is done: reached your overage limit === 2026-09-26 08:00:00 nudge === "
    for tail in (earlier + "Error: no conversation with that id was found", LIMIT):
        assert W.usage_limit_stop(world.cfg, {"last_error": {"at": at, "code": 1, "tail": tail}}) is None


def test_an_adapter_that_declares_no_stops_is_never_read_as_stopped(world, monkeypatch):
    undeclared = {key: value for key, value in A.load_adapter("kiro").items() if key != "quota"}
    monkeypatch.setattr(A, "implementer_adapter", lambda cfg=None: undeclared)
    died = {"at": time.time() - 60, "code": 1, "tail": "Error: You've reached your overage limit."}

    assert W.usage_limit_stop(world.cfg, {"last_error": died}) is None


def test_a_nudge_that_died_on_the_limit_before_the_transcript_went_on_is_not_read(project, monkeypatch, tmp_path):
    world = _say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                 (300, "model", "The slice is finished and its gates pass; the report is in the mailbox."))
    died = {"at": time.time() - 3600, "code": 1, "tail": LIMIT}

    assert W.usage_limit_stop(world.cfg, {"last_error": died}) is None


def test_a_stop_that_cannot_be_read_is_named_when_the_adapter_is_validated():
    adapter = dict(A.load_adapter("kiro"), quota={"stops": [{"words": "reached your (limit", "hours": 5},
                                                            {"words": "hit your limit"}]})

    problems = A.validate_adapter(adapter)

    assert any("#1 words 'reached your (limit' do not compile" in problem for problem in problems)
    assert any("#2 must say in `hours`" in problem for problem in problems)
    assert A.quota_stops(adapter) == []


def test_no_core_module_holds_the_words_or_the_fields_an_adapter_declares_for_a_stop():
    adapters = A.package_adapters().values()
    stops = [stop["words"] for adapter in adapters for stop in A.quota_stops(adapter)]
    fields = {step for adapter in adapters for step in A.quota_reset_path(adapter).split(".") if step}
    assert len(stops) >= 5 and {"quotaLimits", "resetsAt"} <= fields
    named = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, sorted(fields))) + r")(?![A-Za-z0-9_])")

    found = []
    for path in sorted((ROOT / "src" / "ao").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {id(node.body[0].value) for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        found += [f"{path.name}:{node.lineno}" for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
                  and (named.search(node.value) or any(words.search(node.value) for words in stops))]

    assert found == []


# ---- the decision: park, hold, and end once --------------------------------------------------------

def test_the_decision_parks_a_stop_holds_it_until_its_reset_and_resumes_after_it_once():
    stop = {"at": 1000.0, "text": "hit your session limit", "resets_at": 8200.0, "source": "transcript", "hours": 5}

    action, park = W.quota_park_step(stop, None, None, ["B8"], [], now=1100)

    assert action == "park" and park["items"] == ["B8"] and park["until"] == 8200.0 and park["named"]
    assert W.quota_park_step(stop, park, None, [], ["B8"], now=8000)[0] == "hold"
    assert W.quota_park_step(None, park, None, [], ["B8"], now=8000)[0] == "hold"        # a known reset is waited for
    assert W.quota_park_step(stop, park, None, [], ["B8"], now=8200)[0] == "resume"
    assert W.quota_park_step(stop, None, 1000.0, ["B8"], [], now=8300) == (None, None)  # once for a stop
    later = dict(stop, at=8400.0, resets_at=12_000.0)
    assert W.quota_park_step(later, None, 1000.0, ["B8"], [], now=8500)[0] == "park"    # a new stop parks again
    assert W.quota_park_step(stop, park, None, ["B8"], [], now=8000)[0] == "clear"     # someone moved it back
    passed = dict(stop, resets_at=1050.0)
    assert W.quota_park_step(passed, None, None, ["B8"], [], now=1100)[0] == "resume"   # already reset when read
    assert W.quota_park_step(stop, None, None, [], [], now=1100) == (None, None)        # nothing running to park


def test_a_stop_that_named_no_reset_is_tried_again_after_one_retry_and_never_held_for_ever():
    stop = {"at": 1000.0, "text": "reached your overage limit.", "resets_at": None, "source": "nudge", "hours": 744}

    action, park = W.quota_park_step(stop, None, None, ["B8"], [], now=1100, retry=5 * HOUR)

    assert action == "park" and not park["named"] and park["until"] == 1000.0 + 5 * HOUR
    assert [W.quota_park_step(stop, park, None, [], ["B8"], now=now, retry=5 * HOUR)[0]
            for now in (1200, 1000 + 5 * HOUR - 1, 1000 + 5 * HOUR, 30 * 86_400)] \
        == ["hold", "hold", "resume", "resume"]
    # A limit whose own hours are fewer than the retry is gone by then.
    assert W.quota_park_step(dict(stop, hours=1), None, None, ["B8"], [], now=1100, retry=5 * HOUR)[1]["until"] \
        == 1000.0 + HOUR
    # Tried again and still standing is a newer stop, which parks again: one nudge a retry.
    again = dict(stop, at=1000.0 + 5 * HOUR + 30)
    assert W.quota_park_step(again, None, 1000.0, ["B8"], [], now=again["at"] + 60, retry=5 * HOUR)[0] == "park"
    # A newer stop that names its reset gives the park its time.
    action, timed = W.quota_park_step(dict(stop, at=2000.0, resets_at=9000.0), park, None, [], ["B8"], now=2100,
                                      retry=5 * HOUR)
    assert action == "hold" and timed["until"] == 9000.0 and timed["named"]


def test_a_park_ends_as_soon_as_the_transcript_went_on_past_its_stop_whatever_it_named():
    stop = {"at": 1000.0, "text": "hit your session limit", "resets_at": 8200.0, "source": "transcript", "hours": 5}
    _, park = W.quota_park_step(stop, None, None, ["B8"], [], now=1100)

    assert W.quota_park_step(None, park, None, [], ["B8"], now=1300, spoken=900.0)[0] == "hold"
    action, ended = W.quota_park_step(None, park, None, [], ["B8"], now=1300, spoken=1200.0)
    assert action == "resume" and ended["went_on"]
    # A person who resumed and was answered with the limit again is a newer stop, not the limit gone.
    newer = dict(stop, at=1250.0, resets_at=9000.0)
    assert W.quota_park_step(newer, park, None, [], ["B8"], now=1300, spoken=1200.0)[0] == "hold"


def test_items_blocked_on_quota_that_the_state_forgot_are_parked_again_or_resumed():
    stop = {"at": 1000.0, "text": "hit your session limit", "resets_at": 8200.0, "source": "transcript", "hours": 5}

    action, park = W.quota_park_step(stop, None, None, ["B9"], ["B8"], now=1100)
    assert action == "park" and park["items"] == ["B8", "B9"] and park["until"] == 8200.0

    action, park = W.quota_park_step(None, None, None, [], ["B8"], now=1100)
    assert action == "resume" and park["items"] == ["B8"] and park["lost"]


# ---- the watchdog: the board, the alarm, the nudge --------------------------------------------------

def test_a_stop_parks_the_running_slice_with_its_reset_and_its_alarm_stands_while_nothing_nudges_it(
        project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    raised = _alarms(monkeypatch)
    world.board("running", SLICE)

    explained = world.cycle()
    assert explained[-1].startswith("DRY RUN: would park B8 on quota until ")
    assert [item["id"] for item in A.board(world.root)["running"]] == ["B8"]

    for _ in range(3):
        world.cycle(dry_run=False)

    park = _park(world)
    until = park["until"]
    assert until == pytest.approx(time.time() - 400 + 2 * HOUR, abs=5)
    board = A.board(world.root)
    assert board["running"] == []
    assert board["blocked"] == [{"id": "B8", "title": "the slice",
                                 "notes": {"since": "2026-09-26 08:00", "needs": f"quota (resets {W._when(until)})"}}]
    assert _turns(world) == []
    assert world.verdict == f"B8 parked on quota until {W._when(until)}; not nudging"
    # Raised on every cycle it stands, never lapsing; it says one thing, and turns red only past its reset.
    parks = [alarm for alarm in raised if alarm["key"] == "quota-park"]
    assert len(parks) >= 3 and {alarm["what"] for alarm in parks} == {"B8: hit your session limit · resets in 2h"}
    assert "B8 parked on quota" in parks[0]["title"] and parks[0]["audience"] == "human"
    assert parks[-1]["quiet_until"] == until
    assert parks[-1]["red_after"] == pytest.approx(until - park["since"] + 60 * 60)


def test_after_the_reset_the_slice_runs_again_and_the_same_session_is_resumed_once(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", f"{SLICE} · needs: B3")
    world.cycle(dry_run=False)
    assert _needs(world).startswith("quota (resets ")
    state = W.load_state(world.root)
    state["quota_park"]["until"] = time.time() - 1                       # the reset has passed
    W.save_state(world.root, state)

    resumed = world.cycle(dry_run=False)
    world.cycle(dry_run=False)

    assert any(line.startswith("the usage limit reset ") and "B8 running again" in line for line in resumed)
    (argv,) = _turns(world)
    assert argv[0].endswith("/claude") and argv[1:3] == ["--resume", "s1"]
    board = A.board(world.root)
    assert board["blocked"] == [] and board["running"] == [{"id": "B8", "title": "the slice",
                                                            "notes": {"since": "2026-09-26 08:00", "needs": "B3"}}]
    assert "quota_park" not in W.load_state(world.root)


def test_a_limit_that_names_no_reset_is_tried_again_once_a_retry_and_its_alarm_climbs_the_ladder(
        project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", MODEL_LIMIT)), monkeypatch, prints=MODEL_LIMIT)
    raised = _alarms(monkeypatch)
    world.board("running", SLICE)

    for _ in range(3):
        world.cycle(dry_run=False)

    retry = _park(world)["until"]
    assert retry == pytest.approx(time.time() - 400 + 5 * HOUR, abs=5)
    assert _needs(world) == f"quota (retry {W._when(retry)})" and _turns(world) == []
    assert world.verdict == f"B8 parked on quota with no reset named; one nudge tries again {W._when(retry)}; " \
                            "not nudging"
    parks = [alarm for alarm in raised if alarm["key"] == "quota-park"]
    assert len(parks) >= 3 and all(alarm["quiet_until"] is None and alarm["red_after"] is None for alarm in parks)
    assert "one nudge tries again" in parks[0]["msg"]

    # At the retry one nudge goes out; dying on the same limit, it parks the slice again in the same cycle.
    state = W.load_state(world.root)
    state["quota_park"]["until"] = time.time() - 1
    W.save_state(world.root, state)
    tried = world.cycle(dry_run=False)
    assert any("the retry at" in line and "B8 running again" in line for line in tried)
    assert len(_turns(world)) == 1 and _needs(world).startswith("quota (retry ")
    assert not [alarm for alarm in raised if "nudge failed" in alarm["title"]]

    # And it waits for its next retry rather than trying every cycle.
    world.cycle(dry_run=False)
    world.cycle(dry_run=False)
    assert len(_turns(world)) == 1 and _park(world)["until"] > time.time() + 4 * HOUR


def test_a_park_whose_reset_nobody_named_rings_once_turns_red_after_the_hour_and_is_mailed(
        project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", MODEL_LIMIT)), monkeypatch, prints=MODEL_LIMIT)
    world.board("running", SLICE)

    sent = _ladder(world, monkeypatch, minutes=120)

    # Told once, it used to go quiet two hours later while the slice waited on; now it climbs.
    assert [channel for channel, _ in sent] == ["desktop", "telegram", "email"]
    (standing,) = [alarm for alarm in A.active_alarms(A.project_key(world.root)) if alarm["key"] == "quota-park"]
    assert standing["ring"] == "red" and standing["count"] >= 8


def test_a_park_with_a_named_reset_rings_once_and_is_not_mailed_before_its_reset(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)

    sent = _ladder(world, monkeypatch, minutes=90)

    assert [channel for channel, _ in sent] == ["desktop", "telegram"]
    (standing,) = [alarm for alarm in A.active_alarms(A.project_key(world.root)) if alarm["key"] == "quota-park"]
    assert standing["ring"] == "orange" and _needs(world).startswith("quota (resets ")


def test_the_transcript_going_on_past_the_stop_ends_even_a_park_whose_reset_is_hours_away(
        project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)
    world.cycle(dry_run=False)
    assert _park(world)["until"] > time.time() + HOUR

    # A person switched the account and resumed the session in two words; the model answered in two.
    _say(world, (600, "prompt", PROMPT), (400, "harness", LIMIT), (300, "prompt", "go on"), (200, "model", "Going on."))
    went_on = world.cycle(dry_run=False)

    assert any(line.startswith("the transcript went on past the usage limit: B8 running again") for line in went_on)
    assert [item["id"] for item in A.board(world.root)["running"]] == ["B8"] and len(_turns(world)) == 1


def test_with_rotation_on_a_window_rotated_to_headroom_ends_the_park_at_once(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)
    asked = []
    headroom = {"ok": True, "provider": "claude", "rotated": False, "text": "headroom"}
    monkeypatch.setattr(A, "rotate_if_exhausted", lambda cfg, argv, who: asked.append((argv[0], who)) or dict(headroom))

    world.cycle(dry_run=False)
    assert _needs(world).startswith("quota (resets ") and asked == [("claude", "implementer")]

    # Held, it asks again once fifteen minutes have passed, not on every cycle.
    headroom.update(rotated=True, text="rotated through keyflip for the implementer: claude now 20% used")
    world.cycle(dry_run=False)
    assert len(asked) == 1 and _needs(world).startswith("quota (resets ")
    state = W.load_state(world.root)
    state["quota_rotation_asked"] -= 15 * 60
    W.save_state(world.root, state)
    rotated = world.cycle(dry_run=False)

    assert any(line.startswith("keyflip moved the implementer's window to an account with headroom")
               and "B8 running again" in line for line in rotated)
    assert [item["id"] for item in A.board(world.root)["running"]] == ["B8"] and len(_turns(world)) == 1


def test_with_rotation_on_a_stop_met_by_a_rotation_to_headroom_is_not_parked_at_all(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)
    monkeypatch.setattr(A, "rotate_if_exhausted", lambda cfg, argv, who: {
        "ok": True, "provider": "claude", "rotated": True,
        "text": "rotated through keyflip for the implementer: claude now 20% used"})

    trace = world.cycle(dry_run=False)

    assert any(line.startswith("keyflip moved the implementer's window") and "B8 not parked" in line for line in trace)
    assert [item["id"] for item in A.board(world.root)["running"]] == ["B8"] and len(_turns(world)) == 1
    # The stop it went on past is not parked on the next cycle either.
    world.transcript_age(900)
    world.cycle(dry_run=False)
    assert A.board(world.root)["blocked"] == []


def test_a_nudge_that_died_on_the_overage_limit_parks_the_slice_in_the_same_cycle(world, monkeypatch):
    request = "-".join(("9ac1cb20", "f411", "4c2b"))
    _nudges_run(world, monkeypatch, prints=f"ServiceQuotaExceededException\n"
                                           f"Error: You've reached your overage limit. (Request ID: {request})")
    raised = _alarms(monkeypatch)
    world.board("running", SLICE)
    world.transcript_age(900)

    first = world.cycle(dry_run=False)
    for _ in range(3):
        world.cycle(dry_run=False)

    assert any(line.startswith("B8 parked on quota with no reset named") for line in first)
    assert len(_turns(world)) == 1 and _needs(world).startswith("quota (retry ")
    assert "reached your overage limit." in _park(world)["text"]
    # A person is told the park, not a failed nudge or a stuck agent, and no architect is woken to judge it.
    assert not [alarm for alarm in raised if "nudge failed" in alarm["title"] or "stuck" in alarm["title"]]
    assert not [name for name in os.listdir(os.path.join(world.root, "agent-mail")) if "restart-failed" in name]


def test_only_the_implementers_items_are_parked(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", "- [D2] the design pass · role: designer")
    world.board("running", "- [B9] the other half · role: implementer")
    world.board("running", SLICE)

    world.cycle(dry_run=False)

    assert [item["id"] for item in A.board(world.root)["running"]] == ["D2"]
    assert sorted(_park(world)["items"]) == ["B8", "B9"] and _needs(world, "B9").startswith("quota (resets ")


def test_a_board_holding_a_byte_that_is_not_utf8_is_parked_and_kept_byte_for_byte(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)
    path = os.path.join(world.root, ".ao", "board.md")
    with open(path, "ab") as fh:
        fh.write(b"\n- [B1] a note an editor saved as cp1254: \xfd\n")

    world.cycle(dry_run=False)

    assert _needs(world).startswith("quota (resets ")
    with open(path, "rb") as fh:
        assert b": \xfd\n" in fh.read()


def test_a_park_the_watchdog_state_lost_is_read_back_from_the_board(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)
    world.cycle(dry_run=False)
    until = _park(world)["until"]

    with open(W.state_path(world.root), "w", encoding="utf-8") as fh:
        fh.write('{"attempts": 0, "quota_pa')                        # torn as it was written
    world.cycle(dry_run=False)
    assert _park(world)["until"] == pytest.approx(until) and _needs(world).startswith("quota (resets ")
    assert _turns(world) == []

    # Lost again, with nothing read that says the limit stands: the slice runs again.
    os.remove(W.state_path(world.root))
    world.transcript.write_text("", encoding="utf-8")
    world.transcript_age(900)
    resumed = world.cycle(dry_run=False)
    assert any(line.startswith("blocked on quota with no park standing") for line in resumed)
    assert [item["id"] for item in A.board(world.root)["running"]] == ["B8"] and len(_turns(world)) == 1


def test_a_board_move_changes_one_line_and_leaves_every_other_as_written(project):
    path = os.path.join(project["root"], ".ao", "board.md")
    with open(path, encoding="utf-8") as fh:
        written = fh.read().replace("## running\n", f"## running\n{SLICE} · role: implementer\n- [B9] another\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(written)

    assert A.board_move(project["root"], "B8", "blocked", {"needs": "quota"})
    with open(path, encoding="utf-8") as fh:
        moved = fh.read()
    assert moved == written.replace(f"{SLICE} · role: implementer\n", "").replace(
        "## blocked\n", f"## blocked\n{SLICE} · role: implementer · needs: quota\n")

    assert A.board_move(project["root"], "B8", "running", {"needs": None})
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == written
    assert A.board_move(project["root"], "B7", "running") is False


def test_a_lost_park_gives_each_item_back_the_needs_it_carried(project, monkeypatch, tmp_path):
    """The `needs:` an item had before its park is kept on the board line, so a park the watchdog's state lost
    gives it back as one that stood does (QUOTA-PARK-2)."""
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE + " · needs: the fixture B7 writes")
    world.cycle(dry_run=False)
    (parked,) = A.board(world.root)["blocked"]
    assert parked["notes"]["held-needs"] == "the fixture B7 writes" and parked["notes"]["needs"].startswith("quota (")

    os.remove(W.state_path(world.root))                            # the park is lost with the state
    world.transcript.write_text("", encoding="utf-8")
    world.transcript_age(900)
    world.cycle(dry_run=False)

    (running,) = A.board(world.root)["running"]
    assert running["notes"].get("needs") == "the fixture B7 writes" and "held-needs" not in running["notes"]


def test_an_item_with_no_needs_of_its_own_is_parked_and_resumed_without_one(project, monkeypatch, tmp_path):
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE)
    world.cycle(dry_run=False)
    (parked,) = A.board(world.root)["blocked"]
    assert "held-needs" not in parked["notes"]

    os.remove(W.state_path(world.root))
    world.transcript.write_text("", encoding="utf-8")
    world.transcript_age(900)
    world.cycle(dry_run=False)

    (running,) = A.board(world.root)["running"]
    assert "needs" not in running["notes"] and "held-needs" not in running["notes"]


def test_a_lost_park_parked_again_on_a_fresh_stop_keeps_the_needs_it_held(project, monkeypatch, tmp_path):
    """The review of QUOTA-PARK-2 found a park made again, after the state lost it, clearing the needs the line held."""
    world = _nudges_run(_say(_harness(project, monkeypatch, tmp_path), (600, "prompt", PROMPT),
                             (400, "harness", LIMIT)), monkeypatch)
    world.board("running", SLICE + " · needs: the fixture B7 writes")
    world.cycle(dry_run=False)

    os.remove(W.state_path(world.root))                            # lost while the limit stands, and a newer
    _say(world, (600, "prompt", PROMPT), (200, "harness", "You've hit your session limit · resets in 3h"))
    world.cycle(dry_run=False)                                     # stop names another reset: parked again

    (parked,) = A.board(world.root)["blocked"]
    assert parked["notes"]["held-needs"] == "the fixture B7 writes"

    os.remove(W.state_path(world.root))                            # lost once more, and nothing says it stands
    world.transcript.write_text("", encoding="utf-8")
    world.transcript_age(900)
    world.cycle(dry_run=False)

    (running,) = A.board(world.root)["running"]
    assert running["notes"].get("needs") == "the fixture B7 writes" and "held-needs" not in running["notes"]
