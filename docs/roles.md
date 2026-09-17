# Roles and actors

The two concepts are deliberately separate:

- A **role** is a *kind of thinking*: architect, implementer, reviewer, tester,
  bug-hunter, verifier, documenter.
- An **actor** is an *engine*: the orchestrating agent itself, or a named session of some
  agent CLI, with its own model and effort.

Keeping them apart is what lets you say *"bug-hunting moves to the sub-agent, development
comes back to me"* by editing one line, instead of rewriting a workflow.

## Actors

What ao reads is a table in `.ao/config.json`: `actors`, each the block a role runs with, and
`roles`, the actor that holds each of the three roles ao runs.

```jsonc
// .ao/config.json
{
  "actors": {
    "kiro": {"adapter": "kiro", "session": "auto", "model": "<model>", "effort": "max"},
    "lead": {"adapter": "claude-code", "session": "auto",
             "argv": ["claude", "--resume", "{session}", "-p", "{prompt}"]},
    "claude-code-reviewer-<model>": {"id": "claude-code-reviewer-<model>", "adapter": "claude-code",
                                     "argv": ["…"], "composed": true, "model": "<model>"}
  },
  "roles": {"implementer": "kiro", "architect": "lead", "reviewer": "claude-code-reviewer-<model>"}
}
```

`ao role set reviewer claude-code --model <model>` composed that reviewer from its adapter; a
reviewer's argv is never written by hand.

Not built yet: actors and roles in a file of their own, the orchestrator itself as an actor,
and the roles beyond implementer, architect and reviewer.

<!-- not built: ao reads actors and roles from .ao/config.json and runs three roles -->
```yaml
# .ao/roles.yml
actors:
  self:   { adapter: self }                                     # the orchestrator itself
  kiro:   { adapter: kiro, session: sess_a1b2c3…, model: gpt-5.6-sol, effort: max }
  hunter: { adapter: codex, model: gpt-5.1-codex-max, effort: high }
  scribe: { adapter: antigravity, model: gemini-3-pro, effort: low }
```

In the design `self` is a first-class actor: the orchestrator is not a supervisor that only
delegates — it takes roles like anything else, and which roles it takes is a configuration
choice.

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
ao role swap implementer reviewer    # exchange two roles' actors
```

A project without a table gets one from its role blocks the first time a role is set.
`ao role set` refuses an assignment that breaks separation of duties (the reviewer the same
actor, or the same declared family, as the implementer). The other roles in the table above
and the presets below are the shape it grows into; each new role must name the failure it
catches that no existing role catches.

A reassignment takes effect on the next slice. Work already in flight keeps its actor, so
you never orphan a half-finished lane.

## Address roles, never names

**The role is the address. The actor's name is display, and the adapter's name is a
launch detail. Neither belongs in routing.**

This is not a rule against naming products. `src/ao/adapters/` names seventeen of them on
purpose, and that list is the point: ao is not written between one particular pair of tools,
it has to work between whichever pair someone runs — and a third party must be able to add
the eighteenth without forking (#77). Naming a harness is correct exactly where a harness is
what you mean: an adapter definition, a support matrix, a launch argv. What is forbidden is
core logic that knows *which* harness it is talking to (#76), and routing that knows *which*
actor holds a role.

Reassignment is the whole point of the table above, and it is worthless if the plumbing
has an actor's name baked in. It had: the urgent-message filter tested for a literal spelled
from the architect actor's own name, so a message to the architect was recognised only while
that particular actor held the role; the same literal appeared in the watchdog's wake prompt,
in the hold-release filename and in the Telegram bridge, and `ao note --to` defaulted to a
hardcoded implementer name. Swap either actor and the routing silently addresses nobody.

The line to hold:

| may name an actor or a model | must resolve through the role |
|---|---|
| adapter identity (`adapter: "kiro"`, the `kiro-cli` binary, its session store, its account/usage probes) | who a message is for or from |
| what a report displays to a human | which messages a filter selects |
| a preset's default assignment | the default of `--to` and friends |
| test fixtures that pin one concrete actor | any prompt ao generates |

Adapters are named on purpose: launching `kiro-cli` requires knowing it is `kiro-cli`.
That is a statement about a *tool*, not about who holds a *role*, and swapping the
implementer must never require touching it.

## Tandem: roles rotate on a tool repository

On a repository that builds tooling for the agents themselves, roles may rotate per slice:
the architect implements one slice while the implementer reviews it, and the next slice
swaps back. Set `repository.kind` to `tool` and reassign with `ao role set` or `ao role
swap`; a reassignment still waits for the running slice to end. On a product repository,
the default, the architect does not implement: `ao role set implementer <architect>` is
refused unless a person names it a hotfix with `--hotfix`.

What never rotates is the invariant: **the reviewer is not the author of the candidate**.
The author is whoever holds the implementer role when the slice runs, resolved from the
table, so after a swap the reviewer that runs the new implementer's engine, or declares its
family, is refused for every grant - whichever actor that is.

A slice landed under a review waiver is reviewed after it lands, when the table may name
another implementer, or none. So the grant under the waiver records who landed it - the role
of a turn ao started, the actor holding that role, its adapter and the family declared for
it - and `ao catchup` judges the retrospective review's independence against that author, not
against whoever implements now: a reviewer that declares the author's family, or no family, is
refused, and the implementer's own engine may review what another family wrote. A family ao
could not record is named by a person, with `--author-family` and `--by`; ao never reads a
family from a model name. A split whose grant recorded its move proof, or that a person states
only moved code, has no reviewer to hold apart from its author: its waiver closes on the proof,
run on the commit that landed. A catch-up review is recorded as the waived slice's, whatever
slice runs while it is asked.

## The bug-hunter lane

The `bug-hunter` finds defects with no slice assigned. It runs as a read lane: no worktree,
no staging, no write to the repository, and it can never hold or influence a grant. Its
output is **leads, not verdicts**. `ao hunt` reads the next bounded slice of tracked files
(`hunter.files_per_run`, `hunter.bytes_per_run`) from a cursor that goes round the tree, and
mails at most `hunter.max_leads` new leads to the architect, who turns each into a backlog row or
discards it with `ao hunt discard <id>`. Each lead carries a fingerprint (file, symbol,
category), so a repeat is not sent again and a discard is remembered. A lead never blocks a
slice or raises a human alarm.

The hunter is configured as `hunter.argv` and refused unless it cannot write, by the same rule a
reviewer must meet. With the `hunter` feature switched on (it is off by default) the watchdog
starts one hunt every `hunter.every_hours`, detached, never continuously. `ao hunt status` and
`ao cost --features` count the runs, so its value can be judged against what it spends. A
cheaper model family suits it well: not good enough to hold a verdict, good enough to find
leads.

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
tried in order when the primary invocation is structurally unavailable. AO reports
permanent failures—missing/inaccessible executables, ordinary nonzero exits,
silence, and unknown failures—on the first occurrence and immediately advances.
Known non-transient spawn errnos (`ENOENT`, `ENOTDIR`, `EACCES`, `EPERM`,
`ENOEXEC`) map to the permanent spawn class; every other unrecognized OS error
maps to a separate unknown class that is also permanent and non-retryable. Only
timeouts, exit 75, `BlockingIOError`, and resource errnos that the running
platform actually exposes (`EAGAIN`, `EWOULDBLOCK`, `ENOMEM`, `EMFILE`, `ENFILE`,
`ETXTBSY`) are transient. Missing errno symbols are never assigned substitute
numbers, and unknown OS errors remain permanently closed. AO retries transient
route positions once after 30 seconds. Reviewer prose never changes that
classification.

An OS error that carries no errno stays in the unknown class on purpose. Without an
errno nothing tells a resource shortage that would clear from a fault that would
repeat, and a retry would spend a reviewer call and part of the review's budget on
a guess. Closing it costs one UNAVAILABLE review that a person or a later run can
repeat, which is the safe direction for a gate. The one errno-less failure treated
as transient is `BlockingIOError`, recognised by its type, because the type itself
names the resource condition. This is a decision with that cost, recorded here
rather than left to the order of the checks (backlog #101).

Reviewer executable discovery is bounded independently of environment cardinality:
AO examines at most the first 64 `PATH` directories, 32 built-in/version-manager
fallback directories, 16 Windows `PATHEXT` suffixes, and 8 distinct executable
candidates. A 30-second monotonic deadline starts before directory discovery; all
candidate `--version` waits share what remains, reserve the 5-second kill/drain
allowance, and retain the existing 25-second per-process ceiling. If the deadline
expires, AO keeps the first absolute executable already found rather than starting
another version subprocess.

Each invocation runs from a disposable non-repository directory with inherited Git
bindings removed, which prevents an accidental relative `git stash`, `git add`, or
file write from changing the live candidate. This is mutation containment, not a
sandbox. The selected reviewer's identity is still recorded in the review artifact
and marked when it came from a fallback. `ao init` and manual `ao doctor`
additionally issue an exact-nonce probe and report the selected route, resolved
binary, version, and reason; scheduled `doctor --check` stays static and spends no
reviewer quota.

Legacy configs prefer a different family but permit a fresh session of the
implementer's family as a last resort. In that mode, `reviewer.id` is the
operator's actor declaration: AO refuses an exact match with the implementer
session, while the nonce probe proves transport liveness only—not runtime actor
attestation. Comparing launcher binaries would wrongly collapse independent
sessions that share one CLI. Projects that need enforced declared separation opt
into the version-1 [capability matrix](capability-matrix.md): a reviewer with the
implementer's binding **or bound model family** is ineligible and is never
spawned. Family comes only from the bound model declaration, so inline family
fields cannot spoof independence. Runtime-unavailable eligible reviewers still
advance in order; malformed substantive output is `INVALID` and does not trigger
approval shopping. A schema-valid reviewer response is the review artifact. Every
unsuccessful response is terminal-only: repository review files, reviewer state,
notices, and strict evidence persist only AO-generated closed metadata such as kind,
exit code, binary, timeout, and attempt position. AO does not derive reset hints or
failure reasons from subprocess prose, and pattern-based diagnostic redaction is not
an authority mechanism.

If no reviewer is available, `ao review` exits 3 and writes a file whose verdict
is `UNAVAILABLE`. That file is not a round and never becomes NEEDS_CHANGES; the
implementer parks the review, and the watchdog nudges again when the window
reopens (`reviewer window reopened` in `ao watchdog explain`). `ao review
--commits <range>` reviews landed work after the fact — the way to close a
waiver.
