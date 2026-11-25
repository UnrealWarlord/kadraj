"""RecorderService: kayıt akışı, mod kuralları, hata yolları, mikrofon testi, işler (yazıya çevirme/mastering), kapanış."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QCoreApplication

from kadraj.ai_engine.providers.whisper_local import WhisperLocalProvider
from kadraj.core.contracts import JobStatus, ServiceBase, SmartError, Topics, UiMode
from kadraj.modules.recorder import service as S
from kadraj.modules.recorder.devices import InputDevice
from kadraj.modules.recorder.dsp import SIMPLE_GATE, GateParams
from kadraj.storage.folders import folder_for
from tests.recorder.conftest import SR, FakeMic, speechlike

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg kurulu değil")


def record(svc, seconds=1.0, **feed):
    path = svc.start_recording()
    svc.mic.feed(seconds, ch=svc.channels, sr=svc.sample_rate, **feed)
    take = svc.stop_recording()
    return path, take


def wait_job(qtbot, job, timeout=8000):
    qtbot.waitUntil(lambda: job.finished, timeout=timeout)
    qtbot.wait(30)
    return job


# ---- kayıt akışı --------------------------------------------------------------------------------------------------
def test_registered_via_the_module_registry(ctx):
    spec = ctx.registry.get("recorder")
    assert spec is not None and spec.accent.value == "lavender" and spec.chip == ".wav" and spec.shortcut == "Ctrl+2"
    assert spec.workspace == "capture" and spec.icon == "mic" and spec.validate() == []
    assert ctx.service("recorder") is ctx.service("recorder")                      # tek örnek


def test_service_contract_shape(svc):
    assert svc.name == "recorder" and callable(svc.start) and callable(svc.stop)


def test_simple_mode_records_a_48k_24bit_wav_into_02_audio_wav(ctx, svc):
    assert ctx.ui_mode is UiMode.SIMPLE
    ctx.settings.set("recorder.sample_rate", 96000)                                 # Legend'den kalma ayar Basit'i etkilemez
    path, take = record(svc, 1.0, amp=0.3)
    assert path.suffix == ".wav" and path.parent == ctx.project.root / folder_for("wav") == ctx.project.root / "02_Audio_WAV"
    info = sf.info(path)
    assert (info.samplerate, info.subtype, info.channels) == (48000, "PCM_24", 1)
    assert take is not None and take.path == path and take.duration_s == pytest.approx(1.0, abs=0.05)
    assert svc.state == "idle" and not svc.engine.running                          # mikrofon bırakıldı


@pytest.mark.parametrize("rate", [44100, 48000, 88200, 96000])
def test_legend_mode_honours_the_selected_rate(ctx, svc, rate):
    ctx.set_ui_mode("legend")
    svc.set_sample_rate(rate)
    path, take = record(svc, 0.5)
    assert sf.info(path).samplerate == rate and svc.sample_rate == rate and svc.mic.opened[-1][0] == rate


def test_legend_stereo_and_mono_rule(ctx, svc):
    svc.set_channels(2)
    assert svc.channels == 1                                                         # Basit her zaman mono
    ctx.set_ui_mode("legend")
    assert svc.channels == 2
    path, _ = record(svc, 0.3)
    assert sf.info(path).channels == 2
    ctx.set_ui_mode("simple")
    assert svc.channels == 1


def test_invalid_sample_rate_is_rejected(svc):
    with pytest.raises(ValueError):
        svc.set_sample_rate(22050)


def test_recording_registers_media_and_announces(ctx, svc, qtbot):
    notes = []
    ctx.bus.subscribe(Topics.NOTIFY, notes.append)
    path, take = record(svc, 1.0)
    rows = ctx.project.db.list_media("audio")
    assert len(rows) == 1 and rows[0]["path"] == str(path) and rows[0]["sample_rate"] == 48000
    assert rows[0]["duration"] == pytest.approx(1.0, abs=0.05) and "kayit" in rows[0]["tags"]
    qtbot.waitUntil(lambda: bool(notes), timeout=800)
    assert path.name in notes[0]["text"]


def test_take_list_refreshes_and_orders_newest_first(svc, qtbot):
    signals = []
    svc.takesChanged.connect(lambda: signals.append(1))
    p1, _ = record(svc, 0.3)
    time.sleep(1.1)
    p2, _ = record(svc, 0.3)
    assert [t.path for t in svc.takes()] == [p2, p1] and len(signals) >= 2


def test_project_less_recording_goes_to_a_quick_folder(ctx, mic, tmp_path):
    from kadraj.modules.recorder.engine import RecorderEngine
    ctx.settings.set("project_root", str(tmp_path / "kok"))
    service = ctx.service("recorder")
    service.engine = RecorderEngine(mic.factory)
    service._injected_factory = True
    service._device_lister = lambda: [InputDevice(0, "M", 1, 48000.0, True)]
    service._devices = None
    service.mic = mic
    assert not ctx.project.is_open
    path = service.start_recording()
    mic.feed(0.2)
    service.stop_recording()
    assert path.parent == tmp_path / "kok" / "Hizli_Kayitlar" / "02_Audio_WAV" and path.exists()
    service.stop()


def test_cannot_start_twice_and_stop_without_recording_is_harmless(svc):
    svc.start_recording()
    with pytest.raises(RuntimeError):
        svc.start_recording()
    svc.stop_recording()
    assert svc.stop_recording() is None


# ---- hata yolları (Docker/CI) ---------------------------------------------------------------------------------------------
def test_no_input_device_is_a_smart_error_not_a_crash(ctx, svc, qtbot):
    svc._device_lister = lambda: []
    svc._devices = None
    errors = []
    svc.errorRaised.connect(errors.append)
    with pytest.raises(SmartError) as ei:
        svc.start_recording()
    assert ei.value.code == "audio.no_input" and svc.state == "idle" and errors and errors[0] is ei.value
    assert svc.availability_error().code == "audio.no_input" and svc.selected_device() is None
    assert not list((ctx.project.root / "02_Audio_WAV").glob("*.wav"))


def test_missing_audio_backend_is_reported_as_such(ctx, svc):
    err = SmartError("audio.no_backend", "x", "y")

    def boom():
        raise err
    svc._device_lister = boom
    svc._devices = None
    assert svc.devices() == [] and svc.last_error is err
    with pytest.raises(SmartError) as ei:
        svc.start_recording()
    assert ei.value.code == "audio.no_backend"


def test_stream_open_failure_maps_to_a_card_and_recovers(svc):
    svc.mic.fail_with = RuntimeError("Device unavailable [PaErrorCode -9985]")
    with pytest.raises(SmartError) as ei:
        svc.start_recording()
    assert ei.value.code == "audio.device_busy" and svc.state == "idle"
    svc.mic.fail_with = None
    record(svc, 0.2)                                                                  # sorun giderilince çalışır
    assert len(svc.takes()) == 1


def test_low_disk_space_is_refused_with_a_disk_card(ctx, svc, monkeypatch):
    monkeypatch.setattr(S.shutil, "disk_usage", lambda p: type("U", (), {"free": 1024})())
    with pytest.raises(SmartError) as ei:
        svc.start_recording()
    assert ei.value.code == "disk.full" and svc.state == "idle"


def test_worker_disk_error_stops_the_recording_gracefully(svc, monkeypatch, qtbot):
    from kadraj.modules.recorder import engine as E
    import errno
    real, n = E.WavWriter.write, {"c": 0}

    def flaky(self, block):
        n["c"] += 1
        if n["c"] > 8:
            raise OSError(errno.ENOSPC, "No space left on device")
        real(self, block)
    monkeypatch.setattr(E.WavWriter, "write", flaky)
    errors = []
    svc.errorRaised.connect(errors.append)
    svc.start_recording()
    svc.mic.feed(1.0)
    svc.engine.flush()
    svc._tick()                                                                        # zamanlayıcı hatayı görür
    assert svc.state == "idle" and errors and errors[0].code == "disk.full"
    assert svc.takes() and sf.info(svc.takes()[0].path).frames == 8 * 1024             # kaydedilen kısım korundu


# ---- gürültü kapısı / otomasyon kuralları --------------------------------------------------------------------------------------------
def test_gate_is_off_by_default_and_automations_never_run_on_their_own(ctx, svc):
    assert svc.gate_enabled is False and svc.input_gain_db == 0.0
    record(svc, 0.5)
    assert ctx.jobs.list() == []                                                       # yazıya çevirme/mastering kendiliğinden yok
    assert svc.engine.gate.enabled is False and svc.last_test is None


def test_simple_gate_uses_the_soft_preset_and_legend_uses_user_values(ctx, svc):
    svc.set_gate_enabled(True)
    assert svc.gate_params == SIMPLE_GATE
    svc.set_gate_param("threshold_db", -30)                                            # Basit'te yok sayılır
    assert svc.gate_params == SIMPLE_GATE
    ctx.set_ui_mode("legend")
    assert svc.gate_params.threshold_db == -30 and svc.gate_params.attack_ms == GateParams().attack_ms
    for key, v in (("attack_ms", 12), ("hold_ms", 250), ("release_ms", 400), ("range_db", -45)):
        svc.set_gate_param(key, v)
    p = svc.gate_params
    assert (p.threshold_db, p.attack_ms, p.hold_ms, p.release_ms, p.range_db) == (-30, 12, 250, 400, -45)
    with pytest.raises(KeyError):
        svc.set_gate_param("bogus", 1)


def test_gate_reaches_the_recording(ctx, svc):
    svc.set_gate_enabled(True)
    ctx.set_ui_mode("legend")
    svc.set_gate_param("threshold_db", -40)
    svc.set_gate_param("hold_ms", 0)
    svc.set_gate_param("release_ms", 20)
    svc.set_gate_param("range_db", -60)
    path = svc.start_recording()
    svc.mic.feed(1.0, amp=0.002, freq=100, modulate=False)
    svc.mic.feed(0.5, amp=0.3, modulate=False)
    svc.stop_recording()
    data, _ = sf.read(path)
    assert np.std(data[SR // 2:SR - 4096]) < 1e-4 and np.std(data[-4096:]) > 0.1


def test_settings_persist_in_the_ctx_settings(ctx, svc):
    svc.set_device("Sahte Mikrofon")
    svc.set_input_gain_db(6.5)
    svc.set_gate_enabled(True)
    svc.set_transcribe_options("en", "base")
    svc.set_master_target(-16)
    s = ctx.settings
    assert s.get("recorder.device") == "Sahte Mikrofon" and s.get("recorder.input_gain_db") == 6.5
    assert s.get("recorder.gate.enabled") is True and s.get("recorder.transcribe.language") == "en"
    assert s.get("recorder.transcribe.model") == "base" and svc.master_target_lufs == -16
    assert (svc.transcribe_language, svc.transcribe_model) == ("en", "base")
    assert svc.selected_device().name == "Sahte Mikrofon"
    svc.set_master_target(-99)
    assert svc.master_target_lufs == -30


def test_gain_applies_live_to_the_engine(svc):
    svc.start_recording()
    svc.set_input_gain_db(9.0)
    assert svc.engine.input_gain_db == 9.0
    svc.stop_recording()
    svc.set_input_gain_db(500)
    assert svc.input_gain_db == 40.0


# ---- mikrofon testi / auto-gain ----------------------------------------------------------------------------------------------------------
def test_mic_test_only_suggests_and_the_user_applies(ctx, svc, qtbot):
    results = []
    svc.micTestFinished.connect(results.append)
    clock = {"t": 100.0}
    svc._clock = lambda: clock["t"]
    svc.start_mic_test(seconds=5.0)
    assert svc.state == "testing" and svc.engine.snapshot().state == "monitoring"
    svc.mic.feed_array(speechlike(5.0, amp=0.02, noise=0.0005))
    svc.engine.flush()
    assert svc.snapshot().test_remaining_s == pytest.approx(5.0)
    clock["t"] += 5.1
    svc._tick()                                                                         # süre doldu → otomatik bitir
    assert svc.state == "idle" and results and results[0].verdict == "quiet"
    assert svc.last_test is results[0] and results[0].delta_db > 8
    assert svc.input_gain_db == 0.0                                                      # öneri kendiliğinden UYGULANMADI
    applied = svc.apply_suggested_gain()
    assert applied == pytest.approx(results[0].gain_db) and svc.input_gain_db == pytest.approx(results[0].gain_db)
    assert not svc.engine.running                                                        # mikrofon bırakıldı


def test_mic_test_silence_and_no_suggestion_to_apply(svc):
    svc.start_mic_test()
    svc.mic.feed_array(np.zeros(SR * 2, dtype=np.float32))
    res = svc.finish_mic_test()
    assert res.verdict == "silent" and svc.apply_suggested_gain() is None and svc.input_gain_db == 0.0


def test_mic_test_can_be_cancelled_and_calibration_runs_longer(svc):
    svc._clock = lambda: 0.0
    svc.start_mic_test()
    svc.cancel_mic_test()
    assert svc.state == "idle" and not svc.engine.running and svc.last_test is None
    svc.start_mic_test(calibration=True)
    assert svc._test_deadline == pytest.approx(S.CALIBRATION_SECONDS)
    svc.cancel_mic_test()


def test_mic_test_good_level_verdict(svc):
    svc.start_mic_test()
    svc.mic.feed_array(speechlike(5.0, amp=0.25))
    assert svc.finish_mic_test().verdict == "good"


def test_mic_test_and_recording_are_exclusive(svc):
    svc.start_recording()
    with pytest.raises(RuntimeError):
        svc.start_mic_test()
    svc.stop_recording()
    svc.start_mic_test()
    svc.start_recording()                                                                 # kayıt, testi iptal eder
    assert svc.state == "recording"
    svc.stop_recording()


def test_mic_test_without_a_device_raises_a_card(svc):
    svc._device_lister = lambda: []
    svc._devices = None
    with pytest.raises(SmartError):
        svc.start_mic_test()
    assert svc.state == "idle"


# ---- canlı durum / teleprompter ----------------------------------------------------------------------------------------------------------------
def test_live_snapshot_and_verdicts(svc):
    assert svc.snapshot().state == "idle" and svc.snapshot().verdict == "idle"
    svc.start_recording()
    svc.mic.feed(1.0, amp=0.25, modulate=False)
    svc.engine.flush()
    s = svc.snapshot()
    assert s.state == "recording" and s.verdict == "good" and s.elapsed_s == pytest.approx(1.0, abs=0.05)
    assert s.levels.peak_db[0] == pytest.approx(-12.0, abs=0.3)
    svc.mic.feed(0.2, amp=0.99, modulate=False)
    assert svc.snapshot().verdict in ("hot", "clip")
    svc.stop_recording()
    quiet = svc.start_recording()
    svc.mic.feed(0.5, amp=0.001, modulate=False)
    assert svc.snapshot().verdict == "quiet"
    svc.stop_recording()


def test_prompter_starts_with_the_recording_and_stops_with_it(svc):
    svc.prompter.set_text("bir iki üç dört beş altı yedi sekiz dokuz on")
    svc.prompter.sync = True
    assert not svc.prompter.running
    svc.start_recording()
    assert svc.prompter.running
    svc.stop_recording()
    assert not svc.prompter.running
    svc.prompter.sync = False
    svc.start_recording()
    assert not svc.prompter.running                                                       # senkron kapalıysa kendiliğinden başlamaz
    svc.stop_recording()


def test_prompter_advances_on_service_ticks(svc):
    clock = {"t": 0.0}
    svc._clock = lambda: clock["t"]
    svc.prompter.set_text("kelime " * 100)
    svc.prompter.set_speed("fast")
    svc.prompter_toggle()
    assert svc.prompter.running and svc._timer.isActive()
    svc._last_tick = 0.0
    clock["t"] = 6.0
    svc._tick()
    assert svc.prompter.position == pytest.approx(170 / 60 * 6.0)
    assert svc.snapshot().prompter_running
    svc.prompter_reset()
    assert svc.prompter.position == 0 and not svc._timer.isActive()


def test_ticked_signal_fires_while_recording(svc, qtbot):
    with qtbot.waitSignal(svc.ticked, timeout=1000):
        svc.start_recording()
    svc.stop_recording()
    assert not svc._timer.isActive()                                                       # boşta zamanlayıcı çalışmaz


def test_prompter_settings_persist_and_first_run_has_a_sample(ctx, svc):
    assert svc.prompter.words                                                              # ilk açılış örnek metni
    svc.prompter.set_text("benim metnim")
    svc.prompter.set_speed(155)
    svc.save_prompter()
    assert ctx.settings.get("recorder.prompter")["text"] == "benim metnim" and ctx.settings.get("recorder.prompter")["wpm"] == 155
    again = S.RecorderService(ctx)
    again.start(ctx)
    assert again.prompter.text == "benim metnim" and again.prompter.wpm == 155
    again.stop()


# ---- işler: yazıya çevirme / mastering ------------------------------------------------------------------------------------------------------------
class FakeModel:
    def transcribe(self, path, **kw):
        segs = [type("S", (), {"start": 0.0, "end": 1.0, "text": "Merhaba dünya"})(),
                type("S", (), {"start": 1.0, "end": 2.0, "text": "ikinci cümle"})()]
        return iter(segs), type("I", (), {"duration": 2.0, "language": "tr"})()


def install_fake_whisper(ctx, svc):
    svc.provider = WhisperLocalProvider(model_loader=lambda size, local: FakeModel())
    ctx.providers.register(svc.provider, replace=True)


def test_transcribe_runs_as_a_job_and_writes_an_srt_next_to_the_wav(ctx, svc, qtbot):
    install_fake_whisper(ctx, svc)
    path, take = record(svc, 1.0)
    notes = []
    ctx.bus.subscribe(Topics.NOTIFY, notes.append)
    job = svc.transcribe(take)
    assert job.kind == "recorder.transcribe" and job.payload["language"] == "tr" and job.payload["path"] == str(path)
    wait_job(qtbot, job)
    assert job.status is JobStatus.DONE
    srt = path.with_suffix(".srt")
    text = srt.read_text(encoding="utf-8")
    assert "Merhaba dünya" in text and "00:00:00,000 --> 00:00:01,000" in text and job.payload["_result"]["segments"] == 2
    assert next(t for t in svc.takes() if t.path == path).has_srt
    qtbot.waitUntil(lambda: any("Altyazı hazır" in n["text"] for n in notes), timeout=2000)


def test_transcribe_without_the_optional_package_gives_an_actionable_card(ctx, svc, qtbot, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    svc.provider = WhisperLocalProvider()
    ctx.providers.register(svc.provider, replace=True)
    path, take = record(svc, 0.5)
    shown, bus_errors = [], []
    svc.errorRaised.connect(shown.append)
    ctx.bus.subscribe(Topics.ERROR, bus_errors.append)
    job = wait_job(qtbot, svc.transcribe(take))
    assert job.status is JobStatus.FAILED
    qtbot.waitUntil(lambda: bool(shown) and bool(bus_errors), timeout=2000)
    err = shown[0]
    assert err.code == "whisper.whisper_missing" and err.actions[0].kind == "copy"
    assert "paketi kur" in err.actions[0].label and "faster-whisper" in err.actions[0].payload["text"]
    assert not path.with_suffix(".srt").exists() and bus_errors[0].code == err.code


def test_transcribe_can_be_cancelled(ctx, svc, qtbot):
    import threading

    class SlowModel:
        def transcribe(self, path, **kw):
            def gen():
                for i in range(200):
                    time.sleep(0.02)
                    yield type("S", (), {"start": float(i), "end": i + 1.0, "text": f"s{i}"})()
            return gen(), type("I", (), {"duration": 200.0, "language": "tr"})()
    svc.provider = WhisperLocalProvider(model_loader=lambda s, l: SlowModel())
    ctx.providers.register(svc.provider, replace=True)
    path, take = record(svc, 0.3)
    job = svc.transcribe(take)
    qtbot.waitUntil(lambda: job.status is JobStatus.RUNNING, timeout=3000)
    qtbot.wait(100)
    ctx.jobs.cancel(job.id)
    wait_job(qtbot, job)
    assert job.status is JobStatus.CANCELLED and not path.with_suffix(".srt").exists()


def test_only_wav_files_can_be_queued(ctx, svc, tmp_path):
    for name in ("a.mp3", "a.flac"):
        with pytest.raises(ValueError):
            svc.transcribe(tmp_path / name)
        with pytest.raises(ValueError):
            svc.master(tmp_path / name)
    assert ctx.jobs.list() == []


@needs_ffmpeg
def test_master_runs_as_a_job_creates_a_new_file_and_keeps_the_original(ctx, svc, qtbot):
    path = svc.start_recording()
    svc.mic.feed_array(speechlike(8.0, amp=0.03))
    take = svc.stop_recording()
    original = path.read_bytes()
    job = svc.master(take)
    assert job.kind == "recorder.master" and job.payload["target_lufs"] == -14.0
    wait_job(qtbot, job, timeout=20000)
    assert job.status is JobStatus.DONE
    out = Path(job.payload["_result"]["output"])
    assert out.name.endswith("_master.wav") and out != path and out.exists()
    assert path.read_bytes() == original                                                  # orijinal korundu
    from kadraj.modules.recorder.dsp import measure_loudness
    assert measure_loudness(sf.read(out, dtype="float32")[0], SR).integrated == pytest.approx(-14.0, abs=0.8)
    takes = {t.name: t for t in svc.takes()}
    assert takes[out.stem].is_master and takes[path.stem].master_path == out


def test_master_failure_without_ffmpeg_is_a_card(ctx, svc, qtbot, monkeypatch):
    from kadraj.modules.recorder import mastering
    monkeypatch.setattr(S, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(mastering, "find_ffmpeg", lambda: None)
    path, take = record(svc, 1.0)
    shown = []
    svc.errorRaised.connect(shown.append)
    job = wait_job(qtbot, svc.master(take))
    assert job.status is JobStatus.FAILED
    qtbot.waitUntil(lambda: bool(shown), timeout=2000)
    assert shown[0].code == "tool.missing" and "ffmpeg" in shown[0].title.lower()


def test_jobs_are_queued_in_the_project_db_and_visible(ctx, svc):
    install_fake_whisper(ctx, svc)
    path, take = record(svc, 0.5)
    job = svc.transcribe(take)
    assert ctx.project.db.get_job(job.id)["kind"] == "recorder.transcribe"
    assert "Yazıya çevir" in job.title


# ---- dalga formu / kapanış ---------------------------------------------------------------------------------------------------------------------------------
def test_waveform_is_computed_in_the_background_and_cached(ctx, svc, qtbot):
    path, take = record(svc, 1.0, amp=0.5)
    assert svc.waveform(take) is None                                                      # hazır değil → arka planda
    with qtbot.waitSignal(svc.waveformReady, timeout=3000) as sig:
        pass
    assert sig.args == [str(path)]
    peaks = svc.waveform(take)
    assert len(peaks) == 240 and max(peaks) == pytest.approx(0.5, abs=0.02)
    assert list((ctx.project.root / ".kadraj" / "cache" / "waveforms").glob("*.json"))


def test_shutdown_finalises_a_running_recording(ctx, svc):
    path = svc.start_recording()
    svc.mic.feed(1.0)
    ctx.shutdown()                                                                         # uygulama kapanırken kayıt sürüyordu
    assert path.exists() and sf.info(path).frames >= SR * 0.9 and sf.info(path).subtype == "PCM_24"
    assert not svc.engine.running and svc.state == "idle"


def test_stop_is_idempotent(svc):
    svc.stop()
    svc.stop()
    assert svc.state == "idle"


def test_mode_switch_during_recording_keeps_the_recording_going(ctx, svc):
    svc.start_recording()
    svc.mic.feed(0.3)
    ctx.set_ui_mode("legend")
    svc.mic.feed(0.3)
    ctx.set_ui_mode("simple")
    assert svc.state == "recording"
    take = svc.stop_recording()
    assert sf.info(take.path).frames >= int(0.55 * SR) and sf.info(take.path).samplerate == 48000


def test_smart_error_roundtrip_through_job_payload():
    from kadraj.core.contracts import SmartAction
    err = SmartError("a.b", "T", "S", cause="C", steps=["x"], actions=[SmartAction("i", "L", "copy", {"text": "t"})],
                      technical="tech", retryable=True, source="s")
    back = S.smart_error_from_dict(err.to_dict())
    assert back.to_dict() == err.to_dict()
