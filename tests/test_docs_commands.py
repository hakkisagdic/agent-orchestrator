"""Every `ao` invocation a document shows is one the parser accepts (DOCS-COMMANDS).

docs/recovery.md sent a reader coming back to a project to `ao since --slice
claim-admission` and `ao brief`. since has no --slice and brief is no command, so following
the document ended in an argparse error, and nothing stopped the next drift. This reads the
invocations the documents show and walks each through the parser `ao.cli.build_parser()`
returns, in process: the command exists, every literal word has a positional to fill and
one with choices is among them, every option is the command's own and spelled out in full
(an abbreviation argparse accepts today stops working once a second option shares its
prefix), and a literal value of an option with choices is among them. Types, required
options and missing arguments are not checked: a sentence may name `ao commit` without its
message.

What counts as an invocation:
- in a fenced block marked bash, sh, shell or zsh, or not marked: each command whose first
  word is `ao`, after joining `\\` continuations, cutting an unquoted `#` comment, splitting
  at `&&`, `||`, `;`, `|` and `&`, and dropping redirections; in a console block, only a
  command after a `$ ` prompt;
- in inline code: a span that starts with `ao `, read as a synopsis: `[...]` is an optional
  part, `a|b` offers alternatives that are each checked, and a `|` with a space beside it
  is a pipe.
`<placeholder>`, `…` and `...` stand for what the reader supplies and are not checked; a
placeholder where the command takes no further argument stands for the rest of the line.

A passage that describes a design not yet built says so on the line directly above it with
`<!-- not built: <why> -->`. The marker covers the fenced block, or the paragraph, list or
table, that starts on the next line; it excuses only the invocations there that the parser
refuses, and it fails when it excuses none, so it cannot outlive the design it describes.
"""
import argparse
import collections
import re
import shlex
from pathlib import Path

from ao import cli

ROOT = Path(__file__).resolve().parent.parent

# Records of what held on an earlier date keep that date's commands: they are read as
# history, not followed, and rewriting them would falsify the record. Every other document
# here is read to be followed - a dated anecdote in one still names commands that exist, and
# a proposal such as docs/donor.md marks what is not built - so all of them are checked.
HISTORICAL = {
    "docs/backlog.md": "open rows propose commands that are not built yet; closed rows and the dated "
                       "notes record what held when they were written",
    "docs/lessons.md": "each lesson quotes the commands of the day it was learned",
    "docs/adr": "a decision record keeps the command line of the day it was decided",
    "docs/audit": "an audit report quotes the tree it audited",
}

FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)")
SHELL_BLOCKS = {"", "bash", "sh", "shell", "zsh", "console"}
MARKER = re.compile(r"^\s*<!--\s*not built:(.*?)-->\s*$")
PLACEHOLDER = re.compile(r"<[^\s<>][^<>]*>")
HELD = re.compile("\x01(\\d+)\x01")
UNIT = re.compile(r"^\s*([-*+]\s|\d+[.)]\s|\||#{1,6}\s)")      # a list item, a table row or a heading
REDIRECT = re.compile(r"\d*(>>?|<|&>)(&\d+)?")
REDIRECT_TO = re.compile(r"\d*(>>?|<|&>)[^&\s]\S*")
NUMBER = re.compile(r"-\d+(\.\d+)?")

Invocation = collections.namedtuple("Invocation", "line text words marker")
Marker = collections.namedtuple("Marker", "line reason covers")


def _documents(root):
    paths = sorted((root / "docs").glob("*.md")) + [root / "README.md", root / "README.tr.md",
                                                  root / "src" / "ao" / "skill" / "SKILL.md"]
    named = [(path.relative_to(root).as_posix(), path) for path in paths]
    return [(name, path) for name, path in named
            if not any(name == kept or name.startswith(kept + "/") for kept in HISTORICAL)]


def _segments(text, synopsis):
    """A command line split at its unquoted control operators, its unquoted comment cut off.

    In a shell block every unquoted `|` is a pipe. In a synopsis `a|b` offers a choice of
    words, so there only a `|` with a space beside it ends the command.
    """
    parts, current, quote, i = [], [], None, 0
    while i < len(text):
        ch, before, after = text[i], text[i - 1:i], text[i + 1:i + 2]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and after:
                current.append(ch)
                i += 1
                ch = text[i]
        elif ch in "'\"":
            quote = ch
        elif ch == "\\" and after:
            current.append(ch)
            i += 1
            ch = text[i]
        elif ch == "#" and (not current or current[-1].isspace()):
            break
        elif ch in ";&|" and not (ch == "&" and (before in ("<", ">") or after == ">")) \
                and not (synopsis and ch == "|" and before.strip() and after.strip() and after != "|"):
            parts.append("".join(current))
            current = []
            i += 2 if text[i:i + 2] in ("&&", "||") else 1
            continue
        current.append(ch)
        i += 1
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _words(command, synopsis):
    """The words of one command, each the list of alternatives it offers."""
    words, target = [], False
    for word in shlex.split(command):
        if not synopsis and REDIRECT.fullmatch(word):
            target = not word[-1].isdigit()                     # `>` names its file next; `2>&1` names none
            continue
        if target or (not synopsis and REDIRECT_TO.fullmatch(word)):
            target = False
            continue
        word = HELD.sub("…", word.strip("[]"))
        alternatives = [alt for alt in word.split("|") if alt] if synopsis else [word]
        if alternatives:
            words.append(alternatives)
    return words


def _commands(text, synopsis):
    """(as written, words) for each command in `text` whose first word is ao.

    A placeholder is held aside while the line is split, so `<dir|ref>` offers no
    alternatives and `<slice>` is no redirection, and it is read as `…`.
    """
    held = []

    def hold(match):
        held.append(match.group(0))
        return "\x01%d\x01" % (len(held) - 1)

    found = []
    for segment in _segments(PLACEHOLDER.sub(hold, text), synopsis):
        if segment.split()[0] != "ao":
            continue
        written = " ".join(HELD.sub(lambda m: held[int(m.group(1))], segment).split())
        try:
            words = _words(segment, synopsis)
        except ValueError as error:                              # an unbalanced quote
            words = error
        found.append((written, words))
    return found


def _spans(text):
    """(offset, content) of each inline code span: a run of backticks closed by a run as long."""
    found, i = [], 0
    while True:
        opening = re.compile(r"`+").search(text, i)
        if not opening:
            return found
        closing = re.compile(r"(?<!`)" + opening.group(0) + r"(?!`)").search(text, opening.end())
        if not closing:
            i = opening.end()
            continue
        found.append((opening.start(), text[opening.end():closing.start()]))
        i = closing.end()


def _read(text):
    """([Invocation], [Marker]): the `ao` invocations a document's text shows, and its markers."""
    lines = text.split("\n")
    invocations, markers, waiting, i = [], [], None, 0
    while i < len(lines):
        line = lines[i]
        marker = MARKER.match(line)
        if marker:
            waiting = Marker(i + 1, marker.group(1).strip(), [])
            markers.append(waiting)
            i += 1
            continue
        if not line.strip():
            waiting, i = None, i + 1
            continue
        fence = FENCE.match(line)
        if fence:
            closing = re.compile(r"^\s*%s{%d,}\s*$" % (re.escape(fence.group(1)[0]), len(fence.group(1))))
            end = next((n for n in range(i + 1, len(lines)) if closing.match(lines[n])), len(lines))
            language, body, n = fence.group(2).lower(), lines[i + 1:end], 0
            while language in SHELL_BLOCKS and n < len(body):
                start, command = i + 2 + n, body[n]
                while command.endswith("\\") and n + 1 < len(body):
                    n += 1
                    command = command[:-1] + " " + body[n]
                n += 1
                prompt = re.match(r"^\s*\$\s", command)
                if language == "console" and not prompt:
                    continue
                invocations.extend(Invocation(start, written, words, waiting) for written, words
                                   in _commands(command[prompt.end():] if prompt else command, synopsis=False))
            if waiting:
                waiting.covers.append((i + 1, end + 1))
            waiting, i = None, end + 1
            continue
        end = i
        while end < len(lines) and lines[end].strip() and not FENCE.match(lines[end]) \
                and not MARKER.match(lines[end]):
            end += 1
        units = []
        for n in range(i, end):
            if not units or UNIT.match(lines[n]):
                units.append((n, []))
            units[-1][1].append(lines[n])
        for first, unit in units:
            joined = "\n".join(unit)
            for offset, content in _spans(joined):
                content = " ".join(content.replace("\\|", "|").split())      # `\|` is a pipe inside a table
                if content.startswith("ao "):
                    line_number = first + 1 + joined.count("\n", 0, offset)
                    invocations.extend(Invocation(line_number, written, words, waiting)
                                       for written, words in _commands(content, synopsis=True))
        if waiting:
            waiting.covers.append((i + 1, end))
        waiting, i = None, end
    return invocations, markers


def _placeholder(word):
    return "…" in word or "..." in word


def _option_like(alternatives):
    return any(alt.startswith("-") and len(alt) > 1 and not NUMBER.fullmatch(alt) and not _placeholder(alt)
               for alt in alternatives)


def _refusals(parser, words):
    """What `parser` refuses in the words that follow its command, as sentences; [] when nothing."""
    if isinstance(words, ValueError):
        return [f"cannot be split into words: {words}"]
    options = {flag: action for action in parser._actions for flag in action.option_strings}
    positionals = [action for action in parser._actions if not action.option_strings]
    problems, slot, only_positionals, i = [], 0, False, 0
    while i < len(words):
        alternatives = words[i]
        i += 1
        if alternatives == ["--"] and not only_positionals:
            only_positionals = True
            continue
        flags = [] if only_positionals else [alt for alt in alternatives if _option_like([alt])]
        values = [alt for alt in alternatives if alt not in flags]
        for flag in flags:
            name, attached = flag.split("=", 1)[0], "=" in flag
            action = options.get(name)
            if action is None and not name.startswith("--") and name[:2] in options:
                action, attached = options[name[:2]], True     # -n5: a value written against its option
            if action is None:
                problems.append(f"{parser.prog} has no option {name}")
                if not attached and not values and i < len(words) and not _option_like(words[i]):
                    i += 1                                     # what reads as its value: refused once, not twice
                continue
            taken = []
            while action.nargs != 0 and not attached and not values and i < len(words) \
                    and not _option_like(words[i]):
                taken.extend(words[i])
                i += 1
                if action.nargs in (None, "?") or len(taken) == action.nargs:
                    break
            problems.extend(f"{value!r} is not one of {parser.prog} {name}'s choices "
                            f"({', '.join(sorted(action.choices))})"
                            for value in taken if action.choices is not None and not _placeholder(value)
                            and value not in action.choices)
        literal = [value for value in values if not _placeholder(value)]
        if not values or (slot >= len(positionals) and not literal):
            continue                                           # `ao fanout record …`: the rest of the line
        if slot >= len(positionals):
            problems.append(f"{parser.prog} takes no argument {literal[0]!r}")
            break
        action = positionals[slot]
        if action.nargs == argparse.REMAINDER:
            break
        if isinstance(action.choices, dict):                   # the subcommands: the words after are theirs
            for value in literal:
                problems.extend(_refusals(action.choices[value], words[i:]) if value in action.choices
                                else [f"{parser.prog} has no command {value!r}"])
            break
        problems.extend(f"{value!r} is not one of {parser.prog}'s {action.dest} choices "
                        f"({', '.join(sorted(action.choices))})"
                        for value in literal if action.choices is not None and value not in action.choices)
        if action.nargs not in ("*", "+"):
            slot += 1
    return problems


def _scan(root, parser):
    """[(document, Invocation, refusals)] for every invocation, and [(document, Marker)]."""
    seen, markers = [], []
    for name, path in _documents(root):
        invocations, found = _read(path.read_text(encoding="utf-8"))
        seen.extend((name, inv, _refusals(parser, inv.words if isinstance(inv.words, ValueError) else inv.words[1:]))
                    for inv in invocations)
        markers.extend((name, marker) for marker in found)
    return seen, markers


def _refused(root, parser):
    """Each invocation the documents under `root` show that `parser` refuses and no marker excuses."""
    seen, _ = _scan(root, parser)
    return [f"{name}:{inv.line}: `{inv.text}`: {'; '.join(problems)}"
            for name, inv, problems in seen if problems and inv.marker is None]


def _marker_problems(root, parser):
    """Each `not built` marker that gives no reason, covers no block, or excuses nothing."""
    seen, markers = _scan(root, parser)
    out = []
    for name, marker in markers:
        if not marker.reason:
            out.append(f"{name}:{marker.line}: a not-built marker gives no reason")
        elif not marker.covers:
            out.append(f"{name}:{marker.line}: a not-built marker sits directly above no block")
        elif not any(problems for doc, inv, problems in seen if doc == name and inv.marker is marker):
            out.append(f"{name}:{marker.line}: a not-built marker excuses nothing the parser refuses")
    return out


def _command(words):
    """The command an invocation's words name, past `ao` and its own options."""
    i = 1
    while i < len(words) and _option_like(words[i]):
        i += 2 if words[i] in (["-C"], ["--root"]) else 1
    return words[i] if i < len(words) else []


def test_every_ao_invocation_in_the_documents_is_one_the_parser_accepts():
    problems = _refused(ROOT, cli.build_parser())

    assert problems == [], "\n".join(problems)


def test_a_not_built_marker_gives_a_reason_and_excuses_only_what_the_parser_refuses():
    problems = _marker_problems(ROOT, cli.build_parser())

    assert problems == [], "\n".join(problems)


def test_the_scan_reaches_every_command_the_parser_has():
    parser = cli.build_parser()
    commands = next(action.choices for action in parser._actions if isinstance(action.choices, dict))
    seen, _ = _scan(ROOT, parser)

    shown = {alt for _, inv, _ in seen if not isinstance(inv.words, ValueError) for alt in _command(inv.words)}

    assert sorted(set(commands) - shown) == []


SAMPLE = """# A document

```bash
ao since last          # a comment with --no-such-option
ao since --slice claim-admission
cd project && ao brief | head -n 3 > out.txt 2>&1
ao lock --wait 60 -- pytest --maxfail 1
ao -C ~/project status --window=12 \\
   --no-such-option
```

Run `ao review collect <R-id>\\|--any`, `ao hooks [status|install] [--allow-shared-hooks]` or
`ao fanout record …`, never `ao review resume R-<id>`, `ao alarms test --level purple|red`
or `ao
doctor --consist`.

```console
$ ao board ready
ao output that is not a command
```

```json
{"run": "ao brief"}
```

<!-- not built: lanes are a design -->
```bash
ao lanes
ao status
```

<!-- not built: -->
`ao board` is built.

<!-- not built: queues are a design -->

`ao queue` is not covered.
"""


def test_the_reading_sees_shell_syntax_synopses_placeholders_markers_and_history(tmp_path):
    parser = cli.build_parser()
    for name, text in (("docs/sample.md", SAMPLE), ("docs/lessons.md", "`ao brief`\n"),
                       ("README.md", "`ao status`\n"), ("README.tr.md", "`ao board`\n"),
                       ("src/ao/skill/SKILL.md", "`ao brief --x`\n")):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    invocations, markers = _read(SAMPLE)

    assert [(inv.line, inv.text) for inv in invocations] == [
        (4, "ao since last"), (5, "ao since --slice claim-admission"), (6, "ao brief"),
        (7, "ao lock --wait 60 -- pytest --maxfail 1"), (8, "ao -C ~/project status --window=12 --no-such-option"),
        (12, "ao review collect <R-id>|--any"), (12, "ao hooks [status|install] [--allow-shared-hooks]"),
        (13, "ao fanout record …"), (13, "ao review resume R-<id>"), (13, "ao alarms test --level purple|red"),
        (14, "ao doctor --consist"), (18, "ao board ready"), (28, "ao lanes"), (29, "ao status"), (33, "ao board"),
        (37, "ao queue"),
    ]
    assert [(marker.line, marker.reason, marker.covers) for marker in markers] == [
        (26, "lanes are a design", [(27, 30)]), (32, "", [(33, 33)]), (35, "queues are a design", []),
    ]
    assert _refused(tmp_path, parser) == [
        "docs/sample.md:5: `ao since --slice claim-admission`: ao since has no option --slice",
        "docs/sample.md:6: `ao brief`: ao has no command 'brief'",
        "docs/sample.md:8: `ao -C ~/project status --window=12 --no-such-option`: "
        "ao status has no option --no-such-option",
        "docs/sample.md:13: `ao review resume R-<id>`: 'resume' is not one of ao review's action choices "
        "(collect, submit)",
        "docs/sample.md:13: `ao alarms test --level purple|red`: 'purple' is not one of ao alarms --level's "
        "choices (orange, red, yellow)",
        "docs/sample.md:14: `ao doctor --consist`: ao doctor has no option --consist",
        "docs/sample.md:37: `ao queue`: ao has no command 'queue'",
        "src/ao/skill/SKILL.md:1: `ao brief --x`: ao has no command 'brief'",
    ]
    assert _marker_problems(tmp_path, parser) == [
        "docs/sample.md:32: a not-built marker gives no reason",
        "docs/sample.md:35: a not-built marker sits directly above no block",
    ]
