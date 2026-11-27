"""Kayıt yeri çubuğu ve son kayıt kartı — Basit ve Legend sayfaları bu iki widget'ı ortak kullanır.

* ``RecordingLocationBar``: "Kayıt yeri: <tam yol>" (seçilebilir), "Klasörü aç", "Değiştir". Yol her zaman görünür.
* ``RecordingResultCard``: kayıt bitince dosya adı, süre, tam yol; "Oynat" ve "Klasörde göster".

Dosya eylemleri ``QDesktopServices`` ile yapılır; Android'de "Oynat" köprüyle açılır, "Klasörü aç", "Değiştir" ve
"Klasörde göster" yerine "Paylaş" ve "Galeriye kaydet" gelir (``kadraj.ui.files``, M20). Başarısızlık bu widget'ların kendi not satırında gösterilir
(sayfa ya da servis durumu değiştirilmez). "Değiştir" kayıt yerini Ayarlar'daki proje klasörü seçimine bırakır:
sayfa ``changeRequested`` ile Ayarlar'a geçer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ... import device
from ...core import i18n
from ...ui import adaptive, files
from ...ui.widgets.squishy_button import SquishyButton
from .takes import Take


def open_folder(folder: Path) -> bool:
    """Klasörü sistem dosya yöneticisinde açar; yoksa önce oluşturur. Başarısızsa ``False``."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))


def open_file(path: Path, note: Optional[files.Note] = None) -> bool:
    """Dosyayı varsayılan oynatıcıyla açar. Dosya yoksa ya da açılamazsa ``False``."""
    if device.is_android():                                    # köprüyle uygun uygulama; nedeni note'a (M20)
        return files.open_path(path, note)
    if not path.is_file():
        return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))


def _note(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "hint")
    label.setWordWrap(True)
    label.setVisible(bool(text))
    return label


def fit_height(widget: QWidget) -> None:
    """Kelime kaydırmalı etiketler yüksekliğini genişliğe göre bildirir; QLayout bunu kendiliğinden hesaplamaz.

    Asgari yükseklik, o anki genişlikteki gerçek yükseklik olarak ayarlanır; aksi halde kaydırma alanındaki sayfa
    içeriği görünür alana sıkışır ve yol/dosya adı kesilir.
    """
    lay = widget.layout()
    if lay is None or widget.width() <= 0:
        return
    widget.setMinimumHeight(lay.totalHeightForWidth(widget.width()))


class RecordingLocationBar(QFrame):
    """Kayıtların nereye gideceğini gösterir (kayıt başlamadan önce de)."""

    changeRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("recording_location")
        self.setProperty("role", "surface2")
        self._folder: Optional[Path] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label)
        row = adaptive.hbox(spacing=8)
        self.open_btn = SquishyButton(i18n.t("recorder.location.open"), variant="secondary")
        self.change_btn = SquishyButton(i18n.t("recorder.location.change"), variant="ghost")
        self.change_btn.setToolTip(i18n.t("recorder.location.change_hint"))
        self.change_btn.setAccessibleDescription(i18n.t("recorder.location.change_hint"))
        row.addWidget(self.open_btn)
        row.addWidget(self.change_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.hint = QLabel(i18n.t("recorder.location.change_hint"))
        self.hint.setProperty("role", "hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        if not files.can_reveal():                 # Android: klasör açılamaz, yer değiştirilemez (M12, M20)
            self.open_btn.hide()
            self.change_btn.hide()
            self.hint.setText(i18n.t("files.android_folder"))
        self.note = _note()
        lay.addWidget(self.note)
        self.open_btn.clicked.connect(self._open)
        self.change_btn.clicked.connect(self.changeRequested.emit)

    @property
    def folder(self) -> Optional[Path]:
        return self._folder

    def set_folder(self, folder: Path) -> None:
        self._folder = Path(folder)
        # telefonda yol ayırıcılardan bölünür: Windows'un "\\" ayırıcısında Qt satır kırmaz, uzun yol ekrandan taşardı
        self.label.setText(i18n.t("recorder.location.label", path=adaptive.breakable(str(self._folder))))
        self.setAccessibleName(i18n.t("recorder.location.label", path=str(self._folder)))
        self.fit_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_height()

    def fit_height(self) -> None:
        fit_height(self)

    def _open(self) -> None:
        if self._folder is None:
            return
        if open_folder(self._folder):
            self._set_note("")
        else:
            self._set_note(i18n.t("recorder.location.open_failed", path=str(self._folder)))

    def _set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))


class RecordingResultCard(QFrame):
    """Kayıt durdurulunca görünür: dosya adı, süre, tam yol ve iki eylem."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("recording_result")
        self.setProperty("role", "card-tone")
        self._take: Optional[Take] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        head = QLabel(i18n.t("recorder.result.title"))
        head.setProperty("role", "caps")
        lay.addWidget(head)
        self.name = QLabel("")
        self.name.setProperty("role", "h2")
        self.name.setWordWrap(True)
        self.name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.name)
        self.meta = QLabel("")
        self.meta.setProperty("role", "muted")
        self.meta.setWordWrap(True)
        lay.addWidget(self.meta)
        self.where = QLabel("")
        self.where.setProperty("role", "muted")
        self.where.setWordWrap(True)
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.where)
        row = adaptive.hbox(spacing=8)
        self.play_btn = SquishyButton(i18n.t("recorder.result.play"), variant="primary", accent="lavender")
        self.show_btn = SquishyButton(i18n.t("recorder.result.show"), variant="secondary")
        row.addWidget(self.play_btn)
        row.addWidget(self.show_btn)
        self.show_btn.setVisible(files.can_reveal())
        self.mobile_btns = files.mobile_buttons(
            lambda: str(self._take.path) if self._take is not None else None, self._set_note)
        for b in self.mobile_btns:                 # Android: Paylaş, Galeriye kaydet
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.note = _note()
        lay.addWidget(self.note)
        self.play_btn.clicked.connect(self._play)
        self.show_btn.clicked.connect(self._show)
        self.hide()

    @property
    def take(self) -> Optional[Take]:
        return self._take

    def set_take(self, take: Take, duration_text: str) -> None:
        self._take = take
        self.name.setText(adaptive.breakable(take.path.name))
        self.meta.setText(i18n.t("recorder.result.meta", duration=duration_text,
                                 rate=f"{take.sample_rate / 1000:g}"))
        self.where.setText(i18n.t("recorder.result.where") + ": " + adaptive.breakable(str(take.path)))
        self.setAccessibleName(take.path.name)
        self._set_note("")
        self.fit_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_height()

    def fit_height(self) -> None:
        fit_height(self)

    def _play(self) -> None:
        if self._take is None:
            return
        if not open_file(self._take.path, self._set_note) and not device.is_android():
            self._set_note(i18n.t("recorder.result.play_failed"))

    def _show(self) -> None:
        if self._take is None:
            return
        if not open_folder(self._take.path.parent):
            self._set_note(i18n.t("recorder.location.open_failed", path=str(self._take.path.parent)))

    def _set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))
