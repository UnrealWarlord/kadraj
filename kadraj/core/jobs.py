"""Kalıcı iş kuyruğu (``JobQueueAPI`` + ``JobControlAPI``; sözleşme: ``core.contracts`` §3 ve §9).

* İşler **projenin SQLite'ında** (``jobs`` tablosu, plan §3) saklanır; proje açık değilken bellekte (negatif kimlik)
  tutulur. ``QThreadPool`` ile en çok ``settings["jobs"]["max_parallel"]`` (2) iş birlikte çalışır.
* İşleyici ``handler(job, jobctx)`` bir **iş parçacığında** çalışır: ``jobctx.report(0..1, mesaj)`` ilerleme bildirir
  (yayın hız sınırlıdır), ``jobctx.is_cancelled()`` iptal/duraklatma isteğini sorar. Döndürdüğü JSON-uyumlu ``dict``
  ``job.payload["_result"]``'a yazılır. İstisna → ``translate`` ile sade ``SmartError``; iş ``FAILED`` olur,
  **uygulama düşmez**. ``retryable`` hatalar ``max_retries`` kadar kendiliğinden yeniden denenir.
* ``cancel`` / ``retry`` / ``resume``. Proje kapanırken çalışan ve bekleyen işler **duraklatılır**; uygulama (çöküş
  dahil) yeniden açılıp proje açılınca ``queued``/``running`` kalmış işler **``paused``** olarak geri gelir.
* Masaüstü bildirimi: iş bitince (≥ ``notifications.min_seconds``) ``QSystemTrayIcon``; sistem tepsisi yoksa sessiz.
* Payload'daki ``_`` ile başlayan anahtarlar kuyruğa aittir (``_title``, ``_notify``, ``_max_retries``, ``_attempts``,
  ``_message``, ``_result``, ``_error``); işleyiciler kendi verilerini ``_`` olmadan yazmalıdır.
* Çalıştırılacak işleyicisi henüz kayıtlı olmayan işler (örn. ``ai.deferred``) ``queued`` bekler; işleyici kaydolunca başlar.
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool

from . import i18n
from .contracts import Job, JobHandler, JobStatus, Topics
from .errors import translate

PROGRESS_PUBLISH_S = 0.05       # ilerleme yayınları arası en az süre
PROGRESS_PERSIST_S = 1.0        # ilerlemenin DB'ye yazılma aralığı
RECOVER_LIMIT = 200
_META = ("_title", "_notify", "_max_retries", "_attempts", "_message", "_result", "_error")


# ---------------------------------------------------------------------------
class DesktopNotifier:
    """``QSystemTrayIcon`` ile masaüstü bildirimi; tepsi yoksa ``False`` döner, hiçbir şey yapmaz."""

    def __init__(self) -> None:
        self._tray: Any = None

    def available(self) -> bool:
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            return bool(QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:
            return False

    def notify(self, title: str, text: str, error: bool = False) -> bool:
        if not self.available():
            return False
        try:
            from PySide6.QtGui import QIcon
            from PySide6.QtWidgets import QSystemTrayIcon
            if self._tray is None:
                from ..ui.widgets.icons import icon_pixmap
                self._tray = QSystemTrayIcon(QIcon(icon_pixmap("film", "#D4A24C", 32)))
                self._tray.show()
            icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
            self._tray.showMessage(title, text, icon, 6000)
            return True
        except Exception:
            return False


class _JobCtx:
    """İşleyiciye verilen ``JobContext``."""

    def __init__(self, queue: "JobQueue", job: Job) -> None:
        self._q, self._job = queue, job
        self.stop = threading.Event()
        self.reason = ""                    # "cancel" | "pause"
        self._last_pub = 0.0
        self._last_persist = 0.0

    def report(self, progress: float, message: str = "") -> None:
        job = self._job
        job.progress = max(0.0, min(1.0, float(progress)))
        if message:
            job.payload["_message"] = message
        now = time.monotonic()
        if now - self._last_pub >= PROGRESS_PUBLISH_S or job.progress >= 1.0:
            self._last_pub = now
            job.updated = time.time()
            self._q._publish(Topics.JOB_UPDATED, job)
        if now - self._last_persist >= PROGRESS_PERSIST_S:
            self._last_persist = now
            self._q._persist(job)

    def is_cancelled(self) -> bool:
        return self.stop.is_set()


class _Runnable(QRunnable):
    def __init__(self, queue: "JobQueue", job: Job) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._q, self._job = queue, job

    def run(self) -> None:
        self._q._execute(self._job)


# ---------------------------------------------------------------------------
class JobQueue(QObject):
    """``ctx.jobs``."""

    def __init__(self, ctx: Any, *, notifier: Optional[DesktopNotifier] = None, retry_delay: float = 2.0,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent or ctx)
        self.ctx = ctx
        self.notifier = notifier if notifier is not None else DesktopNotifier()
        self.retry_delay = retry_delay
        self._lock = threading.RLock()
        self._jobs: Dict[int, Job] = {}
        self._handlers: Dict[str, JobHandler] = {}
        self._ctxs: Dict[int, _JobCtx] = {}
        self._started: Dict[int, float] = {}
        self._notified: set = set()
        self._timers: List[threading.Timer] = []
        self._mem_ids = itertools.count(-1, -1)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(self._max_parallel())
        self._closing = False

        proj = ctx.project
        proj.opened.connect(self._on_opened)
        proj.aboutToClose.connect(self._on_about_to_close)
        proj.closed.connect(self._on_closed)
        self._unsub = ctx.bus.subscribe(Topics.JOB_UPDATED, self._maybe_notify)
        if proj.is_open:
            self._on_opened(str(proj.root))

    # -- yardımcılar ----------------------------------------------------------------
    def _max_parallel(self) -> int:
        try:
            return max(1, int(self.ctx.settings.get("jobs.max_parallel", 2)))
        except (TypeError, ValueError):
            return 2

    def _publish(self, topic: str, job: Job) -> None:
        self.ctx.bus.emit(topic, job)

    def _db(self):
        proj = self.ctx.project
        return proj.db if proj.is_open else None

    def _persist(self, job: Job) -> None:
        if job.id is None or job.id < 0:
            return
        try:
            db = self._db()
            if db is not None:
                db.update_job(job.id, status=job.status.value, progress=job.progress, error=job.error,
                              payload=job.payload)
        except Exception:                      # proje kapanırken DB gitmiş olabilir; iş bellekte kalır
            pass

    @staticmethod
    def _title(kind: str, payload: Mapping[str, Any], title: str = "") -> str:
        return title or str(payload.get("_title") or kind)

    # -- JobQueueAPI --------------------------------------------------------------------
    def register_handler(self, kind: str, handler: JobHandler) -> None:
        self._handlers[kind] = handler
        for job in self.list(JobStatus.QUEUED):          # işleyicisi yüzünden bekleyen işler başlasın
            if job.kind == kind and job.id not in self._ctxs:
                self._enqueue(job)

    def submit(self, kind: str, payload: Optional[Mapping[str, Any]] = None, title: str = "", *,
               notify: bool = True, max_retries: int = 0) -> Job:
        body: Dict[str, Any] = dict(payload or {})
        try:
            json.dumps(body)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"iş yükü JSON-uyumlu olmalı: {exc}") from exc
        body["_title"] = self._title(kind, body, title)
        body.setdefault("_notify", bool(notify))
        if max_retries:
            body["_max_retries"] = int(max_retries)
        db = self._db()
        job_id: Optional[int] = None
        if db is not None:
            try:
                job_id = db.add_job(kind, "queued", body)
            except Exception:
                job_id = None
        if job_id is None:
            job_id = next(self._mem_ids)
        job = Job(kind=kind, id=job_id, title=body["_title"], payload=body)
        with self._lock:
            self._jobs[job_id] = job
        self._publish(Topics.JOB_ADDED, job)
        self._enqueue(job)
        return job

    def get(self, job_id: int) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self, status: Optional[JobStatus] = None) -> List[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: (j.created, j.id or 0))
        return [j for j in jobs if status is None or j.status is status]

    def cancel(self, job_id: int) -> bool:
        job = self.get(job_id)
        if job is None or job.finished:
            return False
        ctx = self._ctxs.get(job_id)
        if ctx is not None:                              # çalışıyor: işleyici iptali kendi zamanında görür
            ctx.reason = "cancel"
            ctx.stop.set()
            return True
        self._finish(job, JobStatus.CANCELLED)           # sırada/duraklatılmış: hemen iptal
        return True

    # -- JobControlAPI -------------------------------------------------------------------------
    def retry(self, job_id: int) -> bool:
        job = self.get(job_id)
        if job is None or job.status not in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.PAUSED) \
                or job.kind not in self._handlers:
            return False
        job.payload["_attempts"] = int(job.payload.get("_attempts", 0)) + 1
        job.payload.pop("_error", None)
        job.payload.pop("_result", None)
        job.progress, job.error = 0.0, None
        self._set_status(job, JobStatus.QUEUED, clear_error=True)
        self._enqueue(job)
        return True

    def resume(self, job_id: int) -> bool:
        job = self.get(job_id)
        if job is None or job.status is not JobStatus.PAUSED or job.kind not in self._handlers:
            return False
        self._set_status(job, JobStatus.QUEUED)
        self._enqueue(job)
        return True

    # -- çalıştırma ----------------------------------------------------------------------------
    def _set_status(self, job: Job, status: JobStatus, *, clear_error: bool = False) -> None:
        job.status = status
        job.updated = time.time()
        try:
            db = self._db()
            if db is not None and job.id is not None and job.id > 0:
                db.update_job(job.id, status=status.value, progress=job.progress, payload=job.payload,
                              error=job.error, clear_error=clear_error and job.error is None)
        except Exception:
            pass
        self._publish(Topics.JOB_UPDATED, job)

    def _finish(self, job: Job, status: JobStatus) -> None:
        self._set_status(job, status)

    def _enqueue(self, job: Job) -> None:
        if self._closing or job.kind not in self._handlers or job.id in self._ctxs:
            return
        with self._lock:
            self._ctxs[job.id] = _JobCtx(self, job)      # çift başlatmayı engelle
        self._pool.start(_Runnable(self, job))

    def _execute(self, job: Job) -> None:
        jc = self._ctxs.get(job.id)
        handler = self._handlers.get(job.kind)
        try:
            if jc is None or handler is None or job.status is not JobStatus.QUEUED:
                return                                    # iptal edilmiş/duraklatılmış: çalıştırma
            if jc.stop.is_set():                          # çalışmadan önce iptal/duraklatma istendi
                self._set_status(job, JobStatus.PAUSED if jc.reason == "pause" else JobStatus.CANCELLED)
                return
            self._started[job.id] = time.time()
            self._set_status(job, JobStatus.RUNNING)
            outcome = JobStatus.DONE
            retry_after: Optional[float] = None
            try:
                result = handler(job, jc)
                if isinstance(result, dict):
                    try:
                        json.dumps(result)
                        job.payload["_result"] = result
                    except (TypeError, ValueError):
                        pass
                if jc.stop.is_set():
                    outcome = JobStatus.PAUSED if jc.reason == "pause" else JobStatus.CANCELLED
            except Exception as exc:
                if jc.stop.is_set():
                    outcome = JobStatus.PAUSED if jc.reason == "pause" else JobStatus.CANCELLED
                else:
                    err = translate(exc, {"source": job.kind})
                    job.error = err.summary
                    job.payload["_error"] = err.to_dict()
                    outcome = JobStatus.FAILED
                    attempts = int(job.payload.get("_attempts", 0))
                    if err.retryable and attempts < int(job.payload.get("_max_retries", 0)):
                        job.payload["_attempts"] = attempts + 1
                        retry_after = self.retry_delay * (2 ** attempts)
            if outcome is JobStatus.DONE:
                job.progress = 1.0
            if retry_after is not None and not self._closing:
                job.progress = 0.0
                self._set_status(job, JobStatus.QUEUED)
                timer = threading.Timer(retry_after, lambda: self._requeue_after_delay(job))
                timer.daemon = True
                self._timers.append(timer)
                timer.start()
            else:
                self._set_status(job, outcome)
        finally:
            with self._lock:
                self._ctxs.pop(job.id, None)

    def _requeue_after_delay(self, job: Job) -> None:
        if job.status is JobStatus.QUEUED and not self._closing:
            self._enqueue(job)

    # -- bildirim ----------------------------------------------------------------------------------
    def _maybe_notify(self, job: Job) -> None:
        if job.status not in (JobStatus.DONE, JobStatus.FAILED) or job.id in self._notified:
            return
        self._notified.add(job.id)
        s = self.ctx.settings
        if not s.get("notifications.desktop", True) or not job.payload.get("_notify", True):
            return
        started = self._started.get(job.id)
        if started is None or time.time() - started < float(s.get("notifications.min_seconds", 5)):
            return
        title = job.title or job.kind
        if job.status is JobStatus.DONE:
            self.notifier.notify(i18n.t("jobs.notify.done_title"), i18n.t("jobs.notify.done", title=title))
        else:
            self.notifier.notify(i18n.t("jobs.notify.failed_title"),
                                 i18n.t("jobs.notify.failed", title=title, error=job.error or ""), error=True)

    # -- proje yaşam döngüsü ---------------------------------------------------------------------------
    def _on_opened(self, _path: str) -> None:
        """Projenin işlerini yükler; yarım kalanlar (``queued``/``running``) **duraklatıldı** olarak geri gelir."""
        self._closing = False
        db = self._db()
        if db is None:
            return
        try:
            rows = db.list_jobs()[-RECOVER_LIMIT:]
        except Exception:
            return
        for row in rows:
            status = JobStatus(row["status"]) if row["status"] in JobStatus._value2member_map_ else JobStatus.FAILED
            payload = row.get("payload") or {}
            if status in (JobStatus.QUEUED, JobStatus.RUNNING):
                status = JobStatus.PAUSED
                try:
                    db.update_job(row["id"], status=status.value)
                except Exception:
                    pass
            job = Job(kind=row["kind"], id=row["id"], title=self._title(row["kind"], payload), status=status,
                      progress=float(row["progress"] or 0.0), payload=payload, error=row.get("error"),
                      created=float(row["created"]), updated=float(row["updated"]))
            with self._lock:
                self._jobs[job.id] = job
            self._notified.add(job.id)
            self._publish(Topics.JOB_ADDED, job)

    def _on_about_to_close(self) -> None:
        """Çalışanları duraklatmaya iste, bitmelerini bekle, kalanları DB'de ``paused`` yap."""
        self._closing = True
        for jc in list(self._ctxs.values()):
            jc.reason = "pause"
            jc.stop.set()
        self._pool.waitForDone(3000)
        for job in self.list():
            if job.id is not None and job.id > 0 and not job.finished:
                job.status = JobStatus.PAUSED
                self._persist(job)

    def _on_closed(self) -> None:
        with self._lock:
            for jid in [j for j in self._jobs if j > 0]:      # proje işleri bellekten çıkar; bellek-içi (negatif) kalır
                self._jobs.pop(jid, None)
            self._ctxs = {k: v for k, v in self._ctxs.items() if k < 0}
        self._closing = False

    def shutdown(self) -> None:
        """Uygulama kapanırken: işleri duraklat, havuzu bekle, zamanlayıcıları iptal et."""
        self._closing = True
        for t_ in self._timers:
            t_.cancel()
        self._timers.clear()
        for jc in list(self._ctxs.values()):
            jc.reason = "pause"
            jc.stop.set()
        self._pool.waitForDone(3000)
        self._unsub()

    def wait_idle(self, timeout_ms: int = 5000) -> bool:
        """Testler/kapanış için: çalışan işlerin bitmesini bekler."""
        return bool(self._pool.waitForDone(timeout_ms))
