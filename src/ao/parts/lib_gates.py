"""Measurement and gates: unfiltered git, size, gate coverage, review artefacts, merge checks.

A part of src/ao/lib.py (#44): moved out byte for byte and run in its namespace by `_part`,
where it stood; it is not importable on its own.
"""


# ---- ao's own measurements are not read through a filter (#51) ------------------------

# ELF, Mach-O (fat and thin, either byte order) and PE: a compiled program, not a script.
_NATIVE_MAGIC = (b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xcf\xfa\xed\xfe",
                 b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"MZ")
# What ao treats as evidence; the doctor names them when something could filter them.
MEASUREMENTS = ("candidate size and paths", "git status", "rev-parse", "write-tree", "hash-object",
                "gate output")
_REWRITING_HOOK = re.compile(r"\brtk\b|\bproxy\b|rewrit|compress|condens", re.I)
_GIT_BINARIES = {}


def _native_executable(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    return any(head.startswith(magic) for magic in _NATIVE_MAGIC)


def _find_git_binary():
    explicit = os.environ.get("AO_GIT")
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    names = ("git.exe", "git") if os.name == "nt" else ("git",)
    directories = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and os.path.isabs(d)]
    if os.name == "nt":
        directories.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "cmd"))
    else:
        directories += ["/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin"]
    for directory in directories:
        for name in names:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK) \
                    and _native_executable(os.path.realpath(candidate)):
                return candidate
    return "git"


def git_binary():
    """The git ao measures with (#51).

    A token-saving proxy in an agent's shell reported a 5,844-line staged diff as
    533 lines, and the number nearly sized a decision. ao reads its measurements
    itself, never from an agent's terminal, and it does not take a wrapper for git
    either: AO_GIT when that names an executable, otherwise the first git on PATH,
    then in the system directories, that is a compiled program rather than a
    script in front of one; plain "git" only when there is none.
    """
    key = (os.environ.get("AO_GIT"), os.environ.get("PATH", ""), os.name)
    found = _GIT_BINARIES.get(key)
    if found is None or (found != "git" and not os.path.isfile(found)):
        found = _GIT_BINARIES[key] = _find_git_binary()
    return found


def measured_by():
    """How the measurements in a record were taken: which git, no shell, read by ao (#51)."""
    return {"git": git_binary(), "candidate_via_shell": False, "captured_by": "ao"}


def command_hook_files(root):
    """Settings files whose pre-tool-use hooks can rewrite an agent's shell commands, as adapters declare them (#76)."""
    out = []
    for adapter in package_adapters().values():
        hooks = (adapter.get("directives") or {}).get("command_hooks") or {}
        if hooks.get("format") != "pre-tool-use":
            continue
        for rel in hooks.get("files") or []:
            path = _home_path(rel) if str(rel).startswith("~") else os.path.join(root, *str(rel).split("/"))
            if path not in out:
                out.append(path)
    return out


def measurement_filters(root):
    """What could stand between a number and its source, one sentence each (#51).

    A script named git ahead of any compiled git on PATH, AO_GIT naming nothing
    executable, and a Claude Code PreToolUse hook that rewrites the shell commands
    an agent runs. A hook is recognised by its command, since what it does cannot
    be read without running it.
    """
    import shutil
    found = []
    explicit = os.environ.get("AO_GIT")
    if explicit and not (os.path.isfile(explicit) and os.access(explicit, os.X_OK)):
        found.append(f"AO_GIT names {explicit}, which is not an executable file; ao measures with {git_binary()}")
    first = shutil.which("git")
    if first and not _native_executable(os.path.realpath(first)):
        chosen = git_binary()
        found.append(f"git on PATH is {first}, a script in front of git; " + (
            f"ao measures with {chosen}" if chosen != "git" else
            "no compiled git was found, so ao's own measurements go through it too: set AO_GIT"))
    for path in command_hook_files(root):
        try:
            with open(path, encoding=UTF8) as fh:
                hooks = json.load(fh).get("hooks")
        except (OSError, ValueError, AttributeError):
            continue
        entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            matcher = str(entry.get("matcher") or "*")
            if matcher != "*" and "bash" not in matcher.lower():
                continue
            for hook in entry.get("hooks") or []:
                command = str(hook.get("command") or "") if isinstance(hook, dict) else ""
                if _REWRITING_HOOK.search(command):
                    found.append(f"{path}: the PreToolUse hook `{command}` can rewrite the shell commands an "
                                 f"agent runs, so what an agent reads of {', '.join(MEASUREMENTS)} may be "
                                 "compressed; take such numbers from ao's records, which do not pass through it")
    return found


def _git_output(root, *args, timeout=60):
    """Run git without a shell and return bytes, failing closed on an unreadable tree."""
    try:
        result = subprocess.run(
            [git_binary(), *args], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot measure git tree: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:240]
        raise RuntimeError(f"cannot measure git tree: {detail or 'git failed'}")
    return result.stdout


def _digest_field(digest, label, value):
    """Length-frame one digest field so path/content boundaries cannot collide."""
    digest.update(len(label).to_bytes(2, "big"))
    digest.update(label)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def tree_digest(root, cfg=None):
    """A staging-independent fingerprint of tracked and untracked product state.

    HEAD already identifies every unchanged tracked file. We therefore hash the
    current bytes, type and executable bit of each path that differs from HEAD,
    plus every non-ignored untracked path. The representation is identical before
    and after `git add`, including for newly added files.

    AO's own ledgers, reviews, mail and agent coordination directories are
    deliberately excluded. Verification and review must be able to persist their
    evidence without immediately making that evidence stale.
    """
    cfg = load_config(root) if cfg is None else cfg
    changed = _git_output(
        root, "diff", "--name-only", "-z", "--no-renames", "HEAD", "--"
    ).split(b"\0")
    untracked = _git_output(
        root, "ls-files", "--others", "--exclude-standard", "-z", "--"
    ).split(b"\0")
    paths = {
        os.fsdecode(raw)
        for raw in changed + untracked
        if raw and not _is_coordination_path(os.fsdecode(raw), cfg)
    }

    digest = hashlib.sha256()
    _digest_field(digest, b"format", b"ao-product-tree-v2")
    _digest_field(digest, b"head", _git_output(root, "rev-parse", "HEAD").strip())

    for rel in sorted(paths, key=os.fsencode):
        full = os.path.join(root, rel)
        _digest_field(digest, b"path", os.fsencode(rel))
        try:
            st = os.lstat(full)
        except OSError:
            _digest_field(digest, b"entry", b"missing")
            continue

        if os.path.islink(full):
            _digest_field(digest, b"entry", b"symlink")
            _digest_field(digest, b"target", os.fsencode(os.readlink(full)))
            continue
        if os.path.isfile(full):
            content = hashlib.sha256()
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    content.update(chunk)
            _digest_field(digest, b"entry", b"file")
            _digest_field(digest, b"executable", b"1" if st.st_mode & 0o111 else b"0")
            _digest_field(digest, b"content", content.digest())
            continue
        if os.path.isdir(full):
            # A directory here is normally a changed gitlink. Recurse so a dirty
            # submodule is measured by content rather than by index state.
            try:
                sub = tree_digest(full, {})
            except RuntimeError:
                sub = "unreadable-directory"
            _digest_field(digest, b"entry", b"directory")
            _digest_field(digest, b"content", sub.encode(UTF8, "surrogateescape"))
            continue
        _digest_field(digest, b"entry", f"special:{st.st_mode}".encode())

    return "sha256:" + digest.hexdigest()


CANDIDATE_FORMAT = "ao-index-candidate-v1"
REVIEW_EVIDENCE_PREFIX = "<!-- ao-evidence: "


def index_candidate(root):
    """Measure the exact tree Git would commit from the active index.

    ``git write-tree`` honors ``GIT_INDEX_FILE``, so this also measures the
    temporary index exposed by ``git commit -a`` and path-limited commits. The
    resulting tree object is immutable even if the live index changes later.
    """
    head = _git_output(root, "rev-parse", "HEAD").strip()
    index_tree = _git_output(root, "write-tree").strip()
    if not head or not index_tree:
        raise RuntimeError("cannot measure index candidate: missing object id")
    head_s = head.decode("ascii", "strict")
    tree_s = index_tree.decode("ascii", "strict")
    status = _git_output(
        root, "diff-tree", "-r", "--no-commit-id", "--name-status", "-z",
        "--no-renames", head_s, tree_s, "--",
    )
    names = _git_output(
        root, "diff-tree", "-r", "--no-commit-id", "--name-only", "-z",
        "--no-renames", head_s, tree_s, "--",
    )
    changed_paths = sorted(
        {os.fsdecode(raw) for raw in names.split(b"\0") if raw}, key=os.fsencode
    )
    digest = hashlib.sha256()
    _digest_field(digest, b"format", CANDIDATE_FORMAT.encode("ascii"))
    _digest_field(digest, b"head", head)
    _digest_field(digest, b"index-tree", index_tree)
    _digest_field(digest, b"status", status)
    return {
        "format": CANDIDATE_FORMAT,
        "digest": "sha256:" + digest.hexdigest(),
        "head": head_s,
        "index_tree": tree_s,
        "changed_paths": changed_paths,
        "changed_count": len(changed_paths),
        "status_digest": "sha256:" + hashlib.sha256(status).hexdigest(),
    }


def _normal_scope_paths(paths):
    out = []
    for value in paths or []:
        raw = str(value or "").replace("\\", "/")
        if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
            raise ValueError(f"scope path must be repository-relative: {value}")
        normal = os.path.normpath(raw).replace("\\", "/")
        if normal in ("", ".", "..") or normal.startswith("../"):
            raise ValueError(f"scope path escapes the repository: {value}")
        if normal not in out:
            out.append(normal)
    return sorted(out, key=os.fsencode)


def _path_in_scope(path, scope_paths):
    return any(path == base or path.startswith(base.rstrip("/") + "/")
               for base in scope_paths)


def candidate_scope(candidate, paths=None):
    """Bind a literal review scope to a candidate, rejecting staged extras."""
    scope_paths = _normal_scope_paths(paths)
    kind = "paths" if scope_paths else "full-index"
    outside = [p for p in candidate["changed_paths"]
               if scope_paths and not _path_in_scope(p, scope_paths)]
    digest = hashlib.sha256()
    _digest_field(digest, b"format", b"ao-candidate-scope-v1")
    _digest_field(digest, b"candidate", candidate["digest"].encode("ascii"))
    _digest_field(digest, b"kind", kind.encode("ascii"))
    for path in scope_paths:
        _digest_field(digest, b"path", os.fsencode(path))
    return {
        "kind": kind,
        "paths": scope_paths,
        "digest": "sha256:" + digest.hexdigest(),
        "outside_paths": outside,
    }


def candidate_diff(root, candidate, scope=None):
    """Render the immutable staged candidate, never the mutable worktree."""
    scope = scope or candidate_scope(candidate)
    args = [
        "--literal-pathspecs", "diff-tree", "-p", "--binary", "--full-index",
        "--no-ext-diff", "--no-renames", "--no-commit-id",
        candidate["head"], candidate["index_tree"], "--",
    ]
    args.extend(scope.get("paths") or [])
    return _git_output(root, *args, timeout=60)


# ---- size is a tripwire that asks a question, not a gate that reshapes work (#34) ------

SIZE_KINDS = ("product", "tests", "fixtures", "generated", "deletion")
_GENERATED_PATH = re.compile(r"(^|/)(dist|build|generated|__generated__|vendor|node_modules)/|\.min\.(js|css)$|"
                             r"\.pb\.go$|_pb2\.py$|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|"
                             r"poetry\.lock|Cargo\.lock|go\.sum|uv\.lock|Gemfile\.lock)$")
_FIXTURE_PATH = re.compile(r"(^|/)(fixtures?|testdata|__snapshots__|snapshots)/|\.snap$")


def size_kind(path):
    """What a changed path is, for counting: generated, fixtures, tests or product."""
    path = str(path).replace("\\", "/")
    if _GENERATED_PATH.search(path):
        return "generated"
    if _FIXTURE_PATH.search(path):
        return "fixtures"
    name = path.rsplit("/", 1)[-1]
    if _is_test_path(path) or "__tests__" in path.split("/") or "spec" in path.split("/")[:-1] \
            or re.search(r"_test\.go$|\.(test|spec)\.[cm]?[jt]sx?$", name):
        return "tests"
    return "product"


def candidate_size(root, candidate):
    """The staged candidate's size by kind and by path, never as one number (#34).

    A single total conflated 400 lines of fixtures with 400 lines of concurrency.
    Lines are what git counts between HEAD and the pinned index tree; a file
    whose change only removes lines is a deletion, whatever its path.
    """
    listed = _git_output(root, "diff-tree", "-r", "--numstat", "-z", "--no-renames",
                         candidate["head"], candidate["index_tree"])
    kinds = {kind: {"paths": 0, "added": 0, "deleted": 0} for kind in SIZE_KINDS}
    for entry in listed.split(b"\0"):
        added, _, rest = entry.partition(b"\t")
        deleted, _, path = rest.partition(b"\t")
        if not path:
            continue
        plus = int(added) if added.isdigit() else 0
        minus = int(deleted) if deleted.isdigit() else 0
        kind = "deletion" if plus == 0 and minus > 0 else size_kind(os.fsdecode(path))
        kinds[kind]["paths"] += 1
        kinds[kind]["added"] += plus
        kinds[kind]["deleted"] += minus
    return {"paths": sum(k["paths"] for k in kinds.values()), "kinds": kinds}


def size_text(size):
    """One line naming each kind that changed: paths and lines, product first."""
    parts = [f"{kind} {k['paths']} path(s) +{k['added']}/-{k['deleted']}"
             for kind, k in size["kinds"].items() if k["paths"]]
    return ", ".join(parts) or "nothing changed"


def size_tripwire(cfg, size):
    """Where a candidate's size stands against the guideline, and what that asks for (#34).

    Within it, nothing. Over it, a question: the boundary must say why the slice
    is one invariant that cannot be split without leaving a seam unreviewed, and
    that statement goes to the reviewer. Only far above it, where no review is
    credible at any length, a refusal. Generated files and pure deletions count
    toward no limit.
    """
    lines = size["kinds"]["product"]["added"] + size["kinds"]["product"]["deleted"]
    paths = sum(size["kinds"][kind]["paths"] for kind in ("product", "tests", "fixtures"))
    guide_lines = settings.get(cfg, "size.guideline_product_lines")
    guide_paths = settings.get(cfg, "size.guideline_paths")
    refuse_lines = settings.get(cfg, "size.refuse_product_lines")
    measured = f"{lines} product line(s) across {paths} path(s)"
    guideline = f"the guideline is {guide_lines} product lines and {guide_paths} paths"
    if lines > refuse_lines:
        return {"state": "refuse", "lines": lines, "paths": paths, "overshoot_pct": None,
                "text": f"{measured} is far above what a review can credibly judge ({refuse_lines} product "
                        f"lines, size.refuse_product_lines); {guideline}. Split it."}
    if lines <= guide_lines and paths <= guide_paths:
        return {"state": "within", "lines": lines, "paths": paths, "overshoot_pct": 0, "text": measured}
    overshoot = max(100 * (lines - guide_lines) / guide_lines, 100 * (paths - guide_paths) / guide_paths)
    return {"state": "over", "lines": lines, "paths": paths, "overshoot_pct": round(overshoot),
            "text": f"{measured} is over the guideline ({guideline})"}


def one_slice_statement(item, boundary=None):
    """Why an oversized slice is one invariant, as its boundary states it (#34): a file section or `one slice:`."""
    if boundary and (boundary.get("sections") or {}).get("why one slice", "").strip():
        return boundary["sections"]["why one slice"].strip()
    return ((((item or {}).get("notes") or {}).get("one slice")) or "").strip()


REVIEW_CONTEXT_BUDGET = 100_000


def _is_test_path(path):
    parts = str(path).replace("\\", "/").split("/")
    name = parts[-1]
    return (any(part in ("tests", "test") for part in parts[:-1])
            or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py")


def _review_definitions(root, rev, path, cache):
    """Top-level definitions of one committed Python file: (lines, name -> (start, end))."""
    import ast
    if path not in cache:
        cache[path] = None
        try:
            source = _git_output(root, "show", f"{rev}:{path}", timeout=30).decode(UTF8, "replace")
            tree = ast.parse(source)
        except (RuntimeError, SyntaxError, ValueError):
            return None
        spans = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                spans[node.name] = (min([node.lineno] + [d.lineno for d in node.decorator_list]),
                                    node.end_lineno)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(target, ast.Name):
                        spans[target.id] = (node.lineno, node.end_lineno)
        cache[path] = (source.splitlines(), spans)
    return cache[path]


def review_context(root, paths, read_rev, context_rev, budget=REVIEW_CONTEXT_BUDGET):
    """Committed source a test-only candidate exercises, for the reviewer to read (#97).

    The candidate is what may land; this is what it is judged against. None of it
    enters the diff or the candidate digest, so supplying it widens the authority
    binding by nothing. Context is attached only when every Python file in the
    candidate is a test: a test cannot be judged without the code it runs, while
    a source change carries its own subject.

    The tests are read at ``read_rev`` (the index tree for a staged candidate) and
    the source at ``context_rev``. A definition the tests name through an import -
    an attribute of an imported module, an imported name, or a name handed to
    ``setattr`` as a string - is copied whole, in the order the tests first name
    it, while the budget lasts. What does not fit is named, never cut, and the
    whole text stays within the budget unless the names alone exceed it. Python only.
    """
    import ast
    py = [path for path in paths or [] if path.endswith(".py")]
    if not py or not all(_is_test_path(path) for path in py):
        return None
    try:
        listing = _git_output(root, "ls-tree", "-r", "--name-only", "-z", context_rev, "--")
    except RuntimeError:
        return None
    committed = {os.fsdecode(raw) for raw in listing.split(b"\0") if raw}
    refs = []
    for path in py:
        try:
            tree = ast.parse(_git_output(root, "show", f"{read_rev}:{path}", timeout=30))
        except (RuntimeError, SyntaxError, ValueError):
            continue
        modules, names = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        modules[alias.asname] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    bound = alias.asname or alias.name
                    modules[bound] = f"{node.module}.{alias.name}"
                    names[bound] = (node.module, alias.name)
        for node in ast.walk(tree):
            at = (path, getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in modules):
                refs.append((at, modules[node.value.id], node.attr))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names:
                refs.append((at, *names[node.id]))
            elif (isinstance(node, ast.Call) and len(node.args) >= 2
                  and isinstance(node.args[0], ast.Name) and node.args[0].id in modules
                  and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                refs.append((at, modules[node.args[0].id], node.args[1].value))
    cache, seen, order, blocks = {}, set(), [], {}
    for _, module, name in sorted(refs):
        qualified = f"{module}.{name}"
        if qualified in seen:
            continue
        seen.add(qualified)
        base = module.replace(".", "/")
        path = next((candidate for candidate in (f"src/{base}.py", f"{base}.py",
                                                 f"src/{base}/__init__.py", f"{base}/__init__.py")
                     if candidate in committed), None)
        found = _review_definitions(root, context_rev, path, cache) if path else None
        if not found or name not in found[1]:
            continue
        start, end = found[1][name]
        order.append(qualified)
        blocks[qualified] = (path, f"# {path}:{start}-{end}  {qualified}\n"
                                   + "\n".join(found[0][start - 1:end]) + "\n")
    files = list(dict.fromkeys(blocks[qualified][0] for qualified in order))

    def render(kept):
        omitted = [qualified for qualified in order if qualified not in kept]
        lines = [f"committed at {context_rev[:12]}; read-only; not under review",
                 "files: " + (", ".join(files) or "none resolved from the tests' imports")]
        if omitted:
            shown = []
            for qualified in omitted:
                if sum(len(name) + 2 for name in shown) + len(qualified) > 2_000:
                    break
                shown.append(qualified)
            more = len(omitted) - len(shown)
            lines.append("not inlined for size, read them from the committed tree: "
                         + ", ".join(shown) + (f" and {more} more" if more else ""))
        return {"rev": context_rev, "paths": files, "names": list(kept), "omitted": omitted,
                "text": "\n".join(lines) + "".join("\n\n" + blocks[q][1] for q in kept)}

    kept, spent = [], 0
    for qualified in order:
        size = len(blocks[qualified][1].encode(UTF8)) + 2
        if spent + size <= budget:
            kept.append(qualified)
            spent += size
    result = render(kept)
    while kept and len(result["text"].encode(UTF8)) > budget:
        kept.pop()
        result = render(kept)
    return result


def review_range_context(root, commits, budget=REVIEW_CONTEXT_BUDGET):
    """The same context for a retrospective range, read at the range's end commit."""
    if ".." not in commits:
        return None
    end = commits.split("..", 1)[1].lstrip(".") or "HEAD"
    if end.startswith("-"):
        return None
    try:
        rev = _git_output(root, "rev-parse", "--verify", "--quiet",
                          end + "^{commit}").decode("ascii").strip()
        names = _git_output(root, "diff", "--name-only", "-z", "--no-renames", commits, "--")
    except (RuntimeError, UnicodeError):
        return None
    paths = sorted({os.fsdecode(raw) for raw in names.split(b"\0") if raw}, key=os.fsencode)
    return review_context(root, paths, rev, rev, budget)


def review_context_line(context):
    files = review_header_value(", ".join(context["paths"])) \
        or "none resolved from the tests' imports"
    line = (f"- context: read-only at `{context['rev'][:12]}`: {files}; "
            f"{len(context['names'])} definitions")
    if context["omitted"]:
        line += f", {len(context['omitted'])} not inlined for size"
    return line


# ---- gate coverage: a source tree no gate exercises (#5) ------------------------------

TOOLCHAINS = {
    "python": {"ext": (".py",), "runners": ("pytest", "python", "python3", "ruff", "mypy", "tox", "uv")},
    "node": {"ext": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
             "runners": ("npm", "npx", "pnpm", "yarn", "node", "vitest", "jest", "tsc", "bun", "deno")},
    "dotnet": {"ext": (".cs", ".fs", ".vb"), "runners": ("dotnet", "msbuild")},
    "go": {"ext": (".go",), "runners": ("go",)},
    "rust": {"ext": (".rs",), "runners": ("cargo",)},
    "jvm": {"ext": (".java", ".kt", ".scala"), "runners": ("mvn", "gradle", "gradlew", "sbt")},
    "swift": {"ext": (".swift",), "runners": ("swift", "xcodebuild")},
}


def source_trees(root, minimum):
    """{top-level tree: {toolchain: files}} for every tree holding at least `minimum` of one toolchain's files.

    Tracked and untracked-but-not-ignored files, as git lists them; files at the top
    level count as the tree ".".
    """
    try:
        listed = _git_output(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    except RuntimeError:
        return {}
    counts = {}
    for raw in listed.split(b"\0"):
        path = os.fsdecode(raw)
        if not path:
            continue
        tree = path.split("/", 1)[0] if "/" in path else "."
        chain = next((name for name, spec in TOOLCHAINS.items() if path.endswith(spec["ext"])), None)
        if chain:
            counts.setdefault(tree, {}).setdefault(chain, 0)
            counts[tree][chain] += 1
    return {tree: {chain: n for chain, n in chains.items() if n >= minimum}
            for tree, chains in counts.items() if any(n >= minimum for n in chains.values())}


def _gate_covers(gate, tree, chain):
    """Whether one gate exercises a toolchain's files in one tree: it runs that toolchain's
    runner and, when it declares inputs, one of them reaches into the tree."""
    import fnmatch
    run = str((gate or {}).get("run") or "")
    runners = TOOLCHAINS[chain]["runners"]
    if not any(re.search(rf"(?:^|[\s/;&|(]){re.escape(runner)}(?:$|[\s.;&|)])", run) for runner in runners):
        return False
    inputs = (gate or {}).get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return True
    probe = "x" + TOOLCHAINS[chain]["ext"][0] if tree == "." else f"{tree}/x{TOOLCHAINS[chain]['ext'][0]}"
    return any(fnmatch.fnmatchcase(probe, str(glob)) or str(glob).startswith(f"{tree}/") for glob in inputs)


def gate_coverage_gaps(root, spec, minimum, names=None):
    """(tree, toolchain, files) for every tree's toolchain that no gate - or none of `names` - exercises."""
    gates = (spec or {}).get("gates") or {}
    chosen = [gate for name, gate in gates.items() if names is None or name in names]
    gaps = []
    for tree, chains in sorted(source_trees(root, minimum).items()):
        for chain, files in sorted(chains.items()):
            if not any(_gate_covers(gate, tree, chain) for gate in chosen):
                gaps.append((tree, chain, files))
    return gaps


def gate_inputs(root):
    """The paths the declared gates read, as globs, or None for the whole tree (#16).

    A gate may name its `inputs`. Only when every gate in `.ao/gates.json` does can
    a dirty path outside all of them be known not to reach a gate; one gate without
    inputs reads the whole tree, and so does a file that cannot be read.
    """
    try:
        with open(os.path.join(root, ".ao", "gates.json"), encoding=UTF8) as fh:
            spec = json.load(fh)
    except (OSError, ValueError):
        return None
    gates = spec.get("gates") if isinstance(spec, dict) else None
    if not isinstance(gates, dict) or not gates:
        return None
    globs = set()
    for gate in gates.values():
        declared = gate.get("inputs") if isinstance(gate, dict) else None
        if not isinstance(declared, list) or not declared \
                or not all(isinstance(item, str) and item.strip() for item in declared):
            return None
        globs.update(item.strip() for item in declared)
    return sorted(globs)


def candidate_worktree_issues(root, cfg, candidate=None, inputs="declared"):
    """State that would make gates execute bytes other than the staged candidate.

    Only a dirty path that is part of the candidate or that a gate reads refuses (#16).
    On 2026-09-06 seven untracked files outside the slice withheld commit authority
    until a person moved them. A path no gate reads is listed as `worktree_noise`,
    never fatal; while any gate declares no inputs, every dirty path counts.
    """
    candidate = candidate or index_candidate(root)
    if inputs == "declared":
        inputs = gate_inputs(root)
    unstaged = {
        os.fsdecode(raw)
        for raw in _git_output(
            root, "diff", "--name-only", "-z", "--no-renames", "--"
        ).split(b"\0")
        if raw and not _is_coordination_path(os.fsdecode(raw), cfg)
    }
    untracked = {
        os.fsdecode(raw)
        for raw in _git_output(
            root, "ls-files", "--others", "--exclude-standard", "-z", "--"
        ).split(b"\0")
        if raw and not _is_coordination_path(os.fsdecode(raw), cfg)
    }
    coordination = {
        path for path in candidate["changed_paths"] if _is_coordination_path(path, cfg)
    }
    if inputs is None:
        noise = set()
    else:
        import fnmatch
        mine = set(candidate["changed_paths"])
        noise = {path for path in unstaged | untracked
                 if path not in mine and not any(fnmatch.fnmatchcase(path, glob) for glob in inputs)}
    return {
        "unstaged": sorted(unstaged - noise, key=os.fsencode),
        "untracked": sorted(untracked - noise, key=os.fsencode),
        "staged_coordination": sorted(coordination, key=os.fsencode),
        "worktree_noise": sorted(noise, key=os.fsencode),
    }


def candidate_issue_messages(issues):
    labels = (
        ("unstaged", "unstaged product changes differ from the index"),
        ("untracked", "untracked product files are outside the index"),
        ("staged_coordination", "coordination paths must not share a product commit"),
    )
    return [f"{label}: {', '.join(issues[key])}" for key, label in labels
            if issues.get(key)]


def review_evidence(body):
    """Structured evidence embedded in a human-readable review artifact."""
    for line in str(body or "").splitlines():
        if line.startswith(REVIEW_EVIDENCE_PREFIX) and line.endswith(" -->"):
            try:
                value = json.loads(line[len(REVIEW_EVIDENCE_PREFIX):-4])
            except (TypeError, ValueError):
                return None
            return value if isinstance(value, dict) else None
    return None


def review_evidence_line(value):
    return REVIEW_EVIDENCE_PREFIX + json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ) + " -->"


def review_header_value(value):
    """One header value on one line, whatever the implementer or the config supplied (#55).

    A review artefact's header echoes the boundary, the commit range, labels and
    file names. A value holding a line break, or any other character
    ``str.splitlines`` breaks on (``\\x1c``, ``\\x85``, ``\\u2028`` ...), would start a
    line of its own, and a line reading ``VERDICT: APPROVED`` there is a verdict to
    every reader. Such a value is written as a JSON string; a printable one as it is.
    """
    text = str(value)
    return text if text.isprintable() else json.dumps(text, ensure_ascii=True)


REVIEWER_OUTPUT_HEADING = "## Reviewer output, verbatim"
REVIEWER_OUTPUT_INDENT = "    "


def review_verbatim_lines(text):
    """The reviewer's own output for the artefact, below ao's adjudication (#54).

    Indented, so none of its lines starts at the margin, where the verdict and the
    evidence line are read. It is split with the ``splitlines`` those readers use,
    so a separator inside a line cannot lift the rest of it back to the margin.
    """
    return ["", REVIEWER_OUTPUT_HEADING, ""] + [
        REVIEWER_OUTPUT_INDENT + line if line else ""
        for line in str(text or "").splitlines()
    ]


def candidate_review_integrity(root, candidate, evidence):
    """Return the canonical reviewed scope and any exact-candidate refusals."""
    reasons = []
    if not isinstance(evidence, dict):
        return None, ["review evidence is not a structured object"]
    if evidence.get("candidate") != candidate:
        reasons.append(
            "review evidence candidate does not exactly match the current index candidate"
        )

    recorded_scope = evidence.get("scope")
    if not isinstance(recorded_scope, dict):
        reasons.append("review evidence has no structured candidate scope")
        return None, reasons
    paths = recorded_scope.get("paths")
    if not isinstance(paths, list):
        reasons.append("review evidence candidate scope paths must be a list")
        return None, reasons
    try:
        scope = candidate_scope(candidate, paths)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        reasons.append(f"review evidence has an invalid candidate scope: {exc}")
        return None, reasons

    if scope["outside_paths"]:
        reasons.append(
            "review evidence scope excludes staged paths: "
            + ", ".join(scope["outside_paths"])
        )
    if recorded_scope != scope:
        reasons.append(
            "review evidence scope does not exactly match the current candidate"
        )

    try:
        diff_digest = "sha256:" + hashlib.sha256(
            candidate_diff(root, candidate, scope)
        ).hexdigest()
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, UnicodeError) as exc:
        reasons.append(f"review evidence candidate diff cannot be reproduced: {exc}")
    else:
        if evidence.get("diff_digest") != diff_digest:
            reasons.append(
                "review evidence diff digest does not match the exact candidate diff: "
                f"expected {diff_digest}, got {evidence.get('diff_digest') or '<missing>'}"
            )
    return scope, reasons


REVIEW_CHAIN = "ao-review-row-v1"


def review_requests_dir(root):
    return os.path.join(root, ".ao", "review-requests")


STANDIN_LIMITS = (
    "ao cannot verify which model wrote this answer; it records the model the person declared",
    "a same-user actor could have written the answer; the nonce binds it to one candidate, nothing more",
)


def write_review_request(root, candidate, scope, diff_digest, boundary, slice_id, paths, prompt):
    """A review request a person can carry to a session ao cannot reach (#75).

    Written when no reviewer could be reached for a staged candidate: the exact
    prompt the reviewer would have received, and a nonce the answer must lead with.
    The metadata beside it binds the request to that candidate.
    """
    import secrets
    from .storage import replace_file_durably
    nonce = secrets.token_hex(16)
    directory = review_requests_dir(root)
    meta = {"nonce": nonce, "at": int(time.time()), "candidate": candidate["digest"],
            "index_tree": candidate["index_tree"], "scope": scope, "diff_digest": diff_digest,
            "boundary": boundary, "slice": slice_id, "paths": paths, "collected": None}
    path = os.path.join(directory, f"{nonce}.md")
    replace_file_durably(os.path.join(directory, f"{nonce}.json"),
                         json.dumps(meta, indent=1, sort_keys=True).encode(UTF8))
    text = (f"# ao review request {nonce}\n\n"
            "No reviewer could be reached for the staged candidate below. Paste everything\n"
            "under the line into a session running a different model family from the\n"
            "implementer's, save its whole answer to a file, and then a person runs:\n\n"
            f"    ao collect-review {nonce} --response <file> --model <the model that answered> --by <your name>\n\n"
            f"The request binds to candidate `{candidate['digest']}`; if the staged bytes change,\n"
            "it no longer applies.\n\n---\n\n"
            f"Cevabının İLK satırı tam olarak şu olsun: NONCE: {nonce}\n\n{prompt}\n")
    replace_file_durably(path, text.encode(UTF8))
    return dict(meta, path=path)


def review_request(root, nonce):
    """A request's metadata by its nonce, or None."""
    if not re.fullmatch(r"[0-9a-f]{32}", str(nonce or "")):
        return None
    try:
        with open(os.path.join(review_requests_dir(root), f"{nonce}.json"), encoding=UTF8) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("nonce") == nonce else None


def mark_review_request_collected(root, nonce, artefact, by):
    """A request answers once: record which artefact its answer became and who carried it."""
    from .storage import replace_file_durably
    meta = review_request(root, nonce)
    if meta is None:
        return None
    meta["collected"] = {"at": int(time.time()), "artefact": artefact, "by": by}
    replace_file_durably(os.path.join(review_requests_dir(root), f"{nonce}.json"),
                         json.dumps(meta, indent=1, sort_keys=True).encode(UTF8))
    return meta


def review_ledger_path(root):
    return os.path.join(root, ".ao", "ledger", "reviews.jsonl")


def review_artefact_name(root, reviews_dir, head):
    """A review artefact name no earlier review has, so no record points at a rewritten file."""
    stem = f"{datetime.now():%Y-%m-%d-%H%M%S}-{head}"
    name, n = f"{stem}.md", 2
    while os.path.lexists(os.path.join(root, reviews_dir, name)):
        name, n = f"{stem}-{n}.md", n + 1
    return name


def record_review(root, name, data, evidence, verdict, reviewer=None, fallback=False):
    """Append one review to the chained review ledger: which file, which bytes, what it decided (#63)."""
    from .storage import append_chained_jsonl
    evidence = evidence if isinstance(evidence, dict) else {}
    candidate = evidence.get("candidate") if isinstance(evidence.get("candidate"), dict) else {}
    row = {
        "at": int(time.time()),
        "artefact": name,
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "kind": evidence.get("kind"),
        "candidate": candidate.get("digest"),
        "verdict": verdict,
        "authorizable": evidence.get("authorizable") is True,
        "fallback": bool(fallback),
        "reviewer": reviewer,
        "slice": evidence.get("slice"),
    }
    if row["kind"] == "commit-range":
        row["commits"] = evidence.get("commits")
    return append_chained_jsonl(review_ledger_path(root), row, REVIEW_CHAIN)


def write_review_artefact(root, reviews_dir, name, text, *, evidence, verdict,
                          reviewer=None, fallback=False):
    """Record a review, then write exactly the recorded bytes, whole (#63, #65).

    The row comes first. A write that dies after it leaves a newest review that
    cannot be read, and that refuses; written first, a file whose row was never
    appended left the older recorded approval of the candidate deciding (audit).
    """
    from .storage import replace_file_durably
    directory = os.path.join(root, reviews_dir)
    os.makedirs(directory, exist_ok=True)
    data = scan_evidence(text)[0].encode(UTF8)          # scanned before it is recorded or written (#48)
    record_review(root, name, data, evidence, verdict, reviewer=reviewer, fallback=fallback)
    replace_file_durably(os.path.join(directory, name), data)


# ---- review artefacts: kept while anything rests on them, pruned by age (#38) ----------

# Files whose text names the review artefacts they rest on, and what each one is.
REVIEW_REFERENCE_FILES = (
    ("grant", (".ao", "ledger", "authority.jsonl")),
    ("verification", (".ao", "ledger", "verifications.jsonl")),
    ("board", (".ao", "board.md")),
    ("waiver", (".ao", "ledger", "waivers.jsonl")),
    ("decision", (".ao", "ledger", "decisions.jsonl")),
)


# ---- a merge is verified on its result, before it is made (#39) ------------------------

MERGE_CHAIN = "ao-merge-check-row-v1"


def merge_checks_path(root):
    return os.path.join(root, ".ao", "ledger", "merges.jsonl")


def merge_checks(root):
    """Recorded runs of merge results, oldest first; raises on a broken chain."""
    from .storage import read_chained_jsonl
    return [row for row in read_chained_jsonl(merge_checks_path(root), MERGE_CHAIN) if isinstance(row, dict)]


def record_merge_check(root, row):
    from .storage import append_chained_jsonl
    return append_chained_jsonl(merge_checks_path(root), scan_record(row), MERGE_CHAIN)


def merge_result_tree(root, into, branch):
    """(tree, conflict): the tree `git merge` would make of two commits, computed without touching a worktree.

    RuntimeError when git cannot compute it at all (merge-tree --write-tree needs git 2.38).
    """
    try:
        result = subprocess.run([git_binary(), "merge-tree", "--write-tree", "--name-only", "--no-messages",
                                 into, branch], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot compute the merge: {exc}") from exc
    lines = result.stdout.decode(UTF8, "replace").splitlines()
    if result.returncode == 0 and lines:
        return lines[0].strip(), None
    if result.returncode == 1 and lines:
        return None, "conflicts in " + ", ".join(line.strip() for line in lines[1:] if line.strip())
    detail = result.stderr.decode(UTF8, "replace").strip()
    raise RuntimeError(f"cannot compute the merge: {detail or f'git merge-tree exit {result.returncode}'}")


def unverified_merges(root, cfg):
    """(merge commit, why) for each recent merge on HEAD's first-parent line no passing run vouches for (#39).

    A run vouches for a merge only when it names both parents and the tree the
    merge made, so a run of an older pair, or of a result that was changed while
    merging, vouches for nothing.
    """
    days = settings.get(cfg, "merge.check_days")
    log = git_text(root, "log", "--merges", "--first-parent", f"--since={days}.days", "--format=%H %T %P", "HEAD")
    runs = {(row.get("into"), row.get("branch"), row.get("tree")): row for row in merge_checks(root)}
    out = []
    for line in log.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        sha, tree, first, second = parts[:4]
        run = runs.get((first, second, tree))
        if run is None:
            out.append((sha, "no run of its merge result was recorded"))
        elif run.get("passed") is not True:
            out.append((sha, f"the recorded run of its merge result, {run.get('id')}, failed"))
    return out


def review_artefact_names(root, reviews_dir):
    """The review artefacts on disk: the files in the reviews directory, dotfiles aside."""
    directory = os.path.join(root, reviews_dir)
    try:
        return sorted(name for name in os.listdir(directory)
                      if not name.startswith(".") and os.path.isfile(os.path.join(directory, name)))
    except FileNotFoundError:
        return []


def tracked_review_names(root, reviews_dir):
    """The review artefacts git tracks, by name; RuntimeError when git cannot say."""
    listed = _git_output(root, "ls-files", "-z", "--", reviews_dir)
    return {os.path.basename(os.fsdecode(path)) for path in listed.split(b"\0") if path}


def review_artefact_references(root, cfg):
    """{artefact: [what rests on it]} for every review artefact ao must keep (#38).

    A grant, a verification, the board, a waiver or a decision that names the
    file; a submitted review's state; a review of a slice that is neither done nor
    rejected, or of the candidate staged now; and a file git tracks, which moving
    would turn into a deletion in the tree. The review ledger is the catalogue of
    every review, not a reason to keep one. Whatever cannot be read raises, so
    nothing is pruned on a guess.
    """
    from .storage import read_chained_jsonl
    names = review_artefact_names(root, cfg["reviews"])
    kept = {}

    def keep(name, why):
        reasons = kept.setdefault(name, [])
        if why not in reasons:
            reasons.append(why)

    texts = []
    for why, parts in REVIEW_REFERENCE_FILES:
        try:
            with open(os.path.join(root, *parts), encoding=UTF8, errors="replace") as fh:
                texts.append((why, fh.read()))
        except FileNotFoundError:
            continue
    states = os.path.join(root, ".ao", "reviews")
    for entry in sorted(os.listdir(states)) if os.path.isdir(states) else []:
        if entry.startswith("R-") and entry.endswith(".json"):
            with open(os.path.join(states, entry), encoding=UTF8, errors="replace") as fh:
                texts.append(("submitted review", fh.read()))
    for why, text in texts:
        for name in names:
            if name in text:
                keep(name, why)
    open_slices = {item["id"] for state, items in board(root).items() if state not in ("done", "rejected")
                   for item in items}
    try:
        staged = index_candidate(root)["digest"]
    except RuntimeError:
        staged = None
    for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN):
        name = row.get("artefact") if isinstance(row, dict) else None
        if name not in names:
            continue
        if row.get("slice") in open_slices:
            keep(name, "open slice")
        if staged and row.get("candidate") == staged:
            keep(name, "staged candidate")
    tracked = tracked_review_names(root, cfg["reviews"])
    for name in names:
        if name in tracked:
            keep(name, "tracked in git")
    return kept


def prune_review_artefacts(root, cfg, days, apply=False, now=None):
    """Move the review artefacts nothing rests on, older than `days`, out of the repository (#38).

    Age is the later of the file's time and its ledger row's, so neither alone
    makes a review old. They move to ~/.ao/archive/<project>/, not away: a review
    is evidence someone may still ask for. Returns what was kept and why, how many
    were recent, what was moved (or would be), its bytes and where it went.
    """
    import shutil
    from .storage import read_chained_jsonl
    now = time.time() if now is None else now
    kept = review_artefact_references(root, cfg)
    written = {}
    for row in read_chained_jsonl(review_ledger_path(root), REVIEW_CHAIN):
        if isinstance(row, dict) and row.get("artefact"):
            written[row["artefact"]] = max(written.get(row["artefact"], 0), int(row.get("at") or 0))
    directory = os.path.join(root, cfg["reviews"])
    cutoff = now - days * 86400
    recent, moving, size = 0, [], 0
    for name in review_artefact_names(root, cfg["reviews"]):
        if name in kept:
            continue
        path = os.path.join(directory, name)
        if max(os.path.getmtime(path), written.get(name, 0)) >= cutoff:
            recent += 1
        else:
            moving.append(name)
            size += os.path.getsize(path)
    folder = f"{os.path.basename(os.path.normpath(cfg['reviews']))}-{datetime.fromtimestamp(now):%Y%m%d-%H%M%S}"
    archive = os.path.join(HOME, ".ao", "archive", project_key(root), folder)
    if apply and moving:
        os.makedirs(archive, exist_ok=True)
        for name in moving:
            shutil.move(os.path.join(directory, name), os.path.join(archive, name))
            record_archived(root, name, archive)     # where it went stays on the record (#47)
    return {"kept": kept, "recent": recent, "moved": moving, "bytes": size, "archive": archive}
