"""Sekiz sağlayıcı: istek biçimi, yanıt çözümleme, maliyet/token, hata kartları — hepsi sahte HTTP."""
from __future__ import annotations

import io
import wave

import httpx
import pytest

from kadraj.ai_engine.providers import comfy_workflows as wf
from kadraj.ai_engine.providers.comfyui import ComfyUIProvider
from kadraj.ai_engine.providers.elevenlabs import ElevenLabsProvider
from kadraj.ai_engine.providers.images import (BflFluxProvider, GoogleImagenProvider, OpenAIImageProvider, size_for)
from kadraj.ai_engine.providers.llm import AnthropicProvider, OllamaProvider, OpenAICompatProvider
from kadraj.ai_engine.providers.aicore import extract_json, pcm_to_wav, wav_seconds
from kadraj.ai_engine.providers.video import VideoProvider
from kadraj.core.contracts import SmartError

from .conftest import FakeSettings, FakeVault, Recorder, png_bytes

NOSLEEP = lambda s: None


def anthropic_ok(text="Merhaba", usage=(120, 30), stop="end_turn"):
    return httpx.Response(200, json={"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
                                     "stop_reason": stop, "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}})


# ================================================================================ Anthropic
def test_anthropic_request_shape_and_cost(vault):
    rec = Recorder(lambda r: anthropic_ok())
    p = AnthropicProvider(transport=rec.transport)
    res = p.complete([{"role": "user", "content": "Selam"}], system="Kısa yaz.", vault=vault, temperature=0.9)
    req, body = rec.requests[0], rec.body()
    assert req.url.path == "/v1/messages" and req.headers["x-api-key"] == "sk-ant-SECRET-1234"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000 and body["system"] == "Kısa yaz."
    assert "temperature" not in body and "thinking" not in body                          # 5.x'te örnekleme parametresi 400 verir
    assert body["fallbacks"] == "default" and req.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert res.text == "Merhaba" and (res.usage.tokens_in, res.usage.tokens_out) == (120, 30) and res.model == "claude-opus-5-5"
    assert res.usage.cost_usd == pytest.approx(120 / 1e6 * 4 + 30 / 1e6 * 20)            # Opus 5.5: 4/20 $ per MTok


def test_anthropic_tiers_models_settings_and_legacy_temperature(vault):
    rec = Recorder(lambda r: anthropic_ok())
    p = AnthropicProvider(transport=rec.transport, settings=FakeSettings(**{"providers.anthropic.model": "claude-sonnet-5-5"}))
    p.complete([{"role": "user", "content": "x"}], vault=vault)
    assert rec.body()["model"] == "claude-sonnet-5-5"
    p.complete([{"role": "user", "content": "x"}], vault=vault, tier="cheap")
    assert rec.body()["model"] == "claude-haiku-5-5" and rec.body()["max_tokens"] == 4096 and "fallbacks" not in rec.body()
    p.complete([{"role": "user", "content": "x"}], vault=vault, model="claude-sonnet-4-6", temperature=0.3)
    assert rec.body()["temperature"] == 0.3                                               # eski modelde serbest


def test_anthropic_json_schema_vision_and_refusal(vault):
    rec = Recorder(lambda r: anthropic_ok('{"a": 1}'))
    p = AnthropicProvider(transport=rec.transport)
    res = p.complete([{"role": "user", "content": "json ver"}], vault=vault, json_schema={"type": "object", "properties": {"a": {"type": "integer"}}})
    assert rec.body()["output_config"]["format"]["type"] == "json_schema" and res.json() == {"a": 1}
    p.complete([{"role": "user", "content": "bu ne?"}], vault=vault, images=[png_bytes()])
    blocks = rec.body()["messages"][-1]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png" and blocks[-1]["text"] == "bu ne?"
    rec.queue.append(httpx.Response(200, json={"content": [], "stop_reason": "refusal", "stop_details": {"category": "cyber"}, "usage": {}}))
    with pytest.raises(SmartError) as ei:
        p.complete([{"role": "user", "content": "x"}], vault=vault)
    assert ei.value.code == "ai.refused"


def test_anthropic_errors_never_leak_the_key(vault):
    p = AnthropicProvider(transport=Recorder(lambda r: httpx.Response(401, json={"error": {"message": "bad key sk-ant-SECRET-1234"}})).transport)
    with pytest.raises(SmartError) as ei:
        p.complete([{"role": "user", "content": "x"}], vault=vault)
    e = ei.value
    assert e.code == "provider.auth" and e.status == 401 and "SECRET" not in (e.technical + e.summary + repr(e.actions))
    with pytest.raises(SmartError) as ei:
        AnthropicProvider(transport=Recorder().transport).complete([{"role": "user", "content": "x"}], vault=FakeVault())
    assert ei.value.code == "provider.no_key"
    with pytest.raises(SmartError) as ei:
        AnthropicProvider(transport=Recorder(lambda r: httpx.Response(400, json={"error": {"message": "Your credit balance is too low"}})).transport).complete(
            [{"role": "user", "content": "x"}], vault=vault)
    assert ei.value.code == "ai.quota"
    rl = Recorder(lambda r: httpx.Response(429, headers={"retry-after": "7"}, json={}))
    with pytest.raises(SmartError) as ei:
        AnthropicProvider(transport=rl.transport).complete([{"role": "user", "content": "x"}], vault=vault)
    assert ei.value.status == 429 and ei.value.retry_after == 7.0 and ei.value.retryable


def test_network_failure_becomes_offline_card_with_fallback_marker(vault):
    def boom(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(SmartError) as ei:
        AnthropicProvider(transport=httpx.MockTransport(boom)).complete([{"role": "user", "content": "x"}], vault=vault)
    from kadraj.core.errors import is_network_error
    assert is_network_error(ei.value) and ei.value.code.startswith("net.")


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json('İşte liste: [1, 2, 3] bitti') == [1, 2, 3]
    with pytest.raises(SmartError) as ei:
        extract_json("json değil")
    assert ei.value.code == "ai.invalid_json"


# ================================================================================ OpenAI uyumlu + Ollama
def test_openai_compat_chat_vision_json_and_custom_base(vault):
    rec = Recorder(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": '{"x": 1}'}, "finish_reason": "stop"}],
                                                       "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}))
    p = OpenAICompatProvider(transport=rec.transport, settings=FakeSettings(**{"providers.openai_compat.base_url": "http://lm.local/v1"}))
    res = p.complete([{"role": "user", "content": "ver"}], system="sistem", vault=vault, want_json=True, temperature=0.2, images=[png_bytes()])
    req, body = rec.requests[0], rec.body()
    assert str(req.url) == "http://lm.local/v1/chat/completions" and req.headers["authorization"] == "Bearer sk-oa-SECRET-5678"
    assert body["messages"][0] == {"role": "system", "content": "sistem"} and body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2 and body["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert res.json() == {"x": 1} and res.usage.cost_usd == pytest.approx(1000 / 1e6 * 0.15 + 500 / 1e6 * 0.6)


def test_ollama_local_chat_vision_models_and_down_card():
    def route(r):
        if r.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}, {"name": "llava:7b"}]})
        return httpx.Response(200, json={"message": {"content": "tamam"}, "prompt_eval_count": 12, "eval_count": 5, "done_reason": "stop"})
    rec = Recorder(route)
    p = OllamaProvider(transport=rec.transport)
    assert p.list_models() == ["llama3.1:8b", "llava:7b"] and not p.needs_key and p.local
    res = p.complete([{"role": "user", "content": "selam"}], want_json=True, images=[png_bytes()], temperature=0.1)
    body = rec.body()
    assert body["model"] == "llava" and body["format"] == "json" and body["stream"] is False and body["messages"][-1]["images"]
    assert body["options"]["temperature"] == 0.1 and res.usage.cost_usd == 0 and res.usage.tokens_in == 12
    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(SmartError) as ei:
        OllamaProvider(transport=httpx.MockTransport(down)).complete([{"role": "user", "content": "x"}])
    assert ei.value.code == "provider.local_down"


# ================================================================================ Görsel
def test_flux_polls_until_ready_and_increments_seed(vault):
    state = {"polls": 0}

    def route(r):
        if r.method == "POST":
            return httpx.Response(200, json={"id": "abc", "polling_url": "https://api.bfl.ai/v1/get_result?id=abc"})
        if "get_result" in str(r.url):
            state["polls"] += 1
            if state["polls"] < 3:
                return httpx.Response(200, json={"status": "Pending"})
            return httpx.Response(200, json={"status": "Ready", "result": {"sample": "https://cdn.bfl.ai/out.png"}})
        return httpx.Response(200, content=png_bytes())
    rec = Recorder(route)
    p = BflFluxProvider(transport=rec.transport, sleep=NOSLEEP)
    res = p.generate("bir vapur", vault=vault, aspect="16:9", seed=10, n=2)
    posts = [r for r in rec.requests if r.method == "POST"]
    assert posts[0].url.path == "/v1/flux-pro-1.1" and posts[0].headers["x-key"] == "bfl-SECRET"
    import json
    b0, b1 = json.loads(posts[0].content), json.loads(posts[1].content)
    assert (b0["width"], b0["height"]) == (1024, 576) and b0["seed"] == 10 and b1["seed"] == 11
    assert len(res.images) == 2 and res.usage.cost_usd == pytest.approx(0.08) and res.usage.units == 2
    assert size_for("9:16") == (576, 1024) and size_for("1:1") == (1024, 1024) and all(v % 32 == 0 for v in size_for("4:3"))
    rec2 = Recorder(lambda r: httpx.Response(200, json={"id": "z", "polling_url": "https://api.bfl.ai/x"}) if r.method == "POST"
                    else httpx.Response(200, json={"status": "Content Moderated"}))
    with pytest.raises(SmartError) as ei:
        BflFluxProvider(transport=rec2.transport, sleep=NOSLEEP).generate("x", vault=vault)
    assert ei.value.code == "ai.moderated"
    forever = Recorder(lambda r: httpx.Response(200, json={"id": "z", "polling_url": "https://api.bfl.ai/x"}) if r.method == "POST"
                       else httpx.Response(200, json={"status": "Pending"}))
    with pytest.raises(SmartError) as ei:
        BflFluxProvider(transport=forever.transport, sleep=NOSLEEP).generate("x", vault=vault, max_wait=3)
    assert ei.value.code == "ai.timeout"


def test_imagen_aspect_mapping_and_base64(vault):
    import base64
    img = png_bytes()
    rec = Recorder(lambda r: httpx.Response(200, json={"predictions": [{"bytesBase64Encoded": base64.b64encode(img).decode()}] * 2}))
    p = GoogleImagenProvider(transport=rec.transport)
    res = p.generate("kedi", vault=vault, aspect="3:2", n=2)
    req, body = rec.requests[0], rec.body()
    assert req.url.path == "/v1beta/models/imagen-4.0-generate-001:predict" and req.headers["x-goog-api-key"] == "g-SECRET"
    assert body["parameters"] == {"sampleCount": 2, "aspectRatio": "4:3"} and res.images == [img, img]
    with pytest.raises(SmartError) as ei:
        GoogleImagenProvider(transport=Recorder(lambda r: httpx.Response(200, json={})).transport).generate("x", vault=vault)
    assert ei.value.code == "ai.moderated"


def test_openai_image_generate_and_edit_use_shared_key(vault):
    import base64
    img = png_bytes()
    rec = Recorder(lambda r: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(img).decode()}]}))
    p = OpenAIImageProvider(transport=rec.transport)
    assert p.is_configured(vault) and p.key_id == "openai_compat"
    res = p.generate("dağ", vault=vault, aspect="16:9")
    assert rec.requests[0].url.path == "/v1/images/generations" and rec.body()["size"] == "1536x1024"
    assert rec.requests[0].headers["authorization"] == "Bearer sk-oa-SECRET-5678" and res.images == [img]
    p.edit(img, png_bytes(), "yağmur ekle", vault=vault)
    req = rec.requests[-1]
    assert req.url.path == "/v1/images/edits" and req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="mask"' in req.content and b"yagmur" not in req.content


# ================================================================================ ComfyUI
def comfy_routes(seen):
    def route(r):
        seen.append((r.method, r.url.path))
        if r.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "up.png"})
        if r.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "P1"})
        if r.url.path == "/history/P1":
            return httpx.Response(200, json={"P1": {"status": {"completed": True, "status_str": "success"},
                                                   "outputs": {"9": {"images": [{"filename": "o.png", "subfolder": "", "type": "output"}]}}}})
        if r.url.path == "/view":
            return httpx.Response(200, content=png_bytes((16, 16)))
        return httpx.Response(404)
    return route


def test_comfyui_generate_upscale_inpaint_img2img():
    seen = []
    rec = Recorder(comfy_routes(seen))
    p = ComfyUIProvider(transport=rec.transport, sleep=NOSLEEP)
    res = p.generate("deniz", negative="bulanık", aspect="1:1", seed=5, n=1, steps=20, cfg=6.5)
    sub = [r for r in rec.requests if r.url.path == "/prompt"][0]
    import json
    graph = json.loads(sub.content)["prompt"]
    assert graph["5"]["inputs"]["seed"] == 5 and graph["5"]["inputs"]["steps"] == 20 and graph["2"]["inputs"]["text"] == "deniz"
    assert graph["3"]["inputs"]["text"] == "bulanık" and res.usage.cost_usd == 0 and len(res.images) == 1
    p.upscale(png_bytes(), 2)
    g = json.loads([r for r in rec.requests if r.url.path == "/prompt"][-1].content)["prompt"]
    assert g["2"]["inputs"]["model_name"] == "RealESRGAN_x4plus.pth" and g["4"]["inputs"]["scale_by"] == 0.5     # x4 model → ×2 için 0.5
    p.upscale(png_bytes(), 4)
    g = json.loads([r for r in rec.requests if r.url.path == "/prompt"][-1].content)["prompt"]
    assert "4" not in g                                                                                              # ×4: küçültme yok
    n_up = sum(1 for m, path in seen if path == "/upload/image")
    p.inpaint(png_bytes(), png_bytes(), "ağaç ekle", seed=1)
    assert sum(1 for m, path in seen if path == "/upload/image") == n_up + 2                                         # görsel + maske
    g = json.loads([r for r in rec.requests if r.url.path == "/prompt"][-1].content)["prompt"]
    assert g["6"]["class_type"] == "VAEEncodeForInpaint" and g["5"]["inputs"]["channel"] == "red"
    p.img2img(png_bytes(), "suluboya", strength=0.4)
    g = json.loads([r for r in rec.requests if r.url.path == "/prompt"][-1].content)["prompt"]
    assert g["6"]["inputs"]["denoise"] == 0.4
    assert wf.txt2img("a", "b", 64, 64, 1, 5, 5.0, "ck")["7"]["class_type"] == "SaveImage"


def test_comfyui_errors_down_and_workflow_failure():
    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(SmartError) as ei:
        ComfyUIProvider(transport=httpx.MockTransport(down)).generate("x")
    assert ei.value.code == "provider.local_down"
    def route(r):
        if r.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "E"})
        return httpx.Response(200, json={"E": {"status": {"status_str": "error", "messages": ["ckpt yok"]}}})
    with pytest.raises(SmartError) as ei:
        ComfyUIProvider(transport=Recorder(route).transport, sleep=NOSLEEP).generate("x")
    assert ei.value.code == "ai.comfy" and "ckpt yok" in ei.value.summary
    bad = Recorder(lambda r: httpx.Response(200, json={"error": {"message": "node eksik"}}))
    with pytest.raises(SmartError) as ei:
        ComfyUIProvider(transport=bad.transport, sleep=NOSLEEP).generate("x")
    assert ei.value.code == "ai.comfy"


# ================================================================================ Ses + Video
def test_elevenlabs_tts_is_always_wav_and_sfx(vault):
    pcm = b"\x00\x01" * 24000                                   # 1 sn @ 24 kHz mono
    rec = Recorder(lambda r: httpx.Response(200, content=pcm))
    p = ElevenLabsProvider(transport=rec.transport)
    res = p.synthesize("Merhaba dünya", vault=vault, voice_id="VOICE1", language="tr")
    req = rec.requests[0]
    assert req.url.path == "/v1/text-to-speech/VOICE1" and req.url.params["output_format"] == "pcm_24000"
    assert req.headers["xi-api-key"] == "xi-SECRET"
    with wave.open(io.BytesIO(res.wav), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 24000, 24000)
    assert res.seconds == pytest.approx(1.0) and res.usage.units == 13 and res.usage.cost_usd == pytest.approx(13 * 0.0003)
    sfx = p.sound_effect("kapı gıcırtısı", vault=vault, seconds=2.0)
    assert rec.requests[-1].url.path == "/v1/sound-generation" and rec.body()["duration_seconds"] == 2.0 and sfx.wav[:4] == b"RIFF"
    with pytest.raises(ValueError):
        p.synthesize("   ", vault=vault)
    assert wav_seconds(pcm_to_wav(pcm)) == pytest.approx(1.0)


def test_video_provider_configurable_img2vid_text2vid_and_polling(vault):
    st = {"n": 0}

    def route(r):
        if r.method == "POST":
            return httpx.Response(200, json={"task_id": "T9"})
        if r.url.path == "/gen/T9":
            st["n"] += 1
            return httpx.Response(200, json={"state": "running"} if st["n"] < 3 else {"state": "succeeded", "output": {"url": "https://cdn.v/x.mp4"}})
        return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42")
    rec = Recorder(route)
    s = FakeSettings(**{"providers.video.base_url": "https://vid.example", "providers.video.submit_path": "/gen",
                        "providers.video.status_path": "/gen/{id}", "providers.video.model": "v1", "providers.video.cost_per_second": 0.2,
                        "providers.video.auth": "header:X-Api-Key"})
    p = VideoProvider(settings=s, transport=rec.transport, sleep=NOSLEEP)
    assert p.is_configured(vault)
    res = p.generate("sahil", vault=vault, image=png_bytes(), seconds=4.0, seed=3)
    body = rec.body(0)
    assert rec.requests[0].headers["x-api-key"] == "vid-SECRET" and body["mode"] == "img2vid" and body["image"].startswith("data:image/png;base64,")
    assert body["duration"] == 4.0 and body["seed"] == 3 and res.data.startswith(b"\x00\x00\x00\x18ftyp")
    assert res.usage.cost_usd == pytest.approx(0.8) and st["n"] == 3
    posts_before = len([r for r in rec.requests if r.method == "POST"])
    p.generate("sahil", vault=vault)
    posts = [i for i, r in enumerate(rec.requests) if r.method == "POST"]
    assert len(posts) == posts_before + 1 and rec.body(posts[-1])["mode"] == "text2vid" and "image" not in rec.body(posts[-1])
    unconfigured = VideoProvider(settings=FakeSettings(), transport=rec.transport)
    assert not unconfigured.is_configured(vault)
    with pytest.raises(SmartError) as ei:
        unconfigured.generate("x", vault=vault)
    assert ei.value.code == "ai.no_video"
    direct = Recorder(lambda r: httpx.Response(200, json={"video_url": "https://cdn.v/d.mp4"}) if r.method == "POST" else httpx.Response(200, content=b"VID"))
    assert VideoProvider(settings=s, transport=direct.transport).generate("x", vault=vault).data == b"VID"
