"""İstem tabanı: normalizasyon, eksik alan soruları, stil kütüphanesi, model dosyaları."""
from __future__ import annotations

import json
import re

import pytest

from kadraj.ai_engine.prompt_adapter import (ADAPTERS, CRITICAL_SLOTS, STYLE_IDS, PromptError, PromptSpec,
                                             missing_slots, resolve_style)
from kadraj.ai_engine.prompt_adapter.base import DATA_DIR, SLOT_QUESTIONS, style_library


def test_create_normalizes_text_and_negatives():
    spec = PromptSpec.create(subject="  Gün   batımı ", aspect="16:9", negatives="bulanıklık, yazı, yazı , ")
    assert spec.subject == "Gün batımı"
    assert spec.negatives == ("bulanıklık", "yazı")          # tekrarsız, boşlar atılır


def test_unknown_field_and_unsupported_aspect_are_refused_in_turkish():
    with pytest.raises(PromptError):
        PromptSpec.create(konu="x")
    with pytest.raises(PromptError) as exc:
        PromptSpec.create(subject="x", aspect="7:3")
    assert "7:3" in str(exc.value) and "16:9" in str(exc.value)


def test_style_accepts_id_or_turkish_name_and_keeps_free_text():
    assert resolve_style("Yağlı Boya") == "yagli_boya"
    assert resolve_style("sinematik") == "sinematik"
    assert resolve_style("bambaşka bir stil") is None
    assert PromptSpec.create(style="Retro Film").style == "retro_film"
    assert PromptSpec.create(style="neon ışıklı sokak").style == "neon ışıklı sokak"


def test_missing_slots_lists_critical_first_with_one_turkish_question_each():
    slots = missing_slots(PromptSpec())
    names = [s.name for s in slots]
    assert names[: len(CRITICAL_SLOTS)] and set(names[: len(CRITICAL_SLOTS)]) == set(CRITICAL_SLOTS)
    for slot in slots:
        assert slot.question.endswith("?")
        assert len(re.findall(r"[.!?]", slot.question[:-1])) == 0     # tek cümle
        assert slot.question == SLOT_QUESTIONS[slot.name]


def test_filled_spec_has_no_missing_critical_slots():
    spec = PromptSpec.create(subject="deniz", lighting="altın", style="sinematik", aspect="1:1")
    assert not [s for s in missing_slots(spec) if s.critical]


def test_style_library_has_the_eight_styles_with_turkish_names():
    library = style_library()
    assert set(library) == set(STYLE_IDS)
    assert {entry["name"] for entry in library.values()} == {
        "Sinematik", "Belgesel", "Yağlı Boya", "Çizgi Film", "Ürün Çekimi", "Retro Film", "Pastel", "Gerçekçi"}


@pytest.mark.parametrize("model", sorted(ADAPTERS))
def test_every_model_file_covers_all_styles_and_negatives(model):
    data = json.loads((DATA_DIR / f"{model}.json").read_text(encoding="utf-8"))
    assert data["model"] == model
    assert set(data["styles"]) == set(STYLE_IDS)
    assert isinstance(data["negatives"], list)
    if model == "dalle":
        assert data["negatives"] == []                       # DALL-E negatif desteklemez
