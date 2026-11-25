"""DPI ve pencere yerleşimi — iskeletin ``scaling.py`` mantığının Qt karşılığı.

Qt 6 yüksek DPI'ı kendisi yönetir (mantıksal piksel); bu yüzden Tk'daki ``px()`` çarpanı gerekmez.
Geriye kalan: pencereyi çalışma alanının %94'üne sığdırma ve ortalama (``fit_window``).
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

SCREEN_MARGIN = 0.94


def configure_high_dpi() -> None:
    """``QApplication`` oluşturulmadan ÖNCE çağrılır. Ölçek yuvarlama ilkesini kesirli ölçeğe ayarlar
    (125 % / 150 % ekranlarda bulanıklığı önler); kullanıcı ``QT_SCALE_FACTOR_ROUNDING_POLICY`` verdiyse dokunmaz."""
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")


def work_area(widget: Optional[QWidget] = None) -> QRect:
    screen = (widget.screen() if widget is not None else None) or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)


def fit_window(window: QWidget, width: int, height: int, *, min_width: int = 0, min_height: int = 0,
               parent: Optional[QWidget] = None) -> Tuple[int, int]:
    """Pencereyi ekrana sığacak (%94) boyutta ``parent``'ın (yoksa ekranın) ortasına yerleştirir."""
    area = work_area(parent or window)
    w = min(width, int(area.width() * SCREEN_MARGIN))
    h = min(height, int(area.height() * SCREEN_MARGIN))
    window.setMinimumSize(min(min_width, w), min(min_height, h))
    window.resize(w, h)
    if parent is not None and parent.isVisible():
        cx = parent.geometry().center().x() - w // 2
        cy = parent.geometry().center().y() - h // 2
    else:
        cx = area.center().x() - w // 2
        cy = area.center().y() - h // 2
    window.move(max(area.left(), min(cx, area.right() - w)), max(area.top(), min(cy, area.bottom() - h)))
    return w, h
