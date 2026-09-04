#!/usr/bin/env python3
"""ao telegram — a second keyboard for the mailbox, and a phone that says what happened.

Two directions, and the inbound one is the point. When the architect's quota runs
out everything stops, twice in one day here; if a human can write a decision from
a phone at that moment, it does not stop. The architect role is already file-driven
— a decision is a file in `agent-mail/` — so this adds no new source of truth. It
is a keyboard for the mailbox, exactly as the MCP tools are.

Outbound carries what a human can act on *and* what is being done about the rest,
because "an anomaly was detected" without "and the architect is handling it" is the
half that makes someone check manually.

Everything a human sends is treated as urgent. A person who reaches for their
phone to type a decision has already decided it matters, and second-guessing that
with a marker they must remember is how a channel gets ignored.

Standard library only. Long polling — a webhook needs a public endpoint, and
nothing else here does.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ao import lib as A  # noqa: E402

CONF = os.path.join(A.HOME, ".ao", "telegram.json")
API = "https://api.telegram.org/bot{token}/{method}"


def config():
    """Token and allowed chats. A file with 0600, never the repository.

    A bot token is a credential that can instruct the implementer, so it lives
    where credentials live and not where code lives. The allowlist is not
    optional: an inbound channel without one is an authority surface open to
    whoever finds the bot.
    """
    if not os.path.exists(CONF):
        return None
    try:
        c = json.load(open(CONF))
    except Exception:
        return None
    if not c.get("token") or not c.get("chats"):
        return None
    c["chats"] = [str(x) for x in c["chats"]]
    return c


def api(cfg, method, **params):
    url = API.format(token=cfg["token"], method=method)
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
         for k, v in params.items() if v is not None}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data),
                                    timeout=(params.get("timeout") or 10) + 15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "body": e.read()[:200].decode(errors="replace")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def send(text, root=None, keyboard=None):
    """Send to every allowed chat. Returns how many got it."""
    cfg = config()
    if not cfg:
        return 0
    n = 0
    for chat in cfg["chats"]:
        r = api(cfg, "sendMessage", chat_id=chat, text=text[:4000],
                parse_mode="Markdown", disable_web_page_preview="true",
                reply_markup={"inline_keyboard": keyboard} if keyboard else None)
        if r.get("ok"):
            n += 1
        elif root:
            A.record_notice(root, "telegram send failed", str(r)[:200], sent=False,
                            key="telegram-error")
    return n


def _offset_path():
    return os.path.join(A.HOME, ".ao", "telegram-offset")


def poll(root, cfg_project, seconds=25):
    """Fetch new messages and turn each into an urgent architect message.

    Delivery-by-file, like everything else: the message lands in `agent-mail/`
    where the implementer already looks, rather than in a queue only this process
    knows about.
    """
    cfg = config()
    if not cfg:
        return {"error": f"no config at {CONF}"}
    try:
        offset = int(open(_offset_path()).read().strip())
    except Exception:
        offset = 0
    r = api(cfg, "getUpdates", offset=offset or None, timeout=seconds)
    if not r.get("ok"):
        return {"error": r.get("error") or r.get("description") or "getUpdates failed"}

    box = cfg_project.get("mailbox", "agent-mail")
    os.makedirs(os.path.join(root, box), exist_ok=True)
    written, ignored, last = [], 0, offset
    for u in r.get("result", []):
        last = max(last, u.get("update_id", 0) + 1)
        m = u.get("message") or u.get("channel_post") or {}
        text = (m.get("text") or "").strip()
        chat = str((m.get("chat") or {}).get("id", ""))
        if not text:
            continue
        if chat not in cfg["chats"]:
            ignored += 1                    # not on the allowlist: it never happened
            continue
        if text.startswith("/"):
            written.append(_command(root, cfg_project, text, chat))
            continue
        # Everything a person types is urgent. They reached for a phone to say it.
        slug = "".join(c if c.isalnum() else "-" for c in text.lower())[:40].strip("-")
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-fable-to-kiro-ACIL-{slug or 'mesaj'}.md"
        who = (m.get("from") or {}).get("username") or chat
        with open(os.path.join(root, box, name), "w") as fh:
            fh.write(f"# {text.splitlines()[0][:120]}\n\n## ACİL\n\n{text}\n\n"
                     f"---\n_Telegram, {who}, {time.strftime('%Y-%m-%d %H:%M')}_\n")
        written.append(name)
        send(f"✅ Kaydedildi: `{name}`\n\nUygulayıcıya `ao lock`, `ao verify` ve "
             f"`ao commit-ok` üzerinden ulaşacak; onaylamadan commit edemez.", root)
    if last != offset:
        open(_offset_path(), "w").write(str(last))
    return {"written": written, "ignored_unauthorised": ignored}


def _command(root, cfg_project, text, chat):
    """A few read-only commands, so the phone can answer 'what is happening'."""
    import subprocess
    cmd = text.split()[0].lstrip("/").split("@")[0]
    exe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "..", "bin", "ao")
    exe = os.path.abspath(exe) if os.path.exists(exe) else "ao"
    allowed = {"status": ["status"], "board": ["board"], "credits": ["credits"],
               "notices": ["notices", "-n", "8"], "fleet": ["fleet"]}
    if cmd not in allowed:
        send("Komutlar: /status /board /credits /notices /fleet\n"
             "Komut olmayan her mesaj acil karar olarak kutuya yazılır.", root)
        return f"/{cmd} (unknown)"
    try:
        out = subprocess.run([exe, "-C", root] + allowed[cmd], capture_output=True,
                             text=True, timeout=60).stdout
    except Exception as e:
        out = str(e)
    import re
    clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", out)[:3500]
    send(f"```\n{clean}\n```", root)
    return f"/{cmd}"


def main():
    root = os.getcwd()
    if "-C" in sys.argv:
        root = os.path.abspath(os.path.expanduser(sys.argv[sys.argv.index("-C") + 1]))
    cfg_project = A.load_config(root)
    once = "--once" in sys.argv
    while True:
        r = poll(root, cfg_project)
        if r.get("error"):
            print(r["error"], flush=True)
            if once:
                return 1
            time.sleep(30)
            continue
        if r.get("written"):
            print(f"{len(r['written'])} message(s): {', '.join(r['written'])}", flush=True)
        if r.get("ignored_unauthorised"):
            print(f"{r['ignored_unauthorised']} ignored (chat not on the allowlist)",
                  flush=True)
        if once:
            return 0


if __name__ == "__main__":
    sys.exit(main())
