"""Durum makinesi, yönetmen (LLM adımları), üretim (devamlılık/checkpoint/resume), plan-kota, montaj — sahte HTTP, gerçek FFmpeg."""
from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import httpx
import pytest

from kadraj.ai_engine.export.fountain import Screenplay
from kadraj.ai_engine.pipeline import assembly, director, quota
from kadraj.ai_engine.pipeline.gateway import AIGateway
from kadraj.ai_engine.pipeline.machine import (CAMERA_ANGLES, Character, Line, Location, MachineError, PipelineMachine, Scene,
                                               SeriesProject, Stage)
from kadraj.ai_engine.pipeline.production import Producer, extract_last_frame
from kadraj.ai_engine.providers.base import default_registry
from kadraj.ai_engine.providers.install import install_all
from kadraj.core.contracts import SmartError

from .conftest import FakeDB, FakeSettings, FakeVault, Recorder, make_mp4, png_bytes

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg yok")
PCM = b"\x00\x01" * 4800


def full_project(n_scenes=3) -> SeriesProject:
    p = SeriesProject(title="Sis", idea="vapur", logline="Bir vapur", screenplay="INT. VAPUR - GÜN\n\nAHMET\nMerhaba.",
                      format={"kind": "dizi", "minutes": 5, "episodes": 1, "resolution": "1080p"})
    p.characters = [Character("ahmet", "Ahmet", "a man in a navy coat", "VOICE_A"), Character("ayse", "Ayşe", "a woman with red scarf", "VOICE_B")]
    p.locations = [Location("vapur", "Vapur", "old steam ferry in fog")]
    for i in range(n_scenes):
        sc = Scene(f"E01S{i + 1:02d}", 1, i + 1, f"S{i + 1}", f"Sahne {i + 1}", "vapur", ["ahmet", "ayse"], seconds=4.0, cut=(i == 0))
        sc.lines = [Line("ahmet", f"Merhaba {i}"), Line("ayse", f"Selam {i}")]
        sc.keyframe = ""
        p.scenes.append(sc)
    return p


# ============================================================================== durum makinesi
def test_machine_requires_approvals_and_content_at_each_gate():
    p = SeriesProject()
    m = PipelineMachine(p)
    assert m.blockers() == ["missing_idea"]
    p.idea = "bir vapur"
    assert m.advance() is Stage.SCREENPLAY
    assert set(m.blockers()) == {"needs_approval", "missing_screenplay"}
    with pytest.raises(MachineError) as ei:
        m.advance()
    p.screenplay = "INT. VAPUR"
    with pytest.raises(MachineError) as ei:
        m.advance()                                                                 # senaryo var ama ONAY yok
    assert ei.value.reason == "needs_approval"
    m.approve()
    assert m.advance() is Stage.FORMAT and m.blockers() == ["missing_format"]
    p.format = {"kind": "film", "minutes": 8, "episodes": 1, "resolution": "4k"}
    assert m.advance() is Stage.BIBLE and m.blockers()[0] == "needs_approval"
    p.scenes = full_project().scenes
    m.approve()
    assert m.advance() is Stage.STORYBOARD
    with pytest.raises(MachineError):
        m.approve()                                                                 # kareler onaylanmadan storyboard onaylanamaz
    for s in p.scenes:
        s.story = "approved"
    m.approve()
    assert m.advance() is Stage.PLAN
    assert m.blockers() == ["needs_approval"]
    m.approve()
    assert m.advance() is Stage.PRODUCTION and m.blockers() == ["production_incomplete"]
    for s in p.scenes:
        s.prod = "done"
    assert m.advance() is Stage.ASSEMBLY and m.advance() is Stage.DONE and m.next_stage() is None


def test_machine_going_back_drops_later_approvals_and_content_change_invalidates():
    p = full_project()
    m = PipelineMachine(p)
    p.stage = Stage.PRODUCTION
    p.approvals = {"screenplay": True, "bible": True, "storyboard": True, "plan": True}
    for s in p.scenes:
        s.story = "approved"
    m.goto(Stage.BIBLE)                                                             # geri git: sonrakilerin onayı düşer
    assert m.stage is Stage.BIBLE and p.approvals == {"screenplay": True}
    with pytest.raises(MachineError) as ei:
        m.goto(Stage.PLAN)
    assert ei.value.reason == "cannot_skip"
    p.approvals = {"screenplay": True, "bible": True}
    m.content_changed(Stage.BIBLE)
    assert "bible" not in p.approvals and "screenplay" in p.approvals
    changes = []
    PipelineMachine(p, on_change=changes.append).approve(Stage.SCREENPLAY)
    assert changes and p.updated > 0


def test_project_roundtrip_and_scene_helpers():
    p = full_project()
    p.scenes[0].prod = "done"
    p.scenes[1].lines[0].audio["orig"] = "/x.wav"
    d = json.loads(json.dumps(p.to_dict()))
    q = SeriesProject.from_dict(d)
    assert q.to_dict() == p.to_dict() and q.progress() == (1, 3) and q.next_pending().id == "E01S02"
    assert q.scene("E01S02").lines[0].audio == {"orig": "/x.wav"} and q.character("ayse").voice_id == "VOICE_B"
    assert SeriesProject.from_dict({"stage": "bogus", "scenes": ["x"]}).stage is Stage.IDEA


def test_reset_scene_only_touches_that_scene():
    p = full_project()
    m = PipelineMachine(p)
    for s in p.scenes:
        s.prod, s.clip, s.done_steps = "done", f"/{s.id}.mp4", ["video"]
    p.stage = Stage.ASSEMBLY
    m.reset_scene("E01S02")
    assert [s.prod for s in p.scenes] == ["done", "pending", "done"] and p.scenes[1].clip == "" and p.scenes[2].clip == "/E01S03.mp4"
    assert p.stage is Stage.PRODUCTION


# ============================================================================== yönetmen
def test_normalize_bible_validates_and_repairs_model_output():
    data = {"episodes": [{"index": 1, "title": "B1", "synopsis": "s"}],
            "characters": [{"id": "Ahmet Bey", "name": "Ahmet", "desc": "navy coat"}, {"id": "ahmet_bey", "name": "Kopya", "desc": ""}, {"name": "", "desc": "x"}],
            "locations": [{"id": "Vapur", "name": "Vapur", "desc": "ferry"}],
            "scenes": [{"episode": 1, "title": "A", "summary": "s", "location": "bilinmeyen", "characters": ["Ahmet Bey", "hayalet"], "angle": "zoom!", "seconds": 99,
                        "cut": False, "lines": [{"character": "Ahmet Bey", "text": "Selam"}, {"character": "hayalet", "text": "x"}]},
                       {"episode": 1, "title": "B", "summary": "t", "location": "vapur", "characters": [], "angle": "close", "seconds": 1, "cut": True, "lines": []}]}
    eps, chars, locs, scenes = director.normalize_bible(data)
    assert [c.id for c in chars] == ["ahmet_bey"] and [l.id for l in locs] == ["vapur"]
    assert [s.id for s in scenes] == ["E01S01", "E01S02"] and scenes[0].location == "vapur" and scenes[0].characters == ["ahmet_bey"]
    assert scenes[0].angle == "medium" and scenes[0].seconds == 12.0 and scenes[1].seconds == 3.0 and scenes[1].angle == "close"
    assert scenes[0].cut is True and [l.text for l in scenes[0].lines] == ["Selam"]                  # ilk sahne her zaman kesme


def test_keyframe_spec_and_video_prompt_keep_characters_consistent():
    p = full_project()
    spec = director.keyframe_spec(p, p.scenes[0])
    assert "navy coat" in spec.subject and "red scarf" in spec.subject and spec.angle == CAMERA_ANGLES["medium"] and spec.aspect == "16:9"
    assert "old steam ferry" in spec.lighting
    assert "navy coat" in director.video_prompt(p, p.scenes[1])


def llm_gateway(responses):
    """``responses``: gövdeye göre LLM metni döndüren fonksiyon."""
    def route(r):
        if r.url.host == "api.anthropic.com":
            body = json.loads(r.content)
            return httpx.Response(200, json={"content": [{"type": "text", "text": responses(body)}], "stop_reason": "end_turn",
                                             "usage": {"input_tokens": 10, "output_tokens": 5}})
        return httpx.Response(404)
    rec = Recorder(route)
    reg = default_registry()
    install_all(reg, transport=rec.transport, sleep=lambda s: None)
    return AIGateway(reg, FakeVault(anthropic="sk"), None, db=lambda: None), rec


def test_brainstorm_screenplay_and_bible_via_llm(tmp_path):
    def respond(body):
        props = body["output_config"]["format"]["schema"]["properties"]
        if "ideas" in props:
            return json.dumps({"ideas": [{"title": "Sis", "logline": "Bir vapur sisle kaybolur", "hook": "Kaptan yok"}] * 3})
        if "elements" in props:
            return json.dumps({"title": "Sis", "elements": [{"type": "scene", "text": "INT. VAPUR - GÜN"}, {"type": "action", "text": "Sis her yerde."},
                                                            {"type": "character", "text": "AHMET"}, {"type": "dialogue", "text": "Kimse yok mu?"},
                                                            {"type": "transition", "text": "CUT TO:"}]})
        return json.dumps({"episodes": [{"index": 1, "title": "Sis", "synopsis": "s"}], "characters": [{"id": "ahmet", "name": "Ahmet", "desc": "navy coat"}],
                           "locations": [{"id": "vapur", "name": "Vapur", "desc": "ferry"}],
                           "scenes": [{"episode": 1, "title": "Güverte", "summary": "Ahmet güverteye çıkar", "location": "vapur", "characters": ["ahmet"], "angle": "wide",
                                       "seconds": 6, "cut": True, "lines": [{"character": "ahmet", "text": "Kimse yok mu?"}]}]})
    gw, rec = llm_gateway(respond)
    ideas = director.brainstorm(gw, "vapur")
    assert len(ideas) == 3 and ideas[0]["title"] == "Sis"
    p = SeriesProject(title="Sis", idea="vapur", format={"kind": "film", "minutes": 3, "episodes": 1, "resolution": "1080p"})
    sp = director.write_screenplay(gw, p)
    assert isinstance(sp, Screenplay) and not sp.problems()
    files = director.save_screenplay(sp, tmp_path / "05_Scripts", "sis")
    assert Path(files["fountain"]).read_text(encoding="utf-8").startswith("Title:") and Path(files["pdf"]).read_bytes()[:4] == b"%PDF"
    p.screenplay = Path(files["fountain"]).read_text(encoding="utf-8")
    director.write_bible(gw, p)
    assert [s.id for s in p.scenes] == ["E01S01"] and p.characters[0].name == "Ahmet" and p.scenes[0].angle == "wide"
    models = {json.loads(r.content)["model"] for r in rec.requests}
    assert models == {"claude-haiku-5-5", "claude-opus-5-5"}                       # brainstorm ucuz, yazım güçlü model
    gw2, _ = llm_gateway(lambda b: "json değil")
    with pytest.raises(SmartError) as ei:
        director.brainstorm(gw2, "x")
    assert ei.value.code == "ai.invalid_json"


def test_translate_lines_and_foley_suggestions():
    def respond(body):
        props = body["output_config"]["format"]["schema"]["properties"]
        if "items" in props:
            lines = body["messages"][-1]["content"]
            return json.dumps({"items": [{"i": int(l.split(":")[0]), "text": "EN " + l.split(": ", 1)[1]} for l in lines.splitlines() if l[:1].isdigit()]})
        return json.dumps({"cues": [{"text": "foghorn in the distance", "at": 0.5, "seconds": 3}, {"text": "waves", "at": 0, "seconds": 99}]})
    gw, rec = llm_gateway(respond)
    p = full_project(2)
    n = director.translate_lines(gw, p, ["en"])
    assert n == 4 and p.scenes[0].lines[0].texts == {"en": "EN Merhaba 0"} and p.dub_languages == ["en"]
    assert director.translate_lines(gw, p, ["en"]) == 0                              # tekrar çevrilmez
    cues = director.suggest_foley(gw, p.scenes[0])
    assert [c.text for c in cues] == ["foghorn in the distance", "waves"] and cues[1].seconds == 10.0


# ============================================================================== üretim
class FakeWorld:
    """Video/TTS/SFX sağlayıcılarını sahte yanıtlayan dünya. ``fail_video_at``: o sayıdaki video isteğinde 429/402 verir."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.mp4 = tmp_path / "src.mp4"
        make_mp4(self.mp4, 1.0, "blue")
        self.video_calls = 0
        self.tts_calls = 0
        self.fail_status = None
        self.fail_after = None
        self.images_sent = []

    def route(self, r: httpx.Request) -> httpx.Response:
        if r.url.host == "vid.example":
            if r.method == "POST":
                self.video_calls += 1
                body = json.loads(r.content)
                self.images_sent.append(body.get("image"))
                if self.fail_after is not None and self.video_calls > self.fail_after:
                    return httpx.Response(self.fail_status, json={"error": {"message": "credit balance too low"}})
                return httpx.Response(200, json={"video_url": "https://cdn.vid/x.mp4"})
        if r.url.host == "cdn.vid":
            return httpx.Response(200, content=self.mp4.read_bytes())
        if r.url.host == "api.elevenlabs.io":
            if "sound-generation" not in r.url.path:
                self.tts_calls += 1
            return httpx.Response(200, content=PCM)
        return httpx.Response(404)


@pytest.fixture()
def prod(tmp_path):
    world = FakeWorld(tmp_path)
    rec = Recorder(world.route)
    settings = FakeSettings(**{"providers.video.base_url": "https://vid.example", "providers.video.status_path": "/v/{id}"})
    reg = default_registry(settings)
    install_all(reg, settings=settings, transport=rec.transport, sleep=lambda s: None)
    db = FakeDB()
    gw = AIGateway(reg, FakeVault(video="v", elevenlabs="e"), settings, db=lambda: db)
    return world, gw, db


@needs_ffmpeg
def test_last_frame_extraction(tmp_path):
    clip = tmp_path / "c.mp4"
    make_mp4(clip, 1.0, "green")
    out = extract_last_frame(clip, tmp_path / "f" / "last.png")
    from PIL import Image
    im = Image.open(out).convert("RGB")
    assert im.size == (160, 90) and im.getpixel((5, 5))[1] > 100
    with pytest.raises(SmartError) as ei:
        extract_last_frame(tmp_path / "yok.mp4", tmp_path / "x.png")
    assert ei.value.code == "ai.ffmpeg"


@needs_ffmpeg
def test_production_continuity_tts_per_character_and_foley(prod, tmp_path):
    world, gw, db = prod
    p = full_project(3)
    p.scenes[1].cut = False
    p.scenes[2].cut = True
    p.scenes[2].keyframe = str(tmp_path / "kf.png")
    Path(p.scenes[2].keyframe).write_bytes(png_bytes((20, 20), (1, 2, 3)))
    p.scenes[0].foley = [director.FoleyCue("waves", 0.0, 2.0)] if hasattr(director, "FoleyCue") else []
    p.dub_languages = ["en"]
    for sc in p.scenes:
        for ln in sc.lines:
            ln.texts["en"] = "EN " + ln.text
    m = PipelineMachine(p)
    progress = []
    res = Producer(m, gw, tmp_path / "04", report=lambda f, msg: progress.append(f)).run()
    assert res.status == "done" and res.done == 3 and [s.prod for s in p.scenes] == ["done"] * 3
    assert world.video_calls == 3 and world.tts_calls == 3 * 2 * 2                                  # 3 sahne × 2 satır × (tr + en)
    sent = world.images_sent
    assert sent[0] is None                                                                          # ilk sahne: anahtar kare yok → metinden video
    assert sent[1] is not None and sent[2] is not None
    import base64 as b64
    assert b64.b64decode(sent[1].split(",", 1)[1])[:8] == b"\x89PNG\r\n\x1a\n"                    # S02: önceki sahnenin SON KARESİ
    assert b64.b64decode(sent[2].split(",", 1)[1]) == Path(p.scenes[2].keyframe).read_bytes()      # S03: kesme → anahtar kare
    assert Path(p.scenes[0].last_frame).exists() and p.scenes[0].lines[0].audio.keys() == {"orig", "en"}
    assert all(Path(a).suffix == ".wav" for sc in p.scenes for ln in sc.lines for a in ln.audio.values())
    assert p.scenes[0].foley and Path(p.scenes[0].foley[0].path).suffix == ".wav" and progress[-1] == 1.0
    assert {u["provider"] for u in db.usage} == {"video", "elevenlabs"} and p.progress() == (3, 3)


@needs_ffmpeg
def test_quota_error_freezes_at_scene_then_resume_continues_without_repaying(prod, tmp_path):
    world, gw, db = prod
    p = full_project(4)
    m = PipelineMachine(p)
    notes = []
    world.fail_status, world.fail_after = 402, 2                                                    # 3. video isteğinde kota bitti
    res = Producer(m, gw, tmp_path / "04", notify=lambda k, msg: notes.append((k, msg))).run()
    assert res.status == "paused" and res.scene == "E01S03" and res.error.code == "ai.quota" and res.done == 2
    assert [s.prod for s in p.scenes] == ["done", "done", "paused", "pending"] and p.paused["scene"] == "E01S03" and p.paused["code"] == "ai.quota"
    assert notes and notes[0][0] == "paused"
    assert p.scenes[2].done_steps == [] and not p.scenes[3].clip
    # çökme/yeniden açılış: durum JSON'dan geri yüklenir; yeni kota gelince kaldığı sahneden devam
    restored = PipelineMachine(SeriesProject.from_dict(json.loads(json.dumps(p.to_dict()))))
    world.fail_after = None
    calls_before = world.video_calls
    res2 = Producer(restored, gw, tmp_path / "04").run()
    assert res2.status == "done" and not restored.project.paused
    assert world.video_calls == calls_before + 2                                                    # yalnız S03 ve S04 yeniden istendi (S01/S02 ödenmiş)
    assert [s.prod for s in restored.project.scenes] == ["done"] * 4


@needs_ffmpeg
def test_failure_after_video_does_not_repay_video_on_resume(prod, tmp_path):
    world, gw, db = prod
    p = full_project(1)
    m = PipelineMachine(p)
    gw.registry.get("elevenlabs").transport = Recorder(lambda r: httpx.Response(429, headers={"retry-after": "0"}, json={})).transport
    res = Producer(m, gw, tmp_path / "04").run()
    assert res.status == "paused" and res.error.code == "ai.rate_limit" and p.scenes[0].done_steps == ["video", "last_frame"]
    gw.registry.cool_down("elevenlabs", 0)
    gw.registry.get("elevenlabs").transport = Recorder(world.route).transport
    gw.registry._cool.clear()
    res2 = Producer(m, gw, tmp_path / "04").run()
    assert res2.status == "done" and world.video_calls == 1                                         # video ikinci kez ödenmedi


@needs_ffmpeg
def test_no_tts_provider_skips_audio_but_does_not_freeze_and_cancel_is_clean(prod, tmp_path):
    world, gw, db = prod
    gw.vault = FakeVault(video="v")                                                                 # ElevenLabs anahtarı yok
    p = full_project(2)
    notes = []
    res = Producer(PipelineMachine(p), gw, tmp_path / "04", notify=lambda k, m: notes.append(k)).run()
    assert res.status == "done" and not p.scenes[0].lines[0].audio and "warn" in notes
    p2 = full_project(2)
    stop = {"n": 0}

    def cancelled():
        stop["n"] += 1
        return stop["n"] > 1
    res = Producer(PipelineMachine(p2), gw, tmp_path / "04b", cancelled=cancelled).run()
    assert res.status == "cancelled" and p2.scenes[0].prod != "paused" and not p2.paused


# ============================================================================== plan/kota + montaj
def test_quota_estimate_and_plan_share_text():
    est = quota.estimate(images=4, video_seconds=60, tts_chars=2000, sfx_seconds=10, llm_tokens=60000)
    assert est.cost_usd == pytest.approx(4 * 0.04 + 60 * 0.10 + 2000 * 0.0003 + 10 * 0.01 + 0.72, abs=0.01)
    st = FakeSettings(**{"plan.budget_usd": 100})
    share = quota.plan_share(est, spent=38.0, settings=st)
    assert share.before_pct == 38.0 and share.project_pct == pytest.approx(est.cost_usd, abs=0.06) and share.level == "ok" and "Planının" in share.text
    assert quota.plan_share(est, spent=95.0, settings=st).level == "over" and quota.plan_share(est, spent=90, settings=FakeSettings(**{"plan.budget_usd": 100})).level in ("tight", "over")
    assert quota.plan_share(est, settings=FakeSettings()).level == "none"
    db = FakeDB()
    assert quota.spent_this_month(None) == 0.0


@needs_ffmpeg
def test_assembly_builds_one_undoable_timeline_with_voices_foley_ducking(prod, tmp_path):
    from kadraj.modules.editor.service import EditorService
    world, gw, db = prod
    p = full_project(2)
    p.dub_languages = ["en"]
    for sc in p.scenes:
        for ln in sc.lines:
            ln.texts["en"] = "EN"
    p.scenes[0].foley = [director.FoleyCue("waves", 0.5, 2.0)] if hasattr(director, "FoleyCue") else []
    Producer(PipelineMachine(p), gw, tmp_path / "04").run()
    p.format["resolution"] = "2k"
    ed = EditorService()
    before = ed.tl.snapshot()
    summary = assembly.assemble(ed, p)
    assert summary["scenes"] == 2 and summary["voice_tracks"] == 4 and summary["foley"] and (ed.tl.width, ed.tl.height) == (2560, 1440)
    names = [t.name for t in ed.tl.tracks]
    assert "Dizi · Video" in names and "Ses · Ahmet" in names and "Ses · Ayşe (EN)" in names and "Foley" in names
    en = next(t for t in ed.tl.tracks if t.name == "Ses · Ahmet (EN)")
    assert en.muted and not next(t for t in ed.tl.tracks if t.name == "Ses · Ahmet").muted
    foley = next(t for t in ed.tl.tracks if t.name == "Foley")
    duck = foley.effects[0]
    ids = {t.name: t.id for t in ed.tl.tracks}
    assert duck["type"] == "duck" and set(duck["params"]["keys"]) == {ids["Ses · Ahmet"], ids["Ses · Ayşe"]}
    v = next(t for t in ed.tl.tracks if t.name == "Dizi · Video")
    assert [c.start for c in v.clips][0] == 0 and v.clips[1].start == pytest.approx(v.clips[0].duration, abs=0.01)
    a = next(t for t in ed.tl.tracks if t.name == "Ses · Ahmet")
    assert a.clips[0].start == pytest.approx(0.3) and a.clips[1].start > v.clips[0].duration                   # ikinci sahnenin satırı ikinci kliple hizalı
    ed.undo()
    assert ed.tl.snapshot() == before                                                                     # tek geri alma adımı
    assembly.assemble(ed, p)
    assembly.assemble(ed, p)
    assert sum(1 for t in ed.tl.tracks if t.name == "Dizi · Video") == 1                                   # tekrar montaj çoğaltmaz
    from kadraj.modules.editor import render
    s = render.RenderSettings(width=160, height=90, fps=10, quality="fast", lufs=None)
    plan = render.build_plan(ed.tl, s, str(tmp_path / "out.mp4"))
    assert not plan.warnings
    render.execute_plan(plan)
    assert (tmp_path / "out.mp4").exists()                                                                # montaj gerçekten işlenebilir
