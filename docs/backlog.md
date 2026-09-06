# ao backlog — what is left, in order

Maintained by the architect. Every item is a slice with an acceptance boundary
written as an invariant, so an implementer can take it without a conversation.
Nothing here is authorised until it is queued on a project board.

| # | Slice | Why now | Acceptance boundary (invariant) |
|---|---|---|---|
| 1 | **Interactive architect presence suppresses wakes for the whole session** | 2026-09-06 06:38: the watchdog woke a second architect while the first was at the keyboard but inside a 4-minute command; both acted on one inbox | While an interactive architect session for the project is alive (process, not recency of writes), the watchdog never spawns a wake; presence is measured, not declared; a dead session releases within one tick |
| 2 | **Rounds are scoped to a slice, not to a HEAD** | `round 5/3` counted B8 reviews at the same HEAD against E18-1 | `rounds()` counts only reviews whose boundary/slice matches the running item; reviews of other slices at the same HEAD never count; a test with two slices at one HEAD proves it |
| 3 | **Authority ledger tamper evidence** (Kiro's PR #2 finding) | `authority.jsonl` rows are unsigned and unchained; a same-user process can append a forged grant that `commit-check` then honours | Each row carries the digest of the previous row; `commit-check` refuses on a broken chain; the limit is documented: same-user access cannot be cryptographically excluded, only detected |
| 4 | **Reviewer verdict and health literals are closed enums** (Kiro's PR #2 finding) | free-form strings compared by equality drift silently | One enum owns verdict/health literals; parsing anything else is `INVALID`; the skill and docs list the same values, a test enforces the three agree |
| 5 | **Gate coverage check in `doctor` and `init`** | dükkan pilot: `gates.json` covered only the JS workspace, the .NET backend had no gate, and the implementer had no `dotnet` permission | `doctor --check` reports every top-level source tree with N files that no configured gate exercises; `init` refuses to write a `quick` profile that covers none of the detected toolchains without an explicit override |
| 6 | **Reports carry exit codes, not prose counts** | PR #2 body said "159 passed" while the suite exited 1 | `ao verify` and the report template record the gate exit code and the parsed summary line; a report whose text claims green while the newest verification failed is flagged `INCONSISTENT` |
| 7 | **Adversarial suite against commit authority** | the authority model changed twice in two days; attacks should be enumerated, not discovered | Ten named implementer attacks (stage swap, index race, forged review evidence, retro review as authority, waiver replay, coordination-path smuggling, hook removal, ledger truncation, digest collision by rename, GIT_INDEX_FILE spoof) each have a test that fails closed |
| 8 | **Secondary project queue** | 80 "queue empty" reports in one day while another project had READY work | When a project's READY set is empty and its blockers wait on a human or architect, the nudge names the READY item of a configured secondary project instead of asking the implementer to wait |
| 9 | **Windows completeness** | `cwd` is unavailable on Windows, hold semantics untested there, no toast channel | Windows CI lane green on demand; `procs.cwd` via PEB or documented `None` handling in every caller; `ao hold` proven on Windows; toast notification channel behind a feature switch |
| 10 | **Measured feature costs** | `features.md` percentages are estimates | `ao cost` attributes implementer spend per feature from ledgers; the doc shows measured values with the sample window, and the estimate column is removed |
| 11 | **Scenario fuzzing of the guard chain** | contradictions in the guard chain were found one at a time | The scenario World is randomised over N seeds; any cycle whose verdict contradicts a documented invariant (F1–F18) fails the run with the seed printed |
| 12 | **Release 0.5.0** | the installed tool already runs main; PyPI and Homebrew lag | After one landed slice under candidate-bound authority on Voltrai: version bump, changelog, tag, Homebrew formula; README EN and TR rows match the CLI |

Two policies stay outside the queue: hosted CI runs only on the maintainer's word, one
environment at a time; nothing in this repository lands without an independent review
of the exact candidate.
