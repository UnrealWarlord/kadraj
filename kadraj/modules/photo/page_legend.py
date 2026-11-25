"""Legend "Görsel Üret": üç sütun — sol: fikir + 8 alanlı istem kurucu (kritik alanlar işaretli) + sohbet; orta: hedef model + **uyarlanmış istem
önizleme** (Midjourney: kopyala · API'li: doğrudan üret · A/B) + galeri; sağ: sekmeler — Geçmiş/Favoriler, Stil + negatif kütüphanesi, Araçlar
(2×/4× büyüt, fırçalı inpainting, stil aktarımı, YouTube kapağı)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from ...ai_engine.prompt_adapter import ASPECTS, CRITICAL_SLOTS, MODELS
from ...core import i18n
from ...ui.widgets.squishy_button import SquishyButton
from .page_base import PhotoPageBase
from .service import SLOT_FIELDS
from .widgets import MaskPainter, VariationGrid


def _panel(title: str) -> tuple:
    f = QFrame()
    f.setProperty("role", "panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(8)
    cap = QLabel(title.upper())
    cap.setProperty("role", "caps")
    lay.addWidget(cap)
    return f, lay


class PhotoLegendPage(PhotoPageBase):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(ctx, parent)
        self.setObjectName("photo_legend")
        self._sync = False
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(self.ai_panel)
        root.addLayout(self.error_slot)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)
        split.addWidget(self._build_left())
        split.addWidget(self._build_middle())
        split.addWidget(self._build_right())
        split.setSizes([360, 620, 420])
        foot = QHBoxLayout()
        foot.addWidget(self.share_label)
        foot.addStretch(1)
        foot.addWidget(self.busy_label)
        root.addLayout(foot)
        self.connect_service()
        self.svc.specChanged.connect(self._load_spec)
        self.svc.chatChanged.connect(self._load_chat)
        self.svc.historyChanged.connect(self.refresh_history)
        self.svc.toolDone.connect(self._on_tool)
        self._load_spec()
        self._load_chat()
        self.refresh_history()
        self.refresh_gallery()
        self.refresh_share()

    # -- sol -------------------------------------------------------------------------------------------
    def _build_left(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        p, pl = _panel(i18n.t("photo.legend.idea"))
        self.idea = QPlainTextEdit()
        self.idea.setPlaceholderText(i18n.t("photo.simple.example"))
        self.idea.setFixedHeight(70)
        row = QHBoxLayout()
        self.parse_btn = SquishyButton(i18n.t("photo.legend.parse"), variant="primary")
        self.image_btn = SquishyButton(i18n.t("photo.legend.from_image"), variant="secondary", icon="image")
        row.addWidget(self.parse_btn)
        row.addWidget(self.image_btn)
        pl.addWidget(self.idea)
        pl.addLayout(row)
        lay.addWidget(p)
        p2, sl = _panel(i18n.t("photo.legend.slots"))
        form = QFormLayout()
        self.slot_edits: Dict[str, Any] = {}
        for slot in SLOT_FIELDS:
            label = i18n.t(f"photo.slot.{slot}") + (" *" if slot in CRITICAL_SLOTS else "")
            if slot == "aspect":
                w: Any = QComboBox()
                w.addItem("", "")
                for a in ASPECTS:
                    w.addItem(a, a)
                w.activated.connect(lambda _i, s=slot, c=w: self._slot(s, str(c.currentData() or "")))
            elif slot == "style":
                w = QComboBox()
                w.setEditable(True)
                w.addItem("", "")
                for sid, name in self.svc.style_choices():
                    w.addItem(name, sid)
                w.activated.connect(lambda _i, c=w: self._slot("style", str(c.currentData() or c.currentText())))
                w.lineEdit().editingFinished.connect(lambda c=w: self._slot("style", c.currentText()) if c.currentData() is None or c.currentText() else None)
            else:
                w = QLineEdit()
                w.editingFinished.connect(lambda s=slot, c=w: self._slot(s, c.text()))
            self.slot_edits[slot] = w
            form.addRow(label, w)
        sl.addLayout(form)
        lay.addWidget(p2)
        p3, cl = _panel(i18n.t("photo.legend.chat"))
        self.chat_view = QPlainTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setMaximumHeight(120)
        row2 = QHBoxLayout()
        self.answer = QLineEdit()
        self.answer.setPlaceholderText(i18n.t("photo.simple.answer"))
        self.send_btn = QPushButton(i18n.t("photo.simple.send"))
        self.skip_btn = QPushButton(i18n.t("photo.simple.skip"))
        for w in (self.answer, self.send_btn, self.skip_btn):
            row2.addWidget(w)
        cl.addWidget(self.chat_view)
        cl.addLayout(row2)
        lay.addWidget(p3)
        lay.addStretch(1)
        self.parse_btn.clicked.connect(self.parse_idea)
        self.image_btn.clicked.connect(self.from_image)
        self.send_btn.clicked.connect(self.send_answer)
        self.answer.returnPressed.connect(self.send_answer)
        self.skip_btn.clicked.connect(self.svc.skip_question)
        return box

    # -- orta ------------------------------------------------------------------------------------------
    def _build_middle(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        p, pl = _panel(i18n.t("photo.legend.prompt"))
        row = QHBoxLayout()
        self.model_box = QComboBox()
        for m in MODELS:
            self.model_box.addItem(i18n.t(f"photo.model.{m}"), m)
        self.model_box.setCurrentIndex(max(0, self.model_box.findData(self.svc.model)))
        self.model_box.activated.connect(lambda _i: self.svc.set_model(str(self.model_box.currentData())))
        self.action_label = QLabel("")
        self.action_label.setProperty("role", "mono")
        row.addWidget(QLabel(i18n.t("photo.legend.model")))
        row.addWidget(self.model_box)
        row.addWidget(self.action_label, 1)
        pl.addLayout(row)
        self.prompt_text = QPlainTextEdit()
        self.prompt_text.setReadOnly(True)
        self.prompt_text.setFixedHeight(96)
        self.negative_text = QLabel("")
        self.negative_text.setWordWrap(True)
        self.negative_text.setProperty("role", "hint")
        self.params_label = QLabel("")
        self.params_label.setProperty("role", "mono")
        pl.addWidget(self.prompt_text)
        pl.addWidget(self.negative_text)
        pl.addWidget(self.params_label)
        row2 = QHBoxLayout()
        self.copy_btn = SquishyButton(i18n.t("photo.copy"), variant="secondary")
        self.gen_btn = SquishyButton(i18n.t("photo.generate"), variant="primary", icon="check", accent="butter")
        self.count = QSpinBox()
        self.count.setRange(1, 4)
        self.count.setValue(1)
        self.ab_btn = SquishyButton(i18n.t("photo.ab"), variant="secondary")
        for w in (self.copy_btn, self.gen_btn, QLabel("×"), self.count, self.ab_btn):
            row2.addWidget(w)
        row2.addStretch(1)
        pl.addLayout(row2)
        lay.addWidget(p)
        p2, gl = _panel(i18n.t("photo.legend.gallery"))
        self.gallery = VariationGrid(simple=False, columns=2)
        self.bind_grid(self.gallery)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(self.gallery)
        area.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }")
        gl.addWidget(area)
        lay.addWidget(p2, 1)
        self.copy_btn.clicked.connect(self.copy_prompt)
        self.gen_btn.clicked.connect(self.generate)
        self.ab_btn.clicked.connect(lambda: self.svc.generate_ab())
        return box

    # -- sağ -------------------------------------------------------------------------------------------
    def _build_right(self) -> QWidget:
        self.tabs = QTabWidget()
        # geçmiş
        hist = QWidget()
        hl = QVBoxLayout(hist)
        self.fav_only = QPushButton(i18n.t("photo.legend.fav_only"))
        self.fav_only.setCheckable(True)
        self.history_list = QListWidget()
        self.reuse_btn = QPushButton(i18n.t("photo.legend.reuse"))
        self.fav_btn = QPushButton(i18n.t("photo.legend.toggle_fav"))
        hl.addWidget(self.fav_only)
        hl.addWidget(self.history_list, 1)
        r = QHBoxLayout()
        r.addWidget(self.reuse_btn)
        r.addWidget(self.fav_btn)
        hl.addLayout(r)
        self.tabs.addTab(hist, i18n.t("photo.legend.tab.history"))
        self.fav_only.toggled.connect(lambda _c: self.refresh_history())
        self.reuse_btn.clicked.connect(self._reuse)
        self.fav_btn.clicked.connect(self._toggle_hist_fav)
        # kütüphane
        lib = QWidget()
        ll = QVBoxLayout(lib)
        ll.addWidget(QLabel(i18n.t("photo.legend.styles")))
        self.style_list = QListWidget()
        for sid, name in self.svc.style_choices():
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, sid)
            self.style_list.addItem(it)
        self.new_style_name, self.new_style_text = QLineEdit(), QLineEdit()
        self.new_style_name.setPlaceholderText(i18n.t("photo.legend.style_name"))
        self.new_style_text.setPlaceholderText(i18n.t("photo.legend.style_text"))
        self.add_style_btn = QPushButton(i18n.t("photo.legend.add_style"))
        ll.addWidget(self.style_list, 1)
        for w in (self.new_style_name, self.new_style_text, self.add_style_btn):
            ll.addWidget(w)
        ll.addWidget(QLabel(i18n.t("photo.legend.negatives")))
        self.neg_box = QComboBox()
        for k in self.svc.negative_presets():
            self.neg_box.addItem(k, k)
        self.neg_btn = QPushButton(i18n.t("photo.legend.apply_neg"))
        ll.addWidget(self.neg_box)
        ll.addWidget(self.neg_btn)
        self.tabs.addTab(lib, i18n.t("photo.legend.tab.library"))
        self.style_list.itemDoubleClicked.connect(lambda it: self.svc.set_slot("style", str(it.data(Qt.ItemDataRole.UserRole))))
        self.add_style_btn.clicked.connect(self._add_style)
        self.neg_btn.clicked.connect(lambda: self.svc.apply_negative_preset(str(self.neg_box.currentData())))
        # araçlar
        tools = QWidget()
        tl = QVBoxLayout(tools)
        self.tool_path = QLineEdit()
        self.tool_path.setPlaceholderText(i18n.t("photo.legend.tool_path"))
        self.pick_btn = QPushButton(i18n.t("photo.pick_image"))
        tl.addWidget(self.tool_path)
        tl.addWidget(self.pick_btn)
        up = QHBoxLayout()
        self.up2_btn, self.up4_btn = QPushButton(i18n.t("photo.up2")), QPushButton(i18n.t("photo.up4"))
        up.addWidget(self.up2_btn)
        up.addWidget(self.up4_btn)
        tl.addLayout(up)
        self.mask = MaskPainter()
        self.brush = QSlider(Qt.Orientation.Horizontal)
        self.brush.setRange(6, 160)
        self.brush.setValue(36)
        self.inpaint_prompt = QLineEdit()
        self.inpaint_prompt.setPlaceholderText(i18n.t("photo.legend.inpaint_prompt"))
        mrow = QHBoxLayout()
        self.mask_undo, self.mask_clear = QPushButton(i18n.t("photo.mask.undo")), QPushButton(i18n.t("photo.mask.clear"))
        self.inpaint_btn = SquishyButton(i18n.t("photo.legend.inpaint"), variant="primary")
        for w in (self.mask_undo, self.mask_clear, self.inpaint_btn):
            mrow.addWidget(w)
        tl.addWidget(QLabel(i18n.t("photo.legend.brush")))
        tl.addWidget(self.brush)
        tl.addWidget(self.mask, 1)
        tl.addWidget(self.inpaint_prompt)
        tl.addLayout(mrow)
        tl.addWidget(QLabel(i18n.t("photo.legend.style_transfer")))
        srow = QHBoxLayout()
        self.style_ref = QLineEdit()
        self.style_ref.setPlaceholderText(i18n.t("photo.legend.style_ref"))
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0.1, 1.0)
        self.strength.setSingleStep(0.1)
        self.strength.setValue(0.7)
        self.transfer_btn = QPushButton(i18n.t("photo.legend.transfer"))
        for w in (self.style_ref, self.strength, self.transfer_btn):
            srow.addWidget(w)
        tl.addLayout(srow)
        tl.addWidget(QLabel(i18n.t("photo.legend.cover")))
        self.cover_title, self.cover_sub = QLineEdit(), QLineEdit()
        self.cover_title.setPlaceholderText(i18n.t("photo.legend.cover_title"))
        self.cover_sub.setPlaceholderText(i18n.t("photo.legend.cover_sub"))
        crow = QHBoxLayout()
        self.cover_btn = QPushButton(i18n.t("photo.legend.cover_make"))
        self.auto_cover_btn = QPushButton(i18n.t("photo.legend.cover_auto"))
        crow.addWidget(self.cover_btn)
        crow.addWidget(self.auto_cover_btn)
        for w in (self.cover_title, self.cover_sub):
            tl.addWidget(w)
        tl.addLayout(crow)
        self.tool_status = QLabel("")
        self.tool_status.setProperty("role", "hint")
        tl.addWidget(self.tool_status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(tools)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }")
        self.tabs.addTab(scroll, i18n.t("photo.legend.tab.tools"))
        self.pick_btn.clicked.connect(self._pick)
        self.tool_path.editingFinished.connect(lambda: self.mask.set_image(self.tool_path.text()))
        self.brush.valueChanged.connect(lambda v: setattr(self.mask, "brush", float(v)))
        self.mask_undo.clicked.connect(self.mask.undo)
        self.mask_clear.clicked.connect(self.mask.clear)
        self.up2_btn.clicked.connect(lambda: self._tool_upscale(2))
        self.up4_btn.clicked.connect(lambda: self._tool_upscale(4))
        self.inpaint_btn.clicked.connect(self.run_inpaint)
        self.transfer_btn.clicked.connect(self.run_transfer)
        self.cover_btn.clicked.connect(self.run_cover)
        self.auto_cover_btn.clicked.connect(lambda: self.svc.auto_cover(self.cover_title.text(), self.cover_sub.text()))
        self.gallery.selected.connect(self._select_variation)
        return self.tabs

    # -- eylemler ----------------------------------------------------------------------------------------
    def parse_idea(self) -> None:
        text = self.idea.toPlainText().strip()
        if text:
            self.clear_error()
            self.svc.submit_idea(text)

    def from_image(self) -> None:
        p = self.pick_image()
        if p:
            self.clear_error()
            self.svc.describe_image(p)

    def send_answer(self) -> None:
        t = self.answer.text().strip()
        if t:
            self.answer.clear()
            self.svc.answer(t)

    def _slot(self, slot: str, value: str) -> None:
        if self._sync:
            return
        try:
            self.svc.set_slot(slot, value)
        except Exception as exc:                      # PromptError → kart
            self.svc._fail(exc)

    def generate(self) -> None:
        model = str(self.model_box.currentData())
        self.svc.set_model(model)
        self.svc.generate(model, self.count.value())

    def _load_spec(self) -> None:
        self._sync = True
        spec = self.svc.spec
        for slot, w in self.slot_edits.items():
            val = ", ".join(spec.negatives) if slot == "negatives" else getattr(spec, slot)
            if isinstance(w, QComboBox):
                idx = w.findData(val)
                if idx >= 0:
                    w.setCurrentIndex(idx)
                elif w.isEditable():
                    w.setEditText(val)
                else:
                    w.setCurrentIndex(0)
            else:
                w.setText(val)
            missing = slot in {m.name for m in self.svc.missing()}
            w.setProperty("missing", missing)
        self.model_box.setCurrentIndex(max(0, self.model_box.findData(self.svc.model)))
        self.action_label.setText(i18n.t("photo.action.copy") if self.svc.action_for(self.svc.model) == "copy" else i18n.t("photo.action.generate"))
        self.gen_btn.setVisible(self.svc.action_for(self.svc.model) == "generate")
        self.ab_btn.setVisible(self.svc.action_for(self.svc.model) == "generate")
        if spec.subject:
            try:
                ad = self.svc.preview(self.svc.model)
                self.prompt_text.setPlainText(ad.text)
                self.negative_text.setText((i18n.t("photo.negative") + ": " + ad.negative) if ad.negative else "")
                self.params_label.setText("  ".join(f"{k}={v}" for k, v in ad.params.items()))
            except Exception as exc:
                self.svc._fail(exc)
        else:
            self.prompt_text.clear()
            self.negative_text.clear()
            self.params_label.clear()
        self._sync = False

    def _load_chat(self) -> None:
        self.chat_view.setPlainText("\n".join(("» " if t.role == "user" else "? ") + t.text for t in self.svc.chat))

    def refresh_history(self) -> None:
        self.history_list.clear()
        for row in self.svc.history(self.fav_only.isChecked()):
            it = QListWidgetItem(f"{'♥' if row['favorite'] else '♡'}  [{row['model']}]  {row['idea'][:60] or row['prompt'][:60]}")
            it.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.history_list.addItem(it)

    def _reuse(self) -> None:
        it = self.history_list.currentItem()
        if it is not None:
            self.svc.reuse(int(it.data(Qt.ItemDataRole.UserRole)))

    def _toggle_hist_fav(self) -> None:
        it = self.history_list.currentItem()
        if it is not None:
            pid = int(it.data(Qt.ItemDataRole.UserRole))
            cur = next((r for r in self.svc.history() if r["id"] == pid), None)
            self.svc.set_history_favorite(pid, not (cur and cur["favorite"]))

    def _add_style(self) -> None:
        self.svc.add_custom_style(self.new_style_name.text(), self.new_style_text.text())
        self.style_list.clear()
        for sid, name in self.svc.style_choices():
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, sid)
            self.style_list.addItem(it)

    def _pick(self) -> None:
        p = self.pick_image()
        if p:
            self.set_tool_image(p)

    def set_tool_image(self, path: str) -> None:
        self.tool_path.setText(path)
        self.mask.set_image(path)

    def _select_variation(self, vid: int) -> None:
        v = next((x for x in self.svc.variations if x.id == vid), None)
        if v is not None:
            self.set_tool_image(v.path)

    def _tool_upscale(self, factor: int) -> None:
        if self.tool_path.text():
            self.svc.upscale(self.tool_path.text(), factor)

    def run_inpaint(self) -> None:
        if self.tool_path.text() and self.mask.has_mask:
            self.svc.inpaint(self.tool_path.text(), self.mask.mask_png(), self.inpaint_prompt.text() or self.svc.spec.subject)

    def run_transfer(self) -> None:
        if self.tool_path.text() and self.style_ref.text():
            self.svc.style_transfer(self.tool_path.text(), self.style_ref.text(), self.strength.value())

    def run_cover(self) -> None:
        if self.tool_path.text():
            self.svc.make_cover(self.tool_path.text(), self.cover_title.text(), self.cover_sub.text())

    def _on_tool(self, tool: str, path: str) -> None:
        self.tool_status.setText(i18n.t("photo.tool_done", path=path))
