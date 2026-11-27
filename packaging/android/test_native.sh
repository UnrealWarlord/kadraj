#!/usr/bin/env bash
# KADRAJ · Android yerel araçlarını (build_native.sh çıktısı: statik arm64 ffmpeg / ffprobe / qjs) Linux'ta
# qemu-aarch64 ile sınar. Emülatör ya da telefon gerekmez; araçlar statik olduğu için Android kütüphanesi istemez.
#
# Kullanım (depo kökünden):  packaging/android/test_native.sh [ARAÇ_KLASÖRÜ]
#   ARAÇ_KLASÖRÜ varsayılan: packaging/android/build/native/arm64-v8a
# Gerekenler: qemu-aarch64-static (ya da qemu-aarch64); AV1 çözme sınaması için sistemde AV1 kodlayabilen bir ffmpeg
# (yoksa o sınama atlanır); yt-dlp kuruluysa QuickJS'in yt-dlp tarafından tanınması da sınanır.
set -uo pipefail

DIR=$(cd "${1:-packaging/android/build/native/arm64-v8a}" && pwd)
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
FONTS="$ROOT/kadraj/ui/assets/fonts"
QEMU=${QEMU:-$(command -v qemu-aarch64-static || command -v qemu-aarch64 || true)}
[ -n "$QEMU" ] || { echo "qemu-aarch64 yok" >&2; exit 2; }
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
FAIL=0

ff() { "$QEMU" "$DIR/libffmpeg.so" -hide_banner -nostdin -v error -y "$@"; }
fp() { "$QEMU" "$DIR/libffprobe.so" -v error "$@"; }
qjs() { "$QEMU" "$DIR/libqjs.so" "$@"; }
ok() { echo "[ok] $1"; }
bad() { echo "[HATA] $1"; FAIL=1; }
check() { local name=$1; shift; if "$@" >"$T/out.log" 2>&1; then ok "$name"; else bad "$name"; tail -5 "$T/out.log"; fi; }
has_all() {          # has_all <liste-komutu> <ad>...: ffmpeg -encoders vb. çıktısında hepsi var mı
  local list=$1; shift
  ff "-$list" >"$T/$list.txt" 2>/dev/null
  local missing=()
  for n in "$@"; do grep -qE "^ [A-Z.]{6} $n( |$)|^ [A-Z.]{3,5} $n( |$)|^ +[A-Z.]+ +$n " "$T/$list.txt" || missing+=("$n"); done
  [ ${#missing[@]} -eq 0 ] || { echo "  eksik: ${missing[*]}"; return 1; }
}
same_frame() { cmp -s "$1" "$2"; }

echo "Araçlar: $DIR"
echo "Sürüm: $("$QEMU" "$DIR/libffmpeg.so" -version | head -1)"

check "kodlayıcılar (libx264, aac, prores_ks, png, mjpeg, gif, pcm_s24le)" \
  has_all encoders libx264 aac prores_ks png mjpeg gif pcm_s24le pcm_s16le
check "çözücüler (libdav1d, h264, hevc, vp9, vp8, aac, opus, mp3, flac)" \
  has_all decoders libdav1d h264 hevc vp9 vp8 aac opus mp3 flac
check "süzgeçler (subtitles, ass, drawtext, loudnorm, chromakey, lut3d, sidechaincompress, silencedetect, atempo, xfade)" \
  has_all filters subtitles ass drawtext loudnorm chromakey lut3d sidechaincompress silencedetect atempo xfade overlay
check "lavfi girişi" has_all devices lavfi

check "H.264 + AAC kodlama (mp4)" ff -f lavfi -i testsrc2=s=320x240:r=25:d=2 -f lavfi -i sine=f=440:d=2 \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest "$T/a.mp4"
check "ffprobe akışları (h264, aac)" bash -c "[ \"\$($(printf '%q ' "$QEMU" "$DIR/libffprobe.so") -v error -show_entries stream=codec_name -of csv=p=0 '$T/a.mp4' | tr '\n' ' ')\" = 'h264 aac ' ]"
check "ffprobe JSON" bash -c "$(printf '%q ' "$QEMU" "$DIR/libffprobe.so") -v error -print_format json -show_format -show_streams '$T/a.mp4' | grep -q '\"format_name\"'"

check "görüntü + ses birleştirme (-c copy)" bash -c "
  $(printf '%q ' "$QEMU" "$DIR/libffmpeg.so") -v error -y -nostdin -i '$T/a.mp4' -an -c copy '$T/v.mp4' &&
  $(printf '%q ' "$QEMU" "$DIR/libffmpeg.so") -v error -y -nostdin -i '$T/a.mp4' -vn -c copy '$T/a.m4a' &&
  $(printf '%q ' "$QEMU" "$DIR/libffmpeg.so") -v error -y -nostdin -i '$T/v.mp4' -i '$T/a.m4a' -c copy -map 0:v -map 1:a '$T/m.mp4'"

HOST_ENCODERS=$(command -v ffmpeg >/dev/null && ffmpeg -hide_banner -encoders 2>/dev/null || true)
enc=$(grep -oE "libsvtav1|libaom-av1" <<<"$HOST_ENCODERS" | head -1 || true)
if [ -n "$enc" ]; then
  ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=s=320x240:r=25:d=1 -c:v "$enc" -pix_fmt yuv420p "$T/av1.mkv" 2>/dev/null
  check "AV1 çözme (dav1d) ve H.264'e çevirme" ff -i "$T/av1.mkv" -c:v libx264 -preset ultrafast "$T/av1-to-h264.mp4"
else
  echo "[atlandı] AV1 çözme: sistemde AV1 kodlayıcılı ffmpeg yok"
fi

printf '1\n00:00:00,000 --> 00:00:02,000\nMerhaba dünya ĞÜŞİÖÇ\n' >"$T/s.srt"
check "altyazı yakma (libass, uygulamanın yazı tipleri, fontconfig yok)" ff -i "$T/a.mp4" \
  -vf "subtitles=filename=$T/s.srt:fontsdir=$FONTS:force_style=FontName=Inter" -c:v libx264 -preset ultrafast -an "$T/sub.mp4"
ff -ss 0.5 -i "$T/a.mp4" -frames:v 1 "$T/plain.png" && ff -ss 0.5 -i "$T/sub.mp4" -frames:v 1 "$T/subbed.png"
if same_frame "$T/plain.png" "$T/subbed.png"; then bad "altyazı karede görünüyor"; else ok "altyazı karede görünüyor"; fi
check "drawtext (yazı tipi dosyasıyla)" ff -i "$T/a.mp4" \
  -vf "drawtext=text='KADRAJ':fontfile=$FONTS/Inter-SemiBold.ttf:fontsize=32:fontcolor=white:x=10:y=10" \
  -c:v libx264 -preset ultrafast -an "$T/txt.mp4"
check "loudnorm + atempo" ff -i "$T/a.mp4" -af "loudnorm=I=-16,atempo=1.5" -vn "$T/ln.wav"

QHELP=$(qjs --help 2>&1 | head -1)
if [[ "$QHELP" =~ ^QuickJS-ng\ version\ ([0-9.]+) ]]; then ok "QuickJS-ng sürümü: ${BASH_REMATCH[1]}"; else bad "QuickJS --help: $QHELP"; fi
cat >"$T/t.js" <<'EOF'
class A { #x = 2n ** 64n; get x() { return this.#x; } }
const s = "abc".replace(/(?<=a)b/, "B");
const r = [3, 1, 2].toSorted().at(-1);
const m = new Map([[1, "x"]]); const w = Object.groupBy([1, 2, 3], (n) => (n % 2 ? "tek" : "çift"));
console.log(String(new A().x), s, r, JSON.stringify({ a: [1, { b: null }] }), [..."ğüş"].length, m.get(1), w.tek.length);
EOF
check "QuickJS betik çalıştırma" bash -c "[ \"\$($(printf '%q ' "$QEMU" "$DIR/libqjs.so") --script '$T/t.js')\" = '18446744073709551616 aBc 3 {\"a\":[1,{\"b\":null}]} 3 x 2' ]"

if python3 -c "import yt_dlp" 2>/dev/null; then
  printf '#!/bin/sh\nexec %q %q "$@"\n' "$QEMU" "$DIR/libqjs.so" >"$T/qjs"
  chmod +x "$T/qjs"
  check "yt-dlp QuickJS-ng'yi tanıyor" python3 - "$T/qjs" <<'EOF'
import sys
from yt_dlp.utils._jsruntime import QuickJsRuntime
info = QuickJsRuntime(sys.argv[1]).info
print(info)
assert info is not None and info.name == "quickjs-ng" and info.supported
EOF
else
  echo "[atlandı] yt-dlp kurulu değil"
fi

# Uçtan uca: KADRAJ'ın Android yolu (taklit) + bu araçlar. Ayrı görüntü/ses akışlı yerel bir DASH yayını (YouTube'un
# biçimleri gibi) yt-dlp ile indirilir, FFmpeg'le birleştirilir, ffprobe ile doğrulanır (main.py --selftest-download).
if python3 -c "import yt_dlp, PySide6" 2>/dev/null; then
  LIBS="$T/android-lib"; mkdir -p "$LIBS" "$T/www"
  for n in ffmpeg ffprobe qjs; do
    printf '#!/bin/sh\nexec %q %q "$@"\n' "$QEMU" "$DIR/lib$n.so" >"$LIBS/lib$n.so"; chmod +x "$LIBS/lib$n.so"
  done
  ff -f lavfi -i testsrc2=s=640x360:r=25:d=4 -f lavfi -i sine=f=440:d=4 -map 0:v -map 1:a \
    -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -f dash -adaptation_sets "id=0,streams=v id=1,streams=a" \
    "$T/www/test.mpd"
  PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
  python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$T/www" >/dev/null 2>&1 &
  SERVER=$!
  sleep 1
  check "KADRAJ indirme yolu: Android taklidi, yerel DASH → yt-dlp → FFmpeg birleştirme → ffprobe" \
    env KADRAJ_PLATFORM=android KADRAJ_ANDROID_LIBDIR="$LIBS" KADRAJ_ANDROID_PRIVATE="$T/private" HOME="$T/home" \
    QT_QPA_PLATFORM=offscreen python3 "$ROOT/main.py" --selftest-download "http://127.0.0.1:$PORT/test.mpd" "$T/indirilen"
  grep -E "bileşenler|İNDİRME|HATA" "$T/out.log" | cut -c1-200 | sed 's/^/  /'
  kill "$SERVER" 2>/dev/null
else
  echo "[atlandı] KADRAJ indirme yolu: yt-dlp ya da PySide6 kurulu değil"
fi

echo "-----"
[ $FAIL -eq 0 ] && echo "ANDROID ARAÇLARI TAMAM" || echo "ANDROID ARAÇLARI: HATA VAR"
exit $FAIL
