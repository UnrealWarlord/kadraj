"""Uygulama simgesi: ``kadraj/ui/assets/icon/kadraj-<n>.png`` dosyalarından bir ``QIcon``.

Dosyalar ``tools/make_icons.py`` ile üretilir (master: ``kadraj.svg``). Pencere ve görev çubuğu simgesi için
tüm boyutlar eklenir; işletim sistemi hangi boyutu isterse o seçilir. Paketli (PyInstaller) sürümde kök dizin
``paths.assets_dir()`` tarafından çözülür.
"""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

from ..paths import assets_dir

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def icon_dir():
    return assets_dir() / "icon"


def app_icon() -> QIcon:
    """Tüm mevcut PNG boyutlarını içeren ``QIcon``. Dosya yoksa boş (``isNull()``) bir simge döner."""
    icon = QIcon()
    folder = icon_dir()
    for size in ICON_SIZES:
        path = folder / f"kadraj-{size}.png"
        if path.is_file():
            icon.addFile(str(path), QSize(size, size))
    return icon
