"""Gerçek ``QAudioSource`` yolu (Android'deki kayıt yolu) — yalnız sanal sinüs kaynağı olan ortamda çalışır.

``KADRAJ_LIVE_AUDIO=sine:<Hz>`` verilmezse atlanır (gerçek mikrofonlu makinede odanın sesini kaydetmesin). Bulut
makinesinde PulseAudio'nun ``module-sine-source``'u varsayılan giriş yapılarak çalıştırıldı (M18): Qt'nin aygıt
listesi → biçim seçimi → ``QAudioSource`` (çekme kipi) → motor → soundfile'sız ``.wav`` → frekans denetimi.
"""
from __future__ import annotations

import os
import wave

import numpy as np
import pytest

from kadraj.modules.recorder import devices as D
from kadraj.modules.recorder import qt_audio as Q
from kadraj.modules.recorder import wavio as W
from kadraj.modules.recorder.engine import RecorderEngine

_SPEC = os.environ.get("KADRAJ_LIVE_AUDIO", "")
pytestmark = pytest.mark.skipif(not _SPEC.startswith("sine:"), reason="sanal sinüs kaynağı yok (KADRAJ_LIVE_AUDIO)")
FREQ = float(_SPEC.split(":", 1)[1]) if _SPEC.startswith("sine:") else 0.0


@pytest.fixture()
def android_audio(monkeypatch):
    monkeypatch.setenv("KADRAJ_AUDIO_BACKEND", "qt")
    monkeypatch.setenv("KADRAJ_WAV_BACKEND", "wave")


def _dominant_hz(x: np.ndarray, sr: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / sr)[int(np.argmax(spec))])


def test_qt_lists_the_input_and_supports_48k_mono(qapp, android_audio):
    devices = D.list_input_devices()
    assert devices and any(d.is_default for d in devices)
    assert D.check_input(None, 48000, 1) is None
    fmt = Q.choose_format(Q.audio_device(None), 48000, 1)
    assert fmt is not None and fmt.sampleFormat().name in Q.FORMAT_ORDER


def _wait_for_sound(engine, qtbot) -> None:
    """Ses sunucusunun akışı başlatmasını bekler (kapsayıcıdaki PulseAudio'da ~2 sn; telefonda AAudio ile çok kısa)."""
    engine.start_monitoring(collect=True)
    qtbot.waitUntil(lambda: len(engine.collected()) > 0, timeout=8000)


@pytest.mark.parametrize("rate", [48000, 44100])
def test_recording_through_qaudiosource_captures_the_sine(qapp, qtbot, android_audio, tmp_path, rate):
    engine = RecorderEngine(D.default_stream_factory)
    engine.configure(sample_rate=rate, channels=1)
    _wait_for_sound(engine, qtbot)
    path = tmp_path / "canli.wav"
    engine.start_recording(path)                                                # açık akış kullanılır
    qtbot.wait(1500)                                                            # Qt olay döngüsü: readyRead gelir
    result = engine.stop_recording()
    engine.stop()
    assert result.error is None and result.dropped_blocks == 0
    with wave.open(str(path), "rb") as r:
        assert (r.getframerate(), r.getnchannels(), r.getsampwidth()) == (rate, 1, 3)
        assert r.getnframes() == result.frames
    assert 1.2 <= result.duration_s <= 1.8                                      # gerçek zamanlı akış
    audio = np.concatenate(list(W.iter_blocks(path)))[:, 0]
    tail = audio[len(audio) // 4:]                                              # açılış geçişini atla
    assert np.sqrt(np.mean(tail ** 2)) > 0.01
    assert _dominant_hz(tail, rate) == pytest.approx(FREQ, abs=5.0)


def test_mic_test_through_qaudiosource_collects_audio(qapp, qtbot, android_audio):
    engine = RecorderEngine(D.default_stream_factory)
    engine.configure(sample_rate=48000, channels=1)
    _wait_for_sound(engine, qtbot)
    start = len(engine.collected())
    qtbot.wait(800)
    engine.flush()
    collected = engine.collected()
    peak = engine.snapshot().levels.max_peak_db
    engine.stop()
    assert len(collected) - start > 48000 * 0.6 and np.max(np.abs(collected)) > 0.01
    assert peak > -40.0                                                         # VU canlı
