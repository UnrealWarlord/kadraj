"""Gömülü yazı tipleri (OFL) — ``kadraj/ui/assets/fonts/``.

Nunito (Basit gövde), Archivo (wordmark, Legend başlıklar), Inter (Legend gövde),
JetBrains Mono (zaman kodu), Instrument Serif İtalik (``from İnsibağ`` imzası).
Hepsi OFL; Qt değişken TTF'nin yalnızca varsayılan örneğini kaydettiği için ağırlıklar **sabit örnek**
dosyalar olarak gömülüdür (``tools/make_static_fonts.py``). Yüklenemezse (dosya yok / bozuk) sistem yazı
tipine düşülür; uygulama bu yüzden hata vermez.
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtGui import QFont, QFontDatabase

from .. import paths

# Aile adı → dosya adları (sabit ağırlık örnekleri; üretim: tools/make_static_fonts.py)
FONT_FILES: Dict[str, List[str]] = {
    "Nunito": [f"Nunito-{n}.ttf" for n in ("Regular", "SemiBold", "Bold", "ExtraBold", "Black")],
    "Archivo": [f"Archivo-{n}.ttf" for n in ("SemiBold", "Bold", "ExtraBold")],
    "Archivo Expanded": ["ArchivoExpanded-ExtraBold.ttf"],      # wordmark: genişlik %125
    "Inter": [f"Inter-{n}.ttf" for n in ("Regular", "Medium", "SemiBold", "Bold")],
    "JetBrains Mono": [f"JetBrainsMono-{n}.ttf" for n in ("Regular", "Medium", "Bold")],
    "Instrument Serif": ["InstrumentSerif-Regular.ttf", "InstrumentSerif-Italic.ttf"],
}

# Sistemde karşılığı yoksa denenecek yedekler
FALLBACKS: Dict[str, List[str]] = {
    "Nunito": ["Noto Sans", "DejaVu Sans", "Sans Serif"],
    "Archivo": ["Noto Sans", "DejaVu Sans", "Sans Serif"],
    "Archivo Expanded": ["Archivo", "Noto Sans", "DejaVu Sans", "Sans Serif"],
    "Inter": ["Noto Sans", "DejaVu Sans", "Sans Serif"],
    "JetBrains Mono": ["DejaVu Sans Mono", "Monospace"],
    "Instrument Serif": ["Noto Serif", "DejaVu Serif", "Serif"],
}

_loaded: Dict[str, bool] = {}
_resolved: Dict[str, str] = {}


def load_fonts() -> Dict[str, bool]:
    """Gömülü yazı tiplerini ``QFontDatabase``'e yükler. ``{aile: yüklendi_mi}`` döner.
    Qt uygulaması (``QGuiApplication``) oluşturulduktan sonra çağrılmalıdır."""
    fonts_dir = paths.fonts_dir()
    for family, files in FONT_FILES.items():
        ok = False
        for fname in files:
            fid = QFontDatabase.addApplicationFont(str(fonts_dir / fname))
            ok = ok or fid != -1
        _loaded[family] = ok
    _resolved.clear()
    return dict(_loaded)


def embedded_ok(family: str) -> bool:
    return bool(_loaded.get(family))


def resolve(family: str) -> str:
    """Kullanılabilir gerçek aile adı: gömülü/sistemdeki ``family`` ya da ilk yedek."""
    cached = _resolved.get(family)
    if cached:
        return cached
    available = set(QFontDatabase.families())
    choice = family if family in available else next(
        (f for f in FALLBACKS.get(family, []) if f in available), family)
    _resolved[family] = choice
    return choice


def qfont(family: str, pixel_size: float, weight: int = 400, italic: bool = False,
          stretch: int = 100, letter_spacing_em: float = 0.0) -> QFont:
    """Ortak ``QFont`` üretici (piksel boyutlu → DPI'dan bağımsız oranlar).

    ``weight`` 100–900 (CSS ölçeği); sabit örnek dosyalar sayesinde gerçek ağırlık seçilir. Geniş wordmark
    için ``"Archivo Expanded"`` ailesi kullanılır (gerçek ``wdth=125`` örneği; yapay ``setStretch`` gerekmez).
    ``stretch`` yalnızca sistem yedek fontlarında yapay genişletme içindir.
    """
    f = QFont(resolve(family))
    f.setPixelSize(max(1, int(round(pixel_size))))
    f.setWeight(QFont.Weight(max(1, min(1000, weight))))
    f.setItalic(italic)
    if stretch != 100:
        f.setStretch(stretch)
    if letter_spacing_em:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing_em * pixel_size)
    return f
