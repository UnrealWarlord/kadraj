"""İki sayfanın ortak tabanı: servise bağlanma, satır içi hata kartı, öneri şeridi, önizleme kare isteği (geciktirmeli), oynatma."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ...core.contracts import SmartAction, SmartError
from ...ui.widgets.smart_error_card import SmartErrorCard
from .panels import ProposalBar
from .player import PreviewPlayer
from .service import EditorService
from .widgets import PreviewView

FRAME_DEBOUNCE_MS = 120


class EditorPageBase(QWidget):
    """Sayfa **durum tutmaz**: her şey ``self.svc`` (``EditorService``) üzerindedir."""

    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.svc: EditorService = ctx.service("editor")
        self.error_slot = QVBoxLayout()
        self.error_slot.setContentsMargins(0, 0, 0, 0)
        self._error_card: Optional[SmartErrorCard] = None
        self.proposal_bar = ProposalBar(self.svc)
        self.player = PreviewPlayer(self.svc, self)
        self.preview: Optional[PreviewView] = None
        self.compare_on = False
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setInterval(FRAME_DEBOUNCE_MS)
        self._frame_timer.timeout.connect(self._request_frames)
        self.last_frame: Optional[np.ndarray] = None

    @property
    def error_visible(self) -> bool:
        return self._error_card is not None

    def connect_service(self) -> None:
        s = self.svc
        s.errorRaised.connect(self.show_error)
        s.timelineChanged.connect(lambda _r: self.schedule_frame())
        s.playheadChanged.connect(lambda _t: self.on_playhead())
        s.frameReady.connect(self._on_frame)
        s.jobChanged.connect(self.on_job)
        self.player.frameImage.connect(self._on_player_image)
        self.player.stateChanged.connect(self.on_player_state)

    def on_playhead(self) -> None:
        if self.player.state != "playing" or self.player.backend == "timer":
            self.schedule_frame()

    def on_job(self, job: Any) -> None: ...
    def on_player_state(self, state: str) -> None: ...

    # -- önizleme ---------------------------------------------------------------------------------------------------
    def schedule_frame(self) -> None:
        if self.preview is not None and self.isVisible():
            self._frame_timer.start()

    def _request_frames(self) -> None:
        if self.preview is None:
            return
        size = (max(160, self.preview.width()), max(90, int(self.preview.width() / max(0.1, self.preview.aspect))))
        size = (min(size[0], 960), min(size[1], 540))
        self.svc.request_frame(size=size)
        if self.compare_on:
            self.svc.request_frame(before=True, size=size)

    def request_frames_now(self) -> None:
        self._frame_timer.stop()
        self._request_frames()

    def _on_frame(self, _token: int, before: bool, img: np.ndarray) -> None:
        if self.preview is None:
            return
        if before:
            self.preview.set_images(self.preview.after, img)
        else:
            self.last_frame = img
            self.preview.set_images(img, self.preview.before if self.compare_on else None)
            self.on_frame(img)

    def on_frame(self, img: np.ndarray) -> None: ...

    def _on_player_image(self, img: Any) -> None:
        if self.preview is not None and self.player.state == "playing":
            self.preview.set_images(img)

    def set_compare(self, on: bool) -> None:
        self.compare_on = bool(on)
        if self.preview is not None:
            self.preview.set_compare(on)
        if on:
            self.request_frames_now()

    def toggle_play(self) -> None:
        if self.player.state == "playing":
            self.player.pause()
            return
        if self.player.available and self.svc.project_open and self.svc.tl.duration > 0:
            path = self.svc.preview_path()
            if path.exists():
                self.player.load(str(path))
            else:
                self.player.unload()
                self.svc.render_preview()          # sonraki oynatmada hazır olur; şimdilik kare kare ilerler
        self.player.play()

    # -- hata kartı ------------------------------------------------------------------------------------------------------
    def show_error(self, err: SmartError) -> None:
        self.clear_error()
        card = SmartErrorCard(err)
        card.dismissed.connect(self.clear_error)
        card.actionTriggered.connect(self._on_action)
        self._error_card = card
        self.error_slot.addWidget(card)

    def clear_error(self) -> None:
        if self._error_card is not None:
            self._error_card.hide()
            self._error_card.setParent(None)
            self._error_card.deleteLater()
            self._error_card = None

    def _on_action(self, action: SmartAction) -> None:
        if action.kind in ("retry", "copy"):
            self.clear_error() if action.kind == "retry" else None

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.schedule_frame()

    def closeEvent(self, e) -> None:
        self.player.stop()
        super().closeEvent(e)
