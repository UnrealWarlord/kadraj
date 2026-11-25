"""Fotoğraf Üret modülünün kayıt girişi (``register() -> ModuleSpec``). Ağır içe aktarmalar sayfa/servis fabrikalarında tembeldir."""
from __future__ import annotations

from ...core.contracts import Accent, ModuleSpec


def register() -> ModuleSpec:
    def simple(ctx):
        from .page_simple import PhotoSimplePage
        return PhotoSimplePage(ctx)

    def legend(ctx):
        from .page_legend import PhotoLegendPage
        return PhotoLegendPage(ctx)

    def service(ctx):
        from .service import PhotoService
        return PhotoService(ctx)

    from .strings import STRINGS
    return ModuleSpec(
        id="photo", title_key="mod.photo.title", order=40, accent=Accent.BUTTER, icon="image", desc_key="mod.photo.desc",
        chip=".png", shortcut="Ctrl+4", workspace="studio", simple_page=simple, legend_page=legend, service_factory=service,
        strings=STRINGS)
