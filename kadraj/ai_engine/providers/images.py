"""Bulut görsel sağlayıcıları: FLUX (Black Forest Labs), Google Gemini görsel ("Nano Banana"), OpenAI görsel (gpt-image).

Ortak yüz: ``generate(prompt, *, vault, aspect, seed, n, negative, width, height, model, timeout) -> ImageResult``.

Resmî dokümanlara göre (2026-11): Google **Imagen kapatıldı** (``:predict`` artık yok) → Gemini görsel modelleri ``generateContent`` ile;
OpenAI ``gpt-image-1`` (23 Eki 2026) ve ``dall-e-*`` (12 May 2026) kapatıldı → ``gpt-image-2.x``. Eski bir model kapanırsa
``AIProvider.candidates/walk`` sıradaki adayı dener; kullanıcı Ayarlar'dan model seçtiyse yalnız o denenir.
"""
from __future__ import annotations

import base64
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ...core.contracts import ProviderKind
from .. import pricing
from ...core import i18n
from .aicore import AIProvider, ImageResult, Usage, ai_err, b64


def parse_aspect(aspect: str, default: Tuple[int, int] = (1, 1)) -> Tuple[int, int]:
    try:
        a, b = str(aspect).replace("x", ":").split(":")
        return max(1, int(float(a))), max(1, int(float(b)))
    except (ValueError, TypeError):
        return default


def size_for(aspect: str, long_side: int = 1024, multiple: int = 32, lo: int = 256, hi: int = 1440) -> Tuple[int, int]:
    """``aspect`` ("16:9") için ``multiple``'ın katı genişlik/yükseklik (uzun kenar ≈ ``long_side``)."""
    a, b = parse_aspect(aspect)
    if a >= b:
        w, h = long_side, long_side * b / a
    else:
        h, w = long_side, long_side * a / b
    snap = lambda v: int(min(hi, max(lo, round(v / multiple) * multiple)))
    return snap(w), snap(h)


class BflFluxProvider(AIProvider):
    """FLUX: ``POST /v1/<model>`` → ``polling_url`` yoklanır → ``result.sample`` (10 dk geçerli imzalı adres) indirilir.

    Kaynak: https://docs.bfl.ml/quick_start/generating_images.md · https://docs.bfl.ml/api_integration/integration_guidelines.md
    · https://docs.bfl.ml/api_integration/errors.md · https://docs.bfl.ml/api-reference/get-the-users-credits.md
    """
    MODEL = "flux-pro-1.1"
    MODELS = ("flux-pro-1.1", "flux-2-pro")
    PENDING = ("Pending", "Reasoning", "Generating", "Queued", "Processing")
    MODERATED = ("Request Moderated", "Content Moderated")
    FAILED = ("Error", "Failed", "Task not found", "No Card Generated")

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("bfl_flux", "FLUX (Black Forest Labs)", ProviderKind.IMAGE, base_url="https://api.bfl.ai",
                         ping_path="/v1/credits", auth="header:x-key", settings=settings, transport=transport, **kw)

    def ping_message(self, resp: Any, ms: float) -> str:
        try:
            credits = resp.json().get("credits")
        except Exception:
            credits = None
        if credits is not None:
            return i18n.t("ai.test.ok_credits", name=self.display_name, credits=f"{float(credits):g}", ms=f"{ms:.0f}")
        return super().ping_message(resp, ms)

    def generate(self, prompt: str, *, vault: Any = None, aspect: str = "1:1", seed: Optional[int] = None, n: int = 1,
                 negative: str = "", width: Optional[int] = None, height: Optional[int] = None, model: Optional[str] = None,
                 timeout: float = 60.0, max_wait: float = 180.0, **_: Any) -> ImageResult:
        secret = self.secret(vault)
        w, h = (width, height) if width and height else size_for(aspect)

        def run(mdl: str) -> List[bytes]:
            images: List[bytes] = []
            for i in range(max(1, int(n))):
                body: Dict[str, Any] = {"prompt": prompt, "width": int(w), "height": int(h), "output_format": "png",
                                        "safety_tolerance": 2}
                if seed is not None:
                    body["seed"] = int(seed) + i
                sub = self.request_json("POST", f"/v1/{mdl}", secret=secret, timeout=timeout, json_body=body, model=mdl)
                poll_url = sub.get("polling_url") or (f"{self.endpoint()}/v1/get_result?id={sub['id']}" if sub.get("id") else "")
                if not poll_url:
                    raise ai_err("bad_response", source=self.id, name=self.display_name)

                def check(url: str = poll_url) -> Tuple[bool, Any]:
                    data = self.request_json("GET", url, secret=secret, timeout=timeout)
                    status = str(data.get("status", ""))
                    if status == "Ready":
                        return True, (data.get("result") or {}).get("sample")
                    if status in self.MODERATED:
                        raise ai_err("moderated", source=self.id, name=self.display_name)
                    if status in self.FAILED:
                        raise ai_err("bad_response", source=self.id, name=self.display_name, technical=status)
                    return False, None                                  # Pending / Reasoning / Generating / bilinmeyen: beklemeye devam
                sample = self.poll(check, interval=1.0, max_wait=max_wait)
                if not sample:
                    raise ai_err("bad_response", source=self.id, name=self.display_name)
                images.append(self.download(str(sample)))
            return images

        used: List[str] = []
        images = self.walk("image", self.candidates("image", self.MODELS, model), lambda m: (used.append(m), run(m))[1])
        mdl = used[-1]
        return ImageResult(images, Usage(self.id, mdl, 0, 0, float(len(images)), pricing.image_cost(mdl, len(images))), self.id, mdl, seed)


class GoogleImagenProvider(AIProvider):
    """Google görsel. **Imagen kapatıldı**; Gemini görsel modelleri ("Nano Banana") ``generateContent`` ile çalışır:
    ``POST /v1/models/<model>:generateContent`` → ``candidates[0].content.parts[].inlineData`` (base64).

    Kimlik ``google_imagen`` aynı kaldı (kasadaki anahtar ve ayarlar bozulmasın). Kaynak:
    https://ai.google.dev/gemini-api/docs/image-generation (REST sekmesi, "generateContent") · https://ai.google.dev/gemini-api/docs/imagen
    ("Imagen models are shut down").
    """
    MODEL = "gemini-nano-banana-2.1"
    MODELS = ("gemini-nano-banana-2.1", "gemini-3.1-flash-image", "gemini-2.5-flash-image")
    ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")     # tüm Gemini görsel modellerinde ortak
    BLOCKED = ("SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION",
               "IMAGE_RECITATION", "OTHER", "IMAGE_OTHER", "NO_IMAGE")

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("google_imagen", "Google Gemini görsel", ProviderKind.IMAGE, base_url="https://generativelanguage.googleapis.com",
                         ping_path="/v1beta/models", auth="header:x-goog-api-key", settings=settings, transport=transport,
                         ping_params={"pageSize": "50"}, **kw)

    def _ratio(self, aspect: str) -> str:
        a, b = parse_aspect(aspect)
        return min(self.ASPECTS, key=lambda s: abs(math.log((parse_aspect(s)[0] / parse_aspect(s)[1]) / (a / b))))

    @staticmethod
    def _parts(data: Any) -> List[Mapping[str, Any]]:
        out: List[Mapping[str, Any]] = []
        for cand in (data.get("candidates") if isinstance(data, Mapping) else None) or []:
            content = cand.get("content") if isinstance(cand, Mapping) else None
            out += [p for p in ((content or {}).get("parts") or []) if isinstance(p, Mapping)]
        return out

    def generate(self, prompt: str, *, vault: Any = None, aspect: str = "1:1", n: int = 1, model: Optional[str] = None,
                 timeout: float = 120.0, **_: Any) -> ImageResult:
        secret = self.secret(vault)
        ratio = self._ratio(aspect)
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": ratio}}}

        def one(mdl: str) -> Tuple[bytes, str]:
            data = self.request_json("POST", f"/v1/models/{mdl}:generateContent", secret=secret, timeout=timeout, json_body=body, model=mdl)
            for part in self._parts(data):
                blob = part.get("inlineData") or part.get("inline_data")
                if isinstance(blob, Mapping) and blob.get("data"):
                    return base64.b64decode(blob["data"]), str(blob.get("mimeType") or blob.get("mime_type") or "image/png")
            reasons = [str(c.get("finishReason", "")) for c in (data.get("candidates") or []) if isinstance(c, Mapping)] if isinstance(data, Mapping) else []
            block = str((data.get("promptFeedback") or {}).get("blockReason", "")) if isinstance(data, Mapping) else ""
            if block or any(r in self.BLOCKED for r in reasons):
                raise ai_err("moderated", source=self.id, name=self.display_name, technical=",".join([block, *reasons]))
            raise ai_err("bad_response", source=self.id, name=self.display_name, technical=",".join(reasons) or "görsel yok")

        used: List[str] = []
        cands = self.candidates("image", self.MODELS, model)
        images: List[bytes] = []
        mime = "image/png"
        for _i in range(max(1, min(4, int(n)))):
            img, mime = self.walk("image", cands, lambda m: (used.append(m), one(m))[1])
            images.append(img)
            cands = [used[-1]]
        mdl = used[-1]
        return ImageResult(images, Usage(self.id, mdl, 0, 0, float(len(images)), pricing.image_cost(mdl, len(images))), self.id, mdl, mime=mime)


GoogleImageProvider = GoogleImagenProvider


class OpenAIImageProvider(AIProvider):
    """OpenAI görsel (``/images/generations``, ``/images/edits``). Anahtar ``openai_compat`` ile paylaşılır.

    gpt-image modelleri her zaman ``b64_json`` döner (``response_format`` gönderilmez). ``gpt-image-1``/``dall-e-*`` kapatıldı.
    Kaynak: https://developers.openai.com/api/reference/resources/images · https://developers.openai.com/api/docs/deprecations
    (düzenleme formu: ``image[]`` + ``mask`` + ``prompt``; maske ``alpha`` kanalı ister, **saydam** alan düzenlenir).
    """
    MODEL = "gpt-image-2.6-flare"
    MODELS = ("gpt-image-2.6-flare", "gpt-image-2.5-flare", "gpt-image-2.6-sunburst", "gpt-image-2.5-sunburst", "gpt-image-2")
    EDIT_MODELS = ("gpt-image-2.6-sunburst", "gpt-image-2.5-sunburst", "gpt-image-2.6-flare", "gpt-image-2.5-flare", "gpt-image-2")
    SIZES = ((1024, 1024), (1536, 1024), (1024, 1536))

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("openai_image", "OpenAI görsel", ProviderKind.IMAGE, key_id="openai_compat",
                         base_url="https://api.openai.com/v1", ping_path="/models", auth="bearer", settings=settings,
                         transport=transport, **kw)

    def endpoint(self) -> str:
        if self.settings is not None:
            for k in ("providers.openai_image.base_url", "providers.openai_compat.base_url"):
                try:
                    v = self.settings.get(k)
                except Exception:
                    v = None
                if v:
                    return str(v).rstrip("/")
        return self.base_url

    def _size(self, aspect: str) -> str:
        a, b = parse_aspect(aspect)
        r = a / b
        w, h = min(self.SIZES, key=lambda s: abs(s[0] / s[1] - r))
        return f"{w}x{h}"

    def _images(self, data: Any) -> List[bytes]:
        out: List[bytes] = []
        for item in (data.get("data") if isinstance(data, Mapping) else None) or []:
            if isinstance(item, Mapping) and item.get("b64_json"):
                out.append(base64.b64decode(item["b64_json"]))
            elif isinstance(item, Mapping) and item.get("url"):
                out.append(self.download(str(item["url"])))
        if not out:
            raise ai_err("bad_response", source=self.id, name=self.display_name)
        return out

    def generate(self, prompt: str, *, vault: Any = None, aspect: str = "1:1", n: int = 1, model: Optional[str] = None,
                 timeout: float = 180.0, **_: Any) -> ImageResult:
        secret = self.secret(vault)
        used: List[str] = []

        def one(mdl: str) -> List[bytes]:
            used.append(mdl)
            body: Dict[str, Any] = {"model": mdl, "prompt": prompt, "n": max(1, min(4, int(n))), "size": self._size(aspect)}
            if mdl.startswith("dall-e"):
                body["response_format"] = "b64_json"
            return self._images(self.request_json("POST", "/images/generations", secret=secret, timeout=timeout, json_body=body, model=mdl))
        images = self.walk("image", self.candidates("image", self.MODELS, model), one)
        mdl = used[-1]
        return ImageResult(images, Usage(self.id, mdl, 0, 0, float(len(images)), pricing.image_cost(mdl, len(images))), self.id, mdl)

    def edit(self, image: bytes, mask: bytes, prompt: str, *, vault: Any = None, model: Optional[str] = None,
             timeout: float = 180.0, **_: Any) -> ImageResult:
        """``mask``: PNG (alfa kanallı); **saydam** alanlar düzenlenir (OpenAI kuralı)."""
        secret = self.secret(vault)
        used: List[str] = []

        def one(mdl: str) -> List[bytes]:
            used.append(mdl)
            files = {"image[]": ("image.png", image, "image/png"), "mask": ("mask.png", mask, "image/png")}
            return self._images(self.request_json("POST", "/images/edits", secret=secret, timeout=timeout, files=files,
                                                  data={"model": mdl, "prompt": prompt, "n": "1"}, model=mdl))
        images = self.walk("edit", self.candidates("edit", self.EDIT_MODELS, model), one)
        mdl = used[-1]
        return ImageResult(images, Usage(self.id, mdl, 0, 0, 1.0, pricing.image_cost(mdl, 1)), self.id, mdl)
