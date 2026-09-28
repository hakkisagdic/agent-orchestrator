"""`ao cost --usd` prices the implementer's tokens from a price table ao ships, and says it is an estimate (USD-COST).

`ao cost` counts spend in the unit a harness bills - credits, tokens - and a token count weighs a
cache read like an output token though one costs fifty times the other. The adapter declares where
a usage record names its model and its tokens of each kind (`telemetry.cost.model`, `tokens`) and
the fields that say it was billed at list prices (`priced_when`); the same reading and the same
turns as `ao cost` read them, and src/ao/prices.json prices them per million tokens, by model and
kind. The figure is labelled an estimate, with the table's version and the day and source of each
vendor's prices. A model, a kind of token or a rate the table does not price is unknown, never
zero; a model served free is priced at zero only because the table lists it so; a table with a
mistake in it prices nothing.
"""
import json
import re
import time
from types import SimpleNamespace

import pytest

from ao import cli, lib as A
from tests.test_second_harness_cost import HARNESS, R1, R2, R3, R4, _prompt, _stamp, _text, _world
from tests.test_subagent_spend import S1, S2, S3, S4, _delegating, _subagents, _world as _delegating_world
from tests.test_transcript_shape import FIXTURE, _event, _world as _shape_world

KINDS = {"input": "input tokens at the base rate", "cache_write_5m": "written to a five-minute cache",
         "cache_write_1h": "written to a one-hour cache", "cache_read": "read from a cache",
         "output": "output tokens"}
TABLE = {"version": 7, "currency": "USD", "per_tokens": 1_000_000, "kinds": KINDS,
         "vendors": {"vendor-a": {"as_of": "2026-09-01", "source": "https://example.com/prices", "models": {
             "model-known": {"input": 3, "cache_write_5m": 3.75, "cache_write_1h": 6, "cache_read": 0.3, "output": 15,
                             "ids": ["model-known-20260901"]},
             "model-free": {"input": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0,
                            "note": "served free on its vendor's free tier"}}}}}


def _usage(fresh=0, five=0, hour=0, read=0, written=0, **rate):
    """A response's usage as the second shipped harness writes it, its cache writes broken down by duration."""
    return dict({"input_tokens": fresh, "cache_creation_input_tokens": five + hour, "cache_read_input_tokens": read,
                 "output_tokens": written, "service_tier": "standard",
                 "cache_creation": {"ephemeral_5m_input_tokens": five, "ephemeral_1h_input_tokens": hour}}, **rate)


def _answer(at, ident, model, usage, *blocks, stop="end_turn"):
    """One response by one model: a record per content block, each repeating the response's usage."""
    return [{"type": "assistant", "timestamp": _stamp(at),
             "message": {"id": ident, "model": model, "type": "message", "role": "assistant", "content": [block],
                         "stop_reason": stop, "usage": dict(usage)}} for block in blocks or [_text("done")]]


def _by_kind(*usages):
    """The tokens of each kind the table prices, as the adapter reads them from usages the harness wrote."""
    out = {}
    for usage in usages:
        for kind, count in (("input", usage["input_tokens"]),
                            ("cache_write_5m", usage["cache_creation"]["ephemeral_5m_input_tokens"]),
                            ("cache_write_1h", usage["cache_creation"]["ephemeral_1h_input_tokens"]),
                            ("cache_read", usage["cache_read_input_tokens"]), ("output", usage["output_tokens"])):
            if count:
                out[kind] = out.get(kind, 0) + count
    return out


def _of(tokens, model, rate=None):
    """{kind: count} of one model at one rate, from what `turn_costs(by_model=True)` adds up."""
    return {kind: count for (named, at, kind), count in tokens.items() if (named, at) == (model, rate)}


def _prices(monkeypatch, tmp_path, table=TABLE):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(A, "PRICES_FILE", str(path))


def _usd_report(cfg, capsys):
    code = cli.cmd_cost(cfg, SimpleNamespace(since=None, features=False, usd=True))
    return code, re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def _line(out, start):
    return next(line for line in out.splitlines() if line.strip().startswith(start))


BIG = _usage(fresh=10_000, five=200_000, hour=400_000, read=10_000_000, written=50_000)
BIG_USD = (10_000 * 3 + 200_000 * 3.75 + 400_000 * 6 + 10_000_000 * 0.3 + 50_000 * 15) / 1_000_000     # 6.93


# ── a model the table lists is priced, each kind at its own rate ──

def test_a_model_the_table_lists_is_priced_by_its_tokens_of_each_kind(project, monkeypatch, tmp_path, capsys):
    now = time.time()
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"),
        *_answer(now - 50, "msg-1", "model-known", BIG, _text("writing it"), _text("written"))])
    _prices(monkeypatch, tmp_path)

    costs = A.turn_costs(cfg, by_model=True)
    estimate = A.usd_estimate(costs["tokens"], TABLE)

    # The response is two records repeating its usage, and its tokens count once.
    assert _of(costs["tokens"], "model-known") == _by_kind(BIG)
    assert [(row["model"], row["usd"], row["why"]) for row in estimate["rows"]] == [
        ("model-known", pytest.approx(BIG_USD), None)]
    assert estimate["total"] == pytest.approx(BIG_USD) and BIG_USD == pytest.approx(6.93)
    code, out = _usd_report(cfg, capsys)
    assert code == 0 and _line(out, "model-known").split() == ["model-known", "$6.93"]
    assert "cache_write_1h 400,000" in out and "cache_read 10,000,000" in out


def test_a_snapshot_id_the_table_lists_for_a_model_is_priced_as_that_model(project, monkeypatch, tmp_path):
    now = time.time()
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"), *_answer(now - 50, "msg-1", "model-known-20260901", BIG)])

    estimate = A.usd_estimate(A.turn_costs(cfg, by_model=True)["tokens"], TABLE)

    assert [(row["model"], row["usd"]) for row in estimate["rows"]] == [
        ("model-known-20260901", pytest.approx(BIG_USD))]


def test_cache_tokens_are_priced_at_their_own_rates_not_the_input_rate(project, monkeypatch, tmp_path):
    now = time.time()
    older = {"input_tokens": 0, "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 0,
             "output_tokens": 0}                   # written before the harness broke cache writes down by duration
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "read the repository"),
        *_answer(now - 50, "msg-1", "model-known", _usage(five=1_000_000), stop="tool_use"),
        *_answer(now - 40, "msg-2", "model-known", _usage(hour=1_000_000), stop="tool_use"),
        *_answer(now - 30, "msg-3", "model-known", _usage(read=1_000_000), stop="tool_use"),
        *_answer(now - 20, "msg-4", "model-known", older)])

    costs = A.turn_costs(cfg, by_model=True)
    row, = A.usd_estimate(costs["tokens"], TABLE)["rows"]

    assert row["tokens"] == {"cache_write_5m": 2_000_000, "cache_write_1h": 1_000_000, "cache_read": 1_000_000}
    # At the input rate the same four million tokens would read $12.00.
    assert row["usd"] == pytest.approx(2 * 3.75 + 6 + 0.3) and 2 * 3.75 + 6 + 0.3 == pytest.approx(13.80)
    assert sum(row["tokens"].values()) == costs["total"]


# ── what the table does not price is unknown, never zero ──

def test_a_model_the_table_does_not_price_is_unknown_never_zero(project, monkeypatch, tmp_path, capsys):
    now = time.time()
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"),
        *_answer(now - 50, "msg-1", "model-known", BIG, stop="tool_use"),
        *_answer(now - 40, "msg-2", "model-unlisted", _usage(fresh=5_000, written=900))])
    _prices(monkeypatch, tmp_path)

    estimate = A.usd_estimate(A.turn_costs(cfg, by_model=True)["tokens"], TABLE)

    assert [(row["model"], row["usd"], row["why"]) for row in estimate["rows"]] == [
        ("model-known", pytest.approx(BIG_USD), None), ("model-unlisted", None, "a model the table does not price")]
    assert estimate["total"] is None and estimate["priced"] == pytest.approx(BIG_USD)
    code, out = _usd_report(cfg, capsys)
    assert code == 0 and "unknown" in _line(out, "model-unlisted") and "$" not in _line(out, "model-unlisted")
    total = _line(out, "total (estimate)")
    assert total.split()[2] == "unknown" and "at least $6.93" in total and "$0.00" not in out


def test_a_model_id_that_would_break_a_line_is_printed_as_its_json_string(project, monkeypatch, tmp_path, capsys):
    now = time.time()
    forged = "model-known\n  total (estimate)       $0.00"
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"), *_answer(now - 50, "msg-1", forged, _usage(fresh=5_000))])
    _prices(monkeypatch, tmp_path)

    code, out = _usd_report(cfg, capsys)

    assert code == 0 and _line(out, json.dumps(forged)).endswith("unknown  a model the table does not price")
    assert [line.split()[-1] for line in out.splitlines() if line.strip().startswith("total")] == ["unknown"]


def test_a_kind_of_token_the_table_gives_a_model_no_price_for_leaves_it_unknown(project, monkeypatch, tmp_path):
    now = time.time()
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"), *_answer(now - 50, "msg-1", "model-known", BIG)])
    table = json.loads(json.dumps(TABLE))
    del table["vendors"]["vendor-a"]["models"]["model-known"]["cache_write_1h"]

    row, = A.usd_estimate(A.turn_costs(cfg, by_model=True)["tokens"], table)["rows"]

    assert (row["usd"], row["why"]) == (None, "the table gives it no price for cache_write_1h")


def test_a_response_at_a_rate_the_table_does_not_list_is_unknown_and_the_rest_is_priced(project, monkeypatch,
                                                                                        tmp_path, capsys):
    now = time.time()
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"),
        *_answer(now - 50, "msg-1", "model-known", _usage(fresh=1_000_000, speed="standard"), stop="tool_use"),
        *_answer(now - 40, "msg-2", "model-known", _usage(fresh=1_000_000, speed="fast"))])
    _prices(monkeypatch, tmp_path)

    estimate = A.usd_estimate(A.turn_costs(cfg, by_model=True)["tokens"], TABLE)

    assert [(row["model"], row["rate"], row["usd"]) for row in estimate["rows"]] == [
        ("model-known", None, pytest.approx(3.0)), ("model-known", 'speed "fast"', None)]
    assert estimate["rows"][1]["why"] == "a rate the table does not price" and estimate["total"] is None
    code, out = _usd_report(cfg, capsys)
    assert " ".join(_line(out, 'model-known at speed "fast"').split()) == \
        'model-known at speed "fast" unknown a rate the table does not price'
    assert "at least $3.00" in _line(out, "total (estimate)")


def test_a_model_served_free_is_priced_at_zero_only_because_the_table_lists_it_so(project, monkeypatch, tmp_path,
                                                                                  capsys):
    now = time.time()
    free = _usage(fresh=2_000_000, written=100_000)
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"), *_answer(now - 50, "msg-1", "model-free", free)])
    _prices(monkeypatch, tmp_path)
    unlisted = json.loads(json.dumps(TABLE))
    del unlisted["vendors"]["vendor-a"]["models"]["model-free"]
    tokens = A.turn_costs(cfg, by_model=True)["tokens"]

    assert [(row["usd"], row["why"]) for row in A.usd_estimate(tokens, TABLE)["rows"]] == [(0.0, None)]
    assert [(row["usd"], row["why"]) for row in A.usd_estimate(tokens, unlisted)["rows"]] == [
        (None, "a model the table does not price")]
    code, out = _usd_report(cfg, capsys)
    assert _line(out, "model-free").split() == ["model-free", "$0.00"]


# ── the estimate says it is one ──

def test_the_estimate_is_labelled_as_one_with_the_tables_version_and_each_vendors_day_and_source(project, monkeypatch,
                                                                                               tmp_path, capsys):
    now = time.time()
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"), *_answer(now - 50, "msg-1", "model-known", BIG)])
    _prices(monkeypatch, tmp_path)

    code, out = _usd_report(cfg, capsys)

    assert code == 0
    assert out.splitlines()[0].startswith("implementer spend in US dollars: an estimate")
    assert "list prices, not a bill" in out.splitlines()[0]
    assert _line(out, "total (estimate)").split()[-1] == "$6.93"
    assert "prices: table version 7; vendor-a as of 2026-09-01, https://example.com/prices" in out
    assert "unknown is never counted as zero" in out


# ── the same turns `ao cost` counts, under the same reading ──

def test_tokens_are_read_from_the_turns_ao_cost_counts_in_its_window(project, monkeypatch, tmp_path):
    now = time.time()
    early, late = _usage(fresh=1_000, five=2_000, read=30_000, written=400), _usage(fresh=7, hour=90, written=5)
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 7200, "write the parser"), *_answer(now - 7190, "msg-1", "model-known", early),
        _prompt(now - 600, "review it"), *_answer(now - 590, "msg-2", "model-known", late)])

    whole, window = A.turn_costs(cfg, by_model=True), A.turn_costs(cfg, since=now - 3600, by_model=True)

    assert _of(whole["tokens"], "model-known") == _by_kind(early, late)
    assert _of(window["tokens"], "model-known") == _by_kind(late)
    assert sum(window["tokens"].values()) == window["total"] == A.turn_costs(cfg, since=now - 3600)["total"]


def test_a_subagents_tokens_are_priced_at_its_own_model_once_per_response(project, monkeypatch, tmp_path):
    root, now = project["root"], time.time()

    def modelled(records, model):
        return [dict(record, message=dict(record["message"], model=model)) if record["type"] == "assistant"
                else record for record in records]
    cfg, _ = _delegating_world(project, monkeypatch, tmp_path, session=modelled(_delegating(root, now), "model-known"),
                               subagents={name: modelled(records, "model-sub")
                                          for name, records in _subagents(root, now).items()})

    tokens = A.turn_costs(cfg, by_model=True)["tokens"]

    # A subagent writes a response's records as it streams them, and each response counts at its last.
    assert _of(tokens, "model-known") == _by_kind(R1, R2, R3, R4)
    assert _of(tokens, "model-sub") == _by_kind(S1, S2, S3, S4, S1)


def test_a_harness_ao_never_shipped_has_its_tokens_priced_through_its_declaration(project, monkeypatch, tmp_path):
    declared = json.loads(json.dumps(FIXTURE))
    declared["telemetry"]["cost"].update(model="engine", tokens={"input": "tokens.in", "output": "tokens.out"})
    declared["billing"] = {"fallback": {"reading": "peak-per-turn"}}
    now = time.time()
    cfg, _ = _shape_world(project, monkeypatch, tmp_path, adapter=declared, records=[
        _event(now - 300, "round.open"),
        _event(now - 290, "meter", engine="model-known", tokens={"in": 1_000, "out": 100}),
        _event(now - 280, "meter", engine="model-known", tokens={"in": 4_000, "out": 900}),
        _event(now - 270, "round.close")])

    costs = A.turn_costs(cfg, by_model=True)

    # Each record is the running total of the turn in progress, so the turn costs the most they reached.
    assert _of(costs["tokens"], "model-known") == {"input": 4_000, "output": 900} and costs["total"] == 4_900
    assert A.usd_estimate(costs["tokens"], TABLE)["total"] == pytest.approx((4_000 * 3 + 900 * 15) / 1_000_000)


# ── where no dollar figure can be given, none is ──

def test_an_implementer_billed_in_credits_gets_no_dollar_figure(project, monkeypatch, tmp_path, capsys):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("".join(json.dumps({"timestamp": "2026-09-05T10:00:00Z", "payload": payload}) + "\n"
                                  for payload in ({"type": "turn_start"},
                                                  {"type": "usage_summary",
                                                   "promptTurnSummaries": [{"unit": "credit", "usage": 5.0}]},
                                                  {"type": "turn_end"})), encoding="utf-8")
    monkeypatch.setattr(A, "session_paths", lambda cfg: (str(transcript), None))

    code = cli.main(["-C", project["root"], "cost", "--usd"])
    out = capsys.readouterr().out

    assert A.turn_costs(project, by_model=True)["tokens"] is None
    assert code == 0 and out.startswith("no estimate in US dollars: kiro declares no tokens by model and kind")
    assert out.rstrip().endswith("`ao cost` counts its spend in its own unit, credit") and "$" not in out


def test_a_price_table_holding_a_mistake_prices_nothing(project, monkeypatch, tmp_path, capsys):
    def broken(change):
        table = json.loads(json.dumps(TABLE))
        change(table, table["vendors"]["vendor-a"], table["vendors"]["vendor-a"]["models"])
        return A.price_problems(table)

    assert broken(lambda table, vendor, models: models["model-known"].update(input=-3)) == [
        "model-known: `input` must be a price of zero or more"]
    assert broken(lambda table, vendor, models: models["model-known"].update(cache_writes=3)) == [
        "model-known: cache_writes is no kind of token `kinds` names"]
    assert broken(lambda table, vendor, models: vendor.update(as_of="September")) == [
        "vendor vendor-a: `as_of` must be the day its prices were read, as YYYY-MM-DD"]
    assert broken(lambda table, vendor, models: vendor.pop("source")) == [
        "vendor vendor-a: `source` must say where its prices were read"]
    assert broken(lambda table, vendor, models: models["model-free"].update(ids=["model-known"])) == [
        "model-known is priced twice, under vendor-a and vendor-a"]
    assert broken(lambda table, vendor, models: table.update(currency="EUR")) == [
        "`currency` must be USD: `ao cost --usd` estimates US dollars"]
    now = time.time()
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "write the parser"), *_answer(now - 50, "msg-1", "model-known", BIG)])
    table = json.loads(json.dumps(TABLE))
    table["version"] = 0
    _prices(monkeypatch, tmp_path, table)

    code, out = _usd_report(cfg, capsys)

    assert code == 1 and "`version` must be a whole number from 1" in out and "$" not in out


def test_the_table_ao_ships_is_sound_and_prices_the_models_its_adapters_run_by_default():
    table, problems = A.price_table()

    assert problems == [] and table["currency"] == "USD" and table["per_tokens"] == 1_000_000
    listed = {ident for block in table["vendors"].values() for model, entry in block["models"].items()
              for ident in [model] + entry.get("ids", [])}
    priced = [adapter for adapter in A.package_adapters().values()
              if A.transcript_shape(adapter)["tokens"] is not None]
    assert HARNESS in [adapter["id"] for adapter in priced]
    for adapter in priced:
        assert set(A.transcript_shape(adapter)["tokens"]["kinds"]) <= set(table["kinds"])
        assert {model for role, model in adapter.get("models", {}).items() if not role.startswith("_")} <= listed


# ── the declaration and the command line ──

def test_a_pricing_declaration_ao_cannot_read_whole_is_named_and_prices_nothing():
    shipped = A.load_adapter(HARNESS)
    cost = dict(shipped["telemetry"]["cost"], priced_when=[{"field": "message.usage.speed", "values": "standard"}])
    declared = dict(shipped, telemetry=dict(shipped["telemetry"], cost=cost))

    before = A.validate_adapter(shipped)

    assert A.token_problems(shipped["telemetry"]["cost"]) == []
    assert [problem for problem in A.validate_adapter(declared) if problem not in before] == [
        "`telemetry.cost.priced_when` must be a list of {field, values}, with values a list"]
    assert A.transcript_shape(shipped)["tokens"] is not None and A.transcript_shape(declared)["tokens"] is None


def test_usd_and_features_are_two_views_of_ao_cost_and_not_one(capsys):
    parser = cli.build_parser()

    assert parser.parse_args(["cost", "--usd", "--since", "24h"]).usd is True
    with pytest.raises(SystemExit) as refused:
        parser.parse_args(["cost", "--usd", "--features"])
    assert refused.value.code == 2 and "not allowed with argument" in capsys.readouterr().err


# ── what the review of USD-COST found ──

def test_each_anthropic_cache_read_is_the_multiple_of_its_input_the_pricing_page_states():
    """A cache hit costs a tenth of the input rate, except where the pricing page's footnotes say otherwise:
    a fortieth on Claude Fable 5.1 and Claude Mythos 5.1, a twentieth on Claude Opus 5.5 (read 2026-09-28)."""
    table, problems = A.price_table()
    assert problems == []
    models = table["vendors"]["anthropic"]["models"]
    multiple = {"claude-fable-5-1": 0.025, "claude-mythos-5-1": 0.025, "claude-opus-5-5": 0.05}

    for name, price in models.items():
        assert price["cache_read"] == pytest.approx(price["input"] * multiple.get(name, 0.1)), name


def test_a_breakdown_that_holds_only_the_hour_is_not_counted_again_as_five_minutes(project, monkeypatch, tmp_path):
    now = time.time()
    hour_only = {"input_tokens": 0, "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 0,
                 "output_tokens": 0, "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}}
    cfg, _ = _world(project, monkeypatch, tmp_path, records=[
        _prompt(now - 60, "read the repository"),
        *_answer(now - 50, "msg-1", "model-known", hour_only)])

    costs = A.turn_costs(cfg, by_model=True)

    assert _of(costs["tokens"], "model-known") == {"cache_write_1h": 1_000_000}
    assert sum(costs["tokens"].values()) == costs["total"] == 1_000_000


def test_a_turn_whose_records_name_two_models_keeps_both_under_peak_per_turn(project, monkeypatch, tmp_path):
    declared = json.loads(json.dumps(FIXTURE))
    declared["telemetry"]["cost"].update(model="engine", tokens={"input": "tokens.in"})
    declared["billing"] = {"fallback": {"reading": "peak-per-turn"}}
    now = time.time()
    cfg, _ = _shape_world(project, monkeypatch, tmp_path, adapter=declared, records=[
        _event(now - 300, "round.open"),
        _event(now - 290, "meter", engine="model-a", tokens={"in": 1_000}),
        _event(now - 285, "meter", engine="model-b", tokens={"in": 500}),
        _event(now - 280, "meter", engine="model-a", tokens={"in": 1_200}),
        _event(now - 270, "round.close")])

    costs = A.turn_costs(cfg, by_model=True)

    assert _of(costs["tokens"], "model-a") == {"input": 1_200} and _of(costs["tokens"], "model-b") == {"input": 500}


def test_a_kind_named_as_anything_but_a_plain_word_is_refused_and_prices_nothing():
    cost = {"from": "transcript", "model": "message.model",
            "tokens": {"input\n  total (estimate)  $0.00": "message.usage.input_tokens"}}

    assert "`telemetry.cost.tokens` names each kind in lowercase letters, digits and `_`" in A.token_problems(cost)
    assert A.token_shape(cost, {"reading": "sum"}) is None
