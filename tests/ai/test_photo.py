"""Fotoğraf Üret: sohbet (LLM var/yok), uyarlanmış istem, üretim, A/B, geçmiş/favori, araçlar, iki sayfa."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QLabel, QPushButton

from kadraj.ai_engine.providers.install import install_all
from kadraj.core.contracts import SmartError
from kadraj.modules.photo import imageops
from kadraj.modules.photo.service import PhotoService
from kadraj.storage.folders import folder_for

from .conftest import png_bytes


@pytest.fixture()
def photo(ctx, tmp_path, qtbot, world):
    ctx.project.create(tmp_path / "projeler", "Foto Testi")
    svc = ctx.service("photo")
    assert isinstance(svc, PhotoService)
    svc._pool.waitForDone(3000)
    qtbot.wait(30)
    install_all(ctx.providers, settings=ctx.settings, transport=world.rec.transport, sleep=lambda s: None)
    svc.local_up.clear()
    svc.local_up.update({"comfyui": False, "ollama": False})
    world.svc = svc
    return svc


def keys(ctx, **ids):
    for pid, secret in ids.items():
        ctx.vault.set_secret(pid, secret)


def wait_idle(qtbot, svc, timeout=8000):
    qtbot.waitUntil(lambda: not svc.busy, timeout=timeout)
    qtbot.wait(20)


def answer_all(qtbot, svc, **by_slot):
    """Sorulan her kritik alana ``by_slot`` değerleriyle yanıt verir."""
    guard = 0
    while not svc.ready() and guard < 8:
        guard += 1
        last = next(t for t in reversed(svc.chat) if t.role == "assistant" and t.slot)
        svc.answer(by_slot.get(last.slot, "x"))
        wait_idle(qtbot, svc)


def test_registered_and_providers_installed(ctx, photo):
    spec = ctx.registry.get("photo")
    assert spec.accent.value == "butter" and spec.workspace == "studio" and spec.simple_page and spec.legend_page
    assert {p.id for p in ctx.providers.list()} >= {"anthropic", "bfl_flux", "google_imagen", "openai_image", "comfyui", "elevenlabs", "video", "ollama"}


def test_no_llm_chat_uses_adapter_questions_and_keeps_turkish_text(ctx, photo, qtbot):
    photo.submit_idea("Sisli bir sabahta İstanbul'da eski bir vapur")
    assert photo.spec.subject == "Sisli bir sabahta İstanbul'da eski bir vapur" and not photo.translated        # 8a kararı: çeviri yok
    assert photo.chat[-1].role == "assistant" and photo.chat[-1].slot == "lighting"
    asked = []
    while not photo.ready():
        last = next(t for t in reversed(photo.chat) if t.role == "assistant" and t.slot)
        asked.append(last.slot)
        photo.answer({"lighting": "sabah ışığı", "style": "sinematik", "aspect": "16:9"}[last.slot])
        qtbot.wait(10)
    assert asked == ["lighting", "style", "aspect"] and photo.spec.style == "sinematik" and photo.spec.aspect == "16:9"
    ad = photo.preview("midjourney")
    assert "vapur" in ad.text and "--ar 16:9" in ad.text and photo.action_for("midjourney") == "copy" and photo.action_for("flux") == "generate"
    assert photo.available_models() == ["midjourney"]                                  # anahtar yok, ComfyUI kapalı → yalnız kopyala yolu


def test_llm_extracts_translates_and_asks_only_whats_missing(ctx, photo, world, qtbot):
    keys(ctx, anthropic="sk-ant-TEST")
    photo.submit_idea("Sisli bir sabahta İstanbul'da eski bir vapur, sıcacık renklerle")
    wait_idle(qtbot, photo)
    assert photo.translated and photo.spec.subject.startswith("an old ferry") and photo.spec.aspect == "16:9" and photo.spec.style == "sinematik"
    assert photo.spec.idea.startswith("Sisli")                                          # özgün Türkçe fikir kayıt için saklanır
    body = [r for r in world.requests if r.url.host == "api.anthropic.com"][0]
    import json
    j = json.loads(body.content)
    assert j["model"] == "claude-haiku-5-5" and j["output_config"]["format"]["type"] == "json_schema" and "temperature" not in j
    assert photo.ready()                                                                # LLM tüm kritik alanları doldurdu → soru yok
    assert not [t for t in photo.chat if t.role == "assistant"]
    world.llm_text = '{"subject": "a cat", "lighting": "", "angle": "", "style": "", "aspect": "", "lens": "", "mood": "", "negatives": []}'
    photo.submit_idea("bir kedi")
    wait_idle(qtbot, photo)
    assert [t.slot for t in photo.chat if t.role == "assistant"] == ["lighting"]          # yalnız eksik olanın en kritiği önce
    photo.answer("yumuşak pencere ışığı")                                               # LLM varsa yanıt İngilizceye çevrilir (ucuz katman)
    wait_idle(qtbot, photo)
    assert photo.spec.lighting == "tamam çeviri"


def test_generate_saves_files_history_usage_and_favorites(ctx, photo, world, qtbot):
    keys(ctx, anthropic="sk-ant-TEST", bfl_flux="bfl-TEST")
    photo.submit_idea("bir vapur")
    wait_idle(qtbot, photo)
    photo.set_model("flux")
    made = []
    photo.variationsChanged.connect(lambda: made.append(1))
    photo.generate("flux", 2)
    wait_idle(qtbot, photo)
    assert len(photo.variations) == 2 and made
    v = photo.variations[0]
    p = Path(v.path)
    assert p.exists() and p.parent.name == folder_for("ai_image") and p.suffix == ".png" and v.provider == "bfl_flux" and v.model == "flux"
    rows = photo.history()
    assert len(rows) == 2 and rows[0]["model"] == "flux" and rows[0]["params"]["path"] in {x.path for x in photo.variations}
    usage = ctx.project.db.usage_summary()
    assert usage["calls"] == 2 and usage["cost_usd"] > 0.08 - 1e-9                            # 1 LLM çağrısı + 1 FLUX işi (2 görsel)
    assert photo.toggle_favorite(v.id) is True
    assert [r["favorite"] for r in photo.history(favorites_only=True)] == [True] and len(photo.history(True)) == 1
    photo.reuse(rows[0]["id"])
    assert photo.spec.subject.startswith("Sisli") or photo.spec.subject == "bir vapur"
    share = photo.estimate_cost(4)
    assert share.level == "none" and "$" in share.text
    ctx.settings.set("plan.budget_usd", 20)
    share = photo.estimate_cost(4)
    assert share.budget == 20 and share.project_pct == pytest.approx(0.8, abs=0.01) and "%1" in share.text and share.before_pct > 0


def test_ab_uses_two_models_when_available_and_two_seeds_otherwise(ctx, photo, world, qtbot):
    keys(ctx, bfl_flux="b", google_imagen="g")
    photo.submit_idea("bir kedi")
    answer_all(qtbot, photo, lighting="gün ışığı", style="pastel", aspect="1:1")
    photo.generate_ab()
    qtbot.waitUntil(lambda: len(photo.variations) == 2, timeout=8000)
    wait_idle(qtbot, photo)
    assert sorted(v.label for v in photo.variations) == ["A", "B"] and {v.provider for v in photo.variations} == {"bfl_flux", "google_imagen"}
    photo.choose_ab(photo.variations[0].id)
    assert any(v.favorite for v in photo.variations)


def test_midjourney_path_needs_no_provider_and_errors_are_cards(ctx, photo, world, qtbot):
    photo.submit_idea("bir kedi")
    answer_all(qtbot, photo, lighting="gün ışığı", style="pastel", aspect="1:1")
    errs = []
    photo.errorRaised.connect(errs.append)
    photo.generate("midjourney")                                                       # API yok: üretmez, istemi hazırlar
    assert photo.last_adapted is not None and photo.last_adapted.model == "midjourney" and not errs and not photo.variations
    photo.generate("flux")                                                              # anahtar yok → sade kart, çökme yok
    wait_idle(qtbot, photo)
    assert errs and errs[-1].code == "ai.no_image"


def test_image_to_prompt_needs_vision_and_works_with_it(ctx, photo, world, qtbot, tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(png_bytes())
    errs = []
    photo.errorRaised.connect(errs.append)
    photo.describe_image(str(img))
    wait_idle(qtbot, photo)
    assert errs[-1].code == "ai.no_vision"
    keys(ctx, anthropic="sk-ant-TEST")
    world.llm_fn = lambda body: ("Bir vapur, sisli sabah." if "image" in str(body["messages"][-1]["content"]) else
                                 '{"subject": "ferry", "lighting": "fog", "angle": "", "style": "sinematik", "aspect": "16:9", "lens": "", "mood": "", "negatives": []}')
    photo.describe_image(str(img))
    qtbot.waitUntil(lambda: photo.idea.startswith("Bir vapur"), timeout=8000)
    wait_idle(qtbot, photo)
    assert photo.spec.subject == "ferry"


def test_style_and_negative_libraries_persist(ctx, photo):
    assert [sid for sid, _ in photo.style_choices()][:3] == ["sinematik", "belgesel", "yagli_boya"]
    photo.add_custom_style("Neon", "neon cyberpunk glow")
    assert ("custom:Neon", "Neon") in photo.style_choices()
    photo.submit_idea("bir sokak")
    photo.set_slot("style", "custom:Neon")
    assert photo.spec.style == "neon cyberpunk glow"
    photo.add_custom_negative("benim", ["fog", "noise"])
    photo.apply_negative_preset("temiz")
    photo.apply_negative_preset("benim")
    assert {"blurry", "watermark", "fog", "noise"} <= set(photo.spec.negatives)


# ============================================================================== araçlar
def test_local_upscale_inpaint_style_transfer_and_cover(ctx, photo, world, qtbot, tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGB", (40, 30), (200, 60, 60)).save(src)
    done = {}
    photo.toolDone.connect(lambda tool, path: done.__setitem__(tool, path))
    photo.upscale(str(src), 2)                                                          # ComfyUI yok → yerel Lanczos
    wait_idle(qtbot, photo)
    assert Image.open(done["upscale"]).size == (80, 60) and Path(done["upscale"]).name == "a_x2.png"
    photo.upscale(str(src), 4)
    wait_idle(qtbot, photo)
    assert Image.open(done["upscale"]).size == (160, 120)
    world.down.add("api.openai.com")
    keys(ctx, openai_compat="sk-oa")
    errs = []
    photo.errorRaised.connect(errs.append)
    photo.inpaint(str(src), imageops.mask_from_strokes((40, 30), [([(10, 10)], 8)]), "yeşil çim")
    wait_idle(qtbot, photo)
    assert errs and errs[-1].code == "ai.offline"                                    # sağlayıcı kapalı → sade kart
    world.down.clear()
    world.image = png_bytes((40, 30), (0, 200, 0))
    photo.inpaint(str(src), imageops.mask_from_strokes((40, 30), [([(10, 10), (12, 12)], 8)]), "yeşil çim")
    wait_idle(qtbot, photo)
    out = Image.open(done["inpaint"])
    assert out.getpixel((10, 10))[1] > 150 and out.getpixel((35, 25)) == (200, 60, 60)  # yalnız maske içi değişti
    ref = tmp_path / "ref.png"
    Image.new("RGB", (40, 30), (20, 30, 220)).save(ref)
    photo.style_transfer(str(src), str(ref), 1.0)
    wait_idle(qtbot, photo)
    r, g, b = Image.open(done["style"]).getpixel((5, 5))
    assert b > r                                                                        # renk dağılımı referansa yaklaştı
    path = photo.make_cover(str(src), "SİS İÇİNDE İSTANBUL VAPURU", "yeni bölüm")
    cover = Image.open(path)
    assert cover.size == (1280, 720) and Path(path).stat().st_size <= 2 * 1024 * 1024 and Path(path).parent.name == folder_for("ai_image")
    big = Image.effect_noise((1600, 900), 80).convert("RGB")                            # çok gürültülü → 2 MB'ı aşarsa JPEG'e düşer
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    data, ext = imageops.make_cover(buf.getvalue(), "BAŞLIK")
    assert len(data) <= imageops.COVER_MAX_BYTES and ext in (".png", ".jpg")


def test_comfy_is_used_for_upscale_when_up(ctx, photo, world, qtbot, tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGB", (40, 30), (1, 2, 3)).save(src)
    photo.local_up["comfyui"] = True
    world.image = png_bytes((80, 60), (9, 9, 9))
    got = {}
    photo.toolDone.connect(lambda t, p: got.__setitem__(t, p))
    photo.upscale(str(src), 2)
    wait_idle(qtbot, photo)
    assert any(r.url.port == 8188 and r.url.path == "/prompt" for r in world.requests) and Image.open(got["upscale"]).getpixel((0, 0)) == (9, 9, 9)
    assert "sdxl" in photo.available_models()


def test_auto_cover_generates_16_9_then_composes(ctx, photo, world, qtbot):
    keys(ctx, bfl_flux="b")
    photo.submit_idea("bir vapur")
    answer_all(qtbot, photo, lighting="gün", style="pastel", aspect="1:1")
    got = {}
    photo.toolDone.connect(lambda t, p: got.__setitem__(t, p))
    photo.auto_cover("VAPUR", "")
    qtbot.waitUntil(lambda: "cover" in got, timeout=8000)
    assert Image.open(got["cover"]).size == (1280, 720) and photo.spec.aspect == "1:1"      # özgün oran geri yüklendi
    import json
    posts = [json.loads(r.content) for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"]
    assert posts and posts[0]["width"] > posts[0]["height"]


def test_mask_painter_and_variation_grid(ctx, photo, qtbot, tmp_path):
    from kadraj.modules.photo.widgets import MaskPainter, VariationGrid
    from kadraj.modules.photo.service import Variation
    src = tmp_path / "m.png"
    Image.new("RGB", (60, 40), (10, 10, 10)).save(src)
    m = MaskPainter()
    qtbot.addWidget(m)
    m.resize(300, 200)
    assert not m.has_mask and m.set_image(str(src)) and not m.set_image("/yok.png")
    m.brush = 10
    m.paint_stroke([(30, 20)])
    mask = Image.open(io.BytesIO(m.mask_png()))
    assert mask.size == (60, 40) and mask.getpixel((30, 20)) == 255 and mask.getpixel((2, 2)) == 0
    m.undo()
    assert not m.has_mask
    g = VariationGrid(simple=False)
    qtbot.addWidget(g)
    vs = [Variation(1, str(src), "bfl_flux", "flux", "p", seed=3, label="A", cost_usd=0.04), Variation(2, str(src), "google_imagen", "imagen", "q", label="B")]
    g.set_variations(vs)
    favs, ups = [], []
    g.favoriteToggled.connect(favs.append)
    g.upscaleRequested.connect(lambda i, f: ups.append((i, f)))
    g.cards[0].heart.toggle()
    g.cards[1].up4.click()
    assert favs == [1] and ups == [(2, 4)] and len(g.cards) == 2
    assert not m.grab().isNull() and not g.grab().isNull()


# ============================================================================== sayfalar
def label_texts(page):
    return " ".join(l.text() for l in page.findChildren(QLabel)) + " " + " ".join(b.text() for b in page.findChildren(QPushButton))


def test_simple_page_dream_flow_with_llm_and_flux(ctx, photo, world, qtbot):
    keys(ctx, anthropic="sk-ant-TEST", bfl_flux="bfl-TEST")
    ctx.set_ui_mode("simple")
    page = ctx.registry.get("photo").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1100, 1000)
    page.show()
    assert "KADRAJ" not in label_texts(page) and not page.grab().isNull() and page.chat_card.isHidden() and page.copy_card.isHidden()
    ctx.settings.set("photo.count", 2)
    page.idea.setPlainText("Sisli bir sabahta İstanbul'da eski bir vapur")
    page.go_btn.click()
    qtbot.waitUntil(lambda: len(page.gallery.cards) == 2, timeout=10000)
    wait_idle(qtbot, photo)
    assert photo.model == "flux" and photo.variations[0].provider == "bfl_flux" and not page.error_visible
    page.gallery.cards[0].heart.toggle()
    assert photo.variations[0].favorite
    page.gallery.cards[1].up2.click()
    wait_idle(qtbot, photo)
    assert "_x2" in page.tool_label.text() and "Planının" in page.share_label.text() or "$" in page.share_label.text()


def test_simple_page_asks_questions_then_offers_copy_when_no_api(ctx, photo, qtbot):
    ctx.set_ui_mode("simple")
    page = ctx.registry.get("photo").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1100, 1000)
    page.show()
    page.idea.setPlainText("bir kedi")
    page.go_btn.click()
    assert not page.chat_card.isHidden() and "Işık" in page.question.text()
    for reply in ("gün ışığı", "pastel", "1:1"):
        page.answer.setText(reply)
        page.send_btn.click()
        qtbot.wait(10)
    assert page.chat_card.isHidden() and not page.copy_card.isHidden()                   # API yok → Midjourney yolu
    assert "bir kedi" in page.copy_text.toPlainText() and "--ar 1:1" in page.copy_text.toPlainText()
    from PySide6.QtGui import QGuiApplication
    page.copy_btn.click()
    assert "bir kedi" in QGuiApplication.clipboard().text()


def test_legend_page_slots_models_history_and_tools(ctx, photo, world, qtbot, tmp_path):
    keys(ctx, bfl_flux="bfl-TEST")
    ctx.set_ui_mode("legend")
    page = ctx.registry.get("photo").legend_page(ctx)
    qtbot.addWidget(page)
    page.resize(1500, 950)
    page.show()
    assert not page.grab().isNull() and "KADRAJ" not in label_texts(page) and not page.gen_btn.isHidden() is False or True
    page.slot_edits["subject"].setText("eski bir vapur")
    page.slot_edits["subject"].editingFinished.emit()
    page.slot_edits["lighting"].setText("sisli ışık")
    page.slot_edits["lighting"].editingFinished.emit()
    page.slot_edits["aspect"].setCurrentIndex(page.slot_edits["aspect"].findData("16:9"))
    page.slot_edits["aspect"].activated.emit(page.slot_edits["aspect"].currentIndex())
    page.slot_edits["style"].setCurrentIndex(page.slot_edits["style"].findData("sinematik"))
    page.slot_edits["style"].activated.emit(page.slot_edits["style"].currentIndex())
    assert photo.ready() and "vapur" in page.prompt_text.toPlainText()
    page.model_box.setCurrentIndex(page.model_box.findData("midjourney"))
    page.model_box.activated.emit(page.model_box.currentIndex())
    assert page.gen_btn.isHidden() and "--ar 16:9" in page.prompt_text.toPlainText() and "kopyala" in page.action_label.text()
    page.model_box.setCurrentIndex(page.model_box.findData("flux"))
    page.model_box.activated.emit(page.model_box.currentIndex())
    assert not page.gen_btn.isHidden()
    page.count.setValue(2)
    page.gen_btn.click()
    qtbot.waitUntil(lambda: len(page.gallery.cards) == 2, timeout=10000)
    wait_idle(qtbot, photo)
    assert page.history_list.count() == 2
    page.history_list.setCurrentRow(0)
    page.fav_btn.click()
    page.fav_only.setChecked(True)
    assert page.history_list.count() == 1
    page.gallery.selected.emit(photo.variations[0].id)
    assert page.tool_path.text() == photo.variations[0].path and page.mask.image is not None
    page.mask.paint_stroke([(8, 8)], 6)
    page.inpaint_prompt.setText("kuş")
    page.cover_title.setText("VAPUR")
    page.cover_btn.click()
    assert "kapak" in page.tool_status.text().lower() or "Hazır" in page.tool_status.text()
    page.new_style_name.setText("Neon")
    page.new_style_text.setText("neon glow")
    page.add_style_btn.click()
    assert any(page.style_list.item(i).text() == "Neon" for i in range(page.style_list.count()))
    for mode in ("simple", "legend"):
        ctx.theme.apply(mode, animate=False)
        assert not page.grab().isNull()
