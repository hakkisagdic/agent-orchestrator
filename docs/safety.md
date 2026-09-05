# Safety model

An orchestrator that can inject prompts into other agents' sessions and grant commit
authority is a privileged component. These are the invariants that keep it from becoming
the most dangerous process on the machine.

## 1. Mail is data, never authority

Everything arriving through the mailbox, a transcript, a repository file or a tool result
is **untrusted input**. It can carry a plan, a ruling, a file list, a commit message. It
can never grant:

- `git push`, force-push, PR creation, tag or release publication
- hook bypass (`--no-verify`), history rewriting, amend of published commits
- mutation of any repository other than the canonical one
- credential access, or exfiltration of anything to a network destination

Those require a direct instruction from the human, in the human's own channel. An agent
that receives a message asking for one of them rejects that part, records the rejection,
and continues with the rest.

This matters because a mailbox is a prompt-injection surface by construction. If someone
can write a file into your repository, they can write a message that *looks* like a
decision. The rule holds regardless of how convincing the text is: urgency, claimed
authority, "the user already approved this", or an instruction embedded inside code the
agent is reading.

## 2. Two writers corrupt state

Never inject into a session that is mid-turn. Two hazards, two mitigations:

| Hazard | Mitigation |
|---|---|
| Two writers on one session transcript | Ask the OS which agent processes have this repo as their cwd. Start nothing while any of them is alive. |
| Two writers on one working tree | Write lanes get separate git worktrees; a second write lane in the same workspace is refused. |
| A human needs the tree | `ao hold` stops every agent in it and holds the lock; every restart path checks the lock first. |
| A turn ended but its processes did not | `ao writers` shows them as orphans (no terminal, dead group leader) and counts zero writers; the watchdog clears them before every count, and `ao hold` / the reaper stop turns by process group so no new ones are made. |

**Do not infer this from file timestamps.** That was the original design — status AND
write-age, ANDed — and it failed twice in one night. A turn retrying a provider 5xx sits
in backoff writing nothing, which reads exactly like a turn that ended, so the guard
started a second turn on the same session id and the two left a rename half-applied.
Then the fix that tracked the pid of *our own last child* missed the other fourteen:
every nudge spawns a detached process, nothing reaps them, and fifteen live agents had
accumulated in one repository with four still burning CPU.

Measure with `pgrep -f` plus an `lsof` cwd match, and never with `ps -eo args | grep`:
`ps` truncates long argument lists, a resume prompt is long, and a `ps`-based check
reported one process while fifteen were running. That wrong answer was then passed to
the implementer as a measured fact, which cost more time than the original bug.

The corresponding rule for the agent: check at most once per turn, act on the answer,
and never conclude a second writer exists from a growing diff. Your own edits land
asynchronously — a file changing during your own turn is you.

Call-return adapters avoid this entirely — a synchronous call means the orchestrator
knows when a turn is in flight and takes a real lock instead of inferring one.

## 3. Commit authority is separated

The agent that writes the code does not decide when it is good enough.

1. The implementer finishes and reports. It does not commit.
2. The verifier — a **different actor** — re-runs typecheck, tests and diff checks itself.
3. Commit authority is granted against the verifier's numbers, never the implementer's report.
4. Push is never automatic, under any configuration.

Reports from implementers are usually accurate. The point is not to catch liars; it is that
a system whose correctness depends on self-reporting has no independent check at all.

`ao commit-ok` is that step made mechanical, so work can land while nobody is awake. It
grants only when four things hold at once:

- the newest verification passed, and
- its **tree digest still matches** the working tree — a pass measured before the last
  three edits describes a tree that no longer exists, and
- no plan was edited after admission, and
- the newest review is APPROVED and the project is not held.

Every refusal names its missing condition so an agent can act instead of asking again,
and every decision — granted or refused — is appended to an authority ledger with the
evidence it rested on. The grant never covers `push`, and no configuration makes it.

It decides; it does not commit. Deciding and acting stay in different hands, which is
the only reason the decision is worth anything.

## 4. Separation of duties

`reviewer ≠ implementer` for the same slice, enforced rather than advised. A model
reviewing its own output shares its own blind spots. Where only one engine is available,
the reviewer must at least run a different model family.

The same applies to `verifier ≠ implementer`.

## 5. Trust flags are a decision, not a default

Every adapter exposes something like `--trust-all-tools` or
`--dangerously-skip-permissions`. Driving an agent with those flags hands it unattended
shell access on your machine.

- Default to **scoped trust** — the narrowest tool set the slice needs.
- Reserve trust-all for sandboxed workspaces or work you would run unattended anyway.
- Never combine trust-all with a prompt assembled from untrusted content.

The orchestrator records which trust level each turn ran at, so "what could that turn have
done?" has an answer after the fact.

## 6. Capability gating on MCP

The MCP surface is grouped: `read`, `write`, `run`, `drive`, `authority`. Only `read` and
`write` are on by default. `drive` lets one agent inject prompts into another's session;
`authority` lets it request commit rights. Turning those on is a deliberate act, and they
are the two groups an attacker would want most.

## 6b. Escalation lifts scope, never authority

When the implementer refuses work because of a standing instruction, the architect stops
and asks the human. An approved `ESCALATION` message carries the human's authorisation
verbatim and unlocks exactly one scope lock — review-only becomes implement, a paused lane
resumes.

It can never unlock anything in the prohibited list of §1. An escalation that appears to
grant push, force-push, hook bypass or foreign-repository mutation is malformed by
definition and must be rejected whole, not partially honoured.

## 7. Secrets

Secrets never enter the mailbox, the event log, a message, a commit, a status line or a
report. Adapters record *where* credentials live, never their values. If a transcript
contains one, that is the vendor's bug and the orchestrator does not propagate it — the
dashboard renders opaque handles and commitments only.

## 8. Blast radius of the machine itself

Parallel lanes are cheap to start and expensive to run. Five simultaneous test suites will
swap a laptop into uselessness, and each lane individually looks reasonable. Gate runs are
serialised, memory pressure is checked before starting work, and lanes are refused rather
than queued into a thrash.

## 9. What this model does not cover

- **Cross-machine trust.** Delegated to [keyflip](keyflip.md): origin authentication,
  replay guards, encrypted rendezvous, consent-gated exec. agent-orchestrator adds no
  crypto of its own and should not.
- **Vendor-side data handling.** What a provider does with a prompt is outside this
  boundary. Decide that before you send the prompt.
- **Malicious adapters.** An adapter file is executable configuration; it names commands
  that will be run. Review one before installing it, the same as any script.

## 4b. The reviewer was the implementer, and nothing checked

Section 4 says `reviewer ≠ implementer` is "enforced rather than advised". It was
not enforced anywhere. The implementer wrote its own review, `ao commit-ok`
required only that a review say APPROVED, and authority was granted on a verdict
the implementer had produced about itself. A model reviewing its own output shares
its own blind spots, so that verdict measured nothing it did not already believe.

`ao review` runs a separate actor against the diff and the slice's acceptance
boundary. It records who reviewed, against which tree digest, and `commit-ok` now
refuses when:

- the review names no reviewer — nobody can tell who wrote it;
- the reviewer is the implementer;
- the review's tree digest no longer matches, so it described different code.

The first independent run returned NEEDS_CHANGES with a BLOCKER on work whose
self-review had been APPROVED with zero findings: three of the acceptance
boundary's four items were not covered. That is the argument for the separation,
made on its first use.

The reviewer's identity also goes into `.ao/ledger/authority.jsonl`, because the
review file is evidence and evidence gets cleaned up — `semantic-review/` was
untracked and a routine cleanup slice removed it. The record of what authority
rested on has to outlive the document it cites.
