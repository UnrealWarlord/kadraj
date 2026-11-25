"""Açılış ekranı (splash) — ``Lockup`` + ilerleme çubuğu + araç denetimi.

İskeletin 2,5–3,5 sn'lik rastgele süresi korunur (``splash_duration``). İlerleme zamana göre akar;
metin sırayla "FFmpeg kontrol ediliyor…" gibi adımları gösterir. Süre dolunca ``finished`` yayınlanır;
``skip()`` hemen bitirir (Esc / tık). Çerçevesiz, ekran ortasında.
"""
from __future__ import annotations

import random
import time
from typing import Dict, Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ... import __version__
from ...core import i18n
from ..theme.manager import ThemedMixin, current_tokens
from ..widgets.lockup import Lockup
from .startup import ToolStatus, detect_tools, summary_line

SPLASH_MIN_MS = 2500
SPLASH_MAX_MS = 3500
_FRAME_MS = 16


def splash_duration() -> int:
    return random.randint(SPLASH_MIN_MS, SPLASH_MAX_MS)


class SplashScreen(ThemedMixin, QWidget):
    finished = Signal()

    def __init__(self, duration_ms: Optional[int] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("SplashRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._duration = max(200, duration_ms if duration_ms is not None else splash_duration())
        self._started: Optional[float] = None
        self._done = False
        self._tools: Dict[str, ToolStatus] = {}
        tk = self.tk
        self.resize(tk.px(720), tk.px(460))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(tk.px(48), tk.px(40), tk.px(48), tk.px(32))
        lay.setSpacing(tk.px(14))
        lay.addStretch(2)
        self.lockup = Lockup(wordmark_px=tk.px(64), show_mark=True)
        lay.addWidget(self.lockup, 0, Qt.AlignmentFlag.AlignHCenter)
        self.tagline = QLabel(i18n.t("splash.tagline"))
        self.tagline.setProperty("role", "muted")
        self.tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tagline.setWordWrap(True)
        lay.addWidget(self.tagline)
        lay.addStretch(3)
        self.status = QLabel(i18n.t("splash.preparing"))
        self.status.setProperty("role", "muted")
        lay.addWidget(self.status)
        self._bar_top = lay.count()
        lay.addSpacing(tk.px(14))                     # ilerleme çubuğu bu boşluğa boyanır
        self.footer = QLabel("")
        self.footer.setProperty("role", "mono")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer.setStyleSheet("font-size: 11px;")
        lay.addWidget(self.footer)

        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._bind_theme()

    # -- yaşam döngüsü ----------------------------------------------------
    def start(self) -> None:
        self._tools = detect_tools()
        self.footer.setText(summary_line(self._tools, __version__))
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)
        self._started = time.perf_counter()
        self.show()
        self._timer.start()

    def progress(self) -> float:
        if self._started is None:
            return 0.0
        return min(1.0, (time.perf_counter() - self._started) * 1000.0 / self._duration)

    def _status_text(self, frac: float) -> str:
        steps = [("splash.step_settings", None), ("splash.step_ffmpeg", "ffmpeg"),
                 ("splash.step_ytdlp", "yt-dlp"), ("splash.step_ai", "ollama"), ("splash.step_ready", None)]
        key, tool = steps[min(len(steps) - 1, int(frac * len(steps)))]
        return i18n.t(key)

    def _tick(self) -> None:
        frac = self.progress()
        self.status.setText(self._status_text(frac))
        self.update()
        if frac >= 1.0:
            self.skip()

    def skip(self) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.hide()
        self.finished.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.skip()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:
        self.skip()

    # -- boyama: vizör köşeleri + ilerleme çubuğu -------------------------------
    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(tk.border if not tk.high_contrast else tk.text), 2))
        m, L = tk.px(24), tk.px(40)
        w, h = self.width(), self.height()
        for (x, y, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(x, y, x + dx * L, y)
            p.drawLine(x, y, x, y + dy * L)
        # ilerleme
        track = QRectF(tk.px(48), self.footer.y() - tk.px(14), w - 2 * tk.px(48), 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tk.surface2))
        p.drawRoundedRect(track, 2, 2)
        fill = QRectF(track)
        fill.setWidth(track.width() * self.progress())
        p.setBrush(QColor(tk.primary))
        p.drawRoundedRect(fill, 2, 2)
        p.end()
