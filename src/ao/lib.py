"""Shared library for the agent-orchestrator reference scripts.

Standard library only, by design — see docs/surfaces.md. Nothing here writes to a
vendor's session store; observation is strictly read-only.
"""
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

HOME = os.path.expanduser("~")
from . import settings  # noqa: E402  (reads HOME through this module, lazily)
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

C = {
    "reset": "\033[0m", "dim": "\033[2m", "b": "\033[1m", "green": "\033[32m",
    "red": "\033[31m", "yellow": "\033[33m", "cyan": "\033[36m",
    "mag": "\033[35m", "blue": "\033[34m",
}


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
    top = sh("git rev-parse --show-toplevel", cwd=start or os.getcwd())
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
        found = discover_session(root)
        if found:
            cfg["implementer"] = found
    return cfg


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


def split_moves(root):
    """What the staged candidate moves between Python files, and everything that is not a pure move (#44).

    A definition moves when it leaves one file and arrives in another; it must arrive
    byte for byte. In a candidate that moves, nothing else may change: no definition
    edited in place, lost or added, no other top-level statement changed, and every
    part the candidate adds is loaded by a `_part(name, globals())` call. Returns
    {"moved": [(name, from, to)], "problems": [text]}.
    """
    raw = _git_output(root, "diff", "--cached", "--name-only", "--no-renames", "-z").decode(UTF8, "replace").split("\0")
    paths = sorted({path for path in raw if path.endswith(".py")})

    def read(spec):
        result = subprocess.run([git_binary(), "show", spec], cwd=root, capture_output=True)
        return result.stdout.decode(UTF8, "replace") if result.returncode == 0 else None

    old, new = {}, {}
    old_other, new_other, loaded, parts = [], [], set(), []
    for path in paths:
        for side, spec, defs, other in (("old", f"HEAD:{path}", old, old_other), ("new", f":{path}", new, new_other)):
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
                if "/parts/" in f"/{path}" and read(f"HEAD:{path}") is None:
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
    # cmd.exe has no /dev/null; it calls it NUL, and the command failed instead (#71).
    if os.name == "nt":
        cmd = cmd.replace("2>/dev/null", "2>NUL")
    # Through a shell too, git is the compiled one, not a script standing in front of it (#51).
    if cmd.startswith("git "):
        cmd = _shell_word(git_binary()) + cmd[3:]
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True, encoding=UTF8, errors="replace", timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


_part("lib_transcript", globals())


_part("lib_gates", globals())


_part("lib_state", globals())


# ---- recall: what was decided, found or learned before, in every project (#43) ----------

_RECALL_STOP = frozenset(
    "the and for with that this from into have has had was were are not but then than when what which who why how "
    "its our your their there here been being will would could should about after before over under only also just "
    "very more most some such each other same one two can may must does did done yet still any all bir ve ile için "
    "bu şu da de mi ne gibi daha çok".split())
_FINDING_LINE = re.compile(r"^\s*- \[(BLOCKER|HIGH|MEDIUM|LOW)\]\s*(.+)$")


def _recall_words(text):
    words = (word.strip("._-") for word in re.findall(r"[a-zçğıöşü0-9][a-zçğıöşü0-9_.-]*", str(text or "").lower()))
    return {word for word in words if len(word) >= 3 and word not in _RECALL_STOP}


def _epoch(value):
    """A recorded time as seconds, whether ao wrote it as a number or as ISO text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def recall_roots(root=None):
    """(project, root) for every project this machine has registered, the one asking first."""
    out, seen = [], set()
    if root:
        out.append((project_key(root), root))
        seen.add(os.path.realpath(root))
    for name, row in sorted(project_registry().items()):
        path = row.get("root")
        if path and os.path.isdir(path) and os.path.realpath(path) not in seen:
            seen.add(os.path.realpath(path))
            out.append((name, path))
    return out


def recall_entries(project, root):
    """What one project remembers, each with the file and row it came from (#43).

    Questions asked and their answers, architect decisions, waivers, review
    findings with their review's verdict, and lessons. A source that cannot be
    read is passed over; the rest still answer.
    """
    entries = []

    def add(kind, ident, at, text, outcome, source):
        entries.append({"project": project, "kind": kind, "id": ident, "at": _epoch(at), "text": text,
                        "outcome": outcome, "source": source})

    for rec in decisions(root):
        add("question", rec.get("id"), rec.get("answered_at") or rec.get("asked_at"),
            " ".join(str(rec.get(key) or "") for key in ("question", "context", "answer")),
            f"answered: {rec['answer']}" if rec.get("answer") else rec.get("state") or "open",
            f"{DECISION_DIR}/{rec.get('id')}.json")
    readers = ((decision_rows, "decision", ("decision", "why", "scope"), ".ao/ledger/decisions.jsonl"),
               (waiver_rows, "waiver", ("gate", "slice", "why"), ".ao/ledger/waivers.jsonl"))
    for reader, kind, fields, source in readers:
        try:
            rows = reader(root)
        except Exception:
            continue
        for number, row in enumerate(rows, 1):
            if isinstance(row, dict):
                add(kind, row.get("id"), row.get("at"), " ".join(str(row.get(key) or "") for key in fields),
                    row.get("event") or ("recorded by " + str(row.get("by") or "architect")), f"{source}:{number}")
    reviews_dir = "semantic-review"
    try:
        with open(os.path.join(root, ".ao", "config.json"), encoding=UTF8) as fh:
            reviews_dir = json.load(fh).get("reviews") or reviews_dir
    except (OSError, ValueError, AttributeError):
        pass
    directory = os.path.join(root, reviews_dir)
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        path = os.path.join(directory, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding=UTF8, errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        verdict = _review_verdict(body)
        for number, line in enumerate(body.splitlines(), 1):
            found = _FINDING_LINE.match(line)
            if found:
                add("finding", name, os.path.getmtime(path), found.group(2), f"{found.group(1)} in a {verdict} review",
                    f"{reviews_dir}/{name}:{number}")
    try:
        with open(os.path.join(root, "docs", "lessons.md"), encoding=UTF8, errors="replace") as fh:
            lessons = fh.read().splitlines()
    except OSError:
        lessons = []
    starts = [number for number, line in enumerate(lessons) if line.startswith("## ")]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lessons)
        add("lesson", lessons[start][3:].strip(), None, "\n".join(lessons[start:end]), "a lesson",
            f"docs/lessons.md:{start + 1}")
    return entries


def recall(text, root=None, limit=10, exclude=(), share=0.5):
    """Records in every registered project that share the words of `text`, best first (#43).

    Plain words over the files ao already keeps: no index, no service, no network.
    One or two words must all appear; longer text needs `share` of its words.
    """
    words = _recall_words(text)
    if not words:
        return []
    need = len(words) if len(words) <= 2 else max(2, int(len(words) * share + 0.999))
    found = []
    for project, path in recall_roots(root):
        try:
            entries = recall_entries(project, path)
        except Exception:
            continue
        for entry in entries:
            if entry["id"] in exclude:
                continue
            score = len(words & _recall_words(entry["text"]))
            if score >= need:
                found.append(dict(entry, score=score))
    found.sort(key=lambda entry: (-entry["score"], -(entry["at"] or 0)))
    return found[:limit]


def architect_absence(root, cfg):
    """When the architect was last seen, and the questions waiting for its return (#84).

    Seen means its session wrote, it recorded a decision, or it left mail still in
    the mailbox, whichever is newest.
    """
    seen = []
    try:
        found = discover_architect((cfg.get("architect") or {}).get("cwd") or root)
        if found and found.get("age") is not None:
            seen.append(time.time() - float(found["age"]))
    except Exception:
        pass
    try:
        rows = decision_rows(root)
        if rows and _epoch(rows[-1].get("at")):
            seen.append(_epoch(rows[-1].get("at")))
    except Exception:
        pass
    mailbox_dir = cfg.get("mailbox", "agent-mail")
    for name in mailbox(root, mailbox_dir):
        if from_architect(name, cfg):
            try:
                seen.append(os.path.getmtime(os.path.join(root, mailbox_dir, name)))
            except OSError:
                pass
    waiting = sorted(decisions(root, "open"), key=lambda d: d.get("asked_at") or 0)
    return {"seen_at": max(seen) if seen else None, "waiting": [d["id"] for d in waiting],
            "oldest_at": waiting[0].get("asked_at") if waiting else None}


# ---- a worktree lives exactly as long as its slice (#42) --------------------------------

def default_branch(root):
    """The branch work lands on: origin's HEAD, else main, else master, else the main checkout's."""
    remote = git_text(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if remote.startswith("refs/remotes/origin/"):
        return remote[len("refs/remotes/origin/"):]
    for name in ("main", "master"):
        if git_text(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
            return name
    trees = worktree_list(root)
    return trees[0]["branch"] if trees else None


def worktree_list(root):
    """Every worktree of the repository: {"path", "head", "branch", "prunable"}, the main one first."""
    listed = _git_output(root, "worktree", "list", "--porcelain", "-z")
    out = []
    for record in listed.split(b"\0\0"):
        fields = {}
        for attribute in record.split(b"\0"):
            key, _, value = os.fsdecode(attribute).partition(" ")
            if key:
                fields[key] = value
        if "worktree" in fields:
            branch = fields.get("branch", "")
            out.append({"path": fields["worktree"], "head": fields.get("HEAD"),
                        "branch": branch[len("refs/heads/"):] if branch.startswith("refs/heads/") else None,
                        "prunable": "prunable" in fields})
    return out


def reviews_in_flight(root):
    """Submitted reviews in one checkout whose runner is alive or has only just started."""
    directory = os.path.join(root, ".ao", "reviews")
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    flying = []
    for name in names:
        if not (name.startswith("R-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(directory, name), encoding=UTF8) as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and state.get("state") == "running" and (
                _pid_alive(state.get("pid")) if state.get("pid")
                else time.time() - float(state.get("submitted_at") or 0) < 60):
            flying.append(state.get("id") or name[:-5])
    return flying


def _tree_bytes(path):
    total = 0
    for directory, subdirs, files in os.walk(path):
        subdirs[:] = [d for d in subdirs if not os.path.islink(os.path.join(directory, d))]
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total


def worktree_facts(root, cfg, sizes=False):
    """What decides whether each worktree may go (#42).

    A worktree may go when its branch is merged into the default branch, when the
    board rejected the slice that owns it (`worktree:` or `branch:` on the item),
    or when its directory is already gone - and never while it holds product
    changes nobody committed, a review in flight, or the command that is asking.
    """
    target = default_branch(root)
    board_items = board(root)
    owners = []
    for state, items in board_items.items():
        for item in items:
            owners.append((state, item))
    here = os.path.realpath(root)
    out = []
    for index, tree in enumerate(worktree_list(root)):
        path, branch = tree["path"], tree["branch"]
        real = os.path.realpath(path)
        slice_state = next((state for state, item in owners
                            if (item["notes"].get("worktree") and os.path.realpath(item["notes"]["worktree"]) == real)
                            or (branch and item["notes"].get("branch") == branch)), None)
        merged = bool(branch and target and branch != target and subprocess.run(
            [git_binary(), "merge-base", "--is-ancestor", tree["head"], target], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
        exists = os.path.isdir(path)
        dirty = product_dirty(path, cfg) if exists else []
        flying = reviews_in_flight(path) if exists else []
        keep = []
        if index == 0:
            keep.append("the main checkout")
        if real == here:
            keep.append("the checkout asking")
        if dirty:
            keep.append(f"{len(dirty)} uncommitted product change(s)")
        if flying:
            keep.append(f"review in flight: {', '.join(flying)}")
        why = "merged into " + target if merged else "its slice was rejected" if slice_state == "rejected" \
            else "its directory is gone" if not exists or tree["prunable"] else None
        may_go = bool(why) and not keep
        # Sizes are for what may go: walking every dependency tree on disk is not free.
        out.append(dict(tree, merged=merged, slice_state=slice_state, dirty=len(dirty), in_flight=flying,
                        keep=keep, why=why, may_go=may_go,
                        bytes=_tree_bytes(path) if sizes and may_go and exists else None))
    return out


def prune_worktree(root, fact, apply=False, now=None):
    """Retire one worktree that may go: archive its coordination state and branch tip, then remove both (#42).

    The `.ao/` state and review artefacts go to ~/.ao/archive/<project>/, and the
    branch tip stays reachable as refs/ao/archive/<branch>-<stamp>, so what the
    worktree held can still be read after it is gone.
    """
    import shutil
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y%m%d-%H%M%S")
    name = os.path.basename(os.path.normpath(fact["path"]))
    archive = os.path.join(HOME, ".ao", "archive", project_key(root), f"worktree-{name}-{stamp}")
    steps = []
    for part in (".ao", "semantic-review"):
        source = os.path.join(fact["path"], part)
        if os.path.isdir(source):
            steps.append(f"archive {part}/ to {archive}")
            if apply:
                shutil.copytree(source, os.path.join(archive, part), symlinks=True)
    if fact["branch"]:
        steps.append(f"keep {fact['branch']} at refs/ao/archive/{fact['branch']}-{stamp}")
        if apply:
            _git_output(root, "update-ref", f"refs/ao/archive/{fact['branch']}-{stamp}", fact["head"])
    steps.append(f"remove the worktree {fact['path']}")
    if apply and os.path.isdir(fact["path"]):
        _git_output(root, "worktree", "remove", "--force", fact["path"])
    if fact["branch"]:
        steps.append(f"delete the branch {fact['branch']}")
        if apply:
            _git_output(root, "branch", "-D", fact["branch"])
    steps.append("git worktree prune")
    if apply:
        _git_output(root, "worktree", "prune")
    return steps


# ---- the secondary project: the same agent, another queue (#8, #22, #92) ----------------

HUMAN_WAITING = ("human", "insan", "person", "owner")


def secondary_projects(cfg):
    """The projects this one names as secondary (`"secondary": [{"root", "name"}]`), each with its config (#8)."""
    out = []
    for entry in (cfg or {}).get("secondary") or []:
        other_root = entry.get("root") if isinstance(entry, dict) else entry
        if not isinstance(other_root, str) or not os.path.isdir(os.path.join(other_root, ".ao")):
            continue
        try:
            other = load_config(other_root)
        except Exception:
            continue
        name = (entry.get("name") if isinstance(entry, dict) else None) or project_key(other_root)
        out.append({"name": name, "root": other_root, "cfg": other})
    return out


def working_elsewhere(cfg, idle_seconds):
    """The secondary project the implementer is writing in now, if it is (#22).

    2026-09-07: while the implementer worked in an ao worktree, the Voltrai
    watchdog read the same agent as idle with a slice running and nudged it
    back. Presence belongs to the agent: a transcript moving in a secondary
    project is the agent working.
    """
    for other in secondary_projects(cfg):
        try:
            transcript, _ = session_paths(other["cfg"])
            age = time.time() - os.path.getmtime(transcript)
        except (OSError, TypeError, ValueError):
            continue
        if age < idle_seconds:
            return {"name": other["name"], "root": other["root"], "age": age}
    return None


def secondary_ready(cfg):
    """The first READY item of a secondary project, for an implementer with nothing READY here (#8)."""
    for other in secondary_projects(cfg):
        try:
            items = ready(other["root"])
        except Exception:
            continue
        if items:
            return {"name": other["name"], "root": other["root"], "item": items[0]["id"]}
    return None


def human_waits(root):
    """Blocked board items the implementer declared as waiting on a person: `waiting: human` (#92)."""
    return [item for item in board(root)["blocked"]
            if (item["notes"].get("waiting") or "").strip().lower() in HUMAN_WAITING]


# ---- the process is measured by its outcomes (#49) ---------------------------------------

def _since_epoch(text):
    try:
        return datetime.strptime(str(text).strip()[:16], "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return None


def slice_outcomes(root, project=None):
    """What happened to every slice that landed, from what ao already recorded (#49).

    On 2026-09-07 the round budget, the size guideline and the review pipeline
    were all re-decided in one day on anecdote. Each outcome is read, never
    typed: the verdicts in the order ao recorded them (a re-specification keeps
    the history, it does not start a new one), rounds, ready-to-landed time, size
    by kind, and whether a defect was later found in what landed - a
    retrospective review that asked for changes, or a board item `fixes:` it.
    """
    from .storage import read_chained_jsonl
    project = project or project_key(root)
    try:
        reviews = [row for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN) if isinstance(row, dict)]
        grants = [row for row in authority_rows(root) if isinstance(row, dict) and row.get("granted") is True]
    except Exception:
        return []
    try:
        verifications = [row for row in read_chained_jsonl(os.path.join(root, ".ao", "ledger", "verifications.jsonl"),
                                                           VERIFICATION_CHAIN, legacy_prefix=True)
                         if isinstance(row, dict)]
    except Exception:
        verifications = []
    try:
        waivers = {row.get("id"): row for row in waiver_rows(root)
                   if isinstance(row, dict) and row.get("event") == "waived"}
    except Exception:
        waivers = {}
    by_artefact = {row.get("artefact"): row for row in reviews}
    items = {item["id"]: item for state_items in board(root).values() for item in state_items}
    fixed = {item["notes"]["fixes"].strip() for item in items.values() if item["notes"].get("fixes")}
    out = {}
    for grant in grants:
        linked = by_artefact.get(grant.get("review")) or {}
        slice_id = linked.get("slice") or (waivers.get(grant.get("waiver")) or {}).get("slice")
        if not slice_id or slice_id in out:
            continue
        mine = [row for row in reviews if row.get("slice") == slice_id]
        prospective = [row for row in mine if row.get("kind") == "index-candidate"
                       and row.get("verdict") in ("APPROVED", "NEEDS_CHANGES")]
        candidate = (grant.get("candidate") or {}).get("digest")
        size = next((row.get("candidate_size") for row in reversed(verifications)
                     if (row.get("candidate") or {}).get("digest") == candidate and row.get("candidate_size")), None)
        started = _since_epoch((items.get(slice_id) or {}).get("notes", {}).get("since", "")) \
            or (min(float(row.get("at") or 0) for row in mine) if mine else None)
        landed = float(grant.get("at") or 0) or None
        retro = [row for row in mine if row.get("kind") == "commit-range" and row.get("verdict") == "NEEDS_CHANGES"]
        out[slice_id] = {
            "project": project, "slice": slice_id, "verdicts": [row.get("verdict") for row in prospective],
            "rounds": len(prospective), "first_pass": bool(prospective) and prospective[0].get("verdict") == "APPROVED",
            "waived": not prospective and bool(grant.get("waiver")),
            "started_at": started, "landed_at": landed,
            "hours": round((landed - started) / 3600, 2) if landed and started and landed >= started else None,
            "size": size, "defect_found": bool(retro) or slice_id in fixed,
        }
    return list(out.values())


def outcome_stats(outcomes):
    """The distribution a process change is judged against: rounds, first-pass rate, time, size, defects."""
    def spread(values):
        values = sorted(v for v in values if v is not None)
        if not values:
            return None
        return {"median": values[len(values) // 2], "p90": values[min(len(values) - 1, int(len(values) * 0.9))],
                "n": len(values)}
    reviewed = [o for o in outcomes if not o["waived"]]
    return {
        "slices": len(outcomes), "waived": len(outcomes) - len(reviewed),
        "rounds": spread(o["rounds"] for o in reviewed),
        "first_pass_pct": round(100 * sum(o["first_pass"] for o in reviewed) / len(reviewed)) if reviewed else None,
        "hours": spread(o["hours"] for o in outcomes),
        "product_lines": spread((o["size"]["kinds"]["product"]["added"] + o["size"]["kinds"]["product"]["deleted"])
                                if isinstance(o.get("size"), dict) else None for o in outcomes),
        "defects_pct": round(100 * sum(o["defect_found"] for o in outcomes) / len(outcomes)) if outcomes else None,
    }


# ---- every store is bounded, without being asked (#50) -----------------------------------

OBSERVATION_LOGS = ("nudge-{key}.log", "watchdog-{key}.log", "refill-{key}.log", "escalate-{key}.log",
                    "cycles-{key}.jsonl")


def bound_store(path, kb):
    """Keep an observation store within its bound as it is written: past it, the oldest records go (#50).

    Measured 2026-09-08: notices 3.2 MB, a nudge log 2.6 MB, and `ao prune`, which
    existed and defaulted to a dry run, had never been run - the only outcome a
    manual cleanup has. A store is trimmed once it is a quarter over its bound,
    back to three quarters of it, at a line boundary, so it is not rewritten on
    every write. Evidence is never trimmed here; it is sealed.
    """
    limit = int(kb) * 1024
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size <= limit * 1.25:
        return False
    from .storage import replace_file_durably
    with open(path, "rb") as fh:
        fh.seek(size - int(limit * 0.75))
        tail = fh.read()
    replace_file_durably(path, tail[tail.find(b"\n") + 1:])
    return True


def observation_stores(root, state_dir=None):
    """Every observation store of a project: its ledgers' and the watchdog's logs, as paths."""
    key = project_key(root)
    state_dir = state_dir or os.path.join(HOME, ".ao")
    stores = [os.path.join(root, ".ao", "ledger", name) for name in ("notices.jsonl", "progress.jsonl")]
    return stores + [os.path.join(state_dir, pattern.format(key=key)) for pattern in OBSERVATION_LOGS]


def bound_observation_logs(root, state_dir=None):
    """Hold every observation store to its bound; the watchdog does this each cycle (#50)."""
    limit = settings.get(load_config(root), "retention.observation_kb")
    return [path for path in observation_stores(root, state_dir) if bound_store(path, limit)]


def stores_over_bound(root, cfg, state_dir=None):
    """(path, bytes, bound) for each observation store over its bound, for `ao doctor` (#50)."""
    limit = settings.get(cfg, "retention.observation_kb") * 1024
    out = []
    for path in observation_stores(root, state_dir):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size > limit * 1.25:
            out.append((path, size, limit))
    return out


# ---- a standing bug-hunter: read-only, bounded, leads not verdicts (#45) -----------------

HUNT_CATEGORIES = ("correctness", "concurrency", "clock", "durability", "subprocess", "portability", "secrets",
                   "authority", "tests")
_LEAD = re.compile(r"^\s*-\s*\[([a-z]+)\]\s*([^\s:]+):(\d+)\s+(\S+)\s+[—-]+\s*(.+?)\s*$")


def hunter_ledger_path(root):
    return os.path.join(root, ".ao", "ledger", "hunter.jsonl")


def hunter_record(root, event, **fields):
    from .storage import append_jsonl
    append_jsonl(hunter_ledger_path(root), scan_record(dict(fields, at=int(time.time()), event=event)))


def hunter_rows(root):
    from .storage import read_jsonl
    return [row for row in read_jsonl(hunter_ledger_path(root)) if isinstance(row, dict)]


def hunter_known(root):
    """Fingerprints already sent or discarded: a repeat is suppressed, a discard remembered (#45)."""
    return {row.get("fingerprint") for row in hunter_rows(root) if row.get("event") in ("sent", "discarded")}


def hunt_slice(root, cfg):
    """The next bounded slice of tracked product files, from a cursor that goes round the tree (#45)."""
    try:
        listed = [os.fsdecode(p) for p in _git_output(root, "ls-files", "-z").split(b"\0") if p]
    except RuntimeError:
        return [], 0
    candidates = [p for p in listed if not _is_coordination_path(p, cfg) and os.path.isfile(os.path.join(root, p))]
    if not candidates:
        return [], 0
    state_path = os.path.join(root, ".ao", "hunter.json")
    try:
        with open(state_path, encoding=UTF8) as fh:
            cursor = int(json.load(fh).get("cursor") or 0) % len(candidates)
    except (OSError, ValueError, TypeError, AttributeError):
        cursor = 0
    files, spent, index = [], 0, cursor
    budget, most = settings.get(cfg, "hunter.bytes_per_run"), settings.get(cfg, "hunter.files_per_run")
    while len(files) < most and len(files) < len(candidates):
        path = candidates[index % len(candidates)]
        index += 1
        try:
            with open(os.path.join(root, path), encoding=UTF8) as fh:
                text = fh.read(budget)
        except (OSError, UnicodeDecodeError):
            continue
        if files and spent + len(text) > budget:
            break
        files.append((path, text))
        spent += len(text)
    from .storage import replace_file_durably
    replace_file_durably(state_path, (json.dumps({"cursor": index % len(candidates)}) + "\n").encode(UTF8))
    return files, cursor


def parse_leads(out):
    """Leads a hunter wrote as `- [category] path:line symbol — what is wrong`, each with a fingerprint (#45)."""
    leads = []
    for line in str(out or "").splitlines():
        found = _LEAD.match(line)
        if not found or found.group(1) not in HUNT_CATEGORIES:
            continue
        category, path, number, symbol, text = found.groups()
        fingerprint = hashlib.sha256(f"{path}|{symbol}|{category}".encode(UTF8)).hexdigest()[:16]
        leads.append({"category": category, "path": path, "line": int(number), "symbol": symbol, "finding": text,
                      "fingerprint": fingerprint})
    return leads


# ---- the governance survives the disk (#46) ---------------------------------------------

_BACKUP_SKIP = re.compile(r"(\.lock|\.tmp|\.pending|\.bak[^/]*|~)$|(^|/)\.ao/reviews/|(^|/)\.ao/hunter\.json$")


def governance_files(root, cfg):
    """Every file a project's control plane lives in, relative to the root (#46).

    Config, authority, board, backlog, gates and sources; decisions, parked work and
    every ledger with its sealed archives; the mailbox; and the review artefacts a
    grant rests on. Locks, temporary files and config backups are not governance.
    """
    out = []
    for top in (".ao", cfg.get("mailbox", "agent-mail")):
        base = os.path.join(root, top)
        for directory, subdirs, files in os.walk(base):
            subdirs[:] = [d for d in subdirs if not os.path.islink(os.path.join(directory, d))]
            for name in files:
                rel = os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/")
                if not _BACKUP_SKIP.search(rel) and os.path.isfile(os.path.join(root, rel)):
                    out.append(rel)
    reviews_dir = cfg.get("reviews", "semantic-review")
    try:
        for name in {row.get("review") for row in authority_rows(root)
                     if isinstance(row, dict) and row.get("granted") is True and row.get("review")}:
            rel = f"{reviews_dir}/{name}"
            if os.path.isfile(os.path.join(root, rel)):
                out.append(rel)
    except Exception:
        pass
    return sorted(set(out))


def _manifest(root, files):
    entries = {}
    for rel in files:
        with open(os.path.join(root, rel), "rb") as fh:
            entries[rel] = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    return {"schema": 1, "project": project_key(root), "at": int(time.time()),
            "head": git_text(root, "rev-parse", "HEAD") or None, "files": entries}


def write_backup(root, cfg, destination):
    """Write the governance to a destination the project names: a directory, `ref`, or `remote:<name>` (#46).

    A directory gets `<project>/<stamp>/` with a manifest of every file's digest. A
    ref is a commit of the same files under refs/ao/backup/latest, made from blob
    and tree objects without touching the index or the worktree. A remote gets that
    ref pushed, and only after the host says the repository is private.
    Returns the manifest with where it went, and it is recorded in the ledger.
    """
    files = governance_files(root, cfg)
    manifest = _manifest(root, files)
    stamp = datetime.fromtimestamp(manifest["at"]).strftime("%Y%m%d-%H%M%S")
    if destination.startswith("remote:"):
        remote = destination.split(":", 1)[1]
        if remote_is_private(root, remote) is not True:
            raise RuntimeError(f"not pushing governance to {remote}: the host did not confirm it is private")
    if destination == "ref" or destination.startswith("remote:"):
        where = _backup_ref(root, manifest)
        if destination.startswith("remote:"):
            _git_output(root, "push", remote, "refs/ao/backup/latest:refs/ao/backup/latest", timeout=300)
            where = f"{remote} {where}"
    else:
        target = os.path.join(os.path.expanduser(destination), manifest["project"], stamp)
        from .storage import replace_file_durably
        for rel in files:
            with open(os.path.join(root, rel), "rb") as fh:
                replace_file_durably(os.path.join(target, rel), fh.read())
        replace_file_durably(os.path.join(target, "manifest.json"),
                             (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode(UTF8))
        where = target
    manifest["where"] = where
    from .storage import append_jsonl
    append_jsonl(os.path.join(root, ".ao", "ledger", "backups.jsonl"),
                 {"at": manifest["at"], "where": where, "files": len(files), "head": manifest["head"]})
    return manifest


def _backup_ref(root, manifest):
    """A commit holding the governance files and the manifest, made without an index (#46)."""
    tree = {}
    for rel in list(manifest["files"]) + ["ao-backup-manifest.json"]:
        data = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode(UTF8) if rel == "ao-backup-manifest.json" \
            else open(os.path.join(root, rel), "rb").read()
        blob = subprocess.run([git_binary(), "hash-object", "-w", "--stdin"], cwd=root, input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()
        node = tree
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = blob

    def write(node):
        lines = []
        for name in sorted(node):
            if isinstance(node[name], dict):
                lines.append(f"040000 tree {write(node[name])}\t{name}")
            else:
                lines.append(f"100644 blob {node[name]}\t{name}")
        return subprocess.run([git_binary(), "mktree"], cwd=root, input=("\n".join(lines) + "\n").encode(UTF8),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()

    commit = subprocess.run([git_binary(), "-c", "user.name=ao", "-c", "user.email=ao@localhost", "commit-tree",
                             write(tree), "-m", f"ao backup of {manifest['project']} governance"], cwd=root,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()
    _git_output(root, "update-ref", "refs/ao/backup/latest", commit)
    return f"refs/ao/backup/latest {commit[:12]}"


def remote_is_private(root, remote):
    """True only when the host says the remote's repository is private; None when it cannot say (#46, #83)."""
    import shutil
    url = git_text(root, "remote", "get-url", remote)
    found = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url or "")
    if not found or not shutil.which("gh"):
        return None
    try:
        answer = subprocess.run(["gh", "api", f"repos/{found.group(1)}/{found.group(2)}", "--jq", ".private"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {"true": True, "false": False}.get(answer.stdout.strip())


def restore_backup(root, source):
    """Put the governance back from a backup directory, verifying every file against its manifest (#46).

    Returns (restored, unverified): a file whose bytes do not match the digest it was
    backed up with is not restored, and is named.
    """
    with open(os.path.join(source, "manifest.json"), encoding=UTF8) as fh:
        manifest = json.load(fh)
    from .storage import replace_file_durably
    restored, unverified = [], []
    for rel, digest in sorted(manifest["files"].items()):
        path = os.path.join(source, rel)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            unverified.append(f"{rel}: missing from the backup")
            continue
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            unverified.append(f"{rel}: its bytes do not match the manifest")
            continue
        replace_file_durably(os.path.join(root, rel), data)
        restored.append(rel)
    return restored, unverified


def backup_age(root):
    """(seconds since the newest backup, where it went), or None when there is none (#46)."""
    from .storage import read_jsonl
    rows = [row for row in read_jsonl(os.path.join(root, ".ao", "ledger", "backups.jsonl")) if isinstance(row, dict)]
    return (time.time() - float(rows[-1]["at"]), rows[-1].get("where")) if rows else None


# ---- the harness content seam: borrow, pin, verify (#14) ---------------------------------

CONTENT_TEXT = (".md", ".txt", ".json", ".yaml", ".yml")


def content_manifest_path(root):
    return os.path.join(root, ".ao", "content.json")


def content_manifest(root):
    try:
        with open(content_manifest_path(root), encoding=UTF8) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"skills": []}
    return data if isinstance(data, dict) and isinstance(data.get("skills"), list) else {"skills": []}


def fetch_pinned(source, pin, paths, workdir):
    """Check out exactly `paths` of `source` at commit `pin`; nothing unpinned is ever fetched (#14)."""
    if not re.fullmatch(r"[0-9a-f]{40}", str(pin or "")):
        raise ValueError(f"{pin!r} is not a pin: name a full 40-character commit id, never a branch or a tag")
    git = git_binary()
    subprocess.run([git, "init", "-q", workdir], check=True, capture_output=True)
    subprocess.run([git, "-C", workdir, "fetch", "-q", "--depth", "1", source, pin], check=True, capture_output=True,
                   timeout=300)
    fetched = subprocess.run([git, "-C", workdir, "rev-parse", "FETCH_HEAD"], check=True, capture_output=True,
                             text=True).stdout.strip()
    if fetched != pin:
        raise RuntimeError(f"{source} answered {fetched[:12]}, not the pinned {pin[:12]}")
    subprocess.run([git, "-C", workdir, "checkout", "-q", pin, "--", *paths], check=True, capture_output=True)


def vendor_skills(root, source, pin, skills, harnesses):
    """Copy chosen skills, text files only, into each harness's discovery directory at a pinned commit (#14).

    Where a harness's adapter declares `directives.skills_dir` a skill keeps its
    files; where it declares `directives.steering_dir` the skill's SKILL.md becomes
    a manually included steering file. Anything executable - a file with its exec
    bit, a `#!` script, anything but text - and every `hooks/` directory is skipped
    and named. Each written file's digest is recorded in .ao/content.json.
    """
    import tempfile
    record = content_manifest(root)
    written = []
    with tempfile.TemporaryDirectory(prefix="ao-content-") as work:
        fetch_pinned(source, pin, [f"skills/{name}" for name in skills], work)
        for name in skills:
            if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
                raise ValueError(f"{name!r} is not a skill name")
            base = os.path.join(work, "skills", name)
            if not os.path.isdir(base):
                raise ValueError(f"{name} is not a skill at {pin[:12]}")
            files, skipped = [], []
            for directory, subdirs, names in os.walk(base):
                if "hooks" in subdirs:
                    subdirs.remove("hooks")
                    skipped.append(os.path.relpath(os.path.join(directory, "hooks"), base) + "/ (hooks are never imported)")
                for file_name in sorted(names):
                    full = os.path.join(directory, file_name)
                    rel = os.path.relpath(full, base).replace(os.sep, "/")
                    with open(full, "rb") as fh:
                        data = fh.read()
                    if not file_name.lower().endswith(CONTENT_TEXT) or os.access(full, os.X_OK) or data[:2] == b"#!":
                        skipped.append(rel)
                        continue
                    files.append((rel, data))
            digests = {}
            from .storage import replace_file_durably
            for harness in harnesses:
                directives = load_adapter(harness, root).get("directives") or {}
                if directives.get("skills_dir"):
                    for rel, data in files:
                        target = f"{directives['skills_dir']}/{name}/{rel}"
                        replace_file_durably(os.path.join(root, target), data)
                        digests[target] = "sha256:" + hashlib.sha256(data).hexdigest()
                elif directives.get("steering_dir") and dict(files).get("SKILL.md"):
                    body = dict(files)["SKILL.md"].decode(UTF8, "replace")
                    body = re.sub(r"\A---\n.*?\n---\n+", "", body, flags=re.S)
                    data = ("---\ninclusion: manual\n---\n\n" + body).encode(UTF8)
                    target = f"{directives['steering_dir']}/{name}.md"
                    replace_file_durably(os.path.join(root, target), data)
                    digests[target] = "sha256:" + hashlib.sha256(data).hexdigest()
            record["skills"] = [entry for entry in record["skills"] if entry.get("skill") != name]
            record["skills"].append({"skill": name, "source": source, "pin": pin, "files": digests, "skipped": skipped})
            written.append((name, digests, skipped))
    from .storage import replace_file_durably
    replace_file_durably(content_manifest_path(root), (json.dumps(record, indent=1, sort_keys=True) + "\n").encode(UTF8))
    return written


def verify_content(root):
    """Vendored files whose bytes no longer match the digest they were vendored with (#14)."""
    drift = []
    for entry in content_manifest(root)["skills"]:
        for target, digest in sorted((entry.get("files") or {}).items()):
            try:
                with open(os.path.join(root, target), "rb") as fh:
                    actual = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                drift.append(f"{target} ({entry['skill']}@{entry['pin'][:12]}) is missing")
                continue
            if actual != digest:
                drift.append(f"{target} ({entry['skill']}@{entry['pin'][:12]}) changed since it was vendored")
    return drift


_UNSAFE_HOOK = re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b|\brm\s+-rf\s+(/|~|\$HOME)(\s|$)")
_UNPINNED_RUNNER = ("npx", "bunx", "uvx", "pipx")


def agent_config_files(root):
    """Project files that configure an agent, as every adapter declares them, and AGENTS.md (#76)."""
    files = []

    def add(rel):
        rel = str(rel or "")
        if rel and not rel.startswith("~") and not os.path.isabs(rel) and rel not in files:
            files.append(rel)

    for _, entry in sorted(adapter_catalog(root).items()):
        adapter = entry["adapter"]
        directives = adapter.get("directives") or {}
        for rel in (directives.get("rule_files") or []) + (directives.get("steering_files") or []) \
                + ((directives.get("command_hooks") or {}).get("files") or []):
            add(rel)
        add((adapter.get("mcp") or {}).get("file"))
        steering = directives.get("steering_dir")
        if steering and os.path.isdir(os.path.join(root, *steering.split("/"))):
            for name in sorted(os.listdir(os.path.join(root, *steering.split("/")))):
                if name.endswith(".md"):
                    add(f"{steering}/{name}")
    add("AGENTS.md")
    return files


def agent_config_findings(root):
    """AgentShield's check categories over a project's agent configuration, ported natively (#14).

    Secrets in agent files, allow rules that admit everything, permissions with no
    deny list, hooks that pipe a download into a shell or remove the home directory,
    and MCP servers run from an unpinned package. Only allow rules are scored, so a
    deny rule naming `--no-verify` is not a finding, and `env -u VAR` is not read as
    dumping the environment (the known false positives in docs/upstream.md).
    Returns [(category, text)].
    """
    out = []
    files = agent_config_files(root)
    for rel in files:
        try:
            with open(os.path.join(root, rel), encoding=UTF8, errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        hits = scan_evidence(text)[1]
        if hits:
            out.append(("secrets", f"{rel} holds what looks like a credential: {', '.join(sorted(set(hits)))}"))
        if not rel.endswith(".json"):
            continue
        try:
            document = json.loads(text)
        except ValueError:
            continue
        permissions = document.get("permissions") if isinstance(document, dict) else None
        if isinstance(permissions, dict):
            broad = [rule for rule in permissions.get("allow") or [] if str(rule).strip() in ("*", "Bash", "Bash(*)", "Bash(*:*)")]
            if broad:
                out.append(("permissive-allow", f"{rel} allows {', '.join(broad)}: every command, a hook bypass and a push "
                                                "among them"))
            if permissions.get("allow") and not permissions.get("deny"):
                out.append(("missing-deny", f"{rel} allows tools and denies nothing"))
        hooks = document.get("hooks") if isinstance(document, dict) else None
        for entries in (hooks.values() if isinstance(hooks, dict) else []):
            for entry in entries if isinstance(entries, list) else []:
                for hook in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
                    command = str((hook or {}).get("command") or "") if isinstance(hook, dict) else ""
                    if _UNSAFE_HOOK.search(command):
                        out.append(("hook-safety", f"{rel} runs a hook that pipes a download into a shell or removes "
                                                   f"a home: {command[:80]}"))
        servers = document.get("mcpServers") if isinstance(document, dict) else None
        for name, server in (servers.items() if isinstance(servers, dict) else []):
            command = os.path.basename(str((server or {}).get("command") or ""))
            args = [str(arg) for arg in (server or {}).get("args") or []]
            packages = [arg for arg in args if not arg.startswith("-")]
            if command in _UNPINNED_RUNNER and packages and not re.search(r".@\d", packages[0]):
                out.append(("mcp-hygiene", f"{rel} runs MCP server {name} from {command} {packages[0]} with no "
                                           "version pinned"))
    return out


# ---- asking the codebase a question is a capability, not a copy (#81) --------------------

def ask_codebase(root, cfg, question, timeout=300):
    """Answer a question about the code through a configured provider, never without citations (#81).

    ao cannot embed a code-intelligence engine without breaking `dependencies = []`,
    and should not reimplement one. A provider is a command in `codebase.provider`
    (argv with {question} and {root}) that prints JSON: {"answer": text, "citations":
    [{"file": path, "lines": "a-b"}]}. With none configured, or an answer that cites
    nothing, the question is refused and what would satisfy it is named.
    Returns {"answer", "citations"} or raises ValueError.
    """
    provider = (cfg.get("codebase") or {}).get("provider") or {}
    argv = provider.get("argv") if isinstance(provider, dict) else None
    if not argv:
        raise ValueError("no codebase provider is configured: set codebase.provider.argv to a command that takes "
                         "{question} and {root} and prints {\"answer\", \"citations\"} as JSON (ctxman is the first "
                         "provider documented in docs/upstream.md)")
    rendered = [str(part).replace("{question}", question).replace("{root}", root) for part in argv]
    try:
        result = subprocess.run(rendered, cwd=root, capture_output=True, text=True, encoding=UTF8, errors="replace",
                                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"the codebase provider did not run: {exc}") from exc
    try:
        document = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError(f"the codebase provider did not answer in JSON (exit {result.returncode})") from exc
    answer = str((document or {}).get("answer") or "").strip() if isinstance(document, dict) else ""
    citations = [item for item in (document.get("citations") or []) if isinstance(item, dict) and item.get("file")] \
        if isinstance(document, dict) else []
    if not answer or not citations:
        raise ValueError("the provider's answer cites no file and line range; an answer without citations is not "
                         "an answer ao passes on")
    missing = [item["file"] for item in citations if not os.path.exists(os.path.join(root, item["file"]))]
    if missing:
        raise ValueError(f"the answer cites files that are not in this repository: {', '.join(missing[:3])}")
    return {"answer": scan_evidence(answer)[0], "citations": citations}


def safe_slug(text, fallback="note", limit=40):
    """A file-name part holding only [A-Za-z0-9._-] (#19).

    `ao mail send note "ao: kiro/* dal …"` kept the "/" and wrote into a directory
    that did not exist. Separators, wildcards, quotes, whitespace and letters
    outside ASCII all become a dash; what is left is cut to `limit`.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "")).strip("-.")[:limit].strip("-.")
    return slug or fallback


def note(root, cfg, to, title, body, urgent=False):
    """Write an architect message into the mailbox through the tool.

    So that a woken architect needs no raw Write or Edit to do its job. That
    matters because the one time it had them, it used them on the orchestrator's
    own source and built a runaway. The mailbox is the only thing an unattended
    architect should be able to write, and this is the only door to it.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    os.makedirs(box, exist_ok=True)
    slug = safe_slug(title.lower(), "not")
    kind = "ACIL" if urgent else "DECISION"
    impl, arch = mail_names(cfg)
    to = safe_slug(to or impl, impl)          # the implementer's role by default, whoever holds it (#31)
    name = f"{time.strftime('%Y%m%d-%H%M')}-{arch}-to-{to}-{kind}-{slug}.md"
    text = f"# {title}\n\n" + ("## ACİL\n\n" if urgent else "") + body.rstrip() + "\n"
    return write_mail(root, cfg, name, text, {"kind": kind.lower(), "from": arch, "to": to})


def running_slice(root):
    """The one board item review accounting treats as current.

    A malformed board may contain several running entries. Preserve the board's
    established first-entry behavior, but resolve it once so review attribution,
    start time and re-specification cannot silently select different slices.
    """
    items = board(root)["running"]
    return items[0] if items else None


def slice_boundary(item):
    """The declared review boundary for a parsed board item."""
    if not item:
        return ""
    notes = item.get("notes") or {}
    return notes.get("acceptance") or notes.get("scope") or item.get("title") or ""


# ---- a slice's boundary: a sentence, or a file the row points at (#73, #35) ------------

BOUNDARY_SECTIONS = ("invariant", "scenarios", "paths", "out of scope", "why one slice")
_BOUNDARY_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")
_NAMED_FILE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.\w+|[\w-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|"
                         r"rb|cs|swift|c|h|cpp|hpp|sql|sh|ps1|json|ya?ml|toml|md))(?![\w/-])")
_NAMED_SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})(?:\(\))?`")


def boundary_pointer(item):
    """(path, commit or None) when a board item's boundary is a file: `boundary: docs/slices/B8a.md@1a2b3c4`."""
    value = (((item or {}).get("notes") or {}).get("boundary") or "").strip()
    if not value:
        return None
    path, _, commit = value.partition("@")
    return path.strip(), (commit.strip() or None)


def boundary_sections(text):
    """{section: body} for the headed parts of a boundary file (#73)."""
    sections, current = {}, None
    for line in text.splitlines():
        heading = re.match(r"^#{1,4}\s+(.+?)\s*$", line)
        if heading:
            name = heading.group(1).strip().lower()
            current = next((section for section in BOUNDARY_SECTIONS if section in name), None)
            if current:
                sections.setdefault(current, "")
            continue
        if current:
            sections[current] += line + "\n"
    return sections


def read_boundary(root, item):
    """The boundary file a slice points at, read at the commit the pointer names (#73).

    The boundary lived as prose in a table cell, and twice on 2026-09-07/08 the
    boundary itself was what was wrong, found mid-slice by an implementer that had
    started, with no diff to show what changed. A file read at a named commit is
    one source of truth, and when it has changed since, the change travels to the
    reviewer as a diff. None when the item's boundary is a sentence.

    Returns {"file", "commit", "changed", "sha256", "label", "text", "sections", "problem"}.
    """
    pointer = boundary_pointer(item)
    if not pointer:
        return None
    path, commit = pointer
    out = {"file": path, "commit": commit, "changed": False, "sha256": None, "sections": {}, "problem": None}
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, path))
    if os.path.isabs(path) or not target.startswith(real_root + os.sep):
        out["problem"] = "it is not a path inside the repository"
    elif commit and not _BOUNDARY_COMMIT.fullmatch(commit):
        out["problem"] = f"{commit!r} is not a commit id"
    else:
        try:
            if commit:
                data = _git_output(root, "show", f"{commit}:{path}")
            else:
                with open(target, "rb") as fh:
                    data = fh.read()
        except (OSError, RuntimeError) as exc:
            out["problem"] = str(exc)
    if out["problem"]:
        out.update(label=f"{path} (unreadable)",
                   text=f"the boundary file {path} cannot be read: {out['problem']} - say so as a finding")
        return out
    content = data.decode(UTF8, "replace")
    diff = ""
    if commit:
        try:
            diff = _git_output(root, "diff", commit, "--", path).decode(UTF8, "replace")
        except RuntimeError:
            diff = ""
    label = f"{path} at {commit[:12]}" if commit else f"{path} as it stands in the worktree"
    text = f"the boundary file {label}:\n\n{content}"
    if diff.strip():
        label += ", changed since"
        text += (f"\n\nThe file has changed since {commit[:12]}. The change is part of what you judge: "
                 f"a boundary that moved mid-slice is a finding unless a decision records it.\n{diff}")
    out.update(changed=bool(diff.strip()), sha256="sha256:" + hashlib.sha256(data).hexdigest(), label=label,
               text=text, sections=boundary_sections(content))
    return out


def declared_paths(item, boundary=None):
    """[(path, new)] a slice declares: its boundary file's Paths section, or its row's `paths:` note."""
    if boundary and boundary.get("sections", {}).get("paths"):
        raw = [line.strip().lstrip("-*").strip() for line in boundary["sections"]["paths"].splitlines()]
    else:
        raw = (((item or {}).get("notes") or {}).get("paths") or "").split(",")
    out = []
    for entry in raw:
        entry = entry.strip().strip("`").strip()
        new = bool(re.search(r"\((?:new|yeni)\)$", entry, re.I))
        path = re.sub(r"\s*\((?:new|yeni)\)$", "", entry, flags=re.I).strip().strip("`").strip()
        if path:
            out.append((path, new))
    return out


def _symbol_owners(root, symbol):
    """Tracked files that define a symbol, by a definition keyword in front of its name."""
    pattern = rf"(def|class|function|interface|type|struct|enum|trait|fn|func|const|let|var)[[:space:]]+{symbol}"
    try:
        listed = _git_output(root, "grep", "-l", "-z", "-w", "-E", pattern, timeout=20)
    except RuntimeError:
        return []
    return [os.fsdecode(path) for path in listed.split(b"\0") if path]


def boundary_conflicts(root, item):
    """What a slice's declared paths cannot hold, found when it is registered (#35).

    2026-09-07: B8a named eight paths while its own acceptance could only be met
    by touching a ninth; the implementer found out mid-slice and lost three hours
    waiting on a decision. Advisory prose matching, so it names and never blocks:
    a declared path that neither exists nor is marked `(new)`, a file the
    acceptance names outside the declared paths, and the file defining a symbol
    the acceptance names in backticks when that file is outside them. An item
    that declares no paths has nothing to check.
    """
    source = read_boundary(root, item)
    if source and source["problem"]:
        return [f"its boundary file {source['file']} cannot be read: {source['problem']}"]
    declared = declared_paths(item, source)
    if not declared:
        return []

    def inside(path):
        return any(path == d or path.startswith(d.rstrip("/") + "/") for d, _ in declared)

    out = [f"{path} is declared but does not exist; write `(new)` after it if the slice creates it"
           for path, new in declared if not new and not os.path.exists(os.path.join(root, path))]
    if source:
        text = "\n".join(body for name, body in source["sections"].items()
                         if name not in ("paths", "out of scope", "why one slice")) \
            or source["text"]
    else:
        text = slice_boundary(item)
    tracked = None
    for named in sorted(set(_NAMED_FILE.findall(text))):
        if inside(named) or any(d.endswith("/" + named) for d, _ in declared):
            continue
        if "/" in named:
            if os.path.exists(os.path.join(root, named)):
                out.append(f"the acceptance names {named}, outside the declared paths")
            continue
        if tracked is None:
            try:
                tracked = [os.fsdecode(p) for p in _git_output(root, "ls-files", "-z").split(b"\0") if p]
            except RuntimeError:
                tracked = []
        owners = [p for p in tracked if os.path.basename(p) == named]
        if owners and not any(inside(p) for p in owners):
            out.append(f"the acceptance names {named} ({', '.join(owners[:3])}), outside the declared paths")
    for symbol in sorted(set(_NAMED_SYMBOL.findall(text)))[:10]:
        owners = _symbol_owners(root, symbol)
        if owners and not any(inside(p) for p in owners):
            out.append(f"the acceptance names `{symbol}`, defined in {', '.join(owners[:3])}, "
                       "outside the declared paths")
    return out


def boundary_advice(root, cfg, item):
    """Everything worth saying about one item's boundary before it is worked (#35, #73)."""
    notes = (item or {}).get("notes") or {}
    out = boundary_conflicts(root, item)
    if notes.get("boundary") and notes.get("acceptance"):
        out.append("it has both a boundary file and an acceptance sentence; one source of truth, so keep the file")
    limit = settings.get(cfg, "boundary.inline_max_chars")
    sentence = notes.get("acceptance") or ""
    if not notes.get("boundary") and len(sentence) > limit:
        out.append(f"its acceptance is {len(sentence)} characters; above boundary.inline_max_chars ({limit}) it "
                   "belongs in a file the row points at with `boundary: path@commit`")
    return out


DECISION_CHAIN = "ao-decision-row-v1"


def decisions_path(root):
    return os.path.join(root, ".ao", "ledger", "decisions.jsonl")


def decision_rows(root):
    """Architect decisions in the order `ao decide` recorded them (#65).

    Rows written before the ledger was chained are read as its legacy prefix. A
    row added by hand after a chained one breaks the chain and the read raises:
    a re-specification nobody recorded must not reset a round budget.
    """
    from .storage import read_chained_jsonl
    return [row for row in read_chained_jsonl(decisions_path(root), DECISION_CHAIN, legacy_prefix=True)
            if isinstance(row, dict)]


def respecified_at(root, item=None):
    """When the architect last re-specified the current slice (`ao decide --scope <id>`).

    None when the decision ledger cannot be trusted: the budget then stands.
    """
    item = item or running_slice(root)
    item_id = ((item or {}).get("id") or (item or {}).get("key") or "").strip()
    if not item_id:
        return None
    try:
        rows = decision_rows(root)
    except Exception:
        return None
    last = 0
    for r in rows:
        if r.get("by") == "architect" and (r.get("scope") or "").strip() == item_id:
            try:
                last = max(last, int(r.get("at") or 0))
            except (TypeError, ValueError):
                continue
    return last or None


def _recorded_review_slice(root, reviews_dir, row):
    """The slice of a review recorded before ledger rows carried it, from its unchanged file."""
    try:
        with open(os.path.join(root, reviews_dir, str(row.get("artefact"))), "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if "sha256:" + hashlib.sha256(data).hexdigest() != row.get("sha256"):
        return None
    evidence = review_evidence(data.decode(UTF8, "replace"))
    return (evidence or {}).get("slice") if isinstance(evidence, dict) else None


def rounds(root, reviews_dir):
    """Completed prospective review rounds spent on the current slice (#65).

    A round is a review that produced a verdict, read from the chained review
    ledger in the order ao recorded it. Deleting, emptying or back-dating a review
    file changes nothing, and neither does editing the board's `since:`, which an
    implementer can reach. Only index-candidate reviews of the running board
    item's exact ID count - HEAD and candidate bytes are not slice identity. The
    count runs back to the newest approval that could authorise, or to the
    architect's newest recorded re-specification of the item. A fallback's
    approval after a rejection of the same candidate authorises nothing, so it
    ends nothing: the rejection still counts. UNAVAILABLE and INVALID are not
    rounds; nobody completed a review.

    Review files from before the ledger have no row and are read as they always
    were, so a slice that began before it keeps its count.
    """
    current = running_slice(root)
    slice_id = ((current or {}).get("id") or (current or {}).get("key") or "").strip()
    if not slice_id:
        return 0
    respec = respecified_at(root, current) or 0

    from .storage import read_chained_jsonl
    try:
        rows = read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN)
    except Exception:
        rows = []                       # an unreadable ledger is reported elsewhere; files still count
    events, recorded, rejected = [], set(), set()
    for order, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("kind") != "index-candidate":
            continue
        recorded.add(row.get("artefact"))
        verdict, candidate = row.get("verdict"), row.get("candidate")
        if verdict not in ("APPROVED", "NEEDS_CHANGES"):
            continue
        row_slice = row["slice"] if "slice" in row else _recorded_review_slice(root, reviews_dir, row)
        mine = (row_slice or "").strip() == slice_id
        try:
            at = int(row.get("at") or 0)
        except (TypeError, ValueError):
            at = 0
        if verdict == "NEEDS_CHANGES":
            if candidate:
                rejected.add(candidate)
            if mine:
                events.append((at, 1, order, "round"))
        elif mine and row.get("authorizable") is True \
                and not (row.get("fallback") and candidate in rejected):
            events.append((at, 1, order, "approved"))

    for order, (f, v) in enumerate(reviews(root, reviews_dir, limit=None)):
        if f in recorded or v not in ("APPROVED", "NEEDS_CHANGES"):
            continue
        path = os.path.join(root, reviews_dir, f)
        try:
            at = int(os.path.getmtime(path))
            body = open(path, errors="replace", encoding=UTF8).read(100_000)
        except OSError:
            continue
        evidence = review_evidence(body)
        if not evidence or evidence.get("kind") != "index-candidate" \
                or (evidence.get("slice") or "").strip() != slice_id \
                or evidence.get("review_status") in ("unavailable", "invalid"):
            continue
        events.append((at, 0, -order, "approved" if v == "APPROVED" else "round"))

    n = 0
    for at, _, _, what in sorted(events, reverse=True):
        if at < respec or what == "approved":
            break
        n += 1
    return n


def mailbox(root, mail_dir):
    d = os.path.join(root, mail_dir)
    files = [f for f in sorted(os.listdir(d)) if f != "README.md" and f.endswith(".md")] if os.path.isdir(d) else []
    if mail_store_mode(root) != "append-only":
        return files
    # The queue is derived from the store: a view file removed unhandled still counts (#80).
    try:
        rows = mail_store_rows(root)
    except Exception:
        return files
    stored = {row.get("id") for row in rows if row.get("event") == "message"}
    handled = {row.get("id") for row in rows if row.get("event") == "handled"}
    return sorted((stored - handled) | {name for name in files if name not in stored})


REMOTE_PREFIX = "refs/remotes/origin/"


def _default_remote_branch(root):
    """The remote default branch to measure a checkout against, as a full ref, or None.

    origin/HEAD can still name a branch the remote has since deleted, so a candidate
    is used only when it resolves to a commit.
    """
    candidates = []
    try:
        ref = _git_output(root, "symbolic-ref", "--quiet",
                          "refs/remotes/origin/HEAD").decode(UTF8, "replace").strip()
        if ref.startswith(REMOTE_PREFIX):
            candidates.append(ref)
    except RuntimeError:
        pass
    for candidate in (*candidates, REMOTE_PREFIX + "main", REMOTE_PREFIX + "master"):
        try:
            _git_output(root, "rev-parse", "--verify", "--quiet", candidate + "^{commit}")
            return candidate
        except RuntimeError:
            continue
    return None


def git_state(root):
    """Where this checkout stands, not only what it holds.

    An ahead count alone reads as current on a checkout that is weeks behind. On
    2026-09-14 `ao status` printed "0 commits unpushed" for a branch 77 commits
    behind main and already merged into it, and two backlog rows were written from
    measurements taken in that checkout. So measure against the remote default
    branch, report behind as well as ahead, say when the branch is already merged,
    and say "?" when there is nothing to compare against instead of "0".

    "ahead" stays a string for existing readers; "behind" is an int or None.
    """
    ref = _default_remote_branch(root)
    ahead = behind = None
    if ref:
        try:
            counts = _git_output(root, "rev-list", "--left-right", "--count",
                                 f"{ref}...HEAD").decode(UTF8, "replace").split()
            if len(counts) == 2 and all(c.isdigit() for c in counts):
                behind, ahead = int(counts[0]), int(counts[1])
        except RuntimeError:
            pass
    merged = False
    if ahead == 0 and behind:
        # The default branch itself, checked out behind its remote, needs a pull; it
        # is not a merged branch. A detached head inside the base counts as merged.
        try:
            head = _git_output(root, "symbolic-ref", "--quiet", "HEAD").decode(UTF8, "replace").strip()
        except RuntimeError:
            head = ""
        merged = head != "refs/heads/" + ref[len(REMOTE_PREFIX):]
    known = ahead is not None
    return {
        "log": sh("git log --oneline -4", cwd=root).split("\n"),
        "dirty": [l for l in sh("git status --short", cwd=root).split("\n") if l.strip()],
        "ahead": str(ahead) if known else "?",
        "behind": behind,
        "base": ref[len("refs/remotes/"):] if known else None,
        "merged": merged,
    }


# ---- fan-out budget --------------------------------------------------------
#
# A coordinator that fans out to sub-agents has no gate of its own. One ran 47
# verification agents at once: 11 finished, 36 died with "session limit", two
# million tokens were spent and the answer was mostly missing. The watchdog's
# quota guard could not have helped — it watches the implementer's pool, and the
# fan-out was the coordinator's. This is the gate that was missing: a hard cap,
# the provider window as keyflip reports it, and the project's own record of
# what fan-outs cost, so the estimate is empirical after the first one.

def fanout_config(cfg):
    """The fan-out limits in force for this project (#74)."""
    return {name: settings.get(cfg, f"fanout.{name}")
            for name in ("max_agents", "per_agent_tokens", "window_reserve_pct")}


def provider_window(name):
    """The machine-wide usage window keyflip reports for a provider, parsed.

    Read through the same adapter command the status panel uses, so the panel and
    the verdict never disagree. Returns {pct, window, resets_in, resets_s, raw} or
    None when no readable line exists — and None is reported as "unreadable", not
    as headroom.
    """
    argv = None
    for _, adapter in sorted(package_adapters().items()):
        spec = (adapter.get("telemetry") or {}).get("quota") or {}
        if spec.get("argv"):
            argv = spec["argv"]
            break
    if not argv:
        return None
    out = sh(" ".join(argv) + " 2>/dev/null", cwd=HOME) or ""
    for line in out.split("\n"):
        if name.lower() not in line.lower():
            continue
        m = re.search(r"(\d+)\s*%", line)
        if not m:
            continue
        w = re.search(r"\b(\d+[hdw])\b", line)
        r = re.search(r"resets?\s+(?:in\s+)?([0-9hms ]+)", line)
        secs = 0
        if r:
            for n, u in re.findall(r"(\d+)\s*([hms])", r.group(1)):
                secs += int(n) * {"h": 3600, "m": 60, "s": 1}[u]
        return {"pct": int(m.group(1)), "window": w.group(1) if w else "?",
                "resets_in": r.group(1).strip() if r else "?", "resets_s": secs,
                "raw": line.strip()}
    return None


# ---- quota rotation is asked of keyflip, never written into config (#32) -----------------

def provider_of(argv):
    """The keyflip provider an actor's argv spends, as its adapter declares it (`quota.provider`), or None."""
    if not argv or not isinstance(argv[0], str):
        return None
    program = _program_name(argv[0])
    for ident, adapter in sorted(package_adapters().items()):
        provider = (adapter.get("quota") or {}).get("provider")
        if provider and program in {_program_name(name) for name in [ident, *adapter_binaries(adapter)]}:
            return provider
    return None


def rotate_if_exhausted(cfg, argv, who):
    """Before an actor starts: rotate its provider's account through keyflip when the window is spent (#32).

    2026-09-07: kiro-cli hit its overage limit and every nudge failed silently for
    an hour, and the reviewer shared one Claude window with the architect. Account
    routing is keyflip's; ao only asks. keyflip.rotation is off unless a person
    turns it on, because a rotation is machine-wide and in place: it moves every
    session on that provider, so rotations are serialised under one machine lock
    and a window another actor already rotated is not rotated again. Returns
    {"ok", "provider", "rotated", "text"}; not ok means no account has headroom,
    and the caller surfaces that instead of spending the attempt.
    """
    provider = provider_of(argv)
    if settings.get(cfg, "keyflip.rotation") != "on" or not provider:
        return {"ok": True, "provider": provider, "rotated": False, "text": "rotation off"}
    ceiling = settings.get(None, "quota.block_percent")
    window = provider_window(provider)
    if not window or window["pct"] < ceiling:
        return {"ok": True, "provider": provider, "rotated": False, "text": "headroom"}
    from .storage import _exclusive_lock
    os.makedirs(os.path.join(HOME, ".ao"), exist_ok=True)
    with _exclusive_lock(os.path.join(HOME, ".ao", "keyflip-rotation.lock"), timeout=180):
        window = provider_window(provider)
        if window and window["pct"] < ceiling:
            return {"ok": True, "provider": provider, "rotated": False, "text": "another actor rotated first"}
        try:
            subprocess.run(["keyflip", "next", "--strategy", "best"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except Exception as exc:                      # a rotation that cannot run is not a headroom
            return {"ok": False, "provider": provider, "rotated": False,
                    "text": f"{provider} window {window['pct']}% used and keyflip could not rotate: {exc}"}
        after = provider_window(provider)
    if after and after["pct"] < ceiling:
        return {"ok": True, "provider": provider, "rotated": True,
                "text": f"rotated through keyflip for the {who}: {provider} now {after['pct']}% used"}
    return {"ok": False, "provider": provider, "rotated": True,
            "text": f"no {provider} account has headroom after rotating for the {who}"}


def fanout_history(root, limit=20):
    p = os.path.join(root, ".ao", "ledger", "fanouts.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-limit:]


def record_fanout(root, agents, done=None, errors=None, tokens=None, note=None):
    """What a fan-out actually cost. The next verdict is estimated from this."""
    d = os.path.join(root, ".ao", "ledger")
    os.makedirs(d, exist_ok=True)
    rec = {"at": int(time.time()), "agents": int(agents)}
    if done is not None:
        rec["done"] = int(done)
    if errors is not None:
        rec["errors"] = int(errors)
    if tokens is not None:
        rec["tokens"] = int(tokens)
    if note:
        rec["note"] = note
    rec["limit_hit"] = bool(rec.get("errors")) and bool(
        re.search(r"limit|quota|429|rate", note or "", re.I))
    with open(os.path.join(d, "fanouts.jsonl"), "a", encoding=UTF8) as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def observed_per_agent_tokens(root):
    """Average tokens per agent over recorded fan-outs (errored agents spent too)."""
    tok = n = 0
    for r in fanout_history(root, 50):
        if r.get("tokens") and (r.get("done") or r.get("errors")):
            tok += r["tokens"]
            n += (r.get("done") or 0) + (r.get("errors") or 0)
    return int(tok / n) if n else None


def fanout_verdict(root, cfg, agents, per_agent_tokens=None, provider=None):
    """May a fan-out of this size start now?

    Three checks, each a fact the caller can see: the project's hard cap, whether
    a fan-out already hit this provider's limit inside the current window, and
    how much of the window keyflip says is left. The token estimate is shown, not
    gated on — a percentage window cannot be converted to tokens honestly — but
    it becomes empirical after the first recorded run.
    """
    fc = fanout_config(cfg)
    observed = observed_per_agent_tokens(root)
    per = per_agent_tokens or observed or fc["per_agent_tokens"]
    provider = provider or provider_of((cfg.get("architect") or {}).get("argv"))
    win = provider_window(provider) if provider else None
    reasons, verdict = [], "ok"
    if agents > fc["max_agents"]:
        verdict = "too-many"
        reasons.append(f"{agents} agents > max_agents {fc['max_agents']} — run in batches of "
                       f"{fc['max_agents']} and record each")
    now = time.time()
    span = 5 * 3600
    if win and win.get("window", "").endswith("h"):
        try:
            span = int(win["window"][:-1]) * 3600
        except ValueError:
            pass
    window_start = now - (span - win["resets_s"]) if win and win.get("resets_s") else now - span
    for r in reversed(fanout_history(root, 10)):
        if r.get("limit_hit") and r["at"] >= window_start:
            if verdict == "ok":
                verdict = "limit-hit-recently"
            reasons.append(f"a fan-out of {r['agents']} hit the provider limit "
                           f"{int((now - r['at']) / 60)}m ago ({r.get('errors')} errors); "
                           f"wait for the window to reset"
                           + (f" (in {win['resets_in']})" if win else ""))
            break
    if win:
        left = 100 - win["pct"]
        if left < fc["window_reserve_pct"]:
            if verdict == "ok":
                verdict = "window-low"
            reasons.append(f"{provider} window {win['pct']}% used, {left}% left < reserve "
                           f"{fc['window_reserve_pct']}%; resets in {win['resets_in']}")
    else:
        reasons.append(f"{provider} window unreadable (keyflip absent or no line) — "
                       f"hard cap and history only")
    spent = sum(r.get("tokens") or 0 for r in fanout_history(root, 50) if r["at"] >= window_start)
    return {"verdict": verdict, "ok": verdict == "ok", "agents": agents,
            "per_agent_tokens": per,
            "per_agent_source": "arg" if per_agent_tokens else ("observed" if observed else "default"),
            "estimated_tokens": agents * per, "spent_this_window": spent,
            "window": win, "max_agents": fc["max_agents"], "reasons": reasons}



# ---- binaries ----------------------------------------------------------------
#
# The architect was woken forty times in eleven hours and every wake died with
# "Claude Code 2.1.185 does not support this model". The binary was real, on
# PATH, and two hundred versions stale — an npm-global leftover in /usr/local
# whose node had long since moved under a version manager, where a current
# copy sat unused. `which` answers "the first one", and the first one is the
# wrong question. Ask "the newest one" and remember what it said.

# Where a harness installs itself outside the usual directories is its adapter's to declare
# (`detect.install_dirs`, #76), placed where such a directory always stood in the search.
_BIN_DIRS = (("~/.local/bin", "~/bin", "/usr/local/bin", "/opt/homebrew/bin")
             + tuple(sorted({d for adapter in package_adapters().values()
                             for d in (adapter.get("detect") or {}).get("install_dirs") or []}))
             + ("~/.npm-global/bin", "~/.volta/bin", "~/.asdf/shims"))
_BIN_GLOBS = ("~/.local/share/fnm/node-versions/*/installation/bin",
              "~/.fnm/node-versions/*/installation/bin",
              "~/.nvm/versions/node/*/bin", "~/.local/share/mise/installs/node/*/bin")


def binary_candidates(name, path=None):
    """Every executable called `name` this machine has, PATH first, deduplicated."""
    import glob as _glob
    dirs = [d for d in (path or os.environ.get("PATH", "")).split(os.pathsep) if d]
    dirs += [os.path.expanduser(d) for d in settings.get(None, "binaries.extra_dirs")]
    dirs += [os.path.expanduser(d) for d in _BIN_DIRS]
    for g in _BIN_GLOBS:
        dirs += sorted(_glob.glob(os.path.expanduser(g)), reverse=True)
    exts = [""] + (os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";") if os.name == "nt" else [])
    seen, out = set(), []
    for d in dirs:
        for ext in exts:
            cand = os.path.join(d, name + ext.lower()) if ext else os.path.join(d, name)
            if not (os.path.isfile(cand) and os.access(cand, os.X_OK)):
                continue
            real = os.path.realpath(cand)
            if real in seen:
                continue
            seen.add(real)
            out.append(cand)
    return out


def binary_version(path):
    """`path --version`, cached by (path, mtime) so a wake does not pay for it twice."""
    cache_p = os.path.join(HOME, ".ao", "binaries.json")
    try:
        cache = json.load(open(cache_p, encoding=UTF8))
    except (OSError, ValueError):
        cache = {}
    try:
        mtime = os.path.getmtime(os.path.realpath(path))
    except OSError:
        return ""
    ent = cache.get(path)
    if ent and ent.get("mtime") == mtime:
        return ent.get("version", "")
    out = ""
    try:
        # cwd=HOME: a version probe started from inside a repository would inherit
        # that cwd and read, for one cycle, as a turn running in it.
        r = subprocess.run([path, "--version"], capture_output=True, text=True, encoding=UTF8, errors="replace", timeout=25, cwd=HOME,
                           env=dict(os.environ, PATH=os.environ.get("PATH", "") + ":" +
                                    os.path.dirname(os.path.realpath(path)) + ":" + os.path.dirname(path)))
        m = re.search(r"(\d+\.\d+\.\d+)", (r.stdout or "") + (r.stderr or ""))
        out = m.group(1) if m else ""
    except Exception:
        out = ""
    cache[path] = {"mtime": mtime, "version": out, "at": int(time.time())}
    try:
        os.makedirs(os.path.dirname(cache_p), exist_ok=True)
        json.dump(cache, open(cache_p, "w", encoding=UTF8))
    except OSError:
        pass
    return out


def _vtuple(v):
    return tuple(int(x) for x in v.split(".")) if v else (0,)


def resolve_binary(name, path=None):
    """(path, version) of the newest `name` on this machine; (None, "") if none.

    An absolute path is returned as-is with its version. Ties keep PATH order.
    """
    if os.path.isabs(name):
        return (name if os.access(name, os.X_OK) else None), binary_version(name) if os.path.exists(name) else ""
    best = (None, "")
    for cand in binary_candidates(name, path):
        v = binary_version(cand)
        if best[0] is None or _vtuple(v) > _vtuple(best[1]):
            best = (cand, v)
    return best


# ---- implementer reports ------------------------------------------------------

def _report_summary(path):
    try:
        for line in open(path, errors="replace", encoding=UTF8):
            if line.startswith("#"):
                return line.lstrip("# ").strip()
    except OSError:
        pass
    return ""


def bump_repeat(path):
    """Fold a repeated report into the standing one; keep its mtime (its age is the fact)."""
    try:
        st = os.stat(path)
        body = open(path, errors="replace", encoding=UTF8).read()
    except OSError:
        return 0
    m = re.search(r"^Tekrar: (\d+)", body, re.M)
    n = int(m.group(1)) + 1 if m else 2
    line = f"Tekrar: {n} · son: {time.strftime('%Y-%m-%d %H:%M')}"
    body = re.sub(r"^Tekrar: .*$", line, body, flags=re.M) if m else body.rstrip("\n") + "\n\n" + line + "\n"
    try:
        open(path, "w", encoding=UTF8).write(body)
        os.utime(path, (st.st_atime, st.st_mtime))
    except OSError:
        pass
    return n


def _name_time(name):
    m = re.match(r"(\d{8})-(\d{4})", name)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M"))
    except ValueError:
        return None


def implementer_inbox(root, cfg):
    """Mail addressed to the implementer: not its own reports, not the watchdog's."""
    box = cfg.get("mailbox", "agent-mail")
    return [m for m in mailbox(root, box) if not to_architect(m, cfg) and not from_watchdog(m)]


def product_dirty(root, cfg):
    """Uncommitted paths outside the coordination directories."""
    out = []
    # Not sh(): it strips its output, and the first line's leading status column
    # went with it - " M .ao/ledger/notices.jsonl" became "M .ao/...", the path
    # lost its first character, and a coordination file read as a product change.
    # A failure still reads as no uncommitted paths, as it did through sh(); no
    # caller decides authority from this.
    try:
        status = _git_output(root, "status", "--porcelain").decode(UTF8, "replace")
    except Exception:
        status = ""
    for line in status.split("\n"):
        if not line.strip():
            continue
        raw = line[3:].strip().strip('"')
        paths = [part.strip().strip('"') for part in raw.split(" -> ")]
        # A rename crossing the boundary is a product change; ignore it only
        # when both its old and new names are coordination state.
        if paths and all(_is_coordination_path(path, cfg) for path in paths):
            continue
        out.append(line)
    return out


def waiting_on_architect(root, cfg):
    """The implementer's standing request the architect has not answered.

    Returns (report, asked_at) when the newest implementer report asks for a
    decision, nothing addressed to the implementer arrived after it, and the
    board has nothing queued. Nudging in that state produces reports, not
    progress: eighty of them, once, at eight-minute intervals.
    """
    box = cfg.get("mailbox", "agent-mail")
    files = mailbox(root, box)
    reports = [m for m in files if to_architect(m, cfg) and not from_watchdog(m)
               and not from_architect(m, cfg)]
    if not reports:
        return None
    latest = reports[-1]
    p = os.path.join(root, box, latest)
    try:
        low = open(p, errors="replace", encoding=UTF8).read(4000).lower()
    except OSError:
        return None
    if not any(h in low for h in ("## karar gerekli", "## acil", "## decision required",
                                   "## urgent", "## blocked")):
        return None
    # Delivery is by deletion, so any file addressed to the implementer is
    # unread — whatever its timestamp. An answer written while the implementer
    # was repeating its request is older than the repeat and still the answer.
    if implementer_inbox(root, cfg):
        return None
    if board(root)["queued"]:
        return None
    return latest, _name_time(latest) or os.path.getmtime(p)


# ---- mail envelope, names, ledger --------------------------------------------

def mail_names(cfg):
    """(implementer, architect) mail names; defaults keep the historical files valid."""
    impl = (settings.get(cfg, "implementer.name") or implementer_actor_name(cfg)).strip()
    arch = settings.get(cfg, "architect.name").strip()
    return impl, arch


def to_architect(name, cfg):
    _, arch = mail_names(cfg)
    return f"-to-{arch}-" in name or "-to-architect-" in name


def from_architect(name, cfg):
    """Whether a mail was written by the architect, read from its sender field (#18)."""
    _, arch = mail_names(cfg)
    found = re.match(r"^\d{8}-\d{4}-(.+?)-to-", name)
    return bool(found) and found.group(1).lower() in {arch.lower(), "architect"}


def notice_recently_recorded(root, key, window):
    """Was this key recorded inside the window at all, delivered or held (#69)?

    An architect-audience notice is recorded unsent by design, so a check on sent
    rows never held for one, and the same anomaly wrote a row every cycle.
    """
    p = os.path.join(root, ".ao", "ledger", "notices.jsonl")
    if not os.path.exists(p):
        return False
    cutoff = time.time() - window
    try:
        with open(p, errors="replace", encoding=UTF8) as fh:
            fh.seek(max(0, os.path.getsize(p) - 100_000))
            lines = fh.read().split("\n")
    except OSError:
        return False
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("at", 0) < cutoff:
            return False
        if rec.get("key") == key:
            return True
    return False


SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9_-]{16,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}",
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    r"[A-Za-z0-9+_=-]{40,}",
)


# What ao writes into a repository is scanned for credentials first (#48). Named rules,
# so a redaction says what it removed; hex digests (commit ids, sha256 values) match
# none of them, which is why the generic long-token rule `redact` uses is not here.
EVIDENCE_RULES = (
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("anthropic-key", r"sk-ant-[A-Za-z0-9_-]{16,}"),
    ("openai-key", r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    ("github-token", r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("slack-token", r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    ("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ("bearer-token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    ("assigned-secret", r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token)\b[\"']?\s*[:=]\s*[\"']?"
                        r"[A-Za-z0-9+/_.=-]{12,}"),
)


def scan_evidence(text):
    """(text with every credential-shaped string replaced by `[redacted:<rule>]`, the rules that hit)."""
    out = str(text if text is not None else "")
    hits = []
    for rule, pattern in EVIDENCE_RULES:
        out, count = re.subn(pattern, f"[redacted:{rule}]", out)
        if count:
            hits.append(rule)
    return out, hits


def scan_record(value):
    """A JSON-shaped value with every string in it scanned (#48)."""
    if isinstance(value, str):
        return scan_evidence(value)[0]
    if isinstance(value, dict):
        return {key: scan_record(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scan_record(item) for item in value]
    return value


def redact(text):
    """Text from an agent's own output with anything token-shaped masked (#69).

    A wake or nudge log merges a model's prose with its process's errors, and a
    line of it went to the committed notices ledger, Telegram and e-mail. What
    leaves the log is masked first.
    """
    out = str(text or "")
    for pattern in SECRET_PATTERNS:
        out = re.sub(pattern, "[redacted]", out)
    return out


def record_actor_flags(root, actor, flags, reason):
    """Record a flag ao adds to an actor's argv, with why, each time that changes (#69)."""
    from .storage import append_jsonl, read_jsonl
    path = os.path.join(root, ".ao", "ledger", "actor-flags.jsonl")
    try:
        rows = read_jsonl(path)
    except Exception:
        rows = []
    last = next((row for row in reversed(rows)
                 if isinstance(row, dict) and row.get("actor") == actor), None)
    if last and last.get("flags") == list(flags) and last.get("reason") == reason:
        return last
    return append_jsonl(path, {"at": int(time.time()), "actor": actor,
                               "flags": list(flags), "reason": reason})


def from_watchdog(name):
    return name.startswith("watchdog-to-") or "-watchdog-to-" in name


def write_mail(root, cfg, name, body, meta=None):
    """Every mail ao itself writes goes through here: envelope first, prose after.

    The body stays Markdown — agents and people read it. The envelope is a small
    front-matter block a program can read without parsing prose: kind, from, to,
    slice, time, id. And every write is mirrored to `.ao/ledger/mail.jsonl`, so
    that delivery-by-deletion stops deleting the record: a mailbox that was
    emptied is still searchable, and "how long did that request stand" has an
    answer after the file is gone.
    """
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    os.makedirs(box, exist_ok=True)
    meta = dict(meta or {})
    meta.setdefault("ao", 1)
    meta.setdefault("id", name)
    meta.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    # The address is the role; the name beside it is display (#31).
    meta.setdefault("from_role", role_of(meta.get("from"), cfg))
    meta.setdefault("to_role", role_of(meta.get("to"), cfg))
    order = ["ao", "id", "kind", "from", "from_role", "to", "to_role", "slice", "at"]
    keys = [k for k in order if k in meta] + [k for k in meta if k not in order]
    meta = scan_record(meta)
    body = scan_evidence(body)[0]                     # scanned before it is written (#48)
    head = "---\n" + "".join(f"{k}: {meta[k]}\n" for k in keys if meta[k] not in (None, "")) + "---\n"
    with open(os.path.join(box, name), "w", encoding=UTF8) as fh:
        fh.write(head + body.lstrip("\n"))
    summary = next((l.lstrip("# ").strip() for l in body.split("\n") if l.startswith("#")), "")
    mail_ledger_append(root, {"event": "written", "id": name, "kind": meta.get("kind"),
                              "from": meta.get("from"), "to": meta.get("to"),
                              "slice": meta.get("slice"), "summary": summary[:200]})
    return name


def mail_meta(path):
    """The envelope of a mail file, or {} for a file written without one."""
    try:
        head = open(path, errors="replace", encoding=UTF8).read(2000)
    except OSError:
        return {}
    if not head.startswith("---\n"):
        return {}
    end = head.find("\n---", 4)
    if end < 0:
        return {}
    out = {}
    for line in head[4:end].split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


# ---- unread age escalates on the ladder (#30) --------------------------------------------

MAIL_CLASSES = ("fyi", "needs-read", "needs-decision", "urgent")
_DECISION_KINDS = ("decision", "decision-request", "blocked", "karar", "question", "escalation")
_FYI_KINDS = ("done", "rapor", "report", "fyi", "info", "status", "note")


def mail_class(name, meta=None, body=""):
    """What a message asks of whoever reads it: fyi, needs-read, needs-decision or urgent (#30).

    Declared in the envelope (`class:`) when the writer said; otherwise read from
    its kind and its headings, the way the watchdog already reads a request.
    """
    meta = meta or {}
    declared = str(meta.get("class") or "").strip().lower()
    if declared in MAIL_CLASSES:
        return declared
    kind = str(meta.get("kind") or "").strip().lower()
    if not kind:
        found = re.match(r"^\d{8}-\d{4}-.+?-to-.+?-([a-z]+)-", name, re.I)
        kind = found.group(1).lower() if found else ""
    low = str(body or "").lower()
    if kind in _DECISION_KINDS or any(mark in low for mark in ("## karar gerekli", "## decision required")):
        return "needs-decision"
    return "fyi" if kind in _FYI_KINDS else "needs-read"


def addressed_to(name, cfg, role):
    """Whether a message is for a role: the architect's inbox, or the implementer's; a person reads both."""
    if role == "architect":
        return to_architect(name, cfg)
    if role == "implementer":
        return not to_architect(name, cfg) and not from_watchdog(name)
    return True


def mail_seen(root, names, by):
    """Record, once each, that messages were put in front of their reader (#30).

    Seeing is not handling: handling stays deletion, and a message seen and not
    yet handled is still in the mailbox. What this separates is "nobody has been
    shown it" from "someone is working on it".
    """
    already = {row.get("id") for row in mail_log(root, 5000) if row.get("event") == "seen"}
    fresh = [name for name in names if name not in already]
    for name in fresh:
        mail_ledger_append(root, {"event": "seen", "id": name, "by": by})
    return fresh


def unseen_messages(root, cfg):
    """Messages in the mailbox nobody has been shown, oldest first, with their class and age (#30)."""
    box = cfg.get("mailbox", "agent-mail")
    rows = mail_log(root, 5000)
    seen = {row.get("id") for row in rows if row.get("event") == "seen"}
    written = {row.get("id"): row.get("at") for row in rows if row.get("event") == "written"}
    out = []
    for name in mailbox(root, box):
        if name in seen or from_watchdog(name):
            continue
        path = os.path.join(root, box, name)
        try:
            with open(path, errors="replace", encoding=UTF8) as fh:
                body = fh.read(4000)
            at = float(written.get(name) or os.path.getmtime(path))
        except (OSError, TypeError, ValueError):
            continue
        out.append({"id": name, "class": mail_class(name, mail_meta(path), body), "at": at,
                    "age": max(0.0, time.time() - at)})
    return sorted(out, key=lambda message: message["at"])


# ---- nothing is deleted to prove it was handled (#80) ------------------------------------

MAIL_STORE_CHAIN = "ao-mail-store-row-v1"


def mail_store_mode(root):
    """`append-only` when the project has switched its mailbox to the store, else `deletion` (#80)."""
    try:
        return settings.get(load_config(root), "mail.store")
    except Exception:
        return "deletion"


def mail_store_rows(root):
    from .storage import read_chained_jsonl
    return [row for row in read_chained_jsonl(os.path.join(root, ".ao", "ledger", "mail-store.jsonl"), MAIL_STORE_CHAIN)
            if isinstance(row, dict)]


def _mail_store_append(root, row):
    from .storage import append_chained_jsonl
    return append_chained_jsonl(os.path.join(root, ".ao", "ledger", "mail-store.jsonl"),
                                scan_record(dict(row, at=int(time.time()))), MAIL_STORE_CHAIN)


def _store_path(root, mid):
    return os.path.join(root, ".ao", "mail", "store", os.path.basename(mid))


def ingest_mail(root, cfg):
    """Take every message in the mailbox view into the append-only store, once (#80)."""
    from .storage import replace_file_durably
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    known = {row.get("id") for row in mail_store_rows(root) if row.get("event") == "message"}
    taken = []
    for name in (sorted(os.listdir(box)) if os.path.isdir(box) else []):
        if name == "README.md" or not name.endswith(".md") or name in known:
            continue
        with open(os.path.join(box, name), "rb") as fh:
            data = fh.read()
        replace_file_durably(_store_path(root, name), data)
        meta = mail_meta(os.path.join(box, name))
        _mail_store_append(root, {"event": "message", "id": name,
                                  "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                                  "from": meta.get("from"), "to": meta.get("to"), "kind": meta.get("kind")})
        taken.append(name)
    return taken


def unhandled_messages(root):
    """The unhandled queue, derived: every stored message with no handling record (#80)."""
    rows = mail_store_rows(root)
    handled = {row.get("id") for row in rows if row.get("event") == "handled"}
    return sorted({row.get("id") for row in rows if row.get("event") == "message"} - handled)


def handle_message(root, cfg, mid, by, outcome):
    """Record that a message was handled, by whom and how; its view file then goes, its record never (#80)."""
    if mid not in unhandled_messages(root):
        return False
    _mail_store_append(root, {"event": "handled", "id": mid, "by": by, "outcome": outcome})
    view = os.path.join(root, cfg.get("mailbox", "agent-mail"), mid)
    try:
        os.remove(view)
    except FileNotFoundError:
        pass
    return True


def message_body(root, mid):
    """A stored message's bytes, read through a compaction stub when it has one (#80)."""
    import gzip
    try:
        with open(_store_path(root, mid), "rb") as fh:
            data = fh.read()
    except OSError:
        data = None
    if data is not None and data.startswith(b"ao-mail-stub v1\n"):
        archive = data.decode(UTF8).split("archive: ", 1)[1].strip()
        with gzip.open(os.path.join(root, archive), "rb") as fh:
            return fh.read()
    return data


def sync_mail_view(root, cfg):
    """Make the mailbox view the derived queue: restore what was removed unhandled, drop what was handled (#80).

    An agent may delete a view file; that removes nothing. The message comes back
    until someone records handling it, because the actor deciding what was handled
    must not also be able to erase the question.
    """
    from .storage import replace_file_durably
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    queue = set(unhandled_messages(root))
    restored, dropped = [], []
    for mid in sorted(queue):
        view = os.path.join(box, mid)
        if not os.path.exists(view):
            body = message_body(root, mid)
            if body is not None:
                replace_file_durably(view, body)
                restored.append(mid)
    stored = {row.get("id") for row in mail_store_rows(root) if row.get("event") == "message"}
    for name in (sorted(os.listdir(box)) if os.path.isdir(box) else []):
        if name in stored and name not in queue:
            os.remove(os.path.join(box, name))
            dropped.append(name)
    return restored, dropped


def compact_messages(root, days, now=None):
    """Collapse stored bodies older than `days` to a stub with their digest and an archive pointer (#80)."""
    import gzip
    from .storage import replace_file_durably
    cutoff = (time.time() if now is None else now) - days * 86400
    compacted = []
    done = {row.get("id") for row in mail_store_rows(root) if row.get("event") == "compacted"}
    for row in mail_store_rows(root):
        mid = row.get("id")
        if row.get("event") != "message" or mid in done or float(row.get("at") or 0) > cutoff:
            continue
        path = _store_path(root, mid)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        archive = f".ao/mail/archive/{mid}.gz"
        os.makedirs(os.path.dirname(os.path.join(root, archive)), exist_ok=True)
        with gzip.open(os.path.join(root, archive), "wb") as fh:
            fh.write(data)
        replace_file_durably(path, f"ao-mail-stub v1\ndigest: {row.get('digest')}\narchive: {archive}\n".encode(UTF8))
        _mail_store_append(root, {"event": "compacted", "id": mid, "archive": archive, "digest": row.get("digest")})
        compacted.append(mid)
    return compacted


def room_search(text, root=None, limit=20):
    """Stored messages in every registered project whose body mentions `text` (#80)."""
    needle = str(text or "").lower()
    found = []
    for project, path in recall_roots(root):
        try:
            rows = [row for row in mail_store_rows(path) if row.get("event") == "message"]
        except Exception:
            continue
        for row in rows:
            body = message_body(path, row["id"])
            if body is not None and needle in body.decode(UTF8, "replace").lower():
                found.append(dict(row, project=project))
    found.sort(key=lambda row: -float(row.get("at") or 0))
    return found[:limit]


# ---- the message store syncs to one private repository, never the product's remote (#83) --

def _url_is_private(root, url):
    """True for a local path, or when the host says the repository is private; None when it cannot say (#83)."""
    import shutil
    if url and not re.match(r"^[a-z][a-z0-9+.-]*://|^[^/]+@[^:]+:", url):
        return True                              # a directory on this machine publishes nothing
    found = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url or "")
    if not found or not shutil.which("gh"):
        return None
    try:
        answer = subprocess.run(["gh", "api", f"repos/{found.group(1)}/{found.group(2)}", "--jq", ".private"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {"true": True, "false": False}.get(answer.stdout.strip())


def mail_ref_commit(root):
    """Commit the message store under refs/ao/mail, scanned, parented on the last one; returns the commit (#83)."""
    store = os.path.join(root, ".ao", "mail")
    ledger = os.path.join(root, ".ao", "ledger", "mail-store.jsonl")
    entries = {}
    for directory, _, files in os.walk(store):
        for name in files:
            rel = os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/")
            with open(os.path.join(root, rel), "rb") as fh:
                data = fh.read()
            if not name.endswith(".gz"):
                data = scan_evidence(data.decode(UTF8, "replace"))[0].encode(UTF8)
            entries[rel] = data
    if os.path.exists(ledger):
        with open(ledger, encoding=UTF8) as fh:
            entries[".ao/ledger/mail-store.jsonl"] = scan_evidence(fh.read())[0].encode(UTF8)
    tree = {}
    for rel, data in sorted(entries.items()):
        blob = subprocess.run([git_binary(), "hash-object", "-w", "--stdin"], cwd=root, input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()
        node = tree
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = blob

    def write(node):
        lines = [f"040000 tree {write(node[name])}\t{name}" if isinstance(node[name], dict)
                 else f"100644 blob {node[name]}\t{name}" for name in sorted(node)]
        return subprocess.run([git_binary(), "mktree"], cwd=root, input=("\n".join(lines) + "\n").encode(UTF8),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode().strip()

    parent = git_text(root, "rev-parse", "--verify", "--quiet", "refs/ao/mail")
    tree_id = write(tree)
    if parent and git_text(root, "rev-parse", f"{parent}^{{tree}}") == tree_id:
        return parent
    argv = [git_binary(), "-c", "user.name=ao", "-c", "user.email=ao@localhost", "commit-tree", tree_id,
            "-m", f"ao mail store of {project_key(root)}"]
    if parent:
        argv += ["-p", parent]
    commit = subprocess.run(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=True).stdout.decode().strip()
    _git_output(root, "update-ref", "refs/ao/mail", commit)
    return commit


def sync_mail(root, cfg):
    """Push the store's ref to the one private repository named in mail.sync_repo, as refs/mail/<project> (#83).

    The product's own remote is never a target, and a repository the host does not
    confirm private is refused, named and logged. Every record is scanned before it
    leaves. Returns (commit, where).
    """
    target = (settings.get(cfg, "mail.sync_repo") or "").strip()
    if not target:
        raise RuntimeError("no mail.sync_repo is named; syncing is opt-in per project")
    origin = git_text(root, "remote", "get-url", "origin")
    if origin and target.rstrip("/").removesuffix(".git") == origin.rstrip("/").removesuffix(".git"):
        raise RuntimeError("mail.sync_repo is this product's own remote; mail never goes there")
    private = _url_is_private(root, target)
    if private is not True:
        record_notice(root, "mail sync refused", f"{target} was not confirmed private", sent=False, key="mail-sync-refused")
        raise RuntimeError(f"not syncing mail to {target}: the host did not confirm it is private")
    commit = mail_ref_commit(root)
    ref = f"refs/mail/{project_key(root)}"
    _git_output(root, "push", target, f"refs/ao/mail:{ref}", timeout=300)
    return commit, f"{target} {ref}"


def mail_sync_state(root, cfg):
    """(local commit, remote commit or None, problem or None) for `ao doctor` (#83)."""
    target = (settings.get(cfg, "mail.sync_repo") or "").strip()
    if not target:
        return None
    local = git_text(root, "rev-parse", "--verify", "--quiet", "refs/ao/mail") or None
    remote = (git_text(root, "ls-remote", target, f"refs/mail/{project_key(root)}").split() or [None])[0]
    problem = None if _url_is_private(root, target) is True else f"{target} could not be verified private"
    return local, remote, problem


def mail_ledger_append(root, row):
    d = os.path.join(root, ".ao", "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        row = dict(row)
        row.setdefault("at", int(time.time()))
        with open(os.path.join(d, "mail.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def mail_log(root, limit=200):
    p = os.path.join(root, ".ao", "ledger", "mail.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-limit:]


def reconcile_mail_ledger(root, cfg):
    """Mail the ledger saw written and the mailbox no longer has was consumed.

    The architect and the implementer delete by hand; nothing hooks `rm`. So
    the watchdog closes the loop each cycle: an open id that is not on disk gets
    a `consumed` row, timed now — a lower bound on when it was read.
    """
    if mail_store_mode(root) == "append-only":
        # Nothing is consumed by vanishing: take new mail in, and put the view back in line (#80).
        ingest_mail(root, cfg)
        sync_mail_view(root, cfg)
        return []
    box = os.path.join(root, cfg.get("mailbox", "agent-mail"))
    rows = mail_log(root, 5000)
    open_ids = {}
    for r in rows:
        if r.get("event") == "written":
            open_ids[r["id"]] = r
        elif r.get("event") == "consumed":
            open_ids.pop(r["id"], None)
    closed = []
    for mid, r in open_ids.items():
        if not os.path.exists(os.path.join(box, mid)):
            mail_ledger_append(root, {"event": "consumed", "id": mid, "kind": r.get("kind"),
                                      "from": r.get("from"), "to": r.get("to"),
                                      "stood_s": int(time.time()) - int(r.get("at") or time.time())})
            closed.append(mid)
    return closed


def mail_search(root, text, limit=50):
    """Ledger rows and live files whose id, summary or body mention `text`."""
    t = text.lower()
    out, seen = [], set()
    for r in reversed(mail_log(root, 5000)):
        if r.get("event") != "written":
            continue
        hay = " ".join(str(r.get(k) or "") for k in ("id", "summary", "kind", "slice")).lower()
        if t in hay and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
        if len(out) >= limit:
            return out
    return out


# ---- alarms -------------------------------------------------------------------
#
# Three levels, named by who acts: yellow is the architect's (an anomaly and a
# wake), orange is the human's (desktop + Telegram), red is the human's *now*
# (e-mail). The ladder is what turns a standing orange into a red: on 2026-09-05
# every alert went to a notification centre nobody looked at for eleven hours.

# The defaults, from the settings registry (#74); the functions below read the values in force.
ALARM_RED_AFTER = settings.default("alarms.red_after_minutes") * 60
ALARM_RED_REPEAT = settings.default("alarms.red_repeat_hours") * 3600
ALARM_RESET_AFTER = settings.default("alarms.reset_after_hours") * 3600


def _alarm_reset_after():
    return settings.get(None, "alarms.reset_after_hours") * 3600


def alarms_path():
    return os.path.join(HOME, ".ao", "alarms.json")


def load_alarms():
    try:
        return json.load(open(alarms_path(), encoding=UTF8))
    except (OSError, ValueError):
        return {}


def save_alarms(d):
    try:
        os.makedirs(os.path.dirname(alarms_path()), exist_ok=True)
        json.dump(d, open(alarms_path(), "w", encoding=UTF8), indent=1)
    except OSError:
        pass


def alarm_snoozes_path():
    return os.path.join(HOME, ".ao", "alarm-snoozes.json")


def load_alarm_snoozes():
    try:
        return json.load(open(alarm_snoozes_path(), encoding=UTF8))
    except (OSError, ValueError):
        return {}


def _save_alarm_snoozes(d):
    os.makedirs(os.path.dirname(alarm_snoozes_path()), exist_ok=True)
    with open(alarm_snoozes_path(), "w", encoding=UTF8) as fh:
        json.dump(d, fh, indent=1)


def alarm_snooze(project, key, until, by="human", why=""):
    """Keep one alarm off the human channels until a date; it stays on the record.

    A snooze is for a condition that is real, known and waiting on someone. On
    2026-09-15 the doctor check reported Voltrai's legacy commit hook, whose fix
    only the owner can make and not before 1 October; left alone it would ring red
    and mail every six hours about something nobody could act on yet. The snooze
    names who set it and why, and it ends by itself on the date.
    """
    d = load_alarm_snoozes()
    d[f"{project}:{key}"] = {"until": int(until), "by": by, "why": why, "at": int(time.time())}
    _save_alarm_snoozes(d)
    return d[f"{project}:{key}"]


def alarm_unsnooze(project, key):
    d = load_alarm_snoozes()
    gone = d.pop(f"{project}:{key}", None)
    if gone is not None:
        _save_alarm_snoozes(d)
    return gone


def alarm_snoozed(project, key, now=None):
    """The snooze standing for this alarm, or None; an expired snooze is no snooze."""
    entry = load_alarm_snoozes().get(f"{project}:{key}")
    if isinstance(entry, dict) and float(entry.get("until") or 0) > (now or time.time()):
        return entry
    return None


def alarm_touch(project, key, level, now=None, red_after=ALARM_RED_AFTER, title=None,
                persist=True, quiet_until=None, evidence=None):
    """Calculate a raise of `key` at `level`; return (level to ring at, episode).

    An orange raised repeatedly for `red_after` seconds rings red. `red_due` on
    the episode says whether a mail should go now (once per ALARM_RED_REPEAT).
    ``persist=False`` runs the identical calculation against the current ledger
    without writing it, so watchdog explain can preview the live verdict safely.
    """
    now = now or time.time()
    d = load_alarms()
    k = f"{project}:{key}"
    e = d.get(k) or {}
    if e and now - e.get("last", 0) > _alarm_reset_after():
        e = {}
    e.setdefault("first", now)
    e["last"] = now
    e["level"] = level
    e["count"] = e.get("count", 0) + 1
    if title:
        e["title"] = title
    if quiet_until:
        e["quiet_until"] = float(quiet_until)
    if evidence:
        e["evidence"] = evidence      # the ladder shows what the notice was raised on (#37)
    ring = level
    e["red_due"] = False
    if level == "red" or (level == "orange" and now - e["first"] >= red_after):
        ring = "red"
        e["red_due"] = e.get("red_sent") is None or now - e["red_sent"] >= settings.get(None, "alarms.red_repeat_hours") * 3600
        # A standing red with a known end is mailed once, then held until that end (#40).
        if e.get("red_sent") is not None and now < float(e.get("quiet_until") or 0):
            e["red_due"] = False
    e["ring"] = ring
    d[k] = e
    if persist:
        save_alarms(d)
    return ring, e


def alarm_mailed(project, key, now=None):
    d = load_alarms()
    k = f"{project}:{key}"
    if k in d:
        d[k]["red_sent"] = now or time.time()
        d[k]["red_due"] = False
        save_alarms(d)


def active_alarms(project=None, now=None):
    now = now or time.time()
    out = []
    for k, e in load_alarms().items():
        proj, _, key = k.partition(":")
        if project and proj != project:
            continue
        if now - e.get("last", 0) > _alarm_reset_after():
            continue
        out.append(dict(e, project=proj, key=key, age_s=int(now - e.get("first", now))))
    return sorted(out, key=lambda e: -e["age_s"])


# ---- heartbeat ------------------------------------------------------------------

# ---- one name for what a project keeps outside its tree (#66) -----------------------

PROJECT_KEY_FILE = "project-key"


def project_registry_path():
    """Which path holds which project name on this machine."""
    return os.environ.get("AO_PROJECT_REGISTRY") or os.path.join(HOME, ".ao", "projects.json")


def project_registry():
    try:
        with open(project_registry_path(), encoding=UTF8) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: row for name, row in data.items() if isinstance(row, dict) and row.get("root")}


def _usable_key(name):
    return (isinstance(name, str) and 0 < len(name) <= 200 and name not in (".", "..")
            and not any(ch in name for ch in "/\\\0\n\r"))


def project_key(root):
    """The name a project's files outside its tree are kept under (#66).

    Push windows, watchdog state and logs, heartbeats, locks, the reviewer and
    helper records and alarms were named by the directory's basename, so two
    checkouts called `api` shared them: a push allowed in one opened the other,
    and one project's watchdog state overwrote the other's.

    A project's name belongs to its resolved path in the machine registry. A new
    project takes its basename - the name its files already had, so an existing
    install keeps them - unless a path that is still an ao project holds that
    name, compared without case because launchd labels are lower-cased; then it
    takes the basename and eight hex digits of its resolved path. The name is
    also written to `.ao/project-key` with the path, so a lost registry gives
    each project its own name back and a copied `.ao/` does not claim another's.
    A directory with no `.ao/` is not an ao project: it gets its basename and
    nothing is written.
    """
    base = os.path.basename(os.path.abspath(root).rstrip("/\\")) or "root"
    real = os.path.realpath(root)
    if not os.path.isdir(os.path.join(real, ".ao")):
        return base
    mine = next((name for name, row in project_registry().items() if row.get("root") == real), None)
    if mine:
        return mine
    from .storage import _exclusive_lock, replace_file_durably
    registry = project_registry_path()
    recorded = os.path.join(real, ".ao", PROJECT_KEY_FILE)
    try:
        with _exclusive_lock(registry + ".lock"):
            known = project_registry()
            mine = next((name for name, row in known.items() if row.get("root") == real), None)
            if mine is None:
                held = {name.lower() for name, row in known.items()
                        if row.get("root") != real and os.path.isdir(os.path.join(row["root"], ".ao"))}
                try:
                    with open(recorded, encoding=UTF8) as fh:
                        mark = json.load(fh)
                except (OSError, ValueError):
                    mark = {}
                wanted = mark.get("key") if isinstance(mark, dict) and mark.get("root") == real else None
                if _usable_key(wanted) and wanted.lower() not in held:
                    mine = wanted
                elif base.lower() not in held:
                    mine = base
                else:
                    mine = f"{base}-{hashlib.sha256(real.encode('utf-8')).hexdigest()[:8]}"
                known = {name: row for name, row in known.items() if name.lower() != mine.lower()}
                known[mine] = {"root": real, "at": int(time.time())}
                replace_file_durably(registry, json.dumps(known, indent=1, sort_keys=True).encode("utf-8"))
            replace_file_durably(recorded, json.dumps({"key": mine, "root": real}).encode("utf-8"))
    except OSError:
        return mine or base
    return mine


def project_key_collisions():
    """Registered projects that share a directory name, and the name each is kept under."""
    groups = {}
    for name, row in sorted(project_registry().items()):
        base = os.path.basename(str(row["root"]).rstrip("/\\")).lower()
        groups.setdefault(base, []).append((name, row["root"]))
    return {base: rows for base, rows in groups.items() if len(rows) > 1}


def heartbeat_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"heartbeat-{key}")


def heartbeat(root):
    """The watchdog touching this each cycle is the only proof it is alive."""
    try:
        p = heartbeat_path(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding=UTF8) as fh:
            fh.write(str(int(time.time())))
    except OSError:
        pass


def heartbeat_age(root):
    try:
        return int(time.time() - os.path.getmtime(heartbeat_path(root)))
    except OSError:
        return None


def expire_alarms(project, now=None):
    """Episodes that went quiet are over; return them once and forget them."""
    now = now or time.time()
    d = load_alarms()
    done = []
    for k in list(d):
        proj, _, key = k.partition(":")
        e = d[k]
        if proj == project and now - e.get("last", 0) > _alarm_reset_after():
            done.append(dict(e, project=proj, key=key, age_s=int(e.get("last", now) - e.get("first", now))))
            del d[k]
    if done:
        save_alarms(d)
    return done


RETIRED_HEARTBEAT_AGE = settings.default("heartbeat.retired_days") * 86400


def stale_siblings(root, max_age=900):
    """Other projects whose watchdog once had a heartbeat and now has none.

    A dead watchdog cannot report itself; the ones next to it can. Only projects
    with a heartbeat file count — a project that never ran one is not late."""
    me = project_key(root)
    out = {}
    try:
        for f in os.listdir(os.path.join(HOME, ".ao")):
            if not f.startswith("heartbeat-") or f == f"heartbeat-{me}":
                continue
            age = int(time.time() - os.path.getmtime(os.path.join(HOME, ".ao", f)))
            # A week of silence is a project that was retired, not a watchdog that
            # just died; ringing a person about it every cycle never ended (audit).
            if max_age < age <= settings.get(None, "heartbeat.retired_days") * 86400:
                out[f[len("heartbeat-"):]] = age
    except OSError:
        pass
    return out


# ---- review scope ----------------------------------------------------------------

COORDINATION_DIRS = (".ao/", "agent-mail/")      # and each shipped harness's own, from harness_dirs()


def harness_dirs():
    """What the shipped harnesses keep in a repository (`detect.dirs`), each ending in a slash (#76)."""
    return tuple(sorted({str(d).strip("/") + "/" for adapter in package_adapters().values()
                         for d in (adapter.get("detect") or {}).get("dirs") or [] if str(d).strip("/")}))


def review_diff(root, cfg, paths=None, budget=1_500_000):
    """What the reviewer sees: the slice's changes, and nothing else.

    `git diff HEAD` carried every tracked change in the tree, so an unrelated
    steering edit polluted a review of a credential lifecycle; untracked files
    were capped at the first twenty, and thirty review artefacts consumed the
    cap before the one untracked file that mattered — the inventory under
    review — was reached. Two rounds of a five-round budget went to that.

    Coordination directories are never product. Untracked product files are
    included newest first within a byte budget. `paths` narrows both sides.
    """
    skip = _coordination_dirs(cfg)
    spec = " -- " + " ".join(f"'{p}'" for p in paths) if paths else ""
    diff = sh(f"git diff HEAD{spec}", cwd=root, timeout=60) or ""
    if not paths:
        parts = re.split(r"(?m)^(?=diff --git )", diff)
        diff = "".join(pt for pt in parts if not any(
            f" b/{d}" in pt.split("\n", 1)[0] for d in skip))
    untracked = [l[3:].strip().strip('"') for l in (sh("git status --porcelain", cwd=root) or "").split("\n")
                 if l.startswith("?? ")]
    untracked = [f for f in untracked if not _is_coordination_path(f, cfg)]
    if paths:
        untracked = [f for f in untracked if any(f == p or f.startswith(p.rstrip("/") + "/")
                                                  or p.startswith(f.rstrip("/") + "/") for p in paths)]
    files = []
    for f in untracked:
        p = os.path.join(root, f)
        if os.path.isdir(p):
            for dp, _, fn in os.walk(p):
                for x in fn:
                    rel = os.path.relpath(os.path.join(dp, x), root).replace(os.sep, "/")
                    if _is_coordination_path(rel, cfg):
                        continue
                    if not paths or any(rel == q or rel.startswith(q.rstrip("/") + "/") for q in paths):
                        files.append(rel)
        elif os.path.isfile(p):
            files.append(f)
    files.sort(key=lambda f: -os.path.getmtime(os.path.join(root, f)))
    included, used = [], 0
    for f in files:
        p = os.path.join(root, f)
        try:
            size = os.path.getsize(p)
            if size > 400_000 or used + size > budget:
                continue
            raw = open(p, "rb").read()
            if b"\0" in raw[:4000]:
                continue
            diff += f"\n--- NEW FILE {f} ---\n" + raw.decode("utf-8", "replace")
            used += size
            included.append(f)
        except OSError:
            pass
    return diff, included


# ---- helpers: processes ao starts that are not writers -------------------------

def helpers_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"helpers-{key}.json")


def _helpers_update(root, change):
    """Change the helper registry under its lock and replace it whole (#68).

    It was read, changed and written back unlocked, and the read path wrote too, so
    a status call overwrote a helper registered a moment before and the watchdog
    counted a running reviewer as a second writer.
    """
    from .storage import _exclusive_lock, replace_file_durably
    path = helpers_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _exclusive_lock(path + ".lock"):
        try:
            with open(path, encoding=UTF8) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        change(d)
        replace_file_durably(path, json.dumps(d).encode(UTF8))


def helper_register(root, pid, what):
    """A reviewer, a probe: started by ao inside the repo, never a writer.

    Bind the declaration to process start identity. PID existence alone is not
    identity: after reuse it would exclude an unrelated process indefinitely.
    """
    start = _process_start(pid, refresh=True)

    def change(d):
        # Dead, reused or unprovable entries go here, under the lock, never on a read.
        for key in list(d):
            recorded = d.get(key) if isinstance(d.get(key), dict) else {}
            try:
                current = _process_start(int(key))
            except (TypeError, ValueError):
                current = None
            if current is None or current != recorded.get("start"):
                d.pop(key, None)
        d[str(pid)] = {"what": what, "at": int(time.time()), "start": start}

    try:
        _helpers_update(root, change)
    except OSError:
        pass


def helper_release(root, pid):
    try:
        _helpers_update(root, lambda d: d.pop(str(pid), None))
    except OSError:
        pass


def helper_pids(root, what=None):
    """Registered helpers still running as the process that registered; reading writes nothing."""
    try:
        with open(helpers_path(root), encoding=UTF8) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return set()
    if not isinstance(d, dict):
        return set()
    live = set()
    for key, recorded in d.items():
        try:
            pid = int(key)
        except (TypeError, ValueError):
            continue
        recorded = recorded if isinstance(recorded, dict) else {}
        current_start = _process_start(pid)
        # Dead, reused, or legacy/unprovable pid: never let declaration alone
        # exclude a process. Conservative writer checks may count it once.
        if current_start is not None and recorded.get("start") == current_start:
            if what is None or recorded.get("what") == what:
                live.add(pid)
    return live


# ---- cost: what the coordination itself spends ----------------------------------
#
# "How much quota does ao cost, and is it bureaucracy?" is answerable only from
# the transcript. Every turn is classified by what it did — wrote product, ran
# the review/gate ceremony, only coordinated, or read and thought — and its
# spend is summed per class, so the overhead is a number and not an opinion.

_PRODUCT_PATH = re.compile(r"(^|/)(src|lib|app|apps|test|tests|spec|fixtures|evidence|docs|plugins|site|"
                           r"package\.json|pyproject\.toml|tsconfig)")
def _coord_path():
    """A path under a coordination directory, the shipped harnesses' own included (#76)."""
    names = ["agent-mail", r"\.ao", "semantic-review"] + [re.escape(d.rstrip("/")) for d in harness_dirs()]
    return re.compile(r"(^|/)(" + "|".join(names) + r")(/|$)")


# ---- what each feature costs, measured rather than estimated (#10) ------------------------

SPAWN_LOGS = {"nudge": "nudge-{key}.log", "architect_wake": "escalate-{key}.log", "refill": "refill-{key}.log"}


def spawn_times(root, what, since=None):
    """When the watchdog started a nudge, an architect wake or a refill, read from its own logs (#10)."""
    from .watchdog import STATE_DIR
    path = os.path.join(STATE_DIR, SPAWN_LOGS[what].format(key=project_key(root)))
    times = []
    try:
        with open(path, encoding=UTF8, errors="replace") as fh:
            for line in fh:
                found = re.match(r"^=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ", line)
                if found:
                    at = time.mktime(time.strptime(found.group(1), "%Y-%m-%d %H:%M:%S"))
                    if since is None or at >= since:
                        times.append(at)
    except OSError:
        pass
    return times


def feature_costs(cfg, since=None):
    """The implementer's spend attributed to each feature switch, and the window it was measured over (#10).

    `review`: turns that ran `ao review`. `reports`: turns that only coordinated -
    inbox, report, board, writers. `nudge`: turns a nudge started, within five
    minutes of it, that wrote no product; a nudge that started real work is the
    work, not overhead. The architect's wakes and refills spend the architect's
    pool, not this transcript, so they are counted and not priced, and an
    inventory review cannot be told from a review in a transcript, so it is
    counted with review.
    """
    root = cfg["root"]
    costs = turn_costs(cfg, since)
    turns = [turn for turn in costs["turns"] if turn.get("cls")]
    nudges = spawn_times(root, "nudge", since)
    features = {name: {"turns": 0, "usage": 0.0} for name in ("review", "reports", "nudge")}
    for turn in turns:
        start = turn.get("start") or 0
        if turn.get("reviews"):
            key = "review"
        elif turn["cls"] == "coordination":
            key = "reports"
        elif turn["cls"] != "product" and any(0 <= start - at <= 300 for at in nudges):
            key = "nudge"
        else:
            continue
        features[key]["turns"] += 1
        features[key]["usage"] += float(turn.get("usage") or 0)
    starts = [turn["start"] for turn in turns if turn.get("start")]
    return {"unit": costs["unit"], "total": sum(float(turn.get("usage") or 0) for turn in turns),
            "turns": len(turns), "from": min(starts) if starts else None, "to": max(starts) if starts else None,
            "features": features,
            "counted": dict({what: len(spawn_times(root, what, since)) for what in ("architect_wake", "refill")},
                            hunter=sum(1 for row in hunter_rows(root) if row.get("event") == "run"
                                       and (since is None or row.get("at", 0) >= since)))}


def turn_costs(cfg, since=None):
    """Per-turn cost and class from the implementer's transcript.

    Returns {"unit", "turns": [ {start, usage, cls, tool_calls, product_writes,
    reviews, commits, blocked_report} ], "by_class": {cls: {turns, usage}},
    "ao_commands": Counter, "total"}. Classes: product (wrote product files or
    committed), ceremony (review / verify / lock / commit-ok, nothing written),
    coordination (only inbox/report/writers/board, few calls), analysis (read and
    reasoned, wrote nothing).
    """
    import collections
    msgs, _ = session_paths(cfg)
    out = {"unit": "credit", "turns": [], "by_class": {}, "ao_commands": collections.Counter(), "total": 0.0}
    if not msgs or not os.path.exists(msgs):
        return out
    recs = read_tail(msgs, 400_000_000)
    cur = None

    def ts(d):
        raw = d.get("timestamp", "")
        try:
            import datetime as _dt
            return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    for d in recs:
        pl = d.get("payload") or d.get("message") or {}
        t = pl.get("type") or d.get("type")
        if t == "turn_start" or (t == "user" and cur is None) or (t == "user" and cur and cur.get("closed")):
            cur = {"start": ts(d), "usage": 0.0, "product_writes": 0, "coord_writes": 0, "tool_calls": 0,
                   "reviews": 0, "commits": 0, "blocked_report": False, "ao": collections.Counter()}
            out["turns"].append(cur)
            continue
        if cur is None:
            continue
        if t == "usage_summary":
            for ps in pl.get("promptTurnSummaries") or []:
                try:
                    cur["usage"] += float(ps.get("usage") or 0)
                    out["unit"] = ps.get("unit") or out["unit"]
                except (TypeError, ValueError):
                    pass
        elif t == "assistant" and isinstance(pl.get("usage"), dict):      # claude-code transcripts
            u = pl["usage"]
            cur["usage"] += (u.get("input_tokens", 0) + u.get("output_tokens", 0)) / 1000.0
            out["unit"] = "ktok"
        elif t == "turn_end":
            cur["closed"] = True
        elif t == "tool_call":
            cur["tool_calls"] += 1
            name = str(pl.get("toolName") or pl.get("name") or "")
            args = pl.get("args") or pl.get("input") or {}
            text = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
            if name.endswith("ao_report") and "blocked" in text:      # MCP clients prefix tool names
                cur["blocked_report"] = True
            if "write" in name.lower() or name in ("fs_write", "Edit", "Write", "MultiEdit"):
                path = str(args.get("path") or args.get("file_path") or "") if isinstance(args, dict) else ""
                if _coord_path().search(path):
                    cur["coord_writes"] += 1
                elif _PRODUCT_PATH.search(path) or (path and "/" in path):
                    cur["product_writes"] += 1
            m = re.search(r"\bao\s+(?:-C\s+\S+\s+)?([a-z][a-z-]+)", text)
            if m:
                cur["ao"][m.group(1)] += 1
                out["ao_commands"][m.group(1)] += 1
            if re.search(r"\bao\s+(?:-C\s+\S+\s+)?review\b", text):
                cur["reviews"] += 1
            if re.search(r"git\s+commit\b", text):
                cur["commits"] += 1
    for tn in out["turns"]:
        if since and (not tn["start"] or tn["start"] < since):
            tn["cls"] = None
            continue
        if tn["product_writes"] or tn["commits"]:
            cls = "product"
        elif tn["reviews"] or any(c in tn["ao"] for c in ("verify", "lock", "commit-ok")):
            cls = "ceremony"
        elif tn["blocked_report"] or (tn["tool_calls"] <= 8 and (tn["ao"] or tn["coord_writes"])):
            cls = "coordination"
        else:
            cls = "analysis"
        tn["cls"] = cls
        b = out["by_class"].setdefault(cls, {"turns": 0, "usage": 0.0, "wasted": 0, "wasted_usage": 0.0})
        b["turns"] += 1
        b["usage"] += tn["usage"]
        if tn["blocked_report"] and not tn["product_writes"]:
            b["wasted"] += 1
            b["wasted_usage"] += tn["usage"]
        out["total"] += tn["usage"]
    return out


# ---- deferred work: what could not run, so it can run later ----------------------
#
# A quota window closing must not lose anything. Every action ao could not take
# — a nudge, a wake, a review — is written down with why, and `ao catchup`
# replays the queue when the way is clear. This is the "bypass now, reconcile
# later" the human asked for: the run degrades, it never forgets.

def deferred_append(root, kind, **fields):
    """Defer one kind of work; an open deferral of that kind is the one that stands (#87).

    A wake is state, not a queue of attempts: fourteen deferred wakes piled up over
    one outage and replayed in a burst when it ended.
    """
    standing = next((row for row in deferred_open(root) if row.get("kind") == kind), None)
    if standing:
        return standing
    d = os.path.join(root, ".ao", "ledger")
    rec = {"event": "deferred", "id": f"DF-{int(time.time())}-{kind}", "kind": kind, "at": int(time.time())}
    rec.update(fields)
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "deferred.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def deferred_close(root, did, outcome="done"):
    d = os.path.join(root, ".ao", "ledger")
    try:
        with open(os.path.join(d, "deferred.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps({"event": "closed", "id": did, "at": int(time.time()), "outcome": outcome}) + "\n")
    except OSError:
        pass


def deferred_open(root):
    p = os.path.join(root, ".ao", "ledger", "deferred.jsonl")
    if not os.path.exists(p):
        return []
    rows, closed = {}, set()
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "deferred":
            rows[r["id"]] = r
        elif r.get("event") == "closed":
            closed.add(r["id"])
    return [r for i, r in rows.items() if i not in closed]


def recently_deferred(root, kind, within=3600):
    cut = time.time() - within
    return any(r["kind"] == kind and r["at"] >= cut for r in deferred_open(root))


# ---- waivers: the human's bypass, on the record ---------------------------------

WAIVER_CHAIN = "ao-waiver-row-v1"
WAIVER_HOURS_DEFAULT = settings.default("waivers.default_hours")
WAIVER_HOURS_MAX = settings.default("waivers.max_hours")


def waivers_path(root):
    return os.path.join(root, ".ao", "ledger", "waivers.jsonl")


def waiver_rows(root):
    """Every waiver row once the chain validates (#67).

    Rows written before the ledger was chained may stand first. An unlinked row
    after them, or a broken link, makes the ledger unreadable, and every reader
    fails closed.
    """
    from .storage import read_chained_jsonl
    return read_chained_jsonl(waivers_path(root), WAIVER_CHAIN, legacy_prefix=True)


def _login_and_terminal():
    """The login a command ran under and whether a terminal was attached."""
    try:
        import getpass
        user = getpass.getuser()
    except Exception:
        user = None
    try:
        interactive = os.isatty(0)
    except OSError:
        interactive = False
    return user, bool(interactive)


def waive(root, gate, slice_id, why, by, hours=WAIVER_HOURS_DEFAULT):
    """Record that a person waived a gate for one slice, for a bounded time (#67).

    A chained append like an authority row. It carries an expiry, the name the
    person gave, and beside it what ao can check: the login and whether a terminal
    was attached.
    """
    from .storage import append_chained_jsonl
    now = int(time.time())
    taken = {row.get("id") for row in waiver_rows(root) if isinstance(row, dict)}
    wid, n = f"W-{now}", 2
    while wid in taken:
        wid, n = f"W-{now}-{n}", n + 1
    user, interactive = _login_and_terminal()
    record = {"event": "waived", "id": wid, "gate": gate, "slice": slice_id, "why": why,
              "by": by, "at": now, "expires": now + int(float(hours) * 3600),
              "user": user, "interactive": interactive,
              "head": sh("git rev-parse HEAD", cwd=root), "tree": tree_digest(root)}
    return append_chained_jsonl(waivers_path(root), record, WAIVER_CHAIN, legacy_prefix=True)


def close_waiver(root, wid, outcome):
    """Retire a waiver with a durable chained append; a failure raises rather than leaving it open unseen (#67)."""
    from .storage import append_chained_jsonl
    return append_chained_jsonl(
        waivers_path(root),
        {"event": "closed", "id": wid, "at": int(time.time()), "outcome": outcome},
        WAIVER_CHAIN, legacy_prefix=True,
    )


def open_waivers(root, gate=None, slice_id=None):
    rows, closed = {}, set()
    for r in waiver_rows(root):
        if not isinstance(r, dict):
            continue
        if r.get("event") == "waived" and r.get("id"):
            rows[r["id"]] = r
        elif r.get("event") == "closed":
            closed.add(r.get("id"))
    out = [r for i, r in rows.items() if i not in closed]
    if gate:
        out = [r for r in out if r.get("gate") == gate]
    if slice_id:
        out = [r for r in out if r.get("slice") in (slice_id, "*")]
    return out


def _waiver_candidates(root):
    """Candidate digests each waiver has already authorised, from the grants that used it."""
    bound = {}
    for row in authority_rows(root):
        if isinstance(row, dict) and row.get("granted") is True and row.get("waiver"):
            bound.setdefault(row["waiver"], set()).add((row.get("candidate") or {}).get("digest"))
    return bound


def review_waiver_for(root, running, candidate_digest, now=None):
    """The review waiver that may stand in for a review of this candidate, and why others may not (#67).

    It names a running slice exactly, has not expired, and has authorised no other
    candidate. A waiver for every slice, one from before waivers expired, and one
    already spent on other bytes stand in for nothing.
    """
    now = time.time() if now is None else now
    bound = _waiver_candidates(root)
    notes = []
    for waiver in reversed(open_waivers(root, gate="review")):
        wid, slice_id = waiver.get("id"), waiver.get("slice")
        if slice_id not in running:
            if slice_id == "*":
                notes.append(f"waiver {wid} names every slice; a waiver covers one")
            continue
        expires = waiver.get("expires")
        if not isinstance(expires, (int, float)) or isinstance(expires, bool):
            notes.append(f"waiver {wid} for {slice_id} predates waiver expiry; a person opens a new one")
            continue
        if expires <= now:
            notes.append(f"waiver {wid} for {slice_id} expired "
                         f"{time.strftime('%d %b %H:%M', time.localtime(expires))}")
            continue
        used = bound.get(wid, set())
        if used and candidate_digest not in used:
            notes.append(f"waiver {wid} already authorised other bytes; it covers one candidate")
            continue
        return waiver, notes
    return None, notes


def open_waiver_report(root, now=None):
    """One line per open waiver with its age and expiry, for `ao doctor` (#67)."""
    now = time.time() if now is None else now
    lines = []
    for waiver in open_waivers(root):
        age = max(0, int(now - (waiver.get("at") or now)))
        expires = waiver.get("expires")
        state = ("no expiry (legacy)" if not isinstance(expires, (int, float))
                 else "expired" if expires <= now
                 else f"expires in {int((expires - now) // 3600)}h")
        lines.append(f"{waiver.get('id')} {waiver.get('gate')} for {waiver.get('slice')}, "
                     f"open {age // 86400}d {age % 86400 // 3600}h, {state}, by {waiver.get('by')}")
    return lines


_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _bound_waiver_target(root, waiver, trees):
    """The one landed commit a bounded waiver covers, found by the tree granted under it (#17).

    A waiver that nothing was granted under covers no commit; one whose granted tree
    never landed is UNRESOLVED rather than reviewed by guess.
    """
    item = {"waiver": waiver, "start": "", "end": "", "landed": 0,
            "newest": False, "problem": None, "unused": not trees,
            "expired": (waiver.get("expires") or 0) <= time.time()}
    if not trees:
        return item
    try:
        log = _git_output(root, "log", "--max-count=500", "--format=%H %T", "HEAD", "--").decode("ascii")
    except (RuntimeError, UnicodeError):
        item["problem"] = "UNRESOLVED: git cannot list the landed commits"
        return item
    sha = next((parts[0] for parts in (line.split() for line in log.splitlines())
                if len(parts) == 2 and parts[1] in trees), None)
    if not sha:
        item["problem"] = "UNRESOLVED: no landed commit carries the tree granted under it"
        return item
    try:
        parent = _git_output(root, "rev-parse", "--verify", f"{sha}^").decode("ascii").strip()
    except (RuntimeError, UnicodeError):
        item["problem"] = "UNRESOLVED: its landed commit has no parent to review against"
        return item
    item.update(start=parent, end=sha, landed=1)
    return item


def review_waiver_ranges(root):
    """Each open review waiver with the commits that landed while it was the newest.

    A waiver records the HEAD it was opened on and nothing else, so reviewing
    head..HEAD for every open waiver reviews each later slice again under the
    first slice's boundary: with one waiver per slice, N waivers become N
    overlapping reviews and the earliest carries every slice after it. A slice's
    commits run from its waiver's head to the head of the next review waiver
    opened after it, open or closed, and the newest waiver runs to HEAD.

    Heads come from a ledger file anyone on the machine can edit, so each is
    required to be a full object id and git runs without a shell.

    Returns dicts: waiver, start, end, landed (commit count), newest, problem.
    """
    waived, closed = [], set()
    for r in waiver_rows(root):
        if not isinstance(r, dict):
            continue
        if r.get("event") == "waived" and r.get("gate") == "review":
            waived.append(r)
        elif r.get("event") == "closed":
            closed.add(r.get("id"))
    if not waived:
        return []
    granted = {}
    for row in authority_rows(root):
        if isinstance(row, dict) and row.get("granted") is True and row.get("waiver"):
            granted.setdefault(row["waiver"], set()).add((row.get("candidate") or {}).get("index_tree"))
    try:
        current = _git_output(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    except (RuntimeError, UnicodeError):
        current = None
    out = []
    for i, w in enumerate(waived):
        if w.get("id") in closed:
            continue
        if "expires" in w:
            out.append(_bound_waiver_target(root, w, granted.get(w["id"], set())))
            continue
        later = waived[i + 1] if i + 1 < len(waived) else None
        start = str(w.get("head") or "")
        end = str(later.get("head") or "") if later else (current or "")
        item = {"waiver": w, "start": start, "end": end, "landed": 0,
                "newest": later is None, "problem": None}
        if not _OBJECT_ID.fullmatch(start):
            item["problem"] = "its recorded head is not a full object id"
        elif not _OBJECT_ID.fullmatch(end):
            item["problem"] = ("the next waiver's recorded head is not a full object id"
                               if later else "HEAD cannot be resolved")
        else:
            try:
                ancestor = subprocess.run(
                    [git_binary(), "merge-base", "--is-ancestor", start, end], cwd=root,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
                ).returncode
            except (OSError, subprocess.TimeoutExpired):
                ancestor = 2
            if ancestor == 1:
                item["problem"] = "its head is not an ancestor of the range end; history was rewritten"
            elif ancestor:
                item["problem"] = "git cannot compare its head with the range end"
            else:
                try:
                    item["landed"] = int(_git_output(root, "rev-list", "--count", f"{start}..{end}").strip() or 0)
                except (RuntimeError, ValueError):
                    item["problem"] = "git cannot count the commits in its range"
        out.append(item)
    return out


# ---- credits: burn rate and the day the work stops -------------------------------

def credit_account(profile):
    """A stable name for the account a reading came from; the profile itself is not kept (#36)."""
    if not profile:
        return None
    return "acct-" + hashlib.sha256(str(profile).encode("utf-8")).hexdigest()[:12]


def record_credit_sample(root, used, limit, reset_at=None, account=None, at=None):
    d = os.path.join(root, ".ao", "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "credits.jsonl"), "a", encoding=UTF8) as fh:
            fh.write(json.dumps({"at": int(at if at is not None else time.time()), "used": float(used),
                                 "limit": float(limit), "reset_at": reset_at,
                                 "account": account}) + "\n")
    except OSError:
        pass


def credit_samples(root, limit=500):
    p = os.path.join(root, ".ao", "ledger", "credits.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-limit:]


def burn_rate(root, window=72 * 3600, now=None):
    """From the samples: credits per day, days left, and whether the plan runs out
    before it resets. None when there are not two samples a few hours apart."""
    now = now or time.time()
    rows = [r for r in credit_samples(root) if r["at"] >= now - window]
    # One account's series only (#36). An owner who switched accounts left the old
    # account's cumulative `used` in the ledger, and the projection ran across both:
    # exhaustion declared against an account that was 4% used. A reading that does
    # not say whose it is projects nothing.
    account = rows[-1].get("account") if rows else None
    if not account:
        return None
    rows = [r for r in rows if r.get("account") == account]
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    span = last["at"] - first["at"]
    if span < 3 * 3600:
        return None
    per_day = max(0.0, (last["used"] - first["used"]) / span * 86400)
    remaining = max(0.0, last["limit"] - last["used"])
    days_left = remaining / per_day if per_day > 0 else None
    exhausts_at = now + days_left * 86400 if days_left is not None else None
    reset_at = last.get("reset_at")
    before_reset = bool(exhausts_at and reset_at and exhausts_at < reset_at)
    return {"per_day": per_day, "remaining": remaining, "days_left": days_left,
            "exhausts_at": exhausts_at, "reset_at": reset_at, "before_reset": before_reset,
            "used": last["used"], "limit": last["limit"], "account": account,
            "samples": [first, last]}


# ---- external ping: the dead man's switch --------------------------------------------

def pings_path():
    return os.path.join(HOME, ".ao", "pings.json")


def ping_url(root):
    try:
        d = json.load(open(pings_path(), encoding=UTF8))
    except (OSError, ValueError):
        return None
    key = project_key(root)
    return d.get(key) or d.get("*")


def set_ping_url(root, url, all_projects=False):
    try:
        d = json.load(open(pings_path(), encoding=UTF8))
    except (OSError, ValueError):
        d = {}
    d["*" if all_projects else project_key(root)] = url
    os.makedirs(os.path.dirname(pings_path()), exist_ok=True)
    json.dump(d, open(pings_path(), "w", encoding=UTF8), indent=2)
    os.chmod(pings_path(), 0o600)
    return d


def ping(root, opener=None):
    """One GET to the project's ping URL. A service that expects it every N
    minutes alarms when it stops — which is the only way anyone learns that both
    the watchdog and its doctor job have died."""
    url = ping_url(root)
    if not url:
        return None
    import urllib.request
    try:
        (opener or urllib.request.urlopen)(url, timeout=5)
        return True
    except Exception:
        return False


# ---- architect lock: one judge at a time --------------------------------------------

def architect_lock_path(root):
    key = project_key(root)
    return os.path.join(HOME, ".ao", f"architect-{key}.lock")


def architect_lock_holder(root):
    try:
        d = json.load(open(architect_lock_path(root), encoding=UTF8))
    except (OSError, ValueError):
        return None
    if d.get("pid") and not _pid_alive(int(d["pid"])):
        return None                                        # stale: holder died
    if time.time() - d.get("at", 0) > 3 * 3600:
        return None                                        # stale: forgotten
    return d


def acquire_architect(root, pid, who):
    """Take the lock unless a live holder has it. Two architect copies deciding at
    once — a desktop resume beside a watchdog wake — contradict each other."""
    holder = architect_lock_holder(root)
    if holder and holder.get("pid") != pid:
        return None
    d = {"pid": pid, "who": who, "at": int(time.time())}
    try:
        os.makedirs(os.path.dirname(architect_lock_path(root)), exist_ok=True)
        json.dump(d, open(architect_lock_path(root), "w", encoding=UTF8))
    except OSError:
        pass
    return d


def release_architect(root, pid=None):
    try:
        d = json.load(open(architect_lock_path(root), encoding=UTF8))
        if pid is None or d.get("pid") == pid:
            os.remove(architect_lock_path(root))
    except (OSError, ValueError):
        pass


# ---- foreign edits: a person in the same files -------------------------------------

def implementer_recent_writes(cfg, minutes=15):
    """Paths the implementer's tools wrote in the last N minutes, from its transcript."""
    msgs, _ = session_paths(cfg)
    if not msgs or not os.path.exists(msgs):
        return set()
    cut = time.time() - minutes * 60
    out = set()
    for d in read_tail(msgs, 3_000_000):
        pl = d.get("payload") or {}
        if pl.get("type") != "tool_call":
            continue
        try:
            import datetime as _dt
            at = _dt.datetime.fromisoformat(d.get("timestamp", "").replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if at < cut:
            continue
        args = pl.get("args") or {}
        path = args.get("path") or args.get("file_path") if isinstance(args, dict) else None
        if path:
            out.add(os.path.realpath(str(path)))
    return out


def foreign_edits(root, cfg, minutes=15):
    """Product files changed in the last N minutes that the implementer did not write.

    `ao writers` sees agents; it cannot see a person in an editor. This is the
    nearest thing: a dirty product file whose mtime is recent and which the
    implementer's own tool calls never touched. Named in the nudge so the
    implementer keeps away from it."""
    mine = implementer_recent_writes(cfg, minutes)
    cut = time.time() - minutes * 60
    out = []
    for line in product_dirty(root, cfg):
        rel = line[3:].strip().strip('"')
        p = os.path.join(root, rel)
        files = []
        if os.path.isdir(p):                                    # git lists an untracked dir as one entry
            for dp, _, fn in os.walk(p):
                files += [os.path.join(dp, x) for x in fn]
        elif os.path.isfile(p):
            files = [p]
        for f in files:
            try:
                if os.path.getmtime(f) >= cut and os.path.realpath(f) not in mine:
                    out.append(os.path.relpath(f, root).replace(os.sep, "/"))     # git's slashes, everywhere
            except OSError:
                pass
    return sorted(out)


# ---- fleet reserve: the machine's shared windows ----------------------------------

def fleet_reserve():
    return settings.get(None, "fleet.window_reserve_pct")


def window_headroom(provider):
    """(left_pct, reserve_pct) or (None, reserve) when the window is unreadable or no provider is known."""
    w = provider_window(provider) if provider else None
    return (100 - w["pct"]) if w else None, fleet_reserve()


def turn_ended(cfg):
    """Has the implementer's transcript closed its last turn?

    A process that is alive after its transcript wrote `turn_end` is not
    working; it is a runtime that forgot to exit. Waiting the full silence
    threshold for it (three times the idle window) cost twenty minutes per
    occurrence. The transcript's own word is enough to reap at the idle window."""
    msgs, _ = session_paths(cfg)
    if not msgs or not os.path.exists(msgs):
        return False
    tail = read_tail(msgs, 200_000)
    for d in reversed(tail):
        t = (d.get("payload") or {}).get("type") or d.get("type")
        if t in ("session_metadata", "usage_summary", "session_event"):
            continue                                        # bookkeeping after the turn
        return t in ("turn_end", "result")
    return False
