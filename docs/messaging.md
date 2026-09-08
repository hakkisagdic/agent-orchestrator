# Messaging

## What it is, in five sentences

Agents leave each other messages. Writing one is appending a record; nobody ever deletes
anything. Handling one is appending a second record that says who handled it and what they did.
Your inbox is therefore *derived*: the messages addressed to you that have no handling record
from you. A room is not a different mechanism — it is a message addressed to everyone, and each
reader's position is derived the same way.

That is the whole model. Everything below is about making it durable, ordered, searchable and
quiet.

## What it replaces, and why the old rule was almost right

Today a message is a file in `agent-mail/` and handling it means **deleting the file**, so "still
present" means "not yet handled". That is a real guarantee with no state to keep in sync, and it
is why the mailbox has been reliable. But deletion was never the guarantee itself — the guarantee
is that **the unhandled queue cannot drift from the truth** — and deletion buys it by destroying
the record. The costs showed up in practice: a handled message is gone, so there is no corpus to
search and #43 has nothing to read; a mailbox lives inside one repository, so an implementer had
to file an agent-orchestrator finding into another project's mailbox because that was the only
one it had; and one recipient is all a file can have.

Deriving the queue from an absent handling record keeps the guarantee exactly — nothing is
handled until someone records handling it, in the same durable store — and gives back the
history, the search, and more than one recipient.

## The store is git, and this is the point

A message is content: text with a header. Git is a content-addressed, append-only object store
with atomic ref updates, replication, and history, and this project already requires it —
commit authority, hooks and worktrees are built on it. So we do not build a message bus. We use
the object store that is already installed and put the semantics on top:

| what we need | what git already is |
|---|---|
| a message id that cannot be forged or collide | the blob's SHA-256; the id **is** the content |
| append-only, never rewritten | objects are immutable |
| ordering when two agents write at once | a commit DAG: each write names the parents it saw |
| concurrent append without a lock | `update-ref` is compare-and-swap — lose the race, re-read, retry |
| history that outlives the disk | push the ref to a remote |
| search | `git log -S`, `git grep` over the ref |
| reading without coordination | objects are immutable; readers take no lock |

The store is a dedicated ref — `refs/ao/mail` — never a product branch, so message history never
enters the code history. Local disk holds a recent window; older bodies compact to a stub
carrying the digest and a pointer, and the full record stays in the ref.

**Concurrency, precisely.** Two agents appending at once both write their objects (no conflict —
different content, different hashes), then race on the ref. `update-ref` takes the old value as a
precondition, so exactly one wins; the loser re-reads the new tip, adds it as a second parent, and
retries. Nothing is lost and nothing needs a lock. This is the same reason two people can commit
to a repository at the same time.

## Records

Three kinds, all append-only:

- **message** — `from_role`, `to` (a role, a list of roles, or `*`), `class` (below), `subject`,
  `body`, `in_reply_to` (a message digest, so a thread is a DAG and a reply cannot dangle or be
  re-pointed at different content).
- **handled** — `message` (digest), `by`, `outcome` (`applied` / `rejected` / `noted`), and a
  reason when rejected. Recording handling is a positive act in the same store as the message,
  which is what deletion used to be.
- **seen** — optional, `message` + `by`. Separates *nobody looked* from *seen, still working* —
  the distinction the current protocol cannot express and the reason a report once sat unread for
  four hours.

Everything else is derived. Inbox: addressed to me, no `handled` by me. Unanswered: a
`needs-decision` with no `handled`. Thread: follow `in_reply_to`. Nothing to keep in sync, because
there is no second copy of the truth.

## Class decides the channel, and most classes are silent

The failure this module must not repeat is not lost messages — it is **noise**. In one minute on
2026-09-08 three false red e-mails arrived, and earlier a genuine report sat unread for four hours
while the channel that should have carried it was busy re-raising a stale alarm. So urgency is a
declared property of a message, not a property of the transport:

| class | means | reaches |
|---|---|---|
| `fyi` | context, no action | nobody; read when you next look |
| `needs-read` | you should see this before your next slice | surfaced by the commands you already run |
| `needs-decision` | work is stopped until someone answers | the recipient's role, and it escalates by age |
| `urgent` | stop what you are doing | carried by the CLI at the boundaries an agent crosses on its own — lock, verify, commit-ok |

Two rules that come from this project's own scars: a command a person or an agent *runs* never
pages anyone (notification belongs to the scheduled watcher), and a resolution is never louder
than the raise. Escalation by age belongs to `needs-decision` alone.

## Sync: local ref, separate private repository

Two layers, because they answer different questions.

**Local — `refs/ao/mail` in the project itself.** Works offline, needs no configuration, and is
never pushed to the product's own remote. That last clause is not caution, it is a fact about this
repository: `hakkisagdic/agent-orchestrator` is **public**. Pushing a mail ref to a project's own
remote would publish every decision, boundary, refusal and diff excerpt in its coordination — for
this project, to the world. A side ref feels private and is not.

**Synced — one dedicated private repository, holding every project's mail** under its own
namespace (`refs/mail/<project>`). This is what makes the model worth the change:

- **Cross-project addressing stops being a hack.** The reason a room is wanted at all is that mail
  is trapped per repository — an implementer had to file an agent-orchestrator finding into
  another project's mailbox because that was the only one it had. Per-project refs inside each
  product repo keep that trap; one mail repository removes it, and `ao room search` spans
  everything by construction.
- **Visibility is decided once, correctly.** One private repository, no dependence on each product
  repo happening to be private.
- **A contributor cloning the product gets none of it.** Coordination is ours; the code is
  everyone's.
- **Retention is independent.** Compaction and pruning follow the mail's own policy, not the
  product's history.

The cost, stated plainly: one repository now concentrates every project's decisions, so if it
leaks, everything leaks. That is the reason the credential scan is a precondition and not a
nicety, and the reason the repository must be private and stay private.

Three conditions, none of them advisory:

- **the target is verified private at push time**, by asking the host, not by trusting the
  configuration — and a push to a public or unknown-visibility remote is refused, named, and
  logged;
- the credential scan (#48) runs on every record before it leaves the machine;
- syncing is opt-in per project, with the mail repository named in configuration and never
  defaulted.

## Derived, not added

The temptation with a message store is to add features. Almost everything worth having here is
already implied by the three records, and costs no new writing:

- **Threads.** `in_reply_to` is a content digest, so a thread is a DAG and a reply cannot dangle
  or be re-pointed at different content. A thread is *open* while its root is a `needs-decision`
  with no handling record — derived, not a field.
- **Supersession.** A decision answered by a later one closes by reference: the new record names
  the old digest. This is why `D-1788813100` sat open for a day after a later decision replaced
  it — nothing could express "this one is finished because that one happened".
- **Metrics.** Time to first `seen`, time to `handled`, the age distribution per class, and — the
  one that matters — messages with no `seen` at all. Every figure is a subtraction over records
  already stored. These are exactly the failures of 2026-09-07/08: a report unread for four hours,
  a decision unanswered for three, another open for two days. Measuring them belongs in #49, not
  in a new subsystem.
- **Cross-project addressing.** A recipient is a role in a project; a message may name another
  project's role, which is what an implementer needed when it had to file a finding about
  agent-orchestrator into a different project's mailbox.

What deliberately stays out: reactions, presence, priorities beyond the four classes, read
receipts finer than `seen`, attachments. Those are chat features. This is a coordination log, and
every field in it must answer "which failure does this prevent".

## Why not a blockchain

The ledgers already carry a hash chain, and #62 adds a checkpoint committing to the log's size —
together those detect modification, insertion and truncation. A blockchain would add exactly one
thing beyond that: **consensus among parties who do not trust each other**, which protects against
someone who controls the machine rewriting history.

That protection cannot be bought locally. It requires independent witnesses — other parties, a
network, availability, and a service to run — and this project is local-first and single-machine
by design, with `docs/safety.md` already conceding that a process running as the same user can be
*detected*, not excluded. A chain of blocks on one disk, signed by a key on the same disk, is a
longer hash chain with ceremony: whoever can rewrite the log can rewrite the chain.

The affordable form of the same idea is already in the plan. When `refs/ao/mail` and the ledgers
are pushed (#83), **the remote is the witness**: it holds what the local machine published, and a
later attempt to rewrite that history shows up as a non-fast-forward push rather than a silent
edit. One honest sentence of protection, for the cost of a `git push`, and it fails visibly rather
than pretending. If real multi-party guarantees are ever wanted, the shape to adopt is a
transparency log with external witnesses — not a chain of our own.

## What this module is not

It is not a bus, a broker, or a service — nothing runs. It is not the alarm ladder: routing
decides which channel a class may use, but the ladder and its thresholds stay where they are. And
the wiki is a **derived view** of this store, produced periodically for people to read; it is
never the source of truth, and nothing reads back from it.

## Invariants

- **M1.** No record is ever deleted or rewritten. Local disk is managed by compaction to a stub
  with a digest, never by removal.
- **M2.** The unhandled queue is derived, never stored. A message is unhandled exactly when no
  handling record for it exists.
- **M3.** An agent may compact its own local copy and may never remove a record — the actor
  deciding what was handled must not be able to erase the question.
- **M4.** A message id is the digest of its content, so an id cannot be reused for different
  content and a reply cannot be re-pointed.
- **M5.** Concurrent appends never lose a record: the ref update is compare-and-swap and the loser
  retries against the new tip.
- **M6.** Class decides the channel. Nothing below `urgent` may interrupt, and no invoked command
  pages a human.
- **M7.** Nothing leaves the machine without the credential scan and an explicit per-project
  opt-in.
- **M8.** The mail ref is never pushed to a product's own remote, and never to a remote whose
  visibility is not verified private at push time. Configuration is not evidence: the check asks
  the host.
