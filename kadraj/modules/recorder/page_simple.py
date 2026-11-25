"""Basit Mod "Ses Kaydet" sayfası (tuval: V2Capture · Ses Kaydet).

Büyük pembe kayıt düğmesi + dev süre, pastel 20 segmentli VU + canlı "Sesin harika!" çipi, "Arka plandaki vızıltıyı sustur"
anahtarı + durum cümlesi, "Mikrofonu Dene", kahverengi okuma ekranı (geçmiş satırlar soluk, okunan satır tereyağı zeminli),
hız hapları, "Konuşmayı Yazıya Çevir (.srt)". Sabit 48 kHz / 24-bit ``.wav`` (``02_Audio_WAV``). Sayfa durum tutmaz.
"""
from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from ...core import i18n
from ...core.contracts import Job
from ...ui.widgets.squishy_button import SquishyButton
from .dsp import FLOOR_DB, LevelAnalysis
from .page_base import RecorderPageBase
from .takes import Take
from .teleprompter import SPEEDS
from .widgets import (BigRecordButton, PillButton, PrompterView, SegmentedVU, ToggleSwitch, WaveformView,
                      format_clock)


class _TakeRow(QFrame):
    """Kayıt listesinde bir satır: ad · süre · dalga · SRT rozeti · eylemler."""

    def __init__(self, page: "RecorderSimplePage", take: Take, selected: bool) -> None:
        super().__init__()
        self.take = take
        self.setProperty("role", "surface2" if not selected else "card-tone")
        self.setObjectName("take_row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(take.name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 14, 8)
        outer.setSpacing(4)
        lay = QHBoxLayout()
        lay.setSpacing(12)
        outer.addLayout(lay)
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(take.name)
        name.setProperty("role", "h2")
        meta = QLabel(f"{page.fmt_duration(take.duration_s)} · {take.sample_rate / 1000:g} kHz"
                      + (f" · {i18n.t('recorder.take.studio')}" if take.is_master else "")
                      + (f" · {i18n.t('recorder.take.srt_ready')}" if take.has_srt else ""))
        meta.setProperty("role", "muted")
        col.addWidget(name)
        col.addWidget(meta)
        lay.addLayout(col, 1)
        self.wave = WaveformView()
        self.wave.setMinimumWidth(60)
        lay.addWidget(self.wave, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        outer.addLayout(actions)
        self.btn_tr = SquishyButton(i18n.t("recorder.take.transcribe"), variant="ghost")
        self.btn_ms = SquishyButton(i18n.t("recorder.take.master"), variant="ghost")
        self.btn_ms.setVisible(not take.is_master)
        actions.addWidget(self.btn_tr)
        actions.addWidget(self.btn_ms)
        actions.addStretch(1)
        self.btn_tr.clicked.connect(lambda: page.request_transcribe(take))
        self.btn_ms.clicked.connect(lambda: page.request_master(take))

    def mousePressEvent(self, e) -> None:
        page = self.parent()
        while page is not None and not isinstance(page, RecorderSimplePage):
            page = page.parent()
        if page is not None:
            page.select_take(self.take.path)


class RecorderSimplePage(RecorderPageBase):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(ctx, parent)
        self.setObjectName("recorder_simple")
        self.rows: List[_TakeRow] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("Root")
        scroll.setWidget(body)
        self.grid = QGridLayout(body)
        self.grid.setContentsMargins(4, 4, 4, 12)
        self.grid.setHorizontalSpacing(20)
        self.grid.setVerticalSpacing(20)
        self._left = self._build_left()
        self._right = self._build_right()
        self._columns = 0
        self._reflow()
        self.connect_service()
        if self.svc is None:
            self.record_btn.setEnabled(False)
        else:
            self._sync_all()

    # ------------------------------------------------------------------------------------------------ sol kart
    def _card(self) -> QFrame:
        f = QFrame()
        f.setProperty("role", "card")
        return f

    def _build_left(self) -> QFrame:
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(16)

        head = QHBoxLayout()
        title = QLabel(i18n.term("mod.recorder.title"))
        title.setProperty("role", "title")
        chip = QLabel(".wav")
        chip.setProperty("role", "chip")
        head.addWidget(title)
        head.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        lay.addLayout(head)
        self.device_label = QLabel("")
        self.device_label.setProperty("role", "muted")
        lay.addWidget(self.device_label)
        lay.addLayout(self.error_slot)

        row = QHBoxLayout()
        row.setSpacing(22)
        self.record_btn = BigRecordButton()
        self.record_btn.set_labels(i18n.t("recorder.a11y.start"), i18n.t("recorder.a11y.stop"))
        self.record_btn.clicked.connect(self.toggle_record)
        row.addWidget(self.record_btn)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.status_label = QLabel("")
        self.status_label.setProperty("role", "h2")
        self.status_label.setWordWrap(True)
        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("rec_timer")
        self.timer_label.setStyleSheet("font-size: 72px; font-weight: 900; font-family: 'Nunito';")
        col.addStretch(1)
        col.addWidget(self.status_label)
        col.addWidget(self.timer_label)
        col.addStretch(1)
        row.addLayout(col, 1)
        lay.addLayout(row)

        cap = QLabel(i18n.upper(i18n.t("recorder.vu.label")))
        cap.setProperty("role", "caps")
        lay.addWidget(cap)
        vu_row = QHBoxLayout()
        self.vu = SegmentedVU(2)
        vu_row.addWidget(self.vu, 1)
        self.verdict_chip = QLabel("")
        self.verdict_chip.setProperty("role", "chip")
        self.verdict_chip.setMinimumWidth(190)
        self.verdict_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vu_row.addWidget(self.verdict_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(vu_row)

        gate = QHBoxLayout()
        gate.setSpacing(14)
        self.gate_switch = ToggleSwitch()
        self.gate_switch.toggled.connect(self._on_gate_toggled)
        gcol = QVBoxLayout()
        gcol.setSpacing(0)
        self.gate_title = QLabel(i18n.t("recorder.gate.label"))
        self.gate_title.setProperty("role", "h2")
        self.gate_hint = QLabel("")
        self.gate_hint.setProperty("role", "muted")
        gcol.addWidget(self.gate_title)
        gcol.addWidget(self.gate_hint)
        gate.addWidget(self.gate_switch)
        gate.addLayout(gcol, 1)
        lay.addLayout(gate)

        test = QHBoxLayout()
        test.setSpacing(14)
        self.test_btn = SquishyButton(i18n.t("recorder.test.button"), variant="secondary", icon="mic")
        self.test_btn.clicked.connect(self._on_test)
        self.apply_btn = SquishyButton(i18n.t("recorder.test.apply"), variant="primary", accent="mint")
        self.apply_btn.clicked.connect(self._on_apply_gain)
        self.apply_btn.setVisible(False)
        test.addWidget(self.test_btn)
        test.addWidget(self.apply_btn)
        test.addStretch(1)
        lay.addLayout(test)
        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        self.test_label.setProperty("role", "muted")
        lay.addWidget(self.test_label)

        sep = QFrame()
        sep.setProperty("role", "divider")
        lay.addWidget(sep)
        tt = QLabel(i18n.t("recorder.takes.title"))
        tt.setProperty("role", "h2")
        lay.addWidget(tt)
        self.takes_box = QVBoxLayout()
        self.takes_box.setSpacing(8)
        lay.addLayout(self.takes_box)
        self.takes_empty = QLabel(i18n.t("recorder.takes.empty"))
        self.takes_empty.setProperty("role", "muted")
        self.takes_empty.setWordWrap(True)
        lay.addWidget(self.takes_empty)
        self.transcribe_btn = SquishyButton(i18n.t("recorder.transcribe.button"), variant="primary", accent="lavender",
                                            icon="book")
        self.transcribe_btn.clicked.connect(lambda: self.request_transcribe())
        lay.addWidget(self.transcribe_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.job_label = QLabel("")
        self.job_label.setProperty("role", "muted")
        self.job_label.setWordWrap(True)
        lay.addWidget(self.job_label)
        lay.addStretch(1)
        return card

    # ------------------------------------------------------------------------------------------------ sağ kart
    def _build_right(self) -> QFrame:
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 22, 22, 22)
        lay.setSpacing(14)
        title = QLabel(i18n.t("recorder.prompter.title"))
        title.setProperty("role", "title")
        lay.addWidget(title)
        prompter = self.svc.prompter if self.svc else None
        from .teleprompter import Prompter
        self.view = PrompterView(prompter or Prompter())
        self.view.empty_text = i18n.t("recorder.prompter.empty")
        self.view.seekRequested.connect(self._on_seek)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(i18n.t("recorder.prompter.placeholder"))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.editor)
        self.stack.setMinimumHeight(340)
        lay.addWidget(self.stack, 1)

        pills = QHBoxLayout()
        pills.setSpacing(8)
        self.speed_group = QButtonGroup(self)
        self.speed_group.setExclusive(True)
        self.pills = {}
        for name in ("slow", "normal", "fast"):
            b = PillButton(i18n.t(f"recorder.speed.{name}"))
            b.setProperty("speed", name)
            self.speed_group.addButton(b)
            b.clicked.connect(lambda _=False, n=name: self._on_speed(n))
            self.pills[name] = b
            pills.addWidget(b)
        pills.addStretch(1)
        lay.addLayout(pills)
        ctl = QHBoxLayout()
        ctl.setSpacing(10)
        self.play_btn = SquishyButton(i18n.t("recorder.prompter.play"), variant="secondary")
        self.play_btn.clicked.connect(self._on_play)
        self.edit_btn = SquishyButton(i18n.t("recorder.prompter.edit"), variant="ghost")
        self.edit_btn.clicked.connect(self._on_edit)
        ctl.addWidget(self.play_btn)
        ctl.addWidget(self.edit_btn)
        ctl.addStretch(1)
        lay.addLayout(ctl)
        hint = QLabel(i18n.t("recorder.prompter.hint"))
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return card

    # ------------------------------------------------------------------------------------------------ yerleşim
    def _reflow(self) -> None:
        cols = 2 if self.width() >= 1000 else 1
        if cols == self._columns:
            return
        self._columns = cols
        while self.grid.count():
            self.grid.takeAt(0)
        if cols == 2:
            self.grid.addWidget(self._left, 0, 0)
            self.grid.addWidget(self._right, 0, 1)
            self.grid.setColumnStretch(0, 3)
            self.grid.setColumnStretch(1, 2)
        else:
            self.grid.addWidget(self._left, 0, 0)
            self.grid.addWidget(self._right, 1, 0)
            self.grid.setColumnStretch(0, 1)
            self.grid.setColumnStretch(1, 0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._reflow()

    # ------------------------------------------------------------------------------------------------ eylemler
    def _on_gate_toggled(self, on: bool) -> None:
        if self.svc is not None and on != self.svc.gate_enabled:
            self.svc.set_gate_enabled(on)
        self._sync_gate_hint()

    def _on_test(self) -> None:
        if self.svc is None:
            return
        self.apply_btn.setVisible(False)
        self.test_label.setText("")
        try:
            if self.svc.state == "testing":
                self.svc.cancel_mic_test()
            else:
                self.clear_error()
                self.svc.start_mic_test()
        except Exception:
            pass                                            # SmartError servis tarafından kart olarak gösterildi

    def _on_apply_gain(self) -> None:
        if self.svc is not None and self.svc.apply_suggested_gain() is not None:
            self.apply_btn.setVisible(False)
            self.test_label.setText(i18n.t("recorder.test.applied"))

    def _on_speed(self, name: str) -> None:
        if self.svc is not None:
            self.svc.prompter.set_speed(name)
            self.svc.save_prompter()

    def _on_play(self) -> None:
        if self.svc is not None:
            self.svc.prompter_toggle()
            self._sync_prompter_controls()

    def _on_seek(self, pos: float) -> None:
        if self.svc is not None:
            self.svc.prompter.seek(pos)
            self.view.update()

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
            self._sync_prompter_controls()

    # ------------------------------------------------------------------------------------------------ servis → sayfa
    def _sync_all(self) -> None:
        self.on_settings()
        self.on_takes()
        self.on_state(self.svc.state)
        self.on_tick()
        err = self.svc.availability_error()
        if err is not None:
            self.show_error(err)

    def _sync_gate_hint(self) -> None:
        if self.svc is None:
            return
        snap = self.svc.snapshot()
        if not self.svc.gate_enabled:
            key = "recorder.gate.hint_off"
        elif snap.state == "idle":
            key = "recorder.gate.hint_on_idle"
        elif snap.gate_state in ("open", "hold"):
            key = "recorder.gate.hint_open"
        else:
            key = "recorder.gate.hint_closed"
        self.gate_hint.setText(i18n.t(key))

    def _sync_prompter_controls(self) -> None:
        if self.svc is None:
            return
        pr = self.svc.prompter
        for name, b in self.pills.items():
            b.setChecked(pr.speed_name == name)
        self.play_btn.setText(i18n.t("recorder.prompter.pause" if pr.running else "recorder.prompter.play"))
        self.view.update()

    def on_settings(self) -> None:
        if self.svc is None:
            return
        self.device_label.setText(self.device_line())
        self.gate_switch.blockSignals(True)
        self.gate_switch.setChecked(self.svc.gate_enabled)
        self.gate_switch.blockSignals(False)
        self._sync_gate_hint()
        self._sync_prompter_controls()

    def on_state(self, state: str) -> None:
        recording, testing = state == "recording", state == "testing"
        self.record_btn.set_recording(recording)
        self.status_label.setText(i18n.t("recorder.status.recording" if recording else "recorder.status.ready"))
        self.test_btn.setText(i18n.t("recorder.test.stop" if testing else "recorder.test.button"))
        self.test_btn.setEnabled(not recording)
        self.transcribe_btn.setEnabled(not recording and self.selected_take() is not None)
        if not recording and not testing:
            self.vu.set_levels([FLOOR_DB, FLOOR_DB])
            self._set_verdict("idle")
        self._sync_prompter_controls()
        self.on_tick()                                          # süre/kapı cümlesi durumla eşit olsun

    def on_tick(self) -> None:
        if self.svc is None:
            return
        snap = self.svc.snapshot()
        self.timer_label.setText(format_clock(snap.elapsed_s if snap.state == "recording" else 0.0))
        if snap.state in ("recording", "testing"):
            self.vu.set_levels(snap.levels.peak_db)
            self._set_verdict(snap.verdict)
            if snap.state == "testing":
                self.status_label.setText(i18n.t("recorder.status.testing", seconds=f"{snap.test_remaining_s:.0f}"))
        self._sync_gate_hint()
        self.view.update()
        if self.svc.prompter.running or snap.prompter_running:
            self._sync_prompter_controls()

    def _set_verdict(self, verdict: str) -> None:
        self.verdict_chip.setText(i18n.t(f"recorder.verdict.{verdict}"))
        self.verdict_chip.setProperty("verdict", verdict)

    @property
    def verdict(self) -> str:
        return str(self.verdict_chip.property("verdict") or "idle")

    def on_mic_result(self, result: LevelAnalysis) -> None:
        self.test_label.setText(i18n.t(f"recorder.test.result.{result.verdict}",
                                       gain=f"{result.gain_db:+.0f}"))
        self.apply_btn.setVisible(result.verdict in ("quiet", "loud", "good", "clipping")
                                  and abs(result.delta_db) >= 1.0)

    def on_takes(self) -> None:
        if self.svc is None:
            return
        takes = [t for t in self.svc.takes()][:5]
        for r in self.rows:
            r.hide()
            self.takes_box.removeWidget(r)
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        sel = self.selected_take()
        for t in takes:
            row = _TakeRow(self, t, selected=sel is not None and t.path == sel.path)
            peaks = self.svc.waveform(t)
            row.wave.set_peaks(peaks)
            self.takes_box.addWidget(row)
            self.rows.append(row)
        self.takes_empty.setVisible(not takes)
        self.transcribe_btn.setEnabled(bool(takes) and self.svc.state != "recording")

    def on_waveform(self, path: str) -> None:
        for r in self.rows:
            if str(r.take.path) == path:
                r.wave.set_peaks(self.svc.waveform(r.take))

    def on_job(self, job: Job) -> None:
        self.job_label.setText(self.job_summary(job))
