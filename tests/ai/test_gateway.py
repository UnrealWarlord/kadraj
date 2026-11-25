"""AIGateway + kayıt defteri zinciri: bulut → yerel, 429 geri çekilme, çevrimdışı atlama, maliyet kaydı, istem uyarlama."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from kadraj.ai_engine.pipeline.gateway import AIGateway
from kadraj.ai_engine.prompt_adapter import PromptSpec
from kadraj.ai_engine.providers.base import ProviderRegistry, default_registry
from kadraj.ai_engine.providers.install import install_all
from kadraj.core.contracts import ProviderKind, SmartError

from .conftest import FakeDB, FakeSettings, FakeVault, Recorder, png_bytes

IMG = png_bytes()


def ok_llm(text):
    return httpx.Response(200, json={"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                     "usage": {"input_tokens": 100, "output_tokens": 20}})


def make(router, vault, settings=None, **gw):
    rec = Recorder(router)
    reg = default_registry(settings)
    reg.sleep = lambda s: None
    install_all(reg, settings=settings, transport=rec.transport, sleep=lambda s: None)
    db = FakeDB()
    return AIGateway(reg, vault, settings, db=lambda: db, **gw), rec, db, reg


def test_llm_prefers_cloud_records_usage_and_falls_to_local_when_offline(vault):
    def router(r):
        if r.url.host == "api.anthropic.com":
            raise httpx.ConnectError("offline")
        if r.url.host == "api.openai.com":
            raise httpx.ConnectError("offline")
        return httpx.Response(200, json={"message": {"content": "yerel yanıt"}, "prompt_eval_count": 7, "eval_count": 3})
    gw, rec, db, reg = make(router, vault)
    res = gw.llm("selam")
    assert res.text == "yerel yanıt" and gw.last_provider == "ollama"
    hosts = [r.url.host for r in rec.requests]
    assert hosts.count("api.anthropic.com") == 1 and "api.openai.com" not in hosts           # çevrimdışı: kalan bulut atlandı
    assert db.usage == [{"provider": "ollama", "model": "llama3.1", "tokens_in": 7, "tokens_out": 3, "units": 0.0, "cost_usd": 0.0}]


def test_llm_cloud_success_writes_cost_and_session_totals(vault):
    gw, rec, db, reg = make(lambda r: ok_llm("tamam"), vault)
    gw.llm("x")
    gw.llm("y", tier="cheap")
    assert [u["model"] for u in db.usage] == ["claude-opus-5-5", "claude-haiku-5-5"]
    assert db.usage[0]["cost_usd"] == pytest.approx(100 / 1e6 * 4 + 20 / 1e6 * 20)
    assert gw.session_cost == pytest.approx(sum(u["cost_usd"] for u in db.usage)) and gw.session_tokens == 240


def test_rate_limit_backs_off_then_falls_back(vault):
    state = {"n": 0}

    def router(r):
        if r.url.host == "api.anthropic.com":
            state["n"] += 1
            return httpx.Response(429, headers={"retry-after": "1"}, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": "openai yanıtı"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 5}})
    gw, rec, db, reg = make(router, vault)
    res = gw.llm("selam")
    assert res.text == "openai yanıtı" and gw.last_provider == "openai_compat"
    assert state["n"] == 1 + reg.max_rate_retries and reg.is_cooling("anthropic")               # üstel geri çekilme sonrası soğuma


def test_no_provider_gives_friendly_cards_not_crashes():
    reg = ProviderRegistry()
    from kadraj.ai_engine.providers.llm import AnthropicProvider
    reg.register(AnthropicProvider())
    gw = AIGateway(reg, FakeVault())
    for fn, code in ((lambda: gw.llm("x"), "ai.no_llm"), (lambda: gw.vision(IMG, "x"), "ai.no_vision"), (lambda: gw.image("x"), "ai.no_image"),
                     (lambda: gw.video("x"), "ai.no_video"), (lambda: gw.tts("x"), "ai.no_tts"), (lambda: gw.edit_image(IMG, IMG, "x"), "ai.edit_unavailable")):
        with pytest.raises(SmartError) as ei:
            fn()
        assert ei.value.code == code, code
    assert not gw.has_llm() and not gw.has_image() and not gw.has_vision() and gw.upscale(IMG) is None and gw.img2img(IMG, "x") is None


def test_vision_only_uses_vision_capable_providers(vault):
    gw, rec, db, reg = make(lambda r: ok_llm("bir vapur"), vault)
    res = gw.vision(IMG, "Bunu anlat")
    assert res.text == "bir vapur" and rec.body()["messages"][-1]["content"][0]["type"] == "image"
    assert gw.has_vision()


def test_image_adapts_prompt_per_provider_and_falls_back(vault):
    def router(r):
        if r.url.host == "api.bfl.ai":
            return httpx.Response(500, json={})                                      # FLUX bozuk → sıradaki
        if r.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(200, json={"predictions": [{"bytesBase64Encoded": base64.b64encode(IMG).decode()}]})
        return httpx.Response(404)
    gw, rec, db, reg = make(router, vault)
    spec = PromptSpec.create(subject="eski bir vapur", lighting="sisli sabah", style="sinematik", aspect="16:9", idea="sisli İstanbul")
    res, adapted = gw.image(spec, prefer_model="flux")
    assert gw.last_provider == "google_imagen" and adapted.model == "imagen" and res.images == [IMG]
    assert [r.url.host for r in rec.requests][:2] == ["api.bfl.ai", "generativelanguage.googleapis.com"]
    assert "eski bir vapur" in json.loads(rec.requests[-1].content)["instances"][0]["prompt"]
    assert db.usage[-1]["provider"] == "google_imagen" and db.usage[-1]["units"] == 1 and db.usage[-1]["cost_usd"] == pytest.approx(0.04)
    res, adapted = gw.image("düz metin istemi", extra={"aspect": "1:1"})
    assert adapted.text == "düz metin istemi"


def test_image_prefers_requested_model_provider_first(vault):
    seen = []

    def router(r):
        seen.append(r.url.host)
        return httpx.Response(200, json={"predictions": [{"bytesBase64Encoded": base64.b64encode(IMG).decode()}]})
    gw, rec, db, reg = make(router, vault)
    gw.image(PromptSpec.create(subject="kedi", lighting="gün ışığı", style="pastel", aspect="1:1"), prefer_model="imagen")
    assert seen[0] == "generativelanguage.googleapis.com"                           # tercih edilen kayıt sırasını aşar


def test_tts_sfx_video_tasks_and_edit_fallback_chain(vault):
    pcm = b"\x00\x01" * 2400

    def router(r):
        if r.url.host == "api.elevenlabs.io":
            return httpx.Response(200, content=pcm)
        if r.url.host == "127.0.0.1" and r.url.port == 8188:
            raise httpx.ConnectError("comfy kapalı")
        if r.url.host == "api.openai.com" and r.url.path == "/v1/images/edits":
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(IMG).decode()}]})
        return httpx.Response(404)
    gw, rec, db, reg = make(router, vault)
    a = gw.tts("Merhaba", voice_id="V")
    s = gw.sfx("yağmur", 2.0)
    assert a.wav[:4] == b"RIFF" and s.wav[:4] == b"RIFF"
    res = gw.edit_image(IMG, IMG, "ağaç ekle")                                         # ComfyUI kapalı → OpenAI düzenleme
    assert gw.last_provider == "openai_image" and res.images == [IMG]
    edit_req = [r for r in rec.requests if r.url.path == "/v1/images/edits"][0]
    assert b'name="mask"' in edit_req.content
    kinds = {u["provider"] for u in db.usage}
    assert kinds == {"elevenlabs", "openai_image"}


def test_install_all_registers_everything_and_is_idempotent():
    reg = default_registry()
    install_all(reg)
    install_all(reg)
    ids = {p.id for p in reg.list()}
    assert ids >= {"anthropic", "openai_compat", "openai_image", "ollama", "bfl_flux", "google_imagen", "comfyui", "elevenlabs", "video",
                   "pexels", "pixabay", "freesound", "whisper_local"}
    assert reg.get("anthropic").complete and reg.get("video").kind is ProviderKind.VIDEO and reg.get("elevenlabs").kind is ProviderKind.TTS
    assert not reg.get("ollama").needs_key and reg.get("comfyui").local


def test_settings_fallback_order_and_models_are_respected(vault):
    s = FakeSettings(**{"fallback_order": {"llm": ["openai_compat", "anthropic"]}, "providers.openai_compat.model": "gpt-4o"})
    gw, rec, db, reg = make(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}), vault, s)
    gw.llm("x")
    assert gw.last_provider == "openai_compat" and rec.body()["model"] == "gpt-4o"


def test_ping_still_works_through_the_new_providers(vault):
    gw, rec, db, reg = make(lambda r: httpx.Response(200, json={"data": []}), vault)
    res = reg.test_connection("anthropic", vault)
    assert res.ok and rec.requests[0].url.path == "/v1/models" and rec.requests[0].headers["x-api-key"] == "sk-ant-SECRET-1234"
    assert reg.test_connection("openai_image", vault).ok                                    # anahtar openai_compat ile paylaşılır
