"""AI testleri: gerçek ağ YOK (soket engelli + ``httpx.MockTransport``), sahte kasa/ayar/veritabanı, ``ai.*`` dizeleri yüklü."""
from __future__ import annotations

import json
import socket
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from kadraj.ai_engine import aistrings
from kadraj.core import i18n


@pytest.fixture(autouse=True)
def _ai_strings():
    i18n.register_strings(aistrings.STRINGS)
    i18n.set_language("tr")
    yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(self, *a, **k):
        raise OSError("ağ kapalı (test)")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("dns kapalı")))


class FakeVault:
    def __init__(self, **secrets: str) -> None:
        self.secrets = dict(secrets)

    def get_secret(self, pid: str) -> Optional[str]:
        return self.secrets.get(pid)

    def has_secret(self, pid: str) -> bool:
        return bool(self.secrets.get(pid))


class FakeSettings:
    def __init__(self, **values: Any) -> None:
        self.values = dict(values)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value


class FakeDB:
    def __init__(self) -> None:
        self.usage: List[Dict[str, Any]] = []
        self.prompts: List[Dict[str, Any]] = []

    def add_usage(self, provider: str, **kw: Any) -> int:
        self.usage.append({"provider": provider, **kw})
        return len(self.usage)


class Recorder:
    """``httpx.MockTransport`` işleyicisi: istekleri kaydeder, ``routes`` ile yanıt üretir."""

    def __init__(self, routes: Optional[Callable[[httpx.Request], httpx.Response]] = None) -> None:
        self.requests: List[httpx.Request] = []
        self.routes = routes
        self.queue: List[httpx.Response] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.queue:
            return self.queue.pop(0)
        if self.routes is not None:
            return self.routes(request)
        return httpx.Response(200, json={})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def body(self, i: int = -1) -> Any:
        return json.loads(self.requests[i].content or b"{}")


def png_bytes(size=(8, 8), color=(200, 30, 30)) -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def vault() -> FakeVault:
    return FakeVault(anthropic="sk-ant-SECRET-1234", openai_compat="sk-oa-SECRET-5678", bfl_flux="bfl-SECRET", google_imagen="g-SECRET",
                     elevenlabs="xi-SECRET", video="vid-SECRET")


# ------------------------------------------------------------------------------------------------ dünya (sahte servisler)
class World:
    """Bütün sağlayıcı ana makinelerini yanıtlayan sahte dünya. ``down`` kümesindeki ana makineler bağlantı hatası verir."""

    def __init__(self) -> None:
        self.rec = Recorder(self.route)
        self.down: set = set()
        self.llm_text = '{"subject": "an old ferry on a foggy morning in Istanbul", "lighting": "soft diffused fog light", "angle": "", "style": "sinematik", "aspect": "16:9", "lens": "", "mood": "warm", "negatives": []}'
        self.llm_fn: Optional[Callable[[dict], str]] = None
        self.image = png_bytes((64, 48), (30, 90, 200))
        self.video_calls = 0
        self.fail_video_after: Optional[int] = None

    def route(self, r: httpx.Request) -> httpx.Response:
        import base64
        host = r.url.host + (f":{r.url.port}" if r.url.port else "")
        if host in self.down or r.url.host in self.down:
            raise httpx.ConnectError("kapalı")
        if r.url.host == "api.anthropic.com":
            body = json.loads(r.content or b"{}")
            text = self.llm_fn(body) if self.llm_fn else (self.llm_text if "output_config" in body else "tamam çeviri")
            return httpx.Response(200, json={"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                             "usage": {"input_tokens": 50, "output_tokens": 10}})
        if r.url.host == "api.bfl.ai":
            if r.method == "POST":
                return httpx.Response(200, json={"id": "i", "polling_url": "https://api.bfl.ai/v1/get_result?id=i"})
            return httpx.Response(200, json={"status": "Ready", "result": {"sample": "https://cdn.bfl.ai/o.png"}})
        if r.url.host == "cdn.bfl.ai":
            return httpx.Response(200, content=self.image)
        if r.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(200, json={"predictions": [{"bytesBase64Encoded": base64.b64encode(self.image).decode()}]})
        if r.url.host == "api.openai.com":
            if r.url.path.endswith("/images/edits") or r.url.path.endswith("/images/generations"):
                return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(self.image).decode()}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": "openai"}, "finish_reason": "stop"}], "usage": {}})
        if r.url.host == "127.0.0.1" and r.url.port == 8188:
            if r.url.path == "/upload/image":
                return httpx.Response(200, json={"name": "u.png"})
            if r.url.path == "/prompt":
                return httpx.Response(200, json={"prompt_id": "C1"})
            if r.url.path.startswith("/history"):
                return httpx.Response(200, json={"C1": {"status": {"completed": True}, "outputs": {"9": {"images": [{"filename": "o.png"}]}}}})
            if r.url.path == "/view":
                return httpx.Response(200, content=self.image)
            return httpx.Response(200, json={})
        if r.url.host == "vid.example":
            self.video_calls += 1
            if self.fail_video_after is not None and self.video_calls > self.fail_video_after:
                return httpx.Response(402, json={"error": {"message": "credit balance too low"}})
            return httpx.Response(200, json={"video_url": "https://cdn.vid/x.mp4"})
        if r.url.host == "cdn.vid":
            return httpx.Response(200, content=self.mp4())
        if r.url.host == "api.elevenlabs.io":
            return httpx.Response(200, content=b"\x00\x01" * 4800)
        if r.url.host == "127.0.0.1" and r.url.port == 11434:
            return httpx.Response(200, json={"message": {"content": "yerel"}})
        return httpx.Response(404, json={})

    def mp4(self) -> bytes:
        if getattr(self, "_mp4", None) is None:
            import tempfile
            from pathlib import Path
            d = Path(tempfile.mkdtemp(prefix="kadraj-ai-"))
            make_mp4(d / "v.mp4", 1.0, "blue")
            self._mp4 = (d / "v.mp4").read_bytes()
        return self._mp4

    @property
    def requests(self):
        return self.rec.requests

    def hosts(self) -> List[str]:
        return [r.url.host for r in self.rec.requests]


@pytest.fixture()
def world() -> World:
    return World()


def make_mp4(path, seconds=1.0, color="red") -> None:
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=10:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, timeout=60)
