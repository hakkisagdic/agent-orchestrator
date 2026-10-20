"""What needs doing now, and whose hand does it (NEXT).

A part of src/ao/lib.py, run in its namespace by `_part` as the parts split out of it are; it is
not importable on its own.

`ao board` answers where each item is, `ao status` whether an agent is alive, `ao gain` what work
cost. None of them answers the question a person asks on coming back to a project, or the one an
agent asks when its turn ends: which command, in what order, by whom. With eighty commands, about
fifty of them needing a project, that question was being answered from memory — which is where a
landing chain stalls: a candidate verified but never reviewed, a review approved but never granted,
a stand-in request left in ~/.ao unread, a route no person has accepted holding every review that
would follow it.

This is the data layer of that answer: it reads the same files every other surface reads and
returns items, so `ao next`, the `ao_next` MCP tool and a later page cannot disagree about what is
waiting. Two rules hold it together.

- every item names its actor. A person's item is returned as the command and run by no one: nothing
  here records, grants or starts anything. `ao harness seen`, `ao collect-review --by` and
  `ao waive --by` stay a person's own hands;
- nothing here is judged. The pipeline items are the reasons `ao commit-ok` would refuse, read
  through the readers it reads — the staged candidate, the newest verification, the review bound to
  that candidate or a person's waiver, the grant — so a candidate this calls ready is one the gate
  agrees with. Where a reader cannot be reached, the item says it cannot be read rather than
  guessing. `ao commit-ok` holds further checks of its own (a boundary file's pin, a review's
  integrity); those are the grant's to refuse, and the item after them names the refusal it recorded,
  never a stage of its own. The capability matrix is not one of them: whether a strict project's
  matrix resolves, and whether the grant carries the schema that mode requires, decide whether any
  grant can carry these bytes, so both are read here — and a matrix that does not resolve is blamed
  as the matrix, never as the row it prevented.
"""

PERSON = "person"
WAITING_PERSON = ("human", "person", "owner")
REQUEST_LIMIT = 5
PROPOSAL_LIMIT = 3

NEXT_ITEM_KEYS = ("who", "kind", "why", "command", "detail", "slice")


def next_item(who, kind, why, command=None, detail=None, slice=None):
    """One thing to do: who does it, what state says so, why, and the command that closes it.

    Text taken from a ledger - a slice's title, a request's nonce, a gate's name - is shown escaped and on one
    line, so a board an implementer can write cannot start a line of its own or hide a direction mark in what a
    person reads as ao's words (LEADS-MCP)."""
    def shown(text):
        return None if text is None else shown_text(" ".join(str(text).split()))
    return {"who": who, "kind": shown(kind), "why": shown(why), "command": shown(command),
            "detail": shown(detail), "slice": shown(slice)}


def reason_of(exc):
    """An exception as one escaped, bounded line: a ledger's own error can be long, and it can hold a
    control character that would start a line of its own in what a person reads as ao's words (NEXT)."""
    return shown_text(" ".join(str(part) for part in (type(exc).__name__, exc) if str(part).strip()))[:200]


def next_item_problem(exc, what):
    """A store that cannot be read, as an item that says so: never an empty list, which would read as nothing
    waiting (NEXT)."""
    return next_item(PERSON, f"{what}-unreadable", f"{what} cannot be read: {reason_of(exc)}")


def next_project_key(root):
    """This project's key in ~/.ao as `project_key` resolves it, without writing the registry or
    `.ao/project-key` when the project has not been registered yet (NEXT)."""
    base = os.path.basename(os.path.abspath(root).rstrip("/\\")) or "root"
    real = os.path.realpath(root)
    if not os.path.isdir(os.path.join(real, ".ao")):
        return base
    known = project_registry()
    mine = next((name for name, row in known.items() if row.get("root") == real), None)
    if mine:
        return mine
    held = {name.lower() for name, row in known.items()
            if row.get("root") != real and os.path.isdir(os.path.join(row["root"], ".ao"))}
    try:
        with open(os.path.join(real, ".ao", PROJECT_KEY_FILE), encoding=UTF8) as fh:
            mark = json.load(fh)
    except (OSError, ValueError):
        mark = {}
    wanted = mark.get("key") if isinstance(mark, dict) and mark.get("root") == real else None
    if _usable_key(wanted) and wanted.lower() not in held:
        return wanted
    if base.lower() not in held:
        return base
    digest = hashlib.sha256(real.encode(UTF8)).hexdigest()
    mine = next((f"{base}-{digest[:length]}" for length in (8, 12, 16, 64)
                 if f"{base}-{digest[:length]}".lower() not in held), None)
    suffix = 2
    while mine is None or mine.lower() in held:
        mine = f"{base}-{digest}-{suffix}"
        suffix += 1
    return mine


def next_index_candidate(root):
    """`index_candidate(root)` with any tree objects `git write-tree` makes kept in a scratch directory,
    so reading a newly staged candidate writes nothing to `.git/objects` or `.git/index` (NEXT)."""
    import shutil
    import tempfile
    try:
        objects = os.path.abspath(os.path.join(
            root, _git_output(root, "rev-parse", "--git-path", "objects").decode(UTF8, "surrogateescape").strip()
        ))
        index = os.environ.get("GIT_INDEX_FILE") or os.path.abspath(os.path.join(
            root, _git_output(root, "rev-parse", "--git-path", "index").decode(UTF8, "surrogateescape").strip()
        ))
    except Exception:
        return index_candidate(root)
    prior = {key: os.environ.get(key) for key in (
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE"
    )}
    with tempfile.TemporaryDirectory(prefix="ao-next-git-") as scratch:
        scratch_objects = os.path.join(scratch, "objects")
        os.makedirs(scratch_objects, exist_ok=True)
        alt = [prior["GIT_OBJECT_DIRECTORY"], objects, prior["GIT_ALTERNATE_OBJECT_DIRECTORIES"]]
        os.environ["GIT_OBJECT_DIRECTORY"] = scratch_objects
        os.environ["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = os.pathsep.join(part for part in alt if part)
        if os.path.isfile(index):
            copied = os.path.join(scratch, "index")
            shutil.copyfile(index, copied)
            os.environ["GIT_INDEX_FILE"] = copied
        try:
            return index_candidate(root)
        finally:
            for key, value in prior.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def open_review_requests(root):
    """This project's stand-in requests whose answer has not been collected, oldest first, and the count of
    files in that directory ao could not read as a request (NEXT).

    A request is metadata beside its text, outside the tree an agent writes; `ao collect-review`
    takes one while its `collected` is empty, so what this returns is exactly what that command would
    still accept. `~/.ao/review-requests` keeps no index, so the directory is listed here. A file there
    that names no request - truncated, rewritten, a nonce that does not match its own name - is counted
    and named, because the requests an agent can no longer read are the ones a person must not lose.
    """
    directory = review_requests_dir(root, next_project_key(root))
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return [], 0
    out, unreadable = [], 0
    for name in names:
        if not name.endswith(".json"):
            continue
        nonce = name[:-len(".json")]
        meta = None
        if re.fullmatch(r"[0-9a-f]{32}", nonce):
            try:
                with open(os.path.join(directory, name), encoding=UTF8) as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and data.get("nonce") == nonce:
                    meta = data
            except (OSError, ValueError):
                meta = None
        if meta is None:
            unreadable += 1
        elif not meta.get("collected"):
            out.append(dict(meta, path=os.path.join(directory, f"{nonce}.md")))
    return sorted(out, key=lambda row: row.get("at") or 0), unreadable


def person_items(cfg):
    """What stands in this project until a person acts, as items (NEXT).

    Six states ao cannot cross on its own, each read from where it is kept: harness configuration no
    person has read, which holds every review, wake, hunt and lane that would start a command; a
    stand-in review request whose answer no person has collected; urgent unacknowledged mail; an open
    rule proposal; a slice blocked waiting on a person; a review switched off in the config no person
    recorded.
    """
    root = cfg["root"]
    out = []
    try:
        key = next_project_key(root)
        unseen = harness_unseen(harness_config_changes(root, key=key), harness_seen_record(root, key=key) or {})
    except Exception as exc:
        out.append(next_item_problem(exc, "what a harness reads from this tree"))
    else:
        if unseen:
            out.append(next_item(PERSON, "harness-unseen",
                                 f"{harness_config_line(unseen)}: no person has read it, and nothing that starts a "
                                 "command does until one has",
                                 "ao harness seen --by <your name> --ask",
                                 detail=harness_config_digest(unseen)))
    staged = None
    try:
        requests, unreadable = open_review_requests(root)
    except Exception as exc:
        requests, unreadable = [], 0
        out.append(next_item_problem(exc, "the review requests"))
    if unreadable:
        out.append(next_item(PERSON, "review-request-unreadable",
                             f"{unreadable} request file(s) in ~/.ao/review-requests name no request ao can read: "
                             "one that was cut short or rewritten answers nothing. Its review has to be asked "
                             "again, which the chain below names; this says a person's copy may be in there"))
    try:
        staged = next_index_candidate(root)["digest"]
    except Exception:
        staged = None       # nothing to bind a request to is no reason to hide what a person must not lose
    # A request binds one candidate's bytes: `ao collect-review` refuses an answer while nothing is staged or once
    # they are no longer staged, so nobody can carry it back. A superseded request is not a person's task — but
    # dropping it in silence would hide that a review has to be asked again, so it is named, with no command (NEXT).
    live = [row for row in requests if staged is not None and row.get("candidate") == staged]
    collected = {row.get("nonce") for row in live}
    dead = [row for row in requests if row.get("nonce") not in collected]
    for request in live[:REQUEST_LIMIT]:
        out.append(next_item(PERSON, "review-uncollected",
                             f"request {request['nonce']} waits for the answer a person carries back"
                             + (f" of {request['slice']}" if request.get("slice") else ""),
                             f"ao collect-review {request['nonce']} --response <file> --model <model> "
                             "--by <your name>",
                             slice=request.get("slice"), detail=request.get("path")))
    for request in dead[:REQUEST_LIMIT]:
        out.append(next_item(PERSON, "review-superseded",
                             f"request {request['nonce']} binds candidate {request.get('candidate')}, which is no "
                             "longer staged: its answer cannot be collected, and no review stands recorded for it",
                             slice=request.get("slice"), detail=request.get("path")))
    if len(live) > REQUEST_LIMIT:
        out.append(next_item(PERSON, "review-uncollected-more",
                             f"{len(live) - REQUEST_LIMIT} further requests wait to be collected in "
                             "~/.ao/review-requests, oldest shown first"))
    if len(dead) > REQUEST_LIMIT:
        out.append(next_item(PERSON, "review-superseded-more",
                             f"{len(dead) - REQUEST_LIMIT} further superseded requests are kept in "
                             "~/.ao/review-requests; none of them can be collected"))
    for message in urgent_messages(root, cfg, role=None):
        if message.get("to") == "reviewer":
            continue
        out.append(next_item(PERSON, "mail-urgent",
                             f"urgent and unacknowledged: {message['id']} — {message['title']}",
                             f"ao mail ack {message['id']}"))
    try:
        rows = [row for row in proposals(root) if row.get("state") in ("open", "missing")]
    except Exception as exc:
        rows = []
        out.append(next_item_problem(exc, "the rule proposals"))
    for proposal in rows[:PROPOSAL_LIMIT]:
        question = proposal.get("question")
        missing = proposal.get("state") == "missing" or not question
        out.append(next_item(PERSON, "proposal-open",
                             f"rule proposal {proposal.get('id')} "
                             + (f"names a question ({question}) that no longer exists"
                                if proposal.get("state") == "missing"
                                else f"({question}) waits" if question else "waits")
                             + " for a person",
                             None if missing else f"ao answer {question} a|b",
                             detail=question))
    for item in board(root)["blocked"]:
        waiting = str((item.get("notes") or {}).get("waiting") or "").strip().lower()
        if waiting in WAITING_PERSON:
            out.append(next_item(PERSON, "slice-waits-human",
                                 f"{item['id']} is blocked waiting on a person: {item['title']}",
                                 f"ao slice start {item['id']}", slice=item["id"]))
    from . import features as F
    if "review" in F.unrecorded(cfg):
        out.append(next_item(PERSON, "review-off-unrecorded",
                             "review is off in .ao/config.json with no person's record, so it stays required",
                             "ao features off review --by <your name>"))
    return out


def verification_isolation_problem(root, record):
    """Why this verification cannot stand as an isolated candidate's, or None (NEXT).

    The two conditions `ao commit-ok` reads, kept here as the data layer's so a lib reader can answer
    without importing the command line: a record that never measured the isolation scope proves nothing,
    and one measured against other inputs verified another candidate's gates. tests/test_next.py holds
    this against the gate's own function, which is the only thing that keeps the two from drifting.
    """
    if "isolation_inputs" not in record:
        return "verification does not record isolation inputs — re-run `ao verify`"
    if record["isolation_inputs"] != gate_inputs(root):
        return "verification isolation inputs changed — re-run `ao verify`"
    return None


def grant_reuse_problem(cfg, grant, candidate, verification=None):
    """Why this grant cannot carry the staged candidate, or None (NEXT).

    The bindings a grant has to the bytes it approved, read exactly as the commit hook reads them: the
    schema the mode requires, the candidate, the latest verification it references, and the scope cut
    from it. What the schema carries is the mode's own - a strict project grants through a schema-3 row
    that holds a capability matrix, and a project that has since removed its matrix refuses the row that
    carried one. A matrix that does not resolve is named as the reason no grant can stand, since the
    item would otherwise blame the grant for a declaration the grant never made. A boundary file's pin
    stays `ao commit-ok`'s to refuse; this only stops `ao next` from calling a grant the hook cannot use
    a reason to commit.
    """
    from . import matrix as M
    strict = M.is_strict(cfg)
    if strict:
        try:
            M.resolve(cfg)
        except M.MatrixError as exc:
            return (f"this project's capability matrix does not resolve, so no grant can stand on it: "
                    f"{'; '.join(exc.problems[:3])}"
                    + (f" ({len(exc.problems)} problems in all)" if len(exc.problems) > 3 else ""))
        except Exception as exc:
            return f"this project's capability matrix cannot be read: {reason_of(exc)}"
        if grant.get("schema") != 3:
            return (f"the grant {grant.get('token')} is not a capability-matrix grant: this project runs strict, "
                    "so `ao commit-ok` must record one")
    if not strict and (grant.get("schema") == 3 or grant.get("matrix") is not None):
        return (f"the grant {grant.get('token')} was made under a capability matrix this project no longer "
                "declares: `ao commit-ok` must record a new one")
    if not strict and grant.get("schema") != 2:
        return f"the grant {grant.get('token')} predates index-candidate binding (schema {grant.get('schema')})"
    if grant.get("candidate") != candidate:
        granted = grant.get("candidate")
        tree = granted.get("index_tree") if isinstance(granted, dict) else granted
        return (f"the grant {grant.get('token')} is bound to another candidate: granted {tree}, "
                f"staged {candidate['index_tree']}")
    if verification is None:
        try:
            verification = latest_verification(cfg["root"])
        except Exception:
            verification = None
    if verification is not None and verification.get("id") != grant.get("verification"):
        return (f"latest verification {verification.get('id') or '<unnamed>'} is not "
                f"the grant's referenced verification {grant.get('verification') or '<missing>'}")
    scope = grant.get("scope")
    if not isinstance(scope, dict) or not isinstance(scope.get("paths"), list):
        return f"the grant {grant.get('token')} carries no candidate scope the hook can read"
    try:
        current = candidate_scope(candidate, scope["paths"])
    except (TypeError, ValueError) as exc:
        return f"the grant {grant.get('token')} has an invalid candidate scope: {reason_of(exc)}"
    if current["outside_paths"]:
        return (f"staged paths fall outside the granted scope of {grant.get('token')}: "
                f"{', '.join(current['outside_paths'][:5])}")
    if current != scope:
        return f"the grant {grant.get('token')}'s scope no longer matches the staged candidate"
    return None


def pipeline_items(cfg):
    """The staged candidate's landing chain, at the first stage that does not hold (NEXT).

    The five stages are `ao commit-ok`'s own, read with its readers. It stops at the first one that
    does not hold: an agent has one thing to do, and listing the three behind it invites it to skip
    ahead.
    """
    root = cfg["root"]
    try:
        candidate = next_index_candidate(root)
    except Exception as exc:
        return [next_item("implementer", "candidate-unreadable",
                          f"the staged candidate cannot be measured: {reason_of(exc)}", "ao candidate")]
    running = [item["id"] for item in board(root)["running"]]
    slice_id = running[0] if len(running) == 1 else None
    if not candidate["changed_paths"]:
        ready = board_graph(root)["ready"]
        return [next_item("implementer", "no-candidate",
                          "nothing is staged: an agent commits only the candidate it staged in the index",
                          f"ao slice start {ready[0]['id']}" if ready else "ao board ready",
                          slice=ready[0]["id"] if ready else None)]

    from . import features as F
    try:
        ver = latest_verification(root)
    except Exception as exc:
        return [next_item("implementer", "verification-unreadable",
                          f"the verification ledger cannot be read: {reason_of(exc)}",
                          "ao verify", slice=slice_id)]
    if not ver:
        return [next_item("implementer", "no-verification", "no gate has run on this candidate",
                          "ao verify", slice=slice_id)]
    if not ver.get("passed"):
        failed = [g["name"] for g in (ver.get("gates") or []) if not g.get("passed")]
        return [next_item("implementer", "gates-failed",
                          f"the last verification failed: {', '.join(failed) or ver.get('id')}",
                          "ao verify", slice=slice_id)]
    measured = ver.get("candidate") or {}
    if measured.get("digest") != candidate["digest"]:
        return [next_item("implementer", "verification-stale",
                          f"the index changed since {ver.get('id')} verified it: verified "
                          f"{measured.get('index_tree')}, staged {candidate['index_tree']}",
                          "ao verify", slice=slice_id)]
    if not ver.get("candidate_ready"):
        detail = "; ".join(ver.get("candidate_issues") or [])
        return [next_item("implementer", "verification-unisolated",
                          f"{ver.get('id')} did not verify an isolated staged candidate"
                          + (f": {detail}" if detail else ""), "ao verify", slice=slice_id)]
    isolation = verification_isolation_problem(root, ver)
    if isolation:
        return [next_item("implementer", "verification-unisolated", isolation, "ao verify", slice=slice_id)]
    try:
        gates_now = gate_definitions_digest(root, ver.get("profile"))
    except Exception as exc:
        return [next_item("implementer", "gates-unreadable",
                          f"the gate definitions cannot be read: {reason_of(exc)}",
                          "ao verify", slice=slice_id)]
    if gates_now != ver.get("gates_digest"):
        return [next_item("implementer", "gates-changed",
                          f"the gate definitions changed since {ver.get('id')} ran with them",
                          "ao verify", slice=slice_id)]

    if F.enabled(cfg, "review"):
        try:
            waiver, _notes = review_waiver_for(root, running, candidate["digest"])
        except Exception as exc:
            return [next_item("implementer", "waiver-unreadable",
                              f"the waiver ledger cannot be read: {reason_of(exc)}", "ao review", slice=slice_id)]
        if not waiver:
            try:
                decision = candidate_review_decision(root, cfg["reviews"], candidate["digest"])
            except Exception as exc:
                decision = {"match": None,
                            "problem": f"the review ledger cannot be read: {reason_of(exc)}"}
            if not decision.get("match"):
                return [next_item("implementer", "no-review",
                                  decision.get("problem")
                                  or "no APPROVED review is bound to these bytes: a review of another candidate "
                                     "grants nothing", "ao review", slice=slice_id)]

    try:
        grant = latest_authority_decision(root)
    except Exception as exc:
        return [next_item("implementer", "authority-unreadable",
                          f"the authority ledger cannot be read: {reason_of(exc)}", "ao commit-ok", slice=slice_id)]
    if not isinstance(grant, dict) or grant.get("granted") is not True:
        why = ("no grant for these bytes: a candidate lands only on a grant" if not isinstance(grant, dict)
               else f"the last authority decision refused: "
                    f"{'; '.join(grant.get('reasons') or []) or grant.get('token')}")
        return [next_item("implementer", "no-grant", why, "ao commit-ok", slice=slice_id)]
    problem = grant_reuse_problem(cfg, grant, candidate, ver)
    if problem:
        return [next_item("implementer", "grant-stale", problem, "ao commit-ok", slice=slice_id)]
    held = hold_state(root)
    if held:
        return [next_item(PERSON, "project-held",
                          f"this project is held by {held.get('by')}: {held.get('reason', '')}", "ao hold status")]
    urgent = [m for m in urgent_messages(root, cfg, role="implementer")
              if m.get("to") in (None, "implementer")]
    if urgent:
        message = urgent[0]
        return [next_item("implementer", "mail-urgent",
                          f"urgent message unacknowledged: {message['id']} — {message['title']}",
                          f"ao mail ack {message['id']}", slice=slice_id)]
    return [next_item("implementer", "granted",
                      f"{len(candidate['changed_paths'])} path(s) stand granted on {grant.get('token')}: commit, "
                      "and the hook takes this grant", 'ao commit -m "<message>"', slice=slice_id)]


def next_items(cfg):
    """Everything standing in this project: a person's first, then the implementer's chain (NEXT).

    The order is the point. A person's item holds work an agent can see and cannot cross, and it goes
    unnoticed precisely because everything else looks fine.
    """
    return person_items(cfg) + pipeline_items(cfg)
