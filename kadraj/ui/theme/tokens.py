"""Tasarım tokenları — plan §4 (Claude Design tuvali, "v2 · Çift Estetik Ekranlar").

İki tam token kümesi: ``SIMPLE`` ("ponçik": pastel, yuvarlak, hap butonlar) ve ``LEGEND``
(koyu, keskin, yoğun). Erişilebilirlik katmanları sonradan uygulanır:

* ``large_text``     — yazı ve kontrol boyları ×1.25
* ``high_contrast``  — zıtlık yüksek, kenarlıklar 2 px, pastel/yarı saydam renkler kaldırılır

``resolve(mode, large_text, high_contrast)`` donmuş (frozen) bir ``Tokens`` döner.
Renkler ``#RRGGBB`` dizeleridir; QSS ve ``QPainter`` kodu doğrudan kullanır.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Mapping

from ...core.contracts import Accent, UiMode

LARGE_TEXT_SCALE = 1.25


@dataclass(frozen=True)
class AccentSet:
    base: str    # dolgu (parlak): blob, rozet, vurgu çizgisi
    tone: str    # açık zemin: kart yüzeyi
    ink: str     # tone üzerinde okunur koyu renk (metin/ikon)


@dataclass(frozen=True)
class Tokens:
    mode: str
    # yüzeyler ve metin
    bg: str
    card: str
    surface2: str
    border: str
    text: str
    text2: str
    # birincil eylem
    primary: str
    primary_deep: str          # hover/basılı
    primary_tone: str          # birincil ton (seçili zemin)
    on_primary: str            # birincil dolgu üstündeki metin
    # durum renkleri
    good: str
    warn: str
    bad: str
    info: str
    accents: Mapping[str, AccentSet]
    # şekil
    radius_card: int
    radius_ctl: int
    pill: bool                 # butonlar hap şeklinde mi
    ctl_h: int                 # genel kontrol yüksekliği
    btn_h: int                 # birincil buton yüksekliği
    border_w: int
    shadows: bool
    # yazı
    font_ui: str
    font_display: str
    font_mono: str
    font_serif: str
    ui_px: int
    ui_weight: int
    display_weight: int
    # katmanlar
    scale: float = 1.0
    large_text: bool = False
    high_contrast: bool = False

    def accent(self, accent: "Accent | str") -> AccentSet:
        key = accent.value if isinstance(accent, Accent) else str(accent)
        return self.accents.get(key, self.accents[Accent.NEUTRAL.value])

    def px(self, value: float) -> int:
        """Ölçekli piksel (Büyük Yazı katmanını uygular)."""
        return int(round(value * self.scale))

    @property
    def is_legend(self) -> bool:
        return self.mode == UiMode.LEGEND.value


# ---------------------------------------------------------------------------
def mix(c1: str, c2: str, t: float) -> str:
    """``c1``'den ``c2``'ye ``t`` (0..1) oranında karışım."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


def alpha(color: str, a: float) -> str:
    """QSS için ``rgba(r, g, b, a)``."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {a:.3f})"


# --- Basit Mod -------------------------------------------------------------
_SIMPLE_ACCENTS: Dict[str, AccentSet] = {
    Accent.PEACH.value:    AccentSet("#FFB38A", "#FFE7D6", "#9A4320"),
    Accent.LAVENDER.value: AccentSet("#C9B8FF", "#EFE9FF", "#5B44B8"),
    Accent.MINT.value:     AccentSet("#9FE0C1", "#E1F6EC", "#2E5E48"),
    Accent.BUTTER.value:   AccentSet("#FFD97A", "#FFF4D1", "#7A5600"),
    Accent.PINK.value:     AccentSet("#FFB3C4", "#FFE6EC", "#A8324F"),
    Accent.NEUTRAL.value:  AccentSet("#E6D6C0", "#F6ECDD", "#6E5D52"),
}

SIMPLE = Tokens(
    mode=UiMode.SIMPLE.value,
    bg="#FBF4EA", card="#FFFDF9", surface2="#F6ECDD", border="#EFE2D0",
    text="#3B2F2A", text2="#6E5D52",
    primary="#FFB38A", primary_deep="#F08A5D", primary_tone="#FFE7D6", on_primary="#3B2F2A",
    good="#2E7D5B", warn="#9A6A00", bad="#B03A48", info="#5B44B8",
    accents=_SIMPLE_ACCENTS,
    radius_card=24, radius_ctl=16, pill=True, ctl_h=48, btn_h=56, border_w=1, shadows=True,
    font_ui="Nunito", font_display="Nunito", font_mono="JetBrains Mono", font_serif="Instrument Serif",
    ui_px=16, ui_weight=600, display_weight=800,
)

# --- Legend Mode -----------------------------------------------------------
_L_PANEL = "#15171C"
_LEGEND_BASES: Dict[str, str] = {
    Accent.PEACH.value: "#F5A04A",     # turuncu  — indirici
    Accent.LAVENDER.value: "#A68BFF",  # mor      — ses
    Accent.MINT.value: "#D4A24C",      # altın    — kurgu
    Accent.BUTTER.value: "#3FD0E0",    # camgöbeği — görsel
    Accent.PINK.value: "#FF7A93",      # pembe    — dizi/film
    Accent.NEUTRAL.value: "#9BA1AB",
}
_LEGEND_ACCENTS: Dict[str, AccentSet] = {
    k: AccentSet(base=v, tone=mix(_L_PANEL, v, 0.16), ink=v) for k, v in _LEGEND_BASES.items()}

LEGEND = Tokens(
    mode=UiMode.LEGEND.value,
    bg="#0C0D10", card="#15171C", surface2="#1A1D23", border="#262A31",
    text="#E8E6E1", text2="#9BA1AB",
    primary="#D4A24C", primary_deep="#B98A38", primary_tone=mix("#15171C", "#D4A24C", 0.16),
    on_primary="#0C0D10",
    good="#46D18A", warn="#F5A04A", bad="#FF5A5F", info="#3FD0E0",
    accents=_LEGEND_ACCENTS,
    radius_card=4, radius_ctl=3, pill=False, ctl_h=24, btn_h=26, border_w=1, shadows=False,
    font_ui="Inter", font_display="Archivo", font_mono="JetBrains Mono", font_serif="Instrument Serif",
    ui_px=12, ui_weight=400, display_weight=700,
)

BASE: Dict[str, Tokens] = {SIMPLE.mode: SIMPLE, LEGEND.mode: LEGEND}


# --- Erişilebilirlik katmanları -----------------------------------------------
def _high_contrast(tok: Tokens) -> Tokens:
    if tok.is_legend:
        accents = {k: AccentSet(mix(v.base, "#FFFFFF", 0.25), "#000000", mix(v.base, "#FFFFFF", 0.25))
                   for k, v in tok.accents.items()}
        return replace(tok, bg="#000000", card="#000000", surface2="#101010", border="#FFFFFF",
                       text="#FFFFFF", text2="#E6E6E6", primary="#FFD166", primary_deep="#FFE29A",
                       primary_tone="#2A2100", on_primary="#000000", good="#6BFFB0", warn="#FFC266",
                       bad="#FF8084", info="#7BEBFF", accents=accents, border_w=2, high_contrast=True)
    accents = {k: AccentSet(mix(v.ink, "#FFFFFF", 0.78), "#FFFFFF", v.ink) for k, v in tok.accents.items()}
    return replace(tok, bg="#FFFFFF", card="#FFFFFF", surface2="#F2F2F2", border="#000000",
                   text="#000000", text2="#1F1F1F", primary="#7A2E0E", primary_deep="#5C210A",
                   primary_tone="#FFE7D6", on_primary="#FFFFFF", good="#14573A", warn="#6B4700",
                   bad="#8A1C2B", info="#3A2A8F", accents=accents, border_w=2, shadows=False,
                   high_contrast=True)


def _large_text(tok: Tokens) -> Tokens:
    s = LARGE_TEXT_SCALE
    return replace(tok, scale=s, ui_px=round(tok.ui_px * s), ctl_h=round(tok.ctl_h * s),
                   btn_h=round(tok.btn_h * s), large_text=True)


def resolve(mode: "UiMode | str" = UiMode.SIMPLE, large_text: bool = False,
            high_contrast: bool = False) -> Tokens:
    """Mod + erişilebilirlik katmanlarından nihai ``Tokens``."""
    tok = BASE[UiMode.coerce(mode).value]
    if high_contrast:
        tok = _high_contrast(tok)
    if large_text:
        tok = _large_text(tok)
    return tok
