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

from . import lib as A
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


def _local(root, rel):
    return os.path.join(root, *str(rel).split("/"))


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


def agent_choices():
    """The names `--agent` accepts: every harness ao sets up, by adapter id and by vendor name."""
    ids = {ident for ident, _ in setup_adapters()}
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
        marks = [path for path in (detect.get("dirs") or []) if os.path.isdir(_local(root, path))]
        marks += [path for path in (detect.get("files") or []) if os.path.exists(_local(root, path))]
        if requested == "all" or marks or any(shutil.which(binary) for binary in detect.get("binaries") or []):
            found.add(ident)
    return None, found or {"generic"}


def _write_marked(path, body, header=""):
    """Write `body` between markers; replace an earlier rendering; keep the rest."""
    block = f"{MARK_START}\n{body.rstrip()}\n{MARK_END}\n"
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
                  for name in (adapter.get("directives") or {}).get("rule_files") or [] if name != SHARED_RULE_FILE})
    return own + [SHARED_RULE_FILE]


def steering_dirs(root=None):
    """Directories whose every file a harness always reads, as the adapters declare them."""
    return sorted({(adapter.get("directives") or {}).get("steering_dir") for _, adapter in setup_adapters(root)} - {None})


def install_playbook(root, agents, rules=False):
    """Render the playbook for each agent. Returns {relpath: wrote|updated|kept|appended}.

    Only files ao owns are written: the file each adapter declares as its playbook
    and `.ao/PLAYBOOK.md`. The owner's rule files sit on the authority boundary: a
    tool that appends instructions to them has, from the reading agent's side,
    issued rules nobody authorised. A coordinator on the second pilot refused
    exactly such lines and escalated them, and was right to. With `rules=True` the
    owner asks for the pointer; otherwise it is printed for them to paste.
    """
    front, body = playbook()
    out = {}
    p = os.path.join(root, ".ao", "PLAYBOOK.md")
    note = ("<!-- No agent reads this file until a rule file points at it. Paste into "
            + " or ".join(rule_file_names(root)) + ": "
            + RULE_POINTER.split("\n\n", 1)[1].replace("\n", " ")
            + " (or run `ao init --rules`). `ao doctor` reports rules-not-wired until then. -->\n\n")
    out[".ao/PLAYBOOK.md"] = _write_marked(p, body, header=note)
    for _, adapter in setup_adapters(root, agents):
        target = (adapter.get("directives") or {}).get("playbook") or {}
        if not target.get("path"):
            continue
        header = front if target.get("header") == "frontmatter" else str(target.get("header") or "")
        out[target["path"]] = _write_marked(_local(root, target["path"]), body, header=header)
    if rules:
        own = any(name != SHARED_RULE_FILE for _, adapter in setup_adapters(root, agents)
                  for name in (adapter.get("directives") or {}).get("rule_files") or [])
        for name in rule_file_names(root):
            p = os.path.join(root, name)
            if os.path.exists(p) or name == SHARED_RULE_FILE and not own:
                out[name] = _write_marked(p, RULE_POINTER, header="# Agents\n\n" if name == SHARED_RULE_FILE else "")
    return out


def register_mcp(root, agents, exe=None):
    """Register the ao MCP server for each agent, in the file its adapter declares.

    Merged, never clobbered: other servers in the same file survive. Returns
    {agent: what happened}. A harness that keeps its servers in a user-level file
    gets the snippet its adapter declares, and the human decides.
    """
    exe = exe or shutil.which("ao") or os.path.join(A.REPO, "bin", "ao")
    args = ["-C", root, "mcp", "serve"]
    server = {"command": exe, "args": args}
    out = {}

    def merge(path, key="mcpServers", extra=None):
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
        if mcp.get("manual"):
            snippet = str(mcp.get("snippet") or "").replace("{exe}", exe).replace("{root}", root)
            out[ident] = f"manual: add to {mcp['manual']}\n{snippet}"
            continue
        if not mcp.get("file"):
            continue
        path = _local(root, mcp["file"])
        register = mcp.get("register") or []
        if register and shutil.which(register[0]) and not os.path.exists(path):
            argv = [part.replace("{exe}", exe).replace("{args}", json.dumps(args)) for part in register]
            r = subprocess.run(argv, capture_output=True, text=True, encoding=UTF8, errors="replace", cwd=root)
            out[ident] = "registered" if r.returncode == 0 else merge(path, mcp.get("key", "mcpServers"), mcp.get("extra"))
        else:
            out[ident] = merge(path, mcp.get("key", "mcpServers"), mcp.get("extra"))
    return out


def ao_files(root=None):
    """(paths, [(mcp file, server, remove when empty)]) ao may have written for any harness, for `ao remove`."""
    paths, mcp_files = [], []
    for _, adapter in setup_adapters(root):
        directives = adapter.get("directives") or {}
        paths += [path for path in directives.get("ao_files") or [] if path not in paths]
        mcp = adapter.get("mcp") or {}
        if mcp.get("file"):
            mcp_files.append((os.path.join(*mcp["file"].split("/")), "ao", bool(mcp.get("remove_when_empty"))))
    return paths, mcp_files


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
        if os.path.isdir(_local(root, steering)):
            candidates += [os.path.join(_local(root, steering), f) for f in os.listdir(_local(root, steering))]
    for _, adapter in setup_adapters(root):
        skills = (adapter.get("directives") or {}).get("skills_dir")
        if skills and os.path.isdir(os.path.join(_local(root, skills), "ao")):
            return True                  # a skill is discovered by the agent on its own
    for p in candidates:
        try:
            if any(n in open(p, encoding="utf-8", errors="replace").read() for n in needles):
                return True
        except OSError:
            continue
    return False
