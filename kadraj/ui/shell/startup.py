"""Açılış denetimleri — ağa **çıkmadan**, yalnızca yerel araç algılama (plan §0.2).

Splash altındaki "yt-dlp ✓ · FFmpeg ✓ · Ollama ✓" satırını ve "Stüdyo hazırlanıyor… FFmpeg kontrol ediliyor"
durum metnini besler. Hiçbir denetim istisna atmaz.
"""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple


@dataclass(frozen=True)
class ToolStatus:
    key: str
    label: str
    ok: bool


def _which(name: str) -> bool:
    try:
        return shutil.which(name) is not None
    except Exception:
        return False


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


#: (anahtar, etiket, denetim) — sıra splash'te gösterilen sıradır.
CHECKS: List[Tuple[str, str, Callable[[], bool]]] = [
    ("ffmpeg", "FFmpeg", lambda: _which("ffmpeg") and _which("ffprobe")),
    ("yt-dlp", "yt-dlp", lambda: _module("yt_dlp") or _which("yt-dlp")),
    ("ollama", "Ollama", lambda: _which("ollama")),
    ("portaudio", "PortAudio", lambda: _module("sounddevice")),
]


def detect_tools() -> Dict[str, ToolStatus]:
    out: Dict[str, ToolStatus] = {}
    for key, label, fn in CHECKS:
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        out[key] = ToolStatus(key, label, ok)
    return out


def summary_line(tools: Dict[str, ToolStatus], version: str) -> str:
    parts = [f"sürüm {version}"]
    for key in ("yt-dlp", "ffmpeg", "ollama"):
        t = tools.get(key)
        if t:
            parts.append(f"{t.label} {'✓' if t.ok else '–'}")
    return " · ".join(parts)
