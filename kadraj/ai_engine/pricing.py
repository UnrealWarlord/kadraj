"""Maliyet **tahmini** tablosu (USD). Gerçek fatura sağlayıcıdadır; bunlar bütçe/kota göstergeleri içindir.

Fiyatlar ``settings["pricing.<anahtar>"]`` ile geçersiz kılınabilir (``cost(..., overrides=)``). Yerel sağlayıcılar 0.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

# model öneki → (girdi $/1M token, çıktı $/1M token)
LLM_PER_MTOK: Dict[str, tuple] = {
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-5": (0.10, 0.50), "claude-haiku-4": (1.0, 5.0), "claude-fable": (8.0, 40.0),
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.5, 10.0), "gpt-4.1": (2.0, 8.0), "gpt-5": (1.25, 10.0),
}
IMAGE_PER_IMAGE: Dict[str, float] = {
    "flux-pro-1.1": 0.04, "flux-pro": 0.05, "flux-dev": 0.025, "flux-kontext": 0.04,
    "imagen-4": 0.04, "imagen-3": 0.03, "dall-e-3": 0.04, "gpt-image-1": 0.04,
}
TTS_PER_CHAR = 0.00030            # ElevenLabs ~ $0.30 / 1k karakter (plana göre değişir)
SFX_PER_SECOND = 0.01
VIDEO_PER_SECOND = 0.10           # genel varsayılan; Ayarlar'dan değiştirilir


def _lookup(table: Mapping[str, Any], model: str) -> Optional[Any]:
    best = None
    for key in table:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return table[best] if best is not None else None


def llm_cost(model: str, tokens_in: int, tokens_out: int, *, local: bool = False) -> float:
    if local:
        return 0.0
    price = _lookup(LLM_PER_MTOK, model or "")
    if price is None:
        return 0.0
    return round(tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1], 6)


def image_cost(model: str, n: int = 1, *, local: bool = False) -> float:
    if local:
        return 0.0
    price = _lookup(IMAGE_PER_IMAGE, model or "")
    return round((price if price is not None else 0.04) * n, 6)


def tts_cost(chars: int) -> float:
    return round(chars * TTS_PER_CHAR, 6)


def sfx_cost(seconds: float) -> float:
    return round(seconds * SFX_PER_SECOND, 6)


def video_cost(seconds: float, per_second: Optional[float] = None) -> float:
    return round(seconds * (VIDEO_PER_SECOND if per_second is None else per_second), 6)
