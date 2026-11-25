from __future__ import annotations

import pytest

from kadraj.modules.editor import ops
from kadraj.modules.editor.timeline import Clip, Keyframe, Timeline, default_timeline


def make() -> Timeline:
    tl = default_timeline(fps=30)
    ops.add_clip(tl, "V1", Clip("a", "media", "a.mp4", out=10, start=0))
    ops.add_clip(tl, "V1", Clip("b", "media", "b.mp4", out=5, start=10))
    return tl


def test_split_trim_move_delete_ripple():
    tl = make()
    left, right = ops.split_clip(tl, "a", 4.0)
    assert (left.in_, left.out, left.start) == (0, 4.0, 0) and (right.in_, right.out, right.start) == (4.0, 10, 4.0)
    assert left.end == right.start and right.id != left.id
    with pytest.raises(ops.EditError):
        ops.split_clip(tl, "b", 99)
    ops.trim_clip(tl, "b", "start", 12.0)
    b = tl.find_clip("b")[1]
    assert (b.in_, b.start, b.duration) == (2.0, 12.0, 3.0)
    ops.trim_clip(tl, "b", "end", 14.0)
    assert tl.find_clip("b")[1].out == pytest.approx(4.0)
    ops.delete_clip(tl, right.id, ripple=True)
    assert tl.find_clip("b")[1].start == pytest.approx(6.0)             # 12 - 6 sn ripple
    ops.move_clip(tl, "b", 20.0, "V1")
    assert tl.find_clip("b")[1].start == 20.0


def test_split_distributes_keyframes_and_speed_curve():
    tl = make()
    ops.set_keyframe(tl, "a", "scale", 0, 1.0)
    ops.set_keyframe(tl, "a", "scale", 10, 2.0)
    ops.set_speed_curve(tl, "b", [(0, 1.0), (4, 2.0)])
    left, right = ops.split_clip(tl, "a", 5.0)
    assert left.prop("scale", 5.0, 0) == pytest.approx(1.5, abs=.01) and right.prop("scale", 0.0, 0) == pytest.approx(1.5, abs=.01)
    assert right.prop("scale", 5.0, 0) == pytest.approx(2.0)
    bl, br = ops.split_clip(tl, "b", 10 + 1.0)
    assert br.speed_curve[0].t == 0 and bl.speed_curve[-1].v == pytest.approx(br.speed_curve[0].v)


def test_locked_track_and_validation():
    tl = make()
    ops.set_track(tl, "V1", locked=True)
    for fn in (lambda: ops.move_clip(tl, "a", 3), lambda: ops.delete_clip(tl, "a"), lambda: ops.split_clip(tl, "a", 2)):
        with pytest.raises(ops.LockedError):
            fn()
    with pytest.raises(ops.EditError):
        ops.set_track(tl, "V1", bogus=1)
    with pytest.raises(ops.EditError):
        ops.add_track(tl, "nope")
    ops.set_track(tl, "V1", locked=False)
    with pytest.raises(ops.EditError):
        ops.set_speed(tl, "a", 50)
    assert ops.set_speed(tl, "a", 2.0).duration == 5.0


def test_unlimited_tracks_adj_and_markers_chapters():
    tl = make()
    for _ in range(25):
        ops.add_track(tl, "video")
    assert len(tl.tracks) == 28 and len({t.id for t in tl.tracks}) == 28
    adj = ops.add_adjust_clip(tl, "ADJ1", 2.0, 3.0, [{"type": "eq", "params": {"brightness": .1}}])
    assert adj.duration == 3.0 and tl.track("ADJ1").kind == "adj"
    ops.add_marker(tl, 30.0, "Final", True)
    ops.add_marker(tl, 12.0, "Orta", True)
    ops.add_marker(tl, 5.0, "not", False)
    assert ops.youtube_chapters(tl).splitlines() == ["0:00 Giriş", "0:12 Orta", "0:30 Final"]
    assert ops.chapter_problems(tl) == ["count", "first"]
    ops.add_marker(tl, 0.0, "Başla", True)
    assert ops.chapter_problems(tl) == []
    ops.remove_marker(tl, 12.0)
    assert [m.t for m in tl.chapters()] == [0.0, 30.0] and ops.chapter_problems(tl) == ["count"]


def test_freeze_frame_inserts_and_shifts():
    tl = make()
    fz = ops.freeze_frame(tl, "a", 4.0, 2.0)
    a, b = tl.find_clip("a")[1], tl.find_clip("b")[1]
    assert fz.kind == "freeze" and fz.start == 4.0 and fz.duration == 2.0 and fz.in_ == 4.0
    assert a.end == 4.0 and b.start == pytest.approx(12.0)
    assert sorted(c.start for c in tl.track('V1').clips) == [0.0, 4.0, 6.0, 12.0]      # sol, donuk kare, sağ yarı, b
