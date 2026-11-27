"""Fotoğraf sayfalarının ortak tabanı: servise bağlanma, satır içi hata kartı, meşgul göstergesi, plan/kota satırı, galeri bağlama."""
from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QLayout, QVBoxLayout, QWidget

from ...ai_engine.aiwidgets import AIPanel, handle_action, open_url_default
from ...core import i18n
from ...ui import adaptive, files
from ...core.contracts import SmartAction, SmartError
from ...ui.widgets.smart_error_card import SmartErrorCard
from .service import PhotoService
from .widgets import VariationGrid


class PhotoPageBase(QWidget):
    """Sayfa **durum tutmaz**: her şey ``self.svc`` (``PhotoService``)'tedir."""

    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.svc: PhotoService = ctx.service("photo")
        self.error_slot = QVBoxLayout()
        self.error_slot.setContentsMargins(0, 0, 0, 0)
        self._error_card: Optional[SmartErrorCard] = None
        self.busy_label = QLabel("")
        self.busy_label.setProperty("role", "hint")
        self.share_label = QLabel("")
        self.share_label.setProperty("role", "hint")
        self.grid: Optional[VariationGrid] = None
        #: üstte "Görsel yapay zekâsı: … · Bağlı" + (gerekirse) kurulum kartı; yazı yardımcısı isteğe bağlı satır
        self.ai_panel = AIPanel(ctx, self.svc, ("image", "text"))
        self.open_url = open_url_default                       # testler tarayıcı açmasın diye değiştirilebilir
        # Android'de seçilen görsel content:// adresinden uygulama klasörüne kopyalanır (M20)
        self.pick_image = lambda: files.pick_file(self, i18n.t("photo.pick_image"), "Images (*.png *.jpg *.jpeg *.webp)")
        #: son araç çıktısı (arka plan silme, büyütme…) ve Android'de onun için Aç/Paylaş/Galeriye kaydet düğmeleri
        self.tool_path: Optional[str] = None
        self.tool_btns: List[QWidget] = []

    def tool_file_row(self, note: QLabel) -> QLayout:
        """Araç çıktısı için düğme satırı (Android; masaüstünde boş). Düğmeler ilk çıktıya kadar gizli."""
        row = adaptive.hbox()
        self.tool_btns = files.mobile_buttons(lambda: self.tool_path, note.setText, open_button=True)
        for b in self.tool_btns:
            b.hide()
            row.addWidget(b)
        if self.tool_btns:
            row.addStretch(1)
        return row

    def remember_tool_output(self, path: str) -> None:
        self.tool_path = path
        for b in self.tool_btns:
            b.show()

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

    def connect_service(self) -> None:
        s = self.svc
        s.errorRaised.connect(self.show_error)
        s.busyChanged.connect(lambda b: self.busy_label.setText(i18n.t("photo.busy") if b else ""))
        s.variationsChanged.connect(self.refresh_gallery)
        s.specChanged.connect(self.refresh_share)

    def refresh_share(self) -> None:
        n = int(self.ctx.settings.get("photo.count") or (4 if self.__class__.__name__.endswith("SimplePage") else 1))
        self.share_label.setText(self.svc.estimate_cost(n).text)

    def refresh_gallery(self) -> None:
        if self.grid is not None:
            self.grid.set_variations(self.svc.variations)

    def bind_grid(self, grid: VariationGrid) -> None:
        self.grid = grid
        grid.favoriteToggled.connect(self.svc.toggle_favorite)
        grid.upscaleRequested.connect(self._upscale)

    def _upscale(self, variation_id: int, factor: int) -> None:
        v = next((x for x in self.svc.variations if x.id == variation_id), None)
        if v is not None:
            self.svc.upscale(v.path, factor)

    def copy_prompt(self) -> str:
        ad = self.svc.preview()
        text = ad.text + (f"\n\nNegative: {ad.negative}" if ad.negative and ad.model != "midjourney" else "")
        QGuiApplication.clipboard().setText(ad.text)
        return text

    # -- hata kartı -------------------------------------------------------------------------------
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
