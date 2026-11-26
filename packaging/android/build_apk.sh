#!/usr/bin/env bash
# KADRAJ · Android APK'sı (docs/steps/MOBILE_3.md): gerçek uygulama — PySide6 6.12 Android tekerlekleri,
# python-for-android (Qt önyüklemesi) ve yerel araçlar (build_native.sh: FFmpeg, ffprobe, QuickJS-ng).
#
# Kullanım (packaging/android/Dockerfile imajında, root olmayan kullanıcıyla):
#   packaging/android/build_native.sh             # önce; araç sürümleri değişmedikçe bir kez yeter
#   packaging/android/build_apk.sh [debug|release]
# Ortam:
#   KADRAJ_APK_WORK      çalışma klasörü (varsayılan packaging/android/build/apk-work; buildozer önbelleği burada)
#   KADRAJ_NATIVE        yerel araçlar (varsayılan packaging/android/build/native/arm64-v8a)
#   KADRAJ_WHEELS        PySide6/shiboken6 Android tekerlekleri (yoksa download.qt.io'dan indirilir, SHA-256 doğrulanır)
#   ANDROID_NDK_HOME     NDK (yoksa pyside6-android-deploy kendisi indirir, ~/.pyside6_android_deploy)
#   KADRAJ_P4A_PREFETCH  isteğe bağlı betik: python-for-android indirme önbelleğini doldurur (kısıtlı ağ için)
#   KADRAJ_APK_OUT       çıktı klasörü (varsayılan packaging/android/build)
# Çıktı: <çıktı>/KADRAJ-<sürüm>-android-arm64-debug.apk (ya da -unsigned.apk) ve .sha256. "release" imzasızdır; imzalama
#        CI'da apksigner ile yapılır (7. adım). Kimlik org.insibag.kadraj (karar M11).
set -euo pipefail

MODE=${1:-debug}
case "$MODE" in debug|release) ;; *) echo "kullanım: $0 [debug|release]" >&2; exit 2 ;; esac
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
WORK=${KADRAJ_APK_WORK:-$HERE/build/apk-work}
NATIVE=$(cd "${KADRAJ_NATIVE:-$HERE/build/native/arm64-v8a}" && pwd)
WHEELS=${KADRAJ_WHEELS:-$WORK/wheels}
APP="$WORK/app"
OUTDIR=${KADRAJ_APK_OUT:-$HERE/build}
PYSIDE_VERSION=6.12.0
# Deploy aracının bulduğu Qt modüllerine ek olarak paketlenen eklentiler: JPEG/WebP/GIF/ICO/SVG resimler ve SVG simgeler
QT_PLUGINS="imageformats/qjpeg,imageformats/qwebp,imageformats/qgif,imageformats/qico,imageformats/qsvg,iconengines/qsvgicon"
QT_BASE=https://download.qt.io/official_releases/QtForPython
VERSION=$(sed -nE 's/^__version__ *= *"([^"]+)".*/\1/p' "$ROOT/kadraj/version.py")
[ -n "$VERSION" ] || { echo "HATA: kadraj/version.py okunamadı" >&2; exit 1; }
for tool in libffmpeg.so libffprobe.so libqjs.so; do
  [ -x "$NATIVE/$tool" ] || { echo "HATA: $NATIVE/$tool yok (önce build_native.sh)" >&2; exit 1; }
done
log() { printf '\n==> %s\n' "$*"; }

# --- 1. Android tekerlekleri (sağlama doğrulamalı) -----------------------------------------------------------------
mkdir -p "$WHEELS"
WHEEL_PYSIDE="$WHEELS/pyside6-$PYSIDE_VERSION-$PYSIDE_VERSION-cp310-abi3-android_aarch64.whl"
WHEEL_SHIBOKEN="$WHEELS/shiboken6-$PYSIDE_VERSION-$PYSIDE_VERSION-cp310-abi3-android_aarch64.whl"
for pair in "pyside6|$WHEEL_PYSIDE" "shiboken6|$WHEEL_SHIBOKEN"; do
  IFS='|' read -r pkg wheel <<<"$pair"
  url="$QT_BASE/$pkg/$(basename "$wheel")"
  if [ ! -f "$wheel" ]; then
    log "indir: $(basename "$wheel")"
    curl -fL --retry 4 -o "$wheel.part" "$url"
    mv "$wheel.part" "$wheel"
  fi
  want=$(curl -fsL --retry 4 "$url.sha256" | awk '{print $1}')
  echo "$want  $wheel" | sha256sum -c - >/dev/null || { echo "HATA: $(basename "$wheel") sağlaması tutmadı" >&2; exit 1; }
done

# --- 2. Uygulama klasörü: yalnız çalışan kod ve varlıklar ----------------------------------------------------------
log "uygulama klasörü: $APP"
mkdir -p "$APP"
# buildozer önbelleği (.buildozer) ve bin/ korunur; kod her derlemede baştan kopyalanır
find "$APP" -mindepth 1 -maxdepth 1 ! -name .buildozer ! -name bin -exec rm -rf {} +
# buildozer yalnız source.include_exts uzantılarını paketler: uzantısız lisans dosyaları .txt adıyla girer
cp "$ROOT/main.py" "$ROOT/THIRD_PARTY_NOTICES.md" "$APP/"
cp "$ROOT/LICENSE" "$APP/LICENSE.txt"
tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' --exclude='requirements*.txt' -cf - kadraj | tar -C "$APP" -xf -
# Araçların lisans metinleri APK'ya girmek zorunda (FFmpeg GPL): build_native.sh çıktısının yanında olmalı
LIC="$APP/kadraj/ui/assets/licenses/android"
[ -d "$NATIVE/../licenses" ] && [ -f "$NATIVE/../VERSIONS.txt" ] || {
  echo "HATA: $NATIVE/../licenses ya da VERSIONS.txt yok (build_native.sh çıktısı eksik)" >&2; exit 1; }
mkdir -p "$LIC"
for f in "$NATIVE/../licenses/"*; do
  name=$(basename "$f"); name=${name%.TXT}; name=${name%.txt}
  case "$name" in *.md) ;; *) name="$name.txt" ;; esac
  cp "$f" "$LIC/$name"
done
cp "$NATIVE/../VERSIONS.txt" "$LIC/VERSIONS.txt"

# --- 3. pyside6-android-deploy: buildozer.spec + PySide6/shiboken6 tarifleri + Qt jar'ları -------------------------
log "pyside6-android-deploy --init"
SCRIPTS=$(python -c "import PySide6, pathlib; print(pathlib.Path(PySide6.__file__).parent / 'scripts')")
NDK_ARGS=()
[ -n "${ANDROID_NDK_HOME:-}" ] && NDK_ARGS+=(--ndk-path "$ANDROID_NDK_HOME")
[ -n "${ANDROID_SDK_ROOT:-}" ] && NDK_ARGS+=(--sdk-path "$ANDROID_SDK_ROOT")
(
  cd "$APP"
  python "$SCRIPTS/android_deploy.py" --init --keep-deployment-files -f \
    --name kadraj --wheel-pyside "$WHEEL_PYSIDE" --wheel-shiboken "$WHEEL_SHIBOKEN" "${NDK_ARGS[@]}"
)
RECIPES=$(sed -nE 's/^p4a\.local_recipes *= *(.*)$/\1/p' "$APP/buildozer.spec")
[ -d "$RECIPES" ] || { echo "HATA: deploy aracı tarif klasörünü üretmedi" >&2; exit 1; }
# Yerel tarifler (reportlab, inceltilmiş PySide6) üretilenlerin üzerine yazılır
cp -r "$HERE/recipes/." "$RECIPES/"
grep -q KADRAJ "$RECIPES/PySide6/__init__.py" || { echo "HATA: PySide6 tarifi değiştirilemedi" >&2; exit 1; }

# --- 4. buildozer.spec ayarları -----------------------------------------------------------------------------------
log "buildozer.spec"
python - "$APP/buildozer.spec" "$VERSION" "$NATIVE" "$RECIPES/PySide6/kadraj.json" "$WHEEL_PYSIDE" "$QT_PLUGINS" <<'PY'
import json, re, shlex, sys, zipfile
from pathlib import PurePosixPath
spec, version, native, recipe_config, wheel, plugins = sys.argv[1:7]
text = open(spec, encoding="utf-8").read()
# Deploy aracının seçtiği Qt modülleri ve açılışta yüklenen kütüphaneler → inceltilmiş PySide6 tarifinin ayarı
extra = dict(arg.split("=", 1) for arg in shlex.split(
    re.search(r"^p4a\.extra_args *= *(.*)$", text, re.M).group(1)) if arg.startswith("--") and "=" in arg)
listed = lambda key: [x for x in extra.get(key, "").split(",") if x]
config = {"wheel": wheel, "qt_libs": listed("--qt-libs"), "load_local_libs": listed("--load-local-libs"),
          "plugins": [x for x in plugins.split(",") if x]}
if not config["qt_libs"]:
    sys.exit("HATA: buildozer.spec'te --qt-libs yok")
open(recipe_config, "w", encoding="utf-8").write(json.dumps(config, indent=2) + "\n")
print("PySide6 tarifi:", json.dumps(config, ensure_ascii=False))
# Qt'nin yükleyicisi (QtLoader) bu listeyi açılışta "lib<ad>_arm64-v8a.so" adıyla yükler ve bulamadığı ilk dosyada
# uygulamayı başlatmaz. Qt'nin FFmpeg kütüphaneleri ise ekisiz (libavutil.so…): onlar listeden çıkarılır, FFmpeg
# eklentisi yüklenirken bağımlılık olarak aynı klasörden yüklenir.
qt_files = {PurePosixPath(n).name for n in zipfile.ZipFile(wheel).namelist()
            if n.startswith(("PySide6/Qt/lib/", "PySide6/Qt/plugins/"))}
loadable = [x for x in config["load_local_libs"] if f"lib{x}_arm64-v8a.so" in qt_files]
if not loadable:
    sys.exit("HATA: --load-local-libs boş kaldı")
print("açılışta yüklenecek:", ",".join(loadable), "| bağımlılık olarak:",
      ",".join(x for x in config["load_local_libs"] if x not in loadable) or "-")
text = re.sub(r"^(p4a\.extra_args *=.*--load-local-libs=)\S*", lambda m: m.group(1) + ",".join(loadable), text,
              count=1, flags=re.M)
settings = {
    "title": "KADRAJ",
    "package.name": "kadraj",
    "package.domain": "org.insibag",                                   # kimlik: org.insibag.kadraj (M11)
    "version": version,
    "source.dir": ".",
    "source.include_exts": "py,png,svg,ico,ttf,json,txt,md",
    "source.exclude_dirs": "deployment,bin,.buildozer,tests,docs,packaging",
    # numpy/Pillow tarifle derlenir; reportlab yerel tarifle (saf); geri kalanı saf Python (pip, --no-deps)
    "requirements": ",".join([
        "python3", "shiboken6", "PySide6", "numpy", "Pillow",
        "httpx", "httpcore", "h11", "anyio", "sniffio", "idna", "certifi",
        "charset-normalizer", "reportlab", "yt-dlp", "yt-dlp-ejs",
    ]),
    "orientation": "portrait, landscape, portrait-reverse, landscape-reverse",
    "fullscreen": "0",
    "icon.filename": "%(source.dir)s/kadraj/ui/assets/icon/kadraj-512.png",
    # İzinler en az (M16): indirme/AI için ağ; ses kaydı için mikrofon (çalışma zamanında sorulur, M18)
    "android.permissions": "android.permission.INTERNET, android.permission.RECORD_AUDIO",
    "android.archs": "arm64-v8a",
    "android.allow_backup": "False",                                   # ayarlar/kasa bulut yedeğine gitmesin
    "android.accept_sdk_license": "True",
    "android.add_libs_arm64_v8a": f"{native}/*.so",                    # FFmpeg/ffprobe/QuickJS: nativeLibraryDir
    "android.release_artifact": "apk",
    "android.debug_artifact": "apk",
}
lines = text.splitlines()
start = next(i for i, l in enumerate(lines) if l.strip() == "[app]")
end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\[.+\]\s*$", lines[i])), len(lines))
for key, value in settings.items():
    pat = re.compile(r"^\s*#?\s*" + re.escape(key) + r"\s*=")
    hit = next((i for i in range(start + 1, end) if pat.match(lines[i])), None)
    if hit is None:
        lines.insert(end, f"{key} = {value}")
        end += 1
    else:
        lines[hit] = f"{key} = {value}"
open(spec, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
grep -E '^(title|package\.|version|requirements|android\.(add_libs|permissions|api|minapi)|p4a\.(extra_args|local_recipes|commit))' "$APP/buildozer.spec"

# --- 5. Derleme ---------------------------------------------------------------------------------------------------
if [ -n "${KADRAJ_P4A_PREFETCH:-}" ]; then
  log "indirme önbelleği: $KADRAJ_P4A_PREFETCH"
  "$KADRAJ_P4A_PREFETCH" "$APP"
fi
log "buildozer android $MODE"
export PIP_NO_BINARY=charset-normalizer          # x86 derlemeli tekerlek değil, saf Python kaynağı
(cd "$APP" && python -m buildozer android "$MODE")

# --- 6. Çıktı -----------------------------------------------------------------------------------------------------
built=$(ls -t "$APP"/*.apk "$APP"/bin/*.apk 2>/dev/null | head -1 || true)     # bin_dir deploy aracının spec'inde: "."
[ -n "$built" ] || { echo "HATA: APK bulunamadı" >&2; exit 1; }
suffix=$([ "$MODE" = debug ] && echo "-debug" || echo "-unsigned")
out="$OUTDIR/KADRAJ-$VERSION-android-arm64$suffix.apk"
mkdir -p "$OUTDIR"
cp "$built" "$out"
(cd "$OUTDIR" && sha256sum "$(basename "$out")" >"$(basename "$out").sha256")
log "APK: $out ($(du -h "$out" | cut -f1))"
