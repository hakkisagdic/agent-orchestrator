"""A Kiro CLI user takes ECC's Kiro layer through the content seam, with no fork (#15).

Backlog #15 asked for a Kiro target in a fork of ECC's installer. ao's content seam already
borrowed skills pinned and text only; it now borrows steering files and agent definitions
too, wherever a harness's adapter declares them. These build a repository in the shapes ECC
keeps in `.kiro/` - steering in each inclusion mode, agents as CLI JSON beside their IDE
Markdown, a skill, IDE hooks, scripts - and hold what the seam promises of it: a full-commit
pin, front matter as written, agents in the CLI's format with no field that runs a command,
hooks named and never imported, executables refused on every platform, a dry run that
writes nothing, drift that `ao content verify` names, and no write outside the harness's own
directory but the record.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

from ao import cli, lib as A, storage  # noqa: F401  (storage: loaded before a test listens to writes)

ANSI = re.compile(r"\x1b\[[0-9;]*m")
STEERING = {
    "coding-style": "---\ninclusion: auto\nname: coding-style\ndescription: Core coding style rules.\n---\n\n"
                    "# Coding style\n\nPrefer immutable data.\n",
    "python-patterns": "---\ninclusion: fileMatch\nfileMatchPattern: \"*.py\"\ndescription: Python patterns.\n---\n\n"
                       "# Python\n",
    "review-mode": "---\ninclusion: manual\ndescription: Code review mode.\n---\n\n# Review mode\n",
    "always-on": "---\ninclusion: always\n---\n\n# Always on\n",
}
SKILL = "---\nname: tdd-workflow\ndescription: Tests first.\n---\n\n# TDD\n\nWrite the test first.\n"


def _git(cwd, *args, stdin=None):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, input=stdin).stdout.strip()


def _agent(name, **fields):
    """An agent definition in the shape of ECC's .kiro/agents/*.json."""
    return json.dumps(dict({"name": name, "description": f"The {name}.", "mcpServers": {}, "tools": ["@builtin"],
                            "allowedTools": ["fs_read"], "resources": [], "hooks": {}, "useLegacyMcpJson": False,
                            "prompt": f"You are the {name}."}, **fields), indent=2) + "\n"


BUILDER = {"allowedTools": ["fs_read", "shell"],
           "hooks": {"agentSpawn": [{"command": "git status"}],
                     "postToolUse": [{"matcher": "fs_write", "command": "npx tsc --noEmit"}]},
           "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}


def _ecc(tmp_path):
    """A repository keeping a Kiro layer in .kiro/ as ECC does, and the commit to pin it at."""
    source = tmp_path / "ecc"
    files = {f"steering/{name}.md": text for name, text in STEERING.items()}
    files.update({
        "steering/setup.md": "#!/bin/sh\necho a steering file that runs\n",
        "steering/tdd-workflow.md": "---\ninclusion: manual\n---\n\n# TDD, as steering\n",
        "steering/ao-playbook.md": "# a playbook that is not ao's\n",
        "agents/planner.json": _agent("planner"),
        "agents/planner.md": "---\nname: planner\ndescription: The planner.\nallowedTools:\n  - read\n---\n\n"
                             "You are the planner.\n",
        "agents/builder.json": _agent("builder", **BUILDER),
        "agents/impostor.json": _agent("kiro_default"),
        "agents/runner.json": _agent("runner"),
        "skills/tdd-workflow/SKILL.md": SKILL,
        "skills/tdd-workflow/check.sh": "#!/bin/sh\nexit 0\n",
        "skills/tdd-workflow/hooks/pre.json": "{}\n",
        "hooks/quality-gate.kiro.hook": json.dumps({
            "version": "1.0.0", "enabled": True, "name": "quality-gate", "description": "Run the quality gate.",
            "when": {"type": "userTriggered"},
            "then": {"type": "runCommand", "command": "bash .kiro/scripts/quality-gate.sh"}}, indent=2) + "\n",
        "hooks/README.md": "# Hooks\n",
        "scripts/quality-gate.sh": "#!/bin/bash\nnpm test\n",
    })
    for rel, text in files.items():
        (source / ".kiro" / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / ".kiro" / rel).write_text(text, encoding="utf-8")
    _git(source, "init", "-q")
    _git(source, "add", "-A")
    # The modes are the commit's, not this file system's: an executable and a link on every platform.
    for rel in ("agents/runner.json", "skills/tdd-workflow/check.sh", "scripts/quality-gate.sh"):
        _git(source, "update-index", "--chmod=+x", f".kiro/{rel}")
    link = _git(source, "hash-object", "-w", "--stdin", stdin="/etc/hosts")
    _git(source, "update-index", "--add", "--cacheinfo", f"120000,{link},.kiro/skills/tdd-workflow/notes.md")
    _git(source, "commit", "-q", "-m", "a kiro layer")
    return str(source), _git(source, "rev-parse", "HEAD")


def _add(project, spec, dry_run=False, **chosen):
    args = SimpleNamespace(action="add", spec=spec, harness="kiro", base=".kiro", dry_run=dry_run,
                           skills=chosen.get("skills"), steering=chosen.get("steering"), agents=chosen.get("agents"))
    return cli.cmd_content(project, args)


def _out(capsys):
    return ANSI.sub("", capsys.readouterr().out)


def _tree(base):
    """{path under `base`: its bytes, "directory", or ("link", target)} for everything there."""
    found = {}
    for directory, dirs, files in os.walk(base):
        for name in dirs + files:
            full = os.path.join(directory, name)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            if os.path.islink(full):
                found[rel] = ("link", os.readlink(full))
            elif os.path.isdir(full):
                found[rel] = "directory"
            else:
                with open(full, "rb") as fh:
                    found[rel] = fh.read()
    return found


def _read(root, rel):
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        return fh.read()


def test_the_layer_is_borrowed_at_its_pin_and_steering_keeps_its_front_matter(project, tmp_path, capsys):
    root = project["root"]
    source, pin = _ecc(tmp_path)

    assert _add(project, f"{source}@main", steering="coding-style") == 2
    assert "is not a pin" in _out(capsys)

    assert _add(project, f"{source}@{pin}", steering=",".join(STEERING), skills="tdd-workflow", agents="planner") == 0

    out = _out(capsys)
    for name, text in STEERING.items():
        assert _read(root, f".kiro/steering/{name}.md") == text.encode()
    assert _read(root, ".kiro/steering/tdd-workflow.md") \
        == b"---\ninclusion: manual\n---\n\n# TDD\n\nWrite the test first.\n"
    with open(os.path.join(root, ".ao", "content.json"), encoding="utf-8") as fh:
        record = json.load(fh)
    assert sorted(entry["steering"] for entry in record["steering"]) == sorted(STEERING)
    assert {(entry["source"], entry["pin"]) for kind in ("skills", "steering", "agents") for entry in record[kind]} \
        == {(source, pin)}
    planner = _read(root, ".kiro/agents/planner.json")
    assert planner == _agent("planner").encode()
    digest = "sha256:" + hashlib.sha256(planner).hexdigest()
    assert record["agents"] == [{"agent": "planner", "source": source, "pin": pin, "skipped": [],
                                 "files": {".kiro/agents/planner.json": digest}}]
    # The CLI ao runs loads every steering file whatever its inclusion says: a mode it does not honour is named, not faked.
    for name, mode in (("coding-style", "auto"), ("python-patterns", "fileMatch"), ("review-mode", "manual"),
                       ("tdd-workflow", "manual")):
        assert f"unsupported .kiro/steering/{name}.md (inclusion: {mode} - Kiro CLI 2.x, which this adapter runs, " \
               "supports no inclusion mode and loads every steering file in every session" in out
    assert "unsupported .kiro/steering/always-on.md" not in out
    assert A.verify_content(root) == []


def test_agents_land_as_cli_definitions_with_no_field_that_runs_a_command(project, tmp_path, capsys):
    root = project["root"]
    source, pin = _ecc(tmp_path)

    assert _add(project, f"{source}@{pin}", agents="planner,builder,impostor") == 0

    out = _out(capsys)
    # The CLI's JSON alone: the IDE's Markdown twin would be a second agent of the same name.
    assert sorted(os.listdir(os.path.join(root, ".kiro", "agents"))) == ["builder.json", "planner.json"]
    assert _read(root, ".kiro/agents/planner.json") == _agent("planner").encode()
    expected = json.loads(_agent("builder", **BUILDER))
    del expected["hooks"], expected["mcpServers"]
    assert json.loads(_read(root, ".kiro/agents/builder.json")) == expected
    assert "skipped agents/builder.json hooks (hooks are never imported: each runs a command" in out
    assert "skipped agents/builder.json mcpServers (an MCP server is a command the CLI starts: never imported)" in out
    assert ".kiro/agents/builder.json keeps allowedTools fs_read, shell: the tools it uses without asking" in out
    assert ".kiro/agents/planner.json names no resources: a custom agent loads no steering and no skills" in out
    assert "skipped agents/impostor.json (it names itself 'kiro_default', and an agent is named as its file)" in out
    with open(os.path.join(root, ".ao", "content.json"), encoding="utf-8") as fh:
        builder = next(entry for entry in json.load(fh)["agents"] if entry["agent"] == "builder")
    assert [line.split(" (")[0] for line in builder["skipped"]] == ["agents/builder.json hooks",
                                                                    "agents/builder.json mcpServers"]


def test_hooks_are_never_imported_and_each_is_named_with_the_reason(project, tmp_path, capsys):
    root = project["root"]
    source, pin = _ecc(tmp_path)

    assert _add(project, f"{source}@{pin}", steering="coding-style", skills="tdd-workflow") == 0

    out = _out(capsys)
    assert "skipped hooks/quality-gate.kiro.hook (hooks are never imported; kiro: an IDE hook in the 0.x format " \
           "(when, then), which Kiro CLI does not read)" in out
    assert "skipped hooks/README.md (hooks are never imported)" in out
    assert "skipped hooks/ (hooks are never imported)" in out                      # the skill's own
    assert not os.path.exists(os.path.join(root, ".kiro", "hooks"))
    with open(os.path.join(root, ".ao", "content.json"), encoding="utf-8") as fh:
        record = json.load(fh)
    assert not [target for kind in record.values() for entry in kind for target in entry["files"] if "hook" in target]


def test_an_executable_a_script_or_a_link_is_refused_on_every_platform(project, tmp_path, capsys):
    root = project["root"]
    source, pin = _ecc(tmp_path)

    assert _add(project, f"{source}@{pin}", steering="setup,coding-style", skills="tdd-workflow",
                agents="runner,planner") == 0

    out = _out(capsys)
    assert "skipped steering/setup.md (a script: only text is borrowed)" in out
    assert "skipped agents/runner.json (executable: only text is borrowed)" in out
    assert "skipped check.sh (executable: only text is borrowed)" in out
    assert "skipped notes.md (a symbolic link: only text is borrowed)" in out
    assert sorted(rel for rel, kind in _tree(os.path.join(root, ".kiro")).items() if kind != "directory") \
        == ["agents/planner.json", "steering/coding-style.md", "steering/tdd-workflow.md"]


def test_a_dry_run_says_what_would_be_written_where_and_writes_nothing(project, tmp_path, capsys):
    root = project["root"]
    source, pin = _ecc(tmp_path)
    before = _tree(root)

    assert _add(project, f"{source}@{pin}", dry_run=True, steering="coding-style,python-patterns",
                skills="tdd-workflow", agents="planner,builder") == 0

    out = _out(capsys)
    assert _tree(root) == before
    for kind, name, target in (("steering", "python-patterns", ".kiro/steering/python-patterns.md"),
                               ("skill", "tdd-workflow", ".kiro/steering/tdd-workflow.md"),
                               ("agent", "builder", ".kiro/agents/builder.json")):
        assert f"would vendor {kind} {name}@{pin[:12]} → {target}" in out
    assert "unsupported .kiro/steering/python-patterns.md (inclusion: fileMatch" in out
    assert "skipped agents/builder.json hooks" in out and "skipped hooks/quality-gate.kiro.hook" in out
    assert "vendored" not in out.replace("would vendor", "")
    assert out.rstrip().endswith("dry run: nothing was written")


def test_verify_names_a_steering_file_or_an_agent_that_left_its_pin(project, tmp_path, capsys):
    root = project["root"]
    source, pin = _ecc(tmp_path)
    assert _add(project, f"{source}@{pin}", steering="coding-style", agents="builder") == 0
    assert cli.cmd_content(project, SimpleNamespace(action="verify")) == 0

    with open(os.path.join(root, ".kiro", "steering", "coding-style.md"), "a", encoding="utf-8") as fh:
        fh.write("an edit nobody pinned\n")
    os.remove(os.path.join(root, ".kiro", "agents", "builder.json"))

    assert A.verify_content(root) == [
        f".kiro/steering/coding-style.md (steering coding-style@{pin[:12]}) changed since it was vendored",
        f".kiro/agents/builder.json (agent builder@{pin[:12]}) is missing"]
    capsys.readouterr()
    assert cli.cmd_content(project, SimpleNamespace(action="verify")) == 1
    assert "2 file(s) drifted" in _out(capsys)


_WRITES, _LISTENING, _HOOKED = [], [], []
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _audit(event, args):
    """Each (event, path) the listening thread opens to write, creates, renames or removes.

    A path given relative to a directory descriptor belongs to a tree already named by the
    event that walks it (shutil.rmtree), so it is left out; so is another thread's write.
    """
    if not _LISTENING or threading.get_ident() != _LISTENING[0]:
        return
    if event == "open":
        found = [(args[0], None)] if isinstance(args[2], int) and args[2] & _WRITE_FLAGS else []
    elif event in ("os.rename", "os.link"):
        found = [(args[0], args[2]), (args[1], args[3])]
    elif event in ("os.mkdir", "os.chmod"):
        found = [(args[0], args[2])]
    elif event in ("os.remove", "os.rmdir"):
        found = [(args[0], args[1])]
    elif event == "os.symlink":
        found = [(args[1], args[2])]
    elif event in ("os.truncate", "shutil.rmtree"):
        found = [(args[0], None)]
    else:
        found = []
    _WRITES.extend((event, os.path.abspath(os.fsdecode(path))) for path, fd in found
                   if isinstance(path, (str, bytes, os.PathLike)) and fd in (None, -1))


def test_nothing_is_written_outside_the_harness_directory_but_the_record(project, tmp_path, monkeypatch, capsys):
    root = project["root"]
    source, pin = _ecc(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if not _HOOKED:
        sys.addaudithook(_audit)                         # an audit hook stays for the process; it listens on demand
        _HOOKED.append(True)
    before = _tree(tmp_path)
    del _WRITES[:]

    _LISTENING.append(threading.get_ident())
    try:
        code = _add(project, f"{source}@{pin}", steering=",".join(STEERING), skills="tdd-workflow",
                    agents="planner,builder")
    finally:
        _LISTENING.clear()

    assert code == 0, _out(capsys)
    home, record = os.path.realpath(os.path.join(root, ".kiro")), os.path.realpath(os.path.join(root, ".ao"))
    work = os.path.realpath(str(scratch))
    existed = {os.path.realpath(os.path.join(str(tmp_path), *rel.split("/"))) for rel, kind in before.items()
               if kind == "directory"}

    def allowed(event, path):
        real = os.path.realpath(path)
        if event == "os.mkdir" and real in existed:          # makedirs asks for a directory that is there already
            return True
        if any(real == top or real.startswith(top + os.sep) for top in (home, work)):
            return True
        return os.path.dirname(real) == record and re.fullmatch(r"\.?content\.json(\.\d+\.tmp)?",
                                                                 os.path.basename(real)) is not None

    assert _WRITES and [write for write in _WRITES if not allowed(*write)] == []
    after = _tree(tmp_path)
    changed = sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))
    assert changed and [rel for rel in changed if rel != "proj/.ao/content.json" and rel != "proj/.kiro"
                        and not rel.startswith("proj/.kiro/")] == []
    assert os.listdir(str(scratch)) == []


def test_a_directory_named_elsewhere_a_link_a_file_ao_did_not_vendor_or_a_clash_writes_nothing(project, tmp_path,
                                                                                                 capsys):
    root = project["root"]
    source, pin = _ecc(tmp_path)
    with open(os.path.join(A.adapters_dir(), "kiro.json"), encoding="utf-8") as fh:
        kiro = json.load(fh)
    layer = os.path.join(root, ".ao", "adapters")
    os.makedirs(layer)
    # A project's adapter layer is writable by the agents it describes: here it points steering at ao's
    # own state and agents at the product.
    with open(os.path.join(layer, "kiro.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(kiro, directives=dict(kiro["directives"], steering_dir=".ao", agents_dir="src/agents")), fh)
    before = _tree(root)

    assert _add(project, f"{source}@{pin}", steering="coding-style", agents="planner") == 2

    out = _out(capsys)
    assert "refused .ao/coding-style.md is outside kiro's own directory (.kiro)" in out
    assert "refused src/agents/planner.json is outside kiro's own directory (.kiro)" in out
    assert _tree(root) == before

    os.remove(os.path.join(layer, "kiro.json"))
    outside = tmp_path / "outside"
    outside.mkdir()
    os.makedirs(os.path.join(root, ".kiro", "steering"))
    with open(os.path.join(root, ".kiro", "steering", "coding-style.md"), "w", encoding="utf-8") as fh:
        fh.write("# the project's own rules\n")
    try:
        os.symlink(str(outside), os.path.join(root, ".kiro", "agents"), target_is_directory=True)
        linked = True
    except (OSError, NotImplementedError):
        linked = False                                   # Windows without the privilege to make one
    before = _tree(root)

    assert _add(project, f"{source}@{pin}", steering="coding-style,tdd-workflow,ao-playbook", skills="tdd-workflow",
                agents="planner") == 2

    out = _out(capsys)
    assert "refused .kiro/steering/coding-style.md exists and ao did not vendor it as steering coding-style: " \
           "move it aside first" in out
    assert "refused .kiro/steering/tdd-workflow.md would be written by both skill tdd-workflow and steering " \
           "tdd-workflow" in out
    assert "refused .kiro/steering/ao-playbook.md is a file a harness or ao keeps for itself" in out
    assert not linked or "refused .kiro/agents/planner.json passes through a symbolic link" in out
    assert "not vendored: nothing was written" in out
    assert _tree(root) == before and _tree(str(outside)) == {}
    assert not os.path.exists(os.path.join(root, ".ao", "content.json"))
