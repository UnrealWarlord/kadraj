"""Galeriye kaydet (Android): dosyanın bir kopyası telefonun ortak klasörlerine (``docs/steps/MOBILE_6.md``, M20).

Uygulamanın dosyaları (projeler, kayıtlar, dışa aktarmalar) uygulamaya ayrılmış klasördedir; galeri ve dosya
yöneticisi orayı göstermez. "Galeriye kaydet" türüne göre bir kopya yazar:

* video → ``Movies/<Uygulama>``, ses → ``Music/<Uygulama>``, görsel → ``Pictures/<Uygulama>``,
* diğerleri (PDF, senaryo, altyazı) → ``Download/<Uygulama>``.

Android 11 (API 30) ve sonrası uygulamanın bu klasörlere izinsiz dosya eklemesine izin verir ve dosyayı galeriye
kendisi ekler (köprü ayrıca ``scan`` ister). Android 10 ve öncesinde bu yazma izin ister: ``GalleryError("old_android")``.

Aynı adda dosya varsa adın sonuna ``(2)``, ``(3)``… eklenir; var olan dosyanın üzerine yazılmaz.
Taklit/test: ``KADRAJ_SHARED_STORAGE=<klasör>`` ortak depolamanın kökü sayılır (masaüstünde de çalışır).
"""
from __future__ import annotations

import mimetypes
import os
import shutil
from pathlib import Path
from typing import Optional

from .. import device
from ..branding import APP_NAME

#: Ortak klasörler (Android ``Environment.DIRECTORY_*``); ``other`` için ``Download``.
FOLDERS = {"video": "Movies", "audio": "Music", "image": "Pictures", "other": "Download"}
#: Android'in uygulamalara izinsiz ortak klasör yazma hakkı verdiği ilk sürüm (Android 11).
MIN_API = 30


class GalleryError(Exception):
    """Galeriye kaydedilemedi. ``code``: ``not_found`` | ``no_storage`` | ``old_android`` | ``denied`` | ``failed``."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def shared_root() -> Optional[Path]:
    """Ortak depolamanın kökü (ör. ``/storage/emulated/0``). Android değilse ``None``.

    Uygulamanın dış klasörü ``<kök>/Android/data/<paket>/...`` biçimindedir; kök oradan bulunur (çok kullanıcılı
    cihazlarda ``/storage/emulated/<kullanıcı>`` doğru çıkar). Bulunamazsa ``EXTERNAL_STORAGE``.
    """
    override = os.environ.get("KADRAJ_SHARED_STORAGE")
    if override:
        return Path(override)
    if not device.is_android():
        return None
    marker = "/Android/data/"
    home = str(Path.home())
    if marker in home:
        return Path(home.split(marker, 1)[0])
    ext = os.environ.get("EXTERNAL_STORAGE")
    return Path(ext) if ext else None


def available() -> bool:
    """Bu cihazda "Galeriye kaydet" sunulmalı mı? (ortak kök var; Android'de sürüm yeterli)."""
    if shared_root() is None:
        return False
    level = device.android_api_level() if device.is_android() else 0
    return level == 0 or level >= MIN_API


def media_kind(path: "str | os.PathLike[str]") -> str:
    """``video`` | ``audio`` | ``image`` | ``other`` (dosya adının uzantısından)."""
    mime = mimetypes.guess_type(Path(path).name)[0] or ""
    kind = mime.split("/", 1)[0]
    return kind if kind in ("video", "audio", "image") else "other"


def folder_for(path: "str | os.PathLike[str]") -> str:
    """Dosyanın gideceği klasör, kullanıcıya gösterilecek biçimde (ör. ``Movies/KADRAJ``)."""
    return f"{FOLDERS[media_kind(path)]}/{APP_NAME}"


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while (folder / f"{stem} ({n}){suffix}").exists():
        n += 1
    return folder / f"{stem} ({n}){suffix}"


def save(path: "str | os.PathLike[str]") -> Path:
    """Dosyayı ortak klasöre kopyalar ve yeni yolu döner. Başarısızsa ``GalleryError``."""
    src = Path(path)
    if not src.is_file():
        raise GalleryError("not_found", str(src))
    root = shared_root()
    if root is None:
        raise GalleryError("no_storage")
    level = device.android_api_level() if device.is_android() else 0
    old = 0 < level < MIN_API
    folder = root / FOLDERS[media_kind(src)] / APP_NAME
    target: Optional[Path] = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = _free_name(folder, src.name)
        shutil.copyfile(src, target)
    except PermissionError as exc:
        _discard(target)
        raise GalleryError("old_android" if old else "denied", str(exc)) from exc
    except OSError as exc:
        _discard(target)
        raise GalleryError("failed", str(exc)) from exc
    return target


def _discard(target: Optional[Path]) -> None:
    """Yarım kalan kopyayı siler (galeride bozuk dosya kalmasın)."""
    if target is None:
        return
    try:
        target.unlink()
    except OSError:
        pass
