"""İki tutamaçlı aralık çubuğu (Basit: "Sadece bir kısmı"; Legend: IN/OUT). Değerler saniye.

* Fare: en yakın tutamaç sürüklenir.
* Klavye: Yukarı/Aşağı etkin tutamacı değiştirir; Sol/Sağ 1 sn (Shift ile 10 sn) oynatır.
* En az ``MIN_GAP`` saniye aralık korunur; değerler süre dışına çıkamaz.
"""
from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...ui.theme.manager import ThemedMixin

MIN_GAP = 1.0
_PAD = 14.0
_HANDLE_R = 9.0


class RangeBar(ThemedMixin, QWidget):
    valuesChanged = Signal(float, float)        # (başlangıç, bitiş) saniye

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._drag = ""                         # "start" | "end" | ""
        self._active = "end"                    # klavyeyle oynanacak tutamaç
        self.setFixedHeight(44)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bind_theme()

    # -- değerler ----------------------------------------------------------
    def duration(self) -> float:
        return self._duration

    def values(self) -> Tuple[float, float]:
        return self._start, self._end

    def set_duration(self, seconds: float) -> None:
        self._duration = max(float(seconds or 0.0), 0.0)
        self._start, self._end = 0.0, self._duration
        self.update()

    def set_values(self, start: float, end: float) -> None:
        s, e = self._clamp(float(start), float(end))
        if (s, e) != (self._start, self._end):
            self._start, self._end = s, e
            self.update()

    def _clamp(self, start: float, end: float) -> Tuple[float, float]:
        if self._duration <= MIN_GAP:
            return 0.0, self._duration
        s = min(max(start, 0.0), self._duration - MIN_GAP)
        e = min(max(end, s + MIN_GAP), self._duration)
        return s, e

    def _set_handle(self, which: str, value: float) -> None:
        s, e = self._start, self._end
        if which == "start":
            s = min(max(value, 0.0), e - MIN_GAP)
        else:
            e = min(max(value, s + MIN_GAP), self._duration)
        if self._duration <= MIN_GAP:
            s, e = 0.0, self._duration
        if (s, e) != (self._start, self._end):
            self._start, self._end = s, e
            self.update()
            self.valuesChanged.emit(s, e)

    # -- geometri ----------------------------------------------------------
    def _x_of(self, value: float) -> float:
        usable = max(self.width() - 2 * _PAD, 1.0)
        if self._duration <= 0:
            return _PAD
        return _PAD + usable * value / self._duration

    def _value_of(self, x: float) -> float:
        usable = max(self.width() - 2 * _PAD, 1.0)
        ratio = min(max((x - _PAD) / usable, 0.0), 1.0)
        return ratio * self._duration

    # -- çizim -------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 — Qt adı
        tk = self.tk
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        track = QRectF(_PAD, cy - 4, max(self.width() - 2 * _PAD, 1.0), 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tk.border))
        painter.drawRoundedRect(track, 4, 4)
        if self._duration > 0:
            x1, x2 = self._x_of(self._start), self._x_of(self._end)
            painter.setBrush(QColor(tk.primary))
            painter.drawRoundedRect(QRectF(x1, cy - 4, max(x2 - x1, 0.0), 8), 4, 4)
            painter.setPen(QPen(QColor(tk.primary), 2))
            painter.setBrush(QColor(tk.card))
            for x in (x1, x2):
                painter.drawEllipse(QRectF(x - _HANDLE_R, cy - _HANDLE_R, 2 * _HANDLE_R, 2 * _HANDLE_R))
        if self.hasFocus():
            painter.setPen(QPen(QColor(tk.primary), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 8, 8)
        painter.end()

    # -- fare --------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._duration <= 0:
            return
        x = event.position().x()
        near_start = abs(x - self._x_of(self._start)) <= abs(x - self._x_of(self._end))
        self._drag = "start" if near_start else "end"
        self._active = self._drag
        self._set_handle(self._drag, self._value_of(x))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag:
            self._set_handle(self._drag, self._value_of(event.position().x()))

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:  # noqa: N802
        self._drag = ""

    # -- klavye ------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        if key == Qt.Key.Key_Up:
            self._active = "start"
        elif key == Qt.Key.Key_Down:
            self._active = "end"
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            delta = -step if key == Qt.Key.Key_Left else step
            current = self._start if self._active == "start" else self._end
            self._set_handle(self._active, current + delta)
        else:
            super().keyPressEvent(event)
            return
        event.accept()
