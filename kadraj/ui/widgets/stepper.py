"""``Stepper`` — adım göstergesi (sihirbaz 3 adım, API rehberi 5 adım).

Numaralı daireler ve aralarında çizgi; tamamlanan adımlar dolu, geçerli adım vurgulu.
``set_current`` 0 tabanlıdır. Yalnızca gösterir; gezinti kabuğun işidir.
"""
from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import fonts
from ..theme.manager import ThemedMixin


class Stepper(ThemedMixin, QWidget):
    def __init__(self, steps: int = 3, current: int = 0, labels: Sequence[str] = (),
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._n = max(1, steps)
        self._cur = current
        self._labels = list(labels)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bind_theme()
        self._on_theme()

    def set_current(self, idx: int) -> None:
        self._cur = max(0, min(self._n - 1, idx))
        self.setAccessibleDescription(f"{self._cur + 1} / {self._n}")
        self.update()

    @property
    def current(self) -> int:
        return self._cur

    @property
    def steps(self) -> int:
        return self._n

    def _on_theme(self) -> None:
        self.setFixedHeight(self.tk.px(34) + (self.tk.px(18) if self._labels else 0))

    def sizeHint(self) -> QSize:
        return QSize(self.tk.px(48) * self._n, self.height())

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = tk.px(30)
        w = self.width()
        gap = (w - d) / max(1, self._n - 1) if self._n > 1 else 0
        cy = d / 2 + 1
        for i in range(self._n):
            cx = d / 2 + i * gap
            if i < self._n - 1:
                done = i < self._cur
                p.setPen(QPen(QColor(tk.primary if done else tk.border), max(2, tk.border_w)))
                p.drawLine(QPointF(cx + d / 2, cy), QPointF(cx + gap - d / 2, cy))
            active, done = i == self._cur, i < self._cur
            p.setPen(Qt.PenStyle.NoPen if (active or done) else QPen(QColor(tk.border), max(2, tk.border_w)))
            p.setBrush(QColor(tk.primary if (active or done) else tk.card))
            p.drawEllipse(QRectF(cx - d / 2, cy - d / 2, d, d))
            f = fonts.qfont(tk.font_ui, tk.ui_px * 0.85, weight=800)
            p.setFont(f)
            p.setPen(QColor(tk.on_primary if (active or done) else tk.text2))
            p.drawText(QRectF(cx - d / 2, cy - d / 2, d, d), Qt.AlignmentFlag.AlignCenter, str(i + 1))
            if self._labels and i < len(self._labels):
                lf = fonts.qfont(tk.font_ui, tk.ui_px * 0.78, weight=700)
                p.setFont(lf)
                p.setPen(QColor(tk.text if active else tk.text2))
                fm = QFontMetrics(lf)
                text = fm.elidedText(self._labels[i], Qt.TextElideMode.ElideRight, int(gap if gap else w))
                p.drawText(QPointF(cx - fm.horizontalAdvance(text) / 2, cy + d / 2 + fm.ascent() + 3), text)
        p.end()
