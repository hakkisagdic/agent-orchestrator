"""Shell completion: `ao completion bash|zsh|fish|powershell` prints a script that completes ao (SHELL-COMPLETION).

A part of src/ao/cli.py, run in its namespace by `_part` as the parts moved out of it are (#44); it is not
importable on its own. The script is generated from build_parser() each time the command runs, so it holds the
commands, the long options and the fixed choices of the ao that printed it, and completing a word runs no
program, ao included, and reads no project. Every word taken from the parser is written into the script as a
literal quoted for its shell, and every word the script completes is quoted again on the command line, so an
option or a choice holding a quote, a `$` or a backquote can neither break the script nor run anything.
"""

COMPLETION_SHELLS = ("bash", "zsh", "fish", "powershell")
COMPLETION_WIDTH = 100          # the column a generated line breaks before, where a word fits


def cmd_completion(cfg, args):
    """Print the completion script for one shell, generated from this ao's own command line (SHELL-COMPLETION).

    It goes to standard output with line feeds alone on every platform: bash, zsh and fish read a carriage
    return as part of a word, and PowerShell reads either. An unknown shell never reaches here: the parser
    refuses it, exit 2, naming the four.
    """
    script = completion_script(build_parser(), args.shell)
    try:
        sys.stdout.reconfigure(newline="\n")
    except (AttributeError, OSError, ValueError):      # a stream that is not a text file: written as it is
        pass
    sys.stdout.write(script)
    sys.stdout.flush()
    return 0


def completion_script(parser, shell):
    """The completion script for `shell`, one of COMPLETION_SHELLS, generated from an argparse parser (SHELL-COMPLETION)."""
    writers = {"bash": _completion_bash, "zsh": _completion_zsh, "fish": _completion_fish,
               "powershell": _completion_powershell}
    if shell not in writers:
        raise ValueError(f"no completion script for {shell!r}: one of {', '.join(COMPLETION_SHELLS)}")
    return "\n".join(writers[shell](_completion_model(parser))) + "\n"


# ---- what the parser offers --------------------------------------------------------------------------------------

def _completable(word):
    """Whether a word can be offered: not empty, and no control character, which no shell lists safely and fish
    takes for the end of a completion (SHELL-COMPLETION). A word ao cannot offer is left out of the script."""
    return bool(word) and not any(ord(ch) < 32 or 127 <= ord(ch) < 160 for ch in word)


def _completion_choices(action):
    """The choices argparse declares for an action, as the words a person types, once each; [] for none."""
    if action.choices is None or action.help == argparse.SUPPRESS:
        return []
    try:
        words = [str(choice) for choice in action.choices]
    except TypeError:                                  # a container that answers `in` and cannot be listed
        return []
    return [word for word in dict.fromkeys(words) if _completable(word)]


def _completion_words(parser):
    """What one parser offers, as the scripts read it (SHELL-COMPLETION).

    {options, valued, values, slots, repeat}: its long options, less those whose help is suppressed; every
    option string that takes the next word as its value, suppressed ones too, so that word is never counted as
    an argument; the choices of each option that declares them; the choices of each positional word in order,
    None where it has none; and whether the last slot takes every word after it, as nargs *, + and ... do.
    A subcommand's name is one slot, whose choices are the subcommands.
    """
    options, valued, values, slots, repeat = set(), set(), {}, [], False
    for action in parser._actions:
        choices = _completion_choices(action)
        if action.option_strings:
            for flag in filter(_completable, action.option_strings):
                if action.nargs != 0:
                    valued.add(flag)
                    if choices:
                        values[flag] = choices
                if flag.startswith("--") and action.help != argparse.SUPPRESS:
                    options.add(flag)
        elif not repeat:
            if action.nargs in ("*", "+", argparse.REMAINDER):
                slots.append(choices or None)
                repeat = True
            else:
                slots.extend([choices or None] * (action.nargs if isinstance(action.nargs, int) else 1))
    return {"options": sorted(options), "valued": sorted(valued), "values": values, "slots": slots, "repeat": repeat}


def _completion_model(parser):
    """{"": what the top level offers, name: what each subcommand offers} (SHELL-COMPLETION).

    The subcommands are the action whose choices map each name to its parser, as tests/test_docs_commands.py
    finds them. A name is never empty, so the empty key stands for the words before any command.
    """
    model = {"": _completion_words(parser)}
    for action in parser._actions:
        if isinstance(action.choices, dict):
            for name, command in action.choices.items():
                if _completable(name) and name not in model:
                    model[name] = _completion_words(command)
    return model


def _completion_arms(model):
    """(command, kind, key, words) for every list a script holds, kind one of valued, options, values and slot.

    key is the option for values, the slot's index for slot, and None otherwise; a slot that takes every word
    after it has the key (index, "and after").
    """
    arms = []
    for name, words in model.items():
        if words["valued"]:
            arms.append((name, "valued", None, words["valued"]))
        if words["options"]:
            arms.append((name, "options", None, words["options"]))
        arms.extend((name, "values", flag, choices) for flag, choices in sorted(words["values"].items()))
        last = len(words["slots"]) - 1
        arms.extend((name, "slot", (index, "and after") if words["repeat"] and index == last else index, choices)
                    for index, choices in enumerate(words["slots"]) if choices)
    return arms


def _completion_wrapped(tokens, indent, more=None):
    """The tokens on lines that start with `indent`, and `more` after the first where given, a line broken before
    a token that would pass COMPLETION_WIDTH."""
    lines, line = [], ""
    for token in tokens:
        lead = more if lines and more else indent
        if line and len(lead) + len(line) + 1 + len(token) > COMPLETION_WIDTH:
            lines.append(lead + line)
            line = ""
        line += (" " if line else "") + token
    if line:
        lines.append((more if lines and more else indent) + line)
    return lines


def _completion_header(shell, name, install, notes=()):
    """The comment a script opens with: what it is, that it goes stale with ao, and how it is installed."""
    return [f"# {name} completion for ao, printed by `ao completion {shell}` (SHELL-COMPLETION).",
            "# It holds the commands, the long options and the fixed choices of the ao that printed it:",
            "# print it again after updating ao. Completing a word runs no program. Install it with",
            *(f"#   {line}" for line in install), *(f"# {line}" for line in notes)]


# ---- bash and zsh --------------------------------------------------------------------------------------------------

def _completion_sh_quote(word):
    """A single-quoted literal that bash and zsh read back as exactly `word`: nothing in it is expanded."""
    return "'" + word.replace("'", "'\\''") + "'"


def _completion_sh_functions(model):
    """The functions the bash and the zsh script share, each setting the array `reply` (SHELL-COMPLETION).

    `_ao_valued COMMAND` sets the option strings that take the next word as their value, `_ao_options COMMAND`
    the long options, `_ao_values COMMAND OPTION` an option's choices and `_ao_arguments COMMAND SLOT` a
    positional word's; COMMAND is empty before the command. Each selects by `case` on quoted patterns, which
    both shells match as written, and sets reply from quoted literals, so a word is never read as syntax.
    """
    q, arms = _completion_sh_quote, _completion_arms(model)

    def assign(pattern, words, indent, guard=""):
        literals = [q(word) for word in words]
        line = f"{indent}{pattern}) {guard}reply=({' '.join(literals)});;"
        if len(line) <= COMPLETION_WIDTH:
            return [line]
        return [f"{indent}{pattern}) {guard}reply=("] + _completion_wrapped(literals, indent + "    ") + [f"{indent});;"]

    def function(name, what, kind, keyed):
        lines = [f"# {what}", f"{name}() {{", "    reply=()", "    case $1 in"]
        for command in dict.fromkeys(arm[0] for arm in arms if arm[1] == kind):
            mine = [arm for arm in arms if arm[:2] == (command, kind)]
            if not keyed:
                lines += assign(q(command), mine[0][3], "        ")
                continue
            lines += [f"        {q(command)})", "            case $2 in"]
            for _, _, key, words in mine:
                if kind == "values":
                    lines += assign(q(key), words, "                ")
                elif isinstance(key, tuple):
                    lines += assign("*", words, "                ", f'[ "$2" -ge {key[0]} ] && ')
                else:
                    lines += assign(str(key), words, "                ")
            lines += ["            esac;;"]
        return lines + ["    esac", "}", ""]

    return (function("_ao_valued", "The option strings of command $1 that take the next word as their value.",
                     "valued", False)
            + function("_ao_options", "The long options of command $1; before any command, those of ao itself.",
                       "options", False)
            + function("_ao_values", "The choices option $2 of command $1 declares.", "values", True)
            + function("_ao_arguments", "The choices of positional word $2 (from 0) of command $1; before any "
                                        "command, the commands.", "slot", True))


_COMPLETION_SH_TAKES_VALUE = r'''# Whether word $2 takes the next word as its value, among the words of command $1 or before any.
_ao_takes_value() {
    local option
    _ao_valued "$1"
    for option in "${reply[@]}"; do
        [[ $option == "$2" ]] && return 0
    done
    return 1
}
'''

_COMPLETION_BASH = r'''
# The words before the cursor name the command, then the positional word the cursor is on, or the
# option whose value it is. With = in COMP_WORDBREAKS, bash splits --option=value into three words.
_ao() {
    local cur="${COMP_WORDS[COMP_CWORD]}" cmd= rest= option= word= quoted= i=1 slot=0 n=0
    local -a reply
    COMPREPLY=()
    while [[ $i -lt $COMP_CWORD ]]; do
        word="${COMP_WORDS[i]}"
        if [[ -z $rest && -n $cmd && $word == "--" ]]; then
            rest=1
        elif [[ -z $rest && $word == -?* ]]; then
            n=1
            if [[ ${COMP_WORDS[i+1]-} == "=" ]]; then
                n=3
            elif _ao_takes_value "$cmd" "$word"; then
                n=2
            fi
            if [[ $n -gt 1 && $((i + n - 1)) -ge $COMP_CWORD ]]; then
                option="$word"
                break
            fi
            i=$((i + n - 1))
        elif [[ -z $cmd ]]; then
            cmd="$word"
        else
            slot=$((slot + 1))
        fi
        i=$((i + 1))
    done
    if [[ -n $option ]]; then
        [[ $cur == "=" ]] && cur=
        _ao_values "$cmd" "$option"
    elif [[ -z $rest && $cur == -* ]]; then
        _ao_options "$cmd"
    else
        _ao_arguments "$cmd" "$slot"
    fi
    # Each word that begins with what is typed, quoted so the command line reads it back as it is;
    # %q leaves a leading ~ bare in older bash, where the command line would expand it.
    for word in "${reply[@]}"; do
        if [[ $word == "$cur"* ]]; then
            printf -v quoted %q "$word"
            [[ $quoted == "~"* ]] && quoted="\\$quoted"
            COMPREPLY+=("$quoted")
        fi
    done
}

complete -o default -F _ao ao
'''

_COMPLETION_ZSH = r'''
# The words before the cursor name the command, then the positional word the cursor is on, or the
# option whose value it is.
_ao() {
    local cmd= rest= option= word=
    local -a reply
    local -i i=2 slot=0 n=0
    while (( i < CURRENT )); do
        word="${words[i]}"
        if [[ -z $rest && -n $cmd && $word == "--" ]]; then
            rest=1
        elif [[ -z $rest && $word == -?* ]]; then
            n=1
            _ao_takes_value "$cmd" "$word" && n=2
            if (( n > 1 && i + 1 >= CURRENT )); then
                option="$word"
                break
            fi
            i=$(( i + n - 1 ))
        elif [[ -z $cmd ]]; then
            cmd="$word"
        else
            slot=$(( slot + 1 ))
        fi
        i=$(( i + 1 ))
    done
    if [[ -z $option && -z $rest && $PREFIX == -?*=* ]]; then
        option="${PREFIX%%=*}"
        compset -P 1 '*='
    fi
    if [[ -n $option ]]; then
        _ao_values "$cmd" "$option"
    elif [[ -z $rest && $PREFIX == -* ]]; then
        _ao_options "$cmd"
    else
        _ao_arguments "$cmd" "$slot"
    fi
    # compadd keeps the words that match what is typed and quotes the one it inserts; with none, files.
    if (( ${#reply} )); then
        compadd -- "${reply[@]}"
    else
        _files
    fi
}

# Loaded from fpath, this file is the body of _ao, and runs it; sourced, it registers _ao for ao.
if [[ ${funcstack[1]} == _ao ]]; then
    _ao "$@"
else
    compdef _ao ao
fi
'''


def _completion_bash(model):
    return (_completion_header("bash", "bash", ["mkdir -p ~/.local/share/bash-completion/completions",
                                                "ao completion bash > ~/.local/share/bash-completion/completions/ao"],
                               ["which bash-completion loads the first time ao is completed; without that package,",
                                "source the file from ~/.bashrc."])
            + [""] + _completion_sh_functions(model) + _COMPLETION_SH_TAKES_VALUE.splitlines()
            + _COMPLETION_BASH.splitlines())


def _completion_zsh(model):
    return (["#compdef ao"]
            + _completion_header("zsh", "zsh", ["mkdir -p ~/.zfunc && ao completion zsh > ~/.zfunc/_ao"],
                                 ["and, in ~/.zshrc before compinit runs: fpath=(~/.zfunc $fpath)"])
            + [""] + _completion_sh_functions(model) + _COMPLETION_SH_TAKES_VALUE.splitlines()
            + _COMPLETION_ZSH.splitlines())


# ---- fish ----------------------------------------------------------------------------------------------------------

def _completion_fish_quote(word):
    """A single-quoted fish literal: fish reads back exactly `word`, escaping only a backslash and a quote there."""
    return "'" + word.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _completion_fish_functions(model):
    """fish functions that print, one a line, what _completion_sh_functions sets (SHELL-COMPLETION).

    fish has no case that matches literally - its switch reads * and ? in a quoted pattern - so each function
    is a chain of `contains`, which compares strings exactly, and prints quoted literals with printf.
    """
    q, arms = _completion_fish_quote, _completion_arms(model)

    def printed(words, indent):
        literals = [q(word) for word in words]
        lines = _completion_wrapped(["printf", "'%s\\n'"] + literals, indent, indent + "    ")
        return [line + " \\" for line in lines[:-1]] + lines[-1:]

    def function(name, parameters, kind):
        lines, first = [f"function {name} --argument-names {parameters}"], True
        for command, _, key, words in (arm for arm in arms if arm[1] == kind):
            test = f'contains -- "$cmd" {q(command)}'
            if kind == "values":
                test += f'; and contains -- "$option" {q(key)}'
            elif kind == "slot":
                test += f'; and test "$slot" -ge {key[0]}' if isinstance(key, tuple) else f'; and test "$slot" -eq {key}'
            lines += [("    if " if first else "    else if ") + test] + printed(words, "        ")
            first = False
        return lines + ([] if first else ["    end"]) + ["end", ""]

    return (["# The option strings of a command that take the next word as their value."]
            + function("__ao_valued", "cmd", "valued")
            + ["# The long options of a command; before any command, those of ao itself."]
            + function("__ao_options", "cmd", "options")
            + ["# The choices an option of a command declares."]
            + function("__ao_values", "cmd option", "values")
            + ["# The choices of a positional word (from 0) of a command; before any command, the commands."]
            + function("__ao_arguments", "cmd slot", "slot"))


_COMPLETION_FISH = r'''# What completes the word at the cursor: the command, a long option, or the choices of an option or a
# positional word. It fails when there is none, and fish then completes files.
function __ao_candidates
    set -l tokens (commandline -opc)
    set -l cur (commandline -ct)
    set -l cmd ''
    set -l rest ''
    set -l option ''
    set -l prefix ''
    set -l slot 0
    set -l i 2
    while test $i -le (count $tokens)
        set -l word $tokens[$i]
        if test -z "$rest"; and test -n "$cmd"; and contains -- "$word" '--'
            set rest 1
        else if test -z "$rest"; and string match -q -- '-?*' "$word"
            if contains -- "$word" (__ao_valued "$cmd")
                if test $i -eq (count $tokens)
                    set option "$word"
                    break
                end
                set i (math $i + 1)
            end
        else if test -z "$cmd"
            set cmd "$word"
        else
            set slot (math $slot + 1)
        end
        set i (math $i + 1)
    end
    if test -z "$option"; and test -z "$rest"; and string match -q -- '-?*=*' "$cur"
        set option (string split -m 1 -- '=' "$cur")[1]
        set prefix "$option="
    end
    set -l reply
    if test -n "$option"
        set reply (__ao_values "$cmd" "$option")
    else if test -z "$rest"; and string match -q -- '-*' "$cur"
        set reply (__ao_options "$cmd")
    else
        set reply (__ao_arguments "$cmd" "$slot")
    end
    set -q reply[1]; or return 1
    printf '%s\n' "$prefix"$reply
end

complete -c ao -n '__ao_candidates >/dev/null' -f -a '(__ao_candidates)'
'''


def _completion_fish(model):
    return (_completion_header("fish", "fish", ["ao completion fish > ~/.config/fish/completions/ao.fish"])
            + [""] + _completion_fish_functions(model) + _COMPLETION_FISH.splitlines())


# ---- PowerShell ----------------------------------------------------------------------------------------------------

# PowerShell reads each of these as a single quote, and a doubled one as the character itself.
_COMPLETION_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def _completion_ps_quote(word):
    """A single-quoted PowerShell literal: PowerShell reads back exactly `word`, where nothing is expanded."""
    return "'" + "".join(ch * 2 if ch in _COMPLETION_PS_QUOTES else ch for ch in word) + "'"


def _completion_ps_functions(model):
    """PowerShell functions that return what _completion_sh_functions sets (SHELL-COMPLETION).

    Each selects by `switch -Exact -CaseSensitive` on quoted literals, which compares strings exactly, as ao's
    parser does, and returns an array of quoted literals.
    """
    q, arms = _completion_ps_quote, _completion_arms(model)

    def returned(words, indent, guard=None):
        literals = [q(word) for word in words]
        opening, closing = (f"if ({guard}) {{ return @(", ") }") if guard else ("return @(", ")")
        line = f"{indent}{opening}{', '.join(literals)}{closing}"
        if len(line) <= COMPLETION_WIDTH:
            return [line]
        tokens = [literal + "," for literal in literals[:-1]] + literals[-1:]
        return [indent + opening] + _completion_wrapped(tokens, indent + "    ") + [indent + closing.strip()]

    def function(name, parameters, kind):
        lines = [f"    function {name}({parameters}) {{"]
        commands = list(dict.fromkeys(arm[0] for arm in arms if arm[1] == kind))
        if commands:
            lines += ["        switch -Exact -CaseSensitive ($Command) {"]
        for command in commands:
            lines += [f"            {q(command)} {{"]
            mine = [arm for arm in arms if arm[:2] == (command, kind)]
            if kind == "values":
                lines += ["                switch -Exact -CaseSensitive ($Option) {"]
                for _, _, flag, words in mine:
                    lines += [f"                    {q(flag)} {{"] + returned(words, "                        ") \
                        + ["                    }"]
                lines += ["                }"]
            elif kind == "slot":
                for _, _, key, words in mine:
                    guard = f"$Slot -ge {key[0]}" if isinstance(key, tuple) else f"$Slot -eq {key}"
                    lines += returned(words, "                ", guard)
            else:
                lines += returned(mine[0][3], "                ")
            lines += ["            }"]
        if commands:
            lines += ["        }"]
        return lines + ["        return @()", "    }", ""]

    return (["    # The option strings of a command that take the next word as their value."]
            + function("Get-AoValued", "[string]$Command", "valued")
            + ["    # The long options of a command; before any command, those of ao itself."]
            + function("Get-AoOptions", "[string]$Command", "options")
            + ["    # The choices an option of a command declares."]
            + function("Get-AoValues", "[string]$Command, [string]$Option", "values")
            + ["    # The choices of a positional word (from 0) of a command; before any command, the commands."]
            + function("Get-AoArguments", "[string]$Command, [int]$Slot", "slot"))


_COMPLETION_POWERSHELL = r'''
    # The words before the cursor name the command, then the positional word the cursor is on, or the
    # option whose value it is. Every comparison is ordinal and case-sensitive, as argparse compares.
    $words = @()
    foreach ($element in $commandAst.CommandElements) {
        if ($element.Extent.EndOffset -ge $cursorPosition) { break }
        if ($element -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
            $words += $element.Value
        } else {
            $words += $element.Extent.Text
        }
    }
    $command = ''
    $rest = $false
    $option = $null
    $slot = 0
    $i = 1
    while ($i -lt $words.Count) {
        $word = [string]$words[$i]
        if (-not $rest -and $command -cne '' -and $word -ceq '--') {
            $rest = $true
        } elseif (-not $rest -and $word.Length -gt 1 -and $word.StartsWith('-', [System.StringComparison]::Ordinal)) {
            if (@(Get-AoValued $command) -ccontains $word) {
                if ($i + 1 -ge $words.Count) {
                    $option = $word
                    break
                }
                $i++
            }
        } elseif ($command -ceq '') {
            $command = $word
        } else {
            $slot++
        }
        $i++
    }
    $current = [string]$wordToComplete
    $prefix = ''
    if ($null -eq $option -and -not $rest -and $current -cmatch '^(-[^=]+)=') {
        $option = $Matches[1]
        $prefix = $option + '='
        $current = $current.Substring($prefix.Length)
    }
    if ($null -ne $option) {
        $candidates = @(Get-AoValues $command $option)
    } elseif (-not $rest -and $current.StartsWith('-', [System.StringComparison]::Ordinal)) {
        $candidates = @(Get-AoOptions $command)
    } else {
        $candidates = @(Get-AoArguments $command $slot)
    }
    # Each word that begins with what is typed; one PowerShell would read as syntax goes in single quotes.
    foreach ($candidate in $candidates) {
        if (-not $candidate.StartsWith($current, [System.StringComparison]::Ordinal)) { continue }
        $text = $candidate
        if ($candidate -cnotmatch '^[\w./-]+$') {
            $text = "'" + ($candidate -replace "['\u2018\u2019\u201a\u201b]", '$0$0') + "'"
        }
        [System.Management.Automation.CompletionResult]::new($prefix + $text, $candidate, 'ParameterValue', $candidate)
    }
}
'''


def _completion_powershell(model):
    return (_completion_header("powershell", "PowerShell", ["ao completion powershell | Out-String | Invoke-Expression"],
                               ["as a line of your PowerShell profile, the file $PROFILE names."])
            + ["", "Register-ArgumentCompleter -Native -CommandName 'ao' -ScriptBlock {",
               "    param($wordToComplete, $commandAst, $cursorPosition)", ""]
            + _completion_ps_functions(model) + _COMPLETION_POWERSHELL.strip("\n").splitlines())
