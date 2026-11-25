"""KADRAJ simgelerini üretir (tekrarlanabilir): master SVG → PNG boyutları ve çok boyutlu ICO.

Çalıştırma (depo kökünden):  ``python tools/make_icons.py``

Girdi : ``kadraj/ui/assets/icon/kadraj.svg`` (master)
Çıktı : ``kadraj/ui/assets/icon/kadraj-{16,24,32,48,64,128,256,512}.png`` ve ``kadraj.ico`` (16–256)

* PNG'ler QtSvg (``QSvgRenderer``) ile render edilir; imza için ``InstrumentSerif-Italic.ttf`` yüklenir.
* Marka kuralı: "from İnsibağ" imzası yalnızca 256 px ve üzeri boyutlarda çizilir; küçük boyutlarda SVG'den çıkarılır.
* ICO'nun kareleri Pillow ile kodlanır. Pillow'un kendi ICO yazıcısı tek bir taban görüntüyü küçülterek kareler
  üretir; imza küçük karelere sızardı. Bu yüzden her boyut ayrı render edilir ve kap (ICONDIR) elle yazılır.
"""
from __future__ import annotations

import io
import os
import re
import struct
import sys
from pathlib import Path
from typing import List, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "kadraj" / "ui" / "assets" / "icon"
FONT_FILE = ROOT / "kadraj" / "ui" / "assets" / "fonts" / "InstrumentSerif-Italic.ttf"
MASTER = ICON_DIR / "kadraj.svg"
PNG_SIZES: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256, 512)
ICO_SIZES: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
SIGNATURE_MIN_PX = 256
_SIGNATURE = re.compile(r'<g id="signature".*?</g>', re.S)


def svg_for_size(svg: str, size: int) -> str:
    """Boyuta göre SVG metni: imza yalnızca ``SIGNATURE_MIN_PX`` ve üzerinde kalır."""
    if size >= SIGNATURE_MIN_PX:
        return svg
    stripped, count = _SIGNATURE.subn("", svg)
    if count != 1:
        raise ValueError("master SVG içinde tek bir imza grubu (id=\"signature\") bekleniyordu")
    return stripped


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
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
