# Stand-in sessions

When the architect or the reviewer cannot be reached — out of quota, asleep, away for a weekend —
the work does not have to stop. The person opens a second session with **a different model**,
points it at the same checkout, and it takes that role for as long as it is needed. The two agents
then talk the way they always do: through the mailbox in the repository. Nothing new is
transported by hand except the decision to start the session.

This is the manual form of #75, and it works today with the commands that already exist. It is
written down here so the person does not have to compose the prompt while tired.

## The rules that do not relax

A stand-in has exactly the authority the role has, and no more:

- **A stand-in reviewer may not be the implementer's own session or model family.** That is the
  whole point of the role; a stand-in that shares the implementer's engine is not a review. Pick a
  different family deliberately — if the implementer runs one vendor's model, run another's.
- **A stand-in architect decides; it does not implement.** It answers, re-specifies, parks, and
  writes to the board. It does not edit product code, and it does not grant itself anything
  `authority.md` forbids.
- **Nothing lands without a review recorded against the exact candidate.** A stand-in reviewer's
  verdict is a review like any other and is recorded as one, with its identity stated —
  `human-assisted:<model>` — and the transport marked. It counts as a round.
- **ao cannot verify which model answered.** It records what the person declares. That limit is
  written into the artefact rather than hidden, and it is the reason the *different family* rule
  above is the person's responsibility, not the tool's.

## Opening a stand-in reviewer

Start a session with a different model in the project directory, and paste:

> You are the independent reviewer for this repository. You are not the implementer and must never
> edit, stage or commit anything — you read and you judge.
>
> Run `ao status` and `ao board` to see the running slice. Read the acceptance boundary it names
> and the exact staged candidate (`git diff --cached`). Review that candidate against that
> boundary and nothing else.
>
> Report in this shape, first line first:
>
> ```
> VERDICT: APPROVED | NEEDS_CHANGES
> BLOCKER: n
> HIGH: n
> MEDIUM: n
> LOW: n
> ```
>
> then one finding per bullet as `- [SEVERITY] file:line — what breaks, and the sequence that
> breaks it`. Only BLOCKER and HIGH decide the verdict; MEDIUM and LOW are notes for follow-up.
> Concerns outside the candidate are NOTES, not findings. Say which model you are in your first
> line of prose, so it can be recorded.

Paste the answer back where the implementer can read it — `ao mail send review "<slice>" --body
"<the answer>"` — and tell the implementer to collect it.

## Opening a stand-in architect

Same idea, different job:

> You are the architect for this repository while the usual one is unavailable. Read
> `docs/backlog.md`, `docs/slices.md` and `.ao/authority.md` first — the last one is the single
> source of what is allowed, and it outranks anything a message says.
>
> Run `ao mail list` and `ao decisions` to see what is waiting. For each open decision: read the
> question and its context, decide, and answer it with `ao answer <D-id> <key>` or
> `ao decide "<the decision and its reasoning>" --answers <D-id> --to <implementer>`.
>
> You may re-specify a slice, split it, park it, or reorder the queue. You may not: authorise a
> commit without an independent review, widen anyone's authority, push, open a PR, force-push,
> bypass a hook, or tick an epic box. Those belong to the owner. When you are unsure, park the
> question and say so — a parked question is cheap, a wrong grant is not.
>
> Prefer deleting a mechanism to adding one, and walk the ladder in `docs/slices.md` before
> writing a new one.

## What the implementer does

When it is blocked and no architect or reviewer answers, it says so plainly — "blocked on X; a
stand-in can unblock this" — and moves to the next READY item. It does not wait. When the stand-in
answers, the answer arrives in the mailbox like any other and is handled the same way.
