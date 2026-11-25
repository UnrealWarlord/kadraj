"""İki sayfanın ortak tabanı: servise bağlanma, satır içi hata kartı, iş ilerlemesi, kayıt listesi eylemleri."""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ...core import i18n
from ...core.contracts import Job, JobStatus, SmartAction, SmartError, Topics
from ...ui.widgets.smart_error_card import SmartErrorCard
from .location import RecordingLocationBar, RecordingResultCard
from .service import JOB_MASTER, JOB_TRANSCRIBE, RecorderService
from .takes import Take


class RecorderPageBase(QWidget):
    """Sayfa **durum tutmaz**: her şey ``self.svc`` (``RecorderService``) üzerindedir; sayfa yalnızca bağlanır ve çizer."""

    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.svc: Optional[RecorderService] = ctx.service("recorder")
        self._selected: Optional[Path] = None
        self._error_card: Optional[SmartErrorCard] = None
        self.error_slot = QVBoxLayout()
        self.error_slot.setContentsMargins(0, 0, 0, 0)
        self.job_text = ""
        # alt sınıflar kurar: kayıt yeri çubuğu ve son kayıt kartı (location.py)
        self.location: Optional[RecordingLocationBar] = None
        self.result_card: Optional[RecordingResultCard] = None

    # -- bağlanma -----------------------------------------------------------------------------------------------------
    def connect_service(self) -> None:
        svc = self.svc
        if svc is None:
            return
        svc.ticked.connect(self.on_tick)
        svc.stateChanged.connect(self.on_state)
        svc.takesChanged.connect(self.on_takes)
        svc.settingsChanged.connect(self.on_settings)
        svc.errorRaised.connect(self.show_error)
        svc.micTestFinished.connect(self.on_mic_result)
        svc.waveformReady.connect(self.on_waveform)
        svc.jobChanged.connect(self.on_job)

    # alt sınıflar doldurur
    def on_tick(self) -> None: ...
    def on_state(self, state: str) -> None: ...
    def on_takes(self) -> None: ...
    def on_settings(self) -> None: ...
    def on_mic_result(self, result: Any) -> None: ...
    def on_waveform(self, path: str) -> None: ...
    def on_job(self, job: Job) -> None: ...

    # -- hata kartı -------------------------------------------------------------------------------------------------------
    def show_error(self, err: SmartError) -> None:
        self.clear_error()
        card = SmartErrorCard(err)
        card.dismissed.connect(self.clear_error)
        card.actionTriggered.connect(self._on_action)
        self._error_card = card
        self.error_slot.addWidget(card)

    def clear_error(self) -> None:
        if self._error_card is not None:
            self.error_slot.removeWidget(self._error_card)
            self._error_card.hide()
            self._error_card.setParent(None)
            self._error_card.deleteLater()
            self._error_card = None

    @property
    def error_visible(self) -> bool:
        return self._error_card is not None

    def _on_action(self, action: SmartAction) -> None:
        if action.kind == "open_settings":
            self.ctx.bus.emit(Topics.NAVIGATE, {"module": "settings"})
        elif action.kind == "retry":
            self.clear_error()

    # -- kayıt listesi eylemleri ------------------------------------------------------------------------------------------------
    def selected_take(self) -> Optional[Take]:
        takes = self.svc.takes() if self.svc else []
        for t in takes:
            if self._selected is not None and t.path == self._selected:
                return t
        return next((t for t in takes if not t.is_master), takes[0] if takes else None)

    def select_take(self, path: Optional[Path]) -> None:
        self._selected = path
        self.on_takes()

    def request_transcribe(self, take: Optional[Take] = None) -> Optional[Job]:
        take = take or self.selected_take()
        if self.svc is None or take is None:
            return None
        self.clear_error()
        return self.svc.transcribe(take)

    def request_master(self, take: Optional[Take] = None) -> Optional[Job]:
        take = take or self.selected_take()
        if self.svc is None or take is None:
            return None
        self.clear_error()
        return self.svc.master(take)

    def job_summary(self, job: Job) -> str:
        pct = int(job.progress * 100)
        title = job.title or job.kind
        if job.status is JobStatus.DONE:
            return i18n.t("recorder.job.finished", title=title)
        if job.status is JobStatus.FAILED:
            return i18n.t("recorder.job.failed", title=title, error=job.error or "")
        if job.status is JobStatus.CANCELLED:
            return i18n.t("recorder.job.cancelled", title=title)
        return i18n.t("recorder.job.progress", title=title, pct=pct)

    # -- ortak ---------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def fmt_duration(seconds: float) -> str:
        whole = int(round(seconds))
        return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}" if whole >= 3600 else \
            f"{whole // 60}:{whole % 60:02d}"

    def device_line(self) -> str:
        d = self.svc.selected_device() if self.svc else None
        return i18n.t("recorder.device.line", name=d.name) if d else i18n.t("recorder.device.none")

    def toggle_record(self) -> None:
        """Kayıt düğmesi: durdurur ya da başlatır. Hata → satır içi kart (istisna dışarı sızmaz)."""
        if self.svc is None:
            return
        try:
            if self.svc.state == "recording":
                take = self.svc.stop_recording()
                self.show_result(take)
            else:
                self.clear_error()
                self.hide_result()
                self.svc.start_recording()
                self.refresh_location()
        except SmartError:
            pass                                            # servis errorRaised yayınladı; kart zaten gösterildi

    # -- kayıt yeri ve son kayıt ----------------------------------------------------------------------------------------
    def refresh_location(self) -> None:
        """Kayıt yeri çubuğunu servisin o anki çıktı klasörüyle eşitler (proje açılınca/kapanınca da değişir)."""
        if self.svc is not None and self.location is not None:
            self.location.set_folder(self.svc.output_folder())

    def show_result(self, take: Optional[Take]) -> None:
        if take is None or self.result_card is None:
            return
        self.result_card.set_take(take, self.fmt_duration(take.duration_s))
        self.result_card.show()
        self.result_card.fit_height()

    def hide_result(self) -> None:
        if self.result_card is not None:
            self.result_card.hide()

    def change_recording_folder(self) -> None:
        """"Değiştir": kayıt yeri proje klasörüne bağlı; seçim Ayarlar'da yapılır."""
        self.ctx.bus.emit(Topics.NAVIGATE, {"module": "settings"})
