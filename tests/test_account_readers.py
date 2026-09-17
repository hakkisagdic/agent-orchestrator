"""Every credit account ao reads is the implementer's own adapter's (ACCOUNT-READERS).

HARNESS-READINGS-2 gave `ao cost` the implementer's own account. `ao credits`, the credits
section of `ao digest`, `ao handoff` and the watchdog's credit sampler still read the first
shipped adapter's, whatever the implementer ran, and the samples the sampler records fed the
doctor's projection, page and credits line whoever had read them. An implementer on a harness
that bills no account ao can read was shown, sampled and alarmed on one it never spends.

Two harnesses are shipped beside the real ones, each the reference adapter under another id
with a fake driver: one declares no account lookup, the other declares one. Both sort after
the first shipped adapter that declares a lookup, whose driver records it if it is ever asked,
and a layer the implementer can write claims a lookup for each. Every reader asks only the
package's lookup of the implementer's own adapter; where a figure stood it says there is none
ao can read for the first, samples nothing for it and raises no credits alarm for it, whatever
the ledger holds. A sample names the adapter it was read through, and only that adapter's
samples project its burn rate.
"""
import json
import os
import re
import time
from types import SimpleNamespace

import pytest

from ao import cli, drivers, lib as A
from ao import watchdog as W
from tests.scenarios import World
from tests.test_transcript_readings import _transcript, _turn

ACCOUNT_USAGE = A.account_usage              # the scenario world stubs the reader these tests are about
PLAIN, METERED = "plain-harness", "metered-harness"
FIRST = "the first shipped adapter's lookup"
DAY, HOUR = 86400, 3600


def _plain(capsys):
    return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _fleet(project, monkeypatch, reading):
    """The two harnesses shipped beside the real ones, a layer's claims, and every lookup asked.

    A lookup answers `reading` as it stands when it is asked, and is recorded by the declaration it
    came from: the package's adapter, a layer's claim, or the first shipped adapter's.
    """
    asked = []
    monkeypatch.setitem(drivers.USAGE, "fake-usage",
                        lambda api, timeout=20: asked.append(api["marker"]) or dict(reading))
    monkeypatch.setitem(drivers.USAGE, "usage-limits", lambda api, timeout=20: asked.append(FIRST) or dict(reading))
    shipped = dict(A.package_adapters())
    reference = shipped["kiro"]
    shipped[PLAIN] = dict(reference, id=PLAIN, billing={"unit": "token"})
    shipped[METERED] = dict(reference, id=METERED, billing={
        "api": {"driver": "fake-usage", "marker": METERED, "login": ["metered", "login"]}})
    monkeypatch.setattr(A, "package_adapters", lambda: shipped)
    layer = os.path.join(project["root"], ".ao", "adapters")
    os.makedirs(layer, exist_ok=True)
    for ident in (PLAIN, METERED):
        claim = dict(shipped[ident], billing={"api": {"driver": "fake-usage", "marker": f"{ident} as a layer claims"}})
        with open(os.path.join(layer, f"{ident}.json"), "w", encoding="utf-8") as fh:
            json.dump(claim, fh)
    return asked


def _implementer(project, ident):
    """The project's configuration with its implementer on `ident`, written where a watchdog cycle loads it."""
    cfg = dict(project, implementer=dict(project["implementer"], adapter=ident))
    with open(os.path.join(project["root"], ".ao", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({key: value for key, value in cfg.items() if key != "root"}, fh)
    return cfg


def _ledger(root):
    """Every credit sample written, whichever adapter it names."""
    path = os.path.join(root, ".ao", "ledger", "credits.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ── the lookup ──

def test_a_lookup_is_the_package_adapters_own_and_a_reader_that_names_no_adapter_reads_none(project, monkeypatch):
    asked = _fleet(project, monkeypatch, {"used": 1.0, "limit": 2.0})
    first = next(ident for ident in sorted(A.package_adapters()) if A.usage_api(ident))

    assert first < METERED < PLAIN                  # a reader falling back on the first one's lookup asks it
    assert A.load_adapter(PLAIN, project["root"])["billing"]["api"]["driver"] == "fake-usage"
    assert A.usage_api(PLAIN) == {} and A.usage_api(METERED)["marker"] == METERED
    assert A.usage_api(None) == {} and A.usage_api("") == {}
    assert A.account_usage(adapter_id=PLAIN) is None and A.account_usage() is None and asked == []
    assert A.account_usage(adapter_id=METERED) == {"used": 1.0, "limit": 2.0} and asked == [METERED]


# ── ao credits ──

def test_ao_credits_shows_the_implementers_own_account_or_that_its_adapter_declares_none(project, monkeypatch, capsys):
    asked = _fleet(project, monkeypatch, {"used": 4000.0, "limit": 10000.0, "plan": "METERED PLAN"})
    monkeypatch.setattr(A, "turn_costs", lambda cfg, since=None: {"turns": 0, "total": 0.0, "unit": "credits"})
    args = SimpleNamespace(offline=False, local=False, reset_day=None)

    assert cli.cmd_credits(_implementer(project, PLAIN), args) == 0
    out = _plain(capsys)
    assert f"the account: {PLAIN} declares none ao can read" in out and "10,000" not in out and asked == []

    assert cli.cmd_credits(_implementer(project, METERED), args) == 0
    out = _plain(capsys)
    assert "METERED PLAN" in out and "4,000.00 of 10,000" in out and "none ao can read" not in out
    assert set(asked) == {METERED} and _ledger(project["root"]) == [] and A.active_alarms("proj") == []


def test_the_offline_estimate_reads_only_the_implementers_own_adapters_transcripts(project, monkeypatch, capsys):
    _fleet(project, monkeypatch, {})
    records = []
    _turn(records, time.time() - HOUR, 12.5)
    store = A.package_adapters()["kiro"]["billing"]["fallback"]["transcripts"]
    _transcript(A._home_path(store.replace("*", "s1")), monkeypatch, records)
    offline = SimpleNamespace(offline=True, local=False, reset_day=None)

    plain = _implementer(project, PLAIN)
    assert cli.cmd_credits(plain, offline) == 0
    out = _plain(capsys)
    assert f"the account: {PLAIN} declares none ao can read" in out and "ESTIMATE" not in out
    assert A.credit_usage(adapter_id=PLAIN)["days"] == {} and A.digest(project["root"], plain)["credit_days"] == []

    assert cli.cmd_credits(_implementer(project, "kiro"), offline) == 0
    assert "ESTIMATE FROM LOCAL TRANSCRIPTS" in _plain(capsys)
    assert sum(A.credit_usage(adapter_id="kiro")["days"].values()) == 12.5


# ── ao digest ──

def test_the_digests_credits_are_the_implementers_own_account_or_say_its_adapter_declares_none(project, monkeypatch,
                                                                                               capsys):
    asked = _fleet(project, monkeypatch, {"used": 4000.0, "limit": 10000.0})
    monkeypatch.setattr(A, "busy", lambda *args, **kwargs: ("idle", None, ""))
    monkeypatch.setattr(A, "spinning", lambda *args, **kwargs: None)
    root, args = project["root"], SimpleNamespace(days=1, n=5)

    plain = _implementer(project, PLAIN)
    digest = A.digest(root, plain)
    assert "credits" not in digest and digest["account"] == {"adapter": PLAIN, "readable": False}
    assert cli.cmd_digest(plain, args) == 0
    assert f"{PLAIN} declares none ao can read" in _plain(capsys) and asked == []

    metered = _implementer(project, METERED)
    assert A.digest(root, metered)["credits"] == {"used": 4000.0, "limit": 10000.0, "remaining": 6000.0}
    assert cli.cmd_digest(metered, args) == 0
    out = _plain(capsys)
    assert "4,000 / 10,000" in out and "none ao can read" not in out and set(asked) == {METERED}
    assert _ledger(root) == [] and A.active_alarms("proj") == []


# ── ao handoff ──

def test_the_handoff_names_the_implementers_own_credit_or_that_its_adapter_declares_none(project, monkeypatch, capsys):
    asked = _fleet(project, monkeypatch, {"used": 4000.0, "limit": 10000.0})
    monkeypatch.setattr(A, "busy", lambda *args, **kwargs: ("idle", None, ""))
    args = SimpleNamespace(reason=None, no_send=True)

    assert cli.cmd_handoff(_implementer(project, PLAIN), args) == 0
    assert f"- credit: {PLAIN} declares none ao can read" in _plain(capsys) and asked == []

    assert cli.cmd_handoff(_implementer(project, METERED), args) == 0
    assert "- credit: 4,000 / 10,000 (6,000 left)" in _plain(capsys) and asked == [METERED]
    assert _ledger(project["root"]) == [] and A.active_alarms("proj") == []


# ── the watchdog's credit sampler ──

def test_the_sampler_samples_and_alarms_only_on_the_account_the_implementers_adapter_declares(project, monkeypatch):
    reading = {"used": 10240.0, "limit": 10000.0, "reset_at": time.time() + 10 * DAY, "account": "acct-1"}
    asked = _fleet(project, monkeypatch, reading)
    raised = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, key=None, **kw: raised.append(key) or True)
    root, now, st = project["root"], time.time(), {}

    W._sample_credits(root, st, PLAIN, "proj", now=now)

    assert (asked, raised, st, _ledger(root)) == ([], [], {}, [])

    W._sample_credits(root, st, METERED, "proj", now=now)

    assert asked == [METERED] and raised == ["credits-exhaust"]
    assert [(sample["adapter"], sample["account"]) for sample in _ledger(root)] == [(METERED, "acct-1")]

    reading.clear()
    reading["error"] = "the metered harness is not on PATH"
    W._sample_credits(root, st, METERED, "proj", now=now + HOUR)

    assert W.load_state(root)["credit_check_problem"]["adapter"] == METERED and len(_ledger(root)) == 1


@pytest.mark.parametrize("ident, sampled", [(PLAIN, []), (METERED, [METERED])], ids=["no-lookup", "own-lookup"])
def test_a_watchdog_cycle_samples_only_the_account_its_implementers_own_adapter_declares(project, monkeypatch,
                                                                                         tmp_path, ident, sampled):
    world = World(project, monkeypatch, tmp_path)
    raised = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, key=None, **kw: raised.append(key) or True)
    monkeypatch.setattr(A, "account_usage", ACCOUNT_USAGE)
    asked = _fleet(project, monkeypatch, {"used": 10240.0, "limit": 10000.0, "reset_at": time.time() + 10 * DAY,
                                          "account": "acct-1"})
    _implementer(project, ident)
    with open(os.path.join(world.root, ".ao", "hold"), "w", encoding="utf-8") as fh:     # the cycle stands down
        json.dump({"by": "owner", "reason": "the credits are read all the same", "at": time.time()}, fh)

    world.cycle(dry_run=False)

    assert asked == sampled and [sample["adapter"] for sample in _ledger(world.root)] == sampled
    assert [key for key in raised if str(key).startswith("credits")] == ["credits-exhaust"] * len(sampled)


# ── the doctor, over a ledger holding other harnesses' readings ──

def test_other_readings_in_the_ledger_project_page_and_show_nothing_for_this_implementer(project, monkeypatch, capsys):
    _fleet(project, monkeypatch, {})
    raised = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, key=None, **kw: raised.append(key) or True)
    root, now = project["root"], time.time()
    # One account throughout: the first shipped adapter's reading, one from before readings named their
    # adapter, then the metered harness's own two, the last spent.
    for at, used, adapter in ((now - 6 * HOUR, 500, "kiro"), (now - 5 * HOUR, 700, None),
                              (now - 4 * HOUR, 8000, METERED), (now - HOUR, 10240, METERED)):
        A.record_credit_sample(root, used, 10000, reset_at=now + 10 * DAY, account="acct-1", at=at, adapter=adapter)
    W.save_state(root, {"credit_check_problem": {"at": int(now), "reason": "the lookup failed", "adapter": METERED}})

    plain = _implementer(project, PLAIN)
    assert cli._implementer_credits(plain) == {"adapter": PLAIN, "readable": False, "samples": [], "rate": None}
    assert [" ".join(re.sub(r"\x1b\[[0-9;]*m", "", line).split()) for line in cli._doctor_credit_lines(plain)] == [
        f"credits {PLAIN} declares none ao can read"]
    cli._doctor_check(plain, page=True)
    assert "PROBLEM credits-" not in _plain(capsys) and not {"credits-exhaust", "doctor:credits-check"} & set(raised)

    metered = _implementer(project, METERED)
    own = cli._implementer_credits(metered)
    assert [sample["used"] for sample in own["samples"]] == [8000.0, 10240.0]
    assert [sample["used"] for sample in own["rate"]["samples"]] == [8000.0, 10240.0]
    assert A.credit_samples(root, None) == [] and A.burn_rate(root, None) is None
    cli._doctor_check(metered, page=True)
    out = _plain(capsys)
    assert "PROBLEM credits-exhaust: credits exhausted at the last reading: 10240/10000 (account acct-1)" in out
    assert "PROBLEM credits-check: credit usage cannot be read (the lookup failed)" in out
    assert "credits-exhaust" in raised
