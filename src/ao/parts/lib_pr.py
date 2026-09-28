"""Pull requests: what GitHub says about this checkout's branches, read through gh, each fact mailed once.

A part of src/ao/lib.py, run in its namespace by `_part` as the parts moved out of it are (#44); it is
not importable on its own (PR-WATCH).
"""


# ---- pull requests, read and never written (PR-WATCH) ------------------------------------------
#
# ao governs what lands in a checkout; what becomes of a branch once a person pushes it is GitHub's to
# say. A check that failed there, a conflict with the base branch or a reviewer asking for changes
# waited until somebody opened the page. `ao pr watch --once` reads the open pull requests of this
# checkout's branches and mails the implementer each such fact once. gh is asked `pr list` and
# `pr view` and nothing else, so nothing here can write to GitHub.

PR_FACTS = ("check-failed", "conflict", "changes-requested")
PR_LIST_LIMIT = 200
PR_LIST_FIELDS = "number,headRefName,headRefOid,isCrossRepository"
PR_VIEW_FIELDS = ("number,title,url,headRefName,baseRefName,headRefOid,mergeable,reviewDecision,"
                  "statusCheckRollup,reviews")
# A check's latest run failed when it ended in one of these, a check run's conclusion or a commit
# status's state: gh's fail bucket, and STARTUP_FAILURE, a workflow that could not start, which gh
# counts as pending although nothing runs it again until somebody fixes it. A run cancelled, skipped
# or neutral has not failed; a required check that failed ended as any other did, which is why
# whether GitHub requires a check is not asked.
PR_CHECK_FAILED = ("FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE")
# A run that has not ended: a check run's status before it completes, a commit status pending or expected.
PR_CHECK_RUNNING = ("EXPECTED", "PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED")
# A review that says what its author makes of the change, and says it until they review again or it
# is dismissed. A comment says nothing of it, and a reply in a review thread is a comment.
PR_REVIEW_OPINIONS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
# What makes gh write for a terminal into a pipe, and so wrap its JSON in colour codes.
GH_TERMINAL_ONLY = ("CLICOLOR_FORCE", "GH_FORCE_TTY")
GH_AUTH_REQUIRED = 4        # gh's documented exit status for a command that needs `gh auth login`
_HEAD = re.compile(r"^[0-9a-fA-F]{7,64}$")


class GhUnreadable(RuntimeError):
    """gh gave no answer a pass can act on: it failed, timed out, is not authenticated or wrote no JSON."""


def pr_watch_path(root):
    return os.path.join(root, ".ao", "pr-watch.json")


def gh_json(root, program, *args, timeout=60):
    """gh's answer to one read in this checkout, parsed; GhUnreadable naming why when there is none.

    gh finds the repository from the checkout's remotes. It is started without a shell and with no
    terminal to prompt on, so a gh that would ask which repository is meant fails and says so,
    rather than a pass guessing. Nor is it let colour what it writes: in a shell that forces colour
    (GH_TERMINAL_ONLY), gh wrapped its JSON in escape codes even into a pipe, and every pass failed.
    """
    asked = "gh " + " ".join(args[:2])
    env = {key: value for key, value in os.environ.items() if key not in GH_TERMINAL_ONLY}
    try:
        done = subprocess.run([program, *args], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding=UTF8, errors="replace", timeout=timeout,
                              env=dict(env, GH_PROMPT_DISABLED="1", NO_COLOR="1"))
    except subprocess.TimeoutExpired:
        raise GhUnreadable(f"`{asked}` gave no answer in {timeout}s") from None
    except OSError as exc:
        raise GhUnreadable(f"`{asked}` could not start: {exc}") from None
    if done.returncode == GH_AUTH_REQUIRED:
        raise GhUnreadable("gh is not authenticated: run `gh auth login`")
    if done.returncode:
        said = "; ".join(line.strip() for line in (done.stderr or "").splitlines() if line.strip())
        raise GhUnreadable(f"`{asked}` failed with exit {done.returncode}: {redact(said)[:300] or 'it said nothing'}")
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise GhUnreadable(f"`{asked}` answered with something that is not JSON") from None


def checkout_branches(root):
    """The names of this checkout's branches: a pull request whose head is one of them is this project's."""
    refs = _git_output(root, "for-each-ref", "--format=%(refname)", "refs/heads").decode(UTF8, "replace")
    return {ref[len("refs/heads/"):] for ref in refs.splitlines() if ref.startswith("refs/heads/")}


def read_pull_requests(root, program):
    """(the open pull requests of this checkout's branches as `gh pr view` describes each, whether gh's list was cut).

    Every one is read before anything is decided, and a read that fails raises: a pull request left
    unread would look as if its facts had cleared, and they would be mailed again when they "returned".
    A fork's branch that shares a name with one of ours is not ours.
    """
    branches = checkout_branches(root)
    listed = gh_json(root, program, "pr", "list", "--state", "open", "--limit", str(PR_LIST_LIMIT),
                     "--json", PR_LIST_FIELDS)
    if not isinstance(listed, list):
        raise GhUnreadable("`gh pr list` answered with something that is not a list")
    numbers = sorted({pr["number"] for pr in listed
                      if isinstance(pr, dict) and isinstance(pr.get("number"), int) and not pr.get("isCrossRepository")
                      and isinstance(pr.get("headRefName"), str) and pr["headRefName"] in branches})
    pulls = []
    for number in numbers:
        pr = gh_json(root, program, "pr", "view", str(number), "--json", PR_VIEW_FIELDS)
        if not isinstance(pr, dict) or pr.get("number") != number or not _HEAD.match(str(pr.get("headRefOid") or "")):
            raise GhUnreadable(f"`gh pr view {number}` did not describe pull request #{number} and its head commit")
        pulls.append(pr)
    return pulls, len(listed) >= PR_LIST_LIMIT


def _check_state(check):
    """A run's state as gh reads it: a check run's conclusion once completed, its status before; a status's state."""
    if check.get("__typename") != "CheckRun":
        return str(check.get("state") or "").upper()
    status = str(check.get("status") or "").upper()
    return str(check.get("conclusion") or "").upper() if status in ("", "COMPLETED") else status


def pr_latest_checks(rollup):
    """[(a check's latest run, whether an earlier run of it failed)] for each check, in the order GitHub lists them.

    GitHub's rollup keeps every run of a check: a job re-run after it failed stands beside the
    failure, which read as a failed check long after the re-run passed. Only the run that started
    last counts, as `gh pr checks` counts it: a check run is one check by its name and workflow, a
    commit status by its context. gh also tells apart the event that started a run, which
    `gh pr view` does not say, so two runs of one workflow started by two events on one head - push
    and pull_request - are one check here, the later-started. A run not started yet, which gh dates
    to year 1, or whose start cannot be read is the earliest; of two started together the one listed
    later counts.
    """
    runs = {}
    for index, check in enumerate(rollup or []):
        if not isinstance(check, dict):
            continue
        key = (("run", str(check.get("name") or ""), str(check.get("workflowName") or ""))
               if check.get("__typename") == "CheckRun" else ("status", str(check.get("context") or "")))
        runs.setdefault(key, []).append((_ledger_time(check.get("startedAt")), index, check))
    latest = []
    for attempts in runs.values():
        attempts.sort(key=lambda attempt: attempt[:2])
        *earlier, (_, index, last) = attempts
        latest.append((index, last, any(_check_state(check) in PR_CHECK_FAILED for _, _, check in earlier)))
    return [(check, failed_before) for _, check, failed_before in sorted(latest, key=lambda row: row[0])]


def pr_changes_requested_by(reviews):
    """The logins of the reviewers whose opinion of the change asks for changes, sorted.

    A reviewer's opinion is their latest review in PR_REVIEW_OPINIONS, the one submitted last. gh's
    `latestReviews` is each reviewer's latest review of any kind, so a reviewer who asked for changes
    and then replied in a thread was read as asking for nothing: every review is read instead.
    """
    opinion = {}
    for index, review in enumerate(reviews or []):
        state = str(review.get("state") or "").upper() if isinstance(review, dict) else ""
        if state not in PR_REVIEW_OPINIONS:
            continue
        author = review.get("author") if isinstance(review.get("author"), dict) else {}
        login, when = str(author.get("login") or "a reviewer"), (_ledger_time(review.get("submittedAt")), index)
        if login not in opinion or when > opinion[login][0]:
            opinion[login] = (when, state)
    return sorted(login for login, (_, state) in opinion.items() if state == "CHANGES_REQUESTED")


def pr_facts(pr):
    """({fact: what shows it}, {facts GitHub has not decided}) for one pull request.

    A failed check is one whose latest run on the head commit failed (pr_latest_checks). While a
    check that failed runs again its failure is neither new nor cleared - the run decides it - as a
    conflict is neither while GitHub is still computing `mergeable` (UNKNOWN): a failure mailed,
    re-run and failed again on one head is not mailed twice. Changes are requested when the review
    decision says so or a reviewer's opinion does (pr_changes_requested_by): a repository that
    requires no review has no decision to read.
    """
    found, undecided, failed, rerun = {}, set(), [], False
    for check, failed_before in pr_latest_checks(pr.get("statusCheckRollup")):
        run, state = check.get("__typename") == "CheckRun", _check_state(check)
        if state in PR_CHECK_FAILED:
            failed.append({"name": str((check.get("name") if run else check.get("context")) or "?"),
                           "workflow": str((check.get("workflowName") if run else "") or ""), "state": state,
                           "link": str((check.get("detailsUrl") if run else check.get("targetUrl")) or "")})
        elif failed_before and state in PR_CHECK_RUNNING:
            rerun = True
    if failed:
        found["check-failed"] = failed
    elif rerun:
        undecided.add("check-failed")
    mergeable = str(pr.get("mergeable") or "").upper()
    if mergeable == "CONFLICTING":
        found["conflict"] = []
    elif mergeable != "MERGEABLE":
        undecided.add("conflict")
    asked = pr_changes_requested_by(pr.get("reviews"))
    if asked or str(pr.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED":
        found["changes-requested"] = asked
    return found, undecided


def _github_text(value):
    """Text GitHub supplied, on one line and unable to carry a mail marker.

    A marker is two hashes and a word (`## URGENT`, `## STOP`), and ao reads one anywhere in a
    message: a title, a check's name or the link an integration gave its check holding one would
    make this mail urgent, or a stop, for every reader. Every double hash is broken with a space,
    and a value that would break a line is written as a JSON string (#55).
    """
    return review_header_value(re.sub(r"#(?=#)", "# ", str(value)))


def pr_mail_body(pr, fact, shown):
    """The mail for one new fact: the pull request, what GitHub says of its head, and what to look at."""
    number, head = pr["number"], pr["headRefOid"]
    branch, base = _github_text(pr.get("headRefName") or "?"), _github_text(pr.get("baseRefName") or "?")
    heading = {"check-failed": "a check failed", "conflict": f"it conflicts with {base}",
               "changes-requested": "changes were requested"}[fact]
    lines = [f"# Pull request #{number}: {heading}", "",
             f"Pull request #{number} from `{branch}` into `{base}`: {_github_text(pr.get('url') or '')}",
             f"Title: {_github_text(pr.get('title') or '')}", "",
             f"GitHub says this of its head commit {head}:", ""]
    if fact == "check-failed":
        for check in shown:
            workflow = f" ({_github_text(check['workflow'])})" if check["workflow"] else ""
            link = f": {_github_text(check['link'])}" if check["link"] else ""
            lines.append(f"- `{_github_text(check['name'])}`{workflow} {check['state']}{link}")
        look = f"`gh pr checks {number}`, and the log behind each link"
    elif fact == "conflict":
        lines.append(f"- mergeable: CONFLICTING, against `{base}`")
        look = f"bring `{base}` into `{branch}` and resolve the conflicting files; `gh pr view {number}` shows the rest"
    else:
        lines.append(f"- changes requested by {', '.join(_github_text(login) for login in shown) or 'a reviewer'}")
        look = f"`gh pr view {number} --comments`, for what each reviewer asked"
    lines += ["", f"What to look at: {look}.", "",
              "ao read this through gh and wrote nothing to GitHub. It mails this fact once for this head "
              "commit: again after a new head, or when the fact clears and returns."]
    return "\n".join(lines) + "\n"


def _unwritten_mail_name(root, cfg, stem):
    """`<stem>.md`, or `<stem>-2.md` and on when a message of that name was ever written.

    A mail's name is its id, and the message store takes an id in once (#80): a fact that clears
    and returns on the same head within one minute would otherwise be written under the name its
    first mail had, and never reach the store.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    written = {row.get("id") for row in mail_log(root, 5000) if row.get("event") == "written"}
    name, n = f"{stem}.md", 2
    while name in written or os.path.exists(os.path.join(box, name)):
        name, n = f"{stem}-{n}.md", n + 1
    return name


def write_pr_mail(root, cfg, pr, fact, shown):
    """Mail one new fact to whoever holds the implementer role, through the writer every message ao writes takes.

    The name carries the pull request, the fact and the head it stands at, so two facts never share
    a file; the envelope carries them too, for a program that reads it.
    """
    implementer, _ = mail_names(cfg)
    to, head = safe_slug(implementer, "implementer"), pr["headRefOid"]
    stem = f"{time.strftime('%Y%m%d-%H%M')}-pr-watch-to-{to}-PR-{pr['number']}-{fact}-{head[:12]}"
    return write_mail(root, cfg, _unwritten_mail_name(root, cfg, stem), pr_mail_body(pr, fact, shown),
                      {"kind": "pr", "class": "needs-read", "from": "pr-watch", "to": to,
                       "pr": pr["number"], "condition": fact, "head": head})


def pr_watch_state(root):
    """(what was mailed, as {number: {fact: {head, mail, at}}}; why the record could not be read, or None).

    A record ao cannot read is set aside rather than trusted: what stands is mailed once more, and the
    pass says so.
    """
    try:
        with open(pr_watch_path(root), encoding=UTF8) as fh:
            document = json.load(fh)
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    reported = document.get("reported") if isinstance(document, dict) else None
    if not isinstance(reported, dict) or not all(isinstance(facts, dict) for facts in reported.values()):
        return {}, "it holds no record of mailed facts in the shape ao writes"
    return reported, None


def _pr_watch_document(state):
    """What the record holds for `state`: each pull request that has a fact mailed and standing."""
    return (json.dumps({"reported": {number: facts for number, facts in state.items() if facts}}, indent=2,
                       sort_keys=True) + "\n").encode(UTF8)


def pr_watch(root, cfg, program):
    """One pass: read every open pull request of this checkout's branches, mail each new fact, record what stands.

    A fact is new when the pull request's head is not the one it was last mailed at, or when it was
    not standing at the last pass; a fact that cleared is forgotten, so it is mailed again if it
    returns, and so is a pull request that is no longer open. A fact is recorded as soon as it is
    mailed, so a pass stopped part way - killed, or its terminal closed - never mails it twice. One
    pass runs at a time, under a lock beside the record: two would read one record and both mail
    every new fact. Returns None when another pass holds the lock, else {"read", "sent", "standing",
    "cut", "problem"}.
    """
    from .storage import LedgerLockTimeout, _exclusive_lock
    lock = pr_watch_path(root) + ".lock"
    try:
        with _exclusive_lock(lock, timeout=0):
            return _pr_watch_pass(root, cfg, program)
    except LedgerLockTimeout as exc:
        if exc.path != lock:
            raise
        return None


def _pr_watch_pass(root, cfg, program):
    """The pass pr_watch runs under its lock; the record is written whenever what it holds changed."""
    from .storage import replace_file_durably
    pulls, cut = read_pull_requests(root, program)
    reported, problem = pr_watch_state(root)
    state = {number: dict(facts) for number, facts in reported.items()}
    sent, standing = [], []
    on_disk = None if problem else _pr_watch_document(reported)   # a record ao could not read is replaced

    def save():
        nonlocal on_disk
        document = _pr_watch_document(state)
        if document != on_disk:
            replace_file_durably(pr_watch_path(root), document)
            on_disk = document

    try:
        for pr in pulls:
            number, head = str(pr["number"]), pr["headRefOid"]
            before = reported.get(number) or {}
            found, undecided = pr_facts(pr)
            after = state[number] = {fact: mailed for fact, mailed in before.items()
                                     if fact in found or fact in undecided}
            for fact in PR_FACTS:
                if fact not in found:
                    continue
                if isinstance(before.get(fact), dict) and before[fact].get("head") == head:
                    standing.append((pr["number"], fact, head))
                    continue
                mail = write_pr_mail(root, cfg, pr, fact, found[fact])
                after[fact] = {"head": head, "mail": mail, "at": int(time.time())}
                save()                       # before the next mail is written, not when the pass ends
                sent.append((pr["number"], fact, head, mail))
        if not cut:                  # a list gh cut short leaves a pull request unread, not closed
            read = {str(pr["number"]) for pr in pulls}
            state = {number: facts for number, facts in state.items() if number in read}
    finally:
        save()
    return {"read": len(pulls), "sent": sent, "standing": standing, "cut": cut, "problem": problem}
