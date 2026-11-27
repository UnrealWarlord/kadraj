"""Legend "Recorder" sayfası (tuval: V2Capture · Legend).

Üst çubuk: ``RECORDER · WAV 48 kHz / 24-bit`` + aygıt / örnekleme / kanal seçimi + mono zaman kodu + REC ışığı + kayıt düğmesi.
Orta: dikey stereo metre + LUFS-M / LUFS-S / True Peak kutuları · NOISE GATE paneli (OPEN/BYPASS ışığı, 5 parametre, GR çubuğu) +
Auto-Gain kalibrasyonu · siyah teleprompter (WPM, ayna, yazı boyutu, kayıtla senkron).
Alt: take listesi (dalga formu, .srt durumu) + yazıya çevirme / stüdyo mastering eylemleri.
"""
from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton, QSpinBox,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import i18n
from ...core.contracts import Job
from ...ui import adaptive
from ...ui.widgets.squishy_button import SquishyButton
from .devices import SAMPLE_RATES
from .dsp import FLOOR_DB, LevelAnalysis
from .location import RecordingLocationBar, RecordingResultCard
from .page_base import RecorderPageBase, transcribe_available
from .teleprompter import MAX_FONT, MAX_WPM, MIN_FONT, MIN_WPM, Prompter
from .widgets import (BigRecordButton, GRBar, PrompterView, StatusLamp, StereoMeter, WaveformView, format_timecode)

WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
LANGUAGES = (("tr", "Türkçe"), ("en", "English"), ("auto", "Auto"))


def _panel(title: str) -> tuple:
    f = QFrame()
    f.setProperty("role", "panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 12)
    lay.setSpacing(8)
    cap = QLabel(title)
    cap.setProperty("role", "caps")
    lay.addWidget(cap)
    return f, lay


def _dbox(caption: str) -> tuple:
    f = QFrame()
    f.setProperty("role", "surface2")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(10, 6, 10, 6)
    lay.setSpacing(0)
    cap = QLabel(caption)
    cap.setProperty("role", "caps")
    val = QLabel("—")
    val.setProperty("role", "mono")
    val.setStyleSheet("font-size: 16px; font-weight: 700;")
    lay.addWidget(cap)
    lay.addWidget(val)
    return f, val


class RecorderLegendPage(RecorderPageBase):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(ctx, parent)
        self.setObjectName("recorder_legend")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self._build_header())
        root.addLayout(self.error_slot)
        mid = QHBoxLayout()
        mid.setSpacing(8)
        mid.addWidget(self._build_meters())
        mid.addWidget(self._build_gate(), 3)
        mid.addWidget(self._build_prompter(), 4)
        root.addLayout(mid, 3)
        root.addWidget(self._build_takes(), 2)
        self.connect_service()
        if self.svc is None:
            self.record_btn.setEnabled(False)
        else:
            self._sync_all()

    # ------------------------------------------------------------------------------------------------ üst çubuk
    def _build_header(self) -> QFrame:
        f = QFrame()
        f.setProperty("role", "panel")
        lay = adaptive.hbox(f)                                          # tablet dikeyde alt satıra geçer
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        self.header_label = QLabel("")
        self.header_label.setProperty("role", "caps")
        lay.addWidget(self.header_label)
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(170)
        self.device_combo.setAccessibleName(i18n.t("recorder.legend.device"))
        self.device_combo.activated.connect(self._on_device)
        lay.addWidget(self.device_combo)
        self.rate_combo = QComboBox()
        for r in SAMPLE_RATES:
            self.rate_combo.addItem(f"{r / 1000:g} kHz", r)
        self.rate_combo.setAccessibleName(i18n.t("recorder.legend.rate"))
        self.rate_combo.activated.connect(self._on_rate)
        lay.addWidget(self.rate_combo)
        self.ch_combo = QComboBox()
        self.ch_combo.addItem(i18n.t("recorder.legend.mono"), 1)
        self.ch_combo.addItem(i18n.t("recorder.legend.stereo"), 2)
        self.ch_combo.setAccessibleName(i18n.t("recorder.legend.channels"))
        self.ch_combo.activated.connect(self._on_channels)
        lay.addWidget(self.ch_combo)
        self.refresh_btn = QPushButton(i18n.t("recorder.legend.refresh"))
        self.refresh_btn.setProperty("role", "topbtn")
        self.refresh_btn.clicked.connect(self._on_refresh_devices)
        lay.addWidget(self.refresh_btn)
        lay.addStretch(1)
        self.rec_lamp = StatusLamp("REC")
        self.rec_lamp.set_state("bypass", "REC")
        lay.addWidget(self.rec_lamp)
        self.timecode = QLabel("00:00:00:00")
        self.timecode.setProperty("role", "mono")
        self.timecode.setStyleSheet("font-size: 20px; font-weight: 500;")
        lay.addWidget(self.timecode)
        self.record_btn = BigRecordButton()
        self.record_btn.set_labels("REC", "STOP")
        self.record_btn.clicked.connect(self.toggle_record)
        lay.addWidget(self.record_btn)
        return f

    # ------------------------------------------------------------------------------------------------ metre
    def _build_meters(self) -> QFrame:
        f, lay = _panel(i18n.t("recorder.legend.meter"))
        row = QHBoxLayout()
        self.meter = StereoMeter(2)
        row.addWidget(self.meter)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.box_m, self.val_m = _dbox("LUFS-M")
        self.box_s, self.val_s = _dbox("LUFS-S")
        self.box_tp, self.val_tp = _dbox("TRUE PEAK")
        for b in (self.box_m, self.box_s, self.box_tp):
            col.addWidget(b)
        self.xrun_label = QLabel("XRUN 0")
        self.xrun_label.setProperty("role", "mono")
        col.addWidget(self.xrun_label)
        col.addStretch(1)
        row.addLayout(col)
        lay.addLayout(row, 1)
        f.setFixedWidth(260)
        return f

    # ------------------------------------------------------------------------------------------------ kapı + auto-gain
    def _spin(self, lo: float, hi: float, step: float, decimals: int, suffix: str, key: str) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setSingleStep(step)
        sb.setDecimals(decimals)
        sb.setSuffix(suffix)
        sb.setProperty("gate_key", key)
        sb.setAccessibleName(i18n.t(f"recorder.gate.{key}"))
        sb.valueChanged.connect(lambda v, k=key: self.svc and self.svc.set_gate_param(k, v))
        return sb

    def _build_gate(self) -> QFrame:
        f, lay = _panel("NOISE GATE")
        top = QHBoxLayout()
        self.gate_lamp = StatusLamp("BYPASS")
        top.addWidget(self.gate_lamp)
        self.gate_check = QCheckBox(i18n.t("recorder.legend.gate_enable"))
        self.gate_check.toggled.connect(lambda on: self.svc and on != self.svc.gate_enabled and self.svc.set_gate_enabled(on))
        top.addWidget(self.gate_check)
        top.addStretch(1)
        lay.addLayout(top)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        specs = (("threshold_db", -90, 0, 1, 1, " dB"), ("attack_ms", 0.1, 100, 0.5, 1, " ms"),
                 ("hold_ms", 0, 2000, 10, 0, " ms"), ("release_ms", 1, 2000, 10, 0, " ms"),
                 ("range_db", -90, 0, 1, 0, " dB"))
        self.gate_spins = {}
        for i, (key, lo, hi, step, dec, suf) in enumerate(specs):
            lab = QLabel(i18n.t(f"recorder.gate.{key}"))
            lab.setProperty("role", "muted")
            sp = self._spin(lo, hi, step, dec, suf, key)
            grid.addWidget(lab, i, 0)
            grid.addWidget(sp, i, 1)
            self.gate_spins[key] = sp
        lay.addLayout(grid)
        gr_row = QHBoxLayout()
        gr_row.addWidget(QLabel("GR"))
        self.gr_bar = GRBar()
        gr_row.addWidget(self.gr_bar, 1)
        lay.addLayout(gr_row)
        sep = QFrame()
        sep.setProperty("role", "divider")
        lay.addWidget(sep)
        cap = QLabel(i18n.t("recorder.legend.autogain_title"))
        cap.setProperty("role", "caps")
        lay.addWidget(cap)
        gain_row = QHBoxLayout()
        gain_row.addWidget(QLabel(i18n.t("recorder.legend.gain")))
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(-20.0, 40.0)
        self.gain_spin.setSingleStep(0.5)
        self.gain_spin.setDecimals(1)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.valueChanged.connect(lambda v: self.svc and abs(v - self.svc.input_gain_db) > 1e-6
                                            and self.svc.set_input_gain_db(v))
        gain_row.addWidget(self.gain_spin)
        gain_row.addStretch(1)
        lay.addLayout(gain_row)
        self.calib_btn = SquishyButton(i18n.t("recorder.legend.autogain"), variant="secondary", icon="sliders")
        self.calib_btn.clicked.connect(self._on_calibrate)
        lay.addWidget(self.calib_btn)
        self.apply_btn = SquishyButton(i18n.t("recorder.test.apply"), variant="primary")
        self.apply_btn.clicked.connect(self._on_apply_gain)
        self.apply_btn.setVisible(False)
        lay.addWidget(self.apply_btn)
        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        self.test_label.setProperty("role", "muted")
        lay.addWidget(self.test_label)
        lay.addStretch(1)
        return f

    # ------------------------------------------------------------------------------------------------ teleprompter
    def _build_prompter(self) -> QFrame:
        f, lay = _panel("TELEPROMPTER")
        prompter = self.svc.prompter if self.svc else Prompter()
        self.view = PrompterView(prompter)
        self.view.empty_text = i18n.t("recorder.prompter.empty")
        self.view.seekRequested.connect(lambda pos: self.svc and (self.svc.prompter.seek(pos), self.view.update()))
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(i18n.t("recorder.prompter.placeholder"))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.editor)
        lay.addWidget(self.stack, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("WPM"))
        self.wpm_spin = QSpinBox()
        self.wpm_spin.setRange(MIN_WPM, MAX_WPM)
        self.wpm_spin.valueChanged.connect(self._on_wpm)
        row.addWidget(self.wpm_spin)
        row.addWidget(QLabel(i18n.t("recorder.legend.font")))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(MIN_FONT, MAX_FONT)
        self.font_spin.setSuffix(" px")
        self.font_spin.valueChanged.connect(self._on_font)
        row.addWidget(self.font_spin)
        row.addStretch(1)
        lay.addLayout(row)
        row_opts = QHBoxLayout()
        row_opts.setSpacing(14)
        self.mirror_check = QCheckBox(i18n.t("recorder.legend.mirror"))
        self.mirror_check.toggled.connect(self._on_mirror)
        self.sync_check = QCheckBox(i18n.t("recorder.legend.sync"))
        self.sync_check.toggled.connect(self._on_sync)
        row_opts.addWidget(self.mirror_check)
        row_opts.addWidget(self.sync_check)
        row_opts.addStretch(1)
        lay.addLayout(row_opts)
        row2 = QHBoxLayout()
        self.play_btn = SquishyButton(i18n.t("recorder.prompter.play"), variant="secondary")
        self.play_btn.clicked.connect(lambda: self.svc and (self.svc.prompter_toggle(), self._sync_prompter()))
        self.reset_btn = SquishyButton(i18n.t("recorder.prompter.reset"), variant="ghost")
        self.reset_btn.clicked.connect(lambda: self.svc and (self.svc.prompter_reset(), self._sync_prompter()))
        self.edit_btn = SquishyButton(i18n.t("recorder.prompter.edit"), variant="ghost")
        self.edit_btn.clicked.connect(self._on_edit)
        self.prompter_info = QLabel("")
        self.prompter_info.setProperty("role", "mono")
        for w in (self.play_btn, self.reset_btn, self.edit_btn):
            row2.addWidget(w)
        row2.addStretch(1)
        row2.addWidget(self.prompter_info)
        lay.addLayout(row2)
        return f

    # ------------------------------------------------------------------------------------------------ takes
    def _build_takes(self) -> QFrame:
        f, lay = _panel(i18n.upper(i18n.t("recorder.recent.title")))
        self.location = RecordingLocationBar()
        self.location.changeRequested.connect(self.change_recording_folder)
        lay.addWidget(self.location)
        self.result_card = RecordingResultCard()
        lay.addWidget(self.result_card)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([i18n.upper(i18n.t(f"recorder.legend.col.{c}"))
                                              for c in ("take", "duration", "rate", "wave", "srt", "studio")])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_selection)
        lay.addWidget(self.table, 1)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        lang_label = QLabel(i18n.t("recorder.legend.language"))
        bar.addWidget(lang_label)
        self.lang_combo = QComboBox()
        for code, name in LANGUAGES:
            self.lang_combo.addItem(name, code)
        self.lang_combo.activated.connect(self._on_transcribe_opts)
        bar.addWidget(self.lang_combo)
        self.model_combo = QComboBox()
        for m in WHISPER_MODELS:
            self.model_combo.addItem(m, m)
        self.model_combo.activated.connect(self._on_transcribe_opts)
        bar.addWidget(self.model_combo)
        self.transcribe_btn = SquishyButton(i18n.t("recorder.transcribe.button_short"), variant="secondary", icon="book")
        self.transcribe_btn.clicked.connect(lambda: self.request_transcribe())
        bar.addWidget(self.transcribe_btn)
        if not transcribe_available():                                  # Android: yazıya çevirme yok
            for w in (lang_label, self.lang_combo, self.model_combo, self.transcribe_btn):
                w.setVisible(False)
        bar.addSpacing(16)
        bar.addWidget(QLabel(i18n.t("recorder.legend.target_lufs")))
        self.target_spin = QDoubleSpinBox()
        self.target_spin.setRange(-30.0, -5.0)
        self.target_spin.setDecimals(1)
        self.target_spin.setSuffix(" LUFS")
        self.target_spin.valueChanged.connect(lambda v: self.svc and self.svc.set_master_target(v))
        bar.addWidget(self.target_spin)
        self.master_btn = SquishyButton(i18n.t("recorder.legend.master"), variant="primary", icon="sliders")
        self.master_btn.clicked.connect(lambda: self.request_master())
        bar.addWidget(self.master_btn)
        bar.addStretch(1)
        self.job_label = QLabel("")
        self.job_label.setProperty("role", "mono")
        bar.addWidget(self.job_label)
        lay.addLayout(bar)
        return f

    # ------------------------------------------------------------------------------------------------ olaylar
    def _on_device(self, idx: int) -> None:
        if self.svc is not None:
            self.svc.set_device(self.device_combo.itemData(idx))

    def _on_rate(self, idx: int) -> None:
        if self.svc is not None:
            self.svc.set_sample_rate(int(self.rate_combo.itemData(idx)))

    def _on_channels(self, idx: int) -> None:
        if self.svc is not None:
            self.svc.set_channels(int(self.ch_combo.itemData(idx)))

    def _on_refresh_devices(self) -> None:
        if self.svc is not None:
            self.svc.devices(refresh=True)
            self.clear_error()
            err = self.svc.availability_error()
            if err is not None:
                self.show_error(err)
            self.on_settings()

    def _on_calibrate(self) -> None:
        if self.svc is None:
            return
        self.apply_btn.setVisible(False)
        self.test_label.setText("")
        try:
            if self.svc.state == "testing":
                self.svc.cancel_mic_test()
            else:
                self.clear_error()
                self.svc.start_mic_test(calibration=True)
        except Exception:
            pass

    def _on_apply_gain(self) -> None:
        if self.svc is not None and self.svc.apply_suggested_gain() is not None:
            self.apply_btn.setVisible(False)
            self.test_label.setText(i18n.t("recorder.test.applied"))

    def _on_wpm(self, v: int) -> None:
        if self.svc is not None and v != self.svc.prompter.wpm:
            self.svc.prompter.set_speed(v)
            self.svc.save_prompter()

    def _on_font(self, v: int) -> None:
        if self.svc is not None and v != self.svc.prompter.font_px:
            self.svc.prompter.set_font(v)
            self.svc.save_prompter()
            self.view.update()

    def _on_mirror(self, on: bool) -> None:
        if self.svc is not None and on != self.svc.prompter.mirror:
            self.svc.prompter.mirror = on
            self.svc.save_prompter()
            self.view.update()

    def _on_sync(self, on: bool) -> None:
        if self.svc is not None and on != self.svc.prompter.sync:
            self.svc.prompter.sync = on
            self.svc.save_prompter()

    def _on_edit(self) -> None:
        if self.svc is None:
            return
        if self.stack.currentWidget() is self.view:
            self.editor.setPlainText(self.svc.prompter.text)
            self.stack.setCurrentWidget(self.editor)
            self.edit_btn.setText(i18n.t("recorder.prompter.done"))
        else:
            self.svc.prompter.set_text(self.editor.toPlainText())
            self.svc.save_prompter()
            self.stack.setCurrentWidget(self.view)
            self.view.set_prompter(self.svc.prompter)
            self.edit_btn.setText(i18n.t("recorder.prompter.edit"))
            self._sync_prompter()

    def _on_transcribe_opts(self, _i: int = 0) -> None:
        if self.svc is not None:
            self.svc.set_transcribe_options(self.lang_combo.currentData(), self.model_combo.currentData())

    def _on_selection(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows and self.svc is not None:
            takes = self.svc.takes()
            idx = rows[0].row()
            if idx < len(takes):
                self._selected = takes[idx].path

    # ------------------------------------------------------------------------------------------------ senkron
    def _sync_all(self) -> None:
        self.on_settings()
        self.on_takes()
        self.on_state(self.svc.state)
        self.on_tick()
        err = self.svc.availability_error()
        if err is not None:
            self.show_error(err)

    def _sync_prompter(self) -> None:
        if self.svc is None:
            return
        pr = self.svc.prompter
        for w, v in ((self.wpm_spin, pr.wpm), (self.font_spin, pr.font_px)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        for w, v in ((self.mirror_check, pr.mirror), (self.sync_check, pr.sync)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self.play_btn.setText(i18n.t("recorder.prompter.pause" if pr.running else "recorder.prompter.play"))
        self.prompter_info.setText(f"{pr.speed_name.upper()} · {pr.duration_s:.0f}s")
        self.view.update()

    def on_settings(self) -> None:
        svc = self.svc
        if svc is None:
            return
        self.refresh_location()
        self.header_label.setText(i18n.t("recorder.legend.header", rate=f"{svc.sample_rate / 1000:g}"))
        self.header_label.setMinimumWidth(self.header_label.sizeHint().width() + 6)      # kısaltılmasın
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        devices = svc.devices()
        if not devices:
            self.device_combo.addItem(i18n.t("recorder.legend.no_device"), None)
            self.device_combo.setEnabled(False)
        else:
            self.device_combo.setEnabled(True)
            for d in devices:
                self.device_combo.addItem(d.name, d.name)
            sel = svc.selected_device()
            if sel is not None:
                self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(sel.name)))
        self.device_combo.blockSignals(False)
        self.rate_combo.blockSignals(True)
        self.rate_combo.setCurrentIndex(max(0, self.rate_combo.findData(svc.sample_rate)))
        self.rate_combo.blockSignals(False)
        self.ch_combo.blockSignals(True)
        self.ch_combo.setCurrentIndex(max(0, self.ch_combo.findData(svc.channels)))
        self.ch_combo.blockSignals(False)
        self.gate_check.blockSignals(True)
        self.gate_check.setChecked(svc.gate_enabled)
        self.gate_check.blockSignals(False)
        gp = svc.gate_params
        for key, sp in self.gate_spins.items():
            sp.blockSignals(True)
            sp.setValue(getattr(gp, key))
            sp.blockSignals(False)
        self.gain_spin.blockSignals(True)
        self.gain_spin.setValue(svc.input_gain_db)
        self.gain_spin.blockSignals(False)
        self.lang_combo.blockSignals(True)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(svc.transcribe_language)))
        self.lang_combo.blockSignals(False)
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(svc.transcribe_model)))
        self.model_combo.blockSignals(False)
        self.target_spin.blockSignals(True)
        self.target_spin.setValue(svc.master_target_lufs)
        self.target_spin.blockSignals(False)
        self._sync_prompter()

    def on_state(self, state: str) -> None:
        recording, testing = state == "recording", state == "testing"
        self.record_btn.set_recording(recording)
        self.rec_lamp.set_state("closed" if recording else "bypass", "REC")
        for w in (self.device_combo, self.rate_combo, self.ch_combo, self.refresh_btn):
            w.setEnabled(not recording and not testing and (w is not self.device_combo or bool(self.svc.devices())))
        self.calib_btn.setText(i18n.t("recorder.test.stop" if testing else "recorder.legend.autogain"))
        self.calib_btn.setEnabled(not recording)
        if not recording and not testing:
            self.meter.set_levels([FLOOR_DB, FLOOR_DB])
        self._sync_prompter()
        self.on_tick()

    def on_tick(self) -> None:
        if self.svc is None:
            return
        s = self.svc.snapshot()
        self.timecode.setText(format_timecode(s.elapsed_s if s.state == "recording" else 0.0))
        active = s.state in ("recording", "testing")
        if active:
            self.meter.set_levels(s.levels.peak_db, s.levels.hold_db, s.levels.clipping)
        for lab, v in ((self.val_m, s.loudness.momentary), (self.val_s, s.loudness.short_term)):
            lab.setText("—" if v <= -100 else f"{v:.1f}")
        self.val_tp.setText("—" if s.loudness.true_peak_db <= -100 else f"{s.loudness.true_peak_db:.1f} dBTP")
        self.xrun_label.setText(f"XRUN {s.xruns}" + (f" · DROP {s.dropped_blocks}" if s.dropped_blocks else ""))
        state_txt = {"open": "OPEN", "hold": "HOLD", "closed": "CLOSED", "bypass": "BYPASS"}[s.gate_state]
        self.gate_lamp.set_state(s.gate_state, state_txt)
        self.gr_bar.set_gr(s.gr_db)
        if s.state == "testing":
            self.test_label.setText(i18n.t("recorder.status.testing", seconds=f"{s.test_remaining_s:.0f}"))
        self.view.update()
        if self.svc.prompter.running or s.prompter_running:
            self._sync_prompter()

    def on_mic_result(self, result: LevelAnalysis) -> None:
        self.test_label.setText(i18n.t(f"recorder.test.result.{result.verdict}", gain=f"{result.gain_db:+.0f}") +
                                f"  ({result.level_dbfs:.1f} dBFS · {result.peak_dbfs:.1f} dBFS pk)")
        self.apply_btn.setVisible(result.verdict != "silent" and abs(result.delta_db) >= 1.0)

    def on_takes(self) -> None:
        svc = self.svc
        if svc is None:
            return
        takes = svc.takes()
        sel = self.selected_take()
        self.table.blockSignals(True)
        self.table.setRowCount(len(takes))
        for r, t in enumerate(takes):
            name = QTableWidgetItem(t.name + (" ★" if t.is_master else ""))
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(self.fmt_duration(t.duration_s)))
            self.table.setItem(r, 2, QTableWidgetItem(f"{t.sample_rate / 1000:g}k/{t.channels}ch"))
            wave = WaveformView()
            wave.set_peaks(svc.waveform(t))
            self.table.setCellWidget(r, 3, wave)
            self.table.setItem(r, 4, QTableWidgetItem(i18n.t("recorder.take.srt_ready") if t.has_srt else "—"))
            self.table.setItem(r, 5, QTableWidgetItem(i18n.t("recorder.take.studio") if t.is_master
                                                      else ("✓" if t.master_path else "—")))
            self.table.setRowHeight(r, 30)
            if sel is not None and t.path == sel.path:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        has = bool(takes) and svc.state != "recording"
        self.transcribe_btn.setEnabled(has)
        self.master_btn.setEnabled(has)
        self.refresh_location()

    def on_waveform(self, path: str) -> None:
        if self.svc is None:
            return
        for r, t in enumerate(self.svc.takes()):
            if str(t.path) == path:
                w = self.table.cellWidget(r, 3)
                if isinstance(w, WaveformView):
                    w.set_peaks(self.svc.waveform(t))

    def on_job(self, job: Job) -> None:
        self.job_label.setText(self.job_summary(job))
