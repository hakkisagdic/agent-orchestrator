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

`ao commit-ok` is that decision made mechanical, so work can land while nobody is
awake. The implementer first stages the exact candidate. A grant is issued only when:

- the index candidate is non-empty and isolated from unstaged or untracked product changes;
- the newest verification passed, marked that candidate ready, and names the exact same
  immutable index candidate;
- no plan was edited after admission; and
- the newest structured prospective review for that candidate is APPROVED (unless review
  is currently disabled or a matching running-slice waiver is open), and the project is
  not held.

Every refusal names its missing condition, and every grant or refusal is appended to the
authority ledger with the candidate and scope it rested on. Each authority row has a
`previous` field: `null` for the first row, then the domain-separated SHA-256 digest of
the preceding canonical JSON object. `ao commit-check` validates the complete committed
chain before selecting the newest decision and refuses a missing or incorrect link. A
non-empty legacy ledger without links is unreadable and is never rewritten implicitly.
`ao commit-check` is the non-mutating enforcement half: the optional AO pre-commit hook
runs it against Git's active index and revalidates the latest persisted grant, its exact
verification and review or live waiver, plus current plan drift, holds, and urgent mail.
It neither issues nor consumes a grant. Its limit: Git writes the commit's tree from the
index after the hook returns, so a process that stages a path in that window lands it
inside an authorised commit, and no hook can prevent that. `ao commit` compares the tree
that landed with the one the grant bound and reports and records a difference, and
`ao doctor` reports any commit since the first grant whose tree no grant bound, and the
watchdog tells the architect about one within a cycle - which is what holds when the
implementer's harness is granted every tool and could commit with `--no-verify`.
`tests/test_attacks.py` names ten attacks on commit authority, each of which fails closed. A review
waiver (`ao waive review`) is a person's
act and is bounded: it names one slice and a person, expires (24 hours unless `--hours`
says otherwise, at most a week), stands in for the review of one candidate only, and is
opened and retired by chained appends. `ao catchup` retires it only on a review that was
recorded for the exact commit granted under it, and that review may not come from the model
family that wrote the commit: the grant records who landed it where ao can establish that (a
turn ao started names its role), and where it could not, a person names the family with
`ao catchup --author-family <family> --by <name>`, on the record. A retrospective
`ao review --commits` artifact can reconcile landed work but can never authorize a candidate.

Hook ownership is content- and topology-sensitive, not marker-based. Project
enrollment is marker-based: only a root `.ao-project` with exact
`ao-project-v1\n` bytes in HEAD or the active index activates enforcement. The
active index supports first adoption; HEAD keeps a staged marker deletion
protected until its authorized commit. `GIT_INDEX_FILE` is part of that active
candidate; an explicitly empty override is invalid, while relative overrides are
resolved against the repository root where the Git query runs, so direct
`commit-check` and the installed hook measure the same index. A marker with
missing, unreadable, malformed, non-object, or empty `.ao/config.json` state
refuses rather than falling back to auto-discovery. Every write of that file replaces
it whole — a temporary file beside it, fsync, rename — so a crash or kill leaves the old
document or the new one, never an empty file that has lost its `capability_matrix`;
a writer refuses a config it cannot read instead of rebuilding it. Both pre-dispatch loading and
commit enforcement use one bounded reader: it reads at most 1,048,576 bytes and
rejects container nesting deeper than 64 before recursive JSON decoding. A
repository with neither HEAD nor index marker is uninitialized, so an incidental
`.ao/` directory does nothing.

`ao hooks status`
asks Git for the effective path and preserves foreign, ambiguous, symlinked, and
tracked content. Exact generated bytes remain labelled `behavior unverified` because
static intent is only the safety precondition for a runtime probe; it is never the
installed verdict. The positive verdict comes only from `git hook run pre-commit`
executing the active path with a temporary index containing a nonce-named gitlink to
the existing HEAD commit, then receiving the same nonce in `commit-check`'s
fail-closed response. The four `AO_HOOK_PROBE_NONCE`, `AO_HOOK_PROBE_PATH`,
`AO_HOOK_PROBE_HEAD`, and `AO_HOOK_PROBE_INDEX` fields are one reserved internal
challenge namespace. A process carrying all four is treated as a probe, never as a
live commit: the namespaced index must be absolute and exactly equal to
`GIT_INDEX_FILE`, and the branch always refuses after validating it. Callers that
start a real commit must not inject the complete reserved challenge. A partial
challenge is inert, while a complete malformed challenge refuses without emitting
proof. The real index, worktree, refs, and object store are unchanged.
`hooks status`, both doctor modes, and init use that one result. A missing executable,
wrong path, altered body, stale AO binary, scoped route that fails open, timeout, or a
hook that merely exits nonzero without the nonce is reported `not installed`.
Install, uninstall, and remove preflight the complete mutation set and refuse all
eligible shared/external or global/system-configured mutations unless a person
supplies `--allow-shared-hooks`. The flag authorizes the location, never foreign or
protected content. `ao remove --yes` decommissions in two authorized phases:
its first invocation removes only the working-tree marker and preserves state and
enforcement; after that marker deletion is staged, granted, and committed, the
second invocation may remove `.ao/` and the remaining AO-owned surfaces. Pre-push
remains informational rather than a doctor alarm because a repository may
intentionally retain its own custom push hook.

The chain is tamper-evidence, not writer authentication. It detects an edit, deletion,
reorder, or append whose successor/predecessor links were not recomputed. It cannot
cryptographically exclude a process with the same user's write access: that process can
append with the correct predecessor, recompute a replacement suffix, or truncate a valid
tail and edit the recorded length to match. The length record outside the repository
makes a cut tail visible to anything that did not also rewrite that record; it is not a
trusted anchor. There is no trusted external head anchor, signing key, or authenticated append
service in AO, so the ledger must not be described as proving who wrote a row or as
detecting every same-user rewrite.

The grant never covers `push`, and no configuration makes it. Deciding and acting stay
in different hands, which is the only reason the decision is worth anything.

## 4. Separation of duties

`reviewer ≠ implementer` for the same slice, enforced rather than advised. A model
reviewing its own output shares its own blind spots. Where only one engine is available,
the reviewer must at least run a different model family.

Who reviewed, the verdict and the counts are read from the evidence line ao writes into
the artefact, never from its text, where the implementer's boundary also appears. Without
a capability matrix a reviewer is the implementer when its id is the implementer's
session id (`auto` resolved the way the watchdog resumes it) or its command carries that
id as an argument. Such a route is never run, and `ao commit-ok` refuses a review that
records one. A label is not an identity: a reviewer called `s10` is not the implementer
`s1`, and one called `independent-auditor` that resumes `s1` is.

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

### 7b. What ao writes is scanned before it is written

On 2026-09-08 a boundary asked for a reviewer's raw output to be kept as evidence, and the
decision had to reverse it: review artefacts are committed, and a model's stdout can carry a
credential. The class is covered now (#48). Review artefacts, mail, decision records (`ao ask`,
an answer, `ao decide`) and verification records pass `lib.scan_evidence` before a byte is
written, and a hit becomes `[redacted:<rule>]`, naming what was removed. A review artefact's
recorded digest is of the scanned bytes.

| rule | matches |
|---|---|
| `private-key` | a PEM private key block |
| `anthropic-key` | `sk-ant-` and 16 or more key characters |
| `openai-key` | `sk-` or `sk-proj-` and 20 or more |
| `github-token` | GitHub's personal, OAuth, user, server and refresh token prefixes with 20 or more characters, and fine-grained personal tokens |
| `slack-token` | Slack's app, bot, user, refresh and session tokens |
| `aws-access-key` | `AKIA` or `ASIA` and 16 upper-case letters or digits |
| `jwt` | three dot-separated base64url parts starting `eyJ` |
| `bearer-token` | `Bearer` and a 16-character or longer token |
| `assigned-secret` | `api_key`, `secret`, `password`, `passwd` or `access_token` assigned a 12-character or longer value |

False positives, and why the rules are shaped as they are: a commit id or a `sha256:` digest is
hexadecimal and matches no rule - the generic long-token rule the watchdog's log masking uses
would erase every digest commit authority reads, so it is not one of these. A word like
`password` in prose is untouched unless a long value is assigned to it. A test fixture that must
hold a token-shaped string builds it at run time.

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

`ao review` runs a separate actor against the immutable staged candidate and the
slice's acceptance boundary. It records who reviewed and embeds structured candidate
and scope evidence. `commit-ok` refuses when:

- the review names no reviewer — nobody can tell who wrote it;
- the reviewer is the implementer;
- the newest structured prospective artifact for that candidate is not APPROVED or is
  marked non-authorizable; or
- the candidate or scope no longer matches the active index.

Unrelated and retrospective artifacts do not supersede a candidate review, and a
retrospective review never grants commit authority.

The first independent run returned NEEDS_CHANGES with a BLOCKER on work whose
self-review had been APPROVED with zero findings: three of the acceptance
boundary's four items were not covered. That is the argument for the separation,
made on its first use.

The reviewer's identity also goes into `.ao/ledger/authority.jsonl`, because the
review file is evidence and evidence gets cleaned up — `semantic-review/` was
untracked and a routine cleanup slice removed it. The record of what authority
rested on has to outlive the document it cites.
