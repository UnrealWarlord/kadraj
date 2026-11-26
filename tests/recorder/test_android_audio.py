"""Android ses yolu (4. adım): soundfile'sız .wav, QtMultimedia girişi, mikrofon izni (M18).

Gerçek mikrofon yok: Qt kaynağı sahte nesnelerle, izin sahte bir izin nesnesiyle sınanır. Gerçek ``QAudioSource``
yolu ayrıca ``test_qt_audio_live.py``'de (yalnız ses aygıtı olan ortamda) sınanır.
"""
from __future__ import annotations

import shutil
import struct
import sys
import types
import wave

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtMultimedia import QAudioFormat, QtAudio

from kadraj.core.contracts import SmartError
from kadraj.modules.recorder import devices as D
from kadraj.modules.recorder import mastering as M
from kadraj.modules.recorder import permission as P
from kadraj.modules.recorder import qt_audio as Q
from kadraj.modules.recorder import takes as T
from kadraj.modules.recorder import wavio as W
from tests.recorder.conftest import SR, speechlike

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg kurulu değil")


@pytest.fixture()
def no_soundfile(monkeypatch):
    """Android gibi: ``import soundfile`` ImportError verir."""
    monkeypatch.setitem(sys.modules, "soundfile", None)
    assert W.soundfile_module() is None


@pytest.fixture()
def qt_backend(monkeypatch):
    monkeypatch.setenv("KADRAJ_AUDIO_BACKEND", "qt")


def sine(seconds=1.0, sr=SR, freq=440.0, amp=0.5, ch=1):
    t = np.arange(int(seconds * sr)) / sr
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(x[:, None], ch, axis=1)


# ---- .wav: soundfile olmadan ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("ch", [1, 2])
def test_wave_writer_output_is_a_pcm24_wav_that_soundfile_reads_back(tmp_path, monkeypatch, ch):
    monkeypatch.setenv("KADRAJ_WAV_BACKEND", "wave")
    ref = sine(1.0, ch=ch)
    w = W.open_writer(tmp_path / "a.wav", SR, ch)
    assert isinstance(w, W.Pcm24WavWriter)
    w.write(ref[:1000])
    w.write(ref[1000:])
    w.close()
    info = sf.info(tmp_path / "a.wav")
    assert (info.format, info.subtype, info.samplerate, info.channels, info.frames) == ("WAV", "PCM_24", SR, ch, SR)
    data, _ = sf.read(tmp_path / "a.wav", dtype="float32", always_2d=True)
    assert np.max(np.abs(data - ref)) < 2e-6                                    # 24 bit ≈ 1.2e-7 adım


def test_full_scale_and_clipping(tmp_path, monkeypatch):
    monkeypatch.setenv("KADRAJ_WAV_BACKEND", "wave")
    w = W.open_writer(tmp_path / "c.wav", SR, 1)
    w.write(np.array([[1.0], [-1.0], [1.7], [-3.0], [0.0]], dtype=np.float32))
    w.close()
    data, _ = sf.read(tmp_path / "c.wav", dtype="int32")
    assert (data >> 8).tolist() == [8388607, -8388607, 8388607, -8388607, 0]


def test_header_is_updated_on_flush_so_an_interrupted_recording_stays_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("KADRAJ_WAV_BACKEND", "wave")
    path = tmp_path / "k.wav"
    w = W.open_writer(path, SR, 1)
    w.write(sine(0.5))
    w.flush()
    with wave.open(str(path), "rb") as r:                                     # yazıcı hâlâ açık (uygulama çöktü)
        assert (r.getnframes(), r.getsampwidth(), r.getframerate()) == (SR // 2, 3, SR)
    w.write(sine(0.25))
    w.close()
    assert sf.info(path).frames == SR * 3 // 4
    w.close()                                                                  # ikinci kapatma zararsız


def test_odd_sized_data_gets_the_riff_pad_byte(tmp_path, monkeypatch):
    monkeypatch.setenv("KADRAJ_WAV_BACKEND", "wave")
    path = tmp_path / "o.wav"
    w = W.open_writer(path, SR, 1)
    w.write(np.zeros((3, 1), dtype=np.float32))                                # 9 bayt veri
    w.close()
    raw = path.read_bytes()
    assert len(raw) == 44 + 9 + 1
    assert struct.unpack("<I", raw[4:8])[0] == 36 + 10 and struct.unpack("<I", raw[40:44])[0] == 9
    assert sf.info(path).frames == 3 and W.info(path).frames == 3


@pytest.mark.parametrize("fmt, subtype, tol", [("WAV", "PCM_16", 4e-5), ("WAV", "PCM_24", 2e-7),
                                               ("WAV", "PCM_32", 1e-7), ("WAV", "PCM_U8", 1e-2),
                                               ("WAVEX", "PCM_24", 2e-7)])
def test_wave_fallback_reads_the_same_samples_as_soundfile(tmp_path, monkeypatch, fmt, subtype, tol):
    path = tmp_path / "r.wav"
    ref = sine(0.3, ch=2, amp=0.4)
    sf.write(path, ref, SR, format=fmt, subtype=subtype)
    expected, _ = sf.read(path, dtype="float32", always_2d=True)
    monkeypatch.setenv("KADRAJ_WAV_BACKEND", "wave")
    info = W.info(path)
    assert (info.samplerate, info.channels, info.frames) == (SR, 2, len(ref))
    assert info.duration == pytest.approx(0.3)
    got = np.concatenate(list(W.iter_blocks(path, 1000)))
    assert got.shape == expected.shape and np.max(np.abs(got - expected)) <= tol


def test_engine_records_without_soundfile(engine, mic, tmp_path, no_soundfile):
    path = tmp_path / "Kayit.wav"
    engine.start_recording(path)
    mic.feed(1.0, amp=0.5, modulate=False)
    res = engine.stop_recording()
    engine.stop()
    with wave.open(str(path), "rb") as r:
        assert (r.getsampwidth(), r.getframerate(), r.getnchannels(), r.getnframes()) == (3, SR, 1, res.frames)
    assert res.frames == SR and res.error is None


def test_take_list_and_waveform_without_soundfile(tmp_path, monkeypatch):
    path = tmp_path / "Kayit_20261126_101010.wav"
    sf.write(path, sine(2.0, amp=0.25), SR, subtype="PCM_24")
    with_sf = T.waveform_peaks(path, 60)
    monkeypatch.setitem(sys.modules, "soundfile", None)
    takes = T.scan_takes(tmp_path)
    assert len(takes) == 1 and takes[0].duration_s == pytest.approx(2.0) and takes[0].sample_rate == SR
    assert np.allclose(T.waveform_peaks(path, 60), with_sf, atol=1e-6)


@needs_ffmpeg
def test_mastering_works_without_soundfile(tmp_path, no_soundfile):
    src = tmp_path / "Kayit_1.wav"
    w = W.open_writer(src, SR, 1)
    w.write(speechlike(6, amp=0.03)[:, None])
    w.close()
    res = M.master_file(src)
    info = W.info(res.path)                                                  # FFmpeg'in WAVE_FORMAT_EXTENSIBLE çıktısı
    assert res.path != src and src.is_file() and info.samplerate == SR and info.frames > 0


# ---- QtMultimedia girişi -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind, raw, expected", [
    ("Float", np.array([0.5, -0.25], dtype=np.float32).tobytes(), [0.5, -0.25]),
    ("Int16", np.array([16384, -32768], dtype=np.int16).tobytes(), [0.5, -1.0]),
    ("Int32", np.array([1 << 30, -(1 << 31)], dtype=np.int32).tobytes(), [0.5, -1.0]),
    ("UInt8", bytes([192, 0]), [0.5, -1.0]),
])
def test_qt_samples_are_converted_to_float32(kind, raw, expected):
    x = Q.to_float(raw, kind, 1)
    assert x.dtype == np.float32 and x.shape == (2, 1) and x[:, 0].tolist() == pytest.approx(expected)
    assert Q.to_float(raw * 2, kind, 2).shape == (2, 2)                       # iç içe kanallar


def _fmt(kind="Float", rate=SR, ch=1):
    f = QAudioFormat()
    f.setSampleRate(rate)
    f.setChannelCount(ch)
    f.setSampleFormat(getattr(QAudioFormat.SampleFormat, kind))
    return f


class FakeIO(QObject):
    readyRead = Signal()

    def __init__(self):
        super().__init__()
        self.chunks = []

    def push(self, raw):
        self.chunks.append(raw)
        self.readyRead.emit()

    def readAll(self):
        data, self.chunks = b"".join(self.chunks), []
        return QByteArray(data)


class FakeSource:
    def __init__(self, device, fmt, error=QtAudio.Error.NoError):
        self.device, self.fmt, self._error = device, fmt, error
        self.io = FakeIO()
        self.started = self.stopped = 0
        self.buffer = None

    def setBufferSize(self, n):
        self.buffer = n

    def start(self):
        self.started += 1
        return self.io

    def error(self):
        return self._error

    def stop(self):
        self.stopped += 1


def test_qt_stream_delivers_whole_frames_in_order():
    got = []
    src = {}

    def factory(dev, fmt):
        src["s"] = FakeSource(dev, fmt)
        return src["s"]

    stream = Q.QtInputStream("dev", _fmt("Int16", ch=2), 1024, lambda b, n, t, s: got.append(b.copy()),
                             source_factory=factory)
    fake = src["s"]
    assert fake.buffer == 1024 * 4 * 4                                        # 4 blok × (2 kanal × 2 bayt)
    stream.start()
    pcm = (np.arange(-50, 50, dtype=np.int16) * 300).tobytes()                 # 50 kare, stereo
    fake.io.push(pcm[:7])                                                     # yarım kare bekletilir
    fake.io.push(pcm[7:150])
    fake.io.chunks.append(pcm[150:])                                          # readyRead gelmeden duran kalan
    stream.drain()
    out = np.concatenate(got)
    assert out.shape == (50, 2) and np.allclose(out.reshape(-1), np.frombuffer(pcm, np.int16) / 32768.0)
    stream.stop()
    assert fake.stopped == 1
    fake.io.push(pcm)                                                          # durduktan sonra gelen ses yok sayılır
    assert sum(len(b) for b in got) == 50
    stream.close()


def test_qt_stream_open_error_becomes_a_card(qt_backend, monkeypatch):
    dev = FakeDevice()
    real = Q.QtInputStream
    monkeypatch.setattr(Q, "audio_device", lambda index: dev)
    monkeypatch.setattr(Q, "QtInputStream", lambda d, f, b, cb: real(
        d, f, b, cb, source_factory=lambda d2, f2: FakeSource(d2, f2, QtAudio.Error.OpenError)))
    stream = D.default_stream_factory(SR, 1, None, 1024, lambda *a: None)
    with pytest.raises(OSError):
        stream.start()
    err = D.map_audio_error(OSError("QAudioSource açılamadı: OpenError"))
    assert err.code == "audio.open_failed"


class FakeDevice:
    def __init__(self, kinds=("Int16",), rates=(8000, 48000), max_ch=2, name="Dahili mikrofon", default=False,
                 ident=b"mic0"):
        self.kinds, self.rates, self.max_ch, self.name, self.default, self.ident = kinds, rates, max_ch, name, default, ident

    def isFormatSupported(self, fmt):
        return (fmt.sampleFormat().name in self.kinds and self.rates[0] <= fmt.sampleRate() <= self.rates[1]
                and fmt.channelCount() <= self.max_ch)

    def maximumChannelCount(self):
        return self.max_ch

    def minimumSampleRate(self):
        return self.rates[0]

    def maximumSampleRate(self):
        return self.rates[1]

    def description(self):
        return self.name

    def preferredFormat(self):
        return _fmt("Int16", 48000, 1)

    def isDefault(self):
        return self.default

    def isNull(self):
        return False

    def id(self):
        return QByteArray(self.ident)


def test_format_choice_prefers_float_and_falls_back_to_int16():
    assert Q.choose_format(FakeDevice(kinds=("Float", "Int16")), SR, 1).sampleFormat().name == "Float"
    fmt = Q.choose_format(FakeDevice(kinds=("Int16",)), SR, 2)
    assert (fmt.sampleFormat().name, fmt.sampleRate(), fmt.channelCount()) == ("Int16", SR, 2)
    assert Q.choose_format(FakeDevice(kinds=()), SR, 1) is None


def test_unsupported_settings_are_plain_cards(monkeypatch):
    dev = FakeDevice(max_ch=1, rates=(8000, 48000))
    monkeypatch.setattr(Q, "audio_device", lambda index: dev)
    assert Q.check_input(0, SR, 1) is None
    assert Q.check_input(0, SR, 2).code == "audio.unsupported_channels"
    assert Q.check_input(0, 96000, 1).code == "audio.unsupported_rate"


def _fake_qtm(inputs, default=None):
    class MD:
        @staticmethod
        def audioInputs():
            return list(inputs)

        @staticmethod
        def defaultAudioInput():
            if default is not None:
                return default
            null = FakeDevice()
            null.isNull = lambda: True
            return null

    return types.SimpleNamespace(QMediaDevices=MD, QAudioFormat=QAudioFormat, QAudioSource=FakeSource)


def test_qt_device_list_marks_the_default(monkeypatch):
    a, b = FakeDevice(name="Dahili", ident=b"a"), FakeDevice(name="Kulaklık", ident=b"b", max_ch=1)
    monkeypatch.setattr(Q, "_qtm", lambda: _fake_qtm([a, b], default=FakeDevice(ident=b"b")))
    devs = Q.list_input_devices()
    assert [(d.index, d.name, d.max_input_channels, d.is_default) for d in devs] == [
        (0, "Dahili", 2, False), (1, "Kulaklık", 1, True)]
    assert devs[0].default_samplerate == 48000.0
    assert Q.audio_device(1) is b and Q.audio_device(None).ident == b"b"
    monkeypatch.setattr(Q, "_qtm", lambda: _fake_qtm([a, b]))                 # Qt varsayılan bildirmezse ilk aygıt
    assert [d.is_default for d in Q.list_input_devices()] == [True, False]
    assert Q.audio_device(None) is a
    monkeypatch.setattr(Q, "_qtm", lambda: _fake_qtm([]))
    with pytest.raises(SmartError) as ei:
        Q.audio_device(None)
    assert ei.value.code == "audio.no_input"


def test_backend_choice(monkeypatch):
    monkeypatch.delenv("KADRAJ_AUDIO_BACKEND", raising=False)
    monkeypatch.setenv("KADRAJ_PLATFORM", "desktop")
    assert D.audio_backend() == "portaudio"
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    assert D.audio_backend() == "qt"
    monkeypatch.setenv("KADRAJ_AUDIO_BACKEND", "portaudio")
    assert D.audio_backend() == "portaudio"


def test_device_functions_go_to_qt_on_android(monkeypatch):
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    monkeypatch.delenv("KADRAJ_AUDIO_BACKEND", raising=False)
    monkeypatch.setitem(sys.modules, "sounddevice", None)                     # Android'de PortAudio yok
    dev = FakeDevice(kinds=("Float",))
    monkeypatch.setattr(Q, "_qtm", lambda: _fake_qtm([dev], default=dev))
    assert [d.name for d in D.list_input_devices()] == ["Dahili mikrofon"]
    assert D.check_input(0, SR, 1) is None and D.check_input(0, SR, 4).code == "audio.unsupported_channels"
    stream = D.default_stream_factory(SR, 1, 0, 512, lambda *a: None)
    assert isinstance(stream, Q.QtInputStream) and stream.sample_format == "Float"


def test_android_error_texts_talk_about_the_phone(qt_backend):
    err = D.map_audio_error(ImportError("PySide6.QtMultimedia"))
    assert err.code == "audio.no_backend" and "PortAudio" not in err.summary + err.steps[0]
    perm = D.permission_error()
    assert perm.code == "audio.permission" and "İzinler" in perm.steps[0] and "Mikrofon" in perm.steps[0]
    assert D.map_audio_error(PermissionError("denied")).steps == perm.steps


def test_desktop_error_texts_are_unchanged(monkeypatch):
    monkeypatch.setenv("KADRAJ_AUDIO_BACKEND", "portaudio")
    assert "libportaudio2" in D.map_audio_error(ImportError("x")).steps[0]
    assert "gizlilik" in D.permission_error().steps[0]


# ---- mikrofon izni ---------------------------------------------------------------------------------------------------
class FakePermission(P.MicPermission):
    def __init__(self, state=P.UNDETERMINED):
        self.state, self.requests = state, []

    def status(self):
        return self.state

    def request(self, context, done):
        self.requests.append(done)

    def answer(self, granted):
        self.state = P.GRANTED if granted else P.DENIED
        self.requests.pop()(granted)


def test_recording_waits_for_the_permission_and_then_starts(svc):
    svc._mic = perm = FakePermission()
    assert svc.start_recording() is None and svc.state == "idle" and svc.asking_permission
    assert svc.start_recording() is None and len(perm.requests) == 1               # tekrar basmak ikinci pencere açmaz
    perm.answer(True)
    assert svc.state == "recording" and not svc.asking_permission
    svc.mic.feed(0.5)
    take = svc.stop_recording()
    assert take is not None and take.duration_s == pytest.approx(0.5, abs=0.05)
    assert svc.start_recording() is not None and perm.requests == []               # izin bir kez verildi
    svc.stop_recording()


def test_denied_permission_shows_the_phone_settings_card(svc, qtbot, qt_backend):
    svc._mic = perm = FakePermission(P.DENIED)
    errors = []
    svc.errorRaised.connect(errors.append)
    assert svc.start_recording() is None and len(perm.requests) == 1               # reddedildiyse yine sorulur
    perm.answer(False)
    assert svc.state == "idle" and not svc.engine.running and len(errors) == 1
    assert errors[0].code == "audio.permission" and "İzinler" in errors[0].steps[0]
    assert svc.last_error is errors[0]


def test_mic_test_waits_for_the_permission(svc):
    svc._mic = perm = FakePermission()
    assert svc.start_mic_test(calibration=True) is None and svc.state == "idle"
    perm.answer(True)
    assert svc.state == "testing"
    svc.cancel_mic_test()


def test_granted_permission_is_not_asked_again(svc):
    svc._mic = perm = FakePermission(P.GRANTED)
    assert svc.start_recording() is not None and perm.requests == []
    svc.stop_recording()


def test_permission_object_by_platform(monkeypatch):
    monkeypatch.delenv("KADRAJ_AUDIO_BACKEND", raising=False)
    monkeypatch.setenv("KADRAJ_PLATFORM", "desktop")
    assert isinstance(P.default(), P.AlwaysGranted)
    monkeypatch.setenv("KADRAJ_AUDIO_BACKEND", "qt")                           # masaüstünde Qt yolu: izin sorusu yok
    assert isinstance(P.default(), P.AlwaysGranted)
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    assert isinstance(P.default(), P.QtMicPermission)


def test_qt_permission_api_answers_on_this_platform(qapp, qtbot):
    perm = P.QtMicPermission()
    assert perm.status() in (P.GRANTED, P.DENIED, P.UNDETERMINED)
    answers = []
    perm.request(qapp, answers.append)
    qtbot.waitUntil(lambda: len(answers) == 1, timeout=3000)
    assert answers[0] in (True, False)


def test_startup_line_names_qt_multimedia_on_android(monkeypatch):
    from kadraj.ui.shell import startup
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    monkeypatch.delenv("KADRAJ_AUDIO_BACKEND", raising=False)
    row = startup.detect_tools()["portaudio"]
    assert row.label == "Qt Multimedia" and row.ok
    monkeypatch.setenv("KADRAJ_PLATFORM", "desktop")
    assert startup.detect_tools()["portaudio"].label == "PortAudio"
