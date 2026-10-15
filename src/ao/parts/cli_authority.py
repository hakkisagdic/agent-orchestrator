"""Authority: verify, merge checks, commit-ok, the project marker, commit and commit-check.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


def _scheduled_path():
    """The PATH a scheduled job runs with, fixed when the job is installed.

    launchd starts jobs with /usr/bin:/bin:/usr/sbin:/sbin, which does not reach
    ~/.local/bin, where kiro-cli, ao and the watchdog live; the credit sampler ran
    blind for its whole life because of it. A user systemd's own PATH does not reach
    it either. Take the watchdog's child PATH, keep directories that exist, and drop
    per-shell version-manager directories that disappear when the shell that created
    them exits.
    """
    from .watchdog import child_path
    seen, keep = set(), []
    for d in child_path().split(os.pathsep):
        if not d or d in seen or "fnm_multishells" in d or not os.path.isdir(d):
            continue
        seen.add(d)
        keep.append(d)
    return os.pathsep.join(keep)


def _launchd_path():
    """The PATH a launchd job runs with, escaped as the text of a plist's <string>."""
    from xml.sax.saxutils import escape
    return escape(_scheduled_path())


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


# Every launchd label ao gives a job starts with this; `ao uninstall` takes the namespace whole.
LAUNCHD_NAMESPACE = "com.agentorchestrator."


def _launchd_label(job, key):
    """The launchd label of one job ao schedules for a project: `watchdog`, `doctor` or `telegram`."""
    return f"{LAUNCHD_NAMESPACE}{job}.{key.lower()}"


def _launchd_plist(label):
    return os.path.join(A.HOME, "Library", "LaunchAgents", label + ".plist")


def _launchd_loaded(label):
    """Whether `launchctl list` names exactly this label.

    `_launchd_listed` reads the listing as grep did, and the watchdog label of a project
    called `proj` is inside the one of `proj-api`: a removal checks the label itself.
    """
    return any(line.rsplit("\t", 1)[-1].strip() == label for line in _launchctl("list")[0].split("\n"))

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
        # What it prints decides, and it counts only where the command exited 0, or with a code the gate declares
        # in `ok_exit` - grep and git grep exit 1 for finding nothing, so a sweep with them declares [0, 1]. No code
        # is taken for an answer unsaid: grep exits 2 for an error, and `grep -R '[' src 2>/dev/null` printed
        # nothing, measured nothing and passed, as did a program whose silent 1 is its failure (LEADS-GATE).
        answered = _gate_ok_exits(g)
        ran = isinstance(code, int) and not isinstance(code, bool) and code in answered
        passed = ran and not out.strip()
        detail = ("clean" if passed else " ".join(out.split())[:120] if out.strip()
                  else f"exit {code} and nothing printed: the command failed rather than found nothing (a sweep "
                       f"counts on {', '.join(map(str, sorted(answered)))}; a program that answers otherwise is "
                       "declared with ok_exit)")
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


def _gate_ok_exits(g):
    """The exit codes an `expect: empty` gate's command answers with when it found nothing: 0, and the ones the gate
    declares in `ok_exit` (LEADS-GATE)."""
    declared = g.get("ok_exit")
    return {0} | (set(declared) if isinstance(declared, list) else set())


def _run_gate(cfg, g, cwd):
    """(what one gate printed, its exit code): its command line run in `cwd`, as `ao verify` and `ao merge-check` run it.

    In Windows' own cmd.exe, never one found in the current directory (WINDOWS-NO-CWD-EXEC); what that
    shell runs is the project's, as on POSIX. It leads a process group of its own, so a timeout stops
    it with whatever it started (A.run_as_group): a gate's shell was killed alone and its test workers
    ran on, loading the machine after the run had let go of the lock they ran under (LEADS-GATE). A
    SIGTERM or SIGHUP sent to ao stops the gate as Ctrl-C does, since its group hears neither.
    """
    timeout = g.get("timeout") or S.get(cfg, "gates.default_timeout")
    saved = A._hear_stop_signals()
    try:
        ran = A.run_as_group(g["run"], timeout, shell=True, cwd=cwd, capture_output=True, text=True,
                             encoding=UTF8, errors="replace", executable=A.shell_program())
    except subprocess.TimeoutExpired as expired:
        left, unasked = getattr(expired, "left", None), getattr(expired, "unasked", None)
        return ("timed out" + (f"; {len(left)} process(es) it started still run after it was stopped: {left} - "
                               "stop them by hand" if left else "")
                + (f"; {unasked} - look for them and stop them by hand" if unasked else ""), 124)
    except OSError as exc:
        # A command line the system cannot start - one past ARG_MAX, a shell that is gone - is the gate's failure,
        # recorded as its answer, where it raised once the machine lock was held (LEADS-GATE-RUNS).
        return f"could not start: {' '.join(str(exc).split())[:200]}", 127
    finally:
        A._restore_signals(saved)
    return (ran.stdout or "") + (ran.stderr or ""), ran.returncode


def _profile_gates(spec, profile, cfg=None):
    """(the names of the gates `profile` runs, why .ao/gates.json cannot run them); ([], None) for a profile it does not
    declare (LEADS-GATE).

    The file is the implementer's to write, and every shape a run reads is checked before the machine
    lock is taken: a file that was not an object, a profile that was not a list of names, a gate it
    named that was not there or had no command, a timeout that was not a number - each raised in the
    middle of `ao verify`, once the lock was held, where it is now one line and exit 2.
    """
    if not isinstance(spec, dict):
        return [], "it is not a JSON object"
    profiles, gates = spec.get("profiles", {}), spec.get("gates", {})
    if not isinstance(profiles, dict) or not isinstance(gates, dict):
        return [], "its profiles and gates are not both JSON objects"
    if not isinstance(profile, str):
        return [], f"its default_profile {profile!r} is not a name"
    if profile not in profiles:
        return [], None
    names = profiles[profile]
    # A profile that is there is a list of names, an empty one too: `""` read as no profile at all (LEADS-GATE).
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        return [], f"profile {profile} is not a list of gate names"
    for name in names:
        why = _gate_shape_problem(gates.get(name))
        if why:
            return [], f"profile {profile} runs gate {name}, and {why}"
    untimed = next((name for name in names if gates[name].get("timeout") is None), None)
    if untimed is not None and cfg is not None:
        # The default a gate with no timeout runs under is held to what a gate's own is held to: one past a week was
        # passed over for 600 seconds, where the gates file is refused (LEADS-GATE).
        unusable = S.resolve(cfg, "gates.default_timeout")[2]
        if unusable:
            return [], f"profile {profile} runs gate {untimed} with no timeout of its own, and {unusable}"
    return names, None


def _gate_shape_problem(g):
    """Why one gate's definition is not one a run reads, or None (LEADS-GATE)."""
    if g is None:
        return "no gate defines it"
    if not isinstance(g, dict):
        return "it is not a JSON object"
    if not isinstance(g.get("run"), str) or not g["run"].strip():
        return "it has no command in run"
    if "\x00" in g["run"]:
        return "its command holds a NUL character, which no command line can carry"
    timeout = g.get("timeout")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                or not 0 < timeout <= S.GATE_TIMEOUT_MAX):
        # Zero was taken, then read as no timeout at all and replaced by the default; 1e308 was taken, then
        # overflowed the wait once the machine lock was held (LEADS-GATE).
        return "its timeout is not a number of seconds above zero and at most a week"
    ok_exit = g.get("ok_exit")
    if ok_exit is not None and not (isinstance(ok_exit, list) and ok_exit and all(
            isinstance(code, int) and not isinstance(code, bool) and 0 <= code <= 125 for code in ok_exit)):
        return "its ok_exit is not a list of the exit codes 0 to 125 its command answers with"
    for key in ("expect", "summary"):
        if g.get(key) is not None and not isinstance(g[key], str):
            return f"its {key} is not text"
    min_tests = g.get("min_tests")
    # A whole number of tests or nothing: `[]` and `""` read as zero and the gate passed without its floor, and
    # 1e309 read as infinity, which no int holds (LEADS-GATE).
    if min_tests is not None and (isinstance(min_tests, bool) or not isinstance(min_tests, int) or min_tests < 0):
        return "its min_tests is not a whole number of tests"
    return None


def _take_gate_lock(cfg, wait):
    """Take the machine gate lock for one run of the gates: the call that lets it go, or None if it stayed busy.

    The lock is taken first and looked at only when the take fails. A look and then a take were
    two steps, and two runs that looked at the same moment both found it free; the one whose take
    failed ran its suite beside the other's all the same. A holder is waited for whatever project
    it runs for: an `ao lock -- <suite>` in this checkout is as much a second suite as another
    project's gates, and only another project's used to be waited for (GATE-LOCK-SAME-ROOT).

    The one holder not waited for is the run's own: this process, or a process that started it,
    as `ao lock -- ao verify` starts its verify. That holder is waiting for this run to finish, so
    waiting for it could end only in a refusal; the run goes ahead inside its lock and leaves the
    letting go to it. Nothing in ao holds the lock while it starts a verify - the review runner and
    the MCP server take none, and `ao commit-ok --verify` and `ao prove` hold none when they call
    one - so each of those runs takes the lock like any other.

    Once it holds the lock, the run reads the machine and waits up to `wait` seconds more while its
    memory, paging or disk is past its limit, keeping the lock - a run waiting for it would find the
    same machine; a machine still short then has the lock let go, and no suite starts (MACHINE-PRESSURE).
    Under its own holder's lock it reads nothing: the holder read the machine when it took the lock,
    and what loads the machine now is the holder's own work.
    """
    root = cfg["root"]
    if A.acquire_gate_lock(root, 0):
        return _room_under_the_lock(cfg, wait)
    holder = A.gate_lock_holder()
    ours, unread = A.ancestry(holder.get("pid")) if holder else (False, None)
    if ours:
        return lambda: None                      # its holder lets it go, after this run has finished
    if ours is None:
        # A walk that could not read a parent cannot tell its own holder from another run's; it says so
        # before it waits, as for any holder, where it used to wait without a word (ANCESTOR-WINDOWS).
        print(f"{C['yellow']}cannot tell whether pid {holder.get('pid')} started this run{C['reset']}: "
              f"this platform did not say which process started pid {unread}", flush=True)
    if not holder:                               # a lock ao cannot read, or one let go since the take
        who, detail = "the machine lock is taken", A.GATE_LOCK
    elif holder.get("root") == root:
        who, detail = "another run in this project holds the machine lock", \
            f"pid {holder.get('pid')}, {holder['minutes']}m"
    else:
        who, detail = f"{os.path.basename(holder.get('root') or 'another project')} is running its gates", \
            f"{holder['minutes']}m"
    print(f"{C['yellow']}{who}{C['reset']} {C['dim']}({detail}){C['reset']} — waiting up to {wait}s so two "
          f"suites do not fight for one machine", flush=True)
    if A.acquire_gate_lock(root, wait):
        return _room_under_the_lock(cfg, wait)
    print(f"{C['red']}still busy; not starting a second suite{C['reset']}")
    return None


def _room_under_the_lock(cfg, wait):
    """The call that lets the gate lock go once the machine has room for the gates it is held for, or None, the lock
    let go, when the machine stays short (MACHINE-PRESSURE)."""
    room = False
    try:
        room = _machine_has_room(cfg, wait, "the gates")
    finally:
        if not room:                             # short, or stopped while it waited
            A.release_gate_lock()
    return A.release_gate_lock if room else None


def _machine_has_room(cfg, wait, what):
    """Whether the machine has room for `what`, after waiting up to `wait` seconds while it is short; a refusal is
    printed, and the caller exits 2 on it as on a lock that stayed busy (MACHINE-PRESSURE)."""
    from . import pressure
    refused = pressure.wait_for_room(cfg, wait, what)
    if refused:
        print(f"{C['red']}{refused}{C['reset']}", flush=True)
    return refused is None


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
    declared = spec if isinstance(spec, dict) else {}
    profiles = declared.get("profiles") if isinstance(declared.get("profiles"), dict) else {}
    profile = args.profile or ("full" if "full" in profiles else declared.get("default_profile", "quick"))
    names, problem = _profile_gates(spec, profile, cfg)
    if problem:
        print(f"{C['red']}no usable .ao/gates.json{C['reset']}: {problem}")
        return 2
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
    release = _take_gate_lock(cfg, args.wait)
    if release is None:
        return 2
    scratch = tempfile.mkdtemp(prefix="ao-merge-check-")
    result_dir = os.path.join(scratch, "result")
    links, results, ok = [], [], True
    made = False
    try:
        added = subprocess.run([A.git_binary(), "worktree", "add", "--detach", "--quiet", result_dir, into],
                               cwd=root, capture_output=True)
        made = added.returncode == 0 or os.path.lexists(os.path.join(result_dir, ".git"))
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
            out, code = _run_gate(cfg, g, result_dir)
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
        try:
            cleanup_steps = (("remove", "--force", result_dir), ("prune",)) if made else ()
            if not made:
                shutil.rmtree(scratch, ignore_errors=True)
            for cleanup_args in cleanup_steps:
                try:
                    A._git_worktree_cleanup(root, *cleanup_args)
                except A.WorktreeCleanupRefused:
                    raise
                except RuntimeError as exc:
                    print(f"{C['yellow']}worktree cleanup incomplete{C['reset']}: "
                          f"{' '.join(str(exc).split())}; inspect the worktree administration and retry cleanup")
                if cleanup_args[0] == "remove":
                    shutil.rmtree(scratch, ignore_errors=True)
        except A.WorktreeCleanupRefused as exc:
            ok = False
            print(f"{C['red']}worktree cleanup failed{C['reset']}: {exc}")
        finally:
            release()
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

    The machine lock is taken once the gates to run are known (VERIFY-LOCK-LATE): a project that
    declares none, or a profile it does not have, is told so at once instead of after a wait for
    another project's suite, as `ao merge-check` already did. The candidate is read inside the lock,
    so a change to the index during the wait is not read as one made while the gates ran.
    """
    import json as _json
    root = cfg["root"]
    _urgent_banner(cfg)
    gates_file = os.path.join(root, ".ao", "gates.json")
    if not os.path.exists(gates_file):
        print(f"{C['yellow']}No .ao/gates.json — nothing declared to verify.{C['reset']}")
        print(f"{C['dim']}See docs/gates.md for the shape.{C['reset']}")
        return 1
    try:
        with open(gates_file, encoding=UTF8) as fh:
            spec = _json.load(fh)
    except (OSError, ValueError) as exc:
        # Said in one line before the lock is taken, as `ao merge-check` says it, where it raised (LEADS-GATE).
        print(f"{C['red']}no usable .ao/gates.json{C['reset']}: {exc}")
        return 2
    declared = spec if isinstance(spec, dict) else {}
    profile = args.profile or declared.get("default_profile", "quick")
    names, problem = _profile_gates(spec, profile, cfg)
    if problem:
        print(f"{C['red']}no usable .ao/gates.json{C['reset']}: {problem}")
        return 2
    if not names:
        profiles = declared.get("profiles")
        print(f"unknown profile {profile}; have: {', '.join(profiles if isinstance(profiles, dict) else [])}")
        return 1
    # The record names what ran, so a later edit to gates.json cannot inherit
    # this result (#61).
    gates_digest = A.gate_definitions_digest_of(spec, profile)
    release = _take_gate_lock(cfg, args.wait)
    if release is None:
        return 2

    try:
        candidate_before = A.index_candidate(root)
        candidate_issues_before = A.candidate_worktree_issues(root, cfg, candidate_before)
    except RuntimeError as exc:
        release()
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
        out, code = _run_gate(cfg, g, root)
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

    harness = _verify_harness_config(root)

    revs = A.reviews(root, cfg["reviews"], limit=1)
    rec = {"id": _verification_id(root), "at": datetime.now().isoformat(timespec="seconds"),
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
           # What a harness reads from the tree that changed and no person has seen (HARNESS-CONFIG).
           "harness_config": harness,
           "dirty": len([l for l in A._git_text(root, "status", "--short").split("\n") if l.strip()])}
    try:
        A.record_verification(root, rec)
    except Exception as exc:
        print(f"{C['red']}{C['b']}NOT RECORDED{C['reset']}  verification ledger: {exc}")
        return 2
    finally:
        release()

    print(f"\n{C['b']}{'PASS' if ok else 'FAIL'}{C['reset']}  recorded as {rec['id']}")
    if revs:
        col = C["green"] if "APPROVED" in (revs[0][1] or "").upper() else C["yellow"]
        print(f"{C['dim']}newest review:{C['reset']} {col}{revs[0][1]}{C['reset']} ({revs[0][0]})")
    return 0 if ok else 1


def _verification_id(root):
    """A verification's id: V- and the millisecond it is minted in, above the newest one recorded (LEADS-GATE).

    It was the second, and two runs in one second - two quick runs under one `ao lock`, on a machine
    whose pressure is not read - or a run after the clock stepped back recorded two verifications
    under one id, which a grant, a commit's Ao-Verification trailer and a report each name.
    A.unique_ms keeps one process's ids apart, and the newest id in the ledger keeps every run above
    the runs before it.
    """
    # Three digits of the process's id after the millisecond: two verifications under one held lock read the same
    # newest id in the same millisecond and recorded two runs under one id; each now goes above it by its own.
    salt = os.getpid() % 1000
    value = A.unique_ms() * 1000 + salt
    try:
        newest = str((A.latest_verification(root) or {}).get("id") or "")
    except Exception:
        newest = ""                       # an unreadable ledger refuses the record itself
    found = A.re.fullmatch(r"V-([0-9]+)", newest)
    above = (int(found.group(1)) // 1000 + 1) * 1000 + salt if found else value
    return f"V-{max(value, above)}"


def _verify_harness_config(root):
    """Name each change to what a harness reads from the tree that no person has seen, and what records it as seen;
    the verification's record of them (HARNESS-CONFIG).

    Not a failed gate: verification measures the candidate, and a change an implementer makes to its own instructions
    can be the slice. What holds is elsewhere - no wake, refill, nudge, hunt or lane starts in the tree until a person
    has seen it - and the record keeps what stood unseen when the gates ran.
    """
    try:
        unseen = A.unseen_harness_config(root)
    except Exception as exc:
        why = " ".join(str(exc).split())[:240]
        print(f"\n  {C['yellow']}harness configuration{C['reset']}  cannot be read ({why}); nothing ao starts in this "
              "tree runs until it can")
        return {"unreadable": why}
    if not unseen:
        return {"unseen": []}
    print(f"\n  {C['yellow']}harness configuration nobody has seen{C['reset']}  {len(unseen)} change(s) to what a "
          "harness reads from this tree:")
    for path, state, _ in unseen:
        print(f"    {A.shown_text(path)} ({A.HARNESS_CONFIG_STATES.get(state, state)})")
    print(f"  {C['dim']}not a failed gate; no wake, refill, nudge, hunt or lane starts here until a person reads them: "
          f"ao harness seen --by <name>{C['reset']}")
    return {"unseen": [{"path": path, "state": state, "sha256": sha} for path, state, sha in unseen],
            "digest": A.harness_config_digest(unseen)}


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
            matrix_resolution = _resolve_matrix(cfg, require_independent=False)
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
    move_proof, unproven = None, []
    if move_only:
        try:
            split = A.split_moves(root)
        except RuntimeError as exc:
            split = {"moved": [], "problems": [f"the candidate cannot be read: {exc}"]}
        reasons.extend(f"{move_only[0]} is move-only: {problem}" for problem in split["problems"])
        # The proof reads Python definitions. A grant keeps it only when it covers every staged
        # path, and a waived review of what lands then closes on it, run again (#44).
        unproven = [path for path in candidate["changed_paths"] if not path.endswith(".py")]
        if not split["problems"] and not unproven:
            move_proof = {"slice": move_only[0], "moved": len(split["moved"])}
    if drift:
        reasons.append(f"plan edited after admission: {', '.join(drift)}")

    from . import features as F
    running = [it["id"] for it in A.board(root)["running"]]
    try:
        waiver, waiver_notes = A.review_waiver_for(root, running, candidate["digest"])
    except Exception as exc:
        waiver, waiver_notes = None, [f"waiver ledger is unreadable: {exc}"]
    review_required = F.enabled(cfg, "review")
    if "review" in F.unrecorded(cfg):
        # A config the implementer can write turned review off; it is off only on a person's record (FEATURE-RECORDED).
        reasons_note = ("review is off in .ao/config.json with no person's record, so it stays required: "
                        "ao features off review --by <name>")
        print(f"{C['yellow']}{reasons_note}{C['reset']}")
    try:
        decision = A.candidate_review_decision(root, cfg["reviews"], candidate["digest"])
    except Exception as exc:
        decision = {"match": None, "problem": f"review ledger is unreadable: {exc}"}
    match = decision["match"]
    review_name, rbody, evidence, rwho = None, "", None, None
    strict_reviewer_identity = None
    review_tier = None
    scope = A.candidate_scope(candidate)

    def bound_review():
        """What the review bound to this candidate grants: (its fields, why it does not hold)."""
        name, _, body, ev = match
        fields = {"review_name": name, "rbody": body, "evidence": ev, "scope": None, "rwho": None,
                  "strict_reviewer_identity": None, "review_tier": None}
        why = []
        reviewed_scope, integrity_reasons = A.candidate_review_integrity(root, candidate, ev)
        why.extend(f"{name}: {reason}" for reason in integrity_reasons)
        # A boundary that lists criteria lands only when the review found every one met (CRITERIA-VERDICTS).
        why.extend(f"{name}: {reason}" for reason in A.criteria_refusals(ev))
        fields["scope"] = reviewed_scope
        if strict:
            why.extend(f"{name}: {reason}" for reason in M.review_evidence_problems(matrix_resolution, ev))
            identity = ev.get("reviewer_identity") if isinstance(ev, dict) else None
            fields["strict_reviewer_identity"] = identity
            fields["rwho"] = (identity or {}).get("binding") if isinstance(identity, dict) else None
            fields["review_tier"] = next((route.get("tier") for route in matrix_resolution["reviewers"]
                                          if route["eligible"] and route["identity"] == identity), None)
        else:
            # Who reviewed is read from the evidence ao wrote, never from the
            # artefact's text, where the implementer's boundary appears too (#60).
            recorded = ev.get("reviewer") if isinstance(ev, dict) else None
            who = recorded.get("id") if isinstance(recorded, dict) else None
            if not isinstance(who, str) or not who:
                who = None
                why.append(f"{name} records no reviewer in its evidence — run `ao review` again")
            else:
                # The tier it was recorded in must still hold: a withdrawn opt-in grants nothing (REVIEW-TIERS).
                fields["review_tier"], refused = _recorded_review_tier(cfg, ev, who)
                if refused == "it runs as the implementer":
                    why.append(f"the review was written by the implementer ({who})")
                elif refused:
                    why.append(f"the review's reviewer {who} may not review this implementer: {refused}")
            fields["rwho"] = who
        return fields, why

    no_eligible = strict and not any(route["eligible"] for route in matrix_resolution["reviewers"])
    bound, bound_why = bound_review() if review_required and match and not no_eligible else (None, [])
    if not review_required:
        pass
    elif bound is not None and (not bound_why or not waiver):
        # A candidate a review is bound to stands on that review, and an open waiver is left for what needs
        # one: the waiver was asked first, and a reviewed candidate was granted as waived, the review
        # unrecorded and the waiver spent on it (WAIVER-BOUND-2).
        reasons.extend(bound_why)
        review_name, rbody, evidence = bound["review_name"], bound["rbody"], bound["evidence"]
        rwho, review_tier = bound["rwho"], bound["review_tier"]
        strict_reviewer_identity = bound["strict_reviewer_identity"]
        if bound["scope"] is not None:
            scope = bound["scope"]
        waiver = None
    elif waiver:
        if bound_why:
            print(f"{C['dim']}the review bound to this candidate does not hold ({bound_why[0]}); "
                  f"the waiver stands in{C['reset']}")
        print(f"{C['yellow']}review waived{C['reset']} by {waiver['by']} ({waiver['id']}): "
              f"{waiver['why'][:80]} — reconcile with `ao catchup`")
    elif no_eligible:
        reasons.append("capability matrix has no independently eligible reviewer")
    else:
        reasons.append(decision["problem"]
                       or "no APPROVED prospective review is bound to this staged candidate")
        reasons.extend(waiver_notes)

    # A proof from another running slice cannot retire this slice's waiver (TRIAGE-4-REST).
    proof_note = None
    if move_proof:
        authorising_slice = (waiver.get("slice") if review_required and waiver
                             else evidence.get("slice") if evidence else None)
        if len(move_only) != 1:
            proof_note = "more than one move-only slice is running; review the range or name it with --move-only --by"
        elif authorising_slice != move_proof["slice"]:
            proof_note = ("the authorising waiver or review does not name the running move-only slice; "
                          "review the range or name it with --move-only --by")
        if proof_note:
            move_proof = None

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
    waived = review_required and waiver
    try:
        # A waived candidate is reviewed after it lands, held to the family that wrote it (#65).
        author = A.grant_author(cfg, matrix_resolution["implementer_identity"] if strict else None) \
            if waived else None
        A.record_authority(
            root, True, [], now, ver["id"], token,
            review=review_name, reviewer=rwho, candidate=candidate, scope=scope,
            waiver=waiver["id"] if waived else None, author=author, move_only=move_proof,
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
    from . import tiers as T
    if review_name and T.weaker(review_tier):
        person = f" ({_person_line(evidence.get('person'))})" if review_tier == T.PERSON else ""
        print(f"  {C['yellow']}tier{C['reset']} {T.label(review_tier)}{person}")
    elif review_name and review_tier is None and not strict:
        print(f"  {C['yellow']}tier{C['reset']} not established: no implementer is configured to compare the "
              "reviewer with — ao doctor")
    print(f"  {C['dim']}index tree{C['reset']} {candidate['index_tree']}")
    if move_proof:
        print(f"  {C['dim']}move proof{C['reset']} {move_proof['moved']} definition(s) moved byte for byte, "
              "recorded with the grant")
    elif proof_note:
        print(f"  {C['dim']}no move proof recorded: {proof_note}{C['reset']}")
    elif unproven:
        print(f"  {C['dim']}no move proof recorded: it reads Python definitions, and the candidate also "
              f"changes {', '.join(unproven)}{C['reset']}")
    print(f"\n  {C['dim']}This grant names only the staged index above; push is never covered.{C['reset']}")
    return 0


PROJECT_MARKER = ".ao-project"
PROJECT_MARKER_BYTES = b"ao-project-v1\n"
# One command adopts AO state no tracked marker covers (INIT-ADOPTION). Every message about such a
# project spelled out a recipe instead - write the marker, stage it, land it - with `ao hooks install`
# still to follow, and that command refused the project until the marker was staged.
PROJECT_ADOPT_COMMAND = "ao init --adopt"
PROJECT_ADOPT_HINT = (
    f"{PROJECT_ADOPT_COMMAND} stages {PROJECT_MARKER} and installs the current hooks; "
    "then land it through ao commit-ok"
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
            return {"status": "absent", "unborn": True}
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
            # Last, so that Windows can compare a path's stat with a handle's without it (below).
            getattr(value, "st_ctime_ns", int(value.st_ctime * 1_000_000_000)),
        )

    listed_identity = identity(listed)
    opened_identity = identity(opened)
    finished_identity = identity(finished)
    fingerprint = ("regular", finished_identity, data)
    # CPython 3.12 and later on Windows read two clocks into st_ctime: a path's stat reports when the
    # file was created, a handle's when its metadata last changed. They agree only while nothing
    # changed after creation, so init refused the marker it had just written and the one a clone
    # checked out (#71). There the path and the handle are one file when every other field agrees;
    # the handle's two readings, and so the fingerprint, still compare st_ctime.
    across = slice(None, -1) if os.name == "nt" else slice(None)
    if listed_identity[across] != opened_identity[across]:
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

    A project whose marker `ao init` wrote and nobody has staged yet measures the
    same, so the detail states what is measured rather than how the project came
    to be, and names the one command that adopts either (INIT-ADOPTION).
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
            f"no {PROJECT_MARKER} has ever been tracked here, so .ao/config.json governs "
            f"commits — {PROJECT_ADOPT_HINT}"
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
        # A branch not born yet, with no marker staged: a repository whose history tracks the marker on any ref was
        # enrolled, and an orphan branch with the marker unstaged is no way out of it; nor is deleting every ref
        # that tracked it, where this machine recorded the project as enrolled (LEADS-GATE). Only a repository
        # with neither is before its marker's first commit (docs/threat-model.md).
        if headed.get("unborn"):
            tracked = _hook_git(root, "log", "--all", "-1", "--format=%H", "--", PROJECT_MARKER)
            recorded = A.enrollment_record(root)
            why = None
            if tracked.returncode:
                why = "the repository's history cannot be read for its marker"
            elif tracked.stdout.strip():
                why = ("this branch is not born yet and stages no marker, but the repository's history tracks "
                       f"{PROJECT_MARKER} on another branch; commit the marker on this branch, or switch back")
            elif recorded is not None:
                when = recorded.get("at")
                why = ("this branch is not born yet and stages no marker, and no ref's history tracks one, but this "
                       "machine recorded the project as enrolled"
                       + (f" on {time.strftime('%Y-%m-%d', time.localtime(when))}"
                          if isinstance(when, int) and not isinstance(when, bool) and when > 0 else "")
                       + (f" (the record cannot be read: {recorded['unreadable']})" if recorded.get("unreadable")
                          else "")
                       + f": the refs that tracked {PROJECT_MARKER} were deleted - restore the branch that tracked it "
                       "(git reflog names its last commit), or commit the marker on this branch")
            if why:
                return {"state": "broken", "detail": _project_refusal(why), "index": indexed, "head": headed}
        else:
            # Born, and tracking no marker: a branch whose history never tracked one - made from a commit before the
            # marker's - in a repository this machine recorded as enrolled is no new repository either; one whose
            # history did is a removal committed, and passes (LEADS-GATE-HOOK).
            touched = _hook_git(root, "rev-list", "-n", "1", "HEAD", "--", PROJECT_MARKER)
            recorded = A.enrollment_record(root) if touched.returncode == 0 and not touched.stdout.strip() else None
            if recorded is not None:
                return {"state": "broken", "detail": _project_refusal(
                    f"this branch tracks no {PROJECT_MARKER} and descends from no commit that did, but this machine "
                    "recorded the project as enrolled: a branch made from before the marker's commit - commit the "
                    "marker on it, or switch back"
                    + (f" (the record cannot be read: {recorded['unreadable']})" if recorded.get("unreadable")
                       else "")), "index": indexed, "head": headed}
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


def _message_bytes(root, message, file):
    """The message ao commit was given, as the bytes git would have read (COMMIT-TRAILER).

    `-m` as its argument reached git; `-F -` from standard input; any other `-F` from the file, read
    where git read it: from the project root, where ao runs git.
    """
    if message:
        return os.fsencode(message)
    if file == "-":
        return sys.stdin.buffer.read()
    with open(os.path.join(root, file), "rb") as fh:
        return fh.read()


def cmd_commit(cfg, args):
    """Commit the staged candidate in the one form a tool grant can safely allow (#58).

    A grant of `git commit:*` is a grant of `git commit --no-verify`, the flag that
    skips the only commit-time enforcement. This takes a message and nothing else and
    runs the same authority check the pre-commit hook runs, so a deleted or redirected
    hook does not skip it.

    The message ends with the grant that check held the index to, as git trailers
    (COMMIT-TRAILER): the grant stays in this checkout's ledger while the commit
    leaves it, so the commit names its own authority wherever it goes, and
    `ao commit-check --range` holds it to that. A message that already ends with
    Ao- trailers is refused, not rewritten: they could only be another commit's,
    copied with its message, or typed by hand, and ao does not edit what a person
    wrote into a claim it did not make.

    What lands is the tree the grant bound, on the commit it was built on, and nothing
    else (LEADS-GATE). `git commit` read the index again once its hooks returned, so a
    path staged in between - by another process, by a hook into the index Git handed
    it, or written into a copy of the index - landed inside the granted commit; the
    branch could be switched under it; and the hooks it ran were files an implementer
    can edit where a repository keeps them in its tree, run as the person committing.
    So ao writes the commit itself, `git commit-tree` of the granted tree on the granted
    commit, and moves the branch from that commit only, compared and swapped; no hook
    runs. A path staged after the check stays staged, uncommitted. A project ao does not
    govern passes the check with no grant, and `git commit` commits it as before.
    """
    message, file = getattr(args, "message", None), getattr(args, "file", None)
    if message is not None and file is not None:
        print("ao commit takes exactly one of -m MESSAGE or -F FILE")
        return 2
    if not (file or str(message or "").strip()):
        # An empty message was told it had given both or neither (GRANTS-AUDIT-2).
        print("ao commit needs a message: -m MESSAGE with text in it, or -F FILE")
        return 2
    from types import SimpleNamespace
    check = SimpleNamespace()
    code = cmd_commit_check(cfg, check)
    if code:
        return code
    grant = getattr(check, "granted", None)
    if grant is None:
        # A project ao does not govern passes the check with no grant, and its commit stays git's alone.
        return _git_commit(cfg, message, file)
    return _commit_the_grant(cfg, grant, message, file)


def _git_commit(cfg, message, file):
    """`git commit` with the message ao commit was given, for a project ao does not govern: its exit code."""
    import subprocess
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("GIT_CONFIG", "GIT_DIR", "GIT_WORK_TREE"))}
    source = ["-m", message] if message else ["-F", file]
    # The git ao measures with, never one Windows finds in the current directory first (WINDOWS-NO-CWD-EXEC).
    return subprocess.run([A.git_binary(), "commit", *source], cwd=cfg["root"], env=env).returncode


def _plumbing(root, *args, data=None, timeout=60):
    """One git command `ao commit` writes with: no hook runs - core.hooksPath names no directory, so neither the
    reference-transaction hook nor any other is found - and no repository binding is inherited (LEADS-GATE)."""
    env = _hook_git_env()
    try:
        return A.run_as_group([A.git_binary(), "-c", f"core.hooksPath={os.devnull}", "-C", str(root), *args],
                              timeout, env=env, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _HookResolutionError(f"git {args[0]} failed: {' '.join(str(exc).split())[:160]}") from exc


def _plumbing_out(root, *args, data=None):
    done = _plumbing(root, *args, data=data)
    if done.returncode:
        detail = done.stderr.decode(UTF8, "replace").strip()[:200] or f"git exit {done.returncode}"
        raise _HookResolutionError(f"git {args[0]}: {detail}")
    return done.stdout


def _branch_before(root):
    """(the ref HEAD names, or HEAD where it is detached; the commit it points at, or None on a branch not born yet):
    the ref `ao commit` moves, and the commit it moves it from (LEADS-GATE)."""
    named = _hook_git(root, "symbolic-ref", "-q", "HEAD")
    ref = named.stdout.decode(UTF8, "replace").strip() if named.returncode == 0 else "HEAD"
    head = _hook_git(root, "rev-parse", "--verify", "-q", "HEAD")
    if head.returncode and (head.stdout or head.stderr):
        raise _HookResolutionError(head.stderr.decode(UTF8, "replace").strip()[:160] or f"git exit {head.returncode}")
    return ref, (head.stdout.decode("ascii", "replace").strip() or None) if head.returncode == 0 else None


# What `git commit` would have finished: ao commit finishes none of it, and sends it back to git (LEADS-GATE). A merge
# is no grant's - a grant binds one parent - so a lane lands squashed, as one commit on one parent.
_IN_PROGRESS = (("MERGE_HEAD", "a merge", "ao lands no merge: git merge --abort, then git merge --squash, which "
                 "stages the lane as one candidate to verify, review and grant as any"),
                ("CHERRY_PICK_HEAD", "a cherry-pick", "git cherry-pick --quit"),
                ("REVERT_HEAD", "a revert", "git revert --quit"),
                ("rebase-merge", "a rebase", "git rebase --abort, or finish it"),
                ("rebase-apply", "a rebase or git am", "git rebase --abort or git am --abort, or finish it"))


def _git_path(root, name):
    return os.path.join(root, os.fsdecode(_plumbing_out(root, "rev-parse", "--git-path", name).strip()))


def _cleaned_message(root, data):
    """The message as `git commit -F` would have cleaned it, by `commit.cleanup`: verbatim left as it is, strip
    without its comment lines, anything else without trailing space, runs of blank lines or blank ends."""
    done = _plumbing(root, "config", "--get", "commit.cleanup")
    mode = done.stdout.decode(UTF8, "replace").strip().lower() if done.returncode == 0 else ""
    if mode == "verbatim":
        return data
    return _plumbing_out(root, "stripspace", *(["--strip-comments"] if mode == "strip" else []), data=data)


def _commit_the_grant(cfg, grant, message, file):
    """Write the granted tree as a commit on the granted commit, and move the branch from that commit to it: the exit
    code (LEADS-GATE)."""
    root = cfg["root"]

    def refused(why, code=1):
        print(f"{C['red']}{C['b']}COMMIT REFUSED{C['reset']}\n  {C['red']}·{C['reset']} {why}")
        return code

    try:
        trailers = A.grant_trailers(grant)
    except ValueError as exc:
        return refused(exc)
    try:
        text = _message_bytes(root, message, file).decode(UTF8, "surrogateescape")
        data = A.with_trailers(text, trailers).encode(UTF8, "surrogateescape")
    except OSError as exc:
        print(f"ao commit cannot read the message file {file}: {exc}")
        return 2
    except ValueError as exc:
        return refused(exc, 2)
    candidate = grant.get("candidate") or {}
    tree, head = candidate.get("index_tree"), candidate.get("head")
    try:
        ref, old = _branch_before(root)
        shown = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        if not old or old != head:
            return refused(f"{shown} points at {(old or 'no commit')[:12]}, not at {str(head)[:12]}, the commit the "
                           "grant was built on: nothing was committed")
        for name, what, how in _IN_PROGRESS:
            if os.path.lexists(_git_path(root, name)):
                return refused(f"{what} is in progress, which ao commit does not finish - {how}, then ao commit "
                               "again")
        cleaned = _cleaned_message(root, data)
        if not cleaned.strip():
            return refused("the message is empty once cleaned as git commit cleans one")
        sign = _plumbing(root, "config", "--type=bool", "--get", "commit.gpgSign")
        if sign.returncode not in (0, 1):
            # Git refuses a commit whose commit.gpgSign it cannot read as a boolean; so does ao, rather than read it
            # as unset and write the commit unsigned (LEADS-GATE).
            return refused("commit.gpgSign cannot be read: "
                           + (sign.stderr.decode(UTF8, "replace").strip()[:160] or f"git exit {sign.returncode}"))
        signed = sign.returncode == 0 and sign.stdout.strip() == b"true"
        made = _plumbing_out(root, "commit-tree", *(["-S"] if signed else []), tree, "-p", old, data=cleaned)
        made = made.decode("ascii", "replace").strip()
        if _plumbing_out(root, "rev-parse", "--verify", f"{made}^{{tree}}").decode("ascii").strip() != tree:
            return refused(f"the commit written, {made[:12]}, does not hold the granted tree {tree[:12]}")
        subject = cleaned.decode(UTF8, "replace").split("\n", 1)[0]
        moved = _plumbing(root, "update-ref", "-m", f"commit: {subject}", ref, made, old)
        if moved.returncode:
            detail = moved.stderr.decode(UTF8, "replace").strip()[:200]
            return refused(f"{shown} moved while ao commit ran, so it was left where it is and nothing landed: "
                           f"{detail}")
    except _HookResolutionError as exc:
        return refused(exc)
    print(f"[{shown} {made[:7]}] {subject}")
    try:
        now = _hook_git(root, "symbolic-ref", "-q", "HEAD")
        named = now.stdout.decode(UTF8, "replace").strip() if now.returncode == 0 else "HEAD"
        if named != ref:
            # Switched while ao commit ran: the commit is on the branch the grant was made on, which is not the one
            # checked out now (LEADS-GATE).
            print(f"  {C['yellow']}·{C['reset']} HEAD was switched while ao commit ran: the commit is on {shown}, "
                  f"and {named} is checked out now")
    except _HookResolutionError:
        pass
    try:
        stat = _plumbing(root, "diff", "--no-ext-diff", "--no-textconv", "--shortstat", old, made)
        if stat.returncode == 0 and stat.stdout.strip():
            print(stat.stdout.decode(UTF8, "replace").rstrip())
    except _HookResolutionError:
        pass
    return 0


def _commit_range_check(cfg, args):
    """Hold each landed commit of a revision range to the grant its Ao- trailers name (COMMIT-TRAILER).

    What `ao commit-check --range` runs, CI among its callers: read-only, and in a clone with no .ao/ it
    judges by tree alone. Exit 0 when every commit it checked matches, 1 naming each that does not, 2
    when the range cannot be read. Requiring it is a person's step, a repository ruleset, never ao's.
    """
    spec = args.range
    try:
        audit = A.trailer_audit(cfg["root"], spec, since_first=bool(getattr(args, "since_first_trailer", False)))
    except (ValueError, RuntimeError) as exc:
        print(f"{C['red']}cannot check {spec}{C['reset']}: {exc}")
        return 2
    commits = audit["commits"]
    if not commits:
        print(f"no commits in {spec}")
        return 0
    checked = [commit for commit in commits if not commit["exempt"]]
    failing = [commit for commit in checked if commit["problems"]]
    for commit in commits:
        sha = commit["sha"][:12]
        if commit["problems"]:
            print(f"  {C['red']}NO MATCH{C['reset']}  {sha}  {'; '.join(commit['problems'])}")
        elif not commit["exempt"]:
            record = {None: "", True: " · on record", False: " · not in this checkout's ledger"}[commit["on_record"]]
            print(f"  {C['green']}matches{C['reset']}   {sha}  {commit['grant']} · tree {commit['tree'][:12]}{record}")
    exempt = sum(1 for commit in commits if commit["exempt"])
    if exempt:
        why = (f"older than {audit['first'][:12]}, the first commit carrying Ao- trailers" if audit["first"]
               else f"no commit in the history of {audit['end']} carries Ao- trailers yet")
        print(f"  {C['dim']}{exempt} commit(s) not checked: {why}{C['reset']}")
    if audit["unreadable"]:
        print(f"  {C['yellow']}checked by tree alone{C['reset']}: the authority ledger cannot be read: "
              f"{audit['unreadable']}")
    elif not audit["ledger"]:
        print(f"  {C['dim']}checked by tree alone: this checkout keeps no authority ledger{C['reset']}")
    if failing:
        print(f"{C['red']}{C['b']}TRAILERS DO NOT MATCH{C['reset']}  {len(failing)} of {len(checked)} commit(s) "
              f"in {spec}: " + ", ".join(commit["sha"][:12] for commit in failing))
        return 1
    print(f"{C['green']}{C['b']}TRAILERS MATCH{C['reset']}  {len(checked)} commit(s) checked in {spec}")
    return 0


def cmd_commit_check(cfg, args):
    """Revalidate the latest persisted grant against Git's exact active index.

    With --range it checks landed commits instead (COMMIT-TRAILER), before anything reads the
    project's enrollment: a clone in CI holds the tracked marker and no .ao/.
    """
    if getattr(args, "range", None) is not None:
        return _commit_range_check(cfg, args)
    if getattr(args, "since_first_trailer", False):
        print("ao commit-check: --since-first-trailer goes with --range")
        return 2
    root = cfg["root"]
    enrollment = _project_enrollment(root)
    if enrollment["state"] == "uninitialized":
        return 0
    if enrollment["state"] == "broken":
        # Refused, and recorded as enrolled all the same: an orphan branch of an enrolled repository is read as
        # broken while a ref still tells of the marker, and an agent can delete the ref next. A record that cannot be
        # written is said with the refusal (LEADS-GATE-HOOK).
        unkept = A.record_enrollment(root)
        print(f"{C['red']}{C['b']}COMMIT REFUSED{C['reset']}")
        print(f"  {C['red']}·{C['reset']} {enrollment['detail']}")
        if unkept:
            print(f"  {C['red']}·{C['reset']} this machine cannot record that the project is enrolled either, and a "
                  f"branch not born yet would read as a new repository's: {unkept}")
        return 1
    probe_code = _commit_hook_probe_response(root)
    if probe_code is not None:
        return probe_code
    # Kept outside the tree, for a branch not born yet to ask where no ref tracks the marker any more; a proof that
    # the hook runs records nothing (LEADS-GATE).
    unkept = A.record_enrollment(root)
    if unkept:
        print(f"{C['red']}{C['b']}COMMIT REFUSED{C['reset']}")
        print(f"  {C['red']}·{C['reset']} this machine cannot record that the project is enrolled, and a branch not "
              f"born yet would read as a new repository's: {unkept}")
        return 1
    reasons = []
    strict = M.is_strict(cfg)
    matrix_resolution = None
    if strict:
        try:
            matrix_resolution = _resolve_matrix(cfg, require_independent=False)
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
                reasons.extend(f"{match[0]}: {reason}" for reason in A.criteria_refusals(match[3]))
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
                    # The waiver the grant stood on is the one asked; the newest one open was, and a second
                    # waiver opened for the slice since refused the grant the first had made (WAIVER-BOUND-2).
                    waiver, notes = A.review_waiver_for(
                        root, running, (candidate or {}).get("digest"), wanted=grant.get("waiver") or None
                    )
                except Exception as exc:
                    reasons.append(f"cannot validate the live review waiver: {exc}")
                else:
                    if not waiver:
                        reasons.append(
                            "review is enabled and no matching running-slice waiver is open"
                            + (f" (the grant stood on {grant['waiver']})" if grant.get("waiver") else "")
                            + (f": {'; '.join(notes)}" if notes else "")
                        )

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
    # ao commit names in its message the grant held to the index here, and no other (COMMIT-TRAILER).
    args.granted = grant
    return 0
