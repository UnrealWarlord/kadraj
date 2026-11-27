"""python-for-android kancası (buildozer: ``p4a.hook``) — KADRAJ'ın Android eklerini dağıtıma işler (MOBILE_6, M20).

p4a, şablonları (``AndroidManifest.xml`` vb.) dağıtım klasörüne yazdıktan sonra ve gradle'dan önce
``after_apk_build(toolchain)`` işlevini çağırır; çalışma klasörü dağıtım klasörüdür. Burada:

1. Manifest'e KADRAJ'ın dosya sağlayıcısı eklenir (``org.insibag.kadraj.KadrajFiles``; Java kaynağı
   ``packaging/android/java``, buildozer ``android.add_src`` ile derlenir). Qt önyüklemesinin manifest şablonunda
   ``<application>`` içine öğe eklemenin başka yolu yok.
2. python-for-android'in ``PythonActivity.onKeyDown`` kısayolu kaldırılır: her tuşun ilk basışını yutup İngilizce
   "Click again to close the app" diyordu (geri tuşu Qt'ye ancak ikinci basışta ulaşıyor, ses tuşları ilk basışta
   çalışmıyordu). Tuşlar artık doğrudan Qt'ye gider; geri tuşunu uygulama karşılar (``kadraj/ui/shell``).

İkisi de birden çok kez çalıştırılabilir (dağıtım klasörü derlemeler arasında korunur). Beklenen metin bulunamazsa
derleme durur: p4a sürümü değiştiyse kancanın da güncellenmesi gerekir.
"""
from __future__ import annotations

import re
from pathlib import Path

MARK = "<!-- KADRAJ: dosya sağlayıcı (packaging/android/p4a_hook.py) -->"
PROVIDER = """        <provider android:name="org.insibag.kadraj.KadrajFiles"
                  android:authorities="${applicationId}.files"
                  android:exported="false"
                  android:grantUriPermissions="true" />
"""
KEY_MARK = "// KADRAJ: tuşlar doğrudan Qt'ye gider (packaging/android/p4a_hook.py)"
#: p4a'nın "iki kez bas" kısayolu: sayaç alanı ve onKeyDown yöntemi (58d21141 sürümü).
KEY_HANDLER = re.compile(
    r"\n[ \t]*long lastBackClick = SystemClock\.elapsedRealtime\(\);\s*"
    r"@Override\s*public boolean onKeyDown\(int keyCode, KeyEvent event\) \{.*?"
    r"return super\.onKeyDown\(keyCode, event\);\s*\}\n",
    re.S)


def patch_manifest(text: str) -> str:
    if MARK in text:
        return text
    if text.count("</application>") != 1:
        raise SystemExit("KADRAJ kancası: AndroidManifest.xml içinde tek bir </application> bekleniyordu")
    return text.replace("</application>", f"{MARK}\n{PROVIDER}    </application>", 1)


def patch_activity(text: str) -> str:
    if KEY_MARK in text:
        return text
    patched, n = KEY_HANDLER.subn(f"\n    {KEY_MARK}\n", text, count=1)
    if n != 1:
        raise SystemExit("KADRAJ kancası: PythonActivity.onKeyDown bulunamadı (python-for-android değişmiş olabilir)")
    return patched


def _rewrite(path: Path, fn) -> None:
    if not path.is_file():
        raise SystemExit(f"KADRAJ kancası: {path} yok")
    text = path.read_text(encoding="utf-8")
    new = fn(text)
    if new != text:
        path.write_text(new, encoding="utf-8")
    print(f"KADRAJ kancası: {path} hazır")


def after_apk_build(toolchain) -> None:  # noqa: ARG001 — p4a imzası
    _rewrite(Path("src/main/AndroidManifest.xml"), patch_manifest)
    _rewrite(Path("src/main/java/org/kivy/android/PythonActivity.java"), patch_activity)
