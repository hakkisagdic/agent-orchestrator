"""Alarms and records: alarms, project keys, review scope, helpers, costs, deferrals, waivers,
credits, pings, locks.

A part of src/ao/lib.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


# ---- alarms -------------------------------------------------------------------
#
# Three levels, named by who acts: yellow is the architect's (an anomaly and a
# wake), orange is the human's (desktop + Telegram), red is the human's *now*
# (e-mail). The ladder is what turns a standing orange into a red: on 2026-09-05
# every alert went to a notification centre nobody looked at for eleven hours.

# The defaults, from the settings registry (#74); the functions below read the values in force.
ALARM_RED_AFTER = settings.default("alarms.red_after_minutes") * 60
ALARM_RED_REPEAT = settings.default("alarms.red_repeat_hours") * 3600
ALARM_RESET_AFTER = settings.default("alarms.reset_after_hours") * 3600


def _alarm_reset_after():
    return settings.get(None, "alarms.reset_after_hours") * 3600


def alarms_path():
    return os.path.join(HOME, ".ao", "alarms.json")


def load_alarms():
    try:
        return json.load(open(alarms_path(), encoding=UTF8))
    except (OSError, ValueError):
        return {}


def save_alarms(d):
    try:
        os.makedirs(os.path.dirname(alarms_path()), exist_ok=True)
        json.dump(d, open(alarms_path(), "w", encoding=UTF8), indent=1)
    except OSError:
        pass


def alarm_snoozes_path():
    return os.path.join(HOME, ".ao", "alarm-snoozes.json")


def load_alarm_snoozes():
    try:
        return json.load(open(alarm_snoozes_path(), encoding=UTF8))
    except (OSError, ValueError):
        return {}


def _save_alarm_snoozes(d):
    os.makedirs(os.path.dirname(alarm_snoozes_path()), exist_ok=True)
    with open(alarm_snoozes_path(), "w", encoding=UTF8) as fh:
        json.dump(d, fh, indent=1)


def alarm_snooze(project, key, until, by="human", why=""):
    """Keep one alarm off the human channels until a date; it stays on the record.

    A snooze is for a condition that is real, known and waiting on someone. On
    2026-09-15 the doctor check reported Voltrai's legacy commit hook, whose fix
    only the owner can make and not before 1 October; left alone it would ring red
    and mail every six hours about something nobody could act on yet. The snooze
    names who set it and why, and it ends by itself on the date.
    """
    d = load_alarm_snoozes()
    d[f"{project}:{key}"] = {"until": int(until), "by": by, "why": why, "at": int(time.time())}
    _save_alarm_snoozes(d)
    return d[f"{project}:{key}"]


def alarm_unsnooze(project, key):
    d = load_alarm_snoozes()
    gone = d.pop(f"{project}:{key}", None)
    if gone is not None:
        _save_alarm_snoozes(d)
    return gone


def alarm_snoozed(project, key, now=None):
    """The snooze standing for this alarm, or None; an expired snooze is no snooze."""
    entry = load_alarm_snoozes().get(f"{project}:{key}")
    if isinstance(entry, dict) and float(entry.get("until") or 0) > (now or time.time()):
        return entry
    return None


def alarm_touch(project, key, level, now=None, red_after=ALARM_RED_AFTER, title=None,
                persist=True, quiet_until=None, evidence=None, what=None):
    """Calculate a raise of `key` at `level`; return (level to ring at, episode).

    An orange raised repeatedly for `red_after` seconds rings red. `red_due` on
    the episode says whether a mail should go now (once per ALARM_RED_REPEAT).
    ``persist=False`` runs the identical calculation against the current ledger
    without writing it, so watchdog explain can preview the live verdict safely.

    `what` names what the raise says, when its caller can. The episode keeps what it
    last told (`told_what`); a raise that says something else is `news` on the returned
    episode, and news is mailed at once when the episode is red, past the repeat and
    past a known end: a projection told on Monday does not hold back the day the credits
    actually run out (NOTICE-NOISE).
    """
    now = now or time.time()
    d = load_alarms()
    k = f"{project}:{key}"
    e = d.get(k) or {}
    if e and now - e.get("last", 0) > _alarm_reset_after():
        e = {}
    e.setdefault("first", now)
    e["last"] = now
    e["level"] = level
    e["count"] = e.get("count", 0) + 1
    if title:
        e["title"] = title
    if quiet_until:
        e["quiet_until"] = float(quiet_until)
    if evidence:
        e["evidence"] = evidence      # the ladder shows what the notice was raised on (#37)
    news = what is not None and e.get("told_what") is not None and e["told_what"] != what
    if what is not None and e.get("told_what") is None \
            and any(e.get(field) is not None for field in ("rang_at", "red_sent", "named_at")):
        e["told_what"] = what         # told by a raise that named nothing, a resume notice's: this is what it told
    ring = level
    e["red_due"] = False
    if level == "red" or (level == "orange" and now - e["first"] >= red_after):
        ring = "red"
        # A red a resume notice named is told from then, as a mailed one is from its mail (RESUME-QUIET).
        told = e.get("red_sent") if e.get("red_sent") is not None else e.get("named_at")
        e["red_due"] = told is None or news or now - told >= settings.get(None, "alarms.red_repeat_hours") * 3600
        # A standing red with a known end is mailed once, then held until that end (#40).
        if told is not None and not news and now < float(e.get("quiet_until") or 0):
            e["red_due"] = False
    e["ring"] = ring
    d[k] = e
    if persist:
        save_alarms(d)
    return ring, dict(e, news=news)


def alarm_mailed(project, key, now=None, what=None):
    d = load_alarms()
    k = f"{project}:{key}"
    if k in d:
        d[k]["red_sent"] = now or time.time()
        d[k]["red_due"] = False
        if what is not None:
            d[k]["told_what"] = what
        save_alarms(d)


def alarm_rang(project, key, now=None, what=None):
    """Record that the orange channels told this episode, and what they told (NOTICE-NOISE).

    A condition raised with a `what` rings those channels once for what it says: the
    raises after this record and do not ring again until they say something else, and
    red's mail keeps its own schedule. A resume notice that named an episode rang it too.
    """
    d = load_alarms()
    k = f"{project}:{key}"
    if k in d:
        d[k]["rang_at"] = now or time.time()
        if what is not None:
            d[k]["told_what"] = what
        save_alarms(d)


def alarm_named(project, key, now=None):
    """Record that a resume notice named a red episode instead of mailing it (RESUME-QUIET).

    Its repeat counts from here, as a mailed red's counts from its mail, so the notice that
    has just named it is not followed by a mail about the same thing at the next raise.
    """
    d = load_alarms()
    k = f"{project}:{key}"
    if k in d:
        d[k]["named_at"] = now or time.time()
        d[k]["red_due"] = False
        save_alarms(d)


def active_alarms(project=None, now=None):
    now = now or time.time()
    out = []
    for k, e in load_alarms().items():
        proj, _, key = k.partition(":")
        if project and proj != project:
            continue
        if now - e.get("last", 0) > _alarm_reset_after():
            continue
        out.append(dict(e, project=proj, key=key, age_s=int(now - e.get("first", now))))
    return sorted(out, key=lambda e: -e["age_s"])


# ---- heartbeat ------------------------------------------------------------------

# ---- one name for what a project keeps outside its tree (#66) -----------------------

PROJECT_KEY_FILE = "project-key"


def project_registry_path():
    """Which path holds which project name on this machine."""
    return os.environ.get("AO_PROJECT_REGISTRY") or os.path.join(HOME, ".ao", "projects.json")


def project_registry():
    try:
        with open(project_registry_path(), encoding=UTF8) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: row for name, row in data.items() if isinstance(row, dict) and row.get("root")}


def _usable_key(name):
    return (isinstance(name, str) and 0 < len(name) <= 200 and name not in (".", "..")
            and not any(ch in name for ch in "/\\\0\n\r"))


def project_key(root):
    """The name a project's files outside its tree are kept under (#66).

    Push windows, watchdog state and logs, heartbeats, locks, the reviewer and
    helper records and alarms were named by the directory's basename, so two
    checkouts called `api` shared them: a push allowed in one opened the other,
    and one project's watchdog state overwrote the other's.

    A project's name belongs to its resolved path in the machine registry. A new
    project takes its basename - the name its files already had, so an existing
    install keeps them - unless a path that is still an ao project holds that
    name, compared without case because launchd labels are lower-cased; then it
    takes the basename and eight hex digits of its resolved path. The name is
    also written to `.ao/project-key` with the path, so a lost registry gives
    each project its own name back and a copied `.ao/` does not claim another's.
    A directory with no `.ao/` is not an ao project: it gets its basename and
    nothing is written.
    """
    base = os.path.basename(os.path.abspath(root).rstrip("/\\")) or "root"
    real = os.path.realpath(root)
    if not os.path.isdir(os.path.join(real, ".ao")):
        return base
    mine = next((name for name, row in project_registry().items() if row.get("root") == real), None)
    if mine:
        return mine
    from .storage import _exclusive_lock, replace_file_durably
    registry = project_registry_path()
    recorded = os.path.join(real, ".ao", PROJECT_KEY_FILE)
    try:
        with _exclusive_lock(registry + ".lock"):
            known = project_registry()
            mine = next((name for name, row in known.items() if row.get("root") == real), None)
            if mine is None:
                held = {name.lower() for name, row in known.items()
                        if row.get("root") != real and os.path.isdir(os.path.join(row["root"], ".ao"))}
                try:
                    with open(recorded, encoding=UTF8) as fh:
                        mark = json.load(fh)
                except (OSError, ValueError):
                    mark = {}
                wanted = mark.get("key") if isinstance(mark, dict) and mark.get("root") == real else None
                if _usable_key(wanted) and wanted.lower() not in held:
                    mine = wanted
                elif base.lower() not in held:
                    mine = base
                else:
                    mine = f"{base}-{hashlib.sha256(real.encode('utf-8')).hexdigest()[:8]}"
                known = {name: row for name, row in known.items() if name.lower() != mine.lower()}
                known[mine] = {"root": real, "at": int(time.time())}
                replace_file_durably(registry, json.dumps(known, indent=1, sort_keys=True).encode("utf-8"))
            replace_file_durably(recorded, json.dumps({"key": mine, "root": real}).encode("utf-8"))
    except OSError:
        return mine or base
    return mine


def project_key_collisions():
    """Registered projects that share a directory name, and the name each is kept under."""
    groups = {}
    for name, row in sorted(project_registry().items()):
        base = os.path.basename(str(row["root"]).rstrip("/\\")).lower()
        groups.setdefault(base, []).append((name, row["root"]))
    return {base: rows for base, rows in groups.items() if len(rows) > 1}


def heartbeat_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"heartbeat-{key}")


def heartbeat(root):
    """The watchdog touching this each cycle is the only proof it is alive."""
    try:
        p = heartbeat_path(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding=UTF8) as fh:
            fh.write(str(int(time.time())))
    except OSError:
        pass


def heartbeat_age(root):
    try:
        return int(time.time() - os.path.getmtime(heartbeat_path(root)))
    except OSError:
        return None


def expire_alarms(project, now=None, quiet_for=None):
    """Episodes that went quiet are over; return them once and forget them.

    `quiet_for` is a shorter quiet that ends one: a resume closes every episode its silence
    carried, which nothing watched, instead of letting them age on (RESUME-QUIET).
    """
    now = now or time.time()
    quiet = _alarm_reset_after() if quiet_for is None else quiet_for
    d = load_alarms()
    done = []
    for k in list(d):
        proj, _, key = k.partition(":")
        e = d[k]
        if proj == project and now - e.get("last", 0) > quiet:
            done.append(dict(e, project=proj, key=key, age_s=int(e.get("last", now) - e.get("first", now))))
            del d[k]
    if done:
        save_alarms(d)
    return done


RETIRED_HEARTBEAT_AGE = settings.default("heartbeat.retired_days") * 86400


def stale_siblings(root, max_age=900):
    """Other projects whose watchdog once had a heartbeat and now has none.

    A dead watchdog cannot report itself; the ones next to it can. Only projects
    with a heartbeat file count — a project that never ran one is not late."""
    me = project_key(root)
    out = {}
    try:
        for f in os.listdir(os.path.join(HOME, ".ao")):
            if not f.startswith("heartbeat-") or f == f"heartbeat-{me}":
                continue
            age = int(time.time() - os.path.getmtime(os.path.join(HOME, ".ao", f)))
            # A week of silence is a project that was retired, not a watchdog that
            # just died; ringing a person about it every cycle never ended (audit).
            if max_age < age <= settings.get(None, "heartbeat.retired_days") * 86400:
                out[f[len("heartbeat-"):]] = age
    except OSError:
        pass
    return out


# ---- review scope ----------------------------------------------------------------

COORDINATION_DIRS = (".ao/", "agent-mail/")      # and each shipped harness's own, from harness_dirs()


def harness_dirs():
    """What the shipped harnesses keep in a repository (`detect.dirs`), each ending in a slash (#76)."""
    return tuple(sorted({str(d).strip("/") + "/" for adapter in package_adapters().values()
                         for d in (adapter.get("detect") or {}).get("dirs") or [] if str(d).strip("/")}))


def review_diff(root, cfg, paths=None, budget=1_500_000):
    """What the reviewer sees: the slice's changes, and nothing else.

    `git diff HEAD` carried every tracked change in the tree, so an unrelated
    steering edit polluted a review of a credential lifecycle; untracked files
    were capped at the first twenty, and thirty review artefacts consumed the
    cap before the one untracked file that mattered — the inventory under
    review — was reached. Two rounds of a five-round budget went to that.

    Coordination directories are never product. Untracked product files are
    included newest first within a byte budget. `paths` narrows both sides.
    """
    skip = _coordination_dirs(cfg)
    # Git without a shell: the pathspecs were single-quoted for sh, cmd.exe hands the quotes
    # to git as part of each path, and a scoped diff held none of the changes it named (#71).
    spec = ["--", *paths] if paths else []
    diff = _git_text(root, "diff", "HEAD", *spec, timeout=60) or ""
    if not paths:
        parts = re.split(r"(?m)^(?=diff --git )", diff)
        diff = "".join(pt for pt in parts if not any(
            f" b/{d}" in pt.split("\n", 1)[0] for d in skip))
    untracked = [l[3:].strip().strip('"') for l in (_git_text(root, "status", "--porcelain") or "").split("\n")
                 if l.startswith("?? ")]
    untracked = [f for f in untracked if not _is_coordination_path(f, cfg)]
    if paths:
        untracked = [f for f in untracked if any(f == p or f.startswith(p.rstrip("/") + "/")
                                                  or p.startswith(f.rstrip("/") + "/") for p in paths)]
    files = []
    for f in untracked:
        p = os.path.join(root, f)
        if os.path.isdir(p):
            for dp, _, fn in os.walk(p):
                for x in fn:
                    rel = os.path.relpath(os.path.join(dp, x), root).replace(os.sep, "/")
                    if _is_coordination_path(rel, cfg):
                        continue
                    if not paths or any(rel == q or rel.startswith(q.rstrip("/") + "/") for q in paths):
                        files.append(rel)
        elif os.path.isfile(p):
            files.append(f)
    files.sort(key=lambda f: -os.path.getmtime(os.path.join(root, f)))
    included, used = [], 0
    for f in files:
        p = os.path.join(root, f)
        try:
            size = os.path.getsize(p)
            if size > 400_000 or used + size > budget:
                continue
            raw = open(p, "rb").read()
            if b"\0" in raw[:4000]:
                continue
            diff += f"\n--- NEW FILE {f} ---\n" + raw.decode("utf-8", "replace")
            used += size
            included.append(f)
        except OSError:
            pass
    return diff, included


# ---- helpers: processes ao starts that are not writers -------------------------

def helpers_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"helpers-{key}.json")


def _helpers_update(root, change):
    """Change the helper registry under its lock and replace it whole (#68).

    It was read, changed and written back unlocked, and the read path wrote too, so
    a status call overwrote a helper registered a moment before and the watchdog
    counted a running reviewer as a second writer.
    """
    from .storage import _exclusive_lock, replace_file_durably
    path = helpers_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _exclusive_lock(path + ".lock"):
        try:
            with open(path, encoding=UTF8) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        change(d)
        replace_file_durably(path, json.dumps(d).encode(UTF8))


def helper_register(root, pid, what):
    """A reviewer, a probe: started by ao inside the repo, never a writer.

    Bind the declaration to process start identity. PID existence alone is not
    identity: after reuse it would exclude an unrelated process indefinitely.
    """
    start = _process_start(pid, refresh=True)

    def change(d):
        # Dead, reused or unprovable entries go here, under the lock, never on a read.
        for key in list(d):
            recorded = d.get(key) if isinstance(d.get(key), dict) else {}
            try:
                current = _process_start(int(key))
            except (TypeError, ValueError):
                current = None
            if current is None or current != recorded.get("start"):
                d.pop(key, None)
        d[str(pid)] = {"what": what, "at": int(time.time()), "start": start}

    try:
        _helpers_update(root, change)
    except OSError:
        pass


def helper_release(root, pid):
    try:
        _helpers_update(root, lambda d: d.pop(str(pid), None))
    except OSError:
        pass


def helper_pids(root, what=None):
    """Registered helpers still running as the process that registered; reading writes nothing."""
    try:
        with open(helpers_path(root), encoding=UTF8) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return set()
    if not isinstance(d, dict):
        return set()
    live = set()
    for key, recorded in d.items():
        try:
            pid = int(key)
        except (TypeError, ValueError):
            continue
        recorded = recorded if isinstance(recorded, dict) else {}
        current_start = _process_start(pid)
        # Dead, reused, or legacy/unprovable pid: never let declaration alone
        # exclude a process. Conservative writer checks may count it once.
        if current_start is not None and recorded.get("start") == current_start:
            if what is None or recorded.get("what") == what:
                live.add(pid)
    return live


# ---- cost: what the coordination itself spends ----------------------------------
#
# "How much quota does ao cost, and is it bureaucracy?" is answerable only from
# the transcript. Every turn is classified by what it did — wrote product, ran
# the review/gate ceremony, only coordinated, or read and thought — and its
# spend is summed per class, so the overhead is a number and not an opinion.

_PRODUCT_PATH = re.compile(r"(^|/)(src|lib|app|apps|test|tests|spec|fixtures|evidence|docs|plugins|site|"
                           r"package\.json|pyproject\.toml|tsconfig)")
def _coord_path():
    """A path under a coordination directory, the shipped harnesses' own included (#76)."""
    names = ["agent-mail", r"\.ao", "semantic-review"] + [re.escape(d.rstrip("/")) for d in harness_dirs()]
    return re.compile(r"(^|/)(" + "|".join(names) + r")(/|$)")


# ---- what each feature costs, measured rather than estimated (#10) ------------------------

SPAWN_LOGS = {"nudge": "nudge-{key}.log", "architect_wake": "escalate-{key}.log", "refill": "refill-{key}.log"}


def spawn_times(root, what, since=None):
    """When the watchdog started a nudge, an architect wake or a refill, read from its own logs (#10)."""
    from .watchdog import STATE_DIR
    path = os.path.join(STATE_DIR, SPAWN_LOGS[what].format(key=project_key(root)))
    times = []
    try:
        with open(path, encoding=UTF8, errors="replace") as fh:
            for line in fh:
                found = re.match(r"^=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ", line)
                if found:
                    at = time.mktime(time.strptime(found.group(1), "%Y-%m-%d %H:%M:%S"))
                    if since is None or at >= since:
                        times.append(at)
    except OSError:
        pass
    return times


def feature_costs(cfg, since=None):
    """The implementer's spend attributed to each feature switch, and the window it was measured over (#10).

    `review`: turns that ran `ao review`. `reports`: turns that only coordinated -
    inbox, report, board, writers. `nudge`: turns a nudge started, within five
    minutes of it, that wrote no product; a nudge that started real work is the
    work, not overhead. The architect's wakes and refills spend the architect's
    pool, not this transcript, so they are counted and not priced, and an
    inventory review cannot be told from a review in a transcript, so it is
    counted with review. A turn the model never answered (`UNANSWERED`) is no turn
    of the implementer's.
    """
    root = cfg["root"]
    costs = turn_costs(cfg, since)
    turns = [turn for turn in costs["turns"] if turn.get("cls") not in (None, UNANSWERED)]
    nudges = spawn_times(root, "nudge", since)
    features = {name: {"turns": 0, "usage": 0.0} for name in ("review", "reports", "nudge")}
    for turn in turns:
        start = turn.get("start") or 0
        if turn.get("reviews"):
            key = "review"
        elif turn["cls"] == "coordination":
            key = "reports"
        elif turn["cls"] != "product" and any(0 <= start - at <= 300 for at in nudges):
            key = "nudge"
        else:
            continue
        features[key]["turns"] += 1
        features[key]["usage"] += float(turn.get("usage") or 0)
    starts = [turn["start"] for turn in turns if turn.get("start")]
    return {"unit": costs["unit"], "total": sum(float(turn.get("usage") or 0) for turn in turns),
            "turns": len(turns), "from": min(starts) if starts else None, "to": max(starts) if starts else None,
            "features": features,
            "counted": dict({what: len(spawn_times(root, what, since)) for what in ("architect_wake", "refill")},
                            hunter=sum(1 for row in hunter_rows(root) if row.get("event") == "run"
                                       and (since is None or row.get("at", 0) >= since)))}


# The class of a turn the model never answered: every reply in it the harness wrote in the model's place,
# with no usage (`in_place_reply`). It spent nothing and did nothing, and is counted apart from the classes
# of the implementer's work.
UNANSWERED = "unanswered"


def turn_costs(cfg, since=None):
    """Per-turn cost and class from the implementer's transcript.

    Returns {"unit", "turns": [ {start, usage, delegated, cls, tool_calls, product_writes,
    reviews, commits, blocked_report} ], "by_class": {cls: {turns, usage, delegated}},
    "ao_commands": Counter, "total", "delegated"}. Classes: product (wrote product files or
    committed), ceremony (review / verify / lock / commit-ok, nothing written),
    coordination (only inbox/report/writers/board, few calls), analysis (read and
    reasoned, wrote nothing). A turn whose every reply the harness wrote in the model's
    place, with no usage (`in_place_reply`) - an error of the model's service, a reply to a
    note that asked the model nothing - is `UNANSWERED`: counted, and counted apart.

    A subagent the implementer started works for it: the records of its transcript
    (`transcript.subagents`) are read after the record that starts it, its spend is in the
    usage of the turn that started it and in that turn's `delegated`, and its tool calls,
    writes and commits are that turn's (`with_subagents`).

    Which records open and close a turn, carry usage or call a tool is the
    implementer's adapter's to declare (`transcript.turn`, `transcript.messages`,
    `transcript.tool_call`, `telemetry.cost`), and so is the reading that adds usage
    up (`billing.fallback.reading`), the one the credit estimate applies. A turn opens
    at a start record, or at a prompt when none is open or the open one ended; the
    start record that follows a turn's prompt is that turn's start, not a second turn
    (`next_turn`). Counting both doubled every turn count and left spend where it was.
    """
    import collections
    msgs, _ = session_paths(cfg)
    shape = transcript_shape(implementer_adapter(cfg))
    usage, tool = shape["usage"], shape["tool"]
    out = {"unit": shape["unit"], "turns": [], "by_class": {}, "ao_commands": collections.Counter(), "total": 0.0,
           "delegated": 0.0}
    if not msgs or not os.path.exists(msgs):
        return out
    recs = read_tail(msgs, 400_000_000)
    cur, counted = None, {}
    in_place, answered = set(), set()           # the turns holding a reply written in the model's place, or its own

    def ts(d):
        raw = record_time(d, shape)
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    for d, subagent in with_subagents(recs, shape, msgs):
        pl = record_body(d, shape) or {}
        t = record_kind(d, shape)
        cur, opened = next_turn(cur, d, shape, subagent)
        if opened:
            cur.update({"start": ts(d), "usage": 0.0, "delegated": 0.0, "product_writes": 0, "coord_writes": 0,
                        "tool_calls": 0, "reviews": 0, "commits": 0, "blocked_report": False,
                        "ao": collections.Counter()})
            out["turns"].append(cur)
            continue
        if cur is None:
            continue
        if subagent is None and t in shape["reply"]:
            (in_place if in_place_reply(d, shape) else answered).add(id(cur))
        if usage and t == usage["type"]:
            add_usage(cur, pl, usage, counted, subagent)
        # Not an elif: one record of a store that nests its calls carries a response's usage and its tool calls.
        if tool and t == tool["type"]:
            for name, args in tool_calls(pl, tool):
                cur["tool_calls"] += 1
                args = args or {}
                text = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
                if name.endswith("ao_report") and "blocked" in text:      # MCP clients prefix tool names
                    cur["blocked_report"] = True
                if tool_writes_file(name, tool):
                    path = str(tool_path(args, tool) or "")
                    if _coord_path().search(path):
                        cur["coord_writes"] += 1
                    elif _PRODUCT_PATH.search(path) or (path and "/" in path):
                        cur["product_writes"] += 1
                m = re.search(r"\bao\s+(?:-C\s+\S+\s+)?([a-z][a-z-]+)", text)
                if m:
                    cur["ao"][m.group(1)] += 1
                    out["ao_commands"][m.group(1)] += 1
                if re.search(r"\bao\s+(?:-C\s+\S+\s+)?review\b", text):
                    cur["reviews"] += 1
                if re.search(r"git\s+commit\b", text):
                    cur["commits"] += 1
    for tn in out["turns"]:
        if since and (not tn["start"] or tn["start"] < since):
            tn["cls"] = None
            continue
        if id(tn) in in_place and id(tn) not in answered and not tn["usage"]:
            cls = UNANSWERED
        elif tn["product_writes"] or tn["commits"]:
            cls = "product"
        elif tn["reviews"] or any(c in tn["ao"] for c in ("verify", "lock", "commit-ok")):
            cls = "ceremony"
        elif tn["blocked_report"] or (tn["tool_calls"] <= 8 and (tn["ao"] or tn["coord_writes"])):
            cls = "coordination"
        else:
            cls = "analysis"
        tn["cls"] = cls
        b = out["by_class"].setdefault(cls, {"turns": 0, "usage": 0.0, "wasted": 0, "wasted_usage": 0.0,
                                             "delegated": 0.0})
        b["turns"] += 1
        b["usage"] += tn["usage"]
        b["delegated"] += tn["delegated"]
        if tn["blocked_report"] and not tn["product_writes"]:
            b["wasted"] += 1
            b["wasted_usage"] += tn["usage"]
        out["total"] += tn["usage"]
        out["delegated"] += tn["delegated"]
    return out


# ---- ids: a clock reading names one record, even where the clock is coarse (#71) --------

_ID_CLOCK = {}


def _unique_clock(unit):
    """Nanoseconds since the epoch in whole `unit`s, never the same value twice in this process (#71).

    Windows advanced the clock about every 15.6 ms before Python 3.13, so every id
    minted within one tick - two notices in one watchdog cycle, two merge checks, two
    grants - was one id. A repeat takes the next value up: an id keeps its shape and
    still sorts by the time it was minted. Two processes can still read one tick; where
    a shared id would overwrite a record, the store claims it (a review's state file is
    created exclusively), and two ledger rows that share one stay two rows.
    """
    import threading
    with _ID_CLOCK.setdefault("lock", threading.Lock()):     # the first lock stored is the one used
        value = max(time.time_ns() // unit, _ID_CLOCK.get(unit, 0) + 1)
        _ID_CLOCK[unit] = value
    return value


def unique_ms():
    """Milliseconds since the epoch for an id: a notice, a submitted review, a merge check."""
    return _unique_clock(1_000_000)


def unique_ns():
    """Nanoseconds since the epoch for an id: a commit grant's token."""
    return _unique_clock(1)


# ---- deferred work: what could not run, so it can run later ----------------------
#
# A quota window closing must not lose anything. Every action ao could not take
# — a nudge, a wake, a review — is written down with why, and `ao catchup`
# replays the queue when the way is clear. This is the "bypass now, reconcile
# later" the human asked for: the run degrades, it never forgets.

def deferred_append(root, kind, **fields):
    """Defer one kind of work; an open deferral of that kind is the one that stands (#87).

    A wake is state, not a queue of attempts: fourteen deferred wakes piled up over
    one outage and replayed in a burst when it ended.
    """
    standing = next((row for row in deferred_open(root) if row.get("kind") == kind), None)
    if standing:
        return standing
    d = os.path.join(root, ".ao", "ledger")
    rec = {"event": "deferred", "id": f"DF-{int(time.time())}-{kind}", "kind": kind, "at": int(time.time())}
    rec.update(fields)
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "deferred.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def deferred_close(root, did, outcome="done"):
    d = os.path.join(root, ".ao", "ledger")
    try:
        with open(os.path.join(d, "deferred.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps({"event": "closed", "id": did, "at": int(time.time()), "outcome": outcome}) + "\n")
    except OSError:
        pass


def deferred_open(root):
    p = os.path.join(root, ".ao", "ledger", "deferred.jsonl")
    if not os.path.exists(p):
        return []
    rows, closed = {}, set()
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "deferred":
            rows[r["id"]] = r
        elif r.get("event") == "closed":
            closed.add(r["id"])
    return [r for i, r in rows.items() if i not in closed]


def recently_deferred(root, kind, within=3600):
    cut = time.time() - within
    return any(r["kind"] == kind and r["at"] >= cut for r in deferred_open(root))


# ---- waivers: the human's bypass, on the record ---------------------------------

WAIVER_CHAIN = "ao-waiver-row-v1"
WAIVER_HOURS_DEFAULT = settings.default("waivers.default_hours")
WAIVER_HOURS_MAX = settings.default("waivers.max_hours")


def waivers_path(root):
    return os.path.join(root, ".ao", "ledger", "waivers.jsonl")


def waiver_rows(root):
    """Every waiver row once the chain validates (#67).

    Rows written before the ledger was chained may stand first. An unlinked row
    after them, or a broken link, makes the ledger unreadable, and every reader
    fails closed.
    """
    from .storage import read_chained_jsonl
    return read_chained_jsonl(waivers_path(root), WAIVER_CHAIN, legacy_prefix=True)


def _login_and_terminal():
    """The login a command ran under and whether a terminal was attached."""
    try:
        import getpass
        user = getpass.getuser()
    except Exception:
        user = None
    try:
        interactive = os.isatty(0)
    except OSError:
        interactive = False
    return user, bool(interactive)


def waive(root, gate, slice_id, why, by, hours=WAIVER_HOURS_DEFAULT):
    """Record that a person waived a gate for one slice, for a bounded time (#67).

    A chained append like an authority row. It carries an expiry, the name the
    person gave, and beside it what ao can check: the login and whether a terminal
    was attached.
    """
    from .storage import append_chained_jsonl
    now = int(time.time())
    taken = {row.get("id") for row in waiver_rows(root) if isinstance(row, dict)}
    wid, n = f"W-{now}", 2
    while wid in taken:
        wid, n = f"W-{now}-{n}", n + 1
    user, interactive = _login_and_terminal()
    record = {"event": "waived", "id": wid, "gate": gate, "slice": slice_id, "why": why,
              "by": by, "at": now, "expires": now + int(float(hours) * 3600),
              "user": user, "interactive": interactive,
              "head": _git_text(root, "rev-parse", "HEAD"), "tree": tree_digest(root)}
    return append_chained_jsonl(waivers_path(root), record, WAIVER_CHAIN, legacy_prefix=True)


def close_waiver(root, wid, outcome, evidence=None):
    """Retire a waiver with a durable chained append; a failure raises rather than leaving it open unseen (#67).

    `evidence` is what a close stood on when no review decided it: a move proof, and what it read (#44).
    """
    from .storage import append_chained_jsonl
    row = {"event": "closed", "id": wid, "at": int(time.time()), "outcome": outcome}
    if evidence is not None:
        row["evidence"] = evidence
    return append_chained_jsonl(waivers_path(root), row, WAIVER_CHAIN, legacy_prefix=True)


def open_waivers(root, gate=None, slice_id=None):
    rows, closed = {}, set()
    for r in waiver_rows(root):
        if not isinstance(r, dict):
            continue
        if r.get("event") == "waived" and r.get("id"):
            rows[r["id"]] = r
        elif r.get("event") == "closed":
            closed.add(r.get("id"))
    out = [r for i, r in rows.items() if i not in closed]
    if gate:
        out = [r for r in out if r.get("gate") == gate]
    if slice_id:
        out = [r for r in out if r.get("slice") in (slice_id, "*")]
    return out


def _waiver_candidates(root):
    """Candidate digests each waiver has already authorised, from the grants that used it."""
    bound = {}
    for row in authority_rows(root):
        if isinstance(row, dict) and row.get("granted") is True and row.get("waiver"):
            bound.setdefault(row["waiver"], set()).add((row.get("candidate") or {}).get("digest"))
    return bound


def review_waiver_for(root, running, candidate_digest, now=None):
    """The review waiver that may stand in for a review of this candidate, and why others may not (#67).

    It names a running slice exactly, has not expired, and has authorised no other
    candidate. A waiver for every slice, one from before waivers expired, and one
    already spent on other bytes stand in for nothing.
    """
    now = time.time() if now is None else now
    bound = _waiver_candidates(root)
    notes = []
    for waiver in reversed(open_waivers(root, gate="review")):
        wid, slice_id = waiver.get("id"), waiver.get("slice")
        if slice_id not in running:
            if slice_id == "*":
                notes.append(f"waiver {wid} names every slice; a waiver covers one")
            continue
        expires = waiver.get("expires")
        if not isinstance(expires, (int, float)) or isinstance(expires, bool):
            notes.append(f"waiver {wid} for {slice_id} predates waiver expiry; a person opens a new one")
            continue
        if expires <= now:
            notes.append(f"waiver {wid} for {slice_id} expired "
                         f"{time.strftime('%d %b %H:%M', time.localtime(expires))}")
            continue
        used = bound.get(wid, set())
        if used and candidate_digest not in used:
            notes.append(f"waiver {wid} already authorised other bytes; it covers one candidate")
            continue
        return waiver, notes
    return None, notes


def open_waiver_report(root, now=None):
    """One line per open waiver with its age and expiry, for `ao doctor` (#67)."""
    now = time.time() if now is None else now
    lines = []
    for waiver in open_waivers(root):
        age = max(0, int(now - (waiver.get("at") or now)))
        expires = waiver.get("expires")
        state = ("no expiry (legacy)" if not isinstance(expires, (int, float))
                 else "expired" if expires <= now
                 else f"expires in {int((expires - now) // 3600)}h")
        lines.append(f"{waiver.get('id')} {waiver.get('gate')} for {waiver.get('slice')}, "
                     f"open {age // 86400}d {age % 86400 // 3600}h, {state}, by {waiver.get('by')}")
    return lines


_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# How long before a waiver was opened a commit landed under it may say it was committed: a clock
# that disagreed, on this machine or on the one the commit was made on (CATCHUP-POLISH).
WAIVER_CLOCK_ALLOWANCE = 86400


def _waiver_history(root, waiver, grants):
    """The `git log` revisions that hold the commit a bounded waiver's grant landed, or None (CATCHUP-POLISH).

    The newest 500 commits were searched, and a repository that lands dozens of commits a day
    would soon have called every older waiver UNRESOLVED. The commit that lands a granted tree
    descends from the head the grant was built on, so the search runs from the first recorded
    head git still has on HEAD's line, whatever came before it. A head comes from a ledger
    anyone on the machine can edit, so it must be a full object id; a history rewritten since
    can have left it off that line, and then the waiver's own date bounds the walk: git stops
    walking where the commits grow older than that. With neither, nothing is searched: the
    whole history of a long-lived repository is no bound.
    """
    seen = set()
    for row in grants:
        head = str((row.get("candidate") or {}).get("head") or "")
        if head in seen or not _OBJECT_ID.fullmatch(head):
            continue
        seen.add(head)
        try:
            ancestor = subprocess.run(
                [git_binary(), "merge-base", "--is-ancestor", head, "HEAD"], cwd=root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            ).returncode
        except (OSError, subprocess.TimeoutExpired):
            ancestor = 2
        if ancestor == 0:
            return ["--ancestry-path", f"{head}..HEAD"]
    at = waiver.get("at")
    if isinstance(at, (int, float)) and not isinstance(at, bool) and at > WAIVER_CLOCK_ALLOWANCE:
        return [f"--since=@{int(at) - WAIVER_CLOCK_ALLOWANCE}", "HEAD"]
    return None


def _bound_waiver_target(root, waiver, grants):
    """The one landed commit a bounded waiver covers, found by the tree granted under it (#17).

    A waiver that nothing was granted under covers no commit; one whose granted tree
    never landed is UNRESOLVED rather than reviewed by guess. The newest grant of the
    tree that landed says who landed it, where it recorded that (#65), and the move
    proof it stood on, where it recorded one (#44). The commit is looked for where it can
    have landed, and it is the first there to carry a granted tree: a later commit of the
    same tree landed under another grant (CATCHUP-POLISH).
    """
    trees = {(row.get("candidate") or {}).get("index_tree") for row in grants}
    item = {"waiver": waiver, "start": "", "end": "", "landed": 0,
            "newest": False, "problem": None, "unused": not trees, "author": None, "grant": None,
            "move_only": None, "expired": (waiver.get("expires") or 0) <= time.time()}
    if not trees:
        return item
    span = _waiver_history(root, waiver, grants)
    if span is None:
        item["problem"] = ("UNRESOLVED: no grant under it records a head on HEAD's line, and it records no date "
                           "to search from")
        return item
    try:
        log = _git_output(root, "log", "--format=%H %T", *span, "--").decode("ascii")
    except (RuntimeError, UnicodeError):
        item["problem"] = "UNRESOLVED: git cannot list the landed commits"
        return item
    carried = [(parts[0], parts[1]) for parts in (line.split() for line in log.splitlines())
               if len(parts) == 2 and parts[1] in trees]
    if not carried:
        item["problem"] = "UNRESOLVED: no landed commit carries the tree granted under it"
        return item
    sha, tree = carried[-1]                      # git lists the newest first
    try:
        parent = _git_output(root, "rev-parse", "--verify", f"{sha}^").decode("ascii").strip()
    except (RuntimeError, UnicodeError):
        item["problem"] = "UNRESOLVED: its landed commit has no parent to review against"
        return item
    grant = next((row for row in reversed(grants) if (row.get("candidate") or {}).get("index_tree") == tree), {})
    item.update(start=parent, end=sha, landed=1, grant=grant.get("token"),
                author=grant.get("author") if isinstance(grant.get("author"), dict) else None,
                move_only=grant.get("move_only") if isinstance(grant.get("move_only"), dict) else None)
    return item


def review_waiver_ranges(root):
    """Each open review waiver with the commits that landed while it was the newest.

    A waiver records the HEAD it was opened on and nothing else, so reviewing
    head..HEAD for every open waiver reviews each later slice again under the
    first slice's boundary: with one waiver per slice, N waivers become N
    overlapping reviews and the earliest carries every slice after it. A slice's
    commits run from its waiver's head to the head of the next review waiver
    opened after it, open or closed, and the newest waiver runs to HEAD.

    Heads come from a ledger file anyone on the machine can edit, so each is
    required to be a full object id and git runs without a shell.

    Returns dicts: waiver, start, end, landed (commit count), newest, problem, and
    for a bounded waiver's landed commit the grant that landed it and the author and
    move proof that grant recorded; a waiver from before waivers were bounded has none.
    """
    waived, closed = [], set()
    for r in waiver_rows(root):
        if not isinstance(r, dict):
            continue
        if r.get("event") == "waived" and r.get("gate") == "review":
            waived.append(r)
        elif r.get("event") == "closed":
            closed.add(r.get("id"))
    if not waived:
        return []
    granted = {}
    for row in authority_rows(root):
        if isinstance(row, dict) and row.get("granted") is True and row.get("waiver"):
            granted.setdefault(row["waiver"], []).append(row)
    try:
        current = _git_output(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    except (RuntimeError, UnicodeError):
        current = None
    out = []
    for i, w in enumerate(waived):
        if w.get("id") in closed:
            continue
        if "expires" in w:
            out.append(_bound_waiver_target(root, w, granted.get(w["id"], [])))
            continue
        later = waived[i + 1] if i + 1 < len(waived) else None
        start = str(w.get("head") or "")
        end = str(later.get("head") or "") if later else (current or "")
        item = {"waiver": w, "start": start, "end": end, "landed": 0,
                "newest": later is None, "problem": None, "author": None, "grant": None, "move_only": None}
        if not _OBJECT_ID.fullmatch(start):
            item["problem"] = "its recorded head is not a full object id"
        elif not _OBJECT_ID.fullmatch(end):
            item["problem"] = ("the next waiver's recorded head is not a full object id"
                               if later else "HEAD cannot be resolved")
        else:
            try:
                ancestor = subprocess.run(
                    [git_binary(), "merge-base", "--is-ancestor", start, end], cwd=root,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
                ).returncode
            except (OSError, subprocess.TimeoutExpired):
                ancestor = 2
            if ancestor == 1:
                item["problem"] = "its head is not an ancestor of the range end; history was rewritten"
            elif ancestor:
                item["problem"] = "git cannot compare its head with the range end"
            else:
                try:
                    item["landed"] = int(_git_output(root, "rev-list", "--count", f"{start}..{end}").strip() or 0)
                except (RuntimeError, ValueError):
                    item["problem"] = "git cannot count the commits in its range"
        out.append(item)
    return out


def range_changed_lines(root, start, end):
    """Lines a landed range adds and removes, as git counts them for its diff; None when git cannot say.

    A binary file counts none. `ao catchup --plan` sizes a sitting of retrospective
    reviews from these before any reviewer is asked.
    """
    try:
        out = _git_output(root, "diff", "--numstat", "--no-ext-diff", f"{start}..{end}", "--").decode(UTF8, "replace")
    except RuntimeError:
        return None
    total = 0
    for line in out.splitlines():
        added, _, rest = line.partition("\t")
        removed = rest.partition("\t")[0]
        if added.isdigit() and removed.isdigit():
            total += int(added) + int(removed)
    return total


def range_move_proof(root, start, end):
    """(moved, problems): `ao split-check`'s proof, taken on a landed range rather than a candidate (#44).

    A move-only grant recorded that proof for the tree it granted. What landed is proven
    again, from the commit it landed on: a proof taken against another parent says nothing
    about this diff. The proof reads Python definitions only, so every other changed path is
    a problem too - a closure on the proof must cover the whole range. The commits come from
    ledgers anyone on the machine can edit, so each must be a full object id.
    """
    if not (_OBJECT_ID.fullmatch(str(start)) and _OBJECT_ID.fullmatch(str(end))):
        return [], ["the range is not two full object ids"]
    try:
        names = _git_output(root, "diff", "--name-only", "--no-renames", "-z", start, end, "--")
        result = split_moves(root, start, end)
    except (RuntimeError, ValueError) as exc:
        return [], [f"the landed range cannot be read: {exc}"]
    unread = sorted({os.fsdecode(raw) for raw in names.split(b"\0") if raw and not raw.endswith(b".py")})
    return result["moved"], result["problems"] + [f"{path} changed, and the proof reads Python definitions only"
                                                  for path in unread]


# ---- credits: burn rate and the day the work stops -------------------------------

def credit_account(profile):
    """A stable name for the account a reading came from; the profile itself is not kept (#36)."""
    if not profile:
        return None
    return "acct-" + hashlib.sha256(str(profile).encode("utf-8")).hexdigest()[:12]


def record_credit_sample(root, used, limit, reset_at=None, account=None, at=None, adapter=None):
    """One credit reading, with the account it is of (#36) and the adapter whose lookup took it (ACCOUNT-READERS)."""
    d = os.path.join(root, ".ao", "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "credits.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps({"at": int(at if at is not None else time.time()), "used": float(used),
                                 "limit": float(limit), "reset_at": reset_at,
                                 "account": account, "adapter": adapter}) + "\n")
    except OSError:
        pass


def credit_samples(root, adapter, limit=500):
    """The newest credit readings taken through this adapter's lookup; none for a call that names no adapter.

    A reading is the account of the adapter that read it (ACCOUNT-READERS). One ledger can
    hold another harness's readings - an implementer that moved to another harness leaves
    its old one's behind - and an implementer does not bill that account, so no reader
    takes them for its own. A reading written before readings named their adapter is
    nobody's, as one that does not name its account projects nothing (#36).
    """
    p = os.path.join(root, ".ao", "ledger", "credits.jsonl")
    if not adapter or not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("adapter") == adapter:
            rows.append(row)
    return rows[-limit:]


def burn_rate(root, adapter, window=72 * 3600, now=None):
    """From the samples this adapter's lookup took: credits per day, days left, and whether
    the plan runs out before it resets. None when there are not two samples a few hours apart."""
    now = now or time.time()
    rows = [r for r in credit_samples(root, adapter) if r["at"] >= now - window]
    # One account's series only (#36). An owner who switched accounts left the old
    # account's cumulative `used` in the ledger, and the projection ran across both:
    # exhaustion declared against an account that was 4% used. A reading that does
    # not say whose it is projects nothing.
    account = rows[-1].get("account") if rows else None
    if not account:
        return None
    rows = [r for r in rows if r.get("account") == account]
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    span = last["at"] - first["at"]
    if span < 3 * 3600:
        return None
    per_day = max(0.0, (last["used"] - first["used"]) / span * 86400)
    remaining = max(0.0, last["limit"] - last["used"])
    days_left = remaining / per_day if per_day > 0 else None
    exhausts_at = now + days_left * 86400 if days_left is not None else None
    reset_at = last.get("reset_at")
    before_reset = bool(exhausts_at and reset_at and exhausts_at < reset_at)
    return {"per_day": per_day, "remaining": remaining, "days_left": days_left,
            "exhausts_at": exhausts_at, "reset_at": reset_at, "before_reset": before_reset,
            "used": last["used"], "limit": last["limit"], "account": account,
            "samples": [first, last]}


# ---- external ping: the dead man's switch --------------------------------------------

def pings_path():
    return os.path.join(HOME, ".ao", "pings.json")


def ping_url(root):
    try:
        d = json.load(open(pings_path(), encoding=UTF8))
    except (OSError, ValueError):
        return None
    key = project_key(root)
    return d.get(key) or d.get("*")


def set_ping_url(root, url, all_projects=False):
    try:
        d = json.load(open(pings_path(), encoding=UTF8))
    except (OSError, ValueError):
        d = {}
    d["*" if all_projects else project_key(root)] = url
    os.makedirs(os.path.dirname(pings_path()), exist_ok=True)
    json.dump(d, open(pings_path(), "w", encoding=UTF8), indent=2)
    os.chmod(pings_path(), 0o600)
    return d


def ping(root, opener=None):
    """One GET to the project's ping URL. A service that expects it every N
    minutes alarms when it stops — which is the only way anyone learns that both
    the watchdog and its doctor job have died."""
    url = ping_url(root)
    if not url:
        return None
    import urllib.request
    try:
        (opener or urllib.request.urlopen)(url, timeout=5)
        return True
    except Exception:
        return False


# ---- architect lock: one judge at a time --------------------------------------------

def architect_lock_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"architect-{key}.lock")


def architect_lock_holder(root):
    try:
        d = json.load(open(architect_lock_path(root), encoding=UTF8))
    except (OSError, ValueError):
        return None
    if d.get("pid") and not _pid_alive(int(d["pid"])):
        return None                                        # stale: holder died
    if time.time() - d.get("at", 0) > 3 * 3600:
        return None                                        # stale: forgotten
    return d


def acquire_architect(root, pid, who):
    """Take the lock unless a live holder has it. Two architect copies deciding at
    once — a desktop resume beside a watchdog wake — contradict each other."""
    holder = architect_lock_holder(root)
    if holder and holder.get("pid") != pid:
        return None
    d = {"pid": pid, "who": who, "at": int(time.time())}
    try:
        os.makedirs(os.path.dirname(architect_lock_path(root)), exist_ok=True)
        json.dump(d, open(architect_lock_path(root), "w", encoding=UTF8))
    except OSError:
        pass
    return d


def release_architect(root, pid=None):
    try:
        d = json.load(open(architect_lock_path(root), encoding=UTF8))
        if pid is None or d.get("pid") == pid:
            os.remove(architect_lock_path(root))
    except (OSError, ValueError):
        pass


# ---- foreign edits: a person in the same files -------------------------------------

def implementer_recent_writes(cfg, minutes=15):
    """Paths the implementer's tools wrote in the last N minutes, from its transcript.

    A tool call, the argument naming its file and the tools that write one are what the
    implementer's adapter declares in `transcript.tool_call` (`write_tools`, `write_words`),
    as `ao cost` reads them; one that declares none has written nothing ao can see. Every
    call naming a file was taken for a write, reads and directory listings too, so a person
    editing a file the implementer had only read was never told apart from the implementer.
    A subagent's calls are the implementer's too: every subagent transcript beside the
    session's written in the window is read (`transcript.subagents`), whichever turn started it.
    """
    msgs, _ = session_paths(cfg)
    if not msgs or not os.path.exists(msgs):
        return set()
    shape = transcript_shape(implementer_adapter(cfg))
    tool = shape["tool"]
    if not tool:
        return set()
    cut = time.time() - minutes * 60
    out = set()
    for d in read_tail(msgs, 3_000_000) + subagent_tails(msgs, shape, cut, 3_000_000):
        if record_kind(d, shape) != tool["type"]:
            continue
        try:
            import datetime as _dt
            at = _dt.datetime.fromisoformat(record_time(d, shape).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if at < cut:
            continue
        for name, args in tool_calls(record_body(d, shape), tool):
            path = tool_path(args, tool) if tool_writes_file(name, tool) else None
            if path:
                out.add(os.path.realpath(str(path)))
    return out


def foreign_edits(root, cfg, minutes=15):
    """Product files changed in the last N minutes that the implementer did not write.

    `ao writers` sees agents; it cannot see a person in an editor. This is the
    nearest thing: a dirty product file whose mtime is recent and which the
    implementer's own tool calls never touched. Named in the nudge so the
    implementer keeps away from it."""
    mine = implementer_recent_writes(cfg, minutes)
    cut = time.time() - minutes * 60
    out = []
    for line in product_dirty(root, cfg):
        rel = line[3:].strip().strip('"')
        p = os.path.join(root, rel)
        files = []
        if os.path.isdir(p):                                    # git lists an untracked dir as one entry
            for dp, _, fn in os.walk(p):
                files += [os.path.join(dp, x) for x in fn]
        elif os.path.isfile(p):
            files = [p]
        for f in files:
            try:
                if os.path.getmtime(f) >= cut and os.path.realpath(f) not in mine:
                    out.append(os.path.relpath(f, root).replace(os.sep, "/"))     # git's slashes, everywhere
            except OSError:
                pass
    return sorted(out)


# ---- fleet reserve: the machine's shared windows ----------------------------------

def fleet_reserve():
    return settings.get(None, "fleet.window_reserve_pct")


def window_headroom(provider):
    """(left_pct, reserve_pct) or (None, reserve) when the window is unreadable or no provider is known."""
    w = provider_window(provider) if provider else None
    return (100 - w["pct"]) if w else None, fleet_reserve()


def turn_ended(cfg):
    """Has the implementer's transcript closed its last turn?

    A process that is alive after its transcript wrote `turn_end` is not
    working; it is a runtime that forgot to exit. Waiting the full silence
    threshold for it (three times the idle window) cost twenty minutes per
    occurrence. The transcript's own word is enough to reap at the idle window.

    Which kinds close a turn and which are bookkeeping that may follow it is the
    implementer's adapter's to declare (`transcript.turn`).

    A session may end its turn while a subagent it started works on in the background
    (`transcript.subagents`), and its runtime then lingers for the subagent, not because it
    forgot to exit. A subagent transcript written after the session transcript's own last
    write holds work the session has not taken back, so the turn has not ended. The session
    takes a finished subagent back with a record of its own - the result of the call that
    waited for it, or the notification of its end - so a finished subagent leaves the turn
    to what the session says. Only modification times are read: a subagent's own records
    cannot say it finished, since an agent a workflow started ends on the result of a call,
    not on a response that ends a turn."""
    msgs, _ = session_paths(cfg)
    if not msgs or not os.path.exists(msgs):
        return False
    shape = transcript_shape(implementer_adapter(cfg))
    tail = read_tail(msgs, 200_000)
    for d in reversed(tail):
        t = record_kind(d, shape)
        if is_bookkeeping(t, shape):
            continue                                        # bookkeeping after the turn
        if not closes_turn(d, shape):
            return False
        written = os.path.getmtime(msgs)
        return all(mtime <= written for mtime, _ in subagent_writes(msgs, shape))
    return False
