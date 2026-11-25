"""Medya İndirici — Basit Mod sayfası ("Video İndir" kartı). Teknik terim yok.

Sayfa yalnızca görünümdür: taslak ve iş durumu ``DownloaderService``'tedir. Sayfa
yeniden çizilince (Basit ↔ Legend) indirme durumu korunur.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLayout,
                               QLineEdit, QVBoxLayout, QWidget)

from ...core import i18n
from ...core.contracts import Accent, Job
from ...ui.theme.manager import ThemedMixin
from ...ui.widgets import ChoiceCard, ChoiceGroup, SmartErrorCard, SquishyButton
from ...ui.widgets.surface import page_surface_qss
from ...storage import disk
from .range_bar import RangeBar
from .rows import RecentDownloadRow, first_available_quality, quality_top, unavailable_text
from .service import (MODE_AUDIO, MODE_AV, MODE_VIDEO, QUALITY_BALANCED, QUALITY_BEST,
                      QUALITY_FAST, VideoInfo, describe_length, estimate_bytes, format_timecode)
from .strings import K

_QUALITY_CARDS = (
    (QUALITY_FAST, K.QUALITY_FAST, K.QUALITY_FAST_SUB, "sliders"),
    (QUALITY_BALANCED, K.QUALITY_BALANCED, K.QUALITY_BALANCED_SUB, "layers"),
    (QUALITY_BEST, K.QUALITY_BEST, K.QUALITY_BEST_SUB, "image"),
)


def _clear(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class DownloaderSimplePage(ThemedMixin, QWidget):
    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("downloader_simple")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # kök zemini QSS ile boyanır
        self.ctx = ctx
        service = ctx.service("downloader")
        if service is None:
            raise RuntimeError("İndirici servisi kullanılamıyor")
        self.service = service
        self._info: Optional[VideoInfo] = service.info
        self._build()
        self._wire()
        self._sync_view()
        self._refresh_recent()
        self._bind_theme()
        self._on_theme()

    def _on_theme(self) -> None:
        """Basit tokenlarından zemin (tema değişince yenilenir)."""
        self.setStyleSheet(page_surface_qss(self.tk, "downloader_simple"))

    # -- yapı --------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        capsule = QFrame()
        capsule.setProperty("role", "card")
        row = QHBoxLayout(capsule)
        row.setContentsMargins(16, 8, 8, 8)
        row.setSpacing(8)
        self.url = QLineEdit()
        self.url.setPlaceholderText(i18n.t(K.URL_PLACEHOLDER))
        self.url.setAccessibleName(i18n.t(K.URL_PLACEHOLDER))
        self.paste = SquishyButton(i18n.t(K.BTN_PASTE), variant="secondary")
        row.addWidget(self.url, 1)
        row.addWidget(self.paste)
        lay.addWidget(capsule)

        self.chip = QLabel(i18n.t(K.CHIP_IDLE))
        self.chip.setProperty("role", "chip")
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignLeft)
        self.error_box = QVBoxLayout()
        lay.addLayout(self.error_box)

        self.mode_cards = [
            ChoiceCard(MODE_AV, i18n.t(K.MODE_AV_TITLE), i18n.t(K.MODE_AV_SUB), "film", accent=Accent.PEACH),
            ChoiceCard(MODE_AUDIO, i18n.t(K.MODE_AUDIO_TITLE), i18n.t(K.MODE_AUDIO_SUB), "mic",
                       accent=Accent.LAVENDER),
            ChoiceCard(MODE_VIDEO, i18n.t(K.MODE_VIDEO_TITLE), i18n.t(K.MODE_VIDEO_SUB), "image",
                       accent=Accent.MINT),
        ]
        self.modes = ChoiceGroup(self.mode_cards, exclusive=True, columns=3)
        lay.addWidget(self.modes)

        self.partial = QCheckBox(i18n.t(K.PARTIAL_TOGGLE))
        self.range = RangeBar()
        self.range.setAccessibleName(i18n.t(K.PARTIAL_TOGGLE))
        self.range_text = QLabel("")
        self.range_text.setProperty("role", "hint")
        lay.addWidget(self.partial)
        lay.addWidget(self.range)
        lay.addWidget(self.range_text)

        self.quality_cards: List[ChoiceCard] = []
        for qid, title_key, sub_key, icon in _QUALITY_CARDS:
            self.quality_cards.append(ChoiceCard(qid, i18n.t(title_key), i18n.t(sub_key), icon))
        self.qualities = ChoiceGroup(self.quality_cards, exclusive=True, columns=3)
        lay.addWidget(self.qualities)

        self.recent_frame = QFrame()
        self.recent_frame.setProperty("role", "card")
        recent_lay = QVBoxLayout(self.recent_frame)
        recent_lay.setContentsMargins(16, 14, 16, 14)
        head = QLabel(i18n.t(K.RECENT_TITLE))
        head.setProperty("role", "h2")
        recent_lay.addWidget(head)
        self.recent_rows = QVBoxLayout()
        recent_lay.addLayout(self.recent_rows)
        lay.addWidget(self.recent_frame)

        foot = QHBoxLayout()
        self.target = QLabel("")
        self.target.setProperty("role", "muted")
        self.go = SquishyButton(i18n.t(K.BTN_DOWNLOAD), variant="primary", accent=Accent.PEACH)
        foot.addWidget(self.target, 1)
        foot.addWidget(self.go)
        lay.addLayout(foot)
        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        lay.addWidget(self.status)
        lay.addStretch(1)

    def _wire(self) -> None:
        s = self.service
        self.url.returnPressed.connect(lambda: s.probe(self.url.text()))
        self.paste.clicked.connect(self._paste)
        self.modes.selectionChanged.connect(self._on_mode)
        self.qualities.selectionChanged.connect(self._on_quality)
        self.partial.toggled.connect(self._on_partial)
        self.range.valuesChanged.connect(self._on_range)
        self.go.clicked.connect(self._download)
        s.probeStarted.connect(self._on_probe_started)
        s.probed.connect(self._on_probed)
        s.probeFailed.connect(self._on_probe_failed)
        s.draftChanged.connect(self._sync_view)
        s.queueChanged.connect(self._refresh_recent)

    # -- olaylar -----------------------------------------------------------
    def _paste(self) -> None:
        text = QApplication.clipboard().text().strip()
        self.url.setText(text)
        self.service.probe(text)

    def _on_mode(self, ids: List[str]) -> None:
        if ids:
            self.service.set_mode(ids[0])
        self._refresh_sizes()

    def _on_quality(self, ids: List[str]) -> None:
        if ids:
            self.service.set_quality(ids[0])

    def _on_partial(self, checked: bool) -> None:
        self.service.set_partial(checked)
        if checked and self._info is not None:
            self.range.set_duration(self._info.duration)
            self.range.set_values(0.0, self._info.duration)

    def _on_range(self, start: float, end: float) -> None:
        self.service.set_range(start, end)

    def _on_probe_started(self) -> None:
        self._info = None
        self._set_error(None)
        self.chip.setText(i18n.t(K.CHIP_SEARCHING))
        self.go.setEnabled(False)

    def _on_probed(self, info: VideoInfo) -> None:
        self._info = info
        self._set_error(None)
        if info.is_playlist:
            self.chip.setText(i18n.t(K.CHIP_PLAYLIST, count=len(info.entries)))
        else:
            self.chip.setText(i18n.t(K.CHIP_FOUND, site=info.site_name,
                                     length=describe_length(info.duration)))
            self.range.set_duration(info.duration)
            self.range.set_values(0.0, info.duration)
        self.go.setEnabled(True)
        best = first_available_quality(info, self.service.draft.quality)
        if best != self.service.draft.quality:              # seçili kalite bu videoda yoksa en yükseği seç
            self.service.set_quality(best)
        self._sync_view()

    def _on_probe_failed(self, error) -> None:
        self._info = None
        self.chip.setText(i18n.t(K.CHIP_IDLE))
        self._set_error(SmartErrorCard(error))
        self.go.setEnabled(False)
        self._sync_view()

    def _set_error(self, card: Optional[SmartErrorCard]) -> None:
        _clear(self.error_box)
        if card is not None:
            self.error_box.addWidget(card)

    # -- görünüm -----------------------------------------------------------
    def _sync_view(self, *_args) -> None:
        d = self.service.draft
        blockers = (self.modes, self.qualities, self.partial, self.range)
        for w in blockers:
            w.blockSignals(True)
        try:
            self.modes.set_selected([d.mode])
            self.qualities.set_selected([d.quality])
            self.partial.setChecked(d.partial)
            if self._info is not None and d.partial:
                self.range.set_values(d.start, d.end if d.end is not None else self._info.duration)
            self.range.setEnabled(d.partial and self._info is not None)
        finally:
            for w in blockers:
                w.blockSignals(False)
        if not self.url.hasFocus() and self.url.text() != d.url:
            self.url.setText(d.url)
        self.go.setEnabled(self._info is not None)
        self._refresh_range_text()
        self._refresh_sizes()
        self.target.setText(i18n.t(K.TARGET, folder=self._folder_text()))

    def _refresh_range_text(self) -> None:
        d = self.service.draft
        info = self._info
        if info is None or not d.partial or info.is_playlist:
            self.range_text.setText(i18n.t(K.RANGE_WHOLE))
            return
        end = d.end if d.end is not None else info.duration
        length = max(end - d.start, 0.0)
        self.range_text.setText(i18n.t(K.RANGE_SUMMARY, start=format_timecode(d.start),
                                       length=describe_length(length), end=format_timecode(end)))

    def _refresh_sizes(self) -> None:
        d = self.service.draft
        info = self._info
        for card, (qid, title_key, sub_key, _icon) in zip(self.quality_cards, _QUALITY_CARDS):
            top = quality_top(info, qid)
            if top is not None:                               # bu videoda yok: kart kapalı, en yüksek kaliteyi söyler
                card.set_texts(i18n.t(title_key), unavailable_text(top), "")
                card.setEnabled(False)
                continue
            card.setEnabled(True)
            trailing = ""
            if info is not None and not info.is_playlist:
                start = d.start if d.partial else 0.0
                end = d.end if d.partial else None
                size = estimate_bytes(info, d.mode, qid, start, end)
                trailing = (i18n.t(K.SIZE_ABOUT, size=disk.human_size(size)) if size
                            else i18n.t(K.SIZE_UNKNOWN))
            card.set_texts(i18n.t(title_key), i18n.t(sub_key), trailing)

    def _folder_text(self) -> str:
        try:
            return str(self.service.default_output_dir())
        except Exception:  # noqa: BLE001 — yalnızca gösterim; bağlam yoksa boş
            return ""

    # -- indirme -----------------------------------------------------------
    def _download(self) -> None:
        info = self._info
        if info is None:
            self.status.setText(i18n.t(K.NEED_LINK))
            return
        try:
            request = self.service.build_request(duration=info.duration)
            if info.is_playlist and info.entries:
                jobs = self.service.submit_playlist(info.entries, request)
                self.status.setText(i18n.t(K.QUEUED_MANY, count=len(jobs)))
            else:
                job: Job = self.service.submit(request, title=info.title or info.url)
                self.status.setText(i18n.t(K.QUEUED_ONE, title=job.title))
        except ValueError as exc:
            self.status.setText(str(exc))
        except Exception as exc:  # noqa: BLE001 — kuyruk hatası kartla gösterilir
            self._set_error(SmartErrorCard(self.ctx.report_error(exc, {"source": "downloader"})))

    def _refresh_recent(self, *_args) -> None:
        _clear(self.recent_rows)
        rows = self.service.recent(limit=5)
        if not rows:
            empty = QLabel(i18n.t(K.RECENT_EMPTY))
            empty.setProperty("role", "muted")
            self.recent_rows.addWidget(empty)
            return
        for r in rows:
            self.recent_rows.addWidget(RecentDownloadRow(self.ctx, r))
