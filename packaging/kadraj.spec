# -*- mode: python ; coding: utf-8 -*-
"""KADRAJ — PyInstaller tarifi (onedir, her platformda aynı).

Linux:    bash packaging/build_linux.sh                       (Docker içinde, glibc 2.34 tabanlı)
Windows:  pyinstaller packaging/kadraj.spec --noconfirm       (GitHub Actions: .github/workflows/release.yml)

Çıktı: dist/KADRAJ/
  Linux:   KADRAJ (konsol uygulaması; GUI yine açılır, --selftest çıktısı terminale yazılır) + _internal/
  Windows: KADRAJ.exe (konsolsuz, kullanıcının çalıştırdığı) + KADRAJ-console.exe (aynı uygulama, konsollu:
           `KADRAJ-console.exe --selftest` çıktısı CI günlüğünde görünsün diye) + _internal/
"""
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH: bu .spec dosyasının klasörü (PyInstaller tanımlar) → depo kökü bir üstü.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
IS_WINDOWS = sys.platform == "win32"

# Modül kayıt defteri (kadraj/core/registry.py) modülleri pkgutil + importlib ile *çalışma zamanında* bulur;
# PyInstaller bu dinamik içe aktarmaları göremez → tüm kadraj alt paketleri açıkça toplanır.
hiddenimports = collect_submodules("kadraj")
# yt-dlp eklentileri (youtube/jsc/_builtin ...) importlib ile çalışma zamanında yüklenir; PyInstaller göremez.
hiddenimports += collect_submodules("yt_dlp")

# Gömülü yazı tipleri (kadraj/ui/assets/fonts) ve istem uyarlayıcı verileri (prompt_adapter/data/*.json).
datas = collect_data_files("kadraj") + collect_data_files("yt_dlp")
try:
    datas += collect_data_files("yt_dlp_ejs")           # YouTube JS çözücü betikleri (pip paketi)
    hiddenimports += collect_submodules("yt_dlp_ejs")
except Exception:                                       # paket kurulu değilse: resmî zipapp ya da ejs:github yedeği
    pass

# --- uygulamayla gelen harici bileşenler: ffmpeg, ffprobe, deno, yt-dlp (+ versions.json) → <paket>/tools/ -------------
# Klasörü `python -m kadraj.core.updater --seed <klasör>` üretir (sağlamaları doğrulanmış resmî sürümler). Derleme
# betikleri/CI KADRAJ_TOOLS_DIR'i verir; yoksa uyarılır ve paket bileşensiz (yalnız geliştirme için) üretilir.
TOOLS_DIR = os.environ.get("KADRAJ_TOOLS_DIR", "")
if TOOLS_DIR and os.path.isdir(TOOLS_DIR):
    for _f in sorted(os.listdir(TOOLS_DIR)):
        if os.path.isfile(os.path.join(TOOLS_DIR, _f)):
            datas.append((os.path.join(TOOLS_DIR, _f), "tools"))
else:
    print("UYARI: KADRAJ_TOOLS_DIR verilmedi; ffmpeg/ffprobe/deno/yt-dlp pakete GİRMİYOR (yalnız geliştirme derlemesi).")
datas.append((os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), "."))
datas.append((os.path.join(ROOT, "LICENSE"), "."))

# --- Linux: ctypes ile yüklenen sistem kütüphaneleri PyInstaller'ın bağımlılık çözümlemesinde görünmez → açıkça eklenir.
#     libportaudio (mikrofon kaydı) ve libxcb-cursor (X11'de Qt 6.5+ xcb eklentisi). Bulunamazsa derleme DÜŞER.
extra_binaries = []
if sys.platform.startswith("linux"):
    for _lib in ("libportaudio.so.2", "libxcb-cursor.so.0"):
        _hits = [p for d in ("/usr/lib64", "/usr/lib/x86_64-linux-gnu", "/lib64", "/usr/lib") for p in glob.glob(os.path.join(d, _lib))]
        if not _hits:
            raise SystemExit(f"HATA: {_lib} derleme makinesinde bulunamadı (Dockerfile.linux'a ekleyin).")
        extra_binaries.append((os.path.realpath(_hits[0]), "."))

# Uygulamanın hiç içe aktarmadığı şeyler: paketi küçültür. Uygulama yalnızca
# QtCore / QtGui / QtWidgets / QtSvg / QtMultimedia kullanır (grep ile doğrulandı).
excludes = [
    # test araçları (derleme imajında zaten kurulmaz; kurulu bir ortamda sızmasın diye)
    "pytest", "_pytest", "pytestqt", "pluggy", "iniconfig",
    # kullanılmayan GUI/bilim kütüphaneleri (kurulu bir ortamda sürüklenmesin)
    "tkinter", "matplotlib", "scipy", "IPython",
    # kullanılmayan Qt modülleri (QtMultimedia kullanılıyor → QtNetwork/QtGui bağımlılığı korunur)
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCanvasPainter", "PySide6.QtCharts", "PySide6.QtConcurrent",
    "PySide6.QtDataVisualization", "PySide6.QtDBus", "PySide6.QtDesigner", "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets", "PySide6.QtHelp", "PySide6.QtHttpServer", "PySide6.QtLocation",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNetworkAuth", "PySide6.QtNfc", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning",
    "PySide6.QtPrintSupport", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2", "PySide6.QtQuickTest", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSerialBus",
    "PySide6.QtSerialPort", "PySide6.QtShaderTools", "PySide6.QtSpatialAudio", "PySide6.QtSql",
    "PySide6.QtStateMachine", "PySide6.QtSvgWidgets", "PySide6.QtTest", "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools", "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets",
    "PySide6.QtWebView", "PySide6.QtXml",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=extra_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# --- pakete girmeyecek dosyalar (yol, küçük harfe çevrilip "/" ile karşılaştırılır; her platformda geçerli) ------------
#  * libstdc++ / libgcc_s (Linux): derleme imajının (AlmaLinux 9) kopyaları paketten ÇIKARILIR; kullanıcının sistemi
#    kendi sürümünü kullanır. Paketteki kopya hem sistemdeki daha yeni C++ kütüphanelerini (ör. libjack → portaudio)
#    yüklenemez yapar hem de `_dl_find_object@GLIBC_2.35` ister (glibc 2.34 sistemlerde açılmazdı).
#  * Qt çeviri dosyaları (uygulamanın kendi i18n'i var), Qt PDF, Qt Sanal Klavye (kullanılmıyor, Qml/Quick sürüklerler).
_DROP_EXACT = {"libstdc++.so.6", "libgcc_s.so.1"}
_DROP_PARTS = ("pyside6/qt/translations/", "qt6pdf", "qpdf.", "virtualkeyboard")


def _keep(entry):
    dest = entry[0].replace("\\", "/").lower()
    return dest.rsplit("/", 1)[-1] not in _DROP_EXACT and not any(p in dest for p in _DROP_PARTS)


a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]

pyz = PYZ(a.pure)

# Windows'ta paketlenmiş uygulamada UTF-8 kipi: dosya/konsol kodlaması Linux ile aynı olsun (Türkçe harfler).
options = [("X utf8", None, "OPTION")] if IS_WINDOWS else []


def make_exe(name, console):
    return EXE(
        pyz,
        a.scripts,
        options,
        exclude_binaries=True,       # onedir: kütüphaneler COLLECT ile yanına konur
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        icon=os.path.join(ROOT, "kadraj", "ui", "assets", "icon", "kadraj.ico") if IS_WINDOWS else None,
    )


if IS_WINDOWS:
    exes = [make_exe("KADRAJ", console=False), make_exe("KADRAJ-console", console=True)]
else:
    exes = [make_exe("KADRAJ", console=True)]

coll = COLLECT(
    *exes,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="KADRAJ",
)
