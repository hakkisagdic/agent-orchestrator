"""Slices and projects: recall, worktrees, secondary projects, outcomes, bounded stores, hunting,
backup, content, boundaries.

A part of src/ao/lib.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


# ---- recall: what was decided, found or learned before, in every project (#43) ----------

_RECALL_STOP = frozenset(
    "the and for with that this from into have has had was were are not but then than when what which who why how "
    "its our your their there here been being will would could should about after before over under only also just "
    "very more most some such each other same one two can may must does did done yet still any all bir ve ile için "
    "bu şu da de mi ne gibi daha çok".split())
_FINDING_LINE = re.compile(r"^\s*- \[(BLOCKER|HIGH|MEDIUM|LOW)\]\s*(.+)$")


def _recall_words(text):
    words = (word.strip("._-") for word in re.findall(r"[a-zçğıöşü0-9][a-zçğıöşü0-9_.-]*", str(text or "").lower()))
    return {word for word in words if len(word) >= 3 and word not in _RECALL_STOP}


def _epoch(value):
    """A recorded time as seconds, whether ao wrote it as a number or as ISO text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def recall_roots(root=None):
    """(project, root) for every project this machine has registered, the one asking first."""
    out, seen = [], set()
    if root:
        out.append((project_key(root), root))
        seen.add(os.path.realpath(root))
    for name, row in sorted(project_registry().items()):
        path = row.get("root")
        if path and os.path.isdir(path) and os.path.realpath(path) not in seen:
            seen.add(os.path.realpath(path))
            out.append((name, path))
    return out


def recall_entries(project, root):
    """What one project remembers, each with the file and row it came from (#43).

    Questions asked and their answers, architect decisions, waivers, review
    findings with their review's verdict, and lessons. A source that cannot be
    read is passed over; the rest still answer.
    """
    entries = []

    def add(kind, ident, at, text, outcome, source):
        entries.append({"project": project, "kind": kind, "id": ident, "at": _epoch(at), "text": text,
                        "outcome": outcome, "source": source})

    for rec in decisions(root):
        add("question", rec.get("id"), rec.get("answered_at") or rec.get("asked_at"),
            " ".join(str(rec.get(key) or "") for key in ("question", "context", "answer")),
            f"answered: {rec['answer']}" if rec.get("answer") else rec.get("state") or "open",
            f"{DECISION_DIR}/{rec.get('id')}.json")
    readers = ((decision_rows, "decision", ("decision", "why", "scope"), ".ao/ledger/decisions.jsonl"),
               (waiver_rows, "waiver", ("gate", "slice", "why"), ".ao/ledger/waivers.jsonl"))
    for reader, kind, fields, source in readers:
        try:
            rows = reader(root)
        except Exception:
            continue
        for number, row in enumerate(rows, 1):
            if isinstance(row, dict):
                add(kind, row.get("id"), row.get("at"), " ".join(str(row.get(key) or "") for key in fields),
                    row.get("event") or ("recorded by " + str(row.get("by") or "architect")), f"{source}:{number}")
    reviews_dir = "semantic-review"
    try:
        with open(os.path.join(root, ".ao", "config.json"), encoding=UTF8) as fh:
            reviews_dir = json.load(fh).get("reviews") or reviews_dir
    except (OSError, ValueError, AttributeError):
        pass
    directory = os.path.join(root, reviews_dir)
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        path = os.path.join(directory, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding=UTF8, errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        verdict = _review_verdict(body)
        for number, line in enumerate(body.splitlines(), 1):
            found = _FINDING_LINE.match(line)
            if found:
                add("finding", name, os.path.getmtime(path), found.group(2), f"{found.group(1)} in a {verdict} review",
                    f"{reviews_dir}/{name}:{number}")
    try:
        with open(os.path.join(root, "docs", "lessons.md"), encoding=UTF8, errors="replace") as fh:
            lessons = fh.read().splitlines()
    except OSError:
        lessons = []
    starts = [number for number, line in enumerate(lessons) if line.startswith("## ")]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lessons)
        add("lesson", lessons[start][3:].strip(), None, "\n".join(lessons[start:end]), "a lesson",
            f"docs/lessons.md:{start + 1}")
    return entries


def recall(text, root=None, limit=10, exclude=(), share=0.5):
    """Records in every registered project that share the words of `text`, best first (#43).

    Plain words over the files ao already keeps: no index, no service, no network.
    One or two words must all appear; longer text needs `share` of its words.
    """
    words = _recall_words(text)
    if not words:
        return []
    need = len(words) if len(words) <= 2 else max(2, int(len(words) * share + 0.999))
    found = []
    for project, path in recall_roots(root):
        try:
            entries = recall_entries(project, path)
        except Exception:
            continue
        for entry in entries:
            if entry["id"] in exclude:
                continue
            score = len(words & _recall_words(entry["text"]))
            if score >= need:
                found.append(dict(entry, score=score))
    found.sort(key=lambda entry: (-entry["score"], -(entry["at"] or 0)))
    return found[:limit]


def architect_absence(root, cfg):
    """When the architect was last seen, and the questions waiting for its return (#84).

    Seen means its session wrote, it recorded a decision, or it left mail still in
    the mailbox, whichever is newest.
    """
    seen = []
    try:
        # Its own session's last write. The newest transcript where the architect works is as likely
        # the implementer's, when both run one harness there (SESSION-IDENTITY).
        state = session_state(cfg, "architect")
        if state is not None:
            transcript = role_session_paths(dict(cfg, root=root), "architect")[0] if state["session"] else None
            if transcript and os.path.exists(transcript):
                seen.append(os.path.getmtime(transcript))
        else:
            found = discover_architect((cfg.get("architect") or {}).get("cwd") or root)
            if found and found.get("age") is not None \
                    and found.get("session") != (session_state(cfg, "implementer") or {}).get("session"):
                seen.append(time.time() - float(found["age"]))
    except Exception:
        pass
    try:
        rows = decision_rows(root)
        if rows and _epoch(rows[-1].get("at")):
            seen.append(_epoch(rows[-1].get("at")))
    except Exception:
        pass
    mailbox_dir = cfg.get("mailbox", "agent-mail")
    for name in mailbox(root, mailbox_dir):
        if from_architect(name, cfg):
            try:
                seen.append(os.path.getmtime(os.path.join(root, mailbox_dir, name)))
            except OSError:
                pass
    waiting = sorted(decisions(root, "open"), key=lambda d: d.get("asked_at") or 0)
    return {"seen_at": max(seen) if seen else None, "waiting": [d["id"] for d in waiting],
            "oldest_at": waiting[0].get("asked_at") if waiting else None}


# ---- a worktree lives exactly as long as its slice (#42) --------------------------------

def default_branch(root):
    """The branch work lands on: origin's HEAD, else main, else master, else the main checkout's."""
    remote = git_text(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if remote.startswith("refs/remotes/origin/"):
        return remote[len("refs/remotes/origin/"):]
    for name in ("main", "master"):
        if git_text(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
            return name
    trees = worktree_list(root)
    return trees[0]["branch"] if trees else None


def worktree_list(root):
    """Every worktree of the repository: {"path", "head", "branch", "prunable"}, the main one first."""
    listed = _git_output(root, "worktree", "list", "--porcelain", "-z")
    out = []
    for record in listed.split(b"\0\0"):
        fields = {}
        for attribute in record.split(b"\0"):
            key, _, value = os.fsdecode(attribute).partition(" ")
            if key:
                fields[key] = value
        if "worktree" in fields:
            branch = fields.get("branch", "")
            out.append({"path": fields["worktree"], "head": fields.get("HEAD"),
                        "branch": branch[len("refs/heads/"):] if branch.startswith("refs/heads/") else None,
                        "prunable": "prunable" in fields})
    return out


def reviews_in_flight(root):
    """Submitted reviews in one checkout whose runner is alive or has only just started."""
    directory = os.path.join(root, ".ao", "reviews")
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    flying = []
    for name in names:
        if not (name.startswith("R-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(directory, name), encoding=UTF8) as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and state.get("state") == "running" and (
                _pid_alive(state.get("pid")) if state.get("pid")
                else time.time() - float(state.get("submitted_at") or 0) < 60):
            flying.append(state.get("id") or name[:-5])
    return flying


def _tree_bytes(path):
    total = 0
    for directory, subdirs, files in os.walk(path):
        subdirs[:] = [d for d in subdirs if not os.path.islink(os.path.join(directory, d))]
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total


def worktree_facts(root, cfg, sizes=False):
    """What decides whether each worktree may go (#42).

    A worktree may go when its branch is merged into the default branch, when the
    board rejected the slice that owns it (`worktree:` or `branch:` on the item),
    or when its directory is already gone - and never while it holds product
    changes nobody committed, a review in flight, or the command that is asking.
    """
    target = default_branch(root)
    board_items = board(root)
    owners = []
    for state, items in board_items.items():
        for item in items:
            owners.append((state, item))
    here = os.path.realpath(root)
    out = []
    for index, tree in enumerate(worktree_list(root)):
        path, branch = tree["path"], tree["branch"]
        real = os.path.realpath(path)
        slice_state = next((state for state, item in owners
                            if (item["notes"].get("worktree") and os.path.realpath(item["notes"]["worktree"]) == real)
                            or (branch and item["notes"].get("branch") == branch)), None)
        merged = bool(branch and target and branch != target and subprocess.run(
            [git_binary(), "merge-base", "--is-ancestor", tree["head"], target], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
        exists = os.path.isdir(path)
        dirty = product_dirty(path, cfg) if exists else []
        flying = reviews_in_flight(path) if exists else []
        keep = []
        if index == 0:
            keep.append("the main checkout")
        if real == here:
            keep.append("the checkout asking")
        if dirty:
            keep.append(f"{len(dirty)} uncommitted product change(s)")
        if flying:
            keep.append(f"review in flight: {', '.join(flying)}")
        why = "merged into " + target if merged else "its slice was rejected" if slice_state == "rejected" \
            else "its directory is gone" if not exists or tree["prunable"] else None
        may_go = bool(why) and not keep
        # Sizes are for what may go: walking every dependency tree on disk is not free.
        out.append(dict(tree, merged=merged, slice_state=slice_state, dirty=len(dirty), in_flight=flying,
                        keep=keep, why=why, may_go=may_go,
                        bytes=_tree_bytes(path) if sizes and may_go and exists else None))
    return out


def prune_worktree(root, fact, apply=False, now=None):
    """Retire one worktree that may go: archive its coordination state and branch tip, then remove both (#42).

    The `.ao/` state and review artefacts go to ~/.ao/archive/<project>/, and the
    branch tip stays reachable as refs/ao/archive/<branch>-<stamp>, so what the
    worktree held can still be read after it is gone.
    """
    import shutil
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y%m%d-%H%M%S")
    name = os.path.basename(os.path.normpath(fact["path"]))
    archive = os.path.join(HOME, ".ao", "archive", project_key(root), f"worktree-{name}-{stamp}")
    steps = []
    for part in (".ao", "semantic-review"):
        source = os.path.join(fact["path"], part)
        if os.path.isdir(source):
            steps.append(f"archive {part}/ to {archive}")
            if apply:
                shutil.copytree(source, os.path.join(archive, part), symlinks=True)
    if fact["branch"]:
        steps.append(f"keep {fact['branch']} at refs/ao/archive/{fact['branch']}-{stamp}")
        if apply:
            _git_output(root, "update-ref", f"refs/ao/archive/{fact['branch']}-{stamp}", fact["head"])
    steps.append(f"remove the worktree {fact['path']}")
    if apply and os.path.isdir(fact["path"]):
        _git_output(root, "worktree", "remove", "--force", fact["path"])
    if fact["branch"]:
        steps.append(f"delete the branch {fact['branch']}")
        if apply:
            _git_output(root, "branch", "-D", fact["branch"])
    steps.append("git worktree prune")
    if apply:
        _git_output(root, "worktree", "prune")
    return steps


# ---- the secondary project: the same agent, another queue (#8, #22, #92) ----------------

HUMAN_WAITING = ("human", "insan", "person", "owner")


def secondary_projects(cfg):
    """The projects this one names as secondary (`"secondary": [{"root", "name"}]`), each with its config (#8)."""
    out = []
    for entry in (cfg or {}).get("secondary") or []:
        other_root = entry.get("root") if isinstance(entry, dict) else entry
        if not isinstance(other_root, str) or not os.path.isdir(os.path.join(other_root, ".ao")):
            continue
        try:
            other = load_config(other_root)
        except Exception:
            continue
        name = (entry.get("name") if isinstance(entry, dict) else None) or project_key(other_root)
        out.append({"name": name, "root": other_root, "cfg": other})
    return out


def working_elsewhere(cfg, idle_seconds):
    """The secondary project the implementer is writing in now, if it is (#22).

    While the implementer worked in an ao worktree, the watchdog of the project
    it had left read the same agent as idle with a slice running and nudged it
    back. Presence belongs to the agent: a transcript moving in a secondary
    project is the agent working.
    """
    for other in secondary_projects(cfg):
        try:
            transcript, _ = session_paths(other["cfg"])
            age = time.time() - last_write(transcript, transcript_shape(implementer_adapter(other["cfg"])))
        except (OSError, TypeError, ValueError):
            continue
        if age < idle_seconds:
            return {"name": other["name"], "root": other["root"], "age": age}
    return None


def secondary_ready(cfg):
    """The first READY item of a secondary project, for an implementer with nothing READY here (#8)."""
    for other in secondary_projects(cfg):
        try:
            items = ready(other["root"])
        except Exception:
            continue
        if items:
            return {"name": other["name"], "root": other["root"], "item": items[0]["id"]}
    return None


def human_waits(root):
    """Blocked board items the implementer declared as waiting on a person: `waiting: human` (#92)."""
    return [item for item in board(root)["blocked"]
            if (item["notes"].get("waiting") or "").strip().lower() in HUMAN_WAITING]


# ---- the process is measured by its outcomes (#49) ---------------------------------------

def _since_epoch(text):
    try:
        return datetime.strptime(str(text).strip()[:16], "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return None


def slice_outcomes(root, project=None):
    """What happened to every slice that landed, from what ao already recorded (#49).

    On 2026-09-07 the round budget, the size guideline and the review pipeline
    were all re-decided in one day on anecdote. Each outcome is read, never
    typed: the verdicts in the order ao recorded them (a re-specification keeps
    the history, it does not start a new one), rounds, ready-to-landed time, size
    by kind, and whether a defect was later found in what landed - a
    retrospective review that asked for changes, or a board item `fixes:` it.
    """
    from .storage import read_chained_jsonl
    project = project or project_key(root)
    try:
        reviews = [row for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN) if isinstance(row, dict)]
        grants = [row for row in authority_rows(root) if isinstance(row, dict) and row.get("granted") is True]
    except Exception:
        return []
    try:
        verifications = [row for row in read_chained_jsonl(os.path.join(root, ".ao", "ledger", "verifications.jsonl"),
                                                           VERIFICATION_CHAIN, legacy_prefix=True)
                         if isinstance(row, dict)]
    except Exception:
        verifications = []
    try:
        waivers = {row.get("id"): row for row in waiver_rows(root)
                   if isinstance(row, dict) and row.get("event") == "waived"}
    except Exception:
        waivers = {}
    by_artefact = {row.get("artefact"): row for row in reviews}
    items = {item["id"]: item for state_items in board(root).values() for item in state_items}
    fixed = {item["notes"]["fixes"].strip() for item in items.values() if item["notes"].get("fixes")}
    out = {}
    for grant in grants:
        linked = by_artefact.get(grant.get("review")) or {}
        slice_id = linked.get("slice") or (waivers.get(grant.get("waiver")) or {}).get("slice")
        if not slice_id or slice_id in out:
            continue
        mine = [row for row in reviews if row.get("slice") == slice_id]
        prospective = [row for row in mine if row.get("kind") == "index-candidate"
                       and row.get("verdict") in ("APPROVED", "NEEDS_CHANGES")]
        candidate = (grant.get("candidate") or {}).get("digest")
        size = next((row.get("candidate_size") for row in reversed(verifications)
                     if (row.get("candidate") or {}).get("digest") == candidate and row.get("candidate_size")), None)
        started = _since_epoch((items.get(slice_id) or {}).get("notes", {}).get("since", "")) \
            or (min(float(row.get("at") or 0) for row in mine) if mine else None)
        landed = float(grant.get("at") or 0) or None
        retro = [row for row in mine if row.get("kind") == "commit-range" and row.get("verdict") == "NEEDS_CHANGES"]
        out[slice_id] = {
            "project": project, "slice": slice_id, "verdicts": [row.get("verdict") for row in prospective],
            "rounds": len(prospective), "first_pass": bool(prospective) and prospective[0].get("verdict") == "APPROVED",
            "waived": not prospective and bool(grant.get("waiver")),
            "started_at": started, "landed_at": landed,
            "hours": round((landed - started) / 3600, 2) if landed and started and landed >= started else None,
            "size": size, "defect_found": bool(retro) or slice_id in fixed,
            # The tier of the review the grant stood on, so weaker independence is counted (REVIEW-TIERS).
            "tier": linked.get("tier"),
        }
    return list(out.values())


def outcome_stats(outcomes):
    """The distribution a process change is judged against: rounds, first-pass rate, time, size, defects."""
    def spread(values):
        values = sorted(v for v in values if v is not None)
        if not values:
            return None
        return {"median": values[len(values) // 2], "p90": values[min(len(values) - 1, int(len(values) * 0.9))],
                "n": len(values)}
    reviewed = [o for o in outcomes if not o["waived"]]
    return {
        "slices": len(outcomes), "waived": len(outcomes) - len(reviewed),
        "rounds": spread(o["rounds"] for o in reviewed),
        "first_pass_pct": round(100 * sum(o["first_pass"] for o in reviewed) / len(reviewed)) if reviewed else None,
        "hours": spread(o["hours"] for o in outcomes),
        "product_lines": spread((o["size"]["kinds"]["product"]["added"] + o["size"]["kinds"]["product"]["deleted"])
                                if isinstance(o.get("size"), dict) else None for o in outcomes),
        "defects_pct": round(100 * sum(o["defect_found"] for o in outcomes) / len(outcomes)) if outcomes else None,
        "tiers": {tier: sum(1 for o in outcomes if o.get("tier") == tier)
                  for tier in sorted({o.get("tier") for o in outcomes if o.get("tier")})},
    }


# ---- every store is bounded, without being asked (#50) -----------------------------------

OBSERVATION_LOGS = ("nudge-log", "watchdog-log", "refill-log", "escalate-log", "cycles")      # PROJECT_FILES


def bound_store(path, kb):
    """Keep an observation store within its bound as it is written: past it, the oldest records go (#50).

    Measured 2026-09-08: notices 3.2 MB, a nudge log 2.6 MB, and `ao prune`, which
    existed and defaulted to a dry run, had never been run - the only outcome a
    manual cleanup has. A store is trimmed once it is a quarter over its bound,
    back to three quarters of it, at a line boundary, so it is not rewritten on
    every write. Evidence is never trimmed here; it is sealed.
    """
    limit = int(kb) * 1024
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size <= limit * 1.25:
        return False
    from .storage import replace_file_durably
    with open(path, "rb") as fh:
        fh.seek(size - int(limit * 0.75))
        tail = fh.read()
    replace_file_durably(path, tail[tail.find(b"\n") + 1:])
    return True


def observation_stores(root, state_dir=None):
    """Every observation store of a project: its ledgers' and the watchdog's logs, as paths."""
    key = project_key(root)
    state_dir = state_dir or os.path.join(HOME, ".ao")
    stores = [os.path.join(root, ".ao", "ledger", name) for name in ("notices.jsonl", "progress.jsonl")]
    return stores + [os.path.join(state_dir, project_file_name(what, key)) for what in OBSERVATION_LOGS]


def bound_observation_logs(root, state_dir=None):
    """Hold every observation store to its bound; the watchdog does this each cycle (#50).

    The notices are folded first: what the bound trims still counts in a window (NOTICE-WINDOW).
    """
    limit = settings.get(load_config(root), "retention.observation_kb")
    fold_notice_times(root)
    return [path for path in observation_stores(root, state_dir) if bound_store(path, limit)]


def stores_over_bound(root, cfg, state_dir=None):
    """(path, bytes, bound) for each observation store over its bound, for `ao doctor` (#50)."""
    limit = settings.get(cfg, "retention.observation_kb") * 1024
    out = []
    for path in observation_stores(root, state_dir):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size > limit * 1.25:
            out.append((path, size, limit))
    return out


# ---- a standing bug-hunter: read-only, bounded, leads not verdicts (#45) -----------------

HUNT_CATEGORIES = ("correctness", "concurrency", "clock", "durability", "subprocess", "portability", "secrets",
                   "authority", "tests")
_LEAD = re.compile(r"^\s*-\s*\[([a-z]+)\]\s*([^\s:]+):(\d+)\s+(\S+)\s+[—-]+\s*(.+?)\s*$")


def hunter_ledger_path(root):
    return os.path.join(root, ".ao", "ledger", "hunter.jsonl")


def hunter_record(root, event, **fields):
    from .storage import append_jsonl
    append_jsonl(hunter_ledger_path(root), scan_record(dict(fields, at=int(time.time()), event=event)))


def hunter_rows(root):
    from .storage import read_jsonl
    return [row for row in read_jsonl(hunter_ledger_path(root)) if isinstance(row, dict)]


def hunter_known(root):
    """Fingerprints already sent or discarded: a repeat is suppressed, a discard remembered (#45)."""
    return {row.get("fingerprint") for row in hunter_rows(root) if row.get("event") in ("sent", "discarded")}


def hunt_slice(root, cfg):
    """The next bounded slice of tracked product files, from a cursor that goes round the tree (#45)."""
    try:
        listed = [os.fsdecode(p) for p in _git_output(root, "ls-files", "-z").split(b"\0") if p]
    except RuntimeError:
        return [], 0
    candidates = [p for p in listed if not _is_coordination_path(p, cfg) and os.path.isfile(os.path.join(root, p))]
    if not candidates:
        return [], 0
    state_path = os.path.join(root, ".ao", "hunter.json")
    try:
        with open(state_path, encoding=UTF8) as fh:
            cursor = int(json.load(fh).get("cursor") or 0) % len(candidates)
    except (OSError, ValueError, TypeError, AttributeError):
        cursor = 0
    files, spent, index = [], 0, cursor
    budget, most = settings.get(cfg, "hunter.bytes_per_run"), settings.get(cfg, "hunter.files_per_run")
    while len(files) < most and len(files) < len(candidates):
        path = candidates[index % len(candidates)]
        index += 1
        try:
            with open(os.path.join(root, path), encoding=UTF8) as fh:
                text = fh.read(budget)
        except (OSError, UnicodeDecodeError):
            continue
        if files and spent + len(text) > budget:
            break
        files.append((path, text))
        spent += len(text)
    from .storage import replace_file_durably
    replace_file_durably(state_path, (json.dumps({"cursor": index % len(candidates)}) + "\n").encode(UTF8))
    return files, cursor


def parse_leads(out):
    """Leads a hunter wrote as `- [category] path:line symbol — what is wrong`, each with a fingerprint (#45)."""
    leads = []
    for line in str(out or "").splitlines():
        found = _LEAD.match(line)
        if not found or found.group(1) not in HUNT_CATEGORIES:
            continue
        category, path, number, symbol, text = found.groups()
        fingerprint = hashlib.sha256(f"{path}|{symbol}|{category}".encode(UTF8)).hexdigest()[:16]
        leads.append({"category": category, "path": path, "line": int(number), "symbol": symbol, "finding": text,
                      "fingerprint": fingerprint})
    return leads


# ---- the governance survives the disk (#46) ---------------------------------------------

_BACKUP_SKIP = re.compile(r"(\.lock|\.tmp|\.pending|\.bak[^/]*|~)$|(^|/)\.ao/reviews/|(^|/)\.ao/hunter\.json$")


def governance_files(root, cfg):
    """Every file a project's control plane lives in, relative to the root (#46).

    Config, authority, board, backlog, gates and sources; decisions, parked work and
    every ledger with its sealed archives; the mailbox; and the review artefacts a
    grant rests on. Locks, temporary files and config backups are not governance.
    """
    out = []
    for top in (".ao", cfg.get("mailbox", "agent-mail")):
        base = os.path.join(root, top)
        for directory, subdirs, files in os.walk(base):
            subdirs[:] = [d for d in subdirs if not os.path.islink(os.path.join(directory, d))]
            for name in files:
                rel = os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/")
                if not _BACKUP_SKIP.search(rel) and os.path.isfile(os.path.join(root, rel)):
                    out.append(rel)
    reviews_dir = cfg.get("reviews", "semantic-review")
    try:
        for name in {row.get("review") for row in authority_rows(root)
                     if isinstance(row, dict) and row.get("granted") is True and row.get("review")}:
            rel = f"{reviews_dir}/{name}"
            if os.path.isfile(os.path.join(root, rel)):
                out.append(rel)
    except Exception:
        pass
    return sorted(set(out))


def _manifest(root, files):
    entries = {}
    for rel in files:
        with open(os.path.join(root, rel), "rb") as fh:
            entries[rel] = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    return {"schema": 1, "project": project_key(root), "at": int(time.time()),
            "head": git_text(root, "rev-parse", "HEAD") or None, "files": entries}


def write_backup(root, cfg, destination):
    """Write the governance to a destination the project names: a directory, `ref`, or `remote:<name>` (#46).

    A directory gets `<project>/<stamp>/` with a manifest of every file's digest. A
    ref is a commit of the same files under refs/ao/backup/latest, made from blob
    and tree objects without touching the index or the worktree. A remote gets that
    ref pushed, and only after the host says the repository is private.
    Returns the manifest with where it went, and it is recorded in the ledger.
    """
    files = governance_files(root, cfg)
    manifest = _manifest(root, files)
    stamp = datetime.fromtimestamp(manifest["at"]).strftime("%Y%m%d-%H%M%S")
    if destination.startswith("remote:"):
        remote = destination.split(":", 1)[1]
        if remote_is_private(root, remote) is not True:
            raise RuntimeError(f"not pushing governance to {remote}: the host did not confirm it is private")
    if destination == "ref" or destination.startswith("remote:"):
        where = _backup_ref(root, manifest)
        if destination.startswith("remote:"):
            _git_output(root, "push", remote, "refs/ao/backup/latest:refs/ao/backup/latest", timeout=300)
            where = f"{remote} {where}"
    else:
        target = os.path.join(os.path.expanduser(destination), manifest["project"], stamp)
        from .storage import replace_file_durably
        for rel in files:
            with open(os.path.join(root, rel), "rb") as fh:
                replace_file_durably(os.path.join(target, rel), fh.read())
        replace_file_durably(os.path.join(target, "manifest.json"),
                             (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode(UTF8))
        where = target
    manifest["where"] = where
    from .storage import append_jsonl
    append_jsonl(os.path.join(root, ".ao", "ledger", "backups.jsonl"),
                 {"at": manifest["at"], "where": where, "files": len(files), "head": manifest["head"]})
    return manifest


def _backup_ref(root, manifest):
    """A commit holding the governance files and the manifest, made without an index (#46)."""
    tree = {}
    for rel in list(manifest["files"]) + ["ao-backup-manifest.json"]:
        data = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode(UTF8) if rel == "ao-backup-manifest.json" \
            else open(os.path.join(root, rel), "rb").read()
        blob = subprocess.run([git_binary(), "hash-object", "-w", "--stdin"], cwd=root, input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()
        node = tree
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = blob

    def write(node):
        lines = []
        for name in sorted(node):
            if isinstance(node[name], dict):
                lines.append(f"040000 tree {write(node[name])}\t{name}")
            else:
                lines.append(f"100644 blob {node[name]}\t{name}")
        return subprocess.run([git_binary(), "mktree"], cwd=root, input=("\n".join(lines) + "\n").encode(UTF8),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()

    commit = subprocess.run([git_binary(), "-c", "user.name=ao", "-c", "user.email=ao@localhost", "commit-tree",
                             write(tree), "-m", f"ao backup of {manifest['project']} governance"], cwd=root,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()
    _git_output(root, "update-ref", "refs/ao/backup/latest", commit)
    return f"refs/ao/backup/latest {commit[:12]}"


def remote_is_private(root, remote):
    """True only when the host says the remote's repository is private; None when it cannot say (#46, #83)."""
    import shutil
    url = git_text(root, "remote", "get-url", remote)
    found = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url or "")
    if not found or not shutil.which("gh"):
        return None
    try:
        answer = subprocess.run(["gh", "api", f"repos/{found.group(1)}/{found.group(2)}", "--jq", ".private"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {"true": True, "false": False}.get(answer.stdout.strip())


def restore_backup(root, source):
    """Put the governance back from a backup directory, verifying every file against its manifest (#46).

    Returns (restored, unverified): a file whose bytes do not match the digest it was
    backed up with is not restored, and is named.
    """
    with open(os.path.join(source, "manifest.json"), encoding=UTF8) as fh:
        manifest = json.load(fh)
    from .storage import replace_file_durably
    restored, unverified = [], []
    for rel, digest in sorted(manifest["files"].items()):
        path = os.path.join(source, rel)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            unverified.append(f"{rel}: missing from the backup")
            continue
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            unverified.append(f"{rel}: its bytes do not match the manifest")
            continue
        replace_file_durably(os.path.join(root, rel), data)
        restored.append(rel)
    return restored, unverified


def backup_age(root):
    """(seconds since the newest backup, where it went), or None when there is none (#46)."""
    from .storage import read_jsonl
    rows = [row for row in read_jsonl(os.path.join(root, ".ao", "ledger", "backups.jsonl")) if isinstance(row, dict)]
    return (time.time() - float(rows[-1]["at"]), rows[-1].get("where")) if rows else None


# ---- the harness content seam: borrow, pin, verify (#14, #15) ----------------------------

CONTENT_TEXT = (".md", ".txt", ".json", ".yaml", ".yml")
# Each kind of content a source keeps, and the key naming an entry of its list in .ao/content.json.
CONTENT_KINDS = (("skills", "skill"), ("steering", "steering"), ("agents", "agent"))
CONTENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# A tree entry of one of these modes is not a text file, whatever its name says.
_NOT_TEXT_MODES = {"120000": "a symbolic link", "160000": "a submodule", "100755": "executable"}


def content_manifest_path(root):
    return os.path.join(root, ".ao", "content.json")


def content_manifest(root):
    """{kind: [entry]} as .ao/content.json records them; a kind it lacks, or holds as no list, is empty."""
    try:
        with open(content_manifest_path(root), encoding=UTF8) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {kind: [entry for entry in data[kind] if isinstance(entry, dict)] if isinstance(data.get(kind), list)
            else [] for kind, _ in CONTENT_KINDS}


def fetch_pinned(source, pin, paths, workdir):
    """{path: (mode, blob id)} for each file under `paths` of `source` at commit `pin` (#14).

    Nothing unpinned is ever fetched, and nothing is checked out: a file is read by its
    blob id when it is wanted (`pinned_blobs`). So its bytes are the commit's whatever this
    machine converts - Git for Windows checks text out with CRLF by default, and neither
    the files nor their digests were the commit's (#71) - and a symbolic link is a mode
    here, never a path ao follows to a file of this machine. A path the commit does not
    hold is absent from the answer.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", str(pin or "")):
        raise ValueError(f"{pin!r} is not a pin: name a full 40-character commit id, never a branch or a tag")
    git = git_binary()
    subprocess.run([git, "init", "-q", workdir], check=True, capture_output=True)
    subprocess.run([git, "-C", workdir, "fetch", "-q", "--depth", "1", source, pin], check=True, capture_output=True,
                   timeout=300)
    fetched = subprocess.run([git, "-C", workdir, "rev-parse", "FETCH_HEAD"], check=True, capture_output=True,
                             text=True).stdout.strip()
    if fetched != pin:
        raise RuntimeError(f"{source} answered {fetched[:12]}, not the pinned {pin[:12]}")
    if not paths:
        return {}
    listing = subprocess.run([git, "-C", workdir, "--literal-pathspecs", "ls-tree", "-r", "-z", pin, "--", *paths],
                             check=True, capture_output=True, timeout=300).stdout
    files = {}
    for record in listing.split(b"\0"):
        meta, _, path = record.partition(b"\t")
        fields = meta.decode(UTF8, "replace").split()
        if path and len(fields) == 3:
            files[path.decode(UTF8, "surrogateescape")] = (fields[0], fields[2])
    return files


def pinned_blobs(workdir, ids):
    """{blob id: bytes} for each id, read from the fetched commit by one `git cat-file --batch` (#15)."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    out = subprocess.run([git_binary(), "-C", workdir, "cat-file", "--batch"],
                         input="".join(f"{oid}\n" for oid in wanted).encode(UTF8), check=True, capture_output=True,
                         timeout=300).stdout
    blobs, at = {}, 0
    for oid in wanted:
        end = out.find(b"\n", at)
        header = out[at:end].split() if end >= 0 else []
        if len(header) != 3 or header[1] != b"blob":
            raise RuntimeError(f"the pinned commit holds no blob {oid[:12]}")
        size = int(header[2])
        blobs[oid] = out[end + 1:end + 1 + size]
        at = end + 1 + size + 1
    return blobs


def _not_borrowed(path, mode, data=None):
    """Why a file of a pinned tree is not borrowed, or None when it is text (#14).

    The mode is the commit's, so an executable is one on every platform: Windows keeps
    no execute bit on disk, and os.access says every file there has one (#71).
    """
    if mode in _NOT_TEXT_MODES:
        return f"{_NOT_TEXT_MODES[mode]}: only text is borrowed"
    if not path.lower().endswith(CONTENT_TEXT):
        return "not Markdown, text, JSON or YAML: only text is borrowed"
    if data is not None and data[:2] == b"#!":
        return "a script: only text is borrowed"
    return None


def _front_matter(data, field):
    """What a Markdown file's front matter says for a top-level `field`, or None."""
    block = re.match(rb"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", data, re.S)
    found = block and re.search(rb"^" + re.escape(field.encode(UTF8)) + rb"[ \t]*:[ \t]*['\"]?([^'\"\s#]+)",
                                block.group(1), re.M)
    return found.group(1).decode(UTF8, "replace") if found else None


def _declared(*blocks):
    """One {name: text} from each block that is a dict, a later block's names winning."""
    merged = {}
    for block in blocks:
        merged.update(block if isinstance(block, dict) else {})
    return merged


def _kept_files(root):
    """Files an adapter says a harness or ao keeps for itself; borrowed content replaces none (#15).

    A playbook, a coordination file, an MCP registration, settings whose hooks run
    commands: a borrowed file landing on one would speak for ao or run what nobody
    pinned. A path ending in `/` stands for everything under it.
    """
    kept = set()
    for adapter in list(package_adapters().values()) + [entry["adapter"] for entry in adapter_catalog(root).values()]:
        adapter = adapter if isinstance(adapter, dict) else {}
        directives = adapter.get("directives") if isinstance(adapter.get("directives"), dict) else {}
        named = [_declared(directives.get("playbook")).get("path"), directives.get("coordination"),
                 directives.get("config"), directives.get("mcp"), _declared(adapter.get("mcp")).get("file")]
        for field in ("ao_files", "rule_files", "steering_files"):
            named += directives.get(field) if isinstance(directives.get(field), list) else []
        named += _declared(directives.get("command_hooks")).get("files") or []
        kept.update(rel for rel in named if isinstance(rel, str) and rel)
    return kept


def _content_refusal(root, harness, target, kept):
    """Why ao must not write `target` for `harness`, or None (#15).

    Borrowed content lands in the harness's own directory, as the package's adapter
    declares it in `detect.dirs`, and nowhere else. A user's or a project's adapter layer
    is writable by the agents it describes, so a directory it names elsewhere - the
    product, ao's state, another harness's settings - is refused, as is a symbolic link
    on the way and a file a harness or ao keeps for itself.
    """
    homes = [str(d).strip("/") for d in ((package_adapters().get(harness) or {}).get("detect") or {}).get("dirs") or []
             if str(d).strip("/")]
    steps = _plain_steps(target)
    if not steps:
        return f"{target} is not a path inside the project"
    if not any(target.startswith(home + "/") for home in homes):
        return f"{target} is outside {harness}'s own directory ({', '.join(homes) or 'the package declares none'})"
    if any(target == rel or (rel.endswith("/") and target.startswith(rel)) for rel in kept):
        return f"{target} is a file a harness or ao keeps for itself"
    real = os.path.realpath(root)
    if os.path.normcase(os.path.realpath(os.path.join(root, *steps))) != os.path.normcase(os.path.join(real, *steps)):
        return f"{target} passes through a symbolic link"
    return None


def _content_item(kind, name):
    return {"kind": kind, "name": name, "files": {}, "skipped": [], "unsupported": [], "notes": []}


def _skill_item(name, head, tree, blobs, declared):
    """A skill's text files for each harness that takes skills, or its SKILL.md as a steering file (#14)."""
    item, texts = _content_item("skills", name), {}
    for path, (mode, oid) in sorted(tree.items()):
        if not path.startswith(head):
            continue
        rel = path[len(head):]
        steps = rel.split("/")
        if "hooks" in steps[:-1]:
            hooks = ("/".join(steps[:steps.index("hooks") + 1]) + "/", "hooks are never imported")
            if hooks not in item["skipped"]:
                item["skipped"].append(hooks)
            continue
        why = _not_borrowed(rel, mode, blobs.get(oid))
        if why:
            item["skipped"].append((rel, why))
        else:
            texts[rel] = blobs[oid]
    for harness, (directives, _, _) in declared.items():
        if directives.get("skills_dir"):
            item["files"].update({f"{directives['skills_dir']}/{name}/{rel}": (harness, data)
                                  for rel, data in texts.items()})
        elif directives.get("steering_dir") and "SKILL.md" in texts:
            body = re.sub(r"\A---\n.*?\n---\n+", "", texts["SKILL.md"].decode(UTF8, "replace"), flags=re.S)
            header = str(_declared(directives.get("steering_inclusion")).get("skill_header") or "")
            item["files"][f"{directives['steering_dir']}/{name}.md"] = (harness, (header + body).encode(UTF8))
    return item


def _steering_item(name, path, tree, blobs, declared):
    """A steering file, byte for byte and front matter and all, for each harness that reads steering (#15)."""
    item = _content_item("steering", name)
    mode, oid = tree[path]
    why = _not_borrowed(path, mode, blobs.get(oid))
    if why:
        item["skipped"].append((f"steering/{name}.md", why))
        return item
    for harness, (directives, _, known) in declared.items():
        if directives.get("steering_dir"):
            item["files"][f"{directives['steering_dir']}/{name}.md"] = (harness, blobs[oid])
        elif known:
            item["notes"].append(f"{harness} reads no steering files")
    return item


def _agent_item(name, path, tree, blobs, declared, takes_agents):
    """An agent definition in each harness's declared format, with no field in it that runs a command (#15).

    A field the package's adapter or a layer over it names in `agent_format.commands` -
    hooks, servers the harness starts - is taken out and named, so a layer cannot take one
    back in. The fields that grant trust, and an empty field that loads context, are kept
    and named: whoever runs the agent should see them before it runs.
    """
    item = _content_item("agents", name)
    for harness, (directives, shipped, known) in declared.items():
        if harness not in takes_agents:
            if known:
                item["notes"].append(f"{harness} reads no agent definitions ao can borrow")
            continue
        shape, (mode, oid) = _declared(directives.get("agent_format")), tree[path]
        why = _not_borrowed(path, mode, blobs.get(oid))
        try:
            definition = None if why else json.loads(blobs[oid].decode("utf-8-sig"))
        except ValueError:
            definition = None
        if not why and not isinstance(definition, dict):
            why = "not a JSON object, which an agent definition is"
        elif not why and definition.get("name", name) != name:
            why = f"it names itself {definition.get('name')!r}, and an agent is named as its file"
        if why:
            if (f"agents/{name}.json", why) not in item["skipped"]:
                item["skipped"].append((f"agents/{name}.json", why))
            continue
        commands = _declared(shape.get("commands"), _declared(shipped.get("agent_format")).get("commands"))
        taken = [field for field in sorted(commands) if definition.get(field)]
        for field in taken:
            definition.pop(field)
            item["skipped"].append((f"agents/{name}.json {field}", str(commands[field])))
        target = f"{directives['agents_dir']}/{name}.json"
        item["files"][target] = (harness, (json.dumps(definition, indent=2, ensure_ascii=False) + "\n").encode(UTF8)
                                 if taken else blobs[oid])
        for field, meaning in sorted(_declared(shape.get("grants")).items()):
            value = definition.get(field)
            if value:
                shown = value if isinstance(value, list) else sorted(value) if isinstance(value, dict) else [value]
                item["notes"].append(f"{target} keeps {field} {', '.join(map(str, shown))}: {meaning}")
        context = _declared(shape.get("context"))
        if context.get("field") and not definition.get(context["field"]):
            item["notes"].append(f"{target} names no {context['field']}: {context.get('empty')}")
    return item


def _unsupported_inclusion(item, declared):
    """Name each steering file whose inclusion its harness does not honour; the file is written as it is (#15)."""
    for target, (harness, data) in sorted(item["files"].items()):
        directives = declared[harness][0]
        rules = _declared(directives.get("steering_inclusion"))
        if not (rules.get("field") and directives.get("steering_dir")
                and target.startswith(f"{directives['steering_dir']}/")):
            continue
        mode = _front_matter(data, rules["field"]) or str(rules.get("default") or "")
        if mode not in (rules.get("honoured") or []):
            item["unsupported"].append((target, f"{rules['field']}: {mode} - {rules.get('unhonoured')}"))


def _content_refusals(root, items):
    """Every reason a planned write must not be made; one of them stops them all (#15)."""
    record, kept, refused, writers = content_manifest(root), _kept_files(root), [], {}
    for item in items:
        key = dict(CONTENT_KINDS)[item["kind"]]
        label = f"{key} {item['name']}"
        for target, (harness, data) in sorted(item["files"].items()):
            why = _content_refusal(root, harness, target, kept)
            if not why and target in writers:
                why = f"{target} would be written by both {writers[target]} and {label}"
            writers.setdefault(target, label)
            path = os.path.join(root, *target.split("/"))
            if not why and os.path.isdir(path):
                why = f"{target} is a directory"
            elif not why and os.path.exists(path):
                with open(path, "rb") as fh:
                    current = fh.read()
                # A file ao vendored here before is ao's to replace; any other file is someone's work.
                vendored = any(target in (entry.get("files") or {}) for entry in record[item["kind"]]
                               if entry.get(key) == item["name"])
                if current != data and not vendored:
                    why = f"{target} exists and ao did not vendor it as {label}: move it aside first"
            if why:
                refused.append(why)
    return refused


def vendor_content(root, source, pin, harnesses, skills=(), steering=(), agents=(), base="", dry_run=False):
    """Borrow skills, steering files and agent definitions pinned to a commit, text only, into each harness (#14, #15).

    A source keeps `skills/<name>/`, `steering/<name>.md` and `agents/<name>.json` under
    `base`, its root unless named: a repository that ships one harness's layer keeps it in
    that harness's own directory. Each harness takes what its adapter declares. Only text
    is borrowed and hooks never are, and both are named; what a harness does not honour,
    such as an inclusion only its IDE reads, is written as it is and named unsupported,
    never faked. Every write is checked before any is made, one refusal writes nothing,
    and a dry run writes nothing at all. Each written file's digest is recorded in
    .ao/content.json.

    Returns {"items", "hooks", "refused"}: each item {kind, name, files: {target: (harness,
    bytes)}, skipped: [(path, why)], unsupported: [(target, why)], notes: [text]}, each hook
    (path, why), and each refusal a sentence.
    """
    import tempfile
    for kind, names in (("skills", skills), ("steering", steering), ("agents", agents)):
        for name in names:
            if not CONTENT_NAME.fullmatch(name):
                raise ValueError(f"{name!r} is not a {dict(CONTENT_KINDS)[kind]} name")
    base = str(base or "").strip("/")
    if base and not _plain_steps(base):
        raise ValueError(f"{base!r} is not a directory inside the source: names joined by `/`, with no `.` or `..`")
    prefix = f"{base}/" if base else ""
    declared = {}                   # harness: (its directives, the package's directives for it, has it an adapter)
    for harness in harnesses:
        adapter = load_adapter(harness, root)
        declared[harness] = (_declared(adapter.get("directives")),
                             _declared((package_adapters().get(harness) or {}).get("directives")), bool(adapter))
    takes_agents = [harness for harness, (directives, _, _) in declared.items() if directives.get("agents_dir")
                    and _declared(directives.get("agent_format")).get("extension") == ".json"]
    wanted = [f"{prefix}skills/{name}" for name in skills] + [f"{prefix}steering/{name}.md" for name in steering]
    wanted += [f"{prefix}agents/{name}.json" for name in agents] if takes_agents else []
    wanted += [f"{prefix}hooks"] if steering or agents else []
    where = f" at {pin[:12]}" + (f" in {base}" if base else "")
    with tempfile.TemporaryDirectory(prefix="ao-content-") as work:
        tree = fetch_pinned(source, pin, wanted, work)
        missing = [f"{name} is not a skill{where}" for name in skills
                   if not any(path.startswith(f"{prefix}skills/{name}/") for path in tree)]
        missing += [f"{name} is not a steering file{where}" for name in steering
                    if f"{prefix}steering/{name}.md" not in tree]
        missing += [f"{name} is not an agent{where}" for name in agents
                    if takes_agents and f"{prefix}agents/{name}.json" not in tree]
        if missing:
            raise ValueError("; ".join(missing))
        blobs = pinned_blobs(work, [oid for path, (mode, oid) in tree.items()
                                    if not path.startswith(f"{prefix}hooks/") and not _not_borrowed(path, mode)])
    items = [_skill_item(name, f"{prefix}skills/{name}/", tree, blobs, declared) for name in skills]
    items += [_steering_item(name, f"{prefix}steering/{name}.md", tree, blobs, declared) for name in steering]
    items += [_agent_item(name, f"{prefix}agents/{name}.json", tree, blobs, declared, takes_agents) for name in agents]
    for item in items:
        _unsupported_inclusion(item, declared)
    hooks = []
    for path in sorted(path for path in tree if path.startswith(f"{prefix}hooks/")):
        rel, why = path[len(prefix):], ["hooks are never imported"]
        for harness, (directives, _, _) in declared.items():
            formats = _declared(directives.get("hook_files"))
            suffix = max((suffix for suffix in formats if rel.endswith(suffix)), key=len, default=None)
            if suffix:
                why.append(f"{harness}: {formats[suffix]}")
        hooks.append((rel, "; ".join(why)))
    plan = {"items": items, "hooks": hooks, "refused": _content_refusals(root, items)}
    if plan["refused"] or dry_run:
        return plan

    from .storage import replace_file_durably
    record = content_manifest(root)
    for item in items:
        key, digests = dict(CONTENT_KINDS)[item["kind"]], {}
        for target, (_, data) in sorted(item["files"].items()):
            replace_file_durably(os.path.join(root, *target.split("/")), data)
            digests[target] = "sha256:" + hashlib.sha256(data).hexdigest()
        record[item["kind"]] = [entry for entry in record[item["kind"]] if entry.get(key) != item["name"]]
        record[item["kind"]].append({key: item["name"], "source": source, "pin": pin, "files": digests,
                                     "skipped": [f"{path} ({why})" for path, why in item["skipped"]]})
    replace_file_durably(content_manifest_path(root), (json.dumps(record, indent=1, sort_keys=True) + "\n").encode(UTF8))
    return plan


def verify_content(root):
    """Vendored files whose bytes no longer match the digest they were vendored with (#14, #15)."""
    drift = []
    for kind, entries in content_manifest(root).items():
        key = dict(CONTENT_KINDS)[kind]
        for entry in entries:
            # A skill is named as it always was; the kinds borrowed since say which kind they are.
            label = f"{'' if kind == 'skills' else key + ' '}{entry.get(key)}@{str(entry.get('pin') or '')[:12]}"
            for target, digest in sorted((entry.get("files") or {}).items()):
                try:
                    with open(os.path.join(root, target), "rb") as fh:
                        actual = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
                except OSError:
                    drift.append(f"{target} ({label}) is missing")
                    continue
                if actual != digest:
                    drift.append(f"{target} ({label}) changed since it was vendored")
    return drift


_UNSAFE_HOOK = re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b|\brm\s+-rf\s+(/|~|\$HOME)(\s|$)")
_UNPINNED_RUNNER = ("npx", "bunx", "uvx", "pipx")


def agent_config_files(root):
    """Project files that configure an agent, as every adapter declares them, and AGENTS.md (#76)."""
    files = []

    def add(rel):
        rel = str(rel or "")
        if rel and not rel.startswith("~") and not os.path.isabs(rel) and rel not in files:
            files.append(rel)

    for _, entry in sorted(adapter_catalog(root).items()):
        adapter = entry["adapter"]
        directives = adapter.get("directives") or {}
        for rel in (directives.get("rule_files") or []) + (directives.get("steering_files") or []) \
                + ((directives.get("command_hooks") or {}).get("files") or []):
            add(rel)
        add((adapter.get("mcp") or {}).get("file"))
        steering = directives.get("steering_dir")
        if steering and os.path.isdir(os.path.join(root, *steering.split("/"))):
            for name in sorted(os.listdir(os.path.join(root, *steering.split("/")))):
                if name.endswith(".md"):
                    add(f"{steering}/{name}")
    add("AGENTS.md")
    return files


def agent_config_findings(root):
    """AgentShield's check categories over a project's agent configuration, ported natively (#14).

    Secrets in agent files, allow rules that admit everything, permissions with no
    deny list, hooks that pipe a download into a shell or remove the home directory,
    and MCP servers run from an unpinned package. Only allow rules are scored, so a
    deny rule naming `--no-verify` is not a finding, and `env -u VAR` is not read as
    dumping the environment (the known false positives in docs/upstream.md).
    Returns [(category, text)].
    """
    out = []
    files = agent_config_files(root)
    for rel in files:
        try:
            with open(os.path.join(root, rel), encoding=UTF8, errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        hits = scan_evidence(text)[1]
        if hits:
            out.append(("secrets", f"{rel} holds what looks like a credential: {', '.join(sorted(set(hits)))}"))
        if not rel.endswith(".json"):
            continue
        try:
            document = json.loads(text)
        except ValueError:
            continue
        permissions = document.get("permissions") if isinstance(document, dict) else None
        if isinstance(permissions, dict):
            broad = [rule for rule in permissions.get("allow") or [] if str(rule).strip() in ("*", "Bash", "Bash(*)", "Bash(*:*)")]
            if broad:
                out.append(("permissive-allow", f"{rel} allows {', '.join(broad)}: every command, a hook bypass and a push "
                                                "among them"))
            if permissions.get("allow") and not permissions.get("deny"):
                out.append(("missing-deny", f"{rel} allows tools and denies nothing"))
        hooks = document.get("hooks") if isinstance(document, dict) else None
        for entries in (hooks.values() if isinstance(hooks, dict) else []):
            for entry in entries if isinstance(entries, list) else []:
                for hook in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
                    command = str((hook or {}).get("command") or "") if isinstance(hook, dict) else ""
                    if _UNSAFE_HOOK.search(command):
                        out.append(("hook-safety", f"{rel} runs a hook that pipes a download into a shell or removes "
                                                   f"a home: {command[:80]}"))
        servers = document.get("mcpServers") if isinstance(document, dict) else None
        for name, server in (servers.items() if isinstance(servers, dict) else []):
            command = os.path.basename(str((server or {}).get("command") or ""))
            args = [str(arg) for arg in (server or {}).get("args") or []]
            packages = [arg for arg in args if not arg.startswith("-")]
            if command in _UNPINNED_RUNNER and packages and not re.search(r".@\d", packages[0]):
                out.append(("mcp-hygiene", f"{rel} runs MCP server {name} from {command} {packages[0]} with no "
                                           "version pinned"))
    return out


# ---- asking the codebase a question is a capability, not a copy (#81) --------------------

def ask_codebase(root, cfg, question, timeout=300):
    """Answer a question about the code through a configured provider, never without citations (#81).

    ao cannot embed a code-intelligence engine without breaking `dependencies = []`,
    and should not reimplement one. A provider is a command in `codebase.provider`
    (argv with {question} and {root}) that prints JSON: {"answer": text, "citations":
    [{"file": path, "lines": "a-b"}]}. With none configured, or an answer that cites
    nothing, the question is refused and what would satisfy it is named.
    Returns {"answer", "citations"} or raises ValueError.
    """
    provider = (cfg.get("codebase") or {}).get("provider") or {}
    argv = provider.get("argv") if isinstance(provider, dict) else None
    if not argv:
        raise ValueError("no codebase provider is configured: set codebase.provider.argv to a command that takes "
                         "{question} and {root} and prints {\"answer\", \"citations\"} as JSON (ctxman is the first "
                         "provider documented in docs/upstream.md)")
    rendered = [str(part).replace("{question}", question).replace("{root}", root) for part in argv]
    try:
        result = subprocess.run(rendered, cwd=root, capture_output=True, text=True, encoding=UTF8, errors="replace",
                                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"the codebase provider did not run: {exc}") from exc
    try:
        document = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError(f"the codebase provider did not answer in JSON (exit {result.returncode})") from exc
    answer = str((document or {}).get("answer") or "").strip() if isinstance(document, dict) else ""
    citations = [item for item in (document.get("citations") or []) if isinstance(item, dict) and item.get("file")] \
        if isinstance(document, dict) else []
    if not answer or not citations:
        raise ValueError("the provider's answer cites no file and line range; an answer without citations is not "
                         "an answer ao passes on")
    missing = [item["file"] for item in citations if not os.path.exists(os.path.join(root, item["file"]))]
    if missing:
        raise ValueError(f"the answer cites files that are not in this repository: {', '.join(missing[:3])}")
    return {"answer": scan_evidence(answer)[0], "citations": citations}


def safe_slug(text, fallback="note", limit=40):
    """A file-name part holding only [A-Za-z0-9._-] (#19).

    `ao mail send note "ao: kiro/* dal …"` kept the "/" and wrote into a directory
    that did not exist. Separators, wildcards, quotes, whitespace and letters
    outside ASCII all become a dash; what is left is cut to `limit`.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "")).strip("-.")[:limit].strip("-.")
    return slug or fallback


def note(root, cfg, to, title, body, urgent=False):
    """Write an architect message into the mailbox through the tool.

    So that a woken architect needs no raw Write or Edit to do its job. That
    matters because the one time it had them, it used them on the orchestrator's
    own source and built a runaway. The mailbox is the only thing an unattended
    architect should be able to write, and this is the only door to it.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    os.makedirs(box, exist_ok=True)
    slug = safe_slug(title.lower(), "not")
    kind = "ACIL" if urgent else "DECISION"
    impl, arch = mail_names(cfg)
    to = safe_slug(to or impl, impl)          # the implementer's role by default, whoever holds it (#31)
    name = f"{time.strftime('%Y%m%d-%H%M')}-{arch}-to-{to}-{kind}-{slug}.md"
    text = f"# {title}\n\n" + ("## ACİL\n\n" if urgent else "") + body.rstrip() + "\n"
    return write_mail(root, cfg, name, text, {"kind": kind.lower(), "from": arch, "to": to})


def running_slice(root):
    """The one board item review accounting treats as current.

    A malformed board may contain several running entries. Preserve the board's
    established first-entry behavior, but resolve it once so review attribution,
    start time and re-specification cannot silently select different slices.
    """
    items = board(root)["running"]
    return items[0] if items else None


def slice_boundary(item):
    """The declared review boundary for a parsed board item."""
    if not item:
        return ""
    notes = item.get("notes") or {}
    return notes.get("acceptance") or notes.get("scope") or item.get("title") or ""


# ---- a slice's boundary: a sentence, or a file the row points at (#73, #35) ------------

BOUNDARY_SECTIONS = ("invariant", "scenarios", "paths", "out of scope", "why one slice")
_BOUNDARY_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")
_NAMED_FILE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.\w+|[\w-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|"
                         r"rb|cs|swift|c|h|cpp|hpp|sql|sh|ps1|json|ya?ml|toml|md))(?![\w/-])")
_NAMED_SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})(?:\(\))?`")


def boundary_pointer(item):
    """(path, commit or None) when a board item's boundary is a file: `boundary: docs/slices/B8a.md@1a2b3c4`."""
    value = (((item or {}).get("notes") or {}).get("boundary") or "").strip()
    if not value:
        return None
    path, _, commit = value.partition("@")
    return path.strip(), (commit.strip() or None)


def boundary_sections(text):
    """{section: body} for the headed parts of a boundary file (#73)."""
    sections, current = {}, None
    for line in text.splitlines():
        heading = re.match(r"^#{1,4}\s+(.+?)\s*$", line)
        if heading:
            name = heading.group(1).strip().lower()
            current = next((section for section in BOUNDARY_SECTIONS if section in name), None)
            if current:
                sections.setdefault(current, "")
            continue
        if current:
            sections[current] += line + "\n"
    return sections


def read_boundary(root, item):
    """The boundary file a slice points at, read at the commit the pointer names (#73).

    The boundary lived as prose in a table cell, and twice on 2026-09-07/08 the
    boundary itself was what was wrong, found mid-slice by an implementer that had
    started, with no diff to show what changed. A file read at a named commit is
    one source of truth, and when it has changed since, the change travels to the
    reviewer as a diff. None when the item's boundary is a sentence.

    Returns {"file", "commit", "changed", "sha256", "label", "text", "sections", "problem"}.
    """
    pointer = boundary_pointer(item)
    if not pointer:
        return None
    path, commit = pointer
    out = {"file": path, "commit": commit, "changed": False, "sha256": None, "sections": {}, "problem": None}
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, path))
    if os.path.isabs(path) or not target.startswith(real_root + os.sep):
        out["problem"] = "it is not a path inside the repository"
    elif commit and not _BOUNDARY_COMMIT.fullmatch(commit):
        out["problem"] = f"{commit!r} is not a commit id"
    else:
        try:
            if commit:
                data = _git_output(root, "show", f"{commit}:{path}")
            else:
                with open(target, "rb") as fh:
                    data = fh.read()
        except (OSError, RuntimeError) as exc:
            out["problem"] = str(exc)
    if out["problem"]:
        out.update(label=f"{path} (unreadable)",
                   text=f"the boundary file {path} cannot be read: {out['problem']} - say so as a finding")
        return out
    content = data.decode(UTF8, "replace")
    diff = ""
    if commit:
        try:
            diff = _git_output(root, "diff", commit, "--", path).decode(UTF8, "replace")
        except RuntimeError:
            diff = ""
    label = f"{path} at {commit[:12]}" if commit else f"{path} as it stands in the worktree"
    text = f"the boundary file {label}:\n\n{content}"
    if diff.strip():
        label += ", changed since"
        text += (f"\n\nThe file has changed since {commit[:12]}. The change is part of what you judge: "
                 f"a boundary that moved mid-slice is a finding unless a decision records it.\n{diff}")
    out.update(changed=bool(diff.strip()), sha256="sha256:" + hashlib.sha256(data).hexdigest(), label=label,
               text=text, sections=boundary_sections(content))
    return out


def declared_paths(item, boundary=None):
    """[(path, new)] a slice declares: its boundary file's Paths section, or its row's `paths:` note."""
    if boundary and boundary.get("sections", {}).get("paths"):
        raw = [line.strip().lstrip("-*").strip() for line in boundary["sections"]["paths"].splitlines()]
    else:
        raw = (((item or {}).get("notes") or {}).get("paths") or "").split(",")
    out = []
    for entry in raw:
        entry = entry.strip().strip("`").strip()
        new = bool(re.search(r"\((?:new|yeni)\)$", entry, re.I))
        path = re.sub(r"\s*\((?:new|yeni)\)$", "", entry, flags=re.I).strip().strip("`").strip()
        if path:
            out.append((path, new))
    return out


def _symbol_owners(root, symbol):
    """Tracked files that define a symbol, by a definition keyword in front of its name."""
    pattern = rf"(def|class|function|interface|type|struct|enum|trait|fn|func|const|let|var)[[:space:]]+{symbol}"
    try:
        listed = _git_output(root, "grep", "-l", "-z", "-w", "-E", pattern, timeout=20)
    except RuntimeError:
        return []
    return [os.fsdecode(path) for path in listed.split(b"\0") if path]


def boundary_conflicts(root, item):
    """What a slice's declared paths cannot hold, found when it is registered (#35).

    2026-09-07: B8a named eight paths while its own acceptance could only be met
    by touching a ninth; the implementer found out mid-slice and lost three hours
    waiting on a decision. Advisory prose matching, so it names and never blocks:
    a declared path that neither exists nor is marked `(new)`, a file the
    acceptance names outside the declared paths, and the file defining a symbol
    the acceptance names in backticks when that file is outside them. An item
    that declares no paths has nothing to check.
    """
    source = read_boundary(root, item)
    if source and source["problem"]:
        return [f"its boundary file {source['file']} cannot be read: {source['problem']}"]
    declared = declared_paths(item, source)
    if not declared:
        return []

    def inside(path):
        return any(path == d or path.startswith(d.rstrip("/") + "/") for d, _ in declared)

    out = [f"{path} is declared but does not exist; write `(new)` after it if the slice creates it"
           for path, new in declared if not new and not os.path.exists(os.path.join(root, path))]
    if source:
        text = "\n".join(body for name, body in source["sections"].items()
                         if name not in ("paths", "out of scope", "why one slice")) \
            or source["text"]
    else:
        text = slice_boundary(item)
    tracked = None
    for named in sorted(set(_NAMED_FILE.findall(text))):
        if inside(named) or any(d.endswith("/" + named) for d, _ in declared):
            continue
        if "/" in named:
            if os.path.exists(os.path.join(root, named)):
                out.append(f"the acceptance names {named}, outside the declared paths")
            continue
        if tracked is None:
            try:
                tracked = [os.fsdecode(p) for p in _git_output(root, "ls-files", "-z").split(b"\0") if p]
            except RuntimeError:
                tracked = []
        owners = [p for p in tracked if os.path.basename(p) == named]
        if owners and not any(inside(p) for p in owners):
            out.append(f"the acceptance names {named} ({', '.join(owners[:3])}), outside the declared paths")
    for symbol in sorted(set(_NAMED_SYMBOL.findall(text)))[:10]:
        owners = _symbol_owners(root, symbol)
        if owners and not any(inside(p) for p in owners):
            out.append(f"the acceptance names `{symbol}`, defined in {', '.join(owners[:3])}, "
                       "outside the declared paths")
    return out


def boundary_advice(root, cfg, item):
    """Everything worth saying about one item's boundary before it is worked (#35, #73)."""
    notes = (item or {}).get("notes") or {}
    out = boundary_conflicts(root, item)
    if notes.get("boundary") and notes.get("acceptance"):
        out.append("it has both a boundary file and an acceptance sentence; one source of truth, so keep the file")
    limit = settings.get(cfg, "boundary.inline_max_chars")
    sentence = notes.get("acceptance") or ""
    if not notes.get("boundary") and len(sentence) > limit:
        out.append(f"its acceptance is {len(sentence)} characters; above boundary.inline_max_chars ({limit}) it "
                   "belongs in a file the row points at with `boundary: path@commit`")
    return out


DECISION_CHAIN = "ao-decision-row-v1"


def decisions_path(root):
    return os.path.join(root, ".ao", "ledger", "decisions.jsonl")


def decision_rows(root):
    """Architect decisions in the order `ao decide` recorded them (#65).

    Rows written before the ledger was chained are read as its legacy prefix. A
    row added by hand after a chained one breaks the chain and the read raises:
    a re-specification nobody recorded must not reset a round budget.
    """
    from .storage import read_chained_jsonl
    return [row for row in read_chained_jsonl(decisions_path(root), DECISION_CHAIN, legacy_prefix=True)
            if isinstance(row, dict)]


def respecified_at(root, item=None):
    """When the architect last re-specified the current slice (`ao decide --scope <id>`).

    None when the decision ledger cannot be trusted: the budget then stands.
    """
    item = item or running_slice(root)
    item_id = ((item or {}).get("id") or (item or {}).get("key") or "").strip()
    if not item_id:
        return None
    try:
        rows = decision_rows(root)
    except Exception:
        return None
    last = 0
    for r in rows:
        if r.get("by") == "architect" and (r.get("scope") or "").strip() == item_id:
            try:
                last = max(last, int(r.get("at") or 0))
            except (TypeError, ValueError):
                continue
    return last or None


def _recorded_review_slice(root, reviews_dir, row):
    """The slice of a review recorded before ledger rows carried it, from its unchanged file."""
    try:
        with open(os.path.join(root, reviews_dir, str(row.get("artefact"))), "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if "sha256:" + hashlib.sha256(data).hexdigest() != row.get("sha256"):
        return None
    evidence = review_evidence(data.decode(UTF8, "replace"))
    return (evidence or {}).get("slice") if isinstance(evidence, dict) else None


def rounds(root, reviews_dir):
    """Completed prospective review rounds spent on the current slice (#65).

    A round is a review that produced a verdict, read from the chained review
    ledger in the order ao recorded it. Deleting, emptying or back-dating a review
    file changes nothing, and neither does editing the board's `since:`, which an
    implementer can reach. Only index-candidate reviews of the running board
    item's exact ID count - HEAD and candidate bytes are not slice identity. The
    count runs back to the newest approval that could authorise, or to the
    architect's newest recorded re-specification of the item. A fallback's
    approval after a rejection of the same candidate authorises nothing, so it
    ends nothing: the rejection still counts. UNAVAILABLE and INVALID are not
    rounds; nobody completed a review.

    Review files from before the ledger have no row and are read as they always
    were, so a slice that began before it keeps its count.
    """
    current = running_slice(root)
    slice_id = ((current or {}).get("id") or (current or {}).get("key") or "").strip()
    if not slice_id:
        return 0
    respec = respecified_at(root, current) or 0

    from .storage import read_chained_jsonl
    try:
        rows = read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
    except Exception:
        rows = []                       # an unreadable ledger is reported elsewhere; files still count
    events, recorded, rejected = [], set(), set()
    for order, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("kind") != "index-candidate":
            continue
        recorded.add(row.get("artefact"))
        verdict, candidate = row.get("verdict"), row.get("candidate")
        if verdict not in ("APPROVED", "NEEDS_CHANGES"):
            continue
        row_slice = row["slice"] if "slice" in row else _recorded_review_slice(root, reviews_dir, row)
        mine = (row_slice or "").strip() == slice_id
        try:
            at = int(row.get("at") or 0)
        except (TypeError, ValueError):
            at = 0
        if verdict == "NEEDS_CHANGES":
            if candidate:
                rejected.add(candidate)
            if mine:
                events.append((at, 1, order, "round"))
        elif mine and row.get("authorizable") is True \
                and not (row.get("fallback") and candidate in rejected):
            events.append((at, 1, order, "approved"))

    for order, (f, v) in enumerate(reviews(root, reviews_dir, limit=None)):
        if f in recorded or v not in ("APPROVED", "NEEDS_CHANGES"):
            continue
        path = os.path.join(root, reviews_dir, f)
        try:
            at = int(os.path.getmtime(path))
            body = open(path, errors="replace", encoding=UTF8).read(100_000)
        except OSError:
            continue
        evidence = review_evidence(body)
        if not evidence or evidence.get("kind") != "index-candidate" \
                or (evidence.get("slice") or "").strip() != slice_id \
                or evidence.get("review_status") in ("unavailable", "invalid"):
            continue
        events.append((at, 0, -order, "approved" if v == "APPROVED" else "round"))

    n = 0
    for at, _, _, what in sorted(events, reverse=True):
        if at < respec or what == "approved":
            break
        n += 1
    return n


def mailbox(root, mail_dir):
    d = os.path.join(root, mail_dir)
    files = [f for f in sorted(os.listdir(d)) if f != "README.md" and f.endswith(".md")] if os.path.isdir(d) else []
    if mail_store_mode(root) != "append-only":
        return files
    # The queue is derived from the store: a view file removed unhandled still counts (#80).
    try:
        rows = mail_store_rows(root)
    except Exception:
        return files
    stored = {row.get("id") for row in rows if row.get("event") == "message"}
    handled = {row.get("id") for row in rows if row.get("event") == "handled"}
    return sorted((stored - handled) | {name for name in files if name not in stored})


REMOTE_PREFIX = "refs/remotes/origin/"


def _default_remote_branch(root):
    """The remote default branch to measure a checkout against, as a full ref, or None.

    origin/HEAD can still name a branch the remote has since deleted, so a candidate
    is used only when it resolves to a commit.
    """
    candidates = []
    try:
        ref = _git_output(root, "symbolic-ref", "--quiet",
                          "refs/remotes/origin/HEAD").decode(UTF8, "replace").strip()
        if ref.startswith(REMOTE_PREFIX):
            candidates.append(ref)
    except RuntimeError:
        pass
    for candidate in (*candidates, REMOTE_PREFIX + "main", REMOTE_PREFIX + "master"):
        try:
            _git_output(root, "rev-parse", "--verify", "--quiet", candidate + "^{commit}")
            return candidate
        except RuntimeError:
            continue
    return None


def git_state(root):
    """Where this checkout stands, not only what it holds.

    An ahead count alone reads as current on a checkout that is weeks behind. On
    2026-09-14 `ao status` printed "0 commits unpushed" for a branch 77 commits
    behind main and already merged into it, and two backlog rows were written from
    measurements taken in that checkout. So measure against the remote default
    branch, report behind as well as ahead, say when the branch is already merged,
    and say "?" when there is nothing to compare against instead of "0".

    "ahead" stays a string for existing readers; "behind" is an int or None.
    """
    ref = _default_remote_branch(root)
    ahead = behind = None
    if ref:
        try:
            counts = _git_output(root, "rev-list", "--left-right", "--count",
                                 f"{ref}...HEAD").decode(UTF8, "replace").split()
            if len(counts) == 2 and all(c.isdigit() for c in counts):
                behind, ahead = int(counts[0]), int(counts[1])
        except RuntimeError:
            pass
    merged = False
    if ahead == 0 and behind:
        # The default branch itself, checked out behind its remote, needs a pull; it
        # is not a merged branch. A detached head inside the base counts as merged.
        try:
            head = _git_output(root, "symbolic-ref", "--quiet", "HEAD").decode(UTF8, "replace").strip()
        except RuntimeError:
            head = ""
        merged = head != "refs/heads/" + ref[len(REMOTE_PREFIX):]
    known = ahead is not None
    return {
        "log": _git_text(root, "log", "--oneline", "-4").split("\n"),
        "dirty": [l for l in _git_text(root, "status", "--short").split("\n") if l.strip()],
        "ahead": str(ahead) if known else "?",
        "behind": behind,
        "base": ref[len("refs/remotes/"):] if known else None,
        "merged": merged,
    }
