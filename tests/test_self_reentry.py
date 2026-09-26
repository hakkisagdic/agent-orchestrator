"""A child that re-enters ao as `-m ao` is handed the path this ao was imported from (SELF-REENTRY).

Three places start such a child: the review runner, the watchdog's hunt and the MCP server's verify.
Each ran `sys.executable -m ao` and said nothing about where ao lives, so from a clone - ao's own
documented quickstart, a symlink to `bin/ao` on PATH with nothing installed - the child died on "No
module named ao". The review died silently: its log held that one line from the interpreter, and the
review stood at "running" until it was declared lost 34 minutes later.

The first guard over them read those three files by name and knew one spelling, the argv written
into the call as a list: a fourth re-entry in another file, or one whose argv was bound to a name
first, could leave the environment out and still pass (REENTRY-GUARD-WIDE). The guard reads
every module under src/ao as a syntax tree now. A call handed an argv holding `-m ao` - written
into it, joined with `+`, unpacked with `*` or bound to a name the call sees - must spell out
`env=` built by self_child_env. A list holding `-m ao` that the guard cannot follow to the call it
is handed fails too, so a spelling the guard does not read fails instead of passing unseen.
"""
import ast
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap

import pytest

import ao
from ao import lib as A

PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(ao.__file__)))


def test_the_child_environment_names_the_directory_this_ao_was_imported_from():
    env = A.self_child_env({"PATH": "/usr/bin"})

    assert env["PYTHONPATH"].split(os.pathsep)[0] == PACKAGE_PARENT
    assert env["PATH"] == "/usr/bin"          # it adds one name and carries everything else


def test_a_path_already_there_is_kept_after_it_and_never_twice():
    env = A.self_child_env({"PYTHONPATH": os.pathsep.join(["/other", PACKAGE_PARENT])})

    assert env["PYTHONPATH"].split(os.pathsep) == [PACKAGE_PARENT, "/other"]


def _an_interpreter_that_cannot_import_ao():
    """A python on PATH that does not have ao, or None: the shape of a clone with nothing installed.

    `-I` was tried first and proves nothing: isolated mode ignores PYTHONPATH, and the interpreter
    running the suite finds ao through its own environment whatever the child is handed.
    """
    for name in ("python3", "python3.9", "python3.10", "python3.11", "python3.12", "python3.13", "python3.14"):
        exe = shutil.which(name)
        if not exe or os.path.realpath(exe) == os.path.realpath(sys.executable):
            continue
        if subprocess.run([exe, "-c", "import ao"], capture_output=True).returncode != 0:
            return exe
    return None


def test_an_interpreter_without_ao_imports_it_when_handed_this_environment():
    """The clone case, run: an interpreter that has no ao is given the environment and finds it."""
    exe = _an_interpreter_that_cannot_import_ao()
    if exe is None:
        pytest.skip("every python on PATH already imports ao")
    code = "import ao, sys; sys.stdout.write(ao.__file__)"
    scrubbed = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    without = subprocess.run([exe, "-c", code], capture_output=True, text=True, env=scrubbed)
    with_env = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                              env=A.self_child_env(scrubbed))

    assert without.returncode != 0 and "No module named" in without.stderr
    assert with_env.returncode == 0
    assert os.path.realpath(with_env.stdout.strip()) == os.path.realpath(ao.__file__)


# ---- the guard ------------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parent.parent
# What the guard says of a call handed an argv that re-enters ao, and of such an argv it lost.
GUARDED = "env=self_child_env(...)"
NO_ENV = "no env= at the call"
OTHER_ENV = "env= not self_child_env(...)"
LOST = "argv not followed to its call"
# The places SELF-REENTRY closed, by file and function: a guard that stops finding them has stopped reading.
REENTRIES = {("src/ao/parts/cli_review.py", "_spawn_review_run"), ("src/ao/watchdog.py", "_schedule_hunt"),
             ("src/ao/mcp.py", "call")}


def _runs_ao(words):
    """Do these argv words hold `-m ao` or `-m ao.<module>`, an interpreter told to import this package?

    The pair counts wherever it stands, not only right after `sys.executable`: an option such as
    `-X utf8` may come between, the interpreter may be a name, and a list of `-m ao` alone may be
    joined to the one holding the interpreter. The two must be neighbouring words of one list or of
    one call's arguments. A module named through a variable is not read: `_scheduled_argv` names
    the one a scheduled job runs that way, and that child is the scheduler's, not ao's.
    """
    text = [word.value if isinstance(word, ast.Constant) and isinstance(word.value, str) else None
            for word in words]
    return any(flag == "-m" and module is not None and (module == "ao" or module.startswith("ao."))
               for flag, module in zip(text, text[1:]))


def _is_self_child_env(node):
    """Is `node` a call of self_child_env, bare in lib's namespace or `A.self_child_env` elsewhere?"""
    func = node.func if isinstance(node, ast.Call) else None
    return (isinstance(func, ast.Name) and func.id == "self_child_env") or \
        (isinstance(func, ast.Attribute) and func.attr == "self_child_env")


class _Module:
    """One module read in one walk: what each scope binds, every call, and every list that runs `-m ao`.

    A name is looked up as Python looks it up: a function sees its own names, then those of the
    functions around it, then the module's, never those of a class body it sits in. A name re-enters
    ao when any value bound to it does, and it is self_child_env's environment only when every value
    bound to it is one; a value the guard does not read - a parameter, a loop variable - is neither.
    """

    def __init__(self, source, rel):
        self.rel = rel
        self.chains, self.bindings, self.targets = {}, {}, set()
        self.calls, self.lists, self.reached = [], [], set()
        self._read(ast.parse(source, rel), (), "<module>")

    def _bind(self, scopes, name, value):
        self.bindings.setdefault(scopes[0], {}).setdefault(name, []).append(value)

    def _read(self, node, scopes, function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            outer = tuple(scope for scope in scopes if not isinstance(scope, ast.ClassDef))
            scopes = self.chains[node] = (node,) + outer
            function = getattr(node, "name", function)          # a lambda is reported by its function
            params = node.args
            for param in params.posonlyargs + params.args + params.kwonlyargs + [params.vararg, params.kwarg]:
                if param:
                    self._bind(scopes, param.arg, None)
        elif isinstance(node, (ast.Module, ast.ClassDef)):
            scopes = self.chains[node] = (node,) + scopes
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):                # `cmd += [...]` adds to what cmd may hold
                    self.targets.add(target)
                    self._bind(scopes, target.id, node.value)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node not in self.targets:
            self._bind(scopes, node.id, None)       # a loop, `with`, comprehension or unpacked variable
        elif isinstance(node, ast.Call):
            self.calls.append((node, scopes, function))
        elif isinstance(node, (ast.List, ast.Tuple)) and _runs_ao(node.elts):
            self.lists.append((node, function))
        for child in ast.iter_child_nodes(node):
            self._read(child, scopes, function)

    def _values(self, name, scopes):
        """The scope that binds `name` for code in `scopes`, and every value bound to it there."""
        for scope in scopes:
            if name in self.bindings.get(scope, ()):
                return scope, self.bindings[scope][name]
        return None, []

    def _reenters(self, node, scopes, seen):
        """Does `node` hold an argv that re-enters ao? Each list running `-m ao` it reaches is marked reached.

        Every branch is read before the answer is given, not only up to the first that re-enters,
        so a list on a later branch is marked too and is not reported as lost. A name already read
        for this question answers no the second time: what it holds has been counted once.
        """
        if isinstance(node, (ast.List, ast.Tuple)):
            runs = _runs_ao(node.elts)
            if runs:
                self.reached.add(node)
            return any([runs] + [self._reenters(element.value, scopes, seen)
                                 for element in node.elts if isinstance(element, ast.Starred)])
        if isinstance(node, (ast.Starred, ast.NamedExpr)):
            return self._reenters(node.value, scopes, seen)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return any([self._reenters(node.left, scopes, seen), self._reenters(node.right, scopes, seen)])
        if isinstance(node, ast.IfExp):
            return any([self._reenters(node.body, scopes, seen), self._reenters(node.orelse, scopes, seen)])
        if isinstance(node, ast.Name):
            scope, values = self._values(node.id, scopes)
            if (scope, node.id) in seen:
                return False
            seen.add((scope, node.id))
            return any([self._reenters(value, self.chains[scope], seen) for value in values if value is not None])
        return False

    def _environment(self, call, scopes):
        """The verdict on the environment a call hands its child: GUARDED for env= built by self_child_env.

        env= must be the call of self_child_env itself, or a name bound to nothing else: wrapped in
        anything, a later word could set PYTHONPATH over the one self_child_env wrote. An env= that
        arrives through **kwargs is not read, so it counts as none.
        """
        env = next((keyword.value for keyword in call.keywords if keyword.arg == "env"), None)
        if env is None:
            return NO_ENV
        values = self._values(env.id, scopes)[1] if isinstance(env, ast.Name) else [env]
        return GUARDED if values and all(_is_self_child_env(value) for value in values) else OTHER_ENV

    def findings(self):
        """(file, function, verdict) in source order, for the calls handed an argv that re-enters ao.

        A list running `-m ao` that no call was found to be handed is reported as LOST. Any call is
        judged, not only subprocess's: a helper handed the argv is the spawn as far as the guard can
        see, so it is handed env= too and trusted to pass it on. A call that only reads or grows the
        argv - to print it, or `cmd.extend(["-m", "ao"])` - is judged as well; none does today.
        """
        found = []
        for call, scopes, function in self.calls:
            handed = [self._reenters(value, scopes, set())
                      for value in call.args + [keyword.value for keyword in call.keywords]]
            # The second reading is an argv spread over the arguments, as create_subprocess_exec takes it.
            if any(handed) or _runs_ao(call.args):
                found.append((call.lineno, call.col_offset, function, self._environment(call, scopes)))
        found += [(node.lineno, node.col_offset, function, LOST)
                  for node, function in self.lists if node not in self.reached]
        return [(self.rel, function, verdict) for _, _, function, verdict in sorted(found)]


def _reentries(source, rel):
    return _Module(source, rel).findings()


def test_every_place_that_re_enters_ao_hands_the_child_that_environment():
    """Every module is read, so a re-entry in a file nobody named is judged as the three that were."""
    found = [finding for path in sorted((ROOT / "src" / "ao").rglob("*.py"))
             for finding in _reentries(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())]

    assert [finding for finding in found if finding[2] != GUARDED] == []
    assert {(rel, function) for rel, function, _ in found} >= REENTRIES


def test_the_guard_finds_a_re_entry_in_a_module_nobody_named_and_one_whose_argv_was_bound_first():
    """The two spellings the first guard let through, each without the environment and then with it.

    The module is one the guard was never told of: it reaches the guard as its source, as every
    module under src/ao does, and no list of files stands between them.
    """
    source = textwrap.dedent('''
        import subprocess, sys
        from ao import lib as A

        def hunt(root):
            subprocess.Popen([sys.executable, "-m", "ao", "-C", root, "hunt", "run"], cwd=root)

        def verify(root):
            cmd = [sys.executable, "-m", "ao"]
            return subprocess.run(cmd + ["-C", root, "verify"], capture_output=True)

        def hunt_handed(root):
            subprocess.Popen([sys.executable, "-m", "ao", "-C", root, "hunt", "run"], env=A.self_child_env())

        def verify_handed(root):
            cmd = [sys.executable, "-m", "ao"]
            return subprocess.run(cmd + ["-C", root, "verify"], env=A.self_child_env())
    ''')

    assert _reentries(source, "src/ao/fourth.py") == [
        ("src/ao/fourth.py", "hunt", NO_ENV), ("src/ao/fourth.py", "verify", NO_ENV),
        ("src/ao/fourth.py", "hunt_handed", GUARDED), ("src/ao/fourth.py", "verify_handed", GUARDED)]


def test_the_guard_follows_each_way_an_argv_is_put_together_and_fails_on_one_it_cannot_follow():
    """Each function is one way to write a re-entry, or one that is not; the verdicts follow in order."""
    source = textwrap.dedent('''
        import asyncio, os, subprocess, sys
        from ao import lib as A

        WATCHDOG = (sys.executable, "-X", "utf8", "-m", "ao.watchdog")

        def a_module_tuple_unpacked():
            subprocess.Popen([*WATCHDOG, "--once"], env=A.self_child_env())

        class Hunter:
            WATCHDOG = ["git"]

            def a_method_sees_the_module_past_its_class(self):
                subprocess.run(WATCHDOG)

        def joined_to_the_interpreter_with_env_bound_first(verbose):
            env = self_child_env(dict(os.environ, AO_ROLE="hunter"))
            subprocess.run([sys.executable] + (["-m", "ao", "-v"] if verbose else ["-m", "ao"]), env=env)

        def grown_in_place():
            cmd = ["nice"]
            cmd += [sys.executable, "-m", "ao"]
            cmd = cmd + ["--once"]
            subprocess.run(cmd)

        def bound_inside_the_call():
            subprocess.run(cmd := [sys.executable, "-m", "ao"], env=A.self_child_env())
            subprocess.run(cmd)

        def spread_over_the_arguments():
            return asyncio.create_subprocess_exec(sys.executable, "-m", "ao", "status")

        def handed_to_a_helper(spawn):
            spawn([sys.executable, "-m", "ao"])

        def env_only_in_kwargs(options):
            subprocess.run([sys.executable, "-m", "ao"], **options)

        def env_from_elsewhere():
            subprocess.run([sys.executable, "-m", "ao"], env=dict(os.environ))

        def env_bound_again():
            env = A.self_child_env()
            env = dict(env, PYTHONPATH="")
            subprocess.run([sys.executable, "-m", "ao"], env=env)

        def returned_to_a_caller():
            return [sys.executable, "-m", "ao"]

        def not_a_re_entry(WATCHDOG):
            subprocess.run(WATCHDOG)
            subprocess.run([sys.executable, "-m", "pytest"])
            subprocess.run(["git", "commit", "-m", "aorta"])
    ''')

    assert _reentries(source, "m.py") == [
        ("m.py", "a_module_tuple_unpacked", GUARDED), ("m.py", "a_method_sees_the_module_past_its_class", NO_ENV),
        ("m.py", "joined_to_the_interpreter_with_env_bound_first", GUARDED),
        ("m.py", "grown_in_place", NO_ENV),
        ("m.py", "bound_inside_the_call", GUARDED), ("m.py", "bound_inside_the_call", NO_ENV),
        ("m.py", "spread_over_the_arguments", NO_ENV), ("m.py", "handed_to_a_helper", NO_ENV),
        ("m.py", "env_only_in_kwargs", NO_ENV),
        ("m.py", "env_from_elsewhere", OTHER_ENV), ("m.py", "env_bound_again", OTHER_ENV),
        ("m.py", "returned_to_a_caller", LOST)]
