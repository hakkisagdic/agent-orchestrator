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

## Keeping the records small

Everything here grows monotonically: the progress ledger gains a row every couple
of minutes, and one nudge log reached 295 KB overnight. Unbounded growth is how a
tool becomes the thing someone turns off.

```bash
ao prune                 # dry run: what would go
ao prune --yes           # operational records older than 7 days, logs trimmed to 64KB tails
ao prune --evidence --yes   # also verification records and plan hashes
```

Evidence is excluded by default and takes an explicit flag. Verification records
and plan hashes are what commit authority was granted against — deleting them as
housekeeping would quietly remove the ability to answer "on what basis did this
land". Log trimming keeps the tail rather than the head, because the last turn's
output is the part anyone actually reads when diagnosing a failed nudge.
