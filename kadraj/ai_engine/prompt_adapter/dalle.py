"""DALL-E: betimleyici paragraf. Negatif alanı yoktur; istenmeyenler ``dropped_negatives`` ile raporlanır.

Boyutlar: 1024x1024 (kare), 1792x1024 (yatay), 1024x1792 (dikey).
"""
from __future__ import annotations

from .base import AdaptedPrompt, PromptSpec, aspect_or_default, style_fragment

MODEL = "dalle"
SIZES = {"1:1": "1024x1024", "16:9": "1792x1024", "3:2": "1792x1024", "21:9": "1792x1024",
         "9:16": "1024x1792", "4:5": "1024x1792"}
SIZE_OPTIONS = ("1024x1024", "1792x1024", "1024x1792")


def size_for(aspect: str) -> str:
    return SIZES[aspect]


def build(spec: PromptSpec) -> AdaptedPrompt:
    spec = spec.normalized()
    sentences = []
    if spec.subject:
        sentences.append(spec.subject.rstrip(".") + ".")
    if spec.lighting:
        sentences.append(f"The light is {spec.lighting}.")
    if spec.angle:
        sentences.append(f"The camera angle is {spec.angle}.")
    if spec.lens:
        sentences.append(f"Shot with a {spec.lens} lens.")
    if spec.mood:
        sentences.append(f"The mood is {spec.mood}.")
    frag = style_fragment(MODEL, spec.style)
    if frag:
        sentences.append(f"The style is {frag}.")
    ar = aspect_or_default(spec)
    return AdaptedPrompt(MODEL, " ".join(sentences), "",
                         {"size": size_for(ar), "quality": "standard", "dropped_negatives": list(spec.negatives)})
