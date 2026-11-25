"""Ayarlar › API anahtarları: "Bağlantıyı test et" sonucu sade Türkçe ve adımlarla; OpenAI görsel anahtarı OpenAI ile tek satır."""
from __future__ import annotations

import httpx

from kadraj.ai_engine import aierrors
from kadraj.core.contracts import PingResult, Topics
from kadraj.modules.settings.page_legend import SettingsLegendPage
from kadraj.modules.settings.page_simple import SettingsSimplePage
from kadraj.ui.widgets.smart_error_card import SmartErrorCard
from kadraj.ui.widgets.squishy_button import SquishyButton

from .fakes import FULL_KEY


def test_openai_image_shares_the_openai_row_and_key(ctx, svc):
    ids = [r.provider_id for r in svc.key_rows()]
    assert "openai_compat" in ids and "openai_image" not in ids            # kasada tek kayıt: aynı anahtar hem yazı hem görsel
    assert [p.id for p in svc.key_providers() if p.id == "openai_image"] == []
    svc.save_key("openai_compat", "sk-oa-" + "x" * 20)
    assert ctx.providers.get("openai_image").is_configured(ctx.vault)       # görsel sağlayıcı aynı anahtarı görür


def test_test_button_calls_the_real_cheap_list_and_explains_in_plain_turkish(ctx, svc, qtbot):
    seen = []

    def route(r):
        seen.append((r.method, r.url.path, r.url.params.get("limit")))
        return httpx.Response(200, json={"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-5-5"}]})
    ctx.providers.get("anthropic").transport = httpx.MockTransport(route)
    svc.ping_fn = None                                                       # gerçek sağlayıcı testi (sahte HTTP üzerinden)
    svc.test_connection("anthropic", background=False)
    assert seen == [("GET", "/v1/models", "100")]                            # tek, ucuz GET (model listesi)
    res = svc.test_results()["anthropic"]
    assert res.ok and "2 model görünüyor" in res.message and FULL_KEY not in res.message
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    page.show()
    assert "bağlantı tamam" in " ".join(w.text() for w in page.key_result.findChildren(type(page.status)))


def test_wrong_key_shows_numbered_steps_and_working_buttons_on_both_pages(ctx, svc, qtbot):
    ctx.providers.get("anthropic").transport = httpx.MockTransport(
        lambda r: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
    svc.ping_fn = None
    svc.test_connection("anthropic", background=False)
    nav = []
    ctx.bus.subscribe(Topics.NAVIGATE, nav.append)
    for page_cls in (SettingsSimplePage, SettingsLegendPage):
        page = page_cls(ctx)
        qtbot.addWidget(page)
        page.resize(1300, 1000)
        page.show()
        if page_cls is SettingsLegendPage:
            page.nav.setCurrentRow(1)                                         # Legend bölümleri ilk seçildiğinde kurulur → "API anahtarları"
        cards = page.key_result.findChildren(SmartErrorCard)
        assert len(cards) == 1 and cards[0].error.code == "ai.auth" and len(cards[0].error.steps) >= 4
        opened = []
        page.key_result.open_url = opened.append
        # kart düğmeleri: anahtar sayfasını aç / Ayarlar / yeniden dene
        btn = next(b for b in cards[0].findChildren(SquishyButton) if b.text() == "Anahtar sayfasını aç")
        btn.click()
        assert opened == [aierrors.info("anthropic").key_url]
    svc.test_connection("anthropic", background=False)                       # "yeniden dene" aynı testi yapar
    assert svc.test_results()["anthropic"].error.code == "ai.auth"
    svc.clear_test_result("anthropic")
    assert svc.test_results() == {}


def test_each_failure_reason_has_its_own_card_in_settings(ctx, svc):
    cases = {
        401: "ai.auth", 402: "ai.quota", 429: "ai.rate_limit", 404: "ai.model_not_found", 503: "ai.server",
    }
    prov = ctx.providers.get("openai_compat")
    svc.ping_fn = None
    for status, code in cases.items():
        body = {"error": {"message": "x", "code": "model_not_found" if status == 404 else ""}}
        prov.transport = httpx.MockTransport(lambda r, s=status, b=body: httpx.Response(s, json=b, headers={"retry-after": "3"} if s == 429 else {}))
        svc.save_key("openai_compat", "sk-oa-" + "k" * 20)
        svc.test_connection("openai_compat", background=False)
        err = svc.test_results()["openai_compat"].error
        # test GET'inde model adı yok: 404 yalnız "http" olur (liste adresi) — diğerleri kendi kartlarıyla
        assert err.code == (code if status != 404 else "ai.http"), (status, err.code)
        assert len(err.steps) >= 3
    def boom(request):
        raise httpx.ConnectError("x")
    prov.transport = httpx.MockTransport(boom)
    svc.test_connection("openai_compat", background=False)
    assert svc.test_results()["openai_compat"].error.code == "ai.offline"
