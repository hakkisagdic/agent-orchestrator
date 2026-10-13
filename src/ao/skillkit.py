"""The playbook, and putting it where the agents read.

`ao init` used to leave a repository with the files and a short steering note;
the rest of how to behave lived in one architect's head. That is the wrong
place for it: the promise of this tool is "add it and hand the project to the
agent already running", and the agent can only keep that promise if it knows
what the architect knows — the roles, the loop, what authority is, how to talk,
how to measure, what never to do, and every command. So the playbook ships
inside the package and is written, on init, in the dialect each agent reads.
One source, one rendering per harness, and a test that fails when a command is
missing from it.

Which harnesses there are, what each leaves in a repository, where its playbook
and its MCP registration go: all of that is declared by the adapters (#76). This
module reads the declarations and names no harness itself.
"""
import json
import os
import re
import shutil
import subprocess

from . import lib as A, matrix as M
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

MARK_START = "<!-- ao-playbook:start -->"
MARK_END = "<!-- ao-playbook:end -->"
SKILL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill", "SKILL.md")
SHARED_RULE_FILE = "AGENTS.md"      # the cross-harness convention; a harness's own rule file is declared


def playbook():
    """(frontmatter, body) of the packaged playbook."""
    text = open(SKILL_PATH, encoding=UTF8).read()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        return text[:end + 5], text[end + 5:].lstrip("\n")
    return "", text


def _escapes(rel):
    """Whether a declared setup path leaves the repository by its words: absolute, a home (`~`), a drive or a `..`."""
    text = str(rel or "").replace("\\", "/")
    return not text or text.startswith(("/", "~")) or text[1:2] == ":" or ".." in text.split("/")


def _local(root, rel):
    """`rel` inside the repository `root`, or None where it would leave it (HARNESS-SETUP-2).

    A setup path is an adapter's word, and the project's adapter layer is one an agent can write: a
    playbook, a rule file or an MCP file declared as `../x` was written outside the project, `ao remove`
    deleted it there, and a home path such as opencode's `~/.config/...` was written into the project as
    a directory named `~`. The path is held to the repository by its words and by where it resolves.
    """
    if _escapes(rel):
        return None
    path = os.path.join(root, *str(rel).split("/"))
    real = os.path.realpath(root)
    try:
        return path if os.path.commonpath([real, os.path.realpath(path)]) == real else None
    except ValueError:
        return None


def setup_adapters(root=None, agents=None):
    """[(id, adapter)] for every adapter that declares how ao is set up in a repository, by id (#76).

    An adapter takes part when it declares a playbook, a coordination file or an MCP
    registration. With `agents`, only those.
    """
    out = []
    for ident, entry in sorted(A.adapter_catalog(root).items()):
        adapter = entry["adapter"]
        if entry["problem"] or (agents is not None and ident not in agents):
            continue
        directives = adapter.get("directives") or {}
        if directives.get("playbook") or directives.get("coordination") or adapter.get("mcp"):
            out.append((ident, adapter))
    return out


def resolve_agent(name, root=None):
    """An adapter id for a name a person typed: the id itself, or a vendor's name for it."""
    for vendor in A.vendor_list():
        if vendor.get("id") == name and vendor.get("adapter"):
            return vendor["adapter"]
    return name


def agent_choices(root=None):
    """The names `--agent` accepts: every harness ao sets up, by adapter id and by vendor name.

    A project's own adapters are among them where `root` names the project: `ao -C <repo> init --agent <id>`
    was refused for an adapter the project declared, though init and skill set it up (HARNESS-SETUP-3).
    """
    ids = {ident for ident, _ in setup_adapters(root)}
    names = ids | {vendor["id"] for vendor in A.vendor_list() if vendor.get("adapter") in ids}
    return sorted(names) + ["auto", "all"]


def detect_agents(root, requested="auto"):
    """Which agents read this repository. Cheap signals, each declared by its adapter: directories, files, binaries."""
    if requested and requested not in ("auto", "all"):
        ident = resolve_agent(requested, root)
        return ident, {ident}
    found = set()
    for ident, adapter in setup_adapters(root):
        detect = adapter.get("detect") or {}
        marks = [path for path in (detect.get("dirs") or []) if os.path.isdir(_local(root, path) or "\0")]
        marks += [path for path in (detect.get("files") or []) if os.path.exists(_local(root, path) or "\0")]
        if requested == "all" or marks or any(A.program_path(binary) for binary in detect.get("binaries") or []):
            found.add(ident)
    return None, found or {"generic"}


def _marked(body, header=""):
    """What `_write_marked` writes into a file of its own: `header`, then `body` between the markers."""
    return f"{header}{MARK_START}\n{body.rstrip()}\n{MARK_END}\n"


def _as_written(text):
    """The bytes a text-mode write of `text` leaves on this platform, as `_write_marked` and the MCP merge write it."""
    return text.replace("\n", os.linesep).encode(UTF8)


def _shipped(ident, adapter, read):
    """Whether the adapter ao ships as `ident` declares what `adapter` does, as `read` takes it from either.

    What ao writes is recorded as seen only where its own declarations say where and in what words: a project's adapter
    layer is a file an agent can write, and a playbook path or header declared there would put an agent's text under
    ao's name (HARNESS-SEEN-OWN).
    """
    shipped = A.package_adapters().get(ident)
    return shipped is not None and read(shipped) == read(adapter)


def _write_marked(path, body, header=""):
    """Write `body` between markers; replace an earlier rendering; keep the rest."""
    block = _marked(body)
    if os.path.exists(path):
        old = open(path, encoding=UTF8, errors="replace").read()
        if MARK_START in old and MARK_END in old:
            new = old[:old.index(MARK_START)] + block + old[old.index(MARK_END) + len(MARK_END):].lstrip("\n")
            if new != old:
                open(path, "w", encoding=UTF8).write(new)
                return "updated"
            return "kept"
        open(path, "a", encoding=UTF8).write("\n\n" + block)
        return "appended"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w", encoding=UTF8).write(header + block)
    return "wrote"


RULE_POINTER = ("## ao\n\nThis repository runs under agent-orchestrator. Load the `ao` playbook "
                "(`.ao/PLAYBOOK.md`, or the `ao` skill) before coordinating, implementing or reviewing "
                "here, and start every turn with `ao status` and the mailbox.")


def rule_file_names(root=None):
    """The owner's rule files: each harness's own, as its adapter declares them, then the shared one."""
    own = sorted({name for _, adapter in setup_adapters(root)
                  for name in (adapter.get("directives") or {}).get("rule_files") or []
                  if name != SHARED_RULE_FILE and not _escapes(name)})
    return own + [SHARED_RULE_FILE]


def steering_dirs(root=None):
    """Directories whose every file a harness always reads, as the adapters declare them."""
    return sorted({(adapter.get("directives") or {}).get("steering_dir") for _, adapter in setup_adapters(root)} - {None})


def install_playbook(root, agents, rules=False, ours=None):
    """Render the playbook for each agent. Returns {relpath: wrote|updated|kept|appended}.

    Only files ao owns are written: the file each adapter declares as its playbook
    and `.ao/PLAYBOOK.md`. The owner's rule files sit on the authority boundary: a
    tool that appends instructions to them has, from the reading agent's side,
    issued rules nobody authorised. A coordinator on the second pilot refused
    exactly such lines and escalated them, and was right to. With `rules=True` the
    owner asks for the pointer; otherwise it is printed for them to paste.

    `ours`, a list when given, gets (path, the bytes ao's rendering leaves there) for each file a shipped adapter
    declares that holds nothing but ao's text when ao has written it, for `A.record_harness_written` to record as seen
    where the file holds exactly that (HARNESS-SEEN-OWN).
    """
    front, body = playbook()
    out = {}
    p = os.path.join(root, ".ao", "PLAYBOOK.md")
    note = ("<!-- No agent reads this file until a rule file points at it. Paste into "
            + " or ".join(rule_file_names(root)) + ": "
            + RULE_POINTER.split("\n\n", 1)[1].replace("\n", " ")
            + " (or run `ao init --rules`). `ao doctor` reports rules-not-wired until then. -->\n\n")
    out[".ao/PLAYBOOK.md"] = _write_marked(p, body, header=note)
    for ident, adapter in setup_adapters(root, agents):
        target = (adapter.get("directives") or {}).get("playbook") or {}
        if not target.get("path"):
            continue
        header = front if target.get("header") == "frontmatter" else str(target.get("header") or "")
        place = _local(root, target["path"])
        out[target["path"]] = _write_marked(place, body, header=header) if place else "not written: outside the project"
        if place and ours is not None \
                and _shipped(ident, adapter, lambda a: (a.get("directives") or {}).get("playbook")):
            ours.append((place, _as_written(_marked(body, header))))
    if rules:
        own = any(name != SHARED_RULE_FILE for _, adapter in setup_adapters(root, agents)
                  for name in (adapter.get("directives") or {}).get("rule_files") or [])
        shipped = {name for adapter in A.package_adapters().values()
                   for name in (adapter.get("directives") or {}).get("rule_files") or []} | {SHARED_RULE_FILE}
        for name in rule_file_names(root):
            p = os.path.join(root, name)
            if os.path.exists(p) or name == SHARED_RULE_FILE and not own:
                header = "# Agents\n\n" if name == SHARED_RULE_FILE else ""
                out[name] = _write_marked(p, RULE_POINTER, header=header)
                # A pointer added to a person's rule file leaves their text beside it: that file is never all ao's.
                if ours is not None and name in shipped:
                    ours.append((p, _as_written(_marked(RULE_POINTER, header))))
    return out


def _toml_string_body(value):
    """`value` escaped as the inside of a double-quoted TOML string (#71).

    A manual snippet quotes `{exe}` and `{root}`, and a TOML basic string reads a
    backslash as an escape: a Windows root, C:\\Users\\..., began with a \\U the parser
    refused. Backslashes and quotes are escaped, and control characters written as
    \\uXXXX, which JSON and YAML double-quoted strings read the same way.
    """
    out = []
    for char in str(value):
        if char in "\\\"":
            out.append("\\" + char)
        elif char < " " or char == "\x7f":
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return "".join(out)


def registration_role(root, ident):
    """The role the ao MCP registration for harness `ident` names in this project, or None (MCP-ROLES).

    Read from the project's role blocks: the roles whose block, or a reviewer's fallback, runs
    a harness that reads the registration. That is every harness whose adapter declares the
    same file, since two harnesses can read one file and a file holds one `ao` entry; a
    registration a person adds by hand is read by its own harness alone. Exactly one role names
    it. Several, or none, name nothing, and the server serves every tool, as before roles
    existed: a registration read by two roles' sessions cannot hold either to its own tools,
    and naming one would take tools from the other. A project on a capability matrix binds its
    roles to declared tools rather than to harnesses, and its registrations name none.
    """
    adapters = dict(setup_adapters(root))
    declared = ((adapters.get(ident) or {}).get("mcp") or {}).get("file")
    readers = {ident} | {other for other, adapter in adapters.items()
                         if declared and (adapter.get("mcp") or {}).get("file") == declared}
    document = A.project_config_document(root)["config"]
    if not isinstance(document, dict) or M.is_strict(document):
        return None
    cfg = A.resolve_roles(root, dict(document))
    held = set()
    for role in A.ROLE_BLOCKS:
        block = cfg.get(role) if isinstance(cfg.get(role), dict) else {}
        runs = [block]
        if role == "reviewer":
            runs += list(block.get("fallbacks") or [])
        if any(A.block_adapter(each) in readers for each in runs):
            held.add(role)
    return held.pop() if len(held) == 1 else None


def _snippet(template, exe, root, args):
    """A manual registration's snippet with `{exe}`, `{root}` and `{args}` filled in one pass.

    One pass, so a value holding a placeholder's spelling is never filled again. `{args}` is the
    server's arguments as a bracketed list of double-quoted strings, each escaped as `{root}` is.
    """
    values = {"exe": _toml_string_body(exe), "root": _toml_string_body(root),
              "args": "[" + ", ".join(f'"{_toml_string_body(arg)}"' for arg in args) + "]"}
    return re.sub(r"\{(exe|root|args)\}", lambda match: values[match.group(1)], str(template or ""))


def register_mcp(root, agents, exe=None, ours=None):
    """Register the ao MCP server for each agent, in the file its adapter declares.

    Merged, never clobbered: other servers in the same file survive. Returns
    {agent: what happened}. A harness that keeps its servers in a user-level file
    gets the snippet its adapter declares, and the human decides. Each registration
    names the role its readers hold in this project, when they hold one (`registration_role`).

    `ours`, a list when given, gets (path, the bytes a file holding ao's entry alone has) for each file a shipped
    adapter declares, as install_playbook's does (HARNESS-SEEN-OWN). One the harness's own `register` command wrote is
    in that harness's words, not ao's, and is left for a person to see.
    """
    # The ao the registration names runs as the person whenever a harness starts it: one named by an absolute path,
    # never one the current directory holds (WINDOWS-NO-CWD-EXEC).
    exe = exe or A.ao_program()
    out = {}

    def merge(path, server, key="mcpServers", extra=None):
        data = {}
        if os.path.exists(path):
            try:
                data = json.load(open(path, encoding=UTF8))
            except ValueError:
                data = {}
        servers = data.setdefault(key, {})
        entry = dict(server, **(extra or {}))
        if servers.get("ao") == entry:
            return "kept"
        servers["ao"] = entry
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        json.dump(data, open(path, "w", encoding=UTF8), indent=2)
        return "registered"

    for ident, adapter in setup_adapters(root, agents):
        mcp = adapter.get("mcp") or {}
        if exe is None and (mcp.get("manual") or mcp.get("file")):
            # A registration naming a program that is not there starts nothing, and says nothing of why (the review
            # of WINDOWS-NO-CWD-EXEC): none is written until ao can be named by an absolute path.
            out[ident] = ("not registered: no ao program to name - none is on PATH and this ao was not started from "
                          "one; put ao on PATH and run ao init again")
            continue
        role = registration_role(root, ident)
        args = ["-C", root, "mcp", "serve"] + (["--role", role] if role else [])
        if mcp.get("manual"):
            out[ident] = f"manual: add to {mcp['manual']}\n{_snippet(mcp.get('snippet'), exe, root, args)}"
            continue
        if not mcp.get("file"):
            continue
        path = _local(root, mcp["file"])
        if path is None:
            out[ident] = f"not written: {mcp['file']} lies outside the project; add ao to it by hand"
            continue
        server = {"command": exe, "args": args}
        # A command ao runs comes from a shipped adapter alone, never a layer an agent can write (HARNESS-SETUP-2).
        register = ((A.package_adapters().get(ident) or {}).get("mcp") or {}).get("register") or []
        program = A.program_path(register[0]) if register else None
        key, extra = mcp.get("key", "mcpServers"), mcp.get("extra")
        if program and not os.path.exists(path):
            argv = [program] + [part.replace("{exe}", exe).replace("{args}", json.dumps(args))
                                for part in register[1:]]
            r = subprocess.run(argv, capture_output=True, text=True, encoding=UTF8, errors="replace", cwd=root)
            if r.returncode == 0:
                out[ident] = "registered"
                continue
        out[ident] = merge(path, server, key, extra)
        if ours is not None and _shipped(ident, adapter, lambda a: a.get("mcp")):
            ours.append((path, _as_written(json.dumps({key: {"ao": dict(server, **(extra or {}))}}, indent=2))))
    return out


def ao_files(root=None):
    """(paths, [(mcp file, server, remove when empty)]) ao may have written for any harness, for `ao remove`.

    The paths are those the adapter the package ships under each id declares. A user's or a project's layer is a
    file the agents it describes can write, and `"ao_files": ["src/"]` there had the removal delete the project's
    source: what a layer names beyond them is layer_ao_files', which the removal names and leaves (REMOVE-SHIPPED).
    An MCP file stays every adapter's, since only the `ao` entry that runs ao's server is taken from it.
    """
    paths, mcp_files = [], []
    shipped = A.package_adapters()
    for ident, adapter in setup_adapters(root):
        own = ((shipped.get(ident) or {}).get("directives") or {}).get("ao_files") or []
        paths += [path for path in own if path not in paths and not _escapes(path)]
        mcp = adapter.get("mcp") or {}
        if mcp.get("file") and not _escapes(mcp["file"]):
            mcp_files.append((os.path.join(*mcp["file"].split("/")), "ao", bool(mcp.get("remove_when_empty"))))
    return paths, mcp_files


def layer_ao_files(root=None):
    """The paths a user's or a project's adapter layer names as ao's and no shipped adapter does: `ao remove` names
    them and deletes none, since an agent can write the layer (REMOVE-SHIPPED)."""
    shipped, _ = ao_files(root)
    out = []
    for _, adapter in setup_adapters(root):
        for path in (adapter.get("directives") or {}).get("ao_files") or []:
            if isinstance(path, str) and path not in shipped and path not in out and not _escapes(path):
                out.append(path)
    return out


def next_steps(agents, registered):
    """What the human has to do now — the one thing the tool cannot do for them."""
    names = [adapter.get("name") or ident for ident, adapter in setup_adapters(None, agents)]
    lines = []
    if names:
        lines.append(f"Start or restart {' / '.join(names)} in this directory so the `ao` MCP "
                     f"tools load (registered: "
                     f"{', '.join(f'{k}: {v.splitlines()[0]}' for k, v in registered.items())}).")
    else:
        lines.append("No MCP-capable agent detected; the mailbox protocol works from the files alone.")
    lines.append("Then `ao doctor` — binaries, channels, watchdog — and `ao email setup` if it says no channel.")
    lines.append("Write the first slice into .ao/backlog.md with an acceptance boundary; the agent takes it from there.")
    return lines


def rules_wired(root):
    """Does any file the agents read point at the playbook?

    True when a rule file mentions the playbook or the skill, None when no
    playbook has been installed (nothing to wire), False when the playbook is
    there and nothing references it — written, not in force.
    """
    if not os.path.exists(os.path.join(root, ".ao", "PLAYBOOK.md")):
        return None
    needles = ("PLAYBOOK.md", "skills/ao", "ao-playbook", "ao-coordination", "agent-orchestrator")
    candidates = [os.path.join(root, name) for name in rule_file_names(root)]
    for steering in steering_dirs(root):
        place = _local(root, steering)
        if place and os.path.isdir(place):
            candidates += [os.path.join(place, f) for f in os.listdir(place)]
    for _, adapter in setup_adapters(root):
        skills = (adapter.get("directives") or {}).get("skills_dir")
        if skills and _local(root, skills) and os.path.isdir(os.path.join(_local(root, skills), "ao")):
            return True                  # a skill is discovered by the agent on its own
    for p in candidates:
        try:
            if any(n in open(p, encoding="utf-8", errors="replace").read() for n in needles):
                return True
        except OSError:
            continue
    return False
