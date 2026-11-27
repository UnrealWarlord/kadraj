"""Ana pencere — tek ``QMainWindow``; Basit ↔ Legend geçişi yalnızca sayfaları ve temayı değiştirir.

Basit: ``QStackedWidget`` içinde ana ekran (görev kartları) + modül sayfaları ("← Ana ekran" çerçevesiyle).
Legend: üstte menü çubuğu, ortada çalışma alanları (Proje merkezi, Kurgu, Stüdyo, Yakala, Sistem), çevrede
``QDockWidget`` panelleri (Hızlı Başlat, Görev Kuyruğu, Sistem); paneller koparılabilir ve çalışma alanı başına
``saveState()`` ile saklanır.

Pencere **durum tutmaz**: proje, geri alma geçmişi, seçili modül ``ctx``'tedir; mod geçişinde yalnızca görünüm değişir.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QWindow
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QInputDialog, QMainWindow, QMenu, QMenuBar,
                               QMessageBox, QStackedWidget, QVBoxLayout, QWidget)

from ... import branding, bridge, device, updates
from ...core import i18n
from ...core.contracts import PROJECT_FILE_NAME, JobStatus, SmartAction, SmartError, Topics, UiMode
from ...core.registry import ModuleEntry
from .dialogs import AboutDialog, ErrorDialog
from .home import SimpleHome
from .legend import JobsPanel, LegendCenter, QuickStartPanel, SystemPanel
from .pages import PageFrame, build_module_page
from .topbar import TopBar
from ..widgets.status_bar import StatusBar
from .. import adaptive


def _b64(data: QByteArray) -> str:
    return bytes(data.toBase64().data()).decode("ascii")


def _from_b64(text: str) -> QByteArray:
    return QByteArray.fromBase64(text.encode("ascii"))


#: Ana ekranda "çıkmak için bir kez daha geri bas" süresi (saniye).
BACK_EXIT_WINDOW_S = 2.5


class _BackKey(QObject):
    """Android geri tuşu (``Qt.Key_Back``), ana pencereye geldiğinde: açık sayfadan ana ekrana döner; ana ekranda
    iki kez basılınca karşılanmaz ve Qt pencereyi kapatır (uygulamadan çıkış). Pencereler (iletişim kutuları, menüler)
    kendi pencereleri olduğundan Qt onları kendisi kapatır."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self._accepted = False

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) or not isinstance(obj, QWindow):
            return False
        if event.key() != Qt.Key.Key_Back or obj is not self.window.windowHandle():
            return False
        if kind == QEvent.Type.KeyPress and not event.isAutoRepeat():
            self._accepted = self.window.handle_back()
        if self._accepted:
            event.accept()
            return True
        return False


class MainWindow(QMainWindow):
    rewindRequested = Signal(int)

    def __init__(self, ctx, theme, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx, self.theme = ctx, theme
        self.setWindowTitle(branding.window_title())
        self.resize(1320, 860)
        if not device.is_mobile():                 # telefon/tablette pencere ekranın kendisi (6. adım)
            self.setMinimumSize(920, 620)
        self._portrait_docks: List[QDockWidget] = []   # tablet dikeyde gizlenen Legend panelleri
        self._back_at = 0.0                            # ana ekranda son geri tuşu (iki kez basınca çıkış)
        if device.is_android():
            self._back_key = _BackKey(self)
            QApplication.instance().installEventFilter(self._back_key)
        adaptive.touch_scroll_on_show(self)          # telefon/tablet: ana ekran ve paneller parmakla kayar
        self.setDockNestingEnabled(True)
        self._frame: Optional[PageFrame] = None
        self._error_dialogs: List[ErrorDialog] = []
        self._unsubs: list = []

        # --- üst çubuk + menü ---------------------------------------------
        self.topbar = TopBar(ctx.ui_mode)
        self.menu_bar = QMenuBar()
        self.menu_bar.setNativeMenuBar(False)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        tl.addWidget(self.topbar)
        tl.addWidget(self.menu_bar)
        self.setMenuWidget(top)

        # --- gövde -----------------------------------------------------------
        self.body = adaptive.stack()              # telefon/tablette boyutu yalnız görünen sayfa belirler
        self.simple = adaptive.stack()
        self.home = SimpleHome(ctx)
        self.simple.addWidget(self.home)
        self.legend = LegendCenter(ctx)
        self.body.addWidget(self.simple)
        self.body.addWidget(self.legend)
        self.setCentralWidget(self.body)

        # --- Legend dock'ları ------------------------------------------------
        self.quick = QuickStartPanel(ctx)
        self.jobs_panel = JobsPanel(ctx)
        self.system_panel = SystemPanel(ctx)
        self.dock_quick = self._dock("dock_quick", self.quick, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.dock_jobs = self._dock("dock_jobs", self.jobs_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.dock_system = self._dock("dock_system", self.system_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.docks = [self.dock_quick, self.dock_jobs, self.dock_system]
        self.splitDockWidget(self.dock_jobs, self.dock_system, Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_quick], [250], Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_jobs], [280], Qt.Orientation.Horizontal)
        self._default_dock_state: Optional[QByteArray] = None

        # --- durum çubuğu ------------------------------------------------------
        self.status = StatusBar()
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setStyleSheet("QStatusBar { border: none; } QStatusBar::item { border: none; }")
        sb.addWidget(self.status, 1)

        self._make_actions()
        self._wire()
        self.retranslate()
        self._apply_mode(ctx.ui_mode, animate=False)
        self._restore_geometry()
        self._sync_project()
        self._sync_undo()

    # ------------------------------------------------------------------ kurulum
    def _dock(self, name: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(area, dock)
        return dock

    def _act(self, key: str, slot, shortcut: str = "", checkable: bool = False) -> QAction:
        action = QAction(self)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(slot)
        action.setData(key)
        self.addAction(action)                      # menü gizliyken (Basit Mod) de kısayol çalışsın
        self.actions_by_key[key] = action
        return action

    def _make_actions(self) -> None:
        self.actions_by_key: Dict[str, QAction] = {}
        self._act("new", self.new_project, "Ctrl+N")
        self._act("open", self.open_folder, "Ctrl+O")
        self._act("save", self.save_project, "Ctrl+S")
        self._act("close", self.close_project)
        self._act("quit", self.close, "Ctrl+Q")
        self._act("undo", self.undo, "Ctrl+Z")
        self._act("redo", self.redo, "Ctrl+Shift+Z")
        self._act("undo_rewind", self.undo_rewind)
        self._act("toggle_mode", self.toggle_mode, "Ctrl+M").setEnabled(device.legend_allowed())
        self._act("reset_layout", self.reset_layout)
        self._act("large_text", lambda c: self._set_a11y("large_text", c), checkable=True)
        self._act("high_contrast", lambda c: self._set_a11y("high_contrast", c), checkable=True)
        self._act("reduced_motion", lambda c: self._set_a11y("reduced_motion", c), checkable=True)
        self._act("about", self.show_about)
        self._act("updates", self.check_updates)
        for i, mid in enumerate(("downloader", "recorder", "editor", "photo", "series"), 1):
            self._act(f"module_{mid}", lambda _c=False, m=mid: self.open_module(m), f"Ctrl+{i}")
        self.workspace_actions: Dict[str, QAction] = {}

    def _wire(self) -> None:
        ctx, tb = self.ctx, self.topbar
        tb.modeSelected.connect(ctx.set_ui_mode)
        tb.undoRequested.connect(self.undo)
        tb.rewindRequested.connect(self.request_rewind)
        tb.settingsRequested.connect(lambda: self.open_module("settings"))
        self._conns = [
            (ctx.modeChanged, ctx.modeChanged.connect(lambda m: self._apply_mode(UiMode.coerce(m), animate=True))),
            (ctx.languageChanged, ctx.languageChanged.connect(lambda _l: self.retranslate())),
            (ctx.undo.canUndoChanged, ctx.undo.canUndoChanged.connect(lambda _c: self._sync_undo())),
            (ctx.undo.undoTextChanged, ctx.undo.undoTextChanged.connect(lambda _t: self._sync_undo())),
        ]
        self.home.moduleRequested.connect(self.open_module)
        self.home.openProjectRequested.connect(self.open_recent)
        self.home.newProjectRequested.connect(self.new_project)
        self.home.openFolderRequested.connect(self.open_folder)
        self.home.filesDropped.connect(self.on_files_dropped)
        self.home.helpRequested.connect(lambda: self.open_module("downloader"))
        self.legend.openProjectRequested.connect(self.open_recent)
        self.legend.newProjectRequested.connect(self.new_project)
        self.legend.openFolderRequested.connect(self.open_folder)
        self.legend.workspaceChanged.connect(self._on_workspace_changed)
        self.quick.moduleRequested.connect(self.open_module)
        sub = ctx.bus.subscribe
        self._unsubs += [
            sub(Topics.PROJECT_OPENED, lambda _p: self._sync_project()),
            sub(Topics.PROJECT_CLOSED, lambda _p: self._sync_project()),
            sub(Topics.PROJECT_RESTORED, lambda _d: self._sync_project()),
            sub(Topics.PROJECT_DIRTY, lambda _d: self._sync_project(refresh=False)),
            sub(Topics.PROJECT_SAVED, lambda _p: self.status.set_message(i18n.t("status.saved"))),
            sub(Topics.NOTIFY, self._on_notify),
            sub(Topics.ERROR, self.show_error),
            sub(Topics.AUTOSAVE_STATE, self._on_autosave),
            sub(Topics.NAVIGATE, lambda d: self.open_module(str((d or {}).get("module", "")))),
            sub(Topics.JOB_ADDED, lambda _j: self._sync_jobs()),
            sub(Topics.JOB_UPDATED, lambda _j: self._sync_jobs()),
            sub(Topics.SYSMON, self.status.set_system),
            sub(Topics.VAULT_RESET, lambda _d=None: self.reset_vault()),
        ]

    # ------------------------------------------------------------------ çeviri
    def retranslate(self) -> None:
        t = i18n.t
        labels = {"new": ("menu.new", ""), "open": ("menu.open", ""), "save": ("menu.save", ""),
                  "close": ("menu.close_project", ""), "quit": ("menu.quit", ""), "undo": ("top.undo", ""),
                  "redo": ("menu.redo", ""), "undo_rewind": ("menu.undo_rewind", ""), "toggle_mode": ("menu.toggle_mode", ""),
                  "reset_layout": ("menu.reset_layout", ""), "large_text": ("a11y.large_text", ""),
                  "high_contrast": ("a11y.high_contrast", ""), "reduced_motion": ("a11y.reduced_motion", ""),
                  "about": ("menu.about", ""), "updates": ("menu.updates", "")}
        for key, (tkey, _x) in labels.items():
            self.actions_by_key[key].setText(t(tkey))
        for mid in ("downloader", "recorder", "editor", "photo", "series"):
            e = self._entry(mid)
            self.actions_by_key[f"module_{mid}"].setText(i18n.term(e.title_key) if e else mid)
        self.actions_by_key["large_text"].setChecked(self.ctx.settings.large_text)
        self.actions_by_key["high_contrast"].setChecked(self.ctx.settings.high_contrast)
        self.actions_by_key["reduced_motion"].setChecked(bool(self.ctx.settings.get("a11y.reduced_motion", False)))
        self._rebuild_menus()
        self.dock_quick.setWindowTitle(i18n.upper(t("dock.quick")))
        self.dock_jobs.setWindowTitle(i18n.upper(t("dock.jobs")))
        self.dock_system.setWindowTitle(i18n.upper(t("dock.system")))
        self.topbar.retranslate()
        self.topbar.mode_switch._refresh_a11y()
        self.topbar.mode_switch._resize()
        self.topbar.mode_switch.update()
        self.home.refresh()
        self.quick.refresh()
        self.legend.rebuild()
        self._sync_project(refresh=False)
        self._sync_undo()
        self._reopen_current_module()

    def _rebuild_menus(self) -> None:
        mb, a, t = self.menu_bar, self.actions_by_key, i18n.t
        mb.clear()
        m = mb.addMenu(t("menu.file"))
        for k in ("new", "open", "save", "close"):
            m.addAction(a[k])
        self.recent_menu = m.addMenu(t("menu.recent"))
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addSeparator()
        m.addAction(a["quit"])
        m = mb.addMenu(t("menu.edit"))
        m.addAction(a["undo"])
        m.addAction(a["redo"])
        m.addAction(a["undo_rewind"])
        m = mb.addMenu(t("menu.view"))
        ws_menu = m.addMenu(t("menu.workspaces"))
        self.workspace_actions.clear()
        for ws in ("project", "edit", "studio", "capture", "system"):
            act = QAction(t(f"workspace.{ws}"), self)
            act.triggered.connect(lambda _c=False, w=ws: self.show_workspace(w))
            ws_menu.addAction(act)
            self.workspace_actions[ws] = act
        m.addAction(a["toggle_mode"])
        m.addAction(a["reset_layout"])
        m.addSeparator()
        for k in ("large_text", "high_contrast", "reduced_motion"):
            m.addAction(a[k])
        m = mb.addMenu(t("menu.window"))
        for dock in self.docks:
            m.addAction(dock.toggleViewAction())
        m = mb.addMenu(t("menu.help"))
        m.addAction(a["updates"])
        m.addAction(a["about"])
        self.project_menu_refresh()

    def project_menu_refresh(self) -> None:
        menu = self.topbar.project_menu
        menu.clear()
        a = self.actions_by_key
        menu.addAction(a["new"])
        menu.addAction(a["open"])
        menu.addAction(a["save"])
        menu.addAction(a["close"])
        if device.is_mobile():                      # dar çubukta (telefon, dikey tablet) "10 Dk Önceye Döndür" burada
            menu.addSeparator()
            menu.addAction(self.topbar.rewind_action)
        menu.addSeparator()
        sub = menu.addMenu(i18n.t("menu.recent"))
        sub.aboutToShow.connect(lambda s=sub: self._fill_recent_menu(s))

    def _fill_recent_menu(self, menu: Optional[QMenu] = None) -> None:
        menu = menu or self.recent_menu
        menu.clear()
        items = self.ctx.recent.get_recent()
        if not items:
            empty = menu.addAction(i18n.t("menu.recent_empty"))
            empty.setEnabled(False)
        for e in items:
            act = menu.addAction(e.name)
            act.triggered.connect(lambda _c=False, p=e.path: self.open_recent(p))

    # ------------------------------------------------------------------ mod
    def toggle_mode(self) -> None:
        if not device.legend_allowed():            # telefonda yalnız Basit Mod (M1)
            return
        self.ctx.set_ui_mode(UiMode.LEGEND if self.ctx.ui_mode is UiMode.SIMPLE else UiMode.SIMPLE)

    def _apply_mode(self, mode: UiMode, animate: bool) -> None:
        legend = mode is UiMode.LEGEND
        if not legend:
            self._save_dock_state()
        self.theme.apply(mode, animate=animate)
        self.topbar.mode_switch.set_mode(mode, animate=animate)
        self.body.setCurrentIndex(1 if legend else 0)
        self.menu_bar.setVisible(legend)
        for d in self.docks:
            d.setVisible(legend)
        self.status.set_compact(not legend)
        self.topbar.setProperty("legend", legend)
        if legend:
            if self._default_dock_state is None:
                self._default_dock_state = self.saveState()
            self._restore_dock_state(self.legend.current)
            self.system_panel.start()
        else:
            self.system_panel.stop()
        self.topbar.mode_switch._resize()
        self._reopen_current_module()
        self.topbar.retranslate()
        self._sync_portrait_docks()
        self.status.set_message(i18n.t("status.mode_legend" if legend else "status.mode_simple"), 2500)

    # ------------------------------------------------------------------ gezinti
    def _entry(self, module_id: str) -> Optional[ModuleEntry]:
        return next((e for e in self.ctx.registry.entries() if e.id == module_id), None)

    def open_module(self, module_id: str) -> None:
        entry = self._entry(module_id)
        if entry is None:
            self.status.set_message(i18n.t("status.unknown_module", id=module_id))
            return
        self.ctx.current_module = module_id
        if self.ctx.ui_mode is UiMode.LEGEND:
            self.legend.show_module(entry)
        else:
            self._show_simple_module(entry)

    def _show_simple_module(self, entry: ModuleEntry) -> None:
        self._clear_frame()
        page = build_module_page(self.ctx, entry, UiMode.SIMPLE)
        self._frame = PageFrame(i18n.term(entry.title_key), page)
        self._frame.backRequested.connect(self.go_home)
        self.simple.addWidget(self._frame)
        self.simple.setCurrentWidget(self._frame)
        adaptive.touch_scroll_all(self._frame)            # telefon/tablet: sayfa ve listeler parmakla kayar

    def _clear_frame(self) -> None:
        if self._frame is not None:
            self.simple.removeWidget(self._frame)
            self._frame.deleteLater()
            self._frame = None

    def handle_back(self) -> bool:
        """Geri tuşu: açık sayfa varsa ana ekrana (``True``). Ana ekranda ilk basış ipucu gösterir (``True``),
        süre içinde ikinci basış ``False`` döner: tuş karşılanmaz, Qt pencereyi kapatır."""
        if self.ctx.current_module is not None:
            self.go_home()
            self._back_at = 0.0
            return True
        now = time.monotonic()
        if self._back_at and now - self._back_at <= BACK_EXIT_WINDOW_S:
            self._back_at = 0.0
            return False
        self._back_at = now
        self.status.set_message(i18n.t("back.exit_hint"), int(BACK_EXIT_WINDOW_S * 1000))
        return True

    def go_home(self) -> None:
        self.ctx.current_module = None
        self._clear_frame()
        self.simple.setCurrentWidget(self.home)
        if self.ctx.ui_mode is UiMode.LEGEND:
            self.legend.show_workspace("project")

    def _reopen_current_module(self) -> None:
        """Mod/dil değişince açık modülün sayfasını yeni modun fabrikasıyla yeniden kurar (durum servistedir)."""
        mid = self.ctx.current_module
        entry = self._entry(mid) if mid else None
        if entry is None:
            self._clear_frame()
            self.simple.setCurrentWidget(self.home)
            return
        if self.ctx.ui_mode is UiMode.LEGEND:
            self.legend.show_module(entry)
        else:
            self._show_simple_module(entry)

    def show_workspace(self, ws: str) -> None:
        if not device.legend_allowed():
            return
        if self.ctx.ui_mode is not UiMode.LEGEND:
            self.ctx.set_ui_mode(UiMode.LEGEND)
        self.legend.show_workspace(ws)

    def _on_workspace_changed(self, ws: str) -> None:
        self._save_dock_state()
        self._restore_dock_state(ws)
        self.ctx.settings.set("workspace", ws)

    # ------------------------------------------------------------------ dock durumu
    def _save_dock_state(self, ws: Optional[str] = None) -> None:
        if not self.docks[0].isVisible() and self.ctx.ui_mode is not UiMode.LEGEND:
            return
        key = f"window.legend_state.{ws or self.legend.current}"
        self.ctx.settings.set(key, _b64(self.saveState()))

    def _restore_dock_state(self, ws: str) -> None:
        raw = self.ctx.settings.get(f"window.legend_state.{ws}")
        if raw:
            self.restoreState(_from_b64(str(raw)))
        self._portrait_docks = []
        for d in self.docks:
            d.setVisible(self.ctx.ui_mode is UiMode.LEGEND)
        self._sync_portrait_docks()

    def reset_layout(self) -> None:
        if self._default_dock_state is not None:
            self.restoreState(self._default_dock_state)
        self._portrait_docks = []
        for d in self.docks:
            d.setFloating(False)
            d.setVisible(self.ctx.ui_mode is UiMode.LEGEND)
        self._sync_portrait_docks()

    def _sync_portrait_docks(self) -> None:
        """Tablet dikey tutuşta Legend panelleri gizlenir, orta alan ekranın tamamını alır; yatayda gizlenenler geri gelir
        (kullanıcının kapattığı panel kapalı kalır). Masaüstünde ve Basit Mod'da hiçbir şey yapmaz."""
        if not device.is_mobile():
            return
        legend = self.ctx.ui_mode is UiMode.LEGEND
        if legend and self.width() < self.height():
            for d in self.docks:
                if d.isVisible() and not d.isFloating():
                    self._portrait_docks.append(d)
                    d.hide()
        elif self._portrait_docks:
            hidden, self._portrait_docks = self._portrait_docks, []
            for d in hidden:
                d.setVisible(legend)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_portrait_docks()

    def _restore_geometry(self) -> None:
        raw = self.ctx.settings.get("window.geometry")
        if raw:
            try:
                self.restoreGeometry(_from_b64(str(raw)))
            except Exception:
                pass

    # ------------------------------------------------------------------ proje
    def _sync_project(self, refresh: bool = True) -> None:
        p = self.ctx.project
        name = p.name + (" •" if p.dirty else "") if p.is_open else ""
        self.topbar.set_project_name(name)
        if refresh:
            self.home.refresh()
            self.legend.hub.refresh()

    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, i18n.t("project.new_title"), i18n.t("project.new_label"),
                                        text=i18n.t("project.default_name"))
        if ok and name.strip():
            root = self.ctx.create_project(name.strip())
            if root:
                self.status.set_message(i18n.t("status.project_created", name=self.ctx.project.name))

    def open_folder(self) -> None:
        start = str(self.ctx.settings.get("project_root") or Path.home())
        if device.is_android():                    # klasör seçici content:// döner; projeler uygulamanın alanında (M20)
            self._pick_project_android(Path(start))
            return
        chosen = QFileDialog.getExistingDirectory(self, i18n.t("project.open_title"), start)
        if chosen:
            self.open_recent(chosen)

    def project_folders(self, root: Path) -> List[Path]:
        """``root`` altındaki proje klasörleri (içinde ``proje.kadraj`` olan), en son değişen önce."""
        try:
            found = [f.parent for f in root.glob(f"*/{PROJECT_FILE_NAME}") if f.is_file()]
        except OSError:
            return []
        return sorted(found, key=lambda d: (d / PROJECT_FILE_NAME).stat().st_mtime, reverse=True)

    def _pick_project_android(self, root: Path) -> None:
        folders = self.project_folders(root)
        if not folders:
            self.status.set_message(i18n.t("project.none_found"), 6000)
            return
        names = [d.name for d in folders]
        name, ok = QInputDialog.getItem(self, i18n.t("project.open_title"), i18n.t("project.pick_label"), names, 0, False)
        if ok and name in names:
            self.open_recent(str(folders[names.index(name)]))

    def open_recent(self, path: str) -> None:
        if self.ctx.open_project(path):
            self.status.set_message(i18n.t("status.project_opened", name=self.ctx.project.name))

    def save_project(self) -> None:
        if not self.ctx.project.is_open:
            self.status.set_message(i18n.t("status.no_project"))
            return
        try:
            self.ctx.project.save()
        except Exception as exc:
            self.ctx.report_error(exc, {"source": "project"})

    def close_project(self) -> None:
        self.ctx.project.close()

    def on_files_dropped(self, paths: list) -> None:
        self.status.set_message(i18n.t("status.files_received", n=len(paths)), 6000)

    # ------------------------------------------------------------------ geri al / döndür
    def undo(self) -> None:
        if self.ctx.undo.canUndo():
            self.ctx.undo.undo()

    def redo(self) -> None:
        if self.ctx.undo.canRedo():
            self.ctx.undo.redo()

    def _sync_undo(self) -> None:
        try:
            self.topbar.set_undo_enabled(self.ctx.undo.canUndo(), self.ctx.undo.undoText())
        except RuntimeError:                      # bağlam yok edilirken gelen son sinyaller
            pass

    def request_rewind(self, minutes: int = 10) -> None:
        """"10 Dk Önceye Döndür": olayı yayınlar; ``ctx.autosave`` halkadan geri yükler ve sonucu ``Topics.NOTIFY`` ile bildirir."""
        self.status.set_message(i18n.t("status.rewind_requested", minutes=minutes), 6000)
        self.ctx.bus.emit(Topics.REWIND_REQUESTED, {"minutes": minutes})
        self.rewindRequested.emit(minutes)

    def undo_rewind(self) -> None:
        """Son geri dönüşü geri alır (dönmeden önce alınan yedeği geri yükler)."""
        res = self.ctx.autosave.undo_rewind()
        key = {"restored": "rewind.undone", "no_history": "rewind.nothing_to_undo",
               "no_project": "rewind.no_project"}.get(res.reason, "rewind.error")
        self.status.set_message(i18n.t(key), 6000)

    # ------------------------------------------------------------------ olaylar
    def _on_notify(self, data) -> None:
        text = (data or {}).get("text", "") if isinstance(data, dict) else str(data or "")
        self.status.set_message(text, 6000)

    def _on_autosave(self, data) -> None:
        state = (data or {}).get("state", "none")
        at = (data or {}).get("at")
        self.topbar.autosave.set_state(state, at)
        self.status.autosave.set_state(state, at)

    def _sync_jobs(self) -> None:
        jobs = self.ctx.jobs.list()
        self.status.set_jobs(len([j for j in jobs if not j.finished]), len(jobs))
        # Android: indirme/dışa aktarma sürerken ekran kararmasın (uygulama arka plana düşüp durmasın; M20)
        bridge.screen.set("jobs", any(j.status in (JobStatus.QUEUED, JobStatus.RUNNING) for j in jobs))

    def show_error(self, err: SmartError) -> None:
        if not isinstance(err, SmartError):
            return
        dlg = ErrorDialog(err, self)
        dlg.card.actionTriggered.connect(lambda action, d=dlg: self._on_error_action(action, d))
        self._error_dialogs.append(dlg)
        dlg.finished.connect(lambda _r, d=dlg: self._error_dialogs.remove(d) if d in self._error_dialogs else None)
        dlg.show()

    def _on_error_action(self, action: SmartAction, dialog: ErrorDialog) -> None:
        """Hata penceresindeki düğmeler (kopyalama kartın içinde): ayarları aç, sayfayı aç, konu yayınla."""
        kind = action.kind
        if kind == "open_settings":
            dialog.close()
            self.open_module("settings")
        elif kind == "open_url":
            url = str(action.payload.get("url", ""))
            if url.startswith(("https://", "http://")):
                QDesktopServices.openUrl(QUrl(url))
        elif kind == "custom":
            topic = str(action.payload.get("topic", ""))
            if topic:
                dialog.close()
                self.ctx.bus.emit(topic, dict(action.payload))

    def reset_vault(self) -> None:
        """Kasa hata kartındaki "Kasayı sıfırla": Ayarlar'daki düğmeyle aynı onaylı akış (eski dosya yedeklenir)."""
        service = self.ctx.service("settings")
        if service is None:
            return
        from ...modules.settings import vault_ui
        vault_ui.reset_vault(self, service, self.status.set_message,
                             lambda exc: self.ctx.report_error(exc, {"source": "vault"}))

    def _set_a11y(self, key: str, value: bool) -> None:
        self.ctx.settings.set(f"a11y.{key}", bool(value))
        self.theme.apply(large_text=self.ctx.settings.large_text, high_contrast=self.ctx.settings.high_contrast,
                         reduced_motion=bool(self.ctx.settings.get("a11y.reduced_motion", False)), animate=False)
        self.ctx.bus.emit(Topics.A11Y_CHANGED, {"large_text": self.ctx.settings.large_text,
                                                "high_contrast": self.ctx.settings.high_contrast,
                                                "reduced_motion": bool(self.ctx.settings.get("a11y.reduced_motion", False))})
        self.topbar.mode_switch._resize()
        self.topbar._on_theme()
        if not device.is_mobile():
            self.setMinimumWidth(self.theme.tokens.px(920))

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def check_updates(self) -> None:
        """Yalnızca kullanıcı isteğiyle; ağ yoksa sessizce "denetlenemedi" der (çökme yok)."""
        self.status.set_message(i18n.t("status.updates_checking"), 0)

        def work() -> None:
            try:
                rel = updates.check_for_update()
                text = i18n.t("status.updates_new", version=rel.version) if rel else i18n.t("status.updates_none")
            except Exception:
                text = i18n.t("status.updates_failed")
            self.ctx.bus.emit(Topics.NOTIFY, {"text": text, "level": "info"})

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ kapanış
    def closeEvent(self, event) -> None:
        proj = self.ctx.project
        if proj.is_open and proj.dirty:
            box = QMessageBox(self)
            box.setWindowTitle(i18n.t("project.unsaved_title"))
            box.setText(i18n.t("project.unsaved_body", name=proj.name))
            save = box.addButton(i18n.t("project.save"), QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton(i18n.t("project.discard"), QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(i18n.t("common.cancel"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is save:
                proj.save()
            elif clicked is not discard:
                event.ignore()
                return
        self.system_panel.stop()
        if self.ctx.ui_mode is UiMode.LEGEND:
            self._save_dock_state()
        self.ctx.settings.set("window.geometry", _b64(self.saveGeometry()))
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for signal, conn in self._conns:         # bağlam pencereden uzun yaşayabilir; bağları çöz
            try:
                signal.disconnect(conn)
            except (RuntimeError, TypeError):
                pass
        self._conns.clear()
        self.theme.finish_transitions()
        super().closeEvent(event)
