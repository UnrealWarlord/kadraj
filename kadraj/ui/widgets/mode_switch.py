"""``ModeSwitch`` — üst çubuktaki canlı Basit ↔ Legend anahtarı.

Kaydıran bir "baş parmak" (thumb) iki etiket arasında gezinir; renk, hedef modun birincil
rengine doğru karışır (şeftali → altın). Yalnızca ``modeSelected(str)`` yayınlar; asıl geçişi
``AppContext.set_ui_mode`` yapar ve durum korunur. Klavye: ←/→/Boşluk/Enter. Erişilebilir ad ve değer içerir.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                            Property, Signal)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core import i18n
from ...core.contracts import UiMode
from .. import fonts
from ..theme import tokens as T
from ..theme.manager import ThemedMixin, active_manager


class ModeSwitch(ThemedMixin, QWidget):
    modeSelected = Signal(str)            # kullanıcı bir mod seçince ("simple" | "legend")

    def __init__(self, mode: "UiMode | str" = UiMode.SIMPLE, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._short = False                 # dar üst çubuk (dikey tablet): Basit'te de kısa adlar
        self._mode = UiMode.coerce(mode)
        self._pos = 0.0 if self._mode is UiMode.SIMPLE else 1.0
        self._anim = QPropertyAnimation(self, b"position", self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._bind_theme()
        self._refresh_a11y()
        self._resize()

    # -- API --------------------------------------------------------------
    @property
    def mode(self) -> UiMode:
        return self._mode

    def set_mode(self, mode: "UiMode | str", animate: bool = True) -> None:
        """Programatik değişiklik (sinyal yaymaz)."""
        new = UiMode.coerce(mode)
        if new is self._mode and abs(self._pos - (1.0 if new is UiMode.LEGEND else 0.0)) < 1e-6:
            return
        self._mode = new
        self._slide(1.0 if new is UiMode.LEGEND else 0.0, animate)
        self._refresh_a11y()

    def set_short(self, short: bool) -> None:
        """Dar üst çubukta (dikey tablet) Basit'te de kısa adlar: "Basit" / "Legend"."""
        if short != self._short:
            self._short = short
            self._resize()
            self.update()

    def _select(self, mode: UiMode) -> None:
        if mode is self._mode:
            return
        self.set_mode(mode)
        self.modeSelected.emit(mode.value)

    # -- animasyon --------------------------------------------------------
    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    position = Property(float, _get_pos, _set_pos)

    def _slide(self, target: float, animate: bool) -> None:
        mgr = active_manager()
        self._anim.stop()
        if not animate or (mgr is not None and mgr.transition_ms == 0):
            self._set_pos(target)
            return
        self._anim.setDuration(120 if (mgr and mgr.reduced_motion) else 260)
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.start()

    # -- ölçü / metin -----------------------------------------------------
    def _labels(self):
        tk = self.tk
        if tk.is_legend:
            return i18n.upper(i18n.t("mode.simple_short")), i18n.upper(i18n.t("mode.legend_short"))
        if self._short:
            return i18n.t("mode.simple_short"), i18n.t("mode.legend_short")
        return i18n.t("mode.simple"), i18n.t("mode.legend")

    def _font(self):
        tk = self.tk
        size = tk.ui_px * (0.92 if not tk.is_legend else 0.85)
        return fonts.qfont(tk.font_ui, size, weight=800 if not tk.is_legend else 700,
                           letter_spacing_em=0.0 if not tk.is_legend else 0.06)

    def _resize(self) -> None:
        tk = self.tk
        fm = QFontMetrics(self._font())
        a, b = self._labels()
        seg = max(fm.horizontalAdvance(a), fm.horizontalAdvance(b)) + round(tk.ctl_h * 1.0)
        h = tk.ctl_h if not tk.is_legend else max(tk.ctl_h, 24)
        self.setFixedSize(2 * seg + 6, h)

    def _on_theme(self) -> None:
        self._resize()
        self._refresh_a11y()

    def _refresh_a11y(self) -> None:
        self.setAccessibleName(i18n.t("mode.switch_label"))
        self.setAccessibleDescription(i18n.t("mode.simple") if self._mode is UiMode.SIMPLE
                                      else i18n.t("mode.legend"))
        self.setToolTip(i18n.t("mode.switch_tip"))

    def sizeHint(self) -> QSize:
        return self.size()

    # -- etkileşim --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            half = self.width() / 2
            self._select(UiMode.SIMPLE if event.position().x() < half else UiMode.LEGEND)
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._select(UiMode.SIMPLE)
        elif key == Qt.Key.Key_Right:
            self._select(UiMode.LEGEND)
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._select(UiMode.LEGEND if self._mode is UiMode.SIMPLE else UiMode.SIMPLE)
        else:
            super().keyPressEvent(event)

    # -- boyama -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        r = outer.height() / 2 if tk.pill else tk.radius_ctl
        track = QPainterPath()
        track.addRoundedRect(outer, r, r)
        p.fillPath(track, QColor(tk.surface2))
        p.setPen(QPen(QColor(tk.border if not tk.high_contrast else tk.text), tk.border_w))
        p.drawPath(track)

        # baş parmak: iki modun birincil rengi arasında karışır
        sc, lc = T.SIMPLE.primary, T.LEGEND.primary
        thumb_color = QColor(T.mix(sc, lc, self._pos))
        if tk.high_contrast:
            thumb_color = QColor(tk.primary)
        inset = 3
        seg_w = (outer.width() - 2 * inset) / 2
        x = outer.x() + inset + seg_w * self._pos
        thumb = QRectF(x, outer.y() + inset, seg_w, outer.height() - 2 * inset)
        tr = thumb.height() / 2 if tk.pill else max(1, tk.radius_ctl - 1)
        tp = QPainterPath()
        tp.addRoundedRect(thumb, tr, tr)
        p.fillPath(tp, thumb_color)

        p.setFont(self._font())
        fm = QFontMetrics(self._font())
        a, b = self._labels()
        for idx, (label, left) in enumerate(((a, outer.x() + inset), (b, outer.x() + inset + seg_w))):
            active = (idx == 0 and self._pos < 0.5) or (idx == 1 and self._pos >= 0.5)
            if active:
                col = QColor(T.SIMPLE.on_primary if idx == 0 else T.LEGEND.on_primary)
                if tk.high_contrast:
                    col = QColor(tk.on_primary)
            else:
                col = QColor(tk.text2)
            p.setPen(col)
            tw = fm.horizontalAdvance(label)
            ty = outer.center().y() + (fm.ascent() - fm.descent()) / 2
            p.drawText(QPointF(left + (seg_w - tw) / 2, ty), label)

        if self.hasFocus():
            ring = QPainterPath()
            ring.addRoundedRect(outer.adjusted(-1, -1, 1, 1), r + 1, r + 1)
            p.setPen(QPen(QColor(tk.text if tk.high_contrast else tk.primary_deep), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(ring)
        p.end()
