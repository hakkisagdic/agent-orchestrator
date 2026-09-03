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
| Two writers on one session transcript | Idle guard: status **and** write-age, ANDed, before any injection. |
| Two writers on one working tree | Write lanes get separate git worktrees; a second write lane in the same workspace is refused. |

The AND is deliberate. A false "idle" corrupts a session; a false "busy" only delays a
nudge. Bias toward delay.

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
