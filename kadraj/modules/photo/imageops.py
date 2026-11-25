"""Yerel görsel işlemleri (Pillow + numpy; ağ yok): Lanczos büyütme, ton/renk aktarımı, YouTube kapağı, maske yardımcıları, kayıt."""
from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

COVER_SIZE = (1280, 720)
COVER_MAX_BYTES = 2 * 1024 * 1024            # YouTube küçük resim sınırı
FONT_PATHS = ("kadraj/ui/assets/fonts",)


def load(data: "bytes | str | Path") -> Image.Image:
    if isinstance(data, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(data))).convert("RGB")
    return Image.open(str(data)).convert("RGB")


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save_bytes(data: bytes, folder: Path, stem: str, ext: str = ".png") -> Path:
    """``folder/<stem><ext>``; varsa ``-2``, ``-3``… (asla üzerine yazmaz)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cand, n = folder / f"{stem}{ext}", 2
    while cand.exists():
        cand = folder / f"{stem}-{n}{ext}"
        n += 1
    cand.write_bytes(data)
    return cand


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def lanczos_upscale(data: "bytes | str | Path", factor: int = 2) -> bytes:
    """Yerel (ComfyUI yokken) ×2/×4 büyütme: Lanczos + hafif keskinleştirme."""
    img = load(data)
    factor = 4 if factor >= 4 else 2
    big = img.resize((img.width * factor, img.height * factor), Image.LANCZOS)
    big = big.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
    return to_png(big)


def color_transfer(content: "bytes | str | Path", reference: "bytes | str | Path", strength: float = 0.8) -> bytes:
    """Yerel stil aktarımı (yedek): ``reference``'ın kanal ortalama/yayılımını ``content``'e uygular (Reinhard benzeri)."""
    a = np.asarray(load(content), dtype=np.float32) / 255.0
    b = np.asarray(load(reference), dtype=np.float32) / 255.0
    ma, sa = a.reshape(-1, 3).mean(0), a.reshape(-1, 3).std(0) + 1e-4
    mb, sb = b.reshape(-1, 3).mean(0), b.reshape(-1, 3).std(0) + 1e-4
    out = np.clip((a - ma) / sa * sb + mb, 0, 1)
    out = a * (1 - strength) + out * strength
    return to_png(Image.fromarray((out * 255).astype(np.uint8)))


def cover_crop(img: Image.Image, size: Tuple[int, int] = COVER_SIZE) -> Image.Image:
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.45))


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    root = Path(__file__).resolve().parents[2] / "ui" / "assets" / "fonts"
    for cand in sorted(root.glob("*.ttf")) if root.exists() else []:
        if ("Bold" in cand.name) == bold and ("Archivo" in cand.name or "Inter" in cand.name):
            return ImageFont.truetype(str(cand), size)
    for cand in sorted(root.glob("*.ttf")) if root.exists() else []:
        return ImageFont.truetype(str(cand), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_w: int) -> list:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def make_cover(image: "bytes | str | Path", title: str, subtitle: str = "", *, size: Tuple[int, int] = COVER_SIZE) -> Tuple[bytes, str]:
    """1280×720 YouTube kapağı: görseli kırp, alttan karartma, kalın başlık (otomatik sığdırma + kontur). 2 MB'ı aşarsa JPEG.
    ``(bayt, uzantı)`` döner."""
    base = cover_crop(load(image), size).convert("RGBA")
    w, h = size
    grad = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(h):
        a = int(max(0, (y - h * 0.35) / (h * 0.65)) ** 1.5 * 200)
        gd.line([(0, y), (w, y)], fill=(0, 0, 0, a))
    base = Image.alpha_composite(base, grad)
    d = ImageDraw.Draw(base)
    title = (title or "").strip()
    if title:
        fs = int(h * 0.15)
        while fs > 28:
            font = _font(fs)
            lines = _wrap(d, title, font, int(w * 0.88))
            if len(lines) <= 3 and all(d.textlength(l, font=font) <= w * 0.9 for l in lines):
                break
            fs -= 4
        font = _font(fs)
        lines = _wrap(d, title, font, int(w * 0.88))
        total = len(lines) * int(fs * 1.12)
        y = h - total - int(h * (0.12 if not subtitle else 0.2))
        for ln in lines:
            d.text((int(w * 0.06), y), ln, font=font, fill=(255, 255, 255, 255), stroke_width=max(2, fs // 14), stroke_fill=(0, 0, 0, 255))
            y += int(fs * 1.12)
    if subtitle:
        sf = _font(int(h * 0.055), bold=False)
        d.text((int(w * 0.06), h - int(h * 0.12)), subtitle.strip(), font=sf, fill=(255, 220, 120, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
    rgb = base.convert("RGB")
    png = to_png(rgb)
    if len(png) <= COVER_MAX_BYTES:
        return png, ".png"
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue(), ".jpg"


def mask_from_strokes(size: Tuple[int, int], strokes: Sequence[Tuple[Sequence[Tuple[float, float]], float]]) -> bytes:
    """Fırça vuruşlarından (``[(noktalar, çap)]``) beyaz = düzenle, siyah = koru PNG maske."""
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for pts, diameter in strokes:
        r = max(1.0, diameter / 2)
        pts = list(pts)
        for x, y in pts:
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
        if len(pts) > 1:
            d.line(pts, fill=255, width=int(diameter))
    buf = io.BytesIO()
    m.save(buf, format="PNG")
    return buf.getvalue()


def composite_edit(original: bytes, edited: bytes, mask: bytes, feather: float = 3.0) -> bytes:
    """Düzenleme sonucunu yalnızca maske içinde özgün görsele karıştırır (yumuşak kenar) — taşan değişiklikler kalmaz."""
    o, e = load(original), load(edited).resize(load(original).size, Image.LANCZOS)
    m = Image.open(io.BytesIO(mask)).convert("L").resize(o.size)
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    return to_png(Image.composite(e, o, m))
