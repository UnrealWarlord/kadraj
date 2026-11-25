"""Bulut görsel sağlayıcıları: FLUX (Black Forest Labs), Google Imagen, OpenAI görsel (DALL-E / gpt-image).

Ortak yüz: ``generate(prompt, *, vault, aspect, seed, n, negative, width, height, model, timeout) -> ImageResult``.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ...core.contracts import ProviderKind
from .. import pricing
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
    """FLUX: ``POST /v1/<model>`` → ``polling_url`` yoklanır → ``result.sample`` indirilir."""
    MODEL = "flux-pro-1.1"

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("bfl_flux", "FLUX (Black Forest Labs)", ProviderKind.IMAGE, base_url="https://api.bfl.ai",
                         ping_path="/v1/credits", auth="header:x-key", settings=settings, transport=transport, **kw)

    def generate(self, prompt: str, *, vault: Any = None, aspect: str = "1:1", seed: Optional[int] = None, n: int = 1,
                 negative: str = "", width: Optional[int] = None, height: Optional[int] = None, model: Optional[str] = None,
                 timeout: float = 60.0, max_wait: float = 180.0, **_: Any) -> ImageResult:
        secret = self.secret(vault)
        mdl = model or self.model(self.MODEL)
        w, h = (width, height) if width and height else size_for(aspect)
        images: List[bytes] = []
        for i in range(max(1, int(n))):
            body: Dict[str, Any] = {"prompt": prompt, "width": int(w), "height": int(h), "output_format": "png",
                                    "safety_tolerance": 2}
            if seed is not None:
                body["seed"] = int(seed) + i
            sub = self.request_json("POST", f"/v1/{mdl}", secret=secret, timeout=timeout, json_body=body)
            poll_url = sub.get("polling_url") or (f"{self.endpoint()}/v1/get_result?id={sub['id']}" if sub.get("id") else "")
            if not poll_url:
                raise ai_err("bad_response", source=self.id, name=self.display_name)

            def check(url: str = poll_url) -> Tuple[bool, Any]:
                data = self.request_json("GET", url, secret=secret, timeout=timeout)
                status = str(data.get("status", ""))
                if status == "Ready":
                    return True, (data.get("result") or {}).get("sample")
                if status in ("Request Moderated", "Content Moderated"):
                    raise ai_err("moderated", source=self.id, name=self.display_name)
                if status in ("Error", "Failed", "Task not found"):
                    raise ai_err("bad_response", source=self.id, name=self.display_name, technical=status)
                return False, None
            sample = self.poll(check, interval=1.0, max_wait=max_wait)
            if not sample:
                raise ai_err("bad_response", source=self.id, name=self.display_name)
            images.append(self.download(str(sample)))
        return ImageResult(images, Usage(self.id, mdl, 0, 0, float(len(images)), pricing.image_cost(mdl, len(images))),
                           self.id, mdl, seed)


class GoogleImagenProvider(AIProvider):
    """Imagen: ``POST /v1beta/models/<model>:predict`` → ``predictions[].bytesBase64Encoded``."""
    MODEL = "imagen-4.0-generate-001"
    ASPECTS = ("1:1", "3:4", "4:3", "9:16", "16:9")

    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("google_imagen", "Google Imagen", ProviderKind.IMAGE, base_url="https://generativelanguage.googleapis.com",
                         ping_path="/v1beta/models", auth="header:x-goog-api-key", settings=settings, transport=transport, **kw)

    def generate(self, prompt: str, *, vault: Any = None, aspect: str = "1:1", n: int = 1, model: Optional[str] = None,
                 timeout: float = 90.0, **_: Any) -> ImageResult:
        secret = self.secret(vault)
        mdl = model or self.model(self.MODEL)
        a, b = parse_aspect(aspect)
        ratio = min(self.ASPECTS, key=lambda s: abs((parse_aspect(s)[0] / parse_aspect(s)[1]) - a / b))
        body = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": max(1, min(4, int(n))), "aspectRatio": ratio}}
        data = self.request_json("POST", f"/v1beta/models/{mdl}:predict", secret=secret, timeout=timeout, json_body=body)
        preds = data.get("predictions") if isinstance(data, Mapping) else None
        images = [base64.b64decode(p["bytesBase64Encoded"]) for p in (preds or []) if isinstance(p, Mapping) and p.get("bytesBase64Encoded")]
        if not images:
            raise ai_err("moderated" if isinstance(data, Mapping) and not preds else "bad_response", source=self.id, name=self.display_name)
        return ImageResult(images, Usage(self.id, mdl, 0, 0, float(len(images)), pricing.image_cost(mdl, len(images))), self.id, mdl)


class OpenAIImageProvider(AIProvider):
    """OpenAI uyumlu görsel (``/images/generations``, ``/images/edits``). Anahtar ``openai_compat`` ile paylaşılır."""
    MODEL = "gpt-image-1"
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
                 timeout: float = 120.0, **_: Any) -> ImageResult:
        secret = self.secret(vault)
        mdl = model or self.model(self.MODEL)
        body: Dict[str, Any] = {"model": mdl, "prompt": prompt, "n": max(1, min(4, int(n))), "size": self._size(aspect)}
        if mdl.startswith("dall-e"):
            body["response_format"] = "b64_json"
        images = self._images(self.request_json("POST", "/images/generations", secret=secret, timeout=timeout, json_body=body))
        return ImageResult(images, Usage(self.id, mdl, 0, 0, float(len(images)), pricing.image_cost(mdl, len(images))), self.id, mdl)

    def edit(self, image: bytes, mask: bytes, prompt: str, *, vault: Any = None, model: Optional[str] = None,
             timeout: float = 120.0, **_: Any) -> ImageResult:
        """``mask``: PNG; **saydam** alanlar düzenlenir (OpenAI kuralı)."""
        secret = self.secret(vault)
        mdl = model or self.model(self.MODEL)
        files = {"image": ("image.png", image, "image/png"), "mask": ("mask.png", mask, "image/png")}
        data = self.request_json("POST", "/images/edits", secret=secret, timeout=timeout, files=files,
                                 data={"model": mdl, "prompt": prompt, "n": "1"})
        images = self._images(data)
        return ImageResult(images, Usage(self.id, mdl, 0, 0, 1.0, pricing.image_cost(mdl, 1)), self.id, mdl)
