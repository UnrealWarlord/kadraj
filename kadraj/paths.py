"""Dosya sistemi konumları.

Hepsi **çağrıldığı anda** hesaplanır (modül yüklenirken değil); böylece testler
``HOME`` ya da ``KADRAJ_HOME`` ortam değişkenini değiştirerek gerçek kullanıcı
verisine dokunmadan çalışabilir.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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
    """Genel kullanıcı verisi: ``~/.kadraj`` (``KADRAJ_HOME`` ile değiştirilebilir)."""
    override = os.environ.get("KADRAJ_HOME")
    return Path(override).expanduser() if override else Path.home() / ".kadraj"


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
