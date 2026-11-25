"""Yer tutucu uygulamalar — sonraki adımlar gerçekleriyle değiştirene dek.

Amaç: Haiku'nun (Adım 2, 4, 5) sayfaları ``ctx.vault`` / ``ctx.jobs`` / ``ctx.recent`` ile
**şimdiden** çalışabilsin; sözleşme imzaları gerçekleriyle aynıdır.

* ``InMemoryVault``   — diske YAZMAZ, süreç bitince kaybolur (``is_placeholder = True``). Adım 3 şifreli
  ``storage/vault.py`` ile değişir (``ctx.install_vault``).
* ``InMemoryJobQueue`` — işleri ayrı iş parçacığında çalıştırır; kalıcı DEĞİLDİR. Adım 3 ``core/jobs.py``.
* ``FallbackRecent``  — ``~/.kadraj/recent.json`` okur/yazar. Adım 2'nin ``storage/recent.py``'si varsa o kullanılır.
"""
from __future__ import annotations

import itertools
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from .. import paths
from .contracts import (MAX_RECENT, EventBusAPI, Job, JobContext, JobHandler, JobStatus, RecentEntry,
                        Topics)
from .errors import translate


# ---------------------------------------------------------------------------
# Kasa
# ---------------------------------------------------------------------------
class InMemoryVault:
    is_placeholder = True

    def __init__(self) -> None:
        self._secrets: Dict[str, str] = {}

    def is_available(self) -> bool:
        return True

    def providers(self) -> List[str]:
        return sorted(self._secrets)

    def has_secret(self, provider_id: str) -> bool:
        return provider_id in self._secrets

    def masked(self, provider_id: str) -> str:
        return mask_secret(self._secrets.get(provider_id, ""))

    def set_secret(self, provider_id: str, value: str) -> None:
        if not value or not value.strip():
            raise ValueError("Boş anahtar kaydedilemez")
        self._secrets[provider_id] = value.strip()

    def get_secret(self, provider_id: str) -> Optional[str]:
        return self._secrets.get(provider_id)

    def delete_secret(self, provider_id: str) -> bool:
        return self._secrets.pop(provider_id, None) is not None


def mask_secret(value: str) -> str:
    """``sk-abc…xyz9`` → ``sk-a••••xyz9``; kısa değerler tümüyle maskelenir."""
    if not value:
        return ""
    dot = "•"
    if len(value) <= 8:
        return dot * len(value)
    return value[:4] + dot * 4 + value[-4:]


# ---------------------------------------------------------------------------
# İş kuyruğu
# ---------------------------------------------------------------------------
class _Ctx:
    def __init__(self, queue: "InMemoryJobQueue", job: Job) -> None:
        self._q, self._job, self.cancelled = queue, job, threading.Event()

    def report(self, progress: float, message: str = "") -> None:
        self._job.progress = max(0.0, min(1.0, float(progress)))
        self._job.updated = time.time()
        self._q._publish(Topics.JOB_UPDATED, self._job)

    def is_cancelled(self) -> bool:
        return self.cancelled.is_set()


class InMemoryJobQueue:
    def __init__(self, bus: Optional[EventBusAPI] = None) -> None:
        self._bus = bus
        self._jobs: Dict[int, Job] = {}
        self._ctxs: Dict[int, _Ctx] = {}
        self._handlers: Dict[str, JobHandler] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def _publish(self, topic: str, job: Job) -> None:
        if self._bus is not None:
            self._bus.emit(topic, job)

    def register_handler(self, kind: str, handler: JobHandler) -> None:
        self._handlers[kind] = handler

    def submit(self, kind: str, payload: Optional[Mapping[str, Any]] = None, title: str = "") -> Job:
        with self._lock:
            job = Job(kind=kind, id=next(self._ids), title=title or kind, payload=dict(payload or {}))
            self._jobs[job.id] = job            # type: ignore[index]
        self._publish(Topics.JOB_ADDED, job)
        handler = self._handlers.get(kind)
        if handler is not None:
            ctx = _Ctx(self, job)
            self._ctxs[job.id] = ctx            # type: ignore[index]
            threading.Thread(target=self._run, args=(job, handler, ctx), daemon=True).start()
        return job

    def _run(self, job: Job, handler: JobHandler, ctx: _Ctx) -> None:
        job.status = JobStatus.RUNNING
        self._publish(Topics.JOB_UPDATED, job)
        try:
            handler(job, ctx)
            job.status = JobStatus.CANCELLED if ctx.is_cancelled() else JobStatus.DONE
            if job.status is JobStatus.DONE:
                job.progress = 1.0
        except Exception as exc:               # iş hatası uygulamayı düşürmez
            job.status = JobStatus.FAILED
            job.error = translate(exc).summary
        job.updated = time.time()
        self._publish(Topics.JOB_UPDATED, job)

    def cancel(self, job_id: int) -> bool:
        ctx = self._ctxs.get(job_id)
        job = self._jobs.get(job_id)
        if ctx is None or job is None or job.finished:
            return False
        ctx.cancelled.set()
        return True

    def get(self, job_id: int) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self, status: Optional[JobStatus] = None) -> List[Job]:
        jobs = list(self._jobs.values())
        return [j for j in jobs if status is None or j.status is status]


# ---------------------------------------------------------------------------
# Sistem izleyici
# ---------------------------------------------------------------------------
class BasicSysmon:
    """psutil varsa CPU/RAM/disk örneği; yoksa boş sözlük. GPU alanı ``None`` (Adım 3)."""

    def sample(self, path: Optional[str] = None) -> Dict[str, Any]:
        try:
            import psutil
            vm = psutil.virtual_memory()
            out: Dict[str, Any] = {"cpu": psutil.cpu_percent(interval=None),
                                   "ram_used": vm.used, "ram_total": vm.total, "gpu": None}
            target = path or str(Path.home())
            du = psutil.disk_usage(target)
            out.update({"disk_free": du.free, "disk_total": du.total})
            return out
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Son projeler (yedek)
# ---------------------------------------------------------------------------
class FallbackRecent:
    """``RecentAPI`` yedek uygulaması (biçim: ``contracts.RecentAPI`` belgesi)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or paths.recent_file()

    def _read(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return []
        items = data.get("recent", []) if isinstance(data, dict) else []
        return [i for i in items if isinstance(i, dict) and isinstance(i.get("path"), str)]

    def _write(self, items: List[Dict[str, Any]]) -> None:
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".recent-", suffix=".tmp", dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "recent": items}, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
            tmp = None
        except OSError:
            pass
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def get_recent(self) -> List[RecentEntry]:
        out: List[RecentEntry] = []
        for i in self._read():
            if Path(i["path"]).exists():
                out.append(RecentEntry(i["path"], str(i.get("name") or Path(i["path"]).name),
                                       float(i.get("last_opened") or 0.0), str(i.get("kind") or "project")))
        return out[:MAX_RECENT]

    def add_recent(self, path: str, name: Optional[str] = None) -> None:
        resolved = str(Path(path).resolve())
        items = [i for i in self._read() if i["path"] != resolved]
        items.insert(0, {"path": resolved, "name": name or Path(resolved).name,
                         "last_opened": time.time(), "kind": "project"})
        self._write(items[:MAX_RECENT])

    def forget(self, path: str) -> None:
        resolved = str(Path(path).resolve())
        self._write([i for i in self._read() if i["path"] != resolved])

    def clear(self) -> None:
        self._write([])


class ModuleRecent:
    """Adım 2'nin ``kadraj.storage.recent`` modül işlevlerini ``RecentAPI``'ye sarar."""

    def __init__(self, module: Any) -> None:
        self._m = module

    def get_recent(self) -> List[RecentEntry]:
        return list(self._m.get_recent())

    def add_recent(self, path: str, name: Optional[str] = None) -> None:
        self._m.add_recent(path, name)

    def forget(self, path: str) -> None:
        self._m.forget(path)

    def clear(self) -> None:
        self._m.clear()


def make_recent() -> Any:
    """Haiku'nun ``storage.recent`` modülü tam (4 işlev) varsa onu, yoksa yedeği döner."""
    try:
        import importlib
        mod = importlib.import_module("kadraj.storage.recent")
        if all(callable(getattr(mod, n, None)) for n in ("get_recent", "add_recent", "forget", "clear")):
            return ModuleRecent(mod)
    except Exception:
        pass
    return FallbackRecent()
