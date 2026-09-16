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
from contextvars import ContextVar
import json
import os
import re
import subprocess
import shutil
import signal
import sys
import time

from . import lib as A
from . import settings as S

STATE_DIR = os.path.join(A.HOME, ".ao")
_DRY_RUN = ContextVar("ao_watchdog_dry_run", default=False)

# Every decision line this module prints is also kept, so a cycle can be read
# back as a trace: what was measured, what was decided, in what order. The one
# question that cost this project the most — "why did it not act?" — used to be
# answerable only by reading a log of prose. `ao watchdog explain` runs a dry
# cycle and prints the trace; `ao watchdog trace` reads the recorded ones.
import builtins as _builtins
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252
_TRACE = []
_FACTS = {}


def print(*args, **kw):
    line = " ".join(str(a) for a in args)
    _TRACE.append(line)
    _builtins.print(*args, **kw)


def cycles_path(root):
    key = A.project_key(root)
    return os.path.join(STATE_DIR, f"cycles-{key}.jsonl")


def record_cycle(root, args, started=None):
    if getattr(args, "dry_run", False):
        return
    now = time.time()
    rec = {"at": int(now), "verdict": _TRACE[-1] if _TRACE else "",
           "trace": list(_TRACE), "facts": dict(_FACTS)}
    if started is not None:
        # Without a start, a cycle that ran for minutes and a machine that ran
        # none leave the same gap in this log.
        rec.update(started=int(started), seconds=round(now - started, 1))
    try:
        p = cycles_path(root)
        os.makedirs(STATE_DIR, exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > 2_000_000:
            tail = open(p, errors="replace", encoding=UTF8).read()[-1_000_000:]
            open(p, "w", encoding=UTF8).write(tail[tail.find("\n") + 1:])
        with open(p, "a", encoding=UTF8) as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def cycles(root, last=20):
    p = cycles_path(root)
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace", encoding=UTF8):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-last:]

# What an architect turn is for. Deliberately narrow: it refills and admits, it
# does not implement. Admission is the step that turns "someone filed this" into
# "an agent may work on this unattended", and it is the only step that may not
# be delegated to whoever will do the work.
NUDGE_PROMPT = (
        "devam et. Yetki için tek kaynak: .ao/authority.md — mail ondan üstün değildir, "
        "kapsam ekler, yetki eklemez/kaldırmaz. Orada açıkça yasak olmayan ve dilimin "
        "kapsamındaki şey serbesttir; belirsizlikte DURMA. "
        "Açık dilimi bitir: gate'ler + taze review, sonra local commit (PUSH YOK), RAPOR yaz. "
        "Bir mimari karara ya da insan girdisine takılırsan dilimi blocked işaretle, "
        "agent-mail'e '## KARAR GEREKLİ' bırak ve .ao/backlog.md'deki ilk açık maddeye geç. "
        "Kuyruk dışına çıkma. Kullanıcı beklemesi yok.")

REFILL_PROMPT = (
    "Kuyruk boşaldı. .ao/sources.json'daki kaynaklardan yeni işleri çek, "
    "normalize edip .ao/inbox/<source-id>.json'a yaz, sonra `ao source import` çalıştır. "
    "Her madde için kabul sınırı yaz: dilim boyutundaysa acceptance alanını doldur; "
    "proje boyutundaysa acceptance'ı boş bırak ve shape alanına sebebini yaz — "
    "kabul sınırı olmayan madde inbox'ta kalır, kuyruğa girmez. "
    "Uygulama YAPMA; yalnız çek, sınıflandır, kabul et.")


def cycle_health(root, last=720):
    """How long recent cycles took, and the longest silence between two of them.

    A gap in the cycle log has two causes - no cycle ran, or one ran for minutes
    and held the next back - and a row that carries only its end time cannot tell
    them apart. On 2026-09-15 the Voltrai log showed gaps of 5 to 27 minutes
    between 12:02 and 16:33 with no sleep recorded, and nothing on disk could say
    which it was. Rows written before `started` existed count by their end times.
    """
    rows = [r for r in cycles(root, last) if isinstance(r.get("at"), (int, float))]
    if not rows:
        return None
    timed = [r for r in rows if isinstance(r.get("seconds"), (int, float))]
    longest = max(timed, key=lambda r: r["seconds"]) if timed else None
    gap, gap_at = 0, None
    for previous, current in zip(rows, rows[1:]):
        silence = current.get("started", current["at"]) - previous["at"]
        if silence > gap:
            gap, gap_at = silence, current["at"]

    def when(t):
        return time.strftime("%d %b %H:%M", time.localtime(t))

    return {"count": len(rows),
            "longest_seconds": longest["seconds"] if longest else None,
            "longest_at": when(longest["at"]) if longest else None,
            "gap_minutes": gap / 60, "gap_at": when(gap_at) if gap_at else None}


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
    for d in list(S.get(None, "binaries.extra_dirs")) + [
            "~/.local/bin", "~/bin", "/usr/local/bin", "/opt/homebrew/bin",
            "/usr/bin", "/bin", "/usr/sbin", "/sbin"]:
        parts.append(os.path.expanduser(d))
    # Version-manager installs keep node beside the agent binary; a wake that
    # resolves the newest `claude` there must find that node too.
    for cand in A.binary_candidates("claude") + A.binary_candidates("node"):
        parts.append(os.path.dirname(cand))
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
    for chunk in os.pathsep.join(parts).split(os.pathsep):
        if chunk and chunk not in seen:
            seen.add(chunk)
            out.append(chunk)
    return os.pathsep.join(out)


# Legacy call sites that do not declare an audience are classified by title.
# Explicit audience is authoritative: wording and localization must never turn a
# human alarm into an architect-only ledger row (or vice versa).
HUMAN_AUDIENCE = ("out of quota", "agent stuck", "nudge failed", "watchdog",
                  "turns piling up", "needs you", "provider degraded")


def for_human(title):
    # Match the words after the "<project>: " prefix only. Titles name the project,
    # and a project called, say, "watchdog-lab" must not turn every alert it raises
    # into a human one by its name alone.
    subject = title.split(": ", 1)[1] if ": " in title else title
    return any(k in subject.lower() for k in HUMAN_AUDIENCE)


def desktop_notify(title, msg):
    """A desktop notification where the platform has one; never an exception (audit).

    `osascript` exists only on macOS. Elsewhere its FileNotFoundError ended the cycle
    before the notice was recorded or the state saved.
    """
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return False
    title, msg = (str(part).replace('"', "'").replace("\\", "/") for part in (title, msg))
    try:
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                       capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def notify(title, msg, root=None, key=None, window=1800, audience=None, level=None,
           quiet_until=None, evidence=None):
    """Raise an alert at most once per window, and always record that we did.

    An explicit audience is a routing decision and is never inferred again from
    the title.  Title inference exists only for legacy callers that omit it.

    The watchdog runs every two minutes. A guard that notifies on each run turns
    a single ongoing condition into thirty alerts an hour, and a human who learns
    to swipe those away has effectively turned the alerting off — which is worse
    than not alerting, because everyone still believes it works.

    Recording happens either way. A suppressed alert is evidence too: a long run
    of them says the condition has held for a long time.
    """
    key = key or title
    if audience is None:
        audience = "human" if for_human(title) else "architect"
    if audience not in ("architect", "human"):
        raise ValueError("unknown notification audience: %s" % audience)
    dry_run = _DRY_RUN.get()
    # Architect-audience alerts are recorded and delivered through the mailbox and
    # the wake; they never use a human channel.
    if audience == "architect":
        if dry_run:
            print(f"DRY RUN: would record architect notice: {title}")
        elif root:
            A.record_notice(root, title, msg, sent=False, key=key, evidence=evidence)
        return False
    # The ladder: this is an orange (a person must act). Standing an hour, it
    # rings red and goes to mail — the channel people open when they wake up.
    project = (A.project_key(root) if root else "ao")
    try:
        red_after = S.get(A.load_config(root) if root else None, "alarms.red_after_minutes") * 60
    except Exception:
        red_after = A.ALARM_RED_AFTER
    # Dry-run uses the same alarm state and calculation as a live cycle, but the
    # preview is deliberately not persisted.
    # A snoozed alarm is recorded, not rung: off the channels and off the ladder, so
    # it neither turns red nor mails until its snooze ends.
    snoozed = A.alarm_snoozed(project, key)
    if snoozed:
        until = time.strftime("%d %b", time.localtime(snoozed["until"]))
        if dry_run:
            print(f"DRY RUN: would hold {title}: snoozed until {until} by {snoozed.get('by')}")
        elif root:
            A.record_notice(root, title, f"{msg} [snoozed until {until}]", sent=False, key=key, evidence=evidence)
        return False
    ring, episode = A.alarm_touch(
        project, key, level or "orange", red_after=red_after, title=title,
        persist=not dry_run, quiet_until=quiet_until, evidence=evidence,
    )
    if ring == "red" and episode.get("red_due"):
        if dry_run:
            print(f"DRY RUN: would send red e-mail: {title}")
        else:
            try:
                from . import email
                since = time.strftime("%d %b %H:%M", time.localtime(episode.get("first", time.time())))
                if email.send(f"{title}", f"{msg}\n\nDuruyor: {since}'den beri ({episode.get('count', 1)} kez). "
                              f"Proje: {root or '?'}\n`ao alarms` merdiveni, `ao status` durumu gösterir."
                              + ("\n\nKanıt:\n" + "\n".join(A.evidence_lines(evidence))
                                 if evidence else ""), root):
                    A.alarm_mailed(project, key)
                    if root:
                        A.record_notice(root, title, msg, sent=True, key="mail:" + key)
            except Exception:
                pass
    # Told once, with a known end and nothing new to say before it: record, do not ring.
    if episode.get("red_sent") is not None and time.time() < float(episode.get("quiet_until") or 0):
        until = time.strftime("%d %b %H:%M", time.localtime(float(episode["quiet_until"])))
        if dry_run:
            print(f"DRY RUN: would hold {title}: told once, quiet until {until}")
        elif root:
            A.record_notice(root, title, f"{msg} [told once; quiet until {until}]", sent=False, key=key, evidence=evidence)
        return False
    if root and A.notice_recently_sent(root, key, window):
        if dry_run:
            print(f"DRY RUN: would suppress {ring} desktop/Telegram channels "
                  f"(recent notice): {title}")
        else:
            A.record_notice(root, title, msg, sent=False, key=key, evidence=evidence)
        return False
    if root and storm(root):
        if dry_run:
            print(f"DRY RUN: would suppress {ring} desktop/Telegram channels "
                  f"(alert storm): {title}")
        else:
            A.record_notice(root, title, msg, sent=False, key=key, evidence=evidence)
            if not A.notice_recently_sent(root, "storm", 3600):
                A.record_notice(root, f"{project}: alert storm", "12+ alerts in an hour; further ones "
                                "are recorded only — ao notices", sent=True, key="storm")
                desktop_notify(f"{project}: alert storm",
                               "12+ alerts in an hour; further ones recorded only (ao notices)")
        return False
    if dry_run:
        print(f"DRY RUN: would ring {ring} desktop/Telegram channels: {title}")
        return False
    safe = msg.replace('"', "'")[:200]
    desktop_notify(title, safe)
    try:
        from . import telegram
        telegram.send(f"*{title}*\n{msg}", root)
    except Exception:
        pass                                # a phone being unreachable is not a failure
    if root:
        A.record_notice(root, title, msg, sent=True, key=key, evidence=evidence)
    return True


def touch_architect_quota(root, st):
    """Keep a standing architect-quota alarm alive until its reset window.

    ``notify`` touches the alarm episode before applying its desktop rate limit,
    so calling this on every watchdog cycle rings orange once, advances to red
    after the configured interval, and does not create a notification storm.
    """
    until = st.get("arch_quota_until", 0)
    if until <= time.time():
        return False
    text = (st.get("wake_error") or {}).get("text") or "architect quota exhausted"
    reset = time.strftime("%H:%M", time.localtime(until))
    return notify(
        f"{A.project_key(root)}: mimar kotada",
        f"{text[:100]} — uyandırma {reset}'e kadar bekletiliyor; Claude Desktop "
        "auto-continue açıksa oturum kendi devam eder",
        root,
        key="architect-quota",
        window=6 * 3600,
        audience="human",
        quiet_until=until,
    )


def _announce_resolved(root, e):
    """Record an alarm that stopped being raised; never louder than the raise (#40).

    An episode ends when nothing has raised it for two hours, which is not proof the
    condition is gone: a check can stop running, or stop repeating itself. On
    2026-09-15 two episodes about credits that were still exhausted were announced
    "resolved" by e-mail. So the end is recorded as what it is, a red episode's end
    shows once on the desktop, and nothing goes to Telegram or e-mail.
    """
    title = f"{e.get('project', '')}: no longer raised — {e.get('key', '')}"
    msg = (f"stood {e.get('age_s', 0) // 60}m, raised {e.get('count', 1)}×"
           + ("; was red" if e.get("red_sent") else ""))
    was_red = bool(e.get("red_sent")) or e.get("ring") == "red"
    A.record_notice(root, title, msg, sent=was_red, key="resolved:" + e.get("key", ""))
    if not was_red:
        return
    try:
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                       capture_output=True)
    except Exception:
        pass


def storm(root, limit=12):
    """More than `limit` alerts sent in the last hour is a storm: the person has
    stopped reading them. Record, do not ring — except red, which still mails."""
    cut = time.time() - 3600
    sent = [n for n in A.notices(root, limit=400) if n.get("sent") and n.get("at", 0) > cut
            and not str(n.get("key", "")).startswith(("tg:", "mail:", "resolved:", "storm"))]
    return len(sent) >= limit


def state_path(root):
    key = A.project_key(root)
    return os.path.join(STATE_DIR, f"watchdog-{key}.json")


def load_state(root):
    try:
        return json.load(open(state_path(root), encoding=UTF8))
    except Exception:
        return {"attempts": 0, "last_nudge": 0, "last_size": 0}


def save_state(root, st):
    if _DRY_RUN.get():
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(st, open(state_path(root), "w", encoding=UTF8), indent=2)


def arch_alive(root, architect):
    """Is any configured architect turn currently alive?

    A remembered pid or lock can be reused after its process dies. Measure the
    configured binary and cwd again instead; this still prevents two headless
    watchdog helpers while releasing a dead one on the next scan.
    """
    return A.architect_turn_present(root, architect)


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
    # A bare pid can be reused by an unrelated process, and os.kill(pid, 0) sends
    # CTRL_C_EVENT on Windows. Liveness comes from the portable probe, identity from
    # the start recorded when the turn was spawned.
    if not A._pid_alive(pid):
        return False
    started = st.get("child_start")
    return started is None or A._process_start(pid) == started


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
    key = A.project_key(root)
    p = os.path.join(STATE_DIR, f"nudge-{key}.log")
    if not os.path.exists(p):
        return None
    try:
        size = os.path.getsize(p)
        with open(p, errors="replace", encoding=UTF8) as fh:
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


def architect_hold_reason(root, cfg, adapter, st, found=None, now=None):
    """May a notice be held for the architect? {"holdable", "code", "reason"}.

    A notice addressed to the architect reaches no person, so holding it is honest
    only when an architect is going to read it: a wake already in flight, or one
    this cycle will start. The rule this replaces held whenever an architect argv
    was configured, and on 2026-09-14 seven notices in a row sat held behind
    "architect will be woken" while the only architect was an interactive session
    that acts when someone prompts it.

    Built from the predicates the wake in escalate() uses, and pure: it starts
    nothing and writes nothing. Blockers are checked before in-flight wakes, so a
    wake that has already failed is never counted as one that is coming.
    """
    from . import features as F
    now = now or time.time()
    arch = cfg.get("architect") or {}
    argv = arch.get("argv") or []

    def no(code, reason):
        return {"holdable": False, "code": code, "reason": reason}

    if not argv:
        return no("no-architect", "no architect is configured")
    if A.architect_present(root, arch):
        return no("interactive", "the architect session is interactive and acts only when someone prompts it")
    if not F.enabled(cfg, "architect_wake"):
        return no("wake-off", "architect wakes are switched off")
    if not quota_ok(adapter):
        return no("no-quota", "there is no quota headroom to wake the architect")
    if st.get("arch_quota_until", 0) > now:
        return no("quota-block", "the architect is at quota until "
                  + time.strftime("%H:%M", time.localtime(st["arch_quota_until"])))
    if arch_alive(root, arch):
        return {"holdable": True, "code": "wake-running", "reason": "an architect wake is already running"}
    left, reserve = A.window_headroom("claude")
    urgent = any(item.get("kind") == "decision-requested" for item in (found or []))
    if left is not None and left < reserve and not urgent:
        return no("window-reserve", f"the machine's Claude window has {left}% left, below the {reserve}% reserve")
    if "{session}" in " ".join(argv) and arch.get("session") in (None, "auto") \
            and not (A.discover_architect(arch.get("cwd") or root) or {}).get("session"):
        return no("session-unresolved", "the architect session cannot be resolved")
    resolved, ver = A.resolve_binary(argv[0], path=child_path())
    if not resolved:
        return no("binary-missing", f"{argv[0]} cannot be found")
    key = A.project_key(root)
    err = wake_error(os.path.join(STATE_DIR, f"escalate-{key}.log"))
    if err and err.get("kind") == "binary" and err.get("binary") == f"{resolved} {ver}" \
            and now - (st.get("wake_error") or {}).get("at", 0) < 6 * 3600:
        return no("binary-failed", f"the last wake failed with this binary: {err.get('text', '')[:80]}")
    if now - st.get("last_arch_wake", 0) < 900:
        return {"holdable": True, "code": "recent-wake",
                "reason": f"the architect was woken {int((now - st.get('last_arch_wake', 0)) / 60)}m ago"}
    return {"holdable": True, "code": "wakeable", "reason": "architect will be woken"}


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
    project = A.project_key(root)
    hold = None
    for a in found:
        key = f"anomaly:{a['kind']}"
        window = 600 if a["kind"] == "decision-requested" else 3600
        if a["kind"] == "report-waiting" and not open_work(cfg, root):
            continue                     # nothing is stuck; it can wait for a human
        # Suppression is read-only and therefore part of explain's decision path.
        # A dry-run must not claim it would write a report that a live cycle would
        # suppress; it merely skips the suppressed-notice ledger write.
        # An architect-audience notice is recorded unsent, so a check on sent rows
        # never held and the same anomaly wrote a row every cycle (#69). Any row
        # inside the window stops the repeat; the report and that row are the record.
        if A.notice_recently_recorded(root, key, window):
            if args.dry_run:
                print(f"DRY RUN: would suppress anomaly {a['kind']} "
                      f"(reported within {window}s)")
            continue
        if args.dry_run:
            print(f"DRY RUN: would report anomaly {a['kind']} to the architect")
            woke = True
            continue
        name = A.write_report(root, cfg, a["kind"], a["facts"], key=a.get("key"))
        # Say what actually happened. "reported to the architect" was untrue
        # whenever no architect was configured or startable, and a notification
        # that overstates its own effect is how a gap stays invisible.
        # Hold for the architect only when a wake is actually coming; decided from the
        # wake's own predicates, not from whether an argv is configured.
        hold = hold or architect_hold_reason(root, cfg, adapter, st, found)
        if hold["holdable"]:
            notify(f"{project}: anomaly", f"{a['kind']} — {hold['reason']}", root,
                   key=key, window=window, audience="architect")
        else:
            notify(f"{project}: needs you",
                   f"{a['kind']} — no architect will act on it: {hold['reason']}", root,
                   key=key, window=window, audience="human")
        print(f"anomaly {a['kind']}: {'reported as ' + name if name else 'already reported'}")
        woke = True
    arch = cfg.get("architect") or {}

    # Wake on durable state, not on the moment of detection. The notification
    # throttle exists so a human is not rung every two minutes; using it to gate
    # the wake as well meant the architect was only ever started on an anomaly's
    # *first* sighting, and a single suppressed cycle lost it for good. What
    # actually matters is whether a report is still sitting unprocessed — the
    # mailbox is the state, the notification is only a bell.
    # Same exclusion as the anomaly scanner: the watchdog's own reports are
    # addressed to the architect but are not themselves reports awaiting the
    # architect, and counting them here re-woke it endlessly on its own output.
    # Addressed by role, never by an actor's name, and the watchdog's own anomaly
    # reports count: an anomaly nobody is woken for is not escalated (#69). What
    # keeps them from re-waking the architect on its own output is the handed set
    # below, not their names. The architect's notes to itself wake nobody (#18).
    pending = [m for m in A.mailbox(root, cfg["mailbox"])
               if A.to_architect(m, cfg) and not A.from_architect(m, cfg)]
    # Closing the loop out loud: an alert that says a thing was detected, with no
    # later word on whether anything came of it, is what makes someone check by
    # hand — which is the work the alert was supposed to save.
    if st.get("arch_pending") and not pending:
        if args.dry_run:
            print(f"DRY RUN: would announce {st['arch_pending']} completed architect report(s)")
        else:
            try:
                from . import telegram
                telegram.send(f"✅ *Mimar bitirdi* — {st['arch_pending']} rapor kapandı, "
                              f"kuyruk boş", root)
            except Exception:
                pass
            st["arch_pending"] = 0
            save_state(root, st)
    elif pending and not args.dry_run:
        st["arch_pending"] = len(pending)
    last_wake = st.get("last_arch_wake", 0)
    # Wake on the durable state (#69). A report is owed a wake until one that did
    # not fail has been handed it; comparing its time with the last wake dropped it
    # for good whenever that wake died at once. A wake whose log shows a transport
    # failure hands nothing back. A model's own prose saying "Error:" does not count.
    handed = dict(st.get("handed") or {})
    if handed:
        failure = wake_error(os.path.join(STATE_DIR, f"escalate-{project}.log"))
        if failure and (failure.get("at") or 0) >= int(last_wake) - 2 \
                and (failure.get("kind") != "other" or "API Error" in failure.get("text", "")):
            handed = {}
    mtimes = {}
    for m in pending:
        try:
            mtimes[m] = int(os.path.getmtime(os.path.join(root, cfg["mailbox"], m)))
        except OSError:
            continue                  # acknowledged while this cycle ran
    stale = [m for m in pending if m in mtimes and handed.get(m) != mtimes[m]]
    if not args.dry_run and handed != (st.get("handed") or {}):
        st["handed"] = {m: t for m, t in handed.items() if m in mtimes}
        save_state(root, st)
    woke = bool(stale)
    if woke and time.time() - last_wake < 900:
        print(f"{len(stale)} unhandled report(s), but the architect was woken "
              f"{int((time.time() - last_wake) / 60)}m ago")
        woke = False

    # Waking spends a turn on the same provider the implementer uses, so a report
    # that could have waited must not burn the window the implementer needs.
    if woke and arch.get("argv") and not quota_ok(adapter):
        print("reports pending, but no quota headroom to wake the architect")
        if not args.dry_run and time.time() - st.get("last_handoff", 0) > 3600:
            try:
                exe = shutil.which("ao", path=child_path())
                if exe:
                    subprocess.run([exe, "-C", root, "handoff", "--reason",
                                    "mimar uyandırılamadı — kota yok"],
                                   capture_output=True, timeout=120)
                    st["last_handoff"] = time.time()
                    save_state(root, st)
            except Exception:
                pass
        woke = False

    # Wake into absence, never alongside. A live architect does not need a copy of
    # itself: the copy inherits the conversation in progress and continues that
    # rather than the triage it was started for.
    if woke and A.architect_present(root, arch):
        print("reports pending, but the architect is already at the keyboard")
        # Suppressing the wake must not silence the reports (#23): the session acts
        # when someone prompts it, so tell that someone, once per newest report.
        if not args.dry_run:
            newest = max(stale, key=lambda m: mtimes.get(m, 0))
            notify(f"{project}: reports wait for the architect",
                   f"{len(stale)} report(s) in {cfg['mailbox']}/, newest {newest}; the "
                   "architect session is interactive and reads them when prompted",
                   root, key=f"present-pending:{newest}", window=24 * 3600, audience="human")
        woke = False
    from . import features as F
    if woke and not F.enabled(cfg, "architect_wake"):
        print("reports pending; architect_wake feature is off — recorded and alarmed, not woken")
        notify(f"{A.project_key(root)}: needs you", f"{len(stale)} report(s) waiting and architect wakes are off",
               root, key="reports-no-wake", window=3600, audience="human")
        woke = False
    if woke:
        left, reserve = A.window_headroom("claude")
        urgent = any(a.get("kind") == "decision-requested" for a in (found or []))
        if left is not None and left < reserve and not urgent:
            print(f"reports pending, but the machine's Claude window has {left}% left (< reserve {reserve}%); not waking")
            woke = False
    if woke and arch_alive(root, arch):
        print("reports pending, but an architect wake is already running")
        woke = False
    if woke and arch.get("argv") and not args.dry_run:
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
            "Sonra işlediğin mesajı `ao mail ack <dosya-veya-glob>` ile sil; teslim onayı "
            "budur. Normal bir durumsa yalnız sil ve bir şey yapma.\n\n"
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
        resolved, ver = A.resolve_binary(argv[0], path=search)
        key = A.project_key(root)
        log_path = os.path.join(STATE_DIR, f"escalate-{key}.log")
        # Read what the previous wake said before starting another. Same binary,
        # same error, less than six hours old: the human has been told, and a
        # retry is the forty-first identical failure.
        err = wake_error(log_path)
        if resolved and err:
            text, used, when, kind = err["text"], err["binary"], err["when"], err["kind"]
            # The log merges the model's prose with its errors; mask before it is kept or sent.
            text = A.redact(text)
            prev = st.get("wake_error") or {}
            fresh = prev.get("text") != text or prev.get("binary") != used or prev.get("when") != when
            if fresh:
                st["wake_error"] = {"at": time.time(), "text": text, "binary": used,
                                    "when": when, "kind": kind}
                if kind == "quota":
                    # The architect is paused, not broken. Wait for the window, tell
                    # the human once (orange), and remember that the desktop app may
                    # resume the session itself when its auto-continue is on.
                    until = quota_block_until(err)
                    if until:
                        st["arch_quota_until"] = until
                        A.deferred_append(root, "wake", reason="architect quota", until=until)
                save_state(root, st)
                # notify records the notice; a row written first under the same key
                # made notify's own rate limit swallow the ring (#69).
                if kind == "quota":
                    touch_architect_quota(root, st)
                else:
                    notify(f"{key}: mimar uyandırılamadı", f"{kind}: {text[:110]} — ikili: "
                           f"{used or '?'}; `ao doctor`", root, key="architect-wake-failed",
                           window=6 * 3600, audience="human")
            same = used == f"{resolved} {ver}"
            if kind == "binary" and same and time.time() - (st.get("wake_error") or {}).get("at", 0) < 6 * 3600:
                print(f"architect wake failed with this same binary ({used}); not retrying: {text[:90]}")
                resolved = None
            elif kind == "quota" and st.get("arch_quota_until", 0) > time.time():
                print(f"architect at quota until "
                      f"{time.strftime('%H:%M', time.localtime(st['arch_quota_until']))}; not waking")
                resolved = None
            elif kind == "session":
                # A dead session id: resume a different one or none. Setting the config
                # back to auto after argv was built resumed the same dead session (#69).
                dead = st.get("arch_session")
                if sess and (sess == dead or sess in text):
                    found = (A.discover_architect(arch.get("cwd") or root) or {}).get("session")
                    if found and found != sess and found not in text:
                        sess = found
                        argv = [x.replace("{prompt}", prompt).replace("{session}", sess)
                                for x in arch["argv"]]
                        print(f"last wake resumed a dead session; resuming {sess[:12]} instead")
                    else:
                        print("last wake resumed a dead session and no other session was found; not waking")
                        resolved = None
        if resolved and st.get("arch_quota_until", 0) > time.time():
            print(f"architect at quota until "
                  f"{time.strftime('%H:%M', time.localtime(st['arch_quota_until']))}; not waking")
            resolved = None
        held = A.hold_state(root)
        if resolved and held:
            print(f"held by {held.get('by')} since this cycle began; not waking the architect")
            resolved = None
        if resolved:
            argv[0] = resolved
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(log_path, "a", encoding=UTF8) as log:
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} escalate {resolved} {ver} ===\n")
                log.flush()
                # stdin must be closed, not inherited. A detached child holding a
                # pipe or tty it will never read can wait forever for an EOF that
                # never comes: alive, silent, producing nothing. That is the exact
                # shape of the first architect wake — over a minute, no output —
                # and very likely of the fifteen agent processes this project
                # found accumulated in one repository.
                proc = subprocess.Popen(argv, cwd=root, env=dict(os.environ, PATH=search, AO_ROLE="architect"),
                                        stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            st["arch_pid"] = proc.pid
            st["last_arch_wake"] = time.time()
            st["handed"] = {m: mtimes[m] for m in pending if m in mtimes}
            st["arch_session"] = sess
            A.helper_register(root, proc.pid, "architect")   # a judge, not a writer
            A.acquire_architect(root, proc.pid, "watchdog wake")   # one judge at a time
            save_state(root, st)
            try:
                from . import telegram
                telegram.send(f"🤖 *Mimar uyandırıldı* — {len(stale)} rapor "
                              f"işleniyor (pid {proc.pid})", root)
            except Exception:
                pass
            print(f"woke the architect (pid {proc.pid}) to judge it")
    return woke


def queue_past_a_question(root):
    """(what waits, the READY item to take) when a question waits and READY work stands (#84).

    The architect is a person's quota and attention, and both run out. An open
    decision cannot be answered by a nudged turn (#20), but it must not stop the
    queue either: the slice it blocks is parked and the next READY item is taken.
    None when nothing waits, or when nothing is READY and waiting is all there is.
    """
    ready = A.ready(root)
    if not ready:
        return None
    asked = [d for d in A.decisions(root, "open") if d.get("asked_at")]
    if asked:
        return min(asked, key=lambda d: d["asked_at"])["id"], ready[0]["id"]
    blocked = A.board(root)["blocked"]
    return (blocked[0]["id"], ready[0]["id"]) if blocked else None


def parked_note(waits, ready_id):
    """What a nudge adds when a question waits and READY work stands (#84)."""
    return (f" {waits} cevap bekliyor ve kuyruğu durdurmaz: bekleyen dilimi blocked bırak (needs: {waits}), "
            f"soruyu yeniden sorma, READY {ready_id} ile devam et.")


def secondary_note(found):
    """What a nudge adds when nothing is READY here and a secondary project has work (#8)."""
    return (f" Bu projede READY iş yok: ikincil proje {found['name']} ({found['root']}) READY {found['item']}. "
            "Orada devam et; buradaki engeller insanı ya da mimarı bekliyor, bekleme.")


def open_work(cfg, root):
    """Is there something for the implementer to continue? Cheap signals only.

    Only signals that mean *work*. Mail counts when it is addressed to the
    implementer — its own outgoing reports and the watchdog's anomaly files once
    counted as "unread mail", so every report the implementer wrote was a reason
    to nudge it into writing another. Dirty paths count outside the coordination
    directories; a review counts only if it is newer than HEAD.
    """
    reasons = []
    if A.implementer_inbox(root, cfg):
        reasons.append("unread mail")
    # A slice the board says is running is work, whether or not a file has
    # changed yet: an implementer that has just started one and ended its turn
    # on a plan must be nudged back into it.
    if A.board(root)["running"]:
        reasons.append("slice running")
    rs = A.reviewer_state(root)
    # A reviewer that was unavailable with no known window - an expired login, a
    # missing binary - is not work a nudge can do; nudging for it never ended (audit).
    if rs.get("pending_review") and rs.get("until") and rs["until"] <= time.time():
        reasons.append("reviewer window reopened — re-run the pending review")
    if A.product_dirty(root, cfg):
        reasons.append("uncommitted changes")
    # Findings are the newest review that produced a verdict; an UNAVAILABLE or
    # INVALID file is not a finding anyone can act on (audit).
    revs = [(f, v) for f, v in A.reviews(root, cfg["reviews"], limit=8)
            if v in ("APPROVED", "NEEDS_CHANGES")]
    if revs and revs[0][1] == "NEEDS_CHANGES":
        try:
            rev_at = os.path.getmtime(os.path.join(root, cfg["reviews"], revs[0][0]))
            head_at = int(A.sh("git log -1 --format=%ct", cwd=root) or 0)
        except (OSError, ValueError):
            rev_at, head_at = 1, 0
        if rev_at > head_at:
            reasons.append("open review findings")
    return reasons


WAKE_SIGNATURES = (
    ("quota", r"(hit your (?:session|usage|weekly|monthly) limit[^\n]*|usage limit[^\n]*|"
              r"rate limit(?:ed)?[^\n]*resets?[^\n]*|out of (?:credits|quota)[^\n]*)"),
    ("binary", r"(does not support this model[^\n]*|version [^\n]*required[^\n]*|"
               r"command not found[^\n]*|No such file or directory[^\n]*|env: node: [^\n]*)"),
    ("session", r"(No conversation found[^\n]*|[Ss]ession[^\n]{0,40}not found[^\n]*|"
                r"Invalid session[^\n]*)"),
    ("other", r"(API Error: \d{3}[^\n]*|Error: [^\n]{8,})"),
)


# The architect's usage limit comes back within five hours of being hit; a reset
# a message names further away than that cannot be the one it meant (#40).
ARCHITECT_QUOTA_WINDOW = S.default("architect.quota_window_hours") * 3600


RESET_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _reset_clock(hour, minute, meridiem):
    """(hour, minute) on a 24-hour clock from "9", "20", "pm"."""
    h, mi, ap = int(hour), int(minute or 0), (meridiem or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h, mi


def parse_reset(text, now=None, window=None):
    """When does the quota come back? "resets 4:30am" / "resets in 4h 43m" / "resets Sep 14 at 4am" / None.

    `now` is when the message was written, not when it is read: a log line read
    again hours later keeps the reset it named. A clock time already past rolls to
    the next day only while that stays inside `window`, the limit's own length;
    otherwise the message meant the time that has gone. On 2026-09-07 a 17:48
    "resets 9:20pm" was re-read after 21:20 on every cycle and pushed a day ahead
    each time, so the block it raised never ended (#40).
    """
    now = now or time.time()
    m = re.search(r"resets?\s+in\s+((?:\d+\s*[hms]\s*)+)", text, re.I)
    if m:
        secs = 0
        for n, u in re.findall(r"(\d+)\s*([hms])", m.group(1), re.I):
            secs += int(n) * {"h": 3600, "m": 60, "s": 1}[u.lower()]
        return now + secs
    # A weekly limit names its day: "resets Sep 14 at 4am". It was read as no reset
    # at all, so a week-long block was retried every few hours (#41).
    m = re.search(r"resets?\s+(?:on\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
                  r"(\d{1,2})(?:,?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?", text, re.I)
    if m:
        month = RESET_MONTHS.index(m.group(1).lower()) + 1
        h, mi = _reset_clock(m.group(3) or "0", m.group(4), m.group(5))
        lt = time.localtime(now)
        cand = time.mktime((lt.tm_year, month, int(m.group(2)), h, mi, 0, 0, 0, -1))
        if cand < now - 180 * 86400:          # December's message read in January
            cand = time.mktime((lt.tm_year + 1, month, int(m.group(2)), h, mi, 0, 0, 0, -1))
        return cand
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
    if not m:
        return None
    h, mi = _reset_clock(m.group(1), m.group(2), m.group(3))
    lt = time.localtime(now)
    cand = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, 0, 0, 0, -1))
    if cand <= now and (window is None or cand + 24 * 3600 - now <= window):
        cand += 24 * 3600
    return cand


def wake_error(log_path):
    """What the last architect wake said, if it failed.

    {"text", "binary", "when", "kind", "resets_at"} or None. The wake is
    detached, so its outcome can only be read on the next cycle from the log
    segment it wrote. Forty identical failures went unread because nothing
    looked. `kind` is what the caller should do about it: a stale *binary* wants
    another binary, a *quota* wants a wait until `resets_at`, a dead *session*
    wants rediscovery.
    """
    try:
        tail = open(log_path, errors="replace", encoding=UTF8).read()[-20000:]
    except OSError:
        return None
    segs = re.split(r"^=== (\S+ \S+) (escalate|refill)(?: (.*?))? ===$", tail, flags=re.M)
    if len(segs) < 5:
        return None
    when, _, binary, body = segs[-4], segs[-3], segs[-2] or "", segs[-1]
    try:
        at = time.mktime(time.strptime(when, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        at = None
    for kind, pat in WAKE_SIGNATURES:
        m = re.search(pat, body)
        if m:
            text = m.group(1).strip()[:300]
            # A reset is read against when the wake wrote it, not against this cycle.
            resets_at = (parse_reset(body, now=at, window=S.get(None, "architect.quota_window_hours") * 3600)
                         if kind == "quota" else None)
            return {"text": text, "binary": binary, "when": when, "at": at, "kind": kind,
                    "resets_at": resets_at}
    return None


def quota_block_until(err, now=None):
    """Until when a quota error read from the wake log blocks a wake; None once it no longer does.

    The block ends at the reset the message named or, when it named none, one
    limit window after the message was written. An older error describes a
    window that is over, however many times the log is read again (#40).
    """
    now = now or time.time()
    if not err or err.get("kind") != "quota":
        return None
    until = err.get("resets_at") or ((err.get("at") or now) + S.get(None, "architect.quota_window_hours") * 3600)
    return until if until > now else None


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
    ceiling = S.get(None, "quota.block_percent")
    for line in A.quota(adapter):
        for token in line.replace("%", "% ").split():
            if token.endswith("%"):
                try:
                    if float(token[:-1]) >= ceiling:
                        return False
                except ValueError:
                    pass
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--idle-minutes", type=float, default=None,
                   help="default: the project's watchdog.idle_minutes setting")
    p.add_argument("--dry-run", action="store_true")
    # The default prompt encodes two rules learned the expensive way. Park-and-
    # continue: a blocked slice must not stop the run, because the architect can
    # hit a quota limit and the human can be away. And a pointer to one canonical
    # authority file: when "what may I do" is spread across mail, a prompt and
    # months-old instructions, an agent facing an apparent conflict refuses and
    # waits — which is safe, and cost this project four hours with 7,000 lines of
    # finished work sitting uncommitted.
    p.add_argument("--prompt", default=NUDGE_PROMPT)
    args = p.parse_args()
    return run(args)


CYCLE_LOCK = "watchdog-{key}.cycle.lock"


def run(args):
    """One cycle, traced and — unless dry — recorded.

    Live cycles take a per-project lock first. The scheduled job and `ao catchup`
    both ran a cycle, both passed the process scan before either spawned, and both
    resumed the implementer: the two-writer incident the scan exists to prevent.
    A cycle that finds the lock held stands down.
    """
    _TRACE.clear()
    _FACTS.clear()
    root = os.path.abspath(os.path.expanduser(args.root))
    if getattr(args, "idle_minutes", None) is None:
        args.idle_minutes = S.get(A.load_config(root), "watchdog.idle_minutes")
    started = time.time()
    if args.dry_run:
        try:
            return _cycle(args, root)
        finally:
            record_cycle(root, args, started)
    from .storage import LedgerLockTimeout, _exclusive_lock
    key = A.project_key(root)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with _exclusive_lock(os.path.join(STATE_DIR, CYCLE_LOCK.format(key=key)), timeout=0):
            try:
                A.bound_observation_logs(root, STATE_DIR)       # every store is bounded (#50)
            except OSError:
                pass
            try:
                return _cycle(args, root)
            finally:
                record_cycle(root, args, started)
    except LedgerLockTimeout:
        print("another watchdog cycle is running for this project; standing down")
        return 0


def _cycle(args, root):
    """Run one cycle with dry-run effects suppressed across nested helpers."""
    token = _DRY_RUN.set(bool(args.dry_run))
    try:
        return _cycle_impl(args, root)
    finally:
        _DRY_RUN.reset(token)


def _sample_credits(root, st, adapter, project, now=None):
    """Read the provider's credit figure at most once per half hour and act on it.

    Exhaustion is reported from the reading itself, on the first observation: a
    projection has nothing to say once the plan has already run out, which is the
    moment it matters. The burn rate still warns ahead of it. A reading that cannot
    be taken is recorded as a broken check, which `ao doctor` shows, instead of
    being skipped: a silent check and a passing check must never look the same. A
    failed attempt waits the same half hour before the next one.
    """
    now = now or time.time()
    if now - max(st.get("last_credit_sample", 0), st.get("last_credit_attempt", 0)) <= 1800:
        return
    if not (adapter.get("billing") or {}).get("api"):
        return
    try:
        acct = A.kiro_account_usage()
    except Exception as exc:
        acct = {"error": f"the usage check raised {type(exc).__name__}"}
    if acct is None:
        return
    st["last_credit_attempt"] = now
    if not acct.get("limit"):
        reason = acct.get("error") or ("the CLI's token has expired" if acct.get("expired")
                                       else "the provider returned no credit limit")
        st["credit_check_problem"] = {"at": int(now), "reason": reason}
        save_state(root, st)
        return
    st.pop("credit_check_problem", None)
    A.record_credit_sample(root, acct.get("used", 0), acct["limit"], acct.get("reset_at"),
                           account=acct.get("account"))
    st["last_credit_sample"] = now
    save_state(root, st)
    used, limit = float(acct.get("used") or 0), float(acct["limit"])
    if used >= limit:
        reading = A.notice_evidence("credit_reading", [
            {"value": f"{used:.0f}/{limit:.0f}", "at": int(now),
             "source": f"GetUsageLimits, account {acct.get('account')}"}])
        notify(f"{project}: credits exhausted",
               f"{used:.0f}/{limit:.0f} used; the plan is spent and only overage, if enabled, runs "
               f"until the reset. New account (keyflip) or `ao features off …`", root,
               key="credits-exhaust", window=6 * 3600, audience="human", level="red", evidence=reading)
        return
    br = A.burn_rate(root)
    if br and br["before_reset"]:
        evidence = A.notice_evidence("burn_rate", [
            {"value": f"{sample['used']:.0f}/{sample['limit']:.0f}", "at": sample.get("at"),
             "source": f"GetUsageLimits, account {sample.get('account')}"} for sample in br["samples"]])
        notify(f"{project}: credits run out {time.strftime('%d %b', time.localtime(br['exhausts_at']))}",
               f"{br['used']:.0f}/{br['limit']:.0f} at {br['per_day']:.0f}/day; the reset is later. "
               f"New account (keyflip) or `ao features off …`", root, key="credits-exhaust",
               window=6 * 3600, audience="human", level="red", evidence=evidence)


DECISION_HUMAN_AFTER = S.default("decisions.human_after_minutes") * 60


def escalate_open_decisions(root, project, dry_run=False, now=None):
    """A decision nobody has answered reaches a person (#20).

    Fifteen minutes after it was asked (`decisions.human_after_minutes`) it rings
    orange on the human channel, once
    an hour, and the alarm ladder turns it red and mails after its hour. The wake
    is not waited for: on 2026-09-07 a decision stood three hours while the only
    notice about it was held for an architect that never came. An answered
    decision stops being raised and its alarm ends on its own. Returns the ids
    that are ringing.
    """
    now = time.time() if now is None else now
    ringing = []
    wait = S.get(A.load_config(root), "decisions.human_after_minutes") * 60
    for decision in A.decisions(root, "open"):
        asked = decision.get("asked_at") or 0
        if not asked or now - asked < wait:
            continue
        ringing.append(decision.get("id"))
        if dry_run:
            print(f"DRY RUN: would ring a person about decision {decision.get('id')}")
            continue
        notify(f"{project}: decision waiting",
               f"{decision.get('id')}: {str(decision.get('question') or '')[:120]} — open "
               f"{int((now - asked) / 60)}m; ao answer {decision.get('id')} <key>",
               root, key=f"decision-open:{decision.get('id')}", window=3600, audience="human")
    return ringing


def report_ungranted_commits(root, project, st, limit=20):
    """Tell the architect once about commits whose tree no grant bound (#109).

    A harness granted every tool can run `git commit --no-verify`, and nothing in
    its grant keeps that out. The landed-tree check cannot stop such a commit; run
    every cycle, it makes one visible within a cycle. Commits already told are kept
    in the watchdog state, so a standing one is not re-reported every two minutes.
    Returns how many there are.
    """
    try:
        stray = A.commits_without_grant(root, limit=limit)
    except Exception:
        return 0
    told = set(st.get("ungranted_told") or [])
    new = [sha for sha in stray if sha not in told]
    if new:
        notify(f"{project}: {len(new)} commit(s) landed without a grant",
               ", ".join(sha[:12] for sha in new[:5])
               + ": no grant bound their trees — a path staged after commit-check, "
               "or a commit made outside ao", root, key=f"ungranted-commits:{new[0][:12]}",
               window=30 * 86400, audience="architect")
        st["ungranted_told"] = sorted(told | set(new))[-200:]
        save_state(root, st)
    return len(stray)


def _cycle_impl(args, root):
    # Proof the watchdog ran, before anything that can end the cycle early: a
    # broken session binding was reported to people as a dead watchdog (audit).
    if not args.dry_run:
        A.heartbeat(root)
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
    if not args.dry_run:
        A.reconcile_mail_ledger(root, cfg)  # deleted mail becomes a consumed row
        A.record_progress(root, cfg)      # history of what moved, for the spin check
    project = A.project_key(root)
    try:
        bd = A.board(root)
        _FACTS.update(idle_s=int(age), transcript_bytes=size,
                      inbox=len(A.implementer_inbox(root, cfg)),
                      standing_request=bool(A.waiting_on_architect(root, cfg)),
                      queued=len(bd["queued"]), running=len(bd["running"]), blocked=len(bd["blocked"]),
                      hold=bool(A.hold_state(root)),
                      arch_quota_until=st.get("arch_quota_until") or None,
                      arch_present=A.architect_present(root, cfg.get("architect") or {}),
                      last_wake_error=(st.get("wake_error") or {}).get("kind"))
    except Exception as exc:                                  # facts must never stop a cycle
        _FACTS["facts_error"] = str(exc)[:120]
    # Alarm hygiene, every cycle: keep durable conditions raised before closing
    # episodes that went quiet, then say so once.  A quota window lives in state,
    # so unlike a transient notice it must keep aging toward red while it stands.
    # In dry-run, notify previews that same touch without persisting it.
    # A present, working architect is not at quota, whatever the cache says (#40).
    architect = cfg.get("architect") or {}
    if st.get("arch_quota_until", 0) > time.time() and architect.get("argv") \
            and A.architect_present(root, architect):
        st.pop("arch_quota_until", None)
        save_state(root, st)
        print("the architect is present and working; its cached quota block is cleared")
    touch_architect_quota(root, st)
    if not args.dry_run:
        for e in A.expire_alarms(project):
            _announce_resolved(root, e)
        # The dead man's switch is a real external ping, not an explain probe.
        _FACTS["ping"] = A.ping(root)
    else:
        _FACTS["ping"] = "dry-run"
    if not args.dry_run:
        _sample_credits(root, st, adapter, project)
    # Only meaningful when no implementer turn is running: a sub-agent's writes
    # do not appear as the parent's tool calls and would read as a stranger's.
    fe = [] if A.agent_pids(root, adapter) else A.foreign_edits(root, cfg)
    _FACTS["foreign_edits"] = fe
    if not args.dry_run:
        _FACTS["ungranted_commits"] = report_ungranted_commits(root, project, st)
    _FACTS["decisions_ringing"] = escalate_open_decisions(root, project, dry_run=args.dry_run)
    parked = A.reviewer_state(root)
    if parked.get("pending_review") and not parked.get("until") and not args.dry_run:
        notify(f"{project}: a review is parked",
               f"the reviewer was unavailable: {A.redact(parked.get('reason') or '?')[:120]} — a person "
               "restores it, carries the request with ao collect-review, or waives", root,
               key="review-parked", window=24 * 3600, audience="human")
    for sib, age_s in A.stale_siblings(root).items():
        notify(f"{sib}: watchdog silent", f"no heartbeat for {age_s // 60}m — its watchdog is not "
               f"running; launchctl / ao watchdog status", root, key=f"watchdog-dead:{sib}",
               window=3600, audience="human")
    hs = A.hold_state(root)
    if hs and time.time() - int(hs.get("at") or time.time()) > 4 * 3600:
        notify(f"{project}: hold standing {int((time.time() - hs['at']) / 3600)}h",
               f"set by {hs.get('by', '?')}: {hs.get('reason', '')} — ao hold release when done",
               root, key="hold-standing", window=6 * 3600, audience="human", level="red")

    # What the implementer declares needs a person reaches a person, never held for an agent (#92).
    for item in A.human_waits(root):
        notify(f"{project}: needs you", f"{item['id']} waits on a person: "
               f"{item['notes'].get('needs') or item['title']}", root, key=f"waiting-human:{item['id']}",
               window=6 * 3600, audience="human")
    # A decision request nobody has been shown climbs the ladder by its age: the
    # architect first, then a person's desktop and phone, then e-mail (#30).
    for message in A.unseen_messages(root, cfg):
        if message["class"] != "needs-decision":
            continue
        minutes = message["age"] / 60
        level = ("red" if minutes >= S.get(cfg, "mail.unseen_red_minutes")
                 else "orange" if minutes >= S.get(cfg, "mail.unseen_orange_minutes")
                 else "yellow" if minutes >= S.get(cfg, "mail.unseen_yellow_minutes") else None)
        if level:
            text = f"{message['id']} has waited {int(minutes)}m and nobody has been shown it"
            notify(f"{project}: unread decision request", text, root, key=f"unseen:{message['id']}", window=3600,
                   audience="architect" if level == "yellow" else "human",
                   level=None if level == "yellow" else level)
    # An agent that is busy and producing nothing never trips the idle guard, so
    # check it before the guard chain rather than inside it. Notify only; a nudge
    # would add a turn to a loop that is already spending them.
    spin = A.spinning(root)
    if spin and time.time() - st.get("last_spin_notice", 0) > 1800:
        notify(f"{project}: agent spinning", f"{spin}m busy, nothing committed or changed — needs re-specifying", root, audience="architect")
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
    # What an ended turn left behind is cleared before anything is counted. A
    # wrapper that was reaped by pid — or exited on its own — leaves its runtime
    # and engine children alive with this repo as their cwd; the implementer
    # counts them as writers and refuses to write, its empty turns trip the
    # reaper again, and the reaper makes one more. They are identified by shape
    # (no terminal, dead group leader), never by age, so nothing a person is in
    # can match.
    dead = A.orphans(root, adapter)
    if dead:
        print(f"{len(dead)} orphaned agent process(es) left by an ended turn; clearing {dead}")
        if not args.dry_run:
            A.record_notice(root, "orphans cleared", f"{len(dead)} leftover process(es): {dead}", False, key="orphans")
            A.sweep_orphans(dead)
    running = [p for p in A.agent_pids(root, adapter) if p not in set(dead)]
    _FACTS.update(writers=len(A.process_trees(running)) if running else 0, orphans=len(dead))
    # Windows cannot say which tree an agent works in (#71). One that cannot be
    # placed may be this tree's writer; starting another beside it is the two-writer
    # incident, so stand down and say why.
    unplaced = A.unplaced_agent_pids(root, adapter)
    if unplaced:
        print(f"{len(unplaced)} agent process(es) cannot be placed in a tree - Windows exposes no "
              f"process working directory ({unplaced}); not starting another turn")
        return 0
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
        # The transcript closed the turn and the process is still here past the
        # idle window: a runtime that forgot to exit, not a turn. Reap now.
        ended = A.turn_ended(cfg) and age >= args.idle_minutes * 60
        if ended:
            print(f"turn ended in the transcript {int(age / 60)}m ago but {len(running)} process(es) linger; reaping")
        if age < args.idle_minutes * 60 * 3 and not ended:
            print(f"{len(turns)} turn(s) already in this tree "
                  f"(roots {turns}, {len(running)} processes); not starting another")
            if len(turns) > 2:
                notify(f"{project}: turns piling up",
                       f"{len(turns)} concurrent turns — `ao hold` to clear", root)
            return 0
        # Reaping is an action, so it keeps the conservative bound even though
        # reporting no longer does.
        print(f"{len(running)} process(es) alive but silent {int(age / 60)}m; reaping")
        notify(f"{project}: reaping hung turn",
               f"{len(running)} process(es) silent {int(age / 60)}m — cleaning up", root, audience="architect")
        if args.dry_run:
            print("DRY RUN: would reap", running)
            return 0
        # Reap only what we could have started. A person's interactive session
        # in this tree is silent between their keystrokes, not hung.
        # By process group: the flag is on the wrapper, the children carry none.
        for pid in A.agent_pids(root, adapter, headless_only=True):
            A.kill_turn(pid, signal.SIGTERM)
        for _ in range(20):
            if not A.agent_pids(root, adapter, headless_only=True):
                break
            time.sleep(0.5)
        for pid in A.agent_pids(root, adapter, headless_only=True):
            A.kill_turn(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        # Only headless turns were reaped. A person's interactive session in this
        # tree is still here and still a writer; nudging next to it starts a second.
        # Counted as what the reaper could not have started, from the same scan.
        headless = set(A.agent_pids(root, adapter, headless_only=True))
        people = [pid for pid in A.agent_pids(root, adapter) if pid not in headless]
        if people:
            print(f"{len(people)} interactive agent process(es) remain in this tree after reaping; "
                  "not starting another turn")
            return 0

    # 1 — still working
    #
    # Progress is not the same as a commit. A slice whose independent review found
    # real defects is *correct* to withhold the commit while it fixes them, and to
    # a HEAD-only check that looks exactly like an agent that has stopped: three
    # nudges, no movement, stand down. It happened — two hours of a live slice sat
    # idle because the counter could not tell a careful implementer from a dead
    # one. Fingerprint everything that moves when work is happening.
    fp = A.work_fingerprint(root)
    if st.get("attempts") and fp != st.get("last_fingerprint"):
        st.update(attempts=0, last_fingerprint=fp)
        save_state(root, st)
    # An implementer that answered a nudge with nothing is not asked again until
    # something it is given, or something it produces, moves (#96).
    idle = st.get("idle_answer")
    if idle and (fp != idle.get("work") or A.nudge_inputs(root, cfg) != idle.get("inputs")):
        for key in ("idle_answer", "nudge_size", "nudge_fingerprint", "nudge_inputs"):
            st.pop(key, None)
        st["attempts"] = 0
        save_state(root, st)
        print("the board, the backlog, a decision, mail or the work moved since the "
              "implementer had nothing to do; nudges resume")
    if age < args.idle_minutes * 60:
        if st.get("attempts") and size != st.get("last_size"):
            # The transcript moving is not the work moving: a nudged turn that finds
            # nothing to do writes its answer too. Only the fingerprint above resets
            # the backoff.
            st.update(last_size=size)
            save_state(root, st)
        print(f"working ({int(age)}s since last write)")
        return 0
    # Presence is the agent's, not this tree's: writing in a secondary project is working (#22).
    elsewhere = A.working_elsewhere(cfg, args.idle_minutes * 60)
    if elsewhere:
        print(f"working in {elsewhere['name']} ({int(elsewhere['age'])}s since its last write there); "
              "not nudging here")
        return 0

    # 2 — nothing to continue. Before standing down, ask why: an empty board can
    # mean "all done" or "the queue ran dry". Only the second one is actionable,
    # and it needs the *architect*, not the implementer — refilling means pulling
    # from a tracker and deciding what may be worked, and an implementer that
    # chooses its own scope is the one thing this tool exists to prevent.
    # An implementer that asked for a decision and got no answer is not idle; it
    # is waiting, and a nudge cannot answer it. Eighty nudged turns once produced
    # eighty copies of the same request. The wake path above has already told
    # the architect; the only useful thing here is to say so and stand down.
    waiting = A.waiting_on_architect(root, cfg)
    if waiting:
        name, at = waiting
        print(f"implementer is waiting on the architect since "
              f"{time.strftime('%H:%M', time.localtime(at))} ({name}); not nudging")
        return 0
    # An open decision cannot be answered by a nudged turn (#20), and it does not
    # stop the queue while READY work stands: its slice parks and the next starts (#84).
    asked = [d for d in A.decisions(root, "open") if d.get("asked_at")]
    passing = queue_past_a_question(root)
    if asked and not passing:
        oldest = min(asked, key=lambda d: d["asked_at"])
        print(f"decision {oldest['id']} is open since "
              f"{time.strftime('%H:%M', time.localtime(oldest['asked_at']))}; not nudging")
        return 0
    if passing:
        print(f"{passing[0]} waits, and {passing[1]} is READY: an open question does not stop the queue")
    reasons = open_work(cfg, root)
    if not reasons and passing:
        reasons = [f"READY {passing[1]} while {passing[0]} waits"]
    # Nothing READY here and a secondary project has work: the nudge names it (#8).
    elsewhere_ready = None
    if not reasons and not A.ready(root):
        elsewhere_ready = A.secondary_ready(cfg)
        if elsewhere_ready:
            reasons = [f"READY {elsewhere_ready['item']} in {elsewhere_ready['name']}"]
    if not reasons:
        sc = A.sources(root)
        arch = cfg.get("architect") or {}
        depth = len(A.board(root)["queued"])
        # With a source bound, refill below its threshold; without one, an empty
        # queue is still a refill condition — the architect fills it from the
        # spec. This project had no source, so the empty queue was never a
        # refill and the only signal was the implementer's own blocked report.
        threshold = sc.get("refill_below", 3) if sc else 1
        from . import features as F
        if arch.get("argv") and depth < threshold and not F.enabled(cfg, "refill"):
            print(f"queue low ({depth}); refill feature is off — alarming instead")
            notify(f"{A.project_key(root)}: needs you", f"queue has {depth} item(s) and refill wakes are off — add slices to .ao/backlog.md",
                   root, key="queue-empty-no-refill", window=3600, audience="human")
            return 0
        if arch.get("argv") and depth < threshold and A.architect_present(root, arch):
            print("queue low, but the architect is already at the keyboard")
            return 0
        if arch.get("argv") and depth < threshold:
            if arch_alive(root, arch):
                print("queue low, but an architect wake is already running")
                return 0
            if args.dry_run:
                print(f"queue low ({depth}); would wake the architect to refill")
                return 0
            if child_alive(st):
                print("queue low, but a turn is still running")
                return 0
            if time.time() - st.get("last_refill", 0) < 1800:
                print(f"queue low ({depth}); refill wake sent "
                      f"{int((time.time() - st.get('last_refill', 0)) / 60)}m ago; waiting")
                return 0
            # A report wake may have started earlier in this same cycle, now that the
            # watchdog's own reports wake the architect (#69). The helper scan is the
            # guard; the wake's own time is the one that cannot miss a young process.
            if time.time() - st.get("last_arch_wake", 0) < 900:
                print(f"queue low ({depth}); the architect was woken "
                      f"{int((time.time() - st.get('last_arch_wake', 0)) / 60)}m ago; waiting")
                return 0
            if st.get("arch_quota_until", 0) > time.time():
                print("queue low, but the architect is at quota; waiting")
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
            resolved, ver = A.resolve_binary(argv[0], path=search)
            if not resolved:
                print(f"architect command {argv[0]} not on PATH")
                return 0
            argv[0] = resolved
            key = A.project_key(root)
            log_path = os.path.join(STATE_DIR, f"refill-{key}.log")
            if A.hold_state(root):
                print("held since this cycle began; not waking the architect to refill")
                return 0
            # A refill spends the same window a report wake does, and its failures
            # were never read (audit): check the quota and the last refill's log.
            if not quota_ok(adapter):
                print("queue low, but there is no quota headroom to wake the architect")
                return 0
            failed = wake_error(log_path)
            blocked = quota_block_until(failed)
            if blocked:
                print(f"the last refill hit the architect's limit; waiting until "
                      f"{time.strftime('%H:%M', time.localtime(blocked))}")
                return 0
            if failed and failed.get("kind") != "quota" and time.time() - (failed.get("at") or 0) < 6 * 3600:
                notify(f"{project}: refill failed", f"{failed.get('kind')}: "
                       f"{A.redact(failed.get('text', ''))[:110]}", root,
                       key="refill-failed", window=6 * 3600, audience="human")
                # A transport error is retried on the refill spacing; a binary or a
                # session that failed fails the same way until someone changes it.
                if failed.get("kind") in ("binary", "session") and failed.get("binary") == f"{resolved} {ver}":
                    print(f"the last refill with this binary failed ({failed.get('kind')}); not retrying")
                    return 0
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(log_path, "a", encoding=UTF8) as log:
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} refill {resolved} {ver} ===\n")
                log.flush()
                proc = subprocess.Popen(argv, cwd=root, env=dict(os.environ, PATH=search, AO_ROLE="architect"),
                                        stdin=subprocess.DEVNULL,   # see escalate()
                                        stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            st.update(arch_pid=proc.pid, last_refill=time.time())
            A.helper_register(root, proc.pid, "architect")   # a judge, not a writer
            A.acquire_architect(root, proc.pid, "watchdog refill")
            save_state(root, st)
            print(f"queue low ({depth} < {threshold}); woke the architect")
            return 0
        print("idle, but no open work — leaving it alone")
        return 0

    # 3 — over the round budget: a nudge would only buy another identical round.
    # Unless the architect has already intervened: a coordination message newer
    # than the newest review means the slice was re-specified, and the budget
    # applies to the old specification, not the new one.
    rn = A.rounds(root, cfg["reviews"])
    budget = S.get(cfg, "round_budget")
    intervened = False
    revs_all = A.reviews(root, cfg["reviews"], limit=1)
    if revs_all:
        # Only the architect re-specifies a slice; any other mail, the watchdog's own
        # included, is not an intervention (audit). Files can be acknowledged while
        # this reads them.
        try:
            rev_mt = os.path.getmtime(os.path.join(root, cfg["reviews"], revs_all[0][0]))
        except OSError:
            rev_mt = time.time()
        for m in A.mailbox(root, cfg["mailbox"]):
            if not A.from_architect(m, cfg):
                continue
            try:
                newer = os.path.getmtime(os.path.join(root, cfg["mailbox"], m)) > rev_mt
            except OSError:
                continue
            if newer:
                intervened = True
                break
    if rn > budget and not intervened:
        notify(f"{project}: over budget", f"round {rn}/{budget} — re-specify, split or change actor", root, audience="architect")
        print(f"over budget ({rn}/{budget}); notified instead of nudging")
        return 0
    if rn > budget and intervened:
        print(f"over budget ({rn}/{budget}) but re-specified since the last review; proceeding")

    # 4 — no quota. Delivery still works: reports are written above this gate and
    # the transport is HTTP, so a pending question reaches a phone even now. What
    # stops is deciding — so hand the state to whoever can.
    if not quota_ok(adapter):
        if not args.dry_run:
            if not A.recently_deferred(root, "nudge"):
                A.deferred_append(root, "nudge", reason="implementer quota")
            if time.time() - st.get("last_handoff", 0) > 3600:
                try:
                    import subprocess as _sp
                    exe = shutil.which("ao", path=child_path())
                    if exe:
                        _sp.run([exe, "-C", root, "handoff", "--reason",
                                 "sağlayıcı kotası tükendi"],
                                capture_output=True, timeout=120)
                        st["last_handoff"] = time.time()
                        save_state(root, st)
                except Exception:
                    pass
        notify(f"{project}: out of quota",
               "provider window exhausted; handoff note sent", root)
        print("provider out of headroom; not nudging")
        return 0

    # 4b — the provider stopped, not the agent. Nudging an outage buys nothing
    # and, while the stalled turn is still retrying, costs a second writer.
    degraded = provider_degraded(root)
    if degraded:
        notify(f"{project}: provider degraded", f"{degraded} — waiting, not nudging", root)
        print(f"provider degraded ({degraded}); waiting rather than nudging")
        return 0

    # 4c — the implementer answered the last nudge and changed nothing while nothing
    # it is given moved: it has no work it can see, whatever the board says. Asking
    # again every idle window cost 29 turns in one morning (#96). This reads what
    # the turn did, not the wording of its report.
    if (st.get("nudge_size") is not None and size > st["nudge_size"] and A.turn_ended(cfg)
            and fp == st.get("nudge_fingerprint")
            and A.nudge_inputs(root, cfg) == st.get("nudge_inputs")
            and (st.get("last_error") or {}).get("at", 0) < st.get("last_nudge", 0)):
        idle = st.get("idle_answer")
        if not idle:
            idle = {"since": st.get("last_nudge") or time.time(), "work": fp,
                    "inputs": st.get("nudge_inputs")}
            st["idle_answer"] = idle
            save_state(root, st)
            notify(f"{project}: implementer has nothing to do",
                   "it answered the last nudge without changing anything; no more nudges until "
                   "the board, the backlog, a decision or its mail changes", root,
                   key="idle-answer", window=12 * 3600, audience="human")
        since = time.strftime("%H:%M", time.localtime(idle["since"]))
        print(f"idle since {since}: the implementer answered the last nudge without changing "
              f"anything; waiting for the board, the backlog, a decision or mail; not nudging")
        return 0

    # 5 — the previous nudge changed nothing
    max_attempts = S.get(cfg, "watchdog.max_attempts")
    if st.get("attempts", 0) >= max_attempts:
        notify(f"{project}: agent stuck", f"{max_attempts} nudges, no progress — needs a human", root)
        print("backoff exhausted; notified a human")
        return 0
    if st.get("last_nudge") and size == st.get("last_size"):
        wait = args.idle_minutes * 60 * (2 ** st["attempts"])
        if time.time() - st["last_nudge"] < wait:
            print(f"backing off ({st['attempts']} attempts, waiting {int(wait)}s)")
            return 0

    prompt = args.prompt + (parked_note(*passing) if passing else "") \
        + (secondary_note(elsewhere_ready) if elsewhere_ready else "")
    argv = [x.replace("{session}", impl["session"]).replace("{prompt}", prompt)
            for x in (adapter.get("resume", {}).get("argv") or [])]
    if not argv:
        print("adapter has no resume command")
        return 1

    # Resolve the CLI and hand the child a usable PATH — see child_path().
    search = child_path()
    resolved = shutil.which(argv[0], path=search)
    if not resolved:
        notify(f"{project}: watchdog", f"{argv[0]} not on PATH; cannot nudge", root)
        print(f"{argv[0]} not found on PATH ({search})")
        return 1
    argv[0] = resolved
    # Only widen to trust-all when the adapter's resume carries no allowlist of
    # its own. Appending --dangerously-skip-permissions on top of --allowedTools
    # would silently override the narrower grant.
    scoped = any(a in ("--allowedTools", "--allowed-tools", "--trust-all-tools") for a in argv)
    added = []
    if not scoped and "trust_all" in (adapter.get("options") or {}):
        added = list(adapter["options"]["trust_all"])
        argv += added
    # The project chooses the implementer's model and effort in its config; the
    # adapter says how to spell them. Nothing is appended for an adapter that
    # has no such option.
    opts = adapter.get("options") or {}
    for key in ("model", "effort"):
        val = impl.get(key)
        if val and key in opts and not any(str(a).startswith(f"--{key}") for a in argv):
            if key == "effort" and opts.get("effort_values") and val not in opts["effort_values"]:
                print(f"effort {val!r} not in {opts['effort_values']}; ignored")
                continue
            argv += [x.replace("{" + key + "}", str(val)) for x in opts[key]]

    from . import features as F
    if not F.enabled(cfg, "nudge"):
        print(f"idle {int(age)}s · {', '.join(reasons)} · nudge feature off; not starting a turn")
        return 0
    if fe:
        # A person is in these files right now. Say so in the prompt; the
        # implementer keeps away from them for this turn.
        argv = [a.replace(prompt, prompt + " İnsan şu dosyaları düzenliyor, bu turda dokunma: "
                          + ", ".join(fe[:8])) if a == prompt else a for a in argv]
    print(f"idle {int(age)}s · {', '.join(reasons)} · nudging")
    if args.dry_run:
        print("DRY RUN:", " ".join(argv[:4]), "…")
        return 0

    # Never discard the child's output. A nudge that dies on an expired login or
    # an exhausted plan looks exactly like an agent that ignored us, and the
    # difference is the only thing worth knowing at that moment.
    key = A.project_key(root)
    log_path = os.path.join(STATE_DIR, f"nudge-{key}.log")
    os.makedirs(STATE_DIR, exist_ok=True)
    env = dict(os.environ, PATH=search, AO_ROLE="implementer")
    if added:
        # Recorded with its reason whenever it changes, never appended silently (#69).
        try:
            A.record_actor_flags(root, "implementer", added,
                                 "the adapter's resume argv carries no tool scope, so ao appends its trust_all")
        except Exception as exc:
            print(f"could not record the flags added to the implementer: {exc}")
    held = A.hold_state(root)
    if held:
        print(f"held by {held.get('by')} since this cycle began; not nudging")
        return 0
    with open(log_path, "a", encoding=UTF8) as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} nudge"
                  f"{' (added: ' + ' '.join(added) + ')' if added else ''} ===\n")
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

    nudged_fp = A.work_fingerprint(root)
    try:
        child_start = A._process_start(proc.pid, refresh=True)
    except Exception:
        child_start = None
    st.update(attempts=st.get("attempts", 0) + 1, last_nudge=time.time(),
              last_size=size, child_pid=proc.pid, child_start=child_start,
              last_fingerprint=nudged_fp,
              nudge_size=size, nudge_fingerprint=nudged_fp,
              nudge_inputs=A.nudge_inputs(root, cfg))
    if early not in (None, 0):
        tail = ""
        try:
            with open(log_path, encoding=UTF8) as fh:
                tail = A.redact(" ".join(fh.read().strip().split("\n")[-3:])[-300:])
        except OSError:
            pass
        st.pop("child_pid", None)                 # it is gone; do not guard on a dead pid
        st["last_error"] = {"at": time.time(), "code": early, "tail": tail}
        notify(f"{project}: nudge failed", f"exit {early}: {tail[-120:] or 'see nudge log'}", root)
        print(f"nudge failed (exit {early}): {tail}")
    else:
        st.pop("last_error", None)
    save_state(root, st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
