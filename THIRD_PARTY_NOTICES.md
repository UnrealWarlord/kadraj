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

## Diğer Python bağımlılıkları

NumPy, Pillow, cryptography, keyring, psutil, soundfile, httpx, reportlab ve PyInstaller çalışma zamanı: her birinin kendi
(BSD/MIT/Apache benzeri) lisansı vardır; metinleri ilgili projelerin depolarında bulunur. Yazı tipleri: SIL Open Font
License 1.1 (`kadraj/ui/assets/fonts/` içinde lisans metinleri).
