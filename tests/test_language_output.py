"""What a person reads is in the project's language, and what a person types is read in either (LANGUAGE-OUTPUT).

LANGUAGE-FILES gave ao its `language` setting and converted what ao writes into a repository and
parses back. What a person reads stayed Turkish in every project: the digest and the handoff note, the
phone's messages and its replies, the e-mail and Telegram setup, the notes `ao decide` and a released
hold leave, a decision's free-text option, and the watchdog's alarms, its red mail and its lines to the
phone. Each now comes from src/ao/language.py: English by default and, with `language: tr`, the Turkish
ao wrote before, byte for byte - every Turkish text below is copied from what ao printed, sent and wrote
before the change, colour codes where they stood. The setup of a channel the whole machine shares
follows the machine's choice.

What ao parses back is read in both languages: the heading a handoff note goes to the phone up to, and a
decision's free-text option. What a person types or taps on the phone is read the same in either. An
alarm's key is never a text, so a project that changes its language rings no standing alarm again. And
`ao init --language` chooses before init writes a file, where `ao config set` needs the config init writes.
"""
import json
import os
import subprocess
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from ao import cli, email, language, lib as A, mcp, telegram, watchdog as W
from ao import settings as S
from tests import conftest
from tests import test_noise_repeats as noise
from tests.test_language_files import INIT_FILES, STEERING, TURKISH, _choose, _plain, _read
from tests.test_profiles import _init_args

LANGUAGES = ("en", "tr")
STAMP = datetime(2026, 9, 17, 16, 0)


class _Now(datetime):
    """The clock `ao handoff` stamps its note with, stopped."""

    @classmethod
    def now(cls, tz=None):
        return STAMP


def _machine(chosen):
    with open(S.machine_path(), "w", encoding="utf-8") as fh:
        json.dump({"language": chosen}, fh)


def _colour(monkeypatch):
    """Colour on, as a terminal draws it, so the Turkish is compared with its escape codes where they stood."""
    monkeypatch.setattr(A, "colour_enabled", lambda stream=None: True)
    return A.C


# ---- the e-mail and the phone channels: set up for the machine, tested for the project -------------

EMAIL_SETUP_TR = """\
E-posta kanalı (kırmızı alarm) — sunucu gerekmez, formsubmit.co üzerinden gider.

1. Bir kez doğrulama: aşağıdaki komutu KENDİ adresinle çalıştır; formsubmit sana
   bir aktivasyon e-postası gönderir, içindeki bağlantıya tıkla.
     curl -s -X POST -H "Content-Type: application/json" -H "Accept: application/json" \\
       -d '{{"message":"ao aktivasyon"}}' https://formsubmit.co/ajax/SENIN@ADRESIN
2. Aktivasyondan sonra formsubmit sana adresini gizleyen rastgele bir token verir
   (https://formsubmit.co/ajax/<token>). Adresin kendisi de token olarak çalışır.
3. Kaydet:  ao email setup --token <token> --to SENIN@ADRESIN
4. Dene:    ao email test        → gelen kutunda "[ao/<proje>] test" görmelisin.

Kendi posta sunucun varsa formsubmit yerine SMTP (parola komut satırına yazılmaz,
bir ortam değişkeninden bir kez okunur):
     AO_SMTP_PASSWORD=… ao email setup --provider smtp --host smtp.ornek.com \\
       --user SEN@ORNEK.COM --password-env AO_SMTP_PASSWORD --to SEN@ORNEK.COM

Token {conf} dosyasında 0600 ile durur; hiçbir depoya yazılmaz. Kırmızı alarmlar
(bir saatten uzun süren turuncu durumlar, tükenmiş kota, başarısız mimar uyandırma)
bu adrese gider; `ao alarms` merdiveni gösterir.
"""
EMAIL_TEST = {
    "en": "ao's e-mail channel works. Project: {root}\nRed alarms come here: orange conditions standing longer than "
          "an hour, an exhausted quota, a failed architect wake.",
    "tr": "ao e-posta kanalı çalışıyor. Proje: {root}\nKırmızı alarmlar buraya gelir: bir saatten uzun süren "
          "turuncu durumlar, tükenmiş kota, başarısız mimar uyandırma.",
}


def test_the_email_setup_follows_the_machines_language_and_the_test_mail_the_projects(project, monkeypatch, capsys):
    setup = SimpleNamespace(action="setup", provider=None, token=None, to=None)
    turkish_project = _choose(project, "tr")

    assert cli.cmd_email(turkish_project, setup) == 0                # a project's choice is not the machine's
    english = capsys.readouterr().out
    assert english.startswith("The e-mail channel (the red alarm) — no server needed, it goes through formsubmit.co.\n")
    assert "3. Save:    ao email setup --token <token> --to YOUR@ADDRESS\n" in english
    assert f"The token stays in {email.CONF}, mode 0600" in english
    assert not TURKISH.search(english.replace(email.CONF, ""))
    _machine("tr")
    assert cli.cmd_email(turkish_project, setup) == 0
    assert capsys.readouterr().out == EMAIL_SETUP_TR.format(conf=email.CONF) + "\n"

    sent = []
    monkeypatch.setattr(email, "config", lambda: {"provider": "formsubmit", "token": "t", "to": "", "name": "ao"})
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None: sent.append((subject, body)) or True)
    for chosen in LANGUAGES:
        assert cli.cmd_email(_choose(project, chosen), SimpleNamespace(action="test", provider=None)) == 0
    assert sent == [("test", EMAIL_TEST[chosen].format(root=project["root"])) for chosen in LANGUAGES]


TELEGRAM_TEST = {
    "en": "*proj* — connection test.\n\nEvery message you write in this chat lands in the mailbox as an urgent "
          "decision, and the implementer cannot commit until it acknowledges it.\n\n"
          "Commands: /status /board /credits /notices /fleet",
    "tr": "*proj* — bağlantı testi.\n\nBu sohbete yazdığın her mesaj acil karar olarak kutuya düşer ve "
          "uygulayıcı onaylamadan commit edemez.\n\nKomutlar: /status /board /credits /notices /fleet",
}


def test_the_telegram_setup_follows_the_machines_language_and_its_test_message_the_projects(project, monkeypatch,
                                                                                             capsys):
    C = _colour(monkeypatch)
    monkeypatch.setattr(telegram, "config", lambda: None)
    setup = SimpleNamespace(action="setup", once=False)
    turkish_project = _choose(project, "tr")

    assert cli.cmd_telegram(turkish_project, setup) == 0
    lines = capsys.readouterr().out.split("\n")
    assert f"{C['b']}2.{C['reset']} Write the bot a message, then get your chat id:" in lines
    assert (f"{C['b']}3.{C['reset']} {C['b']}You{C['reset']} write the file — a bot token is a credential; it goes "
            f"into neither a repository nor a chat:") in lines
    _machine("tr")
    assert cli.cmd_telegram(turkish_project, setup) == 0
    lines = capsys.readouterr().out.split("\n")
    assert f"{C['b']}2.{C['reset']} Bota bir mesaj yaz, sonra chat id'ni al:" in lines
    assert (f"{C['b']}3.{C['reset']} Dosyayı {C['b']}sen{C['reset']} yaz — bir bot token'ı "
            f"kimlik bilgisidir; ne repoya ne bir sohbete girer:") in lines

    sent = []
    monkeypatch.setattr(telegram, "config", lambda: {"token": "t", "chats": ["42"]})
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: sent.append(text) or 1)
    for chosen in LANGUAGES:
        assert cli.cmd_telegram(_choose(project, chosen), SimpleNamespace(action="test", once=False)) == 0
    assert sent == [TELEGRAM_TEST[chosen] for chosen in LANGUAGES]


# ---- the phone: answered in the project's language, read the same in either -----------------------

REPLIES = {
    "en": {"unauthorised": "unauthorised", "recorded": "a) recorded", "gone": "decision not found",
           "unknown": "There is no decision `D-2`.",
           "commands": "Commands: /status /board /credits /notices /fleet /decisions\n\n"
                       "To answer a pending decision: tap a button, or type `D-123 b`.\n"
                       "Every message that is not a command is written to the mailbox as an urgent decision.",
           "saved": "✅ Saved: `{name}`\n\nIt reaches the implementer through `ao lock`, `ao verify` and "
                    "`ao commit-ok`; it cannot commit until it acknowledges it.",
           "free": "Other (free text)"},
    "tr": {"unauthorised": "yetkisiz", "recorded": "a) kaydedildi", "gone": "karar bulunamadı",
           "unknown": "`D-2` diye bir karar yok.",
           "commands": "Komutlar: /status /board /credits /notices /fleet /decisions\n\n"
                       "Bekleyen bir karara cevap: butona bas, ya da `D-123 b` yaz.\n"
                       "Komut olmayan her mesaj acil karar olarak kutuya yazılır.",
           "saved": "✅ Kaydedildi: `{name}`\n\nUygulayıcıya `ao lock`, `ao verify` ve "
                    "`ao commit-ok` üzerinden ulaşacak; onaylamadan commit edemez.",
           "free": "Başka (serbest metin)"},
}


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_phone_is_answered_in_the_projects_language_and_what_it_types_works_in_either(project, monkeypatch,
                                                                                         tmp_path, chosen):
    cfg = _choose(project, chosen)
    root = cfg["root"]
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(A, "recall", lambda *args, **kwargs: [])
    first = A.ask(root, "which store keeps the ledger?", ["sqlite", "files"], cfg=cfg)
    clock[0] += 1
    second = A.ask(root, "where do exports go?", ["s3", "disk"])       # labeled from the config on disk
    clock[0] += 1
    chat, stranger = {"id": 42}, {"id": 99}
    updates = [
        {"update_id": 1, "callback_query": {"id": "c1", "data": f"{first['id']}:a", "message": {"chat": stranger},
                                            "from": {"username": "eve"}}},
        {"update_id": 2, "callback_query": {"id": "c2", "data": f"{first['id']}:a", "message": {"chat": chat},
                                            "from": {"username": "alice"}}},
        {"update_id": 3, "callback_query": {"id": "c3", "data": "D-1:a", "message": {"chat": chat},
                                            "from": {"username": "alice"}}},
        {"update_id": 4, "message": {"text": "D-2 b", "chat": chat, "from": {"username": "alice"}}},
        {"update_id": 5, "message": {"text": f"{second['id']} x a bucket per tenant", "chat": chat,
                                     "from": {"username": "alice"}}},
        {"update_id": 6, "message": {"text": "/nonsense", "chat": chat, "from": {"username": "alice"}}},
        {"update_id": 7, "message": {"text": "/status", "chat": chat, "from": {"username": "alice"}}},
        {"update_id": 8, "message": {"text": "stop the deploy", "chat": chat, "from": {"username": "alice"}}},
    ]
    answered, sent, ran = [], [], []
    monkeypatch.setattr(telegram, "config", lambda: {"token": "t", "chats": ["42"]})
    monkeypatch.setattr(telegram, "api", lambda conf, method, **params: {"ok": True, "result": updates}
                        if method == "getUpdates" else answered.append(params.get("text")) or {"ok": True})
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: sent.append(text) or 1)
    monkeypatch.setattr(telegram, "_offset_path", lambda: str(tmp_path / "telegram-offset"))
    run = subprocess.run

    def command(argv, *args, **kwargs):
        if isinstance(argv, list) and argv and os.path.basename(str(argv[0])) == "ao":
            ran.append(argv[1:])
            return SimpleNamespace(stdout="working\n", stderr="", returncode=0)
        return run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", command)

    result = telegram.poll(root, cfg)

    said = REPLIES[chosen]
    name = result["written"][-1]
    assert [first["options"][-1]["label"], second["options"][-1]["label"]] == [said["free"], said["free"]]
    assert answered == [said["unauthorised"], said["recorded"], said["gone"]]
    assert sent == ["✅ *which store keeps the ledger?*\n→ sqlite", said["unknown"],
                    "✅ *where do exports go?*\n→ a bucket per tenant", said["commands"], "```\nworking\n\n```",
                    said["saved"].format(name=name)]
    assert result["written"][:4] == [f"{first['id']}=a", f"{second['id']}=x", "/nonsense (unknown)", "/status"]
    assert ran == [["-C", root, "status"]] and result["ignored_unauthorised"] == 1
    assert name.endswith(f"-human-to-kiro-{language.marker(cfg, 'urgent-kind')}-stop-the-deploy.md")
    assert {rec["id"]: rec["answer"] for rec in A.decisions(root)} == {first["id"]: "sqlite",
                                                                      second["id"]: "a bucket per tenant"}


BLOCKED_PHONE = {
    "en": "⛔ *queue empty*\n\nthe next slice\n\n"
          "_the implementer is stuck; a reply you write lands as an urgent decision_",
    "tr": "⛔ *queue empty*\n\nthe next slice\n\n_uygulayıcı takıldı; cevap yazarsan acil karar olarak düşer_",
}
DECISION_PHONE = {
    "en": ["❓ *which store keeps the ledger?*", "\n_the ledger grows_", "\nslice: `S3`",
           "\nbefore: acme-api question D-1 — answered: sqlite", "", "*a)* sqlite", "*b)* files",
           "*x)* Other (free text)",
           "\nAnswer: tap a button, or type `{id} <letter>`. For free text, `{id} x <your answer>`."],
    "tr": ["❓ *which store keeps the ledger?*", "\n_the ledger grows_", "\ndilim: `S3`",
           "\nönceden: acme-api question D-1 — answered: sqlite", "", "*a)* sqlite", "*b)* files",
           "*x)* Başka (serbest metin)",
           "\nCevap: butona bas, ya da `{id} <harf>` yaz. Serbest metin için `{id} x <cevabın>`."],
}


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_a_blocked_report_and_a_question_reach_the_phone_in_the_projects_language(project, monkeypatch, chosen):
    cfg = _choose(project, chosen)
    sent = []
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: sent.append((text, keyboard)) or 1)
    monkeypatch.setattr(A, "recall", lambda *args, **kwargs: [])

    mcp.call("ao_report", {"kind": "blocked", "summary": "queue empty", "needs": "the next slice"}, cfg, False)
    asked = mcp.call("ao_ask", {"question": "which store keeps the ledger?", "options": ["sqlite", "files"],
                                "context": "the ledger grows", "slice": "S3"}, cfg, False)

    [(blocked, _), (question, keyboard)] = sent
    assert blocked == BLOCKED_PHONE[chosen]
    without_precedent = [line for line in DECISION_PHONE[chosen] if "D-1 " not in line]
    assert question == "\n".join(without_precedent).replace("{id}", asked["id"])
    assert [row[0]["callback_data"] for row in keyboard] == [f"{asked['id']}:a", f"{asked['id']}:b"]
    rec = dict(A.decisions(cfg["root"])[0], precedents=[{"project": "acme-api", "kind": "question", "id": "D-1",
                                                         "outcome": "answered: sqlite"}])
    assert cli._decision_text(rec, cfg) == "\n".join(DECISION_PHONE[chosen]).replace("{id}", asked["id"])


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_a_free_text_option_labeled_in_either_language_is_answered_in_words_in_either_project(project, chosen):
    cfg = _choose(project, chosen)
    root = cfg["root"]
    os.makedirs(os.path.join(root, A.DECISION_DIR), exist_ok=True)

    for did, label in (("D-101", "Başka (serbest metin)"), ("D-102", "Other (free text)")):
        record = {"asked_at": 1, "question": "which store?", "state": "open",     # labeled, and never flagged
                  "options": [{"key": "a", "label": "sqlite"}, {"key": "x", "label": label}]}
        with open(os.path.join(root, A.DECISION_DIR, did + ".json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        with pytest.raises(A.AnswerRefused, match="is answered in your own words"):
            A.answer(root, did, "x")
        with pytest.raises(A.AnswerRefused, match="answer in your own words with x <text>"):
            A.answer(root, did, "a and a word more")
        assert A.answer(root, did, "x a file a day", by="alice")["answer"] == "a file a day"

    assert A.free_text_option({"key": "y", "free_text": True})
    assert A.free_text_option({"key": "x", "label": "Other (free text)"})
    assert not A.free_text_option({"key": "a", "label": "Other (free text)"})     # a person's option, so named
    assert not A.free_text_option({"key": "x", "label": "Other"}) and not A.free_text_option("x")


# ---- the handoff note: written in the project's language, sent to the phone up to either heading ---

HANDOFF = {
    "en": ["# Handoff — proj", "_2026-09-17 16:00_", "", "**Reason:** the provider's quota ran out", "",
           "## Now", "- implementer: **working**, last wrote 5m ago", "- HEAD `abc1234 add the store`",
           "- 2 file(s) uncommitted, 1 commit(s) unpushed, 0 commit(s) behind (no remote branch to compare with)",
           "- last review: APPROVED (2026-09-17-1200-review.md)", "- it says: _writing the tests_",
           "- credit: 4,000 / 10,000 (6,000 left)",
           "", "## Decisions waiting for an answer — **these unblock the work**", "- `D-100` which store?",
           "    - `D-100 a` → sqlite", "    - `D-100 x` → a label",
           "", "## Blocked", "- **S2** sync — no reason recorded", "- **S6** auth — a key",
           "", "## Running", "- **S3** store", "", "## Next (2 item(s))", "- **S4** export",
           "", "## What a successor can do",
           "- Answer a pending decision: tap a button on the phone, or `ao answer <id> <letter>`",
           "- Write a decision of your own: send a message on Telegram — it lands in the mailbox as urgent",
           "- See the state: `ao status`, `ao board`, `ao decisions`",
           "", "_push, PRs and closing an epic never transfer in a handoff._"],
    "tr": ["# Devir — proj", "_2026-09-17 16:00_", "", "**Sebep:** the provider's quota ran out", "",
           "## Şu an", "- uygulayıcı: **working**, son yazım 5dk önce", "- HEAD `abc1234 add the store`",
           "- 2 dosya commit'siz, 1 commit push'suz, 0 commit geride (karşılaştırılacak uzak dal yok)",
           "- son review: APPROVED (2026-09-17-1200-review.md)", "- diyor ki: _writing the tests_",
           "- kredi: 4,000 / 10,000 (6,000 kaldı)",
           "", "## Cevap bekleyen kararlar — **bunlar işi açar**", "- `D-100` which store?",
           "    - `D-100 a` → sqlite", "    - `D-100 x` → a label",
           "", "## Blocked", "- **S2** sync — sebep kayıtlı değil", "- **S6** auth — a key",
           "", "## Yürüyen", "- **S3** store", "", "## Sıradaki (2 madde)", "- **S4** export",
           "", "## Devralan ne yapabilir",
           "- Bekleyen kararı cevapla: telefondan butona bas, ya da `ao answer <id> <harf>`",
           "- Serbest karar yaz: Telegram'a mesaj at — acil olarak kutuya düşer",
           "- Durumu gör: `ao status`, `ao board`, `ao decisions`",
           "", "_push, PR ve epic kapatma hiçbir devirde aktarılmaz._"],
}
SUCCESSOR = {"en": "## What a successor can do", "tr": "## Devralan ne yapabilir"}


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_handoff_note_is_in_the_projects_language_and_the_phone_is_sent_it_up_to_its_heading(project,
                                                                                                monkeypatch,
                                                                                                capsys, chosen):
    cfg = _choose(project, chosen)
    sent = []
    monkeypatch.setattr(cli, "datetime", _Now)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: sent.append(text) or 1)
    monkeypatch.setattr(A, "busy", lambda cfg, adapter: ("working", 300, "writing the tests"))
    monkeypatch.setattr(A, "git_state", lambda root: {"log": ["abc1234 add the store"], "dirty": [" M a.py", "?? b.py"],
                                                      "ahead": "1", "behind": 0, "base": ""})
    monkeypatch.setattr(A, "reviews", lambda root, reviews_dir, limit=4: [("2026-09-17-1200-review.md", "APPROVED")])
    monkeypatch.setattr(A, "decisions", lambda root, state=None: [
        {"id": "D-100", "question": "which store?",
         "options": [{"key": "a", "label": "sqlite"}, {"key": "x", "label": "a label", "free_text": True}]}])
    monkeypatch.setattr(A, "board", lambda root: {
        "blocked": [{"id": "S2", "title": "sync", "notes": {}},
                {"id": "S6", "title": "auth", "notes": {"needs": "a key"}}],
        "running": [{"id": "S3", "title": "store", "notes": {}}],
        "queued": [{"id": "S4", "title": "export", "notes": {}}, {"id": "S5", "title": "import", "notes": {}}],
        "inbox": [], "verified": [], "done": []})
    monkeypatch.setattr(A, "usage_api", lambda ident: {"driver": "fake"})
    monkeypatch.setattr(A, "account_usage", lambda timeout=20, adapter_id=None: {"used": 4000.0, "limit": 10000.0})

    assert cli.cmd_handoff(cfg, SimpleNamespace(reason="the provider's quota ran out", no_send=False)) == 0

    note = "\n".join(HANDOFF[chosen])
    [name] = A.mailbox(cfg["root"], cfg["mailbox"])
    assert _read(os.path.join(cfg["root"], cfg["mailbox"], name)) == note + "\n"
    assert _plain(capsys.readouterr().out).startswith(note + "\n")
    assert sent == [note.split(SUCCESSOR[chosen])[0]]


def test_the_handoff_heading_the_phone_part_ends_at_is_read_in_either_language():
    for chosen, other in (("en", "tr"), ("tr", "en")):
        note = "\n".join(HANDOFF[chosen])
        head = note.split(SUCCESSOR[chosen])[0]
        assert cli._handoff_head(note) == head and SUCCESSOR[other] not in head
    assert cli._handoff_head("# Handoff — proj\n\n## Now\n") == "# Handoff — proj\n\n## Now\n"
    assert set(language.forms("handoff-successor")) == set(SUCCESSOR.values())


# ---- the digest -------------------------------------------------------------------------------------

def _digest_ledgers():
    return {"decisions": {"open": 2, "asked": 3, "answered": 1, "median_minutes": 14},
            "blocked": [{"id": "S2", "needs": "a key from the owner"}],
            "commits": [{"sha": "abc1234", "subject": "add the store"}, {"sha": "def5678", "subject": "export"}],
            "unpushed": 2, "verifications": {"passed": 5, "failed": 1}, "authority": {"granted": 4, "refused": 2},
            "reviews": {"approved": 3, "changes": 1}, "refusal_reasons": [("unreviewed tree", 2), ("one-off", 1)],
            "credits": {"used": 7400.0, "limit": 10000.0, "remaining": 2600.0},
            "account": {"adapter": "kiro", "readable": True},
            "credit_days": [("2026-09-16", 1200.0), ("2026-09-17", 800.0)],
            "alerts": {"sent": 3, "held": 9}, "board": {"running": 1, "blocked": 1, "queued": 4, "done": 12}}


def _digest_page(chosen, C):
    """The digest of _digest_ledgers, as a terminal shows it."""
    b, dim, reset, red, green, yellow = C["b"], C["dim"], C["reset"], C["red"], C["green"], C["yellow"]
    head = f"{b}{C['mag']}── "
    words = {
        "en": [f"{b}proj{reset}{dim}  last 24 hours{reset}", "",
               f"  state    {yellow}slowing{reset}{dim}, last wrote 10m ago{reset}  "
               f"{red}⚠ 12m busy, nothing produced{reset}",
               f"\n  {yellow}2 decision(s) waiting for an answer{reset}"
               f"{dim} — these unblock the work: ao decisions{reset}",
               f"\n{head}LANDED WORK {'─' * 42}{reset}", f"{dim}, 2 unpushed{reset}",
               f"\n{head}GATES {'─' * 48}{reset}",
               f"  verify     {green}5 passed{reset}, {red}1 failed{reset}",
               f"  review     {green}3 APPROVED{reset}, {yellow}1 NEEDS_CHANGES{reset}",
               f"  commit-ok  {green}4 granted{reset}, {yellow}2 refused{reset}",
               f"\n  decisions  1/3 answered{dim}, median 14m{reset}",
               f"\n{head}CREDIT {'─' * 47}{reset}", f"{dim}  (2,600 left){reset}",
               f"\n{dim}board: running 1 · blocked 1 · queued 4 · done 12  |  alerts: 3 sent, 9 suppressed{reset}"],
        "tr": [f"{b}proj{reset}{dim}  son 24 saat{reset}", "",
               f"  durum    {yellow}slowing{reset}{dim}, son yazım 10dk önce{reset}  "
               f"{red}⚠ 12dk meşgul, üretim yok{reset}",
               f"\n  {yellow}2 cevap bekleyen karar{reset}{dim} — bunlar işi açar: ao decisions{reset}",
               f"\n{head}İNEN İŞ {'─' * 46}{reset}", f"{dim}, 2 push'suz{reset}",
               f"\n{head}KAPILAR {'─' * 46}{reset}",
               f"  doğrulama  {green}5 geçti{reset}, {red}1 düştü{reset}",
               f"  review     {green}3 APPROVED{reset}, {yellow}1 değişiklik{reset}",
               f"  commit-ok  {green}4 verildi{reset}, {yellow}2 reddedildi{reset}",
               f"\n  karar      1/3 cevaplandı{dim}, ortanca 14dk{reset}",
               f"\n{head}KREDİ {'─' * 48}{reset}", f"{dim}  (2,600 kaldı){reset}",
               f"\n{dim}pano: running 1 · blocked 1 · queued 4 · done 12  |  "
               f"uyarı: 3 gönderildi, 9 susturuldu{reset}"],
    }[chosen]
    title, blank, state, waiting, landed, unpushed, gates, verify, review, grants, asked, credit, left, board = words
    return "\n".join([title, blank, state, f"  {dim}↳ reading the store{reset}", waiting,
                      f"  {red}⊘{reset} S2  {dim}a key from the owner{reset}", landed,
                      f"  {b}2{reset} commit{unpushed}", f"    {dim}abc1234{reset} add the store",
                      f"    {dim}def5678{reset} export", gates, verify, review, grants,
                      f"    {dim}2× unreviewed tree{reset}", asked, credit,
                      f"  {yellow}7,400{reset} / 10,000{left}", f"    {dim}2026-09-16{reset}     1,200",
                      f"    {dim}2026-09-17{reset}       800", board]) + "\n"


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_digest_is_in_the_projects_language_and_its_sections_keep_their_width(project, monkeypatch, capsys,
                                                                                 chosen):
    cfg = _choose(project, chosen)
    C = _colour(monkeypatch)
    monkeypatch.setattr(A, "digest", lambda root, cfg, since_days=1.0: _digest_ledgers())
    monkeypatch.setattr(A, "busy", lambda cfg, adapter: ("slowing", 600, "reading the store"))
    monkeypatch.setattr(A, "spinning", lambda root: 12)

    assert cli.cmd_digest(cfg, SimpleNamespace(days=1, n=6)) == 0

    page = capsys.readouterr().out
    assert page == _digest_page(chosen, C)
    headings = [line for line in _plain(page).split("\n") if line.startswith("── ")]
    assert len(headings) == 3 and {len(line) for line in headings} == {57}
    assert cli.cmd_digest(cfg, SimpleNamespace(days=7, n=6)) == 0
    assert cli.cmd_digest(cfg, SimpleNamespace(days=0.5, n=6)) == 0
    windows = [line.split("  ", 1)[1] for line in _plain(capsys.readouterr().out).split("\n") if line.startswith("proj  ")]
    assert windows == {"en": ["last 7 days", "last 0.5 days"], "tr": ["son 7 gün", "son 0.5 gün"]}[chosen]


# ---- the notes ao decide and a released hold leave the implementer --------------------------------

DECIDED = {
    "en": "# keep the ledger in sqlite\n\nkeep the ledger in sqlite\n\n**Why:** one file to back up\n\n**Scope:** S3\n\n"
          "_decision record: AD-1789650000_\n",
    "tr": "# keep the ledger in sqlite\n\nkeep the ledger in sqlite\n\n**Neden:** one file to back up\n\n**Kapsam:** S3\n\n"
          "_karar kaydı: AD-1789650000_\n",
}
RELEASED = {
    "en": "# INFO — hold released\n\nHeld: 10 minutes\nReason: maintenance\n\n"
          "## What changed meanwhile\n\nrebased onto main\n",
    "tr": "# INFO — hold released\n\nDuruldu: 10 dakika\nSebep: maintenance\n\n"
          "## Bu sürede ne değişti\n\nrebased onto main\n",
}


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_notes_a_decision_and_a_released_hold_leave_are_in_the_projects_language(project, monkeypatch, chosen):
    cfg = _choose(project, chosen)
    box = os.path.join(cfg["root"], cfg["mailbox"])
    monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: 1789650000.0, strftime=time.strftime,
                                                     localtime=time.localtime, sleep=time.sleep))

    assert cli.cmd_decide(cfg, SimpleNamespace(list=False, decision="keep the ledger in sqlite", why="one file to back up",
                                               scope="S3", answers=None, to=None, urgent=False, n=10)) == 0
    [decided] = os.listdir(box)
    assert _read(os.path.join(box, decided)).endswith("\n---\n" + DECIDED[chosen])
    os.remove(os.path.join(box, decided))

    with open(os.path.join(cfg["root"], A.HOLD_FILE), "w", encoding="utf-8") as fh:
        json.dump({"by": "alice", "reason": "maintenance", "at": int(time.time()) - 630, "stopped": []}, fh)
    assert cli.cmd_hold(cfg, SimpleNamespace(action="release", note="rebased onto main")) == 0
    [released] = os.listdir(box)
    assert released.endswith("-INFO-hold-released.md") and _read(os.path.join(box, released)) == RELEASED[chosen]


# ---- alarms: worded in the project's language, keyed and routed the same in either -----------------

ALARMS = {
    "en": [("proj: alarm test", "red level test — ao alarms test"),
           ("proj: architect at quota", "limit reached — wakes are held until {reset}; if auto-continue is on in the "
                                        "architect's app, the session goes on by itself"),
           ("proj: architect at quota", "architect quota exhausted — wakes are held until {reset}; if auto-continue "
                                        "is on in the architect's app, the session goes on by itself")],
    "tr": [("proj: alarm testi", "red seviyesi testi — ao alarms test"),
           ("proj: mimar kotada", "limit reached — uyandırma {reset}'e kadar bekletiliyor; mimarın uygulamasında "
                                  "auto-continue açıksa oturum kendi devam eder"),
           ("proj: mimar kotada", "architect quota exhausted — uyandırma {reset}'e kadar bekletiliyor; mimarın "
                                  "uygulamasında auto-continue açıksa oturum kendi devam eder")],
}


def test_an_alarm_is_worded_in_the_projects_language_and_keyed_and_routed_the_same_in_either(project, monkeypatch):
    until = time.time() + 3600
    reset = time.strftime("%H:%M", time.localtime(until))
    raised = {}
    for chosen in LANGUAGES:
        cfg = _choose(project, chosen)
        raised[chosen] = []
        monkeypatch.setattr(W, "notify", lambda title, msg, root=None, into=raised[chosen], **kw:
                            into.append((title, msg, kw)) or True)
        assert cli.cmd_alarms(cfg, SimpleNamespace(action="test", level="red")) == 0
        W.touch_architect_quota(cfg["root"], {"arch_quota_until": until, "wake_error": {"text": "limit reached"}})
        W.touch_architect_quota(cfg["root"], {"arch_quota_until": until}, cfg)
        assert [(title, msg) for title, msg, _ in raised[chosen]] == \
            [(title, msg.format(reset=reset)) for title, msg in ALARMS[chosen]]

    def route(kw):
        return dict(kw, key="alarm-test" if kw["key"].startswith("alarm-test-") else kw["key"])
    assert [route(kw) for _, _, kw in raised["en"]] == [route(kw) for _, _, kw in raised["tr"]]
    assert [kw["key"] for _, _, kw in raised["tr"]][1:] == ["architect-quota", "architect-quota"]
    assert all(kw["audience"] == "human" for _, _, kw in raised["tr"])


def test_a_project_that_changes_its_language_rings_no_standing_alarm_again(project, monkeypatch):
    rung = []
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: rung.append(title) or True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None: True)
    state = {"arch_quota_until": time.time() + 3600, "wake_error": {"text": "limit reached"}}

    assert W.touch_architect_quota(_choose(project, "en")["root"], state) is True
    assert W.touch_architect_quota(_choose(project, "tr")["root"], state) is False

    [alarm] = A.active_alarms("proj")
    assert alarm["key"] == "architect-quota" and alarm["count"] == 2 and rung == ["proj: architect at quota"]


RED_MAIL = {
    "en": "set by alice: maintenance\n\nStanding since {since}, raised 1×. Project: {root}\n"
          "`ao alarms` shows the ladder, `ao status` the state.\n\nEvidence:\ncheck: hold\n  5h  from hold file, 2m ago",
    "tr": "set by alice: maintenance\n\nDuruyor: {since}'den beri (1 kez). Proje: {root}\n"
          "`ao alarms` merdiveni, `ao status` durumu gösterir.\n\nKanıt:\ncheck: hold\n  5h  from hold file, 2m ago",
}


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_a_red_alarms_mail_is_worded_in_the_projects_language(project, monkeypatch, chosen):
    cfg = _choose(project, chosen)
    mails = []
    monkeypatch.setattr(W, "desktop_notify", lambda title, msg, cfg=None: True)
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: 1)
    monkeypatch.setattr(email, "send", lambda subject, body, root=None, opener=None: mails.append((subject, body)) or True)
    evidence = {"check": "hold", "samples": [{"value": "5h", "source": "hold file", "at": time.time() - 150}]}

    W.notify("proj: hold standing 5h", "set by alice: maintenance", cfg["root"], key="hold-standing", window=6 * 3600,
             audience="human", level="red", evidence=evidence)

    [alarm] = A.active_alarms("proj")
    since = time.strftime("%d %b %H:%M", time.localtime(alarm["first"]))
    assert mails == [("proj: hold standing 5h", RED_MAIL[chosen].format(since=since, root=cfg["root"]))]
    assert alarm["key"] == "hold-standing" and alarm["red_sent"]


# ---- the watchdog's lines to the phone, and the reasons it hands off with --------------------------

WATCHDOG = {
    "en": {"woken": "🤖 *Architect woken* — handling 2 report(s) (pid 99999)",
           "retried": "🤖 *Architect woken* — after failed attempts, for 2 report(s) (pid 99999)",
           "failed": "proj: architect wake failed", "binary": " — binary: /agents/claude 2.1.261; `ao doctor`",
           "done": "✅ *Architect done* — 2 report(s) closed, queue empty",
           "handoffs": ["the architect could not be woken — no quota", "the provider's quota is exhausted"]},
    "tr": {"woken": "🤖 *Mimar uyandırıldı* — 2 rapor işleniyor (pid 99999)",
           "retried": "🤖 *Mimar uyandırıldı* — başarısız denemelerin ardından, 2 rapor için (pid 99999)",
           "failed": "proj: mimar uyandırılamadı", "binary": " — ikili: /agents/claude 2.1.261; `ao doctor`",
           "done": "✅ *Mimar bitirdi* — 2 rapor kapandı, kuyruk boş",
           "handoffs": ["mimar uyandırılamadı — kota yok", "sağlayıcı kotası tükendi"]},
}


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_watchdog_tells_the_phone_of_a_wake_and_rings_a_failed_one_in_the_projects_language(project, monkeypatch,
                                                                                               tmp_path, chosen):
    world, sent, clock = noise._world(_choose(project, chosen), monkeypatch, tmp_path, wakes=True)
    noise._wakes(world, monkeypatch, clock, lambda n: noise.OVERLOADED % n if n < 5 else None)
    noise._mail(world, noise.REQUEST, noise.BLOCKED)
    raised = []
    monkeypatch.setattr(W, "notify", lambda title, msg, root=None, **kw:
                        raised.append((title, kw.get("key"))) or noise.NOTIFY(title, msg, root, **kw))

    noise._cycles(world, clock, 3 * noise.HOUR)

    said = WATCHDOG[chosen]
    assert [text for kind, text, _, _ in sent if kind == "telegram" and text.startswith("🤖")] == \
        [said["woken"], said["retried"]]
    failed = [(title, msg) for kind, title, msg, _ in sent if kind == "desktop" and title == said["failed"]]
    assert len(failed) == 1 and failed[0][1].endswith(said["binary"])
    other = WATCHDOG["tr" if chosen == "en" else "en"]
    assert not [title for kind, title, _, _ in sent if other["failed"] in title or other["woken"] in title]
    assert {key for title, key in raised if title == said["failed"]} == {"architect-wake-failed"}


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_phone_hears_the_architect_is_done_in_the_projects_language(project, monkeypatch, chosen):
    cfg = _choose(project, chosen)
    told = []
    monkeypatch.setattr(telegram, "send", lambda text, root=None, keyboard=None: told.append(text) or 1)
    monkeypatch.setattr(A, "anomalies", lambda *args, **kwargs: [{"kind": "report-waiting", "facts": [], "reports": []}])
    monkeypatch.setattr(W, "open_work", lambda cfg, root: False)
    state = {"arch_pending": 2}

    W.escalate(cfg["root"], cfg, {}, 0, SimpleNamespace(idle_minutes=6.0, dry_run=False), state)

    assert told == [WATCHDOG[chosen]["done"]] and state["arch_pending"] == 0


@pytest.mark.parametrize("chosen", LANGUAGES)
def test_the_watchdog_hands_off_with_a_reason_in_the_projects_language(project, monkeypatch, tmp_path, chosen):
    world, sent, clock = noise._world(_choose(project, chosen), monkeypatch, tmp_path, wakes=True)
    reasons = []
    run = subprocess.run

    def handoff(argv, *args, **kwargs):
        if isinstance(argv, list) and "handoff" in argv:
            reasons.append(argv[argv.index("--reason") + 1])
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        return run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", handoff)
    world.quota = False
    world.mail(noise.REQUEST, noise.BLOCKED)
    world.transcript_age(noise.HOUR)

    world.cycle(dry_run=False)                           # a report waits, and no quota wakes the architect
    for name in os.listdir(os.path.join(world.root, "agent-mail")):
        os.remove(os.path.join(world.root, "agent-mail", name))
    state = W.load_state(world.root)
    W.save_state(world.root, dict(state, last_handoff=0))
    world.board("running", "- [B8] slice · since: 2026-09-05 10:00").transcript_age(700)
    clock[0] += 2 * noise.HOUR
    world.cycle(dry_run=False)                           # the implementer's provider is out of quota

    assert reasons == WATCHDOG[chosen]["handoffs"]


# ---- ao init --language ------------------------------------------------------------------------------

def _init_in(root, capsys, **overrides):
    root.mkdir(exist_ok=True)
    subprocess.run([conftest.GIT, "init", "-q"], cwd=root, check=True)
    code = cli.cmd_init({"root": str(root)}, _init_args(name="acme-api", profile=None, agent="kiro", **overrides))
    return code, _plain(capsys.readouterr().out)


def test_init_language_chooses_the_projects_language_before_init_writes_a_file(project, tmp_path, capsys):
    root = tmp_path / "chosen"
    code, out = _init_in(root, capsys, language="tr")

    assert code == 0 and "wrote  .ao/config.json (+language tr)" in out
    assert json.loads((root / ".ao" / "config.json").read_text(encoding="utf-8"))["language"] == "tr"
    files = {rel: _read(root / rel) for rel in INIT_FILES}
    assert files == {".ao/authority.md": language.TEXTS["init.authority"]["tr"],
                     ".ao/board.md": language.TEXTS["init.board"]["tr"],
                     ".ao/backlog.md": language.TEXTS["init.backlog"]["tr"].format(name="acme-api"),
                     "agent-mail/README.md": language.TEXTS["init.mail-readme"]["tr"].format()}
    assert "(`## ACİL`)" in _read(root / STEERING)

    _machine("tr")                                         # the flag outranks the machine's choice
    english = tmp_path / "english"
    code, out = _init_in(english, capsys, language="en")
    assert code == 0 and _read(english / ".ao" / "board.md") == language.TEXTS["init.board"]["en"]

    config = json.loads((english / ".ao" / "config.json").read_text(encoding="utf-8"))
    (english / ".ao" / "config.json").write_text(json.dumps(dict(config, language="en")), encoding="utf-8")
    code, out = _init_in(english, capsys, language="tr")   # a config that chose: replaced, its files kept
    assert code == 0 and "wrote  .ao/config.json (+language tr)" in out and "kept   .ao/board.md" in out
    assert json.loads((english / ".ao" / "config.json").read_text(encoding="utf-8"))["language"] == "tr"
    assert _read(english / ".ao" / "board.md") == language.TEXTS["init.board"]["en"]


def test_init_takes_only_a_language_ao_has(capsys):
    parser = cli.build_parser()
    assert parser.parse_args(["init", "--language", "tr"]).language == "tr"
    assert parser.parse_args(["init"]).language is None
    with pytest.raises(SystemExit):
        parser.parse_args(["init", "--language", "de"])
    assert "invalid choice: 'de'" in capsys.readouterr().err
