"""Shared library for the agent-orchestrator reference scripts.

Standard library only, by design — see docs/surfaces.md. Nothing here writes to a
vendor's session store; observation is strictly read-only.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

HOME = os.path.expanduser("~")
from . import settings  # noqa: E402  (reads HOME through this module, lazily)
from . import language  # noqa: E402  (what ao writes into a project, in its language; what it reads, in any)
# Adapters ship with the package, but the documented install is still a git
# clone plus an alias — both have to resolve. Look beside this module first, then
# at the repository root, so neither path depends on the other existing.
# A module split into parts keeps one namespace (#44). A part is a contiguous run of a
# module's own definitions, moved out byte for byte and run here, in the module's
# globals: every name stays where callers, tests and monkeypatches look for it, so a
# split moves text and never behaviour. `ao split-check` proves a move is only that.
_PARTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parts")


def _part(name, namespace):
    """Run the part `name` in `namespace` - the globals of the module it was moved out of.

    Compiled through the import system's own loader, so a part's bytecode is cached like
    any module's and an `ao` hook does not recompile thousands of lines on every run.
    """
    from importlib.machinery import SourceFileLoader
    path = os.path.join(_PARTS_DIR, f"{name}.py")
    exec(SourceFileLoader(f"ao.parts.{name}", path).get_code(f"ao.parts.{name}"), namespace)


_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(_HERE))


def adapters_dir():
    for cand in (os.path.join(_HERE, "adapters"), os.path.join(REPO, "adapters")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(_HERE, "adapters")

# ── what a person sees and types: colour, and time arguments (CLI-ROBUST) ─────

def terminal(stream=None):
    """Whether `stream`, stdout unless named, is a terminal that draws escape codes.

    A pipe, a file or a log is none, and neither is a terminal that says it is dumb
    (`TERM=dumb`): what ao writes there is read by a program, or scrolled back through,
    so a code in it is noise.
    """
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool((sys.stdout if stream is None else stream).isatty())
    except (AttributeError, OSError, ValueError):       # no stdout at all, or a closed one
        return False


def colour_enabled(stream=None):
    """Whether ANSI colour may be written: to a terminal, and never while NO_COLOR is set, to any value."""
    return "NO_COLOR" not in os.environ and terminal(stream)


class _Palette(dict):
    """ANSI codes by name, each read as "" wherever colour is off.

    Decided at every lookup rather than once at import: one process prints to a terminal,
    to a pipe a hook reads and to the log a scheduled job keeps, and a test swaps stdout
    under it. Every `C[...]` passes through here, so no command decides colour on its own.
    """

    def __getitem__(self, name):
        return dict.__getitem__(self, name) if colour_enabled() else ""

    def get(self, name, default=None):
        return self[name] if name in self else default


C = _Palette({
    "reset": "\033[0m", "dim": "\033[2m", "b": "\033[1m", "green": "\033[32m",
    "red": "\033[31m", "yellow": "\033[33m", "cyan": "\033[36m",
    "mag": "\033[35m", "blue": "\033[34m",
})

TIME_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}
TIME_FORMS = "30m, 2h, 1d, 1w, today, yesterday or a date such as 2026-09-17"
_TIME_NUMBER = re.compile(r"\d+(?:\.\d+)?|\.\d+")          # 7, 0.5 and .5, as float() read them
_TIME_SPAN = re.compile(rf"({_TIME_NUMBER.pattern})([mhdw])")
_TIME_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?")


class When:
    """A time an `ao` command was given: a span back from now, or the moment a date names.

    `text` is what was typed, so a report names its window the way it was asked for.
    Exactly one of `seconds` (a span) and `at` (an epoch) is set.
    """

    def __init__(self, text, seconds=None, at=None):
        self.text, self.seconds, self.at = text, seconds, at

    def __str__(self):
        return self.text

    def __repr__(self):
        return f"When({self.text!r})"

    def moment(self, ahead=False, now=None):
        """The epoch named: a date's own, or the span back from now, or forward from it with `ahead`."""
        if self.at is not None:
            return self.at
        now = time.time() if now is None else now
        return now + self.seconds if ahead else now - self.seconds

    def span(self, unit, now=None):
        """How far back from now this reaches, counted in `unit`, a key of TIME_UNITS."""
        seconds = self.seconds if self.at is None else (time.time() if now is None else now) - self.at
        return seconds / TIME_UNITS[unit]

    def label(self):
        """How a report names the window: `last 24h` for a span, `since today` for a date."""
        return f"last {self.text}" if self.at is None else f"since {self.text}"


def parse_time(text, bare=None, now=None):
    """What a time argument names, as a When; ValueError saying which forms it takes.

    Every command reads one syntax, where there were nine: `30m`, `2h`, `1d` and `1w` are
    spans back from now; `today` and `yesterday` the local midnight that began them;
    `2026-09-17` or `2026-09-17T14:30` a local moment unless it carries `Z` or an offset.
    A bare number counts in `bare`, the unit its option always took (`--days 7`,
    `--window 24`), and is refused where the option never took one: `7` alone says
    neither minutes nor days.
    """
    if isinstance(text, When):
        return text
    word = str("" if text is None else text).strip()
    span = _TIME_SPAN.fullmatch(word.lower())
    if span:
        return When(word, seconds=float(span.group(1)) * TIME_UNITS[span.group(2)])
    if _TIME_NUMBER.fullmatch(word):
        if bare:
            return When(word, seconds=float(word) * TIME_UNITS[bare])
        raise ValueError(f"{word} needs a unit: {word}m, {word}h or {word}d")
    if word.lower() in ("today", "yesterday"):
        # Calendar days, not 24 hours: across a clock change yesterday's midnight is 23 or 25 hours back.
        day = datetime.fromtimestamp(time.time() if now is None else now).toordinal()
        return When(word, at=datetime.fromordinal(day - (word.lower() == "yesterday")).timestamp())
    if _TIME_DATE.fullmatch(word):
        try:
            return When(word, at=datetime.fromisoformat(word.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass                                      # 2026-02-30 has the shape of a date and is none
    raise ValueError(f"{word!r} is not a time: give {TIME_FORMS}")


def time_span(text, unit, now=None):
    """How far back a time argument reaches, in `unit`, a bare number counting in that unit.

    ValueError for what is no time, and for a moment still ahead: a window reaching into the
    future is no window, and `ao mail compact` given one would have compacted every message.
    """
    when = parse_time(text, bare=unit, now=now)
    span = when.span(unit, now=now)
    if span < 0:
        raise ValueError(f"{when} is in the future")
    return span


def time_arg(unit=None):
    """The argparse type= of every time an `ao` option takes.

    Without `unit`, a When, for an option that names a moment (`--since`, `--until`); with
    one, the span back from now in that unit, the number `--days` and `--window` always held.
    Anything else exits 2 naming the forms, where it used to reach a command and end in a
    traceback.
    """
    import argparse

    def time_value(text):
        try:
            return time_span(text, unit) if unit else parse_time(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from None

    return time_value


# ── config ────────────────────────────────────────────────────────────────────

def find_root(start=None):
    """Nearest ancestor containing .ao/, else the git root, else cwd.

    An explicit path is taken at face value — asking for a directory and being
    given its parent is never what the caller meant. And $HOME/.ao is the global
    state directory, not a project marker, so the walk never treats home as a
    project root.
    """
    if start:
        return os.path.abspath(os.path.expanduser(start))
    d = os.path.abspath(os.getcwd())
    while True:
        if d != HOME and os.path.isdir(os.path.join(d, ".ao")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    top = _git_text(start or os.getcwd(), "rev-parse", "--show-toplevel")
    return top or os.path.abspath(start or os.getcwd())


def _has_top_level_json_key(raw, wanted):
    """Recognize a top-level JSON object key even when the document is truncated.

    Config parse failure cannot be allowed to erase an opt-in authority boundary.
    Scan only complete depth-one string tokens followed by ``:``; decoding each
    token with the JSON decoder also recognizes escaped spellings such as
    ``capability\\u005fmatrix`` without mistaking nested keys or string values for
    a top-level declaration. Invalid UTF-8 elsewhere is retained as surrogate
    text and does not hide an ASCII key.
    """
    if not isinstance(raw, (bytes, bytearray)):
        return False
    text = bytes(raw).decode(UTF8, "surrogateescape")
    length = len(text)
    index = 1 if text.startswith("\ufeff") else 0
    while index < length and text[index].isspace():
        index += 1
    if index >= length or text[index] != "{":
        return False
    depth = 1
    index += 1
    while index < length and depth:
        char = text[index]
        if char == '"':
            start = index
            index += 1
            escaped = False
            while index < length:
                current = text[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    index += 1
                    break
                index += 1
            else:
                return False
            if depth == 1:
                after = index
                while after < length and text[after].isspace():
                    after += 1
                if after < length and text[after] == ":":
                    try:
                        key = json.loads(text[start:index])
                    except (TypeError, ValueError, UnicodeError):
                        key = None
                    if key == wanted:
                        return True
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        index += 1
    return False


PROJECT_CONFIG_MAX_BYTES = 1_048_576
PROJECT_CONFIG_MAX_CONTAINER_DEPTH = 64


def _json_container_depth_problem(raw, max_depth=PROJECT_CONFIG_MAX_CONTAINER_DEPTH):
    """Return a bounded lexical depth error before the recursive JSON decoder.

    JSON structural characters are ASCII, so scanning bytes is sufficient and
    avoids decoding or recursively parsing a document whose container nesting is
    already outside the project-state contract. Braces inside strings and escaped
    quotes do not affect depth; all other syntax validation remains json.loads's
    job.
    """
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):  # [ {
            depth += 1
            if depth > max_depth:
                return (
                    f".ao/config.json exceeds the {max_depth}-level "
                    "container depth"
                )
        elif byte in (0x5D, 0x7D):  # ] }
            depth -= 1
    return None


def project_config_document(root):
    """Read one bounded, regular, non-empty-object project config document.

    The returned raw prefix is always at most PROJECT_CONFIG_MAX_BYTES. Keeping
    the parse result and its problem together gives pre-dispatch loading and
    commit enforcement one state table instead of two subtly different readers.
    """
    import stat

    path = os.path.join(root, ".ao", "config.json")
    raw = b""
    try:
        listed = os.lstat(path)
    except OSError as exc:
        return {
            "config": None,
            "raw": raw,
            "problem": f".ao/config.json is missing or unreadable ({exc})",
        }
    if not stat.S_ISREG(listed.st_mode):
        return {
            "config": None,
            "raw": raw,
            "problem": ".ao/config.json is not a regular file",
        }

    try:
        with open(path, "rb") as fh:
            opened = os.fstat(fh.fileno())
            if not stat.S_ISREG(opened.st_mode) or (
                listed.st_dev,
                listed.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                return {
                    "config": None,
                    "raw": raw,
                    "problem": ".ao/config.json changed before it could be read",
                }
            raw = fh.read(PROJECT_CONFIG_MAX_BYTES)
            finished = os.fstat(fh.fileno())
    except OSError as exc:
        return {
            "config": None,
            "raw": raw,
            "problem": f".ao/config.json is missing or unreadable ({exc})",
        }

    if max(opened.st_size, finished.st_size) > PROJECT_CONFIG_MAX_BYTES:
        return {
            "config": None,
            "raw": raw,
            "problem": (
                ".ao/config.json exceeds the "
                f"{PROJECT_CONFIG_MAX_BYTES:,}-byte limit"
            ),
        }
    if opened.st_size != finished.st_size or len(raw) != finished.st_size:
        return {
            "config": None,
            "raw": raw,
            "problem": ".ao/config.json changed while it was being read",
        }

    problem = _json_container_depth_problem(raw)
    if problem:
        return {"config": None, "raw": raw, "problem": problem}
    try:
        parsed = json.loads(raw.decode(UTF8))
    except (UnicodeError, ValueError, RecursionError) as exc:
        return {
            "config": None,
            "raw": raw,
            "problem": f".ao/config.json is not readable valid JSON ({exc})",
        }
    if not isinstance(parsed, dict) or not parsed:
        return {
            "config": None,
            "raw": raw,
            "problem": ".ao/config.json must be a non-empty top-level JSON object",
        }
    return {"config": parsed, "raw": raw, "problem": None}


def load_config(root):
    """Load bounded project config; malformed legacy state degrades explicitly.

    Missing config remains optional for discovery and ``ao init``. When a config
    path exists but cannot satisfy the project-state contract, callers receive
    defaults plus ``_config_problem`` instead of an exception or unbounded parse.
    Enrollment enforcement consumes the same ``project_config_document`` result.
    """
    p = os.path.join(root, ".ao", "config.json")
    document = project_config_document(root)
    cfg = document["config"] or {}
    if document["problem"] is not None and os.path.lexists(p):
        cfg["_config_problem"] = document["problem"]
        # Legacy malformed config has historically degraded to discovery. Once
        # the strict key is present, however, a parse failure must not erase the
        # opt-in and silently reactivate legacy authority. ``raw`` is bounded.
        if _has_top_level_json_key(document["raw"], "capability_matrix"):
            cfg["_capability_matrix_error"] = True
    cfg.setdefault("root", root)
    cfg.setdefault("mailbox", "agent-mail")
    cfg.setdefault("reviews", "semantic-review")
    resolve_roles(root, cfg)
    if "implementer" not in cfg:
        architect = cfg.get("architect") if isinstance(cfg.get("architect"), dict) else {}
        pinned = _concrete_session(architect.get("session"))
        found = discover_session(root, exclude={pinned} if pinned else ())
        if found:
            # Beside an architect on the same harness, the newest session may be the architect's own.
            beside = bool(architect) and block_adapter(architect) == found["adapter"]
            found["_session"] = {"how": "discovered", "trusted": not beside, "count": None,
                                 "why": "no implementer is configured: the newest session for this workspace"
                                        + (", where the architect's are kept too: read, but not resumed until an "
                                           "implementer is configured" if beside else "")}
            cfg["implementer"] = found
    # `auto` names no session: each role's is resolved here, once, for every reader (SESSION-IDENTITY).
    return resolve_sessions(root, cfg)


# ---- a split moves text, never behaviour (#44) ------------------------------------------

_PART_CALL = re.compile(r"""^(?:\w+\.)?_part\(\s*["']([\w-]+)["']\s*,\s*globals\(\)\s*\)\s*$""")


def top_level_statements(source, filename="<source>"):
    """(definitions, others) of one Python source: {name: [text]} for each top-level def, class
    and simple assignment, decorators included, and [text] for every other top-level statement
    except a part being loaded and a leading docstring."""
    import ast
    tree = ast.parse(source, filename)
    lines = source.splitlines(keepends=True)
    definitions, others = {}, []
    for index, node in enumerate(tree.body):
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        text = "".join(lines[start - 1:node.end_lineno])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
            names = [t.id for t in node.targets]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            names = []
        if names:
            for name in names:
                definitions.setdefault(name, []).append(text)
        elif _PART_CALL.match(text.strip()):
            continue
        elif index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
        else:
            others.append(text)
    return definitions, others


def split_moves(root, start=None, end=None):
    """What the staged candidate moves between Python files, and everything that is not a pure move (#44).

    A definition moves when it leaves one file and arrives in another; it must arrive
    byte for byte. In a candidate that moves, nothing else may change: no definition
    edited in place, lost or added, no other top-level statement changed, and every
    part the candidate adds is loaded by a `_part(name, globals())` call. Returns
    {"moved": [(name, from, to)], "problems": [text]}.

    Given two commits, the same proof reads a range that landed: `end` against `start`.
    A waived review of a move closes on it once the move has landed.
    """
    if start is None:
        listing, before, after = ("diff", "--cached", "--name-only", "--no-renames", "-z"), "HEAD", ""
    else:
        listing, before, after = ("diff", "--name-only", "--no-renames", "-z", start, end, "--"), start, end
    raw = _git_output(root, *listing).decode(UTF8, "replace").split("\0")
    paths = sorted({path for path in raw if path.endswith(".py")})

    def read(spec):
        result = subprocess.run([git_binary(), "show", spec], cwd=root, capture_output=True)
        return result.stdout.decode(UTF8, "replace") if result.returncode == 0 else None

    old, new = {}, {}
    old_other, new_other, loaded, parts = [], [], set(), []
    for path in paths:
        for side, spec, defs, other in (("old", f"{before}:{path}", old, old_other),
                                        ("new", f"{after}:{path}", new, new_other)):
            text = read(spec)
            if text is None:
                continue
            try:
                definitions, others = top_level_statements(text, path)
            except SyntaxError as exc:
                return {"moved": [], "problems": [f"{path} does not parse on the {side} side: {exc.msg}"]}
            for name, texts in definitions.items():
                defs.setdefault(name, []).extend((path, t) for t in texts)
            other.extend(others)
            if side == "new":
                loaded |= {m.group(1) for m in re.finditer(r"""_part\(\s*["']([\w-]+)["']""", text)}
                if "/parts/" in f"/{path}" and read(f"{before}:{path}") is None:
                    parts.append(path)
    moved, problems = [], []
    for name in sorted(set(old) | set(new)):
        before, after = old.get(name, []), new.get(name, [])
        if not after:
            problems.append(f"{name} left {', '.join(sorted({p for p, _ in before}))} and arrived nowhere")
            continue
        if not before:
            problems.append(f"{name} is new in {', '.join(sorted({p for p, _ in after}))}")
            continue
        was, now = sorted({p for p, _ in before}), sorted({p for p, _ in after})
        if sorted(t for _, t in before) != sorted(t for _, t in after):
            problems.append(f"{name} changed" + (f" on its way from {', '.join(was)} to {', '.join(now)}"
                                                 if was != now else f" in {', '.join(now)}"))
        elif was != now:
            moved.append((name, ", ".join(was), ", ".join(now)))
    if sorted(old_other) != sorted(new_other):
        problems.append("a top-level statement that is not a definition changed")
    for path in parts:
        if os.path.splitext(os.path.basename(path))[0] not in loaded:
            problems.append(f"{path} is not loaded by any _part call")
    if not moved and not problems:
        problems.append("nothing moved")
    return {"moved": moved, "problems": problems}


_part("lib_adapters", globals())


# ── shell ─────────────────────────────────────────────────────────────────────

def _shell_word(word):
    if os.name == "nt":
        return subprocess.list2cmdline([word])
    import shlex
    return shlex.quote(word)


def sh(cmd, cwd=None, timeout=20):
    return _sh_run(cmd, cwd=cwd, timeout=timeout)[0]


def _sh_run(cmd, cwd=None, timeout=20):
    """sh()'s command and how it ended: (stripped standard output, exit status), or ("", None).

    None when no shell could be started for it or it ran past its timeout. sh() returns the
    output alone, and a caller that keeps what a command printed has to tell a command that
    answered from one that failed.
    """
    # cmd.exe has no /dev/null; it calls it NUL, and the command failed instead (#71).
    if os.name == "nt":
        cmd = cmd.replace("2>/dev/null", "2>NUL")
    # Through a shell too, git is the compiled one, not a script standing in front of it (#51).
    if cmd.startswith("git "):
        cmd = _shell_word(git_binary()) + cmd[3:]
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True, encoding=UTF8, errors="replace", timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception:
        return "", None


_part("lib_transcript", globals())


_part("lib_gates", globals())


_part("lib_state", globals())


_part("lib_slices", globals())


_part("lib_mail", globals())


_part("lib_alarms", globals())


