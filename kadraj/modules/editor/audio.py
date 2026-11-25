"""Ses araçları: 10 bant EQ eğrisi (RBJ tepe filtresi büyüklük yanıtı), hazır ayarlar, kanal karıştırıcı modeli, klip seviyesi ölçümü."""
from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .probe import find_binary
from .timeline import CHANNEL_MODES, EQ_BANDS, Timeline

EQ_PRESETS: Dict[str, List[float]] = {
    "flat": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "voice": [-6, -4, -2, 0, 1, 3, 4, 2, 0, -2],
    "bass": [6, 5, 3, 1, 0, 0, 0, 0, 0, 0],
    "bright": [0, 0, 0, 0, 0, 1, 2, 4, 5, 4],
    "warm": [2, 2, 1, 1, 0, 0, -1, -2, -2, -3],
    "podcast": [-8, -5, 0, 1, 2, 3, 3, 1, -1, -3],
}
EQ_RANGE_DB = 18.0


def peaking_response_db(freqs: Sequence[float], f0: float, gain_db: float, sr: float = 48000.0, bandwidth_oct: float = 1.0) -> np.ndarray:
    """RBJ 'peaking EQ' süzgecinin ``freqs`` noktalarındaki dB yanıtı (FFmpeg ``equalizer t=o w=1`` ile uyumlu)."""
    f = np.asarray(freqs, dtype=np.float64)
    if abs(gain_db) < 1e-9:
        return np.zeros_like(f)
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / sr
    alpha = math.sin(w0) * math.sinh(math.log(2) / 2 * bandwidth_oct * w0 / math.sin(w0))
    b = np.array([1 + alpha * A, -2 * math.cos(w0), 1 - alpha * A])
    a = np.array([1 + alpha / A, -2 * math.cos(w0), 1 - alpha / A])
    z = np.exp(-1j * 2 * math.pi * f / sr)
    h = (b[0] + b[1] * z + b[2] * z ** 2) / (a[0] + a[1] * z + a[2] * z ** 2)
    return 20 * np.log10(np.abs(h) + 1e-12)


def eq_curve_db(gains: Sequence[float], points: int = 128, sr: float = 48000.0) -> Tuple[np.ndarray, np.ndarray]:
    """``(frekanslar, dB)``: 20 Hz–20 kHz logaritmik eksen, 10 bandın toplam yanıtı."""
    freqs = np.logspace(math.log10(20), math.log10(20000), points)
    total = np.zeros(points)
    for f0, g in zip(EQ_BANDS, gains):
        total += peaking_response_db(freqs, f0, max(-EQ_RANGE_DB, min(EQ_RANGE_DB, g)), sr)
    return freqs, total


def clamp_eq(gains: Sequence[float]) -> List[float]:
    g = [max(-EQ_RANGE_DB, min(EQ_RANGE_DB, float(x))) for x in list(gains)[:len(EQ_BANDS)]]
    return g + [0.0] * (len(EQ_BANDS) - len(g))


@dataclass(frozen=True)
class Strip:
    """Mikser kanal şeridi (tuvaldeki mikser): ``Track``'ın ses alanlarının görünümü."""
    track_id: str
    name: str
    volume_db: float
    pan: float
    muted: bool
    solo: bool
    channels: str
    ducked: bool


def strips(tl: Timeline) -> List[Strip]:
    return [Strip(t.id, t.name or t.id, t.volume_db, t.pan, t.muted, t.solo, t.channels,
                  any(e.get("type") == "duck" and e.get("enabled", True) for e in t.effects))
            for t in tl.tracks if t.kind in ("audio", "video")]


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20)


def gain_to_db(g: float) -> float:
    return 20 * math.log10(max(g, 1e-9))


def decode_mono(path: str, sr: int = 16000, start: float = 0.0, duration: Optional[float] = None) -> np.ndarray:
    """Ses (ya da videonun sesini) FFmpeg ile ``sr`` Hz mono float32 diziye çözer. Ses yoksa boş dizi."""
    ff = find_binary("ffmpeg")
    if not ff:
        return np.zeros(0, dtype=np.float32)
    cmd = [ff, "-v", "error", "-nostdin"] + (["-ss", f"{start:.3f}"] if start else []) + (["-t", f"{duration:.3f}"] if duration else []) + \
        ["-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    return np.frombuffer(r.stdout, dtype=np.float32).copy() if r.returncode == 0 else np.zeros(0, dtype=np.float32)


@lru_cache(maxsize=64)
def _levels_cached(path: str, mtime: float) -> Tuple[float, float]:
    x = decode_mono(path, 16000)
    if x.size == 0:
        return -120.0, -120.0
    return gain_to_db(float(np.abs(x).max())), gain_to_db(float(np.sqrt(np.mean(x ** 2))))


def clip_levels(path: str) -> Tuple[float, float]:
    """``(tepe dBFS, RMS dBFS)`` — mikser metrelerinde klip seviyesini göstermek için."""
    p = Path(path)
    return _levels_cached(str(p), p.stat().st_mtime) if p.exists() else (-120.0, -120.0)


__all__ = ["EQ_PRESETS", "EQ_RANGE_DB", "CHANNEL_MODES", "peaking_response_db", "eq_curve_db", "clamp_eq", "Strip", "strips",
           "db_to_gain", "gain_to_db", "decode_mono", "clip_levels"]
