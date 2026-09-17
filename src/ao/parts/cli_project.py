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
    win = ("24 saat" if args.days == 1 else
           f"{int(args.days)} gün" if args.days == int(args.days) else f"{args.days} gün")

    # Lead with the blocking answer. Someone opening this at 3am wants "what is
    # in the way", not a scoreboard.
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    state, age, doing = A.busy(cfg, adapter) if impl else ("unknown", None, "")
    spin = A.spinning(root)
    print(f"{C['b']}{cfg.get('project') or os.path.basename(root)}{C['reset']}"
          f"{C['dim']}  son {win}{C['reset']}\n")

    col = {"working": C["green"], "slowing": C["yellow"]}.get(state, C["red"])
    line = f"{col}{state}{C['reset']}"
    if age is not None:
        line += f"{C['dim']}, son yazım {age // 60}dk önce{C['reset']}"
    if spin:
        line += f"  {C['red']}⚠ {spin}dk meşgul, üretim yok{C['reset']}"
    print(f"  durum    {line}")
    if doing:
        print(f"  {C['dim']}↳ {doing[:110]}{C['reset']}")

    if d["decisions"]["open"]:
        print(f"\n  {C['yellow']}{d['decisions']['open']} cevap bekleyen karar{C['reset']}"
              f"{C['dim']} — bunlar işi açar: ao decisions{C['reset']}")
    for b in d["blocked"]:
        print(f"  {C['red']}⊘{C['reset']} {b['id']}  {C['dim']}{b['needs'][:88]}{C['reset']}")

    print(f"\n{C['b']}{C['mag']}── İNEN İŞ {'─' * 46}{C['reset']}")
    print(f"  {C['b']}{len(d['commits'])}{C['reset']} commit"
          f"{C['dim']}, {d['unpushed']} push'suz{C['reset']}")
    for c in d["commits"][:args.n]:
        print(f"    {C['dim']}{c['sha']}{C['reset']} {c['subject'][:76]}")

    v, a, r = d["verifications"], d["authority"], d["reviews"]
    print(f"\n{C['b']}{C['mag']}── KAPILAR {'─' * 46}{C['reset']}")
    vf = f", {C['red']}{v['failed']} düştü{C['reset']}" if v["failed"] else ""
    rc = f", {C['yellow']}{r['changes']} değişiklik{C['reset']}" if r["changes"] else ""
    ar = f", {C['yellow']}{a['refused']} reddedildi{C['reset']}" if a["refused"] else ""
    print(f"  doğrulama  {C['green']}{v['passed']} geçti{C['reset']}{vf}")
    print(f"  review     {C['green']}{r['approved']} APPROVED{C['reset']}{rc}")
    if a.get("integrity") == "broken":
        print(f"  commit-ok  {C['red']}YETKİ DEFTERİ BÜTÜNLÜĞÜ BOZUK{C['reset']}")
        if a.get("error"):
            print(f"    {C['dim']}{a['error'][:110]}{C['reset']}")
    else:
        print(f"  commit-ok  {C['green']}{a['granted']} verildi{C['reset']}{ar}")
    # A refusal repeated all week is a process problem, not an incident.
    for reason, n in d["refusal_reasons"]:
        if n > 1:
            print(f"    {C['dim']}{n}× {reason}{C['reset']}")

    dec = d["decisions"]
    if dec["asked"]:
        med = f"{dec['median_minutes']}dk" if dec["median_minutes"] is not None else "—"
        print(f"\n  karar      {dec['answered']}/{dec['asked']} cevaplandı"
              f"{C['dim']}, ortanca {med}{C['reset']}")

    account = d.get("account") or {}
    if d.get("credits"):
        c = d["credits"]
        pct = c["used"] / c["limit"] * 100 if c["limit"] else 0
        cc = C["red"] if pct > 90 else C["yellow"] if pct > 70 else C["green"]
        print(f"\n{C['b']}{C['mag']}── KREDİ {'─' * 48}{C['reset']}")
        print(f"  {cc}{c['used']:,.0f}{C['reset']} / {c['limit']:,.0f}"
              f"{C['dim']}  ({c['remaining']:,.0f} kaldı){C['reset']}")
    elif account and not account.get("readable"):
        # Where the figure stood, the implementer's adapter declares no account (ACCOUNT-READERS).
        print(f"\n{C['b']}{C['mag']}── KREDİ {'─' * 48}{C['reset']}")
        print(f"  {C['dim']}{_no_account(account.get('adapter'))}{C['reset']}")
    for day, val in d["credit_days"][-args.n:]:
        print(f"    {C['dim']}{day}{C['reset']}  {val:>8,.0f}")

    al = d["alerts"]
    print(f"\n{C['dim']}pano: " +
          " · ".join(f"{k} {n}" for k, n in d["board"].items()) +
          f"  |  uyarı: {al['sent']} gönderildi, {al['held']} susturuldu{C['reset']}")
    return 0


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


def _detect_gates(root):
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
        if shutil.which("ruff"):
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
        cfg["architect"] = {"adapter": architect, "session": "auto", "cwd": root, "name": "fable",
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


def cmd_init(cfg, args):
    """Put `ao` on a project. Idempotent: existing files are left alone.

    This is the answer to "how do I apply this to another project". Everything
    it writes is a file the agent already knows how to read, so a project without
    MCP or a watchdog gets the whole protocol from the files alone; the optional
    flags add the automation on top.
    """
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
    detected_gates = _detect_gates(root)
    if not os.path.lexists(os.path.join(root, ".ao", "gates.json")):
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
    runtime_cfg = _planned_runtime_config(root, planned_config)
    if opt_in:
        runtime_cfg["_same_family_opt_in"] = opt_in
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

    wrote, kept = [], []

    def put(rel, content, mode=None):
        p = os.path.join(root, rel)
        if os.path.lexists(p):
            kept.append(rel)
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        # The bytes as given on every platform: Windows text mode wrote the marker with
        # CRLF, and the marker check below refused the init that had just written it (#71).
        with open(p, "w", encoding=UTF8, newline="\n") as fh:
            fh.write(content)
        if mode:
            os.chmod(p, mode)
        wrote.append(rel)

    # The config is replaced whole or not at all (#56); a crash never leaves it
    # empty of the opt-ins it held.
    if config_existed:
        kept.append(".ao/config.json")
        if added:
            A.write_project_config(root, config_text)
            wrote.append(".ao/config.json (+" + ", ".join(added) + ")")
    else:
        if os.path.lexists(config_path):
            kept.append(".ao/config.json")
        else:
            A.write_project_config(root, config_text)
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
    put(".ao/gates.json", json.dumps(detected_gates, indent=2) + "\n")
    put(".ao/ledger/.gitkeep", "")
    put(".ao/decisions/.gitkeep", "")
    put("semantic-review/.gitkeep", "")
    put("agent-mail/README.md", language.text(runtime_cfg, "init.mail-readme"))

    gi = os.path.join(root, ".gitignore")
    lines = open(gi, encoding=UTF8).read().split("\n") if os.path.exists(gi) else []
    # The sessions ao settles for this checkout's roles are this machine's, never the repository's (SESSION-IDENTITY).
    add = [l for l in ("agent-mail/*.md", "!agent-mail/README.md", ".ao/inbox/", ".ao/hold", ".ao/sessions.json")
           if l not in lines]
    if add:
        with open(gi, "a", encoding=UTF8) as fh:
            fh.write("\n# agent-orchestrator: mail is transient; the ledger and reviews are not\n"
                     + "\n".join(add) + "\n")
        wrote.append(".gitignore (+%d)" % len(add))

    # Steering for whichever agent this repo uses. Kiro reads .kiro/steering;
    # Claude Code and most others read CLAUDE.md / AGENTS.md.
    # Steering goes only into files ao owns. The owner's rule files (CLAUDE.md,
    # AGENTS.md) are never appended to unless they ask with --rules: a tool that
    # writes instructions there has, from the reading agent's side, issued rules
    # nobody authorised — the second pilot's coordinator refused exactly that.
    from . import skillkit
    requested = skillkit.detect_agents(root, args.agent)[0]
    for ident, adapter in skillkit.setup_adapters(root):
        coordination = (adapter.get("directives") or {}).get("coordination")
        present = any(os.path.isdir(os.path.join(root, *path.split("/")))
                      for path in (adapter.get("detect") or {}).get("dirs") or [])
        if coordination and (ident == requested or present):
            put(coordination, STEERING_COORD.format(urgent=language.marker(runtime_cfg, "urgent")))

    for rel in wrote:
        print(f"  {C['green']}wrote{C['reset']}  {rel}")
    for rel in kept:
        print(f"  {C['dim']}kept   {rel}{C['reset']}")

    exe = shutil.which("ao") or os.path.abspath(sys.argv[0])
    # The playbook and the MCP registration are the two things an agent needs to
    # behave like the architect from its first turn; both are written for every
    # agent this repository is seen to use. Ask the human before running init;
    # the tool itself does not ask.
    from . import skillkit
    _, agents = skillkit.detect_agents(root, args.agent)
    playbook_files = skillkit.install_playbook(root, agents, rules=getattr(args, "rules", False))
    for rel, what in playbook_files.items():
        print(f"  {C['green'] if what != 'kept' else C['dim']}{what:<8}{C['reset']} {rel}")
    if not getattr(args, "rules", False):
        print(f"  {C['dim']}rule files untouched — paste this into {' / '.join(skillkit.rule_file_names(root))} "
              f"yourself, or re-run with --rules:{C['reset']}")
        for line in skillkit.RULE_POINTER.split("\n"):
            print(f"      {line}")
    registered = {} if args.no_mcp else skillkit.register_mcp(root, agents, exe)
    # What init wrote, so `ao remove` can take exactly that away again.
    manifest = {"at": int(time.time()), "wrote": list(wrote) + [r for r, w in playbook_files.items() if w != "kept"],
                "mcp": [k for k, v in registered.items() if v == "registered"], "gitignore": bool(add)}
    with open(os.path.join(root, ".ao", "init-manifest.json"), "w", encoding=UTF8) as fh:
        json.dump(manifest, fh, indent=2)
    for agent, what in registered.items():
        print(f"  {C['green']}mcp{C['reset']}      {agent}: {what.splitlines()[0]}")
        if what.startswith("manual"):
            print("           " + "\n           ".join(what.splitlines()[1:]))
    if args.watchdog:
        code = subprocess.run([exe, "-C", root, "watchdog", "install"],
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
    return 0



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
    body = f"{args.decision}\n\n**Neden:** {args.why or '—'}"
    if args.scope:
        body += f"\n\n**Kapsam:** {args.scope}"
    body += f"\n\n_karar kaydı: {rec['id']}_"
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


def cmd_board(cfg, args):
    """Where every pre-authorised item is, blocked ones first.

    Order is deliberate: `running` and `blocked` are the two states a human can
    act on, and `blocked` is the one that goes unnoticed — work carried on past
    it, so nothing else in the panel looks wrong.
    """
    root = cfg["root"]
    for line in _mailbox_banner(cfg):
        print(line)
    b = A.board(root)
    path = os.path.join(root, ".ao", "board.md")
    if not os.path.exists(path):
        print(f"{C['yellow']}No board here.{C['reset']} Create {C['b']}.ao/board.md{C['reset']} with "
              f"`## running` / `## blocked` / `## queued` / `## verified` / `## done` sections\n"
              f"and one `- [ID] title · note: value` line per item.")
        return 0
    colours = {"running": C["green"], "blocked": C["red"], "queued": C["dim"],
               "verified": C["cyan"], "done": C["dim"]}
    # Eligible work first: queued items whose `needs:` are all done. This is the
    # dependency graph answering "what is next" without the implementer choosing
    # its own scope. A broken edge is named before anything else (#33).
    graph = A.board_graph(root)
    if getattr(args, "view", None) == "ready":
        for problem in graph["problems"]:
            print(f"{C['red']}board: {problem}{C['reset']}")
        for it in graph["ready"]:
            print(f"{it['id']}  {it['title']}")
        return 1 if graph["problems"] else 0
    for problem in graph["problems"]:
        print(f"{C['red']}{C['b']}BOARD{C['reset']}  {C['red']}{problem}{C['reset']}")
    rd = graph["ready"]
    if rd:
        print(f"\n{C['b']}{C['green']}READY{C['reset']} {C['dim']}({len(rd)}) — "
              f"dependencies satisfied{C['reset']}")
        for it in rd:
            role = f"  {C['cyan']}role:{it['role']}{C['reset']}" if it.get("role") else ""
            print(f"   {C['b']}{it['id']}{C['reset']}  {it['title']}{role}")
    for st in ("running", "blocked", "queued", "verified", "done"):
        items = b[st]
        if not items:
            continue
        print(f"\n{C['b']}{colours[st]}{st.upper()}{C['reset']} {C['dim']}({len(items)}){C['reset']}")
        for it in items:
            notes = "  ".join(f"{C['dim']}{k}:{C['reset']} {v}" if v else f"{C['dim']}{k}{C['reset']}"
                              for k, v in it["notes"].items())
            print(f"   {C['b']}{it['id']}{C['reset']}  {it['title']}" + (f"   {notes}" if notes else ""))
            if st in ("running", "queued"):
                # Named when the item is registered, while widening or splitting is cheap (#35).
                for advice in A.boundary_advice(root, cfg, it):
                    print(f"      {C['yellow']}boundary: {advice}{C['reset']}")
    if not any(b.values()):
        print(f"{C['dim']}Board is empty.{C['reset']}")
    return 0


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
    # "an agent may work on it unattended"
    if not files:
        print(f"{C['dim']}Nothing in .ao/inbox/ to import.{C['reset']}")
        return 0
    admitted = held = 0
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
        sid = doc.get("source", "?")
        for it in doc.get("items", []):
            iid, title = it.get("id"), it.get("title", "")
            if not iid:
                continue
            if any(x["id"] == iid for st in A.BOARD_STATES for x in A.board(root)[st]):
                continue                                  # already on the board
            acc = (it.get("acceptance") or "").strip()
            if acc:
                A.board_append(root, "queued",
                               f"- [{iid}] {title} · source: {sid} · acceptance: {acc}")
                dg = A.plan_digest(root, iid)
                if dg:
                    A.record_plan(root, iid, dg)   # baseline: the plan as admitted
                admitted += 1
            else:
                why = it.get("shape") or "no acceptance boundary written"
                A.board_append(root, "inbox",
                               f"- [{iid}] {title} · source: {sid} · needs: {why}")
                held += 1
        os.rename(f, f + ".imported")
    print(f"{C['green']}{admitted} admitted{C['reset']} to queued · "
          f"{C['yellow']}{held} held{C['reset']} in inbox (no acceptance boundary)")
    return 0


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
        pids = A.agent_pids(root, adapter)
        print(f"{len(pids)} agent process(es) in this tree" + (f": {pids}" if pids else ""))
        return 0

    if args.action == "release":
        if not os.path.exists(path):
            print(f"{C['dim']}No hold to release.{C['reset']}")
            return 0
        st = A.hold_state(root) or {}
        os.remove(path)
        print(f"{C['green']}released{C['reset']} after {st.get('minutes', 0)}m")
        if args.note:
            # The agent wakes into a tree it did not change. Say what moved, or it
            # spends its first turns rediscovering it — or worse, mistrusting it.
            box = os.path.join(root, cfg["mailbox"])
            os.makedirs(box, exist_ok=True)
            impl, arch = A.mail_names(cfg)
            name = f"{datetime.now():%Y%m%d-%H%M}-{arch}-to-{impl}-INFO-hold-released.md"
            with open(os.path.join(box, name), "w", encoding=UTF8) as fh:
                fh.write(f"# INFO — hold released\n\nDuruldu: {st.get('minutes',0)} dakika\n"
                         f"Sebep: {st.get('reason','')}\n\n## Bu sürede ne değişti\n\n"
                         f"{args.note}\n")
            print(f"handover note → {cfg['mailbox']}/{name}")
        return 0

    # hold — stops unattended turns only. An interactive session has a person in
    # it who did not ask to be stopped; the lock still keeps the watchdog from
    # starting anything new.
    pids = A.agent_pids(root, adapter, headless_only=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"by": args.by, "reason": args.reason or "manual intervention",
               "at": int(time.time()), "stopped": pids},
              open(path, "w", encoding=UTF8), indent=2)
    if not pids:
        dead = A.orphans(root, adapter)
        if dead:
            print(f"clearing {len(dead)} orphaned process(es) left by ended turns: {dead}")
            A.sweep_orphans(dead)
        if A.unplaced_agent_pids(root, adapter):
            print(f"{C['yellow']}hold set{C['reset']} — no agent turn could be placed in this tree")
            return _hold_unplaced(root, adapter)
        print(f"{C['yellow']}hold set{C['reset']} — no agent turn was running")
        return 0
    dead = A.orphans(root, adapter)
    if dead:
        print(f"clearing {len(dead)} orphaned process(es) left by ended turns: {dead}")
        A.sweep_orphans(dead)
    print(f"stopping {len(pids)} process(es): {pids}")
    for pid in pids:
        A.kill_turn(pid, signal.SIGTERM)     # whole group; let it finish the write it is in
    deadline = time.time() + args.grace
    while time.time() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            break
        time.sleep(0.5)
    alive = [p for p in pids if _alive(p)]
    for pid in alive:
        A.kill_turn(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    if os.name == "nt":
        # taskkill /T /F ends a tree at once; nothing exited on request (#71).
        print(f"{C['red']}HELD{C['reset']} — {len(pids)} process tree(s) stopped by force "
              "(Windows). The watchdog will not restart while .ao/hold exists.")
    else:
        print(f"{C['red']}HELD{C['reset']} — {len(pids) - len(alive)} exited on request, "
              f"{len(alive)} killed. The watchdog will not restart while .ao/hold exists.")
    return _hold_unplaced(root, adapter)


def _hold_unplaced(root, adapter):
    """Say which agent processes a hold could not stop because they cannot be placed (#71)."""
    unplaced = A.unplaced_agent_pids(root, adapter)
    if not unplaced:
        return 0
    print(f"{C['red']}{len(unplaced)} agent process(es) were not stopped{C['reset']}: Windows exposes no "
          f"process working directory, so they cannot be placed in this tree: {unplaced}. Stop them "
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
    roots, dead = A.writers(root, adapter)
    unplaced = A.unplaced_agent_pids(root, adapter)
    if unplaced:
        # A count that cannot see a writer must not report none (#71).
        print(f"{C['red']}writers unknown{C['reset']} — {len(unplaced)} agent process(es) cannot be placed "
              f"in a tree: Windows exposes no process working directory ({unplaced})")
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
        A.sweep_orphans(dead)
        left = [p for p in dead if A._pid_alive(p)]
        cleaned = [p for p in dead if p not in left]
    else:
        cleaned, left = [], dead
    if args.json:
        print(json.dumps({"writers": len(roots), "turns": rows, "orphans": left,
                          "cleaned": cleaned}, ensure_ascii=False))
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
    if left:
        print(f"   {C['yellow']}{len(left)} orphan(s){C['reset']} left by ended turns, not counted: {left}"
              + ("" if args.clean else "  (`ao writers --clean` stops them)"))
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
        print(f"   {args.provider} window: {w['pct']}% used, {w['window']} window, resets in {w['resets_in']}"
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
        try:
            c = email.save_provider(provider, host=args.host, port=args.port, user=args.user,
                                    password=password, to=args.to, tls=args.tls,
                                    **{"from": args.sender})
        except ValueError as exc:
            print(f"{C['red']}not saved{C['reset']}: {exc}"); return 2
        print(f"{C['green']}saved{C['reset']} {email.CONF} (0600) provider={provider} → {c.get('to')}")
        print(f"now: {C['b']}ao email test{C['reset']}")
        return 0
    if args.action == "setup":
        if args.token:
            c = email.save(args.token, to=args.to)
            print(f"{C['green']}saved{C['reset']} {email.CONF} (0600) → {c.get('to') or 'address hidden behind token'}")
            print(f"now: {C['b']}ao email test{C['reset']}")
            return 0
        print(email.SETUP.format(conf=email.CONF))
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
        ok = email.send("test", f"ao e-posta kanalı çalışıyor. Proje: {cfg['root']}\n"
                        f"Kırmızı alarmlar buraya gelir: bir saatten uzun süren turuncu durumlar, "
                        f"tükenmiş kota, başarısız mimar uyandırma.", cfg["root"])
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
        ok = notify(f"{project}: alarm testi", f"{lvl} seviyesi testi — ao alarms test", root,
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


def cmd_skill(cfg, args):
    """The playbook, rendered for the agents this repository uses."""
    from . import skillkit
    root = cfg["root"]
    if args.action == "show":
        print(skillkit.playbook()[1])
        return 0
    _, agents = skillkit.detect_agents(root, args.agent)
    for rel, what in skillkit.install_playbook(root, agents).items():
        print(f"  {C['green'] if what != 'kept' else C['dim']}{what:<8}{C['reset']} {rel}")
    print(f"{C['dim']}agents: {', '.join(sorted(agents))} · regenerate any time; hand edits outside the markers survive{C['reset']}")
    return 0
