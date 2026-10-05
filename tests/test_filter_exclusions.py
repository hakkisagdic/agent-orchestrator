"""A filter in front of an agent's shell is asked, not trusted, and asked only when that is safe (#52).

Each fake hook is a Python script run as `[sys.executable, script]` - the exec form a
settings file declares with `command` and `args` - so it runs the same way on Windows.
"""
import json
import os
import sys
import time

from ao import cli, lib as A

READS_PAYLOAD = "import json, os, sys\npayload = json.load(sys.stdin)\ncommand = payload['tool_input']['command']\n"


def _hook(tmp_path, name, body):
    """A command hook in exec form whose script lies outside the project."""
    directory = tmp_path / "hooks"
    directory.mkdir(exist_ok=True)
    script = directory / f"{name}.py"
    script.write_text(READS_PAYLOAD + body, encoding="utf-8")
    return {"type": "command", "command": sys.executable, "args": [str(script)]}


def _settings(base, *entries):
    """base/.claude/settings.json with these PreToolUse entries; a bare hook is matched to the shell tool."""
    os.makedirs(os.path.join(base, ".claude"), exist_ok=True)
    path = os.path.join(base, ".claude", "settings.json")
    entries = [entry if "hooks" in entry else {"matcher": "Bash", "hooks": [entry]} for entry in entries]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"hooks": {"PreToolUse": entries}}, fh)
    return path


def _machine(programs, timeout=30):
    """The machine settings a person writes: which programs ao may ask, and for how long."""
    with open(os.environ["AO_SETTINGS"], "w", encoding="utf-8") as fh:
        json.dump({"filters": {"probe_programs": programs, "probe_timeout_seconds": timeout}}, fh)


def test_a_hook_that_passes_every_measurement_through_has_its_exclusions_in_force(project, tmp_path, monkeypatch):
    root = project["root"]
    with open(os.path.join(root, ".ao", "gates.json"), "w", encoding="utf-8") as fh:
        json.dump({"gates": {"test": {"run": "python3 -m pytest -q"}}, "profiles": {"quick": ["test"]}}, fh)
    seen = tmp_path / "seen.jsonl"
    monkeypatch.setenv("AO_PROBE_TEST_TOKEN", "never-for-a-hook")
    records = (f"with open({str(seen)!r}, 'a', encoding='utf-8') as fh:\n"
               "    fh.write(json.dumps({'payload': payload, 'env': sorted(os.environ), 'cwd': os.getcwd()}) + '\\n')\n")
    rewrites_edits = {"matcher": "Edit|Write", "hooks": [
        _hook(tmp_path, "edits", "print(json.dumps({'hookSpecificOutput': {'updatedInput': {'command': 'x'}}}))\n")]}
    _settings(A.HOME, _hook(tmp_path, "passes", records), rewrites_edits)
    _machine([os.path.basename(sys.executable)])

    [result] = A.probe_filters(root)

    assert result["verdict"] == "in-force" and result["changed"] == [] and result["why"] is None
    asked = [json.loads(line) for line in seen.read_text(encoding="utf-8").splitlines()]
    assert [row["payload"]["tool_input"]["command"] for row in asked] == [*A.MEASUREMENT_COMMANDS,
                                                                          "python3 -m pytest -q"]
    first = asked[0]
    assert (first["payload"]["hook_event_name"], first["payload"]["tool_name"]) == ("PreToolUse", "Bash")
    assert first["payload"]["cwd"] == os.path.abspath(root)
    assert os.path.realpath(first["cwd"]) == os.path.realpath(root)
    project_dir = A.load_adapter("claude-code")["directives"]["command_hooks"]["project_dir_env"]
    assert project_dir in first["env"] and "AO_PROBE_TEST_TOKEN" not in first["env"]
    monkeypatch.setattr(A, "probe_filters", lambda root: [result])
    text = "\n".join(cli._measurement_lines(project))
    assert f"passes all {len(asked)} measurement commands through unchanged: its exclusions are in force" in text


def test_a_hook_that_rewrites_git_diff_is_a_doctor_problem_naming_the_command_and_its_rewritten_form(project,
                                                                                                    tmp_path):
    root = project["root"]
    answers = ("if command.startswith('git diff'):\n"
               "    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',\n"
               "        'permissionDecisionReason': 'auto-rewrite',\n"
               "        'updatedInput': dict(payload['tool_input'], command='rtk ' + command)}}))\n"
               "elif command == 'git log':\n"
               "    sys.stderr.write('read the short log instead\\n')\n"
               "    sys.exit(2)\n")
    _settings(A.HOME, _hook(tmp_path, "filter", answers))
    _machine([os.path.basename(sys.executable)])

    [result] = A.probe_filters(root)

    assert result["verdict"] == "filters" and result["why"] is None
    assert result["asked"] == len(A.MEASUREMENT_COMMANDS)
    assert ("git diff", "rewrites", "rtk git diff") in result["changed"]
    assert ("git diff --cached --numstat", "rewrites", "rtk git diff --cached --numstat") in result["changed"]
    assert ("git log", "blocks", "is blocked (read the short log instead)") in result["changed"]
    assert not [command for command, _, _ in result["changed"] if not command.startswith(("git diff", "git log"))]
    problems = dict(cli.doctor_problems(project))
    [key] = [key for key in problems if key.startswith("measurement-filter:")]
    assert "`git diff` becomes `rtk git diff`" in problems[key]
    assert "`git log` is blocked" in problems[key] and "`git status`" not in problems[key]


def test_a_hook_that_hangs_is_cut_off_by_the_timeout_and_reported(project, tmp_path):
    root = project["root"]
    _settings(A.HOME, _hook(tmp_path, "hangs", "import time\ntime.sleep(120)\n"))
    _machine([os.path.basename(sys.executable)], timeout=1)

    started = time.monotonic()
    [result] = A.probe_filters(root)

    assert time.monotonic() - started < 30
    assert result["verdict"] == "unverified" and result["asked"] == 1 and result["changed"] == []
    assert result["why"] == "it did not answer within 1s when asked about `git diff`"
    assert "is not verified: it did not answer within 1s" in A.filter_probe_text(result)


def test_no_hook_runs_unless_the_agents_ao_governs_can_neither_write_it_nor_choose_its_program(project, tmp_path):
    root = project["root"]
    ran = tmp_path / "ran"
    marks = f"open({str(ran)!r}, 'a', encoding='utf-8').write(command + '\\n')\n"
    _machine([os.path.basename(sys.executable)])

    # A project's own settings: its agents can write them, whatever program they name.
    project_settings = _settings(root, _hook(tmp_path, "project-level", marks))
    [result] = A.probe_filters(root)
    assert not ran.exists()
    assert (result["verdict"], result["asked"], result["user"]) == ("unverified", 0, False)
    assert "the project's own settings" in result["why"]
    os.remove(project_settings)

    # A user-level hook whose script the agents can write.
    inside = os.path.join(root, "hook.py")
    with open(inside, "w", encoding="utf-8") as fh:
        fh.write(READS_PAYLOAD + marks)
    _settings(A.HOME, {"type": "command", "command": sys.executable, "args": [inside]})
    [result] = A.probe_filters(root)
    assert not ran.exists() and result["asked"] == 0 and "lies inside the project" in result["why"]

    # A user-level filter whose program the machine does not allow ao to run.
    _machine([])
    _settings(A.HOME, _hook(tmp_path, "compress", marks))
    [result] = A.probe_filters(root)
    assert not ran.exists() and result["asked"] == 0 and "is not in filters.probe_programs" in result["why"]

    # A command only a shell could run.
    _machine(["rtk"])
    _settings(A.HOME, {"type": "command", "command": f"rtk hook $(python3 -c 'open(\"{ran.name}\", \"w\")')"})
    [result] = A.probe_filters(root)
    assert not ran.exists() and not os.path.exists(os.path.join(root, ran.name))
    assert result["asked"] == 0 and "needs a shell" in result["why"]


def test_a_hook_argument_naming_a_project_path_not_there_yet_is_refused(project, tmp_path):
    """FILTER-EXCLUSIONS-2: a relative argument counted as a path only if it existed, so an agent could write
    `.ao/filter.toml` after the check and have the hook read it, once per command measured."""
    root = project["root"]
    ran = tmp_path / "ran"
    marks = f"open({str(ran)!r}, 'a', encoding='utf-8').write(command + '\\n')\n"
    _machine([os.path.basename(sys.executable)])
    for word in ("--config=.ao/filter.toml", "filter.toml"):
        hook = _hook(tmp_path, "config", marks)
        hook["args"].append(word)
        _settings(A.HOME, hook)

        [result] = A.probe_filters(root)

        assert not ran.exists() and result["asked"] == 0 and "lies inside the project" in result["why"], word
    allowed = [A._program_name(sys.executable)]                  # python.exe is python, as the setting reads it
    assert A._probe_program([sys.executable, "--level=1.5", "v2", "--endpoint=http://localhost:8080/rules"], root,
                            allowed, os.environ.get("PATH", ""))[1] is None
    assert "lies inside the project" in A._probe_program([sys.executable, "file://" + root + "/.ao/rules.toml"], root,
                                                         allowed, os.environ.get("PATH", ""))[1]


def test_a_file_address_is_judged_by_the_path_it_decodes_to(project):
    """FILTER-EXCLUSIONS-3: the text after file:// was judged as it stood, so an encoded project path passed, and on
    Windows so did any file:///C:/ address."""
    import pathlib
    root = project["root"]
    allowed = [A._program_name(sys.executable)]
    uri = pathlib.Path(root, ".ao", "filter.toml").as_uri()
    encoded = "file:///" + uri[len("file:///"):].replace("/", "%2F")

    for word in (uri, encoded, "--config=" + encoded):
        why = A._probe_program([sys.executable, word], root, allowed, os.environ.get("PATH", ""))[1]
        assert why and "lies inside the project" in why, word


def _on_windows(monkeypatch):
    """ao's path rules as Windows has them, on every platform: ntpath, and the file-address decoder Windows uses."""
    import ntpath
    import shutil
    import urllib.request
    import warnings
    from tests.test_windows_followups import _Instead
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import nturl2path               # urllib.request's url2pathname on Windows; deprecated from 3.14, still there
    monkeypatch.setattr(A, "os", _Instead(os, name="nt", sep="\\", altsep="/", path=ntpath))
    monkeypatch.setattr(urllib.request, "url2pathname", nturl2path.url2pathname)
    monkeypatch.setattr(shutil, "which", lambda name, mode=None, path=None: "C:\\tools\\rtk.exe")


def test_a_file_address_whose_drive_colon_is_encoded_names_that_drive(monkeypatch):
    """FILTER-EXCLUSIONS-4: url2pathname looks for a drive before it decodes, so `/C%3A/x` was `\\C:\\x` to ao,
    outside C:\\repo, and C:\\x to Node's fileURLToPath and Rust's url, which decode first."""
    _on_windows(monkeypatch)
    for word in ("file:///C%3A/repo/.ao/filter.toml", "file:///c%3a/repo/.ao/filter.toml",
                 "file:///%43%3A/repo/.ao/filter.toml", "--config=file:///C%3A/repo/.ao/filter.toml",
                 "file://localhost/C%3A/repo/.ao/filter.toml"):
        why = A._probe_program(["rtk", word], "C:\\repo", {"rtk"}, "")[1]
        assert why and "lies inside the project" in why, word
    assert A._probe_program(["rtk", "file:///C%3A/elsewhere/filter.toml"], "C:\\repo", {"rtk"}, "")[1] is None


def test_a_file_address_holding_a_nul_is_refused(project):
    """FILTER-EXCLUSIONS-4: a decoded NUL raised out of realpath (POSIX, Python 3.10 on) and stopped the whole
    probe; where realpath let it through, `filter.toml%00/../../..` was judged above the project, though a
    program that ends the path at the NUL opens the project's filter.toml."""
    import pathlib
    root = project["root"]
    allowed = [A._program_name(sys.executable)]
    uri = pathlib.Path(root, ".ao", "filter.toml").as_uri()

    for word in (uri.replace("filter.toml", "%00filter.toml"), uri + "%00/../../..",
                 "--config=" + uri + "%00/../../.."):
        why = A._probe_program([sys.executable, word], root, allowed, os.environ.get("PATH", ""))[1]
        assert why and "cannot read as a path" in why, word


def test_an_answer_is_read_as_the_harness_reads_it():
    def answer(output="", code=0, err=""):
        return A._hook_answer("git diff", code, output.encode(), err.encode())

    def specific(**fields):
        return json.dumps({"hookSpecificOutput": dict(hookEventName="PreToolUse", **fields)})

    assert answer() == (None, None)
    assert answer("compressing output from here on") == (None, None)
    assert answer(specific(permissionDecision="allow", updatedInput={"command": "git diff"})) == (None, None)
    assert answer(specific(updatedInput={"command": "rtk git diff"})) == ("rewrites", "rtk git diff")
    assert answer(specific(permissionDecision="allow", updatedInput={"description": "x"})) == ("rewrites", None)
    assert answer(specific(permissionDecision="deny", permissionDecisionReason="no")) == ("blocks", "is blocked (no)")
    assert answer(specific(permissionDecision="ask")) == ("blocks", "is held for a person's approval")
    assert answer(json.dumps({"decision": "block", "reason": "legacy"})) == ("blocks", "is blocked (legacy)")
    assert answer(json.dumps({"continue": False})) == ("blocks", "stops the agent before it runs")
    assert answer(err="blocked", code=2) == ("blocks", "is blocked (blocked)")
    assert answer("{not json}") == ("unreadable", "its answer is not JSON")
    assert answer(code=1) == ("unreadable", "it exited 1")


def test_a_file_address_without_its_slashes_is_read_as_the_path_it_names(monkeypatch):
    """FILTER-EXCLUSIONS-5: `file:/C:/x` is an address with no authority, which Node reads as C:\\x; ao took it for a
    relative path under the project, which on Windows its realpath could not follow, and the hook ran."""
    _on_windows(monkeypatch)
    why = A._probe_program(["rtk", "file:/C:/repo/.ao/filter.toml"], "C:\\repo", {"rtk"}, "")[1]
    assert why and "C:\\repo\\.ao\\filter.toml lies inside the project" in why
    assert A._probe_program(["rtk", "--config=file:/C:/elsewhere/filter.toml"], "C:\\repo", {"rtk"}, "")[1] is None


def test_a_path_that_cannot_be_followed_to_where_it_lies_is_refused(monkeypatch):
    """FILTER-EXCLUSIONS-5: Windows' realpath of `C:\\repo\\f::$DATA`, the stream of a file not yet written, came back
    relative to the drive, and `_within` judged it outside the project."""
    import ntpath
    _on_windows(monkeypatch)
    real = ntpath.realpath
    monkeypatch.setattr(ntpath, "realpath", lambda path, *a, **k: "f::$DATA" if str(path).endswith("::$DATA")
                        else real(path, *a, **k))

    why = A._probe_program(["rtk", "C:\\repo\\.ao\\f::$DATA"], "C:\\repo", {"rtk"}, "")[1]

    assert why and "cannot follow" in why
