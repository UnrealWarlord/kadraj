"""EditorService: proje kalıcılığı, geri al/yinele, yapışma, kilit, işler (gerçek FFmpeg), otomasyon öner→uygula."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QUrl

from kadraj.core.contracts import JobStatus, ServiceBase, SmartError
from kadraj.modules.editor import render
from kadraj.modules.editor.service import JOB_RENDER, EditorService
from kadraj.storage.folders import folder_for

from .conftest import needs_ffmpeg


@pytest.fixture()
def svc(ctx, tmp_path, qtbot):
    ctx.project.create(tmp_path / "projeler", "Deneme Projesi")
    s = ctx.service("editor")
    assert isinstance(s, EditorService)
    s.reload("load")
    return s


def errors(s):
    got = []
    s.errorRaised.connect(got.append)
    return got


def test_registered_and_contract(ctx):
    spec = ctx.registry.get("editor")
    assert spec.id == "editor" and spec.accent.value == "mint" and spec.workspace == "edit" and spec.chip == ".mp4"
    assert isinstance(ctx.service("editor"), ServiceBase) or hasattr(ctx.service("editor"), "start")
    assert not spec.is_placeholder if hasattr(spec, "is_placeholder") else True


@needs_ffmpeg
def test_add_media_undo_redo_and_persistence(svc, ctx, media):
    c = svc.add_media(media["a"])
    assert c is not None and c.start == 0 and svc.selection == [c.id] and svc.tl.find_clip(c.id)[0].id == "V1"
    m = svc.add_media(media["music"])
    assert svc.tl.find_clip(m.id)[0].kind == "audio"
    im = svc.add_media(media["pic"])
    assert im.kind == "image" and im.duration == 5.0 and im.start == pytest.approx(3.0, abs=0.2)           # videonun sonuna
    assert ctx.project.dirty and len(ctx.project.data.timeline["tracks"][0]["clips"]) == 2
    assert {i.kind for i in svc.media_items()} >= {"video", "audio"} and any(i.path == media["a"] for i in svc.media_items())
    stack = ctx.undo
    n = stack.count()
    svc.undo()
    assert svc.tl.find_clip(im.id) is None and len(ctx.project.data.timeline["tracks"][0]["clips"]) == 1
    svc.redo()
    assert svc.tl.find_clip(im.id) is not None and stack.count() == n
    ctx.project.save(silent=True)
    root = ctx.project.root
    ctx.project.close()
    ctx.project.open(root)
    svc.reload("load")
    assert svc.tl.find_clip(c.id) is not None and svc.tl.find_clip(m.id) is not None and svc.tl.duration == pytest.approx(8.0, abs=0.3)


@needs_ffmpeg
def test_split_delete_move_snap_and_locked(svc, media, qtbot):
    a = svc.add_media(media["a"])
    b = svc.add_media(media["b"])
    assert b.start == pytest.approx(3.0, abs=0.2)
    svc.set_playhead(1.5)
    svc.select([a.id])
    left, right = svc.split_at_playhead()
    assert left.end == pytest.approx(1.5, abs=0.11) and right.start == left.end
    snaps = []
    svc.snapped.connect(snaps.append)
    r = svc.move_clip(b.id, 3.05, px_per_s=100)             # 10 px = 0.1 sn eşik → 3.0'a yapışır (sağ klibin sonu)
    assert r.snapped and snaps
    svc.set_snap(False)
    r2 = svc.move_clip(b.id, 5.05, px_per_s=100)
    assert not r2.snapped and svc.tl.find_clip(b.id)[1].start == pytest.approx(5.05, abs=0.06)
    svc.set_snap(True)
    got = errors(svc)
    svc.set_track("V1", locked=True)
    before = svc.tl.snapshot()
    n = svc.undo_stack.count()
    svc.move_clip(b.id, 9.0)
    svc.select([b.id])
    svc.delete_selection()
    assert svc.tl.snapshot() == before and svc.undo_stack.count() == n
    assert got and got[0].code == "editor.locked"
    svc.set_track("V1", locked=False)
    svc.select([b.id])
    svc.delete_selection(ripple=True)
    assert svc.tl.find_clip(b.id) is None


def test_undo_merges_slider_drags_and_stack_text(svc, media):
    if not media:
        pytest.skip()
    a = svc.add_media(media["a"])
    n = svc.undo_stack.count()
    for v in (-1, -2, -3, -4):
        svc.set_clip(a.id, volume_db=v)
    assert svc.undo_stack.count() == n + 1                                    # aynı kaydırıcı = tek adım
    assert svc.tl.find_clip(a.id)[1].volume_db == -4
    svc.undo()
    assert svc.tl.find_clip(a.id)[1].volume_db == 0
    assert svc.undo_stack.undoText() != ""


def test_simple_mode_track_cap_and_markers(svc):
    assert not svc.can_add_track(True) and svc.can_add_track(False)
    for _ in range(5):
        svc.add_track("video")
    assert len(svc.tl.tracks) == 8
    svc.add_marker(0.0, "Giriş", True)
    svc.add_marker(15.0, "Orta", True)
    svc.add_marker(40.0, "Son", True)
    assert svc.chapters_text().splitlines()[0] == "0:00 Giriş" and svc.chapter_problems() == []
    svc.set_eq([0, 0, 0, 3, 0, 0, 0, 0, 0, 0])
    svc.set_master_db(-2)
    assert svc.tl.eq[3] == 3 and svc.tl.master_db == -2


@needs_ffmpeg
def test_render_job_end_to_end_and_reveal(svc, ctx, media, qtbot):
    svc.add_media(media["a"])
    svc.add_media(media["music"])
    svc.add_marker(0, "Giriş", True)
    s = svc.template_settings("youtube", "fast")
    s.width, s.height, s.fps = 160, 90, 10
    est = svc.estimate(s)
    assert est.size_bytes > 0 and est.seconds > 0
    done = []
    svc.exportFinished.connect(done.append)
    job = svc.start_render(s)
    assert job is not None and job.kind == JOB_RENDER
    qtbot.waitUntil(lambda: job.finished, timeout=20000)
    qtbot.wait(50)
    assert job.status is JobStatus.DONE, job.error
    out = Path(job.payload["_result"]["output"])
    assert out.exists() and out.parent.name == folder_for("export") and out.suffix == ".mp4" and out.name.startswith("Deneme_Projesi_youtube_")
    assert done == [str(out)] and svc.last_export == str(out)
    opened = []
    svc.opener = lambda url: opened.append(url) or True
    assert svc.reveal() and opened[0] == QUrl.fromLocalFile(str(out.parent))
    assert not svc.reveal("/yok/dosya.mp4")
    wav = svc.template_settings("podcast")
    job2 = svc.start_render(wav)
    qtbot.waitUntil(lambda: job2.finished, timeout=20000)
    qtbot.wait(50)
    assert job2.status is JobStatus.DONE and job2.payload["_result"]["output"].endswith(".wav")


@needs_ffmpeg
def test_render_errors_are_cards_not_crashes(svc, ctx, media, tmp_path):
    got = errors(svc)
    assert svc.start_render(render.RenderSettings()) is None and got[-1].code == "editor.empty_timeline"
    ctx.project.close()
    svc.reload("load")
    svc.add_media(media["a"])
    assert svc.start_render(render.RenderSettings()) is None and got[-1].code == "editor.no_project"
    assert svc.add_media(str(tmp_path / "yok.mp4")) is None and got[-1].code == "editor.missing_media"


@needs_ffmpeg
def test_proxy_preview_jobs_and_frames(svc, media, qtbot):
    svc.add_media(media["a"])
    j = svc.queue_proxy(media["a"])
    qtbot.waitUntil(lambda: j.finished, timeout=15000)
    assert j.status is JobStatus.DONE and j.payload["_result"]["proxy"] is None            # küçük kaynak: proxy gerekmez
    p = svc.render_preview()
    assert p is not None
    qtbot.waitUntil(lambda: p.finished, timeout=20000)
    assert p.status is JobStatus.DONE and svc.preview_path().exists()
    assert svc.render_preview() is None                                                    # aynı özet: yeniden üretmez
    svc.add_media(media["b"])
    assert not svc.preview_path().exists()                                                 # özet değişti
    frames_got = []
    svc.frameReady.connect(lambda tok, before, img: frames_got.append((before, img.shape)))
    svc.set_playhead(1.0)
    svc.request_frame(size=(160, 90))
    svc.request_frame(size=(160, 90), before=True)
    qtbot.waitUntil(lambda: len(frames_got) == 2, timeout=15000)
    assert {b for b, _s in frames_got} == {True, False} and frames_got[0][1] == (90, 160, 3)
    assert svc.frame_now(0.5, size=(160, 90)).shape == (90, 160, 3)


@needs_ffmpeg
def test_proposals_are_user_triggered_and_undoable(svc, media, monkeypatch):
    svc.add_media(media["music"])
    svc.add_media(media["voice"], track_id="A2")
    snap = svc.tl.snapshot()
    n = svc.undo_stack.index()
    shown = []
    svc.proposalReady.connect(lambda name, prop: shown.append(name))
    prop = svc.propose("ducking")
    assert prop is not None and shown == ["ducking"]
    assert svc.tl.snapshot() == snap and svc.undo_stack.index() == n                       # öneri hiçbir şeyi değiştirmedi
    notes = []
    svc.notice.connect(notes.append)
    assert svc.apply(prop) is True and notes
    assert any(e["type"] == "duck" for e in svc.tl.track("A1").effects) and svc.undo_stack.index() == n + 1
    svc.undo()
    assert svc.tl.snapshot() == snap
    got = errors(svc)
    svc.select([])
    assert svc.propose("silence") is None and got[-1].code == "editor.no_clip"
    assert svc.apply(None) is False
    voice = svc.tl.track("A2").clips[0].id
    svc.select([voice])
    empty = svc.propose("silence")                                                          # saf ton: kesilecek sessizlik yok
    assert empty is not None and empty.empty and svc.apply(empty) is False and svc.undo_stack.index() == n
    monkeypatch.setitem(sys.modules, "librosa", None)
    svc.select([svc.tl.track("A1").clips[0].id])
    assert svc.propose("beat") is None and got[-1].code == "editor.opt_librosa" and got[-1].actions[0].kind == "copy"
    assert svc.propose("reframe") is None and got[-1].code == "editor.no_source"           # sesin görüntüsü yok


@needs_ffmpeg
def test_async_proposal_and_recording_into_timeline(svc, media, qtbot):
    svc.add_media(media["a"])
    svc.select([svc.tl.track("V1").clips[0].id])
    seen = []
    svc.proposalReady.connect(lambda n, p: seen.append((n, p)))
    svc.propose_async("freeze", seconds=1.0)
    qtbot.waitUntil(lambda: bool(seen), timeout=10000)
    assert seen[0][0] == "freeze"
    rec_ok = svc.start_recording("webcam", source=["-f", "lavfi", "-i", "testsrc=s=160x90:r=10"])
    assert rec_ok and svc.recording and not svc.start_recording("screen")
    qtbot.wait(900)
    clip = svc.stop_recording()
    assert clip is not None and not svc.recording and clip.src.endswith(".mp4") and Path(clip.src).parent.name == folder_for("download")
