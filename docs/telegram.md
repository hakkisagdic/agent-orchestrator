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
ao telegram setup      # the four steps, then the token, asked and not shown
ao telegram test       # confirms the bot reaches you
ao telegram install    # launchd job on macOS, long-polls for your replies
```

The bot and its allowlist serve every project on the machine, so `ao telegram setup` prints its steps in
the machine's `language` ([configuration.md](configuration.md)); `ao telegram test` sends its message in
the project's.

**You give the token, at a terminal of yours; no assistant does.** A bot token can
instruct the implementer, so it is a credential: it goes on no command line, into no
repository and into no conversation. `ao telegram setup` asks for it and does not show it,
asks Telegram with it which chats wrote to the bot, and takes the ids you type of the ones
the bot may take messages from. Then it writes `~/.ao/telegram.json` itself, readable by you
alone from the moment it exists (mode 0600). Windows keeps no file mode: there the file is as
private as `~/.ao`, and `ao telegram setup` says so ([windows.md](windows.md)).

```json
{"token": "…", "chats": ["…"]}
```

Where no terminal is attached to ask at, nothing is asked: a script names the variable that
holds the token, and each chat, `ao telegram setup --token-env NAME --chat ID`.

The chat allowlist is not optional. An inbound channel without one is an authority
surface open to whoever finds the bot.

The poller is a launchd job, and only macOS has launchd. On Linux and on Windows
`ao telegram install`, `uninstall` and `status` refuse, and say what keeps `ao telegram poll`
running there instead: a systemd user service, or Task Scheduler.

*In ao since slice LEADS-SECRETS: the setup had a person put the token into a curl command line -
on Windows a PowerShell one - to read their chat id, then echo the file into `~/.ao` and chmod it.
The shell's history and the process list kept the token, PowerShell's history file as well, and
anyone on the machine could read the file until the chmod. ao asks for the token now, finds the
chats with it, and writes the file through its durable replace, 0600 from its creation; a token that
is not the shape @BotFather gives is refused before it is put into a request. `ao telegram install`,
`uninstall` and `status` refused on Windows alone, so on Linux install wrote into a
`~/Library/LaunchAgents` that is not there and asked for launchctl, uninstall said it had removed the
job, and status sent the person to install; they refuse wherever there is no launchd.
`tests/test_leads_secrets.py`.*

## Inbound: everything you type is urgent

A message that is not a command becomes an urgent architect message:

```
agent-mail/<ts>-human-to-<implementer>-URGENT-<slug>.md
```

Marked `## URGENT`, so it reaches the implementer through all three delivery
surfaces — printed by `ao lock` and `ao verify`, attached to every MCP tool
response, and blocking `ao commit-ok` until acknowledged.

A project whose `language` is `tr` names it `-ACIL-` and marks it `## ACİL`; ao reads both in
every project ([protocol.md](protocol.md#markers)).

The phone is answered in the project's language: the confirmation that a message was written, an
answer recorded, a decision that is not there, the list of commands. What you type is read the same
in either language - a command, `D-123 b`, `D-123 x <text>`.

It is urgent by default on purpose. Someone who reaches for a phone to type a
decision has already judged that it matters, and making them remember a marker is
how a channel stops being used.

Read-only commands answer the other question: `/status` `/board` `/credits`
`/notices` `/fleet` `/decisions`.

## Outbound: what happened, and what is being done about it

Alerts carry an audience. A human hears what only a human can fix — quota
exhausted, an agent still stuck after the backoff, a restart that failed on an
expired login. Anomalies and spin detection are the architect's business and do
not ring a desktop.

But they do reach the phone, with their **action state**, because "an anomaly was
detected" and nothing further is the half that makes someone check by hand:

```
🤖 Architect woken — handling 3 report(s) (pid 5676)
✅ Architect done — 3 report(s) closed, queue empty
```

A project whose `language` is `tr` is told `🤖 Mimar uyandırıldı — 3 rapor işleniyor (pid 5676)` and
`✅ Mimar bitirdi — 3 rapor kapandı, kuyruk boş`; a blocked report, a question and a handoff note reach
its phone in Turkish as well.

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
ao ask "Which OS credential store for B2?" "Keychain" "DPAPI fixture" "Defer" --slice B2
ao decisions            # what is open, what was answered
ao answer D-123 b       # from the terminal
```

The implementer poses its own through MCP (`ao_ask`) and reads answers with
`ao_decisions`. On the phone the options arrive as inline buttons; a tap answers
it. Typing `D-123 b` works too, for when the message has scrolled away, and
`D-123 x <text>` gives the free-text answer.

An answer is a key the question offers; anything else is refused, with the keys it does offer. A
question answered once keeps its answer, so a second tap is refused too: `ao answer D-123 a --change`
replaces it from the terminal, and the first answer stays in the decision's record beside the new one.

Free text is always the last option, appended automatically: `x) Other (free text)`, or
`x) Başka (serbest metin)` in a Turkish project, and an option labeled either way is answered in
words. Options are a convenience, never a cage: the answer that matters is often the one nobody
listed, and a form that cannot express it produces a wrong answer chosen because it was available.

The implementer does not get its own channel to a phone. It reports — `ao_ask`,
or `ao_report {kind: "blocked"}` — and delivery is decided centrally. An agent
holding its own notification pipe is a spam surface, and a channel people mute is
worse than no channel.
