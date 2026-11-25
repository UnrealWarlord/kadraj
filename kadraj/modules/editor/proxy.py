"""Arka plan proxy (düşük çözünürlüklü kopya): ağır kaynaklar (>1080p, HEVC/ProRes) önizlemede akıcı olsun diye ``06_Proxies``'e
360p olarak çıkarılır. **Son işleme her zaman özgün dosyayla yapılır**; proxy yalnızca önizleme/ölçüm içindir.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .filtergraph import fps_arg
from .probe import MediaInfo, probe, require_ffmpeg
from .render import RenderCancelled, run_commands
from .timeline import Timeline

PROXY_HEIGHT = 360
HEAVY_CODECS = {"hevc", "prores", "av1", "vp9", "dnxhd", "mjpeg"}


def needs_proxy(info: MediaInfo) -> bool:
    if not info.has_video or info.is_image:
        return False
    return info.height > 1088 or info.width > 1920 or info.vcodec in HEAVY_CODECS


def proxy_path(proxy_dir: Path, src: str) -> Path:
    p = Path(src)
    try:
        st = p.stat()
        key = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        key = str(p)
    return Path(proxy_dir) / f"{p.stem[:40]}-{hashlib.sha1(key.encode()).hexdigest()[:8]}.proxy.mp4"


def build_command(src: str, dst: str, info: MediaInfo, *, height: int = PROXY_HEIGHT, ffmpeg: Optional[str] = None) -> List[str]:
    ff = ffmpeg or require_ffmpeg()
    cmd = [ff, "-hide_banner", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-nostats", "-i", src,
           "-vf", f"scale=-2:{height},fps={fps_arg(info.fps or 30)}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
           "-pix_fmt", "yuv420p", "-g", "15"]
    cmd += ["-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2"] if info.has_audio else ["-an"]
    return cmd + ["-f", "mp4", "-movflags", "+faststart", dst + ".part"]


def make_proxy(src: str, proxy_dir: Path, *, report: Optional[Callable[[float, str], None]] = None,
               cancelled: Optional[Callable[[], bool]] = None, force: bool = False) -> Optional[str]:
    """Proxy üretir (zaten varsa yeniden üretmez). Gerekmiyorsa ``None``. Yarım dosya iptal/hatada silinir."""
    info = probe(src)
    if not needs_proxy(info) and not force:
        return None
    Path(proxy_dir).mkdir(parents=True, exist_ok=True)
    dst = proxy_path(proxy_dir, src)
    if dst.exists() and dst.stat().st_size > 0:
        return str(dst)
    cmd = build_command(src, str(dst), info)
    try:
        run_commands([cmd], info.duration, report=report, cancelled=cancelled)
        os.replace(str(dst) + ".part", dst)
    except BaseException:
        try:
            os.unlink(str(dst) + ".part")
        except OSError:
            pass
        raise
    return str(dst)


def proxy_map(tl: Timeline, proxy_dir: Path) -> Dict[str, str]:
    """Zaman çizelgesindeki kaynaklar için **var olan** proxy'ler (``src → proxy``)."""
    out: Dict[str, str] = {}
    for _tr, c in tl.all_clips():
        if c.src and c.src not in out:
            p = proxy_path(proxy_dir, c.src)
            if p.exists():
                out[c.src] = str(p)
    return out


__all__ = ["RenderCancelled", "needs_proxy", "proxy_path", "build_command", "make_proxy", "proxy_map", "PROXY_HEIGHT"]
