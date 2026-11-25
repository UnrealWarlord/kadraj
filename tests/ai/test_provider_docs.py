"""Sağlayıcı istekleri **resmî dokümandaki** biçimlere göre (2026-11) — hepsi sahte HTTP; gerçek anahtar/ağ yok.

Her testin başındaki adres, biçimin alındığı resmî sayfadır. Gerçek sunucuyla doğrulanmadı (bkz. rapor): yalnız dokümana uyum denenir.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from kadraj.ai_engine import aierrors
from kadraj.ai_engine.providers.comfyui import ComfyUIProvider
from kadraj.ai_engine.providers.images import BflFluxProvider, GoogleImagenProvider, OpenAIImageProvider
from kadraj.ai_engine.providers.llm import AnthropicProvider, OllamaProvider, OpenAICompatProvider
from kadraj.core.contracts import SmartError

from .conftest import FakeSettings, FakeVault, Recorder, png_bytes

NOSLEEP = lambda s: None
MSG = [{"role": "user", "content": "x"}]
SECRETS = ("sk-ant-SECRET-1234", "sk-oa-SECRET-5678", "bfl-SECRET", "g-SECRET")


def fail(provider, status, body, headers=None, **kw):
    """Sağlayıcıya ``status``+``body`` döndüren sahte sunucuyla bir çağrı yapar, ``SmartError``'ı döner."""
    rec = Recorder(lambda r: httpx.Response(status, json=body, headers=headers or {}))
    provider.transport = rec.transport
    with pytest.raises(SmartError) as ei:
        if hasattr(provider, "complete"):
            provider.complete(MSG, **kw)
        else:
            provider.generate("x", **kw)
    return ei.value


def check_card(err, code, *, steps=3):
    assert err.code == code, (err.code, err.summary)
    assert len(err.steps) >= steps and err.cause and err.title and err.summary            # neden + numaralı adımlar
    assert all(s for s in err.steps) and err.actions
    blob = " ".join([err.title, err.summary, err.cause, err.technical, *err.steps])
    assert not any(sec in blob for sec in SECRETS)                                          # anahtar asla görünmez


# ===================================================================== hata eşlemesi (altı ayrı durum + izin/sunucu)
def test_anthropic_error_bodies_map_to_distinct_cards(vault):
    # https://platform.claude.com/docs/en/api/errors — {"type":"error","error":{"type":...,"message":...}}; 402 billing_error, 529 overloaded_error
    p = AnthropicProvider()
    def body(t, m="x"):
        return {"type": "error", "error": {"type": t, "message": m}, "request_id": "req_1"}
    check_card(fail(p, 401, body("authentication_error", "invalid x-api-key sk-ant-SECRET-1234"), vault=vault), "ai.auth", steps=4)      # yanlış anahtar
    check_card(fail(p, 402, body("billing_error", "Check your payment details"), vault=vault), "ai.quota", steps=4)                      # bakiye yok
    check_card(fail(p, 400, body("invalid_request_error", "Your credit balance is too low to access the API"), vault=vault), "ai.quota", steps=4)
    check_card(fail(p, 429, {**body("rate_limit_error"), "error": {"type": "rate_limit_error", "message": "slow", "details": {"error_code": "enforced_spend_limit_reached"}}}, vault=vault), "ai.quota", steps=4)
    rl = fail(p, 429, body("rate_limit_error", "rate"), headers={"retry-after": "30"}, vault=vault)
    check_card(rl, "ai.rate_limit")
    assert rl.retryable and rl.retry_after == 30.0                                                                                        # hız sınırı
    check_card(fail(p, 404, body("not_found_error", "model: claude-opus-9"), vault=vault, model="claude-opus-9"), "ai.model_not_found")  # model yok
    check_card(fail(p, 529, body("overloaded_error"), vault=vault), "ai.server")
    check_card(fail(p, 403, body("permission_error"), vault=vault), "ai.permission")


def test_openai_error_bodies_map_to_distinct_cards(vault):
    # https://developers.openai.com/api/docs/guides/error-codes — 401 Incorrect API key; 429 credit_balance_exhausted / insufficient_quota;
    # 429 Rate limit reached (Retry-After); 403 unsupported country
    p = OpenAICompatProvider()
    def body(code, msg="x", typ="invalid_request_error"):
        return {"error": {"message": msg, "type": typ, "param": None, "code": code}}
    check_card(fail(p, 401, body("invalid_api_key", "Incorrect API key provided: sk-oa-SECRET-5678"), vault=vault), "ai.auth", steps=4)
    check_card(fail(p, 429, body("credit_balance_exhausted", "prepaid credits depleted"), vault=vault), "ai.quota", steps=4)
    check_card(fail(p, 429, body("insufficient_quota", "You exceeded your current quota", "insufficient_quota"), vault=vault), "ai.quota", steps=4)
    check_card(fail(p, 429, body("rate_limit_exceeded", "Rate limit reached for requests"), headers={"retry-after": "2"}, vault=vault), "ai.rate_limit")
    check_card(fail(p, 404, body("model_not_found", "The model `gpt-9` does not exist"), vault=vault, model="gpt-9"), "ai.model_not_found")
    check_card(fail(p, 403, body("unsupported_country_region_territory", "Country, region, or territory not supported"), vault=vault), "ai.permission")
    check_card(fail(p, 503, body("server_is_overloaded", "overloaded", "service_unavailable_error"), vault=vault), "ai.server")


def test_google_and_bfl_error_bodies_map_to_distinct_cards(vault):
    # https://aistudio.google.com/docs/troubleshooting + /docs/api-errors: {"error":{"code":"authentication"|"payment_required"|...,"message":...}}
    g = GoogleImagenProvider()
    def gb(code, msg="x"):
        return {"error": {"code": code, "message": msg}}
    check_card(fail(g, 401, gb("authentication", "API key expired"), vault=vault), "ai.auth", steps=4)
    check_card(fail(g, 400, gb("invalid_request", "API key not valid. Please pass a valid API key."), vault=vault), "ai.auth", steps=4)   # eski biçim
    check_card(fail(g, 402, gb("payment_required", "prepay credit depleted"), vault=vault), "ai.quota", steps=4)
    check_card(fail(g, 429, gb("rate_limit_exceeded", "slow down"), headers={"retry-after": "5"}, vault=vault), "ai.rate_limit")
    check_card(fail(g, 404, gb("model_not_found", "models/x is not found"), vault=vault, model="x"), "ai.model_not_found")
    check_card(fail(g, 403, gb("permission_denied", "FREE_TIER_VERIFICATION_REQUIRED"), vault=vault), "ai.permission")
    # https://docs.bfl.ml/api_integration/errors.md — 402 yetersiz kredi, 403 izin, 429 çok fazla aktif görev, 422 {"detail":[...]}
    b = BflFluxProvider()
    check_card(fail(b, 402, {"detail": "Insufficient credits"}, vault=vault), "ai.quota", steps=4)
    check_card(fail(b, 403, {"detail": "Forbidden"}, vault=vault), "ai.permission")
    check_card(fail(b, 429, {"detail": "Too many active tasks"}, vault=vault), "ai.rate_limit")
    unp = fail(b, 422, {"detail": [{"loc": ["body", "width"], "msg": "multiple of 32", "type": "value_error"}]}, vault=vault)
    check_card(unp, "ai.http")
    assert "422" in unp.summary and any(a.kind == "copy" for a in unp.actions)                                                         # ayrıntıyı kopyala


def test_network_and_local_service_cases_are_separate_cards(vault):
    # internet yok (bulut) ≠ yerel servis kapalı ≠ yerel model yok
    def boom(request):
        raise httpx.ConnectError("no route")
    off = fail(AnthropicProvider(), 0, {}, vault=vault) if False else None
    p = AnthropicProvider(transport=httpx.MockTransport(boom))
    with pytest.raises(SmartError) as ei:
        p.complete(MSG, vault=vault)
    check_card(ei.value, "ai.offline", steps=4)
    assert any(a.kind == "retry" for a in ei.value.actions)
    for local in (OllamaProvider(transport=httpx.MockTransport(boom)), ComfyUIProvider(transport=httpx.MockTransport(boom))):
        with pytest.raises(SmartError) as ei:
            local.complete(MSG) if hasattr(local, "complete") else local.generate("x")
        check_card(ei.value, "ai.local_down")
        assert any(a.kind == "open_url" and a.payload["url"].startswith("https://") for a in ei.value.actions)                          # indirme sayfası
    # https://docs.ollama.com/api/errors — 404 {"error": "model 'x' not found"}
    nf = Recorder(lambda r: httpx.Response(200, json={"models": [{"name": "yok:1b"}]}) if r.url.path == "/api/tags" else httpx.Response(404, json={"error": "model 'yok:1b' not found"}))
    with pytest.raises(SmartError) as ei:
        OllamaProvider(transport=nf.transport, settings=FakeSettings(**{"providers.ollama.model": "yok:1b"})).complete(MSG)
    check_card(ei.value, "ai.local_model_missing")
    assert "ollama pull" in " ".join(ei.value.steps) and any(a.kind == "copy" for a in ei.value.actions)
    # yavaş yanıt (internet var) "internet yok" demez
    def slow(request):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(SmartError) as ei:
        AnthropicProvider(transport=httpx.MockTransport(slow)).complete(MSG, vault=vault)
    check_card(ei.value, "ai.timeout")


def test_aierrors_classify_and_links_are_official():
    assert aierrors.classify(404, "", has_model=False) == "http" and aierrors.classify(404, "x", has_model=True) == "model_not_found"
    assert aierrors.key_id_of("openai_image") == "openai_compat" and aierrors.key_id_of("anthropic") == "anthropic"
    for pid in ("anthropic", "openai_compat", "bfl_flux", "google_imagen"):
        pi = aierrors.info(pid)
        assert pi.key_url.startswith("https://") and pi.billing_url.startswith("https://")
    assert aierrors.info("anthropic").key_url == "https://platform.claude.com/settings/keys"          # platform.claude.com/docs/en/get-api-key
    assert aierrors.info("openai_compat").key_url == "https://platform.openai.com/settings/organization/api-keys"
    assert aierrors.info("ollama").local_url == "https://ollama.com/download" and "comfy.org" in aierrors.info("comfyui").local_url


# ===================================================================== Anthropic
def anthropic_ok(text="Merhaba", model=None):
    d = {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}}
    if model:
        d["model"] = model
    return httpx.Response(200, json=d)


def test_anthropic_model_not_found_walks_to_next_model_and_remembers(vault):
    # https://platform.claude.com/docs/en/about-claude/models/overview (opus/sonnet/haiku 5.5); 404 not_found_error
    seen = []

    def route(r):
        mdl = json.loads(r.content)["model"]
        seen.append(mdl)
        if mdl == "claude-opus-5-5":
            return httpx.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": "model: claude-opus-5-5"}})
        return anthropic_ok()
    p = AnthropicProvider(transport=Recorder(route).transport)
    res = p.complete(MSG, vault=vault)
    assert seen == ["claude-opus-5-5", "claude-sonnet-5-5"] and res.model == "claude-sonnet-5-5"
    p.complete(MSG, vault=vault)
    assert seen[-1] == "claude-sonnet-5-5" and len(seen) == 3                       # çalışan model başa alındı
    only = AnthropicProvider(transport=Recorder(route).transport, settings=FakeSettings(**{"providers.anthropic.model": "claude-opus-5-5"}))
    with pytest.raises(SmartError) as ei:
        only.complete(MSG, vault=vault)
    assert ei.value.code == "ai.model_not_found" and "claude-opus-5-5" in ei.value.summary    # kullanıcı seçtiyse sessizce değiştirilmez


def test_anthropic_fallback_beta_header_is_retried_without_when_rejected(vault):
    # https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback — fallbacks:"default" + anthropic-beta: server-side-fallback-2026-07-01
    calls = []

    def route(r):
        body = json.loads(r.content)
        calls.append(("fallbacks" in body, r.headers.get("anthropic-beta")))
        if "fallbacks" in body:
            return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "fallbacks: not enabled for this organization"}})
        return anthropic_ok()
    res = AnthropicProvider(transport=Recorder(route).transport).complete(MSG, vault=vault)
    assert calls == [(True, "server-side-fallback-2026-07-01"), (False, None)] and res.text == "Merhaba"
    calls.clear()
    AnthropicProvider(transport=Recorder(route).transport).complete(MSG, vault=vault, tier="cheap")      # Haiku 5.5'te sunucu yedeği yok
    assert calls == [(False, None)]


def test_anthropic_cheap_tier_keeps_room_for_adaptive_thinking(vault):
    rec = Recorder(lambda r: anthropic_ok())
    p = AnthropicProvider(transport=rec.transport)
    p.complete(MSG, vault=vault, tier="cheap", max_tokens=200)
    assert rec.body()["model"] == "claude-haiku-5-5" and rec.body()["max_tokens"] == 2048
    p.complete(MSG, vault=vault, tier="cheap", max_tokens=16)                           # bağlantı denemesi minik kalır
    assert rec.body()["max_tokens"] == 16


# ===================================================================== OpenAI uyumlu
def oa_ok(text="tamam"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}})


def test_openai_uses_max_completion_tokens_and_current_models(vault):
    # https://developers.openai.com/api/reference/resources/chat — max_tokens kullanımdan kalktı; model kataloğu: developers.openai.com/api/docs/models
    rec = Recorder(lambda r: oa_ok())
    p = OpenAICompatProvider(transport=rec.transport)
    p.complete(MSG, vault=vault, max_tokens=300)
    b = rec.body()
    assert rec.requests[0].url.path == "/v1/chat/completions" and b["max_completion_tokens"] == 300 and "max_tokens" not in b
    assert b["model"] == "gpt-6.1-terra"
    p.complete(MSG, vault=vault, tier="cheap")
    assert rec.body()["model"] == "gpt-6-luna" and rec.requests[-1].headers["authorization"] == "Bearer sk-oa-SECRET-5678"


def test_openai_adapts_to_rejected_parameters_and_missing_models(vault):
    seen = []

    def route(r):
        b = json.loads(r.content)
        seen.append(sorted(k for k in b if k in ("max_tokens", "max_completion_tokens", "temperature")) + [b["model"]])
        if b["model"] == "gpt-6.1-terra":
            return httpx.Response(404, json={"error": {"message": "The model `gpt-6.1-terra` does not exist", "type": "invalid_request_error", "code": "model_not_found"}})
        if "temperature" in b:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature' does not support 0.2 with this model.", "code": "unsupported_value"}})
        return oa_ok()
    res = OpenAICompatProvider(transport=Recorder(route).transport).complete(MSG, vault=vault, temperature=0.2, max_tokens=100)
    assert res.model == "gpt-6-luna" and seen[-1] == ["max_completion_tokens", "gpt-6-luna"]                  # model → sıradaki; temperature → atıldı
    # uyumlu sunucu `max_completion_tokens`'ı tanımıyor → `max_tokens`'a döner
    def old_server(r):
        b = json.loads(r.content)
        if "max_completion_tokens" in b:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument supplied: max_completion_tokens"}})
        return oa_ok()
    rec = Recorder(old_server)
    OpenAICompatProvider(transport=rec.transport).complete(MSG, vault=vault, max_tokens=50)
    assert rec.body()["max_tokens"] == 50


# ===================================================================== görsel sağlayıcıları
def test_openai_image_uses_current_models_no_response_format_and_edit_form_fields(vault):
    # https://developers.openai.com/api/reference/resources/images — GPT image modelleri her zaman b64_json; response_format desteklenmez.
    # https://developers.openai.com/api/docs/deprecations — gpt-image-1 (23 Eki 2026) ve dall-e-* (12 May 2026) kapatıldı.
    img = png_bytes()
    seen = []

    def route(r):
        if r.url.path == "/v1/images/generations":
            b = json.loads(r.content)
            seen.append(b)
            if b["model"] == "gpt-image-2.6-flare":
                return httpx.Response(404, json={"error": {"message": "The model does not exist", "code": "model_not_found"}})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(img).decode()}]})
    rec = Recorder(route)
    p = OpenAIImageProvider(transport=rec.transport)
    res = p.generate("dağ", vault=vault, aspect="16:9")
    assert [b["model"] for b in seen] == ["gpt-image-2.6-flare", "gpt-image-2.5-flare"] and res.images == [img]
    assert all("response_format" not in b for b in seen) and seen[-1]["size"] == "1536x1024"
    p.edit(img, png_bytes(), "yağmur ekle", vault=vault)
    req = rec.requests[-1]
    assert req.url.path == "/v1/images/edits" and b'name="image[]"' in req.content and b'name="mask"' in req.content
    assert b'name="model"' in req.content and b"gpt-image-2.6-sunburst" in req.content
    seen.clear()
    OpenAIImageProvider(transport=rec.transport, settings=FakeSettings(**{"providers.openai_image.model": "dall-e-3"})).generate("x", vault=vault)
    assert seen[0]["model"] == "dall-e-3" and seen[0]["response_format"] == "b64_json"                  # yalnız eski model açıkça seçilirse


def test_flux_status_values_model_fallback_and_credits_message(vault):
    # https://docs.bfl.ml/api-reference/utility/get-result.md — Pending/Reasoning/Generating/Ready/Request Moderated/Content Moderated/Error/Task not found
    # https://docs.bfl.ml/api-reference/get-the-users-credits.md — GET /v1/credits → {"credits": number}
    state = {"polls": 0}
    posts = []

    def route(r):
        if r.method == "POST":
            posts.append(r.url.path)
            if r.url.path == "/v1/flux-pro-1.1":
                return httpx.Response(404, json={"detail": "Not Found"})
            return httpx.Response(200, json={"id": "i", "polling_url": "https://api.bfl.ai/v1/get_result?id=i", "cost": 4.0})
        if r.url.path == "/v1/credits":
            return httpx.Response(200, json={"credits": 12.5})
        if "get_result" in str(r.url):
            state["polls"] += 1
            status = ["Pending", "Reasoning", "Generating"][state["polls"] - 1] if state["polls"] <= 3 else "Ready"
            return httpx.Response(200, json={"status": status, "result": {"sample": "https://delivery-eu.bfl.ai/o.png"} if status == "Ready" else None})
        return httpx.Response(200, content=png_bytes())
    rec = Recorder(route)
    p = BflFluxProvider(transport=rec.transport, sleep=NOSLEEP)
    res = p.generate("kedi", vault=vault)
    assert posts == ["/v1/flux-pro-1.1", "/v1/flux-2-pro"] and res.model == "flux-2-pro" and state["polls"] == 4
    assert rec.requests[0].headers["x-key"] == "bfl-SECRET"
    ping = p.test_connection(vault)
    assert ping.ok and "12.5" in ping.message and rec.requests[-1].url.path == "/v1/credits"
    for status in ("Error", "Task not found"):
        bad = Recorder(lambda r, st=status: httpx.Response(200, json={"id": "z", "polling_url": "https://api.bfl.ai/x"}) if r.method == "POST" else httpx.Response(200, json={"status": st}))
        with pytest.raises(SmartError) as ei:
            BflFluxProvider(transport=bad.transport, sleep=NOSLEEP).generate("x", vault=vault)
        assert ei.value.code == "ai.bad_response"


def test_gemini_image_fallback_models_and_aspects(vault):
    # https://ai.google.dev/gemini-api/docs/image-generation — models: gemini-nano-banana-2.1, gemini-3.1-flash-image, gemini-2.5-flash-image
    img = png_bytes()
    seen = []

    def route(r):
        mdl = r.url.path.split("/")[-1].split(":")[0]
        seen.append(mdl)
        if mdl == "gemini-nano-banana-2.1":
            return httpx.Response(404, json={"error": {"code": "model_not_found", "message": "model not found"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}}]}}]})
    p = GoogleImagenProvider(transport=Recorder(route).transport)
    res = p.generate("x", vault=vault, aspect="16:9")
    assert seen == ["gemini-nano-banana-2.1", "gemini-3.1-flash-image"] and res.mime == "image/jpeg" and res.images == [img]     # snake_case de okunur
    assert p._ratio("16:9") == "16:9" and p._ratio("1:1") == "1:1" and p._ratio("7:3") == "21:9" and p._ratio("5:8") == "2:3"


# ===================================================================== ComfyUI
def test_comfyui_picks_installed_checkpoint_and_explains_missing_model(vault):
    # https://docs.comfy.org/development/comfyui-server/comms_routes — GET /object_info/CheckpointLoaderSimple, POST /prompt (400 doğrulama hatası)
    names = {"v": ["dreamshaper.safetensors", "sd_xl_base_1.0.safetensors"]}
    prompts = []

    def route(r):
        if r.url.path == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [names["v"]]}}}})
        if r.url.path == "/prompt":
            prompts.append(json.loads(r.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "P"})
        if r.url.path == "/history/P":
            return httpx.Response(200, json={"P": {"status": {"completed": True}, "outputs": {"7": {"images": [{"filename": "o.png"}]}}}})
        return httpx.Response(200, content=png_bytes())
    p = ComfyUIProvider(transport=Recorder(route).transport, sleep=NOSLEEP)
    p.generate("x", aspect="1:1")
    assert prompts[-1]["1"]["inputs"]["ckpt_name"] == "sd_xl_base_1.0.safetensors"                    # kurulu olanlardan XL tercih edilir
    assert (prompts[-1]["4"]["inputs"]["width"], prompts[-1]["4"]["inputs"]["height"]) == (1024, 1024)
    names["v"] = ["v1-5-pruned-emaonly-fp16.safetensors"]
    p.generate("x", aspect="1:1")
    assert prompts[-1]["1"]["inputs"]["ckpt_name"].startswith("v1-5") and prompts[-1]["4"]["inputs"]["width"] == 640   # SD1.5: küçük boyut
    names["v"] = []
    with pytest.raises(SmartError) as ei:
        p.generate("x")
    check_card(ei.value, "ai.comfy_no_model")
    ping = p.test_connection()
    assert not ping.ok and ping.error.code == "ai.comfy_no_model"
    # POST /prompt 400 → {"error":..., "node_errors": {"1": {"errors":[{"message": "Value not in list", "details": "ckpt_name: 'x'"}]}}}
    bad = Recorder(lambda r: httpx.Response(400, json={"error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                                                       "node_errors": {"1": {"errors": [{"message": "Value not in list", "details": "ckpt_name: 'yok.safetensors'"}]}}})
                   if r.url.path == "/prompt" else httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors"]]}}}}))
    with pytest.raises(SmartError) as ei:
        ComfyUIProvider(transport=bad.transport, sleep=NOSLEEP).generate("x")
    check_card(ei.value, "ai.comfy")
    assert "ckpt_name" in ei.value.summary


# ===================================================================== Ollama
def test_ollama_picks_installed_model_and_reports_empty_install():
    # https://docs.ollama.com/api/tags · /api/chat (stream:false; message.content; prompt_eval_count/eval_count)
    tags = {"v": [{"name": "nomic-embed-text:latest"}, {"name": "gemma4:e2b"}, {"name": "llava:7b"}]}

    def route(r):
        if r.url.path == "/api/tags":
            return httpx.Response(200, json={"models": tags["v"]})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "selam"}, "done": True, "done_reason": "stop", "prompt_eval_count": 3, "eval_count": 2})
    rec = Recorder(route)
    p = OllamaProvider(transport=rec.transport)
    res = p.complete(MSG)
    assert rec.body()["model"] == "gemma4:e2b" and res.model == "gemma4:e2b"                      # gömme modeli değil, sohbet modeli
    ok = p.test_connection()
    assert ok.ok and "gemma4:e2b" in ok.message
    tags["v"] = []
    p2 = OllamaProvider(transport=rec.transport)
    with pytest.raises(SmartError) as ei:
        p2.complete(MSG)
    check_card(ei.value, "ai.local_model_missing")
    empty = p2.test_connection()
    assert not empty.ok and empty.error.code == "ai.local_model_missing"


# ===================================================================== bağlantı testleri (Ayarlar + "Kaydet ve dene")
def test_connection_tests_are_cheap_lists_and_plain_language(vault):
    # Anthropic: https://platform.claude.com/docs/en/api/models/list (GET /v1/models?limit=…, data[]); OpenAI: GET /v1/models → {"object":"list","data":[…]}
    # Google: GET /v1beta/models (x-goog-api-key) → {"models":[…]}
    cases = [
        (AnthropicProvider, {"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-5-5"}], "has_more": False}, "/v1/models"),
        (OpenAICompatProvider, {"object": "list", "data": [{"id": "gpt-6-luna"}] * 3}, "/v1/models"),
        (OpenAIImageProvider, {"object": "list", "data": [{"id": "gpt-image-2.6-flare"}]}, "/v1/models"),
        (GoogleImagenProvider, {"models": [{"name": "models/gemini-nano-banana-2.1"}] * 4}, "/v1beta/models"),
    ]
    for cls, payload, path in cases:
        rec = Recorder(lambda r, pl=payload: httpx.Response(200, json=pl))
        p = cls(transport=rec.transport)
        res = p.test_connection(vault)
        assert res.ok and res.latency_ms is not None and "model görünüyor" in res.message, (cls.__name__, res.message)
        assert rec.requests[0].method == "GET" and rec.requests[0].url.path == path and len(rec.requests) == 1       # tek, ucuz GET
        assert not any(sec in res.message for sec in SECRETS)
    # anahtar yok / yanlış / bakiye yok / internet yok — her biri kendi kartıyla
    assert AnthropicProvider().test_connection(FakeVault()).error.code == "ai.no_key"
    wrong = AnthropicProvider(transport=Recorder(lambda r: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})).transport)
    res = wrong.test_connection(vault)
    assert not res.ok and res.error.code == "ai.auth" and len(res.error.steps) >= 4 and res.message == res.error.summary
    def boom(request):
        raise httpx.ConnectError("x")
    assert AnthropicProvider(transport=httpx.MockTransport(boom)).test_connection(vault).error.code == "ai.offline"


def test_llm_quick_test_makes_one_tiny_real_call_and_catches_no_credit(vault):
    # "Kaydet ve dene": liste çağrısı anahtarı doğrular; asıl bakiye sorunu minik bir istekte (max_tokens=16, ucuz model) ortaya çıkar
    calls = []

    def route(r):
        calls.append((r.method, r.url.path))
        if r.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "claude-haiku-5-5"}]})
        return anthropic_ok("OK")
    rec = Recorder(route)
    p = AnthropicProvider(transport=rec.transport)
    res = p.quick_test(vault)
    assert res.ok and calls == [("GET", "/v1/models"), ("POST", "/v1/messages")]
    assert rec.body()["model"] == "claude-haiku-5-5" and rec.body()["max_tokens"] == 16 and "bağlantı tamam" in res.message
    def nocredit(r):
        if r.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(402, json={"type": "error", "error": {"type": "billing_error", "message": "billing"}})
    res = AnthropicProvider(transport=Recorder(nocredit).transport).quick_test(vault)
    assert not res.ok and res.error.code == "ai.quota" and any(a.kind == "open_url" and "billing" in a.payload["url"] for a in res.error.actions)
    # yerel Ollama: model yüklemek dakikalar sürebilir → yalnız liste
    ol = Recorder(lambda r: httpx.Response(200, json={"models": [{"name": "gemma4:e2b"}]}))
    assert OllamaProvider(transport=ol.transport).quick_test().ok and all(r.method == "GET" for r in ol.requests)
