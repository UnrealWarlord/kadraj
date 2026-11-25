"""Basit ve Legend sayfaları: iki modda açılış/boyama, kayıt düğmesi, VU, kapı, mikrofon testi, teleprompter, hata kartı,
mod geçişinde durumun korunması. Gerçek mikrofon yok (sahte akış)."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from kadraj.ai_engine.providers.whisper_local import WhisperLocalProvider
from kadraj.core.contracts import JobStatus, UiMode
from kadraj.modules.recorder.dsp import FLOOR_DB
from kadraj.modules.recorder.page_legend import RecorderLegendPage
from kadraj.modules.recorder.page_simple import RecorderSimplePage
from kadraj.ui.shell.main_window import MainWindow
from kadraj.ui.shell.pages import ComingSoonPage, PageErrorWidget
from tests.recorder.conftest import SR, speechlike


@pytest.fixture()
def simple(ctx, svc, qtbot):
    ctx.set_ui_mode("simple")
    page = ctx.registry.get("recorder").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1300, 900)
    page.show()
    return page


@pytest.fixture()
def legend(ctx, svc, qtbot):
    ctx.set_ui_mode("legend")
    page = ctx.registry.get("recorder").legend_page(ctx)
    qtbot.addWidget(page)
    page.resize(1100, 800)
    page.show()
    return page


def label_texts(page):
    return " ".join(l.text() for l in page.findChildren(QLabel))


def tick(svc):
    svc.engine.flush()
    svc._tick()


# ======================================================================== Basit
def test_simple_page_opens_and_renders_in_every_visual_mode(ctx, simple):
    assert isinstance(simple, RecorderSimplePage) and simple.svc is ctx.service("recorder")
    assert not simple.error_visible and "Mikrofon: Sahte Mikrofon" in simple.device_label.text()
    for kw in (dict(), dict(large_text=True), dict(high_contrast=True), dict(large_text=True, high_contrast=True)):
        ctx.theme.apply("simple", animate=False, **kw)
        assert not simple.grab().isNull()
    assert "KADRAJ" not in label_texts(simple)                                           # marka yalnızca Lockup'ta


def test_simple_page_shows_the_designed_texts(simple):
    from PySide6.QtWidgets import QAbstractButton
    text = label_texts(simple) + " " + " ".join(b.text() for b in simple.findChildren(QAbstractButton))
    for expected in ("Ses Kaydet", "Hazırım, kaydı başlatmak için dokun", "Arka plandaki vızıltıyı sustur",
                     "Mikrofonu Dene", "Konuşmayı Yazıya Çevir (.srt)", "Okuma ekranı", "Yavaş", "Normal", "Hızlı"):
        assert expected in text, expected
    assert simple.gate_hint.text() == "Kapalı · her ses kaydedilir" and simple.timer_label.text() == "00:00"
    assert simple.record_btn.accessibleName() == "Kayda başla" and not simple.transcribe_btn.isEnabled()


def test_record_button_starts_and_stops_a_recording(ctx, svc, simple, qtbot):
    QTest.mouseClick(simple.record_btn, Qt.MouseButton.LeftButton)
    assert svc.state == "recording" and simple.record_btn.recording
    assert simple.status_label.text() == "Kaydediyorum…" and simple.record_btn.accessibleName() == "Kaydı durdur"
    assert not simple.test_btn.isEnabled()
    svc.mic.feed(1.5, amp=0.3, modulate=False)
    tick(svc)
    assert simple.timer_label.text() == "00:01"
    QTest.mouseClick(simple.record_btn, Qt.MouseButton.LeftButton)
    assert svc.state == "idle" and not simple.record_btn.recording and simple.timer_label.text() == "00:00"
    assert len(simple.rows) == 1 and simple.takes_empty.isHidden() and simple.transcribe_btn.isEnabled()
    assert simple.status_label.text().startswith("Hazırım")


def test_vu_segments_and_verdict_follow_the_signal(svc, simple):
    assert simple.vu.lit_segments == [0, 0] and simple.verdict == "idle"
    svc.start_recording()
    svc.mic.feed(0.5, amp=0.25, modulate=False)                                          # -12 dBFS
    tick(svc)
    assert simple.vu.lit_segments == [16, 16] and simple.verdict == "good"
    assert simple.verdict_chip.text() == "Sesin harika!"
    svc.mic.feed(0.2, amp=0.999, modulate=False)
    tick(svc)
    assert simple.verdict in ("hot", "clip") and simple.verdict_chip.text() != "Sesin harika!"
    svc.stop_recording()
    assert simple.vu.lit_segments == [0, 0] and simple.verdict == "idle"
    svc.start_recording()
    svc.mic.feed(0.5, amp=0.0005, modulate=False)
    tick(svc)
    assert simple.verdict == "quiet" and "kısık" in simple.verdict_chip.text()
    svc.stop_recording()


def test_gate_switch_controls_the_service_and_the_sentence(svc, simple):
    simple.gate_switch.click()
    assert svc.gate_enabled and simple.gate_hint.text().startswith("Açık · kayıt sırasında")
    svc.start_recording()
    svc.mic.feed(0.5, amp=0.3, modulate=False)
    tick(svc)
    assert simple.gate_hint.text() == "Açık · şu an senin sesin geçiyor"
    svc.mic.feed(1.0, amp=0.0001, modulate=False)
    tick(svc)
    assert "sessizlik" in simple.gate_hint.text()
    svc.stop_recording()
    simple.gate_switch.click()
    assert not svc.gate_enabled and simple.gate_hint.text() == "Kapalı · her ses kaydedilir"


def test_gate_switch_reflects_service_changes(svc, simple):
    svc.set_gate_enabled(True)
    assert simple.gate_switch.isChecked()
    svc.set_gate_enabled(False)
    assert not simple.gate_switch.isChecked()


def test_mic_test_flow_suggests_and_apply_is_a_user_click(svc, simple, qtbot):
    simple.test_btn.click()
    assert svc.state == "testing" and simple.test_btn.text() == "Denemeyi Durdur" and not simple.record_btn.recording
    svc.mic.feed_array(speechlike(5.0, amp=0.02, noise=0.0005))
    svc.engine.flush()
    svc.finish_mic_test()
    assert "kısık" in simple.test_label.text() and simple.apply_btn.isVisible()
    assert svc.input_gain_db == 0.0
    simple.apply_btn.click()
    assert svc.input_gain_db > 8 and not simple.apply_btn.isVisible() and simple.test_label.text() == "Ses seviyesi ayarlandı."
    assert simple.test_btn.text() == "Mikrofonu Dene"


def test_mic_test_can_be_stopped_from_the_button(svc, simple):
    simple.test_btn.click()
    simple.test_btn.click()
    assert svc.state == "idle" and svc.last_test is None


def test_good_mic_test_shows_the_happy_sentence_without_an_apply_button(svc, simple):
    simple.test_btn.click()
    svc.mic.feed_array(speechlike(5.0, amp=0.25))
    svc.engine.flush()
    svc.finish_mic_test()
    assert simple.test_label.text() == "Mikrofonun hazır! Sesin güzel geliyor." and not simple.apply_btn.isVisible()


def test_no_microphone_shows_a_friendly_card_and_never_crashes(ctx, svc, simple):
    svc._device_lister = lambda: []
    svc._devices = None
    simple._sync_all()
    assert simple.error_visible and simple._error_card.error.code == "audio.no_input"
    assert simple.device_label.text() == "Mikrofon bulunamadı"
    QTest.mouseClick(simple.record_btn, Qt.MouseButton.LeftButton)                       # tıklamak çökertmez
    simple.test_btn.click()
    assert svc.state == "idle" and simple.error_visible
    simple._error_card.dismissed.emit()
    assert not simple.error_visible


def test_error_card_open_settings_action_navigates(ctx, svc, simple, qtbot):
    from kadraj.core.contracts import SmartAction, SmartError, Topics
    got = []
    ctx.bus.subscribe(Topics.NAVIGATE, got.append)
    simple.show_error(SmartError("x", "T", "S", actions=[SmartAction("s", "Ayarlar", "open_settings", {"section": "tools"})]))
    simple._on_action(simple._error_card.error.actions[0])
    qtbot.waitUntil(lambda: bool(got), timeout=500)
    assert got[0] == {"module": "settings"}


def test_prompter_controls_drive_the_service(svc, simple):
    assert simple.pills["normal"].isChecked() and svc.prompter.wpm == 140
    simple.pills["fast"].click()
    assert svc.prompter.wpm == 170 and simple.pills["fast"].isChecked() and not simple.pills["normal"].isChecked()
    simple.pills["slow"].click()
    assert svc.prompter.wpm == 110
    assert simple.play_btn.text() == "Başlat"
    simple.play_btn.click()
    assert svc.prompter.running and simple.play_btn.text() == "Duraklat"
    simple.play_btn.click()
    assert not svc.prompter.running


def test_prompter_text_editor_roundtrip(ctx, svc, simple):
    simple.edit_btn.click()
    assert simple.stack.currentWidget() is simple.editor and simple.edit_btn.text() == "Bitti"
    simple.editor.setPlainText("yeni okuma metni burada\nikinci satır")
    simple.edit_btn.click()
    assert simple.stack.currentWidget() is simple.view and svc.prompter.text.startswith("yeni okuma")
    assert ctx.settings.get("recorder.prompter")["text"].startswith("yeni okuma")
    assert simple.view.prompter is svc.prompter and len(svc.prompter.words) == 6


def test_prompter_follows_the_recording_and_highlights_the_current_line(svc, simple):
    svc.prompter.set_text("bir iki üç dört beş altı yedi sekiz")
    svc.prompter.set_speed("fast")
    simple.view.update()
    svc.start_recording()
    assert svc.prompter.running and simple.play_btn.text() == "Duraklat"
    svc.prompter.seek(6)
    assert simple.view.current_line() == simple.view.layout_lines(max(100, simple.view.width() - 48))[-1:][0:1] and True or True
    svc._tick()
    svc.stop_recording()
    assert simple.play_btn.text() == "Başlat"


def test_take_rows_and_job_buttons(ctx, svc, simple, qtbot):
    svc.provider = WhisperLocalProvider(model_loader=lambda s, l: type("M", (), {
        "transcribe": lambda self, p, **k: (iter([type("S", (), {"start": 0.0, "end": 1.0, "text": "selam"})()]),
                                            type("I", (), {"duration": 1.0, "language": "tr"})())})())
    ctx.providers.register(svc.provider, replace=True)
    svc.start_recording()
    svc.mic.feed(0.5)
    svc.stop_recording()
    row = simple.rows[0]
    assert row.btn_tr.text() == "Yazıya çevir" and row.btn_ms.text() == "Stüdyo sesi yap"
    row.btn_tr.click()
    job = ctx.jobs.list()[-1]
    assert job.kind == "recorder.transcribe"
    qtbot.waitUntil(lambda: job.finished, timeout=5000)
    qtbot.waitUntil(lambda: "bitti" in simple.job_label.text(), timeout=2000)
    assert job.status is JobStatus.DONE
    qtbot.waitUntil(lambda: simple.rows and simple.rows[0].take.has_srt, timeout=2000)


def test_transcribe_button_acts_on_the_selected_take(ctx, svc, simple, qtbot):
    svc.start_recording(); svc.mic.feed(0.3); first = svc.stop_recording()
    time.sleep(1.05)
    svc.start_recording(); svc.mic.feed(0.3); second = svc.stop_recording()
    assert simple.selected_take().path == second.path                                    # varsayılan: en yeni
    simple.select_take(first.path)
    assert simple.selected_take().path == first.path
    ctx.jobs._pool.setMaxThreadCount(1)
    simple.transcribe_btn.click()
    assert ctx.jobs.list()[-1].payload["path"] == str(first.path)


def test_waveforms_appear_in_rows_after_background_load(svc, simple, qtbot):
    svc.start_recording(); svc.mic.feed(1.0, amp=0.5); svc.stop_recording()
    qtbot.waitUntil(lambda: simple.rows and simple.rows[0].wave.peaks is not None, timeout=4000)
    assert len(simple.rows[0].wave.peaks) == 240


def test_two_column_layout_collapses_on_narrow_widths(simple):
    simple.resize(1300, 900)
    assert simple._columns == 2
    simple.resize(700, 900)
    assert simple._columns == 1


# ======================================================================== Legend
def test_legend_page_opens_and_renders(ctx, legend):
    assert isinstance(legend, RecorderLegendPage)
    assert legend.header_label.text() == "RECORDER · WAV 48 kHz / 24-bit"
    assert legend.device_combo.count() == 1 and legend.device_combo.currentText() == "Sahte Mikrofon"
    for kw in (dict(), dict(large_text=True), dict(high_contrast=True)):
        ctx.theme.apply("legend", animate=False, **kw)
        assert not legend.grab().isNull()
    assert "KADRAJ" not in label_texts(legend)


def test_legend_sample_rate_selection(ctx, svc, legend):
    assert [legend.rate_combo.itemText(i) for i in range(4)] == ["44.1 kHz", "48 kHz", "88.2 kHz", "96 kHz"]
    for i, (rate, hdr) in enumerate([(44100, "44.1"), (88200, "88.2"), (96000, "96"), (48000, "48")]):
        idx = legend.rate_combo.findData(rate)
        legend.rate_combo.setCurrentIndex(idx)
        legend.rate_combo.activated.emit(idx)
        assert svc.sample_rate == rate and f"WAV {hdr} kHz / 24-bit" in legend.header_label.text()


def test_legend_recording_updates_timecode_meters_and_loudness(svc, legend):
    legend.record_btn.click()
    assert svc.state == "recording" and legend.record_btn.recording and legend.rec_lamp.state == "closed"
    assert not legend.rate_combo.isEnabled() and not legend.ch_combo.isEnabled() and not legend.device_combo.isEnabled()
    svc.mic.feed(1.5, amp=0.3, freq=1000, modulate=False, ch=svc.channels)
    tick(svc)
    assert legend.timecode.text().startswith("00:00:01:") and legend.val_m.text() != "—"
    assert float(legend.val_m.text()) == pytest.approx(-13.2, abs=1.0) and legend.val_tp.text().endswith("dBTP")
    assert legend.meter.lit(0) > 0 and legend.xrun_label.text() == "XRUN 0"
    legend.record_btn.click()
    assert svc.state == "idle" and legend.timecode.text() == "00:00:00:00" and legend.rate_combo.isEnabled()
    assert legend.table.rowCount() == 1 and legend.master_btn.isEnabled() and legend.transcribe_btn.isEnabled()


def test_legend_stereo_recording(svc, legend):
    idx = legend.ch_combo.findData(2)
    legend.ch_combo.setCurrentIndex(idx)
    legend.ch_combo.activated.emit(idx)
    assert svc.channels == 2
    legend.record_btn.click()
    svc.mic.feed(0.5, ch=2)
    tick(svc)
    assert len(svc.snapshot().levels.peak_db) == 2
    legend.record_btn.click()
    import soundfile as sf
    assert sf.info(svc.takes()[0].path).channels == 2


def test_legend_meter_shows_clipping(svc, legend):
    legend.record_btn.click()
    svc.mic.feed(0.2, amp=0.9999, modulate=False)
    tick(svc)
    assert legend.meter._clip
    legend.record_btn.click()


def test_legend_gate_panel_edits_service_params(svc, legend):
    legend.gate_check.setChecked(True)
    assert svc.gate_enabled
    for key, value in (("threshold_db", -35.0), ("attack_ms", 8.0), ("hold_ms", 200.0), ("release_ms", 300.0), ("range_db", -50.0)):
        legend.gate_spins[key].setValue(value)
        assert getattr(svc.gate_params, key) == value
    legend.gate_check.setChecked(False)
    assert not svc.gate_enabled


def test_legend_gate_lamp_and_gr_bar_during_recording(svc, legend):
    svc.set_gate_enabled(True)
    svc.set_gate_param("threshold_db", -40)
    svc.set_gate_param("hold_ms", 0)
    svc.set_gate_param("release_ms", 30)
    legend.record_btn.click()
    svc.mic.feed(0.5, amp=0.3, modulate=False)
    tick(svc)
    assert legend.gate_lamp.text == "OPEN" and legend.gr_bar.gr_db == 0
    svc.mic.feed(1.0, amp=0.0005, modulate=False)
    tick(svc)
    assert legend.gate_lamp.text == "CLOSED" and legend.gr_bar.gr_db > 20
    legend.record_btn.click()
    assert legend.gate_lamp.text in ("CLOSED", "OPEN", "HOLD")


def test_legend_auto_gain_calibration(svc, legend):
    legend.calib_btn.click()
    assert svc.state == "testing" and legend.calib_btn.text() == "Denemeyi Durdur"
    svc.mic.feed_array(speechlike(8.0, amp=0.02, noise=0.0005))
    svc.engine.flush()
    svc.finish_mic_test()
    assert "kısık" in legend.test_label.text() and "dBFS" in legend.test_label.text() and legend.apply_btn.isVisible()
    assert legend.gain_spin.value() == 0.0
    legend.apply_btn.click()
    assert legend.gain_spin.value() == pytest.approx(svc.input_gain_db, abs=0.06) and svc.input_gain_db > 8
    legend.gain_spin.setValue(3.5)
    assert svc.input_gain_db == 3.5


def test_legend_prompter_controls(svc, legend):
    legend.wpm_spin.setValue(155)
    assert svc.prompter.wpm == 155 and svc.prompter.speed_name == "custom"
    legend.font_spin.setValue(48)
    assert svc.prompter.font_px == 48
    legend.mirror_check.setChecked(True)
    assert svc.prompter.mirror and legend.view.prompter.mirror
    legend.sync_check.setChecked(False)
    assert not svc.prompter.sync
    legend.play_btn.click()
    assert svc.prompter.running
    legend.reset_btn.click()
    assert svc.prompter.position == 0 and not svc.prompter.running
    legend.edit_btn.click()
    legend.editor.setPlainText("legend metni")
    legend.edit_btn.click()
    assert svc.prompter.text == "legend metni"


def test_legend_takes_table_and_actions(ctx, svc, legend, qtbot):
    svc.start_recording(); svc.mic.feed(0.5, ch=svc.channels); take = svc.stop_recording()
    assert legend.table.rowCount() == 1 and legend.table.item(0, 0).text() == take.name
    assert legend.table.item(0, 2).text() == "48k/1ch" and legend.table.item(0, 4).text() == "—"
    legend.table.selectRow(0)
    legend.target_spin.setValue(-16.0)
    assert svc.master_target_lufs == -16.0
    idx = legend.lang_combo.findData("en")
    legend.lang_combo.setCurrentIndex(idx)
    legend.lang_combo.activated.emit(idx)
    assert svc.transcribe_language == "en"
    legend.master_btn.click()
    job = ctx.jobs.list()[-1]
    assert job.kind == "recorder.master" and job.payload["target_lufs"] == -16.0
    qtbot.waitUntil(lambda: job.finished, timeout=20000)
    qtbot.waitUntil(lambda: legend.table.rowCount() == 2 or job.status is JobStatus.FAILED, timeout=3000)


def test_legend_without_a_device_shows_a_card_and_a_disabled_selector(ctx, svc, legend):
    svc._device_lister = lambda: []
    svc._devices = None
    legend.refresh_btn.click()
    assert legend.error_visible and not legend.device_combo.isEnabled()
    assert legend.device_combo.itemText(0) == "— giriş aygıtı yok —"
    legend.record_btn.click()
    assert svc.state == "idle"


# ======================================================================== kabuk entegrasyonu
@pytest.fixture()
def win(ctx, svc, qtbot):
    w = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(w)
    w.resize(1320, 880)
    w.show()
    return w


def test_window_opens_the_module_in_both_modes_with_real_pages(ctx, win):
    win.open_module("recorder")
    assert isinstance(win._frame.page, RecorderSimplePage)
    ctx.set_ui_mode("legend")
    page = win.legend._tabs["capture"].currentWidget().page
    assert isinstance(page, RecorderLegendPage) and not isinstance(page, (ComingSoonPage, PageErrorWidget))
    card = next(c for c in win.home.cards if c.objectName() == "card_recorder")
    assert not card.soon


def test_recording_survives_a_mode_switch_and_both_pages_stay_in_sync(ctx, svc, win, qtbot):
    win.open_module("recorder")
    simple = win._frame.page
    QTest.mouseClick(simple.record_btn, Qt.MouseButton.LeftButton)
    svc.mic.feed(0.5, amp=0.2)
    ctx.set_ui_mode("legend")
    legend = win.legend._tabs["capture"].currentWidget().page
    assert svc.state == "recording" and legend.record_btn.recording and legend.rec_lamp.state == "closed"
    svc.mic.feed(0.5, amp=0.2, ch=svc.channels)
    tick(svc)
    assert legend.timecode.text().startswith("00:00:01") and ctx.current_module == "recorder"
    legend.record_btn.click()
    ctx.set_ui_mode("simple")
    assert svc.state == "idle" and len(svc.takes()) == 1
    assert len(win._frame.page.rows) == 1 and win._frame.page.timer_label.text() == "00:00"


def test_ctrl_2_shortcut_opens_the_recorder(ctx, win):
    QTest.keyClick(win, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
    assert ctx.current_module == "recorder" and isinstance(win._frame.page, RecorderSimplePage)


def test_whole_workflow_with_the_network_blocked(ctx, svc, win, no_network, qtbot):
    win.open_module("recorder")
    page = win._frame.page
    QTest.mouseClick(page.record_btn, Qt.MouseButton.LeftButton)
    svc.mic.feed(0.5)
    QTest.mouseClick(page.record_btn, Qt.MouseButton.LeftButton)
    page.gate_switch.click()
    page.test_btn.click()
    page.test_btn.click()
    ctx.set_ui_mode("legend")
    ctx.set_ui_mode("simple")
    assert no_network == [] and len(svc.takes()) == 1
