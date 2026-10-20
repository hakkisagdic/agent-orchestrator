#!/usr/bin/env python3
"""ao-mcp — expose this project's orchestration state over MCP, read-mostly.

Any MCP-capable client (Claude Code, Cursor, Zed, an IDE extension) can then ask
what the implementer is doing and what has been measured, without shelling out
and without learning this tool's output format.

One rule shapes the surface: **authority never goes on it.** `commit-ok` decides
whether work may land, and exposing it here would let the implementer grant
itself the authority the separation exists to withhold. Reading is free; the one
expensive action, `verify`, is opt-in because it runs the project's real gates.
No resource, prompt or tool records an approval, a `commit-ok`, a waiver, an
answer or a `harness-seen`; a step that is a person's comes back as the CLI
command text only (MCP-RES).

A second rule narrows it per session (MCP-ROLES): a server started for a role,
`--role architect|implementer|reviewer`, lists and runs that role's tools alone. A
reviewer reads and sends nothing; an implementer works its mailbox; the architect
keeps every tool. `ao init` writes the role into a registration only one role reads. A
role it does not know stops the server, naming the roles: served every tool, a mistyped
reviewer could act as the architect (HUNT-LEADS-SEC).

A third rule scopes which projects the server speaks for (MCP-RES): started for one
project, it serves that project's tools, read-only resources (`ao://<project>/board`,
`roles`, `ledger`, `decisions`) and prompts (`status`, `land-slice`, `stuck`); started
with `--project all`, one server serves every project in ao's project registry
(`project_registry_path`), each tool, resource and prompt naming its project, and
refuses any project outside the registry.

JSON-RPC 2.0 over stdio, standard library only.
"""
import json
import math
import os
import re
import shlex
import sys
import time
import urllib.parse

from . import __version__, language, lib as A, settings as S
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

PROTOCOL = "2025-06-18"
PROTOCOL_2026 = "2026-07-28"
PROTOCOLS = (PROTOCOL_2026, PROTOCOL)
LEGACY_PROTOCOLS = (PROTOCOL,)
INVALID_PARAMS = -32602
RESOURCE_NOT_FOUND = -32002
UNSUPPORTED_PROTOCOL_VERSION = -32022
RESOURCE_SCHEME = "ao"
RESOURCE_MIME_TYPE = "application/json"
PROJECT_SCOPES = ("all",)
CAPABILITIES = {"tools": {}, "resources": {}, "prompts": {}}
SLICE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?\Z")
QUESTION_ID_RE = re.compile(r"D-\d+\Z")
ID_PATTERNS = {
    "slice": SLICE_ID_RE,
    "question": QUESTION_ID_RE,
}
SUGGESTED_COMMAND_TEMPLATES = (
    "answer",
    "harness-seen",
    "person-review",
    "commit-ok",
    "review",
    "collect-review",
    "commit",
    "waive-review",
    "waive-gate",
    "mail-ack",
    "init",
)


def valid_caller_id(kind, value):
    """True when caller-supplied `value` matches the pattern ao's own `kind` ids follow (MCP-2026-2)."""
    pattern = ID_PATTERNS.get(kind)
    return pattern is not None and isinstance(value, str) and 0 < len(value) <= 200 and bool(pattern.match(value))


def _require_caller_id(kind, value):
    if not valid_caller_id(kind, value):
        raise ValueError(f"invalid {kind} id {value!r}")
    return value


def _require_project_key(value):
    if not A._usable_key(value):
        raise ValueError(f"invalid project key {value!r}")
    return value


def suggested_command(*words):
    """Build a suggested CLI command from `words`, passing every word through `shlex.quote` (MCP-2026-2)."""
    if not words:
        raise ValueError("suggested_command requires at least one word")
    quoted = []
    for word in words:
        text = str(word)
        if "\0" in text:
            raise ValueError("command word cannot contain NUL")
        quoted.append(shlex.quote(text))
    return " ".join(quoted)


def suggested_command_words(template, root=None, *, slice_id=None, question_id=None, project=None):
    """The unquoted word tuple for one suggested CLI command template, validating caller ids first (MCP-2026-2)."""
    if project is not None:
        _require_project_key(project)
    if slice_id is not None:
        _require_caller_id("slice", slice_id)
    if question_id is not None:
        _require_caller_id("question", question_id)
    if template == "mail-ack":
        return ("ao", "mail", "ack")
    if template == "init":
        return ("ao", "init")
    if not isinstance(root, str) or not root or "\0" in root:
        raise ValueError(f"invalid project root {root!r}")
    prefix = ("ao", "-C", root)
    if template == "answer":
        return prefix + ("answer", question_id or "ID", "KEY")
    if template == "harness-seen":
        return prefix + ("harness", "seen", "--by", "NAME")
    if template == "person-review":
        return prefix + ("person-review", "--by", "NAME", "--verdict", "APPROVED", "--digest", "DIGEST")
    if template == "commit-ok":
        return prefix + ("commit-ok", "--verify")
    if template == "review":
        return prefix + ("review",)
    if template == "collect-review":
        return prefix + ("collect-review", "NONCE", "--response", "FILE", "--model", "MODEL", "--by", "NAME")
    if template == "commit":
        target = _require_caller_id("slice", slice_id)
        return prefix + ("commit", "-m", f"{target}: SUMMARY")
    if template == "waive-review":
        target = _require_caller_id("slice", slice_id)
        return prefix + ("waive", "review", "--slice", target, "--by", "NAME", "--why", "REASON")
    if template == "waive-gate":
        return prefix + ("waive", "GATE", "--slice", slice_id or "SLICE", "--by", "NAME", "--why", "REASON")
    raise ValueError(f"unknown suggested command template {template!r}")


def render_suggested_command(template, root=None, *, slice_id=None, question_id=None, project=None):
    """Render one suggested CLI command template with every word passed through `shlex.quote` (MCP-2026-2)."""
    return suggested_command(
        *suggested_command_words(template, root, slice_id=slice_id, question_id=question_id, project=project)
    )


TOOLS = [
    {"name": "ao_status",
     "description": "Implementer state, context/cost telemetry, git state and open reviews.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_board",
     "description": "Where each pre-authorised work item is: running, blocked (with what it "
                    "waits on), queued, inbox, verified, done. Blocked items are the ones a "
                    "human must act on.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_candidate",
     "description": "The candidate staged now and what a review judges it against: the running "
                    "slice and its boundary, the candidate's digest and paths, and the committed "
                    "source a test-only candidate exercises. Not the diff. Read-only.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_notices",
     "description": "Alerts this project raised, including ones the rate limit suppressed.",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer", "default": 10},
                                    "include_suppressed": {"type": "boolean", "default": False}}}},
    {"name": "ao_fleet",
     "description": "One row per project with a local agent session, ordered by what needs a "
                    "human first.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_inbox",
     "description": "Coordination messages addressed to you that you have not yet "
                    "acknowledged. Check this at the start of every turn. Each message "
                    "carries an id; acknowledge it with ao_ack once you have applied or "
                    "explicitly rejected it.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ao_ack",
     "description": "Acknowledge one coordination message: it is removed, which is how "
                    "delivery is confirmed. Acknowledge only after applying or explicitly "
                    "rejecting the message — never on a partial read.",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "string"},
                                    "outcome": {"type": "string",
                                                "description": "applied | rejected: why"}}}},
    {"name": "ao_report",
     "description": "Tell the architect something, at any point in a turn. Use kind "
                    "'blocked' when you cannot proceed without a decision — that escalates "
                    "immediately rather than waiting for a detector to infer it. Use "
                    "'status' or 'done' for everything else.",
     "inputSchema": {"type": "object", "required": ["kind", "summary"],
                     "properties": {
                         "kind": {"type": "string", "enum": ["blocked", "status", "done"]},
                         "summary": {"type": "string"},
                         "detail": {"type": "string"},
                         "needs": {"type": "string",
                                   "description": "for kind=blocked: what input would unblock it"}}}},
    {"name": "ao_ask",
     "description": "Ask the architect or the human a question you cannot answer "
                    "yourself, with options. Prefer this over parking on a prose "
                    "blocker: an options question is answerable from a phone in one "
                    "tap, a paragraph is not. Free text is always available as a last "
                    "option, so list what you think is likely, not everything.",
     "inputSchema": {"type": "object", "required": ["question", "options"],
                     "properties": {
                         "question": {"type": "string"},
                         "options": {"type": "array", "items": {"type": "string"},
                                     "description": "2-8 concrete choices"},
                         "context": {"type": "string",
                                     "description": "what makes this a real question"},
                         "slice": {"type": "string"}}}},
    {"name": "ao_decisions",
     "description": "Questions you asked and whether they have been answered. Check "
                    "this at the start of a turn: an answered question is the thing "
                    "that unparks a slice. A rule proposal's question is 'proposed' "
                    "until a person answers it.",
     "inputSchema": {"type": "object",
                     "properties": {"state": {"type": "string",
                                              "enum": ["open", "proposed", "answered"]}}}},
    {"name": "ao_propose",
     "description": "Propose a change to a rule you work under - a step of the playbook, a "
                    "steering or rule file, .ao/authority.md, a gate - instead of editing it. "
                    "It is recorded in the decision ledger with the outcomes of the project's "
                    "recent slices, and a person accepts or rejects it; until one does, keep "
                    "working under the rule as it stands. ao_decisions shows the answer. The "
                    "same change proposed again is not recorded twice.",
     "inputSchema": {"type": "object", "required": ["text", "why"],
                     "properties": {
                         "text": {"type": "string",
                                  "description": "the change: what the rule would say, or what would change"},
                         "why": {"type": "string",
                                 "description": "what went wrong under the rule as it stands"},
                         "rule_file": {"type": "string",
                                       "description": "the file that holds the rule, as a path in the project"}}}},
    {"name": "ao_fanout",
     "description": "Before fanning out to sub-agents: may a fan-out of this size "
                    "start now (hard cap, recent limit hit, provider window)? "
                    "After: record what it cost so the next estimate is real. "
                    "47 agents once died at 36 with a session limit; this is the "
                    "gate that would have refused it.",
     "inputSchema": {"type": "object", "required": ["agents"],
                     "properties": {
                         "action": {"type": "string", "enum": ["ok", "record"]},
                         "agents": {"type": "integer"},
                         "per_agent_tokens": {"type": "integer"},
                         "done": {"type": "integer"}, "errors": {"type": "integer"},
                         "tokens": {"type": "integer"}, "note": {"type": "string"}}}},
    {"name": "ao_watchdog",
     "description": "Why the watchdog did or did not act. explain runs one dry cycle "
                    "and returns its measurements and verdicts; trace returns the recorded "
                    "cycles. Read-only.",
     "inputSchema": {"type": "object",
                     "properties": {"action": {"type": "string", "enum": ["explain", "trace"]},
                                    "last": {"type": "integer"}}}},
    {"name": "ao_verify",
     "description": "Run the project's declared gates and record the measured result. "
                    "Expensive; disabled unless the server was started with --allow-verify.",
     "inputSchema": {"type": "object",
                     "properties": {"profile": {"type": "string"}}}},
]

EVERY_TOOL = tuple(tool["name"] for tool in TOOLS)

# The tools each role's playbook uses, and the only ones a server started for that role lists or
# runs (MCP-ROLES). The implementer's are the tools the playbook's "Talking" section names, and
# ao_verify, the gate step of its loop, still behind --allow-verify. A reviewer judges a candidate:
# it reads the project's state, the board and the candidate, and sends no mail and no report. The
# architect keeps every tool it had before roles existed.
ROLE_TOOLS = {
    "architect": EVERY_TOOL,
    "implementer": ("ao_status", "ao_board", "ao_notices", "ao_inbox", "ao_ack", "ao_report", "ao_ask",
                    "ao_decisions", "ao_propose", "ao_fanout", "ao_watchdog", "ao_verify"),
    "reviewer": ("ao_status", "ao_board", "ao_candidate"),
}


def role_tools(role):
    """The names of the tools a server started for `role` lists and runs.

    Every tool when `role` is None: a registration written before roles existed, or one several
    roles read, keeps what it served then, and `role_notice` says so. No tool for a role this
    server does not know. It was served every tool, so a mistyped `--role reviwer` was a reviewer
    that could report and acknowledge mail as the architect does (HUNT-LEADS-SEC); `served_role`
    refuses such a role before the server starts, and this holds to it should one reach a call.
    """
    if role is None:
        return EVERY_TOOL
    return ROLE_TOOLS.get(role, ())


def served_role(argv):
    """The role the server's arguments name, read as AO_ROLE is - without case or surrounding space - or None.

    ValueError, naming the roles, for a `--role` that names none of them, or nothing at all: both were
    served every tool (HUNT-LEADS-SEC). `--role=<role>` is read too, which was taken for no role.
    """
    named = [index for index, word in enumerate(argv) if word == "--role" or word.startswith("--role=")]
    if not named:
        return None
    index = named[-1]
    given = argv[index].partition("=")[2] if argv[index] != "--role" else \
        (argv[index + 1] if index + 1 < len(argv) else "")
    role = given.strip().lower()
    if role not in ROLE_TOOLS:
        known = ", ".join(sorted(ROLE_TOOLS))
        init_cmd = render_suggested_command("init")
        raise ValueError(f"ao mcp: --role {given!r} is none of {known}, so the server does not start; name one of "
                         f"them in the registration - `{init_cmd}` writes it - or leave --role out for every tool")
    return role


def served_project(argv):
    """The project scope the server's arguments name (`all`), or None for one project (MCP-RES).

    ValueError when `--project` is given anything other than `all`: one project is chosen with `-C <dir>`,
    and `--project all` serves every project in ao's project registry (`project_registry_path`).
    """
    named = [index for index, word in enumerate(argv) if word == "--project" or word.startswith("--project=")]
    if not named:
        return None
    index = named[-1]
    given = argv[index].partition("=")[2] if argv[index] != "--project" else \
        (argv[index + 1] if index + 1 < len(argv) else "")
    scope = given.strip().lower()
    if scope not in PROJECT_SCOPES:
        raise ValueError(f"ao mcp: --project {given!r} is not 'all', so the server does not start; use --project all "
                         "for every registered project, or -C <dir> without --project for one project")
    return scope


def role_notice(role):
    """The line a server started with no role prints on stderr, or None.

    stderr, because stdout is the protocol: an MCP client keeps what its server writes there in
    its own log, where the person who wrote the registration finds it.
    """
    if role is not None:
        return None
    known = ", ".join(sorted(ROLE_TOOLS))
    init_cmd = render_suggested_command("init")
    return (f"ao mcp: started with no --role, so every tool is served; the roles are {known}, "
            f"and `{init_cmd}` writes one into each registration a single role reads")


def _project_name(cfg):
    """The served project's stable name, read without writing `.ao/project-key` or the registry."""
    root = (cfg or {}).get("root") or ""
    abs_root = os.path.abspath(root) if root else ""
    reg_key = (A.registered_key(root)
               or next((name for name, path in registered_projects().items() if path == abs_root), None)) if root else None
    for candidate in (
        reg_key,
        str((cfg or {}).get("project") or "").strip(),
        os.path.basename(abs_root.rstrip("/\\")) if abs_root else "",
    ):
        if A._usable_key(candidate):
            return candidate
    return "project"


def registered_projects():
    """{project: root} for every existing project in ao's project registry (`A.project_registry_path()`)."""
    found = {}
    for name, row in sorted(A.project_registry().items()):
        path = row.get("root")
        if A._usable_key(name) and isinstance(path, str) and os.path.isdir(path):
            found[name] = os.path.abspath(path)
    return found


def _served_projects(cfg, project_scope=None):
    """[(project, root, project_cfg)] for the projects this server serves, in stable order."""
    if project_scope == "all":
        return [(name, root, dict(A.load_config(root), root=root))
                for name, root in sorted(registered_projects().items())]
    root = os.path.abspath(cfg["root"])
    return [(_project_name(cfg), root, dict(cfg, root=root))]


def _resolve_project(cfg, project_scope, requested):
    """((project, root, project_cfg), None) for `requested`, or (None, error_message)."""
    if project_scope == "all":
        projects = registered_projects()
        if not isinstance(requested, str) or not requested.strip():
            known = ", ".join(sorted(projects)) or "none registered"
            return None, f"project is required with --project all (registered: {known})"
        if requested not in projects:
            known = ", ".join(sorted(projects)) or "none registered"
            return None, (f"project {requested!r} is not in ao's project registry "
                          f"({A.project_registry_path()}; registered: {known})")
        root = projects[requested]
        return (requested, root, dict(A.load_config(root), root=root)), None
    current = _project_name(cfg)
    root = os.path.abspath(cfg["root"])
    if requested is not None:
        if not isinstance(requested, str) or requested != current:
            return None, (f"project {requested!r} is not served by this server "
                          f"(serving {current!r}); start with --project all for every registered project")
    return (current, root, dict(cfg, root=root)), None


def tools_for_role(role, project_scope=None):
    """The tool definitions a server started for `role` and `project_scope` lists (MCP-ROLES, MCP-RES).

    With `--project all`, every tool's inputSchema names `project` as a required string so each call
    identifies the registered project it acts on.
    """
    served = role_tools(role)
    listed = []
    for tool in TOOLS:
        if tool["name"] not in served:
            continue
        if project_scope != "all":
            listed.append(tool)
            continue
        schema = dict(tool["inputSchema"])
        props = dict({"project": {"type": "string",
                                  "description": "project name in ao's project registry"}},
                     **dict(schema.get("properties") or {}))
        required = list(schema.get("required") or [])
        if "project" not in required:
            required = ["project"] + required
        schema["properties"] = props
        schema["required"] = required
        listed.append(dict(tool, inputSchema=schema))
    return listed


def status_payload(cfg):
    from . import cli, watchdog as W
    root = cfg["root"]
    # The server loads the config once, and an agent starts it before its own session has written a
    # transcript: a session `auto` found no session for is looked for again (SESSION-IDENTITY).
    if not (A.session_state(cfg, "implementer") or {}).get("session"):
        cfg = dict(A.load_config(root), root=root)
    mode = cli.project_mode(cfg)
    enforcement = cli.ENFORCEMENT_OFF_LITE_LINE if mode == "lite" else "on"
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    state, age, desc = A.busy(cfg, adapter) if impl else ("unknown", None, "")
    msgs, _ = A.session_paths(cfg)
    recs = A.read_tail(msgs, 4_000_000) if msgs else []
    tel = A.telemetry(recs, adapter, msgs) if recs else {}
    g = A.git_state(root)
    revs = A.reviews(root, cfg["reviews"], limit=3)
    # Processes that could not be counted are null, and the reason says why: none were seen is not none run
    # (WINDOWS-TABLE-CLOSED).
    from .procs import TableUnreadable
    unread = None
    try:
        processes = len(A.agent_pids(root, adapter)) if impl else 0
    except TableUnreadable as exc:
        processes, unread = None, str(exc)
    return {"project": cfg.get("project") or os.path.basename(root), "root": root,
            "mode": mode, "enforcement": enforcement,
            "adapter": impl.get("adapter"), "state": state, "seconds_since_write": age,
            "doing": desc, "spinning_minutes": A.spinning(root),
            "nothing_to_do_since": (W.load_state(root).get("idle_answer") or {}).get("since"),
            "context_percent": tel.get("ctx"), "turns": tel.get("turns"),
            "cost_total": tel.get("total"), "cost_delegated": tel.get("delegated"), "cost_unit": tel.get("unit"),
            "head": g["log"][0] if g["log"] else None,
            "dirty_files": len(g["dirty"]), "unpushed": g["ahead"],
            "behind": g.get("behind"), "base": g.get("base"), "merged": g.get("merged"),
            "mailbox": A.mailbox(root, cfg["mailbox"]),
            "reviews": [{"file": f, "verdict": v} for f, v in revs],
            "throughput": A.throughput(root, cfg),
            "held": A.hold_state(root),
            "agent_processes": processes, "agent_processes_unread": unread}


def board_payload(root):
    b = A.board(root)
    graph = A.board_graph(root)
    return {"states": {k: v for k, v in b.items() if v},
            "ready": [item["id"] for item in graph["ready"]], "problems": graph["problems"],
            "a2a": {k: A.A2A_STATE.get(k) for k in b if b[k]},
            "counts": {k: len(v) for k, v in b.items()}}


def candidate_payload(cfg):
    """The candidate staged now, and what a review judges it against (MCP-ROLES).

    What `ao review` builds its request from, read the same way: the running slice, its boundary
    (the file its row points at, or its sentence), the candidate's identity and paths, and the
    committed source a test-only candidate exercises (#97), within review.context_bytes. The diff
    is left out: a reviewer is handed the one it judges, and a submitted review judges the tree it
    pinned, which the index may have moved on from - the digest tells the two apart.
    """
    root = cfg["root"]
    running = A.running_slice(root)
    source = A.read_boundary(root, running)
    out = {"slice": (running or {}).get("id"), "title": (running or {}).get("title"),
           "boundary": (source or {}).get("text") or A.slice_boundary(running) or None}
    try:
        candidate = A.index_candidate(root)
    except (RuntimeError, ValueError) as exc:
        return dict(out, candidate=None, context=None, problem=f"the staged candidate cannot be measured: {exc}")
    out["candidate"] = {key: candidate[key] for key in ("digest", "head", "index_tree", "changed_paths")}
    context = A.review_context(root, candidate["changed_paths"], candidate["index_tree"], candidate["head"],
                               S.get(cfg, "review.context_bytes"))
    out["context"] = None if context is None else \
        {key: context[key] for key in ("rev", "paths", "names", "omitted", "text")}
    return out


def roles_payload(cfg):
    """The project's actors, held roles and any pending role assignment, read-only (MCP-RES)."""
    root = cfg["root"]
    try:
        with open(os.path.join(root, ".ao", "config.json"), encoding=UTF8) as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        stored = {}
    source = stored if isinstance(stored, dict) and stored else cfg
    actors, roles, _ = A.role_table(source)
    held = A.effective_roles(root, dict(source, roles=roles))
    waiting, after = A.pending_roles(root, source)
    return {
        "roles": {role: held.get(role) for role in A.ROLE_BLOCKS},
        "actors": {
            name: {key: block[key] for key in ("adapter", "model", "effort", "family") if block.get(key)}
            for name, block in sorted(actors.items())
        },
        "pending": {"roles": waiting, "after": after} if waiting else None,
    }


def ledger_payload(root):
    """Read-only summary of the project's ledgers, carrying no authority token (MCP-RES)."""
    from .storage import read_chained_history
    slice_rows = [row for row in A.slice_moves(root) if isinstance(row, dict)]
    verifications = [row for row in A.verification_history(root) if isinstance(row, dict)]
    reviews = [row for row in read_chained_history(A.review_ledger_path(root), A.REVIEW_CHAIN) if isinstance(row, dict)]
    decisions = [row for row in A.decision_rows(root) if isinstance(row, dict)]
    waivers = [row for row in A.waiver_rows(root) if isinstance(row, dict)]
    latest_v = A.latest_verification(root)
    return {
        "counts": {
            "slices": len(slice_rows),
            "verifications": len(verifications),
            "reviews": len(reviews),
            "decisions": len(decisions),
            "waivers": len(waivers),
        },
        "latest_slice": (
            {key: slice_rows[-1].get(key) for key in ("slice", "from", "to", "at", "by", "why")
             if key in slice_rows[-1]}
            if slice_rows else None
        ),
        "latest_verification": (
            {key: latest_v.get(key) for key in ("id", "passed", "ok", "profile", "at")
             if key in latest_v}
            if isinstance(latest_v, dict) else None
        ),
        "latest_review": (
            {"id": reviews[-1].get("artefact") or reviews[-1].get("id"),
             "slice": reviews[-1].get("slice"),
             "verdict": reviews[-1].get("verdict"),
             "at": reviews[-1].get("at")}
            if reviews else None
        ),
    }


def decisions_payload(root, limit=10):
    """The project's latest architect decisions and asked questions, read-only (MCP-RES)."""
    recorded = [row for row in A.decision_rows(root)
                if isinstance(row, dict) and row.get("kind") != "proposal"][-limit:]
    asked = A.decisions(root)
    return {
        "decisions": recorded,
        "questions": asked[:limit],
        "open_count": sum(1 for item in asked if item.get("state") in ("open", "proposed")),
    }


RESOURCE_SPECS = (
    {"kind": "board",
     "title": "Board",
     "description": "Where each pre-authorised work item stands on the project's board, read-only and carrying no authority."},
    {"kind": "roles",
     "title": "Role table",
     "description": "The project's actors, role assignments and any pending reassignment, read-only and carrying no authority."},
    {"kind": "ledger",
     "title": "Ledger summary",
     "description": "Summary of the project's slice, verification, review, decision and waiver ledgers, read-only and carrying no authority."},
    {"kind": "decisions",
     "title": "Latest decisions",
     "description": "The project's latest architect decisions and asked questions, read-only and carrying no authority."},
)
RESOURCE_SPEC_BY_KIND = {spec["kind"]: spec for spec in RESOURCE_SPECS}


def resource_uri(project, kind):
    """The stable `ao://<project>/<kind>` URI of one read-only resource, with `<project>` percent-encoded (MCP-RES, MCP-2026-2)."""
    return f"{RESOURCE_SCHEME}://{urllib.parse.quote(str(project), safe='')}/{kind}"


def _parse_resource_uri(uri):
    """(project, kind) for a valid `ao://<project>/<kind>` URI, or (None, None)."""
    prefix = f"{RESOURCE_SCHEME}://"
    if not isinstance(uri, str) or not uri.startswith(prefix) or any(ch.isspace() for ch in uri):
        return None, None
    rest = uri[len(prefix):]
    raw_project, sep, kind = rest.partition("/")
    if not sep or not raw_project or not kind or "/" in kind or any(ch in raw_project for ch in "#?\\"):
        return None, None
    project = urllib.parse.unquote(raw_project)
    if not A._usable_key(project):
        return None, None
    if kind not in RESOURCE_SPEC_BY_KIND:
        return None, None
    return project, kind


def _cursor_error(params):
    if params is None:
        return None
    if not isinstance(params, dict):
        return {"code": INVALID_PARAMS, "message": "Invalid params"}
    if params.get("cursor") is not None:
        return {"code": INVALID_PARAMS, "message": "Invalid params: unknown cursor"}
    return None


def resources_list(cfg, project_scope=None, params=None):
    """Answer `resources/list`: (result, error) for the served project or every registered project (MCP-RES)."""
    err = _cursor_error(params)
    if err is not None:
        return None, err
    resources = []
    for project, _, _ in _served_projects(cfg, project_scope):
        for spec in RESOURCE_SPECS:
            kind = spec["kind"]
            resources.append({
                "uri": resource_uri(project, kind),
                "name": f"{project}/{kind}",
                "title": f"{project} {spec['title'].lower()}",
                "description": spec["description"],
                "mimeType": RESOURCE_MIME_TYPE,
            })
    return {"resources": resources}, None


def resource_templates_list(params=None):
    """Answer `resources/templates/list`: (result, error) for per-project `ao://{project}/<kind>` URIs (MCP-RES)."""
    err = _cursor_error(params)
    if err is not None:
        return None, err
    templates = [{
        "uriTemplate": f"{RESOURCE_SCHEME}://{{project}}/{spec['kind']}",
        "name": spec["kind"],
        "title": spec["title"],
        "description": spec["description"],
        "mimeType": RESOURCE_MIME_TYPE,
    } for spec in RESOURCE_SPECS]
    return {"resourceTemplates": templates}, None


def resource_read(cfg, project_scope=None, params=None, negotiated=None):
    """Answer `resources/read`: (result, error), read-only and refusing any project outside the registry (MCP-RES).

    In MCP `2025-06-18` an unknown or unserved resource URI answers with `-32002` (`Resource not found`);
    in `2026-07-28` (SEP-2164) it answers with `-32602` (`Invalid params`).
    """
    if not isinstance(params, dict) or not isinstance(params.get("uri"), str) or not params.get("uri"):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params: uri is required"}
    uri = params["uri"]
    not_found_code = INVALID_PARAMS if negotiated == PROTOCOL_2026 else RESOURCE_NOT_FOUND
    project, kind = _parse_resource_uri(uri)
    if project is None:
        return None, {"code": not_found_code, "message": f"Resource not found: {uri}", "data": {"uri": uri}}
    resolved, problem = _resolve_project(cfg, project_scope, project)
    if problem is not None:
        return None, {"code": not_found_code, "message": problem, "data": {"uri": uri}}
    project_name, root, project_cfg = resolved
    canonical_uri = resource_uri(project_name, kind)
    if kind == "board":
        body = board_payload(root)
    elif kind == "roles":
        body = roles_payload(project_cfg)
    elif kind == "ledger":
        body = ledger_payload(root)
    else:
        body = decisions_payload(root)
    payload = dict({"project": project_name, "uri": canonical_uri, "authority": "none"}, **body)
    return {
        "contents": [{
            "uri": canonical_uri,
            "mimeType": RESOURCE_MIME_TYPE,
            "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        }]
    }, None


PROMPT_SPECS = (
    {"name": "status",
     "title": "Day's status",
     "description": "Summarise the day's status for the project by calling its read-only ao tools in order.",
     "tools": ("ao_status", "ao_board", "ao_decisions", "ao_notices"),
     "arguments": ()},
    {"name": "land-slice",
     "title": "Land a named slice",
     "description": "Prepare a named slice to land by calling ao's inspection and gate tools in order; "
                    "every authority step stays a CLI command for a person.",
     "tools": ("ao_board", "ao_status", "ao_inbox", "ao_decisions", "ao_verify", "ao_candidate"),
     "arguments": ({"name": "slice",
                    "description": "Slice id on the project's board to land",
                    "required": True},)},
    {"name": "stuck",
     "title": "Explain what is stuck",
     "description": "Explain what is stuck in the project by calling ao's inspection tools in order; "
                    "any authority step comes back as a CLI command for a person.",
     "tools": ("ao_status", "ao_board", "ao_decisions", "ao_notices", "ao_watchdog", "ao_inbox"),
     "arguments": ()},
)
PROMPT_SPEC_BY_NAME = {spec["name"]: spec for spec in PROMPT_SPECS}


def prompts_for_role(role):
    """The prompt specs whose ordered tools are all served for `role` (MCP-2026-2)."""
    served = set(role_tools(role))
    return tuple(spec for spec in PROMPT_SPECS if set(spec["tools"]).issubset(served))


def _prompt_arguments(spec, project_scope=None):
    project_arg = {
        "name": "project",
        "description": "Project name in ao's project registry",
        "required": project_scope == "all",
    }
    return [project_arg] + [dict(arg) for arg in spec["arguments"]]


def prompts_list(project_scope=None, params=None, role=None):
    """Answer `prompts/list`: (result, error) for the menu prompts a person picks from (MCP-RES, MCP-2026-2)."""
    err = _cursor_error(params)
    if err is not None:
        return None, err
    prompts = [{
        "name": spec["name"],
        "title": spec["title"],
        "description": spec["description"],
        "arguments": _prompt_arguments(spec, project_scope),
    } for spec in prompts_for_role(role)]
    return {"prompts": prompts}, None


def _prompt_text(name, project_name, root, slice_id=None, question_id=None):
    _require_project_key(project_name)
    if slice_id is not None:
        _require_caller_id("slice", slice_id)
    if question_id is not None:
        _require_caller_id("question", question_id)
    call_arg = json.dumps({"project": project_name}, ensure_ascii=False)
    answer_cmd = render_suggested_command("answer", root, question_id=question_id, project=project_name)
    seen_cmd = render_suggested_command("harness-seen", root, project=project_name)
    person_review_cmd = render_suggested_command("person-review", root, project=project_name)
    commit_ok_cmd = render_suggested_command("commit-ok", root, project=project_name)
    if name == "status":
        return (
            f"Project: {project_name}\n"
            f"Inspect the day's status for project `{project_name}` by calling these ao MCP tools in order:\n"
            f"1. `ao_status` with arguments `{call_arg}` — read implementer state, telemetry, git state and open reviews.\n"
            f"2. `ao_board` with arguments `{call_arg}` — read running, blocked, ready, verified and done slices.\n"
            f"3. `ao_decisions` with arguments `{call_arg}` — read open, proposed and answered questions.\n"
            f"4. `ao_notices` with arguments `{call_arg}` — read recent alerts and notices.\n\n"
            "Authority stays in the CLI — no MCP resource, prompt or tool records an approval, a commit-ok, "
            "a waiver, an answer or a harness-seen. If a person must act, return the exact CLI command text "
            "for them to run in their terminal:\n"
            f"- Answer an open question: `{answer_cmd}`\n"
            f"- Record harness configuration changes as seen: `{seen_cmd}`\n"
            f"- Record a person review verdict: `{person_review_cmd}`\n"
            f"- Grant commit authority: `{commit_ok_cmd}`"
        )
    if name == "land-slice":
        _require_caller_id("slice", slice_id)
        verify_arg = json.dumps({"project": project_name, "profile": "quick"}, ensure_ascii=False)
        review_cmd = render_suggested_command("review", root, project=project_name)
        collect_cmd = render_suggested_command("collect-review", root, project=project_name)
        commit_cmd = render_suggested_command("commit", root, slice_id=slice_id, project=project_name)
        waive_review_cmd = render_suggested_command("waive-review", root, slice_id=slice_id, project=project_name)
        return (
            f"Project: {project_name}\n"
            f"Slice: {slice_id}\n"
            f"Prepare slice `{slice_id}` in project `{project_name}` to land by calling these ao MCP tools in order:\n"
            f"1. `ao_board` with arguments `{call_arg}` — confirm `{slice_id}` is on the board and check its state.\n"
            f"2. `ao_status` with arguments `{call_arg}` — check dirty files, head, open reviews and unacknowledged mail.\n"
            f"3. `ao_inbox` with arguments `{call_arg}` — read any unacknowledged coordination messages (and acknowledge handled ones with `ao_ack`).\n"
            f"4. `ao_decisions` with arguments `{call_arg}` — confirm no open question blocks `{slice_id}`.\n"
            f"5. `ao_verify` with arguments `{verify_arg}` — run the declared gates when the server was started with `--allow-verify`.\n"
            f"6. `ao_candidate` with arguments `{call_arg}` — read the staged candidate's digest, paths and boundary for `{slice_id}`.\n\n"
            "Authority stays in the CLI — no MCP resource, prompt or tool records an approval, a commit-ok, "
            "a waiver, an answer or a harness-seen. Return any person step as CLI command text only:\n"
            f"- Independent or person review: `{review_cmd}` or `{person_review_cmd}` "
            f"(or `{collect_cmd}`)\n"
            f"- Grant commit authority for the staged candidate: `{commit_ok_cmd}`\n"
            f"- Commit the granted candidate: `{commit_cmd}`\n"
            f"- Waive a gate on the record (person only): `{waive_review_cmd}`\n"
            f"- Answer an open question (person only): `{answer_cmd}`\n"
            f"- Record harness configuration changes as seen (person only): `{seen_cmd}`"
        )
    waive_gate_cmd = render_suggested_command("waive-gate", root, slice_id=slice_id, project=project_name)
    return (
        f"Project: {project_name}\n"
        f"Explain what is stuck in project `{project_name}` by calling these ao MCP tools in order:\n"
        f"1. `ao_status` with arguments `{call_arg}` — check implementer state, spinning minutes, held state and unread processes.\n"
        f"2. `ao_board` with arguments `{call_arg}` — find blocked slices, what each waits on, and any board graph problems.\n"
        f"3. `ao_decisions` with arguments `{call_arg}` — list open questions and rule proposals waiting on a person.\n"
        f"4. `ao_notices` with arguments `{call_arg}` — inspect recent anomaly notices.\n"
        f"5. `ao_watchdog` with arguments `{call_arg}` — run one dry explain cycle to see why the watchdog did or did not act.\n"
        f"6. `ao_inbox` with arguments `{call_arg}` — check unacknowledged coordination messages.\n\n"
        "Authority stays in the CLI — no MCP resource, prompt or tool records an approval, a commit-ok, "
        "a waiver, an answer or a harness-seen. When a person's action is needed to unblock the project, "
        "return the command text only:\n"
        f"- Answer an open decision or proposal: `{answer_cmd}`\n"
        f"- Mark harness changes seen: `{seen_cmd}`\n"
        f"- Record a person review: `{person_review_cmd}`\n"
        f"- Waive a gate on the record: `{waive_gate_cmd}`\n"
        f"- Grant commit authority: `{commit_ok_cmd}`"
    )


def prompt_get(cfg, project_scope=None, params=None, role=None):
    """Answer `prompts/get`: (result, error) naming the project and ordered ao tools to call (MCP-RES, MCP-2026-2)."""
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params: prompt name is required"}
    name = params["name"]
    spec = PROMPT_SPEC_BY_NAME.get(name)
    if spec is None:
        return None, {"code": INVALID_PARAMS, "message": f"Invalid params: unknown prompt {name!r}"}
    served = set(role_tools(role))
    unserved = [tool for tool in spec["tools"] if tool not in served]
    if unserved:
        return None, {
            "code": INVALID_PARAMS,
            "message": (f"Invalid params: prompt {name!r} calls {', '.join(unserved)}, which is not served "
                        f"with --role {role}"),
        }
    raw_args = params.get("arguments")
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                             for k, v in raw_args.items()):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params: arguments must be a map of strings"}
    resolved, problem = _resolve_project(cfg, project_scope, raw_args.get("project"))
    if problem is not None:
        return None, {"code": INVALID_PARAMS, "message": f"Invalid params: {problem}"}
    project_name, root, _ = resolved
    slice_id = None
    if name == "land-slice" or "slice" in raw_args:
        raw_slice = raw_args.get("slice", "")
        slice_id = raw_slice.strip() if isinstance(raw_slice, str) else ""
        if not slice_id:
            return None, {"code": INVALID_PARAMS, "message": "Invalid params: argument 'slice' is required"}
        if not valid_caller_id("slice", slice_id):
            return None, {"code": INVALID_PARAMS, "message": f"Invalid params: invalid slice id {raw_slice!r}"}
    question_id = None
    if "question" in raw_args:
        raw_question = raw_args.get("question", "")
        question_id = raw_question.strip() if isinstance(raw_question, str) else ""
        if not valid_caller_id("question", question_id):
            return None, {"code": INVALID_PARAMS, "message": f"Invalid params: invalid question id {raw_question!r}"}
    text = _prompt_text(name, project_name, root, slice_id=slice_id, question_id=question_id)
    return {
        "description": f"{spec['description']} (project: {project_name})",
        "messages": [{
            "role": "user",
            "content": {"type": "text", "text": text},
        }],
    }, None


def _attach_project(payload, project_name, project_scope):
    if project_scope == "all" and isinstance(payload, dict) and "error" not in payload:
        return dict({"project": project_name}, **payload)
    return payload


def call(name, args, cfg, allow_verify, role=None, project_scope=None):
    """Run one tool for a server started for `role` and `project_scope`, and return what it answers.

    A tool outside the role's set is refused here, not only left off the listing: a client can
    call a name it was never shown, and the refusal is the same whichever way the call arrives.
    With `--project all`, every call must name a project in ao's project registry (`project_registry_path`),
    and any project outside the registry is refused before the tool runs (MCP-RES).
    """
    args = args if isinstance(args, dict) else {}
    if name not in EVERY_TOOL:
        return {"error": f"unknown tool {name}"}
    if name not in role_tools(role):
        return {"error": f"{name} is not one of the {role}'s tools; this server was started with --role {role}"}
    resolved, problem = _resolve_project(cfg, project_scope, args.get("project"))
    if problem is not None:
        return {"error": problem}
    project_name, root, cfg = resolved
    if name == "ao_status":
        out = status_payload(cfg)
        if project_scope == "all":
            out = dict(out, project=project_name)
        return out
    if name == "ao_board":
        return _attach_project(board_payload(root), project_name, project_scope)
    if name == "ao_candidate":
        return _attach_project(candidate_payload(cfg), project_name, project_scope)
    if name == "ao_notices":
        return _attach_project({"notices": A.notices(root, args.get("limit", 10),
                                                     args.get("include_suppressed", False))},
                               project_name, project_scope)
    if name == "ao_fleet":
        if project_scope == "all":
            projects = [{"name": proj, "root": path} for proj, path in sorted(registered_projects().items())]
            return {"project": project_name, "projects": projects}
        return {"projects": [{"name": os.path.basename(w["path"]), "root": w["path"]}
                             for w in A.all_workspaces()]}
    if name == "ao_inbox":
        # The same files the file-based protocol uses. One source of truth: an MCP
        # store beside the mailbox would be a second place for the same fact, and
        # every failure in this project's history has come from two records of one
        # thing drifting apart.
        box = cfg.get("mailbox", "agent-mail")
        out = []
        for m in A.mailbox(root, box):
            if not A.addressed_to(m, cfg, "implementer"):
                continue                     # our own outbound, or the watchdog's; not addressed to us
            try:
                body = open(os.path.join(root, box, m), errors="replace", encoding=UTF8).read(20000)
            except OSError:
                continue
            out.append({"id": m, "body": body})
        A.mail_seen(root, [message["id"] for message in out], "implementer")   # shown, not handled (#30)
        return _attach_project({"messages": out, "count": len(out),
                                "note": "acknowledge each with ao_ack after applying or rejecting it"},
                               project_name, project_scope)

    if name == "ao_ack":
        box = cfg.get("mailbox", "agent-mail")
        mid = os.path.basename(args.get("id", ""))
        path = os.path.join(root, box, mid)
        if not mid or mid == "README.md" or not os.path.exists(path):
            return {"error": f"no such message: {args.get('id')}"}
        # Only what ao_inbox lists: mail addressed to the implementer. Any name in the mailbox was taken, so an
        # implementer consumed the architect's reports, the hunter's leads and the watchdog's anomalies - deleted,
        # or with mail.store append-only recorded handled, which the store keeps from the actor deciding what was
        # handled precisely so that it cannot erase the question (LEADS-MCP).
        if not A.addressed_to(mid, cfg, "implementer"):
            mail_ack_cmd = render_suggested_command("mail-ack")
            return {"error": f"{mid} is not addressed to the implementer, so ao_ack does not take it: it acknowledges "
                             "only what ao_inbox lists. Mail to the architect, and the watchdog's, is acknowledged by "
                             f"the one it is for, with `{mail_ack_cmd}`; leave it where it is."}
        if A.mail_store_mode(root) == "append-only":
            A.ingest_mail(root, cfg)
            A.handle_message(root, cfg, mid, "implementer", args.get("outcome", "applied"))   # a record (#80)
        else:
            os.remove(path)
        A.record_notice(root, "ack", f"{mid}: {args.get('outcome', 'applied')}",
                        sent=False, key="ack")
        A.mail_ledger_append(root, {"event": "consumed", "id": mid,
                                    "outcome": args.get("outcome", "applied")})
        return _attach_project({"acknowledged": mid, "outcome": args.get("outcome", "applied")},
                               project_name, project_scope)

    if name == "ao_report":
        if args.get("slice") is not None and not valid_caller_id("slice", args.get("slice")):
            return {"error": f"invalid slice id {args.get('slice')!r}"}
        box = cfg.get("mailbox", "agent-mail")
        os.makedirs(os.path.join(root, box), exist_ok=True)
        kind = args.get("kind", "status")
        # `blocked` writes the marker the watchdog escalates on within one cycle.
        # The agent saying so directly beats a detector inferring it twenty
        # minutes later, which is what used to happen. It is the project's
        # language's marker; the watchdog reads every language's (LANGUAGE-FILES).
        header = language.marker(cfg, "decision") if kind == "blocked" else f"## {kind.upper()}"
        slug = A.safe_slug(args["summary"].lower(), "report")
        # The same request twice is one request. An implementer nudged into a
        # turn with nothing to do reports the same blocker again; eighty copies
        # of "queue empty" stood in one mailbox after eleven hours, each a fresh
        # anomaly and a fresh wake. Fold a repeat into the standing report and
        # keep that file's age — the age is the fact the architect needs.
        impl, arch = A.mail_names(cfg)
        # the standing report may be addressed by a name the architect's mail had before (#72)
        markers = [f"-{impl}-to-{name}-{kind.upper()}-" for name in A.architect_mail_names(cfg)]
        dup = None
        for m in A.mailbox(root, box):
            if any(marker in m for marker in markers) and \
                    A._report_summary(os.path.join(root, box, m)) == args["summary"].strip():
                dup = m
        if dup:
            n = A.bump_repeat(os.path.join(root, box, dup), cfg)
            return _attach_project(
                {"written": dup, "repeated": n, "escalates": kind == "blocked",
                 "delivered_to_phone": 0,
                 "note": (f"the same {kind} report is already standing ({n}× now); the architect "
                          f"sees one file with its original time. Do not report it again; end the turn.")},
                project_name, project_scope)
        name_ = f"{time.strftime('%Y%m%d-%H%M')}-{impl}-to-{arch}-{kind.upper()}-{slug}.md"
        text = f"# {args['summary']}\n\n{header}\n\n"
        # Counts are quoted from the ledger, not typed; a green claim over a red
        # verification is said to be what it is (#6).
        inconsistent = A.report_inconsistency(
            root, " ".join(str(args.get(field) or "") for field in ("summary", "detail", "needs")))
        if inconsistent:
            text += f"## INCONSISTENT\n\n{inconsistent}\n\n"
        if args.get("detail"):
            text += args["detail"] + "\n\n"
        if args.get("needs"):
            text += f"**Needs:** {args['needs']}\n"
        evidence = A.verification_evidence(root)
        if evidence:
            text += f"**Verification:** {evidence}\n"
        name_ = A.write_mail(root, cfg, name_, text, {"kind": kind, "from": impl, "to": arch,
                                              "slice": args.get("slice")})
        delivered = 0
        if kind == "blocked":
            try:
                from . import telegram
                delivered = telegram.send(
                    language.text(cfg, "report.blocked-phone", summary=args["summary"],
                                  needs=args.get("needs") or args.get("detail") or ""),
                    root)
            except Exception:
                pass
        return _attach_project(
            {"written": name_, "escalates": kind == "blocked",
             "delivered_to_phone": delivered, "inconsistent": inconsistent,
             "note": ("the architect is woken on the next watchdog cycle"
                      if kind == "blocked" else "queued for the architect")},
            project_name, project_scope)

    if name == "ao_watchdog":
        from types import SimpleNamespace
        from . import watchdog as W
        if args.get("action", "explain") == "trace":
            return _attach_project({"cycles": W.cycles(root, int(args.get("last") or 20))},
                                   project_name, project_scope)
        W.run(SimpleNamespace(root=root, idle_minutes=None, dry_run=True))
        return _attach_project({"facts": dict(W._FACTS), "trace": list(W._TRACE),
                                "verdict": W._TRACE[-1] if W._TRACE else ""},
                               project_name, project_scope)
    if name == "ao_fanout":
        if args.get("action") == "record":
            return _attach_project(
                A.record_fanout(root, args["agents"], args.get("done"), args.get("errors"),
                                args.get("tokens"), args.get("note")),
                project_name, project_scope)
        return _attach_project(
            A.fanout_verdict(root, cfg, int(args["agents"]), args.get("per_agent_tokens")),
            project_name, project_scope)
    if name == "ao_ask":
        if args.get("slice") is not None and not valid_caller_id("slice", args.get("slice")):
            return {"error": f"invalid slice id {args.get('slice')!r}"}
        rec = A.ask(root, args["question"], args.get("options") or [],
                    context=args.get("context"), slice_id=args.get("slice"), cfg=cfg)
        # Delivery is decided here, not by the caller. An implementer with its own
        # channel to a phone is a spam surface; an implementer that reports and
        # lets the centre route is not.
        delivered = 0
        try:
            from .cli import _phone_decision
            delivered = _phone_decision(rec, cfg)
        except Exception:
            pass
        return _attach_project({"id": rec["id"], "options": rec["options"],
                                "delivered_to_phone": delivered,
                                "note": "park the slice and take the next queued item; check "
                                        "ao_decisions next turn"},
                               project_name, project_scope)

    if name == "ao_decisions":
        return _attach_project({"decisions": A.decisions(root, args.get("state"))},
                               project_name, project_scope)

    if name == "ao_propose":
        # A proposal, never an edit: it records the change and asks a person, and writes no rule file. It is
        # the agent's to make, as a question is; the answer is a person's, and no tool here gives one.
        try:
            proposal, question, created = A.propose(root, cfg, args.get("text"), args.get("why"),
                                                     rule_file=args.get("rule_file"), via="mcp")
        except A.ProposalRefused as exc:
            return {"error": str(exc)}
        if not created:
            return _attach_project(
                {"id": proposal["id"], "question": proposal["question"], "standing": True,
                 "note": "this change is proposed already and waits for a person; do not propose it again"},
                project_name, project_scope)
        delivered = 0
        try:
            from .cli import _phone_decision
            delivered = _phone_decision(question, cfg)
        except Exception:
            pass
        return _attach_project(
            {"id": proposal["id"], "question": proposal["question"], "delivered_to_phone": delivered,
             "note": "a person accepts or rejects it; keep working under the rule as it stands, and read "
                     "the answer with ao_decisions"},
            project_name, project_scope)

    if name == "ao_verify":
        if not allow_verify:
            return {"error": "verify is disabled; start the server with --allow-verify"}
        import subprocess
        r = subprocess.run([sys.executable, "-m", "ao", "-C", root, "verify"]
                           + (["-p", args["profile"]] if args.get("profile") else []),
                           capture_output=True, text=True, encoding=UTF8, errors="replace", timeout=3600,
                           cwd=A.self_child_cwd(), env=A.self_child_env())
        return _attach_project({"exit": r.returncode, "output": (r.stdout or r.stderr)[-4000:],
                                "record": A.latest_verification(root)},
                               project_name, project_scope)
    return {"error": f"unknown tool {name}"}


def _server_info():
    return {"name": "agent-orchestrator", "version": __version__}


def _capabilities():
    return {key: dict(value) for key, value in CAPABILITIES.items()}


def _discover_result():
    return {"resultType": "complete",
            "supportedVersions": list(PROTOCOLS),
            "capabilities": _capabilities(),
            "_meta": {"io.modelcontextprotocol/serverInfo": _server_info()}}


def _negotiate_initialize(params):
    """Answer `initialize` (2025-06-18 lifecycle): (result, error).

    MCP `2026-07-28` is stateless and removed the `initialize` handshake in favour of
    `server/discover` and per-request `params._meta` (`basic/versioning`, `basic/index`
    #Statelessness). Naming `2026-07-28` on `initialize` previously answered with a
    `2026-07-28` handshake result while subsequent no-`_meta` calls were served as
    `2025-06-18`; refuse `2026-07-28` on `initialize` and point at `server/discover`,
    while legacy clients keep working on `2025-06-18` (MCP-2026-2).
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
    requested = params.get("protocolVersion")
    if requested is not None and (not isinstance(requested, str) or not requested):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
    meta = params.get("_meta")
    meta_version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
    if requested == PROTOCOL_2026 or meta_version == PROTOCOL_2026:
        return None, {
            "code": INVALID_PARAMS,
            "message": (f"Invalid params: protocolVersion {PROTOCOL_2026!r} is stateless and does not use "
                        "initialize; probe with server/discover and send "
                        "io.modelcontextprotocol/protocolVersion in params._meta on each request, "
                        f"or initialize with {PROTOCOL!r}"),
            "data": {"supported": list(PROTOCOLS), "requested": PROTOCOL_2026},
        }
    version = requested if requested in LEGACY_PROTOCOLS else PROTOCOL
    return {"protocolVersion": version,
            "capabilities": _capabilities(),
            "serverInfo": _server_info()}, None


def _negotiate_meta(params, require_meta=False):
    """Validate 2026-07-28 `params._meta` when present or required (MCP-2026): (version, error)."""
    if params is None:
        if require_meta:
            return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
        return None, None
    if not isinstance(params, dict):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
    if "_meta" not in params:
        if require_meta:
            return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
        return None, None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
    if not require_meta and set(meta) == {"progressToken"} and type(meta.get("progressToken")) in (str, int):
        return None, None
    requested = meta.get("io.modelcontextprotocol/protocolVersion")
    if not isinstance(requested, str) or not requested:
        return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
    if requested not in PROTOCOLS:
        return None, {"code": UNSUPPORTED_PROTOCOL_VERSION,
                      "message": "Unsupported protocol version",
                      "data": {"supported": list(PROTOCOLS), "requested": requested}}
    capabilities = meta.get("io.modelcontextprotocol/clientCapabilities", {})
    if not isinstance(capabilities, dict):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
    client_info = meta.get("io.modelcontextprotocol/clientInfo")
    if client_info is not None and (
            not isinstance(client_info, dict)
            or not isinstance(client_info.get("name"), str)
            or not isinstance(client_info.get("version"), str)):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
    log_level = meta.get("io.modelcontextprotocol/logLevel")
    if log_level is not None and not isinstance(log_level, str):
        return None, {"code": INVALID_PARAMS, "message": "Invalid params"}
    return requested, None


def main():
    # MCP's stdio transport is UTF-8. A client writes a request in it whatever the platform's code
    # page, and each reply below is seven-bit JSON, which reads the same in any (#71).
    A.utf8_streams()
    root = None
    allow_verify = "--allow-verify" in sys.argv
    if "-C" in sys.argv:
        root = sys.argv[sys.argv.index("-C") + 1]
    # The role and project scope the registration names (MCP-ROLES, MCP-RES): fixed for the session,
    # since a harness starts its servers once, when its session starts. Read without case or
    # surrounding space, so a hand-written `Reviewer` is the reviewer; a role or `--project` value
    # the server does not know stops the server before it answers anything (HUNT-LEADS-SEC).
    try:
        role = served_role(sys.argv[1:])
        project_scope = served_project(sys.argv[1:])
    except ValueError as refused:
        print(refused, file=sys.stderr, flush=True)
        return 2
    served = role_tools(role)
    notice = role_notice(role)
    if notice:
        print(notice, file=sys.stderr, flush=True)
    cfg = {"root": A.find_root(root)} if project_scope == "all" else A.load_config(A.find_root(root))

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (ValueError, RecursionError):
            # Not JSON - or JSON this parser will not hold, a number of more than 4300 digits or nesting deeper than
            # it recurses, which raised no JSONDecodeError and ended the server (LEADS-MCP).
            continue
        if not isinstance(req, dict):
            # A JSON array or a scalar is no request, and reading it as one ended the server, as it ended the A2A
            # bridge (A2A-HARDEN): answered as invalid, and the next line is read (LEADS-MCP).
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": "a request is a JSON object"}}) + "\n")
            sys.stdout.flush()
            continue
        rid, method = req.get("id"), req.get("method")
        # Never dispatch or echo a container id: encoding a deeply nested one ended the server (LEADS-OPEN).
        if (rid is not None and type(rid) not in (str, int, float)) or (
                isinstance(rid, float) and not math.isfinite(rid)):
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": "a request id must be a string, finite number or null"}}) + "\n")
            sys.stdout.flush()
            continue
        try:
            error = None
            negotiated = None
            if method in ("notifications/initialized", "initialized"):
                continue                                  # notification: no reply
            elif method == "initialize":
                result, error = _negotiate_initialize(req.get("params"))
            else:
                params = req.get("params")
                negotiated, error = _negotiate_meta(params, require_meta=(method == "server/discover"))
                if error is None:
                    if method == "server/discover":
                        result = _discover_result()
                    elif method == "tools/list":
                        result = {"tools": tools_for_role(role, project_scope)}
                    elif method == "tools/call":
                        p = params if isinstance(params, dict) else {}
                        args = p.get("arguments") if isinstance(p.get("arguments"), dict) else {}
                        payload = call(p.get("name"), args, cfg, allow_verify, role, project_scope)
                        # Ride along on whatever the agent called. MCP has no way to push,
                        # so the next best thing is to attach the message to the next
                        # response it asks for — which costs nothing and arrives sooner
                        # than the agent's own next inbox check. Only where ao_inbox is served:
                        # the note sends the agent there, and a role without it - a reviewer -
                        # can neither read the message nor acknowledge it.
                        if (isinstance(payload, dict) and "error" not in payload
                                and p.get("name") != "ao_inbox" and "ao_inbox" in served):
                            resolved, problem = _resolve_project(cfg, project_scope, args.get("project"))
                            if problem is None:
                                _, target_root, target_cfg = resolved
                                urgent = A.urgent_messages(target_root, target_cfg)
                                if urgent:
                                    payload["URGENT_UNACKNOWLEDGED"] = [
                                        {"id": u["id"], "title": u["title"]} for u in urgent]
                                    payload["URGENT_NOTE"] = (
                                        "Read these with ao_inbox and acknowledge them before "
                                        "continuing. ao commit-ok will refuse while any remain.")
                        result = {"content": [{"type": "text",
                                               "text": json.dumps(payload, ensure_ascii=False,
                                                                  indent=2, default=str)}]}
                    elif method == "resources/list":
                        result, error = resources_list(cfg, project_scope, params)
                    elif method == "resources/templates/list":
                        result, error = resource_templates_list(params)
                    elif method == "resources/read":
                        result, error = resource_read(cfg, project_scope, params, negotiated=negotiated)
                    elif method == "prompts/list":
                        result, error = prompts_list(project_scope, params, role=role)
                    elif method == "prompts/get":
                        result, error = prompt_get(cfg, project_scope, params, role=role)
                    elif method == "ping":
                        result = {}
                    else:
                        raise ValueError(f"method not found: {method}")
                    if error is None and method != "server/discover" and negotiated == PROTOCOL_2026:
                        result = dict({"resultType": "complete"}, **result,
                                      _meta={"io.modelcontextprotocol/serverInfo": _server_info()})
        except Exception as e:                            # never take the transport down
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "error": {"code": -32603, "message": str(e)}}) + "\n")
            sys.stdout.flush()
            continue
        if error is not None:
            if rid is not None:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": error}, default=str) + "\n")
                sys.stdout.flush()
            continue
        if rid is not None:
            # Escaped to ASCII, as the error reply above always was: this write is outside the handler, and
            # a character the stream cannot carry, or a lone surrogate a request held, took the server down.
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
