"""Seçilebilir kartlar: ``ChoiceCard`` (tek kart), ``ChoiceGroup`` (tekli/çoklu grup).

Sihirbazın üç sorusu, Ayarlar'ın mod kartı ve İndirici'nin kalite seçicisi aynı yapıyı kullanır;
``QualityPicker`` ve ``PlatformCard`` bu bileşenlerin adlandırılmış kısayollarıdır (Adım 4/5/7
isterlerse kendi alt sınıflarını yazar). Kartlar kontrol edilebilir (``checkable``), klavyeyle gezilir ve
erişilebilir ad/durum içerir.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QPushButton, QSizePolicy, QWidget

from ...core.contracts import Accent
from .. import fonts
from ..theme.manager import ThemedMixin
from .icons import draw_icon


class ChoiceCard(ThemedMixin, QPushButton):
    """İkon kutusu + başlık + alt metin (+ sağda isteğe bağlı metin). ``choice_id`` seçim kimliğidir."""

    def __init__(self, choice_id: str, title: str, subtitle: str = "", icon: str = "dot", *,
                 accent: "Accent | str | None" = None, trailing: str = "", trailing_color: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self.choice_id = choice_id
        self._title, self._sub, self._icon = title, subtitle, icon
        self._accent = accent
        self._trailing, self._trailing_color = trailing, trailing_color
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle)
        self.toggled.connect(lambda _c: self.update())
        self._bind_theme()
        self._on_theme()

    def set_texts(self, title: str, subtitle: str = "", trailing: str = "") -> None:
        self._title, self._sub, self._trailing = title, subtitle, trailing
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle)
        self.update()

    def _on_theme(self) -> None:
        tk = self.tk
        self.setFixedHeight(tk.px(84) if not tk.is_legend else tk.px(52))

    def sizeHint(self) -> QSize:
        return QSize(self.tk.px(260), self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — metin kısaltılarak çizilir; telefonda kart daralabilir
        return QSize(self.tk.px(160), self.height())

    def paintEvent(self, _e) -> None:
        tk = self.tk
        on = self.isChecked()
        acc = tk.accent(self._accent) if self._accent else None
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = tk.radius_card - (4 if not tk.is_legend else 0)
        border_w = max(2, tk.border_w) if on else tk.border_w
        fill = tk.primary_tone if on else (tk.surface2 if self.underMouse() else tk.card)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, QColor(fill))
        p.setPen(QPen(QColor(tk.primary if on else tk.border), border_w))
        p.drawPath(path)
        pad = tk.px(16) if not tk.is_legend else tk.px(10)
        isz = tk.px(46) if not tk.is_legend else tk.px(30)
        ir = QRectF(rect.left() + pad, rect.center().y() - isz / 2, isz, isz)
        ibg = (acc.base if acc else tk.primary) if on else (acc.tone if acc else tk.surface2)
        ifg = (acc.ink if acc else tk.on_primary) if on else (acc.ink if acc else tk.primary_deep)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ibg))
        p.drawRoundedRect(ir, isz * (0.3 if not tk.is_legend else 0.12), isz * (0.3 if not tk.is_legend else 0.12))
        draw_icon(p, ir.adjusted(isz * 0.2, isz * 0.2, -isz * 0.2, -isz * 0.2), self._icon, ifg, stroke=1.9)
        tx = ir.right() + pad
        tw = rect.right() - pad - tx - (QFontMetrics(fonts.qfont(tk.font_ui, tk.ui_px * 0.85, weight=700))
                                         .horizontalAdvance(self._trailing) + 12 if self._trailing else 0)
        tf = fonts.qfont(tk.font_ui, tk.ui_px * 1.05, weight=800 if not tk.is_legend else 600)
        sf = fonts.qfont(tk.font_ui, tk.ui_px * 0.85, weight=600 if not tk.is_legend else 400)
        tfm, sfm = QFontMetrics(tf), QFontMetrics(sf)
        block = tfm.height() + (sfm.height() if self._sub else 0)
        y = rect.center().y() - block / 2
        p.setFont(tf)
        p.setPen(QColor(tk.text))
        p.drawText(QPointF(tx, y + tfm.ascent()), tfm.elidedText(self._title, Qt.TextElideMode.ElideRight, int(tw)))
        if self._sub:
            p.setFont(sf)
            p.setPen(QColor(tk.text2))
            p.drawText(QPointF(tx, y + tfm.height() + sfm.ascent()),
                       sfm.elidedText(self._sub, Qt.TextElideMode.ElideRight, int(tw)))
        if self._trailing:
            ff = fonts.qfont(tk.font_ui, tk.ui_px * 0.85, weight=700)
            p.setFont(ff)
            p.setPen(QColor(self._trailing_color or tk.text2))
            p.drawText(QRectF(rect.left(), rect.top(), rect.width() - pad, rect.height()),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), self._trailing)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(tk.text if tk.high_contrast else tk.primary_deep), 3))
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        p.end()


class ChoiceGroup(QWidget):
    """Kart ızgarası. ``exclusive=True`` radyo, ``False`` çoklu seçim. ``selectionChanged(list[str])``.

    ``columns`` en çok sütun sayısıdır: genişlik kartları rahat sığdırmıyorsa (dar pencere, tablet dikey, telefon)
    sütun azalır, kartlar alt alta geçer. Yükseklik genişliğe göre bildirilir (``heightForWidth``).
    """

    selectionChanged = Signal(list)

    def __init__(self, cards: Iterable[ChoiceCard] = (), *, exclusive: bool = True, columns: int = 3,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._exclusive = exclusive
        self._max_columns = max(1, columns)
        self._columns = self._max_columns
        self._cards: List[ChoiceCard] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(exclusive)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(14)
        for card in cards:
            self.add_card(card)

    def add_card(self, card: ChoiceCard) -> None:
        idx = len(self._cards)
        self._cards.append(card)
        self._group.addButton(card)
        self._grid.addWidget(card, idx // self._columns, idx % self._columns)
        card.toggled.connect(lambda _c: self.selectionChanged.emit(self.selected()))

    def cards(self) -> List[ChoiceCard]:
        return list(self._cards)

    def selected(self) -> List[str]:
        return [c.choice_id for c in self._cards if c.isChecked()]

    def set_selected(self, ids: Sequence[str]) -> None:
        for c in self._cards:
            c.setChecked(c.choice_id in ids)

    def set_columns(self, columns: int) -> None:
        """En çok sütun sayısı; gerçek sayı genişliğe göre (``columns_for``)."""
        self._max_columns = max(1, columns)
        self._apply_columns(self.columns_for(self.width()) if self.width() > 0 else self._max_columns)

    def columns(self) -> int:
        return self._columns

    def columns_for(self, width: int) -> int:
        """Bu genişlikte kaç sütun: kartlar önerilen genişlikleriyle sığmalı (en az bir sütun)."""
        if not self._cards:
            return self._max_columns
        card = max(c.sizeHint().width() for c in self._cards)
        gap = self._grid.horizontalSpacing()
        fit = (max(0, width) + gap) // max(1, card + gap)
        return max(1, min(self._max_columns, fit, len(self._cards)))

    def _apply_columns(self, columns: int) -> None:
        if columns == self._columns and self._grid.count() == len(self._cards):
            return
        self._columns = columns
        for c in self._cards:
            self._grid.removeWidget(c)
        for i, c in enumerate(self._cards):
            self._grid.addWidget(c, i // columns, i % columns)
        for col in range(self._max_columns):
            self._grid.setColumnStretch(col, 1 if col < columns else 0)
        self.updateGeometry()

    def _rows_height(self, columns: int) -> int:
        if not self._cards:
            return 0
        rows = [self._cards[i:i + columns] for i in range(0, len(self._cards), columns)]
        heights = [max(c.sizeHint().height() for c in row) for row in rows]
        return sum(heights) + self._grid.verticalSpacing() * (len(rows) - 1)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._rows_height(self.columns_for(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — tek sütuna kadar daralabilir
        if not self._cards:
            return super().minimumSizeHint()
        width = max(c.minimumSizeHint().width() for c in self._cards)
        return QSize(width, self._rows_height(1))

    def sizeHint(self) -> QSize:  # noqa: N802
        if not self._cards:
            return super().sizeHint()
        columns = min(self._max_columns, len(self._cards))
        card = max(c.sizeHint().width() for c in self._cards)
        width = card * columns + self._grid.horizontalSpacing() * (columns - 1)
        return QSize(width, self._rows_height(columns))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_columns(self.columns_for(self.width()))


#: Adım 4/5/7 için adlandırılmış kısayollar (şimdilik aynı davranış).
QualityPicker = ChoiceGroup
PlatformCard = ChoiceCard
