"""Genel ``VideoProvider`` (img2vid / text2vid): uç nokta, yollar ve alan adları **Ayarlar'dan** yapılandırılır.

Ayarlar (``providers.video.*``): ``base_url``, ``submit_path`` (varsayılan ``/v1/videos``), ``status_path`` (``/v1/videos/{id}``),
``model``, ``auth`` (``bearer`` | ``header:<Ad>``), ``cost_per_second``, ``poll_interval``, ``max_wait``.
Gönderim gövdesi: ``{model, prompt, duration, aspect_ratio, seed, mode, image(data URI)}``; yanıtta iş kimliği, durum ve çıktı adresi
yaygın alan adlarından aranır (``id|task_id|job_id``, ``status|state``, ``video_url|url|output.url|result.url|data[0].url``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from ...core.contracts import ProviderKind
from .. import pricing
from .aicore import AIProvider, Usage, VideoResult, ai_err, b64, sniff_mime

_ID_KEYS = ("id", "task_id", "job_id", "request_id", "generation_id")
_STATUS_KEYS = ("status", "state")
_DONE = {"succeeded", "success", "completed", "complete", "done", "ready", "finished"}
_FAILED = {"failed", "error", "cancelled", "canceled", "rejected", "moderated"}
_URL_PATHS = ("video_url", "url", "output.url", "output", "result.url", "result.video_url", "data.0.url", "video.url", "videos.0.url",
              "output.0", "outputs.0")


def dig(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, Mapping):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


class VideoProvider(AIProvider):
    def __init__(self, *, settings: Any = None, transport: Any = None, **kw: Any) -> None:
        super().__init__("video", "Video sağlayıcısı", ProviderKind.VIDEO, base_url="", ping_path="/", auth="bearer",
                         settings=settings, transport=transport, **kw)

    def configured_endpoint(self) -> bool:
        return bool(self.endpoint())

    def is_configured(self, vault: Any) -> bool:
        return self.configured_endpoint() and super().is_configured(vault)

    def auth_headers(self, secret: Optional[str]) -> Dict[str, str]:
        mode = str(self.setting("auth", "bearer"))
        h = {"User-Agent": "kadraj-ai"}
        if secret:
            if mode.startswith("header:"):
                h[mode.split(":", 1)[1]] = secret
            else:
                h["Authorization"] = f"Bearer {secret}"
        return h

    def generate(self, prompt: str, *, vault: Any = None, image: Optional[bytes] = None, seconds: float = 5.0,
                 aspect: str = "16:9", seed: Optional[int] = None, model: Optional[str] = None, timeout: float = 60.0,
                 **_: Any) -> VideoResult:
        if not self.configured_endpoint():
            raise ai_err("no_video", source=self.id)
        secret = self.secret(vault)
        mdl = model or self.model("video-default")
        body: Dict[str, Any] = {"model": mdl, "prompt": prompt, "duration": float(seconds), "aspect_ratio": aspect,
                                "mode": "img2vid" if image else "text2vid"}
        if seed is not None:
            body["seed"] = int(seed)
        if image:
            body["image"] = f"data:{sniff_mime(image)};base64,{b64(image)}"
        sub = self.request_json("POST", str(self.setting("submit_path", "/v1/videos")), secret=secret, timeout=timeout, json_body=body)
        direct = self._url(sub)
        if direct:
            data = self.download(direct)
        else:
            job_id = next((str(sub[k]) for k in _ID_KEYS if isinstance(sub, Mapping) and sub.get(k)), "")
            if not job_id:
                raise ai_err("bad_response", source=self.id, name=self.display_name)
            path = str(self.setting("status_path", "/v1/videos/{id}")).replace("{id}", job_id)

            def check() -> Tuple[bool, Any]:
                st = self.request_json("GET", path, secret=secret, timeout=timeout)
                status = str(next((st.get(k) for k in _STATUS_KEYS if isinstance(st, Mapping) and st.get(k)), "")).lower()
                if status in _FAILED:
                    raise ai_err("moderated" if status in ("rejected", "moderated") else "bad_response", source=self.id,
                                 name=self.display_name, technical=status)
                url = self._url(st) if (status in _DONE or not status) else None
                return (bool(url), url)
            url = self.poll(check, interval=float(self.setting("poll_interval", 3.0)), max_wait=float(self.setting("max_wait", 600.0)))
            data = self.download(url)
        per = self.setting("cost_per_second", None)
        cost = pricing.video_cost(seconds, float(per) if per is not None else None)
        return VideoResult(data, Usage(self.id, mdl, 0, 0, float(seconds), cost), self.id, float(seconds), seed)

    @staticmethod
    def _url(payload: Any) -> Optional[str]:
        for p in _URL_PATHS:
            v = dig(payload, p)
            if isinstance(v, str) and v.startswith("http"):
                return v
        return None
