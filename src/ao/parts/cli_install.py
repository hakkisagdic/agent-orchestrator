"""Installation: `ao update` and `ao uninstall`, so a person never has to learn how ao was installed (UPDATE-UNINSTALL).

A part of src/ao/cli.py, run in its namespace by `_part`; it is not importable on its own. What these commands
take off a machine they take the way it went on: the jobs through `_installed_jobs` and `_unschedule`, with which
`ao remove` takes one project's, the hooks through `_ao_hook_inventory` and `_hooks_uninstall`, and the MCP
entries from the files the adapters declare, where `ao init` merged them.
"""


# The distribution pip, pipx and uv install: pyproject.toml's [project] name. The import package and the command
# are `ao`; `agent-orchestrator` on PyPI is another project's (packaging/RELEASING.md).
DISTRIBUTION = "ao-orchestrator"
# How each kind of installation reads in "installed <how> at <where>".
INSTALLED_HOW = {"clone": "from a git clone", "homebrew": "with Homebrew", "pipx": "with pipx", "uv": "with uv tool",
                 "pip": "with pip"}
# The hook states that are ao's own bytes, whatever a target's track state: what an uninstall looks for.
AO_HOOK_STATES = ("current-local", "current-scoped", "legacy", "ambiguous-ao")


def _argv_text(argv):
    """An argument vector as words a person can read, and paste into a shell, exactly as it runs."""
    return " ".join(A._shell_word(str(word)) for word in argv)


def _installation(package=None, prefix=None, python=None):
    """How the ao running now was installed, and the commands that update and remove it.

    {kind, where, update, remove, env, missing}: kind is clone, homebrew, pipx, uv or pip, or None when it
    cannot be told; where is the clone, the keg, the environment or the site directory; update and remove are
    argument vectors (remove is None for a clone, which is deleted rather than uninstalled); env is the
    environment the update runs in, None for this one; missing names the program an update needs when it is not
    on PATH. It is the code running now that is asked about, not the first ao on PATH: this is its command.

    Told apart in this order, since what marks a later kind can be true of an earlier one too. A clone - the
    quickstart's symlink to bin/ao, or an editable install - is a src/ tree beside bin/ao, with git's .git
    around them. Homebrew keeps a formula's virtualenv in a keg, <prefix>/Cellar/<formula>/<version>/, which
    holds its INSTALL_RECEIPT.json, and its brew in <prefix>/bin. pipx and uv mark the environment they made with
    pipx_metadata.json and uv-receipt.toml and name it for what they installed. pip leaves the package in one of
    this interpreter's site directories; the user's one means it was given --user, and so must the upgrade be, or
    it installs a second copy beside the first.
    """
    import site
    package = os.path.realpath(package or os.path.dirname(os.path.abspath(A.__file__)))
    prefix = os.path.realpath(prefix or sys.prefix)
    python = python or sys.executable
    found = {"kind": None, "where": package, "update": None, "remove": None, "env": None, "missing": None}
    source = os.path.dirname(package)
    clone = os.path.dirname(source)
    if os.path.basename(source) == "src" and os.path.isfile(os.path.join(clone, "bin", "ao")) \
            and os.path.exists(os.path.join(clone, ".git")):
        # Run as ao's hooks run git: no repository, index or configuration the caller's environment names
        # reaches the pull, which touches the clone and nothing else.
        return dict(found, kind="clone", where=clone, env=_hook_git_env(),
                    update=[A.git_binary(), "-C", clone, "pull", "--ff-only"])
    parts = package.split(os.sep)
    if "Cellar" in parts:
        at = parts.index("Cellar")
        keg = os.sep.join(parts[:at + 3])
        if len(parts) > at + 3 and os.path.isfile(os.path.join(keg, "INSTALL_RECEIPT.json")):
            formula = parts[at + 1]
            brew = os.path.join(os.sep.join(parts[:at]) or os.sep, "bin", "brew")
            brew = brew if os.path.isfile(brew) and os.access(brew, os.X_OK) else shutil.which("brew")
            return dict(found, kind="homebrew", where=os.sep.join(parts[:at + 2]), missing=None if brew else "brew",
                        update=[brew or "brew", "upgrade", formula], remove=[brew or "brew", "uninstall", formula])
    if _hook_contains(prefix, package):
        for kind, marker, tool, verb in (("pipx", "pipx_metadata.json", "pipx", []),
                                         ("uv", "uv-receipt.toml", "uv", ["tool"])):
            if os.path.isfile(os.path.join(prefix, marker)):
                program, name = shutil.which(tool), os.path.basename(prefix)
                return dict(found, kind=kind, where=prefix, missing=None if program else tool,
                            update=[program or tool, *verb, "upgrade", name],
                            remove=[program or tool, *verb, "uninstall", name])
    sites = list(site.getsitepackages()) if hasattr(site, "getsitepackages") else []
    user = site.getusersitepackages() if hasattr(site, "getusersitepackages") else None
    if user and os.path.realpath(user) == source:
        flags = ["--user"]
    elif any(os.path.realpath(path) == source for path in sites):
        flags = []
    else:
        return found
    return dict(found, kind="pip", where=source,
                update=[python, "-m", "pip", "install", "--upgrade", *flags, DISTRIBUTION],
                remove=[python, "-m", "pip", "uninstall", DISTRIBUTION])


def _clone_state(clone):
    """(why `git pull --ff-only` must not run in this clone, or None; its branch and what that tracks).

    Uncommitted changes to what git tracks are a person's work, and an update is no reason to put them at risk.
    Untracked files do not count: a fast-forward touches one only when it brings a file of that name itself, and
    then git refuses before it writes anything. A detached HEAD, or a branch that tracks nothing, has nothing a
    pull can fast-forward to. A git that cannot answer is a refusal too, never a clean clone.
    """
    try:
        status = _hook_git(clone, "status", "--porcelain", "--untracked-files=no")
        branch = _hook_git(clone, "symbolic-ref", "--quiet", "--short", "HEAD")
        upstream = _hook_git(clone, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    except _HookResolutionError as exc:
        return f"git cannot read the clone at {clone}: {exc}", None
    if status.returncode:
        detail = status.stderr.decode(UTF8, "replace").strip()[:160] or f"git exit {status.returncode}"
        return f"git cannot read the clone at {clone}: {detail}", None
    changed = [line[3:] for line in status.stdout.decode(UTF8, "replace").splitlines() if line.strip()]
    if changed:
        more = f" and {len(changed) - 3} more" if len(changed) > 3 else ""
        return (f"the clone at {clone} has uncommitted changes ({', '.join(changed[:3])}{more}); commit or stash "
                "them, then run ao update again"), None
    name = branch.stdout.decode(UTF8, "replace").strip()
    if branch.returncode or not name:
        return f"the clone at {clone} is on no branch (a detached HEAD); check out the branch to update", None
    tracked = upstream.stdout.decode(UTF8, "replace").strip()
    if upstream.returncode or not tracked:
        return (f"branch {name} of the clone at {clone} tracks no upstream; set one with "
                "`git branch --set-upstream-to`"), None
    return None, f"branch {name}, tracking {tracked}"


def _confirm(question):
    """A person's answer at the terminal: True for yes, False for anything else, None when nobody can be asked.

    Nobody can when standard input is no terminal - a pipe, a scheduled job, an agent's tool call - and there
    silence must not read as consent.
    """
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return None
        return input(question).strip().lower() in ("y", "yes")
    except (EOFError, OSError, ValueError):
        return False


def _run_update(argv, env=None):
    """Run one update in the foreground, its output going to the person as the tool writes it.

    The exit status, or None when the program cannot be started. ao reads none of what it prints: whether
    anything was newer, and what changed, is the tool's to say.
    """
    try:
        return subprocess.run(argv, env=env).returncode
    except OSError:
        return None


def cmd_update(cfg, args):
    """Update ao the way it was installed, having said what will run (UPDATE-UNINSTALL).

    A clone is fast-forwarded, a Homebrew, pipx or uv install is upgraded by its own tool, and a pip install by
    this interpreter's pip. The command is printed before anything runs, and it runs on --yes or a yes at the
    terminal; --dry-run prints it and stops. A clone with uncommitted changes, on no branch or tracking nothing is
    refused first, and so is an installation ao cannot tell. Exit 0 once the update ran, or on a dry run; 1 when
    it was refused, declined or failed.
    """
    found = _installation()
    kind = found["kind"]
    print(f"ao {__version__}, " + (f"installed {INSTALLED_HOW[kind]} at {found['where']}" if kind
                                   else f"running from {found['where']}"))
    if kind is None:
        print(f"{C['red']}not updated{C['reset']}: this is no git clone, and no Homebrew, pipx, uv or pip "
              "installation holds it; update it the way you installed it")
        return 1
    if found["missing"]:
        print(f"{C['red']}not updated{C['reset']}: {found['missing']} updates this installation, and it is not "
              "on PATH")
        return 1
    if kind == "clone":
        problem, detail = _clone_state(found["where"])
        if problem:
            print(f"{C['red']}not updated{C['reset']}: {problem}")
            return 1
        print(f"  {detail}")
    print(f"will run: {_argv_text(found['update'])}")
    if args.dry_run:
        print(f"{C['dim']}dry run: nothing was run{C['reset']}")
        return 0
    if not args.yes:
        answer = _confirm("run it? [y/N] ")
        if not answer:
            print("not run" + (": nobody can answer here, standard input is not a terminal; run it again with --yes"
                               if answer is None else ""))
            return 1
    code = _run_update(found["update"], found["env"])
    if code is None:
        print(f"{C['red']}update failed{C['reset']}: {found['update'][0]} could not be started")
        return 1
    if code:
        print(f"{C['red']}update failed{C['reset']}: {os.path.basename(found['update'][0])} exited {code}; "
              "what it said is above")
        return 1
    print(f"{C['green']}updated{C['reset']}; `ao --version` names the version now installed")
    return 0


# ---- ao uninstall ---------------------------------------------------------------------------------------------

def _machine_jobs():
    """(kind, name) of every job ao scheduled on this machine, as `_unschedule` takes one.

    launchd: every label in ao's namespace (LAUNCHD_NAMESPACE, beside `_launchd_label`), whether its plist is in
    LaunchAgents or launchd holds it loaded with the plist gone, so the jobs of a project deleted since, or never
    in the registry, go with the rest. A user systemd (LINUX-SCHEDULER) likewise: every unit in ao's namespace,
    `ao-<job>-<project>` (`_systemd_unit`), with a file in the user unit directory - the link `enable` made
    counts, as `_installed_jobs` reads a job. Task Scheduler offers no namespace to list, so there they are the
    tasks of the projects the machine registry knows, named as `ao watchdog install` names them.
    """
    if os.name == "nt":
        found = []
        for key in sorted(A.project_registry()):
            found += [job for job in _installed_jobs(key) if job not in found]
        return found
    if _scheduler() == "systemd":
        prefixes, units = tuple(f"ao-{job}-" for job in SYSTEMD_JOBS), set()
        for directory in (_systemd_dir(), os.path.join(_systemd_dir(), "timers.target.wants")):
            try:
                names = os.listdir(directory)
            except OSError:
                continue
            units |= {name.rpartition(".")[0] for name in names
                      if name.startswith(prefixes) and name.rpartition(".")[2] in ("service", "timer")}
        return [("systemd timer", unit + ".timer") for unit in sorted(units)]
    try:
        names = os.listdir(os.path.dirname(_launchd_plist(LAUNCHD_NAMESPACE)))
    except OSError:
        names = []
    labels = {name[:-len(".plist")] for name in names
              if name.startswith(LAUNCHD_NAMESPACE) and name.endswith(".plist")}
    labels |= {label for label in (line.rsplit("\t", 1)[-1].strip() for line in _launchctl("list")[0].split("\n"))
               if label.startswith(LAUNCHD_NAMESPACE)}
    return [("launchd job", label) for label in sorted(labels)]


def _serves_ao(entry):
    """Whether an MCP server entry is the one `ao init` registered: it runs `ao … mcp serve` (UPDATE-UNINSTALL-2).

    skillkit.register_mcp writes {command: the ao it found, args: [-C, <root>, mcp, serve]}, followed by
    `--role <role>` when the registration names one (MCP-ROLES); an agent's own CLI, asked to register the
    same, keeps the command, and an ao run as `python -m ao` keeps its interpreter. So an entry is ao's when
    its arguments end with `mcp serve` and nothing after it but ao's own options, `--role` and
    `--allow-verify`, whatever its command is called: a server named `ao` whose program is another one
    called ao was taken for ao's on its name alone, and removed. One ao registered some other way is left,
    since leaving it is the smaller harm.
    """
    if not isinstance(entry, dict):
        return False
    args = entry.get("args")
    words = [str(word) for word in args] if isinstance(args, list) else []
    served = [index for index in range(len(words) - 1) if words[index:index + 2] == ["mcp", "serve"]]
    if not served:
        return False
    rest, index = words[served[-1] + 2:], 0
    while index < len(rest):
        if rest[index] == "--allow-verify" or rest[index].startswith("--role="):
            index += 1
        elif rest[index] == "--role" and index + 1 < len(rest):
            index += 2
        else:
            return False
    return True


def _mcp_entries(root):
    """([(file, key, remove when empty)] holding ao's MCP server entry, [(file, why)] that could not be read).

    Looked for where `ao init` merged the entry (skillkit.register_mcp): in the file each adapter declares, under
    the key it declares. Two adapters may declare one file: it may go when either says so. A declared file that
    resolves outside the project is not read, so an adapter a project declares for itself cannot point an
    uninstall at another file. `ao remove` takes its project's entries from here too (UPDATE-UNINSTALL-2).
    """
    from . import skillkit
    declared = {}
    for _, adapter in skillkit.setup_adapters(root):
        mcp = adapter.get("mcp") or {}
        if not mcp.get("file"):
            continue
        spot = (os.path.join(root, *str(mcp["file"]).split("/")), mcp.get("key", "mcpServers"))
        declared[spot] = declared.get(spot, False) or bool(mcp.get("remove_when_empty"))
    found, unread = [], []
    for (path, key), remove_when_empty in sorted(declared.items()):
        if not os.path.isfile(path) or not _hook_contains(root, path):
            continue
        try:
            with open(path, encoding=UTF8) as fh:
                document = json.load(fh)
        except (OSError, ValueError) as exc:
            unread.append((path, " ".join(str(exc).split())))
            continue
        servers = document.get(key) if isinstance(document, dict) else None
        if isinstance(servers, dict) and _serves_ao(servers.get("ao")):
            found.append((path, key, remove_when_empty))
    return found, unread


def _drop_mcp_entry(path, key, remove_when_empty):
    """Take ao's server out of one MCP file, every other entry and key kept; "entry" or "file", what went.

    Written whole through storage.replace_file_durably, at the resolved path so a symbolic link to the file stays
    one. The file itself goes only when its adapter says so and nothing but the empty server table is left in it.
    Raises OSError or ValueError when the file cannot be read or written, or no longer holds ao's entry.
    """
    from .storage import replace_file_durably
    real = os.path.realpath(path)
    with open(real, encoding=UTF8) as fh:
        document = json.load(fh)
    servers = document.get(key) if isinstance(document, dict) else None
    if not isinstance(servers, dict) or not _serves_ao(servers.get("ao")):
        raise ValueError(f"no `ao` entry under {key} any more")
    del servers["ao"]
    if remove_when_empty and document == {key: {}}:
        os.remove(real)
        return "file"
    replace_file_durably(real, json.dumps(document, indent=2).encode(UTF8))
    return "entry"


def _registry_problem():
    """Why the machine registry cannot be read, or None; a registry that is simply not there is no problem.

    A.project_registry() reads an unreadable registry as holding no project, which for an uninstall would be a
    silent pass over every project's hooks.
    """
    path = A.project_registry_path()
    try:
        with open(path, encoding=UTF8) as fh:
            rows = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        return (f"{_home_relative(path)} cannot be read ({' '.join(str(exc).split())}), so no project's hooks are "
                "looked at")
    return None if isinstance(rows, dict) else (f"{_home_relative(path)} holds no JSON object, so no project's "
                                                "hooks are looked at")


def _uninstall_projects():
    """What an uninstall finds in each project the machine registry knows: one dict per resolved path.

    names, every name the registry holds for the path, and root; gone when the directory is not there; problem
    when its hooks cannot be resolved in a git work tree; inv, the hook inventory `_hooks_uninstall` acts on;
    hooks, the files it would remove; stays, ao's hooks it leaves - tracked, of a track state git cannot measure
    (outside every work tree), or not ao's exact bytes; mcp and unread, from `_mcp_entries`.
    """
    rows = {}
    for name, row in sorted(A.project_registry().items()):
        if row["root"] in rows:
            rows[row["root"]]["names"].append(name)
            continue
        project = rows[row["root"]] = {"names": [name], "root": row["root"], "gone": False, "problem": None,
                                       "inv": None, "hooks": [], "stays": [], "mcp": [], "unread": []}
        root = row["root"]
        if not os.path.isdir(root):
            project["gone"] = True
            continue
        inv = _ao_hook_inventory(root)
        if inv["error"]:
            try:
                in_git = _hook_git(root, "rev-parse", "--git-dir").returncode == 0
            except _HookResolutionError:
                in_git = True                   # unknown: say so rather than call it hook-free
            project["problem"] = f"its hooks cannot be resolved: {inv['error']}" if in_git else None
        else:
            project["inv"] = inv
            project["hooks"] = _hook_removal_plan(inv)
            project["stays"] = [target for target in inv["targets"] if target not in project["hooks"]
                                and _state_base(target["static_state"]) in AO_HOOK_STATES]
        project["mcp"], project["unread"] = _mcp_entries(root)
    return list(rows.values())


def _program_removal(found):
    """What removes the ao program itself, as an instruction to a person: `ao uninstall` runs none of it."""
    if found["remove"]:
        return f"run `{_argv_text(found['remove'])}`"
    if found["kind"] == "clone":
        target = os.path.realpath(os.path.join(found["where"], "bin", "ao"))
        links = sorted({path for path in (shutil.which("ao"), os.path.join(A.HOME, ".local", "bin", "ao"))
                        if path and os.path.islink(path) and os.path.realpath(path) == target})
        return f"delete the clone {found['where']}" + "".join(f" and the link {link}" for link in links)
    return "remove it the way it was installed"


def _pinged():
    """How many dead man's switch URLs `ao pings setup` recorded: the pings stop with the jobs."""
    try:
        with open(A.pings_path(), encoding=UTF8) as fh:
            urls = json.load(fh)
    except (OSError, ValueError):
        return 0
    return len([url for url in urls.values() if url]) if isinstance(urls, dict) else 0


def _uninstall_leaves(args, found):
    """The lines naming what an uninstall leaves on this machine, and what takes each."""
    lines = [f"in each project: .ao/ with its ledgers, the mailbox, reviews, the .ao-project marker and the "
             f"playbook files — `ao remove --yes` in a project takes those, while ao is still installed",
             f"projects {_home_relative(A.project_registry_path())} does not hold: `ao hooks uninstall` in each"]
    if not args.purge:
        lines.append(f"{_home_relative(os.path.join(A.HOME, '.ao'))}: ao's settings, channel credentials, logs, "
                     "archived reviews and the project registry — `--purge` deletes it")
    lines.append(f"the program itself: {_program_removal(found)}")
    pinged = _pinged()
    if pinged:
        lines.append(f"the dead man's switch: {pinged} ping URL(s) stop being called with the jobs, and the "
                     "service behind them will alarm — pause the check there")
    if _scheduler() == "systemd":
        lines.append("systemd: a unit you wrote to run ao stays; ao's own, the timers `ao watchdog install` wrote, "
                     "are among the jobs above (LINUX-SCHEDULER)")
    elif shutil.which("systemctl"):
        lines.append("systemd: ao schedules no unit of its own here (its jobs are launchd's or Task Scheduler's), "
                     "so a unit you wrote to run ao stays")
    return lines


def cmd_uninstall(cfg, args):
    """Take off this machine what ao installed on it, a dry run unless --yes (UPDATE-UNINSTALL).

    Three things of ao's run or load without anyone asking. Its scheduled jobs go first, each checked gone, so
    none runs against what follows. Then, in every project the machine registry knows, its git hooks - only
    files that are ao's exact bytes and untracked, as `ao hooks uninstall` takes them, with the same
    --allow-shared-hooks - and the `ao` entry `ao init` merged into the project's MCP files. The hooks matter
    most: once the program is gone, an enrolled project's hook fails every commit with "ao not found". With the
    jobs gone, the watchdog heartbeats of those projects go too, as `ao watchdog uninstall` takes one, since a
    heartbeat left behind reads as a dead watchdog.

    What stays is named: each project's own state, ~/.ao unless --purge, and the program, with the command
    that removes it. --purge deletes ~/.ao only when nothing was left behind, since its registry is how a
    second run finds the projects whose hooks stayed. Exit 1 names each thing left.
    """
    allow = bool(getattr(args, "allow_shared_hooks", False))
    jobs = _machine_jobs()
    unreadable = _registry_problem()
    projects = _uninstall_projects()
    found = _installation()
    home = os.path.join(A.HOME, ".ao")
    names = [name for project in projects for name in project["names"]]
    beats = [path for path in (os.path.join(home, A.project_file_name("heartbeat", name)) for name in names)
             if os.path.lexists(path)]

    print(f"{C['b']}ao uninstall{C['reset']} {'takes' if args.yes else 'would take'} off this machine:")
    for kind, name in jobs:
        print(f"   {kind} {name}")
    for path in beats:
        print(f"   {_home_relative(path)} (a heartbeat left behind reads as a dead watchdog)")
    if unreadable:
        print(f"   {C['red']}{unreadable}{C['reset']}")
    for project in projects:
        root = project["root"]
        empty = not any(project[what] for what in ("problem", "hooks", "mcp", "stays", "unread"))
        print(f"   {C['b']}{', '.join(project['names'])}{C['reset']}  {root}"
              + (f"  {C['dim']}gone from disk: nothing of it to take{C['reset']}" if project["gone"]
                 else f"  {C['dim']}nothing of ao's to take{C['reset']}" if empty else ""))
        if project["problem"]:
            print(f"      {C['red']}{project['problem']}{C['reset']}")
        for target in project["hooks"]:
            needs = target["needs_authorization"] and not allow
            print(f"      hook {target['role']} — {target['path']}"
                  + (f"  {C['yellow']}needs --allow-shared-hooks{C['reset']}" if needs else ""))
        for path, _, _ in project["mcp"]:
            print(f"      the `ao` server entry in {os.path.relpath(path, root)} (other entries stay)")
        for target in project["stays"]:
            print(f"      {C['dim']}stays: {target['static_state']} {target['role']} — {target['path']} "
                  f"({target['track_state']}, {target['reachability']}){C['reset']}")
        for path, why in project["unread"]:
            print(f"      {C['red']}cannot read {os.path.relpath(path, root)}{C['reset']}: {why}")
    if args.purge and os.path.lexists(home):
        print(f"   {_home_relative(home)}: every file ao keeps on this machine — settings, channel credentials, "
              "logs, archived reviews and the project registry")
    print("it leaves:")
    for line in _uninstall_leaves(args, found):
        print(f"   {C['dim']}{line}{C['reset']}")
    if not args.yes:
        print(f"\nre-run with {C['b']}--yes{C['reset']} to do it")
        return 0

    # The jobs first, in this process, each checked gone: one left behind keeps running against the rest.
    left = []
    for kind, name in jobs:
        problem = _unschedule(name)
        if problem:
            left.append(name)
            print(f"{C['red']}left{C['reset']} {kind} {name}: {problem}")
        else:
            print(f"removed {kind} {name}")
    if left and beats:
        print(f"kept {len(beats)} heartbeat(s): a job left behind still writes one")
    for path in beats if not left else []:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            left.append(path)
            print(f"{C['red']}left{C['reset']} {_home_relative(path)}: {exc}")
    if unreadable:
        left.append(A.project_registry_path())
        print(f"{C['red']}left{C['reset']} the hooks and MCP entries of every project: {unreadable}")
    for project in projects:
        root = project["root"]
        if project["problem"]:
            left.append(root)
            print(f"{C['red']}left{C['reset']} the hooks of {root}: {project['problem']}")
        elif project["inv"] is not None and (project["hooks"] or project["stays"]):
            if _hooks_uninstall(project["inv"], allow):
                left.append(root)
                print(f"{C['red']}left{C['reset']} hooks of {root}, each named above")
        for path, key, remove_when_empty in project["mcp"]:
            shown = os.path.relpath(path, root)
            try:
                gone = _drop_mcp_entry(path, key, remove_when_empty)
            except (OSError, ValueError) as exc:
                left.append(path)
                print(f"{C['red']}left{C['reset']} the `ao` entry in {path}: {' '.join(str(exc).split())}")
                continue
            print(f"removed {shown} of {root}, which held only ao's entry" if gone == "file"
                  else f"removed the `ao` entry from {shown} of {root}")
        for path, why in project["unread"]:
            left.append(path)
            print(f"{C['red']}left{C['reset']} {path}: it cannot be read ({why}), so any `ao` entry in it stays")
    if args.purge and os.path.lexists(home):
        if left:
            print(f"{C['yellow']}kept{C['reset']} {_home_relative(home)}: {len(left)} thing(s) were left, and its "
                  "registry is how the next `ao uninstall` finds them")
        else:
            try:
                shutil.rmtree(home)
                print(f"removed {_home_relative(home)}")
            except OSError as exc:
                left.append(home)
                print(f"{C['red']}left{C['reset']} {_home_relative(home)}: {exc}")
    if left:
        print(f"{C['red']}not complete{C['reset']}: {len(left)} thing(s) left, each named above")
        return 1
    print(f"{C['green']}uninstalled{C['reset']}: ao's jobs, hooks and MCP entries are off this machine; "
          f"the program itself stays — {_program_removal(found)}")
    return 0
