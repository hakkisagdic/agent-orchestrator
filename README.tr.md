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
mkdir -p ~/.local/bin && ln -s ~/ao/bin/ao ~/.local/bin/ao    # ~/.local/bin PATH'te olmalı
```

Shell alias değil, symlink: Git ao'nun commit hook'unu `/bin/sh` altında çalıştırır,
watchdog da ajanları senin shell'in olmadan başlatır; ikisi de alias'ı göremez.

Bağımlılık yok, bilerek — bu araç kontrol etmediğin makinelerdeki ajanları izler ve
bağımlılık, tam da orada eksik olabilecek şeydir. İlk koşudan önce yapılandırma
gerekmez: `ao` oturumu ajanın kendi deposundan keşfeder — bugün Kiro'nun ve Claude
Code'un deposundan — ve `ao init --profile`'ın yazdığı `session: auto` da aynı yolla
çözülür. Uygulayıcı ve mimar aynı dizinde aynı harness'i çalıştırıyorsa `ao` hangi
oturumun kimin olduğunu tahmin etmez: `ao doctor` bunu söyler, sabitlenen bir oturum
kimliği belirsizliği giderir ([docs/profiles.md](docs/profiles.md#how-auto-finds-a-session)).

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


## Kurulum, adım adım

1. `pip install ao-orchestrator` (ya da `uv tool install ao-orchestrator`, ya da Homebrew tap).
2. Depoda: `ao init --profile claude-kiro`. `.ao/`, posta kutusu, playbook yazılır,
   tespit edilen ajanlar için MCP sunucusu kaydedilir. Her rol için tek bir harness varsa `claude-claude`
   bir review katmanı ister, çünkü reviewer'ı uygulayıcıyla aynı model ailesindendir:
   `ao init --profile claude-claude --review-tier same-family --by <ad>` aynı aileden başka bir modelin
   review etmesine izin verir ve bu review'ların her biri `same family: weaker independence` (aynı aile: daha
   zayıf bağımsızlık) diye etiketlenir; `ao init --profile claude-claude --review-tier person` hiçbir model
   reviewer kurmaz ve her adayı bir insan `ao person-review --by <ad>` ile review eder. Başka aileden bir
   reviewer varsayılan ve en güçlü katman olarak kalır ([review katmanları](docs/roles.md#review-tiers)).
3. **Kuralları bağla.** ao `CLAUDE.md` / `AGENTS.md` dosyana yazmaz: basılan işaretçiyi oraya yapıştır ya da
   `ao init --rules` çalıştır. O zamana kadar `ao doctor` `rules-not-wired` der: playbook yazılmış ama hiçbir
   ajan okumuyor. (Claude Code skill dosyasını ajan kendi bulur.)
4. Ajanın uygulamasını bu dizinde başlat/yeniden başlat ki `ao` MCP araçları yüklensin.
5. `ao doctor`. Sonra `ao email setup`, istersen `ao telegram setup`; dead man's switch için `ao pings setup --url`.
6. Gözetimsiz koşu için `ao watchdog install` (doctor işi birlikte gelir). Neye para ödediğini `ao features` ile
   seç; `ao remove --yes` her şeyi geri alır.

## Komutlar

| | |
|---|---|
| `ao status` · `ao watch` | tek proje: durum, telemetri, sorunlar, pano |
| `ao watch --all` · `ao fleet` | tüm projeler, insana en çok ihtiyaç duyan üstte |
| `ao board` | her iş nerede; READY = `needs:` bağımlılıkları tamamlanmış kuyruk maddeleri |
| `ao verify [-p full]` | tanımlı gate'leri koştur, sonucu kaydet |
| `ao commit-ok [--verify]` | bu ağaç commit edilebilir mi? kanıttan karar |
| `ao hold` / `ao hold release --note …` | ağaçtaki tüm ajanları durdur ve durdurulmuş tut |
| `ao writers` / `ao writers --clean` | ağaçtaki canlı turlar (süreç değil tur başına bir), öksüzler ayrı; `--clean` yalnız öksüzleri durdurur |
| `ao fanout ok --agents N` / `ao fanout record …` / `ao fanout history` | N alt-ajanlık fan-out şimdi başlayabilir mi (üst sınır, yakın limit vuruşu, sağlayıcı penceresi); bir koşunun maliyetini kaydet |
| `ao cost --since 24h` | koordinasyonun kendisi ne harcıyor: uygulayıcı turları sınıfa göre (ürün / analiz / tören / koordinasyon), boşa giden turlar, review sayısı; `--since`, her zaman argümanı gibi, `30m`, `2h`, `1d`, `today`, `yesterday` ya da bir tarih alır |
| `ao features [on|off <anahtar>]` | anahtarlar ve her birinin maliyeti; hepsi kapalı = deterministik ao, sıfır model harcaması ([features.md](docs/features.md)) |
| platformlar | macOS ve Linux yerel; Windows ilk sürüm ([windows.md](docs/windows.md)); testler her push ve pull request'te Ubuntu'da, macOS ve Windows'ta haftalık koşar |
| `ao waive review --slice B7 --by <ad> --why …` / `ao catchup` | insan bir kapıyı kayıtlı biçimde atlar; catchup inen her aralığı onu yazan model ailesinden başka bir aileyle, commit mesajlarının iddia ettiklerine karşı review eder, `move-only` bir bölmeyi grant'in kaydettiği kanıtı yeniden çalıştırarak kapatır, ertelenen uyandırma/dürtmeleri yeniden oynatır. `ao catchup --plan` hiçbir şey yazmadan önizler ve hangilerinin kanıtla kapanacağını söyler, `ao catchup --limit 10` ve `ao catchup --slice B7` bir koşuyu sınırlar, `ao catchup --author-family <aile> --by <ad>` ao'nun kaydedemediği aileyi bir insanın adlandırmasıdır, `ao catchup --move-only <dilimler> --by <ad>` ise waiver'lı hangi dilimlerin yalnızca kod taşıdığını bir insanın beyan etmesidir; her biri yalnızca kanıt tutarsa kapanır; bir koşu başlattığı review'lar hiçbir karar vermediğinde 3, ilerlediğinde ya da yapacak bir şeyi olmadığında 0 ile çıkar |
| `ao pings setup --url …` | dead man's switch: watchdog ve doctor işi birlikte ölünce alarm veren dış ping |
| `ao hooks [status|install|uninstall] [--allow-shared-hooks]` / `ao push allow` | Git'in etkin hook yolunu çöz; roller bağımsızdır, paylaşılan/harici/global mutasyonlar komutun tamamı için açık yetki ister |
| `ao skill install` / `ao skill show` | playbook (roller, döngü, yetki, protokol, alarmlar, tüm komutlar) deponun ajanları için: Claude skill, Kiro steering, AGENTS.md |
| `ao remove --yes [--allow-shared-hooks]` | iki aşamalı kaldırma: dayatma etkinken `.ao-project` dosyasını silip commit et, HEAD ve index artık taşımayınca AO durumunu kaldır; yabancı/korunan hook'lara dokunma. İkinci aşama projenin zamanlanmış işlerini ve `~/.ao` içindeki yalnız kendi dosyalarını kaldırır, kuru koşuda her birini adıyla listeler ve kaldıramadığını adıyla söyleyip 1 ile çıkar ([watchdog.md](docs/watchdog.md)) |
| `ao init --profile claude-kiro|claude-claude [--review-tier same-family|person] [--language en|tr]` | rol bloklarını ve exact `.ao-project` kayıt işaretini yaz, ama stage etme; tek harness'lı profil bir review katmanı seçer ([profiles.md](docs/profiles.md)); `--language tr` ile ao'nun projeye yazdıkları ve insanlara söyledikleri Türkçe olur ([configuration.md](docs/configuration.md)) |
| `ao doctor --check` | zamanlayıcı için sessiz doctor: problem başına bir satır, exit 1, alarm — `ao watchdog install` 15 dakikalık launchd işi olarak kurar |
| `ao email setup` / `ao email test` | kırmızı alarm kanalı: formsubmit.co ile e-posta, sunucu yok ([alarms.md](docs/alarms.md)) |
| `ao alarms` / `ao alarms test --level red` | canlı alarm bölümleri ve seviyeleri; test tüm kanalları çaldırır |
| `ao mail log` / `ao mail search <metin>` / `ao mail ack <glob>` | posta defteri: yazılan her mesaj ve ne zaman okunduğu, silindikten sonra da aranabilir |
| `ao watchdog explain` / `ao watchdog trace` | watchdog neden dürttü ya da dürtmedi: bu döngünün ve kayıtlı döngülerin ölçüm ve kararları ([watchdog.md](docs/watchdog.md)) |
| `ao source import` | takip sistemindeki işleri panoya kabul et |
| `ao mail` · `ao notices` | koordinasyon mesajları; projenin ürettiği uyarılar |
| `ao watchdog install` | takılan ajanı yeniden başlatan launchd işi |
| `ao mcp serve` · `ao a2a serve` | durumu MCP istemcilerine / A2A görevi olarak sun |
| `ao telegram setup` | telefona uyarı, telefondan karar |
| `ao digest [--days N]` | ne oldu, defterlerden okunur — "neden ilerlemiyor"un da cevabı |
| `ao ask` · `ao answer` · `ao decisions` | tek dokunuşla cevaplanan sorular; serbest metin hep sonda; cevap sorunun sunduğu bir harf olmalı, `ao answer <D-id> <harf> --change` bir cevabı değiştirir ve ilki kayıtta kalır |
| `ao note` | kutuya mimar mesajı, araç üzerinden |
| `ao review` | ağacı, onu yazmayan bir aktörle gözden geçir |
| `ao person-review --by <ad>` | bir insan stage edilmiş diff'i okur, sonra gösterilen `--digest` ile `--verdict APPROVED` ya da `NEEDS_CHANGES` kaydeder; her review gibi adaya bağlanır, `person review` diye etiketlenir, asla bir ajanın komutu değildir |
| `ao review --commits <aralık>` | inmiş işi sonradan review et; çıkış 3 = reviewer erişilemedi (asla verdict değil), yedekler `reviewer.fallbacks` ([roles.md](docs/roles.md)) |
| `ao handoff` | devralanın ihtiyacı olan her şeyi yaz ve gönder |
| `ao a2a-mcp serve` | MCP-only istemciden A2A ajanlarına ulaş |
| `ao prune` | biriken kayıt ve logları buda |
| `ao doctor` · `ao adapters` | bağlantıları denetle; ne destekleniyor ve ne kadar |
| `ao --version` | kurulu sürüm |

Hook durumu statik niyeti çalıştırılabilir dayatmadan ayırır. AO dayatması yalnız
HEAD'de veya etkin index'te exact `ao-project-v1\n` baytları bulunan kök
`.ao-project` ile açılır; tesadüfi `.ao/` dizini etkisizdir. Stage edilmiş işaret
ilk kaydı açar, stage edilmiş silme ise silme commit edilene kadar HEAD üzerinden
dayatmayı korur. Kayıtlı projede `.ao/config.json` geçerli, boş olmayan üst seviye
JSON nesnesi olmalıdır. AO en fazla 1.048.576 bayt okur ve 64'ten derin container
yapısını recursive JSON ayrıştırmasından önce reddeder; komut yönlendirmesi
öncesindeki yükleme ile commit dayatması aynı bounded sonucu kullanır. Eksik ya da
okunamayan durum tek satırlık `ao init --profile claude-kiro` düzeltmesiyle
birlikte reddedilir.

`current-local (behavior unverified)` ve `current-scoped (behavior unverified)`
yalnız baytların AO'nun amaçlanan rolünü ifade ettiğini söyler. `pre-commit
execution: installed (execution proved)` ancak Git etkin hook'u yalıtılmış geçici
index ile çözüp çalıştırdığında ve AO aynı nonce'a bağlı reddi döndürdüğünde
yazılır. Prob gerçek index'i, worktree'yi, ref'leri veya Git object store'u
değiştirmez. Eksik, yanlış yerde, çalıştırılamayan, bayat, yabancı ya da fail-open
hook `not installed` olur; status, doctor, `doctor --check` ve init aynı sonucu
kullanır. `status`; etkin yolu, yol sınıfını, kazanan `core.hooksPath`
scope/origin/value bilgisini, track durumunu ve yanlış yerdeki AO biçimlerini
gösterir. Deponun git dizinine kurulan hook, onu kuran ao'nun yolunu da taşır ve o
dosyaya yalnız `/bin/sh` PATH'te ao bulamadığında başvurur; bulamadığında `hooks status`
ve `doctor` bunu düzelten symlink'le birlikte söyler. Install, pre-commit ile pre-push
rollerini bağımsız ele alır; uygun pre-commit'i kurup özel pre-push'ı bayt düzeyinde
koruyabilir, push-window hook'unun kullanılamadığını söyleyebilir ve 1 dönebilir. Uygun
hedeflerden biri paylaşılan, harici ya da global/system config ile seçilmişse
install, uninstall ve remove işlemlerinin tamamı açık `--allow-shared-hooks`
olmadan reddedilir.

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
kullanımda. Hâlâ şartname: belgelerin "Not built yet" diye işaretlediği her bölüm (lane'ler
ve birleştirme kuyruğu bunlardan) ve projeler arası paralel **koşum** (görünüm var; birkaç
uygulayıcıyı aynı anda çalıştırmak makine gate kilidine bağlandı ama gerçek yükte denenmedi).

MIT.
