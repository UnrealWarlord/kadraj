"""Durum çubuğu ("Bağlı / Anahtar yok / Yerel servis kapalı"), kurulum kartı ("Kaydet ve dene"), hata kartı düğmeleri.

Taze kurulum (anahtar yok, Ollama yok, ComfyUI yok) ve anahtar girilmiş akışlar; hepsi sahte HTTP + bellek-içi kasa.
"""
from __future__ import annotations

import httpx
import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from kadraj.ai_engine.access import AIAccess
from kadraj.ai_engine.providers.base import default_registry
from kadraj.ai_engine.providers.install import install_all
from kadraj.core.contracts import SmartError, Topics
from kadraj.ui.widgets.smart_error_card import SmartErrorCard

from .conftest import FakeSettings, Recorder

KEY = "bfl-NEW-KEY-9999"


class WVault:
    """Yazılabilir sahte kasa (``set_secret`` / ``delete_secret``)."""

    def __init__(self, **secrets: str) -> None:
        self.secrets = dict(secrets)
        self.fail = None

    def get_secret(self, pid): return self.secrets.get(pid)
    def has_secret(self, pid): return bool(self.secrets.get(pid))

    def set_secret(self, pid, value):
        if self.fail:
            raise self.fail
        self.secrets[pid] = value

    def delete_secret(self, pid): return bool(self.secrets.pop(pid, None))


def make_access(router=None, vault=None, settings=None):
    rec = Recorder(router or (lambda r: httpx.Response(200, json={})))
    settings = settings if settings is not None else FakeSettings()
    reg = default_registry(settings)
    install_all(reg, settings=settings, transport=rec.transport, sleep=lambda s: None)
    return AIAccess(reg, vault if vault is not None else WVault(), settings), rec


# ===================================================================== durum
def test_status_fresh_install_then_key_then_local_states():
    acc, _ = make_access()
    t, i = acc.status("text"), acc.status("image")
    assert t.state == "no_key" and i.state == "no_key" and not t.ready
    assert "Anahtar yok" in t.label and t.label.startswith("Yazı yapay zekâsı") and i.label.startswith("Görsel yapay zekâsı")
    acc.vault.secrets["anthropic"] = "sk-ant-x"
    st = acc.status("text")
    assert st.ready and st.provider_id == "anthropic" and st.label == "Yazı yapay zekâsı: Anthropic (Claude) · Bağlı"
    # yerel servisler: kapalı + hiç kullanılmamış → "Anahtar yok"; kullanıcı yerel yolu seçmişse → "Yerel servis kapalı"
    acc.set_local_state({"comfyui": "down", "ollama": "down"})
    assert acc.status("image").state == "no_key" and acc.local_up_map() == {"comfyui": False, "ollama": False}
    acc.settings.set("ai.local_seen.image", True)
    st = acc.status("image")
    assert st.state == "local_down" and st.label.endswith("Yerel servis kapalı") and st.provider_id == "comfyui"
    acc.set_local_state({"comfyui": "no_model"})
    assert acc.status("image").state == "no_model" and acc.local_up_map()["comfyui"] is True
    acc.set_local_state({"comfyui": "up"})
    st = acc.status("image")
    assert st.ready and st.local and st.label.endswith("Bağlı") and acc.settings.get("ai.local_seen.image") is True


def test_probe_local_tells_closed_from_empty():
    def router(r):
        if r.url.port == 11434 and r.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})                    # Ollama açık ama modelsiz
        raise httpx.ConnectError("refused")                                    # ComfyUI kapalı
    acc, _ = make_access(router)
    assert acc.probe_local() == {"ollama": "no_model", "comfyui": "down"}


# ===================================================================== kaydet + dene
def test_save_and_test_uses_shared_key_id_never_leaks_and_rolls_back_wrong_keys():
    acc, rec = make_access(lambda r: httpx.Response(200, json={"credits": 3.0}) if r.url.host == "api.bfl.ai" else httpx.Response(200, json={"data": [{"id": "m"}]}))
    res = acc.save_and_test("bfl_flux", f"  {KEY} ")
    assert res.ok and acc.vault.secrets["bfl_flux"] == KEY and "3" in res.message and KEY not in res.message          # boşluklar kırpılır
    assert acc.status("image").ready and acc.status("image").provider_id == "bfl_flux"
    # OpenAI görsel anahtarı OpenAI ile paylaşılır → kasada tek kayıt
    res = acc.save_and_test("openai_image", "sk-oa-NEW-0001")
    assert res.ok and acc.vault.secrets["openai_compat"] == "sk-oa-NEW-0001" and "openai_image" not in acc.vault.secrets
    # boş anahtar
    assert not acc.save_and_test("bfl_flux", "   ").ok
    # yanlış anahtar: kart gelir, kasaya yanlış anahtar bırakılmaz (önceki çalışan anahtar geri döner)
    acc.registry.get("bfl_flux").transport = Recorder(lambda r: httpx.Response(403 if False else 401, json={"detail": "bad key"})).transport
    res = acc.save_and_test("bfl_flux", "bfl-WRONG-0000")
    assert not res.ok and res.error.code == "ai.auth" and len(res.error.steps) >= 4 and acc.vault.secrets["bfl_flux"] == KEY
    fresh, _ = make_access(lambda r: httpx.Response(401, json={}))
    assert not fresh.save_and_test("anthropic", "sk-ant-WRONG").ok and not fresh.vault.has_secret("anthropic") and not fresh.status("text").ready
    # bakiye yok: anahtar doğru → kayıtlı kalır, kart ödeme sayfasını açar
    nocred, _ = make_access(lambda r: httpx.Response(402, json={"detail": "x"}))
    res = nocred.save_and_test("bfl_flux", KEY)
    assert not res.ok and res.error.code == "ai.quota" and nocred.vault.has_secret("bfl_flux")
    # kasa kilitli/kurulamıyor → "Ayarlar'ı aç" düğmeli kart
    locked, _ = make_access()
    locked.vault.fail = SmartError("vault.locked", "Kasa kilitli", "Parola gerekli")
    res = locked.save_and_test("bfl_flux", KEY)
    assert not res.ok and any(a.kind == "open_settings" for a in res.error.actions)


# ===================================================================== sayfalar
@pytest.fixture()
def photo(ctx, tmp_path, qtbot, world):
    ctx.project.create(tmp_path / "projeler", "Foto Testi")
    svc = ctx.service("photo")
    svc._pool.waitForDone(3000)
    qtbot.wait(30)
    install_all(ctx.providers, settings=ctx.settings, transport=world.rec.transport, sleep=lambda s: None)
    world.down.update({"127.0.0.1"})                                        # yerel servisler kapalı
    svc.refresh_local()
    svc._pool.waitForDone(3000)
    qtbot.wait(50)
    return svc


def texts(w):
    return " ".join(lb.text() for lb in w.findChildren(QLabel))


def button(w, text):
    return next(b for b in w.findChildren(QPushButton) if b.text() == text) if any(b.text() == text for b in w.findChildren(QPushButton)) else None


def squishy(w, text):
    from kadraj.ui.widgets.squishy_button import SquishyButton
    return next(b for b in w.findChildren(SquishyButton) if b.text() == text)


@pytest.mark.parametrize("mode", ["simple", "legend"])
def test_photo_pages_show_status_and_setup_card_on_fresh_install(ctx, photo, qtbot, mode):
    ctx.set_ui_mode(mode)
    page = getattr(ctx.registry.get("photo"), f"{mode}_page")(ctx)
    qtbot.addWidget(page)
    page.resize(1200, 1000)
    page.show()
    panel = page.ai_panel
    assert panel.rows["image"].text.text().startswith("Görsel yapay zekâsı") and "Anahtar yok" in panel.rows["image"].text.text()
    assert "(isteğe bağlı)" in panel.rows["text"].text.text()
    card = panel.cards["image"]
    assert card.isVisible() and not panel.cards["text"].isVisible()                  # asıl servis hazır değil → kart kendiliğinden açık
    body = texts(card)
    assert "1." in body and "2." in body and "3." in body and "https://dashboard.bfl.ai" in body                 # 1-2-3 + resmî anahtar sayfası
    assert "ollama" not in body.lower() or "ComfyUI" in body                                                     # görselde yerel yol: ComfyUI
    assert "https://www.comfy.org/download" in body and card.save_btn.text() == "Kaydet ve dene"
    assert [card.combo.itemText(i) for i in range(card.combo.count())] == ["FLUX (Black Forest Labs)", "Google Gemini görsel", "OpenAI görsel"]
    assert card.key_edit.echoMode().name == "Password"                                                           # anahtar ekranda gizli


def test_setup_card_saves_key_tests_and_turns_status_green(ctx, photo, world, qtbot):
    page = ctx.registry.get("photo").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1200, 1000)
    page.show()
    opened = []
    page.open_url = opened.append
    card = page.ai_panel.cards["image"]
    card.open_key.click()
    assert opened == ["https://dashboard.bfl.ai"]
    card.key_edit.setText(KEY)
    card.save_btn.click()
    assert card.key_edit.text() == "" and not card.save_btn.isEnabled()                                          # düz anahtar kutuda bekletilmez
    qtbot.waitUntil(lambda: "Bağlı" in page.ai_panel.rows["image"].text.text() and "Anahtar yok" not in page.ai_panel.rows["image"].text.text(), timeout=8000)
    assert ctx.vault.get_secret("bfl_flux") == KEY and "Tamam" in card.result.text() and not card.isVisible()
    everything = texts(page) + " ".join(b.text() for b in page.findChildren(QPushButton))
    assert KEY not in everything and "bfl-NEW" not in everything                                                  # anahtar hiçbir yerde görünmez
    assert all(KEY not in str(r.url) for r in world.rec.requests)                                                # URL'ye girmez
    assert page.ai_panel.rows["image"].help_btn.isHidden()


def test_setup_card_wrong_key_shows_numbered_steps_and_open_key_page(ctx, photo, world, qtbot):
    page = ctx.registry.get("photo").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1200, 1000)
    page.show()
    opened = []
    page.open_url = opened.append
    world.rec.queue.append(httpx.Response(401, json={"detail": "Not authenticated"}))
    card = page.ai_panel.cards["image"]
    card.key_edit.setText("bfl-WRONG-0001")
    card.save_btn.click()
    qtbot.waitUntil(lambda: card._card is not None, timeout=8000)
    err = card._card.error
    assert err.code == "ai.auth" and len(err.steps) >= 4 and not ctx.vault.has_secret("bfl_flux")
    assert "Anahtar yok" in page.ai_panel.rows["image"].text.text()                                              # yanlış anahtar "Bağlı" yapmaz
    btn = squishy(card._card, "Anahtar sayfasını aç")
    btn.click()
    assert opened[-1] == "https://dashboard.bfl.ai"
    assert "bfl-WRONG" not in texts(card._card) and "bfl-WRONG" not in err.technical


def test_error_card_buttons_work_open_billing_settings_and_retry(ctx, photo, world, qtbot):
    ctx.set_ui_mode("simple")
    ctx.vault.set_secret("bfl_flux", "bfl-OLD-0002")
    photo.aiChanged.emit()
    page = ctx.registry.get("photo").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1200, 1000)
    page.show()
    opened, nav = [], []
    page.open_url = opened.append
    ctx.bus.subscribe(Topics.NAVIGATE, nav.append)
    state = {"fail": True}
    real = world.route
    world.rec.routes = lambda r: (httpx.Response(402, json={"detail": "Insufficient credits"}) if r.url.host == "api.bfl.ai" and state["fail"] else real(r))
    page.idea.setPlainText("sisli bir vapur")
    page.go_btn.click()
    for _ in range(6):                                                                                             # yazı yapay zekâsı yok → eksik alanlar sorulur
        if photo.ready():
            break
        last = next(t for t in reversed(photo.chat) if t.role == "assistant" and t.slot)
        photo.answer({"lighting": "sabah ışığı", "style": "sinematik", "aspect": "16:9"}.get(last.slot, "x"))
        qtbot.wait(10)
    qtbot.waitUntil(lambda: page.error_visible, timeout=8000)
    card = page._error_card
    assert isinstance(card, SmartErrorCard) and card.error.code == "ai.quota" and len(card.error.steps) >= 4
    squishy(card, "Ödeme sayfasını aç").click()
    assert opened == ["https://dashboard.bfl.ai"]
    squishy(card, "Ayarlar'ı aç").click()
    assert nav and nav[-1]["module"] == "settings"
    before = len(world.rec.requests)
    state["fail"] = False
    squishy(card, "Yeniden dene").click()                                                                          # son işi aynen yeniden çalıştırır
    qtbot.waitUntil(lambda: len(page.gallery.cards) >= 1, timeout=10000)
    assert len(world.rec.requests) > before and not page.error_visible


def test_llm_helper_failure_is_optional_idea_still_proceeds(ctx, photo, world, qtbot):
    """Yazı yardımcısı (isteğe bağlı) hata verirse fikir olduğu gibi konu olur; soru-cevap yine ilerler, kart nedenini söyler."""
    ctx.vault.set_secret("anthropic", "sk-ant-x")
    photo.aiChanged.emit()
    world.rec.routes = lambda r: httpx.Response(402, json={"type": "error", "error": {"type": "billing_error", "message": "x"}}) if r.url.host == "api.anthropic.com" else httpx.Response(404)
    errs = []
    photo.errorRaised.connect(errs.append)
    photo.submit_idea("Sisli İstanbul'da bir vapur")
    qtbot.waitUntil(lambda: not photo.busy and bool(errs), timeout=8000)
    assert errs[-1].code == "ai.quota" and photo.spec.subject == "Sisli İstanbul'da bir vapur" and photo.chat[-1].role == "assistant"


@pytest.fixture()
def series(ctx, tmp_path, qtbot, world):
    ctx.project.create(tmp_path / "projeler", "Dizi Testi")
    s = ctx.service("series")
    s._pool.waitForDone(3000)
    qtbot.wait(30)
    install_all(ctx.providers, settings=ctx.settings, transport=world.rec.transport, sleep=lambda x: None)
    world.down.update({"127.0.0.1"})
    s.refresh_local()
    s._pool.waitForDone(3000)
    qtbot.wait(50)
    return s


@pytest.mark.parametrize("mode", ["simple", "legend"])
def test_series_pages_explain_where_the_ai_key_comes_from(ctx, series, qtbot, mode):
    ctx.set_ui_mode(mode)
    page = getattr(ctx.registry.get("series"), f"{mode}_page")(ctx)
    qtbot.addWidget(page)
    page.resize(1300, 1100)
    page.show()
    panel = page.ai_panel
    assert panel.rows["text"].text.text() == "Yazı yapay zekâsı: seçili servis yok · Anahtar yok" and "(isteğe bağlı)" in panel.rows["image"].text.text()
    card = panel.cards["text"]
    assert card.isVisible()
    body = texts(card)
    assert "https://platform.claude.com/settings/keys" in body or card.url_label.text() == "https://platform.claude.com/settings/keys"
    assert "https://ollama.com/download" in body and "ollama pull gemma4:e2b" in body and "Kaydet ve dene" == card.save_btn.text()
    assert [card.combo.itemText(i) for i in range(card.combo.count())] == ["Claude (Anthropic)", "OpenAI"]
    card.combo.setCurrentIndex(1)
    assert card.url_label.text() == "https://platform.openai.com/settings/organization/api-keys"
    # anahtarsız "fikir üret" → anlaşılır kart (neden + adımlar + Ayarlar düğmesi), uygulama çökmez
    if mode == "simple":
        page.idea_edit.setPlainText("bir vapur gizemi")
        page.brain_btn.click()
        qtbot.waitUntil(lambda: page.error_visible, timeout=8000)
        err = page._error_card.error
        assert err.code == "ai.no_llm" and len(err.steps) >= 3 and err.cause and any(a.kind == "open_settings" for a in err.actions)


def test_series_setup_card_connects_claude_with_one_tiny_call(ctx, series, world, qtbot):
    page = ctx.registry.get("series").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1300, 1100)
    page.show()
    card = page.ai_panel.cards["text"]
    card.key_edit.setText("sk-ant-NEWKEY-4321")
    card.save_btn.click()
    qtbot.waitUntil(lambda: page.ai_panel.rows["text"].text.text().endswith("Bağlı"), timeout=8000)
    posts = [r for r in world.rec.requests if r.method == "POST" and r.url.host == "api.anthropic.com"]
    assert len(posts) == 1 and posts[0].url.path == "/v1/messages"                                              # tek, minik gerçek çağrı
    assert posts[0].headers["x-api-key"] == "sk-ant-NEWKEY-4321" and ctx.vault.has_secret("anthropic")
    assert "sk-ant-NEWKEY" not in texts(page) and page.ai_panel.rows["text"].text.text() == "Yazı yapay zekâsı: Anthropic (Claude) · Bağlı"
    assert not card.isVisible()


def test_status_still_connected_while_a_provider_cools_down_after_rate_limit():
    acc, _ = make_access(vault=WVault(anthropic="sk-ant-x"))
    acc.registry.cool_down("anthropic", 120)                                       # 429 sonrası geri çekilme: anahtar yine de var
    assert acc.registry.is_cooling("anthropic") and acc.status("text").ready


def test_ai_guide_strings_have_matching_turkish_and_english_with_same_placeholders():
    import re
    from kadraj.ai_engine.aiguide_strings import EN, TR
    assert set(TR) == set(EN)
    for key in TR:
        assert re.findall(r"\{(\w+)\}", TR[key]) == re.findall(r"\{(\w+)\}", EN[key]) or set(re.findall(r"\{(\w+)\}", TR[key])) == set(re.findall(r"\{(\w+)\}", EN[key])), key
        assert "KADRAJ" not in TR[key] and "KADRAJ" not in EN[key]


def test_rate_limit_card_tells_minutes_not_seconds(vault_unused=None):
    from kadraj.ai_engine.aierrors import error_card
    assert error_card("rate_limit", "anthropic", retry_in=30).steps[0] == "1 dakika bekle."
    assert error_card("rate_limit", "anthropic", retry_in=150).steps[0] == "3 dakika bekle."
    assert error_card("rate_limit", "anthropic").steps[0] == "1-2 dakika bekle."
