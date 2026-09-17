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
`ao role set` refuses an assignment that breaks separation of duties: the reviewer the same
actor as the implementer, or a reviewer no [review tier](#review-tiers) admits, refused in the
words `ao review` would use. The other roles in the table above
and the presets below are the shape it grows into; each new role must name the failure it
catches that no existing role catches.

A reassignment takes effect on the next slice. Work already in flight keeps its actor, so
you never orphan a half-finished lane. Until the running slice leaves, `ao role` shows the
assignment in force and the one that waits; another reassignment joins the one that waits
and is checked against it; and where a reviewer waits, `ao review` and `ao catchup` name it
rather than report that none is configured.

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
refused, and the implementer's own engine may review what another family wrote. The review's
header names the implementer configured when it runs, or says that none is, and that the range
is judged against its author. A family ao could not record is named by a person, with
`--author-family` and `--by`; ao never reads a
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
one harness is available, the [review tiers](#review-tiers) say what may stand in: another
model of the same family once a person opts in, labeled as weaker independence, or a
person's own review.

## Review tiers

A candidate lands only on a review by someone who did not write it, and a model reviewing
the output of its own family shares that family's blind spots. Someone who runs a single
harness has one family to hand, so ao recognises three tiers. The tier is recorded with every
review and named wherever the review shows: the artefact's `- tier:` line, the review
ledger row's `tier`, `ao commit-ok`, `ao reviews` and `ao stats`.

| tier | who reviews | when ao accepts it | label |
|---|---|---|---|
| another model family | a model of a family other than the implementer's | always: the default, and the strongest | none |
| same family | another model of the implementer's family | only once a person has opted in, on the record | same family: weaker independence |
| person | a person, who reads the diff and records the verdict | always, through `ao person-review` | person review |

One rule decides the tier. `ao role set`, the reviewer probe that `ao init`, `ao doctor` and
`ao prove` run, `ao review`, `ao commit-ok`, the [capability matrix](capability-matrix.md) and
`ao catchup` all ask it, and a reviewer no tier admits is refused in the same words everywhere,
naming the three ways forward.

**Same family.** The families are the ones the reviewer and the implementer declare, or their
adapters declare; the models are the ones the configuration names, as `model` or through the
adapter's model option in the reviewer's argv. The reviewer is admitted only when both declare
one family, both name a model and the two models differ. A person opts a project in with
`ao config set review.same_family labeled --by <name>`, or at init with
`ao init --profile claude-claude --review-tier same-family --by <name>`: the name, the login
and whether a terminal was attached go to `.ao/ledger/opt-ins.jsonl`. A value written into
`.ao/config.json` by hand is not in force, because an agent that edits files can write it, and
`ao doctor` names it. Withdrawn (`ao config set review.same_family refused --by <name>`), the
opt-in leaves every same-family review granting nothing. In a capability-matrix project the
opt-in makes a binding of the implementer's family and another model argument eligible; the
implementer's own binding never is.

**Person.** A person reviews in two steps, so what they approve is exactly what they read:

```bash
ao person-review --by <name>                                        # the staged diff and its digest; records nothing
ao person-review --by <name> --verdict APPROVED --digest <digest>   # recorded on those bytes, or refused
ao person-review --by <name> --verdict NEEDS_CHANGES --digest <digest> --findings <file>
```

A findings file holds one finding a line, `- [BLOCKER|HIGH|MEDIUM|LOW] file:line - what
breaks`; the counts are read from it and decide as a reviewer's do, so an approval with a
BLOCKER or HIGH finding is refused, and so is a rejection naming none. The review is bound to
the candidate exactly as a model's, and `ao commit-ok` grants on it as on any. It is a
person's command, as `ao waive` and `ao collect-review` are: `--by` never names an agent or a
role, the login and whether a terminal was attached are recorded beside the name, and no grant
ao checks admits it. It is not a flag of `ao review`, because every grant that lets an agent
run its own reviews would then admit a person's too. A person's approval cannot supersede a
rejection of the same bytes: stage the fix, or waive the review. A capability-matrix project
records reviews only from its declared bindings and refuses it.

**Catch-up** keeps its rule: a waived range is held to the family that wrote it, and no
same-family tier reaches it, since the grant recorded that family and never its model. A
person's review of exactly the range (`ao person-review --commits <range> --by <name>`, with
the range `ao catchup --plan` names) holds whichever family wrote it, and `ao catchup` closes the
waiver on it with no reviewer started.

`ao doctor` names the active tier, and `ao doctor --check` names three problems: `no-implementer`
(nothing to compare a reviewer with, so no tier holds), `review-tier` (the configured reviewer
stands in none) and `review-opt-in` (a `labeled` value no person recorded). What ao cannot see
stays a limit: it compares the models the configuration names, not the one a session ran, so an
alias of the implementer's model passes as another; and it cannot know that no agent typed a
person's command, which is why the command is recorded, checked against agent names and kept
out of every grant, as a waiver is.

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

Configs without a capability matrix refuse a reviewer that runs as the
implementer's session, declares the implementer's family, or - where the two do
not both declare families - runs the implementer's engine, unless a review tier
admits it: another model of that family, once a person opted in. In that mode,
`reviewer.id` is the operator's actor declaration: AO refuses an exact match with
the implementer session, while the nonce probe proves transport liveness only—not
runtime actor attestation. Projects that need enforced declared separation opt
into the version-1 [capability matrix](capability-matrix.md): a reviewer with the
implementer's binding **or bound model family** is ineligible and is never
spawned, but for the opted-in same-family tier. Family comes only from the bound model declaration, so inline family
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
