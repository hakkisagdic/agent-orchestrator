"""Every MCP tool, ao path and setting a document names is one ao has (DOCS-NAMES).

docs/mcp.md listed nine MCP tools; seven of them the server never had, and ten it has were not
in that table. docs/gates.md declared gates in `.ao/gates.yml` with fields ao never reads, and
docs/slices.md sent a restarted session to `.ao/ledger/slices.jsonl`, which nothing writes.
tests/test_docs_commands.py cannot see any of it, since none of it is an `ao` invocation. This
reads the same documents, under the same not-built markers, for four more kinds of name:

- a tool: `ao_<name>` anywhere, except in a dotted field such as `directives.ao_files` or as a
  key (`"ao_files": …`). The server in src/ao/mcp.py registers it. The table in docs/mcp.md
  that has an Access column lists every tool the server registers, and each class is what the
  tool is seen to do when it is called in a copy of a scratch project with each value its schema
  offers: run when it refuses without --allow-verify, write when it changes a file of the
  project, read otherwise;
- a path: `.ao/…` or `~/.ao/…` anywhere, and each entry of a tree drawn under one. The code
  names it in the same place, `~/.ao` on the machine or `.ao` in a project: in an os.path.join
  of literals, constants, loop values and what a function returns, or in a string it writes or
  prints. A docstring names nothing. `<placeholder>` stands for one name, and a directory is
  named when a path the code names lies inside it;
- a file's content: a block marked json, jsonc, yaml or toml shows a file when its first line is
  a comment naming the file, or when the paragraph directly above names one file and ends with
  a colon. A JSON file is shown as JSON, and a YAML or TOML file ao does not name must not stand
  in for the JSON file of that name that ao does read. A gates example holds only what
  `ao verify` reads, and a config or settings example must pass the checks ao runs on one:
  settings.problems, the role table and the capability matrix;
- a setting, in `ao config get|set|unset <key> [<value>]`: settings.py registers the key, a value
  that is set is one its kind accepts, and a machine setting is changed with --machine. The
  parser accepts all of these, and `ao config` refuses them.

A marker excuses a name the same way it excuses an invocation, and a marker must excuse something
from one of the two readings, or it has outlived its design.
"""
import ast
import collections
import functools
import itertools
import json
import re
import shutil
from pathlib import Path

from ao import cli, lib as A, matrix, mcp, settings as S, telegram
from tests.test_docs_commands import FENCE, ROOT, _documents, _marker_problems, _option_like, _placeholder, _read

ACCESS = ("read", "write", "run")
TOOL = re.compile(r"(?<![\w.$/-])ao_[a-z][a-z0-9_]*(?![a-z0-9_])(?!\"?\s*:)")
DOC_PATH = re.compile(r"(?<![\w.~/$-])(?:~/)?\.ao/[^\s`'\"()\[\]|,;:]*")
CODE_PATH = re.compile(r"(?<![\w.~/$-])(?:~/)?\.ao/[\w.<>{}*/-]*")
TREE_ROOT = re.compile(r"^\s*((?:~/)?\.ao/\S*/)(?:\s|$)")
TREE_ENTRY = re.compile(r"^((?:[│ ] {3})*)[├└]── ([^\s#]+)")
WILD = re.compile(r"(<[^<>/]+>|\{[^{}/]*\}|\*)")
DATA_BLOCKS = {"json", "jsonc", "yaml", "yml", "toml"}
SHOWN_FIRST = re.compile(r"^\s*(?:#|//)\s*((?:~/)?\.ao/\S*[^\s/])\s*$")
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

UNKNOWN, HOME = "\x00", "\x01"      # a piece the code computes, and the home directory however it is reached

# What `ao verify` and `ao merge-check` read of `.ao/gates.json` (parts/cli_authority.py), with
# what the filter probe and the inputs rule read of a gate (parts/lib_gates.py). `expect: empty`
# passes on no output; any other value leaves the verdict to the exit code, and `ao init`
# writes that value as exit_zero. A summary is a regex with pass and fail groups (lib.gate_counts).
GATES_FILE = ("gates", "profiles", "default_profile")
GATE = {"run": str, "expect": str, "timeout": (int, float), "summary": str, "min_tests": int, "inputs": list}
EXPECT = ("exit_zero", "empty")

Finding = collections.namedtuple("Finding", "document line kind text")


# ---- tools -----------------------------------------------------------------------------------

def _tools(text):
    """(line, name) of each MCP tool a document names."""
    return [(number, match.group(0)) for number, line in enumerate(text.split("\n"), 1)
            for match in TOOL.finditer(line)]


def _access_table(text):
    """{tool: (line, access)} from each table whose header has a Tool and an Access column."""
    rows, header = {}, None
    for number, line in enumerate(text.split("\n"), 1):
        if not line.lstrip().startswith("|"):
            header = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header is None:
            header = [cell.lower() for cell in cells]
        elif {"tool", "access"} <= set(header) and len(cells) == len(header) \
                and not all(re.fullmatch(r":?-*:?", cell) for cell in cells):
            rows[cells[header.index("tool")].strip("`")] = (number, cells[header.index("access")].strip("*` ").lower())
    return rows


def _table_problems(text, tools, observed):
    """What the access table of docs/mcp.md says wrongly about the tools the server registers."""
    table = _access_table(text)
    problems = [f"docs/mcp.md lists no access for {tool}" for tool in sorted(tools) if tool not in table]
    for tool, (line, access) in sorted(table.items()):
        if tool not in tools:
            problems.append(f"docs/mcp.md:{line}: the server registers no {tool}")
        elif access not in ACCESS:
            problems.append(f"docs/mcp.md:{line}: {tool} is {access!r}; access is one of {', '.join(ACCESS)}")
        elif access != observed[tool]:
            problems.append(f"docs/mcp.md:{line}: {tool} is listed as {access}, and it is seen to {observed[tool]}")
    return problems


def _arguments(tool, message):
    """One call with what a tool's schema requires, then one for each value of each enum it offers.

    A required string gets the name of the message waiting in the mailbox: that is what ao_ack
    must be given, and it serves the other tools as a summary or a question.
    """
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    fill = {"string": message, "integer": 2, "number": 2, "boolean": True, "array": ["yes", "no"], "object": {}}
    base = {name: fill[(properties.get(name) or {}).get("type", "string")] for name in schema.get("required") or []}
    return [base] + [dict(base, **{name: value}) for name, spec in properties.items()
                     for value in spec.get("enum") or []]


def _contents(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(Path(root).rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts}


def _observed_access(project, calls):
    """{tool: access} as each tool behaves, called on a fresh copy of a scratch project for each set of arguments."""
    implementer, architect = A.mail_names(project)
    message = f"20260917-0900-{architect}-to-{implementer}-DECISION-scratch.md"
    Path(project["root"], project["mailbox"], message).write_text("# scratch\n\nApply it, then acknowledge it.\n",
                                                                   encoding="utf-8")
    seen = {}
    for tool in mcp.TOOLS:
        classes = set()
        for number, arguments in enumerate(_arguments(tool, message)):
            root = Path(calls) / f"{tool['name']}-{number}"
            shutil.copytree(project["root"], root)
            A.project_key(str(root))                  # a project's name is written the first time any command sees it
            before = _contents(root)
            result = mcp.call(tool["name"], arguments, dict(project, root=str(root)), False)
            if isinstance(result, dict) and "--allow-verify" in str(result.get("error", "")):
                classes.add("run")
            elif _contents(root) != before:
                classes.add("write")
        seen[tool["name"]] = next((access for access in ("run", "write") if access in classes), "read")
    return seen


# ---- paths a document names ------------------------------------------------------------------

def _paths(text):
    """(line, path) of each ao path a document names, and of each entry of a tree drawn under one."""
    found, tree = [], None
    for number, line in enumerate(text.split("\n"), 1):
        found.extend((number, match.group(0).rstrip(".")) for match in DOC_PATH.finditer(line))
        entry = TREE_ENTRY.match(line)
        if entry and tree:
            del tree[len(entry.group(1)) // 4 + 1:]
            found.append((number, tree[-1] + entry.group(2)))
            if entry.group(2).endswith("/"):
                tree.append(tree[-1] + entry.group(2))
            continue
        root = TREE_ROOT.match(line)
        tree = [root.group(1)] if root else None
    return found


def _segment(segment, wildcard):
    return "".join(wildcard if WILD.fullmatch(piece) else re.escape(piece) for piece in WILD.split(segment) if piece)


def _names(path, pattern):
    """Whether a path the code names, as a pattern, is the path a document names, or lies inside it."""
    wanted, named = [part for part in path.split("/") if part], [part for part in pattern.split("/") if part]
    if (wanted[0] == "~") != (named[0] == "~") or len(named) < len(wanted):
        return False
    for mine, theirs in zip(wanted, named):
        if theirs == "*":                              # a name the code computes: only a placeholder stands for it
            if not WILD.fullmatch(mine):
                return False
        elif not (re.fullmatch(_segment(theirs, "[^/]*"), WILD.sub("x", mine))
                  or re.fullmatch(_segment(mine, "[^/]+"), WILD.sub("x", theirs))):
            return False
    return len(named) == len(wanted) or path.endswith("/")


# ---- paths the code names --------------------------------------------------------------------

class _Code:
    """What the Python under one source directory builds its paths from: literals, constants, loops, returns."""

    def __init__(self, source):
        self.trees = [ast.parse(path.read_text(encoding="utf-8")) for path in sorted(Path(source).rglob("*.py"))]
        self.constants, self.functions, self.enclosing = {}, {}, {}
        self.bindings, self.returns, self.busy = {}, {}, set()
        for tree in self.trees:
            statements = list(tree.body)
            while statements:                          # the module's own level, through if and try
                node = statements.pop()
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.constants.setdefault(target.id, []).append(node.value)
                elif isinstance(node, (ast.If, ast.Try)):
                    statements.extend(child for child in ast.iter_child_nodes(node) if isinstance(child, ast.stmt))
                    statements.extend(child for handler in getattr(node, "handlers", []) for child in handler.body)
            stack = [(tree, None)]
            while stack:                               # the function each node is read in
                node, scope = stack.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions.setdefault(node.name, []).append(node)
                inner = node if isinstance(node, SCOPES) else scope
                for child in ast.iter_child_nodes(node):
                    self.enclosing[child] = inner
                    stack.append((child, inner))

    def _bound_in(self, scope, name):
        """How `name` is bound in one function, or None.

        Each binding is ("param", default), ("value", expr) or ("each", iterable, index).
        """
        if (scope, name) not in self.bindings:
            found, arguments = [], scope.args
            positional = arguments.posonlyargs + arguments.args
            defaults = dict(zip([arg.arg for arg in positional][::-1], arguments.defaults[::-1]))
            defaults.update((arg.arg, default) for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults))
            found.extend(("param", defaults.get(name)) for arg in positional + arguments.kwonlyargs if arg.arg == name)
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        found.extend(self._targets(target, ("value", node.value), name))
                elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name) \
                        and node.target.id == name and node.value is not None:
                    found.append(("value", node.value))
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    found.extend(self._targets(node.target, ("each", node.iter, None), name))
            self.bindings[scope, name] = tuple(found) or None
        return self.bindings[scope, name]

    def _targets(self, target, binding, name):
        if isinstance(target, ast.Name):
            return [binding] if target.id == name else []
        if not isinstance(target, (ast.Tuple, ast.List)):
            return []
        if binding[0] == "each":
            return [("each", binding[1], index) for index, element in enumerate(target.elts)
                    if isinstance(element, ast.Name) and element.id == name]
        if isinstance(binding[1], (ast.Tuple, ast.List)) and len(binding[1].elts) == len(target.elts):
            return [found for element, part in zip(target.elts, binding[1].elts)
                    for found in self._targets(element, ("value", part), name)]
        return []

    def _resolve(self, node):
        """The bindings of the name a node reads: the innermost function binding it, else every module's."""
        if isinstance(node, ast.Attribute):
            return tuple(("value", expr) for expr in self.constants.get(node.attr, []))
        scope = self.enclosing.get(node)
        while scope is not None:
            bound = self._bound_in(scope, node.id)
            if bound is not None:
                return bound
            scope = self.enclosing.get(scope)
        return tuple(("value", expr) for expr in self.constants.get(node.id, []))

    def _elements(self, node, depth, indexing=False):
        """The element expressions of the literal container an expression is, or is bound to."""
        if depth > 12:
            return []
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return list(node.elts)
        if isinstance(node, ast.Dict):
            return list(node.values) if indexing else [key for key in node.keys if key is not None]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args \
                and node.func.id in ("sorted", "list", "tuple", "set", "reversed"):
            return self._elements(node.args[0], depth + 1, indexing)
        if isinstance(node, (ast.Name, ast.Attribute)):
            return [element for binding in self._resolve(node) if binding[0] == "value"
                    for element in self._elements(binding[1], depth + 1, indexing)]
        return []

    def _bound(self, binding, depth):
        if binding[0] == "value":
            return self.values(binding[1], depth + 1)
        if binding[0] == "param":
            return [UNKNOWN] + (self.values(binding[1], depth + 1) if binding[1] is not None else [])
        _, iterable, index = binding
        out = []
        for element in self._elements(iterable, depth + 1):
            if index is None:
                out.extend(self.values(element, depth + 1))
            elif isinstance(element, (ast.Tuple, ast.List)) and index < len(element.elts):
                out.extend(self.values(element.elts[index], depth + 1))
        return out or [UNKNOWN]

    def _returned(self, name):
        if name not in self.returns:
            self.returns[name] = [UNKNOWN]             # a recursion names nothing more
            self.returns[name] = [value for function in self.functions[name] for node in ast.walk(function)
                                  if isinstance(node, ast.Return) and node.value is not None
                                  for value in self.values(node.value)] or [UNKNOWN]
        return self.returns[name]

    def values(self, node, depth=0):
        """The strings an expression may evaluate to, UNKNOWN standing in for what it computes."""
        if depth > 12 or node in self.busy:
            return [UNKNOWN]
        self.busy.add(node)
        try:
            return self._values(node, depth)
        finally:
            self.busy.discard(node)

    def _values(self, node, depth):
        more = functools.partial(self.values, depth=depth + 1)
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else [UNKNOWN]
        if isinstance(node, ast.JoinedStr):
            return ["".join(part.value if isinstance(part, ast.Constant) else "*" for part in node.values)]
        if isinstance(node, (ast.Name, ast.Attribute)):
            if (node.id if isinstance(node, ast.Name) else node.attr) == "HOME":
                return [HOME]
            return [value for binding in self._resolve(node) for value in self._bound(binding, depth)] or [UNKNOWN]
        if isinstance(node, (ast.BoolOp, ast.IfExp)):
            return [value for branch in (node.values if isinstance(node, ast.BoolOp) else [node.body, node.orelse])
                    for value in more(branch)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            glue = "" if isinstance(node.op, ast.Add) else "/"
            return [left + glue + right for left, right in
                    itertools.islice(itertools.product(more(node.left), more(node.right)), 64)]
        if isinstance(node, ast.Subscript):
            return [value for element in self._elements(node.value, depth + 1, indexing=True)
                    for value in more(element)] or [UNKNOWN]
        if not isinstance(node, ast.Call) or any(isinstance(arg, ast.Starred) for arg in node.args):
            return [UNKNOWN]
        func, first = node.func, node.args[0] if node.args else None
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        owner = func.value if isinstance(func, ast.Attribute) else None
        if name == "join" and _os_path(owner):
            return ["/".join(piece for piece in pieces if piece) for pieces in
                    itertools.islice(itertools.product(*(more(arg) for arg in node.args)), 256)]
        if name == "expanduser" and first is not None:
            return [HOME + value[1:] if value.startswith("~") else value for value in more(first)]
        if name in ("abspath", "realpath", "normpath", "fspath", "str", "Path") and first is not None:
            return more(first)
        if name == "dirname" and first is not None:
            return [value.rsplit("/", 1)[0] if "/" in value else UNKNOWN for value in more(first)]
        if name == "format" and owner is not None:
            return [re.sub(r"\{[^{}]*\}", "*", value) for value in more(owner)]
        if name == "getenv" or name == "get" and isinstance(owner, ast.Attribute) and owner.attr == "environ":
            return [UNKNOWN] + (more(node.args[1]) if len(node.args) > 1 else [])
        return self._returned(name) if name in self.functions else [UNKNOWN]

    def named(self):
        """Every path under an .ao directory the code names, as `~/.ao/…` or `.ao/…`, `*` where it computes."""
        found = set()
        for tree in self.trees:
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                          and node.body and isinstance(node.body[0], ast.Expr)
                          and isinstance(node.body[0].value, ast.Constant)}
            formatted = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr in ("join", "expanduser") and _os_path(node.func.value):
                    values = self.values(node)
                elif isinstance(node, (ast.Tuple, ast.List)) and node.elts \
                        and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts) \
                        and node.elts[0].value == ".ao":
                    values = ["/".join(element.value for element in node.elts)]
                elif isinstance(node, ast.JoinedStr) or isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and id(node) not in docstrings and id(node) not in formatted:
                    values = [match.group(0).rstrip(".") for value in self.values(node)
                              for match in CODE_PATH.finditer(value)]
                else:
                    continue
                found.update(pattern for pattern in map(_pattern, values) if pattern)
        return found


def _os_path(node):
    return isinstance(node, ast.Attribute) and node.attr == "path" and isinstance(node.value, ast.Name) \
        and node.value.id == "os"


def _pattern(value):
    parts = [part for part in value.replace(UNKNOWN, "*").split("/") if part not in ("", ".")]
    if ".ao" not in parts:
        return None
    at = parts.index(".ao")
    return ("~/" if at == 1 and parts[0] in (HOME, "~") else "") + "/".join(parts[at:])


@functools.lru_cache(maxsize=None)
def _code_paths(source):
    return frozenset(_Code(source).named())


# ---- a file a document shows, and what it holds ------------------------------------------------

def _shown(text):
    """(line, file, language, body) of each data block that shows an ao file."""
    lines, found, paragraph, apart, i = text.split("\n"), [], [], False, 0
    while i < len(lines):
        fence = FENCE.match(lines[i])
        if not fence:
            if lines[i].strip():                       # the paragraph above a block, blank lines between allowed
                paragraph, apart = ([] if apart else paragraph) + [lines[i]], False
            else:
                apart = True
            i += 1
            continue
        closing = re.compile(r"^\s*%s{%d,}\s*$" % (re.escape(fence.group(1)[0]), len(fence.group(1))))
        end = next((n for n in range(i + 1, len(lines)) if closing.match(lines[n])), len(lines))
        language, body = fence.group(2).lower(), lines[i + 1:end]
        first = SHOWN_FIRST.match(body[0]) if body else None
        introduced = {path.rstrip(".") for path in DOC_PATH.findall("\n".join(paragraph)) if not path.endswith("/")} \
            if paragraph and paragraph[-1].rstrip().endswith(":") else set()
        if language in DATA_BLOCKS and (first or len(introduced) == 1):
            found.append((i + 1, first.group(1) if first else introduced.pop(), language, body))
        paragraph, apart, i = [], False, end + 1
    return found


def _uncommented(text):
    """JSON text without its `//` comments."""
    out, quoted, i = [], False, 0
    while i < len(text):
        if quoted:
            out.append(text[i:i + 2] if text[i] == "\\" else text[i])
            quoted = text[i] != '"'
            i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else len(text)
        else:
            quoted = text[i] == '"'
            out.append(text[i])
            i += 1
    return "".join(out)


def _content_problems(file, language, body, code):
    """What a block showing an ao file holds that ao does not read there, as sentences."""
    if not file.endswith((".json", ".jsonl")):
        twin = re.sub(r"\.(ya?ml|toml)$", ".json", file)
        named = [path for path in (file, twin) if any(_names(path, pattern) for pattern in code)]
        return [f"ao reads {twin}, as JSON, and no {file}"] if named == [twin] and twin != file else []
    if language not in ("json", "jsonc"):
        return [f"ao reads {file} as JSON, and this block is {language}"]
    text, decoder, documents, at = _uncommented("\n".join(body)), json.JSONDecoder(), [], 0
    try:
        while text[at:].strip():
            at += len(text[at:]) - len(text[at:].lstrip())
            document, at = decoder.raw_decode(text, at)
            documents.append(document)
    except ValueError as error:
        return [f"ao reads {file} as JSON, and this block does not parse: {error}"]
    if file == ".ao/gates.json":
        return _gates_problems(documents[0])
    if file in (".ao/config.json", "~/.ao/settings.json"):
        return _config_problems(documents[0], machine=file.startswith("~"))
    return []


def _gates_problems(spec):
    if not isinstance(spec, dict):
        return ["gates are declared in one JSON object"]
    problems = [f"ao reads no {key!r} in .ao/gates.json" for key in spec if key not in GATES_FILE]
    gates, profiles = spec.get("gates"), spec.get("profiles")
    if not isinstance(gates, dict) or not gates:
        return problems + ["no gate is declared"]
    for name, gate in gates.items():
        if not isinstance(gate, dict) or not isinstance(gate.get("run"), str):
            problems.append(f"gate {name} has no run command")
            continue
        for field, value in gate.items():
            if field not in GATE:
                problems.append(f"gate {name}: ao reads no {field!r}")
            elif isinstance(value, bool) or not isinstance(value, GATE[field]):
                problems.append(f"gate {name}: {field} is {value!r}, which ao cannot use there")
            elif field == "expect" and value not in EXPECT:
                problems.append(f"gate {name}: ao tells only {' and '.join(EXPECT)} apart, not {value!r}")
            elif field == "inputs" and not all(isinstance(glob, str) and glob.strip() for glob in value):
                problems.append(f"gate {name}: inputs is a list of globs")
            elif field == "summary" and not {"pass", "fail"} <= set(_groups(value)):
                problems.append(f"gate {name}: summary is not a regex with pass and fail groups")
    if not isinstance(profiles, dict) or not profiles:
        return problems + ["no profile is declared"]
    for profile, names in profiles.items():
        if not isinstance(names, list) or not names or A.gate_definitions_digest_of(spec, profile) is None:
            problems.append(f"profile {profile} is not a list of declared gates")
    default = spec.get("default_profile", "quick")
    if default not in profiles:
        problems.append(f"`ao verify` without --profile runs {default!r}, and no profile has that name")
    return problems


def _groups(pattern):
    try:
        return re.compile(pattern).groupindex
    except re.error:
        return {}


def _config_problems(document, machine):
    """What ao finds wrong in a config example: settings.problems, the role table and the capability matrix."""
    if not isinstance(document, dict):
        return ["a config is one JSON object"]
    saved = S.machine_settings
    S.machine_settings = lambda: dict(document) if machine else {}
    try:
        problems = [text for _, text in S.problems({} if machine else document)]
    finally:
        S.machine_settings = saved
    if machine:
        return problems
    actors, roles = document.get("actors"), document.get("roles")
    if actors is not None or roles is not None:
        if not isinstance(actors, dict) or not isinstance(roles, dict):
            return problems + ["actors and roles are read together, each an object"]
        problems.extend(f"ao runs no {role} role" for role in roles if role not in A.ROLE_BLOCKS)
        problems.extend(f"the {role} role names {actor!r}, which is not an actor" for role, actor in roles.items()
                        if actor not in actors)
        kind = (document.get("repository") or {}).get("kind") or S.default("repository.kind")
        problems.extend(problem for problem in [A.assignment_problem(actors, roles, kind)] if problem)
    if "capability_matrix" in document:
        try:
            matrix.resolve(document)
        except (matrix.MatrixError, TypeError, ValueError) as error:
            problems.extend(getattr(error, "problems", None) or [str(error)])
    return problems


# ---- settings ----------------------------------------------------------------------------------

def _config_refusals(words):
    """What `ao config` refuses in one invocation's words; [] for any other command."""
    i = 1
    while i < len(words) and _option_like(words[i]):
        i += 2 if words[i] in (["-C"], ["--root"]) else 1
    if i >= len(words) or words[i] != ["config"]:
        return []
    rest = words[i + 1:]
    plain = [word for word in rest if not _option_like(word)]
    actions = [action for action in (plain[0] if plain else []) if action in ("get", "set", "unset")]
    keys = [key for key in (plain[1] if len(plain) > 1 and actions else []) if not _placeholder(key)]
    values = [value for value in (plain[2] if len(plain) > 2 else []) if not _placeholder(value)]
    problems = []
    for key in keys:
        if key not in S.SETTINGS:
            problems.append(f"ao config has no setting {key!r}")
            continue
        for action in actions:
            if action != "get" and S.SETTINGS[key].scope == "machine" and ["--machine"] not in rest:
                problems.append(f"{key} governs the whole machine, and ao config {action} refuses it without --machine")
            for value in values if action == "set" else []:
                try:
                    S.parse(key, value)
                except (TypeError, ValueError):
                    problems.append(f"{key} cannot be set to {value!r}: it is {S.expected(key)}")
    return problems


# ---- the documents -----------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _scan(root):
    """([Finding] for every name the documents under root show that ao does not have, [(document, Marker)])."""
    root = Path(root)
    tools, code = {tool["name"] for tool in mcp.TOOLS}, _code_paths(str(root / "src" / "ao"))
    findings, markers = [], []
    for name, path in _documents(root):
        text = path.read_text(encoding="utf-8")
        invocations, found = _read(text)
        markers.extend((name, marker) for marker in found)
        findings.extend(Finding(name, line, "tool", f"the MCP server registers no {tool}")
                        for line, tool in _tools(text) if tool not in tools)
        findings.extend(Finding(name, line, "path", f"ao names no {shown}")
                        for line, shown in _paths(text) if not any(_names(shown, pattern) for pattern in code))
        findings.extend(Finding(name, line, "content", f"{file}: {problem}")
                        for line, file, language, body in _shown(text)
                        for problem in _content_problems(file, language, body, code))
        findings.extend(Finding(name, inv.line, "setting", f"`{inv.text}`: {problem}") for inv in invocations
                        if not isinstance(inv.words, ValueError) for problem in _config_refusals(inv.words))
    return findings, markers


def _covered(marker, finding):
    return any(start <= finding.line <= end for start, end in marker.covers)


def _unexcused(root, kind):
    findings, markers = _scan(str(root))
    return [f"{f.document}:{f.line}: {f.text}" for f in findings if f.kind == kind
            and not any(name == f.document and _covered(marker, f) for name, marker in markers)]


def _excusing(root):
    """(document, line) of each marker that excuses a name ao does not have."""
    findings, markers = _scan(str(root))
    return {(name, marker.line) for name, marker in markers
            if any(f.document == name and _covered(marker, f) for f in findings)}


def test_every_mcp_tool_a_document_names_is_one_the_server_registers():
    problems = _unexcused(ROOT, "tool")

    assert problems == [], "\n".join(problems)


def test_docs_mcp_lists_every_tool_with_the_access_it_is_seen_to_have(project, tmp_path, monkeypatch):
    monkeypatch.setattr(telegram, "CONF", str(tmp_path / "no-telegram.json"))      # nothing reaches a phone
    monkeypatch.setattr(A, "provider_window", lambda *args, **kwargs: None)         # nor asks a provider
    text = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")

    observed = _observed_access(project, tmp_path / "calls")

    assert (observed["ao_verify"], observed["ao_ack"], observed["ao_board"]) == ("run", "write", "read")
    assert _table_problems(text, set(observed), observed) == []


def test_every_ao_path_a_document_names_is_one_the_code_names():
    problems = _unexcused(ROOT, "path")

    assert problems == [], "\n".join(problems)


def test_a_file_a_document_shows_is_in_the_format_and_fields_ao_reads():
    problems = _unexcused(ROOT, "content")

    assert problems == [], "\n".join(problems)


def test_every_ao_config_example_names_a_setting_and_a_value_ao_config_accepts():
    problems = _unexcused(ROOT, "setting")

    assert problems == [], "\n".join(problems)


def test_a_not_built_marker_gives_a_reason_and_excuses_only_what_ao_does_not_have():
    problems = _marker_problems(ROOT, cli.build_parser(), _excusing(ROOT))

    assert problems == [], "\n".join(problems)


CODE = '''"""A docstring names nothing, not even .ao/policy.yml."""
import os

HOME = os.path.expanduser("~")
LEDGERS = ("notices.jsonl", "merges.jsonl")
LOGS = {"nudge": "nudge-{key}.log"}
CHAINED = (("grant", (".ao", "ledger", "authority.jsonl")),)


def ledger_dir(root):
    return os.path.join(root, ".ao", "ledger")


def stores(root, key, state=None):
    state = state or os.path.join(HOME, ".ao")
    return [os.path.join(ledger_dir(root), name) for name in LEDGERS] + [
        os.path.join(state, LOGS["nudge"].format(key=key)), os.path.join(state, f"cycles-{key}.jsonl"),
        os.path.join(os.path.expanduser("~"), ".ao", "checkpoints.json"), os.path.join(root, ".ao", key)]


def config(root):
    return os.path.join(root, ".ao", "config.json"), os.path.join(root, ".ao", "gates.json")


def hint(key):
    return f"refill .ao/backlog.md, or read ~/.ao/watchdog-{key}.log"
'''

SAMPLE = """# Names

`ao_status` and `ao_board` exist; `ao_resume` does not, and `directives.ao_files` is a field.

State: `.ao/ledger/notices.jsonl`, `~/.ao/nudge-<project>.log`, `~/.ao/cycles-<project>.jsonl`,
`.ao/ledger/`, `.ao/<key>` and `.ao/backlog.md` are named; `~/.ao/notices.jsonl`, `.ao/<x>/y.json`
and `.ao/policy.yml` are not.

```
.ao/ledger/
├── authority.jsonl      # named
└── slices.jsonl         # not named
```

```yaml
# .ao/config.json
alarms: { red_after_minutes: 60 }
```

```yaml
# .ao/gates.yml
gates: { full: { run: "npm test", "ao_files": [] } }
```

So `.ao/gates.json` reads:

```json
{"gates": {"full": {"run": "npm test", "timeout": "30m", "serialise": true, "expect": "all_pass"}},
 "profiles": {"full": ["full", "lint"]}}
```

```jsonc
// .ao/config.json
{"alarms": {"red_after_minute": 60}, "actors": {"kiro": {"adapter": "kiro"}},
 "roles": {"implementer": "kiro", "reviewer": "kiro", "tester": "kiro"}}
```

```bash
ao config set transport keyflip
ao config set round_budget lots
ao config set quota.block_percent 90
ao config set quota.block_percent 90 --machine
```

As a synopsis, `ao config [list|get|set|unset] [<setting> <value>] [--machine]`.

<!-- not built: sessions are resumed by a design -->
Not built yet: `ao_resume` and `.ao/lanes.json`.

<!-- not built: a design that names nothing ao lacks -->
Not built yet: `.ao/backlog.md` holding lanes.
"""


def test_the_reading_sees_tools_paths_trees_shown_files_settings_and_markers(tmp_path):
    for name, text in (("src/ao/paths.py", CODE), ("docs/sample.md", SAMPLE), ("README.md", ""), ("README.tr.md", ""),
                       ("src/ao/skill/SKILL.md", ""), ("docs/lessons.md", "`ao_mail_send` in `.ao/roles.yml`\n")):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    assert _code_paths(str(tmp_path / "src" / "ao")) == {
        ".ao/ledger", ".ao/ledger/notices.jsonl", ".ao/ledger/merges.jsonl", ".ao/ledger/authority.jsonl", ".ao/*",
        ".ao/config.json", ".ao/gates.json", ".ao/backlog.md", "~/.ao", "~/.ao/nudge-*.log", "~/.ao/cycles-*.jsonl",
        "~/.ao/checkpoints.json", "~/.ao/watchdog-*.log"}
    assert [f"{f.document}:{f.line}: {f.text}" for f in _scan(str(tmp_path))[0]] == [
        "docs/sample.md:3: the MCP server registers no ao_resume",
        "docs/sample.md:48: the MCP server registers no ao_resume",
        "docs/sample.md:6: ao names no ~/.ao/notices.jsonl",
        "docs/sample.md:6: ao names no .ao/<x>/y.json",
        "docs/sample.md:7: ao names no .ao/policy.yml",
        "docs/sample.md:12: ao names no .ao/ledger/slices.jsonl",
        "docs/sample.md:21: ao names no .ao/gates.yml",
        "docs/sample.md:48: ao names no .ao/lanes.json",
        "docs/sample.md:15: .ao/config.json: ao reads .ao/config.json as JSON, and this block is yaml",
        "docs/sample.md:20: .ao/gates.yml: ao reads .ao/gates.json, as JSON, and no .ao/gates.yml",
        "docs/sample.md:27: .ao/gates.json: gate full: timeout is '30m', which ao cannot use there",
        "docs/sample.md:27: .ao/gates.json: gate full: ao reads no 'serialise'",
        "docs/sample.md:27: .ao/gates.json: gate full: ao tells only exit_zero and empty apart, not 'all_pass'",
        "docs/sample.md:27: .ao/gates.json: profile full is not a list of declared gates",
        "docs/sample.md:27: .ao/gates.json: `ao verify` without --profile runs 'quick', and no profile has that name",
        "docs/sample.md:32: .ao/config.json: alarms.red_after_minute in the project settings is not a setting ao "
        "reads; `ao config list` shows the names",
        "docs/sample.md:32: .ao/config.json: ao runs no tester role",
        "docs/sample.md:32: .ao/config.json: the reviewer and the implementer would both be kiro; no actor reviews "
        "its own work",
        "docs/sample.md:39: `ao config set transport keyflip`: ao config has no setting 'transport'",
        "docs/sample.md:40: `ao config set round_budget lots`: round_budget cannot be set to 'lots': it is a whole "
        "number of at least 1",
        "docs/sample.md:41: `ao config set quota.block_percent 90`: quota.block_percent governs the whole machine, "
        "and ao config set refuses it without --machine",
    ]
    assert _unexcused(tmp_path, "tool") == ["docs/sample.md:3: the MCP server registers no ao_resume"]
    assert _unexcused(tmp_path, "path")[-1] == "docs/sample.md:21: ao names no .ao/gates.yml"
    assert _excusing(tmp_path) == {("docs/sample.md", 47)}
    assert _marker_problems(tmp_path, cli.build_parser(), _excusing(tmp_path)) == [
        "docs/sample.md:50: a not-built marker excuses nothing: no command the parser refuses, no name ao lacks"]
    assert _table_problems("| Tool | Access | What |\n|---|---|---|\n| `ao_status` | read | x |\n"
                           "| `ao_ack` | **drive** | x |\n| `ao_verify` | read | x |\n| `ao_resume` | run | x |\n",
                           {"ao_status", "ao_ack", "ao_verify", "ao_board"},
                           {"ao_status": "read", "ao_ack": "write", "ao_verify": "run", "ao_board": "read"}) == [
        "docs/mcp.md lists no access for ao_board",
        "docs/mcp.md:4: ao_ack is 'drive'; access is one of read, write, run",
        "docs/mcp.md:6: the server registers no ao_resume",
        "docs/mcp.md:5: ao_verify is listed as read, and it is seen to run",
    ]
