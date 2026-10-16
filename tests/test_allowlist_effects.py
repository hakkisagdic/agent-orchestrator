"""Landing review: parseable options must also have classified effects."""
import pytest

from ao import allowlist as AL, lib as A


def problems(command, role="architect"):
    return AL.problems(["claude", "--allowedTools", f"Bash({command})"], role=role)


@pytest.mark.parametrize("command", [
    "env -C src rm agent-mail/x", "env --chdir=.. rm -rf agent-mail",
    "sudo -D src tee .ao/board.md", "env -Csrc rm agent-mail/x",
    "env -iCsrc rm agent-mail/x", "env --ch=src rm agent-mail/x",
    "genv --chdir src cp agent-mail/a agent-mail/b", "sudo --chdir=src tee .ao/board.md",
    "sudo -nDsrc rm agent-mail/x", "sudo --chd src rm agent-mail/x",
    "sudo -i rm agent-mail/x", "sudo --login rm agent-mail/x",
    "sudo -R src rm agent-mail/x", "sudo --chroot=src rm agent-mail/x",
    "git -C src status", "git -C .. diff --output=agent-mail/x",
    "git -C . -C src status", "git --work-tree=src status", "git --git-dir=other status",
    "ao -C src status", "ao --root=src status", "ao --roo src status",
    "npm --prefix=src test", "npm --prefix src test", "npm -C src test",
    "npm --workspace=src test", "npm --workspaces test",
    "uv --directory=src sync", "uv --project src sync",
    "make -C src", "gmake --directory=src", "tar -C src -xf archive",
    "nice env -C src rm agent-mail/x", "rtk git -C src status",
    "ao lock -- env -C src tee .ao/board.md",
])
def test_directory_selection_never_reuses_repository_relative_paths(command):
    assert problems(command)
    assert problems(command, "implementer")
    assert A.turn_refusal("architect", "claude-code", ["claude", "--allowedTools", f"Bash({command})"])


@pytest.mark.parametrize("prefix", ["env -C .", "genv --chdir=.", "sudo -D .", "sudo --chdir=."])
def test_literal_dot_is_the_only_proven_directory_noop(prefix):
    assert not problems(f"{prefix} rm agent-mail/x")
    assert problems(f"{prefix} rm src/app.py")
    assert problems(f"{prefix} env -C src rm agent-mail/x")


@pytest.mark.parametrize("command", [
    "git archive --remote=. --exec=sh HEAD", "git archive --exec sh HEAD",
    "git archive --ex=sh HEAD", "git archive:*", "git archive HEAD*",
    "git fetch --upload-pack=sh origin", "git clone --upload-pack=sh origin dst",
    "git ls-remote --upload-pack=sh origin", "git push --receive-pack=sh origin",
    "git -c core.editor=sh status", "git --config-env=diff.external=PROGRAM diff",
    "git clone --config=core.sshCommand=sh origin dst",
    "git config core.editor sh", "git config filter.clean.clean sh",
    "git config diff.external sh", "git config --global core.fsmonitor sh",
    "git commit -m x", "git commit --no-edit -m x", "git show HEAD", "git diff-files",
    "git diff-index HEAD", "git diff-tree HEAD", "git range-diff a b", "git whatchanged",
    "git stash", "git stash push", "git stash pop", "git stash apply", "git stash show",
    "git stash drop", "git stash clear", "git stash store HEAD", "git stash list -p",
    "sudo -A git status", "sudo --askpass git status", "sudo -s git status",
    "sudo --shell git status", "env -L user git status", "env -U user git status",
    "pytest -q", "py.test -c settings.ini", "pytest -o addopts=-pplugin",
    "npm test", "uv sync",
])
def test_commands_and_options_that_select_or_run_code_are_named_for_both_roles(command):
    assert problems(command, "implementer")
    assert problems(command, "architect")


@pytest.mark.parametrize("command", [
    "git stash", "git stash push", "git stash pop", "git stash apply", "git stash drop",
    "git stash clear", "git stash store HEAD", "git stash branch work",
    "git checkout branch", "git switch branch", "git restore file", "git reset --hard",
    "git apply patch", "git am patch", "git merge branch", "git rebase branch",
    "git pull", "git cherry-pick HEAD", "git revert HEAD", "git clean -fd", "git rm file",
    "git mv old new", "git read-tree -u HEAD", "git worktree add dir",
    "git format-patch HEAD~1", "git archive -o src/out HEAD", "git archive --output=src/out HEAD",
    "git bundle create src/out HEAD", "git config user.name Bot", "git config --file src/config user.name Bot",
    "git add src/file", "git diff --output=src/file", "git log --output=src/file",
])
def test_git_writes_are_never_an_architect_grant(command):
    assert problems(command)


@pytest.mark.parametrize("command", [
    "time -o src/out git status", "time --output=src/out git status",
    "gtime -ao src/out git status", "nice time -o src/out command -v git",
    "nohup git status", "gnohup git status",
    "cp --suffix=../../src/out --backup agent-mail/a agent-mail/b",
    "mv -S../../src/out -b agent-mail/a agent-mail/b",
])
def test_wrapper_and_tool_writes_survive_unwrapping_and_are_role_specific(command):
    assert problems(command)
    assert not problems(command, "implementer")


@pytest.mark.parametrize("program", ["cp", "mv"])
@pytest.mark.parametrize("option", ["-b", "--backup=numbered", "-S.bak", "--suffix=.bak"])
def test_backup_options_can_write_beside_an_exact_coordination_file(program, option):
    command = f"{program} {option} agent-mail/a .ao/board.md"
    assert problems(command)
    assert not problems(command, "implementer")


@pytest.mark.parametrize("command", ["env -a sudoedit sudo git status", "genv --argv0=sudoedit sudo git status",
                                     "exec -a sudoedit sudo git status", "env --argv0=other git status"])
def test_an_overridden_invocation_name_is_not_the_program_the_reading_knows(command):
    assert AL.normalized_rule(f"Bash({command})") == "Bash(*)"
    assert problems(command, "implementer")


def test_output_option_values_and_similar_spellings_are_not_other_options():
    assert not problems("time -o agent-mail/timing git status")
    assert not problems("stdbuf -oL -e0 git status")
    assert not problems("sudo -C 3 git status")  # closes descriptors, not chdir
    assert not problems("git diff -C HEAD", "implementer")  # copy detection
    assert not problems("git archive HEAD --output=--exec", "implementer")
    assert not problems("git -C . status")
    assert not problems("ao -C . status")


@pytest.mark.parametrize("kind, name", [
    (kind, name) for kind, tables in (("wrapper", AL.WRAPPERS), ("tool", AL.TOOL_OPTIONS), ("git", AL.GIT_TOOL_OPTIONS))
    for name in tables
])
def test_adding_option_syntax_without_a_classification_cannot_grant_it(monkeypatch, kind, name):
    tables = {"wrapper": AL.WRAPPERS, "tool": AL.TOOL_OPTIONS, "git": AL.GIT_TOOL_OPTIONS}[kind]
    short, long = tables[name]
    monkeypatch.setitem(tables, name, (short, long + ("unclassified",)))
    command = f"git {name}" if kind == "git" else name
    command += " --unclassified" + (" git status" if kind == "wrapper" else "")
    assert AL.normalized_rule(f"Bash({command})") == "Bash(*)"
    assert problems(command, "implementer")


def test_new_global_options_subcommands_and_tools_need_an_effect_policy(monkeypatch):
    monkeypatch.setitem(AL.GIT_OPTIONS, "--unclassified", False)
    monkeypatch.setitem(AL.GIT_TOOL_OPTIONS, "unclassified", ("", ()))
    monkeypatch.setitem(AL.TOOL_OPTIONS, "unclassified", ("", ()))
    for command in ("git --unclassified status", "git unclassified", "unclassified"):
        assert AL.normalized_rule(f"Bash({command})") == "Bash(*)"
        assert problems(command)


@pytest.mark.parametrize("kind, name, option", [
    (kind, name, option) for (kind, name), options in AL.DIRECTORY_OPTIONS.items() for option in options
])
@pytest.mark.parametrize("attached", [False, True])
def test_every_declared_directory_option_is_closed_in_each_value_form(kind, name, option, attached):
    if kind == "global":
        takes_value = AL.GIT_OPTIONS[option]
    else:
        short, long = (AL.WRAPPERS if kind == "wrapper" else AL.TOOL_OPTIONS)[name]
        takes_value = AL._letters(short).get(option[1:], 0) if not option.startswith("--") else \
            any(word.rstrip("=?") == option[2:] and word.endswith(("=", "=?")) for word in long)
    value = ("=" if option.startswith("--") else "") + "src" if attached else " src"
    argument = option + (value if takes_value else "")
    after = "git status" if kind == "wrapper" else "status" if name == "git" else "test" if name == "npm" else "sync"
    command = f"{name} {argument} {after}"
    assert AL.normalized_rule(f"Bash({command})") == "Bash(*)"
    assert problems(command)


def test_every_admitted_git_subcommand_has_an_effect_class():
    assert set(AL.GIT_EFFECTS) == set(AL.GIT_TOOL_OPTIONS)
    for kind, tables in (("wrapper", AL.WRAPPERS), ("tool", AL.TOOL_OPTIONS), ("git", AL.GIT_TOOL_OPTIONS)):
        for name, grammar in tables.items():
            assert AL._option_names(grammar) <= AL.OPTION_EFFECTS[kind, name].keys()
    assert AL.GIT_OPTIONS.keys() <= AL.OPTION_EFFECTS["global", "git"].keys()


@pytest.mark.parametrize("command", ["git config --get core.editor",
                                     "git config --file src/config --list", "git archive --format=tar HEAD",
                                     "git archive --output=src/out.tar HEAD", "git --version"])
def test_reading_configuration_and_plain_archive_output_do_not_run_a_selected_program(command):
    assert not problems(command, "implementer")


@pytest.mark.parametrize("command", ["git archive --format=custom HEAD", "git archive -o out.tar.gz HEAD",
                                     "git -p status", "git --paginate status", "git --attr-source=HEAD status",
                                     "git --shallow-file=src/file status"])
def test_program_and_file_selection_through_git_global_or_archive_options_is_named(command):
    assert problems(command, "implementer")


@pytest.mark.parametrize("command", [
    "git archive*", "git stash*", "git show*", "git diff-files*", "git diff-index*",
    "git diff-tree*", "git range-diff*", "git whatchanged*", "git diff*", "git status*",
    "ao st*", "ls -n*", "git status src/file*", "nice git status*",
])
def test_attached_wildcards_are_unknown_commands(command):
    rule = f"Bash({command})"
    assert problems(command, "implementer")
    assert problems(command, "architect")
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert A.turn_refusal("implementer", "claude-code", ["claude", "--allowedTools", rule])


@pytest.mark.parametrize("program", ["xargs", "gxargs"])
@pytest.mark.parametrize("arguments", [
    "-I tatus git status", "-I tatus_x git status*", "-I dd git add", "-I atus ao status",
    "-I token git status", "-Itoken git status", "-i git status", "-itoken git status",
    "--replace git status", "--replace=token git status", "--rep=token git status",
    "-J token git status", "-Jtoken git status", "-rItoken git status",
    "-I", "-J", "-i", "--replace", "-I token -iother git status", "-I token command -v git",
])
def test_every_xargs_replacement_mode_is_unknown_even_without_a_match(program, arguments):
    command = f"{program} {arguments}"
    rule = f"Bash({command})"
    assert problems(command, "implementer")
    assert problems(command, "architect")
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert A.turn_refusal("implementer", "claude-code", ["claude", "--allowedTools", rule])


@pytest.mark.parametrize("command, changed", [
    ("nice git status", ["nice", "git", "unclassified"]),
    ("nice ao status", ["nice", "ao", "unclassified"]),
    ("nice ls -a", ["nice", "ls", "--unclassified"]),
])
def test_validation_uses_the_words_left_by_a_wrapper(monkeypatch, command, changed):
    monkeypatch.setattr(AL, "_wrapper", lambda name, words, at, tail, rest, effects=None:
                        (at, changed, tail, rest))
    assert AL._reading(command) is None
    assert problems(command, "implementer")


@pytest.mark.parametrize("program", ["xargs", "gxargs"])
@pytest.mark.parametrize("command, named", [
    ("git status", False), ("git add", False), ("ao push status", False),
    ("git archive", True), ("git config --get user.name", True),
    ("ao features", True), ("ao push", True), ("git", True), ("ls --unclassified", True),
])
def test_xargs_without_replacement_is_judged_as_appending_arguments(program, command, named):
    rule = f"Bash({program} -n1 {command})"
    assert AL._rule_reading(rule) == AL._rule_reading(f"Bash({command} *)")
    assert bool(problems(f"{program} -n1 {command}", "implementer")) == named


@pytest.mark.parametrize("command", [
    "ao -Csrc=. status", "ao -C/tmp/other=. verify:*",
    "ao -Csrc=. lock -- git status", "nice ao -Csrc=. status",
    "ao -Csrc status", "ao -C=x status", "ao -C. status", "ao -C=. status",
    "ao --root=x status", "ao --root=. status", "ao --root . status",
    "ao --roo=. status", "ao --roo . status", "ao -C src=. status",
    "ao -C . -Csrc=. status", "ao -C . --root=. status",
    "ao -C ./ status", "ao -C .. status", "ao -C =. status",
    "ao backup --to .", "ao backup --to=. ", "ao backup --to=src=.",
    "ao content verify --from .", "ao content verify --from=src=.",
    "ao content verify --fro=.",
])
def test_ao_directory_options_only_admit_the_separate_C_dot_pair(command):
    rule = f"Bash({command})"
    assert problems(command, "implementer")
    assert problems(command, "architect")
    assert AL.normalized_rule(rule) == "Bash(*)"
    assert A.turn_refusal("implementer", "claude-code", ["claude", "--allowedTools", rule])


@pytest.mark.parametrize("argument, root", [
    ("-Csrc=.", "src=."), ("-C/tmp/other=.", "/tmp/other=."),
    ("-C=.", "."), ("--root=src=.", "src=."),
])
def test_ao_argparse_preserves_equals_inside_an_attached_root(argument, root):
    from ao import cli
    assert cli.build_parser().parse_args([argument, "status"]).root == root


@pytest.mark.parametrize("command", [
    "ao -C . status", "ao -C . -C . verify:*", "nice ao -C . lock -- git status",
    "ao features on review --by=Alice=.", "ao note text --to=Alice=.",
])
def test_ao_literal_root_noop_and_non_directory_values_keep_their_meaning(command):
    assert not problems(command, "implementer")
    assert not problems(command, "architect")


def test_all_getopt_table_values_preserve_embedded_equals():
    for tables in (AL.WRAPPERS, AL.TOOL_OPTIONS, AL.GIT_TOOL_OPTIONS):
        for name, (short, long) in tables.items():
            options = [("-" + letter, kind == 1) for letter, kind in AL._letters(short).items() if kind]
            options += [("--" + option.rstrip("=?"), option.endswith("="))
                        for option in long if option.endswith(("=", "=?"))]
            for option, required in options:
                for value in ("src=.", "=.", "src=other=."):
                    attached = option + ("=" if option.startswith("--") else "") + value
                    parsed = AL._options([attached], short, long)
                    assert parsed is not None and parsed[1] == [(option, value)], (name, attached, parsed)
                    if required:
                        parsed = AL._options([option, value], short, long)
                        assert parsed is not None and parsed[1] == [(option, value)], (name, option, value, parsed)


@pytest.mark.parametrize("command", [
    "env -Csrc=. git status", "env --chdir=src=. git status", "sudo -Dsrc=. git status",
    "sudo --chdir=src=. git status", "git --work-tree=src=. status", "git -C src=. status",
    "cp -tagent-mail=. agent-mail/a", "cp --target-directory=agent-mail=. agent-mail/a",
    "time -oagent-mail=. git status", "time --output=agent-mail=. git status",
])
def test_equals_cannot_disguise_another_tools_directory_or_output_path(command):
    assert problems(command, "architect")


@pytest.mark.parametrize("command", ["ao backup:*", "ao content verify:*", "xargs ao backup",
                                     "nice ao content verify *"])
def test_ao_prefix_cannot_append_a_directory_option(command):
    assert problems(command, "implementer")
    assert problems(command, "architect")
    assert AL.normalized_rule(f"Bash({command})") == "Bash(*)"


def test_option_tables_never_declare_an_option_twice():
    tables = {**{("wrapper", name): value for name, value in AL.WRAPPERS.items()},
              **{("tool", name): value for name, value in AL.TOOL_OPTIONS.items()},
              **{("git", name): value for name, value in AL.GIT_TOOL_OPTIONS.items()},
              **{("base", name): getattr(AL, name) for name in
                 ("GIT_CONFIG_OPTIONS", "GIT_DIFF_OPTIONS", "GIT_LOG_OPTIONS")}}
    for name, (short, long) in tables.items():
        letters = short.replace(":", "")
        options = [option.rstrip("=?") for option in long]
        assert len(letters) == len(set(letters)), (name, short)
        assert len(options) == len(set(options)), (name, long)
    for name, inventory in AL.INERT_OPTIONS.items():
        assert len(inventory.split()) == len(set(inventory.split())), name


@pytest.mark.parametrize("command", ["log", "show", "format-patch", "whatchanged", "stash"])
def test_git_reverse_is_parsed_once_and_judged_by_its_command(command):
    rule = f"Bash(git {command} --reverse:*)"
    assert AL._tool_options(["git", command, "--reverse"]) is not None
    assert AL.normalized_rule(rule) is None
    found = problems(f"git {command} --reverse:*", "implementer")
    if command == "log":
        assert found == []
        assert not problems("git log --reverse")
    else:
        assert all("skips the commit hook" not in reason for reason, _, _ in found)
