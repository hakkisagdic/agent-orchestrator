"""What needs doing now, and whose hand does it: the surface (NEXT).

A part of src/ao/cli.py, run in its namespace by `_part` as the parts split out of it are; it is
not importable on its own.

`ao board` answers where each item is, `ao status` whether an agent is alive, `ao gain` what work cost.
None of them answers the question a person asks on coming back to a project, or the one an agent asks
when its turn ends: which command, in what order, by whom. With eighty commands, about fifty of them
needing a project, that question was being answered from memory — which is where a landing chain
stalls: a candidate verified but never reviewed, a review approved but never granted, a stand-in
request left in ~/.ao unread, a route no person has accepted holding every review that would follow it.

The reading is `lib_next`'s, so `ao next`, the `ao_next` MCP tool and a later page answer from one
place. What is here is only how it is shown: to a person in the terminal, as one JSON document for a
panel or a script, and as the one line a menu bar holds. Nothing here runs or records: a person's
command is printed, never typed.
"""


def render_next(cfg, items):
    """The items as a person reads them: one block per actor, the command under the reason."""
    lines = []
    for who, label in ((A.PERSON, f"{C['b']}{C['yellow']}for you{C['reset']}"),
                       ("implementer", f"{C['b']}{C['cyan']}for the implementer{C['reset']}")):
        mine = [item for item in items if item["who"] == who]
        if not mine:
            continue
        lines.append(f"\n{label} {C['dim']}({len(mine)}){C['reset']}")
        for item in mine:
            where = f" {C['dim']}{A.shown_text(item['slice'])}{C['reset']}" if item.get("slice") else ""
            lines.append(f"  {C['b']}{A.shown_text(item['kind'])}{C['reset']}{where}  {A.shown_text(item['why'])}")
            if item.get("command"):
                lines.append(f"      {C['green']}{A.shown_text(item['command'])}{C['reset']}")
    if not items:
        lines.append(f"{C['green']}Nothing is waiting.{C['reset']} What is staged stands granted, and no item "
                     f"holds on a person.")
    return lines


def cmd_next(cfg, args):
    """What needs doing now, and whose hand does it (NEXT).

    `--json` prints the same items as one document, for a panel, a menu-bar item or a script.
    `--short` is the one line a menu bar holds: how many wait for a person and how many for the
    implementer, with the command added when exactly one item stands open.
    """
    items = A.next_items(cfg)
    if getattr(args, "json", False):
        print(json.dumps({"project": cfg.get("project") or A.next_project_key(cfg["root"]),
                          "person": [item for item in items if item["who"] == A.PERSON],
                          "implementer": [item for item in items if item["who"] != A.PERSON]},
                         ensure_ascii=False, indent=2, default=str))
        return 0
    # A menu bar counts actions, so an item names no command is counted for no one: a superseded review
    # request is listed in full by `ao next` and asks nothing of the person reading the bar (NEXT).
    open_items = [item for item in items if item.get("command")]
    counts = {who: sum(1 for item in open_items if item["who"] == who) for who in (A.PERSON, "implementer")}
    if getattr(args, "short", False):
        line = f"{counts[A.PERSON]} for you · {counts['implementer']} for the implementer"
        print(line + (f" · {A.shown_text(open_items[0]['command'])}" if len(open_items) == 1 else ""))
        return 0
    for line in render_next(cfg, items):
        print(line)
    print(f"\n{C['dim']}Read from .ao/, ~/.ao/ and Git's index. Nothing here was run or recorded: a person's "
          f"command is shown, never typed.{C['reset']}")
    return 0
