#!/usr/bin/env python3
"""ao-watchdog — restart a stalled implementer, cheaply.

Turn-based agents stop when a turn ends, mid-slice or not. Something has to
notice and nudge. Detection here is free: a file mtime and a few `git` calls.
Only the nudge itself costs anything, and it costs exactly what the work would
have cost when a human noticed twenty minutes later.

Guards, in order — the point is to spend nothing when spending would not help:

  -1. Has a human taken the tree?           (.ao/hold — overrides everything)
  0. Is an agent already WORKING here?      (cwd match AND a moving transcript; a
                                             process alive but long silent is hung,
                                             and is reaped rather than waited on)
  1. Is the session actually idle?          (mtime)
  2. Is there open work to continue?        (mailbox / dirty tree / NEEDS_CHANGES)
  2b. Did the queue run dry rather than end? (wake the architect, not the implementer)
  3. Is the slice over its round budget?    (if so: notify a human, never nudge)
  4. Is there provider quota left?          (keyflip, if installed)
  4b. Is the provider itself degraded?      (5xx at the end of our own nudge log)
  4c. Are reports sitting unhandled?        (wake the architect — its own pid guard)
  5. Did the last nudge achieve anything?   (backoff, then hand over to a human)

Run it from cron/launchd every couple of minutes, or once by hand.

    ao-watchdog --root ~/work/project            # act
    ao-watchdog --root ~/work/project --dry-run  # decide, change nothing
"""
import argparse
import json
import os
import re
import subprocess
import shutil
import signal
import sys
import time

from . import lib as A

STATE_DIR = os.path.join(A.HOME, ".ao")
MAX_ATTEMPTS = 3

# What an architect turn is for. Deliberately narrow: it refills and admits, it
# does not implement. Admission is the step that turns "someone filed this" into
# "an agent may work on this unattended", and it is the only step that may not
# be delegated to whoever will do the work.
REFILL_PROMPT = (
    "Kuyruk boşaldı. .ao/sources.json'daki kaynaklardan yeni işleri çek, "
    "normalize edip .ao/inbox/<source-id>.json'a yaz, sonra `ao source import` çalıştır. "
    "Her madde için kabul sınırı yaz: dilim boyutundaysa acceptance alanını doldur; "
    "proje boyutundaysa acceptance'ı boş bırak ve shape alanına sebebini yaz — "
    "kabul sınırı olmayan madde inbox'ta kalır, kuyruğa girmez. "
    "Uygulama YAPMA; yalnız çek, sınıflandır, kabul et.")


def child_path():
    """A PATH the nudged agent can actually build with.

    Two different failures hide here. launchd gives us a minimal PATH, so we
    cannot find the agent CLI. And the agent we spawn inherits whatever PATH we
    hand it — if that lacks the toolchain (node, npm, a version-manager shim),
    the agent starts its turn and then cannot run its own gates. Resolve both by
    building one PATH that covers the user's real toolchain, including any
    version-manager shim directory the current interpreter can see.
    """
    parts = [os.environ.get("PATH", "")]
    for d in ("~/.local/bin", "~/bin", "/usr/local/bin", "/opt/homebrew/bin",
              "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        parts.append(os.path.expanduser(d))
    # fnm / nvm / asdf style shims: whichever one currently owns `node`
    node = shutil.which("node")
    if node:
        parts.append(os.path.dirname(node))
    for base in ("~/.local/state/fnm_multishells", "~/.nvm/versions/node", "~/.asdf/shims"):
        b = os.path.expanduser(base)
        if os.path.isdir(b):
            try:
                for entry in sorted(os.listdir(b), reverse=True)[:3]:
                    cand = os.path.join(b, entry, "bin")
                    if os.path.isdir(cand):
                        parts.append(cand)
                    elif os.path.isdir(os.path.join(b, entry)):
                        parts.append(os.path.join(b, entry))
            except OSError:
                pass
    seen, out = set(), []
    for chunk in ":".join(parts).split(":"):
        if chunk and chunk not in seen:
            seen.add(chunk)
            out.append(chunk)
    return ":".join(out)


# Who each alert is actually for. Severity was the wrong axis: an anomaly is
# urgent *and* entirely the architect's business, and ringing a human about it
# teaches them to ignore the channel before the one alert that needs them
# arrives. A human hears only what a human can fix.
HUMAN_AUDIENCE = ("out of quota", "agent stuck", "nudge failed", "watchdog",
                  "turns piling up", "needs you", "provider degraded")


def for_human(title):
    return any(k in title.lower() for k in HUMAN_AUDIENCE)


def notify(title, msg, root=None, key=None, window=1800, audience="human"):
    """Raise an alert at most once per window, and always record that we did.

    The watchdog runs every two minutes. A guard that notifies on each run turns
    a single ongoing condition into thirty alerts an hour, and a human who learns
    to swipe those away has effectively turned the alerting off — which is worse
    than not alerting, because everyone still believes it works.

    Recording happens either way. A suppressed alert is evidence too: a long run
    of them says the condition has held for a long time.
    """
    key = key or title
    # Architect-audience alerts are recorded and delivered through the mailbox and
    # the wake; they do not ring a phone.
    if audience == "architect" or not for_human(title):
        if root:
            A.record_notice(root, title, msg, sent=False, key=key)
            try:
                from . import telegram
                if not A.notice_recently_sent(root, "tg:" + key, window):
                    if telegram.send(f"*{title}*\n{msg}", root):
                        A.record_notice(root, title, msg, sent=True, key="tg:" + key)
            except Exception:
                pass
        return False
    if root and A.notice_recently_sent(root, key, window):
        A.record_notice(root, title, msg, sent=False, key=key)
        return False
    safe = msg.replace('"', "'")[:200]
    subprocess.run(["osascript", "-e",
                    f'display notification "{safe}" with title "{title}"'],
                   capture_output=True)
    try:
        from . import telegram
        telegram.send(f"*{title}*\n{msg}", root)
    except Exception:
        pass                                # a phone being unreachable is not a failure
    if root:
        A.record_notice(root, title, msg, sent=True, key=key)
    return True


def state_path(root):
    key = os.path.basename(root.rstrip("/")) or "root"
    return os.path.join(STATE_DIR, f"watchdog-{key}.json")


def load_state(root):
    try:
        return json.load(open(state_path(root)))
    except Exception:
        return {"attempts": 0, "last_nudge": 0, "last_size": 0}


def save_state(root, st):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(st, open(state_path(root), "w"), indent=2)


def arch_alive(st):
    """Is an architect turn we started still running?

    Separate from the implementer's pid, and the separation matters: gating the
    architect on `child_alive` meant it was never woken while the implementer was
    working, which is exactly when anomalies happen. Two actors, two guards --
    the same lesson as one threshold doing two jobs, in a different disguise.
    """
    pid = st.get("arch_pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def child_alive(st):
    """Is the turn we last started still running?

    A transcript mtime cannot answer this. A turn retrying a provider 5xx sits in
    backoff writing nothing, which looks exactly like a turn that ended — and
    nudging then starts a SECOND turn on the same session id. Two turns editing
    one tree produce a half-finished refactor: one renames a field, the other
    still calls the old name, and the build breaks in a way neither writer can
    attribute to itself. Ask the OS instead of inferring from a file.
    """
    pid = st.get("child_pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)          # signal 0 asks "does this exist?" and changes nothing
    except OSError:
        return False
    return True


def provider_degraded(root, window=900):
    """A provider-side failure in the *most recent* nudge, recent enough to hold.

    An exhausted quota and an outage both silence the agent, but only one of them
    is fixed by nudging. Naming it here keeps "the model is down" from being
    reported — and retried — as "the agent is stuck".

    Scope it to the last nudge attempt, not the whole file. The log is
    append-only, so an hour-old outage stays in it forever, and a check that
    merely greps the tail keeps firing long after the provider recovered — which
    blocks every later nudge and costs exactly the time this guard was added to
    save. Read only what came after the last `=== <time> nudge ===` marker, and
    only if that marker itself is inside the window.
    """
    key = os.path.basename(root.rstrip("/")) or "root"
    p = os.path.join(STATE_DIR, f"nudge-{key}.log")
    if not os.path.exists(p):
        return None
    try:
        size = os.path.getsize(p)
        with open(p, errors="replace") as fh:
            fh.seek(max(0, size - 400_000))
            tail = fh.read()
    except OSError:
        return None
    marks = list(re.finditer(r"=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) nudge ===", tail))
    if not marks:
        return None
    last = marks[-1]
    try:
        at = time.mktime(time.strptime(last.group(1), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None
    if time.time() - at > window:
        return None                       # the last attempt is old news
    # Only the *end* of the segment counts. A 5xx the turn recovered from is
    # history: this log had one at line 130 of 242, with a hundred lines of real
    # work after it. Blocking on that would stand the watchdog down for fifteen
    # minutes because of an error the agent had already handled.
    segment = tail[last.end():].strip().split("\n")[-30:]
    end = "\n".join(segment)
    for marker in ("MODEL_TEMPORARILY_UNAVAILABLE", "ThrottlingException",
                   "ServiceUnavailableException", "InternalServerException",
                   "experiencing a high volume of traffic"):
        if marker in end:
            return marker
    return None


def escalate(root, cfg, adapter, age, args, st):
    """Hand every judgement call to the architect, once per condition per hour.

    The watchdog is good at mechanical questions and bad at everything else. Its
    worst failures came from answering the second kind anyway: a provider outage
    read as a stuck agent, a hung process read as a live writer, a finished slice
    read as work in progress. Each was defensible from the one signal it had and
    wrong given the others.

    So it reports instead. Facts go to the mailbox where they persist; the
    architect is woken only if one is configured, because a report nobody reads is
    not an escalation.
    """
    # The architect we spawned resumes with this repo as its cwd, so it reads as a
    # second turn to the process check. We know its pid; exclude it and our own
    # rather than making the detector guess.
    found = A.anomalies(root, cfg, adapter, age, args.idle_minutes * 60,
                        exclude_pids={st.get("arch_pid"), os.getpid()})

    # A second turn is a hazard only if it persists. A real concurrent writer runs
    # for minutes; a gate helper, an `ao` call or a one-shot process blinks in and
    # out and trips a single-scan check for one cycle. Require the same extra roots
    # on two consecutive scans before flagging.
    prev_roots = set(st.get("last_roots") or [])
    seen_several = False
    kept = []
    for a in found:
        if a["kind"] == "several-turns-active":
            seen_several = True
            roots = set(a.get("roots") or [])
            st["last_roots"] = list(roots)
            if len(roots) < 2 or not (roots & prev_roots):
                print(f"several turns seen once ({sorted(roots)}); waiting for a "
                      f"second scan before flagging")
                continue
        kept.append(a)
    if not seen_several:
        st["last_roots"] = []
    save_state(root, st)
    found = kept
    if not found:
        return False

    woke = False
    for a in found:
        key = f"anomaly:{a['kind']}"
        window = 600 if a["kind"] == "decision-requested" else 3600
        if a["kind"] == "report-waiting" and not open_work(cfg, root):
            continue                     # nothing is stuck; it can wait for a human
        if A.notice_recently_sent(root, key, window):
            A.record_notice(root, "Voltrai: anomaly", a["kind"], sent=False, key=key)
            continue
        name = A.write_report(root, cfg, a["kind"], a["facts"])
        # Say what actually happened. "reported to the architect" was untrue
        # whenever no architect was configured or startable, and a notification
        # that overstates its own effect is how a gap stays invisible.
        reach = "architect will be woken" if (cfg.get("architect") or {}).get("argv") \
            else "written to the mailbox; no architect configured"
        notify("Voltrai: anomaly", f"{a['kind']} — {reach}", root,
               key=key, window=window, audience="architect")
        print(f"anomaly {a['kind']}: {'reported as ' + name if name else 'already reported'}")
        woke = True
    arch = cfg.get("architect") or {}

    # Wake on durable state, not on the moment of detection. The notification
    # throttle exists so a human is not rung every two minutes; using it to gate
    # the wake as well meant the architect was only ever started on an anomaly's
    # *first* sighting, and a single suppressed cycle lost it for good. What
    # actually matters is whether a report is still sitting unprocessed — the
    # mailbox is the state, the notification is only a bell.
    pending = [m for m in A.mailbox(root, cfg["mailbox"])
               if "-to-fable-" in m or "-to-architect-" in m]
    # Closing the loop out loud: an alert that says a thing was detected, with no
    # later word on whether anything came of it, is what makes someone check by
    # hand — which is the work the alert was supposed to save.
    if st.get("arch_pending") and not pending:
        try:
            from . import telegram
            telegram.send(f"✅ *Mimar bitirdi* — {st['arch_pending']} rapor kapandı, "
                          f"kuyruk boş", root)
        except Exception:
            pass
        st["arch_pending"] = 0
        save_state(root, st)
    elif pending:
        st["arch_pending"] = len(pending)
    last_wake = st.get("last_arch_wake", 0)
    stale = [m for m in pending
             if os.path.getmtime(os.path.join(root, cfg["mailbox"], m)) > last_wake]
    woke = bool(stale)
    if woke and time.time() - last_wake < 900:
        print(f"{len(stale)} unhandled report(s), but the architect was woken "
              f"{int((time.time() - last_wake) / 60)}m ago")
        woke = False

    # Waking spends a turn on the same provider the implementer uses, so a report
    # that could have waited must not burn the window the implementer needs.
    if woke and arch.get("argv") and not quota_ok(adapter):
        print("reports pending, but no quota headroom to wake the architect")
        woke = False

    # Wake into absence, never alongside. A live architect does not need a copy of
    # itself: the copy inherits the conversation in progress and continues that
    # rather than the triage it was started for.
    if woke and A.architect_present(arch.get("cwd") or root):
        print("reports pending, but the architect is already at the keyboard")
        woke = False
    if woke and arch.get("argv") and not args.dry_run and not arch_alive(st):
        prompt = (
            "Sen bu deponun mimarısın ve watchdog tarafından uyandırıldın. "
            "`agent-mail/` içindeki `*-watchdog-to-fable-ANOMALY-*.md` ve "
            "`*-kiro-to-fable-*.md` mesajlarını oku. Watchdog'unkiler olgudur, yorum "
            "değil — kendi kararını sen ver. Durumu `ao status`, `ao board`, "
            "`ao doctor` ile doğrula; ölçmeden sonuç çıkarma.\n\n"
            "Gerçekten müdahale gerekiyorsa yap: `agent-mail/`'e uygulayıcı için "
            "karar mesajı yaz, gerekiyorsa `.ao/board.md`'yi güncelle. Acil bir şeyse "
            "mesaja `## ACİL` başlığı koy — o zaman uygulayıcıya `ao lock`, `ao verify` "
            "ve `ao commit-ok` üzerinden ulaşır.\n\n"
            "Sonra işlediğin mesajı sil; teslim onayı budur. Normal bir durumsa "
            "yalnız sil ve bir şey yapma.\n\n"
            "Yapmayacakların: push, PR, force-push, epic kutusu işaretleme, mimari "
            "sözleşme değiştirme. Bunlar insana aittir. Emin değilsen dokunma ve "
            "kullanıcıya bırak.")
        # Resolve the session at wake time. A resumed architect carries the whole
        # history -- what was decided and why -- where a fresh one knows only what
        # is on disk. Claude Code forks a copy rather than double-writing when the
        # session is already running, so resuming cannot repeat the two-writer
        # incident. Pinning an id in config would go stale the moment the human
        # opens a new conversation, and a watchdog waking a dead session fails
        # silently, which is the worst shape of failure.
        sess = arch.get("session")
        if sess in (None, "auto"):
            sess = (A.discover_architect(arch.get("cwd") or root) or {}).get("session")
        if "{session}" in " ".join(arch["argv"]) and not sess:
            print("architect session not resolvable; reported only")
            return woke
        argv = [x.replace("{prompt}", prompt) .replace("{session}", sess or "")
                for x in arch["argv"]]
        search = child_path()
        resolved = shutil.which(argv[0], path=search)
        if resolved:
            argv[0] = resolved
            key = os.path.basename(root.rstrip("/")) or "root"
            log_path = os.path.join(STATE_DIR, f"escalate-{key}.log")
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(log_path, "a") as log:
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} escalate ===\n")
                log.flush()
                # stdin must be closed, not inherited. A detached child holding a
                # pipe or tty it will never read can wait forever for an EOF that
                # never comes: alive, silent, producing nothing. That is the exact
                # shape of the first architect wake — over a minute, no output —
                # and very likely of the fifteen agent processes this project
                # found accumulated in one repository.
                proc = subprocess.Popen(argv, cwd=root, env=dict(os.environ, PATH=search),
                                        stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            st["arch_pid"] = proc.pid
            st["last_arch_wake"] = time.time()
            save_state(root, st)
            try:
                from . import telegram
                telegram.send(f"🤖 *Mimar uyandırıldı* — {len(stale)} rapor "
                              f"işleniyor (pid {proc.pid})", root)
            except Exception:
                pass
            print(f"woke the architect (pid {proc.pid}) to judge it")
    return woke


def open_work(cfg, root):
    """Is there something to continue? Cheap signals only."""
    reasons = []
    if A.mailbox(root, cfg["mailbox"]):
        reasons.append("unread mail")
    if A.sh("git status --porcelain", cwd=root):
        reasons.append("uncommitted changes")
    revs = A.reviews(root, cfg["reviews"], limit=1)
    if revs and "APPROVED" not in revs[0][1].upper():
        reasons.append("open review findings")
    return reasons


def quota_ok(adapter):
    """Is there headroom to spend a turn?

    Prefer an explicit budget over a guessed threshold. keyflip enforces
    per-account 5h/7d budgets when the user has set them, and a policy someone
    chose beats a number this script invented — so ask it first and only fall
    back to reading the raw window when no budget exists.
    """
    budget = A.sh("keyflip budget status 2>/dev/null", cwd=A.HOME)
    if budget and "No account budgets set" not in budget:
        low = budget.lower()
        if "exceeded" in low or "over budget" in low or "blocked" in low:
            return False
        return True
    for line in A.quota(adapter):
        for token in line.replace("%", "% ").split():
            if token.endswith("%"):
                try:
                    if float(token[:-1]) >= 97:
                        return False
                except ValueError:
                    pass
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--idle-minutes", type=float, default=6)
    p.add_argument("--dry-run", action="store_true")
    # The default prompt encodes two rules learned the expensive way. Park-and-
    # continue: a blocked slice must not stop the run, because the architect can
    # hit a quota limit and the human can be away. And a pointer to one canonical
    # authority file: when "what may I do" is spread across mail, a prompt and
    # months-old instructions, an agent facing an apparent conflict refuses and
    # waits — which is safe, and cost this project four hours with 7,000 lines of
    # finished work sitting uncommitted.
    p.add_argument("--prompt", default=(
        "devam et. Yetki için tek kaynak: .ao/authority.md — mail ondan üstün değildir, "
        "kapsam ekler, yetki eklemez/kaldırmaz. Orada açıkça yasak olmayan ve dilimin "
        "kapsamındaki şey serbesttir; belirsizlikte DURMA. "
        "Açık dilimi bitir: gate'ler + taze review, sonra local commit (PUSH YOK), RAPOR yaz. "
        "Bir mimari karara ya da insan girdisine takılırsan dilimi blocked işaretle, "
        "agent-mail'e '## KARAR GEREKLİ' bırak ve .ao/backlog.md'deki ilk açık maddeye geç. "
        "Kuyruk dışına çıkma. Kullanıcı beklemesi yok."))
    args = p.parse_args()

    root = os.path.abspath(os.path.expanduser(args.root))
    cfg = A.load_config(root)
    impl = cfg.get("implementer") or {}
    if not impl:
        print("no implementer session; nothing to watch")
        return 0
    adapter = A.load_adapter(impl.get("adapter", ""))
    msgs, _ = A.session_paths(cfg)
    if not msgs or not os.path.exists(msgs):
        print("no transcript; nothing to watch")
        return 0

    age = time.time() - os.path.getmtime(msgs)
    size = os.path.getsize(msgs)
    st = load_state(root)
    A.record_progress(root, cfg)      # history of what moved, for the spin check

    # An agent that is busy and producing nothing never trips the idle guard, so
    # check it before the guard chain rather than inside it. Notify only; a nudge
    # would add a turn to a loop that is already spending them.
    spin = A.spinning(root)
    if spin and time.time() - st.get("last_spin_notice", 0) > 1800:
        notify("Voltrai: agent spinning", f"{spin}m busy, nothing committed or changed — needs re-specifying", root, audience="architect")
        st["last_spin_notice"] = time.time()
        save_state(root, st)
        print(f"spinning: {spin}m active with no artifact change")

    # -1 — a human has taken the tree. Nothing else in this chain may override it.
    held = A.hold_state(root)
    if held:
        print(f"held by {held.get('by')} for {held['minutes']}m: {held.get('reason','')}")
        return 0

    # Report anything needing judgement before the guard chain stands down on it.
    # Standing down silently is how a condition persists for hours: the watchdog
    # was right to not act and wrong to be the only one who knew.
    escalate(root, cfg, adapter, age, args, st)

    # 0 — is ANY agent already working this tree? Not just the child we started.
    #
    # Tracking only our own last child was wrong, and expensively so: every nudge
    # spawns a detached process, nothing reaps them, and this project accumulated
    # fifteen live agent processes in one repository with four still burning CPU.
    # Each was invisible to a guard that remembered a single pid. Ask the OS which
    # processes have this repo as their cwd, and treat any of them as a writer.
    running = A.agent_pids(root, adapter)
    if running:
        # A process being alive is not a turn being in flight. An agent can finish
        # its turn and never exit, and the first version of this guard treated that
        # hung process as a writer — so one zombie blocked every future nudge for
        # seven hours while the slice it had already finished sat uncommitted.
        # Fixing an accumulation bug had quietly introduced a deadlock.
        #
        # Tell them apart by the transcript: a live turn writes to it. Silence far
        # past the idle threshold, with a process still up, is hung, not busy.
        turns = A.process_trees(running)
        if age < args.idle_minutes * 60 * 3:
            print(f"{len(turns)} turn(s) already in this tree "
                  f"(roots {turns}, {len(running)} processes); not starting another")
            if len(turns) > 2:
                notify("Voltrai: turns piling up",
                       f"{len(turns)} concurrent turns — `ao hold` to clear", root)
            return 0
        # Reaping is an action, so it keeps the conservative bound even though
        # reporting no longer does.
        print(f"{len(running)} process(es) alive but silent {int(age / 60)}m; reaping")
        notify("Voltrai: reaping hung turn",
               f"{len(running)} process(es) silent {int(age / 60)}m — cleaning up", root, audience="architect")
        if args.dry_run:
            print("DRY RUN: would reap", running)
            return 0
        for pid in running:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        for _ in range(20):
            if not A.agent_pids(root, adapter):
                break
            time.sleep(0.5)
        for pid in A.agent_pids(root, adapter):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    # 1 — still working
    head = A.sh("git rev-parse --short HEAD", cwd=root)
    if st.get("attempts") and head and head != st.get("last_head"):
        # Something landed since the last nudge, so the nudges were working. The
        # backoff counted attempts rather than failures, and after three of them
        # it stood down permanently on a project that was committing fine.
        st.update(attempts=0, last_head=head)
        save_state(root, st)
    if age < args.idle_minutes * 60:
        if st.get("attempts"):
            st.update(attempts=0, last_size=size)   # it moved; forget the backoff
            save_state(root, st)
        print(f"working ({int(age)}s since last write)")
        return 0

    # 2 — nothing to continue. Before standing down, ask why: an empty board can
    # mean "all done" or "the queue ran dry". Only the second one is actionable,
    # and it needs the *architect*, not the implementer — refilling means pulling
    # from a tracker and deciding what may be worked, and an implementer that
    # chooses its own scope is the one thing this tool exists to prevent.
    reasons = open_work(cfg, root)
    if not reasons:
        sc = A.sources(root)
        arch = cfg.get("architect") or {}
        depth = len(A.board(root)["queued"])
        if sc and arch.get("argv") and depth < sc.get("refill_below", 3):
            if args.dry_run:
                print(f"queue low ({depth}); would wake the architect to refill")
                return 0
            if child_alive(st):
                print("queue low, but a turn is still running")
                return 0
            # Resolve the session at wake time. A resumed architect carries the whole
            # history -- what was decided and why -- where a fresh one knows only what
            # is on disk. Claude Code forks a copy rather than double-writing when the
            # session is already running, so resuming cannot repeat the two-writer
            # incident. Pinning an id in config would go stale the moment the human
            # opens a new conversation, and a watchdog waking a dead session fails
            # silently, which is the worst shape of failure.
            sess = arch.get("session")
            if sess in (None, "auto"):
                sess = (A.discover_architect(arch.get("cwd") or root) or {}).get("session")
            if "{session}" in " ".join(arch["argv"]) and not sess:
                print("architect session not resolvable; reported only")
                return 0
            argv = [x.replace("{prompt}", arch.get("prompt", REFILL_PROMPT)) .replace("{session}", sess or "")
                    for x in arch["argv"]]
            search = child_path()
            resolved = shutil.which(argv[0], path=search)
            if not resolved:
                print(f"architect command {argv[0]} not on PATH")
                return 0
            argv[0] = resolved
            key = os.path.basename(root.rstrip("/")) or "root"
            log_path = os.path.join(STATE_DIR, f"refill-{key}.log")
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(log_path, "a") as log:
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} refill ===\n")
                log.flush()
                proc = subprocess.Popen(argv, cwd=root, env=dict(os.environ, PATH=search),
                                        stdin=subprocess.DEVNULL,   # see escalate()
                                        stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            st.update(arch_pid=proc.pid, last_refill=time.time())
            save_state(root, st)
            print(f"queue low ({depth} < {sc.get('refill_below', 3)}); woke the architect")
            return 0
        print("idle, but no open work — leaving it alone")
        return 0

    # 3 — over the round budget: a nudge would only buy another identical round.
    # Unless the architect has already intervened: a coordination message newer
    # than the newest review means the slice was re-specified, and the budget
    # applies to the old specification, not the new one.
    rn = A.rounds(root, cfg["reviews"])
    budget = cfg.get("round_budget", 5)
    intervened = False
    revs_all = A.reviews(root, cfg["reviews"], limit=1)
    if revs_all:
        rev_mt = os.path.getmtime(os.path.join(root, cfg["reviews"], revs_all[0][0]))
        for m in A.mailbox(root, cfg["mailbox"]):
            if os.path.getmtime(os.path.join(root, cfg["mailbox"], m)) > rev_mt:
                intervened = True
                break
    if rn > budget and not intervened:
        notify("Voltrai: over budget", f"round {rn}/{budget} — re-specify, split or change actor", root, audience="architect")
        print(f"over budget ({rn}/{budget}); notified instead of nudging")
        return 0
    if rn > budget and intervened:
        print(f"over budget ({rn}/{budget}) but re-specified since the last review; proceeding")

    # 4 — no quota
    if not quota_ok(adapter):
        notify("Voltrai: out of quota", "provider window exhausted; not nudging", root)
        print("provider out of headroom; not nudging")
        return 0

    # 4b — the provider stopped, not the agent. Nudging an outage buys nothing
    # and, while the stalled turn is still retrying, costs a second writer.
    degraded = provider_degraded(root)
    if degraded:
        notify("Voltrai: provider degraded", f"{degraded} — waiting, not nudging", root)
        print(f"provider degraded ({degraded}); waiting rather than nudging")
        return 0

    # 5 — the previous nudge changed nothing
    if st.get("attempts", 0) >= MAX_ATTEMPTS:
        notify("Voltrai: agent stuck", f"{MAX_ATTEMPTS} nudges, no progress — needs a human", root)
        print("backoff exhausted; notified a human")
        return 0
    if st.get("last_nudge") and size == st.get("last_size"):
        wait = args.idle_minutes * 60 * (2 ** st["attempts"])
        if time.time() - st["last_nudge"] < wait:
            print(f"backing off ({st['attempts']} attempts, waiting {int(wait)}s)")
            return 0

    argv = [x.replace("{session}", impl["session"]).replace("{prompt}", args.prompt)
            for x in (adapter.get("resume", {}).get("argv") or [])]
    if not argv:
        print("adapter has no resume command")
        return 1

    # Resolve the CLI and hand the child a usable PATH — see child_path().
    search = child_path()
    resolved = shutil.which(argv[0], path=search)
    if not resolved:
        notify("Voltrai: watchdog", f"{argv[0]} not on PATH; cannot nudge", root)
        print(f"{argv[0]} not found on PATH ({search})")
        return 1
    argv[0] = resolved
    if "--trust-all-tools" not in argv and "trust_all" in (adapter.get("options") or {}):
        argv += adapter["options"]["trust_all"]

    print(f"idle {int(age)}s · {', '.join(reasons)} · nudging")
    if args.dry_run:
        print("DRY RUN:", " ".join(argv[:4]), "…")
        return 0

    # Never discard the child's output. A nudge that dies on an expired login or
    # an exhausted plan looks exactly like an agent that ignored us, and the
    # difference is the only thing worth knowing at that moment.
    key = os.path.basename(root.rstrip("/")) or "root"
    log_path = os.path.join(STATE_DIR, f"nudge-{key}.log")
    os.makedirs(STATE_DIR, exist_ok=True)
    env = dict(os.environ, PATH=search)
    with open(log_path, "a") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} nudge ===\n")
        log.flush()
        proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)

    # Give it a moment to fail. A healthy turn runs for minutes; anything that
    # exits within seconds died rather than started.
    early = None
    for _ in range(12):
        time.sleep(1)
        if proc.poll() is not None:
            early = proc.returncode
            break

    st.update(attempts=st.get("attempts", 0) + 1, last_nudge=time.time(),
              last_size=size, child_pid=proc.pid,
              last_head=A.sh("git rev-parse --short HEAD", cwd=root))
    if early not in (None, 0):
        tail = ""
        try:
            with open(log_path) as fh:
                tail = " ".join(fh.read().strip().split("\n")[-3:])[-300:]
        except OSError:
            pass
        st.pop("child_pid", None)                 # it is gone; do not guard on a dead pid
        st["last_error"] = {"at": time.time(), "code": early, "tail": tail}
        notify("Voltrai: nudge failed", f"exit {early}: {tail[-120:] or 'see nudge log'}", root)
        print(f"nudge failed (exit {early}): {tail}")
    else:
        st.pop("last_error", None)
    save_state(root, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
