# Roles and actors

The two concepts are deliberately separate:

- A **role** is a *kind of thinking*: architect, implementer, reviewer, tester,
  bug-hunter, verifier, documenter.
- An **actor** is an *engine*: the orchestrating agent itself, or a named session of some
  agent CLI, with its own model and effort.

Keeping them apart is what lets you say *"bug-hunting moves to the sub-agent, development
comes back to me"* by editing one line, instead of rewriting a workflow.

## Actors

```yaml
# .ao/roles.yml
actors:
  self:   { adapter: self }                                     # the orchestrator itself
  kiro:   { adapter: kiro, session: sess_a1b2c3…, model: gpt-5.6-sol, effort: max }
  hunter: { adapter: codex, model: gpt-5.1-codex-max, effort: high }
  scribe: { adapter: antigravity, model: gemini-3-pro, effort: low }
```

`self` is a first-class actor. The orchestrator is not a supervisor that only delegates —
it takes roles like anything else, and which roles it takes is a configuration choice.

## Roles

| Role | Owns | Typical actor |
|---|---|---|
| `architect` | Contracts, boundaries, design decisions, spec text. | strong reasoning |
| `implementer` | Writes the code for one closed slice. | fast, high-context |
| `reviewer` | Semantic review against the contract. | **different family** from implementer |
| `tester` | Adversarial and fault-injection tests. | thorough, patient |
| `bug-hunter` | Finds defects in existing code with no slice assigned. | different family again |
| `verifier` | Re-runs gates independently; grants commit authority. | cheap; it runs commands |
| `documenter` | README, spec, changelog. | cheap model, low effort |

```yaml
roles:
  architect:   self
  implementer: kiro
  reviewer:    self
  tester:      kiro
  bug-hunter:  hunter
  verifier:    self
  documenter:  scribe
```

## Reassigning

```bash
ao role                              # current assignment table
ao role set implementer self         # take development yourself
ao role set bug-hunter kiro          # push bug-hunting to the sub-agent
ao role swap implementer reviewer    # exchange two roles' actors
ao role preset pair                  # apply a named preset
```

A reassignment takes effect on the next slice. Work already in flight keeps its actor, so
you never orphan a half-finished lane.

## Presets

```yaml
presets:
  solo:      { all: self }                                    # no sub-agent at all
  pair:      { implementer: kiro, rest: self }                # the default
  hunt:      { bug-hunter: kiro, implementer: self }          # inverted: you build, it hunts
  factory:   { implementer: [kiro, agy], reviewer: self }     # parallel implementers
  audit:     { reviewer: [self, hunter], implementer: none }  # review-only, nothing writes
```

## The separation-of-duties invariant

**The reviewer must not be the same actor as the implementer for the same slice.**

`ao` refuses that assignment rather than warning about it. A model reviewing its own work
shares its own blind spots, and the whole reason for a second agent evaporates. When only
one engine is available, the reviewer must at least be a different *model family* on that
engine — enforced through the role's model policy, not by trust.

The same rule applies between `implementer` and `verifier`: whoever wrote the code does
not get to decide the gates passed.

## Why family diversity matters

In the run this project came out of, two consecutive approvals from the same reviewer
missed a real crash-recovery defect. An adversarial test written afterwards found it in
minutes. Different instruments, different blind spots — the role table is how you
guarantee you are using more than one.

Set it explicitly:

```yaml
role_models:
  implementer: { family: openai }
  reviewer:    { family: anthropic, not_family_of: implementer }
  bug-hunter:  { family: google }
```

## Heavy operations belong to one actor

Roles split *who decides*. They must also split *who spends the machine*, and that
second split is easy to miss until it breaks something.

A run on one laptop hit this directly. The implementer's slices were not failing on
logic; they were failing on contention. It had a 330-test suite, a shared 16 GB
machine, and other agents on it — so it spent five review rounds walking a
concurrency setting from 8 down to 1, killing orphaned runners from earlier attempts,
and re-running the suite to get one trustworthy number. None of those rounds produced
a line of product code, and every one of them counted against the slice's round
budget as if it had.

Adding parallel lanes there makes it strictly worse: each lane runs the same suite,
and they contend with each other instead of with the background.

So separate the work by weight, not only by role:

| Work | Where |
|---|---|
| Writing code, reading code, designing, reviewing | any lane, in parallel |
| Full test suites, builds, containers, benchmarks | **one actor, serialised** |

The lanes stay light and parallel; a single actor merges and runs the expensive
gates once, on a machine it is not fighting for. `ao verify` is that actor's
instrument — it already serialises the `full` profile for this reason.

The rule generalises past tests. Anything that saturates a shared resource — a
database restore, a Docker image build, an integration environment — is a
centralised operation, and treating it as parallelisable work costs rounds without
producing any.

## When the reviewer cannot review

`reviewer.fallbacks` in `.ao/config.json` is a list of further reviewer blocks
tried in order when the primary is unavailable — out of quota, not logged in,
timed out, or silent. The identity of whoever actually reviewed is recorded in
the review file, marked as a fallback. A reviewer from a different family is
preferred; a fresh session of the implementer's family with no tools is an
acceptable last resort and still not the implementer reviewing itself.

If no reviewer is available, `ao review` exits 3 and writes a file whose verdict
is `UNAVAILABLE`. That file is not a round and never becomes NEEDS_CHANGES; the
implementer parks the review, and the watchdog nudges again when the window
reopens (`reviewer window reopened` in `ao watchdog explain`). `ao review
--commits <range>` reviews landed work after the fact — the way to close a
waiver.
