"""A scenario runs the whole watchdog cycle against a fabricated world.

The unit tests prove each measurement; this proves the *decision*. Every fault
in docs/watchdog.md is a world the cycle once misread — orphans that looked
like writers, a report that looked like unread mail, a quota error that looked
like a verdict. Each becomes a scenario here: build the world, run one dry
cycle, assert the last decision line. New faults get a scenario before a fix.
"""
import json
import os
import time
from types import SimpleNamespace

from ao import lib as A
from ao import watchdog as W


class World:
    def __init__(self, project, monkeypatch, tmp_path):
        self.cfg = project
        self.root = project["root"]
        self.mp = monkeypatch
        self.tmp = tmp_path
        self.notices = []
        self.procs = {}                 # pid -> {"argv", "cwd", "ppid", "pgid", "tty", "headless"}
        self.transcript = tmp_path / "transcript.jsonl"
        self.transcript.write_text(json.dumps({"payload": {"type": "turn_end"}}) + "\n")
        self.transcript_age(0)
        self.turn_ended = False
        self.arch_present = False
        self.quota = True
        self._patch()

    # ---- knobs
    def transcript_age(self, seconds):
        t = time.time() - seconds
        os.utime(self.transcript, (t, t))
        return self

    def process(self, pid, argv, cwd=None, ppid=1, pgid=None, tty=None, headless=True):
        self.procs[pid] = {"argv": argv, "cwd": cwd or self.root, "ppid": ppid,
                           "pgid": pid if pgid is None else pgid, "tty": tty, "headless": headless}
        return self

    def mail(self, name, body):
        open(os.path.join(self.root, self.cfg["mailbox"], name), "w").write(body)
        return self

    def board(self, section, line):
        p = os.path.join(self.root, ".ao", "board.md")
        s = open(p).read()
        open(p, "w").write(s.replace(f"## {section}\n", f"## {section}\n{line}\n", 1))
        return self

    def review(self, verdict, age=0):
        d = os.path.join(self.root, self.cfg["reviews"])
        p = os.path.join(d, f"r-{len(os.listdir(d)):03d}.md")
        open(p, "w").write(f"# review\n\nVERDICT: {verdict}\n")
        t = time.time() - age
        os.utime(p, (t, t))
        return self

    # ---- the fakes
    def _patch(self):
        mp, w = self.mp, self
        from ao import procs
        mp.setattr(A, "session_paths", lambda cfg: (str(w.transcript), None))
        mp.setattr(procs, "all_pids", lambda: list(w.procs))
        mp.setattr(procs, "argv", lambda pid: w.procs.get(pid, {}).get("argv"))
        mp.setattr(procs, "cwd", lambda pid: w.procs.get(pid, {}).get("cwd"))
        mp.setattr(A, "_proc_table", lambda: {p: (v["ppid"], v["pgid"], "??" if v["tty"] is None else str(v["tty"]))
                                              for p, v in w.procs.items()})
        mp.setattr(A, "_is_headless", lambda pid: w.procs.get(pid, {}).get("headless", False))
        mp.setattr(A, "_pid_alive", lambda pid: pid in w.procs)
        mp.setattr(A, "_executable", lambda t: t.startswith("/agents/"))
        mp.setattr(W.shutil, "which", lambda name, path=None, mode=None: f"/agents/{os.path.basename(name)}"
                   if os.path.basename(name) in ("kiro-cli", "claude", "ao") else None)
        mp.setattr(A, "resolve_binary", lambda name, path=None: (f"/agents/{os.path.basename(name)}", "2.1.261"))
        w.spawned = []

        class _Proc:
            pid = 99999
        mp.setattr(W.subprocess, "Popen", lambda argv, **kw: w.spawned.append(argv) or _Proc())
        mp.setattr(A, "turn_ended", lambda cfg: w.turn_ended)
        mp.setattr(A, "architect_present", lambda cwd, idle_seconds=600: w.arch_present)
        mp.setattr(A, "discover_architect", lambda cwd: {"session": "sess-1", "age": 9999})
        mp.setattr(A, "provider_window", lambda name="claude": None)
        mp.setattr(A, "kiro_account_usage", lambda timeout=20: None)
        mp.setattr(A, "ping", lambda root, opener=None: None)
        mp.setattr(A, "quota", lambda adapter, ttl=300: [])
        mp.setattr(A, "helper_pids", lambda root: set())
        mp.setattr(W, "quota_ok", lambda adapter: w.quota)
        mp.setattr(W, "notify", lambda title, msg, root=None, key=None, window=1800, audience="human", level=None:
                   w.notices.append((title, msg, audience, level)) or True)
        mp.setattr(W, "wake_error", lambda log_path: None)
        # reaping in a scenario removes the processes it targets
        def kill_turn(pid, sig):
            for p in [q for q, v in w.procs.items() if q == pid or v["pgid"] == pid]:
                w.procs.pop(p, None)
        mp.setattr(A, "kill_turn", kill_turn)
        mp.setattr(A, "sweep_orphans", lambda pids, grace=3.0: [w.procs.pop(p, None) for p in list(pids)])

    # ---- run
    def cycle(self, dry_run=True, idle_minutes=6.0):
        ns = SimpleNamespace(root=self.root, idle_minutes=idle_minutes, dry_run=dry_run, prompt=W.NUDGE_PROMPT)
        W.run(ns)
        return list(W._TRACE)

    @property
    def verdict(self):
        return W._TRACE[-1] if W._TRACE else ""
