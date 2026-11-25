"""``PreviewTooltip`` — fare üzerindeyken küçük önizleme + iki satır metin gösteren açılır balon (klip / kitaplık ögesi için)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from ..theme.manager import ThemedMixin
from .. import fonts


class PreviewTooltip(ThemedMixin, QWidget):
    THUMB = QSize(160, 90)

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setProperty("no_crossfade", True)
        self._title = ""
        self._sub = ""
        self._thumb: Optional[QPixmap] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._bind_theme()

    @property
    def title(self) -> str:
        return self._title

    @property
    def subtitle(self) -> str:
        return self._sub

    def set_content(self, title: str, subtitle: str = "", image: "QImage | QPixmap | None" = None) -> None:
        self._title, self._sub = title, subtitle
        pm = QPixmap.fromImage(image) if isinstance(image, QImage) else image
        self._thumb = pm.scaled(self.THUMB, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation) if pm else None
        tk = self.tk
        f1, f2 = fonts.qfont(tk.font_ui, tk.ui_px, 700), fonts.qfont(tk.font_ui, tk.ui_px * 0.9)
        w = max(QFontMetrics(f1).horizontalAdvance(title), QFontMetrics(f2).horizontalAdvance(subtitle), self.THUMB.width() if self._thumb else 0)
        h = tk.px(14) * 2 + QFontMetrics(f1).height() + (QFontMetrics(f2).height() if subtitle else 0) + (self.THUMB.height() + 6 if self._thumb else 0)
        self.resize(w + tk.px(24), h)
        self.update()

    def show_at(self, global_pos: QPoint, ms: int = 4000) -> None:
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        x, y = global_pos.x() + 14, global_pos.y() + 18
        if geo is not None:
            x = min(x, geo.right() - self.width())
            y = min(y, geo.bottom() - self.height())
        self.move(x, y)
        self.show()
        self._timer.start(ms)

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), tk.radius_card, tk.radius_card)
        p.fillPath(path, QColor(tk.card))
        p.setPen(tk.border)
        p.drawPath(path)
        y = tk.px(10)
        if self._thumb:
            p.drawPixmap(tk.px(12), y, self._thumb)
            y += self._thumb.height() + 6
        p.setPen(tk.text)
        p.setFont(fonts.qfont(tk.font_ui, tk.ui_px, 700))
        fm = p.fontMetrics()
        p.drawText(tk.px(12), y + fm.ascent(), self._title)
        if self._sub:
            p.setPen(tk.text2)
            p.setFont(fonts.qfont(tk.font_ui, tk.ui_px * 0.9))
            p.drawText(tk.px(12), y + fm.height() + p.fontMetrics().ascent(), self._sub)
