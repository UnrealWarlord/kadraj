"""QtMultimedia ses girişi: Android'de ``sounddevice``/PortAudio yerine (``docs/MOBILE_PLAN.md`` §6, M18).

Sözleşme ``devices.py``'deki PortAudio yoluyla aynıdır, böylece kayıt motoru (``engine.py``) değişmez:

* ``list_input_devices()`` → ``[InputDevice]`` (``QMediaDevices.audioInputs()``; ``index`` bu listedeki sıra)
* ``check_input(device, rate, channels)`` → ``None`` ya da ``SmartError``
* ``stream_factory(rate, channels, device, blocksize, callback)`` → ``start()/stop()/close()`` yöntemli akış

Akış ``QAudioSource``'u çekme (pull) kipinde açar; ``readyRead`` geldikçe baytlar ``float32`` ``(kare, kanal)``
bloğa çevrilip motorun geri çağrısına verilir (``callback(blok, kare, None, None)``). Qt'nin olayları nesnenin
iş parçacığında (arayüz) gelir; geri çağrı hafiftir (kopya, kazanç, VU, kapı, kuyruk). Örnek biçimi tercih sırası:
Float, Int16, Int32, UInt8 (cihazın desteklediği ilki).

Mikrofon izni (Android ``RECORD_AUDIO``) ``permission.py``'dedir; akış açılmadan önce servis ister.
Masaüstünde bu yol ``KADRAJ_AUDIO_BACKEND=qt`` ile denenebilir (Linux'ta PulseAudio ile sınandı).
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

import numpy as np

from .devices import InputDevice, backend_error, map_audio_error, no_input_error, unsupported_error

#: Tercih sırası (``QAudioFormat.SampleFormat`` adları)
FORMAT_ORDER = ("Float", "Int16", "Int32", "UInt8")
#: Qt'nin iç arabelleği: bu kadar blok (gecikme ile kesinti arasında denge; ~85 ms @ 48 kHz, 1024 kare)
BUFFER_BLOCKS = 4


def _qtm() -> Any:
    try:
        from PySide6 import QtMultimedia
        return QtMultimedia
    except Exception as exc:  # noqa: BLE001 — Qt Multimedia yüklenemedi
        raise backend_error(exc) from exc


def list_input_devices() -> List[InputDevice]:
    """Giriş aygıtları; varsayılan işaretli (Qt bildirmezse ilk aygıt)."""
    qtm = _qtm()
    inputs = list(qtm.QMediaDevices.audioInputs())
    default = qtm.QMediaDevices.defaultAudioInput()
    default_id = bytes(default.id().data()) if not default.isNull() else b""
    out = []
    for i, d in enumerate(inputs):
        fmt = d.preferredFormat()
        out.append(InputDevice(i, d.description() or f"Mikrofon {i + 1}", max(1, int(d.maximumChannelCount())),
                               float(fmt.sampleRate() or 48000), bool(d.isDefault())
                               or (bool(default_id) and bytes(d.id().data()) == default_id)))
    if out and not any(d.is_default for d in out):
        first = out[0]
        out[0] = InputDevice(first.index, first.name, first.max_input_channels, first.default_samplerate, True)
    return out


def audio_device(index: Optional[int]) -> Any:
    """``index`` (``list_input_devices`` sırası) → ``QAudioDevice``; ``None``/geçersizse varsayılan aygıt."""
    qtm = _qtm()
    inputs = list(qtm.QMediaDevices.audioInputs())
    if isinstance(index, int) and 0 <= index < len(inputs):
        return inputs[index]
    default = qtm.QMediaDevices.defaultAudioInput()
    if not default.isNull():
        return default
    if inputs:
        return inputs[0]
    raise no_input_error()


def choose_format(device: Any, sample_rate: int, channels: int) -> Optional[Any]:
    """Aygıtın desteklediği ilk ``QAudioFormat`` (``FORMAT_ORDER`` sırasıyla); hiçbiri yoksa ``None``."""
    qtm = _qtm()
    for name in FORMAT_ORDER:
        fmt = qtm.QAudioFormat()
        fmt.setSampleRate(int(sample_rate))
        fmt.setChannelCount(int(channels))
        fmt.setSampleFormat(getattr(qtm.QAudioFormat.SampleFormat, name))
        if device.isFormatSupported(fmt):
            return fmt
    return None


def check_input(device: Optional[int], sample_rate: int, channels: int) -> Optional[Any]:
    """Aygıt bu ayarları destekliyor mu? Destekliyorsa ``None``, değilse açıklayıcı ``SmartError``."""
    try:
        dev = audio_device(device)
    except Exception as exc:  # noqa: BLE001 — aygıt yok / Qt Multimedia yok
        return map_audio_error(exc)
    if choose_format(dev, sample_rate, channels) is not None:
        return None
    if channels > max(1, int(dev.maximumChannelCount())):
        return unsupported_error("unsupported_channels", f"{dev.description()}: {channels} kanal")
    low, high = int(dev.minimumSampleRate()), int(dev.maximumSampleRate())
    if high and not low <= sample_rate <= high:
        return unsupported_error("unsupported_rate", f"{dev.description()}: {sample_rate} Hz ({low}–{high})")
    return unsupported_error("open_failed", f"{dev.description()}: {sample_rate} Hz, {channels} kanal")


def to_float(raw: bytes, sample_format: str, channels: int) -> np.ndarray:
    """Qt örnekleri (yerel bayt sırası, iç içe kanallar) → ``float32`` ``(kare, kanal)``, −1..1."""
    if sample_format == "Float":
        x = np.frombuffer(raw, dtype=np.float32).copy()
    elif sample_format == "Int16":
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_format == "Int32":
        x = (np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483648.0).astype(np.float32)
    elif sample_format == "UInt8":
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"desteklenmeyen örnek biçimi: {sample_format}")
    return x.reshape(-1, channels)


class QtInputStream:
    """``QAudioSource`` (çekme kipi) → motorun geri çağrısı. ``source_factory`` testlerde sahte kaynak verir."""

    def __init__(self, device: Any, fmt: Any, blocksize: int, callback: Callable[..., None],
                 source_factory: Optional[Callable[[Any, Any], Any]] = None) -> None:
        self._fmt = fmt
        self._kind = fmt.sampleFormat().name
        self._channels = int(fmt.channelCount())
        self._frame_bytes = int(fmt.bytesPerFrame())
        self._callback = callback
        self._rest = b""
        self._io: Any = None
        self._source = (source_factory or _qtm().QAudioSource)(device, fmt)
        try:
            self._source.setBufferSize(int(fmt.bytesForFrames(max(256, int(blocksize)) * BUFFER_BLOCKS)))
        except Exception:  # noqa: BLE001 — arabellek önerisi; desteklenmezse Qt'ninki kalır
            pass

    @property
    def sample_format(self) -> str:
        return self._kind

    def start(self) -> None:
        if self._io is not None:
            return
        io = self._source.start()
        error = self._source.error()
        if io is None or getattr(error, "name", "NoError") != "NoError":
            self._source.stop()
            raise OSError(f"QAudioSource açılamadı: {getattr(error, 'name', error)}")
        self._io = io
        io.readyRead.connect(self._pull)

    def _pull(self) -> None:
        io = self._io
        if io is None:
            return
        data = io.readAll()
        raw = bytes(data.data()) if hasattr(data, "data") else bytes(data)
        if not raw:
            return
        buf = self._rest + raw
        whole = len(buf) - len(buf) % self._frame_bytes
        self._rest = buf[whole:]
        if whole:
            block = to_float(buf[:whole], self._kind, self._channels)
            self._callback(block, len(block), None, None)

    def drain(self) -> None:
        """Qt arabelleğinde bekleyen sesi hemen motora verir (kayıt bitmeden önce motor çağırır)."""
        self._pull()

    def stop(self) -> None:
        io = self._io
        if io is not None:
            try:
                self._pull()                              # Qt arabelleğinde kalanı da motora ver
            finally:
                self._io = None
            try:
                io.readyRead.disconnect(self._pull)
            except Exception:  # noqa: BLE001
                pass
        self._rest = b""
        self._source.stop()

    def close(self) -> None:
        self.stop()


def stream_factory(sample_rate: int, channels: int, device: Optional[int], blocksize: int,
                   callback: Callable[..., None]) -> QtInputStream:
    """``RecorderEngine`` akış fabrikası (Qt). Aygıt/biçim yoksa ``SmartError``."""
    dev = audio_device(device)
    fmt = choose_format(dev, sample_rate, channels)
    if fmt is None:
        raise check_input(device, sample_rate, channels) or no_input_error()
    return QtInputStream(dev, fmt, blocksize, callback)
