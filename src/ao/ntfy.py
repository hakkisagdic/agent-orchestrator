"""ntfy: an outbound phone channel beside Telegram.

For a person who would rather use ntfy (open source and self-hostable, with its
own phone app) than a Telegram bot. Alerts go out; nothing comes back in.

Each alert is one HTTP POST to the topic URL with Python's urllib, carrying the
same text Telegram gets, a `Priority` header mapped from the alarm level (`urgent`
for red, `high` for orange, `default` for the rest), and `Authorization: Bearer
<token>` when an access token was set. Settings live in `~/.ao/ntfy.json`, mode
0600 from creation, never in a repository.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from . import lib as A, outbound, storage
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

CONF = os.path.join(A.HOME, ".ao", "ntfy.json")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
# ntfy priority names (docs.ntfy.sh/publish/#message-priority): 5 is urgent/max, 4 is high, 3 is default.
PRIORITIES = {"red": "urgent", "orange": "high"}


def priority(level=None):
    """The ntfy `Priority` header value for an alarm level: red highest, orange high, the rest default."""
    return PRIORITIES.get(level, "default")


def _parse_topic_url(url):
    """Validate and split a topic URL; returns `(clean_url, hostname)` or raises `ValueError`."""
    if not isinstance(url, str):
        raise ValueError("a topic URL is required")
    clean = url.strip()
    if not clean or any(ch.isspace() or not ch.isprintable() for ch in clean):
        raise ValueError("a topic URL cannot contain spaces or control characters")
    try:
        parts = urllib.parse.urlsplit(clean)
        _ = parts.port
    except ValueError as exc:
        raise ValueError("invalid topic URL") from exc
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("a topic URL takes only the server and topic path; pass a token with --token-env")
    hostname = (parts.hostname or "").lower()
    if not hostname or not parts.path.strip("/"):
        raise ValueError("a topic URL needs a host and a topic path, such as https://ntfy.sh/<topic>")
    if parts.scheme != "https" and not (parts.scheme == "http" and hostname in LOOPBACK):
        raise ValueError("a topic URL must use https:// (or http:// on 127.0.0.1)")
    return clean, hostname


def _valid_token(token):
    return isinstance(token, str) and bool(token) and not any(ch.isspace() or not ch.isprintable() for ch in token)


def config():
    """The ntfy channel's settings from `~/.ao/ntfy.json`, or None when it cannot send.

    0600, outside any repository.
    """
    if not os.path.exists(CONF):
        return None
    try:
        with open(CONF, encoding=UTF8) as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(c, dict):
        return None
    try:
        clean_url, _ = _parse_topic_url(c.get("topic_url"))
    except ValueError:
        return None
    token = c.get("token")
    if token is not None and not _valid_token(token):
        return None
    out = {"topic_url": clean_url}
    if token:
        out["token"] = token
    return out


def host(c=None):
    """The ntfy server's host, without the topic path or token."""
    c = config() if c is None else c
    if not c or not isinstance(c.get("topic_url"), str):
        return None
    try:
        return (urllib.parse.urlsplit(c["topic_url"]).hostname or "").lower() or None
    except ValueError:
        return None


def save(topic_url, token=None):
    """Write the topic URL and optional access token to `CONF`, mode 0600 from its creation."""
    clean_url, _ = _parse_topic_url(topic_url)
    data = {"topic_url": clean_url}
    if token is not None:
        clean_token = str(token).strip() if isinstance(token, str) else ""
        if not _valid_token(clean_token):
            raise ValueError("an access token cannot be empty or contain spaces or control characters")
        data["token"] = clean_token
    storage.replace_file_durably(CONF, (json.dumps(data, indent=2) + "\n").encode(UTF8), mode=0o600)
    return config()


def _scrub(text, c):
    """Remove the access token and the secret topic path from an error string before recording it."""
    out = str(text)
    if c.get("token"):
        out = out.replace(c["token"], "<token>")
    if c.get("topic_url"):
        parts = urllib.parse.urlsplit(c["topic_url"])
        masked = f"{parts.scheme}://{parts.hostname or '?'}/<topic>"
        out = out.replace(c["topic_url"], masked)
        topic = parts.path.strip("/")
        if topic:
            out = out.replace(topic, "<topic>")
    return out


def send(text, root=None, level=None):
    """Publish one alert to the configured ntfy topic. Returns True on HTTP 2xx, False otherwise; never raises."""
    c = config()
    if not c:
        return False
    headers = {"Priority": priority(level)}
    if c.get("token"):
        headers["Authorization"] = f"Bearer {c['token']}"
    req = urllib.request.Request(
        c["topic_url"],
        data=str(text)[:4000].encode(UTF8),
        headers=headers,
        method="POST",
    )
    server_host = host(c)
    open_url = (outbound.opener(urllib.request.ProxyHandler({}))
                if server_host in LOOPBACK else outbound.OPENER).open
    try:
        with open_url(req, timeout=15) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            resp.read(200)
            if 200 <= int(status) < 300:
                return True
            why = f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(200).decode(UTF8, errors="replace").strip()
        except Exception:
            body = ""
        why = f"HTTP {exc.code}" + (f": {body}" if body else "")
    except Exception as exc:
        why = str(exc)
    if root:
        A.record_notice(root, "ntfy send failed", _scrub(why, c)[:200], sent=False, key="ntfy-error")
    return False
