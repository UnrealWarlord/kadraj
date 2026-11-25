"""İstem adaptörü: ortak ``PromptSpec``, eksik alan soruları ve kütüphane yükleyicileri.

Kullanıcının serbest metinleri (konu, ışık, açı, …) çevrilmez; olduğu gibi kullanılır. Stil
kütüphanesi ortak ``data/styles.json`` ve model başına ``data/<model>.json`` dosyalarındadır.
Adaptörler (``midjourney``, ``flux``, ``imagen``, ``dalle``, ``sdxl``) ``build(spec)`` ile
``AdaptedPrompt`` döner; asla istisna yerine ``PromptError`` (Türkçe) yükseltir.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent / "data"
ASPECTS: Tuple[str, ...] = ("16:9", "9:16", "1:1", "4:5", "3:2", "21:9")
DEFAULT_ASPECT = "16:9"
STYLE_IDS: Tuple[str, ...] = ("sinematik", "belgesel", "yagli_boya", "cizgi_film", "urun_cekimi",
                              "retro_film", "pastel", "gercekci")
#: Eksikse istemin zayıflayacağı alanlar; ``missing_slots`` bunları önce listeler.
CRITICAL_SLOTS: Tuple[str, ...] = ("subject", "lighting", "style", "aspect")
SLOT_ORDER: Tuple[str, ...] = ("subject", "lighting", "angle", "style", "aspect", "lens", "mood", "negatives")
SLOT_QUESTIONS: Dict[str, str] = {
    "subject": "Görselde ne olsun; ana konu nedir?",
    "lighting": "Işık nasıl olsun; gün ışığı mı, sert bir gölge mi?",
    "angle": "Kamera hangi açıdan çeksin; göz hizası mı, yukarıdan mı?",
    "style": "Hangi stilde olsun; sinematik, belgesel ya da çizgi film gibi?",
    "aspect": "Kare mi, yatay mı (16:9), yoksa dikey mi (9:16) olsun?",
    "lens": "Hangi lens hissi olsun; geniş açı mı, 85 mm portre mi?",
    "mood": "Hangi ruh hali hâkim olsun; sakin, gergin ya da neşeli mi?",
    "negatives": "İstemediğin şeyler neler; bulanıklık, yazı ya da fazla kalabalık mı?",
}
_TEXT_FIELDS = ("subject", "lighting", "angle", "style", "aspect", "lens", "mood", "idea")
_SPACE = re.compile(r"\s+")


class PromptError(ValueError):
    """Geçersiz istem bilgisi. Mesaj Basit Türkçedir."""


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _split(value: Any) -> Tuple[str, ...]:
    parts = value.split(",") if isinstance(value, str) else list(value or ())
    return tuple(p for p in (_clean(x) for x in parts) if p)


@dataclass(frozen=True)
class MissingSlot:
    name: str
    question: str
    critical: bool


@dataclass(frozen=True)
class PromptSpec:
    subject: str = ""
    lighting: str = ""
    angle: str = ""
    style: str = ""                 # stil kimliği (STYLE_IDS) ya da serbest metin
    aspect: str = ""                # ASPECTS içinden; boşsa DEFAULT_ASPECT
    lens: str = ""
    mood: str = ""
    negatives: Tuple[str, ...] = ()  # istenmeyenler
    idea: str = ""                  # kullanıcının özgün fikri (kayıt için)

    @classmethod
    def create(cls, *, negatives: Any = (), **fields_: Any) -> "PromptSpec":
        values = {k: v for k, v in fields_.items() if k in _TEXT_FIELDS}
        unknown = set(fields_) - set(_TEXT_FIELDS)
        if unknown:
            raise PromptError(f"Bilinmeyen alan: {', '.join(sorted(unknown))}")
        return cls(negatives=_split(negatives), **values).normalized()

    def normalized(self) -> "PromptSpec":
        aspect = _clean(self.aspect)
        if aspect and aspect not in ASPECTS:
            raise PromptError(f"Desteklenmeyen oran: {aspect}. Şunlardan birini seç: {', '.join(ASPECTS)}.")
        style = resolve_style(self.style) or _clean(self.style)
        negs = tuple(dict.fromkeys(n for n in (_clean(x) for x in self.negatives) if n))
        return replace(self, subject=_clean(self.subject), lighting=_clean(self.lighting),
                       angle=_clean(self.angle), style=style, aspect=aspect, lens=_clean(self.lens),
                       mood=_clean(self.mood), negatives=negs, idea=_clean(self.idea))


def missing_slots(spec: PromptSpec) -> List[MissingSlot]:
    """Boş alanlar için Türkçe tek cümlelik soru. Kritik alanlar önce gelir."""
    values = {
        "subject": spec.subject, "lighting": spec.lighting, "angle": spec.angle, "style": spec.style,
        "aspect": spec.aspect, "lens": spec.lens, "mood": spec.mood, "negatives": ", ".join(spec.negatives),
    }
    out = [MissingSlot(name, SLOT_QUESTIONS[name], name in CRITICAL_SLOTS)
           for name in SLOT_ORDER if not values[name]]
    out.sort(key=lambda m: not m.critical)
    return out


@dataclass(frozen=True)
class AdaptedPrompt:
    model: str
    text: str
    negative: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Kütüphaneler
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _load(name: str) -> Dict[str, Any]:
    with (DATA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def style_library() -> Dict[str, Dict[str, str]]:
    return dict(_load("styles.json")["styles"])


def model_profile(model: str) -> Dict[str, Any]:
    return _load(f"{model}.json")


def resolve_style(text: Any) -> Optional[str]:
    """Stil kimliği ya da Türkçe adı (büyük/küçük harf duyarsız) → kimlik. Bulunamazsa ``None``."""
    raw = _clean(text).lower()
    if not raw:
        return None
    library = style_library()
    if raw in library:
        return raw
    for sid, entry in library.items():
        if entry["name"].lower() == raw:
            return sid
    return None


def style_fragment(model: str, style: str) -> str:
    sid = resolve_style(style)
    if sid is None:
        return _clean(style)
    entry = model_profile(model)["styles"].get(sid)
    return entry["fragment"] if entry else style_library()[sid]["en"]


def style_params(model: str, style: str) -> Dict[str, Any]:
    sid = resolve_style(style)
    if sid is None:
        return {}
    return dict(model_profile(model)["styles"].get(sid, {}).get("params", {}))


def merge_negatives(spec: PromptSpec, model: str) -> Tuple[str, ...]:
    """Kullanıcının istenmeyenleri önce, model varsayılanları sonra; tekrarsız."""
    defaults = tuple(model_profile(model).get("negatives", []))
    return tuple(dict.fromkeys((*spec.negatives, *defaults)))


def aspect_or_default(spec: PromptSpec) -> str:
    return spec.aspect or DEFAULT_ASPECT


def join_parts(*parts: Iterable[str] | str) -> str:
    return ", ".join(p for p in parts if p)
