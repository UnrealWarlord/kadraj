"""Google Imagen: etiketli bölümler (Subject, Lighting, Camera angle, Lens, Mood, Style, Aspect ratio)
ve ayrı ``negative_prompt`` alanı."""
from __future__ import annotations

from .base import (AdaptedPrompt, PromptSpec, aspect_or_default, merge_negatives, style_fragment)

MODEL = "imagen"


def build(spec: PromptSpec) -> AdaptedPrompt:
    spec = spec.normalized()
    ar = aspect_or_default(spec)
    sections = (
        ("Subject", spec.subject),
        ("Lighting", spec.lighting),
        ("Camera angle", spec.angle),
        ("Lens", spec.lens),
        ("Mood", spec.mood),
        ("Style", style_fragment(MODEL, spec.style)),
    )
    lines = [f"{label}: {value}" for label, value in sections if value]
    lines.append(f"Aspect ratio: {ar}")
    negative = ", ".join(merge_negatives(spec, MODEL))
    return AdaptedPrompt(MODEL, "\n".join(lines), negative, {"aspectRatio": ar})
