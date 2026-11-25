"""Proxy, renk hattı/LUT, ölçüm ekranları, EQ/mikser, kare üretimi."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from kadraj.modules.editor import audio, color, frames, ops, proxy, scopes
from kadraj.modules.editor.probe import MediaInfo, probe
from kadraj.modules.editor.timeline import Clip, default_timeline

from .conftest import needs_ffmpeg


def gradient(h=36, w=64):
    r = np.tile(np.linspace(0, 1, w, dtype=np.float32), (h, 1))
    g = np.tile(np.linspace(0, 1, h, dtype=np.float32)[:, None], (1, w))
    return np.stack([r, g, np.full((h, w), 0.5, np.float32)], axis=-1)


def test_cube_roundtrip_and_identity(tmp_path):
    lut = color.identity_lut(5)
    p = color.write_cube(str(tmp_path / "id.cube"), lut)
    back = color.read_cube(p)
    assert np.allclose(back, lut, atol=1e-5)
    img = gradient()
    assert np.allclose(color.apply_lut(img, back), img, atol=1e-3)
    (tmp_path / "bad.cube").write_text("LUT_3D_SIZE 2\n0 0 0\n")
    with pytest.raises(ValueError):
        color.read_cube(str(tmp_path / "bad.cube"))


def test_pipeline_eq_and_match():
    img = color.to_u8(gradient())
    assert color.ColorPipeline().apply(img) is not None and np.array_equal(color.ColorPipeline().apply(img), img)
    up = color.ColorPipeline(brightness=0.1).apply(img)
    assert up.mean() > img.mean()
    gray = color.ColorPipeline(saturation=0.0).apply(img)
    assert np.abs(gray[..., 0].astype(int) - gray[..., 1].astype(int)).max() <= 1
    pipe = color.ColorPipeline.from_effects([{"type": "eq", "params": {"saturation": 1.2}}, {"type": "grayscale", "enabled": False}])
    assert pipe.saturation == pytest.approx(1.2)
    src, ref = gradient() * 0.5, gradient() * 0.9 + 0.05                      # koyu → parlak referans
    lut = color.match_lut(src, ref)
    out = color.apply_lut(src, lut)
    assert abs(float(out.mean()) - float(ref.mean())) < abs(float(src.mean()) - float(ref.mean())) * 0.35
    dull = np.full((20, 20, 3), 0.45, np.float32) + np.random.default_rng(1).normal(0, 0.01, (20, 20, 3)).astype(np.float32)
    vit = color.vitalize_params(dull)
    assert vit["saturation"] > 1.1 and vit["contrast"] > 1.0


def test_scopes_shapes_and_content():
    img = color.to_u8(gradient(90, 160))
    h = scopes.histogram(img)
    assert h.shape == (4, 256) and h[0].sum() == 90 * 160 and h[2].argmax() in (127, 128)
    par = scopes.rgb_parade(img, 80, 64)
    assert par.shape == (3, 64, 80) and par[0].sum() == 90 * 80
    red = np.zeros((20, 20, 3), np.uint8)
    red[..., 0] = 255
    vs = scopes.vectorscope(red, 64)
    assert vs.shape == (64, 64) and vs.sum() == 400 and np.argwhere(vs > 0)[0][0] < 32            # kırmızı = Cr yüksek → üstte
    gray = np.full((20, 20, 3), 128, np.uint8)
    cy, cx = np.argwhere(scopes.vectorscope(gray, 64) > 0)[0]
    assert abs(cx - 31.5) <= 1 and abs(cy - 31.5) <= 1                                               # renksiz = merkez
    st = scopes.stats(np.full((10, 10, 3), 255, np.uint8))
    assert st["clipped_high"] == 1.0 and st["saturation"] == 0.0
    rgba = scopes.to_density_rgba(h[0], (255, 0, 0))
    assert rgba.shape == (256, 4) and rgba[..., 3].max() > 0


def test_eq_curve_and_strips():
    f, flat = audio.eq_curve_db([0] * 10)
    assert np.allclose(flat, 0)
    gains = [0, 0, 0, 0, 0, 6, 0, 0, 0, 0]
    f, db = audio.eq_curve_db(gains)
    assert db.max() == pytest.approx(6.0, abs=0.6) and abs(f[int(db.argmax())] - 1000) < 250
    assert audio.clamp_eq([99, -99] + [0] * 3) == [18.0, -18.0, 0, 0, 0, 0, 0, 0, 0, 0]
    assert set(audio.EQ_PRESETS) >= {"flat", "voice", "bass"} and all(len(v) == 10 for v in audio.EQ_PRESETS.values())
    tl = default_timeline()
    ops.set_track(tl, "A1", volume_db=-6, pan=0.5, channels="swap")
    tl.tracks[2].effects.append({"type": "duck"})
    s = {x.track_id: x for x in audio.strips(tl)}
    assert s["A1"].channels == "swap" and s["A1"].pan == 0.5 and s["A2"].ducked and not s["A1"].ducked
    assert audio.db_to_gain(0) == 1 and audio.gain_to_db(0.5) == pytest.approx(-6.02, abs=.01)


def test_proxy_rules_and_paths(tmp_path):
    big = MediaInfo("x", 10, 3840, 2160, 30, "h264", "aac", 48000, 2, True, True)
    small = MediaInfo("x", 10, 1280, 720, 30, "h264", "aac", 48000, 2, True, True)
    assert proxy.needs_proxy(big) and not proxy.needs_proxy(small)
    assert proxy.needs_proxy(MediaInfo("x", 10, 1280, 720, 30, "hevc", "", 0, 0, True, False))
    assert not proxy.needs_proxy(MediaInfo("x", 1, 4000, 3000, 0, "png", "", 0, 0, True, False, True))
    f = tmp_path / "klip.mp4"
    f.write_bytes(b"1")
    p1 = proxy.proxy_path(tmp_path / "px", str(f))
    assert p1.name.startswith("klip-") and p1.suffixes[-2:] == [".proxy", ".mp4"]
    f.write_bytes(b"22")
    assert proxy.proxy_path(tmp_path / "px", str(f)) != p1                                    # içerik değişince yeni proxy


@needs_ffmpeg
def test_real_proxy_and_proxy_map(media, tmp_path):
    assert proxy.make_proxy(media["a"], tmp_path / "px") is None                               # küçük: gerekmez
    out = proxy.make_proxy(media["a"], tmp_path / "px", force=True)
    info = probe(out)
    assert info.height == 360 and info.has_audio and Path(out).exists() and not Path(out + ".part").exists()
    assert proxy.make_proxy(media["a"], tmp_path / "px", force=True) == out                    # yeniden üretmez
    tl = default_timeline()
    ops.add_clip(tl, "V1", Clip("a", "media", media["a"], out=3))
    assert proxy.proxy_map(tl, tmp_path / "px") == {media["a"]: out}
    with pytest.raises(proxy.RenderCancelled):
        proxy.make_proxy(media["b"], tmp_path / "px2", force=True, cancelled=lambda: True)
    assert not list((tmp_path / "px2").glob("*.part"))


@needs_ffmpeg
def test_extract_frame_before_after_and_scopes(media):
    tl = default_timeline(fps=10, width=160, height=90)
    tl.width, tl.height = 160, 90
    ops.add_clip(tl, "V1", Clip("a", "media", media["a"], out=3.0))
    ops.add_adjust_clip(tl, "ADJ1", 0.0, 3.0, [{"type": "hue", "params": {"saturation": 0}}])
    after = frames.extract_frame(tl, 1.0, size=(160, 90))
    before = frames.extract_frame(tl, 1.0, size=(160, 90), before=True)
    assert after.shape == (90, 160, 3) and before.shape == (90, 160, 3)
    assert np.abs(after[..., 0].astype(int) - after[..., 1].astype(int)).max() < 8              # gri tonlu
    assert np.abs(before[..., 0].astype(int) - before[..., 1].astype(int)).max() > 40           # renkli
    assert scopes.stats(before)["saturation"] > scopes.stats(after)["saturation"]
    assert audio.clip_levels(media["music"])[0] < 0 and audio.clip_levels(media["v"]) == (-120.0, -120.0)
