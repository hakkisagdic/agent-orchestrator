"""Lanes: `ao lane start`, `ao lane list` and `ao lane remove` (LANE-START).

A part of src/ao/cli.py, run in its namespace by `_part` as the parts split out of it are; it is
not importable on its own.
"""


def cmd_lane(cfg, args):
    """Start, list or remove this checkout's lanes: one git worktree per board item (LANE-START).

    Two writers in one checkout make interleaved diffs nobody can review, and a worktree
    added by hand is prepared by hand, or not at all. A lane is made only for a READY item,
    on a branch of its own beside the main checkout, and is reported ready only once the
    settings have prepared it. A refusal exits 2 and changes nothing, a lane setting ao cannot
    use among its reasons; a lane git could not make, or the settings could not prepare, exits
    1; a start stopped by Ctrl-C, SIGTERM or SIGHUP stops what it ran and exits 130.
    """
    action = getattr(args, "action", None) or "list"
    item = getattr(args, "item", None)
    if action == "list":
        return _lane_list(cfg)
    if not item:
        print(f"{C['red']}refused{C['reset']}: ao lane {action} names the board item: ao lane {action} <item>")
        return 2
    try:
        return _lane_start(cfg, item) if action == "start" else _lane_remove(cfg, item)
    except A.LaneRefused as exc:
        print(f"{C['red']}refused{C['reset']}: {exc}")
        return 2


def _lane_start(cfg, item):
    root = cfg["root"]
    try:
        lane = A.start_lane(root, cfg, item)
    except (OSError, RuntimeError) as exc:
        print(f"{C['red']}lane {item} not started{C['reset']}: {exc}")
        return 1
    except KeyboardInterrupt:
        # Stopped by a signal: say what the record now says, if the terminal is still there to read it.
        lane = A.lane_record(root, item) or {}
        try:
            if lane.get("problem"):
                print(f"{C['red']}lane {item} not ready{C['reset']}: {lane['problem']}; "
                      f"`ao lane remove {item}` retires it")
        except OSError:
            pass
        raise
    where = f"{lane['path']} on {lane['branch']} from {lane['base'][:10]}"
    if lane["state"] == "ready":
        print(f"{C['green']}lane {item} ready{C['reset']}: {where}")
        if lane["steps"]:
            print(f"  {C['dim']}{' · '.join(lane['steps'])}{C['reset']}")
        return 0
    print(f"{C['red']}lane {item} not ready{C['reset']}: {lane['problem']}")
    if lane["steps"]:
        print(f"  {C['dim']}done before it: {' · '.join(lane['steps'])}{C['reset']}")
    try:
        with open(A.lane_log_path(root, lane["name"]), encoding=UTF8, errors="replace") as fh:
            tail = _last_lines(fh.read()[-4000:])
    except OSError:
        tail = ""
    if tail:
        print(tail)
    print(f"  the worktree stays at {lane['path']} for inspection; `ao lane remove {item}` retires it")
    return 1


def _lane_list(cfg):
    rows = A.lane_rows(cfg["root"])
    if not rows:
        print(f"{C['dim']}no lanes; `ao lane start <item>` starts one for a READY board item{C['reset']}")
        return 0
    tones = {"ready": C["green"], "preparing": C["yellow"]}
    for row in rows:
        facts = []
        if not row["exists"]:
            facts.append("worktree gone")
        elif row["changes"] is None:
            facts.append("its git status cannot be read")
        elif row["changes"]:
            facts.append(f"{len(row['changes'])} uncommitted change(s)")
        facts.append(f"board: {row['board'] or 'not on the board'}")
        if row.get("held"):
            facts.append(row["held"])
        if row.get("problem"):
            facts.append(row["problem"])
        print(f"  {C['b']}{row.get('item') or row['name']}{C['reset']}  "
              f"{tones.get(row['state'], C['red'])}{row['state']}{C['reset']}  "
              f"{C['dim']}{row.get('branch') or '-'}{C['reset']}  {row.get('path') or '-'}  "
              f"{C['dim']}{' · '.join(facts)}{C['reset']}")
    return 0


def _lane_remove(cfg, item):
    try:
        steps = A.remove_lane(cfg["root"], item)
    except (OSError, RuntimeError) as exc:
        print(f"{C['red']}stopped{C['reset']}: {exc}; `ao lane list` shows what is left")
        return 1
    for step in steps:
        print(f"  done: {step}")
    print(f"{C['green']}lane {item} removed{C['reset']}")
    return 0
