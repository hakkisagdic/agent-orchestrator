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

# Every decision line this module prints is also kept, so a cycle can be read
# back as a trace: what was measured, what was decided, in what order. The one
# question that cost this project the most — "why did it not act?" — used to be
# answerable only by reading a log of prose. `ao watchdog explain` runs a dry
# cycle and prints the trace; `ao watchdog trace` reads the recorded ones.
import builtins as _builtins
_TRACE = []
_FACTS = {}


def print(*args, **kw):
    line = " ".join(str(a) for a in args)
    _TRACE.append(line)
    _builtins.print(*args, **kw)


def cycles_path(root):
    key = os.path.basename(root.rstrip("/")) or "root"
    return os.path.join(STATE_DIR, f"cycles-{key}.jsonl")


def record_cycle(root, args):
    if getattr(args, "dry_run", False):
        return
    rec = {"at": int(time.time()), "verdict": _TRACE[-1] if _TRACE else "",
           "trace": list(_TRACE), "facts": dict(_FACTS)}
    try:
        p = cycles_path(root)
        os.makedirs(STATE_DIR, exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > 2_000_000:
            tail = open(p, errors="replace").read()[-1_000_000:]
            open(p, "w").write(tail[tail.find("\n") + 1:])
        with open(p, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def cycles(root, last=20):
    p = cycles_path(root)
    if not os.path.exists(p):
        return []
    rows = []
    for line in open(p, errors="replace"):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[-last:]
MAX_ATTEMPTS = 3

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


def notify(title, msg, root=None, key=None, window=1800, audience="human", level=None):
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
    # The ladder: this is an orange (a person must act). Standing an hour, it
    # rings red and goes to mail — the channel people open when they wake up.
    project = os.path.basename((root or "").rstrip("/")) or "ao"
    red_after = A.ALARM_RED_AFTER
    if root:
        try:
            red_after = int((A.load_config(root).get("alarms") or {}).get("red_after_minutes", 60)) * 60
        except Exception:
            pass
    ring, episode = A.alarm_touch(project, key, level or "orange", red_after=red_after, title=title)
    if ring == "red" and episode.get("red_due"):
        try:
            from . import email
            since = time.strftime("%d %b %H:%M", time.localtime(episode.get("first", time.time())))
            if email.send(f"{title}", f"{msg}\n\nDuruyor: {since}'den beri ({episode.get('count', 1)} kez). "
                          f"Proje: {root or '?'}\n`ao alarms` merdiveni, `ao status` durumu gösterir.", root):
                A.alarm_mailed(project, key)
                if root:
                    A.record_notice(root, title, msg, sent=True, key="mail:" + key)
        except Exception:
            pass
    if root and A.notice_recently_sent(root, key, window):
        A.record_notice(root, title, msg, sent=False, key=key)
        return False
    if root and storm(root):
        A.record_notice(root, title, msg, sent=False, key=key)
        if not A.notice_recently_sent(root, "storm", 3600):
            A.record_notice(root, f"{project}: alert storm", "12+ alerts in an hour; further ones "
                            "are recorded only — ao notices", sent=True, key="storm")
            subprocess.run(["osascript", "-e", f'display notification "12+ alerts in an hour; further '
                            f'ones recorded only (ao notices)" with title "{project}: alert storm"'],
                           capture_output=True)
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


def _announce_resolved(root, e):
    """Close the loop on an alarm: the same channels that heard it hear it end."""
    title = f"{e.get('project', '')}: resolved — {e.get('key', '')}"
    msg = (f"stood {e.get('age_s', 0) // 60}m, raised {e.get('count', 1)}×"
           + ("; was red" if e.get("red_sent") else ""))
    A.record_notice(root, title, msg, sent=True, key="resolved:" + e.get("key", ""))
    try:
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                       capture_output=True)
        from . import telegram
        telegram.send(f"✅ *{title}*\n{msg}", root)
        if e.get("red_sent"):
            from . import email
            email.send(title, msg, root)
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
        name = A.write_report(root, cfg, a["kind"], a["facts"], key=a.get("key"))
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
    # Same exclusion as the anomaly scanner: the watchdog's own reports are
    # addressed to the architect but are not themselves reports awaiting the
    # architect, and counting them here re-woke it endlessly on its own output.
    pending = [m for m in A.mailbox(root, cfg["mailbox"])
               if ("-to-fable-" in m or "-to-architect-" in m)
               and not (m.startswith("watchdog-to-") or "-watchdog-to-" in m)]
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
        if time.time() - st.get("last_handoff", 0) > 3600:
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
    if woke and A.architect_present(arch.get("cwd") or root):
        print("reports pending, but the architect is already at the keyboard")
        woke = False
    from . import features as F
    if woke and not F.enabled(cfg, "architect_wake"):
        print("reports pending; architect_wake feature is off — recorded and alarmed, not woken")
        notify(f"{os.path.basename(root)}: needs you", f"{len(stale)} report(s) waiting and architect wakes are off",
               root, key="reports-no-wake", window=3600, audience="human")
        woke = False
    if woke:
        left, reserve = A.window_headroom("claude")
        urgent = any(a.get("kind") == "decision-requested" for a in (found or []))
        if left is not None and left < reserve and not urgent:
            print(f"reports pending, but the machine's Claude window has {left}% left (< reserve {reserve}%); not waking")
            woke = False
    if woke:
        holder = A.architect_lock_holder(root)
        if holder:
            print(f"reports pending, but an architect turn holds the lock ({holder.get('who')}, pid {holder.get('pid')})")
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
        key = os.path.basename(root.rstrip("/")) or "root"
        log_path = os.path.join(STATE_DIR, f"escalate-{key}.log")
        # Read what the previous wake said before starting another. Same binary,
        # same error, less than six hours old: the human has been told, and a
        # retry is the forty-first identical failure.
        err = wake_error(log_path)
        if resolved and err:
            text, used, when, kind = err["text"], err["binary"], err["when"], err["kind"]
            prev = st.get("wake_error") or {}
            fresh = prev.get("text") != text or prev.get("binary") != used or prev.get("when") != when
            if fresh:
                st["wake_error"] = {"at": time.time(), "text": text, "binary": used,
                                    "when": when, "kind": kind}
                if kind == "quota":
                    # The architect is paused, not broken. Wait for the window, tell
                    # the human once (orange), and remember that the desktop app may
                    # resume the session itself when its auto-continue is on.
                    st["arch_quota_until"] = err.get("resets_at") or time.time() + 3600
                    A.deferred_append(root, "wake", reason="architect quota", until=st["arch_quota_until"])
                save_state(root, st)
                A.record_notice(root, f"{key}: architect wake failed", f"{kind}: {text} [{used}]",
                                True, key="architect-wake-failed")
                if kind == "quota":
                    until = time.strftime("%H:%M", time.localtime(st["arch_quota_until"]))
                    notify(f"{key}: mimar kotada", f"{text[:100]} — uyandırma {until}'e kadar "
                           f"bekletiliyor; Claude Desktop auto-continue açıksa oturum kendi "
                           f"devam eder", root, key="architect-quota", window=6 * 3600,
                           audience="human")
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
                # A dead session id: rediscover instead of resuming it again.
                arch["session"] = "auto"
                print(f"last wake resumed a dead session ({text[:60]}); rediscovering")
        if resolved and st.get("arch_quota_until", 0) > time.time():
            print(f"architect at quota until "
                  f"{time.strftime('%H:%M', time.localtime(st['arch_quota_until']))}; not waking")
            resolved = None
        if resolved:
            argv[0] = resolved
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(log_path, "a") as log:
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} escalate {resolved} {ver} ===\n")
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
    if rs.get("pending_review") and (not rs.get("until") or rs["until"] <= time.time()):
        reasons.append("reviewer window reopened — re-run the pending review")
    if A.product_dirty(root, cfg):
        reasons.append("uncommitted changes")
    revs = A.reviews(root, cfg["reviews"], limit=1)
    if revs and "APPROVED" not in revs[0][1].upper():
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


def parse_reset(text, now=None):
    """When does the quota come back? "resets 4:30am" / "resets in 4h 43m" / None."""
    now = now or time.time()
    m = re.search(r"resets?\s+in\s+((?:\d+\s*[hms]\s*)+)", text, re.I)
    if m:
        secs = 0
        for n, u in re.findall(r"(\d+)\s*([hms])", m.group(1), re.I):
            secs += int(n) * {"h": 3600, "m": 60, "s": 1}[u.lower()]
        return now + secs
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    lt = time.localtime(now)
    cand = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, 0, 0, 0, -1))
    if cand <= now:
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
        tail = open(log_path, errors="replace").read()[-20000:]
    except OSError:
        return None
    segs = re.split(r"^=== (\S+ \S+) (escalate|refill)(?: (.*?))? ===$", tail, flags=re.M)
    if len(segs) < 5:
        return None
    when, _, binary, body = segs[-4], segs[-3], segs[-2] or "", segs[-1]
    for kind, pat in WAKE_SIGNATURES:
        m = re.search(pat, body)
        if m:
            text = m.group(1).strip()[:300]
            return {"text": text, "binary": binary, "when": when, "kind": kind,
                    "resets_at": parse_reset(body) if kind == "quota" else None}
    return None


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
    p.add_argument("--prompt", default=NUDGE_PROMPT)
    args = p.parse_args()
    return run(args)


def run(args):
    """One cycle, traced and — unless dry — recorded."""
    _TRACE.clear()
    _FACTS.clear()
    root = os.path.abspath(os.path.expanduser(args.root))
    try:
        return _cycle(args, root)
    finally:
        record_cycle(root, args)


def _cycle(args, root):
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
    A.heartbeat(root)                 # the only proof this watchdog is alive
    A.reconcile_mail_ledger(root, cfg)  # deleted mail becomes a consumed row
    A.record_progress(root, cfg)      # history of what moved, for the spin check
    project = os.path.basename(root.rstrip("/")) or "root"
    try:
        bd = A.board(root)
        _FACTS.update(idle_s=int(age), transcript_bytes=size,
                      inbox=len(A.implementer_inbox(root, cfg)),
                      standing_request=bool(A.waiting_on_architect(root, cfg)),
                      queued=len(bd["queued"]), running=len(bd["running"]), blocked=len(bd["blocked"]),
                      hold=bool(A.hold_state(root)),
                      arch_quota_until=st.get("arch_quota_until") or None,
                      arch_present=A.architect_present((cfg.get("architect") or {}).get("cwd") or root),
                      last_wake_error=(st.get("wake_error") or {}).get("kind"))
    except Exception as exc:                                  # facts must never stop a cycle
        _FACTS["facts_error"] = str(exc)[:120]
    # Alarm hygiene, every cycle: close episodes that went quiet (and say so, once —
    # an alert with no "over" teaches people to keep worrying), watch the sibling
    # watchdogs' heartbeats (a dead watchdog cannot report itself), and a hold that
    # has stood four hours is a person who forgot, which is a red.
    for e in A.expire_alarms(project):
        _announce_resolved(root, e)
    # The dead man's switch: an external service that alarms when this stops.
    _FACTS["ping"] = A.ping(root)
    # Credits: one sample per half hour, and a red alarm when the burn rate says
    # the plan runs out before it resets — the day everything stops.
    if time.time() - st.get("last_credit_sample", 0) > 1800:
        try:
            acct = A.kiro_account_usage() if (adapter.get("billing") or {}).get("api") else None
        except Exception:
            acct = None
        if acct and acct.get("limit"):
            A.record_credit_sample(root, acct.get("used", 0), acct["limit"], acct.get("reset_at"))
            st["last_credit_sample"] = time.time()
            save_state(root, st)
            br = A.burn_rate(root)
            if br and br["before_reset"]:
                notify(f"{project}: credits run out {time.strftime('%d %b', time.localtime(br['exhausts_at']))}",
                       f"{br['used']:.0f}/{br['limit']:.0f} at {br['per_day']:.0f}/day; the reset is later. "
                       f"New account (keyflip) or `ao features off …`", root, key="credits-exhaust",
                       window=6 * 3600, audience="human", level="red")
    # Only meaningful when no implementer turn is running: a sub-agent's writes
    # do not appear as the parent's tool calls and would read as a stranger's.
    fe = [] if A.agent_pids(root, adapter) else A.foreign_edits(root, cfg)
    _FACTS["foreign_edits"] = fe
    for sib, age_s in A.stale_siblings(root).items():
        notify(f"{sib}: watchdog silent", f"no heartbeat for {age_s // 60}m — its watchdog is not "
               f"running; launchctl / ao watchdog status", root, key=f"watchdog-dead:{sib}",
               window=3600, audience="human")
    hs = A.hold_state(root)
    if hs and time.time() - int(hs.get("at") or time.time()) > 4 * 3600:
        notify(f"{project}: hold standing {int((time.time() - hs['at']) / 3600)}h",
               f"set by {hs.get('by', '?')}: {hs.get('reason', '')} — ao hold release when done",
               root, key="hold-standing", window=6 * 3600, audience="human", level="red")

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
        A.record_notice(root, "orphans cleared", f"{len(dead)} leftover process(es): {dead}", False, key="orphans")
        if not args.dry_run:
            A.sweep_orphans(dead)
    running = [p for p in A.agent_pids(root, adapter) if p not in set(dead)]
    _FACTS.update(writers=len(A.process_trees(running)) if running else 0, orphans=len(dead))
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
            A.kill_turn(pid, signal.SIGKILL)

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
    reasons = open_work(cfg, root)
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
            notify(f"{os.path.basename(root)}: needs you", f"queue has {depth} item(s) and refill wakes are off — add slices to .ao/backlog.md",
                   root, key="queue-empty-no-refill", window=3600, audience="human")
            return 0
        if arch.get("argv") and depth < threshold and A.architect_lock_holder(root):
            print("queue low, but an architect turn holds the lock")
            return 0
        if arch.get("argv") and depth < threshold:
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
            key = os.path.basename(root.rstrip("/")) or "root"
            log_path = os.path.join(STATE_DIR, f"refill-{key}.log")
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(log_path, "a") as log:
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} refill {resolved} {ver} ===\n")
                log.flush()
                proc = subprocess.Popen(argv, cwd=root, env=dict(os.environ, PATH=search),
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

    # 4 — no quota. Delivery still works: reports are written above this gate and
    # the transport is HTTP, so a pending question reaches a phone even now. What
    # stops is deciding — so hand the state to whoever can.
    if not quota_ok(adapter):
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
        notify("Voltrai: out of quota",
               "provider window exhausted; handoff note sent", root)
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
    # Only widen to trust-all when the adapter's resume carries no allowlist of
    # its own. Appending --dangerously-skip-permissions on top of --allowedTools
    # would silently override the narrower grant.
    scoped = any(a in ("--allowedTools", "--allowed-tools", "--trust-all-tools") for a in argv)
    if not scoped and "trust_all" in (adapter.get("options") or {}):
        argv += adapter["options"]["trust_all"]
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
        argv = [a.replace(args.prompt, args.prompt + " İnsan şu dosyaları düzenliyor, bu turda dokunma: "
                          + ", ".join(fe[:8])) if a == args.prompt else a for a in argv]
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
              last_fingerprint=A.work_fingerprint(root))
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
