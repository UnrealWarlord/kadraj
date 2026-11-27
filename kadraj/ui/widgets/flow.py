"""``FlowLayout`` — yatay satır; sığmazsa öğeleri alt satıra geçirir (telefon ve tablet düzeni; mobil 6. adım).

* **Sığıyorsa** ``QHBoxLayout`` gibi davranır: öğeler tek satırda, esneme payı olan öğeler (``stretch > 0`` ya da
  ``Expanding``) ve ``addStretch`` boşlukları artan genişliği paylaşır, ``addSpacing`` boşluk bırakır. Tablet yatayda
  masaüstündeki görünüm korunur.
* **Sığmıyorsa** sarılır: tam satır öğeler (metin kutusu, açılır liste, uzun etiket; ``full=True`` ya da esneme payı
  verilmiş öğe) kendi satırını tam genişlikte kaplar, diğerleri (düğmeler, rozetler) yan yana dizilip sığmayan alt
  satıra geçer. Esneme ve sabit boşluklar sarılınca yok sayılır.
* Genişliğe göre yükseklik bildirir (``heightForWidth``): kaydırma alanları ve dikey yerleşimler doğru boyu ayırır.
* ``QHBoxLayout`` ile aynı ekleme işlevleri (``addWidget(w, stretch)``, ``insertWidget``, ``addLayout``, ``addStretch``,
  ``addSpacing``): mobilde satır düzeni değişirken çağıran kod değişmez (``kadraj.ui.adaptive.hbox``).
* Bir satırdaki öğeler dikeyde ortalanır; düzenin en dar hali en geniş öğenin en küçük genişliğidir.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QSpacerItem, QWidget

#: Öğe türleri: widget, esneme boşluğu (``addStretch``), sabit boşluk (``addSpacing``).
WIDGET, STRETCH, SPACING = "widget", "stretch", "spacing"


class FlowLayout(QLayout):
    def __init__(self, parent: Optional[QWidget] = None, *, spacing: int = 8, line_spacing: Optional[int] = None) -> None:
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._meta: List[Tuple[str, int, bool]] = []          # (tür, esneme payı, tam satır)
        self._pending: Tuple[str, int, bool] = (WIDGET, 0, False)
        self._hs = spacing
        self._vs = spacing if line_spacing is None else line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # -- QHBoxLayout'a benzer ekleme işlevleri -----------------------------
    def addWidget(self, widget: QWidget, stretch: int = 0, alignment: Qt.AlignmentFlag = Qt.AlignmentFlag(0), *,
                  full: Optional[bool] = None) -> None:  # noqa: N802
        expanding = bool(widget.sizePolicy().horizontalPolicy().value & QSizePolicy.PolicyFlag.ExpandFlag.value)
        if full is None:
            full = stretch > 0 or expanding
        weight = stretch if stretch > 0 else (1 if full else 0)
        self._pending = (WIDGET, weight, bool(full))
        try:
            super().addWidget(widget)
        finally:
            self._pending = (WIDGET, 0, False)

    def insertWidget(self, index: int, widget: QWidget, stretch: int = 0, *, full: Optional[bool] = None) -> None:  # noqa: N802
        self.addWidget(widget, stretch, full=full)
        item, meta = self._items.pop(), self._meta.pop()
        index = len(self._items) if index < 0 else min(index, len(self._items))
        self._items.insert(index, item)
        self._meta.insert(index, meta)
        self.invalidate()

    def addLayout(self, layout: QLayout, stretch: int = 0, *, full: Optional[bool] = None) -> QWidget:  # noqa: N802
        """İç düzeni bir kaba sarar (akış düzeni yalnız öğe dizer). Kap döner."""
        holder = QWidget()
        holder.setLayout(layout)
        self.addWidget(holder, stretch, full=bool(stretch > 0) if full is None else full)
        return holder

    def addStretch(self, stretch: int = 0) -> None:  # noqa: N802 — tek satırda boşluk paylaşır, sarılınca yok
        self._pending = (STRETCH, max(1, stretch), False)
        try:
            self.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        finally:
            self._pending = (WIDGET, 0, False)
        self.invalidate()

    def addSpacing(self, size: int) -> None:  # noqa: N802 — tek satırda sabit boşluk, sarılınca yok
        self._pending = (SPACING, 0, False)
        try:
            self.addItem(QSpacerItem(max(0, size), 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum))
        finally:
            self._pending = (WIDGET, 0, False)
        self.invalidate()

    def setSpacing(self, spacing: int) -> None:  # noqa: N802
        self._hs = self._vs = spacing
        self.invalidate()

    def spacing(self) -> int:
        return self._hs

    # -- QLayout -----------------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)
        self._meta.append(self._pending)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            self._meta.pop(index)
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def minimumSize(self) -> QSize:  # noqa: N802
        m = self.contentsMargins()
        widest = max((item.minimumSize().width() for item, kind, _w, _f in self._entries() if kind == WIDGET), default=0)
        width = widest + m.left() + m.right()
        return QSize(width, self.heightForWidth(width))

    def sizeHint(self) -> QSize:  # noqa: N802
        m = self.contentsMargins()
        width = self._natural_width(self._entries()) + m.left() + m.right()
        return QSize(width, self.heightForWidth(width))

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    # -- yerleşim ------------------------------------------------------------
    def _entries(self) -> List[Tuple[QLayoutItem, str, int, bool]]:
        """Görünen widget'lar ve boşluklar (gizli widget yer kaplamaz)."""
        out = []
        for item, (kind, weight, full) in zip(self._items, self._meta):
            if kind == WIDGET and item.isEmpty():
                continue
            out.append((item, kind, weight, full))
        return out

    def _natural_width(self, entries) -> int:
        """Tek satırda, her öğe kendi istediği genişlikteyken toplam (esneme boşlukları sıfır)."""
        total, gaps = 0, 0
        for item, kind, _weight, _full in entries:
            if kind == STRETCH:
                continue
            total += item.sizeHint().width() if kind == SPACING else self._hint_width(item)
            gaps += 1
        return total + self._hs * max(0, gaps - 1)

    @staticmethod
    def _hint_width(item: QLayoutItem) -> int:
        return max(item.sizeHint().width(), item.minimumSize().width())

    @staticmethod
    def _height(item: QLayoutItem, width: int) -> int:
        h = item.heightForWidth(width) if item.hasHeightForWidth() else item.sizeHint().height()
        return max(h, item.minimumSize().height())

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        m: QMargins = self.contentsMargins()
        left, top = rect.x() + m.left(), rect.y() + m.top()
        width = max(1, rect.width() - m.left() - m.right())
        entries = self._entries()
        if not any(kind == WIDGET for _i, kind, _w, _f in entries):
            return m.top() + m.bottom()
        if self._natural_width(entries) <= width:
            used = self._single_line(entries, left, top, width, apply)
        else:
            used = self._wrapped(entries, left, top, width, apply)
        return used + m.top() + m.bottom()

    def _single_line(self, entries, left: int, top: int, width: int, apply: bool) -> int:
        """``QHBoxLayout`` gibi: artan genişlik esneme paylarına göre bölüştürülür."""
        extra = width - self._natural_width(entries)
        weights = [weight if kind in (STRETCH, WIDGET) else 0 for _i, kind, weight, _f in entries]
        total_weight = sum(weights)
        widths: List[int] = []
        given = 0
        growers = [i for i, wt in enumerate(weights) if wt > 0]
        for i, (item, kind, _weight, _full) in enumerate(entries):
            base = 0 if kind == STRETCH else (item.sizeHint().width() if kind == SPACING else self._hint_width(item))
            share = 0
            if total_weight and weights[i]:
                share = extra - given if i == growers[-1] else extra * weights[i] // total_weight
                given += share
            widths.append(base + share)
        heights = [self._height(item, w) if kind == WIDGET else 0
                   for (item, kind, _wt, _f), w in zip(entries, widths)]
        line_h = max(heights)
        if apply:
            x = left
            first = True
            for (item, kind, _wt, _f), w, h in zip(entries, widths, heights):
                if kind == STRETCH:
                    x += w
                    continue
                if not first:
                    x += self._hs
                first = False
                if kind == WIDGET:
                    item.setGeometry(QRect(QPoint(x, top + (line_h - h) // 2), QSize(w, h)))
                x += w
        return line_h

    def _wrapped(self, entries, left: int, top: int, width: int, apply: bool) -> int:
        y = top
        line: List[Tuple[QLayoutItem, int, int]] = []
        used = 0
        lines = 0

        def flush() -> None:
            nonlocal y, line, used, lines
            if not line:
                return
            height = max(h for _, _, h in line)
            x = left
            if apply:
                for item, w, h in line:
                    item.setGeometry(QRect(QPoint(x, y + (height - h) // 2), QSize(w, h)))
                    x += w + self._hs
            y += height + self._vs
            lines += 1
            line, used = [], 0

        for item, kind, _weight, full in entries:
            if kind != WIDGET:                                   # sarılınca boşluklar yok sayılır
                continue
            if full:
                flush()
                line = [(item, width, self._height(item, width))]
                flush()
                continue
            w = min(self._hint_width(item), width)
            needed = w if not line else used + self._hs + w
            if line and needed > width:
                flush()
                needed = w
            line.append((item, w, self._height(item, w)))
            used = needed
        flush()
        return (y - self._vs if lines else y) - top
