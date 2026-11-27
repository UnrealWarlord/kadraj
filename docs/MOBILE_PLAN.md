# KADRAJ Mobil — Devir Belgesi (Android önce, iOS sonra)

> Bu belge, KADRAJ'ı Android (ve ileride iOS) telefon/tablete taşıyacak **yeni bir Claude oturumu** için yazıldı.
> Başka bağlam gerekmeden buradan başlanabilir. Tarih: 2026-11-26 (KADRAJ v2.0.0 yayınlandıktan sonra).
> Dağıtım kuralları tarihe bağlı olarak değişiyor: **başlamadan önce "Dağıtım gerçekleri" bölümündeki bilgileri yeniden doğrula.**

---

## 0. İlk okuma (yeni oturum için)

1. Bu belgenin tamamı.
2. `README.md` — ürün, modüller, kurulum.
3. `docs/DECISIONS.md` — alınmış kararlar (özellikle B1–B6: bileşenler, 9–11: koordinatör).
4. Kod haritası için yalnızca şunlar: `main.py`, `kadraj/core/components.py`, `kadraj/core/updater.py`, `kadraj/paths.py`, `kadraj/frozen.py`, `kadraj/ui/shell/app.py`, `packaging/kadraj.spec`, `.github/workflows/release.yml`.
   Gerisini ihtiyaç oldukça oku.

## 1. Kullanıcının çalışma kuralları (uy)

- Kullanıcı Türkçe konuşur; arayüz metinleri Türkçe + İngilizce (`strings.py`).
- **Kapsam 2026-11-26'da onaylandı; kararları artık Claude alır** (Erol: "kararları bundan sonra kendin al", "önerilen seçenekler benim için uygun"). Önerilen seçeneği seç, `docs/DECISIONS.md` → "Mobil kararları" tablosuna yaz; yalnız geri alınamayan ya da kullanıcının hesabını gerektiren işlerde dur (M9).
- **Remote Control yok.** Ağır işler (APK derleme) Claude'un kendi bulut makinesinde Docker içinde yapılır, iş bitince imaj ve birimler silinir; kod değişiklikleri kullanıcının depo klasörüne aktarılır (M8).
- **Commit'ten önce hata raporu:** 3.0.0 commit'inden önce tam test takımı + `--selftest` çalıştırılır ve bulunan bütün hatalar/bilinen sorunlar kullanıcıya listelenir (M7).
- **Başlamadan önce rapor ver, onay al.** En fazla 8 adımlık bir yol haritası çıkar; kolay adımları **Haiku**, orta/zor adımları **Sonnet** thread'ine ver (kullanıcı bunu açıkça istiyor). Her adımdan sonra küçük kontrol, en sonda detaylı doğrulama.
- Thread'lerin sorularında önerilen seçeneği koordinatör seçer, hepsini en sonda kullanıcıya listeler (`docs/DECISIONS.md`'ye yaz).
- **Commit'ler yalnız yerel kayıt noktasıdır.** Push, etiket, Release, Actions'ı çalıştırma, silme gibi **GitHub işlerini kullanıcı kendisi yapar** (GitHub'ı öğreniyor). Sen adım adım tarif edersin; `gh` ile yalnızca okuma (log, durum) yaparsın. Kullanıcı açıkça "sen hallet" derse o istek için yaparsın.
- Doğrulama Docker / izole `.venv` içinde yapılır; doğrulama için oluşturulan her şey (imajlar, venv'ler, geçici klasörler) sonra silinir. Önceden var olan `postgres:16` imajına ve `gpusa-db` konteynerine dokunma.
- Marka kuralı: "KADRAJ" adı/logosu nerede görünürse sağ altında küçük, düşük opaklıklı **"from İnsibağ"** imzası olur (ikonlarda yalnız ≥256 px). Yalnızca `Lockup` bileşeni üzerinden.
- **Dal (branch) kuralı:** Bütün mobil iş **`mobile` dalında** yapılır; `main`'e dokunulmaz.
  - Başlarken: `git switch mobile` (dal yerelde var; GitHub'da yoksa kullanıcı `git push -u origin mobile` ile gönderir).
  - Yerel commit'ler `mobile` dalına atılır; push'u kullanıcı yapar (`git push`).
  - CI denemesi: kullanıcı Actions → "Sürüm paketleri" → **Run workflow** → dal olarak `mobile` seçer (yalnız artifact üretir, Release'e dokunmaz).
  - `main`'de yeni bir masaüstü düzeltmesi olursa `mobile` dalına `git merge main` ile alınır (çakışma olursa kullanıcıya sorulur).
  - İş bitip son doğrulama geçince ve **kullanıcı onaylayınca** `main`'e birleştirilir: `git switch main && git merge --no-ff mobile`; sonra sürüm yükseltilir ve Release'i kullanıcı oluşturur.
- Sürüm: `kadraj/version.py` tek kaynaktır. Release etiketi tam olarak `v` + bu sürüm olmalı; değilse CI derlemeden durur. Release silmek etiketi silmez — yeniden yayında etiketi de silip `main`'den yeni etiket oluşturmak gerekir.

## 2. KADRAJ bugün (v2.0.0)

- Python 3.12 + **PySide6 6.12** masaüstü uygulaması; iki arayüz modu: **Basit Mod** (pastel, dokunmatiğe yakın büyük kartlar) ve **Legend Mode** (koyu, çok panelli profesyonel NLE).
- Modüller (`kadraj/modules/`): `downloader` (yt-dlp), `recorder` (yalnız `.wav`, sounddevice/PortAudio), `editor` (FFmpeg filtergraph ile render, QMediaPlayer önizleme), `photo` (bulut/yerel görsel AI), `series` (fikir → bölüm → senaryo, onay kapıları, `.fountain`/`.pdf`), `settings`.
- AI: `kadraj/ai_engine/` (Anthropic, OpenAI, Google, FLUX, Ollama, ComfyUI; ham httpx; SmartError eşlemeleri).
- Depolama: `~/.kadraj` (ayarlar, SQLite, Fernet kasa + OS keyring, PBKDF2 yedeği), proje klasörü iskeleti `01_Downloads … 07_Exports`, 20 sn otomatik kayıt, 10 dk geri dönüş, Legend snapshot'ları.
- Bileşenler: FFmpeg/ffprobe (GPL derleme), Deno (yt-dlp'nin YouTube için JS çalıştırıcısı), yt-dlp zipapp. `core/components.py` tek bulucudur (env → `~/.kadraj/tools` → pakete gömülü → PATH). `core/updater.py` açılışta resmî GitHub sürümlerinden SHA-256 doğrulamalı günceller; `--seed` CI'da pakete koyar; Ayarlar → Araçlar'da "Şimdi güncelle" / "Onar".
- Paketleme: PyInstaller onedir (`packaging/kadraj.spec`); Linux tar.gz (AlmaLinux 9 / glibc 2.34 Docker), Windows Inno Setup `setup.exe`. `.github/workflows/release.yml`: `workflow_dispatch` (yalnız artifact) ve `release: published` (Release'e ekler), sürüm kapısı, selftest kapısı.
- Testler: ~1100 pytest (`QT_QPA_PLATFORM=offscreen`), `python main.py --selftest` (ağ kapalı, geçici HOME).
- Geliştirme venv'i: `/home/konahrik/Belgeler/Projects/Insibag/KADRAJ/.venv` (Python 3.14, PySide6 6.12). Repo: `/home/konahrik/Belgeler/GitHub/kadraj` (GitHub: `UnrealWarlord/kadraj`, şu an **private**).

## 3. Hedef

- **Android (telefon + tablet):** APK olarak GitHub Releases'ten indirilip kurulsun; **Play Store zorunlu olmasın.**
- **iOS:** Kullanıcı App Store'suz istiyor; Türkiye'de bu genel dağıtım için mümkün değil (aşağıya bak). iOS ayrı bir karar olarak bekliyor.

## 4. Dağıtım gerçekleri (2026-11 itibarıyla — yeniden doğrula)

### Android
- APK'yı mağaza dışından kurmak mümkün ("bilinmeyen uygulamaları yükle" izni).
- **Geliştirici doğrulaması:** Google, sertifikalı cihazlarda mağaza dışı kurulan uygulamalar için de geliştirici kimlik doğrulaması istiyor. 30 Eylül 2026'dan beri Brezilya, Endonezya, Singapur, Tayland'da uygulanıyor; **2027'de dünya geneline (Türkiye dahil)** yayılacak (bir kaynak Ocak 2027'den başlayıp aylar içinde yayılacağını söylüyor).
  - Play Store'a yükleme gerekmez, ama **kullanıcının Android Developer Console'da geliştirici olarak kimliğini doğrulatması** gerekecek (ücret/koşulları başlamadan kontrol et).
  - Doğrulanmamış geliştiricinin APK'sı "gelişmiş akış" (geliştirici modu, baskı altında olmadığını onaylama, yeniden başlatma, 24 saat bekleme, biyometri/PIN) ya da **ADB** ile kurulabilir.
- Kaynaklar: [Android developer verification](https://developer.android.com/developer-verification), [Android Developers Blog (2026-06)](https://android-developers.googleblog.com/2026/06/android-developer-verification.html), [Heise: Ocak 2027 dünya geneli](https://www.heise.de/en/news/Starting-January-2027-Google-expands-Android-developer-verification-worldwide-11534186.html), [Android Authority: gelişmiş akış](https://www.androidauthority.com/google-android-advanced-flow-sideloading-rollout-begins-3700073/), [Keep Android Open](https://en.wikipedia.org/wiki/Keep_Android_Open).

### iOS
- APK yok. AB'deki alternatif mağaza / web dağıtımı (DMA) **Türkiye'de geçerli değil.**
- Kalan yollar (hepsi Apple hesabı ister):
  - **TestFlight:** Apple Developer Program (yıllık ücretli) + App Store Connect; Apple'ın kısa beta incelemesi; sınırlı sayıda test kullanıcısı, derlemeler süreli.
  - **Ad Hoc:** önceden kaydedilen sınırlı sayıda cihaz (UDID), yıllık ücretli üyelik.
  - **AltStore / SideStore:** ücretsiz Apple hesabıyla, uygulama **7 günde bir** yeniden imzalanır; teknik kullanıcı işi.
- Teknik: iOS ayrı süreç (`ffmpeg` gibi çalıştırılabilir dosya) başlatmaya izin vermez → FFmpeg kütüphane olarak gömülmeli. **Qt for Python'un iOS desteği Qt 6.12 ile geldi ama `pyside6-ios-deploy` benzeri bir araç henüz yok** (planlanıyor). macOS + Xcode ile imzalama gerekir (GitHub `macos-latest` çalıştırıcısı derleyebilir ama sertifika/profil kullanıcının Apple hesabından gelir).
- Kaynak: [Qt blog: PySide6 on iOS](https://www.qt.io/blog/python-mobile-app-development-bringing-pyside6-on-ios).

## 5. Önerilen yaklaşım: aynı Python kodu + PySide6 Android aracı

- Araç: **`pyside6-android-deploy`** (Qt for Python resmî). Yalnız Linux/macOS host'ta çalışır (kullanıcının makinesi Linux, CI `ubuntu-latest`). İçeride özel bir python-for-android çatalı + PySide6/shiboken6 tarifleri kullanır; NDK/SDK'yı kendisi indirir. Giriş dosyası **`main.py`** olmalı (zaten öyle).
  - Android tekerlekleri: `qtpip download PySide6 --android --arch aarch64` (ve gerekirse `x86_64` emülatör için).
  - **Debug modu APK, release modu AAB üretir.** Yan yükleme için release AAB → `bundletool build-apks --mode=universal` → `apksigner` ile imzalı evrensel APK. (Bayrakları başlamadan `pyside6-android-deploy --help` ve güncel dokümanla doğrula.)
  - Kaynak: [pyside6-android-deploy](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-android-deploy.html), [Qt blog: Taking Qt for Python to Android](https://www.qt.io/blog/taking-qt-for-python-to-android). Topluluk alternatifi: [PySide6-to-Android](https://www.github.com/EchterAlsFake/PySide6-to-Android).
- Alternatif (yalnız yukarısı tıkanırsa öner, kullanıcıya sor): mobil için ayrı ince istemci (Flutter/Kotlin) — tek kod tabanından iOS da çıkar ama Python çekirdeği yeniden yazılır.

## 6. Teknik engeller ve çözüm önerileri (Android)

| Alan | Masaüstünde | Android'de sorun | Öneri |
|---|---|---|---|
| Arayüz | Basit + Legend (çok panel, dock) | Legend telefona sığmaz | **Telefon:** yalnız **Basit Mod**'un dokunmatik düzeni (tek sütun), Legend kapalı (M1). **Tablet** (en küçük genişlik ≥ 600 dp): masaüstündeki gibi **Basit + Legend** ve tam düzenleyici; aynı APK ekran boyutuna göre seçer (M2). Her ikisinde büyük dokunma hedefleri, sistem geri tuşu, döndürme. |
| FFmpeg | `~/.kadraj/tools` / paket / PATH, alt süreç | Android 10+ W^X: yazılabilir klasörden ikili çalıştırılamaz | FFmpeg/ffprobe'u `jniLibs/arm64-v8a/libffmpeg.so` olarak göm, `nativeLibraryDir`'den çalıştır (`extractNativeLibs=true`). `components.py`'ye "android" kaynağı ekle. Android için GPL/LGPL statik derleme (lisans: `THIRD_PARTY_NOTICES.md`). |
| JS çalıştırıcı | Deno | Deno'nun Android sürümü yok | yt-dlp-ejs'in desteklediği **QuickJS / quickjs-ng**'yi Android için derleyip aynı şekilde göm; yt-dlp'ye çalıştırıcıyı açıkça etkinleştir (yalnız deno varsayılan açık). Kaynak: [yt-dlp-ejs](https://pypi.org/project/yt-dlp-ejs/). |
| Açılışta güncelleme | yt-dlp, FFmpeg, Deno | İndirilen ikili çalıştırılamaz | Android'de updater yalnız **yt-dlp zipapp** (saf Python) için çalışsın; FFmpeg/QuickJS güncellemesi yeni APK ile. Ayarlar'da bunu açıkça yaz. |
| Ses kaydı | sounddevice + PortAudio, `.wav` | PortAudio yok | `QMediaCaptureSession` + `QAudioInput` + `QMediaRecorder` (ya da `QAudioSource` ile PCM → `.wav` yazma). `RECORD_AUDIO` çalışma zamanı izni. Çıktı yine yalnız `.wav` (marka testi `.mp3` vb. literalleri yasaklar). |
| Anahtar kasası | Fernet + OS keyring | `keyring` Android'i desteklemez | İlk sürümde mevcut **PBKDF2 parola yedeği** (M4); Android Keystore (pyjnius üzerinden) sonra. Anahtar asla düz metin/log yok, ekranda maskeli. |
| Dosyalar | `~/.kadraj`, proje klasörü, "Klasörde göster" | Scoped storage | Uygulama özel klasörü + dışa aktarmada MediaStore / SAF ("Paylaş" ve "Galeriye kaydet"); "Klasörde göster" yerine "Paylaş"/"Aç". |
| Sistem izleme | psutil, nvidia-ml-py | Kısıtlı | Mobilde sysmon panelini gizle ya da yalnız pil/depolama. |
| Uzun işler | iş kuyruğu (QThreadPool) | Arka planda süreç öldürülebilir | Uzun indirme/render için ön plan servisi bildirimi (`POST_NOTIFICATIONS`, `FOREGROUND_SERVICE_DATA_SYNC`) ya da "ekran açık kalsın" + kaldığı yerden devam. |
| Video düzenleme | Tam NLE | Ekran/işlemci | **Telefonda sade:** kes, birleştir, müzik ekle, platform şablonuyla dışa aktar (M1). **Tablette tam düzenleyici** (M2). |
| Boyut | — | — | APK tahminen 150–250 MB; yalnız `arm64-v8a` (M3). |
| Bağımlılıklar | requirements.txt | C uzantılı paketlere tarif gerekir | numpy, Pillow, cryptography için python-for-android tarifleri var; reportlab, soundfile, psutil, keyring için kontrol et; olmayan özellik mobilde kapalı + sade açıklama. |

Ağ, AI sağlayıcıları (httpx), SQLite, i18n, otomatik kayıt, proje modeli büyük ölçüde aynen çalışmalı.

## 7. Önerilen yol haritası (≤8 adım, şeritler)

| # | Adım | Şerit | Bitti sayılır |
|---|---|---|---|
| 1 | Keşif: `pyside6-android-deploy` ile **boş bir PySide6 merhaba-dünya** APK'sı Docker'da derle, emülatörde/ADB ile aç; bağımlılık matrisi (hangi paket tarifle gelir) | Sonnet | İmzalı debug APK açılıyor; `docs/steps/MOBILE_1.md` |
| 2 | Platform katmanı: `kadraj/platform.py` (`is_android()`), `paths.py` Android klasörleri, `components.py` Android kaynağı, updater'ın Android davranışı | Sonnet | Testler masaüstünde aynen geçiyor + Android dalı birim testli |
| 3 | FFmpeg + QuickJS'i Android için derle/göm (`jniLibs`), yt-dlp'yi QuickJS ile çalıştır | Sonnet | Emülatörde yt-dlp test videosu ≤1080p iniyor ve birleştiriliyor |
| 4 | Ses kaydı Android yolu (QtMultimedia), izinler | Sonnet | Emülatörde `.wav` kaydı, kayıt yeri görünüyor |
| 5 | Kasa: Android Keystore ya da parola yedeği; Ayarlar'da anahtar girişi | Sonnet | Anahtar kaydet/oku, maskeli gösterim |
| 6 | Telefon: Basit Mod dokunmatik düzeni (Legend kapalı) · Tablet: masaüstü gibi Basit + Legend ve tam düzenleyici · Paylaş/Galeriye kaydet, ikon ve "from İnsibağ" kuralı | Sonnet (tablet işi eklenince zorlaştı) | Ekran görüntüleri (telefon + tablet), offscreen testler |
| 7 | CI: `release.yml`'e `android` işi (ubuntu-latest): seed → APK → imzala → `KADRAJ-<sürüm>-android-arm64.apk` + `.sha256` → Release'e ekle; sürüm kapısı aynı | Sonnet | `workflow_dispatch` artifact'ı oluşuyor (kullanıcı çalıştırır) |
| 8 | README + sürüm notu: Android kurulumu ("bilinmeyen uygulamalara izin ver", gelişmiş akış, geliştirici doğrulaması notu) ve son doğrulama; tam test + hata raporu kullanıcıya; sonra `mobile` → `main` (yerel) ve **Sürüm 3.0.0** commit'i (M7) | Haiku + koordinatör | Docker'da tam derleme + emülatör sınaması; geçici her şey silindi |

Doğrulama: KVM varsa Docker'da Android emülatörü (x86_64 imaj + x86_64 APK) ya da kullanıcının cihazına ADB. Gerçek cihaz denemesi kullanıcıda.

## 8. Kullanıcının yapması gerekenler (Claude yapamaz)

1. **Kapsamı onaylamak** (Bölüm 6–7).
2. **İmza anahtarı (keystore):** bir kez oluşturulur; **kaybolursa kullanıcılar güncelleme alamaz.** Claude komutu verir, kullanıcı çalıştırır ve şifreyi saklar. Örnek:
   `keytool -genkeypair -v -keystore kadraj-release.jks -alias kadraj -keyalg RSA -keysize 4096 -validity 10000`
   Yedeğini güvenli bir yerde tutar; GitHub → Settings → Secrets and variables → Actions'a kendisi ekler (öneri: `KADRAJ_KEYSTORE_B64`, `KADRAJ_KEYSTORE_PASSWORD`, `KADRAJ_KEY_ALIAS`, `KADRAJ_KEY_PASSWORD`). Claude anahtarı/şifreyi görmez, sohbete yazılmaz.
3. **Test cihazı:** Android 9+ (API 28; Qt 6.12'nin alt sınırı, M10) telefon ve/veya tablet; "geliştirici seçenekleri" + USB hata ayıklama (ADB ile kurulum için).
4. **2027'den önce Android geliştirici doğrulaması** (Android Developer Console, kimlik).
5. **iOS istenirse:** Apple Developer Program üyeliği (yıllık ücretli) + dağıtım yolu kararı (öneri: TestFlight) + bir Mac ya da macOS CI'da imzalama için sertifika/profil.
6. Push kullanıcıda. **3.0.0 için Actions kısmını Claude yürütür** (kullanıcı: "ben pushlarım, sen action kısımlarını halledersin", M7).

## 9. Açık kararlar — 2026-11-26'da yanıtlandı

Ayrıntı ve gerekçeler: `docs/DECISIONS.md` → "Mobil kararları".

- Video düzenleme: telefonda sade, tablette tam (M1, M2).
- Mimari: yalnız `arm64-v8a` (M3).
- Kasa: önce parola yedeği, Keystore sonra (M4).
- Depo public/private: 7. adımda (CI) karar verilecek (M6).
- iOS: sonraya bırakıldı (M5).
