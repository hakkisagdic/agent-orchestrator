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

Bunları birbirine bağlayan `ao` komutu hâlâ o özel uygulamadan ayrıştırılıyor. **Aşağıdaki
komutlar hedeflenen yüzeyi tarif eder; henüz çalışmazlar.** O gelene kadar bu depo şunlar
için değerli: bir öğleden sonrada uygulayabileceğin bir protokol, doğrulanmış bir adaptör
kaydı ve çalmaya değer bir güvenlik modeli.

Yol haritası, sırayla:

- [x] agent-mail protokolü, güvenlik modeli, roller, paralel şeritler, telemetri, MCP yüzeyi
- [x] Adaptör kaydı — 3 doğrulanmış, 8 dokümandan yazılmış
- [ ] `ao status` / `ao watch` — gözlem katmanı (referans script'ler özelde mevcut)
- [ ] `ao mail` / `ao resume` — idle korumalı sürüş katmanı
- [ ] `ao verify` / `ao commit-ok` — kapı katmanı
- [ ] `ao mcp serve`
- [ ] `ao init`, şablonlar, kurulum script'i

---

## Kurulum

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator
cd agent-orchestrator && ./install.sh
```

## Hızlı başlangıç

```bash
ao init --implementer kiro            # steering + hook + posta kutusunu bu repoya yaz
ao mail send DECISION "instanceof değil WeakSet markası kullan"
ao watch                              # canlı panel; arka plandaki bir terminalde bırak
ao status                             # tek seferlik özet
ao verify                             # kapıları kendin koş
ao commit-ok "feat: ..."              # kapılar geçtikten sonra commit yetkisi ver
```

## Dokümanlar

- [`docs/protocol.md`](docs/protocol.md) — agent-mail spesifikasyonu
- [`docs/adapters.md`](docs/adapters.md) — adaptör arayüzü ve destek matrisi
- [`docs/roles.md`](docs/roles.md) — rol modeli, aktörler ve görevler ayrılığı
- [`docs/parallel.md`](docs/parallel.md) — paralel projeler, şeritler ve merge kuyruğu
- [`docs/cloud.md`](docs/cloud.md) — bulut ajanları, bulut şeritleri ve pull request arayüzü
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
