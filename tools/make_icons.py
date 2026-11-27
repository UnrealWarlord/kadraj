"""KADRAJ simgelerini üretir (tekrarlanabilir): master SVG → PNG boyutları ve çok boyutlu ICO.

Çalıştırma (depo kökünden):  ``python tools/make_icons.py``

Girdi : ``kadraj/ui/assets/icon/kadraj.svg`` (master)
Çıktı : ``kadraj/ui/assets/icon/kadraj-{16,24,32,48,64,128,256,512}.png`` ve ``kadraj.ico`` (16–256);
        ``packaging/android/res/`` altında Android uyarlanır simgesi (API 26+; MOBILE_6, M22)

* PNG'ler QtSvg (``QSvgRenderer``) ile render edilir; imza için ``InstrumentSerif-Italic.ttf`` yüklenir.
* Marka kuralı: "from İnsibağ" imzası yalnızca 256 px ve üzeri boyutlarda çizilir; küçük boyutlarda SVG'den çıkarılır.
* ICO'nun kareleri Pillow ile kodlanır. Pillow'un kendi ICO yazıcısı tek bir taban görüntüyü küçülterek kareler
  üretir; imza küçük karelere sızardı. Bu yüzden her boyut ayrı render edilir ve kap (ICONDIR) elle yazılır.
* Android: başlatıcı simgesi en çok 48 dp'dir (≤ 192 px), yani imza kuralı gereği **imzasızdır**. Uyarlanır simge iki
  katmandır: zemin (karonun koyu geçişi, tam kare; şekli başlatıcının maskesi verir) ve ön plan (vizör işareti,
  vektör). İşaret 108 dp tuvalin ortasındaki 66 dp'lik güvenli dairenin içinde kalır, maske ne olursa olsun kırpılmaz.
  Android 13+ tek renkli (temalı) simge için de aynı ön plan kullanılır.
"""
from __future__ import annotations

import io
import math
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "kadraj" / "ui" / "assets" / "icon"
FONT_FILE = ROOT / "kadraj" / "ui" / "assets" / "fonts" / "InstrumentSerif-Italic.ttf"
MASTER = ICON_DIR / "kadraj.svg"
PNG_SIZES: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256, 512)
ICO_SIZES: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
SIGNATURE_MIN_PX = 256
_SIGNATURE = re.compile(r'<g id="signature".*?</g>', re.S)

ANDROID_RES = ROOT / "packaging" / "android" / "res"
ADAPTIVE_DP = 108                   # uyarlanır simge tuvali (dp)
SAFE_RADIUS_DP = 33                 # her maskede görünen orta daire (66 dp çap)
ANDROID_UNIT_DP = 2.4               # vizörün 24 birimlik ızgarası → 2.4 dp/birim (işaret ≈ 43 dp genişlik)
ANDROID_FILES = ("drawable/kadraj_icon_background.xml", "drawable/kadraj_icon_foreground.xml",
                 "mipmap-anydpi-v26/icon.xml")
_SVG = "{http://www.w3.org/2000/svg}"
_ANDROID_NOTE = "<!-- tools/make_icons.py üretir (kaynak: kadraj/ui/assets/icon/kadraj.svg); elle düzenlenmez. -->"


def svg_for_size(svg: str, size: int) -> str:
    """Boyuta göre SVG metni: imza yalnızca ``SIGNATURE_MIN_PX`` ve üzerinde kalır."""
    if size >= SIGNATURE_MIN_PX:
        return svg
    stripped, count = _SIGNATURE.subn("", svg)
    if count != 1:
        raise ValueError("master SVG içinde tek bir imza grubu (id=\"signature\") bekleniyordu")
    return stripped


def _mark(svg: str) -> ET.Element:
    root = ET.fromstring(svg)
    for group in root.iter(f"{_SVG}g"):
        if group.get("id") == "mark":
            return group
    raise ValueError("master SVG içinde vizör grubu (id=\"mark\") bekleniyordu")


def _tile_colors(svg: str) -> Tuple[str, str]:
    root = ET.fromstring(svg)
    for grad in root.iter(f"{_SVG}linearGradient"):
        if grad.get("id") == "tile":
            stops = [stop.get("stop-color", "") for stop in grad.iter(f"{_SVG}stop")]
            if len(stops) == 2 and all(stops):
                return stops[0], stops[1]
    raise ValueError("master SVG içinde iki duraklı karo geçişi (id=\"tile\") bekleniyordu")


def path_points(d: str) -> Iterator[Tuple[float, float]]:
    """Yalnız M/L/H/V (büyük/küçük) komutlu bir SVG yolunun köşe noktaları (vizör çizgileri böyle)."""
    tokens = re.findall(r"[A-Za-z]|-?\d*\.?\d+", d)
    x = y = 0.0
    cmd = ""
    i = 0
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            if cmd.upper() not in "MLHV":
                raise ValueError(f"desteklenmeyen yol komutu: {cmd!r}")
            i += 1
            continue
        rel = cmd.islower()
        op = cmd.upper()
        if op in ("M", "L"):
            nx, ny = float(tokens[i]), float(tokens[i + 1])
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            i += 2
        elif op == "H":
            x = x + float(tokens[i]) if rel else float(tokens[i])
            i += 1
        elif op == "V":
            y = y + float(tokens[i]) if rel else float(tokens[i])
            i += 1
        else:
            raise ValueError(f"desteklenmeyen yol komutu: {cmd!r}")
        yield x, y


def mark_reach(svg: str) -> float:
    """Vizör işaretinin merkezden en uzak noktası (ızgara birimi; çizgi kalınlığının yarısı dahil)."""
    mark = _mark(svg)
    half = float(mark.get("stroke-width", "0")) / 2
    reach = 0.0
    for path in mark.iter(f"{_SVG}path"):
        for x, y in path_points(path.get("d", "")):
            reach = max(reach, math.hypot(x - 12, y - 12) + half)
    for circle in mark.iter(f"{_SVG}circle"):
        cx, cy, r = (float(circle.get(k, "0")) for k in ("cx", "cy", "r"))
        reach = max(reach, math.hypot(cx - 12, cy - 12) + r)
    return reach


def _num(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def android_layers(svg: str) -> Dict[str, str]:
    """Android uyarlanır simgesinin kaynak dosyaları: ``res`` altındaki göreli yol → XML metni."""
    mark = _mark(svg)
    top, bottom = _tile_colors(svg)
    stroke = mark.get("stroke", "#D4A24C")
    width = mark.get("stroke-width", "2")
    cap = mark.get("stroke-linecap", "butt")
    join = mark.get("stroke-linejoin", "miter")
    shift = _num(ADAPTIVE_DP / 2 - 12 * ANDROID_UNIT_DP)
    scale = _num(ANDROID_UNIT_DP)
    paths = []
    for path in mark.iter(f"{_SVG}path"):
        paths.append(f"""        <path
            android:pathData="{path.get('d')}"
            android:strokeColor="{stroke}"
            android:strokeWidth="{width}"
            android:strokeLineCap="{cap}"
            android:strokeLineJoin="{join}" />""")
    for circle in mark.iter(f"{_SVG}circle"):
        cx, cy, r = (_num(float(circle.get(k, "0"))) for k in ("cx", "cy", "r"))
        left = _num(float(cx) - float(r))
        paths.append(f"""        <path
            android:pathData="M{left},{cy}a{r},{r} 0 1,0 {_num(2 * float(r))},0a{r},{r} 0 1,0 -{_num(2 * float(r))},0"
            android:fillColor="{circle.get('fill', stroke)}" />""")
    head = f'<?xml version="1.0" encoding="utf-8"?>\n{_ANDROID_NOTE}\n'
    foreground = head + f"""<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="{ADAPTIVE_DP}dp"
    android:height="{ADAPTIVE_DP}dp"
    android:viewportWidth="{ADAPTIVE_DP}"
    android:viewportHeight="{ADAPTIVE_DP}">
    <group
        android:translateX="{shift}"
        android:translateY="{shift}"
        android:scaleX="{scale}"
        android:scaleY="{scale}">
{chr(10).join(paths)}
    </group>
</vector>
"""
    background = head + f"""<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <gradient
        android:type="linear"
        android:angle="270"
        android:startColor="{top}"
        android:endColor="{bottom}" />
</shape>
"""
    adaptive = head + """<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/kadraj_icon_background" />
    <foreground android:drawable="@drawable/kadraj_icon_foreground" />
    <monochrome android:drawable="@drawable/kadraj_icon_foreground" />
</adaptive-icon>
"""
    return dict(zip(ANDROID_FILES, (background, foreground, adaptive)))


def render(svg: str, size: int):
    """SVG'yi ``size × size`` şeffaf bir ``QImage``'a render eder."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(svg.encode("utf-8"))
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    renderer.render(painter)
    painter.end()
    return image


def png_frame(path: Path) -> bytes:
    """Pillow ile PNG karesini yeniden kodlar (ICO kabına girecek bayt dizisi)."""
    from PIL import Image

    buf = io.BytesIO()
    with Image.open(path) as im:
        im.convert("RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def pack_ico(frames: List[Tuple[int, bytes]]) -> bytes:
    """ICONDIR + ICONDIRENTRY'ler + PNG karelerini tek ICO dosyasında birleştirir (256 px → genişlik bayt 0)."""
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = b""
    data = b""
    for size, png in frames:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png
    return header + entries + data


def main() -> int:
    from PySide6.QtCore import Qt  # noqa: F401  (Qt modülü tembel yüklenir)
    from PySide6.QtGui import QFontDatabase, QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    if FONT_FILE.is_file():
        QFontDatabase.addApplicationFont(str(FONT_FILE))

    svg = MASTER.read_text(encoding="utf-8")
    for size in PNG_SIZES:
        out = ICON_DIR / f"kadraj-{size}.png"
        if not render(svg_for_size(svg, size), size).save(str(out), "PNG"):
            raise SystemExit(f"PNG yazılamadı: {out}")
        print(f"png  {size:>4} px  {out.relative_to(ROOT)}")

    frames = [(size, png_frame(ICON_DIR / f"kadraj-{size}.png")) for size in ICO_SIZES]
    ico = ICON_DIR / "kadraj.ico"
    ico.write_bytes(pack_ico(frames))

    from PIL import Image
    with Image.open(ico) as check:
        found = tuple(sorted(w for w, _h in check.info.get("sizes", set())))
    if found != tuple(sorted(ICO_SIZES)):
        raise SystemExit(f"ICO boyutları beklenenden farklı: {found}")
    print(f"ico  {', '.join(str(s) for s in ICO_SIZES)} px  {ico.relative_to(ROOT)}")

    if mark_reach(svg) * ANDROID_UNIT_DP > SAFE_RADIUS_DP:
        raise SystemExit("Android simgesi: vizör işareti güvenli dairenin dışına taşıyor")
    for rel, text in android_layers(svg).items():
        out = ANDROID_RES / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"xml  android  {out.relative_to(ROOT)}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
