"""Renk hattı (numpy): önizleme/ölçüm/renk eşleştirme için. FFmpeg ``eq/colorbalance/lut3d`` ile **aynı yönde** çalışır
(birebir değil); son işleme yine FFmpeg'dir. ``.cube`` LUT okur/yazar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)       # BT.709


def to_float(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img)
    return a.astype(np.float32) / 255.0 if a.dtype == np.uint8 else a.astype(np.float32)


def to_u8(img: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(img * 255.0), 0, 255).astype(np.uint8)


def luma(img: np.ndarray) -> np.ndarray:
    return img[..., :3] @ LUMA


# -- LUT ----------------------------------------------------------------------------------------
def identity_lut(n: int = 17) -> np.ndarray:
    g = np.linspace(0.0, 1.0, n, dtype=np.float32)
    r, gg, b = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r, gg, b], axis=-1)                       # [r][g][b] → rgb


def write_cube(path: str, lut: np.ndarray, title: str = "match") -> str:
    n = lut.shape[0]
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    # .cube sırası: kırmızı en hızlı değişir → döngü b, g, r
    for b in range(n):
        for g in range(n):
            for r in range(n):
                v = np.clip(lut[r, g, b], 0, 1)
                lines.append(f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_cube(path: str) -> np.ndarray:
    n = 0
    vals: List[Tuple[float, float, float]] = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("TITLE") or line.startswith("DOMAIN"):
            continue
        if line.startswith("LUT_3D_SIZE"):
            n = int(line.split()[1])
            continue
        parts = line.split()
        if len(parts) == 3:
            try:
                vals.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                continue
    if n < 2 or len(vals) != n ** 3:
        raise ValueError("geçersiz .cube dosyası")
    arr = np.array(vals, dtype=np.float32).reshape(n, n, n, 3)       # [b][g][r]
    return np.transpose(arr, (2, 1, 0, 3))                            # → [r][g][b]


def apply_lut(img: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Üç doğrusal enterpolasyonlu 3B LUT uygulaması."""
    n = lut.shape[0]
    x = np.clip(img[..., :3], 0, 1) * (n - 1)
    i0 = np.clip(np.floor(x).astype(np.int32), 0, n - 2)
    f = x - i0
    out = np.zeros_like(x)
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = (f[..., 0] if dr else 1 - f[..., 0]) * (f[..., 1] if dg else 1 - f[..., 1]) * (f[..., 2] if db else 1 - f[..., 2])
                out += w[..., None] * lut[i0[..., 0] + dr, i0[..., 1] + dg, i0[..., 2] + db]
    return out


# -- hat ----------------------------------------------------------------------------------------
@dataclass
class ColorPipeline:
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    lift: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    gain: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    lut: Optional[np.ndarray] = field(default=None, repr=False)

    @classmethod
    def from_effects(cls, effects: List[Dict]) -> "ColorPipeline":
        p = cls()
        for e in effects:
            if not e.get("enabled", True):
                continue
            q = e.get("params") or {}
            if e.get("type") == "eq":
                p.brightness += float(q.get("brightness", 0))
                p.contrast *= float(q.get("contrast", 1))
                p.saturation *= float(q.get("saturation", 1))
                p.gamma *= float(q.get("gamma", 1))
            elif e.get("type") == "grayscale":
                p.saturation = 0.0
            elif e.get("type") == "lut" and q.get("file"):
                try:
                    p.lut = read_cube(str(q["file"]))
                except (OSError, ValueError):
                    pass
        return p

    @property
    def is_identity(self) -> bool:
        return (self.brightness == 0 and self.contrast == 1 and self.saturation == 1 and self.gamma == 1 and self.lut is None
                and tuple(self.lift) == (0, 0, 0) and tuple(self.gain) == (1, 1, 1))

    def apply(self, img: np.ndarray) -> np.ndarray:
        """``img`` uint8 ya da [0,1] float RGB → aynı türde sonuç."""
        was_u8 = np.asarray(img).dtype == np.uint8
        x = to_float(img)[..., :3]
        if not self.is_identity:
            x = (x - 0.5) * self.contrast + 0.5 + self.brightness
            x = np.clip(x, 0, 1) ** (1.0 / max(0.1, self.gamma))
            y = luma(x)[..., None]
            x = y + (x - y) * self.saturation
            x = x * np.asarray(self.gain, dtype=np.float32) + np.asarray(self.lift, dtype=np.float32)
            x = np.clip(x, 0, 1)
            if self.lut is not None:
                x = apply_lut(x, self.lut)
        return to_u8(x) if was_u8 else np.clip(x, 0, 1)


# -- renk eşleştirme / canlandırma ---------------------------------------------------------------------
def channel_stats(img: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    x = to_float(img)[..., :3].reshape(-1, 3)
    return x.mean(axis=0), x.std(axis=0) + 1e-4


def match_gain_offset(src: np.ndarray, ref: np.ndarray, strength: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
    """Kanal başına ``y = g·x + o``: ``src``'nin ortalama/yayılımını ``ref``'e yaklaştırır (``strength`` 0–1)."""
    ms, ss = channel_stats(src)
    mr, sr = channel_stats(ref)
    gain = np.clip(sr / ss, 0.5, 2.0)
    off = mr - gain * ms
    gain = 1 + (gain - 1) * strength
    off = off * strength
    return gain.astype(np.float32), off.astype(np.float32)


def match_lut(src: np.ndarray, ref: np.ndarray, size: int = 17, strength: float = 1.0) -> np.ndarray:
    gain, off = match_gain_offset(src, ref, strength)
    return np.clip(identity_lut(size) * gain + off, 0, 1).astype(np.float32)


def vitalize_params(img: np.ndarray) -> Dict[str, float]:
    """Kareyi ölçüp tek tuşluk "renkleri canlandır" ayarını önerir: soluk kareye daha çok doygunluk, düz kareye daha çok kontrast."""
    x = to_float(img)[..., :3]
    y = luma(x)
    mx, mn = x.max(axis=-1), x.min(axis=-1)
    sat = float(np.mean((mx - mn) / (mx + 1e-4)))
    spread = float(np.percentile(y, 95) - np.percentile(y, 5))
    saturation = float(np.clip(1.0 + (0.45 - sat) * 1.2, 1.05, 1.5))
    contrast = float(np.clip(1.0 + (0.7 - spread) * 0.4, 1.0, 1.25))
    brightness = float(np.clip((0.45 - float(y.mean())) * 0.25, -0.08, 0.08))
    return {"saturation": round(saturation, 3), "contrast": round(contrast, 3), "brightness": round(brightness, 3)}
