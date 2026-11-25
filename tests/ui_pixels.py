"""Piksel tabanlı tema ölçümleri: sayfa zemini, tablo/liste gövdesi ve metin/zemin kontrastı (WCAG 2.x).

Ekran gerekmez: ``QT_QPA_PLATFORM=offscreen`` ile ``grab()`` kullanılır. Yardımcılar yalnızca okur; hiçbir
widget'ı değiştirmez.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QScrollArea, QStackedWidget, QWidget


def _channel(value: int) -> float:
    s = value / 255.0
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def luminance(color: QColor) -> float:
    """WCAG göreli parlaklık (0 = siyah, 1 = beyaz)."""
    return (0.2126 * _channel(color.red()) + 0.7152 * _channel(color.green())
            + 0.0722 * _channel(color.blue()))


def contrast(a: QColor, b: QColor) -> float:
    """WCAG kontrast oranı (1:1 … 21:1)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def render(widget: QWidget, width: int = 1000, height: int = 700) -> QImage:
    """Widget'ı gösterir, olayları işler ve görüntüsünü alır."""
    widget.resize(width, height)
    widget.show()
    for _ in range(2):
        QApplication.processEvents()
    return widget.grab().toImage()


#: Kendi zemini olmayan (ya da sayfa zeminiyle aynı renkte boyanan) türler. Bunların altındaki piksel sayfanın
#: zeminidir. Etiket, düğme, çerçeve, tablo ve liste gibi boyalı widget'lar bu listede yoktur.
_BACKGROUND_TYPES = (QWidget, QStackedWidget, QScrollArea)


def _deepest(page: QWidget, point: QPoint) -> QWidget:
    """``point`` (sayfa koordinatında) üzerindeki en derin widget. ``childAt`` yalnızca doğrudan çocuğu verdiği
    için zincir boyunca iner."""
    widget, local = page, point
    while True:
        child = widget.childAt(local)
        if child is None:
            return widget
        local = child.mapFrom(widget, local)
        widget = child


def _shows_background(page: QWidget, point: QPoint) -> bool:
    node = _deepest(page, point)
    while node is not None and node is not page:
        if type(node) not in _BACKGROUND_TYPES:
            return False
        node = node.parentWidget()
    return True


def background_points(page: QWidget, limit: int = 60) -> list:
    """Sayfa zemininin göründüğü noktalar (en fazla ``limit``). Kenar çizgilerinden kaçınmak için çoğunluk
    alınır (bkz. ``background_color``)."""
    found = []
    for y in range(page.height() - 2, 0, -9):
        for x in range(page.width() - 2, 0, -9):
            point = QPoint(x, y)
            if _shows_background(page, point):
                found.append(point)
                if len(found) >= limit:
                    return found
    if not found:
        raise AssertionError("sayfada zemin görünen bir nokta bulunamadı")
    return found


def background_color(image: QImage, page: QWidget, host: QWidget | None = None) -> QColor:
    """Zemin rengi: ``background_points`` üzerinde çoğunluk. ``host`` görüntünün alındığı widget'tır
    (varsayılan sayfanın kendisi; kabuk testinde ``MainWindow``)."""
    host = host or page
    counts = Counter()
    for point in background_points(page):
        mapped = page.mapTo(host, point)
        counts[image.pixelColor(mapped.x(), mapped.y()).name()] += 1
    return QColor(counts.most_common(1)[0][0])


def to_page(widget: QWidget, page: QWidget, local: QPoint) -> QPoint:
    """``widget`` yerel noktasını ``page`` koordinatına çevirir."""
    return widget.mapTo(page, local)


def color_at(image: QImage, page: QWidget, widget: QWidget, local: QPoint) -> QColor:
    point = to_page(widget, page, local)
    return image.pixelColor(point.x(), point.y())


def table_body_point(table: QWidget) -> QPoint:
    """Tablonun gövdesinde (başlığın altında, satır dışı) bir nokta: boş alanın rengi = kart zemini."""
    viewport = table.viewport()
    return QPoint(viewport.width() // 2, viewport.height() - 6)


def dominant_color(image: QImage, rect: QRect) -> QColor:
    counts = Counter(_pixels(image, rect))
    return QColor(counts.most_common(1)[0][0]) if counts else QColor()


def ink_contrast(image: QImage, rect: QRect) -> float:
    """Dikdörtgendeki metnin (zeminden en uzak, en az iki kez görünen renk) zeminle kontrastı."""
    counts = Counter(_pixels(image, rect))
    if not counts:
        return 1.0
    bg = QColor(counts.most_common(1)[0][0])
    inks = [c for c, n in counts.items() if n >= 2]
    return max((contrast(QColor(c), bg) for c in inks), default=1.0)


def _pixels(image: QImage, rect: QRect) -> Iterable[str]:
    x0, y0 = max(rect.left(), 0), max(rect.top(), 0)
    x1, y1 = min(rect.right(), image.width() - 1), min(rect.bottom(), image.height() - 1)
    for y in range(y0, y1 + 1, 2):
        for x in range(x0, x1 + 1, 2):
            yield image.pixelColor(x, y).name()


def widget_rect_in(page: QWidget, widget: QWidget) -> QRect:
    """``widget``'ın ``page`` içindeki dikdörtgeni."""
    top_left = widget.mapTo(page, QPoint(0, 0))
    return QRect(top_left, widget.size())
