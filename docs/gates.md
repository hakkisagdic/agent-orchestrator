# Gates

A gate is a check the **verifier runs itself**. Not a check the implementer reports having
run — that distinction is the whole point, and everything else here follows from it.

## Declaring them

```yaml
# .ao/gates.yml
gates:
  typecheck:      { run: "npm run typecheck", expect: exit_zero }
  focused:        { run: "node --test test/{slice_tests}", expect: all_pass }
  full:           { run: "npm test", expect: all_pass, timeout: 30m, serialise: true }
  diff-check:     { run: "git diff --check", expect: exit_zero }
  artifact-sweep: { run: "ls -d .tmp-* scratch-* 2>/dev/null", expect: empty }

profiles:
  quick: [typecheck, focused, diff-check]      # during iteration
  full:  [typecheck, focused, full, diff-check, artifact-sweep]   # before commit authority
```

Declared once, in the repository, so a gate is not something the orchestrator improvises
differently each time it asks.

## The artifact sweep earns its place

That fifth gate looks like housekeeping and is not. In the source run, fault-injection
tests left four scratch directories in the repository root. The implementer's own file
search did not report them — dot-directories whose contents are git-ignored are invisible
to most agent search tools — so it truthfully reported a clean tree.

An agent cannot reliably see its own mess. `ls -d` can. Sweep from the outside.

## Running

```bash
ao verify                 # profile: full. Runs the gates, writes a verification record.
ao verify --profile quick
ao verify --slice claim-admission
```

Two properties that are not negotiable:

- **The verifier executes the commands.** It never parses a claim out of a report. Reports
  are usually accurate; a system whose correctness depends on that has no check at all.
- **The result is recorded** in `.ao/ledger/verifications.jsonl` with the exact numbers and
  the actor who measured them ([`ledger.md`](ledger.md)).

## Commit authority is bound to a verification

```bash
ao commit-ok --verification V-041 --files "src/…,test/…" --message-file msg.txt
```

`ao` refuses to grant commit authority against a stale verification — one taken before the
last file change — or against a verification produced by the same actor that wrote the
code. Both refusals are enforced, not advised; see separation of duties in
[`safety.md`](safety.md).

Push is not a gate outcome and never becomes one. It stays a direct human act.

## Serialisation and machine pressure

Lanes think in parallel; gates do not run in parallel. `serialise: true` marks the
expensive ones, and `ao` holds a machine-wide lock across them.

Before starting any gate run it also checks memory pressure and swap-in rate, and **refuses
rather than thrashes**. Five simultaneous test suites will make a laptop unusable while
each one individually looks reasonable — and the failure mode is a machine that appears
hung, which is the most expensive kind of confusion to debug.

A word on measurement, learned the hard way: do not gate on swap *usage*. macOS does not
release swap files after a heavy job, so an idle machine can read 93% and the gate is red
forever — and a gate that is always red teaches everyone to ignore gates, which is worse
than having none. Gate on **swap-ins per second**, which reflects present pressure.

## Reporting a failed gate

A red gate is data, not an accusation. The record keeps the exact output, the slice returns
to `changes-requested`, and the round counter increments — which is how a slice that cannot
converge eventually trips its budget in [`slices.md`](slices.md) instead of quietly
consuming an afternoon.


## The board carries edges, and the review loop is measured

`needs: B3, B4` on a queued item is a dependency: the item becomes READY the moment
those land. `unlocks:` is the same edge written from the other end. This is the
useful core of "backend done, now the frontend" — the next item becomes eligible
without the implementer choosing its own scope, which is the one authority it must
not hold. `role:` tags an item for a particular actor; routing is a detail on top
of the graph, not a mechanism of its own.

A round budget counts rounds. It cannot see that round four's blocker is round
two's blocker with the line numbers moved — the actual shape of a slice that is
not converging. `ao` fingerprints each finding on its file and first clause across
consecutive NEEDS_CHANGES reviews and raises a `review-loop` anomaly when one
recurs three times: more rounds will not fix it; it needs re-specifying or a
different actor.

`ao digest` reads all of this back from the ledgers rather than from memory.
Refusals repeated across a week are surfaced as a process signal, because a
commit-ok refused five times for "re-run ao verify" is not five incidents — it
is one habit.
