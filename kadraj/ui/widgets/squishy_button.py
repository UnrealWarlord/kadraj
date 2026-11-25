"""``SquishyButton`` — basınca "yumuşak ezilen" buton (Basit Mod'un imzası).

Basit'te hap şekilli, basınca yatayda genişleyip dikeyde ezilen (``QPropertyAnimation``);
Legend'de keskin köşeli ve sadece 1 px aşağı kayan düz buton. Tokenlardan boyanır, tema
değişince kendini günceller. Varyantlar: ``primary`` (dolgulu), ``secondary`` (çerçeveli),
``ghost`` (çerçevesiz), ``danger``; ``accent`` verilirse o modül rengiyle dolar.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                            Property)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from ...core.contracts import Accent
from .. import fonts
from ..theme.manager import ThemedMixin
from ..theme.tokens import mix
from .icons import draw_icon

VARIANTS = ("primary", "secondary", "ghost", "danger")


class SquishyButton(ThemedMixin, QPushButton):
    def __init__(self, text: str = "", *, variant: str = "primary", icon: str = "",
                 accent: "Accent | str | None" = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._variant = variant if variant in VARIANTS else "primary"
        self._icon = icon
        self._accent = accent
        self._squish = 0.0
        self._anim = QPropertyAnimation(self, b"squish", self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.pressed.connect(lambda: self._animate(1.0, 90))
        self.released.connect(lambda: self._animate(0.0, 260, spring=True))
        self._bind_theme()
        self._apply_height()

    # -- API --------------------------------------------------------------
    def set_variant(self, variant: str) -> None:
        self._variant = variant if variant in VARIANTS else "primary"
        self._apply_height()
        self.update()

    def set_icon_name(self, name: str) -> None:
        self._icon = name
        self.updateGeometry()
        self.update()

    def set_accent(self, accent: "Accent | str | None") -> None:
        self._accent = accent
        self.update()

    @property
    def variant(self) -> str:
        return self._variant

    # -- animasyon --------------------------------------------------------
    def _get_squish(self) -> float:
        return self._squish

    def _set_squish(self, v: float) -> None:
        self._squish = v
        self.update()

    squish = Property(float, _get_squish, _set_squish)

    def _animate(self, target: float, ms: int, spring: bool = False) -> None:
        from ..theme.manager import active_manager
        mgr = active_manager()
        if mgr is not None and mgr.transition_ms == 0:      # animasyonlar kapalı (test)
            self._set_squish(target)
            return
        self._anim.stop()
        self._anim.setDuration(ms if not (mgr and mgr.reduced_motion) else 40)
        self._anim.setStartValue(self._squish)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack if spring else QEasingCurve.Type.OutCubic)
        self._anim.start()

    # -- ölçü -------------------------------------------------------------
    def _apply_height(self) -> None:
        tk = self.tk
        h = tk.btn_h if self._variant == "primary" else max(tk.ctl_h, round(tk.btn_h * 0.86))
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)

    def _on_theme(self) -> None:
        self._apply_height()
        self.updateGeometry()

    def _font(self):
        tk = self.tk
        weight = 800 if (tk.pill and self._variant == "primary") else (700 if tk.pill else 600)
        return fonts.qfont(tk.font_ui, tk.ui_px * (1.0 if tk.pill else 1.0), weight=weight)

    def sizeHint(self) -> QSize:
        tk = self.tk
        fm = QFontMetrics(self._font())
        icon_w = (tk.ui_px + 8) if self._icon else 0
        pad = round(tk.ctl_h * (0.75 if tk.pill else 0.55))
        h = self.maximumHeight()
        return QSize(fm.horizontalAdvance(self.text()) + icon_w + 2 * pad, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # -- renkler ----------------------------------------------------------
    def _colors(self):
        tk = self.tk
        enabled = self.isEnabled()
        hover = self.underMouse() and enabled
        down = self.isDown()
        if self._variant == "primary":
            base = tk.accent(self._accent).base if self._accent else tk.primary
            deep = mix(base, "#000000", 0.12)
            bg = deep if down else (mix(base, "#FFFFFF", 0.12) if hover else base)
            fg = tk.on_primary
            border = None
        elif self._variant == "danger":
            bg = mix(tk.bad, "#000000", 0.12) if down else tk.bad
            fg = "#FFFFFF" if not tk.is_legend else "#0C0D10"
            border = None
        elif self._variant == "secondary":
            bg = tk.border if down else (tk.surface2 if hover else tk.card)
            fg = tk.text
            border = tk.border if not tk.high_contrast else tk.text
        else:  # ghost
            bg = tk.surface2 if (down or hover) else None
            fg = tk.text
            border = None
        if not enabled:
            bg = tk.surface2 if bg else None
            fg = tk.text2
        return bg, fg, border

    # -- boyama -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        tk = self.tk
        bg, fg, border = self._colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if tk.pill:                                           # yumuşak ezilme
            sx = 1.0 + 0.035 * self._squish
            sy = 1.0 - 0.09 * self._squish
            cx, cy = rect.center().x(), rect.center().y()
            p.translate(cx, cy)
            p.scale(sx, sy)
            p.translate(-cx, -cy)
        else:                                                 # Legend: 1 px aşağı
            rect.translate(0, 1.0 * self._squish)
        radius = rect.height() / 2 if tk.pill else tk.radius_ctl
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        if bg:
            p.fillPath(path, QColor(bg))
        if border:
            p.setPen(QPen(QColor(border), tk.border_w))
            p.drawPath(path)
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            ring = QPainterPath()
            ring.addRoundedRect(rect.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
            p.setPen(QPen(QColor(tk.text if tk.high_contrast else tk.primary_deep), max(2, tk.border_w)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(ring)
        # içerik
        p.setFont(self._font())
        p.setPen(QColor(fg))
        fm = QFontMetrics(self._font())
        text_w = fm.horizontalAdvance(self.text())
        icon_sz = tk.ui_px + 2
        total = text_w + ((icon_sz + 8) if self._icon else 0)
        x = rect.center().x() - total / 2
        if self._icon:
            draw_icon(p, QRectF(x, rect.center().y() - icon_sz / 2, icon_sz, icon_sz), self._icon, fg,
                      stroke=2.0)
            x += icon_sz + 8
        ty = rect.center().y() + (fm.ascent() - fm.descent()) / 2
        p.drawText(QPointF(x, ty), self.text())
        p.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)
