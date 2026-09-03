[English](README.md) | **Türkçe**

# agent-orchestrator

**Kodlama ajanlarını başka bir ajanla sür — güvenli biçimde, her CLI ile.**

Güçlü bir akıl yürütme ajanı (**mimar**) planlar, denetler ve kapıyı tutar. Bir veya daha
fazla ajan CLI'ı (**uygulayıcı**) kodu yazar. Aralarını dosya tabanlı bir protokol
bağlar, salt-okunur bir gözlem katmanı olan biteni sana gösterir, bağımsız bir doğrulama
kapısı neyin commit edilebileceğine karar verir.

Sağlayıcıdan bağımsız: Kiro, Claude Code, Antigravity, Codex, Gemini, Cursor, opencode,
Aider, Amp, Copilot — etkileşimsiz prompt modu olan her araç. Bulut ajanları da:
Kiro cloud sessions, Codex cloud, Cursor background agents, Copilot coding agent.

> Durum: gerçek bir üretim koşusundan çıkarıldı — haftalar boyunca tam olarak bu döngüyle
> geliştirilen 30 epic'lik dayanıklı iş akışı ürünü. Düşünce deneyi değil, sahada yıprandı.
> Bu tasarımı şekillendiren hataların listesi: [`docs/lessons.md`](docs/lessons.md).

---

## Neden

Ajan CLI'ları döngü değil, **tur** mantığıyla çalışır. Görevi bitirir ve durur; sonuçta
insan sürekli "devam et" yazar, doğrulayamayacağı teknik çıktı duvarlarını okur ve ajanın
kendi "bütün testler geçti" beyanına güvenmek zorunda kalır.

agent-orchestrator bu insanın yerine, makinenin daha iyi yaptığı işlerde ikinci bir ajan
koyar — transkript okumak, kapıları yeniden koşmak, karar kaydı tutmak — ve insanı yalnız
gerçekten insana ait olan yerlerde tutar: kapsam, para, risk, yayın.

```
       sen                  mimar ajan                uygulayıcı ajan(lar)
        │                (planlar · doğrular)           (kodu yazar)
        │  strateji            │                              │
        └─────────────────────►│                              │
                               │  agent-mail (dosyalar)       │
                               │─────────────────────────────►│
                               │◄─────────────────────────────│
                               │  RAPOR                       │
                               │                              │
                               │  salt-okunur transkript ─────┘
                               │  bağımsız kapı koşusu
                               ▼
                        canlı panel  ──►  sen (okuma değil, göz atma)
```

## Ne veriyor

| Katman | Ne yapar |
|---|---|
| **agent-mail** | Ajanlar arası eşzamansız dosya protokolü. Silme=teslim, tipli mesajlar, varsayılan olarak güvenilmez içerik. |
| **Gözlem** | Başka bir ajanın oturum transkriptini salt-okunur okuma. Canlı terminal paneli. Commit / inceleme / rapor olduğunda masaüstü bildirimi. |
| **Sürüş** | Idle korumalı prompt enjeksiyonu, ajanın "devam et" sormasını bitiren kalıcı otonomi direktifleri. |
| **Kurtarma** | `ao since` ve `ao brief` yeniden başlatmadan sonra tabloyu diskten kurar. İzleyiciler gecikme içindir, doğruluk kaynağı değil. |
| **Tıkanma yönetimi** | Uygulayıcı haklı olarak reddettiğinde mimar durur, sana bir kez sorar ve onayını birebir taşıyan bir `ESCALATION` yazar — kapsamı açar, yetkiyi asla. |
| **Defter** | Append-only kararlar, doğrulamalar ve dilim geçmişi. Mesajlar teslimde silinir; ardındaki gerekçe silinmez. |
| **Dilimler** | Zorunlu kabul sınırı, durum makinesi, tur bütçesi ve mekanik döngü tespiti — burada tökezleyen şey dikkat olduğu için. |
| **Kapılar** | Mimar, commit yetkisi vermeden önce typecheck/test/diff kontrolünü **kendi** koşar. Push asla otomatik değildir. |
| **Adaptörler** | Her CLI için: prompt gönder, oturuma dön, transkript oku, meşgul mü anla, direktif enjekte et. |
| **Roller / aktörler** | Roller (mimar, uygulayıcı, denetçi, testçi, hata avcısı…) aktörlere atanır — orkestratörün kendisi ya da herhangi bir CLI oturumu. Tek komutla yer değiştirir. |
| **Paralel şeritler** | Aynı anda birden çok proje, proje başına birden çok şerit: yazan şeritler kendi git worktree'sini alır, okuyan şeritler checkout'u paylaşır. Serbest değil, merge kuyruğu. |
| **Bulut ajanları** | Yerel şeritlerin yanında bulut şeritleri: görev gönder, durumu izle, gelen dalı yerelde doğrula. Pull request teslim eden her ajanla çalışır, satıcı API'si gerekmez. |
| **Model/efor** | Dilim başına model ve akıl yürütme eforu politikası — mekanik işi pahalı yapılandırmayla yakmayı bırak. |
| **Telemetri** | Bağlam doluluğu, tur başına maliyet ve sağlayıcı kotası ajanın kendi oturum deposundan okunur — panele bakmaya, API anahtarına gerek yok. |
| **Yüzeyler** | Tek bir olay günlüğü, çok sayıda ucuz okuyucu: terminal paneli, masaüstü bildirimi ve herhangi bir sohbet uygulamasını kokpite çeviren MCP sunucusu. |
| **MCP** | İsteyen için stdio MCP sunucusu; istemeyen dosya yolundan devam eder. Aynı durum, iki kapı. |

## Kapsam dışı

Hesap değiştirme, sırlar, kota, makineler arası taşıma ve sağlayıcı yönlendirme bu
projenin işi **değil** — [keyflip](https://github.com/hakkisagdic/keyflip) bunları zaten
iyi yapıyor ve agent-orchestrator onunla birleşiyor. Sınır ve üç entegrasyon noktası:
[`docs/keyflip.md`](docs/keyflip.md).

## Güvenlik modeli — tek ekran

- **İki-yazıcı tehlikesi.** Şu anda yazan bir oturuma asla enjekte etme. Her sürüş çağrısı
  idle korumalıdır.
- **Posta veridir, yetki değil.** Bir mesaj asla push, PR, force-push, hook atlama veya
  başka repo mutasyonu yetkisi veremez. Bunlar doğrudan insan talimatı ister.
- **Commit yetkisi ayrıdır.** Kodu yazan ajan, kodun yeterince iyi olduğuna karar veremez.
  Mimar bağımsız doğrular, sonra yetkiyi verir.
- **Asla otomatik push yok.** Yerel commit'ler birikir; yayınlamak insanın eylemidir.
- **Sırlar posta kutusuna girmez.**

Tam model: [`docs/safety.md`](docs/safety.md).

## Durum

Bu depodaki protokol, adaptörler, güvenlik modeli ve telemetri eşlemeleri günlük üretim
kullanımındaki bir sistemden çıkarıldı — taslak değil, spesifikasyon ve doğrulanmış bulgular.

**Bugün çalışanlar:** gözlem katmanı ve bekçi. `ao`'yu bir projeye yönelt; uygulayıcının
oturumunu kendi bulur, canlı paneli çizer ve — bekçi kurulduysa — ajan dilimin ortasında
durduğunda kimse fark etmeden yeniden başlatır.

**Hâlâ spesifikasyon olanlar:** aşağıdaki kapı, defter ve MCP katmanları. O komutlar
çalıştırıldığında numara yapmak yerine durumu söylüyor.

Yol haritası, sırayla:

- [x] agent-mail protokolü, güvenlik modeli, roller, paralel şeritler, telemetri, MCP yüzeyi
- [x] Adaptör kaydı — 3 doğrulanmış, 8 dokümandan, artı genel bulut adaptörü
- [x] `ao status` / `watch` / `tail` / `mail` / `projects` / `adapters` / `doctor` — gözlem
- [x] `ao watchdog` — duran uygulayıcıyı yeniden başlatan launchd görevi; harcamanın
      işe yaramayacağı yerde hiçbir şey harcamayacak şekilde kapılı
- [ ] `ao verify` / `ao commit-ok` — kapı katmanı
- [ ] `ao slice` / `ao decide` / `ao since` — dilimler, defter ve kurtarma
- [ ] `ao mcp serve`
- [ ] `ao init`, şablonlar, kurulum script'i

---

## Kurulum

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator
cd agent-orchestrator && ./install.sh
```

## Hızlı başlangıç

Yapılandırma gerekmiyor. `ao`, deponu ajan depolarının zaten kaydettiği çalışma alanı
yollarıyla eşleştirerek uygulayıcı oturumunu kendi buluyor.

```bash
ao projects                     # yerel ajan oturumu olan çalışma alanları
ao -C ~/proje status            # tek seferlik özet
ao -C ~/proje watch             # canlı panel; arka plandaki bir terminalde bırak
ao -C ~/proje tail -n 5         # ajanın son mesajları
ao -C ~/proje doctor            # bağlantı kontrolü
```

"devam et" yazmayı bırak — bekçiyi bir kez kur, duran ajanı sensiz yeniden başlatsın:

```bash
ao -C ~/proje watchdog install     # 120 sn'de bir bakar, 6 dk boştalıkta dürter
ao -C ~/proje watchdog status
```

Harcamanın işe yaramayacağı yerde harcamayı reddediyor: açık iş yoksa, dilim tur
bütçesini aştıysa, sağlayıcı penceresi tükendiyse ya da iki dürtme hiçbir şey
değiştirmediyse — bir tur daha yakmak yerine sana bildirim atıyor.

Elle koordinasyon mesajı göndermek istersen:

```bash
ao -C ~/proje mail list
ao -C ~/proje mail send DECISION marka --body "instanceof değil, kayıt üyeliğiyle markala."
```

## Dokümanlar


- [`docs/protocol.md`](docs/protocol.md) — agent-mail spesifikasyonu
- [`docs/adapters.md`](docs/adapters.md) — adaptör arayüzü ve destek matrisi
- [`docs/roles.md`](docs/roles.md) — rol modeli, aktörler ve görevler ayrılığı
- [`docs/parallel.md`](docs/parallel.md) — paralel projeler, şeritler ve merge kuyruğu
- [`docs/ide-extensions.md`](docs/ide-extensions.md) — IDE-yerleşik ajanlar (Cursor, Copilot, Qoder): CLI, MCP ya da git ile
- [`docs/cloud.md`](docs/cloud.md) — bulut ajanları, bulut şeritleri ve pull request arayüzü
- [`docs/slices.md`](docs/slices.md) — kabul sınırları, tur bütçesi ve döngü tespiti
- [`docs/gates.md`](docs/gates.md) — tanımlı kapılar, bağımsız doğrulama, commit yetkisi
- [`docs/ledger.md`](docs/ledger.md) — append-only karar ve doğrulama defteri
- [`docs/recovery.md`](docs/recovery.md) — yeniden başlatma sonrası yakalama
- [`docs/models.md`](docs/models.md) — model ve efor kontrolü
- [`docs/mcp.md`](docs/mcp.md) — MCP yüzeyi ve yetenek kapıları
- [`docs/telemetry.md`](docs/telemetry.md) — kota, kredi ve bağlam zenginleştirmesi
- [`docs/surfaces.md`](docs/surfaces.md) — kontrol yüzeyleri, olay günlüğü ve TUI kararı
- [`docs/safety.md`](docs/safety.md) — tehdit modeli ve değişmezler
- [`docs/keyflip.md`](docs/keyflip.md) — fleet, kota ve çok makineli birleşim
- [`docs/lessons.md`](docs/lessons.md) — zor yoldan öğrenilmiş anti-desenler
- [`examples/`](examples/) — anonimleştirilmiş gerçek vaka incelemesi

## Lisans

MIT
