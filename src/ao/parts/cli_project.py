"""The project: digest, notes, templates, init and profiles, decisions, the board, holds, fan-out,
e-mail, alarms, the playbook.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


def cmd_digest(cfg, args):
    """What happened in a window — read from the ledgers, never estimated.

    Event alerts answer "did something just occur". They cannot answer "is this
    week going well", and reconstructing that from thirty notifications is asking
    a human to do the tool's job.

    It also absorbs the question people actually ask most: *why is nothing
    moving*. That is not a separate command — it is the same facts, read at the
    top instead of the bottom.
    """
    root = cfg["root"]
    d = A.digest(root, cfg, args.days)

    def say(key, **values):
        # In the project's language; a count, a verdict and a command read the same in any (LANGUAGE-OUTPUT).
        return language.text(cfg, key, **values)

    days = int(args.days) if args.days == int(args.days) else args.days
    win = say("digest.day") if args.days == 1 else say("digest.days", n=days)

    # Lead with the blocking answer. Someone opening this at 3am wants "what is
    # in the way", not a scoreboard.
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    state, age, doing = A.busy(cfg, adapter) if impl else ("unknown", None, "")
    spin = A.spinning(root)
    print(f"{C['b']}{cfg.get('project') or os.path.basename(root)}{C['reset']}"
          f"{C['dim']}  {say('digest.window', window=win)}{C['reset']}\n")

    col = {"working": C["green"], "slowing": C["yellow"]}.get(state, C["red"])
    line = f"{col}{state}{C['reset']}"
    if age is not None:
        line += f"{C['dim']}{say('digest.last-write', minutes=age // 60)}{C['reset']}"
    if spin:
        line += f"  {C['red']}{say('digest.spinning', minutes=spin)}{C['reset']}"
    print(f"  {say('digest.state'):<9}{line}")
    if doing:
        print(f"  {C['dim']}↳ {doing[:110]}{C['reset']}")

    if d["decisions"]["open"]:
        print(f"\n  {C['yellow']}{say('digest.open-decisions', n=d['decisions']['open'])}{C['reset']}"
              f"{C['dim']} — {say('digest.unblocks')}{C['reset']}")
    for b in d["blocked"]:
        print(f"  {C['red']}⊘{C['reset']} {b['id']}  {C['dim']}{b['needs'][:88]}{C['reset']}")

    print(_digest_heading(say("digest.landed")))
    print(f"  {C['b']}{len(d['commits'])}{C['reset']} commit"
          f"{C['dim']}, {say('digest.unpushed', n=d['unpushed'])}{C['reset']}")
    for c in d["commits"][:args.n]:
        print(f"    {C['dim']}{c['sha']}{C['reset']} {c['subject'][:76]}")

    v, a, r = d["verifications"], d["authority"], d["reviews"]
    print(_digest_heading(say("digest.gates")))
    vf = f", {C['red']}{say('digest.failed', n=v['failed'])}{C['reset']}" if v["failed"] else ""
    rc = f", {C['yellow']}{say('digest.changes', n=r['changes'])}{C['reset']}" if r["changes"] else ""
    ar = f", {C['yellow']}{say('digest.refused', n=a['refused'])}{C['reset']}" if a["refused"] else ""
    print(f"  {say('digest.verify'):<11}{C['green']}{say('digest.passed', n=v['passed'])}{C['reset']}{vf}")
    print(f"  review     {C['green']}{r['approved']} APPROVED{C['reset']}{rc}")
    if a.get("integrity") == "broken":
        print(f"  commit-ok  {C['red']}{say('digest.ledger-broken')}{C['reset']}")
        if a.get("error"):
            print(f"    {C['dim']}{a['error'][:110]}{C['reset']}")
    else:
        print(f"  commit-ok  {C['green']}{say('digest.granted', n=a['granted'])}{C['reset']}{ar}")
    # A refusal repeated all week is a process problem, not an incident.
    for reason, n in d["refusal_reasons"]:
        if n > 1:
            print(f"    {C['dim']}{n}× {reason}{C['reset']}")

    dec = d["decisions"]
    if dec["asked"]:
        med = say("digest.minutes", n=dec["median_minutes"]) if dec["median_minutes"] is not None else "—"
        answered = say("digest.answered", answered=dec["answered"], asked=dec["asked"])
        print(f"\n  {say('digest.decisions'):<11}{answered}"
              f"{C['dim']}, {say('digest.median', median=med)}{C['reset']}")

    account = d.get("account") or {}
    if d.get("credits"):
        c = d["credits"]
        pct = c["used"] / c["limit"] * 100 if c["limit"] else 0
        cc = C["red"] if pct > 90 else C["yellow"] if pct > 70 else C["green"]
        print(_digest_heading(say("digest.credit")))
        left = say("digest.left", n=f"{c['remaining']:,.0f}")
        print(f"  {cc}{c['used']:,.0f}{C['reset']} / {c['limit']:,.0f}"
              f"{C['dim']}  ({left}){C['reset']}")
    elif account and not account.get("readable"):
        # Where the figure stood, the implementer's adapter declares no account (ACCOUNT-READERS).
        print(_digest_heading(say("digest.credit")))
        print(f"  {C['dim']}{_no_account(account.get('adapter'))}{C['reset']}")
    for day, val in d["credit_days"][-args.n:]:
        print(f"    {C['dim']}{day}{C['reset']}  {val:>8,.0f}")

    al = d["alerts"]
    print(f"\n{C['dim']}{say('digest.board')} " +
          " · ".join(f"{k} {n}" for k, n in d["board"].items()) +
          f"  |  {say('digest.alerts', sent=al['sent'], held=al['held'])}{C['reset']}")
    return 0


def _digest_heading(label):
    """A digest section's heading, ruled to the same width whatever the length of its label in a language."""
    return f"\n{C['b']}{C['mag']}── {label} {'─' * (53 - len(label))}{C['reset']}"


def cmd_note(cfg, args):
    """Write an architect message into the mailbox — through the tool, on purpose.

    A woken architect should need no raw Write or Edit to do its job. The one
    time it had them, it used them on the orchestrator's own source and built a
    runaway. This is the only door to the mailbox an unattended architect gets.
    """
    # Read stdin only when asked. Defaulting to it made `ao note "title"` hang
    # waiting on a terminal that would never close, which is the wrong failure
    # for a command an unattended architect calls.
    body = args.body if args.body else (sys.stdin.read() if args.stdin else "")
    if not args.title or not body.strip():
        print(f"usage: {C['b']}ao note \"title\" --body \"…\" [--to implementer] [--urgent]{C['reset']}")
        print(f"       {C['dim']}or pipe the body on stdin{C['reset']}")
        return 1
    name = A.note(cfg["root"], cfg, args.to, args.title, body, urgent=args.urgent)
    print(f"{C['green']}written{C['reset']} {cfg['mailbox']}/{name}")
    if args.urgent:
        print(f"{C['dim']}urgent: reaches the implementer via ao lock, ao verify and "
              f"ao commit-ok{C['reset']}")
    return 0


# The board, backlog, authority and mailbox files `ao init` writes are in language.py, in the
# project's language (LANGUAGE-FILES). This steering is English in every project, as it always
# was; only the urgent heading it names is the project's, filled in as {urgent}.
STEERING_COORD = """---
inclusion: always
---

# Coordination: the `ao` tools

Every turn starts with `ao_inbox`; apply or explicitly reject each message, then
`ao_ack`. Report changes as they happen: `ao_report {{kind: "blocked"|"status"|"done"}}`.
When you need a decision, ask with options — `ao_ask` — and move to the next queued
item; do not park on prose. Check `ao_decisions` next turn.

Urgent messages (`{urgent}`) reach you through `ao lock`, `ao verify` and every `ao_*`
response. `ao commit-ok` refuses authority until you acknowledge them; an installed
AO pre-commit hook runs `ao commit-check` to revalidate them immediately before commit.

Review with `ao review`, never yourself. `ao commit-ok` refuses a review you wrote.
Heavy commands go through the machine lock: `ao lock -- <cmd>`. `ao verify` takes it
itself. Authority lives in `.ao/authority.md`, not in mail. `push` is never yours.
"""


def _detect_gates(root, children=True):
    """Guess the project's gates from what is already there. A wrong guess costs a
    failed verify, which is visible; no guess costs an empty gate file nobody
    notices, which is not.

    Every toolchain found gets its gates (#5): a repository with a JavaScript
    workspace and a .NET backend got npm gates only, and nothing verified the
    backend.
    """
    gates, quick = {}, []
    pj = os.path.join(root, "package.json")
    if os.path.exists(pj):
        try:
            scripts = (json.load(open(pj, encoding=UTF8)).get("scripts") or {})
        except Exception:
            scripts = {}
        for name, key in (("typecheck", "typecheck"), ("lint", "lint"), ("test", "test")):
            if key in scripts:
                gates[name] = {"run": f"npm run {key}", "expect": "exit_zero",
                               "timeout": 600 if name != "test" else 2400}
                if name != "test":
                    quick.append(name)
        if "test" in gates:
            gates["test"]["serialise"] = True
    if any(os.path.exists(os.path.join(root, f)) for f in ("pyproject.toml", "setup.py")):
        gates["pytest"] = {"run": "python -m pytest -q", "expect": "exit_zero",
                           "timeout": 2400, "serialise": True}
        # Collecting imports every test module: cheap, and it exercises the code.
        gates["pytest-collect"] = {"run": "python -m pytest --collect-only -q", "expect": "exit_zero",
                                   "timeout": 300}
        quick.append("pytest-collect")
        if A.program_path("ruff"):
            gates["ruff"] = {"run": "ruff check .", "expect": "exit_zero", "timeout": 300}
            quick.append("ruff")
    if any(name.endswith((".sln", ".csproj", ".fsproj")) for name in os.listdir(root)):
        gates["dotnet-build"] = {"run": "dotnet build --nologo -v q", "expect": "exit_zero", "timeout": 1200}
        gates["dotnet-test"] = {"run": "dotnet test --nologo", "expect": "exit_zero", "timeout": 2400,
                                "serialise": True}
        quick.append("dotnet-build")
    if os.path.exists(os.path.join(root, "go.mod")):
        gates["go-vet"] = {"run": "go vet ./...", "expect": "exit_zero", "timeout": 600}
        gates["go-test"] = {"run": "go test ./...", "expect": "exit_zero", "timeout": 2400, "serialise": True}
        quick.append("go-vet")
    if os.path.exists(os.path.join(root, "Cargo.toml")):
        gates["cargo-check"] = {"run": "cargo check", "expect": "exit_zero", "timeout": 1200}
        gates["cargo-test"] = {"run": "cargo test", "expect": "exit_zero", "timeout": 2400, "serialise": True}
        quick.append("cargo-check")
    for manifest, runner, check, test in (("pom.xml", "mvn", "compile", "test"),
                                           ("build.gradle", "gradle", "classes", "test"),
                                           ("Package.swift", "swift", "build", "test")):
        if os.path.isfile(os.path.join(root, manifest)):
            gates[f"{runner}-check"] = {"run": f"{runner} {check}", "expect": "exit_zero", "timeout": 1200}
            gates[f"{runner}-test"] = {"run": f"{runner} {test}", "expect": "exit_zero", "timeout": 2400,
                                      "serialise": True}
            quick.append(f"{runner}-check")
    for gate in gates.values():
        gate["inputs"] = ["**"]
    # A backend one level down was left out beside a root package.json (TRIAGE-2-MEDIUM).
    if children:
        for child in sorted(os.listdir(root)):
            path = os.path.join(root, child)
            if child.startswith(".") or child in ("node_modules", "vendor", "target", "venv") \
                    or not os.path.isdir(path) or os.path.islink(path):
                continue
            found = _detect_gates(path, children=False)
            # An unrelated directory needs no shell spelling (TRIAGE-2-MEDIUM).
            if set(found["gates"]) == {"diff-check"}:
                continue
            quoted = A.copyable_word(child)
            if quoted is None:
                raise ValueError(f"cannot quote gate directory {child!r}; rename it or declare its gates by hand")
            for name, gate in found["gates"].items():
                if name == "diff-check":
                    continue
                scoped = f"{child}/{name}"
                gates[scoped] = dict(gate, run=f"cd {quoted} && {gate['run']}",
                                     inputs=[f"{child}/**"])
                if name in found["profiles"]["quick"]:
                    quick.append(scoped)
    gates["diff-check"] = {"run": "git diff --check", "expect": "exit_zero", "timeout": 60}
    quick.append("diff-check")
    return {"gates": gates,
            "profiles": {"quick": quick, "full": list(gates)},
            "default_profile": "quick"}

def _models(adapter_id):
    try:
        return A.load_adapter(adapter_id).get("models") or {}
    except Exception:
        return {}


def _reviewer_block(adapter_id, model=None):
    """A reviewer block composed from its adapter: its actor's name, family, and argv (#88, #76)."""
    declared = A.load_adapter(adapter_id)
    route = A.compose_reviewer(adapter_id, model=model)
    name = declared.get("actor_name") or adapter_id
    block = {"id": f"{name}-reviewer-{model}" if model else f"{name}-reviewer"}
    if declared.get("family"):
        block["family"] = declared["family"]
    block["argv"] = route["argv"]
    return block


def _architect_argv(adapter_id):
    """The architect's argv: its adapter's resume, with the implementer's grant replaced by `options.architect_tools`.

    The grant is the adapter's to spell (#58, GRANTS-PINNED): every ao command the playbook's
    routine and the wake and refill prompts name, and writes to ao's coordination files only.
    An adapter that declares how to grant tools but no architect grant leaves the architect
    none: a wake then only reads, rather than holding the implementer's grant.
    """
    declared = A.load_adapter(adapter_id)
    argv = list((declared.get("resume") or {}).get("argv") or [])
    options = declared.get("options") or {}
    grant = options.get("allowed_tools") or []
    if grant:
        while grant[0] in argv:
            at = argv.index(grant[0])
            del argv[at:at + 2]
        if options.get("architect_tools"):
            argv += [part.replace("{tools}", options["architect_tools"]) for part in grant]
    return argv


def _profile_config(root, args, base):
    """Return the profile's intended config and added block names without writing.

    --review-tier chooses the tier a single harness reviews at (REVIEW-TIERS): `person` writes no
    model reviewer, and `same-family` writes review.same_family labeled, which init records as the
    person's opt-in once it writes.
    """
    cfg = dict(base) if isinstance(base, dict) else {}
    presets = A.profiles()
    prof = presets.get(getattr(args, "profile", None) or "")
    impl_adapter = getattr(args, "implementer", None) or (prof or {}).get("implementer")
    tier = getattr(args, "review_tier", None)
    opted = []
    if tier == "same-family" and S.lookup(cfg, "review.same_family") != "labeled":
        cfg["review"] = dict(cfg["review"]) if isinstance(cfg.get("review"), dict) else {}
        S.assign(cfg, "review.same_family", "labeled")
        opted.append("review.same_family labeled")
    if not impl_adapter and not prof:
        return cfg, opted
    roles = prof or presets.get(A.default_profile()) or {}
    added = []
    if "implementer" not in cfg:
        impl_adapter = impl_adapter or roles.get("implementer")
        block = {"adapter": impl_adapter, "session": "auto",
                 "name": A.load_adapter(impl_adapter).get("actor_name") or impl_adapter}
        model = getattr(args, "model", None) or _models(impl_adapter).get("default")
        if model:
            block["model"] = model
        if getattr(args, "effort", None):
            block["effort"] = args.effort
        cfg["implementer"] = block
        added.append("implementer")
    if "reviewer" not in cfg and tier != "person":
        # With --review-tier person no model reviews: a person reviews each candidate.
        reviewer = roles.get("reviewer")
        rmodel = getattr(args, "reviewer_model", None) or _models(reviewer).get("review")
        cfg["reviewer"] = dict(_reviewer_block(reviewer, rmodel),
                               _why="must not be the implementer; a different model where one is available")
        added.append("reviewer")
    if "architect" not in cfg:
        architect = roles.get("architect")
        cfg["architect"] = {"adapter": architect, "session": "auto", "cwd": root, "name": "architect",
                            "argv": _architect_argv(architect),
                            "_why": "resumable and woken only into absence; read-only tools plus ao"}
        added.append("architect")
    return cfg, added + opted


def _apply_profile(root, args):
    """Write missing profile blocks while preserving existing project choices.

    A config that exists but cannot be read is refused: planning from an empty
    document and writing the plan over it would erase what it held (#56).
    """
    document = A.project_config_document(root)
    if document["problem"] and os.path.lexists(os.path.join(root, ".ao", "config.json")):
        raise ValueError(document["problem"])
    planned, added = _profile_config(root, args, document["config"] or {})
    if added:
        A.write_project_config(root, json.dumps(planned, indent=2, ensure_ascii=False))
    return added


def _planned_project_config_text(cfg):
    """Serialize and validate one in-memory config against the disk contract."""
    if not isinstance(cfg, dict) or not cfg:
        return None, ".ao/config.json must be a non-empty top-level JSON object"
    try:
        text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
        raw = text.encode(UTF8)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        return None, f".ao/config.json is not readable valid JSON ({exc})"
    if len(raw) > A.PROJECT_CONFIG_MAX_BYTES:
        return None, (
            ".ao/config.json exceeds the "
            f"{A.PROJECT_CONFIG_MAX_BYTES:,}-byte limit"
        )
    problem = A._json_container_depth_problem(raw)
    return (None, problem) if problem else (text, None)


def _planned_runtime_config(root, cfg):
    """Apply load_config's runtime-only defaults to an in-memory document."""
    runtime = dict(cfg)
    runtime.setdefault("root", root)
    runtime.setdefault("mailbox", "agent-mail")
    runtime.setdefault("reviews", "semantic-review")
    if "implementer" not in runtime:
        found = A.discover_session(root)
        if found:
            runtime["implementer"] = found
    return runtime


def _init_tier_hints(args):
    """How `ao init` goes on after its probe refused a reviewer no review tier admits (REVIEW-TIERS)."""
    profile = getattr(args, "profile", None)
    base = f"ao init --profile {profile}" if profile else " ".join(
        ["ao init"] + ([f"--implementer {args.implementer}"] if getattr(args, "implementer", None) else []))
    other = A.default_profile()
    lines = [f"{C['dim']}or, at init:{C['reset']}"]
    if other and other != profile:
        lines.append(f"ao init --profile {other}   a reviewer of another model family: the strongest tier")
    lines.append(f"{base} --review-tier same-family --by <name>   another model of the same family, labeled "
                 "weaker independence; a person opts in, on the record")
    lines.append(f"{base} --review-tier person   no model reviewer: a person reviews each candidate with "
                 "ao person-review --by <name>")
    return lines


def _append_to_project_file(path, text):
    """Add `text` to the end of the project's own file at `path`, made where it is missing; None, or why it was not
    (LEADS-ADAPTERS).

    `ao init` read and added to `.gitignore` through whatever stood there: a link, or a file with a second name - a hard
    link to a file of the person's - took its lines into that file, a shell's startup file among them. Only a regular
    file with this one name is written, opened without following a link and without waiting on a FIFO."""
    from . import storage
    try:
        storage.append_in_tree(os.path.dirname(path), os.path.basename(path), text.encode(UTF8))
    except OSError as exc:
        return f"it is not a writable file of the project's alone ({exc})"
    return None


def cmd_init(cfg, args):
    """Put `ao` on a project. Idempotent: existing files are left alone.

    This is the answer to "how do I apply this to another project". Everything
    it writes is a file the agent already knows how to read, so a project without
    MCP or a watchdog gets the whole protocol from the files alone; the optional
    flags add the automation on top. `--adopt` is the other answer, for a project
    that has AO state and no tracked marker: `_adopt_project`.
    """
    if getattr(args, "adopt", False):
        return _adopt_project(cfg, args)
    if getattr(args, "allow_shared_hooks", False):
        print(f"{C['red']}init refused{C['reset']}: --allow-shared-hooks authorizes the hooks --adopt installs; "
              "init without --adopt installs none")
        return 1
    root = cfg["root"]
    name = args.name or os.path.basename(root)
    config_path = os.path.join(root, ".ao", "config.json")
    marker_path = os.path.join(root, PROJECT_MARKER)

    # Resolve every project-local decision before creating anything. A failed
    # reviewer probe is a clean refusal, not a half-initialized installation.
    marker_document = _worktree_project_marker_document(root)
    marker_fingerprint = marker_document["fingerprint"]
    if marker_document["exists"] and marker_document["problem"]:
        print(
            f"{C['red']}init refused{C['reset']}: "
            f"{_project_refusal(marker_document['problem'])}"
        )
        return 1

    config_existed = os.path.lexists(config_path)
    existing_raw = None
    if config_existed:
        document = A.project_config_document(root)
        if document["problem"]:
            print(
                f"{C['red']}init refused{C['reset']}: "
                f"{_project_refusal(document['problem'])}"
            )
            return 1
        base_config = document["config"]
        existing_raw = document["raw"]
    else:
        base_config = {"project": name, "round_budget": S.default("round_budget")}

    planned_config, added = _profile_config(root, args, base_config)
    # --language is chosen before init writes a word, where `ao config set` needs the config init writes
    # (LANGUAGE-OUTPUT). A person asked for it, so it replaces a language the config holds.
    chosen = getattr(args, "language", None)
    if chosen and S.lookup(planned_config, "language") != chosen:
        planned_config = S.assign(dict(planned_config), "language", chosen)
        added.append(f"language {chosen}")
    config_text, config_problem = _planned_project_config_text(planned_config)
    if config_problem:
        print(f"{C['red']}init refused{C['reset']}: {_project_refusal(config_problem)}")
        return 1
    # Opting into the same-family tier is a person's act, on the record, as `ao config set` makes it (REVIEW-TIERS).
    by = (getattr(args, "by", None) or "").strip()
    opt_in = None
    if getattr(args, "review_tier", None) == "same-family":
        if not by:
            print(f"{C['red']}init refused{C['reset']}: --by is required with --review-tier same-family: a person "
                  "opts into weaker independence, on the record")
            return 1
        if by.lower() in _agent_names(planned_config):
            print(f"{C['red']}init refused{C['reset']}: --by names an agent or a role ({by}); opting into "
                  "same-family review is a person's act")
            return 1
        user, interactive = A._login_and_terminal()
        opt_in = {"value": "labeled", "by": by, "user": user, "interactive": interactive}
    elif by:
        print(f"{C['red']}init refused{C['reset']}: --by names the person who opts in with --review-tier same-family")
        return 1
    # A quick profile that exercises none of the code gives every verify a green it did
    # not earn (#5). Checked while planning, before anything is written.
    detected_gates = None
    if not os.path.lexists(os.path.join(root, ".ao", "gates.json")):
        # Handwritten gates are also the recovery for an unquotable child (TRIAGE-2-MEDIUM).
        try:
            detected_gates = _detect_gates(root)
        except ValueError as exc:
            print(f"{C['red']}init refused{C['reset']}: {exc}")
            return 1
        minimum = S.default("gates.coverage_min_files")
        trees = A.source_trees(root, minimum)
        quick = detected_gates["profiles"]["quick"]
        uncovered = A.gate_coverage_gaps(root, detected_gates, minimum, names=quick)
        if trees and len(uncovered) == sum(len(chains) for chains in trees.values()) \
                and not getattr(args, "allow_uncovered_gates", False):
            chains = sorted({chain for chains in trees.values() for chain in chains})
            print(f"{C['red']}init refused{C['reset']}: the quick gates ({', '.join(quick)}) exercise none of "
                  f"the detected toolchains ({', '.join(chains)}); declare gates in .ao/gates.json first, "
                  "or pass --allow-uncovered-gates")
            return 1
    # A copy, since the probe's routes may carry a mark the plan must not (ROUTE-ARGV).
    runtime_cfg = _planned_runtime_config(root, json.loads(json.dumps(planned_config)))
    if opt_in:
        runtime_cfg["_same_family_opt_in"] = opt_in
    # A reviewer kept from the configuration as it stood is probed only as a person accepted it, as a review starts it;
    # one init composes is its own (ROUTE-ARGV).
    if config_existed and "reviewer" not in added:
        A.note_routes_read(runtime_cfg)
    reviewer_probe = _reviewer_probe(runtime_cfg)
    if not reviewer_probe["ok"]:
        print(
            f"{C['red']}init refused{C['reset']}: reviewer probe "
            f"{_reviewer_probe_text(reviewer_probe)}"
        )
        if reviewer_probe.get("tier_refused"):
            for line in _init_tier_hints(args):
                print(f"  {line}")
        return 1

    # The probe may take time. Revalidate both planning inputs immediately
    # before the first write; never apply a plan to marker/config state that
    # moved while the reviewer was running.
    if config_existed:
        current_config = A.project_config_document(root)
        if current_config["problem"] or current_config["raw"] != existing_raw:
            problem = (
                current_config["problem"]
                or ".ao/config.json changed while reviewer probe ran"
            )
            print(f"{C['red']}init refused{C['reset']}: {_project_refusal(problem)}")
            return 1
    elif os.path.lexists(config_path):
        print(
            f"{C['red']}init refused{C['reset']}: "
            ".ao/config.json appeared while reviewer probe ran"
        )
        return 1

    # Keep the marker read last: the concurrent-init boundary that authorizes
    # project writes is then the final observation before mutation begins.
    current_marker = _worktree_project_marker_document(root)
    if current_marker["fingerprint"] != marker_fingerprint:
        problem = (
            current_marker["problem"]
            if current_marker["exists"] and current_marker["problem"]
            else f"{PROJECT_MARKER} changed while reviewer probe ran"
        )
        print(f"{C['red']}init refused{C['reset']}: {_project_refusal(problem)}")
        return 1
    # Every file init writes in the project is made, replaced or read in folders held open from the root down, never
    # through a link (storage.create_in_tree and its kin): one on the way, there before init or put there while it
    # writes, took the configuration, the board, the playbook and the manifest wherever it led (LEADS-ADAPTERS).
    from . import storage
    # What init keeps of the project goes into .ao/, a folder of the project's own and never a link.
    if storage.not_a_folder_of_its_own(os.path.join(root, ".ao")):
        print(f"{C['red']}init refused{C['reset']}: .ao is a link or a file, where init keeps the project's "
              "configuration, board and ledgers: make it a folder of the project's, then run ao init again")
        return 1

    wrote, kept, unwritten = [], [], []

    def put(rel, content):
        # The bytes as given on every platform: Windows text mode wrote the marker with
        # CRLF, and the marker check below refused the init that had just written it (#71).
        try:
            done = storage.create_in_tree(root, rel, content.encode(UTF8))
        except storage.InTheWay as exc:
            unwritten.append((rel, f"{exc}: make it a folder, then run ao init again"))
            return
        (wrote if done == "wrote" else kept).append(rel)

    # The config is replaced whole or not at all (#56); a crash never leaves it
    # empty of the opt-ins it held. A link put in place of .ao after the look above is never written through: the
    # configuration is refused, before anything else is written (LEADS-ADAPTERS).
    if not config_existed or added:
        try:
            if not config_existed:
                done = storage.create_in_tree(root, ".ao/config.json", config_text.encode(UTF8))
                if done != "wrote":
                    print(f"{C['red']}init refused{C['reset']}: .ao/config.json appeared while init ran, after it "
                          "planned the project without one: kept it; run ao init again to plan from it")
                    return 1
            else:
                storage.replace_in_tree(root, ".ao/config.json", config_text.encode(UTF8))
        except storage.InTheWay as exc:
            print(f"{C['red']}init refused{C['reset']}: {exc}: init keeps the project's configuration, board and "
                  "ledgers in .ao; make it a folder of the project's own, then run ao init again")
            return 1
    if config_existed:
        kept.append(".ao/config.json")
    else:
        wrote.append(".ao/config.json")
    if added:
        wrote.append(".ao/config.json (+" + ", ".join(added) + ")")

    config_problem = _project_config_problem(root)
    if config_problem:
        print(f"{C['red']}init refused{C['reset']}: {_project_refusal(config_problem)}")
        return 1
    if opt_in:
        try:
            newest = A.recorded_opt_in(root, "review.same_family")
            if not newest or newest.get("value") != "labeled":
                A.record_opt_in(root, "review.same_family", "labeled", opt_in["by"])
                wrote.append(f".ao/ledger/opt-ins.jsonl (review.same_family labeled by {opt_in['by']})")
        except Exception as exc:
            print(f"{C['red']}init stopped{C['reset']}: the opt-in into same-family review could not be recorded "
                  f"({exc}), so it is not in force: ao config set review.same_family labeled --by <name>")
            return 1
    put(PROJECT_MARKER, PROJECT_MARKER_BYTES.decode("ascii"))
    marker_problem = _worktree_project_marker_problem(root)
    if marker_problem:
        print(f"{C['red']}init refused{C['reset']}: {_project_refusal(marker_problem)}")
        return 1
    # In the project's language, read from the config just planned: a project that set `language`
    # before init, or a machine that did, gets its files in that language (LANGUAGE-FILES).
    put(".ao/board.md", language.text(runtime_cfg, "init.board"))
    put(".ao/backlog.md", language.text(runtime_cfg, "init.backlog", name=name))
    put(".ao/authority.md", language.text(runtime_cfg, "init.authority"))
    if detected_gates is not None:
        put(".ao/gates.json", json.dumps(detected_gates, indent=2) + "\n")
    else:
        kept.append(".ao/gates.json")
    put(".ao/ledger/.gitkeep", "")
    put(".ao/decisions/.gitkeep", "")
    put("semantic-review/.gitkeep", "")
    put("agent-mail/README.md", language.text(runtime_cfg, "init.mail-readme"))

    gi = os.path.join(root, ".gitignore")
    # Read only as the project's own file: a FIFO there held init for as long as its writer liked, and a link was read
    # through (LEADS-ADAPTERS). What is no such file is added to by nothing below either.
    try:
        raw = storage.read_in_tree(root, ".gitignore")
    except storage.InTheWay:
        raw = None
    # Each line as a text-mode read gave it, a CRLF checkout's among them.
    lines = [] if raw is None else raw.decode(UTF8, "replace").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # The sessions ao settles for this checkout's roles are this machine's, never the repository's (SESSION-IDENTITY),
    # and so are the lanes it starts from here: each names a directory on this machine (LANE-START).
    add = [l for l in ("agent-mail/*.md", "!agent-mail/README.md", ".ao/inbox/", ".ao/hold", ".ao/sessions.json",
                       ".ao/lanes/") if l not in lines]
    # So is the MCP file init writes into the tree for a harness that reads one there, `.mcp.json` for Claude Code: its
    # entry names this machine's ao. Left untracked, it was the product file the first `ao verify` refused
    # (README-QUICKSTART-7); one the repository already tracks is the repository's, and stays so.
    add += [l for l in ([] if args.no_mcp else _mcp_files_in_tree(root, args)) if l not in lines and l not in add]
    if add:
        refused = _append_to_project_file(gi, "\n# agent-orchestrator: mail is transient; the ledger and reviews are "
                                              "not\n" + "\n".join(add) + "\n")
        if refused:
            unwritten.append((".gitignore", f"{refused}; add these lines to the project's own .gitignore yourself: "
                                            + " ".join(add)))
        else:
            wrote.append(".gitignore (+%d)" % len(add))

    # Steering for whichever agent this repo uses. Kiro reads .kiro/steering;
    # Claude Code and most others read CLAUDE.md / AGENTS.md.
    # Steering goes only into files ao owns. The owner's rule files (CLAUDE.md,
    # AGENTS.md) are never appended to unless they ask with --rules: a tool that
    # writes instructions there has, from the reading agent's side, issued rules
    # nobody authorised — the second pilot's coordinator refused exactly that.
    from . import skillkit
    requested = skillkit.detect_agents(root, args.agent)[0]
    # What ao writes into what a harness reads, from its own declarations, to record as seen (HARNESS-SEEN-OWN).
    ours = []
    for ident, adapter in skillkit.setup_adapters(root):
        coordination = (adapter.get("directives") or {}).get("coordination")
        detect = adapter.get("detect") or {}
        # A harness is here by a file of its own as much as by a directory: one declared by its files alone had
        # the playbook and no coordination (HARNESS-SETUP-3).
        present = any(os.path.isdir(skillkit._local(root, path) or "\0") for path in detect.get("dirs") or []) \
            or any(os.path.isfile(skillkit._local(root, path) or "\0") for path in detect.get("files") or [])
        if coordination and (ident == requested or present):
            if skillkit._escapes(coordination):
                print(f"  {C['yellow']}not written{C['reset']}  {coordination}: it lies outside the project (HARNESS-SETUP-2)")
                continue
            if skillkit._local(root, coordination) is None:
                unwritten.append((coordination, skillkit._through_a_link(coordination)))
                continue
            steering = STEERING_COORD.format(urgent=language.marker(runtime_cfg, "urgent"))
            put(coordination, steering)
            if skillkit._shipped(ident, adapter, lambda a: (a.get("directives") or {}).get("coordination")):
                ours.append((os.path.join(root, coordination), steering.encode(UTF8)))     # put writes "\n" as it is

    for rel in wrote:
        print(f"  {C['green']}wrote{C['reset']}  {rel}")
    for rel in kept:
        print(f"  {C['dim']}kept   {rel}{C['reset']}")
    for rel, why in unwritten:
        print(f"  {C['yellow']}not written{C['reset']}  {rel}: {why}")

    # The ao the MCP registrations name, by an absolute path, never one the current directory - the repository being
    # enrolled - holds, which Windows looks in first (WINDOWS-NO-CWD-EXEC); the watchdog's install below re-enters
    # this ao itself.
    exe = A.ao_program()
    # The playbook and the MCP registration are the two things an agent needs to
    # behave like the architect from its first turn; both are written for every
    # agent this repository is seen to use. Ask the human before running init;
    # the tool itself does not ask.
    from . import skillkit
    _, agents = skillkit.detect_agents(root, args.agent)
    # The playbook and the registrations are read and written as init's own files are, never through a link: what
    # they did not write for one is named and counted with the rest (LEADS-ADAPTERS).
    refused = []
    playbook_files = skillkit.install_playbook(root, agents, rules=getattr(args, "rules", False), ours=ours,
                                               unwritten=refused)
    _print_playbook_files(playbook_files)
    if not getattr(args, "rules", False):
        print(f"  {C['dim']}rule files untouched — paste this into {' / '.join(skillkit.rule_file_names(root))} "
              f"yourself, or re-run with --rules:{C['reset']}")
        for line in skillkit.RULE_POINTER.split("\n"):
            print(f"      {line}")
    registered = {} if args.no_mcp else skillkit.register_mcp(root, agents, exe, ours=ours, unwritten=refused)
    unwritten += refused
    # What init wrote, so `ao remove` can take exactly that away again.
    manifest = {"at": int(time.time()),
                "wrote": list(wrote) + [r for r, w in playbook_files.items() if w in ("wrote", "updated", "appended")],
                "mcp": [k for k, v in registered.items() if v == "registered"],
                "gitignore": any(rel.startswith(".gitignore") for rel in wrote)}
    # Replaced whole under a name no file had, so a link an agent left at its name is replaced, never written through
    # (LEADS-ADAPTERS).
    try:
        storage.replace_in_tree(root, ".ao/init-manifest.json", json.dumps(manifest, indent=2).encode(UTF8))
    except storage.InTheWay as exc:
        unwritten.append((".ao/init-manifest.json", str(exc)))
        print(f"  {C['yellow']}not written{C['reset']}  .ao/init-manifest.json: {exc}")
    # A person adopted the tree as it stands: its harness configuration is measured from this HEAD, once (HARNESS-CONFIG).
    A.record_harness_baseline(root, "ao init")
    # The role blocks init composed are its own commands, and the rest are accepted as they stand, once (TURN-ARGV).
    composed = [A.turn_path(role) for role in A.record_turns_written(root, planned_config, added, "ao init")]
    A.record_turn_baseline(root, "ao init")
    _print_seen_own(A.record_harness_written(root, ours, "ao init") + composed)
    for agent, what in registered.items():
        print(f"  {C['green']}mcp{C['reset']}      {agent}: {what.splitlines()[0]}")
        if what.startswith("manual"):
            print("           " + "\n           ".join(what.splitlines()[1:]))
    if args.watchdog:
        # This interpreter running this ao, handed where ao lives (SELF-REENTRY): never a program found by name.
        code = subprocess.run([sys.executable, "-m", "ao", "-C", root, "watchdog", "install"],
                              cwd=A.self_child_cwd(), env=A.self_child_env(dict(os.environ)),
                              capture_output=True, text=True, encoding=UTF8, errors="replace").returncode
        print(f"  {C['green'] if code == 0 else C['red']}watchdog{C['reset']} "
              f"{'installed' if code == 0 else 'install failed — run ao watchdog install'}")

    hook_proof = _hook_execution_probe(_ao_hook_inventory(root))
    tone = C["green"] if hook_proof["installed"] else C["yellow"]
    print(f"  {tone}commit hook{C['reset']} {_hook_probe_text(hook_proof)}")

    probe_tone = C["green"] if reviewer_probe["ok"] else C["red"]
    print(
        f"  {probe_tone}reviewer probe{C['reset']} "
        f"{_reviewer_probe_text(reviewer_probe)}"
    )

    print(f"\n{C['b']}Next, for a person:{C['reset']}")
    for line in skillkit.next_steps(agents, registered):
        print(f"  · {line}")
    if unwritten:
        print(f"{C['yellow']}init wrote the rest; {len(unwritten)} file(s) were not written, each named above with "
              f"why: make each a file or folder of the project's own, then run ao init again{C['reset']}")
        return 1
    return 0


def _print_playbook_files(written):
    """Each playbook file `install_playbook` wrote, kept or did not write, the last with why (LEADS-ADAPTERS)."""
    for rel, what in written.items():
        if what.startswith("not written: "):
            print(f"  {C['yellow']}not written{C['reset']}  {rel}: {what[len('not written: '):]}")
        else:
            print(f"  {C['green'] if what != 'kept' else C['dim']}{what:<8}{C['reset']} {rel}")


# What a new setup chooses, or asks init to write. Adoption takes a project as it was set up and
# writes only the marker and the hooks, so each of these is refused beside --adopt rather than
# ignored (INIT-ADOPTION); --agent counts only when it names an agent, and --no-mcp asks for
# nothing adoption does.
def _adopt_refused(args):
    """The options `--adopt` refuses that were given, as their flags; each read by its name, since the split proof reads
    no attribute named at run time (#111)."""
    given = (("name", getattr(args, "name", None)), ("profile", getattr(args, "profile", None)),
             ("implementer", getattr(args, "implementer", None)), ("model", getattr(args, "model", None)),
             ("effort", getattr(args, "effort", None)), ("reviewer_model", getattr(args, "reviewer_model", None)),
             ("review_tier", getattr(args, "review_tier", None)), ("by", getattr(args, "by", None)),
             ("language", getattr(args, "language", None)), ("mcp", getattr(args, "mcp", None)),
             ("rules", getattr(args, "rules", None)), ("watchdog", getattr(args, "watchdog", None)),
             ("allow_uncovered_gates", getattr(args, "allow_uncovered_gates", None)),
             ("prove", getattr(args, "prove", None)), ("no_review", getattr(args, "no_review", None)))
    return ["--" + option.replace("_", "-") for option, value in given if value]


def _mcp_files_in_tree(root, args):
    """The MCP files inside the tree that init registers ao's server in for the agents it sees, and that the
    repository does not track: each holds an entry naming this machine's ao (README-QUICKSTART-7)."""
    from . import skillkit
    _, agents = skillkit.detect_agents(root, args.agent)
    files = set()
    for ident, adapter in skillkit.setup_adapters(root):
        declared = str((adapter.get("mcp") or {}).get("file") or "") if ident in agents else ""
        if declared and not declared.startswith(("~", "/")) and ".." not in declared.split("/"):
            files.add(declared)
    if not files:
        return []
    try:
        tracked = A._git_output(root, "ls-files", "-z", "--", *sorted(files)).decode(UTF8, "replace").split("\0")
    except RuntimeError:                 # what git cannot answer for is left as it is
        return []
    return sorted(files - set(tracked))


def _git_on_active_index(root, *args):
    """Run one git command against the index enrollment is measured in; None when it succeeded, else why not."""
    extra_env, problem = _project_index_env(root)
    if problem:
        return problem
    try:
        result = _hook_git(root, *args, extra_env=extra_env)
    except _HookResolutionError as exc:
        return str(exc)
    if result.returncode:
        return result.stderr.decode(UTF8, "replace").strip()[:160] or f"git exit {result.returncode}"
    return None


def _adopt_project(cfg, args):
    """`ao init --adopt`: enroll AO state no tracked .ao-project covers, keeping all of it (INIT-ADOPTION).

    A project an older ao set up has a config that governs its commits, a hook that runs
    commit-check, and no marker anywhere in its history; one whose marker `ao init` wrote and
    nobody staged measures the same. The current hooks stand aside while no marker is
    tracked, so `ao hooks install` refuses such a project, and each message about it spelled
    out a recipe of its own. This is the one step. The exact marker is written where the
    working tree has none, then staged: enrollment is measured in the index, so the hooks
    installed next enforce from the moment they are written, as the old one did, and go on
    enforcing while the marker stays staged - the output asks for that until it lands. They
    are installed as `ao hooks install` installs them, Git is made to run the commit hook to
    prove it, and the commit that finishes the adoption is named.

    Adoption writes nothing under .ao/: the config, the board, every ledger and decision stay
    as they are. A marker file that is not the exact marker is refused, never replaced, and a
    staged marker that does not read back as one is unstaged again, which leaves the project
    governed as it was. Run again, it finds the marker staged or committed, leaves it, and
    only makes sure of the hooks. Exits 0 only when Git was seen to run the commit hook.
    """
    root = cfg["root"]
    given = _adopt_refused(args)
    if getattr(args, "agent", None) not in (None, "auto"):
        given.append("--agent")
    if given:
        print(f"{C['red']}init refused{C['reset']}: --adopt keeps the project as it was set up, and "
              f"{', '.join(given)} belong{'s' if len(given) == 1 else ''} to a new setup: ao init without --adopt")
        return 1
    enrollment = _project_enrollment(root)
    if enrollment["state"] in ("broken", "redirected"):
        print(f"{C['red']}init --adopt refused{C['reset']}: {enrollment['detail']}")
        return 1
    if enrollment["state"] == "uninitialized":
        if os.path.lexists(os.path.join(root, ".ao", "config.json")):
            why = (f"{PROJECT_MARKER} was tracked here and its removal is committed, so this AO state is inert "
                   f"on purpose; enrolling it again is a new setup: {PROJECT_INIT_COMMAND}")
        else:
            why = f"no .ao/config.json here, so no AO state to adopt; a new project starts with {PROJECT_INIT_COMMAND}"
        print(f"{C['red']}init --adopt refused{C['reset']}: {why}")
        return 1

    if enrollment["state"] == "legacy":
        document = _worktree_project_marker_document(root)
        if document["exists"] and document["problem"]:
            print(f"{C['red']}init --adopt refused{C['reset']}: {document['problem']}, and ao never replaces it: "
                  f"make it exactly ao-project-v1 or move it aside, then {PROJECT_ADOPT_COMMAND}")
            return 1
        if document["exists"]:
            print(f"  {C['dim']}kept   {PROJECT_MARKER} (ao-project-v1 already){C['reset']}")
        else:
            try:
                # Created, never replaced: a file that appeared since the read above is left as it is.
                with open(os.path.join(root, PROJECT_MARKER), "x", encoding=UTF8, newline="\n") as fh:
                    fh.write(PROJECT_MARKER_BYTES.decode("ascii"))
            except OSError as exc:
                print(f"{C['red']}init --adopt refused{C['reset']}: cannot create {PROJECT_MARKER}: {exc}")
                return 1
            print(f"  {C['green']}wrote{C['reset']}  {PROJECT_MARKER} (ao-project-v1)")
        problem = _worktree_project_marker_problem(root)
        if problem:
            print(f"{C['red']}init --adopt stopped{C['reset']}: {problem}; nothing was staged")
            return 1
        failure = _git_on_active_index(root, "add", "--", PROJECT_MARKER)
        if failure:
            print(f"{C['red']}init --adopt stopped{C['reset']}: git could not stage {PROJECT_MARKER}: {failure}")
            return 1
        enrollment = _project_enrollment(root)
        if enrollment["state"] == "redirected":
            print(f"{C['red']}init --adopt stopped{C['reset']}: {enrollment['detail']}")
            return 1
        if enrollment["state"] != "enrolled":
            # A clean filter or an attribute can store other bytes than the file's. Such a marker
            # would read as broken and refuse every commit with another repair; the project is
            # left governed as it was instead.
            undone = _git_on_active_index(root, "rm", "--cached", "--ignore-unmatch", "--quiet", "--",
                                          PROJECT_MARKER)
            print(f"{C['red']}init --adopt stopped{C['reset']}: the staged {PROJECT_MARKER} does not enroll the "
                  f"project ({enrollment['detail']}); "
                  + (f"it could not be unstaged ({undone}): git rm --cached -- {PROJECT_MARKER}" if undone
                     else "it was unstaged again, and the project is governed as it was"))
            return 1
        print(f"  {C['green']}staged{C['reset']} {PROJECT_MARKER}")
    else:
        where = "committed" if enrollment["head"]["status"] == "canonical" else "staged"
        print(f"  {C['dim']}kept   {PROJECT_MARKER} ({where} already){C['reset']}")
    try:
        ledgers = sorted(name for name in os.listdir(os.path.join(root, ".ao", "ledger")) if name.endswith(".jsonl"))
    except OSError:
        ledgers = []
    held = f" with its {len(ledgers)} ledger(s) ({', '.join(ledgers)})" if ledgers else ""
    print(f"  {C['dim']}kept   .ao/{held}: adoption writes nothing there{C['reset']}")

    allow = bool(getattr(args, "allow_shared_hooks", False))
    # Its exit status is not the answer: a foreign pre-push it preserves fails it and leaves commit
    # authority whole. What it says is printed; whether the commit hook runs is proven below.
    cmd_hooks(cfg, argparse.Namespace(action="install", allow_shared_hooks=allow))
    inventory = _ao_hook_inventory(root)
    proof = _hook_execution_probe(inventory)
    print(f"  {C['green'] if proof['installed'] else C['yellow']}commit hook{C['reset']} {_hook_probe_text(proof)}")

    print(f"\n{C['b']}Next, for a person:{C['reset']}")
    if not proof["installed"]:
        target = {} if inventory["error"] else _active_hook_targets(inventory)["pre-commit"]
        if HOOK_AO_NOT_FOUND in proof["detail"]:
            fix = _ao_link_fix()           # an alias is invisible to the hook's /bin/sh (SAFE-REMOVE)
        elif target.get("needs_authorization") and not allow and os.name != "nt":
            fix = f"{PROJECT_ADOPT_COMMAND} --allow-shared-hooks, since Git runs these hooks from a shared place"
        else:
            fix = f"ao hooks status names what runs in its place; once that is resolved, {PROJECT_ADOPT_COMMAND}"
        print(f"  · the commit hook is not proven: {fix}")
    if enrollment["head"]["status"] == "canonical":
        print(f"  · nothing to commit: {PROJECT_MARKER} is in HEAD")
    else:
        print(f"  · land the staged {PROJECT_MARKER} through the loop - ao verify, ao review, ao commit-ok, then "
              "ao commit -m \"…\" - and the project is adopted for good")
        print(f"  · keep it staged until then: the current hooks enforce while {PROJECT_MARKER} is in the index "
              "or in HEAD")
    return 0 if proof["installed"] else 1


def cmd_decide(cfg, args):
    """Record an architect decision where it survives.

    `ao note` is a message: read, acted on, deleted. A decision is a fact about
    the project that the next architect — or the same one after a compaction —
    has to be able to find. It goes to the ledger, and to the implementer's
    mailbox so it is acted on; if it answers an open `ao ask`, that is closed too.
    """
    root = cfg["root"]
    if args.list:
        p = os.path.join(root, ".ao", "ledger", "decisions.jsonl")
        rows = []
        if os.path.exists(p):
            for line in open(p, errors="replace", encoding=UTF8):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        # A rule proposal shares the ledger and is no architect's decision: `ao proposals` lists it (RULE-PROPOSALS).
        rows = [r for r in rows if isinstance(r, dict) and r.get("kind") != "proposal"]
        if not rows:
            print(f"{C['dim']}No decisions recorded.{C['reset']}")
            return 0
        for r in rows[-args.n:]:
            when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
            print(f"  {C['dim']}{when}{C['reset']}  {C['b']}{r['id']}{C['reset']}  {r['decision']}")
            if r.get("why"):
                print(f"           {C['dim']}{r['why'][:110]}{C['reset']}")
        return 0
    if not args.decision:
        print(f"usage: {C['b']}ao decide \"decision\" --why \"…\" [--answers D-123] "
              f"[--scope B2] [--urgent]{C['reset']}")
        return 1
    asked = next((d for d in A.decisions(root) if d["id"] == args.answers), None) if args.answers else None
    if asked and asked.get("proposal"):
        # Refused before anything is recorded or mailed: a decision settling it would reach the implementer as
        # one, and a rule proposal is a person's to decide (RULE-PROPOSALS).
        print(f"{C['red']}refused{C['reset']}: {args.answers} asks about rule proposal {asked['proposal']}, which a "
              f"person decides with ao answer {args.answers} <key>; no architect decision settles it")
        return 2
    holder = A.architect_lock_holder(root)
    if holder and holder.get("pid") != os.getpid() and holder.get("pid") != os.getppid():
        print(f"{C['yellow']}another architect turn holds the lock{C['reset']} ({holder.get('who')}, pid {holder.get('pid')}, "
              f"since {time.strftime('%H:%M', time.localtime(holder.get('at', 0)))}) — two judges at once contradict each other; "
              f"recording anyway, check `ao decide --list`")
    rec = A.scan_record({"id": f"AD-{int(time.time())}", "at": int(time.time()), "decision": args.decision,
                         "why": args.why, "scope": args.scope, "answers": args.answers, "by": "architect"})
    # Chained, so a row added by hand cannot pass for a re-specification (#65).
    from .storage import append_chained_jsonl
    append_chained_jsonl(A.decisions_path(root), rec, A.DECISION_CHAIN, legacy_prefix=True)
    if args.answers:
        try:
            # The decision is the answer in its own words: the free-text option (CLI-ROBUST).
            ans = A.answer(root, args.answers, "x " + args.decision, by="architect")
            print(f"  {C['green']}answered{C['reset']} {args.answers}" if ans else
                  f"  {C['yellow']}no open question {args.answers}{C['reset']}")
        except A.AnswerRefused as exc:
            # Recorded in the ledger all the same; an answer already given is not overwritten by it.
            print(f"  {C['yellow']}{args.answers} not answered{C['reset']}: {exc}")
    # The note is in the project's language (LANGUAGE-OUTPUT).
    body = f"{args.decision}\n\n" + language.text(cfg, "decide.why", why=args.why or "—")
    if args.scope:
        body += "\n\n" + language.text(cfg, "decide.scope", scope=args.scope)
    body += "\n\n" + language.text(cfg, "decide.record", id=rec["id"])
    name = A.note(root, cfg, args.to, args.decision[:60], body, urgent=args.urgent)
    print(f"  {C['green']}recorded{C['reset']} {rec['id']}  →  {cfg['mailbox']}/{name}")
    return 0


def _since_marker(root):
    return os.path.join(root, ".ao", "ledger", "since.json")


def cmd_since(cfg, args):
    """What changed since I last looked — for an architect coming back.

    `ao digest` is a window; this is a delta, anchored on the moment you last
    asked. A resumed session after a compaction, or a human back from lunch, has
    one question — what happened while I was gone — and reconstructing it from
    the whole day's digest is the tool making them do its job.
    """
    root = cfg["root"]
    ref = args.ref or "last"
    now = time.time()
    if ref == "last":
        try:
            cut = json.load(open(_since_marker(root), encoding=UTF8))["at"]
        except Exception:
            cut = now - 86400
    else:
        try:
            # The time every command reads is tried before a ref, as a duration always was; a bare
            # number is no time here, since digits alone name a commit (CLI-ROBUST).
            cut = A.parse_time(ref, now=now).moment(now=now)
        except ValueError:
            # One ref, handed to git as one argument. Through a shell the ref was split and expanded, and
            # `HEAD;touch x` ran touch; an option is no ref either (`--output=<file>` had git write a file).
            ts = "" if ref.startswith("-") else A._git_text(root, "log", "-1", "--format=%ct", ref)
            if not ts.isdigit():
                print(f"{C['red']}not a time ({A.TIME_FORMS}), a git ref, or 'last': {ref}{C['reset']}")
                return 1
            cut = float(ts)
    mins = int((now - cut) / 60)
    print(f"{C['b']}since{C['reset']} {C['dim']}{mins // 60}h {mins % 60}m ago{C['reset']}")

    log = A.git_text(root, "log", f"--since=@{int(cut)}", "--pretty=%h|%s")
    commits = [l.split("|", 1) for l in log.split("\n") if "|" in l]
    print(f"\n  {C['b']}{len(commits)}{C['reset']} commit")
    for sha, subj in commits[:8]:
        print(f"    {C['dim']}{sha}{C['reset']} {subj[:72]}")

    def newer(rel):
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d)
                      if not f.startswith(".") and os.path.getmtime(os.path.join(d, f)) >= cut)
    revs = newer(cfg["reviews"])
    if revs:
        verdicts = dict(A.reviews(root, cfg["reviews"], limit=40))
        print(f"\n  {C['b']}{len(revs)}{C['reset']} review")
        for f in revs[-6:]:
            v = verdicts.get(f, "")
            col = C["green"] if "APPROVED" in v.upper() else C["yellow"]
            print(f"    {col}{v or '?':<14}{C['reset']} {f[:60]}")
    decs = [d for d in A.decisions(root) if d.get("asked_at", 0) >= cut or (d.get("answered_at") or 0) >= cut]
    if decs:
        print(f"\n  {C['b']}{len(decs)}{C['reset']} decision")
        for d in decs[-6:]:
            st = f"{C['green']}→ {d['answer']}{C['reset']}" if d["state"] == "answered" else f"{C['yellow']}open{C['reset']}"
            print(f"    {d['id']}  {d['question'][:56]}  {st}")
    mail = [m for m in A.mailbox(root, cfg["mailbox"])
            if os.path.getmtime(os.path.join(root, cfg["mailbox"], m)) >= cut]
    if mail:
        print(f"\n  {C['b']}{len(mail)}{C['reset']} message in the mailbox now")
        for m in mail[:6]:
            print(f"    {C['dim']}{m[:70]}{C['reset']}")
    notices = [n for n in A.notices(root, 50, include_suppressed=False) if n.get("at", 0) >= cut]
    if notices:
        print(f"\n  {C['b']}{len(notices)}{C['reset']} alert sent")
        for n in notices[:4]:
            print(f"    {C['dim']}{n['title']}: {n['msg'][:60]}{C['reset']}")
    b = A.board(root)
    print(f"\n  {C['dim']}board now: " + " · ".join(f"{k} {len(v)}" for k, v in b.items() if v) + C["reset"])

    if not args.no_mark:
        os.makedirs(os.path.dirname(_since_marker(root)), exist_ok=True)
        json.dump({"at": now}, open(_since_marker(root), "w", encoding=UTF8))
    return 0


# What a board line carries between its brackets and a needs note can name whole: lib_transcript.board
# reads up to the first ']', lib_state._edge_ids splits at commas and spaces and takes a word in
# parentheses for a remark, and plan_digest reads the plan named before the first '/'. A backslash is a
# path separator on one system and not on another, and a '·' separates a line's notes to a person.
BOARD_ID = r"[^\s,\]\\·(/][^\s,\]\\·]*"


def _board_add(cfg, args, edit):
    """Admit one item to `queued` the way `ao source import` admits one: only with its boundary.

    Work entered a board by hand-editing `.ao/board.md` or through a tracker import. Both keep the rule
    that matters - nothing runs unattended without an acceptance boundary written before the work - and
    the first asks a person to learn a file's shape first. This writes the line the import writes and
    records the same plan baseline, and it refuses what the import would hold back or the board could
    not hold: no boundary, an id already there or one another item's needs could not name, a dependency
    on an item that is not on the board. Refused, nothing is written.

    An id is what the board reads between a line's brackets and what a needs note can name: a phase
    item such as ACME-187/1 is one, baselined on the plan ACME-187 (BOARD-ADD-2). `--needs` is read the
    way the board reads a needs note, split at commas and spaces, so what is checked is what is written.
    The checks and the line they admit are made on one reading of the board, `edit` (SLICE-LIFECYCLE-2).
    """
    import re
    root = cfg["root"]
    for line in _mailbox_banner(cfg):
        print(line)
    iid, title = (args.id or "").strip(), " ".join((args.title or "").split())
    acceptance, role = " ".join((args.acceptance or "").split()), (args.role or "").strip()
    needs = [token for token in re.split(r"[,\s]+", args.needs or "") if token]
    board = edit.board
    held = {item["id"] for state in A.BOARD_STATES for item in board.get(state, [])}
    if edit.data is None:
        problem = "this project has no .ao/board.md; `ao init` writes one"
    elif not re.fullmatch(BOARD_ID, iid):
        problem = (f"`{iid}` cannot be an id: an id holds no space, comma, ']', backslash or '·' and does not "
                   "start with '(' or '/', so that a board line carries it whole and a needs note can name it")
    elif iid in held:
        problem = f"{iid} is already on the board"
    elif not title:
        problem = "an item needs a title: `ao board add ID \"what it is\" --acceptance \"…\"`"
    elif not acceptance:
        problem = ("an item enters the board only with a written acceptance boundary: "
                   "--acceptance \"what the work must meet\"")
    elif any("·" in text for text in (title, acceptance, role)):
        problem = "a title, a boundary and a role hold no '·': it separates a board line's notes"
    elif [name for name in needs if not re.fullmatch(BOARD_ID, name)]:
        problem = ("--needs names " + ", ".join(f"`{name}`" for name in needs if not re.fullmatch(BOARD_ID, name))
                   + ", which a needs note would not read as an id")
    elif [name for name in needs if name not in held]:
        problem = ("it needs " + ", ".join(name for name in needs if name not in held)
                   + ", which the board does not hold")
    else:
        problem = None
    if problem:
        print(f"{C['red']}REFUSED{C['reset']}  {problem}")
        return 2
    notes = [("acceptance", acceptance)] + ([("needs", ", ".join(needs))] if needs else []) \
        + ([("role", role)] if role else [])
    edit.append("queued", f"- [{iid}] {title}" + "".join(f" · {key}: {value}" for key, value in notes))
    edit.write()
    digest = A.plan_digest(root, iid)
    if digest:
        A.record_plan(root, iid, digest)       # the plan as admitted: later edits are measured against it
    waiting = f"  {C['dim']}waits for {', '.join(needs)}{C['reset']}" if needs else ""
    print(f"{C['green']}queued{C['reset']}  {C['b']}{iid}{C['reset']}  {title}{waiting}")
    return 0


def cmd_board(cfg, args):
    """Where every pre-authorised item is, blocked ones first.

    Order is deliberate: `running` and `blocked` are the two states a human can
    act on, and `blocked` is the one that goes unnoticed — work carried on past
    it, so nothing else in the panel looks wrong.
    """
    if getattr(args, "view", None) == "add":
        with A.BoardEdit(cfg["root"], hold=True) as edit:
            return _board_add(cfg, args, edit)
    root = cfg["root"]
    for line in _mailbox_banner(cfg):
        print(line)
    if getattr(args, "view", None) == "ready" and os.path.exists(os.path.join(root, ".ao", "board.md")):
        # Exactly the eligible items, for a script: a broken edge exits 1 (#33).
        graph = A.board_graph(root)
        for problem in graph["problems"]:
            print(f"{C['red']}board: {A.shown_text(problem)}{C['reset']}")
        for it in graph["ready"]:
            print(f"{A.shown_text(it['id'])}  {A.shown_text(it['title'])}")
        return 1 if graph["problems"] else 0
    for line in _board_lines(cfg):
        print(line)
    return 0


def _board_lines(cfg):
    """The board as `ao board` prints it below the mailbox banner, one string a line.

    `ao watch --web` serves the same lines, so the page and the command cannot drift apart.
    """
    root = cfg["root"]
    b = A.board(root)
    path = os.path.join(root, ".ao", "board.md")
    if not os.path.exists(path):
        return [f"{C['yellow']}No board here.{C['reset']} Create {C['b']}.ao/board.md{C['reset']} with "
                f"`## running` / `## blocked` / `## queued` / `## verified` / `## done` sections\n"
                f"and one `- [ID] title · note: value` line per item."]
    colours = {"running": C["green"], "blocked": C["red"], "queued": C["dim"],
               "verified": C["cyan"], "done": C["dim"]}
    # Eligible work first: queued items whose `needs:` are all done. This is the
    # dependency graph answering "what is next" without the implementer choosing
    # its own scope. A broken edge is named before anything else (#33).
    # The board is text an agent writes, and what is read from it is shown escaped (LEADS-MCP).
    shown = A.shown_text
    graph = A.board_graph(root)
    lines = []
    for problem in graph["problems"]:
        lines.append(f"{C['red']}{C['b']}BOARD{C['reset']}  {C['red']}{shown(problem)}{C['reset']}")
    rd = graph["ready"]
    if rd:
        lines.append(f"\n{C['b']}{C['green']}READY{C['reset']} {C['dim']}({len(rd)}) — "
                     f"dependencies satisfied{C['reset']}")
        for it in rd:
            role = f"  {C['cyan']}role:{shown(it['role'])}{C['reset']}" if it.get("role") else ""
            lines.append(f"   {C['b']}{shown(it['id'])}{C['reset']}  {shown(it['title'])}{role}")
    for st in ("running", "blocked", "queued", "verified", "done"):
        items = b[st]
        if not items:
            continue
        lines.append(f"\n{C['b']}{colours[st]}{st.upper()}{C['reset']} {C['dim']}({len(items)}){C['reset']}")
        for it in items:
            notes = "  ".join(f"{C['dim']}{shown(k)}:{C['reset']} {shown(v)}" if v
                              else f"{C['dim']}{shown(k)}{C['reset']}" for k, v in it["notes"].items())
            lines.append(f"   {C['b']}{shown(it['id'])}{C['reset']}  {shown(it['title'])}"
                         + (f"   {notes}" if notes else ""))
            if st in ("running", "queued"):
                # Named when the item is registered, while widening or splitting is cheap (#35).
                for advice in A.boundary_advice(root, cfg, it):
                    lines.append(f"      {C['yellow']}boundary: {shown(advice)}{C['reset']}")
    if not any(b[st] for st in colours):
        # A board holding only what waits for a boundary printed nothing at all (LEADS-ADAPTERS).
        lines.append(f"{C['dim']}No item is admitted: {len(b['inbox'])} wait(s) in the inbox of .ao/board.md for an "
                     f"acceptance boundary.{C['reset']}" if b["inbox"] else f"{C['dim']}Board is empty.{C['reset']}")
    return lines


# ---- a slice's moves: start, block, abandon, each a line in the slice ledger (SLICE-LIFECYCLE) ----------

# The sections a slice is open in; done and rejected are where it ends.
SLICE_OPEN = ("running", "blocked", "queued", "inbox", "verified")


def _slice_refused(problem):
    print(f"{C['red']}REFUSED{C['reset']}  {A.shown_text(problem)}")
    return 2


def _slice_when(at):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(at or 0)))


def _slice_age(seconds):
    minutes = max(0, int(seconds // 60))
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 24 * 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes // (24 * 60)}d {minutes % (24 * 60) // 60}h"


def _slice_places(board, iid):
    """[(state, item)] for each line the board holds with this id: one, unless the board names it twice."""
    return [(state, item) for state in A.BOARD_STATES for item in board[state] if item["id"] == iid]


def _slice_named(board, named):
    """(id, None) for the slice a command acts on: the one named, else the one running; (None, why) when neither."""
    named = (named or "").strip()
    if named:
        return named, None
    running = [item["id"] for item in board["running"]]
    if not running:
        return None, "nothing is running: name the slice with --slice"
    if len(running) > 1:
        return None, f"{len(running)} slices are running ({', '.join(running)}): name one with --slice"
    return running[0], None


def _slice_item(board, iid):
    """(state, item, None) for one board id, or (None, None, why it cannot be moved)."""
    places = _slice_places(board, iid)
    if not places:
        return None, None, f"{iid} is not on the board; `ao board add` admits an item with its acceptance boundary"
    if len(places) > 1:
        return None, None, (f"{iid} is on the board twice, under {places[0][0]} and {places[1][0]}: keep one line "
                            "of it, then move it")
    return places[0][0], places[0][1], None


def _slice_blocked_by(root, iid):
    """Who made a blocked slice's block, from the newest move ao recorded of it; None when no move says so."""
    rows = [row for row in A.slice_moves(root) if row.get("slice") == iid]
    return rows[-1].get("by") if rows and rows[-1].get("to") == "blocked" else None


def _boundary_file_pointer(root, named):
    """(`path@commit`, None) for the boundary file a slice starts against, or (None, why it cannot be one).

    A boundary file is read at the commit its pointer names (docs/slices.md), so it must be in the repository
    and committed as it stands: an uncommitted one would be read as the worktree holds it, which the
    implementer can change. The path is the repository's, from its root, as a row's `paths:` are.
    """
    real_root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, named))
    if not full.startswith(real_root + os.sep) or not os.path.isfile(full):
        return None, f"--boundary-file {named}: no file of that name inside the repository, from its root"
    rel = os.path.relpath(full, real_root).replace(os.sep, "/")
    if "·" in rel or "@" in rel:
        return None, f"--boundary-file {rel}: a pointer is `path@commit`, and a path holding '·' or '@' cannot be one"
    try:
        tracked = rel.encode(UTF8) in A._git_output(root, "ls-files", "-z", "--", rel).split(b"\0")
        changed = A._git_output(root, "status", "--porcelain", "-z", "--", rel).strip(b"\0")
    except RuntimeError as exc:
        return None, f"--boundary-file {rel}: {exc}"
    if not tracked or changed:
        what = "has changes nobody committed" if tracked else "is not committed"
        return None, (f"--boundary-file {rel} {what}: commit it first, since a boundary is read at the commit its "
                      "pointer names")
    head = A._git_text(root, "rev-parse", "--short=12", "HEAD")
    return f"{rel}@{head}", None


def _slice_start(cfg, args, edit):
    """Move a queued slice to running, or resume a blocked one; each start is a line in the slice ledger.

    The playbook has the implementer take the first READY item and settle alone which READY item comes next, so a
    turn ao started for the implementer may start a READY item - queued, its needs landed, waiting on no one - and
    resume a slice it blocked itself. Anything else chooses scope, which is the architect's or a person's: an item
    that is not READY, a block someone else made, a boundary file. Refused, nothing is written (SLICE-LIFECYCLE).
    Whether it is READY is judged on `edit`, the reading the move is made on (SLICE-LIFECYCLE-2).
    """
    shown_text = A.shown_text
    root = cfg["root"]
    iid, role = (args.text or "").strip(), A.invoking_role()
    if not iid:
        return _slice_refused("name the item: `ao slice start <id>`; `ao board ready` lists what may start now")
    state, item, problem = _slice_item(edit.board, iid)
    if problem:
        return _slice_refused(problem)
    if state == "running":
        if args.boundary_file:
            return _slice_refused(f"{iid} is running already: a boundary that changes mid-slice is a "
                                  f"re-specification, recorded with `ao decide \"…\" --scope {iid} --why \"…\"`")
        print(f"{C['dim']}{shown_text(iid)} is running already; nothing moved{C['reset']}")
        return 0
    if state not in ("queued", "blocked"):
        return _slice_refused({
            "inbox": f"{iid} is in the inbox, held without an acceptance boundary: it starts once one is written "
                     "and it is queued",
            "verified": f"{iid} is verified: its gates passed, and it lands rather than starts again",
        }.get(state, f"{iid} is {state}: a slice that ended does not start again; `ao board add` admits new work"))
    not_ready = A.lane_not_ready(root, iid, edit.data) if state == "queued" else None
    if role == "implementer":
        if args.boundary_file:
            return _slice_refused("a boundary is the architect's to write (docs/roles.md): start the item as the "
                                  "board admitted it, or ask for another boundary with `ao ask`")
        if not_ready:
            return _slice_refused(f"{not_ready}; an implementer starts only a READY item, and `ao board ready` "
                                  "lists them")
        blocker = _slice_blocked_by(root, iid) if state == "blocked" else "implementer"
        if blocker != "implementer":
            return _slice_refused(
                f"{iid} is parked on quota by the watchdog, which gives it back when the limit resets"
                if blocker == "watchdog" else
                f"{iid} was blocked by {blocker or 'a hand edit ao has no record of'}: a block is lifted by whoever "
                "made it, the architect or a person")
    notes = {"waiting": None}
    if state == "queued" or not item["notes"].get("since"):
        notes["since"] = time.strftime("%Y-%m-%d %H:%M")
    if state == "blocked":
        # The block's reason goes; a `needs:` the slice carried before it comes back, as a quota park gives it back.
        notes.update({"needs": item["notes"].get(A.HELD_NEEDS) or None, A.HELD_NEEDS: None})
    if args.boundary_file:
        pointer, problem = _boundary_file_pointer(root, args.boundary_file)
        if problem:
            return _slice_refused(problem)
        # One source of truth, never two: the file replaces the sentence, which the ledger keeps (docs/slices.md).
        notes.update({"boundary": pointer, "acceptance": None})
    why = "; ".join(part for part in (" ".join((args.why or "").split()),
                                      f"started while not READY: {not_ready}" if not_ready else "") if part) or None
    others = [other["id"] for other in edit.board["running"]]
    edit.move(iid, "running", notes, by=role or "person", why=why)
    edit.write()
    print(f"{C['green']}running{C['reset']}  {C['b']}{shown_text(iid)}{C['reset']}  {shown_text(item['title'])}  "
          f"{C['dim']}{state} → running, recorded in .ao/ledger/slices.jsonl{C['reset']}")
    if why:
        print(f"  {C['yellow']}{shown_text(why)}{C['reset']}")
    if others:
        print(f"  {C['dim']}also running: {shown_text(', '.join(others))}; reviews now count for {shown_text(iid)}, "
              f"the first slice under running{C['reset']}")
    return 0


def _slice_block(cfg, args, edit):
    """Park a running slice under `blocked` with what it waits on, which anyone may do.

    The reason becomes the item's `needs:`, which on a blocked item is its reason in words and what `ao status`
    shows; a `needs:` the slice carried is kept in `held-needs:` and given back when it resumes, as the watchdog's
    quota park keeps it. `--waiting human` says it waits on a person, who is told directly (SLICE-LIFECYCLE).
    """
    shown_text = A.shown_text
    import re
    why, waiting = " ".join((args.text or args.why or "").split()), (args.waiting or "").strip()
    if not why:
        return _slice_refused("say what the slice waits on: `ao slice block \"the vendor's signing key\"`")
    if "·" in why:
        return _slice_refused("a reason holds no '·': it separates a board line's notes")
    if waiting and not re.fullmatch(r"[\w-]+", waiting):
        return _slice_refused("--waiting is one word: human, architect, decision or external")
    iid, problem = _slice_named(edit.board, args.slice)
    state, item, problem = (None, None, problem) if problem else _slice_item(edit.board, iid)
    if problem:
        return _slice_refused(problem)
    if state == "blocked":
        print(f"{C['dim']}{shown_text(iid)} is blocked already, on "
              f"{shown_text(item['notes'].get('needs') or 'nothing it names')}; nothing moved{C['reset']}")
        return 0
    if state != "running":
        return _slice_refused(f"{iid} is {state}: only a running slice is blocked; a queued item that must not "
                              "start yet carries `waiting:` instead")
    notes = {"needs": why}
    if item["notes"].get("needs") and not item["notes"].get(A.HELD_NEEDS):
        notes[A.HELD_NEEDS] = item["notes"]["needs"]
    if waiting:
        notes["waiting"] = waiting
    edit.move(iid, "blocked", notes, by=A.invoking_role() or "person", why=why)
    edit.write()
    print(f"{C['red']}blocked{C['reset']}  {C['b']}{shown_text(iid)}{C['reset']}  {shown_text(item['title'])}  "
          f"{C['dim']}needs: {shown_text(why)}{' · waiting: ' + shown_text(waiting) if waiting else ''}; "
          f"running → blocked, recorded in .ao/ledger/slices.jsonl{C['reset']}")
    print(f"  {C['dim']}take the next READY item (`ao board ready`); `ao slice start {shown_text(iid)}` resumes it{C['reset']}")
    return 0


def _slice_abandon(cfg, args, edit):
    """Drop a slice on the record: its row moves to `rejected`, with the reason as its `abandoned:` note.

    Abandoning is a legitimate outcome (docs/slices.md), and a row deleted from the board said nothing of the
    slice ever having been dropped, or why. `rejected` is the section the board already keeps for work that will
    not land: its worktree may be retired and its review artefacts archived. Whether work is dropped is scope, the
    architect's or a person's to decide, so a turn ao started for the implementer is refused (SLICE-LIFECYCLE).
    """
    shown_text = A.shown_text
    role = A.invoking_role()
    if role == "implementer":
        return _slice_refused("abandoning a slice is the architect's decision or a person's (docs/roles.md): park it "
                              "with `ao slice block \"…\"` and ask whether to drop it with `ao ask`")
    why = " ".join((args.why or "").split())
    if not why:
        return _slice_refused("--why is required: an abandoned slice is an outcome, and its reason is what is kept "
                              "of it")
    if "·" in why:
        return _slice_refused("a reason holds no '·': it separates a board line's notes")
    iid, problem = _slice_named(edit.board, args.slice or args.text)
    state, item, problem = (None, None, problem) if problem else _slice_item(edit.board, iid)
    if problem:
        return _slice_refused(problem)
    if state == "rejected":
        print(f"{C['dim']}{shown_text(iid)} is rejected already; nothing moved{C['reset']}")
        return 0
    if state == "done":
        return _slice_refused(f"{iid} is done: a slice that landed is not abandoned; undoing what it landed is a "
                              "slice of its own")
    edit.move(iid, "rejected", {"abandoned": why}, by=role or "person", why=why)
    edit.write()
    print(f"{C['yellow']}abandoned{C['reset']}  {C['b']}{shown_text(iid)}{C['reset']}  {shown_text(item['title'])}  "
          f"{C['dim']}{state} → rejected, recorded in .ao/ledger/slices.jsonl{C['reset']}")
    if state in ("running", "blocked"):
        print(f"  {C['dim']}it was {state}: tell the implementer with `ao note \"…\"`, since what it has in the tree "
              f"for {shown_text(iid)} belongs to no slice now{C['reset']}")
    return 0


def _slice_status(cfg, args):
    """Where one slice stands: its state and since when, the moves ao recorded, rounds used and its newest review.

    It reads and writes nothing. The board is what a restarted session reads; the ledger says how the slice got
    there, and where the two disagree the slice was moved by hand, which is said rather than smoothed over
    (SLICE-LIFECYCLE).
    """
    shown_text = A.shown_text
    root, reviews = cfg["root"], cfg.get("reviews", "semantic-review")
    board = A.board(root)
    named = (args.text or args.slice or "").strip()
    if not named:
        running = [item["id"] for item in board["running"]]
        if not running:
            print(f"{C['dim']}Nothing is running.{C['reset']} `ao slice status <id>` for another slice, `ao slices` "
                  "for every one.")
            return 0
        named = running[0]              # the one review accounting reads as current
    places = _slice_places(board, named)
    try:
        history, unreadable = [row for row in A.slice_moves(root, voided=True) if row.get("slice") == named], None
    except Exception as exc:
        history, unreadable = [], f"{type(exc).__name__}: {' '.join(str(exc).split())}"
    # A move whose board write failed is in the history, marked, and nowhere else (SLICE-LIFECYCLE-2).
    moves = [row for row in history if not row.get("void")]
    if not places and not moves:
        print(f"{C['yellow']}{shown_text(named)} is not on the board{C['reset']}, and ao recorded no move of it"
              + (" that reached the board" if history else "") + "; `ao slices` lists every slice")
        return 1 if unreadable is None else _slice_unreadable(unreadable)
    state, item = places[0] if places else (None, None)
    newest = moves[-1] if moves else None
    title = item["title"] if item else next((row["title"] for row in history if row.get("title")), "")
    print(f"{C['b']}{shown_text(named)}{C['reset']}  {shown_text(title)}")
    if state is None:
        print(f"  state     {C['red']}not on the board{C['reset']}: ao last recorded it under {shown_text(newest.get('to'))} on "
              f"{_slice_when(newest.get('at'))}, and its row was removed by hand; `ao slice abandon --why` drops a "
              "slice on the record")
    elif newest and newest.get("to") == state:
        print(f"  state     {state} since {_slice_when(newest.get('at'))}, "
              f"{_slice_age(time.time() - float(newest.get('at') or 0))} ago, moved by {shown_text(newest.get('by'))}")
    elif newest:
        print(f"  state     {state}  {C['yellow']}{shown_text(_slice_off_record(newest, state))}{C['reset']}")
    else:
        since = item["notes"].get("since")
        print(f"  state     {state}" + (f" since {shown_text(since)}, as the board's since: note says" if since else "")
              + f"  {C['dim']}no move recorded: put on the board or moved by hand{C['reset']}")
    if places[1:]:
        print(f"  {C['red']}on the board twice{C['reset']}, under {places[0][0]} and {places[1][0]}")
    if item and state in SLICE_OPEN:
        spent, budget = A.rounds(root, reviews, item), S.get(cfg, "round_budget")
        colour = C["red"] if spent > budget else C["reset"]
        print(f"  rounds    {colour}{spent} of {budget}{C['reset']}")
    try:
        review = A.latest_slice_review(root, reviews, named)
    except Exception as exc:
        review, unreadable = None, unreadable or f"the review ledger: {type(exc).__name__}: {exc}"
    if review is None:
        print(f"  review    {C['dim']}none that reached a verdict{C['reset']}")
    else:
        kind = " (retrospective)" if review["kind"] == "commit-range" else ""
        colour = C["green"] if review["verdict"] == "APPROVED" else C["yellow"]
        print(f"  review    {colour}{shown_text(review['verdict'])}{C['reset']}{kind} on {_slice_when(review['at'])}  "
              f"{C['dim']}{shown_text(reviews)}/{shown_text(review['artefact'])}{C['reset']}")
        if not review.get("recorded", True):
            print(f"            {C['yellow']}no row in the review ledger: a review from before it, read from its file "
                  f"as it stands{C['reset']}")
        counts = review["counts"] or {}
        if counts:
            print("            " + " · ".join(f"{key} {shown_text(counts.get(key, 0))}"
                                             for key in ("BLOCKER", "HIGH", "MEDIUM", "LOW")))
        if review["problem"]:
            print(f"            {C['yellow']}{shown_text(review['problem'])}{C['reset']}: its findings cannot be read")
        elif review["verdict"] == "NEEDS_CHANGES":
            for finding in review["findings"][:10]:
                print(f"            - {shown_text(finding)}")
            if len(review["findings"]) > 10:
                print(f"            {C['dim']}and {len(review['findings']) - 10} more{C['reset']}")
    if history:
        print("  moves")
        for row in history:
            arrow = f"{row.get('from')} → {row.get('to')}" if row.get("from") else f"enters {row.get('to')}"
            void = row.get("void")
            print(f"    {_slice_when(row.get('at'))}  {shown_text(arrow):<20}  by {shown_text(row.get('by'))}"
                  + (f": {shown_text(row['why'])}" if row.get("why") else "")
                  + (f"  {C['yellow']}did not happen: {shown_text(void.get('why'))}{C['reset']}" if void else ""))
    if item and item["notes"]:
        print("  notes     " + " · ".join(f"{shown_text(key)}: {shown_text(value)}" if value else shown_text(key)
                                        for key, value in item["notes"].items()))
    return 0 if unreadable is None else _slice_unreadable(unreadable)


def _slice_off_record(row, state):
    """What it means that the board holds a slice under `state` while ao's newest recorded move put it elsewhere.

    A row moved by hand, mostly. Where the board still holds it in the section that move took it from, the board
    may never have taken the move: ao records a move before it writes the board, and a process stopped between
    the two writes leaves no line voiding it (SLICE-LIFECYCLE-2).
    """
    if row.get("from") == state:
        return (f"the board never took ao's last recorded move of it, {state} → {row.get('to')} on "
                f"{_slice_when(row.get('at'))}: ao stopped between recording it and writing the board, or a hand "
                "moved it back")
    return f"moved by hand: ao last recorded it under {row.get('to')} on {_slice_when(row.get('at'))}"


def _slice_unreadable(problem):
    print(f"{C['red']}a ledger cannot be read{C['reset']}: {A.shown_text(problem)}. Nothing is appended to a chained "
          "ledger until it is mended, so no move is recorded or made until then (docs/ledger.md)")
    return 1


def cmd_slice(cfg, args):
    """`ao slice start|block|abandon|status`: a slice's moves between the board's sections, each one recorded.

    A move is decided on one reading of the board and made on that same reading, which ao's other writers wait
    for and which is compared with the board as it stands before it is replaced (BoardEdit): a row edited
    meanwhile is never moved on a judgement made before the edit (SLICE-LIFECYCLE-2).
    """
    for line in _mailbox_banner(cfg):
        print(line)
    if args.action == "status":
        return _slice_status(cfg, args)
    with A.BoardEdit(cfg["root"], hold=True) as edit:
        return {"start": _slice_start, "block": _slice_block, "abandon": _slice_abandon}[args.action](cfg, args, edit)


def cmd_slices(cfg, args):
    """Every slice on the board, by state, with since when; `--open` leaves out done and rejected.

    A slice whose row was removed by hand is listed too, from the last move ao recorded of it: a deleted row is
    the quiet drop docs/slices.md warns of, and abandoning one on the record is `ao slice abandon` (SLICE-LIFECYCLE).
    """
    shown_text = A.shown_text
    root = cfg["root"]
    for line in _mailbox_banner(cfg):
        print(line)
    board = A.board(root)
    try:
        moves, unreadable = A.slice_moves(root), None
    except Exception as exc:
        moves, unreadable = [], f"{type(exc).__name__}: {' '.join(str(exc).split())}"
    newest, titles = {}, {}
    for row in moves:
        newest[row.get("slice")] = row
        titles[row.get("slice")] = row.get("title") or titles.get(row.get("slice")) or ""
    held = {item["id"] for items in board.values() for item in items}
    shown = 0
    for state in SLICE_OPEN + (() if args.open else ("done", "rejected")):
        for item in board[state]:
            row, shown = newest.get(item["id"]), shown + 1
            since = _slice_when(row.get("at")) if row and row.get("to") == state else "-"
            why = item["notes"].get("needs") if state == "blocked" else item["notes"].get("abandoned")
            hand = f"  {C['yellow']}{shown_text(_slice_off_record(row, state))}{C['reset']}" \
                if row and row.get("to") != state else ""
            print(f"  {C['b']}{shown_text(item['id']):<14}{C['reset']} {state:<9} {since:<16}  {shown_text(item['title'])}"
                  + (f"  {C['dim']}{shown_text(why)}{C['reset']}" if why else "") + hand)
    for sid, row in newest.items():
        if sid not in held and row.get("to") and (not args.open or row.get("to") in SLICE_OPEN):
            shown += 1
            print(f"  {C['b']}{shown_text(sid):<14}{C['reset']} {C['red']}gone{C['reset']}      "
                  f"{_slice_when(row.get('at')):<16}  "
                  f"{shown_text(titles.get(sid))}  {C['yellow']}not on the board: ao last recorded it under "
                  f"{shown_text(row.get('to'))}, and its row was removed by hand{C['reset']}")
    if not shown:
        print(f"{C['dim']}No {'open ' if args.open else ''}slices on the board.{C['reset']}")
    return 0 if unreadable is None else _slice_unreadable(unreadable)


def cmd_source(cfg, args):
    """External work queues: what is configured, what is waiting, what may enter.

    Admission is the whole point of this command. Pulling an issue is free and
    carries no authority; a tracker item is something a person wrote, not a
    specification anyone verified. An item enters the board only with a written
    acceptance boundary, because the alternative — an agent inferring scope from
    a title — is how a one-line bug fix becomes a refactor nobody asked for.

    `ao` does not judge which items qualify: it has no model and no tracker
    credential. It enforces the rule; an architect turn makes the call and writes
    it into the inbox file.
    """
    root = cfg["root"]
    sc = A.sources(root)
    if not sc:
        print(f"{C['yellow']}No sources configured.{C['reset']}  Create {C['b']}.ao/sources.json{C['reset']}:\n"
              f'  {{"bound_root": "{root}", "wip_limit": 1, "refill_below": 3,\n'
              f'   "sources": [{{"id": "linear-x", "kind": "mcp", "server": "linear",\n'
              f'                "select": {{"team": "…", "state": "Todo"}}}}]}}')
        return 0

    files = A.inbox_files(root)
    if args.action in ("list", "status"):
        bd = A.board(root)
        print(f"{C['b']}bound to{C['reset']}  {sc.get('bound_root', root)}")
        for src in sc["sources"]:
            sel = " ".join(f"{k}={v}" for k, v in (src.get("select") or {}).items())
            wb = (src.get("writeback") or {})
            wbs = (f"{C['yellow']}writes back{C['reset']}" if wb.get("enabled")
                   else f"{C['dim']}read-only{C['reset']}")
            print(f"  {C['b']}{src['id']}{C['reset']}  {src.get('kind','mcp')}:{src.get('server','?')}"
                  f"  {C['dim']}{sel}{C['reset']}  {wbs}")
        depth = len(bd["queued"])
        low = depth < sc["refill_below"]
        col = C["yellow"] if low else C["green"]
        print(f"\n{C['b']}queue{C['reset']}  {col}{depth} admitted{C['reset']}"
              f"{C['dim']} · refill below {sc['refill_below']} · wip limit {sc['wip_limit']}{C['reset']}"
              + (f"  {C['yellow']}← needs a refill pass{C['reset']}" if low else ""))
        print(f"{C['b']}inbox{C['reset']}  {len(bd['inbox'])} on the board, "
              f"{len(files)} pull file(s) not yet imported")
        return 0

    # import — the only place an item crosses from "someone wrote this" to
    # "an agent may work on it unattended". Every item is judged and put on one reading of the board, written
    # once, and a pull file is marked imported only after that write: a board that changed meanwhile leaves
    # every file to import again (SLICE-LIFECYCLE-2).
    if not files:
        print(f"{C['dim']}Nothing in .ao/inbox/ to import.{C['reset']}")
        return 0
    admitted, held, refused, done = [], 0, 0, []
    with A.BoardEdit(root, hold=True) as edit:
        for f in files:
            try:
                doc = json.load(open(f, encoding=UTF8))
            except Exception as e:
                print(f"{C['red']}skip{C['reset']} {os.path.basename(f)}: unreadable ({e})")
                continue
            err = A.binding_error(root, doc.get("bound_root"))
            if err:
                print(f"{C['red']}REFUSED{C['reset']} {os.path.basename(f)}: {err}")
                continue
            # Each text a board line carries is one line, as `ao board add` writes it: a tracker item's title is
            # anyone's text, and a line break in it wrote items and sections of its own into the board, an
            # acceptance boundary among them that nobody wrote (LEADS-ADAPTERS).
            sid = " ".join(str(doc.get("source", "?")).split())
            refused_here = 0
            for number, it in enumerate(doc.get("items", []), 1):
                iid = it.get("id") if isinstance(it, dict) else None
                # An id is a text or a whole number: an item with none, an empty one or a false one was dropped
                # without a word and its file marked imported, so nobody could correct it (the review of
                # LEADS-ADAPTERS).
                if isinstance(iid, bool) or not isinstance(iid, (str, int)) or not str(iid).strip():
                    print(f"{C['red']}REFUSED{C['reset']} item {number} in {os.path.basename(f)}: "
                          + ("it has no id, a text or a whole number the board names it by" if isinstance(it, dict)
                             else "it is no object with an id"))
                    refused_here += 1
                    continue
                iid = str(iid)
                title = " ".join(str(it.get("title") or "").split())
                acc = " ".join(str(it.get("acceptance") or "").split())
                why = " ".join(str(it.get("shape") or "").split()) or "no acceptance boundary written"
                problem = _source_item_problem(iid, (sid, title, acc, why))
                if problem:
                    print(f"{C['red']}REFUSED{C['reset']} {A.shown_text(iid)} in {os.path.basename(f)}: {problem}")
                    refused_here += 1
                    continue
                if any(x["id"] == iid for st in A.BOARD_STATES for x in edit.board[st]):
                    continue                                  # already on the board
                if acc:
                    edit.append("queued", f"- [{iid}] {title} · source: {sid} · acceptance: {acc}")
                    admitted.append(iid)
                else:
                    edit.append("inbox", f"- [{iid}] {title} · source: {sid} · needs: {why}")
                    held += 1
            refused += refused_here
            if refused_here:
                # Kept to be imported again once corrected: what it admitted is on the board, and is not added twice.
                print(f"{C['yellow']}kept{C['reset']} {os.path.basename(f)}: correct its refused item(s) and run "
                      "ao source import again")
            else:
                done.append(f)
        edit.write()
    for iid in admitted:
        dg = A.plan_digest(root, iid)
        if dg:
            A.record_plan(root, iid, dg)   # baseline: the plan as admitted
    for f in done:
        os.rename(f, f + ".imported")
    print(f"{C['green']}{len(admitted)} admitted{C['reset']} to queued · "
          f"{C['yellow']}{held} held{C['reset']} in inbox (no acceptance boundary)"
          + (f" · {C['red']}{refused} refused{C['reset']}" if refused else ""))
    return 1 if refused else 0


def _source_item_problem(iid, texts):
    """Why an imported item cannot be a board line as it reads, or None: its id must be one the board reads whole and a
    needs note can name, and no text of its line may hold the '·' that separates a line's notes - what `ao board add`
    holds an item to (BOARD-ADD-2, LEADS-ADAPTERS)."""
    import re
    if not re.fullmatch(BOARD_ID, iid):
        return ("its id cannot be one: an id holds no space, comma, ']', backslash or '·' and does not start with '(' "
                "or '/', so that a board line carries it whole")
    if any("·" in text for text in texts):
        return "its source, title, boundary or reason holds a '·', which separates a board line's notes"
    return None


def cmd_hold(cfg, args):
    """Stop this project's agents and keep them stopped.

    A kill switch that only kills is not a switch: the watchdog sees an idle
    session with open work and restarts it within a couple of minutes. So the
    stop and the lock are one operation, and every restart path checks the lock
    first.

    It stops *every* agent process whose cwd is this repository, not just the one
    we most recently started. Detached turns accumulate — this project found
    fifteen live `kiro-cli` processes in one tree, four of them still burning
    CPU, because each nudge spawned one and nothing ever reaped them. Tracking
    only our own last child made the rest invisible.
    """
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    path = os.path.join(root, A.HOLD_FILE)

    if args.action == "status":
        st = A.hold_state(root)
        if not st:
            print(f"{C['green']}running free{C['reset']} — no hold")
        else:
            print(f"{C['red']}HELD{C['reset']} by {C['b']}{st.get('by')}{C['reset']} "
                  f"for {st['minutes']}m: {st.get('reason','')}")
        try:
            pids = A.agent_pids(root, adapter)
        except TableUnreadable as exc:
            # Not counted is not none (WINDOWS-TABLE-CLOSED).
            print(f"{C['yellow']}agent processes in this tree: unknown{C['reset']} — {exc}")
            return 0
        print(f"{len(pids)} agent process(es) in this tree" + (f": {pids}" if pids else ""))
        return 0

    if args.action == "release":
        if not os.path.exists(path):
            print(f"{C['dim']}No hold to release.{C['reset']}")
            return 0
        st = A.hold_state(root) or {}
        name = None
        if args.note:
            # The agent wakes into a tree it did not change. Say what moved, or it
            # spends its first turns rediscovering it — or worse, mistrusting it.
            impl, arch = A.mail_names(cfg)
            name = f"{datetime.now():%Y%m%d-%H%M}-{arch}-to-{impl}-INFO-hold-released.md"
            # Through write_mail, as every mail ao writes; it was written past it (ROLE-TABLE-2). Written while the hold
            # stands: the watchdog starts a turn as soon as it finds none, and a turn started before the note was
            # written woke without it (LEADS-ADAPTERS).
            name = A.write_mail(root, cfg, name, language.text(cfg, "hold.released", minutes=st.get("minutes", 0),
                                                        reason=st.get("reason", ""), note=args.note),
                         {"kind": "info", "class": "fyi", "from": arch, "to": impl})
        os.remove(path)
        print(f"{C['green']}released{C['reset']} after {st.get('minutes', 0)}m")
        if name:
            print(f"handover note → {cfg['mailbox']}/{name}")
        return 0

    # hold — stops unattended turns only. An interactive session has a person in
    # it who did not ask to be stopped; the lock still keeps the watchdog from
    # starting anything new.
    # The hold stands before anything is counted, and a watchdog cycle already past its own reading of it is
    # waited for: counted first, a turn a cycle started between the count and the hold read no hold and ran on
    # under it (JOURNAL-6).
    from .storage import replace_file_durably, _exclusive_lock
    from . import watchdog as W
    hold = {"by": args.by, "reason": args.reason or "manual intervention", "at": int(time.time()), "stopped": []}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    replace_file_durably(path, json.dumps(hold, indent=2).encode(UTF8))
    late = None
    try:
        with _exclusive_lock(os.path.join(W.STATE_DIR, W.CYCLE_LOCK.format(key=A.project_key(root))), timeout=60):
            pass
    except LedgerLockTimeout:
        late = "a watchdog cycle that began before this hold was still running after a minute"
    except OSError as exc:
        late = f"the watchdog's cycle lock could not be taken ({exc})"
    try:
        code = _hold_stop(root, adapter, args, path, hold)
    except TableUnreadable as exc:
        # It said no agent turn was running, where it could not see one (WINDOWS-TABLE-CLOSED).
        print(f"{C['red']}hold set, but {exc}{C['reset']}: a turn in this tree may still be running, and ao "
              "cannot see it to stop it. The hold keeps the watchdog from starting another; stop a running "
              "turn by hand, or run `ao hold` again once the table can be read")
        code = 1
    if late:
        # Counted while that cycle ran: a turn it starts after its last reading of the hold is not counted (JOURNAL-6).
        print(f"{C['yellow']}{late}{C['reset']}: the hold stands, and a cycle reads it again before it starts a "
              "turn, but a turn it started as the hold was placed may not be stopped - `ao hold status` shows "
              "it, and `ao hold` again stops it")
        return 1
    return code


def _hold_stop(root, adapter, args, path, hold):
    """Count the unattended turns in this tree, record them in the hold, stop them, and prove it (JOURNAL-6).

    What was stopped is read again, never assumed: on Windows taskkill is refused a process
    another user or an elevated prompt started, and misses a child whose parent had already
    exited, and the hold still said every tree was stopped (the 2026-09-08 audit). Each stop
    returns what of its tree still runs, the process table is read once more for unattended
    turns and what ended turns left, and whatever still runs is named and the hold exits 1
    (WINDOWS-STOP-PROVEN).
    """
    from .storage import replace_file_durably
    pids = A.agent_pids(root, adapter, headless_only=True)
    hold["stopped"] = pids
    replace_file_durably(path, json.dumps(hold, indent=2).encode(UTF8))
    dead, survived = A.orphans(root, adapter), []
    if dead:
        print(f"clearing {len(dead)} orphaned process(es) left by ended turns: {dead}")
        survived += A.sweep_orphans(dead) or []
    if not pids:
        still = _hold_still_running(root, adapter, survived) if dead else []
        if A.unplaced_agent_pids(root, adapter):
            print(f"{C['yellow']}hold set{C['reset']} — no agent turn could be placed in this tree")
            return max(_hold_unplaced(root, adapter), _hold_proven(still))
        print(f"{C['yellow']}hold set{C['reset']} — no agent turn was running")
        return _hold_proven(still)
    print(f"stopping {len(pids)} process(es): {pids}")
    for pid in pids:
        survived += A.kill_turn(pid, signal.SIGTERM) or []     # whole group; let it finish the write it is in
    deadline = time.time() + args.grace
    while time.time() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            break
        time.sleep(0.5)
    alive = [p for p in pids if _alive(p)]
    for pid in alive:
        survived += A.kill_turn(pid, getattr(signal, "SIGKILL", signal.SIGTERM)) or []
    still = _hold_still_running(root, adapter, survived)
    if os.name == "nt":
        # taskkill /T /F ends a tree at once; nothing exited on request (#71).
        print(f"{C['red']}HELD{C['reset']} — {len([p for p in pids if p not in still])} process tree(s) stopped "
              "by force (Windows). The watchdog will not restart while .ao/hold exists.")
    else:
        print(f"{C['red']}HELD{C['reset']} — {len([p for p in pids if p not in alive and p not in still])} exited "
              f"on request, {len([p for p in alive if p not in still])} killed. The watchdog will not restart while "
              ".ao/hold exists.")
    return max(_hold_unplaced(root, adapter), _hold_proven(still))


def _hold_still_running(root, adapter, survived):
    """What still runs in this tree after a hold stopped it: what a stop returned that runs yet - a later stop may
    have ended it - and the unattended turns and what ended turns left, the process table read again
    (WINDOWS-STOP-PROVEN)."""
    return sorted(set(A._still_running(survived, seconds=0)) | set(A.agent_pids(root, adapter, headless_only=True))
                  | set(A.orphans(root, adapter)))


def _hold_proven(still):
    """0 when nothing the hold stopped still runs; else name what does and what to do about it, and 1
    (WINDOWS-STOP-PROVEN)."""
    if not still:
        return 0
    print(f"{C['red']}{len(still)} process(es) still run in this tree after the hold{C['reset']}: {still}. The "
          f"hold stands and the watchdog starts nothing beside them; {_stop_by_hand()} - `ao hold status` shows "
          "what still runs")
    return 1


def _stop_by_hand():
    """What a person does about a process ao could not stop, as their platform does it (WINDOWS-STOP-PROVEN)."""
    if os.name == "nt":
        return ("stop them by hand with `taskkill /T /F /PID <pid>`, from a prompt of the user that started them, "
                "or an elevated one where access is denied")
    return "stop them by hand with `kill -9 <pid>`, as the user that started them"


def _hold_unplaced(root, adapter):
    """Say which agent processes a hold could not stop because they cannot be placed (#71)."""
    unplaced = A.unplaced_agent_pids(root, adapter)
    if not unplaced:
        return 0
    print(f"{C['red']}{len(unplaced)} agent process(es) were not stopped{C['reset']}: their working "
          f"directory could not be read, so they cannot be placed in this tree: {unplaced}. Stop them "
          "by hand if they work here; the hold keeps the watchdog from starting another.")
    return 1


def _etime(seconds):
    """Seconds as `ps -o etime` writes them: [[dd-]hh:]mm:ss, the days two digits wide where macOS's ps pads them."""
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    clock = f"{hours:02d}:{minutes:02d}:{secs:02d}" if days or hours else f"{minutes:02d}:{secs:02d}"
    return (f"{days:02d}-" if sys.platform == "darwin" else f"{days}-") + clock if days else clock


def _ps_args(argv):
    """An argument vector as `ps -o args=` writes it: joined by spaces, each control character escaped as ps does.

    A prompt handed to a turn as an argument holds newlines, and ps kept each turn on one
    line: macOS writes a tab and a newline in octal and the other controls as ^X, procps
    a question mark.
    """
    def shown(ch):
        code = ord(ch)
        if code >= 32 and code != 127:
            return ch
        if sys.platform != "darwin":
            return "?"
        return f"\\{code:03o}" if ch in "\t\n" else "^" + chr(code ^ 64)

    return " ".join("".join(shown(ch) for ch in arg) for arg in argv)


def cmd_writers(cfg, args):
    """Who is writing in this tree — turns, not processes, with orphans set aside.

    This is the measurement a single-writer rule should run. The process table
    answers a different question: one turn is a wrapper, a runtime and an engine,
    and a turn that ended can leave the last two behind at 0% CPU with the repo
    as their cwd. Counting processes reported four writers where there were
    none, and an implementer that trusted the count refused to write for three
    and a half hours. Exit status is 0 for at most one live turn, 1 otherwise;
    `--clean` stops orphans only — never a live turn, never a person's session.
    """
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    try:
        roots, dead = A.writers(root, adapter)
        unplaced = A.unplaced_agent_pids(root, adapter)
    except TableUnreadable as exc:
        # A count that could not look must not report none, nor pass a single-writer rule (WINDOWS-TABLE-CLOSED).
        if args.json:
            print(json.dumps({"writers": None, "unreadable": str(exc)}, ensure_ascii=False))
        else:
            print(f"{C['red']}writers unknown{C['reset']} — {exc}; whoever counts writers must not read "
                  "this as none: run `ao writers` again once the table can be read")
        return 1
    if unplaced:
        # A count that cannot see a writer must not report none (#71).
        print(f"{C['red']}writers unknown{C['reset']} — {len(unplaced)} agent process(es) cannot be placed "
              f"in a tree: their working directory could not be read ({unplaced})")
        return 1
    from . import procs
    table = A._proc_table()
    rows = []
    for pid in roots:
        # From the process table, as `ps -o etime=,args=` printed them; Windows has no ps at all.
        seconds = procs.elapsed(pid)
        et, cmd = ("" if seconds is None else _etime(seconds)), _ps_args(procs.argv(pid) or [])
        rows.append({"pid": pid, "elapsed": et, "headless": A._is_headless(pid),
                     "tty": table.get(pid, (0, 0, "?"))[2], "cmd": cmd.strip()[:90]})
    if args.clean and dead:
        # What the sweep could not stop is what it returns, each process asked (WINDOWS-STOP-PROVEN).
        left = A.sweep_orphans(dead) or []
        cleaned = [p for p in dead if p not in left]
    else:
        cleaned, left = [], dead
    # Windows has no process groups to tell an ended turn's leftovers by: none set aside there is not none, and the
    # count may hold them (WINDOWS-SAY-WHY).
    blind = os.name == "nt"
    if args.json:
        told = {"writers": len(roots), "turns": rows, "orphans": None if blind else left, "cleaned": cleaned}
        if blind:
            told["orphans_unknown"] = WINDOWS_LIMITS["orphans"]
        print(json.dumps(told, ensure_ascii=False))
        return 0 if len(roots) <= 1 else 1
    if not roots:
        print(f"{C['green']}0 writers{C['reset']} — no live turn in this tree")
    else:
        colour = C['green'] if len(roots) == 1 else C['red']
        print(f"{colour}{len(roots)} writer(s){C['reset']}")
        for r in rows:
            kind = "headless" if r["headless"] else f"interactive tty={r['tty']}"
            print(f"   {r['pid']:>6}  {r['elapsed']:>10}  {kind:<22} {r['cmd']}")
    if cleaned:
        print(f"   cleaned {len(cleaned)} orphan(s): {cleaned}")
    if left and args.clean:
        print(f"   {C['red']}{len(left)} process(es){C['reset']} left by ended turns still run after they were "
              f"stopped, not counted: {left}; {_stop_by_hand()}")
    elif left:
        print(f"   {C['yellow']}{len(left)} orphan(s){C['reset']} left by ended turns, not counted: {left}"
              "  (`ao writers --clean` stops them)")
    if blind:
        print(f"   {C['yellow']}orphans not told apart{C['reset']}: {WINDOWS_LIMITS['orphans']}")
    return 0 if len(roots) <= 1 else 1


def cmd_fanout(cfg, args):
    """May a fan-out of N sub-agents start now — and what did the last one cost.

    `ok` answers from three facts (hard cap, recent limit hit, provider window);
    `record` writes what a run cost so the next estimate is empirical; `history`
    lists the runs. Exit 1 on any refusal, so a coordinator can gate on it.
    """
    root = cfg["root"]
    if args.action == "record":
        if args.agents is None:
            print("--agents is required"); return 2
        rec = A.record_fanout(root, args.agents, args.done, args.errors, args.tokens, args.note)
        print(f"{C['green']}recorded{C['reset']} {json.dumps(rec, ensure_ascii=False)}")
        return 0
    if args.action == "history":
        rows = A.fanout_history(root, args.limit)
        if not rows:
            print("no fan-outs recorded"); return 0
        for r in rows:
            when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
            flag = f"  {C['red']}limit hit{C['reset']}" if r.get("limit_hit") else ""
            print(f"  {when}  {r['agents']:>3} agents  done {r.get('done','?'):>3}  "
                  f"errors {r.get('errors','?'):>3}  tokens {r.get('tokens','?')}{flag}"
                  + (f"  — {r['note']}" if r.get('note') else ""))
        return 0
    agents = args.agents
    if args.roots and args.per_root:
        # A pipeline's fan-out is not known up front: 7 roots that each may
        # spawn up to 12 branches is a bound of 7 + 7×12. Gate on the bound.
        agents = args.roots + args.roots * args.per_root
        print(f"{C['dim']}pipeline bound: {args.roots} roots + {args.roots}×{args.per_root} branches = {agents} agents{C['reset']}")
    if agents is None:
        print("--agents N, or --roots R --per-root K for a pipeline, is required"); return 2
    v = A.fanout_verdict(root, cfg, agents, args.per_agent_tokens, args.provider)
    if args.json:
        print(json.dumps(v, ensure_ascii=False))
        return 0 if v["ok"] else 1
    colour = C['green'] if v["ok"] else C['red']
    print(f"{colour}{v['verdict'].upper()}{C['reset']}  {v['agents']} agents × ~{v['per_agent_tokens']:,} "
          f"tokens ({v['per_agent_source']}) ≈ {v['estimated_tokens']:,} tokens")
    w = v.get("window")
    if w:
        # The provider whose window was read, not the argument: without --provider that named None (HARNESS-ACCOUNTS-2).
        print(f"   {v['provider']} window: {w['pct']}% used, {w['window']} window, resets in {w['resets_in']}"
              + (f"; {v['spent_this_window']:,} tokens fanned out in it so far" if v['spent_this_window'] else ""))
    for r in v["reasons"]:
        print(f"   {C['yellow'] if not v['ok'] else C['dim']}· {r}{C['reset']}")
    if v["ok"]:
        print(f"   {C['dim']}after the run: ao fanout record --agents {v['agents']} --done D --errors E --tokens T{C['reset']}")
    return 0 if v["ok"] else 1


def cmd_email(cfg, args):
    """The red channel: e-mail through a provider - formsubmit.co, or any SMTP server (#74)."""
    from . import email
    provider = getattr(args, "provider", None) or "formsubmit"
    if args.action == "setup" and provider != "formsubmit":
        password = None
        if getattr(args, "password_env", None):
            # Read once from the environment: a password on the command line stays in history.
            password = os.environ.get(args.password_env)
            if password is None:
                print(f"{C['red']}not saved{C['reset']}: ${args.password_env} is not set"); return 2
        elif args.user:
            # An account's password is asked at the terminal, not shown: the setup text had a person read it into a
            # variable first, a line that swallowed the next one pasted with it as the password, and left the
            # variable set when it was cut short (EMAIL-SETUP-PROMPT).
            if not sys.stdin.isatty():
                print(f"{C['red']}not saved{C['reset']}: {args.user}'s password is asked at a terminal, and none is "
                      "attached; --password-env names a variable that holds it")
                return 2
            password = _hidden_input(f"password for {args.user} on {args.host} (not shown): ")
            if password is None:
                print(f"{C['red']}not saved{C['reset']}: this terminal cannot hide what is typed, and a password "
                      "typed here would stand in its scrollback; --password-env names a variable that holds it")
                return 2
            if not password:
                print(f"{C['red']}not saved{C['reset']}: no password was given"); return 2
        try:
            c = email.save_provider(provider, host=args.host, port=args.port, user=args.user,
                                    password=password, to=args.to, tls=args.tls,
                                    **{"from": args.sender})
        except ValueError as exc:
            print(f"{C['red']}not saved{C['reset']}: {exc}"); return 2
        print(f"{C['green']}saved{C['reset']} {email.CONF} ({_private_mode()}) provider={provider} → {c.get('to')}")
        print(f"now: {C['b']}ao email test{C['reset']}")
        return 0
    if args.action == "setup":
        if args.token:
            c = email.save(args.token, to=args.to)
            print(f"{C['green']}saved{C['reset']} {email.CONF} ({_private_mode()}) → "
                  f"{c.get('to') or 'address hidden behind token'}")
            print(f"now: {C['b']}ao email test{C['reset']}")
            return 0
        # The channel is the machine's, so its steps are in the machine's language (LANGUAGE-OUTPUT); how its file is
        # kept private is the platform's (WINDOWS-SAY-WHY).
        print(language.text(None, "email.setup", conf=email.CONF,
                            kept=language.text(None, "private-file.kept-windows" if os.name == "nt"
                                               else "private-file.kept")))
        return 0
    c = email.config()
    if args.action == "status":
        if not c:
            print(f"{C['yellow']}not configured{C['reset']} — ao email setup"); return 1
        print(f"{C['green']}configured{C['reset']} provider={c['provider']} to={c.get('to') or '(hidden)'} file={email.CONF}")
        return 0
    if args.action == "test":
        if not c:
            print(f"{C['red']}not configured{C['reset']} — ao email setup"); return 1
        ok = email.send("test", language.text(cfg, "email.test", root=cfg["root"]), cfg["root"])
        print(f"{C['green']}sent{C['reset']}" if ok else f"{C['red']}relay refused{C['reset']} — token/activation?")
        return 0 if ok else 1
    return 0


def cmd_alarms(cfg, args):
    """Live alarm episodes and their level; `test` rings every channel."""
    root = cfg["root"]
    project = A.project_key(root)
    if args.action in ("snooze", "unsnooze"):
        key = getattr(args, "key", None)
        if not key:
            print(f"usage: ao alarms {args.action} <key>"
                  + (" --until <date or span> --why '…'" if args.action == "snooze" else ""))
            return 2
        if args.action == "unsnooze":
            gone = A.alarm_unsnooze(project, key)
            print(f"{'unsnoozed' if gone else 'no snooze for'} {key}")
            return 0
        try:
            # A date, or a span counted forward: a snooze of 3d ends three days from now (CLI-ROBUST).
            until = A.parse_time(getattr(args, "until", None) or "").moment(ahead=True)
        except ValueError:
            print("snooze needs --until: a date such as 2026-10-01, or a span from now such as 3d")
            return 2
        if until <= time.time():
            print("--until must be a date in the future")
            return 2
        why = (getattr(args, "why", None) or "").strip()
        if not why:
            print("snooze needs --why: a snoozed alarm says why nobody can act on it yet")
            return 2
        A.alarm_snooze(project, key, until, by=getattr(args, "by", None) or "human", why=why)
        print(f"snoozed {key} until {time.strftime('%d %b %Y', time.localtime(until))} — "
              "it stays in ao alarms and rings again from that date")
        return 0
    if args.action == "test":
        from .watchdog import notify
        lvl = args.level or "orange"
        # Worded in the project's language, keyed and routed the same in any (LANGUAGE-OUTPUT).
        ok = notify(language.text(cfg, "alarm.test-title", project=project),
                    language.text(cfg, "alarm.test", level=lvl), root,
                    key=f"alarm-test-{int(time.time())}", window=0, audience="human", level=lvl)
        print(f"{lvl}: desktop+telegram {'sent' if ok else 'suppressed'}"
              + (" · e-mail attempted (see ao notices)" if lvl == "red" else ""))
        return 0
    alive = A.active_alarms(project)
    for full, snooze in sorted(A.load_alarm_snoozes().items()):
        owner, _, key = full.partition(":")
        if owner == project and A.alarm_snoozed(project, key):
            print(f"  {C['dim']}snoozed{C['reset']} {key:<32} until "
                  f"{datetime.fromtimestamp(snooze['until']).strftime('%d %b')}  "
                  f"{C['dim']}by {snooze.get('by')}: {(snooze.get('why') or '')[:60]}{C['reset']}")
    if not alive:
        print(f"{C['green']}no live alarms{C['reset']}"); return 0
    for e in alive:
        tone = {"red": C['red'], "orange": C['yellow']}.get(e.get("ring"), C['dim'])
        since = datetime.fromtimestamp(e["first"]).strftime("%d %b %H:%M")
        print(f"  {tone}{e.get('ring', '?'):<7}{C['reset']} {e['key']:<32} since {since}  "
              f"×{e.get('count', 1)}  {C['dim']}{e.get('title', '')[:50]}{C['reset']}"
              + (f"  mailed {datetime.fromtimestamp(e['red_sent']).strftime('%H:%M')}" if e.get('red_sent') else ""))
        if e.get("evidence"):
            for line in A.evidence_lines(e["evidence"]):
                print(f"          {C['dim']}{line}{C['reset']}")
    return 0


def _watchdog_debug(cfg, args):
    """`ao watchdog explain` runs one dry cycle and shows every measurement and
    verdict; `ao watchdog trace` shows the recorded cycles. The question both
    answer is the one that cost the most this project: why did it not act?"""
    from types import SimpleNamespace
    from . import watchdog as W
    root = cfg["root"]
    if args.action == "explain":
        ns = SimpleNamespace(root=root, idle_minutes=S.get(cfg, "watchdog.idle_minutes"), dry_run=True)
        W.run(ns)
        print(f"\n{C['b']}{C['mag']}── MEASUREMENTS ──{C['reset']}")
        for k, v in W._FACTS.items():
            print(f"   {k:<22} {v}")
        print(f"\n{C['b']}{C['mag']}── DECISIONS (in order) ──{C['reset']}")
        for i, line in enumerate(W._TRACE, 1):
            last = i == len(W._TRACE)
            print(f"   {C['b'] if last else ''}{i:>2}. {line}{C['reset']}")
        return 0
    rows = W.cycles(root, args.last or 20)
    if not rows:
        print("no cycles recorded yet"); return 0
    for r in rows:
        when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M:%S")
        f = r.get("facts", {})
        print(f"  {when}  {C['b']}{(r.get('verdict') or '')[:70]:<70}{C['reset']}  "
              f"{C['dim']}idle {f.get('idle_s', '?')}s · writers {f.get('writers', '?')} · inbox {f.get('inbox', '?')} "
              f"· queued {f.get('queued', '?')}{' · standing request' if f.get('standing_request') else ''}{C['reset']}")
    return 0


def _print_seen_own(paths):
    """Say which files ao wrote into what a harness reads and recorded as seen, as they stand (HARNESS-SEEN-OWN)."""
    if paths:
        print(f"  {C['green']}{'seen':<8}{C['reset']} {', '.join(A.shown_text(path) for path in paths)}  "
              f"{C['dim']}ao's own text holds no turn; a change made to it later does, until a person sees it"
              f"{C['reset']}")


def cmd_skill(cfg, args):
    """The playbook, rendered for the agents this repository uses.

    What it writes as a file of ao's own is recorded as seen, so the turns HARNESS-CONFIG holds on harness configuration
    nobody has seen start on it (HARNESS-SEEN-OWN).
    """
    from . import skillkit
    root = cfg["root"]
    if args.action == "show":
        print(skillkit.playbook()[1])
        return 0
    _, agents = skillkit.detect_agents(root, args.agent)
    ours, refused = [], []
    # Written as init writes it, never through a link: what was not is named, and the command exits 1 (LEADS-ADAPTERS).
    _print_playbook_files(skillkit.install_playbook(root, agents, ours=ours, unwritten=refused))
    _print_seen_own(A.record_harness_written(root, ours, "ao skill install"))
    print(f"{C['dim']}agents: {', '.join(sorted(agents))} · regenerate any time; hand edits outside the markers survive{C['reset']}")
    if refused:
        print(f"{C['yellow']}{len(refused)} file(s) were not written, each named above with why: make each a file or "
              f"folder of the project's own, then run ao skill install again{C['reset']}")
        return 1
    return 0
