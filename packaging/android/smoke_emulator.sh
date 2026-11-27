#!/usr/bin/env bash
# KADRAJ · Android açılış denemesi (docs/steps/MOBILE_7.md). APK'yı Android emülatöründe (x86_64 sistem imajı, ARM64
# çevirisiyle) kurar; önce telefon, sonra tablet boyutunda açar, her aşamada ekran görüntüsü ve logcat alır.
# Uygulama açılış aşamalarını logcat'e yazar (kadraj/ui/shell/app.py → crashlog.note):
#   "KADRAJ: açılış: phone|tablet, …"  "KADRAJ: onay soruluyor"  "KADRAJ: sihirbaz açıldı"  "KADRAJ: ana pencere açıldı (…)"
# Deneme onayı Enter ile kabul eder (varsayılan düğme), sihirbazı Esc ile atlar.
#
# Kullanım:   packaging/android/smoke_emulator.sh <apk> <çıktı-klasörü>
# Gerekenler: ANDROID_HOME (cmdline-tools), Java, KVM. GitHub'ın Linux çalıştırıcısında bilgi amaçlı çalışır
#             (.github/workflows/release.yml → android-smoke). KVM ya da emülatörde ARM64 çevirisi yoksa "atlandı"
#             der ve 0 ile çıkar. İmzasız APK (depo gizlileri yokken) yalnız bu deneme için geçici anahtarla imzalanır.
# Çıktı:      <çıktı>/ozet.md (GitHub'da iş özetine de eklenir), *.png, logcat-*.txt, emülatör/kurulum günlükleri.
set -Eeuo pipefail

[ $# -eq 2 ] || { echo "kullanım: $0 <apk> <çıktı-klasörü>" >&2; exit 2; }
APK=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
mkdir -p "$2"
OUT=$(cd "$2" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
PKG=org.insibag.kadraj
ACTIVITY=org.kivy.android.PythonActivity
API=${KADRAJ_SMOKE_API:-30}                     # Android 11: x86_64 imajında ARM64 çevirisi var
IMAGE="system-images;android-$API;google_apis;x86_64"
SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
SUMMARY="$OUT/ozet.md"
WORK=$(mktemp -d)
EMU=""
: > "$SUMMARY"

say() { printf '%s\n' "$*" | tee -a "$SUMMARY"; }
finish() {
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$SUMMARY" >> "$GITHUB_STEP_SUMMARY"; fi
    exit "$1"
}
skip() { say ""; say "**Atlandı:** $*"; echo "::notice title=Android açılış denemesi atlandı::$*"; finish 0; }
fail() { say ""; say "**HATA:** $*"; echo "::error title=Android açılış denemesi::$*"; finish 1; }
cleanup() {
    [ -n "$EMU" ] && kill "$EMU" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT
trap 'fail "beklenmeyen hata (smoke_emulator.sh satır $LINENO)"' ERR

say "## Android açılış denemesi (emülatör)"
say ""
[ -f "$APK" ] || fail "APK yok: $APK"
[ -n "$SDK" ] && [ -d "$SDK" ] || fail "ANDROID_HOME yok"
SDKM="$SDK/cmdline-tools/latest/bin/sdkmanager"
AVDM="$SDK/cmdline-tools/latest/bin/avdmanager"
[ -x "$SDKM" ] && [ -x "$AVDM" ] || fail "sdkmanager/avdmanager yok ($SDK/cmdline-tools/latest)"

# --- KVM (GitHub'ın önerdiği udev kuralı) -------------------------------------------------------------------------
[ -e /dev/kvm ] || skip "bu makinede KVM yok; emülatör donanım hızlandırması olmadan çalışmaz"
if [ ! -w /dev/kvm ]; then
    echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' \
        | sudo tee /etc/udev/rules.d/99-kvm4all.rules >/dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger --name-match=kvm
    sleep 2
fi
[ -w /dev/kvm ] || skip "KVM'e erişilemiyor"

# --- emülatör ---------------------------------------------------------------------------------------------------
say "- Sistem imajı: \`$IMAGE\`"
{ yes 2>/dev/null || true; } | "$SDKM" --licenses > "$OUT/sdkmanager.log" 2>&1 || true
"$SDKM" --install "platform-tools" "emulator" "build-tools;36.0.0" "$IMAGE" >> "$OUT/sdkmanager.log" 2>&1 \
    || fail "sdkmanager paketleri kuramadı (sdkmanager.log)"
export ANDROID_AVD_HOME="$WORK/avd"
mkdir -p "$ANDROID_AVD_HOME"
echo no | "$AVDM" create avd -n kadraj -k "$IMAGE" -d pixel_4 --force > "$OUT/avdmanager.log" 2>&1 \
    || fail "emülatör (AVD) oluşturulamadı (avdmanager.log)"
CFG="$ANDROID_AVD_HOME/kadraj.avd/config.ini"
setcfg() { if grep -q "^$1=" "$CFG"; then sed -i "s|^$1=.*|$1=$2|" "$CFG"; else echo "$1=$2" >> "$CFG"; fi; }
setcfg hw.ramSize 4096
setcfg disk.dataPartition.size 6G
setcfg hw.keyboard yes

"$SDK/emulator/emulator" -avd kadraj -no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect \
    -camera-back none -camera-front none > "$OUT/emulator.log" 2>&1 &
EMU=$!
ADB="$SDK/platform-tools/adb"
timeout 300 "$ADB" wait-for-device || fail "emülatör 5 dakikada bağlanmadı (emulator.log)"
deadline=$((SECONDS + 600))
until [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; do
    [ $SECONDS -lt $deadline ] || fail "emülatör 10 dakikada açılmadı (emulator.log)"
    kill -0 "$EMU" 2>/dev/null || fail "emülatör kapandı (emulator.log)"
    sleep 5
done
"$ADB" shell input keyevent 82 >/dev/null 2>&1 || true                    # kilit ekranını kaldır
for s in window_animation_scale transition_animation_scale animator_duration_scale; do
    "$ADB" shell settings put global "$s" 0 >/dev/null 2>&1 || true
done
abis=$("$ADB" shell getprop ro.product.cpu.abilist | tr -d '\r')
say "- Emülatörün ABI listesi: \`$abis\`"
case ",$abis," in *,arm64-v8a,*) ;; *) skip "emülatörde ARM64 çevirisi yok ($abis)" ;; esac

# --- APK --------------------------------------------------------------------------------------------------------
BT=$(find "$SDK/build-tools" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*' | sort -V | tail -n1)
if ! "$BT/apksigner" verify "$APK" >/dev/null 2>&1; then
    say "- APK imzasız: yalnız bu deneme için geçici anahtarla imzalandı"
    export KADRAJ_KEYSTORE="$WORK/deneme.p12" KADRAJ_KEY_ALIAS=deneme
    KADRAJ_KEYSTORE_PASSWORD=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
    export KADRAJ_KEYSTORE_PASSWORD
    "${JAVA_HOME:+$JAVA_HOME/bin/}keytool" -genkeypair -keystore "$KADRAJ_KEYSTORE" -storetype PKCS12 -alias deneme \
        -keyalg RSA -keysize 2048 -validity 7 -dname "CN=Acilis denemesi" -storepass:env KADRAJ_KEYSTORE_PASSWORD \
        >/dev/null 2>&1 || fail "geçici anahtar üretilemedi"
    ANDROID_BUILD_TOOLS="$BT" bash "$HERE/sign_apk.sh" "$APK" "$WORK/deneme.apk" > "$OUT/imza.log" 2>&1 \
        || fail "geçici imza atılamadı (imza.log)"
    APK="$WORK/deneme.apk"
fi
"$ADB" install -r -g "$APK" > "$OUT/kurulum.log" 2>&1 || fail "APK kurulamadı: $(tail -n1 "$OUT/kurulum.log")"
say "- Kurulum: tamam ($(du -h "$APK" | cut -f1))"
say ""
say "| Boyut | Beklenen | Açılış satırı | Son aşama | Süreç | Python hatası |"
say "|---|---|---|---|---|---|"

# --- açılış -----------------------------------------------------------------------------------------------------
# Yavaş emülatörde sistem "yanıt vermiyor" pencereleri çıkabilir: tuşlar ve ekran görüntüsü öncesi kapatılır
calm() { "$ADB" shell am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS >/dev/null 2>&1 || true; }
shot() { calm; "$ADB" exec-out screencap -p > "$OUT/$1.png" 2>/dev/null || true; }
# wait_for <saniye> <metin>...: logcat'teki "KADRAJ: <metin>" satırlarından biri görünene dek bekler, görüneni yazar.
# Uygulama kapanırsa "kapandı", süre dolarsa "zaman aşımı" yazar.
wait_for() {
    local limit=$1 started=$SECONDS want
    shift
    while [ $((SECONDS - started)) -lt "$limit" ]; do
        "$ADB" logcat -d -s python:I > "$WORK/python.txt" 2>/dev/null || true
        for want in "$@"; do
            if grep -qF "KADRAJ: $want" "$WORK/python.txt"; then echo "$want"; return 0; fi
        done
        if [ $((SECONDS - started)) -gt 30 ] && ! "$ADB" shell pidof "$PKG" >/dev/null 2>&1; then
            echo "kapandı"; return 0
        fi
        sleep 3
    done
    echo "zaman aşımı"
}

FAILED=0
run_case() {   # run_case <ad> <beklenen form faktörü>
    local name=$1 expect=$2 stage first alive errors ok=1
    "$ADB" shell am force-stop "$PKG" >/dev/null 2>&1 || true
    "$ADB" logcat -c
    "$ADB" shell am start -n "$PKG/$ACTIVITY" >/dev/null
    stage=$(wait_for 360 "onay soruluyor" "sihirbaz açıldı" "ana pencere açıldı")
    if [ "$stage" = "onay soruluyor" ]; then
        sleep 6; shot "$name-1-onay"; calm
        "$ADB" shell input keyevent KEYCODE_ENTER                          # "Kabul ediyorum" varsayılan düğme
        stage=$(wait_for 120 "sihirbaz açıldı" "ana pencere açıldı")
    fi
    if [ "$stage" = "sihirbaz açıldı" ]; then
        sleep 6; shot "$name-2-sihirbaz"; calm
        "$ADB" shell input keyevent KEYCODE_ESCAPE                         # sihirbaz atlanır, varsayılanlarla devam
        stage=$(wait_for 120 "ana pencere açıldı")
    fi
    sleep 10
    shot "$name-3-son"
    "$ADB" logcat -d > "$OUT/logcat-$name.txt" 2>/dev/null || true
    first=$(grep -o 'KADRAJ: açılış: .*' "$OUT/logcat-$name.txt" | head -n1 | tr -d '\r' | sed 's/^KADRAJ: //' || true)
    alive=$("$ADB" shell pidof "$PKG" 2>/dev/null | tr -d '\r' || true)
    errors=$(grep -cE 'Traceback \(most recent call last\)|Fatal Python error|FATAL EXCEPTION|SIGSEGV|SIGABRT' \
             "$OUT/logcat-$name.txt" || true)
    [ "$stage" = "ana pencere açıldı" ] || ok=0
    [ -n "$alive" ] || ok=0
    [ "${errors:-0}" = 0 ] || ok=0
    case "$first" in "açılış: $expect"*) ;; *) ok=0 ;; esac
    say "| $name | $expect | ${first:-—} | $stage | $([ -n "$alive" ] && echo çalışıyor || echo YOK) | ${errors:-0} |"
    [ $ok = 1 ] || FAILED=1
}

run_case telefon phone
"$ADB" shell wm size 1600x2560 && "$ADB" shell wm density 320                # en küçük genişlik 800 dp → tablet
run_case tablet tablet
"$ADB" shell wm size reset >/dev/null 2>&1 || true
"$ADB" shell wm density reset >/dev/null 2>&1 || true
"$ADB" pull "/sdcard/Android/data/$PKG/files/Documents/KADRAJ-hata.txt" "$OUT/" >/dev/null 2>&1 \
    && say "" && say "Uygulamanın hata günlüğü bulundu: \`KADRAJ-hata.txt\`" || true

say ""
if [ $FAILED = 0 ]; then
    say "Sonuç: **tamam** — telefon ve tablet boyutunda ana pencere açıldı, Python hatası yok."
    finish 0
fi
fail "açılış denemesi beklenen sonucu vermedi (tablo, ekran görüntüleri ve logcat-*.txt)"
