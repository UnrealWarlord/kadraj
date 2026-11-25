"""Video Düzenleme (NLE) modülünün kayıt girişi (``register() -> ModuleSpec``; kurallar: ``core.contracts.DISCOVERY_RULES``).

Ağır içe aktarmalar (numpy-yoğun renk/ölçüm, FFmpeg süreçleri, QtMultimedia) burada YAPILMAZ; sayfa ve servis fabrikaları tembeldir.
FFmpeg yoksa modül yine de yüklenir (işleme anında ``SmartError``).
"""
from __future__ import annotations

from ...core.contracts import Accent, ModuleSpec


def register() -> ModuleSpec:
    def simple(ctx):
        from .page_simple import EditorSimplePage
        return EditorSimplePage(ctx)

    def legend(ctx):
        from .page_legend import EditorLegendPage
        return EditorLegendPage(ctx)

    def service(ctx):
        from .service import EditorService
        return EditorService(ctx)

    from .strings import STRINGS
    return ModuleSpec(
        id="editor", title_key="mod.editor.title", order=30, accent=Accent.MINT, icon="film",
        desc_key="mod.editor.desc", chip=".mp4", shortcut="Ctrl+3", workspace="edit",
        simple_page=simple, legend_page=legend, service_factory=service, strings=STRINGS)
