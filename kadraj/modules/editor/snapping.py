"""Manyetik yapışma: klip kenarları, çalma başlığı, işaretçiler ve 0 noktasına.

``snap_time`` tek bir zamanı, ``snap_move`` taşınan klibin **başını ve sonunu** hedeflere yapıştırır. Eşik saniye cinsindendir
(arayüz piksel eşiğini ``threshold_px / px_per_second`` ile çevirir). Sonuçta hangi hedefe yapıştığı bildirilir ("Yapıştı!" balonu).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .timeline import Clip, Timeline


@dataclass(frozen=True)
class SnapResult:
    time: float
    target: Optional[str] = None          # None | "clip_start" | "clip_end" | "playhead" | "marker" | "zero"
    delta: float = 0.0                    # uygulanan kayma (sn)

    @property
    def snapped(self) -> bool:
        return self.target is not None


def targets(timeline: Timeline, *, exclude: Iterable[str] = (), playhead: Optional[float] = None,
            markers: bool = True, zero: bool = True) -> List[Tuple[float, str]]:
    skip = set(exclude)
    out: List[Tuple[float, str]] = []
    if zero:
        out.append((0.0, "zero"))
    if playhead is not None:
        out.append((playhead, "playhead"))
    if markers:
        out += [(m.t, "marker") for m in timeline.markers]
    for _tr, c in timeline.all_clips():
        if c.id in skip:
            continue
        out.append((c.start, "clip_start"))
        out.append((c.end, "clip_end"))
    return out


def snap_time(timeline: Timeline, t: float, threshold_s: float, *, exclude: Iterable[str] = (),
              playhead: Optional[float] = None, enabled: bool = True, markers: bool = True) -> SnapResult:
    if not enabled or threshold_s <= 0:
        return SnapResult(t)
    best: Optional[Tuple[float, str]] = None
    for tt, name in targets(timeline, exclude=exclude, playhead=playhead, markers=markers):
        if best is None or abs(tt - t) < abs(best[0] - t):
            best = (tt, name)
    if best is not None and abs(best[0] - t) <= threshold_s:
        return SnapResult(best[0], best[1], best[0] - t)
    return SnapResult(t)


def snap_move(timeline: Timeline, clip: Clip, new_start: float, threshold_s: float, *, playhead: Optional[float] = None,
              enabled: bool = True) -> SnapResult:
    """Klibi ``new_start``'a taşırken baş **ve** son kenarı hedeflere yapıştırır; daha yakın olan kazanır."""
    if not enabled or threshold_s <= 0:
        return SnapResult(new_start)
    a = snap_time(timeline, new_start, threshold_s, exclude=[clip.id], playhead=playhead)
    b = snap_time(timeline, new_start + clip.duration, threshold_s, exclude=[clip.id], playhead=playhead)
    cands = [(abs(r.delta), r, shift) for r, shift in ((a, 0.0), (b, -clip.duration)) if r.snapped]
    if not cands:
        return SnapResult(new_start)
    _d, r, shift = min(cands, key=lambda x: x[0])
    final = max(0.0, r.time + shift)
    return SnapResult(final, r.target, final - new_start)
