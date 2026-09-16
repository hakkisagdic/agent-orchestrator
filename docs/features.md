# Features and what each costs

ao's cost is a menu. Everything that spends a model's quota is a switch in
`.ao/config.json` (`"features": {…}`). All off, ao is a deterministic monitor — board,
mailbox, gates under the lock, commit authority bound to an immutable index candidate,
alarms, pings, hooks — and spends nothing.

What each switch costs is **measured, not estimated**: an earlier version of this page
carried percentages from one pilot, and nothing kept them true. `ao cost --features` reads
the implementer's transcript and the watchdog's own logs and prints, for the window it
covers, what each switch spent:

| switch | on by default | measured as | what it spends |
|---|---|---|---|
| `review` | yes | implementer turns that ran `ao review` | the reviewer model reads the slice diff, once or twice per slice |
| `inventory_review` | yes | counted with `review`; a transcript cannot tell them apart | one or two more reviews on slices that open a new surface |
| `nudge` | yes | turns a nudge started, within five minutes, that wrote no product | implementer turns; only the empty ones are overhead |
| `architect_wake` | yes | wakes started, from the watchdog's log; the architect's pool, not priced here | one architect turn per batch of anomalies or decisions |
| `refill` | yes | refills started, likewise | one architect turn when the queue is empty |
| `reports` | yes | turns that only coordinated: inbox, report, board, writers | a few implementer tool calls per slice |

The reviewer's own spend is the reviewer's pool and is not in the implementer's
transcript either. `ao features` shows the last seven days beside each switch; if one
is well above what you expect, something is looping — `ao cost --since 24h` names the
turn class, `ao watchdog explain` the guard.

```bash
ao features                      # the switches, and what each spent in the last 7 days
ao cost --features --since 30d   # the same, over a window you choose
ao features off review           # candidate-bound gates still decide; review is skipped
ao features off architect_wake   # anomalies are written and alarmed, nobody is woken
```

What each switch changes when off:

- `review` off — `ao commit-ok` no longer requires an APPROVED review; the
  staged candidate must still be isolated and exactly match its verification,
  and `ao review` still works when asked.
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
waiver; `ao commit-ok` honours it only for a matching running slice, and the
pre-commit `ao commit-check` requires that waiver to remain open when the grant
names no review. `ao catchup` later reviews the landed range with `ao review
--commits` and closes the waiver, or writes the architect a decision request when
the retrospective review finds problems. Retrospective evidence reconciles the
record; it never authorizes a candidate. Nothing is skipped silently, and
nothing is lost when the run degrades:

- deferred nudges and wakes (quota) are queued in `.ao/ledger/deferred.jsonl`
  and replayed by `ao catchup`;
- the credit burn rate is sampled every half hour; when it says the plan runs
  out before it resets, the alarm is red, days ahead;
- `ao pings setup --url …` gives an external service (healthchecks.io) a
  heartbeat from the watchdog and the doctor job — when both die, that service
  e-mails you, which nothing on the dead machine can;
- `.ao-project` is the tracked enrollment boundary: only exact
  `ao-project-v1\n` bytes in HEAD or the active index activate enforcement. An
  incidental `.ao/` directory stays inert. A staged marker supports first
  adoption; a staged deletion remains enrolled through HEAD. Once enrolled,
  `.ao/config.json` must be a valid, non-empty top-level JSON object or every
  commit is refused with an `ao init --profile claude-kiro` repair. Its shared
  pre-dispatch/enforcement reader consumes at most 1,048,576 bytes and rejects
  container nesting deeper than 64 before recursive JSON decoding.
- `ao hooks status` asks Git for the effective hook path and reports its class,
  winning `core.hooksPath` scope/origin/value, each role's static and track
  state, and misplaced AO forms. Exact current bytes remain labelled `behavior
  unverified`; a separate `installed (execution proved)` result exists only when
  `git hook run pre-commit` carries AO's isolated synthetic index to
  `commit-check` and receives the nonce-bound fail-closed response. Status,
  doctor, `doctor --check`, and init all use that same runtime result.
- `ao hooks install` treats pre-commit and pre-push independently. Pre-commit
  intends to revalidate the persisted grant against Git's active index; `ao
  push allow` opens the human push window for thirty minutes. Foreign,
  ambiguous, symlinked, tracked, and indeterminate targets are never
  overwritten. One eligible role may be installed while a custom other role is
  preserved, reported unavailable, and makes install return 1.
- Install and uninstall preflight their complete mutation sets. `ao remove --yes`
  is additionally two-phase: while the canonical marker remains in HEAD or the
  active index it removes only the working-tree `.ao-project` and keeps state and
  enforcement; after the authorized marker-deletion commit, a second invocation
  may remove state. If any eligible hook target is shared, external, or selected
  by global/system config, the command refuses every hook mutation unless
  `--allow-shared-hooks` is explicit. The flag never authorizes foreign or
  protected content.
