# Mobil 2. adım · Platform katmanı: cihaz algılama, Android klasörleri, araç kaynakları

Tarih: 2026-11-26 · Dal: `mobile` · Ortam: Claude'un bulut makinesinde Docker (`kadraj-test` imajı, ekransız Qt; M8).

## Sonuç

- **`kadraj/device.py` (yeni):** `is_android()`, `android_api_level()`, `form_factor()` → `desktop` | `phone` |
  `tablet`, `classify(dp)`, `android_private_dir()`, `native_library_dir()`, `libdir_from_maps()`. Qt'ye bağlı değil,
  ağa çıkmaz. Telefon/tablet ayrımı ekranın kısa kenarı ≥ 600 dp (Android'in `sw600dp` eşiği); ölçü bilinmezse
  telefon sayılır. Geliştirme için taklit: `KADRAJ_PLATFORM=android`, `KADRAJ_FORM_FACTOR=phone|tablet|desktop`,
  `KADRAJ_ANDROID_PRIVATE`, `KADRAJ_ANDROID_LIBDIR`.
- **`kadraj/paths.py`:** `home_dir()` Android'de `<getFilesDir()>/.kadraj`. Yeni `user_dir()` ve
  `prepare_android_environment(documents)`: `HOME` → Qt'nin Documents klasörü (uygulamaya ayrılmış dış depolama),
  yazılamazsa iç klasör; `TMPDIR` → `<iç klasör>/tmp`. İç klasör `KADRAJ_ANDROID_PRIVATE`'e sabitlenir, böylece
  `HOME` değişse de ayarlar/kasa/veritabanı yerinden oynamaz; `tempfile`'ın önbelleği sıfırlanır.
- **`kadraj/ui/shell/app.py`:** `create_app` Android'de QApplication kurulur kurulmaz (ayarlar ve bağlam yüklenmeden
  önce) `prepare_android_environment`'ı çağırır. Masaüstünde hiçbir şey değişmez.
- **`kadraj/core/components.py`:** yeni kaynak `android`. Android'de ffmpeg/ffprobe **yalnız** ortam değişkeninden ya
  da `nativeLibraryDir/libffmpeg.so`, `libffprobe.so`'dan bulunur; `~/.kadraj/tools` ve PATH ikililer için denenmez
  (W^X). yt-dlp'nin güncellenmiş zipapp'ı Android'de de kullanılır. Deno Android'de yok (QuickJS 3. adımda).
- **`kadraj/core/updater.py`:** Android'de masaüstü platform tablosu yok (`_platform()` → `None`) ama güncelleyici
  atlanmaz: yalnız yt-dlp denetlenir/indirilir. Deno ve FFmpeg hiç sorulmaz. Masaüstü davranışı aynı.
- **Ayarlar:** kaynak etiketi "Uygulamada" (`SOURCE_ANDROID`); Android'de güncelleme notu "FFmpeg uygulamayla gelir…
  açılışta yalnız yt-dlp denetlenir", "Onar" açıklaması "yalnız yt-dlp" der (Basit ve Legend sayfaları).
- **Testler:** `tests/core/test_device.py` (26 durum: algılama, API düzeyi, sw600dp eşiği, zorlama, `/proc/self/maps`
  ayrıştırma, Android klasörleri, `HOME` taşınınca `home_dir()`'in sabit kalması, `tempfile` önbelleği, araçların
  yalnız APK'dan bulunması, ortam değişkeninin kazanması, güncellenmiş yt-dlp, güncelleyicinin yalnız yt-dlp'yi
  sorması, Ayarlar notları) ve `tests/settings/test_pages.py`'de Android araç kartı testi. Gerçek cihaz gerekmez:
  Android ortamı ortam değişkenleriyle taklit edilir.

## Android'de klasörler (M12)

| Ne | Nerede | Not |
|---|---|---|
| Ayarlar, kasa, veritabanı, güncellenen yt-dlp (`home_dir()`) | `/data/user/0/org.insibag.kadraj/files/.kadraj` | Uygulamanın iç klasörü; başka uygulamalar okuyamaz |
| Kullanıcı dosyaları (`Path.home()`: projeler, indirmeler, kayıtlar) | Qt Documents: `/storage/emulated/0/Android/data/org.insibag.kadraj/files/Documents` (beklenen) | İzin gerekmez. Uygulama kaldırılınca silinir; dışa aktarma için "Paylaş" / "Galeriye kaydet" 6. adımda |
| Geçici dosyalar (`TMPDIR`) | `<iç klasör>/tmp` | Önbellek klasörü değil: sistem uzun render sırasında silebilir |
| FFmpeg / ffprobe | `nativeLibraryDir/libffmpeg.so`, `libffprobe.so` | 3. adımda APK'ya gömülecek (`extractNativeLibs=true`) |

Yollar kodda sabit değildir; çalışma anında python-for-android'in `ANDROID_PRIVATE` değişkeninden (Qt önyüklemesi
`getFilesDir()` koyar), `/proc/self/maps`'ten ve Qt'nin `QStandardPaths`'inden okunur. Tablodaki Documents yolu
deneme APK'sının bilgi ekranıyla cihazda doğrulanacak.

## Plandan farklar

- Plan modülü `kadraj/platform.py` diye anıyordu; standart kütüphanedeki `platform` modülünü gölgelememek için adı
  `kadraj/device.py` oldu.
- Android'de kullanıcı dosyaları için ayrı yol fonksiyonları yazmak yerine `HOME` uygulamanın Documents klasörüne
  çevrildi: kodun her yerindeki `Path.home()` varsayılanları (proje kökü, indirme klasörü, kayıtlar) değişmeden doğru
  yere yazar.

## Doğrulama

- Hedefli testler (device, components, updater, settings): geçti.
- Tam takım ve `--selftest`: aşağıdaki "Test sonucu" satırı.
- Cihazda doğrulama bekliyor: deneme APK'sının bilgi ekranı (Erol'un telefonu/tableti). Özellikle `lib = …` satırı
  (`base.apk!` içermemeli), `ANDROID_PRIVATE` ve Qt Documents yolu.

Test sonucu: tam takım **1129 geçti** (1102 eski + 27 yeni; 163 s), `python main.py --selftest` → **SELFTEST TAMAM** (ekransız Qt, ağ kapalı).

## Kararlar

- Karar: Modülün adı `kadraj/device.py` (stdlib `platform` ile çakışmasın).
- Karar: Android uygulama kimliği `org.insibag.kadraj` (M11). İlk yayından sonra değişmez; testlerde ve belgelerde bu ad
  kullanılır.
- Karar: Android'de `HOME` = Qt Documents (uygulamaya ayrılmış dış depolama), yazılamazsa iç klasör; ayarlar/kasa/db
  iç klasörde (M12).
- Karar: Geçici dosyalar iç klasörde `tmp`, önbellek klasöründe değil (M12).
- Karar: Android'de ikililer yalnız ortam değişkeni ya da APK'nın kütüphane klasöründen; güncelleyici ve "Onar" yalnız
  yt-dlp (M13). Ayarlar'da bu açıkça yazar.
- Karar: Ekran ölçüsü bilinmezse telefon düzeni (güvenli taraf); `KADRAJ_FORM_FACTOR` ile zorlanabilir.

## DEVİR

3. adım (FFmpeg + QuickJS + gerçek uygulama APK'sı) için:

- FFmpeg/ffprobe'u Android arm64 için derle (NDK r28c, API 28) ve `libffmpeg.so` / `libffprobe.so` adıyla
  `jniLibs/arm64-v8a`'ya koy (buildozer `android.add_libs_arm64_v8a`). `components.find("ffmpeg")` Android'de
  oradan bulur; `ffmpeg_location()` yt-dlp'ye klasörü verir.
- Kaynak erişimi (bu oturumda): ffmpeg.org kapalı → GitHub'daki FFmpeg/FFmpeg etiketi. x264 (code.videolan.org)
  kapalı → H.264 için Android'in `h264_mediacodec` kodlayıcısı ya da GitHub'daki openh264 değerlendirilecek.
  Lisans `THIRD_PARTY_NOTICES.md`'ye.
- QuickJS-ng (GitHub quickjs-ng/quickjs) → `libqjs.so`; `components.py`'ye `quickjs` adı ve `ANDROID_LIBS` satırı,
  yt-dlp'ye çalıştırıcıyı açıkça etkinleştir (yalnız deno varsayılan açık).
- Gerçek uygulama APK'sı: `buildozer.spec`'te kimlik `org.insibag.kadraj` (M11), `orientation` (portrait değil, tüm
  yönler), `requirements` (bkz. MOBILE_1 bağımlılık matrisi), izinler (INTERNET; RECORD_AUDIO 4. adımda).
- Bilgi ekranının sonucu gelirse `device.py`/`paths.py` varsayımlarını ona göre düzelt.
