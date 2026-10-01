"""Maintenance: remove, prune, notices, the watchdog command, projects, adapters, worktrees, prove,
backup, doctor.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


def _remove_inert_local(target, inv):
    """The one protected effective hook that may safely outlive project state."""
    common_hooks = os.path.realpath(os.path.join(inv["common_dir"], "hooks"))
    return bool(
        target["active"]
        and _state_base(target["static_state"]) == "current-local"
        and target["directory_class"] == "project-local"
        and not target["globally_configured"]
        and target["effective_count"] == 1
        and not target["legacy_location"]
        and os.path.realpath(target["directory"]) != common_hooks
        and (_hook_contains(inv["top"], target["directory"])
             or _hook_contains(inv["git_dir"], target["directory"]))
    )


def _remove_hook_preflight(inv, allow):
    plan, blockers, inert = [], [], []
    if inv["error"]:
        print(f"{C['red']}hook resolver failed; state kept intact{C['reset']}: {inv['error']}")
        return False, plan, inert
    for target in inv["targets"]:
        base = _state_base(target["static_state"])
        if target["eligible"] and base in ("current-local", "current-scoped", "legacy"):
            plan.append(target)
            continue
        if not target["protected"] or base not in (
            "current-local", "current-scoped", "legacy", "ambiguous-ao"
        ):
            continue
        if target["reachability"] == "dead-misplaced":
            print(f"preserved protected dead-misplaced {target['role']} (does not block remove): "
                  f"{target['path']}")
            continue
        if base == "current-local" and _remove_inert_local(target, inv):
            inert.append(target)
            print(f"preserved protected current-local {target['role']} — becomes inert after .ao removal: "
                  f"{target['path']}")
            continue
        blockers.append(target)
        print(f"{C['red']}remove blocked{C['reset']} by protected potentially-effective "
              f"{target['static_state']} {target['role']}: {target['path']}")
    if blockers or _authorization_refusal(plan, allow):
        print("AO state was not changed")
        return False, plan, inert
    return True, plan, inert


def _home_relative(path):
    """A path under the home directory as ~/…, the way a person reads it.

    With forward slashes on Windows too, where the rest of the path came with backslashes, so a file
    in the home is named one way on every platform (WINDOWS-LANE-4).
    """
    home = A.HOME.rstrip("/\\")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):].replace(os.sep, "/")
    return path


def _remove_key(root):
    """(the name this project's files outside its tree are kept under, whether that name is this project's).

    A directory with no .ao/ is named by its basename, and that can be another project's
    name: a second remove, run after the first took .ao/, would take that project's files.
    Only a project with .ao/, or one the registry still holds by its path, owns a name.
    """
    if os.path.isdir(os.path.join(os.path.realpath(root), ".ao")):
        return A.project_key(root), True
    registered = A.registered_key(root)
    return (registered, True) if registered else (A.project_key(root), False)


def _project_home_files(key):
    """This project's files in ~/.ao that exist, named from PROJECT_FILES (SAFE-REMOVE).

    One path per file: a case-insensitive disk answers to a lower-cased name and to the
    name as it is, and both would otherwise be listed and removed as two files.
    """
    seen, found = set(), []
    for name in A.project_file_names(key):
        path = os.path.join(A.HOME, ".ao", name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        identity = (st.st_dev, st.st_ino) if st.st_ino else os.path.normcase(path)
        if identity not in seen:
            seen.add(identity)
            found.append(path)
    return found


def _registry_names(root):
    """The names the machine registry holds for this project's resolved path."""
    real = os.path.realpath(root)
    return sorted(name for name, row in A.project_registry().items() if row.get("root") == real)


AO_GITIGNORE = ("agent-mail/*.md", "!agent-mail/README.md", ".ao/inbox/", ".ao/hold", ".ao/sessions.json",
                ".ao/lanes/")


def _gitignore_without_ao(root):
    """(.gitignore's path, its lines without the ones ao added, how many of those it had)."""
    path = os.path.join(root, ".gitignore")
    try:
        lines = open(path, encoding=UTF8).read().split("\n")
    except OSError:
        return path, [], 0
    keep = [line for line in lines if line.strip() not in AO_GITIGNORE
            and "agent-orchestrator: mail is transient" not in line]
    return path, keep, len(lines) - len(keep)


# The jobs `ao watchdog install` and `ao telegram install` schedule for a project (SAFE-REMOVE). Windows
# has no telegram task of ao's: `ao telegram install` refuses there. Linux's are SYSTEMD_JOBS, below.
LAUNCHD_JOBS = ("watchdog", "doctor", "telegram")
WINDOWS_TASKS = ("watchdog", "doctor")
# How long a job taken off may take to leave `launchctl list`, or to stop being active in a user
# systemd, before it counts as left.
JOB_GONE_SECONDS = 5


def _windows_task(job, key):
    return f"ao-{job}-{key.lower()}"


def _schtasks(*args):
    """(what schtasks printed, its exit status), asked without a shell; ("", None) when it cannot be run."""
    try:
        done = subprocess.run(["schtasks", *args], capture_output=True, text=True, encoding=UTF8, errors="replace")
    except OSError:
        return "", None
    return (done.stdout + done.stderr).strip(), done.returncode


def _scheduler():
    """Which scheduler holds ao's jobs here: Task Scheduler on Windows, a user systemd on Linux, else launchd."""
    if os.name == "nt":
        return "schtasks"
    return "systemd" if sys.platform.startswith("linux") else "launchd"


# ---- a user systemd, Linux's launchd (LINUX-SCHEDULER) ----------------------------------------
#
# Each job `ao watchdog install` schedules is two units in the user's own unit directory: a oneshot
# service that runs it once, and a timer that starts the service. Nothing here needs root: the units
# are the user's, run by the user's manager, and asked for with `systemctl --user`.

SYSTEMD_JOBS = ("watchdog", "doctor")
# Present while systemd is the init system (sd_booted(3)). Without it no manager can hold a unit,
# whatever a systemctl on PATH answers: a container, or WSL without systemd.
SYSTEMD_RUNTIME = "/run/systemd/system"
# The longest unit name systemd accepts, its suffix included.
SYSTEMD_NAME_MAX = 255
# What `systemctl is-active` says of a unit that is running or about to be.
SYSTEMD_UP = ("active", "activating", "reloading")
LINGER_ADVICE = ("the timers run while you are logged in; to keep them running after you log out: "
                 "loginctl enable-linger")

# The unit files, as a person reading ~/.config/systemd/user should understand them. A value in them
# is written by _systemd_word or _systemd_text, which keep a project's path from ending the line.
SYSTEMD_SERVICE = """# Written by `ao watchdog install`; `ao watchdog uninstall` removes it.
[Unit]
Description={description}

[Service]
Type=oneshot
ExecStart={argv}
Environment={path}
# The watchdog starts each nudge in a session of its own and exits. With systemd's default
# KillMode, a oneshot unit that ends kills every process left in its control group: the nudge too.
KillMode=process
StandardOutput=append:{log}
StandardError=append:{log}
"""

SYSTEMD_TIMER = """# Written by `ao watchdog install`; `ao watchdog uninstall` removes it.
[Unit]
Description={description}

[Timer]
# At once when the timer starts, as a launchd job's RunAtLoad, then every interval from the last
# start, as its StartInterval: to the second, not anywhere in the minute systemd allows by default.
OnActiveSec=0
OnUnitActiveSec={seconds}s
AccuracySec=1s

[Install]
WantedBy=timers.target
"""


def _systemctl(*args, merge=False):
    """(what `systemctl --user <args>` printed, its exit status), asked without a shell as launchctl is.

    Its standard error is discarded unless `merge` keeps it in the answer, where a failure says why.
    """
    return A._run_program(["systemctl", "--user", *args], stderr=subprocess.STDOUT if merge else subprocess.DEVNULL)


def _systemd_dir():
    """The user unit directory the jobs are written to, and the one a user manager reads by default."""
    return os.path.join(A.HOME, ".config", "systemd", "user")


def _systemd_unit(job, key):
    """The name, without its suffix, of the units that schedule one of ao's jobs for a project.

    `ao-<job>-<key>`, as the Windows task is named. A unit name holds letters, digits and `:_.-`
    only, and a project's key may hold any other character: each is written as systemd-escape
    writes it, `\\xNN` for each of its bytes, a backslash among them, so two keys stay two names.
    A name longer than systemd accepts keeps its first part and eight hex digits of the key's hash.
    """
    import hashlib
    prefix, key = f"ao-{job}-", key.lower()
    pieces = [ch if ch.isascii() and (ch.isalnum() or ch in ":_.-")
              else "".join(f"\\x{byte:02x}" for byte in ch.encode(UTF8, "surrogateescape")) for ch in key]
    room = SYSTEMD_NAME_MAX - len(prefix) - len(".service")
    if sum(map(len, pieces)) <= room:
        return prefix + "".join(pieces)
    kept = ""
    for piece in pieces:
        if len(kept) + len(piece) > room - 9:
            break
        kept += piece
    return f"{prefix}{kept}-{hashlib.sha256(key.encode(UTF8, 'surrogateescape')).hexdigest()[:8]}"


def _systemd_files(unit):
    """The paths of one job's service, its timer, and the link `systemctl --user enable` makes to the timer.

    The link is named as a file of the job's too: a disable that did not run leaves it pointing at
    a timer that is gone, and the manager then names that missing unit at every login.
    """
    return [os.path.join(_systemd_dir(), unit + ".service"), os.path.join(_systemd_dir(), unit + ".timer"),
            os.path.join(_systemd_dir(), "timers.target.wants", unit + ".timer")]


def _systemd_word(word, expand=True):
    """One word as systemd reads it back unchanged from ExecStart= or Environment= (systemd.syntax(7)).

    Double-quoted, with a backslash and a quote escaped and a control character written as its C
    escape, which systemd undoes inside quotes; a `%` doubled, since specifiers are expanded before
    the line is split into words; and, on a line that expands variables (ExecStart=), a `$` doubled.
    """
    out = []
    for ch in word:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\x{ord(ch):02x}")
        elif ch == "%" or (ch == "$" and expand):
            out.append(ch + ch)
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _systemd_text(text):
    """`text` as a value systemd reads with its specifiers and nothing else, such as Description=.

    A `%` doubled; a control character, which would end the line, and a backslash, which at the
    end of one joins the next line to it, each shown as `?`.
    """
    return "".join("?" if ord(ch) < 32 or ch in "\\\x7f" else ch for ch in text).replace("%", "%%")


def _systemd_unit_texts(unit, job, argv, seconds, log, root, path):
    """{file name: text} of the service that runs `argv` once and the timer that starts it every `seconds`."""
    return {unit + ".service": SYSTEMD_SERVICE.format(
                description=_systemd_text(f"ao {job} for {root}"),
                argv=" ".join(_systemd_word(word) for word in argv),
                path=_systemd_word(f"PATH={path}", expand=False), log=log.replace("%", "%%")),
            unit + ".timer": SYSTEMD_TIMER.format(
                description=_systemd_text(f"ao {job} for {root}, every {seconds}s"), seconds=seconds)}


def _systemd_interval(path):
    """The seconds between starts a timer written by `ao watchdog install` names; None when it names none."""
    try:
        with open(path, encoding=UTF8) as fh:
            for line in fh:
                name, _, value = line.strip().partition("=")
                if name == "OnUnitActiveSec" and value[:-1].isdigit() and value.endswith("s"):
                    return int(value[:-1])
    except (OSError, ValueError):
        return None
    return None


def _systemd_user_problem():
    """None when a user systemd answers here; else why none does, as a person reads it.

    The jobs run in the user's own manager, which `systemctl --user` reaches through
    $XDG_RUNTIME_DIR: a container or WSL without systemd has no manager at all, and a `su`
    or `sudo -u` shell has one it cannot reach.
    """
    if not os.path.isdir(SYSTEMD_RUNTIME):
        return "this machine does not run systemd (a container, WSL without systemd, or another init)"
    said, status = _systemctl("show", "--property=Version", merge=True)
    if status == 0:
        return None
    if status is None:
        return "systemctl could not be run"
    return "systemctl --user reaches no user manager: " + (said.split("\n")[0].strip() or f"exit {status}")


def _systemd_states(*units):
    """{unit: what `systemctl --user is-active` says of it}; None for each when the manager cannot be asked.

    It prints one state for each unit it is given, in order, and `inactive` for a unit it does not know.
    """
    said, status = _systemctl("is-active", *units)
    states = said.split("\n") if said else []
    if status is None or len(states) != len(units):
        return dict.fromkeys(units)
    return {unit: state.strip() for unit, state in zip(units, states)}


def _systemd_linger():
    """Whether this user's manager outlives their last session, "yes" or "no"; None when loginctl cannot say.

    Without it the manager stops when the last session ends, and every timer in it: on a server,
    the moment the person who installed the jobs logs out.
    """
    said, status = A._run_program(["loginctl", "show-user", str(os.getuid()), "--property=Linger"])
    name, _, value = said.partition("=")
    return value if status == 0 and name == "Linger" and value in ("yes", "no") else None


def _systemd_unschedule(timer):
    """Take one job's timer and service off the user systemd and out of its directory; what is left, or None.

    Without systemd as the init system nothing can hold them, and their files are all there is to
    remove. With it, the manager is asked to disable and stop them and then whether the timer is
    gone. A manager this shell cannot reach may still run them: they are then left as they are,
    files included, so that a session that reaches it still finds them to take off.
    """
    unit = timer[:-len(".timer")]
    files = _systemd_files(unit)
    booted = os.path.isdir(SYSTEMD_RUNTIME)
    if booted:
        problem = _systemd_user_problem()
        if problem:
            if not any(os.path.lexists(path) for path in files):
                return None                     # nothing of ao's is here, and a manager out of reach cannot be asked
            return f"{problem} — take it off from a login session of this user: ao watchdog uninstall"
        # What these two answer is not read: the timer's state afterwards is what decides.
        _systemctl("disable", "--now", timer)
        _systemctl("stop", unit + ".service")
    left = []
    for path in files:
        try:
            if os.path.lexists(path):
                os.remove(path)
        except OSError as exc:
            left.append(f"cannot remove {_home_relative(path)}: {exc}")
    if not booted:
        return "; ".join(left) or None
    _systemctl("daemon-reload")
    deadline = time.monotonic() + JOB_GONE_SECONDS
    state = _systemd_states(timer)[timer]
    while state in SYSTEMD_UP and time.monotonic() < deadline:
        time.sleep(0.25)
        state = _systemd_states(timer)[timer]
    if state is None:
        left.append(f"the user manager stopped answering — systemctl --user is-active {timer}")
    elif state in SYSTEMD_UP:
        left.append(f"still {state} — systemctl --user disable --now {timer}")
    return "; ".join(left) or None


def _installed_jobs(key):
    """(kind, name) of each job ao scheduled for a project that is there to remove on this platform.

    A user systemd loads ao's units from the files `ao watchdog install` writes and from nowhere
    else, so on Linux a job is there while one of its files is.
    """
    scheduler = _scheduler()
    if scheduler == "schtasks":
        return [("scheduled task", name) for name in (_windows_task(job, key) for job in WINDOWS_TASKS)
                if _schtasks("/Query", "/TN", name)[1] == 0]
    if scheduler == "systemd":
        return [("systemd timer", unit + ".timer") for unit in (_systemd_unit(job, key) for job in SYSTEMD_JOBS)
                if any(os.path.lexists(path) for path in _systemd_files(unit))]
    return [("launchd job", label) for label in (_launchd_label(job, key) for job in LAUNCHD_JOBS)
            if _launchd_loaded(label) or os.path.lexists(_launchd_plist(label))]


def _unschedule(name):
    """Remove one scheduled job in this process and check that it is gone; what is left of it, or None.

    `ao remove` ran `python -m ao watchdog uninstall` and did not read the result: from a clone
    that interpreter has no ao module, the jobs went on running against a removed project, and
    remove said they were gone. The telegram poller, which launchd keeps alive, was never removed.
    """
    scheduler = _scheduler()
    if scheduler == "schtasks":
        said, status = _schtasks("/Delete", "/TN", name, "/F")
        if _schtasks("/Query", "/TN", name)[1] == 0:
            return f"Task Scheduler still holds it: {said[:120] or f'schtasks exit {status}'}"
        return None
    if scheduler == "systemd":
        return _systemd_unschedule(name)
    plist, left = _launchd_plist(name), []
    if _launchd_loaded(name) and _launchctl("bootout", _launchd_domain(name))[1] != 0 and os.path.exists(plist):
        _launchctl("unload", plist)
    try:
        if os.path.lexists(plist):
            os.remove(plist)
    except OSError as exc:
        left.append(f"cannot remove {_home_relative(plist)}: {exc}")
    deadline = time.monotonic() + JOB_GONE_SECONDS
    while _launchd_loaded(name):
        if time.monotonic() >= deadline:
            left.append(f"still loaded — launchctl bootout {_launchd_domain(name)}")
            break
        time.sleep(0.25)
    return "; ".join(left) or None


def cmd_remove(cfg, args):
    """Take ao off only after every reachable hook target passes one preflight, and while no lane stands."""
    root = cfg["root"]
    key, owned = _remove_key(root)
    from . import skillkit
    harness_files, _ = skillkit.ao_files(root)
    # Found as `ao uninstall` finds them, an entry named ao taken only when it runs ao's server, and read
    # now: the adapters that declare the files may be the project's own, in the .ao/ this removes
    # (UPDATE-UNINSTALL-2).
    mcp_entries, mcp_unread = _mcp_entries(root)
    plan = [PROJECT_MARKER, ".ao/", "agent-mail/", cfg.get("reviews", "semantic-review") + "/"] + harness_files
    print(f"{C['b']}ao remove{C['reset']} would delete from {root}:")
    for rel in plan:
        if os.path.exists(os.path.join(root, rel)):
            print(f"   {rel}")
    for path, _, _ in mcp_entries:
        print(f"   {os.path.relpath(path, root)}: the `ao` server entry (other entries stay)")
    for path, why in mcp_unread:
        print(f"   {C['red']}cannot read {os.path.relpath(path, root)}{C['reset']}: {why}; any `ao` entry in it stays")
    if _gitignore_without_ao(root)[2]:
        print("   .gitignore: the lines ao added")
    # Outside the tree, each thing by name, found as the removal below finds it (SAFE-REMOVE).
    for kind, name in _installed_jobs(key) if owned else []:
        print(f"   {kind} {name}")
    for path in _project_home_files(key) if owned else []:
        print(f"   {_home_relative(path)}")
    for name in _registry_names(root):
        print(f"   {_home_relative(A.project_registry_path())}: the `{name}` entry (other projects stay)")
    archive = os.path.join(A.HOME, ".ao", "archive", key)
    print(f"   {C['dim']}hook files only when statically AO-owned, untracked, and fully authorized; "
          f"protected dead-misplaced files are preserved{C['reset']}")
    print(f"   {C['dim']}not touched: product files, reviews you moved elsewhere, "
          + (f"what ao archived in {_home_relative(archive)}/, " if owned and os.path.isdir(archive) else "")
          + f"other projects' and the machine's files in ~/.ao, "
          f"{' / '.join(skillkit.rule_file_names(root))} text (paste-in was yours){C['reset']}")
    if not owned:
        print(f"   {C['dim']}nothing of ~/.ao: no .ao/ here, and the registry holds no project at this path"
              f"{C['reset']}")
    # A lane's record is in .ao/, and with it gone no ao command could retire the lane's worktree or branch.
    lanes = A.lane_records(root)
    for lane in lanes:
        who = lane.get("item") or lane["name"]
        print(f"   {C['red']}refused while it stands{C['reset']}: lane {who} at {lane.get('path') or '-'}; "
              f"`ao lane remove {who}` retires it first")
    if not args.yes:
        print(f"\nre-run with {C['b']}--yes{C['reset']} to do it")
        return 0
    if lanes:
        print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: {len(lanes)} lane(s) stand, and "
              f".ao/ holds what retires them: `ao lane remove {lanes[0].get('item') or lanes[0]['name']}`"
              + (" and the rest, as `ao lane list` shows" if len(lanes) > 1 else "") + " first")
        return 1

    enrollment = _project_enrollment(root)
    if enrollment["state"] == "broken":
        print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
              f"{enrollment['detail']}")
        return 1
    if enrollment["state"] == "enrolled":
        marker_path = os.path.join(root, PROJECT_MARKER)
        if os.path.lexists(marker_path):
            if os.path.isdir(marker_path) and not os.path.islink(marker_path):
                print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
                      f"{PROJECT_MARKER} is a directory")
                return 1
            try:
                os.remove(marker_path)
            except OSError as exc:
                print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
                      f"cannot remove {PROJECT_MARKER}: {exc}")
                return 1
            print(f"removed working-tree {PROJECT_MARKER}")
        print(f"{C['yellow']}phase 1/2{C['reset']} — AO project state and enforcement remain active")
        print(f"  stage the marker deletion: git add -u -- {PROJECT_MARKER}")
        print("  authorize and commit that deletion, then run: ao remove --yes")
        return 0

    allow = bool(getattr(args, "allow_shared_hooks", False))
    inventory = _ao_hook_inventory(root)
    safe, hook_plan, _ = _remove_hook_preflight(inventory, allow)
    if not safe:
        return 1
    for target in hook_plan:
        try:
            os.remove(target["path"])
            print(f"removed {target['role']} — {target['path']}")
        except OSError as exc:
            print(f"{C['red']}failed to remove hook; state kept intact{C['reset']} "
                  f"{target['path']}: {exc}")
            return 1

    # Re-resolve before the first state deletion. A topology race cannot turn a
    # shared live hook into an unrecorded survivor behind deleted AO state.
    after_hooks = _ao_hook_inventory(root)
    safe, new_plan, _ = _remove_hook_preflight(after_hooks, allow)
    if not safe or new_plan:
        print(f"{C['red']}hook topology changed during remove; state kept intact{C['reset']}")
        return 1

    # The jobs go first, in this process, and each is checked gone (SAFE-REMOVE). A job left
    # behind runs against the project every few minutes, so its state stays until none is left.
    left = []
    for kind, name in _installed_jobs(key) if owned else []:
        problem = _unschedule(name)
        if problem:
            left.append(name)
            print(f"{C['red']}left{C['reset']} {kind} {name}: {problem}")
        else:
            print(f"removed {kind} {name}")
    if left:
        print(f"{C['red']}remove stopped; AO state kept intact{C['reset']}: {len(left)} scheduled job(s) would go on "
              "running against a removed project — remove them, then run: ao remove --yes")
        return 1

    import shutil as _sh
    for rel in plan:
        p = os.path.join(root, rel)
        if os.path.isdir(p):
            _sh.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    for path, mcp_key, remove_when_empty in mcp_entries:
        shown = os.path.relpath(path, root)
        try:
            gone = _drop_mcp_entry(path, mcp_key, remove_when_empty)
        except (OSError, ValueError) as exc:
            left.append(path)
            print(f"{C['red']}left{C['reset']} the `ao` entry in {shown}: {' '.join(str(exc).split())}")
            continue
        print(f"removed {shown}, which held only ao's entry" if gone == "file"
              else f"removed the `ao` entry from {shown}")
    for path, why in mcp_unread:
        left.append(path)
        print(f"{C['red']}left{C['reset']} {os.path.relpath(path, root)}: it cannot be read ({why}), "
              "so any `ao` entry in it stays")
    gi, keep, dropped = _gitignore_without_ao(root)
    if dropped:
        open(gi, "w", encoding=UTF8).write("\n".join(keep))
    # Exactly this project's files in ~/.ao, named by the table their writers name them from, and
    # its row of the registry, which stays for every other project (SAFE-REMOVE).
    removed = 0
    for path in _project_home_files(key) if owned else []:
        try:
            os.remove(path)
            removed += 1
        except OSError as exc:
            left.append(path)
            print(f"{C['red']}left{C['reset']} {_home_relative(path)}: {exc}")
    if removed:
        print(f"removed {removed} file(s) of {key} from {_home_relative(os.path.join(A.HOME, '.ao'))}")
    registry = _home_relative(A.project_registry_path())
    try:
        for name in A.forget_project(root) if owned else []:
            print(f"removed the `{name}` entry from {registry}")
    except (OSError, ValueError) as exc:
        left.append(registry)
        print(f"{C['red']}left{C['reset']} the `{key}` entry in {registry}: {exc}")
    if left:
        print(f"{C['red']}not complete{C['reset']} — phase 2/2 left {len(left)} thing(s) behind, each named above")
        return 1
    print(f"{C['green']}removed{C['reset']} — phase 2/2 complete; AO state is gone and preserved local source hooks are inert")
    return 0


def _alive(pid):
    # os.kill(pid, 0) is CTRL_C_EVENT on Windows, not a question (#71).
    return A._pid_alive(pid)


# What each store is for. The split matters: pruning operational noise is
# housekeeping, pruning evidence destroys the record that commit authority was
# granted against — so they cannot share a default.
STORES = [
    ("progress",      "operational", ".ao/ledger/progress.jsonl",      "watchdog samples behind the spin check"),
    ("notices",       "operational", ".ao/ledger/notices.jsonl",       "alerts raised and suppressed"),
    ("inbox",         "operational", ".ao/inbox",                      "imported source pulls (*.imported)"),
    ("verifications", "evidence",    ".ao/ledger/verifications.jsonl", "measured gate results"),
    ("plans",         "evidence",    ".ao/ledger/plans.jsonl",         "plan hashes as admitted"),
]
HOME_LOGS = ["nudge-log", "watchdog-log", "refill-log"]         # PROJECT_FILES


def _prune_jsonl(path, cutoff, dry):
    """Drop records older than cutoff. Returns (dropped, kept, bytes_freed)."""
    if not os.path.exists(path):
        return 0, 0, 0
    before = os.path.getsize(path)
    keep = []
    dropped = 0
    for line in open(path, errors="replace", encoding=UTF8):
        if not line.strip():
            continue
        try:
            if json.loads(line).get("at", 0) < cutoff:
                dropped += 1
                continue
        except Exception:
            pass                                  # unparseable: keep it, do not silently lose data
        keep.append(line if line.endswith("\n") else line + "\n")
    if dropped and not dry:
        from .storage import replace_file_durably
        replace_file_durably(path, "".join(keep).encode(UTF8))
    freed = before - sum(len(k.encode()) for k in keep) if dropped else 0
    return dropped, len(keep), max(0, freed)


def cmd_prune(cfg, args):
    """Trim the records this tool accumulates, without touching the audit trail.

    Every store here grows monotonically — one nudge log reached 295 KB in a
    night, and the progress ledger gains a row every two minutes. Left alone they
    become the reason someone stops running the tool.

    Evidence is excluded by default and needs `--evidence`. Verification records
    and plan hashes are what commit authority was granted against; deleting them
    as housekeeping would quietly remove the ability to answer "on what basis did
    this land".
    """
    root = cfg["root"]
    cutoff = time.time() - args.days * 86400
    dry = not args.yes
    key = A.project_key(root)
    print(f"{C['b']}pruning records older than {args.days} day(s){C['reset']}"
          f"{C['dim']}  {root}{C['reset']}")
    if dry:
        print(f"{C['yellow']}dry run — add --yes to apply{C['reset']}")

    total = 0
    for name, kind, rel, desc in STORES:
        if kind == "evidence" and args.evidence:
            # Evidence is never pruned. A chained ledger past its bound is sealed:
            # its oldest rows move whole to .ao/ledger/sealed/ and stay readable (#50).
            keep = S.get(cfg, "retention.evidence_keep")
            sealed = None
            if name == "verifications" and not dry:
                from .storage import seal_chained_jsonl
                sealed = seal_chained_jsonl(os.path.join(root, rel), A.VERIFICATION_CHAIN, keep, legacy_prefix=True)
            print(f"  {C['dim']}keep  {name:<14} evidence is never pruned; "
                  + (f"sealed through row {sealed['retired']} into .ao/ledger/sealed/" if sealed
                     else f"rows past the newest {keep} are sealed, not dropped") + f"{C['reset']}")
            continue
        if kind == "evidence" and not args.evidence:
            path = os.path.join(root, rel)
            if os.path.exists(path):
                n = sum(1 for _ in open(path, errors="replace", encoding=UTF8))
                print(f"  {C['dim']}skip  {name:<14} {n} records — evidence, needs --evidence{C['reset']}")
            continue
        path = os.path.join(root, rel)
        if name == "inbox":
            if not os.path.isdir(path):
                continue
            gone = 0
            for f in os.listdir(path):
                fp = os.path.join(path, f)
                if f.endswith(".imported") and os.path.getmtime(fp) < cutoff:
                    total += os.path.getsize(fp)
                    gone += 1
                    if not dry:
                        os.remove(fp)
            if gone:
                print(f"  {C['green']}{'would drop' if dry else 'dropped'}{C['reset']}  "
                      f"{name:<14} {gone} file(s)  {C['dim']}{desc}{C['reset']}")
            continue
        # What the prune drops still counts in a window once folded (NOTICE-WINDOW); a fold that could not be
        # written leaves the ledger whole (NOTICE-WINDOW-2).
        if name == "notices" and not dry and not A.fold_notice_times(root):
            print(f"  {C['yellow']}kept{C['reset']}   {name:<14} the notices could not be folded, and what no fold "
                  f"counted is not dropped  {C['dim']}{desc}{C['reset']}")
            continue
        dropped, kept, freed = _prune_jsonl(path, cutoff, dry)
        total += freed
        if dropped:
            print(f"  {C['green']}{'would drop' if dry else 'dropped'}{C['reset']}  "
                  f"{name:<14} {dropped} of {dropped + kept}  {C['dim']}{desc}{C['reset']}")

    # Review artefacts stay while anything rests on them; the rest leave by age (#38).
    review_days = getattr(args, "review_days", None)
    if review_days is None:
        review_days = S.get(cfg, "review.prune_after_days")
    try:
        outcome = A.prune_review_artefacts(root, cfg, review_days, apply=not dry)
    except Exception as exc:
        print(f"  {C['yellow']}keep  {'reviews':<14} every artefact: what rests on them cannot be read "
              f"({exc}){C['reset']}")
    else:
        why = {}
        for reasons in outcome["kept"].values():
            for reason in reasons:
                why[reason] = why.get(reason, 0) + 1
        if outcome["moved"]:
            total += outcome["bytes"]
            print(f"  {C['green']}{'would move' if dry else 'moved'}{C['reset']}  {'reviews':<14} "
                  f"{len(outcome['moved'])} older than {review_days:g} day(s) that nothing rests on  "
                  f"{C['dim']}→ {outcome['archive']}{C['reset']}")
        if outcome["kept"] or outcome["recent"]:
            detail = ", ".join(f"{count} {reason}" for reason, count in sorted(why.items()))
            print(f"  {C['dim']}keep  {'reviews':<14} {len(outcome['kept'])} referenced"
                  + (f" ({detail})" if detail else "") + f", {outcome['recent']} recent{C['reset']}")

    # The authority chain is never truncated by deletion: past its bound it is sealed (#50).
    if args.evidence and not dry:
        from .storage import seal_chained_jsonl
        sealed = seal_chained_jsonl(os.path.join(root, ".ao", "ledger", "authority.jsonl"), A.AUTHORITY_CHAIN,
                                    S.get(cfg, "retention.evidence_keep"))
        if sealed:
            print(f"  {C['green']}sealed{C['reset']}  {'authority':<14} through row {sealed['retired']}; "
                  f"{C['dim']}ao commit-check validates across the seal{C['reset']}")
    # Dedupe by inode, not by path string: this filesystem is case-insensitive, so
    # "nudge-Acme-API.log" and "nudge-acme-api.log" are one file that would
    # otherwise be counted — and truncated — twice.
    seen_inodes = set()
    for name in HOME_LOGS:
        path = os.path.join(A.HOME, ".ao", A.project_file_name(name, key))
        alt = os.path.join(A.HOME, ".ao", A.project_file_name(name, key.lower()))
        for pth in {path, alt}:
            if not os.path.exists(pth):
                continue
            ino = os.stat(pth).st_ino
            if ino in seen_inodes:
                continue
            seen_inodes.add(ino)
            size = os.path.getsize(pth)
            if size < args.keep_kb * 1024:
                continue
            if not dry:
                # keep the tail: the last turn's output is the only part anyone
                # reads, and it is what a failed nudge is diagnosed from
                with open(pth, errors="replace", encoding=UTF8) as fh:
                    fh.seek(max(0, size - args.keep_kb * 1024))
                    tail = fh.read()
                with open(pth, "w", encoding=UTF8) as fh:
                    fh.write(f"[truncated by ao prune {datetime.now():%Y-%m-%d %H:%M}]\n" + tail)
            total += size - args.keep_kb * 1024
            print(f"  {C['green']}{'would trim' if dry else 'trimmed'}{C['reset']}  "
                  f"{os.path.basename(pth):<24} {size // 1024}KB → {args.keep_kb}KB")

    print(f"\n{C['b']}{total // 1024}KB{C['reset']} {'reclaimable' if dry else 'reclaimed'}")
    return 0


def _notice_facts(row):
    """One recorded notice as `ao notices --json` prints it: every key present, null where an older row has none."""
    return {"id": row.get("id"), "at": row.get("at"), "title": row.get("title"), "msg": row.get("msg"),
            "sent": bool(row.get("sent")), "key": row.get("key"), "evidence": row.get("evidence")}


def cmd_notices(cfg, args):
    """Alerts this project raised — the desktop notification, kept.

    A notification reaches the human and vanishes, so the architect reading the
    panel is the one participant who never sees what the human was told.

    With --json the same rows are one JSON document and nothing else (JSON-OUTPUT): the list,
    or the one notice an id names, with null and exit 1 for an id no notice has.
    """
    root = cfg["root"]
    wanted = getattr(args, "ident", None)
    as_json = getattr(args, "json", False)
    if wanted:
        # "Why did I get this?" is one command (#37).
        row = next((r for r in A.notices(root, 10**9, include_suppressed=True) if r.get("id") == wanted), None)
        if not row:
            missing = f"no notice {wanted}; `ao notices --all` lists them with their ids"
            print(json.dumps({"notice": None, "error": missing}, ensure_ascii=False) if as_json else missing)
            return 1
        if as_json:
            print(json.dumps({"notice": _notice_facts(row)}, ensure_ascii=False))
            return 0
        when = datetime.fromtimestamp(row["at"]).strftime("%d %b %H:%M")
        print(f"{C['b']}{row['title']}{C['reset']}  {C['dim']}{wanted} · {when} · "
              f"{'sent' if row.get('sent') else 'held'} · key {row.get('key')}{C['reset']}")
        print(f"  {row.get('msg')}")
        for line in A.evidence_lines(row.get("evidence")):
            print(f"  {line}")
        return 0
    rows = A.notices(root, args.n, include_suppressed=args.all)
    if as_json:
        print(json.dumps({"notices": [_notice_facts(r) for r in rows], "include_suppressed": bool(args.all)},
                         ensure_ascii=False))
        return 0
    if not rows:
        print(f"{C['dim']}No notices recorded.{C['reset']}")
        return 0
    for r in rows:
        when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
        tag = (f"{C['green']}sent{C['reset']}" if r.get("sent")
               else f"{C['dim']}held{C['reset']}")
        print(f"  {C['dim']}{when}{C['reset']}  {tag}  {C['b']}{r['title']}{C['reset']}  {r['msg']}"
              + (f"  {C['dim']}{r['id']}{' · evidence' if r.get('evidence') else ''}{C['reset']}"
                 if r.get("id") else ""))
    if not args.all:
        print(f"{C['dim']}  (--all also shows alerts the rate limit suppressed){C['reset']}")
    return 0


def _package_installed():
    """Whether this interpreter imports ao from its own site directories, as a scheduler starting it would.

    A clone puts its own src/ on the import path; a scheduled `python -m ao` from one fails
    with "No module named ao".
    """
    import site
    here = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(A.__file__))))
    sites = list(site.getsitepackages()) if hasattr(site, "getsitepackages") else []
    sites.append(site.getusersitepackages())
    return any(os.path.realpath(path) == here for path in sites)


def _scheduled_argv(console, clone, module):
    """The words a scheduled job starts one of ao's entry points with; None when there are none (SAFE-REMOVE).

    Install fell back to the clone's script whether or not there was a clone: a package whose
    console scripts are not on PATH, as `pip install --user` leaves it, has no such file, and
    launchd ran a missing file every two minutes while install said "installed". In order:
    the console script on PATH, which carries its own interpreter in its shebang (prefixing
    this process's python imports ao from an interpreter that may not have it); the clone's
    script, with this interpreter, since its shebang is `/usr/bin/env python3`; this
    interpreter running the module, when it imports ao from its own site directories. A path found
    through a relative PATH entry is made absolute: a job starts from `/`, not from here (SAFE-REMOVE-2).
    """
    found = shutil.which(console)
    found = os.path.abspath(found) if found else None
    python = sys.executable if sys.executable and os.path.isfile(sys.executable) else None
    if found:
        in_repo = os.path.realpath(found).startswith(os.path.realpath(A.REPO) + os.sep)
        return ([python] if in_repo and python else []) + [found]
    script = os.path.join(A.REPO, *clone)
    if python and os.path.isfile(script):
        return [python, script]
    if python and _package_installed():
        return [python, "-m", module]
    return None


def _schedule_refused(missing):
    print(f"{C['red']}not installed{C['reset']}: nothing a scheduler can start for {' or '.join(missing)} — not on "
          "PATH, no clone beside this code, and this interpreter imports no installed ao")
    print("  put the directory holding ao's console scripts on PATH, or install the jobs from a clone")
    return 1


def _watchdog_programs():
    """(the watchdog's words, the doctor's words) a scheduler starts; None, once refused, when either has none.

    launchd and a user systemd both need an absolute program that exists: resolve the one this
    installation has, or refuse before anything is written, since a job naming a missing file
    fails silently (SAFE-REMOVE).
    """
    wargv = _scheduled_argv("ao-watchdog", ("scripts", "ao-watchdog"), "ao.watchdog")
    dargv = _scheduled_argv("ao", ("bin", "ao"), "ao")
    missing = [name for name, argv in (("ao-watchdog", wargv), ("ao", dargv)) if argv is None]
    if missing:
        _schedule_refused(missing)
        return None
    return wargv, dargv


def _job_log(what, key):
    """Where a scheduled job's output goes: its PROJECT_FILES log in ~/.ao, `watchdog-log` or `doctor-log`."""
    return os.path.join(A.HOME, ".ao", A.project_file_name(what, key))


def _forget_heartbeat(root):
    """Remove the watchdog's heartbeat with its jobs.

    A heartbeat left behind reads as a dead watchdog to every other project (audit).
    """
    try:
        os.remove(A.heartbeat_path(root))
    except OSError:
        pass


def _print_watchdog_health(root, log):
    """How the recorded cycles went and the log's last lines, as `ao watchdog status` shows them under any scheduler."""
    from .watchdog import cycle_health
    health = cycle_health(root)
    if health:
        took = (f"longest took {health['longest_seconds']:.0f}s at {health['longest_at']}"
                if health["longest_seconds"] is not None else "durations not recorded yet")
        gap = (f"longest gap {health['gap_minutes']:.0f}m before {health['gap_at']}"
               if health["gap_at"] else "no gaps")
        print(f"cycles  {health['count']} recorded · {took} · {gap}")
    if os.path.exists(log):
        print(f"\nlast lines of {log}:")
        print(_log_tail(log))


def _systemd_absent(problem, argvs, seconds, logs):
    """Say that no user systemd answers here, why, and what runs the same two jobs without it; 1.

    A crontab is the scheduler nearly every Linux has without systemd, and its lines are what
    a person can paste: the words run through /bin/sh, so each is quoted, and cron reads a `%`
    as a line break unless it is escaped.
    """
    import shlex
    print(f"{C['red']}not installed{C['reset']}: no user systemd — {problem}")
    print("  nothing was written. Any scheduler this machine has can run the two jobs; a crontab, for instance:")
    for job in SYSTEMD_JOBS:
        minutes = max(1, -(-seconds[job] // 60))
        line = f"*/{minutes} * * * * {shlex.join(argvs[job])} >> {shlex.quote(logs[job])} 2>&1"
        print("    " + line.replace("%", "\\%"))
    print(f"  or one watchdog cycle by hand: {shlex.join(argvs['watchdog'])}")
    return 1


def _watchdog_systemd(cfg, args):
    """Install, remove or inspect the two jobs as systemd user units: a user systemd is Linux's launchd.

    Each job is a oneshot service and a timer that starts it at once and then every interval, as
    the launchd job's RunAtLoad and StartInterval do, written to ~/.config/systemd/user and enabled
    with `systemctl --user`, asked by argument vector. Where no user systemd answers, nothing is
    written, and what would run the jobs instead is printed (LINUX-SCHEDULER).
    """
    root = cfg["root"]
    key = A.project_key(root).lower()
    units = {job: _systemd_unit(job, key) for job in SYSTEMD_JOBS}
    timers = {job: unit + ".timer" for job, unit in units.items()}
    logs = {job: _job_log(f"{job}-log", key) for job in SYSTEMD_JOBS}

    if args.action == "status":
        problem = _systemd_user_problem()
        states = dict.fromkeys(timers.values()) if problem else _systemd_states(*timers.values())
        present = any(os.path.lexists(path) for unit in units.values() for path in _systemd_files(unit))
        print(f"unit    {timers['watchdog']}")
        print(f"timer   {states[timers['watchdog']] or 'unknown'}")
        print(f"doctor  {states[timers['doctor']] or 'unknown'}  (ao doctor --check every 15m)")
        print(f"units   {'present' if present else 'absent'}  ({_home_relative(_systemd_dir())})")
        if problem:
            print(f"systemd {C['yellow']}{problem}{C['reset']}")
        else:
            linger = _systemd_linger()
            print("linger  " + (f"no — {LINGER_ADVICE}" if linger == "no" else linger or "unknown"))
        _print_watchdog_health(root, logs["watchdog"])
        return 0

    if args.action == "uninstall":
        left = 0
        for unit in units.values():
            present = any(os.path.lexists(path) for path in _systemd_files(unit))
            problem = _systemd_unschedule(unit + ".timer")
            if problem:
                left += 1
                print(f"{C['red']}left{C['reset']} {unit}.timer: {problem}")
            elif present:
                print(f"removed {unit}.timer")
        _forget_heartbeat(root)
        return 1 if left else 0

    if args.interval < 1:
        # OnUnitActiveSec=0 starts the next cycle the moment one ends.
        print(f"{C['red']}not installed{C['reset']}: --interval is the seconds between cycles, at least 1")
        return 1
    programs = _watchdog_programs()
    if programs is None:
        return 1
    wargv, dargv = programs
    argvs = {"watchdog": wargv + ["--root", root, "--idle-minutes", str(args.idle_minutes)],
             "doctor": dargv + ["-C", root, "doctor", "--check", "--notify"]}
    seconds = {"watchdog": args.interval, "doctor": 900}
    problem = _systemd_user_problem()
    if problem:
        return _systemd_absent(problem, argvs, seconds, logs)
    # StandardOutput=append: takes a path as it stands, with no escape a line break could be written as.
    broken = [log for log in logs.values() if any(ord(ch) < 32 or ord(ch) == 127 for ch in log)]
    if broken:
        print(f"{C['red']}not installed{C['reset']}: a unit cannot send output to a path holding a control "
              f"character: {broken[0]!r}")
        return 1
    os.makedirs(_systemd_dir(), exist_ok=True)
    os.makedirs(os.path.join(A.HOME, ".ao"), exist_ok=True)
    path = _scheduled_path()
    for job, unit in units.items():
        for name, text in _systemd_unit_texts(unit, job, argvs[job], seconds[job], logs[job], root, path).items():
            with open(os.path.join(_systemd_dir(), name), "w", encoding=UTF8) as fh:
                fh.write(text)
    # Restarted rather than started: on a reinstall a timer that is already running starts again and
    # runs a cycle at once, as the launchd job's bootout and bootstrap do; a start would run none.
    failed = []
    for step in (("daemon-reload",), ("enable", *timers.values()), ("restart", *timers.values())):
        said, status = _systemctl(*step, merge=True)
        if status != 0:
            failed.append(said.split("\n")[0].strip() or f"systemctl --user {step[0]} exited {status}")
    states = _systemd_states(*timers.values())
    for timer, state in states.items():
        if state not in SYSTEMD_UP:
            failed.append(f"{timer} is {state or 'not known to the user manager'}")
    if failed:
        print(f"{C['red']}NOT SCHEDULED{C['reset']}: {'; '.join(failed)[:240]}")
        print(f"  run: systemctl --user enable --now {timers['watchdog']} {timers['doctor']}")
        return 1
    print(f"installed {timers['watchdog']}")
    print(f"installed {timers['doctor']}  (ao doctor --check --notify every 15m — the second, independent check)")
    print(f"  checks every {args.interval}s · nudges after {args.idle_minutes}m idle")
    print(f"  units: {_home_relative(_systemd_dir())}")
    print(f"  log: {logs['watchdog']}")
    print(f"  remove with: ao -C {root} watchdog uninstall")
    if _systemd_linger() == "no":
        print(f"  {C['yellow']}{LINGER_ADVICE}{C['reset']}")
    return 0


def _watchdog_windows(cfg, args):
    """Task Scheduler is Windows' launchd: one task every two minutes, one every fifteen."""
    root = cfg["root"]
    key = A.project_key(root)
    idle = getattr(args, "idle_minutes", None) or S.get(cfg, "watchdog.idle_minutes")
    tasks = {_windows_task("watchdog", key): (2, ("ao-watchdog", ("scripts", "ao-watchdog"), "ao.watchdog"),
                                              ["--root", root, "--idle-minutes", str(idle)]),
             _windows_task("doctor", key): (15, ("ao", ("bin", "ao"), "ao"),
                                            ["-C", root, "doctor", "--check", "--notify"])}
    if args.action == "status":
        for name in tasks:
            r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True, text=True, encoding=UTF8, errors="replace")
            print(f"{name}: {'scheduled' if r.returncode == 0 else 'absent'}")
        return 0
    if args.action == "uninstall":
        for name in tasks:
            subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True)
            print(f"removed {name}")
        return 0
    # A task that names a missing program fails every run and says nothing (SAFE-REMOVE).
    argvs = {name: _scheduled_argv(*entry) for name, (_, entry, _) in tasks.items()}
    missing = [entry[0] for name, (_, entry, _) in tasks.items() if argvs[name] is None]
    if missing:
        return _schedule_refused(missing)
    failed = 0
    for name, (minutes, _, rest) in tasks.items():
        said, status = _schtasks("/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes), "/TN", name,
                                 "/TR", subprocess.list2cmdline(argvs[name] + rest))
        failed += status != 0
        print(f"{'installed' if status == 0 else 'FAILED'} {name} (every {minutes}m)"
              + ("" if status == 0 else f": {said[:120] or 'schtasks could not be run'}"))
    return 1 if failed else 0


def _log_tail(path, count=5):
    """A log's last `count` lines as `tail -<count>` printed them, stripped; "" when it cannot be read.

    Read back from the end in blocks: a job's log grows for as long as the job runs.
    """
    try:
        with open(path, "rb") as fh:
            position, data = fh.seek(0, os.SEEK_END), b""
            while position and data.count(b"\n") <= count:
                step = min(position, 65536)
                position -= step
                fh.seek(position)
                data = fh.read(step) + data
    except OSError:
        return ""
    lines = data.split(b"\n")
    if data.endswith(b"\n"):
        lines.pop()
    # Decoded as sh() decoded tail's output: text mode also reads a carriage return as a newline.
    text = b"\n".join(lines[-count:]).decode(UTF8, "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def cmd_watchdog(cfg, args):
    if getattr(args, "idle_minutes", None) is None:
        args.idle_minutes = S.get(cfg, "watchdog.idle_minutes")
    return _cmd_watchdog(cfg, args)


def _cmd_watchdog(cfg, args):
    """Install, remove or inspect the jobs that restart a stalled agent: launchd's, a user systemd's on Linux,
    Task Scheduler's on Windows."""
    if args.action in ("explain", "trace"):
        return _watchdog_debug(cfg, args)
    scheduler = _scheduler()
    if scheduler == "schtasks":
        return _watchdog_windows(cfg, args)
    if scheduler == "systemd":
        return _watchdog_systemd(cfg, args)
    root = cfg["root"]
    key = A.project_key(root).lower()
    label = _launchd_label("watchdog", key)
    plist_path = _launchd_plist(label)
    log = _job_log("watchdog-log", key)

    if args.action == "status":
        loaded = _launchd_listed(label)
        dloaded = _launchd_listed(_launchd_label("doctor", key))
        print(f"label   {label}")
        print(f"doctor  {'loaded' if dloaded else 'not installed'}  (ao doctor --check every 15m)")
        print(f"plist   {'present' if os.path.exists(plist_path) else 'absent'}")
        print(f"loaded  {loaded if loaded else 'no'}")
        _print_watchdog_health(root, log)
        return

    if args.action == "uninstall":
        if _launchctl("bootout", _launchd_domain(label))[1] != 0:
            _launchctl("unload", plist_path)
        if os.path.exists(plist_path):
            os.remove(plist_path)
        print(f"removed {label}")
        dlabel = _launchd_label("doctor", key)
        dplist = _launchd_plist(dlabel)
        _launchctl("bootout", _launchd_domain(dlabel))
        if os.path.exists(dplist):
            os.remove(dplist)
            print(f"removed {dlabel}")
        _forget_heartbeat(root)
        return

    programs = _watchdog_programs()
    if programs is None:
        return 1
    wargv, dargv = programs
    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
    os.makedirs(os.path.join(A.HOME, ".ao"), exist_ok=True)
    open(plist_path, "w", encoding=UTF8).write(PLIST.format(
        path=_launchd_path(), label=label, python_arg="".join(f"<string>{a}</string>" for a in wargv[:-1]),
        script=wargv[-1], root=root,
        idle=args.idle_minutes, interval=args.interval, log=log))
    _launchctl("bootout", _launchd_domain(label))
    # bootout is asynchronous: a bootstrap issued before the old job is fully
    # gone fails with "5: Input/output error" and leaves nothing loaded — a
    # reinstall that silently uninstalled. Wait for the label to clear, retry.
    out = ""
    for attempt in range(5):
        if _launchd_listed(label):
            time.sleep(1)
        out, status = _launchctl("bootstrap", _launchd_domain(), plist_path, merge=True)
        out = out or ("launchctl could not be run" if status is None else "loaded")
        if "error" not in out.lower() or _launchd_listed(label):
            break
        time.sleep(1 + attempt)
    if not _launchd_listed(label):
        print(f"{C['red']}NOT LOADED{C['reset']} {label}: {out.strip()[:120]} — run: launchctl bootstrap gui/$(id -u) {plist_path}")
        return 1
    print(f"installed {label}")
    # The second, independent check. A watchdog cannot report its own death;
    # this job runs `ao doctor --check --notify` every fifteen minutes from its own
    # launchd entry and raises the alarm the watchdog would have.
    dlabel = _launchd_label("doctor", key)
    dplist = _launchd_plist(dlabel)
    dargs = dargv + ["-C", root, "doctor", "--check", "--notify"]
    open(dplist, "w", encoding=UTF8).write(PLIST_CMD.format(
        path=_launchd_path(), label=dlabel, args="".join(f"<string>{a}</string>" for a in dargs),
        interval=900, log=_job_log("doctor-log", key)))
    _launchctl("bootout", _launchd_domain(dlabel))
    _launchctl("bootstrap", _launchd_domain(), dplist, merge=True)
    print(f"installed {dlabel}  (ao doctor --check --notify every 15m — the second, independent check)")
    print(f"  checks every {args.interval}s · nudges after {args.idle_minutes}m idle")
    print(f"  log: {log}")
    print(f"  remove with: ao -C {root} watchdog uninstall")
    if "error" in out.lower():
        print(f"  launchctl: {out}")


def cmd_projects(cfg, args):
    ws = A.all_workspaces()
    if not ws:
        print("No local agent sessions found.")
    else:
        print(f"{'last active':<12}{'agent':<14}{'status':<14}workspace")
    for r in ws:
        mins = int((time.time() - r["mtime"]) / 60)
        age = f"{mins}m" if mins < 90 else (f"{mins//60}h" if mins < 2880 else f"{mins//1440}d")
        col = C["green"] if mins < 5 else C["dim"]
        # Every store an adapter declares is listed, and a store that keeps no status says none (SESSION-IDENTITY).
        print(f"{col}{age:<12}{C['reset']}{str(r['adapter'])[:13]:<14}{str(r['status'] or '—')[:13]:<14}{r['path']}")
    # Directories with one name used to share every file ao keeps outside them (#66).
    for base, rows in A.project_key_collisions().items():
        print(f"\n{C['yellow']}{len(rows)} projects are named {base}{C['reset']}; "
              "each keeps its own files under its key:")
        for key, where in rows:
            print(f"  {key:<28}{where}")


def cmd_adapters(cfg, args):
    """Every adapter ao can load, where it came from, and whether a candidate is sound (#77)."""
    root = cfg["root"]
    action = getattr(args, "action", None) or "list"
    if action in ("validate", "conform"):
        target = getattr(args, "target", None)
        catalog = A.adapter_catalog(root)
        if target in catalog:
            adapter = catalog[target]["adapter"]
        else:
            try:
                with open(target or "", encoding=UTF8) as fh:
                    adapter = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"{C['red']}no adapter {target}{C['reset']}: {exc}")
                return 2
        if action == "validate":
            problems = A.validate_adapter(adapter)
            for problem in problems:
                print(f"  {C['red']}·{C['reset']} {problem}")
            print(f"{C['green']}sound{C['reset']}" if not problems else f"{len(problems)} problem(s)")
            return 1 if problems else 0
        import tempfile
        harness = os.path.join(os.path.dirname(A.__file__), "conformance_harness.py")
        with tempfile.TemporaryDirectory(prefix="ao-conform-") as workdir:
            shim = os.path.join(workdir, "harness")
            with open(shim, "w", encoding=UTF8) as fh:
                fh.write(f"#!{sys.executable}\n" + open(harness, encoding=UTF8).read())
            os.chmod(shim, 0o755)
            results = A.conform_adapter(adapter, shim, workdir)
        for capability, state, detail in results:
            tone = C["green"] if state == "pass" else C["red"] if state == "fail" else C["dim"]
            print(f"  {capability:<11} {tone}{state}{C['reset']}  {C['dim']}{detail}{C['reset']}")
        return 1 if any(state == "fail" for _, state, _ in results) else 0
    avail = A.tool_availability()
    print(f"{'adapter':<16}{'source':<9}{'contract':<10}{'verified':<12}{'on this machine':<22}observation")
    for ident, entry in sorted(A.adapter_catalog(root).items()):
        a = entry["adapter"]
        if a.get("kind") == "cloud":
            continue
        if entry["problem"]:
            print(f"{ident:<16}{entry['source']:<9}{C['red']}refused{C['reset']}  {entry['problem']}")
            continue
        verified = a.get("verified", "?")
        col = C["green"] if verified == "full" else C["yellow"] if verified == "partial" else C["dim"]
        have = avail.get(ident, {})
        here = ("installed + account" if have.get("installed") and have.get("account") else "installed"
                if have.get("installed") else "account, no CLI" if have.get("account") else "—")
        observation = "call-return" if a.get("observation_mode") == "call-return" \
            else (a.get("transcript", {}) or {}).get("kind", "—")
        if observation in ("jsonl", "sqlite", "json", "markdown") and not A.session_store_reachable(a):
            observation = f"{observation}, not reached"
        eligible, _ = A.reviewer_eligibility(a)
        print(f"{ident:<16}{entry['source']:<9}{str(a.get('contract', A.ADAPTER_CONTRACT)):<10}{col}{verified:<12}"
              f"{C['reset']}{here:<22}{observation:<22}"
              f"{'reviewer: eligible' if eligible else C['dim'] + 'reviewer: ineligible' + C['reset']}")
    for vendor in A.vendor_list():
        if not vendor.get("adapter"):
            print(f"{vendor['id']:<16}{C['dim']}{'vendor':<9}{'—':<10}{'no adapter':<12}{vendor.get('why', '')}"
                  f"{C['reset']}")
    for problem in A.vendor_problems():
        print(f"{C['red']}vendor list{C['reset']}: {problem}")
    print(f"\n{C['dim']}Account detection via keyflip surfaces; it never reads the secret. Adapters load from the "
          f"package, then ~/.ao/adapters, then .ao/adapters; a later one overrides by id.{C['reset']}")


def _optional_features(cfg):
    """(name, state, what would enable it) for each optional capability, core excluded (#82)."""
    from . import email, telegram
    root = cfg["root"]
    mail = email.config()
    features = [
        ("keyflip", "installed" if shutil.which("keyflip") else "absent",
         "install keyflip for account budgets and quota rotation"),
        ("telegram", "configured" if telegram.config() else "absent", "ao telegram setup"),
        ("email", f"configured ({mail['provider']})" if mail else "absent", "ao email setup"),
        ("ping", "configured" if A.ping_url(root) else "absent", "ao ping set <url>"),
    ]
    # A tool reviewer is an optional extra with its own provider account (#86).
    primary = cfg.get("reviewer") or {}
    routes = [primary] + list(primary.get("fallbacks") or []) if isinstance(primary, dict) else []
    configured = {route.get("adapter") for route in routes if _tool_route(route)}
    for ident, adapter in sorted(A.package_adapters().items()):
        contract = A.tool_review_contract(adapter)
        if contract is None:
            continue
        found = any(shutil.which(binary) or _tool_beside_interpreter(binary) for binary in A.adapter_binaries(adapter))
        features.append((ident, "absent" if not found else "configured" if ident in configured else "installed",
                         str(contract.get("install") or f"install {ident}")))
    return features


def _measurement_lines(cfg):
    """How ao measures, what could filter the numbers an agent reads (#51), and what each filter does to them (#52)."""
    filters = A.measurement_filters(cfg["root"])
    state = (f"{C['yellow']}{len(filters)} possible filter(s){C['reset']}" if filters
             else f"{C['green']}unfiltered{C['reset']}")
    lines = [f"{'measurement':<16}{state}  {C['dim']}ao measures with {A.git_binary()}, "
             f"the candidate without a shell{C['reset']}"]
    lines += [f"{'':<16}{C['dim']}{text}{C['reset']}" for text in filters]
    try:
        probed = A.probe_filters(cfg["root"])
    except Exception as exc:
        return lines + [f"{'filter probe':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    return lines + [f"{'filter probe':<16}{C['green'] if result['verdict'] == 'in-force' else C['yellow']}"
                    f"{A.filter_probe_text(result)}{C['reset']}" for result in probed]


def _review_evidence_lines(cfg):
    """The reviews a grant rests on that git does not hold: one disk from gone (#38)."""
    try:
        risky = A.grant_artefacts_at_risk(cfg["root"], cfg)
    except Exception as exc:
        return [f"{'review evidence':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    if not risky:
        return [f"{'review evidence':<16}{C['green']}every review a grant rests on is in git{C['reset']}"]
    untracked = sum(1 for _, state in risky if state == "untracked")
    lines = [f"{'review evidence':<16}{C['yellow']}{untracked} untracked, {len(risky) - untracked} missing of "
             f"the reviews grants rest on{C['reset']}  {C['dim']}commit them; ao prune never moves them{C['reset']}"]
    lines += [f"{'':<16}{C['dim']}{state:<9} {name}{C['reset']}" for name, state in risky[:5]]
    if len(risky) > 5:
        lines.append(f"{'':<16}{C['dim']}and {len(risky) - 5} more{C['reset']}")
    return lines


def _session_lines(cfg):
    """`ao doctor`'s line for each role's session: the id ao resolved and how, or why there is none (SESSION-IDENTITY).

    It printed the implementer's `session` as written, and `auto` read as a session's id.
    """
    lines = []
    for role in A.SESSION_ROLES:
        state = A.session_state(cfg, role)
        if state is None:
            if role == "implementer":
                lines.append(f"{role:<16}{C['red']}none found{C['reset']}")
            continue
        adapter = A.block_adapter(cfg.get(role)) or "?"
        if state["session"]:
            tone = C["green"] if state["trusted"] else C["yellow"]
            head = f"{role:<16}{adapter} / {state['session'][:36]}  {tone}{state['how']}{C['reset']}"
        else:
            tone = C["red"] if state["how"] == "ambiguous" else C["yellow"]
            head = f"{role:<16}{adapter} / auto  {tone}{state['how']}{C['reset']}"
        lines.append(head + (f"  {C['dim']}{state['why']}{C['reset']}" if state.get("why") else ""))
    return lines


def _architect_absence_lines(cfg):
    """How long the architect has been away and how many questions wait for it (#84)."""
    try:
        away = A.architect_absence(cfg["root"], cfg)
    except Exception as exc:
        return [f"{'architect away':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    seen = (f"last seen {_elapsed(time.time() - away['seen_at'])} ago" if away["seen_at"]
            else "not seen in this project's records")
    if not away["waiting"]:
        return [f"{'architect away':<16}{C['dim']}{seen} · no question waiting{C['reset']}"]
    oldest = _elapsed(time.time() - float(away["oldest_at"] or time.time()))
    return [f"{'architect away':<16}{C['yellow']}{seen} · {len(away['waiting'])} question(s) waiting, the oldest "
            f"{away['waiting'][0]} for {oldest}{C['reset']}  {C['dim']}answered in one pass: ao decisions{C['reset']}"]


def _human_bytes(count):
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024


def cmd_worktrees(cfg, args):
    """Every worktree, what keeps it and whether it may go; `prune` retires those that may (#42).

    Seven worktrees stood on this machine, one per slice, none removed when its
    branch landed, each a full checkout with its own state and stale reviews.
    Prune is a dry run until --yes.
    """
    root = cfg["root"]
    try:
        facts = A.worktree_facts(root, cfg, sizes=True)
    except RuntimeError as exc:
        print(f"{C['red']}{exc}{C['reset']}")
        return 2
    pruning = getattr(args, "action", None) == "prune"
    apply = pruning and getattr(args, "yes", False)
    if pruning and not apply:
        print(f"{C['yellow']}dry run — add --yes to apply{C['reset']}")
    for fact in facts:
        size = _human_bytes(fact["bytes"]) if fact["bytes"] is not None \
            else "gone" if not os.path.isdir(fact["path"]) else "—"
        state = (f"{C['green']}may go{C['reset']} ({fact['why']})" if fact["may_go"]
                 else f"{C['dim']}keep{C['reset']} ({'; '.join(fact['keep']) or 'not merged, slice not rejected'})")
        print(f"  {fact['path']}  {C['dim']}{fact['branch'] or 'detached'} · {size}{C['reset']}  {state}")
        if pruning and fact["may_go"]:
            try:
                for step in A.prune_worktree(root, fact, apply=apply):
                    print(f"      {'done' if apply else 'would'}: {step}")
            except (OSError, RuntimeError) as exc:
                print(f"      {C['red']}stopped{C['reset']}: {exc}")
                return 1
    return 0


def _worktree_lines(cfg):
    """Worktrees whose branch is merged or gone, with their size on disk (#42)."""
    try:
        going = [fact for fact in A.worktree_facts(cfg["root"], cfg, sizes=True) if fact["may_go"]]
    except Exception as exc:
        return [f"{'worktrees':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    if not going:
        return []
    total = sum(fact["bytes"] or 0 for fact in going)
    lines = [f"{'worktrees':<16}{C['yellow']}{len(going)} may go ({_human_bytes(total)}){C['reset']}  "
             f"{C['dim']}ao worktrees prune{C['reset']}"]
    lines += [f"{'':<16}{C['dim']}{fact['path']}  {fact['why']}  "
              f"{_human_bytes(fact['bytes']) if fact['bytes'] is not None else 'gone'}{C['reset']}" for fact in going[:5]]
    return lines


PROVE_BOUNDARY = ("ao prove: a throwaway candidate that adds one line to ao-prove.txt and nothing else. "
                  "Approve it if the diff is exactly that.")


def _prove_throwaway(cfg):
    """A one-line slice through verify, review and commit-ok in a temporary worktree; nothing lands (#85).

    Returns (ok, what failed or None, what would fix it or None). The worktree's
    coordination state and review go to ~/.ao/archive/<project>/prove-<stamp>/.
    """
    import io
    import shutil
    import tempfile
    root = cfg["root"]
    scratch = tempfile.mkdtemp(prefix="ao-prove-")
    tree = os.path.join(scratch, "tree")
    links, unlinked, heard = [], [], io.StringIO()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        added = subprocess.run([A.git_binary(), "worktree", "add", "--detach", "--quiet", tree, "HEAD"], cwd=root,
                               capture_output=True)
        if added.returncode:
            return False, "a temporary worktree could not be made: " + added.stderr.decode(UTF8, "replace").strip(), \
                "a repository with at least one commit"
        # A step that raises is a check that failed, said with what would fix it: it ended `ao prove` in a
        # traceback (PROVE-2).
        try:
            return _prove_steps(cfg, root, tree, stamp, links, unlinked, heard)
        except Exception as exc:
            return False, f"the throwaway slice stopped: {type(exc).__name__}: {' '.join(str(exc).split())}", \
                "run the step it names by hand in this checkout, or `ao doctor`"
    finally:
        for link in links:
            try:
                os.unlink(link)
            except OSError:
                pass
        archive = os.path.join(A.HOME, ".ao", "archive", A.project_key(root), f"prove-{stamp}")
        for part in (".ao", cfg.get("reviews", "semantic-review")):
            if os.path.isdir(os.path.join(tree, part)):
                shutil.copytree(os.path.join(tree, part), os.path.join(archive, part), symlinks=True,
                                dirs_exist_ok=True)
        subprocess.run([A.git_binary(), "worktree", "remove", "--force", tree], cwd=root, capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)
        subprocess.run([A.git_binary(), "worktree", "prune"], cwd=root, capture_output=True)


def _prove_steps(cfg, root, tree, stamp, links, unlinked, heard):
    """The throwaway slice's own steps, in a worktree made for them: (ok, what failed, what would fix it)."""
    import contextlib
    import shutil
    from types import SimpleNamespace
    os.makedirs(os.path.join(tree, ".ao"), exist_ok=True)
    for name in ("config.json", "gates.json"):
        if os.path.exists(os.path.join(root, ".ao", name)):
            shutil.copy2(os.path.join(root, ".ao", name), os.path.join(tree, ".ao", name))
    with open(os.path.join(tree, ".ao", "board.md"), "w", encoding=UTF8) as fh:
        fh.write(f"# Board\n\n## running\n- [AO-PROVE] a throwaway one-line change · acceptance: {PROVE_BOUNDARY}\n"
                 "\n## blocked\n\n## queued\n\n## verified\n\n## done\n")
    for name in S.get(cfg, "merge.link_paths"):
        source, target = os.path.join(root, name), os.path.join(tree, name)
        if os.path.exists(source) and not os.path.lexists(target):
            try:
                os.symlink(source, target)
                links.append(target)
            except OSError as exc:
                # Named when a gate fails: a gate that needed it failed for this, not for the change (PROVE-2).
                unlinked.append(f"{name} ({' '.join(str(exc).split())})")
    with open(os.path.join(tree, "ao-prove.txt"), "a", encoding=UTF8) as fh:
        fh.write(f"ao prove {stamp}\n")
    subprocess.run([A.git_binary(), "add", "ao-prove.txt"], cwd=tree, check=True, capture_output=True)
    tree_cfg = A.load_config(tree)
    with contextlib.redirect_stdout(heard):
        verified = cmd_verify(tree_cfg, SimpleNamespace(profile="quick", wait=900))
    if verified != 0:
        return False, "the quick gates did not pass a one-line change" \
            + (f"; merge.link_paths could not link {', '.join(unlinked)}" if unlinked else ""), \
            "run `ao verify -p quick` and read the failing gate:\n" + _last_lines(heard.getvalue())
    with contextlib.redirect_stdout(heard):
        reviewed = cmd_review(tree_cfg, SimpleNamespace(action=None, rid=None, any=False, run=None, boundary=None,
                                                        paths=None, commits=None, timeout=None))
    if reviewed != 0:
        return False, "the reviewer did not approve a trivial candidate", \
            "read the review it wrote (archived below); exit 3 means no reviewer could review:\n" \
            + _last_lines(heard.getvalue())
    with contextlib.redirect_stdout(heard):
        granted = cmd_commit_ok(tree_cfg, SimpleNamespace(verify=False, profile=None, review=None))
    if granted != 0:
        return False, "ao commit-ok refused the approved candidate", _last_lines(heard.getvalue())
    return True, None, None


def _last_lines(text, count=6):
    lines = [A.re.sub(r"\x1b\[[0-9;]*m", "", line) for line in text.strip().splitlines() if line.strip()]
    return "\n".join("      " + line for line in lines[-count:])


def cmd_prove(cfg, args):
    """Run the guarantees instead of describing them, and say what would fix each one that fails (#85).

    The hook must refuse a synthetic candidate, the reviewer must answer and be
    another actor than the implementer, and a throwaway slice must go through
    verify, review and commit-ok in a temporary worktree. Nothing is committed.
    """
    root = cfg["root"]
    results = []
    probe = _hook_execution_probe(_ao_hook_inventory(root))
    enrollment = _project_enrollment(root)
    # A hook whose /bin/sh finds no ao is not fixed by installing it again (SAFE-REMOVE), and a project no
    # tracked marker covers refuses `ao hooks install` until it is adopted; a current hook there failed its
    # proof with the adoption's own text, which is said once (INIT-ADOPTION).
    if HOOK_AO_NOT_FOUND in probe["detail"]:
        hook_fix = _ao_link_fix()
    elif enrollment["state"] == "legacy":
        hook_fix = None if probe["detail"] == enrollment["detail"] else enrollment["detail"]
    else:
        hook_fix = "ao hooks install"
    results.append(("hook refuses an unauthorised commit", probe["installed"],
                    None if probe["installed"]
                    else _hook_probe_text(probe) + (f" — {hook_fix}" if hook_fix else "")))
    reviewer = _reviewer_probe(cfg)
    reviewer_ok = reviewer["ok"] and reviewer["configured"]
    # What would fix it names the review tiers; a reviewer of a weaker tier is proven, and labeled (REVIEW-TIERS).
    from . import tiers as T
    if reviewer_ok:
        note = f"tier: {T.label(reviewer.get('tier'))}" if T.weaker(reviewer.get("tier")) else None
    elif not reviewer["configured"]:
        note = f"not configured — {T.WAYS_FORWARD} (docs/roles.md)"
    elif reviewer.get("kind") == "configuration-error":
        note = f"{_reviewer_probe_text(reviewer)} (docs/roles.md)"
    else:
        note = f"{_reviewer_probe_text(reviewer)} — name a reviewer that answers in .ao/config.json (docs/roles.md)"
    results.append(("reviewer answers and is another actor", reviewer_ok, note))
    if getattr(args, "no_review", False):
        results.append(("a throwaway slice lands end to end", False, "skipped with --no-review: not proven"))
    elif not reviewer_ok:
        results.append(("a throwaway slice lands end to end", False, "not tried: no reviewer can review"))
    else:
        ok, failed, fix = _prove_throwaway(cfg)
        results.append(("a throwaway slice lands end to end", ok, None if ok else f"{failed} — {fix}"))
    for claim, ok, fix in results:
        print(f"  {C['green'] + 'proven' if ok else C['red'] + 'NOT PROVEN'}{C['reset']}  {claim}")
        if fix:
            print(f"      {fix}")
    proven = all(ok for _, ok, _ in results)
    print(f"\n{C['b']}{'PROVEN' if proven else 'NOT PROVEN'}{C['reset']}  "
          f"{C['dim']}nothing was committed; the throwaway worktree is gone{C['reset']}")
    return 0 if proven else 1


def _init_then_prove(cfg, args):
    code = cmd_init(cfg, args)
    if code or not getattr(args, "prove", False):
        return code
    print(f"\n{C['b']}proving the guarantees{C['reset']}")
    return cmd_prove(A.load_config(cfg["root"]), args)


def _doctor_consistency(cfg, repair=False):
    """`ao doctor --consistency [--repair]`: the four stores checked against each other (#47)."""
    root = cfg["root"]
    findings = A.consistency_findings(root, cfg)
    if repair:
        for finding in A.repair_consistency(root, findings):
            print(f"{C['green']}repaired{C['reset']}  {finding['text']}  {C['dim']}recorded in "
                  f".ao/ledger/repairs.jsonl{C['reset']}")
        findings = A.consistency_findings(root, cfg)
    if not findings:
        print(f"{C['green']}consistent{C['reset']}  board, ledgers, review artefacts and git agree")
        return 0
    for finding in findings:
        mark = f"{C['yellow']}repairable{C['reset']}" if finding["repair"] else f"{C['red']}disagrees{C['reset']}"
        print(f"{mark}  {finding['kind']}: {finding['text']}")
    if any(finding["repair"] for finding in findings):
        print(f"{C['dim']}ao doctor --consistency --repair fixes the repairable ones and records what it did{C['reset']}")
    return 1


def _store_bound_lines(cfg):
    """Observation stores past their bound (#50): the watchdog holds them each cycle, so one here means it is not."""
    from .watchdog import STATE_DIR
    over = A.stores_over_bound(cfg["root"], cfg, STATE_DIR)
    if not over:
        return []
    return [f"{'stores':<16}{C['yellow']}{len(over)} over their bound{C['reset']}  "
            f"{C['dim']}{', '.join(f'{os.path.basename(p)} {size // 1024}KB/{limit // 1024}KB' for p, size, limit in over)}"
            f" — the watchdog trims them each cycle; is it running?{C['reset']}"]


def cmd_backup(cfg, args):
    """Write the project's governance to the destination it names: a directory, `ref`, or `remote:<name>` (#46)."""
    destination = getattr(args, "to", None) or (cfg.get("backup") or {}).get("to")
    if not destination:
        print(f"{C['yellow']}no destination{C['reset']}: `ao backup --to <directory|ref|remote:name>`, or name one as "
              "backup.to in .ao/config.json")
        return 2
    try:
        manifest = A.write_backup(cfg["root"], cfg, destination)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"{C['red']}not backed up{C['reset']}: {exc}")
        return 1
    print(f"{C['green']}backed up{C['reset']} {len(manifest['files'])} governance file(s) → {manifest['where']}")
    try:
        A.authority_rows(cfg["root"])
    except Exception as exc:
        # Which artefacts a grant rests on is the chain's word; without it every one was kept (GOVERNANCE-BACKUP-2).
        print(f"  {C['yellow']}the authority chain does not validate{C['reset']} ({exc}): every review artefact "
              "was backed up, not only those a grant names")
    return 0


def cmd_restore(cfg, args):
    """Reconstruct the control plane from a backup directory; say what could not be verified (#46)."""
    root = cfg["root"]
    try:
        restored, unverified = A.restore_backup(root, args.source)
    except (OSError, ValueError, KeyError) as exc:
        print(f"{C['red']}not restored{C['reset']}: {exc}")
        return 2
    print(f"restored {len(restored)} file(s)")
    for line in unverified:
        print(f"  {C['red']}not verified{C['reset']}  {line}")
    # A backup that put nothing back, or no config, is no control plane: it "validated" and exited 0, and a
    # script acted on that (GOVERNANCE-BACKUP-2).
    if not restored or not os.path.isfile(os.path.join(root, ".ao", "config.json")):
        print(f"{C['red']}the backup restored no control plane{C['reset']}: "
              + ("no file was restored" if not restored else ".ao/config.json is not among what was restored"))
        return 1
    try:
        A.authority_rows(root)
        A.board(root)
        print(f"{C['green']}the authority chain and the board validate{C['reset']}")
    except Exception as exc:
        print(f"{C['red']}restored state does not validate{C['reset']}: {exc}")
        return 1
    return 1 if unverified else 0


def _backup_lines(cfg):
    """How old the newest backup is, and whether governance exists with none (#46)."""
    age = A.backup_age(cfg["root"])
    if age is None:
        return [f"{'backup':<16}{C['yellow']}none{C['reset']}  {C['dim']}the control plane is on this disk only — "
                f"ao backup --to <directory|ref|remote:name>{C['reset']}"]
    seconds, where = age
    tone = C["green"] if seconds < 7 * 86400 else C["yellow"]
    return [f"{'backup':<16}{tone}{_elapsed(seconds)} ago{C['reset']}  {C['dim']}{where}{C['reset']}"]


def cmd_doctor(cfg, args):
    if getattr(args, "consistency", False):
        return _doctor_consistency(cfg, repair=getattr(args, "repair", False))
    if getattr(args, "check", False):
        # Scheduled checks return through the existing static helper here;
        # only the manual path below invokes the reviewer nonce probe.
        if getattr(args, "notify", False):
            return _doctor_check(cfg, page=True)
        return _doctor_check(cfg)
    root, impl, adapter = _ctx(cfg)
    ok = lambda b: f"{C['green']}ok{C['reset']}" if b else f"{C['red']}missing{C['reset']}"

    # Run outside a project, every check is "missing" and none of it is a fault.
    # Say that instead of printing a wall of red.
    if not impl and not os.path.isdir(os.path.join(root, cfg["mailbox"])):
        print(f"{C['yellow']}No project here.{C['reset']} {C['dim']}{root}{C['reset']}\n")
        ws = A.all_workspaces()[:5]
        if ws:
            print("Point at one of these:")
            for r in ws:
                mins = int((time.time() - r["mtime"]) / 60)
                age = f"{mins}m ago" if mins < 90 else f"{mins // 60}h ago"
                print(f"  {C['b']}ao -C {r['path']} doctor{C['reset']}  {C['dim']}{age}{C['reset']}")
        else:
            print(f"{C['dim']}No local agent sessions found. See docs/adapters.md.{C['reset']}")
        return
    print(f"root            {root}")
    # Configuration is optional: discovery covers the common case, so its absence
    # is a fact, not a fault. Red is reserved for things that need fixing.
    has_cfg = os.path.exists(os.path.join(root, ".ao", "config.json"))
    print(f"config          " + (f"{C['green']}.ao/config.json{C['reset']}" if has_cfg
                                 else f"{C['dim']}none — using auto-discovery{C['reset']}"))
    for line in _session_lines(cfg):
        print(line)
    msgs, _ = A.session_paths(cfg)
    print(f"transcript      {ok(bool(msgs and os.path.exists(msgs)))}")
    print(f"mailbox         {ok(os.path.isdir(os.path.join(root, cfg['mailbox'])))}")
    print(f"reviews         {ok(os.path.isdir(os.path.join(root, cfg['reviews'])))}")
    reviewer_probe = _reviewer_probe(cfg)
    probe_tone = C["green"] if reviewer_probe["ok"] else C["red"]
    print(
        f"reviewer probe  {probe_tone}{_reviewer_probe_text(reviewer_probe)}"
        f"{C['reset']}"
    )
    # The tier the next review would stand in, and whether any does (REVIEW-TIERS).
    from . import tiers as T
    tier_text, tier_problems = _active_review_tier(cfg)
    strongest = tier_text.startswith(T.LABELS[T.INDEPENDENT]) and not tier_problems
    print(f"review tier     {C['green'] if strongest else C['yellow']}{tier_text}{C['reset']}")
    for _, text in tier_problems:
        print(f"                {C['yellow']}{text}{C['reset']}")
    print(f"review budget   {C['dim']}{_review_budget_text(_review_timeout(cfg))}{C['reset']}")
    guard = _implementer_commit_guard(cfg)
    if guard:
        print(f"commit guard    {C['dim']}{guard}{C['reset']}")
    try:
        waiver_lines = A.open_waiver_report(root)
    except Exception as exc:
        waiver_lines = [f"{C['red']}waiver ledger is unreadable: {exc}{C['reset']}"]
    print(f"waivers         {len(waiver_lines) if waiver_lines else 'none open'}"
          + (" open" if waiver_lines else ""))
    for line in waiver_lines:
        print(f"                {C['dim']}{line}{C['reset']}")
    for line in _review_evidence_lines(cfg):
        print(line)
    for line in _architect_absence_lines(cfg):
        print(line)
    for line in _worktree_lines(cfg):
        print(line)
    for line in _store_bound_lines(cfg):
        print(line)
    for line in _backup_lines(cfg):
        print(line)
    print(f"quota source    {'keyflip' if A.runnable_binary('keyflip') else '—'}")
    # Optional capabilities announce themselves; the core never needs them (#82).
    for name, state, hint in _optional_features(cfg):
        print(f"optional        {name:<9} {state}" + (f"  {C['dim']}{hint}{C['reset']}" if state == "absent" else ""))
    key = A.project_key(root).lower()
    scheduler = _scheduler()
    if scheduler == "schtasks":
        # Windows schedules the watchdog with Task Scheduler, not launchd (#71).
        wd = subprocess.run(["schtasks", "/Query", "/TN", _windows_task("watchdog", key)], capture_output=True,
                            text=True, encoding=UTF8, errors="replace").returncode == 0
    elif scheduler == "systemd":
        timer = _systemd_unit("watchdog", key) + ".timer"
        wd = _systemd_states(timer)[timer] in SYSTEMD_UP
    else:
        wd = _launchd_listed(_launchd_label("watchdog", key))
    print(f"watchdog        {C['green']}running{C['reset']}" if wd else
          f"watchdog        {C['dim']}not installed — ao watchdog install{C['reset']}")
    err = A.last_nudge_error(root)
    if err:
        mins = int((time.time() - err.get("at", 0)) / 60)
        print(f"last restart    {C['red']}failed {mins}m ago (exit {err.get('code')}){C['reset']}")
        print(f"                {C['dim']}{err.get('tail','')[:110]}{C['reset']}")
    else:
        print(f"last restart    {C['dim']}no failures recorded{C['reset']}")
    hook_inventory = _ao_hook_inventory(root)
    if hook_inventory["error"]:
        detail = f"resolver failure: {hook_inventory['error']}"
        print(f"{'commit hook':<16}{C['yellow']}{detail}{C['reset']}  ao hooks status")
        print(f"{'push hook':<16}{C['yellow']}{detail}{C['reset']}  AO push-window hook unavailable")
    else:
        active = _active_hook_targets(hook_inventory)
        legacy_project = _project_enrollment(root)["state"] == "legacy"
        for role, label in (("pre-commit", "commit hook"), ("pre-push", "push hook")):
            target = active[role]
            base = _state_base(target["static_state"])
            current = base in ("current-local", "current-scoped")
            tone = C["green"] if current else C["yellow"]
            notes = []
            if role == "pre-commit" and not current:
                # `ao hooks install` refuses a project no tracked marker covers; one command adopts it (INIT-ADOPTION).
                notes.append(
                    f"no {PROJECT_MARKER} tracked yet — {PROJECT_ADOPT_COMMAND}"
                    if legacy_project else "ao hooks install"
                )
            if role == "pre-push" and not current:
                notes.append("AO push-window hook unavailable")
            suffix = "  " + "; ".join(notes) if notes else ""
            print(
                f"{label:<16}{tone}{target['static_state']} / "
                f"{target['track_state']}{C['reset']}{suffix}"
            )
        for target in hook_inventory["targets"]:
            if target["active"] or target["role"] != "pre-commit" \
                    or target["reachability"] != "potentially-effective" \
                    or _state_base(target["static_state"]) not in (
                        "current-local", "current-scoped", "legacy", "ambiguous-ao"
                    ):
                continue
            print(
                f"{'commit misplaced':<16}{C['yellow']}{target['static_state']} / "
                f"{target['track_state']} / {target['directory_class']} — "
                f"{target['path']}{C['reset']}  ao hooks uninstall, then ao hooks install"
            )
    hook_proof = _hook_execution_probe(hook_inventory)
    proof_tone = C["green"] if hook_proof["installed"] else C["yellow"]
    print(f"{'commit proof':<16}{proof_tone}{_hook_probe_text(hook_proof)}{C['reset']}")
    reach = _hook_ao_reach(hook_inventory)
    if reach:
        print(f"{'ao for hooks':<16}{C['yellow']}{reach[0]}{C['reset']}")
        print(f"{'':<16}{C['dim']}fix: {reach[1]}{C['reset']}")
    print(f"{'checkout':<16}{_checkout_position(A.git_state(root))}")
    for line in _measurement_lines(cfg):
        print(line)
    # Can the *agent* run `ao`? A shell alias is invisible to a non-interactive
    # process, so steering that says "run your gates through ao lock" is an
    # instruction the agent cannot follow — and a disciplined agent then parks the
    # slice rather than working around it. That cost this project three parked
    # items and half a day. Check the child's PATH, not this shell's.
    from .watchdog import child_path
    reachable = shutil.which("ao", path=child_path())
    if reachable:
        # The architect is woken through a binary the watchdog resolves by version,
        # not by PATH order; say which one, and whether the last wake died on it.
        arch = cfg.get("architect") or {}
        if arch.get("argv"):
            from .watchdog import child_path, wake_error, STATE_DIR
            rb, rv = A.resolve_binary(arch["argv"][0], path=child_path())
            others = [c for c in A.binary_candidates(arch["argv"][0], child_path()) if c != rb]
            print(f"architect bin   {C['green'] if rb else C['red']}{rb or 'not found'}{C['reset']} {C['dim']}{rv}{C['reset']}"
                  + (f"  {C['dim']}({len(others)} older copy: {', '.join(others)}){C['reset']}" if others else ""))
            key = A.project_key(root)
            we = wake_error(os.path.join(STATE_DIR, A.project_file_name("escalate-log", key)))
            if we:
                text, used, when = we["text"], we["binary"], we["when"]
                print(f"last wake       {C['red']}failed{C['reset']} {when} [{used or '?'}]: {text[:90]}")
                if not used:
                    print(f"                {C['dim']}binary not recorded (older log); the next wake uses the one above{C['reset']}")
                elif used != f"{rb} {rv}":
                    print(f"                {C['dim']}a different binary resolves now; the next wake will use it{C['reset']}")
                else:
                    update = next((adapter["detect"]["update"] for adapter in A.package_adapters().values()
                                   if (adapter.get("detect") or {}).get("update")
                                   and os.path.basename(arch["argv"][0]) in A.adapter_binaries(adapter)), None)
                    how = f" ({' '.join(update)})" if update else ""
                    print(f"                {C['yellow']}same binary — update it{how} or remove the stale copy{C['reset']}")
        # Liveness and channels. A watchdog nobody can prove is alive, and an orange
        # alarm with no channel beyond the desktop, are both silent failures.
        hb = A.heartbeat_age(root)
        if hb is None:
            print(f"last tick       {C['yellow']}never{C['reset']} {C['dim']}(no heartbeat file yet){C['reset']}")
        else:
            tone = C['green'] if hb < 360 else C['red']
            print(f"last tick       {tone}{hb // 60}m {hb % 60}s ago{C['reset']}"
                  + (f"  {C['red']}watchdog is not running its cycles{C['reset']}" if hb >= 360 else ""))
        from . import telegram as _tg, email as _em
        tg_ok, em_ok = bool(_tg.config()), bool(_em.config())
        print(f"channels        desktop {C['green']}on{C['reset']} · telegram "
              f"{C['green'] if tg_ok else C['yellow']}{'on' if tg_ok else 'off'}{C['reset']} · e-mail "
              f"{C['green'] if em_ok else C['yellow']}{'on' if em_ok else 'off'}{C['reset']}"
              + ("" if (tg_ok or em_ok) else f"  {C['yellow']}orange alarms reach nothing but the desktop — ao email setup{C['reset']}"))
        try:
            from .watchdog import load_state as _ls, reset_when as _reset_when
            _st = _ls(root)
            if _st.get("arch_quota_until", 0) > time.time():
                # The date too when the end is a day or more away: a week's end is not tonight (ARCHITECT-WAKE-QUOTA).
                print(f"architect       {C['yellow']}at quota{C['reset']} until {_reset_when(_st['arch_quota_until'])}")
        except Exception:
            pass
        alive = A.active_alarms(A.project_key(root))
        if alive:
            worst = max(alive, key=lambda e: {"yellow": 0, "orange": 1, "red": 2}.get(e.get("ring"), 0))
            print(f"alarms          {C['red'] if worst['ring'] == 'red' else C['yellow']}{len(alive)} live, worst {worst['ring']}{C['reset']}  {C['dim']}ao alarms{C['reset']}")
        # Things that fail silently unless someone asks: the transcript format (a CLI
        # update changes it and every status goes blind), the credits running out
        # before the reset, the external ping, the push hook, the switches.
        try:
            msgs_p, _ = A.session_paths(cfg)
            if msgs_p and os.path.exists(msgs_p):
                n_recs = len(A.read_tail(msgs_p, 2_000_000))
                age_m = int((time.time() - os.path.getmtime(msgs_p)) / 60)
                bad = n_recs == 0 and age_m < 60
                print(f"transcript      {C['red'] if bad else C['green']}{n_recs} records parsed{C['reset']}, last write {age_m}m ago"
                      + (f"  {C['red']}fresh file, nothing parsed — the CLI's format changed; update the adapter{C['reset']}" if bad else ""))
        except Exception:
            pass
        # The implementer's own account only, or that its adapter declares none (ACCOUNT-READERS).
        for line in _doctor_credit_lines(cfg):
            print(line)
        print(f"ping            {C['green'] + 'configured' + C['reset'] if A.ping_url(root) else C['yellow'] + 'off' + C['reset'] + '  ao pings setup'}")
        from . import features as _F
        print(f"features        {sum(_F.switches(cfg).values())}/{len(_F.ORDER)} on  {C['dim']}ao cost --features for what "
              f"each spent{C['reset']}")
        print(f"ao for agents   {C['green']}{reachable}{C['reset']}")
    else:
        print(f"ao for agents   {C['red']}not on a spawned agent's PATH{C['reset']}")
        print(f"                {C['dim']}a shell alias does not count — {_ao_link_fix()}{C['reset']}")

    from . import skillkit as _skillkit
    for steering in _skillkit.steering_dirs(root):
        steer = os.path.join(root, *steering.split("/"))
        if not os.path.isdir(steer) or reachable:
            continue
        refs = [f for f in os.listdir(steer)
                if f.endswith(".md") and "ao " in open(os.path.join(steer, f),
                                                       errors="replace", encoding=UTF8).read()]
        if refs:
            print(f"                {C['yellow']}steering references it: "
                  f"{', '.join(refs)}{C['reset']}")

    # Two copies of `ao` on one machine is an ambiguity that bites silently: a
    # shell alias to a git checkout and a package install answer to the same
    # name, drift apart after the next commit, and which one runs depends on
    # how it was invoked. The second pilot's agent caught it on its first day.
    cands = []
    for cand in (shutil.which("ao"), os.path.join(A.HOME, ".local", "bin", "ao"),
                 os.path.join(A.REPO, "bin", "ao")):
        if cand and os.path.exists(cand):
            real = os.path.realpath(cand)
            if real not in [os.path.realpath(c) for c in cands]:
                cands.append(cand)
    if len(cands) > 1:
        print(f"ao binaries     {C['yellow']}{len(cands)} distinct{C['reset']} — "
              f"{C['dim']}which one runs depends on how it is invoked{C['reset']}")
        for c in cands:
            print(f"                {C['dim']}{c} → {os.path.realpath(c)}{C['reset']}")
        print(f"                {C['dim']}keep one: drop the shell alias, or "
              f"uv tool uninstall ao-orchestrator{C['reset']}")
    return 0 if reviewer_probe["ok"] else 1
