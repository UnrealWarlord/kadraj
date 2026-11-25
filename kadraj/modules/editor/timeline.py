"""Zaman çizelgesi modeli (saf Python; Qt yok) — ``proje.kadraj`` içindeki ``timeline`` bölümünün tipli karşılığı.

Biçim (plan §3 + eklemeler; bilinmeyen alanlar **korunur**)::

    timeline = {"tracks": [{"id": "V1", "kind": "video|audio|fx|sub|adj", "name", "muted", "locked", "solo",
                            "volume_db", "pan", "effects": [...], "clips": [
        {"id", "kind": "media|image|adjust|text|freeze", "src", "media_id", "in", "out", "start",
         "speed", "speed_curve": [{"t","v","ease"}], "keyframes": {"scale": [{"t","v","ease","bezier"}], ...},
         "effects": [{"type", "params", "enabled"}], "volume_db", "fade_in", "fade_out", "name", "enabled", ...}]}],
     "fps": 30.0, "width": 1920, "height": 1080, "eq": {...}, "mixer": {...}}

Zaman **saniye** (float); ``HH:MM:SS:FF`` yalnızca gösterim/giriştir (``format_timecode`` / ``parse_timecode``). Bir klibin
``in``/``out`` değerleri **kaynak** zamanı, ``start`` zaman çizelgesi zamanıdır; süre ``(out-in)/hız``'tır (hız eğrisi varsa integralle).
Basit Mod 3 kanalla sınırlıdır (``EditorService``); modelin kendisi **sınırsız kanal** destekler.
"""
from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

TRACK_KINDS = ("video", "audio", "fx", "sub", "adj")
CLIP_KINDS = ("media", "image", "adjust", "text", "freeze")
CHANNEL_MODES = ("stereo", "mono", "left", "right", "swap")
TRACK_PREFIX = {"video": "V", "audio": "A", "fx": "FX", "sub": "S", "adj": "ADJ"}
MIN_SPEED, MAX_SPEED = 0.1, 16.0
NTSC = {23.976: 24000 / 1001, 29.97: 30000 / 1001, 59.94: 60000 / 1001}


# ---------------------------------------------------------------------------
# Zaman kodu
# ---------------------------------------------------------------------------
def fps_value(fps: float) -> float:
    """23.976 / 29.97 / 59.94 gibi kısa yazımları gerçek kesirli değere çevirir."""
    f = float(fps)
    for short, real in NTSC.items():
        if abs(f - short) < 0.01:
            return real
    return f if f > 0 else 30.0


def nominal_fps(fps: float) -> int:
    """Zaman kodunda kare sayacının sınırı (23.976 → 24)."""
    return max(1, int(round(fps_value(fps))))


def snap_to_frame(t: float, fps: float) -> float:
    f = fps_value(fps)
    return round(max(0.0, t) * f) / f


def to_frames(t: float, fps: float) -> int:
    return int(round(t * fps_value(fps)))


def from_frames(frames: int, fps: float) -> float:
    return frames / fps_value(fps)


def format_timecode(seconds: float, fps: float = 30.0) -> str:
    """``HH:MM:SS:FF`` (non-drop-frame; kare sayacı nominal fps'e göre)."""
    n = nominal_fps(fps)
    total = max(0, int(round(max(0.0, seconds) * fps_value(fps))))
    sec, ff = divmod(total, n)
    return f"{sec // 3600:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}:{ff:02d}"


_TC = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+)(?::(\d+))?\s*$")


def parse_timecode(text: str, fps: float = 30.0) -> float:
    """``HH:MM:SS:FF`` | ``MM:SS:FF`` | ``SS`` | ``SS.mmm`` → saniye. Geçersizse ``ValueError``."""
    t = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return float(t)
    parts = t.split(":")
    if not 2 <= len(parts) <= 4 or not all(p.isdigit() for p in parts):
        raise ValueError(f"geçersiz zaman kodu: {text!r}")
    nums = [int(p) for p in parts]
    if len(nums) == 4:
        h, m, s, f = nums
    elif len(nums) == 3:
        h, (m, s, f) = 0, nums
    else:
        h, m, (s, f) = 0, 0, nums
    if m > 59 or s > 59 or f >= nominal_fps(fps):
        raise ValueError(f"zaman kodu aralık dışı: {text!r}")
    return h * 3600 + m * 60 + s + f / fps_value(fps)


def format_clock(seconds: float) -> str:
    """Basit Mod: ``4 dk 32 sn`` / ``0:48`` biçimleri için ``M:SS``."""
    s = int(round(max(0.0, seconds)))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Keyframe / Bezier
# ---------------------------------------------------------------------------
EASE_PRESETS: Dict[str, Tuple[float, float, float, float]] = {
    "ease": (0.25, 0.1, 0.25, 1.0), "ease_in": (0.42, 0.0, 1.0, 1.0), "ease_out": (0.0, 0.0, 0.58, 1.0),
    "ease_in_out": (0.42, 0.0, 0.58, 1.0),
}


def cubic_bezier(x1: float, y1: float, x2: float, y2: float, u: float) -> float:
    """CSS ``cubic-bezier`` eğrisi: girdi ``u`` (0..1, zaman oranı) → çıktı (ilerleme). x için t'yi Newton + ikiye bölmeyle çözer."""
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    x1, x2 = min(1.0, max(0.0, x1)), min(1.0, max(0.0, x2))

    def bx(t: float) -> float:
        return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3

    def dbx(t: float) -> float:
        return 3 * (1 - t) ** 2 * x1 + 6 * (1 - t) * t * (x2 - x1) + 3 * t * t * (1 - x2)

    t = u
    for _ in range(8):
        err = bx(t) - u
        if abs(err) < 1e-7:
            break
        d = dbx(t)
        if abs(d) < 1e-6:
            break
        t -= err / d
    if not 0.0 <= t <= 1.0 or abs(bx(t) - u) > 1e-5:
        lo, hi = 0.0, 1.0
        for _ in range(40):
            t = (lo + hi) / 2
            if bx(t) < u:
                lo = t
            else:
                hi = t
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3


def ease_value(ease: str, bezier: Optional[Sequence[float]], u: float) -> float:
    if ease == "hold":
        return 0.0
    if ease == "bezier" and bezier and len(bezier) == 4:
        return cubic_bezier(*[float(v) for v in bezier], u)
    if ease in EASE_PRESETS:
        return cubic_bezier(*EASE_PRESETS[ease], u)
    return min(1.0, max(0.0, u))                                   # linear


@dataclass
class Keyframe:
    t: float                              # klibe göre yerel zaman (sn)
    v: float
    ease: str = "linear"                  # linear | hold | ease | ease_in | ease_out | ease_in_out | bezier
    bezier: Optional[Tuple[float, float, float, float]] = None   # ease == "bezier" iken (x1, y1, x2, y2)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"t": self.t, "v": self.v, "ease": self.ease}
        if self.bezier is not None:
            d["bezier"] = list(self.bezier)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Keyframe":
        bz = d.get("bezier")
        return cls(float(d.get("t", 0.0)), float(d.get("v", 0.0)), str(d.get("ease", "linear")),
                   tuple(float(x) for x in bz) if isinstance(bz, (list, tuple)) and len(bz) == 4 else None)  # type: ignore[arg-type]


def interpolate(kfs: Sequence[Keyframe], t: float, default: Optional[float] = None) -> Optional[float]:
    """``t`` anındaki değer. Her anahtar karenin ``ease``'i **kendinden sonraki** aralığı belirler."""
    if not kfs:
        return default
    ks = sorted(kfs, key=lambda k: k.t)
    if t <= ks[0].t:
        return ks[0].v
    if t >= ks[-1].t:
        return ks[-1].v
    for a, b in zip(ks, ks[1:]):
        if a.t <= t <= b.t:
            span = b.t - a.t
            if span <= 0:
                return b.v
            e = ease_value(a.ease, a.bezier, (t - a.t) / span)
            return a.v + (b.v - a.v) * e
    return ks[-1].v


def sample_keyframes(kfs: Sequence[Keyframe], per_segment: int = 8) -> List[Tuple[float, float]]:
    """FFmpeg ifadelerine dökmek için ``(t, v)`` örnekleri (her aralığı ``per_segment`` doğrusal parçaya böler)."""
    ks = sorted(kfs, key=lambda k: k.t)
    if not ks:
        return []
    pts: List[Tuple[float, float]] = [(ks[0].t, ks[0].v)]
    for a, b in zip(ks, ks[1:]):
        if a.ease == "hold":
            pts.append((b.t - 1e-3, a.v))
            pts.append((b.t, b.v))
            continue
        if a.ease == "linear":
            pts.append((b.t, b.v))
            continue
        for i in range(1, per_segment + 1):
            tt = a.t + (b.t - a.t) * i / per_segment
            pts.append((tt, float(interpolate(ks, tt))))      # type: ignore[arg-type]
    return pts


# ---------------------------------------------------------------------------
# Klip
# ---------------------------------------------------------------------------
def new_id(prefix: str = "c") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@dataclass
class Clip:
    id: str
    kind: str = "media"
    src: str = ""
    media_id: Optional[int] = None
    in_: float = 0.0
    out: float = 0.0
    start: float = 0.0
    speed: float = 1.0
    speed_curve: List[Keyframe] = field(default_factory=list)
    keyframes: Dict[str, List[Keyframe]] = field(default_factory=dict)
    effects: List[Dict[str, Any]] = field(default_factory=list)
    volume_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    name: str = ""
    enabled: bool = True
    duration_hint: float = 0.0            # kaynaksız klipler (adjust/text) için süre
    data: Dict[str, Any] = field(default_factory=dict)       # text/freeze/image parametreleri
    extra: Dict[str, Any] = field(default_factory=dict)      # tanınmayan alanlar (korunur)

    # -- süre ---------------------------------------------------------------------------------
    @property
    def duration(self) -> float:
        if self.kind in ("adjust", "text") or (self.kind in ("image", "freeze") and self.out <= self.in_):
            return max(0.0, self.duration_hint)
        if self.kind == "freeze":
            return max(0.0, self.duration_hint)
        span = max(0.0, self.out - self.in_)
        if not self.speed_curve:
            return span / max(MIN_SPEED, min(MAX_SPEED, self.speed))
        return self._curve_duration(span)

    @property
    def end(self) -> float:
        return self.start + self.duration

    def _curve_duration(self, span: float, step: float = 1 / 60) -> float:
        consumed, t = 0.0, 0.0
        for _ in range(int(3600 / step)):
            sp = max(MIN_SPEED, min(MAX_SPEED, float(interpolate(self.speed_curve, t, self.speed) or self.speed)))
            if consumed + sp * step >= span:
                return t + (span - consumed) / sp
            consumed += sp * step
            t += step
        return t

    def speed_at(self, local_t: float) -> float:
        v = interpolate(self.speed_curve, local_t, self.speed) if self.speed_curve else self.speed
        return max(MIN_SPEED, min(MAX_SPEED, float(v if v is not None else 1.0)))

    def source_time(self, local_t: float, step: float = 1 / 120) -> float:
        """Klibin ``local_t``. saniyesinde kaynak dosyadaki zaman (hız eğrisi integrali)."""
        local_t = max(0.0, local_t)
        if not self.speed_curve:
            return self.in_ + local_t * max(MIN_SPEED, min(MAX_SPEED, self.speed))
        acc, t = 0.0, 0.0
        while t + step < local_t:
            acc += self.speed_at(t + step / 2) * step
            t += step
        acc += self.speed_at((t + local_t) / 2) * (local_t - t)
        return self.in_ + acc

    def prop(self, name: str, local_t: float, default: float) -> float:
        kfs = self.keyframes.get(name)
        v = interpolate(kfs, local_t, None) if kfs else None
        return default if v is None else float(v)

    # -- (de)serileştirme ---------------------------------------------------------------------------
    _KNOWN = {"id", "kind", "src", "media_id", "in", "out", "start", "speed", "speed_curve", "keyframes", "effects",
              "volume_db", "fade_in", "fade_out", "name", "enabled", "duration", "data"}

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = copy.deepcopy(self.extra)
        d.update({"id": self.id, "kind": self.kind, "src": self.src, "media_id": self.media_id, "in": self.in_,
                  "out": self.out, "start": self.start, "speed": self.speed,
                  "speed_curve": [k.to_dict() for k in self.speed_curve],
                  "keyframes": {p: [k.to_dict() for k in ks] for p, ks in self.keyframes.items() if ks},
                  "effects": copy.deepcopy(self.effects), "volume_db": self.volume_db, "fade_in": self.fade_in,
                  "fade_out": self.fade_out, "name": self.name, "enabled": self.enabled})
        if self.duration_hint:
            d["duration"] = self.duration_hint
        if self.data:
            d["data"] = copy.deepcopy(self.data)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Clip":
        def f(key: str, default: float = 0.0) -> float:
            try:
                return float(d.get(key, default))
            except (TypeError, ValueError):
                return default
        kf = {str(p): [Keyframe.from_dict(k) for k in ks if isinstance(k, dict)]
              for p, ks in (d.get("keyframes") or {}).items() if isinstance(ks, list)}
        kind = d.get("kind") if d.get("kind") in CLIP_KINDS else "media"
        return cls(str(d.get("id") or new_id()), kind, str(d.get("src") or ""),
                   d.get("media_id") if isinstance(d.get("media_id"), int) else None, f("in"), f("out"), f("start"),
                   f("speed", 1.0) or 1.0,
                   [Keyframe.from_dict(k) for k in d.get("speed_curve", []) if isinstance(k, dict)], kf,
                   [e for e in d.get("effects", []) if isinstance(e, dict)], f("volume_db"), f("fade_in"), f("fade_out"),
                   str(d.get("name") or ""), bool(d.get("enabled", True)), f("duration"),
                   dict(d.get("data") or {}) if isinstance(d.get("data"), dict) else {},
                   {k: v for k, v in d.items() if k not in cls._KNOWN})

    def copy(self, new_id_: Optional[str] = None) -> "Clip":
        c = Clip.from_dict(self.to_dict())
        c.id = new_id_ or new_id()
        return c


# ---------------------------------------------------------------------------
# Kanal / işaretçi / zaman çizelgesi
# ---------------------------------------------------------------------------
@dataclass
class Marker:
    t: float
    label: str = ""
    chapter: bool = False                 # YouTube bölümü (kapak metnine ve dosya meta verisine girer)

    def to_dict(self) -> Dict[str, Any]:
        return {"t": self.t, "label": self.label, "chapter": self.chapter}


@dataclass
class Track:
    id: str
    kind: str = "video"
    name: str = ""
    clips: List[Clip] = field(default_factory=list)
    muted: bool = False
    locked: bool = False
    solo: bool = False
    hidden: bool = False
    volume_db: float = 0.0
    pan: float = 0.0                      # -1 sol … +1 sağ
    effects: List[Dict[str, Any]] = field(default_factory=list)      # kanal efektleri (örn. ducking)
    channels: str = "stereo"              # kanal karıştırıcı: stereo | mono | left | right | swap
    extra: Dict[str, Any] = field(default_factory=dict)

    _KNOWN = {"id", "kind", "name", "clips", "muted", "locked", "solo", "hidden", "volume_db", "pan", "effects", "channels"}

    @property
    def is_audio(self) -> bool:
        return self.kind == "audio"

    @property
    def duration(self) -> float:
        return max((c.end for c in self.clips), default=0.0)

    def sorted_clips(self) -> List[Clip]:
        return sorted(self.clips, key=lambda c: (c.start, c.id))

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = copy.deepcopy(self.extra)
        d.update({"id": self.id, "kind": self.kind, "name": self.name, "muted": self.muted, "locked": self.locked,
                  "solo": self.solo, "hidden": self.hidden, "volume_db": self.volume_db, "pan": self.pan,
                  "effects": copy.deepcopy(self.effects), "channels": self.channels,
                  "clips": [c.to_dict() for c in self.sorted_clips()]})
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any], index: int = 0) -> "Track":
        kind = d.get("kind") if d.get("kind") in TRACK_KINDS else "video"
        return cls(str(d.get("id") or f"{TRACK_PREFIX[kind]}{index + 1}"), kind, str(d.get("name") or ""),
                   [Clip.from_dict(c) for c in d.get("clips", []) if isinstance(c, dict)], bool(d.get("muted", False)),
                   bool(d.get("locked", False)), bool(d.get("solo", False)), bool(d.get("hidden", False)),
                   float(d.get("volume_db", 0.0) or 0.0), float(d.get("pan", 0.0) or 0.0),
                   [e for e in d.get("effects", []) if isinstance(e, dict)],
                   str(d.get("channels")) if d.get("channels") in CHANNEL_MODES else "stereo",
                   {k: v for k, v in d.items() if k not in cls._KNOWN})


EQ_BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)       # 10 bant EQ merkez frekansları (Hz)


@dataclass
class Timeline:
    tracks: List[Track] = field(default_factory=list)
    markers: List[Marker] = field(default_factory=list)
    fps: float = 30.0
    width: int = 1920
    height: int = 1080
    eq: List[float] = field(default_factory=lambda: [0.0] * len(EQ_BANDS))      # ana çıkış 10 bant EQ (dB)
    master_db: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    # -- sorgular -------------------------------------------------------------------------------------
    @property
    def duration(self) -> float:
        return max((t.duration for t in self.tracks), default=0.0)

    def all_clips(self) -> Iterator[Tuple[Track, Clip]]:
        for tr in self.tracks:
            for c in tr.clips:
                yield tr, c

    def find_clip(self, clip_id: str) -> Optional[Tuple[Track, Clip]]:
        for tr, c in self.all_clips():
            if c.id == clip_id:
                return tr, c
        return None

    def track(self, track_id: str) -> Optional[Track]:
        return next((t for t in self.tracks if t.id == track_id), None)

    def tracks_of(self, kind: str) -> List[Track]:
        return [t for t in self.tracks if t.kind == kind]

    def clips_at(self, t: float, kinds: Iterable[str] = ("video", "audio", "adj", "sub", "fx")) -> List[Tuple[Track, Clip]]:
        ks = set(kinds)
        return [(tr, c) for tr, c in self.all_clips() if tr.kind in ks and c.start <= t < c.end]

    def unique_track_id(self, kind: str) -> str:
        prefix = TRACK_PREFIX[kind]
        n = 1
        ids = {t.id for t in self.tracks}
        while f"{prefix}{n}" in ids:
            n += 1
        return f"{prefix}{n}"

    def chapters(self) -> List[Marker]:
        return sorted((m for m in self.markers if m.chapter), key=lambda m: m.t)

    # -- (de)serileştirme --------------------------------------------------------------------------------
    _KNOWN = {"tracks", "fps", "width", "height", "eq", "master_db"}

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = copy.deepcopy(self.extra)
        d.update({"tracks": [t.to_dict() for t in self.tracks], "fps": self.fps, "width": self.width,
                  "height": self.height, "eq": list(self.eq), "master_db": self.master_db})
        return d

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]], markers: Optional[List[Dict[str, Any]]] = None,
                  settings: Optional[Dict[str, Any]] = None) -> "Timeline":
        d = d if isinstance(d, dict) else {}
        settings = settings or {}
        w, h = _parse_resolution(settings.get("resolution"), d)
        try:
            fps = float(d.get("fps") or settings.get("fps") or 30.0)
        except (TypeError, ValueError):
            fps = 30.0
        eq = d.get("eq")
        eq_list = [float(x) for x in eq][:len(EQ_BANDS)] if isinstance(eq, list) else []
        eq_list += [0.0] * (len(EQ_BANDS) - len(eq_list))
        tl = cls([Track.from_dict(t, i) for i, t in enumerate(d.get("tracks", [])) if isinstance(t, dict)],
                 [Marker(float(m.get("t", 0.0)), str(m.get("label", "")), bool(m.get("chapter", False)))
                  for m in (markers or []) if isinstance(m, dict)], fps, w, h, eq_list,
                 float(d.get("master_db", 0.0) or 0.0), {k: v for k, v in d.items() if k not in cls._KNOWN})
        return tl

    def snapshot(self) -> Dict[str, Any]:
        return {"timeline": self.to_dict(), "markers": [m.to_dict() for m in self.markers]}

    def restore(self, snap: Dict[str, Any]) -> None:
        other = Timeline.from_dict(snap["timeline"], snap.get("markers"))
        self.tracks, self.markers, self.fps, self.width, self.height = other.tracks, other.markers, other.fps, other.width, other.height
        self.eq, self.master_db, self.extra = other.eq, other.master_db, other.extra


def _parse_resolution(value: Any, d: Dict[str, Any]) -> Tuple[int, int]:
    if isinstance(d.get("width"), (int, float)) and isinstance(d.get("height"), (int, float)):
        return int(d["width"]), int(d["height"])
    m = re.fullmatch(r"\s*(\d+)\s*[x×]\s*(\d+)\s*", str(value or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (1920, 1080)


def default_timeline(fps: float = 30.0, width: int = 1920, height: int = 1080, simple: bool = True) -> Timeline:
    """Yeni proje için başlangıç: Basit'te Video + Müzik + Seslendirme (tuvaldeki 3 kanal)."""
    tl = Timeline(fps=fps, width=width, height=height)
    tl.tracks = [Track("V1", "video", "Video"), Track("A1", "audio", "Müzik"), Track("A2", "audio", "Seslendirme")]
    return tl
