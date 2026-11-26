# Mobil 3. adım · FFmpeg + QuickJS gömme, yt-dlp, gerçek uygulama APK'sı

Tarih: 2026-11-26 · Dal: `mobile` · Ortam: Claude'un bulut makinesinde Docker (`kadraj-android-build`,
`kadraj-test`, sınama için `kadraj-qemu`; M8). KVM yok: emülatör çalışmaz, cihaz denemesi Erol'da.

## Sonuç

- **Yerel araçlar (`packaging/android/build_native.sh`, yeni):** NDK r28c, API 28, arm64-v8a. Kaynaklar GitHub'dan
  sabit commit'le (betik commit eşleşmezse durur): FFmpeg **n9.1** (`ffmpeg version 9.1`), x264 (stable), dav1d 1.5.5,
  FreeType 2.15.0, FriBidi 1.0.18, HarfBuzz 15.2.0, libass 0.17.5, QuickJS-ng **0.18.0**. Çıktılar **statik**
  çalıştırılabilir dosyalar: `libffmpeg.so` (25 MB), `libffprobe.so` (25 MB), `libqjs.so` (1,7 MB). Statik oldukları için
  yanlarında `.so` gerekmez, QtMultimedia'nın kendi FFmpeg kütüphaneleriyle (`libavcodec.so`…) çakışmazlar ve aynı dosyalar
  Linux'ta qemu ile sınanabilir. 16 KB sayfa hizalı (Android 15+ cihazlar). Lisans metinleri `licenses/`, sürümler ve
  FFmpeg configure satırı `VERSIONS.txt`, kaynakların tam arşivi `android-native-sources.tar` (GPL karşılığı kaynak;
  7. adımda Release'e eklenecek). Tam derleme ~13 dk.
- **FFmpeg özellikleri:** kodlayıcılar libx264, aac, prores_ks, png, mjpeg, gif, pcm; çözücüler H.264/HEVC/VP9/VP8/AV1
  (dav1d)/AAC/Opus/MP3/FLAC; süzgeçler subtitles/ass (libass), drawtext, loudnorm, chromakey, lut3d, sidechaincompress,
  silencedetect, atempo, xfade, overlay; `lavfi` girişi. Ağ protokolü (TLS) yok: indirmeyi yt-dlp (Python) yapar, FFmpeg
  yalnız yerel dosyaları birleştirir/işler.
- **Sınama (`packaging/android/test_native.sh`, yeni):** qemu-aarch64 ile Linux'ta, emülatörsüz. Kodlama (H.264+AAC),
  ffprobe (akışlar, JSON), `-c copy` birleştirme, AV1 çözme, altyazı yakma (uygulamanın yazı tipleriyle, fontconfig
  yok; karede göründüğü piksel karşılaştırmasıyla doğrulanır), drawtext, loudnorm+atempo, QuickJS sürümü ve modern JS
  (BigInt, özel alanlar, `toSorted`, `Object.groupBy`), yt-dlp'nin QuickJS-ng'yi tanıması ve **uçtan uca indirme**:
  KADRAJ'ın Android yolu (taklit) + bu arm64 araçlar → yerel DASH yayını (ayrı görüntü/ses, YouTube biçimleri gibi) →
  yt-dlp → FFmpeg birleştirme → ffprobe (`main.py --selftest-download` → **İNDİRME TAMAM**). Sonuç: **ANDROID ARAÇLARI
  TAMAM** (17/17).
- **Uygulama kodu:**
  - `kadraj/core/components.py`: yeni bileşen `quickjs` (`KADRAJ_QUICKJS`, Android'de `libqjs.so`); `js_runtime()`
    önce Deno, yoksa QuickJS; Android'de `ffmpeg_location()` `~/.kadraj/bin` altında `ffmpeg`/`ffprobe` adlı
    bağlantılardan oluşan bir klasör verir (yt-dlp klasörde bu adları arar; APK'daki adlar `lib*.so`). QuickJS sürümü
    `--help`'in ilk satırından okunur.
  - İndirici: yt-dlp'ye `js_runtimes={"quickjs": {"path": …}}` (Android) ya da Deno; "JS çözücü yok" hatası artık
    ikisine de bakar. Açılış ekranı ve Ayarlar'daki satır Android'de "Video çözücü (QuickJS)" der.
  - Düzenleyici: Android'de altyazı yakma `fontsdir=<uygulamanın yazı tipleri>` + `FontName=Inter`, drawtext
    `Inter-SemiBold.ttf` ile (Android'de fontconfig yok); otomatik altyazı (`build_ass`) aynı yazı tipini kullanır.
    Masaüstü komutları değişmedi.
  - `kadraj/paths.py`: Android'de `SSL_CERT_FILE` = certifi'nin listesi (Android'deki OpenSSL'in sistem sertifika
    klasörü yok; urllib/ssl için).
  - `kadraj/crashlog.py` (yeni): Android'de açılışı durduran hata kara ekran bırakmaz; iletişim kutusunda gösterilir
    (ekran görüntüsü alınabilir) ve `Belgeler/KADRAJ-hata.txt`'ye yazılır. Yakalanmamış hatalar ve yerel çökmeler
    (faulthandler) da aynı dosyaya eklenir. Masaüstünde hiçbir şey değişmez.
  - Android'de Qt stili açıkça Fusion (Android stili eklentisi paketlenmez; masaüstü Linux'takiyle aynı taban).
- **APK (`packaging/android/build_apk.sh`, yeni):** `pyside6-android-deploy --init` + buildozer; kimlik
  `org.insibag.kadraj`, sürüm `kadraj/version.py`'den, dört yön, yalnız `INTERNET` izni (RECORD_AUDIO 4. adımda),
  `allow_backup=False`, simge `kadraj-512.png`, yerel araçlar `android.add_libs_arm64_v8a` ile. Paketler: numpy, Pillow
  (tarif), reportlab (yerel saf Python tarifi), httpx ailesi, certifi, charset-normalizer (saf kaynaktan), yt-dlp,
  yt-dlp-ejs. Lisanslar APK'nın içinde (`LICENSE.txt`, `THIRD_PARTY_NOTICES.md`, `kadraj/ui/assets/licenses/android/`).
- **İnceltilmiş PySide6 tarifi (`packaging/android/recipes/PySide6`, yeni):** deploy aracının ürettiği tarif tekerleğin
  tamamını iki kez paketliyordu (deneme APK'sı 170 MB, açılınca yüzlerce MB). Yeni tarif yalnız uygulamanın Qt
  modüllerini (Core, Gui, Widgets, Svg, Multimedia + bağımlılıkları Network, Concurrent), açılışta yüklenen kütüphaneleri,
  JPEG/WebP/GIF/ICO/SVG resim ve SVG simge eklentilerini ve bunların ELF bağımlılık kapanışını paketler (yerel Qt
  kısmı ~270 MB → 61 MB; site-packages'taki PySide6 ~200 MB → 18 MB). ELF ayrıştırıcısı tekerlekteki 386 kütüphanenin
  hepsinde `llvm-readelf` ile aynı sonucu verdi.

APK: `KADRAJ-2.0.0-android-arm64-debug.apk` (deneme; hata ayıklama imzalı) **89 MB** (açılmış 159 MB). İçerik:
yerel kütüphaneler 86,8 MB sıkışık (`libpybundle.so` 28,6 MB = Python paketleri; `libffmpeg.so`/`libffprobe.so`
24,6 MB'şer; Qt ~61 MB açılmış), uygulama dosyaları (`private.tar`) 1,9 MB. Bildirim dosyası: `org.insibag.kadraj`,
hedef/derleme API 35, en düşük 28, yalnız `INTERNET`, `allowBackup=false`, `extractNativeLibs=true`. Qt'nin açılış
listesi (`libs.xml`) yalnız APK'da bulunan dosyaları içerir (Qt yükleyicisi ilk eksik dosyada açılışı durdurur).

## Plandan farklar

- Plan "emülatörde yt-dlp test videosu iner" diyordu; KVM olmadığından emülatör yok. Yerine: arm64 araçlar qemu ile,
  KADRAJ'ın Android yolu taklitle, yerel DASH yayınıyla uçtan uca sınandı. YouTube ve gerçek cihaz denemesi Erol'da
  (bulut makinesinden YouTube'a erişim yok).
- 2. adımın devir notu H.264 için MediaCodec ya da openh264 diyordu: x264'ün GitHub yansısı (mirror/x264) erişilebilir
  olduğu için masaüstüyle aynı kodlayıcı (libx264) kullanıldı; düzenleyicinin şablonları değişmeden çalışır.
- reportlab yerel tarifi 7. adım yerine bu adımda yazıldı (APK'nın ilk derlemesi için gerekiyordu).

## Bilinen sınırlar

- `.ass` altyazı dosyası uygulamanın yazı tiplerinde olmayan bir yazı tipi isterse Android'de libass varsayılan yazı
  tipine (Inter) düşer.
- FFmpeg'de donanım kodlayıcı (MediaCodec) yok; dışa aktarma CPU ile (libx264). Telefonda uzun videolar yavaş olabilir.
- `libffmpeg.so` ve `libffprobe.so` ayrı ayrı statik (her biri 25 MB). Boyut sorun olursa ffprobe yalnız çözümleyici
  ve demuxer'larla ayrı derlenebilir.
- Ayarlar'daki araç satırında FFmpeg sürümü açılışta boş, arka planda ölçülünce dolar (masaüstündeki gibi).
- Android'in dosya seçicisi `content://` adresi döndürebilir; Python/FFmpeg bunu doğrudan açamaz. İçe aktarmada
  dosyayı `QFile` ile uygulama klasörüne kopyalama ve dışa aktarmada "Paylaş" / "Galeriye kaydet" 6. adımda.
- Telefon düzeni 6. adımda: bu APK'da arayüz masaüstü düzeniyle açılır (telefonda sığmayan paneller olabilir).

## Doğrulama

- `packaging/android/test_native.sh` (qemu): 17/17 tamam, uçtan uca indirme dahil.
- Hedefli testler: device, components, settings, downloader, editor, crashlog: geçti.
- Tam takım ve `--selftest`: aşağıdaki "Test sonucu" satırı.
- Cihazda doğrulama bekliyor (Erol): uygulama açılıyor mu, YouTube indirmesi (QuickJS), düzenleyici dışa aktarması.

## Kararlar

- Karar: Android'de FFmpeg/ffprobe/QuickJS **statik** çalıştırılabilir, `lib<ad>.so` adıyla APK'nın yerel kütüphane
  klasöründe; kaynaklar GitHub'dan sabit commit'le; FFmpeg GPL v2+ (x264 için), TLS'siz (M14).
- Karar: Android'de JS çalıştırıcı QuickJS-ng; masaüstünde Deno önce, QuickJS yalnız `KADRAJ_QUICKJS` verilirse (M15).
- Karar: Android'de altyazı ve yazı süzgeçleri uygulamanın yazı tiplerini kullanır (Inter); fontconfig yok (M15).
- Karar: APK'ya yalnız kullanılan Qt parçaları girer (kendi PySide6 tarifimiz); Qt stili Fusion (M16).
- Karar: `allow_backup=False` (ayarlar ve kasa Google bulut yedeğine gitmesin); izinler en az: şimdilik yalnız
  `INTERNET` (M16).
- Karar: Android'de açılış hatası ekranda gösterilir ve `Belgeler/KADRAJ-hata.txt`'ye yazılır; yakalanmamış hata ve
  yerel çökme de oraya eklenir (M17).
- Karar: GPL karşılığı kaynak, araçların derlendiği kaynakların tam arşivi olarak APK ile aynı Release'e eklenir
  (7. adım) (M14).

## DEVİR

4. adım (ses kaydı, QtMultimedia) için:

- APK derlemesi: `packaging/android/build_native.sh` (bir kez) ve `packaging/android/build_apk.sh debug`. Bulut
  makinesinde sarmalayıcı `drun.sh` ve p4a indirme önbelleği (`KADRAJ_P4A_PREFETCH`) kullanılıyor; CI'da gerekmez.
- QtMultimedia zaten pakette (düzenleyicinin oynatıcısı için deploy aracı seçti): `--qt-libs` içinde Multimedia,
  `--load-local-libs` içinde FFmpeg ve Android medya eklentileri, `Qt6AndroidMultimedia.jar`. Kayıt için ek modül
  gerekmez; `QMediaCaptureSession` + `QAudioInput` + `QMediaRecorder` ya da `QAudioSource` (PCM → `wave` ile `.wav`).
- `RECORD_AUDIO` izni: `build_apk.sh`'teki `android.permissions` satırına eklenir; çalışma zamanında Qt'nin
  `QMicrophonePermission`'ı ile istenir.
- soundfile Android'de yok: `.wav` yazma/okuma için standart `wave` + numpy (selftest'in kayıt bölümü `soundfile`
  kullanıyor; Android'de selftest çalıştırılmıyor).
