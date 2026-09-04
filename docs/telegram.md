# Telegram: alerts out, decisions in

The outbound half is the obvious one. The inbound half is the reason this exists.

When the architect's quota runs out, everything stops. That happened twice in one
day on this project, and both times the state that would have unblocked it was in
a conversation nobody else could reach. A person who can write one decision from a
phone at that moment keeps the run alive.

That works because the architect role was already file-driven: a decision is a
file in `agent-mail/`. Telegram adds no new source of truth — it is a second
keyboard for the same mailbox, exactly as the MCP tools are.

## Setup

```bash
ao telegram setup      # prints the four steps
ao telegram test       # confirms the bot reaches you
ao telegram install    # launchd job, long-polls for your replies
```

**You write the config file, not the tool and not an assistant.** A bot token can
instruct the implementer, so it is a credential: it belongs in `~/.ao/telegram.json`
with mode 600, and never in the repository or in a conversation.

```json
{"token": "…", "chats": ["…"]}
```

The chat allowlist is not optional. An inbound channel without one is an authority
surface open to whoever finds the bot.

## Inbound: everything you type is urgent

A message that is not a command becomes an urgent architect message:

```
agent-mail/<ts>-fable-to-kiro-ACIL-<slug>.md
```

Marked `## ACİL`, so it reaches the implementer through all three delivery
surfaces — printed by `ao lock` and `ao verify`, attached to every MCP tool
response, and blocking `ao commit-ok` until acknowledged.

It is urgent by default on purpose. Someone who reaches for a phone to type a
decision has already judged that it matters, and making them remember a marker is
how a channel stops being used.

Read-only commands answer the other question: `/status` `/board` `/credits`
`/notices` `/fleet`.

## Outbound: what happened, and what is being done about it

Alerts carry an audience. A human hears what only a human can fix — quota
exhausted, an agent still stuck after the backoff, a restart that failed on an
expired login. Anomalies and spin detection are the architect's business and do
not ring a desktop.

But they do reach the phone, with their **action state**, because "an anomaly was
detected" and nothing further is the half that makes someone check by hand:

```
🤖 Mimar uyandırıldı — 3 rapor işleniyor (pid 5676)
✅ Mimar bitirdi — 3 rapor kapandı, kuyruk boş
```

Detection alone tells you the system noticed. Detection plus disposition tells you
whether to get involved, which is the only thing the alert was for.

## Authority

A message from the phone has exactly the authority the architect has, no more. It
can decide scope, mark something urgent, update the board. It cannot push, open a
PR, bypass a hook, or close an epic — not because the prompt asks nicely, but
because nothing in `ao` grants those to anyone.

## When it is not configured

Nothing degrades. `telegram.send` returns 0, the desktop notification still fires,
the mailbox still works, and the file protocol is unchanged. This is an addition
for people who install it, never a dependency.

## Decisions: one tap instead of a paragraph

```bash
ao ask "B2 için OS kimlik deposu?" "Keychain" "DPAPI fixture" "Ertele" --slice B2
ao decisions            # what is open, what was answered
ao answer D-123 b       # from the terminal
```

The implementer poses its own through MCP (`ao_ask`) and reads answers with
`ao_decisions`. On the phone the options arrive as inline buttons; a tap answers
it. Typing `D-123 b` works too, for when the message has scrolled away, and
`D-123 x <text>` gives the free-text answer.

Free text is always the last option, appended automatically. Options are a
convenience, never a cage: the answer that matters is often the one nobody
listed, and a form that cannot express it produces a wrong answer chosen because
it was available.

The implementer does not get its own channel to a phone. It reports — `ao_ask`,
or `ao_report {kind: "blocked"}` — and delivery is decided centrally. An agent
holding its own notification pipe is a spam surface, and a channel people mute is
worse than no channel.
