"""E-mail: the red alarm.

Desktop notifications are swiped away and a Telegram bot is a thing to set up;
both were silent for eleven hours on 2026-09-05 while a queue sat empty and an
architect wake failed forty times — the human had not looked at either. Mail is
the channel people actually check when they wake up, so it is the top of the
ladder: an orange condition that stands for an hour becomes a red one, and red
is a mail.

No server. formsubmit.co relays a JSON POST to an address the user has verified
once; the token in `~/.ao/email.json` is the alias formsubmit hands back after
that verification. It lives with credentials, never in a repository.
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
    """{"token": ..., "to": ..., "name": ...} or None. 0600, outside any repo."""
    if not os.path.exists(CONF):
        return None
    try:
        c = json.load(open(CONF, encoding=UTF8))
    except (OSError, ValueError):
        return None
    if not c.get("token"):
        return None
    c.setdefault("name", "ao")
    c.setdefault("provider", "formsubmit")
    return c


def save(token, to=None, name="ao"):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    c = {"provider": "formsubmit", "token": token.strip(), "to": to or "", "name": name}
    with open(CONF, "w", encoding=UTF8) as fh:
        json.dump(c, fh, indent=2)
    os.chmod(CONF, 0o600)
    return c


def send(subject, body, root=None, opener=None):
    """Deliver one mail. Returns True on an accepted relay, False otherwise; never raises."""
    c = config()
    if not c:
        return False
    project = os.path.basename((root or "").rstrip("/")) or "ao"
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
    try:
        resp = (opener or urllib.request.urlopen)(req, timeout=20)
        raw = resp.read().decode("utf-8", "replace") if hasattr(resp, "read") else str(resp)
        try:
            ok = str(json.loads(raw).get("success", "")).lower() in ("true", "1")
        except ValueError:
            ok = False
    except Exception:
        ok = False
    if root:
        A.record_notice(root, f"mail: {subject}", body[:200], sent=ok, key="mail:" + subject[:40])
    return ok


SETUP = """\
E-posta kanalı (kırmızı alarm) — sunucu gerekmez, formsubmit.co üzerinden gider.

1. Bir kez doğrulama: aşağıdaki komutu KENDİ adresinle çalıştır; formsubmit sana
   bir aktivasyon e-postası gönderir, içindeki bağlantıya tıkla.
     curl -s -X POST -H "Content-Type: application/json" -H "Accept: application/json" \\
       -d '{{"message":"ao aktivasyon"}}' https://formsubmit.co/ajax/SENIN@ADRESIN
2. Aktivasyondan sonra formsubmit sana adresini gizleyen rastgele bir token verir
   (https://formsubmit.co/ajax/<token>). Adresin kendisi de token olarak çalışır.
3. Kaydet:  ao email setup --token <token> --to SENIN@ADRESIN
4. Dene:    ao email test        → gelen kutunda "[ao/<proje>] test" görmelisin.

Token {conf} dosyasında 0600 ile durur; hiçbir depoya yazılmaz. Kırmızı alarmlar
(bir saatten uzun süren turuncu durumlar, tükenmiş kota, başarısız mimar uyandırma)
bu adrese gider; `ao alarms` merdiveni gösterir.
"""
