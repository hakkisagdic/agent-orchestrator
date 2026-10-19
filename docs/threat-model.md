# Threat model

What ao protects, from whom, and what it does not claim to. Every review of this repository is
judged against this page: ao reads a project's threat model from `THREAT_MODEL.md` or
`docs/threat-model.md` as it stood before the change under review - the last commit for a staged
candidate, the commit a landed range starts from for catch-up - and puts it in each review prompt
above the candidate. A change to the model that landed under a review waiver still open judges no
review until it is reviewed itself, and where ao cannot tell which model applies, a review runs
without one. There is no setting to point it elsewhere, since the implementer can write the
project's configuration; a person's review and a carried stand-in answer record the model they were
given, which for a person is none.

## What ao protects

1. **What lands.** A commit lands on a governed repository only with its verification and a review by
   another model family or a person, each recorded in ledgers that show tampering.
2. **The machine.** An agent ao runs as reviewer or hunter only reads: it writes nowhere and runs
   nothing on its own account. Configuration an agent writes where a harness reads it - hooks,
   plugins, MCP servers, instructions - runs in no harness ao starts before a person has seen it: a
   reviewer is not handed a candidate's, and no wake, refill, nudge, hunt or lane starts in a tree
   that holds a change to it nobody has seen. `ao ask --codebase` runs its provider in a copy of the
   tree without that configuration, as a review does.
3. **Secrets.** A provider's key, a token in mail and a person's credentials never reach a log, a
   prompt, a record or a host they were not meant for.
4. **Failing closed.** A guarantee ao cannot keep on a platform or in a state is refused, and says
   why; it is never silently weakened.

## Who is in scope, and what they can do

| Actor | Can | Wants |
|---|---|---|
| **The implementer agent** | write any file in the working tree; stage any candidate; run the commands its harness allows; race ao's checks inside the tree; write mail and board text; put instructions in code, comments and documents | to land a change that was not reviewed, or to widen its own authority |
| **A reviewer or hunter agent** | read what it is handed; be steered by what it reads | nothing: it is semi-trusted, and must not be able to write |
| **Untrusted content** | diffs, file names, pull-request bodies, tracker items, a provider's response, a vendor command's output | to make ao run, approve, leak or write something |
| **A model provider** | answer badly, answer nothing, answer slowly, echo what it was sent | nothing: it is honest but fails, and sees the review prompt by design |

## Out of scope

A finding that needs one of these assumptions is a hardening note, not a blocker:

- The attacker already controls the person's account or shell, ao's install, `PATH`, the harness
  binaries, or the person's global configuration (`~/.gitconfig`, `~/.ao/settings.json`).
- Code that already landed after review is itself malicious - "an existing module rebinds the loader" -
  unless the candidate under review introduces it or turns it on.
- The interpreter fails at an arbitrary line - a MemoryError, a thread that cannot start - with no
  attacker able to cause it.
- The operating system, the kernel or the hardware is compromised, or someone has physical access.
- A service the person gave a credential to turns it against them: an A2A agent registered with a
  token in `~/.ao/a2a-agents.json` echoes it back, encoded or not, or acts with it. The person chose
  to trust that service with the credential, and no reading of its answers can take it back.
- A repository before the commit that tracks its `.ao-project`. Enrollment is that commit: until it
  lands the marker is only staged, and an agent can unstage it and commit unchecked, with no race at all.
  The person commits the marker before an agent works in a new repository.
- A commit on a born branch that tracks no `.ao-project`, in a repository none of whose refs tracks
  one any more - every such ref deleted. The commit hook reads what Git answers about the marker, and
  such a repository is one it cannot tell from one never enrolled: it stands aside there, as it does for
  that one.
- Another program writing a hook path in the instant between `ao hooks install`'s last look at it and
  its write. Install keeps a hook it did not write wherever it can tell - one there before it read the
  path, or put there before its last look, compared by device, inode, size, times and bytes - and no
  file operation replaces a file only while it holds what was read; taking ao's own hook aside first
  would leave a commit running with no hook at all. What that instant can cost is another tool's hook,
  never a commit's check.
- A gate's own code running on after ao stops it. `.ao/gates.json` names the project's checks, the
  implementer writes it, and `ao verify` runs what it names as the person, by design: a test suite is
  code. ao stops a gate past its time with what it can find of it - its process tree, its group, its
  session - so that a suite whose workers outlive its shell does not load the machine after the lock is
  let go; a gate written to outlive that stop - a worker that starts a session of its own, or forks
  faster than ao reads the process table - is code the implementer chose to run, and no reading of the
  table bounds it. The machine lock schedules heavy runs; it is not a boundary. What holds is what a
  verification binds: the gate definitions it ran, and the tree it ran them on.
- A vendor command's own bug that ao cannot observe. Where one of ao's guarantees rests on a vendor's
  behaviour, ao measures it and records the measurement, as KIRO-READONLY does for kiro-cli.

## The severity rule reviewers apply

The owner decided on 2026-10-19 that a review's severity follows what is at stake: a feature far from
the gate - an export, a notification, an onboarding step - took four to seven rounds, each refused for
a HIGH that needed the implementer to forge a record or plant a file and reached no outcome of the
gate (REVIEW-SCOPE).

- **BLOCKER or HIGH** only when "How it breaks" names an actor in scope, the capability they use and the outcome:
  an unreviewed or unverified commit lands; a guarantee a person records (review on, a person's opt-in, a waiver)
  turns off with no person; a reviewer or hunter writes; a secret leaks; ao runs code an attacker chose.
- A finding in a feature outside that core - reporting, exports, notifications, onboarding and setup,
  documentation pages, dashboards - that needs the implementer to forge a record or plant a file, and
  reaches none of those outcomes, is MEDIUM at most, and says so in its text.
- A finding that needs an assumption out of scope is written under "## Notes" as
  `hardening: <the assumption>`. It has no severity and is not counted; hardening notes are collected
  into the backlog, not lost.
- A correctness finding - a wrong result, a missed case - is judged as before; this model decides
  security findings only, and the verdict rule (NEEDS_CHANGES only for a BLOCKER or HIGH) stays.
- A reviewer that cannot tell writes the finding, and names the assumption it needs.

A candidate that changes this page is judged against the version from before it: a change cannot
loosen the model it is reviewed under, nor, waived, the model of the reviews after it.
