"""``DropZone`` — dosya sürükle-bırak alanı ("Dosyalarını buraya bırak").

Kesikli çerçeveli yuvarlak alan; üzerine dosya getirince vurgulanır. Bırakılan dosyaların yolları
``filesDropped(list[str])`` ile yayınlanır; widget dosyaya dokunmaz (kopyalama/yönlendirme servis işidir).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme.manager import ThemedMixin
from .squishy_button import SquishyButton


class DropZone(ThemedMixin, QWidget):
    filesDropped = Signal(list)
    browseRequested = Signal()

    def __init__(self, title: str, body: str, button_text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._over = False
        self.setAcceptDrops(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(18)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "h2")
        self.body_label = QLabel(body)
        self.body_label.setWordWrap(True)
        self.body_label.setProperty("role", "muted")
        col.addWidget(self.title_label)
        col.addWidget(self.body_label)
        lay.addLayout(col, 1)
        self.button = SquishyButton(button_text, variant="secondary", icon="folder")
        self.button.clicked.connect(self.browseRequested.emit)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._bind_theme()

    def retranslate(self, title: str, body: str, button_text: str) -> None:
        self.title_label.setText(title)
        self.body_label.setText(body)
        self.button.setText(button_text)

    # -- sürükle-bırak ----------------------------------------------------
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            self._over = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e) -> None:
        self._over = False
        self.update()

    def dropEvent(self, e) -> None:
        self._over = False
        self.update()
        # Qt her yerde "/" verir (Windows'ta "C:/Users/a.mp4"); uygulamanın geri kalanı yerel ayraçlı yol bekler.
        paths = [str(Path(u.toLocalFile())) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            e.acceptProposedAction()

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, tk.radius_card, tk.radius_card)
        p.fillPath(path, QColor(tk.primary_tone if self._over else tk.card))
        pen = QPen(QColor(tk.primary if self._over else tk.border), max(2, tk.border_w), Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.drawPath(path)
        p.end()
