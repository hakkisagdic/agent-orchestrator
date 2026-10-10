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


def _parts_folder(folder, path=os.path):
    """Where a module in `folder` keeps its parts: parts/ beside it, or `folder` itself when it is a parts
    folder, as a part that loads one is. `_part` runs a part from here and `ao split-check` takes the moved
    definitions to be here, by this one rule, so the proof never reads a file the loader does not run
    (SPLIT-CHECK-4)."""
    return folder if path.basename(folder) == "parts" else path.join(folder, "parts")


def _part(name, namespace):
    """Run the part `name` in `namespace` - the globals of the module it was moved out of.

    The part is read from the parts folder beside the file whose code calls this - the module, or a
    part that loads one - as it was compiled: a module in any folder runs the file the move proof read,
    where every call read lib.py's own parts/ (SPLIT-CHECK-4). It was read beside `namespace["__file__"]`,
    which the module's own code - an earlier part, an import alias, `globals().update` - could bind to
    another place than the proof read (SPLIT-CHECK-6). Only the module's own code loads its parts: a
    caller whose globals are not `namespace` - a forwarding loader in another module - is refused, and so
    is code compiled from no file, which read lib.py's parts/ in place of the proved one (SPLIT-CHECK-7).

    Compiled through the import system's own loader, so a part's bytecode is cached like
    any module's and an `ao` hook does not recompile thousands of lines on every run.
    """
    from importlib.machinery import SourceFileLoader
    frame = sys._getframe(1)
    if frame.f_globals is not namespace:
        raise RuntimeError(f"_part({name!r}) is called from code outside the module it loads into; a module loads "
                           "its own parts")
    caller = frame.f_code.co_filename
    if not caller or caller.startswith("<"):
        raise RuntimeError(f"_part({name!r}) is called from code compiled from no file ({caller}); a part is read "
                           "beside the file that loads it")
    folder = _parts_folder(os.path.dirname(os.path.abspath(caller)))
    path = os.path.join(folder, f"{name}.py")
    module = sys.modules.get(namespace.get("__name__"))
    if type(module) is type(sys) and module.__dict__ is namespace:
        module.__class__ = _KeepsItsLoader        # what binds its loader is kept from here on (#111)
    exec(SourceFileLoader(f"ao.parts.{name}", path).get_code(f"ao.parts.{name}"), namespace)


def _keeper(loader):
    """The class of a module whose parts `loader` loads: a name of the module that holds ao's lib or its loader is not
    rebound from outside it (#111).

    The move proof takes a module's loads to run ao's loader. Code that already landed could rebind it through an
    expression the proof does not read as the name it binds - `globals()["A"]._part = ...`, `setattr(A, "_part", ...)`,
    a function's `A=A` default, a module reached through `sys.modules` - and every load after that ran another loader.
    Set or deleted on the module, however it is reached, such a name is refused, and so are the class that refuses it
    and the module's class itself. What it refuses by is held here, not in a module's names: a check read through
    the lib's globals was itself a name to rebind (the review of SPLIT-CHECK-12). What writes a namespace without
    asking its module - `globals()`, `__dict__` - the proof reads instead.
    """
    def keep(module, name, how):
        held = getattr(module, name, None)
        try:
            # ao's lib is the module whose `_part` is ao's loader, read off the module itself: an entry in
            # `sys.modules` can be taken away by any code that runs (the review of SPLIT-CHECK-12).
            lib = getattr(held, "_part", None) is loader
        except Exception:                # an object that raises for a missing attribute is no module of ao's
            lib = False
        if name == "__class__" or held is loader or held is Keeper or lib:
            raise AttributeError(f"{module.__name__}.{name} binds ao's loader and is not {how} from outside its "
                                 "module (#111)")

    class Keeper(type(sys)):
        def __setattr__(self, name, value):
            keep(self, name, "set")
            super().__setattr__(name, value)

        def __delattr__(self, name):
            keep(self, name, "deleted")
            super().__delattr__(name)

    Keeper.__qualname__ = Keeper.__name__ = "_KeepsItsLoader"
    return Keeper


_KeepsItsLoader = _keeper(_part)


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


# The error handler Python's UTF-8 mode gives each standard stream.
UTF8_MODE_ERRORS = (("stdin", "surrogateescape"), ("stdout", "surrogateescape"), ("stderr", "backslashreplace"))


def utf8_streams():
    """Read and write the standard streams in UTF-8, as Python's UTF-8 mode does, whatever the locale (#71).

    Each of ao's entry points calls this before anything else. Every file ao writes is UTF-8, and so
    is what it prints: an MCP client, a hook, a scheduled job's log and a program reading a pipe all
    read it as UTF-8. A default Windows install reads and writes a pipe in the ANSI code page
    instead, and there an arrow, a box line or a Turkish letter ended the command - the MCP server
    with it - and a body piped in as UTF-8 was read as the code page's characters. A stream already
    in UTF-8 is left as it is. One that cannot be reconfigured keeps what it had, whatever it
    raised: there is none (pythonw), a caller replaced it with an object that has no encoding or
    raises for one, or it was already read from. That is no worse than before, and there is no
    other stream to say so on; nothing here may stop ao from starting.
    """
    import codecs
    streams = {"stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr}
    for name, errors in UTF8_MODE_ERRORS:
        stream = streams.get(name)
        try:
            if codecs.lookup(stream.encoding).name != "utf-8":
                stream.reconfigure(encoding=UTF8, errors=errors)
        except Exception:
            continue


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
    """(definitions, others, loads, order) of one Python source: {name: [text]} for each top-level def,
    class and simple assignment, decorators included; [text] for every other top-level statement
    except a part being loaded and a leading docstring; the name of each part a top-level
    `_part(name, globals())` call loads, which a mention in a docstring is not (SPLIT-CHECK-2); and
    every statement but the docstring in the order it runs, ("load", name) or ("text", text)
    (SPLIT-CHECK-3)."""
    import ast
    tree = ast.parse(source, filename)
    lines = source.splitlines(keepends=True)
    definitions, others, loads, order = {}, [], [], []
    for index, node in enumerate(tree.body):
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        text = "".join(lines[start - 1:node.end_lineno])
        # The call's own source, not its lines: a comment after it is no other statement (SPLIT-CHECK-3).
        load = _PART_CALL.match(ast.get_source_segment(source, node) or "") if isinstance(node, ast.Expr) else None
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
            order.append(("text", text))
        elif load:
            loads.append(load.group(1))
            order.append(("load", load.group(1)))
        elif index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
        else:
            others.append(text)
            order.append(("text", text))
    return definitions, others, loads, order


def _part_file(module, name):
    """The file `_part(name, globals())` in `module` runs: parts/<name>.py beside the module, or beside a part
    that loads one: `_parts_folder`, the rule `_part` itself reads by (SPLIT-CHECK-4)."""
    import posixpath
    return posixpath.join(_parts_folder(posixpath.dirname(module), posixpath), f"{name}.py")


def _module_level(nodes):
    """The nodes of a module's own namespace: its statements and what they hold, not the bodies of its functions,
    classes and comprehensions, which bind names of their own."""
    import ast
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.ListComp,
                             ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _scope_tables(top):
    """({(line, name): table} of each function and class body under the module's symbol table `top`, {id(table): the
    table it is nested in}): the compiler's own reading of which names a body binds, and which it reads from the module
    (SPLIT-CHECK-10), and where a name a body closes over is bound (SPLIT-CHECK-11)."""
    tables, parents, stack = {}, {}, [top]
    while stack:
        table = stack.pop()
        if table.get_type() in ("function", "class"):
            tables[(table.get_lineno(), table.get_name())] = table
        for child in table.get_children():
            parents[id(child)] = table
            stack.append(child)
    return tables, parents


def _closure_binding(table, name, parents):
    """The symbol of the function body that binds `name`, which `table` closes over; None when none does: a class
    body's names are no function's to close over (SPLIT-CHECK-11)."""
    scope = parents.get(id(table))
    while scope is not None and scope.get_type() != "module":
        if scope.get_type() == "function":
            try:
                symbol = scope.lookup(name)
            except KeyError:
                symbol = None
            if symbol is not None and not symbol.is_free() and not symbol.is_global():
                return symbol
        scope = parents.get(id(scope))
    return None


def _class_binds_first(scope, name, write):
    """Whether a class body binds `name` of its own before the statement that holds `write` runs, whatever runs: a def
    or class at its own level with no decorator or keyword, or an assignment there whose value does not read the name,
    and no `del` of it anywhere in the body (SPLIT-CHECK-11). A class body reads the module's name until it binds its
    own, so a write before that, or after a binding that may not run, sets the module's."""
    import ast
    if any(isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Del) for node in ast.walk(scope)):
        return False
    for statement in scope.body:
        if any(node is write for node in ast.walk(statement)):
            return False
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and statement.name == name:
            if not statement.decorator_list and not getattr(statement, "keywords", None):
                return True
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None:
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            stored = any(isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store)
                         for target in targets for node in ast.walk(target))
            if stored and not any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(statement.value)):
                return True
    return False


def _module_walruses(tree):
    """The names a walrus binds in the module's own namespace: one at module level, in a comprehension there, or in a
    default, decorator, annotation or base the module evaluates - not in a function's, a lambda's or a class's body,
    whose walrus binds a name of its own (SPLIT-CHECK-11)."""
    import ast
    names, stack = set(), list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.NamedExpr):
            names.add(node.target.id)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = node.args
            stack.extend(arguments.defaults + [default for default in arguments.kw_defaults if default is not None])
            if not isinstance(node, ast.Lambda):
                stack.extend(node.decorator_list + ([node.returns] if node.returns else []))
                stack.extend(arg.annotation for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs
                             + [arg for arg in (arguments.vararg, arguments.kwarg) if arg] if arg.annotation)
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend(node.decorator_list + node.bases + [keyword.value for keyword in node.keywords])
            continue
        stack.extend(ast.iter_child_nodes(node))
    return names


def _loader_names(tree):
    """The names a module's `_part` loads call through: `_part`, or `A` in `A._part`."""
    import ast
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_part":
                called.add("_part")
            elif isinstance(func, ast.Attribute) and func.attr == "_part" and isinstance(func.value, ast.Name):
                called.add(func.value.id)
    return called


def _loader_rebound(source, path, called=None):
    """The names a module's `_part` loads call through - `_part`, or `A` in `A._part` - that it binds to anything but
    ao's own loader (SPLIT-CHECK-7). A part runs in its module's namespace, so the names its module's loads call
    through, `called`, are read in it as in the module's own file (#111).

    The move proof takes a module's loads to run ao's `_part`. One that imported `_part` from a module of its own,
    or bound `A` to one, ran a loader the proof never read. The bindings allowed are `from . import lib as A`,
    `from ao import lib as A`, `import ao.lib as A`, `from .lib import _part`, `from ao.lib import _part`, and
    ao's lib defining `_part` itself; a function's own variable binds nothing of the module's.

    Which names a body binds is the compiler's answer, from `symtable`, not a list of the statements that bind: the
    list missed a nested def, an `except` name, a pattern's capture, a closure's name and a walrus in a default, and
    read a class body as a function's, where a class body reads the module's name until it binds its own
    (SPLIT-CHECK-10).
    """
    import ast
    import symtable
    tree = ast.parse(source)
    called = _loader_names(tree) | set(called or ())
    if not called:
        return []
    where = path.replace("\\", "/")
    # A relative `lib` is ao's only in ao's own package: `from .lib import _part` in another package imports
    # that package's lib (SPLIT-CHECK-8).
    # ao's own package is src/ao itself, not a folder that ends so - vendor/src/ao holds another package's lib
    # (SPLIT-CHECK-9), and the `_part` that lib defines is no loader of ao's (SPLIT-CHECK-10).
    in_ao = where.rpartition("/")[0] == "src/ao"
    own_lib = where == "src/ao/lib.py"
    top = symtable.symtable(source, path, "exec")
    rebound = set()
    # `nonlocal` names an enclosing function's variable, never the module's.
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            rebound.update(called & set(node.names))
    # Anything but an import that binds the name in the module's namespace - a statement, or a walrus in a default or
    # a comprehension - binds it to something else; the compiler marks the comprehension's as declared global. ao's lib
    # defining `_part` is read by its statements below, and a walrus anywhere that names `_part` there is taken for one
    # at module level.
    for name in called:
        try:
            symbol = top.lookup(name)
        except KeyError:
            continue
        assigned = symbol.is_assigned() or symbol.is_declared_global()
        if assigned and not (own_lib and name == "_part"):
            rebound.add(name)
    if own_lib and "_part" in _module_walruses(tree):
        rebound.add("_part")
    # `A._part = ...` sets the loader's attribute wherever `A` is the module's name for it. A function's own `A` - a
    # parameter, a nested def or class, an `except` name, a capture, or one it closes over - is another object,
    # unless an import bound it, which may be ao's lib; a class body reads the module's `A` until it binds its own
    # (SPLIT-CHECK-9, SPLIT-CHECK-10). Whether an import bound it is asked of the body that binds it, where an import in
    # any function counted for every one, and a class body's own `A` counts from the statement that surely binds it
    # (SPLIT-CHECK-11).
    tables, parents = _scope_tables(top)
    scopes = [(tree, None)] + [(node, tables.get((node.lineno, node.name))) for node in ast.walk(tree)
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for scope, table in scopes:
        for node in _module_level(scope.body):
            if not (isinstance(node, ast.Attribute) and node.attr == "_part" and isinstance(node.ctx, (ast.Store, ast.Del))
                    and isinstance(node.value, ast.Name) and node.value.id in called):
                continue
            name = node.value.id
            try:
                symbol = table.lookup(name) if table is not None else None
            except KeyError:
                symbol = None
            if symbol is None or symbol.is_global():
                own = False
            elif symbol.is_free():
                binding = _closure_binding(table, name, parents)
                own = binding is not None and not binding.is_imported()
            elif table.get_type() == "function":
                own = not symbol.is_imported()
            else:
                own = not symbol.is_imported() and _class_binds_first(scope, name, node)
            if not own:
                rebound.add(name)
    for node in _module_level(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in called and not (own_lib and node.name == "_part" and isinstance(node, ast.FunctionDef)):
                rebound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in called:
            rebound.add(node.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound in called and not (alias.name == "ao.lib" and alias.asname == bound):
                    rebound.add(bound)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    # A star import may bind any of them from anywhere: `A` as a public name, `_part` by `__all__`.
                    rebound.update(called)
                    continue
                bound = alias.asname or alias.name
                ours = (((node.module, node.level) == ("ao", 0) or in_ao and (node.module, node.level) == (None, 1))
                        and alias.name == "lib"
                        or ((node.module, node.level) == ("ao.lib", 0) or in_ao and (node.module, node.level) == ("lib", 1))
                        and alias.name == "_part")
                if bound in called and not ours:
                    rebound.add(bound)
        elif not isinstance(node, ast.alias):
            for value in (getattr(node, "name", None), getattr(node, "rest", None)):   # `except E as A`, a capture
                if isinstance(value, str) and value in called:
                    rebound.add(value)
    return sorted(rebound)


# What reaches a module's namespace, a frame's or an object's internals other than a statement that names what it binds
# (#111). The files that run in a module's namespace are read as a closed world for reflection: the builtins that hand a
# namespace over, run code or import by a name, reached from anything; getattr and its kin by a name the proof cannot
# read, by one of those names, or handed on; every dunder but the few that reach nothing; the attributes that hold a
# namespace, a frame or a module table, or make code from bytes or run a file's lines; and the modules that import by a
# name, unpickle or evaluate a string.
_NAMESPACE_BUILTINS = ("globals", "vars", "locals", "exec", "eval", "compile", "__import__", "__builtins__",
                       "breakpoint")
_REFLECTION = ("getattr", "setattr", "delattr", "hasattr")
_NAMESPACE_ATTRIBUTES = ("f_globals", "f_locals", "f_builtins", "_getframe", "modules", "import_module",
                         "load_module", "exec_module", "CodeType", "FunctionType", "LambdaType", "addpackage",
                         "addsitedir", "load_extension", "enable_load_extension")
_NAMESPACE_MODULES = ("builtins", "importlib", "inspect", "ctypes", "gc", "pickle", "_pickle", "marshal", "copyreg",
                      "shelve", "runpy", "code", "codeop", "operator", "string", "typing", "dataclasses", "pydoc",
                      "pkgutil", "timeit", "doctest", "cProfile", "profile", "pdb", "bdb", "trace", "logging",
                      "unittest", "multiprocessing", "zipimport")
# The dunders that reach nothing: a name, the file, a constructor, an exception's links, the protocols a class defines,
# and a folder name that is data.
_PLAIN_DUNDERS = ("__name__", "__file__", "__doc__", "__init__", "__main__", "__cause__", "__context__", "__getitem__",
                  "__version__", "__qualname__", "__module__", "__all__", "__enter__", "__exit__", "__len__",
                  "__iter__", "__next__", "__contains__", "__eq__", "__hash__", "__repr__", "__str__", "__bool__",
                  "__slots__", "__tests__")
# ao's loader and what keeps it reach the namespace by design, and this proof names what it looks for: each is read as
# the loader is, as ao's own code.
_NAMESPACE_OWN = ("_part", "_keeper", "_KeepsItsLoader", "_namespace_writes", "_compiles_a_pattern", "_internal",
                  "_NAMESPACE_BUILTINS", "_REFLECTION", "_NAMESPACE_ATTRIBUTES", "_NAMESPACE_MODULES",
                  "_PLAIN_DUNDERS")


def _internal(name):
    """Whether a name is a dunder that reaches an object's internals: every one but the few that reach nothing."""
    return len(name) > 4 and name.startswith("__") and name.endswith("__") and name not in _PLAIN_DUNDERS


def _namespace_writes(source, path):
    """What in one file that runs in a module's namespace can bind a name there that no statement names (#111).

    The move proof reads which names a statement binds. `globals()["A"] = ...`, code that `exec` or `compile` runs -
    under any file's name - a dict reached through `__dict__`, a function's `__globals__`, a frame or `sys.modules`, or
    the builtins reached by a name - `getattr(builtins, "globals")`, or a name built at run time - binds one no statement
    names, so the proof cannot say the module's loads run ao's loader. Reflection is read as a closed world, so a name
    computed at run time reaches nothing unread: what is refused is listed above. The `globals()` a load hands its part
    is the namespace it loads into, no write; ao's own loader and what keeps it are ao's code.

    What the proof reads is what a candidate changes and what runs where its parts load; it judges no code by what that
    code does. Code that already landed and runs code of its own choosing by other means - writes a part's file before
    it loads, starts a process - was reviewed when it landed, as the threat model has it; the proof is no review."""
    import ast
    tree = ast.parse(source)
    skipped, calls = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        calls.add(id(node.func))
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "_part" or isinstance(func, ast.Attribute) and func.attr == "_part") \
                and len(node.args) == 2 and isinstance(node.args[1], ast.Call) and not node.args[1].args \
                and isinstance(node.args[1].func, ast.Name) and node.args[1].func.id == "globals":
            skipped.update((id(node.args[1]), id(node.args[1].func)))
    if path.replace("\\", "/") == "src/ao/lib.py":
        for node in tree.body:
            names = [node.name] if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else \
                [target.id for target in getattr(node, "targets", []) if isinstance(target, ast.Name)]
            if any(name in _NAMESPACE_OWN for name in names):
                skipped.update(id(inner) for inner in ast.walk(node))
    found = []
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Name):
            if node.id in _NAMESPACE_BUILTINS or _internal(node.id):
                found.append(f"`{node.id}` at line {line}")
            elif node.id in _REFLECTION and id(node) not in calls:
                found.append(f"`{node.id}` handed on at line {line}")
        elif isinstance(node, ast.Attribute):
            # on any object: `import sys as s; s.modules`, `importlib.import_module("builtins").globals()`
            if node.attr in _NAMESPACE_ATTRIBUTES or node.attr in _REFLECTION or _internal(node.attr) \
                    or node.attr in _NAMESPACE_BUILTINS and not _compiles_a_pattern(node):
                found.append(f"`.{node.attr}` at line {line}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _internal(node.value) or node.value in _NAMESPACE_ATTRIBUTES:
                found.append(f"`{node.value!r}` at line {line}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _REFLECTION:
            name = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                found.append(f"`{node.func.id}` by a name computed at run time at line {line}")
            elif name.value in _NAMESPACE_BUILTINS or name.value in _REFLECTION:
                found.append(f"`{node.func.id}` of {name.value!r} at line {line}")
        elif isinstance(node, ast.Import):
            found.extend(f"`import {alias.name}` at line {line}" for alias in node.names
                         if alias.name.split(".")[0] in _NAMESPACE_MODULES)
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0] if not node.level else ""
            if top in _NAMESPACE_MODULES or any(alias.name in _NAMESPACE_BUILTINS + _REFLECTION + _NAMESPACE_ATTRIBUTES
                                                or _internal(alias.name) or alias.name == "*" and top == "sys"
                                                for alias in node.names):
                found.append(f"`from {'.' * node.level}{node.module or ''} import` at line {line}")
    return found


def _compiles_a_pattern(node):
    """Whether an attribute is `re.compile`, read off the `re` module by its name: `import builtins as re` is found by
    its import, and `re` bound to another module by what reached it."""
    import ast
    base = node.value
    return node.attr == "compile" and (isinstance(base, ast.Name) and base.id == "re"
                                       or isinstance(base, ast.Attribute) and base.attr == "re")


# The names ao's loads go through, `_part` and lib's `A`: read in a file whose module the proof did not reach.
_LOADER_NAMES = ("_part", "A")


def _namespace_findings(loaders, read, touched=None):
    """([(path, name)] bound to something else than ao's loader, [(path, what)] that writes the namespace unnamed) of
    every file that runs where a module's parts load: each module of `loaders`, {path: text}, each part it loads and
    each part those load, as `read(path)` gives them. Touched by the candidate or not: code that already landed runs
    there as surely as the candidate's (#111). And every file of `touched`, {path: text}, the candidate changes: a part
    whose module the candidate leaves alone is read with the names ao's loads go through."""
    import ast
    rebound, writes, seen = [], [], set()
    queue = [(path, text, set()) for path, text in sorted(loaders.items())]
    queue += [(path, text, set(_LOADER_NAMES)) for path, text in sorted((touched or {}).items())
              if path not in loaders]
    while queue:
        path, text, inherited = queue.pop(0)
        if path in seen or text is None:
            continue
        seen.add(path)
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            writes.append((path, f"code that does not parse ({exc.msg})"))
            continue
        names = _loader_names(tree) | inherited
        rebound.extend((path, name) for name in _loader_rebound(text, path, names))
        writes.extend((path, what) for what in _namespace_writes(text, path))
        for part in top_level_statements(text, path)[2]:
            target = _part_file(path, part)
            queue.append((target, read(target), names))
    return rebound, writes


def split_moves(root, start=None, end=None):
    """What the staged candidate moves between Python files, and everything that is not a pure move (#44).

    A definition moves when it leaves one file and arrives in another; it must arrive
    byte for byte, into a part the module it left loads with a top-level
    `_part(name, globals())` call: a rename, or a part another module loads, runs in
    another namespace (SPLIT-CHECK-2). In a candidate that moves, nothing else may
    change: no definition edited in place, lost or added, no other top-level statement
    changed in any file, no part's load removed, and every part the candidate adds is
    loaded. Returns {"moved": [(name, from, to)], "problems": [text]}.

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
    # Statements and loads are kept by file: one moved between files changed both (SPLIT-CHECK-2).
    other, loads, parts, present = {"old": {}, "new": {}}, {"old": {}, "new": {}}, [], {}
    order, loaders, touched = {"old": {}, "new": {}}, {}, {}
    for path in paths:
        for side, spec, defs in (("old", f"{before}:{path}", old), ("new", f"{after}:{path}", new)):
            text = read(spec)
            if text is None:
                continue
            try:
                definitions, others, loaded, sequence = top_level_statements(text, path)
            except SyntaxError as exc:
                return {"moved": [], "problems": [f"{path} does not parse on the {side} side: {exc.msg}"]}
            for name, texts in definitions.items():
                defs.setdefault(name, []).extend((path, t) for t in texts)
            other[side][path] = others                      # in their order (SPLIT-CHECK-3)
            loads[side][path] = set(loaded)
            order[side][path] = sequence
            present.setdefault(path, {})[side] = bool(definitions or others or loaded)
            if side == "new" and "/parts/" in f"/{path}" and read(f"{before}:{path}") is None:
                parts.append(path)
            if side == "new":
                touched[path] = text
                if loaded:
                    loaders[path] = text
    # A load runs ao's loader only through a name the module binds to it (SPLIT-CHECK-7), in its own file and in each
    # part that runs in its namespace, written by the candidate or landed before it (#111).
    rebinders, writers = _namespace_findings(loaders, lambda path: read(f"{after}:{path}"), touched)
    moved, problems = [], [f"{path} binds {name}, which its _part loads call through, to something else than "
                           "ao's loader" for path, name in rebinders]
    problems += [f"{path} can bind a name in the namespace its module's parts load into by {what}, which the move "
                 "proof cannot read" for path, what in writers]
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
            # A definition leaves a module only for a part that module loads, as a split does: renamed, or
            # into a part another module loads, it runs in another namespace (SPLIT-CHECK-2).
            loader = {_part_file(path, part) for path in was for part in loads["new"].get(path, set())}
            stray = [path for path in now if path not in was and path not in loader]
            if stray:
                problems.append(f"{name} moved to {', '.join(stray)}, which no file it left loads as its part")
            else:
                moved.append((name, ", ".join(was), ", ".join(now)))
    for path in sorted(set(other["old"]) | set(other["new"])):
        if other["old"].get(path, []) != other["new"].get(path, []):
            problems.append(f"a top-level statement that is not a definition changed in {path}")
    for path in sorted(loads["old"]):
        for name in sorted(loads["old"][path] - loads["new"].get(path, set())):
            problems.append(f"the _part call that loaded {name} is gone")
    # A part the candidate adds runs where the module that loads it loads it. Put in place there, each
    # file's statements must be its old ones in their old order - loads of existing parts included - and an
    # added file must be such a part, put in place once (SPLIT-CHECK-3).
    added = {path for path in order["new"] if path not in order["old"]}
    placed = set()
    for path in sorted(order["old"]):
        inlined = []
        for kind, value in order["new"].get(path, []):
            target = _part_file(path, value) if kind == "load" else None
            if target in added and target not in placed:
                placed.add(target)
                inlined.extend(order["new"][target])
            else:
                inlined.append((kind, value))
        if inlined != order["old"][path]:
            problems.append(f"{path} does not run its old statements in their old order with its new parts in place")
    for path in sorted(added - placed):
        if present.get(path, {}).get("new"):
            problems.append(f"{path} is added and is no new part that a module it was moved out of loads")
    # An empty, comment-only or docstring-only file holds nothing to compare, so one added or removed beside
    # a real move passed as part of it - an __init__.py changes which packages there are (CATCHUP-EVIDENCE-2).
    for path, sides in sorted(present.items()):
        if len(sides) == 1 and not any(sides.values()):
            problems.append(f"{path} is {'added' if 'new' in sides else 'removed'} and holds no definition: "
                            "a file's presence is not a move")
    loaded_files = {_part_file(path, part) for path, names in loads["new"].items() for part in names}
    for path in parts:
        if path not in loaded_files:
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


def self_child_env(env=None):
    """The environment a child that re-enters ao as `-m ao` needs, so a clone stays findable.

    ao's documented quickstart is a clone plus a symlink on PATH: `bin/ao` puts `src` on this
    process's `sys.path`, and nothing tells a child of it. Three places start such a child - the
    review runner, the watchdog's hunt and the MCP server's verify - and each died on "No module
    named ao" when ao was not installed. The review died silently: the process was gone before its
    log held anything a person would read, and the review stood at "running" until it was lost.
    The package's own parent goes first on PYTHONPATH, which is the directory this ao was imported
    from, whether that is a clone or an installation.
    """
    child = dict(os.environ if env is None else env)
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rest = [part for part in (child.get("PYTHONPATH") or "").split(os.pathsep) if part and part != parent]
    child["PYTHONPATH"] = os.pathsep.join([parent] + rest)
    return child


_part("lib_transcript", globals())


_part("lib_gates", globals())


_part("lib_state", globals())


_part("lib_slices", globals())


_part("lib_mail", globals())


_part("lib_alarms", globals())


_part("lib_lanes", globals())
_part("lib_pr", globals())
_part("lib_events", globals())


