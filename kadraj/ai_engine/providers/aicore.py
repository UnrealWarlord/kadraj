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
from ...core.contracts import PingResult, ProviderKind, Severity, SmartAction, SmartError
from ...core.errors import is_network_error, is_timeout_error, translate
from .. import aierrors
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
    """``ai.err.<kod>.*`` dizelerinden kart: başlık, özet, (varsa) neden ve **numaralı adımlar** (step1, step2…)."""
    base = f"ai.err.{code}"
    steps = tuple(i18n.t(f"{base}.step{i}", **fmt) for i in range(1, 9) if i18n.has(f"{base}.step{i}"))
    cause = i18n.t(f"{base}.cause", **fmt) if i18n.has(f"{base}.cause") else ""
    return SmartError(f"ai.{code}", i18n.t(f"{base}.title", **fmt), i18n.t(f"{base}.summary", **fmt), cause=cause,
                      steps=steps, actions=tuple(actions) or aierrors.default_actions(code), severity=severity, technical=technical,
                      retryable=retryable, source=source)


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


# ---------------------------------------------------------------------------
# Taban
# ---------------------------------------------------------------------------
class AIProvider(BaseProvider):
    """Gerçek iş çağrısı yapan sağlayıcıların tabanı. Hatalar ``aierrors`` ile **neden + numaralı adım** kartına çevrilir
    (yanlış anahtar, bakiye/kota, hız sınırı, internet yok, model yok, yerel servis kapalı, izin yok, sunucu sorunu)."""

    def __init__(self, id: str, display_name: str, kind: ProviderKind, *, key_id: Optional[str] = None,
                 sleep: Callable[[float], None] = time.sleep, **kw: Any) -> None:
        super().__init__(id, display_name, kind, **kw)
        self.key_id = key_id or id
        self.sleep = sleep
        self._good: Dict[str, str] = {}              # grup → son çalışan model (model-yok durumunda sıradaki aday denenir)

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

    def candidates(self, group: str, defaults: Sequence[str], explicit: Optional[str] = None, *, setting: str = "model") -> List[str]:
        """Denenecek model adları. Kullanıcı bir model seçtiyse (çağrı ya da Ayarlar) **yalnız o**; yoksa varsayılan liste
        (daha önce çalışan başa alınır). Eski bir model kapandıysa sıradaki denenir."""
        chosen = explicit or self.setting(setting)
        if chosen:
            return [str(chosen)]
        out = list(defaults)
        good = self._good.get(group)
        if good in out:
            out.remove(good)
            out.insert(0, good)
        return out

    def walk(self, group: str, candidates: Sequence[str], call: Callable[[str], Any]) -> Any:
        """``call(model)``'ı adaylarla dener; yalnız ``ai.model_not_found`` ise sıradakine geçer."""
        last: Optional[SmartError] = None
        for m in candidates:
            try:
                res = call(m)
            except SmartError as err:
                if err.code != "ai.model_not_found" or len(candidates) == 1:
                    raise
                last = err
                continue
            self._good[group] = m
            return res
        assert last is not None
        raise last

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
        return aierrors.error_card("no_key", self.id, name=self.display_name)

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
                files: Any = None, data: Any = None, content: Any = None, auth: bool = True, model: Optional[str] = None):
        """Tek HTTP isteği; 4xx/5xx → ``SmartError``; ağ/zaman aşımı → sade kart. ``httpx.Response`` döner.
        ``model``: hata kartında adı geçsin ve 404 "model yok" sayılsın diye verilir."""
        import httpx
        url = path_or_url if path_or_url.startswith("http") else self.endpoint() + path_or_url
        if not self.endpoint() and not path_or_url.startswith("http"):
            raise _err("misconfigured", self.id, name=self.display_name)
        hdrs = {**(self.auth_headers(secret) if auth else {"User-Agent": "kadraj-ai"}), **(headers or {})}
        tmo = httpx.Timeout(timeout, connect=min(timeout, 8.0))
        try:
            with httpx.Client(transport=self.transport, timeout=tmo, follow_redirects=False) as client:
                resp = client.request(method, url, headers=hdrs, params=dict(params) if params else None, json=json_body,
                                      files=files, data=data, content=content)
        except SmartError:
            raise
        except Exception as exc:              # noqa: BLE001 — ağ, zaman aşımı, TLS
            raise self._net_error(exc, secret) from None
        if resp.status_code >= 400:
            raise self._status_error(resp, secret, model)
        return resp

    def request_json(self, method: str, path_or_url: str, **kw: Any) -> Any:
        resp = self.request(method, path_or_url, **kw)
        try:
            return resp.json()
        except ValueError:
            raise ai_err("bad_response", source=self.id, name=self.display_name, technical=redact(resp.text[:200])) from None

    def _net_error(self, exc: BaseException, secret: Optional[str]) -> SmartError:
        names = {c.__name__ for c in type(exc).__mro__}
        slow = bool(names & {"ReadTimeout", "WriteTimeout", "PoolTimeout"}) or (isinstance(exc, TimeoutError) and "Connect" not in "".join(names))
        tech = redact(f"{type(exc).__name__}: {exc}", secret)
        if slow:                                                  # bağlandı ama yanıt gecikti: internet var, servis yavaş
            return aierrors.error_card("timeout", self.id, name=self.display_name, technical=tech)
        if is_network_error(exc) or is_timeout_error(exc):
            err = aierrors.error_card("local_down" if self.local else "offline", self.id, name=self.display_name, technical=tech)
            if not self.local:                              # yerel servis kapalıysa internet yok demek değildir: bulutlar atlanmaz
                err.reason = ConnectionError("offline")    # type: ignore[attr-defined]  # call_with_fallback çevrimdışı sayar
            return err
        err = self._translate(exc, secret)
        return err

    def _status_error(self, resp: Any, secret: Optional[str], model: Optional[str] = None) -> SmartError:
        status = resp.status_code
        body = ""
        try:
            body = resp.text[:600]
        except Exception:
            pass
        tech = redact(f"HTTP {status} {body}".strip(), secret)
        code, err = aierrors.from_http(self.id, self.display_name, status, body, headers=dict(resp.headers), model=model or "",
                                       technical=tech, has_model=model is not None)
        if code == "rate_limit":
            retry = parse_retry_after(resp.headers.get("retry-after"))
            if retry:
                err = aierrors.error_card("rate_limit", self.id, name=self.display_name, technical=tech + f" retry-after={retry:.0f}s",
                                          retry_in=retry)       # "30 sn" → "1 dakika bekle"
            err.retry_after = retry                          # type: ignore[attr-defined]
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

    # -- bağlantı testi: ucuz GET (model/kredi listesi) -------------------------------------------------------
    def test_connection(self, vault: Optional[Any] = None, timeout: float = 8.0) -> PingResult:
        """Ayarlar'daki "Bağlantıyı test et": para harcamayan tek GET. Sonuç sade Türkçe; asla istisna atmaz."""
        secret: Optional[str] = None
        started = time.perf_counter()
        try:
            if self.needs_key:
                secret = self.secret(vault)
            started = time.perf_counter()
            resp = self.request("GET", self.ping_path, secret=secret, timeout=timeout, params=self.ping_params or None)
            ms = (time.perf_counter() - started) * 1000.0
            return self.ping_result(resp, ms)
        except SmartError as err:
            return PingResult(False, None, err.summary, err)
        except Exception as exc:               # noqa: BLE001
            err = self._net_error(exc, secret)
            return PingResult(False, None, err.summary, err)

    def ping_result(self, resp: Any, ms: float) -> PingResult:
        return PingResult(True, round(ms, 1), self.ping_message(resp, ms))

    def ping_message(self, resp: Any, ms: float) -> str:
        """Başarılı GET'in sade özeti (alt sınıflar model sayısı/kredi gibi bilgi ekler)."""
        n = _count(resp, ("data", "models"))
        if n:
            return i18n.t("ai.test.ok_models", name=self.display_name, n=n, ms=f"{ms:.0f}")
        return i18n.t("ai.test.ok", name=self.display_name, ms=f"{ms:.0f}")

    def quick_test(self, vault: Optional[Any] = None, timeout: float = 20.0) -> PingResult:
        """"Kaydet ve dene": varsayılan olarak ``test_connection``; metin sağlayıcıları ek olarak minik bir istek dener."""
        return self.test_connection(vault, timeout)


def _count(resp: Any, keys: Sequence[str]) -> int:
    try:
        data = resp.json()
    except Exception:
        return 0
    if isinstance(data, Mapping):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return len(v)
    return 0
