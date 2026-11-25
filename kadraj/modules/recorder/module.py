"""Ses Kayıt Stüdyosu modülünün kayıt girişi (``register() -> ModuleSpec``; kurallar: ``core.contracts.DISCOVERY_RULES``).

Ağır içe aktarmalar (sounddevice, soundfile, numpy-yoğun DSP, faster-whisper) burada YAPILMAZ; sayfa ve servis fabrikaları
tembeldir. ``sounddevice``/PortAudio yoksa modül yine de yüklenir (kayıt anında ``SmartError``).
"""
from __future__ import annotations

from ...core.contracts import Accent, ModuleSpec


def register() -> ModuleSpec:
    def simple(ctx):
        from .page_simple import RecorderSimplePage
        return RecorderSimplePage(ctx)

    def legend(ctx):
        from .page_legend import RecorderLegendPage
        return RecorderLegendPage(ctx)

    def service(ctx):
        from .service import RecorderService
        return RecorderService(ctx)

    from .strings import STRINGS
    return ModuleSpec(
        id="recorder", title_key="mod.recorder.title", order=20, accent=Accent.LAVENDER, icon="mic",
        desc_key="mod.recorder.desc", chip=".wav", shortcut="Ctrl+2", workspace="capture",
        simple_page=simple, legend_page=legend, service_factory=service, strings=STRINGS)
