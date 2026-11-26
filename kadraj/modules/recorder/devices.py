"""Giriş aygıtları ve ses hatalarının sade Türkçe karşılıkları.

``sounddevice`` / PortAudio **tembel** içe aktarılır: kurulu değilse (Docker, CI, başsız sunucu) ya da giriş aygıtı yoksa
``list_input_devices()`` boş liste / ``SmartError`` döner; uygulama asla düşmez.

Android'de PortAudio yoktur: aynı üç işlev (aygıt listesi, ayar denetimi, akış fabrikası) ``qt_audio.py``'ye
(QtMultimedia) gider. Seçim ``audio_backend()``'dedir; ``KADRAJ_AUDIO_BACKEND=qt|portaudio`` zorlar.
"""
from __future__ import annotations

import errno
import os
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


def _err(code: str, technical: str = "", *, retryable: bool = False, actions=(), text: str = "") -> SmartError:
    """``audio.<code>`` hatası; metinler ``recorder.err.<text ya da code>`` anahtarlarından (Android'e özel metin
    için ``text`` verilir, hata kodu aynı kalır)."""
    key = f"recorder.err.{text or code}"
    return SmartError(f"audio.{code}", t(f"{key}.title"), t(f"{key}.summary"), cause=t(f"{key}.cause"),
                      steps=(t(f"{key}.step1"),), actions=tuple(actions), severity=Severity.ERROR,
                      technical=technical, retryable=retryable, source="recorder")


def audio_backend() -> str:
    """``qt`` (Android: QtMultimedia) ya da ``portaudio`` (masaüstü: sounddevice)."""
    forced = os.environ.get("KADRAJ_AUDIO_BACKEND", "").strip().lower()
    if forced in ("qt", "portaudio"):
        return forced
    from ... import device
    return "qt" if device.is_android() else "portaudio"


def backend_error(exc: BaseException) -> SmartError:
    return _no_backend(f"{type(exc).__name__}: {exc}")


def _no_backend(technical: str) -> SmartError:
    return _err("no_backend", technical, text="no_backend_qt" if audio_backend() == "qt" else "")


def unsupported_error(code: str, technical: str = "") -> SmartError:
    """Aygıtın desteklemediği ayar (``unsupported_rate`` / ``unsupported_channels`` / ``open_failed``)."""
    return _err(code, technical, retryable=True)


def permission_error(technical: str = "") -> SmartError:
    """Mikrofon izni verilmedi. Android'de adımlar telefonun uygulama izinlerini anlatır."""
    return _err("permission", technical, text="permission_android" if audio_backend() == "qt" else "")


def map_audio_error(exc: BaseException) -> SmartError:
    """PortAudio / sounddevice istisnasını ``SmartError``'a çevirir. ``SmartError`` olduğu gibi döner."""
    if isinstance(exc, SmartError):
        return exc
    text = f"{type(exc).__name__}: {exc}"
    low = str(exc).lower()
    if isinstance(exc, PermissionError) or "permission" in low or "access denied" in low:
        return permission_error(text)
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.ENOSPC, errno.EDQUOT):
        from ...core.errors import translate
        return translate(exc, {"source": "recorder"})
    if "portaudio library not found" in low or "libportaudio" in low or isinstance(exc, (ImportError,)):
        return _no_backend(text)
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
    if sd_module is None and audio_backend() == "qt":
        from . import qt_audio
        try:
            return qt_audio.list_input_devices()
        except Exception as exc:
            raise map_audio_error(exc) from exc
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
    if sd_module is None and audio_backend() == "qt":
        from . import qt_audio
        try:
            return qt_audio.check_input(device, sample_rate, channels)
        except Exception as exc:
            return map_audio_error(exc)
    sd = sd_module or _sd()
    try:
        sd.check_input_settings(device=device, channels=channels, samplerate=sample_rate, dtype="float32")
        return None
    except Exception as exc:
        return map_audio_error(exc)


def default_stream_factory(sample_rate: int, channels: int, device: Optional[Any], blocksize: int,
                           callback: Callable[..., None]) -> Any:
    """Gerçek ``sounddevice.InputStream`` (Android'de ``QAudioSource``). Testler yerine sahte bir fabrika verir."""
    if audio_backend() == "qt":
        from . import qt_audio
        try:
            return qt_audio.stream_factory(sample_rate, channels, device, blocksize, callback)
        except Exception as exc:
            raise map_audio_error(exc) from exc
    sd = _sd()
    try:
        return sd.InputStream(samplerate=sample_rate, channels=channels, dtype="float32", device=device,
                              blocksize=blocksize, callback=callback)
    except Exception as exc:
        raise map_audio_error(exc) from exc
