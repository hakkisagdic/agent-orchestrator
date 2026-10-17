"""Gain: what ao measured, from the ledgers it already keeps (GAIN).

A part of src/ao/cli.py, run in its namespace by `_part` as the parts split out of it are; it is
not importable on its own.

One surface for the questions a person asks between slices: is work landing, what is it costing,
is review converging, what is the watchdog doing, and what did the lanes last prove. Every number
is read from a store ao wrote - the board, the review and slice ledgers, the implementer's own
transcript, the watchdog's logs - and nothing is estimated or inferred from a counter that does
not exist. `ao stats` reads outcomes alone, `ao cost` spend alone, `ao credits` the account
alone; this is the one place that reads them together, with a daily trend.
"""
import os
import time
from datetime import datetime, timedelta

EVIDENCE_FILE = os.path.join(".ao", "ledger", "platform-runs.jsonl")
CLASSES = ("product", "analysis", "ceremony", "coordination")
BOARD_ORDER = ("running", "queued", "blocked", "verified", "inbox", "done", "rejected")


def _gain_number(value):
    """A count a person reads at a glance: 1,204; 42k; 1.82M."""
    if value is None:
        return "—"
    value = float(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 10_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:,.0f}"


def _gain_hours(value):
    return f"{value:g}h" if value is not None else "—"


def _gain_share(part, total):
    return round(100 * part / total) if total else 0


def _gain_since(args):
    """The window the command names, or None for everything recorded."""
    when = A.parse_time(args.since) if getattr(args, "since", None) else None
    return when


def _gain_project(cfg, project, when, days):
    """Everything one project's ledgers say in this window, as values and not as text."""
    root = cfg["root"]
    since = when.moment() if when else None
    out = {"project": project, "root": root, "window": when.label() if when else "whole history"}
    try:
        outcomes = [row for row in A.slice_outcomes(root, project)
                    if since is None or (row["landed_at"] or 0) >= since]
        out["slices"] = A.outcome_stats(outcomes) if outcomes else None
        out["slice_rows"] = outcomes
    except Exception as exc:
        out["slices"], out["slice_rows"] = None, []
        out["slices_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from .storage import read_chained_jsonl
        rows = [row for row in read_chained_jsonl(A.review_ledger_path(root), A.REVIEW_CHAIN)
                if isinstance(row, dict) and (since is None or (row.get("at") or 0) >= since)]
        out["reviews"] = {
            "rows": len(rows),
            "approved": sum(1 for row in rows if row.get("verdict") == "APPROVED"),
            "changes": sum(1 for row in rows if row.get("verdict") == "NEEDS_CHANGES"),
            "other": sum(1 for row in rows if row.get("verdict") not in ("APPROVED", "NEEDS_CHANGES")),
            "retrospective": sum(1 for row in rows if row.get("kind") == "commit-range"),
            "reviewers": len({row.get("reviewer") for row in rows if row.get("reviewer")}),
            "fallback": sum(1 for row in rows if row.get("fallback")),
        }
    except Exception as exc:
        out["reviews"] = None
        out["reviews_error"] = f"{type(exc).__name__}: {exc}"
    try:
        out["board"] = {state: len(items) for state, items in A.board(root).items()}
    except Exception:
        out["board"] = {}
    turns = []
    try:
        costs = A.turn_costs(cfg, since=since)
        turns = costs["turns"]
        total = costs["total"]
        delegated = sum(float(row.get("delegated") or 0) for row in costs["by_class"].values())
        out["spend"] = {
            "unit": costs["unit"], "total": total, "turns": len(turns), "delegated": delegated,
            "classes": {name: {"turns": row["turns"], "usage": row["usage"],
                               "share": _gain_share(row["usage"], total),
                               "wasted": row.get("wasted", 0)}
                        for name, row in costs["by_class"].items()},
            "overhead_share": _gain_share(sum(costs["by_class"].get(name, {}).get("usage", 0)
                                              for name in ("ceremony", "coordination")), total),
            "wasted": sum(row.get("wasted", 0) for row in costs["by_class"].values()),
            "tool_calls": sum(row.get("tool_calls") or 0 for row in costs["turns"]),
            "ao_commands": dict(costs["ao_commands"].most_common(6)),
        }
    except Exception as exc:
        out["spend"] = None
        out["spend_error"] = f"{type(exc).__name__}: {exc}"
    try:
        hunts = sum(1 for row in A.hunter_rows(root) if row.get("event") == "run"
                    and (since is None or (row.get("at") or 0) >= since))
    except Exception:
        hunts = 0
    try:
        notices = [row for row in A.notices(root, limit=10 ** 9, include_suppressed=True)
                   if since is None or (row.get("at") or 0) >= since]
    except Exception:
        notices = []
    out["activity"] = {
        "wakes": len(A.spawn_times(root, "architect_wake", since)),
        "refills": len(A.spawn_times(root, "refill", since)),
        "nudges": len(A.spawn_times(root, "nudge", since)),
        "hunts": hunts,
        "notices": len(notices),
        "notices_sent": sum(1 for row in notices if row.get("sent")),
    }
    out["evidence"] = _gain_evidence(root)
    out["daily"] = _gain_daily(out, days, turns)
    return out


def _gain_evidence(root):
    """The newest recorded run of each platform, and how far HEAD has moved since its commit."""
    path = os.path.join(root, EVIDENCE_FILE)
    newest = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("platform"), str) and row["platform"]:
                    if row["platform"] not in newest or (row.get("at") or 0) > (newest[row["platform"]].get("at") or 0):
                        newest[row["platform"]] = row
    except OSError:
        return []
    out = []
    for platform in sorted(newest):
        row = newest[platform]
        behind = None
        commit = str(row.get("commit") or "")
        if commit:
            try:
                counted = A._git_text(root, "rev-list", "--count", f"{commit}..HEAD").strip()
                behind = int(counted) if counted.isdigit() else None
            except Exception:
                behind = None
        out.append({"platform": platform, "commit": commit, "run": row.get("run"),
                    "at": row.get("at"), "passed": row.get("passed"), "failed": row.get("failed"),
                    "skipped": row.get("skipped"), "suite": row.get("suite"), "behind": behind,
                    "green": row.get("failed") == 0})
    return out


def _gain_daily(project, days, turns):
    """Spend and landings per day, for the last `days` days that end today."""
    days = max(1, int(days or 14))
    last = datetime.now().date()
    first = last - timedelta(days=days - 1)
    spend, landed = {}, {}
    # The day is the turn's own: a long session that crossed midnight is charged where each turn started.
    for row in turns:
        if row.get("start"):
            day = datetime.fromtimestamp(row["start"]).date()
            spend[day] = spend.get(day, 0.0) + float(row.get("usage") or 0)
    for row in project.get("slice_rows") or []:
        if row.get("landed_at"):
            day = datetime.fromtimestamp(row["landed_at"]).date()
            landed[day] = landed.get(day, 0) + 1
    return [{"date": (first + timedelta(days=offset)).isoformat(),
             "spend": spend.get(first + timedelta(days=offset), 0.0),
             "landed": landed.get(first + timedelta(days=offset), 0)}
            for offset in range(days)]


def _gain_quota(cfg):
    """The implementer's own account, when its adapter declares a lookup ao can run."""
    ident = A.implementer_adapter_id(cfg)
    api = A.usage_api(ident)
    if not ident:
        return {"adapter": "", "readable": False, "why": "no implementer adapter is configured"}
    if not api:
        return {"adapter": ident, "readable": False,
                "why": "its adapter declares no account lookup ao can run"}
    try:
        account = A.account_usage(adapter_id=ident)
    except Exception as exc:
        return {"adapter": ident, "readable": True, "error": f"{type(exc).__name__}: {exc}"}
    if not account:
        return {"adapter": ident, "readable": True, "error": "the lookup returned nothing"}
    if account.get("error") or account.get("expired"):
        return {"adapter": ident, "readable": True,
                "error": account.get("error") or "the account's token expired"}
    used, limit = float(account.get("used") or 0), float(account.get("limit") or 0)
    return {"adapter": ident, "readable": True, "used": used, "limit": limit,
            "percent": round(100 * used / limit, 1) if limit else None,
            "plan": account.get("plan"), "reset_at": account.get("reset_at")}


def _gain_totals(projects):
    """The same headline over every project read: slices, spend and turns."""
    slices = sum((row.get("slices") or {}).get("slices", 0) for row in projects)
    spend = sum((row.get("spend") or {}).get("total", 0) for row in projects)
    turns = sum((row.get("spend") or {}).get("turns", 0) for row in projects)
    return {"slices": slices, "spend": spend, "turns": turns}


def cmd_gain(cfg, args):
    """What ao measured here: slices, spend, reviews, activity, evidence and the daily trend (GAIN).

    Read from the stores ao writes as it works; nothing is estimated, and a store that cannot be
    read is named rather than shown as zero. `--all` reads every registered project. `--quota`
    asks the implementer's account (one request) only when it is named.
    """
    when = _gain_since(args)
    if getattr(args, "all", False):
        roots = A.recall_roots(cfg["root"])
    else:
        roots = [(cfg.get("project") or A.project_key(cfg["root"]), cfg["root"])]
    projects = []
    for project, root in roots:
        if os.path.realpath(root) == os.path.realpath(cfg["root"]):
            grown = dict(cfg, root=root)
        else:
            try:
                grown = dict(A.load_config(root), root=root)
            except Exception as exc:
                projects.append({"project": project, "root": root, "window": when.label() if when else
                                 "whole history", "error": f"{type(exc).__name__}: {exc}"})
                continue
        try:
            row = _gain_project(grown, project, when, getattr(args, "days", 14))
        except Exception as exc:
            row = {"project": project, "root": root, "window": when.label() if when else "whole history",
                   "error": f"{type(exc).__name__}: {exc}"}
        if getattr(args, "quota", False) and "error" not in row:
            row["quota"] = _gain_quota(grown)
        projects.append(row)
    if getattr(args, "json", False):
        payload = {"projects": projects} if getattr(args, "all", False) else projects[0]
        if getattr(args, "all", False) and len(projects) > 1:
            payload["total"] = _gain_totals(projects)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for number, project in enumerate(projects):
        if number:
            print()
        _gain_show(project, args)
    if getattr(args, "all", False) and len(projects) > 1:
        total = _gain_totals(projects)
        print(f"\n{b}total{reset}      {total['slices']} slice(s) · {_gain_number(total['spend'])} spent in "
              f"{total['turns']} turn(s)")
    return 0


def _gain_show(project, args):
    """One project's block, in the order a person reads it: landed, reviewed, queued, spent, done."""
    b, dim, reset, yellow, red = C["b"], C["dim"], C["reset"], C["yellow"], C["red"]
    print(f"{b}ao gain · {project['project']}{reset}  {dim}{project.get('window')}{reset}")
    if project.get("error"):
        print(f"  {red}cannot read this project{reset}: {project['error']}")
        return
    stats = project.get("slices")
    if stats:
        parts = [f"{stats['slices']} landed", f"{stats['waived']} waived" if stats["waived"] else None,
                 f"{stats['product_lines']['median']} product lines median"
                 if stats.get("product_lines") else None]
        print(f"  {'slices':<10} " + " · ".join(part for part in parts if part))
        detail = []
        if stats.get("hours"):
            detail.append(f"ready→landed median {_gain_hours(stats['hours']['median'])}, "
                          f"p90 {_gain_hours(stats['hours']['p90'])}")
        if stats.get("rounds"):
            detail.append(f"review rounds median {stats['rounds']['median']}")
        if stats.get("first_pass_pct") is not None:
            detail.append(f"{stats['first_pass_pct']}% first pass")
        if stats.get("defects_pct") is not None:
            detail.append(f"{stats['defects_pct']}% defect found later")
        if detail:
            print(f"  {'':<10} " + " · ".join(detail))
        for tier, count in (stats.get("tiers") or {}).items():
            if tier:
                print(f"  {'':<10} {yellow}{count} on {tier}{reset}")
    else:
        why = project.get("slices_error")
        print(f"  {'slices':<10} {dim}{'cannot read the ledgers: ' + why if why else 'no landed slice in this window'}{reset}")
    reviews = project.get("reviews")
    if reviews is None:
        print(f"  {'reviews':<10} {dim}cannot read the review ledger: {project.get('reviews_error')}{reset}")
    elif reviews["rows"]:
        print(f"  {'reviews':<10} {reviews['rows']} recorded · {reviews['approved']} approved · "
              f"{reviews['changes']} changes · {reviews['other']} other · {reviews['reviewers']} reviewer(s)"
              + (f" · {reviews['fallback']} fallback" if reviews["fallback"] else "")
              + (f" · {reviews['retrospective']} retrospective" if reviews["retrospective"] else ""))
    else:
        print(f"  {'reviews':<10} {dim}no review recorded in this window{reset}")
    board = project.get("board") or {}
    if board:
        print(f"  {'board':<10} " + " · ".join(f"{state} {board.get(state, 0)}" for state in BOARD_ORDER
                                               if state in board))
    spend = project.get("spend")
    if spend is None:
        print(f"  {'spend':<10} {dim}cannot read the transcript: {project.get('spend_error')}{reset}")
    elif spend["turns"]:
        unit = spend["unit"] if spend["unit"] not in ("", "unit") else "units"
        print(f"  {'spend':<10} {_gain_number(spend['total'])} {unit} in {spend['turns']} turns · "
              f"{_gain_number(spend['tool_calls'])} tool calls · {spend['wasted']} wasted turns")
        shares = [f"{name} {row['share']}%" for name, row in spend["classes"].items() if row["usage"]]
        if shares:
            print(f"  {'':<10} " + " · ".join(shares))
        print(f"  {'':<10} {spend['overhead_share']}% of spend in ceremony + coordination"
              + (f" · {_gain_number(spend['delegated'])} delegated" if spend["delegated"] else ""))
        if spend["ao_commands"]:
            print(f"  {'':<10} {dim}ao: " + ", ".join(f"{name} {count}"
                                                     for name, count in spend["ao_commands"].items()) + reset)
    else:
        print(f"  {'spend':<10} {dim}no implementer turn in this window{reset}")
    activity = project.get("activity") or {}
    print(f"  {'activity':<10} {activity.get('wakes', 0)} wakes · {activity.get('refills', 0)} refills · "
          f"{activity.get('nudges', 0)} nudges · {activity.get('hunts', 0)} hunts · "
          f"{activity.get('notices', 0)} notices ({activity.get('notices_sent', 0)} sent)")
    evidence = project.get("evidence") or []
    if evidence:
        said = []
        for row in evidence:
            state = "green" if row["green"] else f"{red}red{reset}"
            where = f" at {row['commit'][:10]}" if row.get("commit") else ""
            behind = f", {row['behind']} commit(s) behind HEAD" if row.get("behind") is not None else ""
            said.append(f"{row['platform']} {state}{where}{behind}")
        print(f"  {'evidence':<10} " + " · ".join(said))
    else:
        print(f"  {'evidence':<10} {dim}no platform run recorded ({EVIDENCE_FILE}){reset}")
    quota = project.get("quota")
    if quota is not None:
        if not quota.get("readable"):
            print(f"  {'quota':<10} {dim}{quota.get('adapter') or 'no adapter'}: {quota.get('why')}{reset}")
        elif quota.get("error"):
            print(f"  {'quota':<10} {yellow}{quota.get('adapter')}: {quota['error']}{reset}")
        else:
            tone = red if (quota.get("percent") or 0) > 90 else yellow if (quota.get("percent") or 0) > 70 else ""
            reset_at = time.strftime("%d %b", time.localtime(quota["reset_at"])) if quota.get("reset_at") else "?"
            print(f"  {'quota':<10} {tone}{quota.get('plan') or quota.get('adapter')} "
                  f"{quota.get('percent')}% used{reset} · {_gain_number(quota['limit'] - quota['used'])} remaining"
                  f" · resets {reset_at}")
    if getattr(args, "slices", False) and project.get("slice_rows"):
        print(f"  {dim}slices{reset}")
        for row in project["slice_rows"]:
            marks = " → ".join(row.get("verdicts") or []) or "waived"
            facts = [f"{row['rounds']} round(s)" if row.get("rounds") else None,
                     _gain_hours(row.get("hours")) if row.get("hours") is not None else None,
                     f"{row['size']['kinds']['product']['added'] + row['size']['kinds']['product']['deleted']} "
                     "product lines" if isinstance(row.get("size"), dict) else None,
                     "defect found later" if row.get("defect_found") else None,
                     f"tier {row['tier']}" if row.get("tier") else None]
            print(f"    {row['slice']:<16} {marks}  {dim}{' · '.join(fact for fact in facts if fact)}{reset}")
    if getattr(args, "sources", False):
        spent = _gain_source_of(project)
        print(f"  {'sources':<10} {dim}.ao/board.md · .ao/ledger/reviews.jsonl · .ao/ledger/slices.jsonl · "
              f"{EVIDENCE_FILE} · {spent}{reset}")
    daily = project.get("daily") or []
    if any(row["spend"] or row["landed"] for row in daily):
        print(f"\n  {dim}last {len(daily)} days{'':<12}spend        landed{reset}")
        peak = max(row["spend"] for row in daily) or 1
        for row in daily:
            width = round(18 * row["spend"] / peak) if row["spend"] else 0
            bar = "█" * min(width, 18) + ("" if not row["spend"] else "▏")
            print(f"  {row['date'][5:]:<6} {bar:<19} {_gain_number(row['spend']):>8}  {row['landed']:>6}")
    else:
        print(f"\n  {dim}no spend or landing in the last {len(daily)} days{reset}")


def _gain_source_of(project):
    """The transcript a project's spend was read from, named as the adapter resolved it."""
    try:
        cfg = dict(A.load_config(project["root"]), root=project["root"])
        path, _ = A.session_paths(cfg)
    except Exception:
        path = None
    return f"transcript: {path}" if path else "no transcript resolved"
