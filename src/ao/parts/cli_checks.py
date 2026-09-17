"""Checks: doctor problems, costs, settings, features, waivers, catch-up, pings.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


def _actor_grant_problems(cfg):
    """Doctor findings for each configured actor whose tool grant admits a bypass (#58)."""
    from . import allowlist as AL
    grants = []
    impl = cfg.get("implementer") or {}
    if impl.get("adapter"):
        adapter = A.load_adapter(impl["adapter"])
        grants.append(("implementer", impl["adapter"],
                       (adapter.get("resume") or {}).get("argv") or [], adapter.get("options") or {}))
    for role in ("architect", "reviewer"):
        actor = cfg.get(role) or {}
        if actor.get("argv"):
            grants.append((role, actor.get("id") or actor.get("name") or role, actor["argv"], {}))
    for fallback in (cfg.get("reviewer") or {}).get("fallbacks") or []:
        if fallback.get("argv"):
            grants.append(("reviewer fallback", fallback.get("id") or "fallback", fallback["argv"], {}))
    out = []
    for role, name, argv, options in grants:
        text = AL.describe(role, name, AL.problems(argv, options, role=role))
        if text:
            out.append((f"actor-grant:{role.replace(' ', '-')}", text))
        # A reviewer reads, and starts no MCP server it was not given (#24).
        if role.startswith("reviewer"):
            reach = AL.reviewer_problems(argv)
            if reach:
                out.append((f"reviewer-tools:{name}", f"{role} ({name}): " + "; ".join(reach)))
    return out


def _implementer_commit_guard(cfg):
    """Which guard holds commits for the configured implementer, in one sentence (#109)."""
    from . import allowlist as AL
    impl = cfg.get("implementer") or {}
    if not impl.get("adapter"):
        return None
    adapter = A.load_adapter(impl["adapter"])
    argv = (adapter.get("resume") or {}).get("argv") or []
    if AL.problems(argv, adapter.get("options") or {}):
        return (f"{impl['adapter']} can commit around the hook; an ungranted commit is not "
                "prevented but surfaces within one watchdog cycle (landed-tree check)")
    return f"{impl['adapter']}'s grant admits no hook bypass; ao commit and the hook hold commits"


def doctor_problems(cfg):
    """What `ao doctor --check` acts on: conditions a person must fix, as (key, text)."""
    from .watchdog import wake_error, STATE_DIR
    root = cfg["root"]
    key = A.project_key(root)
    out = []
    strict_matrix = M.is_strict(cfg)
    if strict_matrix:
        try:
            matrix_resolution = M.resolve(cfg, require_independent=False)
        except M.MatrixError as exc:
            out.append(("capability-matrix", "; ".join(exc.problems[:3])))
        else:
            if not any(route["eligible"] for route in matrix_resolution["reviewers"]):
                out.append((
                    "no-independent-reviewer",
                    "capability matrix reviewer chain has no binding outside the implementer binding and model family",
                ))
    hb = A.heartbeat_age(root)
    if hb is not None and hb > 360:
        out.append(("watchdog-dead", f"watchdog silent for {hb // 60}m — launchctl / ao watchdog status"))
    we = wake_error(os.path.join(STATE_DIR, f"escalate-{key}.log"))
    if we and we["kind"] in ("binary", "session"):
        try:
            when = time.mktime(time.strptime(we["when"], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            when = time.time()
        if time.time() - when < 24 * 3600:
            out.append(("wake-failed", f"last architect wake failed ({we['kind']}): {we['text'][:90]}"))
    from . import telegram as _tg, email as _em
    if not (_tg.config() or _em.config()):
        out.append(("no-channel", "no human channel beyond desktop notifications — ao email setup"))
    for sib, age in A.stale_siblings(root).items():
        out.append((f"sibling-dead:{sib}", f"{sib}: watchdog silent for {age // 60}m"))
    br = A.burn_rate(root)
    samples = A.credit_samples(root)
    last = samples[-1] if samples else None
    credits = _credits_problem(br, last)
    if credits:
        out.append(credits)
    from . import features as _features
    try:
        from .watchdog import load_state as _load_state
        blind = (_load_state(root) or {}).get("credit_check_problem")
    except Exception:
        blind = None
    # Paged only while the implementer is being driven: a check that cannot read
    # usage matters because exhaustion stops the work, and an owner who stopped the
    # work on purpose does not need an hourly page saying so. Plain doctor always
    # prints it.
    if blind and _features.enabled(cfg, "nudge"):
        out.append(("credits-check", f"credit usage cannot be read ({blind.get('reason')}) — "
                                     f"the exhaustion alarm is blind until it reads again"))
    try:
        msgs_p, _ = A.session_paths(cfg)
        # The watchdog's heartbeat now comes before its transcript check, so a
        # transcript it cannot find no longer looks like a dead watchdog (#71).
        if (cfg.get("implementer") or {}).get("session") and _features.enabled(cfg, "nudge") \
                and (not msgs_p or not os.path.exists(msgs_p)):
            out.append(("transcript-missing", "the implementer's transcript cannot be found — the "
                        "watchdog has nothing to watch and restarts nobody; check implementer.session"))
        if msgs_p and os.path.exists(msgs_p) and time.time() - os.path.getmtime(msgs_p) < 3600 \
                and not A.read_tail(msgs_p, 2_000_000):
            out.append(("transcript-blind", "fresh transcript, nothing parsed — the agent CLI's format changed"))
    except Exception:
        pass
    # A source tree no gate exercises is a tree nothing verifies (#5).
    try:
        with open(os.path.join(root, ".ao", "gates.json"), encoding=UTF8) as fh:
            gate_spec = json.load(fh)
    except (OSError, ValueError):
        gate_spec = None
    if gate_spec is not None:
        for tree, chain, files in A.gate_coverage_gaps(root, gate_spec, S.get(cfg, "gates.coverage_min_files")):
            out.append((f"gate-uncovered:{tree}", f"{tree}/ holds {files} {chain} files that no gate in "
                                                  ".ao/gates.json exercises"))
    # A setting that is written but not used says one thing and does another (#74).
    for name, text in S.problems(cfg):
        out.append((f"setting:{name}", text))
    if len(A.deferred_open(root)) >= 3:
        out.append(("deferred-pile", f"{len(A.deferred_open(root))} deferred actions waiting — ao catchup"))
    # The playbook exists but nothing the agent reads points at it: the rules are
    # written and not in force. init prints the pointer; this is the reminder.
    from . import skillkit
    if skillkit.rules_wired(root) is False:
        named = ", ".join(skillkit.rule_file_names(root) + skillkit.steering_dirs(root))
        out.append(("rules-not-wired", f"no rule file ({named}) points at the ao playbook — "
                                       "paste the pointer or run `ao init --rules`"))
    # Two critical roles on one rate-limited pool fail together: the day the
    # architect ran dry, so did the reviewer, and the run locked.
    arch_bin = os.path.basename(((cfg.get("architect") or {}).get("argv") or [""])[0])
    rv = cfg.get("reviewer") or {}
    rv_bin = os.path.basename((rv.get("argv") or [""])[0])
    if not strict_matrix and arch_bin and arch_bin == rv_bin and not rv.get("fallbacks"):
        out.append(("shared-pool", f"architect and reviewer both run `{arch_bin}` on one quota pool and the reviewer has no "
                                   f"fallback — let keyflip rotate accounts before a spawn (`ao config set "
                                   f"keyflip.rotation on --machine`), use another model family, or, on a "
                                   f"machine without keyflip, add reviewer.fallbacks"))
    # A dependency the board cannot resolve makes READY wrong without a word (#33).
    try:
        graph_problems = A.board_graph(root)["problems"]
    except Exception:
        graph_problems = []
    if graph_problems:
        out.append(("board-graph", "; ".join(graph_problems[:3])
                    + (f" and {len(graph_problems) - 3} more" if len(graph_problems) > 3 else "")
                    + " — ao board"))
    # A boundary its own acceptance cannot fit is found before the work, not in it (#35).
    try:
        conflicts = [f"{item['id']}: {text}" for state in ("running", "queued")
                     for item in A.board(root)[state] for text in A.boundary_conflicts(root, item)]
    except Exception:
        conflicts = []
    if conflicts:
        out.append(("boundary-conflict", "; ".join(conflicts[:3])
                    + (f" and {len(conflicts) - 3} more" if len(conflicts) > 3 else "") + " — ao board"))
    # Green branches make a red main when nobody ran their merge (#39).
    try:
        unverified = A.unverified_merges(root, cfg)
    except Exception:
        unverified = []
    if unverified:
        out.append(("unverified-merge", "; ".join(f"{sha[:12]}: {why}" for sha, why in unverified[:3])
                    + (f" and {len(unverified) - 3} more" if len(unverified) > 3 else "")
                    + " — ao merge-check <branch> before merging"))
    # A reviewer whose adapter cannot deny tools is not a reviewer (#88).
    reviewer_adapter = (cfg.get("reviewer") or {}).get("adapter")
    if reviewer_adapter:
        eligible, why = A.reviewer_eligibility(A.load_adapter(reviewer_adapter, root))
        if not eligible:
            out.append((f"reviewer-ineligible:{reviewer_adapter}", f"the reviewer runs {reviewer_adapter}, which may not "
                        f"review: {why} — ao role set reviewer <adapter> --model <model>"))
    # A configured adapter whose command this machine lacks would fail at its first spawn (#89).
    try:
        absent = A.absent_adapter_binaries(cfg)
    except Exception:
        absent = []
    for actor, ident, binaries in absent:
        out.append((f"adapter-binary:{actor}", f"{actor} runs the {ident} adapter, but {' or '.join(binaries)} "
                    "is not on this machine (PATH, binaries.extra_dirs, the usual install directories)"))
    # Agent configuration checked by AgentShield's categories, natively (#14).
    try:
        for category, text in A.agent_config_findings(root):
            out.append((f"agent-config:{category}", text))
        for text in A.verify_content(root):
            out.append(("content-drift", text))
    except Exception:
        pass
    # A filter in front of an agent's shell is asked what it does to each measurement,
    # not trusted to leave them alone because its configuration says so (#52).
    try:
        probed = A.probe_filters(root)
    except Exception as exc:
        probed = []
        out.append(("measurement-filter", f"the filter probe failed ({exc}); what a filter does to a "
                                          "measurement is not verified"))
    filtering = {}
    for result in probed:
        if result["verdict"] == "in-force":
            continue
        hint = ("add them to the filter's exclusions" if result["changed"]
                else "docs/gates.md says which filters ao may ask")
        key, text = f"measurement-filter:{result['program']}", f"{A.filter_probe_text(result)} — {hint}"
        filtering[key] = f"{filtering[key]}; {text}" if key in filtering else text
    out.extend(filtering.items())
    # Mail is governance; a store ahead of its private copy is on one disk (#83).
    try:
        sync = A.mail_sync_state(root, cfg)
    except Exception:
        sync = None
    if sync:
        local, remote, problem = sync
        if problem:
            out.append(("mail-sync", problem + " — ao mail sync refuses it"))
        elif local and local != remote:
            out.append(("mail-sync", "the message store is ahead of its private copy — ao mail sync"))
    # An implementer with nothing pre-authorised to pick up next stalls the moment
    # the architect is away; two READY items is the floor.
    try:
        ready = len(A.ready(root)) if hasattr(A, "ready") else len(A.board(root)["queued"])
        if ready < 2 and not A.board(root)["running"]:
            out.append(("queue-shallow", f"{ready} READY item(s) and nothing running — refill .ao/backlog.md before the architect is away"))
    except Exception:
        pass

    # Static intent is only the safety precondition for running a hook.  A green
    # commit-hook result requires Git itself to resolve the active path, execute
    # it with an isolated synthetic index, and return AO's nonce-bound refusal.
    # Pre-push remains informational: #59 is about commit authority.
    hook_inventory = _ao_hook_inventory(root)
    proof = _hook_execution_probe(hook_inventory)
    if hook_inventory["error"]:
        out.append(("commit-hook", proof["detail"] + " — ao hooks status"))
    else:
        active = _active_hook_targets(hook_inventory)
        commit = active["pre-commit"]
        misplaced = [
            target for target in hook_inventory["targets"]
            if not target["active"]
            and target["role"] == "pre-commit"
            and target["reachability"] == "potentially-effective"
            and _state_base(target["static_state"]) in (
                "current-local", "current-scoped", "legacy", "ambiguous-ao"
            )
        ]
        reasons = []
        if not proof["installed"]:
            reasons.append("execution proof failed: " + proof["detail"])
        if misplaced:
            reasons.append(
                "potentially-effective misplaced pre-commit: "
                + ", ".join(target["path"] for target in misplaced[:3])
            )
        if reasons:
            if commit["repository_source"] and commit["track_state"] == "tracked" \
                    and commit["crlf_only"]:
                repair = "git restore -- .githooks/pre-commit"
            elif misplaced:
                needs_allow = any(
                    target["directory_class"] in ("shared", "external")
                    or target["globally_configured"]
                    for target in misplaced
                )
                flag = " --allow-shared-hooks" if needs_allow else ""
                repair = f"ao hooks uninstall{flag}, then ao hooks install{flag}"
            elif _project_enrollment(root)["state"] == "legacy":
                repair = PROJECT_ADOPT_HINT + ", then ao hooks install"
            elif _state_base(commit["static_state"]) not in (
                "current-local", "current-scoped"
            ):
                repair = "ao hooks install (restore tracked hooks with Git)"
            else:
                repair = "ensure the hook can resolve this AO executable, then ao hooks status"
            out.append(("commit-hook", "; ".join(reasons) + f" — {repair}"))
    out.extend(_actor_grant_problems(cfg))
    if _project_enrollment(root)["state"] in ("enrolled", "legacy"):
        try:
            stray = A.commits_without_grant(root)
        except Exception:
            stray = []
        if stray:
            out.append(("landed-without-grant",
                        f"{len(stray)} commit(s) landed with a tree no grant bound: "
                        + ", ".join(sha[:12] for sha in stray[:5])
                        + " — a path staged after commit-check, or a commit made outside ao"))
    return out


# Conditions the watchdog raises itself, by the key it raises them under. Two
# alarms for one condition double the ladder - two ids, two levels, two mails -
# and they cannot share an id, because every touch rewrites the level. So the
# doctor rings these only while the watchdog's own alarm is quiet: a stopped
# watchdog, which is what the doctor check is for, lets it go quiet.
DOCTOR_WATCHDOG_ALARMS = {"credits-exhaust": "credits-exhaust",
                          "wake-failed": "architect-wake-failed"}


def _credits_problem(br, last):
    """The one credits problem the readings show: exhausted outranks a projection.

    At 12503/10000 the doctor still said "credits run out 15 Sep, before the
    reset", because the projection was checked first and a date that had already
    passed read like one still ahead.
    """
    # Which account, since a switch leaves another account's figures behind (#36).
    whose = (f"account {last['account']}" if last and last.get("account")
             else "account not named by the reading")
    if last and last.get("limit") and float(last.get("used") or 0) >= float(last["limit"]):
        return ("credits-exhaust", f"credits exhausted at the last reading: "
                                   f"{float(last['used']):.0f}/{float(last['limit']):.0f} ({whose})")
    if br and br.get("before_reset"):
        return ("credits-exhaust", f"credits run out {time.strftime('%d %b', time.localtime(br['exhausts_at']))}, "
                                   f"before the reset ({br['per_day']:.0f}/day, account {br.get('account')}) "
                                   "— new account or fewer features")
    return None


# Findings that mean work has stopped and only a person can restart it. Everything
# else a doctor finds is an advisory: recorded for the architect, never paged (#40).
DOCTOR_RED = {"watchdog-dead", "wake-failed", "transcript-blind", "transcript-missing"}


def _doctor_severity(key, text):
    if key in DOCTOR_RED or (key == "credits-exhaust" and text.startswith("credits exhausted")):
        return "red"
    return "yellow"


def _doctor_check(cfg, page=False):
    """Machine-facing doctor: one line per problem, exit 1 if any.

    Run by a person or an agent it pages nobody: it reports to its caller and
    returns non-zero (#40). Only the scheduled job passes page=True, and then a red
    finding goes to the human ladder while an advisory is recorded for the
    architect. On 2026-09-08 two wake turns ran --check by hand and mailed a
    standing advisory; on 2026-09-15 the scheduled job mailed exhausted credits
    every six hours although nothing could change before their reset.
    """
    from .watchdog import notify
    root = cfg["root"]
    project = A.project_key(root)
    problems = doctor_problems(cfg)
    if not problems:
        print(f"ok {time.strftime('%H:%M')} — no problems")
        return 0
    ringing = {alarm["key"] for alarm in A.active_alarms(project)} if page else set()
    samples = A.credit_samples(root) if page else []
    reset_at = (samples[-1] if samples else {}).get("reset_at")
    for key, text in problems:
        print(f"PROBLEM {key}: {text}")
        if not page or DOCTOR_WATCHDOG_ALARMS.get(key) in ringing:
            continue
        if _doctor_severity(key, text) == "red":
            held = {"quiet_until": reset_at} if key == "credits-exhaust" and reset_at else {}
            notify(f"{project}: {key}", text, root, key=f"doctor:{key}", window=3600,
                   audience="human", **held)
        else:
            notify(f"{project}: {key}", text, root, key=f"doctor:{key}", window=3600,
                   audience="architect")
    return 1


# A credit pool is shared: what ao reads is ao's share, what the account says is everyone's (#95).
SHARED_POOL_NOTE = ("the account's figure counts every session on it - another project's CLI, an IDE, "
                    "another orchestrator - and ao's transcript holds only ao's own turns")


def _account_beside_share(cfg, since=None):
    """Lines setting the implementer's own account beside ao's own transcript share, labelled as such.

    The account is the one the implementer's adapter declares a lookup for. The first shipped
    adapter's was read whatever the implementer ran, so an implementer on a harness that bills
    another pool in another unit was shown a credit account it never spends; it has none ao
    can read, and the line says so.
    """
    ident = str(A.implementer_adapter(cfg).get("id") or "")
    if not A.usage_api(ident):
        return [f"{C['dim']}the account: {ident or 'the implementer'} declares none ao can read; what follows "
                f"is ao's transcript only{C['reset']}"]
    try:
        acct = A.account_usage(adapter_id=ident)
    except Exception:
        acct = None
    if not acct or acct.get("error") or acct.get("expired") or not acct.get("limit"):
        return [f"{C['dim']}the account's figure could not be read; what follows is ao's transcript "
                f"only{C['reset']}"]
    lines = [f"the account: {float(acct['used']):,.0f} of {float(acct['limit']):,.0f} used"]
    try:
        mine = A.turn_costs(cfg, since=since)
    except Exception:
        mine = None
    if mine and mine.get("turns") and float(acct["used"] or 0) > 0:
        lines.append(f"ao's own share, from its transcript: {mine['total']:,.0f} {mine['unit']} "
                     f"({100 * mine['total'] / float(acct['used']):.0f}% of the account's used)")
    lines.append(f"{C['dim']}{SHARED_POOL_NOTE}{C['reset']}")
    return lines


def _cost_by_feature(cfg, since, window):
    """`ao cost --features`: the implementer's spend each switch caused, over the window it was measured (#10)."""
    measured = A.feature_costs(cfg, since=since)
    if not measured["turns"]:
        print("no transcript turns in this window")
        return 0
    span = " to ".join(time.strftime("%d %b %H:%M", time.localtime(at)) for at in (measured["from"], measured["to"]))
    print(f"{C['b']}implementer spend by feature{C['reset']}  {C['dim']}({measured['unit']}; {measured['turns']} turns, "
          f"{span}{' — last ' + window if window else ''}){C['reset']}")
    for name, spent in measured["features"].items():
        share = 100 * spent["usage"] / measured["total"] if measured["total"] else 0
        print(f"  {name:<18}{spent['turns']:>5} turns {spent['usage']:>9.1f}  {share:>5.1f}%")
    for name, count in measured["counted"].items():
        print(f"  {name:<18}{count:>5} started  {C['dim']}the architect's pool, not this transcript{C['reset']}")
    print(f"  {'inventory_review':<18}  {C['dim']}counted with review: a transcript cannot tell them apart{C['reset']}")
    return 0


def cmd_cost(cfg, args):
    """What the coordination spends: the implementer's turns by what they did.

    The honest answer to "is ao bureaucracy" is the share of spend in turns that
    wrote nothing — ceremony (review/gate commands), coordination (inbox, reports,
    writer checks) — against turns that wrote product. `wasted` counts turns that
    ended in a blocked report without a product change: the queue-empty loop.
    """
    root = cfg["root"]
    since = None
    if args.since:
        n, unit = A.re.match(r"(\d+)([hd])", args.since).groups()
        since = time.time() - int(n) * (3600 if unit == "h" else 86400)
    if getattr(args, "features", False):
        return _cost_by_feature(cfg, since, args.since)
    c = A.turn_costs(cfg, since=since)
    if not c["turns"]:
        print("no transcript"); return 0
    tot = c["total"] or 1
    print(f"{C['b']}implementer spend by turn class{C['reset']}  {C['dim']}({c['unit']}; "
          f"{'last ' + args.since if args.since else 'whole transcript'}){C['reset']}")
    print(f"  {'class':<14}{'turns':>6}{'spend':>10}{'share':>7}   {'wasted turns':>12}")
    for cls in ("product", "analysis", "ceremony", "coordination"):
        b = c["by_class"].get(cls)
        if not b:
            continue
        w = f"{b['wasted']} ({b['wasted_usage']:.0f})" if b["wasted"] else ""
        print(f"  {cls:<14}{b['turns']:>6}{b['usage']:>10.0f}{100 * b['usage'] / tot:>6.0f}%   {w:>12}")
    print(f"  {'total':<14}{sum(b['turns'] for b in c['by_class'].values()):>6}{tot:>10.0f}")
    overhead = sum(c["by_class"].get(k, {}).get("usage", 0) for k in ("ceremony", "coordination"))
    for line in _account_beside_share(cfg, since):
        print(f"  {line}")
    print(f"\n  coordination + ceremony: {C['b']}{100 * overhead / tot:.0f}%{C['reset']} of spend"
          f"  ·  ao commands: {', '.join(f'{k} {v}' for k, v in c['ao_commands'].most_common(6))}")
    # the reviewer's side: files, wasted, sizes
    d = os.path.join(root, cfg["reviews"])
    if os.path.isdir(d):
        files = [f for f in os.listdir(d) if f.endswith(".md") and (not since or os.path.getmtime(os.path.join(d, f)) >= since)]
        una = sum(1 for f in files if A.reviews(root, cfg["reviews"], limit=10_000) and False)  # placeholder
        verdicts = dict(A.reviews(root, cfg["reviews"], limit=10_000))
        una = sum(1 for f in files if verdicts.get(f) in ("UNAVAILABLE", "INVALID"))
        size = sum(os.path.getsize(os.path.join(d, f)) for f in files)
        print(f"  reviews: {len(files)} files ({una} unavailable/invalid, cost nothing), "
              f"{size // 1000}k chars of verdict text; each review sends the slice diff to the reviewer model")
    return 0


def cmd_config(cfg, args):
    """Read and change what ao lets a person set (#74).

    `list` shows every setting with its value, its default and where the value came
    from; `get` prints one; `set` and `unset` change the project's config, or with
    --machine the machine's settings. Both are written whole or not at all.
    """
    root = cfg["root"]
    key = getattr(args, "key", None)
    if args.action == "list":
        print(f"  {'setting':<32}{'value':<14}{'default':<12}{'from':<9}what it decides")
        for name, spec in S.SETTINGS.items():
            value, source, problem = S.resolve(cfg, name)
            mark = f" {C['red']}!{C['reset']}" if problem else ""
            colour = C["dim"] if source == "default" else C["b"]
            print(f"  {name:<32}{colour}{str(value):<14}{C['reset']}{str(spec.default):<12}{source:<9}"
                  f"{C['dim']}{spec.text}{C['reset']}{mark}")
            if problem:
                print(f"  {'':<32}{C['red']}{problem}{C['reset']}")
        print(f"\n  {C['dim']}project: .ao/config.json · machine: {S.machine_path()} · "
              f"ao config set <setting> <value> [--machine]{C['reset']}")
        return 0
    if key not in S.SETTINGS:
        print(f"unknown setting {key!r}; `ao config list` shows the names")
        return 2
    if args.action == "get":
        print(S.get(cfg, key))
        return 0
    machine = bool(getattr(args, "machine", False))
    if S.SETTINGS[key].scope == "machine" and not machine:
        print(f"{key} governs the whole machine; set it with --machine")
        return 2
    if args.action == "set":
        try:
            value = S.parse(key, args.value)
        except (TypeError, ValueError) as exc:
            print(f"{C['red']}not changed{C['reset']}: {exc}")
            return 2
    else:
        value = S._MISSING
    if machine:
        try:
            S.write_machine(S.assign(S.machine_settings(), key, value))
        except OSError as exc:
            print(f"{C['red']}not changed{C['reset']}: {exc}")
            return 1
    else:
        document = A.project_config_document(root)
        if document["problem"]:
            # Rebuilding a config ao cannot read would drop what it held (#56).
            print(f"{C['red']}not changed{C['reset']}: {document['problem']}")
            return 1
        try:
            A.write_project_config(root, json.dumps(S.assign(document["config"], key, value), indent=2,
                                                    ensure_ascii=False))
        except OSError as exc:
            print(f"{C['red']}not changed{C['reset']}: {exc}")
            return 1
        cfg = dict(A.load_config(root), root=root)
    value, source, _ = S.resolve(cfg, key)
    print(f"{key} = {value!r} ({source})")
    return 0


def _feature_cell(measured, key):
    """One feature's measured cost as a short cell: a share of the implementer's spend, or a count (#10)."""
    if not measured:
        return "—"
    if key in measured["counted"]:
        return f"{measured['counted'][key]} started"
    if key not in measured["features"] or not measured["total"]:
        return "with review" if key == "inventory_review" else "—"
    return f"{100 * measured['features'][key]['usage'] / measured['total']:.1f}%"


def cmd_features(cfg, args):
    """The switches and what each costs. All off: deterministic ao, zero model spend."""
    from . import features as F
    root = cfg["root"]
    if args.action in ("on", "off"):
        if args.key not in F.FEATURES:
            print(f"unknown feature {args.key}; one of {', '.join(F.ORDER)}"); return 2
        try:
            F.set_switch(root, args.key, args.action == "on")
        except (OSError, ValueError) as exc:
            print(f"{C['red']}not changed{C['reset']}: {exc}")
            return 1
        cfg = A.load_config(root)
    on = F.switches(cfg)
    try:
        measured = A.feature_costs(cfg, since=time.time() - 7 * 86400)
    except Exception:
        measured = None
    print(f"  {'feature':<18}{'':<4}{'last 7 days':>13}  what it spends")
    for k in F.ORDER:
        label, _, what = F.FEATURES[k]
        state = f"{C['green']}on {C['reset']}" if on[k] else f"{C['dim']}off{C['reset']}"
        print(f"  {k:<18}{state:<4}{_feature_cell(measured, k):>13}  {C['dim']}{what}{C['reset']}")
    print(f"\n  {C['dim']}measured from the implementer's transcript and the watchdog's logs, never estimated; "
          f"all off = board, mail, gates, commit-ok, alarms, pings and hooks only; `ao cost --features` "
          f"for the window{C['reset']}")
    print(f"  {C['dim']}ao features on|off <feature>{C['reset']}")
    return 0


def _agent_names(cfg):
    """Names that belong to agents or roles, never to the person a waiver must name."""
    names = {"architect", "implementer", "reviewer", "watchdog", "ao", "agent", "human"}
    for role in ("implementer", "architect"):
        block = cfg.get(role) or {}
        names.update(str(block.get(key) or "") for key in ("name", "adapter", "session"))
    primary = cfg.get("reviewer") or {}
    names.update(str(route.get("id") or "") for route in [primary] + list(primary.get("fallbacks") or [])
                 if isinstance(route, dict))
    return {name.strip().lower() for name in names if name.strip()}


def cmd_waive(cfg, args):
    """A person bypasses a gate for a slice, on the record. Reconciled later by `ao catchup`."""
    root = cfg["root"]
    if not args.why:
        print("--why is required: a waiver without a reason cannot be reconciled"); return 2
    # A waiver covers one slice for a bounded time and names a person (#67).
    if not args.slice or args.slice == "*":
        print("--slice is required: a waiver covers one slice, never every slice"); return 2
    if not (args.by or "").strip():
        print("--by is required: name the person who authorises this waiver"); return 2
    if args.by.strip().lower() in _agent_names(cfg):
        print(f"--by names an agent or a role ({args.by}); a waiver is a person's act"); return 2
    hours = args.hours if args.hours is not None else S.get(cfg, "waivers.default_hours")
    longest = S.get(cfg, "waivers.max_hours")
    if not 0 < hours <= longest:
        print(f"--hours must be above 0 and at most {longest}"); return 2
    try:
        rec = A.waive(root, args.gate, args.slice, args.why, by=args.by.strip(), hours=hours)
    except Exception as exc:
        print(f"{C['red']}waiver not recorded{C['reset']}: {exc}"); return 1
    print(f"{C['yellow']}{C['b']}waived{C['reset']} {args.gate} for {rec['slice']} ({rec['id']}) by {rec['by']} at HEAD {rec['head'][:8]}")
    print(f"{C['dim']}commit-ok honours it; `ao catchup` runs the missed {args.gate} against the landed range and closes it.{C['reset']}")
    A.record_notice(root, f"waiver {args.gate}", f"{rec['slice']}: {args.why[:120]} ({rec['by']})", sent=False, key="waiver")
    return 0


def _catchup_statement(cfg, args):
    """(the family a person names as the author's for this run, or None; why the command is refused, or None).

    Naming the family that wrote a waived range is a person's statement. It is recorded
    with every review it decides, beside what ao can check about who made it - the login
    and whether a terminal was attached - as a waiver is.
    """
    family = str(getattr(args, "author_family", None) or "").strip().lower()
    by = str(getattr(args, "by", None) or "").strip()
    if not family:
        return None, ("--by names the person who states --author-family; give both" if by else None)
    if not A.re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,63}", family):
        return None, "--author-family is one family name, such as the lowercase id its model family goes by"
    if not by:
        return None, "--by is required with --author-family: name the person who says which family wrote the ranges"
    if by.lower() in _agent_names(cfg):
        return None, f"--by names an agent or a role ({by}); naming the author's family is a person's statement"
    user, interactive = A._login_and_terminal()
    return {"family": family, "by": by, "user": user, "interactive": interactive}, None


def _catchup_author(item, statement):
    """What a waived range's review is told about who wrote it: its grant's record, and a person's statement."""
    recorded = item.get("author") if isinstance(item.get("author"), dict) else {}
    author = {key: recorded.get(key) for key in ("role", "actor", "adapter")}
    author.update(family=str(recorded.get("family") or "").strip().lower() or None,
                  grant=item.get("grant"), stated=statement)
    return author


def _catchup_reviewer_note(cfg, author):
    """Why the configured reviewers may not review a range this author wrote, or None; a plan names it early."""
    if M.is_strict(cfg):
        try:
            M.resolve(cfg, require_independent=True, author_families=_author_families(author))
        except M.MatrixError as exc:
            return "; ".join(exc.problems[:2])
        return None
    reviewer = cfg.get("reviewer") or {}
    if not reviewer.get("argv"):
        return "no reviewer is configured"
    refused = _reviewer_ineligible(cfg, reviewer, author=author)
    return f"the reviewer may not review it: {refused}" if refused else None


def _catchup_mail(root, cfg, waiver, rng, artefact):
    """The architect's decision request after a waived range is reviewed NEEDS_CHANGES.

    Named and addressed as every message ao writes (#31): to whoever holds the architect
    role, from the role this command runs for, or from a person when ao did not start it.
    """
    implementer, architect = A.mail_names(cfg)
    sender = {"implementer": implementer, "architect": architect}.get(A.invoking_role(), "human")
    # The waiver's fields come from a ledger anyone on the machine can edit: one line each (#55).
    slice_id, wid, by, why = (A.review_header_value(waiver.get(key) or "") for key in ("slice", "id", "by", "why"))
    name = (f"{time.strftime('%Y%m%d-%H%M')}-{sender}-to-{architect}-REVIEW-"
            f"{A.safe_slug(str(waiver.get('slice') or '').lower(), 'slice')}-needs-changes.md")
    findings = A.review_header_value(f"{cfg['reviews']}/{artefact or ''}")
    body = (f"# The waived review of {slice_id} needs changes\n\n## Decision required\n\n"
            f"The retrospective review of {rng}, which landed under waiver {wid} ({by}: {why}), "
            f"returned NEEDS_CHANGES. The findings are in {findings}.\n\n"
            "The waiver is closed. Decide the fix slice.\n")
    return A.write_mail(root, cfg, name, body, {"kind": "review", "class": "needs-decision", "from": sender,
                                                 "to": architect, "slice": slice_id})


def cmd_catchup(cfg, args):
    """Replay what could not run: waived reviews, deferred wakes and nudges.

    A waived range is reviewed by a model family other than the one that wrote it (#65):
    the family the grant under the waiver recorded, and any a person names with
    --author-family and --by. With neither, its review is refused and the waiver stays
    open. --plan says what a run would do and changes nothing; --slice and --limit bound
    a run. A review stays synchronous: a waiver closes in the same run, on the review
    recorded for exactly its range, or stays open.
    """
    from types import SimpleNamespace
    root = cfg["root"]
    plan = bool(getattr(args, "plan", False))
    limit = getattr(args, "limit", None)
    only = getattr(args, "slice", None)
    statement, refusal = _catchup_statement(cfg, args)
    if refusal:
        print(refusal)
        return 2
    if limit is not None and limit < 1:
        print("--limit is at least 1: the number of reviews this run may start")
        return 2
    did = started = 0
    failed = []
    totals = {"waivers": 0, "commits": 0, "lines": 0, "unnamed": 0}      # what --plan sums up

    def close(wid, outcome):
        # A waiver retires on a durable append or not at all; a failure is reported
        # and fails the command, never counted as handled (#67).
        try:
            A.close_waiver(root, wid, outcome)
            return True
        except Exception as exc:
            print(f"  {C['red']}could not close {wid}{C['reset']}: {exc}")
            failed.append(wid)
            return False

    if plan:
        print(f"{C['b']}catchup plan{C['reset']}{C['dim']}: nothing is reviewed or written{C['reset']}")
    try:
        targets = A.review_waiver_ranges(root)
    except Exception as exc:
        print(f"  {C['red']}waivers cannot be read{C['reset']}: {exc}")
        targets = []
        failed.append("ledger")
    if only:
        targets = [item for item in targets if item["waiver"].get("slice") == only]
    for position, item in enumerate(targets):
        w = item["waiver"]
        label = f"{w['id']} ({w['slice']})"
        if limit is not None and started >= limit:
            print(f"  --limit {limit}: {len(targets) - position} waiver(s) wait for the next run")
            break
        totals["waivers"] += 1
        if item["problem"]:
            print(f"  {label}: {item['problem']}; keeping it open")
            continue
        if item.get("unused"):
            if not item.get("expired"):
                print(f"  {label}: nothing was granted under it yet; keeping it open until it expires")
            elif plan:
                print(f"  {label}: expired unused; a run closes it")
            elif close(w["id"], "nothing was granted under it before it expired"):
                print(f"  {label}: expired unused; closed")
                did += 1
            continue
        if not item["landed"]:
            if item["newest"]:
                print(f"  {label}: nothing landed yet after the waiver; keeping it open")
            elif plan:
                print(f"  {label}: no commits landed before the next waiver; a run closes it")
            elif close(w["id"], "no commits landed before the next waiver was opened"):
                print(f"  {label}: no commits landed before the next waiver; closed")
                did += 1
            continue
        rng = f"{item['start'][:12]}..{item['end'][:12]}"
        lines = A.range_changed_lines(root, item["start"], item["end"])
        size = f"{item['landed']} commit(s), {'unknown' if lines is None else lines} changed line(s)"
        author = _catchup_author(item, statement)
        families = _author_families(author)
        if not families:
            # A review by the family that wrote the range would not be independent (#65).
            totals["unnamed"] += 1
            landed_by = " ".join(str(author[key]) for key in ("role", "actor") if author.get(key))
            print(f"  {label}: {rng}, {size}: the family of the model that wrote it is not established"
                  + (f" (landed by the {landed_by})" if landed_by else "")
                  + "; a person names it with --author-family <family> --by <name>; keeping it open")
            continue
        started += 1
        totals["commits"] += item["landed"]
        totals["lines"] += lines or 0
        if plan:
            note = _catchup_reviewer_note(cfg, author)
            print(f"  {label}: {rng}, {size}: review by a family other than {', '.join(families)}"
                  + (f"; {note}" if note else ""))
            continue
        print(f"  {label}: reviewing its own landed range {rng}, {size}, by a family other than {', '.join(families)}")
        # No deadline is passed: a reviewer's time is review_timeout per call, and a silent
        # one is killed after review.stall_minutes (#63, #25).
        ns = SimpleNamespace(boundary=args.boundary or f"waived review for {w['slice']}: {w['why']}",
                             paths=None, commits=f"{item['start']}..{item['end']}", author=author)
        try:
            before = A.review_row_count(root)
        except Exception as exc:
            print(f"  {C['red']}review ledger cannot be read{C['reset']}: {exc}; {w['id']} stays open")
            failed.append(w["id"])
            continue
        code = cmd_review(cfg, ns)
        # The exit code is not a verdict: a review that refused to run exits 1 or 2
        # too. A waiver closes on the review recorded for exactly this range.
        try:
            recorded = A.range_review(root, ns.commits, since=before) or {}
        except Exception as exc:
            print(f"  {C['red']}review ledger cannot be read{C['reset']}: {exc}; {w['id']} stays open")
            failed.append(w["id"])
            continue
        verdict = recorded.get("verdict")
        if verdict == "APPROVED":
            if close(w["id"], "reviewed: APPROVED"):
                did += 1
        elif verdict == "NEEDS_CHANGES":
            if close(w["id"], "reviewed: NEEDS_CHANGES — fix slice needed"):
                did += 1
                try:
                    _catchup_mail(root, cfg, w, rng, recorded.get("artefact"))
                except OSError as exc:
                    print(f"  {C['red']}the architect's decision request was not written{C['reset']}: {exc}")
                    failed.append(w["id"])
        elif verdict is None and code == 0:
            if close(w["id"], "nothing to review: the landed range has no net change"):
                print(f"  {label}: the landed range has no net change; closed")
                did += 1
        elif code == 3 or verdict == "UNAVAILABLE":
            print(f"  reviewer still unavailable; {w['id']} stays open")
        elif verdict == "INVALID":
            print(f"  the reviewer returned no valid verdict; {w['id']} stays open")
        elif code == 2:
            print(f"  reviewer configuration invalid, or the range cannot be reviewed; {w['id']} stays open")
        else:
            print(f"  no review was recorded (exit {code}); {w['id']} stays open")
    if plan:
        print(f"totals: {totals['waivers']} waiver(s); {started} review(s) of {totals['commits']} commit(s) and "
              f"{totals['lines']} changed line(s); {totals['unnamed']} refused until a person names the author's "
              f"family; {len(A.deferred_open(root))} deferred wake(s) and nudge(s) a run replays")
        return 1 if failed else 0
    for r in A.deferred_open(root):
        print(f"  deferred {r['kind']} ({r.get('reason', '')}) since {time.strftime('%d %b %H:%M', time.localtime(r['at']))}")
        A.deferred_close(root, r["id"], "replayed by catchup")
        did += 1
    if A.heartbeat_age(root) is None:
        # A cycle writes this project's heartbeat. Where no watchdog runs, that file
        # goes stale within minutes and every sibling watchdog reports it as dead.
        print(f"{C['dim']}no watchdog runs for this project; skipping the cycle{C['reset']}")
    else:
        print(f"{C['dim']}running one watchdog cycle to act on what is now possible{C['reset']}")
        from . import watchdog as W
        W.run(SimpleNamespace(root=root, idle_minutes=S.get(cfg, "watchdog.idle_minutes"), dry_run=False, prompt=W.NUDGE_PROMPT))
    print(f"{C['green']}catchup{C['reset']} handled {did} item(s)")
    return 1 if failed else 0


def cmd_pings(cfg, args):
    """Dead man's switch: an external service that alarms when the pings stop."""
    root = cfg["root"]
    if args.action == "setup":
        if not args.url:
            print("1. Create a check at https://healthchecks.io (free) with a 15-minute period and a 10-minute grace.\n"
                  "2. Copy its ping URL, then:  ao pings setup --url https://hc-ping.com/<uuid>  [--all]\n"
                  "The watchdog and the doctor job ping it every cycle; when both die, healthchecks e-mails you.")
            return 0
        A.set_ping_url(root, args.url, all_projects=args.all)
        print(f"{C['green']}saved{C['reset']} {A.pings_path()} (0600)"); return 0
    url = A.ping_url(root)
    if args.action == "test":
        ok = A.ping(root)
        print(f"{C['green']}pinged{C['reset']}" if ok else f"{C['red']}no ping{C['reset']} ({'no url' if not url else 'request failed'})")
        return 0 if ok else 1
    print(f"ping url: {url or C['yellow'] + 'not configured' + C['reset']}")
    return 0
