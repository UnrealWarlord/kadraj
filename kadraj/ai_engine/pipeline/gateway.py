"""``AIGateway`` — görev odaklı tek giriş noktası: ``llm / vision / image / tts / sfx / video``.

Sağlayıcı zincirini **Adım 3 kayıt defteri** yürütür (``ProviderRegistry.call_with_fallback``: bulut → yerel, 429'da geri çekilme,
çevrimdışında bulutları atlama); gateway yalnızca (1) tercih/süzgeç verir, (2) başarılı çağrının ``Usage``'ını ``api_usage``'a yazar,
(3) zincir boşken sade "… gerekli" kartı atar. Sağlayıcıya özgü istem biçimi ``prompt_adapter`` ile yapılır (sağlayıcı → model).
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from ...core.contracts import ProviderKind, SmartError
from ..prompt_adapter import AdaptedPrompt, PromptSpec, adapt
from ..providers.aicore import (AudioResult, ImageResult, LLMResult, Usage, VideoResult, ai_err)

#: görsel sağlayıcı kimliği → istem adaptörü modeli
PROVIDER_PROMPT_MODEL = {"bfl_flux": "flux", "google_imagen": "imagen", "openai_image": "dalle", "comfyui": "sdxl"}
#: istem modeli → sağlayıcı (Midjourney'in API'si yok → ``None``: istem kopyalanır)
MODEL_PROVIDER = {"flux": "bfl_flux", "imagen": "google_imagen", "dalle": "openai_image", "sdxl": "comfyui", "midjourney": None}


class AIGateway:
    def __init__(self, registry: Any, vault: Any = None, settings: Any = None,
                 db: Optional[Callable[[], Any]] = None) -> None:
        self.registry, self.vault, self.settings = registry, vault, settings
        self._db = db or (lambda: None)
        self.last_provider = ""
        self.local_up: dict = {}                  # yerel servis → ulaşılabilir mi (bilinmiyorsa anahtar yok = dene)
        self.session_cost = 0.0
        self.session_tokens = 0

    # -- kayıt -------------------------------------------------------------------------------------
    def record(self, usage: Usage) -> None:
        self.session_cost += usage.cost_usd
        self.session_tokens += usage.tokens_in + usage.tokens_out
        db = self._db()
        if db is None:
            return
        try:
            db.add_usage(usage.provider, model=usage.model, tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
                         units=usage.units, cost_usd=usage.cost_usd)
        except Exception:                          # noqa: BLE001 — kayıt hatası işi bozmaz
            pass

    # -- zincir ---------------------------------------------------------------------------------------
    def chain(self, kind: ProviderKind, *, prefer: Optional[str] = None, accept: Optional[Callable[[Any], bool]] = None):
        def ok(p: Any) -> bool:
            if p.local and self.local_up.get(p.id) is False:           # bilinen kapalı yerel servisi deneme
                return False
            return accept(p) if accept is not None else True
        return self.registry.chain(kind, self.vault, prefer=prefer, accept=ok)

    def available(self, kind: ProviderKind, accept: Optional[Callable[[Any], bool]] = None) -> bool:
        return bool(self.chain(kind, accept=accept))

    def has_llm(self) -> bool:
        return self.available(ProviderKind.LLM)

    def has_vision(self) -> bool:
        return self.available(ProviderKind.LLM, lambda p: getattr(p, "vision", False))

    def has_image(self) -> bool:
        return self.available(ProviderKind.IMAGE, lambda p: p.id in PROVIDER_PROMPT_MODEL)

    def has_video(self) -> bool:
        return self.available(ProviderKind.VIDEO)

    def has_tts(self) -> bool:
        return self.available(ProviderKind.TTS)

    def _call(self, kind: ProviderKind, op: Callable[[Any], Any], empty_code: str, *, prefer: Optional[str] = None,
              accept: Optional[Callable[[Any], bool]] = None) -> Any:
        if not self.chain(kind, prefer=prefer, accept=accept):
            raise ai_err(empty_code)
        res = self.registry.call_with_fallback(kind, op, vault=self.vault, prefer=prefer,
                                               accept=lambda p: not (p.local and self.local_up.get(p.id) is False) and (accept(p) if accept else True))
        if not res.ok:
            err = res.error
            if err is None:
                err = ai_err(empty_code)
            raise err
        self.last_provider = res.provider_id
        usage = getattr(res.value, "usage", None)
        if isinstance(usage, Usage):
            self.record(usage)
        return res.value

    # -- görevler ----------------------------------------------------------------------------------------
    def llm(self, messages: Sequence[Mapping[str, Any]] | str, *, system: str = "", tier: str = "default", want_json: bool = False,
            json_schema: Optional[Mapping[str, Any]] = None, images: Sequence[bytes] = (), temperature: Optional[float] = None,
            max_tokens: Optional[int] = None, prefer: Optional[str] = None, model: Optional[str] = None) -> LLMResult:
        msgs = [{"role": "user", "content": messages}] if isinstance(messages, str) else list(messages)
        accept = (lambda p: getattr(p, "vision", False)) if images else None

        def op(p: Any) -> LLMResult:
            return p.complete(msgs, system=system, vault=self.vault, tier=tier, want_json=want_json, json_schema=json_schema,
                              images=images, temperature=temperature, max_tokens=max_tokens, model=model)
        return self._call(ProviderKind.LLM, op, "no_vision" if images else "no_llm", prefer=prefer, accept=accept)

    def vision(self, image: bytes, prompt: str, **kw: Any) -> LLMResult:
        return self.llm(prompt, images=[image], **kw)

    def image(self, spec: "PromptSpec | str", *, prefer_model: Optional[str] = None, n: int = 1, seed: Optional[int] = None,
              extra: Optional[Mapping[str, Any]] = None) -> Tuple[ImageResult, AdaptedPrompt]:
        """Önce ``prefer_model``'in sağlayıcısı, olmazsa zincirdeki diğerleri; her sağlayıcı için istem **o modele uyarlanır**."""
        prefer = MODEL_PROVIDER.get(prefer_model or "")
        used: dict = {}

        def op(p: Any) -> ImageResult:
            model = PROVIDER_PROMPT_MODEL[p.id]
            if isinstance(spec, PromptSpec):
                ad = adapt(model, spec)
                text, negative, params = ad.text, ad.negative, dict(ad.params)
                aspect = spec.normalized().aspect or "1:1"
            else:
                ad = AdaptedPrompt(model, str(spec), "", {})
                text, negative, params, aspect = str(spec), "", {}, (extra or {}).get("aspect", "1:1")
            used["adapted"] = ad
            kw = {"aspect": aspect, "negative": negative, "seed": seed, "n": n, **{k: v for k, v in (extra or {}).items() if k != "aspect"}}
            for key in ("steps", "cfg"):
                if key in params and key not in kw:
                    kw[key] = params[key]
            return p.generate(text, vault=self.vault, **kw)
        res = self._call(ProviderKind.IMAGE, op, "no_image", prefer=prefer, accept=lambda p: p.id in PROVIDER_PROMPT_MODEL)
        return res, used["adapted"]

    def tts(self, text: str, *, voice_id: Optional[str] = None, language: Optional[str] = None) -> AudioResult:
        return self._call(ProviderKind.TTS, lambda p: p.synthesize(text, vault=self.vault, voice_id=voice_id, language=language), "no_tts")

    def sfx(self, description: str, seconds: float = 3.0) -> AudioResult:
        return self._call(ProviderKind.TTS, lambda p: p.sound_effect(description, vault=self.vault, seconds=seconds), "no_tts",
                          accept=lambda p: hasattr(p, "sound_effect"))

    def video(self, prompt: str, *, image: Optional[bytes] = None, seconds: float = 5.0, aspect: str = "16:9",
              seed: Optional[int] = None) -> VideoResult:
        return self._call(ProviderKind.VIDEO, lambda p: p.generate(prompt, vault=self.vault, image=image, seconds=seconds,
                                                                   aspect=aspect, seed=seed), "no_video")

    def edit_image(self, image: bytes, mask: bytes, prompt: str, *, negative: str = "") -> ImageResult:
        """Fırçalı inpainting: ComfyUI → OpenAI görsel zinciri."""
        def op(p: Any) -> ImageResult:
            if p.id == "comfyui":
                return p.inpaint(image, mask, prompt, negative=negative)
            return p.edit(image, _alpha_mask(mask), prompt, vault=self.vault)
        return self._call(ProviderKind.IMAGE, op, "edit_unavailable", accept=lambda p: p.id in ("comfyui", "openai_image"))

    def upscale(self, image: bytes, factor: int = 2) -> Optional[ImageResult]:
        """ComfyUI varsa ``ImageResult``; yoksa ``None`` (çağıran yerel Lanczos'a düşer)."""
        comfy = self.registry.get("comfyui")
        if comfy is None or not self.chain(ProviderKind.IMAGE, accept=lambda p: p.id == "comfyui"):
            return None
        try:
            res = comfy.upscale(image, factor)
        except SmartError:
            return None
        self.record(res.usage)
        return res

    def img2img(self, image: bytes, prompt: str, *, strength: float = 0.6) -> Optional[ImageResult]:
        comfy = self.registry.get("comfyui")
        if comfy is None or not self.chain(ProviderKind.IMAGE, accept=lambda p: p.id == "comfyui"):
            return None
        try:
            res = comfy.img2img(image, prompt, strength=strength)
        except SmartError:
            return None
        self.record(res.usage)
        return res


def _alpha_mask(mask_png: bytes) -> bytes:
    """Beyaz = düzenle (bizim maske) → OpenAI: **saydam** = düzenle. Siyah/saydam alanlar korunur (opak)."""
    import io
    from PIL import Image
    m = Image.open(io.BytesIO(mask_png)).convert("L")
    rgba = Image.new("RGBA", m.size, (0, 0, 0, 255))
    rgba.putalpha(m.point(lambda v: 0 if v > 127 else 255))
    out = io.BytesIO()
    rgba.save(out, format="PNG")
    return out.getvalue()
