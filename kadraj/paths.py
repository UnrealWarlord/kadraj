"""Dosya sistemi konumları.

Hepsi **çağrıldığı anda** hesaplanır (modül yüklenirken değil); böylece testler
``HOME`` ya da ``KADRAJ_HOME`` ortam değişkenini değiştirerek gerçek kullanıcı
verisine dokunmadan çalışabilir.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from . import device

PACKAGE_DIR = Path(__file__).resolve().parent


def _bundle_root() -> Path:
    # PyInstaller paketinde kaynaklar sys._MEIPASS altındadır.
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", PACKAGE_DIR.parent)) / "kadraj"
    return PACKAGE_DIR


def assets_dir() -> Path:
    return _bundle_root() / "ui" / "assets"


def fonts_dir() -> Path:
    return assets_dir() / "fonts"


def home_dir() -> Path:
    """Genel kullanıcı verisi: ``~/.kadraj`` (``KADRAJ_HOME`` ile değiştirilebilir).

    Android'de uygulamanın özel (iç) klasörü altındadır: ``<getFilesDir()>/.kadraj``. Ayarlar, kasa ve veritabanı
    orada durur; kullanıcının göreceği dosyalar (projeler, kayıtlar) ``user_dir()`` altındadır.
    """
    override = os.environ.get("KADRAJ_HOME")
    if override:
        return Path(override).expanduser()
    private = device.android_private_dir()
    if private is not None:
        return private / ".kadraj"
    return Path.home() / ".kadraj"


def user_dir() -> Path:
    """Kullanıcının dosyalarının kökü: masaüstünde ev klasörü (``~``).

    Android'de ``prepare_android_environment`` bunu uygulamaya ayrılmış dış depolamaya
    (``…/Android/data/<paket>/files/Documents``) çevirir ve ``HOME``'u da oraya ayarlar; böylece kodun her yerindeki
    ``Path.home()`` aynı, kullanıcının erişebildiği klasörü gösterir.
    """
    return Path.home()


def prepare_android_environment(documents_dir: Optional[str] = None) -> Optional[Path]:
    """Android'de ``HOME`` ve ``TMPDIR``'i yazılabilir klasörlere ayarlar (masaüstünde hiçbir şey yapmaz, ``None``).

    ``documents_dir`` Qt'nin ``QStandardPaths.DocumentsLocation`` değeridir (QApplication kurulduktan sonra bilinir).
    Verilmezse ya da yazılamıyorsa uygulamanın özel klasörü kullanılır. Ayarlanan ``HOME`` döner.
    """
    if not device.is_android():
        return None
    private = device.android_private_dir()
    if private is not None:
        # HOME birazdan değişecek; özel klasör HOME'a düşse bile home_dir() sabit kalsın diye bulunan yeri sabitle.
        os.environ.setdefault("KADRAJ_ANDROID_PRIVATE", str(private))
    candidates = [documents_dir, str(private or "")]
    home: Optional[Path] = None
    for cand in candidates:
        if not cand:
            continue
        path = Path(cand)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(path, os.W_OK):
            home = path
            break
    if home is None:
        return None
    os.environ["HOME"] = str(home)
    if private is not None:
        tmp = private / "tmp"
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("TMPDIR", str(tmp))
            import tempfile
            tempfile.tempdir = None              # önceden hesaplanmışsa bir sonraki çağrıda TMPDIR'den yeniden bulunsun
        except OSError:
            pass
    # Android'deki OpenSSL'in sistem sertifika klasörü yoktur: urllib/ssl için certifi'nin CA listesi kullanılsın.
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception:  # noqa: BLE001 — certifi yoksa HTTPS doğrulaması ilk istekte açıklamalı hata verir
        pass
    return home


def settings_file() -> Path:
    return home_dir() / "settings.json"


def consent_file() -> Path:
    return home_dir() / "consent.json"


def profiles_dir() -> Path:
    return home_dir() / "profiles"


def recent_file() -> Path:
    return home_dir() / "recent.json"


def vault_file() -> Path:
    return home_dir() / "vault.bin"
