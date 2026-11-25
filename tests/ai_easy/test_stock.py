"""Stok sağlayıcıları: Pexels, Pixabay, Freesound. Sahte HTTP (MockTransport); ağ yok."""
from __future__ import annotations

import httpx
import pytest

from kadraj.ai_engine.providers.base import ProviderRegistry
from kadraj.ai_engine.providers.freesound import FreesoundProvider
from kadraj.ai_engine.providers.pexels import PexelsProvider
from kadraj.ai_engine.providers.pixabay import PixabayProvider
from kadraj.ai_engine.providers.stock import StockItem, install
from kadraj.core.contracts import ProviderKind, SmartError

SECRET = "SECRETKEY-abcdefghijklmnop-1234"


class Vault:
    def __init__(self, secret=SECRET, *, locked=False):
        self.secret = secret
        self.locked = locked

    def get_secret(self, provider_id):
        if self.locked:
            raise SmartError("vault.locked", "Kasa kilitli", "kilitli")
        return self.secret


def _transport(payload=None, status=200, *, raises=None, seen=None, headers=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if raises is not None:
            raise raises
        return httpx.Response(status, json=payload if payload is not None else {}, headers=headers or {})
    return httpx.MockTransport(handler)


PEXELS_PAYLOAD = {"photos": [{"id": 7, "alt": "Deniz kenarı", "url": "https://www.pexels.com/p/7",
                              "photographer": "Ayla", "width": 4000, "height": 2000,
                              "src": {"original": "https://img/o.jpg", "medium": "https://img/m.jpg"}}]}
PIXABAY_PAYLOAD = {"hits": [{"id": 9, "tags": "orman, sis", "pageURL": "https://pixabay.com/9", "user": "Can",
                             "imageWidth": 1920, "imageHeight": 1080, "webformatURL": "https://img/w.jpg",
                             "largeImageURL": "https://img/l.jpg"}]}
FREESOUND_PAYLOAD = {"results": [{"id": 3, "name": "Yağmur", "username": "Deniz", "duration": 12.5,
                                  "license": "Creative Commons 0", "url": "https://freesound.org/s/3/",
                                  "previews": {"preview-hq-mp3": "https://fs/hq.mp3",
                                               "preview-lq-mp3": "https://fs/lq.mp3"}}]}


def test_pexels_search_parses_into_stock_items_and_sends_key_in_header():
    seen = []
    provider = PexelsProvider(transport=_transport(PEXELS_PAYLOAD, seen=seen))
    items = provider.search("deniz", vault=Vault(), limit=5)
    assert items == [StockItem(provider="pexels", kind="image", id="7", title="Deniz kenarı",
                               page_url="https://www.pexels.com/p/7", preview_url="https://img/m.jpg",
                               download_url="https://img/o.jpg", author="Ayla",
                               license="Pexels lisansı (telifsiz)", width=4000, height=2000)]
    request = seen[0]
    assert request.headers["Authorization"] == SECRET
    assert request.url.params["query"] == "deniz" and request.url.params["per_page"] == "5"


def test_pixabay_search_sends_key_as_query_and_clamps_per_page():
    seen = []
    provider = PixabayProvider(transport=_transport(PIXABAY_PAYLOAD, seen=seen))
    items = provider.search("orman", vault=Vault(), limit=1)
    assert items[0].title == "orman, sis" and items[0].download_url == "https://img/l.jpg"
    params = seen[0].url.params
    assert params["key"] == SECRET and params["per_page"] == "3"       # Pixabay en az 3 ister
    assert params["safesearch"] == "true"


def test_freesound_returns_audio_with_preview_and_duration():
    seen = []
    provider = FreesoundProvider(transport=_transport(FREESOUND_PAYLOAD, seen=seen))
    items = provider.search("yağmur", vault=Vault(), limit=2)
    assert items[0].kind == "audio" and items[0].duration == 12.5
    assert items[0].preview_url == "https://fs/hq.mp3" and items[0].download_url == ""
    assert seen[0].headers["Authorization"] == f"Token {SECRET}"


def test_results_are_capped_at_limit():
    many = {"photos": [{"id": i, "src": {}} for i in range(9)]}
    items = PexelsProvider(transport=_transport(many)).search("x", vault=Vault(), limit=4)
    assert len(items) == 4


def test_missing_key_is_a_smart_error_without_network():
    seen = []
    provider = PexelsProvider(transport=_transport(PEXELS_PAYLOAD, seen=seen))
    with pytest.raises(SmartError) as exc:
        provider.search("deniz", vault=Vault(secret=""))
    assert exc.value.code == "provider.no_key" and seen == []


def test_locked_vault_error_is_passed_through():
    provider = PexelsProvider(transport=_transport(PEXELS_PAYLOAD))
    with pytest.raises(SmartError) as exc:
        provider.search("deniz", vault=Vault(locked=True))
    assert exc.value.code == "vault.locked"


def test_offline_becomes_smart_error_and_key_is_redacted():
    provider = PixabayProvider(transport=_transport(raises=httpx.ConnectError(f"no route {SECRET}")))
    with pytest.raises(SmartError) as exc:
        provider.search("orman", vault=Vault())
    assert exc.value.code.startswith("net.")
    assert SECRET not in (exc.value.technical or "") and SECRET not in str(exc.value)


def test_timeout_becomes_smart_error():
    provider = FreesoundProvider(transport=_transport(raises=httpx.ReadTimeout("zaman aşımı")))
    with pytest.raises(SmartError) as exc:
        provider.search("yağmur", vault=Vault(), timeout=1.0)
    assert exc.value.code.startswith("timeout") or "timeout" in exc.value.code or exc.value.code.startswith("net.")


@pytest.mark.parametrize("status, code", [(401, "provider.auth"), (404, "provider.not_found"),
                                          (429, "provider.rate_limited"), (503, "provider.server"),
                                          (418, "provider.http")])
def test_http_status_maps_to_provider_cards(status, code):
    provider = PexelsProvider(transport=_transport({}, status=status))
    with pytest.raises(SmartError) as exc:
        provider.search("deniz", vault=Vault())
    assert exc.value.code == code


def test_empty_query_is_refused_before_any_request():
    seen = []
    with pytest.raises(ValueError):
        PexelsProvider(transport=_transport({}, seen=seen)).search("   ", vault=Vault())
    assert seen == []


def test_install_replaces_the_ping_profiles_in_the_registry():
    registry = ProviderRegistry()
    providers = install(registry, transport=_transport({}))
    assert {p.id for p in providers} == {"pexels", "pixabay", "freesound"}
    assert isinstance(registry.get("pexels"), PexelsProvider)
    assert all(p.kind is ProviderKind.STOCK for p in providers)
