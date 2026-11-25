"""Legend "Dizi/Film": **AI akış şeması** (düğüm editörü) + denetçi (parametreler, seed kilidi + RND, cfg/steps/temperature/ref_strength, ham
Prompt/Negatif/JSON çekmecesi, "Yalnızca Bu Düğümü Çalıştır") + alt sekmeler: Storyboard (kanban), Batch matrisi, Çalıştırma geçmişi, Maliyet/token analitiği.
Üst çubuk: aşamalar + KOTA (+%18 · ~$3.20 · 38→56%) + "+ Düğüm" + "▶ Akışı Çalıştır"."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from ...ai_engine.nodes.graph import SPECS
from ...ai_engine.pipeline.machine import Stage
from ...core import i18n
from ...ui.widgets.squishy_button import SquishyButton
from .charts import AnalyticsPanel
from .nodecanvas import NodeCanvas
from .page_base import SeriesPageBase
from .widgets import KanbanBoard, StageBar

CHOICES = {"model": ("flux", "imagen", "dalle", "sdxl"), "aspect": ("16:9", "9:16", "1:1", "4:3", "3:2"), "kind": ("tts", "sfx"),
           "tier": ("cheap", "default"), "factor": (2, 4)}
FLOAT_RANGE = {"cfg": (1.0, 20.0, 0.5), "ref_strength": (0.0, 1.0, 0.05), "temperature": (0.0, 2.0, 0.1), "seconds": (0.5, 30.0, 0.5)}


def _panel(title: str) -> tuple:
    f = QFrame()
    f.setProperty("role", "panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(10, 8, 10, 10)
    lay.setSpacing(6)
    cap = QLabel(title.upper())
    cap.setProperty("role", "caps")
    lay.addWidget(cap)
    return f, lay


class SeriesLegendPage(SeriesPageBase):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(ctx, parent)
        self.setObjectName("series_legend")
        self.selected: Optional[str] = None
        self._busy_ui = False
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self._topbar())
        root.addLayout(self.error_slot)
        split = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(split, 1)
        top = QSplitter(Qt.Orientation.Horizontal)
        pc, cl = _panel(i18n.t("series.legend.flow"))
        self.flow_info = QLabel("")
        self.flow_info.setProperty("role", "mono")
        self.canvas = NodeCanvas()
        cl.addWidget(self.flow_info)
        cl.addWidget(self.canvas, 1)
        top.addWidget(pc)
        top.addWidget(self._inspector())
        top.setSizes([760, 360])
        split.addWidget(top)
        split.addWidget(self._tabs())
        split.setSizes([480, 300])
        foot = QHBoxLayout()
        self.session_label = QLabel("")
        self.session_label.setProperty("role", "mono")
        foot.addWidget(self.session_label)
        foot.addStretch(1)
        foot.addWidget(self.busy_label)
        root.addLayout(foot)
        self.connect_base()
        s = self.svc
        s.graphChanged.connect(self.refresh_graph)
        s.changed.connect(self.refresh_project)
        s.stageChanged.connect(lambda _s: self.refresh_project())
        s.runFinished.connect(self._on_run)
        self.canvas.nodeSelected.connect(self.select_node)
        self.canvas.connectRequested.connect(s.connect_nodes)
        self.canvas.moveFinished.connect(s.move_node)
        self.canvas.deleteRequested.connect(s.remove_node)
        self.canvas.addRequested.connect(lambda t, x, y: s.add_node(t, (x, y)))
        self.refresh_graph()
        self.refresh_project()
        self.refresh_history()
        self.refresh_analytics()

    # -- üst çubuk -------------------------------------------------------------------------------------------------
    def _topbar(self) -> QWidget:
        bar = QFrame()
        bar.setProperty("role", "topbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 4)
        self.stage_bar = StageBar()
        self.stage_bar.stageRequested.connect(lambda s: self.svc.goto(Stage(s)))
        self.quota_label = QLabel("")
        self.quota_label.setProperty("role", "mono")
        self.add_btn = QToolButton()
        self.add_btn.setText(i18n.t("series.legend.add_node"))
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_btn)
        for t, spec in SPECS.items():
            menu.addAction(spec.title, lambda t=t: self.svc.add_node(t, (40.0 * len(self.svc.graph.nodes), 200.0)))
        self.add_btn.setMenu(menu)
        self.validate_btn = QPushButton(i18n.t("series.legend.validate_flow"))
        self.run_btn = SquishyButton(i18n.t("series.legend.run_all"), variant="primary", accent="pink")
        self.reset_btn = QPushButton(i18n.t("series.legend.reset"))
        lay.addWidget(self.stage_bar, 1)
        lay.addWidget(self.quota_label)
        for w in (self.add_btn, self.validate_btn, self.reset_btn, self.run_btn):
            lay.addWidget(w)
        self.validate_btn.clicked.connect(self._validate_flow)
        self.run_btn.clicked.connect(lambda: (self.clear_error(), self.svc.run_graph()))
        self.reset_btn.clicked.connect(self.svc.reset_graph)
        return bar

    # -- denetçi -------------------------------------------------------------------------------------------------------
    def _inspector(self) -> QWidget:
        p, lay = _panel(i18n.t("series.legend.inspector"))
        self.node_title = QLabel(i18n.t("series.legend.no_node"))
        self.node_title.setProperty("role", "h2")
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.form_host)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }")
        cap = QLabel(i18n.t("series.legend.raw").upper())
        cap.setProperty("role", "caps")
        self.raw_prompt, self.raw_negative, self.raw_json = QPlainTextEdit(), QPlainTextEdit(), QPlainTextEdit()
        for ed, key in ((self.raw_prompt, "prompt"), (self.raw_negative, "negative"), (self.raw_json, "json")):
            ed.setFixedHeight(48)
            ed.setPlaceholderText(i18n.t(f"series.legend.raw.{key}"))
            ed.textChanged.connect(lambda e=ed, k=key: self._raw_changed(k, e))
        self.raw_check = QLabel("")
        self.raw_check.setProperty("role", "hint")
        self.check_btn = QPushButton(i18n.t("series.legend.check"))
        self.only_btn = SquishyButton(i18n.t("series.legend.run_node"), variant="secondary")
        self.hint = QLabel("")
        self.hint.setProperty("role", "hint")
        self.hint.setWordWrap(True)
        for w in (self.node_title, scroll, cap, self.raw_prompt, self.raw_negative, self.raw_json, self.check_btn, self.raw_check, self.only_btn, self.hint):
            lay.addWidget(w)
        self.check_btn.clicked.connect(self._check_raw)
        self.only_btn.clicked.connect(lambda: self.selected and (self.clear_error(), self.svc.run_graph(only=self.selected)))
        return p

    def select_node(self, node_id: str) -> None:
        self.selected = node_id
        node = self.svc.graph.nodes.get(node_id)
        if node is None:
            return
        self._busy_ui = True
        self.node_title.setText(f"{node.title}  ·  {node.id}")
        while self.form.rowCount():
            self.form.removeRow(0)
        self.widgets: Dict[str, Any] = {}
        params = node.effective_params()
        for key, value in node.spec.params.items():
            cur = params.get(key, value)
            w: Any
            if key == "seed_locked":
                continue
            if key == "seed":
                w = QSpinBox()
                w.setRange(0, 2 ** 31 - 1)
                w.setValue(int(cur))
                lock = QCheckBox(i18n.t("series.legend.seed_lock"))
                lock.setChecked(bool(params.get("seed_locked")))
                lock.toggled.connect(lambda on, nid=node_id: self.svc.set_param(nid, "seed_locked", on))
                rnd = QPushButton("RND")
                rnd.clicked.connect(lambda _c=False, nid=node_id: self._rnd(nid))
                row = QHBoxLayout()
                row.addWidget(w, 1)
                row.addWidget(lock)
                row.addWidget(rnd)
                holder = QWidget()
                holder.setLayout(row)
                row.setContentsMargins(0, 0, 0, 0)
                w.valueChanged.connect(lambda v, nid=node_id: self.svc.set_param(nid, "seed", int(v)))
                self.widgets["seed"], self.widgets["seed_lock"], self.widgets["rnd"] = w, lock, rnd
                self.form.addRow(i18n.t("series.legend.param.seed"), holder)
                continue
            if key in CHOICES:
                w = QComboBox()
                for c in CHOICES[key]:
                    w.addItem(str(c), c)
                w.setCurrentIndex(max(0, w.findData(cur)))
                w.activated.connect(lambda _i, k=key, c=w, nid=node_id: self.svc.set_param(nid, k, c.currentData()))
            elif isinstance(value, bool):
                w = QCheckBox()
                w.setChecked(bool(cur))
                w.toggled.connect(lambda on, k=key, nid=node_id: self.svc.set_param(nid, k, on))
            elif isinstance(value, int):
                w = QSpinBox()
                w.setRange(0, 100000)
                w.setValue(int(cur))
                w.valueChanged.connect(lambda v, k=key, nid=node_id: self.svc.set_param(nid, k, int(v)))
            elif isinstance(value, float):
                lo, hi, step = FLOAT_RANGE.get(key, (0.0, 1000.0, 0.1))
                w = QDoubleSpinBox()
                w.setRange(lo, hi)
                w.setSingleStep(step)
                w.setValue(float(cur))
                w.valueChanged.connect(lambda v, k=key, nid=node_id: self.svc.set_param(nid, k, float(v)))
            else:
                w = QLineEdit(str(cur))
                w.editingFinished.connect(lambda k=key, c=w, nid=node_id: self.svc.set_param(nid, k, c.text()))
            self.widgets[key] = w
            self.form.addRow(i18n.t(f"series.legend.param.{key}") if i18n.has(f"series.legend.param.{key}") else key, w)
        for ed, key in ((self.raw_prompt, "prompt"), (self.raw_negative, "negative"), (self.raw_json, "json")):
            ed.setPlainText(node.raw.get(key, ""))
        self.raw_check.setText("")
        self.hint.setText(self._hint_for(node))
        self._busy_ui = False

    def _hint_for(self, node: Any) -> str:
        bad = [p for p in self.svc.graph.validate() if f":{node.id}." in p]
        base = i18n.t("series.legend.schema_bad", n=len(bad)) if bad else i18n.t("series.legend.schema_ok")
        cost = node.spec.cost
        extra = []
        if node.type == "image":
            extra.append(str(node.effective_params().get("model")))
        return " · ".join([base, *extra, cost] if cost else [base, *extra])

    def _rnd(self, node_id: str) -> None:
        seed = self.svc.randomize_seed(node_id)
        self.select_node(node_id)
        self.widgets["seed"].setValue(seed)

    def _raw_changed(self, key: str, ed: QPlainTextEdit) -> None:
        if not self._busy_ui and self.selected:
            self.svc.set_raw(self.selected, key, ed.toPlainText())

    def _check_raw(self) -> None:
        if self.selected:
            ok, msg = self.svc.check_raw(self.selected)
            self.raw_check.setText(("✓ " if ok else "✗ ") + msg)

    # -- alt sekmeler ----------------------------------------------------------------------------------------------------
    def _tabs(self) -> QWidget:
        self.tabs = QTabWidget()
        sb = QWidget()
        sl = QVBoxLayout(sb)
        self.sb_info = QLabel("")
        self.sb_info.setProperty("role", "mono")
        self.board = KanbanBoard()
        row = QHBoxLayout()
        self.kf_btn = QPushButton(i18n.t("series.board.make"))
        self.regen_btn = QPushButton("")
        self.approve_btn = SquishyButton(i18n.t("series.legend.approve_prod"), variant="primary", accent="pink")
        for w in (self.kf_btn, self.regen_btn, self.approve_btn):
            row.addWidget(w)
        sl.addWidget(self.sb_info)
        sl.addWidget(self.board, 1)
        sl.addLayout(row)
        self.tabs.addTab(sb, i18n.t("series.legend.tab.storyboard"))
        self.kf_btn.clicked.connect(lambda: self.svc.generate_keyframes())
        self.board.cardMoved.connect(self.svc.move_card)
        self.board.angleChanged.connect(self.svc.set_angle)
        self.board.regenerateRequested.connect(self.svc.regenerate_scene)
        self.board.selected.connect(lambda sid: self.regen_btn.setText(i18n.t("series.legend.regen_scene", id=sid)))
        self.regen_btn.clicked.connect(lambda: self.board.selected_id and self.svc.regenerate_scene(self.board.selected_id))
        self.approve_btn.clicked.connect(lambda: self.svc.approve() and self.svc.advance())
        mx = QWidget()
        ml = QFormLayout(mx)
        self.mx_node = QComboBox()
        self.mx_x, self.mx_xv = QComboBox(), QLineEdit("11, 22")
        self.mx_y, self.mx_yv = QComboBox(), QLineEdit("4, 8")
        for c, default in ((self.mx_x, "seed"), (self.mx_y, "cfg")):
            for k in ("seed", "cfg", "steps", "ref_strength", "temperature", "seconds"):
                c.addItem(k, k)
            c.setCurrentIndex(c.findData(default))
        self.mx_btn = SquishyButton(i18n.t("series.legend.matrix_run"), variant="primary")
        self.mx_table = QTableWidget(0, 0)
        ml.addRow(i18n.t("series.legend.matrix_node"), self.mx_node)
        ml.addRow("X", self._pair(self.mx_x, self.mx_xv))
        ml.addRow("Y", self._pair(self.mx_y, self.mx_yv))
        ml.addRow(self.mx_btn)
        ml.addRow(self.mx_table)
        self.tabs.addTab(mx, i18n.t("series.legend.tab.matrix"))
        self.mx_btn.clicked.connect(self._run_matrix)
        hist = QWidget()
        hl = QVBoxLayout(hist)
        self.hist_head = QLabel("")
        self.hist_head.setProperty("role", "caps")
        self.hist_table = QTableWidget(0, 7)
        self.hist_table.setHorizontalHeaderLabels([i18n.t(f"series.legend.h.{k}") for k in ("time", "node", "model", "seed", "dur", "cost", "status")])
        self.hist_table.verticalHeader().setVisible(False)
        self.hist_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hl.addWidget(self.hist_head)
        hl.addWidget(self.hist_table, 1)
        self.tabs.addTab(hist, i18n.t("series.legend.tab.history"))
        self.analytics = AnalyticsPanel()
        self.tabs.addTab(self.analytics, i18n.t("series.legend.tab.analytics"))
        return self.tabs

    @staticmethod
    def _pair(a: QWidget, b: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(a)
        h.addWidget(b, 1)
        return w

    def _values(self, text: str) -> List[Any]:
        out: List[Any] = []
        for part in text.replace(";", ",").split(","):
            part = part.strip()
            if part:
                out.append(float(part) if "." in part else int(part))
        return out

    def _run_matrix(self) -> None:
        node = str(self.mx_node.currentData() or "")
        try:
            xv, yv = self._values(self.mx_xv.text()), self._values(self.mx_yv.text())
        except ValueError:
            return
        if node and xv and yv:
            self.clear_error()
            try:
                self.svc.run_matrix(node, (str(self.mx_x.currentData()), xv), (str(self.mx_y.currentData()), yv))
            except ValueError as exc:
                self.svc._fail(exc)

    # -- çizim ---------------------------------------------------------------------------------------------------------------
    def refresh_graph(self) -> None:
        g = self.svc.graph
        self.canvas.set_graph(g)
        self.flow_info.setText(i18n.t("series.legend.flow_info", nodes=len(g.nodes), edges=len(g.connections)))
        cur = self.mx_node.currentData()
        self.mx_node.clear()
        for n in g.nodes.values():
            if n.type in ("image", "llm", "video"):
                self.mx_node.addItem(f"{n.id}", n.id)
        if cur:
            self.mx_node.setCurrentIndex(max(0, self.mx_node.findData(cur)))
        if self.selected in g.nodes:
            self.hint.setText(self._hint_for(g.nodes[self.selected]))

    def refresh_project(self) -> None:
        p = self.svc.project
        self.stage_bar.set_stage(self.svc.stage, p.approvals)
        share = self.svc.plan_share()
        est = self.svc.estimate()
        self.quota_label.setText(i18n.t("series.legend.quota", pct=f"{share.project_pct:.0f}", cost=f"{est.cost_usd:.2f}", before=f"{share.before_pct:.0f}",
                                        after=f"{share.before_pct + share.project_pct:.0f}") if share.budget else i18n.t("series.legend.quota_usd", cost=f"{est.cost_usd:.2f}"))
        done = sum(1 for s in p.scenes if s.story == "approved")
        self.sb_info.setText(i18n.t("series.legend.sb_info", ep=(p.scenes[0].episode if p.scenes else 1), n=len(p.scenes), ok=done,
                                    locked=sum(1 for c in p.characters if c.locked)))
        self.board.set_scenes(self.svc.columns())
        self.session_label.setText(i18n.t("series.legend.session", cost=f"{self.svc.gw().session_cost:.2f}" if self.svc.gateway else "0.00",
                                          tokens=f"{(self.svc.gateway.session_tokens if self.svc.gateway else 0) / 1000:.1f}"))

    def refresh_history(self) -> None:
        runs = self.svc.runs
        rows = [(run, r) for run in runs for r in run.rows()][:60]
        self.hist_head.setText(i18n.t("series.legend.hist_head", n=len(rows), cost=f"{sum(r.cost for _run, r in rows):.2f}"))
        self.hist_table.setRowCount(len(rows))
        for i, (run, r) in enumerate(rows):
            vals = (time.strftime("%H:%M:%S", time.localtime(r.started or run.started)), r.node, r.model or r.provider, str(r.params.get("seed", "")),
                    f"{r.seconds:.1f}s", f"${r.cost:.3f}", r.status)
            for j, v in enumerate(vals):
                self.hist_table.setItem(i, j, QTableWidgetItem(v))

    def refresh_analytics(self) -> None:
        self.analytics.set_rows(self.svc.analytics_rows(), self.svc.analytics())

    def _on_run(self, run: Any) -> None:
        for nid, res in run.results.items():
            self.canvas.set_status(nid.split("@")[0], res.status)
        if run.matrix:
            self._show_matrix(run)
        self.refresh_history()
        self.refresh_analytics()
        self.refresh_project()
        if self.selected:
            self.select_node(self.selected)

    def _show_matrix(self, run: Any) -> None:
        xp, xv = run.matrix["x"]
        yp, yv = run.matrix["y"]
        self.mx_table.setRowCount(len(yv))
        self.mx_table.setColumnCount(len(xv))
        self.mx_table.setHorizontalHeaderLabels([f"{xp}={v}" for v in xv])
        self.mx_table.setVerticalHeaderLabels([f"{yp}={v}" for v in yv])
        for j, row in enumerate(run.matrix["cells"]):
            for i, key in enumerate(row):
                r = run.results[key]
                self.mx_table.setItem(j, i, QTableWidgetItem(f"{r.status}  ${r.cost:.3f}"))

    def _validate_flow(self) -> None:
        problems = self.svc.graph.validate()
        self.flow_info.setText(i18n.t("series.legend.flow_ok") if not problems else i18n.t("series.legend.flow_bad", p=", ".join(problems[:3])))
