# SECRETS-2 — finish secret setup and message delivery

## Why one slice

one slice: the four LEADS-SECRETS follow-ups close the same setup-to-delivery
boundary: enter a channel's secret without exposing it, start reading at the
right place, and preserve each delivered message under its own ID. The setup
help, doctor hint, channel state, mail callers and documentation travel together
so the documented setup leads to the delivery behavior tested here.

## Invariant

Setting up a channel keeps its secret private and leaves every incoming message
available under an ID that no earlier message owned.

## Scenarios

- Ping setup reads an unseen terminal prompt or a named environment variable,
  refuses a URL on the command line without quoting it, and validates the URL
  before saving. The doctor names the actual setup command.
- Changing a Telegram bot token removes only the old offset; keeping the token
  keeps its offset. The new bot's first messages reach the mailbox.
- Main's mail writer owns exclusive creation, historical ID reservation and
  suffix allocation. Every caller uses the name it returns; handoff and PR
  callers pass the original base name without allocating a suffix first.
- After acknowledgement, another message in the same minute remains unseen and
  available in both mail stores. A competing writer taking suffix `-2` makes
  handoff and PR mail use `-3`, with matching returned names and envelope IDs.

## Paths

- src/ao/cli.py
- src/ao/parts/cli_channels.py
- src/ao/parts/cli_checks.py
- src/ao/parts/cli_maintenance.py
- src/ao/parts/lib_pr.py
- src/ao/telegram.py
- src/ao/skill/SKILL.md
- docs/alarms.md
- docs/commands.md
- docs/commands.tr.md
- docs/features.md
- docs/messaging.md
- docs/privacy.md
- docs/telegram.md
- docs/slices/SECRETS-2.md (new)
- tests/test_secrets_2.py (new)
- tests/test_windows_say_why.py

## Out of scope

No new mail allocator, storage format, provider or channel; the mail writer
retains main's TRIAGE-2-MEDIUM implementation.
