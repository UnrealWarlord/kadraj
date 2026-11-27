"""Legend Mode bileşenleri — proje merkezi, çalışma alanları ve dock panelleri (tuval: V2Home, Legend).

* ``ProjectHub``       : KPI şeridi + proje tablosu + açık proje özeti (Proje çalışma alanı).
* ``LegendCenter``     : çalışma alanı düğmeleri + her çalışma alanı için modül sekmeleri (tembel kurulum).
* ``QuickStartPanel``  : Hızlı Başlat (Ctrl+1…5) · ``JobsPanel`` : Görev Kuyruğu · ``SystemPanel`` : Sistem.

Hiçbiri durum tutmaz; veriyi ``ctx``'ten okur ve olayları ``ctx.bus``'tan dinler.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QProgressBar, QPushButton, QScrollArea, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ...ai_engine import analytics
from ...core import i18n, sysmon
from ...core.contracts import WORKSPACES, Job, JobStatus, Topics, UiMode
from ...core.registry import ModuleEntry
from ...modules.series.charts import AnalyticsPanel
from ...storage import disk
from .. import adaptive
from ..widgets.squishy_button import SquishyButton
from ..widgets.task_card import TaskCard
from .home import human_ago
from .pages import build_module_page


def _gb(n: Optional[float]) -> str:
    return "—" if n is None else f"{n / 1e9:.0f} GB"


def _budget(ctx) -> Optional[float]:
    """Aylık API bütçesi (USD); tanımsız, sıfır ya da geçersizse ``None``."""
    try:
        value = float(ctx.settings.get("plan.budget_usd") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _disk_text(root: Optional[str]) -> str:
    """Kök klasörün boş alanı (``storage.disk``); ölçülemiyorsa ya da kök yoksa "—"."""
    if not root:
        return "—"
    st = disk.status(root)
    if st.level is disk.Level.UNKNOWN:
        return "—"
    return i18n.t("kpi.disk_free", gb=_gb(st.free))


def _tokens_text(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class _Kpi(QFrame):
    def __init__(self, key: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.caption = QLabel(i18n.upper(i18n.t(f"kpi.{key}")))
        self.caption.setProperty("role", "caps")
        self.value = QLabel("—")
        self.value.setProperty("role", "h2")
        self.sub = QLabel("")
        self.sub.setProperty("role", "muted")
        lay.addWidget(self.caption)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        self.key = key

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)


class ProjectHub(QWidget):
    openProjectRequested = Signal(str)
    newProjectRequested = Signal()
    openFolderRequested = Signal()

    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)

        self.kpis: Dict[str, _Kpi] = {k: _Kpi(k) for k in ("render", "cost", "tokens", "disk")}
        row = QHBoxLayout()
        row.setSpacing(12)
        for k in self.kpis.values():
            row.addWidget(k, 1)
        lay.addLayout(row)

        head = QHBoxLayout()
        self.title = QLabel("")
        self.title.setProperty("role", "caps")
        head.addWidget(self.title)
        head.addStretch(1)
        self.new_btn = SquishyButton("", variant="primary", icon="plus")
        self.open_btn = SquishyButton("", variant="secondary", icon="folder")
        self.new_btn.clicked.connect(self.newProjectRequested.emit)
        self.open_btn.clicked.connect(self.openFolderRequested.emit)
        head.addWidget(self.open_btn)
        head.addWidget(self.new_btn)
        lay.addLayout(head)

        self.table = QTableWidget(0, 3)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(self._open_row)
        lay.addWidget(self.table, 1)
        self.analytics = AnalyticsPanel(self)         # günlük maliyet grafiği + sağlayıcı tablosu (veri yoksa sakin boş durum)
        lay.addWidget(self.analytics, 1)

        self.empty = QLabel("")
        self.empty.setProperty("role", "muted")
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        self.current = QLabel("")
        self.current.setProperty("role", "mono")
        lay.addWidget(self.current)
        self.refresh()

    def _open_row(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            self.openProjectRequested.emit(item.data(Qt.ItemDataRole.UserRole))

    def refresh(self) -> None:
        self.title.setText(i18n.upper(i18n.t("hub.projects")))
        self.new_btn.setText(i18n.t("home.new_project"))
        self.open_btn.setText(i18n.t("hub.open"))
        for k in self.kpis.values():
            k.caption.setText(i18n.upper(i18n.t(f"kpi.{k.key}")))
        self.table.setHorizontalHeaderLabels([i18n.upper(i18n.t("hub.col.name")), i18n.upper(i18n.t("hub.col.path")),
                                              i18n.upper(i18n.t("hub.col.opened"))])
        recents = self.ctx.recent.get_recent()
        self.table.setRowCount(len(recents))
        for r, e in enumerate(recents):
            name = QTableWidgetItem(e.name)
            name.setData(Qt.ItemDataRole.UserRole, e.path)
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(e.path))
            self.table.setItem(r, 2, QTableWidgetItem(human_ago(e.last_opened)))
        self.empty.setText("" if recents else i18n.t("hub.empty"))
        self.empty.setVisible(not recents)
        proj = self.ctx.project
        self.current.setText(i18n.t("hub.current", name=proj.name, path=str(proj.root)) if proj.is_open
                             else i18n.t("hub.no_project"))
        # KPI'lar: bu ay (takvim ayı); proje açık değilse yalnız disk ölçülür
        budget = _budget(self.ctx)
        root = str(proj.root) if proj.is_open else (self.ctx.settings.get("project_root") or None)
        self.kpis["disk"].set(_disk_text(root))
        self.kpis["render"].set("—")
        self.kpis["cost"].set("—")
        self.kpis["tokens"].set("—")
        if proj.is_open:
            try:
                kpi = analytics.month_kpi(proj.db, budget_usd=budget)
                self.kpis["render"].set(str(kpi.renders))
                pct = kpi.budget.percent
                self.kpis["cost"].set(f"${kpi.cost_usd:.2f}",
                                      f"{pct:.0f}% / ${budget:.0f}" if pct is not None and budget else "")
                total = kpi.tokens_in + kpi.tokens_out
                self.kpis["tokens"].set(_tokens_text(total), f"{kpi.tokens_in:,} / {kpi.tokens_out:,}")
            except Exception:  # veritabanı okunamazsa KPI'lar "—" kalır; arayüz çökmez
                pass
        self._refresh_analytics(proj, budget)

    def _refresh_analytics(self, proj, budget: Optional[float]) -> None:
        """Son 30 günün maliyet analitiği (grafik + sağlayıcı tablosu). Proje yoksa boş veri."""
        rows: List[dict] = []
        if proj.is_open:
            try:
                rows = analytics.fetch_rows(proj.db, since=time.time() - 30 * 86400)
            except Exception:
                rows = []
        self.analytics.set_rows(rows, analytics.chart_data(rows, days=30, budget_usd=budget))


class QuickStartPanel(QWidget):
    moduleRequested = Signal(str)

    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(8, 8, 8, 8)
        self._lay.setSpacing(6)
        self.cards: List[TaskCard] = []
        self.refresh()

    def refresh(self) -> None:
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = []
        while self._lay.count():
            self._lay.takeAt(0)
        for e in self.ctx.registry.entries():
            if e.id == "settings":
                continue
            card = TaskCard(i18n.term(e.title_key), "", e.icon, e.accent, soon=not e.available, shortcut=e.shortcut)
            card.clicked.connect(lambda _=False, mid=e.id: self.moduleRequested.emit(mid))
            self._lay.addWidget(card)
            self.cards.append(card)
        self._lay.addStretch(1)


class JobsPanel(QWidget):
    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(10, 8, 10, 8)
        self._lay.setSpacing(8)
        self._rows: Dict[int, tuple] = {}
        self.empty = QLabel(i18n.t("jobs.empty"))
        self.empty.setProperty("role", "muted")
        self.empty.setWordWrap(True)
        self._lay.addWidget(self.empty)
        self._lay.addStretch(1)
        ctx.bus.subscribe(Topics.JOB_ADDED, self._on_job)
        ctx.bus.subscribe(Topics.JOB_UPDATED, self._on_job)
        for j in ctx.jobs.list():
            self._on_job(j)

    def _on_job(self, job: Job) -> None:
        if job.id is None:
            return
        row = self._rows.get(job.id)
        if row is None:
            box = QFrame()
            box.setProperty("role", "surface2")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(8, 6, 8, 6)
            title = QLabel(job.title or job.kind)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            state = QLabel("")
            state.setProperty("role", "muted")
            bl.addWidget(title)
            bl.addWidget(bar)
            bl.addWidget(state)
            self._lay.insertWidget(max(0, self._lay.count() - 1), box)
            row = (box, bar, state)
            self._rows[job.id] = row
        _, bar, state = row
        bar.setValue(int(job.progress * 100))
        state.setText(i18n.t(f"jobs.state.{job.status.value}") + (f" — {job.error}" if job.error else ""))
        self.empty.setVisible(not self._rows)

    def active_count(self) -> int:
        return len([j for j in self.ctx.jobs.list() if not j.finished])


class SystemPanel(QWidget):
    sampled = Signal(dict)

    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        lay = QGridLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setVerticalSpacing(6)
        self._vals: Dict[str, QLabel] = {}
        shown = sysmon.available_keys()                 # Android'de işlemci ve ekran kartı satırı yok (M21)
        for i, key in enumerate(sysmon.PANEL_KEYS):
            cap = QLabel(i18n.upper(i18n.t(f"sys.{key}")))
            cap.setProperty("role", "caps")
            val = QLabel("—")
            val.setProperty("role", "mono")
            lay.addWidget(cap, i, 0)
            lay.addWidget(val, i, 1, Qt.AlignmentFlag.AlignRight)
            self._vals[key] = val
            cap.setVisible(key in shown)
            val.setVisible(key in shown)
        lay.setRowStretch(len(sysmon.PANEL_KEYS), 1)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.sample_now)
        self.sample_now()

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def sample_now(self) -> None:
        s = self.ctx.sysmon.sample()
        self._vals["cpu"].setText(f"{s['cpu']:.0f}%" if s.get("cpu") is not None else "—")
        self._vals["ram"].setText(f"{s['ram_used'] / 1e9:.1f}/{s['ram_total'] / 1e9:.0f} GB" if s.get("ram_total") else "—")
        self._vals["gpu"].setText(f"{s['gpu']:.0f}%" if s.get("gpu") is not None else "—")
        self._vals["vram"].setText(f"{s['vram_used'] / 1e9:.1f}/{s['vram_total'] / 1e9:.0f} GB"
                                   if s.get("vram_total") else "—")
        self._vals["disk"].setText(_gb(s.get("disk_free")))
        self._vals["ping"].setText(f"{s['api_ping']:.0f} ms" if s.get("api_ping") is not None else "—")
        self.sampled.emit(s)
        self.ctx.bus.emit(Topics.SYSMON, s)


class _LazyTab(QWidget):
    """Sekme içeriği ilk gösterildiğinde modül sayfasını kurar (açılışı ağırlaştırmaz)."""

    def __init__(self, ctx, entry: ModuleEntry, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx, self.entry = ctx, entry
        self.page: Optional[QWidget] = None
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)

    def ensure(self) -> QWidget:
        if self.page is None:
            self.page = build_module_page(self.ctx, self.entry, UiMode.LEGEND)
            self._lay.addWidget(self.page)
            adaptive.touch_scroll_all(self.page)          # tablet: listeler/tablolar parmakla kayar
        return self.page


class LegendCenter(QWidget):
    """Çalışma alanı düğmeleri + sayfalar. ``workspaceChanged(str)`` seçimi duyurur."""

    workspaceChanged = Signal(str)
    openProjectRequested = Signal(str)
    newProjectRequested = Signal()
    openFolderRequested = Signal()

    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._current = "project"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QFrame()
        bar.setProperty("role", "topbar")
        bl = adaptive.hbox(bar)                           # tablet dikeyde düğmeler alt satıra geçebilir
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(6)
        self._buttons: Dict[str, QPushButton] = {}
        for ws in WORKSPACES:
            b = QPushButton("")
            b.setCheckable(True)
            b.setProperty("role", "topbtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, w=ws: self.show_workspace(w))
            bl.addWidget(b)
            self._buttons[ws] = b
        bl.addStretch(1)
        lay.addWidget(bar)
        self.stack = adaptive.stack()                     # mobilde boy yalnız açık çalışma alanından
        lay.addWidget(adaptive.scroll_both(self.stack), 1)  # tablet dikey: sığmayan sayfa kesilmez, kayar

        self.hub = ProjectHub(ctx)
        self.hub.openProjectRequested.connect(self.openProjectRequested.emit)
        self.hub.newProjectRequested.connect(self.newProjectRequested.emit)
        self.hub.openFolderRequested.connect(self.openFolderRequested.emit)
        self._pages: Dict[str, QWidget] = {"project": self.hub}
        self._tabs: Dict[str, QTabWidget] = {}
        self.stack.addWidget(self.hub)
        for ws in WORKSPACES[1:]:
            tabs = adaptive.tabs()
            tabs.currentChanged.connect(lambda _i, t=tabs: self._ensure_current(t))
            self._tabs[ws] = tabs
            self._pages[ws] = tabs
            self.stack.addWidget(tabs)
        self.rebuild()

    @property
    def current(self) -> str:
        return self._current

    def rebuild(self) -> None:
        """Çalışma alanı sekmelerini kayıt defterinden yeniden kurar (dil/mod/kayıt değişince)."""
        for ws, b in self._buttons.items():
            b.setText(i18n.t(f"workspace.{ws}"))
        entries = self.ctx.registry.entries()
        for ws, tabs in self._tabs.items():
            keep = tabs.currentIndex()
            while tabs.count():
                w = tabs.widget(0)
                tabs.removeTab(0)
                w.deleteLater()
            for e in entries:
                if e.workspace == ws:
                    tabs.addTab(_LazyTab(self.ctx, e), i18n.term(e.title_key))
            if tabs.count() == 0:
                tabs.addTab(_LazyTab(self.ctx, ModuleEntry(ws, f"workspace.{ws}", "", 99, entries[0].accent, "dot", ws, "", "", None)),
                            i18n.t(f"workspace.{ws}"))
            tabs.setCurrentIndex(max(0, min(keep, tabs.count() - 1)))
        self.hub.refresh()
        self._ensure_current(self._tabs.get(self._current))
        self._sync_buttons()

    def _ensure_current(self, tabs: Optional[QTabWidget]) -> None:
        if tabs is None:
            return
        w = tabs.currentWidget()
        if isinstance(w, _LazyTab):
            w.ensure()

    def _sync_buttons(self) -> None:
        for ws, b in self._buttons.items():
            b.setChecked(ws == self._current)

    def show_workspace(self, ws: str) -> None:
        if ws not in self._pages:
            return
        self._current = ws
        self.stack.setCurrentWidget(self._pages[ws])
        self._ensure_current(self._tabs.get(ws))
        self._sync_buttons()
        self.workspaceChanged.emit(ws)

    def show_module(self, entry: ModuleEntry) -> None:
        ws = entry.workspace if entry.workspace in self._pages else "project"
        tabs = self._tabs.get(ws)
        if tabs is not None:
            for i in range(tabs.count()):
                w = tabs.widget(i)
                if isinstance(w, _LazyTab) and w.entry.id == entry.id:
                    tabs.setCurrentIndex(i)
                    break
        self.show_workspace(ws)
