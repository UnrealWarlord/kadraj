"""Ayarlar › API anahtarları: "Bağlantıyı test et" sonucunu **sade Türkçe** gösterir.

Başarılıysa tek satır ("… bağlantı tamam, 12 model görünüyor"); başarısızsa Akıllı Hata Kartı: neden + numaralı adımlar + düğmeler
(anahtar sayfasını aç, ödeme sayfasını aç, Ayarlar, yeniden dene). Test para harcamayan tek bir GET'tir (model/kredi listesi);
anahtar hiçbir yerde gösterilmez.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ...ai_engine.aiwidgets import handle_action, open_url_default
from ...core.contracts import PingResult, SmartError
from ...ui.theme.manager import current_tokens
from ...ui.widgets.smart_error_card import SmartErrorCard


class KeyTestResult(QFrame):
    """``service.test_results()`` → satır/kart. ``service.changed("keys")`` sonrası ``refresh()`` çağrılır."""

    def __init__(self, service: Any, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.service, self.ctx = service, ctx
        self.setObjectName("key_test_result")
        self.open_url: Callable[[str], Any] = open_url_default            # testler tarayıcı açmasın diye değiştirilebilir
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 4, 0, 0)
        self._lay.setSpacing(6)
        self._widgets: list = []

    def _clear(self) -> None:
        for w in self._widgets:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        self._widgets = []

    def refresh(self) -> None:
        self._clear()
        results = self.service.test_results()
        for pid, res in results.items():
            w = self._ok_label(res) if res.ok else self._error_card(pid, res)
            self._widgets.append(w)
            self._lay.addWidget(w)
        self.setVisible(bool(results))

    def _ok_label(self, res: PingResult) -> QWidget:
        label = QLabel("✓  " + res.message)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{current_tokens().good};")
        label.setAccessibleName(res.message)
        return label

    def _error_card(self, pid: str, res: PingResult) -> QWidget:
        if not isinstance(res.error, SmartError):
            label = QLabel("✗  " + res.message)
            label.setWordWrap(True)
            return label
        card = SmartErrorCard(res.error)
        card.dismissed.connect(lambda p=pid: self.service.clear_test_result(p))
        card.actionTriggered.connect(lambda a, p=pid: handle_action(self.ctx, a, retry=lambda: self.service.test_connection(p),
                                                                     open_url=self.open_url))
        return card
