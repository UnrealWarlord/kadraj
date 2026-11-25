"""Gömülü yazı tipleri için sabit ağırlık örnekleri üretir (geliştirme aracı; çalışma zamanında gerekmez).

Neden: Qt, ``addApplicationFont`` ile yüklenen değişken (variable) TTF'nin yalnızca varsayılan örneğini
kaydeder; QSS ``font-weight`` değeri bu yüzden etkisiz kalır. Burada her gereken ağırlık/genişlik için
``fontTools.varLib.instancer`` ile **sabit** bir TTF üretilir; böylece normal aile/ağırlık eşleşmesi çalışır.

Kullanım (fonttools gerektirir; uygulama bağımlılığı değildir):

    python tools/make_static_fonts.py SRC_DIR kadraj/ui/assets/fonts

``SRC_DIR`` Google Fonts (github.com/google/fonts, SIL OFL) değişken dosyalarını içerir:
Nunito[wght].ttf, Archivo[wdth,wght].ttf, Inter[opsz,wght].ttf, JetBrainsMono[wght].ttf.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

# (kaynak dosya, {eksen: değer}, çıktı dosyası, aile adı, stil adı, ağırlık)
JOBS = []
for w, name in ((400, "Regular"), (600, "SemiBold"), (700, "Bold"), (800, "ExtraBold"), (900, "Black")):
    JOBS.append(("Nunito[wght].ttf", {"wght": w}, f"Nunito-{name}.ttf", "Nunito", name, w))
for w, name in ((400, "Regular"), (500, "Medium"), (600, "SemiBold"), (700, "Bold")):
    JOBS.append(("Inter[opsz,wght].ttf", {"wght": w, "opsz": 14}, f"Inter-{name}.ttf", "Inter", name, w))
for w, name in ((400, "Regular"), (500, "Medium"), (700, "Bold")):
    JOBS.append(("JetBrainsMono[wght].ttf", {"wght": w}, f"JetBrainsMono-{name}.ttf", "JetBrains Mono", name, w))
for w, name in ((600, "SemiBold"), (700, "Bold"), (800, "ExtraBold")):
    JOBS.append(("Archivo[wdth,wght].ttf", {"wght": w, "wdth": 100}, f"Archivo-{name}.ttf", "Archivo", name, w))
JOBS.append(("Archivo[wdth,wght].ttf", {"wght": 800, "wdth": 125}, "ArchivoExpanded-ExtraBold.ttf",
             "Archivo Expanded", "ExtraBold", 800))


def _set_names(font: TTFont, family: str, style: str, weight: int) -> None:
    """Qt'nin aile/stil/ağırlığı doğru okuması için ad tablosunu ve OS/2'yi sabitler."""
    name = font["name"]
    full = f"{family} {style}"
    ps = f"{family.replace(' ', '')}-{style}"
    regular_like = style in ("Regular", "Bold")
    for rec in list(name.names):
        if rec.nameID in (1, 2, 3, 4, 6, 16, 17, 21, 22, 25):
            name.removeNames(nameID=rec.nameID)
    for pid, eid, lid in ((3, 1, 0x409), (1, 0, 0)):
        name.setName(family if regular_like else full, 1, pid, eid, lid)
        name.setName(style if regular_like else "Regular", 2, pid, eid, lid)
        name.setName(f"{ps};static", 3, pid, eid, lid)
        name.setName(full, 4, pid, eid, lid)
        name.setName(ps, 6, pid, eid, lid)
        name.setName(family, 16, pid, eid, lid)
        name.setName(style, 17, pid, eid, lid)
    font["OS/2"].usWeightClass = weight
    if "fvar" in font:
        del font["fvar"]


def main(src: Path, dst: Path) -> int:
    dst.mkdir(parents=True, exist_ok=True)
    cache = {}
    for srcfile, axes, out, family, style, weight in JOBS:
        path = src / srcfile
        if not path.is_file():
            print(f"yok: {path}", file=sys.stderr)
            return 1
        vf = TTFont(path)
        inst = instancer.instantiateVariableFont(vf, axes, inplace=False)
        _set_names(inst, family, style, weight)
        inst.save(dst / out)
        print(f"{out}  ({(dst / out).stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
