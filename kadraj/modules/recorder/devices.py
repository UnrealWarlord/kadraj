"""Giriş aygıtları ve ses hatalarının sade Türkçe karşılıkları.

``sounddevice`` / PortAudio **tembel** içe aktarılır: kurulu değilse (Docker, CI, başsız sunucu) ya da giriş aygıtı yoksa
``list_input_devices()`` boş liste / ``SmartError`` döner; uygulama asla düşmez.
"""
from __future__ import annotations

import errno
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ...core.contracts import Severity, SmartAction, SmartError
from ...core.i18n import t

#: Legend'de seçilebilir örnekleme hızları (plan §0.4); Basit Mod sabit 48 kHz.
SAMPLE_RATES = (44100, 48000, 88200, 96000)
SIMPLE_RATE = 48000


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    max_input_channels: int
    default_samplerate: float
    is_default: bool = False

    @property
    def label(self) -> str:
        return f"{self.name}"


def _err(code: str, technical: str = "", *, retryable: bool = False, actions=()) -> SmartError:
    key = f"recorder.err.{code}"
    return SmartError(f"audio.{code}", t(f"{key}.title"), t(f"{key}.summary"), cause=t(f"{key}.cause"),
                      steps=(t(f"{key}.step1"),), actions=tuple(actions), severity=Severity.ERROR,
                      technical=technical, retryable=retryable, source="recorder")


def backend_error(exc: BaseException) -> SmartError:
    return _err("no_backend", f"{type(exc).__name__}: {exc}")


def map_audio_error(exc: BaseException) -> SmartError:
    """PortAudio / sounddevice istisnasını ``SmartError``'a çevirir. ``SmartError`` olduğu gibi döner."""
    if isinstance(exc, SmartError):
        return exc
    text = f"{type(exc).__name__}: {exc}"
    low = str(exc).lower()
    if isinstance(exc, PermissionError) or "permission" in low or "access denied" in low:
        return _err("permission", text)
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.ENOSPC, errno.EDQUOT):
        from ...core.errors import translate
        return translate(exc, {"source": "recorder"})
    if "portaudio library not found" in low or "libportaudio" in low or isinstance(exc, (ImportError,)):
        return _err("no_backend", text)
    if "-9996" in low or "invalid device" in low or "no default input" in low:
        return _err("no_input", text)
    if "-9997" in low or "invalid sample rate" in low:
        return _err("unsupported_rate", text, retryable=True)
    if "-9998" in low or "invalid number of channels" in low:
        return _err("unsupported_channels", text, retryable=True)
    if "-9985" in low or "unavailable" in low or "busy" in low:
        return _err("device_busy", text, retryable=True)
    return _err("open_failed", text, retryable=True)


def _sd() -> Any:
    try:
        import sounddevice as sd
        return sd
    except Exception as exc:                       # PortAudio kütüphanesi yok → OSError da gelebilir
        raise backend_error(exc) from exc


def list_input_devices(sd_module: Any = None) -> List[InputDevice]:
    """Giriş kanalı olan aygıtlar (varsayılan işaretli). Backend yoksa ``SmartError``; aygıt yoksa ``[]``."""
    sd = sd_module or _sd()
    try:
        devices = sd.query_devices()
        try:
            default_idx = sd.default.device[0]
        except Exception:
            default_idx = -1
        out = []
        for i, d in enumerate(devices):
            if int(d.get("max_input_channels", 0)) > 0:
                out.append(InputDevice(i, str(d["name"]), int(d["max_input_channels"]),
                                       float(d.get("default_samplerate", 48000.0)), i == default_idx))
        return out
    except SmartError:
        raise
    except Exception as exc:
        raise map_audio_error(exc) from exc


def no_input_error() -> SmartError:
    return _err("no_input")


def check_input(device: Optional[Any], sample_rate: int, channels: int, sd_module: Any = None) -> Optional[SmartError]:
    """Aygıt bu ayarları destekliyor mu? Destekliyorsa ``None``, değilse açıklayıcı ``SmartError``."""
    sd = sd_module or _sd()
    try:
        sd.check_input_settings(device=device, channels=channels, samplerate=sample_rate, dtype="float32")
        return None
    except Exception as exc:
        return map_audio_error(exc)


def default_stream_factory(sample_rate: int, channels: int, device: Optional[Any], blocksize: int,
                           callback: Callable[..., None]) -> Any:
    """Gerçek ``sounddevice.InputStream``. Testler yerine sahte bir fabrika verir."""
    sd = _sd()
    try:
        return sd.InputStream(samplerate=sample_rate, channels=channels, dtype="float32", device=device,
                              blocksize=blocksize, callback=callback)
    except Exception as exc:
        raise map_audio_error(exc) from exc
