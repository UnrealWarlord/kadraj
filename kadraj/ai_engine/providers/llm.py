"""Dil modeli sağlayıcıları: Anthropic (Claude), OpenAI uyumlu, Ollama (yerel). Ortak yüz: ``complete(...) -> LLMResult``.

Anthropic notu: ``claude-*-5*`` modellerinde ``temperature/top_p/top_k`` gönderilmez (API 400 verir); ``thinking`` verilmez (uyarlamalı);
asistan ön dolgusu yok; JSON için ``output_config.format``; redde karşı ``fallbacks: "default"`` (sunucu tarafı). Model kimlikleri:
varsayılan ``claude-opus-5-5``, ucuz işler ``claude-haiku-5-5`` (Ayarlar'dan değiştirilebilir). HTTP katmanı projenin ortak ``httpx``
katmanıdır (SDK kurulu değil; tek tip zaman aşımı/sahte taşıma/anahtar gizleme için) — bkz. DEVİR "Karar".
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from ...core.contracts import ProviderKind
from .. import pricing
from .aicore import (AIProvider, LLMResult, Usage, ai_err, b64, sniff_mime)

ANTHROPIC_DEFAULT = "claude-opus-5-5"
ANTHROPIC_CHEAP = "claude-haiku-5-5"
_NO_SAMPLING = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable", "claude-mythos", "claude-opus-4-8",
                "claude-opus-4-7")
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable", "claude-mythos")
Message = Mapping[str, Any]


class LLMProvider(AIProvider):
    """``complete`` ortak sözleşmesi; alt sınıf ``_complete`` yazar."""
    vision = False

    def complete(self, messages: Sequence[Message], *, system: str = "", vault: Any = None, tier: str = "default",
                 model: Optional[str] = None, max_tokens: Optional[int] = None, json_schema: Optional[Mapping[str, Any]] = None,
                 want_json: bool = False, images: Sequence[bytes] = (), temperature: Optional[float] = None,
                 timeout: float = 120.0) -> LLMResult:
        secret = self.secret(vault)
        msgs = [dict(m) for m in messages]
        if not msgs or msgs[-1].get("role") != "user":
            raise ValueError("son ileti 'user' olmalı")
        return self._complete(msgs, system, secret, tier, model, max_tokens, json_schema, want_json, list(images), temperature, timeout)

    def _complete(self, msgs, system, secret, tier, model, max_tokens, json_schema, want_json, images, temperature, timeout) -> LLMResult:
        raise NotImplementedError


class AnthropicProvider(LLMProvider):
    vision = True

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("anthropic", "Anthropic (Claude)", ProviderKind.LLM, base_url="https://api.anthropic.com",
                         ping_path="/v1/models", auth="header:x-api-key",
                         extra_headers={"anthropic-version": "2023-06-01", "content-type": "application/json"},
                         settings=settings, transport=transport, **kw)

    def model_for(self, tier: str, model: Optional[str]) -> str:
        if model:
            return model
        return str(self.setting("cheap_model", ANTHROPIC_CHEAP)) if tier == "cheap" else self.model(ANTHROPIC_DEFAULT)

    def _complete(self, msgs, system, secret, tier, model, max_tokens, json_schema, want_json, images, temperature, timeout) -> LLMResult:
        mdl = self.model_for(tier, model)
        if images:
            last = msgs[-1]
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": sniff_mime(im), "data": b64(im)}} for im in images]
            blocks.append({"type": "text", "text": str(last["content"])})
            msgs[-1] = {"role": "user", "content": blocks}
        body: Dict[str, Any] = {"model": mdl, "max_tokens": int(max_tokens or (4096 if tier == "cheap" else 16000)),
                                "messages": msgs}
        if system:
            body["system"] = system
        if json_schema:
            body["output_config"] = {"format": {"type": "json_schema", "schema": dict(json_schema)}}
        if temperature is not None and not mdl.startswith(_NO_SAMPLING):
            body["temperature"] = float(temperature)
        headers: Dict[str, str] = {}
        if self.setting("fallbacks", True) and mdl.startswith(_FALLBACK_MODELS):
            body["fallbacks"] = "default"                       # sunucu tarafı ret yedeği (beta başlığıyla)
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        data = self.request_json("POST", "/v1/messages", secret=secret, timeout=timeout, json_body=body, headers=headers)
        if not isinstance(data, Mapping) or not isinstance(data.get("content"), list):
            raise ai_err("bad_response", source=self.id, name=self.display_name)
        if data.get("stop_reason") == "refusal":
            raise ai_err("refused", source=self.id, name=self.display_name,
                         technical=str((data.get("stop_details") or {}).get("category") or ""))
        text = "".join(str(b.get("text", "")) for b in data["content"] if isinstance(b, Mapping) and b.get("type") == "text")
        u = data.get("usage") or {}
        ti, to = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
        return LLMResult(text, Usage(self.id, mdl, ti, to, 0.0, pricing.llm_cost(mdl, ti, to)), mdl, self.id,
                         str(data.get("stop_reason") or ""), data.get("stop_reason") == "max_tokens")


class OpenAICompatProvider(LLMProvider):
    """OpenAI uyumlu ``/chat/completions`` (OpenAI, OpenRouter, LM Studio, vLLM…): adres Ayarlar'dan değiştirilebilir."""
    vision = True

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("openai_compat", "OpenAI uyumlu", ProviderKind.LLM, base_url="https://api.openai.com/v1",
                         ping_path="/models", auth="bearer", settings=settings, transport=transport, **kw)

    def _complete(self, msgs, system, secret, tier, model, max_tokens, json_schema, want_json, images, temperature, timeout) -> LLMResult:
        mdl = model or (str(self.setting("cheap_model", "gpt-4o-mini")) if tier == "cheap" else self.model("gpt-4o-mini"))
        out: List[Dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        out += msgs
        if images:
            parts: List[Dict[str, Any]] = [{"type": "text", "text": str(out[-1]["content"])}]
            parts += [{"type": "image_url", "image_url": {"url": f"data:{sniff_mime(im)};base64,{b64(im)}"}} for im in images]
            out[-1] = {"role": "user", "content": parts}
        body: Dict[str, Any] = {"model": mdl, "messages": out, "max_tokens": int(max_tokens or 4096)}
        if json_schema or want_json:
            body["response_format"] = {"type": "json_object"}
        if temperature is not None:
            body["temperature"] = float(temperature)
        data = self.request_json("POST", "/chat/completions", secret=secret, timeout=timeout, json_body=body)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ai_err("bad_response", source=self.id, name=self.display_name) from None
        u = data.get("usage") or {}
        ti, to = int(u.get("prompt_tokens", 0) or 0), int(u.get("completion_tokens", 0) or 0)
        local = bool(self.setting("local", False))
        return LLMResult(str(text), Usage(self.id, mdl, ti, to, 0.0, pricing.llm_cost(mdl, ti, to, local=local)), mdl, self.id,
                         str(choice.get("finish_reason") or ""), choice.get("finish_reason") == "length")


class OllamaProvider(LLMProvider):
    """Yerel Ollama: ``/api/chat``; görsel için ``images`` (ör. llava). Çalışmıyorsa ``provider.local_down`` kartı."""
    vision = True

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        import os
        super().__init__("ollama", "Ollama (yerel)", ProviderKind.LLM, needs_key=False, local=True,
                         base_url=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"), ping_path="/api/tags", auth="none",
                         settings=settings, transport=transport, **kw)

    def list_models(self, timeout: float = 4.0) -> List[str]:
        data = self.request_json("GET", "/api/tags", timeout=timeout)
        return [str(m.get("name") or m.get("model")) for m in (data.get("models") or []) if isinstance(m, Mapping)]

    def _complete(self, msgs, system, secret, tier, model, max_tokens, json_schema, want_json, images, temperature, timeout) -> LLMResult:
        mdl = model or (str(self.setting("vision_model", "llava")) if images else self.model("llama3.1"))
        out: List[Dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        out += msgs
        if images:
            out[-1] = {**out[-1], "images": [b64(im) for im in images]}
        opts: Dict[str, Any] = {"num_predict": int(max_tokens or 4096)}
        if temperature is not None:
            opts["temperature"] = float(temperature)
        body: Dict[str, Any] = {"model": mdl, "messages": out, "stream": False, "options": opts}
        if json_schema:
            body["format"] = dict(json_schema)
        elif want_json:
            body["format"] = "json"
        data = self.request_json("POST", "/api/chat", timeout=timeout, json_body=body)
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError):
            raise ai_err("bad_response", source=self.id, name=self.display_name) from None
        ti, to = int(data.get("prompt_eval_count", 0) or 0), int(data.get("eval_count", 0) or 0)
        return LLMResult(str(text), Usage(self.id, mdl, ti, to, 0.0, 0.0), mdl, self.id, str(data.get("done_reason") or ""),
                         data.get("done_reason") == "length")
