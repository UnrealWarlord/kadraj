"""Zaman çizelgesi modeli: zaman kodu, Bezier keyframe, hız eğrisi, (de)serileştirme, yapışma."""
from __future__ import annotations

import pytest

from kadraj.modules.editor import snapping
from kadraj.modules.editor.timeline import (Clip, Keyframe, Marker, Timeline, Track, cubic_bezier, default_timeline,
                                              format_timecode, interpolate, parse_timecode, sample_keyframes,
                                              snap_to_frame)


def test_timecode_roundtrip_and_ntsc():
    assert format_timecode(0, 30) == "00:00:00:00"
    assert format_timecode(3661.5, 30) == "01:01:01:15"
    assert parse_timecode("01:01:01:15", 30) == pytest.approx(3661.5)
    assert parse_timecode("00:02:03", 25) == pytest.approx(2 + 3 / 25)                 # MM:SS:FF biçimi
    assert parse_timecode("12.5") == 12.5
    # 29.97: kare sayacı 30'da sarar
    assert format_timecode(1.0, 29.97) == "00:00:01:00" and format_timecode(0.96, 29.97) == "00:00:00:29"
    assert parse_timecode("00:00:01:00", 29.97) == pytest.approx(1.0, abs=0.04)
    for bad in ("1:99:00:00", "00:00:00:40", "abc", "1:2:3:4:5"):
        with pytest.raises(ValueError):
            parse_timecode(bad, 30)
    assert snap_to_frame(1.01, 30) == pytest.approx(1.0) and snap_to_frame(1.02, 30) == pytest.approx(31 / 30)


def test_cubic_bezier_endpoints_monotonic_and_ease():
    assert cubic_bezier(.42, 0, .58, 1, 0) == 0 and cubic_bezier(.42, 0, .58, 1, 1) == 1
    vals = [cubic_bezier(.42, 0, .58, 1, i / 20) for i in range(21)]
    assert vals == sorted(vals)
    assert cubic_bezier(.42, 0, .58, 1, .5) == pytest.approx(.5, abs=1e-4)
    assert cubic_bezier(.0, .0, 1.0, 1.0, .3) == pytest.approx(.3, abs=1e-4)          # doğrusal
    assert cubic_bezier(.42, 0, 1, 1, .25) < .25                                     # ease-in başta yavaş


def test_interpolate_ease_hold_and_clamp():
    kfs = [Keyframe(0, 0), Keyframe(2, 10, "ease_in_out"), Keyframe(4, 20, "hold"), Keyframe(5, 0)]
    assert interpolate(kfs, -1) == 0 and interpolate(kfs, 9) == 0
    assert interpolate(kfs, 1) == pytest.approx(5.0, abs=0.5)                        # doğrusal aralık ~ orta
    assert interpolate(kfs, 3) == pytest.approx(15.0, abs=.01)                       # ease_in_out orta
    assert interpolate(kfs, 4.5) == 20                                              # hold: ilk değeri tutar
    assert interpolate([], 1, 7.0) == 7.0
    bz = [Keyframe(0, 0, "bezier", (0.1, 0.8, 0.2, 1.0)), Keyframe(1, 1)]
    assert interpolate(bz, .25) > .5                                                # hızlı çıkış
    pts = sample_keyframes(kfs, 4)
    assert pts[0] == (0, 0) and pts[-1] == (5, 0) and [p[0] for p in pts] == sorted(p[0] for p in pts)


def test_clip_duration_speed_and_curve():
    c = Clip("a", "media", "x.mp4", in_=2, out=12, start=1, speed=2.0)
    assert c.duration == 5 and c.end == 6
    assert c.source_time(1.0) == pytest.approx(4.0)
    slow = Clip("b", "media", "x.mp4", in_=0, out=10, start=0, speed_curve=[Keyframe(0, 1.0), Keyframe(100, 1.0)])
    assert slow.duration == pytest.approx(10.0, abs=0.05)
    ramp = Clip("c", "media", "x.mp4", in_=0, out=10, start=0, speed_curve=[Keyframe(0, 1.0), Keyframe(10, 2.0)])
    assert 6.5 < ramp.duration < 7.5                                                # ortalama hız ≈ 1.4
    assert ramp.source_time(ramp.duration) == pytest.approx(10.0, abs=0.1)
    adj = Clip("d", "adjust", duration_hint=3.0, start=2.0)
    assert adj.duration == 3.0 and adj.end == 5.0


def test_timeline_roundtrip_preserves_unknown_fields():
    d = {"tracks": [{"id": "V1", "kind": "video", "custom_track": 5, "clips": [
        {"id": "c1", "kind": "media", "src": "a.mp4", "in": 0, "out": 4, "start": 0, "mystery": {"k": 1},
         "keyframes": {"scale": [{"t": 0, "v": 1.0}, {"t": 2, "v": 1.5, "ease": "bezier", "bezier": [.1, .2, .3, .4]}]}}]}],
         "fps": 24, "future": [1, 2]}
    tl = Timeline.from_dict(d, [{"t": 3.0, "label": "Giriş", "chapter": True}])
    out = tl.to_dict()
    assert out["future"] == [1, 2] and out["tracks"][0]["custom_track"] == 5
    assert out["tracks"][0]["clips"][0]["mystery"] == {"k": 1}
    assert out["tracks"][0]["clips"][0]["keyframes"]["scale"][1]["bezier"] == [.1, .2, .3, .4]
    assert tl.fps == 24 and tl.chapters()[0].label == "Giriş"
    # sınırsız kanal + zorunlu alanlara dayanıklılık
    big = Timeline.from_dict({"tracks": [{"kind": "video"}] * 60 + [{"kind": "bogus"}, "x", None]})
    assert len(big.tracks) == 61 and all(t.kind in ("video", "audio", "fx", "sub", "adj") for t in big.tracks)
    assert Timeline.from_dict(None).tracks == [] and Timeline.from_dict({"tracks": "x"}).tracks == []
    assert Timeline.from_dict({}, settings={"resolution": "1080x1920", "fps": 24}).height == 1920


def test_snapshot_restore_and_unique_ids():
    tl = default_timeline()
    tl.tracks[0].clips.append(Clip("c1", "media", "a.mp4", out=3))
    snap = tl.snapshot()
    tl.tracks.clear()
    tl.restore(snap)
    assert tl.find_clip("c1") is not None
    assert tl.unique_track_id("video") == "V2" and tl.unique_track_id("adj") == "ADJ1"


def test_snapping_edges_playhead_markers_and_move():
    tl = Timeline(tracks=[Track("V1", "video", clips=[Clip("a", "media", "a.mp4", out=4, start=0),
                                                       Clip("b", "media", "b.mp4", out=4, start=10)])],
                  markers=[Marker(20.0)])
    r = snapping.snap_time(tl, 4.05, 0.1)
    assert r.snapped and r.time == 4.0 and r.target == "clip_end"
    assert not snapping.snap_time(tl, 6.0, 0.1).snapped
    assert snapping.snap_time(tl, 6.0, 0.1, playhead=6.05).target == "playhead"
    assert snapping.snap_time(tl, 20.04, 0.1).target == "marker"
    assert not snapping.snap_time(tl, 4.05, 0.1, enabled=False).snapped
    b = tl.find_clip("b")[1]
    mv = snapping.snap_move(tl, b, 4.08, 0.1)                       # b'nin başı a'nın sonuna yapışır
    assert mv.snapped and mv.time == 4.0
    mv2 = snapping.snap_move(tl, tl.find_clip("a")[1], 5.93, 0.1)   # a'nın SONU (5.93+4=9.93) b'nin başına (10) yapışır
    assert mv2.time == pytest.approx(6.0)
    assert snapping.snap_move(tl, b, 30.0, 0.1).time == 30.0
