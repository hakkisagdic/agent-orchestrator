"""`ao status`, `ao fleet` and `ao notices` print their facts as one JSON document with --json (JSON-OUTPUT).

A program that wanted what the panel shows had to read the panel: strip its colours, find its
sections by their rules of box characters and parse numbers out of sentences that wrap with the
width of a terminal. With --json each command prints the same facts as one document and nothing
else on stdout, read by the same function the text is drawn from, so the two cannot drift apart.
A document puts nothing in front of a person, so unlike the panel it records no message as seen.
The text is unchanged byte for byte: the goldens below are what each command printed before its
facts were separated from its printing, colours written as their names.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ao import cli, lib as A, watchdog as W
from tests.test_cli_robust import _printed
from tests.test_session_identity import _kiro_session

ROOT = Path(__file__).resolve().parent.parent

NOW = datetime(2026, 9, 26, 14, 30, tzinfo=timezone.utc).timestamp()
TAGS = {"reset": "</>", "dim": "<dim>", "b": "<b>", "green": "<green>", "red": "<red>", "yellow": "<yellow>",
        "cyan": "<cyan>", "mag": "<mag>", "blue": "<blue>"}


class _Clock(datetime):
    """The command line's datetime at a fixed moment, every time read in UTC, so a golden holds in any time zone."""

    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(NOW, tz)

    @classmethod
    def fromtimestamp(cls, t, tz=None):
        return datetime.fromtimestamp(t, tz or timezone.utc)


def _fixed(monkeypatch):
    """A fixed clock, a terminal 60 columns wide, no role, and each colour written as its name."""
    monkeypatch.setattr(cli, "datetime", _Clock)
    monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: NOW, strftime=time.strftime, localtime=time.gmtime))
    monkeypatch.setattr(cli, "C", TAGS)
    monkeypatch.setenv("COLUMNS", "60")
    monkeypatch.setenv("LINES", "40")
    monkeypatch.delenv("AO_ROLE", raising=False)


# ---- one fixed project, as every reader the panel asks answers for it ------------------------------

MAIL = ["20260926-1200-kiro-to-fable-BLOCKED-queue.md", "20260926-1300-fable-to-kiro-INFO-next.md"]
MESSAGES = [
    ("13:50", "user", "please finish the store and then run the migration check before you stage anything"),
    ("13:58", "assistant", "the store is done; the migration check fails on the second table because the column "
                           "type changed and the fixture was not updated to match it"),
    ("14:01", "user", "update the fixture, run the check again and stage the candidate once it passes"),
    ("14:05", "assistant", "the fixture is updated and the migration check passes; the candidate is staged and "
                           "verified"),
]
BOARD = {"running": [{"id": "S3", "title": "store", "notes": {}}],
         "blocked": [{"id": "S2", "title": "sync", "notes": {"needs": "a key from the vendor, which the owner "
                                                                       "requests through its portal"}},
                     {"id": "S6", "title": "auth", "notes": {}}],
         "queued": [{"id": "S4", "title": "export", "notes": {}}, {"id": "S5", "title": "import", "notes": {}}],
         "inbox": [], "verified": [], "done": [{"id": "S1", "title": "schema", "notes": {}}], "rejected": []}
THROUGHPUT = {"staged": 2, "landed": 0, "decisions_asked": 1, "decisions_open": 1, "oldest_open_minutes": 95,
              "state": "stalled", "stall": {"minutes": 75, "candidate": "sha256:c0ffee",
                                            "paths": ["src/a.py", "src/b.py", "src/c.py", "src/d.py"],
                                            "reason": "waiting on decision D-1: which store?"}}


def _panel(monkeypatch):
    """Every reader the status panel asks, answering for one fixed project whatever this machine holds."""
    answers = {
        "load_adapter": lambda adapter_id, root=None: {"id": adapter_id},
        "busy": lambda cfg, adapter: ("idle", 754, "running the unit tests for the store, then the migration check "
                                                   "that follows them"),
        "session_state": lambda cfg, role: {"session": "s1", "how": "pinned", "why": None, "trusted": True,
                                            "count": 1},
        "working_elsewhere": lambda cfg, idle_seconds: {"name": "acme-web", "root": "/work/acme-web", "age": 42.7},
        "session_paths": lambda cfg: ("/work/transcript.jsonl", None),
        "read_tail": lambda path, nbytes=900_000: [],
        "telemetry": lambda recs, adapter, transcript=None: {"ctx": 87.4, "total": 1200.0, "turns": 4,
                                                             "last": (700.0, 12), "unit": "credit",
                                                             "delegated": 150.0},
        "quota": lambda adapter, ttl=300: ["Claude (Anthropic)  69%  5h  resets in 49m"],
        "last_nudge_error": lambda root: {"at": NOW - 600, "code": 2,
                                          "tail": "error: the agent could not start: its login expired, and a "
                                                  "person has to run the login again"},
        "recent_errors": lambda recs, limit=3, adapter=None: [
            ("14:02", "FAIL tests/test_store.py::test_round_trip - AssertionError: 1 != 2")],
        "spinning": lambda root, min_minutes=6, min_samples=3: 14,
        "project_key": lambda root: "acme-api",
        "returned_reviews": lambda root: [{"id": "R-17", "slice": "S3", "verdict": "NEEDS_CHANGES",
                                           "state": "finished"},
                                          {"id": "R-18", "state": "failed"}],
        "reviews": lambda root, reviews_dir, limit=4: [("2026-09-26-1200-S3-pr1.md", "NEEDS_CHANGES"),
                                                       ("2026-09-26-1100-S3-pr0.md", "APPROVED")],
        "rounds": lambda root, reviews_dir: 6,
        "throughput": lambda root, cfg, hours=24.0, now=None: dict(THROUGHPUT, hours=hours),
        "git_state": lambda root: {"log": ["abc1234 add the store", "def5678 add the schema", "0123456 init",
                                           "89abcde an older commit"],
                                   "dirty": [" M a.py", "?? b.py"], "ahead": "3", "behind": 2,
                                   "base": "origin/main", "merged": False},
        "mailbox": lambda root, mail_dir: list(MAIL),
        "urgent_messages": lambda root, cfg, role="implementer": [
            {"id": MAIL[0], "title": "queue is empty and S3 needs a decision", "body": "# queue is empty",
             "to": "architect"}],
        "mail_seen": lambda root, names, by: [],
        "unseen_messages": lambda root, cfg: [
            {"id": MAIL[1], "class": "needs-read", "at": NOW - 3000, "age": 3000.0},
            {"id": "20260926-1429-kiro-to-fable-INFO-done.md", "class": "fyi", "at": NOW - 60, "age": 60.0}],
        "board": lambda root: {state: list(items) for state, items in BOARD.items()},
        "messages": lambda recs, limit=8, adapter=None: MESSAGES[-limit:],
    }
    for name, answer in answers.items():
        monkeypatch.setattr(A, name, answer)
    monkeypatch.setattr(W, "load_state", lambda root: {"idle_answer": {"since": NOW - 3 * 3600 - 25 * 60}})


def _nobody(monkeypatch):
    """The same project with no implementer, nothing in its mailbox or on its board, and no remote to compare to."""
    answers = {
        "all_workspaces": lambda: [{"path": "/work/acme-web", "mtime": NOW - 300},
                                   {"path": "/work/acme-docs", "mtime": NOW - 3 * 3600 - 60}],
        "returned_reviews": lambda root: [],
        "reviews": lambda root, reviews_dir, limit=4: [],
        "git_state": lambda root: {"log": [""], "dirty": [], "ahead": "?", "behind": None, "base": None,
                                   "merged": False},
        "mailbox": lambda root, mail_dir: [],
        "urgent_messages": lambda root, cfg, role="implementer": [],
        "unseen_messages": lambda root, cfg: [],
        "board": lambda root: {state: [] for state in BOARD},
    }
    for name, answer in answers.items():
        monkeypatch.setattr(A, name, answer)


# ---- the fleet: four workspaces, one of them no project ao can read ---------------------------------

WORKSPACES = {
    "/work/acme-api": {"cfg": {"project": "acme-api", "implementer": {"adapter": "kiro", "session": "s1"}},
                       "busy": ("working", 75, "writing the store and its tests"),
                       "session": {"session": "s1", "how": "pinned", "why": None},
                       "board": {"running": 1, "queued": 2}, "dirty": 2, "mail": 0, "spin": None, "sources": True},
    "/work/acme-web": {"cfg": {"project": "acme-web", "implementer": {"adapter": "kiro", "session": "s2"}},
                       "busy": ("idle", 4000, "waiting for a decision on the cache, which the architect has not "
                                              "answered since the morning"),
                       "session": {"session": "s2", "how": "pinned", "why": None},
                       "board": {"blocked": 1}, "dirty": 0, "mail": 3, "spin": 12, "sources": False},
    "/work/acme-docs": {"cfg": {"project": "acme-documentation-and-examples"},
                        "session": {"session": None, "how": "ambiguous",
                                    "why": "two sessions were written in this workspace within a minute"},
                        "board": {}, "dirty": 0, "mail": 1, "spin": None, "sources": True},
}


def _fleet(monkeypatch):
    """Every reader the fleet asks, for the workspaces above and one whose config cannot be read."""
    def load_config(root):
        if root not in WORKSPACES:
            raise ValueError("config.json is not JSON")
        return dict(WORKSPACES[root]["cfg"], root=root, mailbox="agent-mail", reviews="semantic-review")

    answers = {
        "all_workspaces": lambda: [{"path": path, "mtime": NOW} for path in (*WORKSPACES, "/work/unreadable")],
        "load_config": load_config,
        "load_adapter": lambda adapter_id, root=None: {"id": adapter_id},
        "busy": lambda cfg, adapter: WORKSPACES[cfg["root"]]["busy"],
        "session_state": lambda cfg, role: WORKSPACES[cfg["root"]]["session"],
        "board": lambda root: {state: [{"id": f"{state}-{n}", "title": state, "notes": {}}
                                       for n in range(WORKSPACES[root]["board"].get(state, 0))] for state in BOARD},
        "git_state": lambda root: {"log": [], "dirty": ["?? x.py"] * WORKSPACES[root]["dirty"], "ahead": "0",
                                   "behind": 0, "base": "origin/main", "merged": False},
        "mailbox": lambda root, mail_dir: [f"m{n}.md" for n in range(WORKSPACES[root]["mail"])],
        "spinning": lambda root, min_minutes=6, min_samples=3: WORKSPACES[root]["spin"],
        "sources": lambda root: [{"name": "tracker"}] if WORKSPACES[root]["sources"] else [],
    }
    for name, answer in answers.items():
        monkeypatch.setattr(A, name, answer)


# ---- notices: a legacy row with no id, a held one and one with its evidence -------------------------

NOTICES_RECORDED = [
    {"at": int(NOW) - 7200, "title": "acme-api: nudge failed", "msg": "exit 2", "sent": True},
    {"id": "N-1790429400001", "at": int(NOW) - 3600, "title": "acme-api: implementer stuck",
     "msg": "no write for 20m with a slice running", "sent": True, "key": "stuck"},
    {"id": "N-1790431200002", "at": int(NOW) - 1800, "title": "acme-api: implementer stuck",
     "msg": "no write for 50m with a slice running", "sent": False, "key": "stuck"},
    {"id": "N-1790432400003", "at": int(NOW) - 600, "title": "acme-api: credits run out 30 Sep",
     "msg": "5000/10000 at 1200/day", "sent": True, "key": "credits-exhaust",
     "evidence": {"check": "burn_rate", "samples": [{"value": "1000/10000", "source": "GetUsageLimits", "at": None},
                                                    {"value": "5000/10000", "source": None, "at": "yesterday"}]}},
]


def _noticed(root):
    """The notices above, written to the project's ledger as ao records them."""
    with open(os.path.join(root, ".ao", "ledger", "notices.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(row) + "\n" for row in NOTICES_RECORDED))


# ---- goldens: what each command printed before its facts were separated from its printing ----------

STATUS = "\n".join([
    "<b><cyan>════════════════════════════════════════════════════════════</>",
    "<b>  ACME-API</><dim>   agent-orchestrator   26 Sep 14:30:00</>",
    "<b><cyan>════════════════════════════════════════════════════════════</>",
    "",
    "<red><b>○ IDLE</>  <dim>kiro · last write 12m 34s ago</>",
    "  <dim>↳</> running the unit tests for the store, then the",
    "    migration check that follows them",
    "  <green>↳ working in acme-web</>  <dim>/work/acme-web · last write 42s ago there</>",
    "",
    "<b><mag>── QUOTA / CONTEXT ────────────────────────────────────────</>",
    "   context  <red>█████████████████░░░</> 87%<dim>  ← start a fresh session</>",
    "   cost     last turn <b>700</> credit (12 tool calls) · 4 turns, total <b>1200</><dim> (avg 300, "
    "150 delegated)</>  <yellow>⚠ 2.3× average</>",
    "   <dim>other tools on this machine (not kiro's pool):</>",
    "     <dim>Claude (Anthropic)  69%  5h  resets in 49m</>",
    "",
    "<b><red>── PROBLEMS ───────────────────────────────────────────────</>",
    "   <yellow>nothing to do</> <dim>since 11:05 (3h 25m): answered a nudge without changing anything; "
    "waiting for the board, the backlog, a decision or mail</>",
    "   <red>spinning</> <dim>14m busy, nothing committed or changed</>",
    "     <dim>activity is not progress — re-specify, split, or check for a wait loop</>",
    "   <red>restart failed</> <dim>10m ago · exit 2</>",
    "     <dim>error: the agent could not start: its login expired,</>",
    "     <dim>and a person has to run the login again</>",
    "     <dim>full log: ~/.ao/nudge-acme-api.log</>",
    "   <dim>14:02</> <yellow>agent error</>  FAIL tests/test_store.py::test_round_trip -",
    "        <dim>│</>  AssertionError: 1 != 2",
    "",
    "   <yellow><b>REVIEW RETURNED</> for S3: R-17 NEEDS_CHANGES — handle it before new work (<b>ao "
    "review collect R-17</>)",
    "",
    "   <yellow><b>REVIEW RETURNED</> for a slice: R-18 failed — handle it before new work (<b>ao review "
    "collect R-18</>)",
    "",
    "<b><mag>── REVIEWS ────────────────────────────────────────────────</>",
    "   <red>⚠ round 6/5 — over budget: re-specify, split, or change actor</>",
    "   2026-09-26-1200-S3   <yellow>NEEDS_CHANGES</>",
    "   2026-09-26-1100-S3   <green>APPROVED</>",
    "",
    "<b><mag>── THROUGHPUT 24h ─────────────────────────────────────────</>",
    "   <dim>staged 2 · landed 0 · decisions 1 asked, 1 waiting (oldest 1h 35m)</>",
    "   <red>stalled 1h 15m on a staged candidate (src/a.py,</>",
    "     <dim>src/b.py, src/c.py …) — waiting on decision D-1: which</>",
    "",
    "<b><mag>── REPOSITORY ─────────────────────────────────────────────</>",
    "   abc1234 add the store",
    "   def5678 add the schema",
    "   0123456 init",
    "   <dim>2 files uncommitted · </><dim>3 ahead / 2 behind origin/main</>  <yellow>behind</>",
    "",
    "   <b>Mailbox:</> 20260926-1200-kiro-to-fable-BLOCKED-queue.md, "
    "20260926-1300-fable-to-kiro-INFO-next.md",
    "   <red><b>URGENT</> for the architect: <b>queue is empty and S3 needs a decision</>  "
    "<dim>20260926-1200-kiro-to-fable-BLOCKED-queue.md</>",
    "   <dim>1 other message(s) waiting in agent-mail/</>",
    "   <yellow>oldest unseen</> 20260926-1300-fable-to-kiro-INFO-next.md  <dim>needs-read, 50m · 1 more "
    "unseen</>",
    "   <b>Board:</>   1 running · 2 blocked · 2 queued · 1 done",
    "     <red>⊘</> S2  sync — a key from the vendor, which the",
    "       <dim>owner requests through its portal</>",
    "     <red>⊘</> S6  auth — reason not recorded",
    "",
    "<b><mag>── RECENT MESSAGES ────────────────────────────────────────</>",
    "   <dim>13:50</> <blue>YOU  </>  please finish the store and then run the",
    "        <dim>│</>      migration check before you stage anything",
    "   <dim>13:58</> <cyan>AGENT</>  the store is done; the migration check fails",
    "        <dim>│</>      on the second table because the column type",
    "        <dim>│</>      changed and the fixture was not updated to",
    "   <dim>14:01</> <blue>YOU  </>  update the fixture, run the check again and",
    "        <dim>│</>      stage the candidate once it passes",
    "   <dim>14:05</> <cyan>AGENT</>  the fixture is updated and the migration check",
    "        <dim>│</>      passes; the candidate is staged and verified",
]) + "\n"

NOBODY = "\n".join([
    "<b><cyan>════════════════════════════════════════════════════════════</>",
    "<b>  ACME-API</><dim>   agent-orchestrator   26 Sep 14:30:00</>",
    "<b><cyan>════════════════════════════════════════════════════════════</>",
    "",
    "<yellow>No implementer session found for this workspace.</>",
    "   <dim>/work/acme-api</>",
    "",
    "   Run it from a project, or point at one:",
    "     <b>ao -C /work/acme-web</>  <dim>5m ago</>",
    "     <b>ao -C /work/acme-docs</>  <dim>3h ago</>",
    "",
    "<b><mag>── REPOSITORY ─────────────────────────────────────────────</>",
    "   <dim>0 files uncommitted · </><yellow>no remote default branch to compare against</>",
    "",
    "   <b>Mailbox:</> <dim>empty</>",
]) + "\n"

FLEET = "\n".join([
    "<b><cyan>════════════════════════════════════════════════════════════</>",
    "<b>  ALL PROJECTS</><dim>   agent-orchestrator   26 Sep 14:30:00</>",
    "<b><cyan>════════════════════════════════════════════════════════════</>",
    "",
    " <red>○</> <b>acme-web              </><dim>idle      66m</>  q0 r0 <dim>·</> 0 dirty   "
    "<red>spinning 12m</>  <red>1 blocked</>  <cyan>3 mail</>  <dim>no source</>",
    "     <dim>↳ waiting for a decision on the cache, which the</>",
    "",
    " <green>●</> <b>acme-api              </><dim>working    1m</>  q2 r1 <dim>·</> 2 dirty",
    "     <dim>↳ writing the store and its tests</>",
    "",
    " <dim>?</> <b>acme-documentation-and</><dim>unknown     —</>  q0 r0 <dim>·</> 0 dirty   <cyan>1 "
    "mail</>",
    "     <dim>↳ session ambiguous: two sessions were written in this</>",
]) + "\n"

NOTICES = "\n".join([
    "  <dim>26 Sep 14:20</>  <green>sent</>  <b>acme-api: credits run out 30 Sep</>  5000/10000 at "
    "1200/day  <dim>N-1790432400003 · evidence</>",
    "  <dim>26 Sep 13:30</>  <green>sent</>  <b>acme-api: implementer stuck</>  no write for 20m with a "
    "slice running  <dim>N-1790429400001</>",
    "  <dim>26 Sep 12:30</>  <green>sent</>  <b>acme-api: nudge failed</>  exit 2",
    "<dim>  (--all also shows alerts the rate limit suppressed)</>",
]) + "\n"

NOTICE = "\n".join([
    "<b>acme-api: credits run out 30 Sep</>  <dim>N-1790432400003 · 26 Sep 14:20 · sent · key "
    "credits-exhaust</>",
    "  5000/10000 at 1200/day",
    "  check: burn_rate",
    "    1000/10000  from GetUsageLimits, time unknown",
    "    5000/10000  from ?, time unknown",
]) + "\n"

# The keys each document holds, as the pages describing the commands list them.
STATUS_KEYS = {"project", "root", "at", "implementer", "workspaces", "telemetry", "problems", "returned_reviews",
               "reviews", "rounds", "throughput", "repository", "mail", "board", "messages"}
IMPLEMENTER_KEYS = {"adapter", "state", "seconds_since_write", "doing", "session", "working_elsewhere"}
TELEMETRY_KEYS = {"context_percent", "cost_unit", "turns", "cost_total", "cost_average", "cost_delegated",
                  "last_turn_cost", "last_turn_tool_calls", "machine_quota"}
PROBLEM_KEYS = {"nothing_to_do_since", "spinning_minutes", "nudge_error", "agent_errors"}
THROUGHPUT_KEYS = {"hours", "staged", "landed", "decisions_asked", "decisions_open", "oldest_open_minutes", "state",
                   "stall"}
REPOSITORY_KEYS = {"log", "dirty_files", "ahead", "behind", "base", "merged"}
MAIL_KEYS = {"dir", "waiting", "urgent", "unseen"}
BOARD_KEYS = {"counts", "blocked"}
# Keys of the objects inside those: a session, a nudge error, an agent error, a review, the rounds, a stall,
# a workspace, a marked or unseen message, a blocked item and a message.
NESTED_STATUS_KEYS = {"id", "how", "why", "name", "path", "at", "code", "tail", "log", "time", "text", "file",
                      "verdict", "slice", "spent", "budget", "minutes", "candidate", "paths", "reason", "to",
                      "title", "class", "age_seconds", "role"}
FLEET_KEYS = {"name", "root", "state", "seconds_since_write", "doing", "session", "queued", "blocked", "running",
              "dirty_files", "mail_waiting", "spinning_minutes", "has_source"}
NOTICE_KEYS = {"id", "at", "title", "msg", "sent", "key", "evidence"}


def _shown(capsys, command, cfg, args):
    """What a command returned and printed."""
    code = command(cfg, args)
    return code, capsys.readouterr().out


def _in_colour(monkeypatch):
    """A terminal that draws colour, so a code that leaked into a fact would be there to find."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def _coloured(document):
    """The strings in a parsed document, its keys among them, that carry a colour code.

    json.dumps writes ESC as \\u001b, so the printed text never holds the byte itself, whatever
    leaked into a fact: only the strings the document parses to can show one.
    """
    if isinstance(document, dict):
        return [text for key, value in document.items() for text in _coloured(key) + _coloured(value)]
    if isinstance(document, list):
        return [text for value in document for text in _coloured(value)]
    return [document] if isinstance(document, str) and "\x1b" in document else []


def _seen(root):
    """Which messages the mail ledger says were put in front of a reader, and whom: (id, by) per `seen` row."""
    return [(row["id"], row["by"]) for row in A.mail_log(root, 5000) if row.get("event") == "seen"]


def _lived_in(project, monkeypatch):
    """The temporary project with a Kiro session of its own, an item blocked on its board and a marked message."""
    root = project["root"]
    monkeypatch.setattr(A, "quota", lambda adapter, ttl=300: [])        # no keyflip is asked
    _kiro_session(root, "s1", 600)
    with open(os.path.join(root, ".ao", "board.md"), "w", encoding="utf-8") as fh:
        fh.write("# Board\n\n## running\n\n- [S3] store\n\n## blocked\n\n- [S2] sync · needs: a key\n\n"
                 "## queued\n\n- [S4] export\n\n## verified\n\n## done\n")
    with open(os.path.join(root, project["mailbox"], MAIL[0]), "w", encoding="utf-8") as fh:
        fh.write("# queue is empty and S3 needs a decision\n\n## URGENT\n\nwhich store?\n")
    monkeypatch.delenv("AO_ROLE", raising=False)
    _in_colour(monkeypatch)                             # the document still has none
    return root


# ---- the documents -------------------------------------------------------------------------------

def test_ao_status_json_prints_one_document_holding_what_the_panel_shows(project, monkeypatch):
    root = _lived_in(project, monkeypatch)

    code, out = _printed(project, monkeypatch, True, "status", "--json", "--window", "12h")
    facts = json.loads(out)

    assert code == 0 and out.count("\n") == 1 and _coloured(facts) == [] and "URGENT" not in out
    assert set(facts) == STATUS_KEYS and set(facts["implementer"]) == IMPLEMENTER_KEYS
    assert set(facts["telemetry"]) == TELEMETRY_KEYS and set(facts["problems"]) == PROBLEM_KEYS
    assert set(facts["throughput"]) == THROUGHPUT_KEYS and set(facts["repository"]) == REPOSITORY_KEYS
    assert set(facts["mail"]) == MAIL_KEYS and set(facts["board"]) == BOARD_KEYS
    implementer = facts["implementer"]
    assert (facts["project"], os.path.realpath(facts["root"])) == ("proj", os.path.realpath(root))
    assert (implementer["adapter"], implementer["state"], implementer["session"]) == \
        ("kiro", "idle", {"id": "s1", "how": "pinned", "why": None})
    assert implementer["seconds_since_write"] >= 600 and facts["workspaces"] is None
    assert (facts["throughput"]["hours"], facts["throughput"]["state"]) == (12.0, "busy")
    assert facts["board"]["counts"]["running"] == facts["board"]["counts"]["blocked"] == 1
    assert facts["board"]["blocked"] == [{"id": "S2", "title": "sync", "why": "a key"}]
    assert facts["mail"]["urgent"] == [{"id": MAIL[0], "to": "architect",
                                        "title": "queue is empty and S3 needs a decision"}]
    # Named in the document, the marked message is still unseen: no reader was shown it (#30).
    assert facts["mail"]["waiting"] == [MAIL[0]] and [m["id"] for m in facts["mail"]["unseen"]] == [MAIL[0]]
    # A fresh repository has no remote default branch, so there is nothing to count ahead or behind of.
    repository = facts["repository"]
    assert repository["log"][0].endswith(" init")
    assert (repository["ahead"], repository["behind"], repository["base"]) == (None, None, None)


@pytest.mark.parametrize("role", [None, "architect"])
def test_the_document_records_no_marked_message_as_seen_where_the_panel_does(project, monkeypatch, role):
    # A status bar or a scheduled job polls the document, under whatever AO_ROLE it inherited. Recorded
    # as seen, a request no person was shown would never climb the watchdog's ladder (#30).
    root = _lived_in(project, monkeypatch)
    if role:
        monkeypatch.setenv("AO_ROLE", role)

    code, out = _printed(project, monkeypatch, False, "status", "--json")
    mail = json.loads(out)["mail"]

    assert code == 0 and [m["id"] for m in mail["urgent"]] == [m["id"] for m in mail["unseen"]] == [MAIL[0]]
    assert _seen(root) == [] and [m["id"] for m in A.unseen_messages(root, project)] == [MAIL[0]]

    code, out = _printed(project, monkeypatch, False, "status")

    # The panel's banner puts the message in front of whoever runs it, and that is recorded.
    assert code == 0 and "URGENT" in out and _seen(root) == [(MAIL[0], role or "person")]
    assert A.unseen_messages(root, project) == []


def test_without_an_implementer_the_document_names_the_workspaces_to_point_at(project, monkeypatch, tmp_path):
    path = os.path.join(project["root"], ".ao", "config.json")
    with open(path, encoding="utf-8") as fh:
        stored = json.load(fh)
    del stored["implementer"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(stored, fh)
    # Six workspaces with a Kiro session each, the newest written a minute ago: the panel offers the
    # five newest to point ao at, and so does the document.
    places = []
    for n in range(6):
        place = tmp_path / f"acme-{n}"
        place.mkdir()
        _kiro_session(str(place), f"s{n}", 60 * (n + 1), folder=f"ws-{n}")
        places.append(str(place))
    _in_colour(monkeypatch)

    code, out = _printed(project, monkeypatch, True, "status", "--json")
    facts = json.loads(out)

    assert code == 0 and facts["implementer"] is None and _coloured(facts) == []
    assert all(set(workspace) == {"path", "seconds_since_write"} for workspace in facts["workspaces"])
    assert [workspace["path"] for workspace in facts["workspaces"]] == places[:5]
    assert [workspace["seconds_since_write"] // 60 for workspace in facts["workspaces"]] == [1, 2, 3, 4, 5]
    assert (facts["telemetry"], facts["problems"], facts["throughput"], facts["messages"]) == (None, None, None, [])


def test_ao_fleet_json_prints_one_row_per_project(project, monkeypatch):
    root = _lived_in(project, monkeypatch)

    code, out = _printed(project, monkeypatch, True, "fleet", "--json")
    facts = json.loads(out)

    assert code == 0 and out.count("\n") == 1 and _coloured(facts) == [] and isinstance(facts["at"], int)
    [row] = facts["projects"]
    assert set(row) == FLEET_KEYS and os.path.realpath(row["root"]) == os.path.realpath(root)
    assert (row["name"], row["state"], row["session"]) == ("proj", "idle", {"id": "s1", "how": "pinned", "why": None})
    assert (row["running"], row["blocked"], row["queued"], row["mail_waiting"]) == (1, 1, 1, 1)
    assert row["seconds_since_write"] >= 600 and (row["spinning_minutes"], row["has_source"]) == (None, False)


def test_ao_notices_json_prints_the_rows_and_one_notice_by_its_id(project, monkeypatch):
    root = project["root"]
    with open(os.path.join(root, ".ao", "ledger", "notices.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(NOTICES_RECORDED[0]) + "\n")                  # raised before notices had ids
    evidence = A.notice_evidence("burn_rate", [{"value": "5000/10000", "source": "GetUsageLimits",
                                                "at": time.time()}])
    A.record_notice(root, "proj: implementer stuck", "no write for 20m", True, key="stuck")
    A.record_notice(root, "proj: implementer stuck", "no write for 50m", False, key="stuck")
    A.record_notice(root, "proj: credits run out", "5000/10000 at 1200/day", True, key="credits-exhaust",
                    evidence=evidence)
    _in_colour(monkeypatch)

    code, out = _printed(project, monkeypatch, True, "notices", "--json")
    listed = json.loads(out)

    assert code == 0 and out.count("\n") == 1 and _coloured(listed) == [] and listed["include_suppressed"] is False
    assert all(set(notice) == NOTICE_KEYS for notice in listed["notices"])
    assert [(n["msg"], n["sent"]) for n in listed["notices"]] == \
        [("5000/10000 at 1200/day", True), ("no write for 20m", True), ("exit 2", True)]
    assert listed["notices"][0]["evidence"] == evidence and listed["notices"][2]["id"] is None
    assert listed["notices"][2]["key"] is None and listed["notices"][1]["key"] == "stuck"

    code, out = _printed(project, monkeypatch, True, "notices", "--all", "-n", "2", "--json")
    assert code == 0 and [n["sent"] for n in json.loads(out)["notices"]] == [True, False]
    assert json.loads(out)["include_suppressed"] is True

    newest = listed["notices"][0]
    code, out = _printed(project, monkeypatch, True, "notices", newest["id"], "--json")
    assert code == 0 and json.loads(out) == {"notice": newest}

    code, out = _printed(project, monkeypatch, True, "notices", "N-0", "--json")
    assert code == 1 and json.loads(out)["notice"] is None and "N-0" in json.loads(out)["error"]


@pytest.mark.parametrize("page, keys", [
    ("docs/surfaces.md", STATUS_KEYS | IMPLEMENTER_KEYS | TELEMETRY_KEYS | PROBLEM_KEYS | THROUGHPUT_KEYS
     | REPOSITORY_KEYS | MAIL_KEYS | BOARD_KEYS | NESTED_STATUS_KEYS),
    ("docs/parallel.md", FLEET_KEYS | {"at", "projects", "id", "how", "why"}),
    ("docs/telemetry.md", NOTICE_KEYS | {"notices", "include_suppressed", "notice", "error", "check", "samples",
                                         "value", "source"}),
])
def test_every_key_is_named_on_the_page_that_describes_its_command(page, keys):
    text = (ROOT / page).read_text(encoding="utf-8")

    assert "*In ao since slice JSON-OUTPUT" in text
    assert sorted(key for key in keys if f"`{key}`" not in text and f'"{key}"' not in text) == []


# ---- the text, byte for byte ---------------------------------------------------------------------

def test_the_status_panel_reads_as_it_did_before_its_facts_were_separated(project, monkeypatch, capsys):
    _fixed(monkeypatch)
    _panel(monkeypatch)
    cfg = dict(project, root="/work/acme-api", project="acme-api")

    assert _shown(capsys, cli.cmd_status, cfg, SimpleNamespace(messages=4, window=24.0)) == (None, STATUS)
    panel = STATUS.rstrip("\n").split("\n")
    # ao watch bounds it: the fixed sections first, and what room is left goes to the messages.
    hidden = "   <dim>… older messages hidden (window too short)</>"
    assert cli.render(cfg, 4, 60, 5) == "\n".join(panel[:5])
    assert cli.render(cfg, 4, 60, 60) == "\n".join(panel[:59] + [hidden])

    bare = {key: value for key, value in cfg.items() if key != "implementer"}
    _nobody(monkeypatch)
    assert _shown(capsys, cli.cmd_status, bare, SimpleNamespace(messages=4, window=24.0)) == (None, NOBODY)
    monkeypatch.setattr(A, "all_workspaces", lambda: [])
    nowhere = NOBODY.split("\n")
    nowhere[6:10] = ["   <dim>No local agent sessions found at all. See docs/adapters.md.</>"]
    assert _shown(capsys, cli.cmd_status, bare, SimpleNamespace(messages=4, window=24.0)) == (None, "\n".join(nowhere))


def test_the_fleet_reads_as_it_did_before_its_facts_were_separated(project, monkeypatch, capsys):
    _fixed(monkeypatch)
    _fleet(monkeypatch)

    assert _shown(capsys, cli.cmd_fleet, {}, SimpleNamespace()) == (0, FLEET)
    monkeypatch.setattr(A, "all_workspaces", lambda: [])
    assert _shown(capsys, cli.cmd_fleet, {}, SimpleNamespace()) == \
        (0, "\n".join(FLEET.split("\n")[:3] + ["", "<dim>No workspaces with a local agent session.</>", ""]))


def test_the_notices_read_as_they_did_before_json_output(project, monkeypatch, capsys):
    _fixed(monkeypatch)
    _noticed(project["root"])
    held = ("  <dim>26 Sep 14:00</>  <dim>held</>  <b>acme-api: implementer stuck</>  no write for 50m with a slice "
            "running  <dim>N-1790431200002</>")
    listed = NOTICES.split("\n")

    assert _shown(capsys, cli.cmd_notices, project, SimpleNamespace(n=12, all=False, ident=None)) == (0, NOTICES)
    assert _shown(capsys, cli.cmd_notices, project, SimpleNamespace(n=3, all=True, ident=None)) == \
        (0, "\n".join([listed[0], held, listed[1], ""]))
    assert _shown(capsys, cli.cmd_notices, project, SimpleNamespace(n=12, all=False, ident="N-1790432400003")) == \
        (0, NOTICE)
    assert _shown(capsys, cli.cmd_notices, project, SimpleNamespace(n=12, all=False, ident="N-0")) == \
        (1, "no notice N-0; `ao notices --all` lists them with their ids\n")
    os.remove(os.path.join(project["root"], ".ao", "ledger", "notices.jsonl"))
    assert _shown(capsys, cli.cmd_notices, project, SimpleNamespace(n=12, all=False, ident=None)) == \
        (0, "<dim>No notices recorded.</>\n")
