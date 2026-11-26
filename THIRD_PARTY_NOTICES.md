# KADRAJ — Üçüncü taraf bileşenler ve lisansları

KADRAJ kendi kodu MIT lisanslıdır (bkz. `LICENSE`). Kurulum paketi aşağıdaki üçüncü taraf bileşenleri **ayrı programlar
ya da kütüphaneler olarak** içerir; her birinin kendi lisansı geçerlidir. KADRAJ, FFmpeg ve Deno'yu ayrı süreçler olarak
çalıştırır (kendi koduna bağlamaz), yt-dlp'yi ise Python paketi olarak içe aktarır.

Açılışta bileşen güncelleyicisi (Ayarlar → Araçlar) yt-dlp, FFmpeg ve Deno'yu **yalnızca** aşağıdaki resmî kaynaklardan,
HTTPS üzerinden ve SHA-256 sağlaması doğrulandıktan sonra indirir; `~/.kadraj/tools/` altına koyar.

## FFmpeg (ffmpeg, ffprobe)

- Kullanım: video/ses işleme, dışa aktarma, indirilen akışların birleştirilmesi.
- Lisans: **GNU GPL v3 ya da sonrası** — pakete giren derleme `libx264`/`libx265` içerdiği için GPL'dir (LGPL derlemelerde bu
  kodlayıcılar yoktur; düzenleyicinin CPU ile H.264/H.265 dışa aktarması ve vekil dosyalar bunlara dayanır).
  FFmpeg ayrı bir çalıştırılabilir dosyadır; KADRAJ onunla yalnızca komut satırı üzerinden konuşur.
- Derleme: BtbN FFmpeg-Builds, FFmpeg sürüm dalı (şu an 9.0) — <https://github.com/BtbN/FFmpeg-Builds>
- **Kaynak kod**: <https://ffmpeg.org/download.html> (FFmpeg) ve aynı depodaki derleme betikleri ile kullanılan kitaplıkların
  kaynakları. Kaynak kodu istemek için KADRAJ deposunda bir konu açabilirsiniz.
- Lisans metni: <https://www.gnu.org/licenses/gpl-3.0.html> · FFmpeg yasal bilgi: <https://ffmpeg.org/legal.html>

## Deno

- Kullanım: yt-dlp'nin YouTube doğrulama (JS) çözücüsünü çalıştırması. Yalnızca yt-dlp tarafından, yerel betiklerle çağrılır.
- Lisans: MIT — <https://github.com/denoland/deno> (kaynak ve lisans metni orada)

## yt-dlp ve yt-dlp-ejs

- Kullanım: video/ses indirme. KADRAJ pip paketini ve (güncellemelerde) resmî `yt-dlp` zipapp'ını içe aktarır.
- Lisans: The Unlicense (kamu malı benzeri) — <https://github.com/yt-dlp/yt-dlp>
- yt-dlp-ejs (YouTube çözücü betikleri): Unlicense; içinde MIT (astring) ve ISC (meriyah) bileşenleri bulunur —
  <https://github.com/yt-dlp/ejs>

## PortAudio (yalnız Linux paketi)

- Kullanım: mikrofon kaydı (`sounddevice` aracılığıyla). Windows'ta `sounddevice` tekerleği kendi PortAudio'sunu getirir.
- Lisans: MIT benzeri — <http://www.portaudio.com/license.html>

## xcb-util-cursor / libxcb-cursor (yalnız Linux paketi)

- Kullanım: X11 oturumunda Qt'nin `xcb` penceresi için imleç desteği.
- Lisans: MIT — <https://gitlab.freedesktop.org/xorg/lib/libxcb-cursor>

## Qt 6 / PySide6

- Kullanım: arayüz. Lisans: LGPL v3 (Qt for Python) — <https://doc.qt.io/qtforpython-6/licenses.html>. Kütüphaneler paket içinde
  ayrı dosyalar olarak durur; değiştirilmiş bir sürümle değiştirilebilir.

## Android uygulaması (APK)

Android paketi masaüstündeki bileşenlerin yerine şunları içerir. Lisans metinleri APK'nın içinde de bulunur
(`LICENSE.txt`, bu dosya ve `kadraj/ui/assets/licenses/android/`). Araçlar güncellenmez, yeni APK ile gelir.

### FFmpeg (ffmpeg, ffprobe) — Android derlemesi

- Kullanım: masaüstündeki gibi; APK'nın yerel kütüphane klasöründe `libffmpeg.so` / `libffprobe.so` adlı **ayrı
  çalıştırılabilir dosyalar** olarak durur, KADRAJ onları yalnızca komut satırı üzerinden çalıştırır.
- Lisans: **GNU GPL v2 ya da sonrası** (`--enable-gpl`; libx264 içerdiği için). Statik olarak içine bağlı kitaplıklar:
  x264 (GPL v2+), dav1d (BSD 2 maddeli), libass (ISC), FreeType (FreeType Lisansı), FriBidi (LGPL v2.1+), HarfBuzz
  (MIT benzeri "Old MIT"), zlib (zlib lisansı, Android NDK'den).
- Sürümler: FFmpeg n9.1, x264 stable, dav1d 1.5.5, FreeType 2.15.0, FriBidi 1.0.18, HarfBuzz 15.2.0, libass 0.17.5;
  her birinin GitHub deposu ve commit'i `packaging/android/build_native.sh` ile APK'daki `VERSIONS.txt`'de yazılıdır.
- **Kaynak kod**: derlemede kullanılan kaynakların tam arşivi ve derleme betiği (`android-native-sources.tar`) APK ile
  aynı GitHub Release'inde yayımlanır. Aynı derleme `packaging/android/build_native.sh` ile yeniden üretilebilir.

### QuickJS-ng

- Kullanım: Android'de yt-dlp'nin YouTube doğrulama (JS) çözücüsünü çalıştırması (masaüstündeki Deno'nun yerine).
  `libqjs.so` adlı ayrı çalıştırılabilir dosya. Sürüm 0.18.0.
- Lisans: MIT — <https://github.com/quickjs-ng/quickjs>

### Qt 6 / PySide6 (Android)

- Qt for Python'ın resmî Android tekerleği (6.12.0): LGPL v3. Yalnız kullanılan modüller pakete girer (Core, Gui,
  Widgets, Svg, Multimedia, Network, Concurrent ve resim eklentileri). Qt Multimedia'nın FFmpeg arka ucu için Qt'nin
  derlediği FFmpeg paylaşımlı kütüphaneleri (`libavcodec.so` vb., LGPL) de tekerlekle gelir.
- Kaynak: <https://code.qt.io/cgit/pyside/pyside-setup.git/> ve <https://download.qt.io/official_releases/QtForPython/>

### Python ve paketleme

- CPython 3.14 (PSF lisansı), python-for-android önyükleme kodu (MIT), OpenSSL (Apache 2.0), SQLite (kamu malı),
  libffi (MIT), libjpeg-turbo (IJG/BSD), libpng (libpng lisansı) ve Pillow için FreeType (FreeType Lisansı).
- certifi (sertifika listesi): MPL 2.0 — <https://github.com/certifi/python-certifi>

## Diğer Python bağımlılıkları

NumPy, Pillow, cryptography, keyring, psutil, soundfile, httpx, reportlab ve PyInstaller çalışma zamanı: her birinin kendi
(BSD/MIT/Apache benzeri) lisansı vardır; metinleri ilgili projelerin depolarında bulunur. Yazı tipleri: SIL Open Font
License 1.1 (`kadraj/ui/assets/fonts/` içinde lisans metinleri).
