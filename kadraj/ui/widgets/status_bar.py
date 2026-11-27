"""``AutosaveIndicator`` ve ``StatusBar``.

Otomatik kayıt göstergesi Adım 3'ün ``Topics.AUTOSAVE_STATE`` olaylarını gösterir; o gelene
dek "—" (henüz kayıt yok) durumundadır. ``StatusBar`` altta ince bir şerittir: sol ileti, ortada
iş sayacı, sağda sistem örneği (CPU/RAM/GPU) ve sürüm. Basit'te yalnızca ileti + kayıt göstergesi
görünür (sade), Legend'de hepsi.
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from ...core import i18n
from ... import __version__
from .. import fonts
from ..theme.manager import ThemedMixin


class AutosaveIndicator(ThemedMixin, QWidget):
    """Küçük renkli nokta + metin. Durumlar: ``none`` (kayıt yok), ``idle``, ``saving``, ``saved``, ``error``."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = "none"
        self._at: Optional[float] = None
        self._seconds = 20
        self._compact_mode = False
        self._tick = QTimer(self)
        self._tick.setInterval(5000)
        self._tick.timeout.connect(self._refresh)
        self._tick.start()
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._bind_theme()
        self._refresh()

    def set_compact(self, compact: bool) -> None:
        """Yalnızca nokta (metin araç ipucunda); dar üst çubuk için."""
        self._compact_mode = bool(compact)
        self._refresh()

    def set_interval_seconds(self, seconds: int) -> None:
        self._seconds = max(1, int(seconds))
        self._refresh()

    def set_state(self, state: str, at: Optional[float] = None) -> None:
        self._state = state if state in ("none", "idle", "saving", "saved", "error") else "none"
        if at is not None:
            self._at = float(at)
        elif self._state == "saved":
            self._at = time.time()
        self._refresh()

    @property
    def state(self) -> str:
        return self._state

    def text(self) -> str:
        return self._text

    def _text_now(self) -> str:
        tk = self.tk
        if self._state == "saving":
            return i18n.t("autosave.saving")
        if self._state == "error":
            return i18n.t("autosave.error")
        if self._state == "none" or self._at is None:
            return i18n.t("autosave.none")
        ago = max(0, int(time.time() - self._at))
        if tk.is_legend:
            return f"AUTOSAVE {ago // 60:02d}:{ago % 60:02d} · {self._seconds}s"
        when = i18n.t("autosave.just_now") if ago < 10 else i18n.t("autosave.ago", seconds=ago)
        return i18n.t("autosave.every", seconds=self._seconds) + " · " + when

    def _refresh(self) -> None:
        self._text = self._text_now()
        self.setAccessibleName(i18n.t("autosave.label"))
        self.setAccessibleDescription(self._text)
        self.setToolTip(self._text)
        self._resize()
        self.update()

    def _on_theme(self) -> None:
        self._refresh()

    def _font(self):
        tk = self.tk
        if tk.is_legend:
            return fonts.qfont(tk.font_mono, tk.ui_px * 0.85, weight=500)
        return fonts.qfont(tk.font_ui, tk.ui_px * 0.85, weight=700)

    def _resize(self) -> None:
        tk = self.tk
        fm = QFontMetrics(self._font())
        text_w = 0 if self._compact_mode else fm.horizontalAdvance(self._text) + tk.px(8)
        self.setFixedSize(text_w + tk.px(18), max(tk.px(22), fm.height() + 6))

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = {"saved": tk.good, "idle": tk.good, "saving": tk.warn, "error": tk.bad,
               "none": tk.text2}[self._state]
        r = QRectF(self.rect())
        d = max(6.0, tk.px(8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        p.drawEllipse(QPointF(d, r.center().y()), d / 2, d / 2)
        if not self._compact_mode:
            p.setFont(self._font())
            p.setPen(QColor(tk.text2))
            fm = QFontMetrics(self._font())
            p.drawText(QPointF(d * 2 + 2, r.center().y() + (fm.ascent() - fm.descent()) / 2), self._text)
        p.end()


class _ElidedLabel(QLabel):
    """Tek satırlık ileti: sığmazsa "…" ile kısalır (dar ekranda yarım kesilmiş kelime görünmez).
    Genişlik istemez; şeritte kalan yeri kullanır. Tam metin ``full_text()``'tedir."""

    def __init__(self) -> None:
        super().__init__("")
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._fit()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 0)))


class StatusBar(QFrame):
    """Alt durum şeridi."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "statusbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(16)
        self._message = _ElidedLabel()
        self._message.setProperty("role", "muted")
        self._jobs = QLabel("")
        self._jobs.setProperty("role", "muted")
        self._sys = QLabel("")
        self._sys.setProperty("role", "mono")
        self._sys.setStyleSheet("color: palette(mid);")
        self._ver = QLabel(f"v{__version__}")
        self._ver.setProperty("role", "mono")
        self.autosave = AutosaveIndicator()
        lay.addWidget(self._message, 1)
        lay.addWidget(self._jobs)
        lay.addWidget(self._sys)
        lay.addWidget(self.autosave)
        lay.addWidget(self._ver)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._message.set_full_text(""))
        self.set_compact(True)

    # -- API --------------------------------------------------------------
    def set_message(self, text: str, timeout_ms: int = 4000) -> None:
        self._message.set_full_text(text)
        if timeout_ms > 0:
            self._timer.start(timeout_ms)

    def message(self) -> str:
        return self._message.full_text()

    def set_jobs(self, active: int, total: int) -> None:
        self._jobs.setText(i18n.t("status.jobs", active=active, total=total) if total else "")

    def set_system(self, sample: dict) -> None:
        if not sample:
            self._sys.setText("")
            return
        parts = []
        if sample.get("cpu") is not None:
            parts.append(f"CPU {sample['cpu']:.0f}%")
        if sample.get("ram_total"):
            parts.append(f"RAM {sample['ram_used'] / 1e9:.1f}/{sample['ram_total'] / 1e9:.0f} GB")
        if sample.get("gpu") is not None:
            parts.append(f"GPU {sample['gpu']:.0f}%")
        self._sys.setText("  ".join(parts))

    def set_compact(self, compact: bool) -> None:
        """Basit Mod: yalnızca ileti + kayıt göstergesi. Legend: sistem ve sürüm de görünür."""
        self._compact = compact
        for w in (self._sys, self._ver, self._jobs):
            w.setVisible(not compact)
