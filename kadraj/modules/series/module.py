"""Dizi/Film modülünün kayıt girişi (``register() -> ModuleSpec``). Ağır içe aktarmalar sayfa/servis fabrikalarında tembeldir."""
from __future__ import annotations

from ...core.contracts import Accent, ModuleSpec


def register() -> ModuleSpec:
    def simple(ctx):
        from .page_simple import SeriesSimplePage
        return SeriesSimplePage(ctx)

    def legend(ctx):
        from .page_legend import SeriesLegendPage
        return SeriesLegendPage(ctx)

    def service(ctx):
        from .service import SeriesService
        return SeriesService(ctx)

    from .strings import STRINGS
    return ModuleSpec(
        id="series", title_key="mod.series.title", order=50, accent=Accent.PINK, icon="clapper", desc_key="mod.series.desc",
        chip=".fountain", shortcut="Ctrl+5", workspace="studio", simple_page=simple, legend_page=legend, service_factory=service,
        requires=("ffmpeg",), strings=STRINGS)
