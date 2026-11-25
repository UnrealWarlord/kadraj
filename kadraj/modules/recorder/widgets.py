"""Ses Kayıt Stüdyosu'na özel widget'lar (tokenlardan boyanır; iki modu da destekler). Durum tutmazlar: değerleri
sayfa ``set_*`` ile verir, teleprompter ise servisteki ``Prompter`` nesnesini okur."""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from ...ui import fonts
from ...ui.theme.manager import ThemedMixin
from ...ui.theme.tokens import mix
from ...ui.widgets.icons import draw_icon
from .dsp import FLOOR_DB
from .teleprompter import Prompter

# Basit Mod VU renkleri (tuval): nane → tereyağı → şeftali → pembe; sönük segment
SIMPLE_SEGMENTS = ("#9FE0C1", "#FFD97A", "#FFB38A", "#FF8FA3")
SIMPLE_UNLIT = "#F1E3CF"


def db_to_fraction(db: float, floor: float = -60.0) -> float:
    return max(0.0, min(1.0, (db - floor) / (0.0 - floor)))


def format_timecode(seconds: float, fps: int = 30) -> str:
    """``HH:MM:SS:FF`` (Legend zaman kodu)."""
    total = max(0.0, seconds)
    whole = int(total)
    frames = int((total - whole) * fps)
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}:{frames:02d}"


def format_clock(seconds: float) -> str:
    """``MM:SS`` (Basit dev süre); bir saati aşarsa ``H:MM:SS``."""
    whole = int(max(0.0, seconds))
    if whole >= 3600:
        return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}"
    return f"{whole // 60:02d}:{whole % 60:02d}"


# ---------------------------------------------------------------------------
class SegmentedVU(ThemedMixin, QWidget):
    """Basit Mod'un pastel 20 segmentli yatay VU'su (kanal başına bir satır)."""

    SEGMENTS = 20

    def __init__(self, channels: int = 2, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ch = channels
        self._db: List[float] = [FLOOR_DB] * channels
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bind_theme()
        self._on_theme()

    def set_channels(self, ch: int) -> None:
        self._ch = max(1, ch)
        self._db = (self._db + [FLOOR_DB] * self._ch)[:self._ch]
        self._on_theme()
        self.update()

    def set_levels(self, db: Sequence[float]) -> None:
        vals = list(db)[:self._ch] or [FLOOR_DB]
        vals += [vals[-1]] * (self._ch - len(vals))
        if vals != self._db:
            self._db = vals
            self.update()

    @property
    def lit_segments(self) -> List[int]:
        return [int(round(db_to_fraction(d) * self.SEGMENTS)) for d in self._db]

    def _on_theme(self) -> None:
        self.setFixedHeight(self._ch * self.tk.px(22) + (self._ch - 1) * self.tk.px(6))
        self.setAccessibleName("VU")

    def sizeHint(self) -> QSize:
        return QSize(self.tk.px(320), self.height())

    def _seg_color(self, i: int) -> QColor:
        if self.tk.high_contrast:
            return QColor(self.tk.text)
        group = 0 if i < 12 else (1 if i < 16 else (2 if i < 18 else 3))
        return QColor(SIMPLE_SEGMENTS[group])

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        row_h = self.tk.px(22)
        gap = max(3, self.tk.px(4))
        n = self.SEGMENTS
        seg_w = (self.width() - gap * (n - 1)) / n
        unlit = QColor(SIMPLE_UNLIT if not tk.is_legend else tk.surface2)
        for r, lit in enumerate(self.lit_segments):
            y = r * (row_h + self.tk.px(6))
            for i in range(n):
                rect = QRectF(i * (seg_w + gap), y, seg_w, row_h)
                on = i < lit
                p.setPen(QPen(QColor(tk.text), 1) if (tk.high_contrast and not on) else Qt.PenStyle.NoPen)
                p.setBrush(self._seg_color(i) if on else unlit)
                p.drawRoundedRect(rect, min(seg_w, row_h) * 0.3, min(seg_w, row_h) * 0.3)
        p.end()


class StereoMeter(ThemedMixin, QWidget):
    """Legend dikey stereo metre: 32 segment, dB ölçeği, tepe tutma işaretleri, clipping kutusu."""

    TICKS = (-6, -12, -18, -24, -36, -48, -60)
    SEGMENTS = 32

    def __init__(self, channels: int = 2, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ch = channels
        self._db = [FLOOR_DB] * channels
        self._hold = [FLOOR_DB] * channels
        self._clip = False
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(220)
        self._bind_theme()
        self._on_theme()

    def set_levels(self, peak_db: Sequence[float], hold_db: Sequence[float] = (), clipping: bool = False) -> None:
        peaks = (list(peak_db) + [FLOOR_DB] * self._ch)[:self._ch]
        holds = (list(hold_db) + peaks)[:self._ch] if hold_db else peaks
        if (peaks, holds, clipping) != (self._db, self._hold, self._clip):
            self._db, self._hold, self._clip = peaks, holds, bool(clipping)
            self.update()

    def lit(self, ch: int) -> int:
        return int(round(db_to_fraction(self._db[ch]) * self.SEGMENTS))

    def _on_theme(self) -> None:
        self.setFixedWidth(self.tk.px(96))

    def _color(self, db: float) -> QColor:
        tk = self.tk
        return QColor(tk.good if db < -18 else (tk.primary if db < -6 else tk.bad))

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top, bottom = self.tk.px(22), self.tk.px(18)
        h = self.height() - top - bottom
        label_w = self.tk.px(28)
        bar_w = (self.width() - label_w - self.tk.px(6)) / self._ch - self.tk.px(4)
        n = self.SEGMENTS
        seg_h = h / n
        f = fonts.qfont(tk.font_mono, tk.ui_px * 0.75, weight=400)
        p.setFont(f)
        p.setPen(QColor(tk.text2))
        for t in self.TICKS:                                          # dB ölçeği
            y = top + h * (1.0 - db_to_fraction(t))
            p.drawText(QRectF(0, y - 8, label_w - 2, 16), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                       str(t))
        for c in range(self._ch):
            x = label_w + self.tk.px(6) + c * (bar_w + self.tk.px(4))
            lit = self.lit(c)
            for k in range(n):                                        # k=0 en alt
                db = -60.0 + k * 60.0 / (n - 1)
                rect = QRectF(x, top + h - (k + 1) * seg_h + 1, bar_w, seg_h - 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self._color(db) if k < lit else QColor(tk.surface2))
                p.drawRect(rect)
            hold_k = int(round(db_to_fraction(self._hold[c]) * (n - 1)))
            if hold_k > 0:                                            # tepe tutma işareti
                y = top + h - (hold_k + 1) * seg_h + 1
                p.setBrush(QColor(tk.text2))
                p.drawRect(QRectF(x, y, bar_w, 2))
            p.setPen(QColor(tk.text2))
            p.drawText(QRectF(x, self.height() - bottom, bar_w, bottom), Qt.AlignmentFlag.AlignCenter,
                       "LR"[c] if self._ch == 2 else "M")
        # clipping kutusu
        cw = self.width() - label_w - self.tk.px(6)
        clip_rect = QRectF(label_w + self.tk.px(6), 2, cw, top - 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tk.bad) if self._clip else QColor(tk.surface2))
        p.drawRect(clip_rect)
        p.setPen(QColor(tk.on_primary if self._clip else tk.text2))
        p.drawText(clip_rect, Qt.AlignmentFlag.AlignCenter, "CLIP")
        p.end()


class GRBar(ThemedMixin, QWidget):
    """Kapı kazanç azaltma çubuğu (0…30 dB) + metin ("-1.2 dB")."""

    def __init__(self, max_db: float = 30.0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._gr = 0.0
        self._max = max_db
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bind_theme()
        self._on_theme()

    def set_gr(self, db: float) -> None:
        if abs(db - self._gr) > 0.05:
            self._gr = max(0.0, db)
            self.update()

    @property
    def gr_db(self) -> float:
        return self._gr

    def _on_theme(self) -> None:
        self.setFixedHeight(self.tk.px(18))

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(tk.border), 1))
        p.setBrush(QColor(tk.surface2))
        p.drawRoundedRect(rect, tk.radius_ctl, tk.radius_ctl)
        w = rect.width() * min(1.0, self._gr / self._max)
        if w > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tk.warn))
            p.drawRoundedRect(QRectF(rect.left(), rect.top(), w, rect.height()), tk.radius_ctl, tk.radius_ctl)
        p.setFont(fonts.qfont(tk.font_mono, tk.ui_px * 0.8, weight=500))
        p.setPen(QColor(tk.text))
        p.drawText(rect.adjusted(6, 0, -6, 0), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                   f"-{self._gr:.1f} dB")
        p.end()


class StatusLamp(ThemedMixin, QWidget):
    """Küçük ışık + etiket (OPEN / HOLD / CLOSED / BYPASS)."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text, self._state = text, "bypass"
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._bind_theme()
        self._on_theme()

    def set_state(self, state: str, text: str) -> None:
        if (state, text) != (self._state, self._text):
            self._state, self._text = state, text
            self._on_theme()
            self.update()

    @property
    def state(self) -> str:
        return self._state

    @property
    def text(self) -> str:
        return self._text

    def _on_theme(self) -> None:
        fm = QFontMetrics(fonts.qfont(self.tk.font_mono, self.tk.ui_px * 0.85, weight=700))
        self.setFixedSize(fm.horizontalAdvance(self._text) + self.tk.px(34), self.tk.px(24))

    def paintEvent(self, _e) -> None:
        tk = self.tk
        color = {"open": tk.good, "hold": tk.warn, "closed": tk.bad, "bypass": tk.text2}.get(self._state, tk.text2)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self.tk.px(10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(d, self.height() / 2), d / 2, d / 2)
        p.setFont(fonts.qfont(tk.font_mono, tk.ui_px * 0.85, weight=700))
        p.setPen(QColor(color))
        fm = QFontMetrics(p.font())
        p.drawText(QPointF(d * 2 + 2, self.height() / 2 + (fm.ascent() - fm.descent()) / 2), self._text)
        p.end()


# ---------------------------------------------------------------------------
class ToggleSwitch(ThemedMixin, QAbstractButton):
    """Kaydırmalı anahtar (Basit: nane açık / kum kapalı). ``toggled(bool)`` yayınlar."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.toggled.connect(lambda _c: self.update())
        self._bind_theme()
        self._on_theme()

    def _on_theme(self) -> None:
        self.setFixedSize(self.tk.px(64), self.tk.px(36))

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        track = QColor(tk.accent("mint").base if (on and not tk.is_legend) else (tk.primary if on else
                                                                                  ("#E6D6C0" if not tk.is_legend else tk.surface2)))
        radius = r.height() / 2 if tk.pill else tk.radius_ctl
        p.setPen(QPen(QColor(tk.text if tk.high_contrast else tk.border), tk.border_w) if not on or tk.high_contrast
                 else Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, radius, radius)
        d = r.height() - 6
        x = r.right() - d - 3 if on else r.left() + 3
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF" if not tk.is_legend else tk.text))
        p.drawRoundedRect(QRectF(x, r.top() + 3, d, d), d / 2 if tk.pill else tk.radius_ctl,
                          d / 2 if tk.pill else tk.radius_ctl)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(tk.text if tk.high_contrast else tk.primary_deep), 2))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        p.end()


class PillButton(ThemedMixin, QAbstractButton):
    """Hız hapı (Yavaş/Normal/Hızlı). ``on_dark=True``: kahverengi okuma ekranının üstünde (tuval renkleri)."""

    def __init__(self, text: str, on_dark: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self._on_dark = on_dark
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toggled.connect(lambda _c: self.update())
        self._bind_theme()
        self._on_theme()

    def _font(self):
        return fonts.qfont(self.tk.font_ui, self.tk.ui_px * 0.9, weight=800 if not self.tk.is_legend else 600)

    def _on_theme(self) -> None:
        fm = QFontMetrics(self._font())
        self.setFixedSize(fm.horizontalAdvance(self.text()) + self.tk.px(36), max(self.tk.px(34), self.tk.ctl_h))

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _e) -> None:
        tk = self.tk
        on = self.isChecked()
        if tk.is_legend:
            bg, fg = (tk.primary, tk.on_primary) if on else (tk.surface2, tk.text2)
        else:
            bg, fg = ("#FFD97A", "#3B2F2A") if on else ("#5E4E46", "#FFF4D1")
            if not self._on_dark:
                bg, fg = (tk.primary, tk.on_primary) if on else (tk.surface2, tk.text)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = r.height() / 2 if tk.pill else tk.radius_ctl
        p.setPen(Qt.PenStyle.NoPen if not tk.high_contrast else QPen(QColor(tk.text), 2))
        p.setBrush(QColor(bg))
        p.drawRoundedRect(r, radius, radius)
        p.setFont(self._font())
        p.setPen(QColor(fg))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#FFFFFF" if not tk.is_legend else tk.primary), 2))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        p.end()


class BigRecordButton(ThemedMixin, QAbstractButton):
    """Kayıt düğmesi. Basit: büyük pembe daire (kayıtta durdur karesi + nabız halkası). Legend: kompakt kırmızı "● REC"."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._recording = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._pulse)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._label = ("REC", "STOP")
        self._bind_theme()
        self._on_theme()

    def set_labels(self, rec: str, stop: str) -> None:
        self._label = (rec, stop)
        self._update_a11y()
        self.update()

    def set_recording(self, on: bool) -> None:
        self._recording = bool(on)
        self._update_a11y()
        if on:
            self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    @property
    def recording(self) -> bool:
        return self._recording

    def _update_a11y(self) -> None:
        self.setAccessibleName(self._label[1] if self._recording else self._label[0])

    def _pulse(self) -> None:
        self._phase = (self._phase + 0.04) % 1.0
        self.update()

    def _on_theme(self) -> None:
        tk = self.tk
        if tk.is_legend:
            self.setFixedSize(tk.px(110), max(tk.px(30), tk.ctl_h))
        else:
            self.setFixedSize(tk.px(132), tk.px(132))
        self._update_a11y()

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if tk.is_legend:
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tk.bad if not self._recording else mix(tk.bad, "#000000", 0.25)))
            p.drawRoundedRect(r, tk.radius_ctl, tk.radius_ctl)
            p.setPen(QColor("#FFFFFF" if not tk.high_contrast else "#000000"))
            p.setFont(fonts.qfont(tk.font_mono, tk.ui_px, weight=700))
            text = ("■ " if self._recording else "● ") + (self._label[1] if self._recording else self._label[0])
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            if self.hasFocus():
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(tk.primary), 2))
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), tk.radius_ctl, tk.radius_ctl)
            p.end()
            return
        pink = tk.accent("pink")
        base = QColor(pink.base)
        deep = QColor("#FF8FA3")
        c = QRectF(self.rect()).center()
        radius = min(self.width(), self.height()) / 2 - 10
        if self._recording:                                          # nabız halkaları
            for k in range(2):
                ph = (self._phase + k * 0.5) % 1.0
                ring = QColor(deep)
                ring.setAlphaF(0.45 * (1.0 - ph))
                p.setPen(QPen(ring, 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                rr = radius + 2 + ph * 10
                p.drawEllipse(c, rr, rr)
        if self.isDown():
            radius *= 0.96
        p.setPen(Qt.PenStyle.NoPen if not tk.high_contrast else QPen(QColor(tk.text), 3))
        p.setBrush(deep if self._recording else base)
        p.drawEllipse(c, radius, radius)
        p.setBrush(QColor(pink.ink))
        if self._recording:
            s = radius * 0.62
            p.drawRoundedRect(QRectF(c.x() - s / 2, c.y() - s / 2, s, s), s * 0.18, s * 0.18)
        else:
            p.drawEllipse(c, radius * 0.34, radius * 0.34)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(tk.text if tk.high_contrast else tk.primary_deep), 3))
            p.drawEllipse(c, radius + 5, radius + 5)
        p.end()


# ---------------------------------------------------------------------------
class WaveformView(ThemedMixin, QWidget):
    """Dalga formu özeti çubukları (``set_peaks``). Henüz hazır değilse sönük çizgi."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._peaks: Optional[List[float]] = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bind_theme()
        self._on_theme()

    def set_peaks(self, peaks: Optional[Sequence[float]]) -> None:
        self._peaks = None if peaks is None else list(peaks)
        self.update()

    @property
    def peaks(self) -> Optional[List[float]]:
        return self._peaks

    def _on_theme(self) -> None:
        self.setFixedHeight(self.tk.px(30 if not self.tk.is_legend else 24))

    def sizeHint(self) -> QSize:
        return QSize(self.tk.px(160), self.height())

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(tk.accent("lavender").base if not tk.is_legend else tk.primary)
        mid = self.height() / 2
        if not self._peaks:
            p.setPen(QPen(QColor(tk.border), 1))
            p.drawLine(0, int(mid), self.width(), int(mid))
            p.end()
            return
        n = len(self._peaks)
        bw = self.width() / n
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for i, v in enumerate(self._peaks):
            h = max(1.5, v * (self.height() - 2))
            p.drawRoundedRect(QRectF(i * bw, mid - h / 2, max(1.0, bw - 0.6), h), 0.6, 0.6)
        p.end()


class PrompterView(ThemedMixin, QWidget):
    """Okuma ekranı: ``Prompter``'ı çizer (satır kaydırmayı yazı tipi ölçülerine göre kendisi yapar).

    Basit: kahverengi zemin, geçmiş satırlar soluk, okunan satır tereyağı zeminli. Legend: siyah, okunan satır altın çerçeveli.
    Ayna açıkken yatay çevrilir. Fare tekerleği konumu kaydırır.
    """

    seekRequested = Signal(float)

    def __init__(self, prompter: Prompter, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.prompter = prompter
        self._scroll = 0.0
        self._lines: List[tuple] = []
        self._lines_key = None
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Teleprompter")
        self._bind_theme()

    def set_prompter(self, prompter: Prompter) -> None:
        self.prompter = prompter
        self._lines_key = None
        self.update()

    def _font(self):
        tk = self.tk
        px = self.prompter.font_px * (1.0 if not tk.is_legend else 1.0)
        return fonts.qfont(tk.font_ui if not tk.is_legend else tk.font_ui, px, weight=800 if not tk.is_legend else 500)

    def layout_lines(self, width: int) -> List[tuple]:
        """``[(ilk_kelime, son_kelime, metin), ...]`` — yazı tipi ölçüsüne göre, paragraf sonlarına saygılı."""
        pr = self.prompter
        key = (width, pr.font_px, tuple(pr.words))
        if key == self._lines_key:
            return self._lines
        fm = QFontMetrics(self._font())
        lines: List[tuple] = []
        start, cur = 0, ""
        for i, w in enumerate(pr.words):
            cand = f"{cur} {w}".strip()
            if cur and fm.horizontalAdvance(cand) > width:
                lines.append((start, i - 1, cur))
                start, cur = i, w
            else:
                cur = cand
            if i in pr.breaks and cur:
                lines.append((start, i, cur))
                start, cur = i + 1, ""
        if cur:
            lines.append((start, len(pr.words) - 1, cur))
        self._lines, self._lines_key = lines, key
        return lines

    def current_line(self, width: Optional[int] = None) -> int:
        lines = self.layout_lines(width or max(100, self.width() - 2 * self.tk.px(24)))
        idx = self.prompter.current_index
        for n, (a, b, _t) in enumerate(lines):
            if a <= idx <= b:
                return n
        return max(0, len(lines) - 1)

    def wheelEvent(self, e) -> None:
        steps = e.angleDelta().y() / 120.0
        self.seekRequested.emit(self.prompter.position - steps * 4)
        e.accept()

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect())
        bg = QColor("#3B2F2A") if not tk.is_legend else QColor("#000000")
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), tk.radius_card, tk.radius_card)
        p.fillPath(path, bg)
        if tk.is_legend:
            p.setPen(QPen(QColor(tk.border), 1))
            p.drawPath(path)
        p.setClipPath(path)
        if self.prompter.mirror:
            p.setTransform(QTransform().translate(self.width(), 0).scale(-1, 1))
        pad = tk.px(24)
        width = int(self.width() - 2 * pad)
        font = self._font()
        p.setFont(font)
        fm = QFontMetrics(font)
        line_h = fm.height() * 1.35
        lines = self.layout_lines(width)
        if not self.prompter.words:
            p.setPen(QColor("#9C8C82" if not tk.is_legend else tk.text2))
            p.drawText(rect.adjusted(pad, 0, -pad, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
            p.end()
            return
        cur = self.current_line(width)
        target = cur * line_h - rect.height() * 0.38
        self._scroll += (target - self._scroll) * 0.25
        if abs(target - self._scroll) < 0.5:
            self._scroll = target
        for n, (a, b, text) in enumerate(lines):
            y = n * line_h - self._scroll
            if y < -line_h or y > rect.height():
                continue
            row = QRectF(pad * 0.5, y, rect.width() - pad, line_h)
            if n == cur:
                p.setPen(Qt.PenStyle.NoPen if not tk.is_legend else QPen(QColor(tk.primary), 1))
                p.setBrush(QColor("#FFD97A") if not tk.is_legend else QColor(mix("#000000", tk.primary, 0.22)))
                p.drawRoundedRect(row, line_h * 0.25 if not tk.is_legend else 2, line_h * 0.25 if not tk.is_legend else 2)
                p.setPen(QColor("#3B2F2A" if not tk.is_legend else "#FFFFFF"))
            elif n < cur:
                p.setPen(QColor("#9C8C82" if not tk.is_legend else tk.text2))
            else:
                p.setPen(QColor("#F1E3CF" if not tk.is_legend else tk.text))
            p.drawText(QPointF(pad, y + (line_h - fm.height()) / 2 + fm.ascent()), text)
        p.end()

    empty_text = ""


def mic_icon_rect(p: QPainter, rect: QRectF, color: str) -> None:
    draw_icon(p, rect, "mic", color, stroke=2.0)
