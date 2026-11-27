"""Modül sayfası yardımcıları: "yakında" yer tutucusu, hata sayfası, sayfa çerçevesi, sayfa üretici."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...core import i18n
from ...core.contracts import SmartError, UiMode
from ...core.registry import ModuleEntry
from .. import adaptive
from ..theme.manager import ThemedMixin
from ..widgets.icons import draw_icon
from ..widgets.smart_error_card import SmartErrorCard


class _Blob(ThemedMixin, QWidget):
    """Yer tutucu sayfanın büyük ikon rozeti."""

    def __init__(self, icon: str, accent, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon, self._accent = icon, accent
        self._bind_theme()
        self._on_theme()

    def _on_theme(self) -> None:
        s = self.tk.px(96)
        self.setFixedSize(s, s)

    def paintEvent(self, _e) -> None:
        tk = self.tk
        acc = tk.accent(self._accent)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(r, r.width() * (0.34 if not tk.is_legend else 0.06), r.height() * (0.34 if not tk.is_legend else 0.06))
        p.fillPath(path, QColor(acc.base if not tk.is_legend else acc.tone))
        draw_icon(p, r.adjusted(r.width() * 0.24, r.height() * 0.24, -r.width() * 0.24, -r.height() * 0.24),
                  self._icon, acc.ink if not tk.is_legend else acc.base, stroke=1.8)
        p.end()


class ComingSoonPage(QWidget):
    """Modülün sayfası henüz yokken ("yakında") ya da yüklenemediğinde gösterilir."""

    def __init__(self, entry: ModuleEntry, mode: UiMode, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(14)
        lay.addWidget(_Blob(entry.icon, entry.accent), 0, Qt.AlignmentFlag.AlignHCenter)
        title = QLabel(i18n.term(entry.title_key) if i18n.has(f"{mode.value}.{entry.title_key}") or i18n.has(entry.title_key)
                       else entry.id)
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body = QLabel(i18n.t("soon.body"))
        body.setProperty("role", "muted")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip = QLabel(i18n.t("home.soon"))
        chip.setProperty("role", "chip")
        lay.addWidget(title)
        lay.addWidget(body)
        lay.addWidget(chip, 0, Qt.AlignmentFlag.AlignHCenter)
        self.setObjectName(f"soon_{entry.id}")


class PageErrorWidget(QWidget):
    """Bir modül sayfası kurulurken hata verirse çekirdek düşmez; yerine hata kartı gelir."""

    def __init__(self, error: SmartError, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.addWidget(SmartErrorCard(error))
        lay.addStretch(1)


def build_module_page(ctx, entry: ModuleEntry, mode: UiMode) -> QWidget:
    """``spec.simple_page`` / ``legend_page`` fabrikasını çağırır; yoksa "yakında", hata verirse hata kartı."""
    factory = None
    if entry.spec is not None:
        factory = entry.spec.simple_page if mode is UiMode.SIMPLE else entry.spec.legend_page
    if factory is None:
        return ComingSoonPage(entry, mode)
    try:
        page = factory(ctx)
        if not isinstance(page, QWidget):
            raise TypeError(f"{entry.id}: sayfa fabrikası QWidget döndürmedi")
        return page
    except Exception as exc:
        return PageErrorWidget(ctx.report_error(exc, {"source": entry.id}))


class PageFrame(QFrame):
    """Basit Mod'da modül sayfasının çerçevesi: "← Ana ekran" + başlık + sayfa."""

    backRequested = Signal()

    def __init__(self, title: str, page: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        side, edge = adaptive.margin(28, 10), adaptive.margin(18, 8)
        lay.setContentsMargins(side, edge, side, edge)
        lay.setSpacing(adaptive.margin(12, 8))
        row = QHBoxLayout()
        self.back = QPushButton("←  " + i18n.t("nav.home"))
        self.back.setProperty("role", "topbtn")
        self.back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back.clicked.connect(self.backRequested.emit)
        self.title = QLabel(title)
        self.title.setProperty("role", "h2")
        row.addWidget(self.back)
        row.addSpacing(8)
        row.addWidget(self.title)
        row.addStretch(1)
        lay.addLayout(row)
        self.page = page
        if adaptive.is_phone():                 # telefonda sayfa dikeyde kayar; yatayda ekrana sığar (6. adım)
            self.title.setWordWrap(True)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            lay.addWidget(scroll, 1)
        else:
            lay.addWidget(page, 1)
