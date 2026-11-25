"""FFmpeg işleme motoru: grafik üretimi, komut kurma, gerçek (sentetik klipli) işleme."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kadraj.core import i18n
from kadraj.modules.editor import ops, render
from kadraj.modules.editor.filtergraph import (atempo_chain, build_graph, pw_expr, speed_segments, window)
from kadraj.modules.editor.probe import probe
from kadraj.modules.editor.timeline import Clip, Keyframe, Timeline, default_timeline

from .conftest import FFMPEG, needs_ffmpeg


def ffprobe_json(path: str) -> dict:
    r = subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        "-show_chapters", path], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def small(tl: Timeline) -> Timeline:
    tl.width, tl.height, tl.fps = 160, 90, 10
    return tl


def basic(media) -> Timeline:
    tl = small(default_timeline(fps=10, width=160, height=90))
    ops.add_clip(tl, "V1", Clip("a", "media", media["a"], out=3.0, start=0))
    ops.add_clip(tl, "V1", Clip("b", "media", media["b"], out=2.0, start=3.0))
    ops.add_clip(tl, "A1", Clip("m", "media", media["music"], out=5.0, start=0, volume_db=-12))
    return tl


def test_expression_helpers():
    assert pw_expr([(0, 1)]) == "1"
    e = pw_expr([(0, 0), (2, 10)], "t", 5)
    assert e.startswith("if(lt(t,5),0,") and "(t-5)/2" in e
    assert atempo_chain(1.0) == [] and atempo_chain(0.25) == ["atempo=0.5", "atempo=0.5"] and atempo_chain(4)[0] == "atempo=2.0"
    c = Clip("x", "media", "a.mp4", out=10, speed_curve=[Keyframe(0, 1.0), Keyframe(10, 2.0)])
    segs = speed_segments(c)
    assert len(segs) > 4 and segs[0][0] == 0 and segs[-1][1] == pytest.approx(10.0) and segs[-1][2] > segs[0][2]
    assert all(b > a for a, b, _ in segs)


@needs_ffmpeg
def test_probe_media(media):
    a = probe(media["a"])
    assert (a.width, a.height) == (160, 90) and a.has_video and a.has_audio and a.fps == pytest.approx(10) and a.duration == pytest.approx(3, abs=.2)
    assert probe(media["v"]).has_audio is False and probe(media["music"]).kind == "audio" and probe(media["pic"]).is_image


@needs_ffmpeg
def test_graph_structure_and_window(media):
    tl = basic(media)
    g = build_graph(tl)
    assert g.video_label == "[vout]" and g.audio_label == "[aout]" and g.duration == pytest.approx(5.0)
    assert "overlay=" in g.filter_complex and "amix=" in g.filter_complex and "volume=-12dB" in g.filter_complex
    w = window(tl, 1.0, 4.0)
    assert w.duration == pytest.approx(3.0) and w.find_clip("a")[1].in_ == pytest.approx(1.0) and w.find_clip("b")[1].start == pytest.approx(2.0)
    assert w.find_clip("b")[1].duration == pytest.approx(1.0)
    with pytest.raises(Exception) as ei:
        build_graph(small(default_timeline()))
    assert getattr(ei.value, "code", "") == "editor.empty_timeline"


@needs_ffmpeg
def test_real_render_h264_mp4_with_chapters(media, tmp_path):
    """Gerçek FFmpeg: iki video + müzik → mp4; süre, akışlar, boyut ve bölümler doğru."""
    tl = basic(media)
    ops.add_marker(tl, 0, "Giriş", True)
    ops.add_marker(tl, 3, "Orta", True)
    s = render.template_settings("youtube")
    s.width, s.height, s.fps, s.quality = 160, 90, 10, "fast"
    plan = render.build_plan(tl, s, str(tmp_path / "out.mp4"))
    assert plan.encoder.backend == "cpu" and plan.encoder.name == "libx264" and len(plan.commands) == 1
    seen = []
    out = render.execute_plan(plan, report=lambda p, m: seen.append(p))
    info = ffprobe_json(out)
    kinds = {s_["codec_type"]: s_ for s_ in info["streams"]}
    assert kinds["video"]["codec_name"] == "h264" and kinds["video"]["width"] == 160 and kinds["audio"]["codec_name"] == "aac"
    assert float(info["format"]["duration"]) == pytest.approx(5.0, abs=0.3)
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Giriş", "Orta"]
    assert seen and seen[-1] == 1.0 and not Path(plan.part).exists()


@needs_ffmpeg
def test_real_render_two_pass_cbr_h265_prores_wav(media, tmp_path):
    tl = basic(media)
    for name, mut in {"h264_2p": dict(codec="h264", passes=2, bitrate_kbps=300, rate_mode="vbr"),
                      "h264_cbr": dict(codec="h264", rate_mode="cbr", bitrate_kbps=250),
                      "h265": dict(codec="h265"), "prores": dict(codec="prores")}.items():
        s = render.RenderSettings(width=160, height=90, fps=10, quality="fast", **mut)
        plan = render.build_plan(tl, s, str(tmp_path / f"{name}{s.ext}"))
        assert len(plan.commands) == (2 if mut.get("passes") == 2 else 1)
        out = render.execute_plan(plan)
        v = next(x for x in ffprobe_json(out)["streams"] if x["codec_type"] == "video")
        assert v["codec_name"] == {"h264_2p": "h264", "h264_cbr": "h264", "h265": "hevc", "prores": "prores"}[name], name
    p = render.build_plan(tl, render.template_settings("podcast"), str(tmp_path / "pod.wav"))
    out = render.execute_plan(p)
    st = ffprobe_json(out)["streams"]
    assert len(st) == 1 and st[0]["codec_name"] == "pcm_s24le" and out.endswith(".wav")


@needs_ffmpeg
def test_real_render_keyframes_effects_adj_speed_freeze_text(media, tmp_path):
    """Her özellik gerçek FFmpeg ile bir kez çalışır: keyframe'li scale/x/rotate/opacity, ADJ + efekt, hız eğrisi, dondur, metin, görsel."""
    tl = small(default_timeline(fps=10, width=160, height=90))
    a = ops.add_clip(tl, "V1", Clip("a", "media", media["a"], out=3.0, start=0))
    ops.set_keyframe(tl, "a", "scale", 0, 1.0, "ease_in_out")
    ops.set_keyframe(tl, "a", "scale", 2, 1.4)
    ops.set_keyframe(tl, "a", "x", 0, -0.1, "bezier", (0.1, 0.8, 0.2, 1.0))
    ops.set_keyframe(tl, "a", "x", 2, 0.1)
    ops.set_keyframe(tl, "a", "rotate", 0, 0)
    ops.set_keyframe(tl, "a", "rotate", 2, 10)
    ops.set_keyframe(tl, "a", "opacity", 0, 0.5)
    ops.set_keyframe(tl, "a", "opacity", 2, 1.0)
    ops.add_effect(tl, "a", {"type": "eq", "params": {"saturation": 1.3}})
    ops.set_clip(tl, "a", fade_in=0.3, fade_out=0.3)
    b = ops.add_clip(tl, "V1", Clip("b", "media", media["b"], out=3.0, start=3.0))
    ops.set_speed_curve(tl, "b", [(0, 1.0), (2, 2.0)])
    ops.add_clip(tl, "V1", Clip("p", "image", media["pic"], start=5.5, duration_hint=1.0))
    tl.tracks[0].clips[-1].out = 1.0
    ops.freeze_frame(tl, "a", 1.0, 0.5)
    t = ops.add_track(tl, "sub")
    ops.add_clip(tl, t.id, Clip("t", "text", start=0.5, duration_hint=1.5, data={"text": "Merhaba: 'dünya', 100%"}))
    ops.add_adjust_clip(tl, "ADJ1", 0.5, 2.0, [{"type": "blur", "params": {"amount": 3}}, {"type": "vignette"}, {"type": "hue", "params": {"degrees": 30}}])
    ops.add_clip(tl, "A1", Clip("m", "media", media["music"], out=4.0, start=0))
    ops.add_clip(tl, "A2", Clip("vo", "media", media["voice"], out=2.0, start=1.0))
    ops.set_track(tl, "A1", pan=-0.3, volume_db=-3)
    tl.tracks[1].effects.append({"type": "duck", "params": {"keys": ["A2"]}})
    tl.eq = [0, 0, 0, 2, 0, 0, -3, 0, 0, 0]
    s = render.RenderSettings(width=160, height=90, fps=10, quality="fast", lufs=-16.0)
    plan = render.build_plan(tl, s, str(tmp_path / "all.mp4"))
    assert not plan.warnings, plan.warnings
    out = render.execute_plan(plan)
    info = ffprobe_json(out)
    assert float(info["format"]["duration"]) == pytest.approx(tl.duration, abs=0.4)
    assert {s_["codec_type"] for s_ in info["streams"]} == {"video", "audio"}


def test_hardware_choice_order_and_settings(media):
    render.set_hardware_override({"nvenc": True, "vaapi": True})
    assert render.choose_encoder("h264").name == "h264_nvenc" and render.choose_encoder("h265").name == "hevc_nvenc"
    render.set_hardware_override({"nvenc": False, "vaapi": True})
    assert render.choose_encoder("h264").backend == "vaapi" and render.choose_encoder("h264", "cpu").name == "libx264"
    assert render.choose_encoder("h264", "nvenc").backend == "cpu"                # istenen yok → CPU
    assert render.choose_encoder("prores").name == "prores_ks" and render.choose_encoder("wav").backend == "cpu"
    render.set_hardware_override({"nvenc": False, "vaapi": False})
    assert render.choose_encoder("h264").backend == "cpu"
    s = render.RenderSettings.from_dict({"codec": "bogus", "passes": 5, "rate_mode": "x", "unknown": 1})
    assert (s.codec, s.passes, s.rate_mode) == ("h264", 1, "vbr")


def test_hardware_commands_without_running(media):
    tl = basic(media) if FFMPEG else None
    if tl is None:
        pytest.skip("ffmpeg yok")
    render.set_hardware_override({"nvenc": True, "vaapi": True})
    cmd = render.build_plan(tl, render.RenderSettings(passes=2, bitrate_kbps=4000), "/tmp/x.mp4").commands[0]
    assert "h264_nvenc" in cmd and "-multipass" in cmd and "-rc" in cmd
    render.set_hardware_override({"nvenc": False, "vaapi": True})
    plan = render.build_plan(tl, render.RenderSettings(passes=2), "/tmp/x.mp4")
    assert "h264_vaapi" in plan.commands[0] and "-vaapi_device" in plan.commands[0] and "hwupload" in plan.commands[0][plan.commands[0].index("-filter_complex") + 1]
    assert "two_pass_unsupported" in plan.warnings
    plan.graph.cleanup()


def test_templates_quality_estimates():
    assert list(render.TEMPLATES) == ["youtube", "shorts", "reels", "tiktok", "podcast", "whatsapp", "archive"]
    for tid in render.TEMPLATES:
        assert i18n.has(f"editor.tpl.{tid}") and i18n.has(f"editor.tpl.{tid}.desc")
    assert render.template_settings("shorts").height == 1920 and render.template_settings("podcast").ext == ".wav"
    assert render.template_settings("archive").ext == ".mov" and render.template_settings("whatsapp").max_size_mb == 15.5
    ests = {q: render.estimate(render.RenderSettings(quality=q), 600, 1920, 1080, 30) for q in render.QUALITIES}
    assert ests["fast"].seconds < ests["balanced"].seconds < ests["heavy"].seconds              # ağır daha yavaş
    assert ests["fast"].size_bytes > ests["balanced"].size_bytes > ests["heavy"].size_bytes     # ağır daha küçük
    assert render.estimate(render.RenderSettings(), 600, 1920, 1080, 30, "nvenc").seconds < ests["balanced"].seconds
    wa = render.template_settings("whatsapp")
    e = render.estimate(wa, 120, wa.width, wa.height, 30)
    assert e.size_bytes <= 15.5 * 1024 * 1024 * 1.02
    long = render.estimate(wa, 300, wa.width, wa.height, 30)                                      # çok uzun → bit hızı kısılır
    assert long.size_bytes <= 15.5 * 1024 * 1024 * 1.1 and long.video_kbps < e.video_kbps


@needs_ffmpeg
def test_cancel_and_failure_cleanup(media, tmp_path):
    tl = basic(media)
    plan = render.build_plan(tl, render.RenderSettings(width=160, height=90, fps=10, quality="fast"), str(tmp_path / "c.mp4"))
    with pytest.raises(render.RenderCancelled):
        render.execute_plan(plan, cancelled=lambda: True)
    assert not Path(plan.part).exists() and not Path(plan.output).exists()
    bad = small(default_timeline(fps=10))
    ops.add_clip(bad, "V1", Clip("x", "media", str(tmp_path / "yok.mp4"), out=2.0))
    with pytest.raises(Exception) as ei:
        render.build_plan(bad, render.RenderSettings(), str(tmp_path / "b.mp4"))
    assert ei.value.code == "editor.missing_media"
    err = render.map_ffmpeg_error("No space left on device", 1)
    assert err.code == "editor.disk_full" and render.map_ffmpeg_error("Unknown encoder 'x'", 1).code == "editor.no_encoder"


def test_hardware_detection_never_blocks_the_ui_thread(monkeypatch):
    """Arayüz yolu (``block=False``) gerçek algılama sürerken beklemez: önce "donanım yok", bitince gerçek sonuç."""
    import threading
    gate = threading.Event()
    calls = []

    def slow_probe(args, timeout=20.0):
        calls.append(args)
        gate.wait(5)
        return True
    render.set_hardware_override(None)
    monkeypatch.setattr(render, "_probe_encode", slow_probe)
    monkeypatch.setattr(render.Path, "exists", lambda self: True)
    done = threading.Event()
    first = render.detect_hardware(refresh=True, block=False)
    assert first == {"nvenc": False, "vaapi": False} and render.choose_encoder("h264", block=False).backend == "cpu"
    render.start_hardware_detection(done.set)                      # sürüyor: ikinci iş parçacığı açılmaz
    assert not done.is_set()
    gate.set()
    render._hw_thread.join(5)
    assert render.detect_hardware(block=False) == {"nvenc": True, "vaapi": True} and len(calls) == 2
    assert render.choose_encoder("h264", block=False).name == "h264_nvenc"
    with render._hw_lock:                                          # temizlik: sahte sonuç süreç boyunca kalmasın
        render._hw_result = None
