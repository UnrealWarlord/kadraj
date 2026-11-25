"""Zaman çizelgesi düzenleme işlemleri (saf Python, Qt yok). Hepsi ``Timeline``'ı yerinde değiştirir; geri alma
``EditorService`` içinde anlık görüntü (snapshot) farkıyla yapılır — burada yalnızca mantık vardır.

Kural: kilitli kanallara dokunulmaz (``LockedError``); süre/zaman ``timeline.fps`` karesine yuvarlanır.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .timeline import (CHANNEL_MODES, Clip, Keyframe, MAX_SPEED, MIN_SPEED, Marker, Timeline, Track, TRACK_KINDS, interpolate, new_id,
                       snap_to_frame)

MIN_CLIP = 1 / 60


class EditError(ValueError):
    """Geçersiz düzenleme (bilinmeyen klip, aralık dışı vb.)."""


class LockedError(EditError):
    """Kilitli kanal."""


def _fr(tl: Timeline, t: float) -> float:
    return snap_to_frame(t, tl.fps)


def _get(tl: Timeline, clip_id: str, allow_locked: bool = False) -> Tuple[Track, Clip]:
    found = tl.find_clip(clip_id)
    if found is None:
        raise EditError(f"klip yok: {clip_id}")
    if found[0].locked and not allow_locked:
        raise LockedError(found[0].id)
    return found


def _track(tl: Timeline, track_id: str) -> Track:
    tr = tl.track(track_id)
    if tr is None:
        raise EditError(f"kanal yok: {track_id}")
    if tr.locked:
        raise LockedError(track_id)
    return tr


# -- kanallar ----------------------------------------------------------------------------------
def add_track(tl: Timeline, kind: str = "video", name: str = "", index: Optional[int] = None) -> Track:
    if kind not in TRACK_KINDS:
        raise EditError(f"geçersiz kanal türü: {kind}")
    tr = Track(tl.unique_track_id(kind), kind, name)
    tl.tracks.insert(len(tl.tracks) if index is None else max(0, min(index, len(tl.tracks))), tr)
    return tr


def remove_track(tl: Timeline, track_id: str) -> Track:
    tr = _track(tl, track_id)
    tl.tracks.remove(tr)
    return tr


def set_track(tl: Timeline, track_id: str, **props: Any) -> Track:
    tr = tl.track(track_id)
    if tr is None:
        raise EditError(f"kanal yok: {track_id}")
    for k, v in props.items():
        if k in ("muted", "locked", "solo", "hidden"):
            setattr(tr, k, bool(v))
        elif k in ("volume_db", "pan"):
            setattr(tr, k, float(v))
        elif k == "name":
            tr.name = str(v)
        elif k == "channels":
            if v not in CHANNEL_MODES:
                raise EditError(f"geçersiz kanal modu: {v}")
            tr.channels = str(v)
        else:
            raise EditError(f"bilinmeyen kanal özelliği: {k}")
    return tr


# -- klipler -----------------------------------------------------------------------------------
def free_start(tr: Track, start: float, duration: float) -> float:
    """``start``'tan itibaren ``duration`` uzunluğunda çakışmasız ilk boşluk (yalnızca "yeri bul" yardımcısı)."""
    t = max(0.0, start)
    for c in tr.sorted_clips():
        if c.end <= t:
            continue
        if c.start >= t + duration:
            break
        t = c.end
    return t


def add_clip(tl: Timeline, track_id: str, clip: Clip, *, avoid_overlap: bool = False) -> Clip:
    tr = _track(tl, track_id)
    if tl.find_clip(clip.id):
        clip.id = new_id()
    clip.start = _fr(tl, free_start(tr, clip.start, clip.duration) if avoid_overlap else clip.start)
    tr.clips.append(clip)
    return clip


def add_adjust_clip(tl: Timeline, track_id: str, start: float, duration: float,
                    effects: Optional[List[Dict[str, Any]]] = None, name: str = "ADJ") -> Clip:
    tr = tl.track(track_id)
    if tr is None or tr.kind != "adj":
        tr = add_track(tl, "adj") if tr is None else tr
    clip = Clip(new_id(), "adjust", name=name, start=_fr(tl, start), duration_hint=max(MIN_CLIP, duration),
                effects=copy.deepcopy(effects or []))
    tr.clips.append(clip)
    return clip


def move_clip(tl: Timeline, clip_id: str, new_start: float, track_id: Optional[str] = None) -> Clip:
    tr, c = _get(tl, clip_id)
    c.start = _fr(tl, max(0.0, new_start))
    if track_id and track_id != tr.id:
        dst = _track(tl, track_id)
        tr.clips.remove(c)
        dst.clips.append(c)
    return c


def delete_clip(tl: Timeline, clip_id: str, ripple: bool = False) -> Clip:
    tr, c = _get(tl, clip_id)
    tr.clips.remove(c)
    if ripple:
        dur = c.duration
        for o in tr.clips:
            if o.start >= c.end - 1e-9:
                o.start = _fr(tl, o.start - dur)
    return c


def split_clip(tl: Timeline, clip_id: str, t: float) -> Tuple[Clip, Clip]:
    """``t`` (zaman çizelgesi zamanı) noktasında böler. Hız eğrisi/anahtar kareler iki yarıya yeniden dağıtılır."""
    tr, c = _get(tl, clip_id)
    t = _fr(tl, t)
    if not (c.start + MIN_CLIP < t < c.end - MIN_CLIP):
        raise EditError("bölme noktası klibin içinde olmalı")
    local = t - c.start
    right = c.copy()
    cut_src = c.source_time(local)
    # sol: [in, cut]; sağ: [cut, out]
    right.in_ = cut_src
    right.start = t
    c.out = cut_src
    c.fade_out, right.fade_in = 0.0, 0.0
    for name, kfs in list(c.keyframes.items()):
        c.keyframes[name] = [k for k in kfs if k.t <= local + 1e-9]
        right.keyframes[name] = [Keyframe(k.t - local, k.v, k.ease, k.bezier) for k in kfs if k.t >= local - 1e-9]
        v_at = interpolate(kfs, local) if kfs else None
        if v_at is not None:
            if not any(abs(k.t - local) < 1e-6 for k in c.keyframes[name]):
                c.keyframes[name].append(Keyframe(local, float(v_at)))
            if not any(abs(k.t) < 1e-6 for k in right.keyframes[name]):
                right.keyframes[name].insert(0, Keyframe(0.0, float(v_at)))
    if c.speed_curve:
        v = float(interpolate(c.speed_curve, local))
        right.speed_curve = [Keyframe(0.0, v)] + [Keyframe(k.t - local, k.v, k.ease, k.bezier)
                                                   for k in c.speed_curve if k.t > local + 1e-9]
        c.speed_curve = [k for k in c.speed_curve if k.t < local - 1e-9] + [Keyframe(local, v)]
    if c.kind in ("adjust", "text"):
        c.duration_hint, right.duration_hint = local, right.duration_hint - local
    tr.clips.append(right)
    return c, right


def trim_clip(tl: Timeline, clip_id: str, edge: str, new_time: float) -> Clip:
    """Klip kenarını (``"start"`` | ``"end"``) zaman çizelgesi zamanında ``new_time``'a çeker; kaynak aralığı buna göre değişir."""
    _tr, c = _get(tl, clip_id)
    new_time = _fr(tl, new_time)
    if edge == "start":
        new_time = max(0.0, min(new_time, c.end - MIN_CLIP))
        delta = new_time - c.start
        if c.kind in ("adjust", "text"):
            c.duration_hint -= delta
        else:
            c.in_ = max(0.0, c.source_time(delta))
        c.start = new_time
        c.fade_in = min(c.fade_in, c.duration)
    elif edge == "end":
        new_time = max(c.start + MIN_CLIP, new_time)
        if c.kind in ("adjust", "text", "freeze") or (c.kind == "image"):
            c.duration_hint = new_time - c.start
            if c.kind == "image":
                c.out = c.in_ + c.duration_hint
        else:
            c.out = max(c.in_ + MIN_CLIP, c.source_time(new_time - c.start))
        c.fade_out = min(c.fade_out, c.duration)
    else:
        raise EditError(f"geçersiz kenar: {edge}")
    return c


def set_speed(tl: Timeline, clip_id: str, speed: float) -> Clip:
    _tr, c = _get(tl, clip_id)
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise EditError(f"hız {MIN_SPEED}–{MAX_SPEED} arasında olmalı")
    c.speed = float(speed)
    c.speed_curve = []
    return c


def set_speed_curve(tl: Timeline, clip_id: str, points: Iterable[Tuple[float, float]], ease: str = "ease_in_out") -> Clip:
    """Hız eğrisi: ``(yerel_zaman, hız)`` noktaları; ilk nokta 0'dan başlar. Boş liste eğriyi kaldırır."""
    _tr, c = _get(tl, clip_id)
    c.speed_curve = [Keyframe(float(t), float(v), ease) for t, v in sorted(points)]
    return c


def set_clip(tl: Timeline, clip_id: str, **props: Any) -> Clip:
    _tr, c = _get(tl, clip_id)
    for k, v in props.items():
        if k in ("volume_db", "fade_in", "fade_out"):
            setattr(c, k, max(0.0, float(v)) if k != "volume_db" else float(v))
        elif k in ("name", "src"):
            setattr(c, k, str(v))
        elif k == "enabled":
            c.enabled = bool(v)
        else:
            raise EditError(f"bilinmeyen klip özelliği: {k}")
    return c


# -- efekt / keyframe ---------------------------------------------------------------------------
def add_effect(tl: Timeline, clip_id: str, effect: Dict[str, Any]) -> Dict[str, Any]:
    _tr, c = _get(tl, clip_id)
    eff = {"enabled": True, "params": {}, **copy.deepcopy(effect)}
    c.effects.append(eff)
    return eff


def remove_effect(tl: Timeline, clip_id: str, index: int) -> Dict[str, Any]:
    _tr, c = _get(tl, clip_id)
    try:
        return c.effects.pop(index)
    except IndexError:
        raise EditError("efekt yok") from None


def set_keyframe(tl: Timeline, clip_id: str, prop: str, t: float, v: float, ease: str = "linear",
                 bezier: Optional[Tuple[float, float, float, float]] = None) -> Keyframe:
    _c_tr, c = _get(tl, clip_id)
    t = max(0.0, min(_fr(tl, t), c.duration))
    kfs = c.keyframes.setdefault(prop, [])
    for k in kfs:
        if abs(k.t - t) < 1e-6:
            k.v, k.ease, k.bezier = float(v), ease, bezier
            return k
    kf = Keyframe(t, float(v), ease, bezier)
    kfs.append(kf)
    kfs.sort(key=lambda k: k.t)
    return kf


def remove_keyframe(tl: Timeline, clip_id: str, prop: str, t: float) -> None:
    _tr, c = _get(tl, clip_id)
    kfs = c.keyframes.get(prop, [])
    c.keyframes[prop] = [k for k in kfs if abs(k.t - t) > 1e-6]
    if not c.keyframes[prop]:
        del c.keyframes[prop]


# -- işaretçiler / bölümler ------------------------------------------------------------------------
def add_marker(tl: Timeline, t: float, label: str = "", chapter: bool = False) -> Marker:
    m = Marker(_fr(tl, t), label, chapter)
    tl.markers.append(m)
    tl.markers.sort(key=lambda x: x.t)
    return m


def remove_marker(tl: Timeline, t: float, tol: float = 1e-3) -> Marker:
    for m in tl.markers:
        if abs(m.t - t) <= tol:
            tl.markers.remove(m)
            return m
    raise EditError("işaretçi yok")


def youtube_chapters(tl: Timeline) -> str:
    """YouTube bölüm listesi metni: ilk bölüm 0:00'da olmalı, en az 3 bölüm, her biri ≥10 sn (YouTube kuralları)."""
    ch = tl.chapters()
    if ch and ch[0].t > 0.05:
        ch = [Marker(0.0, "Giriş", True)] + ch

    def stamp(t: float) -> str:
        s = int(t)
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
    return "\n".join(f"{stamp(m.t)} {m.label or 'Bölüm'}" for m in ch)


def chapter_problems(tl: Timeline) -> List[str]:
    """YouTube'un bölüm şartlarını sağlamayan durumlar (boşsa uygun): ``count`` / ``first`` / ``short``."""
    ch = tl.chapters()
    out: List[str] = []
    if 0 < len(ch) < 3:
        out.append("count")
    if ch and ch[0].t > 0.05:
        out.append("first")
    if any(b.t - a.t < 10 for a, b in zip(ch, ch[1:])):
        out.append("short")
    return out


def freeze_frame(tl: Timeline, clip_id: str, t: float, duration: float = 2.0) -> Clip:
    """``t`` anındaki kareyi ``duration`` sn dondurur: klip bölünür, araya donuk kare klibi girer, sağ yarı kayar."""
    tr, c = _get(tl, clip_id)
    t = _fr(tl, t)
    if t <= c.start + MIN_CLIP:
        src_t, left, right = c.source_time(0.0), None, c
    elif t >= c.end - MIN_CLIP:
        src_t, left, right = c.source_time(c.duration), c, None
    else:
        left, right = split_clip(tl, clip_id, t)
        src_t = left.out
    at = t
    fz = Clip(new_id(), "freeze", c.src, c.media_id, src_t, src_t, at, duration_hint=duration, name="Dondur")
    if right is not None:
        for o in tr.clips:
            if o.start >= at - 1e-9 and o is not fz:
                o.start = _fr(tl, o.start + duration)
    tr.clips.append(fz)
    return fz
