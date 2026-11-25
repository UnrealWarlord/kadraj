"""Fotoğraf bileşenleri: ``VariationGrid`` (A/B galeri + kalp + büyüt), ``MaskPainter`` (fırçalı inpainting maskesi)."""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ...core import i18n
from ...ui.theme.manager import ThemedMixin
from . import imageops
from .service import Variation

THUMB = 220


class VariationCard(QFrame):
    selected = Signal(int)
    favoriteToggled = Signal(int)
    upscaleRequested = Signal(int, int)
    coverRequested = Signal(int)

    def __init__(self, v: Variation, simple: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.v = v
        self.setProperty("role", "card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = QPixmap(v.path)
        if not pm.isNull():
            pm = pm.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.thumb.setPixmap(pm)
        self.thumb.setMinimumSize(THUMB if not pm.isNull() else 120, 120)
        self.thumb.setToolTip(v.prompt)
        lay.addWidget(self.thumb)
        row = QHBoxLayout()
        self.badge = QLabel(f"{v.label}  " if v.label else "")
        self.badge.setProperty("role", "caps")
        self.heart = QToolButton()
        self.heart.setCheckable(True)
        self.heart.setChecked(v.favorite)
        self.heart.setText("♥" if v.favorite else "♡")
        self.heart.setAccessibleName(i18n.t("photo.fav"))
        self.heart.toggled.connect(lambda _c: self.favoriteToggled.emit(v.id))
        self.up2 = QPushButton(i18n.t("photo.up2"))
        self.up4 = QPushButton(i18n.t("photo.up4"))
        self.up2.clicked.connect(lambda: self.upscaleRequested.emit(v.id, 2))
        self.up4.clicked.connect(lambda: self.upscaleRequested.emit(v.id, 4))
        row.addWidget(self.badge)
        row.addWidget(self.heart)
        row.addStretch(1)
        row.addWidget(self.up2)
        row.addWidget(self.up4)
        lay.addLayout(row)
        if not simple:
            meta = QLabel(f"{v.provider} · {v.model} · ${v.cost_usd:.3f}" + (f" · seed {v.seed}" if v.seed is not None else ""))
            meta.setProperty("role", "mono")
            lay.addWidget(meta)

    def mousePressEvent(self, e) -> None:
        self.selected.emit(self.v.id)
        super().mousePressEvent(e)


class VariationGrid(QWidget):
    selected = Signal(int)
    favoriteToggled = Signal(int)
    upscaleRequested = Signal(int, int)

    def __init__(self, simple: bool = True, columns: int = 2, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.simple, self.columns = simple, columns
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.cards: List[VariationCard] = []
        self.selected_id: Optional[int] = None

    def set_variations(self, vs: Sequence[Variation]) -> None:
        for c in self.cards:
            c.hide()
            c.setParent(None)
            c.deleteLater()
        self.cards = []
        for i, v in enumerate(vs):
            card = VariationCard(v, self.simple)
            card.selected.connect(self._select)
            card.favoriteToggled.connect(self.favoriteToggled)
            card.upscaleRequested.connect(self.upscaleRequested)
            self.grid.addWidget(card, i // self.columns, i % self.columns)
            self.cards.append(card)

    def _select(self, vid: int) -> None:
        self.selected_id = vid
        self.selected.emit(vid)


class MaskPainter(ThemedMixin, QWidget):
    """Görsel üstüne fırçayla maske çizer (kırmızı yarı saydam = yeniden çizilecek alan). ``mask_png()`` beyaz = düzenle."""

    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.image: Optional[QImage] = None
        self.brush = 36.0
        self.strokes: List[Tuple[List[Tuple[float, float]], float]] = []
        self._cur: Optional[List[Tuple[float, float]]] = None
        self.setMinimumSize(260, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._bind_theme()

    def set_image(self, path: str) -> bool:
        img = QImage(path)
        if img.isNull():
            return False
        self.image, self.strokes, self._cur = img, [], None
        self.update()
        self.changed.emit()
        return True

    def _fit(self) -> QRectF:
        if self.image is None:
            return QRectF(self.rect())
        s = min(self.width() / self.image.width(), self.height() / self.image.height())
        w, h = self.image.width() * s, self.image.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_image(self, pos: QPointF) -> Tuple[float, float]:
        r = self._fit()
        return ((pos.x() - r.left()) / r.width() * self.image.width(), (pos.y() - r.top()) / r.height() * self.image.height())  # type: ignore[union-attr]

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(tk.surface2))
        if self.image is None:
            p.setPen(QColor(tk.text2))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, i18n.t("photo.mask.empty"))
            return
        r = self._fit()
        p.drawImage(r, self.image)
        scale = r.width() / self.image.width()
        c = QColor(255, 60, 60, 120)
        p.setPen(QPen(c, 1))
        p.setBrush(c)
        for pts, d in self.strokes + ([(self._cur, self.brush)] if self._cur else []):
            rad = d * scale / 2
            for x, y in pts:
                p.drawEllipse(QPointF(r.left() + x * scale, r.top() + y * scale), rad, rad)

    def mousePressEvent(self, e) -> None:
        if self.image is not None and e.button() == Qt.MouseButton.LeftButton:
            self._cur = [self._to_image(e.position())]
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._cur is not None:
            self._cur.append(self._to_image(e.position()))
            self.update()

    def mouseReleaseEvent(self, _e) -> None:
        if self._cur:
            self.strokes.append((self._cur, self.brush))
            self.changed.emit()
        self._cur = None
        self.update()

    def paint_stroke(self, points: Sequence[Tuple[float, float]], diameter: Optional[float] = None) -> None:
        """Programatik vuruş (görüntü koordinatlarında; testler/araçlar için)."""
        self.strokes.append((list(points), diameter or self.brush))
        self.update()
        self.changed.emit()

    def undo(self) -> None:
        if self.strokes:
            self.strokes.pop()
            self.update()
            self.changed.emit()

    def clear(self) -> None:
        self.strokes = []
        self.update()
        self.changed.emit()

    @property
    def has_mask(self) -> bool:
        return bool(self.strokes)

    def mask_png(self) -> bytes:
        size = (self.image.width(), self.image.height()) if self.image is not None else (1, 1)
        return imageops.mask_from_strokes(size, self.strokes)
