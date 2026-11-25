"""Otomasyonlar: öner → uygula (geri alınabilir), ağır paket yoksa 'paketi kur' eylemli SmartError."""
from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from kadraj.core.contracts import SmartError
from kadraj.modules.editor import automations as au
from kadraj.modules.editor import ops, render
from kadraj.modules.editor.probe import probe
from kadraj.modules.editor.timeline import Clip, Keyframe, Timeline, default_timeline

from .conftest import FFMPEG, needs_ffmpeg


@dataclass
class Seg:
    start: float
    end: float
    text: str


def tl_with(media, key="a", out=3.0) -> Timeline:
    tl = default_timeline(fps=10, width=160, height=90)
    tl.width, tl.height = 160, 90
    ops.add_clip(tl, "V1", Clip("a", "media", media[key], out=out, start=0))
    return tl


def test_keep_ranges_and_cut_apply():
    assert au.keep_ranges(0, 10, [(2, 4), (6, 7)]) == [(0, 2), (4, 6), (7, 10)]
    assert au.keep_ranges(0, 10, [(0, 10)]) == [] and au.keep_ranges(0, 10, []) == [(0, 10)]
    assert au.keep_ranges(0, 10, [(-5, 2), (9, 20)]) == [(2, 9)]
    tl = default_timeline(fps=10)
    ops.add_clip(tl, "V1", Clip("a", "media", "x.mp4", out=10, start=0))
    ops.add_clip(tl, "V1", Clip("n", "media", "y.mp4", out=5, start=10))
    before = tl.snapshot()
    au.cut_ranges_apply("a", [(2, 4), (6, 7)])(tl)
    starts = sorted((c.start, c.in_, c.out) for c in tl.track("V1").clips if c.src == "x.mp4")
    assert starts == [(0, 0, 2), (2, 4, 6), (4, 7, 10)]
    assert tl.find_clip("n")[1].start == pytest.approx(7.0)
    assert tl.duration == pytest.approx(12.0)
    tl.restore(before)                                                        # geri alma = anlık görüntü
    assert tl.find_clip("n")[1].start == 10 and len(tl.track("V1").clips) == 2
    ops.set_track(tl, "V1", locked=True)
    with pytest.raises(ops.LockedError):
        au.cut_ranges_apply("a", [(1, 2)])(tl)


def test_parse_silencedetect():
    txt = "[silencedetect @ 0x1] silence_start: 1.02\n[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1.48\nsilence_start: 4.0\n"
    assert au.parse_silencedetect(txt) == [(1.02, 2.5), (4.0, float("inf"))]


@needs_ffmpeg
def test_silence_cutter_real(tmp_path):
    wav = str(tmp_path / "talk.wav")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=500:d=1", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-f", "lavfi", "-i", "sine=f=500:d=1", "-filter_complex",
                    "[1:a]atrim=0:1.5[s];[0:a][s][2:a]concat=n=3:v=0:a=1", "-ar", "48000", "-ac", "2", wav], check=True, timeout=60)
    sil = au.detect_silences(wav, noise_db=-40, min_silence=0.5)
    assert len(sil) == 1 and sil[0][0] == pytest.approx(1.0, abs=0.1) and sil[0][1] == pytest.approx(2.5, abs=0.1)
    tl = default_timeline(fps=10)
    ops.add_clip(tl, "A2", Clip("v", "media", wav, out=3.5, start=0))
    prop = au.plan_silence_cut(tl, "v", noise_db=-40, min_silence=0.5, pad=0.1)
    assert prop.details["count"] == 1 and prop.details["seconds"] == pytest.approx(1.3, abs=0.2) and not prop.empty
    assert tl.duration == 3.5                                   # analiz zaman çizelgesini DEĞİŞTİRMEZ
    prop.apply(tl)
    assert tl.duration == pytest.approx(2.2, abs=0.25)
    quiet = au.plan_silence_cut(tl, "v", silences=[])
    assert quiet.empty


def test_studio_voice_uses_mastering_chain(media, tmp_path):
    tl = tl_with(media)
    calls = []

    def fake_master(path, **kw):
        calls.append(Path(path).name)
        out = tmp_path / "voice_master.wav"
        out.write_bytes(b"RIFF")
        return types.SimpleNamespace(path=out, duration_s=2.9)
    prop = au.studio_voice(tl, "a", tmp_path / "wav", mastering_fn=fake_master)
    assert calls and calls[0].endswith("_raw.wav") and not (tmp_path / "wav" / calls[0]).exists()         # ham ses temizlendi
    snap = tl.snapshot()
    prop.apply(tl)
    assert tl.find_clip("a")[1].data["audio_muted"] is True
    voice = [c for t in tl.tracks_of("audio") for c in t.clips]
    assert len(voice) == 1 and voice[0].src.endswith("voice_master.wav") and voice[0].start == 0
    tl.restore(snap)
    assert not tl.find_clip("a")[1].data.get("audio_muted") and not [c for t in tl.tracks_of("audio") for c in t.clips]
    with pytest.raises(SmartError) as ei:
        au.studio_voice(tl_with(media, "v"), "a", tmp_path / "w2", mastering_fn=fake_master)              # sessiz klip
    assert ei.value.code == "editor.no_source"


def test_ducking_color_lut(media, tmp_path):
    tl = default_timeline()
    prop = au.plan_ducking(tl, "A1", ["A2"])
    prop.apply(tl)
    prop.apply(tl)                                               # ikinci uygulama çoğaltmaz
    ducks = [e for e in tl.track("A1").effects if e["type"] == "duck"]
    assert len(ducks) == 1 and ducks[0]["params"]["keys"] == ["A2"]
    with pytest.raises(SmartError):
        au.plan_ducking(tl, "A1", ["yok"])
    tl = tl_with(media)
    dull = np.full((20, 20, 3), 110, np.uint8)
    v = au.plan_vitalize(tl, "a", frame=dull)
    v.apply(tl)
    assert any(e["type"] == "eq" and e["params"]["saturation"] > 1 for e in tl.find_clip("a")[1].effects)
    ops.add_clip(tl, "V1", Clip("r", "media", media["b"], out=2, start=3))
    m = au.plan_color_match(tl, "a", "r", tmp_path / "luts", src_frame=np.full((20, 20, 3), 80, np.uint8),
                            ref_frame=np.full((20, 20, 3), 180, np.uint8))
    m.apply(tl)
    cube = next(e["params"]["file"] for e in tl.find_clip("a")[1].effects if e["type"] == "lut")
    assert Path(cube).exists() and cube.endswith(".cube")
    l = au.plan_lut(tl, "a", cube)
    l.apply(tl)
    with pytest.raises(ValueError):
        (tmp_path / "bad.cube").write_text("x")
        au.plan_lut(tl, "a", str(tmp_path / "bad.cube"))


def test_optional_packages_raise_install_action(media, monkeypatch):
    tl = tl_with(media)
    for mod, fn, key, pkg in [("librosa", lambda: au.detect_beats(media["music"]), "opt_librosa", "librosa"),
                              ("cv2", lambda: au.track_subject(media["a"], 0, 1), "opt_opencv", "opencv-python-headless"),
                              ("rembg", lambda: au.remove_background(media["a"], "/tmp/x.mov"), "opt_rembg", "rembg onnxruntime")]:
        monkeypatch.setitem(sys.modules, mod, None)                 # içe aktarma ImportError verir
        with pytest.raises(SmartError) as ei:
            fn()
        e = ei.value
        assert e.code == f"editor.{key}" and pkg in e.summary
        act = e.actions[0]
        assert act.kind == "copy" and act.payload["text"] == f"pip install {pkg}" and act.label == "İsteğe bağlı paketi kur"


def test_beat_sync_with_fake_librosa(media, monkeypatch):
    fake = types.SimpleNamespace(load=lambda p, sr, mono: (np.zeros(10), sr),
                                 beat=types.SimpleNamespace(beat_track=lambda y, sr: (120.0, np.arange(8))),
                                 frames_to_time=lambda b, sr: np.arange(len(b)) * 0.5)
    monkeypatch.setitem(sys.modules, "librosa", fake)
    assert au.detect_beats(media["music"]) == (120.0, [i * 0.5 for i in range(8)])
    tl = default_timeline(fps=10)
    ops.add_clip(tl, "A1", Clip("m", "media", media["music"], out=4.0, start=0))
    ops.add_clip(tl, "V1", Clip("x", "media", media["a"], out=3.0, start=0))
    ops.add_clip(tl, "V1", Clip("y", "media", media["b"], out=3.0, start=3.0))
    ops.add_clip(tl, "V1", Clip("z", "media", media["a"], out=3.0, start=6.0))
    prop = au.plan_beat_sync(tl, "m", ["x", "y", "z"], every=2)                 # vuruşlar 0,1,2,3 sn
    prop.apply(tl)
    assert [tl.find_clip(i)[1].start for i in "xyz"][:3] == [0.0, 1.0, 2.0]
    assert tl.find_clip("x")[1].duration == pytest.approx(1.0) and tl.find_clip("y")[1].duration == pytest.approx(1.0)
    one = default_timeline(fps=10)
    ops.add_clip(one, "A1", Clip("m", "media", media["music"], out=4.0, start=0))
    ops.add_clip(one, "V1", Clip("x", "media", media["a"], out=3.0, start=0))
    au.plan_beat_sync(one, "m", ["x"], every=1, beats=[0, 1, 2]).apply(one)
    assert len(one.track("V1").clips) == 3 and one.track("V1").duration == pytest.approx(3.0)


def test_reframe_follows_subject(media):
    tl = tl_with(media)
    prop = au.plan_reframe(tl, "a", track=[(0.0, 0.2), (1.0, 0.8), (2.0, 0.5)])
    prop.apply(tl)
    c = tl.find_clip("a")[1]
    assert (tl.width, tl.height) == (1080, 1920)
    zoom = c.keyframes["scale"][0].v
    assert zoom == pytest.approx(1920 / (1080 * 90 / 160), abs=0.01)
    xs = [k.v for k in c.keyframes["x"]]
    assert xs[0] > 0 > xs[1] and xs[2] == 0 and max(abs(x) for x in xs) <= (zoom - 1) / 2 + 1e-6


@needs_ffmpeg
def test_green_screen_pipeline_with_fake_rembg(media, tmp_path, monkeypatch):
    removed = []

    class FakeRembg:
        @staticmethod
        def remove(img):
            removed.append(1)
            out = img.convert("RGBA")
            out.putalpha(128)
            return out
    monkeypatch.setitem(sys.modules, "rembg", FakeRembg)
    tl = tl_with(media, out=1.0)
    prop = au.plan_green_screen(tl, "a", tmp_path / "out")
    assert len(removed) == 10                                              # 1 sn @ 10 fps
    info = probe(prop.details["cutout"])
    assert info.vcodec == "prores" and info.width == 160
    prop.apply(tl)
    c = tl.find_clip("a")[1]
    assert c.src.endswith("_cutout.mov") and c.data["original_src"] == media["a"]
    with pytest.raises(RuntimeError):
        au.remove_background(media["a"], str(tmp_path / "c.mov"), cancelled=lambda: True)
    assert not (tmp_path / "c.mov.part").exists()


@needs_ffmpeg
def test_dynamic_subtitles_watermark_freeze_render(media, tmp_path):
    tl = tl_with(media)
    segs = [Seg(0.2, 1.2, "Merhaba dünya nasılsın"), Seg(1.4, 2.6, "Bugün video çekiyoruz")]
    ass = au.build_ass(segs, 160, 90)
    assert ass.count("Dialogue:") == 2 and "{\\k" in ass and "Merhaba" in ass
    sp = au.plan_dynamic_subtitles(tl, "a", segs, tmp_path / "subs")
    sp.apply(tl)
    sub = tl.tracks_of("sub")[0].clips[0]
    assert sub.data["ass"].endswith(".ass") and sub.duration == 3.0
    assert au.plan_dynamic_subtitles(tl, "a", [], tmp_path).empty
    wm = au.plan_watermark(tl, media["pic"], corner="br", image_size=(64, 64))
    wm.apply(tl)
    layer = [c for t in tl.tracks_of("video") for c in t.clips if c.name == "Filigran"][0]
    assert layer.keyframes["x"][0].v > 0 and layer.keyframes["y"][0].v > 0 and layer.keyframes["opacity"][0].v == 0.75
    tl2 = tl_with(media)
    au.plan_watermark(tl2, media["pic"], corner="tl", image_size=(64, 64)).apply(tl2)
    tl_layer = [c for t in tl2.tracks_of("video") for c in t.clips if c.name == "Filigran"][0]
    assert tl_layer.keyframes["x"][0].v < 0 and tl_layer.keyframes["y"][0].v < 0
    with pytest.raises(ops.EditError):
        au.plan_watermark(tl2, media["pic"], corner="zz", image_size=(1, 1))
    au.plan_freeze(tl, "a", 1.0, 1.0).apply(tl)
    assert any(c.kind == "freeze" for _t, c in tl.all_clips())
    s = render.RenderSettings(width=160, height=90, fps=10, quality="fast", lufs=None)
    plan = render.build_plan(tl, s, str(tmp_path / "all.mp4"))
    assert not plan.warnings, plan.warnings
    out = render.execute_plan(plan)
    assert probe(out).duration == pytest.approx(tl.duration, abs=0.4)


def test_screen_recording_command_uses_gdigrab_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    cmd = au.recording_command("screen", "x.mp4", ffmpeg="ffmpeg", env={})      # DISPLAY gerekmez
    assert "gdigrab" in cmd and "desktop" in cmd and "x11grab" not in cmd
    with pytest.raises(SmartError) as ei:                                      # web kamerası Windows'ta henüz yok → anlaşılır hata
        au.recording_command("webcam", "x.mp4", ffmpeg="ffmpeg", env={})
    assert ei.value.code == "editor.no_source"


@needs_ffmpeg
def test_screen_and_webcam_recording_command_and_run(tmp_path):
    if sys.platform != "win32":                                                  # x11grab/DISPLAY yalnızca Linux'ta
        with pytest.raises(SmartError) as ei:
            au.recording_command("screen", "x.mp4", env={})                      # DISPLAY yok
        assert ei.value.code == "editor.no_source"
        cmd = au.recording_command("screen", "x.mp4", env={"DISPLAY": ":0"})
        assert "x11grab" in cmd and ":0" in cmd
    out = str(tmp_path / "rec.mp4")
    rec = au.start_recording("webcam", out, source=["-f", "lavfi", "-i", "testsrc=s=160x90:r=10"])
    import time
    time.sleep(1.2)
    assert rec.running
    assert rec.stop() == out
    info = probe(out)
    assert info.has_video and info.width == 160 and info.duration > 0.3 and not rec.running


def test_text_edit_proposal(media):
    tl = tl_with(media)
    p = au.plan_text_edit(tl, "a", [(0.5, 1.0), (2.0, 2.5)])
    assert p.details["count"] == 2 and p.details["seconds"] == pytest.approx(1.0)
    p.apply(tl)
    assert tl.duration == pytest.approx(2.0)
    assert au.plan_text_edit(tl, "a", []).empty
