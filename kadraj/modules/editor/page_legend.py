"""Legend "Kurgu" sayfası (tuval: V2Editor · Legend).

Gömülü bir ``QMainWindow``: menü çubuğu (Dosya/Düzen/Klip/Dizi/İşaretçi/Pencere), üstte çalışma alanı sekmeleri
(DÜZENLE · RENK · SES · DIŞA AKTAR) + SNAP + zaman kodu, ortada PROGRAM monitörü (önce/sonra, güvenli alan), kenarlarda yerleştirilebilir
paneller (Medya/Otomasyonlar, Denetçi/İşaretçiler, Ölçüm, 10 bant EQ + Mikser, Dışa aktar) ve altta çok kanallı zaman çizelgesi.
Her çalışma alanının yerleşimi ayrı ``saveState`` ile saklanır (ayarlarda ``editor.workspaces``).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from ...core import i18n
from ...ui.theme.manager import ThemedMixin
from ...ui.widgets.dock_panel import DockPanel
from ...ui.widgets.squishy_button import SquishyButton
from .page_base import EditorPageBase
from .panels import (MAGIC_ORDER, ExportPanel, InspectorPanel, LibraryPanel, MagicGrid, MarkerPanel, MixerPanel, _label)
from .timeline import format_timecode
from .widgets import PreviewView, ScopeView, TimelineView

WORKSPACES = ("edit", "color", "audio", "export")


def _scroll(w: QWidget) -> QScrollArea:
    a = QScrollArea()
    a.setWidgetResizable(True)
    a.setFrameShape(QFrame.Shape.NoFrame)
    a.setWidget(w)
    a.setStyleSheet("QScrollArea { background: transparent; border: none; } "
                    "QScrollArea > QWidget > QWidget { background: transparent; }")        # koyu temada beyaz zemin kalmasın
    return a


class _WsButton(ThemedMixin, QPushButton):
    """Çalışma alanı sekmesi: seçiliyken birincil tonlu, küçük, keskin köşeli (Legend)."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bind_theme()
        self._on_theme()

    def _on_theme(self) -> None:
        tk = self.tk
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {tk.text2}; border: 1px solid {tk.border}; border-radius: {tk.radius_ctl}px;"
            f" padding: 4px 14px; min-height: {tk.px(20)}px; font-weight: 700; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ color: {tk.text}; }}"
            f"QPushButton:checked {{ background: {tk.primary_tone}; color: {tk.primary}; border-color: {tk.primary}; }}")


class EditorLegendPage(EditorPageBase):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(ctx, parent)
        self.setObjectName("editor_legend")
        self.workspace = ""
        self._states: Dict[str, str] = dict(ctx.settings.get("editor.workspaces", {}) or {})
        self.scope_visible = True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_topbar())
        outer.addLayout(self.error_slot)
        outer.addWidget(self.proposal_bar)
        self.win = QMainWindow()
        self.win.setWindowFlags(Qt.WindowType.Widget)
        self.win.setDockNestingEnabled(True)
        outer.addWidget(self.win, 1)
        self.footer = _label("", "mono")
        outer.addWidget(self.footer)
        self._build_central()
        self._build_docks()
        self._build_menus()
        self.connect_service()
        svc = self.svc
        svc.timelineChanged.connect(lambda _r: self._sync())
        svc.selectionChanged.connect(self._sync)
        svc.playheadChanged.connect(lambda _t: self._sync_time())
        self.timeline.seekRequested.connect(self._seek)
        self.timeline.selectionRequested.connect(svc.select)
        self.set_workspace("edit")
        self._sync()

    # -- yapı ------------------------------------------------------------------------------------------------------------------
    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("role", "topbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(6)
        self.ws_buttons: Dict[str, QPushButton] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for ws in WORKSPACES:
            b = _WsButton(i18n.t(f"editor.legend.ws.{ws}"))
            b.clicked.connect(lambda _c=False, w=ws: self.set_workspace(w))
            self.ws_buttons[ws] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.snap_btn = QPushButton("")
        self.snap_btn.setCheckable(True)
        self.snap_btn.setChecked(self.svc.snap_enabled)
        self.snap_btn.toggled.connect(self._snap)
        self.timecode = QLabel("00:00:00:00")
        self.timecode.setProperty("role", "mono")
        self.timecode.setStyleSheet("font-size: 18px; font-weight: 700;")
        lay.addWidget(self.snap_btn)
        lay.addWidget(self.timecode)
        self._snap(self.svc.snap_enabled)
        return bar

    def _build_central(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 4)
        head = QHBoxLayout()
        self.res_label = _label("", "mono")
        self.compare_btn = QPushButton(i18n.t("editor.legend.compare"))
        self.compare_btn.setCheckable(True)
        self.safe_btn = QPushButton(i18n.t("editor.legend.safe"))
        self.safe_btn.setCheckable(True)
        head.addWidget(self.res_label)
        head.addStretch(1)
        head.addWidget(self.compare_btn)
        head.addWidget(self.safe_btn)
        lay.addLayout(head)
        self.preview = PreviewView(pro=True)
        lay.addWidget(self.preview, 1)
        tr = QHBoxLayout()
        self.prev_btn, self.next_btn = QPushButton("◀|"), QPushButton("|▶")
        self.play_btn = SquishyButton(i18n.t("editor.simple.play"), variant="primary", icon="check")
        self.split_btn = QPushButton(i18n.t("editor.legend.act.split"))
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            tr.addWidget(b)
        tr.addStretch(1)
        tr.addWidget(self.split_btn)
        lay.addLayout(tr)
        self.win.setCentralWidget(w)
        self.compare_btn.toggled.connect(self.set_compare)
        self.safe_btn.toggled.connect(lambda on: (setattr(self.preview, "safe_area", on), self.preview.update()))
        self.prev_btn.clicked.connect(lambda: self.svc.step_frames(-1))
        self.next_btn.clicked.connect(lambda: self.svc.step_frames(1))
        self.play_btn.clicked.connect(self.toggle_play)
        self.split_btn.clicked.connect(self.svc.split_at_playhead)

    def _build_docks(self) -> None:
        svc = self.svc
        self.library = LibraryPanel(svc)
        self.magic = MagicGrid(svc, MAGIC_ORDER, columns=1)
        self.inspector = InspectorPanel(svc)
        self.markers = MarkerPanel(svc)
        self.mixer = MixerPanel(svc)
        self.export = ExportPanel(svc, pro=True)
        scopes_w = QWidget()
        sl = QVBoxLayout(scopes_w)
        sl.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.scope_buttons: Dict[str, QPushButton] = {}
        for m in ScopeView.MODES:
            b = QPushButton(i18n.t(f"editor.legend.scope.{m}"))
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setChecked(m == "hist")
            b.clicked.connect(lambda _c=False, mode=m: self.scopes.set_mode(mode))
            self.scope_buttons[m] = b
            row.addWidget(b)
        row.addStretch(1)
        sl.addLayout(row)
        self.scopes = ScopeView()
        sl.addWidget(self.scopes, 1)
        self.scope_stats = _label("", "mono")
        sl.addWidget(self.scope_stats)
        tl_w = QWidget()
        tl = QVBoxLayout(tl_w)
        tl.setContentsMargins(0, 0, 0, 0)
        tool = QHBoxLayout()
        self.add_track_btn = QToolButton()
        self.add_track_btn.setText("+ KANAL")
        self.add_track_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_track_btn)
        for kind, key in (("video", "add_video"), ("audio", "add_audio"), ("adj", "add_adj"), ("sub", "add_sub")):
            menu.addAction(i18n.t(f"editor.legend.act.{key}"), lambda k=kind: self.svc.add_track(k))
        self.add_track_btn.setMenu(menu)
        self.adj_btn = QPushButton(i18n.t("editor.legend.act.add_adj_clip"))
        self.zoom_out_btn, self.zoom_in_btn = QPushButton("−"), QPushButton("+")
        self.delta_label = _label("", "mono")
        for w in (self.add_track_btn, self.adj_btn, self.zoom_out_btn, self.zoom_in_btn):
            tool.addWidget(w)
        tool.addStretch(1)
        tool.addWidget(self.delta_label)
        tl.addLayout(tool)
        self.timeline = TimelineView(svc, pro=True)
        self.timeline_scroll = _scroll(self.timeline)
        tl.addWidget(self.timeline_scroll, 1)
        self.adj_btn.clicked.connect(lambda: svc.add_adjust_layer())
        self.zoom_in_btn.clicked.connect(lambda: self.timeline.zoom_by(1.3))
        self.zoom_out_btn.clicked.connect(lambda: self.timeline.zoom_by(1 / 1.3))
        T = lambda k: i18n.t(f"editor.legend.panel.{k}")
        self.docks: Dict[str, DockPanel] = {
            "library": DockPanel("dock_library", T("library"), self.library),
            "automations": DockPanel("dock_automations", T("automations"), _scroll(self.magic)),
            "inspector": DockPanel("dock_inspector", T("inspector"), _scroll(self.inspector)),
            "markers": DockPanel("dock_markers", T("markers"), self.markers),
            "scopes": DockPanel("dock_scopes", T("scopes"), scopes_w),
            "audio": DockPanel("dock_audio", T("audio"), self.mixer),
            "export": DockPanel("dock_export", T("export"), _scroll(self.export)),
            "timeline": DockPanel("dock_timeline", T("timeline"), tl_w),
        }
        for name, d in self.docks.items():
            self.win.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d)
            d.hide()

    def _build_menus(self) -> None:
        mb = self.win.menuBar()
        svc, T = self.svc, lambda k: i18n.t(f"editor.legend.{k}")
        self.menu_actions: Dict[str, QAction] = {}

        def act(menu: QMenu, key: str, fn) -> QAction:
            a = menu.addAction(T(f"act.{key}"))
            a.triggered.connect(lambda _c=False: fn())
            self.menu_actions[key] = a
            return a
        m = mb.addMenu(T("menu.file"))
        act(m, "import", lambda: self.library._import())
        act(m, "export", lambda: self.set_workspace("export"))
        act(m, "preview", svc.render_preview)
        m = mb.addMenu(T("menu.edit"))
        act(m, "undo", svc.undo)
        act(m, "redo", svc.redo)
        m = mb.addMenu(T("menu.clip"))
        act(m, "split", svc.split_at_playhead)
        act(m, "delete", lambda: svc.delete_selection())
        act(m, "ripple", lambda: svc.delete_selection(True))
        act(m, "add_adj_clip", lambda: svc.add_adjust_layer())
        m = mb.addMenu(T("menu.sequence"))
        for kind, key in (("video", "add_video"), ("audio", "add_audio"), ("adj", "add_adj"), ("sub", "add_sub")):
            act(m, key, lambda k=kind: svc.add_track(k))
        snap = act(m, "snap", lambda: self.snap_btn.toggle())
        snap.setCheckable(True)
        snap.setChecked(svc.snap_enabled)
        m = mb.addMenu(T("menu.marker"))
        act(m, "marker", lambda: svc.add_marker())
        act(m, "chapter", lambda: svc.add_marker(None, "", True))
        self.window_menu = mb.addMenu(T("menu.window"))
        for ws in WORKSPACES:
            self.window_menu.addAction(i18n.t(f"editor.legend.ws.{ws}"), lambda w=ws: self.set_workspace(w))

    # -- çalışma alanları ------------------------------------------------------------------------------------------------------
    LAYOUTS = {
        "edit": {"left": ["library", "automations"], "right": ["inspector", "markers"], "bottom": ["timeline"]},
        "color": {"left": ["scopes"], "right": ["inspector"], "bottom": ["timeline"]},
        "audio": {"left": ["library"], "right": ["audio"], "bottom": ["timeline"]},
        "export": {"left": ["markers"], "right": ["export"], "bottom": ["timeline"]},
    }
    AREAS = {"left": Qt.DockWidgetArea.LeftDockWidgetArea, "right": Qt.DockWidgetArea.RightDockWidgetArea,
             "bottom": Qt.DockWidgetArea.BottomDockWidgetArea}

    def _save_state(self) -> None:
        if self.workspace:
            self._states[self.workspace] = bytes(self.win.saveState().toBase64()).decode("ascii")

    def workspace_state(self, ws: str) -> str:
        return self._states.get(ws, "")

    def set_workspace(self, ws: str) -> None:
        if ws not in WORKSPACES:
            return
        if ws == self.workspace:
            self.ws_buttons[ws].setChecked(True)
            return
        self._save_state()
        self.workspace = ws
        self.ws_buttons[ws].setChecked(True)
        saved = self._states.get(ws)
        self._arrange(ws)
        if saved:
            self.win.restoreState(QByteArray.fromBase64(saved.encode("ascii")))
        self.scope_visible = self.docks["scopes"].isVisible()
        self.ctx.settings.set("editor.workspaces", dict(self._states))
        self.schedule_frame()

    def _arrange(self, ws: str) -> None:
        layout = self.LAYOUTS[ws]
        shown = {n for names in layout.values() for n in names}
        for name, d in self.docks.items():
            self.win.removeDockWidget(d)
            d.setFloating(False)
            d.hide()
        for area, names in layout.items():
            first = None
            for n in names:
                d = self.docks[n]
                self.win.addDockWidget(self.AREAS[area], d)
                d.show()
                if first is not None:
                    self.win.tabifyDockWidget(first, d)
                else:
                    first = d
            if first is not None:
                first.raise_()
        total_w = max(900, self.width())
        side = int(total_w * (0.38 if ws in ("export", "audio") else 0.24))
        try:
            self.win.resizeDocks([self.docks[layout["left"][0]], self.docks[layout["right"][0]]], [side if ws != "export" else int(total_w * .2), side],
                                 Qt.Orientation.Horizontal)
            self.win.resizeDocks([self.docks["timeline"]], [max(220, int(self.height() * 0.38))], Qt.Orientation.Vertical)
        except Exception:
            pass
        assert shown                      # (yerleşim her zaman en az bir panel içerir)

    def stop_workspace_state(self) -> None:
        self._save_state()
        self.ctx.settings.set("editor.workspaces", dict(self._states))

    def hideEvent(self, e) -> None:
        self.stop_workspace_state()
        super().hideEvent(e)

    # -- davranış ----------------------------------------------------------------------------------------------------------------
    def _snap(self, on: bool) -> None:
        self.svc.set_snap(on)
        self.snap_btn.setText(i18n.t("editor.legend.snap_on" if on else "editor.legend.snap_off"))
        if hasattr(self, "menu_actions") and "snap" in self.menu_actions:
            self.menu_actions["snap"].setChecked(on)

    def _seek(self, t: float) -> None:
        self.svc.set_playhead(t, snap=True, px_per_s=self.timeline.px_per_s)
        self.player.seek(self.svc.playhead)

    def _sync(self) -> None:
        tl = self.svc.tl
        self.res_label.setText(i18n.t("editor.legend.res", w=tl.width, h=tl.height, fps=f"{tl.fps:g}"))
        self.footer.setText(i18n.t("editor.legend.footer", tracks=len(tl.tracks), fps=f"{tl.fps:g}", duration=format_timecode(tl.duration, tl.fps)))
        self.delta_label.setText(f"Δ {format_timecode(tl.duration, tl.fps)}")
        self._sync_time()

    def _sync_time(self) -> None:
        self.timecode.setText(self.svc.timecode())
        self.timeline.update()

    def on_frame(self, img) -> None:
        if self.docks["scopes"].isVisible():
            self.scopes.set_frame(img)
            st = self.scopes.stats
            if st:
                self.scope_stats.setText(f"Y {st['luma_mean']:.2f}  P5 {st['luma_p5']:.2f}  P95 {st['luma_p95']:.2f}  "
                                         f"CLIP {st['clipped_high'] * 100:.1f}%  SAT {st['saturation']:.2f}")

    def on_player_state(self, state: str) -> None:
        self.play_btn.setText(i18n.t("editor.simple.pause" if state == "playing" else "editor.simple.play"))
