"""Otomatik kayıt ve "10 Dk Önceye Döndür" halkası (``AutosaveAPI``; sözleşme: ``core.contracts`` §9).

Davranış
--------
* **20 sn'de bir** (``settings["autosave_seconds"]``) ``tick()``: yalnızca **değişiklik varsa** (proje verisinin
  özeti son kayıttan farklıysa ya da proje kirliyse) sessizce kaydeder. Değişiklik yoksa diske hiç dokunmaz.
* Kayıt: proje dosyası atomik yazılır (``save(silent=True)`` — "kaydedildi" iletisiyle rahatsız etmez) ve
  ``<Proje>/.kadraj/autosave/`` içindeki **dakikalık halkaya** bir kayıt noktası düşer: ~60 sn'lik pencere başına
  bir dosya (pencere içindeki kayıtlar birbirinin yerine geçer), en çok 11 dosya ≈ 10 dakikalık geçmiş. Halka
  zamana değil **kayıt sayısına** göre budanır; yani uzun süre boşta kalınca geçmiş silinmez.
* ``rewind(minutes)``: ``Topics.REWIND_REQUESTED``'e bağlıdır. Hedef zamandan önceki en yeni kayıt noktasına döner
  (geçmiş yetmiyorsa en eskiye ve bunu söyler). **Geri alınabilir:** dönmeden önce mevcut durum yedeklenir,
  ``undo_rewind()`` onu geri getirir. Döndükten sonra ``ctx.undo`` temizlenir (eski komutlar artık geçersiz).
* Durum ``Topics.AUTOSAVE_STATE`` ile yayınlanır (``none|idle|saving|saved|error``). Kayıt hatası (örn. disk dolu)
  uygulamayı düşürmez: durum ``error`` olur ve hata **bir kez** ``SmartError`` olarak bildirilir, başarılı kayıtta
  sıfırlanır.
* Proje kapanırken bekleyen son değişiklikler kaydedilir.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import i18n
from ..core.contracts import (HIDDEN_DIR, RewindResult, RingEntry, SmartError, Topics)
from .project import ProjectData

RING_DIR = "autosave"
RING_PREFIX = "ring-"
BACKUP_PREFIX = "rewind-backup-"
DEFAULT_INTERVAL_S = 20
MIN_INTERVAL_S = 5
RING_ENTRIES = 11            # ~10 dakika + güncel durum
RING_WINDOW_S = 60.0
MAX_BACKUPS = 3


def _write_json(path: Path, obj: Any) -> None:
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".as-", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


class AutosaveService(QObject):
    stateChanged = Signal(str)

    def __init__(self, ctx: Any, *, clock: Callable[[], float] = time.time, ring_entries: int = RING_ENTRIES,
                 ring_window_s: float = RING_WINDOW_S, parent: Optional[QObject] = None) -> None:
        super().__init__(parent or ctx)
        self.ctx = ctx
        self._clock = clock
        self._ring_entries = max(2, ring_entries)
        self._ring_window = ring_window_s
        self.state = "none"
        self._last_digest: Optional[str] = None
        self._last_saved_at: Optional[float] = None
        self._bucket_start: Optional[float] = None
        self._bucket_file: Optional[Path] = None
        self._error_reported = False
        self._stopped = False

        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms())
        self._timer.timeout.connect(self.tick)

        proj = ctx.project
        proj.opened.connect(self._on_opened)
        proj.aboutToClose.connect(self._on_about_to_close)
        proj.closed.connect(self._on_closed)
        ctx.settings.changed.connect(self._on_setting)
        self._unsub = ctx.bus.subscribe(Topics.REWIND_REQUESTED, self._on_rewind_requested)
        if proj.is_open:
            self._on_opened(str(proj.root))

    # -- ayar / yaşam döngüsü ---------------------------------------------------------
    def _interval_ms(self) -> int:
        try:
            sec = int(self.ctx.settings.get("autosave_seconds", DEFAULT_INTERVAL_S))
        except (TypeError, ValueError):
            sec = DEFAULT_INTERVAL_S
        return max(MIN_INTERVAL_S, sec) * 1000

    @property
    def interval_seconds(self) -> int:
        return self._timer.interval() // 1000

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _on_setting(self, key: str, _value: Any) -> None:
        if key in ("autosave_seconds", "*"):
            self._timer.setInterval(self._interval_ms())

    def stop(self) -> None:
        """Servisi tamamen devre dışı bırakır (zamanlayıcı + tüm olay/sinyal bağları). Tekrar çağrılabilir."""
        self._timer.stop()
        if self._stopped:
            return
        self._stopped = True
        self._unsub()
        proj = self.ctx.project
        for signal, slot in ((proj.opened, self._on_opened), (proj.aboutToClose, self._on_about_to_close),
                             (proj.closed, self._on_closed), (self.ctx.settings.changed, self._on_setting)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _ring_dir(self) -> Optional[Path]:
        root = self.ctx.project.root
        return root / HIDDEN_DIR / RING_DIR if root else None

    def _on_opened(self, _path: str) -> None:
        proj = self.ctx.project
        self._bucket_start = self._bucket_file = None
        self._last_digest = proj.data.digest() if proj.data else None
        self._error_reported = False
        # başlangıç durumu halkanın en eski güvenli noktası olsun (yoksa ya da farklıysa)
        ring = self.ring()
        if proj.data is not None and (not ring or self._ring_digest(ring[-1]) != self._last_digest):
            try:
                self._write_ring(proj.data, self._clock(), new_bucket=True)
                self._bucket_start = self._bucket_file = None      # ilk düzenleme başlangıç durumunun üzerine yazmasın
            except OSError:
                pass
        self._timer.setInterval(self._interval_ms())
        self._timer.start()
        self._set_state("idle")

    def _on_about_to_close(self) -> None:
        if self.ctx.project.is_open:
            self.tick()                                    # bekleyen son değişiklikleri kaydet
        self._timer.stop()

    def _on_closed(self) -> None:
        self._last_digest = None
        self._bucket_start = self._bucket_file = None
        self._set_state("none")

    # -- durum ------------------------------------------------------------------------------
    def _set_state(self, state: str, at: Optional[float] = None) -> None:
        self.state = state
        if at is not None:
            self._last_saved_at = at
        self.ctx.bus.emit(Topics.AUTOSAVE_STATE, {"state": state, "at": self._last_saved_at,
                                                  "ring": len(self.ring())})
        self.stateChanged.emit(state)

    def _notify(self, text: str, level: str = "info") -> None:
        self.ctx.bus.emit(Topics.NOTIFY, {"text": text, "level": level})

    # -- halka ---------------------------------------------------------------------------------
    def ring(self) -> List[RingEntry]:
        d = self._ring_dir()
        if d is None or not d.is_dir():
            return []
        out: List[RingEntry] = []
        for f in d.glob(f"{RING_PREFIX}*.json"):
            try:
                out.append(RingEntry(int(f.stem[len(RING_PREFIX):]) / 1000.0, str(f)))
            except ValueError:
                continue
        return sorted(out, key=lambda e: e.time)

    def _ring_digest(self, entry: RingEntry) -> Optional[str]:
        try:
            return json.loads(Path(entry.path).read_text(encoding="utf-8")).get("digest")
        except (OSError, ValueError):
            return None

    def _write_ring(self, data: ProjectData, now: float, *, new_bucket: bool = False) -> Path:
        d = self._ring_dir()
        assert d is not None
        path = d / f"{RING_PREFIX}{int(now * 1000):015d}.json"
        _write_json(path, {"saved_at": now, "digest": data.digest(), "project": data.to_dict()})
        same_bucket = (not new_bucket and self._bucket_start is not None and self._bucket_file is not None
                       and now - self._bucket_start < self._ring_window)
        if same_bucket and self._bucket_file != path and self._bucket_file.exists():
            try:
                self._bucket_file.unlink()                 # aynı dakikanın önceki kaydı yeni kayıtla değişir
            except OSError:
                pass
        if not same_bucket:
            self._bucket_start = now
        self._bucket_file = path
        for old in self.ring()[:-self._ring_entries]:      # sayıya göre buda (süreye göre değil)
            try:
                Path(old.path).unlink()
            except OSError:
                pass
        return path

    # -- kayıt ---------------------------------------------------------------------------------------
    def tick(self) -> None:
        """Bir otomatik kayıt turu. Değişiklik yoksa hiçbir şey yazmaz. Asla istisna atmaz."""
        proj = self.ctx.project
        if not proj.is_open or proj.data is None:
            return
        digest = proj.data.digest()
        if digest == self._last_digest and not proj.dirty:
            if self.state not in ("saved", "idle"):
                self._set_state("idle")
            return
        self._set_state("saving")
        now = self._clock()
        try:
            if digest != self._last_digest:
                self._write_ring(proj.data, now)
            proj.save(silent=True)
        except Exception as exc:
            self._set_state("error")
            if not self._error_reported:
                self._error_reported = True
                self.ctx.report_error(exc, {"source": "autosave", "path": str(proj.root)})
            return
        self._last_digest = digest
        self._error_reported = False
        self._set_state("saved", now)

    # -- geri dönüş ------------------------------------------------------------------------------------
    def _backups(self) -> List[Path]:
        d = self._ring_dir()
        return sorted(d.glob(f"{BACKUP_PREFIX}*.json")) if d and d.is_dir() else []

    def _restore(self, project_dict: Dict[str, Any], reason: str, label: str) -> None:
        proj = self.ctx.project
        proj.replace_data(ProjectData.from_dict(project_dict), reason)
        proj.save(silent=True)
        self._last_digest = proj.data.digest() if proj.data else None
        self._bucket_start = self._bucket_file = None
        try:
            self.ctx.undo.clear()                          # eski geri alma komutları artık geçersiz
        except Exception:
            pass
        self.ctx.bus.emit(Topics.PROJECT_RESTORED, {"reason": reason, "label": label})
        self._set_state("saved", self._clock())

    def rewind(self, minutes: int = 10) -> RewindResult:
        """Projeyi ``minutes`` dakika öncesine döndürür (geri alınabilir). Asla istisna atmaz."""
        minutes = max(1, int(minutes))
        proj = self.ctx.project
        if not proj.is_open or proj.data is None:
            return RewindResult(False, False, "no_project", minutes)
        try:
            self.tick()                                    # güncel durum da halkada/yedekte kaybolmasın
            entries = self.ring()
            if not entries:
                return RewindResult(True, False, "no_history", minutes)
            now = self._clock()
            target = now - minutes * 60.0
            older = [e for e in entries if e.time <= target]
            chosen = older[-1] if older else entries[0]
            actual = max(0.0, (now - chosen.time) / 60.0)
            payload = json.loads(Path(chosen.path).read_text(encoding="utf-8"))
            if payload.get("digest") == proj.data.digest():
                return RewindResult(True, False, "no_change", minutes, actual, chosen.time)
            backup = self._ring_dir() / f"{BACKUP_PREFIX}{int(now * 1000):015d}.json"   # type: ignore[union-attr]
            _write_json(backup, {"saved_at": now, "digest": proj.data.digest(), "project": proj.data.to_dict()})
            for old in self._backups()[:-MAX_BACKUPS]:
                old.unlink(missing_ok=True)
            self._restore(payload["project"], "rewind", time.strftime("%H:%M", time.localtime(chosen.time)))
            return RewindResult(True, True, "restored", minutes, actual, chosen.time)
        except Exception as exc:
            err = self.ctx.report_error(exc, {"source": "autosave"})
            return RewindResult(False, False, "error", minutes, error=err)

    def undo_rewind(self) -> RewindResult:
        """Son geri dönüşü geri alır (dönmeden önceki durumu geri yükler)."""
        if not self.ctx.project.is_open:
            return RewindResult(False, False, "no_project")
        backups = self._backups()
        if not backups:
            return RewindResult(True, False, "no_history")
        try:
            latest = backups[-1]
            payload = json.loads(latest.read_text(encoding="utf-8"))
            self._restore(payload["project"], "undo_rewind", "")
            latest.unlink(missing_ok=True)
            return RewindResult(True, True, "restored", 0, 0.0, payload.get("saved_at"))
        except Exception as exc:
            return RewindResult(False, False, "error", error=self.ctx.report_error(exc, {"source": "autosave"}))

    def _on_rewind_requested(self, payload: Any) -> None:
        minutes = 10
        if isinstance(payload, dict):
            try:
                minutes = int(payload.get("minutes", 10))
            except (TypeError, ValueError):
                minutes = 10
        res = self.rewind(minutes)
        key = {"restored": "rewind.done", "no_change": "rewind.no_change", "no_history": "rewind.no_history",
               "no_project": "rewind.no_project", "error": "rewind.error"}[res.reason]
        if res.reason == "restored" and res.minutes_actual + 0.5 < res.minutes_requested:
            key = "rewind.partial"
        when = time.strftime("%H:%M", time.localtime(res.entry_time)) if res.entry_time else ""
        self._notify(i18n.t(key, minutes=res.minutes_requested, actual=f"{res.minutes_actual:.0f}", time=when),
                     "error" if res.reason == "error" else "info")
