"""What a tool grant admits, measured against ao's own guarantees (#58).

An allowlist that grants `git commit:*` grants `git commit --no-verify`, the one
flag that skips the only commit-time enforcement; one that grants `ao:*` grants
`ao push allow`; one that grants an interpreter grants everything, because any
command can be written as a script. So each rule is asked what it would admit,
command by command, instead of being read for its intent.

`ao verify` is not among the forbidden commands, though it runs what `.ao/gates.json` names and an
implementer can write that file: a grant that lets an implementer test its work runs code it wrote,
whatever the command is called. What holds there is the commit hook and `ao commit`, a verification's
binding to the gate definitions it ran (#61), and the push window - not this list (GRANTS-AUDIT-2).
"""
import os
from functools import lru_cache
import posixpath
import re

# (why no actor ao runs may be granted it, the command)
FORBIDDEN = (
    ("skips the commit hook", "git commit --no-verify -m x"),
    ("skips the commit hook", "git commit -n -m x"),
    ("redirects the commit hook", "git -c core.hooksPath=/dev/null commit -m x"),
    ("rewrites the hook configuration", "git config core.hooksPath /dev/null"),
    ("opens a push window", "ao push allow 30"),
    ("waives a gate", "ao waive review --slice s --by b --why w"),
    ("records a review ao did not run", "ao collect-review n --response r --model m --by b"),
    ("records a person's review", "ao person-review --by p --verdict APPROVED --digest d"),
    ("opts into same-family review", "ao config set review.same_family labeled --by p"),
    ("opts into same-family review", "ao init --review-tier same-family --by p"),
    # A person's act on the record since FEATURE-RECORDED; a grant that admits it hands an agent the record too.
    ("switches review off", "ao features off review --by p"),
    ("switches the inventory review off", "ao features off inventory_review --by p"),
    ("names the family that wrote a waived range", "ao catchup --author-family f --by b"),
    ("states which waived slices only moved code", "ao catchup --move-only s --by b"),
    ("replaces a waived range's commit claims", "ao catchup --boundary text --by b"),
    # What a person has seen of the harness configuration an agent changed lets every turn start again (HARNESS-CONFIG),
    # by its digest or by a yes at the terminal (HARNESS-SEEN-OWN).
    ("records harness configuration as seen", "ao harness seen --by p --digest d"),
    ("records harness configuration as seen", "ao harness seen --by p --ask"),
    ("removes the hooks", "ao hooks uninstall"),
    ("pages a person", "ao doctor --check --notify"),
    ("runs any command", "ao lock -- sh -c x"),
    ("runs arbitrary code", "python3 -c x"),
    ("runs arbitrary code", "python -c x"),
    ("runs arbitrary code", "python -m x"),
    ("runs arbitrary code", "python3 -m x"),
    ("runs arbitrary code", "py -m x"),
    ("runs arbitrary code", "node -e x"),
    ("runs arbitrary code", "npx x"),
    ("runs arbitrary code", "npm run x"),
    ("runs arbitrary code", "pytest -p x"),
    ("runs arbitrary code", "bash -c x"),
    ("runs arbitrary code", "sh -c x"),
    ("runs arbitrary code", "zsh -c x"),
    ("runs arbitrary code", "find . -exec sh -c x ;"),
    ("runs arbitrary code", "xargs sh -c x"),
    ("runs arbitrary code", "env sh -c x"),
    ("runs arbitrary code", "uv run x"),
    ("runs arbitrary code", "make x"),
    # Windows' own ways to run a line of code, as `python -c` runs one: the py launcher, PowerShell and cmd.exe each
    # run the text they are handed, and a grant of any of them was asked about none (WINDOWS-SAY-WHY). Git Bash, which
    # runs a harness's Bash tool on Windows, hands cmd its switch as `//c`.
    ("runs arbitrary code", "py -c x"),
    ("runs arbitrary code", "py -3 -c x"),
    ("runs arbitrary code", "python.exe -c x"),
    ("runs arbitrary code", "powershell -Command x"),
    ("runs arbitrary code", "powershell -c x"),
    ("runs arbitrary code", "powershell -EncodedCommand x"),
    ("runs arbitrary code", "powershell.exe -Command x"),
    ("runs arbitrary code", "PowerShell -Command x"),
    ("runs arbitrary code", "pwsh -Command x"),
    ("runs arbitrary code", "pwsh -c x"),
    ("runs arbitrary code", "cmd /c x"),
    ("runs arbitrary code", "cmd //c x"),
    ("runs arbitrary code", "cmd.exe /c x"),
)


# The architect decides. An implementer granted this could restart its own round
# budget with a re-specification it wrote itself (#65).
IMPLEMENTER_FORBIDDEN = (
    ("re-specifies a slice, restarting its round budget", "ao decide x --scope s"),
    ("changes the settings that govern its own review", "ao config set review_timeout 1"),
    # Whether work is dropped is scope; the implementer parks a slice instead (SLICE-LIFECYCLE).
    ("abandons a slice, which the architect decides", "ao slice abandon --why w"),
)

# The architect decides and edits ao's coordination files; a wake writes no product code
# (GRANTS-PINNED). A rule admitting one of these hands it a write its playbook never asks for.
ARCHITECT_FORBIDDEN = (
    ("removes a product file", "rm agent-mail/x src/app.py"),
    ("writes a product file", "git diff --output=src/app.py"),
    ("writes a product file", "git log --output=src/app.py"),
    ("writes a product file", "git show --output=src/app.py"),
    ("writes a product file", "cp agent-mail/x src/app.py"),
    ("writes a product file", "mv agent-mail/x src/app.py"),
    ("writes a product file", "tee src/app.py"),
)

# Programs that run the command after them, each with its own options declared whole, as getopt declares them: the
# letters of its short options, with ":" after one that takes a value and "::" after one that takes it only attached,
# and its long options, with "=" after one that takes a value and "=?" after one that takes it only after "="
# (ALLOWLIST-NORMALIZE). A rule admitting one with any arguments admits what it would admit of the command after it:
# `Bash(timeout 60 pytest:*)` admits `timeout 60 pytest -p x`, which loads any plugin, and `Bash(nice:*)` admits every
# command there is. Each holds the options of GNU's program and of BSD's and macOS's both: the reading listed only
# those that take a value and took any other for one that takes none, so `Bash(env -P /usr/bin git:*)` read
# `/usr/bin` as the program, where macOS's env takes it as the folder to find git in (ALLOWLIST-DEPTH).
WRAPPERS = {
    # coreutils env and BSD's: -a, -C and the long options are GNU's, -L, -P and -U BSD's.
    "env": ("0iva:C:L:P:S:U:u:", ("ignore-environment", "null", "debug", "list-signal-handling", "help", "version",
                                  "argv0=", "chdir=", "split-string=", "unset=", "block-signal=?",
                                  "default-signal=?", "ignore-signal=?")),
    "timeout": ("fpvk:s:", ("foreground", "preserve-status", "verbose", "help", "version", "kill-after=",
                            "signal=")),
    "nice": ("n:", ("help", "version", "adjustment=")),
    "nohup": ("", ("help", "version")),
    "stdbuf": ("e:i:o:", ("help", "version", "error=", "input=", "output=")),
    # findutils xargs and BSD's: -a, -d, and -e, -i and -l, whose value is attached or none, are GNU's; -J, -R and -S
    # BSD's.
    "xargs": ("0oprtxE:I:J:L:P:R:S:a:d:n:s:e::i::l::", ("null", "open-tty", "interactive", "no-run-if-empty",
                                                         "verbose", "exit", "show-limits", "help", "version",
                                                         "arg-file=", "delimiter=", "max-args=", "max-chars=",
                                                         "max-procs=", "process-slot-var=", "eof=?", "max-lines=?",
                                                         "replace=?")),
    # bash's keyword (-p), GNU time and BSD's.
    "time": ("ahlpqvVf:o:", ("append", "portability", "quiet", "verbose", "help", "version", "format=", "output=")),
    "command": ("pvV", ()),
    "exec": ("cla:", ()),
    # The shells' own: bash's and zsh's builtin, zsh's precommand modifiers, the ! before a pipeline, bash's coproc.
    "builtin": ("", ()),
    "noglob": ("", ()),
    "nocorrect": ("", ()),
    "-": ("", ()),
    "!": ("", ()),
    "coproc": ("", ()),
    "caffeinate": ("dimsut:w:", ()),
    "sudo": ("ABbEeHiKklNnPSsVvC:D:R:T:U:g:p:r:t:u:h:", ("askpass", "bell", "background", "edit", "set-home", "help",
                                                         "login", "remove-timestamp", "reset-timestamp", "list",
                                                         "no-update", "non-interactive", "preserve-groups", "stdin",
                                                         "shell", "version", "validate", "chdir=", "chroot=",
                                                         "close-from=", "command-timeout=", "group=", "host=",
                                                         "other-user=", "prompt=", "role=", "type=", "user=",
                                                         "preserve-env=?")),
}
# GNU's names for its own on macOS, where Homebrew installs coreutils and findutils beside the system's programs.
WRAPPERS.update({"g" + name: WRAPPERS[name]
                 for name in ("env", "nice", "nohup", "stdbuf", "time", "timeout", "xargs")})
# Words a wrapper reads before the command that are not options: timeout's duration.
WRAPPER_OPERANDS = {"timeout": 1, "gtimeout": 1}
# A wrapper's options after which the reading cannot say what runs: env -S splits a string of its own into the
# command, env -P names where the program is found, as PATH does, sudo -e edits files with whichever editor the
# environment names, and sudo -R runs the program from another root (ALLOWLIST-DEPTH).
WRAPPER_RUNS_ANY = {"env": ("-S", "--split-string", "-P", "-L", "-U", "-a", "--argv0"),
                    "genv": ("-S", "--split-string", "-P", "-L", "-U", "-a", "--argv0"),
                    "exec": ("-a",),
                    "sudo": ("-e", "--edit", "-R", "--chroot", "-A", "--askpass", "-s", "--shell")}
# A wrapper's options with which it runs nothing: `command -v git` says where git is.
WRAPPER_DESCRIBES = {"command": ("-v", "-V")}
# A wrapper's options with which, given no command, it starts a shell that reads its commands from what it is handed:
# `echo 'git commit -n -m x' | sudo -s` runs the commit (ALLOWLIST-DEPTH).
WRAPPER_SHELLS = {"sudo": ("-s", "-i", "--shell", "--login")}
# Wrappers whose adjustment may stand as an option of its own: `nice -10 git commit -n` runs git.
WRAPPER_NUMBERS = ("nice", "gnice")
# xargs without replacement appends arbitrary arguments. Replacement may rewrite any command word and fails
# closed, including -i/--replace with their default string (ALLOWLIST-DEPTH).
XARGS = ("xargs", "gxargs")
XARGS_REPLACES = ("-I", "-J", "-i", "--replace")
WRAPPER_RUNS_ANY.update({name: XARGS_REPLACES for name in XARGS})
# A word a shell reads as a variable for the command after it, not as its program: `FOO=1 git commit -n` runs git
# (ALLOWLIST-DEPTH).
SHELL_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# The variables such a word, or env's, may set and leave the command as the reading reads it: the locale, the terminal
# and its colours, the time zone, CI. Any other may change what runs - PATH names the program, LD_PRELOAD and
# NODE_OPTIONS load code into it, PYTEST_ADDOPTS hands it options, GIT_CONFIG_COUNT and its keys hand git
# `core.hooksPath` - and a rule that sets one reads as any command: `Bash(GIT_CONFIG_COUNT=1
# GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit -m x)` read as `git commit -m x`
# (ALLOWLIST-DEPTH).
INERT_VARIABLES = re.compile(r"LANG|LANGUAGE|LC_[A-Z]+|TZ|TERM|COLUMNS|LINES|NO_COLOR|FORCE_COLOR|CLICOLOR(_FORCE)?|CI")
# git's own options before its subcommand, as git.c reads them - each matched whole, never abbreviated or run
# together - with whether it takes a value, as the next word or after "=". -v, --version, -h and --help end them, as
# the subcommand each stands for. They change where git works and what it shows, not what a subcommand does to the
# commit hook, except -c and --config-env, which set configuration, and --exec-path=, which names the folder git runs
# its own programs from: `git commit` runs `git maintenance` from it (ALLOWLIST-DEPTH).
GIT_OPTIONS = {
    "-C": True, "-c": True, "--attr-source": True, "--config-env": True, "--git-dir": True, "--list-cmds": True,
    "--namespace": True, "--shallow-file": True, "--super-prefix": True, "--work-tree": True,
    "-P": False, "-p": False, "--bare": False, "--exec-path": False, "--glob-pathspecs": False, "--html-path": False,
    "--icase-pathspecs": False, "--info-path": False, "--literal-pathspecs": False, "--man-path": False,
    "--no-advice": False, "--no-lazy-fetch": False, "--no-optional-locks": False, "--no-pager": False,
    "--no-replace-objects": False, "--noglob-pathspecs": False, "--paginate": False,
}
# The configuration -c or --config-env may set and leave the command as the reading reads it: none of these names a
# command git runs, a file it reads more configuration from or where its hooks are. Any other key reads as any
# command - `alias.x=!sh` runs a shell, `core.fsmonitor` a program, `include.path` a file that may set
# `core.hooksPath` - and `core.hooksPath` itself redirects the commit hook (ALLOWLIST-DEPTH).
INERT_GIT_CONFIG = re.compile(r"user\.(name|email)|(color|advice)\..+|core\.(quotepath|abbrev)|init\.defaultbranch|"
                              r"safe\.directory")
@lru_cache(maxsize=1)
def _ao_tables():
    """Every ao command's options, derived lazily to avoid the cli/allowlist import cycle."""
    from . import cli
    parser = cli.build_parser()
    commands = next(action.choices for action in parser._actions if isinstance(action.choices, dict))
    return {name: {flag: action.nargs != 0 for action in command._actions for flag in action.option_strings}
            for name, command in {None: parser, **commands}.items()}


# Directory-valued ao options, scoped by command so note --to and email --from retain their meanings.
# Only the global, separate word pair `-C .` is a proven no-op; every other spelling fails closed.
AO_DIRECTORY_OPTIONS = {None: ("-C", "--root"), "backup": ("--to",), "content": ("--from",)}


GRANT_ALL = ("--dangerously-skip-permissions", "--trust-all-tools", "--yolo", "--full-auto",
             "--dangerously-bypass-approvals-and-sandbox")

# The tool a `--trust-tools` list trusts every command through: Kiro trusts tools by name, and its shell
# tool runs whatever command it is given (kiro.dev/docs/reference/built-in-tools, read 2026-09-30).
SHELL_TOOLS = ("bash", "shell", "execute_bash", "execute_cmd", "*")


def admits(rule, command):
    """Would this one Claude Code permission rule let `command` run?

    A `*` stands for any text, spaces included, wherever it is in the rule, and a trailing
    `:*` is written the same as a trailing ` *`, which also admits the bare command
    (code.claude.com/docs/en/permissions, "Wildcard patterns", read 2026-09-17). Read as a
    prefix only, `Bash(rm agent-mail/*)` looked like one directory's files and admits
    `rm agent-mail/x src/app.py`.

    One command is asked at a time, as the harness asks: it splits a compound command at `&&`,
    `||`, `;`, `|`, `|&`, `&` and newlines and runs it only when a rule admits each part, so
    `Bash(git add *)` does not run `git add . && git commit --no-verify` (the same page,
    "Compound commands", read 2026-09-30). A forbidden command is forbidden standing alone.
    """
    rule = str(rule).strip()
    if rule in ("Bash", "Bash(*)"):
        return True
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    # A run of wildcards admits what one does. Asked as written, each `*` a `.*` the matcher tried every split of,
    # `Bash(********Z)` did not answer one command in ten seconds, and the audit asks each rule a hundred
    # (ALLOWLIST-DEPTH).
    # A program word the rule leaves to a wildcard is matched as the harness matches it, the command's as written too:
    # read by its name, `Bash(/usr/*/git status:*)` admitted `git status` and not `/usr/bin/python3 -c x /tmp/git
    # status` (ALLOWLIST-DEPTH).
    head = rule[len("Bash("):-1]
    wild = _wild_program(head)
    body = re.sub(r"\*{2,}", "*", head if wild else _program_word(head))
    command = str(command) if wild else _program_word(command)
    if body.endswith(":*"):
        body = body[:-2] + " *"
    if "*" not in body:
        return command == body
    if body.endswith(" *") and body.count("*") == 1 and command == body[:-2]:
        return True
    return re.fullmatch(".*".join(re.escape(part) for part in body.split("*")), command, re.S) is not None


def _program_file(word):
    """A path, drive-relative name or stream is a file, never a trusted bare program."""
    return any(char in word for char in "/\\:")


def _program_name(word):
    """Fold a bare Windows name only; never discard a program file's path or stream."""
    if _program_file(word):
        return word
    program = word.strip("\"'").upper().casefold().rstrip(". ")
    for extension in (".exe", ".cmd", ".bat", ".com"):
        if program.endswith(extension):
            return program[:-len(extension)]
    return program


# A command's or a rule's program word, ending at a space or at a rule's `:*`; a quoted one is one word, spaces and all:
# `"C:\Program Files\...\python.exe"`.
PROGRAM_WORD = re.compile(r"(?:\"([^\"]*)\"|'([^']*)'|(\S+?))(:\*|\s|$)(.*)", re.S)


def _program_word(text):
    """A command or a rule with its program read as Windows starts it - `powershell.exe -c x` is `powershell -c x` -
    the program ending at a space or at a rule's `:*`. Each forbidden command was asked as one spelling, and a rule
    written with another admitted it unnamed (the review of WINDOWS-SAY-WHY).

    A program word with a wildcard in it is left as it is, its folder too: read as its name, `Bash(/usr/*/git
    status:*)` was asked as `Bash(git status:*)` (ALLOWLIST-DEPTH)."""
    found = PROGRAM_WORD.match(str(text))
    if not found or _wild_program(text):
        return str(text)
    program = next(group for group in found.groups()[:3] if group is not None)
    return _program_name(program) + found.group(4) + found.group(5)


def _wild_program(text):
    """Whether a rule's program word holds a wildcard anywhere, its folder included (ALLOWLIST-DEPTH)."""
    found = PROGRAM_WORD.match(str(text))
    return bool(found) and "*" in next(group for group in found.groups()[:3] if group is not None)


# Programs that run whatever code they are handed: an interpreter or a shell. A rule that grants one runs anything,
# whatever arguments it fixes, so the audit reads it as a closed world rather than as spellings to list; only an exact
# command asking its version or its help is no grant of code (the review of WINDOWS-SAY-WHY). Each by its versioned
# name too, as a distribution installs it: only python's was read so, and `Bash(ruby3.2 -e:*)` passed
# (ALLOWLIST-DEPTH).
INTERPRETERS = re.compile(r"(python|pythonw|pypy|py|node|nodejs|deno|bun|ruby|perl|php|lua|powershell|pwsh|cmd|bash|"
                          r"sh|zsh|fish|dash|ksh|csh|tcsh|osascript|wscript|cscript|mshta)(\d+(\.\d+)*)?")
HARMLESS_ARGUMENTS = {"--version", "-v", "-V", "--help", "-h", "/?"}


def interpreter_rule(rule):
    """The interpreter a rule grants with arguments that could hand it code, or None (WINDOWS-SAY-WHY).

    It is read as the command the rule runs; a rule read as any command at all is named as that (ALLOWLIST-DEPTH)."""
    reading = _rule_reading(rule)
    if not reading:
        return None
    said, tail = reading[0], reading[1]
    if not said or not INTERPRETERS.fullmatch(said[0]):
        return None
    if tail is None and _harmless(said[1:]):
        return None
    return said[0]


def _permission_values(argv, flags, prompt=None):
    """Read both variadic grant forms without treating ao's prompt as a tool (TRIAGE-3-MEDIUM)."""
    out = []
    for i, arg in enumerate(argv):
        flag, equals, value = str(arg).partition("=")
        if flag not in flags:
            continue
        values = [value] if equals else []
        after = i + 1
        while after < len(argv) and not str(argv[after]).startswith("-"):
            values.append(str(argv[after]))
            after += 1
        for value in values:
            if value != prompt and not re.fullmatch(r"\{[a-z_]+\}", value):
                out.extend(_tools(value))
    return out


def rules(argv, prompt=None):
    """Every word of a multi-value tool grant, excluding ao's prompt (TRIAGE-3-MEDIUM)."""
    return _permission_values(argv, ("--allowedTools", "--allowed-tools"), prompt)


def _tools(value):
    """Use the same tool-list grammar as grant rewriting and role pins."""
    from . import lib as A
    return A.split_tool_list(value)


def trusted_tools(argv, prompt=None):
    """Tools trusted by name, including every variadic value (TRIAGE-3-MEDIUM)."""
    return _permission_values(argv, ("--trust-tools",), prompt)


def grants_everything(argv, options=None):
    """Does this argv, as ao would run it, grant every tool?

    The watchdog starts an argv that carries no scope of its own with what its adapter
    declares for a turn nobody attends (`options.unattended`) or, where it declares none,
    its trust_all: such a harness has no allowlist, and grants every tool either way unless
    what is appended scopes it.
    """
    from . import lib as A
    args = [str(arg) for arg in argv]
    adapter = A.command_adapter(args)
    trust = ((adapter or {}).get("options") or {}).get("trust_all") or []
    if (any(arg in GRANT_ALL for arg in args) or A.bypass_arguments(args)
            or (trust and any(args[i:i + len(trust)] == trust for i in range(len(args))))):
        return True
    if A.carries_scope(args):
        return False
    options = options or {}
    if isinstance(options.get("unattended"), list):
        return not A.carries_scope(options["unattended"])
    return "trust_all" in options


def problems(argv, options=None, role=None, prompt=None):
    """(reason, command, rule) for everything this grant admits that it must not.

    A grant of every tool is one finding with command and rule '*'. The rules are the
    argv's own and those its adapter's `options.unattended` appends, each with what a
    command rewriter makes of it, as a turn ao starts holds them (GRANTS-RTK); and each
    forbidden command is asked in the forms a rewriter would run it as, too.
    """
    from . import lib as A
    if grants_everything(argv, options):
        return [("grants every tool", "*", "*")]
    appended = A.unattended_flags({"options": options or {}}, argv)[0]
    shell = next((tool for tool in trusted_tools(list(argv) + appended, prompt=prompt)
                  if tool.lower() in SHELL_TOOLS or tool == "Bash(*)"), None)
    if shell:
        return [("trusts the shell tool, which runs every command", "*", f"--trust-tools {shell}")]
    granted = rules(A.admit_rewrites(list(argv), prompt=prompt)[0] + appended, prompt=prompt)
    forbidden = FORBIDDEN + (IMPLEMENTER_FORBIDDEN if role == "implementer" else ()) \
        + (ARCHITECT_FORBIDDEN if role == "architect" else ())
    found = []
    for reason, command in forbidden + through_rewriters(forbidden):
        rule = next((r for r in granted if admits(r, command)), None)
        if rule:
            found.append((reason, command, rule))
    # A rule that admits a forbidden command only as the command it runs - behind a program that runs what
    # follows it, a rewriter's wrapper or git's global options - is named once, for the first such command,
    # and a rule already named for a form it admits as it stands is not named again (ALLOWLIST-NORMALIZE).
    named = {rule for _, _, rule in found}
    plains = {rule: normalized_rule(rule) for rule in granted}
    unwrapped = [(rule, plains[rule]) for rule in granted if rule not in named and plains[rule]]
    for reason, command in forbidden:
        for rule, plain in unwrapped:
            if rule not in named and admits(plain, command):
                found.append((f"{reason}, as the command it runs", command, rule))
                named.add(rule)
    # An interpreter or a shell is named whatever its rule fixes after it (WINDOWS-SAY-WHY).
    for rule in granted:
        program = interpreter_rule(rule) if rule not in named else None
        if program:
            found.append(("runs arbitrary code", f"{program} ...", rule))
            named.add(rule)
    # Every other rule is asked what its own command does: an exact rule was compared with the forbidden commands as
    # strings, so `Bash(git commit --no-verify -m y)` skipped the hook unnamed beside the sample's `-m x`, and a
    # person's act written out in full passed but for the words that start it (GRANTS-PREFIX, ALLOWLIST-DEPTH).
    operations = OPERATIONS + (IMPLEMENTER_OPERATIONS if role == "implementer" else ()) \
        + (ARCHITECT_OPERATIONS if role == "architect" else ())
    effects = {rule: [] for rule in granted}
    readings = {rule: _rule_reading(rule, effects[rule]) for rule in granted}
    if role == "architect":
        for rule in granted:
            if rule not in named and any(path is None or _outside(path) for path in effects[rule]):
                found.append(("writes a product file", "wrapper output", rule))
                named.add(rule)
    _name_operations(found, named, granted, readings, operations)
    if role == "implementer":
        # Every setting ao reads governs the loop an implementer works in - its round budget, its review's
        # timeout, how long a waiver lasts - and only `review_timeout` was asked, so a grant of
        # `ao config set round_budget:*` went unnamed. Each rule is named once, for the first it admits
        # (SETTINGS-2), as it stands or as the command it runs: `Bash(timeout 60 ao config set round_budget:*)`
        # was asked only as it stands, and passed (ALLOWLIST-DEPTH).
        for rule in granted:
            command = next((c for c in settings_commands()
                            if admits(rule, c) or plains[rule] and admits(plains[rule], c)), None) \
                if rule not in named else None
            if command:
                found.append(("changes a setting that governs its own work", command, rule))
                named.add(rule)
        # And by what its own command does, as every forbidden command is: `ao config set round_budget 9`
        # (ALLOWLIST-DEPTH).
        _name_operations(found, named, granted, readings, _settings_operations())
    return found


def _name_operations(found, named, granted, readings, operations):
    """Name each rule not named yet for the first operation its command performs, or could, past its fixed words: an
    exact rule by the command it runs, a rule with a wildcard by the operation it leaves to it (ALLOWLIST-DEPTH)."""
    for rule in granted:
        reading = readings.get(rule)
        if rule in named or reading is False:
            continue
        said, tail, config = reading[:3] if reading else ([], "", ())
        operation = next(((reason, shown) for reason, shown, performs in operations
                          if reading is None or performs(said, tail, config)), None)
        if operation:
            reason, shown = operation
            # An exact rule is shown as the command it runs, with the configuration git is handed.
            runs = " ".join(_configured_words(said, config))
            found.append((reason, runs if reading and tail is None else f"{shown} ...", rule))
            named.add(rule)


def settings_commands():
    """`ao config set` for each setting ao reads, as an implementer's grant is asked it (SETTINGS-2)."""
    from . import settings as S
    return tuple(f"ao config set {key} 1" for key in sorted(S.SETTINGS))


# ---- the reading: a rule as the command it runs ------------------------------------------------------------------

def _lex(text):
    r"""Read plain space-separated words and at most one terminal prefix wildcard.

    No quotes, escapes, expansions or redirections are interpreted. Shipped grants
    need none of them. The only wildcard is a final, separate `*` word; `:*` is converted to
    ` *` by _rule_body. Everything else fails closed before any option is read.
    """
    prefix = text[:-1] if text.endswith("*") else text
    if any(c in "\"'\\;&|<>()$`!{}*?[]^#%" or c.isspace() and c != " "
           or ord(c) < 32 or ord(c) == 127 for c in prefix):
        return None
    words = [word for word in prefix.split(" ") if word]
    if any(word.startswith(("=", "~")) or "=~" in word or ":~" in word for word in words):
        return None                    # command or tilde expansion, not git's HEAD~1
    if not text.endswith("*"):
        return words, None, ""
    if not prefix or prefix.endswith(" "):
        return words, "", "*"
    return None  # An attached wildcard leaves a word unknown, even if its prefix is a known command.


def _rewriters():
    """The command rewriters' own names, and the words of each of their wrappers that runs the command after it."""
    from . import lib as A
    names, runners = set(), []
    for rewriter in A.command_rewriters():
        names.add(_program_name(str(rewriter["command"]).strip()))
        declared = rewriter.get("runs_any_command")
        runners.extend([_program_name(runner.split()[0])] + runner.split()[1:]
                       for runner in (declared if isinstance(declared, list) else [])
                       if isinstance(runner, str) and runner.strip())
    return names, runners


def _letters(short):
    """A getopt short-option string as {letter: 0 for none, 1 for a value, 2 for one only attached}."""
    found, at = {}, 0
    while at < len(short):
        colons = len(short[at + 1:]) - len(short[at + 1:].lstrip(":"))
        found[short[at]] = min(colons, 2)
        at += 1 + colons
    return found


def _options(words, short, long, at=0, interspersed=True, numbers=False):
    """Read getopt options once, including clusters and values that begin with '-'.

    Return (operands, options with their values, next index, waiting for a value).
    None means an option is not in the grammar. Callers must propagate that result;
    they cannot assume an unknown option takes no value and resume at the next word.
    """
    letters, operands, given, ended = _letters(short), [], [], False
    while at < len(words):
        word = words[at]
        if ended or not word.startswith("-") or word == "-":
            if not interspersed:
                break
            operands.append(word)
        elif word == "--":
            if not interspersed:
                return operands, given, at + 1, False
            ended = True
        elif numbers and re.fullmatch(r"-[-+]?\d+", word):
            # `nice -5`: the adjustment stands as one number, recorded under the pseudo-option the
            # wrappers of WRAPPER_NUMBERS declare inert; the reading walks past it, not into an
            # unknown option (ALLOWLIST-DEPTH).
            given.append(("-number", word))
        elif word.startswith("--"):
            asked, equals, value = word[2:].partition("=")
            matches = [option for option in long if option.rstrip("=?") == asked] or [
                option for option in long if asked and option.rstrip("=?").startswith(asked)]
            if len(matches) != 1:
                return None
            option = matches[0]
            if equals and not option.endswith(("=", "=?")):
                return None
            if option.endswith("=") and not equals:
                if at + 1 == len(words):
                    return operands, given + [("--" + option.rstrip("=?"), None)], len(words), True
                at += 1
                value = words[at]
            given.append(("--" + option.rstrip("=?"), value if equals or option.endswith("=") else None))
        else:
            for index, letter in enumerate(word[1:], 1):
                kind = letters.get(letter)
                if kind is None:
                    return None
                value = None
                if kind:
                    value = word[index + 1:] or None
                    if value is None and kind == 1:
                        if at + 1 == len(words):
                            return operands, given + [("-" + letter, None)], len(words), True
                        at += 1
                        value = words[at]
                given.append(("-" + letter, value))
                if kind:
                    break
        at += 1
    return operands, given, at, False


# Only these option grammars support exact semantic readings. New/unknown options
# fail closed, so additions to a tool cannot silently acquire a harmless meaning.
FILE_OPTIONS = {
    "cp": ("aBbcdfHilLnPpRrsS:t:Tuvx", ("archive", "attributes-only", "backup=?", "copy-contents", "debug",
        "force", "interactive", "link", "no-clobber", "no-dereference", "parents", "preserve=?", "no-preserve=",
        "recursive", "reflink=?", "remove-destination", "sparse=", "strip-trailing-slashes", "suffix=",
        "symbolic-link", "target-directory=", "no-target-directory", "update=?", "verbose", "one-file-system",
        "context=?", "help", "version")),
    "mv": ("bfhinS:t:Tuv", ("backup=?", "debug", "exchange", "force", "interactive", "no-clobber", "no-copy",
        "strip-trailing-slashes", "suffix=", "target-directory=", "no-target-directory", "update=?", "verbose",
        "context", "help", "version")),
    "rm": ("dfiIPRrvW", ("dir", "force", "interactive=?", "one-file-system", "no-preserve-root", "preserve-root=?",
        "recursive", "verbose", "help", "version")),
    "tee": ("aip", ("append", "ignore-interrupts", "output-error=?", "help", "version")),
}
GIT_CONFIG_READS = ("--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool", "--list", "-l")
GIT_CONFIG_WRITES = ("set", "unset", "rename-section", "remove-section", "edit")
GIT_CONFIG_WRITE_OPTIONS = ("--add", "--replace-all", "--unset", "--unset-all", "--rename-section", "--remove-section",
                            "--edit", "-e", "--append")
GIT_CONFIG_OPTIONS = ("f:t:zle", tuple(option[2:] for option in GIT_CONFIG_READS + GIT_CONFIG_WRITE_OPTIONS
                                      if option.startswith("--")) + (
    "global", "system", "local", "worktree", "file=", "blob=", "type=", "bool", "int", "bool-or-int", "path",
    "expiry-date", "null", "name-only", "show-origin", "show-scope", "includes", "no-includes", "default=",
    "fixed-value", "comment=", "no-comment", "value=", "all", "regexp", "url=", "help"))
GIT_DIFF_OPTIONS = ("psuU:WqvabRwzB::M::C::D", ("patch", "no-patch", "stat=?", "numstat", "shortstat", "summary",
    "name-only", "name-status", "check", "binary", "full-index", "color=?", "no-color", "word-diff=?",
    "no-ext-diff", "no-textconv", "cached", "staged", "relative=?", "reverse", "quiet", "exit-code", "output=",
    "unified=", "function-context", "ignore-all-space", "ignore-space-change", "help"))
GIT_LOG_OPTIONS = (GIT_DIFF_OPTIONS[0] + "n:", GIT_DIFF_OPTIONS[1] + (
    "oneline", "pretty=?", "format=", "decorate=?", "no-decorate", "all", "graph", "first-parent",
    "no-merges", "merges", "max-count=", "since=", "until=", "author=", "grep=", "date=", "abbrev-commit"))
GIT_TOOL_OPTIONS = {
    "config": GIT_CONFIG_OPTIONS,
    "commit": ("aC:c:F:m:t:nqvseuS::", ("all", "reuse-message=", "reedit-message=", "file=", "message=", "template=",
        "no-verify", "no-edit", "edit", "amend", "signoff", "quiet", "verbose", "allow-empty", "allow-empty-message",
        "dry-run", "only", "include", "interactive", "patch", "reset-author", "author=", "date=", "fixup=",
        "squash=", "cleanup=", "status", "no-status", "gpg-sign=?", "no-gpg-sign", "help")),
    "status": ("sbzuv", ("short", "branch", "porcelain=?", "long", "verbose", "null", "show-stash",
        "untracked-files=?", "ignored=?", "ignore-submodules=?", "column=?", "no-column", "help")),
    "add": ("nvpifAuN", ("dry-run", "verbose", "patch", "interactive", "force", "update", "all", "intent-to-add",
        "refresh", "ignore-errors", "ignore-missing", "renormalize", "chmod=", "pathspec-from-file=",
        "pathspec-file-nul", "help")),
    "format-patch": (GIT_LOG_OPTIONS[0] + "o:", GIT_LOG_OPTIONS[1] + ("output-directory=", "stdout", "cover-letter",
        "numbered", "no-numbered", "start-number=", "suffix=", "subject-prefix=", "to=", "cc=", "thread=?")),
    "archive": ("o:v", ("output=", "format=", "prefix=", "remote=", "exec=", "list", "verbose", "help")),
}
GIT_TOOL_OPTIONS.update({name: GIT_DIFF_OPTIONS for name in ("diff", "diff-files", "diff-index", "diff-tree", "range-diff")})
GIT_TOOL_OPTIONS.update({name: GIT_LOG_OPTIONS for name in ("log", "show", "whatchanged", "stash")})
TOOL_OPTIONS = dict(FILE_OPTIONS, **{
    "ls": ("aAbBcCdDfFgGhHiklLmnpqQrRsStTuUvwx1", ("all", "almost-all", "color=?", "classify=?", "directory", "help",
        "version", "human-readable", "recursive", "reverse", "size", "sort=", "time=", "format=", "quoting-style=")),
    "cat": ("AbeEnstTuv", ("show-all", "number-nonblank", "show-ends", "number", "squeeze-blank", "show-tabs",
        "show-nonprinting", "help", "version")),
    "pytest": ("qvxsra::k:m:p:c:o:n:", ("quiet", "verbose", "exitfirst", "maxfail=", "disable-warnings", "collect-only",
        "tb=", "help", "version", "no-header", "no-summary", "strict-markers", "strict-config")),
    "npm": ("v", ("version", "help", "prefix=", "workspace=", "workspaces", "silent")),
    "uv": ("qv", ("help", "version", "offline", "directory=", "project=", "no-progress")),
    "find": ("", ()),  # find's expression grammar is validated separately
})
TOOL_OPTIONS["py.test"] = TOOL_OPTIONS["pytest"]
# These shipped instruments have no fixed options in their grants. Any explicit
# option not declared here is unknown; a trailing wildcard remains an open prefix.
TOOL_OPTIONS.update({name: ("", ()) for name in ("grep", "head", "tail", "ps", "lsof", "read")})


# Only these script-command paths are understood. Other uv tool operations, such
# as installation, are deliberately unknown rather than guessed harmless.
SCRIPT_COMMANDS = {
    "npm": {"test": True, "run": True, "run-script": True, "rum": True, "urn": True, "exec": True, "x": True},
    "uv": {"sync": True, "run": True, "tool": {"run": True}},
}


# A grammar is not an effect classification. Every admitted git command has a policy;
# commands outside this map remain unknown even if an option grammar is later supplied.
# The four shipped read/add commands retain their existing implicit-config allowance.
# Explicit program selection is never covered by that allowance.
GIT_EFFECTS = {
    "status": "shipped-read", "diff": "shipped-read", "log": "shipped-read", "add": "index",
    "commit": "code", "config": "config", "format-patch": "file", "archive": "archive", "stash": "stash",
    "diff-files": "code", "diff-index": "code", "diff-tree": "code", "range-diff": "code",
    "show": "code", "whatchanged": "code",
}


def _option_names(table):
    return {"-" + letter for letter in _letters(table[0])} | {"--" + word.rstrip("=?") for word in table[1]}


# Explicit inert inventory: adding parser syntax alone grants no new option semantics.
# These options only modify the already classified command. Side effects are listed below.
INERT_OPTIONS = {
    ('wrapper', 'env'): (
        '--block-signal --debug --default-signal --help --ignore-environment --ignore-signal '
        '--list-signal-handling --null --unset --version -0 -i -u -v'),
    ('wrapper', 'timeout'): (
        '--foreground --help --kill-after --preserve-status --signal --verbose --version -f -k -p -s '
        '-v'),
    ('wrapper', 'nice'): (
        '--adjustment --help --version -n -number'),
    ('wrapper', 'nohup'): (
        '--help --version'),
    ('wrapper', 'stdbuf'): (
        '--error --help --input --output --version -e -i -o'),
    ('wrapper', 'xargs'): (
        '--arg-file --delimiter --eof --exit --help --interactive --max-args --max-chars --max-lines '
        '--max-procs --no-run-if-empty --null --open-tty --process-slot-var --replace --show-limits '
        '--verbose --version -0 -E -I -J -L -P -R -S -a -d -e -i -l -n -o -p -r -s -t -x'),
    ('wrapper', 'time'): (
        '--append --format --help --portability --quiet --verbose --version -V -a -f -h -l -p -q -v'),
    ('wrapper', 'command'): (
        '-V -p -v'),
    ('wrapper', 'exec'): (
        '-c -l'),
    ('wrapper', 'builtin'): (
        ''),
    ('wrapper', 'noglob'): (
        ''),
    ('wrapper', 'nocorrect'): (
        ''),
    ('wrapper', '-'): (
        ''),
    ('wrapper', '!'): (
        ''),
    ('wrapper', 'coproc'): (
        ''),
    ('wrapper', 'caffeinate'): (
        '-d -i -m -s -t -u -w'),
    ('wrapper', 'sudo'): (
        '--background --bell --close-from --command-timeout --group --help --host --list --no-update '
        '--non-interactive --other-user --preserve-env --preserve-groups --prompt --remove-timestamp '
        '--reset-timestamp --role --set-home --stdin --type --user --validate --version -B -C -E -H '
        '-K -N -P -S -T -U -V -b -g -h -k -l -n -p -r -t -u -v'),
    ('wrapper', 'genv'): (
        '--block-signal --debug --default-signal --help --ignore-environment --ignore-signal '
        '--list-signal-handling --null --unset --version -0 -i -u -v'),
    ('wrapper', 'gnice'): (
        '--adjustment --help --version -n -number'),
    ('wrapper', 'gnohup'): (
        '--help --version'),
    ('wrapper', 'gstdbuf'): (
        '--error --help --input --output --version -e -i -o'),
    ('wrapper', 'gtime'): (
        '--append --format --help --portability --quiet --verbose --version -V -a -f -h -l -p -q -v'),
    ('wrapper', 'gtimeout'): (
        '--foreground --help --kill-after --preserve-status --signal --verbose --version -f -k -p -s '
        '-v'),
    ('wrapper', 'gxargs'): (
        '--arg-file --delimiter --eof --exit --help --interactive --max-args --max-chars --max-lines '
        '--max-procs --no-run-if-empty --null --open-tty --process-slot-var --replace --show-limits '
        '--verbose --version -0 -E -I -J -L -P -R -S -a -d -e -i -l -n -o -p -r -s -t -x'),
    ('tool', 'cp'): (
        '--archive --attributes-only --context --copy-contents --debug --force --help --interactive '
        '--link --no-clobber --no-dereference --no-preserve --no-target-directory --one-file-system '
        '--parents --preserve --recursive --reflink --remove-destination --sparse '
        '--strip-trailing-slashes --symbolic-link --update --verbose --version -B -H -L -P -R -T -a '
        '-c -d -f -i -l -n -p -r -s -u -v -x'),
    ('tool', 'mv'): (
        '--context --debug --exchange --force --help --interactive --no-clobber --no-copy '
        '--no-target-directory --strip-trailing-slashes --update --verbose --version -T -f -h -i -n '
        '-u -v'),
    ('tool', 'rm'): (
        '--dir --force --help --interactive --no-preserve-root --one-file-system --preserve-root '
        '--recursive --verbose --version -I -P -R -W -d -f -i -r -v'),
    ('tool', 'tee'): (
        '--append --help --ignore-interrupts --output-error --version -a -i -p'),
    ('tool', 'ls'): (
        '--all --almost-all --classify --color --directory --format --help --human-readable '
        '--quoting-style --recursive --reverse --size --sort --time --version -1 -A -B -C -D -F -G '
        '-H -L -Q -R -S -T -U -a -b -c -d -f -g -h -i -k -l -m -n -p -q -r -s -t -u -v -w -x'),
    ('tool', 'cat'): (
        '--help --number --number-nonblank --show-all --show-ends --show-nonprinting --show-tabs '
        '--squeeze-blank --version -A -E -T -b -e -n -s -t -u -v'),
    ('tool', 'pytest'): (
        '--collect-only --disable-warnings --exitfirst --help --maxfail --no-header --no-summary '
        '--quiet --strict-config --strict-markers --tb --verbose --version -a -k -m -n -q -r -s -v '
        '-x'),
    ('tool', 'npm'): (
        '--help --silent --version -v'),
    ('tool', 'uv'): (
        '--help --no-progress --offline --version -q -v'),
    ('tool', 'find'): (
        ''),
    ('tool', 'py.test'): (
        '--collect-only --disable-warnings --exitfirst --help --maxfail --no-header --no-summary '
        '--quiet --strict-config --strict-markers --tb --verbose --version -a -k -m -n -q -r -s -v '
        '-x'),
    ('tool', 'grep'): (
        ''),
    ('tool', 'head'): (
        ''),
    ('tool', 'tail'): (
        ''),
    ('tool', 'ps'): (
        ''),
    ('tool', 'lsof'): (
        ''),
    ('tool', 'read'): (
        ''),
    ('git', 'config'): (
        '--all --blob --bool --bool-or-int --comment --default --expiry-date --fixed-value --get '
        '--get-all --get-color --get-colorbool --get-regexp --get-urlmatch --help --includes --int '
        '--list --name-only --no-comment --no-includes --null --path --regexp --show-origin '
        '--show-scope --type --url --value -l -t -z'),
    ('git', 'commit'): (
        '--all --allow-empty --allow-empty-message --amend --author --cleanup --date --dry-run '
        '--file --fixup --help --include --message --no-edit --no-gpg-sign --no-status --no-verify '
        '--only --quiet --reset-author --reuse-message --signoff --squash --status --template '
        '--verbose -C -F -a -m -n -q -s -t -u -v'),
    ('git', 'status'): (
        '--branch --column --help --ignore-submodules --ignored --long --no-column --null '
        '--porcelain --short --show-stash --untracked-files --verbose -b -s -u -v -z'),
    ('git', 'add'): (
        '--all --chmod --dry-run --force --help --ignore-errors --ignore-missing --intent-to-add '
        '--interactive --patch --pathspec-file-nul --pathspec-from-file --refresh --renormalize '
        '--update --verbose -A -N -f -i -n -p -u -v'),
    ('git', 'format-patch'): (
        '--abbrev-commit --all --author --binary --cached --cc --check --color --cover-letter --date '
        '--decorate --exit-code --first-parent --format --full-index --function-context --graph '
        '--grep --help --ignore-all-space --ignore-space-change --max-count --merges --name-only '
        '--name-status --no-color --no-decorate --no-ext-diff --no-merges --no-numbered --no-patch '
        '--no-textconv --numbered --numstat --oneline --patch --pretty --quiet --relative --reverse '
        '--shortstat --since --staged --start-number --stat --stdout --subject-prefix --suffix '
        '--summary --thread --to --unified --until --word-diff -B -C -D -M -R -U -W -a -b -n -p -q '
        '-s -u -v -w -z'),
    ('git', 'archive'): (
        '--help --list --prefix --verbose -v'),
    ('git', 'diff'): (
        '--binary --cached --check --color --exit-code --full-index --function-context --help '
        '--ignore-all-space --ignore-space-change --name-only --name-status --no-color --no-ext-diff '
        '--no-patch --no-textconv --numstat --patch --quiet --relative --reverse --shortstat '
        '--staged --stat --summary --unified --word-diff -B -C -D -M -R -U -W -a -b -p -q -s -u -v '
        '-w -z'),
    ('git', 'diff-files'): (
        '--binary --cached --check --color --exit-code --full-index --function-context --help '
        '--ignore-all-space --ignore-space-change --name-only --name-status --no-color --no-ext-diff '
        '--no-patch --no-textconv --numstat --patch --quiet --relative --reverse --shortstat '
        '--staged --stat --summary --unified --word-diff -B -C -D -M -R -U -W -a -b -p -q -s -u -v '
        '-w -z'),
    ('git', 'diff-index'): (
        '--binary --cached --check --color --exit-code --full-index --function-context --help '
        '--ignore-all-space --ignore-space-change --name-only --name-status --no-color --no-ext-diff '
        '--no-patch --no-textconv --numstat --patch --quiet --relative --reverse --shortstat '
        '--staged --stat --summary --unified --word-diff -B -C -D -M -R -U -W -a -b -p -q -s -u -v '
        '-w -z'),
    ('git', 'diff-tree'): (
        '--binary --cached --check --color --exit-code --full-index --function-context --help '
        '--ignore-all-space --ignore-space-change --name-only --name-status --no-color --no-ext-diff '
        '--no-patch --no-textconv --numstat --patch --quiet --relative --reverse --shortstat '
        '--staged --stat --summary --unified --word-diff -B -C -D -M -R -U -W -a -b -p -q -s -u -v '
        '-w -z'),
    ('git', 'range-diff'): (
        '--binary --cached --check --color --exit-code --full-index --function-context --help '
        '--ignore-all-space --ignore-space-change --name-only --name-status --no-color --no-ext-diff '
        '--no-patch --no-textconv --numstat --patch --quiet --relative --reverse --shortstat '
        '--staged --stat --summary --unified --word-diff -B -C -D -M -R -U -W -a -b -p -q -s -u -v '
        '-w -z'),
    ('git', 'log'): (
        '--abbrev-commit --all --author --binary --cached --check --color --date --decorate '
        '--exit-code --first-parent --format --full-index --function-context --graph --grep --help '
        '--ignore-all-space --ignore-space-change --max-count --merges --name-only --name-status '
        '--no-color --no-decorate --no-ext-diff --no-merges --no-patch --no-textconv --numstat '
        '--oneline --patch --pretty --quiet --relative --reverse --shortstat --since --staged --stat '
        '--summary --unified --until --word-diff -B -C -D -M -R -U -W -a -b -n -p -q -s -u -v -w -z'),
    ('git', 'show'): (
        '--abbrev-commit --all --author --binary --cached --check --color --date --decorate '
        '--exit-code --first-parent --format --full-index --function-context --graph --grep --help '
        '--ignore-all-space --ignore-space-change --max-count --merges --name-only --name-status '
        '--no-color --no-decorate --no-ext-diff --no-merges --no-patch --no-textconv --numstat '
        '--oneline --patch --pretty --quiet --relative --reverse --shortstat --since --staged --stat '
        '--summary --unified --until --word-diff -B -C -D -M -R -U -W -a -b -n -p -q -s -u -v -w -z'),
    ('git', 'whatchanged'): (
        '--abbrev-commit --all --author --binary --cached --check --color --date --decorate '
        '--exit-code --first-parent --format --full-index --function-context --graph --grep --help '
        '--ignore-all-space --ignore-space-change --max-count --merges --name-only --name-status '
        '--no-color --no-decorate --no-ext-diff --no-merges --no-patch --no-textconv --numstat '
        '--oneline --patch --pretty --quiet --relative --reverse --shortstat --since --staged --stat '
        '--summary --unified --until --word-diff -B -C -D -M -R -U -W -a -b -n -p -q -s -u -v -w -z'),
    ('git', 'stash'): (
        '--abbrev-commit --all --author --binary --cached --check --color --date --decorate '
        '--exit-code --first-parent --format --full-index --function-context --graph --grep --help '
        '--ignore-all-space --ignore-space-change --max-count --merges --name-only --name-status '
        '--no-color --no-decorate --no-ext-diff --no-merges --no-patch --no-textconv --numstat '
        '--oneline --patch --pretty --quiet --relative --reverse --shortstat --since --staged --stat '
        '--summary --unified --until --word-diff -B -C -D -M -R -U -W -a -b -n -p -q -s -u -v -w -z'),
    ('global', 'git'): (
        '--glob-pathspecs --html-path --icase-pathspecs --info-path --list-cmds --literal-pathspecs '
        '--man-path --no-advice --no-lazy-fetch --no-optional-locks --no-pager --no-replace-objects '
        '--noglob-pathspecs -P'),
}
OPTION_EFFECTS = {key: dict.fromkeys(options.split(), "inert") for key, options in INERT_OPTIONS.items()}
DIRECTORY_OPTIONS = {
    ("wrapper", "env"): ("-C", "--chdir"), ("wrapper", "genv"): ("-C", "--chdir"),
    ("wrapper", "sudo"): ("-D", "--chdir", "-R", "--chroot", "-i", "--login"),
    ("global", "git"): ("-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--bare"),
    ("tool", "npm"): ("--prefix", "--workspace", "--workspaces"),
    ("tool", "uv"): ("--directory", "--project"),
}
CODE_OPTIONS = {
    **{("wrapper", name): options for name, options in WRAPPER_RUNS_ANY.items()},
    ("global", "git"): ("-c", "--config-env", "--exec-path", "-p", "--paginate", "--attr-source"),
    ("git", "archive"): ("--exec", "--remote", "--format"),
    ("git", "commit"): ("-c", "--reedit-message", "-e", "--edit", "-S", "--gpg-sign", "--interactive", "--patch"),
    ("git", "config"): ("-e", "--edit"),
    ("tool", "pytest"): ("-p", "-c", "-o"), ("tool", "py.test"): ("-p", "-c", "-o"),
}
FILE_OUTPUT_OPTIONS = {
    ("wrapper", "time"): ("-o", "--output"), ("wrapper", "gtime"): ("-o", "--output"),
    ("global", "git"): ("--shallow-file",),
    **{("git", name): ("--output",) for name in
       ("diff", "diff-files", "diff-index", "diff-tree", "range-diff", "log", "show", "whatchanged", "stash")},
    ("git", "format-patch"): ("-o", "--output", "--output-directory"),
    ("git", "archive"): ("-o", "--output"),
    ("tool", "cp"): ("-t", "--target-directory", "-S", "--suffix", "-b", "--backup"),
    ("tool", "mv"): ("-t", "--target-directory", "-S", "--suffix", "-b", "--backup"),
    ("git", "config"): GIT_CONFIG_WRITE_OPTIONS + ("-f", "--file", "--global", "--system", "--local", "--worktree"),
}
for _effect, _groups in (("file", FILE_OUTPUT_OPTIONS), ("code", CODE_OPTIONS), ("directory", DIRECTORY_OPTIONS)):
    for _key, _options_with_effect in _groups.items():
        OPTION_EFFECTS[_key].update(dict.fromkeys(_options_with_effect, _effect))
OPTION_EFFECTS["tool", "find"].update(dict.fromkeys((
    "-name", "-iname", "-path", "-ipath", "-type", "-maxdepth", "-mindepth", "-mtime", "-size",
    "-print", "-print0", "-empty", "-depth", "-prune", "-H", "-L", "-P"), "inert"))
OPTION_EFFECTS["tool", "find"].update(dict.fromkeys(("-exec", "-execdir", "-ok", "-okdir"), "code"))


def _classified_options(kind, name, given):
    """Only reviewed option names, and no unresolved directory selection."""
    known = OPTION_EFFECTS.get((kind, name))
    if known is None:
        return False
    for option, value in given:
        if option not in known:
            return False
        if known[option] == "directory":
            # These are literal chdir operations. '.' cannot change the base, including
            # in a sequence; repository/project selectors are not equivalent to chdir.
            noop = ((kind == "wrapper" and name in ("env", "genv") and option in ("-C", "--chdir"))
                    or (kind == "wrapper" and name == "sudo" and option in ("-D", "--chdir"))
                    or (kind == "global" and name == "git" and option == "-C"))
            if not noop or value != ".":
                return False
    return True


def _script_command(program, operands):
    """A known leaf (runs code or not), remaining subcommands, or None for an unknown command."""
    node = SCRIPT_COMMANDS[program]
    for operand in operands:
        if not isinstance(node, dict):
            break                       # arguments to the selected command are not subcommands
        node = node.get(operand)
        if node is None:
            return None
    return node


def _tool_options(said):
    """The validated options and operands of a tool, or None for unknown options."""
    if not said:
        return [], [], 0, False
    name = said[0]
    if name == "ao":
        return _ao_options(said)
    if name == "git":
        if len(said) < 2 or said[1] == "":
            return [], [], 0, False
        table = GIT_TOOL_OPTIONS.get(said[1]) if said[1] in GIT_EFFECTS else None
        if table is None:
            if said[1] in ("-v", "--version", "-h", "--help"):
                return _options(said[2:], "", ())
            else:
                return None
        parsed = _options(said[2:], *table)
        return parsed if parsed is not None and _classified_options("git", said[1], parsed[1]) else None
    if INTERPRETERS.fullmatch(name) or name in ("make", "gmake", "npx", "uvx"):
        # Already named as running arbitrary code unless exactly help/version.
        return [], [], 0, False
    if name == "find":
        values = {"-name", "-iname", "-path", "-ipath", "-type", "-maxdepth", "-mindepth", "-mtime", "-size"}
        flags = {"-print", "-print0", "-empty", "-depth", "-prune", "-H", "-L", "-P"}
        operands, given, at = [], [], 1
        while at < len(said):
            word = said[at]
            if word.startswith("-") and word not in OPTION_EFFECTS["tool", "find"]:
                return None
            if word in ("-exec", "-execdir", "-ok", "-okdir"):
                return operands, given + [(word, None)], len(said), False  # runs its command arguments
            if word in values:
                if at + 1 == len(said):
                    return operands, given + [(word, None)], len(said), True
                given.append((word, said[at + 1]))
                at += 2
                continue
            if word in flags:
                given.append((word, None))
            elif word.startswith("-"):
                return None
            else:
                operands.append(word)
            at += 1
        return operands, given, at, False
    table = TOOL_OPTIONS.get(name)
    parsed = _options(said[1:], *table) if table is not None else None
    if parsed is not None and not _classified_options("tool", name, parsed[1]):
        return None
    if parsed is not None and name in SCRIPT_COMMANDS and _script_command(name, parsed[0]) is None:
        return None
    return parsed


def _ao_options(said):
    if len(said) < 2 or said[1] == "":
        return [], [], 0, False
    table = _ao_tables().get(said[1])
    if table is None:
        return None
    at, ended = 2, False
    while at < len(said):
        word = said[at]
        if word == "--":
            ended = True
        elif not ended and word.startswith("-") and word != "-":
            option, attached = _argparse_option(word, table)
            if option is None or option in AO_DIRECTORY_OPTIONS.get(said[1], ()):
                return None
            if table[option] and not attached:
                at += 1
        at += 1
    return [], [], 0, False


DESCRIBES = "describes"


def _wrapper(name, words, at, tail, rest, effects=None):
    """Where the command a wrapper runs starts, read past its own options and operands from `at`, as (at, words,
    tail, rest); None where the reading cannot tell what runs, DESCRIBES where it runs nothing (ALLOWLIST-DEPTH).

    getopt reads a wrapper's options until its first word that is none, or `--`: a cluster of short ones letter by
    letter, until one that takes a value, which is the rest of the cluster or the next word; a long one by any start
    of its name that names no other, its value after `=` or, for one that needs it, the next word. An option the
    declaration does not hold leaves the cluster, and what runs after it, unread; so does a wildcard among them, since
    a `*` there can be the rest of them and any command after."""
    # env's single '-' is its no-environment option, before NAME=VALUE operands.
    if name in ("env", "genv") and words[at:at + 1] == ["-"]:
        at += 1
    parsed = _options(words, *WRAPPERS[name], at=at, interspersed=False, numbers=name in WRAPPER_NUMBERS)
    if parsed is None:
        return None
    _, given, at, waiting = parsed
    if not _classified_options("wrapper", name, given):
        return None
    if effects is not None:
        effects.extend(value for option, value in given if option in FILE_OUTPUT_OPTIONS.get(("wrapper", name), ()))
        if name in ("nohup", "gnohup"):
            effects.append(None)  # nohup.out in cwd or HOME, depending on descriptors and permissions
    options = {option for option, _ in given}
    if name in XARGS and options.intersection(XARGS_REPLACES):
        return None  # Replacement may rewrite any word, including the program or subcommand.
    if waiting:
        return None if tail is not None else (len(words), words, tail, rest)
    if any(OPTION_EFFECTS["wrapper", name][option] == "code" for option in options):
        return None
    if options & set(WRAPPER_DESCRIBES.get(name, ())):
        return DESCRIBES
    at += WRAPPER_OPERANDS.get(name, 0)
    if at > len(words):
        return None if tail is not None else (len(words), words, tail, rest)
    if at == len(words) and options & set(WRAPPER_SHELLS.get(name, ())):
        return None
    if name in XARGS:
        return at, words, "", "*"  # stdin appends arguments after the fixed command words.
    return at, words, tail, rest


def _git(words, at, tail, rest):
    """git's reading from after its program: its own options taken off, and the configuration -c sets kept for what
    it does to the hook (ALLOWLIST-DEPTH)."""
    config = []
    while at < len(words):
        word = words[at]
        if word == "":
            return [], None, (), rest
        name, equals, value = word.partition("=") if word.startswith("--") else (word, "", "")
        if name not in GIT_OPTIONS:
            if word.startswith("-") and word not in ("-v", "--version", "-h", "--help"):
                return None
            break
        if name == "--exec-path" and equals:
            return None
        if equals and not GIT_OPTIONS[name]:
            return None
        if GIT_OPTIONS[name] and not equals:
            if at + 1 >= len(words):
                return None if tail is not None else ([], None, (), rest)
            at += 1
            value, word = words[at], f"{name} {words[at]}"
        at += 1
        if not _classified_options("global", "git", [(name, value)]):
            return None
        if name in ("-p", "--paginate", "--attr-source", "--shallow-file"):
            return None
        if name in ("-c", "--config-env"):
            key = value.split("=", 1)[0].lower()
            if key != "core.hookspath" and not INERT_GIT_CONFIG.fullmatch(key):
                return None
            config.append((key, word))
    if at >= len(words) and tail is not None:
        return None
    return ["git"] + words[at:], tail, tuple(config), rest


def _argparse_option(word, table):
    """(the option of `table` an argparse word names, whether its value is attached) - a long one by any start of its
    name that names no other - or (None, False)."""
    if word in table:
        return word, False
    name, equals, _ = word.partition("=")
    if equals and name in table:
        return (name, True) if table[name] else (None, False)
    if not word.startswith("--") and word[:2] in table:
        return (word[:2], True) if table[word[:2]] else (None, False)
    found = [option for option in table if option.startswith("--") and option.startswith(name)] \
        if name.startswith("--") else []
    return (found[0], bool(equals)) if len(found) == 1 and (not equals or table[found[0]]) else (None, False)


def _ao_subcommand(words, at, tail):
    """Where ao's subcommand stands, read past ao's own options from `at`; None where a wildcard or an option the
    parser does not declare stands among them."""
    table = _ao_tables()[None]
    while at < len(words):
        word = words[at]
        if word == "--":
            at += 1
            continue
        if not word.startswith("-") or word == "-":
            return at
        option, attached = _argparse_option(word, table)
        if option is None:
            return None
        if option in AO_DIRECTORY_OPTIONS[None]:
            # Do not decode attached roots: argparse keeps the '=' in -Csrc=.'s value.
            # The audit accepts only this literal pair, including no long-option alias.
            if word != "-C" or words[at + 1:at + 2] != ["."]:
                return None
        if table[option] and not attached:
            if at + 1 >= len(words):
                return None if tail is not None else len(words)
            at += 1
        at += 1
    return at


def _ao_lock(words, at):
    """Where the command `ao lock` runs starts, read past its own options from `at`; None for one it does not
    declare."""
    table = _ao_tables()["lock"]
    while at < len(words):
        word = words[at]
        if word == "--":
            return at + 1
        if not word.startswith("-") or word == "-":
            return at
        option, attached = _argparse_option(word, table)
        if option is None:
            return None
        at += 2 if table[option] and not attached else 1
    return at


def _reading(text, effects=None):
    """What a command, or a rule's text, runs: (the complete words it runs as, its program's name first; an empty
    string for a trailing argument wildcard, or None for an exact command; the git configuration; the wildcard).
    None where
    the reading cannot tell what runs, which admits any command; no words where nothing runs (ALLOWLIST-DEPTH).

    Each program that runs the command after it is taken off with its own options, however deep, and so are a
    rewriter, a shell's `NAME=VALUE` that changes nothing the reading reads, git's own options and ao's. Every program
    the reading meets is a word it needs, the last one too, and so are a wrapper's options and operands, git's and
    ao's own options and the word where they end: a wildcard in any of them, its folder included, can be the rest of
    them and any command after, and an option or a variable the reading does not know may change what runs, so each
    reads as any command."""
    lexed = _lex(text)
    if lexed is None:
        return None
    words, tail, rest = lexed
    rewriters, runners = _rewriters()
    at = 0
    while True:
        if at >= len(words):
            return None if tail is not None else ([], None, (), rest)
        word = words[at]
        if word == "":
            return [], None, (), rest
        if SHELL_ASSIGNMENT.match(word):
            if not INERT_VARIABLES.fullmatch(word.split("=", 1)[0]):
                return None
            at += 1
            continue
        if _program_file(word):
            return [word] + words[at + 1:], tail, (), rest
        name = _program_name(word)
        # A program Windows names otherwise - `gi\t`, `C:\x\git.exe`, `gi^t` for cmd.exe - is either, and a short
        # name, `POWERS~1` for powershell, is whichever long one it stands for.
        if re.search(r"~\d", name):
            return None
        if name in WRAPPERS:
            step = _wrapper(name, words, at + 1, tail, rest, effects)
            if step is None:
                return None
            if step == DESCRIBES:
                return [], None, (), rest
            at, words, tail, rest = step
            if name in ("env", "genv"):
                while at < len(words) and "=" in words[at]:
                    if not INERT_VARIABLES.fullmatch(words[at].split("=", 1)[0]):
                        return None
                    at += 1
            continue
        runner = next((runner for runner in runners
                       if runner[0] == name and words[at + 1:at + len(runner)] == runner[1:]), None)
        if runner or name in rewriters:
            at += len(runner) if runner else 1
            # Nothing declares which option of a rewriter or its wrapper takes the next word: with more words after
            # it, the program could be either.
            if words[at:at + 1] and words[at].startswith("-"):
                if words[at + 1:] or tail is not None or words[at] not in HARMLESS_ARGUMENTS:
                    return None
                return [], None, (), rest
            continue
        if name == "ao":
            sub = _ao_subcommand(words, at + 1, tail)
            if sub is None:
                return None
            if sub < len(words) and words[sub] == "lock":
                at = _ao_lock(words, sub + 1)
                if at is None:
                    return None
                continue
            if sub >= len(words) and tail is not None:
                return None
            if tail is not None and AO_DIRECTORY_OPTIONS.get(words[sub]):
                return None  # Additional arguments may select a directory the reading cannot resolve.
            if _tool_options(["ao"] + words[sub:]) is None:
                return None
            return ["ao"] + words[sub:], tail, (), rest
        if name == "git":
            reading = _git(words, at + 1, tail, rest)
            if reading is None or _tool_options(reading[0]) is None:
                return None
            return reading
        if _tool_options([name] + words[at + 1:]) is None:
            return None
        return [name] + words[at + 1:], tail, (), rest


def _rule_body(rule):
    """A Bash rule's text, a trailing `:*` written as the ` *` it means; None for a rule of another tool."""
    rule = str(rule).strip()
    if rule in ("Bash", "Bash(*)"):
        return "*"
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return None
    body = rule[len("Bash("):-1]
    return body[:-2] + " *" if body.endswith(":*") else body


def _rule_reading(rule, effects=None):
    """A rule's reading; False for a rule of another tool."""
    body = _rule_body(rule)
    return False if body is None else _reading(body, effects)


def unwrap(words):
    """The words a command runs as, with each program that runs the command after it, a rewriter's wrappers
    and git's global options taken off (ALLOWLIST-NORMALIZE).

    `timeout 60 pytest -p x` runs as `pytest -p x`, `rtk proxy git commit -n` as `git commit -n`, `rtk git -C .
    commit -n` as `git commit -n`. Every one is taken off, however deep: the reading stopped after eight, and `nice`
    nine times before `ao features off review --by p` read as a `nice` command nothing forbids. `["*"]` where the
    reading cannot tell what runs, and the text after a rule's fixed words kept as it stands (ALLOWLIST-DEPTH).
    """
    reading = _reading(" ".join(str(word) for word in words))
    if reading is None:
        return ["*"]
    said, tail, config, rest = reading
    said = _configured_words(said, config)
    return said if tail is None else said + (tail + rest).split()


def _configured_words(said, config):
    """Restore git's configuration without changing argv word boundaries."""
    return said[:1] + [word for _, option in config for word in option.split()] + said[1:]


def _spelled(words, tail, rest):
    """A reading written back as a rule's text, a lone trailing wildcard as `:*`."""
    if tail is None:
        return " ".join(words)
    return " ".join(words) + ":*"


def normalized_rule(rule):
    """The rule a rule is, read as the command it runs: `Bash(timeout 60 pytest:*)` is `Bash(pytest:*)`, and
    `Bash(nice:*)`, whose command is any at all, is `Bash(*)`. None for a rule whose command is already plain, or
    that runs none (ALLOWLIST-NORMALIZE).

    An exact rule is read as the exact command it runs: `Bash(nice git commit --no-verify -m x)` skips the hook as
    `Bash(git commit --no-verify -m x)` does. A wildcard where the reading needs a word makes a rule `Bash(*)` -
    `Bash(timeout * pytest:*)` admits `timeout 1 sh -c x pytest`, and `Bash(ls*)` admits `ls/../build.sh` - and one
    after complete words stays there: `Bash(timeout 60 git commit:*)` is `Bash(git commit:*)`. Each was left to
    the samples, which a rule behind a wrapper does not admit as it stands (ALLOWLIST-DEPTH).
    """
    body = _rule_body(rule)
    if body is None:
        return None
    reading = _reading(body)
    if reading is None:
        return "Bash(*)"
    said, tail, config, rest = reading
    said = _configured_words(said, config)
    if not said:
        return None
    plain = _spelled(said, tail, rest)
    words, own_tail, own_rest = _lex(body)
    return None if not config and plain == _spelled([_program_name(words[0])] + words[1:], own_tail, own_rest) else f"Bash({plain})"


# ---- what ao forbids, as an operation a command performs ---------------------------------------------------------

def _harmless(args):
    """Whether these arguments only ask a program its version or its help."""
    return bool(args) and all(arg in HARMLESS_ARGUMENTS for arg in args)


def _argparse_words(args, table):
    """(the positionals, the option words, whether the last option waits for its value) of an argparse command line,
    given which options take a value: an option may stand among the positionals, and everything after `--` is one.
    An option the table does not hold is taken to take none, and argparse refuses the command then anyway."""
    positionals, options, at, ended = [], [], 0, False
    while at < len(args):
        word = args[at]
        if ended or not word.startswith("-") or word == "-":
            positionals.append(word)
        elif word == "--":
            ended = True
        else:
            options.append(word)
            option, attached = _argparse_option(word, table)
            if option and table[option] and not attached:
                if at + 1 >= len(args):
                    return positionals, options, True
                at += 1
        at += 1
    return positionals, options, False


def _names(word, option):
    """Whether an argparse word names `option`: whole, before an `=`, or by a start of its name - read here as naming
    every option it starts, which can only name more."""
    name = word.split("=", 1)[0]
    return name == option or name.startswith("--") and len(name) > 2 and option.startswith(name)


def _ao_act(subcommand, positionals=(), options=()):
    """An operation of ao's own: its subcommand, the values each positional after it may hold for the operation, and
    the options it needs, each named by any start of its name. A rule with a wildcard after its fixed words performs
    it where they do not rule it out: the wildcard can add the rest (GRANTS-PREFIX, ALLOWLIST-DEPTH)."""
    def performs(said, tail, config):
        if said[:1] != ["ao"]:
            return False
        if len(said) < 2:
            return tail is not None
        if said[1] != subcommand:
            return False
        fixed, given, waiting = _argparse_words(said[2:], _ao_tables()[subcommand])
        for index, values in enumerate(positionals):
            if index < len(fixed):
                if fixed[index] not in values:
                    return False
            elif tail is None:
                return False
            else:
                return True
        return tail is not None or all(any(_names(word, option) for word in given) for option in options)
    return performs


def _git_subcommand(said, tail, names):
    """Whether a git command's subcommand is one of `names`, or may be where a rule's fixed words end before it."""
    if said[:1] != ["git"]:
        return False
    if len(said) < 2:
        return tail is not None
    return said[1] in names


def _skips_the_hook(said, tail, config):
    if not _git_subcommand(said, tail, ("commit",)):
        return False
    parsed = _tool_options(said)
    return tail is not None or parsed is None or any(option in ("-n", "--no-verify") for option, _ in parsed[1])


def _redirects_the_hook(said, tail, config):
    return said[:1] == ["git"] and any(key == "core.hookspath" for key, _ in config)


# A config read must be proved from parsed options, never from a token that merely
# looks like a read flag: it may be --comment's value, or a positional after --.
def _rewrites_the_hooks(said, tail, config):
    if not _git_subcommand(said, tail, ("config",)):
        return False
    parsed = _options(said[2:], *GIT_CONFIG_OPTIONS)
    if parsed is None or tail is not None:
        return True
    operands, given, _, waiting = parsed
    options = {option for option, _ in given}
    writes = options.intersection(GIT_CONFIG_WRITE_OPTIONS) or set(operands).intersection(GIT_CONFIG_WRITES)
    reads = options.intersection(GIT_CONFIG_READS) or operands[:1] in (["get"], ["list"])
    if reads and not writes and not waiting:
        return False
    if options.intersection(("--file", "-f", "--edit", "-e", "--rename-section", "--remove-section")):
        return True
    if set(operands).intersection(("rename-section", "remove-section", "edit")):
        return True
    return any(key.lower() == "core.hookspath" or key.lower().split(".", 1)[0] in ("include", "includeif")
               for key in operands)


def _config_read(parsed):
    if parsed is None:
        return False
    operands, given, _, waiting = parsed
    options = {option for option, _ in given}
    return (not waiting and not options.intersection(GIT_CONFIG_WRITE_OPTIONS)
            and not set(operands).intersection(GIT_CONFIG_WRITES)
            and bool(options.intersection(GIT_CONFIG_READS) or operands[:1] in (["get"], ["list"])))


def _git_runs_code(said, tail, config):
    if said[:1] != ["git"] or len(said) < 2:
        return False
    kind = GIT_EFFECTS.get(said[1])
    if kind is None:
        return False
    if tail is None and _harmless(said[2:]):
        return False
    parsed = _tool_options(said)
    if parsed is None:
        return True
    operands, given, _, _ = parsed
    options = {option for option, _ in given}
    if any(OPTION_EFFECTS["git", said[1]][option] == "code"
           and not (said[1] == "archive" and option == "--format" and value in ("tar", "zip"))
           for option, value in given):
        return True
    if kind in ("shipped-read", "index", "file"):
        return False
    if kind == "code":
        return True  # hooks, editors, signers, external diff or text conversion
    if kind == "archive":
        return (tail is not None or bool(options & {"--exec", "--remote"})
                or any(option == "--format" and value not in ("tar", "zip") for option, value in given)
                or any(option in ("-o", "--output") and value and "." in posixpath.basename(value)
                       and not value.endswith((".tar", ".zip")) for option, value in given))
    if kind == "stash":
        # Restoring/storing can run filters or reference-transaction hooks; list/show
        # take log/diff options that can request configured text conversion. No stash
        # action is one of the shipped implicit-execution exceptions.
        return True
    if kind == "config":
        if tail is not None:
            return True
        if _config_read(parsed):
            return False
        if options & {"-e", "--edit"} or operands[:1] == ["edit"]:
            return True
        key = operands[1:2] if operands[:1] in (["set"], ["unset"]) else operands[:1]
        return not key or not INERT_GIT_CONFIG.fullmatch(key[0].lower())
    return True


def _git_writes_tree(said, tail, config):
    if not _git_subcommand(said, tail, ("stash", "add")):
        return False
    if tail is not None or len(said) < 2:
        return True
    parsed = _tool_options(said)
    if parsed is None:
        return True
    if said[1] == "add":
        return True  # index content controls what the next commit writes
    return not parsed[0] or parsed[0][0] not in ("list", "show")


def _git_writes_a_file(said, tail, config):
    # Commands that take a diff's options take `--output`; format-patch and archive write to `-o` as well.
    if not _git_subcommand(said, tail, ("diff", "diff-files", "diff-index", "diff-tree", "log", "show", "whatchanged",
                                        "format-patch", "range-diff", "stash", "archive", "config")):
        return False
    if tail is not None:
        return True
    parsed = _tool_options(said)
    if parsed is None:
        return True
    if said[1] == "config":
        return not _config_read(parsed)
    options = {option for option, _ in parsed[1]}
    return bool(options & {"-o", "--output", "--output-directory"}) or (
        said[1] == "format-patch" and "--stdout" not in options)


def _runs_any_argument(*programs):
    """npx and uvx run the package they are handed, any arguments but a version's or help's."""
    def performs(said, tail, config):
        return bool(said) and said[0] in programs and (tail is not None or not _harmless(said[1:]) and len(said) > 1)
    return performs


def _runs_a_script(program):
    """Judge only parsed subcommand operands; an option value is never a command."""
    def possible(node):
        return any(possible(child) for child in node.values()) if isinstance(node, dict) else node

    def performs(said, tail, config):
        if said[:1] != [program]:
            return False
        parsed = _tool_options(said)
        if parsed is None:
            return True
        operands, _, _, waiting = parsed
        node = _script_command(program, operands)
        if not isinstance(node, dict):
            return node is not False
        if tail is None:
            return False
        return possible(node)
    return performs


def _loads_a_plugin(said, tail, config):
    if not said or said[0] not in ("pytest", "py.test"):
        return False
    # Tests and conftest.py execute code even without an explicit plugin. Config
    # (-c) and override (-o) options can additionally select plugins via addopts.
    return tail is not None or not _harmless(said[1:])


def _finds_and_runs(said, tail, config):
    if said[:1] != ["find"]:
        return False
    parsed = _tool_options(said)
    return tail is not None or parsed is None or any(
        option in ("-exec", "-execdir", "-ok", "-okdir") for option, _ in parsed[1])


def _makes(said, tail, config):
    # make runs what a Makefile an implementer writes says, whichever target it names: bare, the first.
    return bool(said) and said[0] in ("make", "gmake") and (tail is not None or not _harmless(said[1:]))


# ao's coordination files, which the architect edits; any other path is a product file to it (GRANTS-PINNED).
COORDINATION = ("agent-mail", ".ao/backlog.md", ".ao/board.md", ".ao/inbox")


def _outside(path):
    """Whether a path a command names is outside ao's coordination files, as far as its words tell."""
    path = path.replace("\\", "/")
    if path[:1] in ("/", "~") or re.match(r"[A-Za-z]:", path):
        return True
    path = posixpath.normpath(path)
    return not any(path == place or path.startswith(place + "/") for place in COORDINATION)


def _touches_a_product_file(*programs):
    """rm, cp, mv or tee naming a path outside ao's coordination files, as an operand or as cp's and mv's target."""
    def performs(said, tail, config):
        if not said or said[0] not in programs:
            return False
        if tail is not None:
            return True
        parsed = _options(said[1:], *FILE_OPTIONS[said[0]])
        if parsed is None:
            return True
        paths, given, _, waiting = parsed
        paths += [value for option, value in given if option in ("-t", "--target-directory") and value is not None]
        if any(option in ("-b", "--backup", "-S", "--suffix") for option, _ in given):
            # Even a plain suffix can leave the exact-file allowlist: .ao/board.md~
            # is not .ao/board.md. Do not guess which numbered backup/destination exists.
            return True
        if waiting:
            return True
        return any(map(_outside, paths))
    return performs


# Each forbidden command above, as the operation it performs: (why, how it is shown, whether a command's reading
# performs it). An exact rule was compared with the samples as strings, so `Bash(git commit --no-verify -m y)` passed
# beside the sample's `-m x`; the samples are still asked of a rule with a wildcard, as the harness matches it, and
# tests/test_allowlist_depth.py holds each sample to its operation (ALLOWLIST-DEPTH). `ao lock` is read as the
# wrapper it is, and an interpreter as the closed world INTERPRETERS is.
OPERATIONS = (
    ("runs code the project may hold", "program file",
     lambda said, tail, config: bool(said) and _program_file(said[0])),
    ("skips the commit hook", "git commit --no-verify", _skips_the_hook),
    ("redirects the commit hook", "git -c core.hooksPath=", _redirects_the_hook),
    ("rewrites the hook configuration", "git config core.hooksPath", _rewrites_the_hooks),
    ("runs arbitrary code", "git hooks, filters or selected programs", _git_runs_code),
    ("opens a push window", "ao push allow", _ao_act("push", (("allow",),))),
    ("waives a gate", "ao waive", _ao_act("waive")),
    ("records a review ao did not run", "ao collect-review", _ao_act("collect-review")),
    ("records a person's review", "ao person-review", _ao_act("person-review")),
    ("opts into same-family review", "ao config set review.same_family",
     _ao_act("config", (("set",), ("review.same_family",)))),
    ("opts into same-family review", "ao init --review-tier", _ao_act("init", (), ("--review-tier",))),
    ("switches review off", "ao features off review", _ao_act("features", (("off",), ("review",)))),
    ("switches the inventory review off", "ao features off inventory_review",
     _ao_act("features", (("off",), ("inventory_review",)))),
    ("names the family that wrote a waived range", "ao catchup --author-family",
     _ao_act("catchup", (), ("--author-family",))),
    ("states which waived slices only moved code", "ao catchup --move-only", _ao_act("catchup", (), ("--move-only",))),
    ("replaces a waived range's commit claims", "ao catchup --boundary", _ao_act("catchup", (), ("--boundary",))),
    ("records harness configuration as seen", "ao harness seen --digest",
     _ao_act("harness", (("seen",),), ("--digest",))),
    ("records harness configuration as seen", "ao harness seen --ask", _ao_act("harness", (("seen",),), ("--ask",))),
    ("removes the hooks", "ao hooks uninstall", _ao_act("hooks", (("uninstall",),))),
    ("pages a person", "ao doctor --check --notify", _ao_act("doctor", (), ("--check", "--notify"))),
    ("runs arbitrary code", "npx", _runs_any_argument("npx", "uvx")),
    ("runs arbitrary code", "npm run", _runs_a_script("npm")),
    ("runs arbitrary code", "uv run", _runs_a_script("uv")),
    ("runs arbitrary code", "pytest -p", _loads_a_plugin),
    ("runs arbitrary code", "find -exec", _finds_and_runs),
    ("runs arbitrary code", "make", _makes),
)
IMPLEMENTER_OPERATIONS = (
    ("abandons a slice, which the architect decides", "ao slice abandon", _ao_act("slice", (("abandon",),))),
    ("re-specifies a slice, restarting its round budget", "ao decide --scope", _ao_act("decide", (), ("--scope",))),
    ("changes the settings that govern its own review", "ao config set review_timeout",
     _ao_act("config", (("set",), ("review_timeout",)))),
)
ARCHITECT_OPERATIONS = (
    ("removes a product file", "rm", _touches_a_product_file("rm")),
    ("writes the working tree or index", "git tree or index write", _git_writes_tree),
    ("writes a product file", "git diff --output", _git_writes_a_file),
    ("writes a product file", "cp, mv or tee", _touches_a_product_file("cp", "mv", "tee")),
)


def _settings_operations():
    """Exact values and unsets also change the loop's settings (SETTINGS-2, TRIAGE-1-LOW, ALLOWLIST-DEPTH)."""
    return tuple(("changes a setting that governs its own work", f"ao config {action}",
                  _ao_act("config", ((action,),))) for action in ("set", "unset"))


def through_rewriters(forbidden):
    """Each forbidden (reason, command) as a command rewriter would be handed it, and each of the rewriter's
    wrappers that runs whatever follows it, as forbidden as the command itself (GRANTS-RTK).

    A rewriter runs a command it has no filter for as it is, and passes the words after one it
    filters to the program it stands for (measured on rtk: `rtk sh -c x` ran sh, and
    `rtk git -c core.fsmonitor=<command> status` ran the command, as `git -c` does), so a rule
    admitting `rtk git commit -n -m x` skips the commit hook as `Bash(git commit:*)` does. A grant
    ao composes gains only the rewritten form of a rule it already holds, which admits none of
    these; asking them is what names a wider rule a person writes, `Bash(rtk:*)` or
    `Bash(rtk git:*)`, the way the command's own rule is named.
    """
    from . import lib as A
    found, asked = [], set()
    for rewriter in A.command_rewriters():
        name = rewriter["command"].strip()
        declared = rewriter.get("runs_any_command")
        wrappers = [wrapper.strip() for wrapper in declared if isinstance(wrapper, str) and wrapper.strip()] \
            if isinstance(declared, list) else []
        for reason, command in [(reason, f"{name} {command}") for reason, command in forbidden] \
                + [("runs any command", f"{wrapper} sh -c x") for wrapper in wrappers]:
            # `rtk env sh -c x` is both rtk's form of `env sh -c x` and its env wrapper; it is asked once.
            if command not in asked:
                asked.add(command)
                found.append((reason, command))
    return tuple(found)


# A reviewer reads. Tools a reviewer may use, and the Claude Code flags that keep
# every configured MCP server from starting (#24).
REVIEWER_TOOLS = ("Read", "Grep", "Glob")
# The same, as ACP names the kind of a tool call an agent asks leave for: Read reads, Grep and Glob search.
# A reviewer's ACP session is let run these, once each, and nothing else; a call of a kind that changes
# something, seen to have run, ran without that leave (ACP-REVIEWER).
REVIEWER_TOOL_KINDS = ("read", "search")
WRITING_TOOL_KINDS = ("edit", "delete", "move", "execute")


def reviewer_problems(argv, prompt=None):
    """What a reviewer's argv can reach beyond reading, as short phrases (#24).

    On 2026-09-07 a reviewer run as `claude -p … --allowedTools ""` started four MCP
    servers and pulled several hundred tool descriptions into the review: an allow
    list gates permissions, not which servers start. A reviewer whose adapter declares
    MCP isolation needs its required flags and none of its forbidden ones; any
    reviewer is refused every tool and any tool that writes.
    """
    args = [str(arg) for arg in argv or []]
    if not args:
        return []
    found = []
    if any(arg in GRANT_ALL for arg in args):
        found.append("it is granted every tool")
    extra = sorted({rule.split("(", 1)[0] for rule in rules(args, prompt=prompt)} - set(REVIEWER_TOOLS))
    if extra:
        found.append("its allowed tools go beyond reading: " + ", ".join(extra))
    # Which harness starts MCP servers unless told not to, and with which flags, is its
    # adapter's to declare (`options.mcp_isolation`, #76); so is the mode it runs in (GRANTS-PINNED).
    from . import lib as A
    found.extend(A.pin_conflicts(args, "reviewer"))
    pinned = {flag for flag, _ in A.role_pin(args, "reviewer")}
    for argument in A.bypass_arguments(args):
        flag = argument.split(" ", 1)[0].split("=", 1)[0]
        if argument not in GRANT_ALL and flag not in pinned:
            found.append(f"it carries {argument}, which approves for it, sandboxes nothing or checks no permission")
    program = os.path.basename(args[0]).lower().split(".")[0]
    for adapter in A.package_adapters().values():
        if program not in A.adapter_binaries(adapter):
            continue
        isolation = (adapter.get("options") or {}).get("mcp_isolation") or {}
        for flag in isolation.get("required") or []:
            if flag not in args:
                found.append(f"it starts every configured MCP server (no {flag})")
        for flag in isolation.get("forbidden") or []:
            if any(arg == flag or arg.startswith(flag + "=") for arg in args):
                found.append(f"it loads MCP servers from {flag}")
    return found


def describe(role, name, found):
    """One doctor line for an actor's findings, or None when there are none."""
    if not found:
        return None
    if found[0][1] == "*":
        what = "is granted every tool" if found[0][2] == "*" else \
            f"trusts the shell tool ({found[0][2]}), which runs every command"
        return (f"{role} ({name}) {what}, so no allowlist keeps out a hook bypass "
                f"or a push; the commit hook and ao commit are what hold")
    parts = [f"{rule} admits `{command}` ({reason})" for reason, command, rule in found]
    shown = "; ".join(parts[:4])
    return f"{role} ({name}): {shown}" + (f"; and {len(parts) - 4} more" if len(parts) > 4 else "")
