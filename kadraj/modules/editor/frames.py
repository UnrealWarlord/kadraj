"""Tek kare üretimi (önizleme / önce-sonra / ölçüm ekranları): zaman çizelgesinin küçük bir penceresini FFmpeg ile işleyip ham RGB döner."""
from __future__ import annotations

import copy
import subprocess
from typing import Dict, Optional, Tuple

import numpy as np

from .errors import smart
from .filtergraph import InfoFn, build_graph, window
from .probe import require_ffmpeg
from .timeline import Timeline


def strip_look(tl: Timeline) -> Timeline:
    """"Önce" görüntüsü: efektler, keyframe'ler ve ayar katmanları kaldırılmış kopya (kesimler/yerleşim aynı kalır)."""
    out = Timeline.from_dict(tl.to_dict(), [m.to_dict() for m in tl.markers])
    for tr in out.tracks:
        if tr.kind in ("adj", "fx"):
            tr.clips = []
        for c in tr.clips:
            c.effects = []
            c.keyframes = {k: v for k, v in c.keyframes.items() if k == "volume"}
            c.fade_in = c.fade_out = 0.0
    out.eq = [0.0] * len(out.eq)
    return out


def extract_frame(tl: Timeline, t: float, *, size: Tuple[int, int] = (480, 270), info: Optional[InfoFn] = None,
                  proxies: Optional[Dict[str, str]] = None, before: bool = False) -> np.ndarray:
    """``t`` anındaki kareyi ``size`` boyutunda ``(H, W, 3)`` uint8 RGB olarak döner. ``before=True`` efektsiz sürümdür."""
    ff = require_ffmpeg()
    t = max(0.0, min(t, max(0.0, tl.duration - 0.001)))
    base = strip_look(tl) if before else tl
    win = window(base, t, min(base.duration, t + 0.5) if base.duration - t > 0.05 else base.duration)
    w, h = max(2, size[0] // 2 * 2), max(2, size[1] // 2 * 2)
    work = copy.copy(win)
    g = build_graph(work, include_audio=False, size=(w, h), info=info, proxies=proxies)
    try:
        cmd = [ff, "-v", "error", "-nostdin", *g.input_args(), "-filter_complex", g.filter_complex, "-map", g.video_label or "[vout]",
               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    finally:
        g.cleanup()
    need = w * h * 3
    if r.returncode != 0 or len(r.stdout) < need:
        raise smart("render_failed", technical=r.stderr.decode("utf-8", "replace")[-600:])
    return np.frombuffer(r.stdout[:need], dtype=np.uint8).reshape(h, w, 3).copy()
