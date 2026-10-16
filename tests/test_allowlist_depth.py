"""The grant audit fails closed where its reading of a rule runs out (ALLOWLIST-DEPTH).

ao's bug hunter found two ways a rule passed the audit on 2026-10-12. The reading took off eight programs that run
the command after them and stopped, so `Bash(nice nice nice nice nice nice nice nice nice ao features off review
--by p:*)` was read as a `nice` command nothing forbids; and it took `rtk` off only before git and before rtk's own
wrappers, though rtk runs a program it has no filter for as it is, so `Bash(rtk ruby:*)` was no interpreter rule.
Looking for the same two shapes found more, each a rule read as the plain command it is not, or not read at all: a
wrapper, git or rtk named by its path, a shell's `NAME=VALUE` before a command, `--` before timeout's duration, an
exact rule behind a wrapper, a setting asked only of the rule as it stands, and a wildcard where the reading needs a
word (`Bash(timeout * pytest:*)`, `Bash(ls*)`). A run of wildcards kept the matcher past any wait.

The review of that found three more, each where the reading assumed: an option of a wrapper it did not list was taken
to take no value, and macOS's `env -P /usr/bin git` takes `/usr/bin` as the folder to find git in; a wildcard in a
program's folder was dropped with the folder, so `Bash(/usr/*/git status:*)` read as `Bash(git status:*)` and admits
`/usr/bin/python3 -c x /tmp/git status`; and an exact rule was compared with ao's sample commands as strings, so
`Bash(git commit --no-verify -m y)`, with no wrapper at all, skipped the hook beside the sample's `-m x`. Attacking
the result the same way found the rest of the class: a person's act with an option among its words or ao's `-C` before
them, `git commit -nm`, `--no-veri`, `-c core.hooksPath` and `-c alias.x`, a variable that hands git its
configuration, xargs, a rule split into words at spaces rather than as the shell splits it (`g"i"t`, `gi\\t`,
`-{n,m}`), a comma inside a rule's parentheses, and a dotless `ı`.

The reading now takes off every wrapper however deep, its options declared whole for GNU and BSD, a rewriter before
any program and a program as Windows starts it; it reads a wildcard, an option or a variable it does not know as any
command; and every rule is asked what its own command does, as the operation each forbidden command is.
"""
import pytest

from ao import allowlist as AL, cli, lib as A

NINE = "nice " * 9
HOOK = "skips the commit hook, as the command it runs"
CODE = "runs arbitrary code"
FILE_CODE = "runs code the project may hold"


def _problems(*rules, role=None):
    return AL.problems(["claude", "-p", "{prompt}", "--allowedTools", ",".join(("Read",) + rules)], {}, role=role)


def _named(rule, role=None):
    """(reason, rule) for each finding of a grant of `Read` and `rule`."""
    return [(reason, granted) for reason, _, granted in _problems(rule, role=role)]


# ---- the first round -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rule, reason", [
    (f"Bash({NINE}ao features off review --by p:*)", "switches review off, as the command it runs"),
    ("Bash(" + "timeout 1 " * 9 + "git commit:*)", HOOK),
    (f"Bash({NINE}ao features off review --by Alice)", "switches review off"),
    (f"Bash({NINE}python3 x.py)", "runs arbitrary code"),
])
def test_a_rule_nested_past_eight_wrappers_is_read_to_the_command_it_runs(rule, reason):
    assert _named(rule) == [(reason, rule)]


def test_every_wrapper_is_taken_off_however_deep():
    assert AL.unwrap(("nice " * 50 + "rtk proxy timeout 5 git -C . commit -n").split()) == ["git", "commit", "-n"]
    assert AL.normalized_rule("Bash(" + "nohup " * 50 + "git status:*)") == "Bash(git status:*)"


@pytest.mark.parametrize("rule, program", [
    ("Bash(rtk ruby:*)", "ruby"),
    ("Bash(rtk perl -e:*)", "perl"),
    ("Bash(rtk python3 -O -c:*)", "python3"),
    ("Bash(rtk node --eval:*)", "node"),
    ("Bash(rtk python3 x.py)", "python3"),
    ("Bash(rtk.exe ruby:*)", "ruby"),
])
def test_an_interpreter_reached_through_a_rewriter_is_the_interpreter_rule_it_is(rule, program):
    assert AL.interpreter_rule(rule) == program
    assert _problems(rule) == [("runs arbitrary code", f"{program} ...", rule)]


def test_an_interpreter_through_a_rewriter_asked_only_its_version_is_no_grant_of_code_as_the_plain_one_is_none():
    assert _problems("Bash(rtk python3 --version)") == _problems("Bash(python3 --version)") == []


def test_the_forms_of_the_lead_that_were_already_read_are_read_as_before():
    """The lead named `Bash(rtk python3 -c:*)` and rtk's declared wrappers: rtk's form of a forbidden sample is asked as
    it stands, and a wrapper rtk declares was taken off before an interpreter, before this slice too."""
    assert _problems("Bash(rtk python3 -c:*)") == [
        ("runs arbitrary code", "rtk python3 -c x", "Bash(rtk python3 -c:*)")]
    assert _problems("Bash(rtk proxy ruby:*)") == [("runs arbitrary code", "ruby ...", "Bash(rtk proxy ruby:*)")]


@pytest.mark.parametrize("rule, reason", [
    ("Bash(rtk ao features off review --by Alice)", "switches review off"),
    ("Bash(rtk timeout 60 pytest:*)", "runs arbitrary code, as the command it runs"),
    ("Bash(rtk env FOO=1 git commit:*)", HOOK),
    # Nothing declares which of a rewriter's own options takes the next word: the program could be either.
    ("Bash(rtk -v ls:*)", HOOK),
    ("Bash(rtk proxy -q ls:*)", HOOK),
])
def test_a_rewriter_is_taken_off_before_any_program(rule, reason):
    assert _named(rule) == [(reason, rule)]


@pytest.mark.parametrize("rule, reason", [
    ("Bash(/usr/bin/nice git commit:*)", FILE_CODE),
    ("Bash(NICE.EXE git commit:*)", HOOK),
    ("Bash(/usr/bin/git -C . commit:*)", FILE_CODE),
    ("Bash(sudo /usr/bin/git -C . commit:*)", FILE_CODE),
    ("Bash(rtk.exe git -C . commit:*)", HOOK),
    ("Bash(/usr/local/bin/rtk proxy git commit:*)", FILE_CODE),
    ("Bash(/usr/local/bin/ao features off review --by Alice)", FILE_CODE),
    ("Bash(rtk /usr/bin/python3 -c:*)", FILE_CODE),
])
def test_only_bare_program_names_are_folded_and_program_paths_are_code(rule, reason):
    assert _named(rule) == [(reason, rule)]


def test_env_s_string_is_a_command_line_env_splits_itself():
    assert AL.normalized_rule('Bash(env -S "sh -c x")') == "Bash(*)"
    assert _named('Bash(env -S "sh -c x")') == [(HOOK, 'Bash(env -S "sh -c x")')]


@pytest.mark.parametrize("rule", ["Bash(LANG=C git commit:*)", "Bash(timeout -- 60 git commit:*)",
                                  "Bash(env -- TZ=UTC git commit:*)"])
def test_a_shell_s_assignment_and_the_operands_after_double_dash_are_taken_off(rule):
    assert AL.normalized_rule(rule) == "Bash(git commit:*)"
    assert _named(rule) == [(HOOK, rule)]


def test_a_quote_left_open_anywhere_admits_any_command():
    """A quote that opens in an assignment or an option and is never closed leaves no reading of what runs."""
    for rule in ('Bash(FOO="a git commit:*)', "Bash(timeout 'x git commit:*)", "Bash(git commit -m 'x)"):
        assert AL.normalized_rule(rule) == "Bash(*)", rule


def test_an_exact_rule_is_read_as_the_exact_command_it_runs():
    rule = "Bash(nice git commit --no-verify -m x)"

    assert AL.normalized_rule(rule) == "Bash(git commit --no-verify -m x)"
    assert _named(rule) == [(HOOK, rule)]
    assert _problems("Bash(timeout 60 git status)", "Bash(nice -n 5 ao status)") == []


@pytest.mark.parametrize("rule", ["Bash(timeout 60 ao config set round_budget:*)",
                                  "Bash(rtk ao config set round_budget:*)"])
def test_a_setting_an_implementer_may_not_change_is_asked_of_the_command_a_rule_runs(rule):
    assert _problems(rule, role="implementer") == [
        ("changes a setting that governs its own work", "ao config set round_budget 1", rule)]


@pytest.mark.parametrize("rule", [
    "Bash(nice*)", "Bash(ti*)", "Bash(ls*)", "Bash(ruby*)", "Bash(* --version)",
    "Bash(timeout * pytest:*)", "Bash(timeout * ao features off review --by p)", "Bash(timeout 6* pytest:*)",
    "Bash(nice -n * git status)", "Bash(env FOO=* git status)", "Bash(timeout 60 py*)", "Bash(nice x*)",
    "Bash(git -C * commit:*)", "Bash(git -* status)",
    "Bash(FOO=* git status)", "Bash(FOO=a* git status)", "Bash(A=1 B=* nice git status)", "Bash(FOO='*' git status)",
    # Where git's or ao's own options may still stand: `git -c alias.x=!sh x` (ALLOWLIST-DEPTH).
    "Bash(nice git *)", "Bash(timeout 5 git -C . *)", "Bash(nice rtk git -c *)", "Bash(nice ao *)",
    "Bash(nice ao -C *)",
    "Bash(timeout 60 ao lock *)", "Bash(nice git \\*)",
])
def test_a_wildcard_where_the_reading_needs_a_word_admits_any_command(rule):
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _named(rule) == [(HOOK, rule)]


@pytest.mark.parametrize("rule, plain, reason", [
    ("Bash(timeout 60 git commit:*)", "Bash(git commit:*)", HOOK),
    ("Bash(nice ao features off review *)", "Bash(ao features off review:*)", "switches review off, as the command it runs"),
])
def test_a_wildcard_after_complete_words_stays_where_it_stands(rule, plain, reason):
    assert AL.normalized_rule(rule) == plain
    assert _named(rule) == [(reason, rule)]


def test_everyday_rules_with_a_wildcard_after_their_program_are_not_named():
    assert _problems("Bash(git status:*)", "Bash(git log --oneline *)", "Bash(cat src/ *)", "Bash(ls -la *)",
                     "Bash(timeout 60 git status:*)", "Bash(LC_ALL=C ls:*)", "Bash(rtk read:*)", "Bash(rtk ls:*)",
                     "Bash(rtk git diff:*)", "Bash(rtk --version)", "Bash(ao status:*)", role="implementer") == []


def test_a_run_of_wildcards_is_asked_as_one():
    """`Bash(********Z)` did not answer one command in ten seconds: each `*` was a `.*` the matcher tried every split
    of, and the audit asks each rule a hundred commands."""
    rule = "Bash(" + "*" * 40 + "z)"

    assert not AL.admits(rule, "ao collect-review n --response r --model m --by b")
    assert AL.admits(rule, "sh -c x z")
    assert _named(rule) == [(HOOK, rule)]


# ---- the second round: a wrapper's options, declared whole -----------------------------------------------------------

@pytest.mark.parametrize("rule", ["Bash(env -P /usr/bin git:*)", "Bash(env -P/usr/bin git commit -n -m x)",
                                  "Bash(genv -P /usr/bin git:*)", "Bash(env -iP /usr/bin git status)",
                                  "Bash(env -u X -S git:*)", "Bash(env --split-string=git:*)", "Bash(env --sp=git:*)",
                                  "Bash(sudo -e git:*)", "Bash(sudo --chroot=/x git status)"])
def test_an_option_after_which_a_wrapper_runs_no_program_the_reading_names_admits_any_command(rule):
    """The review: macOS's env takes `-P altpath` as the folder it finds the program in, and the reading took an
    option it did not list for one with no value, so `/usr/bin` read as the program and git went unread."""
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _named(rule) == [(HOOK, rule)]


@pytest.mark.parametrize("rule", [
    "Bash(env -i -0 -v git commit:*)", "Bash(env -u X -C . git commit:*)", "Bash(env -uX git commit:*)",
    "Bash(env -iu X git commit:*)", "Bash(env --unset=X --chdir=. git commit:*)", "Bash(env --unset X git commit:*)",
    "Bash(env --ch=. git commit:*)", "Bash(env - git commit:*)", "Bash(env -a x git commit:*)",
    "Bash(timeout -vk 5 60 git commit:*)", "Bash(timeout -fp -s KILL 60 git commit:*)",
    "Bash(gtimeout --signal=KILL --kill-after=1 60 git commit:*)", "Bash(timeout --sig KILL 60 git commit:*)",
    "Bash(nice -5 git commit:*)", "Bash(nice --10 git commit:*)", "Bash(nice -n5 git commit:*)",
    "Bash(gnice --adjustment=5 git commit:*)", "Bash(nohup -- git commit:*)",
    "Bash(stdbuf -oL -e0 git commit:*)", "Bash(stdbuf --output=L git commit:*)",
    "Bash(xargs -0 -n 1 -P 4 git commit:*)", "Bash(xargs -a list -r git commit:*)", "Bash(xargs -J @ git commit:*)",
    "Bash(time -p git commit:*)", "Bash(time -o out -a git commit:*)", "Bash(gtime --format=x git commit:*)",
    "Bash(command -p git commit:*)", "Bash(exec -a x git commit:*)", "Bash(exec -cl git commit:*)",
    "Bash(caffeinate -i -t 5 git commit:*)", "Bash(sudo -u root -E git commit:*)",
    "Bash(sudo --user=root git commit:*)",
    "Bash(sudo -hhost git commit:*)", "Bash(builtin exec git commit:*)", "Bash(noglob git commit:*)",
    "Bash(nocorrect git commit:*)", "Bash(coproc git commit:*)", "Bash(- git commit:*)",
])
def test_each_wrapper_s_options_are_read_as_getopt_reads_them_for_gnu_and_bsd(rule):
    assert AL.normalized_rule(rule) == ("Bash(*)" if " -a x " in rule or "xargs -J " in rule else "Bash(git commit:*)")


def test_a_number_standing_on_its_own_is_nice_s_adjustment_and_the_command_after_reads_as_it_stands():
    """`nice -10`: one number, recorded under the pseudo-option WRAPPER_NUMBERS' wrappers declare inert, so
    the reading walks past it rather than into an unknown option, and widens nothing after it."""
    assert AL.normalized_rule("Bash(nice -10 git commit:*)") == "Bash(git commit:*)"
    assert AL.normalized_rule("Bash(nice -10 git commit -n -m x)") == "Bash(git commit -n -m x)"


@pytest.mark.parametrize("rule", [
    "Bash(env -Z git status)", "Bash(env --frob git status)", "Bash(env --i git status)",
    "Bash(timeout -x 60 git status)", "Bash(timeout -vx 60 git status)", "Bash(nohup -n git status)",
    "Bash(nice -q git status)", "Bash(sudo --frob git status)", "Bash(stdbuf -z git status)",
    "Bash(command -x git status)", "Bash(builtin -x git status)",
])
def test_an_option_a_wrapper_does_not_declare_admits_any_command(rule):
    """A letter it does not declare leaves the cluster unsplit, and a long option that is no start of one name, as
    `--i` starts two of env's, names none: either may take the next word as its value."""
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _named(rule) == [(HOOK, rule)]


def test_command_asked_where_a_program_is_runs_none():
    assert AL.unwrap(["command", "-v", "git"]) == [] and AL.normalized_rule("Bash(command -v:*)") is None
    assert _problems("Bash(command -v:*)", "Bash(command -V git)") == []


@pytest.mark.parametrize("rule", ["Bash(sudo -s)", "Bash(sudo -i)", "Bash(sudo -u root --shell)"])
def test_a_shell_sudo_starts_with_no_command_reads_any_command(rule):
    """Given no command, `sudo -s` starts a shell that reads its commands from what it is handed: `echo 'git commit -n
    -m x' | sudo -s`. The reading took it for a wrapper with nothing after it."""
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _named(rule) == [(HOOK, rule)]
    assert _problems("Bash(sudo -s git status)")  # the selected shell can run startup code too


# ---- the second round: a wildcard in a word the reading needs ------------------------------------------------------

@pytest.mark.parametrize("rule", ["Bash(/usr/*/git status:*)", "Bash(/usr/bin/*:*)", "Bash(nice /usr/*/git status:*)",
                                  "Bash(timeout 5 /opt/*/bin/git log:*)", "Bash(rtk proxy /*/git status:*)",
                                  "Bash(C:\\*\\git.exe status:*)", 'Bash("/usr/*/git" status)'])
def test_a_wildcard_in_a_program_s_folder_admits_any_command(rule):
    """The review: read by its name, `Bash(/usr/*/git status:*)` was `Bash(git status:*)`, and it admits
    `/usr/bin/python3 -c x /tmp/git status`."""
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _named(rule) == [(HOOK, rule)]


def test_the_matcher_reads_a_wildcard_in_a_program_s_folder_as_the_harness_does():
    rule = "Bash(/usr/*/git status:*)"

    assert AL.admits(rule, "/usr/bin/python3 -c x /tmp/git status --short")
    assert not AL.admits(rule, "git status --short")


# ---- the second round: a rule judged by what its own command does --------------------------------------------------

@pytest.mark.parametrize("rule", ["Bash(git commit --no-verify -m y)", "Bash(nice git commit --no-verify -m y)",
                                  "Bash(git commit -n -m fix-the-parser)", "Bash(git commit -nm y)",
                                  "Bash(git commit -anm y)", "Bash(git commit -qvn -m y)",
                                  "Bash(git commit --no-veri -m y)",
                                  "Bash(git -C src commit -n -F msg.txt)", "Bash(git commit -m y --no-verify)"])
def test_an_exact_rule_is_judged_by_what_its_command_does_not_by_a_sample(rule):
    """The review: an exact rule was compared with the samples as strings, so `Bash(nice git commit --no-verify -m y)`
    passed beside the sample's `-m x`; and so did the same rule with no wrapper at all."""
    assert [reason for reason, _, _ in _problems(rule)] == [HOOK if "-C src" in rule else "skips the commit hook"]


@pytest.mark.parametrize("rule", ["Bash(git commit -mn)", "Bash(git commit -m x)", "Bash(git commit --no-edit -m y)",
                                  "Bash(git commit -Fn)", "Bash(git commit --amend --no-edit)"])
def test_an_exact_commit_through_the_hook_is_code_but_not_a_hook_bypass(rule):
    """`-mn` is a message `n`; the command still runs commit hooks."""
    assert _named(rule) == [(CODE, rule)]


PLACES = {"x": "y", "p": "Alice", "b": "Bob", "w": "late", "s": "S1", "r": "answer.md", "m": "gpt", "n": "nonce",
          "d": "abc123", "f": "openai", "30": "45", "1": "2", "/dev/null": ".hooks", "src/app.py": "src/main.py",
          "--output=src/app.py": "--output=src/main.py", ";": "\\;"}


def _written_otherwise(command, own_options):
    """A forbidden command with each word that only fills a place written otherwise, and git's or ao's own `-C .`."""
    words = [PLACES.get(word, word) for word in command.split()]
    return " ".join(words[:1] + ["-C", "."] + words[1:] if own_options and words[0] in ("git", "ao") else words)


SAMPLES = [(None, reason, command) for reason, command in AL.FORBIDDEN] \
    + [("implementer", reason, command) for reason, command in AL.IMPLEMENTER_FORBIDDEN] \
    + [("architect", reason, command) for reason, command in AL.ARCHITECT_FORBIDDEN] \
    + [("implementer", "changes a setting that governs its own work", command) for command in AL.settings_commands()]


@pytest.mark.parametrize("own_options", [False, True], ids=["plain", "own options"])
@pytest.mark.parametrize("role, reason, command", SAMPLES)
def test_each_forbidden_command_is_the_operation_it_performs_however_its_places_are_filled(role, reason, command,
                                                                                           own_options):
    """Each sample is one spelling: written with other values in its places, as an exact rule, it is named for the
    operation it performs. `ao lock` runs the command after it, which is what is named; a setting with a reason of
    its own is named for that."""
    rule = f"Bash({_written_otherwise(command, own_options)})"

    found = _problems(rule, role=role)

    assert found and found[0][2] == rule, (rule, found)
    assert found[0][0].startswith((reason, CODE, HOOK, "changes the settings that govern its own review",
                                   "opts into same-family review")), (rule, found)


@pytest.mark.parametrize("rule, reason", [
    ("Bash(ao features --by Alice off review)", "switches review off"),
    ("Bash(ao features off --by Alice review)", "switches review off"),
    ("Bash(ao -C . features off review --by Alice)", "switches review off"),
    ("Bash(ao --root=. features off review --by Alice)", HOOK),
    ("Bash(ao --ro . -- features off review --by Alice)", HOOK),
    ("Bash(ao features off review --b Alice)", "switches review off"),
    ("Bash(ao features off review --by=Alice)", "switches review off"),
    ("Bash(ao push --minutes 5 allow)", "opens a push window"),
    ("Bash(ao push --min=5 allow)", "opens a push window"),
    ("Bash(ao hooks --allow-shared-hooks uninstall)", "removes the hooks"),
    ("Bash(ao config --machine set review.same_family labeled --by p)", "opts into same-family review"),
    ("Bash(ao init --review-t same-family --by p)", "opts into same-family review"),
    ("Bash(ao init --review-tier=same-family --by p)", "opts into same-family review"),
    ("Bash(ao catchup --author-fam f --by b)", "names the family that wrote a waived range"),
    ("Bash(ao catchup --move=S1 --by b)", "states which waived slices only moved code"),
    ("Bash(ao harness --by p seen --dig d)", "records harness configuration as seen"),
    ("Bash(ao harness seen --a --by p)", "records harness configuration as seen"),
    ("Bash(ao -C . waive --by b review --slice s --why w)", "waives a gate"),
])
def test_a_person_s_act_is_read_as_argparse_reads_its_command_line(rule, reason):
    """argparse lets an option stand among the positionals and be named by any start of its name, and ao takes `-C`
    before its subcommand: the act's words were asked to come first, as written, so each of these passed."""
    assert _named(rule) == [(reason, rule)]


@pytest.mark.parametrize("rule", ["Bash(ao features on review --by Alice)", "Bash(ao features --by Alice list)",
                                  "Bash(ao push status)", "Bash(ao harness seen --by Alice)", "Bash(ao hooks status)",
                                  "Bash(ao init --profile claude-kiro)"])
def test_a_command_line_that_performs_no_act_is_not_named(rule):
    assert _problems(rule) == []


@pytest.mark.parametrize("rule, reason", [
    ("Bash(ao config set round_budget 9)", "changes a setting that governs its own work"),
    ("Bash(ao config --machine set round_budget 9)", "changes a setting that governs its own work"),
    ("Bash(ao config set review_timeout 9)", "changes the settings that govern its own review"),
    ("Bash(ao decide redo-it --sc S1)", "re-specifies a slice, restarting its round budget"),
])
def test_an_exact_rule_that_changes_what_governs_an_implementer_is_named(rule, reason):
    assert _named(rule, role="implementer") == [(reason, rule)]


@pytest.mark.parametrize("rule, reason", [
    ("Bash(git -c core.hooksPath=/tmp/h commit -m y)", "redirects the commit hook"),
    ("Bash(git -c CORE.HOOKSPATH=/dev/null commit -m y)", "redirects the commit hook"),
    ("Bash(git --config-env=core.hooksPath=H commit -m y)", "redirects the commit hook"),
    ("Bash(git --config-env core.hooksPath=H status)", "redirects the commit hook"),
    ("Bash(git config core.hooksPath .hooks)", "rewrites the hook configuration"),
    ("Bash(git config --local core.hookspath .hooks)", "rewrites the hook configuration"),
    ("Bash(git config set core.hooksPath .hooks)", "rewrites the hook configuration"),
    ("Bash(git config --unset core.hooksPath)", "rewrites the hook configuration"),
    ("Bash(git config include.path ../x.cfg)", "rewrites the hook configuration"),
])
def test_configuration_that_moves_the_hook_is_named_whatever_value_it_holds(rule, reason):
    assert _named(rule) == [(reason, rule)]
    assert _problems("Bash(git -c core.hooksPath=.h commit -m y)")[0][1] == "git -c core.hooksPath=.h commit -m y"


@pytest.mark.parametrize("rule", ["Bash(git -c alias.ci=commit ci -n -m x)", "Bash(git -c include.path=x.cfg status)",
                                  "Bash(git -c core.fsmonitor=./x status)", "Bash(git -c core.pager=less log)",
                                  "Bash(git --exec-path=./bin commit -m x)", "Bash(git -c=x status)",
                                  "Bash(git --frob status)"])
def test_configuration_or_an_option_of_git_s_the_reading_does_not_know_admits_any_command(rule):
    """`alias.x=!sh` runs a shell, `core.fsmonitor` a program, `include.path` a file that may set `core.hooksPath`, and
    `--exec-path=` the folder `git commit` runs `git maintenance` from."""
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _named(rule) == [(HOOK, rule)]


def test_even_inert_git_configuration_is_preserved_when_normalizing():
    rule = "Bash(git -c color.ui=never -c core.quotepath=off log:*)"
    assert AL.normalized_rule(rule) == rule
    assert _problems("Bash(git -c color.ui=never log:*)", "Bash(git -c user.name=Bot status)") == []


@pytest.mark.parametrize("rule", ["Bash(FOO=1 git status)", "Bash(PATH=. git status)",
                                  "Bash(LD_PRELOAD=./x.so git status)", "Bash(lang=C git status)",
                                  "Bash(GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath "
                                  "GIT_CONFIG_VALUE_0=/dev/null "
                                  "git commit -m x)", "Bash(PYTEST_ADDOPTS='-p x' pytest)",
                                  "Bash(env NODE_OPTIONS=--require=./x.js node --version)",
                                  "Bash(sudo LD_PRELOAD=x git status)"])
def test_a_variable_the_reading_does_not_know_to_change_nothing_admits_any_command(rule):
    """A variable can name the program (PATH), load code into it (LD_PRELOAD), hand it options (PYTEST_ADDOPTS) or
    hand git `core.hooksPath` (GIT_CONFIG_COUNT and its keys): taken off as `env`'s are, the last read as a commit
    through the hook."""
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _named(rule) == [(HOOK, rule)]


# ---- the second round: what the self-attack found ------------------------------------------------------------------

@pytest.mark.parametrize("rule", ['Bash(g"i"t commit -n -m x)', "Bash('git' commit '-n' -m x)",
                                  r"Bash(git commit -\-no-verify -m x)", "Bash(git commit -m 'a b' -n)",
                                  r"Bash(\git commit -n -m y)", r"Bash(gi\t commit -\n -m x)"])
def test_a_rule_is_split_into_words_as_the_shell_splits_it(rule):
    """Split at spaces, `g"i"t` and `-\\-no-verify` were no program and no option of git's: bash runs both."""
    assert _problems(rule)[0][0].startswith("skips the commit hook")


def test_a_line_continuation_with_a_windows_reading_is_unknown():
    assert AL.normalized_rule("Bash(nice git \\\n commit -n -m x)") == "Bash(*)"


@pytest.mark.parametrize("rule", [r"Bash(C:\Git\bin\git.exe commit -n -m x)",
                                  r"Bash(.\ao.exe features off review --by A)", "Bash(gi^t commit -n -m x)",
                                  "Bash(%GIT% commit -n -m x)"])
def test_a_program_windows_reads_otherwise_admits_any_command(rule):
    """A POSIX shell, Git Bash's too, takes the backslash as an escape, Windows as a folder; cmd.exe takes the caret
    as its escape and fills in a %VARIABLE%: where the two name different programs, the rule could run either."""
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)[0][0].startswith("skips the commit hook")


@pytest.mark.parametrize("rule", ["Bash(git commit -{n,m} x)", "Bash(git commit -{n..n} -m x)",
                                  "Bash({git,commit} -n -m x)", "Bash(git commit -? -m x)",
                                  "Bash(git commit -[n] -m x)",
                                  "Bash(git commit $FLAG -m x)", 'Bash(git commit "$FLAG" -m x)',
                                  "Bash(=git commit -n -m x)", "Bash(git$IFS commit -n)"])
def test_a_word_the_shell_expands_when_it_runs_is_read_as_a_wildcard(rule):
    """`-{n,m}` is `-n -m` to bash, `-?` any file name one letter after a dash, `$FLAG` what the variable holds, and
    zsh's `=git` git's own path."""
    assert _problems(rule)[0][0].startswith("skips the commit hook")


@pytest.mark.parametrize("rule", ["Bash(git status; git commit -n -m x)", "Bash(git status && git commit -n)",
                                  "Bash(git commit -m $(cat m) -n)", "Bash(git commit -m `cat m`)",
                                  "Bash((git commit -n -m x))", "Bash(git status | sh)"])
def test_shell_syntax_the_reading_does_not_read_admits_any_command(rule):
    assert AL.normalized_rule(rule) == "Bash(*)"


def test_even_a_redirection_after_a_harmless_command_fails_closed():
    assert AL.normalized_rule("Bash(npm test 2>&1)") == "Bash(*)"
    assert _problems("Bash(npm test 2>&1)")


def test_a_comma_inside_a_rule_makes_the_whole_tool_list_unknown():
    """Harnesses disagree about inner commas, so no safe fragment is retained."""
    assert AL.rules(["x", "--allowedTools", "Read,Bash(git commit -{n,m} x),Edit"]) == ["Bash(*)"]
    assert AL.rules(["x", "--allowedTools", "Read Bash(git log:*)"]) == ["Read", "Bash(git log:*)"]


@pytest.mark.parametrize("rule", ["Bash(g\u0131t commit -n -m x)", "Bash(\u017fudo git commit -n -m x)",
                                  "Bash(git. commit -n -m x)", "Bash(GIT.EXE. commit -n -m x)",
                                  "Bash(\ufb01nd . -exec sh -c x \\;)"])
def test_a_program_s_name_is_folded_as_any_platform_folds_it(rule):
    """Windows matches a name by its upper case, where a dotless ı is I and a long ſ is S, and drops the dots at a
    name's end: folded only to lower case, `gıt` read as no program ao knows."""
    assert _problems(rule)


@pytest.mark.parametrize("rule", ["Bash(C:git.exe commit -n -m x)", "Bash('git.exe::$DATA' commit -n -m x)",
                                  "Bash(POWERS~1 -c x)"])
def test_a_program_windows_names_by_its_drive_its_stream_or_its_short_name_is_that_program(rule):
    """`C:git.exe` is git.exe in drive C's folder, `::$DATA` the file's own stream, and `POWERS~1` the short name
    Windows gives a long one such as powershell.exe: a short name reads as any command."""
    assert _problems(rule)


@pytest.mark.parametrize("rule, program", [("Bash(ruby3.2 -e:*)", "ruby3.2"), ("Bash(perl5.36 -e x)", "perl5.36"),
                                           ("Bash(node20 x.js)", "node20"), ("Bash(ksh93 -c x)", "ksh93")])
def test_an_interpreter_is_read_by_its_versioned_name_too(rule, program):
    """Only python's names were read with a version, and a distribution installs ruby3.2, perl5.36 and ksh93."""
    assert AL.interpreter_rule(rule) == program
    assert _named(rule) == [(CODE, rule)]


def test_a_look_alike_no_platform_folds_is_no_program_ao_knows():
    assert _problems("Bash(\uff47\uff49\uff54 commit -n -m x)")  # unknown program, not a harmless look-alike
    rule = "Bash(git commit \u2014no-verify -m x)"
    assert _named(rule) == [(CODE, rule)]  # runs hooks; the look-alike is not --no-verify


@pytest.mark.parametrize("rule", ['Bash(git -C "" commit -n -m x)', 'Bash("" git commit -n -m x)',
                                  'Bash(git "" commit -n -m x)', 'Bash(env "" git commit -n)',
                                  'Bash(ao "" features off review --by A)'])
def test_quoted_empty_words_are_outside_the_grammar(rule):
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)


@pytest.mark.parametrize("rule", ["Bash(xargs git)", "Bash(xargs -I{} git {} -m x)",
                                  "Bash(xargs -I % git commit % -m x)",
                                  "Bash(xargs -i git {} -m x)", "Bash(xargs --replace=@ git @ -m x)",
                                  "Bash(xargs -0 git commit -m x)"])
def test_what_xargs_reads_is_part_of_the_command_it_runs(rule):
    """xargs adds the words it reads after the command's, or puts them in place of its replace string: `cat list |
    xargs git` runs whatever git command the list holds."""
    assert _problems(rule)[0][0].startswith("skips the commit hook")


@pytest.mark.parametrize("command", [
    "xargs -I status git status*", "xargs -I status_x git status*",
    "xargs -I status ao push status*", "xargs -I on ao features on*",
    "xargs --replace=status_x git status*", "gxargs -I status git status*",
    "xargs -I off -I on ao features on*", "nice xargs -I on ao features on*",
])
def test_xargs_replacement_in_a_wildcarded_word_cannot_hide_an_operation(command):
    """The replacement may be in the final prefix, or completed by its wildcard."""
    rule = f"Bash({command})"
    assert _problems(rule, role="implementer")
    refusal = A.turn_refusal("implementer", "claude-code", ["claude", "--allowedTools", rule])
    assert refusal and "its grant admits" in refusal


@pytest.mark.parametrize("command", [
    "xargs -I z git status -z", "xargs -I a ls -a", "xargs -I short git status --short",
    "nice xargs -I a ls -a", "xargs -I a nice ls -a",
])
def test_xargs_replacement_that_leaves_a_partial_option_fails_closed(command):
    rule = f"Bash({command})"
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)
    refusal = A.turn_refusal("implementer", "claude-code", ["claude", "--allowedTools", rule])
    assert refusal and "its grant admits" in refusal


@pytest.mark.parametrize("command", [
    "xargs -I token git status*",
    "xargs -I FILE git status src/FILE*",
    "xargs -I FILE_name git status src/FILE*",
])
def test_xargs_replacement_never_preserves_a_partial_word(command):
    rule = f"Bash({command})"
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)


@pytest.mark.parametrize("rule, reason", [("Bash(ao lock -- git commit -n -m x)", HOOK),
                                          ("Bash(ao lock --wait 5 -- python3 x.py)", CODE),
                                          ("Bash(ao -C . lock git commit --no-verify -m y)", "skips the commit hook")])
def test_ao_lock_is_read_as_the_wrapper_it_is(rule, reason):
    assert _named(rule) == [(reason, rule)]
    assert _problems("Bash(ao lock -- pytest -q)")


@pytest.mark.parametrize("rule", ["Bash(make test)", "Bash(make)", "Bash(gmake -j4 all)",
                                  "Bash(npx prettier --check .)",
                                  "Bash(uvx ruff check)", "Bash(npm --prefix web run build)", "Bash(npm exec eslint)",
                                  "Bash(npm run-script build)", "Bash(uv run pytest)", "Bash(uv tool run ruff)",
                                  "Bash(pytest -vp x)", "Bash(pytest -p myplugin tests)",
                                  r"Bash(find . -name x -exec rm {} \;)"])
def test_an_exact_rule_that_runs_code_it_names_is_named(rule):
    assert _named(rule) == [(HOOK if "\\" in rule or "{" in rule or "--prefix" in rule else CODE, rule)]
    assert _problems("Bash(npx --version)", "Bash(make --version)", "Bash(pytest --version)",
                     "Bash(find . -name x -print)") == []


@pytest.mark.parametrize("rule, reason", [("Bash(rm src/app.py)", "removes a product file"),
                                          ("Bash(rm -f agent-mail/../src/app.py)", "removes a product file"),
                                          ("Bash(rm -- -rf)", "removes a product file"),
                                          ("Bash(cp -t src agent-mail/x)", "writes a product file"),
                                          ("Bash(cp --target-directory=src agent-mail/x)", "writes a product file"),
                                          ("Bash(mv agent-mail/x README.md)", "writes a product file"),
                                          ("Bash(tee -a src/app.py)", "writes a product file"),
                                          ("Bash(git log -p --output=src/x.patch)", "writes a product file"),
                                          ("Bash(git format-patch -o src HEAD~1)", "writes a product file")])
def test_an_architect_s_exact_rule_that_writes_outside_ao_s_files_is_named(rule, reason):
    assert _named(rule, role="architect") == [(reason, rule)]


def test_an_architect_s_rule_that_keeps_to_ao_s_files_is_not_named():
    assert _problems("Bash(rm agent-mail/old.md)", "Bash(rm -r .ao/inbox/done)", "Bash(git diff --stat)",
                     "Bash(mv agent-mail/a agent-mail/b)", "Bash(cp --target-directory agent-mail agent-mail/x)",
                     role="architect") == []


@pytest.mark.parametrize("rule, reason", [
    ("Bash(git commit -m fix *)", "skips the commit hook"),
    ("Bash(git commit -m:*)", "skips the commit hook"),
    ("Bash(nice git commit -am:*)", "skips the commit hook"),
    ("Bash(ao features --by Alice *)", "switches review off"),
    ("Bash(ao features off --by Alice review *)", "switches review off"),
    ("Bash(ao push --minutes 5 *)", "opens a push window"),
    ("Bash(ao config --machine set *)", "opts into same-family review"),
])
def test_a_rule_whose_wildcard_can_add_what_an_operation_needs_is_named(rule, reason):
    """The samples are asked of a rule with a wildcard as the harness matches it, and `Bash(git commit -m fix *)` admits
    none of them: its wildcard adds `--no-verify` after the message."""
    assert _named(rule) == [(reason, rule)]


def test_a_rule_whose_fixed_words_rule_an_operation_out_is_not_named():
    assert _problems("Bash(ao features on *)", "Bash(ao push status:*)", "Bash(xargs -n 1 git add)",
                     "Bash(git -c color.ui=never log *)",
                     role="implementer") == []


def test_ao_s_options_the_reading_declares_are_the_parser_s():
    parser = cli.build_parser()
    commands = next(action.choices for action in parser._actions if isinstance(action.choices, dict))

    def options(of):
        return {flag: action.nargs != 0 for action in of._actions for flag in action.option_strings}

    assert AL._ao_tables() == {name: options(command) for name, command in {None: parser, **commands}.items()}


# The independent review: unknown syntax anywhere invalidates the entire reading.
REVIEW_COMMANDS = [
    "git status $X; git commit -n -m x", "git status * $(sh -c x)",
    'git status "$X $(sh -c x)"', "git status * | sh", 'git status "*',
    "sudo -h localhost git commit -n -m x", "sudo -h localhost git commit:*",
    "sudo -h localhost python3 -c x", "env 1=1 git commit -n -m x", "env 1=1 git commit:*",
    "env a-b=1 python3 -c x", "git config includeif.gitdir:*.path /tmp/cfg",
    "git config includeif.gitdir:*", "git config rename-section tmp core",
    "git config --rename-section tmp core", "git config edit", "git config -e",
    "git config --comment -l core.hooksPath /dev/null", "git config -- core.hooksPath -l",
    "git config core.hooksPath /dev/null # -l", "</dev/null git commit -n -m x",
    "2>&1 python3 -c x", "git</dev/null commit -n -m x", "git commit</dev/null -n -m x",
    "git commit --no-verify>/dev/null -m x", "xargs -I foo -I % git % -m x",
    "git ^commit -n -m x", "git commit -^n -m x", "git commit %FLAG% -m x",
]


@pytest.mark.parametrize("command", REVIEW_COMMANDS)
def test_review_closed_every_reported_grant_is_named(command):
    assert _problems(f"Bash({command})")


@pytest.mark.parametrize("command", ["cp -ftsrc agent-mail/x", "mv -vtsrc agent-mail/x",
                                     "cp -t -dir agent-mail/x", "git format-patch -qo src HEAD~1"])
def test_review_closed_clustered_output_options_are_named(command):
    assert _problems(f"Bash({command})", role="architect")


@pytest.mark.parametrize("grant", ["Read((,Bash(git commit -{n,m} x)", "Read),Bash(git status)",
                                   "Bash(git status", "Read(("])
def test_review_closed_unbalanced_tool_lists_admit_any_command(grant):
    assert AL.problems(["claude", "--allowedTools", grant])


@pytest.mark.parametrize("prefix", ["git status ", "git status * ", "git status ? ", "git status [a] "])
@pytest.mark.parametrize("unknown", ["; echo x", "& echo x", "| cat", "(echo x)", "\necho x", "`echo x`",
                                     "$(echo x)", "$X", "%FLAG%", "{a,b}", "^x", r"arg\x", '"oops',
                                     "<<EOF", ">", "--not-an-option"])
def test_review_closed_unknown_syntax_is_never_hidden_by_a_wildcard(prefix, unknown):
    rule = f"Bash({prefix}{unknown})"
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)


@pytest.mark.parametrize("redirect", ["</dev/null", ">/tmp/out", ">>/tmp/out", "&>/tmp/out", "2>&1",
                                      "<<<text", "3<>/tmp/io", "2>|/tmp/out", "2>&-", "&>>/tmp/out"])
@pytest.mark.parametrize("command", ["{r} git commit -n -m x", "git{r} commit -n -m x",
                                     "git commit{r} -n -m x", "git commit -n -m x {r}"])
def test_review_closed_redirections_do_not_hide_command_words(redirect, command):
    assert _problems("Bash(" + command.format(r=redirect) + ")")


@pytest.mark.parametrize("command", ["cp --not-an-option agent-mail/a agent-mail/b", "mv -Z agent-mail/a agent-mail/b",
                                     "git commit --not-an-option -m x", "git config --unknown --get user.name",
                                     "git log --unknown", "npm --unknown test", "ao status --unknown"])
def test_review_closed_unknown_tool_options_admit_any_command(command):
    rule = f"Bash({command})"
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)


@pytest.mark.parametrize("command", ["git config --get user.name", "git config --list", "git config -l",
                                     "git config get core.hooksPath", "git config list --file /tmp/cfg",
                                     "git config --file /tmp/cfg --get core.hooksPath", "git config user.name Bot"])
def test_review_closed_only_proven_config_reads_and_unrelated_exact_writes_are_unnamed(command):
    assert _problems(f"Bash({command})") == []


@pytest.mark.parametrize("command", ["git config --get:*", "git config user.name:*", "git config get user.name *",
                                     "git config --get core.hooksPath --add", "git config --replace-all core.hooksPath x",
                                     "git config --file /tmp/cfg user.name Bot", "git config include.anything x",
                                     "git config includeIf.gitdir:/repo.anything x", "git config --remove-section core",
                                     "git config remove-section include", "git config --unset-all core.hooksPath"])
def test_review_closed_config_writes_cannot_masquerade_as_reads(command):
    assert any("rewrites the hook" in reason for reason, _, _ in _problems(f"Bash({command})"))


@pytest.mark.parametrize("command", [
    "</dev/null git status", "git</dev/null status", "git status>/tmp/out", "2>&1 git status",
    "git status &>>/tmp/out", "git status <<<text", 'git status "2">/tmp/out', "git status >/tmp/out 2>&1",
])
def test_no_redirection_is_read_as_a_harmless_command(command):
    assert AL._lex(command) is None
    assert AL.normalized_rule(f"Bash({command})") == "Bash(*)"
    assert _problems(f"Bash({command})")


@pytest.mark.parametrize("command", ["git status >&file", "git status <&file", "git status >*", "git status >one>two>",
                                     "git status * --unknown", "git status * -Z", "doas python3 script.py",
                                     "unknown-tool", "git unknown-subcommand", "ao unknown-subcommand", "rtk --unknown",
                                     "npm unknown-subcommand", "uv unknown-subcommand", "git status ~user",
                                     "git status --short*--unknown", "ls -l*Z"])
def test_review_closed_unproved_readings_are_any_command(command):
    rule = f"Bash({command})"
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)


@pytest.mark.parametrize("flag", ["-h", "-u", "-g", "-C", "-D", "-R", "-T", "-U", "-p", "-r", "-t"])
def test_review_closed_sudo_value_options_never_become_the_program(flag):
    assert _problems(f"Bash(sudo {flag} value python3 script.py)")


@pytest.mark.parametrize("command", ["git commit -m --no-verify", "git commit -- -n", "git commit -m x -F n"])
def test_review_closed_option_values_and_positionals_are_not_options(command):
    rule = f"Bash({command})"
    assert _named(rule) == [(CODE, rule)]  # commit hooks, never a --no-verify hidden in a value


# Independent review: fewer shell forms, one terminal prefix suffix, complete option tables.
@pytest.mark.parametrize("command", [
    "git commit -m 2&>/dev/null -n",
    "ao features --by 2&>/dev/null off review",
    "timeout -s 9&>/dev/null 1 sh",
    "git status --* --unknown -Z", "git status -*", "grep -*",
    "git log -n* --unknown", "git log --max-count* --unknown",
    "git status src/*--unknown", "git status*--unknown", "ao status*--unknown",
    "npm test*--unknown", "uv sync*--unknown", "ao status -m*--unknown",
    "ao status --window=*--unknown", "ao status -h*--unknown", "ao status -h--unknown",
])
def test_closed_grammar_review_bypasses_are_named(command):
    rule = f"Bash({command})"
    assert _problems(rule)
    assert AL.normalized_rule(rule) == "Bash(*)"


def test_closed_grammar_review_format_patch_writes_by_default():
    rule = "Bash(git format-patch HEAD~1)"
    assert _named(rule, role="architect") == [("writes a product file", rule)]
    assert _problems("Bash(git format-patch --stdout HEAD~1)", role="architect") == []
    assert _problems("Bash(git format-patch --stdout HEAD~1:*)", role="architect")


@pytest.mark.parametrize("separator", [" ", "\t", "\n", "\r\n", ",", ", \t"])
def test_closed_grammar_review_tool_lists_split_only_outside_rules(separator):
    grant = separator.join(("Bash(git status)", "Bash(git diff)"))
    assert AL.rules(["claude", "--allowedTools", grant]) == ["Bash(git status)", "Bash(git diff)"]
    assert AL.problems(["claude", "--allowedTools", grant]) == []
    assert not any("allowed tools" in problem for problem in
                   AL.reviewer_problems(["claude", "--allowedTools", separator.join(("Read", "Grep"))]))


@pytest.mark.parametrize("command", [
    "nohup --help=x git status", "nice --version=x git status", "git --no-pager=x status",
    "git status --short=x", "git status --sh=x", "ls --all=x", "ls --version=x",
    "ao status -hvalue", "ao status --help=x", "ao -hvalue status", "ao --help=x status",
])
def test_closed_grammar_review_flag_only_options_reject_attached_values(command):
    rule = f"Bash({command})"
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)


@pytest.mark.parametrize("command", [
    '"C:\\Program Files\\ao\\ao.exe" features off review --by Alice',
    'timeout 60 "C:\\Program Files\\Ruby\\bin\\ruby.exe":*',
    'nice "C:\\Program Files\\Python312\\python.exe" x.py',
    'LC_ALL="C b" git commit:*', "TERM='a b' timeout 5 git commit:*",
    "git commit -n -m 'fix the parser'", "ao decide 'redo it' --sc S1",
    'ao features off review --by "Al*"', "git commit --no-verify=x -m y",
    "! git commit:*", "ls -la*", "git status --short*", "git status --*",
    "git status src/**", "git status src/?", "git status src/[ab]", "git status src/]",
    "git status 'plain'", 'git status "plain"', "git\tstatus", "git status\n",
    "xargs -J % git commit:*",
])
def test_closed_grammar_rejects_forms_earlier_rounds_tried_to_interpret(command):
    rule = f"Bash({command})"
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert _problems(rule)


def test_closed_grammar_all_shipped_grants_and_their_rewrites_stay_unnamed():
    checked = set()
    for ident in A.shipped_adapter_ids():
        adapter = A.load_adapter(ident)
        commands = A.role_commands(adapter)
        commands["architect"] = cli._architect_argv(ident)
        for role, argv in commands.items():
            for rule in AL.rules(A.admit_rewrites(argv)[0]):
                if not rule.startswith("Bash("):
                    continue
                context = (ident, role, rule)
                assert AL._lex(AL._rule_body(rule)) is not None, context
                assert _problems(rule, role=role) == [], context
                checked.add(rule)
    assert checked


@pytest.fixture
def lifecycle_parser(monkeypatch):
    """Exercise parser growth on older integration bases as well as SLICE-LIFECYCLE."""
    build = cli.build_parser

    def parser_with_slice(*args, **kwargs):
        parser = build(*args, **kwargs)
        sub = next(action for action in parser._actions if isinstance(action.choices, dict))
        if "slice" not in sub.choices:
            command = sub.add_parser("slice")
            command.add_argument("action", nargs="?", choices=["start", "block", "abandon", "status"])
            command.add_argument("text", nargs="?")
            for flag in ("--slice", "--why", "--waiting", "--boundary-file"):
                command.add_argument(flag)
        return parser

    monkeypatch.setattr(cli, "build_parser", parser_with_slice)
    monkeypatch.delattr(AL._ao_options, "tables", raising=False)
    tables = getattr(AL, "_ao_tables", None)
    if tables is not None:
        tables.cache_clear()
    yield
    if tables is not None:
        tables.cache_clear()


@pytest.mark.parametrize("command", [
    "ao slice abandon --why late", "ao -C . slice abandon --why late",
    "ao slice abandon --why duplicate", "ao slice --slice S1 abandon --why w",
    "ao slice --slice S1:*", "ao slice abandon *",
])
def test_review_four_slice_abandon_is_an_architect_decision(lifecycle_parser, command):
    rule = f"Bash({command})"
    assert any(reason == "abandons a slice, which the architect decides" and granted == rule
               for reason, _, granted in _problems(rule, role="implementer"))
    assert _problems(rule, role="architect") == []


@pytest.mark.parametrize("command", ["./pytest -q", "scripts/git status", "C:git.exe status", "git:sh status",
                                     "/usr/bin/git status", "nice scripts/git status", "rtk ./pytest -q",
                                     "ao lock -- scripts/git status", "scripts/nice git status"])
def test_review_four_program_files_are_code_not_bare_tools(command):
    rule = f"Bash({command})"
    assert any("code" in reason and granted == rule for reason, _, granted in _problems(rule))


@pytest.mark.parametrize("command", ["uv tool install .", "uv tool unknown-subcommand"])
def test_review_four_unknown_uv_tool_commands_fail_closed(command):
    rule = f"Bash({command})"
    assert _problems(rule)
    assert AL.normalized_rule(rule) == "Bash(*)"


@pytest.mark.parametrize("command", ["npm --prefix run test", "npm --prefix web test:*",
                                     "npm --workspace run test", "uv --project run sync"])
def test_review_four_option_values_are_not_script_subcommands(command):
    rule = f"Bash({command})"
    assert AL.normalized_rule(rule) == "Bash(*)"  # directory selection is now independently forbidden
    assert _problems(rule)


@pytest.mark.parametrize("options", ["-c core.hooksPath=/dev/null", "--config-env=core.hooksPath=HOOKS",
                                     "--config-env core.hooksPath=HOOKS"])
def test_review_four_normalization_keeps_git_configuration(options):
    command = f"git {options} commit -m x"
    assert AL.unwrap(command.split()) == command.split()
    rule = f"Bash({command})"
    assert (AL.normalized_rule(rule) or rule) == rule
    assert AL.normalized_rule(f"Bash(nice {command})") == rule
    assert _problems(rule)


@pytest.mark.parametrize("separator", [" ", "\t", "\n", ","])
def test_review_four_one_splitter_for_auditing_and_rewrites(separator):
    grant = separator.join(("Bash(git status:*)", "Bash(git diff:*)"))
    argv = ["claude", "--allowedTools", grant]
    rewritten, added = A.admit_rewrites(argv)
    assert added == ["Bash(rtk git status:*)", "Bash(rtk git diff:*)"]
    assert AL.rules(rewritten) == ["Bash(git status:*)", "Bash(rtk git status:*)",
                                    "Bash(git diff:*)", "Bash(rtk git diff:*)"]
    assert A.admit_rewrites(rewritten) == (rewritten, [])


def test_review_four_trusted_and_pinned_lists_use_the_same_separators():
    assert AL.trusted_tools(["kiro-cli", "--trust-tools", "read shell"]) == ["read", "shell"]
    assert AL.problems(["kiro-cli", "--trust-tools", "read shell"])
    assert A.pin_conflicts(["claude", "--tools", "Read Grep"], "reviewer") == []


@pytest.mark.parametrize("command", ["uv tool run ruff", "uv --project run tool run ruff",
                                     "npm --prefix test run build", "npm --prefix web run:*", "uv tool:*"])
def test_review_four_parsed_script_subcommands_still_name_code(command):
    assert _problems(f"Bash({command})")


def test_review_four_configuration_survives_wrappers_and_multiple_settings():
    command = "git -c color.ui=never --config-env core.hooksPath=HOOKS -c user.name=Bot commit -m x"
    assert AL.unwrap(("nice " + command).split()) == command.split()
    assert AL.normalized_rule(f"Bash(nice {command})") == f"Bash({command})"
    assert any("redirects the commit hook" in reason for reason, _, _ in _problems(f"Bash({command})"))


def test_review_four_rewrites_cover_variadic_values_but_never_the_prompt():
    prompt = "Review Bash(git diff:*) and explain it."
    argv = ["claude", "--allowedTools=Read", "Bash(git status:*) Bash(git diff:*)", prompt]
    rewritten, added = A.admit_rewrites(argv, prompt=prompt)
    assert added == ["Bash(rtk git status:*)", "Bash(rtk git diff:*)"]
    assert rewritten[-1] == prompt
    assert AL.rules(rewritten, prompt=prompt) == ["Read", "Bash(git status:*)", "Bash(rtk git status:*)",
                                                 "Bash(git diff:*)", "Bash(rtk git diff:*)"]
    assert A.admit_rewrites(rewritten, prompt=prompt) == (rewritten, [])


def test_review_four_architect_grant_comparison_uses_the_shared_splitter(monkeypatch):
    adapter = {"options": {"architect_tools": "Read Bash(git status:*)", "allowed_tools": ["--allowedTools"]}}
    monkeypatch.setattr(A, "load_adapter", lambda ident: adapter)
    cfg = {"architect": {"adapter": "example", "argv": ["claude", "--allowedTools", "Read,Bash(git status:*)"]}}
    assert cli._architect_grant_gaps(cfg) == []


@pytest.mark.parametrize("command", ["find . -name -exec", "pytest -k -p"])
def test_review_four_option_values_are_not_code_loading_options(command):
    parsed = AL._tool_options(command.split())
    assert not {option for option, _ in parsed[1]} & {"-exec", "-p"}
    rule = f"Bash({command})"
    assert _named(rule) == ([(CODE, rule)] if command.startswith("pytest") else [])


@pytest.mark.parametrize("grant", [
    "Read(a,Bash(git commit -n -m x),b)", "Read(a)Bash(git commit -n -m x)",
    "Read(a,b)", "Read(a(b)c)", "Read(Bash(git status))", "Read(a)Grep",
    "Read(a)(b)", "Read(a))", "Read(a", "Read)a(", "(a)",
    "Read(a)tail", "Read(foo Bash(git commit -n -m x))", "Read(a),Grep(b,c)",
])
@pytest.mark.parametrize("flag", ["--allowedTools", "--allowed-tools"])
def test_ambiguous_tool_lists_cannot_hide_a_second_rule(grant, flag):
    argv = ["claude", flag, grant]
    assert AL.problems(argv, role="implementer")
    assert AL.problems(argv, role="architect")
    assert AL.reviewer_problems(argv)
    assert A.split_tool_list(grant) == ["Bash(*)"]
    assert AL.rules(argv) == ["Bash(*)"]
    assert A.turn_refusal("implementer", "claude-code", argv)
    rewritten, _ = A.admit_rewrites(argv)
    assert AL.problems(rewritten, role="implementer")
    assert A.pin_conflicts(["claude", "--tools", grant], "reviewer")


@pytest.mark.parametrize("separator", [",", " ", "\t", "\n", ", \n"])
def test_unambiguous_tool_lists_have_one_shared_reading(separator):
    tools = ["Read(src/file.py)", "Grep", "Bash(git status:*)"]
    grant = separator.join(tools + [tools[0]])
    assert A.split_tool_list(grant) == tools
    assert AL.rules(["claude", "--allowedTools", grant]) == tools
    assert AL.problems(["claude", "--allowedTools", grant], role="implementer") == []


def test_no_shipped_tool_list_contains_a_comma_in_a_rule_body():
    def check(value, context):
        depth = 0
        for char in value:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == ",":
                assert not depth, (context, value)
    for ident in A.shipped_adapter_ids():
        adapter = A.load_adapter(ident)
        for name, value in adapter.get("options", {}).items():
            if name.endswith("_tools") and isinstance(value, str):
                check(value, (ident, name))
        for role, argv in A.role_commands(adapter).items():
            active = False
            for argument in map(str, argv):
                flag, equals, value = argument.partition("=")
                if argument.startswith("-"):
                    active = flag in A.GRANT_FLAGS + ("--tools", "--trust-tools")
                    if active and equals:
                        check(value, (ident, role))
                elif active:
                    check(argument, (ident, role))
