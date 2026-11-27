"""Fotoğraf ve Dizi/Film sayfalarının ortak **AI durum çubuğu + kurulum kartı** (Basit ve Legend'de aynı).

* ``AIPanel`` — üstte "Yazı yapay zekâsı: Claude · Bağlı" gibi satırlar; hazır değilse altında düz Türkçe kurulum kartı:
  1-2-3 adım + resmî "anahtar al" bağlantısı + anahtar kutusu (maskeli) + **Kaydet ve dene** + ücretsiz yerel yol (Ollama / ComfyUI).
* ``handle_action`` — hata kartı düğmeleri (adres aç, ayarları aç, yeniden dene) — sayfalar bunu kullanır.

Durum bilgisini ``AIAccess`` (Qt'siz) verir; ağ işleri servisin arka plan havuzunda yapılır, anahtar yalnızca kasaya yazılır.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from .. import device
from ..core import i18n
from ..core.contracts import PingResult, SmartAction, SmartError, Topics
from ..ui import adaptive
from ..ui.theme.manager import current_tokens
from ..ui.widgets.smart_error_card import SmartErrorCard
from ..ui.widgets.squishy_button import SquishyButton
from . import aierrors
from .access import KEY_CHOICES, LOCAL_PROVIDER, ServiceStatus


def open_url_default(url: str) -> bool:
    return bool(QDesktopServices.openUrl(QUrl(url)))


def handle_action(ctx: Any, action: SmartAction, *, retry: Optional[Callable[[], None]] = None,
                  open_url: Callable[[str], Any] = open_url_default) -> None:
    """Hata kartı düğmesini uygular. ``copy`` kartın kendisinde yapılır; ``custom`` konu olarak yayınlanır."""
    kind = action.kind
    if kind == "open_url":
        url = str(action.payload.get("url", ""))
        if url.startswith("https://") or url.startswith("http://"):
            open_url(url)
    elif kind == "open_settings":
        ctx.bus.emit(Topics.NAVIGATE, {"module": "settings"})
    elif kind == "retry":
        if retry is not None:
            retry()
    elif kind == "custom":
        topic = str(action.payload.get("topic", ""))
        if topic:
            ctx.bus.emit(topic, dict(action.payload))


class _KindRow(QFrame):
    """Tek servis türünün durum satırı: ● Yazı yapay zekâsı: Claude · Bağlı   [Nasıl bağlarım?]"""
    helpRequested = Signal(str)

    def __init__(self, kind: str, optional: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.kind, self.optional = kind, optional
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.dot = QLabel("●")
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.text.setObjectName(f"ai_status_{kind}")
        self.help_btn = QPushButton(i18n.t("ai.status.setup"))
        self.help_btn.setProperty("role", "topbtn")
        self.help_btn.setFlat(True)
        self.help_btn.clicked.connect(lambda: self.helpRequested.emit(self.kind))
        lay.addWidget(self.dot)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.help_btn)

    def set_status(self, st: ServiceStatus) -> None:
        tk = current_tokens()
        color = tk.good if st.ready else (tk.warn if st.state in ("local_down", "no_model") else tk.bad)
        if self.optional and not st.ready:
            color = tk.text2
        self.dot.setStyleSheet(f"color:{color};")
        text = st.label
        if self.optional:
            text += f"  ({i18n.t('ai.status.optional')})"
        self.text.setText(text)
        self.text.setAccessibleName(text)
        self.help_btn.setVisible(not st.ready)


class SetupCard(QFrame):
    """Hazır servis yokken: ne gerektiği, 1-2-3 adım, anahtar kutusu, ücretsiz yerel yol."""

    def __init__(self, ctx: Any, svc: Any, kind: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx, self.svc, self.kind = ctx, svc, kind
        self.setObjectName(f"ai_setup_{kind}")
        self.setProperty("role", "card")
        self.open_url: Callable[[str], Any] = open_url_default
        self._card: Optional[SmartErrorCard] = None
        lay = QVBoxLayout(self)
        side = adaptive.margin(18, 12)
        lay.setContentsMargins(side, 14, side, 14)
        lay.setSpacing(8)
        title = QLabel(i18n.t(f"ai.setup.title.{kind}"))
        title.setProperty("role", "h2")
        title.setWordWrap(True)
        lay.addWidget(title)
        for text, role in ((i18n.t("ai.setup.lead"), None), (i18n.t("ai.setup.step1"), None), (i18n.t("ai.setup.step2"), None),
                           (i18n.t("ai.setup.step3"), None)):
            lb = QLabel(text)
            lb.setWordWrap(True)
            lay.addWidget(lb)
        # servis seçimi + anahtar sayfası (telefonda sığmayan alt satıra)
        row = adaptive.hbox()
        row.addWidget(QLabel(i18n.t("ai.setup.service")))
        self.combo = QComboBox()
        for pid in KEY_CHOICES[kind]:
            self.combo.addItem(aierrors.info(pid).name, pid)
        self.combo.setAccessibleName(i18n.t("ai.setup.service"))
        row.addWidget(self.combo, 1)
        self.open_key = SquishyButton(i18n.t("ai.setup.open_key"), variant="secondary")
        row.addWidget(self.open_key)
        lay.addLayout(row)
        self.url_label = QLabel("")
        self.url_label.setProperty("role", "hint")
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.setWordWrap(adaptive.is_mobile())
        lay.addWidget(self.url_label)
        # anahtar kutusu
        key_row = adaptive.hbox()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)           # ekranda hep gizli
        self.key_edit.setPlaceholderText(i18n.t("ai.setup.key_placeholder"))
        self.key_edit.setAccessibleName(i18n.t("ai.setup.key_placeholder"))
        self.save_btn = SquishyButton(i18n.t("ai.setup.save_test"), variant="primary", accent="butter")
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.save_btn)
        lay.addLayout(key_row)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setProperty("role", "hint")
        lay.addWidget(self.result)
        self.error_slot = QVBoxLayout()
        lay.addLayout(self.error_slot)
        cost = QLabel(i18n.t("ai.setup.cost"))
        cost.setProperty("role", "hint")
        cost.setWordWrap(True)
        lay.addWidget(cost)
        # ücretsiz yerel yol (Ollama / ComfyUI bu bilgisayarda). Android'de yok: bölüm gizli.
        local_part = QWidget()
        local_lay = QVBoxLayout(local_part)
        local_lay.setContentsMargins(0, 0, 0, 0)
        local_lay.setSpacing(8)
        local_part.setVisible(not device.is_android())
        lay.addWidget(local_part)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        local_lay.addWidget(sep)
        local_id = LOCAL_PROVIDER[kind]
        lt = QLabel(i18n.t("ai.setup.local_title"))
        lt.setProperty("role", "h2")
        lt.setWordWrap(True)
        local_lay.addWidget(lt)
        pi = aierrors.info(local_id)
        group = "text" if kind == "text" else "image"
        for i in range(1, 5):
            lb = QLabel(i18n.t(f"ai.setup.local_{group}.{i}", local_url=pi.local_url, pull_cmd=f"ollama pull {aierrors.OLLAMA_SUGGESTED}"))
            lb.setWordWrap(True)
            lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            local_lay.addWidget(lb)
        lrow = adaptive.hbox()
        self.open_local = SquishyButton(i18n.t("ai.setup.open_local"), variant="secondary")
        self.recheck = SquishyButton(i18n.t("ai.setup.recheck"), variant="ghost")
        self.local_label = QLabel("")
        self.local_label.setProperty("role", "hint")
        self.local_label.setWordWrap(True)
        lrow.addWidget(self.open_local)
        lrow.addWidget(self.recheck)
        lrow.addWidget(self.local_label, 1)
        local_lay.addLayout(lrow)
        self.combo.currentIndexChanged.connect(self._on_service)
        self.open_key.clicked.connect(self._open_key)
        self.save_btn.clicked.connect(self.save_and_test)
        self.key_edit.returnPressed.connect(self.save_and_test)
        self.open_local.clicked.connect(lambda: self.open_url(pi.local_url))
        self.recheck.clicked.connect(self.recheck_local)
        self._on_service()

    # -- eylemler -----------------------------------------------------------------------------------
    def provider_id(self) -> str:
        return str(self.combo.currentData() or "")

    def _on_service(self) -> None:
        pi = aierrors.info(self.provider_id())
        self.url_label.setText(adaptive.breakable(pi.key_url))
        self.key_edit.clear()
        self.result.setText("")
        self._clear_card()

    def _open_key(self) -> None:
        url = aierrors.info(self.provider_id()).key_url
        if url:
            self.open_url(url)

    def _clear_card(self) -> None:
        if self._card is not None:
            self._card.hide()
            self._card.setParent(None)
            self._card.deleteLater()
            self._card = None

    def save_and_test(self) -> None:
        secret = self.key_edit.text().strip()
        self._clear_card()
        if not secret:
            self.result.setText(i18n.t("ai.setup.empty_key"))
            return
        self.result.setText(i18n.t("ai.setup.testing"))
        self.save_btn.setEnabled(False)
        pid = self.provider_id()
        self.svc.connect_provider(pid, secret, lambda res: self._tested(pid, res))
        self.key_edit.clear()                                           # düz anahtar kutuda bekletilmez

    def _tested(self, pid: str, res: PingResult) -> None:
        self.save_btn.setEnabled(True)
        if res.ok:
            self.result.setText(i18n.t("ai.setup.ok", name=aierrors.info(pid).name, detail=res.message))
            return
        self.result.setText("")
        if isinstance(res.error, SmartError):
            card = SmartErrorCard(res.error)
            card.dismissed.connect(self._clear_card)
            card.actionTriggered.connect(lambda a: handle_action(self.ctx, a, retry=self._clear_card, open_url=self.open_url))
            self._card = card
            self.error_slot.addWidget(card)
        else:
            self.result.setText(res.message)

    def recheck_local(self) -> None:
        self.local_label.setText(i18n.t("ai.setup.rechecking"))
        self.svc.refresh_local()

    def local_checked(self, status: ServiceStatus) -> None:
        name = aierrors.info(LOCAL_PROVIDER[self.kind]).name
        if self.local_label.text() == i18n.t("ai.setup.rechecking"):
            self.local_label.setText(i18n.t("ai.setup.now_ready", name=name) if status.ready
                                     else i18n.t("ai.setup.still_down", name=name))


class AIPanel(QWidget):
    """Sayfanın üstüne konur. ``kinds``: ``("image", "text")`` gibi; ilki asıl servis, diğerleri "isteğe bağlı" satırı."""

    def __init__(self, ctx: Any, svc: Any, kinds: Sequence[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx, self.svc, self.kinds = ctx, svc, tuple(kinds)
        self.setObjectName("ai_panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.rows: Dict[str, _KindRow] = {}
        self.cards: Dict[str, SetupCard] = {}
        self._forced: Dict[str, bool] = {}
        bar = QFrame()
        bar.setProperty("role", "panel")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(4)
        for i, kind in enumerate(self.kinds):
            row = _KindRow(kind, optional=i > 0)
            row.helpRequested.connect(self._toggle_card)
            self.rows[kind] = row
            bl.addWidget(row)
        lay.addWidget(bar)
        for kind in self.kinds:
            card = SetupCard(ctx, svc, kind)
            card.setVisible(False)
            self.cards[kind] = card
            lay.addWidget(card)
        svc.aiChanged.connect(self.refresh)
        self.refresh()

    def status(self, kind: str) -> ServiceStatus:
        return self.svc.ai_status(kind)

    def set_open_url(self, fn: Callable[[str], Any]) -> None:
        for card in self.cards.values():
            card.open_url = fn

    def _toggle_card(self, kind: str) -> None:
        self._forced[kind] = not self._forced.get(kind, False)
        self.refresh()

    def refresh(self) -> None:
        for i, kind in enumerate(self.kinds):
            st = self.status(kind)
            self.rows[kind].set_status(st)
            # asıl servis hazır değilse kart kendiliğinden açılır; isteğe bağlı olanlar yalnız "Nasıl bağlarım?" ile
            show = (not st.ready) and (i == 0 or self._forced.get(kind, False))
            if st.ready:
                self._forced[kind] = False
            self.cards[kind].setVisible(show)
            self.cards[kind].local_checked(st)

    def primary_ready(self) -> bool:
        return self.status(self.kinds[0]).ready
