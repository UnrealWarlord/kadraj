"""Dizi/Film bileşenleri: ``StageBar`` (aşama çubuğu), ``KanbanBoard`` (sahne panosu: sürükle-bırak + kamera açısı + tek sahne yeniden üret)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QByteArray, QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from ...ai_engine.pipeline.machine import CAMERA_ANGLES, STAGE_ORDER, STORY_STATUS, Scene, Stage
from ...core import i18n

MIME = "application/x-kadraj-scene"


class StageBar(QWidget):
    """Aşama çipleri: ✓ tamamlanan, ● şimdiki, ○ gelecek. Geçmiş aşamaya tıklamak geri gider (``stageRequested``)."""

    stageRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.buttons: Dict[Stage, QPushButton] = {}
        for st in STAGE_ORDER:
            if st is Stage.DONE:
                continue
            b = QPushButton(i18n.t(f"series.stage.{st.value}"))
            b.setCheckable(False)
            b.clicked.connect(lambda _c=False, s=st: self.stageRequested.emit(s.value))
            self.buttons[st] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.current: Stage = Stage.IDEA

    def set_stage(self, stage: Stage, approvals: Optional[Dict[str, bool]] = None) -> None:
        self.current = stage
        idx = STAGE_ORDER.index(stage)
        for st, b in self.buttons.items():
            i = STAGE_ORDER.index(st)
            mark = "✓" if i < idx or stage is Stage.DONE else "●" if i == idx else "○"
            b.setText(f"{mark} {i18n.t(f'series.stage.{st.value}')}")
            b.setProperty("stage_state", "done" if mark == "✓" else "current" if mark == "●" else "todo")
            b.setEnabled(i <= idx)
            b.setFlat(i != idx)


class _Column(QListWidget):
    dropped = Signal(str, str)                 # (sahne, sütun durumu)

    def __init__(self, status: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.status = status
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setIconSize(QSize(96, 54))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMinimumWidth(190)

    def startDrag(self, actions) -> None:                  # noqa: N802
        it = self.currentItem()
        if it is None:
            return
        mime = QMimeData()
        mime.setData(MIME, QByteArray(str(it.data(Qt.ItemDataRole.UserRole)).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, e) -> None:                   # noqa: N802
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:                    # noqa: N802
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:                        # noqa: N802
        if e.mimeData().hasFormat(MIME):
            sid = bytes(e.mimeData().data(MIME)).decode()
            e.acceptProposedAction()
            self.dropped.emit(sid, self.status)            # durumu servis günceller; pano oradan yeniden kurulur


class KanbanBoard(QWidget):
    """Sahne panosu. Kartlar sürüklenerek sütun değiştirir (Taslak · Onay bekliyor · Onaylandı); seçili kartın kamera açısı değişir,
    "Bu sahneyi yeniden yap" yalnız o sahneyi tazeler."""

    cardMoved = Signal(str, str)
    angleChanged = Signal(str, str)
    regenerateRequested = Signal(str)
    selected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        cols = QHBoxLayout()
        self.columns: Dict[str, _Column] = {}
        self.headers: Dict[str, QLabel] = {}
        for st in STORY_STATUS:
            box = QVBoxLayout()
            h = QLabel("")
            h.setProperty("role", "caps")
            col = _Column(st)
            col.dropped.connect(self.cardMoved)
            col.itemSelectionChanged.connect(self._sel)
            self.columns[st], self.headers[st] = col, h
            box.addWidget(h)
            box.addWidget(col, 1)
            cols.addLayout(box, 1)
        lay.addLayout(cols, 1)
        row = QHBoxLayout()
        self.angle_box = QComboBox()
        for aid in CAMERA_ANGLES:
            self.angle_box.addItem(i18n.t(f"series.angle.{aid}"), aid)
        self.regen_btn = QPushButton(i18n.t("series.regen"))
        self.drop_hint = QLabel(i18n.t("series.drop_here"))
        self.drop_hint.setProperty("role", "hint")
        row.addWidget(QLabel(i18n.t("series.angle")))
        row.addWidget(self.angle_box)
        row.addWidget(self.regen_btn)
        row.addStretch(1)
        row.addWidget(self.drop_hint)
        lay.addLayout(row)
        self.angle_box.activated.connect(lambda _i: self._angle())
        self.regen_btn.clicked.connect(lambda: self.selected_id and self.regenerateRequested.emit(self.selected_id))
        self.selected_id: Optional[str] = None
        self._busy = False

    def set_scenes(self, columns: Dict[str, List[Scene]]) -> None:
        self._busy = True
        keep = self.selected_id
        for st, col in self.columns.items():
            col.clear()
            self.headers[st].setText(f"{i18n.t(f'series.col.{st}').upper()}  ·  {len(columns.get(st, []))}")
            for sc in columns.get(st, []):
                it = QListWidgetItem(f"{sc.id}  {sc.title}\n{i18n.t(f'series.angle.{sc.angle}')} · {sc.seconds:.0f} sn")
                it.setData(Qt.ItemDataRole.UserRole, sc.id)
                if sc.keyframe:
                    pm = QPixmap(sc.keyframe)
                    if not pm.isNull():
                        it.setIcon(QIcon(pm.scaled(96, 54, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
                col.addItem(it)
                if sc.id == keep:
                    col.setCurrentItem(it)
        self._busy = False

    def card_ids(self, status: str) -> List[str]:
        col = self.columns[status]
        return [str(col.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(col.count())]

    def select(self, scene_id: str) -> None:
        for col in self.columns.values():
            for i in range(col.count()):
                if col.item(i).data(Qt.ItemDataRole.UserRole) == scene_id:
                    col.setCurrentRow(i)

    def move_card(self, scene_id: str, status: str) -> None:
        """Sürükle-bırak ile aynı yol (testler/klavye için)."""
        self.cardMoved.emit(scene_id, status)

    def _sel(self) -> None:
        if self._busy:
            return
        for col in self.columns.values():
            it = col.currentItem()
            if it is not None and it.isSelected():
                self.selected_id = str(it.data(Qt.ItemDataRole.UserRole))
                self.selected.emit(self.selected_id)
                return

    def set_selected_angle(self, angle: str) -> None:
        self.angle_box.setCurrentIndex(max(0, self.angle_box.findData(angle)))

    def _angle(self) -> None:
        if self.selected_id:
            self.angleChanged.emit(self.selected_id, str(self.angle_box.currentData()))
