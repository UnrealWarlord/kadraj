"""Üst çubuk: Lockup · proje menüsü · Geri Al · 10 Dk Önceye Döndür · kayıt göstergesi · mod anahtarı · ayarlar.

Yalnızca sinyal yayınlar; asıl iş ``MainWindow``/``ctx``'tedir. Geri Al ``ctx.undo`` ile (yığın durumuna göre
etkin/pasif), "10 Dk Önceye Döndür" ``rewindRequested(10)`` yayınlar (gövde Adım 3'te).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QPushButton, QToolButton, QWidget

from ...core import i18n
from ...core.contracts import UiMode
from ..theme.manager import ThemedMixin
from ..widgets.icons import icon_pixmap
from ..widgets.lockup import Lockup
from ..widgets.mode_switch import ModeSwitch
from ..widgets.status_bar import AutosaveIndicator


class TopBar(ThemedMixin, QFrame):
    modeSelected = Signal(str)
    undoRequested = Signal()
    rewindRequested = Signal(int)
    settingsRequested = Signal()
    homeRequested = Signal()

    def __init__(self, mode: UiMode = UiMode.SIMPLE, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "topbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(10)

        self.lockup = Lockup(wordmark_px=24, show_mark=True)
        lay.addWidget(self.lockup)
        lay.addSpacing(6)
        self.project_btn = QToolButton()
        self.project_btn.setObjectName("ProjectButton")
        self.project_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.project_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.project_menu = QMenu(self.project_btn)
        self.project_btn.setMenu(self.project_menu)
        self.project_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self.project_btn)
        lay.addStretch(1)

        self.undo_btn = QPushButton("")
        self.undo_btn.setProperty("role", "topbtn")
        self.undo_btn.clicked.connect(self.undoRequested.emit)
        self.rewind_btn = QPushButton("")
        self.rewind_btn.setProperty("role", "topbtn")
        self.rewind_btn.clicked.connect(lambda: self.rewindRequested.emit(10))
        for b in (self.undo_btn, self.rewind_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(b)
        lay.addSpacing(8)
        self.autosave = AutosaveIndicator()
        lay.addWidget(self.autosave)
        lay.addSpacing(8)
        self.mode_switch = ModeSwitch(mode)
        self.mode_switch.modeSelected.connect(self.modeSelected.emit)
        lay.addWidget(self.mode_switch)
        self.gear = QPushButton("")
        self.gear.setProperty("role", "gear")
        self.gear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gear.clicked.connect(self.settingsRequested.emit)
        lay.addWidget(self.gear)
        self._bind_theme()
        self.retranslate()
        self._on_theme()

    def _on_theme(self) -> None:
        tk = self.tk
        color = tk.text
        self.undo_btn.setIcon(QIcon(icon_pixmap("undo", color, 18)))
        self.rewind_btn.setIcon(QIcon(icon_pixmap("rewind", color, 18)))
        self.gear.setIcon(QIcon(icon_pixmap("gear", color, 20)))
        self.project_btn.setIcon(QIcon(icon_pixmap("folder", color, 18)))
        for b in (self.undo_btn, self.rewind_btn):
            b.setIconSize(QSize(tk.px(18), tk.px(18)))
        side = max(tk.px(36), tk.ctl_h)
        self.gear.setFixedSize(side, side)
        self.gear.setIconSize(QSize(tk.px(20), tk.px(20)))
        self.project_btn.setStyleSheet(
            f"QToolButton#ProjectButton {{ padding: 4px 12px; border: {tk.border_w}px solid {tk.border};"
            f" border-radius: {tk.ctl_h // 2 if tk.pill else tk.radius_ctl}px; }}")

    def set_compact(self, compact: bool) -> None:
        """Dar pencere / Büyük Yazı: Geri Al ve Döndür yalnızca ikon (ipucu metniyle), kayıt göstergesi yalnızca nokta."""
        if compact == getattr(self, "_compact", None):
            return
        self._compact = compact
        self.retranslate()
        self.autosave.set_compact(compact)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.set_compact(self.width() < self.tk.px(1250))

    def retranslate(self) -> None:
        compact = bool(getattr(self, "_compact", False))
        self.undo_btn.setText("" if compact else i18n.t("top.undo"))
        self.rewind_btn.setText("" if compact else i18n.t("top.rewind"))
        self.undo_btn.setToolTip(i18n.t("top.undo_tip"))
        self.rewind_btn.setToolTip(i18n.t("top.rewind_tip"))
        self.gear.setToolTip(i18n.t("top.settings"))
        self.gear.setAccessibleName(i18n.t("top.settings"))
        self.undo_btn.setAccessibleName(i18n.t("top.undo"))
        self.rewind_btn.setAccessibleName(i18n.t("top.rewind"))

    def set_project_name(self, name: str) -> None:
        self.project_btn.setText(("  " + name + "  ▾") if name else ("  " + i18n.t("top.no_project") + "  ▾"))

    def set_undo_enabled(self, enabled: bool, text: str = "") -> None:
        self.undo_btn.setEnabled(enabled)
        self.undo_btn.setToolTip(i18n.t("top.undo_tip") + (f" — {text}" if text else ""))
