"""Dizi/Film: servis (onay kapıları, kalıcılık, üretim işi, duraklat/devam), kanban, düğüm akışı, iki sayfa — sahte HTTP + gerçek FFmpeg."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QLabel, QPushButton

from kadraj.ai_engine.pipeline.machine import Stage
from kadraj.ai_engine.providers.install import install_all
from kadraj.core.contracts import JobStatus, Topics
from kadraj.modules.series.service import SeriesService
from kadraj.modules.series.widgets import MIME
from kadraj.storage.folders import folder_for

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg yok")

BIBLE = {"episodes": [{"index": 1, "title": "Sis", "synopsis": "s"}],
         "characters": [{"id": "ahmet", "name": "Ahmet", "desc": "navy coat"}, {"id": "ayse", "name": "Ayşe", "desc": "red scarf"}],
         "locations": [{"id": "vapur", "name": "Vapur", "desc": "old ferry in fog"}],
         "scenes": [{"episode": 1, "title": f"Sahne {i}", "summary": f"Olay {i}", "location": "vapur", "characters": ["ahmet", "ayse"], "angle": "medium",
                     "seconds": 4, "cut": i == 1, "lines": [{"character": "ahmet", "text": f"Merhaba {i}"}]} for i in (1, 2, 3)]}


def respond(body):
    props = (body.get("output_config") or {}).get("format", {}).get("schema", {}).get("properties", {})
    if "ideas" in props:
        return json.dumps({"ideas": [{"title": "Sis", "logline": "Bir vapur sisle kaybolur", "hook": "h"}] * 3})
    if "elements" in props:
        return json.dumps({"title": "Sis", "elements": [{"type": "scene", "text": "INT. VAPUR - GÜN"}, {"type": "action", "text": "Sis."},
                                                        {"type": "character", "text": "AHMET"}, {"type": "dialogue", "text": "Kimse yok mu?"}]})
    if "episodes" in props:
        return json.dumps(BIBLE)
    if "cues" in props:
        return json.dumps({"cues": [{"text": "foghorn", "at": 0.5, "seconds": 2}]})
    if "items" in props:
        return json.dumps({"items": []})
    return "An old ferry in morning fog."


@pytest.fixture()
def svc(ctx, tmp_path, qtbot, world):
    ctx.project.create(tmp_path / "projeler", "Dizi Testi")
    ctx.settings.set("providers.video.base_url", "https://vid.example")
    ctx.settings.set("providers.video.status_path", "/v/{id}")
    s = ctx.service("series")
    assert isinstance(s, SeriesService)
    install_all(ctx.providers, settings=ctx.settings, transport=world.rec.transport, sleep=lambda x: None)
    for pid, secret in (("anthropic", "sk"), ("bfl_flux", "b"), ("video", "v"), ("elevenlabs", "e")):
        ctx.vault.set_secret(pid, secret)
    s.gateway.local_up.update({"comfyui": False, "ollama": False})
    world.llm_fn = respond
    return s


def idle(qtbot, svc, timeout=10000):
    qtbot.waitUntil(lambda: not svc.busy, timeout=timeout)
    qtbot.wait(20)


def errors(svc):
    got = []
    svc.errorRaised.connect(got.append)
    return got


def drive_to_storyboard(qtbot, svc):
    svc.set_idea("bir vapur gizemi", "Sis")
    assert svc.advance()
    svc.write_screenplay()
    idle(qtbot, svc)
    assert svc.approve() and svc.advance()
    svc.set_format("dizi", 6, 1, "1080p")
    assert svc.advance()
    svc.write_bible()
    idle(qtbot, svc)
    assert svc.approve() and svc.advance() and svc.stage is Stage.STORYBOARD


def approve_storyboard(svc):
    for sc in svc.project.scenes:
        svc.move_card(sc.id, "approved")
    assert svc.approve() and svc.advance() and svc.stage is Stage.PLAN


def run_job(qtbot, svc, timeout=30000):
    job = svc.produce()
    assert job is not None
    qtbot.waitUntil(lambda: job.finished, timeout=timeout)
    qtbot.wait(80)
    return job


# ============================================================================== aşama kapıları + kalıcılık
def test_registered_and_gates_show_friendly_cards(ctx, svc):
    spec = ctx.registry.get("series")
    assert spec.accent.value == "pink" and spec.workspace == "studio" and spec.simple_page and spec.legend_page and "ffmpeg" in spec.requires
    got = errors(svc)
    assert not svc.advance() and got[-1].code == "series.blocked.missing_idea"
    svc.set_idea("fikir")
    assert svc.advance() and not svc.advance() and "needs_approval" in got[-1].code or "missing_screenplay" in got[-1].code
    assert svc.project.stage is Stage.SCREENPLAY and not svc.approve() is False


def test_full_flow_gates_persistence_and_reload(ctx, svc, qtbot, world):
    ideas = []
    svc.ideasReady.connect(ideas.extend)
    svc.set_idea("bir vapur gizemi")
    svc.brainstorm()
    idle(qtbot, svc)
    assert len(ideas) == 3
    svc.choose_idea(ideas[0])
    assert svc.project.logline.startswith("Bir vapur")
    drive_to_storyboard(qtbot, svc)
    p = svc.project
    assert p.screenplay.startswith("Title:") and [s.id for s in p.scenes] == ["E01S01", "E01S02", "E01S03"] and p.characters[0].name == "Ahmet"
    assert ctx.project.data.series["pipeline"]["stage"] == "storyboard" and ctx.project.dirty
    row = ctx.project.db.load_series_state("pipeline")
    assert row["checkpoint"]["stage"] == "storyboard" and row["scene_index"] == 0
    got = errors(svc)
    assert not svc.advance() and got[-1].code == "series.blocked.needs_approval"              # kareler onaylanmadan geçilmez
    svc.generate_keyframes()
    idle(qtbot, svc)
    scenes = svc.project.scenes
    assert all(Path(s.keyframe).exists() and s.story == "review" for s in scenes)
    assert Path(scenes[0].keyframe).parent.name == "storyboard" and Path(scenes[0].keyframe).parts[-4] == folder_for("ai_scene")
    posts = [json.loads(r.content)["prompt"] for r in world.requests if r.url.host == "api.bfl.ai" and r.method == "POST"]
    assert len(posts) == 3 and all("navy coat" in x and "red scarf" in x for x in posts)       # karakter tarifleri her karede aynı
    approve_storyboard(svc)
    fresh = SeriesService(ctx)
    fresh.ctx = ctx
    fresh.load()                                                                               # proje yeniden açılış: kalan yerden yüklenir
    assert fresh.stage is Stage.PLAN and fresh.project.title == "Sis" and len(fresh.project.scenes) == 3


def test_checkpoint_newer_than_file_wins_after_crash(ctx, svc, qtbot):
    drive_to_storyboard(qtbot, svc)
    d = svc.project.to_dict()
    d["title"], d["updated"] = "Checkpoint Daha Yeni", svc.project.updated + 100
    ctx.project.db.save_series_state("pipeline", 0, d)                                          # dosya kaydedilmeden çökmüş gibi
    svc.load()
    assert svc.project.title == "Checkpoint Daha Yeni"


def test_kanban_moves_angles_reorder_and_approval_invalidation(ctx, svc, qtbot):
    drive_to_storyboard(qtbot, svc)
    svc.move_card("E01S01", "approved")
    svc.move_card("E01S02", "bogus")
    assert svc.project.scene("E01S01").story == "approved" and svc.project.scene("E01S02").story == "draft"
    assert [s.id for s in svc.columns()["approved"]] == ["E01S01"]
    svc.set_angle("E01S01", "close")
    svc.set_angle("E01S01", "uzay")                                                             # geçersiz açı yok sayılır
    assert svc.project.scene("E01S01").angle == "close" and svc.project.scene("E01S01").story == "draft"
    svc.reorder("E01S03", 0)
    assert [s.title for s in svc.project.scenes] == ["Sahne 3", "Sahne 1", "Sahne 2"] and [s.index for s in svc.project.scenes] == [1, 2, 3]
    svc.regenerate_scene("E01S02")
    idle(qtbot, svc)
    assert svc.project.scene("E01S02").story == "review" and Path(svc.project.scene("E01S02").keyframe).exists() and not svc.project.scene("E01S01").keyframe


# ============================================================================== üretim (iş) + duraklat/devam + montaj
@needs_ffmpeg
def test_production_job_pause_on_quota_notify_then_resume_and_assemble(ctx, svc, qtbot, world):
    drive_to_storyboard(qtbot, svc)
    svc.generate_keyframes()
    idle(qtbot, svc)
    approve_storyboard(svc)
    est = svc.estimate()
    assert est.video_seconds == 12 and svc.plan_share().level == "none"
    got = errors(svc)
    assert svc.produce() is None and got[-1].code.startswith("series.blocked")                  # plan onaylanmadan üretim yok
    assert svc.approve() and svc.advance() and svc.stage is Stage.PRODUCTION
    notes = []
    ctx.bus.subscribe(Topics.NOTIFY, notes.append)
    world.fail_video_after = 1                                                                  # 2. sahnede kota bitti
    job = run_job(qtbot, svc)
    res = job.payload["_result"]
    assert job.status is JobStatus.DONE and res["status"] == "paused" and res["scene"] == "E01S02" and res["error"] == "ai.quota"
    assert [s.prod for s in svc.project.scenes] == ["done", "paused", "pending"] and svc.is_paused() and svc.stage is Stage.PRODUCTION
    assert got[-1].code == "ai.quota" and any(n["level"] == "warn" and "E01S02" in n["text"] for n in notes)
    assert ctx.project.db.load_series_state("pipeline")["checkpoint"]["paused"]["scene"] == "E01S02" and ctx.project.db.load_series_state("pipeline")["scene_index"] == 1
    world.fail_video_after = None                                                               # yeni kota/anahtar geldi
    calls = world.video_calls
    job = run_job(qtbot, svc)
    assert job.payload["_result"]["status"] == "done" and world.video_calls == calls + 2        # yalnız 2. ve 3. sahne
    assert [s.prod for s in svc.project.scenes] == ["done"] * 3 and svc.stage is Stage.ASSEMBLY and not svc.is_paused()
    assert all(Path(s.clip).exists() for s in svc.project.scenes) and all(Path(s.last_frame).exists() for s in svc.project.scenes)
    editor = ctx.service("editor")
    summary = svc.assemble()
    assert summary["scenes"] == 3 and summary["voice_tracks"] == 1 and svc.stage is Stage.DONE
    assert any(t.name == "Dizi · Video" for t in editor.tl.tracks) and ctx.undo.canUndo()
    usage = ctx.project.db.usage_summary()
    assert usage["cost_usd"] > 1.0                                                              # 12 sn video × $0.10 + görseller + LLM


@needs_ffmpeg
def test_regenerate_during_production_resets_only_that_scene_and_cancel(ctx, svc, qtbot, world):
    drive_to_storyboard(qtbot, svc)
    approve_storyboard(svc)
    svc.approve()
    svc.advance()
    run_job(qtbot, svc)
    assert svc.stage is Stage.ASSEMBLY
    svc.regenerate_scene("E01S02")
    assert svc.stage is Stage.PRODUCTION and [s.prod for s in svc.project.scenes] == ["done", "pending", "done"]
    calls = world.video_calls
    run_job(qtbot, svc)
    assert world.video_calls == calls + 1 and svc.stage is Stage.ASSEMBLY                        # yalnız tek sahne yeniden üretildi


@needs_ffmpeg
def test_dubbing_and_foley_prepare_then_audio_for_all_languages(ctx, svc, qtbot, world):
    world.llm_fn = lambda body: (json.dumps({"items": [{"i": i, "text": f"EN line {i}"} for i in range(3)]})
                                 if "items" in (body.get("output_config") or {}).get("format", {}).get("schema", {}).get("properties", {}) else respond(body))
    drive_to_storyboard(qtbot, svc)
    svc.prepare_dubbing(["en"])
    svc.suggest_foley()
    idle(qtbot, svc)
    assert svc.project.dub_languages == ["en"] and svc.project.scenes[0].lines[0].texts["en"].startswith("EN line") and svc.project.scenes[0].foley[0].text == "foghorn"
    assert svc.estimate().tts_chars > sum(len(l.text) for s in svc.project.scenes for l in s.lines)   # dublaj karakter maliyeti eklendi


# ============================================================================== düğüm akışı (servis)
@needs_ffmpeg
def test_node_flow_persists_and_runs_with_history_matrix_and_analytics(ctx, svc, qtbot, world):
    assert len(svc.graph.nodes) == 8 and svc.graph.validate() == []
    n = svc.add_node("llm", (10.0, 20.0))
    assert n.id == "llm_02" and svc.connect_nodes("logline_01", "text", n.id, "context")
    got = errors(svc)
    assert not svc.connect_nodes("logline_01", "text", "upscale_01", "image") and got[-1].code == "ai.bad_response"       # tür uyumsuz
    assert not svc.connect_nodes("upscale_01", "image", "gorsel_01", "reference") and got[-1].code == "ai.cycle"
    svc.remove_node(n.id)
    svc.set_param("gorsel_01", "cfg", 5.5)
    svc.set_raw("gorsel_01", "negative", "blur")
    seed = svc.randomize_seed("gorsel_01")
    assert svc.graph.nodes["gorsel_01"].params["seed_locked"] is True and svc.graph.nodes["gorsel_01"].params["seed"] == seed
    runs = []
    svc.runFinished.connect(runs.append)
    svc.run_graph()
    qtbot.waitUntil(lambda: bool(runs), timeout=30000)
    idle(qtbot, svc)
    run = runs[0]
    assert run.status == "done" and run.results["gorsel_01"].params["seed"] == seed and svc.runs[0] is run
    saved = ctx.project.data.series["nodes"]
    assert len(saved["graph"]["nodes"]) == 8 and saved["runs"][0]["id"] == run.id and saved["graph"]["nodes"][3]["raw"]["negative"] == "blur"
    svc.run_graph(only="upscale_01")
    qtbot.waitUntil(lambda: len(runs) == 2, timeout=30000)
    assert runs[1].mode == "node" and list(runs[1].results) == ["upscale_01"]
    svc.run_matrix("gorsel_01", ("seed", [1, 2]), ("cfg", [4.0, 8.0]))
    qtbot.waitUntil(lambda: len(runs) == 3, timeout=30000)
    assert runs[2].mode == "matrix" and len(runs[2].results) == 4
    svc.load()                                                                                  # proje yeniden yüklenince grafik + geçmiş geri gelir
    assert len(svc.graph.nodes) == 8 and len(svc.runs) == 3 and svc.graph.nodes["gorsel_01"].params["cfg"] == 5.5
    data = svc.analytics()
    assert data.providers and sum(data.totals) > 0.1 and data.tokens["total"] > 0
    ctx.settings.set("plan.budget_usd", 50)
    assert svc.analytics().budget["level"] in ("ok", "warn", "over") and svc.analytics_rows()
    svc.graph.remove("cikis_01")
    got.clear()
    svc.run_graph()
    assert got and got[-1].code == "ai.missing_input"                                            # akış doğrulaması: çıkış düğümü yok


# ============================================================================== sayfalar
def label_texts(page):
    return " ".join(l.text() for l in page.findChildren(QLabel)) + " " + " ".join(b.text() for b in page.findChildren(QPushButton))


def test_simple_page_stage_panels_gate_and_resume_banner(ctx, svc, qtbot):
    ctx.set_ui_mode("simple")
    page = ctx.registry.get("series").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1200, 1000)
    page.show()
    assert "KADRAJ" not in label_texts(page) and not page.grab().isNull() and page.stack.currentWidget() is page.panels[Stage.IDEA] and page.resume_card.isHidden()
    page.title_edit.setText("Sis")
    page.idea_edit.setPlainText("bir vapur gizemi")
    page.idea_next.click()
    assert page.stack.currentWidget() is page.panels[Stage.SCREENPLAY] and "Senaryo" in page.stage_bar.buttons[Stage.SCREENPLAY].text()
    page.script_edit.setPlainText("INT. VAPUR - GÜN\n\nSis her yerde.")
    page.script_ok.click()                                                                       # kaydet + onayla + ilerle
    assert svc.stage is Stage.FORMAT and svc.project.approvals["screenplay"]
    page.kind_box.setCurrentIndex(page.kind_box.findData("dizi"))
    page.episodes.setValue(2)
    page.res_box.setCurrentIndex(page.res_box.findData("2k"))
    page.format_next.click()
    assert svc.stage is Stage.BIBLE and svc.project.format["resolution"] == "2k" and svc.project.format["episodes"] == 2
    page.char_name.setText("Ahmet")
    page.char_desc.setText("navy coat")
    page.char_add.click()
    assert page.char_list.count() == 1 and "Ahmet" in page.char_list.item(0).text()
    page.bible_ok.click()                                                                        # sahne yok → engel kartı, aşama değişmez
    assert page.error_visible and svc.stage is Stage.BIBLE
    svc.add_scene(1, "Güverte", "Ahmet güvertede", location="", characters=["ahmet"])
    page.clear_error()
    page.bible_ok.click()
    assert svc.stage is Stage.STORYBOARD and page.board.card_ids("draft") == ["E01S01"]
    # sürükle-bırak: gerçek drop olayı
    mime = QMimeData()
    mime.setData(MIME, b"E01S01")
    ev = QDropEvent(QPointF(5, 5), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    page.board.columns["approved"].dropEvent(ev)
    assert svc.project.scene("E01S01").story == "approved" and page.board.card_ids("approved") == ["E01S01"]
    page.board.select("E01S01")
    page.board.angle_box.setCurrentIndex(page.board.angle_box.findData("low"))
    page.board.angle_box.activated.emit(page.board.angle_box.currentIndex())
    assert svc.project.scene("E01S01").angle == "low"
    page.board.move_card("E01S01", "approved")
    page.approve_all.click()
    page.board_ok.click()
    assert svc.stage is Stage.PLAN and "$" in page.plan_big.text()
    ctx.settings.set("plan.budget_usd", 20)
    svc.changed.emit()
    assert "Planının" in page.plan_big.text() and "Daha önce" in page.plan_line.text()
    svc.project.scenes[0].prod, svc.project.stage = "paused", Stage.PRODUCTION
    svc.project.paused = {"scene": "E01S01", "code": "ai.quota"}
    svc.changed.emit()
    assert not page.resume_card.isHidden() and "E01S01" in page.resume_text.text() and "Devam" in page.prod_btn.text()
    for mode in ("legend", "simple"):
        ctx.theme.apply(mode, animate=False)
        assert not page.grab().isNull()
    page.stage_bar.stageRequested.emit("bible")                                                  # geriye gitmek serbest
    assert svc.stage is Stage.BIBLE and "storyboard" not in svc.project.approvals


def test_legend_page_node_editor_inspector_seed_raw_and_tabs(ctx, svc, qtbot, world):
    ctx.set_ui_mode("legend")
    page = ctx.registry.get("series").legend_page(ctx)
    qtbot.addWidget(page)
    page.resize(1500, 950)
    page.show()
    assert "KADRAJ" not in label_texts(page) and not page.grab().isNull()
    assert len(page.canvas.items_by_id) == 8 and len(page.canvas.edges) == 7 and "8 düğüm · 7 bağlantı" in page.flow_info.text()
    page.canvas.select("gorsel_01")
    assert page.selected == "gorsel_01" and "Görsel" in page.node_title.text() and {"seed", "cfg", "steps", "ref_strength", "model"} <= set(page.widgets)
    page.widgets["cfg"].setValue(9.5)
    page.widgets["seed_lock"].setChecked(True)
    assert svc.graph.nodes["gorsel_01"].params["cfg"] == 9.5 and svc.graph.nodes["gorsel_01"].params["seed_locked"] is True
    page.widgets["rnd"].click()
    assert page.widgets["seed"].value() == svc.graph.nodes["gorsel_01"].params["seed"] > 0
    page.raw_json.setPlainText("{bozuk")
    page.check_btn.click()
    assert "✗" in page.raw_check.text() and svc.graph.nodes["gorsel_01"].raw["json"] == "{bozuk"
    page.raw_json.setPlainText('{"steps": 40}')
    page.check_btn.click()
    assert "✓" in page.raw_check.text()
    page.raw_prompt.setPlainText("HAM İSTEM")
    assert svc.graph.nodes["gorsel_01"].raw["prompt"] == "HAM İSTEM" and "flux" in page.hint.text()
    # tuvalde bağlantı: LLM çıktısı → ses düğümü girdisi (yeniden bağlama) ve sürükleyerek bağlama
    from PySide6.QtCore import QPointF as P
    from kadraj.modules.series.nodecanvas import port_pos
    svc.disconnect_nodes("ses_01", "text")
    assert page.canvas.edges and len(page.canvas.edges) == 6
    llm = page.canvas.items_by_id["llm_01"]
    ses = page.canvas.items_by_id["ses_01"]
    page.canvas.begin_connect("llm_01", "text", llm.mapToScene(port_pos(P(0, 0), 0, True)))
    assert page.canvas.finish_connect(ses.mapToScene(port_pos(P(0, 0), 0, False)))
    assert svc.graph.nodes["ses_01"].inputs["text"] == ("llm_01", "text") and len(page.canvas.edges) == 7
    page.canvas.deleteRequested.emit("ses_01")
    assert "ses_01" not in page.canvas.items_by_id and len(svc.graph.nodes) == 7
    svc.reset_graph()
    assert len(page.canvas.items_by_id) == 8


@needs_ffmpeg
def test_legend_page_run_history_matrix_analytics_and_quota(ctx, svc, qtbot, world):
    ctx.set_ui_mode("legend")
    ctx.settings.set("plan.budget_usd", 100)
    page = ctx.registry.get("series").legend_page(ctx)
    qtbot.addWidget(page)
    page.resize(1500, 950)
    page.show()
    assert "KOTA" in page.quota_label.text() and "%" in page.quota_label.text()
    page.run_btn.click()
    qtbot.waitUntil(lambda: page.hist_table.rowCount() >= 8, timeout=30000)
    idle(qtbot, svc)
    assert page.canvas.items_by_id["cikis_01"].status == "done" and page.hist_table.item(0, 6).text() in ("done", "skipped")
    assert "son" in page.hist_head.text() and "Oturum" in page.session_label.text() and "$0.00" not in page.session_label.text()
    assert page.analytics.table.rowCount() >= 2 and page.analytics.chart.data.providers and not page.analytics.grab().isNull()
    page.canvas.select("gorsel_01")
    n0 = len([r for r in world.requests if r.url.host == "api.anthropic.com"])
    page.only_btn.click()
    qtbot.waitUntil(lambda: len(svc.runs) == 2, timeout=30000)
    assert len([r for r in world.requests if r.url.host == "api.anthropic.com"]) == n0
    page.mx_node.setCurrentIndex(page.mx_node.findData("gorsel_01"))
    page.mx_xv.setText("5, 6")
    page.mx_yv.setText("4, 8")
    page.mx_btn.click()
    qtbot.waitUntil(lambda: len(svc.runs) == 3, timeout=30000)
    idle(qtbot, svc)
    assert page.mx_table.rowCount() == 2 and page.mx_table.columnCount() == 2 and page.mx_table.item(1, 0).text().startswith("done")
    assert page.mx_table.horizontalHeaderItem(0).text() == "seed=5" and page.mx_table.verticalHeaderItem(1).text() == "cfg=8"
    page.validate_btn.click()
    assert "✓" in page.flow_info.text()
    for mode in ("simple", "legend"):
        ctx.theme.apply(mode, animate=False)
        assert not page.grab().isNull()


def test_export_script_writes_fountain_and_pdf_and_roundtrips_structure(ctx, svc, tmp_path):
    from kadraj.modules.series.service import _screenplay_from_fountain
    svc.set_idea("x", "Sis İçinde")
    svc.set_screenplay(".İÇ. VAPUR - GÜN\n\nSis her yerde.\n\nAHMET\n(fısıldayarak)\nKimse yok mu?\n\n> KESME:\n")
    out = svc.export_script()
    assert Path(out["fountain"]).parent.name == folder_for("script") and Path(out["fountain"]).read_text(encoding="utf-8") == svc.project.screenplay
    assert Path(out["pdf"]).read_bytes()[:4] == b"%PDF" and svc.project.exports["pdf"] == out["pdf"]
    sp = _screenplay_from_fountain("T", svc.project.screenplay)
    assert sp.problems() == [] and [e.kind.value for e in sp.elements][:3] == ["scene", "action", "character"] if hasattr(sp, "elements") else sp.problems() == []
    only_fountain = svc.export_script(pdf=False)
    assert "pdf" not in only_fountain
