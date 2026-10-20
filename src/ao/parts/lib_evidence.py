"""Platform lane runs recorded as evidence: what each platform last proved, and what still vouches (LANE-EVIDENCE).

A part of src/ao/lib.py, run in its namespace by `_part`; it is not importable on its own.

`ao gain` already showed the newest run of each platform from `.ao/ledger/platform-runs.jsonl`,
and nothing ao shipped wrote that file: the record was whatever a lane's script left there,
plain JSON that nothing could tell an edited row from one a run wrote. `ao evidence record`
reads a lane's CI run through `gh` and writes what the run says — its status and conclusion,
its head commit, its repository, and its jobs — into the chained, checkpointed
ledger. A run that is not on this repository, not finished, not successful, or not on a commit
HEAD holds as an ancestor is recorded as such and never reads as green; `ao doctor --check`
names any newest run that no longer covers HEAD. A project that recorded none is not told
anything: absence of evidence is not evidence of a failure.
"""
import os
import time

PLATFORM_RUNS_FILE = os.path.join(".ao", "ledger", "platform-runs.jsonl")
PLATFORM_RUNS_CHAIN = "ao-platform-run-v1"
PLATFORM_RUN_FIELDS = "databaseId,number,createdAt,status,conclusion,headSha,workflowName,name,jobs,url"
_PLATFORM_ALIASES = {
    "windows": ("windows", "win32", "win64"),
    "linux": ("linux", "ubuntu", "debian"),
    "macos": ("macos", "darwin", "mac"),
}


def platform_runs_path(root):
    """Where a project records its platform runs: .ao/ledger/platform-runs.jsonl."""
    return os.path.join(root, PLATFORM_RUNS_FILE)


def _platform_text(value, field):
    """One non-empty word a row carries, such as the platform or the run's id."""
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError(f"a platform run needs a {field}")
    if any(character.isspace() for character in text):
        raise ValueError(f"the {field} must be one word, not {text!r}")
    return text


def _platform_count(value, field):
    """A non-negative whole number, such as a run number or timestamp, never a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{field} must be a whole number, not {value!r}")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a whole number, not {value!r}") from None
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def _platform_repo_slug(value):
    """The lowercase `owner/repo` a run URL, remote URL, or repository object names, or ''."""
    if isinstance(value, dict):
        owner = value.get("owner")
        owner = owner.get("login") if isinstance(owner, dict) else owner
        value = value.get("nameWithOwner") or value.get("full_name") or (
            f"{owner}/{value.get('name')}" if owner and value.get("name") else ""
        )
    text = str(value or "").strip()
    if not text:
        return ""
    identity = _remote_identity(text)
    if identity is not None:
        parts = [part for part in identity[1].split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}".lower().removesuffix(".git")
    parts = [part for part in text.strip("/").split("/") if part]
    if len(parts) == 2 and not any(character.isspace() for character in text):
        return f"{parts[0]}/{parts[1]}".lower().removesuffix(".git")
    return ""


def _platform_checkout_repos(root):
    """Every `owner/repo` this checkout's git remotes name, in lowercase; set() when no remote names one."""
    try:
        entries = _git_config_entries(root, r"^(remote\..+\.(url|pushurl)|url\..+\.(insteadof|pushinsteadof))$")
    except Exception:
        entries = []
    urls = set()
    for key, value in entries:
        if key.startswith("remote.") and key.endswith((".url", ".pushurl")):
            urls.add(value.strip())
            for rule in ("insteadof", "pushinsteadof"):
                rewritten = _git_rewrite(value.strip(), entries, rule)
                if rewritten:
                    urls.add(rewritten[0].strip())
    return {slug for url in urls for slug in (_platform_repo_slug(url),) if slug}


def _platform_on_repository(root, repository, explicit=None):
    """Whether a run belongs to one of this checkout's configured git remote repositories."""
    if explicit is False:
        return False
    repo = _platform_repo_slug(repository)
    repos = _platform_checkout_repos(root)
    return bool(repo and repos and repo in repos)


def _platform_job_matches(name, platform_name):
    """Whether a CI job's name identifies `platform_name`."""
    lower = str(name or "").lower()
    tokens = _PLATFORM_ALIASES.get(platform_name, (platform_name,))
    return any(token in lower for token in tokens)


def _platform_relevant_jobs(platform_name, jobs):
    """The jobs of a run whose names identify `platform_name`."""
    if not jobs or not platform_name:
        return []
    return [job for job in jobs if isinstance(job, dict)
            and _platform_job_matches(job.get("name"), platform_name)]


def _platform_clean_jobs(raw_jobs):
    """Normalized job entries (`name`, `status`, `conclusion`) from a CI run."""
    if not isinstance(raw_jobs, list):
        return []
    jobs = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue
        jobs.append({
            "name": str(item.get("name") or "").strip(),
            "status": str(item.get("status") or "").strip().lower(),
            "conclusion": str(item.get("conclusion") or "").strip().lower(),
        })
    return jobs


def _platform_run_order(row):
    """Where a run stands in CI's own order: its creation time, run number, and run id."""
    created = row.get("created_at") or row.get("createdAt")
    when = _ledger_time(created) if isinstance(created, (int, float, str)) and not isinstance(created, bool) else 0.0
    raw_number = row.get("number")
    try:
        number = 0 if isinstance(raw_number, bool) or raw_number is None else int(raw_number)
    except (TypeError, ValueError):
        number = 0
    raw_run = str(row.get("run") or "").strip()
    run_id = int(raw_run) if raw_run.isdigit() else 0
    return when, number, run_id


def _platform_commit_placement(root, commit):
    """(known, behind) for `commit` in `root`: `behind` is None unless `commit` is an ancestor of HEAD."""
    commit = str(commit or "").strip()
    if not commit:
        return False, None
    resolved = _git_text(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}").strip()
    if not resolved:
        return False, None
    if _git_text(root, "merge-base", resolved, "HEAD").strip() != resolved:
        return True, None
    counted = _git_text(root, "rev-list", "--count", f"{resolved}..HEAD").strip()
    return True, (int(counted) if counted.isdigit() else None)


def _platform_evaluate_row(root, row):
    """Evaluate a recorded or draft row against `root` now."""
    platform_name = str(row.get("platform") or "").strip().lower()
    on_repo = _platform_on_repository(
        root,
        row.get("repository") or row.get("url"),
        row.get("on_repository"),
    )
    status = str(row.get("status") or "completed").strip().lower()
    conclusion = str(row.get("conclusion") if row.get("conclusion") is not None else "success").strip().lower()
    has_jobs = isinstance(row.get("jobs"), list)
    jobs = _platform_clean_jobs(row.get("jobs")) if has_jobs else []
    relevant = _platform_relevant_jobs(platform_name, jobs) if has_jobs else []
    jobs_total = len(relevant) if has_jobs else row.get("jobs_total")
    jobs_passed = (sum(1 for job in relevant if job["status"] == "completed" and job["conclusion"] == "success")
                   if has_jobs else row.get("jobs_passed"))
    jobs_failed = (sum(1 for job in relevant
                       if job["status"] != "completed" or job["conclusion"] not in ("success", "skipped", "neutral"))
                   if has_jobs else row.get("jobs_failed"))
    finished = (status == "completed"
                and (not has_jobs or all(job["status"] == "completed" for job in relevant)))
    jobs_ok = (not has_jobs and jobs_failed in (None, 0)) or (
        bool(jobs_passed and jobs_passed > 0) and jobs_failed == 0
    )
    succeeded = finished and conclusion == "success" and jobs_ok
    known, behind = _platform_commit_placement(root, row.get("commit"))
    return on_repo, finished, succeeded, known, behind, jobs_total, jobs_passed, jobs_failed


def read_platform_run(root, platform, run, gh=None):
    """Read one CI run through `gh run view` and return the row it describes (LANE-EVIDENCE)."""
    platform_name = _platform_text(platform, "platform").lower()
    run_id = _platform_text(run, "run")
    if run_id.startswith("-"):
        raise ValueError(f"the run must be a run id, not {run_id!r}")
    program = gh or runnable_binary("gh")
    if not program:
        raise GhUnreadable(
            "gh is not on this machine (PATH, binaries.extra_dirs, the usual install directories): "
            "ao reads a lane's run only through GitHub's CLI. Install it and run `gh auth login`"
        )
    raw = gh_json(root, program, "run", "view", run_id, "--json", PLATFORM_RUN_FIELDS)
    if not isinstance(raw, dict):
        raise GhUnreadable(f"`gh run view {run_id}` answered with something that is not a JSON object")
    seen_id = raw.get("databaseId")
    if seen_id not in (None, "") and str(seen_id).strip() != run_id:
        raise GhUnreadable(f"`gh run view {run_id}` answered for run {seen_id!r}, not {run_id}")
    raw_number = raw.get("number")
    number = (_platform_count(raw_number, "number")
              if raw_number not in (None, "") and not isinstance(raw_number, bool) else None)
    created_at = str(raw.get("createdAt") or "").strip()
    commit = str(raw.get("headSha") or "").strip()
    if any(character.isspace() for character in commit):
        raise GhUnreadable(f"`gh run view {run_id}` answered with an invalid headSha {commit!r}")
    suite = str(raw.get("workflowName") or raw.get("name") or "full").strip() or "full"
    status = str(raw.get("status") or "").strip().lower() or "unknown"
    conclusion = str(raw.get("conclusion") or "").strip().lower()
    url = str(raw.get("url") or "").strip()
    repository = _platform_repo_slug(url)
    jobs = _platform_clean_jobs(raw.get("jobs"))
    draft = {
        "platform": platform_name,
        "run": run_id,
        "number": number,
        "created_at": created_at,
        "suite": suite,
        "status": status,
        "conclusion": conclusion,
        "commit": commit,
        "repository": repository,
        "on_repository": _platform_on_repository(root, repository or url),
        "jobs": jobs,
    }
    on_repo, finished, succeeded, known, behind, jobs_total, jobs_passed, jobs_failed = _platform_evaluate_row(
        root, draft
    )
    draft.update(
        on_repository=on_repo,
        finished=finished,
        succeeded=succeeded,
        ancestor=bool(known and behind is not None),
        jobs_total=jobs_total,
        jobs_passed=jobs_passed,
        jobs_failed=jobs_failed,
        green=bool(on_repo and finished and succeeded and known and behind is not None),
    )
    return draft


def record_platform_run(root, *, platform, run, gh=None, at=None):
    """Read a platform lane's CI run through `gh` and append what it says to the chained evidence ledger.

    `platform` names the lane - windows, macos, linux, or whatever a lane is called - and is
    written without case. `run` is the CI run's id, unique within its platform so an older run
    cannot be re-appended over a newer one. `ao` reads the run itself through `gh run view` and
    records what the run says: its number and creation time, its status and conclusion, its head
    SHA (`commit`), its repository, and its jobs. A run that is not on this repository, not
    finished, not successful, or not on a commit HEAD holds as an ancestor is recorded as such
    (`green: False`) and never reads as green. Every row is chained and held to this machine's
    checkpoint store (OPTIN-ATTESTED).
    """
    platform_name = _platform_text(platform, "platform").lower()
    run_id = _platform_text(run, "run")
    if run_id.startswith("-"):
        raise ValueError(f"the run must be a run id, not {run_id!r}")
    when = int(time.time()) if at is None else _platform_count(at, "at")
    for existing in platform_runs(root):
        held = existing.get("run")
        if (existing["platform"].strip().lower() == platform_name
                and held is not None and not isinstance(held, bool)
                and str(held).strip() == run_id):
            raise ValueError(
                f"{platform_name} run {run_id} is already recorded in {PLATFORM_RUNS_FILE}")
    row = dict(read_platform_run(root, platform_name, run_id, gh=gh), at=when)
    from .storage import append_chained_in_tree
    return append_chained_in_tree(root, PLATFORM_RUNS_FILE, row, PLATFORM_RUNS_CHAIN)


def platform_runs(root):
    """Every recorded run this machine's checkpoint store attests, oldest first; [] when none is attested.

    Only the prefix recorded by this machine's checkpoint store is read and validated
    (OPTIN-ATTESTED); rows past it - written by hand or on another machine - do not count as
    evidence here and never make ao's own rows unreadable (LANE-EVIDENCE). A row that names
    no platform is left out, as `ao gain` leaves it out.
    """
    from .storage import read_chained_in_tree
    return [row for row in read_chained_in_tree(root, PLATFORM_RUNS_FILE, PLATFORM_RUNS_CHAIN)
            if isinstance(row, dict) and isinstance(row.get("platform"), str)
            and row["platform"].strip()]


def platform_run_reading(root, cfg, row):
    """One recorded run as it stands against this repository now, and whether it still vouches for it.

    `behind` is how many commits HEAD has added since the run's commit when that commit is an
    ancestor of HEAD, and `known` whether this repository holds that commit at all; both are
    read from git, never guessed. A run that is not on this repository, not finished, not
    successful, or not on a commit HEAD holds as an ancestor never reads as `green` and never
    `vouches`. `stale` is a green ancestor run more than `evidence.max_commits_behind` commits
    behind HEAD, and `vouches` is true only when `green` holds and the run is within the bound.
    """
    commit = str(row.get("commit") or "").strip()
    on_repo, finished, succeeded, known, behind, jobs_total, jobs_passed, jobs_failed = _platform_evaluate_row(
        root, row
    )
    bound = settings.get(cfg, "evidence.max_commits_behind")
    ancestor = known and behind is not None
    green = bool(on_repo and finished and succeeded and ancestor)
    stale = bool(green and behind > bound)
    return {
        "platform": row["platform"].strip().lower(),
        "run": row.get("run"),
        "number": row.get("number"),
        "created_at": row.get("created_at") or "",
        "suite": row.get("suite"),
        "status": row.get("status") or "completed",
        "conclusion": row.get("conclusion") if row.get("conclusion") is not None else "success",
        "commit": commit,
        "repository": row.get("repository") or "",
        "on_repository": on_repo,
        "finished": finished,
        "succeeded": succeeded,
        "jobs": row.get("jobs") if isinstance(row.get("jobs"), list) else [],
        "jobs_total": jobs_total,
        "jobs_passed": jobs_passed,
        "jobs_failed": jobs_failed,
        "at": row.get("at"),
        "green": green,
        "known": known,
        "behind": behind,
        "stale": stale,
        "vouches": bool(green and not stale),
    }


def platform_run_summaries(root, cfg):
    """The newest recorded run of each platform, as CI numbers and dates its runs, read against HEAD now.

    CI's own creation time, run number, and run id decide which run of a platform is newest,
    never which row was appended to the ledger last (LANE-EVIDENCE).
    """
    newest = {}
    for row in platform_runs(root):
        platform = row["platform"].strip().lower()
        if platform not in newest or _platform_run_order(row) > _platform_run_order(newest[platform]):
            newest[platform] = row
    return [platform_run_reading(root, cfg, newest[platform]) for platform in sorted(newest)]


def evidence_problems(cfg):
    """What `ao doctor --check` acts on about platform evidence, as (key, text); [] when nothing was recorded.

    Absence is not a finding: a project that never recorded a platform run is not told its
    evidence is red. A row that was recorded and no longer covers the tree is, and the newest
    row of each platform decides alone - a green rerun clears what the run before it said.
    """
    from .storage import _unattested_repair, unattested_in_tree
    root = cfg["root"]
    try:
        foreign, attested = unattested_in_tree(root, PLATFORM_RUNS_FILE, PLATFORM_RUNS_CHAIN)
        rows = platform_run_summaries(root, cfg)
    except Exception as exc:
        problem = " ".join(str(exc).split())
        return [("evidence-ledger",
                 f"{PLATFORM_RUNS_FILE} cannot be read ({problem}); fix it as docs/telemetry.md shows, or "
                 "record a new run once it is gone")]
    bound = settings.get(cfg, "evidence.max_commits_behind")
    out = []
    if foreign:
        out.append(("evidence-unattested",
                    f"{PLATFORM_RUNS_FILE} holds {_unattested_repair(foreign, attested)}"))
    for row in rows:
        platform, where = row["platform"], f"run {row.get('run') or '?'}"
        commit = row.get("commit") or ""
        if not row["on_repository"]:
            repo = row.get("repository") or "another repository"
            out.append((f"evidence-red:{platform}",
                        f"{platform}'s newest recorded {where} is from {repo}, not this repository — "
                        f"run the {platform} lane on this repository and record it"))
        elif not row["finished"]:
            status = row.get("status") or "unfinished"
            out.append((f"evidence-red:{platform}",
                        f"{platform}'s newest recorded {where} is not finished ({status}) at "
                        f"{commit[:10] or 'no commit'} — wait for the {platform} lane to finish and record it"))
        elif not row["succeeded"]:
            if row.get("jobs_total") == 0:
                why = f"ran no {platform} job"
            elif row.get("jobs_failed"):
                why = f"failed {row['jobs_failed']} job(s) ({row.get('conclusion') or 'failure'})"
            else:
                why = f"ended {row.get('conclusion') or 'unsuccessfully'}"
            out.append((f"evidence-red:{platform}",
                        f"{platform}'s newest recorded {where} {why} at "
                        f"{commit[:10] or 'no commit'} — rerun the {platform} lane and record it when it passes"))
        elif not row["known"]:
            what = ("names no commit" if not commit
                    else f"names commit {commit[:10]}, which this repository cannot resolve")
            out.append((f"evidence-unresolved:{platform}",
                        f"{platform}'s newest recorded {where} {what}, so nothing says which tree it ran — "
                        f"rerun the {platform} lane on HEAD and record it"))
        elif row["behind"] is None:
            out.append((f"evidence-unresolved:{platform}",
                        f"{platform}'s newest recorded {where} is at {commit[:10]}, which is not an ancestor of "
                        f"HEAD — rerun the {platform} lane on HEAD and record it"))
        elif row["stale"]:
            out.append((f"evidence-stale:{platform}",
                        f"{platform}'s newest recorded {where} is {row['behind']} commit(s) behind HEAD "
                        f"(evidence.max_commits_behind {bound}) — rerun the {platform} lane and record it"))
    return out
