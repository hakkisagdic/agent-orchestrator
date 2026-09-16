# Configuration

ao decides nothing on your behalf that differs between people, projects or machines.
Each such value is a setting with a default, and `ao config` shows and changes them:

```
ao config list                               # every setting: value, default, where it came from
ao config get round_budget
ao config set round_budget 3                 # this project's .ao/config.json
ao config set quota.block_percent 90 --machine   # ~/.ao/settings.json, for every project here
ao config unset round_budget
```

A **project** setting is read from the project's `.ao/config.json`, then from the
machine's `~/.ao/settings.json`, then its default. A **machine** setting governs state
every project on the machine shares - alarm episodes, provider windows, heartbeats,
where binaries are found - and is read from `~/.ao/settings.json` only. Both files are
written whole or not at all. A dotted name is nested in the file:
`alarms.red_after_minutes` is `{"alarms": {"red_after_minutes": 60}}`.

A value that is set but cannot be used - text where a number belongs, a number out of
range - is passed over for the next layer, and `ao doctor` names it with the value ao
uses instead. So does a name under one of these groups that ao does not read, and a
machine setting written into a project.

| Setting | Default | Scope | What it decides |
|---|---|---|---|
| `round_budget` | `5` | project | review rounds a slice may spend before the watchdog stops nudging and tells a person |
| `review.max_inflight` | `2` | project | submitted reviews that may run at once; a submit beyond it names what to collect |
| `review_timeout` | `900` | project | seconds one reviewer may take; never taken from the command line |
| `stall_minutes` | `60` | project | minutes a staged candidate may wait to land before throughput calls the slice stalled |
| `gates.default_timeout` | `600` | project | seconds a gate may run when its own definition names no timeout |
| `gates.coverage_min_files` | `5` | project | source files of one toolchain a top-level tree must hold before a gate must exercise it |
| `watchdog.idle_minutes` | `6.0` | project | minutes of implementer silence before the watchdog acts |
| `watchdog.max_attempts` | `3` | project | nudges without progress before a person is told the implementer is stuck |
| `decisions.human_after_minutes` | `15` | project | minutes an open decision waits before it rings a person |
| `waivers.default_hours` | `24` | project | hours a review waiver stays open when --hours is not given |
| `waivers.max_hours` | `168` | project | the longest a review waiver may be granted for |
| `alarms.red_after_minutes` | `60` | project | minutes an orange alarm stands before it turns red and sends one e-mail |
| `fanout.max_agents` | `12` | project | the most agents one fan-out may start |
| `fanout.per_agent_tokens` | `50000` | project | the token budget each fanned-out agent is given |
| `fanout.window_reserve_pct` | `30` | project | percent of the provider window a fan-out must leave unused |
| `implementer.name` | `kiro` | project | the implementer's name in mail file names |
| `architect.name` | `fable` | project | the architect's name in mail file names |
| `alarms.red_repeat_hours` | `6` | machine | hours before a red alarm that still stands e-mails again |
| `alarms.reset_after_hours` | `2` | machine | hours of quiet after which an alarm episode is over |
| `quota.block_percent` | `97` | machine | a provider window used at or above this percent stops a wake or a nudge |
| `architect.quota_window_hours` | `5` | machine | the architect's usage window; a reset named further away is not the one meant |
| `heartbeat.retired_days` | `7` | machine | days of watchdog silence after which a project counts as retired, not dead |
| `fleet.window_reserve_pct` | `20` | machine | percent of the machine's provider window kept free before a report wake |
| `binaries.extra_dirs` | `none` | machine | directories searched for agent binaries after PATH, before the usual install locations |

The scheduled watchdog reads `watchdog.idle_minutes` when it is installed; run
`ao watchdog install` again after changing it. Everything else is read when it is used.

An implementer whose tool grant admits `ao config set` could lengthen or shorten what
governs its own review, and `ao doctor` names such a grant. Credentials - the e-mail relay
token, the Telegram bot token - are not settings; they stay in their own files under
`~/.ao`, outside any repository.
