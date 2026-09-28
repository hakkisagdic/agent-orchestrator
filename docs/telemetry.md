# Telemetry — quota, credits and context

You should not have to open each agent's own UI to find out how much context it has left
or what a turn cost. Most of that is already on your disk; you just have to know where.

## Three sources, in order of preference

**1. Transcript-derived — free, local, always available.**
Agents write their own accounting into the session store. No API call, no credentials, no
rate limit. This is the source to exhaust first.

Verified for Kiro CLI:

```jsonc
// context window usage
{"type":"session_metadata","key":"contextUsage","value":{"usagePercentage":67.5}}

// per-turn cost, in the vendor's own unit
{"type":"usage_summary","promptTurnSummaries":[
  {"unit":"credit","usage":354.82,"usedTools":["read_file","execute_bash", …]}]}
```

Each turn writes one `usage_summary`, carrying that turn's whole cost, so a session's spend is
those records added up: the adapter declares the reading `sum` (`billing.fallback.reading`).

From those two records alone you get: context pressure, cost of the last turn, session
total, average burn per turn, and tool-call volume. In a real session that read
**355 credits for one turn with 540 tool calls** — which tells you something no
"working…" spinner ever will.

**1b. Call-return — free, and richer than a transcript.**
CLIs that answer in JSON hand you the accounting directly, per turn:

```json
"usage":{"input_tokens":21140,"output_tokens":1489,"thinking_tokens":1258,
         "cache_read_tokens":0,"total_tokens":22629}, "duration_seconds":18.8
```

Capture it when you make the call and append it to the event log. No format
archaeology, no polling, and `thinking_tokens` and `cache_read_tokens` are visible —
signals a passive transcript rarely exposes.

Watch the input side. In a verified run a two-word prompt consumed **21,140 input
tokens**: the preamble being injected before it cost three orders of magnitude more than
the request. That is a configuration problem the number makes visible immediately.

**2. CLI-derived — a subprocess, cache it.**
Cross-provider quota is [keyflip's](keyflip.md) job:

```
$ keyflip usage --providers
  Claude (Anthropic)  69%   5h   resets in 49m
  Codex CLI (OpenAI)  unknown
```

Never call this on every dashboard refresh. Cache for five minutes; quota windows move in
hours, not seconds, and a slow subprocess in the render loop makes the panel feel broken.
`ao` keeps a reading in `~/.ao/quota.json` under the command that took it, for the adapter's
`cache_seconds`, so `ao status`, the live panel and every watchdog cycle inside that window read
one answer instead of each starting the command again. A command that fails is not kept: the
next read asks again. The command runs without a shell when its program is on ao's binary
search path: PATH, then the directories tools are usually installed in. The provider window a
fan-out verdict or an account rotation checks is read from the same reading, and a rotation
drops it so the next read asks again. keyflip's budget status (`keyflip budget status --json`)
is kept in the same file for the same window.

**3. Remaining balance — we do not guess it.**
Plan balance is fetched by vendor UIs at render time and is not written to disk. Scraping
it or extrapolating from a user-entered total produces a confident number that is wrong
often enough to be worse than nothing — and you would still have to check the real UI.

So there are exactly two supported answers, and the tool says which one is active:

- **Install [keyflip](keyflip.md)** and get real per-provider quota windows
  (`Claude 69% · 5h · resets in 49m`). Recommended if you care about limits.
- **Transcript only** — context percentage, per-turn cost and burn rate, with no balance
  line at all. Zero install, entirely honest.

`ao` never displays an estimated remaining balance.

## Whose account

A credit account belongs to a harness, and every reading of one is the implementer's own.
`ao credits`, the credits section of `ao digest`, `ao handoff`, the account line of `ao cost`
and the watchdog's credit sampler each ask the lookup the implementer's adapter declares
(`billing.api.driver`), and the offline estimate reads only that adapter's transcripts
(`billing.fallback.transcripts`). Both are read from the adapters ao ships, never from a layer
an agent can write, since a lookup runs the command its adapter names. Until 2026-09-17 every
reader but `ao cost` read the first shipped adapter's account whatever the implementer ran, so
an implementer on a harness billed in tokens could be shown, sampled and alarmed on a credit
account it never spends.

An implementer whose adapter declares no lookup is told so where the figure would stand
("… declares none ao can read"), and gets no credit samples and no credits alarm. Every sample
in `.ao/ledger/credits.jsonl` names the account it is of and the adapter whose lookup took it.
Only that adapter's samples give the implementer its burn rate, its exhaustion projection and
the credits finding, page and line of `ao doctor`. A sample that names no adapter is nobody's,
and so is every sample written before samples named one.

## In US dollars: an estimate

`ao cost` counts spend in the unit the implementer's harness bills - credits, or tokens - and a
token count weighs every token alike, though Anthropic, for one, prices a cache read at a tenth of
an input token - a fortieth on Claude Fable 5.1 and Claude Mythos 5.1, and a twentieth on Claude Opus
5.5, as its pricing page's footnotes say - and an output token at five times one. `ao cost --usd` prices the same turns'
tokens by model and by kind, from a price table ao ships:

```bash
ao cost --usd                # the whole transcript
ao cost --usd --since 24h    # the turns `ao cost --since 24h` counts
```

```text
implementer spend in US dollars: an estimate  (list prices, not a bill; last 24h)
  claude-opus-5                                   $11.55
    input 10,000 · cache_write_5m 200,000 · cache_write_1h 400,000 · cache_read 10,000,000 · output 50,000
  claude-opus-5 at speed "fast"                  unknown  a rate the table does not price
    input 1,000 · output 90
  total (estimate)                               unknown  at least $11.55, what the rows priced add up to
  prices: table version 1; anthropic as of 2026-09-26, https://platform.claude.com/docs/en/about-claude/pricing
  unknown is never counted as zero: a model, a kind of token or a rate the table does not price
```

**Where the tokens come from.** Beside the usage `ao cost` already reads (`telemetry.cost`), the
implementer's adapter declares the path to the model that answered (`model`), the path to its count
of each kind of token the table prices (`tokens`), and the fields whose values say a response was
billed at list prices (`priced_when`). They are read from the same records, under the same reading
(`billing.fallback.reading`) and in the same window as `ao cost`, subagents included, so a response
counts once however many records repeat it. A kind may name a list of paths, and the first that
holds a count is read: Claude Code breaks its cache writes down by how long the cache lives, and a
record written without that breakdown is read as five-minute writes, the default duration.

```jsonc
// telemetry.cost of Claude Code's adapter, beside the usage it already declares
"model": "message.model",
"tokens": {
  "input": "message.usage.input_tokens",
  "cache_write_5m": ["message.usage.cache_creation.ephemeral_5m_input_tokens",
                     "message.usage.cache_creation_input_tokens"],
  "cache_write_1h": "message.usage.cache_creation.ephemeral_1h_input_tokens",
  "cache_read": "message.usage.cache_read_input_tokens",
  "output": "message.usage.output_tokens"
},
"priced_when": [
  { "field": "message.usage.speed", "values": ["standard", null] },
  { "field": "message.usage.service_tier", "values": ["standard", null] },
  { "field": "message.usage.inference_geo", "values": ["global", "not_available", null] }
]
```

**The table** is `src/ao/prices.json`. Each price is a vendor's published list price in US dollars
for `per_tokens` (a million) tokens of one kind `kinds` names: `input`, `cache_write_5m`,
`cache_write_1h`, `cache_read` and `output`. Each vendor says when its prices were read (`as_of`),
where (`source`) and what they do not cover (`scope`); each model lists its price for each kind, and
`ids` names the other ids a transcript may write it as, such as a dated snapshot. `version` counts
the table's changes. ao refuses a table holding any mistake - a negative price, a kind `kinds` does
not name, an id priced twice, a vendor without its day or its source - and then prints no figure.

```json
"claude-opus-5": { "input": 5, "cache_write_5m": 6.25, "cache_write_1h": 10, "cache_read": 0.5, "output": 25 }
```

**The label.** Every figure says it is an estimate: the heading reads *an estimate* and *list
prices, not a bill*, the total reads *total (estimate)*, and the last lines name the table's version
and, for each vendor whose prices were used, the day they were read and the page they came from. A
bill differs from it by what the table does not cover: negotiated discounts, a plan paid by
subscription rather than by the token, fast mode, the Batch API, US-only inference, Amazon Bedrock
and Google Cloud, and server tools charged per request, such as web search.

**Unknown is never zero.** A model the table does not list, a kind of token its entry gives no price
for, and a response billed at a rate `priced_when` does not list - fast mode, another service tier,
US-only inference - each make a row that reads `unknown`, and says why. The total is then unknown
too, and says what the priced rows add up to, as a floor: *at least $11.55*. A model served free - a
free tier, a local model - is priced at zero only by an entry that lists it at zero, whose `note`
says why; ao never reads the cost a harness writes into its own records, such as the 0.0 of a
provider that bills elsewhere. An implementer billed in credits gets no dollar figure at all: a
credit's price in dollars is its plan's, and no table here holds one.

**Updating the table.** Prices change, and the table does not follow them by itself:

1. Read the vendor's pricing page, the one its `source` names.
2. Change the prices that moved, add each model the transcripts now name with the other ids it is
   written as, and set the vendor's `as_of` to the day you read the page. A new vendor needs its
   `as_of`, `source` and `scope` too.
3. Raise `version` by one.
4. Run `tests/test_usd_cost.py`: it refuses a table ao could not quote from, and one that leaves a
   model a shipped adapter runs by default without a price.

*In ao since slice USD-COST: `ao cost --usd` prices the implementer's tokens from src/ao/prices.json,
by model and kind, under the reading and in the window `ao cost` counts them, and labels the figure an
estimate at list prices, naming the table's version and each vendor's as-of day and source. What the
table does not price is unknown, never zero, and a model is free only where the table lists it at
zero. Claude Code's adapter declares its tokens (`telemetry.cost.model`, `tokens`, `priced_when`); a
harness billed in credits gets no dollar figure.*

## Adapter block

```jsonc
"telemetry": {
  "context": { "from": "transcript", "type": "session_metadata",
               "match": {"key": "contextUsage"}, "field": "value.usagePercentage" },
  "cost":    { "from": "transcript", "type": "usage_summary",
               "field": "promptTurnSummaries[].usage", "unit": "credit" },
  "quota":   { "from": "command", "argv": ["keyflip","usage","--providers"], "cache_seconds": 300 },
  "balance": { "from": "none", "note": "not exposed locally; never estimated" }
}
```

Every field is optional. An adapter with no telemetry block simply shows nothing — the
panel degrades, it does not break.

## Adding a new signal

1. **Find it.** Run one distinctive turn, then grep the agent's session store for a value
   you saw in its UI. Vendors almost always write more into the transcript than they
   render.
2. **Classify it.** Transcript, command, or API — that decides the cost of reading it.
3. **Declare it** in the adapter's `telemetry` block.
4. **Give it a threshold.** A number without a threshold is decoration. Context gets a bar
   that turns yellow at 70% and red at 85%; burn rate gets compared against the budget.

## Thresholds that earned their place

| Signal | Warn | Act |
|---|---|---|
| Context usage | 70% | 85% — start a fresh session before quality degrades |
| Provider quota window (keyflip) | 80% | 95% — rotate the account or stop dispatching |
| Burn per turn | 2× session average | 5× — something is looping; look at the tool list |

The third one is worth the trouble. A turn that costs five times the average is almost
never five times more valuable; it is usually an agent retrying a failing command in a
loop, and the tool-call list in `usage_summary` shows you which one.

## Notices, and why they are recorded

A desktop notification reaches the human and vanishes. The architect reading the
panel — the participant who could act on a *pattern* of alerts — is then the only
one who never sees what the human was told. `ao notices` closes that: every alert
is written to `.ao/ledger/notices.jsonl` as it is raised.

Alerts are rate-limited per key, default thirty minutes. The watchdog runs every
two minutes, so a guard that notified on each run would turn one ongoing condition
into thirty alerts an hour; a human who learns to swipe those away has turned the
alerting off while everyone still believes it works.

Suppressed alerts are recorded too, with `sent: false`. A long run of them is
itself the signal — it says the condition has held for a long time, which a single
delivered notification cannot express. `ao notices --all` shows them.

*In ao since slice JSON-OUTPUT: `ao notices --json` prints the list as one JSON document and nothing
else on stdout, `{"notices": […], "include_suppressed": false}`, newest first, `-n` and `--all`
choosing the rows as they do for the text. `ao notices <id> --json` prints `{"notice": {…}}`, and for
an id no notice has, `{"notice": null, "error": "…"}` with exit status 1. A notice has `id`, `at`
(epoch seconds), `title`, `msg`, `sent` (false for one the rate limit held), `key` and `evidence`
(`check` and its `samples`, each a `value`, its `source` and `at`), null where a row recorded before
ao kept it has none.*

## Keeping the records small

Everything here grows monotonically: the progress ledger gains a row every couple
of minutes, and one nudge log reached 295 KB overnight. Unbounded growth is how a
tool becomes the thing someone turns off.

```bash
ao prune                 # dry run: what would go
ao prune --yes           # operational records older than 7 days, logs trimmed to 64KB tails
ao prune --evidence --yes   # also seal the verification and authority ledgers past their bound
```

Evidence is excluded by default and takes an explicit flag. Verification records
and plan hashes are what commit authority was granted against — deleting them as
housekeeping would quietly remove the ability to answer "on what basis did this
land". Log trimming keeps the tail rather than the head, because the last turn's
output is the part anyone actually reads when diagnosing a failed nudge.

The machine's event log, `~/.ao/events.jsonl`, needs no pruning: it holds itself to
`retention.events_kb` as it is written ([surfaces.md](surfaces.md#the-event-log)).

Review artefacts are evidence too, and they are kept by reference. One that a grant, a
verification, the board, a waiver or a decision names, one of a slice still open or of
the candidate staged now, and one git tracks never moves. The rest leave once older than
`review.prune_after_days` (30), to `~/.ao/archive/<project>/` rather than away, and
`ao prune` says how many it kept and why; if any of those references cannot be read,
nothing moves. `ao doctor` names a review a grant rests on that git does not hold -
evidence on one disk is one disk failure from gone - so commit those.
