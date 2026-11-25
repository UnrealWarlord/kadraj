"""AI sağlayıcılarının ortak katmanı: sonuç türleri, HTTP isteği, durum → ``SmartError`` eşlemesi, kasadan anahtar.

* Anahtar yalnızca ``vault.get_secret`` ile, çağrı sırasında alınır; hata ayrıntılarına/log'a girmez (``redact``).
* Her istek zaman aşımlıdır; yönlendirme izlenmez; ağ yoksa ``net.offline`` kartı (çökme yok). HTTP durum kodu ve
  ``Retry-After`` hata nesnesine ``status`` / ``retry_after`` olarak eklenir → ``ProviderRegistry.call_with_fallback``
  429'da geri çekilir, çevrimdışında bulutları atlar.
* Maliyet/token **sağlayıcıda değil**, ``pipeline.gateway.AIGateway``'de ``api_usage``'a yazılır; sağlayıcılar yalnız
  ``Usage`` döner (veritabanından habersiz ve test edilebilir).
"""
from __future__ import annotations

import base64
import json
import re
import struct
import time
import wave
import io
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ...core import i18n
from ...core.contracts import ProviderKind, Severity, SmartAction, SmartError
from ...core.errors import is_network_error, is_timeout_error, translate
from .base import BaseProvider, _err, parse_retry_after, redact


# ---------------------------------------------------------------------------
# Sonuç türleri
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Usage:
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    units: float = 0.0              # görsel adedi / saniye / karakter (sağlayıcıya göre)
    cost_usd: float = 0.0           # TAHMİN (pricing.py)


@dataclass
class LLMResult:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    stop_reason: str = ""
    truncated: bool = False

    def json(self) -> Any:
        return extract_json(self.text)


@dataclass
class ImageResult:
    images: List[bytes]
    usage: Usage = field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    seed: Optional[int] = None
    mime: str = "image/png"


@dataclass
class AudioResult:
    wav: bytes                      # her zaman .wav (yalnız wav çıktı kuralı)
    usage: Usage = field(default_factory=Usage)
    provider: str = ""
    seconds: float = 0.0


@dataclass
class VideoResult:
    data: bytes
    usage: Usage = field(default_factory=Usage)
    provider: str = ""
    seconds: float = 0.0
    seed: Optional[int] = None
    mime: str = "video/mp4"


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def ai_err(code: str, *, source: str = "ai", technical: str = "", retryable: bool = False,
           severity: Severity = Severity.ERROR, actions: Sequence[SmartAction] = (), **fmt: Any) -> SmartError:
    base = f"ai.err.{code}"
    return SmartError(f"ai.{code}", i18n.t(f"{base}.title", **fmt), i18n.t(f"{base}.summary", **fmt),
                      steps=(i18n.t(f"{base}.step1", **fmt),), actions=tuple(actions), severity=severity,
                      technical=technical, retryable=retryable, source=source)


def install_action(packages: str) -> SmartAction:
    return SmartAction("install_optional", i18n.t("ai.err.install_action"), "copy", {"text": f"pip install {packages}"})


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Model yanıtından JSON ayıklar (kod çiti / önsöz tolere edilir). Olmazsa ``ai.invalid_json``."""
    candidates = [text.strip()]
    m = _JSON_FENCE.search(text or "")
    if m:
        candidates.insert(0, m.group(1).strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        a, b = (text or "").find(opener), (text or "").rfind(closer)
        if 0 <= a < b:
            candidates.append(text[a:b + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except (ValueError, TypeError):
            continue
    raise ai_err("invalid_json", technical=(text or "")[:200])


def sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def pcm_to_wav(pcm: bytes, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Ham 16-bit PCM → geçerli ``.wav`` baytları."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_seconds(wav: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


_QUOTA_WORDS = ("quota", "billing", "credit", "insufficient", "balance", "exceeded your current", "payment", "out of funds")


# ---------------------------------------------------------------------------
# Taban
# ---------------------------------------------------------------------------
class AIProvider(BaseProvider):
    """Gerçek iş çağrısı yapan sağlayıcıların tabanı (``BaseProvider`` ping davranışını korur)."""

    def __init__(self, id: str, display_name: str, kind: ProviderKind, *, key_id: Optional[str] = None,
                 sleep: Callable[[float], None] = time.sleep, **kw: Any) -> None:
        super().__init__(id, display_name, kind, **kw)
        self.key_id = key_id or id
        self.sleep = sleep

    # -- ayarlar -------------------------------------------------------------------------------
    def setting(self, name: str, default: Any = None) -> Any:
        if self.settings is None:
            return default
        try:
            v = self.settings.get(f"providers.{self.id}.{name}")
        except Exception:
            return default
        return default if v in (None, "") else v

    def model(self, default: str) -> str:
        return str(self.setting("model", default))

    # -- anahtar -----------------------------------------------------------------------------------
    def secret(self, vault: Any) -> Optional[str]:
        if not self.needs_key:
            return None
        if vault is None:
            raise self._no_key()
        try:
            secret = vault.get_secret(self.key_id)
        except SmartError:
            raise
        except Exception:                     # noqa: BLE001
            secret = None
        if not secret:
            raise self._no_key()
        return str(secret)

    def _no_key(self) -> SmartError:
        return _err("no_key", self.id, name=self.display_name, actions=self._key_actions())

    def is_configured(self, vault: Any) -> bool:
        if not self.needs_key:
            return True
        try:
            return bool(vault.has_secret(self.key_id))
        except Exception:
            return False

    # -- HTTP --------------------------------------------------------------------------------------------
    def auth_headers(self, secret: Optional[str]) -> Dict[str, str]:
        h: Dict[str, str] = {"User-Agent": "kadraj-ai", **self.extra_headers}
        if secret:
            if self.auth == "bearer":
                h["Authorization"] = f"Bearer {secret}"
            elif self.auth.startswith("header:"):
                h[self.auth.split(":", 1)[1]] = secret
        return h

    def request(self, method: str, path_or_url: str, *, secret: Optional[str] = None, timeout: float = 60.0,
                json_body: Any = None, params: Optional[Mapping[str, Any]] = None, headers: Optional[Mapping[str, str]] = None,
                files: Any = None, data: Any = None, content: Any = None, auth: bool = True):
        """Tek HTTP isteği; 4xx/5xx → ``SmartError``; ağ/zaman aşımı → sade kart. ``httpx.Response`` döner."""
        import httpx
        url = path_or_url if path_or_url.startswith("http") else self.endpoint() + path_or_url
        if not self.endpoint() and not path_or_url.startswith("http"):
            raise _err("misconfigured", self.id, name=self.display_name)
        hdrs = {**(self.auth_headers(secret) if auth else {"User-Agent": "kadraj-ai"}), **(headers or {})}
        tmo = httpx.Timeout(timeout, connect=min(timeout, 5.0))
        try:
            with httpx.Client(transport=self.transport, timeout=tmo, follow_redirects=False) as client:
                resp = client.request(method, url, headers=hdrs, params=dict(params) if params else None, json=json_body,
                                      files=files, data=data, content=content)
        except SmartError:
            raise
        except Exception as exc:              # noqa: BLE001 — ağ, zaman aşımı, TLS
            raise self._net_error(exc, secret) from None
        if resp.status_code >= 400:
            raise self._status_error(resp, secret)
        return resp

    def request_json(self, method: str, path_or_url: str, **kw: Any) -> Any:
        resp = self.request(method, path_or_url, **kw)
        try:
            return resp.json()
        except ValueError:
            raise ai_err("bad_response", source=self.id, name=self.display_name, technical=redact(resp.text[:200])) from None

    def _net_error(self, exc: BaseException, secret: Optional[str]) -> SmartError:
        err = self._translate(exc, secret)
        if is_network_error(exc) or is_timeout_error(exc):
            err.reason = ConnectionError("offline")        # type: ignore[attr-defined]  # call_with_fallback çevrimdışı sayar
        return err

    def _status_error(self, resp: Any, secret: Optional[str]) -> SmartError:
        status = resp.status_code
        body = ""
        try:
            body = resp.text[:400]
        except Exception:
            pass
        tech = redact(f"HTTP {status} {body}".strip(), secret)
        name = self.display_name
        low = body.lower()
        if status in (401, 403):
            err = _err("auth", self.id, technical=tech, name=name, actions=self._key_actions())
        elif status == 402 or (status in (400, 429) and any(w in low for w in _QUOTA_WORDS)):
            err = ai_err("quota", source=self.id, technical=tech, name=name, severity=Severity.WARNING, retryable=False)
        elif status == 404:
            err = _err("not_found", self.id, technical=tech, name=name)
        elif status == 429:
            retry = parse_retry_after(resp.headers.get("retry-after"))
            err = _err("rate_limited", self.id, technical=tech + (f" retry-after={retry:.0f}s" if retry else ""),
                       name=name, retryable=True, severity=Severity.WARNING)
            err.retry_after = retry                          # type: ignore[attr-defined]
        elif status >= 500:
            err = _err("server", self.id, technical=tech, name=name, retryable=True, severity=Severity.WARNING)
        else:
            err = _err("http", self.id, technical=tech, name=name, status=status)
        err.status = status                                  # type: ignore[attr-defined]
        return err

    def poll(self, check: Callable[[], Tuple[bool, Any]], *, interval: float = 2.0, max_wait: float = 180.0) -> Any:
        """``check() -> (bitti_mi, değer)``'i aralıkla çağırır; süre dolarsa ``ai.timeout``."""
        waited = 0.0
        while True:
            done, value = check()
            if done:
                return value
            if waited >= max_wait:
                raise ai_err("timeout", source=self.id, name=self.display_name, retryable=True)
            self.sleep(interval)
            waited += interval

    def download(self, url: str, timeout: float = 120.0) -> bytes:
        return self.request("GET", url, timeout=timeout, auth=False).content
