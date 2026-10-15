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


def pending_roles(root, cfg):
    """(the roles `roles_next` assigns that still wait for their slice to leave running, that slice), or ({}, None).

    Until the slice leaves, every reader sees each role as it was, and a role nobody held
    reads as unconfigured: rehearsing the catch-up planned for 2026-10-01, a reviewer named
    while a slice ran left catch-up saying that no reviewer was configured (OCT1-FIXES).
    """
    pending = cfg.get("roles_next")
    if not isinstance(pending, dict) or not isinstance(pending.get("roles"), dict) or not root:
        return {}, None
    if pending.get("after") not in {item["id"] for item in board(root)["running"]}:
        return {}, None
    return dict(pending["roles"]), pending.get("after")


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
    roles stand, the reviewer is never the implementer's actor. Whether it may review
    the implementer's work at all - another family, another model of the same family
    where a person opted in, or neither - is its review tier, which `ao role set` asks
    with the rule `ao review` applies (REVIEW-TIERS).
    """
    implementer, reviewer = roles.get("implementer"), roles.get("reviewer")
    if repository != "tool" and not hotfix and implementer and implementer == roles.get("architect"):
        return (f"on a product repository the architect does not implement ({implementer} holds both); roles rotate "
                "on a tool repository (repository.kind tool), or name a hotfix with --hotfix")
    if implementer and reviewer and implementer == reviewer:
        return f"the reviewer and the implementer would both be {implementer}; no actor reviews its own work"
    return None


def role_of(name, cfg):
    """The role a mail name stands for, including the reviewer actor (TRIAGE-3-LOW)."""
    implementer, _ = mail_names(cfg)
    lowered = str(name or "").strip().lower()
    if lowered in {arch.lower() for arch in architect_mail_names(cfg)}:
        return "architect"
    if lowered in (implementer.lower(), "implementer"):
        return "implementer"
    if lowered in {name.lower() for name in reviewer_mail_names(cfg)}:
        return "reviewer"
    if lowered == "watchdog":
        return "watchdog"
    return "human" if lowered in ("human", "person", "owner") else None


def block_adapter(block):
    """The adapter a role block or reviewer route runs: the one it names, else the shipped one whose command it runs.

    None when it names none and its command is no shipped adapter's, or more than one's.
    """
    block = block if isinstance(block, dict) else {}
    named = str(block.get("adapter") or "").strip()
    if named:
        return named
    argv = block.get("argv") or []
    program = _program_name(argv[0]) if argv and isinstance(argv[0], str) else ""
    if not program:
        return None
    found = [ident for ident, adapter in package_adapters().items()
             if program in {_program_name(name) for name in
                            [ident, *adapter_binaries(adapter), *((adapter.get("detect") or {}).get("processes") or [])]
                            if name}]
    return found[0] if len(found) == 1 else None


def declared_family(block):
    """(family, None) as a role block or reviewer route declares it, lowercased, or (None, why none can be named).

    Its own `family`, or the family its shipped adapter declares. Two that disagree name
    no family: one of the statements is wrong and ao cannot tell which. A model name is
    never read as a family (#86).
    """
    block = block if isinstance(block, dict) else {}
    own = str(block.get("family") or "").strip().lower()
    ident = block_adapter(block)
    theirs = str((package_adapters().get(ident) or {}).get("family") or "").strip().lower() if ident else ""
    if own and theirs and own != theirs:
        return None, f"it declares the {own} family and its adapter {ident} the {theirs} family"
    if own or theirs:
        return own or theirs, None
    return None, "it declares no model family, and neither does its adapter"


def grant_author(cfg, matrix_identity=None):
    """Who lands a candidate under a review waiver, as far as ao can establish it: role, actor, adapter, family.

    The grant records it because the waived range is reviewed later against whoever wrote
    it, and the implementer configured by then need not be that actor, nor any (#65). A
    turn ao started names its role (AO_ROLE); that role's block names the actor, its
    adapter and the family declared for it. In a capability-matrix project the
    implementer's family is its binding's, and a family written inline names none. A
    caller ao did not start - a person, or a session someone opened - names no role ao
    can check, so every field stays None and the retrospective review waits for a person
    to name the family.
    """
    role = invoking_role()
    author = {"role": role, "actor": None, "adapter": None, "family": None}
    if role is None:
        return author
    block = cfg.get(role) if isinstance(cfg.get(role), dict) else {}
    implementer, architect = mail_names(cfg)
    author["actor"] = str(block.get("actor") or block.get("name")
                          or (implementer if role == "implementer" else architect))
    author["adapter"] = block_adapter(block)
    if matrix_identity is None:
        author["family"] = declared_family(block)[0]
    elif role == "implementer":
        author.update(adapter=matrix_identity.get("adapter") or author["adapter"],
                      family=str(matrix_identity.get("family") or "").strip().lower() or None)
    return author


def write_project_config(root, text):
    """Write `.ao/config.json` whole, within the reader's byte and depth limits (TRIAGE-3-LOW)."""
    from .storage import replace_file_durably
    raw = text.encode(UTF8)
    if len(raw) > PROJECT_CONFIG_MAX_BYTES:
        raise ValueError(f".ao/config.json exceeds the {PROJECT_CONFIG_MAX_BYTES:,}-byte limit; reduce it first")
    problem = _json_container_depth_problem(raw)
    if problem:
        raise ValueError(problem + "; reduce its nesting first")
    replace_file_durably(os.path.join(root, ".ao", "config.json"), raw)


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


def implementer_adapter_id(cfg=None):
    """The id of the implementer's adapter: the one its block names, else the default profile's implementer; "".

    The id is the configuration's, not a field of whichever layer's adapter answers to it:
    what an account reader asks the package for must not change with a copy of the adapter
    that declares no id (ACCOUNT-READERS).
    """
    return str(((cfg or {}).get("implementer") or {}).get("adapter")
               or (profiles().get(default_profile()) or {}).get("implementer") or "")


def implementer_adapter(cfg=None):
    """The implementer's adapter: the one its block names, else the default profile's implementer (#76).

    A transcript reader asked without an adapter reads what the implementer's adapter
    declares, never one harness's record shape written into the reader.
    """
    ident = implementer_adapter_id(cfg)
    return load_adapter(ident, (cfg or {}).get("root")) if ident else {}


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


def tool_beside_interpreter(name):
    """The command an optional extra installed beside the interpreter ao runs on, or None (#86).

    An extra lands in ao's own environment, whose scripts directory need not be on PATH: beside the
    interpreter in a virtual environment, and on Windows in the Scripts directory of an interpreter
    installed for everyone, which is not beside it (API-REVIEWER). A review and `ao doctor` look for it
    alike: the doctor looked beside the interpreter alone, and named a reviewer a review would find
    absent (API-REVIEWER-2).
    """
    import sys
    import sysconfig
    name = str(name)
    if not name or os.path.basename(name) != name:
        return None
    try:
        scripts = sysconfig.get_path("scripts")
    except (KeyError, TypeError, ValueError):
        scripts = None
    for here in dict.fromkeys(place for place in (os.path.dirname(sys.executable or ""), scripts) if place):
        for extension in ((".exe", "") if os.name == "nt" else ("",)):
            candidate = os.path.join(here, name + extension)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def absent_adapter_binaries(cfg):
    """(actor, adapter, binaries) for each configured actor whose adapter's command this machine lacks (#89).

    A command an optional extra installed beside the interpreter ao runs on is there too (#86).
    """
    def present(name):
        return bool(binary_candidates(name)) or tool_beside_interpreter(name) is not None

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
    # A harness no flag leaves reading runs as an agent of ao's own whose tools only read (AGY-REVIEWER).
    if options["trust_none"] == [] and options.get("reviewer_agent") is not None:
        problems = reviewer_agent_problems(adapter)
        return (False, "its reviewer agent is not sound: " + "; ".join(problems)) if problems else (True, None)
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

    Composed from the adapter the package ships alone, never a user's or a project's layer: `ao ci review` composes
    on a pull request's checkout, whose `.ao/adapters/` its author wrote, and a route that names an adapter runs that
    harness's program wherever it starts (ROUTE-ARGV). `root` only tells a layer's adapter from one nobody declares,
    so the refusal says which.
    """
    adapter = turn_adapter(adapter_id)
    if not adapter:
        if load_adapter(adapter_id, root):
            raise ValueError(f"{adapter_id} is no adapter ao ships, and a reviewer is composed from one the package "
                             "ships, never one a user's or a project's layer declares")
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


def reading_problems(argv, prompt=None):
    """What keeps a command from one that only reads, by the rule a reviewer's and the hunter's meet
    (BUG-HUNTER-2, REVIEWER-REACH-2).

    A reviewer is composed with the flags its adapter declares leave it only reading; a route written by
    hand, an older one, or an implementer's reassigned to review carries what it carries. `reviewer_problems`
    names a flag that widens; with none of these, `pi -p`, `omp -p` and `hermes -z` edit and run commands,
    `kilo run` edits, and qwen, qoder, reasonix and kiro run in whatever mode a person's settings or another
    flag names. So the command must run a harness ao ships, one able to review, with every flag its
    `options.trust_none` names. `prompt` is the text ao hands it in place of `{prompt}`.
    """
    adapter = command_adapter(argv)
    if not adapter:
        return ["ao ships no adapter for its command, so nothing declares how it runs without tools"]
    eligible, why = reviewer_eligibility(adapter)
    if not eligible:
        return [f"{adapter.get('id')} cannot review: {why}"]
    if tool_review_contract(adapter) is not None:
        return [f"{adapter.get('id')} is a tool reviewer, which reads a file ao writes for it, not a tree"]
    args = [str(arg) for arg in argv or []]
    # What follows `--` is no option, though a word there spelled like one was found (REVIEWER-REACH-3).
    args = args[:args.index("--")] if "--" in args else args
    declared = adapter["options"]["trust_none"]
    missing = [token for token in dict.fromkeys(declared) if token not in args]
    if missing:
        return [f"its command lacks {' '.join(missing)}, which {adapter.get('id')} declares leaves it only reading"]
    # A harness ao runs as a reviewer agent of its own takes no other: one a command names could hold any tool
    # (KIRO-READONLY).
    agent = adapter["options"].get("reviewer_agent")
    # The flag's name: agy's `--agent={agent}` names it with its value (AGY-REVIEWER).
    flag = str(agent["argv"][0]).split("=", 1)[0] if isinstance(agent, dict) and agent.get("argv") else None
    if flag and any(arg == flag or arg.startswith(flag + "=") for arg in args):
        return [f"it names {flag}, where ao runs {adapter.get('id')} as an agent of its own that only reads"]
    # A flag of them given again with another value is asked too, as a pinned flag is: a harness that takes
    # the last of two runs with it (`--trust-tools= --trust-tools=fs_write`, or `--approval-mode plan`
    # then `--approval-mode auto-edit`).
    found = []
    for flag, value in _flag_pairs(part for token in declared
                                   for part in (token.split("=", 1) if token.startswith("--") and "=" in token
                                                else [token])):
        if value is None:
            continue
        for at, arg in enumerate(args):
            if arg == flag:
                given = args[at + 1] if at + 1 < len(args) and not args[at + 1].startswith("-") else None
                after = at + 2
            elif arg.startswith(flag + "="):
                given, after = arg.split("=", 1)[1], at + 1
            else:
                continue
            # A flag that takes a list may take every word after it: `--tools Read,Grep,Glob Edit` reads Edit as a
            # tool, where only the first word was asked (REVIEWER-REACH-3), and a parser such as yargs reads
            # `--tools=Read,Grep,Glob Edit` so too (REVIEWER-REACH-4).
            # ao's own words - its prompt, a placeholder it fills - are no value smuggled after a flag, wherever a
            # route puts them: kiro takes its prompt as the word after its options.
            if given is not None and after < len(args) and not args[after].startswith("-") \
                    and not (args[after] == prompt or re.fullmatch(r"\{[a-z_]+\}", args[after])):
                found.append(f"it gives {flag} more than one value ({given or '(empty)'} {args[after]}), and "
                             f"{adapter.get('id')} may read them all")
                continue
            if given == value or (given is not None and "," in value
                                  and set(filter(None, given.split(","))) <= set(value.split(","))):
                continue
            found.append(f"it runs with {flag} {given if given is not None else '(no value)'}, where "
                         f"{adapter.get('id')} declares {flag} {value or '(empty)'} leaves it only reading")
    return found


# ---- a turn holds the grant ao composes, never a person's default or an unnamed bypass (GRANTS-PINNED) ----

# The flags a harness takes its allowlist after, as comma-separated rules in its own syntax (#58).
GRANT_FLAGS = ("--allowedTools", "--allowed-tools")
# An argv that carries one of these carries a tool scope of its own, and ao appends no grant to it (#58, #69).
SCOPE_FLAGS = GRANT_FLAGS + ("--trust-tools", "--trust-all-tools")
# Spellings with which a harness approves everything, sandboxes nothing or checks no permission: a yolo or a
# "dangerously" flag, a trust or an allow of every tool, a standing yes, a forced or automatic run, a mode of
# full access. Each one a command ao starts carries is declared, with why, in its adapter's `options.bypass`.
BYPASS_FLAG = re.compile(r"--?(yolo|dangerously(-[a-z]+)+|(trust|allow)-all(-[a-z]+)*|yes(-always)?|y|full-auto|"
                         r"skip-permissions(-[a-z]+)*|auto(-approve)?|force|f|no-sandbox|not-so-yolo|approve-for-me)",
                         re.I)
BYPASS_VALUE = re.compile(r"yolo|bypass[-_]?permissions|danger-full-access|full-access|always-proceed|unrestricted",
                          re.I)


def carries_scope(argv):
    """Whether an argv grants its tools through an allowlist or a trust flag of its own."""
    return any(str(arg) in SCOPE_FLAGS or str(arg).startswith(tuple(flag + "=" for flag in SCOPE_FLAGS))
               for arg in argv or [])


def _holds(argv, run):
    """Whether a run of arguments occurs in argv."""
    args, run = [str(arg) for arg in argv or []], [str(part) for part in run or []]
    return bool(run) and any(args[at:at + len(run)] == run for at in range(len(args) - len(run) + 1))


def unattended_flags(adapter, argv):
    """(flags, why) ao appends to an implementer turn nobody attends, started from `argv`; ([], None) for none (#69).

    An argv that carries a tool scope of its own is left as it is: a grant of every tool on
    top of an allowlist silently overrides the narrower grant. Otherwise the adapter's
    `options.unattended`, the narrower grant its documentation gives a turn nobody can
    approve, with what a command rewriter makes of each command it names (GRANTS-RTK), and
    where it declares none its `trust_all`.
    """
    options = (adapter or {}).get("options") or {}
    if carries_scope(argv):
        return [], None
    if isinstance(options.get("unattended"), list):
        flags, rewritten = admit_rewrites([str(flag) for flag in options["unattended"]])
        why = "the adapter declares the grant of a turn nobody attends (options.unattended)"
        if rewritten:
            why += ("; it also admits each command it names in the form a command rewriter runs it as "
                    "(adapters/rewriters)")
        return flags, (why if flags else None)
    if options.get("trust_all"):
        return ([str(flag) for flag in options["trust_all"]],
                "the adapter's resume argv carries no tool scope, so ao appends its trust_all")
    return [], None


def _flag_pairs(parts):
    """[(flag, value or None)] of an argv fragment: each flag, with the word after it when that is no flag."""
    parts = [str(part) for part in parts or []]
    return [(part, parts[at + 1] if at + 1 < len(parts) and not parts[at + 1].startswith("-") else None)
            for at, part in enumerate(parts) if part.startswith("-")]


def _named(argv, flag):
    """(named, value): whether an argv carries `flag` - alone, before its value, or as flag=value - and the value."""
    args = [str(arg) for arg in argv or []]
    for at, arg in enumerate(args):
        if arg == flag:
            return True, args[at + 1] if at + 1 < len(args) and not args[at + 1].startswith("-") else None
        if arg.startswith(flag + "="):
            return True, arg.split("=", 1)[1]
    return False, None


def command_adapter(argv):
    """The adapter ao ships whose command an argv runs, when exactly one runs it; else None.

    Read from the package's adapters alone: a layer an agent can write must not choose the
    arguments its own turn, or its reviewer's, starts with.
    """
    if not argv or not isinstance(argv[0], str):
        return None
    program = _program_name(argv[0])
    found = [adapter for ident, adapter in sorted(package_adapters().items())
             if program in {_program_name(name) for name in [ident, *adapter_binaries(adapter)] if name}]
    return found[0] if len(found) == 1 else None


def _config_part(part):
    """A path component as a file system that ignores case, a trailing dot or space, or an NTFS stream names it."""
    return part.split(":", 1)[0].rstrip(". ").casefold()


def project_config_names():
    """{(component, ...): the name as declared} of every file and folder an adapter ao ships says its harness reads from
    a project as configuration or instructions, in `project_config` (REVIEW-TREE-3).

    A harness started in a folder reads its hooks, plugins, MCP servers and instructions from there: Claude Code ran a
    SessionStart hook from the candidate's .claude/settings.json, agy its .agents/hooks.json, and OpenCode a plugin and
    an MCP server from its .opencode/ and opencode.json, each before or while it reviewed. Every adapter's names are
    withheld from every reviewer, since a harness reads others' too - OpenCode reads CLAUDE.md and .claude/skills. Read
    from the package's adapters alone, as `command_adapter` is: a layer an agent can write must not say what its
    reviewer is handed.
    """
    names = {}
    for adapter in package_adapters().values():
        # The instruction files it reads from a project, `directives.steering_files`, are among them; its own home's
        # are no tree's.
        steering = [name for name in (adapter.get("directives") or {}).get("steering_files") or []
                    if isinstance(name, str) and not name.startswith(("~", "/"))]
        for declared in list(adapter.get("project_config") or []) + steering:
            parts = tuple(_config_part(part) for part in str(declared).split("/") if part)
            if parts and all(parts):
                names.setdefault(parts, str(declared))
    return names


def project_config_member(path, names=None):
    """The `project_config` name a tree member's path holds or falls under, wherever in the tree it is; else None
    (REVIEW-TREE-3)."""
    names = project_config_names() if names is None else names
    parts = [_config_part(part) for part in str(path).replace("\\", "/").split("/") if part not in ("", ".")]
    for entry, declared in names.items():
        width = len(entry)
        if any(tuple(parts[at:at + width]) == entry for at in range(len(parts) - width + 1)):
            return declared
    return None


# REVIEW-TREE-3 keeps a candidate's hooks, plugins, MCP servers and instructions from its reviewer, but the same files
# act wherever else a harness starts in a tree an agent writes: a hook the implementer puts in .claude/settings.json
# runs in its own next session, outside the permissions that hold its commands, and in the architect's when ao wakes it
# in the same checkout - each before a review has seen it (#112). So every change to what an adapter declares its
# harness reads from a project is named, and nothing ao starts in the tree runs until a person has seen it
# (HARNESS-CONFIG).

# How each state project_config_changes gives a change reads to a person.
HARNESS_CONFIG_STATES = {"modified": "modified", "added": "added", "deleted": "deleted", "untracked": "untracked",
                         "ignored": "ignored by git", "hidden": "modified, hidden from git status",
                         "linked": "a tracked link, read through to what it leads to",
                         "committed": "committed with no grant",
                         "python": "run by Python as it starts there"}
# The changes ao reads before it stops and says it cannot tell what changed: a folder it walks itself - one git lists
# without descending, a repository of its own, a link's target - can hold any number of files.
HARNESS_CONFIG_LIMIT = 5000
# What ao keeps of a file to show a person; a larger one is named by its size alone.
HARNESS_CONFIG_SHOW_BYTES = 1 << 20
# What a terminal acts on rather than shows: control characters, a carriage return among them, and the zero-width and
# bidirectional ones that make text read otherwise than it runs. Shown escaped, so a change cannot hide in them. A line
# feed and the line and paragraph separators are among them: a name or a session's id holding one started a line of
# its own, which read as one ao wrote (LEADS-MCP). So is a tab: in a title it moved what followed it to another
# column, where a person read it as another field.
_UNSHOWN = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")


def shown_text(text):
    """Text as a person must read it to see what it does: each character of _UNSHOWN escaped, and a name that is not
    UTF-8 shown with replacement characters rather than failing the line it is in (HARNESS-CONFIG). It is one line:
    text of several is shown a line at a time (LEADS-MCP)."""
    return _UNSHOWN.sub(lambda m: "\\x%02x" % ord(m.group()) if ord(m.group()) < 0x100 else "\\u%04x" % ord(m.group()),
                        str(text).encode(UTF8, "surrogatepass").decode(UTF8, "replace"))


def _harness_read(full):
    """(sha256, view) of what a harness reads at `full`, through a link where one stands (HARNESS-CONFIG).

    Only a regular file is read. A FIFO or a device there - or a link to one, /dev/zero - would hold the reader for as
    long as its writer liked, so it is opened without waiting and named for what it is. The view is ("file", its bytes
    or None past HARNESS_CONFIG_SHOW_BYTES, its size), ("special", what it is) or ("missing",) for a link to nothing.
    """
    import stat
    try:
        fd = os.open(full, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        return hashlib.sha256(b"missing").hexdigest(), ("missing",)
    except OSError as exc:
        raise RuntimeError(f"{full} cannot be read ({exc.strerror or exc})") from None
    try:
        mode = os.fstat(fd).st_mode
    except OSError as exc:
        os.close(fd)
        raise RuntimeError(f"{full} cannot be read ({exc.strerror or exc})") from None
    if not stat.S_ISREG(mode):
        os.close(fd)
        kind = ("a folder" if stat.S_ISDIR(mode) else "a FIFO" if stat.S_ISFIFO(mode)
                else "a socket" if stat.S_ISSOCK(mode) else "a device")
        return hashlib.sha256(("special:" + kind).encode(UTF8)).hexdigest(), ("special", kind)
    digest, kept, size = hashlib.sha256(), [], 0
    with os.fdopen(fd, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
            size += len(chunk)
            if size <= HARNESS_CONFIG_SHOW_BYTES:
                kept.append(chunk)
    return digest.hexdigest(), ("file", b"".join(kept) if size <= HARNESS_CONFIG_SHOW_BYTES else None, size)


def harness_baseline(root):
    """The HEAD this project's harness configuration was first read at - `ao init`'s, or the first reading's - kept in
    ~/.ao and never moved by ao again; None before one is recorded (HARNESS-CONFIG)."""
    try:
        with open(project_file(root, "harness-base"), encoding=UTF8) as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    return record.get("head") if isinstance(record, dict) and isinstance(record.get("head"), str) else None


def record_harness_baseline(root, why):
    """Keep HEAD as the commit this project's harness configuration is measured from when no grant or person says
    later, once: the first record stands, so code that runs `ao init` again moves nothing (HARNESS-CONFIG)."""
    from .storage import replace_file_durably
    path = project_file(root, "harness-base")
    head = _git_text(root, "rev-parse", "--verify", "-q", "HEAD")
    if os.path.exists(path) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    replace_file_durably(path, (json.dumps({"head": head, "at": int(time.time()), "why": why}) + "\n").encode(UTF8))
    return head


def _nearest_ancestor(root, marks):
    """Of `marks`, the commit nearest HEAD that HEAD descends from; None when none is."""
    best = None
    for mark in dict.fromkeys(mark for mark in marks if mark):
        try:
            _git_output(root, "merge-base", "--is-ancestor", mark, "HEAD")
            distance = int(_git_output(root, "rev-list", "--count", f"{mark}..HEAD").decode("ascii").strip())
        except (RuntimeError, ValueError, UnicodeError):
            continue
        if best is None or distance < best[0]:
            best = (distance, mark)
    return best[1] if best else None


def harness_config_base(root):
    """The commit harness configuration is measured from when HEAD's own is not to be trusted: the one below the
    oldest commit with no grant on HEAD's first-parent line, after the newest of the first grant's base, the HEAD a
    person last recorded as seen, and the HEAD the configuration was first read at. None when every commit since is
    granted, or when there is nothing to measure from.

    What differs from HEAD is what no commit holds yet. A change committed with no grant - `git commit --no-verify` -
    is in HEAD and differed from nothing, so a hook committed so ran in the next nudge; and in a project that had made
    no grant at all, or one where a granted commit came after it, it went unseen all the same (the reviews of
    HARNESS-CONFIG). Measured from below the oldest ungranted commit, it is a change until a person sees it."""
    trees, base, _ = _granted_trees(root)
    lower = _nearest_ancestor(root, [base, (harness_seen_record(root) or {}).get("head"), harness_baseline(root)])
    if lower is None:
        return None
    try:
        log = _git_output(root, "log", "--first-parent", "--format=%H %T", f"{lower}..HEAD", "--").decode("ascii")
    except (RuntimeError, UnicodeError):
        return None
    commits = [line.split() for line in log.splitlines() if line.strip()]
    ungranted = [index for index, (_, tree) in enumerate(commits) if tree not in trees]
    if not ungranted:
        return None
    oldest = ungranted[-1]
    return commits[oldest + 1][0] if oldest + 1 < len(commits) else lower


def harness_worktree_dirs():
    """The folders the shipped adapters' harnesses keep the worktrees they make in - `.claude/worktrees` - as tuples of
    path components read as a file system would: a worktree of this repository there is a tree of its own, which no
    harness started at the root reads as configuration (HARNESS-CONFIG). Anywhere else a worktree is read like any
    folder: one made at `.claude` itself is configuration through and through (the review of HARNESS-CONFIG)."""
    dirs = set()
    for adapter in package_adapters().values():
        for name in adapter.get("worktree_dirs") or []:
            if isinstance(name, str) and name.strip() and not name.startswith(("/", "~")) and "\\" not in name \
                    and ".." not in name.split("/"):
                dirs.add(tuple(_config_part(part) for part in name.split("/") if part not in ("", ".")))
    return dirs


# What `site` runs whenever Python starts: each .pth file of a site directory, whose lines that begin with `import` are
# run as code, and a module by each of these names wherever it lies on the import path (PYTHON-ENV).
PYTHON_STARTUP_MODULES = ("sitecustomize", "usercustomize")
# The variables ao reads that name a program it runs, the adapters it trusts as a person's, or a record it keeps
# outside the tree on purpose; one that names a path inside the tree hands it to whoever writes there (PYTHON-ENV).
AO_PATH_VARIABLES = ("AO_GIT", "AO_USER_ADAPTERS", "AO_SETTINGS", "AO_LEDGER_CHECKPOINTS", "AO_PROJECT_REGISTRY",
                     "AO_EVENTS")


def _children(folder):
    """The names in a folder, sorted; none where it cannot be listed (PYTHON-ENV)."""
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


def _python_startup_names(folder, site_dir):
    """The names in `folder` that Python runs as it starts: each .pth file where it is a site directory, and
    sitecustomize and usercustomize in any form the import system loads - a source, a sourceless .pyc, an extension, a
    package - with their compiled copies in __pycache__, which Python runs in a source's place while the stamp they
    carry matches it (PYTHON-ENV)."""
    names = _children(folder)
    found = [name for name in names if (site_dir and name.casefold().endswith(".pth"))
             or name.split(".", 1)[0].casefold() in PYTHON_STARTUP_MODULES]
    if "__pycache__" in names:
        found += ["__pycache__/" + name for name in _children(os.path.join(folder, "__pycache__"))
                  if name.split(".", 1)[0].casefold() in PYTHON_STARTUP_MODULES]
    return found


def _own_standard_library(prefix):
    """Why the environment at `prefix` holds a standard library of its own, or None: a conda environment, or a lib folder
    holding anything beside site-packages - a venv's comes from outside it, and holds nothing else there (PYTHON-ENV).

    Such an environment runs from itself, as it starts, whatever modules of its standard library it imports, in any
    form the import system loads: naming some of them let a package or a sourceless file stand in for the rest (the
    reviews of PYTHON-ENV). ao reads no installation file by file; one inside the tree is moved out."""
    if os.path.isdir(os.path.join(prefix, "conda-meta")):
        return "a conda environment"
    if _python_installation(prefix) and not os.path.isfile(os.path.join(prefix, "pyvenv.cfg")):
        return "a Python installation's own standard library"
    for lib in [name for name in _children(prefix) if name.casefold() in ("lib", "lib64")]:
        base = os.path.join(prefix, lib)
        folders = [base] + [os.path.join(base, name) for name in _children(base)
                            if name.casefold().startswith(("python", "pypy"))
                            and os.path.isdir(os.path.join(base, name))]
        for folder in folders:
            extra = [name for name in _children(folder) if name.casefold() not in ("site-packages", "__pycache__")
                     and not (folder == base and os.path.join(base, name) in folders)]
            if extra:
                return f"a standard library of its own ({os.path.basename(folder)}/{sorted(extra)[0]})"
    return None


def _site_directories(prefix):
    """The site directories of the Python environment at `prefix`: lib/pythonX.Y/site-packages and its kin on POSIX,
    Lib/site-packages on Windows, each by the name its folder has, whatever case a file system ignores (PYTHON-ENV)."""
    # PyPy keeps its site-packages at the environment's top on some platforms (the review of PYTHON-ENV).
    found = [os.path.join(prefix, name) for name in _children(prefix) if name.casefold() == "site-packages"]
    for lib in [name for name in _children(prefix) if name.casefold() in ("lib", "lib64")]:
        base = os.path.join(prefix, lib)
        for name in _children(base):
            if name.casefold() == "site-packages":
                found.append(os.path.join(base, name))
            else:
                inner = os.path.join(base, name)
                found += [os.path.join(inner, sub) for sub in _children(inner) if sub.casefold() == "site-packages"]
    return [path for path in found if os.path.isdir(path)]


def _in_tree(path, top):
    """Whether `path`, its links followed, is the folder `top` or lies inside it (PYTHON-ENV). The root itself counts:
    `PYTHONPATH=.` or an environment made at the root was read as outside the tree (the review of PYTHON-ENV). Named
    apart from lib_transcript's `_inside`, a part loaded later into the same namespace, whose stricter reading - links
    not followed, the folder itself outside - had replaced this one's."""
    real, top = (os.path.normcase(os.path.realpath(each)) for each in (path, top))
    return real == top or real.startswith(top.rstrip(os.sep) + os.sep)


# Where a standard library of a Python's own sits: lib/pythonX.Y/os.py or lib/pypyX.Y/os.py; Windows' Lib/os.py is one
# only beside a python.exe, since a project's own lib/os.py is a module of the project's.
_STDLIB_SIGNATURE = re.compile(r"^(?:(.*)/)?(?:[Ll]ib(?:64)?/(?:[Pp]ython|[Pp]y[Pp]y)[^/]*|Lib)/os\.py$")


def _python_installation(prefix):
    """Whether a folder holds a Python installation of its own, by its standard library's signature (PYTHON-ENV)."""
    names = _children(prefix)
    if "Lib" in names and os.path.isfile(os.path.join(prefix, "Lib", "os.py")) \
            and any(name.casefold() in ("python.exe", "pythonw.exe") for name in names):
        return True
    for lib in [name for name in names if name.casefold() in ("lib", "lib64")]:
        base = os.path.join(prefix, lib)
        if any(os.path.isfile(os.path.join(base, name, "os.py")) for name in _children(base)
               if name.casefold().startswith(("python", "pypy"))):
            return True
    return False


# The names a Python's own standard library folder has, probed one by one so an ignored folder is never listed.
_STDLIB_FOLDERS = tuple(f"{name}3.{minor}" for name in ("python", "pypy") for minor in range(30)) + ("python2.7",)


def _python_installation_probe(prefix):
    """_python_installation by asking for each path it would find, never listing `prefix`: what git did not descend
    into - build/, node_modules/ - is not read through (PYTHON-ENV)."""
    if os.path.isfile(os.path.join(prefix, "Lib", "os.py")) and any(
            os.path.isfile(os.path.join(prefix, name)) for name in ("python.exe", "pythonw.exe")):
        return True
    return any(os.path.isfile(os.path.join(prefix, lib, name, "os.py"))
               for lib in ("lib", "lib64") for name in _STDLIB_FOLDERS)


def _venv_home(root):
    """The `home` a venv's pyvenv.cfg names for its Python, or None (PYTHON-ENV)."""
    try:
        with open(os.path.join(root, "pyvenv.cfg"), encoding=UTF8, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().casefold() == "home":
            return value.strip() or None
    return None


def python_startup_files(top, listed=(), folders_out=None):
    """The paths, relative to the tree at `top`, of each file Python runs as it starts from inside the tree
    (PYTHON-ENV).

    A .venv in the repository is ignored, so an implementer can put a .pth file or a sitecustomize.py into its
    site-packages, and every ao that environment runs - the commit hook through the ao on the committer's PATH, the
    watchdog's job, a person's own command - runs it first, before any review (#113). What is read: each environment
    git names in the tree, by the pyvenv.cfg a venv, virtualenv or uv writes or the conda-meta/ conda keeps, with the
    folders `listed`, git status's names, holding one; the environment and the site directories of the interpreter
    running this ao, where they lie inside the tree; and every other folder of this interpreter's import path inside
    it, where only a sitecustomize or usercustomize runs. Nothing else of an environment is read.
    """
    import site
    roots = [sys.prefix]
    # An environment the repository tracks is found by its marker too: git status names only what changed, and a .pth
    # added to a tracked environment changed nothing of its pyvenv.cfg (the review of PYTHON-ENV).
    try:
        tracked = _git_output(top, "ls-files", "-z", "--", "*pyvenv.cfg", "*conda-meta/*", "*os.py").split(b"\0")
    except RuntimeError:
        tracked = []
    for item in tracked:
        rel = os.fsdecode(item)
        if rel.split("/")[-1] == "os.py":
            continue                            # read by its signature below
        if rel.split("/")[-1] == "pyvenv.cfg":
            roots.append(os.path.join(top, *rel.split("/")[:-1]))
        elif "/conda-meta/" in "/" + rel:
            parts = rel.split("/")
            roots.append(os.path.join(top, *parts[:parts.index("conda-meta")]))
    # A Python installation with no marker at all - no pyvenv.cfg, no conda-meta - is found by its standard library's
    # signature: among what git status names, file by file or as a folder it did not descend into, asked for without
    # listing it, and among what the repository tracks (the review of PYTHON-ENV). What lies deeper in an ignored
    # folder is not searched: a person who runs a program an agent left in the tree runs the agent's code.
    for rel in list(listed) + [os.fsdecode(item) for item in tracked if item]:
        if "node_modules" in rel.split("/"):
            continue
        found = _STDLIB_SIGNATURE.match(rel.rstrip("/"))
        prefix = os.path.join(top, *(found.group(1) or ".").split("/")) if found else None
        if prefix and (rel.rstrip("/").split("/")[-2:-1] != ["Lib"] or _python_installation(prefix)):
            roots.append(prefix)
        elif rel.endswith("/"):
            folder = os.path.join(top, *rel.rstrip("/").split("/"))
            if _python_installation_probe(folder):
                roots.append(folder)
    for rel in listed:
        folder = os.path.join(top, *rel.rstrip("/").split("/"))
        if rel.endswith("/") and (os.path.isfile(os.path.join(folder, "pyvenv.cfg"))
                                  or os.path.isdir(os.path.join(folder, "conda-meta"))):
            roots.append(folder)
        elif rel.split("/")[-1] == "pyvenv.cfg":
            roots.append(os.path.dirname(folder))
        elif "/conda-meta/" in "/" + rel:
            # An untracked conda environment is listed file by file, conda-meta/history among them (the review of
            # PYTHON-ENV).
            parts = rel.split("/")
            roots.append(os.path.join(top, *parts[:parts.index("conda-meta")]))
    for root in roots:
        why = _own_standard_library(root) if _in_tree(root, top) else None
        home = _venv_home(root) if _in_tree(root, top) else None
        if home and _in_tree(home, top):
            # pyvenv.cfg names where its Python and that Python's standard library are: a home in the tree is one an
            # agent writes (the review of PYTHON-ENV).
            why = f"its pyvenv.cfg makes {os.path.relpath(os.path.realpath(home), os.path.realpath(top))}, inside the " \
                  "tree, its Python's home"
        if why:
            raise RuntimeError(f"{os.path.relpath(os.path.realpath(root), os.path.realpath(top)) or '.'} is a Python "
                               f"installation inside the tree - {why} - which an agent can write and which runs from "
                               "itself as it starts: move it outside the tree (PYTHON-ENV)")
    sites = [path for root in roots if _in_tree(root, top) for path in _site_directories(root)]
    sites += list(site.getsitepackages()) if hasattr(site, "getsitepackages") else []
    if site.ENABLE_USER_SITE and hasattr(site, "getusersitepackages"):
        sites.append(site.getusersitepackages())
    # One folder each, by what it is: lib64 is often a link to lib, and a file system that ignores case reaches one
    # folder by two spellings.
    folders = {}
    for path, site_dir in [(path, True) for path in sites] + [(path, False) for path in sys.path if path]:
        if not (os.path.isdir(path) and _in_tree(path, top)):
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        # A file system that numbers no folder, as FAT does, is told them apart by name.
        key = (stat.st_dev, stat.st_ino) if stat.st_ino else os.path.normcase(os.path.realpath(path))
        held = folders.setdefault(key, [os.path.realpath(path), site_dir])
        held[1] = held[1] or site_dir
    real_top = os.path.realpath(top)

    def tree_rel(path):
        # The tree's root is "", never "./": git names a file at the root `pyvenv.cfg` (the review of PYTHON-ENV).
        rel = os.path.relpath(os.path.realpath(path), real_top).replace(os.sep, "/")
        return "" if rel == "." else rel

    def joined(folder, name):
        return f"{folder}/{name}" if folder else name

    if folders_out is not None:
        folders_out.update({tree_rel(folder): site_dir for folder, site_dir in folders.values()})
    found = {joined(tree_rel(folder), name)
             for folder, site_dir in folders.values() for name in _python_startup_names(folder, site_dir)}
    # A venv's pyvenv.cfg says where its Python is, and so what that Python runs: configuration as much (PYTHON-ENV).
    found |= {joined(tree_rel(root), "pyvenv.cfg") for root in dict.fromkeys(roots)
              if _in_tree(root, top) and os.path.isfile(os.path.join(root, "pyvenv.cfg"))}
    return found


def _names_python_startup(rel):
    """Whether a path's own name says Python runs it as it starts, wherever it stands and whether it is there or not:
    a pyvenv.cfg, a .pth file in a site-packages folder, a sitecustomize or usercustomize in any form. A whole
    environment deleted takes its folders with it, and what it held back went unnamed (the review of PYTHON-ENV)."""
    parts = rel.split("/")
    name = parts[-1].casefold()
    if name == "pyvenv.cfg" or (name.endswith(".pth") and len(parts) > 1 and parts[-2].casefold() == "site-packages"):
        return True
    return any(part.split(".", 1)[0].casefold() in PYTHON_STARTUP_MODULES for part in parts[-3:])


def environment_in_tree(root):
    """[(what, path)] for each thing this ao runs from, loads or trusts that lies inside the tree an agent writes: the
    Python environment it runs from, its own code, each PYTHONPATH folder, and each path an AO_ variable names
    (PYTHON-ENV).

    A .pth file or a module in any of them has run before ao can name it, so naming is all ao can do for the run that
    finds it: the place is the person's to move outside the tree. Read from this process's own environment, which is
    the person's shell for `ao doctor` and the job's for the watchdog.
    """
    try:
        top = os.fsdecode(_git_output(root, "rev-parse", "--show-toplevel")).strip()
    except RuntimeError:
        top = root
    found = []
    if _in_tree(sys.prefix, top):
        found.append(("the Python environment ao runs from", sys.prefix))
    if _in_tree(_HERE, top):
        found.append(("ao's own code", _HERE))
    for entry in (os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        if entry and _in_tree(entry, top):
            found.append(("PYTHONPATH", entry))
    for name in AO_PATH_VARIABLES:
        value = os.environ.get(name)
        if value and _in_tree(value, top):
            found.append((name, value))
    return found


def project_config_changes(root, names=None, shown=None):
    """[(path, state, sha256 or None)], sorted by path: each path in the working tree that a harness reads from a
    project as its configuration or instructions - `project_config_member` names it - and that differs from HEAD
    (HARNESS-CONFIG).

    The state is one of HARNESS_CONFIG_STATES, the digest is of the bytes a harness reads there, through a link where
    one stands, and None for a path deleted. Staged or not, git status names each change, an untracked file one by
    one, and an ignored one too: a harness reads a gitignored .claude/settings.local.json all the same. An ignored
    folder git names without descending is read here only when it is configuration itself, or the start of a declared
    name such as `.github` of `.github/hooks`, so a node_modules/ is never walked. A folder that holds a repository of
    its own is read like any other - a plugin cloned into .opencode/plugin/ is configuration - unless it is a worktree
    of this repository, a tree of its own that is checked where something runs. A tracked file git is told to skip
    (`update-index --assume-unchanged` or `--skip-worktree`) is left out of git status, so it is compared with HEAD's
    blob by content; one a sparse checkout leaves out of the tree is no change. What Python runs as it starts from
    inside the tree - a .pth file, a sitecustomize or usercustomize - is configuration in the same way, under the state
    "python" (python_startup_files, PYTHON-ENV).

    `shown`, when given, is filled with {path: view} of the bytes each digest was taken of (_harness_read), so what a
    person is shown is what they record. Raises RuntimeError when git or a file cannot be read, and past
    HARNESS_CONFIG_LIMIT changes.
    """
    names = project_config_names() if names is None else names
    firsts = {entry[0] for entry in names}
    worktree_dirs = harness_worktree_dirs()
    top = os.fsdecode(_git_output(root, "rev-parse", "--show-toplevel")).strip()
    changes, visited, worktrees, startup, startup_folders = {}, set(), [], set(), {}

    def parts_of(rel):
        return [_config_part(part) for part in rel.split("/") if part not in ("", ".")]

    def member(rel):
        return (bool(firsts.intersection(parts_of(rel))) and project_config_member(rel, names) is not None) \
            or starts_python(rel)

    def starts_python(rel):
        # What Python runs as it starts from inside the tree is configuration too, a sitecustomize package's own
        # files among it (PYTHON-ENV); and a name of that kind in a folder Python starts from, there or not - a
        # deleted sitecustomize took what it held back with it (the review of PYTHON-ENV).
        if rel in startup or any(rel.startswith(path + "/") for path in startup) or _names_python_startup(rel):
            return True
        folder, _, name = rel.rpartition("/")
        cached = folder.rpartition("/")[2] == "__pycache__"
        site_dir = startup_folders.get(folder.rpartition("/")[0] if cached else folder)
        if site_dir is None:
            return False
        return name.split(".", 1)[0].casefold() in PYTHON_STARTUP_MODULES \
            or (site_dir and not cached and name.casefold().endswith(".pth"))

    def leads_to(rel):
        # A folder no name is declared for that a longer declared name starts with: `.github` of `.github/hooks`.
        parts = parts_of(rel)
        return any(len(parts) >= width and tuple(parts[-width:]) == entry[:width]
                   for entry in names for width in range(1, len(entry)))

    def full_path(rel):
        return os.path.join(top, *rel.split("/"))

    def record(rel, state, sha, view):
        if rel in changes:
            return
        if len(changes) >= HARNESS_CONFIG_LIMIT:
            raise RuntimeError(f"more than {HARNESS_CONFIG_LIMIT} changes to what a harness reads here, more than ao "
                               f"reads to tell what changed; {rel} is past them")
        changes[rel] = (state, sha)
        if shown is not None:
            shown[rel] = view

    def own_worktree(full):
        if not worktrees:
            worktrees.append({os.path.normcase(os.path.realpath(tree["path"])) for tree in worktree_list(top)})
        return os.path.normcase(os.path.realpath(full)) in worktrees[0]

    def take(rel, state):
        full = full_path(rel)
        if os.path.islink(full):
            target = os.readlink(full)
            link = b"link\0" + os.fsencode(target)
            if os.path.isdir(full):
                record(rel, state, hashlib.sha256(link).hexdigest(), ("link", target, None))
                walk(rel, state, everything=True)
                return
            inner, view = _harness_read(full)
            record(rel, state, hashlib.sha256(link + b"\0" + inner.encode(UTF8)).hexdigest(), ("link", target, view))
        elif os.path.isdir(full):
            walk(rel, state, everything=True)          # a submodule, or a folder git named
        elif not os.path.lexists(full):
            record(rel, "deleted", None, ("deleted",))
        else:
            record(rel, state, *_harness_read(full))

    def walk(rel, state, everything):
        full = full_path(rel)
        real = os.path.normcase(os.path.realpath(full))
        if real in visited:
            return
        visited.add(real)
        if os.path.lexists(os.path.join(full, ".git")) and tuple(parts_of(rel)[:-1]) in worktree_dirs \
                and own_worktree(full):
            return
        try:
            entries = sorted(os.listdir(full))
        except OSError as exc:
            raise RuntimeError(f"{rel}/ cannot be read ({exc.strerror or exc})") from None
        for name in entries:
            sub = f"{rel}/{name}"
            if name == ".git":
                continue
            if os.path.isdir(os.path.join(full, name)) and not os.path.islink(os.path.join(full, name)):
                if _config_part(name) == "node_modules":
                    continue
                if everything or member(sub):
                    walk(sub, state, everything=True)
                elif leads_to(sub):
                    walk(sub, state, everything=False)
            elif everything or member(sub):
                take(sub, state)

    listed = {}
    for item in _git_output(top, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching",
                            "--no-renames").split(b"\0"):
        if len(item) > 3:
            listed.setdefault(os.fsdecode(item[3:]), []).append(item[:2].decode("ascii", "replace"))
    startup.update(python_startup_files(top, listed, startup_folders))
    for rel, codes in sorted(listed.items()):
        tracked = [code for code in codes if code not in ("??", "!!")]
        state = ("ignored" if "!!" in codes else "untracked" if not tracked
                 else "added" if any(code[0] == "A" for code in tracked) else "modified")
        if rel.endswith("/"):
            # A folder git does not descend into: ignored as a whole, or a repository of its own.
            folder = rel.rstrip("/")
            if member(folder):
                walk(folder, state, everything=True)
            elif leads_to(folder):
                walk(folder, state, everything=False)
        elif member(rel):
            take(rel, "python" if starts_python(rel) else state)
    # What Python runs as it starts, in a folder git names without descending into it - an ignored environment's
    # site-packages - is read there; one git tracks unchanged is HEAD's, and no change (PYTHON-ENV).
    folders = [rel for rel in listed if rel.endswith("/")]
    for rel in sorted(startup):
        if rel not in changes and any(rel.startswith(folder) for folder in folders):
            take(rel, "python")
    # What was committed since the last granted tree is no review's either: it is read as it stands now.
    since = harness_config_base(top)
    if since:
        for item in _git_output(top, "diff", "--name-only", "-z", "--no-renames", since, "HEAD", "--").split(b"\0"):
            rel = os.fsdecode(item)
            if item and rel not in changes and member(rel):
                # what Python runs as it starts says so, as it does uncommitted (the review of PYTHON-ENV)
                take(rel, "python" if starts_python(rel) else "committed")
    # A link git tracks unchanged is read through as well: what it leads to can change while the link does not, and git
    # names that change at the target's own path, which may be no configuration at all (the review of HARNESS-CONFIG).
    for item in _git_output(top, "ls-files", "-s", "-z").split(b"\0"):
        meta, _, path = item.partition(b"\t")
        rel = os.fsdecode(path)
        if meta.startswith(b"120000 ") and rel not in changes and member(rel):
            take(rel, "linked")
    _hidden_config_changes(top, member, take, record, changes)
    return sorted(((rel, state, sha) for rel, (state, sha) in changes.items()), key=lambda change: change[0])


def _hidden_config_changes(top, member, take, record, changes):
    """Add every tracked file a harness reads whose content is not HEAD's, whatever git status said (HARNESS-CONFIG).

    git status trusts its stat cache: a file rewritten at its old size with its modification time put back - where the
    change time is not checked, as on Windows - reads as unchanged (the review of HARNESS-CONFIG); and one marked
    assume-unchanged (`h`, any lower-case tag of `git ls-files -v`) or skip-worktree (`S`) is left out of it
    altogether. Each is hashed as git would store it and compared with HEAD's blob."""
    hidden = []
    for item in _git_output(top, "ls-files", "-v", "-z").split(b"\0"):
        tag, rel = item[:1].decode("ascii", "replace"), os.fsdecode(item[2:])
        if len(item) > 2 and rel not in changes and member(rel):
            hidden.append((rel, tag))
    if not hidden:
        return
    heads = {}
    try:
        listed = _git_output(top, "--literal-pathspecs", "ls-tree", "-r", "-z", "HEAD", "--",
                             *[rel for rel, _ in hidden])
    except RuntimeError:
        listed = b""                    # no commit yet: each of them is new to HEAD
    for item in listed.split(b"\0"):
        meta, _, path = item.partition(b"\t")
        fields = meta.split()
        if len(fields) == 3:
            heads[os.fsdecode(path)] = (fields[0].decode("ascii"), fields[2].decode("ascii"))
    full = {rel: os.path.join(top, *rel.split("/")) for rel, _ in hidden}
    files = [rel for rel, _ in hidden if os.path.isfile(full[rel]) and not os.path.islink(full[rel])]
    # Hashed as git would store them, filters and line endings applied, so a checkout's CRLF is no change.
    work = dict(zip(files, _git_output(top, "hash-object", "--", *files).decode("ascii").split())) if files else {}
    sparse = None
    for rel, tag in hidden:
        head = heads.get(rel)
        if not os.path.lexists(full[rel]):
            if head is None:
                continue
            if tag in "Ss":
                # A sparse checkout keeps out of the tree what lies outside its cone, and marks it skip-worktree.
                if sparse is None:
                    sparse = _git_text(top, "config", "--bool", "core.sparseCheckout") == "true"
                if sparse:
                    continue
            record(rel, "deleted", None, ("deleted",))
        elif os.path.islink(full[rel]):
            if head is None or head[0] != "120000" \
                    or _git_output(top, "cat-file", "blob", head[1]) != os.fsencode(os.readlink(full[rel])):
                take(rel, "hidden" if tag == "S" or tag.islower() else "modified")
        elif head is None or work.get(rel) != head[1]:
            take(rel, "hidden" if tag == "S" or tag.islower() else "modified")


def harness_config_digest(changes):
    """The digest of a list of changes: each path and the digest of what stands there, never its state, so a change
    staged after it was seen is the change that was seen (HARNESS-CONFIG)."""
    digest = hashlib.sha256()
    _digest_field(digest, b"format", b"ao-harness-config-v1")
    for path, _, sha in sorted(changes, key=lambda change: change[0]):
        _digest_field(digest, b"path", path.encode(UTF8, "surrogatepass"))
        _digest_field(digest, b"content", (sha or "deleted").encode("ascii"))
    return "sha256:" + digest.hexdigest()


def harness_seen_record(root):
    """What a person last recorded as seen of this project's harness configuration, or None (HARNESS-CONFIG)."""
    try:
        with open(project_file(root, "harness-seen"), encoding=UTF8) as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def harness_unseen(changes, record):
    """The changes a person's record does not hold as they stand: each is seen by its path and its content, so a seen
    change that lands leaves the others seen (HARNESS-CONFIG). What ao wrote itself is seen as it wrote it
    (HARNESS-SEEN-OWN)."""
    record = record or {}
    seen = {(item.get("path"), item.get("sha256")) for item in list(record.get("changes") or [])
            + list(record.get("written") or []) if isinstance(item, dict)}
    return [change for change in changes if (change[0], change[2]) not in seen]


def record_harness_written(root, files, by):
    """Record as seen each file ao wrote into what a harness reads whose bytes are ao's own rendering and nothing else;
    the paths recorded, relative to the repository (HARNESS-SEEN-OWN).

    `files` is [(path, the bytes ao's write leaves there)]. After HARNESS-CONFIG the playbook, the coordination steering
    and the MCP entry `ao init` and `ao skill install` write held every turn until a person ran `ao harness seen`,
    though no agent wrote a byte of them. A file is recorded only while it holds exactly what ao renders - a fresh
    write, or one an earlier run left as it was - and by the digest of that rendering, never of what is read back: a
    person's rule file ao adds its pointer to holds other text, and one an agent changed before or after the write
    reads as other bytes, so neither is seen. Kept beside what a person recorded, in the same file in ~/.ao, and only
    for an ao project: a folder with no .ao/ shares a key with whatever project bears its name. What cannot be read or
    written is not recorded, and the files read as unseen, which holds until a person sees them.
    """
    from .storage import _exclusive_lock, replace_file_durably
    if not os.path.isdir(os.path.join(root, ".ao")):
        return []
    try:
        top = os.path.realpath(os.fsdecode(_git_output(root, "rev-parse", "--show-toplevel")).strip())
    except RuntimeError:
        return []
    names, now, ours = project_config_names(), int(time.time()), {}
    for path, data in files:
        rel = os.path.relpath(os.path.realpath(path), top).replace(os.sep, "/")
        if rel.startswith("../") or rel == ".." or os.path.islink(path) or project_config_member(rel, names) is None:
            continue
        expected = hashlib.sha256(data).hexdigest()
        try:
            sha, view = _harness_read(path)
        except RuntimeError:
            continue
        if view[0] == "file" and sha == expected:
            ours[rel] = {"path": rel, "sha256": expected, "by": by, "at": now}
    if not ours:
        return []
    target = project_file(root, "harness-seen")
    try:
        # Init and a person's acceptance shared an unlocked read-modify-write and lost each other's entries
        # (LEADS-OPEN). Only the record merge needs the lock, not reading the project's files.
        with _exclusive_lock(project_file(root, "harness-seen-lock")):
            record = harness_seen_record(root) or {}
            record["written"] = [item for item in record.get("written") or []
                                 if isinstance(item, dict) and item.get("path") not in ours] + list(ours.values())
            os.makedirs(os.path.dirname(target), exist_ok=True)
            replace_file_durably(target, (json.dumps(record, indent=1, sort_keys=True) + "\n").encode(UTF8))
    except OSError:
        return []
    return sorted(ours)


def unseen_harness_config(root):
    """The changes to what a harness reads from this tree that no person has seen as they stand; [] when nothing
    differs from HEAD (HARNESS-CONFIG), and each role's command is the one last accepted (TURN-ARGV). Raises
    RuntimeError when they cannot be read."""
    # The first reading keeps HEAD to measure from, where `ao init` left none: a project adopted before ao kept one.
    record_harness_baseline(root, "first reading")
    # And each role's command as it stands, where none is accepted yet (TURN-ARGV).
    record_turn_baseline(root, "first reading")
    changes = harness_config_changes(root)
    return harness_unseen(changes, harness_seen_record(root)) if changes else []


def record_harness_seen(root, changes, by, digest):
    """Record that a person has seen each of `changes` as it stands (HARNESS-CONFIG).

    Kept in this project's file in ~/.ao, outside the tree an agent writes, and written whole. `digest` is of what the
    person was shown; beside the name they gave, ao records what it can check, the login and whether a terminal was
    attached, as a waiver does.
    """
    from .storage import _exclusive_lock, replace_file_durably
    user, interactive = _login_and_terminal()
    # The HEAD it was seen at is measured from after it: what is committed later is a change again (HARNESS-CONFIG).
    head = _git_text(root, "rev-parse", "--verify", "-q", "HEAD")
    # Keep ao's own writes beside the person's snapshot, read under the same lock (LEADS-OPEN).
    with _exclusive_lock(project_file(root, "harness-seen-lock")):
        record = {"digest": digest, "by": by, "at": int(time.time()), "user": user, "interactive": interactive,
                  "head": head if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) else None,
                  "changes": [{"path": path, "state": state, "sha256": sha} for path, state, sha in changes],
                  # What ao wrote itself stays seen beside it (HARNESS-SEEN-OWN).
                  "written": list((harness_seen_record(root) or {}).get("written") or [])}
        path = project_file(root, "harness-seen")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        replace_file_durably(path, (json.dumps(record, indent=1, sort_keys=True) + "\n").encode(UTF8))
    # A role's command seen is the one accepted from now on, and a change from it is measured from there (TURN-ARGV).
    record_turns_seen(root, changes, by)
    return record


def harness_config_line(changes, limit=None):
    """`a (modified), b (untracked)`, and `and N more` past `limit`, for a line a person reads (HARNESS-CONFIG)."""
    named = [f"{shown_text(path)} ({HARNESS_CONFIG_STATES.get(state, state)})" for path, state, _ in changes]
    if limit is not None and len(named) > limit:
        return ", ".join(named[:limit]) + f" and {len(named) - limit} more"
    return ", ".join(named)


def role_pin(argv, role, adapter=None):
    """[(flag, value)] an adapter pins on every turn ao starts in `role` (`options.pin`); a hunter holds the reviewer's."""
    adapter = command_adapter(argv) if adapter is None else adapter
    declared = ((adapter or {}).get("options") or {}).get("pin")
    pin = declared.get(role) if isinstance(declared, dict) else None
    return _flag_pairs(pin) if isinstance(pin, list) else []


def pinned_argv(argv, role, adapter=None):
    """(argv, added): `argv` with each flag its adapter pins for `role` that it does not name, appended (GRANTS-PINNED).

    A harness that takes its permission mode from a person's settings when a command names
    none lets a default there widen every turn ao starts. A command composed before the pin,
    or written by hand, gets it when its turn starts. One that names the flag keeps its own
    value, which `pin_conflicts` reports; one that grants every tool is reported as that and
    left as it is.
    """
    argv = list(argv or [])
    adapter = command_adapter(argv) if adapter is None else adapter
    trust_all = ((adapter or {}).get("options") or {}).get("trust_all") or []
    if not adapter or _holds(argv, trust_all):
        return argv, []
    added = []
    for flag, value in role_pin(argv, role, adapter):
        if not _named(argv + added, flag)[0]:
            added += [flag] + ([value] if value is not None else [])
    return argv + added, added


def pin_conflicts(argv, role, adapter=None):
    """What an argv sets otherwise than its adapter pins for `role`, as phrases; empty when it holds the pin (GRANTS-PINNED).

    A comma-separated list that names no more than the pinned list is narrower, and no conflict.
    """
    out = []
    args = [str(arg) for arg in argv or []]
    for flag, value in role_pin(argv, role, adapter):
        if value is None:
            continue
        # Every occurrence is asked: a harness takes the last of two, or refuses them, and the first said nothing.
        for at, arg in enumerate(args):
            if arg == flag:
                given = args[at + 1] if at + 1 < len(args) and not args[at + 1].startswith("-") else None
            elif arg.startswith(flag + "="):
                given = arg.split("=", 1)[1]
            else:
                continue
            if given == value:
                continue
            if given is not None and "," in value and set(filter(None, given.split(","))) <= set(value.split(",")):
                continue
            out.append(f"it runs with {flag} {given if given is not None else '(no value)'}, where ao pins {value}")
    return out


def bypass_arguments(argv):
    """The arguments of an argv that approve everything, sandbox nothing or check no permission, as
    `options.bypass` names them: the argument itself, or a flag and the value after it."""
    args = [str(arg) for arg in argv or []]
    found = []
    for at, arg in enumerate(args):
        flag, equals, value = arg.partition("=")
        if not flag.startswith("-"):
            continue
        if BYPASS_FLAG.fullmatch(flag) or (equals and BYPASS_VALUE.fullmatch(value)):
            found.append(arg)
        elif not equals and at + 1 < len(args) and BYPASS_VALUE.fullmatch(args[at + 1]):
            found.append(f"{arg} {args[at + 1]}")
    return found


def role_commands(adapter):
    """{role: argv} for each role ao starts from an adapter's own declarations, as it starts them (GRANTS-PINNED).

    The implementer's nudge is its resume with what ao appends to a turn nobody attends; the
    architect's wake is its resume, whose tool grant ao replaces with the architect's own; a
    reviewer route is its send with trust_none, where the adapter may review. Each carries its pin,
    and the grant of a nudge or a wake admits what a command rewriter makes of each command it
    names, as the watchdog starts them (GRANTS-RTK).
    """
    adapter = adapter or {}
    options = adapter.get("options") or {}
    commands = {}
    resume = (adapter.get("resume") or {}).get("argv") if isinstance(adapter.get("resume"), dict) else None
    if isinstance(resume, list) and resume:
        own = admit_rewrites(resume)[0]
        commands["implementer"] = pinned_argv(own + unattended_flags(adapter, resume)[0], "implementer", adapter)[0]
        commands["architect"] = pinned_argv(own, "architect", adapter)[0]
    send = (adapter.get("send") or {}).get("argv") if isinstance(adapter.get("send"), dict) else None
    if isinstance(send, list) and send and reviewer_eligibility(adapter)[0]:
        denied = [] if tool_review_contract(adapter) is not None else list(options.get("trust_none") or [])
        commands["reviewer"] = pinned_argv(list(send) + denied, "reviewer", adapter)[0]
    return commands


def declared_bypass(adapter):
    """{argument: {roles, why, sandbox}} an adapter declares in `options.bypass`, the well-formed entries only."""
    declared = ((adapter or {}).get("options") or {}).get("bypass")
    return {str(argument): entry for argument, entry in declared.items() if isinstance(entry, dict)} \
        if isinstance(declared, dict) else {}


def bypass_problems(adapter):
    """Each argument that approves everything, sandboxes nothing or checks no permission in a command ao starts
    from an adapter with no reason in `options.bypass` for that role, and each reason no command needs (GRANTS-PINNED).

    A new flag of that kind is refused until someone writes down why it is there, and a reason
    outlives no flag.
    """
    options = (adapter or {}).get("options") or {}
    if options.get("bypass") is not None and not isinstance(options["bypass"], dict):
        return ["`options.bypass` must be an object: each argument, the roles whose command carries it, and why"]
    declared = declared_bypass(adapter)
    carried = {}
    for role, argv in role_commands(adapter).items():
        for argument in bypass_arguments(argv):
            carried.setdefault(argument, set()).add(role)

    def commands(roles):
        return f"the {' and the '.join(roles)} command" + ("s" if len(roles) > 1 else "")

    problems = []
    for argument, roles in sorted(carried.items()):
        entry = declared.get(argument) or {}
        unexplained = sorted(roles if not str(entry.get("why") or "").strip()
                             else roles - set(entry.get("roles") or []))
        if unexplained:
            problems.append(f"{commands(unexplained)} {'carry' if len(unexplained) > 1 else 'carries'} `{argument}`, "
                            "and `options.bypass` gives no reason for it there")
    for argument, entry in sorted(declared.items()):
        stale = sorted(set(entry.get("roles") or []) - carried.get(argument, set()))
        if stale:
            problems.append(f"`options.bypass` gives a reason for `{argument}` in {commands(stale)}, which "
                            f"{'do' if len(stale) > 1 else 'does'} not carry it")
    return problems


def sandbox_bypass(adapter, flags):
    """The arguments among `flags` that turn off the sandbox a harness runs in by default (`options.bypass` →
    `sandbox`), as a shipped adapter or this one declares them: a layer an agent can write cannot take one away."""
    sandboxed = {argument for source in [*package_adapters().values(), adapter]
                 for argument, entry in declared_bypass(source).items() if entry.get("sandbox")}
    return [argument for argument in bypass_arguments(flags) if argument in sandboxed]


def bypass_refusal(adapter, flags, cfg=None):
    """Why an unattended turn may not start with these flags, or None (GRANTS-PINNED).

    A flag that turns off the sandbox its harness runs in by default starts a turn only for
    an adapter a person named in `watchdog.bypass_adapters`. That is a machine setting: a
    project's own files are writable by the agents it governs.
    """
    ident = str((adapter or {}).get("id") or "")
    found = sandbox_bypass(adapter, flags)
    if not found or ident in settings.get(cfg, "watchdog.bypass_adapters"):
        return None
    return (f"its unattended turn would run with {found[0]}, which turns off {ident}'s sandbox and its approvals, "
            f"and no person has allowed that on this machine (ao config set watchdog.bypass_adapters {ident} "
            "--machine)")


# ---- the command a turn starts with is the package's, not the tree's (TURN-ARGV) ----------------

# The watchdog composed the implementer's nudge from its adapter as every layer loads it - the project's .ao/adapters/
# among them - and started the architect's wake and refill with the argv its block in .ao/config.json names: files an
# agent writes, so a change to either could start a command the agent chose, another program or a grant that admits a
# hook bypass (#116). A turn ao starts unattended is composed from the adapter the package ships and checked against
# it before it starts, and what its role's block says of it holds every turn, as harness configuration does, until a
# person has seen it as it stands.

# The roles whose turns the watchdog starts, and the turns each one's command starts.
TURN_ROLES = {"implementer": "a nudge", "architect": "a wake and a refill"}
# A review and a hunt start a command a project's configuration names too - the reviewer's routes, `hunter.argv` -
# in a file an agent writes, and each is held as a role's is (ROUTE-ARGV).
ROUTE_KINDS = {"reviewer": "a review", "hunter": "a hunt"}
# Every command ao starts whose change waits for a person, and what starts it.
HELD_COMMANDS = dict(TURN_ROLES, **ROUTE_KINDS)


def turn_adapter(ident):
    """The adapter a turn ao starts unattended is composed from and checked against: the one the package ships under
    `ident`, else None. Never a user's layer or a project's, which the agents they describe can write (TURN-ARGV)."""
    ident = str(ident or "").strip()
    adapter = package_adapters().get(ident) if ident else None
    return adapter if isinstance(adapter, dict) else None


def turn_path(role):
    """How a role's command is named among the changes a person reads, beside the files they name (TURN-ARGV)."""
    return f"the {role}'s command"


def turn_config(root, config=None):
    """The role blocks a turn's command is composed from, as load_config resolves them - the actor table, and the
    implementer a project that names none is found to run - from `config` or .ao/config.json, with no session read
    (TURN-ARGV)."""
    cfg = dict((project_config_document(root)["config"] if config is None else config) or {})
    resolve_roles(root, cfg)
    if "implementer" not in cfg:
        architect = cfg.get("architect") if isinstance(cfg.get("architect"), dict) else {}
        pinned = _concrete_session(architect.get("session"))
        found = discover_session(root, exclude={pinned} if pinned else ())
        if found:
            cfg["implementer"] = found
    return cfg


def turn_identity(cfg, role):
    """What a role's block says of the command a turn of it starts - its adapter, and the implementer's model and effort
    or the architect's argv and refill prompt - or None for a role ao starts no turn of. The rest of the command is the
    shipped adapter's, or ao's (TURN-ARGV)."""
    if role == "hunter":
        argv = settings.get(cfg, "hunter.argv")
        return {"argv": argv} if argv else None
    block = cfg.get(role) if isinstance(cfg.get(role), dict) else None
    if role == "reviewer":
        # Every route a review can start, the fallbacks among them, and the capability matrix a strict project's
        # reviewers come from (ROUTE-ARGV).
        routes = [_route_fields(route) for route in reviewer_routes(cfg) if route]
        matrix = cfg.get("capability_matrix")
        if not routes and matrix is None:
            return None
        identity = {"routes": routes, "capability_matrix": matrix}
        # And how a review reaches them, once that is not by spawning each: set to acp, a route answers through its
        # adapter's ACP command, which carries none of the route's own flags, and the setting sat in the file the
        # implementer writes, outside what a person accepted. Spawn, the default, adds nothing, so a reviewer accepted
        # before is still the one accepted (REVIEW-PATH-HIGHS).
        transport = settings.get(cfg, "review.transport")
        if transport != settings.default("review.transport"):
            identity["transport"] = transport
        return identity
    if not block:
        return None
    if role == "implementer":
        return {"adapter": block.get("adapter"), "model": block.get("model"), "effort": block.get("effort")}
    if not block.get("argv"):
        return None
    return {"adapter": block.get("adapter"), "argv": block.get("argv"), "prompt": block.get("prompt")}


def reviewer_routes(cfg):
    """The reviewer routes a project's configuration names, as dicts: its reviewer, then each of its fallbacks
    (ROUTE-ARGV)."""
    primary = cfg.get("reviewer") if isinstance(cfg.get("reviewer"), dict) else None
    if primary is None:
        return []
    fallbacks = primary.get("fallbacks") if isinstance(primary.get("fallbacks"), list) else []
    return [primary] + [route for route in fallbacks if isinstance(route, dict)]


def _route_fields(route):
    """What a reviewer route says of itself, every field but its fallbacks and the marks ao puts beside it
    (ROUTE-ARGV)."""
    return {key: value for key, value in route.items() if not str(key).startswith("_") and key != "fallbacks"}


def turn_digest(role, identity):
    """The digest a role's command is accepted by, of the role and what its block says; None where it says nothing."""
    if identity is None:
        return None
    return hashlib.sha256(json.dumps({"role": role, "turn": identity}, sort_keys=True).encode(UTF8)).hexdigest()


def turn_record(root):
    """{role: {sha256, identity, by, at}}: each role's command as last accepted - by `ao init`, at the first reading, or
    by a person who saw it - kept in ~/.ao, outside the tree an agent writes; {} before any is (TURN-ARGV)."""
    try:
        with open(project_file(root, "harness-turns"), encoding=UTF8) as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    return {role: entry for role, entry in record.items() if role in HELD_COMMANDS and isinstance(entry, dict)}


def _keep_turns(root, accepted, by):
    """Accept each role of `accepted`, {role: (digest, what its block says)}, by `by`, beside the roles accepted before;
    the roles kept, [] when they could not be written (TURN-ARGV)."""
    from .storage import replace_file_durably
    record, now = turn_record(root), int(time.time())
    for role, (digest, identity) in accepted.items():
        record[role] = {"sha256": digest, "identity": identity, "by": by, "at": now}
    target = project_file(root, "harness-turns")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        replace_file_durably(target, (json.dumps(record, indent=1, sort_keys=True) + "\n").encode(UTF8))
    except OSError:
        return []
    return [role for role in HELD_COMMANDS if role in accepted]


def _as_composed(cfg, roles):
    """{role: (digest, what its block says)} for each of `roles`, as `cfg` holds it (TURN-ARGV)."""
    return {role: (turn_digest(role, identity), identity) for role in roles for identity in [turn_identity(cfg, role)]}


def record_turn_baseline(root, why):
    """Accept each role's command as it stands where none is accepted for it yet - `ao init`'s, or the first reading's
    in a project adopted before ao kept one - and a role with no command as having none, so a block added later is a
    change. Only in an ao project, as what ao writes is (HARNESS-SEEN-OWN), and not while a .ao/config.json that is
    there cannot be read, as load_config reads one; the roles recorded (TURN-ARGV)."""
    if not os.path.isdir(os.path.join(root, ".ao")):
        return []
    missing = [role for role in HELD_COMMANDS if role not in turn_record(root)]
    document = project_config_document(root) if missing else None
    if not missing or (document["problem"] and os.path.lexists(os.path.join(root, ".ao", "config.json"))):
        return []
    return _keep_turns(root, _as_composed(turn_config(root, document["config"]), missing), why)


def record_turns_written(root, cfg, roles, by):
    """Accept the command of each of `roles` whose block ao composed itself - those `ao init` wrote - by the blocks it
    composed, `cfg`, never by what is read back, nor through an actor table it did not write: a block an agent changed
    before or after the write is another command, and stays unseen (HARNESS-SEEN-OWN, TURN-ARGV)."""
    roles = [role for role in roles if role in HELD_COMMANDS]
    if not roles or not os.path.isdir(os.path.join(root, ".ao")):
        return []
    return _keep_turns(root, _as_composed(cfg, roles), by)


def record_turns_seen(root, changes, by):
    """Accept each role's command among `changes` that a person was shown, by the digest they were shown, with what its
    block says while that is still what stands (TURN-ARGV)."""
    paths = {turn_path(role): role for role in HELD_COMMANDS}
    seen = {paths[path]: sha for path, _, sha in changes if path in paths}
    if not seen:
        return []
    standing = _as_composed(turn_config(root), seen)
    return _keep_turns(root, {role: (sha, standing[role][1] if standing[role][0] == sha else None)
                              for role, sha in seen.items()}, by)


def turn_command(cfg, role):
    """(adapter, argv) of the command a turn of `role` starts, composed as the watchdog composes it, with its prompt and
    session left as placeholders; argv is None where nothing ao ships composes one (TURN-ARGV). A review's is its
    reviewer route's and a hunt's the hunter's, each with the flags a reviewer is pinned to (ROUTE-ARGV)."""
    if role in ROUTE_KINDS:
        routes = reviewer_routes(cfg) if role == "reviewer" else [{"argv": settings.get(cfg, "hunter.argv")}]
        route = routes[0] if routes else {}
        argv = route.get("argv")
        if not isinstance(argv, list) or not argv:
            return (block_adapter(route) if route else None), None
        return block_adapter(route), pinned_argv([str(part) for part in argv], "reviewer")[0]
    block = cfg.get(role) if isinstance(cfg.get(role), dict) else {}
    if role != "implementer":
        argv = block.get("argv")
        if not isinstance(argv, list) or not argv:
            return block_adapter(block), None
        return block_adapter(block), pinned_argv(admit_rewrites([str(part) for part in argv])[0], "architect")[0]
    ident = str(block.get("adapter") or "").strip() or None
    shipped = turn_adapter(ident) or {}
    resume = (shipped.get("resume") or {}).get("argv") if isinstance(shipped.get("resume"), dict) else None
    if not isinstance(resume, list) or not resume:
        return ident, None
    argv = admit_rewrites([str(part) for part in resume])[0]
    argv = pinned_argv(argv + unattended_flags(shipped, argv)[0], "implementer")[0]
    options = shipped.get("options") or {}
    for key in ("model", "effort"):
        value = block.get(key)
        if value and isinstance(options.get(key), list) and not any(str(arg).startswith(f"--{key}") for arg in argv):
            if key == "effort" and options.get("effort_values") and value not in options["effort_values"]:
                continue
            argv += [str(part).replace("{" + key + "}", str(value)) for part in options[key]]
    return ident, argv


def turn_changes(root, shown=None):
    """[(path, "changed", digest)] for each role whose block says otherwise of its command than the one last accepted,
    the path `the architect's command`; with `shown`, what a person is shown of each, by its path (TURN-ARGV)."""
    cfg, record, out = turn_config(root), turn_record(root), []
    for role in HELD_COMMANDS:
        identity = turn_identity(cfg, role)
        accepted = record.get(role) or {}
        if identity is None or (role in record and accepted.get("sha256") == turn_digest(role, identity)):
            continue
        out.append((turn_path(role), "changed", turn_digest(role, identity)))
        if shown is not None:
            shown[turn_path(role)] = ("turn", role, identity, accepted.get("identity"), turn_command(cfg, role))
    return out


def harness_config_changes(root, shown=None):
    """Every change that holds what ao starts in this tree until a person has seen it: each to what a harness reads from
    the tree (project_config_changes), and each role's command its block changed (turn_changes) (TURN-ARGV)."""
    return project_config_changes(root, shown=shown) + turn_changes(root, shown=shown)


def unseen_turn(root, cfg, role):
    """The change no person has seen that a turn of `role` would start with, judged by `cfg`, the configuration its
    command was composed from: the file read again later can be put back between the two readings. None when its
    command is the one last accepted (TURN-ARGV)."""
    record_turn_baseline(root, "first reading")
    identity = turn_identity(cfg, role)
    if identity is None:
        return None
    digest = turn_digest(role, identity)
    if (turn_record(root).get(role) or {}).get("sha256") == digest:
        return None
    unseen = harness_unseen([(turn_path(role), "changed", digest)], harness_seen_record(root))
    return unseen[0] if unseen else None


def _same_program(path, other):
    """Whether two paths, followed through their links, are one file (TURN-ARGV)."""
    return os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(other))


def turn_refusal(role, ident, argv, root=None, resolved=None, search=None, values=None):
    """Why the watchdog refuses to start a turn of `role` with `argv`, as a person reads it; None when it may
    (TURN-ARGV).

    `argv` is the command as it would start, its program as the command names it, composed from adapter `ident`;
    `resolved` the file ao found for that program on `search`, once it has looked; `values` what ao fills into it from
    the role's block and its session, by name. The adapter is one the package ships; the program is its harness's,
    named alone or by where ao finds that harness, and never inside this repository; no value reads as a flag; and the
    grant admits nothing ao forbids the role that the command ao composes for it from the shipped adapter does not. No
    person's record lets a refused turn start: its configuration must change.
    """
    from . import allowlist as AL
    head = f"the watchdog refuses the {role}'s command:"
    shipped = turn_adapter(ident)
    program = str(argv[0]) if argv else ""
    if shipped is None:
        if ident:
            return (f"{head} its adapter {shown_text(ident)} is no adapter ao ships, and a turn ao starts unattended "
                    "is composed from one the package ships, never one a user's or a project's layer declares")
        return f"{head} no adapter ao ships runs `{shown_text(program)}`" if program else f"{head} it names no adapter"
    binaries = _harness_programs(shipped)
    listed = ", ".join(binaries)
    if _program_name(program) not in {_program_name(name) for name in binaries}:
        return f"{head} it runs `{shown_text(program)}`, which is not {ident}'s program ({listed})"
    if not os.path.isabs(program) and os.path.dirname(program):
        return (f"{head} it names its program by a path from where it starts, `{shown_text(program)}`, where an agent "
                "can leave a program of its own")
    for name, value in sorted((values or {}).items()):
        if isinstance(value, str) and value.startswith("-"):
            return f"{head} its {name} `{shown_text(value)}` would be read as a flag, not as a {name}"
    if resolved and root and _within(resolved, root):
        return f"{head} its program would be {shown_text(resolved)}, inside this repository, which an agent writes"
    if resolved and os.path.isabs(program) and not any(
            _same_program(resolved, found) for name in binaries for found in binary_candidates(name, search)):
        return (f"{head} it runs {shown_text(program)}, which is not where ao finds {ident}'s program ({listed}) on "
                "the path it searches")
    own = {(reason, command) for reason, command, _ in AL.problems(role_commands(shipped).get(role) or [], {}, role)}
    for reason, command, rule in AL.problems(list(argv), {}, role):
        if (reason, command) not in own:
            return (f"{head} its grant admits `{command}` ({reason}) through {shown_text(rule)}, which {ident}'s own "
                    f"{role} command does not")
    return None


def _harness_programs(adapter):
    """The names a shipped adapter's harness runs as: those it detects, and the programs its send and resume start
    (TURN-ARGV, ROUTE-ARGV)."""
    names = list(adapter_binaries(adapter))
    for capability in ("send", "resume"):
        argv = (adapter.get(capability) or {}).get("argv") if isinstance(adapter.get(capability), dict) else None
        if isinstance(argv, list) and argv:
            names.append(os.path.basename(str(argv[0])))
    return list(dict.fromkeys(name for name in names if name))


def route_refusal(kind, ident, argv, root=None, resolved=None, values=None):
    """Why ao refuses to start a review's or a hunt's command `argv`, as a person reads it; None when it may
    (ROUTE-ARGV).

    `kind` is reviewer or hunter, `ident` the adapter the route names, if any; `resolved` the file ao found for its
    program, once it has looked, and `values` what ao fills into the command from the configuration, by name. A route
    is held to what turn_refusal holds a turn to, as each rule applies to one a project names. The adapter it names
    and the harness it runs agree: a route that names an adapter ao ships runs that adapter's own program, and one
    that runs a shipped harness's program names that harness or none, since the adapter a route names says its family,
    its prompt channel and its ACP command. No program is named by a path from where it starts, or lies inside this
    repository, and no value reads as a flag. A route that runs a program no adapter ships is written by hand, the
    exception docs/adapters.md keeps, whatever label it gives itself - a capability matrix names its tools' adapters
    in its own words: it is not refused here, and starts only as a person accepted it (hold_unseen_routes). An
    absolute program is taken as written, as `_reviewer_resolve_binary` takes it: a person accepted the route that
    names it. Whether it can write is allowlist.reviewer_problems' and reading_problems' to say, where it starts. No
    person's record lets a refused route start: its configuration must change.
    """
    head = f"ao refuses the {kind}'s command:"
    program = str(argv[0]) if argv else ""
    if not program:
        return f"{head} it names no program"
    named = str(ident or "").strip()
    shipped = turn_adapter(named) if named else None
    runs = command_adapter(argv)
    adapter = shipped or runs
    if named and shipped is None and runs is not None:
        return (f"{head} it runs `{shown_text(program)}`, {runs.get('id')}'s program, under adapter "
                f"{shown_text(named)}, which ao does not ship; name {runs.get('id')} or none")
    if adapter is not None:
        programs = _harness_programs(adapter)
        if _program_name(program) not in {_program_name(name) for name in programs}:
            return (f"{head} it runs `{shown_text(program)}`, which is not {adapter.get('id')}'s program "
                    f"({', '.join(programs)})")
    if not os.path.isabs(program) and os.path.dirname(program):
        return (f"{head} it names its program by a path from where it starts, `{shown_text(program)}`, where an agent "
                "can leave a program of its own")
    for name, value in sorted((values or {}).items()):
        if isinstance(value, str) and value.startswith("-"):
            return f"{head} its {name} `{shown_text(value)}` would be read as a flag, not as a {name}"
    if resolved and program_in_repository(resolved, root):
        return f"{head} its program would be {shown_text(resolved)}, inside this repository, which an agent writes"
    return None


def program_in_repository(path, root):
    """Whether a program lies inside the repository at `root`, where an agent can write it (ROUTE-ARGV).

    ao's own install - the folder of the interpreter it runs on, and that interpreter's scripts folder, where an
    optional extra such as the API reviewer lands - is not asked, wherever it lies: the threat model leaves ao's
    install to the person, and a virtual environment kept in a tree holds ao itself too.
    """
    import sysconfig
    if not path or not root or not _within(path, root):
        return False
    try:
        scripts = sysconfig.get_path("scripts")
    except (KeyError, TypeError, ValueError):
        scripts = None
    own = {os.path.normcase(os.path.realpath(place)) for place in (os.path.dirname(sys.executable or ""), scripts)
           if place}
    return os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(path)))) not in own


def outside_repository(path, root):
    """A search path less each folder inside the repository at `root`, for finding a program ao starts: resolving one
    asks each candidate its version, which runs it, and an agent can leave a program under any name in a folder a
    PATH entry leads to inside the tree (ROUTE-ARGV). A path left with no folder names none, never the process's own
    PATH."""
    kept = os.pathsep.join(entry for entry in str(path or "").split(os.pathsep)
                           if entry and not program_in_repository(os.path.join(entry, "program"), root))
    return kept or os.pathsep


def note_routes_read(cfg):
    """Keep in `cfg`, as load_config read it, the digest of each command a review or a hunt would start, and return
    it (ROUTE-ARGV). Computed from `cfg` alone, so loading a configuration writes nothing; hold_unseen_routes judges
    what it notes."""
    cfg.pop("_routes_judged", None)
    cfg["_routes_read"] = {kind: turn_digest(kind, turn_identity(cfg, kind)) for kind in ROUTE_KINDS}
    return cfg


def hold_unseen_routes(root, cfg):
    """Mark in `cfg` each command a review or a hunt would start, as the project's configuration was read, that no
    person has accepted, and return it (ROUTE-ARGV). A review, a probe and a hunt ask it where they start.

    `cfg["_unseen"]` names each such kind with its change, and each reviewer route of an unseen reviewer carries the
    change itself, as `_unseen`, wherever ao takes it: a route starts only unmarked. Only what note_routes_read noted
    is judged, and only while it is still what `cfg` holds: never the file read again, since `ao review` is the
    implementer's to run and a route it wrote could be put back between ao reading it and the reviewer starting, and
    never a route ao composes in memory - `ao ci review`'s, or a test's - which is no project's configuration. Judged
    once for a `cfg`, against the record of the project at `root`. What cannot be compared is held, as harness
    configuration that cannot be read is.
    """
    if cfg.get("_routes_judged"):
        return cfg
    read = cfg.get("_routes_read") if isinstance(cfg.get("_routes_read"), dict) else {}
    cfg.pop("_unseen", None)
    for route in reviewer_routes(cfg):
        route.pop("_unseen", None)
    unseen = {}
    for kind in ROUTE_KINDS:
        if kind not in read or read[kind] != turn_digest(kind, turn_identity(cfg, kind)):
            continue
        try:
            change = unseen_turn(root, cfg, kind)
        except Exception as exc:
            change = (turn_path(kind), f"it cannot be compared with the one accepted "
                                       f"({' '.join(str(exc).split())[:160]})", None)
        if change:
            unseen[kind] = change
    if unseen:
        cfg["_unseen"] = unseen
    if unseen.get("reviewer"):
        for route in reviewer_routes(cfg):
            route["_unseen"] = unseen["reviewer"]
    cfg["_routes_judged"] = True
    return cfg


def unseen_route_text(kind, change):
    """What a person reads of a route held because its change is unseen: what it is, and what lets it start
    (ROUTE-ARGV)."""
    return (f"{harness_config_line([change])}: {ROUTE_KINDS[kind]} starts no command a person has not accepted, and "
            f"no person has seen this one - a person reads it with ao harness seen --by <name>")


# ---- a grant admits what a command rewriter makes of the commands it names (GRANTS-RTK) ----------

# Where the package declares its command rewriters, one file each: beside the adapters, not among them.
REWRITERS_DIR = "rewriters"
# How a rule names a command with any arguments after it, in the syntaxes the shipped grants are written in.
RULE_WILDCARDS = (":*", " *")
# A form is its rewriter's command and at least one more word, each of letters, digits, `_` and `-` only: a
# wildcard would widen the rule the form becomes, a comma or a bracket would write another rule into the
# grant, and a path would name a program the agent may have written.
REWRITE_FORM = re.compile(r"[\w-]+(?: [\w-]+)+")


def command_rewriters():
    """The command rewriters the package declares in adapters/rewriters/, one JSON file each (GRANTS-RTK).

    A rewriter is a program a harness's hook hands an agent's shell commands to, and runs them
    rewritten: rtk turns `git diff` into `rtk git diff`. What it rewrites, and into what, is measured
    on one of its releases and written there rather than in any harness's adapter, since which
    harness runs the hook is the machine's business. Read from the package alone, as a review
    contract is: a layer an agent can write must not choose what its own grant admits. A file that
    cannot be read, or names no command, declares nothing: a grant then gains no rule for it, so
    what that rewriter rewrites stays blocked, as it was, rather than admitted wider. A file that is
    there and cannot be read is not the same as no file, though: the allowlist check reads the same
    declarations to know which grants admit a forbidden command in a rewriter's form, and a check
    that quietly lost them would pass a grant it exists to refuse. So a package file that does not
    parse raises, naming itself; only a package with no rewriters directory declares none.
    tests/test_grants_rtk.py reads the package's own files, so a broken one fails there first.
    """
    directory = os.path.join(adapters_dir(), REWRITERS_DIR)
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
    except OSError:
        return []
    found = []
    for name in names:
        try:
            with open(os.path.join(directory, name), encoding=UTF8) as fh:
                rewriter = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError(f"the package's command rewriter {name} cannot be read ({exc}); "
                             "reinstall ao, or restore that file from the release") from exc
        if isinstance(rewriter, dict) and isinstance(rewriter.get("command"), str) and rewriter["command"].strip():
            found.append(rewriter)
    return found


def rewriter_forms(rewriter):
    """{command: form} a rewriter declares, keeping each form that is its own command and plain words after it."""
    command = str(rewriter.get("command") or "").strip()
    forms = rewriter.get("forms") if isinstance(rewriter.get("forms"), dict) else {}
    return {str(words): form for words, form in forms.items()
            if isinstance(form, str) and REWRITE_FORM.fullmatch(form) and form.split(" ", 1)[0] == command}


def rewritten_rules(rule, rewriters=None):
    """The rules admitting what each rewriter makes of the commands `rule` admits; [] for most rules (GRANTS-RTK).

    Only a rule naming a command a rewriter declares, with any arguments after it - `Bash(git diff:*)`,
    or `Bash(git diff *)` as another harness spells it - has one: the same rule for the form every
    rewrite of that command begins with, `Bash(rtk git diff:*)`. An exact rule, a rule that names
    arguments of its own and every other tool have none: a rewriter need not keep a command's
    arguments in place (`head -n 5 x` becomes `rtk read x --head-lines 5`), so no narrower rule can be
    read off them, and none is guessed.
    """
    rule = str(rule).strip()
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return []
    body = rule[len("Bash("):-1]
    wildcard = next((end for end in RULE_WILDCARDS if body.endswith(end)), None)
    if wildcard is None:
        return []
    words = body[:-len(wildcard)]
    found = []
    for rewriter in command_rewriters() if rewriters is None else rewriters:
        form = rewriter_forms(rewriter).get(words)
        if form and f"Bash({form}{wildcard})" not in found:
            found.append(f"Bash({form}{wildcard})")
    return found


def admit_rewrites(argv, rewriters=None):
    """(argv, added): `argv` with each rule of its tool grant followed by those of its `rewritten_rules` the
    grant does not hold yet, and the rules added (GRANTS-RTK).

    A harness's hook rewrote `git diff` to `rtk git diff` before the harness checked its rules, and a
    grant naming `Bash(git diff:*)` does not admit `rtk git diff`: an unattended turn was denied the
    commands its grant named (measured on qodercli). A rule added follows the rule it comes from and
    admits that rule's commands only, as the rewriter runs them. The grant is the value after each of
    GRANT_FLAGS, as allowlist.rules reads it, and admitting twice adds nothing the second time.
    """
    rewriters = command_rewriters() if rewriters is None else rewriters
    args, added = list(argv or []), []

    def admitted(value):
        parts = value.split(",")
        held = {part.strip() for part in parts}
        out = []
        for part in parts:
            out.append(part)
            for rule in rewritten_rules(part, rewriters):
                if rule not in held:
                    held.add(rule)
                    out.append(rule)
                    added.append(rule)
        return ",".join(out)

    for at, arg in enumerate(args):
        flag, equals, value = str(arg).partition("=")
        if at and str(args[at - 1]) in GRANT_FLAGS:
            args[at] = admitted(str(arg))
        elif equals and flag in GRANT_FLAGS:
            args[at] = f"{flag}={admitted(value)}"
    return args, added


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
    if "release" in review and not re.fullmatch(r"\d+(?:\.\d+)*", str(review["release"])):
        problems.append("`review.release` names the release the contract was measured against, in dotted digits")
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


def acp_problems(adapter):
    """What is wrong with an adapter's ACP command, if it declares one (ACP-PROBE).

    `acp.argv` starts the harness speaking ACP on its standard streams: a list of strings with no
    placeholder, since the protocol carries the prompt and the session, and `acp.measured` names the
    release and the day it was seen to answer `initialize`.
    """
    block = adapter.get("acp")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["`acp` must be an object"]
    problems = []
    argv = block.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        problems.append("`acp.argv` must be a list of strings")
    elif any("{" in a for a in argv):
        problems.append("`acp.argv` carries no placeholder: the protocol carries the prompt and the session")
    if not isinstance(block.get("measured"), str) or not block["measured"].strip():
        problems.append("`acp.measured` must say which release answered, and when")
    return problems


def reviewer_agent_problems(adapter):
    """What keeps ao from writing the reviewer agent an adapter declares in `options.reviewer_agent` (KIRO-READONLY)."""
    agent = ((adapter or {}).get("options") or {}).get("reviewer_agent")
    if agent is None:
        return []
    if not isinstance(agent, dict):
        return ["`options.reviewer_agent` must be an object"]
    problems = []
    argv, path, config = agent.get("argv"), agent.get("path"), agent.get("config")
    if not (isinstance(argv, list) and argv and all(isinstance(part, str) for part in argv)
            and str(argv[0]).startswith("-") and sum("{agent}" in part for part in argv) == 1):
        problems.append("`options.reviewer_agent.argv` must be a flag, carrying {agent} in exactly one argument")
    if not (isinstance(path, str) and "{agent}" in path and not path.startswith("/") and "\\" not in path
            and ".." not in path.split("/")):
        problems.append("`options.reviewer_agent.path` must be a path within the reviewer's directory, carrying {agent}")
    # A harness names an agent's tools as a list, or as permissions by tool, as opencode does (OPENCODE-REVIEWER).
    if not (isinstance(config, dict) and (isinstance(config.get("tools"), list) or isinstance(config.get("permission"), dict))):
        problems.append("`options.reviewer_agent.config` must be an object that names its `tools` or its `permission`")
    # An agent file is JSON, or a markdown body under the config as front matter (AGY-REVIEWER).
    if agent.get("format", "json") not in ("json", "frontmatter"):
        problems.append("`options.reviewer_agent.format` must be json or frontmatter")
    if agent.get("format") == "frontmatter" and not isinstance(agent.get("body"), str):
        problems.append("`options.reviewer_agent.body` must be the text under the front matter")
    if agent.get("acp") not in (None, True, False):
        problems.append("`options.reviewer_agent.acp` must say whether the agent was measured to hold over ACP too")
    release = agent.get("release")
    if release is not None and not (isinstance(release, str) and re.fullmatch(r"\d+(\.\d+)*", release)):
        problems.append("`options.reviewer_agent.release` must be the release the agent was measured on, as 1.3")
    settings = agent.get("settings")
    if settings is not None and not (
            isinstance(settings, dict) and isinstance(settings.get("path"), str) and settings["path"].startswith("~/")
            and isinstance(settings.get("allow"), list) and settings["allow"]
            and all(isinstance(key, str) for key in settings["allow"])
            and isinstance(settings.get("reads"), list) and all(isinstance(rule, str) for rule in settings["reads"])):
        problems.append("`options.reviewer_agent.settings` must name a file under the home folder, the keys of its "
                        "allow rules, and the rules that only read")
    return problems


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
    config = adapter.get("project_config")
    if config is not None and not (isinstance(config, list) and all(
            isinstance(name, str) and name.strip() and not name.startswith("/") and "\\" not in name
            and ".." not in name.split("/") for name in config)):
        problems.append("`project_config` must list the files and folders its harness reads from a project, each a "
                        "path within it")
    worktrees = adapter.get("worktree_dirs")
    if worktrees is not None and not (isinstance(worktrees, list) and all(
            isinstance(name, str) and name.strip() and not name.startswith(("/", "~")) and "\\" not in name
            and ".." not in name.split("/") for name in worktrees)):
        problems.append("`worktree_dirs` must list the folders its harness keeps the worktrees it makes in, each a "
                        "path within the project")
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
    return (problems + prompt_channel_problems(adapter) + tool_review_problems(adapter) + subagent_problems(adapter)
            + acp_problems(adapter) + reviewer_agent_problems(adapter)
            + bypass_problems(adapter) + quota_stop_problems(adapter)
            + token_problems(_block(_block(adapter, "telemetry"), "cost")))


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
        elif capability == "transcript" and not session_store_reachable(adapter):
            results.append((capability, "declared", "no store ao can resolve, so nothing is read from it"))
        else:
            results.append((capability, "pass", "declared"))
    return results


def session_store_reachable(adapter):
    """Can ao reach this adapter's records at all, from what it declares?

    A record is read from a place ao resolves: a role's session id names it in one of the stores
    this ao dispatches on. An adapter may describe its transcript precisely - its shape, its kinds,
    where a tool call keeps its name - and still name no place ao can find it, and then every
    reader of it returns nothing while a check that only looked for a declaration said it was
    sound. So a declaration is reported as one, and a resolution as the other.
    """
    return isinstance((adapter or {}).get("sessions"), dict) \
        and (adapter["sessions"].get("kind") in SESSION_STORE_KINDS)


# ---- a prompt too long for one argument reaches its command another way (PROMPT-CHANNEL) -----

# Linux refuses one argument longer than 32 pages, its terminating NUL counted (MAX_ARG_STRLEN), however much
# the whole command line may hold; Windows refuses a command line longer than 32,767 UTF-16 units, its NUL
# counted. Linux and macOS both bound the arguments and the environment together (ARG_MAX).
LINUX_ARGUMENT_BYTES = 131_072
WINDOWS_COMMAND_UNITS = 32_767
# Kept free beside what is measured: the program's resolved path, a launcher that starts the real program
# with a path, a script and variables of its own, and what the kernel stores beside the strings.
ARGUMENT_MARGIN_BYTES = 65_536
ARGUMENT_MARGIN_UNITS = 1_024
PROMPT_CHANNELS = ("stdin", "file")


def argument_overflow(argv, env=None):
    """What starting `argv` here would exceed, as a phrase; None when it fits with room to spare (PROMPT-CHANNEL).

    A review prompt carries a diff of up to 400 KB and its context, and it travelled as one argument:
    past what one argument carries the reviewer could not start, and the spawn error read as a reviewer
    that was unavailable. Every string is counted with its NUL and its pointer, and the environment
    (`os.environ` unless one is given) with the arguments. Where the platform's own bound cannot be
    read, the Linux bound on one argument stands in for it.
    """
    import sys
    args = [str(arg) for arg in argv]
    if os.name == "nt":
        units = len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2 + 1
        if units + ARGUMENT_MARGIN_UNITS <= WINDOWS_COMMAND_UNITS:
            return None
        return (f"a command line on {sys.platform} carries at most {WINDOWS_COMMAND_UNITS - 1} characters; this one "
                f"takes {units - 1}, and {ARGUMENT_MARGIN_UNITS} are kept free")
    sizes = [len(os.fsencode(arg)) + 1 for arg in args]
    try:
        whole = int(os.sysconf("SC_ARG_MAX"))
    except (AttributeError, OSError, TypeError, ValueError):
        whole = 0
    if (sys.platform.startswith("linux") or whole <= 0) and max(sizes, default=0) > LINUX_ARGUMENT_BYTES:
        return (f"one argument on {sys.platform} carries at most {LINUX_ARGUMENT_BYTES - 1} bytes"
                + ("" if whole > 0 else ", as ao assumes where the platform's bound cannot be read"))
    if whole <= 0:
        return None
    environment = os.environ if env is None else env
    taken = sum(size + 8 for size in sizes) + sum(len(os.fsencode(key)) + len(os.fsencode(value)) + 2 + 8
                                                for key, value in environment.items())
    if taken + ARGUMENT_MARGIN_BYTES <= whole:
        return None
    return (f"arguments and environment on {sys.platform} carry at most {whole} bytes together; this command takes "
            f"{taken}, and {ARGUMENT_MARGIN_BYTES} are kept free")


def _argument_run(argv, run):
    """Where a run of arguments starts in argv when it occurs there exactly once, else None."""
    if not isinstance(argv, list) or not isinstance(run, list) or not run:
        return None
    found = [at for at in range(len(argv) - len(run) + 1) if argv[at:at + len(run)] == run]
    return found[0] if len(found) == 1 else None


def prompt_channel_problems(adapter):
    """What an adapter's prompt channels get wrong; empty when it declares none, or sound ones (PROMPT-CHANNEL).

    A channel is declared under `send` or `resume` and replaces, once, the run of that capability's own
    arguments that carries {prompt}: `stdin` with arguments that leave the prompt to standard input,
    `file` with arguments that name the prompt's file in {prompt_file}, once.
    """
    problems = []
    for capability in ("send", "resume"):
        block = (adapter or {}).get(capability)
        if not isinstance(block, dict):
            continue
        for channel in PROMPT_CHANNELS:
            if channel not in block:
                continue
            spec, where = block[channel], f"{capability}.{channel}"
            replaces = spec.get("replaces") if isinstance(spec, dict) else None
            replacement = spec.get("with") if isinstance(spec, dict) else None
            if not isinstance(replaces, list) or not all(isinstance(part, str) for part in replaces) \
                    or sum("{prompt}" in part for part in replaces) != 1:
                problems.append(f"`{where}.replaces` must list the arguments that carry {{prompt}}, one of them "
                                "holding it")
            elif _argument_run(block.get("argv"), replaces) is None:
                problems.append(f"`{where}.replaces` must occur exactly once in `{capability}.argv`")
            if not isinstance(replacement, list) or not all(isinstance(part, str) for part in replacement):
                problems.append(f"`{where}.with` must be a list of strings")
                continue
            used = [name for part in replacement for name in re.findall(r"\{([a-z_]+)\}", part)]
            if set(used) - {"prompt_file"}:
                problems.append(f"`{where}.with` fills no placeholder but {{prompt_file}}")
            if used.count("prompt_file") != (1 if channel == "file" else 0):
                problems.append(f"`{where}.with` must carry {{prompt_file}} "
                                + ("exactly once" if channel == "file" else "nowhere: the prompt is on standard input"))
    return problems


def batch_program(program):
    """Whether `program` is a batch file, which Windows runs through cmd.exe: `.cmd` or `.bat` (WINDOWS-CMD-CHANNEL).

    An agent CLI installed with npm on Windows is a `.cmd` file, and cmd.exe reads each argument it is
    handed as its own syntax: it ends the command at a line break, replaces `%NAME%`, and a double quote
    in the text changes what `&` and `|` mean. No quoting carries a line break through it, so such a
    program is handed its prompt on standard input, never in an argument (`prompt_input`). The suffix is
    read in any case, as Windows reads it; elsewhere nothing runs a program through cmd.exe.
    """
    return os.name == "nt" and str(program or "").lower().endswith((".cmd", ".bat"))


def _batch_stdin(template, ident, adapter, capability):
    """How a batch program is handed the prompt `template` carries: ((start, stop, arguments), None), the run
    of the template the adapter's standard input replaces and the arguments put in its place, or (None, why
    none can be) (WINDOWS-CMD-CHANNEL).

    Only standard input counts. A prompt file is handed over by its path, which is an argument cmd.exe
    reads like any other.
    """
    if not isinstance(adapter, dict):
        return None, ((f"{ident} is no adapter ao ships" if ident else "no adapter ao ships runs this command")
                      + ", and only a shipped adapter declares standard input")
    block = adapter.get(capability) if isinstance(adapter.get(capability), dict) else {}
    if not isinstance(block.get("stdin"), dict):
        return None, f"adapter {ident} declares no standard input for `{capability}`"
    replaces, replacement = block["stdin"].get("replaces"), block["stdin"].get("with")
    at = _argument_run(template, replaces)
    if at is None or not isinstance(replacement, list):
        named = " ".join(str(part) for part in replaces) if isinstance(replaces, list) else ""
        return None, (f"this command does not carry the arguments adapter {ident} replaces with standard input "
                      f"for `{capability}` ({named})")
    return (at, at + len(replaces), [str(part) for part in replacement]), None


def _batch_argv(plan, argv):
    """`argv` with its prompt moved to standard input, for a batch program: (argv, None), or (None, why it
    cannot be) (WINDOWS-CMD-CHANNEL).

    The command is rendered from the plan's template part for part, and what a caller adds follows it,
    so the arguments the adapter's standard input replaces stand where the template holds them. They are
    cut only where they carry the prompt: a command that does not carry it there is refused, since what a
    cut would take from it is not known.
    """
    switch, why = plan.get("batch") or (None, "no standard input was planned for it")
    if switch is None:
        return None, why
    start, stop, arguments = switch
    carried = [str(part).replace("{prompt}", plan["prompt"]) for part in plan["argv"][start:stop]]
    if plan.get("channel") != "argument" or list(argv[start:stop]) != carried:
        return None, "this command does not carry its prompt where its template does"
    return list(argv[:start]) + list(arguments) + list(argv[stop:]), None


def prompt_plan(template, prompt, adapter_id=None, detached=False, env=None):
    """How a prompt reaches the command an argv template runs: (plan, None), or (None, why it cannot) (PROMPT-CHANNEL).

    The prompt stays in its argument while the command fits (`argument_overflow`). Past that it goes where
    the adapter declares, in place of the arguments that carried it: `resume`'s channels for a command that
    carries {session}, `send`'s for any other, standard input before a file. A file is removed when its
    process ends and ao does not wait on a detached turn, so a detached turn takes standard input or
    nothing. Channels are read from the package's adapters only, as a review contract is: a layer an agent
    can write must not choose the arguments a reviewer runs with. With no channel nothing may start, and
    the reason says how large the prompt is, what the platform carries and what the adapter declares.
    The plan holds the argv template, the channel ("argument", "stdin" or "file"), the run (start, stop)
    of the arguments the channel put in, and the prompt. It also holds `batch`, how a batch program on
    Windows is handed the prompt whatever its size, as `_batch_stdin` reads it from the same adapter: the
    program is known only once the command is resolved, so `prompt_input` decides (WINDOWS-CMD-CHANNEL).
    """
    template = list(template or [])
    text = str(prompt)
    capability = "resume" if any("{session}" in str(part) for part in template) else "send"
    ident = str(adapter_id or "").strip()
    adapter = package_adapters().get(ident) if ident else None
    plan = {"argv": template, "channel": "argument", "run": None, "prompt": text,
            "batch": _batch_stdin(template, ident, adapter, capability)}
    overflow = argument_overflow([str(part).replace("{prompt}", text) for part in template], env)
    if overflow is None:
        return plan, None
    size = f"the prompt is {len(text.encode(UTF8, 'replace'))} bytes, and {overflow}"
    if not isinstance(adapter, dict):
        return None, (f"{size}; " + (f"{ident} is no adapter ao ships" if ident else "no adapter ao ships runs this "
                                     "command") + ", and only a shipped adapter declares another channel")
    block = adapter.get(capability) if isinstance(adapter.get(capability), dict) else {}
    declared = [channel for channel in PROMPT_CHANNELS if isinstance(block.get(channel), dict)]
    for channel in declared:
        replaces, replacement = block[channel].get("replaces"), block[channel].get("with")
        at = _argument_run(template, replaces)
        if (detached and channel == "file") or at is None or not isinstance(replacement, list):
            continue
        argv = template[:at] + [str(part) for part in replacement] + template[at + len(replaces):]
        return dict(plan, argv=argv, channel=channel, run=(at, at + len(replacement))), None
    if not declared:
        return None, f"{size}; adapter {ident} declares no other channel for `{capability}` (`stdin` or `file`)"
    if detached and declared == ["file"]:
        return None, (f"{size}; adapter {ident} declares only a prompt file for `{capability}`, which is removed when "
                      "its process ends, and ao does not wait on a detached turn")
    return None, (f"{size}; this command does not carry the arguments adapter {ident} replaces for `{capability}` ("
                  + "; ".join(" ".join(str(part) for part in block[channel].get("replaces") or [])
                              for channel in declared) + ")")


def prompt_input(plan, root=None, argv=None):
    """What hands a planned prompt to its process: (given, None), or (None, why it cannot) (PROMPT-CHANNEL).

    `argv` is the command rendered from the plan, the plan's template when none is given. For a channel
    the prompt's bytes are written to a file of mode 0600 in a directory of its own, never inside
    `root`: standard input is that file, opened for reading, or the file's path fills the {prompt_file}
    the channel put in, and nowhere else. A file read as standard input loses its name before the
    process starts where the platform allows it, so nothing named is left whatever becomes of ao;
    `release_prompt` removes the rest once the process has ended. `given` holds the argv, standard input
    (None when the prompt is not on it) and the directory.

    A batch program on Windows is handed its prompt on standard input whatever its size, where its adapter
    declares it, and nothing starts where it does not (WINDOWS-CMD-CHANNEL). The watchdog handed the
    implementer and the architect their prompts as an argument, and cmd.exe would have ended the
    architect's wake, several paragraphs long, at its first line. It is decided here, on the command that
    starts, because the watchdog resolves the program only after the prompt's plan is made.
    """
    given = {"argv": list(plan["argv"] if argv is None else argv), "stdin": None, "directory": None}
    channel = plan.get("channel")
    if channel != "stdin" and given["argv"] and batch_program(given["argv"][0]):
        switched, why = _batch_argv(plan, given["argv"])
        if switched is None:
            return None, (f"{given['argv'][0]} is a batch file, which Windows runs through cmd.exe, and cmd.exe "
                          "reads a prompt in an argument as its own syntax, ending it at a line break and "
                          f"replacing %NAME% in it, so it takes its prompt on standard input alone (#71); {why}")
        given["argv"], channel = switched, "stdin"
    if channel not in PROMPT_CHANNELS:
        return given, None
    import tempfile
    binary = getattr(os, "O_BINARY", 0)
    try:
        given["directory"] = tempfile.mkdtemp(prefix="ao-prompt-")
        if root and _within(given["directory"], root):
            release_prompt(given)
            return None, "the prompt's private directory would lie inside the repository"
        path = os.path.join(given["directory"], "prompt")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary, 0o600), "wb") as fh:
            fh.write(plan["prompt"].encode(UTF8))
        if channel == "stdin":
            # On Windows the file goes when the last handle on it closes, the process's included.
            given["stdin"] = os.fdopen(os.open(path, os.O_RDONLY | binary | getattr(os, "O_TEMPORARY", 0)), "rb")
            if os.name != "nt":
                os.remove(path)
                os.rmdir(given["directory"])
                given["directory"] = None
        else:
            start, stop = plan["run"]
            given["argv"][start:stop] = [str(part).replace("{prompt_file}", path) for part in given["argv"][start:stop]]
    except (OSError, UnicodeError) as exc:
        release_prompt(given)
        return None, f"the prompt could not be written for its process ({type(exc).__name__}: {exc})"
    return given, None


def release_prompt(given):
    """Close ao's handle on a handed prompt and remove its private directory; a second call does nothing."""
    import shutil
    handle, given["stdin"] = given.get("stdin"), None
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass
    directory, given["directory"] = given.get("directory"), None
    if directory:
        shutil.rmtree(directory, ignore_errors=True)
