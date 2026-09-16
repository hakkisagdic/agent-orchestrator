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


_part("lib_slices", globals())


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
