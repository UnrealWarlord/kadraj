# KADRAJ · Koordinatör kararları

Kullanıcı, thread'lerden gelen karar sorularında önerilen seçeneğin koordinatör tarafından seçilmesini ve hepsinin en sonda kendisine bildirilmesini istedi. Thread'ler de kendi küçük kararlarını adım raporlarında "Karar:" satırıyla yazar; bunlar son raporda burada toplanır.

| # | Adım | Konu | Seçilen | Neden |
|---|---|---|---|---|
| 1 | 4–6 | Ana ekranda "yakında" rozetinin testi | Her kart için `soon == modül kayıtlı değil` (sabit liste yok) | Modüller eklendikçe test kendiliğinden doğru kalır, thread'ler birbirinin testini kırmaz |
| 2 | 5 | API anahtarı "göz" düğmesi | Tam anahtar asla gösterilmez; göz yalnız ilk 6 + … + son 4 karakteri açar | Kasa kuralı (yalnız maskeli gösterim) korunur, kullanıcı hangi anahtar olduğunu yine ayırt eder |
| 3 | 5 | FFmpeg "tek tık güncelle" | yt-dlp: iş kuyruğunda pip ile güncelleme · FFmpeg: sistem paket yöneticisi talimatı + sürüm kontrolü | FFmpeg Linux'ta sistem paketi; uygulamanın onu kendiliğinden değiştirmesi riskli |
| 5 | 8c | Sonnet'in açık bıraktığı analitik paneli | Legend Proje merkezine bağlandı (KPI'lar gerçek veriyle), işi Haiku yaptı | Tuvaldeki Legend ana ekranında maliyet/token analitiği var; küçük, kolay iş |
| 6 | son kontrol | Varsayılan dil | İlk açılışta Türkçe, sistem yerel ayarından bağımsız; İngilizce yalnız Ayarlar'dan | Temiz sistemde (Türkçe yerel ayar yokken) uygulama İngilizce açılıyordu; KADRAJ Türkçe öncelikli ürün |
| 7 | son kontrol | Selftest kota kontrolü | Metin yerine sayısal sonucu doğrular, selftest dili açıkça ayarlar | Dile bağlı kontrol temiz Docker'da yanlış alarm veriyordu |
| 8 | son kontrol | Legend İndirici ve Ayarlar kontrastı | Sayfalar Sonnet'in modülleri gibi tema rollerine bağlanacak + piksel tabanlı kontrast testi | Açık zemin üstünde açık yazı çıkıyordu (okunmuyor) |
| 4 | 8 | 8. adımın bölünmesi | 8a (Haiku): prompt adaptörleri, stok medya istemcileri, `.fountain`/`.pdf` dışa aktarım, maliyet/token analitiği · 8b (Sonnet): sağlayıcılar, Fotoğraf, Dizi/Film akışı, düğüm editörü, foley | Kullanıcının "kolay iş Haiku'ya, zor iş Sonnet'e" kuralı; Haiku boşta kalmaz, Sonnet'in yükü ve token harcaması azalır |

## Thread'lerin kendi kararları (adım raporlarından)

| Adım | Thread | Karar |
|---|---|---|
| 5 | Haiku | Legend ayar bölümleri ilk seçildiklerinde kurulur (açılışta yalnız "Arayüz"); kabuk testleri 74 sn → 32 sn |
| 5 | Haiku | Yerel AI algılama ve araç sürümü kontrolü sayfa açılışında bir kez çalışır; yenileme düğmesi her zaman |
| 6 | Sonnet | Basit'te gürültü kapısı yumuşak sabit ön ayarlı (−42 dB) ve varsayılan KAPALI; Legend'de 5 parametre |
| 6 | Sonnet | Auto-gain / mikrofon testi yalnız öneri üretir; kazanç kendiliğinden değişmez ("Önerilen seviyeyi uygula") |
| 6 | Sonnet | Proje yokken kayıtlar `<proje_kökü>/Hizli_Kayitlar/02_Audio_WAV/` altına |
| 6 | Sonnet | Teleprompter ilk açılışta tuvaldeki örnek metinle gelir; kullanıcı metni ayarlara kaydedilir |
| 6 | Sonnet | 24-bit WAV, FFmpeg'de `WAVEX` başlığıyla (geçerli `.wav`) |
| 6 | Sonnet | Selftest, yerel 127.0.0.1 bağlantılarını "ağa çıkma" saymaz (Ollama/ComfyUI algılama) |
| 6 | Sonnet | 3. adımda kendi getirdiği tema gerilemesini (ilk açılışta QSS yüklenmemesi) düzeltti, regresyon testi ekledi |
| 7 | Sonnet | Zaman içeride saniye (float), kare sınırına yuvarlanır; `HH:MM:SS:FF` yalnız gösterim/giriş (drop-frame yok); proje dosyası eklemeli, bilinmeyen alanları korur |
| 7 | Sonnet | Donanım kodlayıcı denenerek algılanır (NVENC → VAAPI → CPU); ProRes, kayıpsız ve WAV her zaman CPU |
| 7 | Sonnet | Podcast şablonu yalnız `.wav` (24-bit/48 kHz, −16 LUFS); Arşiv = ProRes 422 HQ `.mov`; WhatsApp ≤15,5 MB'a göre bit hızı |
| 7 | Sonnet | Önizleme düşük çözünürlüklü birleşik dosyadan QMediaPlayer ile; "Önce" karesi efektler çıkarılarak üretilir |
| 7 | Sonnet | Her otomasyon önce öneri sunar → kullanıcı "Uygula" → tek geri alma adımı; ağır paket yoksa "İsteğe bağlı paketi kur" kartı |
| 7 | Sonnet | Basit Mod 3 kanal, model sınırsız; Legend'de 4 çalışma alanı (Düzenle/Renk/Ses/Dışa Aktar), her biri ayrı düzen kaydı |
| 8a | Haiku | Kritik prompt alanları: konu, ışık, stil, oran; açı/lens/ruh hali/negatifler isteğe bağlı ama soruları listelenir |
| 8a | Haiku | Ortak stil kütüphanesi (8 stil) + model başına negatif/parametre dosyaları; stok sağlayıcılar için ortak `providers/stock.py` |
| 8a | Haiku | Kullanıcının Türkçe metni 8a'da çevrilmez (LLM yok); çeviri 8b'de LLM varsa yapılır |

## Bileşenler (FFmpeg, Deno, yt-dlp) — "kutudan çıktığı gibi çalışsın"

| # | Konu | Seçilen | Neden |
|---|---|---|---|
| B1 | Açılışta ağ kuralı | **Eski "açılışta ağa çıkılmaz" kuralı kalktı.** Açılışta (splash) yt-dlp, FFmpeg ve Deno için sürüm denetimi yapılır; yalnızca kodda yazılı resmî GitHub sürümleri, yalnızca HTTPS, her dosya SHA-256 ile doğrulanır, kısa zaman aşımı, ağ yoksa sessiz atlama. Ayarlar → Araçlar'da "Açılışta bileşenleri güncelle" anahtarı (varsayılan açık), "Şimdi güncelle" düğmesi. `--selftest` ve testlerde kapalı | Kullanıcı kurulumdan sonra hiçbir şey elle kurmak/güncellemek istemiyor; YouTube eski yt-dlp'yi sık kırıyor, gömülü bir yt-dlp kendini güncelleyemiyor |
| B2 | Arama sırası | `core/components.py`: env (`KADRAJ_FFMPEG/FFPROBE/DENO/YTDLP`) → `~/.kadraj/tools/` (güncellenmiş) → pakete gömülü `tools/` → sistem PATH. Başka hiçbir yerde `shutil.which` yok | Tek yerden doğru ikili; güncellenmiş kopya gömülünün üstüne çıkar, bozulursa geri alınır (`.prev/`) |
| B3 | Güncelleme biçimi | yt-dlp: resmî zipapp (`sys.path`'e eklenir, içe aktarmadan önce) · FFmpeg: yalnızca **sürüm dalı** (9.0 → 9.1) değişince indirilir, günlük yeniden derlemeler değil · Deno: sürüm değişince. Küçükler (yt-dlp) splash'te, büyükler (FFmpeg, Deno) arka planda; kullanımda olan dosya (Windows) bir sonraki açılışta konur | Splash uzun bloklanmaz ("Atla" düğmesi); atomik `os.replace`, önce çalıştırılarak denenir |
| B4 | FFmpeg derlemesi | **GPL** (BtbN, sürüm dalı derlemesi), LGPL değil | Düzenleyici CPU H.264/H.265 (`libx264`/`libx265`) ve vekil dosyalar için bunlara ihtiyaç duyuyor; LGPL derlemede yok → dışa aktarma "kutudan çıktığı gibi" çalışmazdı. FFmpeg ayrı süreç olarak çalışır (kod bağlanmaz); kaynak bağlantısı `THIRD_PARTY_NOTICES.md`'de. Tek sabit: `core/updater.py::FFMPEG_VARIANT` |
| B5 | İndirici hataları | Hiçbir hata yalnızca "Olmadı" demez: sade neden + numaralı adımlar + eylem ("Bileşenleri şimdi güncelle" → `components.update`, "Araçlar bölümünü aç") | Kullanıcı teknik bilgiye sahip değil |
| B6 | Gerçek indirme sınaması | yt-dlp'nin kendi test videosu `BaW_jenozKc` artık YouTube'da "unavailable"; sınama `dQw4w9WgXcQ` ile yapıldı (en iyi ≤1080p + birleştirme + ffprobe) | Test videosu kaldırılmış; uygulamanın hatası değil |

## Koordinatör kararları (v1.0.4)

| # | Konu | Seçilen | Neden |
|---|---|---|---|
| 9 | FFmpeg derlemesi (B4) | Sonnet'in seçtiği **GPL** derlemesi onaylandı | LGPL derlemede `libx264`/`libx265` yok; CPU'da H.264/H.265 dışa aktarma ve vekil dosya çalışmazdı. FFmpeg ayrı süreç olarak çalışır, uygulama kodu MIT kalır; lisans ve kaynak bağlantısı `THIRD_PARTY_NOTICES.md`'de |
| 10 | Uygulama ikonu ve marka kuralı | Vizör işareti; "from İnsibağ" imzası yalnız ≥256 px boyutlarda, sağ altta, düşük opaklıkta | 16–128 px'te imza okunmaz, yalnız gürültü olur; büyük boyutlarda kural korunur |
| 11 | Anthropic `temperature` (API `2026-10-21`) | Bu API sürümünde `temperature` hiçbir modele gönderilmez; yalnız Ayarlar'dan eski sürüme dönülürse eski modellerde gönderilir | `2026-10-21` sürümünde örnekleme parametreleri her modelde 400 verir; eski bir model seçildiğinde istek düşüyordu |

## Mobil kararları (2026-11-26, Erol onayladı)

Kaynak: `docs/MOBILE_PLAN.md`. Erol önerilen seçenekleri onayladı ve sonraki kararları Claude'a bıraktı (M9).

| # | Konu | Seçilen | Neden |
|---|---|---|---|
| M1 | Telefon arayüzü | Yalnız Basit Mod, dokunmatik düzen; video düzenleme sade (kes, birleştir, müzik ekle, şablonla dışa aktar) | Legend telefona sığmaz; telefon işlemcisi tam NLE için zayıf |
| M2 | Tablet arayüzü | Masaüstündeki gibi: Basit + Legend Mode ve tam düzenleyici; aynı APK, en küçük genişlik ≥ 600 dp ise tablet düzeni; büyük dokunma hedefleri | Erol'un isteği: "tablet için ayrı oluştur, pc'deki gibi olsun". Tek APK hem güncellemeyi hem dağıtımı basit tutar |
| M3 | İşlemci mimarisi | Yalnız `arm64-v8a` | CPython Android'de yalnız aarch64/x86_64 destekler; 32-bit cihazlar çok eski |
| M4 | Anahtar kasası | İlk sürümde mevcut PBKDF2 parola yedeği; Android Keystore sonra | Masaüstünde zaten var ve sınanmış; Keystore pyjnius ile ayrı iş |
| M5 | iOS | **Kapsam dışı** (2026-11-26 Erol: "ios u boşver"); önce "sonraya" denmişti | Türkiye'de App Store'suz genel dağıtım yok; Apple hesabı ve Mac gerekir |
| M6 | Depo public/private | 7. adımda bakıldı: CI ikisinde de aynı çalışır, görünürlük Erol'un GitHub ayarı olarak kalır (Claude değiştirmez). APK'yı başkaları da Releases'ten indirecekse depo **public** olmalı; private'ta yalnız erişimi olanlar indirir | Görünürlüğü açmak geri alınamaz biçimde dışa dönük (kod kopyalanmış olabilir); derleme ve imza görünürlükten bağımsız |
| M7 | Sürüm ve yayın | Bütün adımlar geçince: tam test + `--selftest`, bulunan hatalar Erol'a raporlanır; sonra `mobile` → `main` (yerel) ve "Sürüm 3.0.0" commit'i. Push Erol'da, Actions kısmı Claude'da | Erol: "hepsi bitince commit v3.0.0 olarak yap ben pushlarım sen action kısımlarını halledersin", "commit etmeden önce sistemde hata varsa onları da söyle" |
| M8 | Derleme ortamı | Remote Control yok. APK derleme Claude'un kendi bulut makinesinde Docker içinde (`kadraj-android-build` imajı, `kadraj-android-sdk` birimi); imaj ve birim adımlar arasında saklanır, iş bitince silinir. Kod Erol'un depo klasörüne git bundle ile aktarılır | Erol bilgisayarının uzaktan kullanılmasını istemiyor; cihazdaki yalıtılmış klasör ortamında ağ ve Docker yok |
| M9 | Karar yetkisi | Kararları Claude alır, önerilen seçeneği seçer, burada listeler | Erol: "kararları bundan sonra kendin al", "recommended kısımları benim için uygundur" |
| M10 | En düşük Android sürümü | Android 9 (API 28); derleme API'si 35 | PySide6 6.12'nin Android aracı bunları sabitliyor (Qt'nin alt sınırı); plandaki "Android 8+" düzeltildi |
| M11 | Android uygulama kimliği | `org.insibag.kadraj` (Qt'deki kuruluş adı `insibag` ile aynı). İlk yayından (v3.0.0) sonra değişmez; değişecekse yayından önce | Kimlik değişirse kurulu uygulama güncellenemez, yeni uygulama sayılır |
| M12 | Android'de klasörler | Ayarlar, kasa, veritabanı: uygulamanın iç klasörü (`files/.kadraj`). Kullanıcı dosyaları: `HOME` = Qt'nin Documents klasörü (uygulamaya ayrılmış dış depolama, izin gerekmez); yazılamazsa iç klasör. Geçici dosyalar `files/tmp` | Masaüstü kodu (`Path.home()`) değişmeden çalışır; gizli veriler dışarıdan okunamaz; önbellek klasörü uzun render sırasında sistemce silinebileceği için geçici dosyalara kullanılmadı |
| M13 | Android'de araçlar | FFmpeg/ffprobe yalnız APK'nın yerel kütüphane klasöründen (`lib<ad>.so`); PATH ve `~/.kadraj/tools` ikililer için denenmez. Güncelleyici ve "Onar" yalnız yt-dlp (saf Python zipapp) | Android 10+ yazılabilir klasörden ikili çalıştırmaz (W^X); FFmpeg yeni APK ile güncellenir |
| M14 | Android'de FFmpeg ve QuickJS | FFmpeg 9.1 (x264, dav1d, libass, FreeType, FriBidi, HarfBuzz) ve QuickJS-ng **statik** çalıştırılabilir, `lib<ad>.so` adıyla APK'da; kaynaklar GitHub'dan sabit commit'le (`build_native.sh`). FFmpeg GPL v2+, TLS'siz. Kaynakların tam arşivi APK ile aynı Release'e eklenir | Statik araçlar QtMultimedia'nın FFmpeg kütüphaneleriyle çakışmaz ve Linux'ta qemu ile sınanır; ffmpeg.org/videolan bu ortamdan erişilemez; x264 masaüstüyle aynı dışa aktarmayı sağlar; indirmeyi yt-dlp yaptığı için FFmpeg'de ağ gerekmez |
| M15 | Android'de JS çalıştırıcı ve yazı tipleri | YouTube çözücüsü QuickJS-ng ile (masaüstünde Deno önce, QuickJS yalnız `KADRAJ_QUICKJS` ile). Altyazı yakma ve drawtext uygulamanın yazı tiplerini (Inter) kullanır | Deno'nun Android sürümü yok, yt-dlp-ejs QuickJS-ng'yi destekler; Android'de fontconfig yok |
| M16 | APK içeriği | Yalnız kullanılan Qt parçaları (kendi PySide6 tarifimiz: modüller + ELF bağımlılık kapanışı + resim/SVG eklentileri), Qt stili Fusion, `allow_backup=False`, izinler en az (`INTERNET`; 4. adımda `RECORD_AUDIO`, M18) | Deploy aracının tarifi tüm Qt'yi iki kez paketliyordu (deneme APK'sı 170 MB); ayarlar ve kasa bulut yedeğine gitmemeli |
| M17 | Android'de hata görünürlüğü | Açılışı durduran hata ekranda gösterilir ve `Belgeler/KADRAJ-hata.txt`'ye yazılır; yakalanmamış hatalar ve yerel çökmeler de oraya eklenir | Telefonda konsol yok; kara ekran yerine ekran görüntüsü ya da dosya gönderilebilir |
| M18 | Android'de ses kaydı | Ses girişi QtMultimedia `QAudioSource` (çekme kipi; Qt 6.12'de AAudio), kayıt motoru/DSP/arayüz aynı. `.wav` için `soundfile` yoksa standart kütüphaneyle PCM 24 bit yazma ve `wave` ile okuma. Mikrofon izni (`RECORD_AUDIO`) kayıt ya da mikrofon testi başlamadan sorulur; izin gelince iş kendiliğinden başlar, verilmezse telefon ayarlarını anlatan kart | PortAudio ve libsndfile Android'de yok; motorun VU, kapı ve LUFS özellikleri PCM bloklarına ihtiyaç duyar (`QMediaRecorder` dosyayı kendisi yazar); izin, kullanıcının o an yaptığı işle birlikte sorulunca anlaşılır |
| M19 | Android'de anahtar kasası | Kasa parola kipinde (PBKDF2-HMAC-SHA256, 600 000 yineleme, `hashlib`). Fernet `cryptography` yoksa saf Python (`kadraj/storage/fernet_lite.py`), dosya biçimi aynı. Parola Ayarlar'da belirlenir ve her oturumda açılır; unutulursa onaylı sıfırlama (eski dosya `.bad`). Android Keystore sonra | APK'da `cryptography` (Rust) ve `keyring` yok; kasa küçük, saf Python hızı yeterli. Belirteçler `cryptography` ile iki yönlü uyumlu, kasa telefon ile bilgisayar arasında taşınabilir |
| M20 | Android'de dosya eylemleri | APK'ya iki Java sınıfı: dosya sağlayıcı `KadrajFiles` (dışa kapalı, yalnız okunur, dosya başına geçici izin) ve komut köprüsü `Bridge` (Python uygulama klasörüne JSON komut yazar, Java `FileObserver` ile işler). "Klasörde göster" yerine "Paylaş" ve "Galeriye kaydet" (Android 11+, `Movies`/`Music`/`Pictures`/`Download` altında uygulama klasörü), "Aç" uygun uygulamayı açar. Seçilen `content://` dosyası uygulama klasörüne (`Belgeler/Imports`) kopyalanır. Kayıt ve işler sürerken ekran açık kalır. Geri tuşu: sayfadan ana ekrana, ana ekranda iki basışta çıkış (p4a'nın tuş yutan kısayolu kaldırıldı) | PySide6 Android'de `QJniObject` yok, p4a'nın Qt önyüklemesinde pyjnius kullanılamıyor; dosyayla konuşmak bağımlılıksız ve sınanabilir. Android'de klasör kavramı kullanıcıya açık değil |
| M21 | Android'de olmayan özellikler | Gösterilmez (devre dışı düğme yerine hiç yok): metinle kurgu ve altyazı (faster-whisper), ritme göre kes (librosa), dikeye çevir (OpenCV), arka plan silme (rembg), H.265, NVENC/VA-API, işlemci/ekran kartı ölçümü, yerel AI adımı, FFmpeg için "Bileşenleri güncelle" | Bu paketler Android'de kurulamıyor; "kur" kartı orada işe yaramaz. Sistem panelinde RAM (`/proc/meminfo`), disk ve ping kalır |
| M22 | Android simgesi | Uyarlanır simge (vektör ön plan, degrade zemin, Android 13 için tek renk katman), `kadraj.svg`'den `tools/make_icons.py` üretir; imza yok | Başlatıcı simgesi ≤192 px; imza yalnız ≥256 px (marka kuralı). Maskeler (daire, damla) PNG'yi keserdi |
| M23 | Android metinleri | "bilgisayar" diyen metinlerin "cihaz" diyen eşi ayrı tabloda (`ANDROID_STRINGS`); Android'de önce o tabloya bakılır | Masaüstü metinleri değişmez; yeni metin eklendiğinde test "bilgisayar" diyen Android metnini yakalar |
| M24 | Telefon ve tablet düzeni | Telefonda her Basit sayfa 360 px'e, tablet üst çubuğu 600 px'e sığar; sığmayan satır alt satıra geçer (`FlowLayout`). Tablet dikeyde Legend panelleri gizlenir; Legend sayfası ekrandan genişse yana kayar, yüksekliği ekrana sıkışır | 360 dp en dar yaygın telefon, 600 dp en dar tablet. Tablet masaüstüne benzesin (M2) ama hiçbir şey ekrandan taşmasın |
| M25 | Dokunmatik | Bütün kaydırma alanları parmakla kayar (`QScroller`); çizim alanları (zaman çizelgesi, aralık çubuğu, fırça, eğri) dokunmayı doğrudan alır; Dizi panosunda sürükle-bırak yerine "Taşı" listesi | Qt Widgets Android'de parmakla kaydırmayı kendiliğinden yapmaz; sürükleme ile kaydırma aynı harekette çakışır |
| M26 | Android CI derlemesi | `release.yml`'e `android` işi: Docker'da `packaging/build_android.sh` (NDK r28c sabit SHA-256 ile → yerel araçlar → APK); yerel araçlar `actions/cache` ile önbellekte; imza ağsız konteynerde `packaging/android/sign_apk.sh` (zipalign 16 KB hizası, apksigner v2 + v3); yayımlanan dosyalar `KADRAJ-<sürüm>-android-arm64.apk` ve `KADRAJ-<sürüm>-android-native-sources.tar` (+ `.sha256`) | Linux paketindeki gibi tek betik: yerelde ve CI'da aynı adımlar. FFmpeg derlemesi uzun sürdüğü için önbellek; anahtar yalnız imza adımına girer, parola komut satırına ve günlüğe düşmez |
| M27 | İmza gizlileri yoksa | Elle denemede uyarı + imzasız APK (yalnız derleme denetimi, kurulamaz); release'te iş derlemeden önce düşer | Anahtar eklenmeden de CI denenebilsin; yayımlanan APK her zaman imzalı olsun |
| M28 | Cihazsız Android denemesi | Bilgi amaçlı `android-smoke` işi: GitHub'ın KVM'li çalıştırıcısında x86_64 Android 11 emülatörü (ARM64 çevirisiyle) APK'yı telefon ve tablet boyutunda açar; uygulama açılış aşamalarını logcat'e yazar (`crashlog.note`); ekran görüntüleri artifact'ta; sürümü engellemez | Bulut makinesinde KVM yok, ilk gerçek Android çalıştırması burada olur. ARM çevirisi yavaş ve kararsız olabileceği için kapı değil |
