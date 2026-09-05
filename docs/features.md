# Features and what each costs

ao's cost is a menu. Everything that spends a model's quota is a switch in
`.ao/config.json` (`"features": {…}`), and `ao features` prints the estimate
for the current switches. All off, ao is a deterministic monitor — board,
mailbox, gates under the lock, commit authority by digest, alarms, pings, hooks
— and spends nothing. All on, about a quarter of the implementer's spend.

| switch | on by default | share | what it spends |
|---|---|---|---|
| `review` | yes | ~8% | the reviewer model reads the slice diff, once or twice per slice |
| `inventory_review` | yes | ~4% | one or two more reviews on slices that open a new surface; none on fix slices |
| `nudge` | yes | ~4% | implementer turns; only the empty ones are overhead |
| `architect_wake` | yes | ~3% | one architect turn per batch of anomalies or decisions |
| `refill` | yes | ~2% | one architect turn when the queue is empty |
| `reports` | yes | ~2% | a few implementer tool calls per slice (start / done / blocked) |

The shares were measured on the first pilot with `ao cost` and are refined by
it: `ao features` shows the measured share of the last seven days beside the
estimate. If the measured number is well above the estimate, something is
looping — `ao cost --since 24h` names the class, `ao watchdog explain` the
guard.

```bash
ao features                      # the table and the estimate
ao features off review           # commit-ok then needs gates and digest only
ao features off architect_wake   # anomalies are written and alarmed, nobody is woken
```

What each switch changes when off:

- `review` off — `ao commit-ok` no longer requires an APPROVED review; `ao
  review` still works when asked.
- `inventory_review` off — the playbook and backlog rule stop asking for an
  inventory review; the surface inventory itself is still good practice.
- `nudge` off — the watchdog measures, alarms and records, but never starts an
  implementer turn; a person starts the implementer.
- `architect_wake` off — anomaly files and alarms only; a person reads them.
- `refill` off — an empty queue is an orange alarm, not a wake.
- `reports` off — the implementer's steering asks for reports only at slice end.

## Bypass, on the record

Sometimes the switch is not the answer: the reviewer is out of quota for two
hours and the slice is ready. `ao waive review --slice B7 --why "…"` records a
waiver; `ao commit-ok` honours it and names it; `ao catchup` later reviews the
landed range with `ao review --commits` and closes the waiver, or writes the
architect a decision request when the retro review finds problems. Nothing is
skipped silently, and nothing is lost when the run degrades:

- deferred nudges and wakes (quota) are queued in `.ao/ledger/deferred.jsonl`
  and replayed by `ao catchup`;
- the credit burn rate is sampled every half hour; when it says the plan runs
  out before it resets, the alarm is red, days ahead;
- `ao pings setup --url …` gives an external service (healthchecks.io) a
  heartbeat from the watchdog and the doctor job — when both die, that service
  e-mails you, which nothing on the dead machine can;
- `ao hooks install` makes push a human window: `ao push allow` opens it for
  thirty minutes, the pre-push hook refuses otherwise.
