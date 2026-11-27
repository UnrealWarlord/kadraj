#!/usr/bin/env bash
# KADRAJ Android paketini üretir (docs/steps/MOBILE_7.md):
#   dist/KADRAJ-<sürüm>-android-arm64-unsigned.apk     (+ .sha256)  derleme çıktısı, imzasız (kurulamaz)
#   dist/KADRAJ-<sürüm>-android-arm64.apk              (+ .sha256)  "sign" ile imzalanmış hâli (release'e giden bu)
#   dist/KADRAJ-<sürüm>-android-native-sources.tar     (+ .sha256)  APK'daki FFmpeg/x264/... kaynakları (GPL karşılığı)
# <sürüm>, kadraj/version.py içinden okunur. Gereken: Docker ve ağ (imzalama ağsız konteynerde yapılır).
#
# Kullanım:
#   bash packaging/build_android.sh [release|debug]
#       derleme imajı (packaging/android/Dockerfile) → Android NDK r28c (sabit SHA-256 ile indirilir) → yerel araçlar
#       (packaging/android/build_native.sh; önbellekte varsa atlanır) → APK (packaging/android/build_apk.sh).
#       "debug" hata ayıklama imzalı deneme APK'sı üretir (…-android-arm64-debug.apk).
#   bash packaging/build_android.sh sign
#       dist/…-unsigned.apk'yı imzalar (packaging/android/sign_apk.sh), imzasız dosyayı siler. Ortam:
#         KADRAJ_KEYSTORE           anahtar deposu dosyası (.jks / .p12)
#         KADRAJ_KEYSTORE_PASSWORD  deponun parolası
#         KADRAJ_KEY_ALIAS          anahtarın adı
#         KADRAJ_KEY_PASSWORD       anahtarın parolası (boşsa deponunki; PKCS12'de ikisi aynıdır)
#       Parolalar konteynere yalnız değişken adıyla geçer; komut satırında ve günlükte görünmez.
# Ortam (hepsi isteğe bağlı):
#   KADRAJ_ANDROID_CACHE    önbellek klasörü (varsayılan build/android): ndk/, native/ (yerel araçlar), home/ (Android
#                           SDK, Gradle), apk-work/ (buildozer), wheels/ (PySide6 Android tekerlekleri)
#   KADRAJ_DOCKER_ARGS      docker run'a eklenecek argümanlar, boşlukla ayrılmış (örn. "--network host")
#   KADRAJ_DOCKER_ENV_FILE  docker run --env-file dosyası (örn. vekil sunucu ayarları)
#   KADRAJ_DOCKER_BUILD_ARGS docker build'e eklenecek argümanlar (örn. vekil sunucunun CA'sı için Dockerfile'daki
#                           --secret id=extra_ca,src=…)
set -euo pipefail

die() { echo "HATA: $*" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }

MODE=${1:-release}
case "$MODE" in release|debug|sign) ;; *) die "kullanım: bash packaging/build_android.sh [release|debug|sign]" ;; esac
[ $# -le 1 ] || die "kullanım: bash packaging/build_android.sh [release|debug|sign]"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null 2>&1 || die "Docker bulunamadı; kurup tekrar deneyin."
VERSION="$(sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' kadraj/version.py | head -n1)"
[ -n "$VERSION" ] || die "kadraj/version.py içinde __version__ bulunamadı."

IMAGE=kadraj-android-build
NAME="KADRAJ-${VERSION}-android-arm64"
SOURCES="KADRAJ-${VERSION}-android-native-sources.tar"
OUT="$ROOT/dist"
CACHE="${KADRAJ_ANDROID_CACHE:-$ROOT/build/android}"
# Android NDK: PySide6 6.12'nin pyside6-android-deploy aracının kendi indirdiği sürüm (docs/steps/MOBILE_1.md)
NDK_VERSION=r28c
NDK_REVISION=28.2.13676358
NDK_SHA256=dfb20d396df28ca02a8c708314b814a4d961dc9074f9a161932746f815aa552f
NDK_URL="https://dl.google.com/android/repository/android-ndk-${NDK_VERSION}-linux.zip"

mkdir -p "$CACHE"/{ndk,native,home,apk-work,wheels} "$OUT"
CACHE="$(cd "$CACHE" && pwd)"
# Derleme konteyneri imajdaki "builder" kullanıcısıyla (uid 1000) çalışır; buildozer root istemez. Başka uid'li
# makinede (örn. GitHub çalıştırıcısı, uid 1001) bağlanan klasörlere yazabilsin. Yalnız bu kullanıcının kendi
# dosyalarına dokunulur: native/ önbellekten (çalıştırıcının kullanıcısıyla) geri yüklenmiş olabilir; konteynerin
# daha önce yazdıkları zaten onundur.
if [ "$MODE" != sign ] && [ "$(id -u)" != 1000 ]; then
    for dir in "$CACHE" "$CACHE"/{ndk,native,home,apk-work,wheels} "$OUT"; do
        if [ -O "$dir" ]; then chmod a+rwx "$dir"; fi
    done
    find "$CACHE/native" -user "$(id -u)" -exec chmod a+rwX {} +
fi

DOCKER_ARGS=()
# shellcheck disable=SC2206   # boşlukla ayrılmış argüman listesi bilerek bölünür
[ -n "${KADRAJ_DOCKER_ARGS:-}" ] && DOCKER_ARGS+=(${KADRAJ_DOCKER_ARGS})
[ -n "${KADRAJ_DOCKER_ENV_FILE:-}" ] && DOCKER_ARGS+=(--env-file "$KADRAJ_DOCKER_ENV_FILE")
BUILD_ARGS=()
# shellcheck disable=SC2206
[ -n "${KADRAJ_DOCKER_BUILD_ARGS:-}" ] && BUILD_ARGS+=(${KADRAJ_DOCKER_BUILD_ARGS})

# ---------------------------------------------------------------------------------------------------------------
if [ "$MODE" = sign ]; then
    UNSIGNED="$OUT/$NAME-unsigned.apk"
    [ -f "$UNSIGNED" ] || die "$UNSIGNED yok (önce: bash packaging/build_android.sh release)"
    [ -n "${KADRAJ_KEYSTORE:-}" ] && [ -f "$KADRAJ_KEYSTORE" ] || die "KADRAJ_KEYSTORE bir anahtar deposu dosyası olmalı"
    [ -n "${KADRAJ_KEYSTORE_PASSWORD:-}" ] || die "KADRAJ_KEYSTORE_PASSWORD boş"
    [ -n "${KADRAJ_KEY_ALIAS:-}" ] || die "KADRAJ_KEY_ALIAS boş"
    export KADRAJ_KEYSTORE_PASSWORD KADRAJ_KEY_ALIAS
    export KADRAJ_KEY_PASSWORD="${KADRAJ_KEY_PASSWORD:-$KADRAJ_KEYSTORE_PASSWORD}"
    KEYSTORE="$(cd "$(dirname "$KADRAJ_KEYSTORE")" && pwd)/$(basename "$KADRAJ_KEYSTORE")"
    SDK=/home/builder/.buildozer/android/platform/android-sdk       # derlemede buildozer'ın indirdiği SDK
    [ -d "$CACHE/home/.buildozer/android/platform/android-sdk/build-tools" ] \
        || die "Android build-tools yok: $CACHE/home/.buildozer (önce: bash packaging/build_android.sh release)"
    log "APK imzalanıyor (ağsız konteyner)"
    # Anahtar dosyası çalıştıranın kullanıcısına ait ve yalnız onun okuyabileceği kipte olabilir: konteyner aynı
    # uid ile çalışır. Ev klasörü (SDK) salt okunur bağlanır.
    docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp -e ANDROID_HOME="$SDK" \
        -v "$ROOT":/src:ro -v "$CACHE/home":/home/builder:ro -v "$OUT":/out -v "$KEYSTORE":/run/kadraj.keystore:ro \
        -e KADRAJ_KEYSTORE=/run/kadraj.keystore -e KADRAJ_KEYSTORE_PASSWORD -e KADRAJ_KEY_ALIAS -e KADRAJ_KEY_PASSWORD \
        "$IMAGE" bash /src/packaging/android/sign_apk.sh "/out/$NAME-unsigned.apk" "/out/$NAME.apk"
    rm -f "$UNSIGNED" "$UNSIGNED.sha256"
    ls -l "$OUT"/KADRAJ-"$VERSION"-android-*
    exit 0
fi

# ---------------------------------------------------------------------------------------------------------------
log "Derleme imajı ($IMAGE)"
# İmaj hiçbir dosya kopyalamaz: bağlam olmadan derlenir (packaging/android/build gibi büyük klasörler gönderilmez)
docker build ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} -t "$IMAGE" - < packaging/android/Dockerfile

# Yerel araçlar yalnız betikleri, imaj ya da NDK değişince yeniden derlenir (CI önbellek anahtarı da bu dosyalar)
STAMP="$({ cat packaging/android/build_native.sh packaging/android/Dockerfile; echo "ndk $NDK_REVISION"; } \
         | sha256sum | cut -c1-64)"

# Konteynerde çalışan kısım. Betik stdin'den okunmaz (bash -c): alt süreçler (buildozer, gradle) stdin'i
# okursa betiğin geri kalanını yutmasın.
INNER=$(cat <<'IN'
set -euo pipefail
log() { printf '\n==> %s\n' "$*"; }
NDK="/cache/ndk/android-ndk-$NDK_VERSION"
if ! grep -qx "Pkg.Revision = $NDK_REVISION" "$NDK/source.properties" 2>/dev/null; then
    log "Android NDK $NDK_VERSION indiriliyor"
    rm -rf "$NDK" /cache/ndk/ndk.zip
    curl -fL --retry 4 -o /cache/ndk/ndk.zip "$NDK_URL"
    echo "$NDK_SHA256  /cache/ndk/ndk.zip" | sha256sum -c - >/dev/null \
        || { echo "HATA: NDK sağlaması tutmadı ($NDK_URL)" >&2; exit 1; }
    unzip -q /cache/ndk/ndk.zip -d /cache/ndk
    rm -f /cache/ndk/ndk.zip
    grep -qx "Pkg.Revision = $NDK_REVISION" "$NDK/source.properties" \
        || { echo "HATA: NDK sürümü $NDK_REVISION değil" >&2; exit 1; }
fi
export ANDROID_NDK_HOME="$NDK"
if [ "$(cat /cache/native/.stamp 2>/dev/null)" = "$STAMP" ] && [ -f /cache/native/android-native-sources.tar ]; then
    log "yerel araçlar önbellekten (FFmpeg, ffprobe, QuickJS)"
else
    log "yerel araçlar derleniyor (build_native.sh)"
    rm -f /cache/native/.stamp
    KADRAJ_NATIVE_WORK=/cache/native-work bash /src/packaging/android/build_native.sh /cache/native
    echo "$STAMP" > /cache/native/.stamp
    rm -rf /cache/native-work                 # kaynakların tamamı android-native-sources.tar'da
fi
cat /cache/native/SHA256SUMS
KADRAJ_APK_WORK=/cache/apk-work KADRAJ_NATIVE=/cache/native/arm64-v8a KADRAJ_WHEELS=/cache/wheels \
KADRAJ_APK_OUT=/out bash /src/packaging/android/build_apk.sh "$MODE"
IN
)
log "NDK → yerel araçlar → APK ($MODE, Docker içinde)"
docker run --rm \
    -v "$ROOT":/src:ro -v "$CACHE":/cache -v "$CACHE/home":/home/builder -v "$OUT":/out \
    -e MODE="$MODE" -e STAMP="$STAMP" -e NDK_VERSION="$NDK_VERSION" -e NDK_REVISION="$NDK_REVISION" \
    -e NDK_SHA256="$NDK_SHA256" -e NDK_URL="$NDK_URL" \
    ${DOCKER_ARGS[@]+"${DOCKER_ARGS[@]}"} \
    "$IMAGE" bash -c "$INNER"

# GPL karşılığı kaynak: APK'daki yerel araçların kaynak arşivi, APK ile aynı sürüm adıyla (THIRD_PARTY_NOTICES.md)
cp "$CACHE/native/android-native-sources.tar" "$OUT/$SOURCES"
(cd "$OUT" && sha256sum "$SOURCES" > "$SOURCES.sha256")
log "Bitti"
ls -l "$OUT"/KADRAJ-"$VERSION"-android-*
