"""Hooks: rendering, classifying, installing, probing and removing ao's git hooks, and push.

A part of src/ao/cli.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


# Exact bodies emitted before AO53. They remain as a deliberately narrow legacy
# grammar: callers and old tests may still render them, but new installs never do.
PRE_COMMIT_HOOK = """#!/bin/sh
# agent-orchestrator: commit authority is bound to Git's exact active index.
exec {ao} -C {root} commit-check
"""

PRE_PUSH_HOOK = """#!/bin/sh
# agent-orchestrator: push is a human decision. Allowed when a person ran
# `ao push allow` in the last 30 minutes for this repository; refused otherwise.
exec {ao} -C {root} push check
"""

_HOOK_ROLES = ("pre-commit", "pre-push")
_HOOK_REPOSITORY_ENV = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS",
}
_CURRENT_STATES = {
    "current-local (behavior unverified)",
    "current-scoped (behavior unverified)",
}


class _HookResolutionError(RuntimeError):
    pass


def _hook_git_env():
    """A copied environment without inherited repository/config/pathspec binding."""
    env = os.environ.copy()
    for key in list(env):
        if key in _HOOK_REPOSITORY_ENV or key.startswith("GIT_CONFIG_KEY_") \
                or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    return env


def _hook_git(cwd, *args, timeout=15, extra_env=None):
    """Run one literal-path Git query without a shell; return the byte result."""
    env = _hook_git_env()
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(
            [A.git_binary(), "--literal-pathspecs", "-C", str(cwd), *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _HookResolutionError(f"git query failed: {exc}") from exc


def _hook_output_path(result, label):
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        raise _HookResolutionError(f"{label} failed: {detail or 'git exit ' + str(result.returncode)}")
    raw = result.stdout[:-1] if result.stdout.endswith(b"\n") else result.stdout
    if not raw:
        raise _HookResolutionError(f"{label} returned an empty path")
    return os.fsdecode(raw)


def _hook_real(top, value):
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        value = os.path.join(top, value)
    return os.path.realpath(os.path.abspath(value))


def _hook_contains(parent, child):
    try:
        return os.path.commonpath([os.path.realpath(parent), os.path.realpath(child)]) \
            == os.path.realpath(parent)
    except (OSError, ValueError):
        return False


def _parse_hooks_config(result):
    """Parse exactly scope/origin/value, preserving empty values and separators."""
    if result.returncode == 1 and not result.stdout:
        return {"set": False, "scope": None, "origin": None, "value": None}
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        raise _HookResolutionError(
            f"core.hooksPath query failed: {detail or 'git exit ' + str(result.returncode)}"
        )
    fields = result.stdout.split(b"\0")
    if len(fields) != 4 or fields[-1] != b"":
        raise _HookResolutionError("core.hooksPath output is not three NUL-terminated fields")
    try:
        scope, origin, value = (os.fsdecode(field) for field in fields[:3])
    except UnicodeError as exc:
        raise _HookResolutionError(f"core.hooksPath output is undecodable: {exc}") from exc
    if not scope or not origin:
        raise _HookResolutionError("core.hooksPath output has an empty scope/origin")
    return {"set": True, "scope": scope, "origin": origin, "value": value}


def _hooks_config(top):
    return _parse_hooks_config(_hook_git(
        top, "config", "--null", "--show-origin", "--show-scope", "--get",
        "core.hooksPath",
    ))


def _parse_worktrees(raw):
    rows = []
    for block in raw.decode(UTF8, "surrogateescape").strip().split("\n\n"):
        if not block.strip():
            continue
        row = {"path": None, "bare": False, "porcelain_locked": False}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                row["path"] = value
            elif key == "bare":
                row["bare"] = True
            elif key == "locked":
                row["porcelain_locked"] = True
        if row["path"]:
            rows.append(row)
    return rows


def _worktree_admins(common_dir):
    """Return regular admin records; a symlink/unreadable record is unknowable."""
    base = os.path.join(common_dir, "worktrees")
    records, unknown = [], []
    if not os.path.isdir(base):
        return records, unknown
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return records, [base]
    for name in names:
        admin = os.path.join(base, name)
        if os.path.islink(admin) or not os.path.isdir(admin):
            unknown.append(admin)
            continue
        gitdir_file = os.path.join(admin, "gitdir")
        try:
            if os.path.islink(gitdir_file) or not os.path.isfile(gitdir_file):
                raise OSError("gitdir is not a regular file")
            marker = open(gitdir_file, "rb").read().decode(UTF8, "surrogateescape").strip()
        except (OSError, UnicodeError):
            unknown.append(admin)
            continue
        if not marker:
            unknown.append(admin)
            continue
        marker = os.path.abspath(marker)
        worktree = os.path.dirname(marker) if os.path.basename(marker) == ".git" else None
        if not worktree:
            unknown.append(admin)
            continue
        records.append({
            "name": name,
            "admin": os.path.realpath(admin),
            "worktree": os.path.realpath(worktree),
            "locked": os.path.isfile(os.path.join(admin, "locked"))
                      and not os.path.islink(os.path.join(admin, "locked")),
        })
    return records, unknown


def _worktree_facts(top, common_dir):
    result = _hook_git(top, "worktree", "list", "--porcelain")
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        raise _HookResolutionError(f"git worktree list failed: {detail or result.returncode}")
    rows = _parse_worktrees(result.stdout)
    admins, unknown = _worktree_admins(common_dir)
    if unknown:
        raise _HookResolutionError("unknown worktree admin: " + ", ".join(unknown[:3]))
    by_path = {row["worktree"]: row for row in admins}
    facts = []
    for row in rows:
        path = os.path.realpath(row["path"])
        fact = {"path": path, "bare": row["bare"], "admin": None,
                "status": "reachable", "effective_dir": None, "config": None}
        if row["bare"]:
            facts.append(fact)
            continue
        marker = os.path.join(path, ".git")
        admin = by_path.get(path)
        if os.path.isdir(path) and os.path.lexists(marker):
            fact["status"] = "reachable"
            if admin:
                fact["admin"] = admin["admin"]
        elif admin:
            fact["admin"] = admin["admin"]
            fact["status"] = "locked" if admin["locked"] else "stale"
        else:
            fact["status"] = "unknown"
        facts.append(fact)
    known = {fact["path"] for fact in facts}
    for admin in admins:
        if admin["worktree"] not in known:
            facts.append({
                "path": admin["worktree"], "bare": False,
                "admin": admin["admin"],
                "status": "locked" if admin["locked"] else "stale",
                "effective_dir": None, "config": None,
            })
    if any(f["status"] == "unknown" for f in facts):
        raise _HookResolutionError("unknown worktree record prevents safe hook mutation")
    for fact in facts:
        if fact["status"] != "reachable" or fact["bare"]:
            continue
        wt_top = _hook_output_path(
            _hook_git(fact["path"], "rev-parse", "--show-toplevel"),
            "worktree --show-toplevel",
        )
        wt_top = _hook_real(fact["path"], wt_top)
        fact["effective_dir"] = _hook_real(
            wt_top,
            _hook_output_path(_hook_git(wt_top, "rev-parse", "--git-path", "hooks"),
                              "worktree --git-path hooks"),
        )
        fact["config"] = _hooks_config(wt_top)
    return facts, admins


def _hook_directory_class(path, top, git_dir, common_dir, facts, config=None):
    path = os.path.realpath(path)
    hits = sum(
        1 for fact in facts
        if fact.get("effective_dir") and os.path.realpath(fact["effective_dir"]) == path
    )
    live_family = sum(1 for fact in facts if fact["status"] != "stale")
    unavailable = any(fact["status"] in ("locked", "unknown") for fact in facts)
    common_hooks = os.path.realpath(os.path.join(common_dir, "hooks"))
    absolute_config = bool(config and config.get("set") and os.path.isabs(config.get("value") or ""))
    if hits >= 2 or (path == common_hooks and live_family > 1) \
            or (absolute_config and unavailable and hits):
        return "shared"
    if _hook_contains(top, path) or _hook_contains(git_dir, path):
        return "project-local"
    return "external"


def _structural_roots(path):
    roots, current = [], os.path.abspath(os.path.dirname(path))
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            roots.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return roots


def _hook_track_state(path):
    """Literal tracked/untracked measurement across every enclosing worktree."""
    exists = os.path.lexists(path)
    measured = []
    seen = set()
    for candidate in _structural_roots(path):
        try:
            result = _hook_git(candidate, "rev-parse", "--show-toplevel")
        except _HookResolutionError:
            measured.append("indeterminate")
            continue
        if result.returncode or not result.stdout:
            measured.append("indeterminate")
            continue
        try:
            top = _hook_real(candidate, _hook_output_path(result, "--show-toplevel"))
        except _HookResolutionError:
            measured.append("indeterminate")
            continue
        if top in seen or not _hook_contains(top, path):
            continue
        seen.add(top)
        rel = os.path.relpath(path, top)
        try:
            result = _hook_git(top, "ls-files", "--error-unmatch", "--", rel)
        except _HookResolutionError:
            measured.append("indeterminate")
            continue
        if result.returncode == 0:
            measured.append("tracked")
        elif result.returncode == 1:
            measured.append("untracked")
        else:
            measured.append("indeterminate")
    if "tracked" in measured:
        return "tracked"
    if "indeterminate" in measured:
        return "indeterminate"
    if measured:
        return "untracked"
    return "indeterminate" if exists else "unobserved"


def _role_command(role):
    return "commit-check" if role == "pre-commit" else "push check"


def _render_v2_local_hook(role, root_rel):
    """Exact project-local body emitted before tracked project enrollment."""
    import shlex
    suffix = "" if root_rel in ("", ".") else "/" + shlex.quote(root_rel)
    command = _role_command(role)
    return (
        "#!/bin/sh\n"
        f"# agent-orchestrator: ao-hook-v2 role={role} binding=project-local\n"
        "initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
        f"root=\"$initial_cwd\"{suffix}\n"
        "[ -d \"$root/.ao\" ] || exit 0\n"
        "case ${GIT_INDEX_FILE-} in\n"
        "  \"\"|/*) ;;\n"
        "  *) GIT_INDEX_FILE=\"$initial_cwd/$GIT_INDEX_FILE\"; export GIT_INDEX_FILE ;;\n"
        "esac\n"
        "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX "
        "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES\n"
        "ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
        f"exec \"$ao\" -C \"$root\" {command}\n"
    ).encode(UTF8)


def _render_v2_scoped_hook(role, root_rel, family, directory_class):
    """Exact shared/external body emitted before tracked project enrollment."""
    import shlex
    suffix = "" if root_rel in ("", ".") else "/" + shlex.quote(root_rel)
    command = _role_command(role)
    return (
        "#!/bin/sh\n"
        f"# agent-orchestrator: ao-hook-v2 role={role} binding={directory_class}\n"
        "initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
        "case ${GIT_INDEX_FILE-} in\n"
        "  \"\"|/*) ;;\n"
        "  *) GIT_INDEX_FILE=\"$initial_cwd/$GIT_INDEX_FILE\"; export GIT_INDEX_FILE ;;\n"
        "esac\n"
        "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX "
        "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES\n"
        "common=$(git --literal-pathspecs rev-parse --git-common-dir 2>/dev/null) || exit 0\n"
        "case $common in /*) ;; *) common=\"$initial_cwd/$common\" ;; esac\n"
        "common=$(CDPATH= cd \"$common\" 2>/dev/null && pwd -P) || exit 0\n"
        f"expected={shlex.quote(os.path.realpath(family))}\n"
        "[ \"$common\" = \"$expected\" ] || exit 0\n"
        "top=$(git --literal-pathspecs rev-parse --show-toplevel 2>/dev/null) || exit 0\n"
        "case $top in /*) ;; *) top=\"$initial_cwd/$top\" ;; esac\n"
        "top=$(CDPATH= cd \"$top\" 2>/dev/null && pwd -P) || exit 0\n"
        f"root=\"$top\"{suffix}\n"
        "[ -d \"$root/.ao\" ] || exit 0\n"
        "ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
        f"exec \"$ao\" -C \"$root\" {command}\n"
    ).encode(UTF8)


def _hook_repository_unset():
    return (
        "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX "
        "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES "
        "GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_CONFIG_GLOBAL "
        "GIT_CONFIG_SYSTEM GIT_CONFIG_NOSYSTEM GIT_CEILING_DIRECTORIES "
        "GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_LITERAL_PATHSPECS GIT_GLOB_PATHSPECS "
        "GIT_NOGLOB_PATHSPECS GIT_ICASE_PATHSPECS\n"
    )


def _hook_project_marker_guard():
    return (
        "if ! git --literal-pathspecs -C \"$root\" ls-files --error-unmatch -- "
        ".ao-project >/dev/null 2>&1 &&\n"
        "   ! git --literal-pathspecs -C \"$root\" cat-file -e "
        "HEAD:.ao-project 2>/dev/null; then\n"
        "  exit 0\n"
        "fi\n"
    )


def _render_local_hook(role, root_rel):
    """Portable bytes for one project-local hook; no machine/family binding."""
    import shlex
    suffix = "" if root_rel in ("", ".") else "/" + shlex.quote(root_rel)
    command = _role_command(role)
    return (
        "#!/bin/sh\n"
        f"# agent-orchestrator: ao-hook-v3 role={role} binding=project-local\n"
        "initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
        f"root=\"$initial_cwd\"{suffix}\n"
        "case ${GIT_INDEX_FILE-} in\n"
        "  \"\"|/*) ;;\n"
        "  *) GIT_INDEX_FILE=\"$initial_cwd/$GIT_INDEX_FILE\"; export GIT_INDEX_FILE ;;\n"
        "esac\n"
        + _hook_repository_unset()
        + _hook_project_marker_guard()
        + "ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
        + f"exec \"$ao\" -C \"$root\" {command}\n"
    ).encode(UTF8)


def _render_scoped_hook(role, root_rel, family, directory_class):
    """Family-bound bytes for a shared/external target; unknown routing is fail-open."""
    import shlex
    suffix = "" if root_rel in ("", ".") else "/" + shlex.quote(root_rel)
    command = _role_command(role)
    return (
        "#!/bin/sh\n"
        f"# agent-orchestrator: ao-hook-v3 role={role} binding={directory_class}\n"
        "initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
        "case ${GIT_INDEX_FILE-} in\n"
        "  \"\"|/*) ;;\n"
        "  *) GIT_INDEX_FILE=\"$initial_cwd/$GIT_INDEX_FILE\"; export GIT_INDEX_FILE ;;\n"
        "esac\n"
        + _hook_repository_unset()
        + "common=$(git --literal-pathspecs rev-parse --git-common-dir 2>/dev/null) || exit 0\n"
        + "case $common in /*) ;; *) common=\"$initial_cwd/$common\" ;; esac\n"
        + "common=$(CDPATH= cd \"$common\" 2>/dev/null && pwd -P) || exit 0\n"
        + f"expected={shlex.quote(os.path.realpath(family))}\n"
        + "[ \"$common\" = \"$expected\" ] || exit 0\n"
        + "top=$(git --literal-pathspecs rev-parse --show-toplevel 2>/dev/null) || exit 0\n"
        + "case $top in /*) ;; *) top=\"$initial_cwd/$top\" ;; esac\n"
        + "top=$(CDPATH= cd \"$top\" 2>/dev/null && pwd -P) || exit 0\n"
        + f"root=\"$top\"{suffix}\n"
        + _hook_project_marker_guard()
        + "ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
        + f"exec \"$ao\" -C \"$root\" {command}\n"
    ).encode(UTF8)


def _legacy_hook_role(data):
    """Recognize only byte-exact generated v1/v2 bodies, including stale bindings."""
    import shlex
    if b"\x00" in data:
        return None
    try:
        text = data.decode(UTF8)
    except UnicodeError:
        return None
    if not text.startswith("#!/bin/sh\n") or "# agent-orchestrator:" not in text:
        return None

    lines = text.splitlines()
    exec_lines = [line for line in lines if line.startswith("exec ")]
    if len(exec_lines) == 1:
        try:
            argv = shlex.split(exec_lines[0])
        except ValueError:
            argv = []
        legacy = None
        if len(argv) == 5 and argv[0] == "exec" and argv[2] == "-C" \
                and argv[4] == "commit-check":
            legacy = PRE_COMMIT_HOOK.format(
                ao=shlex.quote(argv[1]), root=shlex.quote(argv[3])
            ).encode(UTF8)
            role = "pre-commit"
        elif len(argv) == 6 and argv[0] == "exec" and argv[2] == "-C" \
                and argv[4:] == ["push", "check"]:
            legacy = PRE_PUSH_HOOK.format(
                ao=shlex.quote(argv[1]), root=shlex.quote(argv[3])
            ).encode(UTF8)
            role = "pre-push"
        if legacy is not None and data == legacy:
            return role

    marker_prefix = "# agent-orchestrator: ao-hook-v"
    markers = [line for line in lines if line.startswith(marker_prefix)]
    if len(markers) != 1 or " role=" not in markers[0] or " binding=" not in markers[0]:
        return None
    version, declaration = markers[0][len(marker_prefix):].split(" role=", 1)
    if version not in ("2", "3"):
        return None
    role, binding = declaration.split(" binding=", 1)
    if role not in _HOOK_ROLES or binding not in ("project-local", "shared", "external"):
        return None

    def assignment(name):
        prefix = name + "="
        matches = [line for line in lines if line.startswith(prefix)]
        if len(matches) != 1:
            return None
        try:
            words = shlex.split(matches[0])
        except ValueError:
            return None
        if len(words) != 1 or not words[0].startswith(prefix):
            return None
        return words[0][len(prefix):]

    root = assignment("root")
    root_prefix = "$initial_cwd" if binding == "project-local" else "$top"
    if root == root_prefix:
        root_rel = "."
    elif root is not None and root.startswith(root_prefix + "/"):
        root_rel = root[len(root_prefix) + 1:]
    else:
        return None

    if binding == "project-local":
        rendered = (
            _render_v2_local_hook(role, root_rel)
            if version == "2" else _render_local_hook(role, root_rel)
        )
    else:
        family = assignment("expected")
        if not family:
            return None
        rendered = (
            _render_v2_scoped_hook(role, root_rel, family, binding)
            if version == "2" else _render_scoped_hook(role, root_rel, family, binding)
        )
    prior = rendered.replace(
        b"initial_cwd=$(CDPATH= cd -- . && pwd -P)\n",
        b"initial_cwd=$PWD\n",
        1,
    )
    if data == rendered or data == prior:
        return role
    return None


def _crlf_translation(data):
    if b"\r\n" not in data:
        return None
    remainder = data.replace(b"\r\n", b"")
    if b"\n" in remainder or b"\r" in remainder:
        return None
    return data.replace(b"\r\n", b"\n")


def _plausible_ao_role(data, role):
    try:
        text = data.decode(UTF8)
    except UnicodeError:
        return False
    executable = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    command = _role_command(role)
    return command in executable and ("ao" in executable or "agent-orchestrator" in text)


def _classify_hook(path, role, local_body=None, scoped_body=None):
    if not os.path.lexists(path):
        return "absent", False
    if os.path.islink(path):
        return "foreign", False
    try:
        data = open(path, "rb").read()
        data.decode(UTF8)
    except (OSError, UnicodeError):
        return "foreign", False
    if local_body is not None and data == local_body:
        return "current-local (behavior unverified)", False
    if scoped_body is not None and data == scoped_body:
        return "current-scoped (behavior unverified)", False
    translated = _crlf_translation(data)
    for candidate in (data, translated):
        if candidate is None:
            continue
        if (local_body is not None and candidate == local_body) \
                or (scoped_body is not None and candidate == scoped_body) \
                or _legacy_hook_role(candidate) == role:
            return "legacy (behavior unverified)", translated is not None
    if _plausible_ao_role(data, role):
        return "ambiguous-ao", False
    return "foreign", False


def _state_base(state):
    return state.split(" ", 1)[0]


def _repo_source_roles(top):
    result = _hook_git(
        top, "ls-files", "-z", "--", ".githooks/pre-commit", ".githooks/pre-push"
    )
    if result.returncode:
        return set()
    return {os.path.basename(os.fsdecode(raw)) for raw in result.stdout.split(b"\0") if raw}


def _add_hook_dir(directories, path, source):
    path = os.path.realpath(path)
    row = directories.setdefault(path, {"path": path, "sources": set()})
    row["sources"].add(source)
    return row


def _ao_hook_inventory(root):
    """Resolve every active/legacy AO hook target from Git's measured topology."""
    root = os.path.realpath(os.path.abspath(root))
    empty = {
        "root": root, "top": None, "git_dir": None, "common_dir": None,
        "active_dir": None, "directory_class": None, "globally_configured": False,
        "config": {"set": False, "scope": None, "origin": None, "value": None},
        "family": None, "project_rel": None, "worktrees": [], "targets": [],
        "error": None,
    }
    try:
        top = _hook_real(
            root,
            _hook_output_path(_hook_git(root, "rev-parse", "--show-toplevel"),
                              "--show-toplevel"),
        )
        git_dir = _hook_real(
            top, _hook_output_path(_hook_git(top, "rev-parse", "--git-dir"), "--git-dir")
        )
        common_dir = _hook_real(
            top,
            _hook_output_path(_hook_git(top, "rev-parse", "--git-common-dir"),
                              "--git-common-dir"),
        )
        active_dir = _hook_real(
            top,
            _hook_output_path(_hook_git(top, "rev-parse", "--git-path", "hooks"),
                              "--git-path hooks"),
        )
        config = _hooks_config(top)
        facts, admins = _worktree_facts(top, common_dir)
    except _HookResolutionError as exc:
        empty["error"] = str(exc)
        return empty

    directory_class = _hook_directory_class(
        active_dir, top, git_dir, common_dir, facts, config
    )
    globally_configured = config["scope"] in ("system", "global")
    project_rel = os.path.relpath(root, top)
    directories = {}
    _add_hook_dir(directories, active_dir, "active")
    _add_hook_dir(directories, os.path.join(git_dir, "hooks"), "current-private")
    _add_hook_dir(directories, os.path.join(common_dir, "hooks"), "common-fallback")
    for admin in admins:
        _add_hook_dir(directories, os.path.join(admin["admin"], "hooks"),
                      "admin:" + admin["name"])
    for fact in facts:
        if fact.get("effective_dir"):
            _add_hook_dir(directories, fact["effective_dir"], "effective:" + fact["path"])

    repo_dir = os.path.realpath(os.path.join(top, ".githooks"))
    repo_roles = _repo_source_roles(top)
    if repo_roles or any(os.path.lexists(os.path.join(repo_dir, role)) for role in _HOOK_ROLES):
        _add_hook_dir(directories, repo_dir, "repository-source")

    effective_counts = {}
    for fact in facts:
        if fact.get("effective_dir"):
            key = os.path.realpath(fact["effective_dir"])
            effective_counts[key] = effective_counts.get(key, 0) + 1
    locked_admins = {
        os.path.realpath(admin["admin"])
        for admin in admins if admin["locked"]
    }

    targets = []
    ordered_dirs = sorted(directories.values(), key=lambda row: (row["path"] != active_dir, os.fsencode(row["path"])))
    for directory in ordered_dirs:
        path = directory["path"]
        sources = directory["sources"]
        cls = _hook_directory_class(path, top, git_dir, common_dir, facts,
                                    config if path == active_dir else None)
        potential = bool(effective_counts.get(path)) or "common-fallback" in sources
        if any(_hook_contains(admin, path) for admin in locked_admins):
            potential = True
        reachability = "potentially-effective" if potential else "dead-misplaced"
        legacy_location = "active" not in sources and not any(
            source.startswith("effective:") for source in sources
        ) and "repository-source" not in sources
        for role in _HOOK_ROLES:
            target_path = os.path.join(path, role)
            active = path == active_dir
            repository_source = "repository-source" in sources and role in repo_roles
            if not active and not repository_source and not os.path.lexists(target_path):
                continue
            track = _hook_track_state(target_path)
            local_body = _render_local_hook(role, project_rel)
            scoped_body = _render_scoped_hook(role, project_rel, common_dir, cls)
            expected_local = repository_source or cls == "project-local"
            state, crlf_only = _classify_hook(
                target_path, role,
                local_body=local_body if expected_local else None,
                scoped_body=scoped_body if not expected_local else None,
            )
            base = _state_base(state)
            eligible = False
            if base in ("current-local", "current-scoped", "legacy") and track == "untracked":
                eligible = True
            elif base == "absent" and active:
                eligible = track == "untracked" or (
                    track == "unobserved" and (cls in ("shared", "external") or globally_configured)
                )
            needs_auth = eligible and (
                cls in ("shared", "external") or (active and globally_configured)
            )
            targets.append({
                "role": role, "path": target_path, "directory": path,
                "directory_class": cls, "active": active,
                "globally_configured": bool(active and globally_configured),
                "reachability": reachability, "effective_count": effective_counts.get(path, 0),
                "repository_source": repository_source,
                "legacy_location": legacy_location,
                "static_state": state, "track_state": track,
                "protected": track in ("tracked", "indeterminate"),
                "eligible": eligible, "needs_authorization": needs_auth,
                "crlf_only": crlf_only, "sources": sorted(sources),
                "body": local_body if expected_local else scoped_body,
            })

    empty.update({
        "top": top, "git_dir": git_dir, "common_dir": common_dir,
        "active_dir": active_dir, "directory_class": directory_class,
        "globally_configured": globally_configured, "config": config,
        "family": common_dir, "project_rel": project_rel,
        "worktrees": facts, "targets": targets,
    })
    return empty


def _ao_hook_paths(root):
    inv = _ao_hook_inventory(root)
    if inv["error"]:
        return {role: os.path.join(root, ".git", "hooks", role) for role in _HOOK_ROLES}
    return {role: os.path.join(inv["active_dir"], role) for role in _HOOK_ROLES}


def _ao_hook_state(path):
    """Compatibility classifier: marker-only files are foreign, old exact bodies legacy."""
    role = os.path.basename(path)
    if role not in _HOOK_ROLES:
        return "foreign"
    return _classify_hook(path, role)[0]


def _active_hook_targets(inv):
    return {target["role"]: target for target in inv["targets"] if target["active"]}


def _hook_execution_probe(inv):
    """Prove that Git resolves and executes AO's active pre-commit hook.

    Static classification is only a safety precondition: it prevents status and
    doctor from executing foreign hook content.  The positive result comes only
    from ``git hook run pre-commit`` carrying a temporary synthetic index into
    ``cmd_commit_check`` and receiving its nonce-bound refusal marker.
    """
    failed = lambda detail, code=None: {
        "installed": False, "state": "not installed", "detail": detail,
        "exit": code,
    }
    if inv.get("error"):
        return failed("hook topology cannot be resolved: " + inv["error"])
    try:
        target = _active_hook_targets(inv)["pre-commit"]
    except KeyError:
        return failed("Git resolved no active pre-commit target")
    base = _state_base(target["static_state"])
    if base not in ("current-local", "current-scoped"):
        return failed(
            f"active pre-commit intent is {target['static_state']} / "
            f"{target['track_state']}"
        )
    enrollment = _project_enrollment(inv["root"])
    if enrollment["state"] == "uninitialized":
        return failed(enrollment["detail"])
    if enrollment["state"] == "broken":
        return failed(enrollment["detail"])
    if enrollment["state"] == "legacy":
        # A current hook in a legacy project exits in its shell guard before AO
        # runs, so it governs nothing until the marker is adopted.
        return failed(enrollment["detail"])

    head_result = _hook_git(inv["top"], "rev-parse", "--verify", "HEAD")
    if head_result.returncode:
        return failed("HEAD cannot supply the synthetic gitlink object")
    try:
        head = head_result.stdout.strip().decode("ascii", "strict")
    except UnicodeError:
        return failed("HEAD object id is not ASCII")
    if len(head) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in head):
        return failed("HEAD returned an invalid object id")

    import shutil
    import tempfile
    nonce = os.urandom(16).hex()
    path = ".ao-hook-probe-" + nonce
    marker = f"AO-HOOK-PROBE-REFUSED {nonce} {head} {path}".encode("ascii")
    # Removed without raising: a hook that runs past its timeout leaves children holding
    # the probe index, Windows cannot delete an open file, and the cleanup error replaced
    # the probe's own answer (#71). TemporaryDirectory can ignore that only from Python 3.10.
    temporary = tempfile.mkdtemp(prefix="ao-hook-probe-")
    try:
        index = os.path.abspath(os.path.join(temporary, "index"))
        extra = {"GIT_INDEX_FILE": index}
        prepared = _hook_git(inv["top"], "read-tree", "HEAD", extra_env=extra)
        if prepared.returncode:
            return failed("cannot initialize the temporary probe index")
        if enrollment["source"] == "index":
            project_marker = enrollment["marker"]
            carried = _hook_git(
                inv["top"], "update-index", "--add", "--cacheinfo",
                f"{project_marker['mode']},{project_marker['oid']},{PROJECT_MARKER}",
                extra_env=extra,
            )
            if carried.returncode:
                return failed("cannot carry the staged .ao-project into the probe index")
        staged = _hook_git(
            inv["top"], "update-index", "--add", "--cacheinfo",
            f"160000,{head},{path}", extra_env=extra,
        )
        if staged.returncode:
            return failed("cannot stage the synthetic probe candidate")
        extra.update({
            "AO_HOOK_PROBE_NONCE": nonce,
            "AO_HOOK_PROBE_PATH": path,
            "AO_HOOK_PROBE_HEAD": head,
            "AO_HOOK_PROBE_INDEX": index,
        })
        result = _hook_git(
            inv["top"], "hook", "run", "pre-commit",
            timeout=15, extra_env=extra,
        )
    except _HookResolutionError as exc:
        return failed(f"Git could not execute the pre-commit probe: {exc}")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)

    channels = result.stdout.splitlines() + result.stderr.splitlines()
    if result.returncode and marker in channels:
        return {
            "installed": True,
            "state": "installed",
            "detail": "Git executed pre-commit and AO refused the synthetic candidate",
            "exit": result.returncode,
        }
    if result.returncode == 0:
        return failed("Git or the resolved hook allowed the synthetic candidate", 0)
    return failed(
        f"the hook exited {result.returncode} without AO's nonce-bound refusal proof",
        result.returncode,
    )


def _hook_probe_text(probe):
    if probe["installed"]:
        return "installed (execution proved)"
    return "not installed — " + probe["detail"]


def _authorization_refusal(targets, allow):
    needed = [target for target in targets if target.get("needs_authorization")]
    if needed and os.name == "nt":
        # The shared hook compares Git's shell path, /c/..., with C:\..., never
        # matches, and lets every commit through (#71).
        print(f"{C['red']}refused{C['reset']} — a shared or external hook cannot recognise this "
              "repository on Windows, where Git's shell names it /c/... and ao names it C:\\...; "
              "install the hooks inside the repository")
        return True
    if needed and not allow:
        places = ", ".join(sorted({
            f"{target['directory_class']}:{target['directory']}"
            + (" (global/system config)" if target["globally_configured"] else "")
            for target in needed
        }))
        print(f"{C['red']}refused{C['reset']} — shared/external/globally-configured hook mutation "
              f"needs --allow-shared-hooks: {places}")
        return True
    return False


def _atomic_hook_write(path, data):
    import tempfile
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".ao-hook-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            # os.fchmod exists on Windows only from Python 3.13 (#71).
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), 0o755)
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _print_hook_status(inv):
    proof = _hook_execution_probe(inv)
    if inv["error"]:
        print(f"resolver failure: {inv['error']}")
        print(f"pre-commit execution: {_hook_probe_text(proof)}")
        return
    print(f"effective hooks: {inv['active_dir']}")
    facts = []
    for status in ("reachable", "locked", "stale"):
        count = sum(1 for row in inv["worktrees"] if row["status"] == status)
        if count:
            facts.append(f"{count} {status}")
    print(f"class: {inv['directory_class']}" + (f" ({'; '.join(facts)})" if facts else ""))
    config = inv["config"]
    if config["set"]:
        print(f"core.hooksPath: {config['scope']} {config['origin']} value={config['value']!r}")
    else:
        print("core.hooksPath: unset")
    active = _active_hook_targets(inv)
    for role in _HOOK_ROLES:
        target = active[role]
        note = ""
        if role == "pre-push" and _state_base(target["static_state"]) not in (
            "current-local", "current-scoped"
        ):
            note = " — AO push-window hook unavailable"
        print(f"{role}: {target['static_state']} / {target['track_state']}{note}")
    print(f"pre-commit execution: {_hook_probe_text(proof)}")
    for target in inv["targets"]:
        if target["active"] or _state_base(target["static_state"]) in ("absent", "foreign"):
            continue
        print(f"misplaced {target['role']}: {target['static_state']} / "
              f"{target['track_state']} / {target['reachability']} / "
              f"{target['directory_class']} — {target['path']}")


def _hooks_uninstall(inv, allow):
    plan = [
        target for target in inv["targets"]
        if target["eligible"] and _state_base(target["static_state"])
        in ("current-local", "current-scoped", "legacy")
    ]
    if _authorization_refusal(plan, allow):
        return 1
    failed = False
    for target in plan:
        try:
            os.remove(target["path"])
            print(f"removed {target['role']} — {target['path']}")
        except OSError as exc:
            failed = True
            print(f"{C['red']}failed to remove{C['reset']} {target['path']}: {exc}")
    if failed:
        return 1
    after = _ao_hook_inventory(inv["root"])
    if after["error"]:
        print(f"resolver failure after uninstall: {after['error']}")
        return 1
    blockers = []
    for target in after["targets"]:
        base = _state_base(target["static_state"])
        if base not in ("current-local", "current-scoped", "legacy", "ambiguous-ao"):
            continue
        if target["reachability"] == "dead-misplaced" and target["protected"]:
            print(f"preserved protected dead-misplaced {target['role']}: {target['path']}")
            continue
        if target["reachability"] == "potentially-effective":
            blockers.append(target)
            print(f"preserved potentially-effective {target['static_state']} {target['role']}: "
                  f"{target['path']}")
    return 1 if blockers else 0


def cmd_hooks(cfg, args):
    """Manage AO hook intent at Git's effective path without replacing user hooks."""
    root = cfg["root"]
    inv = _ao_hook_inventory(root)
    if inv["error"]:
        print(f"{C['red']}hook resolver failed{C['reset']}: {inv['error']}")
        return 1
    action = args.action
    allow = bool(getattr(args, "allow_shared_hooks", False))
    if action == "status":
        _print_hook_status(inv)
        return 0
    if action == "uninstall":
        return _hooks_uninstall(inv, allow)

    enrollment = _project_enrollment(root)
    if enrollment["state"] == "legacy":
        # The current hooks exit in their shell guard when no marker is tracked, so
        # installing them over a legacy project's enforcing hook would turn commit
        # authority off at exactly the moment someone meant to repair it.
        print(f"{C['red']}hooks install refused{C['reset']}: this project was {enrollment['detail']}")
        print("  then run: ao hooks install")
        return 1

    active = _active_hook_targets(inv)
    plan, unavailable = [], []
    for role in _HOOK_ROLES:
        target = active[role]
        base = _state_base(target["static_state"])
        if base in ("current-local", "current-scoped"):
            continue
        if target["eligible"] and base in ("absent", "legacy"):
            plan.append(target)
        else:
            unavailable.append(target)
            print(f"preserved {target['static_state']} {role} / {target['track_state']} — "
                  f"AO {'commit authority' if role == 'pre-commit' else 'push-window'} hook unavailable")
    if _authorization_refusal(plan, allow):
        return 1
    if plan:
        if os.path.lexists(inv["active_dir"]) and not os.path.isdir(inv["active_dir"]):
            print(f"{C['red']}effective hook path is not a directory{C['reset']}: {inv['active_dir']}")
            return 1
        try:
            os.makedirs(inv["active_dir"], exist_ok=True)
        except OSError as exc:
            print(f"{C['red']}cannot create hook directory{C['reset']}: {exc}")
            return 1
        # Directory creation and another process can change classification. Re-read
        # once, then authorize the whole write set again before the first replace.
        fresh = _ao_hook_inventory(root)
        if fresh["error"] or fresh["active_dir"] != inv["active_dir"] \
                or fresh["directory_class"] != inv["directory_class"] \
                or fresh["globally_configured"] != inv["globally_configured"]:
            print(f"{C['red']}hook topology changed during install; no hook written{C['reset']}")
            return 1
        fresh_active = _active_hook_targets(fresh)
        fresh_plan = [fresh_active[target["role"]] for target in plan]
        if any(not target["eligible"] or _state_base(target["static_state"])
               not in ("absent", "legacy") for target in fresh_plan):
            print(f"{C['red']}hook target changed during install; no hook written{C['reset']}")
            return 1
        if _authorization_refusal(fresh_plan, allow):
            return 1
        for target in fresh_plan:
            try:
                _atomic_hook_write(target["path"], target["body"])
                print(f"installed {target['role']} — {target['path']}")
            except OSError as exc:
                print(f"{C['red']}failed to install{C['reset']} {target['role']}: {exc}")
                return 1
    final = _ao_hook_inventory(root)
    if final["error"]:
        return 1
    final_active = _active_hook_targets(final)
    ok = all(_state_base(final_active[role]["static_state"])
             in ("current-local", "current-scoped") for role in _HOOK_ROLES)
    if ok:
        print(f"{C['green']}current (behavior unverified){C['reset']} — static hook intent installed; "
              "run `ao hooks status` for execution proof")
    return 0 if ok else 1


def cmd_push(cfg, args):
    """`ao push allow [--minutes N]` opens a window for a person's push; `check` is what the hook runs."""
    root = cfg["root"]
    key = A.project_key(root)
    tok = os.path.join(A.HOME, ".ao", f"push-{key}.ok")
    if args.action == "allow":
        os.makedirs(os.path.dirname(tok), exist_ok=True)
        json.dump({"at": int(time.time()), "minutes": args.minutes, "by": os.environ.get("USER", "human")}, open(tok, "w", encoding=UTF8))
        print(f"{C['green']}push allowed{C['reset']} for {args.minutes} minutes"); return 0
    if args.action == "check":
        try:
            t = json.load(open(tok, encoding=UTF8))
            if time.time() - t["at"] <= t.get("minutes", 30) * 60:
                return 0
        except (OSError, ValueError, KeyError):
            pass
        sys.stderr.write("agent-orchestrator: push refused — pushing is a human decision. Run `ao push allow` and push again.\n")
        return 1
    try:
        t = json.load(open(tok, encoding=UTF8)); left = t.get("minutes", 30) * 60 - (time.time() - t["at"])
        print(f"push window: {'open, ' + str(int(left // 60)) + 'm left' if left > 0 else 'closed'}")
    except (OSError, ValueError, KeyError):
        print("push window: closed")
    return 0
