"""Gerçek sağlayıcıları kayıt defterine kurar (``replace=True``; ``default_registry``'nin ping profillerinin yerine geçer)."""
from __future__ import annotations

from typing import Any, List

from .. import aistrings
from ...core import i18n
from .comfyui import ComfyUIProvider
from .elevenlabs import ElevenLabsProvider
from .images import BflFluxProvider, GoogleImagenProvider, OpenAIImageProvider
from .llm import AnthropicProvider, OllamaProvider, OpenAICompatProvider
from .video import VideoProvider


def install_all(registry: Any, *, settings: Any = None, transport: Any = None, sleep: Any = None, stock: bool = True) -> List[Any]:
    """Sekiz AI sağlayıcısını (+ istenirse Haiku'nun stok sağlayıcılarını) kurar. Birden çok kez çağrılabilir."""
    i18n.register_strings(aistrings.STRINGS)
    kw: dict = {"settings": settings, "transport": transport}
    if sleep is not None:
        kw["sleep"] = sleep
    providers = [AnthropicProvider(**kw), OpenAICompatProvider(**kw), OpenAIImageProvider(**kw), OllamaProvider(**kw),
                 BflFluxProvider(**kw), GoogleImagenProvider(**kw), ComfyUIProvider(**kw), ElevenLabsProvider(**kw),
                 VideoProvider(**kw)]
    for p in providers:
        registry.register(p, replace=True)
    if stock:
        from .stock import install as install_stock
        install_stock(registry, settings=settings, transport=transport)
    return providers
