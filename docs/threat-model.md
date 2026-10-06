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
   nothing on its own account.
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
- A vendor command's own bug that ao cannot observe. Where one of ao's guarantees rests on a vendor's
  behaviour, ao measures it and records the measurement, as KIRO-READONLY does for kiro-cli.

## The severity rule reviewers apply

- **BLOCKER or HIGH** only when "How it breaks" names an actor in scope, the capability they use, and a
  concrete outcome: an unreviewed commit lands, a reviewer or hunter writes, a secret leaks, ao runs
  code an attacker chose, or a guarantee fails open.
- A finding that needs an assumption out of scope is written under "## Notes" as
  `hardening: <the assumption>`. It has no severity and is not counted; hardening notes are collected
  into the backlog, not lost.
- A correctness finding - a wrong result, a missed case - is judged as before; this model decides
  security findings only.
- A reviewer that cannot tell writes the finding, and names the assumption it needs.

A candidate that changes this page is judged against the version from before it: a change cannot
loosen the model it is reviewed under, nor, waived, the model of the reviews after it.
