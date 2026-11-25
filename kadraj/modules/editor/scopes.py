"""Ölçüm ekranları (numpy): histogram, RGB parade, vektörskop. Girdi: ``(H, W, 3)`` uint8/float RGB; çıktı: sayım dizileri
(arayüz bunları tema renkleriyle boyar). Büyük kareler önce küçültülür (≤ 320 px yükseklik)."""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from .color import luma, to_float


def downscale(img: np.ndarray, max_h: int = 320) -> np.ndarray:
    a = np.asarray(img)
    if a.shape[0] <= max_h:
        return a
    step = int(np.ceil(a.shape[0] / max_h))
    return a[::step, ::step]


def histogram(img: np.ndarray, bins: int = 256) -> np.ndarray:
    """``(4, bins)``: R, G, B ve parlaklık sayımları."""
    x = to_float(downscale(img))[..., :3]
    out = np.zeros((4, bins), dtype=np.int64)
    for c in range(3):
        out[c] = np.bincount(np.clip((x[..., c] * (bins - 1)).round().astype(np.int64), 0, bins - 1).ravel(), minlength=bins)
    out[3] = np.bincount(np.clip((luma(x) * (bins - 1)).round().astype(np.int64), 0, bins - 1).ravel(), minlength=bins)
    return out


def rgb_parade(img: np.ndarray, width: int = 160, height: int = 128) -> np.ndarray:
    """``(3, height, width)`` sayım: her kanal için sütun-konumu × seviye dağılımı (üst = parlak)."""
    x = to_float(downscale(img))[..., :3]
    h, w, _ = x.shape
    cols = np.linspace(0, w - 1, width).astype(np.int64)
    out = np.zeros((3, height, width), dtype=np.int64)
    for c in range(3):
        lv = np.clip(((1 - x[:, cols, c]) * (height - 1)).round().astype(np.int64), 0, height - 1)      # (h, width)
        xs = np.broadcast_to(np.arange(width), lv.shape)
        out[c] = np.bincount((lv * width + xs).ravel(), minlength=height * width).reshape(height, width)
    return out


def vectorscope(img: np.ndarray, size: int = 192) -> np.ndarray:
    """``(size, size)`` sayım: BT.709 Cb/Cr düzlemi (merkez = renksiz)."""
    x = to_float(downscale(img))[..., :3].reshape(-1, 3)
    y = x @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    cb = (x[:, 2] - y) / 1.8556
    cr = (x[:, 0] - y) / 1.5748
    ix = np.clip(((cb + 0.5) * (size - 1)).round().astype(np.int64), 0, size - 1)
    iy = np.clip(((0.5 - cr) * (size - 1)).round().astype(np.int64), 0, size - 1)
    return np.bincount(iy * size + ix, minlength=size * size).reshape(size, size)


def stats(img: np.ndarray) -> Dict[str, float]:
    x = to_float(downscale(img))[..., :3]
    y = luma(x)
    mx, mn = x.max(axis=-1), x.min(axis=-1)
    return {"luma_mean": float(y.mean()), "luma_p5": float(np.percentile(y, 5)), "luma_p95": float(np.percentile(y, 95)),
            "clipped_high": float((y >= 0.985).mean()), "clipped_low": float((y <= 0.015).mean()),
            "saturation": float(np.mean((mx - mn) / (mx + 1e-4)))}


def to_density_rgba(counts: np.ndarray, color: Tuple[int, int, int], gain: float = 6.0) -> np.ndarray:
    """Sayım dizisini ``color`` tonunda, yoğunluğa göre saydam RGBA uint8 görüntüye çevirir (``QImage.Format_RGBA8888``)."""
    c = np.asarray(counts, dtype=np.float32)
    a = np.clip(np.log1p(c) / max(1.0, np.log1p(c.max())) * gain / 6.0 * 255.0, 0, 255).astype(np.uint8) if c.max() > 0 else np.zeros(c.shape, np.uint8)
    rgba = np.zeros(c.shape + (4,), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2], rgba[..., 3] = color[0], color[1], color[2], a
    return rgba
