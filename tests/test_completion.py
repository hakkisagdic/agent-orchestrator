"""`ao completion <shell>` prints a completion script generated from ao's own parser (SHELL-COMPLETION).

The script holds every command, the long options of each and the choices argparse declares, each word a
literal quoted for its shell, so a word holding a quote or a `$` is offered as it is and never read as
syntax. bash and zsh run the scripts here where they are installed; fish and PowerShell scripts are read
for their shape, since neither shell is on the runners.
"""
import argparse
import os
import shutil
import subprocess

import pytest

from ao import cli

SHELLS = ("bash", "zsh", "fish", "powershell")
# Windows' bash may be WSL's launcher rather than a shell, and neither script is for Windows' own shells.
POSIX_SHELL = "the bash and zsh scripts are run by a POSIX shell, which a Windows runner need not have"
HOSTILE = ["it's", "$(touch pwned)", "`touch pwned`", "a b", "~home"]


def _commands():
    parser = cli.build_parser()
    return sorted(next(action.choices for action in parser._actions if isinstance(action.choices, dict)))


def _hostile_parser():
    """A parser whose choices hold what each shell would read as syntax, and one word no shell lists safely."""
    parser = argparse.ArgumentParser(prog="ao")
    sub = parser.add_subparsers(dest="cmd")
    pick = sub.add_parser("pick")
    pick.add_argument("what", choices=HOSTILE + ["line\nbreak"])
    pick.add_argument("--mode", choices=["x'y", "$HOME"])
    return parser


def _run(argv, cwd, **kwargs):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120, **kwargs)


@pytest.mark.parametrize("shell", SHELLS)
def test_each_script_names_every_command_an_option_and_a_choice(project, capsys, shell):
    code = cli.main(["-C", project["root"], "completion", shell])
    out = capsys.readouterr()

    assert code == 0 and out.err == ""
    assert [name for name in _commands() if f"'{name}'" not in out.out] == []
    assert "'--allow-shared-hooks'" in out.out           # an option of `ao hooks`
    assert "'powershell'" in out.out                     # a choice: the shells of `ao completion` itself
    assert "\r" not in out.out and out.out.endswith("\n")


def test_an_unknown_shell_is_refused_naming_the_four(project, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-C", project["root"], "completion", "tcsh"])

    err = capsys.readouterr().err
    assert stopped.value.code == 2
    assert all(shell in err for shell in SHELLS)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_word_no_shell_lists_safely_is_left_out(shell):
    script = cli.completion_script(_hostile_parser(), shell)

    assert "line\nbreak" not in script and "break'" not in script
    assert "\r" not in script


def test_fish_and_powershell_hold_each_hostile_word_as_a_single_quoted_literal():
    fish = cli.completion_script(_hostile_parser(), "fish")
    powershell = cli.completion_script(_hostile_parser(), "powershell")

    assert "'it\\'s'" in fish and "'$(touch pwned)'" in fish and "'`touch pwned`'" in fish and "'x\\'y'" in fish
    assert "'it''s'" in powershell and "'$(touch pwned)'" in powershell and "'$HOME'" in powershell
    assert "complete -c ao " in fish
    assert "Register-ArgumentCompleter -Native -CommandName 'ao'" in powershell


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason=POSIX_SHELL)
def test_bash_reads_the_script_and_completes_a_command_and_a_choice(tmp_path):
    script = tmp_path / "ao.bash"
    script.write_text(cli.completion_script(cli.build_parser(), "bash"), encoding="utf-8")
    body = ('source "$1"\n'
            'COMP_WORDS=(ao rev); COMP_CWORD=1; _ao; printf "%s\\n" "${COMPREPLY[@]}"; echo --\n'
            'COMP_WORDS=(ao completion ""); COMP_CWORD=2; _ao; printf "%s\\n" "${COMPREPLY[@]}"\n')

    assert _run(["bash", "-n", str(script)], tmp_path).returncode == 0
    done = _run(["bash", "-c", body, "_", str(script)], tmp_path)

    assert done.returncode == 0, done.stderr
    commands, shells = done.stdout.split("--\n")
    assert commands.split() == ["review", "reviews"]
    assert shells.split() == list(SHELLS)


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason=POSIX_SHELL)
def test_bash_offers_a_hostile_word_quoted_and_runs_nothing(tmp_path):
    script = tmp_path / "ao.bash"
    script.write_text(cli.completion_script(_hostile_parser(), "bash"), encoding="utf-8")
    body = ('source "$1"\n'
            'COMP_WORDS=(ao pick ""); COMP_CWORD=2; _ao; printf "%s\\n" "${COMPREPLY[@]}"\n')

    done = _run(["bash", "-c", body, "_", str(script)], tmp_path)

    assert done.returncode == 0, done.stderr
    offered = done.stdout.splitlines()
    assert "it\\'s" in offered and "a\\ b" in offered and "\\~home" in offered
    assert all(word.startswith(("\\$", "\\`")) for word in offered if "touch" in word)
    assert not (tmp_path / "pwned").exists()


@pytest.mark.skipif(os.name == "nt" or shutil.which("zsh") is None, reason=POSIX_SHELL)
def test_zsh_reads_the_script_and_lists_each_word_as_it_is(tmp_path):
    ours = tmp_path / "_ao"
    ours.write_text(cli.completion_script(cli.build_parser(), "zsh"), encoding="utf-8")
    hostile = tmp_path / "_hostile"
    hostile.write_text(cli.completion_script(_hostile_parser(), "zsh"), encoding="utf-8")
    body = ('autoload -U compinit; compinit -u -d "$3"\n'
            'source "$1"; _ao_arguments "" 0; print -rl -- $reply; print -- --\n'
            'source "$2"; _ao_arguments pick 0; print -rl -- $reply; _ao_values pick --mode; print -rl -- $reply\n')

    assert _run(["zsh", "-n", str(ours)], tmp_path).returncode == 0
    done = _run(["zsh", "-f", "-c", body, "_", str(ours), str(hostile), str(tmp_path / "zcompdump")], tmp_path)

    assert done.returncode == 0, done.stderr
    commands, words = done.stdout.split("--\n")
    assert commands.split() == _commands_in_parser_order()
    assert words.splitlines() == HOSTILE + ["x'y", "$HOME"]
    assert not (tmp_path / "pwned").exists()


def _commands_in_parser_order():
    parser = cli.build_parser()
    return list(next(action.choices for action in parser._actions if isinstance(action.choices, dict)))
