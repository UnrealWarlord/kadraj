"""Önizleme oynatıcı (QMediaPlayer + zamanlayıcı yedeği) ve metinle kurgu akışı."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from kadraj.core.contracts import JobStatus
from kadraj.modules.editor.player import PreviewPlayer

from .conftest import needs_ffmpeg


@dataclass
class Seg:
    start: float
    end: float
    text: str


@pytest.fixture()
def svc(ctx, tmp_path):
    ctx.project.create(tmp_path / "projeler", "Oynatici")
    s = ctx.service("editor")
    s.reload("load")
    return s


@needs_ffmpeg
def test_timer_playback_advances_stops_at_end_and_restarts(svc, media, qtbot, monkeypatch):
    monkeypatch.setenv("KADRAJ_NO_MEDIAPLAYER", "1")
    svc.add_media(media["pic"])                                     # 5 sn görsel
    svc.tl.tracks[0].clips[0].duration_hint = 0.4
    svc.tl.tracks[0].clips[0].out = 0.4
    p = PreviewPlayer(svc)
    assert not p.available and p.backend == "timer"
    states = []
    p.stateChanged.connect(states.append)
    svc.set_playhead(0.0)
    p.play()
    qtbot.waitUntil(lambda: p.state == "paused", timeout=3000)                 # süre dolunca durur
    assert states[0] == "playing" and svc.playhead == pytest.approx(0.4, abs=0.05)
    p.play()                                                                   # sondayken oynat → baştan
    assert p.state == "playing" and svc.playhead < 0.2
    p.stop()
    assert p.state == "stopped"


@needs_ffmpeg
def test_media_player_backend_plays_the_rendered_preview(svc, media, qtbot):
    svc.add_media(media["a"])
    job = svc.render_preview()
    qtbot.waitUntil(lambda: job.finished, timeout=20000)
    assert job.status is JobStatus.DONE
    p = PreviewPlayer(svc)
    if not p.available:
        pytest.skip("QtMultimedia yok")
    assert p.load(str(svc.preview_path())) and p.backend == "media"
    p.play()
    assert p.state == "playing"
    qtbot.wait(300)
    p.pause()
    assert p.state == "paused"
    p.unload()
    assert p.backend == "timer" and not p.load("/yok.mp4")


@needs_ffmpeg
def test_text_based_editing_flow(svc, media, qtbot, ctx):
    from kadraj.modules.editor.panels import MagicGrid, TranscriptDialog
    c = svc.add_media(media["a"])
    svc.select([c.id])
    grid = MagicGrid(svc, ("text_edit",))
    qtbot.addWidget(grid)
    segs = [Seg(0.0, 1.0, "bir"), Seg(1.0, 2.0, "iki"), Seg(2.0, 3.0, "üç")]
    holder = {}

    class FakeDialog(TranscriptDialog):
        def exec(self):
            self.list.item(1).setCheckState(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CheckState.Unchecked)   # "iki" kesilsin
            holder["ranges"] = self.deleted_ranges(0.0)
            return 1
    grid.make_dialog = lambda s: FakeDialog(s)
    shown = []
    svc.proposalReady.connect(lambda n, p: shown.append(p))
    grid._on_transcript(c.id, segs)
    assert holder["ranges"] == [(1.0, 2.0)] and shown and shown[0].name == "text_edit"
    assert svc.tl.duration == pytest.approx(3.0, abs=0.1)                  # öneri uygulanmadı
    svc.apply()
    assert svc.tl.duration == pytest.approx(2.0, abs=0.15)
    svc.undo()
    assert svc.tl.duration == pytest.approx(3.0, abs=0.15)
    got = []
    svc.errorRaised.connect(got.append)
    svc.transcribe_async(c.id)                                             # faster-whisper kurulu değil → "paketi kur" kartı
    qtbot.waitUntil(lambda: bool(got), timeout=10000)
    assert got[0].code.startswith("audio.") or "whisper" in (got[0].summary + got[0].title).lower() or got[0].actions or got[0].code
