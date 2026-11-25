"""Yerel ComfyUI: görsel üretme, ×2/×4 büyütme (upscale), fırçalı inpainting ve img2img (stil aktarımı) iş akışları.

Akış: görseli ``/upload/image``'a yükle → iş akışını ``/prompt``'a gönder → ``/history/<id>`` yokla → ``/view`` ile çıktıyı indir.
Çalışmıyorsa ``provider.local_down`` kartı (çökme yok). Model dosyaları ComfyUI tarafında kurulu olmalıdır.
"""
from __future__ import annotations

import random
import uuid
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ...core.contracts import ProviderKind
from . import comfy_workflows as wf
from .aicore import AIProvider, ImageResult, Usage, ai_err
from .images import size_for

CHECKPOINT = "sd_xl_base_1.0.safetensors"
UPSCALE_MODEL = "RealESRGAN_x4plus.pth"


class ComfyUIProvider(AIProvider):
    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("comfyui", "ComfyUI (yerel)", ProviderKind.IMAGE, needs_key=False, local=True,
                         base_url="http://127.0.0.1:8188", ping_path="/system_stats", auth="none", settings=settings,
                         transport=transport, **kw)

    # -- düşük düzey -----------------------------------------------------------------------------------
    def upload(self, name: str, data: bytes, timeout: float = 30.0) -> str:
        res = self.request_json("POST", "/upload/image", timeout=timeout, files={"image": (name, data, "image/png")},
                                data={"overwrite": "true"})
        return str(res.get("name") or name)

    def run(self, workflow: Mapping[str, Any], *, timeout: float = 30.0, max_wait: float = 300.0) -> List[bytes]:
        sub = self.request_json("POST", "/prompt", timeout=timeout, json_body={"prompt": workflow, "client_id": uuid.uuid4().hex})
        pid = sub.get("prompt_id")
        if not pid:
            detail = str((sub.get("error") or {}).get("message") if isinstance(sub.get("error"), Mapping) else sub.get("error", ""))
            raise ai_err("comfy", source=self.id, detail=detail or "prompt_id yok")

        def check() -> Tuple[bool, Any]:
            hist = self.request_json("GET", f"/history/{pid}", timeout=timeout)
            entry = hist.get(pid) if isinstance(hist, Mapping) else None
            if not entry:
                return False, None
            st = entry.get("status") or {}
            if st.get("status_str") == "error":
                msgs = [str(m) for m in (st.get("messages") or [])][-1:]
                raise ai_err("comfy", source=self.id, detail=(msgs[0] if msgs else "hata")[:160])
            if st.get("completed") or entry.get("outputs"):
                return True, entry.get("outputs") or {}
            return False, None
        outputs = self.poll(check, interval=float(self.setting("poll_interval", 1.0)), max_wait=max_wait)
        images: List[bytes] = []
        for node in outputs.values():
            for info in (node.get("images") or []):
                resp = self.request("GET", "/view", timeout=timeout,
                                    params={"filename": info["filename"], "subfolder": info.get("subfolder", ""), "type": info.get("type", "output")})
                images.append(resp.content)
        if not images:
            raise ai_err("comfy", source=self.id, detail="çıktı görseli yok")
        return images

    def _seed(self, seed: Optional[int]) -> int:
        return int(seed) if seed is not None else random.randint(0, 2 ** 31 - 1)

    def _result(self, images: List[bytes], seed: Optional[int]) -> ImageResult:
        return ImageResult(images, Usage(self.id, "comfyui", 0, 0, float(len(images)), 0.0), self.id, "comfyui", seed)

    # -- iş akışları ---------------------------------------------------------------------------------------
    def generate(self, prompt: str, *, vault: Any = None, aspect: str = "1:1", seed: Optional[int] = None, n: int = 1,
                 negative: str = "", width: Optional[int] = None, height: Optional[int] = None, steps: int = 25, cfg: float = 7.0,
                 model: Optional[str] = None, **_: Any) -> ImageResult:
        w, h = (width, height) if width and height else size_for(aspect, 1024, 8)
        ckpt = model or self.model(CHECKPOINT)
        images: List[bytes] = []
        s0 = self._seed(seed)
        for i in range(max(1, int(n))):
            images += self.run(wf.txt2img(prompt, negative, int(w), int(h), s0 + i, steps, cfg, ckpt))
        return self._result(images, s0)

    def upscale(self, image: bytes, factor: int = 2, **_: Any) -> ImageResult:
        name = self.upload("kadraj_in.png", image)
        model = str(self.setting("upscale_model", UPSCALE_MODEL))
        native = 4 if "x4" in model.lower() else 2
        return self._result(self.run(wf.upscale(name, model, factor / native)), None)

    def inpaint(self, image: bytes, mask: bytes, prompt: str, *, negative: str = "", seed: Optional[int] = None, steps: int = 25,
                cfg: float = 7.0, **_: Any) -> ImageResult:
        """``mask``: **beyaz** = yeniden çizilecek alan (siyah/saydam = korunur)."""
        iname, mname = self.upload("kadraj_in.png", image), self.upload("kadraj_mask.png", mask)
        s = self._seed(seed)
        return self._result(self.run(wf.inpaint(iname, mname, prompt, negative, s, steps, cfg, self.model(CHECKPOINT))), s)

    def img2img(self, image: bytes, prompt: str, *, negative: str = "", seed: Optional[int] = None, steps: int = 25, cfg: float = 7.0,
                strength: float = 0.6, **_: Any) -> ImageResult:
        name = self.upload("kadraj_in.png", image)
        s = self._seed(seed)
        return self._result(self.run(wf.img2img(name, prompt, negative, s, steps, cfg, self.model(CHECKPOINT), strength)), s)
