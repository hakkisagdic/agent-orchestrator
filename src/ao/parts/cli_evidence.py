"""Evidence: a platform lane's run recorded from CI, and what the recorded runs still vouch for (LANE-EVIDENCE).

A part of src/ao/cli.py, run in its namespace by `_part` as the parts split out of it are; it is
not importable on its own.

`ao gain` reads `.ao/ledger/platform-runs.jsonl` and nothing wrote it. `ao evidence record`
reads a lane's CI run through `gh` and writes one row from what the run reports, and `ao
evidence show` reads the newest row of each platform back with how far HEAD has moved since -
or every row. The same readings feed `ao doctor --check`, which names a newest run that is not
on this repository, not finished, not successful, not on a commit HEAD holds as an ancestor, or
past `evidence.max_commits_behind`.
"""
import json
import time


def _evidence_when(at):
    """When a row was recorded, as a person reads a date."""
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return "at an unknown time"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at))


def _evidence_state(row):
    """The word and the tone a row is shown with: red, stale or green."""
    if not row["green"]:
        return C["red"], "red"
    if row["stale"]:
        return C["yellow"], "stale"
    return C["green"], "green"


def _evidence_where(row):
    """What the row's run and commit still say about HEAD, in the words a person reads."""
    commit = str(row.get("commit") or "")
    if not row.get("on_repository", True):
        repo = row.get("repository") or "another repository"
        return f"on {repo}, not this repository"
    if not row.get("finished", True):
        return f"{row.get('status') or 'unfinished'} at {commit[:10] or 'no commit'}"
    if row["known"] and row["behind"] is not None:
        return f"at {commit[:10]}, {row['behind']} commit(s) behind HEAD"
    if row["known"]:
        return f"at {commit[:10]}, not an ancestor of HEAD"
    if commit:
        return f"at {commit[:10]}, which this repository cannot place"
    return "with no commit recorded"


def _evidence_counts(row):
    """How a row's jobs and conclusion read on one line."""
    conclusion = row.get("conclusion") or row.get("status") or "unknown"
    if row.get("jobs_total") is not None:
        return f"{row.get('jobs_passed') or 0}/{row['jobs_total']} job(s) passed ({conclusion})"
    return str(conclusion)


def cmd_evidence(cfg, args):
    """Record a platform lane's CI run as evidence, and say what the recorded runs still vouch for (LANE-EVIDENCE).

    `record` reads the CI run through `gh` and appends one row to the chained
    `.ao/ledger/platform-runs.jsonl`: the platform, the run's id, its status and conclusion,
    its head commit, its repository, and its jobs. `show` reads the newest row of
    each platform with how far HEAD has moved since, or every row with `--all`; a run that is
    not on this repository, not finished, not successful, or not on a commit HEAD holds as an
    ancestor never reads as green and vouches for nothing. `ao gain` shows the same rows, and
    `ao doctor --check` names a newest run that is red, unplaceable, or past
    `evidence.max_commits_behind`.
    """
    if getattr(args, "action", None) == "record":
        return _evidence_record(cfg, args)
    return _evidence_show(cfg, args)


def _evidence_record(cfg, args):
    missing = [flag for flag, value in (("--platform", args.platform), ("--run", args.run))
               if value is None or not str(value).strip()]
    if missing:
        print(f"{C['red']}nothing recorded{C['reset']}: record needs {', '.join(missing)}")
        print(f"{C['dim']}ao evidence record --platform windows --run <id>{C['reset']}")
        return 2
    gh = A.runnable_binary("gh")
    if not gh:
        print(f"{C['red']}nothing recorded{C['reset']}: gh is not on this machine (PATH, binaries.extra_dirs, "
              "the usual install directories): ao reads a lane's run only through GitHub's CLI. Install it and "
              "run `gh auth login`")
        return 1
    root = cfg["root"]
    try:
        row = A.record_platform_run(root, platform=args.platform, run=args.run, gh=gh)
    except (TypeError, ValueError) as exc:
        print(f"{C['red']}nothing recorded{C['reset']}: {exc}")
        return 2
    except A.GhUnreadable as exc:
        print(f"{C['red']}nothing recorded{C['reset']}: {exc}")
        return 1
    except Exception as exc:
        print(f"{C['red']}nothing recorded{C['reset']}: the ledger could not be written "
              f"({type(exc).__name__}: {exc})")
        return 1
    reading = A.platform_run_reading(root, cfg, row)
    _, state = _evidence_state(reading)
    commit = (row.get("commit") or "")[:10] or "no commit"
    print(f"recorded {row['platform']} run {row['run']} ({state}): {_evidence_counts(row)} "
          f"at {commit} ({row['suite']})")
    return 0


def _evidence_show(cfg, args):
    from .storage import _unattested_repair, unattested_in_tree
    root = cfg["root"]
    try:
        foreign, attested = unattested_in_tree(root, A.PLATFORM_RUNS_FILE, A.PLATFORM_RUNS_CHAIN)
        if getattr(args, "all", False):
            rows = [A.platform_run_reading(root, cfg, row) for row in A.platform_runs(root)]
        else:
            rows = A.platform_run_summaries(root, cfg)
    except Exception as exc:
        print(f"{C['red']}{A.PLATFORM_RUNS_FILE} cannot be read{C['reset']}: {type(exc).__name__}: {exc}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{C['b']}ao evidence{C['reset']}  {C['dim']}{A.PLATFORM_RUNS_FILE}{C['reset']}")
    if foreign:
        print(f"  {C['yellow']}unattested{C['reset']} {A.PLATFORM_RUNS_FILE} holds "
              f"{_unattested_repair(foreign, attested)}")
    if not rows:
        print(f"  {C['dim']}no platform run recorded in {A.PLATFORM_RUNS_FILE}{C['reset']}")
        print(f"  {C['dim']}record one after a lane runs in CI: "
              f"ao evidence record --platform windows --run <id>{C['reset']}")
        return 0
    bound = S.get(cfg, "evidence.max_commits_behind")
    print(f"  {C['dim']}{len(rows)} row(s), "
          f"{'oldest first' if getattr(args, 'all', False) else 'the newest of each platform'}; "
          f"evidence.max_commits_behind {bound}{C['reset']}")
    for row in rows:
        tone, state = _evidence_state(row)
        print(f"  {row['platform']:<10} {tone}{state:<8}{C['reset']} run {row.get('run')} · {row.get('suite')} · "
              f"{_evidence_counts(row)} · {_evidence_where(row)} · {_evidence_when(row.get('at'))}")
    return 0
