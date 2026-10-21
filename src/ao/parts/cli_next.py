"""What needs doing now, and whose hand does it: the surface (NEXT, SURFACES-SWIFTBAR).

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
panel or a script, and in the plugin output format SwiftBar and xbar read in the menu bar. Nothing here
runs or records: a person's command is printed, never typed.
"""

SWIFTBAR_PLUGIN = "ao.30s.sh"
SWIFTBAR_MARKER = "# agent-orchestrator: ao-surface-v1 surface=swiftbar"


def _swiftbar_text(text):
    """One line of text for SwiftBar or xbar, with control characters escaped and `|` disarmed.

    SwiftBar (github.com/swiftbar/SwiftBar) and xbar split each line at `|` and read the rest as item
    parameters (`bash=`, `shell=`, `href=`, `terminal=`); escaping `|` keeps every line plain text with
    no action attached (SURFACES-SWIFTBAR).
    """
    return A.shown_text(" ".join(str(text).split())).replace("|", r"\x7c")


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


def render_next_short(cfg, items):
    """The menu-bar line and dropdown lines in the format SwiftBar and xbar read (SURFACES-SWIFTBAR).

    The first line is what sits in the menu bar: how many actionable items wait for a person and how
    many for the implementer, with the command added when a single item stands open. `---` begins the
    dropdown, where each actor's items follow as plain text and a person's step appears as the command
    text only.
    """
    # A menu bar counts actions, so an item that names no command is counted for no one: a superseded review
    # request is listed in full in the dropdown and asks nothing of the person reading the bar (NEXT).
    open_items = [item for item in items if item.get("command")]
    counts = {who: sum(1 for item in open_items if item["who"] == who) for who in (A.PERSON, "implementer")}
    head = f"{counts[A.PERSON]} for you · {counts['implementer']} for the implementer"
    if len(open_items) == 1:
        head += f" · {_swiftbar_text(open_items[0]['command'])}"
    lines = [head, "---"]
    for who, label in ((A.PERSON, "for you"), ("implementer", "for the implementer")):
        mine = [item for item in items if item["who"] == who]
        if not mine:
            continue
        lines.append(f"{label} ({len(mine)})")
        for item in mine:
            where = f" {_swiftbar_text(item['slice'])}" if item.get("slice") else ""
            lines.append(f"  {_swiftbar_text(item['kind'])}{where}  {_swiftbar_text(item['why'])}")
            if item.get("command"):
                lines.append(f"      {_swiftbar_text(item['command'])}")
    if not items:
        lines.append("Nothing is waiting. What is staged stands granted, and no item holds on a person.")
    return lines


def cmd_next(cfg, args):
    """What needs doing now, and whose hand does it (NEXT, SURFACES-SWIFTBAR).

    `--json` prints the same items as one document, for a panel, a menu-bar item or a script.
    `--short` prints the menu-bar line and `---` dropdown lines in the format SwiftBar and xbar read.
    """
    items = A.next_items(cfg)
    if getattr(args, "json", False):
        print(json.dumps({"project": cfg.get("project") or A.next_project_key(cfg["root"]),
                          "person": [item for item in items if item["who"] == A.PERSON],
                          "implementer": [item for item in items if item["who"] != A.PERSON]},
                         ensure_ascii=False, indent=2, default=str))
        return 0
    if getattr(args, "short", False):
        for line in render_next_short(cfg, items):
            print(line)
        return 0
    for line in render_next(cfg, items):
        print(line)
    print(f"\n{C['dim']}Read from .ao/, ~/.ao/ and Git's index. Nothing here was run or recorded: a person's "
          f"command is shown, never typed.{C['reset']}")
    return 0


def _render_swiftbar_plugin(root, fallback=None):
    """The SwiftBar and xbar plugin script for `root`, running `ao next --short` alone (SURFACES-SWIFTBAR)."""
    import shlex
    return (
        "#!/bin/sh\n"
        f"{SWIFTBAR_MARKER}\n"
        f"root={shlex.quote(root)}\n"
        + _ao_lookup(fallback)
        + 'exec "$ao" -C "$root" next --short\n'
    )


def _is_ao_swiftbar_plugin(data):
    """Whether `data` is the exact bytes of a SwiftBar plugin ao wrote (SURFACES-SWIFTBAR).

    A marker comment alone is not enough: a person's script or an edited plugin is foreign, and
    neither install nor uninstall may write over or remove it.
    """
    import shlex
    if not isinstance(data, (bytes, bytearray)) or b"\x00" in data:
        return False
    try:
        text = bytes(data).decode(UTF8)
    except UnicodeError:
        return False
    lines = text.split("\n")
    if len(lines) < 4 or lines[0] != "#!/bin/sh" or lines[1] != SWIFTBAR_MARKER or not lines[2].startswith("root="):
        return False
    try:
        words = shlex.split(lines[2])
    except ValueError:
        return False
    if len(words) != 1 or not words[0].startswith("root="):
        return False
    root = words[0][len("root="):]
    if not root or any(ch in root for ch in "\n\r\0"):
        return False
    return text == _render_swiftbar_plugin(root, _hook_fallback_of(bytes(data)))


def _classify_swiftbar_plugin(path):
    """What stands at `path` without following a link: absent, owned or foreign (SURFACES-SWIFTBAR)."""
    import stat
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "foreign"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
        return "foreign"
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return "foreign"
    return "owned" if _is_ao_swiftbar_plugin(data) else "foreign"


def cmd_surfaces(cfg, args):
    """Install or remove the SwiftBar / xbar menu-bar plugin in the folder the person names (SURFACES-SWIFTBAR).

    `ao` never guesses a plugin folder. Install writes `ao.30s.sh` only when the path is free or already
    holds ao's own plugin bytes; uninstall removes only ao's own plugin file and leaves any other file
    in the folder untouched.
    """
    import tempfile
    raw_dir = getattr(args, "dir", None)
    if not raw_dir or not str(raw_dir).strip():
        print(f"{C['red']}surfaces {args.action} refused{C['reset']}: --dir needs the plugin folder; ao never "
              "guesses one")
        return 1
    directory = os.path.abspath(os.path.expanduser(str(raw_dir).strip()))
    if os.path.islink(directory):
        print(f"{C['red']}surfaces {args.action} refused{C['reset']}: {A.shown_text(directory)} is a symbolic link")
        return 1
    if os.path.lexists(directory) and not os.path.isdir(directory):
        print(f"{C['red']}surfaces {args.action} refused{C['reset']}: {A.shown_text(directory)} is not a directory")
        return 1
    path = os.path.join(directory, SWIFTBAR_PLUGIN)
    if args.action == "uninstall":
        state = _classify_swiftbar_plugin(path)
        if state == "absent":
            print(f"no ao swiftbar plugin in {A.shown_text(directory)}")
            return 0
        if state != "owned":
            print(f"{C['red']}surfaces uninstall refused{C['reset']}: {A.shown_text(path)} is not ao's plugin file "
                  "and was left untouched")
            return 1
        before = _file_identity(path)
        if before is None or _classify_swiftbar_plugin(path) != "owned" or _file_identity(path) != before:
            print(f"{C['red']}surfaces uninstall refused{C['reset']}: {A.shown_text(path)} changed while it was "
                  "checked and was left untouched")
            return 1
        os.remove(path)
        print(f"removed swiftbar — {A.shown_text(path)}")
        return 0

    real_root = os.path.realpath(cfg["root"])
    if any(ch in real_root for ch in "\n\r\0"):
        print(f"{C['red']}surfaces install refused{C['reset']}: project root path cannot be written on one shell line")
        return 1
    os.makedirs(directory, exist_ok=True)
    state = _classify_swiftbar_plugin(path)
    if state == "foreign":
        print(f"{C['red']}surfaces install refused{C['reset']}: {A.shown_text(path)} is not ao's plugin file "
              "and was left untouched")
        return 1
    data = _render_swiftbar_plugin(real_root, _hook_fallback()).encode(UTF8)
    fd, temporary = tempfile.mkstemp(prefix=".ao-surface-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), 0o755)
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, 0o755)
        before = _file_identity(path)
        now = _classify_swiftbar_plugin(path)
        if now == "owned":
            if _file_identity(path) != before:
                print(f"{C['red']}surfaces install refused{C['reset']}: {A.shown_text(path)} changed while install "
                      "read it and was left untouched")
                return 1
            os.replace(temporary, path)
        elif now == "absent":
            _hook_put_on_free_path(temporary, path, data)
        else:
            print(f"{C['red']}surfaces install refused{C['reset']}: {A.shown_text(path)} is not ao's plugin file "
                  "and was left untouched")
            return 1
    finally:
        try:
            if os.path.lexists(temporary):
                os.remove(temporary)
        except OSError:
            pass
    print(f"installed swiftbar — {A.shown_text(path)}")
    return 0
