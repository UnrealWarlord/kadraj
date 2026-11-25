"""Düğüm tuvali (``QGraphicsView``): sürüklenebilir düğümler, tür renkli portlar, eğri bağlantılar, çıkış portundan girişe sürükleyerek bağlama,
durum rozetleri (idle/done/error). Durum servistedir (``graph``); tuval yalnız çizer ve kullanıcı eylemini servise iletir."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView, QMenu, QWidget)

from ...ai_engine.nodes.graph import Graph, Node
from ...ai_engine.nodes.graph import SPECS
from ...ui import fonts
from ...ui.theme.manager import current_tokens

W, HEAD, ROW = 190, 28, 22
PORT_R = 6
TYPE_COLOR = {"text": "#7AA2F7", "image": "#E0AF68", "video": "#F7768E", "audio": "#9ECE6A", "any": "#9BA1AB"}
STATUS_COLOR = {"done": "#46D18A", "error": "#FF5A5F", "running": "#F5A04A", "skipped": "#9BA1AB", "idle": "#6B7280"}


def port_pos(node_pos: QPointF, index: int, output: bool) -> QPointF:
    return QPointF(node_pos.x() + (W if output else 0), node_pos.y() + HEAD + 34 + index * ROW)


class NodeItem(QGraphicsItem):
    def __init__(self, node: Node, canvas: "NodeCanvas") -> None:
        super().__init__()
        self.node, self.canvas = node, canvas
        self.status = "idle"
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(*node.pos)
        self.setZValue(2)

    def height(self) -> float:
        return HEAD + 26 + max(len(self.node.spec.inputs), len(self.node.spec.outputs), 1) * ROW + 22

    def boundingRect(self) -> QRectF:                       # noqa: N802
        return QRectF(-PORT_R, 0, W + 2 * PORT_R, self.height())

    def paint(self, p: QPainter, _opt, _w=None) -> None:    # noqa: N802
        tk = current_tokens()
        acc = tk.accent(self.node.spec.accent)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(0, 0, W, self.height())
        sel = self.isSelected()
        p.setBrush(QColor(tk.card))
        p.setPen(QPen(QColor(tk.primary if sel else tk.border), 2 if sel else 1))
        p.drawRoundedRect(body, tk.radius_card, tk.radius_card)
        head = QRectF(0, 0, W, HEAD)
        p.setBrush(QColor(acc.base))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(head, tk.radius_card, tk.radius_card)
        p.setPen(QColor(tk.bg if tk.is_legend else acc.ink))
        p.setFont(fonts.qfont(tk.font_ui, tk.ui_px, 700))
        p.drawText(head.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, f"{self.node.title}")
        p.setFont(fonts.qfont(tk.font_mono, tk.ui_px * 0.8))
        p.setPen(QColor(tk.text2))
        p.drawText(QRectF(8, HEAD + 2, W - 16, 14), Qt.AlignmentFlag.AlignVCenter, self.node.id)
        for i, port in enumerate(self.node.spec.inputs):
            c = port_pos(QPointF(0, 0), i, False)
            p.setBrush(QColor(TYPE_COLOR.get(port.type, "#999")) if port.name in self.node.inputs else QColor(tk.card))
            p.setPen(QPen(QColor(TYPE_COLOR.get(port.type, "#999")), 2))
            p.drawEllipse(c, PORT_R, PORT_R)
            p.setPen(QColor(tk.text))
            p.drawText(QRectF(c.x() + 10, c.y() - 8, W / 2, 16), Qt.AlignmentFlag.AlignVCenter, port.name + ("" if port.required else "?"))
        for i, port in enumerate(self.node.spec.outputs):
            c = port_pos(QPointF(0, 0), i, True)
            p.setBrush(QColor(TYPE_COLOR.get(port.type, "#999")))
            p.setPen(QPen(QColor(TYPE_COLOR.get(port.type, "#999")), 2))
            p.drawEllipse(c, PORT_R, PORT_R)
            p.setPen(QColor(tk.text))
            p.drawText(QRectF(c.x() - W / 2 - 10, c.y() - 8, W / 2, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, port.name)
        p.setBrush(QColor(STATUS_COLOR.get(self.status, "#6B7280")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(12, self.height() - 11), 4, 4)
        p.setPen(QColor(tk.text2))
        p.drawText(QRectF(22, self.height() - 20, W - 30, 18), Qt.AlignmentFlag.AlignVCenter, self.status.upper())

    def itemChange(self, change, value):                    # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.canvas.item_moved(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, e) -> None:                   # noqa: N802
        local = e.pos()
        for i, _p in enumerate(self.node.spec.outputs):
            c = port_pos(QPointF(0, 0), i, True)
            if (local - c).manhattanLength() < PORT_R * 2.2:
                self.canvas.begin_connect(self.node.id, self.node.spec.outputs[i].name, self.mapToScene(c))
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:                 # noqa: N802
        super().mouseReleaseEvent(e)
        self.canvas.moveFinished.emit(self.node.id, self.pos().x(), self.pos().y())


class NodeCanvas(QGraphicsView):
    nodeSelected = Signal(str)
    connectRequested = Signal(str, str, str, str)
    moveFinished = Signal(str, float, float)
    deleteRequested = Signal(str)
    addRequested = Signal(str, float, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setMinimumSize(420, 260)
        self.items_by_id: Dict[str, NodeItem] = {}
        self.edges: List[QGraphicsPathItem] = []
        self._drag_from: Optional[Tuple[str, str]] = None
        self._temp: Optional[QGraphicsPathItem] = None
        self.graph: Optional[Graph] = None
        self.scene_.selectionChanged.connect(self._sel)

    # -- model ↔ görünüm --------------------------------------------------------------------------------------------
    def set_graph(self, graph: Graph, statuses: Optional[Dict[str, str]] = None) -> None:
        self.graph = graph
        keep = {i for i in self.items_by_id if i in graph.nodes}
        for nid in list(self.items_by_id):
            if nid not in keep:
                self.scene_.removeItem(self.items_by_id.pop(nid))
        for node in graph.nodes.values():
            it = self.items_by_id.get(node.id)
            if it is None:
                it = NodeItem(node, self)
                self.items_by_id[node.id] = it
                self.scene_.addItem(it)
            it.node = node
            it.status = (statuses or {}).get(node.id, it.status if statuses is None else "idle")
            it.update()
        self._rebuild_edges()
        self.scene_.setSceneRect(self.scene_.itemsBoundingRect().adjusted(-80, -80, 80, 80))

    def set_status(self, node_id: str, status: str) -> None:
        if node_id in self.items_by_id:
            self.items_by_id[node_id].status = status
            self.items_by_id[node_id].update()

    def _rebuild_edges(self) -> None:
        for e in self.edges:
            self.scene_.removeItem(e)
        self.edges = []
        if self.graph is None:
            return
        for src, sp, dst, dp in self.graph.connections:
            a, b = self.items_by_id.get(src), self.items_by_id.get(dst)
            if a is None or b is None:
                continue
            si = [p.name for p in a.node.spec.outputs].index(sp)
            di = [p.name for p in b.node.spec.inputs].index(dp)
            typ = a.node.spec.outputs[si].type
            path = QGraphicsPathItem()
            path.setPen(QPen(QColor(TYPE_COLOR.get(typ, "#999")), 2))
            path.setZValue(1)
            path.setData(0, (src, sp, dst, dp))
            self.scene_.addItem(path)
            self.edges.append(path)
        self._route_edges()

    def _route_edges(self) -> None:
        for e in self.edges:
            src, sp, dst, dp = e.data(0)
            a, b = self.items_by_id.get(src), self.items_by_id.get(dst)
            if a is None or b is None:
                continue
            si = [p.name for p in a.node.spec.outputs].index(sp)
            di = [p.name for p in b.node.spec.inputs].index(dp)
            e.setPath(self._curve(a.mapToScene(port_pos(QPointF(0, 0), si, True)), b.mapToScene(port_pos(QPointF(0, 0), di, False))))

    @staticmethod
    def _curve(p1: QPointF, p2: QPointF) -> QPainterPath:
        path = QPainterPath(p1)
        dx = max(40.0, abs(p2.x() - p1.x()) * 0.5)
        path.cubicTo(QPointF(p1.x() + dx, p1.y()), QPointF(p2.x() - dx, p2.y()), p2)
        return path

    def item_moved(self, _item: NodeItem) -> None:
        self._route_edges()

    # -- bağlantı sürükleme ------------------------------------------------------------------------------------------
    def begin_connect(self, node_id: str, port: str, scene_pos: QPointF) -> None:
        self._drag_from = (node_id, port)
        self._temp = QGraphicsPathItem()
        self._temp.setPen(QPen(QColor("#F5A04A"), 2, Qt.PenStyle.DashLine))
        self.scene_.addItem(self._temp)
        self._temp.setPath(self._curve(scene_pos, scene_pos))

    def finish_connect(self, scene_pos: QPointF) -> bool:
        src, self._drag_from = self._drag_from, None
        if self._temp is not None:
            self.scene_.removeItem(self._temp)
            self._temp = None
        if src is None:
            return False
        for nid, it in self.items_by_id.items():
            for i, port in enumerate(it.node.spec.inputs):
                if (it.mapToScene(port_pos(QPointF(0, 0), i, False)) - scene_pos).manhattanLength() < PORT_R * 3:
                    self.connectRequested.emit(src[0], src[1], nid, port.name)
                    return True
        return False

    def mouseMoveEvent(self, e) -> None:                    # noqa: N802
        if self._drag_from is not None and self._temp is not None:
            pos = self.mapToScene(e.position().toPoint())
            it = self.items_by_id[self._drag_from[0]]
            names = [p.name for p in it.node.spec.outputs]
            start = it.mapToScene(port_pos(QPointF(0, 0), names.index(self._drag_from[1]), True))
            self._temp.setPath(self._curve(start, pos))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:                 # noqa: N802
        if self._drag_from is not None:
            self.finish_connect(self.mapToScene(e.position().toPoint()))
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:                     # noqa: N802
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            for it in self.scene_.selectedItems():
                if isinstance(it, NodeItem):
                    self.deleteRequested.emit(it.node.id)
            return
        if e.key() == Qt.Key.Key_Tab:                       # "+ Düğüm (Tab)"
            self.context_menu(self.viewport().mapFromGlobal(self.cursor().pos()))
            return
        super().keyPressEvent(e)

    def context_menu(self, pos) -> None:
        menu = QMenu(self)
        scene_pos = self.mapToScene(pos)
        for t, spec in SPECS.items():
            menu.addAction(spec.title, lambda t=t: self.addRequested.emit(t, scene_pos.x(), scene_pos.y()))
        menu.exec(self.mapToGlobal(pos))

    def contextMenuEvent(self, e) -> None:                  # noqa: N802
        if self.itemAt(e.pos()) is None:
            self.context_menu(e.pos())

    def _sel(self) -> None:
        sel = [i for i in self.scene_.selectedItems() if isinstance(i, NodeItem)]
        if sel:
            self.nodeSelected.emit(sel[0].node.id)

    def select(self, node_id: str) -> None:
        self.scene_.clearSelection()
        if node_id in self.items_by_id:
            self.items_by_id[node_id].setSelected(True)
