"""Telifsiz stok arama ortak katmanı: ``StockItem`` biçimi ve ``StockProvider`` (``BaseProvider``'dan türer).

Arama zaman aşımlıdır; anahtar kasadan (``vault.get_secret``) okunur. Kasa kilitliyse kasanın
``SmartError``'ı, anahtar yoksa ``provider.no_key``, ağ yoksa ``net.offline`` yükselir; uygulama çökmez.
Ham anahtar hiçbir hata ayrıntısında görünmez (``redact``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ...core.contracts import ProviderKind, SmartError, Severity
from .base import BaseProvider, _err, parse_retry_after, redact


@dataclass(frozen=True)
class StockItem:
    """Tüm stok sağlayıcılarının ortak sonuç biçimi."""
    provider: str
    kind: str                         # "image" | "audio"
    id: str
    title: str
    page_url: str
    preview_url: str = ""
    download_url: str = ""            # bazı sağlayıcılarda yalnız önizleme ya da OAuth gerekir
    author: str = ""
    license: str = ""
    duration: Optional[float] = None  # ses için saniye
    width: Optional[int] = None
    height: Optional[int] = None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class StockProvider(BaseProvider):
    """Arama uçlu telifsiz stok sağlayıcısı. Alt sınıf ``search_params`` ve ``parse`` sağlar."""

    def __init__(self, id: str, display_name: str, *, base_url: str, search_path: str, auth: str,
                 extra_headers: Optional[Mapping[str, str]] = None, ping_params: Optional[Mapping[str, str]] = None,
                 settings: Any = None, transport: Any = None) -> None:
        super().__init__(id, display_name, ProviderKind.STOCK, needs_key=True, local=False, base_url=base_url,
                         ping_path=search_path, auth=auth, extra_headers=extra_headers, ping_params=ping_params,
                         settings=settings, transport=transport)
        self.search_path = search_path

    # -- alt sınıf sözleşmesi -----------------------------------------------------------
    def search_params(self, query: str, limit: int, **filters: Any) -> Dict[str, str]:
        raise NotImplementedError

    def parse(self, payload: Mapping[str, Any]) -> List[StockItem]:
        raise NotImplementedError

    # -- arama -------------------------------------------------------------------------
    def search(self, query: str, *, vault: Any = None, timeout: float = 8.0, limit: int = 10,
               **filters: Any) -> List[StockItem]:
        """Arar ve en çok ``limit`` sonuç döner. Hata: ``SmartError``. Boş sorgu: ``ValueError``."""
        text = " ".join(str(query or "").split())
        if not text:
            raise ValueError("Aranacak bir kelime yaz.")
        count = max(1, min(int(limit), 80))
        secret = self._secret(vault)
        url, headers, base_params = self.build_request(secret)
        params = {**base_params, **self.search_params(text, count, **filters)}
        status, resp_headers, body = self._get(url, headers, params, timeout, secret)
        if status >= 400:
            raise self._status_error(status, resp_headers, secret)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise self._translate(exc, secret) from None
        if not isinstance(payload, Mapping):
            raise _err("http", self.id, technical=redact("beklenmeyen yanıt biçimi", secret), name=self.display_name)
        return self.parse(payload)[:count]

    # -- yardımcılar -------------------------------------------------------------------
    def _secret(self, vault: Any) -> str:
        if vault is None:
            raise self._no_key()
        try:
            secret = vault.get_secret(self.id)
        except SmartError:
            raise                                       # kasa kilitli/bozuk: kasanın kartı
        except Exception:  # noqa: BLE001 — okuma hatası anahtar yokmuş gibi davranır
            secret = None
        if not secret:
            raise self._no_key()
        return str(secret)

    def _no_key(self) -> SmartError:
        return _err("no_key", self.id, name=self.display_name, actions=self._key_actions())

    def _get(self, url: str, headers: Mapping[str, str], params: Mapping[str, str], timeout: float,
             secret: Optional[str]) -> Tuple[int, Mapping[str, str], str]:
        import httpx                                   # tembel içe aktarma
        tmo = httpx.Timeout(timeout, connect=min(timeout, 3.0))
        try:
            with httpx.Client(transport=self.transport, timeout=tmo, follow_redirects=False) as client:
                resp = client.get(url, headers=dict(headers), params=dict(params))
        except Exception as exc:  # noqa: BLE001 — ağ, zaman aşımı, TLS: sade karta çevrilir
            raise self._translate(exc, secret) from None
        return resp.status_code, resp.headers, resp.text

    def _status_error(self, status: int, headers: Mapping[str, str], secret: Optional[str]) -> SmartError:
        tech = redact(f"HTTP {status}", secret)
        name = self.display_name
        if status in (401, 403):
            return _err("auth", self.id, technical=tech, name=name, actions=self._key_actions())
        if status == 404:
            return _err("not_found", self.id, technical=tech, name=name)
        if status == 429:
            retry = parse_retry_after(headers.get("Retry-After"))
            extra = f" retry-after={retry:.0f}s" if retry else ""
            return _err("rate_limited", self.id, technical=tech + extra, name=name, retryable=True,
                        severity=Severity.WARNING)
        if status >= 500:
            return _err("server", self.id, technical=tech, name=name, retryable=True, severity=Severity.WARNING)
        return _err("http", self.id, technical=tech, name=name, status=status)


def install(registry: Any, *, settings: Any = None, transport: Any = None) -> List[StockProvider]:
    """Pexels, Pixabay ve Freesound'u kayıt defterine (``replace=True``) ekler."""
    from .freesound import FreesoundProvider
    from .pexels import PexelsProvider
    from .pixabay import PixabayProvider
    providers: List[StockProvider] = [PexelsProvider(settings=settings, transport=transport),
                                      PixabayProvider(settings=settings, transport=transport),
                                      FreesoundProvider(settings=settings, transport=transport)]
    for provider in providers:
        registry.register(provider, replace=True)
    return providers
