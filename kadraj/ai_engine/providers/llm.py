"""Dil modeli sağlayıcıları: Anthropic (Claude), OpenAI uyumlu, Ollama (yerel). Ortak yüz: ``complete(...) -> LLMResult``.

Anthropic notu: ``claude-*-5*`` modellerinde ``temperature/top_p/top_k`` gönderilmez (API 400 verir); ``thinking`` verilmez (uyarlamalı);
asistan ön dolgusu yok; JSON için ``output_config.format``; redde karşı ``fallbacks: "default"`` + ``server-side-fallback-2026-07-01``
başlığı (Haiku 5.5 hariç — onda yok). Model kimlikleri: varsayılan ``claude-opus-5-5``, ucuz işler ``claude-haiku-5-5`` (Ayarlar'dan
değiştirilebilir; model kapanırsa sıradaki aday denenir). HTTP katmanı projenin ortak ``httpx`` katmanıdır (SDK kurulu değil; tek tip
zaman aşımı/sahte taşıma/anahtar gizleme için).

Kaynaklar (2026-11): https://platform.claude.com/docs/en/api/messages · .../api/errors · .../api/models/list ·
.../build-with-claude/refusals-and-fallback · .../models/opus-5-5/migration-guide (örnekleme parametresi yok, ``anthropic-version: 2026-10-21``) ·
https://developers.openai.com/api/reference/resources/chat (``max_tokens`` kaldırıldı → ``max_completion_tokens``) ·
https://developers.openai.com/api/docs/models (gpt-6-luna ucuz model) · https://docs.ollama.com/api/chat (+ /api/tags, /api/errors).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlparse

from ...core import i18n
from ...core.contracts import PingResult, ProviderKind, SmartError
from .. import aierrors, pricing
from .aicore import (AIProvider, LLMResult, Usage, ai_err, b64, sniff_mime)
from .base import redact

ANTHROPIC_DEFAULT = "claude-opus-5-5"
ANTHROPIC_CHEAP = "claude-haiku-5-5"
ANTHROPIC_VERSION = "2026-10-21"
_ANTHROPIC_CHAIN = (ANTHROPIC_DEFAULT, "claude-sonnet-5-5", ANTHROPIC_CHEAP)
_ANTHROPIC_CHEAP_CHAIN = (ANTHROPIC_CHEAP, "claude-sonnet-5-5")
_NO_SAMPLING = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable", "claude-mythos", "claude-opus-4-8",
                "claude-opus-4-7")
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable", "claude-mythos")
OPENAI_DEFAULT_CHAIN = ("gpt-6.1-terra", "gpt-6-luna", "gpt-4o-mini")
OPENAI_CHEAP_CHAIN = ("gpt-6-luna", "gpt-4o-mini")
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

    def quick_test(self, vault: Optional[Any] = None, timeout: float = 25.0) -> PingResult:
        """Liste çağrısı + gerçek ama minik bir istek (≈ birkaç token): anahtar doğru ama **bakiye yok** durumunu da yakalar."""
        res = self.test_connection(vault, min(timeout, 10.0))
        if not res.ok:
            return res
        started = time.perf_counter()
        try:
            self.complete([{"role": "user", "content": "Reply with the single word: OK"}], vault=vault, tier="cheap", max_tokens=16,
                          timeout=timeout)
        except SmartError as err:
            return PingResult(False, None, err.summary, err)
        ms = (time.perf_counter() - started) * 1000.0
        return PingResult(True, round(ms, 1), i18n.t("ai.test.ok_call", name=self.display_name, ms=f"{ms:.0f}"))


class AnthropicProvider(LLMProvider):
    vision = True

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("anthropic", "Anthropic (Claude)", ProviderKind.LLM, base_url="https://api.anthropic.com",
                         ping_path="/v1/models", auth="header:x-api-key", ping_params={"limit": "100"},
                         extra_headers={"anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
                         settings=settings, transport=transport, **kw)
        version = self.setting("api_version")
        if version:
            self.extra_headers["anthropic-version"] = str(version)

    def model_for(self, tier: str, model: Optional[str]) -> str:
        if model:
            return model
        return str(self.setting("cheap_model", ANTHROPIC_CHEAP)) if tier == "cheap" else self.model(ANTHROPIC_DEFAULT)

    def _complete(self, msgs, system, secret, tier, model, max_tokens, json_schema, want_json, images, temperature, timeout) -> LLMResult:
        if images:
            last = msgs[-1]
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": sniff_mime(im), "data": b64(im)}} for im in images]
            blocks.append({"type": "text", "text": str(last["content"])})
            msgs[-1] = {"role": "user", "content": blocks}
        cheap = tier == "cheap"
        tokens = int(max_tokens or (4096 if cheap else 16000))
        if cheap:
            tokens = max(tokens, 16 if max_tokens and max_tokens <= 16 else 2048)    # uyarlamalı düşünme için pay (test çağrısı hariç)
        if cheap:
            cands = self.candidates("cheap", _ANTHROPIC_CHEAP_CHAIN, model, setting="cheap_model")
        else:
            cands = self.candidates("default", _ANTHROPIC_CHAIN, model)
        use_fallbacks = bool(self.setting("fallbacks", True))
        state = {"fallbacks": use_fallbacks}

        def one(mdl: str) -> LLMResult:
            body: Dict[str, Any] = {"model": mdl, "max_tokens": tokens, "messages": msgs}
            if system:
                body["system"] = system
            if json_schema:
                body["output_config"] = {"format": {"type": "json_schema", "schema": dict(json_schema)}}
            # API 2026-10-21 ve sonrası: temperature/top_p/top_k HER modelde 400 (docs: api/migration-2026-10-21).
            # Yalnız Ayarlar'dan eski sürüme (ör. 2023-06-01) dönülmüşse ve model örneklemeyi kabul ediyorsa gönderilir.
            legacy_api = str(self.extra_headers.get("anthropic-version", "")) < "2026-10-21"
            if temperature is not None and legacy_api and not mdl.startswith(_NO_SAMPLING):
                body["temperature"] = float(temperature)
            headers: Dict[str, str] = {}
            if state["fallbacks"] and mdl.startswith(_FALLBACK_MODELS):
                body["fallbacks"] = "default"                       # sunucu tarafı ret yedeği (beta başlığıyla)
                headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            try:
                data = self.request_json("POST", "/v1/messages", secret=secret, timeout=timeout, json_body=body, headers=headers, model=mdl)
            except SmartError as err:
                if getattr(err, "status", 0) == 400 and "fallback" in (err.technical or "").lower() and "fallbacks" in body:
                    state["fallbacks"] = False                      # hesap/bölge beta'yı kabul etmiyorsa yedeksiz yeniden dene
                    return one(mdl)
                raise
            if not isinstance(data, Mapping) or not isinstance(data.get("content"), list):
                raise ai_err("bad_response", source=self.id, name=self.display_name)
            if data.get("stop_reason") == "refusal":
                raise ai_err("refused", source=self.id, name=self.display_name,
                             technical=str((data.get("stop_details") or {}).get("category") or ""))
            text = "".join(str(b.get("text", "")) for b in data["content"] if isinstance(b, Mapping) and b.get("type") == "text")
            u = data.get("usage") or {}
            ti, to = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
            used = str(data.get("model") or mdl)                    # sunucu yedeği başka model kullandıysa onu yaz
            return LLMResult(text, Usage(self.id, used, ti, to, 0.0, pricing.llm_cost(used, ti, to)), used, self.id,
                             str(data.get("stop_reason") or ""), data.get("stop_reason") == "max_tokens")
        return self.walk("cheap" if cheap else "default", cands, one)


class OpenAICompatProvider(LLMProvider):
    """OpenAI uyumlu ``/chat/completions`` (OpenAI, OpenRouter, LM Studio, vLLM…): adres Ayarlar'dan değiştirilebilir.

    OpenAI'de ``max_completion_tokens`` kullanılır (``max_tokens`` kaldırıldı); başka sunucularda ``max_tokens``. Sunucu birini
    reddederse (ya da ``temperature``'ı) otomatik olarak diğeriyle bir kez daha denenir.
    """
    vision = True

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("openai_compat", "OpenAI uyumlu", ProviderKind.LLM, base_url="https://api.openai.com/v1",
                         ping_path="/models", auth="bearer", settings=settings, transport=transport, **kw)

    @property
    def is_openai(self) -> bool:
        return (urlparse(self.endpoint()).hostname or "") == "api.openai.com"

    def _complete(self, msgs, system, secret, tier, model, max_tokens, json_schema, want_json, images, temperature, timeout) -> LLMResult:
        cheap = tier == "cheap"
        if self.is_openai:
            cands = self.candidates("cheap" if cheap else "default", OPENAI_CHEAP_CHAIN if cheap else OPENAI_DEFAULT_CHAIN, model,
                                    setting="cheap_model" if cheap else "model")
        else:                                       # başka sunucu: model adını kullanıcı bilir; yoksa OpenAI'ninkiyle denenir
            cands = [model or str(self.setting("cheap_model" if cheap else "model") or self.setting("model") or "gpt-4o-mini")]
        needs_json = bool(json_schema or want_json)
        sys_text = system
        if needs_json and "json" not in (system + " ".join(str(m.get("content", "")) for m in msgs)).lower():
            sys_text = (system + "\n\n" if system else "") + "Reply with valid JSON only."
        out: List[Dict[str, Any]] = []
        if sys_text:
            out.append({"role": "system", "content": sys_text})
        out += msgs
        if images:
            parts: List[Dict[str, Any]] = [{"type": "text", "text": str(out[-1]["content"])}]
            parts += [{"type": "image_url", "image_url": {"url": f"data:{sniff_mime(im)};base64,{b64(im)}"}} for im in images]
            out[-1] = {"role": "user", "content": parts}
        flags = {"tok": "max_completion_tokens" if self.is_openai else "max_tokens", "temp": temperature is not None}

        def one(mdl: str) -> LLMResult:
            data: Any = None
            for _attempt in range(3):
                body: Dict[str, Any] = {"model": mdl, "messages": out, flags["tok"]: int(max_tokens or 4096)}
                if needs_json:
                    body["response_format"] = {"type": "json_object"}
                if flags["temp"] and temperature is not None:
                    body["temperature"] = float(temperature)
                try:
                    data = self.request_json("POST", "/chat/completions", secret=secret, timeout=timeout, json_body=body, model=mdl)
                    break
                except SmartError as err:
                    low = (err.technical or "").lower()
                    if getattr(err, "status", 0) != 400:
                        raise
                    if flags["tok"] == "max_tokens" and "max_completion_tokens" in low:
                        flags["tok"] = "max_completion_tokens"
                    elif flags["tok"] == "max_completion_tokens" and "max_completion_tokens" in low:
                        flags["tok"] = "max_tokens"
                    elif flags["temp"] and "temperature" in low:
                        flags["temp"] = False
                    else:
                        raise
            else:
                raise ai_err("bad_response", source=self.id, name=self.display_name, technical="parametre reddedildi")
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
        return self.walk("cheap" if cheap else "default", cands, one)


_PREFERRED = ("gemma4", "gemma3", "qwen3", "qwen2.5", "llama3.3", "llama3.2", "llama3.1", "mistral", "phi")
_VISION_HINTS = ("llava", "vision", "-vl", "vl:", "minicpm-v", "moondream", "gemma3", "gemma4", "bakllava")
_NOT_CHAT = ("embed", "nomic", "bge", "minilm", "rerank")


class OllamaProvider(LLMProvider):
    """Yerel Ollama: ``/api/chat``; görsel için ``images`` (ör. llava). Çalışmıyorsa ``ai.local_down`` kartı; model kurulu değilse
    ``ai.local_model_missing`` (``ollama pull …`` adımıyla). Model seçilmediyse **kurulu olanlardan** biri seçilir."""
    vision = True

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        import os
        super().__init__("ollama", "Ollama (yerel)", ProviderKind.LLM, needs_key=False, local=True,
                         base_url=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"), ping_path="/api/tags", auth="none",
                         settings=settings, transport=transport, **kw)
        self._installed: List[str] = []
        self._installed_at = 0.0

    def list_models(self, timeout: float = 4.0) -> List[str]:
        data = self.request_json("GET", "/api/tags", timeout=timeout)
        return [str(m.get("name") or m.get("model")) for m in (data.get("models") or []) if isinstance(m, Mapping)]

    def installed(self, timeout: float = 4.0, fresh: bool = False) -> List[str]:
        if not fresh and self._installed and time.monotonic() - self._installed_at < 20.0:
            return self._installed
        self._installed = self.list_models(timeout)
        self._installed_at = time.monotonic()
        return self._installed

    def ping_result(self, resp: Any, ms: float) -> PingResult:
        try:
            names = [str(m.get("name") or m.get("model")) for m in (resp.json().get("models") or []) if isinstance(m, Mapping)]
        except Exception:
            names = []
        self._installed, self._installed_at = names, time.monotonic()
        if not names:
            err = aierrors.error_card("local_model_missing", self.id, name=self.display_name, model=aierrors.OLLAMA_SUGGESTED)
            return PingResult(False, round(ms, 1), i18n.t("ai.test.local_no_models", name=self.display_name), err)
        shown = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
        return PingResult(True, round(ms, 1), i18n.t("ai.test.local_models", name=self.display_name, models=shown))

    def quick_test(self, vault: Optional[Any] = None, timeout: float = 10.0) -> PingResult:
        return self.test_connection(vault, timeout)             # yerel modeli yüklemek dakikalar sürebilir: liste yeter

    def resolve_model(self, explicit: Optional[str], vision: bool) -> str:
        if explicit:
            return explicit
        chosen = self.setting("vision_model") if vision else self.setting("model")
        if chosen:
            return str(chosen)
        names = [n for n in self.installed() if not any(w in n.lower() for w in _NOT_CHAT)]
        if not names:
            raise aierrors.error_card("local_model_missing", self.id, name=self.display_name, model=aierrors.OLLAMA_SUGGESTED)
        if vision:
            hit = next((n for n in names if any(h in n.lower() for h in _VISION_HINTS)), None)
            if hit:
                return hit
        for pref in _PREFERRED:
            hit = next((n for n in names if n.lower().startswith(pref)), None)
            if hit:
                return hit
        return names[0]

    def _complete(self, msgs, system, secret, tier, model, max_tokens, json_schema, want_json, images, temperature, timeout) -> LLMResult:
        mdl = self.resolve_model(model, bool(images))
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
        data = self.request_json("POST", "/api/chat", timeout=max(timeout, 180.0), json_body=body, model=mdl)
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError):
            raise ai_err("bad_response", source=self.id, name=self.display_name, technical=redact(str(data)[:200])) from None
        ti, to = int(data.get("prompt_eval_count", 0) or 0), int(data.get("eval_count", 0) or 0)
        return LLMResult(str(text), Usage(self.id, mdl, ti, to, 0.0, 0.0), mdl, self.id, str(data.get("done_reason") or ""),
                         data.get("done_reason") == "length")
