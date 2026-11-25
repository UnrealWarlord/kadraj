"""FLUX: doğal İngilizce cümle. İstenmeyenler "No …" cümlesiyle yazılır; ayrı negatif alanı yoktur."""
from __future__ import annotations

from .base import (AdaptedPrompt, PromptSpec, aspect_or_default, join_parts, merge_negatives,
                   style_fragment)

MODEL = "flux"


def build(spec: PromptSpec) -> AdaptedPrompt:
    spec = spec.normalized()
    subject = spec.subject or "scene"
    sentence = subject[:1].upper() + subject[1:]
    clauses = join_parts(
        spec.lighting,
        spec.angle,
        f"{spec.lens} lens" if spec.lens else "",
        f"{spec.mood} mood" if spec.mood else "",
        style_fragment(MODEL, spec.style),
    )
    body = f"{sentence}, {clauses}." if clauses else f"{sentence}."
    negs = merge_negatives(spec, MODEL)
    no = ("No " + ", no ".join(negs) + ".") if negs else ""
    return AdaptedPrompt(MODEL, f"{body} {no}".strip(), "", {"aspect_ratio": aspect_or_default(spec)})
