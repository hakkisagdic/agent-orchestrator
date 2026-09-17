"""Checks: doctor problems, costs, settings, features, waivers, catch-up, pings.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


def _actor_grant_problems(cfg):
    """Doctor findings for each configured actor whose tool grant admits a bypass (#58), whose permission mode is
    not the one its adapter pins, or whose wake grant lacks what its playbook asks of it (GRANTS-PINNED)."""
    from . import allowlist as AL
    grants = []
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl["adapter"]) if impl.get("adapter") else {}
    if impl.get("adapter"):
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
        if role == "implementer":
            # A turn that would turn off its harness's own sandbox is not started until a person allows it,
            # and the doctor says what allowing it costs (GRANTS-PINNED).
            flags = A.unattended_flags(adapter, argv)[0]
            refused = A.bypass_refusal(adapter, flags, cfg)
            if refused:
                out.append((f"nudge-refused:{name}", f"the watchdog nudges no {name} implementer: {refused}; allowed, "
                                                     "each unattended turn runs every command outside that sandbox, "
                                                     "with the network, and writes wherever this user can"))
                continue
        text = AL.describe(role, name, AL.problems(argv, options, role=role))
        if text and role == "implementer" and A.sandbox_bypass(adapter, A.unattended_flags(adapter, argv)[0]):
            text += (f"; and a person allowed {A.sandbox_bypass(adapter, A.unattended_flags(adapter, argv)[0])[0]} "
                     "on this machine (watchdog.bypass_adapters), so its turns run outside its sandbox, with the network")
        if text:
            out.append((f"actor-grant:{role.replace(' ', '-')}", text))
        # A reviewer reads, and starts no MCP server it was not given (#24).
        if role.startswith("reviewer"):
            reach = AL.reviewer_problems(argv)
            if reach:
                out.append((f"reviewer-tools:{name}", f"{role} ({name}): " + "; ".join(reach)))
        elif A.pin_conflicts(argv, role):
            out.append((f"actor-mode:{role}", f"{role} ({name}): " + "; ".join(A.pin_conflicts(argv, role))))
    out.extend(_architect_grant_gaps(cfg))
    return out


def _architect_grant_gaps(cfg):
    """The rules the architect's configured grant lacks of the one its adapter declares now (GRANTS-PINNED).

    `ao init` writes the architect's argv into .ao/config.json, so a grant composed before the
    adapter's changed stays as it was: a refill wake told to write .ao/inbox/ could not.
    """
    from . import allowlist as AL
    architect = cfg.get("architect") or {}
    declared = A.load_adapter(architect["adapter"]) if architect.get("adapter") and architect.get("argv") else {}
    options = declared.get("options") or {}
    wanted = [rule for rule in str(options.get("architect_tools") or "").split(",") if rule]
    have = set(AL.rules(architect.get("argv") or []))
    missing = [rule for rule in wanted if rule not in have]
    if not wanted or not missing:
        return []
    flag = (options.get("allowed_tools") or ["the tool grant"])[0]
    return [("architect-grant", f"the architect's grant lacks {len(missing)} rule(s) its playbook and prompts need "
                                f"({', '.join(missing[:5])}{', …' if len(missing) > 5 else ''}): replace the value "
                                f"after {flag} in the architect's argv in .ao/config.json with "
                                f"{architect['adapter']}'s options.architect_tools")]


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
    if hb is not None and hb > WATCHDOG_SILENT_AFTER:
        out.append(("watchdog-dead", f"watchdog silent for {hb // 60}m — launchctl / ao watchdog status"))
    we = wake_error(os.path.join(STATE_DIR, A.project_file_name("escalate-log", key)))
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
    own = _implementer_credits(cfg)
    credits = _credits_problem(own["rate"], own["samples"][-1] if own["samples"] else None)
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
    # prints it. Only a check of the implementer's own account is its blind check
    # (ACCOUNT-READERS): one another harness's lookup failed is not.
    if blind and own["readable"] and blind.get("adapter") == own["adapter"] and _features.enabled(cfg, "nudge"):
        out.append(("credits-check", f"credit usage cannot be read ({blind.get('reason')}) — "
                                     f"the exhaustion alarm is blind until it reads again"))
    try:
        msgs_p, _ = A.session_paths(cfg)
        # The watchdog's heartbeat now comes before its transcript check, so a
        # transcript it cannot find no longer looks like a dead watchdog (#71).
        if (A.session_state(cfg, "implementer") or {}).get("session") and _features.enabled(cfg, "nudge") \
                and (not msgs_p or not os.path.exists(msgs_p)):
            out.append(("transcript-missing", "the implementer's transcript cannot be found — the "
                        "watchdog has nothing to watch and restarts nobody; check implementer.session"))
        if msgs_p and os.path.exists(msgs_p) and time.time() - os.path.getmtime(msgs_p) < 3600 \
                and not A.read_tail(msgs_p, 2_000_000):
            out.append(("transcript-blind", "fresh transcript, nothing parsed — the agent CLI's format changed"))
    except Exception:
        pass
    # A role's `auto` that settles on no session it may resume: the watchdog nudges or wakes nobody, and an
    # ambiguity is named rather than guessed at (SESSION-IDENTITY).
    try:
        for role, key, feature in (("implementer", "implementer-session", "nudge"),
                                   ("architect", "architect-session", "architect_wake")):
            state = A.session_state(cfg, role)
            if state is None or state["how"] == "pinned" or not _features.enabled(cfg, feature) \
                    or (role == "architect" and not any("{session}" in str(part)
                                                        for part in (cfg.get(role) or {}).get("argv") or [])):
                continue
            if not (state["session"] and state["trusted"]):
                out.append((key, f"the {role}'s session is {state['how']}: {state['why']}"))
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
            elif HOOK_AO_NOT_FOUND in proof["detail"]:
                repair = _ao_link_fix()           # an alias is invisible to the hook's /bin/sh (SAFE-REMOVE)
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


# Conditions the watchdog raises itself, by the doctor's finding: the key, level and
# window the watchdog raises them with. Two alarms for one condition double the
# ladder - two ids, two levels, two mails - and ringing the doctor's copy only while
# the watchdog's was quiet still rang both: the watchdog's alarm is quiet after a
# silence, under a snooze and between readings it could not take. So the doctor
# raises the watchdog's own alarm at the watchdog's level and known end: one
# condition is one alarm whoever sees it, with one snooze and one mail (#106).
DOCTOR_WATCHDOG_ALARMS = {"credits-exhaust": {"key": "credits-exhaust", "level": "red", "window": 6 * 3600},
                          "wake-failed": {"key": "architect-wake-failed", "level": None, "window": 6 * 3600}}

# A heartbeat older than this is a watchdog that stopped.
WATCHDOG_SILENT_AFTER = 360
# The scheduled check runs every fifteen minutes, from its launchd job or its Windows task:
# a run this long after its last one follows a silence of its own.
DOCTOR_SILENT_AFTER = 20 * 60


def _watchdog_interval(root):
    """Seconds between the watchdog's cycles: its launchd job's StartInterval, else two minutes.

    Two minutes is what `ao watchdog install` schedules without --interval, and what
    the Windows task runs.
    """
    import plistlib
    try:
        with open(_launchd_plist(_launchd_label("watchdog", A.project_key(root))), "rb") as fh:
            interval = int(plistlib.load(fh).get("StartInterval") or 120)
    except (OSError, ValueError, TypeError, AttributeError, plistlib.InvalidFileException):
        interval = 120
    return max(60, interval)


def _watchdog_first_cycle_due(root, now=None):
    """When the watchdog's first cycle after its job came back is due, while it still is; else None.

    Both scheduled jobs run at load, and the doctor could run before the watchdog's
    first cycle: on re-enabling a project after two weeks it rang a dead watchdog and
    exhausted credits on the desktop and the phone, two notices each about a watchdog
    that was starting and credits its first cycle was about to name (NOTICE-NOISE).

    Nothing the job loader keeps says when a job was loaded, and a marker written by
    `ao watchdog install` is not written when the jobs are enabled again with launchctl,
    after a reboot or after sleep. What every one of those leaves is this check's own
    silence: the scheduled check records each run, and a run that finds its last one
    more than twenty minutes ago was stopped too, by whatever stopped the watchdog. The
    watchdog then has one of its own cycles from that run before it counts as dead,
    unless its heartbeat had already stopped when this check last ran - a watchdog that
    died while the check was watching is not given another cycle. A check with no
    record counts as back: at worst a dead watchdog is paged one run later.
    """
    from .storage import replace_file_durably
    now = time.time() if now is None else now
    path = A.project_file(root, "doctor-runs")
    try:
        with open(path, encoding=UTF8) as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        record = {}
    record = record if isinstance(record, dict) else {}
    last = record.get("at") if isinstance(record.get("at"), (int, float)) else None
    back = record.get("back") if isinstance(record.get("back"), (int, float)) else None
    age = A.heartbeat_age(root)
    beat = None if age is None else now - age
    if last is None or now - last > DOCTOR_SILENT_AFTER:
        stopped_first = last is not None and beat is not None and last - beat > WATCHDOG_SILENT_AFTER
        back = None if stopped_first else now
    try:
        replace_file_durably(path, json.dumps({"at": int(now), "back": back}).encode("utf-8"))
    except OSError:
        return None                 # a check that cannot keep its record gives no cycle: it pages as it did
    if back is None or beat is None or beat >= back:
        return None
    due = back + _watchdog_interval(root)
    return due if now < due else None


def _no_account(ident):
    """What stands where a credit figure would, for an implementer whose adapter declares no account ao can read."""
    return f"{ident or 'the implementer'} declares none ao can read"


def _implementer_credits(cfg):
    """The implementer's own credit readings: {adapter, readable, samples, rate} (ACCOUNT-READERS).

    The account is the one the implementer's adapter declares a lookup for, and its
    readings are the samples that lookup took. A ledger can hold another harness's
    readings, and every credits finding, page and line was computed from all of them:
    an implementer whose adapter declares no account ao can read is not readable, and has
    no samples and no rate whatever the ledger holds.
    """
    ident = A.implementer_adapter_id(cfg)
    readable = bool(A.usage_api(ident))
    return {"adapter": ident, "readable": readable,
            "samples": A.credit_samples(cfg["root"], ident) if readable else [],
            "rate": A.burn_rate(cfg["root"], ident) if readable else None}


def _credits_problem(br, last, now=None):
    """The one credits problem the readings show: exhausted outranks a projection.

    At 12503/10000 the doctor still said "credits run out 15 Sep, before the
    reset", because the projection was checked first and a date that had already
    passed read like one still ahead. A spent reading whose own reset has passed says
    nothing of the plan after it: rehearsing 2026-10-01, the scheduled check paged
    credits exhausted from a reading taken before the plan reset, on the desktop, the
    phone and in four mails in a day while the usage could not be read again (OCT1-FIXES).
    """
    from .watchdog import credit_reset
    now = time.time() if now is None else now
    # Which account, since a switch leaves another account's figures behind (#36).
    whose = (f"account {last['account']}" if last and last.get("account")
             else "account not named by the reading")
    reset = credit_reset(last.get("reset_at"), now) if last else None
    if last and last.get("limit") and float(last.get("used") or 0) >= float(last["limit"]) \
            and not (reset is not None and reset <= now):
        return ("credits-exhaust", f"credits exhausted at the last reading: "
                                   f"{float(last['used']):.0f}/{float(last['limit']):.0f} ({whose})")
    if br and br.get("before_reset"):
        return ("credits-exhaust", f"credits run out {time.strftime('%d %b', time.localtime(br['exhausts_at']))}, "
                                   f"before the reset ({br['per_day']:.0f}/day, account {br.get('account')}) "
                                   "— new account or fewer features")
    return None


def _doctor_credit_lines(cfg):
    """`ao doctor`'s credits lines, read from the implementer's own readings (ACCOUNT-READERS).

    Where the figure stood, an implementer whose adapter declares no account ao can read is
    told so: no credit alarm can ring for it, and a check that cannot exist must not look
    like one that passes. A blind check is shown only for the implementer's own account.
    """
    own = _implementer_credits(cfg)
    if not own["readable"]:
        return [f"credits         {C['dim']}{_no_account(own['adapter'])}{C['reset']}"]
    lines, br = [], own["rate"]
    last = (own["samples"] or [None])[-1]
    # A reading already over the limit is exhausted; a date ahead would say otherwise.
    over = bool(last and last.get("limit") and float(last.get("used") or 0) >= float(last["limit"]))
    if br and not over:
        when = time.strftime('%d %b', time.localtime(br['exhausts_at'])) if br['exhausts_at'] else '—'
        tone = C['red'] if br['before_reset'] else C['green']
        lines.append(f"credits         {br['used']:.0f}/{br['limit']:.0f} · {br['per_day']:.0f}/day · runs out "
                     f"{tone}{when}{C['reset']}"
                     + (f"  {C['red']}before the reset — new account / ao features off{C['reset']}"
                        if br['before_reset'] else ""))
    if (over or not br) and last and last.get("limit"):
        used, limit = float(last.get("used") or 0), float(last["limit"])
        lines.append(f"credits         {C['red'] if used >= limit else C['green']}{used:.0f}/{limit:.0f}{C['reset']} "
                     "at the last reading" + (f"  {C['red']}exhausted{C['reset']}" if used >= limit else ""))
    try:
        from .watchdog import load_state as _load_state
        blind = (_load_state(cfg["root"]) or {}).get("credit_check_problem")
    except Exception:
        blind = None
    if blind and blind.get("adapter") == own["adapter"]:
        lines.append(f"credits check   {C['red']}cannot read usage{C['reset']} — {blind.get('reason')}  "
                     f"{C['dim']}the exhaustion alarm is blind until it reads again{C['reset']}")
    return lines


# Findings that mean work has stopped and only a person can restart it. Everything
# else a doctor finds is an advisory: recorded for the architect, never paged (#40).
DOCTOR_RED = {"watchdog-dead", "wake-failed", "transcript-blind", "transcript-missing", "implementer-session"}


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

    A condition the watchdog raises itself is raised under the watchdog's own alarm
    (#106). While the watchdog's first cycle after its job came back is due, neither
    that cycle's conditions nor its silence are paged: that cycle names them (NOTICE-NOISE).
    """
    from .watchdog import credit_reset, credits_told, notify
    root = cfg["root"]
    project = A.project_key(root)
    due = _watchdog_first_cycle_due(root) if page else None
    problems = doctor_problems(cfg)
    if not problems:
        print(f"ok {time.strftime('%H:%M')} — no problems")
        return 0
    samples = A.credit_samples(root, A.implementer_adapter_id(cfg)) if page else []
    last = samples[-1] if samples else {}
    for key, text in problems:
        print(f"PROBLEM {key}: {text}")
        if not page:
            continue
        if _doctor_severity(key, text) != "red":
            notify(f"{project}: {key}", text, root, key=f"doctor:{key}", window=3600,
                   audience="architect")
            continue
        shared = DOCTOR_WATCHDOG_ALARMS.get(key)
        if due and (shared or key == "watchdog-dead"):
            print(f"  not paged: the watchdog's job came back with this check; its first cycle is due by "
                  f"{time.strftime('%H:%M', time.localtime(due))}")
        elif key == "credits-exhaust":
            notify(f"{project}: {key}", text, root, key=shared["key"], window=shared["window"],
                   audience="human", level=shared["level"], quiet_until=credit_reset(last.get("reset_at")),
                   what=credits_told("exhausted", last.get("account")))
        elif shared:
            # A failed wake says what the watchdog's own raise of it says, so the same failure rings once
            # whoever sees it (NOISE-REPEATS).
            from .watchdog import STATE_DIR, wake_error, wake_failure_told
            failed = wake_error(os.path.join(STATE_DIR, A.project_file_name("escalate-log", project))) \
                if key == "wake-failed" else None
            notify(f"{project}: {key}", text, root, key=shared["key"], window=shared["window"],
                   audience="human", level=shared["level"], what=wake_failure_told(failed) if failed else None)
        else:
            notify(f"{project}: {key}", text, root, key=f"doctor:{key}", window=3600,
                   audience="human")
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
    ident = A.implementer_adapter_id(cfg)
    if not A.usage_api(ident):
        return [f"{C['dim']}the account: {_no_account(ident)}; what follows is ao's transcript only{C['reset']}"]
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
          f"{span}{' — ' + window if window else ''}){C['reset']}")
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
    # One time syntax for every command (CLI-ROBUST): `yesterday` or `30m` reached a pattern of
    # hours and days here and ended in a traceback.
    when = A.parse_time(args.since) if args.since else None
    since = when.moment() if when else None
    window = when.label() if when else None
    if getattr(args, "features", False):
        return _cost_by_feature(cfg, since, window)
    c = A.turn_costs(cfg, since=since)
    if not c["turns"]:
        print("no transcript"); return 0
    tot = c["total"] or 1
    print(f"{C['b']}implementer spend by turn class{C['reset']}  {C['dim']}({c['unit']}; "
          f"{window or 'whole transcript'}){C['reset']}")
    # What the subagents a turn started spent is inside that turn's spend; a column says how much, when any did.
    delegated = any(b.get("delegated") for b in c["by_class"].values())
    column = f"{'delegated':>11}" if delegated else ""
    print(f"  {'class':<14}{'turns':>6}{'spend':>10}{'share':>7}{column}   {'wasted turns':>12}")
    for cls in ("product", "analysis", "ceremony", "coordination"):
        b = c["by_class"].get(cls)
        if not b:
            continue
        w = f"{b['wasted']} ({b['wasted_usage']:.0f})" if b["wasted"] else ""
        d = f"{b.get('delegated', 0.0):>11.0f}" if delegated else ""
        print(f"  {cls:<14}{b['turns']:>6}{b['usage']:>10.0f}{100 * b['usage'] / tot:>6.0f}%{d}   {w:>12}")
    d = f"{'':>7}{sum(b.get('delegated', 0.0) for b in c['by_class'].values()):>11.0f}" if delegated else ""
    # A turn the model never answered is the implementer's work in no class, and in no total of turns.
    work = [b for cls, b in c["by_class"].items() if cls != A.UNANSWERED]
    print(f"  {'total':<14}{sum(b['turns'] for b in work):>6}{tot:>10.0f}{d}")
    unanswered = c["by_class"].get(A.UNANSWERED)
    if unanswered:
        print(f"  {C['dim']}{A.UNANSWERED}: {unanswered['turns']} more turn(s) the model never answered, the harness "
              f"replying in its place with nothing spent{C['reset']}")
    if delegated:
        print(f"  {C['dim']}delegated: what the subagents a turn started spent, inside that turn's spend{C['reset']}")
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
    """(what a person states for this run, or None; why the command is refused, or None).

    A person may name the family that wrote the waived ranges (--author-family) and the
    waived slices that were pure moves (--move-only). Either is a person's statement. It is
    recorded with what it decides, beside what ao can check about who made it - the login
    and whether a terminal was attached - as a waiver is. A statement of moves closes
    nothing by itself: a waiver closes only on the move proof, run on what landed (#44).
    """
    family = str(getattr(args, "author_family", None) or "").strip().lower()
    named = getattr(args, "move_only", None)
    moves = list(dict.fromkeys(part.strip() for part in str(named or "").split(",") if part.strip()))
    by = str(getattr(args, "by", None) or "").strip()
    if named is not None and (not moves or not all(move.isprintable() and len(move) <= 200 for move in moves)):
        return None, "--move-only names waived slices as the board writes them, separated by commas"
    if not family and not moves:
        return None, ("--by names the person who states --author-family or --move-only; give one with it"
                      if by else None)
    if family and not A.re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,63}", family):
        return None, "--author-family is one family name, such as the lowercase id its model family goes by"
    if not by:
        return None, ("--by is required with --author-family: name the person who says which family wrote the ranges"
                      if family else "--by is required with --move-only: name the person who says those slices "
                                     "only moved code")
    if by.lower() in _agent_names(cfg):
        what = "naming the author's family" if family else "saying which slices only moved code"
        return None, f"--by names an agent or a role ({by}); {what} is a person's statement"
    user, interactive = A._login_and_terminal()
    return {"family": family or None, "move_only": moves, "by": by, "user": user, "interactive": interactive}, None


def _catchup_author(item, statement):
    """What a waived range's review is told about who wrote it: its grant's record, and a person's statement."""
    recorded = item.get("author") if isinstance(item.get("author"), dict) else {}
    author = {key: recorded.get(key) for key in ("role", "actor", "adapter")}
    stated = {key: statement[key] for key in ("family", "by", "user", "interactive")} \
        if statement and statement.get("family") else None
    author.update(family=str(recorded.get("family") or "").strip().lower() or None,
                  grant=item.get("grant"), stated=stated)
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
        return "no reviewer is configured" + _waiting_reviewer(cfg)
    refused = _reviewer_ineligible(cfg, reviewer, author=author)
    return f"the reviewer may not review it: {refused}" if refused else None


def _waiting_reviewer(cfg):
    """'; <actor> is assigned the reviewer role and holds it once <slice> leaves running', or '' (OCT1-FIXES)."""
    waiting, after = A.pending_roles(cfg.get("root"), cfg)
    return (f"; {waiting['reviewer']} is assigned the reviewer role and holds it once {after} leaves running"
            if waiting.get("reviewer") else "")


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


def _catchup_undecided(root, targets):
    """{waiver id: verdict} for each waived range whose newest review of exactly that range decided nothing.

    A review that ends UNAVAILABLE or INVALID keeps its waiver open, and the next run met
    that waiver first again: runs of --limit N whose first N reviews kept failing reviewed
    nothing else, however often they were repeated (OCT1-FIXES). A ledger that cannot be
    read orders nothing; the review that needs it says so.
    """
    from .storage import read_chained_jsonl
    try:
        rows = read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)
    except Exception:
        return {}
    newest = {row.get("commits"): row.get("verdict") for row in rows
              if isinstance(row, dict) and row.get("kind") == "commit-range"}
    ranges = {item["waiver"].get("id"): f"{item['start']}..{item['end']}" for item in targets if item.get("landed")}
    return {wid: newest[rng] for wid, rng in ranges.items() if newest.get(rng) in ("UNAVAILABLE", "INVALID")}


def _catchup_exit(failed, started, decided):
    """The exit code of a catch-up run: what failed, reviews that decided nothing, or 0 (CATCHUP-POLISH).

    Rehearsing the catch-up planned for 2026-10-01, a run whose every review ended
    UNAVAILABLE exited 0, as a run that closed ten waivers did, so a person or a script
    repeating --limit could not tell a reviewer that decides nothing from progress. 1 stays
    what could not be written or read. 3 is reviews started and none deciding anything,
    whatever else closed, as `ao review` exits 3 when no reviewer could review. Anything else
    is 0: progress, or nothing to do, since an idle catch-up is no failure and a wake turn, a
    `set -e` script and a person all read a nonzero exit as one.
    """
    if failed:
        return 1
    return 3 if started and not decided else 0


def cmd_catchup(cfg, args):
    """Replay what could not run: waived reviews, deferred wakes and nudges.

    A waived range is reviewed by a model family other than the one that wrote it (#65):
    the family the grant under the waiver recorded, and any a person names with
    --author-family and --by. With neither, its review is refused and the waiver stays
    open. The reviewer judges the range against what its commit messages claim, unless a
    person gives --boundary, and the review is recorded as the waived slice's. A range whose
    grant recorded a move proof, or whose slice a person names with --move-only and --by, is
    not reviewed: the proof runs on what landed, and the waiver closes on it or stays open
    with the reason (#44). A range with neither is reviewed, whatever its diff looks like.
    --plan says what a run would do and changes nothing; --slice and --limit bound a run. A
    review stays synchronous: a waiver closes in the same run, on the review recorded for
    exactly its range, or stays open. A range whose last review decided nothing waits behind
    the rest, and once a review finds the reviewer unavailable the run starts no other. --limit
    bounds the reviews a run starts in the same way: what needs no review is still done.

    The exit code says what a run did (CATCHUP-POLISH): 3 when it started reviews and none of
    them decided anything, whatever else it closed; 1 when a close, a ledger or a decision
    request could not be written or read; 2 when it is refused; otherwise 0, for progress and
    for nothing to do alike. --plan exits 0, or 1 when the waivers cannot be read.
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
    did = started = decided = held = waiting = 0
    unavailable = None                          # the waiver whose review found the reviewer unavailable
    failed = []
    totals = {"waivers": 0, "commits": 0, "lines": 0, "unnamed": 0, "proven": 0}      # what --plan sums up

    def close(wid, outcome, evidence=None):
        # A waiver retires on a durable append or not at all; a failure is reported
        # and fails the command, never counted as handled (#67).
        try:
            A.close_waiver(root, wid, outcome, evidence=evidence)
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
    moves = (statement or {}).get("move_only") or []
    open_slices = {item["waiver"].get("slice") for item in targets}
    # A ledger that cannot be read says nothing about which slices it names (OCT1-FIXES).
    for name in [] if "ledger" in failed else moves:
        # A statement about a slice with nothing to prove is reported, never dropped quietly.
        if name not in open_slices:
            print(f"  --move-only {name}: no open review waiver names it; nothing is proven or closed")
        elif only and name != only:
            print(f"  --move-only {name}: --slice {only} bounds this run; it waits for another")
    if only:
        targets = [item for item in targets if item["waiver"].get("slice") == only]
    undecided = _catchup_undecided(root, targets)
    targets = sorted(targets, key=lambda item: item["waiver"].get("id") in undecided)
    for item in targets:
        w = item["waiver"]
        label = f"{w['id']} ({w['slice']})"
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
        granted_proof = item.get("move_only")
        stated = w.get("slice") in moves
        if granted_proof or stated:
            # A semantic review of a proven pure move reads nothing the proof did not. Its grant
            # recorded the proof for the tree it granted, or a person states the slice only moved
            # code; either way what landed is proven here, and the waiver closes on that proof
            # alone, with no reviewer, or stays open. A statement never closes one by itself (#44).
            claimed = " and ".join(part for part in ("its grant recorded a pure move" if granted_proof else "",
                                                     f"{statement['by']} states it is a pure move" if stated else "")
                                   if part)
            moved, problems = A.range_move_proof(root, item["start"], item["end"])
            if problems:
                print(f"  {label}: {rng}, {size}: {claimed}, and the landed range is not one: "
                      f"{'; '.join(problems[:3])}" + (f" and {len(problems) - 3} more" if len(problems) > 3 else "")
                      + "; keeping it open")
                continue
            totals["proven"] += 1
            proof = {"proof": "split-moves", "commits": f"{item['start']}..{item['end']}", "grant": item.get("grant"),
                     "moved": len(moved), "paths": sorted({f"{source} -> {target}" for _, source, target in moved})}
            if granted_proof:
                proof["recorded"] = granted_proof
            if stated:
                proof["stated"] = {key: statement[key] for key in ("move_only", "by", "user", "interactive")}
            proven = f"{len(moved)} definition(s) moved byte for byte on the landed range"
            if plan:
                print(f"  {label}: {rng}, {size}: {claimed}; closes by proof, {proven}; no reviewer")
            elif close(w["id"], f"closed by proof: {proven}; {claimed}", proof):
                print(f"  {label}: {rng}, {size}: {claimed}; {proven}; closed by proof, with no reviewer")
                did += 1
            continue
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
        if unavailable:
            # Every review after one that found the reviewer unavailable waits on it too; each
            # could spend the review's whole time budget finding that out again (OCT1-FIXES).
            held += 1
            continue
        if limit is not None and started >= limit:
            # --limit bounds the reviews a run starts, as an unavailable reviewer does, and nothing else. The
            # run stopped at the limit instead, and said a waiver nothing was granted under waited for the
            # next run, which would close it unreviewed (CATCHUP-POLISH).
            waiting += 1
            continue
        started += 1
        totals["commits"] += item["landed"]
        totals["lines"] += lines or 0
        last = (f"; its last review ended {undecided[w['id']]}, so it comes after the ranges no review has "
                "failed on") if w["id"] in undecided else ""
        if plan:
            note = _catchup_reviewer_note(cfg, author)
            print(f"  {label}: {rng}, {size}: review by a family other than {', '.join(families)}"
                  + (f"; {note}" if note else "") + last)
            continue
        print(f"  {label}: reviewing its own landed range {rng}, {size}, by a family other than {', '.join(families)}"
              + last)
        # No deadline is passed: a reviewer's time is review_timeout per call, and a silent
        # one is killed after review.stall_minutes (#63, #25). The range's commit messages
        # are the statement it is judged against; a person's --boundary replaces them.
        ns = SimpleNamespace(boundary=args.boundary or f"waived review for {w['slice']}: {w['why']}",
                             paths=None, commits=f"{item['start']}..{item['end']}", author=author,
                             claims=not args.boundary, range_slice=w["slice"])
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
        if verdict in ("APPROVED", "NEEDS_CHANGES") or (verdict is None and code == 0):
            decided += 1                        # the review decided what becomes of the waiver
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
        elif verdict == "INVALID":
            # An INVALID review exits 3 as an unavailable one does: the verdict tells them apart (OCT1-FIXES).
            print(f"  the reviewer returned no valid verdict; {w['id']} stays open")
        elif code == 3 or verdict == "UNAVAILABLE":
            print(f"  reviewer still unavailable; {w['id']} stays open")
            if verdict == "UNAVAILABLE":
                unavailable = w["id"]
        elif code == 2:
            print(f"  reviewer configuration invalid, or the range cannot be reviewed; {w['id']} stays open")
        else:
            print(f"  no review was recorded (exit {code}); {w['id']} stays open")
    if held:
        print(f"  the reviewer was unavailable for {unavailable}: this run started no other review, and "
              f"{held} waiver(s) wait for a run in which it answers")
    if waiting:
        print(f"  --limit {limit}: {waiting} waiver(s) wait for a later run to review them")
    if plan:
        print(f"totals: {totals['waivers']} waiver(s); {started} review(s) of {totals['commits']} commit(s) and "
              f"{totals['lines']} changed line(s); {totals['unnamed']} refused until a person names the author's "
              f"family; {totals['proven']} closed by proof, with no reviewer; "
              f"{len(A.deferred_open(root))} deferred wake(s) and nudge(s) a run replays")
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
    code = _catchup_exit(failed, started, decided)
    print(f"{C['green']}catchup{C['reset']} handled {did} item(s)"
          + (f"; none of the {started} review(s) it started decided anything" if code == 3 else ""))
    return code


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
