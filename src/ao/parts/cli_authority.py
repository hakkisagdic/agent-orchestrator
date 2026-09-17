"""Authority: verify, merge checks, commit-ok, the project marker, commit and commit-check.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


def _launchd_path():
    """The PATH a launchd job runs with, fixed when the job is installed.

    launchd starts jobs with /usr/bin:/bin:/usr/sbin:/sbin, which does not reach
    ~/.local/bin, where kiro-cli, ao and the watchdog live; the credit sampler ran
    blind for its whole life because of it. Take the watchdog's child PATH, keep
    directories that exist, and drop per-shell version-manager directories that
    disappear when the shell that created them exits.
    """
    from xml.sax.saxutils import escape
    from .watchdog import child_path
    seen, keep = set(), []
    for d in child_path().split(os.pathsep):
        if not d or d in seen or "fnm_multishells" in d or not os.path.isdir(d):
            continue
        seen.add(d)
        keep.append(d)
    return escape(os.pathsep.join(keep))


def _launchctl(*args, merge=False):
    """(what `launchctl <args>` printed, its exit status), asked without a shell.

    Through a shell the user's id came from `$(id -u)`, and the label and the plist path
    were shell text: a project directory named with a space, a `*` or a `;` reached
    launchctl as other words, or ran as a command. Its standard error is discarded, as the
    shell discarded it, unless `merge` keeps it in the answer as `2>&1` did.
    """
    return A._run_program(["launchctl", *args], stderr=subprocess.STDOUT if merge else subprocess.DEVNULL)


def _launchd_domain(label=None):
    """The user's launchd domain, `gui/<uid>`, or one job's target in it."""
    return f"gui/{os.getuid()}" + (f"/{label}" if label else "")


def _launchd_listed(label):
    """The lines of `launchctl list` that name `label`, as `launchctl list | grep <label>` printed them.

    Matched as text rather than as grep's pattern, which a `.` in every label already
    widened and a `*` or `[` in a project's name turned into one that matched nothing.
    """
    return "\n".join(line for line in _launchctl("list")[0].split("\n") if label in line).strip()

PLIST_CMD = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>{python_arg}<string>{script}</string>
    <string>--root</string><string>{root}</string>
    <string>--idle-minutes</string><string>{idle}</string></array>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def _judge_gate(g, out, code):
    """(passed, detail, counts) for one gate's output and exit code, as `ao verify` judges it."""
    counts = None

    if g.get("expect") == "empty":
        passed = not out.strip()
        detail = "clean" if passed else " ".join(out.split())[:120]
    else:
        # The exit code decides; counts come only from the runner's closing
        # summary, never from the first match in output the implementer controls (#70).
        passed = code == 0
        counts = A.gate_counts(out, g.get("summary"))
        if counts:
            p_, f_ = counts
            detail = f"{p_}/{p_ + f_}"
            passed = passed and f_ == 0
            # Exit zero is not proof that anything ran. A test runner whose
            # worker pool fails can collect zero tests and exit 0, and the
            # gate stays green having measured nothing — the second pilot's
            # agent found vitest doing exactly that. A gate that declares
            # `min_tests` must see at least that many actually execute.
            need = int(g.get("min_tests", 0) or 0)
            if need and (p_ + f_) < need:
                passed = False
                detail += f" — only {p_ + f_} ran, {need} required"
        elif g.get("min_tests"):
            passed = False
            detail = (f"exit {code}, but the runner's closing summary was not found "
                      f"and min_tests={g['min_tests']}")
        else:
            detail = f"exit {code}"
    return passed, detail, counts


def cmd_merge_check(cfg, args):
    """Run a profile's gates on the result of a merge before it is made, and record the run (#39).

    2026-09-07: four branches, each green on its own, were merged and main went
    red - one changed a signature another still called. Hosted CI runs only when
    dispatched by hand, so nothing caught it but a suite run afterwards. The merge
    result is computed by git, checked out into a temporary worktree and gated
    there; the record names both parents and the merged tree, so it vouches for
    exactly that merge and no later one.
    """
    import shutil
    import tempfile
    root = cfg["root"]
    try:
        with open(os.path.join(root, ".ao", "gates.json"), encoding=UTF8) as fh:
            spec = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"{C['red']}no usable .ao/gates.json{C['reset']}: {exc}")
        return 2
    profiles = spec.get("profiles") or {}
    profile = args.profile or ("full" if "full" in profiles else spec.get("default_profile", "quick"))
    names = profiles.get(profile)
    if not names:
        print(f"unknown profile {profile}; have: {', '.join(profiles)}")
        return 2
    into = A.git_text(root, "rev-parse", "--verify", "--quiet", f"{args.into}^{{commit}}")
    branch = A.git_text(root, "rev-parse", "--verify", "--quiet", f"{args.branch}^{{commit}}")
    if not into or not branch:
        print(f"{C['red']}cannot resolve {args.branch if into else args.into} to a commit{C['reset']}")
        return 2
    try:
        tree, conflict = A.merge_result_tree(root, into, branch)
    except RuntimeError as exc:
        print(f"{C['red']}{exc}{C['reset']}")
        return 2
    row = {"id": f"MC-{A.unique_ms()}", "at": int(time.time()), "into": into, "branch": branch,
           "tree": tree, "profile": profile, "gates_digest": A.gate_definitions_digest_of(spec, profile),
           "measured_by": A.measured_by()}
    if conflict:
        row.update(passed=False, gates=[], conflict=conflict)
        A.record_merge_check(root, row)
        print(f"{C['red']}{C['b']}CONFLICT{C['reset']}  {conflict}  {C['dim']}recorded as {row['id']}{C['reset']}")
        return 1
    holder = A.gate_lock_holder()
    if holder and holder.get("root") != root:
        if not A.acquire_gate_lock(root, args.wait):
            print(f"{C['red']}{os.path.basename(holder['root'])} is running its gates; not starting a second "
                  f"suite{C['reset']}")
            return 2
    else:
        A.acquire_gate_lock(root, 0)
    scratch = tempfile.mkdtemp(prefix="ao-merge-check-")
    result_dir = os.path.join(scratch, "result")
    links, results, ok = [], [], True
    try:
        added = subprocess.run([A.git_binary(), "worktree", "add", "--detach", "--quiet", result_dir, into],
                               cwd=root, capture_output=True)
        checked_out = added.returncode == 0 and subprocess.run(
            [A.git_binary(), "read-tree", "-u", "--reset", tree], cwd=result_dir, capture_output=True).returncode == 0
        if not checked_out:
            print(f"{C['red']}could not check out the merge result{C['reset']}: "
                  f"{added.stderr.decode(UTF8, 'replace').strip()}")
            return 2
        # Dependencies are installed per checkout, not tracked; the result borrows the project's.
        for name in S.get(cfg, "merge.link_paths"):
            source, target = os.path.join(root, name), os.path.join(result_dir, name)
            if os.path.exists(source) and not os.path.lexists(target):
                try:
                    os.symlink(source, target)
                    links.append(target)
                except OSError:
                    pass
        print(f"{C['b']}merge {args.branch} ({branch[:12]}) into {args.into} ({into[:12]}){C['reset']}  "
              f"{C['dim']}result tree {tree[:12]}, profile {profile}{C['reset']}")
        for name in names:
            g = spec["gates"][name]
            print(f"{C['dim']}▶ {name}{C['reset']}  {g['run']}")
            started = time.time()
            try:
                r = subprocess.run(g["run"], shell=True, cwd=result_dir, capture_output=True, text=True,
                                   encoding=UTF8, errors="replace",
                                   timeout=g.get("timeout") or S.get(cfg, "gates.default_timeout"))
                out, code = (r.stdout + r.stderr), r.returncode
            except subprocess.TimeoutExpired:
                out, code = "timed out", 124
            passed, detail, counts = _judge_gate(g, out, code)
            ok = ok and passed
            print(f"  {C['green'] + 'pass' if passed else C['red'] + 'FAIL'}{C['reset']}  {detail}")
            results.append({"name": name, "passed": passed, "detail": detail, "exit": code,
                            "seconds": int(time.time() - started), "run": g["run"],
                            "summary": None if g.get("expect") == "empty"
                            else A.gate_summary_line(out, g.get("summary")),
                            "counts": {"pass": counts[0], "fail": counts[1]} if counts else None})
    finally:
        for link in links:
            try:
                os.unlink(link)
            except OSError:
                pass
        subprocess.run([A.git_binary(), "worktree", "remove", "--force", result_dir], cwd=root, capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)
        subprocess.run([A.git_binary(), "worktree", "prune"], cwd=root, capture_output=True)
        A.release_gate_lock()
    row.update(passed=ok, gates=results, conflict=None)
    A.record_merge_check(root, row)
    print(f"\n{C['b']}{'PASS' if ok else 'FAIL'}{C['reset']}  recorded as {row['id']}; it vouches for a merge of "
          f"these two parents whose tree is {tree[:12]}")
    return 0 if ok else 1


def cmd_verify(cfg, args):
    """Run the project's declared gates ourselves and record what we measured.

    The whole point of a second agent is not needing to trust the first one's
    report, so this executes the commands and writes the numbers to the ledger.
    Commit authority is later granted against this record, not against a claim.
    """
    import json as _json
    import subprocess
    root = cfg["root"]
    _urgent_banner(cfg)
    gates_file = os.path.join(root, ".ao", "gates.json")
    holder = A.gate_lock_holder()
    if holder and holder.get("root") != root:
        print(f"{C['yellow']}{os.path.basename(holder['root'])} is running its gates"
              f"{C['reset']} {C['dim']}({holder['minutes']}m){C['reset']} — waiting up to "
              f"{args.wait}s so two suites do not fight for one machine")
        if not A.acquire_gate_lock(root, args.wait):
            print(f"{C['red']}still busy; not starting a second suite{C['reset']}")
            return 2
    else:
        A.acquire_gate_lock(root, 0)
    if not os.path.exists(gates_file):
        print(f"{C['yellow']}No .ao/gates.json — nothing declared to verify.{C['reset']}")
        print(f"{C['dim']}See docs/gates.md for the shape.{C['reset']}")
        A.release_gate_lock()
        return 1
    spec = _json.load(open(gates_file, encoding=UTF8))
    profile = args.profile or spec.get("default_profile", "quick")
    names = spec.get("profiles", {}).get(profile)
    if not names:
        print(f"unknown profile {profile}; have: {', '.join(spec.get('profiles', {}))}")
        A.release_gate_lock()
        return 1
    # The record names what ran, so a later edit to gates.json cannot inherit
    # this result (#61).
    gates_digest = A.gate_definitions_digest_of(spec, profile)

    try:
        candidate_before = A.index_candidate(root)
        candidate_issues_before = A.candidate_worktree_issues(root, cfg, candidate_before)
    except RuntimeError as exc:
        A.release_gate_lock()
        print(f"{C['red']}{exc}{C['reset']}")
        return 2
    candidate_messages = A.candidate_issue_messages(candidate_issues_before)
    results, ok = [], not candidate_messages
    if candidate_messages:
        detail = "; ".join(candidate_messages)
        print(f"{C['red']}FAIL{C['reset']}  staged candidate is not isolated: {detail}")
        results.append({"name": "candidate-isolation", "passed": False,
                        "detail": detail, "exit": 1, "seconds": 0})

    for name in names:
        g = spec["gates"][name]
        print(f"{C['dim']}▶ {name}{C['reset']}  {g['run']}")
        started = time.time()
        try:
            r = subprocess.run(g["run"], shell=True, cwd=root, capture_output=True,
                               text=True, encoding=UTF8, errors="replace",
                               timeout=g.get("timeout") or S.get(cfg, "gates.default_timeout"))
            out, code = (r.stdout + r.stderr), r.returncode
        except subprocess.TimeoutExpired:
            out, code = "timed out", 124
        took = int(time.time() - started)
        passed, detail, counts = _judge_gate(g, out, code)
        ok = ok and passed
        mark = f"{C['green']}pass{C['reset']}" if passed else f"{C['red']}FAIL{C['reset']}"
        print(f"  {mark}  {detail}  {C['dim']}{took}s{C['reset']}")
        if not passed:
            tail = " ".join(out.strip().split("\n")[-4:])[:400]
            print(f"  {C['dim']}{tail}{C['reset']}")
        results.append({"name": name, "passed": passed, "detail": detail,
                        "exit": code, "seconds": took, "run": g["run"],
                        # The runner's own closing line, for reports to quote (#6).
                        "summary": None if g.get("expect") == "empty"
                        else A.gate_summary_line(out, g.get("summary")),
                        "counts": None if g.get("expect") == "empty" or not counts
                        else {"pass": counts[0], "fail": counts[1]}})

    try:
        candidate_after = A.index_candidate(root)
        candidate_issues_after = A.candidate_worktree_issues(root, cfg, candidate_after)
        after_messages = A.candidate_issue_messages(candidate_issues_after)
    except RuntimeError as exc:
        candidate_after = None
        after_messages = [str(exc)]
    candidate_changed = not candidate_after or \
        candidate_after["digest"] != candidate_before["digest"]
    integrity = []
    if candidate_changed:
        integrity.append("index changed while gates were running")
    for message in after_messages:
        if message not in candidate_messages:
            integrity.append(message)
    if integrity:
        ok = False
        detail = "; ".join(integrity)
        print(f"\n  {C['red']}FAIL{C['reset']}  candidate integrity: {detail}")
        results.append({"name": "candidate-integrity", "passed": False,
                        "detail": detail, "exit": 1, "seconds": 0})
    candidate_ready = bool(candidate_before["changed_paths"]) and \
        not candidate_messages and not after_messages and not candidate_changed

    # A plan the implementer edited is a plan that no longer measures anything:
    # the work and the standard it is judged by came from the same hand. Treat it
    # as a failed gate, because that is what it is.
    try:
        drift = A.plan_drift(root)
    except Exception as exc:
        ok = False
        drift = []
        print(f"\n  {C['red']}FAIL{C['reset']}  plan baselines cannot be read: {exc}")
        results.append({"name": "plan-integrity", "passed": False,
                        "detail": f"unreadable: {exc}"[:200], "exit": 1, "seconds": 0})
    if drift:
        ok = False
        print(f"\n  {C['red']}FAIL{C['reset']}  plan changed after admission: "
              f"{', '.join(drift)}")
        print(f"  {C['dim']}the implementer reads its plan; it does not write to it{C['reset']}")
        results.append({"name": "plan-integrity", "passed": False,
                        "detail": f"drifted: {','.join(drift)}", "exit": 1, "seconds": 0})

    revs = A.reviews(root, cfg["reviews"], limit=1)
    rec = {"id": f"V-{int(time.time())}", "at": datetime.now().isoformat(timespec="seconds"),
           "schema": 2, "profile": profile, "gates_digest": gates_digest,
           "by": "ao verify", "passed": ok,
           "gates": results, "plan_drift": drift,
           "candidate": candidate_before,
           "candidate_after": candidate_after,
           "candidate_ready": candidate_ready,
           "candidate_issues": candidate_messages + [
               message for message in after_messages if message not in candidate_messages
           ],
           # Keep the worktree digest for telemetry and legacy readers. Commit
           # authority is bound to the immutable index candidate above.
           "tree": A.tree_digest(root, cfg),
           "review": revs[0][0] if revs else None,
           "review_verdict": revs[0][1] if revs else None,
           "head": A._git_text(root, "rev-parse", "--short", "HEAD"),
           # Size by kind, never one number (#34).
           "candidate_size": _candidate_size_or_none(root, candidate_before),
           # Which git measured the candidate, and that no shell or agent stood between (#51).
           "measured_by": A.measured_by(),
           "dirty": len([l for l in A._git_text(root, "status", "--short").split("\n") if l.strip()])}
    try:
        A.record_verification(root, rec)
    except Exception as exc:
        print(f"{C['red']}{C['b']}NOT RECORDED{C['reset']}  verification ledger: {exc}")
        return 2
    finally:
        A.release_gate_lock()

    print(f"\n{C['b']}{'PASS' if ok else 'FAIL'}{C['reset']}  recorded as {rec['id']}")
    if revs:
        col = C["green"] if "APPROVED" in (revs[0][1] or "").upper() else C["yellow"]
        print(f"{C['dim']}newest review:{C['reset']} {col}{revs[0][1]}{C['reset']} ({revs[0][0]})")
    return 0 if ok else 1


def _candidate_size_or_none(root, candidate):
    try:
        return A.candidate_size(root, candidate)
    except (RuntimeError, KeyError, TypeError):
        return None


def _latest_verification_or_problem(root):
    """The newest verification, or why the chained ledger cannot be read (#61)."""
    try:
        return A.latest_verification(root), None
    except Exception as exc:
        return None, str(exc)


def cmd_commit_ok(cfg, args):
    """Grant authority only for the exact, isolated tree in Git's index."""
    root = cfg["root"]
    strict = M.is_strict(cfg)
    matrix_resolution = None
    if strict:
        try:
            # A waiver may bypass a review, but never malformed declarations or
            # a runtime implementer that disagrees with its bound identity.
            matrix_resolution = M.resolve(cfg, require_independent=False)
        except M.MatrixError as exc:
            print(f"{C['red']}{C['b']}REFUSED{C['reset']}")
            for problem in exc.problems:
                print(f"  {C['red']}·{C['reset']} capability matrix: {problem}")
            return 1
    try:
        candidate = A.index_candidate(root)
        issues = A.candidate_worktree_issues(root, cfg, candidate)
    except RuntimeError as exc:
        print(f"{C['red']}{C['b']}REFUSED{C['reset']}\n  {C['red']}·{C['reset']} {exc}")
        return 1
    wanted = getattr(args, "review", None)
    if wanted:
        # A submitted review grants only the tree it pinned (S1).
        state = _review_state(root, wanted)
        refusal = (f"no submitted review {wanted}" if not state
                   else f"{wanted} is {state.get('state')}, not finished" if state.get("state") != "finished"
                   else f"the staged tree is not the tree {wanted} reviewed: staged {candidate['index_tree']}, "
                        f"reviewed {state.get('tree')}" if state.get("tree") != candidate["index_tree"]
                   else None)
        if refusal:
            print(f"{C['red']}{C['b']}REFUSED{C['reset']}\n  {C['red']}·{C['reset']} {refusal}")
            return 1
    now = A.tree_digest(root, cfg)       # legacy/audit surface, not commit identity
    ver, ver_problem = _latest_verification_or_problem(root)

    def verification_matches(record):
        measured = (record or {}).get("candidate") or {}
        return bool(record and record.get("passed") and record.get("candidate_ready")
                    and measured.get("digest") == candidate["digest"])

    if getattr(args, "verify", False) and not verification_matches(ver):
        from types import SimpleNamespace
        print(f"{C['dim']}verification stale or missing — running quick gates first{C['reset']}")
        cmd_verify(cfg, SimpleNamespace(
            profile=getattr(args, "profile", None) or "quick", wait=900
        ))
        candidate = A.index_candidate(root)
        issues = A.candidate_worktree_issues(root, cfg, candidate)
        now = A.tree_digest(root, cfg)
        ver, ver_problem = _latest_verification_or_problem(root)

    reasons = []
    if not candidate["changed_paths"]:
        reasons.append("no staged candidate — stage exactly what you intend to commit")
    reasons.extend(A.candidate_issue_messages(issues))
    if issues.get("worktree_noise"):
        # Listed, never fatal: no declared gate reads these paths (#16).
        print(f"{C['dim']}worktree noise, outside the candidate and every gate's inputs: "
              f"{', '.join(issues['worktree_noise'])}{C['reset']}")
    if ver_problem:
        reasons.append(f"verification ledger is unreadable: {ver_problem}")
    elif not ver:
        reasons.append("no verification record — run `ao verify`")
    else:
        if not ver.get("passed"):
            failed = [g["name"] for g in ver.get("gates", []) if not g.get("passed")]
            reasons.append(f"last verification failed: {', '.join(failed) or ver['id']}")
        measured = ver.get("candidate")
        if not measured:
            reasons.append(f"{ver['id']} predates index-candidate binding — re-run `ao verify`")
        elif measured.get("digest") != candidate["digest"]:
            reasons.append(
                f"index changed since {ver['id']} — expected {measured.get('index_tree')}, "
                f"got {candidate['index_tree']}"
            )
        elif not ver.get("candidate_ready"):
            detail = "; ".join(ver.get("candidate_issues") or [])
            reasons.append(f"{ver['id']} did not verify an isolated staged candidate"
                           + (f": {detail}" if detail else ""))
        if A.gate_definitions_digest(root, ver.get("profile")) != ver.get("gates_digest"):
            reasons.append(f"gate definitions changed since {ver['id']} — re-run `ao verify`")

    try:
        drift = A.plan_drift(root)
    except Exception as exc:
        drift = []
        reasons.append(f"plan baselines cannot be read: {exc}")
    # A slice the board declares move-only moves text and changes nothing (#44).
    move_only = [it["id"] for it in A.board(root)["running"] if "move-only" in (it.get("notes") or {})]
    if move_only:
        try:
            problems = A.split_moves(root)["problems"]
        except RuntimeError as exc:
            problems = [f"the candidate cannot be read: {exc}"]
        reasons.extend(f"{move_only[0]} is move-only: {problem}" for problem in problems)
    if drift:
        reasons.append(f"plan edited after admission: {', '.join(drift)}")

    from . import features as F
    running = [it["id"] for it in A.board(root)["running"]]
    try:
        waiver, waiver_notes = A.review_waiver_for(root, running, candidate["digest"])
    except Exception as exc:
        waiver, waiver_notes = None, [f"waiver ledger is unreadable: {exc}"]
    review_required = F.enabled(cfg, "review")
    try:
        decision = A.candidate_review_decision(root, cfg["reviews"], candidate["digest"])
    except Exception as exc:
        decision = {"match": None, "problem": f"review ledger is unreadable: {exc}"}
    match = decision["match"]
    review_name, rbody, evidence, rwho = None, "", None, None
    strict_reviewer_identity = None
    scope = A.candidate_scope(candidate)
    if not review_required:
        pass
    elif waiver:
        print(f"{C['yellow']}review waived{C['reset']} by {waiver['by']} ({waiver['id']}): "
              f"{waiver['why'][:80]} — reconcile with `ao catchup`")
    elif strict and not any(route["eligible"] for route in matrix_resolution["reviewers"]):
        reasons.append("capability matrix has no independently eligible reviewer")
    elif not match:
        reasons.append(decision["problem"]
                       or "no APPROVED prospective review is bound to this staged candidate")
        reasons.extend(waiver_notes)
    else:
        review_name, _, rbody, evidence = match
        reviewed_scope, integrity_reasons = A.candidate_review_integrity(
            root, candidate, evidence
        )
        reasons.extend(f"{review_name}: {reason}" for reason in integrity_reasons)
        if reviewed_scope is not None:
            scope = reviewed_scope
        if strict:
            reasons.extend(
                f"{review_name}: {reason}"
                for reason in M.review_evidence_problems(matrix_resolution, evidence)
            )
            strict_reviewer_identity = evidence.get("reviewer_identity") \
                if isinstance(evidence, dict) else None
            rwho = (strict_reviewer_identity or {}).get("binding") \
                if isinstance(strict_reviewer_identity, dict) else None
        else:
            # Who reviewed is read from the evidence ao wrote, never from the
            # artefact's text, where the implementer's boundary appears too (#60).
            recorded = evidence.get("reviewer") if isinstance(evidence, dict) else None
            rwho = recorded.get("id") if isinstance(recorded, dict) else None
            if not isinstance(rwho, str) or not rwho:
                rwho = None
                reasons.append(f"{review_name} records no reviewer in its evidence — "
                               "run `ao review` again")
            elif _reviewer_ineligible(cfg, _configured_reviewer(cfg, rwho)) == "it runs as the implementer":
                reasons.append(f"the review was written by the implementer ({rwho})")
            elif _reviewer_ineligible(cfg, _configured_reviewer(cfg, rwho)):
                reasons.append(f"the review's reviewer {rwho} may not review this implementer: "
                               f"{_reviewer_ineligible(cfg, _configured_reviewer(cfg, rwho))}")

    held = A.hold_state(root)
    if held:
        reasons.append(f"project is held by {held.get('by')}: {held.get('reason','')}")
    for message in A.urgent_messages(root, cfg):
        reasons.append(
            f"urgent message unacknowledged: {message['id']} — {message['title']}"
        )

    strict_authority = M.authority_fields(
        matrix_resolution, strict_reviewer_identity
    ) if strict else {}
    if reasons:
        print(f"{C['red']}{C['b']}REFUSED{C['reset']}")
        for reason in reasons:
            print(f"  {C['red']}·{C['reset']} {reason}")
        try:
            A.record_authority(
                root, False, reasons, now, (ver or {}).get("id"),
                candidate=candidate, scope=scope, **strict_authority,
            )
        except Exception as exc:
            # A broken predecessor chain is itself a refusal condition. Never
            # traceback into ambiguity or imply that the refusal was recorded.
            print(f"  {C['dim']}authority refusal could not be recorded: {exc}{C['reset']}")
        return 1

    token = f"C-{A.unique_ns()}"
    try:
        A.record_authority(
            root, True, [], now, ver["id"], token,
            review=review_name, reviewer=rwho, candidate=candidate, scope=scope,
            waiver=waiver["id"] if review_required and waiver else None,
            **strict_authority,
        )
    except Exception as exc:
        # Corruption and lock failures are as decisive as an I/O failure: no
        # durable chained row means no authority exists.
        print(f"{C['red']}{C['b']}REFUSED{C['reset']}")
        print(f"  {C['red']}·{C['reset']} could not persist authority grant: {exc}")
        if "has no 'previous' field" in str(exc):
            print("  legacy ledger — archive .ao/ledger/authority.jsonl and re-run ao commit-ok (docs/ledger.md)")
        return 1

    print(f"{C['green']}{C['b']}GRANTED{C['reset']}  {token}")
    print(f"  {C['dim']}verified{C['reset']} {ver['id']} · "
          f"{C['dim']}review{C['reset']} {review_name or 'waived/off'}")
    print(f"  {C['dim']}index tree{C['reset']} {candidate['index_tree']}")
    print(f"\n  {C['dim']}This grant names only the staged index above; push is never covered.{C['reset']}")
    return 0


PROJECT_MARKER = ".ao-project"
PROJECT_MARKER_BYTES = b"ao-project-v1\n"
PROJECT_ADOPT_HINT = (
    "adopt the marker: printf 'ao-project-v1\\n' > .ao-project && git add .ao-project, "
    "then land that commit through ao commit-ok"
)
PROJECT_INIT_COMMAND = f"ao init --profile {A.default_profile()}"


def _project_refusal(problem):
    return (
        f"{PROJECT_MARKER} marks AO enrollment, but AO project state is "
        f"missing/unreadable: {problem}; run: {PROJECT_INIT_COMMAND}"
    )


def _project_index_env(root, index_file=None):
    """Return Git's active-index override without other inherited bindings."""
    if index_file is None:
        if "GIT_INDEX_FILE" not in os.environ:
            return None, None
        value = os.environ["GIT_INDEX_FILE"]
    else:
        value = index_file
    if not value:
        return None, "GIT_INDEX_FILE is set but empty"
    if not os.path.isabs(value):
        # The Git query below runs with ``cwd=root``. Resolve its relative index
        # exactly there before the sanitized environment is constructed, so a
        # direct ``ao commit-check`` and the installed hook measure the same
        # active candidate.
        value = os.path.abspath(os.path.join(root, value))
    return {"GIT_INDEX_FILE": value}, None


def _marker_blob(root, oid, extra_env):
    result = _hook_git(root, "cat-file", "blob", oid, extra_env=extra_env)
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        return None, detail or f"git cat-file exited {result.returncode}"
    return result.stdout, None


def _index_project_marker(root, index_file=None):
    extra_env, problem = _project_index_env(root, index_file)
    if problem:
        return {"status": "invalid", "detail": problem}
    result = _hook_git(
        root, "ls-files", "--stage", "-z", "--", PROJECT_MARKER,
        extra_env=extra_env,
    )
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        return {
            "status": "invalid",
            "detail": "active index marker query failed: "
                      + (detail or f"git exit {result.returncode}"),
        }
    rows = [row for row in result.stdout.split(b"\0") if row]
    if not rows:
        return {"status": "absent"}
    if len(rows) != 1 or b"\t" not in rows[0]:
        return {"status": "invalid", "detail": "active index marker is unmerged"}
    metadata, path = rows[0].split(b"\t", 1)
    fields = metadata.split()
    if len(fields) != 3 or fields[2] != b"0" or path != PROJECT_MARKER.encode("ascii"):
        return {"status": "invalid", "detail": "active index marker entry is malformed"}
    try:
        mode, oid = fields[0].decode("ascii"), fields[1].decode("ascii")
    except UnicodeError:
        return {"status": "invalid", "detail": "active index marker entry is not ASCII"}
    if mode not in ("100644", "100755"):
        return {"status": "invalid", "detail": f"active index marker has type/mode {mode}"}
    data, error = _marker_blob(root, oid, extra_env)
    if error:
        return {"status": "invalid", "detail": "active index marker blob is unreadable: " + error}
    if data != PROJECT_MARKER_BYTES:
        return {"status": "invalid", "detail": "active index marker bytes are not ao-project-v1"}
    return {"status": "canonical", "mode": mode, "oid": oid}


def _head_project_marker(root):
    head = _hook_git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    if head.returncode:
        if not head.stdout and not head.stderr:
            return {"status": "absent"}
        detail = head.stderr.decode(UTF8, "replace").strip()[:160]
        return {
            "status": "invalid",
            "detail": "HEAD marker query failed: "
                      + (detail or f"git exit {head.returncode}"),
        }
    result = _hook_git(root, "ls-tree", "-z", "HEAD", "--", PROJECT_MARKER)
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        return {
            "status": "invalid",
            "detail": "HEAD marker query failed: "
                      + (detail or f"git exit {result.returncode}"),
        }
    rows = [row for row in result.stdout.split(b"\0") if row]
    if not rows:
        return {"status": "absent"}
    if len(rows) != 1 or b"\t" not in rows[0]:
        return {"status": "invalid", "detail": "HEAD marker entry is malformed"}
    metadata, path = rows[0].split(b"\t", 1)
    fields = metadata.split()
    if len(fields) != 3 or path != PROJECT_MARKER.encode("ascii"):
        return {"status": "invalid", "detail": "HEAD marker entry is malformed"}
    try:
        mode = fields[0].decode("ascii")
        kind = fields[1].decode("ascii")
        oid = fields[2].decode("ascii")
    except UnicodeError:
        return {"status": "invalid", "detail": "HEAD marker entry is not ASCII"}
    if kind != "blob" or mode not in ("100644", "100755"):
        return {"status": "invalid", "detail": f"HEAD marker has type/mode {kind}/{mode}"}
    data, error = _marker_blob(root, oid, None)
    if error:
        return {"status": "invalid", "detail": "HEAD marker blob is unreadable: " + error}
    if data != PROJECT_MARKER_BYTES:
        return {"status": "invalid", "detail": "HEAD marker bytes are not ao-project-v1"}
    return {"status": "canonical", "mode": mode, "oid": oid}


def _project_config_problem(root):
    """The marker is authority; validate the bounded local state it activates."""
    return A.project_config_document(root)["problem"]


def _worktree_marker_forms():
    """The working-tree bytes that are the marker: exactly its bytes, and on Windows their CRLF checkout.

    Git for Windows checks text out with CRLF (core.autocrlf=true is its default), so a
    Windows clone of an enrolled repository holds ao-project-v1 and CRLF, and `ao init`
    refused the marker Git had just checked out (#71). Enrollment is measured from the
    blobs in HEAD and the active index alone, which that same Git stores as the exact
    bytes, and stays exact; only this working-tree read accepts the one translation,
    of the whole marker, and only where the platform makes it.
    """
    if os.name == "nt":
        return (PROJECT_MARKER_BYTES, PROJECT_MARKER_BYTES.replace(b"\n", b"\r\n"))
    return (PROJECT_MARKER_BYTES,)


def _worktree_project_marker_document(root):
    """Read one bounded marker snapshot whose fingerprint detects replacement."""
    import stat

    path = os.path.join(root, PROJECT_MARKER)
    try:
        listed = os.lstat(path)
    except FileNotFoundError as exc:
        return {
            "exists": False,
            "fingerprint": ("absent",),
            "problem": f"{PROJECT_MARKER} is missing or unreadable ({exc})",
        }
    except OSError as exc:
        return {
            "exists": True,
            "fingerprint": ("unreadable", type(exc).__name__, exc.errno),
            "problem": f"{PROJECT_MARKER} is missing or unreadable ({exc})",
        }
    if not stat.S_ISREG(listed.st_mode) or os.path.islink(path):
        return {
            "exists": True,
            "fingerprint": (
                "not-regular", listed.st_dev, listed.st_ino, listed.st_mode
            ),
            "problem": f"{PROJECT_MARKER} is not a regular file",
        }
    forms = _worktree_marker_forms()
    try:
        with open(path, "rb") as fh:
            opened = os.fstat(fh.fileno())
            # One byte past the longest accepted form, so a longer file never reads as one.
            data = fh.read(max(len(form) for form in forms) + 1)
            finished = os.fstat(fh.fileno())
    except OSError as exc:
        return {
            "exists": True,
            "fingerprint": ("unreadable", type(exc).__name__, exc.errno),
            "problem": f"{PROJECT_MARKER} is unreadable ({exc})",
        }

    def identity(value):
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            getattr(value, "st_mtime_ns", int(value.st_mtime * 1_000_000_000)),
            getattr(value, "st_ctime_ns", int(value.st_ctime * 1_000_000_000)),
        )

    listed_identity = identity(listed)
    opened_identity = identity(opened)
    finished_identity = identity(finished)
    fingerprint = ("regular", finished_identity, data)
    if listed_identity != opened_identity:
        return {
            "exists": True,
            "fingerprint": fingerprint,
            "problem": f"{PROJECT_MARKER} changed before it could be read",
        }
    if opened_identity != finished_identity:
        return {
            "exists": True,
            "fingerprint": fingerprint,
            "problem": f"{PROJECT_MARKER} changed while it was being read",
        }
    if data not in forms:
        return {
            "exists": True,
            "fingerprint": fingerprint,
            "problem": f"{PROJECT_MARKER} bytes are not ao-project-v1",
        }
    return {"exists": True, "fingerprint": fingerprint, "problem": None}


def _worktree_project_marker_problem(root):
    return _worktree_project_marker_document(root)["problem"]


def _legacy_enrollment(root):
    """A project set up before .ao-project existed stays governed by its config.

    The tracked marker is the right authority for a new project and for a
    deliberate two-phase removal, and it left one case out: a project initialised
    before the marker existed. That project has a real .ao/config.json, an
    enforcing commit hook, and no marker anywhere in its history. Calling it
    uninitialised turns its hook into a silent pass the moment the tool is
    upgraded, which is the fail-open the marker was introduced to close.

    Two neighbours must stay unenforced and are told apart here: an incidental
    .ao/ directory has no config, and a completed removal has a commit in HEAD's
    history that touched the marker. A shallow clone that lost the removal commit
    reads as legacy, which errs on the enforcing side. Returns None when the
    project is not legacy.
    """
    if not os.path.lexists(os.path.join(root, ".ao", "config.json")):
        return None
    head = _hook_git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    if head.returncode == 0:
        touched = _hook_git(root, "rev-list", "-n", "1", "HEAD", "--", PROJECT_MARKER)
        if touched.returncode:
            detail = touched.stderr.decode(UTF8, "replace").strip()[:160]
            return {
                "state": "broken",
                "detail": f"cannot read {PROJECT_MARKER} history: "
                          + (detail or f"git exit {touched.returncode}"),
            }
        if touched.stdout.strip():
            return None
    elif head.stdout or head.stderr:
        detail = head.stderr.decode(UTF8, "replace").strip()[:160]
        return {
            "state": "broken",
            "detail": "HEAD query failed: " + (detail or f"git exit {head.returncode}"),
        }
    problem = _project_config_problem(root)
    if problem:
        return {
            "state": "broken",
            "detail": f"this project has AO state but it is unreadable: {problem}",
        }
    return {
        "state": "legacy",
        "detail": (
            f"set up before {PROJECT_MARKER} existed: .ao/config.json governs commits "
            f"and no {PROJECT_MARKER} has ever been tracked — {PROJECT_ADOPT_HINT}"
        ),
        "source": None,
        "marker": None,
    }


def _project_enrollment(root, index_file=None):
    """Measure the tracked marker in the active index and HEAD, then local state.

    The active index wins when it has an entry, so a malformed staged replacement
    cannot disable a canonical HEAD marker.  An absent index entry still consults
    HEAD, which keeps enforcement active while an authorized marker deletion is
    staged.  A canonical staged marker permits first adoption before the first
    marker-bearing commit.
    """
    root = os.path.realpath(os.path.abspath(root))
    try:
        indexed = _index_project_marker(root, index_file=index_file)
        headed = _head_project_marker(root)
    except _HookResolutionError as exc:
        return {"state": "broken", "detail": _project_refusal(str(exc))}

    if indexed["status"] == "invalid":
        return {"state": "broken", "detail": _project_refusal(indexed["detail"])}
    if indexed["status"] == "canonical":
        source, marker = "index", indexed
    elif headed["status"] == "invalid":
        return {"state": "broken", "detail": _project_refusal(headed["detail"])}
    elif headed["status"] == "canonical":
        source, marker = "head", headed
    else:
        legacy = _legacy_enrollment(root)
        if legacy is not None:
            legacy.update({"index": indexed, "head": headed})
            return legacy
        return {
            "state": "uninitialized",
            "detail": f"no canonical {PROJECT_MARKER} exists in HEAD or the active index",
            "index": indexed,
            "head": headed,
        }

    problem = _project_config_problem(root)
    if problem:
        return {
            "state": "broken", "detail": _project_refusal(problem),
            "source": source, "marker": marker,
            "index": indexed, "head": headed,
        }
    return {
        "state": "enrolled", "detail": f"canonical {PROJECT_MARKER} via {source}",
        "source": source, "marker": marker,
        "index": indexed, "head": headed,
    }


def _commit_hook_probe_response(root):
    """Refuse a nonce-bound synthetic index without touching the object store.

    The execution probe reaches this branch through Git's own hook runner.  It
    stages one gitlink in a temporary index, pointing at the existing HEAD
    commit, so validating the challenge needs no ``write-tree`` and leaves no
    loose object behind.  All four namespaced challenge fields must be present
    before this branch activates, so a partial inherited environment cannot
    intercept a real commit.  The namespaced index must also bind exactly to
    Git's active index.  Once active, a missing index or malformed challenge
    fails closed without emitting the success marker the parent process
    requires.
    """
    nonce = os.environ.get("AO_HOOK_PROBE_NONCE")
    path = os.environ.get("AO_HOOK_PROBE_PATH")
    head = os.environ.get("AO_HOOK_PROBE_HEAD")
    probe_index = os.environ.get("AO_HOOK_PROBE_INDEX")
    index = os.environ.get("GIT_INDEX_FILE")
    challenge = (nonce, path, head, probe_index)
    if any(value is None for value in challenge):
        return None

    values = challenge + (index,)
    problem = None
    if not all(values):
        problem = "incomplete hook execution challenge"
    elif len(nonce) != 32 or any(ch not in "0123456789abcdef" for ch in nonce):
        problem = "invalid hook execution challenge nonce"
    elif path != ".ao-hook-probe-" + nonce:
        problem = "hook execution challenge path does not match its nonce"
    elif len(head) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in head):
        problem = "invalid hook execution challenge object"
    elif not os.path.isabs(index):
        problem = "hook execution challenge index is not absolute"
    elif probe_index != index:
        problem = "hook execution challenge index does not match Git's active index"

    if problem is None:
        try:
            measured = _hook_git(
                root, "ls-files", "--stage", "-z", "--", path,
                extra_env={"GIT_INDEX_FILE": index},
            )
        except _HookResolutionError as exc:
            problem = f"cannot inspect hook execution challenge: {exc}"
        else:
            expected = f"160000 {head} 0\t{path}\0".encode("ascii")
            if measured.returncode or measured.stdout != expected:
                problem = "hook execution challenge does not match the synthetic index"

    print(f"{C['red']}{C['b']}COMMIT REFUSED{C['reset']}")
    if problem is not None:
        print(f"  {C['red']}·{C['reset']} {problem}")
    else:
        print(f"AO-HOOK-PROBE-REFUSED {nonce} {head} {path}")
    return 1


def cmd_commit(cfg, args):
    """Commit the staged candidate in the one form a tool grant can safely allow (#58).

    A grant of `git commit:*` is a grant of `git commit --no-verify`, the flag that
    skips the only commit-time enforcement. This takes a message and nothing else,
    runs the same authority check the pre-commit hook runs before Git is asked to
    commit - so a deleted or redirected hook does not skip it - drops inherited
    configuration that could redirect the hooks, and lets them run as usual.
    """
    import subprocess
    message, file = getattr(args, "message", None), getattr(args, "file", None)
    if bool(message) == bool(file):
        print("ao commit takes exactly one of -m MESSAGE or -F FILE")
        return 2
    code = cmd_commit_check(cfg, args)
    if code:
        return code
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("GIT_CONFIG", "GIT_DIR", "GIT_WORK_TREE"))}
    source = ["-m", message] if message else ["-F", file]
    code = subprocess.run(["git", "commit", *source], cwd=cfg["root"], env=env).returncode
    if code:
        return code
    # The hook saw the index before Git wrote the tree; what landed is compared
    # with what was granted, and a difference is reported and recorded (#64).
    problem = A.landed_commit_problem(cfg["root"])
    if problem:
        print(f"{C['red']}{C['b']}LANDED OUTSIDE ITS GRANT{C['reset']}")
        print(f"  {C['red']}·{C['reset']} {problem}")
        A.record_notice(cfg["root"], "commit landed outside its grant", problem,
                        sent=False, key="landed-outside-grant")
        try:
            A.record_authority(cfg["root"], False, [problem], A.tree_digest(cfg["root"], cfg), None)
        except Exception as exc:
            print(f"  {C['dim']}the mismatch could not be recorded: {exc}{C['reset']}")
        return 1
    return 0


def cmd_commit_check(cfg, args):
    """Revalidate the latest persisted grant against Git's exact active index."""
    root = cfg["root"]
    enrollment = _project_enrollment(root)
    if enrollment["state"] == "uninitialized":
        return 0
    if enrollment["state"] == "broken":
        print(f"{C['red']}{C['b']}COMMIT REFUSED{C['reset']}")
        print(f"  {C['red']}·{C['reset']} {enrollment['detail']}")
        return 1
    probe_code = _commit_hook_probe_response(root)
    if probe_code is not None:
        return probe_code
    reasons = []
    strict = M.is_strict(cfg)
    matrix_resolution = None
    if strict:
        try:
            matrix_resolution = M.resolve(cfg, require_independent=False)
        except M.MatrixError as exc:
            reasons.extend("capability matrix: " + problem for problem in exc.problems)
    candidate = None
    try:
        candidate = A.index_candidate(root)
        if not candidate["changed_paths"]:
            reasons.append("no staged candidate")
        reasons.extend(A.candidate_issue_messages(
            A.candidate_worktree_issues(root, cfg, candidate)
        ))
    except Exception as exc:
        reasons.append(f"cannot measure the staged candidate: {exc}")

    grant = None
    try:
        grant = A.latest_authority_decision(root)
    except Exception as exc:
        reasons.append(f"authority ledger is unreadable: {exc}")
    if grant is None:
        if not any(reason.startswith("authority ledger is unreadable:") for reason in reasons):
            reasons.append("no recorded authority decision — run `ao commit-ok`")
    elif grant.get("granted") is not True:
        reasons.append("latest authority decision refused this commit")
    else:
        if strict:
            if matrix_resolution is not None:
                reasons.extend(M.authority_problems(matrix_resolution, grant))
            elif grant.get("schema") != 3:
                reasons.append("strict mode requires a schema-3 capability-matrix grant")
        elif grant.get("schema") == 3 or grant.get("matrix") is not None:
            reasons.append(
                "capability_matrix was removed after this strict authority grant"
            )
        elif grant.get("schema") != 2:
            reasons.append("latest grant predates index-candidate binding")
        if candidate is not None and grant.get("candidate") != candidate:
            reasons.append("latest grant does not match the current index candidate")

        stored_scope = grant.get("scope")
        if not isinstance(stored_scope, dict):
            reasons.append("latest grant has no valid candidate scope")
        elif candidate is not None:
            paths = stored_scope.get("paths")
            if not isinstance(paths, list):
                reasons.append("latest grant has no valid candidate scope paths")
            else:
                try:
                    current_scope = A.candidate_scope(candidate, paths)
                except (TypeError, ValueError) as exc:
                    reasons.append(f"latest grant has an invalid candidate scope: {exc}")
                else:
                    if current_scope["outside_paths"]:
                        reasons.append(
                            "staged paths fall outside the granted scope: "
                            + ", ".join(current_scope["outside_paths"])
                        )
                    if stored_scope != current_scope:
                        reasons.append("latest grant scope does not exactly match the current candidate")

    verification = None
    try:
        verification = A.latest_verification(root)
    except Exception as exc:
        reasons.append(f"verification ledger is unreadable: {exc}")
    if verification is None:
        if not any(reason.startswith("verification ledger is unreadable:") for reason in reasons):
            reasons.append("no verification record — run `ao verify`")
    else:
        referenced = grant.get("verification") if grant else None
        if verification.get("id") != referenced:
            reasons.append(
                f"latest verification {verification.get('id') or '<unnamed>'} is not "
                f"the grant's referenced verification {referenced or '<missing>'}"
            )
        if verification.get("passed") is not True:
            reasons.append("the grant's verification did not pass")
        if verification.get("candidate_ready") is not True:
            reasons.append("the grant's verification did not prove an isolated candidate")
        if candidate is not None and verification.get("candidate") != candidate:
            reasons.append("the grant's verification does not match the current index candidate")
        if A.gate_definitions_digest(root, verification.get("profile")) \
                != verification.get("gates_digest"):
            reasons.append("gate definitions changed since the grant's verification — "
                           "re-run `ao verify`")

    if grant and grant.get("granted") is True:
        review_name = grant.get("review")
        if review_name:
            try:
                decision = A.candidate_review_decision(root, cfg["reviews"], candidate["digest"]) \
                    if candidate is not None else {"match": None, "problem": None}
            except Exception as exc:
                decision = {"match": None, "problem": f"review ledger is unreadable: {exc}"}
            match = decision["match"]
            if not match:
                reasons.append(f"granted review {review_name} is no longer the current approval"
                               + (f": {decision['problem']}" if decision["problem"] else ""))
            else:
                if match[0] != review_name:
                    reasons.append(
                        f"current approval is {match[0]}, not the granted review {review_name}"
                    )
                _, integrity_reasons = A.candidate_review_integrity(
                    root, candidate, match[3]
                )
                reasons.extend(f"{match[0]}: {reason}" for reason in integrity_reasons)
                if match[3].get("scope") != grant.get("scope"):
                    reasons.append(
                        "granted review scope does not exactly match the persisted authority grant"
                    )
                if strict and matrix_resolution is not None:
                    reasons.extend(
                        f"{match[0]}: {reason}"
                        for reason in M.review_evidence_problems(
                            matrix_resolution, match[3]
                        )
                    )
                    evidence_reviewer = match[3].get("reviewer_identity")
                    if grant.get("reviewer_identity") != evidence_reviewer:
                        reasons.append(
                            "granted reviewer identity does not match the review artifact"
                        )
                    binding = (evidence_reviewer or {}).get("binding") \
                        if isinstance(evidence_reviewer, dict) else None
                    if grant.get("reviewer") != binding:
                        reasons.append(
                            "granted reviewer binding does not match the review artifact"
                        )
        else:
            if strict and (
                grant.get("reviewer_identity") is not None
                or grant.get("reviewer") is not None
            ):
                reasons.append("review-bypassed strict grant unexpectedly names a reviewer")
            from . import features as F
            if F.enabled(cfg, "review"):
                try:
                    running = [item["id"] for item in A.board(root)["running"]]
                    waiver, notes = A.review_waiver_for(
                        root, running, (candidate or {}).get("digest")
                    )
                except Exception as exc:
                    reasons.append(f"cannot validate the live review waiver: {exc}")
                else:
                    if not waiver:
                        reasons.append(
                            "review is enabled and no matching running-slice waiver is open"
                            + (f": {'; '.join(notes)}" if notes else "")
                        )
                    elif grant.get("waiver") and grant["waiver"] != waiver["id"]:
                        reasons.append(f"the grant stood on waiver {grant['waiver']}, "
                                       f"not the open {waiver['id']}")

    try:
        drift = A.plan_drift(root)
    except Exception as exc:
        reasons.append(f"cannot validate plan integrity: {exc}")
    else:
        if drift:
            reasons.append(f"plan edited after admission: {', '.join(drift)}")
    try:
        held = A.hold_state(root)
    except Exception as exc:
        reasons.append(f"cannot validate project hold state: {exc}")
    else:
        if held:
            reasons.append(f"project is held by {held.get('by')}: {held.get('reason', '')}")
    try:
        urgent = A.urgent_messages(root, cfg)
    except Exception as exc:
        reasons.append(f"cannot validate urgent messages: {exc}")
    else:
        for message in urgent:
            reasons.append(
                f"urgent message unacknowledged: {message['id']} — {message['title']}"
            )

    if reasons:
        print(f"{C['red']}{C['b']}COMMIT REFUSED{C['reset']}")
        for reason in reasons:
            print(f"  {C['red']}·{C['reset']} {reason}")
        if enrollment["state"] == "legacy":
            print(f"  {C['dim']}{enrollment['detail']}{C['reset']}")
        return 1

    token = grant.get("token") or "recorded grant"
    print(f"{C['green']}{C['b']}AUTHORIZED{C['reset']}  {candidate['index_tree']} · {token}")
    return 0
