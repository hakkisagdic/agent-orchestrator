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

## Sync, and what leaving the machine costs

A project may push `refs/ao/mail` to a **private** remote. Then history is unbounded and
off-machine, the mailbox satisfies the backup requirement for free, and search works across
clones. Two conditions, both non-negotiable:

- the credential scan runs on every record before it is pushed, because messages carry diffs,
  boundaries and decision text;
- pushing is per-project opt-in with the remote named in configuration, never a default.

The honest limit is stated rather than implied: a private remote is a third party holding the
project's decisions. That is the owner's choice to make, and it should be made deliberately.

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
