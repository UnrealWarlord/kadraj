"""SDXL (ComfyUI): pozitif ve negatif metin, ``(terim:1.2)`` ağırlıkları, adım/CFG/örnekleyici varsayılanları."""
from __future__ import annotations

from .base import (AdaptedPrompt, PromptSpec, aspect_or_default, join_parts, merge_negatives,
                   style_fragment, style_params)

MODEL = "sdxl"
RESOLUTIONS = {"1:1": (1024, 1024), "16:9": (1344, 768), "9:16": (768, 1344), "4:5": (896, 1120),
               "3:2": (1216, 832), "21:9": (1536, 640)}
DEFAULTS = {"steps": 30, "cfg": 7.0, "sampler": "dpmpp_2m_karras", "scheduler": "karras"}


def _weight(term: str, weight: float) -> str:
    return f"({term}:{weight})" if term else ""


def build(spec: PromptSpec) -> AdaptedPrompt:
    spec = spec.normalized()
    frag = style_fragment(MODEL, spec.style)
    positive = join_parts(_weight(spec.subject, 1.2), spec.lighting, spec.angle, spec.lens, spec.mood,
                          _weight(frag, 1.1) if frag else "")
    negative = join_parts(*(f"({n}:1.4)" if n == "low quality" else n for n in merge_negatives(spec, MODEL)))
    width, height = RESOLUTIONS[aspect_or_default(spec)]
    params = {"width": width, "height": height, **DEFAULTS, **style_params(MODEL, spec.style)}
    return AdaptedPrompt(MODEL, positive, negative, params)
