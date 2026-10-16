"""Maintenance: remove, prune, notices, the watchdog command, projects, adapters, worktrees, prove,
demo, backup, doctor.

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


def _project_home_files(key, root=None):
    """This project's files in ~/.ao that exist, named from PROJECT_FILES (SAFE-REMOVE), and the record that its
    repository is enrolled (LEADS-GATE-HOOK).

    One path per file: a case-insensitive disk answers to a lower-cased name and to the
    name as it is, and both would otherwise be listed and removed as two files.
    """
    seen, found = set(), []
    names = [os.path.join(A.HOME, ".ao", name) for name in A.project_file_names(key)]
    if root is not None:
        # The record is the repository's, whichever of its worktrees the project is removed from (LEADS-GATE-HOOK).
        record = A.enrollment_record(root)
        if record and record.get("repository"):
            names.append(A.enrollment_record_path(root)[0])
    for path in names:
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
# What the comment `ao init` writes above the lines it adds to .gitignore says (cmd_init), and the comment itself.
AO_GITIGNORE_MARK = "agent-orchestrator: mail is transient"
AO_GITIGNORE_COMMENT = "# agent-orchestrator: mail is transient; the ledger and reviews are not"
# Why a comment in ao's words stays in .gitignore with the lines under it, and what the person does.
GITIGNORE_UNCLAIMED = ("ao cannot tell it wrote them (an older ao named no lines, and a block changed since is not "
                       "what it wrote); delete them by hand if ao added them")


def _gitignore_block(lines):
    """The comment `ao init` writes above the lines it adds to .gitignore, naming them its own: how many follow it,
    and a digest of exactly those lines (LEADS-INSTALL).

    A comment in ao's words was all that marked them, and a person's own block under such a comment - copied from
    another project, or written before init - was taken with ao's. The count and the digest are what ao wrote and
    nobody writes by chance: `ao remove` takes a block only when the lines under its comment are those.
    """
    import hashlib
    digest = hashlib.sha256("\n".join(lines).encode(UTF8)).hexdigest()[:16]
    return f"{AO_GITIGNORE_COMMENT} (ao: {len(lines)} line{'' if len(lines) == 1 else 's'} below, {digest})"


def _gitignore_without_ao(root):
    """(.gitignore's path, its lines without the ones ao added, how many of those it had, and where a comment in
    ao's words stands over lines ao cannot tell it wrote: [(its line now, its line once ao's lines are out)]).

    ao's lines are the blocks `ao init` wrote: its comment, naming the lines under it by their count and digest
    (_gitignore_block), and exactly those lines. Every line holding one of ao's patterns went, wherever it stood,
    and then the run of ao's patterns under any comment in ao's words, so a person's own block under such a
    comment went too (LEADS-INSTALL). A block an older ao wrote names no lines, and one changed since is not
    what ao wrote: each stays, and is named, for the person to delete if it is ao's.
    """
    import re
    from . import storage
    path = os.path.join(root, ".gitignore")
    raw = storage.read_in_tree(root, ".gitignore")
    if raw is None:
        return path, [], 0, []
    # Match init's text decoding, but never read through a link or wait on a FIFO. The caller names an
    # unreadable file at both planning and removal, including a link swapped in between them (LEADS-INSTALL).
    lines = raw.decode(UTF8, "replace").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    keep, unclaimed, index = [], [], 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("#") and AO_GITIGNORE_MARK in line:
            named = re.search(r" \(ao: (\d{1,6}) lines? below, [0-9a-f]{16}\)$", line)
            count = int(named.group(1)) if named else 0
            block = lines[index + 1:index + 1 + count]
            if named and len(block) == count and line == _gitignore_block(block):
                index += 1 + count
                continue
            unclaimed.append((index + 1, len(keep) + 1))
        keep.append(line)
        index += 1
    return path, keep, len(lines) - len(keep), unclaimed


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
    """(what schtasks printed, its exit status), asked without a shell; ("", None) when it cannot be run.

    Windows' own schtasks, from its system directory: by its bare name Windows looked for it in the
    current directory first, and a schtasks.exe an agent left in the tree would have run as the
    person, and been asked to schedule ao's jobs (WINDOWS-NO-CWD-EXEC). What it prints is in the
    OEM code page, read as such (console_text, WINDOWS-SAY-WHY).
    """
    schtasks = A.program_path("schtasks")
    if schtasks is None:
        return "", None
    try:
        done = subprocess.run([schtasks, *args], capture_output=True)
    except OSError:
        return "", None
    return A.console_text((done.stdout or b"") + (done.stderr or b"")).strip(), done.returncode


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
# And of one whose process may still run: a service being stopped is `deactivating` until it has ended.
SYSTEMD_NOT_GONE = SYSTEMD_UP + ("deactivating",)
# And of one that does not run and will not start of itself, a unit it does not know included. Any other word, or
# no word for a unit, is no answer, and a job it is said of counts as there (LEADS-INSTALL).
SYSTEMD_DOWN = ("inactive", "failed")
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
    remove. With it, the manager is asked to disable and stop them and then whether each is gone:
    the timer, and the service, whose cycle may be running as it is asked. Only the timer was
    asked after, so a service a stop did not end ran on against a project its removal said was
    free of it (LEADS-INSTALL). A manager this shell cannot reach may still run them: they are
    then left as they are, files included, so that a session that reaches it still finds them to
    take off - and so they are when no file of theirs is left, since the manager keeps running a
    unit whose files went; that was read as nothing to take off (LEADS-INSTALL). Each is stopped by
    name as well as disabled: a disable finds no unit file to act on once the files went, and its
    `--now` then stops nothing.
    """
    unit = timer[:-len(".timer")]
    service = unit + ".service"
    files = _systemd_files(unit)
    booted = os.path.isdir(SYSTEMD_RUNTIME)
    if booted:
        problem = _systemd_user_problem()
        if problem:
            return f"{problem} — take it off from a login session of this user: ao watchdog uninstall"
        # What these answer is not read: each unit's state afterwards is what decides.
        _systemctl("disable", "--now", timer)
        _systemctl("stop", timer, service)
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
    states = _systemd_states(timer, service)
    while any(state in SYSTEMD_NOT_GONE for state in states.values()) and time.monotonic() < deadline:
        time.sleep(0.25)
        states = _systemd_states(timer, service)
    running = _systemd_running(states)
    if states[timer] in SYSTEMD_NOT_GONE:
        left.append(f"still {states[timer]} — systemctl --user stop {timer}")
    if states[service] in SYSTEMD_NOT_GONE:
        left.append(f"its service still {states[service]} — systemctl --user stop {service}")
    if running is not True and running is not False:
        left.append(f"{running}, so whether it still runs is not known — take it off from a login session of this "
                    "user: ao watchdog uninstall")
    return "; ".join(left) or None


def _systemd_running(states):
    """True when the user manager says one of these units runs or is about to, False when it says each is down;
    else why what it said is neither (LEADS-INSTALL).

    `_systemd_states` names no state when the manager cannot be asked, or does not answer a state for each unit,
    and that read as no unit running: a job the manager could not be asked about counted as gone.
    """
    if any(state in SYSTEMD_NOT_GONE for state in states.values()):
        return True
    if all(state in SYSTEMD_DOWN for state in states.values()):
        return False
    if None in states.values():
        return (_systemd_user_problem()
                or f"the user manager did not answer systemctl --user is-active {' '.join(states)}")
    unit, state = next((unit, state) for unit, state in states.items() if state not in SYSTEMD_DOWN)
    return f"systemctl --user is-active answers {state!r} for {unit}, neither running nor stopped"


def _task_held(name):
    """True when Task Scheduler holds a task of this name, False when it says it holds none; else why it cannot say.

    schtasks exits 1 for a task it does not have and for one it will not show alike - access is
    denied, the service does not answer - and says which in the machine's own language. Every
    failed query read as no task, so `ao remove` took a project's state while a task it could not
    read went on running against it (LEADS-INSTALL). So it is also asked for a name no task has,
    and a failed query is an absence only when it failed the same way, that name aside. And only
    when Task Scheduler answered at all, by listing its tasks: a failure every query shares - access
    to Task Scheduler denied, its service not running - read the same for both names, and was taken
    for an absence (LEADS-INSTALL).
    """
    import secrets
    said, status = _schtasks("/Query", "/TN", name)
    if status == 0:
        return True
    if status is None:
        return "schtasks could not be run"
    listed, listed_status = _schtasks("/Query")
    if listed_status != 0:
        return f"it lists no task: {listed[:120] or 'schtasks /Query exit ' + str(listed_status)}"
    nobody = f"ao-no-task-{secrets.token_hex(6)}"
    absent, absent_status = _schtasks("/Query", "/TN", nobody)
    if absent_status == status and absent.replace(nobody, "\0") == said.replace(name, "\0"):
        return False
    return said[:120] or f"schtasks exit {status}"


def _systemd_loaded(unit):
    """Whether the user manager runs one job's timer or service, or is about to: True, False when it says neither
    does, else why it cannot be asked - a job with no file left.

    The manager keeps a unit it has loaded after its files are gone, a timer running included, until it
    stops; files deleted by hand left one running that `ao remove` never looked for (LEADS-INSTALL). A
    manager this shell cannot reach, or an answer that is not a state for each unit, read as neither
    running, and the removal took the state of a project whose timer ran on: it is no answer, and the
    job counts as there (LEADS-INSTALL). False where systemd is not the init system: no manager holds
    a unit there, and the files are all there is to find.
    """
    if not os.path.isdir(SYSTEMD_RUNTIME):
        return False
    return _systemd_running(_systemd_states(unit + ".timer", unit + ".service"))


def _installed_jobs(key):
    """(kind, name) of each job ao scheduled for a project that is there to remove on this platform.

    A user systemd loads ao's units from the files `ao watchdog install` writes, and keeps one it
    loaded running after they are gone: on Linux a job is there while one of its files is, or
    while the manager runs it. A job its scheduler cannot say it lacks - Task Scheduler, a user
    manager or launchd that does not answer - is there too, so that removing it is tried, and
    what could not be asked is named (LEADS-INSTALL).
    """
    scheduler = _scheduler()
    if scheduler == "schtasks":
        return [("scheduled task", name) for name in (_windows_task(job, key) for job in WINDOWS_TASKS)
                if _task_held(name) is not False]
    if scheduler == "systemd":
        return [("systemd timer", unit + ".timer") for unit in (_systemd_unit(job, key) for job in SYSTEMD_JOBS)
                if any(os.path.lexists(path) for path in _systemd_files(unit)) or _systemd_loaded(unit) is not False]
    return [("launchd job", label) for label in (_launchd_label(job, key) for job in LAUNCHD_JOBS)
            if _launchd_loaded(label) is not False or os.path.lexists(_launchd_plist(label))]


def _unschedule(name):
    """Remove one scheduled job in this process and check that it is gone; what is left of it, or None.

    `ao remove` ran `python -m ao watchdog uninstall` and did not read the result: from a clone
    that interpreter has no ao module, the jobs went on running against a removed project, and
    remove said they were gone. The telegram poller, which launchd keeps alive, was never removed.
    A job its scheduler cannot say is gone is left, named, as one it still holds (LEADS-INSTALL).
    """
    scheduler = _scheduler()
    if scheduler == "schtasks":
        said, status = _schtasks("/Delete", "/TN", name, "/F")
        held = _task_held(name)
        if held is True:
            return f"Task Scheduler still holds it: {said[:120] or f'schtasks exit {status}'}"
        if held is not False:
            return (f"Task Scheduler cannot say whether it still holds it ({held}) — delete it from a prompt "
                    f"that can: schtasks /Delete /TN {name} /F")
        return None
    if scheduler == "systemd":
        return _systemd_unschedule(name)
    plist, left = _launchd_plist(name), []
    if _launchd_loaded(name) is not False and _launchctl("bootout", _launchd_domain(name))[1] != 0 \
            and os.path.exists(plist):
        _launchctl("unload", plist)
    try:
        if os.path.lexists(plist):
            os.remove(plist)
    except OSError as exc:
        left.append(f"cannot remove {_home_relative(plist)}: {exc}")
    deadline = time.monotonic() + JOB_GONE_SECONDS
    loaded = _launchd_loaded(name)
    while loaded is True and time.monotonic() < deadline:
        time.sleep(0.25)
        loaded = _launchd_loaded(name)
    if loaded is True:
        left.append(f"still loaded — launchctl bootout {_launchd_domain(name)}")
    elif loaded is not False:
        left.append(f"launchd cannot say whether it still holds it ({loaded}) — check from a login session of this "
                    f"user with `launchctl list`, and take it off: launchctl bootout {_launchd_domain(name)}")
    return "; ".join(left) or None


def _plan_words(rel):
    """The names that lead from the project to one path of `ao remove`'s plan, or None when it names none inside it.

    A path of the plan comes partly from words an agent can write - the reviews directory of .ao/config.json, a
    project adapter's `ao_files` - and `..` there named the directory above the project, `.` the project itself:
    a containment check of the directory the path is in passes for both, and the delete took that directory whole
    (LEADS-INSTALL). An empty name and `.` are dropped as a path's reading drops them; `..`, and a drive on
    Windows, name nothing inside.
    """
    words = [word for word in rel.replace(os.sep, "/").split("/") if word not in ("", ".")]
    if not words or ".." in words or os.path.splitdrive(os.path.join(*words))[0]:
        return None
    return words


def _remove_in_project(root, rel):
    """Delete one path of `ao remove`'s plan where it lies in the project; None once it is gone, else why it is not.

    A directory goes whole, a link in it as a link; a file or a link goes itself, never what a link
    points at. Every error of the recursive delete was ignored, and a removal that left part of .ao/
    behind said that AO state was gone (LEADS-INSTALL). A path whose directory resolves outside the
    project is left as it is: a link put on its way would send the delete there.

    Storage holds each folder while it is used: relative descriptors on POSIX and native handles
    without delete sharing on Windows. A platform with neither refuses the operation (LEADS-INSTALL).
    """
    from . import storage
    words = _plan_words(rel)
    if words is None:
        return "it names no path inside the project, and nothing is deleted for it"
    path = os.path.join(root, *words)
    if not os.path.lexists(path):
        return None
    parent = os.path.realpath(os.path.dirname(path))
    if not _hook_contains(root, parent):
        return f"its directory resolves outside the project, to {parent}, and nothing is deleted there"
    base = os.path.realpath(root)
    failed = []
    # The resolved parent above is only a diagnostic. Walk the plan's original names so a link to another
    # directory inside the project is refused too, rather than deleting from its target (LEADS-INSTALL).
    steps = words[:-1]
    try:
        with storage._tree_folder(base, steps, make=False) as directory:
            if directory is None:
                return None
            _delete_in_directory(directory, words[-1], failed)
            try:
                storage._tree_stat(directory, words[-1])
            except FileNotFoundError:
                return None
    except OSError as exc:
        failed.append(exc)
    return " ".join(str(failed[0]).split()) if failed else "it is still there"


def _delete_in_directory(directory, name, failed):
    """Delete `name` from the open directory `directory` without following a link; each error goes into `failed`.

    A file or a link goes itself. A directory is emptied first, each directory in it opened relative to the one it
    is in, without following a link, and each name deleted relative to the directory it is in, so that a link
    swapped in for one of them is deleted as a link or stops the walk where it stands (LEADS-INSTALL). It is a walk
    of ao's own because shutil.rmtree starts from a held directory only from Python 3.11, and ao runs on 3.9; it
    keeps a stack of its own, so that a tree nested deeper than the interpreter recurses ends no removal in a
    RecursionError.
    """
    import stat
    from contextlib import ExitStack
    from . import storage
    if isinstance(directory, int) and (os.rmdir not in os.supports_dir_fd or os.scandir not in os.supports_fd):
        failed.append(storage.InTheWay(name, "cannot hold folders for removal; ao refuses this file operation"))
        return
    opened_context = ExitStack()
    try:
        info = storage._tree_stat(directory, name)
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            storage._tree_remove_leaf(directory, name)
            return
        opened = opened_context.enter_context(storage._tree_child(directory, name))
        if opened is None:
            opened_context.close()
            return
    except FileNotFoundError:
        return
    except OSError as exc:
        failed.append(exc)
        return
    stack = [(directory, name, opened, _directory_entries(opened, failed), opened_context)]
    try:
        while stack:
            parent, here, held, entries, context = stack[-1]
            entry = next(entries, None)
            if entry is None:
                stack.pop()
                context.close()
                try:
                    storage._tree_rmdir(parent, here)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    failed.append(exc)
                continue
            child, is_directory = entry
            try:
                if is_directory:
                    context = ExitStack()
                    try:
                        inner = context.enter_context(storage._tree_child(held, child))
                    except BaseException:
                        context.close()
                        raise
                    if inner is None:
                        context.close()
                        continue
                    stack.append((held, child, inner, _directory_entries(inner, failed), context))
                else:
                    storage._tree_remove_leaf(held, child)
            except FileNotFoundError:
                pass
            except OSError as exc:
                failed.append(exc)
    finally:
        for _, _, _, _, context in reversed(stack):
            context.close()


def _directory_entries(directory, failed):
    """An iterator of (name, whether it is a directory itself, not through a link) over an open directory, read whole
    before anything in it is deleted; an error goes into `failed`, and the entries read before it stay."""
    found = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    found.append((entry.name, entry.is_dir(follow_symlinks=False)
                                  and (isinstance(directory, int) or not
                                       getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)))
                except OSError:
                    found.append((entry.name, False))          # deleted as a file, or named with why it is not
    except OSError as exc:
        failed.append(exc)
    return iter(found)


def cmd_remove(cfg, args):
    """Take ao off only after every reachable hook target passes one preflight, and while no lane stands."""
    root = cfg["root"]
    redirected = _commit_repository_binding(root)
    if redirected:
        print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: {redirected}")
        return 1
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
        words = _plan_words(rel)
        if words is None:
            print(f"   {C['red']}not {rel}{C['reset']}: it names no path inside the project")
        elif os.path.exists(os.path.join(root, *words)):
            print(f"   {rel}")
    for path, _, _ in mcp_entries:
        print(f"   {os.path.relpath(path, root)}: the `ao` server entry (other entries stay)")
    for path, why in mcp_unread:
        print(f"   {C['red']}cannot read {os.path.relpath(path, root)}{C['reset']}: {why}; any `ao` entry in it stays")
    try:
        _, _, dropped, unclaimed = _gitignore_without_ao(root)
    except OSError as exc:
        dropped, unclaimed = 0, []
        print(f"   {C['red']}cannot read .gitignore{C['reset']}: {exc}; the lines ao added stay")
    if dropped:
        print("   .gitignore: the lines ao added")
    for line, _ in unclaimed:
        print(f"   {C['yellow']}stays{C['reset']} .gitignore line {line} and the lines under it: {GITIGNORE_UNCLAIMED}")
    # Outside the tree, each thing by name, found as the removal below finds it (SAFE-REMOVE).
    for kind, name in _installed_jobs(key) if owned else []:
        print(f"   {kind} {name}")
    for path in _project_home_files(key, root) if owned else []:
        print(f"   {_home_relative(path)}")
    requests = A.review_requests_dir(root, key) if owned else None
    if requests and os.path.isdir(requests) and not os.path.islink(requests):
        # Its stand-in review requests, kept outside the tree since REVIEW-PATH-HIGHS, as .ao/ kept them before.
        print(f"   {_home_relative(requests)}/: the review requests ao wrote for it")
    for name in _registry_names(root):
        print(f"   {_home_relative(A.project_registry_path())}: the `{name}` entry (other projects stay)")
    archive = os.path.join(A.HOME, ".ao", "archive", key)
    print(f"   {C['dim']}hook files only when statically AO-owned, untracked, and fully authorized; "
          f"protected dead-misplaced files are preserved{C['reset']}")
    print(f"   {C['dim']}not touched: product files, reviews you moved elsewhere, "
          + (f"what ao archived in {_home_relative(archive)}/, " if owned and os.path.isdir(archive) else "")
          + f"other projects' and the machine's files in ~/.ao, "
          f"{' / '.join(skillkit.rule_file_names(root))} text (paste-in was yours){C['reset']}")
    # What only a layer an agent can write names as ao's is named and left (REMOVE-SHIPPED).
    for rel in skillkit.layer_ao_files(root):
        if os.path.lexists(os.path.join(root, rel)):
            print(f"   {C['dim']}left: {A.shown_text(rel)}, which only a user's or a project's adapter layer names "
                  f"as ao's; ao deletes what an adapter it ships names, and you can delete it if ao wrote it"
                  f"{C['reset']}")
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
    if enrollment["state"] in ("broken", "redirected"):
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
    # behind runs against the project every few minutes, so its state stays until none is left,
    # and so it does while a scheduler cannot say whether it holds one (LEADS-INSTALL).
    left = []
    for kind, name in _installed_jobs(key) if owned else []:
        problem = _unschedule(name)
        if problem:
            left.append(name)
            print(f"{C['red']}left{C['reset']} {kind} {name}: {problem}")
        else:
            print(f"removed {kind} {name}")
    if left:
        print(f"{C['red']}remove stopped; AO state kept intact{C['reset']}: {len(left)} scheduled job(s) may go on "
              "running against a removed project — take each off as said above, then run: ao remove --yes")
        return 1

    for rel in plan:
        problem = _remove_in_project(root, rel)
        if problem:
            left.append(os.path.join(root, rel))
            print(f"{C['red']}left{C['reset']} {rel}: {problem}")
    for path, mcp_key, remove_when_empty in mcp_entries:
        shown = os.path.relpath(path, root)
        try:
            gone = _drop_mcp_entry(path, mcp_key, remove_when_empty, root)
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
    gi = os.path.join(root, ".gitignore")
    try:
        gi, keep, dropped, unclaimed = _gitignore_without_ao(root)
        if dropped:
            from .storage import replace_in_tree
            replace_in_tree(root, ".gitignore", "\n".join(keep).encode(UTF8))
    except OSError as exc:
        left.append(gi)
        print(f"{C['red']}left{C['reset']} the lines ao added to .gitignore: {exc}")
    else:
        # Named, and no failure of the removal: lines ao cannot tell it wrote are the person's until they say not.
        for before, after in unclaimed:
            print(f"{C['yellow']}kept{C['reset']} .gitignore line {after if dropped else before} and the lines under "
                  f"it: {GITIGNORE_UNCLAIMED}")
    # Exactly this project's files in ~/.ao, named by the table their writers name them from, and
    # its row of the registry, which stays for every other project (SAFE-REMOVE).
    removed = 0
    for path in _project_home_files(key, root) if owned else []:
        try:
            os.remove(path)
            removed += 1
        except OSError as exc:
            left.append(path)
            print(f"{C['red']}left{C['reset']} {_home_relative(path)}: {exc}")
    if removed:
        print(f"removed {removed} file(s) of {key} from {_home_relative(os.path.join(A.HOME, '.ao'))}")
    # Its stand-in review requests, which ao keeps outside the tree, each file by name and then their folder, which
    # no other project's name reaches (REVIEW-PATH-HIGHS).
    requests = A.review_requests_dir(root, key) if owned else None
    if requests and os.path.isdir(requests) and not os.path.islink(requests):
        try:
            for name in os.listdir(requests):
                os.remove(os.path.join(requests, name))
            os.rmdir(requests)
            print(f"removed {_home_relative(requests)}/, the review requests ao wrote for it")
        except OSError as exc:
            left.append(requests)
            print(f"{C['red']}left{C['reset']} {_home_relative(requests)}/: {exc}")
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
                     else "plans are kept whole; never sealed" if name == "plans"
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
            print(json.dumps({"notice": None, "error": missing}, ensure_ascii=False) if as_json else A.shown_text(missing))
            return 1
        if as_json:
            print(json.dumps({"notice": _notice_facts(row)}, ensure_ascii=False))
            return 0
        when = datetime.fromtimestamp(row["at"]).strftime("%d %b %H:%M")
        print(f"{C['b']}{A.shown_text(row['title'])}{C['reset']}  {C['dim']}{A.shown_text(wanted)} · {when} · "
              f"{'sent' if row.get('sent') else 'held'} · key {A.shown_text(row.get('key'))}{C['reset']}")
        print(f"  {_shown_lines(row.get('msg'))}")
        for line in A.evidence_lines(row.get("evidence")):
            print(f"  {A.shown_text(line)}")
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
        print(f"  {C['dim']}{when}{C['reset']}  {tag}  {C['b']}{A.shown_text(r['title'])}{C['reset']}  {A.shown_text(r['msg'])}"
              + (f"  {C['dim']}{A.shown_text(r['id'])}{' · evidence' if r.get('evidence') else ''}{C['reset']}"
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
    interpreter running the module, when it imports ao from its own site directories. PATH is searched
    as ao searches it, its absolute entries alone and never the current directory: a console script
    found through a relative entry, made absolute, named a file in whatever directory install ran in,
    an agent's tree among them (WINDOWS-NO-CWD-EXEC, where SAFE-REMOVE-2 had made such a path absolute).
    """
    found = A.program_path(console)
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
            held = _task_held(name)
            print(f"{name}: " + ("scheduled" if held is True else "absent" if held is False
                                 else f"unknown — Task Scheduler cannot say ({held})"))
        return 0
    if args.action == "uninstall":
        # Each checked gone, as `ao remove` checks it: "removed" was printed whatever schtasks did (LEADS-INSTALL).
        left = 0
        for name in tasks:
            problem = _unschedule(name)
            left += bool(problem)
            print(f"{C['red']}left{C['reset']} {name}: {problem}" if problem else f"removed {name}")
        return 1 if left else 0
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
        # The fixture harness is started by this interpreter, isolated (-I) so that nothing beside the script is
        # imported. It was a copy whose first line named the interpreter, made executable: Windows runs no such
        # line and refused to start it, and an interpreter whose path holds a space made a line POSIX cannot read,
        # so every capability failed as "the harness did not run" (LEADS-INSTALL).
        with tempfile.TemporaryDirectory(prefix="ao-conform-") as workdir:
            results = A.conform_adapter(adapter, [sys.executable, "-I", harness], workdir)
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
        ("keyflip", "installed" if A.program_path("keyflip") else "absent",
         "install keyflip for account budgets and quota rotation"),
        ("telegram", "configured" if telegram.config() else "absent", "ao telegram setup"),
        ("email", f"configured ({mail['provider']})" if mail else "absent", "ao email setup"),
        # The command that sets the ping up: the hint named `ao ping set <url>`, which never existed (SECRETS-2).
        ("ping", "configured" if A.ping_url(root) else "absent", "ao pings setup"),
    ]
    # A tool reviewer is an optional extra with its own provider account (#86).
    primary = cfg.get("reviewer") or {}
    routes = [primary] + list(primary.get("fallbacks") or []) if isinstance(primary, dict) else []
    configured = {route.get("adapter") for route in routes if _tool_route(route)}
    for ident, adapter in sorted(A.package_adapters().items()):
        contract = A.tool_review_contract(adapter)
        if contract is None:
            continue
        found = any(A.program_path(binary) or _tool_beside_interpreter(binary)
                    for binary in A.adapter_binaries(adapter))
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


def _harness_config_lines(cfg):
    """Each change to what a harness reads from this tree that no person has seen, by name, and what records it as
    seen; or that there is none, or that it cannot be read (HARNESS-CONFIG)."""
    unseen, unreadable = _harness_config_unseen(cfg["root"])
    if unreadable:
        return [f"{'harness config':<16}{C['red']}cannot be read: {unreadable}{C['reset']}  {C['dim']}no wake, refill, "
                f"nudge, hunt or lane starts here until it can{C['reset']}"]
    if not unseen:
        return [f"{'harness config':<16}{C['green']}no change nobody has seen{C['reset']}"]
    lines = [f"{'harness config':<16}{C['yellow']}{len(unseen)} change(s) nobody has seen{C['reset']}  "
             f"{C['dim']}no wake, refill, nudge, hunt or lane starts here until a person reads them: "
             f"ao harness seen --by <name>{C['reset']}"]
    lines += [f"{'':<16}{A.shown_text(path)} ({A.HARNESS_CONFIG_STATES.get(state, state)})"
              for path, state, _ in unseen]
    return lines


def _environment_lines(cfg):
    """What ao runs from, loads or trusts inside the tree an agent writes, one line each; none when nothing is
    (PYTHON-ENV)."""
    found = dict(_environment_problem(cfg["root"])).get("environment-in-tree")
    return [f"{'environment':<16}{C['yellow']}{found}{C['reset']}"] if found else []


def _turn_command_lines(cfg):
    """Each role's command the watchdog refuses to start, in the words it refuses it in (TURN-ARGV)."""
    return [f"{'turn command':<16}{C['red']}{text}{C['reset']}" for _, text in _turn_problems(cfg)]


# Said as what is accepted, never as a word to a reviewer: a boundary that tells a reviewer what to answer reads as
# the instruction a candidate must not carry (the review of TURN-ARGV). One sentence with no semicolon: a boundary's
# semicolons part its criteria, and each criterion needs a verdict of its own (PROVE-BOUNDARY-2).
PROVE_BOUNDARY = "ao prove: a throwaway candidate whose whole diff adds one line to ao-prove.txt and nothing else."


def _prove_throwaway(cfg):
    """A one-line slice through verify, review and commit-ok in a temporary worktree; nothing lands (#85).

    Returns (ok, what failed or None, what would fix it or None, worktree path or None). The worktree's
    coordination state and review go to ~/.ao/archive/<project>/prove-<stamp>/.
    """
    import io
    import shutil
    import tempfile
    root = cfg["root"]
    scratch = tempfile.mkdtemp(prefix="ao-prove-")
    tree = os.path.join(scratch, "tree")
    links, unlinked, heard = [], [], io.StringIO()
    made = False
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        added = subprocess.run([A.git_binary(), "worktree", "add", "--detach", "--quiet", tree, "HEAD"], cwd=root,
                               capture_output=True)
        made = added.returncode == 0 or os.path.lexists(os.path.join(tree, ".git"))
        if added.returncode:
            result = (False, "a temporary worktree could not be made: "
                      + added.stderr.decode(UTF8, "replace").strip(), "a repository with at least one commit")
        else:
            # A step that raises is a check that failed, said with what would fix it: it ended `ao prove` in a
            # traceback (PROVE-2).
            try:
                result = _prove_steps(cfg, root, tree, stamp, links, unlinked, heard)
            except Exception as exc:
                result = (False, f"the throwaway slice stopped: {type(exc).__name__}: {' '.join(str(exc).split())}",
                          "run the step it names by hand in this checkout, or `ao doctor`")
    finally:
        for link in links:
            try:
                os.unlink(link)
            except OSError:
                pass
        # An archive that cannot be written is said, and the worktree goes all the same: a copy that raised here
        # skipped the removal below and left the worktree registered in the checkout (LEADS-INSTALL).
        try:
            _archive_throwaway(cfg, root, tree, stamp)
        except Exception as exc:
            print(f"  {C['yellow']}the throwaway's coordination state and review were not archived{C['reset']}: "
                  f"{type(exc).__name__}: {' '.join(str(exc).split())}")
        finally:
            try:
                cleanup_steps = (("remove", "--force", tree), ("prune",)) if made else ()
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
                result = (False, f"worktree cleanup failed: {exc}", "repair the worktree administration and retry")
    return (*result, tree if made else None)


def _archive_throwaway(cfg, root, tree, stamp):
    """Copy the throwaway's .ao/ and reviews to ~/.ao/archive/<project>/prove-<stamp>/, a directory of this run's own.

    Named by the second, two runs in one second shared a directory and the second was merged into the
    first (LEADS-INSTALL): one that is there already is followed by -2, -3 and on, each made by this run.
    """
    import shutil
    parts = [part for part in (".ao", cfg.get("reviews", "semantic-review")) if os.path.isdir(os.path.join(tree, part))]
    if not parts:
        return None
    parent = os.path.join(A.HOME, ".ao", "archive", A.project_key(root))
    os.makedirs(parent, exist_ok=True)
    count = 1
    while True:
        archive = os.path.join(parent, f"prove-{stamp}" + (f"-{count}" if count > 1 else ""))
        try:
            os.mkdir(archive)
            break
        except FileExistsError:
            count += 1
    for part in parts:
        shutil.copytree(os.path.join(tree, part), os.path.join(archive, part), symlinks=True)
    return archive


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
    # Its reviewer is this project's, accepted or not by this project's record, never a fresh one's (ROUTE-ARGV).
    A.hold_unseen_routes(root, tree_cfg)
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
    worktree = None
    enrollment = _project_enrollment(root)
    if enrollment["state"] == "redirected":
        print(f"{C['red']}prove refused{C['reset']}: {enrollment['detail']}")
        return 1
    probe = _hook_execution_probe(_ao_hook_inventory(root))
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
        ok, failed, fix, worktree = _prove_throwaway(cfg)
        results.append(("a throwaway slice lands end to end", ok, None if ok else f"{failed} — {fix}"))
    for claim, ok, fix in results:
        print(f"  {C['green'] + 'proven' if ok else C['red'] + 'NOT PROVEN'}{C['reset']}  {claim}")
        if fix:
            print(f"      {fix}")
    proven = all(ok for _, ok, _ in results)
    if worktree is None:
        cleanup = "no throwaway worktree was made"
    elif os.path.lexists(worktree):
        cleanup = f"the throwaway worktree remains at {worktree}; inspect it before retrying cleanup"
    else:
        cleanup = "the throwaway worktree is gone"
    print(f"\n{C['b']}{'PROVEN' if proven else 'NOT PROVEN'}{C['reset']}  "
          f"{C['dim']}nothing was committed; {cleanup}{C['reset']}")
    return 0 if proven else 1


def _init_then_prove(cfg, args):
    code = cmd_init(cfg, args)
    if code or not getattr(args, "prove", False):
        return code
    print(f"\n{C['b']}proving the guarantees{C['reset']}")
    return cmd_prove(A.load_config(cfg["root"]), args)


def cmd_demo(cfg, args):
    """`ao demo [--keep]`: the guarantee played on a project of its own, before anyone adopts ao (DEMO).

    `ao prove` runs the guarantees on a project that has ao; the demo shows them to someone who has
    none yet, in a scratch folder with a home of its own. It uses nothing of `cfg`, the project the
    command was run in, which ao loaded before dispatching as it does for every command, and writes
    nothing outside that folder. The demo is src/ao/demo.py.
    """
    from . import demo
    return demo.run(keep=getattr(args, "keep", False))


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
    """Name row bounds separately from byte bounds, with the action that holds each (TRIAGE-1-LOW)."""
    from .watchdog import STATE_DIR
    from .storage import LedgerCorruption
    try:
        over = A.stores_over_bound(cfg["root"], cfg, STATE_DIR)
    except (OSError, LedgerCorruption) as exc:
        return [f"{'stores':<16}{C['yellow']}cannot measure evidence retention{C['reset']}: "
                f"{' '.join(str(exc).split())}; restore the ledger from a known-good backup and run `ao doctor` again"]
    if not over:
        return []
    lines = []
    for unit in ("bytes", "rows"):
        stores = [(path, size, limit) for path, size, limit, kind in over if kind == unit]
        if not stores:
            continue
        detail = ", ".join(f"{os.path.basename(path)} " +
                           (f"{size}/{limit} rows" if unit == "rows" else f"{size // 1024}KB/{limit // 1024}KB")
                           for path, size, limit in stores)
        action = ("run `ao prune --evidence --yes` to seal older rows" if unit == "rows" else
                  "the watchdog trims them each cycle; is it running?")
        lines.append(f"{'stores':<16}{C['yellow']}{len(stores)} over their bound{C['reset']}  "
                     f"{C['dim']}{detail} — {action}{C['reset']}")
    return lines


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
        A.authority_history(cfg["root"])
    except Exception as exc:
        # Which artefacts a grant rests on is the chain's word; without it every one was kept (GOVERNANCE-BACKUP-2).
        # The word of the whole chain, the archives a seal wrote held to it (LEDGER-HISTORY).
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
        A.authority_history(root)            # the sealed archives too, as one chain (LEDGER-HISTORY)
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


def _machine_line(cfg):
    """The machine as a gate run reads it, on one line: memory, paging and disk, each past its limit naming the limit
    (MACHINE-PRESSURE). Not a problem `ao doctor --check` pages about: a busy machine is waited for, not mended."""
    from . import pressure
    reading = pressure.read()
    tone = C["yellow"] if pressure.shortfalls(reading, cfg) else C["dim"] if reading.unmeasured else C["green"]
    return f"{'machine':<16}{tone}{pressure.summary(reading, cfg)}{C['reset']}"


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
    redirected = _commit_repository_binding(root)
    if redirected:
        print(f"{C['red']}repository redirect{C['reset']}: {redirected}")
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
        return 1 if redirected else None
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
    for line in _harness_config_lines(cfg):
        print(line)
    for line in _environment_lines(cfg):
        print(line)
    for line in _turn_command_lines(cfg):
        print(line)
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
        wd = _schtasks("/Query", "/TN", _windows_task("watchdog", key))[1] == 0
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
        enrollment = _project_enrollment(root)
        legacy_project = enrollment["state"] == "legacy"
        for role, label in (("pre-commit", "commit hook"), ("pre-push", "push hook")):
            target = active[role]
            base = _state_base(target["static_state"])
            current = base in ("current-local", "current-scoped")
            tone = C["green"] if current else C["yellow"]
            notes = []
            if enrollment["state"] == "redirected":
                tone = C["yellow"]
                notes.append(enrollment["detail"])
            elif role == "pre-commit" and not current:
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
    print(_machine_line(cfg))
    for line in _measurement_lines(cfg):
        print(line)
    for _, text in _path_problems():                                                  # (WINDOWS-NO-CWD-EXEC)
        print(f"{'PATH':<16}{C['yellow']}{text}{C['reset']}")
    # What ao cannot hold on Windows, and why, where a person looks for it; none is a fault to page about
    # (WINDOWS-SAY-WHY).
    for line in _windows_limit_lines():
        print(line)
    # Can the *agent* run `ao`? A shell alias is invisible to a non-interactive
    # process, so steering that says "run your gates through ao lock" is an
    # instruction the agent cannot follow — and a disciplined agent then parks the
    # slice rather than working around it. That cost this project three parked
    # items and half a day. Check the child's PATH, not this shell's.
    from .watchdog import child_path
    reachable = A.program_path("ao", path=child_path())
    # The architect is woken through a binary the watchdog resolves by version,
    # not by PATH order; say which one, and whether the last wake died on it.
    arch = cfg.get("architect") or {}
    if arch.get("argv"):
        from .watchdog import wake_error, STATE_DIR, architect_command
        # Use the wake's preparation and refusal values before any version probe (ARCH-PROBE-ORDER).
        command, _, stopped = architect_command(root, cfg, arch.get("prompt", language.text(cfg, "prompt.refill")))
        rb, rv = (None, "") if stopped else A.resolve_binary(command["argv"][0], path=command["path"])
        others = [] if stopped else [c for c in A.binary_candidates(command["argv"][0], command["path"]) if c != rb]
        if stopped:
            print(f"architect bin   {C['red']}not asked{C['reset']} {C['dim']}{A.shown_text(stopped)}{C['reset']}")
        else:
            print(f"architect bin   {C['green'] if rb else C['red']}{rb or 'not found'}{C['reset']} {C['dim']}{rv}"
                  f"{C['reset']}" + (f"  {C['dim']}({len(others)} older copy: {', '.join(others)}){C['reset']}"
                                     if others else ""))
        key = A.project_key(root)
        we = wake_error(os.path.join(STATE_DIR, A.project_file_name("escalate-log", key)))
        if we:
            text, used, when = we["text"], we["binary"], we["when"]
            print(f"last wake       {C['red']}failed{C['reset']} {when} [{used or '?'}]: {text[:90]}")
            if stopped:
                print(f"                {C['dim']}no wake starts while the architect's command is held or refused"
                      f"{C['reset']}")
            elif not used:
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
    # Only this line is about finding ao for the agents. Every line above was printed only when ao was found,
    # and a machine where it was not lost the heartbeat, the alarms, the transcript, the credits, the ping and
    # the channels with it (LEADS-INSTALL).
    if reachable:
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
    for cand in (A.program_path("ao"), os.path.join(A.HOME, ".local", "bin", "ao"),
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
    return 0 if reviewer_probe["ok"] and not redirected else 1
