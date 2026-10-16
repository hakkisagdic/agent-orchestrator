# Warp's own store, measured — what a second adapter would read

A measurement note, not an audit of ao. It records what was read off this machine on
2026-10-15 so that the harness adapter shipped in the same branch ([`warp.json`](../../src/ao/adapters/warp.json))
did not have to carry it, and so the work of watching Warp's desktop agent — which that
adapter deliberately declines to declare — is not started from zero by whoever takes it.

Everything below is a reading of one machine's files and of one build of one CLI. Counts
age. Nothing here was read from Warp's documentation, and no claim below is inferred from
what a terminal app is usually like.

This note provides evidence for the adapter's measured refusals to declare an
answer record, sessions or a store: no completed CLI reply was captured, and the
observed desktop conversations were not the conversations created by the CLI.

## Two harnesses live in one bundle

`/Applications/Warp.app/Contents/MacOS/stable` is the desktop app;
`/Applications/Warp.app/Contents/Resources/bin/oz` is a two-line script exec'ing that same
binary under a different argv[0]. `oz --version` answers `Oz v0.2026.10.07.08.29.stable_00`.
Nothing named `oz` is on PATH: the app makes no link for it, and `command -v oz` answers
nothing.
`warp`, `warp-cli`, `warp-dex`, `warp-diag` and `warp-updater` on that PATH belong to
Cloudflare WARP, a VPN, which is why no detection here keys on the word `warp`.

ao finds the bundle's `oz` where Warp installs it: its adapter declares
`/Applications/Warp.app/Contents/Resources/bin` and the corresponding
`~/Applications/Warp.app/Contents/Resources/bin`. No adapter layer is needed to
reach either. The SQLite container below and `~/Library/Logs/oz` remain notes
about state and logs, not binary search directories. MCP management flags were
observed, but no setup file was measured: the adapter carries that observation
in its notes, with no MCP declaration. `ao init --agent warp` therefore uses the
files-only setup, and auto setup falls back to generic when only Oz is present.

The two halves keep different state. The desktop agent writes a SQLite store; a CLI run
writes nothing into it. That was tested rather than assumed: one real `oz agent run` was
started in a throwaway directory and every table count afterwards was the same as before
(`ai_queries` 250, `agent_conversations` 24, `agent_tasks` 24, `blocks` 44, `commands` 207),
and the conversation id that run printed appears in no table.

## The store

`~/Library/Group Containers/2BBY89MBSN.dev.warp/Library/Application Support/dev.warp.Warp-Stable/warp.sqlite`,
52 tables, opened read-only throughout: `lsof` on the file found the app holding it, and a
second writer would corrupt an agent's own state. The path is worth naming because `ls` of
`~/Library/Application Support/dev.warp.Warp-Stable` — where the name suggests it would be —
holds nothing; the store is in the app group container.

Four tables carry the conversation:

- `ai_queries` — 250 rows, one per turn: `conversation_id`, `exchange_id`, `start_ts`,
  `input`, `working_directory`, `output_status`, `model_id`, and empty `planning_model_id`
  and `coding_model_id` in all 250. The prompt's words are inside `input`, which parses as
  JSON in every row: `[{"Query":{"text":"...","context":[...]}}]`, 214 of the 250 over 200
  characters. 15 distinct `model_id` values, 9 of them UUIDs — the ids of models of custom
  endpoints — and 6 names such as `auto`.
- `agent_conversations` — 24 rows, one per conversation: `conversation_data`, `summary`,
  `last_modified_at`. `summary` is a JSON object with `title`, `initial_query`,
  `initial_working_directory`, `is_restorable`. `conversation_data` holds the signals a panel
  would want: `context_window_usage` (0.166 on the newest), `credits_spent`,
  `total_provider_cost_in_cents`, `token_usage[]` split by `warp_tokens`, `byok_tokens` and
  `custom_endpoint_tokens` with a per-category map inside each, and `tool_usage_metadata`
  with a counter per tool, `apply_file_diff_stats` naming `lines_added`, `lines_removed` and
  `files_changed` among them. It also holds `run_id` and `server_conversation_token`, which is
  the store saying plainly that the work is the server's.
- `agent_tasks` — 24 rows, one per conversation; `task` is protobuf, not text.
- `blocks` and `commands` — the terminal's own history: 44 blocks, 207 commands, with `pwd`,
  `exit_code`, `start_ts`, `completed_ts`. The link to an agent conversation is inside JSON
  text — `blocks.ai_metadata` carries `{"requested_command_action_id":"toolu_...","conversation_id":"..."}`
  in 10 of 44 blocks, and `blocks.agent_view_visibility` names an `origin_conversation_id` in
  11 — and `commands.is_agent_executed` is NULL in 190 rows and 0 in 17, never 1 here.

No column of the 52 tables holds what the agent said. The prose in the store is the person's
input, a shell command and its output, and the store's own JSON metadata. An answer is on the
server: `oz run get <task id>` and `oz run conversation get <conversation id>` reach it, and
`run_url` in a run's first event points at `https://oz.warp.dev/runs/<run id>`.

## Three readings that work, and what they cost to declare

The row-store reader (`DatabaseTail`) reads one table, so a session's turns and its
conversation metadata cannot be one transcript. Each reading below was run against the live
store before it was written down.

Sessions of a directory — 29 groups over the 250 turns for the home directory `~`, newest
first, a real title where the conversation has one (6 of the 29 have none, because 18 turns
carry a `conversation_id` with no `agent_conversations` row beside it):

```sql
SELECT q.conversation_id AS session,
       COALESCE(json_extract(s.summary, '$.title'), '') AS title,
       CAST(strftime('%s', MAX(q.start_ts)) AS INTEGER) AS mtime
  FROM ai_queries q LEFT JOIN agent_conversations s ON s.conversation_id = q.conversation_id
 WHERE q.working_directory = ? AND q.conversation_id IS NOT NULL AND q.conversation_id <> ''
 GROUP BY q.conversation_id ORDER BY mtime DESC
```

The `strftime` is not decoration. `working_directory` is the absolute cwd as it stands, so
the directory binds as a parameter with no escaping — but `mtime` must come back as a number:
the reader takes a session's mtime from a numeric column and reads a text one as no mtime at
all.

Freshness, which is the reading that says whether this session is working:

```sql
SELECT CAST(strftime('%s', MAX(start_ts)) AS INTEGER) FROM ai_queries WHERE conversation_id = ?
```

Both the text and the number describe the same instant, and only one of them is true here.
`start_ts` is UTC written as naive text: the newest turn's `start_ts` reads exactly the
machine's UTC offset behind the `current_time` the JSON in its own `input` carries with that
offset — the same moment, a timezone apart — and `blocks`' newest `completed_ts` matches the
modification time of the store's own file only when it is read as UTC.
A reader that parses the text as ao's freshness reading does, `datetime.fromisoformat` on a
naive string, gets local time and answers the store's silence plus the machine's UTC
offset — three hours more on the machine measured, and a shift that will differ on every
box. `SQLite`'s `strftime('%s', ...)` reads naive text as UTC, which is what these rows are,
so the declared query must do the arithmetic the store forgot.

The turn's verdict — `output_status` is the harness's own answer per turn, and it is a
serde-encoded enum, so the row's text carries its own quotes: `"Completed"` 232, `"Failed"`
10, `"Cancelled"` 7, and one row holding a whole error object,
`{"Failed":{"error":{"Other":{"error_message":"Agent Mode request failed: Internal error occurred at transport layer."}}}}`.
A declaration would have to match those strings as they are, quotes included.

## Why the shipped adapter declares none of this

Not because the readings fail — they are above, and they work — but because of what they would
be read as. The sessions this store keeps are the desktop app's conversations, and the ids ao
would get back from `oz agent run` are not in it; `ao resume` would name to a server a
conversation the local store says nothing about. A `sessions` block makes a harness's ids
resolvable, and a panel reading an app conversation as an implementer's turn is the misreading
this project already refuses in writing. Two other readings are missing for smaller reasons:
the words of a turn sit in a JSON **array**, and the row reader promotes only a JSON object
column, so `input` reaches a reader as 26 KB of serialization rather than a prompt; and there
is no role in the store at all — every row is the person's side of one turn, which is why no
reply of Warp's would ever appear even once the array is reachable.

What would make the second adapter honest is a measured answer to one question: does a
conversation id the app's store names mean anything to `oz agent run --conversation`? If it
does, the store and the CLI are one id space, the app's sessions become resumable, and the
declaration above becomes the right one to ship. If it does not, the app is a harness ao can
watch and never drive, and it wants its own adapter id rather than a second block on this one.
Neither has been answered: every attempt this machine made to complete a turn died at the
provider's daily token cap before an answer.
