"""Dizi sayfalarının ortak tabanı: servise bağlanma, satır içi hata kartı, meşgul göstergesi, plan/kota metni."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...ai_engine.aiwidgets import AIPanel, handle_action, open_url_default
from ...core import i18n
from ...core.contracts import SmartAction, SmartError
from ...ui.widgets.smart_error_card import SmartErrorCard
from .service import SeriesService


class SeriesPageBase(QWidget):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.svc: SeriesService = ctx.service("series")
        self.error_slot = QVBoxLayout()
        self.error_slot.setContentsMargins(0, 0, 0, 0)
        self._error_card: Optional[SmartErrorCard] = None
        self.busy_label = QLabel("")
        self.busy_label.setProperty("role", "hint")
        #: üstte "Yazı yapay zekâsı: … · Bağlı" + (gerekirse) kurulum kartı; görsel servisi isteğe bağlı satır
        self.ai_panel = AIPanel(ctx, self.svc, ("text", "image"))
        self.open_url = open_url_default                       # testler tarayıcı açmasın diye değiştirilebilir

    @property
    def open_url(self):
        return self._open_url

    @open_url.setter
    def open_url(self, fn) -> None:
        self._open_url = fn
        self.ai_panel.set_open_url(fn)

    @property
    def error_visible(self) -> bool:
        return self._error_card is not None

    def connect_base(self) -> None:
        self.svc.errorRaised.connect(self.show_error)
        self.svc.busyChanged.connect(lambda b: self.busy_label.setText(i18n.t("series.busy") if b else ""))

    def show_error(self, err: SmartError) -> None:
        self.clear_error()
        card = SmartErrorCard(err)
        card.dismissed.connect(self.clear_error)
        card.actionTriggered.connect(self._on_action)
        self._error_card = card
        self.error_slot.addWidget(card)

    def _on_action(self, action: SmartAction) -> None:
        def retry() -> None:
            self.clear_error()
            self.svc.retry_last()
        handle_action(self.ctx, action, retry=retry, open_url=self.open_url)

    def clear_error(self) -> None:
        if self._error_card is not None:
            self._error_card.hide()
            self._error_card.setParent(None)
            self._error_card.deleteLater()
            self._error_card = None

    def share_text(self) -> str:
        s = self.svc.plan_share()
        if s.budget is None:
            return i18n.t("series.quota.nobudget", cost=f"{self.svc.estimate().cost_usd:.2f}")
        return s.text
