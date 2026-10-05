"""Quota and mail: fan-out, rotation, binaries, reports, the mail envelope, unread age, the message
store and its sync.

A part of src/ao/lib.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


# ---- fan-out budget --------------------------------------------------------
#
# A coordinator that fans out to sub-agents has no gate of its own. One ran 47
# verification agents at once: 11 finished, 36 died with "session limit", two
# million tokens were spent and the answer was mostly missing. The watchdog's
# quota guard could not have helped — it watches the implementer's pool, and the
# fan-out was the coordinator's. This is the gate that was missing: a hard cap,
# the provider window as keyflip reports it, and the project's own record of
# what fan-outs cost, so the estimate is empirical after the first one.

def fanout_config(cfg):
    """The fan-out limits in force for this project (#74)."""
    return {name: settings.get(cfg, f"fanout.{name}")
            for name in ("max_agents", "per_agent_tokens", "window_reserve_pct")}


def _window_adapter():
    """The adapter whose quota command provider_window reads: the first, by id, that declares one."""
    return next((adapter for _, adapter in sorted(package_adapters().items())
                 if ((adapter.get("telemetry") or {}).get("quota") or {}).get("argv")), None)


def provider_window(name):
    """The machine-wide usage window keyflip reports for a provider, parsed.

    Read from the quota reading the status panel shows, kept for the adapter's window
    across ao's processes, so the panel and the verdict never disagree; this started the
    command again, through a shell, on every call. Returns {pct, window, resets_in,
    resets_s, raw} or None when no readable line exists — and None is reported as
    "unreadable", not as headroom.
    """
    adapter = _window_adapter()
    if adapter is None:
        return None
    for line in quota(adapter):
        if name.lower() not in line.lower():
            continue
        m = re.search(r"(\d+)\s*%", line)
        if not m:
            continue
        w = re.search(r"\b(\d+[hdw])\b", line)
        r = re.search(r"resets?\s+(?:in\s+)?([0-9hms ]+)", line)
        secs = 0
        if r:
            for n, u in re.findall(r"(\d+)\s*([hms])", r.group(1)):
                secs += int(n) * {"h": 3600, "m": 60, "s": 1}[u]
        return {"pct": int(m.group(1)), "window": w.group(1) if w else "?",
                "resets_in": r.group(1).strip() if r else "?", "resets_s": secs,
                "raw": line.strip()}
    return None


# ---- quota rotation is asked of keyflip, never written into config (#32) -----------------

def provider_of(argv):
    """The keyflip provider an actor's argv spends, as its adapter declares it (`quota.provider`), or None."""
    if not argv or not isinstance(argv[0], str):
        return None
    program = _program_name(argv[0])
    for ident, adapter in sorted(package_adapters().items()):
        provider = (adapter.get("quota") or {}).get("provider")
        if provider and program in {_program_name(name) for name in [ident, *adapter_binaries(adapter)]}:
            return provider
    return None


def rotate_if_exhausted(cfg, argv, who, on_wait=None):
    """Before an actor starts: rotate its provider's account through keyflip when the window is spent (#32).

    2026-09-07: kiro-cli hit its overage limit and every nudge failed silently for
    an hour, and the reviewer shared one Claude window with the architect. Account
    routing is keyflip's; ao only asks. keyflip.rotation is off unless a person
    turns it on, because a rotation is machine-wide and in place: it moves every
    session on that provider, so rotations are serialised under one machine lock
    and a window another actor already rotated is not rotated again. Returns
    {"ok", "provider", "rotated", "text"}; not ok means no account has headroom,
    and the caller surfaces that instead of spending the attempt. `on_wait` is called once the
    window is read spent, before the lock is waited on, so a caller that says it waits says so only
    then (REVIEW-START-DELAY).
    """
    provider = provider_of(argv)
    if settings.get(cfg, "keyflip.rotation") != "on" or not provider:
        return {"ok": True, "provider": provider, "rotated": False, "text": "rotation off"}
    ceiling = settings.get(None, "quota.block_percent")
    window = provider_window(provider)
    if not window or window["pct"] < ceiling:
        return {"ok": True, "provider": provider, "rotated": False, "text": "headroom"}
    from .storage import _exclusive_lock
    os.makedirs(os.path.join(HOME, ".ao"), exist_ok=True)
    if on_wait is not None:
        on_wait()
    with _exclusive_lock(os.path.join(HOME, ".ao", "keyflip-rotation.lock"), timeout=180):
        window = provider_window(provider)
        if window and window["pct"] < ceiling:
            return {"ok": True, "provider": provider, "rotated": False, "text": "another actor rotated first"}
        try:
            subprocess.run(["keyflip", "next", "--strategy", "best"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except Exception as exc:                      # a rotation that cannot run is not a headroom
            return {"ok": False, "provider": provider, "rotated": False,
                    "text": f"{provider} window {window['pct']}% used and keyflip could not rotate: {exc}"}
        # The kept reading is the window before the rotation: ask again, and an actor waiting on
        # this lock reads the rotated window, not the spent one it would rotate a second time.
        adapter = _window_adapter()
        if adapter is not None:
            forget_quota(adapter)
        after = provider_window(provider)
    if after and after["pct"] < ceiling:
        return {"ok": True, "provider": provider, "rotated": True,
                "text": f"rotated through keyflip for the {who}: {provider} now {after['pct']}% used"}
    return {"ok": False, "provider": provider, "rotated": True,
            "text": f"no {provider} account has headroom after rotating for the {who}"}


# ---- a usage limit's words are declared, not coded (QUOTA-PARK) --------------------------------
#
# A harness that hits its usage limit ends the turn with words of its own: a reply it writes in the
# model's place, a line its process prints as it exits. Each harness words them its own way, and the
# one pattern ao read them by was one harness's wording: another's limit was never read as one.

def quota_stops(adapter):
    """The usage-limit stops an adapter declares (`quota` → `stops`): [{"words", "hours"}]; [] for none.

    `words` is a regular expression that finds the words the harness stops on, and what it matches is
    the stop a person is told; a group named `epoch` in it holds the reset in epoch seconds, where the
    harness writes it so. `hours` is the longest the limit stands once it is hit: a reset its words name
    by the clock alone is the next such time within it, and one named further away than it is not the
    one meant. A stop without both, or whose words do not compile, is not read, and
    `quota_stop_problems` says why.
    """
    declared = (adapter or {}).get("quota")
    stops = declared.get("stops") if isinstance(declared, dict) else None
    found = []
    for stop in stops if isinstance(stops, list) else []:
        words, hours = (stop.get("words"), stop.get("hours")) if isinstance(stop, dict) else (None, None)
        if not isinstance(words, str) or not words or isinstance(hours, bool) \
                or not isinstance(hours, (int, float)) or hours <= 0:
            continue
        try:
            found.append({"words": re.compile(words), "hours": float(hours)})
        except re.error:
            continue
    return found


def quota_reset_path(adapter):
    """Where a reply the harness wrote in the model's place keeps the reset, in epoch seconds; "" where it keeps none.

    `quota` → `resets_at`: a harness that records the reset beside its words is read by it, and its
    words are read only where the record holds none.
    """
    declared = (adapter or {}).get("quota")
    path = declared.get("resets_at") if isinstance(declared, dict) else None
    return path if isinstance(path, str) else ""


def quota_stop_problems(adapter):
    """Each usage-limit stop an adapter declares that ao will not read, and why; [] when it reads them all (QUOTA-PARK).

    `quota_stops` passes such a stop over without a word, since a reading must not fail on a layer's
    mistake; `ao adapters validate` says why before anyone relies on the declaration.
    """
    declared = (adapter or {}).get("quota")
    if not isinstance(declared, dict):
        return []
    problems = []
    stops = declared.get("stops", [])
    if not isinstance(stops, list):
        return ["`quota.stops` must be a list of {words, hours}"]
    for number, stop in enumerate(stops, 1):
        words, hours = (stop.get("words"), stop.get("hours")) if isinstance(stop, dict) else (None, None)
        if not isinstance(words, str) or not words:
            problems.append(f"`quota.stops` #{number} must name its `words`, a regular expression")
        else:
            try:
                re.compile(words)
            except re.error as exc:
                problems.append(f"`quota.stops` #{number} words {words!r} do not compile: {exc}")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
            problems.append(f"`quota.stops` #{number} must say in `hours` how long the limit stands, above 0")
    if "resets_at" in declared and not (isinstance(declared["resets_at"], str) and declared["resets_at"]):
        problems.append("`quota.resets_at` must be the path to the reset in the harness's own reply")
    return problems


def fanout_history(root, limit=20):
    p = os.path.join(root, ".ao", "ledger", "fanouts.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-limit:]


def record_fanout(root, agents, done=None, errors=None, tokens=None, note=None):
    """What a fan-out actually cost. The next verdict is estimated from this."""
    d = os.path.join(root, ".ao", "ledger")
    os.makedirs(d, exist_ok=True)
    rec = {"at": int(time.time()), "agents": int(agents)}
    if done is not None:
        rec["done"] = int(done)
    if errors is not None:
        rec["errors"] = int(errors)
    if tokens is not None:
        rec["tokens"] = int(tokens)
    if note:
        rec["note"] = note
    rec["limit_hit"] = bool(rec.get("errors")) and bool(
        re.search(r"limit|quota|429|rate", note or "", re.I))
    with open(os.path.join(d, "fanouts.jsonl"), "a", encoding=UTF8) as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def observed_per_agent_tokens(root):
    """Average tokens per agent over recorded fan-outs (errored agents spent too)."""
    tok = n = 0
    for r in fanout_history(root, 50):
        if r.get("tokens") and (r.get("done") or r.get("errors")):
            tok += r["tokens"]
            n += (r.get("done") or 0) + (r.get("errors") or 0)
    return int(tok / n) if n else None


def fanout_verdict(root, cfg, agents, per_agent_tokens=None, provider=None):
    """May a fan-out of this size start now?

    Three checks, each a fact the caller can see: the project's hard cap, whether
    a fan-out already hit this provider's limit inside the current window, and
    how much of the window keyflip says is left. The token estimate is shown, not
    gated on — a percentage window cannot be converted to tokens honestly — but
    it becomes empirical after the first recorded run.
    """
    fc = fanout_config(cfg)
    observed = observed_per_agent_tokens(root)
    per = per_agent_tokens or observed or fc["per_agent_tokens"]
    provider = provider or provider_of((cfg.get("architect") or {}).get("argv"))
    win = provider_window(provider) if provider else None
    reasons, verdict = [], "ok"
    if agents > fc["max_agents"]:
        verdict = "too-many"
        reasons.append(f"{agents} agents > max_agents {fc['max_agents']} — run in batches of "
                       f"{fc['max_agents']} and record each")
    now = time.time()
    span = 5 * 3600
    if win and win.get("window", "").endswith("h"):
        try:
            span = int(win["window"][:-1]) * 3600
        except ValueError:
            pass
    window_start = now - (span - win["resets_s"]) if win and win.get("resets_s") else now - span
    for r in reversed(fanout_history(root, 10)):
        if r.get("limit_hit") and r["at"] >= window_start:
            if verdict == "ok":
                verdict = "limit-hit-recently"
            reasons.append(f"a fan-out of {r['agents']} hit the provider limit "
                           f"{int((now - r['at']) / 60)}m ago ({r.get('errors')} errors); "
                           f"wait for the window to reset"
                           + (f" (in {win['resets_in']})" if win else ""))
            break
    if win:
        left = 100 - win["pct"]
        if left < fc["window_reserve_pct"]:
            if verdict == "ok":
                verdict = "window-low"
            reasons.append(f"{provider} window {win['pct']}% used, {left}% left < reserve "
                           f"{fc['window_reserve_pct']}%; resets in {win['resets_in']}")
    else:
        reasons.append((f"{provider} window unreadable (keyflip absent or no line)" if provider
                        else "no keyflip provider runs the architect's command, so no window to read")
                       + " — hard cap and history only")
    spent = sum(r.get("tokens") or 0 for r in fanout_history(root, 50) if r["at"] >= window_start)
    return {"verdict": verdict, "ok": verdict == "ok", "agents": agents,
            "per_agent_tokens": per,
            "per_agent_source": "arg" if per_agent_tokens else ("observed" if observed else "default"),
            "estimated_tokens": agents * per, "spent_this_window": spent,
            "window": win, "provider": provider, "max_agents": fc["max_agents"], "reasons": reasons}



# ---- binaries ----------------------------------------------------------------
#
# The architect was woken forty times in eleven hours and every wake died with
# "Claude Code 2.1.185 does not support this model". The binary was real, on
# PATH, and two hundred versions stale — an npm-global leftover in /usr/local
# whose node had long since moved under a version manager, where a current
# copy sat unused. `which` answers "the first one", and the first one is the
# wrong question. Ask "the newest one" and remember what it said.

# Where a harness installs itself outside the usual directories is its adapter's to declare
# (`detect.install_dirs`, #76), placed where such a directory always stood in the search.
class _SearchDirs:
    """_BIN_DIRS: the directories searched after PATH, the adapter-declared ones in place, read when searched.

    Built as a tuple at import, the list read every package adapter before any `ao`
    run did anything, though most runs never look for a binary. It iterates, indexes
    and answers `in` as that tuple did, and a test may still put a tuple in its place.
    """

    def _dirs(self):
        return (("~/.local/bin", "~/bin", "/usr/local/bin", "/opt/homebrew/bin")
                + tuple(sorted({d for adapter in package_adapters().values()
                                for d in (adapter.get("detect") or {}).get("install_dirs") or []}))
                + ("~/.npm-global/bin", "~/.volta/bin", "~/.asdf/shims"))

    def __iter__(self):
        return iter(self._dirs())

    def __getitem__(self, index):
        return self._dirs()[index]

    def __len__(self):
        return len(self._dirs())

    def __contains__(self, item):
        return item in self._dirs()

    def __repr__(self):
        return repr(self._dirs())


_BIN_DIRS = _SearchDirs()
_BIN_GLOBS = ("~/.local/share/fnm/node-versions/*/installation/bin",
              "~/.fnm/node-versions/*/installation/bin",
              "~/.nvm/versions/node/*/bin", "~/.local/share/mise/installs/node/*/bin")


def binary_candidates(name, path=None):
    """Every executable called `name` this machine has, PATH first, deduplicated."""
    import glob as _glob
    dirs = [d for d in (path or os.environ.get("PATH", "")).split(os.pathsep) if d]
    dirs += [os.path.expanduser(d) for d in settings.get(None, "binaries.extra_dirs")]
    dirs += [os.path.expanduser(d) for d in _BIN_DIRS]
    for g in _BIN_GLOBS:
        dirs += sorted(_glob.glob(os.path.expanduser(g)), reverse=True)
    exts = [""] + (os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";") if os.name == "nt" else [])
    seen, out = set(), []
    for d in dirs:
        for ext in exts:
            cand = os.path.join(d, name + ext.lower()) if ext else os.path.join(d, name)
            if not (os.path.isfile(cand) and os.access(cand, os.X_OK)):
                continue
            real = os.path.realpath(cand)
            if real in seen:
                continue
            seen.add(real)
            out.append(cand)
    return out


def runnable_binary(name):
    """The file ao starts for `name` without a shell: the first binary_candidates match this platform runs, or None.

    Windows starts a program by its extension, and an npm install puts an extensionless
    POSIX script beside the .cmd that cmd.exe found; there only a match with an extension
    is a program.
    """
    return next((path for path in binary_candidates(name) if os.name != "nt" or os.path.splitext(path)[1]), None)


def _run_program(argv, cwd=None, timeout=20, stderr=subprocess.DEVNULL):
    """What sh() returned for these words, and the exit status, from the program started without a shell.

    ("", None) when the program is found nowhere, cannot be started or runs past the
    timeout: there the shell printed nothing. The program is looked for on the binary
    search path, so a launchd job whose PATH is minimal finds what a terminal finds; it
    reads nothing from standard input, and its standard error is discarded, as sh()
    discarded it, unless the caller keeps it in the answer (`subprocess.STDOUT`, `2>&1`).
    """
    program = runnable_binary(argv[0])
    if program is None:
        return "", None
    try:
        done = subprocess.run([program, *argv[1:]], cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=stderr, text=True, encoding=UTF8, errors="replace", timeout=timeout)
    except Exception:
        return "", None
    return done.stdout.strip(), done.returncode


def binary_version(path):
    """`path --version`, cached by (path, mtime) so a wake does not pay for it twice."""
    cache_p = os.path.join(HOME, ".ao", "binaries.json")
    try:
        cache = json.load(open(cache_p, encoding=UTF8))
    except (OSError, ValueError):
        cache = {}
    try:
        mtime = os.path.getmtime(os.path.realpath(path))
    except OSError:
        return ""
    ent = cache.get(path)
    if ent and ent.get("mtime") == mtime:
        return ent.get("version", "")
    out = ""
    try:
        # cwd=HOME: a version probe started from inside a repository would inherit
        # that cwd and read, for one cycle, as a turn running in it.
        r = subprocess.run([path, "--version"], capture_output=True, text=True, encoding=UTF8, errors="replace", timeout=25, cwd=HOME,
                           # The platform's separator: a colon ran Windows' last PATH entry into these (#71).
                           env=dict(os.environ, PATH=os.pathsep.join((os.environ.get("PATH", ""),
                                                                      os.path.dirname(os.path.realpath(path)),
                                                                      os.path.dirname(path)))))
        m = re.search(r"(\d+\.\d+\.\d+)", (r.stdout or "") + (r.stderr or ""))
        out = m.group(1) if m else ""
    except Exception:
        out = ""
    cache[path] = {"mtime": mtime, "version": out, "at": int(time.time())}
    try:
        os.makedirs(os.path.dirname(cache_p), exist_ok=True)
        json.dump(cache, open(cache_p, "w", encoding=UTF8))
    except OSError:
        pass
    return out


def _vtuple(v):
    return tuple(int(x) for x in v.split(".")) if v else (0,)


def resolve_binary(name, path=None):
    """(path, version) of the newest `name` on this machine; (None, "") if none.

    An absolute path is returned as-is with its version. Ties keep PATH order.
    """
    if os.path.isabs(name):
        return (name if os.access(name, os.X_OK) else None), binary_version(name) if os.path.exists(name) else ""
    best = (None, "")
    for cand in binary_candidates(name, path):
        v = binary_version(cand)
        if best[0] is None or _vtuple(v) > _vtuple(best[1]):
            best = (cand, v)
    return best


# ---- implementer reports ------------------------------------------------------

def _report_summary(path):
    try:
        for line in open(path, errors="replace", encoding=UTF8):
            if line.startswith("#"):
                return line.lstrip("# ").strip()
    except OSError:
        pass
    return ""


def bump_repeat(path, cfg=None):
    """Fold a repeated report into the standing one; keep its mtime (its age is the fact).

    The count is read from its line in either language and written back in the project's, so a
    report that stood before the project changed its language keeps counting (LANGUAGE-FILES).
    """
    try:
        st = os.stat(path)
        body = open(path, errors="replace", encoding=UTF8).read()
    except OSError:
        return 0
    label = "(?:" + "|".join(re.escape(form) for form in language.forms("repeat")) + ")"
    m = re.search(rf"^{label}: (\d+)", body, re.M)
    n = int(m.group(1)) + 1 if m else 2
    line = language.text(cfg, "mail.repeated", repeat=language.marker(cfg, "repeat"), n=n,
                         at=time.strftime('%Y-%m-%d %H:%M'))
    body = re.sub(rf"^{label}: \d+.*$", line, body, flags=re.M) if m else body.rstrip("\n") + "\n\n" + line + "\n"
    try:
        open(path, "w", encoding=UTF8).write(body)
        os.utime(path, (st.st_atime, st.st_mtime))
    except OSError:
        pass
    return n


def _name_time(name):
    m = re.match(r"(\d{8})-(\d{4})", name)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M"))
    except ValueError:
        return None


def implementer_inbox(root, cfg):
    """Mail addressed to the implementer: not its own reports, not the watchdog's."""
    box = cfg.get("mailbox", "agent-mail")
    return [m for m in mailbox(root, box) if not to_architect(m, cfg) and not from_watchdog(m)]


def product_dirty(root, cfg):
    """Uncommitted paths outside the coordination directories."""
    out = []
    # Not sh(): it strips its output, and the first line's leading status column
    # went with it - " M .ao/ledger/notices.jsonl" became "M .ao/...", the path
    # lost its first character, and a coordination file read as a product change.
    # A failure still reads as no uncommitted paths, as it did through sh(); no
    # caller decides authority from this.
    try:
        status = _git_output(root, "status", "--porcelain").decode(UTF8, "replace")
    except Exception:
        status = ""
    for line in status.split("\n"):
        if not line.strip():
            continue
        raw = line[3:].strip().strip('"')
        paths = [part.strip().strip('"') for part in raw.split(" -> ")]
        # A rename crossing the boundary is a product change; ignore it only
        # when both its old and new names are coordination state.
        if paths and all(_is_coordination_path(path, cfg) for path in paths):
            continue
        out.append(line)
    return out


def waiting_on_architect(root, cfg):
    """The implementer's standing request the architect has not answered.

    Returns (report, asked_at) when the newest implementer report asks for a
    decision, nothing addressed to the implementer arrived after it, and the
    board has nothing queued. Nudging in that state produces reports, not
    progress: eighty of them, once, at eight-minute intervals.
    """
    box = cfg.get("mailbox", "agent-mail")
    files = mailbox(root, box)
    reports = [m for m in files if to_architect(m, cfg) and not from_watchdog(m)
               and not from_architect(m, cfg)]
    if not reports:
        return None
    latest = reports[-1]
    p = os.path.join(root, box, latest)
    try:
        low = open(p, errors="replace", encoding=UTF8).read(4000).lower()
    except OSError:
        return None
    # In every language a project may choose, whichever this one writes (LANGUAGE-FILES).
    if not any(h in low for h in language.lowered("decision", "urgent") + ("## blocked",)):
        return None
    # Delivery is by deletion, so any file addressed to the implementer is
    # unread — whatever its timestamp. An answer written while the implementer
    # was repeating its request is older than the repeat and still the answer.
    if implementer_inbox(root, cfg):
        return None
    if board(root)["queued"]:
        return None
    return latest, _name_time(latest) or os.path.getmtime(p)


# ---- mail envelope, names, ledger --------------------------------------------

def mail_names(cfg):
    """(implementer, architect) mail names; defaults keep the historical files valid."""
    impl = (settings.get(cfg, "implementer.name") or implementer_actor_name(cfg)).strip()
    arch = settings.get(cfg, "architect.name").strip()
    return impl, arch


def to_architect(name, cfg):
    _, arch = mail_names(cfg)
    return f"-to-{arch}-" in name or "-to-architect-" in name


def from_architect(name, cfg):
    """Whether a mail was written by the architect, read from its sender field (#18)."""
    _, arch = mail_names(cfg)
    found = re.match(r"^\d{8}-\d{4}-(.+?)-to-", name)
    return bool(found) and found.group(1).lower() in {arch.lower(), "architect"}


def notice_recently_recorded(root, key, window):
    """Was this key recorded inside the window at all, delivered or held (#69)?

    An architect-audience notice is recorded unsent by design, so a check on sent
    rows never held for one, and the same anomaly wrote a row every cycle. The whole
    window counts, whatever the ledger still holds (NOTICE-WINDOW).
    """
    return _notice_within(root, key, window, sent=False)


SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9_-]{16,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"(?:xox[abprs]|xapp)-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}",
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    r"[A-Za-z0-9+_=-]{40,}",
)


# What ao writes into a repository is scanned for credentials first (#48). Named rules,
# so a redaction says what it removed; hex digests (commit ids, sha256 values) match
# none of them, which is why the generic long-token rule `redact` uses is not here.
EVIDENCE_RULES = (
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("anthropic-key", r"sk-ant-[A-Za-z0-9_-]{16,}"),
    ("openai-key", r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    ("github-token", r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    # Slack's app-level tokens begin `xapp-` (EVIDENCE-SCAN-2).
    ("slack-token", r"(?:xox[abprs]|xapp)-[A-Za-z0-9-]{10,}"),
    ("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ("bearer-token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    # The name is read with what an environment puts before it - DB_PASSWORD, CLIENT_SECRET, NPM_TOKEN,
    # AWS_SECRET_ACCESS_KEY - where `\b` is no boundary after an underscore and none of them matched
    # (EVIDENCE-SCAN-2). A bare `token` is ao's own grant id, and a name that goes on past the secret word
    # (`password_env`, `secret_file`) names where one is kept, not the secret; neither is read.
    ("assigned-secret", r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]+_)*(?:api[_-]?key|secret(?:[_-]access)?(?:[_-]key)?|"
                        r"password|passwd|(?:access|auth)[_-]?token|[A-Za-z0-9]+_token|private[_-]?key)"
                        r"(?![A-Za-z0-9_])[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_.=-]{12,}"),
)


def scan_evidence(text):
    """(text with every credential-shaped string replaced by `[redacted:<rule>]`, the rules that hit)."""
    out = str(text if text is not None else "")
    hits = []
    for rule, pattern in EVIDENCE_RULES:
        out, count = re.subn(pattern, f"[redacted:{rule}]", out)
        if count:
            hits.append(rule)
    return out, hits


def scan_record(value):
    """A JSON-shaped value with every string in it scanned (#48)."""
    if isinstance(value, str):
        return scan_evidence(value)[0]
    if isinstance(value, dict):
        return {key: scan_record(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scan_record(item) for item in value]
    return value


def redact(text):
    """Text from an agent's own output with anything token-shaped masked (#69).

    A wake or nudge log merges a model's prose with its process's errors, and a
    line of it went to the committed notices ledger, Telegram and e-mail. What
    leaves the log is masked first.
    """
    out = str(text or "")
    for pattern in SECRET_PATTERNS:
        out = re.sub(pattern, "[redacted]", out)
    return out


def record_actor_flags(root, actor, flags, reason):
    """Record a flag ao adds to an actor's argv, with why, each time that changes (#69)."""
    from .storage import append_jsonl, read_jsonl
    path = os.path.join(root, ".ao", "ledger", "actor-flags.jsonl")
    try:
        rows = read_jsonl(path)
    except Exception:
        rows = []
    last = next((row for row in reversed(rows)
                 if isinstance(row, dict) and row.get("actor") == actor), None)
    if last and last.get("flags") == list(flags) and last.get("reason") == reason:
        return last
    return append_jsonl(path, {"at": int(time.time()), "actor": actor,
                               "flags": list(flags), "reason": reason})


def from_watchdog(name):
    return name.startswith("watchdog-to-") or "-watchdog-to-" in name


def write_mail(root, cfg, name, body, meta=None):
    """Every mail ao itself writes goes through here: envelope first, prose after.

    The body stays Markdown — agents and people read it. The envelope is a small
    front-matter block a program can read without parsing prose: kind, from, to,
    slice, time, id. And every write is mirrored to `.ao/ledger/mail.jsonl`, so
    that delivery-by-deletion stops deleting the record: a mailbox that was
    emptied is still searchable, and "how long did that request stand" has an
    answer after the file is gone.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    os.makedirs(box, exist_ok=True)
    meta = dict(meta or {})
    meta.setdefault("ao", 1)
    meta.setdefault("id", name)
    meta.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    # The address is the role; the name beside it is display (#31).
    meta.setdefault("from_role", role_of(meta.get("from"), cfg))
    meta.setdefault("to_role", role_of(meta.get("to"), cfg))
    order = ["ao", "id", "kind", "from", "from_role", "to", "to_role", "slice", "at"]
    keys = [k for k in order if k in meta] + [k for k in meta if k not in order]
    meta = scan_record(meta)
    body = scan_evidence(body)[0]                     # scanned before it is written (#48)
    head = "---\n" + "".join(f"{k}: {meta[k]}\n" for k in keys if meta[k] not in (None, "")) + "---\n"
    with open(os.path.join(box, name), "w", encoding=UTF8) as fh:
        fh.write(head + body.lstrip("\n"))
    summary = next((l.lstrip("# ").strip() for l in body.split("\n") if l.startswith("#")), "")
    mail_ledger_append(root, {"event": "written", "id": name, "kind": meta.get("kind"),
                              "from": meta.get("from"), "to": meta.get("to"),
                              "slice": meta.get("slice"), "summary": summary[:200]})
    # Named, never quoted: the log is read by programs a message's body was not written for (EVENTS-LOG).
    emit_event(root, "mail-sent", {"message": name, "kind": meta.get("kind"), "from": meta.get("from"),
                                   "to": meta.get("to")})
    # Mail ao writes reaches the append-only store at once: it reached it at the next reconcile, and a view
    # file removed before then took its message with it (MESSAGE-STORE-2). The view file stands whatever the
    # store says; one the store cannot take now is taken at the next reconcile, and the doctor names a store
    # it cannot read.
    if mail_store_mode(root) == "append-only":
        try:
            ingest_mail(root, cfg)
        except Exception:
            pass
    return name


def mail_meta(path):
    """The envelope of a mail file, or {} for a file written without one."""
    try:
        head = open(path, errors="replace", encoding=UTF8).read(2000)
    except OSError:
        return {}
    if not head.startswith("---\n"):
        return {}
    end = head.find("\n---", 4)
    if end < 0:
        return {}
    out = {}
    for line in head[4:end].split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


# ---- unread age escalates on the ladder (#30) --------------------------------------------

MAIL_CLASSES = ("fyi", "needs-read", "needs-decision", "urgent")
_DECISION_KINDS = language.words("decision-kinds")        # in every language, whichever the project writes
_FYI_KINDS = language.words("fyi-kinds")


def mail_class(name, meta=None, body=""):
    """What a message asks of whoever reads it: fyi, needs-read, needs-decision or urgent (#30).

    Declared in the envelope (`class:`) when the writer said; otherwise read from
    its kind and its headings, the way the watchdog already reads a request.
    """
    meta = meta or {}
    declared = str(meta.get("class") or "").strip().lower()
    if declared in MAIL_CLASSES:
        return declared
    kind = str(meta.get("kind") or "").strip().lower()
    if not kind:
        found = re.match(r"^\d{8}-\d{4}-.+?-to-.+?-([a-z]+)-", name, re.I)
        kind = found.group(1).lower() if found else ""
    low = str(body or "").lower()
    if kind in _DECISION_KINDS or any(mark in low for mark in language.lowered("decision")):
        return "needs-decision"
    return "fyi" if kind in _FYI_KINDS else "needs-read"


def addressed_to(name, cfg, role):
    """Whether a message is for a role: the architect's inbox, or the implementer's; a person reads both."""
    if role == "architect":
        return to_architect(name, cfg)
    if role == "implementer":
        return not to_architect(name, cfg) and not from_watchdog(name)
    return True


def mail_seen(root, names, by):
    """Record, once each, that messages were put in front of their reader (#30).

    Seeing is not handling: handling stays deletion, and a message seen and not
    yet handled is still in the mailbox. What this separates is "nobody has been
    shown it" from "someone is working on it".
    """
    already = {row.get("id") for row in mail_log(root, 5000) if row.get("event") == "seen"}
    fresh = [name for name in names if name not in already]
    for name in fresh:
        mail_ledger_append(root, {"event": "seen", "id": name, "by": by})
    return fresh


def unseen_messages(root, cfg):
    """Messages in the mailbox nobody has been shown, oldest first, with their class and age (#30)."""
    box = cfg.get("mailbox", "agent-mail")
    rows = mail_log(root, 5000)
    seen = {row.get("id") for row in rows if row.get("event") == "seen"}
    written = {row.get("id"): row.get("at") for row in rows if row.get("event") == "written"}
    out = []
    for name in mailbox(root, box):
        if name in seen or from_watchdog(name):
            continue
        path = os.path.join(root, box, name)
        # A message whose body cannot be read, or whose written time in the ledger is no number, is still one
        # nobody has been shown: it was dropped, and a decision request so read never climbed the ladder. It is
        # classed by its name and envelope, and aged by its file (UNREAD-AGE-2).
        try:
            with open(path, errors="replace", encoding=UTF8) as fh:
                body = fh.read(4000)
        except OSError:
            body = ""
        try:
            at = float(written.get(name))
        except (TypeError, ValueError):
            try:
                at = os.path.getmtime(path)
            except OSError:
                continue                                   # gone since the listing
        out.append({"id": name, "class": mail_class(name, mail_meta(path), body), "at": at,
                    "age": max(0.0, time.time() - at)})
    return sorted(out, key=lambda message: message["at"])


# ---- nothing is deleted to prove it was handled (#80) ------------------------------------

MAIL_STORE_CHAIN = "ao-mail-store-row-v1"


def mail_store_mode(root):
    """`append-only` when the project has switched its mailbox to the store, else `deletion` (#80)."""
    try:
        return settings.get(load_config(root), "mail.store")
    except Exception:
        return "deletion"


def mail_store_rows(root):
    from .storage import read_chained_jsonl
    return [row for row in read_chained_jsonl(os.path.join(root, ".ao", "ledger", "mail-store.jsonl"), MAIL_STORE_CHAIN)
            if isinstance(row, dict)]


def _mail_store_append(root, row):
    from .storage import append_chained_jsonl
    return append_chained_jsonl(os.path.join(root, ".ao", "ledger", "mail-store.jsonl"),
                                scan_record(dict(row, at=int(time.time()))), MAIL_STORE_CHAIN)


def _store_path(root, mid):
    return os.path.join(root, ".ao", "mail", "store", os.path.basename(mid))


def ingest_mail(root, cfg):
    """Take every message in the mailbox view into the append-only store, once (#80).

    A body stored with no row - the row's append failed after the body landed, and the view was
    removed before the next reconcile - is taken from the store itself; only the view was read,
    and the message was gone from the queue with its body orphaned (MESSAGE-STORE-3).
    """
    from .storage import replace_file_durably
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    known = {row.get("id") for row in mail_store_rows(root) if row.get("event") == "message"}
    views = set(os.listdir(box)) if os.path.isdir(box) else set()
    store = os.path.join(root, ".ao", "mail", "store")
    stored = set(os.listdir(store)) if os.path.isdir(store) else set()
    taken = []
    for name in sorted(views | stored):
        if name == "README.md" or not name.endswith(".md") or name in known:
            continue
        source = os.path.join(box, name) if name in views else _store_path(root, name)
        with open(source, "rb") as fh:
            data = fh.read()
        if name not in views and data.startswith(b"ao-mail-stub v1\n"):
            continue
        if name in views:
            replace_file_durably(_store_path(root, name), data)
        meta = mail_meta(source)
        _mail_store_append(root, {"event": "message", "id": name,
                                  "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                                  "from": meta.get("from"), "to": meta.get("to"), "kind": meta.get("kind")})
        taken.append(name)
    return taken


def unhandled_messages(root):
    """The unhandled queue, derived: every stored message with no handling record (#80)."""
    rows = mail_store_rows(root)
    handled = {row.get("id") for row in rows if row.get("event") == "handled"}
    return sorted({row.get("id") for row in rows if row.get("event") == "message"} - handled)


def handle_message(root, cfg, mid, by, outcome):
    """Record that a message was handled, by whom and how; its view file then goes, its record never (#80)."""
    if mid not in unhandled_messages(root):
        return False
    _mail_store_append(root, {"event": "handled", "id": mid, "by": by, "outcome": outcome})
    view = os.path.join(root, cfg.get("mailbox", "agent-mail"), mid)
    try:
        os.remove(view)
    except FileNotFoundError:
        pass
    return True


def message_body(root, mid):
    """A stored message's bytes, read through a compaction stub when it has one (#80)."""
    import gzip
    try:
        with open(_store_path(root, mid), "rb") as fh:
            data = fh.read()
    except OSError:
        data = None
    if data is not None and data.startswith(b"ao-mail-stub v1\n"):
        archive = data.decode(UTF8).split("archive: ", 1)[1].strip()
        with gzip.open(os.path.join(root, archive), "rb") as fh:
            return fh.read()
    return data


def sync_mail_view(root, cfg):
    """Make the mailbox view the derived queue: restore what was removed unhandled, drop what was handled (#80).

    An agent may delete a view file; that removes nothing. The message comes back
    until someone records handling it, because the actor deciding what was handled
    must not also be able to erase the question.
    """
    from .storage import replace_file_durably
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    queue = set(unhandled_messages(root))
    restored, dropped = [], []
    for mid in sorted(queue):
        view = os.path.join(box, mid)
        if not os.path.exists(view):
            body = message_body(root, mid)
            if body is not None:
                replace_file_durably(view, body)
                restored.append(mid)
    stored = {row.get("id") for row in mail_store_rows(root) if row.get("event") == "message"}
    for name in (sorted(os.listdir(box)) if os.path.isdir(box) else []):
        if name in stored and name not in queue:
            os.remove(os.path.join(box, name))
            dropped.append(name)
    return restored, dropped


def compact_messages(root, days, now=None):
    """Collapse stored bodies older than `days` to a stub with their digest and an archive pointer (#80)."""
    import gzip
    from .storage import replace_file_durably
    cutoff = (time.time() if now is None else now) - days * 86400
    compacted = []
    done = {row.get("id") for row in mail_store_rows(root) if row.get("event") == "compacted"}
    for row in mail_store_rows(root):
        mid = row.get("id")
        if row.get("event") != "message" or mid in done or float(row.get("at") or 0) > cutoff:
            continue
        path = _store_path(root, mid)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        archive = f".ao/mail/archive/{mid}.gz"
        if data.startswith(b"ao-mail-stub v1\n"):
            # A compaction whose record did not land left its stub, and its archive holds the body: the stub
            # was read as the body and written over the archive, and the words were gone. The record is what
            # was missing (MESSAGE-STORE-2).
            _mail_store_append(root, {"event": "compacted", "id": mid, "archive": archive, "digest": row.get("digest")})
            compacted.append(mid)
            done.add(mid)
            continue
        os.makedirs(os.path.dirname(os.path.join(root, archive)), exist_ok=True)
        # The archive is written as durably as the stub that replaces the body: written plainly, a crash could
        # leave a stub pointing at an archive that never reached the disk (MESSAGE-STORE-2).
        replace_file_durably(os.path.join(root, archive), gzip.compress(data, mtime=0))
        replace_file_durably(path, f"ao-mail-stub v1\ndigest: {row.get('digest')}\narchive: {archive}\n".encode(UTF8))
        _mail_store_append(root, {"event": "compacted", "id": mid, "archive": archive, "digest": row.get("digest")})
        compacted.append(mid)
        done.add(mid)                   # two writers can each have stored it: it is compacted once
    return compacted


def room_search(text, root=None, limit=20):
    """Stored messages in every registered project whose body mentions `text` (#80)."""
    needle = str(text or "").lower()
    found = []
    for project, path in recall_roots(root):
        try:
            rows = [row for row in mail_store_rows(path) if row.get("event") == "message"]
        except Exception:
            continue
        for row in rows:
            body = message_body(path, row["id"])
            if body is not None and needle in body.decode(UTF8, "replace").lower():
                found.append(dict(row, project=project))
    found.sort(key=lambda row: -float(row.get("at") or 0))
    return found[:limit]


# ---- the message store syncs to one private repository, never the product's remote (#83) --

def _remote_identity(url):
    """(host, path) a git URL names, alike for its https, ssh and host:path forms; None for a path on this machine."""
    url = (url or "").strip()
    found = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", url)
    if not found and _scp_like(url):
        found = re.match(r"^(?:[^@]+@)?([^:]+):(.+)$", url)
    if not found:
        return None
    return found.group(1).lower(), found.group(2).strip("/").lower().removesuffix(".git")    # .GIT too (MAIL-SYNC-3)


def _scp_like(url):
    """Whether git reads `url` as host:path over SSH: a colon before any slash, with or without a user (MAIL-SYNC-2)."""
    colon, slash = url.find(":"), url.find("/")
    return colon > 0 and not 0 <= slash < colon and not (os.name == "nt" and re.match(r"^[A-Za-z]:", url))


def _git_config_entries(root, pattern):
    """(key, value) of every git config entry whose key matches `pattern`, from every file git reads (MAIL-SYNC-4).

    git answers 1 when no key matches; any other failure is no answer, and is not taken for an empty one.
    """
    try:
        result = subprocess.run([git_binary(), "config", "-z", "--get-regexp", pattern], cwd=root,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot read git config: {exc}") from exc
    if result.returncode == 1:
        return []
    if result.returncode:
        raise RuntimeError(f"cannot read git config: {result.stderr.decode(UTF8, 'replace').strip()[:240]}")
    return [tuple(record.partition("\n")[::2]) for record in result.stdout.decode(UTF8, "replace").split("\0")
            if record]


def _git_rewrite(url, entries, rule):
    """(url as git rewrites it, the rule's key, its prefix) by the url.<base>.<rule> entries among `entries`; else None.

    git's alias_url: the longest prefix wins, and of two as long, the one whose base git read first. The
    first entry that matched was taken, and a refusal could name a place git would not push (MAIL-SYNC-6).
    """
    rewrites = {}                                # each base with its prefixes, in the order git first reads it
    for key, value in entries:
        if key.startswith("url.") and key.endswith("." + rule):
            rewrites.setdefault(key[4:key.rindex(".")], []).append((key, value))
    best = None
    for base, rules in rewrites.items():
        for key, prefix in rules:
            if url.startswith(prefix) and (best is None or len(prefix) > len(best[2])):
                best = (base + url[len(prefix):], key, prefix)
    return best


def _git_reads_otherwise(root, url):
    """Why `git push url` would not reach `url` itself, or None (MAIL-SYNC-4).

    git looks a word up as a remote's name, in every config file, before it reads it as a URL, and
    rewrites a URL by url.<base>.insteadOf and, when it pushes, url.<base>.pushInsteadOf: `mailbox`
    was judged a private directory while a pushInsteadOf sent it to another host, a remote's pushurl
    `origin` was pushed to as the remote origin, and a remote kept in ~/.gitconfig, which
    `git remote get-url` does not know, was taken for a directory.
    """
    entries = _git_config_entries(root, r"^(remote\..+\.[^.]+|url\..+\.pushinsteadof)$")
    # What git pushes to is said before what it fetches from: a pushInsteadOf wins over an insteadOf when git
    # pushes, and the refusal named the fetch's rewrite, a place git would not push (MAIL-SYNC-5).
    pushed = _git_rewrite(url, entries, "pushinsteadof")
    if pushed:
        return f"git pushes {url} to {pushed[0]} ({pushed[1]} = {pushed[2]})"
    seen = _git_output(root, "ls-remote", "--get-url", url).decode(UTF8, "replace").strip()

    def spelled(text):                           # a path git prints with its own separators is the same path
        text = text.replace("\\", "/").rstrip("/")
        return text.lower() if os.name == "nt" else text
    if spelled(seen) != spelled(url):
        return f"git reads {url} as {seen}"     # a remote of that name, from any config file, or an insteadOf
    for key, _ in entries:
        if key.startswith("remote.") and key[7:key.rindex(".")] == url:
            return f"{url} is also the name of a remote ({key})"
    return None


def _push_url(root, target):
    """The one URL `git push target` reaches: the push URL of the remote `target` names, else `target` (MAIL-SYNC-3).

    git takes a configured remote's name before a directory of that name, and pushes to its pushurl
    where one is set: `origin` was judged as a directory - private, no host asked, never compared with
    the product's remote - and `git push origin` took the mail to the product. A name is judged, read
    and pushed as the URL it names. A word that begins with `-` is an option to git, not a repository:
    `--upload-pack=<command>` ran the command on every `ao doctor`, and it is refused before git sees it.
    The URL is then pushed to and read as itself, so one git would read as another remote or rewrite
    is refused, naming what git reaches (MAIL-SYNC-4).
    """
    if str(target or "").startswith("-"):
        raise RuntimeError(f"{target!r} is an option to git, not a repository")
    try:
        urls = _git_output(root, "remote", "get-url", "--push", "--all", target).decode(UTF8, "replace")
    except RuntimeError:
        urls = target                            # no remote of that name here: git reads it as a URL or a path
    else:
        urls = [url.strip() for url in urls.splitlines() if url.strip()]
        if len(urls) != 1:
            raise RuntimeError(f"the remote {target} pushes to {len(urls)} URLs; name one repository")
        if urls[0].startswith("-"):
            raise RuntimeError(f"the remote {target} names {urls[0]!r}, an option to git, not a repository")
        urls = urls[0]
    why = _git_reads_otherwise(root, urls)
    if why:
        raise RuntimeError(f"{why}; name the repository git reaches")
    return urls


def _github_says_private(url):
    """True or False as GitHub answers for a repository on github.com itself; None for any other host, or no answer.

    The host is the one git reaches: `github.com/<owner>/<repo>` matched anywhere in the URL let GitHub's
    answer for an unrelated repository send mail and governance to another host (MAIL-SYNC-3).
    """
    import shutil
    identity = _remote_identity(url)
    found = identity and identity[0] == "github.com" and re.fullmatch(r"([^/]+)/([^/]+)", identity[1])
    if not found or not shutil.which("gh"):
        return None
    try:
        answer = subprocess.run(["gh", "api", f"repos/{found.group(1)}/{found.group(2)}", "--jq", ".private"],
                                capture_output=True, text=True, encoding=UTF8, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {"true": True, "false": False}.get(answer.stdout.strip())


def _url_is_private(root, url):
    """True for a local path, or when the host says the repository is private; None when it cannot say (#83).

    A URL is classified as git classifies it (MAIL-SYNC-2): `github.com:owner/repo.git`, an alias
    from ~/.ssh/config and `HTTPS://...` were taken for directories on this machine, and mail was
    pushed with no host asked. A host ao cannot ask - an alias, another server - is not confirmed.
    """
    url = url or ""
    if url and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url) and not _scp_like(url):
        return True                              # a directory on this machine publishes nothing
    return _github_says_private(url)


def mail_ref_commit(root):
    """Commit the message store under refs/ao/mail, scanned, parented on the last one; returns the commit (#83)."""
    store = os.path.join(root, ".ao", "mail")
    ledger = os.path.join(root, ".ao", "ledger", "mail-store.jsonl")
    entries = {}
    for directory, _, files in os.walk(store):
        for name in files:
            rel = os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/")
            with open(os.path.join(root, rel), "rb") as fh:
                data = fh.read()
            if name.endswith(".gz"):
                # A compacted body is scanned as every other record is before it leaves: an agent writes its
                # mail by hand, and a credential in it reached the private copy inside the archive (MAIL-SYNC-2).
                import gzip
                data = gzip.compress(scan_evidence(gzip.decompress(data).decode(UTF8, "replace"))[0].encode(UTF8),
                                     mtime=0)
            else:
                data = scan_evidence(data.decode(UTF8, "replace"))[0].encode(UTF8)
            entries[rel] = data
    if os.path.exists(ledger):
        with open(ledger, encoding=UTF8) as fh:
            entries[".ao/ledger/mail-store.jsonl"] = scan_evidence(fh.read())[0].encode(UTF8)
    tree = {}
    for rel, data in sorted(entries.items()):
        blob = subprocess.run([git_binary(), "hash-object", "-w", "--stdin"], cwd=root, input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()
        node = tree
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = blob

    def write(node):
        lines = [f"040000 tree {write(node[name])}\t{name}" if isinstance(node[name], dict)
                 else f"100644 blob {node[name]}\t{name}" for name in sorted(node)]
        return subprocess.run([git_binary(), "mktree"], cwd=root, input=("\n".join(lines) + "\n").encode(UTF8),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()

    parent = git_text(root, "rev-parse", "--verify", "--quiet", "refs/ao/mail")
    tree_id = write(tree)
    if parent and git_text(root, "rev-parse", f"{parent}^{{tree}}") == tree_id:
        return parent
    argv = [git_binary(), "-c", "user.name=ao", "-c", "user.email=ao@localhost", "commit-tree", tree_id,
            "-m", f"ao mail store of {project_key(root)}"]
    if parent:
        argv += ["-p", parent]
    commit = subprocess.run(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=True).stdout.decode().strip()
    _git_output(root, "update-ref", "refs/ao/mail", commit)
    return commit


def sync_mail(root, cfg):
    """Push the store's ref to the one private repository named in mail.sync_repo, as refs/mail/<project> (#83).

    The product's own remote is never a target, and a repository the host does not
    confirm private is refused, named and logged. Every record is scanned before it
    leaves. Returns (commit, where).
    """
    target = (settings.get(cfg, "mail.sync_repo") or "").strip()
    if not target:
        raise RuntimeError("no mail.sync_repo is named; syncing is opt-in per project")
    target = _push_url(root, target)
    # The product's remote in any of its forms - https and git@ name one repository (MAIL-SYNC-2) - where it
    # fetches and where it pushes (MAIL-SYNC-3), every URL of each: `git push origin` reaches them all (MAIL-SYNC-4).
    origins = {line.strip() for line in (git_text(root, "remote", "get-url", "--all", "origin").splitlines()
                                         + git_text(root, "remote", "get-url", "--push", "--all", "origin").splitlines())}
    # `git remote get-url` knows only the repository's own remotes; an origin kept in ~/.gitconfig is still where
    # `git push origin` goes (MAIL-SYNC-5), as git rewrites it: `product-short` with an insteadOf naming the product
    # was compared as written, and the mail went to the product (MAIL-SYNC-6).
    entries = _git_config_entries(root, r"^(remote\.origin\.(url|pushurl)|url\..+\.(insteadof|pushinsteadof))$")
    for key, value in entries:
        if key in ("remote.origin.url", "remote.origin.pushurl"):
            origins |= {value.strip()} | {rewritten[0].strip() for rewritten in (
                _git_rewrite(value, entries, "insteadof"), _git_rewrite(value, entries, "pushinsteadof")) if rewritten}
    same = any(origin and (target.rstrip("/").removesuffix(".git") == origin.rstrip("/").removesuffix(".git")
                           or _remote_identity(target) is not None and _remote_identity(target) == _remote_identity(origin))
               for origin in origins)
    if same:
        raise RuntimeError("mail.sync_repo is this product's own remote; mail never goes there")
    private = _url_is_private(root, target)
    if private is not True:
        record_notice(root, "mail sync refused", f"{target} was not confirmed private", sent=False, key="mail-sync-refused")
        raise RuntimeError(f"not syncing mail to {target}: the host did not confirm it is private")
    commit = mail_ref_commit(root)
    ref = f"refs/mail/{project_key(root)}"
    _git_output(root, "push", target, f"refs/ao/mail:{ref}", timeout=300)
    return commit, f"{target} {ref}"


def mail_sync_state(root, cfg):
    """(local, remote commit or None, problem or None) for `ao doctor` (#83).

    `local` is the last synced commit while the store is as it synced, and "unsynced" once its
    ledger has moved past the copy in that commit: only `ao mail sync` moves the ref, so mail
    stored since, or a store never synced, read as up to date (MAIL-SYNC-2). Read, never written:
    the doctor commits nothing. A target that cannot be read is named, not taken for one behind.
    """
    target = (settings.get(cfg, "mail.sync_repo") or "").strip()
    if not target:
        return None
    synced = git_text(root, "rev-parse", "--verify", "--quiet", "refs/ao/mail") or None
    try:
        with open(os.path.join(root, ".ao", "ledger", "mail-store.jsonl"), encoding=UTF8) as fh:
            now = scan_evidence(fh.read())[0]
    except OSError:
        now = None
    copy = None
    if synced and now is not None:
        try:
            copy = _git_output(root, "show", "refs/ao/mail:.ao/ledger/mail-store.jsonl").decode(UTF8, "replace")
        except RuntimeError:
            copy = None
    local = synced if now is None or copy == now else "unsynced"
    try:
        target = _push_url(root, target)         # read where a sync would push (MAIL-SYNC-3)
    except RuntimeError as exc:
        return local, None, f"mail.sync_repo cannot be synced to: {exc}"
    try:
        remote = (_git_output(root, "ls-remote", target, f"refs/mail/{project_key(root)}", timeout=60)
                  .decode(UTF8, "replace").split() or [None])[0]
    except RuntimeError as exc:
        return local, None, f"{target} cannot be read ({' '.join(str(exc).split())})"
    problem = None if _url_is_private(root, target) is True else f"{target} could not be verified private"
    return local, remote, problem


def mail_ledger_append(root, row):
    d = os.path.join(root, ".ao", "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        row = dict(row)
        row.setdefault("at", int(time.time()))
        with open(os.path.join(d, "mail.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def mail_log(root, limit=200):
    p = os.path.join(root, ".ao", "ledger", "mail.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-limit:]


def reconcile_mail_ledger(root, cfg):
    """Mail the ledger saw written and the mailbox no longer has was consumed.

    The architect and the implementer delete by hand; nothing hooks `rm`. So
    the watchdog closes the loop each cycle: an open id that is not on disk gets
    a `consumed` row, timed now — a lower bound on when it was read.
    """
    if mail_store_mode(root) == "append-only":
        # Nothing is consumed by vanishing: take new mail in, and put the view back in line (#80).
        ingest_mail(root, cfg)
        sync_mail_view(root, cfg)
        return []
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    rows = mail_log(root, 5000)
    open_ids = {}
    for r in rows:
        if r.get("event") == "written":
            open_ids[r["id"]] = r
        elif r.get("event") == "consumed":
            open_ids.pop(r["id"], None)
    closed = []
    for mid, r in open_ids.items():
        if not os.path.exists(os.path.join(box, mid)):
            mail_ledger_append(root, {"event": "consumed", "id": mid, "kind": r.get("kind"),
                                      "from": r.get("from"), "to": r.get("to"),
                                      "stood_s": int(time.time()) - int(r.get("at") or time.time())})
            closed.append(mid)
    return closed


def mail_search(root, text, limit=50):
    """Ledger rows and live files whose id, summary or body mention `text`."""
    t = text.lower()
    out, seen = [], set()
    for r in reversed(mail_log(root, 5000)):
        if r.get("event") != "written":
            continue
        hay = " ".join(str(r.get(k) or "") for k in ("id", "summary", "kind", "slice")).lower()
        if t in hay and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
        if len(out) >= limit:
            return out
    return out
