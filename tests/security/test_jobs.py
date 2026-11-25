"""Kalıcı iş kuyruğu: ilerleme, iptal, yeniden dene, kalıcılık, yeniden açılışta duraklatma, bildirim."""
from __future__ import annotations

import threading
import time

import pytest

from kadraj.core.contracts import Job, JobControlAPI, JobQueueAPI, JobStatus, SmartError, Topics
from kadraj.core.jobs import DesktopNotifier, JobQueue

T = 5000


class RecNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, title, text, error=False):
        self.calls.append((title, text, error))
        return True

    def available(self):
        return True


@pytest.fixture()
def q(ctx, tmp_path):
    ctx.create_project("İşler", tmp_path / "p")
    return ctx.jobs


def done(qtbot, job, timeout=T):
    qtbot.waitUntil(lambda: job.finished, timeout=timeout)
    return job


def test_implements_contracts(q):
    assert isinstance(q, JobQueueAPI) and isinstance(q, JobControlAPI)


def test_success_progress_result_and_db_persistence(q, ctx, qtbot):
    events = []
    ctx.bus.subscribe(Topics.JOB_ADDED, lambda j: events.append(("added", j.status)))
    ctx.bus.subscribe(Topics.JOB_UPDATED, lambda j: events.append(("updated", j.status, round(j.progress, 2))))

    def handler(job, jc):
        for i in range(1, 5):
            jc.report(i / 4, f"adım {i}")
            time.sleep(0.07)
        return {"output": "/tmp/x.wav"}
    q.register_handler("demo", handler)
    job = q.submit("demo", {"url": "https://x"}, title="Demo iş")
    assert job.id > 0 and job.title == "Demo iş" and job.payload["url"] == "https://x"
    done(qtbot, job)
    assert job.status is JobStatus.DONE and job.progress == 1.0 and job.payload["_result"] == {"output": "/tmp/x.wav"}
    assert job.payload["_message"].startswith("adım")
    qtbot.waitUntil(lambda: ("updated", JobStatus.DONE, 1.0) in events, timeout=T)
    assert events[0] == ("added", JobStatus.QUEUED) and any(e[1] is JobStatus.RUNNING for e in events)
    row = ctx.project.db.get_job(job.id)                               # kalıcı
    assert row["status"] == "done" and row["progress"] == 1.0 and row["payload"]["url"] == "https://x"
    assert q.get(job.id) is job and job in q.list(JobStatus.DONE) and q.list(JobStatus.RUNNING) == []


def test_progress_events_are_throttled(q, ctx, qtbot):
    count = []
    ctx.bus.subscribe(Topics.JOB_UPDATED, lambda j: count.append(j.progress))

    def spam(job, jc):
        for i in range(2000):
            jc.report(i / 2000)
    q.register_handler("spam", spam)
    done(qtbot, q.submit("spam"))
    qtbot.wait(100)
    assert len(count) < 200 and count[-1] == 1.0


def test_handler_exception_becomes_failed_with_smart_error_and_app_survives(q, qtbot):
    q.register_handler("net", lambda job, jc: (_ for _ in ()).throw(ConnectionError("dns")))
    job = done(qtbot, q.submit("net"))
    assert job.status is JobStatus.FAILED and job.error
    assert job.payload["_error"]["code"] and job.payload["_error"]["steps"]
    row = q._db().get_job(job.id)
    assert row["status"] == "failed" and row["error"] == job.error
    q.register_handler("ok", lambda job, jc: None)
    assert done(qtbot, q.submit("ok")).status is JobStatus.DONE        # kuyruk çalışmaya devam eder


def test_smart_error_from_handler_is_kept(q, qtbot):
    q.register_handler("x", lambda job, jc: (_ for _ in ()).throw(SmartError("my.code", "T", "Özet")))
    job = done(qtbot, q.submit("x"))
    assert job.error == "Özet" and job.payload["_error"]["code"] == "my.code"


def test_cancel_running_job(q, qtbot):
    started = threading.Event()

    def loop(job, jc):
        started.set()
        while not jc.is_cancelled():
            time.sleep(0.01)
    q.register_handler("loop", loop)
    job = q.submit("loop")
    assert started.wait(3)
    assert q.cancel(job.id) is True
    done(qtbot, job)
    assert job.status is JobStatus.CANCELLED and q.cancel(job.id) is False and q.cancel(424242) is False


def test_cancel_queued_job_never_runs(q, ctx, qtbot):
    ctx.settings.set("jobs.max_parallel", 1)
    q._pool.setMaxThreadCount(1)
    gate, ran = threading.Event(), []

    def block(job, jc):
        gate.wait(5)

    def second(job, jc):
        ran.append(job.id)
    q.register_handler("block", block)
    q.register_handler("second", second)
    first = q.submit("block")
    qtbot.waitUntil(lambda: first.status is JobStatus.RUNNING, timeout=T)
    queued = q.submit("second")
    assert queued.status is JobStatus.QUEUED and q.cancel(queued.id) is True
    gate.set()
    done(qtbot, first)
    qtbot.wait(150)
    assert ran == [] and queued.status is JobStatus.CANCELLED


def test_concurrency_limit(q, ctx, qtbot):
    q._pool.setMaxThreadCount(2)
    live, peak, lock = 0, 0, threading.Lock()

    def work(job, jc):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.08)
        with lock:
            live -= 1
    q.register_handler("w", work)
    jobs = [q.submit("w") for _ in range(6)]
    for j in jobs:
        done(qtbot, j)
    assert peak == 2 and all(j.status is JobStatus.DONE for j in jobs)


def test_retry_failed_and_cancelled_jobs(q, qtbot):
    attempts = []

    def flaky(job, jc):
        attempts.append(job.payload.get("_attempts", 0))
        if len(attempts) == 1:
            raise RuntimeError("ilk deneme patladı")
    q.register_handler("flaky", flaky)
    job = done(qtbot, q.submit("flaky"))
    assert job.status is JobStatus.FAILED and "_error" in job.payload
    assert q.retry(job.id) is True
    qtbot.waitUntil(lambda: job.status is JobStatus.DONE, timeout=T)
    assert attempts == [0, 1] and "_error" not in job.payload and job.error is None
    assert q.retry(job.id) is False                                    # bitmiş işe retry yok
    assert q.retry(999999) is False


def test_automatic_retry_with_backoff_for_retryable_errors(q, qtbot):
    q.retry_delay = 0.01
    runs = []

    def offline_twice(job, jc):
        runs.append(1)
        if len(runs) < 3:
            raise ConnectionError("no route")
    q.register_handler("net", offline_twice)
    job = q.submit("net", max_retries=3)
    qtbot.waitUntil(lambda: job.status is JobStatus.DONE, timeout=T)
    assert len(runs) == 3 and job.payload["_attempts"] == 2


def test_automatic_retry_stops_at_the_limit_and_skips_non_retryable(q, qtbot):
    q.retry_delay = 0.01
    runs = []
    q.register_handler("net", lambda job, jc: runs.append(1) or (_ for _ in ()).throw(ConnectionError("x")))
    job = q.submit("net", max_retries=1)
    qtbot.waitUntil(lambda: job.status is JobStatus.FAILED, timeout=T)
    assert len(runs) == 2
    bad = []
    q.register_handler("bug", lambda job, jc: bad.append(1) or 1 / 0)
    j2 = done(qtbot, q.submit("bug", max_retries=5))
    qtbot.wait(100)
    assert j2.status is JobStatus.FAILED and len(bad) == 1


def test_job_waits_for_a_late_handler(q, qtbot):
    job = q.submit("ai.deferred", {"prompt": "x"})
    qtbot.wait(80)
    assert job.status is JobStatus.QUEUED
    ran = []
    q.register_handler("ai.deferred", lambda j, jc: ran.append(j.payload["prompt"]))
    done(qtbot, job)
    assert ran == ["x"] and job.status is JobStatus.DONE


def test_payload_must_be_json(q):
    with pytest.raises(ValueError):
        q.submit("x", {"bad": object()})
    assert q.list() == []


def test_jobs_without_a_project_live_in_memory(ctx, qtbot):
    assert not ctx.project.is_open
    ctx.jobs.register_handler("m", lambda j, jc: None)
    job = ctx.jobs.submit("m")
    assert job.id < 0
    done(qtbot, job)
    assert job.status is JobStatus.DONE


# ---- kalıcılık / yeniden açılış ---------------------------------------------------------------------------------------------
def test_running_job_is_paused_when_the_project_closes_and_resumes_after_reopen(q, ctx, qtbot):
    root = ctx.project.root
    started = threading.Event()
    seen = []

    def long(job, jc):
        started.set()
        jc.report(0.4, "yarıda")
        while not jc.is_cancelled():
            time.sleep(0.01)
        seen.append("durduruldu")

    q.register_handler("long", long)
    job = q.submit("long", {"dosya": "a.mp4"}, title="Uzun iş")
    assert started.wait(3)
    ctx.project.close()                                                  # çalışan iş duraklatılır
    assert seen == ["durduruldu"] and job.status is JobStatus.PAUSED
    assert q.list() == []                                                # proje işleri bellekten çıktı
    ctx.open_project(root)                                               # "uygulama yeniden açıldı"
    recovered = q.list()
    assert len(recovered) == 1 and recovered[0].status is JobStatus.PAUSED and recovered[0].title == "Uzun iş"
    assert recovered[0].payload["dosya"] == "a.mp4" and recovered[0].progress == pytest.approx(0.4, abs=0.01)
    done_runs = []
    q.register_handler("long", lambda j, jc: done_runs.append(j.payload["dosya"]))
    assert q.resume(recovered[0].id) is True
    done(qtbot, recovered[0])
    assert done_runs == ["a.mp4"] and recovered[0].status is JobStatus.DONE


def test_crash_leftovers_come_back_as_paused(ctx, tmp_path, qtbot):
    ctx.create_project("Çöküş", tmp_path / "c")
    db = ctx.project.db
    running = db.add_job("render", "running", {"_title": "Render", "x": 1})
    queued = db.add_job("render", "queued", {"x": 2})
    finished = db.add_job("render", "done", {"x": 3})
    failed = db.add_job("render", "failed", {"x": 4})
    root = ctx.project.root
    ctx.project.close()
    ctx.open_project(root)
    by_id = {j.id: j for j in ctx.jobs.list()}
    assert by_id[running].status is JobStatus.PAUSED and by_id[queued].status is JobStatus.PAUSED
    assert by_id[finished].status is JobStatus.DONE and by_id[failed].status is JobStatus.FAILED
    assert by_id[running].title == "Render" and by_id[queued].title == "render"
    assert ctx.project.db.get_job(running)["status"] == "paused"          # DB de düzeltildi


def test_resume_and_retry_need_a_handler_and_the_right_status(q, qtbot):
    q.register_handler("k", lambda j, jc: None)
    job = done(qtbot, q.submit("k"))
    assert q.resume(job.id) is False                                       # duraklatılmış değil
    paused = Job("nohandler", id=-77, status=JobStatus.PAUSED)
    q._jobs[-77] = paused
    assert q.resume(-77) is False and q.retry(-77) is False                # işleyici yok


def test_shutdown_pauses_running_jobs(q, ctx, qtbot):
    started = threading.Event()

    def long(job, jc):
        started.set()
        while not jc.is_cancelled():
            time.sleep(0.01)
    q.register_handler("long", long)
    job = q.submit("long")
    assert started.wait(3)
    q.shutdown()
    assert job.status is JobStatus.PAUSED and q._db().get_job(job.id)["status"] == "paused"
    assert q.submit("long").status is JobStatus.QUEUED                    # kapanırken yeni iş başlamaz


# ---- bildirim ---------------------------------------------------------------------------------------------------------------------
def test_desktop_notification_only_for_long_jobs(q, ctx, qtbot):
    rec = RecNotifier()
    q.notifier = rec
    q.register_handler("fast", lambda j, jc: None)
    done(qtbot, q.submit("fast"))
    qtbot.wait(80)
    assert rec.calls == []                                                 # 5 sn altı: bildirim yok
    ctx.settings.set("notifications.min_seconds", 0)
    q.register_handler("ok", lambda j, jc: None)
    q.register_handler("bad", lambda j, jc: 1 / 0)
    a = done(qtbot, q.submit("ok", title="Güzel iş"))
    b = done(qtbot, q.submit("bad", title="Kötü iş"))
    qtbot.waitUntil(lambda: len(rec.calls) == 2, timeout=T)
    assert "Güzel iş" in rec.calls[0][1] and rec.calls[0][2] is False
    assert "Kötü iş" in rec.calls[1][1] and rec.calls[1][2] is True and a.status is JobStatus.DONE and b.status is JobStatus.FAILED


def test_notification_opt_outs(q, ctx, qtbot):
    rec = RecNotifier()
    q.notifier = rec
    ctx.settings.set("notifications.min_seconds", 0)
    q.register_handler("ok", lambda j, jc: None)
    done(qtbot, q.submit("ok", notify=False))
    ctx.settings.set("notifications.desktop", False)
    done(qtbot, q.submit("ok"))
    qtbot.wait(100)
    assert rec.calls == []


def test_notifier_is_silent_without_a_tray():
    n = DesktopNotifier()
    assert n.available() in (True, False)
    if not n.available():
        assert n.notify("a", "b") is False                                  # offscreen: tepsi yok → sessiz


def test_jobs_failing_after_project_close_do_not_crash(q, ctx, qtbot):
    release = threading.Event()

    def slow(job, jc):
        release.wait(3)
        jc.report(0.9)
    q.register_handler("slow", slow)
    job = q.submit("slow")
    qtbot.waitUntil(lambda: job.status is JobStatus.RUNNING, timeout=T)
    release.set()
    ctx.project.close()                                                   # DB kapanırken iş bitiyor
    qtbot.wait(100)
    assert job.finished or job.status is JobStatus.PAUSED
