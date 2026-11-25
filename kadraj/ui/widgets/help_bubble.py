"""``HelpBubble`` — "?" düğmesi + konuşma balonu, ve maskot (``Mascot`` / ``MascotTip``).

* ``HelpBubble(text)``  : küçük yuvarlak "?" düğmesi; tıklanınca/odaklanıp Enter'a basılınca altında
  konuşma balonu açılır, dışarı tıklanınca kapanır. Klavyeden erişilebilir.
* ``Mascot``            : Basit'te yuvarlak, gülen blob; Legend'de sade bir "?" rozeti.
* ``MascotTip``         : ana ekrandaki "Ne yapmalıyım?" kartı (maskot + metin + eylem düğmesi).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from ...core import i18n
from .. import fonts
from ..theme.manager import ThemedMixin, current_tokens
from .icons import draw_icon
from .squishy_button import SquishyButton


class _BubblePopup(QWidget):
    """Kuyruklu konuşma balonu (Qt.Popup: dışarı tıklayınca kapanır)."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        tk = current_tokens()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(tk.px(16), tk.px(14) + 8, tk.px(16), tk.px(14))
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(tk.px(300))
        self._label.setStyleSheet(f"color: {tk.text}; background: transparent;")
        lay.addWidget(self._label)

    def paintEvent(self, _e) -> None:
        tk = current_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(1, 9, -1, -1)
        r = 18 if tk.pill else tk.radius_card
        path = QPainterPath()
        path.addRoundedRect(body, r, r)
        tail = QPainterPath()                       # üstte küçük üçgen kuyruk (düğmeye bakar)
        tx = 22.0
        tail.moveTo(tx - 8, body.top())
        tail.lineTo(tx, body.top() - 8)
        tail.lineTo(tx + 8, body.top())
        tail.closeSubpath()
        path = path.united(tail)
        p.fillPath(path, QColor(tk.card))
        p.setPen(QPen(QColor(tk.border if not tk.high_contrast else tk.text), tk.border_w))
        p.drawPath(path)
        p.end()


class HelpBubble(ThemedMixin, QPushButton):
    """``HelpBubble("Bu ne işe yarar?")`` — yanına konulan yuvarlak yardım düğmesi."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self._text = text
        self._popup: Optional[_BubblePopup] = None
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(i18n.t("help.button"))
        # genel QPushButton QSS'inin min-height/padding kuralları yuvarlak düğmeyi bozmasın
        self.setStyleSheet("QPushButton { min-height: 0px; min-width: 0px; padding: 0px; border: none; background: transparent; }")
        self.clicked.connect(self.toggle)
        self._bind_theme()
        self._on_theme()

    def set_text(self, text: str) -> None:
        self._text = text
        self.setAccessibleDescription(text)

    @property
    def text_(self) -> str:
        return self._text

    def _on_theme(self) -> None:
        size = max(22, self.tk.px(26))
        self.setFixedSize(size, size)

    def toggle(self) -> None:
        if self._popup is not None and self._popup.isVisible():
            self._popup.close()
            return
        self.show_bubble()

    def show_bubble(self) -> None:
        self._popup = _BubblePopup(self._text, self)
        self._popup.adjustSize()
        origin = self.mapToGlobal(self.rect().bottomLeft())
        x, y = origin.x() - 14, origin.y() + 2
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.left() + 4, min(x, geo.right() - self._popup.width() - 4))
            y = min(y, geo.bottom() - self._popup.height() - 4)
        self._popup.move(x, y)
        self._popup.show()

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        hover = self.underMouse()
        fill = tk.accent("lavender").tone if not tk.is_legend else tk.surface2
        if hover:
            fill = tk.accent("lavender").base if not tk.is_legend else tk.border
        p.setBrush(QColor(fill))
        p.setPen(QPen(QColor(tk.accent("lavender").ink if not tk.is_legend else tk.border), tk.border_w))
        radius = rect.height() / 2 if tk.pill else tk.radius_ctl
        p.drawRoundedRect(rect, radius, radius)
        font = fonts.qfont(tk.font_display, rect.height() * 0.62, weight=800)
        p.setFont(font)
        p.setPen(QColor(tk.accent("lavender").ink if not tk.is_legend else tk.text2))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "?")
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(tk.text if tk.high_contrast else tk.primary_deep), 2))
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        p.end()


class Mascot(ThemedMixin, QWidget):
    """Basit'te şeftali blob + gözler + gülümseme; Legend'de sade "?" rozeti."""

    def __init__(self, size: int = 64, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._bind_theme()
        self._on_theme()

    def _on_theme(self) -> None:
        s = self.tk.px(self._size)
        self.setFixedSize(s, s)

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if tk.is_legend:
            p.setBrush(QColor(tk.surface2))
            p.setPen(QPen(QColor(tk.border), tk.border_w))
            p.drawRoundedRect(r, tk.radius_card, tk.radius_card)
            draw_icon(p, r.adjusted(r.width() * 0.22, r.height() * 0.22, -r.width() * 0.22,
                                    -r.height() * 0.22), "help", tk.primary)
            p.end()
            return
        acc = tk.accent("peach")
        blob = QPainterPath()
        blob.addRoundedRect(r, r.width() * 0.46, r.height() * 0.42)
        p.fillPath(blob, QColor(acc.base))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(acc.ink))
        eye_r = r.width() * 0.065
        for ex in (0.34, 0.66):
            p.drawEllipse(QPointF(r.x() + r.width() * ex, r.y() + r.height() * 0.42), eye_r, eye_r * 1.25)
        p.setBrush(QColor("#FF8E8E"))                                   # yanaklar
        p.setOpacity(0.55)
        for cx in (0.2, 0.8):
            p.drawEllipse(QPointF(r.x() + r.width() * cx, r.y() + r.height() * 0.58), eye_r * 1.4, eye_r * 0.9)
        p.setOpacity(1.0)
        pen = QPen(QColor(acc.ink), max(2.0, r.width() * 0.045), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        smile = QPainterPath()
        smile.moveTo(r.x() + r.width() * 0.4, r.y() + r.height() * 0.62)
        smile.quadTo(r.center().x(), r.y() + r.height() * 0.76, r.x() + r.width() * 0.6, r.y() + r.height() * 0.62)
        p.drawPath(smile)
        p.end()


class MascotTip(QFrame):
    """"Ne yapmalıyım?" kartı: maskot + başlık + metin + eylem düğmesi (``actionClicked``)."""

    actionClicked = Signal()

    def __init__(self, title: str, body: str, action: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(16)
        lay.addWidget(Mascot(60), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "h2")
        self.body_label = QLabel(body)
        self.body_label.setWordWrap(True)
        self.body_label.setProperty("role", "muted")
        self.button = SquishyButton(action, variant="secondary")
        self.button.clicked.connect(self.actionClicked.emit)
        col.addWidget(self.title_label)
        col.addWidget(self.body_label)
        col.addSpacing(4)
        col.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addLayout(col, 1)

    def retranslate(self, title: str, body: str, action: str) -> None:
        self.title_label.setText(title)
        self.body_label.setText(body)
        self.button.setText(action)
