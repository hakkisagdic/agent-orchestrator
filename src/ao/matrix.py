"""Pure capability-matrix validation and identity resolution.

The matrix is deliberately declarative.  This module never reads files, inspects
installed binaries, spawns a process, or attests a provider/model/tool claim.  It
only validates what a project declared and returns JSON-safe identity snapshots
for the impure CLI boundary to measure and persist.
"""
import hashlib
import json
import re
import string

MATRIX_VERSION = 1
MATRIX_DIGEST_PREFIX = "sha256:"

_ID_RE = re.compile(r"^[^\s\x00-\x1f\x7f]+$")
_NORMAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_ALLOWED_PLACEHOLDERS = frozenset(("prompt", "model"))

IMPLEMENTER_REQUIREMENTS = {
    "provider": frozenset(("invoke",)),
    "model": frozenset(("implementation",)),
    "tool": frozenset(("prompt", "workspace-write")),
}
REVIEWER_REQUIREMENTS = {
    "provider": frozenset(("invoke",)),
    "model": frozenset(("semantic-review",)),
    "tool": frozenset(("prompt",)),
}


class MatrixError(ValueError):
    """A closed, user-fixable strict-configuration failure."""

    def __init__(self, problems, key="capability-matrix"):
        self.problems = tuple(str(problem) for problem in problems)
        self.key = key
        super().__init__("; ".join(self.problems))


def is_strict(cfg):
    """True only when a project opted in (including an unreadable opt-in)."""
    return isinstance(cfg, dict) and (
        "capability_matrix" in cfg or "_capability_matrix_error" in cfg
    )


def canonical_digest(value):
    """Canonical-JSON SHA-256 for a validated JSON value."""
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return MATRIX_DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def _problem(problems, path, message):
    problems.append("%s: %s" % (path, message))


def _mapping(value, path, problems):
    if not isinstance(value, dict):
        _problem(problems, path, "must be an object")
        return None
    return value


def _opaque_id(value, path, problems):
    if not isinstance(value, str):
        _problem(problems, path, "must be a string")
        return None
    if not value or value != value.strip() or not _ID_RE.fullmatch(value):
        _problem(problems, path, "must be a nonempty identifier without whitespace")
        return None
    return value


def _normal_id(value, path, problems):
    if not isinstance(value, str):
        _problem(problems, path, "must be a string")
        return None
    if not _NORMAL_ID_RE.fullmatch(value):
        _problem(problems, path, "must be a normalized lowercase identifier")
        return None
    return value


def _only_keys(value, allowed, path, problems):
    for key in value:
        if not isinstance(key, str):
            _problem(problems, path, "keys must be strings")
        elif key not in allowed:
            _problem(problems, "%s.%s" % (path, key), "is not supported")


def _capabilities(value, path, problems):
    if not isinstance(value, list):
        _problem(problems, path, "must be a list")
        return None
    result = []
    for index, item in enumerate(value):
        cap = _normal_id(item, "%s[%d]" % (path, index), problems)
        if cap is not None:
            if cap in result:
                _problem(problems, "%s[%d]" % (path, index), "duplicates %s" % cap)
            else:
                result.append(cap)
    return result


def _argv_template(value, path, problems):
    if not isinstance(value, list):
        _problem(problems, path, "must be a list")
        return None, frozenset()
    if not value:
        _problem(problems, path, "must not be empty")
        return None, frozenset()
    formatter = string.Formatter()
    fields = set()
    result = []
    for index, item in enumerate(value):
        item_path = "%s[%d]" % (path, index)
        if not isinstance(item, str):
            _problem(problems, item_path, "must be a string")
            continue
        if "\x00" in item:
            _problem(problems, item_path, "must not contain NUL")
            continue
        if index == 0 and (not item or item != item.strip()):
            _problem(problems, item_path, "executable must be a nonempty identifier")
        try:
            parsed = list(formatter.parse(item))
        except ValueError as exc:
            _problem(problems, item_path, "invalid template: %s" % exc)
            continue
        for _, field, format_spec, conversion in parsed:
            if field is None:
                continue
            if field not in _ALLOWED_PLACEHOLDERS:
                _problem(problems, item_path, "unknown placeholder {%s}" % field)
                continue
            if format_spec or conversion:
                _problem(problems, item_path, "placeholder formatting is not supported")
                continue
            if index == 0:
                _problem(problems, item_path, "executable cannot be a placeholder")
            fields.add(field)
        result.append(item)
    return result if len(result) == len(value) else None, frozenset(fields)


def _records(value, path, fields, problems):
    records = _mapping(value, path, problems)
    if records is None:
        return {}
    if not records:
        _problem(problems, path, "must not be empty")
    result = {}
    for raw_id, raw_record in records.items():
        item_id = _opaque_id(raw_id, "%s key" % path, problems)
        item_path = "%s.%s" % (path, raw_id)
        record = _mapping(raw_record, item_path, problems)
        if item_id is None or record is None:
            continue
        _only_keys(record, fields, item_path, problems)
        result[item_id] = record
    return result


def _identity(binding_id, binding, providers, models, tools):
    provider_id = binding["provider"]
    model_id = binding["model"]
    tool_id = binding["tool"]
    provider = providers[provider_id]
    model = models[model_id]
    tool = tools[tool_id]
    return {
        "binding": binding_id,
        "provider": provider_id,
        "model": model_id,
        "model_argument": model["argument"],
        "tool": tool_id,
        "adapter": tool.get("adapter"),
        "family": model["family"],
        "provider_capabilities": sorted(provider["capabilities"]),
        "model_capabilities": sorted(model["capabilities"]),
        "tool_capabilities": sorted(tool["capabilities"]),
    }


def _require_capabilities(identity, requirements, path, problems):
    labels = {
        "provider": "provider_capabilities",
        "model": "model_capabilities",
        "tool": "tool_capabilities",
    }
    for layer, required in requirements.items():
        actual = set(identity[labels[layer]])
        missing = sorted(required - actual)
        if missing:
            _problem(
                problems,
                path,
                "%s is missing required capabilities: %s"
                % (layer, ", ".join(missing)),
            )


def _role_binding(role, path, bindings, problems):
    block = _mapping(role, path, problems)
    if block is None:
        return None
    binding_id = _opaque_id(block.get("binding"), path + ".binding", problems)
    if binding_id is not None and binding_id not in bindings:
        _problem(problems, path + ".binding", "references unknown binding %s" % binding_id)
        return None
    return binding_id


def _concrete(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() != "auto"


def resolve(cfg, require_independent=True):
    """Validate and resolve a strict config.

    Returned dictionaries contain runtime argv templates for the CLI, but callers
    must persist only ``matrix``, identities and attempt summaries.  ``argv`` is
    intentionally absent from every identity snapshot.
    """
    if not is_strict(cfg):
        raise MatrixError(("capability_matrix is absent",))
    load_error = cfg.get("_capability_matrix_error")
    if load_error:
        raise MatrixError(("capability_matrix: configuration JSON is malformed",))

    problems = []
    matrix = _mapping(cfg.get("capability_matrix"), "capability_matrix", problems)
    if matrix is None:
        raise MatrixError(problems)
    _only_keys(
        matrix, frozenset(("version", "providers", "models", "tools", "bindings")),
        "capability_matrix", problems,
    )
    version = matrix.get("version")
    if type(version) is not int:
        _problem(problems, "capability_matrix.version", "must be integer 1")
    elif version != MATRIX_VERSION:
        _problem(problems, "capability_matrix.version", "unsupported version %s" % version)

    providers_raw = _records(
        matrix.get("providers"), "capability_matrix.providers",
        frozenset(("capabilities",)), problems,
    )
    models_raw = _records(
        matrix.get("models"), "capability_matrix.models",
        frozenset(("family", "argument", "capabilities")), problems,
    )
    tools_raw = _records(
        matrix.get("tools"), "capability_matrix.tools",
        frozenset(("adapter", "argv", "capabilities")), problems,
    )
    bindings_raw = _records(
        matrix.get("bindings"), "capability_matrix.bindings",
        frozenset(("provider", "model", "tool")), problems,
    )

    providers = {}
    for item_id, record in providers_raw.items():
        caps = _capabilities(
            record.get("capabilities"),
            "capability_matrix.providers.%s.capabilities" % item_id, problems,
        )
        if caps is not None:
            providers[item_id] = {"capabilities": caps}

    models = {}
    for item_id, record in models_raw.items():
        base = "capability_matrix.models.%s" % item_id
        family = _normal_id(record.get("family"), base + ".family", problems)
        argument = record.get("argument")
        if not isinstance(argument, str):
            _problem(problems, base + ".argument", "must be a string")
            argument = None
        elif not argument or not argument.strip() or "\x00" in argument or "\n" in argument:
            _problem(problems, base + ".argument", "must be a nonempty single-line runtime argument")
            argument = None
        caps = _capabilities(record.get("capabilities"), base + ".capabilities", problems)
        if family is not None and argument is not None and caps is not None:
            models[item_id] = {
                "family": family, "argument": argument, "capabilities": caps,
            }

    tools = {}
    for item_id, record in tools_raw.items():
        base = "capability_matrix.tools.%s" % item_id
        adapter = None
        if "adapter" in record:
            adapter = _opaque_id(record.get("adapter"), base + ".adapter", problems)
        argv, placeholders = None, frozenset()
        if "argv" in record:
            argv, placeholders = _argv_template(record.get("argv"), base + ".argv", problems)
        caps = _capabilities(record.get("capabilities"), base + ".capabilities", problems)
        if caps is not None and ("adapter" not in record or adapter is not None) \
                and ("argv" not in record or argv is not None):
            tools[item_id] = {
                "adapter": adapter, "argv": argv,
                "placeholders": placeholders, "capabilities": caps,
            }

    bindings = {}
    for item_id, record in bindings_raw.items():
        base = "capability_matrix.bindings.%s" % item_id
        refs = {}
        for field, declared in (("provider", providers_raw), ("model", models_raw), ("tool", tools_raw)):
            ref = _opaque_id(record.get(field), base + "." + field, problems)
            if ref is not None and ref not in declared:
                _problem(problems, base + "." + field, "references unknown %s %s" % (field, ref))
            refs[field] = ref
        if all(refs.values()):
            bindings[item_id] = refs

    impl_block = cfg.get("implementer")
    impl_binding = _role_binding(
        impl_block, "implementer", bindings_raw, problems
    )
    reviewer_block = _mapping(cfg.get("reviewer"), "reviewer", problems)
    reviewer_bindings = []
    if reviewer_block is not None:
        primary_binding = _role_binding(reviewer_block, "reviewer", bindings_raw, problems)
        if primary_binding is not None:
            reviewer_bindings.append(primary_binding)
        fallbacks = reviewer_block.get("fallbacks", [])
        if not isinstance(fallbacks, list):
            _problem(problems, "reviewer.fallbacks", "must be a list")
        else:
            for index, fallback in enumerate(fallbacks):
                binding_id = _role_binding(
                    fallback, "reviewer.fallbacks[%d]" % index, bindings_raw, problems
                )
                if binding_id is not None:
                    reviewer_bindings.append(binding_id)
    seen = set()
    for binding_id in reviewer_bindings:
        if binding_id in seen:
            _problem(problems, "reviewer.fallbacks", "duplicates reviewer binding %s" % binding_id)
        seen.add(binding_id)

    if problems:
        raise MatrixError(problems)

    # Every referenced record survived scalar/list validation if the binding is
    # usable.  Report broken referenced records as closed configuration errors.
    referenced = [impl_binding] + reviewer_bindings
    for binding_id in referenced:
        binding = bindings.get(binding_id)
        if binding is None or binding["provider"] not in providers \
                or binding["model"] not in models or binding["tool"] not in tools:
            _problem(problems, "capability_matrix.bindings.%s" % binding_id,
                     "references an invalid declaration")
    if problems:
        raise MatrixError(problems)

    implementer_identity = _identity(impl_binding, bindings[impl_binding], providers, models, tools)
    _require_capabilities(
        implementer_identity, IMPLEMENTER_REQUIREMENTS,
        "implementer.binding %s" % impl_binding, problems,
    )

    impl = impl_block
    for field in ("adapter", "model"):
        if field in impl and not isinstance(impl.get(field), str):
            _problem(problems, "implementer.%s" % field, "must be a string")
    adapter = impl.get("adapter")
    if _concrete(adapter) and adapter != implementer_identity["adapter"]:
        _problem(
            problems, "implementer.adapter",
            "does not match binding %s adapter %s"
            % (impl_binding, implementer_identity["adapter"] or "<none>"),
        )
    model = impl.get("model")
    if _concrete(model) and model != implementer_identity["model_argument"]:
        _problem(
            problems, "implementer.model",
            "does not match binding %s model argument %s"
            % (impl_binding, implementer_identity["model_argument"]),
        )

    routes = []
    for index, binding_id in enumerate(reviewer_bindings):
        binding = bindings[binding_id]
        identity = _identity(binding_id, binding, providers, models, tools)
        path = "reviewer.binding" if index == 0 else "reviewer.fallbacks[%d].binding" % (index - 1)
        _require_capabilities(identity, REVIEWER_REQUIREMENTS, path + " " + binding_id, problems)
        tool = tools[binding["tool"]]
        if tool.get("argv") is None:
            _problem(problems, path, "reviewer tool must declare a nonempty argv template")
        else:
            missing = sorted(_ALLOWED_PLACEHOLDERS - tool["placeholders"])
            if missing:
                _problem(
                    problems, path,
                    "reviewer argv must expand placeholders: %s" % ", ".join(missing),
                )
        if binding_id == impl_binding:
            eligible, reason = False, "same binding as implementer"
        elif identity["family"] == implementer_identity["family"]:
            eligible, reason = False, "same model family as implementer"
        else:
            eligible, reason = True, ""
        routes.append({
            "index": index,
            "identity": identity,
            "argv": list(tool.get("argv") or []),
            "eligible": eligible,
            "ineligible_reason": reason,
        })

    if problems:
        raise MatrixError(problems)
    if require_independent and not any(route["eligible"] for route in routes):
        raise MatrixError(
            ("reviewer chain has no binding independent of the implementer binding and model family",),
            key="no-independent-reviewer",
        )

    return {
        "strict": True,
        "version": MATRIX_VERSION,
        "digest": canonical_digest(matrix),
        "matrix": {"version": MATRIX_VERSION, "digest": canonical_digest(matrix)},
        "implementer_identity": implementer_identity,
        "reviewers": routes,
    }


def expand_argv(route, prompt):
    """Expand a validated reviewer route without invoking a shell."""
    if not isinstance(prompt, str) or not prompt:
        raise MatrixError(("review prompt must be a nonempty string",))
    model = route["identity"]["model_argument"]
    try:
        argv = [part.format(prompt=prompt, model=model) for part in route["argv"]]
    except (KeyError, ValueError) as exc:
        raise MatrixError(("reviewer argv expansion failed: %s" % exc,))
    if not argv or not argv[0]:
        raise MatrixError(("reviewer argv expansion produced no executable",))
    return argv


def initial_attempts(resolution):
    """Safe, complete ordered chain snapshot; no argv or process output."""
    attempts = []
    for route in resolution["reviewers"]:
        if route["eligible"]:
            outcome, reason = "not-attempted", ""
        else:
            outcome, reason = "ineligible", route["ineligible_reason"]
        attempts.append({
            "binding": route["identity"]["binding"],
            "outcome": outcome,
            "reason": reason,
        })
    return attempts


def set_attempt(attempts, route, outcome, reason=""):
    """Update one safe attempt row by its unique declared binding."""
    binding = route["identity"]["binding"]
    for attempt in attempts:
        if attempt["binding"] == binding:
            attempt["outcome"] = str(outcome)
            attempt["reason"] = str(reason)
            return
    raise MatrixError(("reviewer attempt binding %s is not in the resolved chain" % binding,))


def safe_unavailable_reason(reason):
    """Collapse arbitrary process text to a credential-safe persisted reason."""
    text = str(reason or "").lower()
    if text.startswith("timeout after"):
        return "timeout"
    if text.startswith("produced nothing"):
        return "no output"
    return "runtime unavailable"


def _role_bindings(resolution):
    return {
        "implementer": resolution["implementer_identity"]["binding"],
        "reviewers": [
            route["identity"]["binding"] for route in resolution["reviewers"]
        ],
    }


def add_evidence_context(evidence, resolution, attempts, reviewer_identity=None,
                         review_status="pending"):
    """Attach schema-3 declared identity evidence; never attach argv."""
    evidence.update({
        "schema": 3,
        "matrix": dict(resolution["matrix"]),
        "role_bindings": _role_bindings(resolution),
        "implementer_identity": dict(resolution["implementer_identity"]),
        "reviewer_identity": dict(reviewer_identity) if reviewer_identity else None,
        "reviewer_attempts": [dict(attempt) for attempt in attempts],
        "review_status": review_status,
    })
    return evidence


def review_evidence_problems(resolution, evidence):
    """Validate authorizable strict prospective evidence against live config."""
    problems = []
    if not isinstance(evidence, dict):
        return ["strict review evidence is not a structured object"]
    if evidence.get("schema") != 3:
        problems.append("strict review evidence must use schema 3")
    if evidence.get("matrix") != resolution["matrix"]:
        problems.append("strict review matrix version or digest does not match current config")
    if evidence.get("role_bindings") != _role_bindings(resolution):
        problems.append("strict review role bindings do not match current config")
    if evidence.get("implementer_identity") != resolution["implementer_identity"]:
        problems.append("strict review implementer identity does not match current config")
    if evidence.get("review_status") != "complete":
        problems.append("strict review did not complete")

    reviewer_identity = evidence.get("reviewer_identity")
    selected = next(
        (route for route in resolution["reviewers"]
         if route["eligible"] and route["identity"] == reviewer_identity),
        None,
    )
    if selected is None:
        problems.append("strict review reviewer identity is not independently eligible now")

    attempts = evidence.get("reviewer_attempts")
    expected_bindings = [route["identity"]["binding"] for route in resolution["reviewers"]]
    if not isinstance(attempts, list):
        problems.append("strict review attempts must be a list")
        attempts = []
    else:
        for attempt in attempts:
            if not isinstance(attempt, dict) or set(attempt) != {"binding", "outcome", "reason"} \
                    or not all(isinstance(attempt.get(key), str)
                               for key in ("binding", "outcome", "reason")):
                problems.append("strict review attempts contain a malformed or unsafe entry")
                break
        actual_bindings = [attempt.get("binding") for attempt in attempts
                           if isinstance(attempt, dict)]
        if actual_bindings != expected_bindings:
            problems.append("strict review attempts do not match the current reviewer chain")

    if selected is not None and len(attempts) == len(resolution["reviewers"]):
        selected_index = selected["index"]
        for route, attempt in zip(resolution["reviewers"], attempts):
            if not isinstance(attempt, dict):
                continue
            if not route["eligible"]:
                expected = "ineligible"
                valid_reasons = {route["ineligible_reason"]}
            elif route["index"] < selected_index:
                expected = "unavailable"
                valid_reasons = {
                    "tool not installed", "timeout", "no output", "runtime unavailable",
                }
            elif route["index"] == selected_index:
                expected = "reviewed"
                valid_reasons = {""}
            else:
                expected = "not-attempted"
                valid_reasons = {""}
            if attempt.get("outcome") != expected:
                problems.append(
                    "strict review attempt outcome for %s must be %s"
                    % (route["identity"]["binding"], expected)
                )
            if attempt.get("reason") not in valid_reasons:
                problems.append(
                    "strict review attempt reason for %s is not safe or canonical"
                    % route["identity"]["binding"]
                )
    return problems


def authority_fields(resolution, reviewer_identity=None):
    """Safe strict fields added to a schema-3 authority row."""
    safe_reviewer = next(
        (route["identity"] for route in resolution["reviewers"]
         if route["eligible"] and route["identity"] == reviewer_identity),
        None,
    )
    return {
        "matrix": dict(resolution["matrix"]),
        "role_bindings": _role_bindings(resolution),
        "implementer_identity": dict(resolution["implementer_identity"]),
        "reviewer_identity": dict(safe_reviewer) if safe_reviewer else None,
    }


def authority_problems(resolution, grant):
    """Validate the live strict matrix and implementer against a persisted grant."""
    problems = []
    if not isinstance(grant, dict) or grant.get("schema") != 3:
        problems.append("strict mode requires a schema-3 capability-matrix grant")
        return problems
    if grant.get("matrix") != resolution["matrix"]:
        problems.append("authority grant matrix version or digest has drifted")
    if grant.get("role_bindings") != _role_bindings(resolution):
        problems.append("authority grant role bindings have drifted")
    if grant.get("implementer_identity") != resolution["implementer_identity"]:
        problems.append("authority grant implementer identity has drifted")
    reviewer_identity = grant.get("reviewer_identity")
    if reviewer_identity is not None and not any(
        route["eligible"] and route["identity"] == reviewer_identity
        for route in resolution["reviewers"]
    ):
        problems.append("authority grant reviewer identity is no longer independently eligible")
    return problems
