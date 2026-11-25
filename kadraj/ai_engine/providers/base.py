"""Sağlayıcı tabanı, kayıt tablosu ve fallback zinciri (Adım 3; sözleşme: ``core.contracts`` §4 + §9).

* ``BaseProvider``  : ``Provider`` protokolünün ortak gövdesi. ``test_connection()`` zaman aşımlı, **asla istisna
  atmaz**; internetsizse ``PingResult(ok=False, error=SmartError)`` + gecikme ms döner. ``ping`` aynı işin takma adıdır.
* ``ProviderRegistry``: kayıt tablosu + zincir. Varsayılan sıra **bulut → yerel**; ``settings["fallback_order"][tür]``
  ile değiştirilebilir. ``call_with_fallback`` zinciri dener: çevrimdışıysa bulutları atlar, 429'da
  (``Retry-After`` saygılı, üstel) geri çekilir ve sağlayıcıyı soğumaya alır, hepsi başarısızsa isteğe bağlı olarak
  **kuyruğa alır** (``ctx.jobs``).
* ``default_registry``: Ayarlar'ın "Bağlantıyı Test Et" ışıkları için bilinen servislerin **hafif ping profilleri**
  (anthropic, openai_compat, bfl_flux, google_imagen, elevenlabs, pexels, pixabay, freesound, ollama, comfyui,
  whisper_local). Adım 8 gerçek sağlayıcıları ``registry.register(p, replace=True)`` ile bunların yerine koyar.

Gizlilik: anahtar ``repr``'e, hata ayrıntılarına ve log'a girmez (``redact``); HTTP yönlendirmeleri izlenmez
(kimlik bilgisi başka sunucuya sızmasın). Ağ yalnızca ``test_connection``/``operation`` içinde, zaman aşımıyla kullanılır.
"""
from __future__ import annotations

import importlib.util
import os
import re
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ...core.contracts import (Attempt, FallbackResult, PingResult, Provider, ProviderKind, Severity,
                              SmartAction, SmartError, VaultAPI)
from ...core.errors import is_network_error, is_timeout_error, translate
from ...core.i18n import t

_QUERY_RE = re.compile(r"\?[^\s'\")]*")


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
class ProviderHTTPError(Exception):
    """Sağlayıcı çağrısının HTTP durum koduyla başarısız olduğunu bildirir (``operation`` bunu atabilir)."""

    def __init__(self, status: int, message: str = "", retry_after: Optional[float] = None) -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = int(status)
        self.retry_after = retry_after


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """``Retry-After`` başlığı: saniye sayısı ya da HTTP tarihi → saniye (``None`` = yok/anlaşılmadı)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def http_status(exc: BaseException) -> Optional[int]:
    for attr in ("status", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def retry_after_of(exc: BaseException) -> Optional[float]:
    v = getattr(exc, "retry_after", None)
    if isinstance(v, (int, float)):
        return float(v)
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is not None:
        try:
            return parse_retry_after(headers.get("retry-after"))
        except Exception:
            return None
    return None


def redact(text: str, *secrets_: Optional[str]) -> str:
    """Metinden anahtarları ve URL sorgu dizelerini siler (hata ayrıntıları güvenle gösterilebilsin)."""
    out = text or ""
    for secret in secrets_:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "••••")
    return _QUERY_RE.sub("?…", out)


def backoff_delay(attempt: int, retry_after: Optional[float] = None, *, base: float = 1.0, cap: float = 30.0) -> float:
    """Üstel geri çekilme: ``base * 2**attempt`` (üst sınır ``cap``); sunucu ``Retry-After`` verdiyse o (≤ ``cap``)."""
    if retry_after is not None:
        return min(cap, max(0.0, retry_after))
    return min(cap, base * (2 ** attempt))


def _err(code: str, source: str, *, technical: str = "", retryable: bool = False, severity: Severity = Severity.ERROR,
         actions: Tuple[SmartAction, ...] = (), **fmt: Any) -> SmartError:
    key = f"provider.{code}"
    return SmartError(f"provider.{code}", t(f"{key}.title", **fmt), t(f"{key}.summary", **fmt),
                      cause=t(f"{key}.cause", **fmt), steps=(t(f"{key}.step1", **fmt),), actions=actions,
                      severity=severity, technical=technical, retryable=retryable, source=source)


# ---------------------------------------------------------------------------
# Taban sağlayıcı
# ---------------------------------------------------------------------------
class BaseProvider:
    """Genel amaçlı HTTP ping sağlayıcısı. Gerçek sağlayıcılar (Adım 8) bundan türeyip iş çağrılarını ekler.

    ``auth``: ``"bearer"`` (Authorization: Bearer), ``"token"`` (Authorization: Token), ``"header:<Ad>"``,
    ``"query:<ad>"`` ya da ``"none"``.
    """

    def __init__(self, id: str, display_name: str, kind: ProviderKind, *, needs_key: bool = True, local: bool = False,
                 base_url: str = "", ping_path: str = "", auth: str = "bearer",
                 extra_headers: Optional[Mapping[str, str]] = None, ping_params: Optional[Mapping[str, str]] = None,
                 settings: Any = None, transport: Any = None) -> None:
        self.id, self.display_name, self.kind = id, display_name, kind
        self.key_id = id                       # kasadaki anahtar adı (bir sağlayıcı başkasınınkini paylaşabilir)
        self.needs_key, self.local = needs_key, local
        self.base_url, self.ping_path, self.auth = base_url, ping_path, auth
        self.extra_headers = dict(extra_headers or {})
        self.ping_params = dict(ping_params or {})
        self.settings = settings
        self.transport = transport           # testler httpx.MockTransport verir; None → gerçek ağ

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id} kind={self.kind.value} local={self.local}>"

    # -- Provider sözleşmesi ----------------------------------------------------
    def is_configured(self, vault: VaultAPI) -> bool:
        """Anahtar var mı (yerel/anahtarsızsa her zaman ``True``)? Ağa çıkmaz, istisna atmaz."""
        if not self.needs_key:
            return True
        try:
            return bool(vault.has_secret(self.key_id))
        except Exception:
            return False

    def ping(self, vault: Optional[VaultAPI] = None, timeout: float = 5.0) -> PingResult:
        return self.test_connection(vault, timeout)

    def test_connection(self, vault: Optional[VaultAPI] = None, timeout: float = 5.0) -> PingResult:
        """"Bağlantıyı Test Et": zaman aşımlı, gecikme ms ölçer, hatayı ``SmartError`` olarak döner. Asla atmaz."""
        secret: Optional[str] = None
        try:
            if self.needs_key:
                if vault is None:
                    return PingResult(False, None, t("provider.no_key.summary", name=self.display_name),
                                      _err("no_key", self.id, name=self.display_name, actions=self._key_actions()))
                secret = vault.get_secret(self.key_id)
                if not secret:
                    return PingResult(False, None, t("provider.no_key.summary", name=self.display_name),
                                      _err("no_key", self.id, name=self.display_name, actions=self._key_actions()))
            started = time.perf_counter()
            status, headers = self._probe(secret, timeout)
            latency = (time.perf_counter() - started) * 1000.0
        except SmartError as err:                      # örn. kasa kilitli
            return PingResult(False, None, err.summary, err)
        except Exception as exc:
            err = self._translate(exc, secret)
            return PingResult(False, None, err.summary, err)
        return self._interpret(status, headers, latency, secret)

    # -- genişletme noktaları -----------------------------------------------------
    def endpoint(self) -> str:
        override = None
        if self.settings is not None:
            try:
                override = self.settings.get(f"providers.{self.id}.base_url")
            except Exception:
                override = None
        return (override or self.base_url or "").rstrip("/")

    def build_request(self, secret: Optional[str]) -> Tuple[str, Dict[str, str], Dict[str, str]]:
        headers = {"User-Agent": "kadraj-connection-test", **self.extra_headers}
        params = dict(self.ping_params)
        if secret:
            if self.auth == "bearer":
                headers["Authorization"] = f"Bearer {secret}"
            elif self.auth == "token":
                headers["Authorization"] = f"Token {secret}"
            elif self.auth.startswith("header:"):
                headers[self.auth.split(":", 1)[1]] = secret
            elif self.auth.startswith("query:"):
                params[self.auth.split(":", 1)[1]] = secret
        return self.endpoint() + self.ping_path, headers, params

    def _probe(self, secret: Optional[str], timeout: float) -> Tuple[int, Mapping[str, str]]:
        """Tek bir hafif GET. ``(durum kodu, başlıklar)`` döner; ağ hatalarını olduğu gibi fırlatır."""
        import httpx
        url, headers, params = self.build_request(secret)
        if not self.endpoint():
            raise ProviderHTTPError(0, "adres tanımsız")
        tmo = httpx.Timeout(timeout, connect=min(timeout, 3.0))
        with httpx.Client(transport=self.transport, timeout=tmo, follow_redirects=False) as client:
            resp = client.get(url, headers=headers, params=params)
        return resp.status_code, resp.headers

    # -- sonuç yorumlama -------------------------------------------------------------
    def _key_actions(self) -> Tuple[SmartAction, ...]:
        return (SmartAction("open_vault", t("err.action.open_settings"), "open_settings", {"section": "vault"}),)

    def _interpret(self, status: int, headers: Mapping[str, str], latency: float, secret: Optional[str]) -> PingResult:
        name = self.display_name
        if 200 <= status < 300:
            return PingResult(True, round(latency, 1), t("provider.ok", name=name, ms=f"{latency:.0f}"))
        tech = f"HTTP {status}"
        if status in (401, 403):
            err = _err("auth", self.id, technical=tech, name=name, actions=self._key_actions())
        elif status == 429:
            ra = parse_retry_after(headers.get("retry-after") if headers else None)
            err = _err("rate_limited", self.id, technical=tech + (f" retry-after={ra:.0f}s" if ra else ""),
                       retryable=True, severity=Severity.WARNING, name=name)
        elif status == 404:
            err = _err("not_found", self.id, technical=tech, name=name)
        elif status >= 500:
            err = _err("server", self.id, technical=tech, retryable=True, severity=Severity.WARNING, name=name)
        else:
            err = _err("http", self.id, technical=tech, name=name, status=status)
        return PingResult(False, round(latency, 1), err.summary, err)

    def _translate(self, exc: BaseException, secret: Optional[str]) -> SmartError:
        """İstisnayı sade karta çevirir; yerel servis kapalıysa "çalışmıyor", bulutta "internet yok" der."""
        if isinstance(exc, ProviderHTTPError) and exc.status == 0:
            return _err("misconfigured", self.id, technical=redact(str(exc), secret), name=self.display_name)
        err = translate(exc, {"provider": self.id, "source": self.id, "tool": "http"})
        if self.local and (is_network_error(exc) or is_timeout_error(exc)):
            err = _err("local_down", self.id, technical=redact(err.technical, secret), retryable=True,
                       severity=Severity.WARNING, name=self.display_name)
        elif err.technical:
            err = SmartError(err.code, err.title, err.summary, cause=err.cause, steps=err.steps, actions=err.actions,
                             severity=err.severity, technical=redact(err.technical, secret),
                             retryable=err.retryable, source=self.id)
        return err


class ModuleProvider(BaseProvider):
    """Yerel kütüphane tabanlı sağlayıcı (örn. faster-whisper): ağ yok; "bağlantı" = kütüphane kurulu mu."""

    def __init__(self, id: str, display_name: str, kind: ProviderKind, module: str, **kw: Any) -> None:
        super().__init__(id, display_name, kind, needs_key=False, local=True, **kw)
        self.module = module

    def test_connection(self, vault: Optional[VaultAPI] = None, timeout: float = 5.0) -> PingResult:
        try:
            ok = importlib.util.find_spec(self.module) is not None
        except Exception:
            ok = False
        if ok:
            return PingResult(True, 0.0, t("provider.module_ok", name=self.display_name))
        err = _err("module_missing", self.id, name=self.display_name, module=self.module)
        return PingResult(False, None, err.summary, err)


# ---------------------------------------------------------------------------
# Kayıt tablosu + fallback
# ---------------------------------------------------------------------------
class ProviderRegistry:
    """``ProviderRegistryAPI`` uygulaması. İş parçacığı güvenlidir."""

    JOB_KIND = "ai.deferred"

    def __init__(self, settings: Any = None, jobs: Any = None, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, max_rate_retries: int = 2) -> None:
        self._providers: Dict[str, Provider] = {}
        self._cool: Dict[str, float] = {}
        self._lock = threading.RLock()
        self.settings, self.jobs = settings, jobs
        self.sleep, self.clock, self.max_rate_retries = sleep, clock, max_rate_retries
        self.on_ping: Optional[Callable[[str, Optional[float]], None]] = None

    # -- kayıt ----------------------------------------------------------------
    def register(self, provider: Provider, *, replace: bool = False) -> None:
        with self._lock:
            if provider.id in self._providers and not replace:
                raise ValueError(f"sağlayıcı zaten kayıtlı: {provider.id}")
            self._providers[provider.id] = provider

    def get(self, provider_id: str) -> Optional[Provider]:
        return self._providers.get(provider_id)

    def list(self, kind: Optional[ProviderKind] = None) -> List[Provider]:
        with self._lock:
            return [p for p in self._providers.values() if kind is None or p.kind is kind]

    def configured(self, vault: Optional[VaultAPI] = None, kind: Optional[ProviderKind] = None) -> List[Provider]:
        out = []
        for p in self.list(kind):
            try:
                if (not p.needs_key) or (vault is not None and p.is_configured(vault)):
                    out.append(p)
            except Exception:
                continue
        return out

    # -- bağlantı testi ---------------------------------------------------------------
    def test_connection(self, provider_id: str, vault: Optional[VaultAPI] = None, timeout: float = 5.0) -> PingResult:
        p = self.get(provider_id)
        if p is None:
            err = _err("unknown", provider_id, id=provider_id)
            return PingResult(False, None, err.summary, err)
        try:
            result = p.ping(vault, timeout)             # type: ignore[arg-type]
        except Exception as exc:                 # kötü yazılmış sağlayıcı uygulamayı düşürmesin
            err = translate(exc, {"provider": provider_id, "source": provider_id})
            result = PingResult(False, None, err.summary, err)
        if self.on_ping is not None:
            try:
                self.on_ping(provider_id, result.latency_ms if result.ok else None)
            except Exception:
                pass
        return result

    # -- geri çekilme (soğuma) -------------------------------------------------------------
    def cool_down(self, provider_id: str, seconds: float) -> None:
        with self._lock:
            self._cool[provider_id] = self.clock() + max(0.0, seconds)

    def cooling_remaining(self, provider_id: str) -> float:
        with self._lock:
            return max(0.0, self._cool.get(provider_id, 0.0) - self.clock())

    def is_cooling(self, provider_id: str) -> bool:
        return self.cooling_remaining(provider_id) > 0

    # -- zincir ------------------------------------------------------------------------------
    def _order_setting(self, kind: ProviderKind) -> List[str]:
        if self.settings is None:
            return []
        try:
            order = self.settings.get("fallback_order") or {}
            lst = order.get(kind.value) if isinstance(order, dict) else None
            return [str(x) for x in lst] if isinstance(lst, list) else []
        except Exception:
            return []

    def chain(self, kind: ProviderKind, vault: Optional[VaultAPI] = None, *, prefer: Optional[str] = None,
              accept: Optional[Callable[[Provider], bool]] = None) -> List[Provider]:
        """Kullanılabilir sağlayıcılar, deneme sırasıyla: ayarlanan sıra, yoksa kayıt sırasıyla **bulut → yerel**.
        Anahtarı olmayan bulutlar ve soğumadakiler çıkarılır. ``prefer`` bir sağlayıcıyı başa alır; ``accept`` süzer
        (ör. yalnız görsel okuyabilenler) — ikisi de Adım 8'de eklendi (geriye uyumlu)."""
        candidates = [p for p in self.configured(vault, kind) if accept is None or accept(p)]
        by_id = {p.id: p for p in candidates}
        ordered: List[Provider] = []
        for pid in self._order_setting(kind):
            if pid in by_id:
                ordered.append(by_id.pop(pid))
        rest = list(by_id.values())
        ordered += [p for p in rest if not p.local] + [p for p in rest if p.local]
        out = [p for p in ordered if not self.is_cooling(p.id)]
        if prefer:
            out.sort(key=lambda p: p.id != prefer)            # kararlı sıralama: tercih edilen başa, gerisi aynı sırada
        return out

    def call_with_fallback(self, kind: ProviderKind, operation: Callable[[Provider], Any], *,
                           vault: Optional[VaultAPI] = None, queue_payload: Optional[Mapping[str, Any]] = None,
                           queue_title: str = "", prefer: Optional[str] = None,
                           accept: Optional[Callable[[Provider], bool]] = None) -> FallbackResult:
        """``operation(provider)``'ı zincirde dener (bulut → yerel → kuyruğa al).

        * Çevrimdışı hatası alınırsa kalan **bulut** sağlayıcılar çağrılmadan atlanır, yerel olanlara geçilir.
        * 429: ``Retry-After``/üstel bekleme ile aynı sağlayıcıda ``max_rate_retries`` kez yeniden denenir; hâlâ
          olmazsa sağlayıcı soğumaya alınır ve sıradakine geçilir.
        * Hepsi başarısızsa ve ``queue_payload`` verildiyse iş ``ai.deferred`` olarak kuyruğa alınır.
        Asla istisna atmaz.
        """
        attempts: List[Attempt] = []
        last_error: Optional[SmartError] = None
        offline = False
        chain = self.chain(kind, vault, prefer=prefer, accept=accept)
        for provider in chain:
            if offline and not provider.local:
                attempts.append(Attempt(provider.id, None, skipped=True))
                continue
            retries = 0
            while True:
                try:
                    value = operation(provider)
                    attempts.append(Attempt(provider.id, None, retries=retries))
                    return FallbackResult(True, provider.id, value, attempts)
                except Exception as exc:
                    status = http_status(exc)
                    err = translate(exc, {"provider": provider.id, "source": provider.id, "status": status})
                    last_error = err
                    if status == 429:
                        ra = retry_after_of(exc)
                        if retries < self.max_rate_retries:
                            self.sleep(backoff_delay(retries, ra))
                            retries += 1
                            continue
                        self.cool_down(provider.id, backoff_delay(retries, ra, cap=120.0))
                    if is_network_error(exc):                 # sözlük kod adını değiştirse de çevrimdışı sayılır
                        offline = True
                    attempts.append(Attempt(provider.id, err, retries=retries))
                    break
        if not chain:
            last_error = _err("none_available", "providers", kind=kind.value)
        job = None
        if queue_payload is not None and self.jobs is not None:
            try:
                job = self.jobs.submit(self.JOB_KIND, {"kind": kind.value, "payload": dict(queue_payload),
                                                       "reason": last_error.code if last_error else ""},
                                       title=queue_title or t("provider.deferred_title"))
            except Exception as exc:
                last_error = translate(exc, {"source": "providers"})
        return FallbackResult(False, "", None, attempts, last_error, job)


# ---------------------------------------------------------------------------
# Varsayılan kayıt (ping profilleri)
# ---------------------------------------------------------------------------
_K = ProviderKind
#: (id, ad, tür, taban adres, ping yolu, kimlik doğrulama, ek başlıklar, ping parametreleri, anahtarlı mı, yerel mi)
_PROFILES: List[tuple] = [
    ("anthropic", "Anthropic (Claude)", _K.LLM, "https://api.anthropic.com", "/v1/models", "header:x-api-key",
     {"anthropic-version": "2023-06-01"}, {}, True, False),
    ("openai_compat", "OpenAI uyumlu", _K.LLM, "https://api.openai.com/v1", "/models", "bearer", {}, {}, True, False),
    # BFL / Imagen uç noktaları en iyi tahmindir; Adım 8 gerçek sağlayıcıyla doğrulayıp değiştirir.
    ("bfl_flux", "FLUX (Black Forest Labs)", _K.IMAGE, "https://api.bfl.ai", "/v1/credits", "header:x-key", {}, {},
     True, False),
    ("google_imagen", "Google Imagen", _K.IMAGE, "https://generativelanguage.googleapis.com", "/v1beta/models",
     "header:x-goog-api-key", {}, {}, True, False),
    ("elevenlabs", "ElevenLabs", _K.TTS, "https://api.elevenlabs.io", "/v1/user", "header:xi-api-key", {}, {}, True,
     False),
    ("pexels", "Pexels", _K.STOCK, "https://api.pexels.com", "/v1/curated", "header:Authorization", {},
     {"per_page": "1"}, True, False),
    ("pixabay", "Pixabay", _K.STOCK, "https://pixabay.com", "/api/", "query:key", {}, {"per_page": "3"}, True, False),
    ("freesound", "Freesound", _K.STOCK, "https://freesound.org", "/apiv2/search/text/", "token", {},
     {"query": "test", "page_size": "1"}, True, False),
    ("ollama", "Ollama (yerel)", _K.LLM, os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"), "/api/tags", "none",
     {}, {}, False, True),
    ("comfyui", "ComfyUI (yerel)", _K.IMAGE, "http://127.0.0.1:8188", "/system_stats", "none", {}, {}, False, True),
]


def default_registry(settings: Any = None, jobs: Any = None, transport: Any = None) -> ProviderRegistry:
    """Bilinen servislerin ping profilleriyle dolu kayıt tablosu (Adım 8 gerçek sağlayıcılarla değiştirir)."""
    reg = ProviderRegistry(settings, jobs)
    for pid, name, kind, base, path, auth, hdrs, params, needs_key, local in _PROFILES:
        url = base if base.startswith("http") else f"http://{base}"
        reg.register(BaseProvider(pid, name, kind, needs_key=needs_key, local=local, base_url=url, ping_path=path,
                                  auth=auth, extra_headers=hdrs, ping_params=params, settings=settings,
                                  transport=transport))
    reg.register(ModuleProvider("whisper_local", "Whisper (yerel)", _K.STT, "faster_whisper"))
    return reg
