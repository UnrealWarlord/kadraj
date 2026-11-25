"""FFmpeg / ffprobe bulma ve ortam bilgisi çıkarma. Ağ yok; yalnızca yerel ikili dosyalar."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import smart


def find_binary(name: str) -> Optional[str]:
    """``KADRAJ_FFMPEG`` / ``KADRAJ_FFPROBE`` ortam değişkeni önceliklidir; yoksa PATH."""
    env = os.environ.get(f"KADRAJ_{name.upper()}")
    if env and Path(env).exists():
        return env
    return shutil.which(name)


def require_ffmpeg() -> str:
    path = find_binary("ffmpeg")
    if not path:
        raise smart("ffmpeg_missing")
    return path


@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    vcodec: str = ""
    acodec: str = ""
    sample_rate: int = 0
    channels: int = 0
    has_video: bool = False
    has_audio: bool = False
    is_image: bool = False

    @property
    def kind(self) -> str:
        return "image" if self.is_image else "video" if self.has_video else "audio"


_IMAGE_CODECS = {"png", "mjpeg", "webp", "bmp", "gif", "tiff", "jpeg2000", "ppm"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _rate(text: str) -> float:
    try:
        a, _, b = (text or "0/1").partition("/")
        return float(a) / float(b or 1) if float(b or 1) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def parse_ffprobe(path: str, data: Dict[str, Any]) -> MediaInfo:
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    try:
        dur = float(fmt.get("duration") or (v or a or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    image = bool(v) and (Path(path).suffix.lower() in _IMAGE_EXT or (v.get("codec_name") in _IMAGE_CODECS and not a and dur <= 0.05))
    return MediaInfo(path, dur, int((v or {}).get("width") or 0), int((v or {}).get("height") or 0),
                     _rate((v or {}).get("avg_frame_rate") or (v or {}).get("r_frame_rate") or ""),
                     str((v or {}).get("codec_name") or ""), str((a or {}).get("codec_name") or ""),
                     int((a or {}).get("sample_rate") or 0), int((a or {}).get("channels") or 0),
                     v is not None, a is not None, image)


@lru_cache(maxsize=256)
def _probe_cached(path: str, mtime: float) -> MediaInfo:
    ffprobe = find_binary("ffprobe")
    if not ffprobe:
        raise smart("ffmpeg_missing")
    r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise smart("probe_failed", name=Path(path).name, technical=(r.stderr or "").strip()[-400:])
    return parse_ffprobe(path, json.loads(r.stdout or "{}"))


def probe(path: str) -> MediaInfo:
    p = Path(path)
    if not p.exists():
        raise smart("missing_media", name=p.name)
    return _probe_cached(str(p), p.stat().st_mtime)
