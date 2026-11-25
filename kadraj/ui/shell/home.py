"""Basit Mod ana ekranı — "Merhaba! Bugün ne yapalım?" (tuval: V2Home, Basit).

Görev kartları (sihirbazda seçilenler başa), "Kaldığın yerden devam et" son proje kartları,
dosya bırakma alanı ve maskot ipucu. Widget durum tutmaz: kartlar ``ctx.registry`` ve ``ctx.recent``'tan
her ``refresh()``'te yeniden kurulur.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout,
                               QWidget)

from ...core import i18n
from ...core.contracts import RecentEntry
from ..widgets.drop_zone import DropZone
from ..widgets.help_bubble import MascotTip
from ..widgets.squishy_button import SquishyButton
from ..widgets.task_card import TaskCard

# Sihirbaz görev kimliği → modül kimliği
TASK_TO_MODULE: Dict[str, str] = {"dl": "downloader", "rec": "recorder", "edit": "editor",
                                  "photo": "photo", "film": "series"}


def human_ago(epoch: float, now: Optional[float] = None) -> str:
    if not epoch:
        return ""
    delta = max(0, int((now or time.time()) - epoch))
    if delta < 60:
        return i18n.t("ago.now")
    if delta < 3600:
        return i18n.t("ago.minutes", n=delta // 60)
    if delta < 86400:
        return i18n.t("ago.hours", n=delta // 3600)
    return i18n.t("ago.days", n=delta // 86400)


class _RecentCard(QFrame):
    clicked = Signal(str)

    def __init__(self, entry: RecentEntry, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setProperty("role", "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(entry.name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(2)
        name = QLabel(entry.name)
        name.setProperty("role", "h2")
        meta = QLabel(human_ago(entry.last_opened))
        meta.setProperty("role", "muted")
        lay.addWidget(name)
        lay.addWidget(meta)

    def mousePressEvent(self, e) -> None:
        self.clicked.emit(self.entry.path)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.entry.path)
        else:
            super().keyPressEvent(e)


class SimpleHome(QWidget):
    moduleRequested = Signal(str)
    openProjectRequested = Signal(str)
    newProjectRequested = Signal()
    openFolderRequested = Signal()
    filesDropped = Signal(list)
    helpRequested = Signal()

    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.cards: List[TaskCard] = []
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._inner = QWidget()
        self._inner.setObjectName("Root")
        scroll.setWidget(self._inner)
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(40, 30, 40, 30)
        self._lay.setSpacing(18)

        self.greeting = QLabel("")
        self.greeting.setProperty("role", "title")
        self.sub = QLabel("")
        self.sub.setProperty("role", "muted")
        self._lay.addWidget(self.greeting)
        self._lay.addWidget(self.sub)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 8, 0, 8)
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(18)
        self._lay.addWidget(self._grid_host)

        self.resume_title = QLabel("")
        self.resume_title.setProperty("role", "h2")
        self._lay.addWidget(self.resume_title)
        self._resume_host = QWidget()
        self._resume = QHBoxLayout(self._resume_host)
        self._resume.setContentsMargins(0, 0, 0, 0)
        self._resume.setSpacing(16)
        self._lay.addWidget(self._resume_host)

        self.drop = DropZone("", "", "")
        self.drop.filesDropped.connect(self.filesDropped.emit)
        self.drop.browseRequested.connect(self.openFolderRequested.emit)
        self._lay.addWidget(self.drop)
        self.tip = MascotTip("", "", "")
        self.tip.actionClicked.connect(self.helpRequested.emit)
        self._lay.addWidget(self.tip)
        self._lay.addStretch(1)
        self._columns = 0
        self.refresh()

    # -- içerik -----------------------------------------------------------
    def _ordered_entries(self):
        entries = [e for e in self.ctx.registry.entries() if e.id != "settings"]
        chosen = [TASK_TO_MODULE[t] for t in (self.ctx.settings.get("wizard.tasks") or []) if t in TASK_TO_MODULE]
        rank = {mid: i for i, mid in enumerate(chosen)}
        return sorted(entries, key=lambda e: (rank.get(e.id, len(rank)), e.order))

    def refresh(self) -> None:
        """Dil/proje/kayıt değişince bütün dinamik içeriği yeniden kurar."""
        self.greeting.setText(i18n.t("home.greeting"))
        self.sub.setText(i18n.t("home.sub"))
        self.resume_title.setText(i18n.t("home.resume"))
        self.drop.retranslate(i18n.t("home.drop.title"), i18n.t("home.drop.body"), i18n.t("home.drop.button"))
        self.tip.retranslate(i18n.t("home.tip.title"), i18n.t("home.tip.body"), i18n.t("home.tip.action"))

        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = []
        for e in self._ordered_entries():
            card = TaskCard(i18n.term(e.title_key), i18n.t(e.desc_key), e.icon, e.accent,
                            chip=e.chip, soon=not e.available, shortcut=e.shortcut)
            card.setObjectName(f"card_{e.id}")
            card.clicked.connect(lambda _=False, mid=e.id: self.moduleRequested.emit(mid))
            self.cards.append(card)
        self._columns = 0
        self._reflow()

        while self._resume.count():
            item = self._resume.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()                           # deleteLater beklerken eski kart boyanmasın
                w.setParent(None)
                w.deleteLater()
        recents = self.ctx.recent.get_recent()[:3]
        if recents:
            for r in recents:
                card = _RecentCard(r)
                card.clicked.connect(self.openProjectRequested.emit)
                self._resume.addWidget(card, 1)
            for _ in range(3 - len(recents)):
                self._resume.addStretch(1)
        else:
            empty = QFrame()
            empty.setProperty("role", "card")
            el = QHBoxLayout(empty)
            el.setContentsMargins(20, 16, 20, 16)
            msg = QLabel(i18n.t("home.resume.empty"))
            msg.setProperty("role", "muted")
            msg.setWordWrap(True)
            btn = SquishyButton(i18n.t("home.new_project"), variant="primary", icon="plus")
            btn.clicked.connect(self.newProjectRequested.emit)
            el.addWidget(msg, 1)
            el.addWidget(btn)
            self._resume.addWidget(empty, 1)

    def _reflow(self) -> None:
        width = max(self.width(), 600)
        cols = 3 if width >= 960 else (2 if width >= 640 else 1)
        if cols == self._columns and self._grid.count() == len(self.cards):
            return
        self._columns = cols
        while self._grid.count():
            self._grid.takeAt(0)
        for i, c in enumerate(self.cards):
            self._grid.addWidget(c, i // cols, i % cols)
        for col in range(cols):
            self._grid.setColumnStretch(col, 1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._reflow()
