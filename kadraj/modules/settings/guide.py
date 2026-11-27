"""Nereden Alırım? — resimli 5 adımlı rehber (QPainter çizimleri).

``GuideView`` hem Basit'te bir pencereye (``GuideDialog``) hem Legend'de satır içi çekmeceye
konur. Rehber yalnızca adresi gösterir ve panoya kopyalatır; tarayıcıyı kendiliğinden açmaz.
Sağlayıcı adresleri her sağlayıcının resmî sayfasına işaret eder ve yayından önce elle doğrulanmalıdır.
"""
from __future__ import annotations

from typing import Mapping, Optional, Tuple

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ...core import i18n
from ...ui import adaptive
from ...ui.theme.manager import current_tokens
from ...ui.widgets import SquishyButton
from .strings import K

STEP_COUNT = 5

#: (sağlayıcı kimliği, resmî sitenin adresi). Adresler yayından önce doğrulanmalıdır.
GUIDE_HOSTS: Tuple[Tuple[str, str], ...] = (
    ("anthropic", "platform.claude.com"),
    ("openai_compat", "platform.openai.com"),
    ("google_imagen", "aistudio.google.com"),
    ("elevenlabs", "elevenlabs.io"),
    ("bfl_flux", "dashboard.bfl.ai"),
    ("pexels", "pexels.com"),
    ("pixabay", "pixabay.com"),
    ("freesound", "freesound.org"),
)


def host_of(provider_id: str) -> str:
    return dict(GUIDE_HOSTS).get(provider_id, "")


class StepCanvas(QWidget):
    """Adım çizimi: 0 tarayıcı, 1 menü, 2 "Create Key" düğmesi, 3 kopyalama, 4 yapıştırma + onay."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._step = 0
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_step(self, step: int) -> None:
        self._step = max(0, min(step, STEP_COUNT - 1))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 — Qt adı
        tk = current_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.setPen(QPen(QColor(tk.border), 1))
        p.setBrush(QColor(tk.surface2))
        p.drawRoundedRect(frame, 14, 14)
        box = frame.adjusted(56, 22, -24, -22)
        draw = (_browser, _menu, _button, _clipboard, _paste)[self._step]
        draw(p, box, tk)
        badge = QRectF(frame.left() + 14, frame.top() + 14, 30, 30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tk.primary))
        p.drawEllipse(badge)
        p.setPen(QColor(tk.on_primary))
        p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), str(self._step + 1))
        p.end()


def _browser(p: QPainter, b: QRectF, tk) -> None:
    p.setPen(QPen(QColor(tk.border), 1))
    p.setBrush(QColor(tk.card))
    p.drawRoundedRect(b, 10, 10)
    bar = QRectF(b.left(), b.top(), b.width(), 22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tk.border))
    p.drawRoundedRect(bar, 10, 10)
    for i, color in enumerate((tk.bad, tk.warn, tk.good)):
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(b.left() + 10 + i * 14, b.top() + 7, 8, 8))
    p.setBrush(QColor(tk.bg))
    p.drawRoundedRect(QRectF(b.left() + 14, b.top() + 34, b.width() * 0.7, 26), 13, 13)
    p.setBrush(QColor(tk.primary))
    p.drawEllipse(QRectF(b.left() + 22, b.top() + 42, 10, 10))


def _menu(p: QPainter, b: QRectF, tk) -> None:
    side = QRectF(b.left(), b.top(), b.width() * 0.38, b.height())
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tk.card))
    p.drawRoundedRect(side, 10, 10)
    for i in range(4):
        item = QRectF(side.left() + 10, side.top() + 14 + i * 34, side.width() - 20, 24)
        p.setBrush(QColor(tk.primary if i == 2 else tk.border))
        p.drawRoundedRect(item, 8, 8)
    p.setBrush(QColor(tk.card))
    p.setPen(QPen(QColor(tk.border), 1))
    p.drawRoundedRect(QRectF(side.right() + 12, b.top(), b.width() - side.width() - 12, b.height()), 10, 10)


def _button(p: QPainter, b: QRectF, tk) -> None:
    btn = QRectF(b.center().x() - b.width() * 0.3, b.center().y() - 20, b.width() * 0.6, 40)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tk.primary))
    p.drawRoundedRect(btn, tk.radius_ctl, tk.radius_ctl)
    p.setPen(QColor(tk.on_primary))
    p.drawText(btn, int(Qt.AlignmentFlag.AlignCenter), "Create Key")


def _clipboard(p: QPainter, b: QRectF, tk) -> None:
    board = QRectF(b.center().x() - 60, b.top() + 10, 120, b.height() - 20)
    p.setPen(QPen(QColor(tk.border), 2))
    p.setBrush(QColor(tk.card))
    p.drawRoundedRect(board, 10, 10)
    p.setBrush(QColor(tk.primary))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(board.center().x() - 22, board.top() - 6, 44, 14), 6, 6)
    p.setBrush(QColor(tk.border))
    for i in range(3):
        p.drawRoundedRect(QRectF(board.left() + 16, board.top() + 28 + i * 18, board.width() - 32, 8), 4, 4)


def _paste(p: QPainter, b: QRectF, tk) -> None:
    field = QRectF(b.left(), b.center().y() - 16, b.width() * 0.7, 32)
    p.setPen(QPen(QColor(tk.border), 1))
    p.setBrush(QColor(tk.card))
    p.drawRoundedRect(field, 8, 8)
    p.setPen(QPen(QColor(tk.primary), 2))
    p.drawLine(int(field.left() + 12), int(field.top() + 8), int(field.left() + 12), int(field.bottom() - 8))
    check = QRectF(field.right() + 22, field.center().y() - 18, 36, 36)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tk.good))
    p.drawEllipse(check)
    p.setPen(QPen(QColor(tk.on_primary), 3))
    p.drawLine(int(check.left() + 9), int(check.center().y()), int(check.left() + 15), int(check.bottom() - 10))
    p.drawLine(int(check.left() + 15), int(check.bottom() - 10), int(check.right() - 8), int(check.top() + 11))


class GuideView(QWidget):
    """Sağlayıcı sekmeleri + 5 adım + geri/ileri + "Adresi kopyala". ``providers``: kimlik → görünen ad."""

    def __init__(self, providers: Mapping[str, str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._providers = list(providers.items())
        self._index = 0
        self._step = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._tabs_row = adaptive.hbox()                              # telefonda servis düğmeleri alt satıra geçer
        self._tab_buttons = []
        for pid, name in self._providers:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setProperty("role", "topbtn")
            btn.setAccessibleName(name)
            btn.clicked.connect(lambda _c=False, i=len(self._tab_buttons): self.set_provider(i))
            self._tab_buttons.append(btn)
            self._tabs_row.addWidget(btn)
        self._tabs_row.addStretch(1)
        lay.addLayout(self._tabs_row)

        self.canvas = StepCanvas()
        lay.addWidget(self.canvas)
        self.counter = QLabel("")
        self.counter.setProperty("role", "muted")
        lay.addWidget(self.counter)
        self.text = QLabel("")
        self.text.setWordWrap(True)
        lay.addWidget(self.text)

        nav = adaptive.hbox()
        self.prev = SquishyButton(i18n.t(K.GUIDE_PREV), variant="secondary")
        self.next = SquishyButton(i18n.t(K.GUIDE_NEXT), variant="primary")
        self.copy = SquishyButton(i18n.t(K.GUIDE_COPY_HOST), variant="ghost")
        self.prev.clicked.connect(lambda: self.set_step(self._step - 1))
        self.next.clicked.connect(lambda: self.set_step(self._step + 1))
        self.copy.clicked.connect(self._copy_host)
        nav.addWidget(self.prev)
        nav.addWidget(self.copy)
        nav.addStretch(1)
        nav.addWidget(self.next)
        lay.addLayout(nav)
        self.note = QLabel("")
        self.note.setProperty("role", "hint")
        lay.addWidget(self.note)
        if self._providers:
            self.set_provider(0)

    # -- durum -------------------------------------------------------------
    def provider_id(self) -> str:
        return self._providers[self._index][0] if self._providers else ""

    def select(self, provider_id: str) -> bool:
        ids = [pid for pid, _name in self._providers]
        if provider_id in ids:
            self.set_provider(ids.index(provider_id))
            return True
        return False

    def set_provider(self, index: int) -> None:
        if not self._providers:
            return
        self._index = max(0, min(index, len(self._providers) - 1))
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == self._index)
        self.set_step(0)

    def set_step(self, step: int) -> None:
        self._step = max(0, min(step, STEP_COUNT - 1))
        self.canvas.set_step(self._step)
        host = host_of(self.provider_id()) or "—"
        self.text.setText(i18n.t(_STEP_KEYS[self._step], host=host))
        self.counter.setText(i18n.t(K.GUIDE_STEP_OF, n=self._step + 1, total=STEP_COUNT))
        self.prev.setEnabled(self._step > 0)
        self.next.setEnabled(self._step < STEP_COUNT - 1)
        self.note.setText("")

    def step(self) -> int:
        return self._step

    def _copy_host(self) -> None:
        host = host_of(self.provider_id())
        if host:
            QApplication.clipboard().setText(host)
            self.note.setText(i18n.t(K.GUIDE_COPIED))


_STEP_KEYS = (K.GUIDE_STEP1, K.GUIDE_STEP2, K.GUIDE_STEP3, K.GUIDE_STEP4, K.GUIDE_STEP5)


class GuideDialog(QDialog):
    """Basit Mod'da "Nereden Alırım?" penceresi."""

    def __init__(self, providers: Mapping[str, str], parent: Optional[QWidget] = None,
                 start: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.t(K.GUIDE_TITLE))
        self.setModal(True)
        if adaptive.is_mobile():                                      # ekrandan taşmasın
            adaptive.fit_dialog(self, 640, 620)
        self.view = GuideView(providers, self)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view)
        close = SquishyButton(i18n.t(K.BTN_CLOSE), variant="secondary")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        if start:
            ids = [pid for pid, _name in self.view._providers]
            if start in ids:
                self.view.set_provider(ids.index(start))
