"""Roles and adapters: the role table, adapter layers, the vendor list and profiles, composed
reviewers.

A part of src/ao/lib.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


# ---- the role table is real, and messages are addressed to roles (#79, #31) -------------

ROLE_BLOCKS = ("implementer", "architect", "reviewer")


def effective_roles(root, cfg):
    """The role → actor assignment in force: `roles`, or `roles_next` once the slice it waits on left running (#79)."""
    roles = dict(cfg.get("roles") or {})
    pending = cfg.get("roles_next")
    if isinstance(pending, dict) and isinstance(pending.get("roles"), dict):
        running = {item["id"] for item in board(root)["running"]} if root else set()
        if pending.get("after") not in running:
            roles.update(pending["roles"])
    return roles


def resolve_roles(root, cfg):
    """Fill each role's block from the actor table, when the project keeps one (#79).

    `docs/roles.md` described actors, roles and `ao role set` long before any of it
    existed. The table lives in `.ao/config.json` as `actors` and `roles`; every
    reader keeps reading the role blocks it always read, now resolved from it.
    """
    actors = cfg.get("actors")
    if not isinstance(actors, dict) or not isinstance(cfg.get("roles"), dict):
        return cfg
    for role, actor in effective_roles(root, cfg).items():
        if role in ROLE_BLOCKS and isinstance(actors.get(actor), dict):
            cfg[role] = dict(actors[actor], actor=actor)
    return cfg


def role_table(cfg):
    """(actors, roles, pending) as configured, or bootstrapped from the role blocks when there is no table yet."""
    actors = {name: dict(block) for name, block in (cfg.get("actors") or {}).items() if isinstance(block, dict)}
    roles = dict(cfg.get("roles") or {})
    if not actors:
        for role in ROLE_BLOCKS:
            block = cfg.get(role)
            if isinstance(block, dict) and block:
                name = str(block.get("actor") or block.get("name") or block.get("id") or role)
                actors[name] = {key: value for key, value in block.items() if key != "actor"}
                roles[role] = name
    return actors, roles, cfg.get("roles_next")


def assignment_problem(actors, roles, repository="tool", hotfix=False):
    """Why an assignment would break separation of duties, or None (#79, #13).

    Roles may rotate per slice on a tool repository. On a product repository the
    architect does not implement, except a hotfix a person names as one. Wherever
    roles stand, the reviewer is never the implementer's actor or family.
    """
    implementer, reviewer = roles.get("implementer"), roles.get("reviewer")
    if repository != "tool" and not hotfix and implementer and implementer == roles.get("architect"):
        return (f"on a product repository the architect does not implement ({implementer} holds both); roles rotate "
                "on a tool repository (repository.kind tool), or name a hotfix with --hotfix")
    if implementer and reviewer and implementer == reviewer:
        return f"the reviewer and the implementer would both be {implementer}; no actor reviews its own work"
    families = [str((actors.get(actor) or {}).get("family") or "").lower() for actor in (implementer, reviewer)]
    if all(families) and families[0] == families[1]:
        return f"the reviewer and the implementer would both be of the {families[0]} family"
    return None


def role_of(name, cfg):
    """The role a name in a message stands for: architect, implementer, watchdog or human (#31)."""
    implementer, architect = mail_names(cfg)
    lowered = str(name or "").strip().lower()
    if lowered in (architect.lower(), "architect"):
        return "architect"
    if lowered in (implementer.lower(), "implementer"):
        return "implementer"
    if lowered == "watchdog":
        return "watchdog"
    return "human" if lowered in ("human", "person", "owner") else None


def write_project_config(root, text):
    """Write `.ao/config.json` whole or not at all (#56)."""
    from .storage import replace_file_durably
    replace_file_durably(os.path.join(root, ".ao", "config.json"), text.encode(UTF8))


# ---- adapters load from outside the package too (#77) ------------------------------------

ADAPTER_CONTRACT = 1
ADAPTER_VERIFIED = ("full", "partial", "documented", "untested", "planned")
ADAPTER_PLACEHOLDERS = ("prompt", "session", "model", "effort", "mode", "cwd", "escaped_cwd", "workspace_hash",
                        "timeout", "tools", "schema", "max_steps", "provider", "name", "n", "tokens", "dir", "path",
                        "agent", "agent_login", "branch", "issue", "lane", "pr", "url", "diff_file", "output")


def adapter_layers(root=None):
    """[(source, directory)] searched for adapters, lowest first: the package, the user's, the project's (#77)."""
    layers = [("package", adapters_dir()),
              ("user", os.environ.get("AO_USER_ADAPTERS") or os.path.join(HOME, ".ao", "adapters"))]
    if root:
        layers.append(("project", os.path.join(root, ".ao", "adapters")))
    return layers


def adapter_catalog(root=None):
    """{id: {"source", "path", "adapter", "problem"}} for every adapter found, a later layer overriding by id.

    An adapter that declares a contract this ao does not implement is listed with
    the problem and never loaded half-way.
    """
    found = {}
    for source, directory in adapter_layers(root):
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".json") or name in DATA_FILES:
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, encoding=UTF8) as fh:
                    adapter = json.load(fh)
            except (OSError, ValueError) as exc:
                found[name[:-5]] = {"source": source, "path": path, "adapter": {}, "problem": f"unreadable: {exc}"}
                continue
            if not isinstance(adapter, dict):
                continue
            ident = str(adapter.get("id") or name[:-5])
            contract = adapter.get("contract", ADAPTER_CONTRACT)
            problem = None if contract == ADAPTER_CONTRACT else (
                f"declares adapter contract {contract!r}; this ao implements contract {ADAPTER_CONTRACT}")
            found[ident] = {"source": source, "path": path, "adapter": adapter, "problem": problem}
    return found


def load_adapter(adapter_id, root=None):
    entry = adapter_catalog(root).get(adapter_id)
    return {} if not entry or entry["problem"] else entry["adapter"]


# ---- the adapter set derives from one canonical vendor list (#89) ------------------------

VENDORS_FILE = "vendors.json"


PROFILES_FILE = "profiles.json"
DATA_FILES = (VENDORS_FILE, PROFILES_FILE)      # beside the adapters, and not adapters


_PROFILE_DOCUMENT = {}


def _profile_document():
    """adapters/profiles.json, read once per state of the file: mail names consult it for every message."""
    path = os.path.join(adapters_dir(), PROFILES_FILE)
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return {}
    if key not in _PROFILE_DOCUMENT:
        try:
            with open(path, encoding=UTF8) as fh:
                document = json.load(fh)
        except (OSError, ValueError):
            document = {}
        _PROFILE_DOCUMENT.clear()
        _PROFILE_DOCUMENT[key] = document if isinstance(document, dict) else {}
    return _PROFILE_DOCUMENT[key]


def profiles():
    """{name: {role: adapter id}}: the presets `ao init --profile` offers, from adapters/profiles.json (#76)."""
    found = _profile_document().get("profiles")
    return {name: dict(roles) for name, roles in found.items() if isinstance(roles, dict)} \
        if isinstance(found, dict) else {}


def default_profile():
    """The profile a new project is pointed at."""
    return str(_profile_document().get("init") or next(iter(sorted(profiles())), ""))


def implementer_actor_name(cfg):
    """The implementer's name when none is set: its adapter's `actor_name`, else the default profile's (#76).

    A project with no implementer block keeps the name its mail was always written
    under, because the default profile's implementer declares that name.
    """
    ident = ((cfg or {}).get("implementer") or {}).get("adapter") \
        or (profiles().get(default_profile()) or {}).get("implementer") or ""
    return str(package_adapters().get(ident, {}).get("actor_name") or ident or "implementer")


def implementer_adapter(cfg=None):
    """The implementer's adapter: the one its block names, else the default profile's implementer (#76).

    A transcript reader asked without an adapter reads what the implementer's adapter
    declares, never one harness's record shape written into the reader.
    """
    ident = ((cfg or {}).get("implementer") or {}).get("adapter") \
        or (profiles().get(default_profile()) or {}).get("implementer") or ""
    return load_adapter(str(ident), (cfg or {}).get("root")) if ident else {}


def vendor_list():
    """Every vendor ao knows, with the adapter that drives it or why none does (#89)."""
    try:
        with open(os.path.join(adapters_dir(), VENDORS_FILE), encoding=UTF8) as fh:
            vendors = json.load(fh).get("vendors")
    except (OSError, ValueError, AttributeError):
        return []
    return [vendor for vendor in vendors if isinstance(vendor, dict) and vendor.get("id")] \
        if isinstance(vendors, list) else []


def shipped_adapter_ids():
    """The ids of the adapters in the package itself, whatever a user or a project layers over them."""
    ids = set()
    try:
        names = os.listdir(adapters_dir())
    except OSError:
        return ids
    for name in names:
        if not name.endswith(".json") or name in DATA_FILES:
            continue
        try:
            with open(os.path.join(adapters_dir(), name), encoding=UTF8) as fh:
                ids.add(str(json.load(fh).get("id") or name[:-5]))
        except (OSError, ValueError, AttributeError):
            ids.add(name[:-5])
    return ids


def vendor_problems():
    """Where the shipped adapters and the vendor list disagree; empty when they agree (#89).

    An adapter no vendor names, a vendor naming an adapter that is not shipped, and a
    vendor with neither an adapter nor a reason are each an error here rather than a
    silent gap on whichever surface met it first.
    """
    vendors = vendor_list()
    if not vendors:
        return [f"adapters/{VENDORS_FILE} is missing or unreadable"]
    shipped = shipped_adapter_ids()
    problems, named = [], {}
    for vendor in vendors:
        adapter = vendor.get("adapter")
        if adapter:
            if adapter in named:
                problems.append(f"adapter {adapter} is named by both {named[adapter]} and {vendor['id']}")
            named[adapter] = vendor["id"]
            if adapter not in shipped:
                problems.append(f"vendor {vendor['id']} names adapter {adapter}, which is not shipped")
        elif not str(vendor.get("why") or "").strip():
            problems.append(f"vendor {vendor['id']} has no adapter and no reason for having none")
    for ident in sorted(shipped - set(named)):
        problems.append(f"adapter {ident} is shipped, but no vendor in adapters/{VENDORS_FILE} names it")
    return problems


def adapter_binaries(adapter):
    """The command names an adapter runs as: `detect.binaries` where it declares them, else what `send` runs."""
    adapter = adapter or {}
    declared = (adapter.get("detect") or {}).get("binaries") if isinstance(adapter.get("detect"), dict) else None
    if isinstance(declared, list) and declared:
        return [str(name) for name in declared if name]
    send = adapter.get("send")
    argv = send.get("argv") if isinstance(send, dict) else None
    return [os.path.basename(str(argv[0]))] if isinstance(argv, list) and argv else []


def absent_adapter_binaries(cfg):
    """(actor, adapter, binaries) for each configured actor whose adapter's command this machine lacks (#89).

    A command an optional extra installed beside the interpreter ao runs on is there too (#86).
    """
    import sys

    def present(name):
        beside = os.path.join(os.path.dirname(sys.executable), name) if sys.executable else ""
        return bool(binary_candidates(name)) or (os.path.isfile(beside) and os.access(beside, os.X_OK))

    actors, _, _ = role_table(cfg)
    out = []
    for actor, block in sorted(actors.items()):
        ident = block.get("adapter")
        if not ident:
            continue
        names = adapter_binaries(load_adapter(ident, cfg.get("root")))
        if names and not any(present(name) for name in names):
            out.append((actor, ident, names))
    return out


# ---- a reviewer invocation is built from its adapter (#88) --------------------------------

def reviewer_eligibility(adapter):
    """(eligible, why not) for the reviewer role, from what an adapter declares about denying tools (#88).

    A reviewer must not be able to write. An adapter declares `options.trust_none`:
    the flags that leave the harness only reading, or null with `trust_none_why`,
    which makes it ineligible rather than silently unsafe. An empty list is an answer
    only from a tool reviewer with a sound review contract (#86): ao runs it over a
    file it wrote, outside any repository, and there is nothing left to deny.
    """
    options = (adapter or {}).get("options") or {}
    if "trust_none" not in options:
        return False, "it does not declare how to run without tools (options.trust_none)"
    if options["trust_none"] == [] and (adapter or {}).get("review") is not None:
        problems = tool_review_problems(adapter)
        return (False, "its review contract is not sound: " + "; ".join(problems)) if problems else (True, None)
    if not options["trust_none"]:
        return False, str(options.get("trust_none_why") or ((adapter.get("roles") or {}).get("reviewer"))
                          or "it cannot be run without tools")
    return True, None


def compose_reviewer(adapter_id, model=None, effort=None, root=None, family=None):
    """A reviewer route from an adapter's send, model, effort and trust_none; never a hand-written argv (#88).

    A tool reviewer (#86) reaches many models through its own provider. Its route
    carries the model, which the adapter's review contract pins when ao runs it, and
    the family only a person can name: ao never infers one from a model name.
    """
    adapter = load_adapter(adapter_id, root)
    if not adapter:
        raise ValueError(f"no adapter {adapter_id}")
    eligible, why = reviewer_eligibility(adapter)
    if not eligible:
        raise ValueError(f"{adapter_id} is ineligible for the reviewer role: {why}")
    argv = list((adapter.get("send") or {}).get("argv") or [])
    if not argv:
        raise ValueError(f"{adapter_id} declares no send.argv")
    declared = str(adapter.get("family") or "").strip()
    if family and declared and family.strip().lower() != declared.lower():
        raise ValueError(f"{adapter_id} declares the {declared} family")
    if tool_review_contract(adapter) is not None:
        if not model:
            raise ValueError(f"{adapter_id} reaches many models; name the one it runs with --model")
        if effort:
            raise ValueError(f"{adapter_id} takes no effort")
        route = {"id": f"{adapter_id}-reviewer-{model}", "adapter": adapter_id, "kind": "tool", "argv": argv,
                 "composed": True, "model": model}
        if family:
            route["family"] = family.strip()
        return route
    options = adapter.get("options") or {}
    if model:
        if not options.get("model"):
            raise ValueError(f"{adapter_id} declares no way to choose a model")
        argv += [part.replace("{model}", model) for part in options["model"]]
    if effort:
        values = options.get("effort_values") or []
        if not options.get("effort") or (values and effort not in values):
            raise ValueError(f"{adapter_id} does not take effort {effort}" + (f"; it takes {', '.join(values)}" if values else ""))
        argv += [part.replace("{effort}", effort) for part in options["effort"]]
    argv += list(options["trust_none"])
    route = {"id": f"{adapter_id}-reviewer" + (f"-{model}" if model else ""), "adapter": adapter_id, "argv": argv,
             "composed": True}
    if model:
        route["model"] = model
    if family:
        route["family"] = family.strip()
    return route


# ---- a reviewer can be a tool ao runs over the candidate, on its own provider (#86) -------

TOOL_REVIEW_PLACEHOLDERS = ("prompt", "diff_file", "output", "model", "timeout")
TOOL_REVIEW_ENV_PLACEHOLDERS = ("model", "timeout")


def tool_review_problems(adapter, argv=None):
    """What a tool reviewer's review contract is missing or gets wrong; empty when there is none (#86).

    The contract says how ao hands the tool the candidate (a diff file ao writes), where
    the answer comes back, and which of the inherited environment the tool must not read:
    for a tool configured through its environment, whoever runs `ao review` could otherwise
    choose the reviewer's model or its instructions. `argv` is the template a configured
    route runs, `send.argv` when none is given. A model the contract does not pin is one ao
    could not honestly record, so a contract that never names {model} is refused.
    """
    review = (adapter or {}).get("review")
    if review is None:
        return []
    if not isinstance(review, dict):
        return ["`review` must be an object"]
    problems = []
    if review.get("candidate") != "diff-file":
        problems.append("`review.candidate` must be diff-file: ao hands a tool reviewer the candidate as a file")
    if "encoding" in review:
        try:
            "".encode(review["encoding"])
        except (LookupError, TypeError):
            problems.append(f"`review.encoding` is {review['encoding']!r}, which is not an encoding")
    answer = review.get("answer")
    if not isinstance(answer, dict) or answer.get("from") != "output":
        problems.append("`review.answer.from` must be output: the file ao names in {output}")
    elif "after_prompt" in answer and not (isinstance(answer["after_prompt"], str) and answer["after_prompt"].strip()
                                           and "\n" not in answer["after_prompt"]):
        problems.append("`review.answer.after_prompt` must be one line of text")
    if argv is None:
        send = adapter.get("send")
        argv = send.get("argv") if isinstance(send, dict) else None
    if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
        return problems + ["a tool reviewer's argv must be a list of strings"]
    used = [name for part in argv for name in re.findall(r"\{([a-z_]+)\}", part)]
    unknown = sorted(set(used) - set(TOOL_REVIEW_PLACEHOLDERS))
    if unknown:
        problems.append(f"a tool reviewer's argv uses placeholders ao does not fill: {', '.join(unknown)}")
    for name in ("prompt", "diff_file", "output"):
        if used.count(name) != 1:
            problems.append(f"a tool reviewer's argv must carry {{{name}}} exactly once")
    environment = review.get("environment") or {}
    if not isinstance(environment, dict):
        return problems + ["`review.environment` must be an object"]
    for key in ("remove", "keep"):
        patterns = environment.get(key) or []
        if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
            problems.append(f"`review.environment.{key}` must be a list of regular expressions")
            continue
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(f"`review.environment.{key}` holds {pattern!r}, which does not compile: {exc}")
    pinned = environment.get("set") or {}
    if not isinstance(pinned, dict) or not all(isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                                               and isinstance(value, str) for name, value in pinned.items()):
        return problems + ["`review.environment.set` must map variable names to strings"]
    values = [name for value in pinned.values() for name in re.findall(r"\{([a-z_]+)\}", value)]
    unknown = sorted(set(values) - set(TOOL_REVIEW_ENV_PLACEHOLDERS))
    if unknown:
        problems.append(f"`review.environment.set` uses placeholders ao does not fill: {', '.join(unknown)}")
    if "model" not in used and "model" not in values:
        problems.append("the review contract never pins {model}, so ao could not record which model answered")
    return problems


def tool_review_contract(adapter):
    """An adapter's review contract when it is a sound tool reviewer's, else None (#86)."""
    review = (adapter or {}).get("review")
    return review if isinstance(review, dict) and not tool_review_problems(adapter) else None


def validate_adapter(adapter):
    """What an adapter is missing or gets wrong, before anyone relies on it (#77)."""
    problems = []
    if not isinstance(adapter, dict):
        return ["an adapter is a JSON object"]
    for field in ("id", "name", "verified"):
        if not isinstance(adapter.get(field), str) or not adapter[field].strip():
            problems.append(f"`{field}` is missing")
    if adapter.get("verified") and adapter["verified"] not in ADAPTER_VERIFIED:
        problems.append(f"`verified` is {adapter['verified']!r}; one of {', '.join(ADAPTER_VERIFIED)}")
    if "contract" not in adapter:
        problems.append(f"`contract` is missing; this ao implements contract {ADAPTER_CONTRACT}")
    elif adapter["contract"] != ADAPTER_CONTRACT:
        problems.append(f"declares contract {adapter['contract']!r}; this ao implements contract {ADAPTER_CONTRACT}")
    for capability in ("send", "resume"):
        block = adapter.get(capability)
        if block is None:
            if capability == "send":
                problems.append("`send` is missing: an adapter must say how to run one prompt")
            continue
        argv = (block or {}).get("argv") if isinstance(block, dict) else None
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            problems.append(f"`{capability}.argv` must be a list of strings")
            continue
        if sum("{prompt}" in a for a in argv) != 1:
            problems.append(f"`{capability}.argv` must carry {{prompt}} in exactly one argument")
        if capability == "resume" and not any("{session}" in a for a in argv):
            problems.append("`resume.argv` must carry {session}")
        unknown = sorted({p for a in argv for p in re.findall(r"\{([a-z_]+)\}", a)} - set(ADAPTER_PLACEHOLDERS))
        if unknown:
            problems.append(f"`{capability}.argv` uses placeholders ao does not fill: {', '.join(unknown)}")
    return problems + tool_review_problems(adapter)


def conform_adapter(adapter, harness, workdir):
    """Run an adapter's send and resume through a fixture harness: does each prompt and session arrive whole? (#77)

    Returns [(capability, "pass" | "fail" | "absent", detail)] for the five capabilities.
    """
    results = []
    prompt = "conformance prompt: spaces, 'quotes', \"double\", = signs and a\nsecond line"
    for capability in ("send", "resume"):
        argv = ((adapter.get(capability) or {}).get("argv") if isinstance(adapter.get(capability), dict) else None)
        if not argv:
            results.append((capability, "absent", "not declared"))
            continue
        rendered = [harness] + [a.replace("{prompt}", prompt).replace("{session}", "sess-conformance")
                                for a in argv[1:]]
        record = os.path.join(workdir, f"{capability}.json")
        try:
            subprocess.run(rendered, cwd=workdir, env=dict(os.environ, AO_HARNESS_RECORD=record), timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with open(record, encoding=UTF8) as fh:
                received = json.load(fh)["argv"]
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            results.append((capability, "fail", f"the harness did not run: {exc}"))
            continue
        whole = [a for a in received if a == prompt or a.endswith("=" + prompt)]
        if len(whole) != 1:
            results.append((capability, "fail", "the prompt did not arrive whole in exactly one argument"))
        elif capability == "resume" and any("{session}" in a for a in argv) \
                and not any("sess-conformance" in a for a in received):
            results.append((capability, "fail", "the session did not arrive"))
        elif capability == "resume" and not any("{session}" in a for a in argv):
            results.append((capability, "pass", "prompt whole; it resumes the most recent session, no id"))
        else:
            results.append((capability, "pass", f"{len(received)} argument(s), prompt whole"))
    for capability in ("transcript", "busy", "directives"):
        block = adapter.get(capability)
        if not block:
            results.append((capability, "absent", "not declared"))
        elif capability == "transcript" and block.get("kind") not in ("jsonl", "sqlite", "json", "markdown",
                                                                        "call-return", "unknown", None):
            results.append((capability, "fail", f"unknown transcript kind {block.get('kind')!r}"))
        else:
            results.append((capability, "pass", "declared"))
    return results
