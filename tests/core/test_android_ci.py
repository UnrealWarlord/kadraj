"""Mobil 7 (M26–M28): Android CI — ``release.yml``'deki Android işleri ve derleme/imza/emülatör betikleri tutarlı.

GitHub Actions burada çalışmaz; dosyaların birbirine ve uygulamaya uyduğu denetlenir: yayımlanan dosya adları,
gizlilerin yalnız imza adımına girmesi, kapılar, önbellek anahtarı ve emülatör denemesinin beklediği açılış satırları.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
BUILD = (ROOT / "packaging" / "build_android.sh").read_text(encoding="utf-8")
SIGN = (ROOT / "packaging" / "android" / "sign_apk.sh").read_text(encoding="utf-8")
SMOKE = (ROOT / "packaging" / "android" / "smoke_emulator.sh").read_text(encoding="utf-8")
SECRETS = ("KADRAJ_KEYSTORE_B64", "KADRAJ_KEYSTORE_PASSWORD", "KADRAJ_KEY_ALIAS", "KADRAJ_KEY_PASSWORD")


def _job(name: str) -> str:
    """``release.yml``'den bir işin metni (bir sonraki işe kadar)."""
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)", WORKFLOW, re.M | re.S)
    assert m, f"release.yml'de {name} işi yok"
    return m.group(1)


def _steps(job: str) -> dict:
    """Adım adı → adım metni."""
    parts = re.split(r"^      - name: ", job, flags=re.M)[1:]
    return {part.split("\n", 1)[0].strip(): part for part in parts}


def test_android_job_builds_signs_and_publishes_the_documented_files():
    job = _job("android")
    steps = _steps(job)
    assert "bash packaging/build_android.sh release" in steps["APK'yı derle (Docker)"]
    sign = steps["APK'yı imzala"]
    assert "if: steps.signing.outputs.sign == 'true'" in sign and "bash packaging/build_android.sh sign" in sign
    assert '[ "$TAG" != "v$VERSION" ]' in steps["Etiket ile sürüm tutarlı mı? (kapı)"]
    gate = steps["İmza anahtarı var mı? (kapı)"]
    assert 'if [ "$EVENT" = release ]' in gate and "exit 1" in gate          # release'e imzasız APK gitmez
    upload = steps["Release'e ekle"]
    for name in ("dist/KADRAJ-*-android-arm64.apk", "dist/KADRAJ-*-android-arm64.apk.sha256",
                 "dist/KADRAJ-*-android-native-sources.tar", "dist/KADRAJ-*-android-native-sources.tar.sha256"):
        assert name in upload
    assert "-unsigned" not in upload and "--clobber" in upload
    # betik bu adları üretir
    assert 'NAME="KADRAJ-${VERSION}-android-arm64"' in BUILD
    assert 'SOURCES="KADRAJ-${VERSION}-android-native-sources.tar"' in BUILD
    assert "KADRAJ-<sürüm>-android-native-sources.tar" in (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")


def test_secret_values_reach_only_the_signing_step():
    for name, text in _steps(_job("android")).items():
        values = [s for s in SECRETS if re.search(rf"\$\{{\{{ secrets\.{s} }}\}}", text)]
        if name == "APK'yı imzala":
            assert values == list(SECRETS)
        else:
            assert values == [], f"{name}: gizli değeri bu adıma girmemeli"
    gate = _steps(_job("android"))["İmza anahtarı var mı? (kapı)"]
    assert gate.count("!= '' }}") == 3                                     # kapı yalnız var/yok bakar


def test_scripts_never_print_or_pass_passwords_on_the_command_line():
    for text in (BUILD, SIGN, SMOKE):
        assert "set -x" not in text
        assert not re.search(r"--(?:ks|key)-pass (?!env:)", text)          # "pass:…" parolayı komut satırına koyar
        assert not re.search(r"-(?:store|key)pass(?!:env) ", text)
    assert "--ks-pass env:KADRAJ_KEYSTORE_PASSWORD --key-pass env:KADRAJ_KEY_PASSWORD" in SIGN
    run = BUILD[BUILD.index('docker run --rm --network none --user'):]
    assert "-e KADRAJ_KEYSTORE_PASSWORD -e KADRAJ_KEY_ALIAS -e KADRAJ_KEY_PASSWORD" in run   # değer değil, yalnız ad
    assert "-e KADRAJ_KEYSTORE_PASSWORD=" not in BUILD


def test_native_cache_key_covers_everything_the_build_stamp_reads():
    key = re.search(r"key: android-native-\$\{\{ hashFiles\(([^)]*)\) \}\}", _job("android"))
    assert key, "yerel araç önbelleğinin anahtarı yok"
    hashed = set(re.findall(r"'([^']+)'", key.group(1)))
    stamp = re.search(r'STAMP="\$\(\{ cat ([^;]+);', BUILD)
    assert stamp
    assert set(stamp.group(1).split()) | {"packaging/build_android.sh"} <= hashed     # NDK sürümü bu betikte
    assert "path: build/android/native" in _job("android") and 'build/android}"' in BUILD


def test_ndk_download_is_pinned():
    assert re.search(r"^NDK_VERSION=r\d+[a-z]?$", BUILD, re.M)
    assert re.search(r"^NDK_SHA256=[0-9a-f]{64}$", BUILD, re.M)
    assert re.search(r"^NDK_REVISION=\d+\.\d+\.\d+$", BUILD, re.M)
    assert 'sha256sum -c' in BUILD


def test_signing_aligns_for_16_kb_pages_and_checks_both_schemes():
    assert '"$BT/zipalign" -f -P 16 4' in SIGN and '"$BT/zipalign" -c -P 16 4 "$SIGNED"' in SIGN
    assert SIGN.index("zipalign\" -f") < SIGN.index("apksigner\" sign")            # önce hizala, sonra imzala
    assert "--v2-signing-enabled true --v3-signing-enabled true" in SIGN
    assert "for scheme in v2 v3" in SIGN and "--min-sdk-version 24" in SIGN


def test_emulator_smoke_is_informational_and_waits_for_what_the_app_writes():
    smoke = _job("android-smoke")
    assert "continue-on-error: true" in smoke and "needs: android" in smoke
    assert "bash packaging/android/smoke_emulator.sh" in smoke
    app = (ROOT / "kadraj" / "ui" / "shell" / "app.py").read_text(encoding="utf-8")
    notes = re.findall(r'_note\(f?"([^"{]+)', app)
    waited = set()
    for call in re.findall(r"wait_for \d+ ((?:\"[^\"]+\" ?)+)", SMOKE):
        waited.update(re.findall(r'"([^"]+)"', call))
    assert waited == {"onay soruluyor", "sihirbaz açıldı", "ana pencere açıldı"}
    for marker in waited:
        assert any(note.startswith(marker) for note in notes), f"uygulama '{marker}' satırını yazmıyor"
    assert any(note.startswith("açılış: ") for note in notes) and "KADRAJ: açılış: " in SMOKE
