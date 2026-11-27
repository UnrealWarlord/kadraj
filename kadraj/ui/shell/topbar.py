"""Üst çubuk: Lockup · proje menüsü · Geri Al · 10 Dk Önceye Döndür · kayıt göstergesi · mod anahtarı · ayarlar.

Yalnızca sinyal yayınlar; asıl iş ``MainWindow``/``ctx``'tedir. Geri Al ``ctx.undo`` ile (yığın durumuna göre
etkin/pasif), "10 Dk Önceye Döndür" ``rewindRequested(10)`` yayınlar (gövde Adım 3'te).

Telefonda (mobil 6. adım): mod anahtarı yok (yalnız Basit Mod), düğmeler yalnız ikon ve kare, proje düğmesi simgesiz ve
kısa adlı, "10 Dk Önceye Döndür" proje menüsünde, kayıt göstergesi alttaki durum çubuğunda; çubuk 360 px genişliğe
(en dar yaygın telefon) sığar. Tablet dikey tutulunca çubuk daralır (küçük logo, kısa mod adları, Döndür proje menüsünde,
kayıt göstergesi alttaki şeritte) ve 600 px'e (7" tablet) sığar; telefon ve tablette proje adı sığmazsa "…" ile kısalır.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QPushButton, QToolButton, QWidget

from ... import device
from ...core import i18n
from ...core.contracts import UiMode
from ..theme.manager import ThemedMixin
from ..widgets.icons import icon_pixmap
from ..widgets.lockup import Lockup
from ..widgets.mode_switch import ModeSwitch
from ..widgets.status_bar import AutosaveIndicator


#: Çubuğun sığması gereken en dar telefon genişliği (px; Android'in yaygın 360 dp ekranı). Proje adı telefonda bu
#: genişliğe (ya da çubuk daha genişse çubuğa) sığacak kadar gösterilir, sonu "…" ile kısalır; tam ad ipucundadır.
PHONE_MIN_WIDTH = 360
#: Tabletin sığması gereken en dar genişlik (px): en küçük kenarı 600 dp olan 7" tablet, dikey tutuş.
TABLET_MIN_WIDTH = 600
#: Tablette çubuk bu genişliğin altında (dikey tutuş) daralır; yatayda masaüstündeki gibidir.
TABLET_NARROW_BELOW = 1000
_UNBOUNDED = (1 << 24) - 1                                  # Qt'nin QWIDGETSIZE_MAX'ı (PySide6 dışa vermiyor)


class TopBar(ThemedMixin, QFrame):
    modeSelected = Signal(str)
    undoRequested = Signal()
    rewindRequested = Signal(int)
    settingsRequested = Signal()
    homeRequested = Signal()

    def __init__(self, mode: UiMode = UiMode.SIMPLE, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "topbar")
        self._phone = device.is_phone()
        self._mobile = device.is_mobile()
        self._narrow = False
        self._project_name = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*((8, 6, 8, 6) if self._phone else (18, 8, 18, 8)))
        lay.setSpacing(6 if self._phone else 10)

        self.lockup = Lockup(wordmark_px=16 if self._phone else 24, show_mark=not self._phone)
        lay.addWidget(self.lockup)
        lay.addSpacing(6)
        self.project_btn = QToolButton()
        self.project_btn.setObjectName("ProjectButton")
        self.project_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.project_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly if self._phone
                                            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
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
        self.rewind_btn.setVisible(not self._phone)         # telefonda proje menüsünde
        self.rewind_action = QAction(self)                  # dar çubukta (telefon, dikey tablet) proje menüsüne konur
        self.rewind_action.triggered.connect(lambda _c=False: self.rewindRequested.emit(10))
        self.rewind_action.setVisible(self._phone)
        if not self._phone:
            lay.addSpacing(8)
        self.autosave = AutosaveIndicator()
        self.autosave.setVisible(not self._phone)           # telefonda durum çubuğundaki gösterge yeter
        lay.addWidget(self.autosave)
        if not self._phone:
            lay.addSpacing(8)
        self.mode_switch = ModeSwitch(mode)
        self.mode_switch.modeSelected.connect(self.modeSelected.emit)
        self.mode_switch.setVisible(device.legend_allowed())  # telefonda Legend yok (M1)
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
        self._size_undo()
        pad = 8 if self._phone else 12
        self.project_btn.setStyleSheet(
            f"QToolButton#ProjectButton {{ padding: 4px {pad}px; border: {tk.border_w}px solid {tk.border};"
            f" border-radius: {tk.ctl_h // 2 if tk.pill else tk.radius_ctl}px; }}")
        if self._mobile and self._project_name:             # ölçüler değişti: ad yeniden sığdırılır
            self._fit_project_name()

    def _size_undo(self) -> None:
        """Dar çubukta (telefon, dikey tablet) Geri Al kare dokunma hedefidir; geniş çubukta metne göre boyutlanır."""
        if self._phone or self._narrow:
            side = max(self.tk.px(36), self.tk.ctl_h)
            self.undo_btn.setFixedSize(side, side)
        else:
            self.undo_btn.setMinimumSize(0, 0)
            self.undo_btn.setMaximumSize(_UNBOUNDED, _UNBOUNDED)

    def set_compact(self, compact: bool) -> None:
        """Dar pencere / Büyük Yazı: Geri Al ve Döndür yalnızca ikon (ipucu metniyle), kayıt göstergesi yalnızca nokta."""
        if compact == getattr(self, "_compact", None):
            return
        self._compact = compact
        self.retranslate()
        self.autosave.set_compact(compact)

    def set_narrow(self, narrow: bool) -> None:
        """Dikey tablet: küçük logo (işaretsiz), kısa mod adları, "10 Dk Önceye Döndür" proje menüsünde, kayıt göstergesi
        yalnız alttaki durum çubuğunda. Telefon zaten hep dardır."""
        if narrow == self._narrow or self._phone:
            return
        self._narrow = narrow
        self.lockup.set_wordmark_px(16 if narrow else 24)
        self.lockup.set_show_mark(not narrow)
        self.mode_switch.set_short(narrow)
        self.rewind_btn.setVisible(not narrow)
        self.rewind_action.setVisible(narrow)
        self.autosave.setVisible(not narrow)
        self.project_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly if narrow
                                            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._size_undo()
        self.layout().setContentsMargins(*((10, 8, 10, 8) if narrow else (18, 8, 18, 8)))
        self.layout().setSpacing(8 if narrow else 10)

    @property
    def narrow(self) -> bool:
        return self._phone or self._narrow

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.set_compact(self._phone or self.width() < self.tk.px(1250))
        if self._mobile:
            self.set_narrow(self.width() < TABLET_NARROW_BELOW)
            self._fit_project_name(self.width())                    # ilk gösterimde de gerçek genişlik

    def retranslate(self) -> None:
        compact = bool(getattr(self, "_compact", False))
        self.undo_btn.setText("" if compact else i18n.t("top.undo"))
        self.rewind_btn.setText("" if compact else i18n.t("top.rewind"))
        self.undo_btn.setToolTip(i18n.t("top.undo_tip"))
        self.rewind_btn.setToolTip(i18n.t("top.rewind_tip"))
        self.rewind_action.setText(i18n.t("top.rewind"))
        self.gear.setToolTip(i18n.t("top.settings"))
        self.gear.setAccessibleName(i18n.t("top.settings"))
        self.undo_btn.setAccessibleName(i18n.t("top.undo"))
        self.rewind_btn.setAccessibleName(i18n.t("top.rewind"))

    def set_project_name(self, name: str) -> None:
        text = name or i18n.t("top.no_project")
        self._project_name = text
        self.project_btn.setToolTip(text)
        self.project_btn.setAccessibleName(text)
        if self._mobile:                                                # telefon/tablette çubuk taşmasın
            self._fit_project_name()
            return
        self.project_btn.setText("  " + text + "  ▾")

    def _fit_project_name(self, width: Optional[int] = None) -> None:
        """Telefon/tablette proje adını çubuğa sığdırır: diğer öğelerden kalan genişlik kadar, sonu "…"."""
        btn = self.project_btn
        fm = btn.fontMetrics()
        prefix, suffix = ("", " ▾") if self.narrow else ("  ", "  ▾")
        btn.setText(prefix + suffix)                                    # yalnız düğmenin kendi payı ölçülsün
        others = self.layout().minimumSize().width() - btn.minimumSizeHint().width()
        chrome = btn.minimumSizeHint().width() - fm.horizontalAdvance(prefix + suffix)
        floor = PHONE_MIN_WIDTH if self._phone else TABLET_MIN_WIDTH
        if width is None:
            width = self.width() if self.isVisible() else floor
        target = max(floor, width)
        room = max(fm.horizontalAdvance("…"), target - others - chrome - fm.horizontalAdvance(prefix + suffix))
        btn.setText(prefix + fm.elidedText(self._project_name, Qt.TextElideMode.ElideRight, room) + suffix)

    def set_undo_enabled(self, enabled: bool, text: str = "") -> None:
        self.undo_btn.setEnabled(enabled)
        self.undo_btn.setToolTip(i18n.t("top.undo_tip") + (f" — {text}" if text else ""))
