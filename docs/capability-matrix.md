# Capability matrix

The capability matrix is an **opt-in, fail-closed** declaration of which provider,
model and tool a role intends to use. It gives review evidence and commit authority
a stable, vendor-neutral identity to compare. Provider, model, tool, binding and
family identifiers are project-owned opaque declarations; `ao` has no built-in
vendor or model-family table.

## Version 1

`.ao/config.json` may add a top-level `capability_matrix` and role binding
references:

```json
{
  "implementer": {
    "adapter": "implementation-adapter",
    "model": "runtime-implementation-model",
    "session": "auto",
    "binding": "implementation-binding"
  },
  "reviewer": {
    "binding": "primary-review-binding",
    "fallbacks": [
      {"binding": "fallback-review-binding"}
    ]
  },
  "capability_matrix": {
    "version": 1,
    "providers": {
      "provider-a": {"capabilities": ["invoke"]},
      "provider-b": {"capabilities": ["invoke"]}
    },
    "models": {
      "implementation-model": {
        "family": "family-a",
        "argument": "runtime-implementation-model",
        "capabilities": ["implementation"]
      },
      "primary-review-model": {
        "family": "family-b",
        "argument": "runtime-primary-review-model",
        "capabilities": ["semantic-review"]
      },
      "fallback-review-model": {
        "family": "family-c",
        "argument": "runtime-fallback-review-model",
        "capabilities": ["semantic-review"]
      }
    },
    "tools": {
      "implementation-tool": {
        "adapter": "implementation-adapter",
        "capabilities": ["prompt", "workspace-write"]
      },
      "primary-review-tool": {
        "adapter": "review-adapter-a",
        "argv": ["review-cli-a", "--prompt", "{prompt}", "--model", "{model}"],
        "capabilities": ["prompt"]
      },
      "fallback-review-tool": {
        "adapter": "review-adapter-b",
        "argv": ["review-cli-b", "--prompt", "{prompt}", "--model", "{model}"],
        "capabilities": ["prompt"]
      }
    },
    "bindings": {
      "implementation-binding": {
        "provider": "provider-a",
        "model": "implementation-model",
        "tool": "implementation-tool"
      },
      "primary-review-binding": {
        "provider": "provider-b",
        "model": "primary-review-model",
        "tool": "primary-review-tool"
      },
      "fallback-review-binding": {
        "provider": "provider-b",
        "model": "fallback-review-model",
        "tool": "fallback-review-tool"
      }
    }
  }
}
```

This example contains identifiers and executable placeholders only. Put no token,
credential or secret in a checked-in config. Runtime argv is never copied into
review evidence or the authority ledger.

Required capabilities are layered:

| Role | Provider | Model | Tool |
|---|---|---|---|
| implementer | `invoke` | `implementation` | `prompt`, `workspace-write` |
| reviewer | `invoke` | `semantic-review` | `prompt`, plus a valid argv template |

Extra capabilities are allowed. Reviewer argv is a shell-free list and must
provide usable `{prompt}` and `{model}` placeholders. `{model}` expands from the
bound model's `argument`. Unknown placeholders, invalid references, duplicate
reviewer bindings, malformed types, missing capabilities and mismatched concrete
implementer `adapter`/`model` fields are configuration errors.

## Independence and fallback

A strict reviewer is ineligible when its binding equals the implementer binding
**or** its bound model has the same `family` as the implementer's bound model.
Inline `reviewer.family` and fallback `family` fields cannot change this: family
comes only from the model declaration. Ineligible routes are never spawned. `ao`
continues in declaration order to the first independently eligible fallback.

Among eligible reviewers, existing runtime behavior is preserved. Missing tools,
timeouts, silence, quota and authentication failures advance to the next eligible
fallback. A substantive response with malformed review output becomes `INVALID`;
it does not shop the candidate to another reviewer. If every eligible route is
runtime-unavailable, `ao review` exits 3 and records no review round.

## Migration boundary

The boundary is the presence of the top-level key:

- **Absent:** legacy config, profile, review artifact and authority behavior is
  unchanged.
- **Present:** strict version-1 validation applies. A malformed matrix fails
  closed and never falls back to legacy behavior.

`ao init --profile ...` continues to write legacy role blocks. It does not opt an
existing or new project into strict mode. Add the matrix and binding references as
one deliberate configuration change. After opt-in, legacy review evidence cannot
authorize a commit; after opt-out, a strict grant cannot authorize one either.

Review-off or an explicit review waiver still bypasses the review gate, but the
strict matrix and implementer identity must remain valid. Strict grants retain the
safe matrix digest and identity needed for `ao commit-check` to detect later drift.

## Declared, not attested

The matrix proves **configuration consistency**, not provider truth. `ao` does not
ask a provider which model actually ran, cryptographically attest a process, or
verify that a declared family accurately describes a model. Evidence therefore
means “these validated declarations selected this process,” not “the provider
attested this model.” Operational attestation, if required, is a separate trust
layer.
