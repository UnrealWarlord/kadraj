"""Basit ve Legend sayfaları: iki modda açılış/boyama, zaman çizgisi etkileşimi (yapışma, sürükle, kırp), önizleme, otomasyon
şeridi, dışa aktarma, tema geçişinde durumun korunması."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from kadraj.modules.editor.page_simple import EditorSimplePage
from kadraj.modules.editor.timeline import Clip

from .conftest import needs_ffmpeg


@pytest.fixture()
def svc(ctx, tmp_path):
    ctx.project.create(tmp_path / "projeler", "Sayfa Testi")
    s = ctx.service("editor")
    s.reload("load")
    return s


@pytest.fixture()
def simple(ctx, svc, qtbot):
    ctx.set_ui_mode("simple")
    page = ctx.registry.get("editor").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1300, 1000)
    page.show()
    return page


def label_texts(page):
    return " ".join(l.text() for l in page.findChildren(QLabel))


def click_pos(view, x, y):
    return QPointF(x, y).toPoint()


@needs_ffmpeg
def test_simple_page_opens_renders_and_has_no_brand_word(ctx, svc, simple, media):
    assert isinstance(simple, EditorSimplePage) and simple.svc is ctx.service("editor")
    assert "KADRAJ" not in label_texts(simple)
    for kw in (dict(), dict(large_text=True), dict(high_contrast=True)):
        ctx.theme.apply("simple", animate=False, **kw)
        assert not simple.grab().isNull()
    txt = label_texts(simple) + " ".join(b.text() for b in simple.findChildren(QPushButton))
    for needle in ("Sihirli dokunuşlar", "Nerede paylaşacaksın?", "Zaman çizgisi", "Videoyu Kaydet", "Önce / Sonra", "Yatay (YouTube)"):
        assert needle in txt, needle
    assert len(simple.export.platforms.group.cards()) == 7
    assert {q for q in simple.export.quality.cards} == {"fast", "balanced", "heavy"}
    assert set(simple.magic.buttons) == {"silence", "voice", "ducking", "vitalize", "subtitles", "watermark"}


@needs_ffmpeg
def test_simple_timeline_drag_snap_and_trim(ctx, svc, simple, media, qtbot):
    a = svc.add_media(media["a"])
    b = svc.add_media(media["b"])
    tlv = simple.timeline
    tlv._compute_rects()
    assert len(tlv._rects) == 2 and tlv.ruler_h > 0
    # b'yi sürükle: başı a'nın sonunun biraz sağında bırak → yapışır (mıknatıs açık)
    rb, _tr, _c = next(x for x in tlv._rects if x[2].id == b.id)
    start_x = rb.center().x()
    y = rb.center().y()
    dx = -(b.start - a.end) * tlv.px_per_s + 5            # a.end = b.start zaten; 5 px sağa kaydır
    QTest.mousePress(tlv, Qt.MouseButton.LeftButton, pos=click_pos(tlv, start_x, y))
    QTest.mouseMove(tlv, click_pos(tlv, start_x + 5, y))
    QTest.mouseMove(tlv, click_pos(tlv, start_x + 7, y))
    assert tlv._drag is not None and tlv._drag["moved"] and tlv.snap_text == "Yapıştı!"
    QTest.mouseRelease(tlv, Qt.MouseButton.LeftButton, pos=click_pos(tlv, start_x + 7, y))
    assert svc.tl.find_clip(b.id)[1].start == pytest.approx(a.end, abs=0.04)
    assert svc.selection == [b.id]
    # kenardan kırp
    rb = next(x for x in tlv._rects if x[2].id == b.id)[0]
    QTest.mousePress(tlv, Qt.MouseButton.LeftButton, pos=click_pos(tlv, rb.right() - 2, rb.center().y()))
    QTest.mouseMove(tlv, click_pos(tlv, rb.right() - 2 - 1.0 * tlv.px_per_s, rb.center().y()))
    QTest.mouseRelease(tlv, Qt.MouseButton.LeftButton, pos=click_pos(tlv, rb.right() - 2 - 1.0 * tlv.px_per_s, rb.center().y()))
    assert svc.tl.find_clip(b.id)[1].duration == pytest.approx(2.0, abs=0.25)
    svc.undo()
    assert svc.tl.find_clip(b.id)[1].duration == pytest.approx(3.0, abs=0.1)
    # cetvele tıkla → oynatma başlığı
    QTest.mousePress(tlv, Qt.MouseButton.LeftButton, pos=click_pos(tlv, tlv.time_to_x(1.0), 5))
    QTest.mouseRelease(tlv, Qt.MouseButton.LeftButton, pos=click_pos(tlv, tlv.time_to_x(1.0), 5))
    assert svc.playhead == pytest.approx(1.0, abs=0.12)
    assert "0:01 /" in simple.time_label.text()
    # böl + sil düğmeleri
    n = len(svc.tl.track("V1").clips)
    simple.split_btn.click()
    assert len(svc.tl.track("V1").clips) == n + 1
    simple.undo_btn.click()
    assert len(svc.tl.track("V1").clips) == n


@needs_ffmpeg
def test_simple_preview_compare_and_orientation(ctx, svc, simple, media, qtbot):
    svc.add_media(media["a"])
    svc.add_adjust_layer([{"type": "hue", "params": {"saturation": 0}}], 3.0)
    svc.set_playhead(1.0)
    simple.compare_btn.setChecked(True)
    qtbot.waitUntil(lambda: simple.preview.after is not None and simple.preview.before is not None, timeout=15000)
    assert simple.preview.compare and simple.preview.before.width() > 0
    simple.compare_btn.setChecked(False)
    assert not simple.preview.compare
    simple.portrait_btn.click()
    assert (svc.tl.width, svc.tl.height) == (1080, 1920) and simple.portrait_btn.isChecked()
    simple.landscape_btn.click()
    assert (svc.tl.width, svc.tl.height) == (1920, 1080)


@needs_ffmpeg
def test_simple_magic_is_user_triggered_and_undoable(ctx, svc, simple, media, qtbot, monkeypatch):
    import sys
    svc.add_media(media["music"])
    svc.add_media(media["voice"], track_id="A2")
    snap = svc.tl.snapshot()
    assert simple.proposal_bar.isHidden()
    simple.magic.trigger("ducking")
    assert not simple.proposal_bar.isHidden() and svc.tl.snapshot() == snap               # öneri var, uygulanmadı
    assert simple.proposal_bar.apply_btn.isVisibleTo(simple.proposal_bar)
    simple.proposal_bar.apply_btn.click()
    assert any(e["type"] == "duck" for e in svc.tl.track("A1").effects)
    simple.undo_btn.click()
    assert svc.tl.snapshot() == snap
    monkeypatch.setitem(sys.modules, "librosa", None)                                       # ağır paket yok → hata kartı + kur eylemi
    svc.select([svc.tl.track("A1").clips[0].id])
    simple.magic.trigger("beat") if "beat" in simple.magic.buttons else svc.propose("beat")
    assert simple.error_visible
    assert any(a.kind == "copy" for a in simple._error_card.error.actions) if hasattr(simple._error_card, "error") else True


@needs_ffmpeg
def test_simple_export_flow_and_reveal(ctx, svc, simple, media, qtbot):
    svc.add_media(media["a"])
    svc.set_canvas(160, 90)
    simple.export.platforms.set_template("youtube")
    assert "Tahmini boyut" in simple.export.quality.estimate_label.text()
    simple.export.quality.group.set_selected(["fast"])
    job = simple.export.start()
    assert job is not None
    qtbot.waitUntil(lambda: job.finished, timeout=20000)
    qtbot.waitUntil(lambda: not simple.export.reveal_btn.isHidden(), timeout=3000)
    assert "Hazır:" in simple.export.status.text()
    opened = []
    svc.opener = lambda url: opened.append(url) or True
    simple.export.reveal_btn.click()
    assert len(opened) == 1
    ctx.set_ui_mode("legend")
    assert "Hazır:" in simple.export.status.text()                                          # mod değişince durum korunur


# ======================================================================== Legend
@pytest.fixture()
def legend(ctx, svc, qtbot):
    ctx.set_ui_mode("legend")
    page = ctx.registry.get("editor").legend_page(ctx)
    qtbot.addWidget(page)
    page.resize(1500, 950)
    page.show()
    return page


@needs_ffmpeg
def test_legend_page_opens_with_panels_and_workspaces(ctx, svc, legend, media):
    from kadraj.modules.editor.page_legend import EditorLegendPage, WORKSPACES
    assert isinstance(legend, EditorLegendPage) and legend.svc is ctx.service("editor")
    assert "KADRAJ" not in label_texts(legend)
    assert not legend.grab().isNull()
    assert legend.workspace == "edit" and legend.docks["timeline"].isVisible() and legend.docks["library"].isVisible()
    assert not legend.docks["scopes"].isVisible() and not legend.docks["export"].isVisible()
    visible = {}
    for ws in WORKSPACES:
        legend.set_workspace(ws)
        visible[ws] = {n for n, d in legend.docks.items() if d.isVisible()}
        assert not legend.grab().isNull()
    assert "scopes" in visible["color"] and "audio" in visible["audio"] and "export" in visible["export"] and "timeline" in visible["edit"]
    assert legend.ws_buttons["export"].isChecked()
    menus = [a.text() for a in legend.win.menuBar().actions()]
    assert menus == ["Dosya", "Düzen", "Klip", "Dizi", "İşaretçi", "Pencere"]


@needs_ffmpeg
def test_legend_workspace_layouts_are_saved_per_workspace(ctx, svc, legend):
    legend.set_workspace("color")
    legend.docks["scopes"].hide()                                  # kullanıcı Ölçüm panelini kapattı
    legend.set_workspace("edit")                                   # RENK durumu kaydedildi
    assert legend.workspace_state("color") != ""
    legend.set_workspace("color")
    assert not legend.docks["scopes"].isVisible()                  # RENK'e dönünce kapalı kalır
    legend.set_workspace("audio")
    assert legend.docks["audio"].isVisible()
    legend.stop_workspace_state()
    saved = ctx.settings.get("editor.workspaces")
    assert set(saved) >= {"edit", "color", "audio"}


@needs_ffmpeg
def test_legend_multitrack_timeline_adj_markers_and_inspector(ctx, svc, legend, media, qtbot):
    a = svc.add_media(media["a"])
    for kind in ("video", "video", "adj", "sub"):
        legend.add_track_btn.menu().actions()[("video", "audio", "adj", "sub").index(kind)].trigger()
    assert len(svc.tl.tracks) >= 7
    legend.timeline._compute_rects()
    assert legend.timeline.pro and legend.timeline.header_w > 100
    legend.adj_btn.click()
    assert any(c.kind == "adjust" for _t, c in svc.tl.all_clips())
    svc.select([a.id])
    insp = legend.inspector
    insp.speed.setValue(2.0)
    assert svc.tl.find_clip(a.id)[1].speed == 2.0
    svc.set_playhead(0.5)
    insp.prop.setCurrentIndex(insp.prop.findData("scale"))
    insp.value.setValue(1.5)
    insp.ease.setCurrentIndex(insp.ease.findData("bezier"))
    insp.add_kf.click()
    kf = svc.tl.find_clip(a.id)[1].keyframes["scale"][0]
    assert kf.ease == "bezier" and kf.bezier is not None and kf.v == 1.5
    insp.color["saturation"].setValue(130)
    assert any(e["type"] == "eq" and e["params"]["saturation"] == pytest.approx(1.3) for e in svc.tl.find_clip(a.id)[1].effects)
    insp.slow_btn.click()
    assert svc.tl.find_clip(a.id)[1].speed_curve
    svc.set_playhead(1.0)
    legend.markers.chapter.setChecked(True)
    legend.markers.label.setText("Giriş")
    legend.markers.add_btn.click()
    assert svc.chapters_text().startswith("0:00") and "Giriş" in svc.chapters_text()
    assert "YouTube en az 3 bölüm" in legend.markers.problem.text()
    legend.mixer.eq.set_gains([0, 0, 0, 4, 0, 0, 0, 0, 0, 0])
    legend.mixer.eq.gainsChanged.emit(list(legend.mixer.eq.gains))
    assert svc.tl.eq[3] == 4
    assert "kanal" in legend.footer.text() and legend.timecode.text().count(":") == 3


@needs_ffmpeg
def test_legend_scopes_color_workspace_and_export_codecs(ctx, svc, legend, media, qtbot):
    svc.add_media(media["a"])
    legend.set_workspace("color")
    svc.set_playhead(1.0)
    legend.request_frames_now()
    qtbot.waitUntil(lambda: legend.scopes.frame is not None, timeout=15000)
    assert legend.scopes.data is not None and legend.scope_stats.text().startswith("Y ")
    for mode in ("parade", "vector", "hist"):
        legend.scope_buttons[mode].click()
        assert legend.scopes.mode == mode and legend.scopes.data is not None and not legend.scopes.grab().isNull()
    legend.set_workspace("export")
    q = legend.export.quality
    assert q.codec is not None and {q.codec.itemData(i) for i in range(q.codec.count())} == {"h264", "h265", "prores", "wav"}
    q.rate.setCurrentIndex(q.rate.findData("cbr"))
    q.passes.setCurrentIndex(q.passes.findData("2"))
    s = legend.export.current_settings()
    assert (s.rate_mode, s.passes) == ("cbr", 2) and legend.export.queue is not None
    hw = svc.hardware()
    assert q.hw.model().item(2).isEnabled() == bool(hw.get("nvenc")) and q.hw.model().item(1).isEnabled()
    svc.set_canvas(160, 90)
    q.group.set_selected(["fast"])
    q.passes.setCurrentIndex(q.passes.findData("1"))
    q.rate.setCurrentIndex(q.rate.findData("vbr"))
    job = legend.export.start()
    qtbot.waitUntil(lambda: job.finished, timeout=20000)
    qtbot.wait(100)
    assert legend.export.queue.count() >= 1 and not legend.export.reveal_btn.isHidden()


@needs_ffmpeg
def test_both_pages_survive_mode_switches_and_theme_changes(ctx, svc, media, qtbot):
    svc.add_media(media["a"])
    simple = ctx.registry.get("editor").simple_page(ctx)
    legend = ctx.registry.get("editor").legend_page(ctx)
    for w in (simple, legend):
        qtbot.addWidget(w)
        w.resize(1300, 900)
        w.show()
    for mode in ("legend", "simple", "legend"):
        ctx.theme.apply(mode, animate=False)
        for w in (simple, legend):
            assert not w.grab().isNull()
    ctx.theme.apply("simple", animate=False, large_text=True, high_contrast=True)
    assert not simple.grab().isNull()
    assert simple.svc.tl is legend.svc.tl                     # tek servis, tek zaman çizelgesi
