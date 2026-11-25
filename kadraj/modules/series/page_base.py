"""Dizi sayfalarının ortak tabanı: servise bağlanma, satır içi hata kartı, meşgul göstergesi, plan/kota metni."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core import i18n
from ...core.contracts import SmartError
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
        self._error_card = card
        self.error_slot.addWidget(card)

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
