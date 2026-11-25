"""Sahte kasa ve ağ/alt süreç yer tutucuları: gerçek kasa, ağ ve pip çağrılmaz."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from kadraj.core.contracts import SmartError, VaultStatus

FULL_KEY = "sk-ant-api03-SECRETKEY-abcdefghijklmnopqrstuvwxyz1234"


def _mask(value: str) -> str:
    return "sk-••••" + value[-4:]


class FakeVault:
    """``VaultAPI`` + isteğe bağlı ``status``. ``locked=True`` iken yazma ve düz okuma SmartError atar."""

    is_placeholder = False
    namespace = "default"

    def __init__(self, secrets: Optional[Dict[str, str]] = None, *, locked: bool = False) -> None:
        self._secrets: Dict[str, str] = dict(secrets or {})
        self.locked = locked

    def is_available(self) -> bool:
        return True

    def providers(self) -> List[str]:
        return [] if self.locked else list(self._secrets)

    def has_secret(self, provider_id: str) -> bool:
        return (not self.locked) and provider_id in self._secrets

    def masked(self, provider_id: str) -> str:
        if self.locked or provider_id not in self._secrets:
            return ""
        return _mask(self._secrets[provider_id])

    def set_secret(self, provider_id: str, value: str) -> None:
        if self.locked:
            raise SmartError("vault.locked", "Kasa kilitli", "kilitli")
        if not value:
            raise ValueError("boş anahtar")
        self._secrets[provider_id] = value

    def get_secret(self, provider_id: str) -> Optional[str]:
        if self.locked:
            raise SmartError("vault.locked", "Kasa kilitli", "kilitli")
        return self._secrets.get(provider_id)

    def delete_secret(self, provider_id: str) -> bool:
        return self._secrets.pop(provider_id, None) is not None

    def status(self) -> VaultStatus:
        return VaultStatus(state="locked" if self.locked else "ready", mode="keyring")


class FakeHttp:
    """Yerel servis algılama için: Ollama açık (iki model), ComfyUI kapalı."""

    def __init__(self) -> None:
        self.urls: List[str] = []

    def __call__(self, url: str, timeout: float) -> Any:
        self.urls.append(url)
        if url.endswith("/api/tags"):
            return {"models": [{"name": "llama3"}, {"name": "qwen2"}]}
        raise ConnectionError("kapalı")


class FakeRun:
    """``run_cmd`` taklidi: ffmpeg -version ve pip. Hiçbir gerçek süreç başlamaz."""

    def __init__(self, pip_code: int = 0, pip_out: str = "Successfully installed yt-dlp") -> None:
        self.calls: List[List[str]] = []
        self.pip_code = pip_code
        self.pip_out = pip_out

    def __call__(self, args: Any, timeout: float) -> tuple:
        self.calls.append(list(args))
        if "-version" in args:
            return 0, "ffmpeg version 7.1 Copyright (c) 2000-2025"
        if "pip" in args:
            return (self.pip_code, self.pip_out)
        return 1, "bilinmeyen komut"


class FakePopen:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, args: Any, **kwargs: Any) -> None:
        self.calls.append({"args": list(args), **kwargs})
