"""Düzenleyici görsel bileşenleri (hepsi tokenlardan boyanır; durum tutmaz — durum ``EditorService``'tedir).

``TimelineView`` (çok kanallı zaman çizelgesi: cetvel, klipler, işaretçiler, oynatma başlığı, manyetik yapışma, "Yapıştı!"),
``PreviewView`` (kare + önce/sonra sürgüsü), ``ScopeView`` (histogram / RGB parade / vektörskop), ``EqView`` (10 bant),
``BezierEditor``, ``CurveView`` (hız eğrisi).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core import i18n
from ...ui import adaptive, fonts
from ...ui.theme.manager import ThemedMixin
from ...ui.theme.tokens import mix
from . import audio, scopes, snapping
from .timeline import EQ_BANDS, Clip, Timeline, Track, format_clock, format_timecode

KIND_ACCENT = {"video": "mint", "audio": "lavender", "adj": "butter", "fx": "butter", "sub": "pink"}
EDGE_PX = 7


def to_qimage(img: np.ndarray) -> QImage:
    a = np.ascontiguousarray(img)
    h, w = a.shape[:2]
    return QImage(a.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def _col(c: str, a: float = 1.0) -> QColor:
    q = QColor(c)
    q.setAlphaF(a)
    return q


class TimelineView(ThemedMixin, QWidget):
    """Çok kanallı zaman çizelgesi. ``pro=False`` (Basit): büyük satırlar, ``M:SS`` cetvel, sade etiketler;
    ``pro=True`` (Legend): ``HH:MM:SS:FF`` cetvel, kanal kimlikleri, kilit/sessiz göstergeleri, anahtar kare işaretleri."""

    seekRequested = Signal(float)
    selectionRequested = Signal(list)
    zoomChanged = Signal(float)

    def __init__(self, svc: Any, *, pro: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        adaptive.direct_touch(self)                 # dokunmatik: sürükleme sayfayı kaydırmaz
        self.svc, self.pro = svc, pro
        self.px_per_s = 60.0 if pro else 44.0
        self._drag: Optional[Dict[str, Any]] = None
        self._scrub = False
        self._snap_text = ""
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.timeout.connect(self._clear_snap)
        self._rects: List[Tuple[QRectF, Track, Clip]] = []
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.setAccessibleName(i18n.t("editor.tl.title"))
        svc.timelineChanged.connect(lambda _r: self._relayout())
        svc.selectionChanged.connect(self.update)
        svc.playheadChanged.connect(lambda _t: self.update())
        svc.snapped.connect(self.flash_snap)
        self._bind_theme()
        self._relayout()

    # -- ölçüler -------------------------------------------------------------------------------------------------
    @property
    def header_w(self) -> int:
        return self.tk.px(150 if self.pro else 92)

    @property
    def row_h(self) -> int:
        return self.tk.px(40 if self.pro else 54)

    @property
    def ruler_h(self) -> int:
        return self.tk.px(26 if self.pro else 28)

    def time_to_x(self, t: float) -> float:
        return self.header_w + t * self.px_per_s

    def x_to_time(self, x: float) -> float:
        return max(0.0, (x - self.header_w) / self.px_per_s)

    def track_y(self, index: int) -> int:
        return self.ruler_h + index * self.row_h

    def _relayout(self) -> None:
        dur = max(self.svc.tl.duration + 8.0, 20.0)
        self.setMinimumSize(int(self.header_w + dur * self.px_per_s), self.ruler_h + max(1, len(self.svc.tl.tracks)) * self.row_h + 6)
        self.update()

    def _on_theme(self) -> None:
        self._relayout()

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    # -- yakınlaştırma ---------------------------------------------------------------------------------------------
    def set_zoom(self, px_per_s: float) -> None:
        self.px_per_s = max(4.0, min(600.0, px_per_s))
        self._relayout()
        self.zoomChanged.emit(self.px_per_s)

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self.px_per_s * factor)

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
        else:
            e.ignore()

    # -- yapışma balonu --------------------------------------------------------------------------------------------
    def flash_snap(self, target: str) -> None:
        self._snap_text = i18n.t("editor.tl.snapped")
        self._snap_timer.start(900)
        self.update()

    def _clear_snap(self) -> None:
        self._snap_text = ""
        self.update()

    @property
    def snap_text(self) -> str:
        return self._snap_text

    # -- boyama ------------------------------------------------------------------------------------------------------
    def _clip_colors(self, tr: Track, c: Clip, selected: bool) -> Tuple[QColor, QColor, QColor]:
        tk = self.tk
        acc = tk.accent(KIND_ACCENT.get(tr.kind, "neutral"))
        if tk.is_legend:
            fill = _col(mix(tk.card, acc.base, 0.42 if selected else 0.28))
            edge = _col(acc.base)
            ink = _col(tk.text)
        else:
            fill = _col(acc.base if selected else acc.tone)
            edge = _col(acc.base if not selected else acc.ink)
            ink = _col(acc.ink)
        if not c.enabled or tr.muted:
            fill.setAlphaF(0.45)
        return fill, edge, ink

    def paintEvent(self, _e) -> None:
        tk = self.tk
        tl: Timeline = self.svc.tl
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _col(tk.surface2 if not tk.is_legend else tk.bg))
        self._compute_rects()
        font = fonts.qfont(tk.font_ui, tk.ui_px * 0.92, 600)
        mono = fonts.qfont(tk.font_mono, tk.ui_px * 0.8, 500)
        # kanallar
        for i, tr in enumerate(tl.tracks):
            y = self.track_y(i)
            band = _col(tk.card if i % 2 == 0 else tk.surface2, 1.0)
            p.fillRect(QRectF(0, y, self.width(), self.row_h), band)
            p.setPen(_col(tk.border))
            p.drawLine(0, y + self.row_h - 1, self.width(), y + self.row_h - 1)
            p.setPen(_col(tk.text))
            p.setFont(font)
            label = tr.name or tr.id
            if self.pro:
                p.setFont(mono)
                p.setPen(_col(tk.accent(KIND_ACCENT.get(tr.kind, "neutral")).base))
                p.drawText(QRectF(8, y, 34, self.row_h), Qt.AlignmentFlag.AlignVCenter, tr.id)
                p.setFont(font)
                p.setPen(_col(tk.text))
                p.drawText(QRectF(44, y, self.header_w - 70, self.row_h), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, label)
                flags = ("M" if tr.muted else "") + ("S" if tr.solo else "") + ("L" if tr.locked else "")
                p.setPen(_col(tk.warn))
                p.setFont(mono)
                p.drawText(QRectF(0, y, self.header_w - 6, self.row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, flags)
            else:
                p.drawText(QRectF(10, y, self.header_w - 14, self.row_h), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, label)
            for c in tr.sorted_clips():
                self._paint_clip(p, i, tr, c, font)
        # cetvel
        self._paint_ruler(p, mono)
        # işaretçiler
        for m in tl.markers:
            x = self.time_to_x(m.t)
            col = _col(tk.primary if m.chapter else tk.info)
            p.setPen(QPen(col, 1))
            p.drawLine(QPointF(x, self.ruler_h - 8), QPointF(x, self.height()))
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(x - 5, 2), QPointF(x + 5, 2), QPointF(x + 5, 10), QPointF(x, 15), QPointF(x - 5, 10)]))
            p.fillPath(path, col if m.chapter else _col(tk.info, 0.7))
            if m.label:
                p.setPen(_col(tk.text))
                p.setFont(mono)
                p.drawText(QPointF(x + 8, 12), m.label)
        # sürükleme hayaleti
        if self._drag and self._drag.get("ghost"):
            r = self._drag["ghost"]
            p.setPen(QPen(_col(tk.primary), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(_col(tk.primary, 0.12))
            p.drawRoundedRect(r, tk.radius_ctl, tk.radius_ctl)
        # oynatma başlığı
        x = self.time_to_x(self.svc.playhead)
        p.setPen(QPen(_col(tk.warn if not tk.is_legend else tk.primary), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setBrush(_col(tk.warn if not tk.is_legend else tk.primary))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 10)]))
        # "Yapıştı!" balonu
        if self._snap_text:
            p.setFont(font)
            fm = QFontMetrics(font)
            w = fm.horizontalAdvance(self._snap_text) + 20
            r = QRectF(min(max(self.header_w, x - w / 2), max(self.header_w, self.width() - w - 4)), self.ruler_h + 4, w, fm.height() + 8)
            p.setBrush(_col(tk.good))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, r.height() / 2 if tk.pill else 3, r.height() / 2 if tk.pill else 3)
            p.setPen(_col(tk.on_primary if not tk.is_legend else tk.bg))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._snap_text)

    def _compute_rects(self) -> None:
        out: List[Tuple[QRectF, Track, Clip]] = []
        for i, tr in enumerate(self.svc.tl.tracks):
            y = self.track_y(i)
            for c in tr.sorted_clips():
                out.append((QRectF(self.time_to_x(c.start), y + 4, max(3.0, c.duration * self.px_per_s), self.row_h - 8), tr, c))
        self._rects = out

    def _paint_clip(self, p: QPainter, row: int, tr: Track, c: Clip, font) -> None:
        tk = self.tk
        y = self.track_y(row)
        r = QRectF(self.time_to_x(c.start), y + 4, max(3.0, c.duration * self.px_per_s), self.row_h - 8)
        sel = c.id in self.svc.selection
        fill, edge, ink = self._clip_colors(tr, c, sel)
        p.setBrush(fill)
        p.setPen(QPen(edge, 2 if sel else 1))
        p.drawRoundedRect(r, tk.radius_ctl + 2 if not tk.is_legend else 2, tk.radius_ctl + 2 if not tk.is_legend else 2)
        p.save()
        p.setClipRect(r.adjusted(4, 0, -4, 0))
        p.setPen(ink)
        p.setFont(font)
        name = c.name or (c.src.rsplit("/", 1)[-1] if c.src else tr.name)
        icon = "◆ " if c.keyframes and self.pro else ""
        sp = f" ×{c.speed:g}" if abs(c.speed - 1) > 1e-6 or c.speed_curve else ""
        p.drawText(r.adjusted(8, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, f"{icon}{name}{sp}")
        p.restore()
        if c.fade_in > 0 or c.fade_out > 0:
            p.setPen(QPen(ink, 1))
            if c.fade_in > 0:
                p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.left() + c.fade_in * self.px_per_s, r.top()))
            if c.fade_out > 0:
                p.drawLine(QPointF(r.right() - c.fade_out * self.px_per_s, r.top()), QPointF(r.right(), r.bottom()))

    def _paint_ruler(self, p: QPainter, mono) -> None:
        tk = self.tk
        p.fillRect(QRectF(0, 0, self.width(), self.ruler_h), _col(tk.card))
        p.setPen(_col(tk.border))
        p.drawLine(0, self.ruler_h - 1, self.width(), self.ruler_h - 1)
        steps = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
        step = next((s for s in steps if s * self.px_per_s >= 70), steps[-1])
        t, p_font = 0.0, mono
        p.setFont(p_font)
        fps = self.svc.tl.fps
        while self.time_to_x(t) < self.width():
            x = self.time_to_x(t)
            p.setPen(_col(tk.text2))
            p.drawLine(QPointF(x, self.ruler_h - 8), QPointF(x, self.ruler_h - 1))
            p.drawText(QPointF(x + 3, self.ruler_h - 11), format_timecode(t, fps) if self.pro else format_clock(t))
            t += step

    # -- fare ------------------------------------------------------------------------------------------------------------
    def clip_at(self, pos: QPointF) -> Optional[Tuple[QRectF, Track, Clip]]:
        self._compute_rects()
        for r, tr, c in reversed(self._rects):
            if r.contains(pos):
                return r, tr, c
        return None

    def row_at(self, y: float) -> int:
        return int((y - self.ruler_h) // self.row_h)

    def mousePressEvent(self, e) -> None:
        pos = e.position()
        if pos.y() < self.ruler_h:
            self._scrub = True
            self.seekRequested.emit(self.x_to_time(pos.x()))
            return
        hit = self.clip_at(pos)
        if hit is None:
            if not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.selectionRequested.emit([])
            return
        r, tr, c = hit
        sel = list(self.svc.selection)
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            sel = [s for s in sel if s != c.id] if c.id in sel else sel + [c.id]
        elif c.id not in sel:
            sel = [c.id]
        self.selectionRequested.emit(sel)
        mode = "trim_start" if pos.x() - r.left() <= EDGE_PX else "trim_end" if r.right() - pos.x() <= EDGE_PX else "move"
        self._drag = {"mode": mode, "clip": c.id, "track": tr.id, "x0": pos.x(), "start0": c.start, "end0": c.end,
                      "cur": c.start, "cur_track": tr.id, "rect": r, "ghost": None, "moved": False}

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self._scrub:
            self.seekRequested.emit(self.x_to_time(pos.x()))
            return
        d = self._drag
        if d is None:
            hit = self.clip_at(pos)
            if hit is None:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            else:
                r = hit[0]
                edge = pos.x() - r.left() <= EDGE_PX or r.right() - pos.x() <= EDGE_PX
                self.setCursor(Qt.CursorShape.SizeHorCursor if edge else Qt.CursorShape.OpenHandCursor)
            return
        dx = (pos.x() - d["x0"]) / self.px_per_s
        if abs(pos.x() - d["x0"]) > 2:
            d["moved"] = True
        found = self.svc.tl.find_clip(d["clip"])
        if found is None:
            return
        c = found[1]
        r: QRectF = d["rect"]
        thr = 10 / self.px_per_s
        if d["mode"] == "move":
            res = snapping.snap_move(self.svc.tl, c, max(0.0, d["start0"] + dx), thr, playhead=self.svc.playhead, enabled=self.svc.snap_enabled)
            d["cur"] = res.time
            row = self.row_at(pos.y())
            tracks = self.svc.tl.tracks
            if 0 <= row < len(tracks) and tracks[row].kind == found[0].kind and not tracks[row].locked:
                d["cur_track"] = tracks[row].id
            ri = next((i for i, t in enumerate(tracks) if t.id == d["cur_track"]), 0)
            d["ghost"] = QRectF(self.time_to_x(res.time), self.track_y(ri) + 4, r.width(), r.height())
            if res.snapped:
                self.flash_snap(res.target or "")
        elif d["mode"] == "trim_start":
            res = snapping.snap_time(self.svc.tl, d["start0"] + dx, thr, exclude=[c.id], playhead=self.svc.playhead, enabled=self.svc.snap_enabled)
            d["cur"] = min(res.time, d["end0"] - 0.05)
            d["ghost"] = QRectF(self.time_to_x(d["cur"]), r.top(), self.time_to_x(d["end0"]) - self.time_to_x(d["cur"]), r.height())
        else:
            res = snapping.snap_time(self.svc.tl, d["end0"] + dx, thr, exclude=[c.id], playhead=self.svc.playhead, enabled=self.svc.snap_enabled)
            d["cur"] = max(res.time, d["start0"] + 0.05)
            d["ghost"] = QRectF(r.left(), r.top(), self.time_to_x(d["cur"]) - r.left(), r.height())
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        self._scrub = False
        d, self._drag = self._drag, None
        if d and d["moved"]:
            if d["mode"] == "move":
                self.svc.move_clip(d["clip"], d["cur"], d["cur_track"] if d["cur_track"] != d["track"] else None, px_per_s=self.px_per_s, snap=False)
            elif d["mode"] == "trim_start":
                self.svc.trim_clip(d["clip"], "start", d["cur"], px_per_s=self.px_per_s)
            else:
                self.svc.trim_clip(d["clip"], "end", d["cur"], px_per_s=self.px_per_s)
        self.update()

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.svc.delete_selection(bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        elif k == Qt.Key.Key_S:
            self.svc.split_at_playhead()
        elif k == Qt.Key.Key_Left:
            self.svc.step_frames(-1)
        elif k == Qt.Key.Key_Right:
            self.svc.step_frames(1)
        elif k == Qt.Key.Key_M:
            self.svc.add_marker()
        else:
            super().keyPressEvent(e)


class PreviewView(ThemedMixin, QWidget):
    """Kare gösterici; ``compare=True`` iken soldaki "Önce", sağdaki "Sonra" arasında sürüklenebilir ayırıcı."""

    wipeChanged = Signal(float)

    def __init__(self, *, pro: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        adaptive.direct_touch(self, when=lambda: self.compare)   # yalnız karşılaştırma sürgüsü sürüklenir
        self.pro = pro
        self.after: Optional[QImage] = None
        self.before: Optional[QImage] = None
        self.compare = False
        self.wipe = 0.5
        self.overlay_text = ""
        self.label_before, self.label_after = i18n.t("editor.preview.before"), i18n.t("editor.preview.after")
        self.safe_area = False
        self.aspect = 16 / 9
        self.setMinimumSize(240, 135)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._dragging = False
        self._bind_theme()

    def set_images(self, after: "np.ndarray | QImage | None", before: "np.ndarray | QImage | None" = None) -> None:
        conv = lambda x: x if (x is None or isinstance(x, QImage)) else to_qimage(x)
        self.after, self.before = conv(after), conv(before)
        if self.after is not None and self.after.height():
            self.aspect = self.after.width() / self.after.height()
        self.update()

    def set_compare(self, on: bool) -> None:
        self.compare = bool(on)
        self.update()

    def _video_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        vw = min(w, h * self.aspect)
        vh = vw / self.aspect
        return QRectF((w - vw) / 2, (h - vh) / 2, vw, vh)

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#0A0A0C") if tk.is_legend else _col(tk.surface2))
        r = self._video_rect()
        p.fillRect(r, QColor("#000000"))
        if self.after is None:
            p.setPen(_col(tk.text2))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, i18n.t("editor.preview.empty"))
        else:
            p.drawImage(r, self.after)
            if self.compare and self.before is not None:
                xs = r.left() + r.width() * self.wipe
                p.save()
                p.setClipRect(QRectF(r.left(), r.top(), xs - r.left(), r.height()))
                p.drawImage(r, self.before)
                p.restore()
                p.setPen(QPen(_col(tk.primary), 2))
                p.drawLine(QPointF(xs, r.top()), QPointF(xs, r.bottom()))
                p.setBrush(_col(tk.primary))
                p.drawEllipse(QPointF(xs, r.center().y()), 9, 9)
                self._chip(p, self.label_before, QPointF(r.left() + 8, r.top() + 8))
                self._chip(p, self.label_after, QPointF(r.right() - 8, r.top() + 8), right=True)
        if self.safe_area:
            p.setPen(QPen(_col(tk.text, 0.5), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(r.width() * 0.05, r.height() * 0.05, -r.width() * 0.05, -r.height() * 0.05))
        if self.overlay_text:
            p.setFont(fonts.qfont(tk.font_mono, tk.ui_px, 600))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(r.adjusted(8, 0, -8, -8), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, self.overlay_text)

    def _chip(self, p: QPainter, text: str, at: QPointF, right: bool = False) -> None:
        tk = self.tk
        p.setFont(fonts.qfont(tk.font_ui, tk.ui_px * 0.9, 700))
        fm = QFontMetrics(p.font())
        w = fm.horizontalAdvance(text) + 16
        r = QRectF(at.x() - (w if right else 0), at.y(), w, fm.height() + 6)
        p.setBrush(QColor(0, 0, 0, 150))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, e) -> None:
        if self.compare:
            self._dragging = True
            self._set_wipe(e.position().x())

    def mouseMoveEvent(self, e) -> None:
        if self._dragging:
            self._set_wipe(e.position().x())

    def mouseReleaseEvent(self, _e) -> None:
        self._dragging = False

    def _set_wipe(self, x: float) -> None:
        r = self._video_rect()
        self.wipe = max(0.0, min(1.0, (x - r.left()) / max(1.0, r.width())))
        self.wipeChanged.emit(self.wipe)
        self.update()


class ScopeView(ThemedMixin, QWidget):
    """Ölçüm ekranı: ``mode`` ∈ ``hist | parade | vector``. ``set_frame`` karenin ölçümünü hesaplar (≤320 px'e küçültülmüş)."""

    MODES = ("hist", "parade", "vector")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.mode = "hist"
        self.frame: Optional[np.ndarray] = None
        self.stats: Dict[str, float] = {}
        self._data: Any = None
        self.setMinimumSize(200, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._bind_theme()

    def set_mode(self, mode: str) -> None:
        if mode in self.MODES and mode != self.mode:
            self.mode = mode
            self._compute()
            self.update()

    def set_frame(self, frame: Optional[np.ndarray]) -> None:
        self.frame = frame
        self._compute()
        self.update()

    def _compute(self) -> None:
        f = self.frame
        if f is None:
            self._data, self.stats = None, {}
            return
        self.stats = scopes.stats(f)
        self._data = {"hist": scopes.histogram, "parade": scopes.rgb_parade, "vector": scopes.vectorscope}[self.mode](f)

    @property
    def data(self) -> Any:
        return self._data

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#08090B") if tk.is_legend else _col(tk.surface2))
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        p.setPen(_col(tk.border))
        for i in range(5):                                           # ızgara
            y = r.top() + r.height() * i / 4
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        if self._data is None:
            p.setPen(_col(tk.text2))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, i18n.t("editor.scopes.empty"))
            return
        cols = {"R": (255, 90, 95), "G": (70, 209, 138), "B": (90, 150, 255), "L": (230, 230, 230)}
        if self.mode == "hist":
            h = np.asarray(self._data, dtype=np.float64)
            top = max(1.0, h[:3].max())
            for ch, key in zip(range(4), "RGBL"):
                c = QColor(*cols[key])
                c.setAlphaF(0.55 if ch < 3 else 0.35)
                path = QPainterPath(QPointF(r.left(), r.bottom()))
                for i, v in enumerate(h[ch]):
                    path.lineTo(r.left() + r.width() * i / (h.shape[1] - 1), r.bottom() - r.height() * min(1.0, v / top))
                path.lineTo(r.right(), r.bottom())
                p.fillPath(path, c)
        elif self.mode == "parade":
            par = np.asarray(self._data)
            w = r.width() / 3
            for i, key in enumerate("RGB"):
                img = scopes.to_density_rgba(par[i], cols[key], 8.0)
                qi = QImage(np.ascontiguousarray(img).data, img.shape[1], img.shape[0], img.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()
                p.drawImage(QRectF(r.left() + w * i, r.top(), w - 2, r.height()), qi)
        else:
            vs = np.asarray(self._data)
            img = scopes.to_density_rgba(vs, (120, 255, 170), 8.0)
            qi = QImage(np.ascontiguousarray(img).data, img.shape[1], img.shape[0], img.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()
            side = min(r.width(), r.height())
            box = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
            p.drawImage(box, qi)
            p.setPen(_col(tk.text2, 0.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(box.center(), side * 0.45, side * 0.45)
            p.drawLine(QPointF(box.left(), box.center().y()), QPointF(box.right(), box.center().y()))
            p.drawLine(QPointF(box.center().x(), box.top()), QPointF(box.center().x(), box.bottom()))


class EqView(ThemedMixin, QWidget):
    """10 bant ekolayzır: eğri + sürüklenebilir tutamaçlar (çift tıklama bandı sıfırlar)."""

    gainsChanged = Signal(list)          # bırakınca
    gainsPreview = Signal(list)          # sürüklerken

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        adaptive.direct_touch(self)                 # dokunmatik: sürükleme sayfayı kaydırmaz
        self.gains: List[float] = [0.0] * len(EQ_BANDS)
        self._active = -1
        self.setMinimumSize(280, 130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._bind_theme()

    def set_gains(self, gains: Sequence[float]) -> None:
        self.gains = audio.clamp_eq(gains)
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(self.rect()).adjusted(28, 8, -8, -18)

    def _x(self, i: int) -> float:
        r = self._plot()
        return r.left() + r.width() * (i + 0.5) / len(EQ_BANDS)

    def _y(self, g: float) -> float:
        r = self._plot()
        return r.center().y() - g / audio.EQ_RANGE_DB * r.height() / 2

    def handle_pos(self, i: int) -> QPointF:
        return QPointF(self._x(i), self._y(self.gains[i]))

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#08090B") if tk.is_legend else _col(tk.surface2))
        r = self._plot()
        p.setFont(fonts.qfont(tk.font_mono, tk.ui_px * 0.75))
        for db in (-12, -6, 0, 6, 12):
            y = self._y(db)
            p.setPen(_col(tk.border if db else tk.text2, 1.0 if db == 0 else 0.7))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(_col(tk.text2))
            p.drawText(QRectF(0, y - 7, 24, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{db:+d}" if db else "0")
        freqs, db = audio.eq_curve_db(self.gains, 96)
        path = QPainterPath()
        for k, (f, v) in enumerate(zip(freqs, db)):
            x = r.left() + r.width() * (math.log10(f) - math.log10(20)) / (math.log10(20000) - math.log10(20))
            y = self._y(float(v))
            path.moveTo(x, y) if k == 0 else path.lineTo(x, y)
        p.setPen(QPen(_col(tk.primary), 2))
        p.drawPath(path)
        for i, f in enumerate(EQ_BANDS):
            p.setPen(_col(tk.text2))
            p.drawText(QRectF(self._x(i) - 20, r.bottom() + 2, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{f // 1000}k" if f >= 1000 else str(f))
            pos = self.handle_pos(i)
            p.setBrush(_col(tk.primary if i == self._active else tk.card))
            p.setPen(QPen(_col(tk.primary), 2))
            p.drawEllipse(pos, 6, 6)

    def _hit(self, pos: QPointF) -> int:
        best, bd = -1, 1e9
        for i in range(len(EQ_BANDS)):
            d = abs(self._x(i) - pos.x())
            if d < bd:
                best, bd = i, d
        return best if bd < self._plot().width() / len(EQ_BANDS) / 2 else -1

    def _set_from_y(self, i: int, y: float) -> None:
        r = self._plot()
        g = (r.center().y() - y) / (r.height() / 2) * audio.EQ_RANGE_DB
        self.gains[i] = round(max(-audio.EQ_RANGE_DB, min(audio.EQ_RANGE_DB, g)) * 2) / 2
        self.gainsPreview.emit(list(self.gains))
        self.update()

    def mousePressEvent(self, e) -> None:
        self._active = self._hit(e.position())
        if self._active >= 0:
            self._set_from_y(self._active, e.position().y())

    def mouseMoveEvent(self, e) -> None:
        if self._active >= 0 and e.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_y(self._active, e.position().y())

    def mouseReleaseEvent(self, _e) -> None:
        if self._active >= 0:
            self.gainsChanged.emit(list(self.gains))
        self._active = -1
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        i = self._hit(e.position())
        if i >= 0:
            self.gains[i] = 0.0
            self.gainsChanged.emit(list(self.gains))
            self.update()


class BezierEditor(ThemedMixin, QWidget):
    """``cubic-bezier(x1,y1,x2,y2)`` iki tutamaçla düzenlenir."""

    changed = Signal(tuple)

    def __init__(self, value: Tuple[float, float, float, float] = (0.25, 0.1, 0.25, 1.0), parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        adaptive.direct_touch(self)                 # dokunmatik: sürükleme sayfayı kaydırmaz
        self.value = tuple(value)
        self._drag = -1
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._bind_theme()

    def set_value(self, v: Sequence[float]) -> None:
        self.value = tuple(float(x) for x in v)          # type: ignore[assignment]
        self.update()

    def _box(self) -> QRectF:
        s = min(self.width(), self.height()) - 24
        return QRectF((self.width() - s) / 2, 12, s, s)

    def _pt(self, i: int) -> QPointF:
        b = self._box()
        x, y = (self.value[0], self.value[1]) if i == 0 else (self.value[2], self.value[3])
        return QPointF(b.left() + x * b.width(), b.bottom() - y * b.height())

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        b = self._box()
        p.fillRect(b, _col(tk.surface2))
        p.setPen(_col(tk.border))
        p.drawRect(b)
        p0, p1, a, c = QPointF(b.left(), b.bottom()), QPointF(b.right(), b.top()), self._pt(0), self._pt(1)
        p.setPen(QPen(_col(tk.text2), 1))
        p.drawLine(p0, a)
        p.drawLine(p1, c)
        path = QPainterPath(p0)
        path.cubicTo(a, c, p1)
        p.setPen(QPen(_col(tk.primary), 2.2))
        p.drawPath(path)
        for pt in (a, c):
            p.setBrush(_col(tk.primary))
            p.setPen(QPen(_col(tk.card), 2))
            p.drawEllipse(pt, 6, 6)

    def mousePressEvent(self, e) -> None:
        pos = e.position()
        d = [math.hypot(pos.x() - self._pt(i).x(), pos.y() - self._pt(i).y()) for i in (0, 1)]
        self._drag = d.index(min(d)) if min(d) < 16 else -1

    def mouseMoveEvent(self, e) -> None:
        if self._drag < 0:
            return
        b = self._box()
        x = max(0.0, min(1.0, (e.position().x() - b.left()) / b.width()))
        y = max(-0.5, min(1.5, (b.bottom() - e.position().y()) / b.height()))
        v = list(self.value)
        v[self._drag * 2], v[self._drag * 2 + 1] = round(x, 3), round(y, 3)
        self.value = tuple(v)
        self.changed.emit(self.value)
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = -1


class CurveView(ThemedMixin, QWidget):
    """Hız eğrisi: ``(zaman, hız)`` noktaları; boş alana tıkla = ekle, sürükle = taşı, çift tıkla = sil."""

    pointsChanged = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        adaptive.direct_touch(self)                 # dokunmatik: sürükleme sayfayı kaydırmaz
        self.points: List[Tuple[float, float]] = []
        self.duration = 5.0
        self.vmax = 4.0
        self._drag = -1
        self.setMinimumSize(200, 100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._bind_theme()

    def set_points(self, pts: Sequence[Tuple[float, float]], duration: float) -> None:
        self.points, self.duration = sorted((float(t), float(v)) for t, v in pts), max(0.5, duration)
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(self.rect()).adjusted(26, 6, -8, -6)

    def _to_px(self, t: float, v: float) -> QPointF:
        r = self._plot()
        return QPointF(r.left() + t / self.duration * r.width(), r.bottom() - v / self.vmax * r.height())

    def _from_px(self, pos: QPointF) -> Tuple[float, float]:
        r = self._plot()
        return (max(0.0, min(self.duration, (pos.x() - r.left()) / r.width() * self.duration)),
                max(0.1, min(self.vmax, (r.bottom() - pos.y()) / r.height() * self.vmax)))

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _col(tk.surface2))
        r = self._plot()
        p.setFont(fonts.qfont(tk.font_mono, tk.ui_px * 0.75))
        for v in (0.5, 1, 2, 4):
            y = self._to_px(0, v).y()
            p.setPen(_col(tk.border if v != 1 else tk.text2, 0.8))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(_col(tk.text2))
            p.drawText(QRectF(0, y - 7, 24, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:g}×")
        if self.points:
            path = QPainterPath(self._to_px(0, self.points[0][1]))
            for t, v in self.points:
                path.lineTo(self._to_px(t, v))
            path.lineTo(self._to_px(self.duration, self.points[-1][1]))
            p.setPen(QPen(_col(tk.primary), 2))
            p.drawPath(path)
            for t, v in self.points:
                p.setBrush(_col(tk.primary))
                p.setPen(QPen(_col(tk.card), 2))
                p.drawEllipse(self._to_px(t, v), 5, 5)

    def _hit(self, pos: QPointF) -> int:
        for i, (t, v) in enumerate(self.points):
            q = self._to_px(t, v)
            if math.hypot(q.x() - pos.x(), q.y() - pos.y()) < 10:
                return i
        return -1

    def mousePressEvent(self, e) -> None:
        i = self._hit(e.position())
        if i < 0:
            t, v = self._from_px(e.position())
            self.points.append((round(t, 2), round(v, 2)))
            self.points.sort()
            i = self._hit(e.position())
            self.pointsChanged.emit(list(self.points))
        self._drag = i
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag >= 0 and e.buttons() & Qt.MouseButton.LeftButton:
            t, v = self._from_px(e.position())
            self.points[self._drag] = (round(t, 2), round(v, 2))
            self.update()

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag >= 0:
            self.points.sort()
            self.pointsChanged.emit(list(self.points))
        self._drag = -1

    def mouseDoubleClickEvent(self, e) -> None:
        i = self._hit(e.position())
        if i >= 0:
            del self.points[i]
            self.pointsChanged.emit(list(self.points))
            self.update()
