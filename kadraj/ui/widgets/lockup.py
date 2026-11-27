"""``Lockup`` — KADRAJ wordmark + ``from İnsibağ`` imzası. **Markanın tek çizim noktası.**

Plan §0.1: imza, wordmark boyunun ~0.30 katı (en az 9 px), %60 opaklık, Instrument Serif
İtalik; sağ kenarı "J" harfinin sağ kenarıyla hizalı ve wordmark'ın sağ altında durur.
Wordmark: Archivo 800, genişlik %125, harf aralığı 0.12 em.

Başka hiçbir widget "KADRAJ" yazmaz; markayı göstermek isteyen ``Lockup`` ekler.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ... import branding
from .. import fonts
from ..theme.manager import ThemedMixin


def signature_px(wordmark_px: float) -> int:
    """İmza boyu: ``wordmark × 0.30``, en az 9 px (marka kuralı)."""
    return max(branding.SIGNATURE_MIN_PX, int(round(wordmark_px * branding.SIGNATURE_RATIO)))


class Lockup(ThemedMixin, QWidget):
    """``Lockup(wordmark_px=44)`` — ``show_mark=True`` ise solda vizör işareti de çizilir.

    ``color`` verilmezse tema metin rengi; işaret rengi Basit'te şeftali-koyu, Legend'de altındır.
    """

    def __init__(self, wordmark_px: int = 28, *, show_mark: bool = False,
                 color: "str | QColor | None" = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._px = int(wordmark_px)
        self._show_mark = show_mark
        self._color = QColor(color) if color else None
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAccessibleName(f"{branding.APP_NAME} {branding.SIGNATURE}")
        self._bind_theme()
        self._relayout()

    # -- API --------------------------------------------------------------
    def set_wordmark_px(self, px: int) -> None:
        self._px = int(px)
        self._relayout()
        self.update()

    def set_show_mark(self, show: bool) -> None:
        self._show_mark = bool(show)
        self._relayout()
        self.update()

    @property
    def wordmark_px(self) -> int:
        return self._px

    @property
    def signature_px(self) -> int:
        return signature_px(self._px)

    # -- ölçüler ----------------------------------------------------------
    def _wordmark_font(self):
        # Archivo 800, genişlik %125 → gerçek "Archivo Expanded ExtraBold" örneği
        return fonts.qfont("Archivo Expanded", self._px, weight=800,
                           letter_spacing_em=branding.WORDMARK_TRACKING_EM)

    def _signature_font(self):
        return fonts.qfont("Instrument Serif", self.signature_px, weight=400, italic=True)

    def _metrics(self) -> dict:
        wf = self._wordmark_font()
        fm = QFontMetricsF(wf)
        track = branding.WORDMARK_TRACKING_EM * self._px
        text_w = fm.horizontalAdvance(branding.APP_NAME) - track   # sondaki harf aralığını at: "J"nin gerçek sağ kenarı
        sf = self._signature_font()
        sfm = QFontMetricsF(sf)
        mark = self._px * 0.9 if self._show_mark else 0.0
        gap = self._px * 0.30 if self._show_mark else 0.0
        word_h = fm.ascent() + fm.descent()
        sig_gap = max(1.0, self._px * 0.05)
        total_w = mark + gap + text_w
        total_h = word_h * 0.86 + sig_gap + sfm.height()
        return dict(fm=fm, sfm=sfm, text_w=text_w, mark=mark, gap=gap, word_h=word_h,
                    sig_gap=sig_gap, total_w=total_w, total_h=total_h, wf=wf, sf=sf)

    def _relayout(self) -> None:
        m = self._metrics()
        self.setFixedSize(int(m["total_w"]) + 2, int(m["total_h"]) + 2)

    def _on_theme(self) -> None:
        self._relayout()

    def sizeHint(self) -> QSize:
        return self.size()

    # -- boyama -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        m = self._metrics()
        tk = self.tk
        color = self._color or QColor(tk.text)
        mark_color = QColor(tk.primary_deep if not tk.is_legend else tk.primary)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        x0 = 1.0
        if self._show_mark:
            self._paint_mark(p, QRectF(x0, 0, m["mark"], m["mark"]), mark_color)
            x0 += m["mark"] + m["gap"]
        # wordmark
        p.setFont(m["wf"])
        p.setPen(color)
        base_y = 1.0 + m["fm"].ascent() * 0.93
        p.drawText(QPointF(x0, base_y), branding.APP_NAME)
        right = x0 + m["text_w"]                      # "J"nin sağ kenarı
        # imza: sağ kenarı J ile hizalı, %60 opaklık
        sig = branding.SIGNATURE
        sw = m["sfm"].horizontalAdvance(sig)
        sig_color = QColor(color)
        sig_color.setAlphaF(branding.SIGNATURE_OPACITY)
        p.setFont(m["sf"])
        p.setPen(sig_color)
        sig_base = base_y + m["fm"].descent() * 0.35 + m["sig_gap"] + m["sfm"].ascent()
        p.drawText(QPointF(right - sw, sig_base), sig)
        p.end()

    @staticmethod
    def _paint_mark(p: QPainter, r: QRectF, color: QColor) -> None:
        """Vizör işareti: dört köşe + merkez nokta (tuvaldeki SVG'nin karşılığı)."""
        s = r.width() / 24.0
        pen = QPen(color, 1.7 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        def pt(x: float, y: float) -> QPointF:
            return QPointF(r.x() + x * s, r.y() + y * s)

        for a, b, c in (((3, 8), (3, 4), (7, 4)), ((17, 4), (21, 4), (21, 8)),
                        ((21, 16), (21, 20), (17, 20)), ((7, 20), (3, 20), (3, 16))):
            p.drawLine(pt(*a), pt(*b))
            p.drawLine(pt(*b), pt(*c))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(pt(12, 12), 2.6 * s, 2.6 * s)
