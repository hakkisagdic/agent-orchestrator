"""`ao demo`: ao's guarantee shown in about a minute, on a project of its own in a scratch folder (DEMO).

The README states the guarantee - a model of another family reviews the exact change an agent
staged, and nothing else commits - and `ao prove` checks the wiring of a project that has ao
already. Neither shows someone who has not adopted ao what that looks like when an agent's change
is wrong, and the first time anyone saw a refusal was on their own repository. The demo plays one
change through the real command line twice: first with a bug whose tests pass, which the review
finds and which neither `ao commit-ok` nor a plain `git commit` lets land; then with the fix, which
is reviewed, granted and committed with the trailers that name its grant.

Everything happens in a folder made for it in the temporary directory: a git project, a home of
its own and a stand-in reviewer. Every ao command runs as `sys.executable -m ao`, the command line
a person types, with HOME and USERPROFILE naming that home, so ~/.ao, git's identity and every
harness's store are the folder's, never the person's; the variables of ao's and git's that point
anywhere else are left out. No model is called and nothing is installed: the reviewer is a short
script that answers in ao's verdict format, declared in the project's .ao/config.json with a family
of its own, as any reviewer is, and held to every check a reviewer is held to.
"""
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

from . import lib as A
from .verdicts import Verdict

UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

C = A.C

# The scratch folder's name starts with this, and only a folder the demo made itself is removed.
PREFIX = "ao-demo-"
# What points ao or git at a person's own files besides HOME: their XDG directories. Every variable of
# ao's (AO_) and of git's (GIT_) is left out as well, as the suite leaves them out of its own home.
HOME_POINTERS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
# ao's one variable that points nowhere: a traceback for whoever has to find where a failure came from.
KEPT = ("AO_DEBUG",)
# Longer than any step needs; a command past it is a demo that went wrong, said as one.
COMMAND_SECONDS = 600

# The acceptance boundary the agent works to. One sentence, so the review is asked no criteria of
# its own (docs/pipeline.md, CRITERIA-VERDICTS): what decides is the reviewer's verdict alone.
BOUNDARY = "calc.divide(a, b) returns a divided by b and raises ValueError when b is 0"
REVIEWER_ID = "stand-in-reviewer"
REVIEWER_FAMILY = "stand-in"

CALC = '''"""A tiny calculator: the module the demo's agent changes."""


def add(a, b):
    return a + b
'''
DIVIDE_WITH_BUG = '''

def divide(a, b):
    return a / b
'''
DIVIDE_FIXED = '''

def divide(a, b):
    if b == 0:
        raise ValueError("divide: b must not be 0")
    return a / b
'''

TEST_HEAD = '''import unittest

from calc import {names}


class CalcTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
'''
TEST_DIVIDE = '''
    def test_divide(self):
        self.assertEqual(divide(6, 3), 2)
'''
TEST_DIVIDE_BY_ZERO = '''
    def test_divide_by_zero_is_refused(self):
        with self.assertRaises(ValueError):
            divide(1, 0)
'''
TEST_TAIL = '''

if __name__ == "__main__":
    unittest.main()
'''

# The stand-in reviewer, run as [python, reviewer.py, prompt]: no shebang, so Windows runs it too. It
# answers the probe `ao init` sends by echoing its nonce, and a review in ao's verdict format. It asks
# one question of the candidate: does `divide` handle a divisor of 0? ao unpacks the staged tree into
# candidate/ in the directory it runs a reviewer in (REVIEW-TREE-3), and that copy is what it reads.
REVIEWER = '''"""The stand-in reviewer of `ao demo`: a script that answers in ao's verdict format, not a model."""
import ast
import os
import sys

prompt = sys.argv[1] if len(sys.argv) > 1 else ""
if prompt.startswith("Reviewer invocation probe"):
    print(prompt.strip().splitlines()[-1])
    sys.exit(0)


def answer(verdict, findings):
    print("VERDICT: " + verdict)
    print("BLOCKER: 0")
    print("HIGH: %d" % len(findings))
    print("MEDIUM: 0")
    print("LOW: 0")
    print()
    print("## Findings")
    print("\\n".join(findings) if findings else "None.")
    print()
    print("## Notes")
    print("A stand-in: a script that asks one question of divide in the staged calc.py.")
    sys.exit(0)


try:
    with open(os.path.join("candidate", "calc.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
except (OSError, SyntaxError) as exc:
    answer("NEEDS_CHANGES", ["- [HIGH] calc.py:1 - the staged calc.py could not be read (%s)" % type(exc).__name__])
divide = next((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "divide"), None)
if divide is None:
    answer("NEEDS_CHANGES", ["- [HIGH] calc.py:1 - the boundary asks for divide(a, b), and calc.py defines none"])
names = [arg.arg for arg in divide.args.args]
divisor = names[1] if len(names) > 1 else "b"


def compares_with_zero(node):
    if isinstance(node, ast.Compare):
        parts = [node.left] + list(node.comparators)
        return any(isinstance(part, ast.Name) and part.id == divisor for part in parts) \\
            and any(isinstance(part, ast.Constant) and part.value == 0 for part in parts)
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not) \\
        and isinstance(node.operand, ast.Name) and node.operand.id == divisor


if any(compares_with_zero(node) for node in ast.walk(divide)):
    answer("APPROVED", [])
line = next((node.lineno for node in ast.walk(divide) if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)),
            divide.lineno)
answer("NEEDS_CHANGES", ["- [HIGH] calc.py:%d - divide(1, 0) raises ZeroDivisionError: nothing in divide handles "
                         "%s == 0, where the boundary asks for a ValueError" % (line, divisor)])
'''


class _Stopped(Exception):
    """A step that did not behave as the demo says it does; the message says what happened instead."""


def _test_module(*cases):
    names = "add, divide" if cases else "add"
    return TEST_HEAD.format(names=names) + "".join(cases) + TEST_TAIL


def _shell_words(words):
    """The words of a command as a person would type them, for the transcript only: nothing runs this text."""
    shown = []
    for word in words:
        plain = word and all(ch.isalnum() or ch in "-_./:=~@%+," for ch in word)
        shown.append(word if plain else '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"')
    return " ".join(shown)


def _gate_command(argv):
    """A gate's `run` for the shell `ao verify` hands it to: cmd.exe's quoting on Windows, sh's elsewhere."""
    return subprocess.list2cmdline(argv) if os.name == "nt" else " ".join(shlex.quote(word) for word in argv)


def _environment(scratch, home, bin_dir):
    """The environment of every command the demo runs: the scratch home, its own temporary directory, and
    this ao first on PATH.

    HOME and USERPROFILE name the scratch home, so ~/.ao - the registry, the settings, the gate lock, the
    event log - and git's global config are the demo's. Every AO_ variable named one of the person's
    own files, every GIT_ variable could point git at another repository or another config, and an XDG
    directory is a home of its own to git; all are left out. Temporary files go into the scratch folder
    too, a reviewer's working directory among them, and no Python the demo starts writes its bytecode
    beside the ao it imports.
    """
    env = {name: value for name, value in os.environ.items()
           if name.upper() in KEPT or not (name.upper().startswith(("AO_", "GIT_")) or name.upper() in HOME_POINTERS)}
    temporary = os.path.join(scratch, "tmp")
    env.update(HOME=home, USERPROFILE=home, TMPDIR=temporary, TEMP=temporary, TMP=temporary,
               PYTHONDONTWRITEBYTECODE="1")
    env["PATH"] = os.pathsep.join([bin_dir] + [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                                               if entry])
    return A.self_child_env(env)


def _ao_on_path(bin_dir):
    """Write an `ao` that is this ao into the scratch bin, which the demo's PATH names first.

    The commit hook finds ao by name on PATH (docs/commands.md), and a person's PATH can hold none -
    ao run from a clone - or another release of it, and Windows' hook falls back on no installed one.
    The hook the demo installs must run the ao that installed it, so the name is this interpreter running
    this package, with the package's own directory on PYTHONPATH, as the `ao` the suite puts on a hook's
    PATH is (tests/test_hook_paths.py). Git runs hooks with its own sh on every platform, so the file is a
    sh script on Windows too, its paths written with forward slashes, which that sh and Python both read.
    """
    os.makedirs(bin_dir)
    path = os.path.join(bin_dir, "ao")
    python, package = (os.path.abspath(where).replace("\\", "/")
                       for where in (sys.executable, os.path.dirname(os.path.dirname(A.__file__))))
    with open(path, "w", encoding=UTF8, newline="\n") as fh:
        fh.write(f"#!/bin/sh\nexport PYTHONPATH={shlex.quote(package)}\nexec {shlex.quote(python)} -m ao \"$@\"\n")
    os.chmod(path, 0o755)


def _remove(scratch):
    """Remove the scratch folder the demo made, and nothing else; None when it is gone, else why not.

    Git writes its objects read-only, and Windows refuses to delete a read-only file, so a file that
    cannot be removed is made writable and removed again.
    """
    made = os.path.realpath(scratch)
    if not (os.path.basename(made).startswith(PREFIX)
            and os.path.dirname(made) == os.path.realpath(tempfile.gettempdir())):
        return f"{scratch} is not a folder the demo made in the temporary directory, so it is left alone"

    def again(function, path, _):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)

    handler = {"onexc": again} if sys.version_info >= (3, 12) else {"onerror": again}
    try:
        shutil.rmtree(made, **handler)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


class _Demo:
    """One run: the scratch folder's places, the environment of its commands, and the step being played."""

    def __init__(self, scratch):
        self.scratch = scratch
        self.home = os.path.join(scratch, "home")
        self.project = os.path.join(scratch, "project")
        self.bin = os.path.join(scratch, "bin")
        self.reviewer = os.path.join(scratch, "reviewer.py")
        self.env = _environment(scratch, self.home, self.bin)
        # ao and git name the folder as it was made and as the file system resolves it.
        spellings = {scratch, os.path.realpath(scratch)}
        self.spellings = sorted(spellings | {path.replace("\\", "/") for path in spellings}, key=len, reverse=True)

    # ---- what a person reads ------------------------------------------------------------------

    def short(self, text):
        """`text` with the scratch folder written <scratch>: its full path is said once, at the start."""
        for spelling in self.spellings:
            text = text.replace(spelling, "<scratch>")
        return text

    def say(self, text=""):
        print(f"   {text}" if text else "", flush=True)

    def show(self, command, output):
        print(f"   {C['b']}$ {self.short(command)}{C['reset']}", flush=True)
        for line in self.short(output).rstrip().splitlines():
            print(f"     {line.rstrip()}", flush=True)

    # ---- what runs ----------------------------------------------------------------------------

    def ao(self, *args):
        """Run `ao <args>` in the project as the command line it is, show it and what it printed; (exit, output)."""
        try:
            done = subprocess.run([sys.executable, "-m", "ao", *args], cwd=self.project,
                                  env=A.self_child_env(self.env), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding=UTF8,
                                  errors="replace", timeout=COMMAND_SECONDS)
        except subprocess.TimeoutExpired:
            raise _Stopped(f"ao {args[0]} did not finish in {COMMAND_SECONDS}s")
        self.show(_shell_words(("ao",) + args), done.stdout)
        return done.returncode, done.stdout

    def git(self, *args, shown=False):
        """Run git in the project; shown, it is part of the transcript, else only a failure is said."""
        try:
            done = subprocess.run(["git", *args], cwd=self.project, env=self.env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding=UTF8,
                                  errors="replace", timeout=COMMAND_SECONDS)
        except subprocess.TimeoutExpired:
            raise _Stopped(f"git {args[0]} did not finish in {COMMAND_SECONDS}s")
        if shown:
            self.show(_shell_words(("git",) + args), done.stdout)
        return done.returncode, done.stdout

    def setup_git(self, *args):
        """A git step of the setup, which must succeed; its output is said only when it does not."""
        code, output = self.git(*args)
        if code:
            self.show(_shell_words(("git",) + args), output)
            raise _Stopped(f"git {args[0]} exited {code}")
        return output

    def head(self):
        return self.setup_git("rev-parse", "HEAD").strip()

    def write(self, name, text):
        with open(os.path.join(self.project, name), "w", encoding=UTF8, newline="\n") as fh:
            fh.write(text)

    # ---- the steps ------------------------------------------------------------------------------

    def a_project(self):
        """A home of its own, a git project with a tested module, and the stand-in reviewer beside it."""
        for folder in (self.home, self.project, os.path.join(self.scratch, "tmp")):
            os.makedirs(folder)
        with open(os.path.join(self.home, ".gitconfig"), "w", encoding=UTF8, newline="\n") as fh:
            fh.write("[user]\n\tname = ao demo\n\temail = demo@example.invalid\n"
                     "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        _ao_on_path(self.bin)
        with open(self.reviewer, "w", encoding=UTF8, newline="\n") as fh:
            fh.write(REVIEWER)
        self.setup_git("init", "-q")
        self.write("calc.py", CALC)
        self.write("test_calc.py", _test_module())
        # Running the tests writes __pycache__/, which a Python project ignores; untracked, it would be a change
        # outside the staged candidate, and `ao verify` refuses to measure one.
        self.write(".gitignore", "__pycache__/\n")
        self.setup_git("add", "calc.py", "test_calc.py", ".gitignore")
        self.setup_git("commit", "-q", "-m", "calc: add(a, b) and its test")
        self.say(f"scratch folder  {self.scratch}  (written <scratch> from here on)")
        self.say("home            <scratch>/home: ~/.ao and git's identity are these, not yours")
        self.say("project         <scratch>/project: calc.py with add(a, b), test_calc.py, .gitignore, one commit")
        self.say("reviewer        <scratch>/reviewer.py: a stand-in script, not a model")

    def put_ao_on_it(self):
        """The reviewer and the gate declared first, then `ao init`, the adoption commit and the hook."""
        os.makedirs(os.path.join(self.project, ".ao"))
        config = {"project": "calc",
                  "reviewer": {"id": REVIEWER_ID, "family": REVIEWER_FAMILY,
                               "argv": [sys.executable, self.reviewer, "{prompt}"],
                               "_why": "ao demo's stand-in: a script of a family of its own, never the implementer"}}
        with open(os.path.join(self.project, ".ao", "config.json"), "w", encoding=UTF8, newline="\n") as fh:
            fh.write(json.dumps(config, indent=2) + "\n")
        gates = {"gates": {"tests": {"run": _gate_command([sys.executable, "-m", "unittest", "-q"]),
                                     "expect": "exit_zero", "timeout": 300}},
                 "profiles": {"quick": ["tests"], "full": ["tests"]}, "default_profile": "quick"}
        with open(os.path.join(self.project, ".ao", "gates.json"), "w", encoding=UTF8, newline="\n") as fh:
            fh.write(json.dumps(gates, indent=2) + "\n")
        self.say(f".ao/config.json  the reviewer: {REVIEWER_ID}, family {REVIEWER_FAMILY}, "
                 "runs <scratch>/reviewer.py")
        self.say(".ao/gates.json   one gate, the project's tests: python -m unittest")
        profile = A.default_profile()
        implementer = (A.profiles().get(profile) or {}).get("implementer")
        # No MCP registration: registering one can run a harness's own command where it is installed, and the
        # demo starts none. The agent named is the profile's implementer, so what init writes is the same
        # on every machine, whatever harnesses its PATH holds.
        code, _ = self.ao("init", "--profile", profile, *(["--agent", implementer] if implementer else []),
                          "--no-mcp")
        if code:
            raise _Stopped(f"ao init exited {code}")
        self.say("the next steps ao init names are for a repository of your own; the demo goes on without them")
        # `ao init` stages nothing. The marker turns enforcement on, and .gitignore is product: left
        # uncommitted, it is a change outside the staged candidate, and `ao verify` refuses to measure one.
        code, _ = self.git("add", ".ao-project", ".gitignore", shown=True)
        if code:
            raise _Stopped(f"git add exited {code}")
        code, _ = self.git("commit", "-q", "-m", "adopt ao", shown=True)
        if code:
            raise _Stopped(f"git commit of the adoption exited {code}")
        code, _ = self.ao("hooks", "install")
        if code:
            raise _Stopped(f"ao hooks install exited {code}")
        self.say("from here on a commit lands only with a grant for its exact staged tree")

    def a_change_with_a_bug(self):
        """divide(a, b) whose tests pass and that does not handle b == 0: refused by review, commit-ok and git."""
        self.write("calc.py", CALC + DIVIDE_WITH_BUG)
        self.write("test_calc.py", _test_module(TEST_DIVIDE))
        self.say("the agent adds divide(a, b) to calc.py, and a test that divides 6 by 3:")
        for line in DIVIDE_WITH_BUG.strip("\n").splitlines():
            self.say(f"  + {line}")
        self.git("add", "calc.py", "test_calc.py", shown=True)
        before = self.head()
        code, _ = self.ao("verify")
        if code:
            raise _Stopped(f"ao verify exited {code}, where the tests pass")
        code, output = self.ao("review", "--boundary", BOUNDARY)
        if code != 1 or Verdict.NEEDS_CHANGES.value not in output:
            raise _Stopped(f"ao review exited {code}, where the review was to find the bug and say "
                           f"{Verdict.NEEDS_CHANGES.value}")
        code, output = self.ao("commit-ok")
        if code != 1 or "REFUSED" not in output:
            raise _Stopped(f"ao commit-ok exited {code}, where it was to refuse")
        code, output = self.git("commit", "-m", "add divide", shown=True)
        if code == 0 or self.head() != before or "COMMIT REFUSED" not in output:
            raise _Stopped(f"git commit exited {code}, where ao's hook was to refuse it")
        self.say(f"{C['green']}nothing landed{C['reset']}: the tests passed, the review found the bug, and "
                 f"HEAD is still {before[:12]}")

    def the_fix(self):
        """divide refuses b == 0: reviewed, granted and committed, its commit naming its grant."""
        self.write("calc.py", CALC + DIVIDE_FIXED)
        self.write("test_calc.py", _test_module(TEST_DIVIDE, TEST_DIVIDE_BY_ZERO))
        self.say("the agent makes divide refuse b == 0, and tests that it does:")
        for line in DIVIDE_FIXED.strip("\n").splitlines():
            self.say(f"  + {line}")
        self.git("add", "calc.py", "test_calc.py", shown=True)
        before = self.head()
        code, _ = self.ao("verify")
        if code:
            raise _Stopped(f"ao verify exited {code}, where the tests pass")
        code, output = self.ao("review", "--boundary", BOUNDARY)
        if code != 0 or Verdict.APPROVED.value not in output:
            raise _Stopped(f"ao review exited {code}, where the fix was to be {Verdict.APPROVED.value}")
        code, output = self.ao("commit-ok")
        if code != 0 or "GRANTED" not in output:
            raise _Stopped(f"ao commit-ok exited {code}, where it was to grant")
        code, _ = self.ao("commit", "-m", "feat: add divide, which refuses b == 0")
        landed = self.head()
        if code or landed == before:
            raise _Stopped(f"ao commit exited {code}, where the granted candidate was to land")
        code, message = self.git("log", "-1", "--format=%B", shown=True)
        if code or "Ao-Grant:" not in message:
            raise _Stopped("the commit that landed names no grant in its trailers")
        code, _ = self.ao("commit-check", "--range", "HEAD~1..HEAD")
        if code:
            raise _Stopped(f"ao commit-check --range exited {code}, where the commit matches its grant")
        code, _ = self.ao("digest")
        if code:
            raise _Stopped(f"ao digest exited {code}")


STEPS = (
    ("a project of its own", _Demo.a_project),
    ("ao on it, the reviewer a stand-in of another family", _Demo.put_ao_on_it),
    ("the agent stages a change with a bug", _Demo.a_change_with_a_bug),
    ("the agent stages the fix", _Demo.the_fix),
)


def run(keep=False):
    """Play the demo; 0 when every step behaved as it says, 1 naming the first that did not (DEMO).

    The scratch folder is removed at the end whatever happened, unless `keep` asks for it to stay, and
    the last lines say which, and where it is.
    """
    if not shutil.which("git"):
        print(f"{C['red']}ao demo needs git{C['reset']}: install git, so that `git --version` answers, and run it "
              "again")
        return 1
    print(f"{C['b']}ao demo{C['reset']}: an agent stages a change whose tests pass and whose code is wrong. A "
          "reviewer of another\nmodel family finds it, and nothing lands until the fix is reviewed: not through "
          "ao, not through\na plain git commit. Nothing outside a scratch folder is touched, no model is called, "
          "and nothing\nis spent: the reviewer is a stand-in script.", flush=True)
    scratch = tempfile.mkdtemp(prefix=PREFIX)
    demo, stopped = _Demo(scratch), None
    try:
        for number, (title, step) in enumerate(STEPS, 1):
            print(f"\n{C['b']}{number}. {title}{C['reset']}", flush=True)
            try:
                step(demo)
            except _Stopped as exc:
                stopped = (number, title, str(exc))
                break
            except OSError as exc:
                stopped = (number, title, f"{type(exc).__name__}: {demo.short(str(exc))}")
                break
    finally:
        print(f"\n{C['b']}{len(STEPS) + 1}. the scratch folder{C['reset']}", flush=True)
        if keep:
            demo.say(f"kept: {scratch}")
            demo.say("the project, its .ao/ ledgers and its review artefacts are there; delete the folder when "
                     "you are done")
            problem = None
        else:
            problem = _remove(scratch)
            demo.say(f"removed {scratch}" if problem is None else
                     f"{C['red']}not removed{C['reset']}: {problem}; delete {scratch} by hand")
    if stopped:
        number, title, what = stopped
        then = f"Read what ao wrote in {scratch}." if keep else \
            "Run `ao demo --keep` to keep the scratch folder and read what ao wrote there."
        print(f"\n{C['red']}{C['b']}DEMO STOPPED{C['reset']} at step {number}, {title}: {demo.short(what)}. {then}")
        return 1
    if problem:
        print(f"\n{C['red']}{C['b']}DEMO STOPPED{C['reset']} at step {len(STEPS) + 1}, the scratch folder: it "
              "could not be removed.")
        return 1
    print(f"\n{C['green']}{C['b']}DEMO PASSED{C['reset']}  every step behaved as it says. To put ao on a "
          "repository of your own:\ndocs/getting-started.md, or `ao init --profile "
          f"{A.default_profile()}` in it.")
    return 0
