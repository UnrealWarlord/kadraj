"""Düzenleyici panelleri (Basit ve Legend sayfalarının ortak yapı taşları). Panel **durum tutmaz**: her şey ``EditorService``'te;
panel servise bağlanır, çizer ve kullanıcı eylemlerini servise iletir."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from ...core import i18n
from ...core.contracts import Job, JobStatus
from ...ui.widgets.choice import ChoiceCard, ChoiceGroup
from ...ui.widgets.preview_tooltip import PreviewTooltip
from ...ui.widgets.squishy_button import SquishyButton
from . import audio, render
from .service import JOB_RENDER, EditorService
from .timeline import Clip, format_clock, format_timecode
from .widgets import BezierEditor, CurveView, EqView


def _label(text: str, role: str = "") -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(True)
    return lb


def fmt_size(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{max(1, round(n / 1024 ** 2))} MB" if n >= 1024 ** 2 else f"{max(1, round(n / 1024))} KB"


def fmt_eta(sec: float) -> str:
    s = int(round(sec))
    return i18n.t("editor.time.sec", n=max(1, s)) if s < 60 else i18n.t("editor.time.min", n=max(1, round(s / 60)))


class ProposalBar(QFrame):
    """Otomasyon önerisi şeridi: "{özet}  [Uygula] [Vazgeç]". Öneri **kullanıcı Uygula'ya basana kadar uygulanmaz**."""

    def __init__(self, svc: EditorService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.svc = svc
        self.setProperty("role", "card-tone")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        self.text = _label("")
        self.apply_btn = SquishyButton(i18n.t("editor.proposal.apply"), variant="primary")
        self.cancel_btn = SquishyButton(i18n.t("editor.proposal.dismiss"), variant="ghost")
        lay.addWidget(self.text, 1)
        lay.addWidget(self.apply_btn)
        lay.addWidget(self.cancel_btn)
        self.apply_btn.clicked.connect(self._apply)
        self.cancel_btn.clicked.connect(self._dismiss)
        svc.proposalReady.connect(self._show)
        svc.notice.connect(self._notice)
        self.hide()

    def _show(self, name: str, prop: Any) -> None:
        self.text.setText(prop.summary)
        self.apply_btn.setVisible(not prop.empty)
        self.cancel_btn.setText(i18n.t("editor.proposal.dismiss" if not prop.empty else "editor.proposal.ok"))
        self.show()

    def _notice(self, text: str) -> None:
        self.text.setText(text)
        self.apply_btn.hide()
        self.cancel_btn.setText(i18n.t("editor.proposal.ok"))
        self.show()

    def _apply(self) -> None:
        self.svc.apply()

    def _dismiss(self) -> None:
        self.svc.dismiss_proposal()
        self.hide()


MAGIC_ORDER = ("silence", "voice", "ducking", "vitalize", "subtitles", "watermark", "freeze", "greenscreen", "reframe", "beat",
               "colormatch", "text_edit")
MAGIC_SIMPLE = ("silence", "voice", "ducking", "vitalize", "subtitles", "watermark")
MAGIC_CHIP = {"silence": "~", "voice": "wav", "ducking": "mix", "vitalize": "renk", "subtitles": "srt", "watermark": "logo",
              "freeze": "kare", "greenscreen": "AI", "reframe": "9:16", "beat": "bpm", "colormatch": "LUT", "text_edit": "txt"}


class TranscriptDialog(QDialog):
    """Metinle kurgu: döküm satırları işaretli (= kalsın); işareti kaldırdığın satırlar videodan kesilir."""

    def __init__(self, segments: Sequence[Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.t("editor.auto.text_edit"))
        self.segments = list(segments)
        lay = QVBoxLayout(self)
        lay.addWidget(_label(i18n.t("editor.transcript.hint"), "hint"))
        self.list = QListWidget()
        for sg in self.segments:
            it = QListWidgetItem(f"{format_clock(sg.start)}  {sg.text}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        self.ok_btn = SquishyButton(i18n.t("editor.transcript.cut"), variant="primary")
        self.cancel_btn = SquishyButton(i18n.t("editor.proposal.dismiss"), variant="ghost")
        row.addStretch(1)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.ok_btn)
        lay.addLayout(row)
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

    def deleted_ranges(self, offset: float = 0.0) -> List[tuple]:
        """İşareti kaldırılan satırların aralıkları (``offset`` = klibin kaynak başlangıcı)."""
        return [(sg.start + offset, sg.end + offset) for i, sg in enumerate(self.segments)
                if self.list.item(i).checkState() != Qt.CheckState.Checked]


class MagicGrid(QWidget):
    """"Sihirli dokunuşlar": her düğme bir otomasyonu **önerir** (hiçbiri kendiliğinden çalışmaz)."""

    triggered = Signal(str)

    def __init__(self, svc: EditorService, names: Sequence[str] = MAGIC_SIMPLE, columns: int = 3, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.svc = svc
        self.make_dialog: Callable[[Sequence[Any]], Any] = lambda segs: TranscriptDialog(segs, self)
        svc.transcriptReady.connect(self._on_transcript)
        self.pick_file: Callable[[], str] = lambda: QFileDialog.getOpenFileName(self, i18n.t("editor.magic.pick_logo"), "", "Images (*.png *.jpg *.jpeg *.webp)")[0]
        self.buttons: Dict[str, ChoiceCard] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        for i, name in enumerate(names):
            card = ChoiceCard(name, i18n.t(f"editor.auto.{name}"), i18n.t(f"editor.magic.{name}.desc"), "sliders", accent="mint",
                              trailing=MAGIC_CHIP.get(name, ""))
            card.setCheckable(False)
            card.clicked.connect(lambda _c=False, n=name: self.trigger(n))
            self.buttons[name] = card
            grid.addWidget(card, i // columns, i % columns)

    def trigger(self, name: str) -> None:
        self.triggered.emit(name)
        if name == "watermark":
            path = self.pick_file()
            if path:
                self.svc.propose("watermark", image_path=path)
        elif name == "text_edit":
            self.svc.transcribe_async()
        elif name == "freeze":
            self.svc.propose("freeze")
        elif name in ("vitalize", "ducking", "watermark", "reframe"):
            self.svc.propose_async(name) if name == "reframe" else self.svc.propose(name)
        else:
            self.svc.propose_async(name)


    def _on_transcript(self, cid: str, segs: Any) -> None:
        found = self.svc.tl.find_clip(cid)
        if found is None or not segs:
            return
        dlg = self.make_dialog(segs)
        if dlg.exec():
            self.svc.propose("text_edit", clip_id=cid, deleted=dlg.deleted_ranges(found[1].in_))


class PlatformGrid(QWidget):
    templateChanged = Signal(str)

    def __init__(self, columns: int = 4, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        icons = {"youtube": "film", "shorts": "clapper", "reels": "clapper", "tiktok": "clapper", "podcast": "mic",
                 "whatsapp": "download", "archive": "folder"}
        cards = [ChoiceCard(tid, i18n.t(f"editor.tpl.{tid}"), i18n.t(f"editor.tpl.{tid}.desc"), icons[tid], accent="mint")
                 for tid in render.TEMPLATE_ORDER]
        self.group = ChoiceGroup(cards, exclusive=True, columns=columns)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.group)
        self.group.set_selected(["youtube"])
        self.group.selectionChanged.connect(lambda ids: ids and self.templateChanged.emit(ids[0]))

    @property
    def template(self) -> str:
        sel = self.group.selected()
        return sel[0] if sel else "youtube"

    def set_template(self, tid: str) -> None:
        self.group.set_selected([tid])


class QualityPicker(QWidget):
    """Hızlı / Dengeli / Ağır + tahmini boyut ve süre. Legend'de kodek, hız denetimi, geçiş, fps, donanım ve LUFS eklenir."""

    changed = Signal()

    def __init__(self, svc: EditorService, pro: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.svc, self.pro = svc, pro
        self.template = "youtube"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.cards = {q: ChoiceCard(q, i18n.t(f"editor.q.{q}"), i18n.t(f"editor.q.{q}.desc"), "sliders", accent="mint") for q in render.QUALITIES}
        self.group = ChoiceGroup(list(self.cards.values()), exclusive=True, columns=3)
        self.group.set_selected(["balanced"])
        lay.addWidget(self.group)
        self.estimate_label = _label("", "hint")
        self.note_label = _label("", "hint")
        lay.addWidget(self.estimate_label)
        lay.addWidget(self.note_label)
        self.codec = self.rate = self.passes = self.fps = self.hw = None
        self.lufs: Optional[QDoubleSpinBox] = None
        if pro:
            row = QGridLayout()
            row.setSpacing(6)
            self.codec = self._combo(row, 0, "editor.pro.codec", [("h264", "H.264"), ("h265", "H.265 / HEVC"), ("prores", "ProRes 422 HQ"), ("wav", "WAV")])
            self.rate = self._combo(row, 1, "editor.pro.rate", [("vbr", "VBR"), ("cbr", "CBR")])
            self.passes = self._combo(row, 2, "editor.pro.passes", [("1", "1"), ("2", "2")])
            self.fps = self._combo(row, 3, "editor.pro.fps", [("0", i18n.t("editor.pro.auto")), ("24", "24"), ("25", "25"), ("30", "30"), ("50", "50"), ("60", "60")])
            self.hw = self._combo(row, 4, "editor.pro.hw", [("auto", i18n.t("editor.hw.auto")), ("cpu", i18n.t("editor.hw.cpu")),
                                                             ("nvenc", i18n.t("editor.hw.nvenc")), ("vaapi", i18n.t("editor.hw.vaapi"))])
            self._update_hw_items()
            svc.hardwareChanged.connect(self._update_hw_items)
            self.lufs = QDoubleSpinBox()
            self.lufs.setRange(-30.0, -5.0)
            self.lufs.setValue(-14.0)
            self.lufs.setSuffix(" LUFS")
            row.addWidget(_label(i18n.t("editor.pro.lufs"), "caps"), 5, 0)
            row.addWidget(self.lufs, 5, 1)
            lay.addLayout(row)
            for w in (self.codec, self.rate, self.passes, self.fps, self.hw):
                w.currentIndexChanged.connect(lambda _i: self._refresh())          # type: ignore[union-attr]
            self.lufs.valueChanged.connect(lambda _v: self._refresh())
        self.group.selectionChanged.connect(lambda _ids: self._refresh())
        svc.timelineChanged.connect(lambda _r: self._refresh())
        self._refresh()

    def _combo(self, grid: QGridLayout, row: int, key: str, items: Sequence[tuple]) -> QComboBox:
        cb = QComboBox()
        for data, text in items:
            cb.addItem(text, data)
        grid.addWidget(_label(i18n.t(key), "caps"), row, 0)
        grid.addWidget(cb, row, 1)
        return cb

    def _update_hw_items(self) -> None:
        """Yok olan donanım seçilemez (algılama arka planda biter; bitince burası yenilenir)."""
        if self.hw is None:
            return
        hw = self.svc.hardware()
        for i, key in enumerate(("auto", "cpu", "nvenc", "vaapi")):
            self.hw.model().item(i).setEnabled(key in ("auto", "cpu") or bool(hw.get(key)))      # type: ignore[union-attr]

    @property
    def quality(self) -> str:
        sel = self.group.selected()
        return sel[0] if sel else "balanced"

    def set_template(self, tid: str) -> None:
        self.template = tid
        if self.codec is not None:
            base = render.template_settings(tid)
            self.codec.setCurrentIndex(max(0, self.codec.findData(base.codec)))
            if self.lufs is not None and base.lufs is not None:
                self.lufs.setValue(base.lufs)
            self.group.set_selected([base.quality])
        self._refresh()

    def settings(self) -> render.RenderSettings:
        s = self.svc.template_settings(self.template, self.quality)
        if self.pro and self.codec is not None:
            s.codec = str(self.codec.currentData())
            s.rate_mode = str(self.rate.currentData())             # type: ignore[union-attr]
            s.passes = int(self.passes.currentData())              # type: ignore[union-attr]
            s.fps = float(self.fps.currentData())                  # type: ignore[union-attr]
            s.hw = str(self.hw.currentData())                      # type: ignore[union-attr]
            s.lufs = float(self.lufs.value()) if self.lufs is not None else s.lufs
        return s

    def _refresh(self) -> None:
        s = self.settings()
        est = self.svc.estimate(s)
        self.estimate_label.setText(i18n.t("editor.q.estimate", size=fmt_size(est.size_bytes), time=fmt_eta(est.seconds)) + "  ·  " + i18n.t("editor.q.estimate.note"))
        notes = []
        if s.max_seconds and self.svc.tl.duration > s.max_seconds:
            notes.append(i18n.t("editor.tpl.too_long", limit=int(s.max_seconds)))
        if s.passes == 2 and render.choose_encoder(s.codec, s.hw).backend == "vaapi":
            notes.append(i18n.t("editor.hw.note.two_pass_unsupported"))
        self.note_label.setText("  ".join(notes))
        self.changed.emit()


class ExportPanel(QWidget):
    """Platform kutucukları + kalite seçici + "Dışa Aktar" + ilerleme + "Dosyayı Klasörde Göster" (+ Legend'de iş kuyruğu)."""

    def __init__(self, svc: EditorService, pro: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.svc, self.pro = svc, pro
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(_label(i18n.t("editor.export.where"), "h2"))
        self.platforms = PlatformGrid(columns=4 if pro else 2)
        lay.addWidget(self.platforms)
        lay.addWidget(_label(i18n.t("editor.export.quality"), "h2"))
        self.quality = QualityPicker(svc, pro)
        lay.addWidget(self.quality)
        row = QHBoxLayout()
        self.export_btn = SquishyButton(i18n.t("editor.export.go"), variant="primary", icon="download", accent="mint")
        self.reveal_btn = SquishyButton(i18n.t("editor.reveal"), variant="secondary", icon="folder")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.status = _label("", "hint")
        row.addWidget(self.export_btn)
        row.addWidget(self.reveal_btn)
        row.addWidget(self.progress, 1)
        lay.addLayout(row)
        lay.addWidget(self.status)
        self.queue = QListWidget() if pro else None
        if self.queue is not None:
            self.queue.setMaximumHeight(110)
            lay.addWidget(self.queue)
        self.reveal_btn.hide()
        self.platforms.templateChanged.connect(self.quality.set_template)
        self.export_btn.clicked.connect(self.start)
        self.reveal_btn.clicked.connect(lambda: self.svc.reveal())
        svc.jobChanged.connect(self._on_job)
        svc.exportFinished.connect(self._on_finished)

    def current_settings(self) -> render.RenderSettings:
        return self.quality.settings()

    def start(self) -> Optional[Job]:
        self.reveal_btn.hide()
        job = self.svc.start_render(self.current_settings())
        if job is not None:
            self.progress.setValue(0)
            self.progress.show()
            self.status.setText(i18n.t("editor.export.running"))
        return job

    def _on_job(self, job: Job) -> None:
        if job.kind != JOB_RENDER:
            return
        self.progress.setValue(int(job.progress * 100))
        if job.status is JobStatus.CANCELLED:
            self.status.setText(i18n.t("editor.export.cancelled"))
            self.progress.hide()
        elif job.status is JobStatus.FAILED:
            self.status.setText(i18n.t("editor.export.failed"))
            self.progress.hide()
        if self.queue is not None:
            self._fill_queue()

    def _fill_queue(self) -> None:
        self.queue.clear()                                           # type: ignore[union-attr]
        for j in self.svc.jobs(JOB_RENDER):
            self.queue.addItem(f"{j.title} — {j.status.value} {int(j.progress * 100)}%")      # type: ignore[union-attr]

    def _on_finished(self, path: str) -> None:
        self.progress.setValue(100)
        self.status.setText(i18n.t("editor.export.done", name=Path(path).name))
        self.reveal_btn.show()


class LibraryPanel(QWidget):
    """Proje medya kitaplığı: liste, süzgeç, içe aktar, klasörü tara, sürükle-bırak, üzerine gelince önizleme balonu."""

    def __init__(self, svc: EditorService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.svc = svc
        self.choose_files: Callable[[], List[str]] = lambda: QFileDialog.getOpenFileNames(self, i18n.t("editor.lib.import"))[0]
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.filter = QComboBox()
        for key, text in (("", i18n.t("editor.lib.all")), ("video", i18n.t("editor.lib.video")), ("audio", i18n.t("editor.lib.audio")),
                          ("image", i18n.t("editor.lib.image"))):
            self.filter.addItem(text, key)
        self.import_btn = SquishyButton(i18n.t("editor.lib.import"), variant="secondary", icon="plus")
        self.scan_btn = SquishyButton(i18n.t("editor.lib.scan"), variant="ghost", icon="folder")
        row.addWidget(self.filter)
        row.addWidget(self.import_btn)
        row.addWidget(self.scan_btn)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.list, 1)
        self.empty = _label(i18n.t("editor.lib.empty"), "hint")
        lay.addWidget(self.empty)
        self.tip = PreviewTooltip()
        self.filter.currentIndexChanged.connect(lambda _i: self.refresh())
        self.import_btn.clicked.connect(self._import)
        self.scan_btn.clicked.connect(lambda: (self.svc.scan_project_folders(), self.refresh()))
        self.list.itemDoubleClicked.connect(self._add)
        self.list.itemEntered.connect(self._hover)
        svc.libraryChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        items = self.svc.media_items(self.filter.currentData() or None)
        for m in items:
            icon = {"video": "▶", "audio": "♪", "image": "▣"}.get(m.kind, "•")
            dur = f"  ·  {format_clock(m.duration)}" if m.duration else ""
            res = f"  ·  {m.width}×{m.height}" if m.width else ""
            it = QListWidgetItem(f"{icon}  {m.name}{dur}{res}")
            it.setData(Qt.ItemDataRole.UserRole, m.path)
            it.setToolTip(m.path)
            if not m.exists:
                it.setForeground(Qt.GlobalColor.gray)
            self.list.addItem(it)
        self.empty.setVisible(not items)

    def _import(self) -> None:
        for p in self.choose_files():
            self.svc.add_media(p)

    def _add(self, item: QListWidgetItem) -> None:
        self.svc.add_media(str(item.data(Qt.ItemDataRole.UserRole)))

    def _hover(self, item: QListWidgetItem) -> None:
        path = str(item.data(Qt.ItemDataRole.UserRole))
        m = next((x for x in self.svc.media_items() if x.path == path), None)
        if m is not None:
            sub = f"{format_clock(m.duration)}" + (f" · {m.width}×{m.height}" if m.width else "")
            self.tip.set_content(m.name, sub)
            self.tip.show_at(self.cursor().pos())

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.svc.add_media(u.toLocalFile())
        e.acceptProposedAction()


class MixerPanel(QWidget):
    """Kanal şeritleri (ses/pan/sessiz/solo/kanal modu/ducking göstergesi) + 10 bant EQ + ana ses."""

    def __init__(self, svc: EditorService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.svc = svc
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.strip_box = QHBoxLayout()
        lay.addLayout(self.strip_box)
        eq_row = QHBoxLayout()
        self.preset = QComboBox()
        for key in audio.EQ_PRESETS:
            self.preset.addItem(i18n.t(f"editor.eq.{key}"), key)
        self.master = QSlider(Qt.Orientation.Horizontal)
        self.master.setRange(-120, 60)
        self.master_label = _label("0 dB", "mono")
        eq_row.addWidget(_label(i18n.t("editor.eq.title"), "caps"))
        eq_row.addWidget(self.preset)
        eq_row.addStretch(1)
        eq_row.addWidget(_label(i18n.t("editor.mixer.master"), "caps"))
        eq_row.addWidget(self.master)
        eq_row.addWidget(self.master_label)
        lay.addLayout(eq_row)
        self.eq = EqView()
        lay.addWidget(self.eq, 1)
        self.strips: Dict[str, Dict[str, Any]] = {}
        self.preset.activated.connect(lambda _i: self.svc.set_eq(audio.EQ_PRESETS[str(self.preset.currentData())]))
        self.eq.gainsChanged.connect(self.svc.set_eq)
        self.master.valueChanged.connect(self._master)
        svc.timelineChanged.connect(lambda _r: self.rebuild())
        self.rebuild()

    def _master(self, v: int) -> None:
        self.master_label.setText(f"{v / 10:+.1f} dB")
        if not self._busy:
            self.svc.set_master_db(v / 10)

    def rebuild(self) -> None:
        self._busy = True
        while self.strip_box.count():
            w = self.strip_box.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
        self.strips = {}
        for s in audio.strips(self.svc.tl):
            box = QFrame()
            box.setProperty("role", "surface2")
            v = QVBoxLayout(box)
            v.setContentsMargins(8, 6, 8, 6)
            name = _label(s.name, "caps")
            vol = QSlider(Qt.Orientation.Vertical)
            vol.setRange(-600, 120)
            vol.setValue(int(s.volume_db * 10))
            vol.setMinimumHeight(70)
            pan = QSlider(Qt.Orientation.Horizontal)
            pan.setRange(-100, 100)
            pan.setValue(int(s.pan * 100))
            mute, solo = QPushButton("M"), QPushButton("S")
            for b, on in ((mute, s.muted), (solo, s.solo)):
                b.setCheckable(True)
                b.setChecked(on)
                b.setFixedWidth(28)
            chans = QComboBox()
            for key in ("stereo", "mono", "left", "right", "swap"):
                chans.addItem(i18n.t(f"editor.mixer.ch.{key}"), key)
            chans.setCurrentIndex(max(0, chans.findData(s.channels)))
            level = _label(f"{s.volume_db:+.1f} dB", "mono")
            duck = _label(i18n.t("editor.mixer.ducked") if s.ducked else "", "hint")
            v.addWidget(name)
            v.addWidget(vol, 1, Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(level)
            v.addWidget(pan)
            r = QHBoxLayout()
            r.addWidget(mute)
            r.addWidget(solo)
            v.addLayout(r)
            v.addWidget(chans)
            v.addWidget(duck)
            tid = s.track_id
            vol.valueChanged.connect(lambda val, t=tid, lb=level: (lb.setText(f"{val / 10:+.1f} dB"), None if self._busy else self.svc.set_track(t, merge=True, volume_db=val / 10)))
            pan.valueChanged.connect(lambda val, t=tid: None if self._busy else self.svc.set_track(t, merge=True, pan=val / 100))
            mute.toggled.connect(lambda on, t=tid: None if self._busy else self.svc.set_track(t, muted=on))
            solo.toggled.connect(lambda on, t=tid: None if self._busy else self.svc.set_track(t, solo=on))
            chans.activated.connect(lambda _i, t=tid, cb=chans: self.svc.set_track(t, channels=str(cb.currentData())))
            self.strip_box.addWidget(box)
            self.strips[tid] = {"vol": vol, "pan": pan, "mute": mute, "solo": solo, "channels": chans, "level": level, "duck": duck}
        self.master.setValue(int(self.svc.tl.master_db * 10))
        self.eq.set_gains(self.svc.tl.eq)
        self._busy = False

    def measure(self) -> Dict[str, float]:
        """Her kanalın klip tepe seviyesini (dBFS) ölçüp şeritlerde gösterir (FFmpeg ile çözer; çağıran arka plana alabilir)."""
        out: Dict[str, float] = {}
        for tr in self.svc.tl.tracks:
            peaks = [audio.clip_levels(c.src)[0] for c in tr.clips if c.src and Path(c.src).exists()]
            if peaks and tr.id in self.strips:
                out[tr.id] = max(peaks)
                self.strips[tr.id]["level"].setText(f"{out[tr.id]:+.1f} dBFS")
        return out


class MarkerPanel(QWidget):
    """İşaretçiler + YouTube bölümleri (kopyalanabilir metin ve kural uyarıları)."""

    def __init__(self, svc: EditorService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.svc = svc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.label = QLineEdit()
        self.label.setPlaceholderText(i18n.t("editor.markers.label"))
        self.chapter = QCheckBox(i18n.t("editor.markers.chapter"))
        self.add_btn = SquishyButton(i18n.t("editor.markers.add"), variant="secondary", icon="plus")
        self.del_btn = SquishyButton(i18n.t("editor.markers.remove"), variant="ghost")
        row.addWidget(self.label, 1)
        row.addWidget(self.chapter)
        row.addWidget(self.add_btn)
        row.addWidget(self.del_btn)
        lay.addLayout(row)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(90)
        self.copy_btn = SquishyButton(i18n.t("editor.markers.copy"), variant="secondary")
        self.problem = _label("", "hint")
        lay.addWidget(self.text)
        lay.addWidget(self.problem)
        lay.addWidget(self.copy_btn)
        self.add_btn.clicked.connect(lambda: (self.svc.add_marker(None, self.label.text(), self.chapter.isChecked()), self.label.clear()))
        self.del_btn.clicked.connect(self._remove)
        self.copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        self.list.itemDoubleClicked.connect(lambda it: self.svc.set_playhead(float(it.data(Qt.ItemDataRole.UserRole))))
        svc.timelineChanged.connect(lambda _r: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for m in self.svc.tl.markers:
            it = QListWidgetItem(f"{'★' if m.chapter else '•'}  {format_timecode(m.t, self.svc.tl.fps)}  {m.label}")
            it.setData(Qt.ItemDataRole.UserRole, m.t)
            self.list.addItem(it)
        self.text.setPlainText(self.svc.chapters_text())
        self.problem.setText("  ".join(i18n.t(f"editor.markers.problem.{p}") for p in self.svc.chapter_problems()))

    def _remove(self) -> None:
        it = self.list.currentItem()
        if it is not None:
            self.svc.remove_marker(float(it.data(Qt.ItemDataRole.UserRole)))


EFFECT_TYPES = ("eq", "hue", "blur", "sharpen", "denoise", "vignette", "grayscale", "hflip", "vflip", "chromakey")
KEY_PROPS = ("scale", "x", "y", "rotate", "opacity", "volume")
EASES = ("linear", "ease", "ease_in", "ease_out", "ease_in_out", "hold", "bezier")
PROP_DEFAULT = {"scale": 1.0, "x": 0.0, "y": 0.0, "rotate": 0.0, "opacity": 1.0, "volume": 1.0}


class InspectorPanel(QWidget):
    """Seçili klibin özellikleri: hız + hız eğrisi, ses/solma, anahtar kareler (Bezier), efektler ve renk, LUT."""

    def __init__(self, svc: EditorService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.svc = svc
        self._busy = False
        self.pick_lut: Callable[[], str] = lambda: QFileDialog.getOpenFileName(self, "LUT", "", "LUT (*.cube)")[0]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.title = _label(i18n.t("editor.insp.none"), "caps")
        lay.addWidget(self.title)
        g = QGridLayout()
        g.setSpacing(6)
        self.speed = self._spin(g, 0, "editor.insp.speed", 0.1, 16.0, 0.1, 2, "×")
        self.volume = self._spin(g, 1, "editor.insp.volume", -60.0, 12.0, 0.5, 1, " dB")
        self.fade_in = self._spin(g, 2, "editor.insp.fade_in", 0.0, 30.0, 0.1, 1, " sn")
        self.fade_out = self._spin(g, 3, "editor.insp.fade_out", 0.0, 30.0, 0.1, 1, " sn")
        lay.addLayout(g)
        lay.addWidget(_label(i18n.t("editor.insp.speed_curve"), "caps"))
        self.curve = CurveView()
        self.curve.setMaximumHeight(110)
        lay.addWidget(self.curve)
        crow = QHBoxLayout()
        self.slow_btn = QPushButton(i18n.t("editor.insp.ramp_slow"))
        self.fast_btn = QPushButton(i18n.t("editor.insp.ramp_fast"))
        self.reset_curve_btn = QPushButton(i18n.t("editor.insp.ramp_reset"))
        for b in (self.slow_btn, self.fast_btn, self.reset_curve_btn):
            crow.addWidget(b)
        lay.addLayout(crow)
        lay.addWidget(_label(i18n.t("editor.insp.keyframes"), "caps"))
        kr = QHBoxLayout()
        self.prop = QComboBox()
        for p in KEY_PROPS:
            self.prop.addItem(i18n.t(f"editor.prop.{p}"), p)
        self.value = QDoubleSpinBox()
        self.value.setRange(-10.0, 10.0)
        self.value.setSingleStep(0.05)
        self.value.setDecimals(3)
        self.ease = QComboBox()
        for e in EASES:
            self.ease.addItem(i18n.t(f"editor.ease.{e}"), e)
        self.add_kf = SquishyButton(i18n.t("editor.insp.add_kf"), variant="secondary")
        for w in (self.prop, self.value, self.ease, self.add_kf):
            kr.addWidget(w)
        lay.addLayout(kr)
        self.kf_list = QListWidget()
        self.kf_list.setMaximumHeight(80)
        self.bezier = BezierEditor()
        self.bezier.setMaximumHeight(130)
        self.del_kf = QPushButton(i18n.t("editor.insp.del_kf"))
        lay.addWidget(self.kf_list)
        lay.addWidget(self.bezier)
        lay.addWidget(self.del_kf)
        lay.addWidget(_label(i18n.t("editor.insp.effects"), "caps"))
        er = QHBoxLayout()
        self.fx_type = QComboBox()
        for t in EFFECT_TYPES:
            self.fx_type.addItem(i18n.t(f"editor.fx.{t}"), t)
        self.add_fx = SquishyButton(i18n.t("editor.insp.add_fx"), variant="secondary")
        self.lut_btn = SquishyButton(i18n.t("editor.insp.lut"), variant="ghost")
        for w in (self.fx_type, self.add_fx, self.lut_btn):
            er.addWidget(w)
        lay.addLayout(er)
        self.fx_list = QListWidget()
        self.fx_list.setMaximumHeight(80)
        self.del_fx = QPushButton(i18n.t("editor.insp.del_fx"))
        lay.addWidget(self.fx_list)
        lay.addWidget(self.del_fx)
        lay.addWidget(_label(i18n.t("editor.insp.color"), "caps"))
        cg = QGridLayout()
        self.color: Dict[str, QSlider] = {}
        for i, (key, lo, hi, dflt) in enumerate((("brightness", -100, 100, 0), ("contrast", 0, 200, 100), ("saturation", 0, 300, 100), ("gamma", 10, 300, 100))):
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(dflt)
            self.color[key] = sl
            cg.addWidget(_label(i18n.t(f"editor.color.{key}")), i, 0)
            cg.addWidget(sl, i, 1)
            sl.valueChanged.connect(lambda _v, k=key: self._color_changed(k))
        lay.addLayout(cg)
        lay.addStretch(1)
        self.speed.valueChanged.connect(lambda v: self._guard(lambda c: self.svc.set_speed(c.id, v)))
        self.volume.valueChanged.connect(lambda v: self._guard(lambda c: self.svc.set_clip(c.id, volume_db=v)))
        self.fade_in.valueChanged.connect(lambda v: self._guard(lambda c: self.svc.set_clip(c.id, fade_in=v)))
        self.fade_out.valueChanged.connect(lambda v: self._guard(lambda c: self.svc.set_clip(c.id, fade_out=v)))
        self.curve.pointsChanged.connect(lambda pts: self._guard(lambda c: self.svc.set_speed_curve(c.id, pts)))
        self.slow_btn.clicked.connect(lambda: self._ramp(1.0, 0.3))
        self.fast_btn.clicked.connect(lambda: self._ramp(1.0, 3.0))
        self.reset_curve_btn.clicked.connect(lambda: self._guard(lambda c: self.svc.set_speed_curve(c.id, [])))
        self.prop.currentIndexChanged.connect(lambda _i: self._on_prop())
        self.add_kf.clicked.connect(self._add_keyframe)
        self.del_kf.clicked.connect(self._del_keyframe)
        self.kf_list.currentRowChanged.connect(self._kf_selected)
        self.bezier.changed.connect(self._bezier_changed)
        self.add_fx.clicked.connect(lambda: self._guard(lambda c: self.svc.add_effect(c.id, {"type": str(self.fx_type.currentData()), "params": {}})))
        self.del_fx.clicked.connect(self._del_fx)
        self.lut_btn.clicked.connect(self._lut)
        svc.selectionChanged.connect(self.refresh)
        svc.timelineChanged.connect(lambda _r: self.refresh())
        self.refresh()

    def _spin(self, grid: QGridLayout, row: int, key: str, lo: float, hi: float, step: float, dec: int, suffix: str) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(dec)
        sp.setSuffix(suffix)
        grid.addWidget(_label(i18n.t(key)), row, 0)
        grid.addWidget(sp, row, 1)
        return sp

    def _guard(self, fn: Callable[[Clip], Any]) -> None:
        if self._busy:
            return
        c = self.svc.selected_clip
        if c is not None:
            fn(c)

    def refresh(self) -> None:
        self._busy = True
        c = self.svc.selected_clip
        enabled = c is not None
        for w in (self.speed, self.volume, self.fade_in, self.fade_out, self.curve, self.add_kf, self.add_fx, self.lut_btn, self.prop,
                  self.ease, self.value, self.kf_list, self.fx_list, self.bezier):
            w.setEnabled(enabled)
        for sl in self.color.values():
            sl.setEnabled(enabled)
        if c is None:
            self.title.setText(i18n.t("editor.insp.none"))
            self.kf_list.clear()
            self.fx_list.clear()
        else:
            self.title.setText(f"{c.name or c.kind}  ·  {format_timecode(c.start, self.svc.tl.fps)} → {format_timecode(c.end, self.svc.tl.fps)}")
            self.speed.setValue(c.speed)
            self.volume.setValue(c.volume_db)
            self.fade_in.setValue(c.fade_in)
            self.fade_out.setValue(c.fade_out)
            self.curve.set_points([(k.t, k.v) for k in c.speed_curve], c.duration)
            self._fill_keyframes(c)
            self.fx_list.clear()
            for e in c.effects:
                self.fx_list.addItem(i18n.t(f"editor.fx.{e.get('type')}") if i18n.has(f"editor.fx.{e.get('type')}") else str(e.get("type")))
            eq = next((e for e in c.effects if e.get("type") == "eq"), None)
            p = (eq or {}).get("params", {})
            for key, mult, dflt in (("brightness", 100, 0.0), ("contrast", 100, 1.0), ("saturation", 100, 1.0), ("gamma", 100, 1.0)):
                self.color[key].setValue(int(round(float(p.get(key, dflt)) * mult)))
        self._busy = False

    def _fill_keyframes(self, c: Clip) -> None:
        prop = str(self.prop.currentData())
        self.kf_list.clear()
        for k in c.keyframes.get(prop, []):
            self.kf_list.addItem(f"{k.t:.2f} s  →  {k.v:g}   ({k.ease})")

    def _on_prop(self) -> None:
        c = self.svc.selected_clip
        if c is not None:
            self._fill_keyframes(c)
            self.value.setValue(c.prop(str(self.prop.currentData()), max(0.0, self.svc.playhead - c.start), PROP_DEFAULT[str(self.prop.currentData())]))

    def _add_keyframe(self) -> None:
        c = self.svc.selected_clip
        if c is None:
            return
        ease = str(self.ease.currentData())
        self.svc.set_keyframe(c.id, str(self.prop.currentData()), max(0.0, self.svc.playhead - c.start), self.value.value(), ease,
                              tuple(self.bezier.value) if ease == "bezier" else None)

    def _kf_selected(self, row: int) -> None:
        c = self.svc.selected_clip
        if c is None or row < 0 or self._busy:
            return
        kfs = c.keyframes.get(str(self.prop.currentData()), [])
        if 0 <= row < len(kfs):
            k = kfs[row]
            self.value.setValue(k.v)
            self.ease.setCurrentIndex(max(0, self.ease.findData(k.ease)))
            if k.bezier:
                self.bezier.set_value(k.bezier)

    def _bezier_changed(self, v: tuple) -> None:
        c = self.svc.selected_clip
        row = self.kf_list.currentRow()
        if c is None or row < 0:
            return
        kfs = c.keyframes.get(str(self.prop.currentData()), [])
        if 0 <= row < len(kfs):
            k = kfs[row]
            self.svc.set_keyframe(c.id, str(self.prop.currentData()), k.t, k.v, "bezier", tuple(v))

    def _del_keyframe(self) -> None:
        c = self.svc.selected_clip
        row = self.kf_list.currentRow()
        if c is None or row < 0:
            return
        kfs = c.keyframes.get(str(self.prop.currentData()), [])
        if 0 <= row < len(kfs):
            self.svc.remove_keyframe(c.id, str(self.prop.currentData()), kfs[row].t)

    def _ramp(self, a: float, b: float) -> None:
        self._guard(lambda c: self.svc.set_speed_curve(c.id, [(0.0, a), (max(0.5, c.duration), b)]))

    def _del_fx(self) -> None:
        c, row = self.svc.selected_clip, self.fx_list.currentRow()
        if c is not None and row >= 0:
            self.svc.remove_effect(c.id, row)

    def _color_changed(self, key: str) -> None:
        if self._busy:
            return
        c = self.svc.selected_clip
        if c is None:
            return
        params = {"brightness": self.color["brightness"].value() / 100, "contrast": self.color["contrast"].value() / 100,
                  "saturation": self.color["saturation"].value() / 100, "gamma": self.color["gamma"].value() / 100}
        idx = next((i for i, e in enumerate(c.effects) if e.get("type") == "eq"), None)
        if idx is None:
            self.svc.add_effect(c.id, {"type": "eq", "params": params})
        else:
            self.svc.update_effect(c.id, idx, params)

    def _lut(self) -> None:
        c = self.svc.selected_clip
        path = self.pick_lut()
        if c is None or not path:
            return
        from . import automations
        try:
            p = automations.plan_lut(self.svc.tl, c.id, path)
        except Exception as exc:                       # geçersiz .cube / klip yok
            self.svc._fail(exc)
            return
        self.svc.apply(p)
