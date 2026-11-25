"""Basit "Fotoğraf Üret" (tuval: V2Studio · Fotoğraf Üret): "Ne hayal ediyorsun?" + örnek fikir + görsel bırak + **Hayal Et!**, eksik alanlar için
sohbet kartı, "İşte senin için 4 tane" galerisi (kalp = sakla, Büyüt 2×/4×), "Planının %X'i kullanılacak". Teknik terim yok; API'si olan
modeller yoksa istem kopyalanır (Midjourney yolu)."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ...core import i18n
from ...ui.widgets.help_bubble import HelpBubble
from ...ui.widgets.squishy_button import SquishyButton
from .page_base import PhotoPageBase
from .widgets import VariationGrid

SIMPLE_PREFERENCE = ("flux", "imagen", "dalle", "sdxl")


def _card() -> tuple:
    f = QFrame()
    f.setProperty("role", "card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    return f, lay


class PhotoSimplePage(PhotoPageBase):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(ctx, parent)
        self.setObjectName("photo_simple")
        self.want_generate = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 12)
        lay.setSpacing(14)
        lay.addLayout(self.error_slot)
        # fikir
        card, cl = _card()
        head = QHBoxLayout()
        title = QLabel(i18n.t("photo.simple.title"))
        title.setProperty("role", "h2")
        head.addWidget(title)
        head.addWidget(HelpBubble(i18n.t("photo.simple.help")))
        head.addStretch(1)
        cl.addLayout(head)
        self.idea = QPlainTextEdit()
        self.idea.setPlaceholderText(i18n.t("photo.simple.example"))
        self.idea.setFixedHeight(84)
        cl.addWidget(self.idea)
        drop = QHBoxLayout()
        self.image_btn = SquishyButton(i18n.t("photo.simple.from_image"), variant="secondary", icon="image")
        self.image_hint = QLabel(i18n.t("photo.simple.drop_hint"))
        self.image_hint.setProperty("role", "hint")
        self.image_hint.setWordWrap(True)
        drop.addWidget(self.image_btn)
        drop.addWidget(self.image_hint, 1)
        cl.addLayout(drop)
        go = QHBoxLayout()
        self.go_btn = SquishyButton(i18n.t("photo.simple.go"), variant="primary", icon="check", accent="butter")
        go.addWidget(self.go_btn)
        go.addWidget(self.share_label)
        go.addStretch(1)
        go.addWidget(self.busy_label)
        cl.addLayout(go)
        lay.addWidget(card)
        # sohbet
        self.chat_card, chl = _card()
        self.question = QLabel("")
        self.question.setWordWrap(True)
        self.question.setProperty("role", "h2")
        row = QHBoxLayout()
        self.answer = QLineEdit()
        self.answer.setPlaceholderText(i18n.t("photo.simple.answer"))
        self.send_btn = SquishyButton(i18n.t("photo.simple.send"), variant="primary")
        self.skip_btn = QPushButton(i18n.t("photo.simple.skip"))
        row.addWidget(self.answer, 1)
        row.addWidget(self.send_btn)
        row.addWidget(self.skip_btn)
        chl.addWidget(self.question)
        chl.addLayout(row)
        lay.addWidget(self.chat_card)
        # kopyala yolu
        self.copy_card, cpl = _card()
        self.copy_text = QPlainTextEdit()
        self.copy_text.setReadOnly(True)
        self.copy_text.setFixedHeight(90)
        self.copy_btn = SquishyButton(i18n.t("photo.copy"), variant="secondary")
        cpl.addWidget(QLabel(i18n.t("photo.simple.copy_hint")))
        cpl.addWidget(self.copy_text)
        cpl.addWidget(self.copy_btn)
        lay.addWidget(self.copy_card)
        # galeri
        self.gallery_card, gl = _card()
        gt = QLabel(i18n.t("photo.simple.gallery"))
        gt.setProperty("role", "h2")
        gl.addWidget(gt)
        gl.addWidget(QLabel(i18n.t("photo.simple.gallery_hint")))
        self.gallery = VariationGrid(simple=True, columns=2)
        self.bind_grid(self.gallery)
        gl.addWidget(self.gallery)
        self.tool_label = QLabel("")
        self.tool_label.setProperty("role", "hint")
        gl.addWidget(self.tool_label)
        lay.addWidget(self.gallery_card)
        lay.addStretch(1)
        self.connect_service()
        self.svc.chatChanged.connect(self._on_chat)
        self.svc.specChanged.connect(self._on_spec)
        self.svc.toolDone.connect(lambda tool, path: self.tool_label.setText(i18n.t("photo.tool_done", path=path)))
        self.go_btn.clicked.connect(self.dream)
        self.send_btn.clicked.connect(self.send_answer)
        self.answer.returnPressed.connect(self.send_answer)
        self.skip_btn.clicked.connect(self.svc.skip_question)
        self.image_btn.clicked.connect(self.from_image)
        self.copy_btn.clicked.connect(self.copy_prompt)
        self.refresh_share()
        self._on_chat()
        self.refresh_gallery()
        self.copy_card.setVisible(False)

    # -- eylemler ------------------------------------------------------------------------------------
    def best_model(self) -> str:
        have = self.svc.available_models()
        return next((m for m in SIMPLE_PREFERENCE if m in have), "midjourney")

    def dream(self) -> None:
        """"Hayal Et!": fikri alır; eksik alan varsa sorar, hazırsa üretir (ya da Midjourney yolunda istemi gösterir)."""
        text = self.idea.toPlainText().strip()
        if not text:
            return
        self.clear_error()
        self.want_generate = True
        self.svc.set_model(self.best_model())
        self.svc.submit_idea(text)

    def from_image(self) -> None:
        path = self.pick_image()
        if path:
            self.clear_error()
            self.want_generate = True
            self.svc.describe_image(path)

    def send_answer(self) -> None:
        text = self.answer.text().strip()
        if text:
            self.answer.clear()
            self.svc.answer(text)

    def _on_chat(self) -> None:
        asking = bool(self.svc.idea) and not self.svc.ready()
        last = next((t for t in reversed(self.svc.chat) if t.role == "assistant" and t.slot), None)
        self.chat_card.setVisible(asking and last is not None)
        if last is not None:
            self.question.setText(last.text)
        if self.svc.ready() and self.want_generate and self.svc.idea:
            self.want_generate = False
            self._produce()

    def _on_spec(self) -> None:
        self._on_chat()

    def _produce(self) -> None:
        model = self.best_model()
        if self.svc.action_for(model) == "copy":
            ad = self.svc.preview("midjourney")
            self.copy_text.setPlainText(ad.text)
            self.copy_card.setVisible(True)
        else:
            self.copy_card.setVisible(False)
            self.svc.generate(model, int(self.ctx.settings.get("photo.count") or 4))
