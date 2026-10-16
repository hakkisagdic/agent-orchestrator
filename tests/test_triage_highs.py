"""Findings of an independent re-review of ao's history that held, each measured here and fixed (TRIAGE-HIGHS).

A re-review by another model family was triaged against the code with ten slices staged on main, and these held:

1. `ao hooks install` refused a project set up before .ao-project existed, and went ahead in the same project when
   its .ao/config.json could not be read: that project reads as broken, not legacy, and `ao doctor` and `ao prove`
   advised exactly that install. The current hook it put over the old enforcing one stands aside while no marker is
   tracked, so every commit went through unchecked. Install now refuses wherever the current hook would stand aside,
   and doctor and prove name the repair and then the adoption.
2. E-mail over implicit TLS (port 465) took smtplib's own context, which verifies no certificate and no host name,
   so the account's password went to whoever answered in between; STARTTLS was already given a verifying context.
   Both verify now, and nothing else in ao builds or installs a TLS context: urllib's default verifies.
3. `ao status` and the watchdog's quota gates read the implementer's quota by the command its adapter declares, as
   every layer loads it - the project's .ao/adapters/, which the implementer writes, among them - and a planted
   program ran. `ao init` composed the architect's command, which every wake starts, from a user's layer too. Both
   commands are the package's alone now, as a turn's, a route's, an MCP registration's and a probe's are.
4. `ao verify` judged the candidate's isolation by .ao/gates.json read again after waiting for the machine lock, not
   by the definitions it ran: inputs narrowed during the wait and put back after hid an untracked file a gate read,
   and the recorded digest still matched the file. Isolation is judged from the reading the run used, and recorded.
5. A .ao/gates.json in a shape the coverage check could not read raised out of `ao doctor --check --notify` before it
   sent any alarm; `echo pytest` counted as running pytest; and a toolchain whose manifest sat one directory down got
   no gates, so a .NET backend beside a JavaScript workspace went unverified. The shape is a finding of its own and
   the doctor sends the rest, a runner counts only where a command starts, and such a toolchain gets gates of its own.
"""
import ast
import json
import os
import shlex
import ssl
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, email, lib as A
from ao import watchdog as W
from tests import standin
from tests.scenarios import World
from tests.test_hook_paths import _git
from tests.test_init_adoption import _legacy, _plain
from tests.test_profiles import _git_init

REPO = Path(__file__).resolve().parent.parent
INSTALL = SimpleNamespace(action="install", allow_shared_hooks=False)
COMMIT = ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q")


# ---- 1. hooks install in a project from before the marker whose config cannot be read ---------------------------

def _unreadable(tmp_path, monkeypatch):
    """A project an older ao set up, its old enforcing hook in place and no marker ever, whose config reads as `{`."""
    base = tmp_path / "pre-marker"
    base.mkdir()
    root, cfg, hook = _legacy(base, monkeypatch)
    (root / ".ao" / "config.json").write_text("{", encoding="utf-8")
    return root, cfg, hook


def test_hooks_install_refuses_a_project_from_before_the_marker_whose_config_cannot_be_read(project, tmp_path,
                                                                                           monkeypatch, capsys):
    root, cfg, hook = _unreadable(tmp_path, monkeypatch)
    before = hook.read_bytes()
    assert cli._project_enrollment(str(root))["state"] == "broken"

    capsys.readouterr()
    assert cli.cmd_hooks(cfg, INSTALL) == 1
    out = _plain(capsys)

    assert hook.read_bytes() == before
    assert "hooks install refused" in out and "make it readable, then " + cli.PROJECT_ADOPT_COMMAND in out
    # The old hook still runs ao, which this PATH does not hold, so the commit is refused; the current hook install
    # wrote here stood aside, and the same commit went through.
    assert _git(root, *COMMIT, "--allow-empty", "-m", "unchecked", check=False).returncode != 0

    # Where a marker is tracked the current hook goes on to ao, which refuses while the config cannot be read: that
    # project is still installed.
    (Path(project["root"]) / ".ao" / "config.json").write_text("{", encoding="utf-8")
    assert cli._project_enrollment(project["root"])["state"] == "broken"
    assert cli.cmd_hooks(project, INSTALL) == 0


@pytest.mark.parametrize("standing", ("the hook an early ao wrote", "the current hook"))
def test_doctor_and_prove_send_such_a_project_to_its_repair_and_the_adoption_not_to_an_install(tmp_path, monkeypatch,
                                                                                               capsys, standing):
    root, cfg, hook = _unreadable(tmp_path, monkeypatch)
    if standing == "the current hook":
        hook.write_bytes(cli._render_local_hook("pre-commit", "."))

    finding = dict(cli.doctor_problems(cfg))["commit-hook"]

    assert finding.count("make it readable, then " + cli.PROJECT_ADOPT_COMMAND) == 1
    assert finding.count("this project has AO state but it is unreadable") == 1 and "ao hooks install" not in finding

    capsys.readouterr()
    assert cli.cmd_prove(cfg, cli.build_parser().parse_args(["prove", "--no-review"])) == 1
    proved = _plain(capsys)
    assert "make it readable, then " + cli.PROJECT_ADOPT_COMMAND in proved and "— ao hooks install" not in proved

    if standing == "the hook an early ao wrote":
        # The doctor's own row: a doctor that finds ao asks agent binaries their version, and none is started here.
        monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)
        cli.cmd_doctor(cfg, cli.build_parser().parse_args(["doctor"]))
        (row,) = [line for line in _plain(capsys).splitlines() if line.startswith("commit hook")]
        assert "make it readable, then " + cli.PROJECT_ADOPT_COMMAND in row and "ao hooks install" not in row


HOOK_CALLERS = ("hooks", "doctor", "doctor-check", "prove")


def _hook_caller(cfg, caller, monkeypatch, capsys):
    """Run the public command and return its hook advice, without asking installed agent binaries anything."""
    capsys.readouterr()
    if caller == "hooks":
        result = cli.cmd_hooks(cfg, INSTALL)
    elif caller == "prove":
        result = cli.cmd_prove(cfg, cli.build_parser().parse_args(["prove", "--no-review"]))
    else:
        monkeypatch.setattr(cli.shutil, "which", lambda *args, **kwargs: None)
        argv = ["doctor", "--check"] if caller == "doctor-check" else ["doctor"]
        result = cli.cmd_doctor(cfg, cli.build_parser().parse_args(argv))
    out = _plain(capsys)
    if caller == "doctor":
        out = next(line for line in out.splitlines() if line.startswith("commit hook"))
    elif caller == "doctor-check":
        out = next(line for line in out.splitlines() if line.startswith("PROBLEM commit-hook:"))
    elif caller == "prove":
        out = out.split("reviewer answers and is another actor", 1)[0]
    return result, out


@pytest.mark.parametrize("caller", HOOK_CALLERS)
@pytest.mark.parametrize("branch", ("born", "unborn", "orphan"))
@pytest.mark.parametrize("broken", (False, True))
def test_pre_marker_hook_advice_requires_adoption_on_every_branch(tmp_path, monkeypatch, capsys,
                                                                 caller, branch, broken):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    original_branch = _git(root, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if branch == "unborn":
        _git(root, "update-ref", "-d", "HEAD")  # No born ref, as before the first commit.
    elif branch == "orphan":
        _git(root, "switch", "--orphan", "tmp")
    if broken:
        (root / ".ao" / "config.json").write_text("{", encoding="utf-8")
    enrollment = cli._project_enrollment(str(root))
    assert enrollment["state"] == ("broken" if broken else "legacy")
    assert not enrollment.get("recordable") and not enrollment.get("unknown")
    assert not _git(root, "log", "--all", "--format=%H", "--", cli.PROJECT_MARKER).stdout.strip()
    before = hook.read_bytes(), _git(root, "ls-files", "--stage").stdout

    result, out = _hook_caller(cfg, caller, monkeypatch, capsys)

    assert out.count(cli.PROJECT_ADOPT_COMMAND) == 1
    assert ("make it readable, then" in out) == broken
    assert hook.read_bytes() == before[0] and _git(root, "ls-files", "--stage").stdout == before[1]
    assert A.enrollment_record(str(root)) is None
    if caller == "hooks":
        assert result == 1 and "hooks install refused" in out
        # Returning to the born branch must not expose the bypass the review found.
        if branch == "orphan":
            _git(root, "switch", original_branch)
        assert _git(root, *COMMIT, "--allow-empty", "-m", "unchecked", check=False).returncode != 0
    else:
        assert "ao hooks install" not in out


@pytest.mark.parametrize("caller", HOOK_CALLERS)
@pytest.mark.parametrize("evidence", ("index", "HEAD", "history", "record"))
def test_enrolled_hook_advice_keeps_enforcement_from_every_source(tmp_path, monkeypatch, capsys, caller, evidence):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    if evidence == "record":
        assert A.record_enrollment(str(root)) is None
        _git(root, "switch", "--orphan", "tmp")
    else:
        (root / cli.PROJECT_MARKER).write_bytes(cli.PROJECT_MARKER_BYTES)
        _git(root, "add", cli.PROJECT_MARKER)
        if evidence != "index":
            _git(root, *COMMIT, "--no-verify", "-m", "enroll")
            if evidence == "HEAD":
                _git(root, "rm", "--cached", cli.PROJECT_MARKER)
            else:
                _git(root, "switch", "--orphan", "tmp")
    enrollment = cli._project_enrollment(str(root))
    assert enrollment["recordable"] and not enrollment.get("unknown")
    before = hook.read_bytes()

    result, out = _hook_caller(cfg, caller, monkeypatch, capsys)

    assert cli.PROJECT_ADOPT_COMMAND not in out and "make it readable, then" not in out
    if caller == "hooks":
        assert result == 0 and b"ao-hook-v7" in hook.read_bytes()
        assert hook.read_bytes() != before
        installed = hook.read_bytes()
        assert cli.cmd_hooks(cfg, INSTALL) == 0
        assert hook.read_bytes() == installed
    else:
        assert "ao hooks install" in out and hook.read_bytes() == before


@pytest.mark.parametrize("caller", HOOK_CALLERS)
@pytest.mark.parametrize("current", (False, True))
@pytest.mark.parametrize("question", ("index", "HEAD", "history", "record"))
def test_unknown_hook_advice_names_the_failed_question_without_install_or_adopt(tmp_path, monkeypatch, capsys,
                                                                              caller, current, question):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    if current:
        hook.write_bytes(cli._render_local_hook("pre-commit", "."))
    before = hook.read_bytes(), _git(root, "ls-files", "--stage").stdout
    real_git = cli._hook_git

    def failed_query(root, *args, **kwargs):
        if args[-1] == cli.PROJECT_MARKER and args[0] == {
                "index": "ls-files", "HEAD": "ls-tree", "history": "log"}.get(question):
            return subprocess.CompletedProcess(args, 128, b"", f"{question} query failed".encode())
        return real_git(root, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(cli, "_hook_git", failed_query)
        if question == "record":
            patch.setattr(A, "enrollment_record", lambda root: {"unreadable": "record query failed"})
        enrollment = cli._project_enrollment(str(root))
        assert enrollment["unknown"] and not enrollment["recordable"]
        if question in ("history", "record"):
            assert enrollment["index"]["status"] == enrollment["head"]["status"] == "absent"

        result, out = _hook_caller(cfg, caller, patch, capsys)

    assert "enrollment is unknown" in out and f"{question} query failed" in out
    assert cli.PROJECT_ADOPT_COMMAND not in out and "ao hooks install" not in out
    if caller == "hooks":
        assert result == 1 and "hooks install refused" in out
    assert hook.read_bytes() == before[0] and _git(root, "ls-files", "--stage").stdout == before[1]
    assert A.enrollment_record(str(root)) is None


# ---- 2. SMTP verifies the server, over implicit TLS as over STARTTLS ---------------------------------------------

class _Server:
    """An SMTP server that answers and keeps what it was asked, the context each TLS was given among it."""

    def __init__(self, host, port, timeout=0, context=None):
        self.calls, self.contexts = [("connect", host, port)], [context] if context is not None else []
        _Server.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.contexts.append(context)

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message):
        self.calls.append(("send", message["To"]))


@pytest.mark.parametrize(("tls", "port"), (("implicit", 465), ("starttls", 587)))
def test_smtp_verifies_the_certificate_and_the_host_name_before_the_password_goes(tmp_path, monkeypatch, tls, port):
    monkeypatch.setattr(email, "CONF", str(tmp_path / "email.json"))
    email.save_provider("smtp", host="smtp.example.test", user="me@example.test", password="pw",
                        to="me@example.test", tls=tls)

    assert email.send("red", "the queue is empty", opener=_Server) is True

    server = _Server.last
    assert server.calls == [("connect", "smtp.example.test", port), ("login", "me@example.test"),
                            ("send", "me@example.test")]
    (context,) = server.contexts
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True


def _names(node):
    return {sub.attr if isinstance(sub, ast.Attribute) else sub.id for sub in ast.walk(node)
            if isinstance(sub, (ast.Attribute, ast.Name))}


def test_nothing_in_ao_reaches_tls_with_a_context_that_does_not_verify():
    """Every other TLS connection ao makes is urllib's - the alarms' HTTP, Telegram, formsubmit, the A2A bridge, the
    account lookup, the API reviewer - whose default context verifies. No module builds, installs or weakens one,
    and the two smtplib calls, whose own context does not verify, are each given one."""
    weakening = {"_create_unverified_context", "_create_stdlib_context", "CERT_NONE", "CERT_OPTIONAL", "check_hostname",
                 "_create_default_https_context", "install_opener", "HTTPSHandler", "HTTPSConnection", "wrap_socket",
                 "SSLContext"}
    found, bare = [], []
    for path in sorted((REPO / "src" / "ao").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        given = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(keyword.arg == "context" for keyword in node.keywords):
                given.update(id(sub) for sub in ast.walk(node.func))
        for node in ast.walk(tree):
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            where = f"{path.relative_to(REPO).as_posix()}:{node.lineno} {name}" if name else None
            if name in weakening:
                found.append(where)
            elif name in ("SMTP_SSL", "starttls") and id(node) not in given:
                bare.append(where)

    assert found == [] and bare == []


# ---- 3. the quota command, and the architect's command init writes, are the package's alone -------------------------

def _planted(root, tmp_path):
    """The project's own .ao/adapters/kiro.json - the shipped adapter, with the quota command a writer of the tree
    chose: a program that leaves a file behind when it runs. (the planted program's path, that file)"""
    ran = tmp_path / "planted-ran"
    program = standin.program(tmp_path / "planted-bin", "planted", f"open({str(ran)!r}, 'w').write('ran')\n")
    adapter = json.loads(json.dumps(A.package_adapters()["kiro"]))
    adapter["telemetry"]["quota"] = {"from": "command", "argv": [program], "cache_seconds": 300}
    layer = Path(root) / ".ao" / "adapters"
    layer.mkdir(parents=True, exist_ok=True)
    (layer / "kiro.json").write_text(json.dumps(adapter), encoding="utf-8")
    return program, ran


def _quota_runs(monkeypatch, planted):
    """The quota commands ao asks to run. The planted one runs as ao runs it; the package's is only noted, so no
    keyflip is started."""
    asked, run = [], A._quota_output
    monkeypatch.setattr(A, "_QUOTA", {})
    monkeypatch.setattr(A, "_quota_output", lambda argv: asked.append(list(argv)) or
                        (run(argv) if list(argv) == [planted] else ("", False)))
    return asked


SHIPPED_QUOTA = A.package_adapters()["kiro"]["telemetry"]["quota"]["argv"]


def test_status_reads_the_quota_by_the_packages_command_not_one_the_projects_layer_declares(project, tmp_path,
                                                                                           monkeypatch):
    program, ran = _planted(project["root"], tmp_path)
    asked = _quota_runs(monkeypatch, program)
    assert A.load_adapter("kiro", project["root"])["telemetry"]["quota"]["argv"] == [program]    # the layer is read

    cli.status_facts(project)

    assert not ran.exists() and asked == [SHIPPED_QUOTA]


# The quota reader and the watchdog's gate as ao has them, before a world stands them in for its own.
QUOTA, QUOTA_OK = A.quota, W.quota_ok


def test_the_watchdogs_quota_gate_reads_it_by_the_packages_command_too(project, tmp_path, monkeypatch):
    program, ran = _planted(project["root"], tmp_path)
    world = World(project, monkeypatch, tmp_path)
    asked, gated = _quota_runs(monkeypatch, program), []
    monkeypatch.setattr(A, "quota", QUOTA)
    monkeypatch.setattr(W, "quota_ok", lambda adapter: gated.append(adapter) or QUOTA_OK(adapter))
    monkeypatch.setattr(A, "_run_program", lambda argv, cwd=None, **kwargs: ("", 1))     # keyflip sets no budget
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)

    world.cycle()

    assert gated and [adapter["telemetry"]["quota"]["argv"] for adapter in gated] == [SHIPPED_QUOTA] * len(gated)
    assert not ran.exists() and asked and all(argv == SHIPPED_QUOTA for argv in asked)


def test_init_composes_the_architects_command_from_the_package_not_from_a_users_layer(tmp_path, monkeypatch):
    shipped = cli._architect_argv("claude-code")
    layer = tmp_path / "user-adapters"
    layer.mkdir()
    adapter = json.loads(json.dumps(A.package_adapters()["claude-code"]))
    adapter["resume"]["argv"] = adapter["resume"]["argv"] + ["--mcp-config", "elsewhere.json"]
    (layer / "claude-code.json").write_text(json.dumps(adapter), encoding="utf-8")
    monkeypatch.setenv("AO_USER_ADAPTERS", str(layer))
    assert A.load_adapter("claude-code")["resume"]["argv"][-2:] == ["--mcp-config", "elsewhere.json"]

    assert cli._architect_argv("claude-code") == shipped and "--mcp-config" not in shipped


# ---- 4. verify judges isolation by the gates it ran ----------------------------------------------------------------

def _command(argv):
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _write_gates(root, spec):
    (Path(root) / ".ao" / "gates.json").write_text(json.dumps(spec), encoding="utf-8")


def test_verify_judges_isolation_by_the_gates_it_ran_not_by_the_file_read_again_after_the_lock(project, tmp_path,
                                                                                              monkeypatch):
    root = project["root"]
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    (Path(root) / "src").mkdir()
    (Path(root) / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "src/a.py")
    (Path(root) / "src" / "conftest.py").write_text("# what the suite loads, never staged\n", encoding="utf-8")
    gates = {"gates": {"tests": {"run": _command([sys.executable, "-c", "pass"]), "inputs": ["src/**"]}},
             "profiles": {"quick": ["tests"]}}
    narrowed = {"gates": {"tests": dict(gates["gates"]["tests"], inputs=["docs/**"])}, "profiles": {"quick": ["tests"]}}
    _write_gates(root, gates)
    taken = cli._take_gate_lock

    def narrowed_while_it_waits(cfg, wait):
        _write_gates(root, narrowed)                      # the implementer narrows the inputs while verify waits
        return taken(cfg, wait)

    monkeypatch.setattr(cli, "_take_gate_lock", narrowed_while_it_waits)

    assert cli.cmd_verify(project, SimpleNamespace(profile="quick", wait=0)) == 1
    _write_gates(root, gates)                             # and puts them back once the run is recorded

    record = A.latest_verification(root)
    isolation = [gate for gate in record["gates"] if gate["name"] == "candidate-isolation"]
    assert isolation and "src/conftest.py" in isolation[0]["detail"] and not isolation[0]["passed"]
    assert record["candidate_ready"] is False and record["isolation_inputs"] == ["src/**"]
    assert A.gate_definitions_digest(root, "quick") == record["gates_digest"]      # the digest alone would match


# ---- 5. gate coverage: a shape ao cannot read, a runner where a command starts, a toolchain one directory down -------

def _files(root, tree, ext, n=6):
    folder = Path(root) / tree
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (folder / f"f{i}{ext}").write_text("x\n", encoding="utf-8")


@pytest.mark.parametrize("written", (
    '"x"', "[1]", json.dumps({"gates": ["x"]}),
    json.dumps({"gates": {"stray": "x", "pytest": {"run": "python -m pytest -q"}}, "profiles": {"quick": ["pytest"]}}),
), ids=("a string", "a list", "gates as a list", "a string gate before one that runs"))
def test_a_gates_file_ao_cannot_read_is_a_finding_and_the_doctor_sends_the_rest(project, monkeypatch, written):
    root = project["root"]
    _files(root, "tools", ".py")
    (Path(root) / ".ao" / "gates.json").write_text(written, encoding="utf-8")

    problems = dict(cli.doctor_problems(project))

    assert ".ao/gates.json cannot" in problems["gates-unusable"] and "docs/gates.md" in problems["gates-unusable"]
    sent = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw: sent.append(kw.get("key")) or True)
    assert cli._doctor_check(project, page=True) == 1
    assert {"doctor:gates-unusable", "doctor:no-channel"} <= set(sent)


def test_a_runner_counts_only_where_a_command_starts():
    assert not A._gate_covers({"run": "echo pytest"}, ".", "python")
    assert not A._gate_covers({"run": "git diff --check # pytest"}, ".", "python")
    assert A._gate_covers({"run": "python -m pytest -q"}, ".", "python")
    assert A._gate_covers({"run": "PYTHONPATH=src uv run pytest"}, ".", "python")
    assert A._gate_covers({"run": "cd backend && dotnet test --nologo", "inputs": ["backend/**"]}, "backend", "dotnet")
    assert A._gate_covers({"run": "./gradlew check --quiet"}, ".", "jvm")


def test_a_toolchain_one_directory_down_gets_gates_that_run_there_and_cover_its_tree(tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    _git_init(root)
    (root / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}), encoding="utf-8")
    _files(root, "src", ".js", n=5)
    _files(root, "backend", ".cs", n=5)
    (root / "backend" / "api.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk\" />\n", encoding="utf-8")
    _files(root, "service", ".java", n=5)
    (root / "service" / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (root / "Package.swift").write_text("// swift-tools-version:5.9\n", encoding="utf-8")

    detected = cli._detect_gates(str(root))

    gates, quick = detected["gates"], detected["profiles"]["quick"]
    # Windows quotes even a bare directory name in a shell command (WINDOWS-TESTS-8).
    assert gates["backend-dotnet-build"] == {
        "run": f"cd {A.copyable_word('backend')} && dotnet build --nologo -v q",
        "expect": "exit_zero", "timeout": 1200, "inputs": ["backend/**"]}
    assert {"lint", "backend-dotnet-build", "service-maven-compile", "swift-build", "diff-check"} <= set(quick)
    assert {"backend-dotnet-test", "service-maven-verify", "swift-test"} <= set(gates)
    assert A.gate_coverage_gaps(str(root), detected, 5, names=quick) == []


@pytest.mark.parametrize("command", ["hooks", "commit-check"])
@pytest.mark.parametrize("broken", [False, True])
def test_refusing_pre_marker_enrollment_does_not_wedge_adoption(tmp_path, monkeypatch, command, broken):
    from tests.test_hook_paths import _make_current_ao_available
    from tests.test_init_adoption import _init
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    config = root / ".ao" / "config.json"
    original = config.read_bytes()
    before = hook.read_bytes()
    if broken:
        config.write_text("{", encoding="utf-8")
    assert A.enrollment_record(str(root)) is None

    result = cli.cmd_hooks(cfg, INSTALL) if command == "hooks" else cli.cmd_commit_check(cfg, SimpleNamespace())

    assert result == 1
    assert A.enrollment_record(str(root)) is None
    assert hook.read_bytes() == before
    config.write_bytes(original)
    assert cli._project_enrollment(str(root))["state"] == "legacy"
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_init(cfg, _init("--adopt")) == 0
    assert cli._project_enrollment(str(root))["state"] == "enrolled"


@pytest.mark.parametrize("broad_inputs", [None, ["**"]])
@pytest.mark.parametrize("command", ["commit-ok", "commit-check"])
def test_restoring_another_profiles_inputs_refuses_the_verification(project, tmp_path, monkeypatch, capsys,
                                                                   broad_inputs, command):
    from tests.conftest import review_off
    from tests.test_commit_authority import _repo_with_change
    cfg = review_off(project)
    root = Path(cfg["root"])
    _repo_with_change(str(root))
    monkeypatch.setattr(A, "GATE_LOCK", str(tmp_path / "gate.lock"))
    quick = {"run": _command([sys.executable, "-c",
                              "from pathlib import Path; assert Path('gate-config.txt').read_text() == 'pass'"]),
             "inputs": ["src/**"]}
    broad = {"run": _command([sys.executable, "-c", "pass"])}
    if broad_inputs is not None:
        broad["inputs"] = broad_inputs
    original = {"gates": {"quick": quick, "full": broad}, "profiles": {"quick": ["quick"], "full": ["full"]}}
    narrowed = dict(original, gates=dict(original["gates"], full=dict(broad, inputs=["src/**"])))
    _write_gates(root, narrowed)
    contaminant = root / "gate-config.txt"
    contaminant.write_text("pass", encoding="utf-8")
    assert cli.cmd_verify(cfg, SimpleNamespace(profile="quick", wait=0)) == 0
    record = A.latest_verification(str(root))
    assert record["candidate_ready"] and record["isolation_inputs"] == ["src/**"]
    issues = A.candidate_worktree_issues(str(root), cfg, inputs=record["isolation_inputs"])
    assert issues["worktree_noise"] == ["gate-config.txt"]
    contaminant.unlink()
    assert cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False)) == 0
    assert cli.cmd_commit_check(cfg, SimpleNamespace()) == 0
    _write_gates(root, original)
    assert A.gate_definitions_digest(str(root), "quick") == record["gates_digest"]
    capsys.readouterr()

    result = (cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False)) if command == "commit-ok"
              else cli.cmd_commit_check(cfg, SimpleNamespace()))

    assert result == 1
    assert "isolation inputs changed" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["commit-ok", "commit-check"])
def test_verification_without_isolation_evidence_requires_reverification(project, capsys, monkeypatch, command):
    from tests.test_attacks import _granted
    cfg = _granted(project, capsys)
    record = dict(A.latest_verification(cfg["root"]))
    record.pop("isolation_inputs")
    monkeypatch.setattr(A, "latest_verification", lambda root: record)
    capsys.readouterr()

    result = (cli.cmd_commit_ok(cfg, SimpleNamespace(verify=False)) if command == "commit-ok"
              else cli.cmd_commit_check(cfg, SimpleNamespace()))

    assert result == 1
    assert "does not record isolation inputs" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["hooks", "commit-check"])
@pytest.mark.parametrize("broken", [False, True])
@pytest.mark.parametrize("unknown", ["empty-index", "unreadable-index", "index-query", "head-query", "history-query", "all-history-query", "record-query"])
def test_unknown_enrollment_changes_nothing_and_preserves_adoption(tmp_path, monkeypatch, capsys,
                                                                  command, broken, unknown):
    from tests.test_hook_paths import _make_current_ao_available
    from tests.test_init_adoption import _init
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    config = root / ".ao" / "config.json"
    original = config.read_bytes()
    if broken:
        config.write_text("{", encoding="utf-8")
    unreadable = tmp_path / "unreadable-index"
    unreadable.mkdir()
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    real_git = cli._hook_git

    def failed_query(root, *args, **kwargs):
        failed = {"index-query": "ls-files", "head-query": "ls-tree", "history-query": "rev-list",
                  "all-history-query": "log"}
        if args[0] == failed.get(unknown):
            return subprocess.CompletedProcess(args, 128, b"", b"simulated query failure")
        return real_git(root, *args, **kwargs)

    capsys.readouterr()
    with monkeypatch.context() as patch:
        if unknown in ("empty-index", "unreadable-index"):
            patch.setenv("GIT_INDEX_FILE", "" if unknown == "empty-index" else str(unreadable))
        patch.setattr(cli, "_hook_git", failed_query)
        if unknown == "record-query":
            patch.setattr(A, "enrollment_record", lambda root: {"unreadable": "simulated repository query failure"})
        result = cli.cmd_hooks(cfg, INSTALL) if command == "hooks" else cli.cmd_commit_check(cfg, SimpleNamespace())

    assert result == 1
    assert A.enrollment_record(str(root)) is None
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    out = _plain(capsys)
    assert "enrollment is unknown" in out
    assert {"empty-index": "GIT_INDEX_FILE is set but empty", "unreadable-index": "active index",
            "index-query": "active index", "head-query": "HEAD",
            "history-query": "history", "all-history-query": "history", "record-query": "record"}[unknown] in out
    config.write_bytes(original)
    _make_current_ao_available(monkeypatch, tmp_path)
    assert cli.cmd_init(cfg, _init("--adopt")) == 0


def test_doctor_names_an_empty_nondefault_profile(project):
    root = Path(project["root"])
    _write_gates(root, {"gates": {"pytest-collect": {"run": "pytest --collect-only"}},
                        "profiles": {"quick": ["pytest-collect"], "full": []}})

    problems = dict(cli.doctor_problems(project))

    assert "gates-unusable" in problems
    assert "full" in problems["gates-unusable"] and "no gate" in problems["gates-unusable"]


@pytest.mark.parametrize("index", [".git/index", "alternate-index"])
def test_a_missing_index_is_known_empty(tmp_path, monkeypatch, index):
    root, cfg, hook = _legacy(tmp_path, monkeypatch)
    (root / index).unlink(missing_ok=True)
    assert not (root / index).exists()
    monkeypatch.setenv("GIT_INDEX_FILE", index)

    assert cli._index_project_marker(str(root)) == {"status": "absent"}
    assert cli._project_enrollment(str(root))["state"] == "legacy"
    assert not (root / index).exists()


def test_an_unborn_branch_can_enroll_from_its_staged_marker(project):
    root = Path(project["root"])
    _git(root, "switch", "--orphan", "first-marker")
    (root / cli.PROJECT_MARKER).write_bytes(cli.PROJECT_MARKER_BYTES)
    _git(root, "add", cli.PROJECT_MARKER)

    enrollment = cli._project_enrollment(str(root))

    assert enrollment["state"] == "enrolled" and enrollment["source"] == "index"
    assert enrollment["head"] == {"status": "absent", "unborn": True}
    assert enrollment["recordable"] is True


@pytest.mark.parametrize("command", ["hooks", "commit-check"])
def test_orphan_enrollment_survives_a_markerless_commit_and_deleted_refs(project, command, capsys):
    root = Path(project["root"])
    assert A.enrollment_record(str(root)) is None
    _git(root, "switch", "--orphan", "elsewhere")
    if command == "hooks":
        cli.cmd_hooks(project, INSTALL)
    else:
        cli.cmd_commit_check(project, SimpleNamespace())
    # commit-tree bypasses hooks; the new branch has no marker in its own history.
    tree = _git(root, "write-tree").stdout.strip()
    commit = _git(root, "commit-tree", tree, "-m", "markerless root").stdout.strip()
    _git(root, "reset", "--hard", commit)
    for ref in _git(root, "for-each-ref", "--format=%(refname)").stdout.split():
        if ref != "refs/heads/elsewhere":
            _git(root, "update-ref", "-d", ref)
    (root / ".ao" / "config.json").unlink()
    assert not _git(root, "log", "--all", "--format=%H", "--", cli.PROJECT_MARKER).stdout.strip()
    capsys.readouterr()

    assert cli.cmd_commit_check(project, SimpleNamespace()) == 1
    assert "recorded the project as enrolled" in capsys.readouterr().out
    assert A.enrollment_record(str(root)) is not None


def test_the_hook_checks_the_record_after_every_marker_ref_is_deleted(project, tmp_path, monkeypatch):
    root = Path(project["root"])
    assert A.record_enrollment(str(root)) is None
    _git(root, "switch", "--orphan", "elsewhere")
    tree = _git(root, "write-tree").stdout.strip()
    commit = _git(root, "commit-tree", tree, "-m", "markerless root").stdout.strip()
    _git(root, "reset", "--hard", commit)
    for ref in _git(root, "for-each-ref", "--format=%(refname)").stdout.split():
        if ref != "refs/heads/elsewhere":
            _git(root, "update-ref", "-d", ref)
    (root / ".ao" / "config.json").unlink()
    standin.program(tmp_path / "bin", "ao", "import sys\n"
                    f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
                    "from ao import cli\nsys.exit(cli.main(sys.argv[1:]))\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ.get("PATH", ""))
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(cli._render_local_hook("pre-commit", "."))
    hook.chmod(0o755)

    done = _git(root, *COMMIT, "--allow-empty", "-m", "unreviewed", check=False)

    assert done.returncode != 0
    assert "recorded the project as enrolled" in done.stdout + done.stderr
    assert _git(root, "rev-parse", "HEAD").stdout.strip() == commit
