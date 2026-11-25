"""Önizleme oynatıcı: hazır önizleme dosyası varsa ``QMediaPlayer`` (ses + görüntü, ``QVideoSink`` ile kareler); yoksa ya da
QtMultimedia kullanılamıyorsa **zamanlayıcıyla kare ilerletme** (sessiz, her adımda servisten kare ister). Durum servistedir
(oynatma başlığı); oynatıcı yalnızca zamanı ilerletir.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal


class PreviewPlayer(QObject):
    stateChanged = Signal(str)            # "playing" | "paused" | "stopped"
    positionChanged = Signal(float)       # sn (zaman çizelgesi zamanı)
    frameImage = Signal(object)           # QImage (yalnızca QMediaPlayer yolunda)

    def __init__(self, svc: Any, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.svc = svc
        self.state = "stopped"
        self.backend = "timer"            # "media" | "timer"
        self._player: Any = None
        self._sink: Any = None
        self._t0 = 0.0
        self._clock0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.available = False
        if os.environ.get("KADRAJ_NO_MEDIAPLAYER") != "1":
            try:
                from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
                self._player = QMediaPlayer(self)
                self._audio = QAudioOutput(self)
                self._sink = QVideoSink(self)
                self._player.setAudioOutput(self._audio)
                self._player.setVideoSink(self._sink)
                self._sink.videoFrameChanged.connect(self._on_frame)
                self._player.positionChanged.connect(self._on_position)
                self.available = True
            except Exception:
                self._player = None

    # -- kaynak ---------------------------------------------------------------------------------------------------
    def load(self, path: str) -> bool:
        if not self.available or not path or not os.path.exists(path):
            self.backend = "timer"
            return False
        self._player.setSource(QUrl.fromLocalFile(path))
        self.backend = "media"
        return True

    def unload(self) -> None:
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        self.backend = "timer"

    # -- kontrol --------------------------------------------------------------------------------------------------
    def play(self) -> None:
        t = self.svc.playhead
        if t >= self.svc.tl.duration - 1e-3:
            t = 0.0
            self.svc.set_playhead(0.0)
        if self.backend == "media":
            self._player.setPosition(int(t * 1000))
            self._player.play()
        else:
            self._t0, self._clock0 = t, time.monotonic()
            self._timer.start()
        self._set_state("playing")

    def pause(self) -> None:
        if self.backend == "media" and self._player is not None:
            self._player.pause()
        self._timer.stop()
        self._set_state("paused")

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
        self._timer.stop()
        self._set_state("stopped")

    def toggle(self) -> None:
        self.pause() if self.state == "playing" else self.play()

    def seek(self, t: float) -> None:
        if self.backend == "media" and self._player is not None:
            self._player.setPosition(int(max(0.0, t) * 1000))
        self._t0, self._clock0 = t, time.monotonic()

    # -- iç -------------------------------------------------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.stateChanged.emit(state)

    def _tick(self) -> None:
        t = self._t0 + (time.monotonic() - self._clock0)
        if t >= self.svc.tl.duration:
            self.svc.set_playhead(self.svc.tl.duration)
            self.pause()
            return
        self.svc.set_playhead(t)
        self.positionChanged.emit(t)

    def _on_position(self, ms: int) -> None:
        if self.state != "playing":
            return
        t = ms / 1000.0
        if t >= self.svc.tl.duration - 0.02:
            self.pause()
        self.svc.set_playhead(t)
        self.positionChanged.emit(t)

    def _on_frame(self, frame: Any) -> None:
        try:
            img = frame.toImage()
            if not img.isNull():
                self.frameImage.emit(img)
        except Exception:
            pass
