"""State that must agree: consistency across stores, repository signals.

A part of src/ao/lib.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


# ---- the stores agree with each other, and a repair is on the record (#47) --------------

REPAIR_CHAIN = "ao-repair-row-v1"


def archived_path(root):
    return os.path.join(root, ".ao", "ledger", "archived.jsonl")


def archived_artefacts(root):
    """{artefact: archive directory} for review artefacts ao moved out of the repository (#38, #47)."""
    from .storage import read_jsonl
    if not os.path.exists(archived_path(root)):
        return {}
    try:
        return {row["artefact"]: row.get("archive") for row in read_jsonl(archived_path(root))
                if isinstance(row, dict) and row.get("artefact")}
    except Exception:
        return {}


def record_archived(root, name, archive):
    from .storage import append_jsonl
    append_jsonl(archived_path(root), {"at": int(time.time()), "artefact": name, "archive": archive})


def _in_archive(root, reviews_dir, name):
    folder = os.path.join(HOME, ".ao", "archive", project_key(root))
    try:
        for entry in sorted(os.listdir(folder)):
            if entry.startswith(os.path.basename(os.path.normpath(reviews_dir)) + "-") \
                    and os.path.exists(os.path.join(folder, entry, name)):
                return os.path.join(folder, entry)
    except OSError:
        pass
    return None


def consistency_findings(root, cfg):
    """Every disagreement between the board, the ledgers, the review artefacts and git (#47).

    On 2026-09-07 the architect hand-edited three state files with no way for the
    tool to confirm the result was coherent. Each finding names both sides. A
    finding carries a repair only when the state is mechanical - a checkpoint for a
    ledger whose checkout is gone, a lock whose holder is dead, an archived review
    with no pointer to where it went; what was authorised, reviewed or verified is
    reported and never changed.
    """
    from .storage import _load_committed_lengths, read_chained_jsonl
    out = []

    def found(kind, text, repair=None):
        out.append({"kind": kind, "text": text, "repair": repair})

    reviews_dir = cfg.get("reviews", "semantic-review")
    try:
        grants = [row for row in authority_rows(root) if isinstance(row, dict) and row.get("granted") is True]
    except Exception as exc:
        grants = []
        found("authority", f"the authority ledger cannot be read: {exc}")
    try:
        from .storage import sealed_rows
        ledger = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
        verifications = {row.get("id") for row in list(read_chained_jsonl(
            ledger, VERIFICATION_CHAIN, legacy_prefix=True)) + sealed_rows(ledger) if isinstance(row, dict)}
    except Exception as exc:
        verifications = None
        found("verifications", f"the verification ledger cannot be read: {exc}")
    try:
        waivers = {row.get("id") for row in waiver_rows(root) if isinstance(row, dict)}
    except Exception as exc:
        waivers = None
        found("waivers", f"the waiver ledger cannot be read: {exc}")
    archived = archived_artefacts(root)
    for row in grants:
        token = row.get("token") or "a grant"
        review = row.get("review")
        if review and not os.path.exists(os.path.join(root, reviews_dir, review)) and review not in archived:
            found("grant-review", f"grant {token} rests on review {review}, which is neither in {reviews_dir}/ "
                                  "nor recorded as archived")
        if verifications is not None and row.get("verification") and row["verification"] not in verifications:
            found("grant-verification", f"grant {token} names verification {row['verification']}, which the "
                                        "verification ledger does not hold")
        if waivers is not None and row.get("waiver") and row["waiver"] not in waivers:
            found("grant-waiver", f"grant {token} stood on waiver {row['waiver']}, which the waiver ledger does not hold")
    try:
        review_rows = read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
    except Exception as exc:
        review_rows = []
        found("reviews", f"the review ledger cannot be read: {exc}")
    for row in review_rows:
        name = row.get("artefact") if isinstance(row, dict) else None
        if not name:
            continue
        path = os.path.join(root, reviews_dir, name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                if "sha256:" + hashlib.sha256(fh.read()).hexdigest() != row.get("sha256"):
                    found("review-bytes", f"{reviews_dir}/{name} is not the bytes its review row recorded")
        elif name not in archived:
            where = _in_archive(root, reviews_dir, name)
            if where:
                found("archive-pointer", f"{name} was moved to {where} and nothing records where",
                      repair=("archive-pointer", name, where))
            else:
                found("review-missing", f"{reviews_dir}/{name} has a review row and no file, here or archived")
    for state, items in board(root).items():
        for item in items:
            for key in ("landed", "commit"):
                sha = (item["notes"].get(key) or "").split(" ")[0].strip()
                if re.fullmatch(r"[0-9a-f]{7,40}", sha) and \
                        not git_text(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"):
                    found("board-commit", f"the board says {item['id']} {key} {sha}, which is not a commit here")
    for ledger in sorted(_load_committed_lengths()):
        checkout = os.path.dirname(os.path.dirname(os.path.dirname(ledger)))
        if os.path.basename(os.path.dirname(ledger)) == "ledger" and not os.path.isdir(checkout):
            found("checkpoint", f"a committed length is recorded for {ledger}, whose checkout is gone",
                  repair=("checkpoint", ledger))
    lock = architect_lock_path(root)
    try:
        with open(lock, encoding=UTF8) as fh:
            holder = json.load(fh)
    except (OSError, ValueError):
        holder = None
    if isinstance(holder, dict) and holder.get("pid") and not _pid_alive(holder["pid"]):
        found("architect-lock", f"the architect lock names pid {holder['pid']}, which is not running",
              repair=("architect-lock", lock))
    return out


def repair_consistency(root, findings):
    """Apply only the mechanical repairs, and record each in .ao/ledger/repairs.jsonl (#47).

    A repair is itself evidence. It never touches the authority, verification,
    review or waiver ledgers.
    """
    from .storage import _exclusive_lock, _load_committed_lengths, append_chained_jsonl, checkpoint_path
    done = []
    for finding in findings:
        repair = finding.get("repair")
        if not repair:
            continue
        kind = repair[0]
        if kind == "checkpoint":
            store = checkpoint_path()
            with _exclusive_lock(store + ".lock"):
                data = _load_committed_lengths()
                if repair[1] not in data:
                    continue
                del data[repair[1]]
                temporary = f"{store}.{os.getpid()}.tmp"
                with open(temporary, "w", encoding=UTF8) as fh:
                    json.dump(data, fh, indent=1, sort_keys=True)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temporary, store)
        elif kind == "architect-lock":
            try:
                os.remove(repair[1])
            except FileNotFoundError:
                continue
        elif kind == "archive-pointer":
            record_archived(root, repair[1], repair[2])
        else:
            continue
        append_chained_jsonl(os.path.join(root, ".ao", "ledger", "repairs.jsonl"),
                             {"at": int(time.time()), "kind": kind, "target": list(repair[1:]),
                              "finding": finding["text"]}, REPAIR_CHAIN)
        done.append(finding)
    return done


def grant_artefacts_at_risk(root, cfg):
    """(name, "untracked" or "missing") for each review a grant rests on that git does not hold (#38)."""
    granted = sorted({row["review"] for row in authority_rows(root)
                      if isinstance(row, dict) and row.get("granted") is True and row.get("review")})
    if not granted:
        return []
    tracked = tracked_review_names(root, cfg["reviews"])
    directory = os.path.join(root, cfg["reviews"])
    return [(name, "untracked" if os.path.exists(os.path.join(directory, name)) else "missing")
            for name in granted if name not in tracked]


def candidate_review_decision(root, review_dir, candidate_digest):
    """What the newest recorded review of one candidate decides (#63).

    Returns ``{"match": (name, verdict, body, evidence) or None, "problem": text or None}``.
    Reviews are taken in the order ao recorded them in the chained review ledger,
    never by file time, which anyone can touch. A review that did not take place
    (UNAVAILABLE) masks nothing. Otherwise the newest review of the candidate
    decides: a missing, unreadable, emptied or rewritten artefact is a refusal and
    never a step back to an older approval, and a fallback's approval cannot
    supersede a completed rejection of the same candidate.
    """
    from .storage import read_chained_jsonl
    rows = [row for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
            if isinstance(row, dict) and row.get("kind") == "index-candidate"
            and row.get("candidate") == candidate_digest]
    for position in range(len(rows) - 1, -1, -1):
        row = rows[position]
        if row.get("verdict") == "UNAVAILABLE":
            continue
        name = row.get("artefact")
        try:
            with open(os.path.join(root, review_dir, str(name)), "rb") as fh:
                data = fh.read()
        except OSError:
            return {"match": None,
                    "problem": f"the newest review of this candidate, {name}, cannot be read"}
        if "sha256:" + hashlib.sha256(data).hexdigest() != row.get("sha256"):
            return {"match": None,
                    "problem": f"the newest review of this candidate, {name}, is not the file "
                               "ao recorded: emptied, cut short or rewritten"}
        body = data.decode(UTF8, "replace")
        evidence = review_evidence(body)
        verdict = _review_verdict(body)
        if isinstance(evidence, dict) and "verdict" in evidence and evidence["verdict"] != verdict:
            verdict = "INVALID"
        if verdict != "APPROVED" or not isinstance(evidence, dict) \
                or evidence.get("authorizable") is not True:
            return {"match": None, "problem": None}
        rejection = next((earlier for earlier in reversed(rows[:position])
                          if earlier.get("verdict") == "NEEDS_CHANGES"), None)
        if row.get("fallback") and rejection:
            return {"match": None,
                    "problem": f"{name} is a fallback reviewer's approval; it cannot supersede "
                               f"the rejection of this candidate in {rejection.get('artefact')}"}
        return {"match": (name, verdict, body, evidence), "problem": None}
    return {"match": None, "problem": None}


def range_review_verdict(root, commits, since=0):
    """The verdict of a retrospective review of exactly this range, recorded after `since` rows, or None."""
    from .storage import read_chained_jsonl
    rows = read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
    for row in reversed(rows[since:]):
        if isinstance(row, dict) and row.get("kind") == "commit-range" \
                and row.get("commits") == commits:
            return row.get("verdict")
    return None


def review_row_count(root):
    from .storage import read_chained_jsonl
    return len(read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN))


def latest_candidate_review(root, review_dir, candidate_digest, limit=40):
    """Return approval only when the newest recorded review of the candidate approves."""
    return candidate_review_decision(root, review_dir, candidate_digest)["match"]


AUTHORITY_CHAIN = "ao-authority-row-v1"


def authority_rows(root):
    """All committed authority rows after validating the complete hash chain.

    A non-empty legacy ledger without predecessor fields is deliberately
    unreadable. Rewriting old authority in place would turn an append-only audit
    trail into evidence manufactured by the reader.
    """
    from .storage import read_chained_jsonl
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    return read_chained_jsonl(path, AUTHORITY_CHAIN)


def latest_authority_decision(root):
    """Newest real boolean decision, only after the full chain validates."""
    rows = authority_rows(root)
    return next(
        (row for row in reversed(rows)
         if isinstance(row, dict) and type(row.get("granted")) is bool),
        None,
    )


VERIFICATION_CHAIN = "ao-verification-row-v1"


GATE_SUMMARY_TAIL_LINES = 30


def gate_counts(output, summary=None):
    """Pass and fail counts from the summary a test runner ends with, or None (#70).

    Only the last lines are read, and the last summary in them counts: a test name,
    a log line or a fixture the implementer wrote can print "# pass 900" anywhere
    earlier. A gate can name its runner's summary with `summary`, a regex with `pass`
    and `fail` groups. Nothing found is None - unparsed, never a number.
    """
    tail = "\n".join(str(output or "").splitlines()[-GATE_SUMMARY_TAIL_LINES:])
    if summary:
        try:
            found = list(re.finditer(summary, tail, re.M))
        except re.error:
            return None
        if not found:
            return None
        groups = found[-1].groupdict()
        try:
            return int(groups.get("pass") or 0), int(groups.get("fail") or 0)
        except ValueError:
            return None
    # node --test closes with "# pass N" / "# fail N" (TAP) or "ℹ pass N" / "ℹ fail N".
    for mark in ("#", "ℹ"):
        passes = re.findall(rf"^{mark} pass (\d+)[ \t]*$", tail, re.M)
        fails = re.findall(rf"^{mark} fail (\d+)[ \t]*$", tail, re.M)
        if passes and fails:
            return int(passes[-1]), int(fails[-1])
    # pytest closes with "3 failed, 461 passed, 2 skipped in 12.30s".
    closing = re.findall(r"^=*[ \t]*((?:\d+ [a-z]+(?:, )?)+) in [\d.]+s\b", tail, re.M)
    if closing:
        counts = {word: int(n) for n, word in re.findall(r"(\d+) ([a-z]+)", closing[-1])}
        if "passed" in counts or "failed" in counts:
            return (counts.get("passed", 0),
                    counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0))
    return None


def gate_summary_line(output, summary=None):
    """The closing line gate_counts read its numbers from, or None (#6).

    Kept in the verification beside the exit code, so a report can quote what the
    runner said rather than a number the reporter typed.
    """
    tail = str(output or "").splitlines()[-GATE_SUMMARY_TAIL_LINES:]
    if summary:
        try:
            found = [line for line in tail if re.search(summary, line)]
        except re.error:
            return None
        return found[-1].strip()[:200] if found else None
    for line in reversed(tail):
        text = line.strip()
        if re.match(r"^[#ℹ] (pass|fail) \d+$", text) or \
                re.match(r"^=*[ \t]*(?:\d+ [a-z]+(?:, )?)+ in [\d.]+s\b", text):
            return text.strip("= ")[:200]
    return None


def verification_evidence(root):
    """One line naming the newest verification: its id, each gate's exit and closing line (#6)."""
    try:
        record = latest_verification(root)
    except Exception:
        return None
    if not record:
        return None
    gates = [f"{g.get('name')} exit {g.get('exit')}" + (f" ({g['summary']})" if g.get("summary") else "")
             for g in record.get("gates") or [] if isinstance(g, dict)]
    return (f"{record.get('id')} {'passed' if record.get('passed') else 'FAILED'}: "
            + ("; ".join(gates) if gates else "no gates"))


_GREEN_CLAIM = re.compile(r"\b\d+\s+passed\b|\ball\s+(?:tests|gates|checks)\s+(?:pass|passed|green)\b|"
                          r"\bgreen\b|\byeşil\b|\bgeçti\b|\bgeçiyor\b", re.I)
_RED_ADMISSION = re.compile(r"\b[1-9]\d*\s+(?:failed|errors?)\b|\bFAIL(?:ED)?\b|\bkırmızı\b|\bkaldı\b")


def report_inconsistency(root, text):
    """Why a report that claims green contradicts the newest verification, or None (#6).

    A report said "159 passed" while the suite had exited 1. The claim is read from
    the report's words; the fact from the verification ledger. A report that admits
    a failure, or a project with no verification, is not inconsistent.
    """
    body = str(text or "")
    if not _GREEN_CLAIM.search(body) or _RED_ADMISSION.search(body):
        return None
    try:
        record = latest_verification(root)
    except Exception:
        return None
    if not record or record.get("passed") is not False:
        return None
    failed = [f"{g.get('name')} exit {g.get('exit')}" for g in record.get("gates") or []
              if isinstance(g, dict) and not g.get("passed")]
    return (f"the report claims green, but the newest verification {record.get('id')} failed"
            + (f": {', '.join(failed)}" if failed else ""))


def gate_definitions_digest_of(spec, profile):
    """Canonical digest of the gate definitions one profile runs, or None when it has none (#61).

    `.ao/gates.json` is a file the implementer can write. A verification that does
    not name the definitions it ran lets a gate weakened after the run inherit the
    pass measured under the stronger one.
    """
    try:
        ran = [[name, spec["gates"][name]] for name in spec["profiles"][profile]]
        canonical = json.dumps({"profile": profile, "gates": ran}, ensure_ascii=True,
                               sort_keys=True, separators=(",", ":"))
    except (KeyError, TypeError, ValueError):
        return None
    return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def gate_definitions_digest(root, profile):
    """The digest of the definitions in force now, read from `.ao/gates.json`."""
    try:
        with open(os.path.join(root, ".ao", "gates.json"), encoding=UTF8) as fh:
            spec = json.load(fh)
    except (OSError, ValueError):
        return None
    return gate_definitions_digest_of(spec, profile)


def latest_verification(root):
    """The newest complete, chained `ao verify` record, or None.

    The ledger is chained like the authority ledger (#61), so a row appended
    without a valid link makes it unreadable. Rows written before it was chained
    carry no link; they stay readable as history and never count.
    """
    from .storage import CHAIN_PREVIOUS_FIELD, read_chained_jsonl
    rows = read_chained_jsonl(os.path.join(root, ".ao", "ledger", "verifications.jsonl"),
                              VERIFICATION_CHAIN, legacy_prefix=True)
    chained = [row for row in rows if CHAIN_PREVIOUS_FIELD in row]
    return chained[-1] if chained else None


def verification_by_id(root, vid):
    """One verification row by id, from the ledger or the rows a seal retired from it (#50)."""
    from .storage import read_chained_jsonl, sealed_rows
    path = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    for row in list(read_chained_jsonl(path, VERIFICATION_CHAIN, legacy_prefix=True)) + sealed_rows(path):
        if isinstance(row, dict) and row.get("id") == vid:
            return row
    return None


def record_verification(root, record):
    """Durably append one chained verification before reporting its result."""
    from .storage import append_chained_jsonl
    path = os.path.join(root, ".ao", "ledger", "verifications.jsonl")
    return append_chained_jsonl(path, scan_record(record), VERIFICATION_CHAIN, legacy_prefix=True)


def _granted_trees(root):
    """The index trees authority was granted for, and the commit the first grant built on."""
    rows = [row for row in authority_rows(root)
            if isinstance(row, dict) and row.get("granted") is True]
    trees = {(row.get("candidate") or {}).get("index_tree") for row in rows}
    trees.discard(None)
    base = next(((row.get("candidate") or {}).get("head") for row in rows
                 if (row.get("candidate") or {}).get("head")), None)
    return trees, base, rows


def landed_commit_problem(root, commit="HEAD"):
    """Why a landed commit is not the tree its grant bound, or None when it is (#64).

    commit-check measures the index when the pre-commit hook runs, and Git writes
    the commit's tree from the index after hooks return, so a process that stages
    a path in between lands it inside an authorised commit. A hook cannot prevent
    that; comparing the tree that landed with the tree that was granted detects it.
    """
    try:
        sha = _git_output(root, "rev-parse", "--verify", commit).decode("ascii").strip()
        tree = _git_output(root, "rev-parse", "--verify", f"{commit}^{{tree}}").decode("ascii").strip()
    except (RuntimeError, UnicodeError) as exc:
        return f"cannot read {commit}: {exc}"
    _, _, rows = _granted_trees(root)
    grant = rows[-1] if rows else None
    granted = ((grant or {}).get("candidate") or {}).get("index_tree")
    if not granted:
        return f"commit {sha[:12]} landed with no grant on record"
    if granted != tree:
        return (f"commit {sha[:12]} landed tree {tree[:12]}, but grant "
                f"{grant.get('token') or '<unnamed>'} bound tree {granted[:12]}")
    return None


def commits_without_grant(root, limit=50):
    """Landed commits, newest first, whose tree no grant bound (#64).

    Only commits on top of the one the first grant was built on are measured;
    history from before the authority ledger has nothing to be compared with.
    Merges are left out: their trees are Git's, not a candidate's.
    """
    trees, base, _ = _granted_trees(root)
    if base is None:
        return []
    try:
        log = _git_output(root, "log", f"--max-count={int(limit)}", "--no-merges",
                          "--format=%H %T", f"{base}..HEAD", "--").decode("ascii")
    except (RuntimeError, UnicodeError):
        return []
    return [sha for sha, tree in (line.split() for line in log.splitlines() if line.strip())
            if tree not in trees]


def record_authority(root, granted, reasons, tree, verification, token=None,
                     review=None, reviewer=None, candidate=None, scope=None,
                     matrix=None, role_bindings=None, implementer_identity=None,
                     reviewer_identity=None, waiver=None):
    """Persist one hash-chained authority decision, raising on any broken prefix."""
    from .storage import append_chained_jsonl
    record = {"at": int(time.time()), "granted": bool(granted),
              "token": token, "reasons": reasons, "tree": tree,
              "verification": verification, "review": review,
              "reviewer": reviewer}
    if matrix is not None:
        record.update({
            "schema": 3,
            "candidate": candidate,
            "scope": scope,
            "matrix": matrix,
            "role_bindings": role_bindings,
            "implementer_identity": implementer_identity,
            "reviewer_identity": reviewer_identity,
        })
    elif candidate is not None:
        record.update({"schema": 2, "candidate": candidate, "scope": scope})
    if waiver is not None:
        # The waiver a grant stood on; it covers no other candidate after this (#67).
        record["waiver"] = waiver
    path = os.path.join(root, ".ao", "ledger", "authority.jsonl")
    # Keep the reviewer's identity here, not only in the review file. A grant is
    # not real until the chain prefix validates and the locked append, file
    # fsync and (on first creation) directory fsync have all completed.
    return append_chained_jsonl(path, record, AUTHORITY_CHAIN)


GATE_LOCK = os.path.join(HOME, ".ao", "gate.lock")


def gate_lock_holder():
    """Which project is running its gates right now, if any."""
    if not os.path.exists(GATE_LOCK):
        return None
    try:
        st = json.load(open(GATE_LOCK, encoding=UTF8))
    except Exception:
        return None
    if not _pid_alive(st.get("pid", -1)):            # stale lock from a killed run
        try:
            os.remove(GATE_LOCK)
        except OSError:
            pass
        return None
    st["minutes"] = int((time.time() - st.get("at", time.time())) / 60)
    return st


def acquire_gate_lock(root, timeout=0):
    """Serialise the expensive work across every project on this machine.

    Roles split who decides; this splits who spends the machine. Each project's
    watchdog is independent, so without a machine-wide lock N projects run N test
    suites at once — and on a shared laptop that is not N times the throughput, it
    is one suite that no longer finishes. This project watched an agent burn five
    review rounds walking a concurrency setting down from 8 to 1 while fighting
    exactly that.

    Advisory and best-effort: a lock nobody can steal becomes a lock that wedges
    the machine, so a holder whose process is gone is cleared on sight.
    """
    os.makedirs(os.path.dirname(GATE_LOCK), exist_ok=True)
    deadline = time.time() + timeout
    while True:
        holder = gate_lock_holder()
        if not holder:
            try:
                fd = os.open(GATE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as fh:
                    json.dump({"root": root, "pid": os.getpid(),
                               "at": int(time.time())}, fh)
                return True
            except FileExistsError:
                pass
        if time.time() >= deadline:
            return False
        time.sleep(2)


def release_gate_lock():
    holder = gate_lock_holder()
    if holder and holder.get("pid") == os.getpid():
        try:
            os.remove(GATE_LOCK)
        except OSError:
            pass


def process_trees(pids, parent=None):
    """Group pids into independent trees — how many *turns*, not how many processes.

    One turn is several processes: a wrapper spawns a runtime which spawns
    children. Counting processes therefore reports four concurrent writers where
    there is one, and an alarm that fires on normal operation is an alarm people
    learn to ignore. Count only the roots: a pid whose parent is not itself in the
    set.
    """
    if not pids:
        return []
    if parent is None:
        parent = {}
        for line in (sh("ps -eo pid,ppid") or "").split("\n")[1:]:
            f = line.split()
            if len(f) >= 2 and f[0].isdigit() and f[1].isdigit():
                parent[int(f[0])] = int(f[1])
    known = set(pids)
    return sorted(p for p in pids if parent.get(p) not in known)


def anomalies(root, cfg, adapter, age, idle_seconds, exclude_pids=()):
    """Conditions a watchdog cannot resolve, reported as facts rather than verdicts.

    A guard chain is good at mechanical questions — is a turn running, is there
    quota, has this nudge already failed. It is bad at everything else, and the
    failures of this project came from letting it try: it decided a provider
    outage was a stuck agent, decided a hung process was a live writer, decided a
    finished slice was work in progress. Each decision was defensible from the one
    signal it had and wrong given the others.

    So it stops deciding. It collects what it can see and hands that to the
    architect, who has the context to weigh it. Crucially the facts carry no
    conclusion — "four processes, transcript moved 12s ago, HEAD unchanged for
    three hours" is useful; "there is a concurrent writer" is the guess that cost
    seven hours the last time something made it.
    """
    out = []
    groups = {}

    # An explicit request outranks every heuristic here. When the implementer
    # writes to the architect it has already decided it is blocked, and waiting
    # for a detector to independently notice is absurd — this project spent half a
    # day doing exactly that while a message saying "decision required" sat
    # unread. Fire on the next cycle, not after a threshold.
    for m in mailbox(root, cfg.get("mailbox", "agent-mail")):
        if not to_architect(m, cfg):
            continue
        # The watchdog must not read its own outbox as an inbox. Its anomaly
        # reports are addressed to the architect ("watchdog-to-<architect>-…"), so they
        # matched this filter and were re-escalated as fresh "report-waiting"
        # anomalies — each new report's name concatenating the last, a runaway
        # that filled the mailbox with names hundreds of characters long. What
        # needs a decision is what the implementer or a human sent, never what
        # this detector emitted.
        # The hunter's leads wait for triage; they are not the implementer asking (#45).
        if m.startswith(("watchdog-to-", "hunter-to-")) or "-watchdog-to-" in m or "-hunter-to-" in m:
            continue
        # The architect's notes to itself are not the implementer asking (#18).
        if from_architect(m, cfg):
            continue
        try:
            body = open(os.path.join(root, cfg.get("mailbox", "agent-mail"), m),
                        errors="replace", encoding=UTF8).read(4000)
        except OSError:
            continue
        first = next((l for l in body.split("\n") if l.strip().startswith("#")), m)
        # "I am blocked" and "I finished" both want the architect eventually, but
        # only one of them stops work. Treating a completion report as urgent is
        # how an urgent channel becomes background noise.
        # Match structure, not substrings. The implementer's report template ends
        # with a "Blockers:" line on every message, including "Blockers: none", so
        # a bare `"blocker" in body` test classified every routine status report
        # as urgent and re-raised it every ten minutes: twenty-eight false alarms
        # in one hour, produced by a line that said the opposite of what matched.
        low = body.lower()
        asking = any(h in low for h in ("## karar gerekli", "## acil",
                                        "## decision required", "## urgent",
                                        "## blocked"))
        if not asking:
            for line in low.split("\n"):
                t = line.strip().lstrip("-*# ").strip()
                if not t.startswith(("blockers:", "blocker:", "engel:", "engeller:")):
                    continue
                value = t.split(":", 1)[1].strip(" .`")
                # An empty value, or one that opens by saying there are none, is
                # the template reporting health — not a request for anything.
                asking = bool(value) and not value.startswith(
                    ("none", "no ", "yok", "-", "n/a", "hiç"))
                break
        kind = "decision-requested" if asking else "report-waiting"
        contradiction = report_inconsistency(root, body)
        if contradiction:
            out.append({"kind": "inconsistent-report", "key": m,
                        "facts": [f"{m}: {contradiction}"]})
        g = groups.setdefault(kind, {"n": 0, "first": m})
        g["n"] += 1
        g["latest"] = m
        g["title"] = first.lstrip("# ").strip()[:200]
    # A review that came back and nobody took is a slice left unattended (#28, W1).
    waited = settings.get(cfg, "review.unhandled_minutes") * 60
    for state in returned_reviews(root):
        age = time.time() - float(state.get("finished_at") or time.time())
        if age >= waited:
            out.append({"kind": "review-returned", "key": state["id"],
                        "facts": [f"{state['id']} for slice {state.get('slice')} returned "
                                  f"{state.get('verdict') or state.get('state')} {int(age / 60)}m ago and "
                                  "has not been collected"]})
    # A question asked with `ao ask` wants the architect as much as a report does,
    # and may have no mail at all (#20). Each open one is its own anomaly.
    for decision in decisions(root, "open"):
        asked = decision.get("asked_at") or 0
        out.append({"kind": "decision-requested", "key": decision.get("id"),
                    "facts": [f"{decision.get('id')} is open since "
                              f"{time.strftime('%d %b %H:%M', time.localtime(asked)) if asked else '?'}",
                              str(decision.get("question") or "")[:200]]})
    # One anomaly per kind, however many reports carry it. Eighty "queue empty"
    # reports in eleven hours became eighty anomaly files and forty wake attempts;
    # the architect needed one line saying "eighty, since 06:31".
    for kind, g in groups.items():
        since = g["first"][:13] if re.match(r"\d{8}-\d{4}", g["first"]) else g["first"][:20]
        head = f"the implementer wrote {g['latest']}"
        if g["n"] > 1:
            head += f" — {g['n']} report(s) of this kind standing, the first since {since}"
        out.append({"kind": kind, "key": "implementer",
                    "facts": [head, g["title"],
                              "an explicit request — not a symptom needing corroboration"
                              if kind == "decision-requested" else "a report, not a blocker"]})

    # Exclude pids the caller knows are not implementer writers — above all the
    # architect the watchdog itself spawned, which resumes with this repo as its
    # cwd and would otherwise read as a second turn. The watchdog knows its pid;
    # the detector should not have to guess.
    pids = [p for p in agent_pids(root, adapter) if p not in set(exclude_pids)]
    dirty = len([l for l in sh("git status --porcelain", cwd=root).split("\n") if l.strip()])
    head = sh("git rev-parse --short HEAD", cwd=root)

    # What a finished turn left behind is not a turn. Orphans are cleared by the
    # watchdog before it counts; here they are simply not counted.
    trees = process_trees([p for p in pids if p not in set(orphans(root, adapter))])
    if len(trees) > 1 and age < idle_seconds:
        out.append({"kind": "several-turns-active", "roots": trees,
                    "facts": [f"{len(trees)} independent process trees with this repo as "
                              f"cwd, roots {trees} (of {len(pids)} processes)",
                              f"transcript last written {int(age)}s ago",
                              "separate roots mean separate turns, not one turn's children"]})
    spin = spinning(root)
    if spin:
        out.append({"kind": "busy-without-progress",
                    "facts": [f"transcript growing for {spin}m",
                              f"HEAD unchanged at {head}", f"{dirty} files dirty, unchanged"]})
    rn = rounds(root, cfg["reviews"])
    budget = settings.get(cfg, "round_budget")
    if rn > budget:
        revs = reviews(root, cfg["reviews"], limit=3)
        out.append({"kind": "over-round-budget",
                    "facts": [f"round {rn} of {budget} on the current slice"] +
                             [f"{f}: {v}" for f, v in revs]})
    # The same finding returning review after review. A round count cannot see
    # it; this is the actual shape of a slice that is not converging.
    loops = review_loop(root, cfg.get("reviews", "semantic-review"))
    if loops:
        out.append({"kind": "review-loop",
                    "facts": [f"[{l['sev']}] {l['file']} — \"{l['clause']}\" has come back "
                              f"{l['count']} reviews running" for l in loops[:4]] +
                             ["more rounds will not converge this; it needs re-specifying "
                              "or a different actor"]})
    err = last_nudge_error(root)
    if err and time.time() - err.get("at", 0) < 3600:
        out.append({"kind": "restart-failed",
                    "facts": [f"exit {err.get('code')} "
                              f"{int((time.time() - err.get('at', 0)) / 60)}m ago",
                              (err.get("tail") or "")[:300]]})
    return out


def write_report(root, cfg, kind, facts, key=None):
    """A watchdog-to-architect message: observations, no interpretation.

    Deduplicated by what the anomaly is *about*, not by wall-clock time. The name
    once carried a minute stamp, so the exists-guard only caught collisions inside
    the same minute and a standing condition produced a fresh file every cycle —
    twenty-five copies of one unread status report in an hour. Key the filename on
    (kind, source) instead: while an anomaly for that pair sits unprocessed, no
    second one is written. The architect deleting it is what re-arms the report,
    which is correct — a condition that recurs after it was judged is genuinely
    new.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    try:
        os.makedirs(box, exist_ok=True)
        # A stable key from the source the facts name (e.g. "the implementer wrote
        # X.md"), falling back to the kind alone for anomalies with no single
        # source. This is what makes the exists-guard actually guard.
        src = "-" + re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-") if key else ""
        for f in [] if key else facts:
            mrk = re.search(r"wrote\s+(\S+\.md)", f)
            if mrk:
                src = "-" + re.sub(r"[^A-Za-z0-9]+", "-", mrk.group(1)[:-3]).strip("-")
                break
        _, arch = mail_names(cfg)
        name = f"watchdog-to-{arch}-ANOMALY-{kind}{src}.md"
        path = os.path.join(box, name)
        if os.path.exists(path):
            return None
        text = (f"# ANOMALY — {kind}\n\n"
                f"Watchdog observation at {time.strftime('%Y-%m-%d %H:%M:%S')}. "
                f"Facts only; the watchdog draws no conclusion and took no action "
                f"beyond standing down.\n\n"
                + "".join(f"- {f}\n" for f in facts)
                + "\n## Asked of the architect\n\n"
                "Decide whether this needs intervention, and what. If it is normal, "
                "delete this message; if not, act and record what you did.\n")
        return write_mail(root, cfg, name, text, {"kind": "anomaly", "from": "watchdog", "to": arch})
    except OSError:
        return None


def usage_api():
    """The first shipped adapter's account lookup that ao has a driver for, or {} (#76)."""
    from . import drivers
    for _, adapter in sorted(package_adapters().items()):
        api = (adapter.get("billing") or {}).get("api") or {}
        if api.get("driver") in drivers.USAGE:
            return api
    return {}


def account_usage(timeout=20):
    """Real usage from the provider, through the driver an adapter's billing names; None when none can.

    The protocol lives in drivers.py and every path, key, command and endpoint in
    the adapter, so this core function names no harness (#76).
    """
    from . import drivers
    api = usage_api()
    return drivers.USAGE[api["driver"]](api, timeout=timeout) if api else None


def credit_usage(monthly_budget=None):
    """Credit spend read from local transcripts, by billing month.

    There is no endpoint for this. A Kiro API key authenticates the CLI
    (`KIRO_API_KEY`); it is not a REST credential, and the published docs
    describe no usage or quota route. The dashboard in the app is the authority.

    Locally, each session writes `usage_summary` records carrying
    `{unit: "credit", usage: <float>}`. The values climb and then drop, because a
    record reports the running total *of the turn in progress* and a drop means a
    new turn began. So a turn costs the peak it reached, and a session costs the
    sum of those peaks.

    Two simpler readings are wrong by large factors and both were tried first:
    summing every record counts each turn once per progress update (30x high),
    and taking only the final record counts one turn per session (40x low). The
    peaks reading was confirmed against a known 10,000/month allowance — the
    month of heaviest use came to 10,148, where the others gave 13,168 and 258.
    """
    import glob
    from collections import defaultdict
    months, days, sessions = defaultdict(float), defaultdict(float), []
    patterns = [_home_path(((adapter.get("billing") or {}).get("fallback") or {}).get("transcripts"))
                for _, adapter in sorted(package_adapters().items())
                if ((adapter.get("billing") or {}).get("fallback") or {}).get("transcripts")]
    for f in [path for pattern in patterns for path in glob.glob(pattern)]:
        peaks, cur, month, turns = 0.0, 0.0, "", 0
        cur_day = ""
        try:
            with open(f, errors="replace", encoding=UTF8) as fh:
                for line in fh:
                    if '"promptTurnSummaries"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    pl = rec.get("payload", rec)
                    if pl.get("type") != "usage_summary":
                        continue
                    v = sum(x.get("usage", 0) for x in (pl.get("promptTurnSummaries") or [])
                            if isinstance(x.get("usage"), (int, float)))
                    ts = (rec.get("timestamp") or "")
                    if v < cur:                  # dropped: the previous turn ended at cur
                        peaks += cur
                        turns += 1
                        # Attribute the turn to the day it ran, not to whenever the
                        # session was last touched. A long session crosses billing
                        # periods, and charging all of it to the final period is how
                        # a month reads as full while the previous one reads as empty.
                        days[cur_day or ts[:10]] += cur
                    cur, cur_day = v, ts[:10]
                    month = ts[:7]
        except OSError:
            continue
        if cur or peaks:
            peaks += cur
            turns += 1
            days[cur_day or month + "-01"] += cur
            months[month] += peaks
            sessions.append({"session": os.path.basename(os.path.dirname(f)),
                             "month": month, "turns": turns, "credits": round(peaks, 2),
                             "mtime": os.path.getmtime(f)})
    sessions.sort(key=lambda r: r["mtime"], reverse=True)
    this_month = time.strftime("%Y-%m")
    # Billing periods are not calendar months — a subscription renews on its own
    # day — so return the daily series and let the caller cut it wherever the
    # user's period actually starts.
    return {"sessions": sessions, "days": {d: round(v, 2) for d, v in sorted(days.items())},
            "months": {m: round(v, 2) for m, v in sorted(months.items())},
            "this_month": round(months.get(this_month, 0.0), 2),
            "budget": monthly_budget,
            "remaining": (round(monthly_budget - months.get(this_month, 0.0), 2)
                          if monthly_budget else None)}


URGENT_MARKERS = ("## ACİL", "## URGENT", "## DUR", "## STOP")


ROLES = ("implementer", "architect")


def invoking_role():
    """The role this ao process runs for, from AO_ROLE, or None for a person or an unknown caller (#29)."""
    role = (os.environ.get("AO_ROLE") or "").strip().lower()
    return role if role in ROLES else None


def returned_reviews(root):
    """Submitted reviews that have ended and that nobody has collected yet (#28)."""
    directory = os.path.join(root, ".ao", "reviews")
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    out = []
    for name in names:
        if not (name.startswith("R-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(directory, name), encoding=UTF8) as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and state.get("state") not in (None, "running") \
                and not state.get("collected_at"):
            out.append(state)
    return out


def urgent_messages(root, cfg, role="implementer"):
    """Unacknowledged messages the implementer must see before it does anything big.

    MCP cannot interrupt. Its tools fire only when the agent chooses to call them,
    so an urgent message sits unread until the agent's next `ao_inbox` — which may
    be a turn away, or never if it is stuck. A2A agents get an inbound push; an
    MCP-only agent does not, and no amount of protocol design changes that.

    What we do control is the boundary the agent crosses on its own: it runs `ao`
    to take the machine lock, to verify, and to ask whether it may commit. Those
    are precisely the moments before something expensive or irreversible, which is
    exactly when an urgent message needs to land. So the CLI carries it.

    Marked messages only. Everything routine waits for `ao_inbox`, or the channel
    becomes noise and gets skimmed — which is how it fails.

    Urgency is resolved for a role, not hardcoded to the implementer (#29): mail
    addressed to the architect's role is the architect's, the rest the
    implementer's, and role None takes both. An implementer report marked urgent
    sat unread for four hours because this skipped everything bound for the architect.
    """
    box = cfg.get("mailbox", "agent-mail")
    out = []
    for m in mailbox(root, box):
        addressed = "architect" if to_architect(m, cfg) else "implementer"
        if role and addressed != role:
            continue
        try:
            body = open(os.path.join(root, box, m), errors="replace", encoding=UTF8).read(8000)
        except OSError:
            continue
        upper = body.upper()
        if any(k.upper() in upper for k in URGENT_MARKERS):
            title = next((l for l in body.split("\n") if l.strip().startswith("# ")), m)
            out.append({"id": m, "title": title.lstrip("# ").strip()[:120], "body": body,
                        "to": addressed})
    return out


def escaped_cwd_dir(store, cwd):
    """The directory an escaped-cwd store keeps a working directory's sessions in (#71, #76).

    On macOS and Linux the name is the absolute path with "/" and "." made
    dashes. ao built it that way everywhere, and on Windows `C:\\repo` kept its
    drive: os.path.join took it as an absolute path, dropped the store's directory,
    and the watchdog found no transcript and ended every cycle there. On Windows
    every character outside letters, digits and dashes is made a dash. A
    directory that already exists under either name is used as found.
    """
    base = _home_path((store or {}).get("dir"))
    cwd = str(cwd or "")
    posix = cwd.replace("/", "-").replace(".", "-")
    portable = re.sub(r"[^A-Za-z0-9-]", "-", cwd)
    for name in (posix, portable):
        if name and "/" not in name and "\\" not in name and ":" not in name \
                and os.path.isdir(os.path.join(base, name)):
            return os.path.join(base, name)
    return os.path.join(base, portable if os.name == "nt" else posix)


def unplaced_agent_pids(root, adapter):
    """Agent processes that may be working in this tree but cannot be placed (#71).

    Windows exposes no process working directory, and no shipped adapter's command
    line names the repository, so there a turn in this tree was no writer at all:
    `ao hold` stopped nothing and the watchdog started a second turn. The guards
    that must not miss a writer count these and say why. Where a process's
    directory can be read this is empty.
    """
    if os.name != "nt":
        return []
    from . import procs
    names = set()
    for key in ("send", "resume"):
        argv = (adapter.get(key) or {}).get("argv") or []
        if argv:
            names.add(os.path.basename(argv[0]))
    names.update(agent_process_names())
    want = os.path.realpath(root)
    me = os.getpid()
    helpers = helper_pids(root)
    table = _proc_table() if helpers else {}

    def under_helper(pid):
        seen, current = set(), pid
        while current > 1 and current not in seen:
            if current in helpers:
                return True
            seen.add(current)
            current = table.get(current, (0, 0, ""))[0]
        return False

    out = []
    for pid in procs.all_pids():
        if pid == me:
            continue
        av = procs.argv(pid)
        if not av or not _is_agent_process(pid, names, av) or procs.cwd(pid) is not None:
            continue
        if any(os.path.isabs(a) and os.path.realpath(a.rstrip("/\\")) == want for a in av):
            continue                      # it names this tree: agent_pids placed it
        if helpers and under_helper(pid):
            continue
        out.append(pid)
    return sorted(out)


def git_text(root, *args, timeout=60):
    """git's output as text without a shell, or "" when git fails (#71).

    cmd.exe treats neither single quotes nor `2>/dev/null` as a POSIX shell does:
    the quoted `--pretty` format was split at its "|" and the log came back empty.
    """
    try:
        return _git_output(root, *args, timeout=timeout).decode(UTF8, "replace").strip()
    except RuntimeError:
        return ""


def discover_architect(cwd):
    """The newest session an escaped-cwd store keeps for a directory, by transcript mtime.

    Pinning a session id in config goes stale the moment the human opens a new
    conversation, and a watchdog that wakes a dead session fails silently — the
    worst shape of failure, because everything still looks configured. Resolve it
    from disk instead, the same way the implementer's session is resolved.
    """
    # The store flattens the path into a directory name - a worktree under a
    # dot-directory gets a doubled dash where "/." was - and on Windows the drive
    # and backslashes go the same way (#71).
    best, best_mt, best_path = None, 0, None
    for _, store in session_stores("escaped-cwd"):
        d = escaped_cwd_dir(store, cwd)
        if not os.path.isdir(d):
            continue
        suffix = store["transcript"].replace("{session}", "")
        for f in os.listdir(d):
            if not f.endswith(suffix):
                continue
            mt = os.path.getmtime(os.path.join(d, f))
            if mt > best_mt:
                best, best_mt, best_path = f[:-len(suffix)], mt, os.path.join(d, f)
    return {"session": best, "transcript": best_path, "age": int(time.time() - best_mt)} if best else None


def _architect_process_roots(root, architect=None, helper_only=False):
    """Configured architect roots, optionally restricted to proven AO helpers."""
    from . import procs

    if architect is None:
        try:
            architect = (load_config(root).get("architect") or {})
        except (OSError, TypeError, ValueError):
            architect = {}
    architect = architect or {}
    configured = architect.get("argv") or []
    if not configured:
        return []

    command = _program_name(configured[0])
    names = {command} if command else set()
    # CLI launchers commonly exec a runtime under the package's other public
    # name. These are aliases of the configured command, not a generic list of
    # agents: an implementer on one harness must not become an architect on
    # another merely because both are interactive in the same tree. The names
    # that are one agent's are its adapter's id, binaries and processes (#76).
    for ident, adapter in package_adapters().items():
        known = {_program_name(name) for name in [ident, *adapter_binaries(adapter),
                                                  *((adapter.get("detect") or {}).get("processes") or [])] if name}
        if command in known:
            names.update(known)
    if not names:
        return []

    def normal_path(path):
        raw = str(path).replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", raw):
            return raw.rstrip("/").lower()
        return os.path.normcase(os.path.realpath(raw))

    def absolute_path(path):
        raw = str(path).replace("\\", "/")
        return os.path.isabs(raw) or bool(re.match(r"^[A-Za-z]:/", raw))

    targets = {normal_path(architect.get("cwd") or root)}
    if helper_only:
        # Watchdog helpers are spawned in the project root even when the human
        # architect works in a configured worktree.
        targets.add(normal_path(root))
    table = _proc_table()
    parents = {pid: row[0] for pid, row in table.items()}
    helpers = helper_pids(root, "architect" if helper_only else None)

    def under_helper(pid):
        current = pid
        seen = set()
        while current > 1 and current not in seen:
            if current in helpers:
                return True
            seen.add(current)
            current = parents.get(current, 0)
        return False

    candidates = set()
    vectors = {}
    for pid in procs.all_pids():
        helper = under_helper(pid)
        if pid == os.getpid() or (helper_only and not helper) \
                or (not helper_only and helper):
            continue
        argv = procs.argv(pid)
        if not argv or not _is_configured_agent_process(names, argv):
            continue
        cwd = procs.cwd(pid)
        if cwd is None:
            # Windows' existing process backend cannot read cwd. Keep the same
            # fail-closed fallback as agent_pids(): an exact absolute repository
            # argument is required until backlog #9 adds PEB cwd support.
            in_project = any(
                normal_path(arg.rstrip("/\\")) in targets
                for arg in argv if absolute_path(arg)
            )
        else:
            in_project = normal_path(cwd) in targets
        if not in_project:
            continue
        candidates.add(pid)
        vectors[pid] = argv

    # Preserve ancestry through nonmatching intermediaries. Filtering first and
    # looking only at an immediate parent turns `claude -p -> shell -> runtime`
    # into two roots; the flagless runtime then looks interactive. Walk the full
    # parent graph and assign every matching process to its highest matching
    # ancestor, with cycle detection for a corrupt or racing process snapshot.
    roots = set()
    for pid in candidates:
        root_pid = pid
        current = pid
        seen = set()
        while current not in seen:
            seen.add(current)
            parent = parents.get(current, 0)
            if parent in candidates:
                root_pid = parent
            if parent <= 1 or parent not in parents:
                break
            current = parent
        roots.add(root_pid)
    return [(pid, vectors[pid]) for pid in sorted(roots)]


def architect_present(root, architect=None):
    """Is a human-driven architect process alive for this project?

    Presence is a process fact, not transcript recency. Match only the configured
    architect command in its configured cwd, exclude AO helpers and descendants,
    and classify headlessness at the full process-tree root. No positive result
    is cached, so process exit releases presence on the next scan.
    """
    headless_flags = ("-p", "--print", "--no-interactive")
    return any(
        not any(flag in argv for flag in headless_flags)
        for _, argv in _architect_process_roots(root, architect)
    )


def architect_turn_present(root, architect=None):
    """Is a configured architect process alive in a proven AO helper tree?

    This is the duplicate-wake guard. It revalidates the registered helper's
    process-start identity and current process tree; an unrelated same-binary
    implementer, remembered pid, or lock file cannot postpone a wake.
    """
    return bool(_architect_process_roots(root, architect, helper_only=True))


DECISION_DIR = ".ao/decisions"


def decisions(root, state=None):
    """Open questions the implementer cannot answer for itself.

    A blocker written as prose costs minutes to answer from a phone: read it,
    work out what is being asked, type a paragraph. The same blocker written as
    a question with options costs one tap. That difference decides whether a run
    survives the hours when nobody is at a desk.
    """
    d = os.path.join(root, DECISION_DIR)
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(d, f), encoding=UTF8))
        except Exception:
            continue
        rec["id"] = f[:-5]
        if state and rec.get("state") != state:
            continue
        out.append(rec)
    return out


def ask(root, question, options, context=None, slice_id=None):
    """Record a question. Free text is always the last option.

    Options are a convenience, never a cage: the answer that matters is often the
    one nobody listed, and a form that cannot express it produces a wrong answer
    chosen because it was available.
    """
    d = os.path.join(root, DECISION_DIR)
    os.makedirs(d, exist_ok=True)
    did = f"D-{int(time.time())}"
    opts = [{"key": chr(ord('a') + i), "label": o} for i, o in enumerate(options[:8])]
    opts.append({"key": "x", "label": "Başka (serbest metin)", "free_text": True})
    # The same question answered before, here or in another project, travels with it (#43).
    try:
        precedents = [{key: found.get(key) for key in ("project", "kind", "id", "at", "outcome", "source")}
                      for found in recall(f"{question} {context or ''}", root, limit=3, share=0.4)]
    except Exception:
        precedents = []
    rec = scan_record({"asked_at": int(time.time()), "question": question, "context": context,
                       "slice": slice_id, "options": opts, "state": "open",
                       "answer": None, "answered_at": None, "answered_by": None,
                       "precedents": precedents})
    json.dump(rec, open(os.path.join(d, did + ".json"), "w", encoding=UTF8),
              ensure_ascii=False, indent=2)
    rec["id"] = did
    return rec


def answer(root, did, key_or_text, by="human"):
    """Answer one question. Returns the updated record, or None if unknown."""
    p = os.path.join(root, DECISION_DIR, did + ".json")
    if not os.path.exists(p):
        return None
    rec = json.load(open(p, encoding=UTF8))
    chosen = next((o for o in rec["options"] if o["key"] == key_or_text.strip().lower()), None)
    rec["answer"] = chosen["label"] if chosen and not chosen.get("free_text") \
        else key_or_text
    rec["answer_key"] = chosen["key"] if chosen else None
    rec["state"] = "answered"
    rec["answered_at"] = int(time.time())
    rec["answered_by"] = by
    rec = scan_record(rec)
    json.dump(rec, open(p, "w", encoding=UTF8), ensure_ascii=False, indent=2)
    rec["id"] = did
    return rec


def last_nudge_error(root):
    """The most recent failed nudge, if the watchdog recorded one."""
    key = project_key(root)
    try:
        st = json.load(open(os.path.join(HOME, ".ao", f"watchdog-{key}.json"), encoding=UTF8))
    except Exception:
        return None
    return st.get("last_error")


def recent_errors(recs, limit=3, adapter=None):
    """Tool calls the agent itself marked as failed.

    Use the structural verdict the store already carries — Kiro records
    `success: true|false` on every tool_result — never a text search. Matching on
    words like "failed" surfaces the agent's own search patterns and passing test
    names, which is worse than showing nothing: a panel that cries wolf gets
    ignored exactly when it is right.
    """
    field = ((adapter or {}).get("telemetry", {}).get("failure") or {}).get("field", "success")
    out = []
    for r in reversed(recs):
        pl = r.get("payload", r)
        if not isinstance(pl, dict) or pl.get("type") != "tool_result":
            continue
        if pl.get(field) is not False:
            continue
        # Failed tool output is usually a wall of passing lines with the real
        # cause buried in it. Lead with the line that actually failed.
        raw = str(pl.get("content", ""))
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
        def is_signal(ln):
            low = ln.lower()
            if ln.startswith("✔") or low.startswith("output:"):
                return False
            return (ln.startswith("✖") or "error ts" in low or "error:" in low
                    or low.startswith("fail") or " failing tests" in low
                    or "exit code: 1" in low or low.startswith("✗"))
        signal = next((ln for ln in lines if is_signal(ln)), None)
        if not signal:
            signal = next((ln for ln in lines if not ln.lower().startswith("output:")
                           and not ln.startswith("✔")), lines[0] if lines else raw)
        text = " ".join(str(signal)[:260].split())
        out.append((local_hhmm(r.get("timestamp", "")) or "--:--", text))
        if len(out) >= limit:
            break
    return list(reversed(out))


# ── repository signals ────────────────────────────────────────────────────────

def _review_verdict(body):
    """Return one explicit top-level verdict value, otherwise INVALID."""
    from .verdicts import VERDICTS
    allowed = set(VERDICTS)
    values = []
    malformed = False
    for line in str(body or "").splitlines():
        if line.startswith((" ", "\t")):
            continue
        if not re.match(r"^(?:\*\*)?verdict\b", line, re.I):
            continue
        prefix = re.match(
            r"^(?:\*\*verdict[ \t]*:\*\*|verdict[ \t]*:)", line, re.I
        )
        if not prefix:
            malformed = True
            continue
        value = line[prefix.end():].strip(" \t")
        if value.startswith("**") and value.endswith("**") and len(value) >= 4:
            value = value[2:-2].strip(" \t")
        if not re.fullmatch(r"[A-Za-z_]+", value or ""):
            malformed = True
            continue
        value = value.upper()
        if value not in allowed:
            malformed = True
            continue
        if value not in values:
            values.append(value)
    return values[0] if not malformed and len(values) == 1 else "INVALID"


def _has_verdict_marker(body):
    """Whether output contains any verdict-like marker; authority stays stricter."""
    return bool(re.search(r"\bverdict\b", str(body or ""), re.I))


def reviews(root, reviews_dir, limit=4):
    d = os.path.join(root, reviews_dir)
    if not os.path.isdir(d):
        return []
    # Review artefacts only. `ao init` leaves semantic-review/.gitkeep, and as the
    # newest file it was reported as an INVALID review and written into every
    # verification record as the review the tree had been measured against.
    files = sorted((f for f in os.listdir(d)
                    if not f.startswith(".") and os.path.isfile(os.path.join(d, f))),
                   key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)[:limit]
    out = []
    for f in files:
        verdict = "INVALID"
        try:
            body = open(os.path.join(d, f), errors="ignore", encoding=UTF8).read()
            verdict = _review_verdict(body)
            evidence = review_evidence(body)
            # ao records the verdict it adjudicated in the evidence line too; a
            # margin verdict that disagrees was changed after ao wrote it (#60).
            if isinstance(evidence, dict) and "verdict" in evidence \
                    and evidence["verdict"] != verdict:
                verdict = "INVALID"
            # Preserve legacy one-line quota/auth artifacts written before reviews
            # carried evidence. An artefact with an evidence line records how the
            # reviewer's process ended as its verdict line, so words in it — a
            # finding about authentication or a rate limit — never decide it (#57).
            has_verdict_line = _has_verdict_marker(body)
            if verdict == "INVALID" and not has_verdict_line \
                    and evidence is None \
                    and REVIEW_UNAVAILABLE_RE.search(body):
                verdict = "UNAVAILABLE"
        except Exception:
            pass
        out.append((f, verdict))
    return out


REVIEW_UNAVAILABLE_RE = re.compile(
    r"hit your (?:session|usage|weekly|monthly) limit|usage limit|rate limit|"
    r"out of (?:credits|quota)|API Error: (?:401|403|429|5\d\d)|"
    r"Not logged in|login required|authentication", re.I)


def reviewer_state_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"reviewer-{key}.json")


def reviewer_state(root):
    try:
        return json.load(open(reviewer_state_path(root), encoding=UTF8))
    except (OSError, ValueError):
        return {}


def set_reviewer_state(root, **fields):
    st = reviewer_state(root)
    st.update(fields)
    try:
        os.makedirs(os.path.dirname(reviewer_state_path(root)), exist_ok=True)
        json.dump(st, open(reviewer_state_path(root), "w", encoding=UTF8))
    except OSError:
        pass
    return st


def _edge_ids(item, field):
    """Board ids a note names; a parenthesised word is a remark, not an id."""
    return [token for token in re.split(r"[,\s]+", item["notes"].get(field, ""))
            if token and not token.startswith("(")]


def board_graph(root):
    """READY, derived from the board's dependency edges, and what is wrong with them (#33).

    A dependency graph, not a handoff mechanism. The valuable part of "backend
    done, now the frontend" is that the second item becomes eligible the moment
    the first lands, and putting the edge on the board keeps the implementer from
    choosing its own scope, the one authority it must not hold.

    `needs: B3, B4` on a queued item names the board items it waits for;
    `unlocks:` on any item is the same edge written from the other end. `needs:`
    on a blocked item stays its reason in words. On 2026-09-07 the architect
    decided what was actionable by reading the board as prose, and a dependency
    named there was never checked against anything. So an id that is not on the
    board, an id listed twice and a cycle are problems naming both ends, never a
    silent skip, and no item they touch is READY; neither is one `waiting:` on
    someone. READY is derived here and nowhere else: a hand-written `## ready`
    section is a problem too.

    Returns {"ready": [item with its role], "problems": [text]}.
    """
    b = board(root)
    problems, where = [], {}
    for state in BOARD_STATES:
        for item in b[state]:
            if item["id"] in where:
                problems.append(f"{item['id']} is on the board twice, under {where[item['id']]} and {state}")
            where.setdefault(item["id"], state)
    try:
        with open(os.path.join(root, ".ao", "board.md"), encoding=UTF8, errors="replace") as fh:
            if any(re.match(r"^\s*##\s+ready\s*$", line, re.I) for line in fh):
                problems.append("the board has a hand-written READY section; READY is derived from `needs:`, "
                                "so its items belong under `## queued`")
    except OSError:
        pass
    edges = {item["id"]: set(_edge_ids(item, "needs")) for item in b["queued"]}
    for state in BOARD_STATES:
        for item in b[state]:
            for target in _edge_ids(item, "unlocks"):
                if target not in where:
                    problems.append(f"{item['id']} unlocks {target}, which is not on the board")
                else:
                    edges.setdefault(target, set()).add(item["id"])
    broken = set()
    for item_id in sorted(edges):
        for dep in sorted(edges[item_id]):
            if dep not in where:
                problems.append(f"{item_id} needs {dep}, which is not on the board")
                broken.add(item_id)
    colour, trail, cycles = {}, [], set()

    def visit(node):
        colour[node] = "open"
        trail.append(node)
        for dep in sorted(edges.get(node, ())):
            if colour.get(dep) == "open":
                cycle = trail[trail.index(dep):]
                if frozenset(cycle) not in cycles:
                    cycles.add(frozenset(cycle))
                    problems.append("a cycle: " + ", ".join(
                        f"{first} needs {then}" for first, then in zip(cycle, cycle[1:] + [dep])))
                broken.update(cycle)
            elif dep not in colour and dep in edges:
                visit(dep)
        trail.pop()
        colour[node] = "closed"

    for node in sorted(edges):
        if node not in colour:
            visit(node)
    done = {item["id"] for state in ("done", "verified") for item in b[state]}
    ready_items = [{**item, "role": item["notes"].get("role", "")} for item in b["queued"]
                   if item["id"] not in broken and "waiting" not in item["notes"]
                   and all(dep in done for dep in edges.get(item["id"], ()))]
    return {"ready": ready_items, "problems": problems}


def ready(root):
    """Queued items whose dependencies are all done and which wait on no one (#33)."""
    return board_graph(root)["ready"]


def review_loop(root, reviews_dir, min_repeats=3):
    """The same finding coming back review after review.

    A round budget counts rounds. It cannot see that round four's blocker is
    round two's blocker with the line numbers moved — which is the actual
    failure: the implementer is not converging, and more rounds will not help.
    Fingerprint each finding on its file and its first clause, and report any
    that recurs across consecutive NEEDS_CHANGES reviews.
    """
    seen = {}
    d = os.path.join(root, reviews_dir)
    for f, v in reviews(root, reviews_dir, limit=12):
        if "APPROVED" in (v or "").upper():
            break
        try:
            body = open(os.path.join(d, f), errors="replace", encoding=UTF8).read()
        except OSError:
            continue
        # Findings sit in the reviewer's verbatim block, indented (#54).
        for m in re.finditer(r"^(?:    )?- \[(BLOCKER|HIGH|MEDIUM|LOW)\]\s*([^\s:]+)[:\d]*\s*[—-]\s*(.{0,60})",
                             body, re.M):
            key = (m.group(2), re.sub(r"\W+", " ", m.group(3).lower()).strip()[:40])
            seen.setdefault(key, {"sev": m.group(1), "count": 0, "reviews": []})
            seen[key]["count"] += 1
            seen[key]["reviews"].append(f)
    return [{"file": k[0], "clause": k[1], **v}
            for k, v in seen.items() if v["count"] >= min_repeats]
