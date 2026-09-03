# Model and effort control

Model choice and reasoning effort are the two biggest levers on both cost and quality,
and they should be a property of **the slice**, not a setting you forget you changed.

## Commands

```bash
ao model                          # what the implementer is running now
ao model list                     # models the adapter's CLI reports
ao model set gpt-5.6-sol          # for subsequent turns
ao effort set max                 # low | medium | high | xhigh | max (adapter-clamped)

ao run --model <m> --effort <e> "<prompt>"   # one turn only, no persistent change
```

Values are validated against the adapter's `effort_values` and clamped, so a profile
written for a CLI with five levels still works against one that has three.

## Per-slice policy

A policy file maps slice kinds to a model and effort, so routine work stops burning the
expensive configuration:

```yaml
# .ao/policy.yml
default:        { model: gpt-5.6-sol,  effort: high }
architecture:   { model: gpt-5.6-sol,  effort: max }     # contracts, security boundaries
implementation: { model: gpt-5.6-sol,  effort: high }
mechanical:     { model: gpt-5.6-luna, effort: low }     # exports, renames, formatting
verification:   { model: claude-opus-5, effort: high }   # a second opinion from another family
```

`mechanical` matters more than it looks. In a real run, an entire review cycle was spent
on one missing export from a barrel file — a task that does not need a frontier model at
maximum effort.

The `verification` row is deliberate: reviewing with a **different model family** than
the one that wrote the code catches failure modes a self-review shares and misses.

## Which CLIs expose what

| CLI | Model flag | Effort flag |
|---|---|---|
| Kiro CLI | `--model` | `--effort` (low…max) |
| Claude Code | `--model` | — |
| Antigravity | `--model` | `--effort` (low/medium/high) |
| Codex | `--model` | `-c model_reasoning_effort=` |
| Aider | `--model` | `--reasoning-effort`, `--thinking-tokens` |
| opencode | `--model provider/model` | — |
| Gemini / Cursor / Copilot / Q | `--model` | — |
| Amp | — (chosen internally) | — |

Where a CLI has no effort flag, the adapter sets `effort: null` and `ao` reports that the
lever is unavailable rather than silently ignoring your setting.

## Cost awareness

agent-orchestrator does not do cost accounting — that is
[keyflip's](keyflip.md) job. What it does is make the *decision* explicit and reviewable:
the policy file is committed, so "why is this slice running at max effort" has an answer
in version control.
