#!/usr/bin/env bash
# KADRAJ · Android APK'sını imzalar (docs/steps/MOBILE_7.md): zipalign (16 KB sayfa hizası) → apksigner (v2 + v3
# imza şeması) → doğrulama → <imzalı.apk>.sha256. CI'da packaging/build_android.sh sign bunu ağsız konteynerde çalıştırır.
#
# Kullanım:  packaging/android/sign_apk.sh <imzasız.apk> <imzalı.apk>
# Ortam:
#   KADRAJ_KEYSTORE           anahtar deposu dosyası (.jks / .p12)
#   KADRAJ_KEYSTORE_PASSWORD  deponun parolası
#   KADRAJ_KEY_ALIAS          anahtarın adı
#   KADRAJ_KEY_PASSWORD       anahtarın parolası (boşsa deponunki; PKCS12'de ikisi aynıdır)
#   ANDROID_BUILD_TOOLS       isteğe bağlı: build-tools klasörü. Verilmezse ANDROID_HOME, ANDROID_SDK_ROOT ve
#                             ~/.buildozer SDK'sındaki en yeni sürüm kullanılır (35.0.0+ gerekir: zipalign -P 16).
# Parolalar apksigner'a değişken adıyla verilir (env:AD); komut satırında ve günlükte görünmez. Günlüğe yalnız
# sertifikanın sahibi (DN) ve SHA-256 parmak izi yazılır; ikisi de APK'nın içinde zaten herkese açıktır.
set -euo pipefail

die() { echo "HATA: $*" >&2; exit 1; }
[ $# -eq 2 ] || die "kullanım: $0 <imzasız.apk> <imzalı.apk>"
IN=$1
SIGNED=$2
[ -f "$IN" ] || die "$IN yok"
[ -n "${KADRAJ_KEYSTORE:-}" ] && [ -f "$KADRAJ_KEYSTORE" ] || die "KADRAJ_KEYSTORE bir anahtar deposu dosyası olmalı"
[ -n "${KADRAJ_KEYSTORE_PASSWORD:-}" ] || die "KADRAJ_KEYSTORE_PASSWORD boş"
[ -n "${KADRAJ_KEY_ALIAS:-}" ] || die "KADRAJ_KEY_ALIAS boş"
export KADRAJ_KEY_PASSWORD="${KADRAJ_KEY_PASSWORD:-$KADRAJ_KEYSTORE_PASSWORD}"

BT=${ANDROID_BUILD_TOOLS:-}
if [ -z "$BT" ]; then
    candidates=()
    for sdk in "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}" "${HOME:-/nonexistent}/.buildozer/android/platform/android-sdk"; do
        [ -n "$sdk" ] && [ -d "$sdk/build-tools" ] || continue
        for dir in "$sdk"/build-tools/[0-9]*/; do
            [ -d "$dir" ] && candidates+=("${dir%/}")
        done
    done
    if [ ${#candidates[@]} -gt 0 ]; then
        BT=$(printf '%s\n' "${candidates[@]}" | awk -F/ '{print $NF "\t" $0}' | sort -V -k1,1 | tail -n1 | cut -f2)
    fi
fi
[ -n "$BT" ] && [ -x "$BT/zipalign" ] && [ -x "$BT/apksigner" ] \
    || die "Android build-tools bulunamadı (ANDROID_HOME ya da ANDROID_BUILD_TOOLS verin)"
major=$(basename "$BT" | cut -d. -f1)
case "$major" in ''|*[!0-9]*) major=0 ;; esac
[ "$major" -ge 35 ] || die "build-tools $(basename "$BT") eski; 35.0.0 ya da sonrası gerekir (zipalign -P 16)"
echo "build-tools: $BT"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
ALIGNED="$TMP/aligned.apk"
# Sıkıştırılmamış yerel kütüphaneler 16 KB sayfa sınırına hizalanır (Android 15+ 16 KB sayfalı cihazlar).
# İmza hizalamadan SONRA atılır: imzalı APK'yı zipalign'dan geçirmek imzayı bozar.
"$BT/zipalign" -f -P 16 4 "$IN" "$ALIGNED"
"$BT/zipalign" -c -P 16 4 "$ALIGNED" >/dev/null || die "zipalign denetimi geçmedi"

mkdir -p "$(dirname "$SIGNED")"
rm -f "$SIGNED" "$SIGNED.sha256"
"$BT/apksigner" sign \
    --ks "$KADRAJ_KEYSTORE" --ks-key-alias "$KADRAJ_KEY_ALIAS" \
    --ks-pass env:KADRAJ_KEYSTORE_PASSWORD --key-pass env:KADRAJ_KEY_PASSWORD \
    --v1-signing-enabled false --v2-signing-enabled true --v3-signing-enabled true --v4-signing-enabled false \
    --out "$SIGNED" "$ALIGNED" || die "apksigner imzalayamadı (anahtar adı ya da parolalar yanlış olabilir)"

# En düşük sürüm Android 9 (API 28) için v3 yeter; v2 de atılır (v2'yi denetleyen araçlar için). Doğrulama
# --min-sdk-version 24 ile yapılır: yoksa apksigner API 28+ için yalnız v3'ü denetler ve v2'yi "false" gösterir.
"$BT/apksigner" verify --min-sdk-version 24 --verbose --print-certs "$SIGNED" > "$TMP/verify.txt" 2>&1 \
    || { cat "$TMP/verify.txt" >&2; die "imza doğrulanamadı"; }
for scheme in v2 v3; do
    grep -q "^Verified using $scheme scheme (APK Signature Scheme $scheme): true" "$TMP/verify.txt" \
        || { cat "$TMP/verify.txt" >&2; die "APK'da $scheme imzası yok"; }
done
grep -E '^Verified using v[23] scheme|certificate (DN|SHA-256 digest):' "$TMP/verify.txt" | sort -u
"$BT/zipalign" -c -P 16 4 "$SIGNED" >/dev/null || die "imzalı APK 16 KB hizalı değil"

(cd "$(dirname "$SIGNED")" && sha256sum "$(basename "$SIGNED")" > "$(basename "$SIGNED").sha256")
echo "==> imzalı APK: $SIGNED ($(du -h "$SIGNED" | cut -f1))"
