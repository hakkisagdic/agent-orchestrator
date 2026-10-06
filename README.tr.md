[English](README.md) | **Türkçe**

# agent-orchestrator

**Yapay zekâ ajanlarının yazdığı hiçbir şey, başka bir model ailesi gözden geçirmeden commit
edilmez - `git commit` anında dayatılır, deftere yazılır.**

Zaten kullandığın kodlama ajanlarını bırakmazsın: Claude Code, Codex, Kiro, Qoder, OpenCode ve
aşağıdaki tablodaki diğerleri. `ao` onların üstünde durur. Bir ajanın stage ettiği değişikliğin
tam kendisinde gate'lerini koşturur, o değişikliği başka aileden bir modele gözden geçirtir ve
yalnızca o değişikliğin commit edilmesine izin verir. Gerisini hook `git commit` anında reddeder;
her karar, kurcalandığını belli eden bir deftere yazılır.

## Neden

- **Ajanlar kimsenin okuyabileceğinden hızlı commit eder.** Bir ağaçta geçen test, commit edilen
  ağaç hakkında hiçbir şey söylemez. `ao` commit yetkisini tek bir stage edilmiş ağaca verir; hook
  başka her ağacı reddeder.
- **Kendi ailesinin işini gözden geçiren model, o ailenin kör noktalarını paylaşır.** Gözden
  geçiren varsayılan olarak başka bir model ailesindendir ya da adını verdiğin bir kişidir, ve
  yalnızca okuyan araçlarla çalışır.
- **Araçlarını bırakmazsın.** `ao` her ajanın kendi oturum deposunu okur ve komut satırını sürer.
  Benimsemek, işini yeni bir şeyin içinde yeniden başlatmak demek değildir.

## İlk gözden geçirilmiş commit'e beş dakika

```bash
pip install ao-orchestrator          # ya da: uv tool install ao-orchestrator
cd deponun-yolu
ao init --profile claude-kiro        # Claude Code yazar, Kiro gözden geçirir; ya da claude-claude
git add .ao-project && git commit -m "adopt ao"
ao hooks install                     # bundan sonra yalnızca yetki verilmiş aday commit edilir
```

Sonra, ajanının stage ettiği her değişiklik için:

```bash
ao status                            # ajanların ne yapıyor, ne seni bekliyor
ao verify                            # gate'lerin, stage edilmiş adayda
ao review                            # başka aileden bir model tam o adayı gözden geçirir
ao commit-ok                         # inebilir mi? kanıttan karar, reddin sebebiyle
ao commit -m "feat: ne değişti"      # commit et; trailer'larıyla o kanıta bağlı
```

[Başlarken](docs/getting-started.md) bunu adım adım anlatır; `ao prove` güvenceleri anlatmak
yerine atılacak bir adayda koşturur. Her rol için tek harness kullanıyorsan `claude-claude` bir
[gözden geçirme katmanı](docs/roles.md#review-tiers) ister: aynı aileden başka bir model ("daha
zayıf bağımsızlık" etiketiyle) ya da bir kişi.

## Bir değişiklik nasıl iner

```text
ajan düzenler ve stage eder
  -> ao verify      gate'lerin, sonuçta hiçbir çıkarı olmayan bir şey tarafından yeniden koşturulur
  -> ao review      başka bir model ailesi o adayı ve etrafındaki kodu okur
  -> ao commit-ok   tam o ağaç için commit yetkisi, kanıttan verilir
  -> ao commit      commit, onu yetkiye bağlayan trailer'ları taşır
pre-commit hook'u başka her commit'i reddeder; push asla yetkilendirilmez
```

## Başka ne yapar

- **İzler.** `ao watch` canlı bir panel: her ajan ne yapıyor, bağlam ve maliyet, açık gözden
  geçirmeler, board, ve bir ajanın *meşgul ama hiçbir şey üretmiyor* olup olmadığı.
- **Yeniden başlatır.** Bir watchdog, iş ortasında duran bir turu fark eder ve dürter - asla bir
  sağlayıcı kesintisine, tur bütçesinin ötesine ya da bir kişinin devraldığı ağaca değil.
- **Durmaz.** Önceden yetkilendirilmiş işlerden oluşan bir board: seni bekleyen dilim sebebiyle
  park edilir, kuyruk boşalınca onu doldurması için mimar uyandırılır.
- **Telafi eder.** Atladığın bir gözden geçirme kaydedilir; `ao catchup` inen aralığı sonradan
  başka aileden bir gözden geçirene inceletir.
- **Güvenle gözden geçirir.** Gözden geçiren yalnızca okuyan araçlarla çalışır ve ao bunu her
  harness için ölçer: kiro-cli, soracak kişi yokken güvenilmeyen araçları yine de çalıştırdığı
  için Kiro gözden geçireni, ao'nun onun için yazdığı salt okur bir ajan olarak koşar.

Her komut [docs/commands.tr.md](docs/commands.tr.md) içinde.

## Kurulum

`pip install ao-orchestrator`, `uv tool install ao-orchestrator` ya da
`brew install hakkisagdic/tap/agent-orchestrator`. Bilerek bağımlılık yok: ao, kontrol etmediği
makinelerde ajanları izler ve bağımlılık tam orada eksik olabilecek bir şeydir. Hiçbir şey
kurmadan, macOS'un ve çoğu Linux dağıtımının getirdiği Python ile bir klondan çalışır:

```bash
git clone https://github.com/hakkisagdic/agent-orchestrator ~/ao
mkdir -p ~/.local/bin && ln -s ~/ao/bin/ao ~/.local/bin/ao    # ~/.local/bin PATH'te olmalı
```

Kabuk alias'ı değil, symlink: ao'nun commit hook'unu çalıştıran `/bin/sh` de, watchdog'un senin
kabuğun olmadan başlattığı ajan da alias'ı göremez. **Windows**'ta `bin/ao.ps1`, hiçbir şey
kurmadan `status`, `board` ve `doctor`'ı karşılar; gerisi için
`winget install Python.Python.3.12 && pip install ao-orchestrator` ([windows](docs/windows.md)).

> **Nereden çıktı.** Haftalarca tam olarak bu döngüyle geliştirilen 30 epic'lik
> dayanıklı-iş-akışı ürününden çıkarıldı. Buradaki her koruma önce bir şey ters
> gittiği için var: tek oturumda ikinci tur başlatıp yeniden adlandırmayı yarım
> bırakan bir watchdog; tek depoda biriken on beş ajan süreci; canlı kanıtı üç saat
> bayat gösteren bir saat hatası. [`docs/lessons.md`](docs/lessons.md) listesi.
> Hiçbiri önceden tasarlanmadı.

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
| **partial** — durumu okur, bazı yetenekler sınanmadı | opencode, command-code, codex, qoder, openai-api |
| **documented** — yayımlanmış dokümandan yazıldı, henüz koşulmadı | cursor-agent |
| **untested** — şema hazır, ilk koşumu bekliyor | gemini, aider, amp, copilot, amazon-q, deepseek, ollama, droid, grok, hermes, kilocode, kimi, omp, pi, pr-agent, qwen, reasonix, trae |

`ao adapters` bu tabloyu makinende gerçekte kurulu olanla yan yana gösterir; ve
[keyflip](https://github.com/hakkisagdic/keyflip) varsa, CLI kurulu olmasa bile hesap
olup olmadığını söyler. Yeni adaptör bir JSON dosyasıdır;
[`docs/adapters.md`](docs/adapters.md).

## Dokümantasyon

**[başlarken](docs/getting-started.md)** · **[komutlar](docs/commands.tr.md)** ·
[protokol](docs/protocol.md) · [güvenlik](docs/safety.md) · [roller](docs/roles.md) ·
[gizlilik](docs/privacy.md) · [açık bildirimi](SECURITY.md) ·
[dilimler](docs/slices.md) · [gate'ler](docs/gates.md) · [kaynaklar](docs/sources.md) ·
[adaptörler](docs/adapters.md) · [paralellik](docs/parallel.md) · [bulut](docs/cloud.md) ·
[modeller](docs/models.md) · [telegram](docs/telegram.md) · [mcp](docs/mcp.md) · [telemetri](docs/telemetry.md) ·
[yüzeyler](docs/surfaces.md) · [defter](docs/ledger.md) · [kurtarma](docs/recovery.md) ·
[keyflip](docs/keyflip.md) · [ide-eklentileri](docs/ide-extensions.md) ·
**[dersler](docs/lessons.md)**

## Durum

Bugün çalışan: [komut listesindeki](docs/commands.tr.md) her şey, gerçek bir projede günlük
kullanımda. Hâlâ şartname: belgelerin "Not built yet" diye işaretlediği her bölüm (salt okunur
lane'ler, bir lane'in rolü ve birleştirme kuyruğu bunlardan) ve projeler arası paralel
**koşum** (görünüm var; birkaç uygulayıcıyı aynı anda çalıştırmak makine gate kilidine bağlandı
ama gerçek yükte denenmedi).

MIT.
