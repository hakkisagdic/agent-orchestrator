"""E-mail: the red alarm.

Desktop notifications are swiped away and a Telegram bot is a thing to set up;
both were silent for eleven hours on 2026-09-05 while a queue sat empty and an
architect wake failed forty times — the human had not looked at either. Mail is
the channel people actually check when they wake up, so it is the top of the
ladder: an orange condition that stands for an hour becomes a red one, and red
is a mail.

It goes through a provider (#74). formsubmit.co needs no server: it relays a
JSON POST to an address the user has verified once, and the token in
`~/.ao/email.json` is the alias formsubmit hands back after that verification.
Any SMTP server works too, with the user's own account. Either way the settings
live with credentials, 0600, never in a repository.
"""
import json
import os
import time
import urllib.request

from . import lib as A
UTF8 = "utf-8"    # every text file ao writes or reads; Windows would otherwise use cp1252

CONF = os.path.join(A.HOME, ".ao", "email.json")
ENDPOINT = "https://formsubmit.co/ajax/{token}"
USER_AGENT = "curl/8.7.1"


def config():
    """The mail channel's settings from `~/.ao/email.json`, or None when it cannot send.

    0600, outside any repository. `provider` names one of PROVIDERS; a file written
    before there was a choice is formsubmit's.
    """
    if not os.path.exists(CONF):
        return None
    try:
        c = json.load(open(CONF, encoding=UTF8))
    except (OSError, ValueError):
        return None
    if not isinstance(c, dict):
        return None
    c.setdefault("name", "ao")
    c.setdefault("provider", "formsubmit")
    provider = PROVIDERS.get(c["provider"])
    if provider is None or any(not c.get(field) for field in provider.required):
        return None
    return c


def _write(c):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF, "w", encoding=UTF8) as fh:
        json.dump(c, fh, indent=2)
    os.chmod(CONF, 0o600)
    return c


def save(token, to=None, name="ao"):
    return _write({"provider": "formsubmit", "token": token.strip(), "to": to or "", "name": name})


def save_provider(provider, name="ao", **fields):
    """Save the settings of any provider; refuses one that is unknown or missing a field."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown mail provider {provider!r}; one of {', '.join(PROVIDERS)}")
    missing = [field for field in PROVIDERS[provider].required if not fields.get(field)]
    if missing:
        raise ValueError(f"{provider} needs {', '.join('--' + field for field in missing)}")
    return _write(dict({key: value for key, value in fields.items() if value not in (None, "")},
                       provider=provider, name=name))


class FormSubmit:
    """formsubmit.co relays a JSON POST to an address verified once; no server."""
    name = "formsubmit"
    required = ("token",)

    def send(self, c, subject, body, project, opener=None):
        payload = {"name": f"{c['name']} · {project}",
                   "email": c.get("to") or "noreply@ao.local",
                   "message": body,
                   "_subject": f"[ao/{project}] {subject}",
                   "_template": "box"}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(ENDPOINT.format(token=c["token"]), data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json",
                                              "Referer": "http://localhost:5173/",
                                              "Origin": "http://localhost:5173/",
                                              # Cloudflare in front of formsubmit rejects
                                              # Python's default agent string (error 1010).
                                              "User-Agent": USER_AGENT})
        resp = (opener or urllib.request.urlopen)(req, timeout=20)
        raw = resp.read().decode("utf-8", "replace") if hasattr(resp, "read") else str(resp)
        try:
            return str(json.loads(raw).get("success", "")).lower() in ("true", "1")
        except ValueError:
            return False


class Smtp:
    """Any mail server that takes SMTP: the user's own provider, with its own credentials.

    `tls` is "starttls" (the default, port 587), "implicit" (port 465) or "none". The
    password is read from the file, never from a repository.
    """
    name = "smtp"
    required = ("host", "to")

    def send(self, c, subject, body, project, opener=None):
        import smtplib
        import ssl
        from email.message import EmailMessage
        message = EmailMessage()
        message["Subject"] = f"[ao/{project}] {subject}"
        message["From"] = c.get("from") or c["to"]
        message["To"] = c["to"]
        message.set_content(body)
        tls = c.get("tls") or "starttls"
        port = int(c.get("port") or (465 if tls == "implicit" else 587))
        factory = opener or (smtplib.SMTP_SSL if tls == "implicit" else smtplib.SMTP)
        with factory(c["host"], port, timeout=20) as server:
            if tls == "starttls":
                server.starttls(context=ssl.create_default_context())
            if c.get("user"):
                server.login(c["user"], c.get("password") or "")
            server.send_message(message)
        return True


# The red channel is one of several providers, not one vendor in an endpoint string (#74).
PROVIDERS = {provider.name: provider for provider in (FormSubmit(), Smtp())}


def send(subject, body, root=None, opener=None):
    """Deliver one mail. Returns True on an accepted relay, False otherwise; never raises."""
    c = config()
    if not c:
        return False
    project = (A.project_key(root) if root else "ao")
    try:
        ok = bool(PROVIDERS[c["provider"]].send(c, subject, body, project, opener=opener))
    except Exception:
        ok = False
    if root:
        A.record_notice(root, f"mail: {subject}", body[:200], sent=ok, key="mail:" + subject[:40])
    return ok
