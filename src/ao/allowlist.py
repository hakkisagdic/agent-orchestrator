"""What a tool grant admits, measured against ao's own guarantees (#58).

An allowlist that grants `git commit:*` grants `git commit --no-verify`, the one
flag that skips the only commit-time enforcement; one that grants `ao:*` grants
`ao push allow`; one that grants an interpreter grants everything, because any
command can be written as a script. So each rule is asked what it would admit,
command by command, instead of being read for its intent.
"""
import os

# (why no actor ao runs may be granted it, the command)
FORBIDDEN = (
    ("skips the commit hook", "git commit --no-verify -m x"),
    ("skips the commit hook", "git commit -n -m x"),
    ("redirects the commit hook", "git -c core.hooksPath=/dev/null commit -m x"),
    ("rewrites the hook configuration", "git config core.hooksPath /dev/null"),
    ("opens a push window", "ao push allow 30"),
    ("waives a gate", "ao waive review --slice s --by b --why w"),
    ("records a review ao did not run", "ao collect-review n --response r --model m --by b"),
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

# Flags with which a harness grants every tool at once.
GRANT_ALL = ("--dangerously-skip-permissions", "--trust-all-tools", "--yolo", "--full-auto",
             "--dangerously-bypass-approvals-and-sandbox")
SCOPE_FLAGS = ("--allowedTools", "--allowed-tools", "--trust-tools")


def admits(rule, command):
    """Would this one Claude Code permission rule let `command` run?"""
    rule = str(rule).strip()
    if rule == "Bash":
        return True
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    body = rule[len("Bash("):-1]
    if body.endswith(":*"):
        prefix = body[:-2]
        return command == prefix or command.startswith(prefix + " ")
    return command == body


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


def grants_everything(argv, options=None):
    """Does this argv, as ao would run it, grant every tool?

    The watchdog appends an adapter's trust_all when the argv carries no scope of
    its own, so an unscoped argv from such an adapter grants everything too.
    """
    args = [str(arg) for arg in argv]
    if any(arg in GRANT_ALL for arg in args):
        return True
    scoped = any(arg in SCOPE_FLAGS or arg.startswith(tuple(flag + "=" for flag in SCOPE_FLAGS))
                 for arg in args)
    return not scoped and "trust_all" in (options or {})


def problems(argv, options=None, role=None):
    """(reason, command, rule) for everything this grant admits that it must not.

    A grant of every tool is one finding with command and rule '*'.
    """
    if grants_everything(argv, options):
        return [("grants every tool", "*", "*")]
    granted = rules(argv)
    found = []
    for reason, command in FORBIDDEN + (IMPLEMENTER_FORBIDDEN if role == "implementer" else ()):
        rule = next((r for r in granted if admits(r, command)), None)
        if rule:
            found.append((reason, command, rule))
    return found


# A reviewer reads. Tools a reviewer may use, and the Claude Code flags that keep
# every configured MCP server from starting (#24).
REVIEWER_TOOLS = ("Read", "Grep", "Glob")


def reviewer_problems(argv):
    """What a reviewer's argv can reach beyond reading, as short phrases (#24).

    On 2026-09-07 a reviewer run as `claude -p … --allowedTools ""` started four MCP
    servers and pulled several hundred tool descriptions into the review: an allow
    list gates permissions, not which servers start. A Claude Code reviewer needs
    `--strict-mcp-config` and no `--mcp-config`; any reviewer is refused every tool
    and any tool that writes.
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
    if os.path.basename(args[0]).lower().split(".")[0] == "claude":
        if "--strict-mcp-config" not in args:
            found.append("it starts every configured MCP server (no --strict-mcp-config)")
        if any(arg == "--mcp-config" or arg.startswith("--mcp-config=") for arg in args):
            found.append("it loads MCP servers from --mcp-config")
    return found


def describe(role, name, found):
    """One doctor line for an actor's findings, or None when there are none."""
    if not found:
        return None
    if found[0][1] == "*":
        return (f"{role} ({name}) is granted every tool, so no allowlist keeps out a hook bypass "
                f"or a push; the commit hook and ao commit are what hold")
    parts = [f"{rule} admits `{command}` ({reason})" for reason, command, rule in found]
    shown = "; ".join(parts[:4])
    return f"{role} ({name}): {shown}" + (f"; and {len(parts) - 4} more" if len(parts) > 4 else "")
