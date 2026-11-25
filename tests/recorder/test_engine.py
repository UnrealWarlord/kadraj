"""Kayıt motoru: sahte akış → kazanç/VU/kapı → PCM_24 WAV; hata yolları, taşma, tek-.wav kuralı."""
from __future__ import annotations

import errno
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from kadraj.core.contracts import SmartError
from kadraj.modules.recorder import engine as E
from kadraj.modules.recorder.dsp import GateParams
from kadraj.modules.recorder.engine import RecorderEngine, WavWriter
from tests.recorder.conftest import SR, FakeMic


def test_records_a_24bit_wav_with_exact_frames(engine, mic, tmp_path):
    engine.configure(sample_rate=48000, channels=1)
    path = tmp_path / "a.wav"
    engine.start_recording(path)
    mic.feed(2.0, amp=0.5, modulate=False)
    res = engine.stop_recording()
    engine.stop()
    info = sf.info(path)
    assert (info.subtype, info.samplerate, info.channels, info.format) == ("PCM_24", 48000, 1, "WAV")
    expected = 2 * SR // 1024 * 1024 + (2 * SR % 1024)
    assert info.frames == expected == res.frames and res.duration_s == pytest.approx(2.0, abs=1e-3)
    assert res.dropped_blocks == 0 and res.error is None


def test_24bit_precision_roundtrip(engine, mic, tmp_path):
    path = tmp_path / "p.wav"
    engine.start_recording(path)
    t = np.arange(SR) / SR
    ref = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    mic.feed_array(ref)
    engine.stop()
    data, _ = sf.read(path, dtype="float32")
    assert len(data) == len(ref) and np.max(np.abs(data - ref)) < 2e-6                 # 24-bit ≈ 1.2e-7 ölçeğinde


@pytest.mark.parametrize("sr, ch", [(44100, 1), (48000, 2), (88200, 1), (96000, 2)])
def test_sample_rates_and_channels(engine, mic, tmp_path, sr, ch):
    engine.configure(sample_rate=sr, channels=ch)
    engine.start_recording(tmp_path / "x.wav")
    mic.feed(0.5, sr=sr, ch=ch)
    engine.stop()
    info = sf.info(tmp_path / "x.wav")
    assert (info.samplerate, info.channels, info.subtype) == (sr, ch, "PCM_24")
    assert mic.opened[-1][:2] == (sr, ch)


@pytest.mark.parametrize("name", ["a.mp3", "a.flac", "a.ogg", "a.m4a", "a", "a.wav.mp3"])
def test_only_wav_output_is_allowed(engine, mic, tmp_path, name):
    with pytest.raises(ValueError):
        engine.start_recording(tmp_path / name)
    assert not engine.running                                                  # hiç akış açılmadı
    with pytest.raises(ValueError):
        WavWriter(tmp_path / name, SR, 1)
    assert not list(tmp_path.iterdir())


def test_uppercase_wav_extension_is_accepted(engine, mic, tmp_path):
    engine.start_recording(tmp_path / "A.WAV")
    mic.feed(0.2)
    engine.stop()
    assert (tmp_path / "A.WAV").is_file()


def test_gain_is_applied_and_clipped_to_full_scale(engine, mic, tmp_path):
    engine.configure(input_gain_db=12.0)
    engine.start_recording(tmp_path / "g.wav")
    mic.feed(0.5, amp=0.5, modulate=False)
    engine.flush()
    snap = engine.snapshot()
    engine.stop()
    data, _ = sf.read(tmp_path / "g.wav")
    assert np.max(np.abs(data)) <= 1.0 and snap.levels.clipping                       # 0.5 × 3.98 = 1.99 → kırpıldı
    assert engine.input_gain_db == 12.0


def test_gain_changes_live_while_recording(engine, mic, tmp_path):
    engine.start_recording(tmp_path / "l.wav")
    mic.feed(0.3, amp=0.1, modulate=False)
    engine.configure(input_gain_db=-6.0)
    mic.feed(0.3, amp=0.1, modulate=False)
    engine.stop()
    data, _ = sf.read(tmp_path / "l.wav")
    half = len(data) // 2
    assert np.max(np.abs(data[:half - 2048])) == pytest.approx(0.1, abs=0.005)
    assert np.max(np.abs(data[half + 2048:])) == pytest.approx(0.05, abs=0.005)


def test_noise_gate_runs_in_the_callback_and_shapes_the_file(engine, mic, tmp_path):
    engine.configure(gate_params=GateParams(threshold_db=-40, range_db=-60, hold_ms=0, release_ms=30),
                     gate_enabled=True)
    engine.start_recording(tmp_path / "gate.wav")
    mic.feed(1.0, amp=0.003, freq=100, modulate=False)                                 # -50 dBFS "vızıltı"
    mic.feed(1.0, amp=0.3, freq=220, modulate=False)                                   # konuşma
    engine.flush()
    snap = engine.snapshot()
    engine.stop()
    data, _ = sf.read(tmp_path / "gate.wav")
    hum, voice = data[SR // 2:SR - 2048], data[SR + SR // 2:]
    assert 20 * np.log10(np.std(hum) + 1e-9) < -75 < 20 * np.log10(np.std(voice))     # vızıltı yok edildi, ses geçti
    assert snap.gate_state in ("open", "hold")


def test_gate_off_by_default_records_everything(engine, mic, tmp_path):
    engine.start_recording(tmp_path / "raw.wav")
    mic.feed(0.5, amp=0.003, freq=100, modulate=False)
    engine.stop()
    data, _ = sf.read(tmp_path / "raw.wav")
    assert np.max(np.abs(data)) == pytest.approx(0.003, abs=5e-4) and not engine.gate.enabled


def test_snapshot_levels_loudness_and_elapsed(engine, mic, tmp_path):
    engine.start_recording(tmp_path / "s.wav")
    mic.feed(1.5, amp=0.1, freq=1000, modulate=False)
    engine.flush()
    snap = engine.snapshot()
    assert snap.state == "recording" and snap.recording and snap.path == str(tmp_path / "s.wav")
    assert snap.elapsed_s == pytest.approx(1.5, abs=0.03)
    assert snap.levels.peak_db[0] == pytest.approx(-20.0, abs=0.2)
    assert snap.loudness.integrated == pytest.approx(-23.0, abs=0.4) and snap.loudness.true_peak_db == pytest.approx(-20, abs=0.3)
    engine.stop()
    assert engine.snapshot().state == "idle" and not engine.running


def test_monitoring_collects_audio_without_writing_a_file(engine, mic, tmp_path):
    engine.start_monitoring(collect=True)
    assert engine.snapshot().state == "monitoring"
    mic.feed(1.0, amp=0.2, modulate=False)
    engine.flush()
    got = engine.collected()
    engine.stop()
    assert got.shape == (SR // 1024 * 1024 + SR % 1024, 1) and not list(tmp_path.iterdir())
    assert np.max(np.abs(got)) == pytest.approx(0.2, abs=0.005)


def test_collection_is_capped(engine, mic, monkeypatch):
    monkeypatch.setattr(E, "MAX_COLLECT_S", 1.0)
    engine.start_monitoring(collect=True)
    mic.feed(3.0, amp=0.2)
    engine.flush()
    n = len(engine.collected())
    engine.stop()
    assert SR <= n < 1.5 * SR


def test_stream_can_record_multiple_takes_with_fresh_loudness(engine, mic, tmp_path):
    engine.start_recording(tmp_path / "1.wav")
    mic.feed(1.0, amp=0.3, modulate=False)
    engine.stop_recording()
    loud_first = engine.snapshot().loudness.integrated
    engine.start_recording(tmp_path / "2.wav")
    mic.feed(1.0, amp=0.03, modulate=False)
    engine.flush()
    second = engine.snapshot().loudness.integrated
    engine.stop()
    assert second < loud_first - 15                                                      # take başına sıfırlandı
    assert sf.info(tmp_path / "1.wav").frames > 0 and sf.info(tmp_path / "2.wav").frames > 0


def test_restart_while_recording_is_rejected(engine, mic, tmp_path):
    engine.start_recording(tmp_path / "1.wav")
    with pytest.raises(RuntimeError):
        engine.start_recording(tmp_path / "2.wav")
    engine.stop()
    with pytest.raises(RuntimeError):
        engine.stop_recording()


def test_structural_config_cannot_change_while_running(engine, mic):
    engine.start_monitoring()
    with pytest.raises(RuntimeError):
        engine.configure(sample_rate=96000)
    engine.configure(input_gain_db=3.0, gate_enabled=True)                               # canlı olanlar serbest
    engine.stop()
    engine.configure(sample_rate=96000, channels=2, device=3)
    assert (engine.sample_rate, engine.channels, engine.device) == (96000, 2, 3)


def test_xruns_are_counted(engine, mic, tmp_path):
    engine.start_recording(tmp_path / "x.wav")
    status = type("S", (), {"input_overflow": True})()
    engine.stream_cb = mic.stream.cb
    mic.stream.cb(np.zeros((512, 1), dtype=np.float32), 512, None, status)
    mic.stream.cb(np.zeros((512, 1), dtype=np.float32), 512, None, None)
    assert engine.snapshot().xruns == 1
    engine.stop()


def test_queue_overflow_drops_blocks_instead_of_blocking(mic, tmp_path, monkeypatch):
    gate = threading.Event()
    real = E.WavWriter.write

    def slow(self, block):
        gate.wait(5)
        real(self, block)
    monkeypatch.setattr(E.WavWriter, "write", slow)
    e = RecorderEngine(mic.factory, queue_blocks=4)
    e.start_recording(tmp_path / "o.wav")
    t0 = time.monotonic()
    mic.feed(1.0, amp=0.2)                                                              # ≈ 47 blok, kuyruk 4
    assert time.monotonic() - t0 < 1.0                                                  # geri çağrı asla engellenmedi
    assert e.snapshot().dropped_blocks > 20
    gate.set()
    res = e.stop()
    assert res is not None and res.dropped_blocks > 20 and sf.info(tmp_path / "o.wav").frames < SR


def test_disk_error_in_the_worker_surfaces_as_smart_error_and_keeps_the_file(mic, tmp_path, monkeypatch):
    real = E.WavWriter.write
    state = {"n": 0}

    def flaky(self, block):
        state["n"] += 1
        if state["n"] > 10:
            raise OSError(errno.ENOSPC, "No space left on device")
        real(self, block)
    monkeypatch.setattr(E.WavWriter, "write", flaky)
    e = RecorderEngine(mic.factory)
    e.start_recording(tmp_path / "d.wav")
    mic.feed(1.0)
    e.flush()
    snap = e.snapshot()
    assert snap.error is not None and snap.error.code == "disk.full" and not snap.recording
    res = e.stop_recording()                                                            # hata sonrası da düzgünce kapanır
    e.stop()
    assert res.error is not None and sf.info(tmp_path / "d.wav").frames == 10 * 1024


def test_missing_input_device_is_a_smart_error_not_a_crash(mic, tmp_path):
    mic.fail_with = RuntimeError("Error querying device -1 [PaErrorCode -9996]: Invalid device")
    e = RecorderEngine(mic.factory)
    with pytest.raises(SmartError) as ei:
        e.start_recording(tmp_path / "n.wav")
    assert ei.value.code == "audio.no_input" and not e.running and not (tmp_path / "n.wav").exists()
    assert not e.recording
    mic.fail_with = None
    e.start_recording(tmp_path / "ok.wav")                                              # sonradan düzelirse çalışır
    e.stop()


def test_stop_is_idempotent_and_releases_the_stream(engine, mic, tmp_path):
    engine.stop()
    engine.start_monitoring()
    stream = mic.stream
    engine.stop()
    engine.stop()
    assert stream.closed and not stream.active and not engine.running


def test_no_worker_thread_leaks(mic, tmp_path):
    before = threading.active_count()
    for i in range(3):
        e = RecorderEngine(mic.factory)
        e.start_recording(tmp_path / f"{i}.wav")
        mic.feed(0.1)
        e.stop()
    assert threading.active_count() <= before


def test_wavwriter_flushes_header_periodically(tmp_path):
    w = WavWriter(tmp_path / "f.wav", 8000, 1)
    w.write(np.zeros(8000, dtype=np.float32))
    w.write(np.zeros(100, dtype=np.float32))
    assert sf.info(tmp_path / "f.wav").frames >= 8000                                   # kapatılmadan okunabilir (çökme güvenli)
    w.close()
