"""Midjourney: virgülle ayrılmış ifadeler + parametreler (``--ar``, ``--style raw``, ``--s``, ``--no``)."""
from __future__ import annotations

from .base import (AdaptedPrompt, PromptSpec, aspect_or_default, join_parts, merge_negatives,
                   style_fragment, style_params)

MODEL = "midjourney"
DEFAULT_STYLIZE = 100


def build(spec: PromptSpec) -> AdaptedPrompt:
    spec = spec.normalized()
    body = join_parts(spec.subject, spec.lighting, spec.angle, spec.lens, spec.mood,
                      style_fragment(MODEL, spec.style))
    ar = aspect_or_default(spec)
    stylize = int(style_params(MODEL, spec.style).get("s", DEFAULT_STYLIZE))
    no = ", ".join(merge_negatives(spec, MODEL))
    flags = f"--ar {ar} --style raw --s {stylize}" + (f" --no {no}" if no else "")
    return AdaptedPrompt(MODEL, f"{body} {flags}".strip(), "",
                         {"ar": ar, "style": "raw", "s": stylize, "no": no})
