# Komutlar

Her `ao` komutu ve ne yaptığı. [README](../README.tr.md) her değişiklikte kullandığın beş komutla başlar; [başlarken](getting-started.md) ilk koşuyu adım adım anlatır.

| | |
|---|---|
| `ao status` · `ao watch` | tek proje: durum, telemetri, sorunlar, pano |
| `ao watch --all` · `ao fleet` | tüm projeler, insana en çok ihtiyaç duyan üstte |
| `ao watch --web [--port N]` | panel, pano ve tüm projeler 127.0.0.1'de salt okunur sayfalar olarak: terminal yerine bir tarayıcı sekmesi ([surfaces.md](surfaces.md)) |
| `ao board` | her iş nerede; READY = `needs:` bağımlılıkları tamamlanmış kuyruk maddeleri |
| `ao board add ID "başlık" --acceptance "…"` | bir maddeyi yalnız kabul sınırıyla birlikte kuyruğa alır |
| `ao verify [-p full]` | tanımlı gate'leri koştur, sonucu kaydet |
| `ao commit-ok [--verify]` | bu ağaç commit edilebilir mi? kanıttan karar |
| `ao commit -m …` · `ao commit-check --range A..B` | yetki verilen adayı commit et, mesajı yetkiyi adlandıran Ao- trailer'larıyla biter; inmiş her commit'in trailer'larının adlandırdığı ağaca sahip olduğunu `.ao/` gerekmeden, CI'ın koştuğu gibi denetle ([gates.md](gates.md#a-commit-names-its-grant)) |
| `ao hold` / `ao hold release --note …` | ağaçtaki tüm ajanları durdur ve durdurulmuş tut |
| `ao writers` / `ao writers --clean` | ağaçtaki canlı turlar (süreç değil tur başına bir), öksüzler ayrı; `--clean` yalnız öksüzleri durdurur |
| `ao fanout ok --agents N` / `ao fanout record …` / `ao fanout history` | N alt-ajanlık fan-out şimdi başlayabilir mi (üst sınır, yakın limit vuruşu, sağlayıcı penceresi); bir koşunun maliyetini kaydet |
| `ao cost --since 24h` | koordinasyonun kendisi ne harcıyor: uygulayıcı turları sınıfa göre (ürün / analiz / tören / koordinasyon), boşa giden turlar, review sayısı; `--since`, her zaman argümanı gibi, `30m`, `2h`, `1d`, `today`, `yesterday` ya da bir tarih alır; `--usd` harcamayı pakette gelen fiyat tablosuyla ABD doları cinsinden tahmin eder ([telemetry.md](telemetry.md)) |
| `ao features [on|off <anahtar>]` | anahtarlar ve her birinin maliyeti; hepsi kapalı = deterministik ao, sıfır model harcaması ([features.md](features.md)) |
| platformlar | macOS ve Linux yerel; Windows ilk sürüm ([windows.md](windows.md)); testler her push ve pull request'te Ubuntu'da, macOS ve Windows'ta haftalık koşar |
| `ao waive review --slice B7 --by <ad> --why …` / `ao catchup` | insan bir kapıyı kayıtlı biçimde atlar; catchup inen her aralığı onu yazan model ailesinden başka bir aileyle, commit mesajlarının iddia ettiklerine karşı review eder, `move-only` bir bölmeyi grant'in kaydettiği kanıtı yeniden çalıştırarak kapatır, ertelenen uyandırma/dürtmeleri yeniden oynatır. `ao catchup --plan` hiçbir şey yazmadan önizler ve hangilerinin kanıtla kapanacağını söyler, `ao catchup --limit 10` ve `ao catchup --slice B7` bir koşuyu sınırlar, `ao catchup --author-family <aile> --by <ad>` ao'nun kaydedemediği aileyi bir insanın adlandırmasıdır, `ao catchup --move-only <dilimler> --by <ad>` ise waiver'lı hangi dilimlerin yalnızca kod taşıdığını bir insanın beyan etmesidir; her biri yalnızca kanıt tutarsa kapanır; `ao catchup --slice B7 --reviewer <aktör>` tablodaki başka bir reviewer aktörüyle review eder, böylece farklı dilimler üzerindeki koşular yan yana yürür; bir koşu başlattığı review'lar hiçbir karar vermediğinde 3, ilerlediğinde ya da yapacak bir şeyi olmadığında 0 ile çıkar |
| `ao pings setup --url …` | dead man's switch: watchdog ve doctor işi birlikte ölünce alarm veren dış ping |
| `ao hooks [status|install|uninstall] [--allow-shared-hooks]` / `ao push allow` | Git'in etkin hook yolunu çöz; roller bağımsızdır, paylaşılan/harici/global mutasyonlar komutun tamamı için açık yetki ister |
| `ao skill install` / `ao skill show` | playbook (roller, döngü, yetki, protokol, alarmlar, tüm komutlar) deponun ajanları için: Claude skill, Kiro steering, AGENTS.md |
| `ao remove --yes [--allow-shared-hooks]` | iki aşamalı kaldırma: dayatma etkinken `.ao-project` dosyasını silip commit et, HEAD ve index artık taşımayınca AO durumunu kaldır; yabancı/korunan hook'lara dokunma. İkinci aşama projenin zamanlanmış işlerini ve `~/.ao` içindeki yalnız kendi dosyalarını kaldırır, kuru koşuda her birini adıyla listeler ve kaldıramadığını adıyla söyleyip 1 ile çıkar ([watchdog.md](watchdog.md)) |
| `ao update [--dry-run\|--yes]` | ao'yu kurulduğu yoldan güncelle: git klonu ileri sarılır, commit edilmemiş değişikliği varken reddedilir; Homebrew, pipx ya da uv kurulumu kendi aracıyla, pip kurulumu ao'yu çalıştıran yorumlayıcının pip'iyle yükseltilir. Komut önce gösterilir, `--yes` ya da terminaldeki bir evet ile çalışır ([başlarken](getting-started.md#5-update-and-uninstall)) |
| `ao uninstall [--yes] [--purge] [--allow-shared-hooks]` | ao'nun bu makineye kurduklarını kaldır: ao'nun her zamanlanmış işi, kayıt defterinin bildiği her projedeki ao'nun kendi hook'ları ve MCP dosyalarındaki `ao` girdisi; `--yes` olmadan kuru koşudur ve bıraktıklarını adıyla söyler - her projenin `.ao/` dizini, `--purge` yoksa `~/.ao` ve programın kendisi, onu kaldıran komutla birlikte |
| `ao init --profile claude-kiro|claude-claude [--review-tier same-family|person] [--language en|tr]` | rol bloklarını ve exact `.ao-project` kayıt işaretini yaz, ama stage etme; tek harness'lı profil bir review katmanı seçer ([profiles.md](profiles.md)); `--language tr` ile ao'nun projeye yazdıkları ve insanlara söyledikleri Türkçe olur ([configuration.md](configuration.md)) |
| `ao doctor --check` | zamanlayıcı için sessiz doctor: problem başına bir satır, exit 1, alarm — `ao watchdog install` 15 dakikalık launchd işi, Linux'ta systemd kullanıcı zamanlayıcısı olarak kurar |
| `ao email setup` / `ao email test` | kırmızı alarm kanalı: formsubmit.co ile e-posta, sunucu yok ([alarms.md](alarms.md)) |
| `ao alarms` / `ao alarms test --level red` | canlı alarm bölümleri ve seviyeleri; test tüm kanalları çaldırır |
| `ao mail log` / `ao mail search <metin>` / `ao mail ack <glob>` | posta defteri: yazılan her mesaj ve ne zaman okunduğu, silindikten sonra da aranabilir |
| `ao watchdog explain` / `ao watchdog trace` | watchdog neden dürttü ya da dürtmedi: bu döngünün ve kayıtlı döngülerin ölçüm ve kararları ([watchdog.md](watchdog.md)) |
| `ao source import` | takip sistemindeki işleri panoya kabul et |
| `ao mail` · `ao notices` | koordinasyon mesajları; projenin ürettiği uyarılar |
| `ao watchdog install` | takılan ajanı yeniden başlatan launchd işi, Linux'ta systemd kullanıcı zamanlayıcısı ([watchdog.md](watchdog.md)) |
| `ao mcp serve` · `ao a2a serve` | durumu MCP istemcilerine / A2A görevi olarak sun |
| `ao telegram setup` | telefona uyarı, telefondan karar |
| `ao digest [--days N]` | ne oldu, defterlerden okunur — "neden ilerlemiyor"un da cevabı |
| `ao ask` · `ao answer` · `ao decisions` | tek dokunuşla cevaplanan sorular; serbest metin hep sonda; cevap sorunun sunduğu bir harf olmalı, `ao answer <D-id> <harf> --change` bir cevabı değiştirir ve ilki kayıtta kalır |
| `ao propose "…" --why "…" [--rule-file <yol>]` · `ao proposals` | ajan, altında çalıştığı bir kuralı düzenlemek yerine değişikliği önerir: kanıtıyla karar defterine yazılır, bir insan `ao answer` ile kabul ya da ret eder; kural dosyasına hiçbir şey yazılmaz ([protocol.md](protocol.md#changing-the-rules-an-agent-proposes-a-person-decides)) |
| `ao note` | kutuya mimar mesajı, araç üzerinden |
| `ao review` | ağacı, onu yazmayan bir aktörle gözden geçir |
| `ao person-review --by <ad>` | bir insan stage edilmiş diff'i okur, sonra gösterilen `--digest` ile `--verdict APPROVED` ya da `NEEDS_CHANGES` kaydeder; her review gibi adaya bağlanır, `person review` diye etiketlenir, asla bir ajanın komutu değildir |
| `ao review --commits <aralık>` | inmiş işi sonradan review et; çıkış 3 = reviewer erişilemedi (asla verdict değil), yedekler `reviewer.fallbacks` ([roles.md](roles.md)) |
| `ao handoff` | devralanın ihtiyacı olan her şeyi yaz ve gönder |
| `ao a2a-mcp serve` | MCP-only istemciden A2A ajanlarına ulaş |
| `ao prune` | biriken kayıt ve logları buda |
| `ao doctor` · `ao adapters` | bağlantıları denetle; ne destekleniyor ve ne kadar |
| `ao completion zsh` | ao'nun komutlarını, seçeneklerini ve seçimlerini tamamlayan betiği yazdır; `bash`, `fish` ve `powershell` için de, [başlarken](getting-started.md#6-shell-completion) belgesindeki gibi kurulur ve `ao update` sonrası yeniden yazdırılır |
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

## Testler

Barındırılan `tests` iş akışı, main'e her push'ta ve her pull request'te paketi Ubuntu'da Python 3.9
(destek tabanı) ve 3.12 ile koşturur; macOS ve Windows her hafta ve istek üzerine koşar
(`gh workflow run tests -f os=windows-latest -f python=3.12`). Depo public olduğu için barındırılan
koşucular ücretsizdir; pre-push hook'u da push'tan önce paketi yerelde koşturur: önce push edilen
commit'lerin dokunduğu testleri, sonra bütün paketi, pytest-xdist (`dev` ekstrası) kuruluysa dört
süreçte - `AO_PREPUSH_WORKERS` kaç süreç olacağını belirler, 0 tek süreçte tutar. Bir sürüm etiketi,
yayımlamadan önce paketi yeniden koşturur. Barındırılan koşular, gerçek alt süreçler ve geçici yollarla
işletim sistemi API davranışını ve belirleyici süreç çökmelerini kapsar. Fiziksel güç kaybının,
depolama denetleyicisinin ya da dosya sisteminin - desteklenmeyen ve ağ dosya sistemleri dahil -
yeterlilik sınaması değildir.
