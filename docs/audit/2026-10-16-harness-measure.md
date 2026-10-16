# Eleven adapter claims, measured against the harnesses they name — 2026-10-16

A measurement note, not an audit of ao. It records what was seen of the eleven claims the triage of
2026-10-12 set aside "to measure before anything changes" (`hunt-triage-2026-10-12.md`, the row that is
backlog #119) — each claim an adapter makes *about a harness*, and each one measured here on 2026-10-16
against that harness: the CLI itself where it runs on the machine, the project's own documentation where
it does not, the wheel, the store, the platform. A claim the measurement agreed with is kept as it was,
with the reading recorded beside it; a claim the measurement contradicted is fixed, with a test that
holds the fix. Three were wrong. None of the eleven moved an adapter's `verified` level: a measurement
written beside a note is not an adoption run.

## The three that were wrong, and what was changed

### a5936652edde4fab cloud-generic: fetching a pull request from a fork

`result.fetch` fetched `{branch}` from origin, and for exactly the pull requests this adapter exists
for — ones from a fork — that branch does not exist in the base repository. Measured against real
GitHub: for `The-PR-Agent/pr-agent#4143` the fork's branch is absent from the base repository's
`refs/heads/`, the commits live only under `refs/pull/4143/head`, and `git fetch origin <branch>` in a
bare clone fails with "couldn't find remote ref". The pull namespace works for fork and same-repo pull
requests alike (`pull/{pr}/head` and its full `refs/` form both probe as fetchable). The fetch is now
`["git", "fetch", "origin", "refs/pull/{pr}/head"]`, and `fetch_note` carries the reading. No Python
consumer of `result.fetch` exists in the tree today — the value is a declared capability, fixed so it
is right when it gets one; `tests/test_harness_measure.py` pins the argv.

### f8c734050f8efa6b gemini: a resume that started a new session

The `resume.argv` was the send's — `--prompt {prompt}` alone, no session — so "resuming" started a
fresh session every time. Re-read on the project's own reference (main branch, 2026-10-16): `-r`/
`--resume` resumes a previous session by id, by index where the id is unknown, or by "latest", as in
`gemini -r "<session-id>" "query"`. The argv is now
`["gemini", "--resume", "{session}", "--prompt", "{prompt}"]`; the `stdin` block keeps its meaning (as
send: the prompt may come from stdin, and a prompt argument is appended to it), and the prompt-channel
checks hold: a resume whose prompt overflows the argument limit still routes to stdin rather than
failing.

### 8db125ad4fc750c0 qoder: an idle window below the quiet its own note recorded

The busy window was 120 s and the note beside it recorded a transcript lag of 135 s mid-turn — the
window sat below its own cited reading. Measured 2026-10-16 with a turn-bounded gap walker over the
stores, read-only, no new writes: in the reviewer store (217 `qodercli` sessions) a live turn stayed
quiet for 612 s before its first partial record (523, 520, 494 and 416 beside it), and IDE-driven turns
went quiet for 599 s and 606 s. The hour-and-day gaps the same walk holds are app closures — reading a
closed app as idle is right. The window is now 900 s, so a live turn across a ten-minute check reads
slowing, not idle. This is a display-only value: the watchdog reaps and re-wakes by its own
`idle_minutes`, so no reaping behaviour changed. The note also records the boundary the walk showed: a
session the IDE drove has no `qodercli` process, so the process check reads it stopped however fresh
its write — `busy()` watches the implementer's headless turn, whose `qodercli` lives in the tree.

## The eight that held, and the reading recorded beside each

### e07a370ec15451dd codex: `--sandbox` has no place on `codex exec resume`

Rechecked on codex-cli 0.160.0. `codex exec resume --sandbox read-only` errors "unexpected argument
'--sandbox' found" (and suggests passing it as a value), while `codex exec resume -c
sandbox_mode="read-only"` is accepted. The adapter's `sandbox` option (`--sandbox {mode}`) is used on
`exec` argv, and no resume argv carries it — a test now holds that — and `sandbox_note` carries the
recheck beside the upstream issue it already cited.

### 47a259b0c1430068 cursor-agent: the binaries listed versus the argv invoked

`detect.binaries` lists `agent` and `cursor-agent` while `send.argv` invokes `agent`. The docs were
re-read on 2026-10-16 (headless and reference/parameters): the invoked command is still `agent` (the
install script is at cursor.com/install; the package installs under the `cursor-agent` name), and
`-p/--print`, `--force`, `--resume [chatId]`, `--output-format` and `--model` stand as declared. The
binary list stays — both spellings can stand on disk — and `verified_source` records the re-read.

### 45a1859e41b240fb kiro: the billing token store off macOS

Measured on macOS: the store stands on disk (`~/Library/Application Support/kiro-cli/data.sqlite3`,
21,942,272 bytes, mode 600), and the binary (Kiro CLI.app's `kiro-cli`, 2.27.1) loads its builder-id
token "from the secret store" and names `data.sqlite3` among its strings — but it resolves the store's
directory at runtime and embeds no other platform's template. The paths off macOS are therefore
unverified, and a `token_store_note` now says so; ao reads this store read-only.

### fbc104976ab5bb0e kiro: the 744-hour stop against the reset day

744 h = 31 days, the longest run from one day-1 reset (`billing.reset_day`) to the next, so a slice
parked on this stop is never held past a reset it could act on. The stop's consumer only honours a
reset the harness names, and only within its own bound (`at < reset <= at + hours*3600`); with no
reset named, the park falls back to `watchdog.quota_retry_hours` (5 h default) and tries again in
between. The `stops_note` now carries this rationale beside the three 744s.

### 45e23501b2742226 pr-agent: a prompt past ARG_MAX

Handled at landing by PROMPT-CHANNEL before this measurement: `prompt_plan` refuses cleanly or routes
the prompt to stdin *before* the process is spawned, so an oversized prompt never becomes a broken
spawn. Measured on macOS for the record: `getconf ARG_MAX` reads 1048576; an exec probe with a
1,048,000-character single argument passed and one at 1,100,000 failed with E2BIG; a probe with a
1,000,000-character environment passed; the guard refuses when the total plus its 65536 margin exceeds
SC_ARG_MAX — i.e. above 983,040 characters — which is the conservative side of every reading above.
No change; the numbers are recorded here and the machine holds `tests/test_prompt_channel.py`.

### b04f23b3fde33d3f pr-agent: the max-model-tokens pin

Measured on the PyPI 0.45.0 wheel: `ask`'s log reads "total tokens under limit" 32000 unpinned and
1000000 with `CONFIG__MAX_MODEL_TOKENS=1000000`. The setting is consumed once, in `get_max_tokens`
(`pr_agent/algo/utils.py`), as `min(config.max_model_tokens, the model's table value)`, so the pin
lifts the default cap to 1,000,000 for the pinned model (`configuration.toml` ships 32000 as the
default; `gpt-5.6` sits at 1,050,000 in the table, so the min is 1,000,000). The environment note now
carries the reading; the pin itself was already declared and stays.

### 9b9354b9d5956718 pr-agent: tool errors on 0.45

Measured on the same wheel, without provider credentials: `ask` ran, every litellm fallback failed,
`_handle_request` logged "Failed to process the command." with a traceback, returned False — and the
process exited 0 with no answer file. `CONFIG__PROPAGATE_TOOL_ERRORS=true` changed neither the exit
code nor the file: at 0.45 only `pr_reviewer`, `pr_code_suggestions` and `pr_description` read the
setting, not the `ask` tool this adapter runs. Upstream main's `cli.py` ends `raise SystemExit(run())`
and returns 1 under the setting — the claim is true of main, not of 0.45. The adapter's `notes` now
carry both readings; ao's reviewer reads the answer channel, and the exit-code ambiguity is recorded
for the adoption run that is still the owner's (#86).

### 15f50f8ae9a80dca journal: a lease expiry on the wall clock

Kept, deliberately, and the docstring now says why: a lease must outlive the process and a reboot,
which a monotonic reading cannot. A clock step only moves when a lease ends, and the reader of a passed
lease re-verifies the recorded pid and start against the process table (`running`), so a jump back can
delay a wake and a jump forward can end a lease early, but neither starts a second turn into the same
session. A test pins the wording, so the rationale cannot quietly disappear.

## What was run

| claim | what was run | reading |
|-------|--------------|---------|
| e07a370ec15451dd | codex-cli 0.160.0 (npx, isolated `CODEX_HOME`): `codex exec resume --sandbox read-only` and `codex exec resume -c sandbox_mode="read-only"` | first errors "unexpected argument '--sandbox' found"; second is accepted |
| a5936652edde4fab | `git ls-remote` of the base repo for the fork's branch and its pull ref; bare-clone fetch probes for `{branch}`, `pull/4143/head` and `refs/pull/4143/head` | branch absent; only the pull-namespace forms fetch |
| 47a259b0c1430068 | cursor.com/docs/cli headless and reference/parameters, re-read 2026-10-16 | the invoked command is `agent`; flags as declared |
| f8c734050f8efa6b | gemini-cli reference on main, read 2026-10-16 | `-r/--resume` takes id, index or "latest": `gemini -r "<session-id>" "query"` |
| 45a1859e41b240fb | stat of the kiro sqlite store (read-only); strings of the `kiro-cli` 2.27.1 binary | store stands, 21,942,272 bytes, mode 600; token "from the secret store"; no cross-platform path embedded |
| fbc104976ab5bb0e | arithmetic against `billing.reset_day`; read of the stop's consumer and `quota_park_for` | 744 h = 31 days = the longest month between day-1 resets |
| 45e23501b2742226 | `getconf ARG_MAX`; exec probes at 1,048,000 and 1,100,000 characters; an environment probe at 1,000,000 | 1048576; ok, E2BIG; ok; guard refuses above 983,040 |
| b04f23b3fde33d3f | PyPI 0.45.0 wheel in a throwaway venv: `ask` with and without `CONFIG__MAX_MODEL_TOKENS`; reads of `config_loader.py` and `algo/utils.py` | budget log reads 32000 then 1000000; consumed once in `get_max_tokens` |
| 9b9354b9d5956718 | the same wheel: `ask` with no provider credentials, then with `CONFIG__PROPAGATE_TOOL_ERRORS=true`; raw `cli.py` of main | exit 0 and no answer file either way; main returns 1 under the setting |
| 8db125ad4fc750c0 | read-only gap walker over the session stores: reviewer store 217 `qodercli` sessions, plus the IDE stores, turn-bounded | live-turn quiet to 612 s (523/520/494/416 beside); IDE turns 599/606; hour-scale gaps are closures |
| 15f50f8ae9a80dca | read of `journal.py` (`claim`, `under_way`, `_connect`) and the consumers that re-verify pid and start | wall clock deliberate; neither jump starts a second turn |

## What a copy of the tree can check

Every value above that the tree now carries, by path:

| path | what it holds |
|------|---------------|
| `src/ao/adapters/cloud-generic.json` | `result.fetch == ["git", "fetch", "origin", "refs/pull/{pr}/head"]`, and `fetch_note` with the fork reading |
| `src/ao/adapters/gemini.json` | `resume.argv == ["gemini", "--resume", "{session}", "--prompt", "{prompt}"]`, and the reference reading in its note |
| `src/ao/adapters/qoder.json` | `busy.idle_seconds == 900`, and the walk's numbers in the note |
| `src/ao/adapters/codex.json` | `options.sandbox == ["--sandbox", "{mode}"]`, no `--sandbox` in any resume argv, and the 0.160.0 recheck in `sandbox_note` |
| `src/ao/adapters/kiro.json` | three 744 h stops, "744 h = 31 days" in `stops_note`, and `token_store_note` |
| `src/ao/adapters/pr-agent.json` | `CONFIG__MAX_MODEL_TOKENS=1000000` and `CONFIG__PROPAGATE_TOOL_ERRORS=true` pinned; `get_max_tokens`/32000 in the environment note; "exited 0 with no answer file" and `raise SystemExit(run())` in `notes`; the wheel named in `disclaimer` |
| `src/ao/adapters/cursor-agent.json` | the re-read in `verified_source`; `detect.binaries == ["agent", "cursor-agent"]` |
| `src/ao/journal.py` | the wall-clock rationale in `claim`'s docstring |
| `tests/test_harness_measure.py` | nine tests pinning the above — three holding the fixes, five holding the kept readings, one holding that no `verified` level moved |

The raw measurements behind the note (probe logs, the throwaway venv, the walker's output) were made on
one machine and are not moved into the tree; every number the text states stands above as a value the
tree can be checked against.
