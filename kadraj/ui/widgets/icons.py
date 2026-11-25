"""Çizgi ikonlar (24×24 viewBox, ``stroke=currentColor``) — QtSvg ile boyanır.

Yollar tasarım tuvalinden (Onboarding/V2Home) alınmıştır; ``gear`` Feather (MIT) setindendir.
``draw_icon`` ikonu istenen renkte ve DPI'da çizer; piksmap'ler önbelleğe alınır.
"""
from __future__ import annotations

from typing import Dict, Tuple

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICONS: Dict[str, str] = {
    "download": "M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19h14",
    "mic": "M9 6a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0zM5 11a7 7 0 0 0 14 0M12 18v3",
    "film": "M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM7 5v14M17 5v14"
            "M3 9h4M3 15h4M17 9h4M17 15h4",
    "image": "M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM3 16l5-5 4 4 3-3 6 6"
             "M16 8.5h.01",
    "clapper": "M4 9h16v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM4 9l2-5h14l-2 5M10 4l-2 5M15 4l-2 5",
    "help": "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17h.01",
    "folder": "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "folder-plus": "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v6M9 13h6",
    "desktop": "M3 5h18v11H3zM8 20h8M12 16v4",
    "key": "M15 7a4 4 0 1 1-3.9 4.9L4 19v2h3l1-1v-2h2v-2h2l1.1-1.1A4 4 0 0 1 15 7zM16.5 9.5h.01",
    "book": "M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21V5M8 7h7M8 11h5",
    "arrow-right": "M5 12h14M13 6l6 6-6 6",
    "undo": "M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3",
    "rewind": "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2",
    "check": "M5 12.5l4.5 4.5L19 7.5",
    "alert": "M12 4l9 16H3zM12 10v5M12 18h.01",
    "search": "M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14zM21 21l-5-5",
    "plus": "M12 5v14M5 12h14",
    "dot": "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z",
    "cloud-off": "M3 3l18 18M9 5a6 6 0 0 1 9.5 4.5A4.5 4.5 0 0 1 20 17M5.6 9A5 5 0 0 0 7 19h10",
    "layers": "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5",
    "sliders": "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
    "gear": "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"
            "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 "
            "1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06"
            "a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09"
            "A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9"
            "a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06"
            "a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09"
            "a1.65 1.65 0 0 0-1.51 1z",
}

_cache: Dict[Tuple[str, str, int, int, float], QPixmap] = {}


def icon_names() -> list:
    return sorted(ICONS)


def _svg(name: str, color: str, stroke: float) -> bytes:
    path = ICONS.get(name, ICONS["dot"])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="{path}"/></svg>').encode("utf-8")


def pixmap(name: str, color: str, size: int, dpr: float = 1.0, stroke: float = 1.8) -> QPixmap:
    key = (name, color, size, int(stroke * 10), round(dpr, 2))
    pm = _cache.get(key)
    if pm is None:
        px = max(1, int(round(size * dpr)))
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(p, QRectF(0, 0, px, px))
        p.end()
        pm.setDevicePixelRatio(dpr)
        _cache[key] = pm
    return pm


def draw_icon(painter: QPainter, rect: QRectF, name: str, color: "str | QColor",
              stroke: float = 1.8) -> None:
    """``rect`` (kare olması önerilir) içine ikon çizer."""
    col = color.name() if isinstance(color, QColor) else str(color)
    dpr = painter.device().devicePixelRatioF() if hasattr(painter.device(), "devicePixelRatioF") else 1.0
    size = int(round(min(rect.width(), rect.height())))
    pm = pixmap(name, col, size, dpr, stroke)
    x = rect.x() + (rect.width() - size) / 2
    y = rect.y() + (rect.height() - size) / 2
    painter.drawPixmap(int(round(x)), int(round(y)), pm)


def icon_pixmap(name: str, color: str, size: int = 24, stroke: float = 1.8, dpr: float = 1.0) -> QPixmap:
    return pixmap(name, color, size, dpr, stroke)
