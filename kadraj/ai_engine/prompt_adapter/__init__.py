"""İstem adaptörleri: ``adapt(model, spec)`` tek giriş noktasıdır. Modeller: midjourney, flux, imagen, dalle, sdxl."""
from __future__ import annotations

from types import ModuleType
from typing import Dict

from . import dalle, flux, imagen, midjourney, sdxl
from .base import (ASPECTS, CRITICAL_SLOTS, DEFAULT_ASPECT, STYLE_IDS, AdaptedPrompt, MissingSlot,
                   PromptError, PromptSpec, missing_slots, resolve_style, style_library)

ADAPTERS: Dict[str, ModuleType] = {"midjourney": midjourney, "flux": flux, "imagen": imagen,
                                   "dalle": dalle, "sdxl": sdxl}
MODELS = tuple(ADAPTERS)


def adapt(model: str, spec: PromptSpec) -> AdaptedPrompt:
    module = ADAPTERS.get(model)
    if module is None:
        raise PromptError(f"Bilinmeyen model: {model}. Şunlardan biri: {', '.join(MODELS)}.")
    return module.build(spec.normalized())


__all__ = ["ADAPTERS", "MODELS", "adapt", "ASPECTS", "CRITICAL_SLOTS", "DEFAULT_ASPECT", "STYLE_IDS",
           "AdaptedPrompt", "MissingSlot", "PromptError", "PromptSpec", "missing_slots", "resolve_style",
           "style_library"]
