#!/usr/bin/env python3
"""ao — agent-orchestrator, observation layer.

Implemented today: status, watch, tail, mail, verify, board, adapters, doctor.
Also: watchdog install/uninstall/status — a launchd job that restarts a stalled
implementer, with guards so it spends nothing when spending would not help.
Not yet: decide, since, init — see the
roadmap in README.md. Commands that do not exist say so rather than pretending.

Standard library only. Observation is strictly read-only.
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


def cmd_digest(cfg, args):
    """What happened in a window — read from the ledgers, never estimated.

    Event alerts answer "did something just occur". They cannot answer "is this
    week going well", and reconstructing that from thirty notifications is asking
    a human to do the tool's job.

    It also absorbs the question people actually ask most: *why is nothing
    moving*. That is not a separate command — it is the same facts, read at the
    top instead of the bottom.
    """
    root = cfg["root"]
    d = A.digest(root, cfg, args.days)
    win = ("24 saat" if args.days == 1 else
           f"{int(args.days)} gün" if args.days == int(args.days) else f"{args.days} gün")

    # Lead with the blocking answer. Someone opening this at 3am wants "what is
    # in the way", not a scoreboard.
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    state, age, doing = A.busy(cfg, adapter) if impl else ("unknown", None, "")
    spin = A.spinning(root)
    print(f"{C['b']}{cfg.get('project') or os.path.basename(root)}{C['reset']}"
          f"{C['dim']}  son {win}{C['reset']}\n")

    col = {"working": C["green"], "slowing": C["yellow"]}.get(state, C["red"])
    line = f"{col}{state}{C['reset']}"
    if age is not None:
        line += f"{C['dim']}, son yazım {age // 60}dk önce{C['reset']}"
    if spin:
        line += f"  {C['red']}⚠ {spin}dk meşgul, üretim yok{C['reset']}"
    print(f"  durum    {line}")
    if doing:
        print(f"  {C['dim']}↳ {doing[:110]}{C['reset']}")

    if d["decisions"]["open"]:
        print(f"\n  {C['yellow']}{d['decisions']['open']} cevap bekleyen karar{C['reset']}"
              f"{C['dim']} — bunlar işi açar: ao decisions{C['reset']}")
    for b in d["blocked"]:
        print(f"  {C['red']}⊘{C['reset']} {b['id']}  {C['dim']}{b['needs'][:88]}{C['reset']}")

    print(f"\n{C['b']}{C['mag']}── İNEN İŞ {'─' * 46}{C['reset']}")
    print(f"  {C['b']}{len(d['commits'])}{C['reset']} commit"
          f"{C['dim']}, {d['unpushed']} push'suz{C['reset']}")
    for c in d["commits"][:args.n]:
        print(f"    {C['dim']}{c['sha']}{C['reset']} {c['subject'][:76]}")

    v, a, r = d["verifications"], d["authority"], d["reviews"]
    print(f"\n{C['b']}{C['mag']}── KAPILAR {'─' * 46}{C['reset']}")
    vf = f", {C['red']}{v['failed']} düştü{C['reset']}" if v["failed"] else ""
    rc = f", {C['yellow']}{r['changes']} değişiklik{C['reset']}" if r["changes"] else ""
    ar = f", {C['yellow']}{a['refused']} reddedildi{C['reset']}" if a["refused"] else ""
    print(f"  doğrulama  {C['green']}{v['passed']} geçti{C['reset']}{vf}")
    print(f"  review     {C['green']}{r['approved']} APPROVED{C['reset']}{rc}")
    if a.get("integrity") == "broken":
        print(f"  commit-ok  {C['red']}YETKİ DEFTERİ BÜTÜNLÜĞÜ BOZUK{C['reset']}")
        if a.get("error"):
            print(f"    {C['dim']}{a['error'][:110]}{C['reset']}")
    else:
        print(f"  commit-ok  {C['green']}{a['granted']} verildi{C['reset']}{ar}")
    # A refusal repeated all week is a process problem, not an incident.
    for reason, n in d["refusal_reasons"]:
        if n > 1:
            print(f"    {C['dim']}{n}× {reason}{C['reset']}")

    dec = d["decisions"]
    if dec["asked"]:
        med = f"{dec['median_minutes']}dk" if dec["median_minutes"] is not None else "—"
        print(f"\n  karar      {dec['answered']}/{dec['asked']} cevaplandı"
              f"{C['dim']}, ortanca {med}{C['reset']}")

    if d.get("credits"):
        c = d["credits"]
        pct = c["used"] / c["limit"] * 100 if c["limit"] else 0
        cc = C["red"] if pct > 90 else C["yellow"] if pct > 70 else C["green"]
        print(f"\n{C['b']}{C['mag']}── KREDİ {'─' * 48}{C['reset']}")
        print(f"  {cc}{c['used']:,.0f}{C['reset']} / {c['limit']:,.0f}"
              f"{C['dim']}  ({c['remaining']:,.0f} kaldı){C['reset']}")
    for day, val in d["credit_days"][-args.n:]:
        print(f"    {C['dim']}{day}{C['reset']}  {val:>8,.0f}")

    al = d["alerts"]
    print(f"\n{C['dim']}pano: " +
          " · ".join(f"{k} {n}" for k, n in d["board"].items()) +
          f"  |  uyarı: {al['sent']} gönderildi, {al['held']} susturuldu{C['reset']}")
    return 0


def cmd_note(cfg, args):
    """Write an architect message into the mailbox — through the tool, on purpose.

    A woken architect should need no raw Write or Edit to do its job. The one
    time it had them, it used them on the orchestrator's own source and built a
    runaway. This is the only door to the mailbox an unattended architect gets.
    """
    # Read stdin only when asked. Defaulting to it made `ao note "title"` hang
    # waiting on a terminal that would never close, which is the wrong failure
    # for a command an unattended architect calls.
    body = args.body if args.body else (sys.stdin.read() if args.stdin else "")
    if not args.title or not body.strip():
        print(f"usage: {C['b']}ao note \"title\" --body \"…\" [--to implementer] [--urgent]{C['reset']}")
        print(f"       {C['dim']}or pipe the body on stdin{C['reset']}")
        return 1
    name = A.note(cfg["root"], cfg, args.to, args.title, body, urgent=args.urgent)
    print(f"{C['green']}written{C['reset']} {cfg['mailbox']}/{name}")
    if args.urgent:
        print(f"{C['dim']}urgent: reaches the implementer via ao lock, ao verify and "
              f"ao commit-ok{C['reset']}")
    return 0


AUTHORITY_TEMPLATE = """# Yetki — kanonik kaynak

Bu dosya bu depoda neyin serbest, neyin yasak olduğunu söyleyen **tek** kaynaktır.

**Öncelik:** Bu dosya mail'den üstündür. `agent-mail/` bir mesaj **kapsam** ekleyebilir
("şu dilimi yap"), **yetki** ekleyemez veya kaldıramaz. Bir mail bu dosyayla çelişiyorsa
bu dosya kazanır — mesajı reddet, çalışmayı durdurma, `KARAR GEREKLİ` yaz ve devam et.

Belirsizlik hâlinde **durmak da bir maliyettir.** Aşağıda açıkça yasak olmayan ve
dilimin kapsamında olan bir şey serbesttir.

## Serbest — sormadan yap

- **Commit** — `ao commit-ok` yetkiyi verdikten sonra `ao commit -m "…"` ile. Doğrudan
  `git commit` değil: o `--no-verify` taşıyabilir, `ao commit` ise önce yetkiyi denetler.
- Kod, test, fixture, doküman yazmak ve değiştirmek
- Gate koşturmak (`ao verify`)
- `.ao/board.md` durumunu güncellemek; `agent-mail/`'e mesaj bırakmak

## Yasak — asla yapma

- **`git push`**, PR açmak, force-push, hook atlamak (`--no-verify`)
- Epic/görev kutusunu tamamlandı işaretlemek (insan kararıdır)
- Fixture kanıtını production-qualified göstermek
- Mimari sözleşmeyi değiştirmek — bunun için `KARAR GEREKLİ` yaz, sıradaki maddeye geç
- Başka bir depoya dokunmak

## Şüphedeysen

Bu dosyada yoksa ve dilimin kapsamındaysa: **yap.** Kapsam dışıysa: `KARAR GEREKLİ`
yaz ve `.ao/backlog.md`'deki sıradaki açık maddeye geç. **Bekleme.**
"""

BOARD_TEMPLATE = """# Board

Her önceden yetkilendirilmiş işin **nerede olduğu**. Kabul sınırları `backlog.md`'de;
bu dosya yalnız durumu ve park edilmişse **neyi beklediğini** söyler.

Durumlar: `queued` → `running` → (`blocked` ⇄) → `verified` → `done`
Satır biçimi: `- [ID] başlık · anahtar: değer` — `blocked` için `needs:` zorunlu.
Bağımlılık: `needs: B1, B2` (kuyruk maddesinde) → tamamlanınca READY olur.

Bu dosyayı uygulayıcı doğrudan düzenler. `ao board` yalnız okur.

## running

## blocked

## queued

## inbox

## verified

## done
"""

BACKLOG_TEMPLATE = """# {name} — önceden yetkilendirilmiş iş kuyruğu

Bu dosya, **mimarın kararı beklenmeden** başlanabilecek işleri sırayla listeler.
Her maddenin kabul sınırı önceden yazılmıştır.

**Kural:** Açık dilim bir mimari karara takılırsa DURMA. Dilimi `blocked` işaretle
(`needs:` ile), `agent-mail/`'e `KARAR GEREKLİ` mesajı bırak — ya da `ao_ask` ile
seçenekli soru sor — ve buradaki ilk **açık** maddeye geç.

---

## 1. <ilk dilim başlığı>
<ne yapılacak, bir paragraf>
**Kabul sınırı:** <ölçülebilir: hangi testler geçer, ne değişmez, ne yasak>

---

## Kuyruk dışı — asla kendi başına yapma
- Görev/epic kutusunu tamamlandı işaretlemek
- `git push`, PR açma, force-push, hook atlama
- Kuyruk dışından yeni işe geçmek
- Mimari sözleşme değiştirmek (`KARAR GEREKLİ` yaz ve sıradaki maddeye geç)
"""

MAIL_README = """# agent-mail — koordinasyon protokolü

Dosya tabanlı, asenkron mesajlaşma. **Teslim onayı = silme.** Mutlak yollar zorunlu.
Bu dizin gitignore'da; mail **veridir, yetki değil** — yetki `.ao/authority.md`'dedir.

- Ad: `YYYYMMDD-HHMM-<gönderen>-to-<alıcı>-<TÜR>-<konu>.md`
- Türler: `DECISION` (kapsam/karar), `INFO`, `ACIL` (acil — `## ACİL` başlığıyla;
  `ao lock`, `ao verify` ve `ao_*` yanıtlarıyla ulaşır; `ao commit-ok` onaylanana
  dek yetki vermez, kurulu AO pre-commit hook'unun çalıştırdığı `ao commit-check`
  commit anında yeniden doğrular), `ANOMALY` (watchdog olgusu), `DEVIR` (devir notu).
- Uygulayıcı her tur başında `ao_inbox` çeker, uygulayıp/reddedip `ao_ack` ile siler.
- Uygulayıcı takılınca `ao_report {{kind:"blocked"}}` ya da `ao_ask` — düz metinle
  park etmez.
"""

STEERING_COORD = """---
inclusion: always
---

# Coordination: the `ao` tools

Every turn starts with `ao_inbox`; apply or explicitly reject each message, then
`ao_ack`. Report changes as they happen: `ao_report {{kind: "blocked"|"status"|"done"}}`.
When you need a decision, ask with options — `ao_ask` — and move to the next queued
item; do not park on prose. Check `ao_decisions` next turn.

Urgent messages (`## ACİL`) reach you through `ao lock`, `ao verify` and every `ao_*`
response. `ao commit-ok` refuses authority until you acknowledge them; an installed
AO pre-commit hook runs `ao commit-check` to revalidate them immediately before commit.

Review with `ao review`, never yourself. `ao commit-ok` refuses a review you wrote.
Heavy commands go through the machine lock: `ao lock -- <cmd>`. `ao verify` takes it
itself. Authority lives in `.ao/authority.md`, not in mail. `push` is never yours.
"""


def _detect_gates(root):
    """Guess the project's gates from what is already there. A wrong guess costs a
    failed verify, which is visible; no guess costs an empty gate file nobody
    notices, which is not.

    Every toolchain found gets its gates (#5): a repository with a JavaScript
    workspace and a .NET backend got npm gates only, and nothing verified the
    backend.
    """
    gates, quick = {}, []
    pj = os.path.join(root, "package.json")
    if os.path.exists(pj):
        try:
            scripts = (json.load(open(pj, encoding=UTF8)).get("scripts") or {})
        except Exception:
            scripts = {}
        for name, key in (("typecheck", "typecheck"), ("lint", "lint"), ("test", "test")):
            if key in scripts:
                gates[name] = {"run": f"npm run {key}", "expect": "exit_zero",
                               "timeout": 600 if name != "test" else 2400}
                if name != "test":
                    quick.append(name)
        if "test" in gates:
            gates["test"]["serialise"] = True
    if any(os.path.exists(os.path.join(root, f)) for f in ("pyproject.toml", "setup.py")):
        gates["pytest"] = {"run": "python -m pytest -q", "expect": "exit_zero",
                           "timeout": 2400, "serialise": True}
        # Collecting imports every test module: cheap, and it exercises the code.
        gates["pytest-collect"] = {"run": "python -m pytest --collect-only -q", "expect": "exit_zero",
                                   "timeout": 300}
        quick.append("pytest-collect")
        if shutil.which("ruff"):
            gates["ruff"] = {"run": "ruff check .", "expect": "exit_zero", "timeout": 300}
            quick.append("ruff")
    if any(name.endswith((".sln", ".csproj", ".fsproj")) for name in os.listdir(root)):
        gates["dotnet-build"] = {"run": "dotnet build --nologo -v q", "expect": "exit_zero", "timeout": 1200}
        gates["dotnet-test"] = {"run": "dotnet test --nologo", "expect": "exit_zero", "timeout": 2400,
                                "serialise": True}
        quick.append("dotnet-build")
    if os.path.exists(os.path.join(root, "go.mod")):
        gates["go-vet"] = {"run": "go vet ./...", "expect": "exit_zero", "timeout": 600}
        gates["go-test"] = {"run": "go test ./...", "expect": "exit_zero", "timeout": 2400, "serialise": True}
        quick.append("go-vet")
    if os.path.exists(os.path.join(root, "Cargo.toml")):
        gates["cargo-check"] = {"run": "cargo check", "expect": "exit_zero", "timeout": 1200}
        gates["cargo-test"] = {"run": "cargo test", "expect": "exit_zero", "timeout": 2400, "serialise": True}
        quick.append("cargo-check")
    gates["diff-check"] = {"run": "git diff --check", "expect": "exit_zero", "timeout": 60}
    quick.append("diff-check")
    return {"gates": gates,
            "profiles": {"quick": quick, "full": list(gates)},
            "default_profile": "quick"}

def _models(adapter_id):
    try:
        return A.load_adapter(adapter_id).get("models") or {}
    except Exception:
        return {}


# The architect's grant names each ao command it runs (#58): `ao:*` would admit
# `ao push allow` and `ao waive`, which are a person's, and `find` runs anything
# through -exec.
ARCHITECT_TOOLS = ("Read,Grep,Glob,"
                   "Bash(ao status:*),Bash(ao board:*),Bash(ao mail:*),Bash(ao decide:*),Bash(ao note:*),"
                   "Bash(ao answer:*),Bash(ao review:*),Bash(ao catchup:*),Bash(ao doctor),"
                   "Bash(ao doctor --check),Bash(ao digest:*),Bash(ao notices:*),Bash(ao alarms),"
                   "Bash(ao alarms list:*),Bash(ao cost:*),Bash(ao credits:*),Bash(ao tail:*),"
                   "Bash(ao watchdog status:*),Bash(ao watchdog explain:*),Bash(ao watchdog trace:*),"
                   "Bash(git status:*),Bash(git log:*),Bash(git diff:*),Bash(git show:*),Bash(ls:*),"
                   "Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(grep:*),Bash(ps:*),Bash(lsof:*),"
                   "Bash(rm agent-mail/*)")


def _reviewer_block(adapter_id, model=None):
    """A reviewer block composed from its adapter: its actor's name, family, and argv (#88, #76)."""
    declared = A.load_adapter(adapter_id)
    route = A.compose_reviewer(adapter_id, model=model)
    name = declared.get("actor_name") or adapter_id
    block = {"id": f"{name}-reviewer-{model}" if model else f"{name}-reviewer"}
    if declared.get("family"):
        block["family"] = declared["family"]
    block["argv"] = route["argv"]
    return block


def _architect_argv(adapter_id):
    """The architect's argv: its adapter's resume, with the allowed tools narrowed to ARCHITECT_TOOLS."""
    declared = A.load_adapter(adapter_id)
    argv = list((declared.get("resume") or {}).get("argv") or [])
    grant = (declared.get("options") or {}).get("allowed_tools") or []
    if grant:
        while grant[0] in argv:
            at = argv.index(grant[0])
            del argv[at:at + 2]
        argv += [part.replace("{tools}", ARCHITECT_TOOLS) for part in grant]
    return argv


def _profile_config(root, args, base):
    """Return the profile's intended config and added block names without writing."""
    cfg = dict(base) if isinstance(base, dict) else {}
    presets = A.profiles()
    prof = presets.get(getattr(args, "profile", None) or "")
    impl_adapter = getattr(args, "implementer", None) or (prof or {}).get("implementer")
    if not impl_adapter and not prof:
        return cfg, []
    roles = prof or presets.get(A.default_profile()) or {}
    added = []
    if "implementer" not in cfg:
        impl_adapter = impl_adapter or roles.get("implementer")
        block = {"adapter": impl_adapter, "session": "auto",
                 "name": A.load_adapter(impl_adapter).get("actor_name") or impl_adapter}
        model = getattr(args, "model", None) or _models(impl_adapter).get("default")
        if model:
            block["model"] = model
        if getattr(args, "effort", None):
            block["effort"] = args.effort
        cfg["implementer"] = block
        added.append("implementer")
    if "reviewer" not in cfg:
        reviewer = roles.get("reviewer")
        rmodel = getattr(args, "reviewer_model", None) or _models(reviewer).get("review")
        cfg["reviewer"] = dict(_reviewer_block(reviewer, rmodel),
                               _why="must not be the implementer; a different model where one is available")
        added.append("reviewer")
    if "architect" not in cfg:
        architect = roles.get("architect")
        cfg["architect"] = {"adapter": architect, "session": "auto", "cwd": root, "name": "fable",
                            "argv": _architect_argv(architect),
                            "_why": "resumable and woken only into absence; read-only tools plus ao"}
        added.append("architect")
    return cfg, added


def _apply_profile(root, args):
    """Write missing profile blocks while preserving existing project choices.

    A config that exists but cannot be read is refused: planning from an empty
    document and writing the plan over it would erase what it held (#56).
    """
    document = A.project_config_document(root)
    if document["problem"] and os.path.lexists(os.path.join(root, ".ao", "config.json")):
        raise ValueError(document["problem"])
    planned, added = _profile_config(root, args, document["config"] or {})
    if added:
        A.write_project_config(root, json.dumps(planned, indent=2, ensure_ascii=False))
    return added


def _planned_project_config_text(cfg):
    """Serialize and validate one in-memory config against the disk contract."""
    if not isinstance(cfg, dict) or not cfg:
        return None, ".ao/config.json must be a non-empty top-level JSON object"
    try:
        text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
        raw = text.encode(UTF8)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        return None, f".ao/config.json is not readable valid JSON ({exc})"
    if len(raw) > A.PROJECT_CONFIG_MAX_BYTES:
        return None, (
            ".ao/config.json exceeds the "
            f"{A.PROJECT_CONFIG_MAX_BYTES:,}-byte limit"
        )
    problem = A._json_container_depth_problem(raw)
    return (None, problem) if problem else (text, None)


def _planned_runtime_config(root, cfg):
    """Apply load_config's runtime-only defaults to an in-memory document."""
    runtime = dict(cfg)
    runtime.setdefault("root", root)
    runtime.setdefault("mailbox", "agent-mail")
    runtime.setdefault("reviews", "semantic-review")
    if "implementer" not in runtime:
        found = A.discover_session(root)
        if found:
            runtime["implementer"] = found
    return runtime


def cmd_init(cfg, args):
    """Put `ao` on a project. Idempotent: existing files are left alone.

    This is the answer to "how do I apply this to another project". Everything
    it writes is a file the agent already knows how to read, so a project without
    MCP or a watchdog gets the whole protocol from the files alone; the optional
    flags add the automation on top.
    """
    root = cfg["root"]
    name = args.name or os.path.basename(root)
    config_path = os.path.join(root, ".ao", "config.json")
    marker_path = os.path.join(root, PROJECT_MARKER)

    # Resolve every project-local decision before creating anything. A failed
    # reviewer probe is a clean refusal, not a half-initialized installation.
    marker_document = _worktree_project_marker_document(root)
    marker_fingerprint = marker_document["fingerprint"]
    if marker_document["exists"] and marker_document["problem"]:
        print(
            f"{C['red']}init refused{C['reset']}: "
            f"{_project_refusal(marker_document['problem'])}"
        )
        return 1

    config_existed = os.path.lexists(config_path)
    existing_raw = None
    if config_existed:
        document = A.project_config_document(root)
        if document["problem"]:
            print(
                f"{C['red']}init refused{C['reset']}: "
                f"{_project_refusal(document['problem'])}"
            )
            return 1
        base_config = document["config"]
        existing_raw = document["raw"]
    else:
        base_config = {"project": name, "round_budget": S.default("round_budget")}

    planned_config, added = _profile_config(root, args, base_config)
    config_text, config_problem = _planned_project_config_text(planned_config)
    if config_problem:
        print(f"{C['red']}init refused{C['reset']}: {_project_refusal(config_problem)}")
        return 1
    # A quick profile that exercises none of the code gives every verify a green it did
    # not earn (#5). Checked while planning, before anything is written.
    detected_gates = _detect_gates(root)
    if not os.path.lexists(os.path.join(root, ".ao", "gates.json")):
        minimum = S.default("gates.coverage_min_files")
        trees = A.source_trees(root, minimum)
        quick = detected_gates["profiles"]["quick"]
        uncovered = A.gate_coverage_gaps(root, detected_gates, minimum, names=quick)
        if trees and len(uncovered) == sum(len(chains) for chains in trees.values()) \
                and not getattr(args, "allow_uncovered_gates", False):
            chains = sorted({chain for chains in trees.values() for chain in chains})
            print(f"{C['red']}init refused{C['reset']}: the quick gates ({', '.join(quick)}) exercise none of "
                  f"the detected toolchains ({', '.join(chains)}); declare gates in .ao/gates.json first, "
                  "or pass --allow-uncovered-gates")
            return 1
    runtime_cfg = _planned_runtime_config(root, planned_config)
    reviewer_probe = _reviewer_probe(runtime_cfg)
    if not reviewer_probe["ok"]:
        print(
            f"{C['red']}init refused{C['reset']}: reviewer probe "
            f"{_reviewer_probe_text(reviewer_probe)}"
        )
        return 1

    # The probe may take time. Revalidate both planning inputs immediately
    # before the first write; never apply a plan to marker/config state that
    # moved while the reviewer was running.
    if config_existed:
        current_config = A.project_config_document(root)
        if current_config["problem"] or current_config["raw"] != existing_raw:
            problem = (
                current_config["problem"]
                or ".ao/config.json changed while reviewer probe ran"
            )
            print(f"{C['red']}init refused{C['reset']}: {_project_refusal(problem)}")
            return 1
    elif os.path.lexists(config_path):
        print(
            f"{C['red']}init refused{C['reset']}: "
            ".ao/config.json appeared while reviewer probe ran"
        )
        return 1

    # Keep the marker read last: the concurrent-init boundary that authorizes
    # project writes is then the final observation before mutation begins.
    current_marker = _worktree_project_marker_document(root)
    if current_marker["fingerprint"] != marker_fingerprint:
        problem = (
            current_marker["problem"]
            if current_marker["exists"] and current_marker["problem"]
            else f"{PROJECT_MARKER} changed while reviewer probe ran"
        )
        print(f"{C['red']}init refused{C['reset']}: {_project_refusal(problem)}")
        return 1

    wrote, kept = [], []

    def put(rel, content, mode=None):
        p = os.path.join(root, rel)
        if os.path.lexists(p):
            kept.append(rel)
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding=UTF8) as fh:
            fh.write(content)
        if mode:
            os.chmod(p, mode)
        wrote.append(rel)

    # The config is replaced whole or not at all (#56); a crash never leaves it
    # empty of the opt-ins it held.
    if config_existed:
        kept.append(".ao/config.json")
        if added:
            A.write_project_config(root, config_text)
            wrote.append(".ao/config.json (+" + ", ".join(added) + ")")
    else:
        if os.path.lexists(config_path):
            kept.append(".ao/config.json")
        else:
            A.write_project_config(root, config_text)
            wrote.append(".ao/config.json")
        if added:
            wrote.append(".ao/config.json (+" + ", ".join(added) + ")")

    config_problem = _project_config_problem(root)
    if config_problem:
        print(f"{C['red']}init refused{C['reset']}: {_project_refusal(config_problem)}")
        return 1
    put(PROJECT_MARKER, PROJECT_MARKER_BYTES.decode("ascii"))
    marker_problem = _worktree_project_marker_problem(root)
    if marker_problem:
        print(f"{C['red']}init refused{C['reset']}: {_project_refusal(marker_problem)}")
        return 1
    put(".ao/board.md", BOARD_TEMPLATE)
    put(".ao/backlog.md", BACKLOG_TEMPLATE.format(name=name))
    put(".ao/authority.md", AUTHORITY_TEMPLATE)
    put(".ao/gates.json", json.dumps(detected_gates, indent=2) + "\n")
    put(".ao/ledger/.gitkeep", "")
    put(".ao/decisions/.gitkeep", "")
    put("semantic-review/.gitkeep", "")
    put("agent-mail/README.md", MAIL_README.format())

    gi = os.path.join(root, ".gitignore")
    lines = open(gi, encoding=UTF8).read().split("\n") if os.path.exists(gi) else []
    add = [l for l in ("agent-mail/*.md", "!agent-mail/README.md", ".ao/inbox/", ".ao/hold")
           if l not in lines]
    if add:
        with open(gi, "a", encoding=UTF8) as fh:
            fh.write("\n# agent-orchestrator: mail is transient; the ledger and reviews are not\n"
                     + "\n".join(add) + "\n")
        wrote.append(".gitignore (+%d)" % len(add))

    # Steering for whichever agent this repo uses. Kiro reads .kiro/steering;
    # Claude Code and most others read CLAUDE.md / AGENTS.md.
    # Steering goes only into files ao owns. The owner's rule files (CLAUDE.md,
    # AGENTS.md) are never appended to unless they ask with --rules: a tool that
    # writes instructions there has, from the reading agent's side, issued rules
    # nobody authorised — the second pilot's coordinator refused exactly that.
    from . import skillkit
    requested = skillkit.detect_agents(root, args.agent)[0]
    for ident, adapter in skillkit.setup_adapters(root):
        coordination = (adapter.get("directives") or {}).get("coordination")
        present = any(os.path.isdir(os.path.join(root, *path.split("/")))
                      for path in (adapter.get("detect") or {}).get("dirs") or [])
        if coordination and (ident == requested or present):
            put(coordination, STEERING_COORD.format())

    for rel in wrote:
        print(f"  {C['green']}wrote{C['reset']}  {rel}")
    for rel in kept:
        print(f"  {C['dim']}kept   {rel}{C['reset']}")

    exe = shutil.which("ao") or os.path.abspath(sys.argv[0])
    # The playbook and the MCP registration are the two things an agent needs to
    # behave like the architect from its first turn; both are written for every
    # agent this repository is seen to use. Ask the human before running init;
    # the tool itself does not ask.
    from . import skillkit
    _, agents = skillkit.detect_agents(root, args.agent)
    playbook_files = skillkit.install_playbook(root, agents, rules=getattr(args, "rules", False))
    for rel, what in playbook_files.items():
        print(f"  {C['green'] if what != 'kept' else C['dim']}{what:<8}{C['reset']} {rel}")
    if not getattr(args, "rules", False):
        print(f"  {C['dim']}rule files untouched — paste this into {' / '.join(skillkit.rule_file_names(root))} "
              f"yourself, or re-run with --rules:{C['reset']}")
        for line in skillkit.RULE_POINTER.split("\n"):
            print(f"      {line}")
    registered = {} if args.no_mcp else skillkit.register_mcp(root, agents, exe)
    # What init wrote, so `ao remove` can take exactly that away again.
    manifest = {"at": int(time.time()), "wrote": list(wrote) + [r for r, w in playbook_files.items() if w != "kept"],
                "mcp": [k for k, v in registered.items() if v == "registered"], "gitignore": bool(add)}
    with open(os.path.join(root, ".ao", "init-manifest.json"), "w", encoding=UTF8) as fh:
        json.dump(manifest, fh, indent=2)
    for agent, what in registered.items():
        print(f"  {C['green']}mcp{C['reset']}      {agent}: {what.splitlines()[0]}")
        if what.startswith("manual"):
            print("           " + "\n           ".join(what.splitlines()[1:]))
    if args.watchdog:
        code = subprocess.run([exe, "-C", root, "watchdog", "install"],
                              capture_output=True, text=True, encoding=UTF8, errors="replace").returncode
        print(f"  {C['green'] if code == 0 else C['red']}watchdog{C['reset']} "
              f"{'installed' if code == 0 else 'install failed — run ao watchdog install'}")

    hook_proof = _hook_execution_probe(_ao_hook_inventory(root))
    tone = C["green"] if hook_proof["installed"] else C["yellow"]
    print(f"  {tone}commit hook{C['reset']} {_hook_probe_text(hook_proof)}")

    probe_tone = C["green"] if reviewer_probe["ok"] else C["red"]
    print(
        f"  {probe_tone}reviewer probe{C['reset']} "
        f"{_reviewer_probe_text(reviewer_probe)}"
    )

    print(f"\n{C['b']}Next, for a person:{C['reset']}")
    for line in skillkit.next_steps(agents, registered):
        print(f"  · {line}")
    return 0



def cmd_decide(cfg, args):
    """Record an architect decision where it survives.

    `ao note` is a message: read, acted on, deleted. A decision is a fact about
    the project that the next architect — or the same one after a compaction —
    has to be able to find. It goes to the ledger, and to the implementer's
    mailbox so it is acted on; if it answers an open `ao ask`, that is closed too.
    """
    root = cfg["root"]
    if args.list:
        p = os.path.join(root, ".ao", "ledger", "decisions.jsonl")
        rows = []
        if os.path.exists(p):
            for line in open(p, errors="replace", encoding=UTF8):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        if not rows:
            print(f"{C['dim']}No decisions recorded.{C['reset']}")
            return 0
        for r in rows[-args.n:]:
            when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
            print(f"  {C['dim']}{when}{C['reset']}  {C['b']}{r['id']}{C['reset']}  {r['decision']}")
            if r.get("why"):
                print(f"           {C['dim']}{r['why'][:110]}{C['reset']}")
        return 0
    if not args.decision:
        print(f"usage: {C['b']}ao decide \"decision\" --why \"…\" [--answers D-123] "
              f"[--scope B2] [--urgent]{C['reset']}")
        return 1
    holder = A.architect_lock_holder(root)
    if holder and holder.get("pid") != os.getpid() and holder.get("pid") != os.getppid():
        print(f"{C['yellow']}another architect turn holds the lock{C['reset']} ({holder.get('who')}, pid {holder.get('pid')}, "
              f"since {time.strftime('%H:%M', time.localtime(holder.get('at', 0)))}) — two judges at once contradict each other; "
              f"recording anyway, check `ao decide --list`")
    rec = A.scan_record({"id": f"AD-{int(time.time())}", "at": int(time.time()), "decision": args.decision,
                         "why": args.why, "scope": args.scope, "answers": args.answers, "by": "architect"})
    # Chained, so a row added by hand cannot pass for a re-specification (#65).
    from .storage import append_chained_jsonl
    append_chained_jsonl(A.decisions_path(root), rec, A.DECISION_CHAIN, legacy_prefix=True)
    if args.answers:
        ans = A.answer(root, args.answers, "x " + args.decision if False else args.decision,
                       by="architect")
        print(f"  {C['green']}answered{C['reset']} {args.answers}" if ans else
              f"  {C['yellow']}no open question {args.answers}{C['reset']}")
    body = f"{args.decision}\n\n**Neden:** {args.why or '—'}"
    if args.scope:
        body += f"\n\n**Kapsam:** {args.scope}"
    body += f"\n\n_karar kaydı: {rec['id']}_"
    name = A.note(root, cfg, args.to, args.decision[:60], body, urgent=args.urgent)
    print(f"  {C['green']}recorded{C['reset']} {rec['id']}  →  {cfg['mailbox']}/{name}")
    return 0


def _since_marker(root):
    return os.path.join(root, ".ao", "ledger", "since.json")


def cmd_since(cfg, args):
    """What changed since I last looked — for an architect coming back.

    `ao digest` is a window; this is a delta, anchored on the moment you last
    asked. A resumed session after a compaction, or a human back from lunch, has
    one question — what happened while I was gone — and reconstructing it from
    the whole day's digest is the tool making them do its job.
    """
    root = cfg["root"]
    ref = args.ref or "last"
    now = time.time()
    if ref == "last":
        try:
            cut = json.load(open(_since_marker(root), encoding=UTF8))["at"]
        except Exception:
            cut = now - 86400
    elif A.re.fullmatch(r"\d+(\.\d+)?[hdm]", ref):
        n, unit = float(ref[:-1]), ref[-1]
        cut = now - n * {"m": 60, "h": 3600, "d": 86400}[unit]
    else:
        ts = A.sh(f"git log -1 --format=%ct {ref}", cwd=root)
        if not ts.isdigit():
            print(f"{C['red']}not a duration (2h, 1d), a git ref, or 'last': {ref}{C['reset']}")
            return 1
        cut = float(ts)
    mins = int((now - cut) / 60)
    print(f"{C['b']}since{C['reset']} {C['dim']}{mins // 60}h {mins % 60}m ago{C['reset']}")

    log = A.git_text(root, "log", f"--since=@{int(cut)}", "--pretty=%h|%s")
    commits = [l.split("|", 1) for l in log.split("\n") if "|" in l]
    print(f"\n  {C['b']}{len(commits)}{C['reset']} commit")
    for sha, subj in commits[:8]:
        print(f"    {C['dim']}{sha}{C['reset']} {subj[:72]}")

    def newer(rel):
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d)
                      if not f.startswith(".") and os.path.getmtime(os.path.join(d, f)) >= cut)
    revs = newer(cfg["reviews"])
    if revs:
        verdicts = dict(A.reviews(root, cfg["reviews"], limit=40))
        print(f"\n  {C['b']}{len(revs)}{C['reset']} review")
        for f in revs[-6:]:
            v = verdicts.get(f, "")
            col = C["green"] if "APPROVED" in v.upper() else C["yellow"]
            print(f"    {col}{v or '?':<14}{C['reset']} {f[:60]}")
    decs = [d for d in A.decisions(root) if d.get("asked_at", 0) >= cut or (d.get("answered_at") or 0) >= cut]
    if decs:
        print(f"\n  {C['b']}{len(decs)}{C['reset']} decision")
        for d in decs[-6:]:
            st = f"{C['green']}→ {d['answer']}{C['reset']}" if d["state"] == "answered" else f"{C['yellow']}open{C['reset']}"
            print(f"    {d['id']}  {d['question'][:56]}  {st}")
    mail = [m for m in A.mailbox(root, cfg["mailbox"])
            if os.path.getmtime(os.path.join(root, cfg["mailbox"], m)) >= cut]
    if mail:
        print(f"\n  {C['b']}{len(mail)}{C['reset']} message in the mailbox now")
        for m in mail[:6]:
            print(f"    {C['dim']}{m[:70]}{C['reset']}")
    notices = [n for n in A.notices(root, 50, include_suppressed=False) if n.get("at", 0) >= cut]
    if notices:
        print(f"\n  {C['b']}{len(notices)}{C['reset']} alert sent")
        for n in notices[:4]:
            print(f"    {C['dim']}{n['title']}: {n['msg'][:60]}{C['reset']}")
    b = A.board(root)
    print(f"\n  {C['dim']}board now: " + " · ".join(f"{k} {len(v)}" for k, v in b.items() if v) + C["reset"])

    if not args.no_mark:
        os.makedirs(os.path.dirname(_since_marker(root)), exist_ok=True)
        json.dump({"at": now}, open(_since_marker(root), "w", encoding=UTF8))
    return 0


def cmd_board(cfg, args):
    """Where every pre-authorised item is, blocked ones first.

    Order is deliberate: `running` and `blocked` are the two states a human can
    act on, and `blocked` is the one that goes unnoticed — work carried on past
    it, so nothing else in the panel looks wrong.
    """
    root = cfg["root"]
    for line in _mailbox_banner(cfg):
        print(line)
    b = A.board(root)
    path = os.path.join(root, ".ao", "board.md")
    if not os.path.exists(path):
        print(f"{C['yellow']}No board here.{C['reset']} Create {C['b']}.ao/board.md{C['reset']} with "
              f"`## running` / `## blocked` / `## queued` / `## verified` / `## done` sections\n"
              f"and one `- [ID] title · note: value` line per item.")
        return 0
    colours = {"running": C["green"], "blocked": C["red"], "queued": C["dim"],
               "verified": C["cyan"], "done": C["dim"]}
    # Eligible work first: queued items whose `needs:` are all done. This is the
    # dependency graph answering "what is next" without the implementer choosing
    # its own scope. A broken edge is named before anything else (#33).
    graph = A.board_graph(root)
    if getattr(args, "view", None) == "ready":
        for problem in graph["problems"]:
            print(f"{C['red']}board: {problem}{C['reset']}")
        for it in graph["ready"]:
            print(f"{it['id']}  {it['title']}")
        return 1 if graph["problems"] else 0
    for problem in graph["problems"]:
        print(f"{C['red']}{C['b']}BOARD{C['reset']}  {C['red']}{problem}{C['reset']}")
    rd = graph["ready"]
    if rd:
        print(f"\n{C['b']}{C['green']}READY{C['reset']} {C['dim']}({len(rd)}) — "
              f"dependencies satisfied{C['reset']}")
        for it in rd:
            role = f"  {C['cyan']}role:{it['role']}{C['reset']}" if it.get("role") else ""
            print(f"   {C['b']}{it['id']}{C['reset']}  {it['title']}{role}")
    for st in ("running", "blocked", "queued", "verified", "done"):
        items = b[st]
        if not items:
            continue
        print(f"\n{C['b']}{colours[st]}{st.upper()}{C['reset']} {C['dim']}({len(items)}){C['reset']}")
        for it in items:
            notes = "  ".join(f"{C['dim']}{k}:{C['reset']} {v}" if v else f"{C['dim']}{k}{C['reset']}"
                              for k, v in it["notes"].items())
            print(f"   {C['b']}{it['id']}{C['reset']}  {it['title']}" + (f"   {notes}" if notes else ""))
            if st in ("running", "queued"):
                # Named when the item is registered, while widening or splitting is cheap (#35).
                for advice in A.boundary_advice(root, cfg, it):
                    print(f"      {C['yellow']}boundary: {advice}{C['reset']}")
    if not any(b.values()):
        print(f"{C['dim']}Board is empty.{C['reset']}")
    return 0


def cmd_source(cfg, args):
    """External work queues: what is configured, what is waiting, what may enter.

    Admission is the whole point of this command. Pulling an issue is free and
    carries no authority; a tracker item is something a person wrote, not a
    specification anyone verified. An item enters the board only with a written
    acceptance boundary, because the alternative — an agent inferring scope from
    a title — is how a one-line bug fix becomes a refactor nobody asked for.

    `ao` does not judge which items qualify: it has no model and no tracker
    credential. It enforces the rule; an architect turn makes the call and writes
    it into the inbox file.
    """
    root = cfg["root"]
    sc = A.sources(root)
    if not sc:
        print(f"{C['yellow']}No sources configured.{C['reset']}  Create {C['b']}.ao/sources.json{C['reset']}:\n"
              f'  {{"bound_root": "{root}", "wip_limit": 1, "refill_below": 3,\n'
              f'   "sources": [{{"id": "linear-x", "kind": "mcp", "server": "linear",\n'
              f'                "select": {{"team": "…", "state": "Todo"}}}}]}}')
        return 0

    files = A.inbox_files(root)
    if args.action in ("list", "status"):
        bd = A.board(root)
        print(f"{C['b']}bound to{C['reset']}  {sc.get('bound_root', root)}")
        for src in sc["sources"]:
            sel = " ".join(f"{k}={v}" for k, v in (src.get("select") or {}).items())
            wb = (src.get("writeback") or {})
            wbs = (f"{C['yellow']}writes back{C['reset']}" if wb.get("enabled")
                   else f"{C['dim']}read-only{C['reset']}")
            print(f"  {C['b']}{src['id']}{C['reset']}  {src.get('kind','mcp')}:{src.get('server','?')}"
                  f"  {C['dim']}{sel}{C['reset']}  {wbs}")
        depth = len(bd["queued"])
        low = depth < sc["refill_below"]
        col = C["yellow"] if low else C["green"]
        print(f"\n{C['b']}queue{C['reset']}  {col}{depth} admitted{C['reset']}"
              f"{C['dim']} · refill below {sc['refill_below']} · wip limit {sc['wip_limit']}{C['reset']}"
              + (f"  {C['yellow']}← needs a refill pass{C['reset']}" if low else ""))
        print(f"{C['b']}inbox{C['reset']}  {len(bd['inbox'])} on the board, "
              f"{len(files)} pull file(s) not yet imported")
        return 0

    # import — the only place an item crosses from "someone wrote this" to
    # "an agent may work on it unattended"
    if not files:
        print(f"{C['dim']}Nothing in .ao/inbox/ to import.{C['reset']}")
        return 0
    admitted = held = 0
    for f in files:
        try:
            doc = json.load(open(f, encoding=UTF8))
        except Exception as e:
            print(f"{C['red']}skip{C['reset']} {os.path.basename(f)}: unreadable ({e})")
            continue
        err = A.binding_error(root, doc.get("bound_root"))
        if err:
            print(f"{C['red']}REFUSED{C['reset']} {os.path.basename(f)}: {err}")
            continue
        sid = doc.get("source", "?")
        for it in doc.get("items", []):
            iid, title = it.get("id"), it.get("title", "")
            if not iid:
                continue
            if any(x["id"] == iid for st in A.BOARD_STATES for x in A.board(root)[st]):
                continue                                  # already on the board
            acc = (it.get("acceptance") or "").strip()
            if acc:
                A.board_append(root, "queued",
                               f"- [{iid}] {title} · source: {sid} · acceptance: {acc}")
                dg = A.plan_digest(root, iid)
                if dg:
                    A.record_plan(root, iid, dg)   # baseline: the plan as admitted
                admitted += 1
            else:
                why = it.get("shape") or "no acceptance boundary written"
                A.board_append(root, "inbox",
                               f"- [{iid}] {title} · source: {sid} · needs: {why}")
                held += 1
        os.rename(f, f + ".imported")
    print(f"{C['green']}{admitted} admitted{C['reset']} to queued · "
          f"{C['yellow']}{held} held{C['reset']} in inbox (no acceptance boundary)")
    return 0


def cmd_hold(cfg, args):
    """Stop this project's agents and keep them stopped.

    A kill switch that only kills is not a switch: the watchdog sees an idle
    session with open work and restarts it within a couple of minutes. So the
    stop and the lock are one operation, and every restart path checks the lock
    first.

    It stops *every* agent process whose cwd is this repository, not just the one
    we most recently started. Detached turns accumulate — this project found
    fifteen live `kiro-cli` processes in one tree, four of them still burning
    CPU, because each nudge spawned one and nothing ever reaped them. Tracking
    only our own last child made the rest invisible.
    """
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    path = os.path.join(root, A.HOLD_FILE)

    if args.action == "status":
        st = A.hold_state(root)
        if not st:
            print(f"{C['green']}running free{C['reset']} — no hold")
        else:
            print(f"{C['red']}HELD{C['reset']} by {C['b']}{st.get('by')}{C['reset']} "
                  f"for {st['minutes']}m: {st.get('reason','')}")
        pids = A.agent_pids(root, adapter)
        print(f"{len(pids)} agent process(es) in this tree" + (f": {pids}" if pids else ""))
        return 0

    if args.action == "release":
        if not os.path.exists(path):
            print(f"{C['dim']}No hold to release.{C['reset']}")
            return 0
        st = A.hold_state(root) or {}
        os.remove(path)
        print(f"{C['green']}released{C['reset']} after {st.get('minutes', 0)}m")
        if args.note:
            # The agent wakes into a tree it did not change. Say what moved, or it
            # spends its first turns rediscovering it — or worse, mistrusting it.
            box = os.path.join(root, cfg["mailbox"])
            os.makedirs(box, exist_ok=True)
            impl, arch = A.mail_names(cfg)
            name = f"{datetime.now():%Y%m%d-%H%M}-{arch}-to-{impl}-INFO-hold-released.md"
            with open(os.path.join(box, name), "w", encoding=UTF8) as fh:
                fh.write(f"# INFO — hold released\n\nDuruldu: {st.get('minutes',0)} dakika\n"
                         f"Sebep: {st.get('reason','')}\n\n## Bu sürede ne değişti\n\n"
                         f"{args.note}\n")
            print(f"handover note → {cfg['mailbox']}/{name}")
        return 0

    # hold — stops unattended turns only. An interactive session has a person in
    # it who did not ask to be stopped; the lock still keeps the watchdog from
    # starting anything new.
    pids = A.agent_pids(root, adapter, headless_only=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"by": args.by, "reason": args.reason or "manual intervention",
               "at": int(time.time()), "stopped": pids},
              open(path, "w", encoding=UTF8), indent=2)
    if not pids:
        dead = A.orphans(root, adapter)
        if dead:
            print(f"clearing {len(dead)} orphaned process(es) left by ended turns: {dead}")
            A.sweep_orphans(dead)
        if A.unplaced_agent_pids(root, adapter):
            print(f"{C['yellow']}hold set{C['reset']} — no agent turn could be placed in this tree")
            return _hold_unplaced(root, adapter)
        print(f"{C['yellow']}hold set{C['reset']} — no agent turn was running")
        return 0
    dead = A.orphans(root, adapter)
    if dead:
        print(f"clearing {len(dead)} orphaned process(es) left by ended turns: {dead}")
        A.sweep_orphans(dead)
    print(f"stopping {len(pids)} process(es): {pids}")
    for pid in pids:
        A.kill_turn(pid, signal.SIGTERM)     # whole group; let it finish the write it is in
    deadline = time.time() + args.grace
    while time.time() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            break
        time.sleep(0.5)
    alive = [p for p in pids if _alive(p)]
    for pid in alive:
        A.kill_turn(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    if os.name == "nt":
        # taskkill /T /F ends a tree at once; nothing exited on request (#71).
        print(f"{C['red']}HELD{C['reset']} — {len(pids)} process tree(s) stopped by force "
              "(Windows). The watchdog will not restart while .ao/hold exists.")
    else:
        print(f"{C['red']}HELD{C['reset']} — {len(pids) - len(alive)} exited on request, "
              f"{len(alive)} killed. The watchdog will not restart while .ao/hold exists.")
    return _hold_unplaced(root, adapter)


def _hold_unplaced(root, adapter):
    """Say which agent processes a hold could not stop because they cannot be placed (#71)."""
    unplaced = A.unplaced_agent_pids(root, adapter)
    if not unplaced:
        return 0
    print(f"{C['red']}{len(unplaced)} agent process(es) were not stopped{C['reset']}: Windows exposes no "
          f"process working directory, so they cannot be placed in this tree: {unplaced}. Stop them "
          "by hand if they work here; the hold keeps the watchdog from starting another.")
    return 1


def cmd_writers(cfg, args):
    """Who is writing in this tree — turns, not processes, with orphans set aside.

    This is the measurement a single-writer rule should run. The process table
    answers a different question: one turn is a wrapper, a runtime and an engine,
    and a turn that ended can leave the last two behind at 0% CPU with the repo
    as their cwd. Counting processes reported four writers where there were
    none, and an implementer that trusted the count refused to write for three
    and a half hours. Exit status is 0 for at most one live turn, 1 otherwise;
    `--clean` stops orphans only — never a live turn, never a person's session.
    """
    root = cfg["root"]
    impl = cfg.get("implementer") or {}
    adapter = A.load_adapter(impl.get("adapter", "")) if impl else {}
    roots, dead = A.writers(root, adapter)
    unplaced = A.unplaced_agent_pids(root, adapter)
    if unplaced:
        # A count that cannot see a writer must not report none (#71).
        print(f"{C['red']}writers unknown{C['reset']} — {len(unplaced)} agent process(es) cannot be placed "
              f"in a tree: Windows exposes no process working directory ({unplaced})")
        return 1
    table = A._proc_table()
    rows = []
    for pid in roots:
        args_ = (A.sh(f"ps -o etime=,args= -p {pid}") or "").strip()
        et, _, cmd = args_.partition(" ")
        rows.append({"pid": pid, "elapsed": et, "headless": A._is_headless(pid),
                     "tty": table.get(pid, (0, 0, "?"))[2], "cmd": cmd.strip()[:90]})
    if args.clean and dead:
        A.sweep_orphans(dead)
        left = [p for p in dead if A._pid_alive(p)]
        cleaned = [p for p in dead if p not in left]
    else:
        cleaned, left = [], dead
    if args.json:
        print(json.dumps({"writers": len(roots), "turns": rows, "orphans": left,
                          "cleaned": cleaned}, ensure_ascii=False))
        return 0 if len(roots) <= 1 else 1
    if not roots:
        print(f"{C['green']}0 writers{C['reset']} — no live turn in this tree")
    else:
        colour = C['green'] if len(roots) == 1 else C['red']
        print(f"{colour}{len(roots)} writer(s){C['reset']}")
        for r in rows:
            kind = "headless" if r["headless"] else f"interactive tty={r['tty']}"
            print(f"   {r['pid']:>6}  {r['elapsed']:>10}  {kind:<22} {r['cmd']}")
    if cleaned:
        print(f"   cleaned {len(cleaned)} orphan(s): {cleaned}")
    if left:
        print(f"   {C['yellow']}{len(left)} orphan(s){C['reset']} left by ended turns, not counted: {left}"
              + ("" if args.clean else "  (`ao writers --clean` stops them)"))
    return 0 if len(roots) <= 1 else 1


def cmd_fanout(cfg, args):
    """May a fan-out of N sub-agents start now — and what did the last one cost.

    `ok` answers from three facts (hard cap, recent limit hit, provider window);
    `record` writes what a run cost so the next estimate is empirical; `history`
    lists the runs. Exit 1 on any refusal, so a coordinator can gate on it.
    """
    root = cfg["root"]
    if args.action == "record":
        if args.agents is None:
            print("--agents is required"); return 2
        rec = A.record_fanout(root, args.agents, args.done, args.errors, args.tokens, args.note)
        print(f"{C['green']}recorded{C['reset']} {json.dumps(rec, ensure_ascii=False)}")
        return 0
    if args.action == "history":
        rows = A.fanout_history(root, args.limit)
        if not rows:
            print("no fan-outs recorded"); return 0
        for r in rows:
            when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
            flag = f"  {C['red']}limit hit{C['reset']}" if r.get("limit_hit") else ""
            print(f"  {when}  {r['agents']:>3} agents  done {r.get('done','?'):>3}  "
                  f"errors {r.get('errors','?'):>3}  tokens {r.get('tokens','?')}{flag}"
                  + (f"  — {r['note']}" if r.get('note') else ""))
        return 0
    agents = args.agents
    if args.roots and args.per_root:
        # A pipeline's fan-out is not known up front: 7 roots that each may
        # spawn up to 12 branches is a bound of 7 + 7×12. Gate on the bound.
        agents = args.roots + args.roots * args.per_root
        print(f"{C['dim']}pipeline bound: {args.roots} roots + {args.roots}×{args.per_root} branches = {agents} agents{C['reset']}")
    if agents is None:
        print("--agents N, or --roots R --per-root K for a pipeline, is required"); return 2
    v = A.fanout_verdict(root, cfg, agents, args.per_agent_tokens, args.provider)
    if args.json:
        print(json.dumps(v, ensure_ascii=False))
        return 0 if v["ok"] else 1
    colour = C['green'] if v["ok"] else C['red']
    print(f"{colour}{v['verdict'].upper()}{C['reset']}  {v['agents']} agents × ~{v['per_agent_tokens']:,} "
          f"tokens ({v['per_agent_source']}) ≈ {v['estimated_tokens']:,} tokens")
    w = v.get("window")
    if w:
        print(f"   {args.provider} window: {w['pct']}% used, {w['window']} window, resets in {w['resets_in']}"
              + (f"; {v['spent_this_window']:,} tokens fanned out in it so far" if v['spent_this_window'] else ""))
    for r in v["reasons"]:
        print(f"   {C['yellow'] if not v['ok'] else C['dim']}· {r}{C['reset']}")
    if v["ok"]:
        print(f"   {C['dim']}after the run: ao fanout record --agents {v['agents']} --done D --errors E --tokens T{C['reset']}")
    return 0 if v["ok"] else 1


def cmd_email(cfg, args):
    """The red channel: e-mail through a provider - formsubmit.co, or any SMTP server (#74)."""
    from . import email
    provider = getattr(args, "provider", None) or "formsubmit"
    if args.action == "setup" and provider != "formsubmit":
        password = None
        if getattr(args, "password_env", None):
            # Read once from the environment: a password on the command line stays in history.
            password = os.environ.get(args.password_env)
            if password is None:
                print(f"{C['red']}not saved{C['reset']}: ${args.password_env} is not set"); return 2
        try:
            c = email.save_provider(provider, host=args.host, port=args.port, user=args.user,
                                    password=password, to=args.to, tls=args.tls,
                                    **{"from": args.sender})
        except ValueError as exc:
            print(f"{C['red']}not saved{C['reset']}: {exc}"); return 2
        print(f"{C['green']}saved{C['reset']} {email.CONF} (0600) provider={provider} → {c.get('to')}")
        print(f"now: {C['b']}ao email test{C['reset']}")
        return 0
    if args.action == "setup":
        if args.token:
            c = email.save(args.token, to=args.to)
            print(f"{C['green']}saved{C['reset']} {email.CONF} (0600) → {c.get('to') or 'address hidden behind token'}")
            print(f"now: {C['b']}ao email test{C['reset']}")
            return 0
        print(email.SETUP.format(conf=email.CONF))
        return 0
    c = email.config()
    if args.action == "status":
        if not c:
            print(f"{C['yellow']}not configured{C['reset']} — ao email setup"); return 1
        print(f"{C['green']}configured{C['reset']} provider={c['provider']} to={c.get('to') or '(hidden)'} file={email.CONF}")
        return 0
    if args.action == "test":
        if not c:
            print(f"{C['red']}not configured{C['reset']} — ao email setup"); return 1
        ok = email.send("test", f"ao e-posta kanalı çalışıyor. Proje: {cfg['root']}\n"
                        f"Kırmızı alarmlar buraya gelir: bir saatten uzun süren turuncu durumlar, "
                        f"tükenmiş kota, başarısız mimar uyandırma.", cfg["root"])
        print(f"{C['green']}sent{C['reset']}" if ok else f"{C['red']}relay refused{C['reset']} — token/activation?")
        return 0 if ok else 1
    return 0


def cmd_alarms(cfg, args):
    """Live alarm episodes and their level; `test` rings every channel."""
    root = cfg["root"]
    project = A.project_key(root)
    if args.action in ("snooze", "unsnooze"):
        key = getattr(args, "key", None)
        if not key:
            print(f"usage: ao alarms {args.action} <key>"
                  + (" --until YYYY-MM-DD --why '…'" if args.action == "snooze" else ""))
            return 2
        if args.action == "unsnooze":
            gone = A.alarm_unsnooze(project, key)
            print(f"{'unsnoozed' if gone else 'no snooze for'} {key}")
            return 0
        try:
            until = time.mktime(time.strptime(getattr(args, "until", None) or "", "%Y-%m-%d"))
        except ValueError:
            print("snooze needs --until YYYY-MM-DD")
            return 2
        if until <= time.time():
            print("--until must be a date in the future")
            return 2
        why = (getattr(args, "why", None) or "").strip()
        if not why:
            print("snooze needs --why: a snoozed alarm says why nobody can act on it yet")
            return 2
        A.alarm_snooze(project, key, until, by=getattr(args, "by", None) or "human", why=why)
        print(f"snoozed {key} until {time.strftime('%d %b %Y', time.localtime(until))} — "
              "it stays in ao alarms and rings again from that date")
        return 0
    if args.action == "test":
        from .watchdog import notify
        lvl = args.level or "orange"
        ok = notify(f"{project}: alarm testi", f"{lvl} seviyesi testi — ao alarms test", root,
                    key=f"alarm-test-{int(time.time())}", window=0, audience="human", level=lvl)
        print(f"{lvl}: desktop+telegram {'sent' if ok else 'suppressed'}"
              + (" · e-mail attempted (see ao notices)" if lvl == "red" else ""))
        return 0
    alive = A.active_alarms(project)
    for full, snooze in sorted(A.load_alarm_snoozes().items()):
        owner, _, key = full.partition(":")
        if owner == project and A.alarm_snoozed(project, key):
            print(f"  {C['dim']}snoozed{C['reset']} {key:<32} until "
                  f"{datetime.fromtimestamp(snooze['until']).strftime('%d %b')}  "
                  f"{C['dim']}by {snooze.get('by')}: {(snooze.get('why') or '')[:60]}{C['reset']}")
    if not alive:
        print(f"{C['green']}no live alarms{C['reset']}"); return 0
    for e in alive:
        tone = {"red": C['red'], "orange": C['yellow']}.get(e.get("ring"), C['dim'])
        since = datetime.fromtimestamp(e["first"]).strftime("%d %b %H:%M")
        print(f"  {tone}{e.get('ring', '?'):<7}{C['reset']} {e['key']:<32} since {since}  "
              f"×{e.get('count', 1)}  {C['dim']}{e.get('title', '')[:50]}{C['reset']}"
              + (f"  mailed {datetime.fromtimestamp(e['red_sent']).strftime('%H:%M')}" if e.get('red_sent') else ""))
        if e.get("evidence"):
            for line in A.evidence_lines(e["evidence"]):
                print(f"          {C['dim']}{line}{C['reset']}")
    return 0


def _watchdog_debug(cfg, args):
    """`ao watchdog explain` runs one dry cycle and shows every measurement and
    verdict; `ao watchdog trace` shows the recorded cycles. The question both
    answer is the one that cost the most this project: why did it not act?"""
    from types import SimpleNamespace
    from . import watchdog as W
    root = cfg["root"]
    if args.action == "explain":
        ns = SimpleNamespace(root=root, idle_minutes=S.get(cfg, "watchdog.idle_minutes"), dry_run=True, prompt=W.NUDGE_PROMPT)
        W.run(ns)
        print(f"\n{C['b']}{C['mag']}── MEASUREMENTS ──{C['reset']}")
        for k, v in W._FACTS.items():
            print(f"   {k:<22} {v}")
        print(f"\n{C['b']}{C['mag']}── DECISIONS (in order) ──{C['reset']}")
        for i, line in enumerate(W._TRACE, 1):
            last = i == len(W._TRACE)
            print(f"   {C['b'] if last else ''}{i:>2}. {line}{C['reset']}")
        return 0
    rows = W.cycles(root, args.last or 20)
    if not rows:
        print("no cycles recorded yet"); return 0
    for r in rows:
        when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M:%S")
        f = r.get("facts", {})
        print(f"  {when}  {C['b']}{(r.get('verdict') or '')[:70]:<70}{C['reset']}  "
              f"{C['dim']}idle {f.get('idle_s', '?')}s · writers {f.get('writers', '?')} · inbox {f.get('inbox', '?')} "
              f"· queued {f.get('queued', '?')}{' · standing request' if f.get('standing_request') else ''}{C['reset']}")
    return 0


def cmd_skill(cfg, args):
    """The playbook, rendered for the agents this repository uses."""
    from . import skillkit
    root = cfg["root"]
    if args.action == "show":
        print(skillkit.playbook()[1])
        return 0
    _, agents = skillkit.detect_agents(root, args.agent)
    for rel, what in skillkit.install_playbook(root, agents).items():
        print(f"  {C['green'] if what != 'kept' else C['dim']}{what:<8}{C['reset']} {rel}")
    print(f"{C['dim']}agents: {', '.join(sorted(agents))} · regenerate any time; hand edits outside the markers survive{C['reset']}")
    return 0


def _actor_grant_problems(cfg):
    """Doctor findings for each configured actor whose tool grant admits a bypass (#58)."""
    from . import allowlist as AL
    grants = []
    impl = cfg.get("implementer") or {}
    if impl.get("adapter"):
        adapter = A.load_adapter(impl["adapter"])
        grants.append(("implementer", impl["adapter"],
                       (adapter.get("resume") or {}).get("argv") or [], adapter.get("options") or {}))
    for role in ("architect", "reviewer"):
        actor = cfg.get(role) or {}
        if actor.get("argv"):
            grants.append((role, actor.get("id") or actor.get("name") or role, actor["argv"], {}))
    for fallback in (cfg.get("reviewer") or {}).get("fallbacks") or []:
        if fallback.get("argv"):
            grants.append(("reviewer fallback", fallback.get("id") or "fallback", fallback["argv"], {}))
    out = []
    for role, name, argv, options in grants:
        text = AL.describe(role, name, AL.problems(argv, options, role=role))
        if text:
            out.append((f"actor-grant:{role.replace(' ', '-')}", text))
        # A reviewer reads, and starts no MCP server it was not given (#24).
        if role.startswith("reviewer"):
            reach = AL.reviewer_problems(argv)
            if reach:
                out.append((f"reviewer-tools:{name}", f"{role} ({name}): " + "; ".join(reach)))
    return out


def _implementer_commit_guard(cfg):
    """Which guard holds commits for the configured implementer, in one sentence (#109)."""
    from . import allowlist as AL
    impl = cfg.get("implementer") or {}
    if not impl.get("adapter"):
        return None
    adapter = A.load_adapter(impl["adapter"])
    argv = (adapter.get("resume") or {}).get("argv") or []
    if AL.problems(argv, adapter.get("options") or {}):
        return (f"{impl['adapter']} can commit around the hook; an ungranted commit is not "
                "prevented but surfaces within one watchdog cycle (landed-tree check)")
    return f"{impl['adapter']}'s grant admits no hook bypass; ao commit and the hook hold commits"


def doctor_problems(cfg):
    """What `ao doctor --check` acts on: conditions a person must fix, as (key, text)."""
    from .watchdog import wake_error, STATE_DIR
    root = cfg["root"]
    key = A.project_key(root)
    out = []
    strict_matrix = M.is_strict(cfg)
    if strict_matrix:
        try:
            matrix_resolution = M.resolve(cfg, require_independent=False)
        except M.MatrixError as exc:
            out.append(("capability-matrix", "; ".join(exc.problems[:3])))
        else:
            if not any(route["eligible"] for route in matrix_resolution["reviewers"]):
                out.append((
                    "no-independent-reviewer",
                    "capability matrix reviewer chain has no binding outside the implementer binding and model family",
                ))
    hb = A.heartbeat_age(root)
    if hb is not None and hb > 360:
        out.append(("watchdog-dead", f"watchdog silent for {hb // 60}m — launchctl / ao watchdog status"))
    we = wake_error(os.path.join(STATE_DIR, f"escalate-{key}.log"))
    if we and we["kind"] in ("binary", "session"):
        try:
            when = time.mktime(time.strptime(we["when"], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            when = time.time()
        if time.time() - when < 24 * 3600:
            out.append(("wake-failed", f"last architect wake failed ({we['kind']}): {we['text'][:90]}"))
    from . import telegram as _tg, email as _em
    if not (_tg.config() or _em.config()):
        out.append(("no-channel", "no human channel beyond desktop notifications — ao email setup"))
    for sib, age in A.stale_siblings(root).items():
        out.append((f"sibling-dead:{sib}", f"{sib}: watchdog silent for {age // 60}m"))
    br = A.burn_rate(root)
    samples = A.credit_samples(root)
    last = samples[-1] if samples else None
    credits = _credits_problem(br, last)
    if credits:
        out.append(credits)
    from . import features as _features
    try:
        from .watchdog import load_state as _load_state
        blind = (_load_state(root) or {}).get("credit_check_problem")
    except Exception:
        blind = None
    # Paged only while the implementer is being driven: a check that cannot read
    # usage matters because exhaustion stops the work, and an owner who stopped the
    # work on purpose does not need an hourly page saying so. Plain doctor always
    # prints it.
    if blind and _features.enabled(cfg, "nudge"):
        out.append(("credits-check", f"credit usage cannot be read ({blind.get('reason')}) — "
                                     f"the exhaustion alarm is blind until it reads again"))
    try:
        msgs_p, _ = A.session_paths(cfg)
        # The watchdog's heartbeat now comes before its transcript check, so a
        # transcript it cannot find no longer looks like a dead watchdog (#71).
        if (cfg.get("implementer") or {}).get("session") and _features.enabled(cfg, "nudge") \
                and (not msgs_p or not os.path.exists(msgs_p)):
            out.append(("transcript-missing", "the implementer's transcript cannot be found — the "
                        "watchdog has nothing to watch and restarts nobody; check implementer.session"))
        if msgs_p and os.path.exists(msgs_p) and time.time() - os.path.getmtime(msgs_p) < 3600 \
                and not A.read_tail(msgs_p, 2_000_000):
            out.append(("transcript-blind", "fresh transcript, nothing parsed — the agent CLI's format changed"))
    except Exception:
        pass
    # A source tree no gate exercises is a tree nothing verifies (#5).
    try:
        with open(os.path.join(root, ".ao", "gates.json"), encoding=UTF8) as fh:
            gate_spec = json.load(fh)
    except (OSError, ValueError):
        gate_spec = None
    if gate_spec is not None:
        for tree, chain, files in A.gate_coverage_gaps(root, gate_spec, S.get(cfg, "gates.coverage_min_files")):
            out.append((f"gate-uncovered:{tree}", f"{tree}/ holds {files} {chain} files that no gate in "
                                                  ".ao/gates.json exercises"))
    # A setting that is written but not used says one thing and does another (#74).
    for name, text in S.problems(cfg):
        out.append((f"setting:{name}", text))
    if len(A.deferred_open(root)) >= 3:
        out.append(("deferred-pile", f"{len(A.deferred_open(root))} deferred actions waiting — ao catchup"))
    # The playbook exists but nothing the agent reads points at it: the rules are
    # written and not in force. init prints the pointer; this is the reminder.
    from . import skillkit
    if skillkit.rules_wired(root) is False:
        named = ", ".join(skillkit.rule_file_names(root) + skillkit.steering_dirs(root))
        out.append(("rules-not-wired", f"no rule file ({named}) points at the ao playbook — "
                                       "paste the pointer or run `ao init --rules`"))
    # Two critical roles on one rate-limited pool fail together: the day the
    # architect ran dry, so did the reviewer, and the run locked.
    arch_bin = os.path.basename(((cfg.get("architect") or {}).get("argv") or [""])[0])
    rv = cfg.get("reviewer") or {}
    rv_bin = os.path.basename((rv.get("argv") or [""])[0])
    if not strict_matrix and arch_bin and arch_bin == rv_bin and not rv.get("fallbacks"):
        out.append(("shared-pool", f"architect and reviewer both run `{arch_bin}` on one quota pool and the reviewer has no "
                                   f"fallback — let keyflip rotate accounts before a spawn (`ao config set "
                                   f"keyflip.rotation on --machine`), use another model family, or, on a "
                                   f"machine without keyflip, add reviewer.fallbacks"))
    # A dependency the board cannot resolve makes READY wrong without a word (#33).
    try:
        graph_problems = A.board_graph(root)["problems"]
    except Exception:
        graph_problems = []
    if graph_problems:
        out.append(("board-graph", "; ".join(graph_problems[:3])
                    + (f" and {len(graph_problems) - 3} more" if len(graph_problems) > 3 else "")
                    + " — ao board"))
    # A boundary its own acceptance cannot fit is found before the work, not in it (#35).
    try:
        conflicts = [f"{item['id']}: {text}" for state in ("running", "queued")
                     for item in A.board(root)[state] for text in A.boundary_conflicts(root, item)]
    except Exception:
        conflicts = []
    if conflicts:
        out.append(("boundary-conflict", "; ".join(conflicts[:3])
                    + (f" and {len(conflicts) - 3} more" if len(conflicts) > 3 else "") + " — ao board"))
    # Green branches make a red main when nobody ran their merge (#39).
    try:
        unverified = A.unverified_merges(root, cfg)
    except Exception:
        unverified = []
    if unverified:
        out.append(("unverified-merge", "; ".join(f"{sha[:12]}: {why}" for sha, why in unverified[:3])
                    + (f" and {len(unverified) - 3} more" if len(unverified) > 3 else "")
                    + " — ao merge-check <branch> before merging"))
    # A reviewer whose adapter cannot deny tools is not a reviewer (#88).
    reviewer_adapter = (cfg.get("reviewer") or {}).get("adapter")
    if reviewer_adapter:
        eligible, why = A.reviewer_eligibility(A.load_adapter(reviewer_adapter, root))
        if not eligible:
            out.append((f"reviewer-ineligible:{reviewer_adapter}", f"the reviewer runs {reviewer_adapter}, which may not "
                        f"review: {why} — ao role set reviewer <adapter> --model <model>"))
    # A configured adapter whose command this machine lacks would fail at its first spawn (#89).
    try:
        absent = A.absent_adapter_binaries(cfg)
    except Exception:
        absent = []
    for actor, ident, binaries in absent:
        out.append((f"adapter-binary:{actor}", f"{actor} runs the {ident} adapter, but {' or '.join(binaries)} "
                    "is not on this machine (PATH, binaries.extra_dirs, the usual install directories)"))
    # Agent configuration checked by AgentShield's categories, natively (#14).
    try:
        for category, text in A.agent_config_findings(root):
            out.append((f"agent-config:{category}", text))
        for text in A.verify_content(root):
            out.append(("content-drift", text))
    except Exception:
        pass
    # Mail is governance; a store ahead of its private copy is on one disk (#83).
    try:
        sync = A.mail_sync_state(root, cfg)
    except Exception:
        sync = None
    if sync:
        local, remote, problem = sync
        if problem:
            out.append(("mail-sync", problem + " — ao mail sync refuses it"))
        elif local and local != remote:
            out.append(("mail-sync", "the message store is ahead of its private copy — ao mail sync"))
    # An implementer with nothing pre-authorised to pick up next stalls the moment
    # the architect is away; two READY items is the floor.
    try:
        ready = len(A.ready(root)) if hasattr(A, "ready") else len(A.board(root)["queued"])
        if ready < 2 and not A.board(root)["running"]:
            out.append(("queue-shallow", f"{ready} READY item(s) and nothing running — refill .ao/backlog.md before the architect is away"))
    except Exception:
        pass

    # Static intent is only the safety precondition for running a hook.  A green
    # commit-hook result requires Git itself to resolve the active path, execute
    # it with an isolated synthetic index, and return AO's nonce-bound refusal.
    # Pre-push remains informational: #59 is about commit authority.
    hook_inventory = _ao_hook_inventory(root)
    proof = _hook_execution_probe(hook_inventory)
    if hook_inventory["error"]:
        out.append(("commit-hook", proof["detail"] + " — ao hooks status"))
    else:
        active = _active_hook_targets(hook_inventory)
        commit = active["pre-commit"]
        misplaced = [
            target for target in hook_inventory["targets"]
            if not target["active"]
            and target["role"] == "pre-commit"
            and target["reachability"] == "potentially-effective"
            and _state_base(target["static_state"]) in (
                "current-local", "current-scoped", "legacy", "ambiguous-ao"
            )
        ]
        reasons = []
        if not proof["installed"]:
            reasons.append("execution proof failed: " + proof["detail"])
        if misplaced:
            reasons.append(
                "potentially-effective misplaced pre-commit: "
                + ", ".join(target["path"] for target in misplaced[:3])
            )
        if reasons:
            if commit["repository_source"] and commit["track_state"] == "tracked" \
                    and commit["crlf_only"]:
                repair = "git restore -- .githooks/pre-commit"
            elif misplaced:
                needs_allow = any(
                    target["directory_class"] in ("shared", "external")
                    or target["globally_configured"]
                    for target in misplaced
                )
                flag = " --allow-shared-hooks" if needs_allow else ""
                repair = f"ao hooks uninstall{flag}, then ao hooks install{flag}"
            elif _project_enrollment(root)["state"] == "legacy":
                repair = PROJECT_ADOPT_HINT + ", then ao hooks install"
            elif _state_base(commit["static_state"]) not in (
                "current-local", "current-scoped"
            ):
                repair = "ao hooks install (restore tracked hooks with Git)"
            else:
                repair = "ensure the hook can resolve this AO executable, then ao hooks status"
            out.append(("commit-hook", "; ".join(reasons) + f" — {repair}"))
    out.extend(_actor_grant_problems(cfg))
    if _project_enrollment(root)["state"] in ("enrolled", "legacy"):
        try:
            stray = A.commits_without_grant(root)
        except Exception:
            stray = []
        if stray:
            out.append(("landed-without-grant",
                        f"{len(stray)} commit(s) landed with a tree no grant bound: "
                        + ", ".join(sha[:12] for sha in stray[:5])
                        + " — a path staged after commit-check, or a commit made outside ao"))
    return out


# Conditions the watchdog raises itself, by the key it raises them under. Two
# alarms for one condition double the ladder - two ids, two levels, two mails -
# and they cannot share an id, because every touch rewrites the level. So the
# doctor rings these only while the watchdog's own alarm is quiet: a stopped
# watchdog, which is what the doctor check is for, lets it go quiet.
DOCTOR_WATCHDOG_ALARMS = {"credits-exhaust": "credits-exhaust",
                          "wake-failed": "architect-wake-failed"}


def _credits_problem(br, last):
    """The one credits problem the readings show: exhausted outranks a projection.

    At 12503/10000 the doctor still said "credits run out 15 Sep, before the
    reset", because the projection was checked first and a date that had already
    passed read like one still ahead.
    """
    # Which account, since a switch leaves another account's figures behind (#36).
    whose = (f"account {last['account']}" if last and last.get("account")
             else "account not named by the reading")
    if last and last.get("limit") and float(last.get("used") or 0) >= float(last["limit"]):
        return ("credits-exhaust", f"credits exhausted at the last reading: "
                                   f"{float(last['used']):.0f}/{float(last['limit']):.0f} ({whose})")
    if br and br.get("before_reset"):
        return ("credits-exhaust", f"credits run out {time.strftime('%d %b', time.localtime(br['exhausts_at']))}, "
                                   f"before the reset ({br['per_day']:.0f}/day, account {br.get('account')}) "
                                   "— new account or fewer features")
    return None


# Findings that mean work has stopped and only a person can restart it. Everything
# else a doctor finds is an advisory: recorded for the architect, never paged (#40).
DOCTOR_RED = {"watchdog-dead", "wake-failed", "transcript-blind", "transcript-missing"}


def _doctor_severity(key, text):
    if key in DOCTOR_RED or (key == "credits-exhaust" and text.startswith("credits exhausted")):
        return "red"
    return "yellow"


def _doctor_check(cfg, page=False):
    """Machine-facing doctor: one line per problem, exit 1 if any.

    Run by a person or an agent it pages nobody: it reports to its caller and
    returns non-zero (#40). Only the scheduled job passes page=True, and then a red
    finding goes to the human ladder while an advisory is recorded for the
    architect. On 2026-09-08 two wake turns ran --check by hand and mailed a
    standing advisory; on 2026-09-15 the scheduled job mailed exhausted credits
    every six hours although nothing could change before their reset.
    """
    from .watchdog import notify
    root = cfg["root"]
    project = A.project_key(root)
    problems = doctor_problems(cfg)
    if not problems:
        print(f"ok {time.strftime('%H:%M')} — no problems")
        return 0
    ringing = {alarm["key"] for alarm in A.active_alarms(project)} if page else set()
    samples = A.credit_samples(root) if page else []
    reset_at = (samples[-1] if samples else {}).get("reset_at")
    for key, text in problems:
        print(f"PROBLEM {key}: {text}")
        if not page or DOCTOR_WATCHDOG_ALARMS.get(key) in ringing:
            continue
        if _doctor_severity(key, text) == "red":
            held = {"quiet_until": reset_at} if key == "credits-exhaust" and reset_at else {}
            notify(f"{project}: {key}", text, root, key=f"doctor:{key}", window=3600,
                   audience="human", **held)
        else:
            notify(f"{project}: {key}", text, root, key=f"doctor:{key}", window=3600,
                   audience="architect")
    return 1


# A credit pool is shared: what ao reads is ao's share, what the account says is everyone's (#95).
SHARED_POOL_NOTE = ("the account's figure counts every session on it - another project's CLI, an IDE, "
                    "another orchestrator - and ao's transcript holds only ao's own turns")


def _account_beside_share(cfg, since=None):
    """Lines setting the account's figure beside ao's own transcript share, labelled as such."""
    try:
        acct = A.account_usage()
    except Exception:
        acct = None
    if not acct or acct.get("error") or acct.get("expired") or not acct.get("limit"):
        return [f"{C['dim']}the account's figure could not be read; what follows is ao's transcript "
                f"only{C['reset']}"]
    lines = [f"the account: {float(acct['used']):,.0f} of {float(acct['limit']):,.0f} used"]
    try:
        mine = A.turn_costs(cfg, since=since)
    except Exception:
        mine = None
    if mine and mine.get("turns") and float(acct["used"] or 0) > 0:
        lines.append(f"ao's own share, from its transcript: {mine['total']:,.0f} {mine['unit']} "
                     f"({100 * mine['total'] / float(acct['used']):.0f}% of the account's used)")
    lines.append(f"{C['dim']}{SHARED_POOL_NOTE}{C['reset']}")
    return lines


def _cost_by_feature(cfg, since, window):
    """`ao cost --features`: the implementer's spend each switch caused, over the window it was measured (#10)."""
    measured = A.feature_costs(cfg, since=since)
    if not measured["turns"]:
        print("no transcript turns in this window")
        return 0
    span = " to ".join(time.strftime("%d %b %H:%M", time.localtime(at)) for at in (measured["from"], measured["to"]))
    print(f"{C['b']}implementer spend by feature{C['reset']}  {C['dim']}({measured['unit']}; {measured['turns']} turns, "
          f"{span}{' — last ' + window if window else ''}){C['reset']}")
    for name, spent in measured["features"].items():
        share = 100 * spent["usage"] / measured["total"] if measured["total"] else 0
        print(f"  {name:<18}{spent['turns']:>5} turns {spent['usage']:>9.1f}  {share:>5.1f}%")
    for name, count in measured["counted"].items():
        print(f"  {name:<18}{count:>5} started  {C['dim']}the architect's pool, not this transcript{C['reset']}")
    print(f"  {'inventory_review':<18}  {C['dim']}counted with review: a transcript cannot tell them apart{C['reset']}")
    return 0


def cmd_cost(cfg, args):
    """What the coordination spends: the implementer's turns by what they did.

    The honest answer to "is ao bureaucracy" is the share of spend in turns that
    wrote nothing — ceremony (review/gate commands), coordination (inbox, reports,
    writer checks) — against turns that wrote product. `wasted` counts turns that
    ended in a blocked report without a product change: the queue-empty loop.
    """
    root = cfg["root"]
    since = None
    if args.since:
        n, unit = A.re.match(r"(\d+)([hd])", args.since).groups()
        since = time.time() - int(n) * (3600 if unit == "h" else 86400)
    if getattr(args, "features", False):
        return _cost_by_feature(cfg, since, args.since)
    c = A.turn_costs(cfg, since=since)
    if not c["turns"]:
        print("no transcript"); return 0
    tot = c["total"] or 1
    print(f"{C['b']}implementer spend by turn class{C['reset']}  {C['dim']}({c['unit']}; "
          f"{'last ' + args.since if args.since else 'whole transcript'}){C['reset']}")
    print(f"  {'class':<14}{'turns':>6}{'spend':>10}{'share':>7}   {'wasted turns':>12}")
    for cls in ("product", "analysis", "ceremony", "coordination"):
        b = c["by_class"].get(cls)
        if not b:
            continue
        w = f"{b['wasted']} ({b['wasted_usage']:.0f})" if b["wasted"] else ""
        print(f"  {cls:<14}{b['turns']:>6}{b['usage']:>10.0f}{100 * b['usage'] / tot:>6.0f}%   {w:>12}")
    print(f"  {'total':<14}{sum(b['turns'] for b in c['by_class'].values()):>6}{tot:>10.0f}")
    overhead = sum(c["by_class"].get(k, {}).get("usage", 0) for k in ("ceremony", "coordination"))
    for line in _account_beside_share(cfg, since):
        print(f"  {line}")
    print(f"\n  coordination + ceremony: {C['b']}{100 * overhead / tot:.0f}%{C['reset']} of spend"
          f"  ·  ao commands: {', '.join(f'{k} {v}' for k, v in c['ao_commands'].most_common(6))}")
    # the reviewer's side: files, wasted, sizes
    d = os.path.join(root, cfg["reviews"])
    if os.path.isdir(d):
        files = [f for f in os.listdir(d) if f.endswith(".md") and (not since or os.path.getmtime(os.path.join(d, f)) >= since)]
        una = sum(1 for f in files if A.reviews(root, cfg["reviews"], limit=10_000) and False)  # placeholder
        verdicts = dict(A.reviews(root, cfg["reviews"], limit=10_000))
        una = sum(1 for f in files if verdicts.get(f) in ("UNAVAILABLE", "INVALID"))
        size = sum(os.path.getsize(os.path.join(d, f)) for f in files)
        print(f"  reviews: {len(files)} files ({una} unavailable/invalid, cost nothing), "
              f"{size // 1000}k chars of verdict text; each review sends the slice diff to the reviewer model")
    return 0


def cmd_config(cfg, args):
    """Read and change what ao lets a person set (#74).

    `list` shows every setting with its value, its default and where the value came
    from; `get` prints one; `set` and `unset` change the project's config, or with
    --machine the machine's settings. Both are written whole or not at all.
    """
    root = cfg["root"]
    key = getattr(args, "key", None)
    if args.action == "list":
        print(f"  {'setting':<32}{'value':<14}{'default':<12}{'from':<9}what it decides")
        for name, spec in S.SETTINGS.items():
            value, source, problem = S.resolve(cfg, name)
            mark = f" {C['red']}!{C['reset']}" if problem else ""
            colour = C["dim"] if source == "default" else C["b"]
            print(f"  {name:<32}{colour}{str(value):<14}{C['reset']}{str(spec.default):<12}{source:<9}"
                  f"{C['dim']}{spec.text}{C['reset']}{mark}")
            if problem:
                print(f"  {'':<32}{C['red']}{problem}{C['reset']}")
        print(f"\n  {C['dim']}project: .ao/config.json · machine: {S.machine_path()} · "
              f"ao config set <setting> <value> [--machine]{C['reset']}")
        return 0
    if key not in S.SETTINGS:
        print(f"unknown setting {key!r}; `ao config list` shows the names")
        return 2
    if args.action == "get":
        print(S.get(cfg, key))
        return 0
    machine = bool(getattr(args, "machine", False))
    if S.SETTINGS[key].scope == "machine" and not machine:
        print(f"{key} governs the whole machine; set it with --machine")
        return 2
    if args.action == "set":
        try:
            value = S.parse(key, args.value)
        except (TypeError, ValueError) as exc:
            print(f"{C['red']}not changed{C['reset']}: {exc}")
            return 2
    else:
        value = S._MISSING
    if machine:
        try:
            S.write_machine(S.assign(S.machine_settings(), key, value))
        except OSError as exc:
            print(f"{C['red']}not changed{C['reset']}: {exc}")
            return 1
    else:
        document = A.project_config_document(root)
        if document["problem"]:
            # Rebuilding a config ao cannot read would drop what it held (#56).
            print(f"{C['red']}not changed{C['reset']}: {document['problem']}")
            return 1
        try:
            A.write_project_config(root, json.dumps(S.assign(document["config"], key, value), indent=2,
                                                    ensure_ascii=False))
        except OSError as exc:
            print(f"{C['red']}not changed{C['reset']}: {exc}")
            return 1
        cfg = dict(A.load_config(root), root=root)
    value, source, _ = S.resolve(cfg, key)
    print(f"{key} = {value!r} ({source})")
    return 0


def _feature_cell(measured, key):
    """One feature's measured cost as a short cell: a share of the implementer's spend, or a count (#10)."""
    if not measured:
        return "—"
    if key in measured["counted"]:
        return f"{measured['counted'][key]} started"
    if key not in measured["features"] or not measured["total"]:
        return "with review" if key == "inventory_review" else "—"
    return f"{100 * measured['features'][key]['usage'] / measured['total']:.1f}%"


def cmd_features(cfg, args):
    """The switches and what each costs. All off: deterministic ao, zero model spend."""
    from . import features as F
    root = cfg["root"]
    if args.action in ("on", "off"):
        if args.key not in F.FEATURES:
            print(f"unknown feature {args.key}; one of {', '.join(F.ORDER)}"); return 2
        try:
            F.set_switch(root, args.key, args.action == "on")
        except (OSError, ValueError) as exc:
            print(f"{C['red']}not changed{C['reset']}: {exc}")
            return 1
        cfg = A.load_config(root)
    on = F.switches(cfg)
    try:
        measured = A.feature_costs(cfg, since=time.time() - 7 * 86400)
    except Exception:
        measured = None
    print(f"  {'feature':<18}{'':<4}{'last 7 days':>13}  what it spends")
    for k in F.ORDER:
        label, _, what = F.FEATURES[k]
        state = f"{C['green']}on {C['reset']}" if on[k] else f"{C['dim']}off{C['reset']}"
        print(f"  {k:<18}{state:<4}{_feature_cell(measured, k):>13}  {C['dim']}{what}{C['reset']}")
    print(f"\n  {C['dim']}measured from the implementer's transcript and the watchdog's logs, never estimated; "
          f"all off = board, mail, gates, commit-ok, alarms, pings and hooks only; `ao cost --features` "
          f"for the window{C['reset']}")
    print(f"  {C['dim']}ao features on|off <feature>{C['reset']}")
    return 0


def _agent_names(cfg):
    """Names that belong to agents or roles, never to the person a waiver must name."""
    names = {"architect", "implementer", "reviewer", "watchdog", "ao", "agent", "human"}
    for role in ("implementer", "architect"):
        block = cfg.get(role) or {}
        names.update(str(block.get(key) or "") for key in ("name", "adapter", "session"))
    primary = cfg.get("reviewer") or {}
    names.update(str(route.get("id") or "") for route in [primary] + list(primary.get("fallbacks") or [])
                 if isinstance(route, dict))
    return {name.strip().lower() for name in names if name.strip()}


def cmd_waive(cfg, args):
    """A person bypasses a gate for a slice, on the record. Reconciled later by `ao catchup`."""
    root = cfg["root"]
    if not args.why:
        print("--why is required: a waiver without a reason cannot be reconciled"); return 2
    # A waiver covers one slice for a bounded time and names a person (#67).
    if not args.slice or args.slice == "*":
        print("--slice is required: a waiver covers one slice, never every slice"); return 2
    if not (args.by or "").strip():
        print("--by is required: name the person who authorises this waiver"); return 2
    if args.by.strip().lower() in _agent_names(cfg):
        print(f"--by names an agent or a role ({args.by}); a waiver is a person's act"); return 2
    hours = args.hours if args.hours is not None else S.get(cfg, "waivers.default_hours")
    longest = S.get(cfg, "waivers.max_hours")
    if not 0 < hours <= longest:
        print(f"--hours must be above 0 and at most {longest}"); return 2
    try:
        rec = A.waive(root, args.gate, args.slice, args.why, by=args.by.strip(), hours=hours)
    except Exception as exc:
        print(f"{C['red']}waiver not recorded{C['reset']}: {exc}"); return 1
    print(f"{C['yellow']}{C['b']}waived{C['reset']} {args.gate} for {rec['slice']} ({rec['id']}) by {rec['by']} at HEAD {rec['head'][:8]}")
    print(f"{C['dim']}commit-ok honours it; `ao catchup` runs the missed {args.gate} against the landed range and closes it.{C['reset']}")
    A.record_notice(root, f"waiver {args.gate}", f"{rec['slice']}: {args.why[:120]} ({rec['by']})", sent=False, key="waiver")
    return 0


def cmd_catchup(cfg, args):
    """Replay what could not run: waived reviews, deferred wakes and nudges."""
    from types import SimpleNamespace
    root = cfg["root"]
    did = 0
    failed = []

    def close(wid, outcome):
        # A waiver retires on a durable append or not at all; a failure is reported
        # and fails the command, never counted as handled (#67).
        try:
            A.close_waiver(root, wid, outcome)
            return True
        except Exception as exc:
            print(f"  {C['red']}could not close {wid}{C['reset']}: {exc}")
            failed.append(wid)
            return False

    try:
        targets = A.review_waiver_ranges(root)
    except Exception as exc:
        print(f"  {C['red']}waivers cannot be read{C['reset']}: {exc}")
        targets = []
        failed.append("ledger")
    for item in targets:
        w = item["waiver"]
        label = f"{w['id']} ({w['slice']})"
        if item["problem"]:
            print(f"  {label}: {item['problem']}; keeping it open")
            continue
        if item.get("unused"):
            if not item.get("expired"):
                print(f"  {label}: nothing was granted under it yet; keeping it open until it expires")
            elif close(w["id"], "nothing was granted under it before it expired"):
                print(f"  {label}: expired unused; closed")
                did += 1
            continue
        if not item["landed"]:
            if item["newest"]:
                print(f"  {label}: nothing landed yet after the waiver; keeping it open")
            elif close(w["id"], "no commits landed before the next waiver was opened"):
                print(f"  {label}: no commits landed before the next waiver; closed")
                did += 1
            continue
        rng = f"{item['start'][:12]}..{item['end'][:12]}"
        print(f"  {label}: reviewing its own landed range {rng} ({item['landed']} commit(s))")
        ns = SimpleNamespace(boundary=args.boundary or f"waived review for {w['slice']}: {w['why']}",
                             timeout=900, paths=None, commits=f"{item['start']}..{item['end']}")
        try:
            before = A.review_row_count(root)
        except Exception as exc:
            print(f"  {C['red']}review ledger cannot be read{C['reset']}: {exc}; {w['id']} stays open")
            failed.append(w["id"])
            continue
        code = cmd_review(cfg, ns)
        # The exit code is not a verdict: a review that refused to run exits 1 or 2
        # too. A waiver closes on the review recorded for exactly this range.
        try:
            verdict = A.range_review_verdict(root, ns.commits, since=before)
        except Exception as exc:
            print(f"  {C['red']}review ledger cannot be read{C['reset']}: {exc}; {w['id']} stays open")
            failed.append(w["id"])
            continue
        if verdict == "APPROVED":
            if close(w["id"], "reviewed: APPROVED"):
                did += 1
        elif verdict == "NEEDS_CHANGES":
            if close(w["id"], "reviewed: NEEDS_CHANGES — fix slice needed"):
                impl, arch = A.mail_names(cfg)
                A.write_mail(root, cfg, f"{time.strftime('%Y%m%d-%H%M')}-catchup-to-{arch}-REVIEW-{w['slice'].lower()}-needs-changes.md",
                             f"# Muafiyet kapandı: {w['slice']} retro review NEEDS_CHANGES\n\n## KARAR GEREKLİ\n\n"
                             f"{w['id']} ({w['by']}: {w['why']}) için inmiş aralık {rng} review edildi; bulgular semantic-review/ altında. "
                             f"Bir düzeltme dilimi gerekir.\n", {"kind": "review", "from": "catchup", "to": arch, "slice": w["slice"]})
                did += 1
        elif verdict is None and code == 0:
            if close(w["id"], "nothing to review: the landed range has no net change"):
                print(f"  {label}: the landed range has no net change; closed")
                did += 1
        elif code == 3 or verdict == "UNAVAILABLE":
            print(f"  reviewer still unavailable; {w['id']} stays open")
        elif verdict == "INVALID":
            print(f"  the reviewer returned no valid verdict; {w['id']} stays open")
        elif code == 2:
            print(f"  reviewer configuration invalid, or the range cannot be reviewed; {w['id']} stays open")
        else:
            print(f"  no review was recorded (exit {code}); {w['id']} stays open")
    for r in A.deferred_open(root):
        print(f"  deferred {r['kind']} ({r.get('reason', '')}) since {time.strftime('%d %b %H:%M', time.localtime(r['at']))}")
        A.deferred_close(root, r["id"], "replayed by catchup")
        did += 1
    if A.heartbeat_age(root) is None:
        # A cycle writes this project's heartbeat. Where no watchdog runs, that file
        # goes stale within minutes and every sibling watchdog reports it as dead.
        print(f"{C['dim']}no watchdog runs for this project; skipping the cycle{C['reset']}")
    else:
        print(f"{C['dim']}running one watchdog cycle to act on what is now possible{C['reset']}")
        from . import watchdog as W
        W.run(SimpleNamespace(root=root, idle_minutes=S.get(cfg, "watchdog.idle_minutes"), dry_run=False, prompt=W.NUDGE_PROMPT))
    print(f"{C['green']}catchup{C['reset']} handled {did} item(s)")
    return 1 if failed else 0


def cmd_pings(cfg, args):
    """Dead man's switch: an external service that alarms when the pings stop."""
    root = cfg["root"]
    if args.action == "setup":
        if not args.url:
            print("1. Create a check at https://healthchecks.io (free) with a 15-minute period and a 10-minute grace.\n"
                  "2. Copy its ping URL, then:  ao pings setup --url https://hc-ping.com/<uuid>  [--all]\n"
                  "The watchdog and the doctor job ping it every cycle; when both die, healthchecks e-mails you.")
            return 0
        A.set_ping_url(root, args.url, all_projects=args.all)
        print(f"{C['green']}saved{C['reset']} {A.pings_path()} (0600)"); return 0
    url = A.ping_url(root)
    if args.action == "test":
        ok = A.ping(root)
        print(f"{C['green']}pinged{C['reset']}" if ok else f"{C['red']}no ping{C['reset']} ({'no url' if not url else 'request failed'})")
        return 0 if ok else 1
    print(f"ping url: {url or C['yellow'] + 'not configured' + C['reset']}")
    return 0


# Exact bodies emitted before AO53. They remain as a deliberately narrow legacy
# grammar: callers and old tests may still render them, but new installs never do.
PRE_COMMIT_HOOK = """#!/bin/sh
# agent-orchestrator: commit authority is bound to Git's exact active index.
exec {ao} -C {root} commit-check
"""

PRE_PUSH_HOOK = """#!/bin/sh
# agent-orchestrator: push is a human decision. Allowed when a person ran
# `ao push allow` in the last 30 minutes for this repository; refused otherwise.
exec {ao} -C {root} push check
"""

_HOOK_ROLES = ("pre-commit", "pre-push")
_HOOK_REPOSITORY_ENV = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS",
}
_CURRENT_STATES = {
    "current-local (behavior unverified)",
    "current-scoped (behavior unverified)",
}


class _HookResolutionError(RuntimeError):
    pass


def _hook_git_env():
    """A copied environment without inherited repository/config/pathspec binding."""
    env = os.environ.copy()
    for key in list(env):
        if key in _HOOK_REPOSITORY_ENV or key.startswith("GIT_CONFIG_KEY_") \
                or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    return env


def _hook_git(cwd, *args, timeout=15, extra_env=None):
    """Run one literal-path Git query without a shell; return the byte result."""
    env = _hook_git_env()
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(
            [A.git_binary(), "--literal-pathspecs", "-C", str(cwd), *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _HookResolutionError(f"git query failed: {exc}") from exc


def _hook_output_path(result, label):
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        raise _HookResolutionError(f"{label} failed: {detail or 'git exit ' + str(result.returncode)}")
    raw = result.stdout[:-1] if result.stdout.endswith(b"\n") else result.stdout
    if not raw:
        raise _HookResolutionError(f"{label} returned an empty path")
    return os.fsdecode(raw)


def _hook_real(top, value):
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        value = os.path.join(top, value)
    return os.path.realpath(os.path.abspath(value))


def _hook_contains(parent, child):
    try:
        return os.path.commonpath([os.path.realpath(parent), os.path.realpath(child)]) \
            == os.path.realpath(parent)
    except (OSError, ValueError):
        return False


def _parse_hooks_config(result):
    """Parse exactly scope/origin/value, preserving empty values and separators."""
    if result.returncode == 1 and not result.stdout:
        return {"set": False, "scope": None, "origin": None, "value": None}
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        raise _HookResolutionError(
            f"core.hooksPath query failed: {detail or 'git exit ' + str(result.returncode)}"
        )
    fields = result.stdout.split(b"\0")
    if len(fields) != 4 or fields[-1] != b"":
        raise _HookResolutionError("core.hooksPath output is not three NUL-terminated fields")
    try:
        scope, origin, value = (os.fsdecode(field) for field in fields[:3])
    except UnicodeError as exc:
        raise _HookResolutionError(f"core.hooksPath output is undecodable: {exc}") from exc
    if not scope or not origin:
        raise _HookResolutionError("core.hooksPath output has an empty scope/origin")
    return {"set": True, "scope": scope, "origin": origin, "value": value}


def _hooks_config(top):
    return _parse_hooks_config(_hook_git(
        top, "config", "--null", "--show-origin", "--show-scope", "--get",
        "core.hooksPath",
    ))


def _parse_worktrees(raw):
    rows = []
    for block in raw.decode(UTF8, "surrogateescape").strip().split("\n\n"):
        if not block.strip():
            continue
        row = {"path": None, "bare": False, "porcelain_locked": False}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                row["path"] = value
            elif key == "bare":
                row["bare"] = True
            elif key == "locked":
                row["porcelain_locked"] = True
        if row["path"]:
            rows.append(row)
    return rows


def _worktree_admins(common_dir):
    """Return regular admin records; a symlink/unreadable record is unknowable."""
    base = os.path.join(common_dir, "worktrees")
    records, unknown = [], []
    if not os.path.isdir(base):
        return records, unknown
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return records, [base]
    for name in names:
        admin = os.path.join(base, name)
        if os.path.islink(admin) or not os.path.isdir(admin):
            unknown.append(admin)
            continue
        gitdir_file = os.path.join(admin, "gitdir")
        try:
            if os.path.islink(gitdir_file) or not os.path.isfile(gitdir_file):
                raise OSError("gitdir is not a regular file")
            marker = open(gitdir_file, "rb").read().decode(UTF8, "surrogateescape").strip()
        except (OSError, UnicodeError):
            unknown.append(admin)
            continue
        if not marker:
            unknown.append(admin)
            continue
        marker = os.path.abspath(marker)
        worktree = os.path.dirname(marker) if os.path.basename(marker) == ".git" else None
        if not worktree:
            unknown.append(admin)
            continue
        records.append({
            "name": name,
            "admin": os.path.realpath(admin),
            "worktree": os.path.realpath(worktree),
            "locked": os.path.isfile(os.path.join(admin, "locked"))
                      and not os.path.islink(os.path.join(admin, "locked")),
        })
    return records, unknown


def _worktree_facts(top, common_dir):
    result = _hook_git(top, "worktree", "list", "--porcelain")
    if result.returncode:
        detail = result.stderr.decode(UTF8, "replace").strip()[:160]
        raise _HookResolutionError(f"git worktree list failed: {detail or result.returncode}")
    rows = _parse_worktrees(result.stdout)
    admins, unknown = _worktree_admins(common_dir)
    if unknown:
        raise _HookResolutionError("unknown worktree admin: " + ", ".join(unknown[:3]))
    by_path = {row["worktree"]: row for row in admins}
    facts = []
    for row in rows:
        path = os.path.realpath(row["path"])
        fact = {"path": path, "bare": row["bare"], "admin": None,
                "status": "reachable", "effective_dir": None, "config": None}
        if row["bare"]:
            facts.append(fact)
            continue
        marker = os.path.join(path, ".git")
        admin = by_path.get(path)
        if os.path.isdir(path) and os.path.lexists(marker):
            fact["status"] = "reachable"
            if admin:
                fact["admin"] = admin["admin"]
        elif admin:
            fact["admin"] = admin["admin"]
            fact["status"] = "locked" if admin["locked"] else "stale"
        else:
            fact["status"] = "unknown"
        facts.append(fact)
    known = {fact["path"] for fact in facts}
    for admin in admins:
        if admin["worktree"] not in known:
            facts.append({
                "path": admin["worktree"], "bare": False,
                "admin": admin["admin"],
                "status": "locked" if admin["locked"] else "stale",
                "effective_dir": None, "config": None,
            })
    if any(f["status"] == "unknown" for f in facts):
        raise _HookResolutionError("unknown worktree record prevents safe hook mutation")
    for fact in facts:
        if fact["status"] != "reachable" or fact["bare"]:
            continue
        wt_top = _hook_output_path(
            _hook_git(fact["path"], "rev-parse", "--show-toplevel"),
            "worktree --show-toplevel",
        )
        wt_top = _hook_real(fact["path"], wt_top)
        fact["effective_dir"] = _hook_real(
            wt_top,
            _hook_output_path(_hook_git(wt_top, "rev-parse", "--git-path", "hooks"),
                              "worktree --git-path hooks"),
        )
        fact["config"] = _hooks_config(wt_top)
    return facts, admins


def _hook_directory_class(path, top, git_dir, common_dir, facts, config=None):
    path = os.path.realpath(path)
    hits = sum(
        1 for fact in facts
        if fact.get("effective_dir") and os.path.realpath(fact["effective_dir"]) == path
    )
    live_family = sum(1 for fact in facts if fact["status"] != "stale")
    unavailable = any(fact["status"] in ("locked", "unknown") for fact in facts)
    common_hooks = os.path.realpath(os.path.join(common_dir, "hooks"))
    absolute_config = bool(config and config.get("set") and os.path.isabs(config.get("value") or ""))
    if hits >= 2 or (path == common_hooks and live_family > 1) \
            or (absolute_config and unavailable and hits):
        return "shared"
    if _hook_contains(top, path) or _hook_contains(git_dir, path):
        return "project-local"
    return "external"


def _structural_roots(path):
    roots, current = [], os.path.abspath(os.path.dirname(path))
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            roots.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return roots


def _hook_track_state(path):
    """Literal tracked/untracked measurement across every enclosing worktree."""
    exists = os.path.lexists(path)
    measured = []
    seen = set()
    for candidate in _structural_roots(path):
        try:
            result = _hook_git(candidate, "rev-parse", "--show-toplevel")
        except _HookResolutionError:
            measured.append("indeterminate")
            continue
        if result.returncode or not result.stdout:
            measured.append("indeterminate")
            continue
        try:
            top = _hook_real(candidate, _hook_output_path(result, "--show-toplevel"))
        except _HookResolutionError:
            measured.append("indeterminate")
            continue
        if top in seen or not _hook_contains(top, path):
            continue
        seen.add(top)
        rel = os.path.relpath(path, top)
        try:
            result = _hook_git(top, "ls-files", "--error-unmatch", "--", rel)
        except _HookResolutionError:
            measured.append("indeterminate")
            continue
        if result.returncode == 0:
            measured.append("tracked")
        elif result.returncode == 1:
            measured.append("untracked")
        else:
            measured.append("indeterminate")
    if "tracked" in measured:
        return "tracked"
    if "indeterminate" in measured:
        return "indeterminate"
    if measured:
        return "untracked"
    return "indeterminate" if exists else "unobserved"


def _role_command(role):
    return "commit-check" if role == "pre-commit" else "push check"


def _render_v2_local_hook(role, root_rel):
    """Exact project-local body emitted before tracked project enrollment."""
    import shlex
    suffix = "" if root_rel in ("", ".") else "/" + shlex.quote(root_rel)
    command = _role_command(role)
    return (
        "#!/bin/sh\n"
        f"# agent-orchestrator: ao-hook-v2 role={role} binding=project-local\n"
        "initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
        f"root=\"$initial_cwd\"{suffix}\n"
        "[ -d \"$root/.ao\" ] || exit 0\n"
        "case ${GIT_INDEX_FILE-} in\n"
        "  \"\"|/*) ;;\n"
        "  *) GIT_INDEX_FILE=\"$initial_cwd/$GIT_INDEX_FILE\"; export GIT_INDEX_FILE ;;\n"
        "esac\n"
        "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX "
        "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES\n"
        "ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
        f"exec \"$ao\" -C \"$root\" {command}\n"
    ).encode(UTF8)


def _render_v2_scoped_hook(role, root_rel, family, directory_class):
    """Exact shared/external body emitted before tracked project enrollment."""
    import shlex
    suffix = "" if root_rel in ("", ".") else "/" + shlex.quote(root_rel)
    command = _role_command(role)
    return (
        "#!/bin/sh\n"
        f"# agent-orchestrator: ao-hook-v2 role={role} binding={directory_class}\n"
        "initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
        "case ${GIT_INDEX_FILE-} in\n"
        "  \"\"|/*) ;;\n"
        "  *) GIT_INDEX_FILE=\"$initial_cwd/$GIT_INDEX_FILE\"; export GIT_INDEX_FILE ;;\n"
        "esac\n"
        "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX "
        "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES\n"
        "common=$(git --literal-pathspecs rev-parse --git-common-dir 2>/dev/null) || exit 0\n"
        "case $common in /*) ;; *) common=\"$initial_cwd/$common\" ;; esac\n"
        "common=$(CDPATH= cd \"$common\" 2>/dev/null && pwd -P) || exit 0\n"
        f"expected={shlex.quote(os.path.realpath(family))}\n"
        "[ \"$common\" = \"$expected\" ] || exit 0\n"
        "top=$(git --literal-pathspecs rev-parse --show-toplevel 2>/dev/null) || exit 0\n"
        "case $top in /*) ;; *) top=\"$initial_cwd/$top\" ;; esac\n"
        "top=$(CDPATH= cd \"$top\" 2>/dev/null && pwd -P) || exit 0\n"
        f"root=\"$top\"{suffix}\n"
        "[ -d \"$root/.ao\" ] || exit 0\n"
        "ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
        f"exec \"$ao\" -C \"$root\" {command}\n"
    ).encode(UTF8)


def _hook_repository_unset():
    return (
        "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX "
        "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES "
        "GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_CONFIG_GLOBAL "
        "GIT_CONFIG_SYSTEM GIT_CONFIG_NOSYSTEM GIT_CEILING_DIRECTORIES "
        "GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_LITERAL_PATHSPECS GIT_GLOB_PATHSPECS "
        "GIT_NOGLOB_PATHSPECS GIT_ICASE_PATHSPECS\n"
    )


def _hook_project_marker_guard():
    return (
        "if ! git --literal-pathspecs -C \"$root\" ls-files --error-unmatch -- "
        ".ao-project >/dev/null 2>&1 &&\n"
        "   ! git --literal-pathspecs -C \"$root\" cat-file -e "
        "HEAD:.ao-project 2>/dev/null; then\n"
        "  exit 0\n"
        "fi\n"
    )


def _render_local_hook(role, root_rel):
    """Portable bytes for one project-local hook; no machine/family binding."""
    import shlex
    suffix = "" if root_rel in ("", ".") else "/" + shlex.quote(root_rel)
    command = _role_command(role)
    return (
        "#!/bin/sh\n"
        f"# agent-orchestrator: ao-hook-v3 role={role} binding=project-local\n"
        "initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
        f"root=\"$initial_cwd\"{suffix}\n"
        "case ${GIT_INDEX_FILE-} in\n"
        "  \"\"|/*) ;;\n"
        "  *) GIT_INDEX_FILE=\"$initial_cwd/$GIT_INDEX_FILE\"; export GIT_INDEX_FILE ;;\n"
        "esac\n"
        + _hook_repository_unset()
        + _hook_project_marker_guard()
        + "ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
        + f"exec \"$ao\" -C \"$root\" {command}\n"
    ).encode(UTF8)


def _render_scoped_hook(role, root_rel, family, directory_class):
    """Family-bound bytes for a shared/external target; unknown routing is fail-open."""
    import shlex
    suffix = "" if root_rel in ("", ".") else "/" + shlex.quote(root_rel)
    command = _role_command(role)
    return (
        "#!/bin/sh\n"
        f"# agent-orchestrator: ao-hook-v3 role={role} binding={directory_class}\n"
        "initial_cwd=$(CDPATH= cd -- . && pwd -P)\n"
        "case ${GIT_INDEX_FILE-} in\n"
        "  \"\"|/*) ;;\n"
        "  *) GIT_INDEX_FILE=\"$initial_cwd/$GIT_INDEX_FILE\"; export GIT_INDEX_FILE ;;\n"
        "esac\n"
        + _hook_repository_unset()
        + "common=$(git --literal-pathspecs rev-parse --git-common-dir 2>/dev/null) || exit 0\n"
        + "case $common in /*) ;; *) common=\"$initial_cwd/$common\" ;; esac\n"
        + "common=$(CDPATH= cd \"$common\" 2>/dev/null && pwd -P) || exit 0\n"
        + f"expected={shlex.quote(os.path.realpath(family))}\n"
        + "[ \"$common\" = \"$expected\" ] || exit 0\n"
        + "top=$(git --literal-pathspecs rev-parse --show-toplevel 2>/dev/null) || exit 0\n"
        + "case $top in /*) ;; *) top=\"$initial_cwd/$top\" ;; esac\n"
        + "top=$(CDPATH= cd \"$top\" 2>/dev/null && pwd -P) || exit 0\n"
        + f"root=\"$top\"{suffix}\n"
        + _hook_project_marker_guard()
        + "ao=$(command -v ao) || { echo 'agent-orchestrator: ao not found' >&2; exit 1; }\n"
        + f"exec \"$ao\" -C \"$root\" {command}\n"
    ).encode(UTF8)


def _legacy_hook_role(data):
    """Recognize only byte-exact generated v1/v2 bodies, including stale bindings."""
    import shlex
    if b"\x00" in data:
        return None
    try:
        text = data.decode(UTF8)
    except UnicodeError:
        return None
    if not text.startswith("#!/bin/sh\n") or "# agent-orchestrator:" not in text:
        return None

    lines = text.splitlines()
    exec_lines = [line for line in lines if line.startswith("exec ")]
    if len(exec_lines) == 1:
        try:
            argv = shlex.split(exec_lines[0])
        except ValueError:
            argv = []
        legacy = None
        if len(argv) == 5 and argv[0] == "exec" and argv[2] == "-C" \
                and argv[4] == "commit-check":
            legacy = PRE_COMMIT_HOOK.format(
                ao=shlex.quote(argv[1]), root=shlex.quote(argv[3])
            ).encode(UTF8)
            role = "pre-commit"
        elif len(argv) == 6 and argv[0] == "exec" and argv[2] == "-C" \
                and argv[4:] == ["push", "check"]:
            legacy = PRE_PUSH_HOOK.format(
                ao=shlex.quote(argv[1]), root=shlex.quote(argv[3])
            ).encode(UTF8)
            role = "pre-push"
        if legacy is not None and data == legacy:
            return role

    marker_prefix = "# agent-orchestrator: ao-hook-v"
    markers = [line for line in lines if line.startswith(marker_prefix)]
    if len(markers) != 1 or " role=" not in markers[0] or " binding=" not in markers[0]:
        return None
    version, declaration = markers[0][len(marker_prefix):].split(" role=", 1)
    if version not in ("2", "3"):
        return None
    role, binding = declaration.split(" binding=", 1)
    if role not in _HOOK_ROLES or binding not in ("project-local", "shared", "external"):
        return None

    def assignment(name):
        prefix = name + "="
        matches = [line for line in lines if line.startswith(prefix)]
        if len(matches) != 1:
            return None
        try:
            words = shlex.split(matches[0])
        except ValueError:
            return None
        if len(words) != 1 or not words[0].startswith(prefix):
            return None
        return words[0][len(prefix):]

    root = assignment("root")
    root_prefix = "$initial_cwd" if binding == "project-local" else "$top"
    if root == root_prefix:
        root_rel = "."
    elif root is not None and root.startswith(root_prefix + "/"):
        root_rel = root[len(root_prefix) + 1:]
    else:
        return None

    if binding == "project-local":
        rendered = (
            _render_v2_local_hook(role, root_rel)
            if version == "2" else _render_local_hook(role, root_rel)
        )
    else:
        family = assignment("expected")
        if not family:
            return None
        rendered = (
            _render_v2_scoped_hook(role, root_rel, family, binding)
            if version == "2" else _render_scoped_hook(role, root_rel, family, binding)
        )
    prior = rendered.replace(
        b"initial_cwd=$(CDPATH= cd -- . && pwd -P)\n",
        b"initial_cwd=$PWD\n",
        1,
    )
    if data == rendered or data == prior:
        return role
    return None


def _crlf_translation(data):
    if b"\r\n" not in data:
        return None
    remainder = data.replace(b"\r\n", b"")
    if b"\n" in remainder or b"\r" in remainder:
        return None
    return data.replace(b"\r\n", b"\n")


def _plausible_ao_role(data, role):
    try:
        text = data.decode(UTF8)
    except UnicodeError:
        return False
    executable = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    command = _role_command(role)
    return command in executable and ("ao" in executable or "agent-orchestrator" in text)


def _classify_hook(path, role, local_body=None, scoped_body=None):
    if not os.path.lexists(path):
        return "absent", False
    if os.path.islink(path):
        return "foreign", False
    try:
        data = open(path, "rb").read()
        data.decode(UTF8)
    except (OSError, UnicodeError):
        return "foreign", False
    if local_body is not None and data == local_body:
        return "current-local (behavior unverified)", False
    if scoped_body is not None and data == scoped_body:
        return "current-scoped (behavior unverified)", False
    translated = _crlf_translation(data)
    for candidate in (data, translated):
        if candidate is None:
            continue
        if (local_body is not None and candidate == local_body) \
                or (scoped_body is not None and candidate == scoped_body) \
                or _legacy_hook_role(candidate) == role:
            return "legacy (behavior unverified)", translated is not None
    if _plausible_ao_role(data, role):
        return "ambiguous-ao", False
    return "foreign", False


def _state_base(state):
    return state.split(" ", 1)[0]


def _repo_source_roles(top):
    result = _hook_git(
        top, "ls-files", "-z", "--", ".githooks/pre-commit", ".githooks/pre-push"
    )
    if result.returncode:
        return set()
    return {os.path.basename(os.fsdecode(raw)) for raw in result.stdout.split(b"\0") if raw}


def _add_hook_dir(directories, path, source):
    path = os.path.realpath(path)
    row = directories.setdefault(path, {"path": path, "sources": set()})
    row["sources"].add(source)
    return row


def _ao_hook_inventory(root):
    """Resolve every active/legacy AO hook target from Git's measured topology."""
    root = os.path.realpath(os.path.abspath(root))
    empty = {
        "root": root, "top": None, "git_dir": None, "common_dir": None,
        "active_dir": None, "directory_class": None, "globally_configured": False,
        "config": {"set": False, "scope": None, "origin": None, "value": None},
        "family": None, "project_rel": None, "worktrees": [], "targets": [],
        "error": None,
    }
    try:
        top = _hook_real(
            root,
            _hook_output_path(_hook_git(root, "rev-parse", "--show-toplevel"),
                              "--show-toplevel"),
        )
        git_dir = _hook_real(
            top, _hook_output_path(_hook_git(top, "rev-parse", "--git-dir"), "--git-dir")
        )
        common_dir = _hook_real(
            top,
            _hook_output_path(_hook_git(top, "rev-parse", "--git-common-dir"),
                              "--git-common-dir"),
        )
        active_dir = _hook_real(
            top,
            _hook_output_path(_hook_git(top, "rev-parse", "--git-path", "hooks"),
                              "--git-path hooks"),
        )
        config = _hooks_config(top)
        facts, admins = _worktree_facts(top, common_dir)
    except _HookResolutionError as exc:
        empty["error"] = str(exc)
        return empty

    directory_class = _hook_directory_class(
        active_dir, top, git_dir, common_dir, facts, config
    )
    globally_configured = config["scope"] in ("system", "global")
    project_rel = os.path.relpath(root, top)
    directories = {}
    _add_hook_dir(directories, active_dir, "active")
    _add_hook_dir(directories, os.path.join(git_dir, "hooks"), "current-private")
    _add_hook_dir(directories, os.path.join(common_dir, "hooks"), "common-fallback")
    for admin in admins:
        _add_hook_dir(directories, os.path.join(admin["admin"], "hooks"),
                      "admin:" + admin["name"])
    for fact in facts:
        if fact.get("effective_dir"):
            _add_hook_dir(directories, fact["effective_dir"], "effective:" + fact["path"])

    repo_dir = os.path.realpath(os.path.join(top, ".githooks"))
    repo_roles = _repo_source_roles(top)
    if repo_roles or any(os.path.lexists(os.path.join(repo_dir, role)) for role in _HOOK_ROLES):
        _add_hook_dir(directories, repo_dir, "repository-source")

    effective_counts = {}
    for fact in facts:
        if fact.get("effective_dir"):
            key = os.path.realpath(fact["effective_dir"])
            effective_counts[key] = effective_counts.get(key, 0) + 1
    locked_admins = {
        os.path.realpath(admin["admin"])
        for admin in admins if admin["locked"]
    }

    targets = []
    ordered_dirs = sorted(directories.values(), key=lambda row: (row["path"] != active_dir, os.fsencode(row["path"])))
    for directory in ordered_dirs:
        path = directory["path"]
        sources = directory["sources"]
        cls = _hook_directory_class(path, top, git_dir, common_dir, facts,
                                    config if path == active_dir else None)
        potential = bool(effective_counts.get(path)) or "common-fallback" in sources
        if any(_hook_contains(admin, path) for admin in locked_admins):
            potential = True
        reachability = "potentially-effective" if potential else "dead-misplaced"
        legacy_location = "active" not in sources and not any(
            source.startswith("effective:") for source in sources
        ) and "repository-source" not in sources
        for role in _HOOK_ROLES:
            target_path = os.path.join(path, role)
            active = path == active_dir
            repository_source = "repository-source" in sources and role in repo_roles
            if not active and not repository_source and not os.path.lexists(target_path):
                continue
            track = _hook_track_state(target_path)
            local_body = _render_local_hook(role, project_rel)
            scoped_body = _render_scoped_hook(role, project_rel, common_dir, cls)
            expected_local = repository_source or cls == "project-local"
            state, crlf_only = _classify_hook(
                target_path, role,
                local_body=local_body if expected_local else None,
                scoped_body=scoped_body if not expected_local else None,
            )
            base = _state_base(state)
            eligible = False
            if base in ("current-local", "current-scoped", "legacy") and track == "untracked":
                eligible = True
            elif base == "absent" and active:
                eligible = track == "untracked" or (
                    track == "unobserved" and (cls in ("shared", "external") or globally_configured)
                )
            needs_auth = eligible and (
                cls in ("shared", "external") or (active and globally_configured)
            )
            targets.append({
                "role": role, "path": target_path, "directory": path,
                "directory_class": cls, "active": active,
                "globally_configured": bool(active and globally_configured),
                "reachability": reachability, "effective_count": effective_counts.get(path, 0),
                "repository_source": repository_source,
                "legacy_location": legacy_location,
                "static_state": state, "track_state": track,
                "protected": track in ("tracked", "indeterminate"),
                "eligible": eligible, "needs_authorization": needs_auth,
                "crlf_only": crlf_only, "sources": sorted(sources),
                "body": local_body if expected_local else scoped_body,
            })

    empty.update({
        "top": top, "git_dir": git_dir, "common_dir": common_dir,
        "active_dir": active_dir, "directory_class": directory_class,
        "globally_configured": globally_configured, "config": config,
        "family": common_dir, "project_rel": project_rel,
        "worktrees": facts, "targets": targets,
    })
    return empty


def _ao_hook_paths(root):
    inv = _ao_hook_inventory(root)
    if inv["error"]:
        return {role: os.path.join(root, ".git", "hooks", role) for role in _HOOK_ROLES}
    return {role: os.path.join(inv["active_dir"], role) for role in _HOOK_ROLES}


def _ao_hook_state(path):
    """Compatibility classifier: marker-only files are foreign, old exact bodies legacy."""
    role = os.path.basename(path)
    if role not in _HOOK_ROLES:
        return "foreign"
    return _classify_hook(path, role)[0]


def _active_hook_targets(inv):
    return {target["role"]: target for target in inv["targets"] if target["active"]}


def _hook_execution_probe(inv):
    """Prove that Git resolves and executes AO's active pre-commit hook.

    Static classification is only a safety precondition: it prevents status and
    doctor from executing foreign hook content.  The positive result comes only
    from ``git hook run pre-commit`` carrying a temporary synthetic index into
    ``cmd_commit_check`` and receiving its nonce-bound refusal marker.
    """
    failed = lambda detail, code=None: {
        "installed": False, "state": "not installed", "detail": detail,
        "exit": code,
    }
    if inv.get("error"):
        return failed("hook topology cannot be resolved: " + inv["error"])
    try:
        target = _active_hook_targets(inv)["pre-commit"]
    except KeyError:
        return failed("Git resolved no active pre-commit target")
    base = _state_base(target["static_state"])
    if base not in ("current-local", "current-scoped"):
        return failed(
            f"active pre-commit intent is {target['static_state']} / "
            f"{target['track_state']}"
        )
    enrollment = _project_enrollment(inv["root"])
    if enrollment["state"] == "uninitialized":
        return failed(enrollment["detail"])
    if enrollment["state"] == "broken":
        return failed(enrollment["detail"])
    if enrollment["state"] == "legacy":
        # A current hook in a legacy project exits in its shell guard before AO
        # runs, so it governs nothing until the marker is adopted.
        return failed(enrollment["detail"])

    head_result = _hook_git(inv["top"], "rev-parse", "--verify", "HEAD")
    if head_result.returncode:
        return failed("HEAD cannot supply the synthetic gitlink object")
    try:
        head = head_result.stdout.strip().decode("ascii", "strict")
    except UnicodeError:
        return failed("HEAD object id is not ASCII")
    if len(head) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in head):
        return failed("HEAD returned an invalid object id")

    import tempfile
    nonce = os.urandom(16).hex()
    path = ".ao-hook-probe-" + nonce
    marker = f"AO-HOOK-PROBE-REFUSED {nonce} {head} {path}".encode("ascii")
    try:
        with tempfile.TemporaryDirectory(prefix="ao-hook-probe-") as temporary:
            index = os.path.abspath(os.path.join(temporary, "index"))
            extra = {"GIT_INDEX_FILE": index}
            prepared = _hook_git(inv["top"], "read-tree", "HEAD", extra_env=extra)
            if prepared.returncode:
                return failed("cannot initialize the temporary probe index")
            if enrollment["source"] == "index":
                project_marker = enrollment["marker"]
                carried = _hook_git(
                    inv["top"], "update-index", "--add", "--cacheinfo",
                    f"{project_marker['mode']},{project_marker['oid']},{PROJECT_MARKER}",
                    extra_env=extra,
                )
                if carried.returncode:
                    return failed("cannot carry the staged .ao-project into the probe index")
            staged = _hook_git(
                inv["top"], "update-index", "--add", "--cacheinfo",
                f"160000,{head},{path}", extra_env=extra,
            )
            if staged.returncode:
                return failed("cannot stage the synthetic probe candidate")
            extra.update({
                "AO_HOOK_PROBE_NONCE": nonce,
                "AO_HOOK_PROBE_PATH": path,
                "AO_HOOK_PROBE_HEAD": head,
                "AO_HOOK_PROBE_INDEX": index,
            })
            result = _hook_git(
                inv["top"], "hook", "run", "pre-commit",
                timeout=15, extra_env=extra,
            )
    except _HookResolutionError as exc:
        return failed(f"Git could not execute the pre-commit probe: {exc}")

    channels = result.stdout.splitlines() + result.stderr.splitlines()
    if result.returncode and marker in channels:
        return {
            "installed": True,
            "state": "installed",
            "detail": "Git executed pre-commit and AO refused the synthetic candidate",
            "exit": result.returncode,
        }
    if result.returncode == 0:
        return failed("Git or the resolved hook allowed the synthetic candidate", 0)
    return failed(
        f"the hook exited {result.returncode} without AO's nonce-bound refusal proof",
        result.returncode,
    )


def _hook_probe_text(probe):
    if probe["installed"]:
        return "installed (execution proved)"
    return "not installed — " + probe["detail"]


def _authorization_refusal(targets, allow):
    needed = [target for target in targets if target.get("needs_authorization")]
    if needed and os.name == "nt":
        # The shared hook compares Git's shell path, /c/..., with C:\..., never
        # matches, and lets every commit through (#71).
        print(f"{C['red']}refused{C['reset']} — a shared or external hook cannot recognise this "
              "repository on Windows, where Git's shell names it /c/... and ao names it C:\\...; "
              "install the hooks inside the repository")
        return True
    if needed and not allow:
        places = ", ".join(sorted({
            f"{target['directory_class']}:{target['directory']}"
            + (" (global/system config)" if target["globally_configured"] else "")
            for target in needed
        }))
        print(f"{C['red']}refused{C['reset']} — shared/external/globally-configured hook mutation "
              f"needs --allow-shared-hooks: {places}")
        return True
    return False


def _atomic_hook_write(path, data):
    import tempfile
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".ao-hook-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            # os.fchmod exists on Windows only from Python 3.13 (#71).
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), 0o755)
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _print_hook_status(inv):
    proof = _hook_execution_probe(inv)
    if inv["error"]:
        print(f"resolver failure: {inv['error']}")
        print(f"pre-commit execution: {_hook_probe_text(proof)}")
        return
    print(f"effective hooks: {inv['active_dir']}")
    facts = []
    for status in ("reachable", "locked", "stale"):
        count = sum(1 for row in inv["worktrees"] if row["status"] == status)
        if count:
            facts.append(f"{count} {status}")
    print(f"class: {inv['directory_class']}" + (f" ({'; '.join(facts)})" if facts else ""))
    config = inv["config"]
    if config["set"]:
        print(f"core.hooksPath: {config['scope']} {config['origin']} value={config['value']!r}")
    else:
        print("core.hooksPath: unset")
    active = _active_hook_targets(inv)
    for role in _HOOK_ROLES:
        target = active[role]
        note = ""
        if role == "pre-push" and _state_base(target["static_state"]) not in (
            "current-local", "current-scoped"
        ):
            note = " — AO push-window hook unavailable"
        print(f"{role}: {target['static_state']} / {target['track_state']}{note}")
    print(f"pre-commit execution: {_hook_probe_text(proof)}")
    for target in inv["targets"]:
        if target["active"] or _state_base(target["static_state"]) in ("absent", "foreign"):
            continue
        print(f"misplaced {target['role']}: {target['static_state']} / "
              f"{target['track_state']} / {target['reachability']} / "
              f"{target['directory_class']} — {target['path']}")


def _hooks_uninstall(inv, allow):
    plan = [
        target for target in inv["targets"]
        if target["eligible"] and _state_base(target["static_state"])
        in ("current-local", "current-scoped", "legacy")
    ]
    if _authorization_refusal(plan, allow):
        return 1
    failed = False
    for target in plan:
        try:
            os.remove(target["path"])
            print(f"removed {target['role']} — {target['path']}")
        except OSError as exc:
            failed = True
            print(f"{C['red']}failed to remove{C['reset']} {target['path']}: {exc}")
    if failed:
        return 1
    after = _ao_hook_inventory(inv["root"])
    if after["error"]:
        print(f"resolver failure after uninstall: {after['error']}")
        return 1
    blockers = []
    for target in after["targets"]:
        base = _state_base(target["static_state"])
        if base not in ("current-local", "current-scoped", "legacy", "ambiguous-ao"):
            continue
        if target["reachability"] == "dead-misplaced" and target["protected"]:
            print(f"preserved protected dead-misplaced {target['role']}: {target['path']}")
            continue
        if target["reachability"] == "potentially-effective":
            blockers.append(target)
            print(f"preserved potentially-effective {target['static_state']} {target['role']}: "
                  f"{target['path']}")
    return 1 if blockers else 0


def cmd_hooks(cfg, args):
    """Manage AO hook intent at Git's effective path without replacing user hooks."""
    root = cfg["root"]
    inv = _ao_hook_inventory(root)
    if inv["error"]:
        print(f"{C['red']}hook resolver failed{C['reset']}: {inv['error']}")
        return 1
    action = args.action
    allow = bool(getattr(args, "allow_shared_hooks", False))
    if action == "status":
        _print_hook_status(inv)
        return 0
    if action == "uninstall":
        return _hooks_uninstall(inv, allow)

    enrollment = _project_enrollment(root)
    if enrollment["state"] == "legacy":
        # The current hooks exit in their shell guard when no marker is tracked, so
        # installing them over a legacy project's enforcing hook would turn commit
        # authority off at exactly the moment someone meant to repair it.
        print(f"{C['red']}hooks install refused{C['reset']}: this project was {enrollment['detail']}")
        print("  then run: ao hooks install")
        return 1

    active = _active_hook_targets(inv)
    plan, unavailable = [], []
    for role in _HOOK_ROLES:
        target = active[role]
        base = _state_base(target["static_state"])
        if base in ("current-local", "current-scoped"):
            continue
        if target["eligible"] and base in ("absent", "legacy"):
            plan.append(target)
        else:
            unavailable.append(target)
            print(f"preserved {target['static_state']} {role} / {target['track_state']} — "
                  f"AO {'commit authority' if role == 'pre-commit' else 'push-window'} hook unavailable")
    if _authorization_refusal(plan, allow):
        return 1
    if plan:
        if os.path.lexists(inv["active_dir"]) and not os.path.isdir(inv["active_dir"]):
            print(f"{C['red']}effective hook path is not a directory{C['reset']}: {inv['active_dir']}")
            return 1
        try:
            os.makedirs(inv["active_dir"], exist_ok=True)
        except OSError as exc:
            print(f"{C['red']}cannot create hook directory{C['reset']}: {exc}")
            return 1
        # Directory creation and another process can change classification. Re-read
        # once, then authorize the whole write set again before the first replace.
        fresh = _ao_hook_inventory(root)
        if fresh["error"] or fresh["active_dir"] != inv["active_dir"] \
                or fresh["directory_class"] != inv["directory_class"] \
                or fresh["globally_configured"] != inv["globally_configured"]:
            print(f"{C['red']}hook topology changed during install; no hook written{C['reset']}")
            return 1
        fresh_active = _active_hook_targets(fresh)
        fresh_plan = [fresh_active[target["role"]] for target in plan]
        if any(not target["eligible"] or _state_base(target["static_state"])
               not in ("absent", "legacy") for target in fresh_plan):
            print(f"{C['red']}hook target changed during install; no hook written{C['reset']}")
            return 1
        if _authorization_refusal(fresh_plan, allow):
            return 1
        for target in fresh_plan:
            try:
                _atomic_hook_write(target["path"], target["body"])
                print(f"installed {target['role']} — {target['path']}")
            except OSError as exc:
                print(f"{C['red']}failed to install{C['reset']} {target['role']}: {exc}")
                return 1
    final = _ao_hook_inventory(root)
    if final["error"]:
        return 1
    final_active = _active_hook_targets(final)
    ok = all(_state_base(final_active[role]["static_state"])
             in ("current-local", "current-scoped") for role in _HOOK_ROLES)
    if ok:
        print(f"{C['green']}current (behavior unverified){C['reset']} — static hook intent installed; "
              "run `ao hooks status` for execution proof")
    return 0 if ok else 1


def cmd_push(cfg, args):
    """`ao push allow [--minutes N]` opens a window for a person's push; `check` is what the hook runs."""
    root = cfg["root"]
    key = A.project_key(root)
    tok = os.path.join(A.HOME, ".ao", f"push-{key}.ok")
    if args.action == "allow":
        os.makedirs(os.path.dirname(tok), exist_ok=True)
        json.dump({"at": int(time.time()), "minutes": args.minutes, "by": os.environ.get("USER", "human")}, open(tok, "w", encoding=UTF8))
        print(f"{C['green']}push allowed{C['reset']} for {args.minutes} minutes"); return 0
    if args.action == "check":
        try:
            t = json.load(open(tok, encoding=UTF8))
            if time.time() - t["at"] <= t.get("minutes", 30) * 60:
                return 0
        except (OSError, ValueError, KeyError):
            pass
        sys.stderr.write("agent-orchestrator: push refused — pushing is a human decision. Run `ao push allow` and push again.\n")
        return 1
    try:
        t = json.load(open(tok, encoding=UTF8)); left = t.get("minutes", 30) * 60 - (time.time() - t["at"])
        print(f"push window: {'open, ' + str(int(left // 60)) + 'm left' if left > 0 else 'closed'}")
    except (OSError, ValueError, KeyError):
        print("push window: closed")
    return 0


def _remove_inert_local(target, inv):
    """The one protected effective hook that may safely outlive project state."""
    common_hooks = os.path.realpath(os.path.join(inv["common_dir"], "hooks"))
    return bool(
        target["active"]
        and _state_base(target["static_state"]) == "current-local"
        and target["directory_class"] == "project-local"
        and not target["globally_configured"]
        and target["effective_count"] == 1
        and not target["legacy_location"]
        and os.path.realpath(target["directory"]) != common_hooks
        and (_hook_contains(inv["top"], target["directory"])
             or _hook_contains(inv["git_dir"], target["directory"]))
    )


def _remove_hook_preflight(inv, allow):
    plan, blockers, inert = [], [], []
    if inv["error"]:
        print(f"{C['red']}hook resolver failed; state kept intact{C['reset']}: {inv['error']}")
        return False, plan, inert
    for target in inv["targets"]:
        base = _state_base(target["static_state"])
        if target["eligible"] and base in ("current-local", "current-scoped", "legacy"):
            plan.append(target)
            continue
        if not target["protected"] or base not in (
            "current-local", "current-scoped", "legacy", "ambiguous-ao"
        ):
            continue
        if target["reachability"] == "dead-misplaced":
            print(f"preserved protected dead-misplaced {target['role']} (does not block remove): "
                  f"{target['path']}")
            continue
        if base == "current-local" and _remove_inert_local(target, inv):
            inert.append(target)
            print(f"preserved protected current-local {target['role']} — becomes inert after .ao removal: "
                  f"{target['path']}")
            continue
        blockers.append(target)
        print(f"{C['red']}remove blocked{C['reset']} by protected potentially-effective "
              f"{target['static_state']} {target['role']}: {target['path']}")
    if blockers or _authorization_refusal(plan, allow):
        print("AO state was not changed")
        return False, plan, inert
    return True, plan, inert


def cmd_remove(cfg, args):
    """Take ao off only after every reachable hook target passes one preflight."""
    root = cfg["root"]
    key = A.project_key(root)
    from . import skillkit
    harness_files, mcp_files = skillkit.ao_files(root)
    plan = [PROJECT_MARKER, ".ao/", "agent-mail/", cfg.get("reviews", "semantic-review") + "/"] + harness_files
    print(f"{C['b']}ao remove{C['reset']} would delete from {root}:")
    for rel in plan:
        if os.path.exists(os.path.join(root, rel)):
            print(f"   {rel}")
    for f, name, _ in mcp_files:
        p = os.path.join(root, f)
        if os.path.exists(p):
            print(f"   {f}: the `{name}` server entry (other entries stay)")
    print(f"   launchd jobs, ~/.ao state and logs for {key}, .gitignore lines")
    print(f"   {C['dim']}hook files only when statically AO-owned, untracked, and fully authorized; "
          f"protected dead-misplaced files are preserved{C['reset']}")
    print(f"   {C['dim']}not touched: product files, reviews you moved elsewhere, "
          f"{' / '.join(skillkit.rule_file_names(root))} text (paste-in was yours){C['reset']}")
    if not args.yes:
        print(f"\nre-run with {C['b']}--yes{C['reset']} to do it")
        return 0

    enrollment = _project_enrollment(root)
    if enrollment["state"] == "broken":
        print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
              f"{enrollment['detail']}")
        return 1
    if enrollment["state"] == "enrolled":
        marker_path = os.path.join(root, PROJECT_MARKER)
        if os.path.lexists(marker_path):
            if os.path.isdir(marker_path) and not os.path.islink(marker_path):
                print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
                      f"{PROJECT_MARKER} is a directory")
                return 1
            try:
                os.remove(marker_path)
            except OSError as exc:
                print(f"{C['red']}remove refused; AO state kept intact{C['reset']}: "
                      f"cannot remove {PROJECT_MARKER}: {exc}")
                return 1
            print(f"removed working-tree {PROJECT_MARKER}")
        print(f"{C['yellow']}phase 1/2{C['reset']} — AO project state and enforcement remain active")
        print(f"  stage the marker deletion: git add -u -- {PROJECT_MARKER}")
        print("  authorize and commit that deletion, then run: ao remove --yes")
        return 0

    allow = bool(getattr(args, "allow_shared_hooks", False))
    inventory = _ao_hook_inventory(root)
    safe, hook_plan, _ = _remove_hook_preflight(inventory, allow)
    if not safe:
        return 1
    for target in hook_plan:
        try:
            os.remove(target["path"])
            print(f"removed {target['role']} — {target['path']}")
        except OSError as exc:
            print(f"{C['red']}failed to remove hook; state kept intact{C['reset']} "
                  f"{target['path']}: {exc}")
            return 1

    # Re-resolve before the first state deletion. A topology race cannot turn a
    # shared live hook into an unrecorded survivor behind deleted AO state.
    after_hooks = _ao_hook_inventory(root)
    safe, new_plan, _ = _remove_hook_preflight(after_hooks, allow)
    if not safe or new_plan:
        print(f"{C['red']}hook topology changed during remove; state kept intact{C['reset']}")
        return 1

    import shutil as _sh
    subprocess.run([sys.executable, "-m", "ao", "-C", root, "watchdog", "uninstall"], capture_output=True)
    for rel in plan:
        p = os.path.join(root, rel)
        if os.path.isdir(p):
            _sh.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    for f, name, remove_when_empty in mcp_files:
        p = os.path.join(root, f)
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding=UTF8))
                if name in (d.get("mcpServers") or {}):
                    del d["mcpServers"][name]
                    json.dump(d, open(p, "w", encoding=UTF8), indent=2)
                if not d.get("mcpServers") and remove_when_empty:
                    os.remove(p)
            except (OSError, ValueError):
                pass
    gi = os.path.join(root, ".gitignore")
    if os.path.exists(gi):
        lines = open(gi, encoding=UTF8).read().split("\n")
        keep = [l for l in lines if l.strip() not in ("agent-mail/*.md", "!agent-mail/README.md", ".ao/inbox/", ".ao/hold")
                and "agent-orchestrator: mail is transient" not in l]
        open(gi, "w", encoding=UTF8).write("\n".join(keep))
    # Backlog #66 owns replacement of this basename/substring state namespace.
    for f in os.listdir(os.path.join(A.HOME, ".ao")) if os.path.isdir(os.path.join(A.HOME, ".ao")) else []:
        if key in f:
            try:
                os.remove(os.path.join(A.HOME, ".ao", f))
            except OSError:
                pass
    print(f"{C['green']}removed{C['reset']} — phase 2/2 complete; AO state is gone and preserved local source hooks are inert")
    return 0


def _alive(pid):
    # os.kill(pid, 0) is CTRL_C_EVENT on Windows, not a question (#71).
    return A._pid_alive(pid)


# What each store is for. The split matters: pruning operational noise is
# housekeeping, pruning evidence destroys the record that commit authority was
# granted against — so they cannot share a default.
STORES = [
    ("progress",      "operational", ".ao/ledger/progress.jsonl",      "watchdog samples behind the spin check"),
    ("notices",       "operational", ".ao/ledger/notices.jsonl",       "alerts raised and suppressed"),
    ("inbox",         "operational", ".ao/inbox",                      "imported source pulls (*.imported)"),
    ("verifications", "evidence",    ".ao/ledger/verifications.jsonl", "measured gate results"),
    ("plans",         "evidence",    ".ao/ledger/plans.jsonl",         "plan hashes as admitted"),
]
HOME_LOGS = ["nudge-{key}.log", "watchdog-{key}.log", "refill-{key}.log"]


def _prune_jsonl(path, cutoff, dry):
    """Drop records older than cutoff. Returns (dropped, kept, bytes_freed)."""
    if not os.path.exists(path):
        return 0, 0, 0
    before = os.path.getsize(path)
    keep = []
    dropped = 0
    for line in open(path, errors="replace", encoding=UTF8):
        if not line.strip():
            continue
        try:
            if json.loads(line).get("at", 0) < cutoff:
                dropped += 1
                continue
        except Exception:
            pass                                  # unparseable: keep it, do not silently lose data
        keep.append(line if line.endswith("\n") else line + "\n")
    if dropped and not dry:
        from .storage import replace_file_durably
        replace_file_durably(path, "".join(keep).encode(UTF8))
    freed = before - sum(len(k.encode()) for k in keep) if dropped else 0
    return dropped, len(keep), max(0, freed)


def cmd_prune(cfg, args):
    """Trim the records this tool accumulates, without touching the audit trail.

    Every store here grows monotonically — one nudge log reached 295 KB in a
    night, and the progress ledger gains a row every two minutes. Left alone they
    become the reason someone stops running the tool.

    Evidence is excluded by default and needs `--evidence`. Verification records
    and plan hashes are what commit authority was granted against; deleting them
    as housekeeping would quietly remove the ability to answer "on what basis did
    this land".
    """
    root = cfg["root"]
    cutoff = time.time() - args.days * 86400
    dry = not args.yes
    key = A.project_key(root)
    print(f"{C['b']}pruning records older than {args.days} day(s){C['reset']}"
          f"{C['dim']}  {root}{C['reset']}")
    if dry:
        print(f"{C['yellow']}dry run — add --yes to apply{C['reset']}")

    total = 0
    for name, kind, rel, desc in STORES:
        if kind == "evidence" and args.evidence:
            # Evidence is never pruned. A chained ledger past its bound is sealed:
            # its oldest rows move whole to .ao/ledger/sealed/ and stay readable (#50).
            keep = S.get(cfg, "retention.evidence_keep")
            sealed = None
            if name == "verifications" and not dry:
                from .storage import seal_chained_jsonl
                sealed = seal_chained_jsonl(os.path.join(root, rel), A.VERIFICATION_CHAIN, keep, legacy_prefix=True)
            print(f"  {C['dim']}keep  {name:<14} evidence is never pruned; "
                  + (f"sealed through row {sealed['retired']} into .ao/ledger/sealed/" if sealed
                     else f"rows past the newest {keep} are sealed, not dropped") + f"{C['reset']}")
            continue
        if kind == "evidence" and not args.evidence:
            path = os.path.join(root, rel)
            if os.path.exists(path):
                n = sum(1 for _ in open(path, errors="replace", encoding=UTF8))
                print(f"  {C['dim']}skip  {name:<14} {n} records — evidence, needs --evidence{C['reset']}")
            continue
        path = os.path.join(root, rel)
        if name == "inbox":
            if not os.path.isdir(path):
                continue
            gone = 0
            for f in os.listdir(path):
                fp = os.path.join(path, f)
                if f.endswith(".imported") and os.path.getmtime(fp) < cutoff:
                    total += os.path.getsize(fp)
                    gone += 1
                    if not dry:
                        os.remove(fp)
            if gone:
                print(f"  {C['green']}{'would drop' if dry else 'dropped'}{C['reset']}  "
                      f"{name:<14} {gone} file(s)  {C['dim']}{desc}{C['reset']}")
            continue
        dropped, kept, freed = _prune_jsonl(path, cutoff, dry)
        total += freed
        if dropped:
            print(f"  {C['green']}{'would drop' if dry else 'dropped'}{C['reset']}  "
                  f"{name:<14} {dropped} of {dropped + kept}  {C['dim']}{desc}{C['reset']}")

    # Review artefacts stay while anything rests on them; the rest leave by age (#38).
    review_days = getattr(args, "review_days", None)
    if review_days is None:
        review_days = S.get(cfg, "review.prune_after_days")
    try:
        outcome = A.prune_review_artefacts(root, cfg, review_days, apply=not dry)
    except Exception as exc:
        print(f"  {C['yellow']}keep  {'reviews':<14} every artefact: what rests on them cannot be read "
              f"({exc}){C['reset']}")
    else:
        why = {}
        for reasons in outcome["kept"].values():
            for reason in reasons:
                why[reason] = why.get(reason, 0) + 1
        if outcome["moved"]:
            total += outcome["bytes"]
            print(f"  {C['green']}{'would move' if dry else 'moved'}{C['reset']}  {'reviews':<14} "
                  f"{len(outcome['moved'])} older than {review_days:g} day(s) that nothing rests on  "
                  f"{C['dim']}→ {outcome['archive']}{C['reset']}")
        if outcome["kept"] or outcome["recent"]:
            detail = ", ".join(f"{count} {reason}" for reason, count in sorted(why.items()))
            print(f"  {C['dim']}keep  {'reviews':<14} {len(outcome['kept'])} referenced"
                  + (f" ({detail})" if detail else "") + f", {outcome['recent']} recent{C['reset']}")

    # The authority chain is never truncated by deletion: past its bound it is sealed (#50).
    if args.evidence and not dry:
        from .storage import seal_chained_jsonl
        sealed = seal_chained_jsonl(os.path.join(root, ".ao", "ledger", "authority.jsonl"), A.AUTHORITY_CHAIN,
                                    S.get(cfg, "retention.evidence_keep"))
        if sealed:
            print(f"  {C['green']}sealed{C['reset']}  {'authority':<14} through row {sealed['retired']}; "
                  f"{C['dim']}ao commit-check validates across the seal{C['reset']}")
    # Dedupe by inode, not by path string: this filesystem is case-insensitive, so
    # "nudge-Voltrai.log" and "nudge-voltrai.log" are one file that would
    # otherwise be counted — and truncated — twice.
    seen_inodes = set()
    for pattern in HOME_LOGS:
        path = os.path.join(A.HOME, ".ao", pattern.format(key=key))
        alt = os.path.join(A.HOME, ".ao", pattern.format(key=key.lower()))
        for pth in {path, alt}:
            if not os.path.exists(pth):
                continue
            ino = os.stat(pth).st_ino
            if ino in seen_inodes:
                continue
            seen_inodes.add(ino)
            size = os.path.getsize(pth)
            if size < args.keep_kb * 1024:
                continue
            if not dry:
                # keep the tail: the last turn's output is the only part anyone
                # reads, and it is what a failed nudge is diagnosed from
                with open(pth, errors="replace", encoding=UTF8) as fh:
                    fh.seek(max(0, size - args.keep_kb * 1024))
                    tail = fh.read()
                with open(pth, "w", encoding=UTF8) as fh:
                    fh.write(f"[truncated by ao prune {datetime.now():%Y-%m-%d %H:%M}]\n" + tail)
            total += size - args.keep_kb * 1024
            print(f"  {C['green']}{'would trim' if dry else 'trimmed'}{C['reset']}  "
                  f"{os.path.basename(pth):<24} {size // 1024}KB → {args.keep_kb}KB")

    print(f"\n{C['b']}{total // 1024}KB{C['reset']} {'reclaimable' if dry else 'reclaimed'}")
    return 0


def cmd_notices(cfg, args):
    """Alerts this project raised — the desktop notification, kept.

    A notification reaches the human and vanishes, so the architect reading the
    panel is the one participant who never sees what the human was told.
    """
    root = cfg["root"]
    wanted = getattr(args, "ident", None)
    if wanted:
        # "Why did I get this?" is one command (#37).
        row = next((r for r in A.notices(root, 10**9, include_suppressed=True) if r.get("id") == wanted), None)
        if not row:
            print(f"no notice {wanted}; `ao notices --all` lists them with their ids")
            return 1
        when = datetime.fromtimestamp(row["at"]).strftime("%d %b %H:%M")
        print(f"{C['b']}{row['title']}{C['reset']}  {C['dim']}{wanted} · {when} · "
              f"{'sent' if row.get('sent') else 'held'} · key {row.get('key')}{C['reset']}")
        print(f"  {row.get('msg')}")
        for line in A.evidence_lines(row.get("evidence")):
            print(f"  {line}")
        return 0
    rows = A.notices(root, args.n, include_suppressed=args.all)
    if not rows:
        print(f"{C['dim']}No notices recorded.{C['reset']}")
        return 0
    for r in rows:
        when = datetime.fromtimestamp(r["at"]).strftime("%d %b %H:%M")
        tag = (f"{C['green']}sent{C['reset']}" if r.get("sent")
               else f"{C['dim']}held{C['reset']}")
        print(f"  {C['dim']}{when}{C['reset']}  {tag}  {C['b']}{r['title']}{C['reset']}  {r['msg']}"
              + (f"  {C['dim']}{r['id']}{' · evidence' if r.get('evidence') else ''}{C['reset']}"
                 if r.get("id") else ""))
    if not args.all:
        print(f"{C['dim']}  (--all also shows alerts the rate limit suppressed){C['reset']}")
    return 0


def _watchdog_windows(cfg, args):
    """Task Scheduler is Windows' launchd: one task every two minutes, one every fifteen."""
    root = cfg["root"]
    key = A.project_key(root).lower()
    tasks = {f"ao-watchdog-{key}": (2, f'"{shutil.which("ao-watchdog") or "ao-watchdog"}" --root "{root}" --idle-minutes {getattr(args, "idle_minutes", None) or S.get(cfg, "watchdog.idle_minutes")}'),
             f"ao-doctor-{key}": (15, f'"{shutil.which("ao") or "ao"}" -C "{root}" doctor --check --notify')}
    if args.action == "status":
        for name in tasks:
            r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True, text=True, encoding=UTF8, errors="replace")
            print(f"{name}: {'scheduled' if r.returncode == 0 else 'absent'}")
        return 0
    if args.action == "uninstall":
        for name in tasks:
            subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True)
            print(f"removed {name}")
        return 0
    for name, (minutes, cmd) in tasks.items():
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes), "/TN", name, "/TR", cmd],
                           capture_output=True, text=True, encoding=UTF8, errors="replace")
        print(f"{'installed' if r.returncode == 0 else 'FAILED'} {name} (every {minutes}m)" + ("" if r.returncode == 0 else f": {r.stderr.strip()[:120]}"))
    return 0


def cmd_watchdog(cfg, args):
    if getattr(args, "idle_minutes", None) is None:
        args.idle_minutes = S.get(cfg, "watchdog.idle_minutes")
    return _cmd_watchdog(cfg, args)


def _cmd_watchdog(cfg, args):
    """Install, remove or inspect the launchd job that restarts a stalled agent."""
    if args.action in ("explain", "trace"):
        return _watchdog_debug(cfg, args)
    if os.name == "nt":
        return _watchdog_windows(cfg, args)
    import getpass
    root = cfg["root"]
    key = A.project_key(root).lower()
    label = f"com.agentorchestrator.watchdog.{key}"
    plist_path = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
    # After a pip/uv install there is no scripts/ directory; there is a console
    # script on PATH. launchd needs an absolute path either way, so resolve
    # whichever one this installation actually has.
    script = shutil.which("ao-watchdog") or os.path.join(A.REPO, "scripts", "ao-watchdog")
    # A console script carries its own interpreter in its shebang — the venv's.
    # Prefixing it with *this* process's python (the system one, if `ao watchdog
    # install` was run from a checkout) imports `ao` from an interpreter that
    # does not have it, and launchd logs ModuleNotFoundError every two minutes.
    # Only our repo shim needs an explicit interpreter, because its shebang is
    # `/usr/bin/env python3` and launchd's PATH is minimal.
    in_repo = os.path.realpath(script).startswith(os.path.realpath(A.REPO))
    python = sys.executable if in_repo else ""
    log = os.path.expanduser(f"~/.ao/watchdog-{key}.log")

    if args.action == "status":
        loaded = A.sh(f"launchctl list | grep {label}")
        dloaded = A.sh(f"launchctl list | grep com.agentorchestrator.doctor.{key}")
        print(f"label   {label}")
        print(f"doctor  {'loaded' if dloaded else 'not installed'}  (ao doctor --check every 15m)")
        print(f"plist   {'present' if os.path.exists(plist_path) else 'absent'}")
        print(f"loaded  {loaded if loaded else 'no'}")
        from .watchdog import cycle_health
        health = cycle_health(root)
        if health:
            took = (f"longest took {health['longest_seconds']:.0f}s at {health['longest_at']}"
                    if health["longest_seconds"] is not None else "durations not recorded yet")
            gap = (f"longest gap {health['gap_minutes']:.0f}m before {health['gap_at']}"
                   if health["gap_at"] else "no gaps")
            print(f"cycles  {health['count']} recorded · {took} · {gap}")
        if os.path.exists(log):
            print(f"\nlast lines of {log}:")
            print(A.sh(f"tail -5 {log}"))
        return

    if args.action == "uninstall":
        A.sh(f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null || launchctl unload {plist_path} 2>/dev/null")
        if os.path.exists(plist_path):
            os.remove(plist_path)
        print(f"removed {label}")
        dlabel = f"com.agentorchestrator.doctor.{key}"
        dplist = os.path.expanduser(f"~/Library/LaunchAgents/{dlabel}.plist")
        A.sh(f"launchctl bootout gui/$(id -u)/{dlabel} 2>/dev/null")
        if os.path.exists(dplist):
            os.remove(dplist)
            print(f"removed {dlabel}")
        # A heartbeat left behind reads as a dead watchdog to every other project (audit).
        try:
            os.remove(A.heartbeat_path(root))
        except OSError:
            pass
        return

    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
    os.makedirs(os.path.expanduser("~/.ao"), exist_ok=True)
    open(plist_path, "w", encoding=UTF8).write(PLIST.format(
        path=_launchd_path(), label=label, python_arg=(f"<string>{python}</string>" if python else ""),
        script=script, root=root,
        idle=args.idle_minutes, interval=args.interval, log=log))
    A.sh(f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null")
    # bootout is asynchronous: a bootstrap issued before the old job is fully
    # gone fails with "5: Input/output error" and leaves nothing loaded — a
    # reinstall that silently uninstalled. Wait for the label to clear, retry.
    out = ""
    for attempt in range(5):
        if A.sh(f"launchctl list | grep {label}"):
            time.sleep(1)
        out = A.sh(f"launchctl bootstrap gui/$(id -u) {plist_path} 2>&1") or "loaded"
        if "error" not in out.lower() or A.sh(f"launchctl list | grep {label}"):
            break
        time.sleep(1 + attempt)
    if not A.sh(f"launchctl list | grep {label}"):
        print(f"{C['red']}NOT LOADED{C['reset']} {label}: {out.strip()[:120]} — run: launchctl bootstrap gui/$(id -u) {plist_path}")
        return 1
    print(f"installed {label}")
    # The second, independent check. A watchdog cannot report its own death;
    # this job runs `ao doctor --check --notify` every fifteen minutes from its own
    # launchd entry and raises the alarm the watchdog would have.
    dlabel = f"com.agentorchestrator.doctor.{key}"
    dplist = os.path.expanduser(f"~/Library/LaunchAgents/{dlabel}.plist")
    ao_exe = shutil.which("ao") or os.path.join(A.REPO, "bin", "ao")
    ao_in_repo = os.path.realpath(ao_exe).startswith(os.path.realpath(A.REPO))
    dargs = ([sys.executable] if ao_in_repo else []) + [ao_exe, "-C", root, "doctor", "--check", "--notify"]
    open(dplist, "w", encoding=UTF8).write(PLIST_CMD.format(
        path=_launchd_path(), label=dlabel, args="".join(f"<string>{a}</string>" for a in dargs),
        interval=900, log=os.path.expanduser(f"~/.ao/doctor-{key}.log")))
    A.sh(f"launchctl bootout gui/$(id -u)/{dlabel} 2>/dev/null")
    A.sh(f"launchctl bootstrap gui/$(id -u) {dplist} 2>&1")
    print(f"installed {dlabel}  (ao doctor --check --notify every 15m — the second, independent check)")
    print(f"  checks every {args.interval}s · nudges after {args.idle_minutes}m idle")
    print(f"  log: {log}")
    print(f"  remove with: ao -C {root} watchdog uninstall")
    if "error" in out.lower():
        print(f"  launchctl: {out}")


def cmd_projects(cfg, args):
    ws = A.all_workspaces()
    if not ws:
        print("No local agent sessions found.")
    else:
        print(f"{'last active':<12}{'status':<14}workspace")
    for r in ws:
        mins = int((time.time() - r["mtime"]) / 60)
        age = f"{mins}m" if mins < 90 else (f"{mins//60}h" if mins < 2880 else f"{mins//1440}d")
        col = C["green"] if mins < 5 else C["dim"]
        print(f"{col}{age:<12}{C['reset']}{r['status'][:13]:<14}{r['path']}")
    # Directories with one name used to share every file ao keeps outside them (#66).
    for base, rows in A.project_key_collisions().items():
        print(f"\n{C['yellow']}{len(rows)} projects are named {base}{C['reset']}; "
              "each keeps its own files under its key:")
        for key, where in rows:
            print(f"  {key:<28}{where}")


def cmd_adapters(cfg, args):
    """Every adapter ao can load, where it came from, and whether a candidate is sound (#77)."""
    root = cfg["root"]
    action = getattr(args, "action", None) or "list"
    if action in ("validate", "conform"):
        target = getattr(args, "target", None)
        catalog = A.adapter_catalog(root)
        if target in catalog:
            adapter = catalog[target]["adapter"]
        else:
            try:
                with open(target or "", encoding=UTF8) as fh:
                    adapter = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"{C['red']}no adapter {target}{C['reset']}: {exc}")
                return 2
        if action == "validate":
            problems = A.validate_adapter(adapter)
            for problem in problems:
                print(f"  {C['red']}·{C['reset']} {problem}")
            print(f"{C['green']}sound{C['reset']}" if not problems else f"{len(problems)} problem(s)")
            return 1 if problems else 0
        import tempfile
        harness = os.path.join(os.path.dirname(A.__file__), "conformance_harness.py")
        with tempfile.TemporaryDirectory(prefix="ao-conform-") as workdir:
            shim = os.path.join(workdir, "harness")
            with open(shim, "w", encoding=UTF8) as fh:
                fh.write(f"#!{sys.executable}\n" + open(harness, encoding=UTF8).read())
            os.chmod(shim, 0o755)
            results = A.conform_adapter(adapter, shim, workdir)
        for capability, state, detail in results:
            tone = C["green"] if state == "pass" else C["red"] if state == "fail" else C["dim"]
            print(f"  {capability:<11} {tone}{state}{C['reset']}  {C['dim']}{detail}{C['reset']}")
        return 1 if any(state == "fail" for _, state, _ in results) else 0
    avail = A.tool_availability()
    print(f"{'adapter':<16}{'source':<9}{'contract':<10}{'verified':<12}{'on this machine':<22}observation")
    for ident, entry in sorted(A.adapter_catalog(root).items()):
        a = entry["adapter"]
        if a.get("kind") == "cloud":
            continue
        if entry["problem"]:
            print(f"{ident:<16}{entry['source']:<9}{C['red']}refused{C['reset']}  {entry['problem']}")
            continue
        verified = a.get("verified", "?")
        col = C["green"] if verified == "full" else C["yellow"] if verified == "partial" else C["dim"]
        have = avail.get(ident, {})
        here = ("installed + account" if have.get("installed") and have.get("account") else "installed"
                if have.get("installed") else "account, no CLI" if have.get("account") else "—")
        observation = "call-return" if a.get("observation_mode") == "call-return" \
            else (a.get("transcript", {}) or {}).get("kind", "—")
        eligible, _ = A.reviewer_eligibility(a)
        print(f"{ident:<16}{entry['source']:<9}{str(a.get('contract', A.ADAPTER_CONTRACT)):<10}{col}{verified:<12}"
              f"{C['reset']}{here:<22}{observation:<14}"
              f"{'reviewer: eligible' if eligible else C['dim'] + 'reviewer: ineligible' + C['reset']}")
    for vendor in A.vendor_list():
        if not vendor.get("adapter"):
            print(f"{vendor['id']:<16}{C['dim']}{'vendor':<9}{'—':<10}{'no adapter':<12}{vendor.get('why', '')}"
                  f"{C['reset']}")
    for problem in A.vendor_problems():
        print(f"{C['red']}vendor list{C['reset']}: {problem}")
    print(f"\n{C['dim']}Account detection via keyflip surfaces; it never reads the secret. Adapters load from the "
          f"package, then ~/.ao/adapters, then .ao/adapters; a later one overrides by id.{C['reset']}")


def _optional_features(cfg):
    """(name, state, what would enable it) for each optional capability, core excluded (#82)."""
    from . import email, telegram
    root = cfg["root"]
    mail = email.config()
    return [
        ("keyflip", "installed" if shutil.which("keyflip") else "absent",
         "install keyflip for account budgets and quota rotation"),
        ("telegram", "configured" if telegram.config() else "absent", "ao telegram setup"),
        ("email", f"configured ({mail['provider']})" if mail else "absent", "ao email setup"),
        ("ping", "configured" if A.ping_url(root) else "absent", "ao ping set <url>"),
    ]


def _measurement_lines(cfg):
    """How ao measures, and what could filter the numbers an agent reads (#51)."""
    filters = A.measurement_filters(cfg["root"])
    state = (f"{C['yellow']}{len(filters)} possible filter(s){C['reset']}" if filters
             else f"{C['green']}unfiltered{C['reset']}")
    lines = [f"{'measurement':<16}{state}  {C['dim']}ao measures with {A.git_binary()}, "
             f"the candidate without a shell{C['reset']}"]
    return lines + [f"{'':<16}{C['dim']}{text}{C['reset']}" for text in filters]


def _review_evidence_lines(cfg):
    """The reviews a grant rests on that git does not hold: one disk from gone (#38)."""
    try:
        risky = A.grant_artefacts_at_risk(cfg["root"], cfg)
    except Exception as exc:
        return [f"{'review evidence':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    if not risky:
        return [f"{'review evidence':<16}{C['green']}every review a grant rests on is in git{C['reset']}"]
    untracked = sum(1 for _, state in risky if state == "untracked")
    lines = [f"{'review evidence':<16}{C['yellow']}{untracked} untracked, {len(risky) - untracked} missing of "
             f"the reviews grants rest on{C['reset']}  {C['dim']}commit them; ao prune never moves them{C['reset']}"]
    lines += [f"{'':<16}{C['dim']}{state:<9} {name}{C['reset']}" for name, state in risky[:5]]
    if len(risky) > 5:
        lines.append(f"{'':<16}{C['dim']}and {len(risky) - 5} more{C['reset']}")
    return lines


def _architect_absence_lines(cfg):
    """How long the architect has been away and how many questions wait for it (#84)."""
    try:
        away = A.architect_absence(cfg["root"], cfg)
    except Exception as exc:
        return [f"{'architect away':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    seen = (f"last seen {_elapsed(time.time() - away['seen_at'])} ago" if away["seen_at"]
            else "not seen in this project's records")
    if not away["waiting"]:
        return [f"{'architect away':<16}{C['dim']}{seen} · no question waiting{C['reset']}"]
    oldest = _elapsed(time.time() - float(away["oldest_at"] or time.time()))
    return [f"{'architect away':<16}{C['yellow']}{seen} · {len(away['waiting'])} question(s) waiting, the oldest "
            f"{away['waiting'][0]} for {oldest}{C['reset']}  {C['dim']}answered in one pass: ao decisions{C['reset']}"]


def _human_bytes(count):
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024


def cmd_worktrees(cfg, args):
    """Every worktree, what keeps it and whether it may go; `prune` retires those that may (#42).

    Seven worktrees stood on this machine, one per slice, none removed when its
    branch landed, each a full checkout with its own state and stale reviews.
    Prune is a dry run until --yes.
    """
    root = cfg["root"]
    try:
        facts = A.worktree_facts(root, cfg, sizes=True)
    except RuntimeError as exc:
        print(f"{C['red']}{exc}{C['reset']}")
        return 2
    pruning = getattr(args, "action", None) == "prune"
    apply = pruning and getattr(args, "yes", False)
    if pruning and not apply:
        print(f"{C['yellow']}dry run — add --yes to apply{C['reset']}")
    for fact in facts:
        size = _human_bytes(fact["bytes"]) if fact["bytes"] is not None \
            else "gone" if not os.path.isdir(fact["path"]) else "—"
        state = (f"{C['green']}may go{C['reset']} ({fact['why']})" if fact["may_go"]
                 else f"{C['dim']}keep{C['reset']} ({'; '.join(fact['keep']) or 'not merged, slice not rejected'})")
        print(f"  {fact['path']}  {C['dim']}{fact['branch'] or 'detached'} · {size}{C['reset']}  {state}")
        if pruning and fact["may_go"]:
            try:
                for step in A.prune_worktree(root, fact, apply=apply):
                    print(f"      {'done' if apply else 'would'}: {step}")
            except (OSError, RuntimeError) as exc:
                print(f"      {C['red']}stopped{C['reset']}: {exc}")
                return 1
    return 0


def _worktree_lines(cfg):
    """Worktrees whose branch is merged or gone, with their size on disk (#42)."""
    try:
        going = [fact for fact in A.worktree_facts(cfg["root"], cfg, sizes=True) if fact["may_go"]]
    except Exception as exc:
        return [f"{'worktrees':<16}{C['yellow']}cannot tell: {exc}{C['reset']}"]
    if not going:
        return []
    total = sum(fact["bytes"] or 0 for fact in going)
    lines = [f"{'worktrees':<16}{C['yellow']}{len(going)} may go ({_human_bytes(total)}){C['reset']}  "
             f"{C['dim']}ao worktrees prune{C['reset']}"]
    lines += [f"{'':<16}{C['dim']}{fact['path']}  {fact['why']}  "
              f"{_human_bytes(fact['bytes']) if fact['bytes'] is not None else 'gone'}{C['reset']}" for fact in going[:5]]
    return lines


PROVE_BOUNDARY = ("ao prove: a throwaway candidate that adds one line to ao-prove.txt and nothing else. "
                  "Approve it if the diff is exactly that.")


def _prove_throwaway(cfg):
    """A one-line slice through verify, review and commit-ok in a temporary worktree; nothing lands (#85).

    Returns (ok, what failed or None, what would fix it or None). The worktree's
    coordination state and review go to ~/.ao/archive/<project>/prove-<stamp>/.
    """
    import contextlib
    import io
    import shutil
    import tempfile
    from types import SimpleNamespace
    root = cfg["root"]
    scratch = tempfile.mkdtemp(prefix="ao-prove-")
    tree = os.path.join(scratch, "tree")
    links, heard = [], io.StringIO()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        added = subprocess.run([A.git_binary(), "worktree", "add", "--detach", "--quiet", tree, "HEAD"], cwd=root,
                               capture_output=True)
        if added.returncode:
            return False, "a temporary worktree could not be made: " + added.stderr.decode(UTF8, "replace").strip(), \
                "a repository with at least one commit"
        os.makedirs(os.path.join(tree, ".ao"), exist_ok=True)
        for name in ("config.json", "gates.json"):
            if os.path.exists(os.path.join(root, ".ao", name)):
                shutil.copy2(os.path.join(root, ".ao", name), os.path.join(tree, ".ao", name))
        with open(os.path.join(tree, ".ao", "board.md"), "w", encoding=UTF8) as fh:
            fh.write(f"# Board\n\n## running\n- [AO-PROVE] a throwaway one-line change · acceptance: {PROVE_BOUNDARY}\n"
                     "\n## blocked\n\n## queued\n\n## verified\n\n## done\n")
        for name in S.get(cfg, "merge.link_paths"):
            source, target = os.path.join(root, name), os.path.join(tree, name)
            if os.path.exists(source) and not os.path.lexists(target):
                try:
                    os.symlink(source, target)
                    links.append(target)
                except OSError:
                    pass
        with open(os.path.join(tree, "ao-prove.txt"), "a", encoding=UTF8) as fh:
            fh.write(f"ao prove {stamp}\n")
        subprocess.run([A.git_binary(), "add", "ao-prove.txt"], cwd=tree, check=True, capture_output=True)
        tree_cfg = A.load_config(tree)
        with contextlib.redirect_stdout(heard):
            verified = cmd_verify(tree_cfg, SimpleNamespace(profile="quick", wait=900))
        if verified != 0:
            return False, "the quick gates did not pass a one-line change", \
                "run `ao verify -p quick` and read the failing gate:\n" + _last_lines(heard.getvalue())
        with contextlib.redirect_stdout(heard):
            reviewed = cmd_review(tree_cfg, SimpleNamespace(action=None, rid=None, any=False, run=None, boundary=None,
                                                            paths=None, commits=None, timeout=None))
        if reviewed != 0:
            return False, "the reviewer did not approve a trivial candidate", \
                "read the review it wrote (archived below); exit 3 means no reviewer could review:\n" \
                + _last_lines(heard.getvalue())
        with contextlib.redirect_stdout(heard):
            granted = cmd_commit_ok(tree_cfg, SimpleNamespace(verify=False, profile=None, review=None))
        if granted != 0:
            return False, "ao commit-ok refused the approved candidate", _last_lines(heard.getvalue())
        return True, None, None
    finally:
        for link in links:
            try:
                os.unlink(link)
            except OSError:
                pass
        archive = os.path.join(A.HOME, ".ao", "archive", A.project_key(root), f"prove-{stamp}")
        for part in (".ao", cfg.get("reviews", "semantic-review")):
            if os.path.isdir(os.path.join(tree, part)):
                shutil.copytree(os.path.join(tree, part), os.path.join(archive, part), symlinks=True,
                                dirs_exist_ok=True)
        subprocess.run([A.git_binary(), "worktree", "remove", "--force", tree], cwd=root, capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)
        subprocess.run([A.git_binary(), "worktree", "prune"], cwd=root, capture_output=True)


def _last_lines(text, count=6):
    lines = [A.re.sub(r"\x1b\[[0-9;]*m", "", line) for line in text.strip().splitlines() if line.strip()]
    return "\n".join("      " + line for line in lines[-count:])


def cmd_prove(cfg, args):
    """Run the guarantees instead of describing them, and say what would fix each one that fails (#85).

    The hook must refuse a synthetic candidate, the reviewer must answer and be
    another actor than the implementer, and a throwaway slice must go through
    verify, review and commit-ok in a temporary worktree. Nothing is committed.
    """
    root = cfg["root"]
    results = []
    probe = _hook_execution_probe(_ao_hook_inventory(root))
    results.append(("hook refuses an unauthorised commit", probe["installed"],
                    None if probe["installed"] else f"{_hook_probe_text(probe)} — ao hooks install"))
    reviewer = _reviewer_probe(cfg)
    reviewer_ok = reviewer["ok"] and reviewer["configured"]
    results.append(("reviewer answers and is another actor", reviewer_ok, None if reviewer_ok else
                    f"{_reviewer_probe_text(reviewer)} — name a reviewer of another model family in .ao/config.json "
                    "(docs/roles.md)"))
    if getattr(args, "no_review", False):
        results.append(("a throwaway slice lands end to end", False, "skipped with --no-review: not proven"))
    elif not reviewer_ok:
        results.append(("a throwaway slice lands end to end", False, "not tried: no reviewer can review"))
    else:
        ok, failed, fix = _prove_throwaway(cfg)
        results.append(("a throwaway slice lands end to end", ok, None if ok else f"{failed} — {fix}"))
    for claim, ok, fix in results:
        print(f"  {C['green'] + 'proven' if ok else C['red'] + 'NOT PROVEN'}{C['reset']}  {claim}")
        if fix:
            print(f"      {fix}")
    proven = all(ok for _, ok, _ in results)
    print(f"\n{C['b']}{'PROVEN' if proven else 'NOT PROVEN'}{C['reset']}  "
          f"{C['dim']}nothing was committed; the throwaway worktree is gone{C['reset']}")
    return 0 if proven else 1


def _init_then_prove(cfg, args):
    code = cmd_init(cfg, args)
    if code or not getattr(args, "prove", False):
        return code
    print(f"\n{C['b']}proving the guarantees{C['reset']}")
    return cmd_prove(A.load_config(cfg["root"]), args)


def _doctor_consistency(cfg, repair=False):
    """`ao doctor --consistency [--repair]`: the four stores checked against each other (#47)."""
    root = cfg["root"]
    findings = A.consistency_findings(root, cfg)
    if repair:
        for finding in A.repair_consistency(root, findings):
            print(f"{C['green']}repaired{C['reset']}  {finding['text']}  {C['dim']}recorded in "
                  f".ao/ledger/repairs.jsonl{C['reset']}")
        findings = A.consistency_findings(root, cfg)
    if not findings:
        print(f"{C['green']}consistent{C['reset']}  board, ledgers, review artefacts and git agree")
        return 0
    for finding in findings:
        mark = f"{C['yellow']}repairable{C['reset']}" if finding["repair"] else f"{C['red']}disagrees{C['reset']}"
        print(f"{mark}  {finding['kind']}: {finding['text']}")
    if any(finding["repair"] for finding in findings):
        print(f"{C['dim']}ao doctor --consistency --repair fixes the repairable ones and records what it did{C['reset']}")
    return 1


def _store_bound_lines(cfg):
    """Observation stores past their bound (#50): the watchdog holds them each cycle, so one here means it is not."""
    from .watchdog import STATE_DIR
    over = A.stores_over_bound(cfg["root"], cfg, STATE_DIR)
    if not over:
        return []
    return [f"{'stores':<16}{C['yellow']}{len(over)} over their bound{C['reset']}  "
            f"{C['dim']}{', '.join(f'{os.path.basename(p)} {size // 1024}KB/{limit // 1024}KB' for p, size, limit in over)}"
            f" — the watchdog trims them each cycle; is it running?{C['reset']}"]


def cmd_backup(cfg, args):
    """Write the project's governance to the destination it names: a directory, `ref`, or `remote:<name>` (#46)."""
    destination = getattr(args, "to", None) or (cfg.get("backup") or {}).get("to")
    if not destination:
        print(f"{C['yellow']}no destination{C['reset']}: `ao backup --to <directory|ref|remote:name>`, or name one as "
              "backup.to in .ao/config.json")
        return 2
    try:
        manifest = A.write_backup(cfg["root"], cfg, destination)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"{C['red']}not backed up{C['reset']}: {exc}")
        return 1
    print(f"{C['green']}backed up{C['reset']} {len(manifest['files'])} governance file(s) → {manifest['where']}")
    return 0


def cmd_restore(cfg, args):
    """Reconstruct the control plane from a backup directory; say what could not be verified (#46)."""
    root = cfg["root"]
    try:
        restored, unverified = A.restore_backup(root, args.source)
    except (OSError, ValueError, KeyError) as exc:
        print(f"{C['red']}not restored{C['reset']}: {exc}")
        return 2
    print(f"restored {len(restored)} file(s)")
    for line in unverified:
        print(f"  {C['red']}not verified{C['reset']}  {line}")
    try:
        A.authority_rows(root)
        A.board(root)
        print(f"{C['green']}the authority chain and the board validate{C['reset']}")
    except Exception as exc:
        print(f"{C['red']}restored state does not validate{C['reset']}: {exc}")
        return 1
    return 1 if unverified else 0


def _backup_lines(cfg):
    """How old the newest backup is, and whether governance exists with none (#46)."""
    age = A.backup_age(cfg["root"])
    if age is None:
        return [f"{'backup':<16}{C['yellow']}none{C['reset']}  {C['dim']}the control plane is on this disk only — "
                f"ao backup --to <directory|ref|remote:name>{C['reset']}"]
    seconds, where = age
    tone = C["green"] if seconds < 7 * 86400 else C["yellow"]
    return [f"{'backup':<16}{tone}{_elapsed(seconds)} ago{C['reset']}  {C['dim']}{where}{C['reset']}"]


def cmd_doctor(cfg, args):
    if getattr(args, "consistency", False):
        return _doctor_consistency(cfg, repair=getattr(args, "repair", False))
    if getattr(args, "check", False):
        # Scheduled checks return through the existing static helper here;
        # only the manual path below invokes the reviewer nonce probe.
        if getattr(args, "notify", False):
            return _doctor_check(cfg, page=True)
        return _doctor_check(cfg)
    root, impl, adapter = _ctx(cfg)
    ok = lambda b: f"{C['green']}ok{C['reset']}" if b else f"{C['red']}missing{C['reset']}"

    # Run outside a project, every check is "missing" and none of it is a fault.
    # Say that instead of printing a wall of red.
    if not impl and not os.path.isdir(os.path.join(root, cfg["mailbox"])):
        print(f"{C['yellow']}No project here.{C['reset']} {C['dim']}{root}{C['reset']}\n")
        ws = A.all_workspaces()[:5]
        if ws:
            print("Point at one of these:")
            for r in ws:
                mins = int((time.time() - r["mtime"]) / 60)
                age = f"{mins}m ago" if mins < 90 else f"{mins // 60}h ago"
                print(f"  {C['b']}ao -C {r['path']} doctor{C['reset']}  {C['dim']}{age}{C['reset']}")
        else:
            print(f"{C['dim']}No local agent sessions found. See docs/adapters.md.{C['reset']}")
        return
    print(f"root            {root}")
    # Configuration is optional: discovery covers the common case, so its absence
    # is a fact, not a fault. Red is reserved for things that need fixing.
    has_cfg = os.path.exists(os.path.join(root, ".ao", "config.json"))
    print(f"config          " + (f"{C['green']}.ao/config.json{C['reset']}" if has_cfg
                                 else f"{C['dim']}none — using auto-discovery{C['reset']}"))
    print(f"implementer     " + (f"{impl.get('adapter')} / {impl.get('session','')[:24]}"
                                 if impl else f"{C['red']}none found{C['reset']}"))
    msgs, _ = A.session_paths(cfg)
    print(f"transcript      {ok(bool(msgs and os.path.exists(msgs)))}")
    print(f"mailbox         {ok(os.path.isdir(os.path.join(root, cfg['mailbox'])))}")
    print(f"reviews         {ok(os.path.isdir(os.path.join(root, cfg['reviews'])))}")
    reviewer_probe = _reviewer_probe(cfg)
    probe_tone = C["green"] if reviewer_probe["ok"] else C["red"]
    print(
        f"reviewer probe  {probe_tone}{_reviewer_probe_text(reviewer_probe)}"
        f"{C['reset']}"
    )
    print(f"review budget   {C['dim']}{_review_budget_text(_review_timeout(cfg))}{C['reset']}")
    guard = _implementer_commit_guard(cfg)
    if guard:
        print(f"commit guard    {C['dim']}{guard}{C['reset']}")
    try:
        waiver_lines = A.open_waiver_report(root)
    except Exception as exc:
        waiver_lines = [f"{C['red']}waiver ledger is unreadable: {exc}{C['reset']}"]
    print(f"waivers         {len(waiver_lines) if waiver_lines else 'none open'}"
          + (" open" if waiver_lines else ""))
    for line in waiver_lines:
        print(f"                {C['dim']}{line}{C['reset']}")
    for line in _review_evidence_lines(cfg):
        print(line)
    for line in _architect_absence_lines(cfg):
        print(line)
    for line in _worktree_lines(cfg):
        print(line)
    for line in _store_bound_lines(cfg):
        print(line)
    for line in _backup_lines(cfg):
        print(line)
    print(f"quota source    {'keyflip' if A.sh('command -v keyflip') else '—'}")
    # Optional capabilities announce themselves; the core never needs them (#82).
    for name, state, hint in _optional_features(cfg):
        print(f"optional        {name:<9} {state}" + (f"  {C['dim']}{hint}{C['reset']}" if state == "absent" else ""))
    key = A.project_key(root).lower()
    if os.name == "nt":
        # Windows schedules the watchdog with Task Scheduler, not launchd (#71).
        wd = subprocess.run(["schtasks", "/Query", "/TN", f"ao-watchdog-{key}"], capture_output=True,
                            text=True, encoding=UTF8, errors="replace").returncode == 0
    else:
        wd = A.sh(f"launchctl list | grep com.agentorchestrator.watchdog.{key}")
    print(f"watchdog        {C['green']}running{C['reset']}" if wd else
          f"watchdog        {C['dim']}not installed — ao watchdog install{C['reset']}")
    err = A.last_nudge_error(root)
    if err:
        mins = int((time.time() - err.get("at", 0)) / 60)
        print(f"last restart    {C['red']}failed {mins}m ago (exit {err.get('code')}){C['reset']}")
        print(f"                {C['dim']}{err.get('tail','')[:110]}{C['reset']}")
    else:
        print(f"last restart    {C['dim']}no failures recorded{C['reset']}")
    hook_inventory = _ao_hook_inventory(root)
    if hook_inventory["error"]:
        detail = f"resolver failure: {hook_inventory['error']}"
        print(f"{'commit hook':<16}{C['yellow']}{detail}{C['reset']}  ao hooks status")
        print(f"{'push hook':<16}{C['yellow']}{detail}{C['reset']}  AO push-window hook unavailable")
    else:
        active = _active_hook_targets(hook_inventory)
        legacy_project = _project_enrollment(root)["state"] == "legacy"
        for role, label in (("pre-commit", "commit hook"), ("pre-push", "push hook")):
            target = active[role]
            base = _state_base(target["static_state"])
            current = base in ("current-local", "current-scoped")
            tone = C["green"] if current else C["yellow"]
            notes = []
            if role == "pre-commit" and not current:
                notes.append(
                    "adopt .ao-project first, then ao hooks install"
                    if legacy_project else "ao hooks install"
                )
            if role == "pre-push" and not current:
                notes.append("AO push-window hook unavailable")
            suffix = "  " + "; ".join(notes) if notes else ""
            print(
                f"{label:<16}{tone}{target['static_state']} / "
                f"{target['track_state']}{C['reset']}{suffix}"
            )
        for target in hook_inventory["targets"]:
            if target["active"] or target["role"] != "pre-commit" \
                    or target["reachability"] != "potentially-effective" \
                    or _state_base(target["static_state"]) not in (
                        "current-local", "current-scoped", "legacy", "ambiguous-ao"
                    ):
                continue
            print(
                f"{'commit misplaced':<16}{C['yellow']}{target['static_state']} / "
                f"{target['track_state']} / {target['directory_class']} — "
                f"{target['path']}{C['reset']}  ao hooks uninstall, then ao hooks install"
            )
    hook_proof = _hook_execution_probe(hook_inventory)
    proof_tone = C["green"] if hook_proof["installed"] else C["yellow"]
    print(f"{'commit proof':<16}{proof_tone}{_hook_probe_text(hook_proof)}{C['reset']}")
    print(f"{'checkout':<16}{_checkout_position(A.git_state(root))}")
    for line in _measurement_lines(cfg):
        print(line)
    # Can the *agent* run `ao`? A shell alias is invisible to a non-interactive
    # process, so steering that says "run your gates through ao lock" is an
    # instruction the agent cannot follow — and a disciplined agent then parks the
    # slice rather than working around it. That cost this project three parked
    # items and half a day. Check the child's PATH, not this shell's.
    from .watchdog import child_path
    reachable = shutil.which("ao", path=child_path())
    if reachable:
        # The architect is woken through a binary the watchdog resolves by version,
        # not by PATH order; say which one, and whether the last wake died on it.
        arch = cfg.get("architect") or {}
        if arch.get("argv"):
            from .watchdog import child_path, wake_error, STATE_DIR
            rb, rv = A.resolve_binary(arch["argv"][0], path=child_path())
            others = [c for c in A.binary_candidates(arch["argv"][0], child_path()) if c != rb]
            print(f"architect bin   {C['green'] if rb else C['red']}{rb or 'not found'}{C['reset']} {C['dim']}{rv}{C['reset']}"
                  + (f"  {C['dim']}({len(others)} older copy: {', '.join(others)}){C['reset']}" if others else ""))
            key = A.project_key(root)
            we = wake_error(os.path.join(STATE_DIR, f"escalate-{key}.log"))
            if we:
                text, used, when = we["text"], we["binary"], we["when"]
                print(f"last wake       {C['red']}failed{C['reset']} {when} [{used or '?'}]: {text[:90]}")
                if not used:
                    print(f"                {C['dim']}binary not recorded (older log); the next wake uses the one above{C['reset']}")
                elif used != f"{rb} {rv}":
                    print(f"                {C['dim']}a different binary resolves now; the next wake will use it{C['reset']}")
                else:
                    update = next((adapter["detect"]["update"] for adapter in A.package_adapters().values()
                                   if (adapter.get("detect") or {}).get("update")
                                   and os.path.basename(arch["argv"][0]) in A.adapter_binaries(adapter)), None)
                    how = f" ({' '.join(update)})" if update else ""
                    print(f"                {C['yellow']}same binary — update it{how} or remove the stale copy{C['reset']}")
        # Liveness and channels. A watchdog nobody can prove is alive, and an orange
        # alarm with no channel beyond the desktop, are both silent failures.
        hb = A.heartbeat_age(root)
        if hb is None:
            print(f"last tick       {C['yellow']}never{C['reset']} {C['dim']}(no heartbeat file yet){C['reset']}")
        else:
            tone = C['green'] if hb < 360 else C['red']
            print(f"last tick       {tone}{hb // 60}m {hb % 60}s ago{C['reset']}"
                  + (f"  {C['red']}watchdog is not running its cycles{C['reset']}" if hb >= 360 else ""))
        from . import telegram as _tg, email as _em
        tg_ok, em_ok = bool(_tg.config()), bool(_em.config())
        print(f"channels        desktop {C['green']}on{C['reset']} · telegram "
              f"{C['green'] if tg_ok else C['yellow']}{'on' if tg_ok else 'off'}{C['reset']} · e-mail "
              f"{C['green'] if em_ok else C['yellow']}{'on' if em_ok else 'off'}{C['reset']}"
              + ("" if (tg_ok or em_ok) else f"  {C['yellow']}orange alarms reach nothing but the desktop — ao email setup{C['reset']}"))
        try:
            from .watchdog import load_state as _ls
            _st = _ls(root)
            if _st.get("arch_quota_until", 0) > time.time():
                print(f"architect       {C['yellow']}at quota{C['reset']} until "
                      f"{time.strftime('%H:%M', time.localtime(_st['arch_quota_until']))}")
        except Exception:
            pass
        alive = A.active_alarms(A.project_key(root))
        if alive:
            worst = max(alive, key=lambda e: {"yellow": 0, "orange": 1, "red": 2}.get(e.get("ring"), 0))
            print(f"alarms          {C['red'] if worst['ring'] == 'red' else C['yellow']}{len(alive)} live, worst {worst['ring']}{C['reset']}  {C['dim']}ao alarms{C['reset']}")
        # Things that fail silently unless someone asks: the transcript format (a CLI
        # update changes it and every status goes blind), the credits running out
        # before the reset, the external ping, the push hook, the switches.
        try:
            msgs_p, _ = A.session_paths(cfg)
            if msgs_p and os.path.exists(msgs_p):
                n_recs = len(A.read_tail(msgs_p, 2_000_000))
                age_m = int((time.time() - os.path.getmtime(msgs_p)) / 60)
                bad = n_recs == 0 and age_m < 60
                print(f"transcript      {C['red'] if bad else C['green']}{n_recs} records parsed{C['reset']}, last write {age_m}m ago"
                      + (f"  {C['red']}fresh file, nothing parsed — the CLI's format changed; update the adapter{C['reset']}" if bad else ""))
        except Exception:
            pass
        br = A.burn_rate(root)
        _last = (A.credit_samples(root) or [None])[-1]
        # A reading already over the limit is exhausted; a date ahead would say otherwise.
        _over = bool(_last and _last.get("limit")
                     and float(_last.get("used") or 0) >= float(_last["limit"]))
        if br and not _over:
            when = time.strftime('%d %b', time.localtime(br['exhausts_at'])) if br['exhausts_at'] else '—'
            tone = C['red'] if br['before_reset'] else C['green']
            print(f"credits         {br['used']:.0f}/{br['limit']:.0f} · {br['per_day']:.0f}/day · runs out {tone}{when}{C['reset']}"
                  + (f"  {C['red']}before the reset — new account / ao features off{C['reset']}" if br['before_reset'] else ""))
        if (_over or not br) and _last and _last.get("limit"):
            _used, _limit = float(_last.get("used") or 0), float(_last["limit"])
            print(f"credits         {C['red'] if _used >= _limit else C['green']}{_used:.0f}/{_limit:.0f}{C['reset']} at the last reading"
                  + (f"  {C['red']}exhausted{C['reset']}" if _used >= _limit else ""))
        try:
            from .watchdog import load_state as _load_state
            _blind = (_load_state(root) or {}).get("credit_check_problem")
        except Exception:
            _blind = None
        if _blind:
            print(f"credits check   {C['red']}cannot read usage{C['reset']} — {_blind.get('reason')}  "
                  f"{C['dim']}the exhaustion alarm is blind until it reads again{C['reset']}")
        print(f"ping            {C['green'] + 'configured' + C['reset'] if A.ping_url(root) else C['yellow'] + 'off' + C['reset'] + '  ao pings setup'}")
        from . import features as _F
        print(f"features        {sum(_F.switches(cfg).values())}/{len(_F.ORDER)} on  {C['dim']}ao cost --features for what "
              f"each spent{C['reset']}")
        print(f"ao for agents   {C['green']}{reachable}{C['reset']}")
    else:
        print(f"ao for agents   {C['red']}not on a spawned agent's PATH{C['reset']}")
        print(f"                {C['dim']}a shell alias does not count — "
              f"uv tool install ao-orchestrator{C['reset']}")

    from . import skillkit as _skillkit
    for steering in _skillkit.steering_dirs(root):
        steer = os.path.join(root, *steering.split("/"))
        if not os.path.isdir(steer) or reachable:
            continue
        refs = [f for f in os.listdir(steer)
                if f.endswith(".md") and "ao " in open(os.path.join(steer, f),
                                                       errors="replace", encoding=UTF8).read()]
        if refs:
            print(f"                {C['yellow']}steering references it: "
                  f"{', '.join(refs)}{C['reset']}")

    # Two copies of `ao` on one machine is an ambiguity that bites silently: a
    # shell alias to a git checkout and a package install answer to the same
    # name, drift apart after the next commit, and which one runs depends on
    # how it was invoked. The second pilot's agent caught it on its first day.
    cands = []
    for cand in (shutil.which("ao"), os.path.join(A.HOME, ".local", "bin", "ao"),
                 os.path.join(A.REPO, "bin", "ao")):
        if cand and os.path.exists(cand):
            real = os.path.realpath(cand)
            if real not in [os.path.realpath(c) for c in cands]:
                cands.append(cand)
    if len(cands) > 1:
        print(f"ao binaries     {C['yellow']}{len(cands)} distinct{C['reset']} — "
              f"{C['dim']}which one runs depends on how it is invoked{C['reset']}")
        for c in cands:
            print(f"                {C['dim']}{c} → {os.path.realpath(c)}{C['reset']}")
        print(f"                {C['dim']}keep one: drop the shell alias, or "
              f"uv tool uninstall ao-orchestrator{C['reset']}")
    return 0 if reviewer_probe["ok"] else 1


def main():
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
    ini.add_argument("--agent", choices=_skillkit.agent_choices(), default="auto")
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
    si.add_argument("ref", nargs="?", help="last | 2h | 1d | <git ref>")
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
    cu = sub.add_parser("catchup", help="replay what could not run: waived reviews, deferred wakes and nudges")
    cu.add_argument("--boundary")
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
    sk.add_argument("--agent", choices=_skillkit.agent_choices(), default="auto")
    sk.set_defaults(fn=cmd_skill)

    args = p.parse_args()
    if not getattr(args, "fn", None):
        p.print_help()
        return 0
    cfg = A.load_config(A.find_root(args.root))
    return args.fn(cfg, args) or 0


if __name__ == "__main__":
    sys.exit(main())
