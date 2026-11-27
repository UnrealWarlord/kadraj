"""İndirici sayfalarının ortak parçaları (Basit ve Legend): son indirme satırı, hata kartı, kalite kullanılabilirliği.

* Başarısız iş → hata kartı: başlık, sade neden, numaralı adımlar, düğmeler. Kart, iş kaydındaki ``_error``
  (``SmartError.to_dict()``) ile kurulur; o yoksa düz metin hata + genel adımlar. "Olmadı" tek başına gösterilmez.
* Biten iş → "Oynat", "Klasörde Göster", "Düzenlemeye gönder" (düzenleyici kitaplığına ``add_media``). Android'de
  "Klasörde Göster" yerine "Paylaş" ve "Galeriye kaydet" (``kadraj.ui.files``, M20).
* Kalite: ``quality_top`` — servis ``quality_unavailable(info, kalite)`` sağlıyorsa onu, yoksa video yüksekliğini kullanır.

Servis ve motor bu dosyada değişmez; sayfa yalnızca ``ctx.jobs`` ve ``ctx.service`` üzerinden okur.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import device
from ...core import i18n
from ...core.contracts import Severity, SmartAction, SmartError, Topics
from ...ui import adaptive, files
from ...ui.widgets import SmartErrorCard, SquishyButton
from .service import fit_quality, quality_states
from .strings import K

STATUS_KEYS = {
    "queued": K.STATUS_QUEUED, "running": K.STATUS_RUNNING, "done": K.STATUS_DONE,
    "failed": K.STATUS_FAILED, "cancelled": K.STATUS_CANCELLED, "paused": K.STATUS_PAUSED,
}


# ---------------------------------------------------------------------------------------------- hata
def _generic_steps() -> Tuple[str, ...]:
    return (i18n.t(K.FAILED_STEP1), i18n.t(K.FAILED_STEP2), i18n.t(K.FAILED_STEP3))


def _error_from_dict(d: Mapping[str, Any]) -> SmartError:
    """``SmartError.to_dict()`` çıktısından yeniden kurar; adım yoksa genel adımları koyar."""
    sev = str(d.get("severity", "error"))
    steps = tuple(str(s) for s in (d.get("steps") or ()) if str(s).strip()) or _generic_steps()
    actions = tuple(SmartAction(str(a.get("id", "")), str(a.get("label", "")), str(a.get("kind", "custom")),
                                dict(a.get("payload") or {}))
                    for a in (d.get("actions") or ()) if isinstance(a, Mapping))
    return SmartError(str(d.get("code", "download.failed")), str(d.get("title", "")) or i18n.t(K.FAILED_TITLE),
                      str(d.get("summary", "")), cause=str(d.get("cause", "")), steps=steps, actions=actions,
                      severity=Severity(sev) if sev in ("info", "warning", "error") else Severity.ERROR,
                      technical=str(d.get("technical", "")), retryable=bool(d.get("retryable", False)),
                      source=str(d.get("source", "downloader")))


def job_error(ctx: Any, row: Mapping[str, Any]) -> Optional[SmartError]:
    """Başarısız bir indirme satırının hatası: iş kaydındaki tam ``SmartError``; yoksa düz metinden kurulan kart."""
    job = None
    try:
        if row.get("id") is not None and ctx is not None:
            job = ctx.jobs.get(int(row["id"]))
    except Exception:  # noqa: BLE001 — iş kaydı okunamazsa düz metin yoluna düşer
        job = None
    raw = (getattr(job, "payload", None) or {}).get("_error")
    if isinstance(raw, Mapping) and raw.get("title"):
        return _error_from_dict(raw)
    text = str(row.get("error") or "").strip()
    return SmartError("download.plain", i18n.t(K.FAILED_TITLE), text or i18n.t(K.UNKNOWN_ERROR),
                      cause=i18n.t(K.FAILED_CAUSE), steps=_generic_steps(),
                      actions=(SmartAction("retry", i18n.t("err.action.retry"), "retry"),),
                      source="downloader")


# ---------------------------------------------------------------------------------------------- kalite
def quality_top(info: Any, quality: str) -> Optional[int]:
    """Kalite bu videoda yoksa videonun en yüksek yüksekliğini (px) döner; varsa ``None``.

    Karar servisindir: ``quality_states(info)`` her kart için ``available`` bayrağını verir. En yüksek yükseklik,
    video biçimlerinden ve ``max_height``'tan alınır (etikette "en yüksek: 1080p" olarak gösterilir).
    """
    if info is None or getattr(info, "is_playlist", False):
        return None
    state = next((s for s in quality_states(info) if s.key == quality), None)
    if state is None or state.available:
        return None
    heights = [int(getattr(info, "max_height", 0) or 0)]
    heights += [int(f.height) for f in getattr(info, "formats", ()) if getattr(f, "height", 0)]
    return max(heights) or None


def first_available_quality(info: Any, current: str) -> str:
    """Seçili kalite bu videoda yoksa servisin ``fit_quality`` kuralıyla en yakın uygun kaliteyi döner."""
    if quality_top(info, current) is None:
        return current
    return fit_quality(info, current)


def unavailable_text(top: int) -> str:
    return i18n.t(K.QUALITY_UNAVAILABLE, height=top)


# ---------------------------------------------------------------------------------------------- dosya eylemleri
def open_file(path: str, note: Optional[files.Note] = None) -> bool:
    file = Path(path)
    if not file.is_file():
        return False
    if device.is_android():                                    # köprüyle uygun uygulama (M20)
        return files.open_path(file, note)
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(file))))


def show_in_folder(path: str) -> bool:
    if not files.can_reveal():
        return False
    folder = Path(path).parent
    if not folder.is_dir():
        return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))


def send_to_editor(ctx: Any, path: str) -> Tuple[bool, str]:
    """Dosyayı düzenleyici kitaplığına ekler (``EditorService.add_media``). Dönüş: (başarılı mı, kullanıcı metni)."""
    editor = ctx.service("editor") if ctx is not None else None
    add = getattr(editor, "add_media", None)
    if not callable(add):
        return False, i18n.t(K.EDITOR_UNAVAILABLE)
    try:
        clip = add(path)
    except Exception:  # noqa: BLE001 — düzenleyici hatası kullanıcıya düz metinle söylenir
        clip = None
    if clip is None:
        return False, i18n.t(K.EDITOR_FAILED)
    return True, i18n.t(K.EDITOR_SENT, name=Path(path).name)


# ---------------------------------------------------------------------------------------------- satır
def _status_text(row: Mapping[str, Any]) -> str:
    status = str(getattr(row.get("status"), "value", row.get("status") or "queued"))
    text = i18n.t(STATUS_KEYS.get(status, K.STATUS_QUEUED))
    if status == "running":
        text += f" · {int(float(row.get('progress') or 0.0) * 100)}%"
    return text


def fit_height(widget: QWidget) -> None:
    """Kelime kaydırmalı etiketler (hata nedeni, adımlar) için asgari yüksekliği o anki genişliğe göre ayarlar.

    QLayout yükseklik-genişlik hesabını kendiliğinden yapmaz; yoksa kaydırma alanındaki satır sıkışır ve metin kesilir.
    """
    lay = widget.layout()
    if lay is None or widget.width() <= 0:
        return
    widget.setMinimumHeight(lay.totalHeightForWidth(widget.width()))


class RecentDownloadRow(QFrame):
    """Son indirmelerde tek satır: başlık, durum, biten dosya eylemleri ya da başarısızlık kartı."""

    def __init__(self, ctx: Any, row: Mapping[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("download_row")
        self.ctx = ctx
        self.row = dict(row)
        status = str(getattr(row.get("status"), "value", row.get("status") or ""))
        path = str(row.get("path") or "")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(6)

        top = QHBoxLayout()
        title = QLabel(str(row.get("title") or "—"))
        title.setWordWrap(True)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(title, 1)
        state = QLabel(_status_text(row))
        state.setProperty("role", "hint")
        top.addWidget(state)
        lay.addLayout(top)

        self.note = QLabel("")
        self.note.setProperty("role", "hint")
        self.note.setWordWrap(True)
        self.note.hide()
        if status == "done" and path:
            buttons = adaptive.hbox()                                   # telefonda düğmeler alt satıra geçer
            play = SquishyButton(i18n.t(K.BTN_PLAY), variant="secondary")
            play.clicked.connect(lambda _c=False, p=path: self._play(p))
            row_buttons = [play]
            if files.can_reveal():
                folder = SquishyButton(i18n.t(K.BTN_SHOW_FOLDER), variant="secondary")
                folder.clicked.connect(lambda _c=False, p=path: self._folder(p))
                row_buttons.append(folder)
            row_buttons += files.mobile_buttons(lambda p=path: p, self._set_note)     # Android: Paylaş, Galeriye kaydet
            send = SquishyButton(i18n.t(K.BTN_SEND_EDITOR), variant="primary", accent="mint")
            send.clicked.connect(lambda _c=False, p=path: self._send(p))
            for b in (*row_buttons, send):
                buttons.addWidget(b)
            buttons.addStretch(1)
            lay.addLayout(buttons)
        lay.addWidget(self.note)

        self.error_card: Optional[SmartErrorCard] = None
        if status == "failed":
            card = SmartErrorCard(job_error(ctx, row))
            self._hide_dismiss(card)
            card.actionTriggered.connect(self._on_action)
            self.error_card = card
            lay.addWidget(card)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        fit_height(self)

    @staticmethod
    def _hide_dismiss(card: SmartErrorCard) -> None:
        """Hata kartı açık kalır: "kapat" düğmesi ayrıntıyı gizleyip yalnızca "Olmadı" bırakmasın."""
        for b in card.findChildren(SquishyButton):
            if b.text() == i18n.t("errcard.dismiss"):
                b.hide()

    def _set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def _play(self, path: str) -> None:
        if not open_file(path, self._set_note) and not device.is_android():   # Android'de nedeni files yazar
            self._set_note(i18n.t(K.PLAY_FAILED))

    def _folder(self, path: str) -> None:
        if not show_in_folder(path):
            self._set_note(i18n.t(K.FOLDER_FAILED))

    def _send(self, path: str) -> None:
        _ok, text = send_to_editor(self.ctx, path)
        self._set_note(text)

    def _on_action(self, action: SmartAction) -> None:
        kind = action.kind
        if kind == "retry":
            jid = self.row.get("id")
            if jid is not None and self.ctx is not None:
                self.ctx.jobs.retry(int(jid))
        elif kind == "open_settings":
            self.ctx.bus.emit(Topics.NAVIGATE, {"module": "settings"})
        elif kind == "open_url":
            url = str(action.payload.get("url", ""))
            if url:
                QDesktopServices.openUrl(QUrl(url))
        elif kind == "custom":
            topic = str(action.payload.get("topic", ""))
            if topic:
                self.ctx.bus.emit(topic, dict(action.payload))


def failed_rows(rows: List[Mapping[str, Any]], limit: int = 3) -> List[Mapping[str, Any]]:
    return [r for r in rows if str(getattr(r.get("status"), "value", r.get("status") or "")) == "failed"][:limit]
