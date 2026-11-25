"""``EditorService`` — Video Düzenleme'nin **bütün durumu** (sayfalar yalnızca çizer; plan §0.5).

Sorumluluklar: ``proje.kadraj`` içindeki zaman çizelgesi (yükle/yaz), seçim + oynatma başlığı, **geri alınabilir** düzenlemeler
(``ctx.undo``; her düzenleme tek bir anlık-görüntü komutu), manyetik yapışma, medya kitaplığı, kare/ölçüm üretimi, işleme
(render) ve proxy **işleri** (``ctx.jobs``), kullanıcı tetiklemeli otomasyon önerileri (``propose`` → ``apply``).

Kurallar
--------
* Hiçbir otomasyon kendiliğinden çalışmaz: ``propose`` zaman çizelgesini DEĞİŞTİRMEZ; ``apply`` kullanıcı eylemidir ve tek
  bir geri alma adımıdır.
* Basit Mod 3 kanalla sınırlıdır (Video/Müzik/Seslendirme); model sınırsız kanalı destekler (Legend).
* Hatalar ``SmartError`` olarak ``errorRaised``'a gider (sayfa satır içi kart gösterir); çökme yok.
* Ses çıktısı yalnız ``.wav`` (Podcast şablonu); çıktılar ``07_Exports``, proxy'ler ``06_Proxies``.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QUndoCommand

from ...core import i18n
from ...core.contracts import Job, JobStatus, SmartError, Topics
from ...core.errors import translate
from ...storage.folders import folder_for
from . import audio, automations, filtergraph, frames, ops, proxy, render, snapping
from .errors import smart
from .probe import MediaInfo, probe
from .timeline import Clip, Timeline, Track, default_timeline, format_timecode, new_id

JOB_RENDER = "editor.render"
JOB_PROXY = "editor.proxy"
JOB_PREVIEW = "editor.preview"
SIMPLE_MAX_TRACKS = 3
IMAGE_SECONDS = 5.0
SNAP_PX = 10


def media_kind(path: str) -> Optional[str]:
    """Dosya türü (``video`` | ``audio`` | ``image``); medya değilse ``None``. Uzantı listesi tutmaz: MIME türüne bakar."""
    mime = (mimetypes.guess_type(path)[0] or "").split("/")[0]
    return mime if mime in ("video", "audio", "image") else None


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")[:40] or "proje"


@dataclass(frozen=True)
class MediaItem:
    path: str
    kind: str                       # video | audio | image
    duration: float = 0.0
    width: int = 0
    height: int = 0
    id: Optional[int] = None

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def exists(self) -> bool:
        return Path(self.path).exists()


class SnapshotCommand(QUndoCommand):
    """Zaman çizelgesinin önceki/sonraki anlık görüntüsü. ``merge_key`` aynıysa ve süre kısaysa (kaydırıcı sürükleme) birleşir."""

    def __init__(self, svc: "EditorService", text: str, before: Dict[str, Any], after: Dict[str, Any],
                 merge_key: Optional[str] = None) -> None:
        super().__init__(text)
        self.svc, self.before, self.after, self.merge_key = svc, before, after, merge_key
        self._stamp = time.monotonic()
        self._first = True

    def id(self) -> int:
        return 7101 if self.merge_key else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, SnapshotCommand) or not self.merge_key or other.merge_key != self.merge_key:
            return False
        if other._stamp - self._stamp > 1.5:
            return False
        self.after, self._stamp = other.after, other._stamp
        return True

    def redo(self) -> None:
        if self._first:                                  # ilk redo: düzenleme zaten uygulandı
            self._first = False
            return
        self.svc._apply_snapshot(self.after, "redo")

    def undo(self) -> None:
        self.svc._apply_snapshot(self.before, "undo")


class _FrameTask(QRunnable):
    def __init__(self, svc: "EditorService", token: int, tl_snap: Dict[str, Any], t: float, before: bool,
                 size: Tuple[int, int], proxies: Dict[str, str]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.svc, self.token, self.snap, self.t, self.before, self.size, self.proxies = svc, token, tl_snap, t, before, size, proxies

    def run(self) -> None:
        try:
            if self.token != self.svc._frame_token.get(self.before):         # daha yeni istek var: boşuna işleme
                return
            tl = Timeline.from_dict(self.snap["timeline"], self.snap.get("markers"))
            img = frames.extract_frame(tl, self.t, size=self.size, proxies=self.proxies, before=self.before)
            self.svc._frame_done.emit(self.token, self.before, img, None)
        except Exception as exc:
            err = exc if isinstance(exc, SmartError) else translate(exc, {"source": "editor"})
            self.svc._frame_done.emit(self.token, self.before, None, err)


class _AutoTask(QRunnable):
    def __init__(self, svc: "EditorService", name: str, fn: Callable[[], automations.Proposal]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.svc, self.name, self.fn = svc, name, fn

    def run(self) -> None:
        try:
            self.svc._auto_done.emit(self.name, self.fn(), None)
        except Exception as exc:
            err = exc if isinstance(exc, SmartError) else translate(exc, {"source": "editor"})
            self.svc._auto_done.emit(self.name, None, err)


class EditorService(QObject):
    """``ctx.service("editor")``."""

    timelineChanged = Signal(str)        # sebep: "edit" | "undo" | "redo" | "load"
    selectionChanged = Signal()
    playheadChanged = Signal(float)
    libraryChanged = Signal()
    errorRaised = Signal(object)         # SmartError
    jobChanged = Signal(object)          # render/proxy/önizleme işi değişti (Job)
    frameReady = Signal(int, bool, object)       # (jeton, önce_mi, (H,W,3) uint8)
    proposalReady = Signal(str, object)          # (ad, Proposal) — kullanıcı "Uygula" diyene kadar bekler
    snapped = Signal(str)                        # yapışma hedefi ("clip_start" …): "Yapıştı!" balonu
    notice = Signal(str)
    exportFinished = Signal(str)                 # çıktı dosyası yolu
    transcriptReady = Signal(str, object)        # (klip kimliği, [Segment]) — metinle kurgu için döküm hazır
    hardwareChanged = Signal()                   # donanım kodlayıcı algılaması bitti (arka plan)
    _frame_done = Signal(int, bool, object, object)
    _hw_done = Signal()
    _tr_done = Signal(str, object, object)
    _auto_done = Signal(str, object, object)

    def __init__(self, ctx: Any = None) -> None:
        super().__init__(ctx if isinstance(ctx, QObject) else None)
        self.ctx = ctx
        self.tl: Timeline = default_timeline()
        self.selection: List[str] = []
        self.playhead = 0.0
        self.snap_enabled = True
        self.last_error: Optional[SmartError] = None
        self.proposal: Optional[automations.Proposal] = None
        self.opener: Callable[[QUrl], Any] = QDesktopServices.openUrl
        self._local_undo: Any = None
        self._unsubs: List[Callable[[], None]] = []
        self._frame_token: Dict[bool, int] = {False: 0, True: 0}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._frame_done.connect(self._on_frame_done)
        self._hw_done.connect(self.hardwareChanged)
        self._tr_done.connect(self._on_transcript)
        self._auto_done.connect(self._on_auto_done)
        self._recording: Optional[automations.Recording] = None
        self._media: List[MediaItem] = []
        self._last_export: Optional[str] = None
        self._hw_cache: Optional[Dict[str, bool]] = None
        self._handled: set = set()                       # sonucu işlenmiş iş kimlikleri (JOB_UPDATED birden çok kez gelir)

    # ------------------------------------------------------------------------------------------------------ yaşam döngüsü
    def start(self, ctx: Any) -> None:
        self.ctx = ctx
        ctx.jobs.register_handler(JOB_RENDER, self._run_render)
        ctx.jobs.register_handler(JOB_PROXY, self._run_proxy)
        ctx.jobs.register_handler(JOB_PREVIEW, self._run_preview)
        self._unsubs += [ctx.bus.subscribe(Topics.JOB_UPDATED, self._on_job_updated),
                         ctx.bus.subscribe(Topics.PROJECT_OPENED, lambda _p: self.reload("load")),
                         ctx.bus.subscribe(Topics.PROJECT_CLOSED, lambda _p: self.reload("load"))]
        try:
            ctx.project.dataReplaced.connect(lambda _r: self.reload("load"))
        except Exception:
            pass
        self.snap_enabled = bool(ctx.settings.get("editor.snap", True))
        render.start_hardware_detection(self._hw_done.emit)      # arka planda; arayüz hiç beklemez
        self.reload("load")

    def stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        if self._recording is not None:
            try:
                self._recording.stop()
            except Exception:
                pass
            self._recording = None
        self._pool.waitForDone(2000)

    @property
    def project_open(self) -> bool:
        return bool(self.ctx is not None and self.ctx.project.is_open)

    @property
    def undo_stack(self):
        if self.ctx is not None:
            return self.ctx.undo
        if self._local_undo is None:
            from PySide6.QtGui import QUndoStack
            self._local_undo = QUndoStack(self)
        return self._local_undo

    def reload(self, reason: str = "load") -> None:
        data = self.ctx.project.data if self.project_open else None
        if data is None:
            self.tl = default_timeline()
        else:
            self.tl = Timeline.from_dict(data.timeline, data.markers, data.settings)
            if not self.tl.tracks:
                dflt = default_timeline(self.tl.fps, self.tl.width, self.tl.height)
                self.tl.tracks = dflt.tracks
        self.selection = [s for s in self.selection if self.tl.find_clip(s)]
        self.playhead = min(self.playhead, self.tl.duration)
        self.refresh_library()
        self.timelineChanged.emit(reason)
        self.selectionChanged.emit()

    def _write_back(self) -> None:
        if not self.project_open or self.ctx.project.data is None:
            return
        d = self.ctx.project.data
        d.timeline = self.tl.to_dict()
        d.markers = [m.to_dict() for m in self.tl.markers]
        self.ctx.project.mark_dirty()

    def _apply_snapshot(self, snap: Dict[str, Any], reason: str) -> None:
        self.tl.restore(snap)
        self.selection = [s for s in self.selection if self.tl.find_clip(s)]
        self._write_back()
        self.timelineChanged.emit(reason)
        self.selectionChanged.emit()

    # ------------------------------------------------------------------------------------------------------ düzenleme çekirdeği
    def _fail(self, exc: BaseException) -> None:
        if isinstance(exc, ops.LockedError):
            err = smart("locked")
        elif isinstance(exc, SmartError):
            err = exc
        else:
            err = translate(exc, {"source": "editor"})
        self.last_error = err
        self.errorRaised.emit(err)

    def _do(self, label_key: str, fn: Callable[[Timeline], Any], *, merge_key: Optional[str] = None, **fmt: Any) -> Any:
        """Bir düzenlemeyi uygular ve **tek** geri alma adımı olarak yığına koyar. Hata → ``errorRaised`` ve ``None``."""
        before = self.tl.snapshot()
        try:
            result = fn(self.tl)
        except (ops.EditError, SmartError, ValueError, OSError) as exc:
            self.tl.restore(before)
            self._fail(exc)
            return None
        after = self.tl.snapshot()
        if before == after:
            return result
        self._write_back()
        self.undo_stack.push(SnapshotCommand(self, i18n.t(label_key, **fmt) if i18n.has(label_key) else label_key,
                                             before, after, merge_key))
        self.timelineChanged.emit("edit")
        return result

    def edit(self, label: str, fn: Callable[[Timeline], Any]) -> Any:
        """Dışarıdan (ör. Dizi/Film montajı) **tek geri alma adımı** olarak düzenleme (``label`` i18n anahtarı ya da düz metin)."""
        return self._do(label, fn)

    def undo(self) -> None:
        if self.undo_stack.canUndo():
            self.undo_stack.undo()

    def redo(self) -> None:
        if self.undo_stack.canRedo():
            self.undo_stack.redo()

    # ------------------------------------------------------------------------------------------------------ seçim / başlık
    def select(self, ids: Sequence[str]) -> None:
        ids = [i for i in ids if self.tl.find_clip(i)]
        if ids != self.selection:
            self.selection = ids
            self.selectionChanged.emit()

    @property
    def selected_clip(self) -> Optional[Clip]:
        found = self.tl.find_clip(self.selection[0]) if self.selection else None
        return found[1] if found else None

    def set_playhead(self, t: float, *, snap: bool = False, px_per_s: float = 60.0) -> float:
        t = max(0.0, min(t, max(self.tl.duration, 0.0)))
        if snap and self.snap_enabled:
            r = snapping.snap_time(self.tl, t, SNAP_PX / max(1.0, px_per_s))
            if r.snapped:
                t = r.time
                self.snapped.emit(r.target or "")
        t = filtergraph_snap(t, self.tl.fps)
        if abs(t - self.playhead) > 1e-9:
            self.playhead = t
            self.playheadChanged.emit(t)
        return t

    def step_frames(self, n: int) -> float:
        return self.set_playhead(self.playhead + n / filtergraph.fps_value(self.tl.fps))

    def timecode(self, t: Optional[float] = None) -> str:
        return format_timecode(self.playhead if t is None else t, self.tl.fps)

    def set_snap(self, enabled: bool) -> None:
        self.snap_enabled = bool(enabled)
        if self.ctx is not None:
            self.ctx.settings.set("editor.snap", self.snap_enabled)

    # ------------------------------------------------------------------------------------------------------ kanallar
    def can_add_track(self, simple: bool) -> bool:
        return not (simple and len(self.tl.tracks) >= SIMPLE_MAX_TRACKS)

    def add_track(self, kind: str = "video", name: str = "") -> Optional[Track]:
        return self._do("editor.undo.add_track", lambda tl: ops.add_track(tl, kind, name))

    def remove_track(self, track_id: str) -> None:
        self._do("editor.undo.remove_track", lambda tl: ops.remove_track(tl, track_id))

    def set_track(self, track_id: str, merge: bool = False, **props: Any) -> None:
        self._do("editor.undo.track", lambda tl: ops.set_track(tl, track_id, **props), merge_key=f"track:{track_id}:{','.join(props)}" if merge else None)

    # ------------------------------------------------------------------------------------------------------ klipler
    def add_media(self, path: str, *, track_id: Optional[str] = None, start: Optional[float] = None) -> Optional[Clip]:
        """Dosyayı uygun kanala klip olarak ekler (video → video kanalı, ses → ses kanalı, görsel → video kanalı 5 sn)."""
        try:
            info = probe(path)
        except SmartError as exc:
            self._fail(exc)
            return None
        want = "audio" if info.kind == "audio" else "video"
        tr = self.tl.track(track_id) if track_id else None
        if tr is None:
            tr = next((t for t in self.tl.tracks if t.kind == want and not t.locked), None)
        at = start if start is not None else (tr.duration if tr is not None else 0.0)

        def build(tl: Timeline) -> Clip:
            target = tl.track(tr.id) if tr is not None else ops.add_track(tl, want)
            if target is None:
                target = ops.add_track(tl, want)
            if info.is_image:
                c = Clip(new_id(), "image", path, in_=0.0, out=IMAGE_SECONDS, start=at, duration_hint=IMAGE_SECONDS, name=Path(path).name)
            else:
                c = Clip(new_id(), "media", path, in_=0.0, out=max(0.1, info.duration), start=at, name=Path(path).name)
            return ops.add_clip(tl, target.id, c)
        clip = self._do("editor.undo.add_clip", build, name=Path(path).name)
        if clip is not None:
            self._register_media(path, info)
            self.select([clip.id])
            if self.ctx is not None and self.ctx.settings.get("editor.auto_proxy", True) and proxy.needs_proxy(info):
                self.queue_proxy(path)
        return clip

    def import_files(self, paths: Sequence[str]) -> List[Clip]:
        out = []
        for p in paths:
            c = self.add_media(p)
            if c:
                out.append(c)
        return out

    def move_clip(self, clip_id: str, new_start: float, track_id: Optional[str] = None, *, px_per_s: float = 60.0,
                  snap: bool = True) -> snapping.SnapResult:
        found = self.tl.find_clip(clip_id)
        if found is None:
            return snapping.SnapResult(new_start)
        r = snapping.snap_move(self.tl, found[1], max(0.0, new_start), SNAP_PX / max(1.0, px_per_s), playhead=self.playhead,
                               enabled=snap and self.snap_enabled)
        self._do("editor.undo.move", lambda tl: ops.move_clip(tl, clip_id, r.time, track_id))
        if r.snapped:
            self.snapped.emit(r.target or "")
        return r

    def trim_clip(self, clip_id: str, edge: str, t: float, *, px_per_s: float = 60.0) -> None:
        if self.snap_enabled:
            r = snapping.snap_time(self.tl, t, SNAP_PX / max(1.0, px_per_s), exclude=[clip_id], playhead=self.playhead)
            if r.snapped:
                t = r.time
                self.snapped.emit(r.target or "")
        self._do("editor.undo.trim", lambda tl: ops.trim_clip(tl, clip_id, edge, t))

    def split_at_playhead(self) -> Optional[Tuple[Clip, Clip]]:
        ph = self.playhead
        ids = [i for i in self.selection if (f := self.tl.find_clip(i)) and f[1].start < ph < f[1].end] or \
            [c.id for _t, c in self.tl.clips_at(ph, ("video", "audio"))]
        res = None
        for cid in ids:
            found = self.tl.find_clip(cid)
            if found and found[1].start < self.playhead < found[1].end:
                res = self._do("editor.undo.split", lambda tl, cid=cid: ops.split_clip(tl, cid, self.playhead))
        return res

    def delete_selection(self, ripple: bool = False) -> None:
        ids = list(self.selection)
        if not ids:
            self._fail(smart("no_clip"))
            return

        def rm(tl: Timeline) -> None:
            for cid in ids:
                ops.delete_clip(tl, cid, ripple)
        self._do("editor.undo.delete", rm)
        self.select([])

    def set_speed(self, clip_id: str, speed: float) -> None:
        self._do("editor.undo.speed", lambda tl: ops.set_speed(tl, clip_id, speed), merge_key=f"speed:{clip_id}")

    def set_speed_curve(self, clip_id: str, points: Sequence[Tuple[float, float]]) -> None:
        self._do("editor.undo.speed", lambda tl: ops.set_speed_curve(tl, clip_id, points))

    def set_clip(self, clip_id: str, **props: Any) -> None:
        self._do("editor.undo.clip", lambda tl: ops.set_clip(tl, clip_id, **props), merge_key=f"clip:{clip_id}:{','.join(props)}")

    def add_effect(self, clip_id: str, effect: Dict[str, Any]) -> None:
        self._do("editor.undo.effect", lambda tl: ops.add_effect(tl, clip_id, effect))

    def remove_effect(self, clip_id: str, index: int) -> None:
        self._do("editor.undo.effect", lambda tl: ops.remove_effect(tl, clip_id, index))

    def update_effect(self, clip_id: str, index: int, params: Dict[str, Any]) -> None:
        def fn(tl: Timeline) -> None:
            _tr, c = ops._get(tl, clip_id)
            c.effects[index].setdefault("params", {}).update(params)
        self._do("editor.undo.effect", fn, merge_key=f"fx:{clip_id}:{index}")

    def set_keyframe(self, clip_id: str, prop: str, t: float, v: float, ease: str = "linear", bezier=None) -> None:
        self._do("editor.undo.keyframe", lambda tl: ops.set_keyframe(tl, clip_id, prop, t, v, ease, bezier))

    def remove_keyframe(self, clip_id: str, prop: str, t: float) -> None:
        self._do("editor.undo.keyframe", lambda tl: ops.remove_keyframe(tl, clip_id, prop, t))

    def add_adjust_layer(self, effects: Optional[List[Dict[str, Any]]] = None, duration: Optional[float] = None) -> Optional[Clip]:
        d = duration or max(1.0, self.tl.duration - self.playhead)
        tr = next((t for t in self.tl.tracks if t.kind == "adj"), None)
        return self._do("editor.undo.adjust", lambda tl: ops.add_adjust_clip(tl, tr.id if tr else "ADJ1", self.playhead, d, effects))

    def set_canvas(self, width: int, height: int) -> None:
        """Proje çözünürlüğü (Basit: Yatay 1920×1080 / Dikey 1080×1920)."""
        def fn(tl: Timeline) -> None:
            tl.width, tl.height = int(width), int(height)
        self._do("editor.undo.canvas", fn)

    @property
    def orientation(self) -> str:
        return "portrait" if self.tl.height > self.tl.width else "landscape"

    # -- işaretçiler / bölümler -----------------------------------------------------------------------------------------
    def add_marker(self, t: Optional[float] = None, label: str = "", chapter: bool = False) -> None:
        at = self.playhead if t is None else t
        self._do("editor.undo.marker", lambda tl: ops.add_marker(tl, at, label, chapter))

    def remove_marker(self, t: float) -> None:
        self._do("editor.undo.marker", lambda tl: ops.remove_marker(tl, t))

    def chapters_text(self) -> str:
        return ops.youtube_chapters(self.tl)

    def chapter_problems(self) -> List[str]:
        return ops.chapter_problems(self.tl)

    # -- ses ---------------------------------------------------------------------------------------------------------------
    def set_eq(self, gains: Sequence[float]) -> None:
        g = audio.clamp_eq(gains)
        self._do("editor.undo.eq", lambda tl: setattr(tl, "eq", g), merge_key="eq")

    def set_master_db(self, db: float) -> None:
        self._do("editor.undo.mixer", lambda tl: setattr(tl, "master_db", float(db)), merge_key="master")

    # ------------------------------------------------------------------------------------------------------ kitaplık
    def _register_media(self, path: str, info: MediaInfo) -> None:
        if not self.project_open:
            return
        try:
            self.ctx.project.db.add_media(path, info.kind, duration=info.duration or None, width=info.width or None,
                                          height=info.height or None, fps=info.fps or None, codec=info.vcodec or info.acodec or None,
                                          sample_rate=info.sample_rate or None)
        except Exception:
            pass
        self.refresh_library()

    def refresh_library(self) -> None:
        items: List[MediaItem] = []
        if self.project_open:
            try:
                for row in self.ctx.project.db.list_media():
                    items.append(MediaItem(str(row["path"]), str(row["kind"] or "video"), float(row["duration"] or 0.0),
                                           int(row["width"] or 0), int(row["height"] or 0), int(row["id"])))
            except Exception:
                pass
        seen = {i.path for i in items}
        for _t, c in self.tl.all_clips():
            if c.src and c.src not in seen and Path(c.src).exists():
                seen.add(c.src)
                items.append(MediaItem(c.src, media_kind(c.src) or "video"))
        self._media = items
        self.libraryChanged.emit()

    def scan_project_folders(self) -> int:
        """Proje klasörlerindeki (indirilenler, ses, AI) bilinmeyen medyayı kitaplığa ekler. Dönüş: eklenen sayısı."""
        if not self.project_open:
            return 0
        known = {m.path for m in self._media}
        added = 0
        for kind in ("download", "wav", "ai_image", "ai_scene"):
            folder = self.ctx.project.subfolder(folder_for(kind))
            for p in sorted(Path(folder).glob("*")):
                if media_kind(str(p)) and str(p) not in known:
                    try:
                        self._register_media(str(p), probe(str(p)))
                        added += 1
                    except SmartError:
                        continue
        return added

    def media_items(self, kind: Optional[str] = None) -> List[MediaItem]:
        return [m for m in self._media if kind in (None, m.kind)]

    # ------------------------------------------------------------------------------------------------------ kare / ölçüm
    def request_frame(self, t: Optional[float] = None, *, before: bool = False, size: Tuple[int, int] = (480, 270)) -> int:
        """Arka planda tek kare üretir; sonuç ``frameReady(jeton, önce_mi, görüntü)``. Yalnızca en yeni istek işlenir."""
        self._frame_token[before] = self._frame_token.get(before, 0) + 1
        token = self._frame_token[before]
        if self.tl.duration <= 0:
            return token
        px = proxy.proxy_map(self.tl, self._proxy_dir()) if self.project_open else {}
        self._pool.start(_FrameTask(self, token, self.tl.snapshot(), self.playhead if t is None else t, before, size, px))
        return token

    def _on_frame_done(self, token: int, before: bool, img: Any, err: Any) -> None:
        if token != self._frame_token.get(before):
            return
        if err is not None:
            self.last_error = err
            self.errorRaised.emit(err)
        elif img is not None:
            self.frameReady.emit(token, before, img)

    def frame_now(self, t: Optional[float] = None, *, before: bool = False, size: Tuple[int, int] = (480, 270)) -> np.ndarray:
        """Eşzamanlı kare (testler / ölçüm). ``SmartError`` atabilir."""
        px = proxy.proxy_map(self.tl, self._proxy_dir()) if self.project_open else {}
        return frames.extract_frame(self.tl, self.playhead if t is None else t, size=size, proxies=px, before=before)

    # ------------------------------------------------------------------------------------------------------ işleme (render)
    def _proxy_dir(self) -> Path:
        return self.ctx.project.subfolder(folder_for("proxy"))

    def hardware(self) -> Dict[str, bool]:
        """Algılanmış donanım (algılama sürüyorsa ``False``'lar; bitince ``hardwareChanged``)."""
        return render.detect_hardware(block=False)

    def estimate(self, settings: render.RenderSettings) -> render.Estimate:
        dur = self.tl.duration
        if settings.t_start is not None or settings.t_end is not None:
            dur = max(0.0, min(dur, settings.t_end if settings.t_end is not None else dur) - max(0.0, settings.t_start or 0.0))
        w, h = settings.width or self.tl.width, settings.height or self.tl.height
        return render.estimate(settings, dur, w, h, settings.fps or self.tl.fps,
                               render.choose_encoder(settings.codec, settings.hw, block=False).backend)

    def template_settings(self, template_id: str, quality: Optional[str] = None) -> render.RenderSettings:
        s = render.template_settings(template_id)
        if quality in render.QUALITIES:
            s.quality = quality
        return s

    def output_path(self, settings: render.RenderSettings) -> Path:
        if not self.project_open:
            raise smart("no_project")
        folder = self.ctx.project.subfolder(folder_for("export"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"{_safe_name(self.ctx.project.name)}_{settings.template or settings.codec}_{stamp}"
        cand = Path(folder) / f"{base}{settings.ext}"
        n = 2
        while cand.exists():
            cand = Path(folder) / f"{base}_{n}{settings.ext}"
            n += 1
        return cand

    def start_render(self, settings: render.RenderSettings) -> Optional[Job]:
        """İşleme işini kuyruğa atar (geçerli zaman çizelgesinin anlık görüntüsüyle). Hata → ``errorRaised``."""
        try:
            if self.tl.duration <= 0:
                raise smart("empty_timeline")
            out = self.output_path(settings)
            snap = self.tl.snapshot()
            name = out.name
            return self.ctx.jobs.submit(JOB_RENDER, {"timeline": snap["timeline"], "markers": snap["markers"],
                                                     "settings": settings.to_dict(), "output": str(out), "name": name},
                                        title=i18n.t("editor.job.render", name=name))
        except (SmartError, OSError) as exc:
            self._fail(exc)
            return None

    def _run_render(self, job: Job, jc: Any) -> Dict[str, Any]:
        p = job.payload
        tl = Timeline.from_dict(p["timeline"], p.get("markers"))
        s = render.RenderSettings.from_dict(p.get("settings"))
        plan = render.build_plan(tl, s, p["output"])
        out = render.execute_plan(plan, report=jc.report, cancelled=jc.is_cancelled)
        return {"output": out, "size": Path(out).stat().st_size, "warnings": plan.warnings, "backend": plan.encoder.backend}

    def queue_proxy(self, path: str) -> Optional[Job]:
        if not self.project_open:
            return None
        return self.ctx.jobs.submit(JOB_PROXY, {"path": path, "dir": str(self._proxy_dir())}, title=i18n.t("editor.job.proxy", name=Path(path).name),
                                    notify=False)

    def _run_proxy(self, job: Job, jc: Any) -> Dict[str, Any]:
        out = proxy.make_proxy(job.payload["path"], Path(job.payload["dir"]), report=jc.report, cancelled=jc.is_cancelled)
        return {"proxy": out}

    def render_preview(self) -> Optional[Job]:
        """Oynatma için düşük çözünürlüklü birleşik önizleme dosyası üretir (``.kadraj/cache/preview/<özet>.mp4``)."""
        if self.tl.duration <= 0 or not self.project_open:
            return None
        out = self.preview_path()
        if out.exists():
            return None
        snap = self.tl.snapshot()
        px = proxy.proxy_map(self.tl, self._proxy_dir())
        return self.ctx.jobs.submit(JOB_PREVIEW, {"timeline": snap["timeline"], "markers": snap["markers"], "output": str(out),
                                                  "proxies": px}, title=i18n.t("editor.job.proxy", name=out.name), notify=False)

    def digest(self) -> str:
        blob = json.dumps(self.tl.snapshot(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]

    def preview_path(self) -> Path:
        base = (self.ctx.project.root / ".kadraj" / "cache" / "preview") if self.project_open else Path("/tmp/kadraj-preview")
        return base / f"{self.digest()}.mp4"

    def _run_preview(self, job: Job, jc: Any) -> Dict[str, Any]:
        p = job.payload
        tl = Timeline.from_dict(p["timeline"], p.get("markers"))
        w = 640
        s = render.RenderSettings(codec="h264", quality="fast", width=w, height=int(w * tl.height / max(1, tl.width)) // 2 * 2,
                                  fps=min(30.0, tl.fps), lufs=None, hw="cpu", chapters=False)
        plan = render.build_plan(tl, s, p["output"], proxies=p.get("proxies") or None)
        return {"output": render.execute_plan(plan, report=jc.report, cancelled=jc.is_cancelled)}

    def _on_job_updated(self, job: Job) -> None:
        if job.kind not in (JOB_RENDER, JOB_PROXY, JOB_PREVIEW):
            return
        self.jobChanged.emit(job)
        if job.finished:
            if job.id in self._handled:
                return
            self._handled.add(job.id)
        if job.kind == JOB_RENDER and job.status is JobStatus.DONE:
            res = job.payload.get("_result") or {}
            self._last_export = res.get("output")
            if self._last_export:
                self.exportFinished.emit(self._last_export)
                if self.ctx is not None:
                    self.ctx.bus.emit(Topics.NOTIFY, {"text": i18n.t("editor.job.done", name=Path(self._last_export).name), "level": "info"})
        elif job.kind == JOB_RENDER and job.status is JobStatus.FAILED and job.payload.get("_error"):
            try:
                from ..recorder.service import smart_error_from_dict
                self.errorRaised.emit(smart_error_from_dict(job.payload["_error"]))
            except Exception:
                pass

    def jobs(self, kind: Optional[str] = None) -> List[Job]:
        if self.ctx is None:
            return []
        return [j for j in self.ctx.jobs.list() if j.kind in ((kind,) if kind else (JOB_RENDER, JOB_PROXY, JOB_PREVIEW))]

    @property
    def last_export(self) -> Optional[str]:
        return self._last_export

    def reveal(self, path: Optional[str] = None) -> bool:
        """"Dosyayı Klasörde Göster": dosyanın klasörünü sistem dosya yöneticisinde açar."""
        p = Path(path or self._last_export or "")
        if not p.exists():
            return False
        return bool(self.opener(QUrl.fromLocalFile(str(p.parent))) is not False)

    # ------------------------------------------------------------------------------------------------------ otomasyonlar
    AUTOMATIONS = ("silence", "voice", "ducking", "vitalize", "colormatch", "beat", "reframe", "greenscreen", "subtitles", "watermark",
                   "freeze", "text_edit")

    def _wav_dir(self) -> Path:
        return self.ctx.project.subfolder(folder_for("wav"))

    def _make_proposal(self, name: str, **kw: Any) -> automations.Proposal:
        tl, sel = self.tl, self.selection
        cid = kw.pop("clip_id", None) or (sel[0] if sel else None)
        if name == "silence":
            return automations.plan_silence_cut(tl, cid, **kw)
        if name == "voice":
            return automations.studio_voice(tl, cid, self._wav_dir(), **kw)
        if name == "ducking":
            audio_ids = [t.id for t in tl.tracks_of("audio")]
            music = kw.pop("music_track", audio_ids[0] if audio_ids else "")
            voice = kw.pop("voice_tracks", [i for i in audio_ids if i != music][:1])
            return automations.plan_ducking(tl, music, voice, **kw)
        if name == "vitalize":
            return automations.plan_vitalize(tl, cid)
        if name == "colormatch":
            ref = kw.pop("ref_clip_id", sel[1] if len(sel) > 1 else None)
            return automations.plan_color_match(tl, cid, ref, self._proxy_dir() / "luts", **kw)
        if name == "beat":
            music = kw.pop("music_clip_id", None) or next((c.id for _t, c in tl.all_clips() if _t.kind == "audio"), None)
            vids = kw.pop("video_clip_ids", [i for i in sel if tl.find_clip(i) and tl.find_clip(i)[0].kind == "video"])
            return automations.plan_beat_sync(tl, music or "", vids, **kw)
        if name == "reframe":
            return automations.plan_reframe(tl, cid, **kw)
        if name == "greenscreen":
            if kw.pop("classic", False):
                return automations.plan_chroma_key(tl, cid, **kw)
            return automations.plan_green_screen(tl, cid, self._proxy_dir() / "cutouts", **kw)
        if name == "subtitles":
            return self._subtitles_proposal(cid, **kw)
        if name == "watermark":
            return automations.plan_watermark(tl, kw.pop("image_path"), **kw)
        if name == "freeze":
            return automations.plan_freeze(tl, cid, kw.pop("t_at", self.playhead), kw.pop("seconds", 2.0))
        if name == "text_edit":
            return automations.plan_text_edit(tl, cid, kw.pop("deleted"))
        raise ValueError(name)

    def _subtitles_proposal(self, cid: Optional[str], segments: Optional[Sequence[Any]] = None, language: str = "tr") -> automations.Proposal:
        found = self.tl.find_clip(cid or "")
        if found is None:
            raise smart("no_clip")
        c = found[1]
        if segments is None:
            from ...ai_engine.providers.whisper_local import WhisperLocalProvider
            tmp = self._wav_dir() / f"{c.id}_sub.wav"
            automations.extract_audio(c.src, str(tmp), c.in_, c.out - c.in_)
            try:
                segments = WhisperLocalProvider().transcribe(tmp, language=language).segments
            finally:
                tmp.unlink(missing_ok=True)
        return automations.plan_dynamic_subtitles(self.tl, c.id, segments, self._proxy_dir() / "subs")

    def transcribe_async(self, clip_id: Optional[str] = None, language: str = "tr") -> None:
        """Metinle kurgu için seçili klibin dökümünü arka planda çıkarır (yerel Whisper; yoksa "paketi kur" kartı)."""
        cid = clip_id or (self.selection[0] if self.selection else "")
        found = self.tl.find_clip(cid)
        if found is None:
            self._fail(smart("no_clip"))
            return
        c = found[1]
        wav_dir = self._wav_dir() if self.project_open else Path("/tmp")

        class _T(QRunnable):
            def run(_self) -> None:                                         # noqa: N805
                try:
                    from ...ai_engine.providers.whisper_local import WhisperLocalProvider
                    tmp = Path(wav_dir) / f"{c.id}_tr.wav"
                    automations.extract_audio(c.src, str(tmp), c.in_, c.out - c.in_)
                    try:
                        segs = WhisperLocalProvider().transcribe(tmp, language=language).segments
                    finally:
                        tmp.unlink(missing_ok=True)
                    self._tr_done.emit(cid, segs, None)
                except Exception as exc:
                    self._tr_done.emit(cid, None, exc if isinstance(exc, SmartError) else translate(exc, {"source": "editor"}))
        t = _T()
        t.setAutoDelete(True)
        self._pool.start(t)

    def _on_transcript(self, cid: str, segs: Any, err: Any) -> None:
        if err is not None:
            self._fail(err)
        else:
            self.transcriptReady.emit(cid, segs)

    def propose(self, name: str, **kw: Any) -> Optional[automations.Proposal]:
        """Eşzamanlı öneri (analiz; zaman çizelgesini DEĞİŞTİRMEZ). ``proposalReady`` yayınlar; hata → ``errorRaised``."""
        try:
            prop = self._make_proposal(name, **kw)
        except (SmartError, ops.EditError, ValueError, OSError) as exc:
            self._fail(exc)
            return None
        self.proposal = prop
        self.proposalReady.emit(name, prop)
        return prop

    def propose_async(self, name: str, **kw: Any) -> None:
        """Ağır analizler (sessizlik, ses işleme, yeşil perde, döküm) arka planda; sonuç ``proposalReady``."""
        self._pool.start(_AutoTask(self, name, lambda: self._make_proposal(name, **kw)))

    def _on_auto_done(self, name: str, prop: Any, err: Any) -> None:
        if err is not None:
            self._fail(err)
            return
        self.proposal = prop
        self.proposalReady.emit(name, prop)

    def apply(self, prop: Optional[automations.Proposal] = None) -> bool:
        """Kullanıcı "Uygula" dedi: öneriyi **tek** geri alma adımı olarak uygular."""
        prop = prop or self.proposal
        if prop is None or prop.empty:
            self.notice.emit(i18n.t("editor.auto.nothing"))
            return False
        before = self.tl.snapshot()
        self._do(f"editor.auto.{prop.name}", lambda tl: prop.apply(tl))
        changed = self.tl.snapshot() != before
        self.proposal = None
        if changed:
            self.notice.emit(prop.summary)
        return changed

    def dismiss_proposal(self) -> None:
        self.proposal = None

    # -- kayıt (ekran / kamera) -------------------------------------------------------------------------------------------
    def start_recording(self, kind: str, **kw: Any) -> bool:
        try:
            if self._recording is not None:
                return False
            folder = self.ctx.project.subfolder(folder_for("download")) if self.project_open else Path.home()
            out = Path(folder) / f"{kind}_{time.strftime('%Y%m%d-%H%M%S')}.mp4"
            self._recording = automations.start_recording(kind, str(out), **kw)
            return True
        except (SmartError, OSError) as exc:
            self._fail(exc)
            return False

    def stop_recording(self) -> Optional[Clip]:
        rec, self._recording = self._recording, None
        if rec is None:
            return None
        try:
            return self.add_media(rec.stop())
        except SmartError as exc:
            self._fail(exc)
            return None

    @property
    def recording(self) -> bool:
        return self._recording is not None and self._recording.running


def filtergraph_snap(t: float, fps: float) -> float:
    from .timeline import snap_to_frame
    return snap_to_frame(t, fps)
