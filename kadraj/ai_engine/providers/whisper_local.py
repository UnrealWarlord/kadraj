"""Yerel konuşma tanıma: faster-whisper (isteğe bağlı paket; ``requirements-ai.txt``).

* Paket kurulu değilse ``SmartError`` — **"İsteğe bağlı paketi kur"** eylemiyle (kurulum komutunu panoya kopyalar).
* Model önce **yalnızca yerel önbellekten** aranır (çevrimdışı çalışır); yoksa ve ``allow_download`` ise indirilir; internet
  yoksa model-indirme hatası ``SmartError`` olur (uygulama düşmez).
* ``transcribe`` ilerleme (0..1) bildirir, iptali (``is_cancelled``) segmentler arasında izler.
* Model yükleyici enjekte edilebilir (``model_loader``): testler ağır modeli sahte nesneyle değiştirir.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...core.contracts import ProviderKind, Severity, SmartAction, SmartError
from ...core.errors import is_network_error, is_timeout_error, translate
from ...core.i18n import t
from .base import ModuleProvider

MODELS = ("tiny", "base", "small", "medium", "large-v3")
INSTALL_COMMAND = "pip install faster-whisper"


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: List[Segment] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0


class TranscribeCancelled(Exception):
    """Kullanıcı yazıya çevirmeyi iptal etti."""


def _err(code: str, technical: str = "", *, retryable: bool = False, actions=()) -> SmartError:
    key = f"recorder.err.{code}"
    return SmartError(f"whisper.{code}", t(f"{key}.title"), t(f"{key}.summary"), cause=t(f"{key}.cause"),
                      steps=(t(f"{key}.step1"),), actions=tuple(actions), severity=Severity.ERROR,
                      technical=technical, retryable=retryable, source="whisper_local")


class WhisperLocalProvider(ModuleProvider):
    def __init__(self, model_loader: Optional[Callable[..., Any]] = None) -> None:
        super().__init__("whisper_local", "Whisper (yerel)", ProviderKind.STT, "faster_whisper")
        self._loader = model_loader
        self._models: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # -- kullanılabilirlik ------------------------------------------------------------------
    def available(self) -> bool:
        if self._loader is not None:
            return True
        return self.test_connection().ok

    def missing_package_error(self) -> SmartError:
        return _err("whisper_missing", actions=(
            SmartAction("install_optional", t("recorder.err.whisper_missing.action"), "copy",
                        {"text": INSTALL_COMMAND}),))

    # -- model ---------------------------------------------------------------------------------
    def _make_model(self, size: str, local_only: bool) -> Any:
        if self._loader is not None:
            return self._loader(size, local_only)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise self.missing_package_error() from exc
        return WhisperModel(size, device="auto", compute_type="default", local_files_only=local_only)

    def load_model(self, size: str = "small", *, allow_download: bool = True) -> Any:
        with self._lock:
            if size in self._models:
                return self._models[size]
            try:
                model = self._make_model(size, True)               # önce yalnızca yerel önbellek
            except SmartError:
                raise
            except Exception as local_exc:
                if not allow_download:
                    raise _err("whisper_model", f"{type(local_exc).__name__}", retryable=True) from local_exc
                try:
                    model = self._make_model(size, False)          # indirme (internet gerekir)
                except SmartError:
                    raise
                except Exception as exc:
                    if is_network_error(exc) or is_timeout_error(exc) or "connect" in str(exc).lower():
                        raise _err("whisper_model", f"{type(exc).__name__}", retryable=True) from exc
                    raise translate(exc, {"source": "whisper_local"}) from exc
            self._models[size] = model
            return model

    # -- yazıya çevirme ----------------------------------------------------------------------------
    def transcribe(self, path: Path, *, language: Optional[str] = "tr", model_size: str = "small",
                   allow_download: bool = True, on_progress: Optional[Callable[[float], None]] = None,
                   is_cancelled: Optional[Callable[[], bool]] = None) -> Transcript:
        if self._loader is None and not self.test_connection().ok:
            raise self.missing_package_error()
        model = self.load_model(model_size, allow_download=allow_download)
        lang = None if (language in (None, "", "auto")) else language
        try:
            segments, info = model.transcribe(str(path), language=lang, vad_filter=True, beam_size=5)
            duration = float(getattr(info, "duration", 0.0) or 0.0)
            out: List[Segment] = []
            for seg in segments:
                if is_cancelled is not None and is_cancelled():
                    raise TranscribeCancelled()
                text = str(seg.text).strip()
                if text:
                    out.append(Segment(float(seg.start), float(seg.end), text))
                if on_progress is not None and duration > 0:
                    on_progress(min(1.0, float(seg.end) / duration))
            if on_progress is not None:
                on_progress(1.0)
            return Transcript(out, str(getattr(info, "language", lang or "")), duration)
        except (TranscribeCancelled, SmartError):
            raise
        except Exception as exc:
            raise translate(exc, {"source": "whisper_local", "path": str(path)}) from exc
