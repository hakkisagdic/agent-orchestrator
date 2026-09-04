[English](README.md) | **Türkçe**

# agent-orchestrator

**Zaten çalışan ajanına doğrult. Kaldığı yerden devam eder.**

Yeni harness yok. Yeniden planlama yok. "Araç yönetebilsin diye temiz oturum aç" yok.
`ao`, hâlihazırda çalışan bir kodlama ajanına iliştirir — IDE'ndeki, terminaldeki,
başkasının başlattığı — durumunu ona dokunmadan okur ve sıkıcı yarısını devralır:
takıldığını fark etmek, gate'leri koşturmak, neyin commit edilebileceğine karar
vermek, ve sen uyurken işi yürütmek.

Fark tam olarak bu. Piyasadaki diğer orkestratörler **ajanın sahibidir**: süreci
onlar başlatır, döngüyü onlar sürer, ve benimsemek işini onların içinde yeniden
başlatmak demektir. `ao` bunun yerine **yetkinin** sahibidir — ne bitti, ne iyi, ne
inebilir — ajanı olduğu yerde bırakır.

```bash
pip install ao-orchestrator                  # ya da: uv tool install ao-orchestrator
cd ~/projen && ao status            # çalışan oturumu kendi bulur
```

Ya da **hiçbir şey kurmadan**, macOS'un ve çoğu Linux dağıtımının zaten getirdiği
Python ile:

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator ~/ao
echo 'alias ao="$HOME/ao/bin/ao"' >> ~/.zshrc && exec zsh
```

Bağımlılık yok, bilerek — bu araç kontrol etmediğin makinelerdeki ajanları izler ve
bağımlılık, tam da orada eksik olabilecek şeydir. İlk koşudan önce yapılandırma
gerekmez: `ao` oturumu ajanın kendi deposundan keşfeder.

**Windows** PowerShell ile gelir, Python ile gelmez; bu yüzden `bin/ao.ps1` hiçbir şey
kurmadan `status`, `board` ve `doctor` verir. Bilerek sınırlı bir alt küme ve öyle
kalacak — geri kalan her şey ya karar verir, ya makineyi harcar, ya süreç öldürür, ya
protokol konuşur; ve bunların herhangi birinin ikinci bir implementasyonu, yanlış
olabilecek ikinci bir şeydir. Gerisi için:
`winget install Python.Python.3.12 && pip install ao-orchestrator`.
Script yazıldı ve gözden geçirildi ama **henüz Windows'ta koşulmadı**.

> **Nereden çıktı.** Haftalarca tam olarak bu döngüyle geliştirilen 30 epic'lik
> dayanıklı-iş-akışı ürününden çıkarıldı. Buradaki her koruma önce bir şey ters
> gittiği için var: tek oturumda ikinci tur başlatıp yeniden adlandırmayı yarım
> bırakan bir watchdog; tek depoda biriken on beş ajan süreci; canlı kanıtı üç saat
> bayat gösteren bir saat hatası. [`docs/lessons.md`](docs/lessons.md) listesi.
> Hiçbiri önceden tasarlanmadı.

---

## Ne yapar

**İzler.** `ao watch` canlı bir panel: ajan ne yapıyor, bağlam ve maliyet, açık
review'lar, iş panosu — ve başka hiçbir panelde olmayan şey: **meşgul ama üretmiyor
mu?** Bekleme döngüsüne saplanmış bir ajan sapasağlam görünür: transcript büyür,
tool call'lar akar, kredi yanar. `ao` faaliyeti üretilen şeyle karşılaştırır ve söyler.

**Yeniden başlatır.** Tur-bazlı ajanlar tur bitince durur, dilim ortasında bile.
Watchdog fark edip dürter. Tespit bedava — bir dosya mtime'ı ve birkaç `git` çağrısı —
ve bir koruma zinciri, dürtmenin işe yaramayacağı yerde harcanmamasını sağlar: bir tur
koşarken ikincisi yok, sağlayıcı çökmüşken dürtme yok, tur bütçesi aşılmışken yok,
bir insan ağacı devralmışken yok.

**Doğrular.** `ao verify` **senin** gate'lerini koşturur ve sayıları deftere yazar.
Ajanın gate raporunu değil — komutları, yeniden, sonuçta çıkarı olmayan bir şey
tarafından.

**Karar verir.** `ao commit-ok` commit yetkisini o kanıttan verir: gate'ler geçti,
review onaylandı, plan değiştirilmedi, ve ölçüm hâlâ önündeki ağacı tarif ediyor. Her
ret, eksik koşulu adıyla söyler. `push`'u asla kapsamaz.

**Durmaz.** İnsan kararına takılan dilim sebebi kaydedilerek park edilir, iş bir
sonraki önceden-yetkilendirilmiş maddeye geçer. Kuyruk boşalınca **mimar** uyandırılır
— uygulayıcı değil, çünkü kendi kapsamını seçmek bir uygulayıcının sahip olmaması
gereken tek yetkidir.

## Komutlar

| | |
|---|---|
| `ao status` · `ao watch` | tek proje: durum, telemetri, sorunlar, pano |
| `ao watch --all` · `ao fleet` | tüm projeler, insana en çok ihtiyaç duyan üstte |
| `ao board` | her işin nerede olduğu, blocked olanlar başta |
| `ao verify [-p full]` | tanımlı gate'leri koştur, sonucu kaydet |
| `ao commit-ok` | bu ağaç commit edilebilir mi? kanıttan karar |
| `ao hold` / `ao hold release --note …` | ağaçtaki tüm ajanları durdur ve durdurulmuş tut |
| `ao source import` | takip sistemindeki işleri panoya kabul et |
| `ao mail` · `ao notices` | koordinasyon mesajları; projenin ürettiği uyarılar |
| `ao watchdog install` | takılan ajanı yeniden başlatan launchd işi |
| `ao mcp serve` · `ao a2a serve` | durumu MCP istemcilerine / A2A görevi olarak sun |
| `ao telegram setup` | telefona uyarı, telefondan karar |
| `ao a2a-mcp serve` | MCP-only istemciden A2A ajanlarına ulaş |
| `ao prune` | biriken kayıt ve logları buda |
| `ao doctor` · `ao adapters` | bağlantıları denetle; ne destekleniyor ve ne kadar |

## Dayattığı kurallar

Bunlar üslup tercihi değil. Her biri gerçek saatlere mal olmuş bir arıza.

- **Kodu yazan doğrulamaz, ve inebileceğine karar veremez.**
- **Plan okunur, yazılmaz** — bir dilimin karşısında ölçüldüğü belge, ölçülen şey
  tarafından değiştirilebiliyorsa sonraki her kontrol döngüseldir.
- **İş çekmek yetkilendirmek değildir.** Takip sistemindeki bir kayıt, birinin
  yazdığı şeydir; kimsenin doğruladığı bir şartname değil. Yazılı kabul sınırıyla
  kuyruğa girer, ya da girmez.
- **Ağır işler tek aktöre aittir.** Gate'ler makine genelinde serileştirilir; N proje
  N test suite koşturmak N kat verim değil, artık hiç bitmeyen tek bir suite'tir.
- **Yetki her tura enjekte edilen bağlamda yaşar, posta kutusunda değil.** Tıkanmış
  ajan, zaten mail okumayan ajandır.
- **`push` bu araç tarafından asla verilmez.** PR, force-push ve hook atlama da öyle.

## Ajan desteği

`ao` her ajanın kendi oturum deposunu okur; adaptör nerede ve hangi biçimde olduğunu
söyler.

| doğrulama | adaptörler |
|---|---|
| **full** — her yetenek gerçek koşumda sınandı | kiro, claude-code, antigravity |
| **partial** — durumu okur, bazı yetenekler sınanmadı | opencode, command-code |
| **documented** — yayımlanmış dokümandan yazıldı, henüz koşulmadı | cursor-agent |
| **untested** — şema hazır, ilk koşumu bekliyor | codex, gemini, aider, amp, copilot, amazon-q, deepseek, qoder, ollama |

`ao adapters` bu tabloyu makinende gerçekte kurulu olanla yan yana gösterir; ve
[keyflip](https://github.com/hakkisagdic/keyflip) varsa, CLI kurulu olmasa bile hesap
olup olmadığını söyler. Yeni adaptör bir JSON dosyasıdır;
[`docs/adapters.md`](docs/adapters.md).

## Dokümantasyon

[protokol](docs/protocol.md) · [güvenlik](docs/safety.md) · [roller](docs/roles.md) ·
[dilimler](docs/slices.md) · [gate'ler](docs/gates.md) · [kaynaklar](docs/sources.md) ·
[adaptörler](docs/adapters.md) · [paralellik](docs/parallel.md) · [bulut](docs/cloud.md) ·
[modeller](docs/models.md) · [telegram](docs/telegram.md) · [mcp](docs/mcp.md) · [telemetri](docs/telemetry.md) ·
[yüzeyler](docs/surfaces.md) · [defter](docs/ledger.md) · [kurtarma](docs/recovery.md) ·
[keyflip](docs/keyflip.md) · [ide-eklentileri](docs/ide-extensions.md) ·
**[dersler](docs/lessons.md)**

## Durum

Bugün çalışan: yukarıdaki komut tablosundaki her şey, gerçek bir projede günlük
kullanımda. Hâlâ şartname: `ao init`, `ao decide`, `ao since`, ve projeler arası
paralel **koşum** (görünüm var; birkaç uygulayıcıyı aynı anda çalıştırmak makine gate
kilidine bağlandı ama gerçek yükte denenmedi).

MIT.
