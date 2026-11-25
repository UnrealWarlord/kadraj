"""Basit "Video Düzenle" sayfası (tuval: V2Editor · Basit).

Üstte proje adı + süre + "Geri Al" + "Videoyu Kaydet". Sol: önizleme (Önce/Sonra, oynat, süre, Yatay/Dikey). Sağ: "Sihirli
dokunuşlar" (hiçbiri kendiliğinden çalışmaz; öneri → Uygula → geri alınabilir) ve medya. Altta: "Nerede paylaşacaksın?" (7 platform) +
kalite seçici (Hızlı/Dengeli/Ağır + tahmini boyut/süre) + "Dosyayı Klasörde Göster", en altta mıknatıslı "Zaman çizgisi"
(3 kanal: Video, Müzik, Seslendirme). Teknik terim yok.
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ...core import i18n
from ...ui.widgets.help_bubble import HelpBubble
from ...ui.widgets.squishy_button import SquishyButton
from .page_base import EditorPageBase
from .panels import ExportPanel, LibraryPanel, MagicGrid, _label
from .timeline import format_clock
from .widgets import PreviewView, TimelineView

WIDE = 980


def _card() -> tuple:
    f = QFrame()
    f.setProperty("role", "card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)
    return f, lay


class EditorSimplePage(EditorPageBase):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(ctx, parent)
        self.setObjectName("editor_simple")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self.scroll)
        body = QWidget()
        self.scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 12)
        lay.setSpacing(14)
        lay.addLayout(self._build_header())
        lay.addLayout(self.error_slot)
        lay.addWidget(self.proposal_bar)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        self.left = self._build_preview()
        self.right = self._build_magic()
        lay.addLayout(self.grid)
        self._columns = 0
        self._relayout(self.width() or 1200)
        self.export = ExportPanel(self.svc, pro=False)
        ex, exl = _card()
        exl.addWidget(self.export)
        lay.addWidget(ex)
        lay.addWidget(self._build_timeline())
        lay.addStretch(1)
        self.connect_service()
        self.svc.selectionChanged.connect(self._sync)
        self.svc.timelineChanged.connect(lambda _r: self._sync())
        self.svc.playheadChanged.connect(lambda _t: self._sync_time())
        self.svc.libraryChanged.connect(self._sync)
        self.timeline.seekRequested.connect(self._seek)
        self.timeline.selectionRequested.connect(self.svc.select)
        self._sync()

    # -- yapı -------------------------------------------------------------------------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.project_label = QLabel("")
        self.project_label.setProperty("role", "h2")
        self.duration_label = _label("", "hint")
        self.undo_btn = SquishyButton(i18n.t("editor.simple.undo"), variant="secondary", icon="undo")
        self.save_btn = SquishyButton(i18n.t("editor.export.go"), variant="primary", icon="download", accent="mint")
        row.addWidget(self.project_label)
        row.addWidget(self.duration_label)
        row.addStretch(1)
        row.addWidget(self.undo_btn)
        row.addWidget(self.save_btn)
        self.undo_btn.clicked.connect(self.svc.undo)
        self.save_btn.clicked.connect(self._save)
        return row

    def _build_preview(self) -> QWidget:
        card, lay = _card()
        self.preview = PreviewView()
        self.preview.setMinimumHeight(260)
        lay.addWidget(self.preview, 1)
        row = QHBoxLayout()
        self.play_btn = SquishyButton(i18n.t("editor.simple.play"), variant="primary", icon="check")
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setProperty("role", "mono")
        self.compare_btn = QPushButton(i18n.t("editor.simple.compare"))
        self.compare_btn.setCheckable(True)
        row.addWidget(self.play_btn)
        row.addWidget(self.time_label)
        row.addStretch(1)
        row.addWidget(self.compare_btn)
        lay.addLayout(row)
        orient = QHBoxLayout()
        self.landscape_btn = QPushButton(i18n.t("editor.simple.landscape"))
        self.portrait_btn = QPushButton(i18n.t("editor.simple.portrait"))
        for b in (self.landscape_btn, self.portrait_btn):
            b.setCheckable(True)
            b.setAutoExclusive(True)
            orient.addWidget(b)
        orient.addStretch(1)
        lay.addLayout(orient)
        self.play_btn.clicked.connect(self.toggle_play)
        self.compare_btn.toggled.connect(self.set_compare)
        self.landscape_btn.clicked.connect(lambda: self.svc.set_canvas(1920, 1080))
        self.portrait_btn.clicked.connect(lambda: self.svc.set_canvas(1080, 1920))
        return card

    def _build_magic(self) -> QWidget:
        card, lay = _card()
        head = QHBoxLayout()
        title = QLabel(i18n.t("editor.simple.magic"))
        title.setProperty("role", "h2")
        head.addWidget(title)
        head.addWidget(HelpBubble(i18n.t("editor.simple.magic.hint")))
        head.addStretch(1)
        lay.addLayout(head)
        lay.addWidget(_label(i18n.t("editor.simple.magic.hint"), "hint"))
        self.magic = MagicGrid(self.svc, columns=1)
        lay.addWidget(self.magic)
        lib_title = QLabel(i18n.t("editor.simple.library"))
        lib_title.setProperty("role", "h2")
        lay.addWidget(lib_title)
        self.library = LibraryPanel(self.svc)
        self.library.setMinimumHeight(120)
        lay.addWidget(self.library)
        return card

    def _build_timeline(self) -> QWidget:
        card, lay = _card()
        head = QHBoxLayout()
        title = QLabel(i18n.t("editor.simple.timeline"))
        title.setProperty("role", "h2")
        self.magnet_btn = QPushButton("")
        self.magnet_btn.setCheckable(True)
        self.magnet_btn.setChecked(self.svc.snap_enabled)
        self.split_btn = SquishyButton(i18n.t("editor.simple.split"), variant="secondary")
        self.delete_btn = SquishyButton(i18n.t("editor.simple.delete"), variant="ghost")
        self.zoom_out_btn = QPushButton("−")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_out_btn.setAccessibleName(i18n.t("editor.simple.zoom_out"))
        self.zoom_in_btn.setAccessibleName(i18n.t("editor.simple.zoom_in"))
        for w in (title, self.magnet_btn):
            head.addWidget(w)
        head.addStretch(1)
        for w in (self.split_btn, self.delete_btn, self.zoom_out_btn, self.zoom_in_btn):
            head.addWidget(w)
        lay.addLayout(head)
        self.timeline = TimelineView(self.svc, pro=False)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.timeline)
        area.setMinimumHeight(self.timeline.minimumHeight() + 24)
        area.setFrameShape(QFrame.Shape.NoFrame)
        self.timeline_area = area
        lay.addWidget(area)
        help_row = QHBoxLayout()
        self.help_label = _label(i18n.t("editor.simple.help"), "hint")
        help_row.addWidget(HelpBubble(i18n.t("editor.simple.help")))
        help_row.addWidget(self.help_label, 1)
        lay.addLayout(help_row)
        self.magnet_btn.toggled.connect(self._magnet)
        self.split_btn.clicked.connect(self.svc.split_at_playhead)
        self.delete_btn.clicked.connect(lambda: self.svc.delete_selection())
        self.zoom_in_btn.clicked.connect(lambda: self.timeline.zoom_by(1.3))
        self.zoom_out_btn.clicked.connect(lambda: self.timeline.zoom_by(1 / 1.3))
        self._magnet(self.svc.snap_enabled)
        return card

    # -- davranış ---------------------------------------------------------------------------------------------------------------
    def _relayout(self, width: int) -> None:
        cols = 2 if width >= WIDE else 1
        if cols == self._columns:
            return
        self._columns = cols
        for w in (self.left, self.right):
            self.grid.removeWidget(w)
        self.grid.addWidget(self.left, 0, 0)
        if cols == 2:
            self.grid.addWidget(self.right, 0, 1)
            self.grid.setColumnStretch(0, 3)
            self.grid.setColumnStretch(1, 2)
        else:
            self.grid.addWidget(self.right, 1, 0)
            self.grid.setColumnStretch(0, 1)
            self.grid.setColumnStretch(1, 0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout(e.size().width())

    def _magnet(self, on: bool) -> None:
        self.svc.set_snap(on)
        self.magnet_btn.setText(i18n.t("editor.simple.magnet" if on else "editor.simple.magnet_off"))

    def _seek(self, t: float) -> None:
        self.svc.set_playhead(t, snap=True, px_per_s=self.timeline.px_per_s)
        self.player.seek(self.svc.playhead)

    def _save(self) -> None:
        self.export.start()

    def _sync(self) -> None:
        name = self.ctx.project.name if self.svc.project_open else ""
        self.project_label.setText(name or i18n.t("editor.simple.title"))
        self.duration_label.setText(format_clock(self.svc.tl.duration))
        self.landscape_btn.setChecked(self.svc.orientation == "landscape")
        self.portrait_btn.setChecked(self.svc.orientation == "portrait")
        self.undo_btn.setEnabled(self.svc.undo_stack.canUndo())
        self._sync_time()

    def _sync_time(self) -> None:
        self.time_label.setText(f"{format_clock(self.svc.playhead)} / {format_clock(self.svc.tl.duration)}")
        self.timeline.update()

    def on_player_state(self, state: str) -> None:
        self.play_btn.setText(i18n.t("editor.simple.pause" if state == "playing" else "editor.simple.play"))
