"""``TaskCard`` — Basit Mod ana ekranının büyük görev kartı (İndir, Kaydet, Kurgula…).

Basit'te: modül pastel tonunda yuvarlak kart, köşede büyük renkli "blob", ikon, başlık, açıklama,
isteğe bağlı rozet (".wav"). Üzerine gelince hafif yukarı kalkar, basınca ezilir. Legend'de aynı
widget keskin, sol vurgu çizgili bir panel satırına dönüşür. ``soon=True`` ise "yakında" etiketi çıkar
ve tıklama yine ``clicked`` yayınlar (kabuk "yakında" sayfasını açar).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QPropertyAnimation, QRectF, QSize, Qt, Property, QEasingCurve
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from ...core import i18n
from ...core.contracts import Accent
from .. import fonts
from ..theme.manager import ThemedMixin, active_manager
from .icons import draw_icon


class TaskCard(ThemedMixin, QAbstractButton):
    def __init__(self, title: str, desc: str, icon: str, accent: "Accent | str" = Accent.PEACH, *,
                 chip: str = "", soon: bool = False, shortcut: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._title, self._desc, self._icon, self._accent = title, desc, icon, accent
        self._chip, self._soon, self._shortcut = chip, soon, shortcut
        self._lift = 0.0
        self._anim = QPropertyAnimation(self, b"lift", self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(title)
        self.setAccessibleDescription(desc)
        self._bind_theme()
        self._on_theme()

    # -- API --------------------------------------------------------------
    def set_texts(self, title: str, desc: str, chip: str = "") -> None:
        self._title, self._desc, self._chip = title, desc, chip
        self.setAccessibleName(title)
        self.setAccessibleDescription(desc)
        self.update()

    @property
    def soon(self) -> bool:
        return self._soon

    @property
    def title(self) -> str:
        return self._title

    # -- animasyon --------------------------------------------------------
    def _get_lift(self) -> float:
        return self._lift

    def _set_lift(self, v: float) -> None:
        self._lift = v
        self.update()

    lift = Property(float, _get_lift, _set_lift)

    def _to(self, target: float) -> None:
        mgr = active_manager()
        if mgr is not None and mgr.transition_ms == 0:
            self._set_lift(target)
            return
        self._anim.stop()
        self._anim.setDuration(40 if (mgr and mgr.reduced_motion) else 160)
        self._anim.setStartValue(self._lift)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    def enterEvent(self, e) -> None:
        self._to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._to(0.0)
        super().leaveEvent(e)

    # -- ölçü -------------------------------------------------------------
    def _on_theme(self) -> None:
        tk = self.tk
        self.setFixedHeight(tk.px(188) if not tk.is_legend else tk.px(56))
        self.setMinimumWidth(tk.px(220) if not tk.is_legend else tk.px(180))

    def sizeHint(self) -> QSize:
        return QSize(self.minimumWidth() + 40, self.height())

    # -- boyama -----------------------------------------------------------
    def paintEvent(self, _e) -> None:
        tk = self.tk
        acc = tk.accent(self._accent)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(3, 5, -3, -3)
        rect.translate(0, -4.0 * self._lift)
        if self.isDown():
            c = rect.center()
            p.translate(c)
            p.scale(0.985, 0.97)
            p.translate(-c)
        if tk.is_legend:
            self._paint_legend(p, rect, acc)
        else:
            self._paint_simple(p, rect, acc)
        p.end()

    def _paint_simple(self, p: QPainter, rect: QRectF, acc) -> None:
        tk = self.tk
        radius = tk.radius_card + 4
        if tk.shadows:                                               # yumuşak gölge
            for i, a in enumerate((18, 12, 7)):
                sh = rect.adjusted(-i * 1.5, i * 2.0 + 2, i * 1.5, i * 2.0 + 2)
                sp = QPainterPath()
                sp.addRoundedRect(sh, radius + i, radius + i)
                c = QColor(60, 40, 30, a)
                p.fillPath(sp, c)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, QColor(acc.tone))
        if tk.high_contrast:
            p.setPen(QPen(QColor(tk.text), tk.border_w))
            p.drawPath(path)
        p.save()
        p.setClipPath(path)
        blob = QRectF(rect.right() - rect.height() * 0.55, rect.top() - rect.height() * 0.28,
                      rect.height() * 0.85, rect.height() * 0.85)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(acc.base))
        p.setOpacity(0.55)
        p.drawEllipse(blob)
        p.setOpacity(1.0)
        p.restore()
        pad = 22.0
        # ikon yuvarlağı
        isz = tk.px(52)
        icon_rect = QRectF(rect.left() + pad, rect.top() + pad, isz, isz)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(acc.base))
        p.drawEllipse(icon_rect)
        draw_icon(p, icon_rect.adjusted(isz * 0.22, isz * 0.22, -isz * 0.22, -isz * 0.22),
                  self._icon, acc.ink, stroke=2.0)
        # başlık
        tfont = fonts.qfont(tk.font_display, tk.ui_px * 1.35, weight=800)
        p.setFont(tfont)
        p.setPen(QColor(acc.ink))
        tfm = QFontMetrics(tfont)
        ty = icon_rect.bottom() + pad * 0.55 + tfm.ascent()
        p.drawText(QPointF(rect.left() + pad, ty), self._title)
        # açıklama
        dfont = fonts.qfont(tk.font_ui, tk.ui_px * 0.92, weight=600)
        p.setFont(dfont)
        p.setPen(QColor(tk.text2 if not tk.high_contrast else tk.text))
        dr = QRectF(rect.left() + pad, ty + 6, rect.width() - 2 * pad, rect.bottom() - ty - pad * 0.5)
        p.drawText(dr, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                   self._desc)
        self._paint_badges(p, rect, acc, pad)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(tk.text if tk.high_contrast else tk.primary_deep), 3))
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)

    def _paint_badges(self, p: QPainter, rect: QRectF, acc, pad: float) -> None:
        tk = self.tk
        bfont = fonts.qfont(tk.font_ui, tk.ui_px * 0.78, weight=800)
        bfm = QFontMetrics(bfont)
        labels = []
        if self._chip:
            labels.append(self._chip)
        if self._soon:
            labels.append(i18n.t("home.soon"))
        x = rect.right() - pad
        for label in labels:
            w = bfm.horizontalAdvance(label) + 18
            r = QRectF(x - w, rect.bottom() - pad - bfm.height() - 4, w, bfm.height() + 6)
            path = QPainterPath()
            path.addRoundedRect(r, r.height() / 2, r.height() / 2)
            p.fillPath(path, QColor(acc.ink))
            p.setFont(bfont)
            p.setPen(QColor(acc.tone))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
            x -= w + 8

    def _paint_legend(self, p: QPainter, rect: QRectF, acc) -> None:
        tk = self.tk
        path = QPainterPath()
        path.addRoundedRect(rect, tk.radius_card, tk.radius_card)
        p.fillPath(path, QColor(tk.surface2 if self.underMouse() else tk.card))
        p.setPen(QPen(QColor(tk.border), tk.border_w))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(acc.base))
        p.drawRect(QRectF(rect.left(), rect.top(), 3, rect.height()))
        draw_icon(p, QRectF(rect.left() + 14, rect.center().y() - 10, 20, 20), self._icon, acc.base, stroke=1.8)
        p.setFont(fonts.qfont(tk.font_ui, tk.ui_px, weight=600))
        p.setPen(QColor(tk.text))
        fm = QFontMetrics(p.font())
        p.drawText(QPointF(rect.left() + 44, rect.center().y() + (fm.ascent() - fm.descent()) / 2), self._title)
        if self._shortcut:
            mf = fonts.qfont(tk.font_mono, tk.ui_px * 0.85, weight=500)
            p.setFont(mf)
            p.setPen(QColor(tk.text2))
            p.drawText(QRectF(rect.left(), rect.top(), rect.width() - 12, rect.height()),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), self._shortcut)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(tk.primary), 2))
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), tk.radius_card, tk.radius_card)
