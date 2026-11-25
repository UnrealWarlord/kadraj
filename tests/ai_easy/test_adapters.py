"""Model adaptörleri: Midjourney, FLUX, Imagen, DALL-E, SDXL çıktı biçimleri."""
from __future__ import annotations

import pytest

from kadraj.ai_engine.prompt_adapter import PromptError, PromptSpec, adapt
from kadraj.ai_engine.prompt_adapter import dalle, sdxl

SPEC = PromptSpec.create(subject="Gün batımında bir sahil", lighting="sıcak altın ışık", angle="alçak açı",
                         style="Sinematik", aspect="16:9", lens="35 mm", mood="huzurlu",
                         negatives="bulanıklık, yazı")


def test_midjourney_uses_flags_and_style_stylize():
    out = adapt("midjourney", SPEC)
    assert out.model == "midjourney" and out.negative == ""
    assert out.text.endswith("--ar 16:9 --style raw --s 250 --no bulanıklık, yazı, blurry, watermark, text, low quality")
    assert out.params["ar"] == "16:9" and out.params["s"] == 250 and out.params["style"] == "raw"


def test_flux_is_a_sentence_with_no_clauses_and_no_negative_field():
    out = adapt("flux", SPEC)
    assert out.text.startswith("Gün batımında bir sahil, sıcak altın ışık, alçak açı, 35 mm lens, huzurlu mood,")
    assert out.text.endswith("No bulanıklık, no yazı, no blur, no watermark, no text.")
    assert out.negative == "" and out.params == {"aspect_ratio": "16:9"}


def test_imagen_uses_labeled_sections_and_separate_negative_prompt():
    out = adapt("imagen", SPEC)
    lines = out.text.split("\n")
    assert lines[0] == "Subject: Gün batımında bir sahil"
    assert "Lighting: sıcak altın ışık" in lines and "Lens: 35 mm" in lines and "Mood: huzurlu" in lines
    assert lines[-1] == "Aspect ratio: 16:9"
    assert out.negative.startswith("bulanıklık, yazı, blurry") and out.params == {"aspectRatio": "16:9"}


def test_dalle_is_a_paragraph_with_size_and_reports_dropped_negatives():
    out = adapt("dalle", SPEC)
    assert out.negative == ""
    assert out.text.startswith("Gün batımında bir sahil. The light is sıcak altın ışık.")
    assert out.params["size"] == "1792x1024" and out.params["quality"] == "standard"
    assert out.params["dropped_negatives"] == ["bulanıklık", "yazı"]


@pytest.mark.parametrize("aspect, size", [("1:1", "1024x1024"), ("9:16", "1024x1792"), ("21:9", "1792x1024")])
def test_dalle_size_options_follow_aspect(aspect, size):
    assert dalle.size_for(aspect) == size
    assert size in dalle.SIZE_OPTIONS


def test_sdxl_has_weights_negatives_and_generation_defaults():
    out = adapt("sdxl", SPEC)
    assert out.text.startswith("(Gün batımında bir sahil:1.2), sıcak altın ışık")
    assert "(cinematic" in out.text                       # stil parçası 1.1 ağırlıkla
    assert out.negative.startswith("bulanıklık, yazı, (low quality:1.4), blurry")
    assert out.params["width"] == 1344 and out.params["height"] == 768
    assert out.params["steps"] == 30 and out.params["sampler"] == "dpmpp_2m_karras"
    assert out.params["cfg"] == 6.5                       # sinematik stilin cfg geçersiz kılması


def test_defaults_apply_when_aspect_and_style_are_missing():
    out = adapt("sdxl", PromptSpec.create(subject="kedi"))
    assert out.params["width"] == 1344 and out.params["cfg"] == sdxl.DEFAULTS["cfg"]
    assert "--ar 16:9" in adapt("midjourney", PromptSpec.create(subject="kedi")).text
    assert adapt("dalle", PromptSpec.create(subject="kedi")).params["size"] == dalle.size_for("16:9")


def test_free_text_style_is_passed_through():
    out = adapt("flux", PromptSpec.create(subject="kulübe", style="karlı bir kış gecesi"))
    assert "karlı bir kış gecesi" in out.text


def test_unknown_model_is_refused_in_turkish():
    with pytest.raises(PromptError) as exc:
        adapt("uydurma", SPEC)
    assert "uydurma" in str(exc.value)
