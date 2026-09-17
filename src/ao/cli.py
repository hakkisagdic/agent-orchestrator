#!/usr/bin/env python3
"""ao — agent-orchestrator's command line.

Every command is in build_parser(), with its code in a part under src/ao/parts/ that runs in
this module's namespace; the playbook, src/ao/skill/SKILL.md, lists each one.

Standard library only. Observation of an agent's session is strictly read-only.
"""
import argparse
import json
import os
import shutil
import subprocess
import signal
import sys
import textwrap
import time
from datetime import datetime

from . import lib as A
from . import matrix as M
from . import settings as S
from .verdicts import REVIEWER_VERDICTS
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

C = A.C


A._part("cli_status", globals())


A._part("cli_authority", globals())


A._part("cli_channels", globals())


A._part("cli_review", globals())


A._part("cli_project", globals())


A._part("cli_checks", globals())


A._part("cli_hooks", globals())


A._part("cli_maintenance", globals())


def build_parser():
    """The whole `ao` command line, built without running a command.

    main parses with it, and tests/test_docs_commands.py walks it, so a document that shows
    an invocation this parser refuses fails there rather than in a reader's terminal. Building
    it reads the adapter catalog once, for the --agent names init and skill share.
    """
    p = argparse.ArgumentParser(prog="ao", description="agent-orchestrator (observation layer)")
    p.add_argument("-C", "--root", help="project directory (default: nearest .ao/ or git root)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="one-shot summary")
    s.add_argument("-m", "--messages", type=int, default=6)
    s.add_argument("--window", type=float, default=24.0,
                   help="hours of throughput to report (default 24)")
    s.set_defaults(fn=cmd_status)

    w = sub.add_parser("watch", help="live panel; leave it in a background terminal")
    w.add_argument("-i", "--interval", type=int, default=15)
    w.add_argument("-m", "--messages", type=int, default=8)
    w.add_argument("--all", action="store_true", help="one row per project instead of one panel")
    w.set_defaults(fn=cmd_watch)

    sub.add_parser("fleet", help="one-shot view of every project").set_defaults(fn=cmd_fleet)

    t = sub.add_parser("tail", help="recent messages from the implementer's transcript")
    t.add_argument("-n", type=int, default=5)
    t.set_defaults(fn=cmd_tail)

    m = sub.add_parser("mail", help="list, read or send coordination messages")
    m.add_argument("action", choices=["list", "read", "send", "log", "search", "ack", "compact", "sync"])
    m.add_argument("type", nargs="?", default="INFO")
    m.add_argument("topic", nargs="?")
    m.add_argument("--body")
    m.add_argument("--class", dest="mail_class", choices=A.MAIL_CLASSES,
                   help="what the message asks of its reader; needs-decision escalates while unseen")
    m.set_defaults(fn=cmd_mail)

    v = sub.add_parser("verify", help="run the declared gates and record the result")
    v.add_argument("-p", "--profile")
    v.add_argument("--wait", type=int, default=900,
                   help="seconds to wait if another project holds the machine gate lock")
    v.set_defaults(fn=cmd_verify)

    mc = sub.add_parser("merge-check", help="run the gates on a merge's result before merging, and record it")
    mc.add_argument("branch", help="the branch or commit to be merged")
    mc.add_argument("--into", default="HEAD", help="what it is merged into (default HEAD)")
    mc.add_argument("-p", "--profile", help="gate profile (default full, when declared)")
    mc.add_argument("--wait", type=int, default=900,
                    help="seconds to wait if another project holds the machine gate lock")
    mc.set_defaults(fn=cmd_merge_check)

    wd = sub.add_parser("watchdog", help="launchd job that restarts a stalled agent")
    wd.add_argument("action", choices=["install", "uninstall", "status", "explain", "trace"])
    wd.add_argument("--interval", type=int, default=120)
    wd.add_argument("--idle-minutes", type=float, default=None,
                    help="default: the watchdog.idle_minutes setting, read when installed")
    wd.add_argument("--last", type=int, default=20)
    wd.set_defaults(fn=cmd_watchdog)

    bd = sub.add_parser("board", help="where each pre-authorised item is")
    bd.add_argument("view", nargs="?", choices=["ready"],
                    help="ready: exactly the items that may start now, exit 1 on a broken edge")
    bd.set_defaults(fn=cmd_board)
    ak = sub.add_parser("ask", help="pose a decision, answerable in one tap")
    ak.add_argument("question", nargs="?")
    ak.add_argument("options", nargs="*")
    ak.add_argument("--context")
    ak.add_argument("--slice")
    ak.add_argument("--codebase", action="store_true",
                    help="ask the code, through codebase.provider; the answer must cite files and lines")
    ak.set_defaults(fn=cmd_ask)
    an = sub.add_parser("answer", help="answer a pending decision")
    an.add_argument("id")
    an.add_argument("value", nargs="+")
    an.set_defaults(fn=cmd_answer)
    dc = sub.add_parser("decisions", help="open and answered questions")
    dc.add_argument("-n", type=int, default=10)
    dc.set_defaults(fn=cmd_decisions)
    st_ = sub.add_parser("stats", help="slice outcomes: rounds, first-pass rate, time, size, defects found later")
    st_.add_argument("--all", action="store_true", help="every project registered on this machine")
    st_.add_argument("--since", help="landed on or after YYYY-MM-DD")
    st_.add_argument("--until", help="landed before YYYY-MM-DD")
    st_.add_argument("--slices", action="store_true", help="one line per slice")
    st_.set_defaults(fn=cmd_stats)
    rc = sub.add_parser("recall", help="what was decided, found or learned before, across projects")
    rc.add_argument("text", nargs="+")
    rc.add_argument("-n", "--limit", type=int, default=10)
    rc.set_defaults(fn=cmd_recall)
    wt = sub.add_parser("worktrees", help="every worktree and whether it may go; prune retires those that may")
    wt.add_argument("action", nargs="?", choices=["list", "prune"], default="list")
    wt.add_argument("--yes", action="store_true", help="apply the prune (default is a dry run)")
    wt.set_defaults(fn=cmd_worktrees)
    ro = sub.add_parser("role", help="the role table: show, or reassign a role to an actor")
    ro.add_argument("action", nargs="?", choices=["show", "set", "swap"], default="show")
    ro.add_argument("role", nargs="?", choices=list(A.ROLE_BLOCKS))
    ro.add_argument("actor", nargs="?", help="set: an actor; swap: the other role")
    ro.add_argument("--model", help="with set reviewer <adapter>: the model the composed reviewer runs")
    ro.add_argument("--effort", help="with set reviewer <adapter>: its effort, where the adapter takes one")
    ro.add_argument("--family", help="with set reviewer <adapter>: the model's family; a tool reviewer needs one")
    ro.add_argument("--hotfix", action="store_true",
                    help="on a product repository: let the architect implement, named as a hotfix")
    ro.set_defaults(fn=cmd_role)
    spl = sub.add_parser("split-check", help="is the staged candidate a pure move between files?")
    spl.set_defaults(fn=cmd_split_check)
    ht = sub.add_parser("hunt", help="a bounded read-only bug hunt; leads go to the architect")
    ht.add_argument("action", nargs="?", choices=["run", "discard", "status"], default="run")
    ht.add_argument("fingerprint", nargs="?", help="discard: the lead's id")
    ht.set_defaults(fn=cmd_hunt)
    bk = sub.add_parser("backup", help="write the governance to a directory, a ref or a private remote")
    bk.add_argument("--to", help="a directory, `ref`, or `remote:<name>`; default backup.to in the config")
    bk.set_defaults(fn=cmd_backup)
    rsr = sub.add_parser("restore", help="reconstruct the governance from a backup directory")
    rsr.add_argument("source", help="a backup directory with its manifest.json")
    rsr.set_defaults(fn=cmd_restore)
    ct = sub.add_parser("content", help="borrow third-party skills pinned to a commit, text only; verify them")
    ct.add_argument("action", choices=["add", "verify"])
    ct.add_argument("spec", nargs="?", help="add: <source>@<40-character commit>")
    ct.add_argument("--skills", help="add: the skills to borrow, comma-separated")
    ct.add_argument("--harness", help="add: harness adapters to install into (default: those detected)")
    ct.set_defaults(fn=cmd_content)
    rm_ = sub.add_parser("room", help="messages across every registered project")
    rm_.add_argument("action", choices=["search"])
    rm_.add_argument("text", nargs="+")
    rm_.set_defaults(fn=cmd_room)
    dg = sub.add_parser("digest", help="what happened, read from the ledgers")
    dg.add_argument("--days", type=float, default=1.0)
    dg.add_argument("-n", type=int, default=6)
    dg.set_defaults(fn=cmd_digest)
    ini = sub.add_parser("init", help="put ao on this project (idempotent)")
    ini.add_argument("--name")
    from . import skillkit as _skillkit
    agents = _skillkit.agent_choices()      # init and skill offer the same names: one read of the adapter catalog
    ini.add_argument("--agent", choices=agents, default="auto")
    ini.add_argument("--mcp", action="store_true", help="(default) register the MCP server for detected agents")
    ini.add_argument("--no-mcp", action="store_true", help="skip the MCP registration")
    ini.add_argument("--rules", action="store_true", help="also write the pointer into the owner's rule files")
    ini.add_argument("--profile", choices=sorted(A.profiles()), help="write the role blocks: who implements, reviews, judges")
    ini.add_argument("--implementer", help="implementer adapter id (`ao adapters` lists them); overrides the profile")
    ini.add_argument("--model", help="implementer model, passed through the adapter's --model option")
    ini.add_argument("--effort", help="implementer effort, where its adapter takes one (options.effort_values)")
    ini.add_argument("--reviewer-model", dest="reviewer_model", help="reviewer model (default: its adapter's models.review)")
    ini.add_argument("--watchdog", action="store_true", help="also install the watchdog")
    ini.add_argument("--allow-uncovered-gates", action="store_true",
                     help="write quick gates that exercise none of the detected toolchains")
    ini.add_argument("--prove", action="store_true", help="finish by running ao prove")
    ini.add_argument("--no-review", action="store_true", help="with --prove: skip the throwaway review")
    ini.set_defaults(fn=_init_then_prove)
    pv = sub.add_parser("prove", help="run the guarantees: the hook refuses, the reviewer answers, a slice lands")
    pv.add_argument("--no-review", action="store_true",
                    help="skip the throwaway slice, which spends one short review")
    pv.set_defaults(fn=cmd_prove)
    de = sub.add_parser("decide", help="record an architect decision durably")
    de.add_argument("decision", nargs="?")
    de.add_argument("--why")
    de.add_argument("--answers", help="open decision id this settles, e.g. D-123")
    de.add_argument("--scope")
    de.add_argument("--to", default=None, help="a name; default: whoever holds the implementer role")
    de.add_argument("--urgent", action="store_true")
    de.add_argument("--list", action="store_true")
    de.add_argument("-n", type=int, default=10)
    de.set_defaults(fn=cmd_decide)
    si = sub.add_parser("since", help="what changed since you last looked")
    si.add_argument("ref", nargs="?",
                    help="last | 2h | 1d | <git ref>: one ref, given to git as a single argument, never an option")
    si.add_argument("--no-mark", action="store_true")
    si.set_defaults(fn=cmd_since)
    nt = sub.add_parser("note", help="write an architect message into the mailbox")
    nt.add_argument("title")
    nt.add_argument("--body")
    nt.add_argument("--to", default=None, help="a name; default: whoever holds the implementer role")
    nt.add_argument("--urgent", action="store_true")
    nt.add_argument("--stdin", action="store_true", help="read the body from stdin")
    nt.set_defaults(fn=cmd_note)
    hf = sub.add_parser("handoff", help="write and send everything a successor needs")
    hf.add_argument("--reason")
    hf.add_argument("--no-send", action="store_true")
    hf.set_defaults(fn=cmd_handoff)
    rw = sub.add_parser("review", help="review an exact staged candidate with an independent actor")
    rw.add_argument("action", nargs="?", choices=["submit", "collect"],
                    help="submit: pin the staged candidate and review it in the background; "
                         "collect: take a finished review")
    rw.add_argument("rid", nargs="?", help="collect: the review id")
    rw.add_argument("--any", action="store_true", help="collect: the oldest finished review")
    rw.add_argument("--run", help=argparse.SUPPRESS)
    rw.add_argument("--boundary", help="acceptance boundary; defaults to the running slice")
    rw.add_argument("--paths", nargs="*", help="narrow a prospective review to these staged paths")
    rw.add_argument("--commits", help="review landed work retrospectively; never authorizes a commit")
    # Accepted so existing callers keep working, and ignored: the timeout is
    # `review_timeout` in .ao/config.json (#63).
    rw.add_argument("--timeout", type=int, help=argparse.SUPPRESS)
    rw.set_defaults(fn=cmd_review)
    rs = sub.add_parser("reviews", help="submitted reviews: state, slice, elapsed and verdict")
    rs.set_defaults(fn=cmd_reviews)
    cr = sub.add_parser("collect-review",
                        help="a person records a stand-in session's answer to ao's review request")
    cr.add_argument("nonce")
    cr.add_argument("--response", required=True, help="file holding the session's whole answer")
    cr.add_argument("--model", required=True, help="the model the session ran, as you know it")
    cr.add_argument("--by", required=True, help="the person who carried the answer")
    cr.set_defaults(fn=cmd_collect_review)
    tg = sub.add_parser("telegram", help="phone channel: alerts out, decisions in")
    tg.add_argument("action", nargs="?", default="status",
                    choices=["status", "setup", "test", "poll", "install", "uninstall"])
    tg.add_argument("--once", action="store_true", help="poll: one pass then exit")
    tg.set_defaults(fn=cmd_telegram)
    cr = sub.add_parser("credits", help="credit spend measured from local transcripts")
    cr.add_argument("--offline", action="store_true", help="skip the account lookup")
    cr.add_argument("--local", action="store_true", help="also show this machine's share")
    cr.add_argument("--reset-day", type=int, help="fallback: override the renewal day")
    cr.set_defaults(fn=cmd_credits)
    cm = sub.add_parser("commit", help="commit the staged candidate after ao commit-ok; never skips hooks")
    source = cm.add_mutually_exclusive_group(required=True)
    source.add_argument("-m", "--message")
    source.add_argument("-F", "--file")
    cm.set_defaults(fn=cmd_commit)
    ck = sub.add_parser("commit-ok", help="grant authority for the exact staged index candidate")
    ck.add_argument("--verify", action="store_true", help="run the quick gates first when the verification is stale")
    ck.add_argument("-p", "--profile", help="gate profile for --verify (default quick)")
    ck.add_argument("--review", help="grant only if the staged tree is the tree this submitted review pinned")
    ck.set_defaults(fn=cmd_commit_ok)
    cc = sub.add_parser(
        "commit-check",
        help="pre-commit check: revalidate the recorded grant against the active index",
    )
    cc.set_defaults(fn=cmd_commit_check)
    lk = sub.add_parser("lock", help="run a heavy command under the machine-wide lock")
    lk.add_argument("--wait", type=int, default=1800)
    lk.add_argument("command", nargs=argparse.REMAINDER)
    lk.set_defaults(fn=cmd_lock)
    mc = sub.add_parser("mcp", help="serve project state to MCP clients (stdio)")
    mc.add_argument("action", choices=["serve", "config"], nargs="?", default="config")
    mc.add_argument("--allow-verify", action="store_true", help="expose the gate runner too")
    mc.set_defaults(fn=cmd_mcp)
    am = sub.add_parser("a2a-mcp", help="reach A2A agents from an MCP client (stdio)")
    am.add_argument("action", choices=["serve", "config"], nargs="?", default="config")
    am.set_defaults(fn=cmd_a2a_mcp)

    a2 = sub.add_parser("a2a", help="serve the board as A2A tasks (loopback HTTP)")
    a2.add_argument("action", choices=["serve", "info"], nargs="?", default="info")
    a2.add_argument("--port", type=int, default=8731)
    a2.set_defaults(fn=cmd_a2a)
    n = sub.add_parser("notices", help="alerts this project raised; with an id, what it was raised on")
    n.add_argument("ident", nargs="?", help="a notice id: print its evidence")
    n.add_argument("-n", type=int, default=12)
    n.add_argument("--all", action="store_true", help="include rate-limited ones")
    n.set_defaults(fn=cmd_notices)
    pr = sub.add_parser("prune", help="trim accumulated records and logs")
    pr.add_argument("--days", type=float, default=7)
    pr.add_argument("--keep-kb", type=int, default=64, help="log tail to keep")
    pr.add_argument("--evidence", action="store_true", help="also prune verifications and plans")
    pr.add_argument("--review-days", type=float, default=None,
                    help="age at which review artefacts nothing rests on move to the archive "
                         "(default: review.prune_after_days)")
    pr.add_argument("--yes", action="store_true", help="apply (default is a dry run)")
    pr.set_defaults(fn=cmd_prune)
    h = sub.add_parser("hold", help="stop this project's agents and keep them stopped")
    h.add_argument("action", choices=["hold", "release", "status"], nargs="?", default="hold")
    h.add_argument("reason", nargs="?")
    h.add_argument("--by", default="architect")
    h.add_argument("--note", help="on release: what changed while the agent was stopped")
    h.add_argument("--grace", type=float, default=10, help="seconds before SIGKILL")
    h.set_defaults(fn=cmd_hold)
    em = sub.add_parser("email", help="the red alarm channel: e-mail via formsubmit.co or an SMTP server")
    em.add_argument("action", choices=["setup", "test", "status"], nargs="?", default="status")
    em.add_argument("--provider", choices=["formsubmit", "smtp"])
    em.add_argument("--token", help="formsubmit: the alias it hands back after verification")
    em.add_argument("--to")
    em.add_argument("--host", help="smtp: the server")
    em.add_argument("--port", type=int, help="smtp: 587 for starttls, 465 for implicit")
    em.add_argument("--user", help="smtp: the account")
    em.add_argument("--password-env", help="smtp: the environment variable holding the password")
    em.add_argument("--from", dest="sender", help="smtp: the sender, if not --to")
    em.add_argument("--tls", choices=["starttls", "implicit", "none"])
    em.set_defaults(fn=cmd_email)
    al = sub.add_parser("alarms", help="live alarm episodes (yellow/orange/red); test rings the channels")
    al.add_argument("action", choices=["list", "test", "snooze", "unsnooze"], nargs="?", default="list")
    al.add_argument("key", nargs="?", help="snooze/unsnooze: the alarm key as ao alarms lists it")
    al.add_argument("--level", choices=["yellow", "orange", "red"])
    al.add_argument("--until", help="snooze: YYYY-MM-DD; the alarm rings again from that date")
    al.add_argument("--why", help="snooze: why nobody can act on it before then")
    al.add_argument("--by", default="human", help="snooze: who decided it")
    al.set_defaults(fn=cmd_alarms)
    co = sub.add_parser("cost", help="what the coordination spends: implementer turns by class (product/analysis/ceremony/coordination)")
    co.add_argument("--since", help="window such as 24h or 7d (default: whole transcript)")
    co.add_argument("--features", action="store_true", help="what each feature switch spent, measured")
    co.set_defaults(fn=cmd_cost)
    cf = sub.add_parser("config", help="what a person can set: list, get, set, unset")
    cf.add_argument("action", choices=["list", "get", "set", "unset"], nargs="?", default="list")
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")
    cf.add_argument("--machine", action="store_true", help="the machine's settings, not the project's")
    cf.set_defaults(fn=cmd_config)
    ft = sub.add_parser("features", help="the switches and what each costs; all off = deterministic ao")
    ft.add_argument("action", choices=["list", "on", "off"], nargs="?", default="list")
    ft.add_argument("key", nargs="?")
    ft.set_defaults(fn=cmd_features)
    wv = sub.add_parser("waive", help="a person bypasses a gate for a slice, on the record")
    wv.add_argument("gate", choices=["review", "inventory", "gates"])
    wv.add_argument("--slice")
    wv.add_argument("--why")
    wv.add_argument("--by", help="the person who authorises it; required")
    wv.add_argument("--hours", type=float, default=None,
                    help="how long it may stand in for the gate (settings waivers.default_hours "
                         "and waivers.max_hours; 24 and 168 unless changed)")
    wv.set_defaults(fn=cmd_waive)
    cu = sub.add_parser("catchup", help="replay what could not run: waived reviews, deferred wakes and nudges",
                        description="Replay what could not run: waived reviews, deferred wakes and nudges. A run "
                                    "exits 3 when the reviews it started decided nothing, 1 when something could "
                                    "not be written or read, 2 when it is refused, and otherwise 0, whether it made "
                                    "progress or had nothing to do.")
    cu.add_argument("--boundary")
    cu.add_argument("--plan", action="store_true",
                    help="list each open review waiver, its range, commits and changed lines, whether it closes "
                         "by proof, and the totals; review nothing and write nothing")
    cu.add_argument("--limit", type=int, help="start at most this many reviews in this run; what needs no review is "
                                              "still done")
    cu.add_argument("--slice", help="only the waivers for this slice")
    cu.add_argument("--author-family", dest="author_family",
                    help="a person names the model family that wrote the waived ranges, for those whose grant "
                         "recorded none; a reviewer of a family named either way is refused. Needs --by")
    cu.add_argument("--move-only", dest="move_only",
                    help="a person states that these waived slices, separated by commas, only moved code; each "
                         "closes only if the move proof holds on what it landed, never on the statement. Needs --by")
    cu.add_argument("--by", help="with --author-family or --move-only: the person who states it, recorded with "
                                 "what it decides")
    cu.set_defaults(fn=cmd_catchup)
    pg = sub.add_parser("pings", help="dead man's switch: external pings that alarm when they stop")
    pg.add_argument("action", choices=["status", "setup", "test"], nargs="?", default="status")
    pg.add_argument("--url")
    pg.add_argument("--all", action="store_true")
    pg.set_defaults(fn=cmd_pings)
    hk = sub.add_parser(
        "hooks",
        help="inspect/install static AO hook intent at Git's effective path; shared/external/global mutation needs explicit authorization",
    )
    hk.add_argument("action", choices=["install", "uninstall", "status"], nargs="?", default="status")
    hk.add_argument(
        "--allow-shared-hooks", action="store_true",
        help="authorize the whole mutation set when Git routes hooks through shared, external, or global/system configuration",
    )
    hk.set_defaults(fn=cmd_hooks)
    ps_ = sub.add_parser("push", help="allow a human push for N minutes; check is what the hook runs")
    ps_.add_argument("action", choices=["status", "allow", "check"], nargs="?", default="status")
    ps_.add_argument("--minutes", type=int, default=30)
    ps_.set_defaults(fn=cmd_push)
    rm = sub.add_parser(
        "remove",
        help="take ao off this repository after a full hook-topology preflight; protected hooks stay untouched",
    )
    rm.add_argument("--yes", action="store_true")
    rm.add_argument(
        "--allow-shared-hooks", action="store_true",
        help="authorize the whole hook-removal set when Git routes hooks through shared, external, or global/system configuration",
    )
    rm.set_defaults(fn=cmd_remove)
    fo = sub.add_parser("fanout", help="may a fan-out of N sub-agents start now; record what one cost")
    fo.add_argument("action", choices=["ok", "record", "history"], nargs="?", default="ok")
    fo.add_argument("--agents", type=int)
    fo.add_argument("--roots", type=int, help="pipeline: number of first-stage agents")
    fo.add_argument("--per-root", type=int, dest="per_root", help="pipeline: at most this many second-stage agents per root")
    fo.add_argument("--per-agent-tokens", type=int, dest="per_agent_tokens")
    fo.add_argument("--provider", help="keyflip provider; default: the architect's")
    fo.add_argument("--done", type=int)
    fo.add_argument("--errors", type=int)
    fo.add_argument("--tokens", type=int)
    fo.add_argument("--note")
    fo.add_argument("--limit", type=int, default=20)
    fo.add_argument("--json", action="store_true")
    fo.set_defaults(fn=cmd_fanout)
    w = sub.add_parser("writers", help="live turns in this tree (not processes); orphans set aside")
    w.add_argument("--clean", action="store_true", help="stop orphaned processes left by ended turns")
    w.add_argument("--json", action="store_true")
    w.set_defaults(fn=cmd_writers)
    sr = sub.add_parser("source", help="external work queues feeding the board")
    sr.add_argument("action", choices=["list", "status", "import"], nargs="?", default="status")
    sr.set_defaults(fn=cmd_source)
    sub.add_parser("projects", help="workspaces with a local agent session").set_defaults(fn=cmd_projects)
    adp = sub.add_parser("adapters", help="adapter registry: list, validate a candidate, run conformance")
    adp.add_argument("action", nargs="?", choices=["list", "validate", "conform"], default="list")
    adp.add_argument("target", nargs="?", help="an adapter id or a JSON file")
    adp.set_defaults(fn=cmd_adapters)
    dr = sub.add_parser("doctor", help="check this workspace's wiring")
    dr.add_argument("--check", action="store_true",
                    help="quiet: one line per problem, exit 1 if any; pages nobody without --notify")
    dr.add_argument("--notify", action="store_true",
                    help="with --check, for the scheduled job only: page red findings, record advisories")
    dr.add_argument("--consistency", action="store_true",
                    help="check the board, the ledgers, the review artefacts and git against each other")
    dr.add_argument("--repair", action="store_true",
                    help="with --consistency: fix only mechanical disagreements, on the record")
    dr.set_defaults(fn=cmd_doctor)
    sk = sub.add_parser("skill", help="the playbook, rendered for the agents this repository uses")
    sk.add_argument("action", choices=["install", "show"], nargs="?", default="install")
    sk.add_argument("--agent", choices=agents, default="auto")
    sk.set_defaults(fn=cmd_skill)
    return p


def main():
    p = build_parser()
    args = p.parse_args()
    if not getattr(args, "fn", None):
        p.print_help()
        return 0
    cfg = A.load_config(A.find_root(args.root))
    return args.fn(cfg, args) or 0


if __name__ == "__main__":
    sys.exit(main())
