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
    ("names the family that wrote a waived range", "ao catchup --author-family f --by b"),
    ("states which waived slices only moved code", "ao catchup --move-only s --by b"),
    ("removes the hooks", "ao hooks uninstall"),
    ("runs any command", "ao lock -- sh -c x"),
    ("runs arbitrary code", "python3 -c x"),
    ("runs arbitrary code", "python -c x"),
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
)

# The architect decides. An implementer granted this could restart its own round
# budget with a re-specification it wrote itself (#65).
IMPLEMENTER_FORBIDDEN = (
    ("re-specifies a slice, restarting its round budget", "ao decide x --scope s"),
    ("changes the settings that govern its own review", "ao config set review_timeout 1"),
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

# Flags with which a harness grants every tool at once.
# Programs that run the command after them, each with the options of its own that take the next word as their
# value (ALLOWLIST-NORMALIZE). A rule admitting one with any arguments admits what it would admit of the command
# after it: `Bash(timeout 60 pytest:*)` admits `timeout 60 pytest -p x`, which loads any plugin, and
# `Bash(nice:*)` admits every command there is.
WRAPPERS = {
    "env": ("-u", "--unset", "-C", "--chdir"),
    "xargs": ("-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a", "--arg-file", "--delimiter", "--max-args",
              "--max-procs", "--max-lines", "--replace", "--max-chars", "--eof"),
    "timeout": ("-s", "--signal", "-k", "--kill-after"),
    "nice": ("-n", "--adjustment"),
    "nohup": (),
    "time": ("-f", "--format", "-o", "--output"),
    "command": (),
    "exec": ("-a",),
    "stdbuf": ("-i", "-o", "-e", "--input", "--output", "--error"),
    "caffeinate": ("-t", "-w"),
    "sudo": ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "--user", "--group", "--chdir", "--host",
             "--prompt", "--role", "--type", "--other-user", "--close-from"),
}
# Words a wrapper reads before the command that are not options: timeout's duration.
WRAPPER_OPERANDS = {"timeout": 1}
# git's global options that take the next word as their value. None of git's global options changes what a
# subcommand does to the commit hook, so a rule admitting `git -C:*` admits `git -C . commit --no-verify`.
GIT_VALUE_OPTIONS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env")

GRANT_ALL = ("--dangerously-skip-permissions", "--trust-all-tools", "--yolo", "--full-auto",
             "--dangerously-bypass-approvals-and-sandbox")

# The tool a `--trust-tools` list trusts every command through: Kiro trusts tools by name, and its shell
# tool runs whatever command it is given (kiro.dev/docs/reference/built-in-tools, read 2026-09-30).
SHELL_TOOLS = ("shell", "execute_bash", "execute_cmd", "*")


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
    body = rule[len("Bash("):-1]
    if body.endswith(":*"):
        body = body[:-2] + " *"
    if "*" not in body:
        return command == body
    if body.endswith(" *") and body.count("*") == 1 and command == body[:-2]:
        return True
    return re.fullmatch(".*".join(re.escape(part) for part in body.split("*")), command, re.S) is not None


def rules(argv):
    """The permission rules an argv grants through --allowedTools, in order."""
    out = []
    for i, arg in enumerate(argv):
        arg = str(arg)
        if arg in ("--allowedTools", "--allowed-tools") and i + 1 < len(argv):
            out.extend(part.strip() for part in str(argv[i + 1]).split(",") if part.strip())
        elif arg.startswith(("--allowedTools=", "--allowed-tools=")):
            out.extend(part.strip() for part in arg.split("=", 1)[1].split(",") if part.strip())
    return out


def trusted_tools(argv):
    """The tools an argv trusts by name through --trust-tools, in order (GRANTS-AUDIT-2).

    It scopes a grant as an allowlist does, and names no command: the audit read no rule from it,
    so a grant that trusted the shell tool was reported as admitting nothing.
    """
    out = []
    for i, arg in enumerate(argv):
        arg = str(arg)
        if arg == "--trust-tools" and i + 1 < len(argv):
            out.extend(part.strip() for part in str(argv[i + 1]).split(",") if part.strip())
        elif arg.startswith("--trust-tools="):
            out.extend(part.strip() for part in arg.split("=", 1)[1].split(",") if part.strip())
    return out


def grants_everything(argv, options=None):
    """Does this argv, as ao would run it, grant every tool?

    The watchdog starts an argv that carries no scope of its own with what its adapter
    declares for a turn nobody attends (`options.unattended`) or, where it declares none,
    its trust_all: such a harness has no allowlist, and grants every tool either way unless
    what is appended scopes it.
    """
    from . import lib as A
    args = [str(arg) for arg in argv]
    if any(arg in GRANT_ALL for arg in args):
        return True
    if A.carries_scope(args):
        return False
    options = options or {}
    if isinstance(options.get("unattended"), list):
        return not A.carries_scope(options["unattended"])
    return "trust_all" in options


def problems(argv, options=None, role=None):
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
    shell = next((tool for tool in trusted_tools(list(argv) + appended) if tool.lower() in SHELL_TOOLS), None)
    if shell:
        return [("trusts the shell tool, which runs every command", "*", f"--trust-tools {shell}")]
    granted = rules(A.admit_rewrites(list(argv))[0] + appended)
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
    unwrapped = [(rule, plain) for rule in granted if rule not in named for plain in [normalized_rule(rule)] if plain]
    for reason, command in forbidden:
        for rule, plain in unwrapped:
            if rule not in named and admits(plain, command):
                found.append((f"{reason}, as the command it runs", command, rule))
                named.add(rule)
    if role == "implementer":
        # Every setting ao reads governs the loop an implementer works in - its round budget, its review's
        # timeout, how long a waiver lasts - and only `review_timeout` was asked, so a grant of
        # `ao config set round_budget:*` went unnamed. Each rule is named once, for the first it admits
        # (SETTINGS-2).
        for rule in granted:
            command = next((c for c in settings_commands() if admits(rule, c)), None) if rule not in named else None
            if command:
                found.append(("changes a setting that governs its own work", command, rule))
                named.add(rule)
    return found


def settings_commands():
    """`ao config set` for each setting ao reads, as an implementer's grant is asked it (SETTINGS-2)."""
    from . import settings as S
    return tuple(f"ao config set {key} 1" for key in sorted(S.SETTINGS))


def _after(words, value_options, operands=0, assignments=False):
    """The words after a program's own options, operands and NAME=VALUE assignments."""
    i = 0
    while i < len(words):
        word = words[i]
        if word == "--":
            return words[i + 1:]
        if word.startswith("-") and len(word) > 1:
            i += 2 if word in value_options else 1
        elif assignments and "=" in word and not word.startswith("="):
            i += 1
        elif operands:
            operands, i = operands - 1, i + 1
        else:
            break
    return words[i:]


def unwrap(words):
    """The words a command runs as, with each program that runs the command after it, a rewriter's wrappers
    and git's global options taken off (ALLOWLIST-NORMALIZE).

    `timeout 60 pytest -p x` runs as `pytest -p x`, `rtk proxy git commit -n` as `git commit -n`, `rtk git -C .
    commit -n` as `git commit -n`. A wrapper inside a wrapper is taken off too, a bounded number of times.
    """
    from . import lib as A
    words = list(words)
    rewriters = [(str(r["command"]).strip(), [str(w).split() for w in r.get("runs_any_command") or []
                                               if isinstance(w, str) and w.strip()])
                 for r in A.command_rewriters()]
    for _ in range(8):
        if not words:
            break
        if words[0] in WRAPPERS:
            words = _after(words[1:], WRAPPERS[words[0]], WRAPPER_OPERANDS.get(words[0], 0),
                           assignments=words[0] == "env")
            continue
        runner = next((w for _, runners in rewriters for w in runners if words[:len(w)] == w), None)
        if runner:
            words = words[len(runner):]
            continue
        if any(words[0] == name and words[1:2] == ["git"] for name, _ in rewriters):
            words = words[1:]
            continue
        break
    if words[:1] == ["git"]:
        words = ["git"] + _after(words[1:], GIT_VALUE_OPTIONS)
    return words


def normalized_rule(rule):
    """The rule a rule is, read as the command it runs: `Bash(timeout 60 pytest:*)` is `Bash(pytest:*)`, and
    `Bash(nice:*)`, whose command is any at all, is `Bash(*)`. None for a rule that names no command with any
    arguments after it, or whose command is already plain (ALLOWLIST-NORMALIZE)."""
    rule = str(rule).strip()
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return None
    body = rule[len("Bash("):-1]
    end = next((end for end in (":*", " *") if body.endswith(end)), None)
    if end is None or "*" in body[:-len(end)]:
        return None
    words = body[:-len(end)].split()
    plain = unwrap(words)
    if plain == words:
        return None
    return f"Bash({' '.join(plain)}:*)" if plain else "Bash(*)"


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


def reviewer_problems(argv):
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
    extra = sorted({rule.split("(", 1)[0] for rule in rules(args)} - set(REVIEWER_TOOLS))
    if extra:
        found.append("its allowed tools go beyond reading: " + ", ".join(extra))
    # Which harness starts MCP servers unless told not to, and with which flags, is its
    # adapter's to declare (`options.mcp_isolation`, #76); so is the mode it runs in (GRANTS-PINNED).
    from . import lib as A
    found.extend(A.pin_conflicts(args, "reviewer"))
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
