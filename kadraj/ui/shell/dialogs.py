"""Kabuk diyalogları: kullanım/gizlilik onayı, hakkında, hata penceresi."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget)

from ... import __version__, about, consent
from ...core import i18n
from ...core.contracts import SmartError
from .. import adaptive
from ..widgets.lockup import Lockup
from ..widgets.smart_error_card import SmartErrorCard
from ..widgets.squishy_button import SquishyButton


class ConsentDialog(QDialog):
    """İlk açılışta bir kez: yerel çalışma, gizlilik, yapay zekâ çıktısı sorumluluğu."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.t("consent.title"))
        self.setModal(True)
        adaptive.fit_dialog(self, 640, 560)
        lay = QVBoxLayout(self)
        m = adaptive.margin(32, 16)
        lay.setContentsMargins(m, adaptive.margin(28, 14), m, adaptive.margin(24, 12))
        lay.setSpacing(14)
        lay.addWidget(Lockup(wordmark_px=adaptive.margin(34, 24), show_mark=True), 0, Qt.AlignmentFlag.AlignLeft)
        body = QWidget()
        text = QVBoxLayout(body)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(14)
        title = QLabel(i18n.t("consent.title"))
        title.setProperty("role", "title")
        title.setWordWrap(True)
        text.addWidget(title)
        for key in ("consent.p1", "consent.p2", "consent.p3", "consent.p4"):
            lab = QLabel(i18n.t(key))
            lab.setWordWrap(True)
            text.addWidget(lab)
        lic = QLabel(i18n.t("consent.license"))
        lic.setWordWrap(True)
        lic.setProperty("role", "muted")
        text.addWidget(lic)
        text.addStretch(1)
        if adaptive.is_phone():                 # telefonda metin ekrana sığmazsa kaydırılır, düğmeler hep görünür
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(body)
            lay.addWidget(scroll, 1)
        else:
            lay.addWidget(body, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.decline = SquishyButton(i18n.t("consent.decline"), variant="secondary")
        self.accept_btn = SquishyButton(i18n.t("consent.accept"), variant="primary")
        self.decline.clicked.connect(self.reject)
        self.accept_btn.clicked.connect(self.accept)
        self.accept_btn.setDefault(True)
        row.addWidget(self.decline)
        row.addWidget(self.accept_btn)
        lay.addLayout(row)


def ensure_consent(parent: Optional[QWidget] = None,
                   ask: Optional[Callable[[], bool]] = None) -> bool:
    """Onay kayıtlıysa ``True``; değilse sorar (``ask`` testlerde sahte verilebilir) ve kaydeder."""
    if consent.is_accepted():
        return True
    accepted = ask() if ask is not None else (ConsentDialog(parent).exec() == QDialog.DialogCode.Accepted)
    if not accepted:
        return False
    consent.record_acceptance()
    return True


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.t("about.title"))
        adaptive.fit_dialog(self, 520, 420)
        lay = QVBoxLayout(self)
        m = adaptive.margin(32, 16)
        lay.setContentsMargins(m, adaptive.margin(28, 14), m, adaptive.margin(24, 12))
        lay.setSpacing(10)
        lay.addWidget(Lockup(wordmark_px=40, show_mark=True), 0, Qt.AlignmentFlag.AlignLeft)
        ver = QLabel(i18n.t("about.version", version=__version__) + "  ·  " + about.format_date())
        ver.setProperty("role", "muted")
        lay.addWidget(ver)
        for text in (i18n.t("about.blurb"),
                     i18n.t("about.skeleton", credit=about.skeleton_credit()),
                     i18n.t("about.license", name=about.LICENSE_NAME)):
            lab = QLabel(text)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        lay.addStretch(1)
        close = SquishyButton(i18n.t("common.close"), variant="primary")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)


class ErrorDialog(QDialog):
    """Akıllı Hata Kartı'nı bir pencerede gösterir (modsuz)."""

    def __init__(self, error: SmartError, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(error.title)
        self.setModal(False)
        adaptive.fit_dialog(self, 560, 360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        self.card = SmartErrorCard(error)
        self.card.dismissed.connect(self.close)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.card)
        lay.addWidget(scroll)
