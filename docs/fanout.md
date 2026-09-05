# Fan-out budget

A coordinator that spawns sub-agents has a quota problem the watchdog cannot see:
the watchdog guards the *implementer's* pool, and a fan-out spends the
*coordinator's*. On 2026-09-05 a coordinator ran 47 verification agents in one
workflow. Eleven finished; thirty-six died with "session limit"; two million
tokens were spent and most of the answer was missing. Nothing refused it because
nothing was asked.

`ao fanout` is what should have been asked.

```bash
ao fanout ok --agents 47            # exit 1: too many, and why
ao fanout ok --agents 8             # exit 0 when the facts allow it
ao fanout record --agents 8 --done 8 --errors 0 --tokens 410000
ao fanout history
```

## The verdict

Three facts, in order. The first that fails names the verdict; the rest are
still listed.

| check | source | refuses when |
|---|---|---|
| hard cap | `.ao/config.json` → `fanout.max_agents` (default 12) | more agents than the cap — run batches, record each |
| recent limit hit | `.ao/ledger/fanouts.jsonl` | a recorded fan-out hit the provider limit inside the current window |
| window headroom | the provider window keyflip reports (the same line `ao status` shows) | less than `fanout.window_reserve_pct` (default 30) of the window is left |

An unreadable window is reported as unreadable, never as headroom: without
keyflip the gate is the cap and the history.

The token estimate (`agents × per_agent_tokens`) is printed, not gated on. A
percentage window cannot be turned into a token count honestly. It starts from
`fanout.per_agent_tokens` (default 50 000) and becomes empirical the moment a
run is recorded: the observed average across recorded runs replaces the default,
with errored agents counted as spenders — they were.

## Recording

`ao fanout record` after every run, whatever happened. `--note` matters: a note
containing *limit*, *quota*, *429* or *rate* alongside errors marks the run as a
limit hit, which is what refuses the next fan-out until the window resets.

## Over MCP

`ao_fanout {agents}` returns the same verdict; `ao_fanout {action: "record",
agents, done, errors, tokens, note}` records. A Claude coordinator running a
workflow can call either from the script before `parallel(...)` and after it.

## Configuration

```json
"fanout": {"max_agents": 12, "per_agent_tokens": 50000, "window_reserve_pct": 30}
```

All optional. The cap is deliberately below what a session can survive at full
window: the run that motivated this had a "medium" guideline of under 15 agents
and started 47.
