"""``SmartErrorCard`` — Akıllı Hata Kartı: ne oldu → neden → ne yapmalı → düğmeler → ayrıntılar.

Veri kaynağı ``SmartError``'dır (``contracts``). Kart yalnızca gösterir; düğme eylemlerini
``actionTriggered(SmartAction)`` sinyaliyle kabuğa bırakır (yeniden dene, ayarları aç, panoya kopyala…).
Teknik ayrıntı varsayılan olarak **katlıdır**.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout,
                               QWidget)

from ...core import i18n
from ...core.contracts import Severity, SmartAction, SmartError
from ..theme.manager import current_tokens
from .squishy_button import SquishyButton


class SmartErrorCard(QFrame):
    actionTriggered = Signal(object)          # SmartAction
    dismissed = Signal()

    def __init__(self, error: SmartError, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.error = error
        tk = current_tokens()
        self.setProperty("role", "card")
        self.setAccessibleName(error.title)
        self.setAccessibleDescription(error.summary)
        color = {Severity.ERROR: tk.bad, Severity.WARNING: tk.warn, Severity.INFO: tk.info}[error.severity]
        self.setStyleSheet(f"QFrame[role='card'] {{ border: {max(2, tk.border_w)}px solid {color}; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(tk.px(20), tk.px(16), tk.px(20), tk.px(16))
        root.setSpacing(tk.px(8))

        title = QLabel(error.title)
        title.setProperty("role", "h2")
        title.setWordWrap(True)
        root.addWidget(title)
        if error.summary:
            summary = QLabel(error.summary)
            summary.setWordWrap(True)
            root.addWidget(summary)
        if error.cause:
            cause = QLabel(i18n.t("errcard.cause") + " " + error.cause)
            cause.setWordWrap(True)
            cause.setProperty("role", "muted")
            root.addWidget(cause)
        if error.steps:
            head = QLabel(i18n.t("errcard.what_to_do"))
            head.setProperty("role", "caps")
            root.addWidget(head)
            for i, step in enumerate(error.steps, 1):
                s = QLabel(f"{i}. {step}")
                s.setWordWrap(True)
                root.addWidget(s)

        row = QHBoxLayout()
        row.setSpacing(tk.px(10))
        for action in error.actions:
            btn = SquishyButton(action.label, variant="primary" if action.kind == "retry" else "secondary")
            btn.clicked.connect(lambda _=False, a=action: self._on_action(a))
            row.addWidget(btn)
        close = SquishyButton(i18n.t("errcard.dismiss"), variant="ghost")
        close.clicked.connect(self.dismissed.emit)
        row.addWidget(close)
        row.addStretch(1)
        root.addLayout(row)

        self._details: Optional[QPlainTextEdit] = None
        if error.technical:
            self._toggle = SquishyButton(i18n.t("errcard.show_details"), variant="ghost")
            self._toggle.clicked.connect(self._toggle_details)
            root.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignLeft)
            self._details = QPlainTextEdit(error.technical)
            self._details.setReadOnly(True)
            self._details.setMaximumHeight(tk.px(110))
            self._details.hide()
            root.addWidget(self._details)

    @property
    def details_visible(self) -> bool:
        return bool(self._details is not None and self._details.isVisible())

    def _toggle_details(self) -> None:
        if self._details is None:
            return
        show = self._details.isHidden()
        self._details.setVisible(show)
        self._toggle.setText(i18n.t("errcard.hide_details" if show else "errcard.show_details"))

    def _on_action(self, action: SmartAction) -> None:
        if action.kind == "copy":
            QApplication.clipboard().setText(str(action.payload.get("text", "")))
        self.actionTriggered.emit(action)
