"""Medya İndirici — Legend Mode sayfası: mono URL, biçim tablosu, IN/OUT, seçenekler, komut önizlemesi, kuyruk.

Sayfa yalnızca görünümdür; durum ``DownloaderService``'tedir. Basit Mod'dan geçişte
taslak (URL, biçimler, aralık, seçenekler) aynen korunur.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QLabel,
                               QLayout, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import i18n
from ...core.contracts import Accent
from ...storage.disk import human_size
from ...ui.theme.manager import ThemedMixin
from ...ui.widgets import SmartErrorCard, SquishyButton
from ...ui.widgets.surface import page_surface_qss
from .range_bar import RangeBar
from .rows import RecentDownloadRow, failed_rows, first_available_quality, quality_top, unavailable_text
from .service import (MODE_AUDIO, MODE_AV, MODE_VIDEO, MODES, QUALITIES, QUALITY_HEIGHT, Options,
                      VideoInfo, command_preview, format_timecode, parse_timecode)
from .strings import K

_MODE_KEYS = {MODE_AV: K.MODE_AV_TITLE, MODE_AUDIO: K.MODE_AUDIO_TITLE, MODE_VIDEO: K.MODE_VIDEO_TITLE}
_QUALITY_LABEL = {"fast": "720p", "balanced": "1080p", "best": "4K"}
_FORMAT_COLUMNS = ("ID", "EXT", "ÇÖZ.", "FPS", "VCODEC", "ACODEC", "BOYUT", "NOT")
_STATUS_KEYS = {
    "queued": K.STATUS_QUEUED, "running": K.STATUS_RUNNING, "done": K.STATUS_DONE,
    "failed": K.STATUS_FAILED, "cancelled": K.STATUS_CANCELLED, "paused": K.STATUS_PAUSED,
}


def _mono() -> QFont:
    font = QFont()
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _clear(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _size_text(size: Optional[int]) -> str:
    return human_size(size) if size else "—"


class DownloaderLegendPage(ThemedMixin, QWidget):
    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("downloader_legend")
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
        self._refresh_queue()
        self._bind_theme()
        self._on_theme()

    def _on_theme(self) -> None:
        """Basit ya da Legend tokenlarından zemin ve tablo/liste yüzeyleri (tema değişince yenilenir)."""
        self.setStyleSheet(page_surface_qss(self.tk, "downloader_legend"))

    # -- yapı --------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setFont(_mono())
        self.url.setPlaceholderText("https://…")
        self.url.setAccessibleName("URL")
        self.list_btn = SquishyButton(i18n.t(K.BTN_LIST_FORMATS), variant="secondary")
        row.addWidget(self.url, 1)
        row.addWidget(self.list_btn)
        lay.addLayout(row)

        self.meta = QLabel("")
        self.meta.setFont(_mono())
        self.meta.setProperty("role", "mono")
        self.meta.setWordWrap(True)
        lay.addWidget(self.meta)
        self.error_box = QVBoxLayout()
        lay.addLayout(self.error_box)

        head = QLabel(i18n.t(K.LEGEND_FORMATS))
        head.setProperty("role", "h2")
        lay.addWidget(head)
        self.formats = QTableWidget(0, len(_FORMAT_COLUMNS))
        self.formats.setHorizontalHeaderLabels(list(_FORMAT_COLUMNS))
        self.formats.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.formats.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.formats.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.formats.setMinimumHeight(160)
        lay.addWidget(self.formats, 1)
        hint = QLabel(i18n.t(K.FORMAT_HEADER_HINT))
        hint.setProperty("role", "hint")
        lay.addWidget(hint)

        controls = QHBoxLayout()
        self.mode = QComboBox()
        for mode in MODES:
            self.mode.addItem(i18n.t(_MODE_KEYS[mode]), mode)
        self.quality = QComboBox()
        for q in QUALITIES:
            self.quality.addItem(f"{_QUALITY_LABEL[q]} ({QUALITY_HEIGHT[q]})", q)
        controls.addWidget(QLabel(i18n.t(K.LEGEND_MODE)))
        controls.addWidget(self.mode)
        controls.addWidget(QLabel(i18n.t(K.LEGEND_QUALITY)))
        controls.addWidget(self.quality)
        controls.addStretch(1)
        lay.addLayout(controls)

        rng = QHBoxLayout()
        self.partial = QCheckBox(i18n.t(K.LEGEND_RANGE))
        self.t_in = QLineEdit("00:00:00")
        self.t_out = QLineEdit("")
        for field in (self.t_in, self.t_out):
            field.setFont(_mono())
            field.setMaximumWidth(120)
        self.t_in.setAccessibleName("IN")
        self.t_out.setAccessibleName("OUT")
        self.range = RangeBar()
        rng.addWidget(self.partial)
        rng.addWidget(QLabel("IN"))
        rng.addWidget(self.t_in)
        rng.addWidget(QLabel("OUT"))
        rng.addWidget(self.t_out)
        lay.addLayout(rng)
        lay.addWidget(self.range)

        opts_head = QLabel(i18n.t(K.LEGEND_OPTIONS))
        opts_head.setProperty("role", "h2")
        lay.addWidget(opts_head)
        opts = QHBoxLayout()
        self.opt_subs = QCheckBox(i18n.t(K.OPT_SUBS))
        self.sub_langs = QLineEdit("tr")
        self.sub_langs.setFont(_mono())
        self.sub_langs.setMaximumWidth(140)
        self.sub_langs.setAccessibleName(i18n.t(K.LEGEND_SUB_LANGS))
        self.opt_thumb = QCheckBox(i18n.t(K.OPT_THUMB))
        self.opt_embed = QCheckBox(i18n.t(K.OPT_EMBED))
        self.opt_playlist = QCheckBox(i18n.t(K.OPT_PLAYLIST))
        self.opt_rate = QCheckBox(i18n.t(K.OPT_RATE))
        for w in (self.opt_subs, self.sub_langs, self.opt_thumb, self.opt_embed, self.opt_playlist,
                  self.opt_rate):
            opts.addWidget(w)
        opts.addStretch(1)
        lay.addLayout(opts)

        cmd_head = QLabel(i18n.t(K.LEGEND_COMMAND))
        cmd_head.setProperty("role", "h2")
        lay.addWidget(cmd_head)
        self.command = QLabel("")
        self.command.setFont(_mono())
        self.command.setProperty("role", "mono")
        self.command.setWordWrap(True)
        self.command.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.command)

        footer = QHBoxLayout()
        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.go = SquishyButton(i18n.t(K.BTN_QUEUE), variant="primary", accent=Accent.PEACH)
        footer.addWidget(self.status, 1)
        footer.addWidget(self.go)
        lay.addLayout(footer)

        q_head = QLabel(i18n.t(K.LEGEND_QUEUE))
        q_head.setProperty("role", "h2")
        lay.addWidget(q_head)
        self.queue = QTableWidget(0, 4)
        self.queue.setHorizontalHeaderLabels([i18n.t(K.QUEUE_TITLE), i18n.t(K.QUEUE_STATUS),
                                              i18n.t(K.QUEUE_PROGRESS), ""])
        self.queue.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue.setMinimumHeight(120)
        lay.addWidget(self.queue)
        self.failures = QVBoxLayout()                  # başarısız işlerin nedeni ve adımları (kuyruğun altında)
        lay.addLayout(self.failures)

    def _wire(self) -> None:
        s = self.service
        self.url.returnPressed.connect(lambda: s.probe(self.url.text()))
        self.list_btn.clicked.connect(lambda: s.probe(self.url.text()))
        self.formats.itemSelectionChanged.connect(self._on_formats)
        self.mode.currentIndexChanged.connect(self._on_mode)
        self.quality.currentIndexChanged.connect(self._on_quality)
        self.partial.toggled.connect(s.set_partial)
        self.t_in.editingFinished.connect(self._on_time_edit)
        self.t_out.editingFinished.connect(self._on_time_edit)
        self.range.valuesChanged.connect(self._on_range)
        for box in (self.opt_subs, self.opt_thumb, self.opt_embed, self.opt_playlist, self.opt_rate):
            box.toggled.connect(self._on_options)
        self.sub_langs.editingFinished.connect(self._on_options)
        self.go.clicked.connect(self._download)
        s.probeStarted.connect(self._on_probe_started)
        s.probed.connect(self._on_probed)
        s.probeFailed.connect(self._on_probe_failed)
        s.draftChanged.connect(self._sync_view)
        s.queueChanged.connect(self._refresh_queue)

    # -- olaylar -----------------------------------------------------------
    def _on_probe_started(self) -> None:
        self._info = None
        _clear(self.error_box)
        self.meta.setText(i18n.t(K.CHIP_SEARCHING))
        self.formats.setRowCount(0)

    def _on_probed(self, info: VideoInfo) -> None:
        self._info = info
        _clear(self.error_box)
        self.formats.setRowCount(0)
        if info.is_playlist:
            self.meta.setText(i18n.t(K.CHIP_PLAYLIST, count=len(info.entries)) + "  ·  " + i18n.t(K.PLAYLIST_NOTE))
            self._sync_view()
            return
        subs = ", ".join(info.subtitles) or i18n.t(K.NO_SUBS)
        self.meta.setText(i18n.t(K.META, site=info.site_name, length=format_timecode(info.duration, full=True),
                                 height=info.max_height, subs=subs))
        self.formats.setRowCount(len(info.formats))
        for r, f in enumerate(info.formats):
            cells = [f.id, f.ext, f.resolution, f"{f.fps:g}" if f.fps else "—", f.vcodec, f.acodec,
                     _size_text(f.size), f.note]
            for c, text in enumerate(cells):
                self.formats.setItem(r, c, QTableWidgetItem(text))
        self.range.set_duration(info.duration)
        self.range.set_values(0.0, info.duration)
        best = first_available_quality(info, self.service.draft.quality)
        if best != self.service.draft.quality:              # seçili kalite bu videoda yoksa en yükseği seç
            self.service.set_quality(best)
        self._sync_view()

    def _on_probe_failed(self, error) -> None:
        self._info = None
        self.meta.setText("")
        _clear(self.error_box)
        self.error_box.addWidget(SmartErrorCard(error))
        self._sync_view()

    def _on_formats(self) -> None:
        rows = sorted({i.row() for i in self.formats.selectedIndexes()})
        ids = [self.formats.item(r, 0).text() for r in rows if self.formats.item(r, 0) is not None]
        self.service.set_format_ids(ids)

    def _on_mode(self, _index: int) -> None:
        self.service.set_mode(self.mode.currentData())

    def _on_quality(self, _index: int) -> None:
        self.service.set_quality(self.quality.currentData())

    def _on_time_edit(self) -> None:
        try:
            start = parse_timecode(self.t_in.text())
            end_text = self.t_out.text().strip()
            end = parse_timecode(end_text) if end_text else None
        except ValueError:
            self.status.setText(i18n.t(K.TIMECODE_INVALID))
            return
        self.service.set_range(start, end, partial=True)
        self.partial.setChecked(True)
        if self._info is not None and end is not None:
            self.range.set_values(start, end)

    def _on_range(self, start: float, end: float) -> None:
        self.t_in.setText(format_timecode(start, full=True))
        self.t_out.setText(format_timecode(end, full=True))
        self.service.set_range(start, end)

    def _on_options(self, *_args) -> None:
        self.service.set_options(self._options())

    def _options(self) -> Options:
        langs = tuple(x.strip() for x in self.sub_langs.text().split(",") if x.strip()) or ("tr",)
        return Options(subtitles=self.opt_subs.isChecked(), sub_langs=langs,
                       thumbnail=self.opt_thumb.isChecked(), embed_thumbnail=self.opt_embed.isChecked(),
                       playlist=self.opt_playlist.isChecked(), rate_limit="5M" if self.opt_rate.isChecked() else "")

    # -- görünüm -----------------------------------------------------------
    def _sync_view(self, *_args) -> None:
        d = self.service.draft
        self._refresh_quality_items()
        for w in (self.mode, self.quality, self.partial):
            w.blockSignals(True)
        try:
            self.mode.setCurrentIndex(max(self.mode.findData(d.mode), 0))
            self.quality.setCurrentIndex(max(self.quality.findData(d.quality), 0))
            self.partial.setChecked(d.partial)
        finally:
            for w in (self.mode, self.quality, self.partial):
                w.blockSignals(False)
        if not self.url.hasFocus() and self.url.text() != d.url:
            self.url.setText(d.url)
        self.go.setEnabled(self._info is not None)
        duration = self._info.duration if self._info is not None and not self._info.is_playlist else None
        try:
            request = self.service.build_request(duration=duration, with_output=False)
            self.command.setText(command_preview(request))
        except ValueError as exc:
            self.command.setText(str(exc))

    def _refresh_quality_items(self) -> None:
        """Kalite listesi: bu videoda olmayan kalite pasif ve "Bu videoda yok (en yüksek: …)" ile işaretlenir."""
        model = self.quality.model()
        for i, q in enumerate(QUALITIES):
            top = quality_top(self._info, q)
            base = f"{_QUALITY_LABEL[q]} ({QUALITY_HEIGHT[q]})"
            self.quality.setItemText(i, base if top is None else f"{base} — {unavailable_text(top)}")
            item = model.item(i) if hasattr(model, "item") else None
            if item is not None:
                item.setEnabled(top is None)

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
                job = self.service.submit(request, title=info.title or info.url)
                self.status.setText(i18n.t(K.QUEUED_ONE, title=job.title))
        except ValueError as exc:
            self.status.setText(str(exc))
        except Exception as exc:  # noqa: BLE001 — kuyruk hatası kartla gösterilir
            self.error_box.addWidget(SmartErrorCard(self.ctx.report_error(exc, {"source": "downloader"})))

    def _refresh_queue(self, *_args) -> None:
        rows = self.service.recent(limit=20)
        self.queue.setRowCount(len(rows))
        for r, row in enumerate(rows):
            status = str(getattr(row["status"], "value", row["status"]))
            label = i18n.t(_STATUS_KEYS.get(status, K.STATUS_QUEUED))
            self.queue.setItem(r, 0, QTableWidgetItem(row["title"] or "—"))
            self.queue.setItem(r, 1, QTableWidgetItem(label))
            self.queue.setItem(r, 2, QTableWidgetItem(f"{int(row['progress'] * 100)}%"))
            if status in ("queued", "running"):
                btn = SquishyButton(i18n.t(K.BTN_CANCEL), variant="ghost")
                job_id = row["id"]
                btn.clicked.connect(lambda _c=False, jid=job_id: self.service.cancel(jid))
                self.queue.setCellWidget(r, 3, btn)
            else:
                self.queue.setCellWidget(r, 3, None)
        _clear(self.failures)
        for row in failed_rows(rows):
            self.failures.addWidget(RecentDownloadRow(self.ctx, row))
