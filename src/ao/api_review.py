"""ao-api-review: an answer from an OpenAI-compatible chat API, run by ao as a tool reviewer (API-REVIEWER).

ao hands a tool reviewer the review prompt, which carries the candidate's diff, and a file to answer
in (docs/adapters.md, "A reviewer can be a tool ao runs"). Without `--tree`, this client asks a
provider's `/chat/completions` once, with the prompt as the only message and no tools, and writes the
answer it gets. With `--tree DIR`, it offers four read-only function tools (`read`, `grep`, `glob`,
`list`) confined to the unpacked candidate tree at `DIR`, answers tool calls within per-answer and
per-turn bounds, and writes the final verdict and the tool usage (`rounds`, `read_bytes`) beside it
(API-REVIEWER-TOOLS).

The provider is the machine's. `review.api_providers` names each by a word, with its base URL and
the environment variable that holds its key; the model is `<provider>/<model>`. The key is read
from that variable at the moment it is sent, sent to that URL alone - over HTTPS, or to this
machine with no proxy between - and never carried through a redirect. It is never written anywhere.
"""
import argparse
import json
import ntpath
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__, lib as A, outbound

UTF8 = "utf-8"
USAGE, FAILED = 2, 1
LOOPBACK = ("127.0.0.1", "localhost", "::1")

TOOL_MAX_BYTES = 32_768
TOOL_MAX_LINES = 200
TOOL_MAX_MATCHES = 100
GREP_MAX_FILE_BYTES = 524_288
TURN_MAX_ROUNDS = 16
TURN_MAX_READ_BYTES = 262_144
TURN_MAX_SECONDS = 300.0

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": ("Read one file in the candidate's unpacked tree. Every path is relative to the tree root "
                            "(do not prefix with candidate/)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path of the file inside the tree."},
                    "first_line": {"type": "integer", "description": "1-based first line to read."},
                    "line_count": {"type": "integer", "description": "Maximum number of lines to read."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search files in the candidate's unpacked tree for a regular expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to match against each line."},
                    "path": {"type": "string", "description": "Optional relative file or directory path to search."},
                    "glob": {"type": "string", "description": "Optional glob pattern filtering file paths."},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files in the candidate's unpacked tree matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Relative glob pattern inside the tree."},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list",
            "description": "List one directory in the candidate's unpacked tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative directory path inside the tree ('.' for root)."},
                },
                "additionalProperties": False,
            },
        },
    },
]


def providers(entries):
    """{word: (base URL, key variable)} from `review.api_providers`, or ValueError naming the entry that is not one.

    `/chat/completions` is put after the base URL. Behind a `?` or a `#` it landed in the query or the fragment, and
    the key went to the base URL's own path; and a user or a password in the URL is a credential written in a setting,
    which urllib read as a port and printed in the error that refused it (HUNT-LEADS-SEC). Neither is taken, and
    neither refusal repeats the URL, whose query may hold a key too.
    """
    table = {}
    for entry in entries or []:
        words = str(entry).split()
        if len(words) != 3:
            raise ValueError(f"{entry!r} in review.api_providers is not a word, a base URL and the environment "
                             "variable that holds its key")
        name, url, variable = words
        parsed = urllib.parse.urlparse(url)
        if "@" in parsed.netloc:
            raise ValueError(f"{name}'s base URL names a user or a password: write it without them, since the key is "
                             "read from its environment variable alone")
        if "?" in url or "#" in url:
            raise ValueError(f"{name}'s base URL holds a query or a fragment, where /chat/completions would land: "
                             "write the URL the API's paths start from, with neither")
        if not (parsed.scheme == "https" or parsed.scheme == "http" and parsed.hostname in LOOPBACK) \
                or not parsed.hostname:
            raise ValueError(f"{name}'s base URL {url} is not https: its key would leave this machine unprotected")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
            raise ValueError(f"{name}'s key is named {variable!r}, which is no environment variable")
        table[name] = (url.rstrip("/"), variable)
    return table


def _scrub(text, key):
    """`text` with the key taken out, as written and as Python escapes it in a repr (API-REVIEWER-3).

    It is put in place by a mark that does not hold the key: a key spelled `<key>` was replaced by itself
    (API-REVIEWER-4).
    """
    forms = {key, repr(key)[1:-1], key.encode("unicode_escape").decode("ascii")} - {""}
    mark = next(mark for mark in ("<key>", "<redacted>", "***", "#") if not any(form in mark for form in forms))
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, mark)
    return text


def _content(answer):
    """The text of the first choice's message: a string, or the text parts of a list of them."""
    try:
        content = answer["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def _no_answer(provider, model, answer):
    """Why an answer holds no text (API-REVIEWER-5).

    A reasoning model that spends the whole answer its provider allows on reasoning stops before it writes a verdict:
    EVREN allows 16,384 tokens, and on a review prompt of about 28 KB GLM-5.3, DeepSeek V4.1 Flash, Qwen3.8 Flash and
    MiMo V2.6 Pro each spent all of them so on 2026-10-06. "Gave no answer" sent a person looking for a fault at the
    provider. Only ao's own words and a whole number from the answer are said: the provider's text is not printed. They
    say thinking, since a string in ao's core never names a field of a harness's transcript (API-REVIEWER-6).
    """
    try:
        choice = answer["choices"][0]
        finish = choice.get("finish_reason")
        details = (answer.get("usage") or {}).get("completion_tokens_details")
    except (KeyError, IndexError, TypeError, AttributeError):
        return f"{provider} gave no answer for {model}"
    if finish != "length":
        return f"{provider} gave no answer for {model}"
    spent = details.get("reasoning_tokens") if isinstance(details, dict) else None
    spent = f" ({spent} tokens of thinking)" if isinstance(spent, int) and not isinstance(spent, bool) else ""
    return (f"{model} on {provider} spent the whole answer it may give{spent} before it wrote one; a smaller "
            "candidate, or a model that reasons less, gives a verdict")


def _check_relative_syntax(raw_path):
    """Refusal reason when `raw_path` is absolute or climbs above the root with `..`, else None."""
    if not isinstance(raw_path, str) or "\x00" in raw_path:
        return "refused: invalid path"
    cleaned = raw_path.strip() or "."
    if os.path.isabs(cleaned) or cleaned.startswith(("/", "\\", "~")) or ntpath.splitdrive(cleaned)[0]:
        return "refused: absolute path is outside the candidate tree"
    depth = 0
    for part in cleaned.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if depth == 0:
                return "refused: '..' leaves the candidate tree"
            depth -= 1
        else:
            depth += 1
    return None


def _link_stays_in_tree(link_path, real_root, config_names):
    """(resolved_path, None) when `link_path` resolves inside `real_root` and outside harness config, else (None, why)."""
    try:
        target = os.readlink(link_path)
    except OSError:
        return None, "refused: symbolic link cannot be read"
    if os.path.isabs(target) or target.startswith(("/", "\\", "~")) or ntpath.splitdrive(target)[0]:
        return None, "refused: symbolic link leads outside the candidate tree"
    step_target = os.path.normpath(os.path.join(os.path.dirname(link_path), target))
    real_target = os.path.realpath(link_path)
    if not (A._within(step_target, real_root) and A._within(real_target, real_root)):
        return None, "refused: symbolic link leads outside the candidate tree"
    rel_target = os.path.relpath(real_target, real_root).replace(os.sep, "/")
    if rel_target != "." and A.project_config_member(rel_target, config_names) is not None:
        return None, "refused: harness configuration is withheld from reviewers"
    return real_target, None


def _resolve_in_tree(tree_root, raw_path, config_names):
    """Resolve `raw_path` inside `tree_root`: (real_path, rel_path, None), or (None, None, refusal)."""
    if not tree_root or os.path.islink(os.path.abspath(tree_root)) or not os.path.isdir(tree_root):
        return None, None, "refused: candidate tree is not available"
    real_root = os.path.realpath(tree_root)
    refused = _check_relative_syntax(raw_path)
    if refused:
        return None, None, refused
    cleaned = (raw_path or "").strip() or "."
    if cleaned != "." and not A._lands_inside(real_root, cleaned):
        return None, None, "refused: '..' leaves the candidate tree"
    cur = real_root
    for part in cleaned.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            cur = os.path.dirname(cur)
            if not A._within(cur, real_root):
                return None, None, "refused: '..' leaves the candidate tree"
            continue
        cur = os.path.join(cur, part)
        rel_cur = os.path.relpath(cur, real_root).replace(os.sep, "/")
        if rel_cur != "." and A.project_config_member(rel_cur, config_names) is not None:
            return None, None, "refused: harness configuration is withheld from reviewers"
        if os.path.islink(cur):
            cur, link_refused = _link_stays_in_tree(cur, real_root, config_names)
            if link_refused:
                return None, None, link_refused
    final_real = os.path.realpath(cur)
    if not A._within(final_real, real_root):
        return None, None, "refused: path resolves outside the candidate tree"
    final_rel = os.path.relpath(final_real, real_root).replace(os.sep, "/")
    if final_rel != "." and A.project_config_member(final_rel, config_names) is not None:
        return None, None, "refused: harness configuration is withheld from reviewers"
    return final_real, ("" if final_rel == "." else final_rel), None


def _glob_regex(pattern):
    parts, i, n = ["^"], 0, len(pattern)
    while i < n:
        if pattern[i:i + 3] == "**/":
            parts.append("(?:.+/)?")
            i += 3
        elif pattern[i:i + 2] == "**":
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            j = pattern.find("]", i + 1)
            if j < 0:
                parts.append(re.escape("["))
                i += 1
            else:
                inner = pattern[i + 1:j].replace("\\", "\\\\")
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                parts.append(f"[{inner}]")
                i = j + 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def _iter_tree_files(real_root, start_real, start_rel, config_names, refused_links=None):
    """Yield `(real_path, rel_path)` for regular files under `start_real`, confined to `real_root`."""
    visited = set()
    try:
        st0 = os.stat(start_real)
        visited.add((st0.st_dev, st0.st_ino))
    except OSError:
        return

    def walk(cur_real, cur_rel):
        try:
            names = sorted(os.listdir(cur_real))
        except OSError:
            return
        for name in names:
            child_lex = os.path.join(cur_real, name)
            child_rel = f"{cur_rel}/{name}" if cur_rel else name
            if A.project_config_member(child_rel, config_names) is not None:
                continue
            if os.path.islink(child_lex):
                child_real, link_refused = _link_stays_in_tree(child_lex, real_root, config_names)
                if link_refused:
                    if refused_links is not None and "outside" in link_refused:
                        refused_links.append((child_rel, link_refused))
                    continue
            else:
                child_real = os.path.realpath(child_lex)
                if not A._within(child_real, real_root):
                    continue
            try:
                st = os.stat(child_real)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                key = (st.st_dev, st.st_ino)
                if key in visited:
                    continue
                visited.add(key)
                yield from walk(child_real, child_rel)
            elif stat.S_ISREG(st.st_mode):
                yield child_real, child_rel

    yield from walk(start_real, start_rel)


def _read_regular_bytes(real_path, max_bytes):
    """Read up to `max_bytes + 1` bytes from a regular file without blocking on special files."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(real_path, flags)
    except FileNotFoundError:
        return None, "error: file not found"
    except IsADirectoryError:
        return None, "error: path is a directory"
    except OSError as exc:
        return None, f"error: could not open file ({type(exc).__name__})"
    try:
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode):
            return None, "error: path is a directory"
        if not stat.S_ISREG(mode):
            return None, "refused: path is not a regular file"
        chunks, total = [], 0
        with os.fdopen(fd, "rb") as fh:
            fd = None
            while total <= max_bytes:
                chunk = fh.read(min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
        return b"".join(chunks), None
    except OSError as exc:
        return None, f"error: could not read file ({type(exc).__name__})"
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _cap_answer_bytes(text, limit=TOOL_MAX_BYTES):
    encoded = text.encode(UTF8, "replace")
    if len(encoded) <= limit:
        return text
    suffix = f"\n... (truncated at {limit} bytes)"
    keep = max(0, limit - len(suffix.encode(UTF8)))
    return encoded[:keep].decode(UTF8, "ignore") + suffix


def _tool_read(state, args):
    real_path, _, refused = _resolve_in_tree(state["tree"], args.get("path"), state["config_names"])
    if refused:
        return refused
    first_line = args.get("first_line", args.get("start_line", 1))
    line_count = args.get("line_count", args.get("limit", TOOL_MAX_LINES))
    if not isinstance(first_line, int) or isinstance(first_line, bool) or first_line < 1:
        return "error: first_line must be a positive integer"
    if not isinstance(line_count, int) or isinstance(line_count, bool) or line_count < 1:
        return "error: line_count must be a positive integer"
    capped_lines = min(line_count, TOOL_MAX_LINES)
    line_bound_hit = line_count > TOOL_MAX_LINES
    remaining = TURN_MAX_READ_BYTES - state["read_bytes"]
    if remaining <= 0:
        state["bound"] = f"total bytes read limit ({TURN_MAX_READ_BYTES} bytes) reached"
        return f"turn bound reached: {state['bound']}; write your review verdict now"
    data, err = _read_regular_bytes(real_path, max(GREP_MAX_FILE_BYTES, TOOL_MAX_BYTES + 1))
    if err:
        return err
    all_lines = data.decode(UTF8, "replace").splitlines(keepends=True)
    selected = all_lines[first_line - 1:first_line - 1 + capped_lines]
    if first_line - 1 + capped_lines < len(all_lines) and capped_lines == TOOL_MAX_LINES:
        line_bound_hit = True
    raw_selected = "".join(selected).encode(UTF8, "replace")
    byte_bound_hit = False
    if len(raw_selected) > remaining:
        raw_selected = raw_selected[:remaining]
        state["read_bytes"] += remaining
        state["bound"] = f"total bytes read limit ({TURN_MAX_READ_BYTES} bytes) reached"
    else:
        if len(raw_selected) > TOOL_MAX_BYTES:
            raw_selected = raw_selected[:TOOL_MAX_BYTES]
            byte_bound_hit = True
        state["read_bytes"] += len(raw_selected)
        if state["read_bytes"] >= TURN_MAX_READ_BYTES:
            state["bound"] = f"total bytes read limit ({TURN_MAX_READ_BYTES} bytes) reached"
    shown_lines = raw_selected.decode(UTF8, "ignore").splitlines()
    numbered = "\n".join(f"{first_line + idx}: {line}" for idx, line in enumerate(shown_lines))
    notes = []
    if line_bound_hit:
        notes.append(f"... (truncated at {TOOL_MAX_LINES} lines)")
    if byte_bound_hit:
        notes.append(f"... (truncated at {TOOL_MAX_BYTES} bytes)")
    if state["bound"]:
        notes.append(f"... (turn bound reached: {state['bound']})")
    out = numbered + ("\n" + "\n".join(notes) if notes else "")
    return _cap_answer_bytes(out, TOOL_MAX_BYTES)


def _tool_list(state, args):
    raw_path = args.get("path", ".")
    if raw_path is None:
        raw_path = "."
    real_path, rel_path, refused = _resolve_in_tree(state["tree"], raw_path, state["config_names"])
    if refused:
        return refused
    if not os.path.isdir(real_path):
        return "error: path is not a directory"
    real_root = os.path.realpath(state["tree"])
    try:
        names = sorted(os.listdir(real_path))
    except OSError as exc:
        return f"error: could not list directory ({type(exc).__name__})"
    entries = []
    for name in names:
        child_rel = f"{rel_path}/{name}" if rel_path else name
        if A.project_config_member(child_rel, state["config_names"]) is not None:
            continue
        child_lex = os.path.join(real_path, name)
        if os.path.islink(child_lex):
            child_real, link_refused = _link_stays_in_tree(child_lex, real_root, state["config_names"])
            if link_refused:
                continue
        else:
            child_real = os.path.realpath(child_lex)
            if not A._within(child_real, real_root):
                continue
        entries.append(f"{name}/" if os.path.isdir(child_real) else name)
    truncated = len(entries) > TOOL_MAX_MATCHES
    shown = entries[:TOOL_MAX_MATCHES]
    out = "\n".join(shown) if shown else "(empty directory)"
    if truncated:
        out += f"\n... (truncated at {TOOL_MAX_MATCHES} entries)"
    return _cap_answer_bytes(out, TOOL_MAX_BYTES)


def _tool_glob(state, args):
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return "error: pattern must be a non-empty string"
    refused = _check_relative_syntax(pattern)
    if refused:
        return refused
    if not state["tree"] or os.path.islink(os.path.abspath(state["tree"])) or not os.path.isdir(state["tree"]):
        return "refused: candidate tree is not available"
    real_root = os.path.realpath(state["tree"])
    if not A._lands_inside(real_root, pattern.strip()):
        return "refused: '..' leaves the candidate tree"
    norm_parts = []
    for part in pattern.strip().replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not norm_parts:
                return "refused: '..' leaves the candidate tree"
            norm_parts.pop()
        else:
            norm_parts.append(part)
    norm_pattern = "/".join(norm_parts) or "*"
    prefix_parts = []
    for part in norm_parts:
        if any(ch in part for ch in "*?["):
            break
        prefix_parts.append(part)
    if prefix_parts:
        _, _, prefix_refused = _resolve_in_tree(state["tree"], "/".join(prefix_parts), state["config_names"])
        if prefix_refused:
            return prefix_refused
    matcher = _glob_regex(norm_pattern)
    refused_links = []
    matches = []
    truncated = False
    for _, rel_path in _iter_tree_files(real_root, real_root, "", state["config_names"], refused_links=refused_links):
        if matcher.match(rel_path):
            if len(matches) >= TOOL_MAX_MATCHES:
                truncated = True
                break
            matches.append(rel_path)
    if not matches and refused_links:
        for link_rel, link_why in refused_links:
            if matcher.match(link_rel):
                return link_why
    out = "\n".join(matches) if matches else "(no matches)"
    if truncated:
        out += f"\n... (truncated at {TOOL_MAX_MATCHES} matches)"
    return _cap_answer_bytes(out, TOOL_MAX_BYTES)


def _tool_grep(state, args):
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return "error: pattern must be a non-empty string"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"error: invalid regular expression ({exc})"
    raw_path = args.get("path", ".")
    if raw_path is None:
        raw_path = "."
    real_path, rel_path, refused = _resolve_in_tree(state["tree"], raw_path, state["config_names"])
    if refused:
        return refused
    file_glob = args.get("glob")
    glob_matcher = None
    glob_basename = False
    if file_glob is not None:
        if not isinstance(file_glob, str) or not file_glob.strip():
            return "error: glob must be a non-empty string"
        glob_refused = _check_relative_syntax(file_glob)
        if glob_refused:
            return glob_refused
        norm_glob = "/".join(part for part in file_glob.strip().replace("\\", "/").split("/") if part not in ("", "."))
        glob_basename = "/" not in norm_glob
        glob_matcher = _glob_regex(norm_glob)
    real_root = os.path.realpath(state["tree"])
    if os.path.isfile(real_path):
        files = [(real_path, rel_path)]
    elif os.path.isdir(real_path):
        files = _iter_tree_files(real_root, real_path, rel_path, state["config_names"])
    else:
        return "error: path not found"
    matches = []
    truncated = False
    for file_real, file_rel in files:
        if glob_matcher is not None:
            target_name = os.path.basename(file_rel) if glob_basename else file_rel
            if not glob_matcher.match(target_name) and not glob_matcher.match(file_rel):
                continue
        remaining = TURN_MAX_READ_BYTES - state["read_bytes"]
        if remaining <= 0:
            state["bound"] = f"total bytes read limit ({TURN_MAX_READ_BYTES} bytes) reached"
            break
        data, err = _read_regular_bytes(file_real, min(GREP_MAX_FILE_BYTES, remaining + 1))
        if err or data is None or b"\x00" in data[:4096]:
            continue
        if len(data) > remaining:
            data = data[:remaining]
            state["read_bytes"] += remaining
            state["bound"] = f"total bytes read limit ({TURN_MAX_READ_BYTES} bytes) reached"
        else:
            state["read_bytes"] += len(data)
            if state["read_bytes"] >= TURN_MAX_READ_BYTES:
                state["bound"] = f"total bytes read limit ({TURN_MAX_READ_BYTES} bytes) reached"
        for lineno, line in enumerate(data.decode(UTF8, "replace").splitlines(), 1):
            if regex.search(line):
                if len(matches) >= TOOL_MAX_MATCHES:
                    truncated = True
                    break
                matches.append(f"{file_rel}:{lineno}: {line[:300]}")
        if truncated or state["bound"]:
            break
    out = "\n".join(matches) if matches else "(no matches)"
    if truncated:
        out += f"\n... (truncated at {TOOL_MAX_MATCHES} matches)"
    if state["bound"]:
        out += f"\n... (turn bound reached: {state['bound']})"
    return _cap_answer_bytes(out, TOOL_MAX_BYTES)


def _dispatch_tool(state, name, raw_args):
    if state["bound"]:
        return f"turn bound reached: {state['bound']}; write your review verdict now"
    try:
        parsed = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except ValueError:
        return "error: tool arguments must be a JSON object"
    if not isinstance(parsed, dict):
        return "error: tool arguments must be a JSON object"
    if name == "read":
        return _tool_read(state, parsed)
    if name == "grep":
        return _tool_grep(state, parsed)
    if name == "glob":
        return _tool_glob(state, parsed)
    if name == "list":
        return _tool_list(state, parsed)
    return f"error: unknown tool {name!r}"


def _ask_provider(opener, url, key, payload, timeout):
    body = json.dumps(payload).encode(UTF8)
    request = urllib.request.Request(url + "/chat/completions", data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        "User-Agent": f"ao/{__version__}"})
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode(UTF8, "replace"))


def _write_answer(output_path, text, rounds=0, read_bytes=0):
    with open(output_path, "w", encoding=UTF8, newline="\n") as fh:
        fh.write(text)
    try:
        with open(output_path + ".tool.json", "w", encoding=UTF8, newline="\n") as fh:
            json.dump({"rounds": int(rounds), "read_bytes": int(read_bytes)}, fh)
    except OSError:
        pass


def main(argv=None):
    A.utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--model", required=True, help="<provider>/<model>, the provider named in review.api_providers")
    parser.add_argument("--diff-file", required=True, help="the candidate ao wrote; the prompt carries the same diff")
    parser.add_argument("--output", required=True, help="where the answer is written")
    parser.add_argument("--tree", default=None, help="the unpacked candidate tree for read-only review tools")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for the answer")
    parser.add_argument("prompt", help="the review prompt, or - to read it from standard input")
    args = parser.parse_args(argv)

    def refuse(text, code=USAGE):
        print(f"{parser.prog}: {text}", file=sys.stderr)
        return code

    provider, _, model = args.model.partition("/")
    if not provider or not model:
        return refuse(f"--model is {args.model!r}; it names <provider>/<model>")
    from . import settings
    try:
        table = providers(settings.get({}, "review.api_providers"))
    except ValueError as exc:
        return refuse(str(exc))
    if provider not in table:
        return refuse(f"review.api_providers names no {provider}; the machine's providers are "
                      f"{', '.join(sorted(table)) or 'none'}")
    url, variable = table[provider]
    key = os.environ.get(variable)
    if not key:
        return refuse(f"{variable} is not set, and it holds {provider}'s key")
    # A header carries printable ASCII alone, and the error that refuses one names its value: a key holding a line
    # break was printed with it (API-REVIEWER-2). What the key holds is not said.
    if not re.fullmatch(r"[\x20-\x7e]+", key):
        return refuse(f"{variable} holds a character outside printable ASCII, which no header carries")
    if not os.path.isfile(args.diff_file):
        return refuse(f"the candidate {args.diff_file} is not there")
    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    # A provider on this machine is asked directly. A proxy the environment named took a request for 127.0.0.1, its
    # key in plain text, to wherever the proxy was (API-REVIEWER-2); one over HTTPS passes a proxy as a tunnel, which
    # carries the key encrypted. No redirect is followed: it is an answer, not a place to send the key.
    handlers = []
    if urllib.parse.urlparse(url).hostname in LOOPBACK:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = outbound.opener(*handlers)

    messages = [{"role": "user", "content": prompt}]
    use_tools = args.tree is not None
    offer_tools = use_tools
    state = {
        "tree": args.tree,
        "config_names": A.project_config_names() if use_tools else {},
        "rounds": 0,
        "read_bytes": 0,
        "bound": None,
        "started": time.monotonic(),
        "turn_seconds": min(float(args.timeout), TURN_MAX_SECONDS),
    }

    while True:
        payload = {"model": model, "messages": messages}
        if offer_tools:
            payload["tools"] = TOOLS
        req_timeout = max(1.0, float(args.timeout) - (time.monotonic() - state["started"])) if use_tools else args.timeout
        try:
            answer = _ask_provider(opener, url, key, payload, req_timeout)
        except urllib.error.HTTPError as exc:
            if outbound.is_redirect(exc.code):
                # The status, and what to do: the key goes to the URL review.api_providers names and nowhere else
                # (HUNT-LEADS-SEC).
                return refuse(f"{provider} answered HTTP {exc.code}, a redirect, which ao does not follow with the key: "
                              "write the URL its endpoint moved to in review.api_providers", FAILED)
            try:
                said = _scrub(exc.read(300).decode(UTF8, "replace"), key) if exc.fp else ""
            except Exception:                       # a body cut short is no reason to say less than the status
                said = ""
            detail = f": {' '.join(said.split())}" if said else ""
            if offer_tools and exc.code in (400, 404, 422, 501):
                return refuse(f"{provider} refused the review tools for {model} (HTTP {exc.code}){detail}", FAILED)
            return refuse(f"{provider} answered HTTP {exc.code}{detail}", FAILED)
        except Exception as exc:
            # Every failure of the exchange is said with the key taken out: http.client's protocol errors are none of
            # URLError, OSError or ValueError, and a provider that put the key in a malformed status line had it printed
            # in the traceback (API-REVIEWER-3).
            return refuse(f"{provider} could not be asked ({type(exc).__name__}: {_scrub(str(exc), key)})", FAILED)

        if not isinstance(answer, dict) or ("error" in answer and "choices" not in answer):
            if offer_tools:
                return refuse(f"{provider} refused the review tools for {model}", FAILED)
            return refuse(_no_answer(provider, model, answer if isinstance(answer, dict) else {}), FAILED)

        if not use_tools:
            break

        try:
            message = answer["choices"][0]["message"]
            tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        except (KeyError, IndexError, TypeError):
            tool_calls = None

        if not tool_calls:
            break
        if not offer_tools or not isinstance(tool_calls, list):
            return refuse(f"{model} on {provider} requested tool calls when no tools were offered", FAILED)

        state["rounds"] += 1
        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls})
        for call in tool_calls:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                return refuse(f"{provider} returned a malformed tool call for {model}", FAILED)
            if state["bound"] is None and (time.monotonic() - state["started"]) >= state["turn_seconds"]:
                state["bound"] = f"wall-time limit ({state['turn_seconds']:g}s) reached"
            if state["bound"] is None and state["read_bytes"] >= TURN_MAX_READ_BYTES:
                state["bound"] = f"total bytes read limit ({TURN_MAX_READ_BYTES} bytes) reached"
            fn = call["function"]
            out = _dispatch_tool(state, str(fn.get("name") or ""), fn.get("arguments") or "{}")
            messages.append({"role": "tool", "tool_call_id": str(call.get("id") or ""), "content": out})

        if state["bound"] is None and (time.monotonic() - state["started"]) >= state["turn_seconds"]:
            state["bound"] = f"wall-time limit ({state['turn_seconds']:g}s) reached"
        if state["bound"] is None and state["read_bytes"] >= TURN_MAX_READ_BYTES:
            state["bound"] = f"total bytes read limit ({TURN_MAX_READ_BYTES} bytes) reached"
        if state["bound"] is None and state["rounds"] >= TURN_MAX_ROUNDS:
            state["bound"] = f"tool round limit ({TURN_MAX_ROUNDS} rounds) reached"
        if state["bound"] is not None:
            messages[-1]["content"] += (f"\n\n[turn bound reached: {state['bound']}; no tools are offered on the "
                                        "next request — provide your final review verdict now]")
            offer_tools = False

    text = _content(answer)
    if not text.strip():
        return refuse(_no_answer(provider, model, answer), FAILED)
    _write_answer(args.output, text, rounds=state["rounds"], read_bytes=state["read_bytes"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
