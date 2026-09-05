"""The playbook, and putting it where the agents read.

`ao init` used to leave a repository with the files and a short steering note;
the rest of how to behave lived in one architect's head. That is the wrong
place for it: the promise of this tool is "add it and hand the project to the
agent already running", and the agent can only keep that promise if it knows
what the architect knows — the roles, the loop, what authority is, how to talk,
how to measure, what never to do, and every command. So the playbook ships
inside the package and is written, on init, in the dialect each agent reads:
a Claude Code skill, a Kiro steering file, an AGENTS.md section. One source,
three renderings, and a test that fails when a command is missing from it.
"""
import json
import os
import re
import shutil
import subprocess

from . import lib as A

MARK_START = "<!-- ao-playbook:start -->"
MARK_END = "<!-- ao-playbook:end -->"
SKILL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill", "SKILL.md")


def playbook():
    """(frontmatter, body) of the packaged playbook."""
    text = open(SKILL_PATH, encoding="utf-8").read()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        return text[:end + 5], text[end + 5:].lstrip("\n")
    return "", text


def detect_agents(root, requested="auto"):
    """Which agents read this repository. Cheap signals: their directories, their binaries."""
    if requested and requested not in ("auto", "all"):
        return {"claude": "claude-code"}.get(requested, requested), {{"claude": "claude-code"}.get(requested, requested)}
    found = set()
    if os.path.isdir(os.path.join(root, ".claude")) or os.path.exists(os.path.join(root, ".mcp.json")) \
            or os.path.exists(os.path.join(root, "CLAUDE.md")) or shutil.which("claude"):
        found.add("claude-code")
    if os.path.isdir(os.path.join(root, ".kiro")) or shutil.which("kiro-cli"):
        found.add("kiro")
    if os.path.isdir(os.path.join(root, ".codex")) or shutil.which("codex"):
        found.add("codex")
    if requested == "all":
        found |= {"claude-code", "kiro", "codex"}
    return None, found or {"generic"}


def _write_marked(path, body, header=""):
    """Write `body` between markers; replace an earlier rendering; keep the rest."""
    block = f"{MARK_START}\n{body.rstrip()}\n{MARK_END}\n"
    if os.path.exists(path):
        old = open(path, encoding="utf-8", errors="replace").read()
        if MARK_START in old and MARK_END in old:
            new = old[:old.index(MARK_START)] + block + old[old.index(MARK_END) + len(MARK_END):].lstrip("\n")
            if new != old:
                open(path, "w", encoding="utf-8").write(new)
                return "updated"
            return "kept"
        open(path, "a", encoding="utf-8").write("\n\n" + block)
        return "appended"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w", encoding="utf-8").write(header + block)
    return "wrote"


def install_playbook(root, agents):
    """Render the playbook for each agent. Returns {relpath: wrote|updated|kept|appended}."""
    front, body = playbook()
    out = {}
    if "claude-code" in agents:
        p = os.path.join(root, ".claude", "skills", "ao", "SKILL.md")
        out[".claude/skills/ao/SKILL.md"] = _write_marked(p, body, header=front)
        cm = os.path.join(root, "CLAUDE.md")
        if os.path.exists(cm):
            pointer = ("## ao\n\nThis repository runs under agent-orchestrator. Load the `ao` skill "
                       "(`.claude/skills/ao/SKILL.md`) before coordinating, implementing or reviewing "
                       "here, and start every turn with `ao status` and the mailbox.")
            out["CLAUDE.md"] = _write_marked(cm, pointer)
    if "kiro" in agents:
        p = os.path.join(root, ".kiro", "steering", "ao-playbook.md")
        out[".kiro/steering/ao-playbook.md"] = _write_marked(p, body, header="---\ninclusion: always\n---\n\n")
    if "codex" in agents or "generic" in agents or not ({"claude-code", "kiro"} & set(agents)):
        p = os.path.join(root, "AGENTS.md")
        out["AGENTS.md"] = _write_marked(p, body, header="# Agents\n\n")
    return out


def register_mcp(root, agents, exe=None):
    """Register the ao MCP server for each agent, in the file that agent reads.

    Merged, never clobbered: other servers in the same file survive. Returns
    {agent: what happened}. Codex keeps its servers in a user-level file, so it
    gets the snippet and the human decides.
    """
    exe = exe or shutil.which("ao") or os.path.join(A.REPO, "bin", "ao")
    server = {"command": exe, "args": ["-C", root, "mcp", "serve"]}
    out = {}

    def merge(path, key="mcpServers", extra=None):
        data = {}
        if os.path.exists(path):
            try:
                data = json.load(open(path))
            except ValueError:
                data = {}
        servers = data.setdefault(key, {})
        entry = dict(server, **(extra or {}))
        if servers.get("ao") == entry:
            return "kept"
        servers["ao"] = entry
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        json.dump(data, open(path, "w"), indent=2)
        return "registered"

    if "claude-code" in agents:
        out["claude-code"] = merge(os.path.join(root, ".mcp.json"))
    if "kiro" in agents:
        p = os.path.join(root, ".kiro", "settings", "mcp.json")
        if shutil.which("kiro-cli") and not os.path.exists(p):
            r = subprocess.run(["kiro-cli", "mcp", "add", "--name", "ao", "--scope", "workspace",
                                "--command", exe, "--args", json.dumps(["-C", root, "mcp", "serve"])],
                               capture_output=True, text=True, cwd=root)
            out["kiro"] = "registered" if r.returncode == 0 else merge(p, extra={"env": {}})
        else:
            out["kiro"] = merge(p, extra={"env": {}})
    if "codex" in agents:
        out["codex"] = ("manual: add to ~/.codex/config.toml\n"
                        f'[mcp_servers.ao]\ncommand = "{exe}"\nargs = ["-C", "{root}", "mcp", "serve"]')
    return out


def next_steps(agents, registered):
    """What the human has to do now — the one thing the tool cannot do for them."""
    apps = {"claude-code": "Claude Code", "kiro": "Kiro", "codex": "Codex"}
    names = [apps[a] for a in ("claude-code", "kiro", "codex") if a in agents]
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
