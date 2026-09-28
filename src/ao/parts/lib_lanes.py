"""Lanes: one git worktree per board item, so parallel work never shares a checkout (LANE-START).

A part of src/ao/lib.py, run in its namespace by `_part` as the parts split out of it are; it is
not importable on its own.
"""


# ---- a lane: one worktree per READY board item (LANE-START) -------------------------------

LANE_BRANCH = "lane/"
LANE_SETTINGS = ("lane.link_paths", "lane.env", "lane.env_file", "lane.post_create", "lane.post_create_timeout")
# Above the lines ao adds to the repository's exclude file, so whoever reads that file knows why they are there.
LANE_EXCLUDE_HEADING = "# ao lane start: links and files it puts in a lane, never to be committed (LANE-START)"
# How long a lane's stopped post-create command may take to be gone.
LANE_STOP_SECONDS = 5
_LANE_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class LaneRefused(Exception):
    """What ao will not do with a lane, as the sentence a person reads; nothing was made or removed."""


def lane_name(item):
    """The name a board item's lane goes by: its directory, its branch and its record in .ao/lanes/.

    A board id may hold what no directory or branch name can - `ACME-187/1` - so each run of
    other characters becomes one hyphen. Two ids that come to one name would share a lane,
    and the second is refused as a lane that already exists.
    """
    return _LANE_UNSAFE.sub("-", str(item or "")).strip(".-")


def _real_path(path):
    return os.path.normcase(os.path.realpath(path))


def _lanes_beside(trees):
    """(the main checkout, the directory its lanes are made in), from git's list of worktrees.

    Beside the main checkout, not the checkout asking: a linked worktree often lives inside the
    main one, and a lane made beside it would sit in the main checkout's tree, where that
    checkout's `git status`, tests and tools would read the lane's files as their own.
    """
    main = os.path.normpath(trees[0]["path"])
    return main, os.path.join(os.path.dirname(main), os.path.basename(main) + "-lanes")


def lane_home(root):
    """(the main checkout, the directory its lanes are made in): `<main checkout>-lanes`, beside it."""
    trees = worktree_list(root)
    if not trees:
        raise LaneRefused("git lists no worktree here, and a lane is one")
    return _lanes_beside(trees)


def lane_record_path(root, name):
    return os.path.join(root, ".ao", "lanes", f"{name}.json")


def lane_log_path(root, name):
    return os.path.join(root, ".ao", "lanes", f"{name}.log")


def lane_records(root):
    """Every lane this checkout started, by name, each with `name`; one that cannot be read says so."""
    directory = os.path.join(root, ".ao", "lanes")
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return []
    out = []
    for entry in entries:
        # A record is replaced through a hidden temporary file beside it; that file is no lane.
        if entry.startswith(".") or not entry.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, entry), encoding=UTF8) as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            record = None
        out.append(dict(record if isinstance(record, dict) else {"state": "unreadable"}, name=entry[:-5]))
    return out


def lane_record(root, item):
    """The lane an item, or a lane's own name, has in this checkout, or None."""
    name = lane_name(item)
    return next((record for record in lane_records(root) if record["name"] == name), None)


def lane_not_ready(root, item):
    """Why a board item may not start now, as a sentence; None when it is READY (#33).

    READY is board_graph's alone. This only says which of its reasons holds, so a refusal
    names what to do: land a dependency, answer who it waits on, or mend an edge.
    """
    graph = board_graph(root)
    if any(entry["id"] == item for entry in graph["ready"]):
        return None
    b = board(root)
    state, entry = next(((column, row) for column in BOARD_STATES for row in b[column] if row["id"] == item),
                        (None, None))
    if state is None:
        return f"{item} is not on the board"
    if state != "queued":
        return f"{item} is {state} on the board, and only a queued item can be READY"
    broken = [problem for problem in graph["problems"]
              if re.search(rf"(?<![\w-]){re.escape(item)}(?![\w-])", problem)]
    if broken:
        return f"{item} stands on a broken edge of the board: {'; '.join(broken)}"
    if "waiting" in entry["notes"]:
        return f"{item} is waiting on {entry['notes']['waiting'] or 'someone'}"
    landed = {row["id"] for column in ("done", "verified") for row in b[column]}
    needs = set(_edge_ids(entry, "needs")) | {row["id"] for column in BOARD_STATES for row in b[column]
                                              if item in _edge_ids(row, "unlocks")}
    unmet = sorted(needs - landed)
    if unmet:
        return f"{item} needs {', '.join(unmet)}, which {'has' if len(unmet) == 1 else 'have'} not landed"
    return f"{item} is not READY"


def _lane_relative(named):
    """A path inside a checkout as a setting names it, with forward slashes, or None.

    Relative, never climbing out with `..`, never into `.git`: a lane is prepared inside
    itself, from inside the main checkout, and nowhere else. No control character either: the
    path becomes one line of git's exclude file (_lane_ignore).
    """
    text = str(named or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text) or any(ord(ch) < 32 for ch in text):
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or ".." in parts or parts[0] == ".git":
        return None
    return "/".join(parts)


def lane_settings(cfg):
    """The lane.* settings a start prepares a lane with, by their last word; LaneRefused names any ao cannot use.

    settings.get passes a value it cannot use over for the default, and the default of a list
    is nothing: `"make setup && npm ci"` as lane.post_create - one shell string where a list of
    words belongs - ran nothing, and the lane was called ready unprepared. So a start reads
    each setting's problem as well, and every value is checked here, before anything is made.
    """
    wanted, problems = {}, []
    for key in LANE_SETTINGS:
        value, _, problem = settings.resolve(cfg, key)
        wanted[key.split(".", 1)[1]] = value
        if problem:
            problems.append(problem)
    problems += [f"lane.link_paths names {named!r}, which is not a path inside a checkout"
                 for named in wanted["link_paths"] if _lane_relative(named) is None]
    for entry in wanted["env"]:
        key, equals, value = entry.partition("=")
        if not equals or not _ENV_NAME.fullmatch(key) or any(ch in value for ch in "\r\n\0"):
            problems.append(f"lane.env holds {entry!r}, which is not one NAME=value line")
    if wanted["env"] and _lane_relative(wanted["env_file"]) is None:
        problems.append(f"lane.env_file is {wanted['env_file']!r}, which names no file in the lane")
    if problems:
        raise LaneRefused("; ".join(problems) + "; a lane is prepared as its settings say or not started, and "
                          "`ao config list` says what each one takes")
    return wanted


def _lanes_directory(root):
    """.ao/lanes/, made on first use with a .gitignore of its own that ignores all of it.

    A record names directories on this machine, and a log holds whatever a post-create command
    printed, a DATABASE_URL among it. A project set up before lanes existed has no line for
    them in its own .gitignore, and `git add -A` there would commit both.
    """
    directory = os.path.join(root, ".ao", "lanes")
    os.makedirs(directory, exist_ok=True)
    ignore = os.path.join(directory, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding=UTF8, newline="\n") as fh:
            fh.write("# what ao lane start records here names this machine's directories (LANE-START)\n*\n")
    return directory


def _write_lane(root, name, record):
    from .storage import replace_file_durably
    replace_file_durably(lane_record_path(root, name),
                         (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode(UTF8))


def _forget_lane(root, name):
    for path in (lane_record_path(root, name), lane_log_path(root, name)):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _process_of(pid):
    """A process as a lane's record names it: its pid, and when it started, so a pid handed on is not taken for it."""
    try:
        start = _process_start(pid, refresh=True)
    except Exception:
        start = None
    return {"pid": pid, "start": start}


def _ao_alive(process):
    """Whether the ao a record names as preparing its lane still runs as that process."""
    pid = (process or {}).get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not _pid_alive(pid):
        return False
    started, now = process.get("start"), _process_start(pid)
    return started is None or now is None or started == now


def _command_state(command):
    """Whether a lane's post-create command, or what it started, still runs: "running", "unknown" or "gone".

    The command leads a process group of its own, and what it started stays in that group
    after it exits. It is "running" only while its own process answers and started when the
    record says: then it is the command, and stopping its group is stopping the command. A
    group that answers without that process may be what the command left behind or a stranger's
    that took its number, and a process that answers with no start to compare may be either:
    "unknown", which a remove refuses on and never stops.
    """
    pid = (command or {}).get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "gone"
    if _pid_alive(pid):
        started, now = command.get("start"), _process_start(pid)
        if started is None or now is None:
            return "unknown"
        return "running" if started == now else "gone"
    if os.name == "nt":
        return "gone"
    try:
        os.killpg(pid, 0)
    except OSError:
        return "gone"
    return "unknown"


def lane_holder(lane):
    """Who is still preparing a lane, as a sentence, or None when nothing is.

    The ao that started it while it runs, or the post-create command that ao started, which
    runs on once that ao is killed outright - nothing can catch that - with nobody holding it to
    lane.post_create_timeout.
    """
    if lane.get("state") != "preparing":
        return None
    process, command = lane.get("process") or {}, lane.get("command") or {}
    if _ao_alive(process):
        return f"ao, process {process['pid']}, is preparing it"
    state = _command_state(command)
    if state == "running":
        return (f"its post-create command, process {command['pid']}, runs on with no ao waiting on it; "
                f"`ao lane remove {lane.get('item') or lane['name']}` stops it")
    if state == "unknown":
        return f"process group {command['pid']} may still run its post-create command"
    return None


def _settle_lane(root, name, record, problem):
    """Record how a start ended - ready, or failed and why - and return the lane with its name."""
    for key in ("process", "command"):
        record.pop(key, None)
    record["state"] = "failed" if problem else "ready"
    if problem:
        record["problem"] = problem
    _write_lane(root, name, record)
    return dict(record, name=name)


def _drop_lane_branch(root, branch, base):
    """Delete the branch a start made when git then could not make its worktree; None, or what stays and why.

    `git worktree add -b` makes the branch before the checkout, and when the checkout fails - a
    required filter such as a missing git-lfs - git removes the worktree and keeps the branch.
    The start found no such branch and made it at base, so while it stands there it holds
    nothing of anyone's; left, it would refuse every later start of the item.
    """
    head = git_text(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if not head:
        return None
    if head != base:
        return f"the branch {branch} stays: it has moved to {head[:10]} since this start made it"
    for attempt in range(2):
        try:
            _git_output(root, "branch", "-D", branch)
            return None
        except RuntimeError as exc:
            if attempt:
                return f"the branch {branch} stays ({exc}); `git branch -D {branch}` removes it"
        # A worktree git gave up on may still be listed, and git keeps a listed worktree's branch.
        git_text(root, "worktree", "prune")


def start_lane(root, cfg, item, now=None):
    """Start a READY board item's lane: a worktree of its own on a branch of its own, prepared from the settings.

    Everything that can refuse is asked before anything is made, the settings among it, and the
    record in .ao/lanes/ is created exclusively, so two starts of one item cannot both make it.
    The worktree is `<main checkout>-lanes/<name>` on the new branch `lane/<name>`, from this
    checkout's HEAD, and prepare_lane prepares it. Returns the record: `ready`, or `failed` with
    its problem and the worktree left where it is for a person to inspect. Raises LaneRefused
    for a lane ao will not start, and RuntimeError when git could not add it; then neither the
    record nor the branch is left. Stopped while it works - Ctrl-C, SIGTERM, SIGHUP - it records
    the lane failed, or leaves nothing where git had not made the worktree yet, and stops too.
    """
    name = lane_name(item)
    if not name:
        raise LaneRefused(f"{item!r} cannot name a lane: it holds no letter or digit")
    held = lane_record(root, item)
    if held is not None:
        raise LaneRefused(f"{item} already has a lane, {held.get('state') or 'unreadable'}, at "
                          f"{held.get('path') or 'an unrecorded path'}; `ao lane remove {item}` retires it")
    why = lane_not_ready(root, item)
    if why:
        raise LaneRefused(f"{why}; a lane starts only for a READY item, and `ao board ready` lists them")
    wanted = lane_settings(cfg)
    branch = LANE_BRANCH + name
    if subprocess.run([git_binary(), "check-ref-format", "--branch", branch], cwd=root,
                      capture_output=True).returncode:
        raise LaneRefused(f"{item} cannot name a branch: git refuses {branch}")
    try:
        trees = worktree_list(root)
    except RuntimeError as exc:
        raise LaneRefused(f"a lane is a git worktree, and this checkout's worktrees cannot be read ({exc})") from None
    if not trees:
        raise LaneRefused("git lists no worktree here, and a lane is one")
    main, home = _lanes_beside(trees)
    path = os.path.join(home, name)
    taken = next((tree for tree in trees if _real_path(tree["path"]) == _real_path(path)), None)
    if taken:
        raise LaneRefused(f"{path} is already a worktree of this repository, on {taken['branch'] or 'no branch'}: "
                          "a lane another checkout started, or one made by hand, and `ao lane list` in the "
                          "checkout that started it shows it")
    inside = next((tree["path"] for tree in trees if _within(path, tree["path"])), None)
    if inside:
        raise LaneRefused(f"{path} would lie inside the worktree {inside}, and a lane never nests in another")
    if os.path.lexists(path):
        raise LaneRefused(f"{path} already exists, and a lane is never made over what is there")
    if git_text(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        raise LaneRefused(f"the branch {branch} already exists, and a lane starts on a branch of its own")
    base = git_text(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if not base:
        raise LaneRefused("this checkout has no commit to start a lane from")
    record = {"item": item, "branch": branch, "path": path, "base": base,
              "started_at": int(time.time() if now is None else now), "state": "preparing",
              "process": _process_of(os.getpid()), "prepared": [], "steps": []}
    _lanes_directory(root)
    try:
        with open(lane_record_path(root, name), "x", encoding=UTF8) as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False)
    except FileExistsError:
        raise LaneRefused(f"{item} already has a lane; `ao lane list` shows it") from None
    try:
        made = subprocess.run([git_binary(), "worktree", "add", "-b", branch, path, base], cwd=root,
                              capture_output=True)
    except BaseException:
        # Git removes a worktree it could not finish when it hears the interrupt too.
        if os.path.isdir(path):
            _settle_lane(root, name, record, "ao was stopped while git added the worktree")
        else:
            _drop_lane_branch(root, branch, base)
            _forget_lane(root, name)
        raise
    said = "; ".join(line.strip() for line in made.stderr.decode(UTF8, "replace").splitlines() if line.strip()) \
        or f"exit {made.returncode}"
    if made.returncode and not os.path.isdir(path):
        left = _drop_lane_branch(root, branch, base)
        _forget_lane(root, name)
        raise RuntimeError(f"git could not add the worktree: {said}" + (f"; {left}" if left else ""))
    if made.returncode:
        # A post-checkout hook fails after git made the worktree: it stands, and it is no lane ready.
        return _settle_lane(root, name, record, f"git worktree add failed after making the worktree: {said}")
    try:
        problem = prepare_lane(root, name, record, main, wanted)
    except KeyboardInterrupt:
        _settle_lane(root, name, record, "ao was stopped before the lane was prepared"
                     + ("; its post-create command was stopped with it" if record.get("command") else ""))
        raise
    except Exception as exc:
        # A lane stuck at `preparing` would read as another start still running: say it failed.
        problem = f"preparation stopped: {type(exc).__name__}: {exc}"
    return _settle_lane(root, name, record, problem)


def prepare_lane(root, name, record, main, wanted):
    """Prepare a new lane as its settings say (lane_settings), stopping at the first step that fails.

    `lane.link_paths` are linked from the main checkout, `lane.env` is written into
    `lane.env_file` as NAME=value lines, and `lane.post_create` runs in the lane with those
    variables. `{item}`, `{lane}` and `{path}` in a value or an argument stand for the lane's
    own. Nothing is put over what the lane already has: a link over a path its branch tracks
    would hide it, and a file written through a link would change the main checkout. Git ignores
    all ao puts there (_lane_ignore). Each path ao put in the lane goes into record["prepared"]
    and each step into record["steps"] as it is done, so a start that is stopped still names
    them. Returns None, or what failed.
    """
    words = {"{item}": record["item"], "{lane}": name, "{path}": record["path"]}

    def fill(text):
        for word, value in words.items():
            text = text.replace(word, value)
        return text

    prepared, steps = record["prepared"], record["steps"]
    for rel in (_lane_relative(named) for named in wanted["link_paths"]):
        if rel in prepared:
            continue                                    # named twice: linked once
        source, target = os.path.join(main, *rel.split("/")), os.path.join(record["path"], *rel.split("/"))
        if not os.path.lexists(source):
            return f"lane.link_paths names {rel}, which the main checkout {main} does not have"
        if os.path.lexists(target):
            return f"lane.link_paths names {rel}, which the lane's branch already has, and a link would hide it"
        problem = _lane_ignore(record["path"], rel)
        if problem:
            return problem
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.symlink(source, target, target_is_directory=os.path.isdir(source))
        except OSError as exc:
            return f"{rel} could not be linked from the main checkout ({exc.strerror or exc})"
        prepared.append(rel)
        steps.append(f"linked {rel}")
    variables = {}
    for entry in wanted["env"]:
        key, _, value = entry.partition("=")
        value = fill(value)
        if any(ch in value for ch in "\r\n\0"):
            return f"lane.env holds {entry!r}, which with this lane's own words is more than one line"
        variables[key] = value
    if variables:
        rel = _lane_relative(wanted["env_file"])
        target = os.path.join(record["path"], rel)
        if os.path.lexists(target):
            return f"the lane already has {rel}, and lane.env is never written over a file"
        problem = _lane_ignore(record["path"], rel)
        if problem:
            return problem
        try:
            # "x" creates or fails, and never follows a link that stands at the name.
            with open(target, "x", encoding=UTF8, newline="\n") as fh:
                fh.write("".join(f"{key}={value}\n" for key, value in variables.items()))
        except FileExistsError:
            return f"the lane already has {rel}, and lane.env is never written over a file"
        except OSError as exc:
            return f"lane.env could not be written to {rel} ({exc.strerror or exc})"
        prepared.append(rel)
        steps.append(f"wrote {len(variables)} variable(s) to {rel}")
    argv = [fill(word) for word in wanted["post_create"]]
    if argv:
        problem = _lane_post_create(root, name, record, argv, variables, wanted["post_create_timeout"])
        if problem:
            return problem
        steps.append(f"ran {' '.join(argv)}")
    return None


def _exclude_line(rel):
    """The line of an exclude file that matches rel, and nothing else, from the top of a checkout."""
    line = "/" + re.sub(r"([\\*?\[])", r"\\\1", rel)
    kept = line.rstrip(" ")
    return kept + "\\ " * (len(line) - len(kept))      # a trailing space is kept only escaped


def _lane_ignore(path, rel):
    """Make git ignore rel in a lane before ao puts a link or a file there; None, or why it cannot.

    What ao puts in a lane is untracked, and `git add -A` there commits whatever git does not
    ignore: a link whose target is the main checkout on this machine, or a file holding the
    lane's port and secrets. The usual `node_modules/` ignores a directory, never a link at
    that name. So where git would not ignore rel, a line naming exactly it goes into the
    repository's own exclude file - read by every worktree of the repository, committed by
    none - and the main checkout ignores that name too, as it should. Asked before rel exists,
    git answers as it will for a link or a file there: a pattern for directories matches neither.
    """
    def ignored():
        return subprocess.run([git_binary(), "check-ignore", "-q", "--", rel], cwd=path, capture_output=True)

    if ignored().returncode == 0:
        return None
    exclude = git_text(path, "rev-parse", "--git-path", "info/exclude")
    if not exclude:
        return f"git cannot name the repository's exclude file, and {rel} would be committed from the lane"
    exclude = os.path.join(path, exclude)
    line = _exclude_line(rel)
    try:
        try:
            with open(exclude, encoding=UTF8, errors="replace") as fh:
                text = fh.read()
        except FileNotFoundError:
            text = ""
        lines = text.split("\n")
        if line not in lines:
            os.makedirs(os.path.dirname(exclude), exist_ok=True)
            with open(exclude, "a", encoding=UTF8, newline="\n") as fh:
                fh.write(("" if not text or text.endswith("\n") else "\n")
                         + ("" if LANE_EXCLUDE_HEADING in lines else LANE_EXCLUDE_HEADING + "\n") + line + "\n")
    except OSError as exc:
        return f"{rel} could not be added to git's exclude file {exclude} ({exc.strerror or exc})"
    asked = ignored()
    if asked.returncode == 0:
        return None
    why = ("a .gitignore of the branch names it again with `!`" if asked.returncode == 1 else
           " ".join(asked.stderr.decode(UTF8, "replace").split()) or f"git check-ignore exited {asked.returncode}")
    return (f"git would not ignore {rel} in the lane even with {line} in {exclude}, and a commit there would "
            f"carry it: {why}")


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def _hear_stop_signals():
    """Make SIGTERM and SIGHUP interrupt ao as Ctrl-C does, while a lane's post-create command runs.

    Returns what _restore_signals puts back. The command leads a process group of its own, so no
    signal the terminal sends reaches it, and by default either signal ends ao on the spot and
    leaves the command running in a lane nobody prepares. As an interrupt, it stops the command
    first. A signal ao was set to ignore, as nohup ignores SIGHUP, stays ignored, and only the
    main thread may set a handler at all.
    """
    import signal
    import threading
    saved = {}
    if threading.current_thread() is not threading.main_thread():
        return saved
    for signame in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, signame, None)
        if signum is not None and signal.getsignal(signum) is signal.SIG_DFL:
            saved[signum] = signal.signal(signum, _raise_interrupt)
    return saved


def _restore_signals(saved):
    import signal
    for signum, handler in saved.items():
        signal.signal(signum, handler)


def _kill_lane_group(pid):
    """Stop a lane's post-create command and everything it started: its process group, or on Windows its tree."""
    import signal
    if os.name == "nt":
        kill_turn(pid, signal.SIGTERM)                 # taskkill /T /F, whatever the signal
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _lane_post_create(root, name, record, argv, variables, timeout):
    """Run a new lane's post-create command in the lane; None when it exited 0, else what went wrong.

    Never through a shell: the setting is a list of words, and no word is read as shell text.
    A program named bare is looked for where ao looks for any (runnable_binary); one named with
    a path is taken from the lane. It reads nothing, it leads its own process group so a
    timeout stops whatever it started, and what it prints goes to .ao/lanes/<name>.log. The
    record names it while it runs, so a list or a remove still finds it should ao be killed
    outright, and a Ctrl-C, SIGTERM or SIGHUP stops it before ao stops.
    """
    program, command = argv[0], " ".join(argv)
    if os.path.isabs(program):
        found = program
    elif "/" in program or os.sep in program:
        found = os.path.join(record["path"], program)
    else:
        found = runnable_binary(program)
    if not found or not os.path.isfile(found):
        return f"lane.post_create runs {program}, which is not a program ao can find"
    log = f".ao/lanes/{name}.log"
    group = ({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt"
             else {"start_new_session": True})
    saved = _hear_stop_signals()
    try:
        with open(lane_log_path(root, name), "wb") as out:
            try:
                proc = subprocess.Popen([found, *argv[1:]], cwd=record["path"], env=dict(os.environ, **variables),
                                        stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **group)
            except OSError as exc:
                return f"lane.post_create `{command}` could not be started ({exc.strerror or exc})"
            try:
                record["command"] = _process_of(proc.pid)
                _write_lane(root, name, record)
                code = proc.wait(timeout=timeout)
            except BaseException as exc:
                # Its group is its pid, and no other process can hold that pid until this one is reaped.
                _kill_lane_group(proc.pid)
                try:
                    proc.wait(timeout=LANE_STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
                if not isinstance(exc, subprocess.TimeoutExpired):
                    raise
                return (f"lane.post_create `{command}` ran past {timeout} seconds and was stopped; "
                        f"its output is in {log}")
    finally:
        _restore_signals(saved)
    if code:
        return f"lane.post_create `{command}` exited {code}; its output is in {log}"
    return None


def lane_changes(path, lane):
    """What a lane's worktree holds that nobody committed, as `git status` names it, less what ao put there.

    Every untracked file is named, not its directory, so a link ao made inside a directory it
    made is still told from a file someone wrote beside it. What ao put there is passed over
    only while it is untracked, as ao made it: once it is staged or tracked, it is a change.
    Git ignores all of it (_lane_ignore); this holds should the exclude file lose its lines.
    """
    raw = _git_output(path, "status", "--porcelain", "-z", "--untracked-files=all")
    ours = set(lane.get("prepared") or [])
    fields, out, index = os.fsdecode(raw).split("\0"), [], 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        status, name = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            index += 1                                  # the name it had before follows
        if not (status == "??" and name in ours):
            out.append(f"{status.strip()} {name}")
    return out


def lane_rows(root):
    """Every lane this checkout started, with where its item stands on the board and what its worktree holds."""
    b = board(root)
    where = {entry["id"]: state for state in BOARD_STATES for entry in b[state]}
    lanes = lane_records(root)
    try:
        home = lane_home(root)[1] if lanes else None
    except (LaneRefused, RuntimeError):
        home = None
    rows = []
    for lane in lanes:
        state = lane.get("state") or "unreadable"
        held = lane_holder(lane)
        if state == "preparing" and not held:
            state = "interrupted"                       # its start ended before it could say how
        # A record that cannot be read still names its lane by its file: the lane is where ao makes it.
        path = lane.get("path") or (os.path.join(home, lane["name"]) if home else None)
        exists = bool(path) and os.path.isdir(path)
        try:
            changes = lane_changes(path, lane) if exists else []
        except RuntimeError:
            changes = None
        rows.append(dict(lane, state=state, held=held, path=path, exists=exists, changes=changes,
                         board=where.get(lane.get("item"))))
    return rows


def lane_keeps(root, trees):
    """{real path: why it stays} for each worktree that is a lane: `ao worktrees` never retires one.

    A lane starts at its base, so it reads as merged the moment it starts, and pruning it took
    the worktree an agent had just been given. Each lane this checkout started is known by its
    record; one another checkout started, by where it stands and the branch it is on.
    """
    out = {}
    for lane in lane_records(root):
        item = lane.get("item") or lane["name"]
        if lane.get("path"):
            out[_real_path(lane["path"])] = f"the lane of {item}: `ao lane remove {item}` retires it"
    if trees:
        home = _real_path(_lanes_beside(trees)[1])
        for tree in trees[1:]:
            real = _real_path(tree["path"])
            # The name as git gives the path: the compared path is case-folded on Windows, and a lane's
            # branch keeps the case of its item, so lane/B was no lane there (WINDOWS-LANE-4).
            name = os.path.basename(os.path.normpath(tree["path"]))
            if (real not in out and os.path.dirname(real) == home
                    and tree["branch"] == LANE_BRANCH + name):
                out[real] = "a lane another checkout started: `ao lane remove` there retires it"
    return out


def remove_lane(root, item, now=None):
    """Retire an item's lane as `ao worktrees prune` retires a worktree (#42), refusing while it holds work.

    Refused while its worktree holds changes nobody committed - other than the links and the
    file ao put there to prepare it - or a review in flight, or while the ao that started it is
    still preparing it. A post-create command that ao left running when it was killed is
    stopped first, once it is certain to be that command. Then ao's own links go, so no removal
    can reach through one into the main checkout, and prune_worktree archives the lane's .ao/
    state and its branch tip and removes the worktree and the branch. Returns the steps done.

    The record is a file under .ao/, which the agents ao governs can write, so it is not taken
    at its word: ao removes only the path where it makes this lane, and only when git lists it
    as a worktree of this repository, and the branch it removes is always `lane/<name>`.
    """
    lane = lane_record(root, item)
    if lane is None:
        raise LaneRefused(f"{item} has no lane here; `ao lane list` shows the lanes this checkout started")
    name = lane["name"]
    preparing = lane.get("state") == "preparing"
    process, command = lane.get("process") or {}, lane.get("command") or {}
    if preparing and process.get("pid") != os.getpid() and _ao_alive(process):
        raise LaneRefused(f"lane {name} is still being prepared by ao, process {process['pid']}")
    try:
        home = lane_home(root)[1]
        trees = worktree_list(root)
    except RuntimeError as exc:
        raise LaneRefused(f"this checkout's worktrees cannot be read ({exc}), so lane {name} stays") from None
    path = os.path.join(home, name)
    if lane.get("path") and _real_path(lane["path"]) != _real_path(path):
        raise LaneRefused(f"the record of lane {name} names {lane['path']}, and ao makes it at {path}; it stays")
    if os.path.lexists(path) and not any(_real_path(tree["path"]) == _real_path(path) for tree in trees[1:]):
        raise LaneRefused(f"{path} is not a worktree of this repository, and ao removes only a lane it made")
    steps = []
    state = _command_state(command) if preparing else "gone"
    if state == "unknown":
        raise LaneRefused(f"process group {command['pid']} may still run lane {name}'s post-create command, and "
                          "ao cannot tell that it is the one it started; stop that group, then remove the lane")
    if state == "running":
        _kill_lane_group(command["pid"])
        deadline = time.monotonic() + LANE_STOP_SECONDS
        while _command_state(command) != "gone" and time.monotonic() < deadline:
            time.sleep(0.1)
        if _command_state(command) != "gone":
            raise LaneRefused(f"lane {name}'s post-create command, process {command['pid']}, did not stop")
        steps.append(f"stop the post-create command ao left running (process {command['pid']})")
    if os.path.lexists(path):
        try:
            changes = lane_changes(path, lane)
        except RuntimeError as exc:
            raise LaneRefused(f"what lane {name} holds at {path} cannot be read ({exc}), so it stays") from None
        if changes:
            shown = ", ".join(changes[:5]) + (f" and {len(changes) - 5} more" if len(changes) > 5 else "")
            raise LaneRefused(f"lane {name} holds {len(changes)} uncommitted change(s) at {path}: {shown}; "
                              "commit or discard them, then remove it")
        flying = reviews_in_flight(path)
        if flying:
            raise LaneRefused(f"lane {name} has a review in flight: {', '.join(flying)}")
        for rel in lane.get("prepared") or []:
            target = os.path.join(path, *str(rel).split("/"))
            if _lane_relative(rel) == rel and os.path.islink(target):
                os.unlink(target)
    branch = LANE_BRANCH + name
    head = git_text(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    steps += prune_worktree(root, {"path": path, "branch": branch if head else None, "head": head},
                            apply=True, now=now)
    _forget_lane(root, name)
    try:
        os.rmdir(home)          # only once it is empty: ao made it for its lanes, and the last one is gone
    except OSError:
        pass
    return steps
